"""Elden Ring adapter: reads the Attack on Elden Ring bridge's shared memory.

Needs the bridge from https://github.com/mpere056/attack-on-elden-ring (a build with the assistant
ray block), with Elden Ring started through its Play-EldenRing.bat (me3 loads aoer_host.dll, which
creates the bridge). See docs/games/eldenring.md. Works whether or not AoTTG2 is linked: state,
entities and rays are published whenever a character is in the world. Layout: host/include/bridge_protocol.h in that repository.
"""
from __future__ import annotations

import json
import mmap
import struct
import threading
import time
from pathlib import Path

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_PLAYER, CAP_RAYCAST, CAP_SCREEN, CAP_SEARCH, Camera, Entity,
                              GameNotRunning, Player, RayHit, Screen, Snapshot, Vec3, cross, normalize, sub)
from .memory import Memory
from .knowledge import EldenRingKnowledge
from .params import read_graces, read_npc_params

ROOT = Path(__file__).resolve().parents[3]
NPC_DUMP = ROOT / '.local' / 'eldenring' / 'npc_params.json'   # what was read from memory, for checking

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
CAMERA_VALID, PLAYER_VALID, WINDOW_VALID, WINDOW_FOCUSED = 1 << 0, 1 << 1, 1 << 2, 1 << 5
PLAYER_DEAD, HOST_BUSY = 1 << 7, 1 << 8
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
    capabilities = frozenset({CAP_PLAYER, CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_KNOWLEDGE, CAP_SEARCH, CAP_SCREEN})
    # Wikis the assistant may search for attack patterns, strategies, lore and quests.
    web_sources = ('eldenring.wiki.fextralife.com', 'eldenring.fandom.com')
    # Words that help speech recognition hear game terms (Whisper's initial prompt).
    vocabulary = ('Elden Ring. Tarnished, Site of Grace, Torrent, runes, Flask of Crimson Tears, katana, '
                  'Moonveil, Rivers of Blood, Golden Seed, Sacred Tear, Smithing Stone, Ash of War, Spirit Ash, '
                  'Margit, Godrick, Rennala, Radahn, Rykard, Morgott, Malenia, Mohg, Maliketh, Ranni, Melina, '
                  'Limgrave, Liurnia, Caelid, Altus Plateau, Leyndell, Stormveil, Raya Lucaria, Volcano Manor, '
                  'Haligtree, Farum Azula, Siofra, Ainsel, Nokron, Weeping Peninsula, Scadutree. '
                  'What is that weak to? Where can I find it?')

    def __init__(self) -> None:
        self._m: mmap.mmap | None = None
        self._npcs: dict[int, dict] | None = None
        self.kb = EldenRingKnowledge()
        self._ray_lock = threading.Lock()  # one ray request at a time (fairy loop and agent share it)
        self._graces_tried = False

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
        wx, wy, ww, wh = struct.unpack_from('<4i', s, 0x64)
        screen = Screen(wx, wy, ww, wh, bool(flags & WINDOW_FOCUSED)) if flags & WINDOW_VALID and ww > 0 and wh > 0 else None

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
                        f'{stage:#x}' if stage else None, screen=screen,
                        menu=bool(flags & HOST_BUSY) or None)  # loading is known; menus are not detected yet

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
        with self._ray_lock:
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

    def _load_npcs(self) -> dict[int, dict]:
        """NpcParam rows read live from the game's memory, once per session."""
        if self._npcs is None:
            self._npcs = read_npc_params(Memory(self._map()))
            NPC_DUMP.parent.mkdir(parents=True, exist_ok=True)
            NPC_DUMP.write_text(json.dumps({str(k): v for k, v in self._npcs.items()}), 'utf-8')
        return self._npcs

    def knowledge(self, type_id: int) -> dict | None:
        """Facts about a character type (NpcParam id): numbers from the game, name from the lists."""
        row = self._load_npcs().get(type_id)
        if row is None:
            return None
        return npc_facts(row, self.kb.npc_name(type_id))

    def search(self, kind: str, query: str) -> dict:
        if not self._graces_tried:  # map tiles -> nearby graces, for open-world item locations
            self._graces_tried = True
            try:
                self.kb.set_graces(read_graces(Memory(self._map())))
            except (GameNotRunning, RuntimeError):
                pass
        out = self.kb.search(kind, query)
        if kind == 'enemy':
            for r in out.get('results', []):
                try:
                    facts = self.knowledge(r['type_ids'][0]) if r.get('type_ids') else None
                except (GameNotRunning, RuntimeError):
                    facts = None
                if facts:
                    r['stats_of_first_variant'] = {k: v for k, v in facts.items() if k not in ('name', 'name_note')}
        return out


STATUS = [('poison', 'resist_poison'), ('scarlet rot', 'resist_scarlet_rot'), ('bleed', 'resist_bleed'),
          ('frost', 'resist_frost'), ('sleep', 'resist_sleep'), ('madness', 'resist_madness'),
          ('death blight', 'resist_death_blight')]
DAMAGE = [('standard', 'taken_standard'), ('slash', 'taken_slash'), ('strike', 'taken_strike'),
          ('pierce', 'taken_pierce'), ('magic', 'taken_magic'), ('fire', 'taken_fire'),
          ('lightning', 'taken_lightning'), ('holy', 'taken_holy')]
IMMUNE = 999  # Elden Ring's "never builds up" value


def npc_facts(row: dict, name: str | None) -> dict:
    """Plain facts from one NpcParam row. Damage numbers are the share of each damage type the
    character takes (100 % = normal); status numbers are the buildup needed to trigger it.
    These are the base values: area scaling and special effects can change them in play."""
    taken = {k: round(row[f] * 100) for k, f in DAMAGE}
    usual = sorted(taken.values())[len(taken) // 2]  # the median
    status = {k: row[f] for k, f in STATUS}
    facts = {
        'name': name,
        'base_hp': row['hp'],
        'runes': row['runes'],
        'damage_taken_percent': taken,
        # A weakness is a damage type clearly above this enemy's own usual (some take extra from everything).
        'weak_to': [k for k, v in taken.items() if v >= 110 and v >= usual + 15],
        'resists': [k for k, v in taken.items() if v <= 80],
        'takes_extra_from_everything': usual >= 110,
        'status_buildup_needed': {k: v for k, v in status.items() if v < IMMUNE},
        'immune_to': [k for k, v in status.items() if v >= IMMUNE],
        'note': 'base values from the game data; area scaling and buffs can change them in play',
    }
    if not name:
        facts['name_note'] = 'no name for this type in the names list (run Get-GameData.bat if it is missing); say you do not know its name'
    return facts
