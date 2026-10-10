"""Navigation: a walkable route from the player to a goal, from the game's own collision (phase 4).

1. Grid: one downward ray per cell gives the floor height and slope of each cell.
2. A*: 8 neighbours; a step is allowed when both cells are walkable floor and the height change is
   climbable going up, or a safe drop going down. A cliff edge is a big height change between
   neighbouring cells, so routes go around it. Cost = distance + extra for climbing and dropping.
3. Walls, lazily: only the route A* found is checked, with two sideways rays per step (knee and
   chest height). A blocked step is removed and A* runs again. Most routes need one or two rounds,
   so walls cost tens of rays instead of thousands.
4. The route is thinned to waypoints where it turns.

Two scales (`Profile`):
- LOCAL: 2 m cells, 30 m around the player (961 rays): "lead me there" to something nearby.
- WIDE:  5 m cells, 150 m around the player (3,721 rays, about 0.3 s in Elden Ring at ~12,000 rays/s):
  guiding to far places without leading over cliffs (the user stood at a cliff edge where the fairy,
  flying straight toward the goal, had crossed it). Re-planned as the player moves.
Rays go out in chunks (`chunk`), so a background plan never holds the game's ray block for long.

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

KNEE, CHEST = 0.5, 1.4
MAX_ROUNDS = 12


@dataclass(frozen=True)
class Profile:
    cell: float          # metres per cell
    radius: float        # grid half-size, metres
    probe_up: float      # rays start this far above the player's feet...
    probe_down: float    # ...and end this far below
    max_up: float        # metres a step may rise between neighbouring cells
    max_down: float      # metres a step may drop (a safe drop, not a cliff)
    min_normal_y: float  # floor normal's Y must be at least this
    chunk: int = 4096    # rays per raycast call


LOCAL = Profile(cell=2.0, radius=30.0, probe_up=25.0, probe_down=40.0, max_up=1.1, max_down=1.1, min_normal_y=0.65)
WIDE = Profile(cell=5.0, radius=150.0, probe_up=200.0, probe_down=250.0, max_up=3.5, max_down=4.5,
               min_normal_y=0.6, chunk=256)

FAR = Profile(cell=8.0, radius=300.0, probe_up=250.0, probe_down=300.0, max_up=5.0, max_down=6.5,
              min_normal_y=0.6, chunk=256)  # 75 x 75 = 5,625 rays: goals further than ~250 m

# Kept for callers that used the old module constants.
CELL, RADIUS = LOCAL.cell, LOCAL.radius


@dataclass
class NavGrid:
    origin: Vec3                 # player's feet when built
    n: int                       # cells per side
    cell: float = CELL
    floor: list[list[float | None]] = field(default_factory=list)  # [ix][iz] floor height or None

    def center(self, ix: int, iz: int) -> Vec3:
        h = self.floor[ix][iz]
        return (self.origin[0] + (ix - self.n // 2) * self.cell, h if h is not None else self.origin[1],
                self.origin[2] + (iz - self.n // 2) * self.cell)

    def cell_of(self, p: Vec3) -> tuple[int, int]:
        return (round((p[0] - self.origin[0]) / self.cell) + self.n // 2,
                round((p[2] - self.origin[2]) / self.cell) + self.n // 2)

    def inside(self, ix: int, iz: int) -> bool:
        return 0 <= ix < self.n and 0 <= iz < self.n


def _cast(raycast: Raycast, rays: list, chunk: int) -> list[RayHit]:
    out: list[RayHit] = []
    for i in range(0, len(rays), chunk):
        out += raycast(rays[i:i + chunk])
    return out


def build_grid(raycast: Raycast, origin: Vec3, radius: float | None = None, profile: Profile = LOCAL) -> NavGrid:
    radius = profile.radius if radius is None else radius
    n = int(radius / profile.cell) * 2 + 1
    g = NavGrid(origin, n, profile.cell, [[None] * n for _ in range(n)])
    rays, cells = [], []
    for ix in range(n):
        for iz in range(n):
            x = origin[0] + (ix - n // 2) * profile.cell
            z = origin[2] + (iz - n // 2) * profile.cell
            rays.append(((x, origin[1] + profile.probe_up, z), (x, origin[1] - profile.probe_down, z)))
            cells.append((ix, iz))
    for (ix, iz), h in zip(cells, _cast(raycast, rays, profile.chunk)):
        if h.hit and h.pos and h.normal and h.normal[1] >= profile.min_normal_y:
            g.floor[ix][iz] = h.pos[1]
    return g


def _astar(g: NavGrid, start: tuple[int, int], goal: tuple[int, int], blocked: set,
           profile: Profile = LOCAL) -> list[tuple[int, int]] | None:
    def h(c):
        return math.hypot(c[0] - goal[0], c[1] - goal[1]) * g.cell

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
                if h1 is None:
                    continue
                diag = 1.4 if dx and dz else 1.0
                rise = h1 - h0
                if rise > profile.max_up * diag or -rise > profile.max_down * diag:
                    continue  # too steep to climb, or a drop too big to jump: a cliff
                if dx and dz and (g.floor[cur[0] + dx][cur[1]] is None or g.floor[cur[0]][cur[1] + dz] is None):
                    continue  # no cutting corners past a hole
                step = math.hypot(dx, dz) * g.cell + 2.0 * abs(rise)
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


def _reachable(g: NavGrid, start: tuple[int, int], profile: Profile) -> set:
    """All cells walkable from start (flood fill with the same step rules as A*)."""
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        h0 = g.floor[cur[0]][cur[1]]
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                nxt = (cur[0] + dx, cur[1] + dz)
                if nxt in seen or not g.inside(*nxt):
                    continue
                h1 = g.floor[nxt[0]][nxt[1]]
                diag = 1.4 if dx and dz else 1.0
                if h1 is None or h1 - h0 > profile.max_up * diag or h0 - h1 > profile.max_down * diag:
                    continue
                seen.add(nxt)
                stack.append(nxt)
    return seen


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


def plan(raycast: Raycast, start: Vec3, goal: Vec3, radius: float | None = None, profile: Profile = LOCAL) -> Route | None:
    """A walkable route from start toward goal, or None if nothing walkable is around the start."""
    counter = {'rays': 0}

    def counted(rays):
        counter['rays'] += len(rays)
        return raycast(rays)

    g = build_grid(counted, start, radius, profile)
    s = _nearest_walkable(g, g.cell_of(start))
    if s is None:
        return None
    want = g.cell_of(goal)
    clamped = (min(max(want[0], 0), g.n - 1), min(max(want[1], 0), g.n - 1))
    # Aim at the reachable cell nearest the goal: if the goal itself is cut off (across a cliff, outside
    # the grid), the route still ends as close as walking allows.
    reach = _reachable(g, s, profile)
    e = min(reach, key=lambda c: ((c[0] - clamped[0]) ** 2 + (c[1] - clamped[1]) ** 2,
                                  (c[0] - s[0]) ** 2 + (c[1] - s[1]) ** 2))
    blocked: set = set()
    for _ in range(MAX_ROUNDS):
        cells = _astar(g, s, e, blocked, profile)
        if cells is None:
            return None
        rays, steps = [], []
        for a, b in zip(cells, cells[1:]):
            pa, pb = g.center(*a), g.center(*b)
            for lift in (KNEE, CHEST):
                rays.append(((pa[0], pa[1] + lift, pa[2]), (pb[0], pb[1] + lift, pb[2])))
                steps.append((a, b))
        hits = _cast(counted, rays, profile.chunk) if rays else []
        bad = {st for st, h in zip(steps, hits) if h.hit and h.normal and abs(h.normal[1]) < 0.6}
        if not bad:
            pts = [g.center(*c) for c in cells]
            reached = want == clamped and math.hypot(pts[-1][0] - goal[0], pts[-1][2] - goal[2]) <= g.cell * 1.5
            return Route(_thin(pts), reached, counter['rays'])
        for a, b in bad:
            blocked.add((a, b))
            blocked.add((b, a))
    return None
