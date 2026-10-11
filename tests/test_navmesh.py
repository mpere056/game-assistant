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


class _WithDrop:
    """A grid_mesh source plus one-way special links {(from face, to face): (portal a, portal b)}."""

    def __init__(self, mesh, links):
        self.m, self.links = mesh, links

    def locate(self, p, **kw):
        return self.m.locate(p, **kw)

    def neighbours(self, f):
        extra = [(t, a, b, 2) for (fr, t), (a, b) in self.links.items() if fr == f]
        return self.m.neighbours(f) + extra

    def centre(self, f):
        return self.m.centre(f)

    def cost(self, f):
        return self.m.cost(f)

    def closest_on(self, f, p):
        return self.m.closest_on(f, p)


class SearchTests(unittest.TestCase):
    def test_time_budget_gives_a_partial_route_toward_the_goal(self):
        from game_assistant.core.navmesh import search
        m = grid_mesh([(i, j) for i in range(150) for j in range(150)], size=2.0)
        r = search(m, (1, 0, 1), (299, 0, 299), budget_s=0.0)  # stops at the first time check
        self.assertTrue(r.timed_out)
        self.assertFalse(r.reaches_goal)
        self.assertGreater(r.length, 5)  # it still made progress
        full = search(m, (1, 0, 1), (299, 0, 299), budget_s=30)
        self.assertTrue(full.reaches_goal)
        self.assertFalse(full.timed_out)

    def test_drop_is_one_way(self):
        from game_assistant.core.navmesh import search
        # A ledge (y = 4) and the ground (y = 0), not joined; a drop from ledge cell 4 to ground cell 4.
        cells = [(i, 0) for i in range(5)] + [(i, 2) for i in range(5)]
        m = grid_mesh(cells, levels={(i, 0): 4.0 for i in range(5)})
        ledge = m.locate((9, 4, 1))[0]
        ground = m.locate((9, 0, 5))[0]
        src = _WithDrop(m, {(ledge, ground): ((8, 4, 2), (10, 4, 2))})
        down = search(src, (1, 4, 1), (1, 0, 5))
        self.assertTrue(down.reaches_goal)
        self.assertEqual(len(down.special), 1)
        up = search(src, (1, 0, 5), (1, 4, 1))
        self.assertFalse(up.reaches_goal)  # no way back up


class LearnedLinkTests(unittest.TestCase):
    """WorldGraph.observe on a small hand-made world: two corridors 3 m apart, not connected."""

    def _graph(self):
        import tempfile
        from pathlib import Path
        from game_assistant.games.eldenring import navgraph
        m = grid_mesh([(i, 0) for i in range(5)] + [(i, 2) for i in range(5)])
        g = navgraph.WorldGraph.__new__(navgraph.WorldGraph)
        g.names, g.area, g.banned, g.learned, g._prev, g.learned_count = ['m60_00_00_00'], 60, set(), {}, None, 0
        g.locate = lambda p, **kw: m.locate(p, **kw)
        g.block = None
        g.neighbours = lambda f: m.neighbours(f) + g.learned.get(f, [])
        self.tmp = tempfile.TemporaryDirectory()
        navgraph.LEARNED_FILE = Path(self.tmp.name) / 'learned.json'
        return g, m

    def tearDown(self):
        from game_assistant.games.eldenring import navgraph
        navgraph.LEARNED_FILE = navgraph.GRAPH_DIR / 'learned.json'
        self.tmp.cleanup()

    def test_learns_a_crossing_the_mesh_lacks_once(self):
        g, m = self._graph()
        self.assertFalse(g.observe((5, 0, 1), 60, 0.0))
        self.assertFalse(g.observe((7, 0, 1), 60, 0.25))   # along the corridor: connected
        self.assertTrue(g.observe((7, 0, 5), 60, 0.5))     # across the gap: learned
        self.assertTrue(g.observe((7, 0, 1), 60, 0.75))    # back again: the other direction is learned too
        self.assertEqual(g.learned_count, 2)
        g.observe((7, 0, 1), 60, 5.0)
        self.assertFalse(g.observe((7, 0, 5), 60, 5.25))   # already known
        self.assertEqual(g.learned_count, 2)

    def test_teleports_and_menus_are_ignored(self):
        g, m = self._graph()
        g.observe((1, 0, 1), 60, 0.0)
        self.assertFalse(g.observe((1, 0, 5), 60, 30.0))  # 30 s later (a menu, a grace): not a move
        g.observe(None)
        self.assertFalse(g.observe((9, 0, 5), 60, 30.25))
        self.assertEqual(g.learned_count, 0)
