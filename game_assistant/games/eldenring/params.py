"""Reads Elden Ring's parameter tables straight from the running game's memory.

Nothing is unpacked from the game files and nothing is downloaded: the game keeps every param
table loaded, so we find a table by its struct name (for example "NPC_PARAM_ST") and read its rows.
Because these are the tables the game actually uses, the numbers are also right for mods that
change them (Convergence and others).

Layouts follow fromsoftware-rs (MIT, https://github.com/vswarte/fromsoftware-rs, commit 59fbd3b):
`fd4/param_repository.rs` (ParamFile) and `param/generated.rs` (NPC_PARAM_ST, 0x2E0 bytes).

ParamFile (64-bit format):
    0x00 u32 strings offset     0x0A u16 row count
    0x10 u32 struct name offset (when format_2d bit 7 is set)
    0x2D u8  format_2d (bit 2: 64-bit offsets)
    0x40     row descriptors, 24 bytes each: u32 id, u32 pad, u64 data offset, u64 name offset
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .memory import Memory, MailboxError

DESC_SIZE = 24
HEADER_SIZE = 0x40

# NPC_PARAM_ST (Elden Ring 1.17.1 regulation), computed from fromsoftware-rs param/generated.rs.
NPC_PARAM_SIZE = 0x2E0
NPC_FIELDS = {
    # name: (offset, struct format)
    'name_id': (0x0C, 'i'),
    'hp': (0x24, 'I'),
    'runes': (0x2C, 'I'),             # get_soul
    'item_lot_id_enemy': (0x30, 'i'),
    'resist_poison': (0x108, 'H'),
    'resist_scarlet_rot': (0x10A, 'H'),  # resist_desease
    'resist_bleed': (0x10C, 'H'),        # resist_blood
    'resist_death_blight': (0x10E, 'H'), # resist_curse
    'resist_sleep': (0x126, 'H'),
    'resist_madness': (0x128, 'H'),
    'npc_type': (0x132, 'B'),
    'team_type': (0x133, 'B'),
    'poise': (0x19C, 'I'),               # toughness
    'taken_standard': (0x1A4, 'f'),      # neutral_damage_cut_rate: damage taken multiplier
    'taken_slash': (0x1A8, 'f'),
    'taken_strike': (0x1AC, 'f'),        # blow
    'taken_pierce': (0x1B0, 'f'),        # thrust
    'taken_magic': (0x1B4, 'f'),
    'taken_fire': (0x1B8, 'f'),
    'taken_lightning': (0x1BC, 'f'),     # thunder
    'taken_holy': (0x1C0, 'f'),          # dark (Elden Ring's internal name for holy)
    'resist_frost': (0x1E0, 'H'),        # resist_freeze
    'role_name_id': (0x230, 'i'),
}


class ParamNotFound(RuntimeError):
    pass


@dataclass
class ParamTable:
    struct_name: str
    address: int
    row_size: int
    rows: dict[int, bytes]  # row id -> raw row bytes


# SoloParamRepository (Elden Ring 1.17.1): a static pointer in eldenring.exe, as used by the Attack on
# Elden Ring bridge (host/src/game.cpp kSoloParamRepository). Holders of 0x48 bytes start at +0x88;
# holder -> res cap (+0x80) -> FD4ParamResCap (+0x80) -> ParamFile. Holder numbers measured in the running
# game on 2026-10-09 (they differ from fromsoftware-rs's INDEX values); other holders are tried if wrong.
SOLO_PARAM_REPOSITORY_RVA = 0x3D85F58
HOLDERS, HOLDER_SIZE, HOLDER_COUNT = 0x88, 0x48, 194
PARAM_INDEX = {'NPC_PARAM_ST': 6, 'BONFIRE_WARP_PARAM_ST': 43}


def struct_name_at(mem: Memory, f: int) -> str | None:
    head = mem.read(f, HEADER_SIZE)
    if head[0x2D] & 0x80:
        off = struct.unpack_from('<I', head, 0x10)[0]
        if not 0 < off < 0x10000000:  # big tables keep their name far in (NpcParam: several MB)
            return None
        raw = mem.read(f + off, 64)
    else:
        raw = head[0x0C:0x2C]
    return raw.split(b'\0', 1)[0].decode('ascii', 'replace')


def find_via_repository(mem: Memory, struct_name: str) -> int | None:
    """Follow the game's own param repository. None if the layout doesn't match."""
    base = mem.modules().get('eldenring.exe', (0, 0))[0]
    if not base:
        return None
    try:
        rep = mem.u64(base + SOLO_PARAM_REPOSITORY_RVA)
        if not rep:
            return None
        hint = PARAM_INDEX.get(struct_name)
        order = ([hint] if hint is not None else []) + [i for i in range(HOLDER_COUNT) if i != hint]
        for i in order:
            rescap = mem.u64(rep + HOLDERS + HOLDER_SIZE * i)
            fd4 = mem.u64(rescap + 0x80) if rescap else 0
            f = mem.u64(fd4 + 0x80) if fd4 else 0
            if f and struct_name_at(mem, f) == struct_name:
                return f
    except MailboxError:
        return None
    return None


def find_param_file(mem: Memory, struct_name: str) -> int:
    """Address of the loaded ParamFile whose struct name is `struct_name`.

    Only the fast path through the game's param repository is used. A memory-wide search for the
    struct name was tried first (2026-10-08) and took many minutes inside the game, blocking the
    bridge's mailbox for everyone (including the AoTTG2 plugin) until it finished; never again."""
    f = find_via_repository(mem, struct_name)
    if f:
        return f
    raise ParamNotFound(f'{struct_name} not found through the param repository: a different game version? '
                        '(the repository address is for Elden Ring 1.17.1)')


