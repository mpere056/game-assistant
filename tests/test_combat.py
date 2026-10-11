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
        self.anim_len = 3.0
        self.extra = ()                    # more animations playing with it: ((anim, t, length), ...)
        self.wall_behind = None            # the player can't step back past this z (a rock)
        self.taps = []

    def snap(self):
        e = Entity(5, 43000010, 'c4300', tuple(self.enemy), (self.enemy[0], 1.0, self.enemy[2]), 0.5, 2.0, True,
                   self.enemy_dead, self.enemy_hp, 500, facing=(0.0, 0.0, -1.0), anim=self.anim,
                   anim_time=self.anim_t, anim_length=self.anim_len,
                   anims=((self.anim, self.anim_t, self.anim_len),) + tuple(self.extra) if self.extra else ())
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        pl = Player(tuple(self.player), (0.0, 0.0, 1.0), 'alive', self.hp, self.max_hp, self.stamina, 100)
        return Snapshot('test', 1, True, pl, cam, (e,), None, screen=Screen(0, 0, 1280, 720, True))

    def step(self, intent, dt):
        self.taps += [(round(self.anim_t, 3), self.anim, t) for t in intent.taps]
        if 'forward' in intent.keys:
            self.player[2] += 4.0 * dt
        if 'back' in intent.keys:
            self.player[2] -= 4.0 * dt
            if self.wall_behind is not None:
                self.player[2] = max(self.player[2], self.wall_behind)
        if 'left' in intent.keys:
            self.player[0] -= 4.0 * dt
        if 'right' in intent.keys:
            self.player[0] += 4.0 * dt
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
        run(f, sim, 0.9)                          # enemy idle: first it waits to see if it attacks
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


