"""Routes done move by move (games/minecraft/moving.py, Baritone's way), end to end in a world with the game's
movement (tests/sim/physics.py): planned by pathing.py, done by the mover's keys and view, a frame late."""
import unittest

from game_assistant.games.minecraft import blocks as B
from game_assistant.games.minecraft import pathing as P

try:
    from sim.physics import World, run
except ModuleNotFoundError:                 # (run as tests.<module>)
    from tests.sim.physics import World, run


def flat(h=16, n=32):
    """Ground at y 3 (stand at 4), the player at (2.5, 4, 5.5) facing +x."""
    w = World((n, h, n))
    w.fill(0, n - 1, 0, 3, 0, n - 1, B.SOLID)
    w.pos = [2.5, 4.0, 5.5]
    w.yaw = -90.0
    return w


def go(w, goal, kind='exact', seconds=20.0, **kw):
    g = w.grid()
    p = P.find(g, w.feet(), goal, kind=kind, **kw)
    assert p is not None and p.reaches_goal, p
    status, mv = run(w, p.moves, g, seconds=seconds)
    return p, status, mv


class WalkTests(unittest.TestCase):
    def test_flat_at_a_run(self):
        w = flat()
        p, status, mv = go(w, (14, 4, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[0], 13)                # (in reach of it: the goal's 'exact' one off)
        self.assertLess(w.t, 3.0)                         # (11 blocks: run)

    def test_round_corners(self):
        w = flat()
        w.fill(6, 6, 4, 5, 0, 5, B.SOLID, 127)
        w.fill(6, 12, 4, 5, 7, 7, B.SOLID, 127)
        p, status, mv = go(w, (10, 4, 2))
        self.assertEqual(status, 'done', mv.why)
        self.assertLessEqual(abs(w.feet()[0] - 10) + abs(w.feet()[2] - 2), 1)

    def test_up_steps_and_down_a_fall(self):
        w = flat()
        for i in range(3):
            w.fill(6 + i, 12, 4, 4 + i, 0, 31, B.SOLID)
        p, status, mv = go(w, (12, 7, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[1], 7)
        w = flat(h=20)
        w.fill(0, 31, 4, 9, 0, 31, B.AIR)
        w.fill(0, 6, 4, 6, 0, 31, B.SOLID)               # a ledge 3 high (stand at 7)
        w.pos = [2.5, 7.0, 5.5]
        p, status, mv = go(w, (14, 4, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.hurt, 0.0)

    def test_a_fall_onto_a_narrow_ledge_is_steered_onto_it(self):
        # (Baritone's: the fall's steered to the ledge's middle as it's made; not walked on past it)
        w = flat(h=20)
        w.fill(0, 31, 0, 12, 0, 31, B.AIR)
        w.fill(0, 6, 0, 9, 0, 31, B.SOLID)               # a cliff (stand at 10)
        w.fill(7, 7, 0, 6, 0, 31, B.SOLID)               # a ledge one wide, 3 down (stand at 7)
        w.fill(8, 31, 0, 0, 0, 31, B.SOLID)              # far below past it
        w.pos = [2.5, 10.0, 5.5]
        p, status, mv = go(w, (7, 7, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (7, 7, 5))
        self.assertGreater(w.lowest, 6.5)

    def test_a_slant_way_begun_from_beside_its_start(self):
        # (stood on a bed beside the way's first block: its diagonal gave up at once, planned again 10 times a second
        # for 15 s, 2026-10-17)
        w = flat()
        g = w.grid()
        p = P.find(g, (2, 4, 5), (8, 4, 11), kind='exact')
        self.assertEqual(p.moves[0].kind, 'diagonal')
        w.pos = [1.6, 4.0, 5.5]                          # (beside its start, not in it)
        status, mv = run(w, p.moves, g, seconds=10.0)
        self.assertEqual(status, 'done', mv.why)

    def test_swims_across(self):
        w = flat()
        w.fill(6, 10, 1, 3, 0, 31, B.WATER)
        p, status, mv = go(w, (14, 4, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[1], 4)

    def lake(self):
        """Banks 9 high (stand at 9) either side of a lake 8 deep right across, its top at 8 (swum at 8)."""
        w = flat(h=20)
        w.fill(0, 31, 4, 8, 0, 31, B.SOLID)
        w.fill(5, 25, 1, 8, 0, 31, B.WATER)
        w.pos = [2.5, 9.0, 4.5]
        return w

    def test_across_a_deep_lake_the_slant_way(self):
        # (a lake swum across slantwise: the diagonal moves didn't hold jump and sank, at a run they dived;
        # off the route again and again, the air nearly gone, 2026-10-17)
        w = self.lake()
        p, status, mv = go(w, (27, 9, 20), kind='cell', seconds=40.0)
        self.assertTrue(any(m.kind == 'diagonal' for m in p.moves))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (27, 9, 20))
        self.assertGreater(w.lowest_air, 200)            # (the head up all the way)
        self.assertEqual(w.drowned, 0.0)

    def test_sunk_below_the_way_up_to_it_first(self):
        # (sunk 3 under the way's start, planned from the top: off the route every half second, sinking on, the
        # air nearly gone, 2026-10-17)
        w = self.lake()
        w.pos = [15.5, 5.0, 15.5]                        # (3 under the top...)
        w.air = 100                                      # (...the air running out)
        g = w.grid()
        p = P.find(g, (15, 8, 15), (27, 9, 27), kind='cell')
        self.assertEqual(p.moves[0].kind, 'diagonal')     # (as in the game: it gave up at once, off its blocks)
        status, mv = run(w, p.moves, g, seconds=40.0)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (27, 9, 27))
        self.assertEqual(w.drowned, 0.0)

    def test_out_of_water_where_nothing_need_be_dug(self):
        # (dug into a bank from the water, bobbing: 25 times as long as on dry ground, 30 s, 2026-10-17)
        w = flat()
        w.fill(6, 12, 1, 3, 0, 31, B.WATER)              # a pool (swum at 3), the bank past x 12 (stand at 4)
        w.fill(13, 13, 4, 4, 0, 6, B.SOLID)              # a block on the bank's edge near (to dig, stepping out)
        w.pos = [11.5, 3.0, 5.5]
        p = P.find(w.grid(), (11, 3, 5), (16, 4, 5), kind='exact', can_break=True)
        self.assertTrue(p.reaches_goal)
        self.assertFalse(any(m.breaks for m in p.moves), [(m.kind, m.dest, m.breaks) for m in p.moves])

    def test_up_a_ladder(self):
        w = flat()
        w.fill(9, 31, 4, 8, 0, 31, B.SOLID, 127)
        w.fill(8, 8, 4, 8, 5, 5, B.CLIMB)
        p, status, mv = go(w, (12, 9, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[1], 9)


class JumpTests(unittest.TestCase):
    """The user: "running jumps are an important mechanic for us to use"."""

    def test_over_one_two_and_three(self):
        for gap, dist, sprint in ((1, 2, False), (2, 3, False), (3, 4, True)):
            w = flat()
            w.fill(6, 6 + gap - 1, 1, 3, 0, 31, B.AIR)   # a gap 3 deep
            p, status, mv = go(w, (14, 4, 5))
            leaps = [m for m in p.moves if m.kind == 'parkour']
            self.assertEqual([(m.get('dist'), m.get('sprint')) for m in leaps], [(dist, sprint)])
            self.assertEqual(status, 'done', (gap, mv.why))
            self.assertEqual(w.lowest, 4.0, gap)         # (never down in the gap)

    def test_up_one_over_a_gap(self):
        w = flat()
        w.fill(6, 6, 1, 3, 0, 31, B.AIR)
        w.fill(7, 20, 4, 4, 0, 31, B.SOLID)
        p, status, mv = go(w, (12, 5, 5))
        self.assertTrue(any(m.kind == 'parkour' and m.get('ascend') for m in p.moves))
        self.assertEqual(status, 'done', mv.why)
        self.assertGreaterEqual(w.lowest, 4.0)

    def test_onto_a_one_block_rim_and_stopped_there(self):
        # (a jump onto a pit's rim came down near its far edge and slid off: 11 blocks down among zombies, the
        # player died there, 2026-10-16; and at a run, it overshot a one-block rim by three)
        w = flat(h=20)
        w.fill(0, 31, 0, 11, 0, 31, B.AIR)
        w.fill(0, 5, 0, 11, 0, 31, B.SOLID)              # stand at 12
        w.fill(7, 7, 0, 11, 0, 31, B.SOLID)              # the rim, one block, a gap before it, 11 down past it
        w.pos = [2.5, 12.0, 5.5]
        p, status, mv = go(w, (7, 12, 5))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (7, 12, 5))
        self.assertEqual(w.lowest, 12.0)

    def table(self):
        """The player's hill and crafting table (2026-10-16): stood on the hill's edge at (6, 9, 12); the table's
        top, one block alone, two across and three along, one lower (stand at (4, 8, 9)); between, ground 5
        down (too far to drop)."""
        w = flat(h=20)
        w.fill(0, 31, 4, 19, 0, 31, B.AIR)               # (ground: stand at 4)
        w.fill(0, 31, 4, 8, 12, 31, B.SOLID)             # the hill (stand at 9)
        w.fill(4, 4, 4, 7, 9, 9, B.SOLID)                # the table on its block (stand at 8)
        w.pos = [6.5, 9.0, 12.5]
        return w

    def test_any_way_down_onto_one_block_at_a_walk(self):
        # ("I can even jump back to the block from the crafting table with one sprinting jump... It can even do it
        # without a sprinting jump", 2026-10-16: the planner went round through the ravine, 17 moves)
        w = self.table()
        p, status, mv = go(w, (4, 8, 9), kind='cell')
        self.assertEqual([(m.kind, m.get('sprint')) for m in p.moves], [('leap', False)])
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (4, 8, 9))
        self.assertGreaterEqual(w.lowest, 8.0)
        self.assertEqual(w.hurt, 0.0)

    def test_any_way_up_from_one_block_at_a_run(self):
        w = self.table()
        w.pos = [4.5, 8.0, 9.5]
        w.yaw = 0.0                                      # (facing away from the hill: turned first)
        p, status, mv = go(w, (6, 9, 12), kind='cell')
        self.assertEqual([(m.kind, m.get('sprint')) for m in p.moves], [('leap', True)])
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (6, 9, 12))
        self.assertGreaterEqual(w.lowest, 8.0)

    def test_a_diagonal_gap_level(self):
        w = flat()
        w.fill(0, 31, 1, 3, 0, 31, B.AIR)
        w.fill(0, 31, 0, 0, 0, 31, B.SOLID)
        w.fill(1, 3, 1, 3, 4, 6, B.SOLID)                # a 3 by 3 here (stand at 4)...
        w.fill(6, 8, 1, 3, 8, 10, B.SOLID)               # ...and one across a gap the diagonal way
        p, status, mv = go(w, (7, 4, 9), kind='cell')
        self.assertEqual([m.kind for m in p.moves].count('leap'), 1)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (7, 4, 9))
        self.assertGreaterEqual(w.lowest, 4.0)

    def test_fewest_jumps_walks_round(self):
        # ("can you do it with just one jump?": answered from the quickest way's jumps, 2026-10-16)
        w = flat()
        w.fill(6, 6, 1, 3, 0, 20, B.AIR)                 # a gap with a way round it past z 20...
        w.fill(6, 6, 1, 2, 0, 20, B.WATER)               # ...water in it (a fall into it does no harm)
        g = w.grid()
        quick = P.find(g, w.feet(), (14, 4, 5), kind='exact')
        few = P.find(g, w.feet(), (14, 4, 5), kind='exact', jump_extra=P.FEWEST_JUMPS)
        self.assertEqual(sum(m.kind in ('parkour', 'leap') for m in quick.moves), 1)
        self.assertTrue(few.reaches_goal)
        self.assertEqual(sum(m.kind in ('parkour', 'leap') for m in few.moves), 0)

    def test_looking_straight_down_off_a_pillar(self):
        # (after a pillar the view stayed straight down: a jump off it went off the side, and the walk after
        # turned on and on, never seeing itself turn, 2026-10-16)
        w = self.table()
        w.pitch = 90.0
        w.yaw = 135.0                                    # (facing away from the table)
        p, status, mv = go(w, (4, 8, 9), kind='cell')
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (4, 8, 9))
        self.assertGreaterEqual(w.lowest, 8.0)
        w = flat()
        w.pitch = 90.0
        p, status, mv = go(w, (2, 4, 14))                # (a walk, its way off to the side)
        self.assertEqual(status, 'done', mv.why)
        self.assertLess(w.t, 6.0)

    def test_over_three_at_a_run_just_after_a_step_up(self):
        # (gone up 0.3 before the middle on a speed read too high after the step: 0.3 short, 5 down, 2026-10-16)
        w = flat()
        w.fill(5, 31, 4, 4, 0, 31, B.SOLID)              # a step up (stand at 5)...
        w.fill(9, 11, 1, 4, 0, 31, B.AIR)                # ...three on, a gap of three
        p, status, mv = go(w, (14, 5, 5))
        self.assertTrue(any(m.kind == 'parkour' and m.get('dist') == 4 for m in p.moves), p.moves)
        self.assertEqual(status, 'done', mv.why)
        self.assertGreaterEqual(w.lowest, 4.0)
        self.assertEqual(w.hurt, 0.0)
        up = [t for t in w.takeoffs if t[1] == 5.0 and 8.0 <= t[0] < 9.4]      # (the jump over the gap)
        self.assertEqual(len(up), 1, w.takeoffs)
        self.assertGreater(up[0][0], 8.5 + 0.4)          # (at the edge: never before the block's middle)

    def test_never_up_at_a_run_not_running(self):
        # (jumps over three went up not running, from a step down or a turn: short, the last into a ravine 35
        # down, dead, 2026-10-17)
        w = flat(h=40)
        w.fill(0, 31, 4, 33, 0, 31, B.AIR)
        w.fill(0, 31, 0, 3, 0, 31, B.AIR)
        w.fill(0, 7, 0, 3, 0, 31, B.SOLID)               # this side (stand at 4)...
        w.fill(11, 31, 0, 3, 0, 31, B.SOLID)             # ...a gap of three, nothing under it, then that side
        g = w.grid()
        p = P.find(g, w.feet(), (14, 4, 5), kind='exact')
        self.assertTrue(any(m.kind == 'parkour' and m.get('sprint') for m in p.moves))
        w.can_run = False                                # (the game won't run now)
        status, mv = run(w, p.moves, g, seconds=12.0)
        self.assertEqual(status, 'replan')
        self.assertEqual(mv.why, 'not running at the jump')
        self.assertGreaterEqual(w.lowest, 4.0)           # (never went up: stood at the edge)
        self.assertEqual(mv.failed[-1][0], 'jump')
        self.assertIsNone(P.find(g, w.feet(), (14, 4, 5), kind='exact', no_jump={
            (mv.failed[-1][1]['from'], mv.failed[-1][1]['to'])}).moves and None)

    def test_a_gap_a_fall_would_hurt_is_gone_round(self):
        def world(water):
            w = flat()
            w.fill(6, 6, 0, 3, 0, 12, B.AIR)             # a gap 4 deep (hurts), a way round past z 12
            if water:
                w.fill(6, 6, 0, 2, 0, 12, B.WATER)
            return w
        w = world(False)
        p = P.find(w.grid(), w.feet(), (14, 4, 5), kind='exact')
        self.assertFalse(any(m.kind in ('parkour', 'leap') for m in p.moves))
        w = world(True)                                  # (water in it: a fall does no harm, jumped)
        p = P.find(w.grid(), w.feet(), (14, 4, 5), kind='exact')
        self.assertTrue(any(m.kind in ('parkour', 'leap') for m in p.moves))

    def test_two_in_a_row(self):
        w = flat()
        w.fill(6, 6, 1, 3, 0, 31, B.AIR)
        w.fill(8, 9, 1, 3, 0, 31, B.AIR)                 # a one-block island between
        p, status, mv = go(w, (14, 4, 5))
        self.assertEqual(sum(1 for m in p.moves if m.kind == 'parkour'), 2)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.lowest, 4.0)


