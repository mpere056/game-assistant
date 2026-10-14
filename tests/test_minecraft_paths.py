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

    def test_never_into_powder_snow(self):
        # (it sinks the body and freezes it: a walk waded in, stuck, and the player lost 8 health, 2026-10-13)
        d, s = world(floor_y=2)
        for z in range(20):
            put(d, s, 10, 1, z, B.POWDER)              # a strip of powder snow in the ground...
            put(d, s, 10, 0, z, B.SOLID)               # ...one block deep
        p = B.find(grid(d, s), (3, 2, 5), (15, 2, 5), parkour=False)
        self.assertFalse(p.reaches_goal)                                # no way without jumping or breaking...
        hard = bytes(1 if k in (B.SOLID, B.POWDER) else 0 for k in d)
        p = B.find(B.Grid((0, 0, 0), s, bytes(d), hard), (3, 2, 5), (15, 2, 5), parkour=True)
        self.assertTrue(p.reaches_goal)                                 # ...a jump over it
        self.assertFalse(any(10 <= pt[0] < 11 and pt[1] < 2.0 for pt in p.cells), p.cells)

    def test_out_of_powder_snow_when_sunk_in_it(self):
        d, s = world(floor_y=1, sy=8)
        for x in range(4, 9):
            for z in range(3, 8):
                put(d, s, x, 1, z, B.POWDER)           # a patch, the player sunk in its middle
        hard = bytes(1 if k in (B.SOLID, B.POWDER) else 0 for k in d)
        p = B.find(B.Grid((0, 0, 0), s, bytes(d), hard), (6, 1, 5), (12, 1, 5), can_break=True)
        self.assertTrue(p.reaches_goal)
        self.assertTrue(any(a[0] == 'break' for a in p.actions))       # broken out of it

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
        self.assertFalse(B.find(g, (3, 1, 5), (15, 1, 5), parkour=False).reaches_goal)
        p = B.find(g, (3, 1, 5), (15, 1, 5), blocks=16, parkour=False)
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

    def test_a_long_drop_into_a_pool_is_taken_onto_ground_it_is_not(self):
        # a cliff 20 high (stand at y = 21 for x < 8); below it, a pool 2 deep at x 8-10 (else ground)
        d, s = world(sy=26, floor_y=1)
        for z in range(20):
            for x in range(0, 8):
                for y in range(1, 21):
                    put(d, s, x, y, z, B.SOLID)
            for x in (8, 9, 10):
                put(d, s, x, 0, z, B.SOLID)
                put(d, s, x, 1, z, B.WATER)
                put(d, s, x, 2, z, B.WATER)
        p = B.find(grid(d, s), (3, 21, 5), (9, 2, 5))
        self.assertTrue(p.reaches_goal)
        self.assertEqual(p.cells[-1][1], 2.0)           # in the pool's top
        d2, s2 = world(sy=26, floor_y=1)
        for z in range(20):
            for x in range(0, 8):
                for y in range(1, 21):
                    put(d2, s2, x, y, z, B.SOLID)
        self.assertFalse(B.find(grid(d2, s2), (3, 21, 5), (9, 1, 5)).reaches_goal)   # (onto ground: no)

    def test_to_the_surface_by_the_way_out(self):
        # a low cave (a roof at y 3-6) with an opening above x = 15
        d, s = world(sy=12)
        for z in range(20):
            for x in range(20):
                if x != 15:
                    for y in range(3, 7):
                        put(d, s, x, y, z, B.SOLID)
        sky = lambda x, z: 1 if x == 15 else 7          # (where the open sky starts in each column)
        p = B.find(grid(d, s), (3, 1, 5), (3, 7, 5), sky=sky)
        self.assertTrue(p.reaches_goal)
        self.assertEqual(int(p.cells[-1][0]), 15)


