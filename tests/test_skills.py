"""The navigation skills, as Baritone's commands (docs/MOVEMENT.md): goals (x z, a height, within a radius), follow,
pickup, tunnel, waypoints."""
import os
import tempfile
import unittest
from types import SimpleNamespace as NS

from game_assistant.core.interface import Camera, Entity, Player, Screen, Snapshot
from game_assistant.core.tasks import TaskRunner
from game_assistant.core.walk_commands import CommandResolver
from game_assistant.core.waypoints import Waypoints
from game_assistant.games.minecraft import blocks as B
from game_assistant.games.minecraft import pathing as P

try:
    from test_minecraft_paths import grid, put, world
except ModuleNotFoundError:                 # (run as tests.<module>)
    from tests.test_minecraft_paths import grid, put, world


class GoalTests(unittest.TestCase):
    def test_a_height_anywhere_dug_down_to(self):
        # (Baritone's GoalYLevel: "go down to y 3")
        d, s = world(sy=12, floor_y=9)
        for z in range(20):
            for x in range(20):
                for y in range(0, 8):
                    put(d, s, x, y, z, B.SOLID)
        g = B.Grid((0, 0, 0), s, bytes(d), bytes(4 if k == B.SOLID else 0 for k in d))
        p = P.find(g, (5, 9, 5), (5, 3, 5), kind='level', can_break=True, budget_s=2.0)
        self.assertTrue(p.reaches_goal)
        self.assertEqual(p.moves[-1].dest[1], 3)

    def test_across_only_and_within_a_radius(self):
        d, s = world()
        g = grid(d, s)
        p = P.find(g, (2, 1, 2), (15, 1, 15), kind='column')
        self.assertTrue(p.reaches_goal)
        p = P.find(g, (2, 1, 2), (15, 1, 15), kind='within:6')
        self.assertTrue(p.reaches_goal)
        end = p.moves[-1].dest
        self.assertLessEqual((end[0] - 15) ** 2 + (end[2] - 15) ** 2, 36)
        self.assertGreater((end[0] - 15) ** 2 + (end[2] - 15) ** 2, 9)       # (stopped as soon as within)


class PickupTests(unittest.TestCase):
    def test_each_dropped_item_walked_onto_till_none_left(self):
        lying = [(1.0, 0.0, 5.0), (4.0, 0.0, 2.0)]
        walks = []

        class W:
            def task_items(self, items, radius):
                return list(lying)

            def task_walk(self, point, name, **kw):
                walks.append(point)

            def task_stop(self):
                pass

            def task_say(self, line):
                pass
        r = TaskRunner(W())
        r.start([('pickup', None, 12.0)], 'items', 1)
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, (), None,
                        screen=Screen(0, 0, 800, 600, True), extra={'inventory': {}})
        r.update(snap, 1 / 30, False)
        self.assertEqual(walks, [lying[0]])
        lying.pop(0)                                     # (picked up)
        r.on_event('arrived')
        r.update(snap, 1 / 30, False)
        self.assertEqual(walks[-1], lying[0])
        lying.pop(0)
        r.on_event('arrived')
        r.update(snap, 1 / 30, False)
        self.assertFalse(r.active)
        self.assertEqual(r.result, 'done')


class TunnelTests(unittest.TestCase):
    def test_a_tunnel_is_a_cleared_box_straight_on(self):
        built = []
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)

        class A:
            def snapshot(self):
                return Snapshot('t', 1, True, Player((-10.5, 64.0, 20.5), None, 'alive'), cam, (), None)

            def game_coords(self, p):
                return (-p[0], p[1], p[2])

            def cells_now(self, cells, items):
                return {}
        r = CommandResolver.__new__(CommandResolver)
        r.adapter = A()
        r.build = lambda parts: built.append(parts) or NS(kind='task', say='')
        got = r.typed('tunnel', {'direction': 'north', 'length': 10, 'width': 1, 'height': 2})
        self.assertEqual(got.kind, 'task')
        part = built[0][0]
        self.assertEqual(part['shape'], 'clear')
        self.assertEqual((part['from'], part['to']), ([10, 64, 19], [10, 65, 10]))
        r.typed('tunnel', {'length': 5})                  # (no direction: the way the camera faces: north)
        self.assertEqual((built[1][0]['from'], built[1][0]['to']), ([10, 64, 19], [10, 65, 15]))