class StaggerAndPoiseTests(unittest.TestCase):
    """Hitting while the enemy reels (third fight, 2026-10-11: dodged well but never attacked)."""

    def _fight(self, sim, f, seconds, react=None):
        """Each swing puts the player in a 1.5 s attack; `react(sim, since_press)` plays the enemy's
        response to the last press (a flinch 0.3 s after it, as in the log)."""
        my = {'anim': 0, 'until': -1.0, 'press': None}
        swings = []
        for i in range(int(seconds * 60)):
            t = i / 60
            if t >= my['until']:
                my['anim'] = 0
            if react and my['press'] is not None:
                react(sim, t - my['press'])
            s = sim.snap()
            pl = s.player
            p2 = type(pl)(pl.pos, pl.facing, pl.state, pl.health, pl.max_health, pl.stamina, pl.max_stamina,
                          anim=my['anim'], anim_time=0.0)
            intent = f.update(Snapshot(s.game, s.frame, True, p2, s.camera, s.entities, None, screen=s.screen),
                              1 / 60)
            if 'attack' in intent.taps:
                swings.append(t)
                my['press'] = t
                my['anim'], my['until'] = 23030000, t + 1.5
            sim.step(intent, 1 / 60)
        return swings

    @staticmethod
    def flinch(sim, since):
        if 0.3 <= since < 0.75:          # the hit lands and it flinches for 0.45 s
            if sim.anim != 3008032 or sim.anim_t > since - 0.3 + 0.05:
                sim.anim, sim.anim_t, sim.anim_len = 3008032, since - 0.3, 0.45
        elif sim.anim == 3008032:
            sim.anim, sim.anim_t, sim.anim_len = 20, 0.0, 3.0

    def test_reaction_animations(self):
        from game_assistant.core.combat import reacting
        for anim, yes in [(3008031, True), (3008032, True), (3008036, True), (3009411, True), (3003000, False),
                          (3043000, False), (20, False), (None, False)]:
            self.assertEqual(reacting(anim), yes, anim)

    def test_chains_light_attacks_while_it_flinches_up_to_the_cap(self):
        from game_assistant.core.combat import COMBO_LOW
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)       # a Godrick Soldier: poise 30
        f.start(5)
        swings = self._fight(sim, f, 3.5, react=self.flinch)
        first = swings[0]
        chain = [t for t in swings if t - first < 2.0]
        self.assertEqual(len(chain), COMBO_LOW, swings)        # pressed again during its own swing
        self.assertTrue(all(b - a < 0.9 for a, b in zip(chain, chain[1:])), chain)

    def test_fewer_hits_in_a_row_against_a_sturdy_enemy(self):
        from game_assistant.core.combat import COMBO_HIGH
        sim, f = Sim(), Fighter(windows, lambda tid: 90.0)
        f.start(5)
        f._last_enemy_attack = f.t - 10         # passive: it goes in
        f.t += 10
        swings = self._fight(sim, f, 2.0, react=self.flinch)
        self.assertEqual(len([t for t in swings if t - swings[0] < 1.5]), COMBO_HIGH, swings)  # one combo

    def test_no_chain_without_a_flinch(self):
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        swings = self._fight(sim, f, 2.0)       # its hits don't land (no reaction): one at a time
        self.assertTrue(swings)
        self.assertTrue(all(b - a >= 1.5 - 1e-6 for a, b in zip(swings, swings[1:])), swings)

    def test_a_flinch_layered_on_a_swing_with_hits_to_come_is_not_an_opening(self):
        WINDOWS[('c4300', 3003000)] = [(0.8, 0.933)]
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        sim.anim, sim.anim_t = 3003000, 0.1      # hyper armor: the flinch plays as a layer only
        sim.extra = ((3008032, 0.05, 0.45),)
        intent = f.update(sim.snap(), 1 / 60)
        self.assertNotIn('attack', intent.taps)
        self.assertIsNone(f._reeling(sim.snap().entities[0]))

    def test_keeps_hitting_a_stance_break(self):
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        f._combo = 99                            # far past the cap
        sim.anim, sim.anim_t, sim.anim_len = 3008500, 0.2, 2.5   # a long stagger
        self.assertEqual(f._reeling(sim.snap().entities[0]), 'broken')
        swings = self._fight(sim, f, 1.0, react=lambda s, since: setattr(s, 'anim_t', min(2.0, 0.2 + since)))
        self.assertGreaterEqual(len(swings), 2, swings)

    def test_punishes_a_short_recovery_only_when_its_poise_is_low(self):
        # Fight log: 3003005 hits 0.4-0.6 s in and ends ~1.5 s: 0.8 s of recovery after: never "an
        # opening" with the 1.35 s rule, so it never swung. A poise-30 soldier flinches first.
        WINDOWS[('c4300', 3003005)] = [(0.4, 0.6)]
        for poise, swings in ((30.0, True), (90.0, False)):
            sim, f = Sim(), Fighter(windows, lambda tid, p=poise: p)
            f.start(5)
            sim.anim, sim.anim_t, sim.anim_len = 3003005, 0.7, 1.5
            run(f, sim, 0.2)
            self.assertEqual('attack' in [tap for _t, _a, tap in sim.taps], swings, poise)

    def test_strafes_when_a_rock_stops_the_step_back(self):
        sim, f = Sim(enemy_z=1.5), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        f._last_roll = f.t                       # just rolled: no swing, it spaces out
        sim.wall_behind = 0.0                    # can't go back at all
        keys = []
        for _ in range(60):
            intent = f.update(sim.snap(), 1 / 60)
            keys.append(intent.keys)
            sim.step(intent, 1 / 60)
        self.assertIn('blocked', f.events)
        self.assertTrue(any(k & {'left', 'right'} for k in keys))
        self.assertGreater(abs(sim.player[0]), 0.3)    # it moved sideways out of the corner


