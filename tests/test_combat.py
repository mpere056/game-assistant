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
        self.cam_yaw = 0.0                 # degrees from +z: the fighter's camera turns are applied
        self.taps = []

    def snap(self):
        e = Entity(5, 43000010, 'c4300', tuple(self.enemy), (self.enemy[0], 1.0, self.enemy[2]), 0.5, 2.0, True,
                   self.enemy_dead, self.enemy_hp, 500, facing=(0.0, 0.0, -1.0), anim=self.anim,
                   anim_time=self.anim_t, anim_length=self.anim_len,
                   anims=((self.anim, self.anim_t, self.anim_len),) + tuple(self.extra) if self.extra else ())
        a = math.radians(self.cam_yaw)
        fwd = (math.sin(a), 0.0, math.cos(a))
        cam = Camera((0.0, 2.0, -3.0), fwd, (math.cos(a), 0.0, -math.sin(a)), (0.0, 1.0, 0.0), 60, 16 / 9)
        pl = Player(tuple(self.player), (0.0, 0.0, 1.0), 'alive', self.hp, self.max_hp, self.stamina, 100)
        return Snapshot('test', 1, True, pl, cam, (e,), None, screen=Screen(0, 0, 1280, 720, True))

    def step(self, intent, dt):
        self.taps += [(round(self.anim_t, 3), self.anim, t) for t in intent.taps]
        self.cam_yaw += intent.turn
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
        sim, f = Sim(hp=150), Fighter(windows)        # (above 20 % it heals first: HealTests)
        f.start(5)
        f.update(sim.snap(), 1 / 60)
        self.assertEqual(f.state, 'escaping')          # an enemy close: run clear first
        sim.enemy[2] = 30.0
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
        # (one direction held all along: without one the roll is a backstep; straight back is a roll too)
        self.assertTrue(held and all(k and k <= {'left', 'right', 'forward', 'back'} for k in held), held)
        self.assertEqual(len(set(held)), 1, held)

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
        sim, f = Sim(), Fighter(windows, lambda tid: 90.0)   # sturdy: no interrupting it
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
        for poise, swings in ((30.0, True), (150.0, False)):   # (60-120 is the middle tier: MiddleTierTests)
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
        for poise, want in ((30.0, SPACING_LOW), (150.0, SPACING)):
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
        f._target_since = f.t - 3.0                  # held long enough (TARGET_HOLD)
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
        self.assertEqual(taps.count('lock'), 0)          # no press (the game may have dropped it already):
                                                          # it stops believing in the lock and turns
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
            f = Fighter(windows, lambda tid: 30.0, lambda b=blocked: ((), b), chains={'_solo': {'c4300': {'3000': 2}}})
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
        f = Fighter(windows, lambda tid: 30.0, lambda: ((), (-135.0, -90.0, -45.0, 0.0, 45.0, 90.0, 135.0, 180.0)))
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
        # a short swing (seen to end on its own) is still rolled through, 0.16 s ahead
        f2 = Fighter(windows, lambda tid: 30.0, chains={'_solo': {'c4300': {'3000': 2}}})
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
        # without the combo known but seen to end on its own: through it
        f2 = Fighter(windows, lambda tid: 30.0, chains={'_solo': {'c4300': {'3003000': 2}}})
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
        f._target_since = f.t - 3.0                  # held long enough (TARGET_HOLD)
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
        self.assertIn('walled', f.events)                 # first: round by the route (the app walks)
        f.start(1)                                        # back from the walk, still walled in
        for _ in range(int(8 * 60)):
            f.update(_snap([target]), 1 / 60)
            if not f.active:
                break
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


class NinthSessionTests(unittest.TestCase):
    """20:31: a 39 s fight against one soldier, 22 rolls, no damage, 4 swings: after each roll it stepped back
    out of reach and waited 0.75 s, and the soldier's 0.3-0.7 s openings went by."""

    def test_interrupts_a_low_poise_wind_up_and_learns_when_it_cannot(self):
        WINDOWS[('c4300', 3710)] = [(1.2, 1.3)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.8), anim=3710, t=0.1)]), 1 / 60)
        self.assertIn('attack', intent.taps)                 # 1.1 s to its hit: strike first
        for i in range(60):                                  # it takes the hit (health down), no flinch: armoured
            hit = _enemy(1, (0.0, 0.0, 1.8), anim=3710, t=0.12 + i / 60)
            hit = Entity(hit.id, hit.type_id, hit.model, hit.pos, hit.center, hit.radius, hit.height, True, False,
                         470, 500, facing=hit.facing, anim=3710, anim_time=0.12 + i / 60, anim_length=3.0)
            f.update(_snap([hit]), 1 / 60)
        self.assertIn('armored', f.events)
        f._last_swing = -1e9
        f._last_enemy_attack = f.t
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.8), anim=3710, t=0.1)]), 1 / 60)
        self.assertNotIn('attack', intent.taps)              # not that attack again
        f2 = Fighter(windows, lambda tid: 90.0)              # sturdy: never
        f2.start(1)
        self.assertNotIn('attack', f2.update(_snap([_enemy(1, (0.0, 0.0, 1.8), anim=3710, t=0.1)]), 1 / 60).taps)

    def test_stays_close_while_it_recovers(self):
        WINDOWS[('c4300', 3711)] = [(0.4, 0.5)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._last_roll = f.t                                   # just rolled: no swing yet
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.2), anim=3711, t=0.9, length=3.0)]), 1 / 60)
        self.assertNotIn('back', intent.keys)                # no stepping away from the opening

    def test_swings_half_a_second_after_a_roll(self):
        WINDOWS[('c4300', 3712)] = [(0.4, 0.5)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._last_roll = f.t - 0.52
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.8), anim=3712, t=1.0, length=2.0)]), 1 / 60)
        self.assertIn('attack', intent.taps)