class ParkourTests(unittest.TestCase):
    def gap_world(self, width, landing_rise=0):
        """Ground at y 0 (stand at 1) with a deep gap `width` blocks wide at x 8.., the far side raised by
        landing_rise; the gap falls 6 into lava (a miss would be deadly)."""
        d, s = world(sy=14, floor_y=7)
        for z in range(20):
            for x in range(20):
                for y in range(0, 6):
                    put(d, s, x, y, z, B.SOLID)            # solid below the walking level (stand at 7)
            for x in range(8, 8 + width):
                for y in range(0, 7):
                    put(d, s, x, y, z, B.AIR)
                put(d, s, x, 0, z, B.DANGER)
            if landing_rise:
                for x in range(8 + width, 20):
                    put(d, s, x, 7, z, B.SOLID)
        return grid(d, s)

    def test_jumps_by_width(self):
        for width, sprint in ((1, False), (2, True), (3, True)):
            p = B.find(self.gap_world(width), (3, 7, 5), (15, 7, 5))
            self.assertTrue(p.reaches_goal, width)
            leaps = [a for a in p.actions if a[0] == 'parkour']
            self.assertEqual(len(leaps), 1, width)
            self.assertEqual((leaps[0][3], leaps[0][4]), (width, sprint))
            self.assertEqual(leaps[0][5] is not None, width == 3)   # a run-up for three
        self.assertFalse(B.find(self.gap_world(4), (3, 7, 5), (15, 7, 5)).reaches_goal)

    def test_a_sprint_jump_up_one_over_a_one_block_gap(self):
        p = B.find(self.gap_world(1, landing_rise=1), (3, 7, 5), (15, 8, 5))
        self.assertTrue(p.reaches_goal)
        leap = next(a for a in p.actions if a[0] == 'parkour')
        self.assertEqual(leap[2][1], 8)
        self.assertTrue(leap[4])


