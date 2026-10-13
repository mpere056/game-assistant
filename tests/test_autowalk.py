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

    def test_walks_past_enemies_without_a_word(self):
        # (the "Careful, an enemy ahead!" warning was removed at the user's request, 2026-10-12)
        w = World()
        w.entities = (Entity(7, 1, 'c1', (1.0, 0.0, 20.0), (1.0, 1.0, 20.0), 0.5, 2.0, True, False),)
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0))
        walk(aw, w, 3)
        self.assertFalse([e for e in aw.events if e.startswith('enemy')])
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

    def test_follows_the_route_to_a_character_far_above(self):
        # 15:22: an Eagle 35 m up a cliff, 18 m away on the map: straight at it walked into the cliff.
        w = World()
        aw = AutoWalk()
        w.entities = (self._enemy((0.0, 35.0, 15.0)),)
        aw.start([(0.0, 0.0, 0.0), (20.0, 0.0, 5.0), (0.0, 35.0, 15.0)], (0.0, 35.0, 15.0), 'Eagle', target_id=7)
        aw.update(w.snap(), 1 / 30)
        self.assertNotEqual(aw.state, 'chasing')     # along the route (round by the slope), not at the cliff

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


class ResumeTests(unittest.TestCase):
    def test_no_stuck_right_after_a_pause(self):
        from game_assistant.core.autowalk import RESUME_GRACE
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0), 'there')
        aw._paused_why = 'not_focused'
        aw._resumed_at = None
        aw.t = 10.0
        aw._stuck_since = None
        aw._paused_why = None
        aw._resumed_at = aw.t
        for _ in range(int((RESUME_GRACE - 0.1) * 30)):
            aw.t += 1 / 30
            self.assertFalse(aw._check_stuck((0.0, 0.0, 0.0)))
        self.assertNotIn('unstick', aw.events)


class ClearLineTests(unittest.TestCase):
    def test_round_by_the_route_when_a_wall_is_between(self):
        # 20:34: a soldier 13 m away behind a wall: straight at it, stuck, gave up.
        w = World()
        aw = AutoWalk()
        w.entities = (Entity(7, 1, 'c1', (0.0, 0.0, 13.0), (0.0, 1.0, 13.0), 0.5, 2.0, True, False),)
        aw.start([(0.0, 0.0, 0.0), (10.0, 0.0, 6.0), (0.0, 0.0, 13.0)], (0.0, 0.0, 13.0), 'Soldier', target_id=7)
        aw.clear_line = False
        aw.update(w.snap(), 1 / 30)
        self.assertNotEqual(aw.state, 'chasing')
        aw.clear_line = True                 # a clear line, but the route bends 6 m off it: the route still
        aw.update(w.snap(), 1 / 30)          # (something low the rays don't see; twenty-second session)
        self.assertNotEqual(aw.state, 'chasing')
        aw.set_route([(0.0, 0.0, 0.0), (0.5, 0.0, 6.0), (0.0, 0.0, 13.0)])   # a straight route: straight at it
        aw.update(w.snap(), 1 / 30)
        self.assertEqual(aw.state, 'chasing')

    def test_the_route_for_a_while_after_getting_stuck_chasing(self):
        from game_assistant.core.autowalk import STUCK_ROUTE_S
        w = World()
        aw = AutoWalk()
        w.entities = (Entity(7, 1, 'c1', (0.0, 0.0, 13.0), (0.0, 1.0, 13.0), 0.5, 2.0, True, False),)
        aw.start([(0.0, 0.0, 0.0), (0.5, 0.0, 6.0), (0.0, 0.0, 13.0)], (0.0, 0.0, 13.0), 'Soldier', target_id=7)
        aw.clear_line = True
        aw.update(w.snap(), 1 / 30)
        self.assertEqual(aw.state, 'chasing')
        aw._stucks = []
        aw._stuck_since, aw._stuck_pos = aw.t - 3.0, (0.0, 0.0, 0.0)
        aw._check_stuck((0.0, 0.0, 0.0))
        aw._unstick_until = aw._around_until = -1.0
        aw.update(w.snap(), 1 / 30)
        self.assertNotEqual(aw.state, 'chasing')
        aw.t += STUCK_ROUTE_S
        aw.update(w.snap(), 1 / 30)
        self.assertEqual(aw.state, 'chasing')

    def test_not_arrived_through_a_wall(self):
        w = World()
        aw = AutoWalk()
        w.entities = (Entity(7, 1, 'c1', (0.0, 0.0, 2.0), (0.0, 1.0, 2.0), 0.5, 2.0, True, False),)
        aw.start([(0.0, 0.0, 0.0), (3.0, 0.0, 2.0)], (0.0, 0.0, 2.0), 'Soldier', target_id=7)
        aw.clear_line = False
        aw.update(w.snap(), 1 / 30)
        self.assertNotIn('arrived', aw.events)



