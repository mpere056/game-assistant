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
from . import blocks, pathing

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
BUILD_FIRST = {'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:cobblestone', 'minecraft:cobbled_deepslate',
               'minecraft:stone', 'minecraft:netherrack', 'minecraft:andesite', 'minecraft:diorite',
               'minecraft:granite', 'minecraft:deepslate', 'minecraft:tuff', 'minecraft:blackstone',
               'minecraft:end_stone', 'minecraft:mud_bricks', 'minecraft:mossy_cobblestone'}   # (what one builds with)
SURFACE_RING = 2          # blocks round a column the land's level is taken from (the surface)
HOLE_RING = 3             # blocks out the land round the player is looked at (in a hole?)
SPOTS = 6                 # spots of a block asked for, reckoning what getting one costs...
SPOTS_FOR = 8.0           # ...asked again after this many seconds
FOE_AVOID = 8.0           # blocks round a hostile creature where a move costs more (Baritone's avoidance)...
FOE_COST = 1.5            # ...this many times as much
NEUTRAL_KINDS = ('enderman', 'zombified_piglin', 'piglin', 'spider', 'cave_spider')   # hostile only when provoked
FOE_NEAR_R = 6.0          # blocks: a hostile creature this near a block to gather...
FOE_NEAR_S = 30.0         # ...makes it this many seconds dearer (the crafting
                          # plan's reckoning of where to get things)
QUICK_MANY = 16           # blocks found weighed by one search toward all of them (which one's way is quickest)...
QUICK_MARGIN = 6          # ...over the box round them and the player, this much more each way...
QUICK_S = 1.0             # ...in this long at most
FOE_GOAL = 3.0            # (one this near the goal is what's walked to)
COVER_RADIUS = 6          # blocks round the player a spot out of a shooter's sight is looked for in...
COVER_TRIES = 40          # ...the nearest this many...
COVER_SEEN_AT = (1.5, 0.7)   # ...out of sight at the head and the body (blocks over the feet)
FIGHT_SPACE = 4           # blocks round the player read for where to move in a fight
ROOM_STEPS = 3            # steps out from a spot its room is counted over
HUNT_S = 8.0              # seconds a creature hunted takes (up to it, the fight, the drops picked up)
SWORD_DAMAGE = 4.0        # a hit this hard (a wooden sword's) or harder: armed (fight_gear)
ARMOR_PARTS = ('helmet', 'chestplate', 'leggings', 'boots')
ARMOR_TIERS = ('netherite', 'diamond', 'iron', 'turtle', 'chainmail', 'copper', 'golden', 'leather')   # best first
HUNT_RANGE = 96.0         # blocks: creatures this near (across; up or down counts three times) are hunted
MORE_CREATURES_S = 60.0   # seconds to find each one more of a kind than are about
FAR_CREATURES = 16        # creatures of a kind asked of the game's search, when too few are about...
FAR_CREATURES_FOR = 2.0   # ...asked again after this many seconds
FAR_KIND_S = 20.0         # seconds more each farther one counts when the kind of it matters (a white sheep):
                          # not known from there
# never hunted for what they drop: they may be someone's (a cat, a dog, a horse) or the village's
NO_HUNT = {'minecraft:' + n for n in ('cat', 'wolf', 'parrot', 'horse', 'donkey', 'mule', 'llama', 'trader_llama',
                                      'camel', 'villager', 'wandering_trader', 'iron_golem', 'snow_golem', 'allay',
                                      'axolotl', 'fox', 'ocelot', 'panda', 'turtle', 'sniffer', 'armadillo',
                                      'strider', 'hoglin', 'piglin', 'zombified_piglin', 'enderman', 'bee')}
USE_S = 4.0               # seconds using an item on something takes (up to it, the click, the drops picked up)
MIN_PER = 0.05            # what a creature or block gives less often than this (a zombie's iron) isn't a way
# the game's verbs for what's got by using something on a creature or a block (its loot tables' folders), and
# what's used ('': the hand): its words, not each thing's
USE_WITH = {'shearing': 'minecraft:shears', 'carve': 'minecraft:shears', 'brush': 'minecraft:brush',
            'till': 'minecraft:wooden_hoe', 'harvest': ''}
PREFETCH_ITEMS = 400      # items looked through for the blocks a plan may need (one search for all of them)
FIND_MANY = 32            # blocks found to choose the quickest trip from (a gather's)
WALK_BPS = 4.317          # blocks a second, walking
CLIMB_S = 1.0             # seconds a block up, reckoned (a step dug or a block put down)
DROP_S = 0.5              # ...a block down (dug or dropped)
MINE_S = 1.5              # ...mining one block of what's gathered
PLACE_RADIUS = 3          # blocks round the player looked at for a spot to put a block down
PLACE_TRIES = 24          # ...the best this many checked for sight
BLOCK_REACH = blocks.REACH   # blocks from the eye a block can be mined or placed at (the game's: 4.5)
FAST = {'spider', 'cave_spider', 'wolf', 'enderman', 'vex', 'phantom', 'bee', 'silverfish', 'endermite',
        'hoglin', 'zoglin', 'piglin_brute', 'ravager', 'breeze'}    # as quick as the player or quicker
RANGED = {'skeleton', 'stray', 'bogged', 'pillager', 'blaze', 'ghast', 'witch', 'drowned', 'shulker', 'breeze',
          'piglin', 'llama', 'snow_golem'}
BRIDGE_VERSION = 20        # the bridge (bridges/minecraft Bridge.VERSION) this adapter needs
SLOW_BREAK = 15           # a block slower than this to break (the bridge's hardness byte: 4 ticks a unit, 3 s)...
SLOW_BREAKS_FOR_TOOL = 3  # ...this many on a route: weigh making a tool first
TOOL_WORTH_S = 20.0       # seconds a tool must save, all told, to be made first
NO_PLACE_S = 120.0        # seconds a cell where placing failed isn't planned for again
HAND_BLOCKS_MIN = 8       # blocks to build with held at least, none in the hotbar: put there before a walk
ROUTE_TINY = 3            # cells of way or fewer, out of time: next to none (the next plan from there: longer)
ROUTE_BUDGET_MAX = 8.0    # seconds a plan may be given at most, so
GRID_REUSE_S = 1.0         # seconds a route's box of blocks is used again for the same box (not asked again)
SPRINT_FOOD = 6           # food at or under this: no running (the game's), so no jump at a run planned
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


