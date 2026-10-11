"""The fighter (core/combat.py) against a simulated enemy that swings on a timetable."""
import math
import unittest

from game_assistant.core.combat import DODGE_LEAD, Fighter
from game_assistant.core.interface import Camera, Entity, Player, Screen, Snapshot

WINDOWS = {('c4300', 3000): [(1.0, 1.2)], ('c4300', 3001): [(0.4, 0.5), (1.4, 1.5)]}


def windows(model, anim):
    return WINDOWS.get((model, anim), [])


class Sim:
    """Player at the origin looking north; an enemy north of it facing the player."""

    def __init__(self, enemy_z=1.5, hp=600, stamina=100):
        self.player = [0.0, 0.0, 0.0]
        self.enemy = [0.0, 0.0, enemy_z]
        self.hp, self.max_hp, self.stamina = hp, 1000, stamina
        self.enemy_hp, self.enemy_dead = 500, False
        self.anim, self.anim_t = 20, 0.0   # idle
        self.taps = []

    def snap(self):
        e = Entity(5, 43000010, 'c4300', tuple(self.enemy), (self.enemy[0], 1.0, self.enemy[2]), 0.5, 2.0, True,
                   self.enemy_dead, self.enemy_hp, 500, facing=(0.0, 0.0, -1.0), anim=self.anim,
                   anim_time=self.anim_t, anim_length=3.0)
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        pl = Player(tuple(self.player), (0.0, 0.0, 1.0), 'alive', self.hp, self.max_hp, self.stamina, 100)
        return Snapshot('test', 1, True, pl, cam, (e,), None, screen=Screen(0, 0, 1280, 720, True))

    def step(self, intent, dt):
        self.taps += [(round(self.anim_t, 3), self.anim, t) for t in intent.taps]
        if 'forward' in intent.keys:
            self.player[2] += 4.0 * dt
        if 'back' in intent.keys:
            self.player[2] -= 4.0 * dt
        self.anim_t += dt
        self.stamina = min(100, self.stamina + 30 * dt)
        if 'attack' in intent.taps:
            self.stamina -= 20


def run(f, sim, seconds, fps=60, script=None):
    for i in range(int(seconds * fps)):
        if script:
            script(sim, i / fps)
        sim.step(f.update(sim.snap(), 1 / fps), 1 / fps)


class FighterTests(unittest.TestCase):
    def test_dodges_just_before_the_hit_lands(self):
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3000, 0.0          # a swing that hits 1.0-1.2 s in
        run(f, sim, 1.3)
        rolls = [t for t, anim, tap in sim.taps if tap == 'roll' and anim == 3000]
        self.assertEqual(len(rolls), 1)
        self.assertTrue(1.0 - DODGE_LEAD - 0.02 <= rolls[0] <= 1.0, rolls)

    def test_two_hit_combo_gets_two_rolls(self):
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3001, 0.0          # hits at 0.4 and 1.4 s
        run(f, sim, 1.6)
        rolls = [t for t, anim, tap in sim.taps if tap == 'roll']
        self.assertEqual(len(rolls), 2)

    def test_attacks_when_safe_and_not_while_a_hit_is_coming(self):
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        run(f, sim, 2.0)                          # enemy idle: first it waits to see if it attacks
        self.assertNotIn('attack', [tap for _t, _a, tap in sim.taps])
        run(f, sim, 4.0)                          # a passive enemy: then it goes in
        sim.enemy[2] = sim.player[2] + 1.5        # (the sim's enemy doesn't move: keep it in reach)
        run(f, sim, 3.0)
        swings = [t for t, anim, tap in sim.taps if tap == 'attack']
        self.assertGreaterEqual(len(swings), 1)
        sim.taps.clear()
        sim.anim, sim.anim_t = 3000, 0.6          # its hit lands in 0.4 s
        run(f, sim, 0.3)
        self.assertNotIn('attack', [tap for _t, _a, tap in sim.taps])

    def test_closes_in_to_spacing_then_in_for_a_passive_enemy(self):
        from game_assistant.core.combat import SPACING
        sim, f = Sim(enemy_z=10.0), Fighter(windows)
        f.start(5)
        run(f, sim, 2)
        self.assertLessEqual(sim.enemy[2] - sim.player[2] - 0.5, SPACING + 1.0 + 0.1)   # waits at spacing
        self.assertIn('lock', [tap for _t, _a, tap in sim.taps])
        run(f, sim, 3)
        self.assertIn('attack', [tap for _t, _a, tap in sim.taps])                  # passive: goes in, swings

    def test_backs_off_to_recover_stamina(self):
        sim, f = Sim(stamina=10), Fighter(windows)
        f.start(5)
        intent = f.update(sim.snap(), 1 / 60)
        self.assertIn('back', intent.keys)
        self.assertEqual(f.state, 'recovering')

    def test_hands_back_when_hurt_and_stops_when_it_dies(self):
        sim, f = Sim(hp=300), Fighter(windows)
        f.start(5)
        f.update(sim.snap(), 1 / 60)
        self.assertIn('low_health', f.events)
        self.assertFalse(f.active)
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.enemy_dead = True
        f.update(sim.snap(), 1 / 60)
        self.assertIn('won', f.events)

    def test_facing_matters_only_for_other_enemies_further_away(self):
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        s = sim.snap()
        e = s.entities[0]
        # The target turned away mid wind-up (logs: three hits from attacks skipped as "facing away"): counts.
        away = Entity(e.id, e.type_id, e.model, e.pos, e.center, e.radius, e.height, True, False, e.health,
                      e.max_health, facing=(0.0, 0.0, 1.0), anim=3000, anim_time=0.9, anim_length=3.0)
        s2 = Snapshot(s.game, s.frame, True, s.player, s.camera, (away,), None, screen=s.screen)
        self.assertIn('roll', f.update(s2, 1 / 60).taps)
        # Another enemy 3 m away and facing away: ignored.
        f2 = Fighter(windows)
        f2.start(5)
        other = Entity(6, e.type_id, e.model, (3.0, 0.0, 3.0), (3.0, 1.0, 3.0), 0.5, 2.0, True, False, 500, 500,
                       facing=(1.0, 0.0, 0.0), anim=3000, anim_time=0.9, anim_length=3.0)
        idle = Entity(e.id, e.type_id, e.model, e.pos, e.center, e.radius, e.height, True, False, e.health,
                      e.max_health, facing=e.facing, anim=20, anim_time=0.0, anim_length=3.0)
        s3 = Snapshot(s.game, s.frame, True, s.player, s.camera, (idle, other), None, screen=s.screen)
        self.assertNotIn('roll', f2.update(s3, 1 / 60).taps)


