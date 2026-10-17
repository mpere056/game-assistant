"""The Minecraft adapter against a stand-in bridge (the same one-JSON-line protocol on a local port)."""
import json
import math
import socket
import threading
import unittest

try:
    from mc_fakes import fake_sight
except ModuleNotFoundError:                 # (run as tests.<module>)
    from tests.mc_fakes import fake_sight

from game_assistant.core.interface import Entity, GameNotRunning, dot
from game_assistant.games.minecraft.adapter import MinecraftAdapter


def _world(**over):
    """A frame as the bridge sends it: the player at the origin looking south (+Z); a zombie 5 blocks south
    and 3 blocks east (+X), so seen from the player it is ahead and to the LEFT (facing south, east is left)."""
    d = {'v': 1, 'frame': 7, 'singleplayer': True, 'in_world': True, 'menu': False, 'paused': False,
         'dimension': 'minecraft:overworld',
         'window': {'x': 100, 'y': 50, 'w': 1280, 'h': 720, 'sw': 1280, 'sh': 720, 'focused': True},
         'player': {'pos': [0.0, 64.0, 0.0], 'eye': [0.0, 65.62, 0.0], 'yaw': 0.0, 'pitch': 0.0, 'health': 18.0,
                    'max_health': 20.0, 'food': 17, 'armor': 0, 'air': 300, 'dead': False, 'creative': False,
                    'spectator': False},
         'camera': {'pos': [0.0, 65.62, 0.0], 'fwd': [0.0, 0.0, 1.0], 'up': [0.0, 1.0, 0.0], 'left': [1.0, 0.0, 0.0],
                    'fov': 70.0, 'yaw': 0.0, 'pitch': 0.0},
         'target': {'kind': 'block', 'pos': [0, 63, 2], 'face': 'up', 'block': 'minecraft:oak_log',
                    'name': 'Oak Log', 'hit': [0.5, 64.0, 2.5]},
         'entities': [{'id': 42, 'type': 'minecraft:zombie', 'name': 'Zombie', 'pos': [3.0, 64.0, 5.0], 'w': 0.6,
                       'h': 1.95, 'yaw': 180.0, 'look': [0.0, 0.0, -1.0], 'hostile': True, 'living': True,
                       'hp': 20.0, 'max_hp': 20.0, 'hurt': 0, 'dead': False}]}
    d.update(over)
    return d


class FakeBridge:
    def __init__(self, world):
        self.world = world
        self.rays = []
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._talk, args=(c,), daemon=True).start()

    def _talk(self, c):
        f = c.makefile('rb')
        for line in f:
            req = json.loads(line)
            if req['op'] == 'snapshot':
                out = self.world
            elif req['op'] == 'raycast':
                self.rays.append(req['rays'])
                # everything hits a wall at z = 2 (its face toward -Z)
                hits = []
                for x1, y1, z1, x2, y2, z2 in req['rays']:
                    if z2 > 2 > z1:
                        k = (2 - z1) / (z2 - z1)
                        hits.append([1, x1 + (x2 - x1) * k, y1 + (y2 - y1) * k, 2.0, 0, 0, -1, 'minecraft:stone'])
                    else:
                        hits.append([0])
                out = {'hits': hits}
            else:
                out = {'error': 'unknown'}
            c.sendall((json.dumps(out) + '\n').encode())

    def close(self):
        self.sock.close()