def _id(name: str) -> str:
    """'bucket', 'Milk Bucket', 'minecraft:cow' -> the game's id."""
    name = str(name).strip().lower()
    return name if ':' in name else 'minecraft:' + name.replace(' ', '_')


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
                    'sneak': 'LSHIFT', 'inventory': 'E', 'escape': 'ESC', 'swap_hands': 'F',
                    **{f'slot{i}': str(i) for i in range(1, 10)}}

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
                     "fine; a block there, an ore: to where it can be reached), 'mine the iron ore at 12 40 -30' "
                     "(that one block, by where it is), 'take me to the ground' (down to the land itself: from a "
                     "tree, a roof, a pillar; 'get me down'), 'take me to the surface' (up out of a cave or a hole to "
                     "the open sky: walked out or dug up; a tree top is already the surface), 'run away' (from the "
                     "hostile ones near), 'eat something', 'go to that' (what the player looks at), 'stop'.")
    # (the doer's Minecraft know-how: core/doer.py's prompt is any game's)
    doer_notes = ("In Minecraft: the walk pillars up and bridges by itself with blocks held (dirt, cobblestone, "
                  "planks or logs); with none, a walk with no way comes back with how many it would take: get some "
                  "dirt or cobblestone first, or craft logs held into planks (four from each). A pickaxe before "
                  "digging down through stone. Keep clear of creepers. Hostile creatures come out in the dark: one "
                  "may be quicker found at night on the player's own level than by digging down to one. A way "
                  "through several points is one act order: 'go to x y z, then go to x y z' (a hill's points along "
                  "its top or round its foot, from the survey; 'this hill' is the rise the player stands on, if "
                  "they are). A tree top or a roof is not the ground. Building: the player's y is the height of "
                  "their feet: the ground block they stand on is at y - 1 (a floor laid there replaces the ground; "
                  "walls start at y). Keep the player's own cells (x, y and y + 1 where they stand) out of the "
                  "blocks, or they stand inside a wall. Fighting: a wooden or stone sword is a minute's work "
                  "from a tree (get 'wooden_sword' or 'stone_sword'); with a few blocks held (dirt, cobblestone) "
                  "a crowd of zombies is fought from up a 2-block pillar, out of their reach (the fight does "
                  "that itself); skeletons and creepers aren't. A fight with a skeleton is taken on with a shield "
                  "in the off hand (6 planks and an iron ingot: a fight order gets one first itself); one shooting "
                  "from afar is hidden from. Zombies burn in daylight; things dropped where the "
                  "player died last 5 minutes.")
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
                 'tool_slot': t.get('tool_slot', -1), 'slot': pl.get('slot'), 'block_slot': self._building(pl)[0],
                 'blocks': self._building(pl)[1], 'inventory': pl.get('inventory') or {},
                 'hotbar': pl.get('hotbar') or [], 'free_slots': pl.get('free_slots'),
                 'container': d.get('container'),
                 'near': d.get('near'),
                 **{k: pl[k] for k in ('attack_strength', 'attack_delay', 'hurt_time', 'on_ground', 'vy', 'vx', 'vz',
                                       'collided', 'crouching', 'sprinting', 'offhand', 'blocking', 'food', 'last_death',
                                       'air', 'armor')
                    if k in pl},
                 **_water_flags(d.get('near'), pl.get('pos'), pl.get('eye'))}
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
            feet = math.floor(p[1] + 0.01)
            out['standing'] = _standing(feet, self._ground_here(x, z))
            # in a hole: the land round the column far above the feet (dug 14 straight down, it said "standing on
            # the ground": the hole's floor is the ground there, 2026-10-15)
            g = self._heights(x - HOLE_RING, z - HOLE_RING, 2 * HOLE_RING + 1, 2 * HOLE_RING + 1, ground=True)
            if g is not None:
                ring = sorted(v for dz in range(-HOLE_RING, HOLE_RING + 1) for dx in range(-HOLE_RING, HOLE_RING + 1)
                              if max(abs(dx), abs(dz)) == HOLE_RING and (v := g(x + dx, z + dz)) is not None)
                if ring and ring[len(ring) // 2] - feet >= 3:
                    up = ring[len(ring) // 2]
                    out['standing'] = (f'at the bottom of a hole {up - feet} deep (the land round it is that '
                                       f'much higher; up: {up})')
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
        best, ties = None, []
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
                    best, ties = (score, kind, ident), [ident]
                elif kind == best[1] and abs(score - best[0]) < 1e-9:
                    ties.append(ident)
        if best and best[1] == 'creature':
            return 'creature', (best[2],)
        if best and best[1] == 'block':
            ident = best[2]
            if ident.endswith('_ore') and not ident.startswith('minecraft:deepslate_'):
                return 'block', (ident, ident.replace('minecraft:', 'minecraft:deepslate_'))
            # (as well matched, all of them: "the bed" was a black one only, the player's white bed not found,
            # 2026-10-17)
            return 'block', tuple(ties)
        return (best[1], best[2]) if best else None

    def locate(self, query: str) -> dict:
        """The nearest place of that name (a village, a stronghold, a cherry grove...), as /locate finds it:
        name, kind, distance (blocks), compass, position. Structures are found at their middle, on their map
        position (height not known: the player's height is given)."""
        if _DEATH.search(query or ''):
            return self._locate_death()
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

    def _surface(self, x0: int, z0: int, sx: int, sz: int):
        """(x, z) -> the surface there: the land's level round the column (low in the 5 x 5 round it: the land's
        own heights, under the trees), not the open sky over it (at the bottom of a shaft open to the sky it was
        "here we are: the surface", 2026-10-15); a cave is under it, a shaft's floor below it. None: not known."""
        r = SURFACE_RING
        g = self._heights(x0 - r, z0 - r, sx + 2 * r, sz + 2 * r, ground=True)
        if g is None:
            return None

        def top(x, z):
            vs = sorted(v for dz in range(-r, r + 1) for dx in range(-r, r + 1) if (v := g(x + dx, z + dz)) is not None)
            return vs[len(vs) // 4] if vs else None
        return top

    def _locate_surface(self) -> dict:
        """The surface: the land's level round the player (the walk takes any way up to it there is: up a cave,
        out of a hole, dug straight up, whichever is quicker)."""
        me = (self.raw().get('player') or {}).get('pos')
        if not me:
            return {'error': 'no player', 'results': []}
        x, z = math.floor(me[0]), math.floor(me[2])
        top = self._surface(x, z, 1, 1)
        h = top(x, z) if top else None
        if h is None:
            return {'error': 'the surface height is not known here', 'results': []}
        if me[1] >= h - 0.5:
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

    def _locate_death(self) -> dict:
        """Where the player last died (the game keeps it: a recovery compass points there): what they had lies
        there for 5 minutes. ("Do you know where I died?" "Sorry, I don't have a record", 2026-10-16.)"""
        d = self.raw()
        pl = d.get('player') or {}
        at, me = pl.get('last_death'), pl.get('pos')
        if not at or not me:
            return {'error': "the game has no death of yours on record", 'results': []}
        x, y, z, dim = at[0], at[1], at[2], at[3] if len(at) > 3 else None
        if dim and d.get('dimension') and dim != d.get('dimension'):
            return {'error': f'you last died in {_pretty(dim)}', 'results': []}
        dx, dz = x + 0.5 - me[0], z + 0.5 - me[2]
        return {'query': 'where you died', 'results': [{
            'name': 'where you died', 'kind': 'death', 'distance_m': round(math.hypot(dx, dz)),
            'compass': _compass(dx, dz), 'height_diff_m': round(y - me[1]),
            'coordinates': {'x': x, 'y': y, 'z': z}, 'position': _mirror((x + 0.5, float(y), z + 0.5)),
            'note': 'what you had lies there for 5 minutes after you died, then it is gone'}]}

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

    def goal_point(self, x=None, y=None, z=None, within=None):
        """A point to walk to and what counts as there, as Baritone's goals: x y z (GoalBlock: stood there; a
        solid block, in reach of it), x z (GoalXZ: that spot across, at any height), y (GoalYLevel: that height,
        anywhere: dug down or climbed up to), within (GoalNear: within that many blocks of x y z)."""
        me = (self.raw().get('player') or {}).get('pos') or [0.0, 0.0, 0.0]
        if x is None or z is None:                        # (a height only: from here, straight down or up)
            if y is None:
                return None
            x, z = me[0], me[2]
            cell = (math.floor(x), math.floor(y), math.floor(z))
            point = _mirror((cell[0] + 0.5, float(cell[1]), cell[2] + 0.5))
            self._goals[_goal_key(point)] = ('level', cell)
            return point
        if y is None:                                    # (across only)
            cell = (math.floor(x), math.floor(me[1] + 0.01), math.floor(z))
            point = _mirror((cell[0] + 0.5, float(cell[1]), cell[2] + 0.5))
            self._goals[_goal_key(point)] = ('column', None)
            return point
        point = self.coords_point(x, y, z)
        if within:
            cell = (math.floor(x), math.floor(y), math.floor(z))
            self._goals[_goal_key(point)] = (f'within:{float(within):g}', cell)
        return point

    def coords_point(self, x: float, y: float, z: float):
        """A point to walk to from the game's own coordinates (F3, as where_is and survey give them): stood there;
        or, a solid block there (an ore), where it's in reach and in sight (walked "into" an iron ore's own cell,
        the way was dug toward it and lost for a minute, 2026-10-15)."""
        key = (math.floor(x), math.floor(y), math.floor(z))
        point = _mirror((x + 0.5, float(y), z + 0.5))
        solid = self.block_solid(key)
        self._goals[_goal_key(point)] = ('mine', key) if solid else ('exact', key)
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

    def _building(self, pl: dict) -> tuple[int, int]:
        """(the hotbar slot to build with, how many blocks there are to build with): dirt and stone first, then
        planks, then logs; none of what the task's steps to come use up (self.reserve: past that, the rest).
        Creative: as many as wanted."""
        if pl.get('creative') or int(pl.get('blocks') or 0) >= 999:
            return int(pl.get('block_slot', -1)), 999
        inv = pl.get('inventory') or {}
        reserve = getattr(self, 'reserve', None) or {}
        best, spare = None, {}
        for i, s in enumerate(pl.get('hotbar') or []):
            it = (s[0] if s else '') or ''
            rank = 0 if it in BUILD_FIRST else 1 if it.endswith('_planks') else \
                2 if it.endswith(('_log', '_stem')) else -1
            if rank < 0:
                continue
            free = min(sum(int(t[1]) for t in pl.get('hotbar') or [] if t and t[0] == it),
                       int(inv.get(it, 0)) - int(reserve.get(it, 0)))
            if free <= 0:
                continue
            spare[it] = free
            if best is None or rank < best[0]:
                best = (rank, i)
        return (best[1] if best else -1), sum(spare.values())

    def blocks_to_hand(self):
        """('arrange', layout): the most plentiful block to build with that's held into the hotbar, when none to
        build with is there (only the hotbar's are built with, and planned with); None when there are some there,
        or too few held. (70 cobblestone and 47 diorite in the pack, none in the hotbar: a ravine 38 deep was dug
        out of step by step, 6 s a block, not climbed by a pillar, 2026-10-17.)"""
        pl = self.raw().get('player') or {}
        if pl.get('creative') or self._building(pl)[1] > 0:
            return None
        inv = pl.get('inventory') or {}
        reserve = getattr(self, 'reserve', None) or {}
        spare = {it: n - int(reserve.get(it, 0)) for it, n in inv.items() if it in BUILD_FIRST}
        spare = {it: n for it, n in spare.items() if n >= HAND_BLOCKS_MIN}
        if not spare:
            return None
        best = max(spare, key=lambda it: spare[it])
        return ('arrange', self.hotbar_layout(inv, pl.get('hotbar') or [], [best]))

    def plan_in_worker(self) -> None:
        """Routes planned in a process of their own (pathing.run): the search never holds up the frame loop (it ran
        at 115 ms a frame while a long climb was planned again and again, 2026-10-17)."""
        pathing.use_worker(True)

    def equipment(self, inventory: dict, offhand: str | None = None, items=()) -> list:
        """The task steps putting on what's held to wear (core/tasks.py 'equip', 'offhand'): the best piece for
        each of the head, chest, legs and feet (what's worn isn't known: one worse than it is swapped back), and
        a shield into an empty off hand. items: only these (the game's ids). ("Equip my armour": "my tools can only
        sort the hotbar", 2026-10-17.)"""
        held = [k for k, v in (inventory or {}).items() if v and (not items or k in items)]
        steps = []
        for part in ARMOR_PARTS:
            got = [h for h in held if h.split(':')[-1].endswith('_' + part)]
            if got:
                steps.append(('equip', min(got, key=lambda h: next(
                    (k for k, t in enumerate(ARMOR_TIERS) if h.split(':')[-1].startswith(t)), 99))))
        if 'minecraft:shield' in held and not offhand:
            steps.append(('offhand', 'minecraft:shield'))
        return steps

    def hotbar_layout(self, inventory: dict, hotbar: list, items=(), sort: bool = False, slots=None) -> tuple:
        """What each of the nine hotbar slots is to hold (an item, or None: left as it is), for the inventory
        skill (core/tasks.py 'arrange'). items: to be in the hotbar; sort: the usual layout, as players keep
        it (the best weapon, pickaxe, axe, shovel, food, blocks to build with, a torch; then items; then what
        else was there); slots: {slot 1-9: item} as asked. Only what's held."""
        held = {k: v for k, v in (inventory or {}).items() if v}
        now = [((s[0] if s else '') or None) for s in (hotbar or [])][:9]
        now += [None] * (9 - len(now))
        items = [i for i in items if i in held]
        if not sort:
            out = list(now)
            for it in items:
                if it in out:
                    continue
                spot = next((j for j in range(9) if out[j] is None), None)
                if spot is None:                         # (full: the last that isn't one of items)
                    spot = next((j for j in range(8, -1, -1) if out[j] not in items), 8)
                out[spot] = it
        else:
            bar = [(h, 1) for h in held]
            weapon = self.weapon_slot(bar)
            weapon = bar[weapon][0] if weapon is not None and bar[weapon][0] else None

            def best(suffix):
                got = [h for h in held if h.split(':')[-1].endswith(suffix) and h != weapon]
                order = ['netherite', 'diamond', 'iron', 'copper', 'stone', 'golden', 'wooden']
                return min(got, key=lambda h: next((k for k, t in enumerate(order)
                                                    if h.split(':')[-1].startswith(t)), 9), default=None)
            food = self.food_slot([(h, 1) for h in held])
            food = list(held)[food] if food is not None else None
            blocks = max((h for h in held if h in BUILD_FIRST), key=lambda h: held[h], default=None)
            torch = 'minecraft:torch' if 'minecraft:torch' in held else None
            order = [weapon, best('_pickaxe'), best('_axe'), best('_shovel'), food, blocks, *items, torch,
                     *[h for h in now if h]]
            out = []
            for h in order:
                if h and h not in out:
                    out.append(h)
            for it in items:                             # (what was asked for always in)
                if it not in out[:9]:
                    drop = next(k for k in range(8, -1, -1) if out[k] not in items)
                    out[drop] = it
            out = (out + [None] * 9)[:9]
        for k, it in (slots or {}).items():               # (asked for, slot by slot)
            j = int(k) - 1
            if 0 <= j < 9 and it in held:
                out[j] = it
        return tuple(out)

    def learn(self, way: dict) -> str:
        """A way to get an item learned (the wiki): kept in .local/minecraft/learned.json; the book read again."""
        from . import data
        way = {'item': _id(way['item']), 'target': _id(way['target']), 'on': way.get('on') or 'block',
               'using': _id(way['using']) if way.get('using') else '', 'per': float(way.get('per') or 1.0)}
        n = len(data.learn(way))
        self._book = None
        return f"learned: {way['item']} from {way['target']}" + (f" with {way['using']}" if way['using'] else '') + \
            f' ({n} learned ways in all)'

    def creature_near(self, kind: str, variant: dict | None = None, exclude=()):
        """The nearest creature of that kind (and kind of it: a sheep's colour, not shorn) about, not one of
        exclude: (where it is (snapshot coordinates), its name, its id); None: none loaded near."""
        got = self.creatures_near(kind, variant, exclude, want=1)
        return got[0] if got else None

    def creatures_near(self, kind: str, variant: dict | None = None, exclude=(), want: int = 0) -> list:
        """Every creature of that kind (and kind of it) about, nearest first, as creature_near gives one. Fewer
        than want about: the nearest farther off in the loaded world too (the game's search, as where_is: what
        kind of it they are isn't known from there, so they're last; walked up to, they're looked at again).
        (Sheep 90 blocks off were found by where_is, and "get wool" said there were none, 2026-10-16.)"""
        d = self.raw()
        me = (d.get('player') or {}).get('pos')
        if not me:
            return []
        found, seen = [], set()
        for e in d.get('entities') or []:
            if e.get('type') != kind:
                continue
            seen.add(e.get('id'))
            if e.get('dead') or e.get('id') in exclude:
                continue
            have = e.get('variant') or {}
            if any(have.get(k, v) != v for k, v in (variant or {}).items()):
                continue
            p = e['pos']
            far = math.hypot(p[0] - me[0], p[2] - me[2]) + abs(p[1] - me[1]) * 3.0
            if far <= HUNT_RANGE:
                found.append((far, e))
        found.sort(key=lambda fe: fe[0])
        out = [(_mirror(e['pos']), e.get('name') or _pretty(kind), e['id']) for _f, e in found]
        if len(out) < want:
            cache = self.__dict__.setdefault('_far_creatures', {})     # (a plan asks of each kind many times)
            hit = cache.get(kind)
            if hit is not None and time.monotonic() - hit[0] < FAR_CREATURES_FOR:
                got = hit[1]
            else:
                try:
                    got = self._ask({'op': 'find_entities', 'types': [kind], 'max': FAR_CREATURES}, timeout=2.0)
                except Exception:
                    got = {}
                cache[kind] = (time.monotonic(), got)
            far = []
            for x, y, z, ident, eid in got.get('found') or []:
                if eid in seen or eid in exclude:
                    continue
                far.append((math.hypot(x - me[0], z - me[2]) + abs(y - me[1]) * 3.0, (x, y, z), eid))
            for _f, pos, eid in sorted(far)[:want - len(out)]:
                out.append((_mirror(pos), _pretty(kind), eid))
        return out

    def game_coords(self, p):
        """A snapshot position in the game's own coordinates (F3)."""
        return _mirror(p)

    def goal_back(self, point) -> None:
        """The walk to point (snapshot coordinates) will be come back along (what's gathered there is taken on):
        ways down that can't be walked back up cost the climb back."""
        self._back_goals = getattr(self, '_back_goals', set()) | {_goal_key(point)}

    def _foe_costs(self, lo, size, goal=None) -> dict:
        """{cell: cost multiplier} round each hostile creature in the box, Baritone's avoidance: a move into a
        cell within FOE_AVOID of one costs FOE_COST times as much (twice as near two of them: that twice over), so
        the walk goes round them, not past them (to where the player had died, at night, through four zombies,
        2026-10-16). Not a spider in daylight, nor one that's only hostile when provoked and isn't; nor one at the
        goal (it's what's walked to: a creature to fight)."""
        out: dict = {}
        light = (self.raw().get('world') or {}).get('light')
        r = int(FOE_AVOID)
        for e in (self.raw().get('entities') or []):
            if not e.get('hostile') or e.get('dead') or not e.get('pos'):
                continue
            kind = str(e.get('type') or '').split(':')[-1]
            if kind in NEUTRAL_KINDS and not e.get('aggressive'):
                if not (kind in ('spider', 'cave_spider') and light is not None and light < 8):
                    continue
            if goal is not None and math.hypot(e['pos'][0] - goal[0], e['pos'][2] - goal[2]) < FOE_GOAL:
                continue
            ex, ey, ez = (math.floor(v) for v in e['pos'])
            if not (lo[0] - r <= ex <= lo[0] + size[0] + r and lo[2] - r <= ez <= lo[2] + size[2] + r):
                continue
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for dz in range(-r, r + 1):
                        if dx * dx + dy * dy + dz * dz <= r * r:
                            c = (ex + dx, ey + dy, ez + dz)
                            out[c] = out.get(c, 1.0) * FOE_COST
        return out

    def mover(self):
        """What does a route move by move in the game (moving.Mover, Baritone's way)."""
        from .moving import Mover
        return Mover(aimer=self.aim_point, probe=self.block_solid)

    def goal_place(self, point, cell) -> None:
        """The walk to point (snapshot coordinates) ends where a block can be put into cell (the game's
        coordinates): in reach and in sight of it, not standing in it."""
        self._goals[_goal_key(point)] = ('place', tuple(int(v) for v in cell))

    def walk_limits(self, point, place_blocks: bool = True, dig: bool = True, fewest_jumps: bool = False) -> None:
        """The walk to point (snapshot coordinates) as the order said: without putting blocks down, without
        digging; with as few jumps over gaps as there can be ("can you do it with just one jump?")."""
        lim = getattr(self, '_limits', None)
        if lim is None:
            lim = self._limits = {}
        lim[_goal_key(point)] = {'place_blocks': bool(place_blocks), 'dig': bool(dig),
                                 'fewest_jumps': bool(fewest_jumps)}

    def way_check(self, target=None, place_blocks: bool = True, dig: bool = True, on_top: bool = True,
                  most_jumps: int | None = None) -> dict:
        """Whether the player could get to target, and how (the planner's own way, nothing done): target 'that'
        (what the crosshair's on: onto its top, on_top) or (x, y, z) (the game's: where the feet would be). Its
        jumps (the gap, at a run or not, up a block), drops, blocks put down and dug, about how long; with or
        without putting blocks down or digging. ("Could you jump onto that crafting table from here without
        placing blocks?": "yes, walk two blocks closer", then "probably not": guessed, 2026-10-16.) most_jumps:
        the way with the fewest jumps over gaps, and whether that's no more than this ("can you do it with just
        one jump?": "I can't", read off the quickest way's two, 2026-10-16)."""
        d = self.raw()
        me = (d.get('player') or {}).get('pos')
        if not me:
            return {'error': 'no player in the world'}
        if target in (None, 'that', 'crosshair'):
            t = d.get('target') or {}
            if t.get('kind') != 'block':
                return {'error': 'the crosshair is on no block'}
            bx, by, bz = (int(v) for v in t['pos'])
            cell = (bx, by + 1, bz) if on_top else (bx, by, bz)
        else:
            cell = tuple(math.floor(float(v)) for v in target)
        goal = _mirror((cell[0] + 0.5, float(cell[1]), cell[2] + 0.5))
        self._goals[_goal_key(goal)] = ('cell', cell)

        def plan(fewest):
            self.walk_limits(goal, place_blocks, dig, fewest_jumps=fewest)
            try:
                return self.route(_mirror(me), goal, budget_s=1.0)
            finally:
                self._limits.pop(_goal_key(goal), None)

        def jumps_of(r):
            return [m for m in (getattr(r, 'moves', None) or []) if m.kind in ('parkour', 'leap')]
        # (in no more jumps than asked: the quickest way, if it's within that; else the way with the fewest)
        route, fewest = plan(False), False
        if most_jumps is not None and route is not None and getattr(route, 'reaches_goal', False) \
                and len(jumps_of(route)) > most_jumps:
            route, fewest = plan(True), True
        note = dict(getattr(self, 'route_note', None) or {})
        out = {'to': {'x': cell[0], 'y': cell[1], 'z': cell[2]}, 'placing_blocks': place_blocks, 'digging': dig}
        if route is None or not getattr(route, 'reaches_goal', False):
            out.update(can=False, why=note.get('found') or 'no way',
                       blocks_it_would_take=note.get('blocks_it_would_take'))
            return {k: v for k, v in out.items() if v is not None}
        moves = list(getattr(route, 'moves', []) or [])
        jumps = [{'gap': m.get('dist') - 1, 'at_a_run': bool(m.get('sprint')), 'up_a_block': bool(m.get('ascend'))}
                 if m.kind == 'parkour' else
                 {'across': m.get('dist'), 'slanted': m.src[0] != m.dest[0] and m.src[2] != m.dest[2],
                  'at_a_run': bool(m.get('sprint')), 'up_a_block': m.get('rise') == 1,
                  'down_a_block': m.get('rise') == -1}
                 for m in jumps_of(route)]
        ticks = getattr(route, 'cost', 0.0) - (pathing.FEWEST_JUMPS * len(jumps) if fewest else 0.0)
        if most_jumps is not None and len(jumps) > most_jumps:
            out.update(can=False, why=f'it takes {len(jumps)} jumps at the fewest', jumps=jumps)
            return out
        out.update(can=True, seconds=round(ticks / 20.0, 1), steps=len(moves),
                   jumps=jumps, drops=[m.get('height') for m in moves if m.kind == 'fall'],
                   blocks_put_down=sum(1 for m in moves if m.place is not None),
                   blocks_dug=sum(len(m.breaks) for m in moves),
                   steps_up=sum(1 for m in moves if m.kind == 'ascend'),
                   go_to={'x': cell[0], 'y': cell[1], 'z': cell[2], 'place_blocks': place_blocks, 'dig': dig,
                          **({'fewest_jumps': True} if fewest else {})})
        return out

    def top_of(self, what=None):
        """The cell (the game's coordinates) the feet are in stood on top of a block: the one the crosshair's on
        (what None or 'that'), or the nearest of a kind of block by name. None if there's none. ("Jump onto that
        crafting table": checked onto its top, then walked to beside it, 2026-10-16.)"""
        if what in (None, '', 'that', 'crosshair'):
            t = self.raw().get('target') or {}
            if t.get('kind') != 'block':
                return None
            bx, by, bz = (int(v) for v in t['pos'])
            return (bx, by + 1, bz)
        got = self.locate(what)
        for r in got.get('results') or []:
            c = r.get('coordinates')
            if r.get('kind') == 'block' and c:
                return (int(c['x']), int(c['y']) + 1, int(c['z']))
        return None

    def cells_now(self, cells, items) -> dict:
        """What's in each of these cells (the game's coordinates) now, and round them: 'same' (one of items is
        there), 'other' (something solid else), 'air' (nothing to stand on: air, water, a plant)."""
        cells = [tuple(int(v) for v in c) for c in cells]
        if not cells:
            return {}
        lo = [min(c[i] for c in cells) - 1 for i in range(3)]
        hi = [max(c[i] for c in cells) + 1 for i in range(3)]
        size = [hi[i] - lo[i] + 1 for i in range(3)]
        got = self._ask({'op': 'grid', 'min': lo, 'size': size}, timeout=4.0)
        if 'error' in got:
            return {}
        g = blocks.Grid.from_bridge(got)
        me = (self.raw().get('player') or {}).get('pos') or [0.0, 0.0, 0.0]
        far = min(64, max(abs(c[i] - math.floor(me[i])) for c in (lo, hi) for i in range(3)) + 1)
        same = set()
        if items:                                        # (the bridge searches round the player)
            hit = self._ask({'op': 'find_blocks', 'ids': sorted(items), 'max': 4096, 'radius': far}, timeout=4.0)
            same = {tuple(int(v) for v in f[:3]) for f in hit.get('found') or []}
        out = {}
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    k = g.at(x, y, z)
                    out[(x, y, z)] = 'same' if (x, y, z) in same else \
                        'other' if k in (blocks.SOLID, blocks.PARTIAL, blocks.DANGER, blocks.UNKNOWN) else 'air'
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
        if what == 'jump' and info.get('from') and info.get('to'):    # (one not made, not running at it)
            self._no_jump = getattr(self, '_no_jump', {})
            self._no_jump[(tuple(info['from']), tuple(info['to']))] = time.monotonic()

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

    def locate_blocks(self, ids, to_mine: bool = False, near=None) -> dict:
        """The nearest blocks of these ids (as locate() for a kind of block). to_mine: walked to within reach
        to mine (not to stand beside and look at). near (the game's coordinates): where what's got is taken
        next (where the step began: by the station it's used at): the quickest there and back first, a block
        straight under the feet only where the fall after it is safe (each stone the nearest, dug straight down
        16 blocks from the crafting table, the last 6 a fall into a cave, then all the way back up for the
        furnace, 2026-10-16)."""
        if to_mine and near is None:                     # (back to here: and hostile ones near counted, always)
            near = (self.raw().get('player') or {}).get('pos')
        got = self._locate_block(_pretty(ids[0]) if ids else 'block', ids, many=near is not None)
        if near is not None and got.get('results'):
            got['results'] = self._quickest(got['results'], tuple(near))
        if to_mine:
            for r in got.get('results', []):
                c = r['coordinates']
                self._goals[_goal_key(r['position'])] = ('mine', (c['x'], c['y'], c['z']))
        return got

    def _quickest(self, results: list, near: tuple) -> list:
        """The found blocks, the quickest to get to first, as Baritone's mining chooses: one search toward the
        nearest of them at once (QUICK_MANY), and the one its way reaches first is the one (not the nearest as the
        crow flies: a tree 34 blocks off was taken past two closer ones, 2026-10-16); the rest after it by the
        trip (to it, then on to near). A block straight under the feet is left out when the fall after it isn't
        safe."""
        me = (self.raw().get('player') or {}).get('pos')
        if not me:
            return results
        feet = (math.floor(me[0]), math.floor(me[1] + 0.01), math.floor(me[2]))
        eye = (me[0], me[1] + blocks.EYE, me[2])

        def trip(a, b):
            dy = b[1] - a[1]
            return math.hypot(b[0] - a[0], b[2] - a[2]) / WALK_BPS + (dy * CLIMB_S if dy > 0 else -dy * DROP_S)
        rows = []
        for r in results:
            c = r['coordinates']
            cell = (c['x'], c['y'], c['z'])
            mid = (cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5)
            if cell == (feet[0], feet[1] - 1, feet[2]) and not self._safe_to_dig_under(cell):
                continue
            there = 0.0 if math.dist(eye, mid) <= blocks.PLAN_REACH else trip(me, mid)
            rows.append((there + trip(mid, near), cell, r))
        rows.sort(key=lambda x: x[0])
        if len(rows) > 1 and rows[0][0] > 0.0:
            first = self._reached_first([row[1] for row in rows[:QUICK_MANY]], me)
            if first is not None:
                rows.sort(key=lambda x: (x[1] != first, x[0]))
        return [row[2] for row in rows[:5]]

    def _reached_first(self, cells, me):
        """Of these blocks (the game's cells), the one the cheapest way (to mine it: in reach and sight) gets to,
        or None (none got to in the time)."""
        pts = [me] + [(c[0] + 0.5, c[1] + 0.5, c[2] + 0.5) for c in cells]
        lo = [math.floor(min(p[i] for p in pts)) - (QUICK_MARGIN if i != 1 else ROUTE_DEPTH) for i in range(3)]
        hi = [math.floor(max(p[i] for p in pts)) + (QUICK_MARGIN if i != 1 else ROUTE_DEPTH) for i in range(3)]
        size = [hi[i] - lo[i] + 1 for i in range(3)]
        while size[0] * size[1] * size[2] > 160 * 1024 and size[1] > 16:
            size[1] -= 2
            lo[1] += 1
        if size[0] * size[1] * size[2] > 160 * 1024:
            return None
        got = self._ask({'op': 'grid', 'min': lo, 'size': size}, timeout=4.0)
        if 'error' in got:
            return None
        grid = blocks.Grid.from_bridge(got)
        start = (math.floor(me[0]), math.floor(me[1] + 0.01), math.floor(me[2]))
        have = self._building(self.raw().get('player') or {})[1]
        try:
            path = pathing.run('find', grid, start, cells[0], goals=cells, kind='mine', budget_s=QUICK_S,
                                can_break=self.can_break, blocks=have, foes=self._foe_costs(lo, size))
        except Exception:
            return None
        return path.goal if path is not None and path.reaches_goal else None

    def _safe_to_dig_under(self, cell) -> bool:
        """Mining the block stood on drops the player into its cell: safe when it lands within a safe fall (or in
        water), not into a cave or lava."""
        x, y, z = cell
        got = self._ask({'op': 'grid', 'min': [x, y - 10, z], 'size': [1, 11, 1]}, timeout=1.0)
        if 'error' in got:
            return False
        g = blocks._opened(blocks.Grid.from_bridge(got), [cell])
        return blocks._fall(g, x, y, z) <= blocks.MAX_DROP

    def _locate_block(self, query: str, ids, many: bool = False) -> dict:
        """The nearest blocks of these kinds in the loaded world round the player (64 blocks out, 32 up and
        down): the nearest first, at the block (the walk goes up to it, mining its way if need be). many: more
        of them (to choose from)."""
        got = self._ask({'op': 'find_blocks', 'ids': list(ids), 'max': FIND_MANY if many else 5}, timeout=4.0)
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
        # (as well matched, the white one first: what one is undyed; "a bed" of 16 colours had only the first 6 in
        # the book planned, none white, and a purple bed was set about: dyes from flowers first, 2026-10-16)
        scored.sort(key=lambda x: (-x[0], not x[1].split(':')[-1].startswith('white_')))
        return [i for _s, i in scored[:6]]

    def items_near(self, items: tuple | None, radius: float) -> list:
        """Dropped items of these kinds (None: any) lying within radius of the player, nearest first (snapshot
        coordinates)."""
        d = self.raw()
        me = (d.get('player') or {}).get('pos') or [0, 0, 0]
        out = []
        for e in d.get('entities') or []:
            if e.get('item') and (items is None or e.get('item') in items):
                p = e['pos']
                dist = math.dist(p, me)
                if dist <= radius:
                    at = _mirror(tuple(p))
                    # walked to: onto its own block (within reach of it isn't enough to pick it up)
                    self._goals[_goal_key(at)] = ('item', (math.floor(p[0]), math.floor(p[1] + 0.01),
                                                           math.floor(p[2])))
                    out.append((dist, at))
        return [p for _d, p in sorted(out)]

    def place_spot(self, exclude=(), only=None) -> tuple | None:
        """Where to put a block down near the player (a crafting table, a furnace): (aim point, the block clicked,
        its face, the new block), the keys in the game's block coordinates. Any open cell within reach that
        neither the player's body nor a creature is in, beside a solid block whose face toward it is in sight
        from the eye (a ray of the game's says so); on the ground near the feet first. (Only the 8 cells round the
        feet were tried: stood in a dip, every one was ground, and "I can't find a spot" twice on open plains,
        with animals about, 2026-10-13.) Seen as the crosshair sees, from the eye crouched (it's put down
        crouched, never using what's clicked): grass in the spot is put over (a block takes its place); a plant in
        the way, knocked out first. exclude: spots (new blocks' cells) tried already. only: that cell (the game's
        coordinates: a block of a building), in reach and in sight from here, or None."""
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
        lo, size = [x - r, y - 2, z - r], [2 * r + 1, 6, 2 * r + 1]
        if only is not None:
            only = tuple(int(v) for v in only)
            lo = [min(lo[i], only[i] - 1) for i in range(3)]
            size = [max(x - r + 2 * r + 1, only[0] + 2) - lo[0], max(y + 4, only[1] + 2) - lo[1],
                    max(z - r + 2 * r + 1, only[2] + 2) - lo[2]]
        got = self._ask({'op': 'grid', 'min': lo, 'size': size}, timeout=2.0)
        if 'error' in got:
            return None
        g = blocks.Grid.from_bridge(got)
        taken = _cells_in(p, 0.3, 1.8)                           # the player's body
        for e in d.get('entities') or []:
            if e.get('living') and not e.get('dead') and e.get('pos'):
                taken |= _cells_in(e['pos'], float(e.get('w') or 0.6) / 2, float(e.get('h') or 1.0))
        cands = []
        cells = [only] if only is not None else [(cx, cy, cz) for cy in range(y - 1, y + 3)
                                                  for cz in range(z - r, z + r + 1) for cx in range(x - r, x + r + 1)]
        for c in cells:
            cx, cy, cz = c
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
        {(dx, dz): 'water' | 'lava' | 'drop' | 'wall'}} for the 8 cells round the player (snapshot coordinates'
        x); and over FIGHT_SPACE blocks round: 'open' (x, z) -> whether the player can stand there (about this
        high, a step up or down; snapshot coordinates), 'room' (x, z) -> how many cells can be got to from there
        in ROOM_STEPS steps (few: a corner, a dead end; backed into one by a zombie, the player died there,
        2026-10-16); 'floor' (x, z) -> the height the feet are at standing there, or None."""
        d = self.raw()
        p = (d.get('player') or {}).get('pos')
        if not p:
            return {}
        x, y, z = math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2])
        r = FIGHT_SPACE
        got = self._ask({'op': 'grid', 'min': [x - r, y - 5, z - r], 'size': [2 * r + 1, 9, 2 * r + 1]},
                        timeout=1.0)
        if 'error' in got:
            return {}
        g = blocks.Grid.from_bridge(got)
        stands = {}                                      # (game x, z) -> the feet's height there
        for cx in range(x - r, x + r + 1):
            for cz in range(z - r, z + r + 1):
                for h in (y, y + 1, y - 1):
                    k = g.at(cx, h, cz)                  # (a snow layer, a carpet: stood on, in its cell)
                    if (k == blocks.LOW or k in blocks.PASS and k != blocks.WATER
                            and g.at(cx, h - 1, cz) in blocks.FLOOR) and g.at(cx, h + 1, cz) in blocks.PASS:
                        stands[(cx, cz)] = h
                        break

        def open_at(sx, sz):
            return (math.floor(-sx), math.floor(sz)) in stands  # (snapshot x is the game's mirrored)

        def room(sx, sz):
            start = (math.floor(-sx), math.floor(sz))
            if start not in stands:
                return 0
            seen, edge = {start}, [start]
            for _ in range(ROOM_STEPS):
                nxt = []
                for c in edge:
                    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n = (c[0] + dx, c[1] + dz)
                        if n in stands and n not in seen and abs(stands[n] - stands[c]) <= 1:
                            seen.add(n)
                            nxt.append(n)
                edge = nxt
            return len(seen)
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
        def floor(sx, sz):
            return stands.get((math.floor(-sx), math.floor(sz)))

        return {'in_water': g.at(x, y, z) == blocks.WATER or g.at(x, y + 1, z) == blocks.WATER, 'avoid': avoid,
                'open': open_at, 'room': room, 'floor': floor}

    def combat_profile(self, e) -> dict:
        """What a kind of character does in a fight (core/melee.py): explodes, shoots, how far it hits."""
        kind = (e.model or '').split(':')[-1]
        return {'explodes': kind == 'creeper',
                'ranged': kind in RANGED,
                # (up a pillar is no way out of their reach: two spiders climbed after the player, dead,
                # 2026-10-17)
                'climbs': kind in ('spider', 'cave_spider'),
                'flies': kind in ('phantom', 'vex', 'bee', 'ghast', 'blaze', 'breeze'),
                # can't be got away from on foot (a spider bit the player to death as it backed off, 2026-10-13)
                'fast': kind in FAST or bool((e.extra or {}).get('baby')),
                # (hostile only when provoked: walks don't stop for them)
                'neutral': kind in ('enderman', 'zombified_piglin', 'piglin', 'spider', 'cave_spider')
                and not (e.extra or {}).get('aggressive'),
                # how far past the widths it hits (Minecraft's melee reach: 0.83 for most; bigger for these)
                'reach': {'ravager': 1.6, 'warden': 1.6, 'iron_golem': 1.6, 'hoglin': 1.2, 'zoglin': 1.2}.get(kind,
                                                                                                           0.83)}

    def sight_pairs(self, pairs) -> list:
        """For each (eye, point) (snapshot coordinates), whether the point is in the eye's sight (one batch of the
        game's rays)."""
        if not pairs:
            return []
        rays = [(_mirror(a), _mirror(b)) for a, b in pairs]
        hits = self._sight(rays)
        hits += [None] * (len(rays) - len(hits))
        return [h is None or math.dist(h['at'], a) >= math.dist(a, b) - 0.3 for (a, b), h in zip(rays, hits)]

    def in_sight(self, eyes, to) -> list:
        """For each eye (snapshot coordinates), whether to (snapshot coordinates) is in its sight: nothing solid on
        the line between, by the game's own rays."""
        if not eyes:
            return []
        b = _mirror(to)
        rays = [(_mirror(e), b) for e in eyes]
        hits = self._sight(rays)
        out = []
        for (a, _b), h in zip(rays, hits + [None] * (len(rays) - len(hits))):
            out.append(h is None or math.dist(h['at'], a) >= math.dist(a, b) - 0.3)
        return out

    def cover_spot(self, foes) -> tuple | None:
        """The nearest spot (snapshot coordinates) within COVER_RADIUS where the player can stand out of the sight of
        all those that shoot (foes: their eyes, snapshot coordinates; or one eye): the game's own rays from each eye
        to the head and the body there, at the height the feet would really be at, each stopped by a block. The
        walk there ends on that very block. None: none near. (Tested at the player's own height, with the head
        only, and "there" a block short of it: a spot 3 blocks off, shot from 20 health to 4 there, 2026-10-16.)"""
        if foes and isinstance(foes[0], (int, float)):
            foes = [foes]
        d = self.raw()
        p = (d.get('player') or {}).get('pos')
        if not p:
            return None
        ground = self.terrain() or {}
        open_at, floor = ground.get('open'), ground.get('floor')
        if open_at is None:
            return None
        sp = _mirror(p)                                  # (the player, snapshot coordinates)
        cands = []
        r = COVER_RADIUS
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                x, z = math.floor(sp[0]) + dx + 0.5, math.floor(sp[2]) + dz + 0.5
                far = math.hypot(x - sp[0], z - sp[2])
                seen_in = (math.floor(x), math.floor(z)) in getattr(self, 'cover_avoid', ())
                if far <= r and open_at(x, z) and not seen_in:
                    y = floor(x, z) if floor is not None else None
                    cands.append((far, (x, float(y if y is not None else math.floor(sp[1] + 0.01)), z)))
        cands.sort()
        cands = cands[:COVER_TRIES]
        if not cands:
            return None
        rays = [(_mirror(f), _mirror((c[0], c[1] + h, c[2]))) for _f, c in cands for f in foes for h in COVER_SEEN_AT]
        hits = self._sight(rays)
        hits += [None] * (len(rays) - len(hits))
        k = len(foes) * len(COVER_SEEN_AT)
        for i, (_far, c) in enumerate(cands):
            hidden = all(h is not None and math.dist(h['at'], a) < math.dist(a, b) - 0.3
                         for h, (a, b) in zip(hits[i * k:(i + 1) * k], rays[i * k:(i + 1) * k]))
            if hidden:                                   # (a block between it and each of them, head and body)
                cell = (math.floor(-c[0]), math.floor(c[1] + 0.01), math.floor(c[2]))
                self._goals[_goal_key(c)] = ('cell', cell)
                return c
        return None

    def fight_gear(self, e, extra: dict) -> list:
        """What a fight with e wants that isn't there: [(items, where)], items the ones that would do (the quickest
        got), where 'hand' (in the hotbar: the fighter takes the best) or 'offhand'. A weapon, bare-handed (sent to
        kill skeletons with nothing, it went for a shield and fought a zombie with its fists: 15 hits, still up, the
        player dead, 2026-10-16); a shield against one that shoots (the user: "it should get a shield before trying
        to kill a skeleton"). [] when it's all there."""
        extra = extra or {}
        need = []
        # (less than a sword's: a pickaxe, 2 to 3, was taken for armed, and two spiders killed the player at night,
        # 2026-10-17; a stone sword is cobblestone and a stick)
        if (self.weapon_damage(extra.get('hotbar') or []) or 1.0) < SWORD_DAMAGE:
            need.append((('minecraft:stone_sword', 'minecraft:wooden_sword'), 'hand'))
        if self.combat_profile(e).get('ranged') and extra.get('offhand') not in ('minecraft:shield',):
            need.append((('minecraft:shield',), 'offhand'))
        return need

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
        # (planned again from about the same spot after next to no way came of the last plan: twice the time, up
        # to ROUTE_BUDGET_MAX; the plan's in a process of its own, the game's never held up by it. Out of a
        # ravine, plan after plan ran out of time with one block of way, stuck, 2026-10-17)
        last = getattr(self, '_route_last', {}).get(_goal_key(goal))
        if last is not None and math.dist(last[0], s) < 2.0 and last[1] == 'part' and last[2] <= ROUTE_TINY:
            budget_s = min(ROUTE_BUDGET_MAX, max(budget_s, last[3] * 2))
        to_mine = kind in ('mine', 'exact', 'place')       # (the goal's cell given: no 'look' at the end)
        box = None
        sky_goal = kind == 'sky'
        ground_goal = kind == 'ground'
        back = _goal_key(goal) in getattr(self, '_back_goals', ())     # (gathering, to be taken back from)
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
            sky = self._surface(lo[0], lo[2], hi[0] - lo[0] + 1, hi[2] - lo[2] + 1)
            if sky is not None:
                tops = [sky(xx, zz) for xx in range(lo[0], hi[0] + 1, 2) for zz in range(lo[2], hi[2] + 1, 2)]
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
        # (the same box asked for again within GRID_REUSE_S: the one got then; each is read on the game's own
        # thread, and plans one after another from one spot made the game lag, 2026-10-17)
        last = getattr(self, '_route_grid', None)
        if last is not None and last[1] == (tuple(lo), tuple(size)) and time.monotonic() - last[0] < GRID_REUSE_S:
            got = last[2]
        else:
            got = self._ask({'op': 'grid', 'min': lo, 'size': size}, timeout=4.0)
            if 'error' in got:
                return None
            self._route_grid = (time.monotonic(), (tuple(lo), tuple(size)), got)
        grid = blocks.Grid.from_bridge(got)
        cell = lambda p: (math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2]))
        pl = self.raw().get('player') or {}
        have = self._building(pl)[1]
        # as the order said: without putting blocks down, without digging ("jump onto it without placing any
        # blocks": the walk put down two, 2026-10-16)
        limits = getattr(self, '_limits', {}).get(_goal_key(goal)) or {}
        if limits.get('place_blocks') is False:
            have = 0
        can_break = self.can_break and limits.get('dig') is not False
        now = time.monotonic()
        self._no_place = {c: t for c, t in getattr(self, '_no_place', {}).items() if now - t < NO_PLACE_S}
        self._no_jump = {c: t for c, t in getattr(self, '_no_jump', {}).items() if now - t < NO_PLACE_S}
        # (running needs food over 6: a jump at a run isn't planned without it)
        sprint = pl.get('food') is None or pl.get('food') > SPRINT_FOOD
        path, short_note = pathing.run('plan_route', grid, cell(s), block if block else cell(aim), have,
                                             no_jump=frozenset(self._no_jump), can_sprint=sprint,
                                             budget_s=budget_s, can_break=can_break, kind=kind, box=box,
                                             no_place=frozenset(self._no_place), sky=sky, ground=ground, back=back,
                                             keep=frozenset(getattr(self, 'keep_blocks', None) or ()),
                                             foes=self._foe_costs(lo, size, g),
                                             jump_extra=pathing.FEWEST_JUMPS if limits.get('fewest_jumps') else 0.0)
        # (short of blocks for the cheapest way: the way that digs, or "needs blocks": blocks.plan_route)
        self.route_note = {'goal_kind': 'sky' if sky_goal else kind, 'box': [lo, size], **short_note}
        self.__dict__.setdefault('_route_last', {})[_goal_key(goal)] = (tuple(s), short_note.get('found'),
                                                                        short_note.get('cells') or 0, budget_s)
        if path is None and short_note.get('found') == 'needs blocks':
            return None
        no_blocks = limits.get('place_blocks') is False
        if (path is None or not (path.reaches_goal or path.timed_out)) and have < 8 and not sky_goal \
                and not no_blocks:
            # with blocks to build with, would there be a way? (then the order can get some first: dirt) (only
            # when there's none: not on a long way out of time, where it doubled each plan's time, 3 s, the
            # game lagging with plans one after another, 2026-10-17)
            try:
                alt = pathing.run('find', grid, cell(s), block if block else cell(aim), budget_s=1.5,
                                  can_break=self.can_break, blocks=64, kind=kind, box=box,
                                  no_place=frozenset(self._no_place), ground=ground)
            except Exception:
                alt = None
            if alt is not None and alt.reaches_goal:
                need = sum(1 for m in alt.moves if m.place is not None)
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
        route.moves, route.grid = list(path.moves), grid      # (done move by move: moving.Mover)
        route.cost = path.cost
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
              'shovel': ['wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite'],
              'hoe': ['wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite']}


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
        # what the world gives besides mining (the game's own loot tables, .local/minecraft: Get-GameData.bat
        # minecraft): creatures killed, and what's got by using an item on a creature or a block (shears on a
        # sheep, the hand on a berry bush), the item by the table's verb
        from . import data
        known = set(got.get('sources') or {})
        for item, drops in data.GameData().drops().items():
            for d in drops:
                if d.per < MIN_PER:
                    continue
                if d.verb == 'kill':
                    if d.on == 'creature' and d.target not in NO_HUNT:
                        self._sources.setdefault(item, []).append(
                            Source(d.target, (item,), None, on='creature', how='kill', per=d.per, variant=d.variant))
                    continue
                if d.verb not in USE_WITH:
                    continue                            # (a charged creeper's blast: not a way)
                targets = [d.target] if d.on == 'creature' else \
                    [b for b in known if b == d.target or b.startswith(d.target + '_') or b == d.target + 's']
                for t in targets or [d.target]:
                    self._sources.setdefault(item, []).append(
                        Source(t, (item,), USE_WITH[d.verb] or None, on=d.on, how='use', per=d.per,
                               variant=d.variant))
        for w in data.learned():                        # (and what the doer learned from the wiki)
            item, target = _id(w['item']), _id(w['target'])
            self._sources.setdefault(item, []).append(
                Source(target, (item,), _id(w['using']) if w.get('using') else None,
                       on='creature' if w.get('on') == 'creature' else 'block', how='use',
                       per=float(w.get('per') or 1.0)))
        self._near: dict = {}
        self._spots: dict = {}             # block -> (when, the nearest few spots)

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

    within = None     # blocks: only what's this near counts as near (stuck: nothing that needs the way out)

    def nearby(self, blocks_):
        me = (self.a.raw().get('player') or {}).get('pos') or [0, 0, 0]
        ds = [math.dist(me, c) for b in blocks_ for c in self._where(b)]
        d = min(ds) if ds else None
        return d if self.within is None or (d is not None and d <= self.within) else None

    def prefetch(self, item: str) -> None:
        """Where the blocks are that getting item may take (its recipes' ingredients' sources, all the way down):
        found in one search, not one each (a bed's every wool and dye, each searched on its own, froze the
        game, 2026-10-16). (An older bridge: one each, as before.)"""
        if getattr(self.a, '_no_find_each', False):
            return
        # (the stations used and, once anything's smelted, every fuel too: shears' iron weighed the fuels, signs and
        # banners among them, and each was searched on its own: 56 searches, 6 s, 2026-10-16)
        blocks_, todo, seen, fuels = {self.station}, [item], set(), False
        while todo and len(seen) < PREFETCH_ITEMS:
            it = todo.pop()
            if it in seen:
                continue
            seen.add(it)
            for src in self.sources(it):
                if not src.creature:
                    blocks_.add(src.block)
                if src.tool:
                    todo.append(src.tool)
            for rec in self.recipes(it):
                todo.extend(f for fits, _n in rec.needs() for f in fits)
                if getattr(rec, 'station', None):
                    blocks_.add(rec.station)
                    if not fuels:
                        fuels = True
                        todo.extend(self.fuels())
        now = time.monotonic()
        ask = sorted(b for b in blocks_ if not (b in self._spots and now - self._spots[b][0] < SPOTS_FOR))
        if not ask:
            return
        got = self.a._ask({'op': 'find_each', 'ids': ask, 'max': SPOTS}, timeout=10.0)
        if 'error' in got and 'found' not in got:
            self.a._no_find_each = 'unknown' in str(got.get('error')).lower()
            return
        found = got.get('found') or {}
        for b in ask:
            self._spots[b] = (now, [tuple(int(v) for v in c[:3]) for c in found.get(b) or []])

    def fuels(self) -> dict:
        # (never a tool or a weapon: the player's own wooden sword was burnt for shears' iron, and another made to
        # burn too, when two planks did it; a sword burns for one item, the planks it's made of for three,
        # 2026-10-17)
        return {k: v for k, v in self._fuels.items() if not _NOT_FUEL.search(k.split(':')[-1])}

    # costs in seconds from where the player is (core/crafting.py: a station there against one made here)

    def _where(self, block: str) -> list:
        """Where the nearest few of a block are (the game's coordinates), asked again after a while."""
        hit = self._spots.get(block)
        if hit is not None and time.monotonic() - hit[0] < SPOTS_FOR:
            return hit[1]
        got = self.a._ask({'op': 'find_blocks', 'ids': [block], 'max': SPOTS}, timeout=4.0)
        spots = [tuple(int(v) for v in f[:3]) for f in got.get('found') or []]
        self._spots[block] = (time.monotonic(), spots)
        return spots

    def _usable(self, block: str) -> list:
        """The spots of a block not lately found out of reach from here, nearest first (and only those in reach
        from here, stuck: self.within)."""
        me = (self.a.raw().get('player') or {}).get('pos') or [0, 0, 0]
        mem = getattr(self.a, 'memory', None)
        out = [c for c in self._where(block) if (mem is None or not mem.is_unreachable(c, me))
               and (self.within is None or math.dist(c, me) <= self.within)]
        return sorted(out, key=lambda c: math.dist(c, me))

    def _travel(self, c) -> float:
        """Seconds to get within reach of c, reckoned: across, and each block up (a step dug or a block put
        down) or down."""
        me = (self.a.raw().get('player') or {}).get('pos') or [0, 0, 0]
        flat = math.hypot(c[0] + 0.5 - me[0], c[2] + 0.5 - me[2])
        dy = c[1] - me[1]
        return max(0.0, flat - 3.0) / 4.3 + max(0.0, dy - 2.0) * CLIMB_S + max(0.0, -dy - 3.0) * DROP_S

    def gather_cost(self, src, n):
        each = HUNT_S if src.how == 'kill' else USE_S if src.how == 'use' else MINE_S
        times = math.ceil(n / max(0.1, src.per))
        if src.creature:
            # each one it takes, of those about: the walk out to the farthest of them; each one more than are
            # about, found further off (12 string from spiders was planned with one spider about, over 3 sheep:
            # "get 12 string, 111 s", 2026-10-16)
            about = len(self.a.creatures_near(src.block, dict(src.variant)))
            near = self.a.creatures_near(src.block, dict(src.variant), want=times)
            if not near:
                return None
            unsure = (len(near) - about) * FAR_KIND_S if src.variant else 0.0
            return self._travel(_mirror(near[min(times, len(near)) - 1][0])) + times * each + unsure \
                + max(0, times - len(near)) * MORE_CREATURES_S
        spots = self._usable(src.block)
        if not spots:
            return None
        return min(self._travel(c) + self._danger(c) for c in spots[:3]) + times * each

    def _danger(self, c) -> float:
        """Seconds a spot counts as dearer for the hostile creatures near it (the crafting table by the pit of
        zombies was gone back to, and the player was killed there, 2026-10-16)."""
        foes = [e['pos'] for e in self.a.raw().get('entities') or [] if e.get('hostile') and not e.get('dead')
                and e.get('pos')]
        return FOE_NEAR_S * sum(1 for f in foes if math.dist(f, (c[0] + 0.5, c[1], c[2] + 0.5)) < FOE_NEAR_R)

    def station_cost(self, item):
        spots = self._usable(item)
        return min(self._travel(c) + self._danger(c) for c in spots[:3]) if spots else None

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