class LayeredAnimationTests(unittest.TestCase):
    def test_dodges_an_attack_playing_next_to_a_layer_animation(self):
        """In the game the attack plays alongside a short layer animation; the fighter must see it."""
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        rolled = False
        for i in range(int(1.2 * 60)):
            t = i / 60
            s = sim.snap()
            e = s.entities[0]
            layered = Entity(e.id, e.type_id, e.model, e.pos, e.center, e.radius, e.height, True, False, e.health,
                             e.max_health, facing=e.facing, anim=3043000, anim_time=0.02, anim_length=0.03,
                             anims=((3000, t, 3.0), (3043000, 0.02, 0.03)))
            s2 = Snapshot(s.game, s.frame, True, s.player, s.camera, (layered,), None, screen=s.screen)
            intent = f.update(s2, 1 / 60)
            if 'roll' in intent.taps:
                rolled = True
                self.assertTrue(1.0 - DODGE_LEAD - 0.02 <= t <= 1.0, t)
        self.assertTrue(rolled)


class FightLogReplayTests(unittest.TestCase):
    """Moments from the 2026-10-11 fight log against a Godrick Soldier."""

    def test_no_swing_right_after_a_roll(self):
        # 295.69: roll for 3003006 (hit at 0.733 s); 295.70 it swung, cancelling the roll.
        WINDOWS[('c4300', 3003006)] = [(0.733, 0.833)]
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3003006, 0.50
        run(f, sim, 0.6)
        taps = [(t, tap) for t, _a, tap in sim.taps]
        roll_t = next(t for t, tap in taps if tap == 'roll')
        swings = [t for t, tap in taps if tap == 'attack' and t > roll_t]
        self.assertTrue(all(t - roll_t >= 0.75 - 1e-6 for t in swings), (roll_t, swings))

    def test_no_swing_with_a_hit_coming_within_a_second(self):
        # 296.66: it swung with a hit 0.66 s away, then couldn't roll out of its own swing.
        WINDOWS[('c4300', 3003003)] = [(1.167, 1.3)]
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3003003, 0.51
        run(f, sim, 0.4)
        self.assertNotIn('attack', [tap for _t, _a, tap in sim.taps])