class MinecraftAdapterTests(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge(_world())
        self.a = MinecraftAdapter(port=self.bridge.port)

    def tearDown(self):
        self.a.close()
        self.bridge.close()

    def test_a_creature_east_of_a_player_facing_south_is_on_the_left(self):
        s = self.a.snapshot()
        self.assertTrue(s.in_world)
        z = s.entities[0]
        rel = tuple(z.center[i] - s.camera.pos[i] for i in range(3))
        self.assertGreater(dot(rel, s.camera.forward), 0)        # ahead
        self.assertLess(dot(rel, s.camera.right), 0)             # and to the left, as in the game
        # the core's headings: clockwise seen from above is to the right; atan2(x, z) of the zombie is negative
        self.assertLess(math.degrees(math.atan2(rel[0], rel[2])), 0)

    def test_player_camera_window_and_creature(self):
        s = self.a.snapshot()
        self.assertEqual(s.player.health, 18.0)
        self.assertAlmostEqual(s.camera.fov_y_deg, 70.0)
        self.assertAlmostEqual(s.camera.aspect, 1280 / 720)
        self.assertEqual((s.screen.x, s.screen.y, s.screen.width, s.screen.height, s.screen.focused),
                         (100, 50, 1280, 720, True))
        z = s.entities[0]
        self.assertEqual((z.id, z.model, z.name, z.hostile, z.health), (42, 'minecraft:zombie', 'Zombie', True, 20.0))
        self.assertAlmostEqual(z.radius, 0.3)
        self.assertAlmostEqual(z.center[1], 64.0 + 1.95 / 2)
        facts = self.a.knowledge(z.type_id)
        self.assertEqual((facts['name'], facts['minecraft_id'], facts['max_hearts']), ('Zombie', 'minecraft:zombie', 10.0))
        self.assertFalse(s.menu)

    def test_the_crosshair_block_and_creature(self):
        self.assertEqual(self.a.crosshair_facts()['block_at_crosshair'], 'Oak Log')
        self.bridge.world = _world(target={'kind': 'entity', 'id': 42, 'hit': [3.0, 65.0, 5.0]})
        self.a._cache = None
        self.assertEqual(self.a.snapshot().target_id, 42)
        self.assertIsNone(self.a.crosshair_facts())

    def test_rays_go_in_and_come_back_in_the_same_frame(self):
        s = self.a.snapshot()
        start = s.camera.pos
        hits = self.a.raycast([(start, (start[0] + 1.0, start[1], start[2] + 5.0)),
                               (start, (start[0], start[1], start[2] - 5.0))])
        self.assertTrue(hits[0].hit and not hits[1].hit)
        self.assertAlmostEqual(hits[0].pos[2], 2.0)
        self.assertAlmostEqual(hits[0].pos[0], 0.4, places=5)   # +1 in ours is -1 in the game's x: back again
        self.assertEqual(self.bridge.rays[0][0][3], -1.0)        # (sent mirrored)
        self.assertEqual(hits[0].normal, (0.0, 0.0, -1.0))

    def test_menus_and_servers(self):
        self.bridge.world = _world(menu=True)
        self.a._cache = None
        self.assertTrue(self.a.snapshot().menu)
        self.bridge.world = {'v': 1, 'frame': 9, 'singleplayer': False, 'in_world': False,
                             'window': {'x': 0, 'y': 0, 'w': 1280, 'h': 720, 'focused': True}}
        self.a._cache = None
        self.assertFalse(self.a.snapshot().in_world)            # on a server: nothing (single-player only)

    def test_no_bridge(self):
        self.bridge.close()
        a = MinecraftAdapter(port=1)
        with self.assertRaises(GameNotRunning):
            a.snapshot()


class ToolFirstTests(unittest.TestCase):
    def advice(self, now, wooden, have, slow=5):
        import sys
        sys.path.insert(0, 'tests')
        from test_crafting import Book
        from game_assistant.games.minecraft.adapter import MinecraftAdapter, _goal_key
        a = MinecraftAdapter()
        goal = (1.0, 2.0, 3.0)
        a._slow_breaks[_goal_key(goal)] = slow
        a.dig_estimate = lambda g: {'seconds_with_what_is_held': now, 'mostly': ['stone'],
                                    'seconds_with': {'wooden_pickaxe': wooden}}
        a.raw = lambda: {'player': {'inventory': have}}
        book = Book(near=('mc:oak_log',))
        book.book['minecraft:wooden_pickaxe'] = book.book['mc:wooden_pickaxe']
        a.book = lambda: book
        return a.tool_advice(goal)

    def test_a_pickaxe_first_for_a_long_dig_through_stone(self):
        got = self.advice(330, 45, {})
        self.assertEqual(got[0], 'minecraft:wooden_pickaxe')
        self.assertGreater(got[1], 100)
        self.assertEqual(got[2][-1][1].result, 'mc:wooden_pickaxe')

    def test_not_for_a_short_dig_or_with_one_held_or_few_hard_blocks(self):
        self.assertIsNone(self.advice(40, 8, {}))                      # the pickaxe wouldn't pay for itself
        self.assertIsNone(self.advice(330, 45, {'minecraft:wooden_pickaxe': 1}))
        self.assertIsNone(self.advice(330, 45, {}, slow=1))            # the route hardly digs


class SurveyTests(unittest.TestCase):
    def test_finds_the_drop_and_the_pool_at_its_bottom(self):
        import base64
        from game_assistant.games.minecraft import blocks as B
        from game_assistant.games.minecraft.adapter import MinecraftAdapter
        a = MinecraftAdapter()
        a.raw = lambda: {'player': {'pos': [0.5, 30.0, 0.5]}}

        def ask(req, timeout=None):
            if req['op'] == 'heights':                   # the land: the ledge's top (x < 3), the cave floor
                (x0, z0), (sx, sz) = req['min'], req['size']
                return {'ground': req.get('ground'), 'min': req['min'], 'size': req['size'],
                        'heights': [30 if x0 + xx < 3 else 8 for zz in range(sz) for xx in range(sx)]}
            (x0, y0, z0), (sx, sy, sz) = req['min'], req['size']
            data = bytearray(sx * sy * sz)
            for yy in range(sy):
                for zz in range(sz):
                    for xx in range(sx):
                        X, Y, Z = x0 + xx, y0 + yy, z0 + zz
                        k = B.AIR
                        if X < 3 and Y < 30:
                            k = B.SOLID                      # the ledge the player stands on (x < 3)
                        elif X >= 3 and Y < 8:
                            k = B.SOLID                      # the cave floor, 22 down...
                        if 5 <= X <= 7 and -1 <= Z <= 1 and 8 <= Y <= 10:
                            k = B.WATER                      # ...with a pool 3 deep
                        data[(yy * sz + zz) * sx + xx] = k
            return {'min': req['min'], 'size': req['size'], 'data': base64.b64encode(bytes(data)).decode()}
        a._ask = ask
        a._block_ids = lambda: []                        # (no trees in this one)
        got = a.survey()
        east = next(e for e in got['edges_and_walls'] if e['direction'] == 'east')
        self.assertEqual(east['drop_after_blocks'], 3)
        self.assertEqual(east['bottom'], 'ground')
        pool = got['water_below'][0]
        self.assertEqual((pool['centre']['x'], pool['deepest']), (6, 3))
        self.assertEqual(got['land']['you_are'], 'on the ground')
        self.assertEqual(got['land']['your_ground_height'], 30)


class PlaceSpotTests(unittest.TestCase):
    def test_a_spot_from_a_dip_with_a_sheep_beside(self):
        # (stood in a one-block dip on plains, animals about: "I can't find a spot to put the crafting table",
        # twice, 2026-10-13): on top of the ground beside it, not where the sheep stands
        import base64
        import math
        from game_assistant.core.interface import RayHit
        from game_assistant.games.minecraft import blocks as B
        from game_assistant.games.minecraft.adapter import MinecraftAdapter, _mirror
        solid = lambda c: c[1] <= 61 and c != (0, 61, 0)               # the ground's top at 62; the dip
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        a.raw = lambda: {'player': {'pos': [0.5, 61.0, 0.5]}, 'camera': {'pos': [0.5, 62.62, 0.5]},
                         'entities': [{'pos': [1.5, 62.0, 0.5], 'w': 0.9, 'h': 1.3, 'living': True}]}

        def ask(req, timeout=None):
            (x0, y0, z0), (sx, sy, sz) = req['min'], req['size']
            data = bytes(B.SOLID if solid((x0 + i, y0 + j, z0 + k)) else B.AIR
                         for j in range(sy) for k in range(sz) for i in range(sx))
            return {'min': req['min'], 'size': req['size'], 'data': base64.b64encode(data).decode()}

        a._ask, a._sight = ask, fake_sight(solid)
        spot = a.place_spot()
        self.assertIsNotNone(spot)
        _aim, against, face, into = spot
        self.assertEqual(face, 'up')
        self.assertEqual(into[1], 62)                                   # on top of the ground
        self.assertNotEqual(into, (1, 62, 0))                           # not where the sheep is
        self.assertTrue(solid(against))

    def test_a_spot_in_grass_seen_as_the_crosshair_sees_it(self):
        # (grass on every block round the player: the crosshair, crouched, stops on it; the table never went
        # down, 2026-10-13): a spot whose grass the table takes the place of, or a face that's really in sight
        import base64
        from game_assistant.games.minecraft import blocks as B
        from game_assistant.games.minecraft.adapter import MinecraftAdapter
        solid = lambda c: c[1] <= 61
        grass = {(x, 62, z): True for x in range(-4, 5) for z in range(-4, 5) if (x, z) != (0, 0)}
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        a.raw = lambda: {'player': {'pos': [0.5, 62.0, 0.5]}, 'camera': {'pos': [0.5, 63.62, 0.5]}, 'entities': []}

        def ask(req, timeout=None):
            (x0, y0, z0), (sx, sy, sz) = req['min'], req['size']
            data = bytes(B.SOLID if solid((x0 + i, y0 + j, z0 + k)) else B.AIR
                         for j in range(sy) for k in range(sz) for i in range(sx))
            return {'min': req['min'], 'size': req['size'], 'data': base64.b64encode(data).decode()}
        a._ask, a._sight = ask, fake_sight(solid, grass)
        spot = a.place_spot()
        self.assertIsNotNone(spot)
        _aim, against, face, into = spot
        self.assertEqual((face, into[1]), ('up', 62))
        sight = fake_sight(solid, grass)([((0.5, 62 + 1.27, 0.5), _aim and (-_aim[0], _aim[1] - 0.01, _aim[2]))])[0]
        self.assertIn(sight['block'], (into, against))       # the crosshair lands on the spot's grass or face


class BuildingBlockTests(unittest.TestCase):
    def test_built_with_only_what_the_task_doesnt_need(self):
        from game_assistant.games.minecraft.adapter import MinecraftAdapter
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        pl = {'hotbar': [['minecraft:cobblestone', 8], ['minecraft:oak_planks', 3], ['', 0]],
              'inventory': {'minecraft:cobblestone': 8, 'minecraft:oak_planks': 3}, 'blocks': 11, 'block_slot': 0}
        self.assertEqual(a._building(pl), (0, 11))
        a.reserve = {'minecraft:cobblestone': 8}                       # a furnace to come: not built with
        self.assertEqual(a._building(pl), (1, 3))
        a.reserve = {'minecraft:cobblestone': 8, 'minecraft:oak_planks': 3}
        self.assertEqual(a._building(pl), (-1, 0))
        pl = {'hotbar': [['minecraft:cobblestone', 20]], 'inventory': {'minecraft:cobblestone': 20}, 'blocks': 20}
        a.reserve = {'minecraft:cobblestone': 8}
        self.assertEqual(a._building(pl), (0, 12))                     # what's over is


class QuickestBlockTests(unittest.TestCase):
    def test_the_quickest_there_and_back_and_never_a_fall_into_a_cave(self):
        # (each stone the nearest: dug straight down 16 blocks from the crafting table, the last 6 a fall into a
        # cave, then all the way back up for the furnace, 2026-10-16)
        import base64
        from game_assistant.games.minecraft import blocks as B
        a = MinecraftAdapter()
        a.raw = lambda: {'player': {'pos': [0.5, 70.0, 0.5]}}
        under, deep, beside = (0, 69, 0), (1, 64, 0), (4, 70, 2)

        def ask(req, timeout=None):
            if req['op'] == 'find_blocks':               # the nearest first, as the bridge gives them
                return {'found': [[*under, 'minecraft:stone'], [*deep, 'minecraft:stone'],
                                  [*beside, 'minecraft:stone']]}
            (x0, y0, z0), (sx, sy, sz) = req['min'], req['size']
            # under the stone stood on: a cave (air from 68 down to 60)
            data = bytes(B.AIR if 60 <= y0 + yy <= 68 else B.SOLID for yy in range(sy))
            return {'min': req['min'], 'size': req['size'], 'data': base64.b64encode(data).decode()}
        a._ask = ask
        got = a.locate_blocks(['minecraft:stone'], to_mine=True, near=(0.5, 70.0, 0.5))
        cells = [tuple(r['coordinates'][k] for k in 'xyz') for r in got['results']]
        self.assertNotIn(under, cells)                  # (a fall of 9 into the cave)
        self.assertEqual(cells[0], beside)              # on the player's level: no climb back
        self.assertEqual(cells[-1], deep)


class GameDataTests(unittest.TestCase):
    def test_interactions_from_the_loot_tables(self):
        # (shearing was in the game's data, and not planned with, 2026-10-16): every interaction's table read the
        # same way, its verb's item from the adapter's few words
        import json
        import pathlib
        import tempfile
        from game_assistant.games.minecraft import data
        root = pathlib.Path(tempfile.mkdtemp())
        lt = root / '26.3' / 'data' / 'minecraft' / 'loot_table'
        tables = {
            'entities/sheep': {'type': 'minecraft:entity', 'pools': [{'rolls': 1, 'entries': [
                {'type': 'minecraft:loot_table', 'value': 'minecraft:entities/sheep/white', 'condition': {
                    'type': 'minecraft:entity_properties', 'entity': 'this', 'predicate': {
                        'minecraft:components': {'minecraft:sheep/color': 'white'}}}}]}]},
            'entities/sheep/white': {'type': 'minecraft:entity', 'pools': [{'rolls': 1, 'entries': [
                {'type': 'minecraft:item', 'name': 'minecraft:white_wool'}]}]},
            'shearing/sheep': {'type': 'minecraft:shearing', 'pools': [{'rolls': {'min': 1, 'max': 3}, 'entries': [
                {'type': 'minecraft:item', 'name': 'minecraft:white_wool'}]}]},
            'entities/zombie': {'type': 'minecraft:entity', 'pools': [{'rolls': 1, 'condition': {
                'type': 'minecraft:all_of', 'terms': [{'type': 'minecraft:killed_by_player'}, {
                    'type': 'minecraft:random_chance_with_enchanted_bonus', 'unenchanted_chance': 0.025}]},
                'entries': [{'type': 'minecraft:item', 'name': 'minecraft:iron_ingot'},
                            {'type': 'minecraft:item', 'name': 'minecraft:carrot'}]}]},
            'harvest/sweet_berry_bush': {'type': 'minecraft:block_interact', 'pools': [{'rolls': 1, 'entries': [
                {'type': 'minecraft:item', 'name': 'minecraft:sweet_berries'}]}]}}
        for name, t in tables.items():
            p = lt / f'{name}.json'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(t))
        saved = data.DIR
        data.DIR = root
        try:
            d = data.GameData().drops()
        finally:
            data.DIR = saved
        wool = {(x.verb, x.per) for x in d['minecraft:white_wool']}
        self.assertEqual(wool, {('kill', 1.0), ('shearing', 2.0)})
        self.assertEqual(d['minecraft:white_wool'][0].variant, (('color', 'white'),))
        self.assertAlmostEqual(d['minecraft:iron_ingot'][0].per, 0.0125, places=3)   # (one of two, 2.5 % of kills)
        berries = d['minecraft:sweet_berries'][0]
        self.assertEqual((berries.on, berries.verb, berries.target), ('block', 'harvest', 'minecraft:sweet_berry_bush'))