class NoRouteTests(unittest.TestCase):
    def test_waits_for_a_route_rather_than_walk_into_a_wall(self):
        w = World()
        aw = AutoWalk()
        w.entities = (Entity(7, 1, 'c1', (0.0, 0.0, 10.0), (0.0, 1.0, 10.0), 0.5, 2.0, True, False),)
        aw.start([], (0.0, 0.0, 10.0), 'Soldier', target_id=7)
        aw.clear_line = False
        intent = aw.update(w.snap(), 1 / 30)
        self.assertEqual(aw.state, 'waiting_route')
        self.assertEqual(intent.keys, frozenset())


class BreakTests(unittest.TestCase):
    """Stuck against something no ray sees (barrels, crates: likely breakable): swing at it first."""

    def _stuck_walk(self, ahead_blocked, seconds=6.0):
        w = World()
        aw = AutoWalk()
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 50.0)], (0.0, 0.0, 50.0))
        aw.ahead_blocked = ahead_blocked
        taps = []
        for _ in range(int(seconds * 30)):
            intent = aw.update(w.snap(), 1 / 30)       # (the world never moves the player: stuck)
            taps += list(intent.taps)
            if not aw.active:
                break
        return aw, taps

    def test_swings_at_an_unseen_blocker_then_falls_back_to_stepping_round(self):
        aw, taps = self._stuck_walk(False, seconds=9.0)
        self.assertIn('break', aw.events)
        self.assertGreaterEqual(taps.count('attack'), 2)
        self.assertLessEqual(aw.events.count('break'), 2)       # at most twice in one place
        self.assertIn('unstick', aw.events)                     # then round it

    def test_a_wall_seen_ahead_is_not_attacked(self):
        aw, taps = self._stuck_walk(True)
        self.assertNotIn('break', aw.events)
        self.assertNotIn('attack', taps)
        self.assertIn('unstick', aw.events)


class ChaseBreakTests(unittest.TestCase):
    def test_walking_to_a_character_and_stuck_on_an_unseen_thing_swings(self):
        # 2026-10-12: "let me smash it" four times walking to soldiers, never a swing (the chase returned first).
        w = World()
        aw = AutoWalk()
        w.entities = (Entity(7, 1, 'c1', (0.0, 0.0, 8.0), (0.0, 1.0, 8.0), 0.5, 2.0, True, False),)
        aw.start([(0.0, 0.0, 0.0), (0.0, 0.0, 8.0)], (0.0, 0.0, 8.0), 'Soldier', target_id=7)
        aw.ahead_blocked, aw.clear_line = False, True
        taps = []
        for _ in range(int(3.5 * 30)):
            taps += list(aw.update(w.snap(), 1 / 30).taps)
        self.assertIn('break', aw.events)
        self.assertGreaterEqual(taps.count('attack'), 2)


class BlockWorld(World):
    """World plus looking up and down, and one block on the way that breaks after 0.5 s of mining while it is
    under the crosshair (Minecraft's route work)."""

    def __init__(self, block, **kw):
        super().__init__(**kw)
        self.pitch, self.block, self.mined, self.held_attack = 0.0, block, 0.0, False

    def snap(self):
        r, pr = math.radians(self.yaw), math.radians(self.pitch)
        fwd = (math.sin(r) * math.cos(pr), -math.sin(pr), math.cos(r) * math.cos(pr))
        right = (math.cos(r), 0.0, -math.sin(r))
        eye = (self.pos[0], self.pos[1] + 1.6, self.pos[2])
        cam = Camera(eye, fwd, right, (0.0, 1.0, 0.0), 70, 16 / 9)
        aim = None
        if self.block is not None:
            d = [self.block[i] - eye[i] for i in range(3)]
            n = math.sqrt(sum(v * v for v in d))
            if sum(d[i] / n * fwd[i] for i in range(3)) > math.cos(math.radians(4)):
                aim = (1, 2, 3)
        return Snapshot('test', 1, True, Player(tuple(self.pos), fwd, 'alive'), cam, (), None, screen=SCREEN,
                        extra={'aim_block': aim, 'aim_face': 'west', 'tool_slot': 2, 'slot': 0, 'block_slot': -1})

    def apply(self, intent, dt):
        self.pitch = max(-90.0, min(90.0, self.pitch + intent.pitch))
        if 'attack' in intent.keys:
            self.held_attack = True
            if self.snap().extra['aim_block'] is not None:
                self.mined += dt
                if self.mined >= 0.5:
                    self.block = None
        if self.block is not None and self.pos[2] > self.block[2] - 1.3:
            intent = type(intent)(frozenset(intent.keys - {'forward'}), intent.turn, intent.taps, intent.pitch)
        super().apply(intent, dt)