class ReachTests(unittest.TestCase):
    """Eleventh session (2026-10-11): the user asked for each enemy's real reach (game data) and the player's
    weapon's; a bat's dives were misjudged (it was chased while flying off; a miss was taken for armour)."""

    def _first_roll(self, f, enemy_at, anim=3720, t0=0.4):
        for i in range(90):
            t = t0 + i / 60
            if 'roll' in f.update(_snap([_enemy(1, enemy_at, anim=anim, t=t)]), 1 / 60).taps:
                return t
        return None

    def test_rolls_only_within_the_attacks_reach_plus_its_lunge(self):
        WINDOWS[('c4300', 3720)] = [(1.0, 1.1)]
        reach = lambda model, type_id, anim: 2.6          # a Godrick Soldier's swing (game data)
        f = Fighter(windows, lambda tid: 30.0, reach_of=reach)
        f.start(1)
        self.assertIsNotNone(self._first_roll(f, (0.0, 0.0, 4.5)))     # 4.5 <= 2.6 + 1.5 + 0.4 + 0.4
        f2 = Fighter(windows, lambda tid: 30.0, reach_of=reach)
        f2.start(1)
        self.assertIsNone(self._first_roll(f2, (0.0, 0.0, 6.5)))       # out of its reach
        f3 = Fighter(windows, lambda tid: 30.0, reach_of=reach, chains={'_lunge': {'c4300': {'3720': 4.0}}})
        f3.start(1)
        self.assertIsNotNone(self._first_roll(f3, (0.0, 0.0, 6.5)))    # but it lunges 4 m: in reach

    def test_learns_how_far_an_attack_carries_the_enemy(self):
        WINDOWS[('c4300', 3721)] = [(0.6, 0.7)]
        f = Fighter(windows, lambda tid: 30.0, reach_of=lambda m, t, a: 1.0)
        f.start(1)
        for i in range(48):                                  # a dive from 9 m to 3 m by its hit
            t = i / 60
            z = 9.0 - 6.0 * min(1.0, t / 0.6)
            f.update(_snap([_enemy(1, (0.0, 0.0, z), anim=3721, t=t)]), 1 / 60)
        got = f.chains.get('_lunge', {}).get('c4300', {}).get('3721')
        self.assertTrue(got and 5.5 <= got <= 6.5, got)

    def test_the_weapons_reach_from_its_type_then_measured(self):
        from game_assistant.core.combat import WEAPON_REACH
        f = Fighter(windows, lambda tid: 30.0, weapon=lambda: (25000000, 25))   # a spear
        f.start(1)
        self.assertEqual(f.my_reach, WEAPON_REACH[25])
        f._learn_my_reach(2.9)
        self.assertAlmostEqual(f.my_reach, 3.0)
        self.assertEqual(f.chains['_my_reach']['25000000'], 3.0)
        f._learn_my_reach(9.0)                                 # never beyond the type's + MY_REACH_GROW
        self.assertLessEqual(f.my_reach, WEAPON_REACH[25] + 0.7 + 1e-6)

    def test_waits_for_a_target_flying_off_instead_of_chasing(self):
        f = Fighter(windows, lambda tid: -1.0)
        f.start(1)
        keys = []
        for i in range(30):
            z = 6.0 + i * 0.15                                 # 9 m/s away
            keys.append(f.update(_snap([_enemy(1, (0.0, 0.0, z))]), 1 / 60).keys)
        self.assertFalse(any('forward' in k for k in keys[10:]), keys[10:])


