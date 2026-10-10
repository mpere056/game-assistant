"""Reading single files out of Elden Ring's packed archives (Data0-3.bdt), read-only.

Used to extract the navmeshes (the game's own walkable-area meshes) into .local/eldenring/navmesh/;
nothing here writes to the game folder, and nothing extracted goes into the repository.

Format knowledge from Smithbox (MIT, https://github.com/vawser/Smithbox, Andre.Formats):
- Data*.bhd (the index) is RSA-encrypted with a public key: each 256-byte block decrypts (raw
  modular exponentiation) to 255 bytes. The four public keys are published in Smithbox's
  ArchiveKeys.cs; `fetch_support_files()` downloads that file and the file-name list into .local/.
- BHD5 index: buckets of 40-byte file headers (u64 name hash, i32 padded size, i32 unpadded size,
  i64 offset in the .bdt, i64 SHA offset, i64 AES key offset).
- Names are hashed (lower case, leading '/'): h = h * 0x85 + c, 64-bit.
- Some byte ranges of a file are AES-128-ECB encrypted, with the key and ranges in the index.
- Files are DCX-compressed with Oodle Kraken; the game's own oo2core_6_win64.dll decompresses them.
- BND4 containers hold several files.
"""
from __future__ import annotations

import base64
import ctypes
import re
import struct
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / '.local' / 'eldenring' / 'archive'
GAME_DIR = Path(r'C:\Program Files (x86)\Steam\steamapps\common\ELDEN RING\Game')
ARCHIVES = ('Data0', 'Data1', 'Data2', 'Data3')
SMITHBOX = 'https://raw.githubusercontent.com/vawser/Smithbox/main/'
KEYS_URL = SMITHBOX + 'src/Andre/Andre.Formats/Util/ArchiveKeys.cs'
NAMES_URL = SMITHBOX + 'src/Smithbox.Data/Assets/UXM%20Dictionaries/EldenRingDictionary.txt'
KEYS_FILE = DIR / 'ArchiveKeys.cs'
NAMES_FILE = DIR / 'EldenRingDictionary.txt'


def fetch_support_files() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    for url, path in ((KEYS_URL, KEYS_FILE), (NAMES_URL, NAMES_FILE)):
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as r:
                path.write_bytes(r.read())


def name_hash(path: str) -> int:
    p = path.strip().replace('\\', '/').lower()
    if not p.startswith('/'):
        p = '/' + p
    h = 0
    for c in p:
        h = (h * 0x85 + ord(c)) & 0xFFFFFFFFFFFFFFFF
    return h


def names(pattern: str = '') -> list[str]:
    """Known archive paths (from the dictionary) containing `pattern`."""
    return [ln.strip() for ln in NAMES_FILE.read_text(encoding='utf-8-sig').splitlines()
            if ln.startswith('/') and pattern in ln]


# ---- the index ----

def _public_keys() -> dict[str, tuple[int, int]]:
    src = KEYS_FILE.read_text(encoding='utf-8-sig')
    er = src[src.index('EldenRingKeys = new'):src.index('ArmoredCore6Keys = new')]
    out = {}
    for m in re.finditer(r'\["(\w+)"\]\s*=\s*@"-----BEGIN RSA PUBLIC KEY-----(.*?)-----END RSA PUBLIC KEY-----', er, re.S):
        out[m.group(1)] = _der_rsa(base64.b64decode(''.join(m.group(2).split())))
    return out


def _der_rsa(der: bytes) -> tuple[int, int]:
    """(modulus, exponent) from a PKCS#1 RSAPublicKey (DER)."""
    i = 0

    def length() -> int:
        nonlocal i
        n = der[i]
        i += 1
        if n & 0x80:
            k = n & 0x7F
            n = int.from_bytes(der[i:i + k], 'big')
            i += k
        return n
    assert der[i] == 0x30
    i += 1
    length()
    vals = []
    for _ in range(2):
        assert der[i] == 0x02
        i += 1
        n = length()
        vals.append(int.from_bytes(der[i:i + n], 'big'))
        i += n
    return vals[0], vals[1]


def _rsa_decrypt(data: bytes, key: tuple[int, int]) -> bytes:
    n, e = key
    bits = n.bit_length()
    in_s, out_s = (bits + 7) // 8, (bits - 1) // 8
    out = bytearray()
    for k in range(0, len(data) - in_s + 1, in_s):
        out += pow(int.from_bytes(data[k:k + in_s], 'big'), e, n).to_bytes(out_s, 'big')
    return bytes(out)


class Entry:
    __slots__ = ('archive', 'padded', 'unpadded', 'offset', 'aes_key', 'aes_ranges')

    def __init__(self, archive, padded, unpadded, offset, aes_key, aes_ranges):
        self.archive, self.padded, self.unpadded, self.offset = archive, padded, unpadded, offset
        self.aes_key, self.aes_ranges = aes_key, aes_ranges


