"""The goal runner (core/goals.py, docs/AGENT.md stage 2): milestones, and when it plans again."""
import unittest

from game_assistant.core.crafting import CantMake, Recipe, Source
from game_assistant.core.goals import DRIFT_SLACK, GoalRunner, milestones
from game_assistant.core.skills import STUCK, UNREACHABLE, Outcome, Reason

PICK = Recipe('r:wp', 'mc:wooden_pickaxe', 1, ((), ), 1, 1)
STONE = Recipe('r:sp', 'mc:stone_pickaxe', 1, ((), ), 1, 1)
PLANKS = Recipe('r:p', 'mc:planks', 4, ((), ), 1, 1)
LOG = ('gather', 'mc:log', 2, Source('mc:log', ('mc:log',)))
COBBLE = ('gather', 'mc:cobblestone', 3, Source('mc:stone', ('mc:cobblestone',), 'mc:wooden_pickaxe'))
PLAN = [LOG, ('craft', PLANKS, 2), ('station', 'mc:crafting_table'), ('craft', PICK, 1), COBBLE,
        ('craft', STONE, 1)]


class GoalTests(unittest.TestCase):
    def test_milestones_are_the_tools_stations_and_the_goal(self):
        self.assertEqual(milestones(PLAN, 'mc:stone_pickaxe'), [2, 3, 5])   # (planks: on the way to them)

    def test_a_milestone_that_ran_long_is_planned_again_and_one_on_time_isnt(self):
        calls = []

        def planner(item, total, after):
            calls.append(after)
            return [COBBLE, ('craft', STONE, 1)]
        g = GoalRunner(planner, lambda step: 10.0)
        g.start('mc:stone_pickaxe', 1, PLAN)
        self.assertIsNone(g.step_done(0, 5.0))
        self.assertIsNone(g.step_done(1, 5.0))
        self.assertIsNone(g.step_done(2, 5.0))                          # (reckoned 30, took 15)
        self.assertIsNotNone(g.step_done(3, 10.0 * 2 + DRIFT_SLACK + 5))  # (reckoned 10: far over)
        self.assertEqual(len(calls), 1)

    def test_a_failure_is_planned_again_but_not_into_the_same_failure(self):
        same = [('station', 'mc:crafting_table'), ('craft', PICK, 1)]
        g = GoalRunner(lambda item, total, after: list(same))
        g.start('mc:wooden_pickaxe', 1, same)
        stuck = Outcome(False, Reason(STUCK, {'step': 'station crafting table'}), 'stuck')
        self.assertIsNone(g.failed(stuck))                              # (the same first step: nothing learned)
        self.assertFalse(g.active)
        g.start('mc:wooden_pickaxe', 1, same)
        there = Outcome(False, Reason(UNREACHABLE, {'step': 'station crafting table', 'at': (1, 2, 3)}), 'x')
        self.assertIsNotNone(g.failed(there))                           # (that one spot left out: another may do)

    def test_no_plan_from_here_is_over(self):
        def planner(item, total, after):
            raise CantMake('no way')
        g = GoalRunner(planner)
        g.start('mc:x', 1, [LOG])
        self.assertIsNone(g.failed(Outcome(False, Reason(STUCK, {'step': 'gather log'}), 'stuck')))
        self.assertFalse(g.active)

    def test_the_plans_end_short_of_the_goal_plans_on(self):
        # (a plan for what a step lacked, blocks to build the way with, ends short of the goal)
        rest = [[('craft', STONE, 1)], []]
        g = GoalRunner(lambda item, total, after: rest.pop(0))
        g.start('mc:stone_pickaxe', 1, [COBBLE])
        self.assertEqual(g.step_done(0, 1.0), [('craft', STONE, 1)])
        self.assertIsNone(g.step_done(0, 1.0))                          # (then met: over)
        self.assertFalse(g.active)


if __name__ == '__main__':
    unittest.main()
