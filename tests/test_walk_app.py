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
    app._navi_line = app.lines.append
    app._fight_log = lambda row: None   # never into the real runtime/combat log
    app._walk_log = lambda row: None    # ...nor the walk log
    app._save_chains = lambda: None     # ...nor the learned combos
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
