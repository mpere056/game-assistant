"""The app's walk step (App._walk_step) with the real companion and auto-walk, stand-in keys and no
game: an order starts guiding and walking, keys are held, the player's own key press hands control
back and releases every key."""
import os
import time
import unittest

from game_assistant.app import App
from game_assistant.core.autowalk import AutoWalk
from game_assistant.core.combat import Fighter
from game_assistant.core.companion import Companion
from game_assistant.core.nav import Route
from game_assistant.core.walk_commands import WalkAction
from tests.test_autowalk import World


class FakeKeys:
    def __init__(self):
        self.held, self.taps, self.released = set(), [], 0

    def set(self, actions):
        self.held = set(actions)

    def tap(self, a):
        self.taps.append(a)

    def release_all(self):
        self.held = set()
        self.released += 1


class FakeTurner:
    def __init__(self):
        self.turned = 0.0

    def turn(self, deg, yaw):
        self.turned += deg


def make_app():
    """(Callers run a second of frames first, as the real app has long been running before an order.)"""
    app = App.__new__(App)
    app.keys, app.turner = FakeKeys(), FakeTurner()
    app.walk = AutoWalk()
    app.fighter = Fighter(lambda model, anim: [])
    app._fight_after = None
    app.companion = Companion()
    app.companion.plan_async = False
    app.companion.route_fn = lambda s, g: Route([s, (0.0, 0.0, 20.0), g], True, 0)
    app._walk_cmd, app._walk_route, app._took_over = None, None, 0.0
    app.lines = []
    app._navi_line = lambda line, urgent=False: app.lines.append(line)
    app._fight_log = lambda row: None   # never into the real runtime/combat log
    app._walk_log = lambda row: None    # ...nor the walk log
    app._save_chains = lambda: None     # ...nor the learned combos
    app._conduct_log = lambda row: None   # ...nor the conductor's decisions
    from game_assistant.core.plan import PlanRunner
    app.plan, app._plan_cmd = PlanRunner(app), None
    app._los = (None, None)
    return app


def step(app, world, seconds, fps=30):
    for _ in range(int(seconds * fps)):
        s = world.snap()
        app.companion.update(s, 1 / fps)
        app._walk_step(s, 1 / fps)
        if app.keys.held:
            from game_assistant.core.autowalk import Intent
            world.apply(Intent(frozenset(app.keys.held), 0.0), 1 / fps)


class WalkAppTests(unittest.TestCase):
    def test_walk_log_records_the_route_and_progress(self):
        app = make_app()
        rows = []
        app._walk_log = rows.append
        w = World()
        step(app, w, 0.5)
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        step(app, w, 2)
        routes = [r['route'] for r in rows if 'route' in r]
        self.assertTrue(routes and routes[0]['waypoints'][-1] == [0.0, 0.0, 60.0], routes)
        self.assertTrue(all(r['pos'] and r['state'] for r in rows))

    def test_order_walks_with_navi_and_the_player_takes_over(self):
        app = make_app()
        w = World()
        step(app, w, 0.5)
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        step(app, w, 2)
        self.assertEqual(app.companion.mode, 'guide')
        self.assertTrue(app.walk.active)
        self.assertIn('forward', app.keys.held)
        self.assertGreater(w.pos[2], 3)
        app._player_key(0x57)          # the player presses W themselves
        step(app, w, 0.1)
        self.assertFalse(app.walk.active)
        self.assertEqual(app.keys.held, set())
        self.assertIn("Okay, you've got it!", app.lines)

    def test_not_in_front_releases_the_keys(self):
        app = make_app()
        w = World()
        step(app, w, 1.5)
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        step(app, w, 1)
        self.assertTrue(app.keys.held)
        w.focused = False
        step(app, w, 0.2)
        self.assertEqual(app.keys.held, set())
        self.assertIn("Click back into the game and I'll keep going.", app.lines)

    def test_stop_order(self):
        app = make_app()
        w = World()
        step(app, w, 1.5)
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        step(app, w, 1)
        app._walk_request('stop', 'stop')
        step(app, w, 0.1)
        self.assertFalse(app.walk.active)
        self.assertEqual(app.keys.held, set())
        self.assertEqual(app.companion.mode, 'follow')