class MineReachTests(unittest.TestCase):
    def trunk(self, mined_up_to=0):
        """A trunk at (10, 1..6, 5) on flat ground (stand at y = 1); the logs below mined_up_to gone."""
        d, s = world(sy=12)
        for y in range(max(1, mined_up_to), 7):
            put(d, s, 10, y, 5, B.SOLID)
        return grid(d, s)

    def test_a_log_above_the_head_is_mined_from_the_ground(self):
        p = B.find(self.trunk(mined_up_to=4), (3, 1, 5), (10, 4, 5), kind='mine')   # 3 up (the ones below mined)
        self.assertTrue(p.reaches_goal)
        x, y, z = (int(v) for v in p.cells[-1])
        self.assertEqual(y, 1)                                           # on the ground...
        self.assertTrue(B.sees(self.trunk(mined_up_to=4), (x, y, z), (10, 4, 5)))   # ...in reach, in sight
        self.assertFalse(any(a[0] in ('place', 'break') for a in p.actions))

    def test_from_inside_the_trunk_once_its_bottom_is_gone(self):
        p = B.find(self.trunk(mined_up_to=5), (3, 1, 5), (10, 5, 5), kind='mine')   # 4 up, from under it
        self.assertTrue(p.reaches_goal)
        self.assertFalse(any(a[0] in ('place', 'break') for a in p.actions))

    def test_out_of_reach_or_out_of_sight_is_not_there(self):
        # (as the game reckons: 4.5 from the eye to a point on it, nothing solid between)
        g = self.trunk()
        self.assertTrue(B._there(g, (9, 1, 5, 0), (10, 4, 5), 'mine'))    # 3 up beside it: its side in sight
        self.assertTrue(B._there(g, (9, 1, 5, 0), (10, 6, 5), 'mine'))    # 5 up: its underside 3.5 off
        d, s = world(sy=12)
        for y in range(1, 9):
            put(d, s, 10, y, 5, B.SOLID)
        self.assertFalse(B._there(grid(d, s), (9, 1, 5, 0), (10, 8, 5), 'mine'))   # 7 up: out of reach
        d, s = world(sy=12)
        for y in (1, 2, 3):
            put(d, s, 9, y, 5, B.SOLID)                                  # a wall between
        put(d, s, 10, 2, 5, B.SOLID)
        put(d, s, 10, 1, 5, B.SOLID)
        self.assertFalse(B._there(grid(d, s), (8, 1, 5, 0), (10, 2, 5), 'mine'))

    def test_a_table_up_on_the_surface_from_the_bottom_of_a_pit(self):
        # (dug 4 down for stone, the crafting table up beside the pit: "near", but behind the pit's wall, and
        # it couldn't be opened, 2026-10-13): climbed to where it's in sight
        d, s = world(sy=12, floor_y=5)
        for y in range(0, 4):
            for z in range(20):
                for x in range(20):
                    put(d, s, x, y, z, B.SOLID)
        for y in range(1, 5):
            put(d, s, 8, y, 5, B.AIR)                                   # the pit
        put(d, s, 10, 5, 5, B.SOLID)                                     # the table, on the surface
        hard = bytes(2 if k == B.SOLID else 0 for k in d)
        g = B.Grid((0, 0, 0), s, bytes(d), hard)
        self.assertFalse(B._there(g, (8, 1, 5, 0), (10, 5, 5), 'mine'))
        p = B.find(g, (8, 1, 5), (10, 5, 5), can_break=True, kind='mine')
        self.assertTrue(p.reaches_goal)
        x, y, z = (int(v) for v in p.cells[-1])
        self.assertGreater(y, 1)

    def test_up_onto_a_tree_top_with_a_few_blocks(self):
        d, s = world(sy=14)
        for y in range(1, 6):
            put(d, s, 10, y, 5, B.SOLID)                 # a trunk, 5 high (stand on top at y = 6)
        for y in (4, 5, 6):
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if (dx, dz, y) != (0, 0, 4) and (dx, dz, y) != (0, 0, 5):
                        put(d, s, 10 + dx, y, 5 + dz, B.SOLID)   # leaves round its top
        hard = bytes(2 if k == B.SOLID else 0 for k in d)        # (leaves and logs: quick to break)
        g = B.Grid((0, 0, 0), s, bytes(d), hard)
        self.assertFalse(B.find(g, (3, 1, 5), (10, 6, 5), kind='exact').reaches_goal)          # no blocks: no way
        p = B.find(g, (3, 1, 5), (10, 6, 5), can_break=True, blocks=8, kind='exact')   # on the trunk's top
        self.assertTrue(p.reaches_goal)
        self.assertGreaterEqual(p.cells[-1][1], 6.0)
        self.assertTrue(any(a[0] == 'place' and a[4] == 'pillar' for a in p.actions))

    def test_up_onto_a_canopy_by_climbing_not_tunnelling(self):
        # a canopy of leaves 7 across, 3 deep (y 1-3) on the ground; the goal: on top of its middle (y 4)
        d, s = world(sy=12)
        for y in (1, 2, 3):
            for x in range(8, 15):
                for z in range(2, 9):
                    put(d, s, x, y, z, B.SOLID)
        hard = bytes(2 if k == B.SOLID else 0 for k in d)          # (leaves: quick to break)
        g = B.Grid((0, 0, 0), s, bytes(d), hard)
        p = B.find(g, (3, 1, 5), (11, 4, 5), can_break=True, kind='exact')
        self.assertTrue(p.reaches_goal)
        breaks = sum(1 for a in p.actions if a[0] == 'break')
        self.assertLessEqual(breaks, 6, p.actions)                    # up through it, then over the top

    def test_down_from_a_tree_top_to_the_ground(self):
        # on top of an oak (trunk y 1-6, leaves y 4-7 round it); "the ground": the land itself (feet at y 1)
        # anywhere, not the open sky the tree top already has (2026-10-13)
        d, s = world(sy=12)
        for y in range(4, 8):
            for x in range(8, 13):
                for z in range(3, 8):
                    put(d, s, x, y, z, B.SOLID)
        for y in range(1, 7):
            put(d, s, 10, y, 5, B.SOLID)
        hard = bytearray(1 if k == B.SOLID else 0 for k in d)               # (leaves: quick)
        for y in range(1, 7):
            hard[(y * s[2] + 5) * s[0] + 10] = 15                           # (the trunk: 3 s a log by hand)
        g = B.Grid((0, 0, 0), s, bytes(d), bytes(hard))
        p = B.find(g, (10, 8, 5), (10, 1, 5), can_break=True, kind='ground', ground=lambda x, z: 1)
        self.assertTrue(p.reaches_goal)
        self.assertEqual(int(p.cells[-1][1]), 1, p.cells)
        ys = [c[1] for c in p.cells]
        self.assertTrue(all(a - b <= 3 for a, b in zip(ys, ys[1:])), ys)   # no fall that hurts
        # already on it: there at once
        q = B.find(g, (3, 1, 3), (10, 1, 5), kind='ground', ground=lambda x, z: 1)
        self.assertTrue(q.reaches_goal)
        self.assertEqual(len(q.cells), 1)

    def test_up_out_of_a_canopy_from_inside_it(self):
        # stood in a pocket inside leaves 6 deep (y 1-6): up a staircase (break ahead-up and above, jump, turn
        # where needed) with no blocks; on the top (y 7) either way
        d, s = world(sy=14)
        for y in range(1, 7):
            for x in range(6, 17):
                for z in range(2, 9):
                    put(d, s, x, y, z, B.SOLID)
        put(d, s, 11, 1, 5, B.AIR)
        put(d, s, 11, 2, 5, B.AIR)
        hard = bytes(1 if k == B.SOLID else 0 for k in d)
        g = B.Grid((0, 0, 0), s, bytes(d), hard)
        for blocks in (0, 16):
            p = B.find(g, (11, 1, 5), (11, 7, 5), can_break=True, kind='exact', blocks=blocks)
            self.assertTrue(p.reaches_goal, blocks)
            ys = [c[1] for c in p.cells]
            self.assertEqual(ys, sorted(ys), (blocks, p.cells))       # up all the way, never across first
            self.assertLessEqual(len(p.cells), 8, (blocks, p.cells))