class RouteWorkTests(unittest.TestCase):
    def test_mines_the_block_in_the_way_then_walks_on(self):
        w = BlockWorld((0.0, 0.5, 5.5), yaw=0.0)
        aw = AutoWalk(sprint=False)
        route = [(0.0, 0.0, 0.0), (0.0, 0.0, 4.5), (0.0, 0.0, 12.0)]
        aw.start(route, route[-1], 'There', actions=[('break', (0.0, 0.0, 4.5), (0.0, 0.5, 5.5), {'key': (1, 2, 3)})])
        walk(aw, w, 20)
        self.assertTrue(w.held_attack)
        self.assertIsNone(w.block)
        self.assertGreater(w.pitch, 20.0)               # it looked down at the block (feet height)
        self.assertIn('slot3', w.taps)                  # the best tool for it
        self.assertIn('arrived', aw.events)

    def test_pillars_up_jumping_and_placing_under_the_feet(self):
        class Pillar(BlockWorld):
            def __init__(self):
                super().__init__(None)
                self.vy, self.floor, self.placed = 0.0, 0.0, 0

            def snap(self):
                s = super().snap()
                down = s.camera.forward[1] < -0.97
                s.extra.update(aim_block=((0, self.floor, 0) if down else None), aim_face='up', block_slot=4,
                               slot=0 if 'slot5' not in self.taps else 4)
                if down and self.placed and self.pos[1] < self.floor + 1.0:
                    s.extra['aim_block'] = (0, self.floor, 0)
                return s

            def apply(self, intent, dt):
                self.pitch = max(-90.0, min(90.0, self.pitch + intent.pitch))
                self.taps += list(intent.taps)
                if 'jump' in intent.taps and self.pos[1] <= self.floor + 0.001:
                    self.vy = 8.4                       # a Minecraft jump: about 1.25 blocks
                if 'interact' in intent.taps and self.pos[1] >= self.floor + 1.0 \
                        and self.snap().extra['aim_block'] == (0, self.floor, 0):
                    self.floor += 1                     # the new block under the feet
                    self.placed += 1
                self.vy -= 28.0 * dt
                self.pos[1] = max(self.floor, self.pos[1] + self.vy * dt)
                if self.pos[1] == self.floor:
                    self.vy = 0.0

        w = Pillar()
        aw = AutoWalk(sprint=False)
        route = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1.0, 3.0)]
        aw.start(route, route[-1], 'Up', actions=[
            ('place', (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {'key': (0, 0, 0), 'face': 'up', 'into': (0, 1, 0),
                                                          'kind': 'pillar'})])
        walk(aw, w, 6)
        self.assertEqual(w.placed, 1)
        self.assertIn('slot5', w.taps)


class PreciseWalkTests(unittest.TestCase):
    def test_goes_round_a_corner_cell_by_cell_without_cutting_it(self):
        # a block at (1, 1) (cells of 1 m): the route goes north two cells, then east two
        w = World(yaw=0.0)
        aw = AutoWalk(sprint=False, precise=True)
        route = [(0.5, 0.0, 0.5), (0.5, 0.0, 1.5), (0.5, 0.0, 2.5), (1.5, 0.0, 2.5), (2.5, 0.0, 2.5),
                 (3.5, 0.0, 2.5), (4.5, 0.0, 2.5), (5.5, 0.0, 2.5)]
        w.pos = [0.5, 0.0, 0.5]
        aw.start(route, route[-1], 'There')
        inside = []
        for _ in range(30 * 10):
            intent = aw.update(w.snap(), 1 / 30)
            w.apply(intent, 1 / 30)
            if 1.0 - 0.3 < w.pos[0] < 2.0 + 0.3 and 1.0 - 0.3 < w.pos[2] < 2.0 + 0.3:
                inside.append(tuple(w.pos))
            if not aw.active:
                break
        self.assertIn('arrived', aw.events)
        self.assertEqual(inside, [])                    # never clipped the block at (1, 1)

    def test_an_extended_route_carries_on_from_where_the_player_is(self):
        w = World(yaw=0.0)
        aw = AutoWalk(sprint=False, precise=True)
        route = [(0.5, 0.0, 0.5 + k) for k in range(40)]
        aw.start(route[:20], route[-1], 'There')
        walk(aw, w, 3)
        z = w.pos[2]
        aw.set_route(route)                            # planned further: the whole route again from its start
        w2 = []
        for _ in range(15):
            intent = aw.update(w.snap(), 1 / 30)
            w.apply(intent, 1 / 30)
            w2.append(w.pos[2])
        self.assertGreater(w2[-1], z)                  # still going on, not back to the start
        self.assertNotIn('replan', aw.events)

    def test_arrives_only_at_the_end_of_a_complete_route_and_looks_at_the_block(self):
        w = BlockWorld(None, yaw=0.0)
        w.pos = [0.5, 0.0, 0.5]
        aw = AutoWalk(sprint=False, precise=True)
        route = [(0.5, 0.0, 0.5 + k) for k in range(4)]
        aw.start(route, (0.5, -1.0, 4.5), 'Stone',
                 actions=[('look', route[-1], (0.5, 0.5, 4.5), {})])   # a block in front, at the feet
        aw.set_route(route, aw_actions := [('look', route[-1], (0.5, 0.5, 4.5), {})], complete=False)
        walk(aw, w, 3)
        self.assertNotIn('arrived', aw.events)          # the route isn't all planned yet: wait at its end
        self.assertEqual(aw.state, 'waiting')
        aw.set_route(route, aw_actions, complete=True)
        walk(aw, w, 3)
        self.assertIn('arrived', aw.events)
        self.assertGreater(w.pitch, 30.0)               # looking down at the block at its feet