class FightAppTests(unittest.TestCase):
    def test_fight_order_walks_up_then_fights_and_the_player_can_take_over(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        w = World()
        enemy = Entity(9, 1, 'c4300', (0.0, 0.0, 30.0), (0.0, 1.0, 30.0), 0.5, 2.0, True, False, 400, 400)
        w.entities = (enemy,)
        step(app, w, 1.5)
        app._walk_request('start', enemy.pos, 'Soldier', 9, 'fight')
        step(app, w, 0.5)
        self.assertTrue(app.walk.active)              # 30 m away: walk up first
        self.assertFalse(app.fighter.active)
        step(app, w, 12)
        self.assertTrue(app.fighter.active)           # arrived: fighting
        self.assertFalse(app.walk.active)
        app._player_key(0x57)
        step(app, w, 0.1)
        self.assertFalse(app.fighter.active)
        self.assertEqual(app.keys.held, set())
        self.assertIn("Okay, you've got it!", app.lines)

    def test_fight_order_close_by_starts_at_once(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        w = World()
        w.entities = (Entity(9, 1, 'c4300', (0.0, 0.0, 6.0), (0.0, 1.0, 6.0), 0.5, 2.0, True, False, 400, 400),)
        step(app, w, 1.5)
        app._walk_request('start', (0.0, 0.0, 6.0), 'Soldier', 9, 'fight')
        step(app, w, 0.1)
        self.assertTrue(app.fighter.active)
        self.assertFalse(app.walk.active)


class OrderSequenceAppTests(unittest.TestCase):
    def test_the_next_part_runs_when_the_fight_is_won(self):
        import time as _time
        from game_assistant.core.interface import Entity
        app = make_app()
        ran = []
        app._order_work = lambda text, **kw: ran.append(text)
        w = World()
        enemy = Entity(9, 1, 'c4300', (0.0, 0.0, 4.0), (0.0, 1.0, 4.0), 0.5, 2.0, True, False, 400, 400)
        w.entities = (enemy,)
        step(app, w, 1.5)
        app._then = ['kill any remaining soldiers']
        app._walk_request('start', enemy.pos, 'Boar', 9, 'fight')
        step(app, w, 0.2)
        self.assertTrue(app.fighter.active)
        w.entities = (Entity(9, 1, 'c4300', (0.0, 0.0, 4.0), (0.0, 1.0, 4.0), 0.5, 2.0, True, True, 0, 400),)
        step(app, w, 0.2)
        _time.sleep(0.1)                     # (the next part runs on a worker thread, as orders do)
        self.assertEqual(ran, ['kill any remaining soldiers'])
        self.assertIn('Got it! Now the next part!', app.lines)


class PlanAppTests(unittest.TestCase):
    def test_a_plan_moves_then_walks_and_the_player_can_take_over(self):
        from game_assistant.core.plan import steps_from
        app = make_app()
        app.resolver = type('R', (), {'character': lambda self, *a, **k: None,
                                      'handle': lambda self, t: type('A', (), {'kind': 'walk', 'point': (0.0, 0.0, 40.0),
                                                                               'name': 'Gatefront', 'entity_id': None,
                                                                               'options': []})(),
                                      'pending': None})()
        w = World()
        step(app, w, 0.5)
        app._plan_cmd = steps_from([{'do': 'move', 'shape': 'circle', 'seconds': 1},
                                    {'do': 'go', 'target': 'Gatefront'}])
        step(app, w, 0.5)
        self.assertTrue(app.plan.active)
        self.assertIn('forward', app.keys.held)            # circling
        step(app, w, 1.0)
        self.assertTrue(app.walk.active)                   # then the walk
        self.assertEqual(app.walk.name, 'Gatefront')
        app._player_key(0x57)
        step(app, w, 0.1)
        self.assertFalse(app.walk.active)
        self.assertFalse(app.plan.active)


class WallAppTests(unittest.TestCase):
    def test_a_walled_fight_goes_round_by_the_route_then_fights(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        w = World()
        enemy = Entity(9, 1, 'c4300', (0.0, 0.0, 6.0), (0.0, 1.0, 6.0), 0.5, 2.0, True, False, 400, 400)
        w.entities = (enemy,)
        step(app, w, 1.5)
        app._walk_request('start', enemy.pos, 'Soldier', 9, 'fight')
        step(app, w, 0.1)
        self.assertTrue(app.fighter.active)
        app.fighter.sees = lambda eid: app._los[1] if app._los[0] == eid else None   # (as App.__init__ does)
        app._los = (9, False)                            # a wall between
        app.fighter._detours = 3                         # its ways round it used up
        step(app, w, 1.5)
        self.assertTrue(app.walk.active)                 # 6 m away, but walking: round the wall first
        self.assertFalse(app.fighter.active)
        self.assertIn("There's a wall in the way. Going round!", app.lines)



class OnTheWayTests(unittest.TestCase):
    def test_an_enemy_coming_at_the_player_on_the_way_is_fought(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        w = World()
        far = Entity(9, 1, 'c4300', (0.0, 0.0, 40.0), (0.0, 1.0, 40.0), 0.5, 2.0, True, False, 400, 400)
        w.entities = (far,)
        step(app, w, 1.5)
        app._fight_all = 'Godrick Soldier'
        app._walk_request('start', far.pos, 'Soldier', 9, 'fight')
        step(app, w, 0.5)
        self.assertTrue(app.walk.active)
        p = w.pos
        near = Entity(10, 1, 'c4300', (p[0] + 1.0, 0.0, p[2] + 3.0), (p[0] + 1.0, 1.0, p[2] + 3.0), 0.5, 2.0, True,
                      False, 400, 400, facing=(-0.3, 0.0, -0.95))     # 3 m ahead, facing the player
        w.entities = (far, near)
        step(app, w, 0.2)
        self.assertTrue(app.fighter.active)
        self.assertEqual(app.fighter.target_id, 10)


class TwentySecondSessionAppTests(unittest.TestCase):
    def test_a_lost_key_press_is_pressed_again(self):
        # 2026-10-12: back in the game after a pause, forward held 4 s and the character stood idle (anim 0)
        from game_assistant.core.interface import Player, Snapshot

        class IdleWorld(World):
            def snap(self):
                s = super().snap()
                pl = Player(s.player.pos, s.player.facing, 'alive', anim=0)
                return Snapshot(s.game, s.frame, True, pl, s.camera, s.entities, None, screen=s.screen)
        app = make_app()
        w = IdleWorld(frozen=True)
        step(app, w, 1.0)
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        before = app.keys.released
        step(app, w, 1.2)
        self.assertTrue(app.walk.active)
        self.assertGreater(app.keys.released, before)       # let go, then pressed again
        self.assertIn('forward', app.keys.held)

    def test_whatever_comes_at_the_player_is_next(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        app.fighter = Fighter(lambda model, anim: [(0.5, 0.6)] if anim == 3000 else [])
        w = World()
        soldier = Entity(9, 1, 'c4310', (0.0, 0.0, 30.0), (0.0, 1.0, 30.0), 0.5, 2.0, True, False, 400, 400)
        wolf = Entity(10, 2, 'c4070', (0.0, 0.0, 12.0), (0.0, 1.0, 12.0), 0.5, 1.0, True, False, 200, 200,
                      facing=(0.0, 0.0, -1.0))
        w.entities = (soldier, wolf)
        app.fighter._vel[10] = (0.0, -5.0)                  # running at the player
        self.assertEqual(app._coming_at_me(w.snap()).id, 10)
        app.fighter._vel[10] = (0.0, 0.0)                   # standing there: not coming
        self.assertIsNone(app._coming_at_me(w.snap()))


class TwentyThirdSessionAppTests(unittest.TestCase):
    def test_the_fairy_keeps_planning_for_a_walk_to_a_close_goal_behind_a_wall(self):
        # 16:52: the soldier 15 m off behind a wall: the fairy took it as "arrived" (within its 15 m), switched to
        # showing it, and threw the route it was planning away: the walk waited 15 s with no route.
        c = Companion()
        c.plan_async = False
        c.route_fn = lambda s, g: None                 # (a plan still running: no route yet)
        c.walking = True
        c.guide((0.0, 0.0, 10.0), 'Soldier')
        w = World()
        for _ in range(10):
            c.update(w.snap(), 1 / 30)
        self.assertEqual(c.mode, 'guide')
        c.walking = False                              # the player walking: arrived, show it
        c.update(w.snap(), 1 / 30)
        self.assertEqual(c.mode, 'show')


class TwentySixthSessionAppTests(unittest.TestCase):
    def test_a_kill_on_the_way_doesnt_finish_the_order(self):
        # 'kill the bat and the soldier': the soldier came at the player first; its death counted as the bat's
        import time as _time
        from game_assistant.core.interface import Entity
        app = make_app()
        ran = []
        app._order_work = lambda text, **kw: ran.append(text)
        app._enemy_name = lambda e: {9: 'Man-Bat', 10: 'Kaiden Sellsword'}.get(e.id)
        w = World()
        bat = Entity(9, 1, 'c4200', (0.0, 0.0, 9.0), (0.0, 1.0, 9.0), 0.5, 1.0, True, False, 100, 100)
        rider = Entity(10, 2, 'c4050', (0.0, 0.0, 3.0), (0.0, 1.0, 3.0), 0.5, 2.0, True, False, 400, 400)
        w.entities = (bat, rider)
        step(app, w, 1.0)
        app._then = ['kill the soldier']
        app._order_target = 9                          # the order: the bat
        app.fighter.start(10)                          # (the rider came at the player: fought first)
        step(app, w, 0.1)
        w.entities = (bat, Entity(10, 2, 'c4050', (0.0, 0.0, 3.0), (0.0, 1.0, 3.0), 0.5, 2.0, True, True, 0, 400))
        step(app, w, 0.2)
        _time.sleep(0.1)
        self.assertEqual(ran, [])                      # not on to the next part...
        self.assertIn('Got it! Now back to the Man-Bat!', app.lines)
        self.assertTrue(app.walk.active or app.fighter.active)
        self.assertIn(app.walk.target_id if app.walk.active else app.fighter.target_id, (9,))

    def test_a_next_part_whose_enemy_is_gone_is_skipped_not_searched_for(self):
        app = make_app()
        planned = []
        app._can_plan = lambda: True
        app._plan_order = planned.append
        app.resolver = type('R', (), {'handle': lambda self, t: WalkAction('none', "I don't see any soldier to attack.")})()
        app.log = type('L', (), {'open': lambda self, *a, **k: open(os.devnull, 'w')})()
        app._then = []
        app._order_work('kill the soldier', queued=True)
        self.assertEqual(planned, [])
        self.assertIn("I don't see any soldier to attack.", app.lines)


class TwentySeventhSessionAppTests(unittest.TestCase):
    def test_kill_two_stops_after_two_even_when_both_fall_in_one_fight(self):
        from game_assistant.core.interface import Entity
        app = make_app()
        app.resolver = type('R', (), {'character': lambda self, *a, **k: None, 'heard': lambda self, t: None})()
        w = World()
        a = Entity(9, 1, 'c4311', (0.0, 0.0, 3.0), (0.0, 1.0, 3.0), 0.5, 2.0, True, False, 100, 100)
        b = Entity(10, 1, 'c4311', (1.5, 0.0, 2.5), (1.5, 1.0, 2.5), 0.5, 2.0, True, False, 100, 100,
                   facing=(-0.5, 0.0, -0.85))
        third = Entity(11, 1, 'c4311', (0.0, 0.0, 30.0), (0.0, 1.0, 30.0), 0.5, 2.0, True, False, 100, 100)
        w.entities = (a, b, third)
        step(app, w, 1.0)
        app._fight_all, app._fight_left, app._kills_read = 'enemy', 2, 0
        app._walk_request('start', a.pos, 'Godrick Soldier', 9, 'fight')
        step(app, w, 0.2)
        self.assertTrue(app.fighter.active)
        dead = lambda e: Entity(e.id, 1, 'c4311', e.pos, e.center, 0.5, 2.0, True, True, 0, 100)
        w.entities = (dead(a), b, third)                    # one down, the other on the player: same fight
        step(app, w, 0.2)
        w.entities = (dead(a), dead(b), third)
        step(app, w, 0.3)
        self.assertIn("Got it! That's all I was asked for!", app.lines)
        self.assertFalse(app.walk.active and app.walk.target_id == 11)   # not on to a third


class DoerQuietTests(unittest.TestCase):
    def test_while_the_doer_runs_only_its_own_and_urgent_lines_are_shown(self):
        # ("get me down from here": eight lines, each action's own on top of the doer's, 2026-10-15)
        import queue
        app = App.__new__(App)
        app.q, app.bubble, app.speaker = queue.Queue(), None, None
        app._doer_busy = True
        app._navi_line("Let's go to the ground!")                      # an action's own: kept, not shown
        app.doer_say("Down from the tree first.")                       # the doer's
        app._navi_line('A Creeper coming at us! Dealing with it first.', urgent=True)
        shown = []
        while not app.q.empty():
            shown.append(app.q.get()[1].strip())
        self.assertEqual(shown, ['Down from the tree first.', 'A Creeper coming at us! Dealing with it first.'])
        self.assertIn("Let's go to the ground!", app.doer_said_since(0.0))   # (the doer still reads it)
        app._doer_busy = False
        app._navi_line('Here we are!')
        self.assertEqual(app.q.get()[1].strip(), 'Here we are!')


class WithAdapterTests(unittest.TestCase):
    def test_a_frame_with_no_task_and_an_adapter(self):
        # (with an adapter, every frame read the task order that wasn't there: "Auto-walk error: TypeError"
        # flooding the window at start-up, 2026-10-15; the tests' app had no adapter)
        app = make_app()

        class A:
            reserve = None
        app.adapter = A()
        w = World()
        step(app, w, 0.2)
        self.assertEqual(app.adapter.reserve, {})


class TaskFailedThinkTests(unittest.TestCase):
    def test_a_failed_task_an_order_started_is_thought_through(self):
        # (an iron pickaxe's ingredients got, then no way up: "I can't get to where I can reach the crafting
        # table", and the order was over, 2026-10-15)
        import threading as th
        from game_assistant.core.tasks import TaskRunner
        app = make_app()
        app.doer, app._doer_busy, got = object(), False, []
        app._doer_order = lambda text: got.append(text)
        app.task = TaskRunner(app)
        app.task.steps, app.task.i, app.task.result = [('station', 'mc:crafting_table')], 0, 'failed: no way up'
        app.task.describe = lambda: 'place a crafting table'
        app._task_order = 'make me an iron pickaxe'
        app._task_failed_think()
        for t in th.enumerate():
            if t is not th.current_thread() and t.daemon:
                t.join(timeout=1.0)
        self.assertEqual(len(got), 1)
        self.assertIn('make me an iron pickaxe', got[0])
        self.assertIn('no way up', got[0])
        app._task_failed_think()                                        # (once)
        self.assertEqual(len(got), 1)


class GoalReplanAppTests(unittest.TestCase):
    def test_a_failed_step_is_planned_again_from_here_then_the_doer(self):
        # (docs/AGENT.md: a step's failure is a fact the next plan works from; only when there's no plan, the doer)
        from game_assistant.core.crafting import Recipe, Source
        from game_assistant.core.goals import GoalRunner
        from game_assistant.core.skills import UNREACHABLE, Outcome, Reason
        from game_assistant.core.tasks import TaskRunner
        app = make_app()
        table = Recipe('r:t', 'mc:crafting_table', 1, (('mc:p',),) * 4, 2, 2)
        from game_assistant.core.crafting import CantMake
        plans = [[('station', 'mc:crafting_table'), ('craft', table, 1)]]

        def planner(item, total, after=None):
            if not plans:
                raise CantMake('no way from here')
            return plans.pop(0)
        app._plan_goal = planner
        app._held = lambda: {}
        app.goal = GoalRunner(app._plan_goal)
        app.task = TaskRunner(app)
        app.doer, app._doer_busy, think = object(), False, []
        app._doer_order = lambda text: think.append(text)
        log = [('gather', 'mc:log', 1, Source('mc:log', ('mc:log',)))]
        app.task.start(log, 'mc:pickaxe', 1)
        app.goal.start('mc:pickaxe', 1, log)
        app._task_order = 'make me a pickaxe'
        app.task.active, app.task.result = False, 'failed: x'
        app.task.outcome = Outcome(False, Reason(UNREACHABLE, {'step': 'gather log', 'at': (1, 2, 3)}), 'x')
        app._task_failed_think()
        self.assertTrue(app.task.active)                                # planned again, under way
        self.assertEqual(app.task.steps[0], ('station', 'mc:crafting_table'))
        self.assertEqual(think, [])
        app.task.active, app.task.result = False, 'failed: y'
        app.task.outcome = Outcome(False, Reason(UNREACHABLE, {'step': 'station crafting table'}), 'y')
        app._task_failed_think()                                        # no plan now: the doer thinks it through
        import threading as th
        for t in th.enumerate():
            if t is not th.current_thread() and t.daemon:
                t.join(timeout=1.0)
        self.assertEqual(len(think), 1)


class DoerOutcomeAppTests(unittest.TestCase):
    """How a walk or a fight ended comes back to the doer as data, as a task's does (docs/AGENT.md stage 4)."""

    def test_a_walk_arrived_or_taken_over(self):
        import time as _t
        app = make_app()
        w = World()
        step(app, w, 0.5)
        t0 = _t.monotonic()
        app._walk_request('start', (0.0, 0.0, 8.0), 'the spot')
        step(app, w, 6)
        self.assertIn('Here we are: the spot!', app.lines)
        self.assertEqual(app.doer_outcome(t0), {'ok': True, 'why': None})
        t1 = _t.monotonic()
        app._walk_request('start', (0.0, 0.0, 60.0), 'Gatefront')
        step(app, w, 1)
        app._player_key(0x57)
        step(app, w, 0.1)
        self.assertEqual(app.doer_outcome(t1), {'ok': False, 'why': {'kind': 'interrupted', 'by': 'the player'}})
        self.assertIsNone(app.doer_outcome(_t.monotonic() + 1))          # (nothing ended since)

    def test_a_fight_given_up_on_low_health(self):
        import time as _t
        app = make_app()
        w = World()
        step(app, w, 0.5)
        t0 = _t.monotonic()
        app.fighter.name = 'Zombie'
        app.fighter.events.append('low_health')
        app._fight_step(w.snap(), 1 / 30)                               # (the fighter's events, read each frame)
        self.assertEqual(app.doer_outcome(t0), {'ok': False, 'why': {'kind': 'danger', 'what': 'Zombie'}})


class DoerTypedAppTests(unittest.TestCase):
    def test_typed_orders_the_plan_and_the_situation(self):
        import json
        try:
            from test_walk_commands import TypedOrderTests
        except ModuleNotFoundError:
            from tests.test_walk_commands import TypedOrderTests
        app = make_app()
        r, M = TypedOrderTests().resolver(held={'minecraft:oak_log': 2})
        app.resolver, app.adapter = r, r.adapter
        self.assertTrue({'get', 'how_to_get', 'put_down', 'mine_at', 'flee', 'explore'} <= app.doer_tools())
        plan = json.loads(app.doer_how_to_get('iron_pickaxe', 1))
        self.assertEqual(plan['steps'][-1]['step'], 'craft 1 iron pickaxe')
        self.assertGreater(plan['seconds'], 30)
        self.assertEqual(plan['seconds'], sum(s['seconds'] for s in plan['steps']))
        self.assertIn('error', json.loads(app.doer_how_to_get('moon rock', 1)))
        self.assertIsNone(app.doer_do('get', {'item': 'iron_pickaxe'}))     # started
        self.assertEqual(app._task_cmd[1:], (M + 'iron_pickaxe', 1))
        self.assertEqual(app.doer_do('go_to', {}), 'go_to needs x, y and z, or a place')
        self.assertEqual(json.loads(app.doer_situation())['holding'], {'oak_log': 2})


class AwareAppTests(unittest.TestCase):
    def test_hostile_ones_where_its_going_are_met_on_open_ground(self):
        # (four zombies at the bottom of a pit where the player's things lay: walked down among them, dead,
        # 2026-10-16)
        from game_assistant.core.interface import Camera, Entity, Player, Snapshot
        from game_assistant.core.melee import MeleeFighter
        app = make_app()
        app.fighter = MeleeFighter()                     # (Minecraft's: it holds ground)
        lines = []
        app._navi_line = lambda line, urgent=False: lines.append(line)

        class A:
            defend_on_walks = True
            room = 25

            def terrain(self):
                return {'room': lambda x, z: self.room}

            def combat_profile(self, e):
                return {}
        app.adapter = A()
        app.walk.goal, app.walk.name = (0.0, 0.0, 12.0), 'where you died'
        app.walk.active = True
        zombies = tuple(Entity(i, 1, 'minecraft:zombie', (x, -4.0, 12.0), (x, -3.0, 12.0), 0.3, 1.95, True, False,
                               20.0, 20.0, name='Zombie') for i, x in ((1, -1.0), (2, 1.0)))
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 20.0, 20.0), cam, zombies, None)
        self.assertTrue(app._conduct(snap, 1 / 30))
        self.assertEqual(app.conductor.running.kind, 'hold')
        self.assertTrue(app.fighter.active and app.fighter.hold)          # (held ground, here: room)
        self.assertEqual(app._paused_order['walk'][1], 'where you died')  # (the order waits)
        self.assertIn('2 Zombies', lines[-1])
        # tight here, open ground passed a few blocks back: back there first
        app2 = make_app()
        app2.fighter = MeleeFighter()
        app2._navi_line = lambda line, urgent=False: None
        app2.adapter = A()
        app2.adapter.room = 4
        app2._last_open = (0.0, 0.0, -5.0)
        app2.walk.goal, app2.walk.name = (0.0, 0.0, 12.0), 'where you died'
        app2.walk.active = True
        self.assertTrue(app2._conduct(snap, 1 / 30))
        self.assertEqual(app2.conductor.running.kind, 'regroup')
        self.assertFalse(app2.fighter.active)
        self.assertEqual(app2._walk_cmd[1], (0.0, 0.0, -5.0))

    def test_skeletons_not_after_the_player_dont_hold_a_walk(self):
        # (two skeletons 6 and 9 blocks off, not after the player: the walk stood still 10 s, 2026-10-16)
        from game_assistant.core.interface import Camera, Entity, Player, Snapshot
        from game_assistant.core.melee import MeleeFighter
        app = make_app()
        app.fighter = MeleeFighter()
        app._navi_line = lambda line, urgent=False: None

        class A:
            defend_on_walks = True

            def terrain(self):
                return {'room': lambda x, z: 25}

            def combat_profile(self, e):
                return {'ranged': 'skeleton' in (e.model or '')}
        app.adapter = A()
        app.walk.goal, app.walk.name = (0.0, 0.0, 20.0), 'oak log'
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
        skeletons = tuple(Entity(i, 1, 'minecraft:skeleton', (x, 0.0, 7.0), (x, 1.0, 7.0), 0.3, 1.95, True, False,
                                 20.0, 20.0, name='Skeleton', extra={'aggressive': True}) for i, x in ((1, -2), (2, 3)))
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 20.0, 20.0), cam, skeletons, None)
        self.assertFalse(app._conduct(snap, 1 / 30))                     # (they shoot, not after us: let be)
        zombie = Entity(3, 1, 'minecraft:zombie', (4.0, 0.0, 6.0), (4.0, 1.0, 6.0), 0.3, 1.95, True, False, 20.0,
                        20.0, name='Zombie', extra={'aggressive': False})
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 20.0, 20.0), cam, (zombie,), None)
        self.assertFalse(app._conduct(snap, 1 / 30))                     # (not after us, not where we're going)


