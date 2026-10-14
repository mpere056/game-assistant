"""Minecraft (Java Edition 26.3) through the Game Assistant Bridge, a client-side Fabric mod
(bridges/minecraft/): one JSON line per request over 127.0.0.1:47821.

What the bridge gives: the player (position, health, food, held item), the camera (position, forward/up/left
vectors, field of view), the creatures within 64 blocks (type, name, health, hostile), what the crosshair is
on (the game's own pick: a creature, or a block and its face), the window, and rays through the blocks.
Single-player only: on a server the bridge publishes nothing.

Coordinates: Minecraft's world is right-handed (+X east, +Z south, Y up); the core's headings assume the
other hand (a heading growing clockwise seen from above is "to the right"), so the adapter mirrors X both
ways: positions and vectors out of the bridge, rays into it. One block is one metre.
"""
from __future__ import annotations

import json
import math
import re
import socket
import threading
import time
import zlib

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_NAVMESH, CAP_PLACES, CAP_PLAYER,
                               CAP_RAYCAST, CAP_SCREEN, CAP_TARGET, Camera, Entity, GameNotRunning, Player, RayHit,
                               Screen, Snapshot, Vec3, normalize)
from ...core import terrain
from . import blocks

PORT = 47821
SNAPSHOT_CACHE = 0.010     # seconds a snapshot is reused (several threads ask every frame)
CONNECT_TIMEOUT = 0.5
REPLY_TIMEOUT = 2.0
ROUTE_REACH = 48          # blocks: one plan covers at most this far (sideways); a farther goal is planned in legs
ROUTE_MARGIN = 10         # blocks round the start and the goal in the box read for planning
ROUTE_DEPTH = 12          # blocks above and below
FOODS = {'golden_carrot': 14, 'cooked_beef': 13, 'cooked_porkchop': 13, 'cooked_mutton': 12, 'cooked_salmon': 12,
         'cooked_chicken': 11, 'cooked_rabbit': 10, 'cooked_cod': 10, 'bread': 9, 'baked_potato': 9, 'pumpkin_pie': 9,
         'mushroom_stew': 8, 'beetroot_soup': 8, 'rabbit_stew': 10, 'apple': 6, 'carrot': 5, 'melon_slice': 4,
         'sweet_berries': 3, 'glow_berries': 3, 'cookie': 3, 'dried_kelp': 2, 'beef': 4, 'porkchop': 4, 'mutton': 3,
         'potato': 2, 'beetroot': 2, 'salmon': 2, 'cod': 2, 'rabbit': 2, 'chicken': 1, 'rotten_flesh': 0}
CROUCH_EYE = 1.27         # the eye above the feet, crouched (standing: 1.62)
PLACE_RADIUS = 3          # blocks round the player looked at for a spot to put a block down
PLACE_TRIES = 24          # ...the best this many checked for sight
BLOCK_REACH = 4.4         # blocks from the eye a block can be mined or placed at (the game: 4.5)
FAST = {'spider', 'cave_spider', 'wolf', 'enderman', 'vex', 'phantom', 'bee', 'silverfish', 'endermite',
        'hoglin', 'zoglin', 'piglin_brute', 'ravager', 'breeze'}    # as quick as the player or quicker
RANGED = {'skeleton', 'stray', 'bogged', 'pillager', 'blaze', 'ghast', 'witch', 'drowned', 'shulker', 'breeze',
          'piglin', 'llama', 'snow_golem'}
BRIDGE_VERSION = 17        # the bridge (bridges/minecraft Bridge.VERSION) this adapter needs
SLOW_BREAK = 15           # a block slower than this to break (the bridge's hardness byte: 4 ticks a unit, 3 s)...
SLOW_BREAKS_FOR_TOOL = 3  # ...this many on a route: weigh making a tool first
TOOL_WORTH_S = 20.0       # seconds a tool must save, all told, to be made first
NO_PLACE_S = 120.0        # seconds a cell where placing failed isn't planned for again
GROUND_RADIUS = 12        # blocks round the player searched for a way down to the land itself (out of a tree)
LAND_RADIUS = 96          # blocks round the player read for the lie of the land (hills) in the survey
SKY_RADIUS = 16           # blocks round the player searched for a way up to the open sky
BOX_ASK = 192             # blocks: ask the game for a located structure's area once this near (loaded by then)
# What players call places, and the game's own ids for them (the rest are matched by their words).
PLACE_ALIASES = {'temple': 'minecraft:desert_pyramid', 'pyramid': 'minecraft:desert_pyramid',
                 'jungle temple': 'minecraft:jungle_pyramid', 'witch hut': 'minecraft:swamp_hut',
                 'end portal': 'minecraft:stronghold', 'nether portal': 'minecraft:ruined_portal',
                 'portal': 'minecraft:ruined_portal', 'mansion': 'minecraft:mansion', 'outpost': 'minecraft:pillager_outpost',
                 'monument': 'minecraft:monument', 'fortress': 'minecraft:fortress', 'trial chamber': 'minecraft:trial_chambers',
                 'trial chambers': 'minecraft:trial_chambers', 'bastion': 'minecraft:bastion_remnant'}
# What players call kinds of block, and the blocks that are (mining stone gives cobblestone; 'wood' is any log).
BLOCK_ALIASES = {
    'cobblestone': ('minecraft:stone', 'minecraft:cobblestone', 'minecraft:mossy_cobblestone'),
    'cobble': ('minecraft:stone', 'minecraft:cobblestone'),
    'stone': ('minecraft:stone', 'minecraft:cobblestone'),
    'rock': ('minecraft:stone', 'minecraft:cobblestone'),
    'wood': '_log', 'log': '_log', 'tree': '_log', 'timber': '_log',
    'iron': ('minecraft:iron_ore', 'minecraft:deepslate_iron_ore'),
    'coal': ('minecraft:coal_ore', 'minecraft:deepslate_coal_ore'),
    'copper': ('minecraft:copper_ore', 'minecraft:deepslate_copper_ore'),
    'gold': ('minecraft:gold_ore', 'minecraft:deepslate_gold_ore', 'minecraft:nether_gold_ore'),
    'diamond': ('minecraft:diamond_ore', 'minecraft:deepslate_diamond_ore'),
    'redstone': ('minecraft:redstone_ore', 'minecraft:deepslate_redstone_ore'),
    'lapi': ('minecraft:lapis_ore', 'minecraft:deepslate_lapis_ore'),
    'lapis': ('minecraft:lapis_ore', 'minecraft:deepslate_lapis_ore'),
    'emerald': ('minecraft:emerald_ore', 'minecraft:deepslate_emerald_ore'),
    'water': ('minecraft:water',), 'lava': ('minecraft:lava',),
}
_FILLER_WORDS = {'the', 'a', 'an', 'nearest', 'closest', 'nearby', 'near', 'me', 'some', 'any', 'to', 'of', 'biome',
                 'block', 'blocks', 'ore', 'ores', 'go', 'take', 'find', 'where', 'is', 'are', 'please'}

# For the model: what's particular about Minecraft (added to its instructions).
PROMPT_NOTES = """- Minecraft facts: the game reports names, health (in half-hearts: 20 health is 10 hearts) and the
  block or creature at the crosshair exactly. Recipes, how to get or use things, mob behaviour and
  strategies: search the Minecraft Wiki (web_search) rather than answering from memory.
- Distances are in blocks (one block is about a metre); say "blocks".
- Moving: the character's own walk finds the way and takes it all into account: it steps up one block, drops
  up to 3 onto ground and any height into water (no harm), swims at the surface, climbs ladders, jumps gaps
  (a walking jump clears 1 block, a sprinting jump 2, with a run-up 3; a sprinting jump also goes up 1 over a
  1-block gap), mines through what's in the way (with the best tool held; it makes a pickaxe first when that's
  quicker), digs down, pillars up and bridges with blocks held. So "go there" is usually one order, however
  rough the way."""


def _mirror(v) -> Vec3:
    return (-float(v[0]), float(v[1]), float(v[2]))


def _type_id(key: str) -> int:
    """A stable number for an entity type ('minecraft:zombie'): the core keys knowledge by integers."""
    return zlib.crc32(key.encode('utf-8')) & 0x7FFFFFFF


