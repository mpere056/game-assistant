"""Routes over a navigation mesh: the walkable ground as polygons (faces) joined at shared edges.

The search works on any `Source` of faces, so a game can hand over a whole world that is loaded
piece by piece while the search runs (like Minecraft's Baritone with its chunk cache). `NavMesh` is
the simple in-memory source (used by the tests and for small areas).

1. `locate(p)`: the face under a point (inside it seen from above, nearest in height).
2. A* from face to face (cost: distance between face centres, climbing counts extra), with a time
   budget: when it runs out, the best partial route toward the goal is returned (`timed_out`), and the
   caller can set off along it and plan the rest from its end while moving. When the search runs out
   of faces instead, the goal can't be walked to (an evergaol arena, behind a fog wall, up a lift):
   the route ends at the nearest point that can be (`reaches_goal` False, `timed_out` False).
3. The funnel algorithm pulls the face corridor tight into a few straight legs; corners are pulled a
   little away from the wall so whoever follows doesn't scrape along it.

Unlike the raycast planner (nav.py) this knows exactly where the ground can be walked: cliffs,
water and walls are simply not part of the mesh, caves and bridges are, and it covers any distance.
Game-independent; y is up.
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

Vec3 = tuple[float, float, float]
# Extra cost (metres) for special links, so the search only uses them when they help:
# 1 a dungeon entrance gap, 2 a drop down a ledge, 3 a step across a gap in the mesh,
# 4 a ladder, 5 a lift (waiting for it), 6 a jump, 7 a door.
LINK_PENALTY = {1: 2.0, 2: 4.0, 3: 8.0, 4: 5.0, 5: 20.0, 6: 3.0, 7: 1.0}
LINK_NAMES = {1: 'entrance', 2: 'drop', 3: 'step', 4: 'ladder', 5: 'lift', 6: 'jump', 7: 'door'}


@dataclass
class MeshRoute:
    waypoints: list[Vec3]      # start (on the mesh) ... end
    faces: list[int]           # the face corridor
    length: float              # metres along the waypoints
    start_off: float           # how far the start point was from the mesh (metres)
    goal_off: float
    reaches_goal: bool = True  # False: it ends short of the goal (see timed_out)
    timed_out: bool = False    # True: the time budget ran out: plan on from the end (else: no walking way)
    expanded: int = 0          # faces the search looked at
    seconds: float = 0.0
    special: list = None       # special links on the route: (kind, edge a, edge b, from, to, landing centre)


class Source(Protocol):
    """What the search needs from a mesh. Faces are any hashable ids (ints here)."""

    def locate(self, p: Vec3, max_below: float = 4.0, max_above: float = 2.5,
               max_side: float = 6.0) -> tuple[int, Vec3, float] | None: ...

    def neighbours(self, f: int) -> list[tuple]:
        """(face, portal end a, portal end b[, kind]) for each face reachable from f: sharing an edge,
        or a special link (kind > 0, e.g. a drop down a ledge; the caller may check those first)."""

    def centre(self, f: int) -> tuple[float, float, float]: ...

    def cost(self, f: int) -> float:
        """Cost multiplier for walking on f (1 normal, inf never)."""

    def closest_on(self, f: int, p: Vec3) -> Vec3: ...


# ---- geometry helpers ----

def height_in(poly: np.ndarray, x: float, z: float) -> float | None:
    """Surface height of a polygon at (x, z) if (x, z) is inside it (triangle fan)."""
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
            return float(l1 * a[1] + l2 * b[1] + l3 * c[1])
    return None


def closest_on_poly(poly: np.ndarray, p: Vec3) -> Vec3:
    best, bd = None, math.inf
    P = np.asarray(p, dtype=np.float64)
    for i in range(len(poly)):
        a, b = poly[i].astype(np.float64), poly[(i + 1) % len(poly)].astype(np.float64)
        ab = b - a
        t = float(np.clip(np.dot(P - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1))
        q = a + t * ab
        d = float(np.dot(P - q, P - q))
        if d < bd:
            best, bd = q, d
    return (float(best[0]), float(best[1]), float(best[2]))


def locate_in(polys_lo: np.ndarray, polys_hi: np.ndarray, poly_of, p: Vec3, max_below: float, max_above: float,
              max_side: float) -> tuple[int, Vec3, float] | None:
    """Shared by sources: lo/hi are per-face bounding boxes (F, 3); poly_of(i) gives face i's polygon."""
    x, y, z = p
    cand = np.nonzero((polys_lo[:, 0] <= x) & (x <= polys_hi[:, 0]) & (polys_lo[:, 2] <= z) & (z <= polys_hi[:, 2])
                      & (polys_lo[:, 1] <= y + max_below) & (polys_hi[:, 1] >= y - max_above))[0]
    best = None
    for f in cand.tolist():
        h = height_in(poly_of(f), x, z)
        if h is None or not (y - max_below <= h <= y + max_above):
            continue
        d = abs(h - y)
        if best is None or d < best[2]:
            best = (f, (x, h, z), d)
    if best is not None:
        return best
    near = np.nonzero((polys_lo[:, 0] <= x + max_side) & (x - max_side <= polys_hi[:, 0]) &
                      (polys_lo[:, 2] <= z + max_side) & (z - max_side <= polys_hi[:, 2]) &
                      (polys_lo[:, 1] <= y + max_below) & (polys_hi[:, 1] >= y - max_below))[0]
    for f in near.tolist():
        q = closest_on_poly(poly_of(f), p)
        d = math.dist(q, p)
        if d <= max_side + max_below and (best is None or d < best[2]):
            best = (f, q, d)
    return best


