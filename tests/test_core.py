"""Offline tests for the game-independent core. Run: Run-Tests.bat"""
import tempfile
import time
import unittest
from pathlib import Path

from game_assistant.core import lookat
from game_assistant.core.interface import Camera, Entity, RayHit, Snapshot
from game_assistant.core.spend import SpendCapReached, SpendGuard, cost

CAM = Camera(pos=(0.0, 1.6, 0.0), forward=(0.0, 0.0, 1.0), right=(1.0, 0.0, 0.0), up=(0.0, 1.0, 0.0),
             fov_y_deg=60.0, aspect=16 / 9)


def ent(i, x, z, y=0.0, radius=0.4, height=1.8, dead=False, hostile=True):
    return Entity(id=i, type_id=1000 + i, model=f'c{i:04d}', pos=(x, y, z), center=(x, y + height / 2, z),
                  radius=radius, height=height, hostile=hostile, dead=dead, health=100, max_health=100)


def snap(*ents, target=None):
    return Snapshot('test', 1, True, None, CAM, tuple(ents), None, target)


def no_walls(rays):
    return [RayHit(a, b, False) for a, b in rays]


def wall_at(z):
    """A wall across the whole world at depth z."""
    def cast(rays):
        out = []
        for a, b in rays:
            if (a[2] - z) * (b[2] - z) < 0:
                t = (z - a[2]) / (b[2] - a[2])
                p = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
                out.append(RayHit(a, b, True, p, (0.0, 0.0, -1.0)))
            else:
                out.append(RayHit(a, b, False))
        return out
    return cast


class LookAtTests(unittest.TestCase):
    def test_entity_on_crosshair(self):
        r = lookat.resolve(snap(ent(1, 0, 10)), no_walls)
        self.assertEqual(r.kind, 'entity')
        self.assertEqual(r.best.entity.id, 1)
        self.assertGreaterEqual(r.confidence, 0.9)
        self.assertEqual(r.best.side, 'centre')

    def test_prefers_closest_to_crosshair_over_closest_to_player(self):
        r = lookat.resolve(snap(ent(1, 2.5, 6), ent(2, 0.1, 30)), no_walls)
        self.assertEqual(r.best.entity.id, 2)

    def test_side_of_screen(self):
        r = lookat.resolve(snap(ent(1, 2.0, 6, y=-0.4, radius=1.0, height=4.0)), no_walls)  # a big enemy to the right
        self.assertIn('right', r.best.side)

    def test_ignores_dead_and_behind(self):
        r = lookat.resolve(snap(ent(1, 0, 10, dead=True), ent(2, 0, -10)), no_walls)
        self.assertNotEqual(r.kind, 'entity')

    def test_wall_hides_entity(self):
        r = lookat.resolve(snap(ent(1, 0, 20)), wall_at(10))
        self.assertEqual(r.kind, 'surface')
        self.assertEqual(r.surface_kind, 'wall')
        self.assertEqual(r.hidden[0].entity.id, 1)

    def test_two_at_crosshair_is_ambiguous(self):
        r = lookat.resolve(snap(ent(1, -0.3, 10), ent(2, 0.3, 10.5)), no_walls)
        self.assertEqual(r.kind, 'entity')
        self.assertLessEqual(r.confidence, 0.5)

    def test_game_target_wins(self):
        r = lookat.resolve(snap(ent(1, 0, 10), ent(2, 5, 10), target=2), no_walls)
        self.assertEqual((r.kind, r.best.entity.id, r.confidence), ('target', 2, 1.0))

    def test_open_sky(self):
        r = lookat.resolve(snap(), no_walls)
        self.assertEqual(r.kind, 'nothing')

    def test_facts_use_knowledge_names(self):
        r = lookat.resolve(snap(ent(1, 0, 10)), no_walls)
        f = lookat.facts(r, lambda tid: {'name': 'Godrick Soldier', 'fire_absorption': 0} if tid == 1001 else None)
        self.assertEqual(f['thing']['name'], 'Godrick Soldier')
        self.assertEqual(f['thing']['data'], {'fire_absorption': 0})