class FightGearTests(unittest.TestCase):
    def test_a_weapon_bare_handed_and_a_shield_against_a_shooter(self):
        a = MinecraftAdapter()
        skel = Entity(1, 1, 'minecraft:skeleton', (0.0, 0.0, 5.0), (0.0, 1.0, 5.0), 0.3, 1.99, True, False, 20.0,
                      20.0)
        zomb = Entity(2, 1, 'minecraft:zombie', (0.0, 0.0, 5.0), (0.0, 1.0, 5.0), 0.3, 1.95, True, False, 20.0, 20.0)
        need = a.fight_gear(skel, {'hotbar': [('minecraft:dirt', 9)]})
        self.assertEqual([w for _i, w in need], ['hand', 'offhand'])     # (a weapon first)
        self.assertIn('minecraft:wooden_sword', need[0][0])
        self.assertEqual(a.fight_gear(zomb, {'hotbar': [('minecraft:stone_sword', 1)]}), [])
        self.assertEqual(a.fight_gear(skel, {'hotbar': [('minecraft:iron_sword', 1)],
                                             'offhand': 'minecraft:shield'}), [])
        # (a pickaxe isn't armed: two spiders, dead, 2026-10-17; an axe is)
        self.assertEqual([w for _i, w in a.fight_gear(zomb, {'hotbar': [('minecraft:stone_pickaxe', 1)]})], ['hand'])
        self.assertEqual(a.fight_gear(zomb, {'hotbar': [('minecraft:stone_axe', 1)]}), [])