class MinecraftAdapter:
    game = 'Minecraft'
    capabilities = frozenset({CAP_PLAYER, CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_TARGET, CAP_KNOWLEDGE,
                              CAP_SCREEN, CAP_PLACES, CAP_NAVMESH})
    place_kinds = ()                       # every place is found by name ('the nearest village'): locate()
    # how orders and tools talk about Minecraft's places and creatures (core/agent.py ORDER_WORDS)
    order_words = {'places': 'a village, a stronghold, a desert temple, a biome such as a cherry grove, or a '
                             'kind of block such as stone, iron ore or a tree',
                   'named': "'village', 'stronghold'",
                   'nearest': "'nearest village', 'nearest cherry grove', 'nearest iron ore' (the game finds the "
                              "nearest of any structure, biome or block by name)",
                   'nearest_hint': 'For "the nearest village", pass place "village" (any structure, biome or '
                                   'block by name: the game finds the nearest).',
                   'character': "'Zombie'", 'characters': "'Zombie', 'Skeleton'", 'character_plural': 'zombies'}
    prompt_notes = PROMPT_NOTES
    web_sources = ('minecraft.wiki',)      # the agent's web search, limited to the official wiki
    # Names the speech recogniser should expect (Whisper's prompt).
    vocabulary = ('Minecraft, Navi, Creeper, Zombie, Skeleton, Spider, Enderman, Witch, Pillager, Piglin, Ghast, Blaze, '
                  'Wither, Ender Dragon, Villager, Iron Golem, Nether, the End, Netherite, Redstone, Elytra, '
                  'diamonds, crafting table, furnace, bed, village. Craft me some sticks, mine cobblestone, '
                  'a stone pickaxe, planks, logs, iron ore.')
    # Minecraft's default controls (the walk and the fighter press these): sprint is Ctrl, Space jumps; there is no
    # lock-on or roll; the right button uses the held item (a flask's job in Elden Ring: food, a potion).
    default_keys = {'forward': 'W', 'back': 'S', 'left': 'A', 'right': 'D', 'sprint': 'LCTRL', 'jump': 'SPACE',
                    'interact': 'RMB', 'attack': 'LMB', 'item': 'RMB', 'lock': '', 'roll': '',
                    'sneak': 'LSHIFT', 'inventory': 'E', 'escape': 'ESC', **{f'slot{i}': str(i) for i in range(1, 10)}}

    walk_breaks = False   # no blind swings when stuck: the route itself says what to break (and place)
    walk_sprint = False   # walk, don't run: a run carries the player farther past a ledge (falls) and costs food
    distance_unit = 'blocks'
    defend_on_walks = True    # a hostile one coming at the player on any walk is fought off first, then on
    fighter_style = 'melee'   # core/melee.py: a charged swing aimed with the camera (not Elden Ring's fighter)
    use_doer = True       # orders the rules don't get, and orders of several parts, are thought through
    doer_examples = ("'take me to the nearest village', 'take me to some iron ore', 'take me to the iron golem', "
                     "'get me 3 sticks', 'get me a stone pickaxe' (gathers and crafts all it takes, tools too), "
                     "'mine 10 cobblestone', 'chop 5 logs', 'craft a crafting table', 'kill the zombie', "
                     "'kill all the zombies', 'take me to the nearest zombie' (a creature anywhere loaded), "
                     "'explore 80 blocks north', 'explore' (on the way the camera faces), 'go to 12 40 -30' "
                     "(the game's coordinates, x y z, as where_is and survey give them: a fall into water there is "
                     "fine), 'take me to the surface' (out of a cave: walked out or dug up), 'run away' (from the "
                     "hostile ones near), 'eat something', 'go to that' (what the player looks at), 'stop'.")
    walk_precise = True   # every block of the route, walked block by block (core/autowalk.py precise)

    can_break = True      # the walk mines through blocks and builds (bridges, pillars) like Baritone

    def __init__(self, port: int = PORT):
        self.port = port
        self._local = threading.local()     # one connection per thread (a raycast waits for the next frame)
        self._cache: tuple[float, Snapshot, dict] | None = None
        self._cache_lock = threading.Lock()
        self._types: dict[int, dict] = {}   # type id -> what the bridge said about that kind
        self._names: dict | None = None      # the game's structure, structure tag and biome ids
        self._slow_breaks: dict = {}         # goal -> blocks on its latest route that are slow to break
        self._advised: set = set()           # goals a tool was weighed for already
        self._goals: dict = {}               # located goals (snapshot coordinates, rounded) -> ('block', cell)
                                             # or ('column', None): what counts as there (blocks.find kind)

    # ---- the connection ----

    def close(self) -> None:
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn[0].close()
            except OSError:
                pass
            self._local.conn = None

    def _ask(self, req: dict, timeout: float = REPLY_TIMEOUT) -> dict:
        conn = getattr(self._local, 'conn', None)
        for attempt in range(2):
            if conn is None:
                try:
                    s = socket.create_connection(('127.0.0.1', self.port), timeout=CONNECT_TIMEOUT)
                except OSError as e:
                    raise GameNotRunning(f'Minecraft bridge not reachable on port {self.port}: {e}') from e
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn = (s, s.makefile('rb'))
                self._local.conn = conn
            s, f = conn
            try:
                s.settimeout(timeout)
                s.sendall((json.dumps(req) + '\n').encode('utf-8'))
                line = f.readline()
                if not line:
                    raise OSError('the bridge closed the connection')
                return json.loads(line)
            except (OSError, ValueError) as e:
                try:
                    s.close()
                except OSError:
                    pass
                self._local.conn = conn = None
                if attempt == 1:
                    raise GameNotRunning(f'Minecraft bridge: {e}') from e
        raise GameNotRunning('Minecraft bridge: no answer')

    def is_live(self, wait: float = 0.25) -> bool:
        """Whether the game is running frames (the bridge counts them)."""
        a = self._ask({'op': 'ping'}).get('frame')
        time.sleep(wait)
        return self._ask({'op': 'ping'}).get('frame') != a

    # ---- state ----

    def raw(self) -> dict:
        """The bridge's latest snapshot as it sent it (Minecraft's own coordinates)."""
        return self._snapshot()[1]

    def snapshot(self) -> Snapshot:
        return self._snapshot()[0]

    def _snapshot(self) -> tuple[Snapshot, dict]:
        now = time.monotonic()
        with self._cache_lock:
            if self._cache is not None and now - self._cache[0] < SNAPSHOT_CACHE:
                return self._cache[1], self._cache[2]
        d = self._ask({'op': 'snapshot'})
        snap = self._convert(d)
        with self._cache_lock:
            self._cache = (now, snap, d)
        return snap, d

    def _convert(self, d: dict) -> Snapshot:
        frame = int(d.get('frame') or 0)
        w = d.get('window') or {}
        screen = Screen(int(w['x']), int(w['y']), int(w['w']), int(w['h']), bool(w.get('focused'))) \
            if w.get('w') and w.get('h') else None
        if not d.get('in_world') or not d.get('player') or not d.get('camera'):
            return Snapshot(self.game, frame, False, None, None, (), d.get('dimension'), screen=screen,
                            menu=True if w else None)
        p = d['player']
        c = d['camera']
        dead = bool(p.get('dead'))
        yaw = math.radians(float(p.get('yaw') or 0.0))
        facing = (math.sin(yaw), 0.0, math.cos(yaw))   # Minecraft (-sin, cos), mirrored
        player = Player(_mirror(p['pos']), facing, 'dead' if dead else 'alive', p.get('health'), p.get('max_health'))
        fwd = normalize(_mirror(c['fwd']))
        up = normalize(_mirror(c['up']))
        right = normalize(_mirror([-x for x in c['left']]))
        aspect = (screen.width / screen.height) if screen else 16 / 9
        camera = Camera(_mirror(c['pos']), fwd, right, up, float(c.get('fov') or 70.0), aspect)
        ents = []
        for e in d.get('entities') or ():
            key = str(e.get('type') or '')
            tid = _type_id(key)
            if tid not in self._types:
                self._types[tid] = {'id': key, 'name': e.get('name'), 'hostile': bool(e.get('hostile')),
                                    'max_health': e.get('max_hp')}
            pos = _mirror(e['pos'])
            h = float(e.get('h') or 1.0)
            look = e.get('look')
            ents.append(Entity(int(e['id']), tid, key, pos, (pos[0], pos[1] + h / 2, pos[2]),
                               float(e.get('w') or 0.6) / 2, h, bool(e.get('hostile')), bool(e.get('dead')),
                               e.get('hp'), e.get('max_hp'), name=e.get('name'),
                               facing=normalize(_mirror(look)) if look else None,
                               extra={k: e[k] for k in ('swell', 'aggressive', 'using_item', 'blocking', 'item', 'baby')
                                      if k in e}))
        t = d.get('target') or {}
        target_id = int(t['id']) if t.get('kind') == 'entity' and 'id' in t else None
        pl = d.get('player') or {}
        extra = {'aim_block': tuple(t['pos']) if t.get('kind') == 'block' else None, 'aim_face': t.get('face'),
                 'aim_id': t.get('block') if t.get('kind') == 'block' else None,
                 'aim_replaceable': t.get('replaceable'), 'aim_solid': t.get('solid'),
                 'tool_slot': t.get('tool_slot', -1), 'slot': pl.get('slot'), 'block_slot': pl.get('block_slot', -1),
                 'blocks': pl.get('blocks', 0), 'inventory': pl.get('inventory') or {},
                 'hotbar': pl.get('hotbar') or [], 'free_slots': pl.get('free_slots'),
                 'container': d.get('container'),
                 **{k: pl[k] for k in ('attack_strength', 'attack_delay', 'hurt_time', 'on_ground', 'vy', 'offhand',
                                       'blocking') if k in pl}}
        return Snapshot(self.game, frame, True, player, camera, tuple(ents), d.get('dimension'),
                        target_id=target_id, screen=screen, menu=bool(d.get('menu')) or bool(d.get('paused')),
                        extra=extra)

    def crosshair_facts(self) -> dict | None:
        """The block at the crosshair as the game names it, when the crosshair is on a block (the look-at
        tool adds it: the core's own look-at knows creatures and terrain, not what a block is)."""
        t = self.raw().get('target') or {}
        if t.get('kind') != 'block':
            return None
        out = {'block_at_crosshair': t.get('name'), 'block_id': t.get('block')}
        hit = t.get('hit')
        cam = (self.raw().get('camera') or {}).get('pos')
        if hit and cam:
            out['block_distance_blocks'] = round(math.dist(hit, cam), 1)
        return out

    # ---- rays ----

    def raycast(self, rays: list[tuple[Vec3, Vec3]], timeout: float = 2.0) -> list[RayHit]:
        if not rays:
            return []
        out: list[RayHit] = []
        for i in range(0, len(rays), 500):
            chunk = rays[i:i + 500]
            req = {'op': 'raycast', 'rays': [list(_mirror(a)) + list(_mirror(b)) for a, b in chunk]}
            got = self._ask(req, timeout=timeout)
            if 'error' in got and not got.get('hits'):
                raise GameNotRunning(f"Minecraft bridge: {got['error']}")
            for (a, b), h in zip(chunk, got.get('hits') or ()):
                if h and h[0]:
                    out.append(RayHit(a, b, True, _mirror(h[1:4]), _mirror(h[4:7])))
                else:
                    out.append(RayHit(a, b, False))
        return out

    # ---- knowledge ----

    def knowledge(self, type_id: int) -> dict | None:
        """What the game itself says about a kind of creature (its name, hostile, its full health)."""
        t = self._types.get(type_id)
        if t is None:
            return None
        out = {'name': t.get('name'), 'minecraft_id': t.get('id'), 'hostile': t.get('hostile')}
        if t.get('max_health'):
            out['max_health'] = t['max_health']
            out['max_hearts'] = round(t['max_health'] / 2, 1)
        return out

    # ---- places ----

    def where_am_i(self) -> dict:
        """The dimension, biome, coordinates (as the game shows them, F3), time of day and weather."""
        d = self.raw()
        if not d.get('in_world'):
            return {'error': 'no character in the world right now'}
        p, w = d['player']['pos'], d.get('world') or {}
        t = int(w.get('time_of_day') or 0)
        part = ('morning' if t < 6000 else 'afternoon' if t < 12000 else 'evening' if t < 13800 else
                'night' if t < 22300 else 'dawn')
        out = {'dimension': _pretty(d.get('dimension')), 'biome': _pretty(w.get('biome')),
               'coordinates': {'x': math.floor(p[0]), 'y': math.floor(p[1]), 'z': math.floor(p[2])},
               'day': w.get('day'), 'time': part, 'time_ticks': t,
               'weather': 'thunderstorm' if w.get('thundering') else 'rain' if w.get('raining') else 'clear',
               'light_level': w.get('light')}
        try:
            x, z = math.floor(p[0]), math.floor(p[2])
            out['standing'] = _standing(math.floor(p[1] + 0.01), self._ground_here(x, z))
        except Exception:
            pass
        return out

    def _place_names(self) -> dict:
        if self._names is None:
            got = self._ask({'op': 'names'}, timeout=6.0)
            if 'error' in got:
                raise GameNotRunning(f"Minecraft bridge: {got['error']}")
            self._names = got
        return self._names

    def _block_ids(self) -> list[str]:
        if getattr(self, '_blocks', None) is None:
            got = self._ask({'op': 'block_ids'}, timeout=6.0)
            self._blocks = got.get('ids', []) if 'error' not in got else []
        return self._blocks

    def _match_place(self, query: str) -> tuple[str, object] | None:
        """('structure', 'biome' or 'block', the game's id; for blocks a tuple of ids) for what the player calls
        a place or a kind of block, or None."""
        # (the game's own ids too: "iron_ore", "minecraft:iron_ore" was no block of that name, 2026-10-13)
        q = query.lower().replace('minecraft:', '').replace('_', ' ').replace('-', ' ')
        words = [w.rstrip('s') if len(w) > 3 else w for w in q.split() if w not in _FILLER_WORDS]
        if not words:
            return None
        phrase = ' '.join(words)
        for alias, ident in PLACE_ALIASES.items():
            if phrase == alias or phrase == alias.rstrip('s'):
                return 'structure', ident
        alias = BLOCK_ALIASES.get(phrase)
        if alias is not None:
            if isinstance(alias, str):                       # every block whose id ends so ('_log')
                return 'block', tuple(b for b in self._block_ids() if b.endswith(alias)
                                      and not b.split(':')[1].startswith('stripped'))
            return 'block', alias
        names = self._place_names()
        best = None
        for kind, ids, bonus in (('structure', names.get('structure_tags', []), 0.3),
                                 ('structure', names.get('structures', []), 0.2), ('biome', names.get('biomes', []), 0.0),
                                 ('block', self._block_ids(), 0.1), ('creature', self._entity_types(), 0.25)):
            for ident in ids:
                if not ident.startswith('minecraft:'):
                    continue
                id_words = ident.split(':', 1)[1].replace('/', '_').split('_')
                id_words = [w.rstrip('s') if len(w) > 3 else w for w in id_words]
                if not all(w in id_words for w in words):
                    continue
                score = len(words) / len(id_words) + bonus     # a whole match; the shorter id the better
                if best is None or score > best[0]:
                    best = (score, kind, ident)
        if best and best[1] == 'creature':
            return 'creature', (best[2],)
        if best and best[1] == 'block':
            ident = best[2]
            if ident.endswith('_ore') and not ident.startswith('minecraft:deepslate_'):
                return 'block', (ident, ident.replace('minecraft:', 'minecraft:deepslate_'))
            return 'block', (ident,)
        return (best[1], best[2]) if best else None

    def locate(self, query: str) -> dict:
        """The nearest place of that name (a village, a stronghold, a cherry grove...), as /locate finds it:
        name, kind, distance (blocks), compass, position. Structures are found at their middle, on their map
        position (height not known: the player's height is given)."""
        if _GROUND.search(query or ''):
            return self._locate_ground()
        if _SURFACE.search(query or ''):
            return self._locate_surface()
        m = self._match_place(query)
        if m is None:
            return {'error': f'no structure, biome or block called "{query}" in this game', 'results': []}
        kind, ident = m
        if kind == 'block':
            return self._locate_block(query, ident)
        if kind == 'creature':
            return self._locate_creature(query, ident)
        got = self._ask({'op': 'locate', 'kind': kind, 'id': ident}, timeout=25.0)
        if not got.get('found'):
            return {'error': got.get('error') or f'no {_pretty(ident)} found nearby', 'results': []}
        d = self.raw()
        me = (d.get('player') or {}).get('pos') or [0.0, 0.0, 0.0]
        at = list(got['pos'])
        if kind == 'structure':
            at[1] = me[1]
        dx, dz = at[0] - me[0], at[2] - me[2]
        res = {'name': _pretty(ident), 'kind': kind, 'distance_m': round(math.hypot(dx, dz)),
               'compass': _compass(dx, dz), 'coordinates': {'x': int(at[0]), 'z': int(at[2])},
               'position': _mirror(at)}
        # (a structure's height isn't known from the map: anywhere in height; its area once its chunk is loaded)
        self._goals[_goal_key(res['position'])] = ('column', {'pos': [int(v) for v in got['pos']], 'box': None}
                                                   if kind == 'structure' else None)
        box = self._structure_box(res['position'])
        if box is not None:
            res['area'] = _mirror_box(box)
            res['distance_m'] = round(_box_distance(box, me))
        if kind == 'biome':
            res['height_diff_m'] = round(at[1] - me[1])
        return {'query': query, 'results': [res]}

    def _entity_types(self) -> list[str]:
        if getattr(self, '_etypes', None) is None:
            got = self._ask({'op': 'entity_types'}, timeout=6.0)
            self._etypes = [i for i in got.get('ids', []) if i not in ('minecraft:item', 'minecraft:player')] \
                    if 'error' not in got else []
        return self._etypes

    def _locate_creature(self, query: str, types) -> dict:
        """The nearest creatures of these kinds anywhere in the loaded world (farther than the player sees)."""
        got = self._ask({'op': 'find_entities', 'types': list(types), 'max': 5}, timeout=6.0)
        found = got.get('found') or []
        if not found:
            return {'error': got.get('error') or f'no {query} anywhere in the loaded world', 'results': []}
        me = (self.raw().get('player') or {}).get('pos') or [0.0, 0.0, 0.0]
        out = []
        for x, y, z, ident, _eid in found:
            dx, dz = x - me[0], z - me[2]
            out.append({'name': _pretty(ident), 'kind': 'creature', 'distance_m': round(math.hypot(dx, dz)),
                        'compass': _compass(dx, dz), 'coordinates': {'x': int(x), 'y': int(y), 'z': int(z)},
                        'height_diff_m': round(y - me[1]), 'position': _mirror((x, y, z))})
        return {'query': query, 'results': out}

    def survey(self, radius: int = 12, depth: int = 40, trees_within: int = 24,
               land_within: int = LAND_RADIUS) -> dict:
        """What the ground does round the player, from the game's blocks (in the dark too): where it drops away in
        each of the 8 directions (how far out, how far down, what's at the bottom: water and how deep, ground,
        lava), and the pools of water below within radius. Coordinates as the game shows them (F3)."""
        d = self.raw()
        p = (d.get('player') or {}).get('pos')
        if not p:
            return {'error': 'no player'}
        x, y, z = math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2])
        got = self._ask({'op': 'grid', 'min': [x - radius, y - depth, z - radius],
                         'size': [2 * radius + 1, depth + 4, 2 * radius + 1]}, timeout=4.0)
        if 'error' in got:
            return {'error': got['error']}
        g = blocks.Grid.from_bridge(got)

        def bottom(cx, cz):
            """(blocks down from the player's feet, what's there, water depth) falling down column (cx, cz)."""
            for k in range(0, depth):
                c = g.at(cx, y - k, cz)
                if c == blocks.WATER:
                    w = 0
                    while g.at(cx, y - k - w, cz) == blocks.WATER and w < depth:
                        w += 1
                    return k, 'water', w
                if c == blocks.DANGER:
                    return k, 'lava or fire', 0
                if c == blocks.POWDER:
                    return k, 'powder snow (one sinks in it and freezes)', 0
                if c == blocks.UNKNOWN:
                    return k, 'not loaded', 0
                if c not in blocks.PASS:
                    return k - 1, 'ground', 0
            return depth, 'deeper than the scan', 0

        names = {(0, -1): 'north', (1, -1): 'north-east', (1, 0): 'east', (1, 1): 'south-east', (0, 1): 'south',
                 (-1, 1): 'south-west', (-1, 0): 'west', (-1, -1): 'north-west'}
        edges = []
        for (dx, dz), name in names.items():
            for k in range(1, radius + 1):
                cx, cz = x + dx * k, z + dz * k
                if g.at(cx, y, cz) not in blocks.PASS or g.at(cx, y + 1, cz) not in blocks.PASS:
                    edges.append({'direction': name, 'blocked_after': k - 1})
                    break
                down, what, wd = bottom(cx, cz)
                if down > blocks.MAX_DROP:
                    edge = {'direction': name, 'drop_after_blocks': k, 'drop_blocks': down, 'bottom': what,
                            'edge_coordinates': {'x': cx, 'y': y, 'z': cz}}
                    if what == 'water':
                        edge['water_depth'] = wd
                        edge['safe_to_drop_in'] = wd >= 1
                    edges.append(edge)
                    break
        # pools below: water columns lower than the player, grouped
        seen, pools = set(), []
        for cx in range(x - radius, x + radius + 1):
            for cz in range(z - radius, z + radius + 1):
                if (cx, cz) in seen:
                    continue
                down, what, wd = bottom(cx, cz)
                if what != 'water' or down <= blocks.MAX_DROP:
                    continue
                stack, cols = [(cx, cz)], []
                seen.add((cx, cz))
                while stack:
                    a, b = stack.pop()
                    cols.append((a, b, wd))
                    for na, nb in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
                        if (na, nb) in seen or abs(na - x) > radius or abs(nb - z) > radius:
                            continue
                        dd, ww, wdd = bottom(na, nb)
                        if ww == 'water' and abs(dd - down) <= 1:
                            seen.add((na, nb))
                            stack.append((na, nb))
                            wd = max(wd, wdd)
                mx = sum(c[0] for c in cols) / len(cols)
                mz = sum(c[1] for c in cols) / len(cols)
                cxs, czs = round(mx - 0.5), round(mz - 0.5)
                pools.append({'centre': {'x': cxs, 'y': y - down, 'z': czs}, 'blocks_across': len(cols),
                              'deepest': max(c[2] for c in cols), 'down_from_you': down,
                              'distance': round(math.hypot(mx - x, mz - z)), 'compass': _compass(mx - x, mz - z)})
        pools.sort(key=lambda q: q['distance'])
        return {'you_are_at': {'x': x, 'y': y, 'z': z}, 'edges_and_walls': edges, 'water_below': pools[:4],
                'trees': self._trees(x, y, z, trees_within),
                'land': self._land(x, y, z, land_within),
                'note': 'a fall into water does no harm; onto ground more than 3 blocks hurts'}

    def _heights(self, x0: int, z0: int, sx: int, sz: int, ground: bool = False):
        """(x, z) -> the height where the open sky starts, for a rectangle of columns (the bridge's heights).
        ground: the land's own height instead (the first air above it, under the trees and plants)."""
        got = self._ask({'op': 'heights', 'min': [x0, z0], 'size': [sx, sz], 'ground': ground},
                        timeout=6.0 if sx * sz > 4096 else 3.0)
        hs = got.get('heights') or []
        if 'error' in got or len(hs) < sx * sz or (ground and not got.get('ground')):
            return None

        def top(x, z):
            if not (x0 <= x < x0 + sx and z0 <= z < z0 + sz):
                return None
            return hs[(z - z0) * sx + (x - x0)]
        return top

    def _locate_surface(self) -> dict:
        """The surface: the top of the player's own column (the walk takes any way out to the open sky there
        is: up a cave, or dug straight up, whichever is quicker)."""
        me = (self.raw().get('player') or {}).get('pos')
        if not me:
            return {'error': 'no player', 'results': []}
        x, z = math.floor(me[0]), math.floor(me[2])
        top = self._heights(x, z, 1, 1)
        h = top(x, z) if top else None
        if h is None:
            return {'error': 'the surface height is not known here', 'results': []}
        if me[1] >= h - 0.01:
            return {'query': 'surface', 'results': [{'name': 'the surface', 'kind': 'surface', 'distance_m': 0,
                                                      'compass': 'here', 'height_diff_m': 0,
                                                      'coordinates': {'x': x, 'y': h, 'z': z},
                                                      'position': _mirror((x + 0.5, float(h), z + 0.5))}],
                    'note': 'already on the surface'}
        point = _mirror((x + 0.5, float(h), z + 0.5))
        self._goals[_goal_key(point)] = ('sky', None)
        return {'query': 'surface', 'results': [{'name': 'the surface', 'kind': 'surface', 'distance_m': 0,
                                                  'compass': 'up', 'height_diff_m': round(h - me[1]),
                                                  'coordinates': {'x': x, 'y': h, 'z': z}, 'position': point}]}

    def _ground_here(self, x: int, z: int):
        """The land's own height at the player's column (None: not known)."""
        g = self._heights(x, z, 1, 1, ground=True)
        return g(x, z) if g else None

    def _locate_ground(self) -> dict:
        """The ground: the land itself under the player (down out of a tree, off whatever was built; up out of
        a cave too): any column near at the land's height (the walk takes the quickest way down or up)."""
        me = (self.raw().get('player') or {}).get('pos')
        if not me:
            return {'error': 'no player', 'results': []}
        x, z = math.floor(me[0]), math.floor(me[2])
        h = self._ground_here(x, z)
        if h is None:
            return {'error': "the ground's height is not known here (an older bridge?)", 'results': []}
        feet = math.floor(me[1] + 0.01)
        row = {'name': 'the ground', 'kind': 'ground', 'distance_m': 0, 'height_diff_m': h - feet,
               'coordinates': {'x': x, 'y': h, 'z': z}}
        if h <= feet <= h + 1:
            return {'query': 'ground', 'results': [row | {'compass': 'here',
                                                         'position': _mirror((x + 0.5, float(feet), z + 0.5))}],
                    'note': 'already on the ground'}
        point = _mirror((x + 0.5, float(h), z + 0.5))
        self._goals[_goal_key(point)] = ('ground', None)
        return {'query': 'ground', 'results': [row | {'compass': 'down' if h < feet else 'up', 'position': point}]}

    def _land(self, x: int, y: int, z: int, radius: int) -> dict:
        """The lie of the land round the player (core/terrain.py): the hills near (their tops and a line along
        each top to walk), and the highest ground each way; the player's own height against the ground."""
        r = max(16, min(120, int(radius)))
        g = self._heights(x - r, z - r, 2 * r + 1, 2 * r + 1, ground=True)
        if g is None:
            return {'error': "the ground's heights are not known here (an older bridge?)"}
        m = terrain.HeightMap(x - r, z - r, 2 * r + 1, 2 * r + 1,
                              [g(xx, zz) for zz in range(z - r, z + r + 1) for xx in range(x - r, x + r + 1)])
        mine = terrain.ground_at(m, x, z)
        return {'within_blocks': r, 'your_ground_height': mine,
                'you_are': _standing(y, mine),
                'where_you_stand': terrain.here(m, (x, y, z)),
                'hills': terrain.hills(m, (x, y, z)),
                'highest_ground_each_way': terrain.each_way(m, (x, y, z)),
                'note': "the land's own shape (named biomes aside): the ground's height, under the trees"}

    def _trees(self, x: int, y: int, z: int, radius: int = 24) -> dict:
        """The trees within radius of the player: each trunk's foot, height, and where to stand on its top
        (coordinates for 'go to x y z'); the nearest few and the tallest few. (Searched out in rings, it saw only
        the trees within 8 blocks: "you're already on the tallest", 2026-10-13.)"""
        ids = [b for b in self._block_ids() if b.endswith('_log') and not b.split(':')[1].startswith('stripped')]
        got = self._ask({'op': 'find_blocks', 'ids': ids, 'max': 3000, 'radius': int(radius)},
                        timeout=6.0) if ids else {}
        cols: dict = {}
        for bx, by, bz, ident in got.get('found') or []:
            c = cols.setdefault((bx, bz), [by, by, ident])
            c[0], c[1] = min(c[0], by), max(c[1], by)
        trees = []
        for (bx, bz), (lo, hi, ident) in cols.items():
            trees.append({'kind': _pretty(ident), 'trunk_foot': {'x': bx, 'y': lo, 'z': bz}, 'height': hi - lo + 1,
                          'trunk_top_y': hi, 'stand_on_top_at': {'x': bx, 'y': hi + 1, 'z': bz},
                          'distance': round(math.hypot(bx + 0.5 - x, bz + 0.5 - z)),
                          'compass': _compass(bx + 0.5 - x, bz + 0.5 - z)})
        trees = [t for t in trees if t['distance'] <= radius]
        # the top of the tree is the top of its leaves over the trunk, not the trunk's top inside them (it went to
        # the trunk's top, 2 down in the canopy, and tunnelled to it through the leaves, 2026-10-13)
        for t in sorted(trees, key=lambda t: t['distance'])[:6] + sorted(trees, key=lambda t: -t['height'])[:6]:
            if t.get('_canopy'):
                continue
            t['_canopy'] = True
            bx, hi, bz = t['trunk_foot']['x'], t['trunk_top_y'], t['trunk_foot']['z']
            got = self._ask({'op': 'grid', 'min': [bx, hi + 1, bz], 'size': [1, 10, 1]}, timeout=2.0)
            if 'error' in got:
                continue
            g = blocks.Grid.from_bridge(got)
            top = hi
            for yy in range(hi + 1, hi + 11):
                if g.at(bx, yy, bz) not in blocks.PASS:
                    top = yy
            t['stand_on_top_at'] = {'x': bx, 'y': top + 1, 'z': bz}
            t['height'] = top - t['trunk_foot']['y'] + 1
        for t in trees:
            t.pop('_canopy', None)
            t.pop('trunk_top_y', None)
        tallest = sorted(trees, key=lambda t: (-t['height'], t['distance']))[:6]
        trees.sort(key=lambda t: t['distance'])
        return {'within_blocks': radius, 'count': len(trees), 'nearest': trees[:5], 'tallest': tallest}

    def coords_point(self, x: float, y: float, z: float):
        """A point to walk to from the game's own coordinates (F3, as where_is and survey give them)."""
        point = _mirror((x + 0.5, float(y), z + 0.5))
        self._goals[_goal_key(point)] = ('exact', (math.floor(x), math.floor(y), math.floor(z)))
        return point

    def aim_point(self, key, eye):
        """Where to aim at the block at key (the game's block coordinates) from the eye (snapshot coordinates)
        for the crosshair to land on it: (a point on it, None) - the nearest of points over the faces that look
        toward the eye (each face's middle and four round it) that a ray from the eye reaches first, within reach;
        or (None, the block in the way) when none is. (Aimed at its middle, a log under another seen from beside
        it, and the blocks to dig out of a pit, were hidden behind others: the crosshair on the wrong block, the
        walk stuck for a minute, 2026-10-13.)"""
        k = tuple(int(v) for v in key)
        e = _mirror(eye)                                         # (the game's coordinates from here)
        pts = []
        for axis in range(3):
            for side in (0.0, 1.0):
                face = k[axis] + side
                if (e[axis] - face) * (1.0 if side else -1.0) <= 0.0:
                    continue                                      # (a face turned away from the eye)
                a, b = [i for i in range(3) if i != axis]
                for u, v in ((0.5, 0.5), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)):
                    q = [0.0, 0.0, 0.0]
                    q[axis], q[a], q[b] = face, k[a] + u, k[b] + v
                    if math.dist(q, e) <= BLOCK_REACH:
                        pts.append(tuple(q))
        pts.sort(key=lambda q: math.dist(q, e))
        mid = (k[0] + 0.5, k[1] + 0.5, k[2] + 0.5)

        def past(q):                                              # (the ray ends a little inside the block)
            d = [q[i] - e[i] for i in range(3)]
            n = math.sqrt(sum(c * c for c in d)) or 1.0
            return tuple(q[i] + d[i] / n * 0.1 for i in range(3))
        hits = self._sight([(e, past(q)) for q in pts + [mid]])
        for q, h in zip(pts, hits):
            if h is not None and h['block'] == k:
                return _mirror(q), None
        h = hits[-1] if len(hits) == len(pts) + 1 else None
        if h is None:
            return None, None
        return None, (h['block'] if h['block'] != k else None)

    def _sight(self, rays) -> list:
        """Rays (the game's coordinates: start, end) as the crosshair sees, through the blocks' outlines (grass
        and flowers stop it, as they stop the crosshair: through their collision shapes, a spot was "in sight"
        through grass the crosshair stopped on, and the table never went down, 2026-10-13). Each: None (nothing),
        or {'at', 'normal', 'block' (its cell), 'id', 'replaceable' (a block put there takes its place: grass),
        'solid' (it has a collision shape)}. (An older bridge: through collision shapes.)"""
        if not rays:
            return []
        got = self._ask({'op': 'raycast', 'outline': True, 'rays': [list(a) + list(b) for a, b in rays]},
                        timeout=1.0)
        out = []
        for h in got.get('hits') or []:
            if not h or not h[0]:
                out.append(None)
                continue
            at, nrm = tuple(h[1:4]), tuple(h[4:7])
            if len(h) >= 13:
                cell, repl, solid = tuple(int(v) for v in h[8:11]), bool(h[11]), bool(h[12])
            else:
                cell, repl, solid = tuple(math.floor(at[i] - nrm[i] * 0.01) for i in range(3)), False, True
            out.append({'at': at, 'normal': nrm, 'block': cell, 'id': h[7] if len(h) > 7 else None,
                        'replaceable': repl, 'solid': solid})
        return out

    def goal_beside(self, point, key) -> None:
        """The walk to point (snapshot coordinates) ends right beside block key (the game's coordinates), its
        side open at the feet or the head: a closer goal than in reach and in sight, when that wasn't enough."""
        self._goals[_goal_key(point)] = ('block', tuple(int(v) for v in key))

    def block_solid(self, key) -> bool | None:
        """Whether the block at key (the game's block coordinates) is still something to break; None: unknown."""
        got = self._ask({'op': 'grid', 'min': list(key), 'size': [1, 1, 1]}, timeout=0.5)
        if 'error' in got:
            return None
        g = blocks.Grid.from_bridge(got)
        return g.at(*key) not in blocks.PASS

    def dig_estimate(self, goal: Vec3) -> dict | None:
        """How long digging straight to goal (snapshot coordinates) would take: blocks in the way, seconds with
        what's held, and with a wooden / stone / iron pickaxe, a shovel or an axe besides."""
        me = (self.raw().get('player') or {}).get('pos')
        if not me:
            return None
        got = self._ask({'op': 'dig_estimate', 'from': list(me), 'to': list(_mirror(goal))}, timeout=3.0)
        if 'error' in got or not got.get('blocks'):
            return None
        return {'blocks_in_the_way': got['blocks'], 'seconds_with_what_is_held': got['seconds_now'],
                'seconds_with': {k.split(':')[-1]: v for k, v in (got.get('seconds_with') or {}).items()},
                'mostly': [k.split(':')[-1] for k in (got.get('kinds') or {})][:3]}

    def tool_advice(self, goal: Vec3):
        """Whether making a tool first gets the player there sooner, digging (once per goal, after its route is
        planned): (tool, seconds saved in all, the crafting plan for it, what's mostly in the way), or None. The
        dig with what's held against the dig with each tool plus the time to make it; a tool already held, or
        one that can't be made from what's near, doesn't count. (Stone dug by hand: 7.5 s a block; a wooden
        pickaxe makes it 1, and takes about half a minute to make: the user, 2026-10-13: "it should ideally not
        try breaking cobblestone without a pickaxe, unless it's not easy to make a pickaxe".)"""
        from ...core.crafting import CantMake, plan
        key = _goal_key(goal)
        if key in self._advised or self._slow_breaks.get(key, 0) < SLOW_BREAKS_FOR_TOOL:
            return None
        self._advised.add(key)
        est = self.dig_estimate(goal)
        if not est:
            return None
        have = dict(((self.raw().get('player') or {}).get('inventory')) or {})
        best = None
        for tool, secs in (est.get('seconds_with') or {}).items():
            item = f'minecraft:{tool}'
            saved = est['seconds_with_what_is_held'] - secs
            if have.get(item) or saved < TOOL_WORTH_S:
                continue
            try:
                steps = plan(self.book(), item, 1, have)
            except CantMake:
                continue
            gain = saved - _steps_seconds(steps)
            if gain > TOOL_WORTH_S and (best is None or gain > best[1]):
                best = (item, round(gain), steps, est.get('mostly') or [])
        return best

    def note_failed_work(self, what: str, info: dict) -> None:
        """A block the walk couldn't place (or break): not planned there again for NO_PLACE_S."""
        if what == 'place' and info.get('into'):
            self._no_place = getattr(self, '_no_place', {})
            self._no_place[tuple(info['into'])] = time.monotonic()

    def explore_point(self, direction: str | None, distance: float | None, vector=None) -> tuple | None:
        """A point to walk to, exploring: that far (blocks; default 64) toward a compass direction (else the way
        the camera faces), at any height (a column goal)."""
        d = self.raw()
        me = (d.get('player') or {}).get('pos')
        if not me:
            return None
        if vector is not None:                       # (snapshot coordinates' x: mirrored to the game's)
            direction, vec0 = None, (-vector[0], vector[1])
        else:
            vec0 = None
        vec = vec0 or {'north': (0, -1), 'south': (0, 1), 'east': (1, 0), 'west': (-1, 0), 'northeast': (1, -1),
               'northwest': (-1, -1), 'southeast': (1, 1), 'southwest': (-1, 1)}.get((direction or '').replace(' ', '')
                                                                                   .replace('-', ''))
        if vec is None:
            f = (d.get('camera') or {}).get('fwd') or [0, 0, 1]
            vec = (f[0], f[2])
        n = math.hypot(*vec) or 1.0
        k = float(distance or 64) / n
        at = (me[0] + vec[0] * k, me[1], me[2] + vec[1] * k)
        point = _mirror(at)
        self._goals[_goal_key(point)] = ('column', None)
        return point

    def locate_blocks(self, ids, to_mine: bool = False) -> dict:
        """The nearest blocks of these ids (as locate() for a kind of block). to_mine: walked to within reach
        to mine (not to stand beside and look at)."""
        got = self._locate_block(_pretty(ids[0]) if ids else 'block', ids)
        if to_mine:
            for r in got.get('results', []):
                c = r['coordinates']
                self._goals[_goal_key(r['position'])] = ('mine', (c['x'], c['y'], c['z']))
        return got

    def _locate_block(self, query: str, ids) -> dict:
        """The nearest blocks of these kinds in the loaded world round the player (64 blocks out, 32 up and
        down): the nearest first, at the block (the walk goes up to it, mining its way if need be)."""
        got = self._ask({'op': 'find_blocks', 'ids': list(ids), 'max': 5}, timeout=4.0)
        found = got.get('found') or []
        if not found:
            return {'error': got.get('error') or f'no {query} within 64 blocks', 'results': []}
        me = (self.raw().get('player') or {}).get('pos') or [0.0, 0.0, 0.0]
        out = []
        for x, y, z, ident in found:
            dx, dz = x + 0.5 - me[0], z + 0.5 - me[2]
            out.append({'name': _pretty(ident), 'kind': 'block', 'distance_m': round(math.hypot(dx, dz)),
                        'compass': _compass(dx, dz), 'coordinates': {'x': x, 'y': y, 'z': z},
                        'height_diff_m': round(y - me[1]), 'position': _mirror((x + 0.5, float(y), z + 0.5))})
            self._goals[_goal_key(out[-1]['position'])] = ('block', (x, y, z))
        return {'query': query, 'results': out}

    def _structure_box(self, goal: Vec3):
        """The area of a located structure (game coordinates: (min, max), the max just outside), once the game
        has its chunk loaded; None till then."""
        kind, info = self._goals.get(_goal_key(goal), (None, None))
        if kind != 'column' or not info:
            return None
        if info['box'] is None:
            got = self._ask({'op': 'structure_box', 'pos': info['pos']}, timeout=4.0)
            if got.get('found'):
                info['box'] = (tuple(got['min']), tuple(got['max']))
        return info['box']

    def goal_shape(self, goal: Vec3) -> dict | None:
        """What counts as there for a located goal (core: arrival): {'area': (min, max)} (snapshot
        coordinates: anywhere inside across counts), {'column': True} (its height isn't known: across only),
        or None (the point itself)."""
        kind, info = self._goals.get(_goal_key(goal), (None, None))
        if kind != 'column':
            return None
        box = self._structure_box(goal) if info else None
        return {'area': _mirror_box(box)} if box else {'column': True}

    # ---- getting items: the game's recipes and drops (core/crafting.py, core/tasks.py) ----

    def book(self):
        """What core/crafting.py plans with: the game's own recipes and loot (read once), tools, what's near."""
        if getattr(self, '_book', None) is None:
            self._book = _Book(self)
        return self._book

    def match_item(self, words: str) -> list[str]:
        """The items a player means ('a stone pickaxe', 'sticks', 'cobble', 'wood'), best first."""
        b = self.book()
        q = [w.rstrip('s') if len(w) > 3 else w
             for w in words.lower().replace('minecraft:', '').replace('_', ' ').replace('-', ' ').split()
             if w not in _FILLER_WORDS and w not in ('me', 'some', 'get', 'make', 'craft')]
        if not q:
            return []
        phrase = ' '.join(q)
        if phrase in ITEM_ALIASES:
            return list(ITEM_ALIASES[phrase])
        known = b.items()
        scored = []
        for ident in known:
            words_ = [w.rstrip('s') if len(w) > 3 else w for w in ident.split(':')[-1].split('_')]
            if all(w in words_ for w in q):
                scored.append((len(q) / len(words_), ident))
        scored.sort(key=lambda x: -x[0])
        return [i for _s, i in scored[:6]]

    def items_near(self, items: tuple, radius: float) -> list:
        """Dropped items of these kinds lying within radius of the player, nearest first (snapshot coordinates)."""
        d = self.raw()
        me = (d.get('player') or {}).get('pos') or [0, 0, 0]
        out = []
        for e in d.get('entities') or []:
            if e.get('item') in items:
                p = e['pos']
                dist = math.dist(p, me)
                if dist <= radius:
                    at = _mirror(tuple(p))
                    # walked to: onto its own block (within reach of it isn't enough to pick it up)
                    self._goals[_goal_key(at)] = ('item', (math.floor(p[0]), math.floor(p[1] + 0.01),
                                                           math.floor(p[2])))
                    out.append((dist, at))
        return [p for _d, p in sorted(out)]

    def place_spot(self, exclude=()) -> tuple | None:
        """Where to put a block down near the player (a crafting table, a furnace): (aim point, the block clicked,
        its face, the new block), the keys in the game's block coordinates. Any open cell within reach that
        neither the player's body nor a creature is in, beside a solid block whose face toward it is in sight
        from the eye (a ray of the game's says so); on the ground near the feet first. (Only the 8 cells round the
        feet were tried: stood in a dip, every one was ground, and "I can't find a spot" twice on open plains,
        with animals about, 2026-10-13.) Seen as the crosshair sees, from the eye crouched (it's put down
        crouched, never using what's clicked): grass in the spot is put over (a block takes its place); a plant in
        the way, knocked out first. exclude: spots (new blocks' cells) tried already."""
        d = self.raw()
        pl = d.get('player') or {}
        p = pl.get('pos')
        eye = (d.get('camera') or {}).get('pos')
        if not p or not eye:
            return None
        eye = (eye[0], p[1] + CROUCH_EYE, eye[2])
        x, y, z = math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2])
        r = PLACE_RADIUS
        exclude = {tuple(c) for c in exclude}
        got = self._ask({'op': 'grid', 'min': [x - r, y - 2, z - r], 'size': [2 * r + 1, 6, 2 * r + 1]}, timeout=2.0)
        if 'error' in got:
            return None
        g = blocks.Grid.from_bridge(got)
        taken = _cells_in(p, 0.3, 1.8)                           # the player's body
        for e in d.get('entities') or []:
            if e.get('living') and not e.get('dead') and e.get('pos'):
                taken |= _cells_in(e['pos'], float(e.get('w') or 0.6) / 2, float(e.get('h') or 1.0))
        cands = []
        for cy in range(y - 1, y + 3):
            for cz in range(z - r, z + r + 1):
                for cx in range(x - r, x + r + 1):
                    c = (cx, cy, cz)
                    if g.at(*c) != blocks.AIR or c in taken or c in exclude:
                        continue
                    if math.dist(eye, (cx + 0.5, cy + 0.5, cz + 0.5)) > blocks.REACH - 0.5:
                        continue
                    across = math.hypot(cx + 0.5 - p[0], cz + 0.5 - p[2])
                    for dv in ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0)):
                        n = (cx + dv[0], cy + dv[1], cz + dv[2])
                        if g.at(*n) != blocks.SOLID:
                            continue
                        face = tuple(c[i] + 0.5 + dv[i] * 0.5 for i in range(3))   # (on n, toward c)
                        # on the ground (clicked on top), at the feet's height, a step or two off
                        score = abs(cy - y) * 2.0 + abs(across - 1.8) + (0.0 if dv == (0, -1, 0) else 1.5)
                        cands.append((score, c, n, face))
        cands.sort(key=lambda t: t[0])
        cands = cands[:PLACE_TRIES]
        if not cands:
            return None

        def past(q):
            dd = [q[i] - eye[i] for i in range(3)]
            nn = math.sqrt(sum(v * v for v in dd)) or 1.0
            return tuple(q[i] + dd[i] / nn * 0.05 for i in range(3))
        hits = self._sight([(eye, past(f)) for _s, _c, _n, f in cands])
        through_plant = None
        for (_s, c, n, f), h in zip(cands, hits):
            if h is None:
                continue
            spot = (_mirror(f), n, _FACES[tuple(c[i] - n[i] for i in range(3))], c)
            if h['block'] == n and math.dist(h['at'], f) < 0.2:
                return spot                                  # its face in sight
            if h['block'] == c and h['replaceable']:
                return spot                                  # grass in the spot: the block takes its place
            if through_plant is None and not h['solid']:
                through_plant = spot                         # a plant in the way: knocked out first
        return through_plant

    def terrain(self) -> dict:
        """What's around the player's feet for moving in a fight (core/melee.py): {'in_water': bool, 'avoid':
        {(dx, dz): 'water' | 'lava' | 'drop'}} for the 8 cells round the player (snapshot coordinates' x)."""
        d = self.raw()
        p = (d.get('player') or {}).get('pos')
        if not p:
            return {}
        x, y, z = math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2])
        got = self._ask({'op': 'grid', 'min': [x - 1, y - 5, z - 1], 'size': [3, 8, 3]}, timeout=1.0)
        if 'error' in got:
            return {}
        g = blocks.Grid.from_bridge(got)
        avoid = {}
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dz == 0:
                    continue
                cx, cz = x + dx, z + dz
                if g.at(cx, y, cz) not in blocks.PASS or g.at(cx, y + 1, cz) not in blocks.PASS:
                    # a wall (not a step up with room above): backing into it, a fighter catches up
                    if not (g.at(cx, y + 1, cz) in blocks.PASS and g.at(cx, y + 2, cz) in blocks.PASS):
                        avoid[(-dx, dz)] = 'wall'
                    continue
                kind = None
                for k in range(0, 5):
                    c = g.at(cx, y - k, cz)
                    if c == blocks.WATER:
                        kind = 'water'
                        break
                    if c == blocks.DANGER:
                        kind = 'lava'
                        break
                    if c not in blocks.PASS:
                        kind = 'drop' if k - 1 > blocks.MAX_DROP else None
                        break
                else:
                    kind = 'drop'
                if kind:
                    avoid[(-dx, dz)] = kind                       # (x mirrored for the snapshot)
        return {'in_water': g.at(x, y, z) == blocks.WATER or g.at(x, y + 1, z) == blocks.WATER, 'avoid': avoid}

    def combat_profile(self, e) -> dict:
        """What a kind of character does in a fight (core/melee.py): explodes, shoots, how far it hits."""
        kind = (e.model or '').split(':')[-1]
        return {'explodes': kind == 'creeper',
                'ranged': kind in RANGED,
                # can't be got away from on foot (a spider bit the player to death as it backed off, 2026-10-13)
                'fast': kind in FAST or bool((e.extra or {}).get('baby')),
                # how far past the widths it hits (Minecraft's melee reach: 0.83 for most; bigger for these)
                'reach': {'ravager': 1.6, 'warden': 1.6, 'iron_golem': 1.6, 'hoglin': 1.2, 'zoglin': 1.2}.get(kind,
                                                                                                           0.83)}

    def food_slot(self, hotbar: list) -> int | None:
        """The hotbar slot with the best food to eat now (cooked meat and bread before raw; rotten flesh, spider
        eyes and the like last), or None."""
        best = None
        for i, s in enumerate(hotbar or []):
            item = ((s[0] if s else '') or '').split(':')[-1]
            score = FOODS.get(item)
            if score is not None and (best is None or score > best[0]):
                best = (score, i)
        return best[1] if best else None

    def weapon_slot(self, hotbar: list) -> int | None:
        """The hotbar slot with the best weapon (a sword, else an axe, else any tool, else an empty hand), or
        None to keep what's held."""
        best = None
        for i, s in enumerate(hotbar or []):
            item = (s[0] if s else '') or ''
            kind = item.split(':')[-1]
            score = None
            for k, base in (('_sword', 100), ('_axe', 90), ('_pickaxe', 40), ('_shovel', 30), ('_hoe', 20)):
                if kind.endswith(k):
                    tier = kind[:-len(k)]
                    score = base + {'wooden': 0, 'golden': 0, 'stone': 2, 'copper': 3, 'iron': 4, 'diamond': 6,
                                    'netherite': 7}.get(tier, 1)
            if not item:
                score = 10
            if score is not None and (best is None or score > best[0]):
                best = (score, i)
        return best[1] if best else None

    def weapon_damage(self, hotbar: list) -> float:
        """The damage of a hit with the best weapon in the hotbar (weapon_slot's): a fist 1."""
        i = self.weapon_slot(hotbar)
        item = ((hotbar[i][0] if i is not None and i < len(hotbar) and hotbar[i] else '') or '').split(':')[-1]
        for k, base in (('_sword', 4), ('_axe', 7), ('_pickaxe', 2), ('_shovel', 2.5), ('_hoe', 1)):
            if item.endswith(k):
                tier = {'wooden': 0, 'golden': 0, 'stone': 1, 'copper': 1, 'iron': 2, 'diamond': 3,
                        'netherite': 4}.get(item[:-len(k)], 0)
                return base + (tier if k != '_hoe' else 0)
        return 1.0

    def bridge_note(self) -> str | None:
        """A line for the player when the game runs an older bridge than this adapter needs (a new one is only
        loaded when Minecraft starts), else None."""
        got = self._ask({'op': 'ping'}, timeout=2.0)
        if 'error' in got or int(got.get('version') or 0) >= BRIDGE_VERSION:
            return None
        return 'Minecraft is running an older version of my bridge mod. Restart Minecraft to load the new one!'

    def nearest_places(self, kind: str = 'village', limit: int = 5) -> dict:
        return self.locate(kind)

    # ---- walking: A* over the blocks (blocks.py) ----

    def route(self, start: Vec3, goal: Vec3, budget_s: float = 1.5):
        """A walkable route from start toward goal (snapshot coordinates), or None. Farther than ROUTE_REACH:
        the first leg (timed_out: the caller plans on from its end as the player walks). Steps up are
        jumps (the walk's 'jump' actions)."""
        from ...core.nav import Route
        s, g = _mirror(start), _mirror(goal)
        kind, block = self._goals.get(_goal_key(goal), ('near', None))
        to_mine = kind in ('mine', 'exact')                # (the goal's cell given: no 'look' at the end)
        box = None
        sky_goal = kind == 'sky'
        ground_goal = kind == 'ground'
        if sky_goal:
            kind, block = 'near', None
        if kind == 'column':
            info, block = block, None
            if info and math.hypot(info['pos'][0] - s[0], info['pos'][2] - s[2]) < BOX_ASK:
                box = self._structure_box(goal)
        far = math.hypot(g[0] - s[0], g[2] - s[2])
        aim = g
        if far > ROUTE_REACH:
            kind, block = 'column', None             # a leg toward it: that far on, at whatever height
            k = ROUTE_REACH / far
            aim = (s[0] + (g[0] - s[0]) * k, s[1] + (g[1] - s[1]) * k, s[2] + (g[2] - s[2]) * k)
        lo = [math.floor(min(s[i], aim[i])) - (ROUTE_MARGIN if i != 1 else ROUTE_DEPTH) for i in range(3)]
        hi = [math.floor(max(s[i], aim[i])) + (ROUTE_MARGIN if i != 1 else ROUTE_DEPTH) for i in range(3)]
        sky = None
        if sky_goal:                                     # (round the player, up to the highest top near)
            for i in (0, 2):
                lo[i], hi[i] = math.floor(s[i]) - SKY_RADIUS, math.floor(s[i]) + SKY_RADIUS
            sky = self._heights(lo[0], lo[2], hi[0] - lo[0] + 1, hi[2] - lo[2] + 1)
            if sky is not None:
                tops = [sky(xx, zz) for xx in range(lo[0], hi[0] + 1) for zz in range(lo[2], hi[2] + 1)]
                hi[1] = max(hi[1], max(t for t in tops if t is not None) + 2)
        ground = None
        if ground_goal:                                  # (round the player, down to the lowest land near)
            for i in (0, 2):
                lo[i], hi[i] = math.floor(s[i]) - GROUND_RADIUS, math.floor(s[i]) + GROUND_RADIUS
            ground = self._heights(lo[0], lo[2], hi[0] - lo[0] + 1, hi[2] - lo[2] + 1, ground=True)
            if ground is None:
                return None
            hs = [ground(xx, zz) for xx in range(lo[0], hi[0] + 1) for zz in range(lo[2], hi[2] + 1)]
            hs = [h for h in hs if h is not None]
            lo[1] = min(lo[1], min(hs) - 2)
            hi[1] = max(hi[1], max(hs) + 3)
        if not sky_goal and not ground_goal:
            # down to the lowest ground (or water) and up to the highest between here and there: the way may be
            # down a cliff into the sea and up the other side (12 either way didn't see the sea 14 down, and
            # found no way to a spider across it, 2026-10-13)
            tops = self._heights(lo[0], lo[2], hi[0] - lo[0] + 1, hi[2] - lo[2] + 1)
            if tops is not None:
                hs = [tops(xx, zz) for xx in range(lo[0], hi[0] + 1, 2) for zz in range(lo[2], hi[2] + 1, 2)]
                hs = [h for h in hs if h is not None]
                if hs:
                    lo[1] = min(lo[1], min(hs) - 3)
                    hi[1] = max(hi[1], max(hs) + 3)
        size = [hi[i] - lo[i] + 1 for i in range(3)]
        while size[0] * size[1] * size[2] > 160 * 1024 and size[1] > 16:
            size[1] -= 2
            lo[1] += 1
        got = self._ask({'op': 'grid', 'min': lo, 'size': size}, timeout=4.0)
        if 'error' in got:
            return None
        grid = blocks.Grid.from_bridge(got)
        cell = lambda p: (math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2]))
        pl = self.raw().get('player') or {}
        have = int(pl.get('blocks') or 0) if int(pl.get('block_slot', -1)) >= 0 else 0
        now = time.monotonic()
        self._no_place = {c: t for c, t in getattr(self, '_no_place', {}).items() if now - t < NO_PLACE_S}
        path = blocks.find(grid, cell(s), block if block else cell(aim), budget_s=budget_s,
                           can_break=self.can_break, blocks=have, kind=kind, box=box,
                           no_place=frozenset(self._no_place), sky=sky, ground=ground)
        self.route_note = {'goal_kind': 'sky' if sky_goal else kind, 'box': [lo, size],
                           'found': None if path is None else ('there' if path.reaches_goal else
                                                               'part' if path.timed_out else 'no way'),
                           'expanded': path.expanded if path else 0, 'cells': len(path.cells) if path else 0,
                           'blocks_to_build_with': have}
        # a plan that places more blocks than are held (the planner doesn't count them as it places them:
        # 2 held, a pillar of 8 planned, and it ran out half way up a tree, 2026-10-13): not followed; the
        # blocks it takes are said, so the order can get them first
        placing = sum(1 for a in path.actions if a[0] == 'place') if path is not None else 0
        if path is not None and placing > have:
            self.route_note['found'] = 'needs blocks'
            self.route_note['blocks_it_would_take'] = placing
            return None
        if (path is None or not path.reaches_goal) and have < 8 and not sky_goal:
            # with blocks to build with, would there be a way? (then the order can get some first: dirt)
            try:
                alt = blocks.find(grid, cell(s), block if block else cell(aim), budget_s=1.5,
                                  can_break=self.can_break, blocks=64, kind=kind, box=box,
                                  no_place=frozenset(self._no_place), ground=ground)
            except Exception:
                alt = None
            if alt is not None and alt.reaches_goal:
                need = sum(1 for a in alt.actions if a[0] == 'place')
                if need > have:
                    self.route_note['blocks_it_would_take'] = need
        if path is None:
            return None
        inside = (box is not None and blocks._inside(cell(path.cells[-1]), box)) or \
            (sky is not None and blocks._open_sky(cell(path.cells[-1]), sky)) or \
            (ground is not None and blocks._on_ground(grid, cell(path.cells[-1]), ground))
        complete = inside or (path.reaches_goal and far <= ROUTE_REACH)
        if len(path.cells) < 2:
            if not complete:
                return None
            path.cells = path.cells * 2                  # there already
        route = Route([_mirror(p) for p in path.cells], complete, 0)
        route.length_m = path.length
        route.timed_out = not complete and (path.timed_out or path.reaches_goal)
        route.actions = [_action(a) for a in path.actions]
        self._slow_breaks[_goal_key(goal)] = sum(1 for a in path.actions if a[0] == 'break'
                                                 and (grid.hardness(*a[2]) & 127) >= SLOW_BREAK)
        if block and kind == 'block' and path.reaches_goal and not to_mine:   # end facing it: came to see it
            route.actions.append(('look', _mirror(path.cells[-1]),
                                  _mirror((block[0] + 0.5, block[1] + 0.5, block[2] + 0.5)), {}))
        return route


