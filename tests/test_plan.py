"""Plans (core/plan.py): orders of several parts, run step by step against a fake world."""
import unittest

from game_assistant.core.interface import Player, Snapshot
from game_assistant.core.plan import CIRCLE_TURN, PlanRunner, steps_from


class FakeWorld:
    """Characters by kind (name -> list of ids still alive) and places; records what the plan asks for."""

    def __init__(self, kinds=None, places=None):
        self.kinds = {k: list(v) for k, v in (kinds or {}).items()}
        self.places = places or {}
        self.calls, self.lines, self.near = [], [], {}   # near: kind -> id within any range asked

    def plan_target(self, target):
        if target.startswith('#entity '):
            return ((0, 0, 5), 'Godrick Soldier', int(target.split()[1]))
        if target in self.places:
            return (self.places[target], target, None)
        ids = self.kinds.get(target)
        return ((0, 0, 9), target, ids[0]) if ids else None

    def plan_find(self, kind, max_range, not_kinds):
        if kind in ('enemy', 'other'):
            for k, ids in self.kinds.items():
                if ids and k not in not_kinds:
                    return ((0, 0, 9), k, ids[0])
            return None
        if kind in self.near:
            return ((0, 0, 20), kind, self.near[kind])
        ids = self.kinds.get(kind)
        return ((0, 0, 9), kind, ids[0]) if ids else None

    def plan_walk(self, point, name, entity_id, fight):
        self.calls.append(('fight' if fight else 'walk', name, entity_id))

    def plan_stop(self):
        self.calls.append(('stop',))

    def plan_say(self, line):
        self.lines.append(line)

    def kill(self, entity_id):
        for ids in self.kinds.values():
            if entity_id in ids:
                ids.remove(entity_id)


SNAP = Snapshot('test', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, (), None)


def run(plan, seconds, fps=30, walking=False, fighting=False):
    out = []
    for _ in range(int(seconds * fps)):
        out.append(plan.update(SNAP, 1 / fps, walking, fighting))
    return out