class GroupTests(unittest.TestCase):
    """Eleventh session: 47 target switches in 45 s against a group, 4 swings, then death."""

    def test_holds_its_target_and_locks_back_on_instead_of_switching(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        side = _enemy(1, (4.0, 0.0, 0.0))
        ahead = _enemy(2, (0.0, 0.0, 3.0))
        taps = []
        for _ in range(60):                           # 1 s: within TARGET_HOLD
            taps += list(f.update(_snap([side, ahead]), 1 / 60).taps)
        self.assertEqual(f.target_id, 1)
        self.assertFalse(f._locked)                   # the lock isn't on it: turn back to its own target
        self.assertGreater(f.update(_snap([side, ahead]), 1 / 60).turn, 0.0)

    def test_next_target_in_front_and_weakest(self):
        from game_assistant.core.combat import target_score
        from game_assistant.core.interface import Camera
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        p = (0.0, 0.0, 0.0)
        front = _enemy(1, (0.0, 0.0, 4.0))
        behind = _enemy(2, (0.0, 0.0, -3.0))
        self.assertLess(target_score(front, p, cam), target_score(behind, p, cam))   # in front, though further
        weak = Entity(3, 43000010, 'c4300', (1.0, 0.0, 4.0), (1.0, 1.0, 4.0), 0.5, 2.0, True, False, 50, 500,
                      facing=(0.0, 0.0, -1.0), anim=20, anim_time=0.0, anim_length=3.0)
        self.assertLess(target_score(weak, p, cam), target_score(front, p, cam))     # nearly dead: first

    def test_backs_off_away_from_the_group_and_round_a_wall(self):
        f = Fighter(windows, lambda tid: 30.0, lambda: ((), (), (180.0,)))   # a rock straight behind (south)
        f.start(1)
        f._last_roll = f.t - 0.35                     # rolled just now: no swing yet, it spaces out
        a = _enemy(1, (-0.5, 0.0, 1.5))
        b = _enemy(2, (0.5, 0.0, 1.5))
        intent = f.update(_snap([a, b]), 1 / 60)
        self.assertEqual(f.state, 'spacing')
        self.assertTrue(intent.keys and 'back' in intent.keys and intent.keys != {'back'}, intent.keys)


class HealTests(unittest.TestCase):
    def _hurt(self, hp):
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        return cam, Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive', hp, 1000, 100, 100)

    def _snap_hp(self, hp, entities):
        cam, pl = self._hurt(hp)
        return Snapshot('test', 1, True, pl, cam, tuple(entities), None, screen=Screen(0, 0, 1280, 720, True))

    def test_drinks_when_hurt_and_nothing_can_reach_it_in_time(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(self._snap_hp(400, [_enemy(1, (0.0, 0.0, 9.0))]), 1 / 60)
        self.assertIn('item', intent.taps)
        self.assertIn('heal', f.events)

    def test_backs_away_first_when_an_enemy_is_close(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(self._snap_hp(400, [_enemy(1, (0.0, 0.0, 2.5))]), 1 / 60)
        self.assertNotIn('item', intent.taps)
        self.assertEqual(f.state, 'retreating')
        self.assertIn('back', intent.keys)

    def test_stops_trying_when_drinking_heals_nothing(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        for _ in range(int(12 * 60)):                 # health never comes back: no flasks
            f.update(self._snap_hp(400, [_enemy(1, (0.0, 0.0, 9.0))]), 1 / 60)
        self.assertEqual(f.events.count('heal'), 2)
        self.assertIn('no_flask', f.events)



class VariantWindowsTests(unittest.TestCase):
    def test_a_variant_with_only_reach_uses_its_base_models_timings(self):
        # 2026-10-11 23:53: c4311 got an entry with only "dummy_reach": no windows, no roll, no swing.
        from game_assistant.games.eldenring.adapter import EldenRingAdapter
        a = EldenRingAdapter.__new__(EldenRingAdapter)
        a._attacks = {'c4310': {'attacks': {'3003006': [[0.733, 0.833]]}, 'bullets': {}},
                      'c4311': {'dummy_reach': {'10': 2.3}}}
        self.assertEqual(a.attack_windows('c4311', 3003006), [(0.733, 0.833)])


class CrowdTests(unittest.TestCase):
    """2026-10-12: eight soldiers within 6 m, hits ~0.5 s apart from different ones, a roll only every
    ~0.95 s: four hits while a second roll waited, then death."""

    def _crowd(self, n=4, r=2.0):
        return [_enemy(i + 1, (r * math.sin(i), 0.0, 2.0 + 0.5 * math.cos(i))) for i in range(n)]

    def test_sprints_out_of_a_crowd_away_from_its_middle(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap(self._crowd()), 1 / 60)    # all of them north of the player
        self.assertEqual(f.state, 'escaping')
        self.assertIn('escape', f.events)
        self.assertIn('sprint', intent.keys)
        self.assertIn('back', intent.keys)                 # away (south)

    def test_a_roll_while_sprinting_lets_go_of_the_sprint_first(self):
        WINDOWS[('c4300', 3730)] = [(0.5, 0.6)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        crowd = self._crowd()
        self.assertIn('sprint', f.update(_snap(crowd), 1 / 60).keys)     # escaping at a sprint
        seq = []
        for i in range(30):
            crowd[0] = _enemy(1, crowd[0].pos, anim=3730, t=0.2 + i / 60)
            it = f.update(_snap(crowd), 1 / 60)
            seq.append((it.keys, it.taps))
            if 'roll' in it.taps:
                break
        k = next(i for i, (_k, t) in enumerate(seq) if 'roll' in t)
        self.assertNotIn('sprint', seq[k - 1][0])            # the sprint let go before the roll
        self.assertNotIn('sprint', seq[k][0])

    def test_turns_back_to_fight_once_clear_and_gives_up_after_three_crowds(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f.update(_snap(self._crowd()), 1 / 60)
        f.update(_snap([_enemy(1, (0.0, 0.0, 6.0)), _enemy(2, (0.0, 0.0, 12.0))]), 1 / 60)   # strung out
        self.assertNotEqual(f.state, 'escaping')
        for _ in range(3):                                  # caught again and again, with its health full:
            f.update(_snap(self._crowd()), 1 / 60)
            f.update(_snap([_enemy(1, (0.0, 0.0, 6.0))]), 1 / 60)
        self.assertNotIn('surrounded', f.events)            # it goes on (twenty-third session)
        hurt = Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive', 400, 1000, 100, 100)

        def snap_hurt(entities):
            s = _snap(entities)
            return Snapshot(s.game, s.frame, True, hurt, s.camera, s.entities, None, screen=s.screen)
        f.update(snap_hurt(self._crowd()), 1 / 60)          # ...and once more, hurt now
        self.assertTrue(f.active)                           # not handed back in the middle of them:
        self.assertEqual(f.state, 'escaping')               # it runs clear first
        f.update(snap_hurt([_enemy(1, (0.0, 0.0, 20.0))]), 1 / 60)
        self.assertFalse(f.active)
        self.assertIn('surrounded', f.events)


class FourteenthSessionTests(unittest.TestCase):
    """2026-10-12: a 24 s fight with one soldier (5 swings; the camera off the target, a swallowed press
    holding back the next swing), and a crowd escape that walked backwards (locked on, no sprint)."""

    def _snap_me(self, anim, entities):
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        pl = Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive', 600, 1000, 100, 100, anim=anim, anim_time=0.3)
        return Snapshot('test', 1, True, pl, cam, tuple(entities), None, screen=Screen(0, 0, 1280, 720, True))

    def test_a_swallowed_swing_doesnt_hold_back_the_next(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._last_enemy_attack = f.t - 10
        f.t += 10                                       # passive: it swings
        target = _enemy(1, (0.0, 0.0, 1.8))
        swings = []
        for i in range(60):                             # the player stays idle: no swing ever comes out
            if 'attack' in f.update(self._snap_me(0, [target]), 1 / 60).taps:
                swings.append(i / 60)
        self.assertGreaterEqual(len(swings), 2, swings)
        self.assertLess(swings[1] - swings[0], 0.9)     # not held back SWING_EVERY by the swallowed press

    def test_the_escape_turns_the_lock_off(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        crowd = [_enemy(i + 1, (math.sin(i), 0.0, 2.0)) for i in range(4)]
        intent = f.update(_snap(crowd), 1 / 60)
        self.assertEqual(f.state, 'escaping')
        self.assertIn('lock', intent.taps)
        self.assertFalse(f._locked)
        self.assertIn('sprint', intent.keys)


class FifteenthSessionTests(unittest.TestCase):
    """2026-10-12: it walked up to soldiers and waited for them to draw and attack; openings went by; five
    hits from a weapon moveset whose attacks had no timings (5003008 imports 1003008's events)."""

    def test_strikes_an_idle_soldier_at_once(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 2.0))]), 1 / 60)     # just arrived, it stands there
        self.assertIn('attack', intent.taps)

    def test_but_not_into_a_hit_due_soon(self):
        WINDOWS[('c4300', 3740)] = [(0.6, 0.7)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 2.0), anim=3740, t=0.1)]), 1 / 60)   # its hit in 0.5 s
        self.assertNotIn('attack', intent.taps)

    def test_animation_imports_are_read(self):
        import struct
        from game_assistant.games.eldenring import tae
        data = bytearray(0x200)
        data[:4] = b'TAE '
        struct.pack_into('<iQ', data, 0x54, 1, 0x60)          # one animation, listed at 0x60
        struct.pack_into('<QQ', data, 0x60, 5003008, 0x80)    # id, its data
        struct.pack_into('<QQQQi', data, 0x80, 0, 0, 0, 0xC0, 0)   # no events; anim file header at 0xC0
        struct.pack_into('<Q', data, 0xC0, 1)                 # "import another animation"
        struct.pack_into('<i', data, 0xC0 + 0x18, 1003008)
        self.assertEqual(tae.imports(bytes(data)), {5003008: 1003008})