# What players call items (else matched by their words against the game's own item names).
ITEM_ALIASES = {
    'wood': ('minecraft:oak_log', 'minecraft:birch_log', 'minecraft:spruce_log', 'minecraft:jungle_log',
             'minecraft:acacia_log', 'minecraft:dark_oak_log', 'minecraft:cherry_log', 'minecraft:mangrove_log'),
    'log': ('minecraft:oak_log', 'minecraft:birch_log', 'minecraft:spruce_log', 'minecraft:jungle_log',
            'minecraft:acacia_log', 'minecraft:dark_oak_log', 'minecraft:cherry_log', 'minecraft:mangrove_log'),
    'tree': ('minecraft:oak_log', 'minecraft:birch_log', 'minecraft:spruce_log', 'minecraft:jungle_log',
             'minecraft:acacia_log', 'minecraft:dark_oak_log', 'minecraft:cherry_log', 'minecraft:mangrove_log'),
    'plank': ('minecraft:oak_planks', 'minecraft:birch_planks', 'minecraft:spruce_planks', 'minecraft:jungle_planks',
              'minecraft:acacia_planks', 'minecraft:dark_oak_planks', 'minecraft:cherry_planks'),
    'cobble': ('minecraft:cobblestone',), 'stone': ('minecraft:cobblestone',), 'rock': ('minecraft:cobblestone',),
    'iron': ('minecraft:raw_iron',), 'gold': ('minecraft:raw_gold',), 'copper': ('minecraft:raw_copper',),
    'pickaxe': ('minecraft:wooden_pickaxe',), 'pick': ('minecraft:wooden_pickaxe',),
    'axe': ('minecraft:wooden_axe',), 'shovel': ('minecraft:wooden_shovel',), 'sword': ('minecraft:wooden_sword',),
    'table': ('minecraft:crafting_table',), 'workbench': ('minecraft:crafting_table',),
}
PICK_TIERS = {'pickaxe': ['wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite'],
              'axe': ['wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite'],
              'shovel': ['wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite']}