class PlayerStateTests(unittest.TestCase):
    """The player's own animation matters: no roll while attacking or staggered (the game ignores it)."""

    def test_reading_the_players_animation(self):
        from game_assistant.core.combat import player_doing
        for anim, doing in [(23030000, 'attacking'), (23030010, 'attacking'), (5101, 'staggered'),
                            (5203, 'staggered'), (27020, 'dodging'), (0, 'free'), (20120, 'free'), (None, 'free')]:
            self.assertEqual(player_doing(anim), doing, anim)

    def _fight(self, enemy_anim=None, seconds=3.0, enemy_t=0.0):
        """A sim where each swing puts the player in a 1.5 s attack animation."""
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        if enemy_anim is not None:
            sim.anim, sim.anim_t = enemy_anim, enemy_t
        my = {'anim': 0, 'until': -1.0}
        swings, rolls, held_after_roll = [], [], []
        for i in range(int(seconds * 60)):
            t = i / 60
            s = sim.snap()
            pl = s.player
            if t >= my['until']:
                my['anim'] = 0
            p2 = type(pl)(pl.pos, pl.facing, pl.state, pl.health, pl.max_health, pl.stamina, pl.max_stamina,
                          anim=my['anim'], anim_time=0.0)
            s2 = Snapshot(s.game, s.frame, True, p2, s.camera, s.entities, None, screen=s.screen)
            intent = f.update(s2, 1 / 60)
            if 'attack' in intent.taps:
                swings.append(t)
                my['anim'], my['until'] = 23030000, t + 1.5
            if 'roll' in intent.taps:
                rolls.append(t)
            if rolls and t - rolls[-1] < 0.29:
                held_after_roll.append(intent.keys)
            sim.step(intent, 1 / 60)
        return swings, rolls, held_after_roll

    def test_one_swing_at_a_time(self):
        swings, _rolls, _ = self._fight(seconds=4.0)          # an idle enemy
        self.assertTrue(swings)
        gaps = [b - a for a, b in zip(swings, swings[1:])]
        self.assertTrue(all(g >= 1.5 - 1e-6 for g in gaps), gaps)   # never while the last swing plays

    def test_roll_keeps_its_direction(self):
        _s, rolls, held = self._fight(enemy_anim=3000, seconds=1.2)
        self.assertEqual(len(rolls), 1)
        self.assertTrue(held and all(k and k <= {'left', 'right', 'forward'} and k & {'left', 'right'}
                                     for k in held), held)

    def test_swings_in_the_opening_after_its_attack(self):
        WINDOWS[('c4300', 3002)] = [(0.4, 0.5)]
        sim_windows_end = 0.5
        swings, rolls, _ = self._fight(enemy_anim=3002, seconds=1.6, enemy_t=0.0)
        # (the sim's animation is 3 s long: after 0.5 s its attack is over: an opening)
        self.assertTrue(any(t > sim_windows_end for t in swings), (swings, rolls))


class SecondFightLogTests(unittest.TestCase):
    """Hits from the fight log after the research change (2026-10-11)."""

    def test_rolls_again_for_a_two_swing_chain(self):
        # 3003000 then 3003001 (hit 0.567 s in) right after: the second roll was blocked while "dodging".
        WINDOWS[('c4300', 3003001)] = [(0.567, 0.667)]
        WINDOWS[('c4300', 3003000)] = [(0.8, 0.933)]
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3003000, 0.5

        def chain(s, t):
            if s.anim == 3003000 and s.anim_t > 1.0:
                s.anim, s.anim_t = 3003001, 0.0
        run(f, sim, 1.7, script=chain)
        rolls = [a for _t, a, tap in sim.taps if tap == 'roll']
        self.assertEqual(rolls, [3003000, 3003001])

    def test_no_swing_unless_it_fits_before_a_roll_is_possible_again(self):
        # 23030000 locks the player ~1.35 s: a swing with a hit 1.2 s away can't be rolled out of.
        WINDOWS[('c4300', 3003002)] = [(1.0, 1.067)]
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        f.t = f._started + 10          # long enough that the enemy would count as passive
        f._last_enemy_attack = f.t - 10
        sim.anim, sim.anim_t = 3003002, 0.0   # its hit lands in 1.0 s
        run(f, sim, 0.5)
        self.assertNotIn('attack', [tap for _t, _a, tap in sim.taps])

    def test_punishes_a_long_recovery(self):
        WINDOWS[('c4300', 3003006)] = [(0.733, 0.833)]
        sim, f = Sim(), Fighter(windows)
        f.start(5)
        sim.anim, sim.anim_t = 3003006, 0.9   # its hit is over; 2.1 s of the 3 s animation left
        run(f, sim, 0.3)
        self.assertIn('attack', [tap for _t, _a, tap in sim.taps])