def read_param(mem: Memory, struct_name: str, row_size: int) -> ParamTable:
    f = find_param_file(mem, struct_name)
    head = mem.read(f, HEADER_SIZE)
    count = struct.unpack_from('<H', head, 0x0A)[0]
    descs = mem.read(f + HEADER_SIZE, count * DESC_SIZE)
    entries = [struct.unpack_from('<IIQ', descs, i * DESC_SIZE)[::2] for i in range(count)]  # (id, data offset)
    offsets = sorted(o for _, o in entries)
    strides = {b - a for a, b in zip(offsets, offsets[1:]) if b != a}
    if strides and min(strides) != row_size:
        raise ParamNotFound(f'{struct_name}: rows are {min(strides):#x} bytes apart, expected {row_size:#x} '
                            '(a different game version or paramdef)')
    lo, hi = offsets[0], offsets[-1] + row_size
    blob = mem.read(f + lo, hi - lo)
    rows = {rid: blob[o - lo:o - lo + row_size] for rid, o in entries}
    return ParamTable(struct_name, f, row_size, rows)


def npc_fields(row: bytes) -> dict:
    return {k: struct.unpack_from('<' + fmt, row, off)[0] for k, (off, fmt) in NPC_FIELDS.items()}


def read_npc_params(mem: Memory) -> dict[int, dict]:
    t = read_param(mem, 'NPC_PARAM_ST', NPC_PARAM_SIZE)
    return {rid: npc_fields(row) for rid, row in t.rows.items()}


# BONFIRE_WARP_PARAM_ST (Sites of Grace), 0xEC bytes, from fromsoftware-rs param/generated.rs.
BONFIRE_WARP_PARAM_SIZE = 0xEC


def read_graces(mem: Memory) -> dict[int, dict]:
    """Every Site of Grace: map area (60 = the Lands Between, 61 = Realm of Shadow), map tile and the
    position inside that tile's map."""
    t = read_param(mem, 'BONFIRE_WARP_PARAM_ST', BONFIRE_WARP_PARAM_SIZE)
    out = {}
    for rid, row in t.rows.items():
        area, gx, gz = row[0x20], row[0x21], row[0x22]
        if area:
            out[rid] = {'area': area, 'grid_x': gx, 'grid_z': gz, 'pos': struct.unpack_from('<3f', row, 0x24)}
    return out