class ShooterAppTests(unittest.TestCase):
    def app_with(self, skeleton_at, health=20.0, offhand=''):
        from game_assistant.core.interface import Camera, Entity, Player, Snapshot
        from game_assistant.core.melee import MeleeFighter
        from game_assistant.core.walk_commands import WalkAction
        app = make_app()
        app.fighter = MeleeFighter(lambda e: {'ranged': 'skeleton' in (e.model or '')}, lambda h: 0,
                                   weapon_damage=lambda h: 5.0)
        app.lines = []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        skel = Entity(5, 1, 'minecraft:skeleton', skeleton_at, (skeleton_at[0], 1.0, skeleton_at[2]), 0.3, 1.99,
                      True, False, 20.0, 20.0, name='Skeleton', extra={'aggressive': True, 'using_item': True})
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', health, 20.0), cam, (skel,), None,
                        extra={'inventory': {}, 'offhand': offhand, 'hotbar': [('minecraft:stone_sword', 1)]})

        class A:
            defend_on_walks = True
            covered = []

            def combat_profile(self, e):
                return {'ranged': 'skeleton' in (e.model or '')}

            def cover_spot(self, foe):
                self.covered.append(foe)
                return (3.0, 0.0, -2.0)

            def terrain(self):
                return {}

            def snapshot(self):
                return snap

            def fight_gear(self, e, extra):
                return [] if extra.get('offhand') == 'minecraft:shield' else [('minecraft:shield', 'offhand')]
        app.adapter = A()

        class R:
            def get_item(self, item, n):
                return WalkAction('task', '', name=item, count=1,
                                  steps=[('craft', type('Rec', (), {'result': item})(), 1)])
        app.resolver = R()
        app._step_cost = lambda s: 3.0
        app.walk.goal, app.walk.name = (0.0, 0.0, 30.0), 'oak log'
        app.walk.active = True
        return app, snap

    def test_a_shooter_far_off_is_hidden_from_near_and_alone_fought(self):
        # (the user: "if a skeleton happens to be nearby or attacks us while we're doing other things, then we
        # should either hide or kill it, depending on the situation")
        app, snap = self.app_with((0.0, 0.0, 14.0))
        self.assertTrue(app._conduct(snap, 1 / 30))
        self.assertEqual(app.conductor.running.kind, 'cover')            # (far off: into cover)
        self.assertEqual(app._walk_cmd[2], 'cover')
        app, snap = self.app_with((0.0, 0.0, 7.0))
        self.assertTrue(app._conduct(snap, 1 / 30))
        self.assertEqual(app.conductor.running.kind, 'fight')            # (near, alone, well: fought)
        app, snap = self.app_with((0.0, 0.0, 7.0), health=8.0)
        app._conduct(snap, 1 / 30)
        self.assertEqual(app.conductor.running.kind, 'cover')            # (hurt: cover)

    def test_a_shield_got_before_an_ordered_fight_with_a_shooter(self):
        from game_assistant.core.walk_commands import WalkAction
        app, snap = self.app_with((0.0, 0.0, 7.0))
        act = WalkAction('walk', '', (0.0, 0.0, 7.0), 'Skeleton', 5, 'fight')
        app._start_action(act, 'kill the skeleton')
        steps = app._task_cmd[0]
        self.assertEqual(steps[-1], ('offhand', 'minecraft:shield'))
        self.assertEqual(app._then_walk[2:], (5, 'fight'))              # (then the fight)
        app2, snap2 = self.app_with((0.0, 0.0, 7.0), offhand='minecraft:shield')
        app2._start_action(act, 'kill the skeleton')
        self.assertEqual(app2._walk_cmd[0], 'start')                    # (one held: straight to it)

    def test_bare_handed_the_quickest_weapon_first(self):
        # (sent to kill skeletons with nothing in hand, it went for a shield and fought a zombie with its fists:
        # 15 hits, still up, the player dead, 2026-10-16)
        from game_assistant.core.walk_commands import WalkAction
        app, snap = self.app_with((0.0, 0.0, 7.0))
        app.adapter.fight_gear = lambda e, extra: [(('minecraft:stone_sword', 'minecraft:wooden_sword'), 'hand'),
                                                   (('minecraft:shield',), 'offhand')]
        costs = {'minecraft:stone_sword': 40.0, 'minecraft:wooden_sword': 25.0, 'minecraft:shield': 90.0}
        app._step_cost = lambda s: costs[s[1].result]
        app._fight_all = 'Zombie'                                       # (the last order's)
        app._start_action(WalkAction('walk', '', (0.0, 0.0, 7.0), 'Skeleton', 5, 'fight'), 'kill the skeleton')
        steps, name, _n = app._task_cmd
        self.assertEqual(name, 'minecraft:wooden_sword')                # (the quickest that does)
        self.assertEqual([s[1].result for s in steps if s[0] == 'craft'],
                         ['minecraft:wooden_sword', 'minecraft:shield'])
        self.assertEqual(steps[-1], ('offhand', 'minecraft:shield'))
        self.assertIsNone(app._fight_all)                               # (not the last order's "all of them")
        self.assertIn('wooden sword and a shield', app.lines[-1])


