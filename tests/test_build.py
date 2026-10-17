"""Building (core/build.py, the tasks' 'place' steps, the resolver's build): "make me a house" (2026-10-16: "my
building tools only lay one block at a time right where I stand", and no house)."""
import unittest

from game_assistant.core.build import BadPlan, expand, needs, order
from game_assistant.core.tasks import TaskRunner

C = 'mc:cobblestone'


def house(x=0, y=1, z=0, w=5, h=3):
    """A floor, walls on it, the inside cleared, a door gap, a roof: as the doer would give it."""
    return [{'shape': 'fill', 'from': (x, y, z), 'to': (x + w - 1, y, z + w - 1), 'item': C},
            {'shape': 'walls', 'from': (x, y + 1, z), 'to': (x + w - 1, y + h, z + w - 1), 'item': C},
            {'shape': 'clear', 'from': (x + 1, y + 1, z + 1), 'to': (x + w - 2, y + h, z + w - 2)},
            {'shape': 'clear', 'from': (x + 2, y + 1, z), 'to': (x + 2, y + 2, z)},
            {'shape': 'fill', 'from': (x, y + h + 1, z), 'to': (x + w - 1, y + h + 1, z + w - 1), 'item': C}]


def flat(cells, ground=0):
    """What's there now: the ground (y <= ground) solid, all else air."""
    out = {}
    lo = [min(c[i] for c in cells) - 1 for i in range(3)]
    hi = [max(c[i] for c in cells) + 1 for i in range(3)]
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                out[(x, y, z)] = 'other' if y <= ground else 'air'
    return out


class BlueprintTests(unittest.TestCase):
    def test_a_house_from_its_parts(self):
        t = expand(house())
        blocks = {c for c, i in t.items() if i}
        self.assertEqual(len([c for c in blocks if c[1] == 1]), 25)                  # the floor
        self.assertEqual(len([c for c in blocks if 2 <= c[1] <= 4]), 3 * 16 - 2)    # walls, but the door
        self.assertEqual(t[(2, 2, 0)], '')                                           # the door gap
        self.assertEqual(t[(2, 3, 2)], '')                                           # the room
        self.assertEqual(len([c for c in blocks if c[1] == 5]), 25)                  # the roof
        self.assertEqual(needs(t, flat(t)), {C: 25 + 46 + 25})

    def test_each_block_goes_against_one_there_by_then(self):
        t = expand(house())
        dig, places = order(t, flat(t))
        self.assertEqual(dig, [])
        done = set()
        for c, _item in places:
            below_or_beside = [(c[0] + d[0], c[1] + d[1], c[2] + d[2]) for d in
                               ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0))]
            self.assertTrue(any(n in done or n[1] <= 0 for n in below_or_beside), c)
            done.add(c)
        self.assertEqual([c[1] for c, _ in places], sorted(c[1] for c, _ in places))   # from the bottom up

    def test_whats_in_the_way_is_mined_and_whats_there_is_kept(self):
        t = expand(house())
        now = flat(t)
        now[(2, 3, 2)] = 'other'                    # a block in the room
        now[(0, 1, 0)] = 'same'                     # a floor block already there
        dig, places = order(t, now)
        self.assertEqual(dig, [(2, 3, 2)])
        self.assertNotIn((0, 1, 0), [c for c, _ in places])

    def test_blocks_in_the_air_with_nothing_beside_them(self):
        t = expand([{'shape': 'fill', 'from': (0, 9, 0), 'to': (0, 9, 0), 'item': C}])
        with self.assertRaises(BadPlan):
            order(t, flat(t))
        with self.assertRaises(BadPlan):
            expand([{'shape': 'walls', 'from': (0, 0, 0), 'to': (1, 1, 1)}])     # (no item)