class FourthFightLogTests(unittest.TestCase):
    """runtime/combat-20261011.jsonl, 2026-10-11 13:51: six rolls, one swing, three hits taken."""

    def test_the_same_attack_again_gets_its_own_roll(self):
        # 3003003 rolled at 5.9 s; the soldier used it again at 8.6 s: never dodged, the hit landed.
        WINDOWS[('c4300', 3003003)] = [(1.167, 1.3)]
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        sim.anim, sim.anim_t = 3003003, 0.5

        def again(s, t):
            if s.anim == 3003003 and s.anim_t > 2.0:
                s.anim, s.anim_t = 3003003, 0.0
        run(f, sim, 4.0, script=again)
        rolls = [t for t, a, tap in sim.taps if tap == 'roll' and a == 3003003]
        self.assertEqual(len(rolls), 2, sim.taps)

    def test_free_to_swing_in_the_tail_of_its_own_roll(self):
        # After rolling 3003005 the target recovered (an opening) while the player's roll animation
        # (27120, ~1.8 s) still played: it waited instead of swinging.
        WINDOWS[('c4300', 3003005)] = [(0.4, 0.6)]
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        f._last_roll = f.t - 0.8
        sim.anim, sim.anim_t, sim.anim_len = 3003005, 0.78, 1.5
        s = sim.snap()
        pl = s.player
        rolling = type(pl)(pl.pos, pl.facing, pl.state, pl.health, pl.max_health, pl.stamina, pl.max_stamina,
                           anim=27120, anim_time=0.7)
        intent = f.update(Snapshot(s.game, s.frame, True, rolling, s.camera, s.entities, None, screen=s.screen),
                          1 / 60)
        self.assertIn('attack', intent.taps)
        early = type(pl)(pl.pos, pl.facing, pl.state, pl.health, pl.max_health, pl.stamina, pl.max_stamina,
                         anim=27120, anim_time=0.3)
        f2 = Fighter(windows, lambda tid: 30.0)
        f2.start(5)
        f2._last_roll = f2.t - 0.8
        intent = f2.update(Snapshot(s.game, s.frame, True, early, s.camera, s.entities, None, screen=s.screen),
                           1 / 60)
        self.assertNotIn('attack', intent.taps)    # early in the roll: still rolling

    def test_waits_closer_to_a_low_poise_enemy(self):
        from game_assistant.core.combat import SPACING, SPACING_LOW
        for poise, want in ((30.0, SPACING_LOW), (90.0, SPACING)):
            sim, f = Sim(enemy_z=1.5), Fighter(windows, lambda tid, p=poise: p)
            f.start(5)
            f._last_roll = f.t                    # no swing: it only spaces out
            run(f, sim, 0.7)                     # (ROLL_RECOVER 0.75: still no swing)
            gap = sim.enemy[2] - sim.player[2] - 0.5
            self.assertTrue(want - 0.5 <= gap <= want + 0.1, (poise, gap))


def _enemy(id_, pos, facing=(0.0, 0.0, -1.0), anim=20, t=0.0, length=3.0, dead=False):
    return Entity(id_, 43000010, 'c4300', pos, (pos[0], 1.0, pos[2]), 0.5, 2.0, True, dead, 0 if dead else 500,
                  500, facing=facing, anim=anim, anim_time=t, anim_length=length)


