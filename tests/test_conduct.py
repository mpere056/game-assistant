"""The one conductor (core/conduct.py, docs/CONDUCT.md): which single behaviour has the character."""
import unittest
from types import SimpleNamespace as NS

from game_assistant.core.conduct import AGAIN_AFTER, MIN_RUN, Conductor


def dec(what, target=1, why=''):
    return NS(what=what, target=NS(id=target), why=why)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class ConductorTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.rows = Clock(), []
        self.c = Conductor(log=self.rows.append, clock=self.clock)

    def test_the_order_has_it_till_danger_asks(self):
        self.assertEqual(self.c.choose(dec('none'))[0], 'order')
        action, r = self.c.choose(dec('fight', 3, 'after the player, up close'))
        self.assertEqual((action, r.kind, r.target), ('start', 'fight', 3))
        self.assertEqual(self.c.choose(dec('fight', 3))[0], 'keep')
        self.assertEqual(self.c.choose(dec('none'))[0], 'keep')          # (it runs till it ends)
        self.c.ended('won')
        self.assertEqual(self.c.choose(dec('none'))[0], 'order')
        self.assertEqual([r['event'] for r in self.rows], ['start', 'ended'])

    def test_a_higher_one_cuts_in_but_not_at_once_and_never_mid_jump(self):
        self.c.choose(dec('hold', 1))
        self.assertEqual(self.c.choose(dec('cover', 2))[0], 'keep')     # (not before MIN_RUN)
        self.clock.t = MIN_RUN + 0.1
        self.assertEqual(self.c.choose(dec('cover', 2), can_cut=False)[0], 'keep')   # (mid-jump)
        action, r = self.c.choose(dec('cover', 2))
        self.assertEqual((action, r.kind), ('start', 'cover'))
        self.clock.t += MIN_RUN + 0.1
        self.assertEqual(self.c.choose(dec('hold', 1))[0], 'keep')      # (lower: no)

    def test_one_that_got_nowhere_isnt_started_again_against_the_same(self):
        # (hold ground, "held off", the order on, hold ground again: a loop, 2026-10-16)
        self.c.choose(dec('hold', 7))
        self.c.ended('held_off')
        self.assertEqual(self.c.choose(dec('hold', 7))[0], 'order')
        self.assertEqual(self.c.choose(dec('hold', 8))[0], 'start')     # (another one: yes)
        self.c.ended('won')
        self.clock.t = AGAIN_AFTER + 1
        self.assertEqual(self.c.choose(dec('hold', 7))[0], 'start')     # (a while later: tried again)

    def test_no_survival_behaviour_mid_jump(self):
        self.assertEqual(self.c.choose(dec('cover', 2), can_cut=False)[0], 'order')


