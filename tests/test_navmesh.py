"""The navmesh planner (core/navmesh.py) on small hand-made meshes."""
import math
import unittest

import numpy as np

from game_assistant.core.navmesh import NavMesh


def grid_mesh(cells, y=0.0, size=2.0, levels=None):
    """Square faces of `size` metres at the given (i, j) cells (x = i * size, z = j * size), joined
    where they share a side. levels: optional {(i, j): height}."""
    verts, index, starts, fv = [], {}, [0], []

    def vid(x, z, h):
        key = (round(x, 3), round(z, 3), round(h, 3))
        if key not in index:
            index[key] = len(verts)
            verts.append((x, h, z))
        return index[key]
    order = list(cells)
    for (i, j) in order:
        h = (levels or {}).get((i, j), y)
        x0, z0 = i * size, j * size
        fv += [vid(x0, z0, h), vid(x0 + size, z0, h), vid(x0 + size, z0 + size, h), vid(x0, z0 + size, h)]
        starts.append(len(fv))
    la, lb, pa, pb = [], [], [], []
    pos = {c: k for k, c in enumerate(order)}
    for (i, j), k in pos.items():
        h = (levels or {}).get((i, j), y)
        for di, dj in ((1, 0), (0, 1)):
            n = pos.get((i + di, j + dj))
            if n is None or (levels or {}).get((i + di, j + dj), y) != h:
                continue
            if di:
                a, b = ((i + 1) * size, h, j * size), ((i + 1) * size, h, (j + 1) * size)
            else:
                a, b = (i * size, h, (j + 1) * size), ((i + 1) * size, h, (j + 1) * size)
            la.append(k), lb.append(n), pa.append(a), pb.append(b)
    return NavMesh(np.array(verts), np.array(starts), np.array(fv), np.array(la), np.array(lb),
                   np.array(pa, dtype=float), np.array(pb, dtype=float))


class NavMeshTests(unittest.TestCase):
    def test_l_corridor_turns_at_the_inside_corner(self):
        # Corridor east along z=0..2 for 10 cells, then north along x=18..20.
        cells = [(i, 0) for i in range(10)] + [(9, j) for j in range(1, 10)]
        m = grid_mesh(cells)
        r = m.route((1, 0, 1), (19, 0, 19))
        self.assertIsNotNone(r)
        self.assertEqual(len(r.waypoints), 3)          # start, one corner, goal
        cx, _, cz = r.waypoints[1]
        # The inside corner is (18, 2); the waypoint is 0.7 m off it, diagonally into the corridor.
        self.assertAlmostEqual(math.dist((cx, cz), (18, 2)), 0.7, places=3)
        self.assertGreater(cx, 18)
        self.assertLess(cz, 2)
        straight = math.dist((1, 0, 1), (19, 0, 19))
        self.assertGreater(r.length, straight)

    def test_straight_when_open(self):
        m = grid_mesh([(i, j) for i in range(10) for j in range(10)])
        r = m.route((1, 0, 1), (19, 0, 19))
        self.assertEqual(len(r.waypoints), 2)

    def test_gap_cannot_be_crossed(self):
        m = grid_mesh([(i, 0) for i in range(4)] + [(i, 0) for i in range(6, 10)])
        r = m.route((1, 0, 1), (19, 0, 1))
        self.assertFalse(r.reaches_goal)          # it ends at the edge of the gap instead
        self.assertAlmostEqual(r.waypoints[-1][0], 8.0, places=3)

    def test_picks_the_level_you_stand_on(self):
        # A ledge 10 m up over the same ground: standing below, the route stays below.
        cells = [(i, 0) for i in range(6)]
        lower = grid_mesh(cells)
        f, p, d = lower.locate((5, 0.3, 1))
        self.assertAlmostEqual(p[1], 0.0)
        two = grid_mesh(cells + [(i, 1) for i in range(6)], levels={(i, 1): 10.0 for i in range(6)})
        f, p, d = two.locate((5, 10.2, 3))
        self.assertAlmostEqual(p[1], 10.0)
        f, p, d = two.locate((5, 0.2, 1))
        self.assertAlmostEqual(p[1], 0.0)
        self.assertFalse(two.route((1, 0, 1), (11, 10, 3)).reaches_goal)  # no ramp between them

    def test_off_mesh_start_snaps_to_the_nearest_edge(self):
        m = grid_mesh([(i, 0) for i in range(5)])
        f, p, d = m.locate((5, 0, 4))  # 2 m beside the corridor
        self.assertAlmostEqual(p[2], 2.0)
        self.assertAlmostEqual(d, 2.0)
