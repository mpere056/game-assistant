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
import socket
import threading
import time
import zlib

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_NAVMESH, CAP_PLACES, CAP_PLAYER,
                               CAP_RAYCAST, CAP_SCREEN, CAP_TARGET, Camera, Entity, GameNotRunning, Player, RayHit,
                               Screen, Snapshot, Vec3, normalize)
from . import blocks

PORT = 47821
SNAPSHOT_CACHE = 0.010     # seconds a snapshot is reused (several threads ask every frame)
CONNECT_TIMEOUT = 0.5
REPLY_TIMEOUT = 2.0
ROUTE_REACH = 48          # blocks: one plan covers at most this far (sideways); a farther goal is planned in legs
ROUTE_MARGIN = 10         # blocks round the start and the goal in the box read for planning
ROUTE_DEPTH = 12          # blocks above and below
RANGED = {'skeleton', 'stray', 'bogged', 'pillager', 'blaze', 'ghast', 'witch', 'drowned', 'shulker', 'breeze',
          'piglin', 'llama', 'snow_golem'}
BRIDGE_VERSION = 10        # the bridge (bridges/minecraft Bridge.VERSION) this adapter needs
SLOW_BREAK = 15           # a block slower than this to break (the bridge's hardness byte: 4 ticks a unit, 3 s)...
SLOW_BREAKS_FOR_TOOL = 3  # ...this many on a route: weigh making a tool first
TOOL_WORTH_S = 20.0       # seconds a tool must save, all told, to be made first
NO_PLACE_S = 120.0        # seconds a cell where placing failed isn't planned for again
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
- Distances are in blocks (one block is about a metre); say "blocks"."""


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
    fighter_style = 'melee'   # core/melee.py: a charged swing aimed with the camera (not Elden Ring's fighter)
    use_doer = True       # orders the rules don't get, and orders of several parts, are thought through
    doer_examples = ("'take me to the nearest village', 'take me to some iron ore', 'take me to the iron golem', "
                     "'get me 3 sticks', 'get me a stone pickaxe' (gathers and crafts all it takes, tools too), "
                     "'mine 10 cobblestone', 'chop 5 logs', 'craft a crafting table', 'kill the zombie', "
                     "'kill all the zombies', 'take me to the nearest zombie' (a creature anywhere loaded), "
                     "'explore 80 blocks north', 'explore' (on the way the camera faces), 'go to that' (what the "
                     "player looks at), 'stop'.")
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
                               extra={k: e[k] for k in ('swell', 'aggressive', 'using_item', 'blocking', 'item')
                                      if k in e}))
        t = d.get('target') or {}
        target_id = int(t['id']) if t.get('kind') == 'entity' and 'id' in t else None
        pl = d.get('player') or {}
        extra = {'aim_block': tuple(t['pos']) if t.get('kind') == 'block' else None, 'aim_face': t.get('face'),
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
        words = [w.rstrip('s') if len(w) > 3 else w for w in query.lower().replace('-', ' ').split()
                 if w not in _FILLER_WORDS]
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
            self._etypes = [i for i in got.get('ids', []) if i not in ('minecraft:item', 'minecraft:player')]                 if 'error' not in got else []
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

    def explore_point(self, direction: str | None, distance: float | None) -> tuple | None:
        """A point to walk to, exploring: that far (blocks; default 64) toward a compass direction (else the way
        the camera faces), at any height (a column goal)."""
        d = self.raw()
        me = (d.get('player') or {}).get('pos')
        if not me:
            return None
        vec = {'north': (0, -1), 'south': (0, 1), 'east': (1, 0), 'west': (-1, 0), 'northeast': (1, -1),
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

    def locate_blocks(self, ids) -> dict:
        """The nearest blocks of these ids (as locate() for a kind of block)."""
        return self._locate_block(_pretty(ids[0]) if ids else 'block', ids)

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
        q = [w.rstrip('s') if len(w) > 3 else w for w in words.lower().replace('-', ' ').split()
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

    def place_spot(self) -> tuple | None:
        """Where to put a block beside the player: (aim point, block clicked, its face, the new block), in the
        game's block coordinates for the keys; the cell in front first."""
        d = self.raw()
        p = (d.get('player') or {}).get('pos')
        cam = (d.get('camera') or {}).get('fwd') or [0, 0, 1]
        if not p:
            return None
        x, y, z = math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2])
        got = self._ask({'op': 'grid', 'min': [x - 2, y - 1, z - 2], 'size': [5, 3, 5]}, timeout=2.0)
        if 'error' in got:
            return None
        g = blocks.Grid.from_bridge(got)
        dirs = sorted(((1, 0), (-1, 0), (0, 1), (0, -1)), key=lambda v: -(v[0] * cam[0] + v[1] * cam[2]))
        for dx, dz in dirs + [(1, 1), (-1, 1), (1, -1), (-1, -1)]:
            cx, cz = x + dx, z + dz
            if g.at(cx, y, cz) == blocks.AIR and g.at(cx, y + 1, cz) in blocks.PASS and g.at(cx, y - 1, cz) == blocks.SOLID:
                return (_mirror((cx + 0.5, float(y), cz + 0.5)), (cx, y - 1, cz), 'up', (cx, y, cz))
        return None

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
                # how far past the widths it hits (Minecraft's melee reach: 0.83 for most; bigger for these)
                'reach': {'ravager': 1.6, 'warden': 1.6, 'iron_golem': 1.6, 'hoglin': 1.2, 'zoglin': 1.2}.get(kind,
                                                                                                           0.83)}

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
        box = None
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
                           no_place=frozenset(self._no_place))
        if path is None:
            return None
        inside = box is not None and blocks._inside(cell(path.cells[-1]), box)
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
        if block and kind == 'block' and path.reaches_goal:   # end facing the block: the player came to see it
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
                Recipe(r['id'], r['result'], int(r['count']), cells, int(r['w']), int(r['h']), bool(r['shapeless'])))
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

    def station_near(self):
        d = self.nearby((self.station,))
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