class CoverWalkTests(unittest.TestCase):
    def test_into_cover_and_then_on_with_the_order(self):
        # (the walk to cover was held up every frame by the hiding itself: the player stood in the skeleton's sight,
        # going nowhere, 2026-10-16): the conductor's frames, end to end
        app, snap = ShooterAppTests().app_with((0.0, 0.0, 14.0))
        app.adapter.cover_spot = lambda foes: (0.0, 0.0, 8.0)
        w = World()
        step(app, w, 0.5)
        app.walk.active, app.walk.goal = True, (0.0, 0.0, 30.0)
        self.assertTrue(app._conduct(snap, 1 / 30))                    # (the skeleton: into cover)
        self.assertEqual(app.conductor.running.kind, 'cover')
        step(app, w, 3)                                                 # (the skeleton's out of sight now)
        self.assertGreater(w.pos[2], 5.0)                               # (it went)
        step(app, w, 5)
        self.assertIsNone(app.conductor.running)                        # (it gave up: over)
        self.assertIsNone(app._paused_order)                            # (the order on again)


class InTheAirTests(unittest.TestCase):
    def test_nothing_cuts_a_jump_off(self):
        # (into cover, mid-jump: the jump cut off, a fall of 10 blocks, 2026-10-16)
        from game_assistant.core.interface import Camera, Entity, Player, Snapshot
        app = make_app()

        class A:
            defend_on_walks = True
        app.adapter = A()
        app.walk.state, app.walk.active = 'parkour', True
        z = Entity(1, 1, 'minecraft:zombie', (0.0, 0.0, 3.0), (0.0, 1.0, 3.0), 0.3, 1.95, True, False, 20.0, 20.0,
                   name='Zombie', extra={'aggressive': True})
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
        snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 20.0, 20.0), cam, (z,), None)
        self.assertFalse(app._conduct(snap, 1 / 30))
        self.assertIsNone(app.conductor.running)


