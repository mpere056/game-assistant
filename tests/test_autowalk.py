"""Auto-walk (core/autowalk.py) in a tiny simulated world: keys move the player relative to the camera,
the camera turns as told."""
import math
import unittest

from game_assistant.core.autowalk import AutoWalk
from game_assistant.core.interface import Camera, Entity, Player, Screen, Snapshot

SPEED = {False: 4.0, True: 7.0}  # m/s walking, running
SCREEN = Screen(0, 0, 1280, 720, True)


class World:
    def __init__(self, pos=(0.0, 0.0, 0.0), yaw=0.0, frozen=False):
        self.pos, self.yaw, self.frozen = list(pos), yaw, frozen
        self.menu, self.focused, self.entities = None, True, ()
        self.taps = []

    def snap(self):
        r = math.radians(self.yaw)
        fwd, right = (math.sin(r), 0.0, math.cos(r)), (math.cos(r), 0.0, -math.sin(r))
        cam = Camera((self.pos[0] - fwd[0] * 3, 2.0, self.pos[2] - fwd[2] * 3), fwd, right, (0.0, 1.0, 0.0), 60, 16 / 9)
        return Snapshot('test', 1, True, Player(tuple(self.pos), fwd, 'alive'), cam, self.entities, None,
                        screen=Screen(0, 0, 1280, 720, self.focused), menu=self.menu)

    def apply(self, intent, dt):
        self.yaw += intent.turn
        self.taps += list(intent.taps)
        if self.frozen or not intent.keys:
            return
        fx = ('forward' in intent.keys) - ('back' in intent.keys)
        rx = ('right' in intent.keys) - ('left' in intent.keys)
        if not fx and not rx:
            return
        r = math.radians(self.yaw)
        fwd, right = (math.sin(r), math.cos(r)), (math.cos(r), -math.sin(r))
        d = (fwd[0] * fx + right[0] * rx, fwd[1] * fx + right[1] * rx)
        n = math.hypot(*d)
        v = SPEED['sprint' in intent.keys] * dt / n
        self.pos[0] += d[0] * v
        self.pos[2] += d[1] * v


def walk(aw, world, seconds, fps=30):
    for _ in range(int(seconds * fps)):
        intent = aw.update(world.snap(), 1 / fps)
        world.apply(intent, 1 / fps)
        if not aw.active:
            break


class AutoWalkTests(unittest.TestCase):
    def test_follows_an_l_shaped_route_and_arrives(self):
        w = World(yaw=90.0)  # the camera looks east; the route goes north first
        aw = AutoWalk()
        route = [(0.0, 0.0, 0.0), (0.0, 0.0, 30.0), (30.0, 0.0, 30.0)]
        aw.start(route, route[-1], 'Grace')
        walk(aw, w, 30)
        self.assertIn('arrived', aw.events)
        self.assertFalse(aw.active)
        self.assertLess(math.hypot(w.pos[0] - 30, w.pos[2] - 30), 3.5)

    def test_stays_near_the_route_around_the_corner(self):
        w = World()
        aw = AutoWalk()
        route = [(0.0, 0.0, 0.0), (0.0, 0.0, 30.0), (30.0, 0.0, 30.0)]
        aw.start(route, route[-1])
        worst = 0.0
        for _ in range(30 * 30):
            w.apply(aw.update(w.snap(), 1 / 30), 1 / 30)
            x, z = w.pos[0], w.pos[2]
            off = min(abs(x) if z <= 30 else 99, abs(z - 30) if x >= 0 else 99)
            worst = max(worst, off)
            if not aw.active:
                break
        self.assertLess(worst, 3.0)   # cuts the corner a little, never far off

    def test_runs_on_a_long_straight(self):
        w = World()
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 100.0)], (0.0, 0.0, 100.0))
        intent = None
        for _ in range(30):
            intent = aw.update(w.snap(), 1 / 30)
            w.apply(intent, 1 / 30)
        self.assertIn('sprint', intent.keys)

    def test_stuck_backs_off_then_asks_for_a_new_route_then_gives_up(self):
        w = World(frozen=True)
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0))
        walk(aw, w, 2.5)
        self.assertEqual(aw.events, ['unstick'])        # first: back off and sidestep
        self.assertTrue(aw.active)
        walk(aw, w, 3.5)
        self.assertIn('stuck', aw.events)              # again: a new route, avoiding the spot
        self.assertTrue(aw.active)
        walk(aw, w, 4)
        self.assertIn('gave_up', aw.events)            # a third time within a minute: over to you
        self.assertFalse(aw.active)

    def test_narrow_bend_is_followed_closely(self):
        """A cave-mouth-like corridor: 10 m in, a sharp 90-degree turn: never more than 1 m off."""
        w = World()
        aw = AutoWalk()
        route = [(0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (-10.0, 0.0, 10.0)]
        aw.start(route, route[-1])
        worst = 0.0
        for _ in range(30 * 20):
            w.apply(aw.update(w.snap(), 1 / 30), 1 / 30)
            x, z = w.pos[0], w.pos[2]
            off = abs(x) if z < 10 - 1e-6 and x > -1e-6 else abs(z - 10)
            worst = max(worst, min(off, abs(x) + abs(z - 10)))
            if not aw.active:
                break
        self.assertIn('arrived', aw.events)
        self.assertLess(worst, 1.0)

    def test_pauses_in_a_menu_and_when_not_in_front(self):
        w = World()
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0))
        w.menu = True
        self.assertEqual(aw.update(w.snap(), 1 / 30).keys, frozenset())
        self.assertIn('paused:menu', aw.events)
        w.menu, w.focused = None, False
        self.assertEqual(aw.update(w.snap(), 1 / 30).keys, frozenset())
        self.assertIn('paused:not_focused', aw.events)
        w.focused = True
        self.assertIn('forward', aw.update(w.snap(), 1 / 30).keys)

    def test_warns_about_an_enemy_ahead_once_and_keeps_walking(self):
        w = World()
        w.entities = (Entity(7, 1, 'c1', (1.0, 0.0, 20.0), (1.0, 1.0, 20.0), 0.5, 2.0, True, False),)
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0))
        walk(aw, w, 3)
        self.assertEqual(aw.events.count('enemy:7'), 1)
        self.assertTrue(aw.active)

    def test_ladder_interacts_and_climbs(self):
        w = World()
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 5.0), (0.0, 10.0, 6.0), (0.0, 10.0, 20.0)], (0.0, 10.0, 20.0),
                 actions=[('ladder', (0.0, 0.0, 5.0), (0.0, 10.0, 6.0))])
        walk(aw, w, 2)
        self.assertIn('interact', w.taps)
        self.assertEqual(aw.state, 'ladder')
        intent = aw.update(w.snap(), 1 / 30)
        self.assertEqual(intent.keys, frozenset({'forward'}))
        w.pos[1] = 10.0  # the game would climb it
        walk(aw, w, 0.2)
        self.assertNotEqual(aw.state, 'ladder')