class QuickestByWayTests(unittest.TestCase):
    def test_the_block_whose_way_is_quickest_not_the_nearest(self):
        # (Baritone's mining: one search toward all of them; a tree 34 blocks off was taken past two closer ones,
        # 2026-10-16). One tree 5 off across a wall that can't be broken; one 9 off in the open.
        import base64
        from game_assistant.games.minecraft import blocks as B
        size = (24, 12, 24)
        data = bytearray(size[0] * size[1] * size[2])
        hard = bytearray(len(data))

        def put(x, y, z, k, h=127):
            i = (y * size[2] + z) * size[0] + x
            data[i], hard[i] = k, h
        for x in range(size[0]):
            for z in range(size[2]):
                put(x, 3, z, B.SOLID)                    # the ground: stand at 4
        for z in range(size[2]):
            for y in range(4, 11):
                put(8, y, z, B.SOLID)                    # a wall across, too high to get over, unbreakable
        behind, open_ = (10, 4, 5), (5, 4, 14)
        for c in (behind, open_):
            for y in range(4, 7):
                put(c[0], y, c[2], B.SOLID, 15)          # the trunks

        def ask(req, timeout=None):
            if req.get('op') == 'grid':
                lo, sz = req['min'], req['size']
                out, out_h = bytearray(sz[0] * sz[1] * sz[2]), bytearray(sz[0] * sz[1] * sz[2])
                n = 0
                for y in range(sz[1]):
                    for z in range(sz[2]):
                        for x in range(sz[0]):
                            gx, gy, gz = lo[0] + x, lo[1] + y, lo[2] + z
                            if 0 <= gx < size[0] and 0 <= gy < size[1] and 0 <= gz < size[2]:
                                k = (gy * size[2] + gz) * size[0] + gx
                                out[n], out_h[n] = data[k], hard[k]
                            else:
                                out[n], out_h[n] = (B.SOLID if gy < 4 else B.AIR), 127
                            n += 1
                return {'min': lo, 'size': sz, 'data': base64.b64encode(bytes(out)).decode(),
                        'hard': base64.b64encode(bytes(out_h)).decode()}
            return {'found': [[*behind, 'minecraft:oak_log'], [*open_, 'minecraft:oak_log']]}
        a = MinecraftAdapter()
        a.can_break = False
        a.raw = lambda: {'player': {'pos': [5.5, 4.0, 5.5]}, 'entities': []}
        a._ask = ask
        got = a.locate_blocks(['minecraft:oak_log'], to_mine=True)
        self.assertEqual(tuple(got['results'][0]['coordinates'][k] for k in 'xyz'), open_)