class SpendTests(unittest.TestCase):
    def test_cost(self):
        self.assertAlmostEqual(cost('claude-haiku-5-5', {'input_tokens': 1_000_000, 'output_tokens': 0}), 0.10)
        self.assertAlmostEqual(cost('claude-haiku-5-5', {'input_tokens': 0, 'output_tokens': 1_000_000}), 0.50)

    def test_cap_and_warning(self):
        with tempfile.TemporaryDirectory() as d:
            g = SpendGuard(cap=1.0, ledger=Path(d) / 'ledger.jsonl')
            self.assertIsNone(g.check())
            g.record('claude-sonnet-5-5', {'input_tokens': 0, 'output_tokens': 85_000})  # $0.85
            self.assertIsNotNone(g.check())   # warns once at 80 %
            self.assertIsNone(g.check())
            g.record('claude-sonnet-5-5', {'input_tokens': 0, 'output_tokens': 20_000})  # +$0.20
            with self.assertRaises(SpendCapReached):
                g.check()

    def test_old_entries_expire(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ledger.jsonl'
            p.write_text(f'{{"t": {time.time() - 4000}, "model": "x", "usd": 5.0}}\n', 'utf-8')
            self.assertEqual(SpendGuard(cap=1.0, ledger=p).last_hour(), 0)



class ForgivingAimTests(unittest.TestCase):
    def test_enemy_a_bit_off_centre_still_counts(self):
        # 10 m ahead, 3 m to the side: about 13 degrees off the crosshair from its edge
        r = lookat.resolve(snap(ent(1, 3.0, 10)), no_walls)
        self.assertEqual(r.kind, 'entity')
        self.assertTrue(0.35 <= r.confidence < 0.9)

    def test_enemy_beats_friendly_at_similar_angle(self):
        r = lookat.resolve(snap(ent(1, 1.2, 10, hostile=False), ent(2, -1.6, 10)), no_walls)
        self.assertEqual(r.best.entity.id, 2)

    def test_far_off_to_the_side_is_not_what_you_look_at(self):
        r = lookat.resolve(snap(ent(1, 10.0, 6)), no_walls)
        self.assertNotEqual(r.kind, 'entity')


if __name__ == '__main__':
    unittest.main()


class EarlyScreenTest(unittest.TestCase):
    """The picture taken when the talk key goes down is used once, if fresh; otherwise a new grab."""

    def _agent(self):
        from game_assistant.core import agent as agent_mod
        a = agent_mod.Agent.__new__(agent_mod.Agent)  # no API client needed for this
        a.grabs = 0

        def grab():
            a.grabs += 1
            return [{'type': 'text', 'text': f'grab {a.grabs}'}]
        a.grab_screen = grab
        return a, agent_mod

    def test_early_picture_used_once(self):
        a, _ = self._agent()
        a.capture_screen()
        self.assertEqual(a.tool_look_at_screen({}), [{'type': 'text', 'text': 'grab 1'}])
        self.assertEqual(a.tool_look_at_screen({}), [{'type': 'text', 'text': 'grab 2'}])

    def test_stale_or_failed_picture_is_replaced(self):
        a, mod = self._agent()
        a._early_screen = (time.monotonic() - mod.EARLY_SCREEN_MAX_AGE - 1, [{'type': 'text', 'text': 'old'}])
        self.assertEqual(a.tool_look_at_screen({}), [{'type': 'text', 'text': 'grab 1'}])
        a._early_screen = (time.monotonic(), {'error': 'not focused'})
        self.assertEqual(a.tool_look_at_screen({}), [{'type': 'text', 'text': 'grab 2'}])


class BubbleTest(unittest.TestCase):
    def test_render_and_tip(self):
        from game_assistant.ui import bubble
        w, h, data, tip = bubble.render("That's a Spiritspring! Jump in on Torrent.", 24, 400, 'bl')
        self.assertEqual(len(data), w * h * 4)
        self.assertLess(tip[0], w / 2)            # bottom-left tail
        self.assertGreater(tip[1], h * 0.8)
        w2, h2, _, tip2 = bubble.render('word ' * 200, 24, 400, 'tr')
        self.assertLess(h2, 24 * 1.3 * (bubble.MAX_LINES + 3))  # long answers keep their last lines
        self.assertGreater(tip2[0], w2 / 2)
        self.assertLess(tip2[1], h2 * 0.2)

    def test_stays_inside_the_game_window(self):
        from game_assistant.ui import bubble
        bounds = (100, 100, 800, 600)
        self.assertEqual(bubble.choose_tail((300, 80), (400, 400), bounds), 'bl')
        self.assertEqual(bubble.choose_tail((300, 80), (880, 120), bounds), 'tr')
        x, y = bubble.position((300, 80), (20, 78), (890, 110), bounds)
        self.assertTrue(100 <= x and x + 300 <= 900 and 100 <= y and y + 80 <= 700)