class Spot:
    """A world with one cell to put a block into: in reach only once walked to."""

    def __init__(self):
        self.inv = {C: 3}
        self.walks, self.near, self.clicked = [], False, 0

    def snap(self):
        from game_assistant.core.interface import Camera, Player, Screen, Snapshot
        cam = Camera((0.0, 1.6, 0.0), (0.0, -0.6, 0.8), (1.0, 0.0, 0.0), (0.0, 0.8, 0.6), 70, 1.7)
        return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), cam, (), None,
                        screen=Screen(0, 0, 800, 600, True),
                        extra={'inventory': dict(self.inv), 'hotbar': [(C, self.inv.get(C, 0))], 'slot': 0,
                               'aim_block': (0, 0, 2), 'aim_face': 'up', 'aim_solid': True})

    def task_place_spot(self, exclude=(), only=None):
        return ((0.0, 1.0, 2.5), (0, 0, 2), 'up', tuple(only)) if self.near else None

    def task_walk(self, point, name, place=None):
        self.walks.append(place)
        self.near = True

    def task_stop(self):
        pass

    def task_say(self, line):
        pass


class PlaceTests(unittest.TestCase):
    def test_walked_within_reach_then_put_down(self):
        w = Spot()
        r = TaskRunner(w)
        r.start([('place', (0, 1, 2), C, (0.0, 1.0, 2.5))], 'building', 1)
        for _ in range(3):
            r.update(w.snap(), 1 / 30, False)
        self.assertEqual(w.walks, [(0, 1, 2)])                          # out of reach: walked to where it is
        r.on_event('arrived')
        for _ in range(60):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None and 'interact' in intent.taps:
                self.assertIn('sneak', intent.keys)                     # (crouched: nothing clicked is used)
                w.inv[C] -= 1
            if not r.active:
                break
        self.assertIn('done', r.events)
        self.assertEqual(r.built, {(0, 1, 2)})
        self.assertEqual(w.inv[C], 2)

    def test_whats_to_be_built_with_is_kept_from_the_walk(self):
        r = TaskRunner(Spot())
        r.start([('place', (0, 1, 2), C, (0.0, 1.0, 2.5)), ('place', (0, 2, 2), C, (0.0, 2.0, 2.5))], 'building', 1)
        self.assertEqual(r.reserved(), {C: 2})
        self.assertIn('build with 2 cobblestone', r.describe())


class ResolverBuildTests(unittest.TestCase):
    def test_a_house_as_a_task(self):
        try:
            from test_walk_commands import FakeAdapter
            from sim.world import M, SimBook
            from test_sim_orders import plains
        except ModuleNotFoundError:
            from tests.test_walk_commands import FakeAdapter
            from tests.sim.world import M, SimBook
            from tests.test_sim_orders import plains
        from game_assistant.core.walk_commands import CommandResolver
        world = plains()

        class A(FakeAdapter):
            def match_item(self, what):
                return [M + what.replace(' ', '_')] if what.replace(' ', '_') in ('cobblestone', 'oak_planks') else []

            def cells_now(self, cells, items):
                return flat(cells, ground=19)

            def book(self):
                return SimBook(world)

            def coords_point(self, x, y, z):
                return (-x - 0.5, float(y), z + 0.5)

            def snapshot(self):
                s = super().snapshot()
                return s.__class__(**{**s.__dict__, 'extra': {'inventory': {M + 'cobblestone': 30}}})
        r = CommandResolver(A([]))
        parts = [dict(p, item='cobblestone') if p.get('item') else p for p in house(10, 19, 10)]
        got = r.typed('build', {'parts': parts})
        self.assertEqual(got.kind, 'task', got.say)
        kinds = [s[0] for s in got.steps]
        self.assertEqual(kinds.count('place'), 96)
        self.assertLess(kinds.index('gather'), kinds.index('place'))   # (66 more cobblestone got first)
        self.assertEqual(got.steps[-1][0], 'place')
        bad = r.typed('build', {'parts': [{'shape': 'fill', 'from': (0, 30, 0), 'to': (0, 30, 0), 'item': 'cobblestone'}]})
        self.assertEqual(bad.kind, 'none')