# ---- the search ----

def search(src: Source, start: Vec3, goal: Vec3, budget_s: float = 1.0, weight: float = 1.0,
           climb_cost: float = 1.5, corner_margin: float = 0.7, max_expand: int = 2_000_000,
           start_face: tuple | None = None, goal_face: tuple | None = None) -> MeshRoute | None:
    """A route from start toward goal over src. weight > 1 makes A* greedier (faster, routes a little
    longer than the best). start_face/goal_face: (face, point, distance) from an earlier locate()."""
    t0 = time.perf_counter()
    s = start_face or src.locate(start)
    if s is None:
        return None
    g = goal_face or src.locate(goal, max_below=8.0, max_above=8.0, max_side=15.0)
    goal_pt = g[1] if g else goal
    gx, gy, gz = goal_pt
    target = g[0] if g else None

    def h(c):
        return math.sqrt((c[0] - gx) ** 2 + (c[1] - gy) ** 2 + (c[2] - gz) ** 2)

    centre, neighbours, fcost = src.centre, src.neighbours, src.cost
    c0 = centre(s[0])
    h0 = h(c0)
    openq = [(weight * h0, 0.0, s[0])]
    came: dict = {s[0]: None}       # face -> (previous face, portal a, portal b)
    cost = {s[0]: 0.0}
    best_f, best_h = s[0], h0
    expanded = 0
    timed_out = False
    found = False
    while openq:
        _, c, f = heapq.heappop(openq)
        if f == target:
            found = True
            best_f = f
            break
        if c > cost[f]:
            continue
        expanded += 1
        if expanded > max_expand or (expanded & 255 == 0 and time.perf_counter() - t0 > budget_s):
            timed_out = True
            break
        cf = centre(f)
        for nb in neighbours(f):
            n, pa, pb = nb[0], nb[1], nb[2]
            m = fcost(n)
            if m == math.inf:
                continue
            cn = centre(n)
            dx, dy, dz = cn[0] - cf[0], cn[1] - cf[1], cn[2] - cf[2]
            step = math.sqrt(dx * dx + dy * dy + dz * dz) + (climb_cost * dy if dy > 0 else 0.0)
            if len(nb) > 3 and nb[3]:
                step += LINK_PENALTY.get(nb[3], 0.0)
            nc = c + step * m
            if nc < cost.get(n, math.inf):
                cost[n] = nc
                came[n] = (f, pa, pb, nb[3] if len(nb) > 3 else 0)
                hn = h(cn)
                if hn < best_h:
                    best_f, best_h = n, hn
                heapq.heappush(openq, (nc + weight * hn, nc, n))
    faces, portals, special = [best_f], [], []
    while came[faces[-1]] is not None:
        prev, pa, pb, kind = came[faces[-1]]
        portals.append((pa, pb, prev, faces[-1]))
        if kind:
            special.append((kind, pa, pb, prev, faces[-1], centre(faces[-1])))
        faces.append(prev)
    special.reverse()
    faces.reverse()
    portals.reverse()
    end = goal_pt if found else src.closest_on(best_f, goal_pt)
    pts = funnel([(np.asarray(pa, dtype=np.float64), np.asarray(pb, dtype=np.float64),
                   np.asarray(centre(f), dtype=np.float64), np.asarray(centre(n), dtype=np.float64))
                  for pa, pb, f, n in portals], s[1], end, corner_margin)
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    return MeshRoute(pts, faces, length, s[2], g[2] if g else math.inf, found, timed_out and not found,
                     expanded, time.perf_counter() - t0, special)