def _snap(entities, forward=(0.0, 0.0, 1.0)):
    cam = Camera((0.0, 2.0, -3.0), forward, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
    pl = Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive', 600, 1000, 100, 100)
    return Snapshot('test', 1, True, pl, cam, tuple(entities), None, screen=Screen(0, 0, 1280, 720, True))


class SeveralEnemiesTests(unittest.TestCase):
    """Fifth fight (2026-10-11 14:25): the target walked off to 30 m while another soldier attacked; the
    game's lock-on was on the attacker; the fighter kept closing on the first, dodged 18 times, never swung."""

    def test_fights_the_attacker_when_the_target_walks_off(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        walker = _enemy(1, (0.0, 0.0, 20.0), facing=(0.0, 0.0, 1.0), anim=2100)   # walking away
        attacker = _enemy(2, (0.0, 0.0, 2.5), anim=3000, t=0.2)                   # swinging at the player
        f.update(_snap([walker, attacker]), 1 / 60)
        self.assertEqual(f.target_id, 2)
        self.assertIn('retarget', f.events)

    def test_follows_the_games_lock_on_choice(self):
        # Locked on, the camera faces another enemy (behind the player as seen from the target): fight it.
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        target = _enemy(1, (0.0, 0.0, -8.0), facing=(0.0, 0.0, -1.0))   # behind, facing away
        locked = _enemy(2, (0.0, 0.0, 6.0))                                # in front of the camera
        for _ in range(45):
            f.update(_snap([target, locked]), 1 / 60)
        self.assertEqual(f.target_id, 2)

    def test_unlocks_and_turns_when_the_lock_is_on_nobody_it_knows(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        target = _enemy(1, (8.0, 0.0, 0.0), facing=(1.0, 0.0, 0.0))     # to the right, camera looks north
        taps, turns = [], []
        for _ in range(45):
            intent = f.update(_snap([target]), 1 / 60)
            taps += list(intent.taps)
            turns.append(intent.turn)
        self.assertEqual(taps.count('lock'), 1)          # unlocked once, and not locked again yet
        self.assertFalse(f._locked)
        self.assertTrue(any(t > 0 for t in turns))        # turning the camera toward it
        # camera on it now: lock again
        intent = f.update(_snap([target], forward=(1.0, 0.0, 0.0)), 1 / 60)
        self.assertIn('lock', intent.taps)

    def test_next_enemy_after_a_kill_if_another_is_on_the_player(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        dead = _enemy(1, (0.0, 0.0, 2.0), dead=True)
        other = _enemy(2, (1.5, 0.0, 2.5))                       # close and facing the player
        other = Entity(2, 43000010, 'c4300', (1.5, 0.0, 2.5), (1.5, 1.0, 2.5), 0.5, 2.0, True, False, 500, 500,
                       facing=(-0.5, 0.0, -0.8), anim=20, anim_time=0.0, anim_length=3.0)
        f.update(_snap([dead, other]), 1 / 60)
        self.assertTrue(f.active)
        self.assertEqual(f.target_id, 2)
        self.assertIn('next_target', f.events)
        f2 = Fighter(windows, lambda tid: 30.0)
        f2.start(1)
        f2.update(_snap([dead]), 1 / 60)
        self.assertEqual((f2.active, f2.events), (False, ['won']))

    def test_no_punish_when_another_enemys_hit_would_land_mid_swing(self):
        WINDOWS[('c4300', 3003005)] = [(0.4, 0.6)]
        WINDOWS[('c4300', 3003000)] = [(0.8, 0.933)]
        target = _enemy(1, (0.0, 0.0, 1.5), anim=3003005, t=0.78, length=1.5)    # an opening
        for other_t, swings in ((0.2, False), (None, True)):
            f = Fighter(windows, lambda tid: 30.0)
            f.start(1)
            ents = [target]
            if other_t is not None:   # a second soldier's hit lands 0.6 s from now
                ents.append(_enemy(2, (1.5, 0.0, 1.0), facing=(-0.8, 0.0, -0.6), anim=3003000, t=other_t))
            intent = f.update(_snap(ents), 1 / 60)
            self.assertEqual('attack' in intent.taps, swings, other_t)


class LedgeTests(unittest.TestCase):
    """Sixth session (2026-10-11 14:41): chasing a fleeing animal, it walked forward off a cliff (4050,
    4000: falling) and died. The camera looks north (heading 0) in these sims."""

    def test_no_step_toward_a_drop(self):
        f = Fighter(windows, lambda tid: 30.0, lambda: ((0.0,), (0.0,)))    # the ground drops away north
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 8.0))]), 1 / 60)       # target north, 7.5 m: close in
        self.assertNotEqual(intent.keys, frozenset({'forward'}))   # around it (diagonal probes are safe)
        self.assertEqual(f.debug.get('ledge'), ['forward'])

    def test_only_safe_directions_when_most_drop(self):
        f = Fighter(windows, lambda tid: 30.0, lambda: ((-45.0, 0.0, 45.0, 90.0, 135.0), ()))
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 8.0))]), 1 / 60)
        self.assertEqual(intent.keys, frozenset({'left'}))     # only sideways is safe: not forward at all

    def test_rolls_away_from_the_drop(self):
        WINDOWS[('c4300', 3000)] = [(1.0, 1.2)]
        # a drop to the north-east (forward-right) and east: the roll goes forward-left
        for blocked, want in (((45.0, 90.0), frozenset({'forward', 'left'})),
                              ((-45.0, -90.0), frozenset({'forward', 'right'}))):
            f = Fighter(windows, lambda tid: 30.0, lambda b=blocked: ((), b))
            f.start(1)
            rolled = None
            for i in range(70):
                intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.5), anim=3000, t=i / 60)]), 1 / 60)
                if 'roll' in intent.taps:
                    rolled = intent.keys
                    break
            self.assertEqual(rolled, want, blocked)

    def test_no_roll_when_every_way_drops(self):
        WINDOWS[('c4300', 3000)] = [(1.0, 1.2)]
        f = Fighter(windows, lambda tid: 30.0, lambda: ((), (-90.0, -45.0, 45.0, 90.0)))
        f.start(1)
        taps = []
        for i in range(70):
            taps += f.update(_snap([_enemy(1, (0.0, 0.0, 1.5), anim=3000, t=i / 60)]), 1 / 60).taps
        self.assertNotIn('roll', taps)          # a hit rather than a fall

    def test_hands_a_runaway_to_the_walk(self):
        from game_assistant.core.combat import CHASE_AFTER
        f = Fighter(windows, lambda tid: -1.0)
        f.start(1)
        far = _enemy(1, (0.0, 0.0, 20.0), facing=(0.0, 0.0, 1.0), anim=2100)
        for _ in range(int((CHASE_AFTER + 0.2) * 60)):
            f.update(_snap([far]), 1 / 60)
        self.assertFalse(f.active)
        self.assertIn('fled', f.events)