class DigToTests(unittest.TestCase):
    def solid_world(self, top):
        """Solid ground up to y = top (stand at top + 1), quick to break."""
        d, s = world(sy=16, floor_y=top + 1)
        for y in range(0, top + 1):
            for z in range(20):
                for x in range(20):
                    put(d, s, x, y, z, B.SOLID)
        return d, s

    def test_down_to_a_stone_quickly_never_through_it(self):
        # (a stone 5 down: the search ran out of time, the route ended in the stone, dug through it, and stood
        # there 10 s; from the bottom of the shaft planned the eye was "in" dirt the plan dug, 2026-10-13)
        d, s = self.solid_world(10)
        g = B.Grid((0, 0, 0), s, bytes(d), bytes(4 if k == B.SOLID else 0 for k in d))
        p = B.find(g, (8, 11, 8), (8, 5, 8), can_break=True, kind='mine', budget_s=2.0)
        self.assertTrue(p.reaches_goal)
        self.assertLess(p.expanded, 3000)
        self.assertNotIn((8, 5, 8), [tuple(a[2]) for a in p.actions if a[0] == 'break'])

    def test_a_line_of_sight_that_only_grazes_a_block_is_not_one(self):
        # (stood in a hole, the eye just below the ground's top, the crafting table on the ground 2 off: "in
        # sight" past a corner from the cell's middle, blocked from where the player stood, 2026-10-13)
        d, s = self.solid_world(4)
        put(d, s, 8, 3, 5, B.AIR)
        put(d, s, 8, 4, 5, B.AIR)                                       # the hole (stand at y 3)
        put(d, s, 6, 5, 5, B.SOLID)                                     # the table, on the ground
        g = B.Grid((0, 0, 0), s, bytes(d), bytes(2 if k == B.SOLID else 0 for k in d))
        self.assertFalse(B._there(g, (8, 3, 5, 0), (6, 5, 5), 'mine'))
        p = B.find(g, (8, 3, 5), (6, 5, 5), can_break=True, kind='mine')
        self.assertTrue(p.reaches_goal)
        self.assertGreater(int(p.cells[-1][1]), 3)                      # up out of the hole first
