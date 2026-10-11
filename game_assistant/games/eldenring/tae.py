"""Elden Ring animation events (TAE files): when each attack of each character can hit.

A character's animations come in chr/c####.anibnd.dcx (plus _div0N parts); the BND4 holds .tae files.
Each animation has events with a start and end time (seconds into the animation) and a type. Type 1
("InvokeAttackBehavior" in the community tools) is an attack's active window: the hitbox is out from
start to end. That is what combat code needs: "this swing hits from 0.53 s to 0.70 s".

Format (64-bit TAE3, little endian), from Smithbox's TAE3.bt (MIT, by TKGP):
  header: 'TAE ' ... +0x58 animCount (i32), +0x60 animsOffset (u64)
  animation: u64 id, u64 animDataOffset
  animData: u64 eventHeadersOffset, u64 eventGroupsOffset, u64 timesOffset, u64 animFileOffset,
            i32 eventCount, i32 eventGroupCount, i32 timeCount
  event header: u64 startTimeOffset, u64 endTimeOffset, u64 eventDataOffset (times are f32 there)
  event data: u64 type, u64 paramsOffset, then the parameters
The full animation id the game plays is the TAE file's id * 1,000,000 + the animation's id
(memory showed 2043000 = file 2, animation 43000).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

ATTACK = 1        # InvokeAttackBehavior: an attack's hitbox is active
BULLET = 2        # InvokeBulletBehavior: a projectile or spell is launched
COMMON = 5        # InvokeCommonBehavior


@dataclass
class Event:
    type: int
    start: float
    end: float
    params: tuple


def parse(data: bytes) -> tuple[int, dict[int, list[Event]]]:
    """(file id, {animation id within the file: events})."""
    if data[:4] != b'TAE ':
        raise ValueError('not a TAE file')
    file_id = struct.unpack_from('<i', data, 0x50)[0]
    count = struct.unpack_from('<i', data, 0x54)[0]
    anims_off = struct.unpack_from('<Q', data, 0x58)[0]
    out: dict[int, list[Event]] = {}
    for i in range(count):
        aid, aoff = struct.unpack_from('<QQ', data, anims_off + i * 16)
        if not aoff or aoff + 0x2C > len(data):
            continue
        ev_off, _grp, _times, _file, n_ev = struct.unpack_from('<QQQQi', data, aoff)
        events = []
        for k in range(max(0, n_ev)):
            s_off, e_off, d_off = struct.unpack_from('<QQQ', data, ev_off + k * 24)
            start = struct.unpack_from('<f', data, s_off)[0]
            end = struct.unpack_from('<f', data, e_off)[0]
            etype, p_off = struct.unpack_from('<QQ', data, d_off)
            params = struct.unpack_from('<4i', data, p_off) if p_off and p_off + 16 <= len(data) else ()
            events.append(Event(int(etype), start, end, params))
        out[int(aid)] = events
    return file_id, out


def attack_windows(events: list[Event]) -> list[tuple[float, float]]:
    """(start, end) seconds of each attack hitbox in an animation."""
    return sorted((e.start, e.end) for e in events if e.type == ATTACK and e.end > e.start)
