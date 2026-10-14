import math
import unittest

from game_assistant.core import terrain as T


def land(fn, r=60):
    """A height map round (0, 0) from fn(x, z)."""
    xs = range(-r, r + 1)
    return T.HeightMap(-r, -r, 2 * r + 1, 2 * r + 1, [fn(x, z) for z in xs for x in xs])


class TerrainTest(unittest.TestCase):
    def test_flat_land_has_no_hills(self):
        m = land(lambda x, z: 64 + (x * 7 + z * 3) % 2)            # (a block of roughness)
        self.assertEqual(T.hills(m, (0, 64, 0)), [])

    def test_a_ridge_is_found_with_a_line_along_its_top(self):
        # a ridge 20 high running north-south 30 blocks east of the player, on land at 64
        m = land(lambda x, z: 64 + round(20 * math.exp(-((x - 30) / 8) ** 2) * (1 if abs(z) < 40 else 0.3)))
        got = T.hills(m, (0, 64, 0))
        self.assertTrue(got)
        self.assertEqual(len(got), 1, got)                              # (one ridge: one hill)
        h = got[0]
        self.assertIn(h['compass'], ('east', 'north-east', 'south-east'))
        self.assertGreaterEqual(h['rises_above_the_land_round_it'], 15)
        self.assertEqual(h['top_above_your_ground'], h['top']['y'] - 64)
        pts = h['along_the_top']['points']
        self.assertGreaterEqual(h['along_the_top']['length_blocks'], 50)
        self.assertTrue(all(abs(p['x'] - 30) <= 3 for p in pts), pts)        # on the top all along
        zs = [p['z'] for p in pts]
        self.assertEqual(zs, sorted(zs) if zs[0] < zs[-1] else sorted(zs, reverse=True))  # end to end
        self.assertLessEqual(h['nearest_point_on_top']['distance'], 32)

    def test_the_nearer_hill_first(self):
        def fn(x, z):
            near = 12 * math.exp(-((x + 25) ** 2 + z ** 2) / 60)
            far = 30 * math.exp(-((x - 45) ** 2 + (z - 45) ** 2) / 80)
            return 70 + round(near + far)
        got = T.hills(land(fn), (0, 70, 0))
        self.assertGreaterEqual(len(got), 2)
        self.assertEqual(got[0]['compass'], 'west')
        self.assertEqual(got[1]['compass'], 'south-east')

    def test_highest_each_way(self):
        m = land(lambda x, z: 64 + max(0, x) // 4)                  # rising to the east
        ways = {w['direction']: w for w in T.each_way(m, (0, 64, 0))}
        self.assertGreater(ways['east']['above_your_ground'], 10)
        self.assertLessEqual(ways['west']['above_your_ground'], 0)

    def test_the_rise_stood_on_with_a_loop_round_its_foot(self):
        # ("walk around 50 meters around this hill": the rise the player stood on wasn't among the hills)
        m = land(lambda x, z: 64 + round(12 * math.exp(-(x * x + z * z) / 300)))
        h = T.here(m, (3, 76, 2))
        self.assertTrue(h.get('on_a_rise'))
        self.assertGreaterEqual(h['rises_above_the_land_round_it'], 4)
        loop = h['round_its_foot']
        self.assertGreater(loop['length_blocks'], 40)
        self.assertTrue(all(66 <= p['y'] <= 72 for p in loop['points']), loop)    # down near its foot
        self.assertEqual(T.here(land(lambda x, z: 64), (0, 64, 0)), {'rises_above_the_land_round_it': 0})

    def test_unknown_columns_are_passed_over(self):
        m = land(lambda x, z: None if (x + z) % 5 == 0 else 64)
        self.assertEqual(T.ground_at(m, 0, 0), 64)
        self.assertEqual(T.hills(m, (0, 64, 0)), [])


if __name__ == '__main__':
    unittest.main()
