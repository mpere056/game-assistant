"""Elden Ring adapter: reads the Attack on Elden Ring bridge's shared memory.

Needs the bridge from https://github.com/mpere056/attack-on-elden-ring (a build with the assistant
ray block), with Elden Ring started through its Play-EldenRing.bat (me3 loads aoer_host.dll, which
creates the bridge). See docs/games/eldenring.md. Works whether or not AoTTG2 is linked: state, entities and rays are published whenever
a character is in the world. Layout: host/include/bridge_protocol.h in that repository.
"""
from __future__ import annotations

import json
import mmap
import struct
import time
from pathlib import Path

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_PLAYER, CAP_RAYCAST, Camera, Entity,
                              GameNotRunning, Player, RayHit, Snapshot, Vec3, cross, normalize, sub)

ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_FILE = ROOT / '.local' / 'eldenring' / 'npcs.json'  # phase 2, extracted from game data

SHM_NAME = 'Local\\AoER_bridge_v1'
SHM_SIZE = 8 * 1024 * 1024
MAGIC = 0x52454F41  # "AOER"

OFF_STATE = 0x100
STATE_SIZE = 0x120
OFF_ENTITIES = 0x200000
ENTITY_SIZE = 0x80
MAX_ENTITIES = 256
OFF_RAYS = 0x360000          # AOER_OFF_ASSIST_RAYS: the assistant's own ray block
MAX_RAYS = 1024              # AOER_ASSIST_MAX_RAYS
OFF_RAYS_ARR = OFF_RAYS + 0x20
OFF_HITS_ARR = OFF_RAYS + 0x20 + MAX_RAYS * 24

# ErmcGameState.flags
CAMERA_VALID, PLAYER_VALID, PLAYER_DEAD, HOST_BUSY = 1 << 0, 1 << 1, 1 << 7, 1 << 8
# ErmcEntity.kind
ENT_LARGE, ENT_SMALL, ENT_OTHER = 1, 2, 3

# Elden Ring is Y-up and (per the bridge's docs/CONTRACT.md) left-handed, like Unity: right = up x forward.
# Confirmed by the adapter tests on 2026-10-08. They check this ("is the enemy on your left or right?"); flip it if they fail.
LEFT_HANDED = True


def _seqlock_read(m: mmap.mmap, off: int, size_fn) -> bytes:
    for _ in range(500):
        a = struct.unpack_from('<I', m, off)[0]
        if a & 1:
            time.sleep(0.0005)
            continue
        blob = bytes(m[off:off + size_fn(m)])
        if struct.unpack_from('<I', m, off)[0] == a:
            return blob
    raise GameNotRunning('the bridge kept changing while being read (game stalled?)')


def _rotate(q: tuple[float, float, float, float], v: Vec3) -> Vec3:
    x, y, z, w = q
    qv = (x, y, z)
    t = cross(qv, v)
    t = (2 * t[0], 2 * t[1], 2 * t[2])
    c = cross(qv, t)
    return (v[0] + w * t[0] + c[0], v[1] + w * t[1] + c[1], v[2] + w * t[2] + c[2])