class _Book:
    """core/crafting.py's Book from the game: its crafting recipes and its blocks' drops (asked once)."""
    hand_grid = 2
    station = 'minecraft:crafting_table'

    def __init__(self, adapter):
        from ...core.crafting import Recipe, Source
        self.a = adapter
        got = adapter._ask({'op': 'recipes'}, timeout=15.0)
        self._recipes: dict = {}
        for r in got.get('recipes') or []:
            cells = tuple(tuple(c) for c in r['cells'])
            self._recipes.setdefault(r['result'], []).append(
                Recipe(r['id'], r['result'], int(r['count']), cells, int(r['w']), int(r['h']), bool(r['shapeless']),
                       r.get('station'), int(r.get('time') or 0)))
        # what burns in a furnace, and how long (ticks): the game's own reckoning (an older bridge: none)
        self._fuels = {k: int(v) for k, v in (got.get('fuels') or {}).items() if isinstance(v, (int, float))}
        got = adapter._ask({'op': 'sources'}, timeout=15.0)
        self._sources: dict = {}
        for block, info in (got.get('sources') or {}).items():
            if block.endswith('_leaves'):
                continue                                # (sticks and saplings only now and then)
            for item in set(info['drops']):
                self._sources.setdefault(item, []).append(Source(block, tuple(info['drops']), info['tool'] or None))
        self._near: dict = {}

    def items(self) -> set:
        return set(self._recipes) | set(self._sources)

    def recipes(self, item):
        return self._recipes.get(item, [])

    def sources(self, item):
        # a block that drops the item itself first ('stone' drops cobblestone; 'cobblestone' itself is rare)
        return self._sources.get(item, [])

    def tool_ok(self, need, have):
        if not need:
            return True
        kind = need.split('_')[-1]
        tiers = PICK_TIERS.get(kind)
        if tiers is None:
            return have.get(need, 0) > 0
        tier = need.split(':')[-1].split('_')[0]
        ok = tiers[tiers.index(tier):] if tier in tiers else [tier]
        if tier in ('wooden', 'golden'):
            ok = tiers                                  # (wood and gold mine the same)
        return any(have.get(f'minecraft:{t}_{kind}', 0) > 0 for t in ok)

    def nearby(self, blocks_):
        key = tuple(blocks_)
        hit = self._near.get(key)
        if hit is not None and time.monotonic() - hit[0] < 20.0:
            return hit[1]
        got = self.a._ask({'op': 'find_blocks', 'ids': list(blocks_), 'max': 1}, timeout=4.0)
        found = got.get('found') or []
        me = (self.a.raw().get('player') or {}).get('pos') or [0, 0, 0]
        d = math.dist(me, found[0][:3]) if found else None
        self._near[key] = (time.monotonic(), d)
        return d

    def fuels(self) -> dict:
        return self._fuels

    def station_near(self, item=None):
        d = self.nearby((item or self.station,))
        return d is not None and d < 32.0