class FollowTests(unittest.TestCase):
    def test_kept_after_and_lost_when_gone(self):
        import time
        from game_assistant.app import App
        app = App.__new__(App)
        app._took_over = 0.0
        app.lines, app.walks = [], []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        app._walk_request = lambda *a: app.walks.append(a)
        app._follow = (7, 'Cow', 3.0)
        cow = Entity(7, 1, 'minecraft:cow', (0.0, 0.0, 3.5), (0.0, 0.7, 3.5), 0.45, 1.4, False, False, 10.0, 10.0)
        snap = lambda *es: Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, es, None)
        app._follow_tick(snap(cow))
        self.assertEqual(app.walks, [])                  # (near enough)
        far = Entity(7, 1, 'minecraft:cow', (0.0, 0.0, 9.0), (0.0, 0.7, 9.0), 0.45, 1.4, False, False, 10.0, 10.0)
        app._follow_tick(snap(far))
        self.assertEqual(app.walks[-1][0], 'start')       # (after it)
        app._follow_tick(snap())
        self.assertIsNone(app._follow)                    # (gone)
        self.assertIn('lost', app.lines[-1])


class WaypointTests(unittest.TestCase):
    def test_set_get_list_delete_kept_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'wp.json')
            w = Waypoints(path)
            w.set('Home', (10.2, 64.0, -5.7), 'minecraft:overworld')
            again = Waypoints(path)                       # (a new session)
            self.assertEqual(again.get('home'), {**again.get('home'), 'x': 10, 'y': 64, 'z': -6})
            self.assertEqual(again.get('hom')['name'], 'Home')    # (near enough a name)
            self.assertEqual(len(again.all()), 1)
            self.assertTrue(again.delete('home'))
            self.assertIsNone(Waypoints(path).get('home'))


class PartialGoToTests(unittest.TestCase):
    def test_go_to_a_height_or_across_only(self):
        asked = []

        class A:
            def goal_point(self, x=None, y=None, z=None, within=None):
                asked.append((x, y, z, within))
                return (0.0, y or 0.0, 0.0)

            def snapshot(self):
                return None
        r = CommandResolver.__new__(CommandResolver)
        r.adapter = A()
        got = r.typed('go_to', {'y': 12})
        self.assertEqual(got.kind, 'walk')
        self.assertEqual(asked[-1], (None, 12.0, None, None))
        r.typed('go_to', {'x': 100, 'z': -40})
        self.assertEqual(asked[-1], (100.0, None, -40.0, None))
        r.typed('go_to', {'x': 1, 'y': 2, 'z': 3, 'within': 5})
        self.assertEqual(asked[-1], (1.0, 2.0, 3.0, 5.0))


class OntoTests(unittest.TestCase):
    def test_onto_a_block_is_its_top_not_beside_it(self):
        # ("jump onto that crafting table": checked onto its top, then walked to beside it, 2026-10-16)
        asked, limits = [], []

        class A:
            def top_of(self, what):
                asked.append(what)
                return (332, 81, -423)

            def coords_point(self, x, y, z):
                return (float(x), float(y), float(z))

            def walk_limits(self, point, place, dig):
                limits.append((point, place, dig))
        r = CommandResolver.__new__(CommandResolver)
        r.adapter = A()
        got = r.typed('go_to', {'place': 'crafting table', 'onto': True, 'place_blocks': False})
        self.assertEqual((got.kind, got.point), ('walk', (332.0, 81.0, -423.0)))
        self.assertEqual(asked[-1], 'crafting table')
        self.assertEqual(limits[-1], ((332.0, 81.0, -423.0), False, True))
        r.typed('go_to', {'place': 'onto that'})          # (said in the place's words)
        self.assertIsNone(asked[-1])
        r.typed('go_to', {'place': 'on top of the furnace'})
        self.assertEqual(asked[-1], 'furnace')


class PickupAnyTests(unittest.TestCase):
    def test_all_means_any_item(self):
        # ("all" matched no item: nothing picked up where the player died, their things lying there, 2026-10-17)
        r = CommandResolver.__new__(CommandResolver)
        r.adapter = NS(match_item=lambda w: ['minecraft:dirt'] if w == 'dirt' else [])
        for words in (['all'], ['everything'], ['my stuff'], ['nothing by that name']):
            got = r.typed('pickup', {'items': words})
            self.assertIsNone(got.steps[0][1], words)
        self.assertEqual(r.typed('pickup', {'items': ['dirt']}).steps[0][1], ('minecraft:dirt',))
