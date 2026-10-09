"""Offline tests for the fairy's movement and for navigation, in synthetic worlds."""
import unittest

from game_assistant.core import nav
from game_assistant.core.companion import LEASH, Companion
from game_assistant.core.interface import Camera, Entity, Player, RayHit, Screen, Snapshot, dist

CAM = Camera((0.0, 3.0, -4.0), (0.0, -0.2, 0.98), (1.0, 0.0, 0.0), (0.0, 0.98, 0.2), 48.0, 16 / 9)
SCREEN = Screen(0, 0, 1920, 1080, True)


def snap(entities=(), player=(0.0, 0.0, 0.0), menu=None):
    return Snapshot('test', 1, True, Player(player, (0.0, 0.0, 1.0), 'alive'), CAM, tuple(entities), None,
                    screen=SCREEN, menu=menu)


def run(c, s, seconds, fps=60):
    v = None
    for _ in range(int(seconds * fps)):
        v = c.update(s, 1 / fps)
    return v


class CompanionTests(unittest.TestCase):
    def test_follows_beside_the_head_and_off_the_crosshair(self):
        c = Companion()
        v = run(c, snap(), 3)
        self.assertTrue(v.visible)
        self.assertLess(dist(v.pos, (0.85, 1.75, 0.0)), 0.6)
        self.assertGreater(abs(v.screen_x - 960), 0.12 * 960 * 0.9)  # not on the crosshair
        self.assertTrue(10 <= v.size_px <= 96)

    def test_goes_to_an_entity_and_comes_back(self):
        wolf = Entity(7, 1, 'c4180', (0.0, 0.0, 10.0), (0.0, 0.6, 10.0), 0.5, 1.2, True, False)
        c = Companion()
        run(c, snap([wolf]), 1)
        c.show(entity_id=7, seconds=3)
        v = run(c, snap([wolf]), 2.5)
        self.assertLess(dist(v.pos, (0.0, 1.7, 9.1)), 1.5)
        v = run(c, snap([wolf]), 3)
        self.assertEqual(v.mode, 'follow')

    def test_leash_holds_and_waits(self):
        c = Companion()
        run(c, snap(), 1)
        c.go(point=(0.0, 2.0, 100.0))
        v = run(c, snap(), 8)
        self.assertTrue(v.waiting)
        self.assertLessEqual(dist(v.pos, (0.0, 1.75, 0.0)), LEASH + 0.5)

    def test_guide_flies_ahead_and_announces_arrival(self):
        c = Companion()
        run(c, snap(), 1)
        c.guide((0.0, 0.0, 600.0), 'Far Ruins')
        v = run(c, snap(), 12)
        self.assertEqual(v.mode, 'guide')
        self.assertFalse(v.waiting)
        self.assertGreater(v.pos[2], 90)                      # well beyond the 75 m leash
        self.assertLessEqual(v.pos[2], 100.5)                 # but never more than 100 m ahead
        v = run(c, snap(player=(0.0, 0.0, 590.0)), 1)          # the player arrives
        self.assertIn('arrived:Far Ruins', c.events)

    def test_hidden_in_menus(self):
        c = Companion()
        run(c, snap(), 1)
        self.assertFalse(c.update(snap(menu=True), 1 / 60).visible)


def world_with_wall(rays):
    """Flat ground at y=0 with a 3 m high wall along z=10 from x=-12 to x=12."""
    out = []
    for a, b in rays:
        hit = None
        if (a[2] - 10) * (b[2] - 10) < 0:  # crosses the wall plane
            t = (10 - a[2]) / (b[2] - a[2])
            p = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
            if -12 <= p[0] <= 12 and 0 <= p[1] <= 3:
                hit = RayHit(a, b, True, p, (0.0, 0.0, -1.0 if b[2] > a[2] else 1.0))
        if hit is None and a[1] > 0 >= b[1]:
            t = a[1] / (a[1] - b[1])
            p = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
            on_wall_top = abs(p[2] - 10) < 0.01 and -12 <= p[0] <= 12
            hit = RayHit(a, b, True, (p[0], 3.0 if on_wall_top else 0.0, p[2]), (0.0, 1.0, 0.0))
        out.append(hit or RayHit(a, b, False))
    return out


class NavTests(unittest.TestCase):
    def test_route_goes_around_the_wall(self):
        r = nav.plan(world_with_wall, (0.0, 0.0, 0.0), (0.0, 0.0, 20.0))
        self.assertIsNotNone(r)
        self.assertTrue(r.reaches_goal)
        self.assertTrue(any(abs(p[0]) >= 12 for p in r.waypoints), r.waypoints)
        self.assertLess(r.rays_used, 1200)

    def test_straight_route_when_open(self):
        r = nav.plan(lambda rays: world_with_wall([(a, b) for a, b in rays]), (20.0, 0.0, 0.0), (20.0, 0.0, 20.0))
        self.assertIsNotNone(r)
        self.assertEqual(len(r.waypoints), 2)

    def test_far_goal_gets_a_partial_route(self):
        r = nav.plan(world_with_wall, (0.0, 0.0, 0.0), (0.0, 0.0, 200.0))
        self.assertFalse(r.reaches_goal)



class CommandTests(unittest.TestCase):
    def setUp(self):
        from game_assistant.core import commands
        from game_assistant.games.demo.adapter import DemoAdapter
        self.commands = commands
        self.fairy = Companion()
        self.spoken = []
        self.ctx = commands.Context(DemoAdapter(), self.fairy, stop_speech=lambda: self.spoken.append('stop'))

    def test_polite_forms(self):
        self.assertEqual(self.commands.normalize('Hey fairy, come back to me please!'), 'come back')
        self.assertEqual(self.commands.normalize('Fairy, stop now.'), 'stop')

    def test_come_back_and_stop(self):
        self.fairy.go(point=(0.0, 1.0, 10.0))
        self.assertEqual(self.commands.handle('come back to me', self.ctx), 'Coming back.')
        self.assertEqual(self.fairy.mode, 'follow')
        self.assertEqual(self.commands.handle('Stop!', self.ctx), 'Stopped.')
        self.assertIn('stop', self.spoken)

    def test_questions_are_not_commands(self):
        self.assertIsNone(self.commands.handle('what is that weak to?', self.ctx))
        self.assertIsNone(self.commands.handle('where can I find the Moonveil?', self.ctx))

    def test_go_to_that_uses_the_crosshair(self):
        self.assertEqual(self.commands.handle('go there', self.ctx), 'On my way.')
        self.assertEqual(self.fairy.mode, 'go')


if __name__ == '__main__':
    unittest.main()
