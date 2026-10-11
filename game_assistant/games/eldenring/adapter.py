"""Elden Ring adapter: reads the Attack on Elden Ring bridge's shared memory.

Needs the bridge from https://github.com/mpere056/attack-on-elden-ring (a build with the assistant
ray block), with Elden Ring started through its Play-EldenRing.bat (me3 loads aoer_host.dll, which
creates the bridge). See docs/games/eldenring.md. Works whether or not AoTTG2 is linked: state,
entities and rays are published whenever a character is in the world. Layout: host/include/bridge_protocol.h in that repository.
"""
from __future__ import annotations

import json
import math
import mmap
import struct
import threading
import time
from pathlib import Path

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_NAVMESH, CAP_PLACES, CAP_PLAYER, CAP_RAYCAST, CAP_SCREEN,
                              CAP_SEARCH, Camera, Entity,
                              GameNotRunning, Player, RayHit, Screen, Snapshot, Vec3, cross, normalize, sub)
from .memory import Memory
from .knowledge import EldenRingKnowledge
from .params import read_graces, read_legacy_conversions, read_map_points, read_npc_params
from .places import TILE, PlaceIndex, compass, place_words
from .wiki import Wiki

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


OBSERVE_EVERY = 0.25  # seconds between telling the route planner where the player is
ROUTE_CHECKS = 6      # plan, check against the world, avoid what's blocked: at most this many times
LEG_CHECK_M = 80.0    # check the route's straight legs this far ahead (it's re-planned as you go)
AVOID_RADIUS = 0.8    # metres around a blocked spot the route planner then keeps clear of
BRIDGE_MAX = 40.0     # a navmesh route ending this close to its goal is finished with the raycast planner


