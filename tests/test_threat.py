"""What's dangerous round the player, weighed as one (core/threat.py), and the fighter out of the arrows."""
import math
import unittest

from game_assistant.core.interface import Camera, Entity, Player, Snapshot
from game_assistant.core.threat import decide, threats


def mob(i, kind, x, z, after=True, bow=False):
    return Entity(i, 1, f'minecraft:{kind}', (x, 0.0, z), (x, 1.0, z), 0.3, 1.95, True, False, 20.0, 20.0, name=kind,
                  extra={'aggressive': after, 'using_item': bow})


def snap(mobs, health=20.0):
    cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
    return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive', health, 20.0), cam, tuple(mobs), None)


def profile(e):
    kind = (e.model or '').split(':')[-1]
    return {'ranged': kind == 'skeleton', 'explodes': kind == 'creeper',
            'neutral': kind == 'enderman' and not (e.extra or {}).get('aggressive')}


def weigh(mobs, health=20.0, goal=None, armed=True, sees=True, winnable=True, perch=False):
    s = snap(mobs, health)
    ts = threats(s, profile)
    for t in ts:
        if t.kind == 'ranged':
            t.sees = sees
    return decide(s, ts, goal, armed=armed, winnable=lambda near=None: winnable, perch=perch)


class DecideTests(unittest.TestCase):
    def test_up_close_with_one_shooting_into_cover(self):
        # (a zombie fought in the open, bare-handed, while a skeleton 8 blocks off shot the player dead, 2026-10-16)
        d = weigh([mob(1, 'zombie', 0.0, 3.0), mob(2, 'skeleton', 6.0, 6.0, bow=True)])
        self.assertEqual((d.what, d.target.id), ('cover', 2))
        self.assertEqual(d.why, 'others up close')

    def test_one_shooting_alone_near_is_fought_far_off_hidden_from(self):
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 7.0, bow=True)]).what, 'fight')
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 14.0, bow=True)]).what, 'cover')
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 7.0, bow=True)], health=8.0).what, 'cover')     # (hurt)
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 7.0, bow=True)], armed=False).what, 'cover')
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 7.0, bow=True)], sees=False).what, 'none')     # (no sight)

    def test_ones_not_after_the_player_are_left_be(self):
        # (two skeletons not after the player held a walk 10 s for nothing, 2026-10-16)
        self.assertEqual(weigh([mob(2, 'skeleton', 0.0, 7.0, after=False), mob(3, 'skeleton', 3.0, 9.0, after=False)])
                         .what, 'none')
        self.assertEqual(weigh([mob(4, 'enderman', 0.0, 3.0, after=False)]).what, 'none')

    def test_up_close_fought_and_where_its_going_held_for(self):
        self.assertEqual(weigh([mob(1, 'zombie', 0.0, 4.0)]).what, 'fight')
        d = weigh([mob(1, 'zombie', 0.0, 20.0, after=False)], goal=(0.0, 0.0, 18.0))
        self.assertEqual((d.what, d.target.id), ('hold', 1))
        self.assertEqual(weigh([mob(1, 'zombie', 0.0, 20.0, after=False)], goal=(0.0, 0.0, -18.0)).what, 'none')


class AwayTests(unittest.TestCase):
    def test_one_coming_thats_no_fight_to_win_is_got_away_from_early(self):
        # (a zombie unseen till 6 blocks off, the player at half health and bare-handed: dead in a corner, 2026-10-16)
        d = weigh([mob(1, 'zombie', 0.0, 12.0)], health=10.0, armed=False, winnable=False)
        self.assertEqual((d.what, d.target.id), ('away', 1))
        self.assertEqual(weigh([mob(1, 'zombie', 0.0, 12.0)]).what, 'none')        # (a fight to win: let it come)
        self.assertEqual(weigh([mob(1, 'zombie', 0.0, 12.0)], winnable=False, perch=True).what, 'none')  # (up a pillar)
        self.assertEqual(weigh([mob(1, 'zombie', 0.0, 12.0, after=False)], winnable=False).what, 'none')


class ExposedTests(unittest.TestCase):
    def test_a_zombie_fought_out_of_a_skeletons_sight(self):
        # a skeleton at x = 9 shooting; anywhere with x < -1 is out of its sight (a block between); a zombie comes
        # from the north: fought from where the skeleton can't see the player, given its sight lines
        import sys
        sys.path.insert(0, 'tests')
        try:
            from test_melee import DT, Arena
        except ModuleNotFoundError:
            from tests.test_melee import DT, Arena
        from game_assistant.core.melee import MeleeFighter

        class WithSkeleton(Arena):
            def snap(self):
                s = super().snap()
                sk = Entity(30, 1, 'minecraft:skeleton', (9.0, 0.0, 0.0), (9.0, 1.0, 0.0), 0.3, 1.99, True, False,
                            20.0, 20.0, name='skeleton', extra={'aggressive': True, 'using_item': True})
                return s.__class__(**{**s.__dict__, 'entities': s.entities + (sk,)})

        def run(sight):
            a = WithSkeleton(at=(0.0, 0.0, 5.0))
            f = MeleeFighter(lambda e: {'reach': 0.83, 'ranged': 'skeleton' in (e.model or '')}, lambda h: 1,
                             weapon_damage=lambda h: 5.0, sight=sight)
            f.start(7, 'zombie')
            hidden = frames = 0
            for _ in range(int(8 / DT)):
                a.step(f.update(a.snap(), DT))
                frames += 1
                hidden += a.p[0] < -1.0
                if not f.active:
                    break
            return hidden / frames
        sight = (lambda pairs: [pt[0] >= -1.0 for _eye, pt in pairs])
        self.assertGreater(run(sight), run(None) + 0.3)
