"""The app's walk step (App._walk_step) with the real companion and auto-walk, stand-in keys and no
game: an order starts guiding and walking, keys are held, the player's own key press hands control
back and releases every key."""
import time
import unittest

from game_assistant.app import App
from game_assistant.core.autowalk import AutoWalk
from game_assistant.core.companion import Companion
from game_assistant.core.nav import Route
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
    app.companion = Companion()
    app.companion.plan_async = False
    app.companion.route_fn = lambda s, g: Route([s, (0.0, 0.0, 20.0), g], True, 0)
    app._walk_cmd, app._walk_route, app._took_over, app._last_enemy_line = None, None, 0.0, -1e9
    app.lines = []
    app._navi_line = app.lines.append
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