def _water_flags(near, pos, eye) -> dict:
    """{'in_water': the feet or the eyes in water, 'head_under': the eyes in it} from the blocks round the feet
    the bridge sends each frame (the walk keeps afloat by them: core/autowalk.py); {} without them."""
    if not near or not near.get('data') or not pos:
        return {}
    import base64
    g = blocks.Grid(tuple(int(v) for v in near['min']), tuple(int(v) for v in near['size']),
                    base64.b64decode(near['data']))
    feet = g.at(math.floor(pos[0]), math.floor(pos[1] + 0.01), math.floor(pos[2])) == blocks.WATER
    e = eye or (pos[0], pos[1] + 1.62, pos[2])
    head = g.at(math.floor(e[0]), math.floor(e[1]), math.floor(e[2])) == blocks.WATER
    return {'in_water': feet or head, 'head_under': head}


# what burns but is never burnt: tools, weapons and the like (_Book.fuels)
_NOT_FUEL = re.compile(r"(_sword|_pickaxe|_axe|_shovel|_hoe|^bow|^crossbow|^fishing_rod|^shield|_spear)$")

# the land itself, under the trees ("take me to the ground" from a tree top was "already on the surface",
# 2026-10-13)
_DEATH = re.compile(r"\b(where (?:i|you|we) died|(?:my|our|the) death(?: point| spot| place)?|where (?:i|we) "
                    r"(?:fell|was killed|got killed)|(?:my|our) (?:stuff|things|items) (?:i|we) dropped)\b", re.I)
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