class BreakBuildTests(unittest.TestCase):
    def test_up_a_hill_dug_through(self):
        # (the user: "it still doesn't know how to climb up properly": a staircase dug up the hill, jumping in
        # place and stuck between, 2026-10-16)
        w = flat()
        w.fill(6, 31, 4, 9, 0, 31, B.SOLID, 2)
        p, status, mv = go(w, (12, 10, 5), can_break=True)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[1], 10)
        self.assertLess(w.t, 15.0)

    def test_through_a_wall(self):
        w = flat()
        w.fill(7, 8, 4, 6, 0, 31, B.SOLID, 2)
        p, status, mv = go(w, (12, 4, 5), can_break=True)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(len(w.broken), 4)

    def test_down_dug_straight_and_by_a_staircase_to_come_back_up(self):
        w = flat()
        p, status, mv = go(w, (2, 1, 5), can_break=True)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (2, 1, 5))
        w = flat(h=20)
        w.fill(0, 31, 0, 12, 0, 31, B.SOLID, 2)
        w.pos = [2.5, 13.0, 5.5]
        p, status, mv = go(w, (6, 6, 5), kind='mine', can_break=True, back=True)
        self.assertTrue(all(m.kind == 'descend' for m in p.moves), [m.kind for m in p.moves])
        self.assertEqual(status, 'done', mv.why)

    def test_up_a_cliff_with_blocks(self):
        w = flat()
        w.fill(8, 31, 4, 6, 0, 31, B.SOLID, 127)
        p, status, mv = go(w, (12, 7, 5), blocks=16)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet()[1], 7)
        self.assertGreaterEqual(len(w.placed), 2)

    def test_pillar_onto_a_column(self):
        w = flat()
        w.fill(8, 8, 4, 7, 5, 5, B.SOLID, 127)
        p, status, mv = go(w, (8, 8, 5), blocks=16)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.feet(), (8, 8, 5))

    def test_a_bridge_over_a_gap(self):
        w = flat()
        w.fill(6, 9, 0, 3, 0, 31, B.AIR)
        p, status, mv = go(w, (14, 4, 5), blocks=16, parkour=False)
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.lowest, 4.0)
        self.assertEqual(len(w.placed), 4)


    def test_a_gap_a_fall_would_hurt_bridged_with_blocks_held(self):
        # (the user: "it should be determining the most efficient, and most efficient is probably to bridge":
        # jumps allowed, a way round there; with blocks held the bridge is the quickest, without them the jump)
        w = flat()
        w.fill(6, 8, 0, 3, 0, 20, B.AIR)                 # three wide, 4 down (a fall hurts), round it past z 20
        p, status, mv = go(w, (14, 4, 5), blocks=16)
        self.assertEqual(sum(1 for m in p.moves if m.place), 3)
        self.assertFalse(any(m.kind in ('parkour', 'leap') for m in p.moves))
        self.assertEqual(status, 'done', mv.why)
        self.assertEqual(w.lowest, 4.0)
        w = flat()
        w.fill(6, 8, 0, 3, 0, 20, B.AIR)
        p = P.find(w.grid(), w.feet(), (14, 4, 5), kind='exact', blocks=0)
        self.assertTrue(any(m.kind == 'parkour' for m in p.moves))       # (no blocks: jumped, not 30 round)