class ErrandGearTests(unittest.TestCase):
    def test_an_errand_among_enemies_gets_a_weapon_first(self):
        # ("make me a bed" at night, 7 health, a pickaxe the best to hit with: two spiders, dead, 2026-10-17)
        from types import SimpleNamespace as NS
        from game_assistant.app import App
        from game_assistant.core.interface import Entity, Player, Snapshot
        spider = Entity(9, 1, 'minecraft:spider', (5.0, 0.0, 5.0), (5.0, 0.5, 5.0), 0.7, 0.9, True, False, 16.0, 16.0,
                        name='Spider')
        sword = [('craft', 'sword recipe', 1)]

        class A:
            def __init__(self, foes):
                self.foes = foes

            def snapshot(self):
                return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, self.foes, None,
                                extra={'hotbar': [('minecraft:stone_pickaxe', 1)], 'inventory': {}})

            def fight_gear(self, e, extra):
                return [(('minecraft:stone_sword',), 'hand')]

            def combat_profile(self, e):
                return {}
        app = App.__new__(App)
        app.lines = []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        app._step_cost = lambda s: 10.0
        app.resolver = NS(get_item=lambda item, n: NS(kind='task', steps=sword))
        bed = NS(kind='task', steps=[('gather', 'wool', 3)], name='minecraft:white_bed', count=1)
        app.adapter = A((spider,))
        app._task_cmd = (bed.steps, bed.name, 1)
        app._errand_gear(bed)
        self.assertEqual(app._task_cmd, (sword + bed.steps, 'minecraft:white_bed', 1))
        self.assertIn('stone sword', app.lines[-1])
        app.adapter = A(())                              # (none about: the errand as it was)
        app._task_cmd = (bed.steps, bed.name, 1)
        app._errand_gear(bed)
        self.assertEqual(app._task_cmd, (bed.steps, 'minecraft:white_bed', 1))

    def test_hurt_among_enemies_with_food_a_bite_first(self):
        from types import SimpleNamespace as NS
        from game_assistant.app import App
        from game_assistant.core.interface import Entity, Player, Snapshot
        spider = Entity(9, 1, 'minecraft:spider', (5.0, 0.0, 5.0), (5.0, 0.5, 5.0), 0.7, 0.9, True, False, 16.0, 16.0)

        class A:
            def snapshot(self):
                return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', 7.0, 20.0), None, (spider,),
                                None, extra={'hotbar': [('minecraft:iron_sword', 1), ('minecraft:bread', 3)]})

            def fight_gear(self, e, extra):
                return []

            def food_slot(self, hotbar):
                return 1
        app = App.__new__(App)
        app.lines = []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        app.adapter = A()
        bed = NS(kind='task', steps=[('gather', 'wool', 3)], name='minecraft:white_bed', count=1)
        app._task_cmd = (bed.steps, bed.name, 1)
        app._errand_gear(bed)
        self.assertEqual(app._task_cmd[0], [('eat', None)] + bed.steps)
        self.assertIn('a bite', app.lines[-1])