class GuardedStepTests(unittest.TestCase):
    def test_held_off_fails_the_step_at_its_target_and_the_plan_goes_round_it(self):
        # (they stay by the tree the shield errand was going to: that tree left out, another taken; the place stays
        # open, docs/CONDUCT.md)
        from game_assistant.core.crafting import Source
        from game_assistant.core.memory import WorldMemory
        from game_assistant.core.tasks import TaskRunner
        try:
            from test_tasks import World
        except ModuleNotFoundError:
            from tests.test_tasks import World
        w = World()
        w.memory = WorldMemory()
        r = TaskRunner(w)
        w.task_note = lambda event, **data: w.memory.couldnt_reach(data.get('what', ''), data.get('at'),
                                                                     data.get('frm'), data.get('reason', '')) \
            if event == 'unreachable' else None
        r.start([('gather', 'mc:oak_log', 1, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 1)
        r.update(w.snap(), 1 / 30, False)
        self.assertEqual(r.target[2], (0, 1, 1))
        r.guarded()
        self.assertFalse(r.active)
        self.assertEqual(r.outcome.reason.kind, 'danger')
        self.assertEqual(r.outcome.reason.get('at'), (0, 1, 1))
        self.assertTrue(w.memory.is_unreachable((0, 1, 1)))              # (that tree, a while)


class NoStartTests(unittest.TestCase):
    def test_one_that_cant_start_isnt_tried_again_at_once_against_any(self):
        # (two skeletons, each the nearest in turn: cover tried and not had every frame, the order stopped and
        # started again each time, 2026-10-16)
        clock = Clock()
        c = Conductor(clock=clock)
        c.choose(dec('cover', 1))
        c.ended('no_spot')
        self.assertEqual(c.choose(dec('cover', 2))[0], 'order')
        clock.t = 6.0
        self.assertEqual(c.choose(dec('cover', 2))[0], 'start')


class AppConductTests(unittest.TestCase):
    def app(self, cover=None):
        import time
        from game_assistant.core.interface import Camera, Entity, Player, Snapshot
        from game_assistant.core.melee import MeleeFighter
        try:
            from test_walk_app import make_app
        except ModuleNotFoundError:
            from tests.test_walk_app import make_app
        app = make_app()
        app.fighter = MeleeFighter(lambda e: {'ranged': 'skeleton' in (e.model or '')}, lambda h: 0,
                                   weapon_damage=lambda h: 5.0)

        class A:
            defend_on_walks = True

            def combat_profile(self, e):
                return {'ranged': 'skeleton' in (e.model or '')}

            def cover_spot(self, foes):
                return cover

            def terrain(self):
                return {}
        app.adapter = A()
        app._took_over = time.monotonic()
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)

        def snap(*mobs):
            return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 20.0, 20.0), cam, mobs, None)

        def mob(i, kind, z):
            return Entity(i, 1, f'minecraft:{kind}', (0.0, 0.0, z), (0.0, 1.0, z), 0.3, 1.95, True, False, 20.0, 20.0,
                          name=kind, extra={'aggressive': True, 'using_item': kind == 'skeleton'})
        return app, snap, mob

    def test_no_cover_near_the_order_isnt_stopped(self):
        app, snap, mob = self.app(cover=None)
        app.walk.active, app.walk.goal, app.walk.name = True, (0.0, 0.0, 30.0), 'oak log'
        self.assertFalse(app._conduct(snap(mob(5, 'skeleton', 14.0)), 1 / 30))
        self.assertTrue(app.walk.active)                                 # (not paused for nothing)
        self.assertIsNone(getattr(app, '_paused_order', None))

    def test_no_order_no_taking_over_unless_chosen(self):
        # ("It shouldn't take over control unless I've given it a command it's trying to finish up", 2026-10-17;
        # guarding the player away from the controls is theirs to choose: guard_when_idle)
        app, snap, mob = self.app()
        self.assertFalse(app._conduct(snap(mob(1, 'zombie', 4.0)), 1 / 30))   # (they're at the controls)
        app._took_over -= 10.0
        self.assertFalse(app._conduct(snap(mob(1, 'zombie', 4.0)), 1 / 30))   # (away a while: still theirs)
        app.cfg = {'guard_when_idle': True}
        self.assertTrue(app._conduct(snap(mob(1, 'zombie', 4.0)), 1 / 30))    # (chosen: guarded)
        self.assertEqual(app.conductor.running.kind, 'fight')

    def test_on_a_fight_order_only_another_up_close_is_the_conductors(self):
        # (a gear errand fought whatever faced the player on the way, outside the conductor, 2026-10-16)
        app, snap, mob = self.app()
        app.walk.active, app.walk.goal, app.walk.name = True, (0.0, 0.0, 14.0), 'Skeleton'
        app._fight_after = app._order_target = 5
        self.assertFalse(app._conduct(snap(mob(5, 'skeleton', 14.0)), 1 / 30))   # (the one it's for: the order's)
        self.assertTrue(app.walk.active)
        self.assertTrue(app._conduct(snap(mob(5, 'skeleton', 14.0), mob(6, 'zombie', 3.0)), 1 / 30))
        self.assertEqual((app.conductor.running.kind, app.conductor.running.target), ('fight', 6))
        self.assertEqual(app._paused_order['walk'][3], 'fight')        # (then on to the skeleton, to fight it)

    def test_cover_that_doesnt_hide_is_given_up_and_not_taken_again(self):
        # (in "cover", seen and shot from 20 health to 4, 2026-10-16)
        import time
        app, snap, mob = self.app(cover=(3.0, 0.0, -2.0))
        app._took_over = 0.0                              # (the player's not at the controls)
        app.walk.active, app.walk.goal, app.walk.name = True, (0.0, 0.0, 30.0), 'stone'
        s = snap(mob(5, 'skeleton', 12.0))
        self.assertTrue(app._conduct(s, 1 / 30))
        self.assertEqual(app.conductor.running.kind, 'cover')
        app._walk_cmd, app.walk.active = None, False      # (there: the walk to it's over)
        app._sight_cache = (time.monotonic() + 100.0, {5: True})   # (and the skeleton still sees the player)
        for _ in range(40):
            app._conduct(s, 1 / 30)
            if app.conductor.running is None or app.conductor.running.since > 0.5:
                break
        self.assertIn((3, -2), getattr(app, '_bad_cover', {}))      # (that spot: not again a while)
        app._conduct(s, 1 / 30)                                      # (the next frame: cover again, elsewhere)
        self.assertEqual(app.adapter.cover_avoid, {(3, -2)})        # (asked for without that one)