class BulletTests(unittest.TestCase):
    """2026-10-11 15:3x (the "kill all of them" sweep): one hit, from a Godrick Soldier's 3003008: its swing
    is out 1.167-1.233 s, and it launches a bullet at 1.2 s. A forward roll pressed at 1.02 s covered the
    swing (and carried the player into it, gap 2.3 -> 0.5 m); the hit landed after the roll (1.28-1.57 s).
    The adapter now merges the bullet into the window: (1.167, 1.5)."""

    def test_a_long_hit_is_rolled_away_from_and_later(self):
        WINDOWS[('c4300', 3003008)] = [(1.167, 1.5)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        rolled = None
        for i in range(100):
            t = 0.8 + i / 60
            intent = f.update(_snap([_enemy(1, (0.0, 0.0, 2.8), anim=3003008, t=t)]), 1 / 60)
            if 'roll' in intent.taps:
                rolled = (t, intent.keys)
                break
        self.assertIsNotNone(rolled)
        t, keys = rolled
        self.assertIn('back', keys)                       # away, not through
        self.assertTrue(1.08 <= t <= 1.14, t)             # invincible ~1.2-1.45 s: over the bullet's hit
        # a short swing is still rolled through, 0.16 s ahead
        f2 = Fighter(windows, lambda tid: 30.0)
        f2.start(1)
        for i in range(100):
            t = 0.5 + i / 60
            intent = f2.update(_snap([_enemy(1, (0.0, 0.0, 2.0), anim=3000, t=t)]), 1 / 60)
            if 'roll' in intent.taps:
                self.assertIn('forward', intent.keys)
                self.assertTrue(1.0 - DODGE_LEAD - 0.02 <= t <= 1.0, t)
                break

    def test_the_adapter_merges_bullets_of_attacks_only(self):
        from game_assistant.games.eldenring.adapter import EldenRingAdapter
        a = EldenRingAdapter.__new__(EldenRingAdapter)
        a._attacks = {'c4310': {'attacks': {'3003008': [[1.167, 1.233]], '3003000': [[0.8, 0.933]]},
                                'bullets': {'3003008': [1.2], '3020021': [0.333], '3008032': [0.0],
                                            '3003007': [1.5, 1.5]}}}
        self.assertEqual(a.attack_windows('c4311', 3003008), [(1.167, 1.5)])
        self.assertEqual(a.attack_windows('c4311', 3003000), [(0.8, 0.933)])
        self.assertEqual(a.attack_windows('c4311', 3003007), [(1.5, 1.8)])   # a throw: a bullet alone
        self.assertEqual(a.attack_windows('c4311', 3020021), [])             # the alert shout: not a hit
        self.assertEqual(a.attack_windows('c4311', 3008032), [])             # a flinch's effect


class FollowUpTests(unittest.TestCase):
    """Seventh session (2026-10-11): two hits. A soldier's 3003000 -> 3003001 (rolled the first forward, the
    second press 0.58 s into the roll only came out at ~0.95 s: hit); a boar's charge 3000 (hits at 1.633,
    2.133, 2.633 s) rolled forward into it, then 3002 hit as it started."""

    def _first_roll(self, f, anim, t0, z=1.6):
        for i in range(200):
            t = t0 + i / 60
            intent = f.update(_snap([_enemy(1, (0.0, 0.0, z), anim=anim, t=t)]), 1 / 60)
            if 'roll' in intent.taps:
                return t, intent.keys
        return None

    def test_rolls_away_from_a_combo_it_cannot_roll_twice(self):
        WINDOWS[('c4300', 3003000)] = [(0.8, 0.933)]
        WINDOWS[('c4300', 3003001)] = [(0.567, 0.667)]
        f = Fighter(windows, lambda tid: 30.0, chains={'c4300': {'3003000': [[0.92, 3003001]]}})
        f.start(1)
        t, keys = self._first_roll(f, 3003000, 0.3)
        self.assertIn('back', keys)                          # 0.69 s to its next hit: away from it
        # without the combo known: through it, as before
        f2 = Fighter(windows, lambda tid: 30.0)
        f2.start(1)
        self.assertIn('forward', self._first_roll(f2, 3003000, 0.3)[1])

    def test_a_combo_with_time_for_a_second_roll_is_rolled_through(self):
        WINDOWS[('c4300', 3003001)] = [(0.567, 0.667)]
        WINDOWS[('c4300', 3003003)] = [(1.167, 1.3)]
        f = Fighter(windows, lambda tid: 30.0, chains={'c4300': {'3003001': [[0.76, 3003003]]}})
        f.start(1)
        self.assertIn('forward', self._first_roll(f, 3003001, 0.1)[1])   # 1.36 s to the next: time to roll

    def test_a_multi_hit_charge_is_rolled_away_from(self):
        WINDOWS[('c4300', 3100)] = [(1.633, 1.833), (2.133, 2.333), (2.633, 2.767)]   # (3000 is the sims' swing)
        f = Fighter(windows, lambda tid: -1.0)
        f.start(1)
        t, keys = self._first_roll(f, 3100, 1.0, z=2.5)
        self.assertIn('back', keys)
        self.assertTrue(1.633 - DODGE_LEAD - 0.02 <= t <= 1.633, t)

    def test_learns_a_combo_it_sees(self):
        WINDOWS[('c4300', 3003000)] = [(0.8, 0.933)]
        WINDOWS[('c4300', 3003001)] = [(0.567, 0.667)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        for i in range(90):                                  # 3003000 from 0.3 s, then 3003001 at 0.93 s
            t = 0.3 + i / 60
            anim, at = (3003000, t) if t < 0.93 else (3003001, t - 0.93)
            f.update(_snap([_enemy(1, (0.0, 0.0, 6.0), anim=anim, t=at)]), 1 / 60)
        got = f.chains.get('c4300', {}).get('3003000')
        self.assertTrue(got and got[0][1] == 3003001 and abs(got[0][0] - 0.93) < 0.05, got)
        self.assertTrue(f.chains_changed)


class EighthSessionTests(unittest.TestCase):
    """2026-10-11 16:35: a soldier behind a wall (10 s at a constant 2.05 m), then two soldiers with the lock
    on one and the target the other (15 openings, one swing), a swing into a known combo (three hits)."""

    def test_the_locked_on_enemy_is_the_target_at_once(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        side = _enemy(1, (4.0, 0.0, 0.0))            # the target, off to the right
        ahead = _enemy(2, (0.0, 0.0, 3.0))           # the camera (locked) looks at this one
        f.update(_snap([side, ahead]), 1 / 60)
        self.assertEqual(f.target_id, 2)

    def test_no_opening_in_an_attack_it_chains_out_of(self):
        WINDOWS[('c4300', 3702)] = [(0.4, 0.5)]
        WINDOWS[('c4300', 3700)] = [(0.8, 0.933)]
        target = _enemy(1, (0.0, 0.0, 1.5), anim=3702, t=1.0, length=3.0)
        f = Fighter(windows, lambda tid: 90.0, chains={'c4300': {'3702': [[1.2, 3700]]}})
        f.start(1)
        self.assertFalse(f._opening(target, 0.45))   # it goes into 3000 at 1.2 s: no time
        f2 = Fighter(windows, lambda tid: 90.0)
        f2.start(1)
        self.assertTrue(f2._opening(target, 0.45))

    def test_rolls_away_when_the_other_enemys_hit_follows_too_soon(self):
        WINDOWS[('c4300', 3700)] = [(0.8, 0.933)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        rolled = None
        for i in range(60):
            t = 0.4 + i / 60
            a = _enemy(1, (0.0, 0.0, 1.6), anim=3700, t=t)
            b = _enemy(2, (1.5, 0.0, 1.2), facing=(-0.8, 0.0, -0.6), anim=3700, t=t - 0.4)   # hits 0.4 s later
            intent = f.update(_snap([a, b]), 1 / 60)
            if 'roll' in intent.taps:
                rolled = intent.keys
                break
        self.assertIsNotNone(rolled)
        self.assertIn('back', rolled)

    def test_no_swing_through_a_wall_and_a_way_round_it(self):
        f = Fighter(windows, lambda tid: 30.0, sees=lambda eid: False)
        f.start(1)
        f._last_enemy_attack = f.t - 10
        f.t += 10                                    # passive: it would swing
        target = _enemy(1, (0.0, 0.0, 2.2))          # 1.7 m: in reach, but behind a wall
        taps, keys = [], []
        for _ in range(int(1.5 * 60)):
            intent = f.update(_snap([target]), 1 / 60)
            taps += list(intent.taps)
            keys.append(intent.keys)
        self.assertNotIn('attack', taps)
        self.assertTrue(any(k & {'left', 'right'} and not k & {'forward'} for k in keys))   # sideways round it
        self.assertIn('blocked', f.events)

    def test_gives_up_on_one_it_cannot_reach(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        target = _enemy(1, (0.0, 0.0, 6.0))          # the sim doesn't move the player: no progress at all
        for _ in range(int(8 * 60)):
            f.update(_snap([target]), 1 / 60)
            if not f.active:
                break
        self.assertFalse(f.active)
        self.assertIn('unreachable', f.events)
        self.assertIn(1, f.unreachable)

    def test_a_blocked_swing_means_a_shield_only_openings_after(self):
        sim, f = Sim(), Fighter(windows, lambda tid: 30.0)
        f.start(5)
        f._last_enemy_attack = f.t - 10
        f.t += 10
        run(f, sim, 0.2)
        self.assertIn('attack', [tap for _t, _a, tap in sim.taps])   # an idle enemy: it tries
        run(f, sim, 1.0)                                             # no flinch: it blocked
        self.assertIn('guarded', f.events)
        sim.taps.clear()
        run(f, sim, 3.0)
        self.assertNotIn('attack', [tap for _t, _a, tap in sim.taps])   # not again while it's idle