class Archives:
    """All entries of Data0-3, by name hash. Decrypting the indexes takes about 30 s the first
    time; the decrypted indexes are kept in .local/ (about 15 MB) so later runs start at once."""

    def __init__(self, game_dir: Path = GAME_DIR):
        self.game_dir = game_dir
        self.entries: dict[int, Entry] = {}
        keys = None
        for name in ARCHIVES:
            cache = DIR / f'{name}.bhd.dec'
            if not cache.exists():
                keys = keys or _public_keys()
                raw = (game_dir / f'{name}.bhd').read_bytes()
                cache.write_bytes(raw if raw[:4] == b'BHD5' else _rsa_decrypt(raw, keys[name]))
            self._index(name, cache.read_bytes())

    def _index(self, archive: str, bhd: bytes) -> None:
        if bhd[:4] != b'BHD5':
            raise ValueError(f'{archive}.bhd did not decrypt to a BHD5 index')
        _size, buckets, offset = struct.unpack_from('<iii', bhd, 12)
        for b in range(buckets):
            count, headers = struct.unpack_from('<ii', bhd, offset + 8 * b)
            for f in range(count):
                h, padded, unpadded, off, _sha, aes = struct.unpack_from('<QiiqqQ', bhd, headers + 40 * f)
                key = ranges = None
                if aes:
                    key = bhd[aes:aes + 16]
                    rc = struct.unpack_from('<i', bhd, aes + 16)[0]
                    ranges = [struct.unpack_from('<qq', bhd, aes + 20 + 16 * r) for r in range(rc)]
                self.entries[h] = Entry(archive, padded, unpadded, off, key, ranges)

    def has(self, path: str) -> bool:
        return name_hash(path) in self.entries

    def read(self, path: str) -> bytes:
        """The file's bytes (decrypted, still DCX-compressed if it was)."""
        e = self.entries[name_hash(path)]
        with open(self.game_dir / f'{e.archive}.bdt', 'rb') as f:
            f.seek(e.offset)
            data = bytearray(f.read(e.padded))
        if e.aes_key and e.aes_ranges:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            dec = Cipher(algorithms.AES(e.aes_key), modes.ECB()).decryptor()
            for start, end in e.aes_ranges:
                if start != -1 and end > start:
                    data[start:end] = dec.update(bytes(data[start:end]))
        size = e.unpadded if e.unpadded > 0 else e.padded
        return bytes(data[:size])


# ---- DCX (Oodle Kraken) ----

_oodle = None


def _oodle_dll():
    global _oodle
    if _oodle is None:
        dll = ctypes.WinDLL(str(GAME_DIR / 'oo2core_6_win64.dll'))
        f = dll.OodleLZ_Decompress
        f.restype = ctypes.c_int64
        f.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_int64, ctypes.c_int]
        _oodle = f
    return _oodle


def undcx(data: bytes) -> bytes:
    if data[:4] != b'DCX\0':
        return data
    if data[0x28:0x2C] != b'KRAK':
        raise ValueError(f'unsupported DCX compression {data[0x28:0x2C]!r}')
    raw_size, comp_size = struct.unpack_from('>II', data, 0x1C)
    if data[0x44:0x48] != b'DCA\x00':
        raise ValueError('unexpected DCX header layout')
    comp = data[0x4C:0x4C + comp_size]  # after the DCA block (0x44 + 8)
    out = ctypes.create_string_buffer(raw_size + 64)  # Oodle may write a little past the end
    got = _oodle_dll()(comp, len(comp), out, raw_size, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if got != raw_size:
        raise ValueError(f'Oodle decompressed {got} bytes, expected {raw_size}')
    return out.raw[:raw_size]


# ---- BND4 containers ----

def bnd4_files(data: bytes) -> dict[str, bytes]:
    """name -> bytes for each file in a BND4 container."""
    if data[:4] != b'BND4':
        raise ValueError('not a BND4 container')
    count = struct.unpack_from('<i', data, 0x0C)[0]
    unicode = data[0x30] == 1
    fmt = data[0x31]
    entry_size = struct.unpack_from('<q', data, 0x20)[0]
    out = {}
    for i in range(count):
        o = 0x40 + i * entry_size
        size = struct.unpack_from('<q', data, o + 8)[0]
        p = o + 16
        if fmt & 0x20:  # uncompressed size present
            p += 8
        if fmt & 0x10 and entry_size >= 0x24:
            offset = struct.unpack_from('<q', data, p)[0] if entry_size >= 0x28 and not (fmt & 0x02) else \
                struct.unpack_from('<I', data, p)[0]
        else:
            offset = struct.unpack_from('<I', data, p)[0]
        name_off = struct.unpack_from('<I', data, o + entry_size - 4)[0]
        if unicode:
            end = name_off
            while data[end:end + 2] != b'\0\0':
                end += 2
            name = data[name_off:end].decode('utf-16-le')
        else:
            name = data[name_off:data.index(b'\0', name_off)].decode('shift_jis', 'replace')
        out[name] = data[offset:offset + size]
    return out