def _steps_seconds(steps: list) -> float:
    """About how long a crafting plan takes: a gathered item ~5 s and a walk to each kind, a craft ~4 s."""
    total = 0.0
    for s in steps:
        if s[0] == 'gather':
            total += 15.0 + 5.0 * s[2]
        else:
            total += 4.0
    return total


def _mirror_box(box):
    (x0, y0, z0), (x1, y1, z1) = box
    return (-x1, y0, z0), (-x0, y1, z1)


def _box_distance(box, p) -> float:
    """Across from p to the nearest edge of a box (0 inside)."""
    (x0, _y0, z0), (x1, _y1, z1) = box
    return math.hypot(max(x0 - p[0], 0.0, p[0] - x1), max(z0 - p[2], 0.0, p[2] - z1))


# the land itself, under the trees ("take me to the ground" from a tree top was "already on the surface",
# 2026-10-13)
_GROUND = re.compile(r"\b((?:the|solid) ground|ground level|down (?:from|out of) (?:this|the) trees?|"
                     r"(?:get|take) (?:me )?down(?: from here)?$|off (?:this|the) trees?)\b", re.I)
_SURFACE = re.compile(r"\b(surface|outside|open air|the sky|daylight|out of (?:this|the) (?:cave|hole|mine|tunnel))\b",
                      re.I)