class RouteKeepingTests(unittest.TestCase):
    def test_knocked_off_the_route_it_plans_again(self):
        w = flat()
        g = w.grid()
        p = P.find(g, w.feet(), (20, 4, 5), kind='exact')
        from game_assistant.games.minecraft.moving import Mover
        mv = Mover()
        mv.start(p.moves, g)
        for _ in range(15):
            it, st = mv.update(w.snap(), 1 / 30)
            w.apply(it, 1 / 30)
        w.pos = [w.pos[0], 4.0, 12.5]                    # (knocked 7 blocks aside)
        it, st = mv.update(w.snap(), 1 / 30)
        self.assertEqual(st, 'replan')


class WalkByMovesTests(unittest.TestCase):
    def test_the_walk_hands_its_frames_to_the_mover_and_arrives(self):
        from game_assistant.core.autowalk import AutoWalk
        from game_assistant.games.minecraft.moving import Mover
        w = flat()
        w.fill(6, 6, 1, 3, 0, 31, B.AIR)                 # a gap to jump on the way
        g = w.grid()
        p = P.find(g, w.feet(), (14, 4, 5), kind='exact')
        aw = AutoWalk(precise=True)
        aw.mover = Mover()
        m = lambda v: (-v[0], v[1], v[2])
        aw.start([], m(p.cells[-1]), 'Oak Log')
        aw.set_route([m(c) for c in p.cells], (), True, p.moves, g)
        for _ in range(30 * 8):
            it = aw.update(w.snap(), 1 / 30)
            w.apply(it, 1 / 30)
            if not aw.active:
                break
        self.assertIn('arrived', aw.events)
        self.assertEqual(w.lowest, 4.0)
        self.assertEqual(w.feet()[0], 13)

    def test_a_route_handed_over_again_whole_goes_on_from_where_the_feet_are(self):
        # (a long way planned further while walked: the walk hands the mover all of it again)
        from game_assistant.games.minecraft.moving import Mover
        w = flat()
        g = w.grid()
        p = P.find(g, w.feet(), (25, 4, 5), kind='exact')
        mv = Mover()
        mv.start(p.moves, g)
        for _ in range(60):
            it, st = mv.update(w.snap(), 1 / 30)
            w.apply(it, 1 / 30)
        at = mv.i
        self.assertGreater(at, 9)                        # (past what's looked at ahead of where it was)
        mv.start(p.moves, g)                             # (handed over whole again)
        it, st = mv.update(w.snap(), 1 / 30)
        self.assertIsNone(st)
        self.assertGreaterEqual(mv.i, at - 1)

    def test_the_walk_ends_when_the_moves_are_done_with_work_in_them(self):
        # (the route's actions, done by the mover, were waited for at its end: 10 s a block short of cover, shot,
        # 2026-10-16)
        from game_assistant.core.autowalk import AutoWalk
        from game_assistant.games.minecraft import adapter as A
        from game_assistant.games.minecraft.moving import Mover
        w = flat()
        w.fill(7, 31, 4, 4, 0, 31, B.SOLID, 2)
        w.fill(8, 8, 5, 6, 5, 5, B.SOLID, 2)              # a block to break on the last step up
        g = w.grid()
        p = P.find(g, w.feet(), (8, 5, 5), kind='cell', can_break=True)
        self.assertTrue(p.moves[-1].breaks)
        aw = AutoWalk(precise=True)
        aw.mover = Mover()
        m = lambda v: (-v[0], v[1], v[2])
        aw.start([], m(p.cells[-1]), 'cover')
        aw.set_route([m(c) for c in p.cells], [A._action(a) for a in p.actions], True, p.moves, g)
        for i in range(30 * 8):
            it = aw.update(w.snap(), 1 / 30)
            w.apply(it, 1 / 30)
            if not aw.active:
                break
        self.assertIn('arrived', aw.events)
        self.assertLess(w.t, 4.0)
        self.assertEqual(w.feet(), (8, 5, 5))

    def test_a_route_planned_again_mid_dig_goes_on_with_the_block_under_way(self):
        # (a dig through stone re-planned every 1.6 s, each new route starting the move over: the block half
        # mined let go and begun again, 2026-10-16)
        from game_assistant.games.minecraft.moving import Mover
        w = flat()
        w.fill(4, 12, 4, 5, 0, 31, B.SOLID, 8)          # a wall to tunnel through, slow to break
        g = w.grid()
        p = P.find(g, w.feet(), (12, 4, 5), kind='exact', can_break=True)
        mv = Mover()
        mv.start(p.moves, g)
        for _ in range(40):
            it, st = mv.update(w.snap(), 1 / 30)
            w.apply(it, 1 / 30)
            if w.breaking is not None and w.breaking[1] > 5:
                break
        self.assertIsNotNone(mv.st.breaking)
        under_way, state = mv.current, mv.st
        q = P.find(w.grid(), w.feet(), (12, 4, 5), kind='exact', can_break=True)   # (planned again from here)
        mv.start(q.moves, w.grid())
        self.assertEqual((mv.current.kind, mv.current.src, mv.current.dest),
                         (under_way.kind, under_way.src, under_way.dest))
        self.assertIs(mv.st, state)                      # (not started over)
