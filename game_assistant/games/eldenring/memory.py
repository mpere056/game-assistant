"""Read-only access to Elden Ring's memory through the bridge's debug mailbox.

The Attack on Elden Ring bridge serves a small command mailbox (bridge_protocol.h, ErmcCmdBlock)
on a background thread (read memory, read many addresses, pattern scan, list modules...). This module
only uses quick read-only commands (read, list modules); the assistant never writes game memory.

The AoTTG2 plugin also uses this mailbox for long-range hooks while linked, so requests wait their
turn and are kept short. Bulk reads happen once (loading parameter tables at start-up).
"""
from __future__ import annotations

import mmap
import struct
import threading
import time

OFF_CMD = 0x1000
OFF_RESP = 0x2000
RESP_MAX = 0x100000 - OFF_RESP
CMD_READ, CMD_MODULES = 2, 8  # never the scan command: it can block the mailbox for minutes


class MailboxError(RuntimeError):
    pass


class Memory:
    _lock = threading.Lock()  # one request at a time from this process

    def __init__(self, m: mmap.mmap):
        self.m = m

    def _cmd(self, code: int, args: bytes = b'', timeout: float = 5.0) -> tuple[int, bytes]:
        with self._lock:
            return self._cmd_locked(code, args, timeout)

    def _cmd_locked(self, code: int, args: bytes, timeout: float) -> tuple[int, bytes]:
        m = self.m
        deadline = time.monotonic() + timeout
        while True:  # wait for any earlier request (ours or the AoTTG2 plugin's) to finish
            req, resp = struct.unpack_from('<II', m, OFF_CMD)
            if req == resp:
                break
            if time.monotonic() > deadline:
                raise MailboxError('the bridge mailbox stayed busy')
            time.sleep(0.002)
        m[OFF_CMD + 0x18:OFF_CMD + 0x18 + len(args)] = args
        struct.pack_into('<II', m, OFF_CMD + 8, code, len(args))
        struct.pack_into('<I', m, OFF_CMD, req + 1)
        while struct.unpack_from('<I', m, OFF_CMD + 4)[0] != req + 1:
            if time.monotonic() > deadline:
                raise MailboxError('no answer from the bridge mailbox')
            time.sleep(0.001)
        status, n = struct.unpack_from('<iI', m, OFF_CMD + 0x10)
        return status, bytes(m[OFF_RESP:OFF_RESP + n])

    def read(self, addr: int, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            k = min(n, RESP_MAX)
            status, data = self._cmd(CMD_READ, struct.pack('<QI', addr, k))
            if status != 0 or len(data) < k:
                raise MailboxError(f'cannot read {k} bytes at {addr:#x}')
            out += data
            addr += k
            n -= k
        return bytes(out)

    def u64(self, addr: int) -> int:
        return struct.unpack('<Q', self.read(addr, 8))[0]

    def modules(self) -> dict[str, tuple[int, int]]:
        status, data = self._cmd(CMD_MODULES)
        out, o = {}, 0
        while o + 14 <= len(data):
            base, size, ln = struct.unpack_from('<QIH', data, o)
            o += 14
            out[data[o:o + ln].decode(errors='replace').lower()] = (base, size)
            o += ln
        return out