class SprintTests(unittest.TestCase):
    def test_running_never_flickers(self):
        """The run key is never held for less than 1.5 s (a short tap would backstep or roll)."""
        w = World()
        aw = AutoWalk()
        zig = [(0.0, 0.0, 0.0)]
        for i in range(1, 12):  # a long zigzag: 22 m stretches with sharp turns
            zig.append((22.0 * (i % 2), 0.0, 22.0 * i))
        aw.start(zig, zig[-1])
        held_for, runs = 0.0, []
        for _ in range(30 * 60):
            intent = aw.update(w.snap(), 1 / 30)
            w.apply(intent, 1 / 30)
            if 'sprint' in intent.keys:
                held_for += 1 / 30
            elif held_for:
                runs.append(held_for)
                held_for = 0.0
            if not aw.active:
                break
        self.assertTrue(runs)
        self.assertGreaterEqual(min(runs), 1.5 - 1e-6)


class ChaseTests(unittest.TestCase):
    """Walking to and attacking a character that moves (live position, not where it was)."""

    def _enemy(self, pos, dead=False):
        return Entity(7, 1, 'c1', tuple(pos), (pos[0], 1.0, pos[2]), 0.5, 2.0, True, dead)

    def test_walks_to_where_a_moving_character_is_now(self):
        w = World()
        aw = AutoWalk()
        enemy = [8.0, 0.0, 15.0]
        aw.start([], tuple(enemy), 'Soldier', target_id=7)
        for _ in range(30 * 15):
            enemy[0] -= 4.0 / 30   # it walks west at 4 m/s... for a while
            if enemy[0] < -12:
                enemy[0] = -12
            w.entities = (self._enemy(enemy),)
            w.apply(aw.update(w.snap(), 1 / 30), 1 / 30)
            if not aw.active:
                break
        self.assertIn('arrived', aw.events)
        self.assertLess(math.hypot(w.pos[0] - enemy[0], w.pos[2] - enemy[2]), 3.5)  # where it is now

    def test_attack_locks_on_swings_once_and_hands_back(self):
        w = World()
        aw = AutoWalk()
        w.entities = (self._enemy((0.0, 0.0, 15.0)),)
        aw.start([], (0.0, 0.0, 15.0), 'Soldier', target_id=7, attack=True)
        walk(aw, w, 10)
        self.assertEqual(w.taps.count('lock'), 1)
        self.assertEqual(w.taps.count('attack'), 1)
        self.assertLess(w.taps.index('lock'), w.taps.index('attack'))
        self.assertIn('attacked', aw.events)
        self.assertFalse(aw.active)
        self.assertLess(math.hypot(w.pos[0], w.pos[2] - 15.0), 0.5 + 1.8 + 0.3)   # swung from close by

    def test_target_dies_on_the_way(self):
        w = World()
        aw = AutoWalk()
        w.entities = (self._enemy((0.0, 0.0, 15.0)),)
        aw.start([], (0.0, 0.0, 15.0), 'Soldier', target_id=7, attack=True)
        walk(aw, w, 1)
        w.entities = (self._enemy((0.0, 0.0, 15.0), dead=True),)
        walk(aw, w, 0.2)
        self.assertIn('target_gone', aw.events)
        self.assertFalse(aw.active)