class WayCheckTests(unittest.TestCase):
    def test_could_you_jump_onto_that_without_placing_blocks(self):
        # ("could you jump onto that crafting table from here without placing blocks?": "yes, walk two blocks
        # closer", then "probably not": guessed, 2026-10-16). The planner on the ground there: a gap of two, the
        # table a block up, at a run.
        import base64
        from game_assistant.games.minecraft import blocks as B
        size = (20, 10, 12)
        data = bytearray(size[0] * size[1] * size[2])
        hard = bytearray(len(data))

        def put(x, y, z, k, h=127):
            i = (y * size[2] + z) * size[0] + x
            data[i], hard[i] = k, h
        for z in range(size[2]):
            for x in range(size[0]):
                if x not in (6, 7):
                    for y in range(0, 4):
                        put(x, y, z, B.SOLID)            # the ground (stand at 4), a gap two wide, 4 deep
        put(8, 4, 5, B.SOLID, 10)                        # the crafting table, beyond the gap

        def ask(req, timeout=None):
            if req.get('op') != 'grid':
                return {'error': 'not here'}
            lo, sz = req['min'], req['size']
            out, out_h = bytearray(sz[0] * sz[1] * sz[2]), bytearray(sz[0] * sz[1] * sz[2])
            n = 0
            for y in range(sz[1]):
                for z in range(sz[2]):
                    for x in range(sz[0]):
                        gx, gy, gz = lo[0] + x, lo[1] + y, lo[2] + z
                        if 0 <= gx < size[0] and 0 <= gy < size[1] and 0 <= gz < size[2]:
                            k = (gy * size[2] + gz) * size[0] + gx
                            out[n], out_h[n] = data[k], hard[k]
                        else:
                            out[n], out_h[n] = (B.SOLID if gy < 0 else B.AIR), 127
                        n += 1
            return {'min': lo, 'size': sz, 'data': base64.b64encode(bytes(out)).decode(),
                    'hard': base64.b64encode(bytes(out_h)).decode()}
        a = MinecraftAdapter()
        a.raw = lambda: {'player': {'pos': [3.5, 4.0, 5.5], 'hotbar': [['minecraft:cobblestone', 58]],
                                    'inventory': {'minecraft:cobblestone': 58}},
                         'entities': [], 'target': {'kind': 'block', 'pos': [8, 4, 5]}}
        a._ask = ask
        got = a.way_check('that', place_blocks=False, dig=False)
        self.assertTrue(got['can'], got)
        self.assertEqual(got['jumps'], [{'gap': 2, 'at_a_run': True, 'up_a_block': True}])
        self.assertEqual((got['blocks_put_down'], got['blocks_dug']), (0, 0))
        self.assertEqual(got['to'], {'x': 8, 'y': 5, 'z': 5})