class EldenRingAdapter:
    game = 'Elden Ring'
    capabilities = frozenset({CAP_PLAYER, CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_KNOWLEDGE})

    def __init__(self) -> None:
        self._m: mmap.mmap | None = None
        self._knowledge: dict | None = None

    # ---- connection ----

    def _map(self) -> mmap.mmap:
        if self._m is None:
            self._m = mmap.mmap(-1, SHM_SIZE, tagname=SHM_NAME)
        if struct.unpack_from('<I', self._m, 0)[0] != MAGIC:
            raise GameNotRunning("The Elden Ring bridge is not running. Start Elden Ring with Attack on Elden Ring's Play-EldenRing.bat.")
        return self._m

    def heartbeat(self) -> int:
        return struct.unpack_from('<Q', self._map(), 0x10)[0]

    def is_live(self, wait: float = 0.25) -> bool:
        a = self.heartbeat()
        time.sleep(wait)
        return self.heartbeat() != a

    # ---- state ----

    def snapshot(self) -> Snapshot:
        m = self._map()
        s = _seqlock_read(m, OFF_STATE, lambda _: STATE_SIZE)
        flags, frame = struct.unpack_from('<IQ', s, 0x04)
        cam_pos = struct.unpack_from('<3f', s, 0x10)
        cam_target = struct.unpack_from('<3f', s, 0x1C)
        cam_up = struct.unpack_from('<3f', s, 0x28)
        fov, _near, _far, aspect = struct.unpack_from('<4f', s, 0x34)
        player_pos = struct.unpack_from('<3f', s, 0x48)
        player_quat = struct.unpack_from('<4f', s, 0x54)
        stage = struct.unpack_from('<I', s, 0x7C)[0]

        in_world = bool(flags & PLAYER_VALID) and not flags & HOST_BUSY
        camera = None
        if flags & CAMERA_VALID:
            fwd = normalize(sub(cam_target, cam_pos))
            up_hint = normalize(cam_up) if any(cam_up) else (0.0, 1.0, 0.0)
            right = normalize(cross(up_hint, fwd) if LEFT_HANDED else cross(fwd, up_hint))
            up = cross(fwd, right) if LEFT_HANDED else cross(right, fwd)
            camera = Camera(cam_pos, fwd, right, up, fov if fov > 1 else 60.0, aspect if aspect > 0.1 else 16 / 9)
        player = None
        if flags & PLAYER_VALID:
            state = 'dead' if flags & PLAYER_DEAD else 'busy' if flags & HOST_BUSY else 'alive'
            facing = normalize(_rotate(player_quat, (0.0, 0.0, 1.0))) if any(player_quat) else None
            player = Player(player_pos, facing, state)

        return Snapshot(self.game, frame, in_world, player, camera, self._entities(m) if in_world else (),
                        f'{stage:#x}' if stage else None)

    def _entities(self, m: mmap.mmap) -> tuple[Entity, ...]:
        blob = _seqlock_read(m, OFF_ENTITIES,
                             lambda mm: 0x10 + ENTITY_SIZE * min(struct.unpack_from('<I', mm, OFF_ENTITIES + 4)[0], MAX_ENTITIES))
        count = min(struct.unpack_from('<I', blob, 4)[0], MAX_ENTITIES, (len(blob) - 0x10) // ENTITY_SIZE)
        out = []
        for i in range(count):
            o = 0x10 + i * ENTITY_SIZE
            eid, kind, type_id = struct.unpack_from('<QII', blob, o)
            pos = struct.unpack_from('<3f', blob, o + 0x10)
            center = struct.unpack_from('<3f', blob, o + 0x2C)
            half = struct.unpack_from('<3f', blob, o + 0x38)
            hp, max_hp, eflags = struct.unpack_from('<ffI', blob, o + 0x44)
            name = blob[o + 0x50:o + 0x80].split(b'\0', 1)[0].decode('ascii', 'replace') or None
            out.append(Entity(
                id=eid, type_id=type_id or None, model=name, pos=pos, center=center,
                radius=half[0], height=half[1] * 2, hostile=kind in (ENT_LARGE, ENT_SMALL),
                dead=bool(eflags & 1), health=hp, max_health=max_hp))
        return tuple(out)

    # ---- world queries ----

    def raycast(self, rays: list[tuple[Vec3, Vec3]], timeout: float = 2.0) -> list[RayHit]:
        out: list[RayHit] = []
        for i in range(0, len(rays), MAX_RAYS):
            out += self._cast_batch(rays[i:i + MAX_RAYS], timeout)
        return out

    def _cast_batch(self, rays, timeout) -> list[RayHit]:
        m = self._map()
        deadline = time.monotonic() + timeout
        req, resp = struct.unpack_from('<II', m, OFF_RAYS)
        while req != resp:  # an earlier request of ours is still being served
            if time.monotonic() > deadline:
                raise TimeoutError('the bridge is still busy with an earlier ray request')
            time.sleep(0.002)
            req, resp = struct.unpack_from('<II', m, OFF_RAYS)
        for k, (a, b) in enumerate(rays):
            struct.pack_into('<6f', m, OFF_RAYS_ARR + k * 24, *a, *b)
        struct.pack_into('<II', m, OFF_RAYS + 8, len(rays), 0)  # count, flags = terrain filter
        struct.pack_into('<I', m, OFF_RAYS + 0x10, 0)           # processed
        struct.pack_into('<I', m, OFF_RAYS, req + 1)            # publish the request last
        while struct.unpack_from('<I', m, OFF_RAYS + 4)[0] != req + 1:
            if time.monotonic() > deadline:
                raise TimeoutError('no answer to a ray request: is a character loaded and the game unpaused? '
                                   '(an older aoer_core.dll without the assistant ray block also never answers)')
            time.sleep(0.002)
        hits = []
        for k, (a, b) in enumerate(rays):
            px, py, pz, nx, ny, nz, hit, _attr = struct.unpack_from('<6fII', m, OFF_HITS_ARR + k * 32)
            hits.append(RayHit(a, b, True, (px, py, pz), (nx, ny, nz)) if hit else RayHit(a, b, False))
        return hits

    # ---- knowledge ----

    def knowledge(self, type_id: int) -> dict | None:
        """Facts by NpcParam id from the extracted game data (phase 2). None until that exists."""
        if self._knowledge is None:
            self._knowledge = json.loads(KNOWLEDGE_FILE.read_text('utf-8')) if KNOWLEDGE_FILE.exists() else {}
        return self._knowledge.get(str(type_id))
