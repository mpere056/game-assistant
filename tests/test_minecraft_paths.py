"""Walking in Minecraft: A* over the blocks (games/minecraft/blocks.py) in small built worlds."""
import unittest

from game_assistant.games.minecraft import blocks as B


def world(sx=20, sy=8, sz=20, floor_y=1):
    """A box with a stone floor at y = floor_y - 1 (stand at y = floor_y)."""
    data = bytearray(sx * sy * sz)
    for z in range(sz):
        for x in range(sx):
            data[((floor_y - 1) * sz + z) * sx + x] = B.SOLID
    return data, (sx, sy, sz)


def put(data, size, x, y, z, kind):
    sx, sy, sz = size
    data[(y * sz + z) * sx + x] = kind


def grid(data, size):
    return B.Grid((0, 0, 0), size, bytes(data))


class PathTests(unittest.TestCase):
    def test_flat_ground_straight_across(self):
        d, s = world()
        p = B.find(grid(d, s), (2, 1, 2), (15, 1, 2))
        self.assertTrue(p.reaches_goal)
        self.assertEqual(p.points[0], (2.5, 1.0, 2.5))
        self.assertAlmostEqual(p.length, 13, delta=1.6)
        self.assertEqual(p.jumps, [])

    def test_a_step_up_is_a_jump(self):
        d, s = world()
        for z in range(20):
            for x in range(10, 20):
                put(d, s, x, 1, z, B.SOLID)            # a one-block-high terrace from x = 10
        p = B.find(grid(d, s), (2, 1, 5), (15, 2, 5))
        self.assertTrue(p.reaches_goal)
        self.assertEqual(len(p.jumps), 1)
        self.assertEqual(p.jumps[0][1][1], 2.0)

    def test_round_a_wall_two_high(self):
        d, s = world()
        for z in range(0, 15):
            for y in (1, 2):
                put(d, s, 10, y, z, B.SOLID)           # a wall with a gap at z >= 15
        p = B.find(grid(d, s), (5, 1, 5), (15, 1, 5))
        self.assertTrue(p.reaches_goal)
        self.assertTrue(any(pt[2] >= 15 for pt in p.points))
        self.assertEqual(p.jumps, [])

    def test_never_through_lava_and_not_a_diagonal_corner(self):
        d, s = world()
        for z in range(0, 18):
            put(d, s, 10, 0, z, B.DANGER)              # a lava strip in the floor, a bridge at z >= 18
            put(d, s, 10, 1, z, B.AIR)
        p = B.find(grid(d, s), (5, 1, 5), (15, 1, 5))
        self.assertTrue(p.reaches_goal)
        for pt in p.points:
            self.assertFalse(int(pt[0]) == 10 and pt[2] < 18)

    def test_drops_up_to_three_blocks(self):
        d, s = world(floor_y=1)
        for z in range(20):
            for x in range(0, 8):
                for y in (1, 2, 3):
                    put(d, s, x, y, z, B.SOLID)        # a ledge 3 high (stand at y = 4) for x < 8
        p = B.find(grid(d, s), (3, 4, 5), (15, 1, 5))
        self.assertTrue(p.reaches_goal)
        self.assertEqual(p.points[-1][1], 1.0)
        self.assertEqual(p.jumps, [])

    def test_swims_when_it_must_and_climbs_a_ladder(self):
        d, s = world()
        for z in range(20):
            for x in range(8, 12):
                put(d, s, x, 0, z, B.WATER)            # a channel
                put(d, s, x, 1, z, B.WATER)
        p = B.find(grid(d, s), (3, 1, 5), (15, 1, 5))
        self.assertTrue(p.reaches_goal)
        d, s = world(sz=6)
        for y in range(1, 5):
            for z in range(6):
                for x in range(10, 20):
                    put(d, s, x, y, z, B.SOLID)         # a 4-high cliff...
            put(d, s, 9, y, 3, B.CLIMB)                 # ...with a ladder on its face
        p = B.find(grid(d, s), (3, 1, 3), (12, 5, 3))
        self.assertTrue(p.reaches_goal, p.points)
        self.assertGreaterEqual(p.points[-1][1], 5.0)

    def test_a_far_goal_ends_at_the_box_edge_to_plan_on(self):
        d, s = world()
        p = B.find(grid(d, s), (2, 1, 2), (60, 1, 2))
        self.assertFalse(p.reaches_goal)
        self.assertTrue(p.timed_out)
        self.assertGreater(p.points[-1][0], 15)

    def test_a_drop_onto_a_narrow_ledge_above_a_long_fall_is_not_taken(self):
        # walking off an edge carries the player on a block or two: a 3-block drop onto a ledge one block
        # wide lands past it, 6 blocks down (it hurt, 2026-10-13); three blocks wide is fine
        for width, ok in ((1, False), (3, True)):
            d, s = world(sy=12)
            for z in range(20):
                for x in range(0, 8):
                    for y in range(1, 7):
                        put(d, s, x, y, z, B.SOLID)    # a cliff: stand at y = 7 for x < 8
                for x in range(8, 8 + width):
                    for y in (1, 2, 3):
                        put(d, s, x, y, z, B.SOLID)    # a ledge: stand at y = 4
            p = B.find(grid(d, s), (3, 7, 5), (15, 1, 5))
            self.assertEqual(p.reaches_goal, ok, width)

    def test_wades_through_powder_snow(self):
        d, s = world(floor_y=2)
        for z in range(20):
            put(d, s, 10, 1, z, B.POWDER)              # a strip of powder snow in the ground...
            put(d, s, 10, 0, z, B.SOLID)               # ...one block deep
        p = B.find(grid(d, s), (3, 2, 5), (15, 2, 5))
        self.assertTrue(p.reaches_goal)
        self.assertTrue(any(10 <= pt[0] < 11 and pt[1] == 1.0 for pt in p.points), p.points)

    def test_keeps_off_a_cliff_edge_when_it_can(self):
        d, s = world(sy=12)
        for z in range(20):
            for x in range(20):
                for y in range(1, 7):
                    if z <= 10:
                        put(d, s, x, y, z, B.SOLID)    # a plateau (stand at 7) with a 6-high cliff at z = 10
        p = B.find(grid(d, s), (2, 7, 10), (17, 7, 10))
        self.assertTrue(p.reaches_goal)
        self.assertTrue(any(pt[2] < 10 for pt in p.points), p.points)   # a step in from the edge