class PlanTests(unittest.TestCase):
    def test_steps_from_the_models_json_are_checked(self):
        steps = steps_from([{'do': 'go', 'target': 'Groveside Cave', 'engage': 'all'},
                            {'do': 'dance'}, {'do': 'move', 'shape': 'zigzag', 'seconds': '5'},
                            {'do': 'fight', 'count': 'every'}, 'nonsense'])
        self.assertEqual([s.do for s in steps], ['go', 'move', 'fight'])
        self.assertEqual((steps[1].shape, steps[1].seconds), ('circle', 5.0))
        self.assertEqual(steps[2].count, 'one')

    def test_go_to_the_grace_killing_whatever_is_met_on_the_way(self):
        # "go to the nearest site of grace and kill any enemies you encounter along the way"
        w = FakeWorld(places={'nearest site of grace': (0, 0, 100)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'nearest site of grace', 'engage': 'all'}]), SNAP)
        self.assertEqual(w.calls[-1], ('walk', 'nearest site of grace', None))
        run(p, 1, walking=True)                       # nothing around: just walking
        w.kinds = {'Godrick Soldier': [7]}
        run(p, 0.1, walking=True)
        self.assertEqual(w.calls[-1], ('fight', 'Godrick Soldier', 7))
        w.kill(7)
        self.assertTrue(p.on_event('won'))
        self.assertEqual(w.calls[-1], ('walk', 'nearest site of grace', None))   # back on the way
        self.assertTrue(p.on_event('arrived'))
        self.assertFalse(p.active)
        self.assertIn('All done!', w.lines)

    def test_without_fighting_walks_past(self):
        w = FakeWorld(kinds={'Godrick Soldier': [7]}, places={'Groveside Cave': (0, 0, 50)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'Groveside Cave', 'engage': 'none'}]), SNAP)
        run(p, 1, walking=True)
        self.assertNotIn('fight', [c[0] for c in w.calls])

    def test_kill_everything_alternating_soldiers_and_the_rest(self):
        w = FakeWorld(kinds={'Godrick Soldier': [1, 2], 'Boar': [3], 'Wolf': [4]})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'fight', 'target': 'enemy', 'count': 'all',
                             'cycle': ['Godrick Soldier', 'other']}]), SNAP)
        order, seen = [], 0
        for _ in range(10):
            if len(w.calls) == seen or w.calls[-1][0] != 'fight':
                break                                 # no new fight: the step is over
            seen = len(w.calls)
            last = w.calls[-1]
            order.append(last[1])
            w.kill(last[2])
            p.on_event('won')
        self.assertEqual(order, ['Godrick Soldier', 'Boar', 'Godrick Soldier', 'Wolf'])
        self.assertFalse(p.active)

    def test_walk_until_a_soldier_is_seen_then_kill_that_one_then_go_back(self):
        w = FakeWorld(places={'Gatefront Ruins': (0, 0, 200), 'Groveside Cave': (0, 0, -50)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'Gatefront Ruins', 'until_see': 'Godrick Soldier'},
                            {'do': 'fight', 'target': 'found'},
                            {'do': 'go', 'target': 'Groveside Cave'}]), SNAP)
        run(p, 1, walking=True)
        w.near['Godrick Soldier'] = 42
        run(p, 0.1, walking=True)
        self.assertEqual(p.found, 42)
        self.assertEqual(w.calls[-1], ('fight', 'Godrick Soldier', 42))
        p.on_event('won')
        self.assertEqual(w.calls[-1], ('walk', 'Groveside Cave', None))

    def test_circle_then_line_for_their_seconds(self):
        w = FakeWorld()
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'move', 'shape': 'circle', 'seconds': 10},
                            {'do': 'move', 'shape': 'line', 'seconds': 2}]), SNAP)
        out = run(p, 9.9)
        self.assertTrue(all(i and i.keys == {'forward'} and abs(i.turn - CIRCLE_TURN / 30) < 1e-6 for i in out))
        run(p, 0.2)                                   # the switch to the line
        out = run(p, 1.6)
        lines = [i for i in out if i is not None]
        self.assertTrue(lines and all(i.turn == 0.0 for i in lines))
        run(p, 0.6)
        self.assertFalse(p.active)

    def test_the_player_taking_over_stops_the_plan(self):
        w = FakeWorld(places={'Groveside Cave': (0, 0, 50)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'Groveside Cave'}, {'do': 'wait', 'seconds': 2}]), SNAP)
        p.on_event('stopped:you')
        self.assertFalse(p.active)

    def test_an_unknown_place_is_skipped(self):
        w = FakeWorld(places={'Groveside Cave': (0, 0, 50)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'Atlantis'}, {'do': 'go', 'target': 'Groveside Cave'}]), SNAP)
        self.assertEqual(w.calls[-1], ('walk', 'Groveside Cave', None))
        self.assertTrue(any('Atlantis' in x for x in w.lines))


class RoutingTests(unittest.TestCase):
    def test_which_orders_go_to_the_planner(self):
        from game_assistant.app import needs_plan
        for text in ['go to the nearest site of grace and kill any enemies you encounter along the way',
                     'kill everything around here, alternating between soldiers then other things',
                     'walk in a circle for 10 seconds',
                     'walk to gatefront ruins until you encounter a soldier and kill it']:
            self.assertTrue(needs_plan(text), text)
        for text in ['kill that soldier', 'go to the nearest site of grace', 'kill all the soldiers around here',
                     'stop', 'attack the boar']:
            self.assertFalse(needs_plan(text), text)


class WanderTests(unittest.TestCase):
    def test_wanders_around_where_the_step_began_and_gives_up_in_time(self):
        from game_assistant.core.plan import SEARCH_LIMIT, WANDER_RADIUS
        w = FakeWorld(places={'Groveside Cave': (100.0, 0.0, 100.0)})
        p = PlanRunner(w)
        p.start(steps_from([{'do': 'go', 'target': 'Groveside Cave'},
                            {'do': 'move', 'shape': 'wander', 'until_see': 'Godrick Soldier'}]), SNAP)
        at_cave = Snapshot('test', 1, True, Player((100.0, 0.0, 100.0), None, 'alive'), None, (), None)
        p.update(at_cave, 1 / 30, True, False)
        p.on_event('arrived')                                  # at the cave: wander there
        p.update(at_cave, 1 / 30, False, False)
        target = p._wander_to
        self.assertLess(((target[0] - 100) ** 2 + (target[2] - 100) ** 2) ** 0.5, WANDER_RADIUS + 0.1)
        for _ in range(int((SEARCH_LIMIT + 2) * 10)):
            p.update(at_cave, 0.1, True, False)
        self.assertFalse(p.active)
        self.assertTrue(any("couldn't find" in x for x in w.lines))
