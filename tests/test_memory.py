"""The world memory (core/memory.py) and skills' typed outcomes (core/skills.py), docs/AGENT.md stage 1."""
import unittest

from game_assistant.core import skills as S
from game_assistant.core.crafting import Source
from game_assistant.core.memory import UNREACHABLE_FOR, WorldMemory
from game_assistant.core.tasks import TaskRunner

try:
    from test_tasks import StationWorld, World
except ModuleNotFoundError:                 # (run as tests.<module>)
    from tests.test_tasks import StationWorld, World


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class MemoryTests(unittest.TestCase):
    def test_a_station_put_down_is_told_with_how_far_up(self):
        # (14 down, the order had no idea it had put a crafting table up top, 2026-10-15)
        m = WorldMemory()
        m.put_down('minecraft:crafting_table', (-12, 67, -38))
        self.assertIn('crafting table put down at -12 67 -38 (14 above you)', m.summary((-12.4, 53.0, -35.6)))
        self.assertEqual([p.at for p in m.stations('minecraft:crafting_table')], [(-12, 67, -38)])
        m.dug_at((-12, 67, -38))                                       # mined: no station there any more
        self.assertEqual(m.stations(), [])

    def test_what_couldnt_be_got_at_is_left_out_for_a_while(self):
        c = Clock()
        m = WorldMemory(clock=c)
        m.couldnt_reach('iron ore', (12, 56, -5), (16.0, 55.0, -5.0), 'behind a wall')
        self.assertTrue(m.is_unreachable((12, 56, -5)))
        self.assertTrue(m.is_unreachable((12, 56, -5), frm=(16.5, 55.0, -5.0)))
        self.assertFalse(m.is_unreachable((12, 56, -5), frm=(30.0, 55.0, -5.0)))   # (from elsewhere: maybe)
        c.t = UNREACHABLE_FOR + 1
        self.assertFalse(m.is_unreachable((12, 56, -5)))                 # (the world changes: tried again)

    def test_a_give_up_as_a_reason(self):
        r = S.route_reason('Crafting Table', None, {'found': 'needs blocks', 'blocks_it_would_take': 13,
                                                    'blocks_to_build_with': 7})
        self.assertEqual(r.kind, S.UNREACHABLE)
        self.assertEqual(r.get('route')['blocks_it_would_take'], 13)
        self.assertEqual(S.route_reason('x', None, {'found': 'no way'}).kind, S.STUCK)


class OutcomeTests(unittest.TestCase):
    def test_a_task_ends_with_a_typed_outcome(self):
        w = World()
        w.logs = 0                                                      # nothing to gather
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 1, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 1)
        for _ in range(5):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertFalse(r.outcome.ok)
        self.assertEqual(r.outcome.reason.kind, S.NO_TARGET)
        self.assertEqual(r.outcome.reason.get('what'), 'mc:oak_log')
        self.assertIn("can't find", r.outcome.line)

    def test_a_station_put_down_is_noted_and_the_task_done(self):
        notes = []

        class W(StationWorld):
            def task_note(self, event, **data):
                notes.append((event, data))
        w = W({})
        r = TaskRunner(w)
        r.start([('station', 'mc:crafting_table')], 'mc:crafting_table', 1)
        for _ in range(200):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
            if not r.active:
                break
        self.assertTrue(r.outcome.ok)
        self.assertEqual(notes, [('put_down', {'item': 'mc:crafting_table', 'at': (0, 0, 2)})])


if __name__ == '__main__':
    unittest.main()
