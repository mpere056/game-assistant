"""Whole orders in the test world (tests/sim/world.py, docs/AGENT.md stage 3): the real planners, end to end."""
import unittest

try:
    from sim.world import AIR, M, SimWorld, run_goal
except ModuleNotFoundError:                 # (run as tests.<module>)
    from tests.sim.world import AIR, M, SimWorld, run_goal


def plains(trees=((8, 8), (30, 25), (22, 6))) -> SimWorld:
    """Grass at y 19 (stand at 20) over dirt and stone, bedrock at 0; oaks; iron ore at y 10-11, coal at 14."""
    w = SimWorld()
    w.fill(0, 39, 0, 0, 0, 39, M + 'bedrock')
    w.fill(0, 39, 1, 15, 0, 39, M + 'stone')
    w.fill(0, 39, 16, 18, 0, 39, M + 'dirt')
    w.fill(0, 39, 19, 19, 0, 39, M + 'grass_block')
    for x, z in trees:
        w.fill(x - 2, x + 2, 23, 25, z - 2, z + 2, M + 'oak_leaves')
        w.fill(x, x, 20, 25, z, z, M + 'oak_log')
    for c in ((20, 10, 20), (21, 10, 20), (20, 11, 20), (21, 11, 21)):
        w.put(c, M + 'iron_ore')
    for c in ((12, 14, 30), (13, 14, 30)):
        w.put(c, M + 'coal_ore')
    w.player = (15, 20, 15)
    return w


class SimOrderTests(unittest.TestCase):
    def test_an_iron_pickaxe_from_nothing(self):
        w = plains()
        out, goal = run_goal(w, M + 'iron_pickaxe')
        self.assertTrue(out.ok, (out.brief(), w.trace[-6:], goal.log))
        self.assertEqual(w.inv[M + 'iron_pickaxe'], 1)
        self.assertLess(w.t, 600, w.trace)

    def test_down_a_hole_a_new_crafting_table_there_beats_climbing_back(self):
        # (2026-10-15: 14 down a shaft, the crafting table up top, 5 planks held: "I can't get to where I can
        # reach the crafting table", and the order was over)
        w = plains()
        w.fill(15, 15, 8, 19, 15, 15, AIR)                              # the shaft
        w.put((16, 20, 15), M + 'crafting_table')                        # the table, up beside its top
        w.memory.put_down(M + 'crafting_table', (16, 20, 15))
        w.player = (15, 8, 15)
        w.inv.update({M + 'dirt': 4, M + 'cobblestone': 11, M + 'wooden_pickaxe': 1, M + 'stick': 6,
                      M + 'oak_planks': 5})
        out, goal = run_goal(w, M + 'stone_pickaxe')
        self.assertTrue(out.ok, (out.brief(), w.trace, goal.log))
        self.assertIn('crafting_table', [t[2] for t in w.trace if t[1] == 'station'])   # a new one, down here
        self.assertLess(w.player[1], 12)                                 # (never climbed back)
        self.assertLess(w.t, 30, w.trace)

    def test_out_of_a_hole_with_nothing_but_a_pickaxe(self):
        # sticks to be made, the logs up on the surface: out of the hole first (blocks from round it, or dug)
        w = plains()
        w.fill(15, 15, 8, 19, 15, 15, AIR)
        w.player = (15, 8, 15)
        w.inv.update({M + 'wooden_pickaxe': 1})
        out, goal = run_goal(w, M + 'stone_pickaxe')
        self.assertTrue(out.ok, (out.brief(), w.trace, goal.log))
        self.assertEqual(w.inv[M + 'stone_pickaxe'], 1)


if __name__ == '__main__':
    unittest.main()


class PlanOrderTests(unittest.TestCase):
    def test_a_station_is_put_down_just_before_it_is_used(self):
        # (the furnace put down by the crafting table before the iron was dug, 23 blocks below: climbed back up to
        # smelt, 2026-10-16)
        try:
            from sim.world import plan_for
        except ModuleNotFoundError:
            from tests.sim.world import plan_for
        steps = plan_for(plains(), M + 'iron_pickaxe', 1)
        kinds = [(s[0], s[1] if isinstance(s[1], str) else s[1].result) for s in steps]
        furnace = kinds.index(('station', M + 'furnace'))
        self.assertGreater(furnace, kinds.index(('gather', M + 'raw_iron')))
        self.assertEqual(steps[furnace + 1][0], 'smelt')