class PlanPacingTests(unittest.TestCase):
    def test_planned_again_from_one_spot_waits_longer_each_time(self):
        # (a way whose first move gave up at once was planned 10 times a second for 15 s, the game lagging,
        # 2026-10-17)
        c = Companion()
        c.plan_async = False
        calls = []
        c.route_fn = lambda s, g: calls.append(s) or None
        c.mode = 'guide'
        for i in range(100):                             # 10 s at 10 a second, never moving
            c.t = i * 0.1
            c._start_plan((0.0, 0.0, 0.0), (0.0, 30.0, 0.0))
        self.assertLess(len(calls), 12)                  # (0.25, 0.5, 1, 2, 4 s apart: not 100)
        c.t += 0.1
        c._start_plan((3.0, 0.0, 0.0), (0.0, 30.0, 0.0))  # (moved on: at once)
        self.assertEqual(calls[-1], (3.0, 0.0, 0.0))


class BlocksFirstTests(unittest.TestCase):
    def test_blocks_put_in_the_hotbar_before_a_walk_or_an_errand(self):
        from types import SimpleNamespace as NS
        from game_assistant.app import App
        app = App.__new__(App)
        app.adapter = NS(blocks_to_hand=lambda: ('arrange', ('a', 'b')))
        walk = NS(kind='walk', point=(1.0, 2.0, 3.0), name='Horse', entity_id=None, attack=False, follow=None)
        app._task_cmd = 'stop'
        self.assertTrue(app._blocks_to_hand(walk))
        self.assertEqual(app._task_cmd, ([('arrange', ('a', 'b'))], 'ready', 1))
        # (the shield picked up where the player died stayed in the pack; a skeleton fought without it, 2026-10-17)
        app.adapter = NS(blocks_to_hand=lambda: None, equipment=lambda inv, off: [('offhand', 'mc:shield')],
                         snapshot=lambda: NS(extra={'inventory': {'mc:shield': 1}, 'offhand': ''}))
        self.assertTrue(app._blocks_to_hand(walk))
        self.assertEqual(app._task_cmd, ([('offhand', 'mc:shield')], 'ready', 1))
        app.adapter = NS(blocks_to_hand=lambda: ('arrange', ('a', 'b')))
        self.assertEqual(app._then_walk, ((1.0, 2.0, 3.0), 'Horse', None, False))
        task = NS(kind='task', steps=[('use', 'x')], name='use', count=1)
        app._task_cmd = (task.steps, task.name, 1)
        self.assertFalse(app._blocks_to_hand(task))
        self.assertEqual(app._task_cmd[0], [('arrange', ('a', 'b')), ('use', 'x')])
        fight = NS(kind='walk', point=(0.0, 0.0, 0.0), name='Zombie', entity_id=3, attack='fight', follow=None)
        self.assertFalse(app._blocks_to_hand(fight))       # (a fight: at once)