class LearnedReachTests(unittest.TestCase):
    def test_a_hit_from_further_than_the_data_raises_that_attacks_reach(self):
        WINDOWS[('c4300', 3750)] = [(0.6, 0.7)]
        f = Fighter(windows, lambda tid: 30.0, reach_of=lambda m, t, a: 2.0)   # the data: a sword's reach
        f.start(1)
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)

        def snap(hp, t):
            pl = Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive', hp, 1000, 100, 100)
            return Snapshot('test', 1, True, pl, cam, (_enemy(1, (0.0, 0.0, 6.0), anim=3750, t=t),), None,
                            screen=Screen(0, 0, 1280, 720, True))
        f.update(snap(900, 0.6), 1 / 60)
        f.update(snap(800, 0.62), 1 / 60)                      # hit from 6 m: a spear, not a sword
        got = f.chains['_reach']['c4300']['3750']
        self.assertTrue(5.0 < got < 6.0, got)
        self.assertTrue(f._threatens(_enemy(1, (0.0, 0.0, 6.0), anim=3750, t=0.3), (0.0, 0.0, 0.0), 3750, 0.3))


class FacingTests(unittest.TestCase):
    """Sixteenth session: in reach, nothing coming, the camera turned away: no swing; a sidestep as
    "blocked"; a swing gone wide taken for a shield, which stopped the attacks for the rest of the fight."""

    def test_turns_to_the_target_even_when_it_believes_its_locked(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._locked = True
        f._last_roll = f.t - 0.35                       # no swing yet: it just turns
        intent = f.update(_snap([_enemy(1, (2.0, 0.0, 0.5))]), 1 / 60)   # to the right, in reach
        self.assertGreater(intent.turn, 0.0)

    def test_in_reach_with_the_camera_off_it_turns_rather_than_walks(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap([_enemy(1, (1.6, 0.0, 0.3))]), 1 / 60)   # 80 degrees off, 1.1 m
        self.assertEqual(f.state, 'turning')
        self.assertEqual(intent.keys, frozenset())
        self.assertGreater(intent.turn, 0.0)
        self.assertNotIn('blocked', f.events)

    def test_a_swing_off_centre_holds_the_way_to_the_target(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        intent = f.update(_snap([_enemy(1, (0.6, 0.0, 1.6))]), 1 / 60)   # ~20 degrees to the right
        self.assertIn('attack', intent.taps)
        self.assertIn('forward', intent.keys)

    def test_a_shield_needs_a_swing_in_front_and_wears_off(self):
        from game_assistant.core.combat import GUARD_FOR
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        e = _enemy(1, (0.0, 0.0, 1.8))
        f._swing_check = [f.t, 1, False, False, None, 'c4300', 500, 1.0, 35.0]   # swung 35 degrees off
        f.t += 1.0
        f._check_guard(e)
        self.assertFalse(f._shielded(e))               # went wide: not a shield
        f._swing_check = [f.t, 1, False, False, None, 'c4300', 500, 1.0, 5.0]    # straight at it
        f.t += 1.0
        f._check_guard(e)
        self.assertTrue(f._shielded(e))
        f.t += GUARD_FOR + 0.1
        self.assertFalse(f._shielded(e))               # tried again later


class SeventeenthSessionTests(unittest.TestCase):
    """2026-10-12: after getting room it chased its old target back into the group (424 damage); a swing
    from 2.4 m fell short; a poise-65 soldier fought as a tough enemy: 27 s without a swing."""

    def test_after_an_escape_the_nearest_and_no_chasing_back(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        crowd = [_enemy(i + 1, (math.sin(i), 0.0, 2.0)) for i in range(4)]
        f.update(_snap(crowd), 1 / 60)
        self.assertEqual(f.state, 'escaping')
        old = _enemy(1, (0.0, 0.0, 25.0))                 # its old target far behind, in the group
        chaser = _enemy(9, (0.0, 0.0, -9.0))             # one that came after it
        f.update(_snap([old, chaser]), 1 / 60)           # clear: escape over
        f.update(_snap([old, chaser]), 1 / 60)
        self.assertEqual(f.target_id, 9)                 # the nearest
        for _ in range(60):
            f.update(_snap([old, chaser]), 1 / 60)
        self.assertNotIn('fled', f.events)               # no chase back
        self.assertTrue(f.active)

    def test_no_attack_when_safe_from_the_edge_of_reach(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        edge = _enemy(1, (0.0, 0.0, 0.5 + f.my_reach - 0.1))   # just inside the reach
        self.assertNotIn('attack', f.update(_snap([edge]), 1 / 60).taps)


class MiddleTierTests(unittest.TestCase):
    def test_a_poise_65_soldier_is_punished_in_a_0_8_s_opening(self):
        WINDOWS[('c4300', 3760)] = [(0.4, 0.6)]
        f = Fighter(windows, lambda tid: 65.0)
        f.start(1)
        target = _enemy(1, (0.0, 0.0, 1.8), anim=3760, t=0.8, length=1.75)   # 0.95 s of recovery left
        self.assertIn('attack', f.update(_snap([target]), 1 / 60).taps)
        f2 = Fighter(windows, lambda tid: 150.0)          # a tough one: not that soon
        f2.start(1)
        self.assertNotIn('attack', f2.update(_snap([target]), 1 / 60).taps)



class EighteenthSessionTests(unittest.TestCase):
    def test_walks_in_to_an_idle_soldier_just_out_of_reach_and_strikes(self):
        # 2026-10-12: the reach margin on going in left it standing out of reach while the soldier drew.
        sim, f = Sim(enemy_z=0.5 + 2.4), Fighter(windows, lambda tid: 30.0)   # 2.4 m from its edge
        f.start(5)
        run(f, sim, 1.0)
        self.assertIn('attack', [tap for _t, _a, tap in sim.taps])            # walked in and struck within 1 s


class ChargerTests(unittest.TestCase):
    def test_no_swing_while_another_runs_in(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        target = _enemy(1, (0.0, 0.0, 1.8))
        for i in range(5):                               # (its speed is measured over a few frames first)
            f.update(_snap([_enemy(1, (0.0, 0.0, 6.0)), _enemy(2, (0.0, 0.0, -7.0 + 5.0 * i / 60))]), 1 / 60)
        taps = []
        for i in range(10):                              # a second soldier running in at 5 m/s from 6.6 m
            other = _enemy(2, (0.0, 0.0, -6.6 + 5.0 * i / 60))
            taps += list(f.update(_snap([target, other]), 1 / 60).taps)
        self.assertNotIn('attack', taps)
        self.assertTrue(f.debug.get('charger'))
        f2 = Fighter(windows, lambda tid: 30.0)          # standing still there: swing
        f2.start(1)
        taps = []
        for i in range(10):
            taps += list(f2.update(_snap([target, _enemy(2, (0.0, 0.0, -6.0))]), 1 / 60).taps)
        self.assertIn('attack', taps)


class TwentiethSessionTests(unittest.TestCase):
    def test_an_attack_not_seen_before_is_rolled_away_from(self):
        WINDOWS[('c4300', 3770)] = [(0.8, 0.9)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        rolled = None
        for i in range(60):
            it = f.update(_snap([_enemy(1, (0.0, 0.0, 1.6), anim=3770, t=0.4 + i / 60)]), 1 / 60)
            if 'roll' in it.taps:
                rolled = it.keys
                break
        self.assertIn('back', rolled)

    def test_counts_attacks_that_end_on_their_own(self):
        WINDOWS[('c4300', 3771)] = [(0.4, 0.5)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        for i in range(30):
            f.update(_snap([_enemy(1, (0.0, 0.0, 6.0), anim=3771, t=0.5 + i / 60)]), 1 / 60)
        for i in range(60):                                # then idle, no attack after it
            f.update(_snap([_enemy(1, (0.0, 0.0, 6.0))]), 1 / 60)
        self.assertEqual(f.chains['_solo']['c4300']['3771'], 1)

    def test_no_attack_when_safe_during_a_wind_up(self):
        WINDOWS[('c4300', 3772)] = [(1.5, 1.6)]
        f = Fighter(windows, lambda tid: 30.0, chains=None or {})
        f.start(1)
        f._armored.add(('c4300', 3772))                    # (it doesn't flinch)
        intent = f.update(_snap([_enemy(1, (0.0, 0.0, 1.8), anim=3772, t=0.2)]), 1 / 60)
        self.assertNotIn('attack', intent.taps)



class OpenGroundTests(unittest.TestCase):
    """Twenty-first session: escaping, it ran straight away from the crowd into an enclosed corner and rolled
    into the wall again and again."""

    def test_follows_the_route_to_open_ground(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        crowd = [_enemy(i + 1, (math.sin(i), 0.0, 2.0)) for i in range(4)]
        f.update(_snap(crowd), 1 / 60)                     # escaping: straight away (south) at first
        f.escape_route = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]   # the app found open ground to the east
        intent = f.update(_snap(crowd), 1 / 60)
        self.assertEqual(f.state, 'escaping')
        self.assertIn('right', intent.keys)                # east, along the route (the camera looks north)
        self.assertIn('sprint', intent.keys)

    def test_rolls_not_into_a_wall_when_another_way_is_clear(self):
        WINDOWS[('c4300', 3780)] = [(0.8, 0.9)]
        f = Fighter(windows, lambda tid: 30.0, lambda: ((), (), (-135.0, 180.0)))   # walls back-left and back
        f.start(1)
        rolled = None
        for i in range(60):
            it = f.update(_snap([_enemy(1, (0.0, 0.0, 1.6), anim=3780, t=0.4 + i / 60)]), 1 / 60)
            if 'roll' in it.taps:
                rolled = it.keys
                break
        self.assertIsNotNone(rolled)
        self.assertNotEqual(rolled, frozenset({'back', 'left'}))


class TwentySecondSessionTests(unittest.TestCase):
    """2026-10-12 13:43-13:58 at Gatefront: 39 of 62 hits that day came right after a roll (keys taken relative
    to an unlocked camera: rolls landed by other soldiers); single swings into a soldier's armoured thrust; hits
    from soldiers standing next to the target; a wolf joining the fight."""

    @staticmethod
    def _landing(keys, cam_yaw):
        from game_assistant.core.autowalk import DIRECTIONS
        rel = next(a for a, k in DIRECTIONS if frozenset(k) == keys)
        a = math.radians(cam_yaw + rel)
        return (math.sin(a) * 2.6, math.cos(a) * 2.6)

    def _roll(self, f, entities_at, forward):
        for i in range(90):
            it = f.update(_snap(entities_at(0.3 + i / 60), forward=forward), 1 / 60)
            if 'roll' in it.taps:
                return it.keys
        return None

    def test_an_away_roll_goes_away_in_the_world_whatever_the_camera(self):
        WINDOWS[('c4300', 3790)] = [(0.8, 0.9)]
        f = Fighter(windows, lambda tid: 30.0)            # an attack not seen to end alone: away from it
        f.start(1)
        keys = self._roll(f, lambda t: [_enemy(1, (0.0, 0.0, 1.6), anim=3790, t=t)], forward=(1.0, 0.0, 0.0))
        x, z = self._landing(keys, 90.0)                   # the camera looks east, the attacker is north
        self.assertLess(z, -1.0, keys)                     # landed south of the player: away from it

    def test_a_roll_doesnt_land_next_to_another_soldier(self):
        WINDOWS[('c4300', 3791)] = [(0.8, 0.9)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        behind = _enemy(2, (0.0, 0.0, -3.0), facing=(0.0, 0.0, 1.0))
        keys = self._roll(f, lambda t: [_enemy(1, (0.0, 0.0, 1.6), anim=3791, t=t), behind], forward=(0.0, 0.0, 1.0))
        x, z = self._landing(keys, 0.0)
        self.assertGreater(math.hypot(x - 0.0, z + 3.0) - 0.5, 2.0, keys)   # not into the one behind
        self.assertLess(z, 1.6, keys)                                       # nor into the attacker

    def test_learns_an_attack_that_goes_on_through_a_hit(self):
        WINDOWS[('c4300', 3792)] = [(1.4, 1.5)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        chains = f.chains

        def target(t, hp, layer=False):
            anims = ((3792, t, 3.0),) + (((9000, t - 0.4, 0.5),) if layer else ())
            return Entity(1, 43000010, 'c4300', (0.0, 0.0, 1.8), (0.0, 1.0, 1.8), 0.5, 2.0, True, False, hp, 500,
                          facing=(0.0, 0.0, -1.0), anim=3792, anim_time=t, anim_length=3.0, anims=anims)
        it = f.update(_snap([target(0.1, 500)]), 1 / 60)
        self.assertIn('attack', it.taps)                   # its wind-up: an interrupt
        for i in range(1, 50):                             # our hit lands, a reaction layer, the attack goes on
            t = 0.1 + i / 60
            f.update(_snap([target(t, 450 if t > 0.5 else 500, layer=t > 0.5)]), 1 / 60)
        self.assertIn(('c4300', 3792), f._armored)
        self.assertIn(3792, chains['_armored']['c4300'])
        f2 = Fighter(windows, lambda tid: 30.0, chains=chains)   # kept: never interrupted again
        f2.start(1)
        self.assertNotIn('attack', f2.update(_snap([target(0.1, 500)]), 1 / 60).taps)

    def test_a_soldiers_thrust_is_known_to_be_armoured(self):
        self.assertIn(('c4310', 4003008), Fighter(windows)._armored)

    def test_no_swing_with_another_soldier_beside_facing_the_player(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        beside = _enemy(2, (1.6, 0.0, 0.6), facing=(-0.94, 0.0, -0.35))
        self.assertNotIn('attack', f.update(_snap([_enemy(1, (0.0, 0.0, 2.0)), beside]), 1 / 60).taps)
        self.assertTrue(f.debug.get('lurking'))
        f2 = Fighter(windows, lambda tid: 30.0)           # facing away: it can't
        f2.start(1)
        away = _enemy(2, (1.6, 0.0, 0.6), facing=(0.94, 0.0, 0.35))
        self.assertIn('attack', f2.update(_snap([_enemy(1, (0.0, 0.0, 2.0)), away]), 1 / 60).taps)

    def test_turns_on_a_wolf_biting_while_the_soldier_stands_off(self):
        WINDOWS[('c4300', 3793)] = [(0.5, 0.6)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._target_since = f.t - 3.0                       # held long enough
        soldier = _enemy(1, (0.0, 0.0, 4.8))
        wolf = _enemy(2, (1.0, 0.0, -0.8), facing=(-0.78, 0.0, 0.62), anim=3793, t=0.1)
        f.update(_snap([soldier, wolf]), 1 / 60)
        self.assertEqual(f.target_id, 2)

    def test_gets_away_through_the_widest_gap(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        snap = _snap([_enemy(1, (0.0, 0.0, 2.0)), _enemy(2, (2.0, 0.0, 0.0)), _enemy(3, (-2.0, 0.0, 0.0))])
        away = f._away_yaw(snap, (0.0, 0.0, 0.0), snap.entities[0])
        self.assertLess(abs(abs(away) - 180.0), 25.0, away)     # north, east and west: out to the south


class TwentyThirdSessionTests(unittest.TestCase):
    """2026-10-12 16:51: four soldiers killed one by one, no hit taken all fight; then a pack of 6-8: four escapes
    in 36 s (from every 3 within 5 m), not one swing, and "surrounded" with full health. A boar asked for was
    left for a soldier coming over (the game's lock-on moved to it)."""

    def _three(self):
        return [_enemy(1, (0.0, 0.0, 2.0)), _enemy(2, (2.0, 0.0, 1.0)), _enemy(3, (-2.0, 0.0, 1.0))]

    def test_three_near_doesnt_send_it_running_unless_they_hit(self):
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f.update(_snap(self._three()), 1 / 60)
        self.assertNotEqual(f.state, 'escaping')
        f2 = Fighter(windows, lambda tid: 30.0)
        f2.start(1)
        f2._last_hurt = f2.t                                # a hit just landed
        f2.update(_snap(self._three()), 1 / 60)
        self.assertEqual(f2.state, 'escaping')

    def test_keeps_the_one_asked_for_while_another_only_comes_over(self):
        f = Fighter(windows, lambda tid: -1.0)
        f.start(1)
        f._locked = True
        f._target_since = f.t - 3.0
        boar = _enemy(1, (0.0, 0.0, 9.0))
        soldier = _enemy(2, (5.0, 0.0, 6.0), facing=(-0.64, 0.0, -0.77))   # the camera on it, 7 m, coming
        for _ in range(60):
            f.update(_snap([boar, soldier], forward=(0.64, 0.0, 0.77)), 1 / 60)
        self.assertEqual(f.target_id, 1)

    def test_back_to_the_one_asked_for_after_one_that_attacked(self):
        WINDOWS[('c4300', 3794)] = [(0.6, 0.7)]
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        f._target_since = f.t - 3.0
        boar = _enemy(1, (0.0, 0.0, 8.0))
        f.update(_snap([boar, _enemy(2, (0.0, 0.0, 1.6), anim=3794, t=0.1)]), 1 / 60)
        self.assertEqual(f.target_id, 2)                    # it attacks: that one first
        f.update(_snap([boar, _enemy(2, (0.0, 0.0, 1.6), dead=True)]), 1 / 60)
        self.assertEqual(f.target_id, 1)                    # down: back to the boar, not "won"
        self.assertTrue(f.active)

    def test_a_fight_ends_when_the_game_stops_moving(self):
        from game_assistant.core.combat import STALE_S
        f = Fighter(windows, lambda tid: 30.0)
        f.start(1)
        s = _snap([_enemy(1, (0.0, 0.0, 6.0))])
        for k in range(3):                                  # frames counting
            f.update(Snapshot(s.game, k, True, s.player, s.camera, s.entities, None, screen=s.screen), 1 / 60)
        for _ in range(int((STALE_S + 1) * 10)):             # then frozen
            f.update(Snapshot(s.game, 2, True, s.player, s.camera, s.entities, None, screen=s.screen), 0.1)
        self.assertFalse(f.active)