def _cells_in(pos, half: float, height: float) -> set:
    """The block cells a body (feet at pos, half its width, its height) is in."""
    out = set()
    for cx in range(math.floor(pos[0] - half), math.floor(pos[0] + half) + 1):
        for cz in range(math.floor(pos[2] - half), math.floor(pos[2] + half) + 1):
            for cy in range(math.floor(pos[1]), math.floor(pos[1] + height - 0.01) + 1):
                out.add((cx, cy, cz))
    return out


def _standing(feet: int, ground) -> str | None:
    """Where the player stands against the land's own height there."""
    if ground is None:
        return None
    if ground <= feet <= ground + 1:
        return 'on the ground'
    if feet > ground:
        return f'{feet - ground} blocks above the ground (up a tree or on something built)'
    return f'{ground - feet} blocks below the ground (underground)'


def _goal_key(p) -> tuple:
    return (round(p[0], 1), round(p[1], 1), round(p[2], 1))


_FACES = {(1, 0, 0): 'east', (-1, 0, 0): 'west', (0, 1, 0): 'up', (0, -1, 0): 'down', (0, 0, 1): 'south',
          (0, 0, -1): 'north'}


def _action(a: tuple) -> tuple:
    """A route action from blocks.Path.actions in the walk's terms (snapshot coordinates):
    ('step', from, to): a jump up one block (the walk taps jump; Navi doesn't call each one out).
    ('break', from, the block's middle, {'key': the block}): stand at from, aim at it, mine it.
    ('place', from, the face to click, {'key': the block clicked, 'face', 'into': the new block, 'kind':
    'bridge' or 'pillar', 'gap': the new block's top}): build there."""
    what, frm = a[0], _mirror(a[1])
    if what == 'step':
        return ('step', frm, _mirror(a[2]))
    if what == 'parkour':                            # a jump over a gap: to the landing block's middle
        (lx, ly, lz), gap, sprint, runup = a[2], a[3], a[4], a[5]
        info = {'gap': gap, 'sprint': sprint}
        if runup is not None:
            info['runup'] = _mirror((runup[0] + 0.5, float(runup[1]), runup[2] + 0.5))
        return ('parkour', frm, _mirror((lx + 0.5, float(ly), lz + 0.5)), info)
    if what == 'break':
        c = a[2]
        return ('break', frm, _mirror((c[0] + 0.5, c[1] + 0.5, c[2] + 0.5)), {'key': tuple(c)})
    into, against, kind = a[2], a[3], a[4]
    d = (into[0] - against[0], into[1] - against[1], into[2] - against[2])
    face = (against[0] + 0.5 + d[0] * 0.5, against[1] + 0.5 + d[1] * 0.5, against[2] + 0.5 + d[2] * 0.5)
    return ('place', frm, _mirror(face), {'key': tuple(against), 'face': _FACES[d], 'into': tuple(into),
                                          'kind': kind, 'gap': _mirror((into[0] + 0.5, into[1] + 1.0, into[2] + 0.5))})


def _pretty(ident: str | None) -> str | None:
    """'minecraft:cherry_grove' -> 'Cherry Grove'."""
    if not ident:
        return ident
    return ident.split(':', 1)[-1].replace('/', ' ').replace('_', ' ').title()


def _compass(dx: float, dz: float) -> str:
    """Minecraft's compass (north is -Z, east +X)."""
    a = (math.degrees(math.atan2(dx, -dz)) + 360) % 360
    return ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'][round(a / 45) % 8]