class ErrandGearLimitTests(unittest.TestCase):
    def test_no_gear_before_picking_things_up_and_never_a_shield(self):
        # (the player's own sword and shield lay where they'd died: a sword and a shield were made first, 17 steps,
        # skeletons shooting, dead again, 2026-10-17)
        from types import SimpleNamespace as NS
        from game_assistant.app import App
        from game_assistant.core.interface import Entity, Player, Snapshot
        skel = Entity(9, 1, 'minecraft:skeleton', (5.0, 0.0, 5.0), (5.0, 1.0, 5.0), 0.3, 1.99, True, False, 20.0,
                      20.0)
        app = App.__new__(App)
        app.lines = []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        app._step_cost = lambda s: 10.0
        made = {'minecraft:stone_sword': [('craft', 'sword', 1)], 'minecraft:shield': [('smelt', 'iron', 1)]}
        app.resolver = NS(get_item=lambda item, n: NS(kind='task', steps=made[item]))
        app.adapter = NS(snapshot=lambda: Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None,
                                                   (skel,), None, extra={'inventory': {}}),
                         fight_gear=lambda e, ex: [(('minecraft:stone_sword',), 'hand'),
                                                   (('minecraft:shield',), 'offhand')],
                         combat_profile=lambda e: {})
        pickup = NS(kind='task', steps=[('pickup', None, 12.0)], name='items', count=1)
        app._task_cmd = (pickup.steps, 'items', 1)
        app._errand_gear(pickup)
        self.assertEqual(app._task_cmd[0], pickup.steps)            # (picking up: as it was)
        bed = NS(kind='task', steps=[('gather', 'wool', 3)], name='minecraft:white_bed', count=1)
        app._task_cmd = (bed.steps, bed.name, 1)
        app._errand_gear(bed)
        self.assertEqual(app._task_cmd[0], [('craft', 'sword', 1)] + bed.steps)    # (a sword: no shield)
