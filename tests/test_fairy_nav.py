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

    def test_standing_above_the_place_is_not_arriving(self):
        c = Companion()
        run(c, snap(), 1)
        c.guide((0.0, -67.0, 300.0), 'Deep Tunnel')
        run(c, snap(player=(0.0, 0.0, 295.0)), 1)              # right above it, 67 m up
        self.assertNotIn('arrived:Deep Tunnel', c.events)
        self.assertIn('level:Deep Tunnel:-67', c.events)

    def test_flies_to_far_targets_instead_of_jumping(self):
        c = Companion()
        start = run(c, snap(), 1).pos
        c.go(point=(0.0, 2.0, 70.0))
        v = run(c, snap(), 0.5)
        self.assertLess(dist(v.pos, start), 6.0)            # half a second in: still close, accelerating
        speeds = []
        for _ in range(240):                                # 4 seconds
            before = c.pos
            v = c.update(snap(), 1 / 60)
            speeds.append(dist(v.pos, before) * 60)
        self.assertLessEqual(max(speeds), 10.5)             # never faster than the travel speed
        self.assertGreater(max(speeds), 8.0)                # but clearly faster than a running player

    def test_trail_stays_in_the_world_when_the_camera_turns(self):
        from game_assistant.core.lookat import project
        c = Companion()
        run(c, snap(), 1)
        c.go(point=(6.0, 2.0, 30.0))
        run(c, snap(), 1.5)
        self.assertGreater(len(c.trail), 10)
        turned = Camera((0.0, 3.0, -4.0), (0.5, -0.2, 0.84), (0.86, 0.0, -0.51), (0.1, 0.98, 0.17), 48.0, 16 / 9)
        s2 = Snapshot('test', 1, True, Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive'), turned, (), None, screen=SCREEN)
        v = c.update(s2, 1 / 60)
        newest_dot = v.trail[0]
        _, pos, _ = c.trail[-2]                                # the trail point that dot is drawn from
        x, y, depth = project(turned, pos)
        self.assertAlmostEqual(newest_dot[0], (x + 1) / 2 * 1920, delta=1.0)  # drawn where it is in the world now
        self.assertAlmostEqual(newest_dot[1], (1 - y) / 2 * 1080, delta=1.0)

    def test_guide_stays_where_the_player_can_see_it(self):
        def hill(rays):  # flat ground; a 30 m high ridge at z = 40 that hides everything behind it
            out = []
            for a, b in rays:
                if (a[2] - 40) * (b[2] - 40) < 0:
                    k = (40 - a[2]) / (b[2] - a[2])
                    p = tuple(a[i] + (b[i] - a[i]) * k for i in range(3))
                    if p[1] <= 30:
                        out.append(RayHit(a, b, True, p, (0.0, 0.0, -1.0)))
                        continue
                if a[1] > 0 >= b[1]:
                    k = a[1] / (a[1] - b[1])
                    out.append(RayHit(a, b, True, tuple(a[i] + (b[i] - a[i]) * k for i in range(3)), (0.0, 1.0, 0.0)))
                    continue
                out.append(RayHit(a, b, False))
            return out
        c = Companion(raycast=hill)
        run(c, snap(), 1)
        c.guide((0.0, 0.0, 500.0), 'Behind The Ridge')
        v = run(c, snap(), 8)
        self.assertLess(v.pos[2], 40)       # waits on this side of the ridge, in sight
        self.assertGreater(v.pos[2], 14)    # but still well ahead

    def test_edge_marker_when_it_is_behind_you(self):
        c = Companion()
        run(c, snap(), 1)
        c.go(point=(0.0, 2.0, -40.0))       # behind the camera
        v = run(c, snap(), 6)
        self.assertFalse(v.visible)
        self.assertIsNotNone(v.edge)
        self.assertTrue(0 <= v.edge[0] <= 1920 and 0 <= v.edge[1] <= 1080)

    def test_guide_goes_around_a_cliff_not_over_it(self):
        def ground(x, z):  # a plateau ending in a 30 m cliff at z = 40; a ramp down at x 30-45
            if z < 40:
                return 0.0
            if 30 <= x <= 45 and z <= 100:
                return -30.0 * (z - 40) / 60
            return -30.0

        def world(rays):
            out = []
            for a, b in rays:
                if abs(a[0] - b[0]) < 1e-6 and abs(a[2] - b[2]) < 1e-6 and a[1] > b[1]:  # straight down
                    h = ground(a[0], a[2])
                    ramp = 30 <= a[0] <= 45 and 40 <= a[2] <= 100
                    n = (0.0, 0.894, -0.447) if ramp else (0.0, 1.0, 0.0)
                    out.append(RayHit(a, b, True, (a[0], h, a[2]), n) if b[1] <= h <= a[1] else RayHit(a, b, False))
                else:
                    out.append(RayHit(a, b, False))  # no walls; sight is clear
            return out

        c = Companion(raycast=world)
        c.plan_async = False
        run(c, snap(), 1)
        c.guide((0.0, -30.0, 140.0), 'Below The Cliff')
        v = run(c, snap(), 6)
        self.assertIsNotNone(c.route)
        below_edge = [q for q in c.route if 42 <= q[2] <= 88]  # beside the 30 m cliff (lower down, small drops are fine)
        self.assertTrue(below_edge, c.route)
        self.assertTrue(all(28 <= q[0] <= 47 for q in below_edge), c.route)  # down the ramp only
        self.assertGreater(v.pos[0], 15)                                     # leading toward the ramp

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
