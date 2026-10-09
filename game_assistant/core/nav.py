"""Navigation: a walkable route from the player to a goal, from the game's own collision (phase 4).

1. Grid: one downward ray per cell (2 m cells, 30 m around the player by default) gives the floor
   height and slope of each cell. One ray batch: 961 rays.
2. A*: 8 neighbours; a step is allowed when both cells are walkable floor and the height change is
   climbable. Cost = distance + extra for climbing.
3. Walls, lazily: only the route A* found is checked, with two sideways rays per step (knee and
   chest height). A blocked step is removed and A* runs again. Most routes need one or two rounds,
   so walls cost tens of rays instead of thousands.
4. The route is thinned to waypoints where it turns.

Goals beyond the grid get a route to the grid cell closest to them; the caller re-plans from there.
Game-independent: it only needs raycast(). Jumps, ladders, doors and moving platforms are not
handled (yet): such routes are simply not found.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable

from .interface import RayHit, Vec3

Raycast = Callable[[list[tuple[Vec3, Vec3]]], list[RayHit]]

CELL = 2.0          # metres
RADIUS = 30.0       # grid half-size, metres
PROBE_UP = 25.0     # rays start this far above the player's feet...
PROBE_DOWN = 40.0   # ...and end this far below
MAX_SLOPE_NY = 0.65 # floor normal's Y must be at least this (about 50 degrees)
MAX_STEP = 1.1      # metres of height change allowed between neighbouring cells
KNEE, CHEST = 0.5, 1.4
MAX_ROUNDS = 12


@dataclass
class NavGrid:
    origin: Vec3                 # player's feet when built
    n: int                       # cells per side
    floor: list[list[float | None]] = field(default_factory=list)  # [ix][iz] floor height or None

    def center(self, ix: int, iz: int) -> Vec3:
        h = self.floor[ix][iz]
        return (self.origin[0] + (ix - self.n // 2) * CELL, h if h is not None else self.origin[1],
                self.origin[2] + (iz - self.n // 2) * CELL)

    def cell_of(self, p: Vec3) -> tuple[int, int]:
        return (round((p[0] - self.origin[0]) / CELL) + self.n // 2, round((p[2] - self.origin[2]) / CELL) + self.n // 2)

    def inside(self, ix: int, iz: int) -> bool:
        return 0 <= ix < self.n and 0 <= iz < self.n


def build_grid(raycast: Raycast, origin: Vec3, radius: float = RADIUS) -> NavGrid:
    n = int(radius / CELL) * 2 + 1
    g = NavGrid(origin, n, [[None] * n for _ in range(n)])
    rays, cells = [], []
    for ix in range(n):
        for iz in range(n):
            x = origin[0] + (ix - n // 2) * CELL
            z = origin[2] + (iz - n // 2) * CELL
            rays.append(((x, origin[1] + PROBE_UP, z), (x, origin[1] - PROBE_DOWN, z)))
            cells.append((ix, iz))
    for (ix, iz), h in zip(cells, raycast(rays)):
        if h.hit and h.pos and h.normal and h.normal[1] >= MAX_SLOPE_NY:
            g.floor[ix][iz] = h.pos[1]
    return g


def _astar(g: NavGrid, start: tuple[int, int], goal: tuple[int, int], blocked: set) -> list[tuple[int, int]] | None:
    def h(c):
        return math.hypot(c[0] - goal[0], c[1] - goal[1]) * CELL

    openq = [(h(start), 0.0, start)]
    came: dict = {start: None}
    cost = {start: 0.0}
    while openq:
        _, c_cost, cur = heapq.heappop(openq)
        if cur == goal:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            return path[::-1]
        if c_cost > cost.get(cur, math.inf):
            continue
        h0 = g.floor[cur[0]][cur[1]]
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if not dx and not dz:
                    continue
                nxt = (cur[0] + dx, cur[1] + dz)
                if not g.inside(*nxt) or (cur, nxt) in blocked:
                    continue
                h1 = g.floor[nxt[0]][nxt[1]]
                if h1 is None or abs(h1 - h0) > MAX_STEP * (1.4 if dx and dz else 1.0):
                    continue
                if dx and dz and (g.floor[cur[0] + dx][cur[1]] is None or g.floor[cur[0]][cur[1] + dz] is None):
                    continue  # no cutting corners past a hole
                step = math.hypot(dx, dz) * CELL + 2.0 * abs(h1 - h0)
                nc = c_cost + step
                if nc < cost.get(nxt, math.inf):
                    cost[nxt] = nc
                    came[nxt] = cur
                    heapq.heappush(openq, (nc + h(nxt), nc, nxt))
    return None


def _nearest_walkable(g: NavGrid, want: tuple[int, int]) -> tuple[int, int] | None:
    best, bd = None, math.inf
    for ix in range(g.n):
        for iz in range(g.n):
            if g.floor[ix][iz] is not None:
                d = (ix - want[0]) ** 2 + (iz - want[1]) ** 2
                if d < bd:
                    best, bd = (ix, iz), d
    return best


def _thin(points: list[Vec3]) -> list[Vec3]:
    if len(points) <= 2:
        return points
    out = [points[0]]
    for a, b, c in zip(points, points[1:], points[2:]):
        d1 = (b[0] - a[0], b[2] - a[2])
        d2 = (c[0] - b[0], c[2] - b[2])
        if abs(d1[0] * d2[1] - d1[1] * d2[0]) > 1e-6 or abs(b[1] - a[1]) > 0.3:
            out.append(b)
    out.append(points[-1])
    return out


@dataclass
class Route:
    waypoints: list[Vec3]
    reaches_goal: bool       # False: the goal is outside the grid or unreachable; this gets closer
    rays_used: int


def plan(raycast: Raycast, start: Vec3, goal: Vec3, radius: float = RADIUS) -> Route | None:
    """A walkable route from start toward goal, or None if nothing walkable is around the start."""
    counter = {'rays': 0}

    def counted(rays):
        counter['rays'] += len(rays)
        return raycast(rays)

    g = build_grid(counted, start, radius)
    s = _nearest_walkable(g, g.cell_of(start))
    want = g.cell_of(goal)
    clamped = (min(max(want[0], 0), g.n - 1), min(max(want[1], 0), g.n - 1))
    e = _nearest_walkable(g, clamped)
    if s is None or e is None:
        return None
    blocked: set = set()
    for _ in range(MAX_ROUNDS):
        cells = _astar(g, s, e, blocked)
        if cells is None:
            return None
        rays, steps = [], []
        for a, b in zip(cells, cells[1:]):
            pa, pb = g.center(*a), g.center(*b)
            for lift in (KNEE, CHEST):
                rays.append(((pa[0], pa[1] + lift, pa[2]), (pb[0], pb[1] + lift, pb[2])))
                steps.append((a, b))
        bad = {st for st, h in zip(steps, counted(rays) if rays else []) if h.hit and h.normal and abs(h.normal[1]) < 0.6}
        if not bad:
            pts = [g.center(*c) for c in cells]
            reached = want == clamped and math.hypot(pts[-1][0] - goal[0], pts[-1][2] - goal[2]) <= CELL * 1.5
            return Route(_thin(pts), reached, counter['rays'])
        for a, b in bad:
            blocked.add((a, b))
            blocked.add((b, a))
    return None
