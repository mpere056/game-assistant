"""The Minecraft adapter against a stand-in bridge (the same one-JSON-line protocol on a local port)."""
import json
import math
import socket
import threading
import unittest

from game_assistant.core.interface import GameNotRunning, dot
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