def hgrid(data, size, hardness=15):
    """A grid whose solid blocks can be broken (hardness in tenths: stone 15)."""
    hard = bytes(hardness if k != B.AIR else 0 for k in data)
    return B.Grid((0, 0, 0), size, bytes(data), hard)


class BreakBuildTests(unittest.TestCase):
    def wall_world(self):
        d, s = world()
        for z in range(20):
            for x in (9, 10, 11):
                for y in range(1, 8):
                    put(d, s, x, y, z, B.SOLID)        # a wall right across, too high to climb
        return d, s

    def test_mines_through_a_wall(self):
        d, s = self.wall_world()
        self.assertFalse(B.find(grid(d, s), (3, 1, 5), (15, 1, 5)).reaches_goal)
        p = B.find(hgrid(d, s), (3, 1, 5), (15, 1, 5), can_break=True)
        self.assertTrue(p.reaches_goal)
        broken = [a[2] for a in p.actions if a[0] == 'break']
        self.assertEqual(len(broken), 6)                # a tunnel two high, three long
        self.assertEqual(broken[0][1], 2)               # the top block first
        self.assertFalse(any(a[0] == 'place' for a in p.actions))

    def test_never_breaks_next_to_water_or_under_sand(self):
        d, s = self.wall_world()
        for z in range(20):
            put(d, s, 12, 1, z, B.WATER)               # water just past the wall
        g = hgrid(d, s)
        p = B.find(g, (3, 1, 5), (15, 1, 5), can_break=True)
        for a in p.actions:
            if a[0] == 'break':
                x, y, z = a[2]
                self.assertNotEqual((x, y), (11, 1))    # (11, 1) touches the water

    def test_pillars_up_a_cliff_with_blocks(self):
        d, s = world(sy=12, sz=6)
        for z in range(6):
            for x in range(10, 20):
                for y in range(1, 5):
                    put(d, s, x, y, z, B.SOLID)        # a 4-high cliff, no ladder
        g = grid(d, s)                                 # (nothing breakable)
        self.assertFalse(B.find(g, (3, 1, 3), (14, 5, 3)).reaches_goal)
        p = B.find(g, (3, 1, 3), (14, 5, 3), blocks=16)
        self.assertTrue(p.reaches_goal)
        self.assertGreaterEqual(sum(1 for a in p.actions if a[0] == 'place' and a[4] == 'pillar'), 3)

    def test_bridges_a_gap_with_blocks(self):
        d, s = world(sy=12)
        for z in range(20):
            for x in (9, 10, 11):
                put(d, s, x, 0, z, B.AIR)              # a gap three wide, falling out of the box
        g = grid(d, s)
        self.assertFalse(B.find(g, (3, 1, 5), (15, 1, 5)).reaches_goal)
        p = B.find(g, (3, 1, 5), (15, 1, 5), blocks=16)
        self.assertTrue(p.reaches_goal)
        placed = [a for a in p.actions if a[0] == 'place']
        self.assertEqual([a[2][0] for a in placed], [9, 10, 11])
        self.assertEqual(placed[0][3], (8, 0, 5))       # against the block the player stands on
        self.assertTrue(all(a[4] == 'bridge' for a in placed))

    def test_digs_down(self):
        d, s = world(sy=12, floor_y=6)
        for z in range(20):
            for x in range(20):
                for y in range(0, 5):
                    put(d, s, x, y, z, B.SOLID)        # solid ground from y = 0 to 5: stand at 6
        p = B.find(hgrid(d, s), (5, 6, 5), (5, 3, 5), can_break=True)
        self.assertTrue(p.reaches_goal)
        self.assertTrue(all(a[0] == 'break' for a in p.actions))

    def test_a_block_goal_ends_beside_it_facing_an_open_side(self):
        # stone two blocks under the grass, right below the player: dig down beside it, not stop on top
        d, s = world(sy=12, floor_y=6)
        for z in range(20):
            for x in range(20):
                for y in range(0, 5):
                    put(d, s, x, y, z, B.SOLID)        # solid ground: stand at y = 6
        p = B.find(hgrid(d, s), (5, 6, 5), (5, 4, 5), can_break=True, kind='block')
        self.assertTrue(p.reaches_goal)
        x, y, z = (int(v) for v in p.cells[-1])
        self.assertEqual(abs(x - 5) + abs(z - 5), 1)    # right next to it...
        self.assertIn(4, (y, y + 1))                    # ...at the feet or the head
        self.assertFalse(any(a[0] == 'break' and a[2] == (5, 4, 5) for a in p.actions))   # and it's still there

    def test_a_column_goal_is_reached_at_any_height(self):
        d, s = world(sy=12)
        p = B.find(grid(d, s), (2, 1, 2), (15, 9, 2), kind='column')   # (a village's height isn't known)
        self.assertTrue(p.reaches_goal)

    def test_an_area_goal_is_reached_at_its_edge(self):
        d, s = world()
        p = B.find(grid(d, s), (2, 1, 2), (17, 1, 2), kind='column', box=((10, 0, 0), (20, 8, 20)))
        self.assertTrue(p.reaches_goal)
        self.assertEqual(int(p.cells[-1][0]), 10)       # the first block inside the village, not its middle
