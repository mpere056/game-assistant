"""Routes over a navigation mesh: the walkable ground as polygons (faces) joined at shared edges.

A game adapter that has the game's own navmesh builds a `NavMesh` from vertices, faces and links
(which faces touch, and the shared edge between them, the "portal"). Then:

1. `locate(p)`: the face under a point (inside it seen from above, nearest in height).
2. A* from face to face (cost: distance between face centres, climbing counts extra).
3. The funnel algorithm pulls the face corridor tight into a few straight legs, turning only at
   corners. Corners are pulled a little away from the wall (`corner_margin`) so a character or the
   fairy following the route doesn't scrape along it.

Unlike the raycast planner (nav.py) this knows exactly where the ground can be walked: cliffs,
water and walls are simply not part of the mesh, caves and bridges are, and it covers any distance.
Game-independent; y is up.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np

Vec3 = tuple[float, float, float]


@dataclass
class MeshRoute:
    waypoints: list[Vec3]      # start (on the mesh) ... goal (on the mesh)
    faces: list[int]           # the face corridor
    length: float              # metres along the waypoints
    start_off: float           # how far the start point was from the mesh (metres)
    goal_off: float
    reaches_goal: bool = True  # False: the goal can't be walked to (an evergaol's arena, behind a
                               # fog wall, a lift); the route ends at the nearest walkable point


class NavMesh:
    def __init__(self, vertices: np.ndarray, face_start: np.ndarray, face_vidx: np.ndarray,
                 link_a: np.ndarray, link_b: np.ndarray, portal_a: np.ndarray, portal_b: np.ndarray,
                 face_cost: np.ndarray | None = None):
        """vertices (N, 3); face f's polygon is face_vidx[face_start[f]:face_start[f + 1]];
        links: faces link_a[i] and link_b[i] share the edge portal_a[i]-portal_b[i] (both directions
        are added here). face_cost: a multiplier per face (1 = normal, inf = never)."""
        self.v = np.asarray(vertices, dtype=np.float64)
        self.fs = np.asarray(face_start, dtype=np.int64)
        self.fv = np.asarray(face_vidx, dtype=np.int64)
        n = len(self.fs) - 1
        self.n = n
        counts = np.diff(self.fs)
        owner = np.repeat(np.arange(n), counts)
        pts = self.v[self.fv]
        sums = np.zeros((n, 3))
        np.add.at(sums, owner, pts)
        self.centroid = sums / np.maximum(counts, 1)[:, None]
        mins = np.full((n, 3), np.inf)
        maxs = np.full((n, 3), -np.inf)
        np.minimum.at(mins, owner, pts)
        np.maximum.at(maxs, owner, pts)
        self.lo, self.hi = mins, maxs
        self.cost_mul = np.ones(n) if face_cost is None else np.asarray(face_cost, dtype=np.float64)
        # Adjacency (both directions) as Python lists: fastest for the A* loop.
        a = np.concatenate([link_a, link_b]).astype(np.int64)
        b = np.concatenate([link_b, link_a]).astype(np.int64)
        pa = np.concatenate([portal_a, portal_b])  # reversed direction: swap ends (orientation fixed later)
        pb = np.concatenate([portal_b, portal_a])
        order = np.argsort(a, kind='stable')
        self._nb_start = np.searchsorted(a[order], np.arange(n + 1)).tolist()
        self._nb = b[order].tolist()
        self._pa = pa[order]
        self._pb = pb[order]
        self._cent = self.centroid.tolist()

    # ---- where am I on the mesh ----

    def locate(self, p: Vec3, max_below: float = 4.0, max_above: float = 2.5, max_side: float = 6.0) -> tuple[int, Vec3, float] | None:
        """(face, point on it, distance) for p: a face whose outline contains p seen from above and
        whose surface is at most max_below under / max_above over p; else the nearest face edge
        within max_side metres sideways (standing just off the mesh, at a wall or on a rock)."""
        x, y, z = p
        cand = np.nonzero((self.lo[:, 0] <= x) & (x <= self.hi[:, 0]) & (self.lo[:, 2] <= z) & (z <= self.hi[:, 2])
                          & (self.lo[:, 1] <= y + max_below) & (self.hi[:, 1] >= y - max_above))[0]
        best = None
        for f in cand.tolist():
            h = self._height_in(f, x, z)
            if h is None or not (y - max_below <= h <= y + max_above):
                continue
            d = abs(h - y)
            if best is None or d < best[2]:
                best = (f, (x, h, z), d)
        if best is not None:
            return best
        near = np.nonzero((self.lo[:, 0] <= x + max_side) & (x - max_side <= self.hi[:, 0]) &
                          (self.lo[:, 2] <= z + max_side) & (z - max_side <= self.hi[:, 2]) &
                          (self.lo[:, 1] <= y + max_below) & (self.hi[:, 1] >= y - max_below))[0]
        for f in near.tolist():
            q = self._closest_on_face(f, p)
            d = math.dist(q, p)
            if d <= max_side + max_below and (best is None or d < best[2]):
                best = (f, q, d)
        return best

    def _poly(self, f: int) -> np.ndarray:
        return self.v[self.fv[self.fs[f]:self.fs[f + 1]]]

    def _height_in(self, f: int, x: float, z: float) -> float | None:
        """Surface height of face f at (x, z) if (x, z) is inside it (triangle fan)."""
        poly = self._poly(f)
        a = poly[0]
        for i in range(1, len(poly) - 1):
            b, c = poly[i], poly[i + 1]
            d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(d) < 1e-9:
                continue
            l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
            l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
            l3 = 1 - l1 - l2
            if l1 >= -1e-6 and l2 >= -1e-6 and l3 >= -1e-6:
                return l1 * a[1] + l2 * b[1] + l3 * c[1]
        return None

    def _closest_on_face(self, f: int, p: Vec3) -> Vec3:
        poly = self._poly(f)
        best, bd = None, math.inf
        P = np.asarray(p)
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            ab = b - a
            t = float(np.clip(np.dot(P - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1))
            q = a + t * ab
            d = float(np.dot(P - q, P - q))
            if d < bd:
                best, bd = q, d
        return (float(best[0]), float(best[1]), float(best[2]))

    # ---- search ----

    def route(self, start: Vec3, goal: Vec3, climb_cost: float = 1.5, max_expand: int = 400_000,
              corner_margin: float = 0.7) -> MeshRoute | None:
        s = self.locate(start)
        g = self.locate(goal, max_below=8.0, max_above=8.0, max_side=15.0)
        if s is None or g is None:
            return None
        faces = self._astar(s[0], g[0], g[1], climb_cost, max_expand)
        reached = True
        end = g[1]
        if faces is None:  # not walkable to the goal: go as close as walking allows
            reach = self.reachable(start)
            if not reach:
                return None
            cand = np.fromiter(reach, dtype=np.int64)
            dist = np.linalg.norm(self.centroid[cand] - np.asarray(g[1]), axis=1)
            f = int(cand[int(dist.argmin())])
            end = self._closest_on_face(f, g[1])
            faces = self._astar(s[0], f, end, climb_cost, max_expand)
            if faces is None:
                return None
            reached = False
        pts = self._funnel(faces, s[1], end, corner_margin)
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        return MeshRoute(pts, faces, length, s[2], g[2], reached)

    def reachable(self, start: Vec3, limit: int = 2_000_000) -> set[int]:
        s = self.locate(start)
        if s is None:
            return set()
        seen, stack = {s[0]}, [s[0]]
        nb, ns = self._nb, self._nb_start
        while stack and len(seen) < limit:
            f = stack.pop()
            for k in range(ns[f], ns[f + 1]):
                h = nb[k]
                if h not in seen and self.cost_mul[h] != math.inf:
                    seen.add(h)
                    stack.append(h)
        return seen

    def _astar(self, s: int, g: int, goal_pt: Vec3, climb_cost: float, max_expand: int) -> list[int] | None:
        cent, nb, ns, mul = self._cent, self._nb, self._nb_start, self.cost_mul
        gx, gy, gz = goal_pt

        def h(f):
            c = cent[f]
            return math.sqrt((c[0] - gx) ** 2 + (c[1] - gy) ** 2 + (c[2] - gz) ** 2)
        openq = [(h(s), 0.0, s)]
        came = {s: -1}
        cost = {s: 0.0}
        expanded = 0
        while openq:
            _, c, f = heapq.heappop(openq)
            if f == g:
                path = [f]
                while came[path[-1]] != -1:
                    path.append(came[path[-1]])
                return path[::-1]
            if c > cost[f]:
                continue
            expanded += 1
            if expanded > max_expand:
                return None
            cf = cent[f]
            for k in range(ns[f], ns[f + 1]):
                n = nb[k]
                m = mul[n]
                if m == math.inf:
                    continue
                cn = cent[n]
                dx, dy, dz = cn[0] - cf[0], cn[1] - cf[1], cn[2] - cf[2]
                step = math.sqrt(dx * dx + dy * dy + dz * dz) + (climb_cost * dy if dy > 0 else 0.0)
                nc = c + step * m
                if nc < cost.get(n, math.inf):
                    cost[n] = nc
                    came[n] = f
                    heapq.heappush(openq, (nc + h(n), nc, n))
        return None

    def _portal(self, f: int, n: int) -> tuple[np.ndarray, np.ndarray]:
        for k in range(self._nb_start[f], self._nb_start[f + 1]):
            if self._nb[k] == n:
                return self._pa[k], self._pb[k]
        raise KeyError((f, n))

    def _funnel(self, faces: list[int], start: Vec3, goal: Vec3, margin: float) -> list[Vec3]:
        """Simple stupid funnel algorithm (Mikko Mononen) on the x-z plane, heights carried along."""
        portals = [(np.asarray(start), np.asarray(start))]
        for f, n in zip(faces, faces[1:]):
            a, b = self._portal(f, n)
            # Left/right as seen walking from face f into face n.
            c0, c1 = self.centroid[f], self.centroid[n]
            d = c1 - c0
            mid = (a + b) / 2
            if d[0] * (a[2] - mid[2]) - d[2] * (a[0] - mid[0]) > 0:  # Detour's sides on x-z
                left, right = a, b
            else:
                left, right = b, a
            portals.append((left, right))
        portals.append((np.asarray(goal), np.asarray(goal)))

        def tri(a, b, c):  # Detour's triarea2 on x-z
            return (c[0] - a[0]) * (b[2] - a[2]) - (b[0] - a[0]) * (c[2] - a[2])

        def same(a, b):
            return (a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2 < 1e-8

        caps = [0.0]  # per point: how far it may move off its wall (under half its portal's width)

        def corner(p, other):
            caps.append(min(margin, 0.45 * math.dist((p[0], p[2]), (other[0], other[2]))))
            return tuple(map(float, p))

        pts = [tuple(map(float, start))]
        apex, left, right = portals[0][0], portals[0][0], portals[0][1]
        ai = li = ri = 0
        i = 1
        while i < len(portals):
            pl, pr = portals[i]
            # Tighten the right side.
            if tri(apex, right, pr) <= 0:
                if same(apex, right) or tri(apex, left, pr) > 0:
                    right, ri = pr, i
                else:  # right crossed left: left becomes a corner
                    pts.append(corner(left, portals[li][1]))
                    apex, ai = left, li
                    left, right, li, ri = apex, apex, ai, ai
                    i = ai + 1
                    continue
            # Tighten the left side.
            if tri(apex, left, pl) >= 0:
                if same(apex, left) or tri(apex, right, pl) < 0:
                    left, li = pl, i
                else:
                    pts.append(corner(right, portals[ri][0]))
                    apex, ai = right, ri
                    left, right, li, ri = apex, apex, ai, ai
                    i = ai + 1
                    continue
            i += 1
        g = tuple(map(float, goal))
        if math.dist(pts[-1], g) > 1e-3:
            pts.append(g)
        # Corners move off the wall along the bisector of the turn (away from its inside).
        out = list(pts)
        for i in range(1, len(pts) - 1):
            a, p, b = pts[i - 1], pts[i], pts[i + 1]
            u = (p[0] - a[0], p[2] - a[2])
            v = (b[0] - p[0], b[2] - p[2])
            lu, lv = math.hypot(*u), math.hypot(*v)
            if lu < 1e-6 or lv < 1e-6:
                continue
            push = (u[0] / lu - v[0] / lv, u[1] / lu - v[1] / lv)
            lp = math.hypot(*push)
            if lp < 1e-6:
                continue
            k = caps[i] / lp
            out[i] = (p[0] + push[0] * k, p[1], p[2] + push[1] * k)
        kept = [out[0]]
        for q in out[1:-1]:
            if math.dist(q, kept[-1]) > 0.3:  # corners of neighbouring portals can coincide
                kept.append(q)
        kept.append(out[-1])
        return kept