class BedAndWoolTests(unittest.TestCase):
    """"Make a bed": a purple one was set about (dyes from flowers first), its wool from 12 string with one spider
    about, while sheep were 90 blocks off and unseen by the plan (2026-10-16)."""

    def adapter(self, entities, far=()):
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        a.raw = lambda: {'player': {'pos': [0.0, 64.0, 0.0]}, 'entities': entities}
        a._ask = lambda req, timeout=None: {'found': [list(f) for f in far]} if req.get('op') == 'find_entities' \
            else {'error': 'not here'}
        return a

    def test_a_bed_is_planned_white_first(self):
        colours = ('black', 'blue', 'brown', 'cyan', 'gray', 'green', 'light_blue', 'purple', 'red', 'white')
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        a.book = lambda: type('B', (), {'items': lambda self: {f'minecraft:{c}_bed' for c in colours}})()
        self.assertEqual(a.match_item('bed')[0], 'minecraft:white_bed')
        self.assertEqual(a.match_item('a red bed'), ['minecraft:red_bed'])

    def test_a_creature_cost_counts_how_many_are_about(self):
        from game_assistant.core.crafting import Source
        from game_assistant.games.minecraft.adapter import _Book
        spider = {'id': 1, 'type': 'minecraft:spider', 'pos': [10.0, 64.0, 0.0]}
        book = _Book.__new__(_Book)
        book.a = self.adapter([spider])
        string = Source('minecraft:spider', ('minecraft:string',), on='creature', how='kill', per=1.0)
        self.assertGreater(book.gather_cost(string, 12), 11 * 60)        # (one about: 11 more found further)
        self.assertLess(book.gather_cost(string, 1), 30)

    def test_creatures_beyond_sight_are_found(self):
        from game_assistant.core.crafting import Source
        from game_assistant.games.minecraft.adapter import _Book
        far = [(90.0, 64.0, 0.0, 'minecraft:sheep', 7), (95.0, 64.0, 3.0, 'minecraft:sheep', 8),
               (99.0, 64.0, 1.0, 'minecraft:sheep', 9)]
        a = self.adapter([], far)
        got = a.creature_near('minecraft:sheep', {'color': 'white'})
        self.assertEqual(got[2], 7)
        book = _Book.__new__(_Book)
        book.a = a
        wool = Source('minecraft:sheep', ('minecraft:white_wool',), on='creature', how='kill', per=1.0,
                      variant=(('color', 'white'),))
        cost = book.gather_cost(wool, 3)
        self.assertIsNotNone(cost)
        self.assertLess(cost, 150)