class EldenRingAdapter:
    game = 'Elden Ring'
    capabilities = frozenset({CAP_PLAYER, CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_KNOWLEDGE, CAP_SEARCH, CAP_SCREEN, CAP_PLACES})
    # Wikis the assistant may search for attack patterns, strategies, lore and quests.
    web_sources = ('eldenring.wiki.fextralife.com', 'eldenring.fandom.com')
    # Words that help speech recognition hear game terms (Whisper's initial prompt).
    vocabulary = ('Elden Ring. Tarnished, Site of Grace, Torrent, runes, Flask of Crimson Tears, katana, '
                  'Moonveil, Rivers of Blood, Golden Seed, Sacred Tear, Smithing Stone, Ash of War, Spirit Ash, '
                  'Margit, Godrick, Rennala, Radahn, Rykard, Morgott, Malenia, Mohg, Maliketh, Ranni, Melina, '
                  'Limgrave, Liurnia, Caelid, Altus Plateau, Leyndell, Stormveil, Raya Lucaria, Volcano Manor, '
                  'Haligtree, Farum Azula, Siofra, Ainsel, Nokron, Weeping Peninsula, Scadutree. '
                  'Spectral Steed Whistle, Spiritspring, inventory, Key Items, Stonesword Key, Kalé. '
                  'What is that weak to? Where can I find it?')

    def __init__(self) -> None:
        self._m: mmap.mmap | None = None
        self._npcs: dict[int, dict] | None = None
        self.kb = EldenRingKnowledge()
        self._ray_lock = threading.Lock()  # one ray request at a time (fairy loop and agent share it)
        self._graces_tried = False
        self._places: PlaceIndex | None = None
        self.wiki = Wiki()
        self._router = None  # the route planner's own process (core/route_worker.py), started when needed
        self._banned_links: set = set()  # drops/entrances a live ray found blocked (this session)
        self._last_observe = 0.0
        self._last_special: list = []  # (from, to, start in snapshot coordinates) of the last route
        self._avoid: list = []         # (x, y, z, radius) in world coordinates: legs found blocked
        self._route_lock = threading.Lock()
        if (Path(__file__).resolve().parents[3] / '.local' / 'eldenring' / 'navgraph' / 'index.json').exists():
            self.capabilities = self.capabilities | {CAP_NAVMESH}  # Get-GameData built the walkable world

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

    def warm_up(self) -> None:
        """Load the game data (enemy stats, graces, places) ahead of the first question. Called in the
        background at start-up; quietly does nothing until a character is in the world."""
        try:
            if self.snapshot().in_world:
                self._load_npcs()
                self._place_index()
                if not self._graces_tried:
                    self._graces_tried = True
                    self.kb.set_graces(read_graces(Memory(self._map())))
        except (GameNotRunning, RuntimeError):
            pass

    def _place_index(self) -> PlaceIndex:
        if self._places is None:
            mem = Memory(self._map())
            conv = read_legacy_conversions(mem)
            self._places = PlaceIndex(read_graces(mem), read_map_points(mem), conv,
                                      self.kb.lists['BonfireWarpParam'], self.kb.lists['WorldMapPointParam'])
            try:  # kept for building the walkable world graph without the game (navgraph.py)
                from .navgraph import save_conversions
                save_conversions(conv)
            except OSError:
                pass
        return self._places

    # ---- routes over the game's own navmesh ----

    def route(self, start: Vec3, goal: Vec3, budget_s: float = 3.0):
        """A walkable route from start toward goal (snapshot coordinates) over the game's own navmesh,
        searched in the planner's own process (navgraph.WorldGraph, loaded block by block), or None.
        The result has waypoints, reaches_goal, length_m and timed_out: when the time budget runs out
        it is the best part of the way so far, and the caller plans on from its end while moving."""
        from ...core.nav import Route
        snap = self.snapshot()
        if not snap.player or not snap.area:
            return None
        idx = self._place_index()
        pos = snap.player.pos
        me = idx.player_world(int(snap.area, 16), pos)
        if me is None:
            return None
        off = (me[1] - pos[0], me[2] - pos[1], me[3] - pos[2])
        s = (start[0] + off[0], start[1] + off[1], start[2] + off[2])
        g = (goal[0] + off[0], goal[1] + off[1], goal[2] + off[2])
        with self._route_lock:
            self._ensure_router()
            for _ in range(ROUTE_CHECKS):  # checked against the real world before it is used
                r = self._router.route(s, g, budget_s=budget_s, timeout=budget_s + 20,
                                       attrs={'area': me[0], 'banned': set(self._banned_links),
                                              'avoid': list(self._avoid)})
                if r is None:
                    return None
                blocked = self._blocked_links(r.special or [], off)
                walls = [] if blocked else self._blocked_legs(r, off)
                if not blocked and not walls:
                    break
                self._banned_links |= blocked
                for hit in walls:  # avoid the spot, and any made-up link near it
                    self._avoid.append((hit[0] + off[0], hit[1] + off[1], hit[2] + off[2], AVOID_RADIUS))
                    self._banned_links |= {(k[3], k[4]) for k in (r.special or []) if k[0] in (1, 2, 3, 8) and
                                           math.dist(((k[1][0] + k[2][0]) / 2 - off[0], (k[1][1] + k[2][1]) / 2 - off[1],
                                                      (k[1][2] + k[2][2]) / 2 - off[2]), hit) < 3.0}
        self._last_special = [(frm, to, ((pa[0] + pb[0]) / 2 - off[0], (pa[1] + pb[1]) / 2 - off[1],
                                          (pa[2] + pb[2]) / 2 - off[2]))
                              for k, pa, pb, frm, to, _land in (r.special or []) if k in (1, 2, 3, 8)]
        route = Route([(w[0] - off[0], w[1] - off[1], w[2] - off[2]) for w in r.waypoints], r.reaches_goal, 0)
        route.length_m, route.timed_out = r.length, r.timed_out
        route.bridged = False
        if not r.reaches_goal:  # (also after a timed-out search: the gap left is what matters)
            self._bridge(route, goal)
            if route.bridged:
                route.timed_out = False
        # Things on the way: (what, where it starts, where it ends) for ladders, lifts, jumps, drops.
        from ...core.navmesh import LINK_NAMES
        route.actions = [(LINK_NAMES[k], ((pa[0] + pb[0]) / 2 - off[0], (pa[1] + pb[1]) / 2 - off[1],
                                          (pa[2] + pb[2]) / 2 - off[2]),
                          (land[0] - off[0], land[1] - off[1], land[2] - off[2]))
                         for k, pa, pb, _f, _t, land in (r.special or []) if k in (2, 4, 5, 6)]
        return route

    def avoid_links_near(self, pos: Vec3, radius: float = 5.0) -> int:
        """The character got stuck at pos: don't use the made-up links (entrance gaps, steps, drops,
        learned) of the last route near there again this session. Returns how many."""
        near = {(frm, to) for frm, to, where in self._last_special if math.dist(where, pos) < radius}
        self._banned_links |= near
        return len(near)

    def _ensure_router(self):
        from ...core.route_worker import RouteWorker
        if self._router is None or not self._router.alive:
            self._router = RouteWorker('game_assistant.games.eldenring.navgraph:WorldGraph')
        return self._router

    def observe(self, snap) -> None:
        """Called every fairy frame: about 4 times a second, tell the route planner where the player is,
        so it learns connections the game's navmesh lacks from where the player actually goes."""
        if CAP_NAVMESH not in self.capabilities or snap is None:
            return
        now = time.monotonic()
        if now - self._last_observe < OBSERVE_EVERY:
            return
        self._last_observe = now
        try:
            if not snap.in_world or not snap.player or snap.menu or not snap.area:
                self._ensure_router().observe(p=None)
                return
            me = self._place_index().player_world(int(snap.area, 16), snap.player.pos)
            if me is None:
                return
            self._ensure_router().observe(p=(me[1], me[2], me[3]), area=me[0], t=now)
        except (GameNotRunning, RuntimeError, OSError):
            pass

    def _bridge(self, route, goal: Vec3) -> None:
        """The navmesh route ends short of a goal less than BRIDGE_MAX metres away: finish the trip with the
        raycast planner (1 m cells, the game's real collision, walls checked). Some passages have no navmesh
        at all: Groveside Cave's entrance has none for ~10 m; this got from the route's end to its grace in
        0.14 s. Rays start just above head height, so an overhang doesn't hide the floor."""
        from ...core import nav
        end = route.waypoints[-1]
        gap = math.dist(end, goal)
        if gap > BRIDGE_MAX or gap < 2.0:
            return
        prof = nav.Profile(cell=1.0, radius=min(BRIDGE_MAX, gap + 10.0), probe_up=1.8, probe_down=6.0, max_up=0.6,
                           max_down=0.9, min_normal_y=0.6, chunk=1024)
        try:
            b = nav.plan(self.raycast, end, goal, profile=prof)
        except (GameNotRunning, TimeoutError, RuntimeError):
            return
        if b is None or not b.reaches_goal or len(b.waypoints) < 2:
            return
        route.waypoints = list(route.waypoints) + list(b.waypoints[1:])
        route.length_m += sum(math.dist(u, v) for u, v in zip(b.waypoints, b.waypoints[1:]))
        route.reaches_goal, route.bridged = True, True

    def _blocked_legs(self, r, off: Vec3) -> list:
        """Where the route's straight legs (the first LEG_CHECK_M metres) run into a wall or rock: rays
        at knee and chest height along each leg. The navmesh can be right while the straight line between
        two corners is not (a step or drop link across a gap, an overhang): measured at a cave mouth near
        Stormhill, two legs went through rock 0.1 and 0.4 m along."""
        pts = [(w[0] - off[0], w[1] - off[1], w[2] - off[2]) for w in r.waypoints]
        rays, walked = [], 0.0
        for u, v in zip(pts, pts[1:]):
            if walked > LEG_CHECK_M:
                break
            walked += math.dist(u, v)
            if math.dist(u, v) < 0.3:
                continue
            for lift in (0.5, 1.2):
                rays.append(((u[0], u[1] + lift, u[2]), (v[0], v[1] + lift, v[2])))
        if not rays:
            return []
        try:
            hits = self.raycast(rays)
        except (GameNotRunning, TimeoutError, RuntimeError):
            return []
        return [h.pos for h in hits if h.hit and h.pos and h.normal and abs(h.normal[1]) < 0.6]

    def _blocked_links(self, special: list, off: Vec3) -> set:
        """Special links (drops, steps, dungeon entrances) that a ray shows are blocked by a wall or a
        rock: from the edge toward the landing at chest height (and knee height for steps)."""
        rays, keys = [], []
        for kind, pa, pb, frm, to, land in special:
            if kind not in (1, 2, 3):  # ladders, lifts, jumps, doors come from the game itself
                continue
            mid = ((pa[0] + pb[0]) / 2 - off[0], (pa[1] + pb[1]) / 2 - off[1], (pa[2] + pb[2]) / 2 - off[2])
            lx, lz = land[0] - off[0] - mid[0], land[2] - off[2] - mid[2]
            d = math.hypot(lx, lz)
            if d < 0.3:
                continue
            reach = {2: 2.5, 3: 1.5}.get(kind, min(d, 9.0))  # drop, step, entrance
            for lift in ((0.5, 1.2) if kind == 3 else (1.2,)):  # steps: low rocks and fences too
                a = (mid[0], mid[1] + lift, mid[2])
                b = (mid[0] + lx / d * reach, mid[1] + lift, mid[2] + lz / d * reach)
                rays.append((a, b))
                keys.append((frm, to))
        if not rays:
            return set()
        try:
            hits = self.raycast(rays)
        except (GameNotRunning, TimeoutError, RuntimeError):
            return set()  # can't check now: use the route as planned
        return {k for k, h in zip(keys, hits) if h.hit and h.normal and abs(h.normal[1]) < 0.6}

    def _describe(self, pl, me, snap) -> dict:
        r = {'name': pl.name, 'region': pl.region, 'kind': pl.kind}
        if pl.world is None or me is None:
            r['note'] = 'its map position is not known' if pl.world is None else 'your own map position is not known here'
        elif pl.world[0] != me[0]:
            r['note'] = 'it is in the other world (the Lands Between vs the Realm of Shadow)'
        else:
            dx, dy, dz = pl.world[1] - me[1], pl.world[2] - me[2], pl.world[3] - me[3]
            p = snap.player.pos
            r.update(distance_m=round((dx * dx + dz * dz) ** 0.5), compass=compass(dx, dz), height_diff_m=round(dy),
                     position=(p[0] + dx, p[1] + dy, p[2] + dz))
            if abs(dy) > 15:
                r['height_note'] = (f'about {abs(round(dy))} m {"below" if dy < 0 else "above"} you: '
                                    f'{"probably in a cave or tunnel, or down a cliff" if dy < 0 else "up a cliff or a tower"}')
        return r

    def where_am_i(self) -> dict:
        """The player's surroundings by name: nearest grace and landmarks, region, and the wiki page
        that best describes this spot."""
        snap = self.snapshot()
        if not snap.in_world or not snap.player:
            return {'error': 'no character in the world right now'}
        idx = self._place_index()
        me = idx.player_world(int(snap.area, 16) if snap.area else 0, snap.player.pos)
        if me is None:
            return {'error': 'your map position is not known here (a place without a world-map conversion)'}
        graces = [self._describe(pl, me, snap) for pl in idx.nearest(me, 'site of grace', 2)]
        marks = [self._describe(pl, me, snap) for pl in idx.nearest(me, 'landmark', 3)]
        for r in graces + marks:
            r.pop('position', None)
        page = None
        if self.wiki.ok:  # the closest named place the wiki knows
            for r in sorted(graces + marks, key=lambda r: r.get('distance_m', 1e9)):
                if r.get('distance_m', 1e9) <= 400 and self.wiki.find_page(r['name']):
                    page = r['name']
                    break
            if page is None:
                region = next((r.get('region') for r in graces + marks if r.get('region')), None)
                page = region if region and self.wiki.find_page(region) else None
        world = 'the Realm of Shadow' if me[0] == 61 else 'the Lands Between'
        return {'world': world, 'nearest_graces': graces, 'nearest_landmarks': marks, 'wiki_page_for_here': page}

    def items_near_me(self, kind: str | None = None, limit: int = 8) -> dict:
        """Pickups in the player's map square and the ones around it (open world), and in named
        places within about 400 m. kind: weapon, armour, talisman, item, ash of war, spell."""
        from .knowledge import ITEM_FILES, TAGGED, _norm
        snap = self.snapshot()
        if not snap.in_world or not snap.player:
            return {'error': 'no character in the world right now'}
        idx = self._place_index()
        me = idx.player_world(int(snap.area, 16) if snap.area else 0, snap.player.pos)
        if me is None:
            return {'error': 'your map position is not known here'}
        kinds = {}
        for fname, k in ITEM_FILES.items():
            for name in self.kb.lists[fname].values():
                kinds.setdefault(_norm(name), k)
        tx, tz = int(me[1] // TILE), int(me[3] // TILE)
        found = []
        for rid, text in self.kb.lists['ItemLotParam_map'].items():
            m = TAGGED.match(text)
            item = m.group('what') if m else text
            k = kinds.get(_norm(item))
            if k is None or (kind and k != kind):
                continue
            s_ = str(rid)
            if not m and len(s_) == 10 and s_[0] in '12' and s_[1] == '0':  # open world: its map square
                area, gx, gz = (60 if s_[0] == '1' else 61), int(s_[2:4]), int(s_[4:6])
                if area != me[0] or max(abs(gx - tx), abs(gz - tz)) > 1:
                    continue
                cx, cz = (gx + 0.5) * TILE, (gz + 0.5) * TILE
                d = ((cx - me[1]) ** 2 + (cz - me[3]) ** 2) ** 0.5
                where = 'in this map square' if (gx, gz) == (tx, tz) else f'in the next map square, {compass(cx - me[1], cz - me[3])}'
                found.append({'item': item, 'kind': k, 'where': where + ' (no exact spot in the data)', '_d': d})
            elif m:
                place = next((pl for w in place_words(m.group('where')) for pl in idx.find(w, 1)), None)
                if place and place.world and place.world[0] == me[0]:
                    d = ((place.world[1] - me[1]) ** 2 + (place.world[3] - me[3]) ** 2) ** 0.5
                    if d <= 400:
                        found.append({'item': item, 'kind': k, 'where': m.group('where'), 'distance_m': round(d),
                                      'compass': compass(place.world[1] - me[1], place.world[3] - me[3]), '_d': d})
        found.sort(key=lambda r: r['_d'])
        seen, out = set(), []
        for r in found:
            if r['item'] not in seen:
                seen.add(r['item'])
                r.pop('_d')
                out.append(r)
            if len(out) >= limit:
                break
        return {'results': out, 'note': 'open-world pickups are only known by map square (256 m)'}

    def nearest_places(self, kind: str = 'site of grace', limit: int = 5) -> dict:
        """The places of a kind ('site of grace' or 'landmark') nearest the player, nearest first."""
        snap = self.snapshot()
        if not snap.in_world or not snap.player:
            return {'error': 'no character in the world right now'}
        idx = self._place_index()
        me = idx.player_world(int(snap.area, 16) if snap.area else 0, snap.player.pos)
        if me is None:
            return {'error': 'your own map position is not known here (a place without a world-map conversion)'}
        return {'kind': kind, 'results': [self._describe(pl, me, snap) for pl in idx.nearest(me, kind, limit)]}

    def locate(self, query: str) -> dict:
        """Where a named place is relative to the player, for guiding. The query may also be an item:
        then its first known location is used ("lead me to the Moonveil" -> Gael Tunnel)."""
        snap = self.snapshot()
        if not snap.in_world or not snap.player:
            return {'error': 'no character in the world right now'}
        idx = self._place_index()
        zone = int(snap.area, 16) if snap.area else 0
        me = idx.player_world(zone, snap.player.pos)
        places, via = idx.find(query), None
        if not places:
            fixed = self.kb.closest_name('place', query)
            places = idx.find(fixed) if fixed else []
        if not places:  # maybe an item: go to where it is found
            for item in self.kb.find_item(query, limit=2):
                for loc in item.get('found_in_world', []):
                    for word in place_words(loc):
                        places = idx.find(word)
                        if places:
                            via = f"{item['name']} is at {loc}"
                            break
                    if places:
                        break
                if places:
                    break
        out = [self._describe(pl, me, snap) for pl in places]
        res = {'query': query, 'results': out}
        if via:
            res['found_via_item'] = via
        if not out:
            res['note'] = 'no place with that name'
        return res

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