def funnel(portals: list[tuple], start: Vec3, goal: Vec3, margin: float) -> list[Vec3]:
    """Simple stupid funnel algorithm (Mikko Mononen) on the x-z plane, heights carried along.
    portals: (end a, end b, centre of the face before, centre of the face after)."""
    seq = [(np.asarray(start, dtype=np.float64), np.asarray(start, dtype=np.float64))]
    for a, b, c0, c1 in portals:
        d = c1 - c0
        mid = (a + b) / 2
        if d[0] * (a[2] - mid[2]) - d[2] * (a[0] - mid[0]) > 0:  # Detour's sides on x-z
            seq.append((a, b))
        else:
            seq.append((b, a))
    seq.append((np.asarray(goal, dtype=np.float64), np.asarray(goal, dtype=np.float64)))

    def tri(a, b, c):  # Detour's triarea2 on x-z
        return (c[0] - a[0]) * (b[2] - a[2]) - (b[0] - a[0]) * (c[2] - a[2])

    def same(a, b):
        return (a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2 < 1e-8

    caps = [0.0]  # per point: how far it may move off its wall (under half its portal's width)

    def corner(p, other):
        caps.append(min(margin, 0.45 * math.dist((p[0], p[2]), (other[0], other[2]))))
        return tuple(map(float, p))

    pts = [tuple(map(float, start))]
    apex, left, right = seq[0][0], seq[0][0], seq[0][1]
    ai = li = ri = 0
    i = 1
    while i < len(seq):
        pl, pr = seq[i]
        if tri(apex, right, pr) <= 0:  # tighten the right side
            if same(apex, right) or tri(apex, left, pr) > 0:
                right, ri = pr, i
            else:  # right crossed left: left becomes a corner
                pts.append(corner(left, seq[li][1]))
                apex, ai = left, li
                left, right, li, ri = apex, apex, ai, ai
                i = ai + 1
                continue
        if tri(apex, left, pl) >= 0:  # tighten the left side
            if same(apex, left) or tri(apex, right, pl) < 0:
                left, li = pl, i
            else:
                pts.append(corner(right, seq[ri][0]))
                apex, ai = right, ri
                left, right, li, ri = apex, apex, ai, ai
                i = ai + 1
                continue
        i += 1
    g = tuple(map(float, goal))
    if math.dist(pts[-1], g) > 1e-3:
        pts.append(g)
        caps.append(0.0)
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


# ---- a whole mesh in memory ----

class NavMesh:
    """A `Source` holding one mesh in memory."""

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
        a = np.concatenate([link_a, link_b]).astype(np.int64)
        b = np.concatenate([link_b, link_a]).astype(np.int64)
        pa = np.concatenate([portal_a, portal_b])
        pb = np.concatenate([portal_b, portal_a])
        order = np.argsort(a, kind='stable')
        ns = np.searchsorted(a[order], np.arange(n + 1)).tolist()
        nb = b[order].tolist()
        pal, pbl = [tuple(r) for r in pa[order].tolist()], [tuple(r) for r in pb[order].tolist()]
        self._nbrs = [[(nb[k], pal[k], pbl[k]) for k in range(ns[f], ns[f + 1])] for f in range(n)]
        self._cent = [tuple(c) for c in self.centroid.tolist()]
        self._cost = self.cost_mul.tolist()

    # Source
    def locate(self, p, max_below=4.0, max_above=2.5, max_side=6.0):
        return locate_in(self.lo, self.hi, self._poly, p, max_below, max_above, max_side)

    def neighbours(self, f):
        return self._nbrs[f]

    def centre(self, f):
        return self._cent[f]

    def cost(self, f):
        return self._cost[f]

    def closest_on(self, f, p):
        return closest_on_poly(self._poly(f), p)

    def _poly(self, f: int) -> np.ndarray:
        return self.v[self.fv[self.fs[f]:self.fs[f + 1]]]

    def _height_in(self, f, x, z):
        return height_in(self._poly(f), x, z)

    def _closest_on_face(self, f, p):
        return closest_on_poly(self._poly(f), p)

    def route(self, start: Vec3, goal: Vec3, climb_cost: float = 1.5, max_expand: int = 400_000,
              corner_margin: float = 0.7, budget_s: float = 10.0) -> MeshRoute | None:
        return search(self, start, goal, budget_s=budget_s, climb_cost=climb_cost, corner_margin=corner_margin,
                      max_expand=max_expand)

    def reachable(self, start: Vec3, limit: int = 2_000_000) -> set[int]:
        s = self.locate(start)
        if s is None:
            return set()
        seen, stack = {s[0]}, [s[0]]
        while stack and len(seen) < limit:
            f = stack.pop()
            for h, _a, _b in self._nbrs[f]:
                if h not in seen and self._cost[h] != math.inf:
                    seen.add(h)
                    stack.append(h)
        return seen