class EquipmentTests(unittest.TestCase):
    def test_the_best_of_each_part_and_a_shield(self):
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        inv = {'minecraft:leather_chestplate': 1, 'minecraft:iron_chestplate': 1, 'minecraft:golden_boots': 1,
               'minecraft:shield': 1, 'minecraft:dirt': 9}
        self.assertEqual(a.equipment(inv, ''), [('equip', 'minecraft:iron_chestplate'),
                                                 ('equip', 'minecraft:golden_boots'), ('offhand', 'minecraft:shield')])
        self.assertEqual(a.equipment(inv, 'minecraft:shield', ['minecraft:golden_boots']),
                         [('equip', 'minecraft:golden_boots')])


class BlocksToHandTests(unittest.TestCase):
    def test_blocks_held_not_in_the_hotbar_put_there(self):
        # (70 cobblestone in the pack, none in the hotbar: a ravine dug out of step by step, 2026-10-17)
        a = MinecraftAdapter.__new__(MinecraftAdapter)
        hot = [['minecraft:wooden_sword', 1], ['minecraft:saddle', 1]] + [['', 0]] * 7
        inv = {'minecraft:wooden_sword': 1, 'minecraft:saddle': 1, 'minecraft:cobblestone': 70,
               'minecraft:diorite': 47}
        a.raw = lambda: {'player': {'hotbar': hot, 'inventory': inv}}
        step = a.blocks_to_hand()
        self.assertEqual(step[0], 'arrange')
        self.assertIn('minecraft:cobblestone', step[1])
        hot2 = [['minecraft:cobblestone', 64]] + hot[1:]
        a.raw = lambda: {'player': {'hotbar': hot2, 'inventory': inv}}
        self.assertIsNone(a.blocks_to_hand())            # (some there already)


class WorkerPlanTests(unittest.TestCase):
    def test_a_plan_in_its_own_process_is_the_same(self):
        # (planned on a thread here, the frame loop ran at 115 ms a frame, 2026-10-17)
        from game_assistant.games.minecraft import pathing as P
        try:
            from test_minecraft_paths import grid, world
        except ModuleNotFoundError:
            from tests.test_minecraft_paths import grid, world
        d, s = world()
        g = grid(d, s)
        here = P.find(g, (2, 1, 2), (15, 1, 15), kind='exact')
        P.use_worker(True)
        try:
            there = P.run('find', g, (2, 1, 2), (15, 1, 15), kind='exact')
        finally:
            P.use_worker(False)
        self.assertEqual([m.dest for m in there.moves], [m.dest for m in here.moves])
