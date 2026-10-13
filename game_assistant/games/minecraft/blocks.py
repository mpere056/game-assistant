"""Walking in Minecraft: A* over the blocks themselves (Minecraft has no navigation mesh).

The bridge reads a box of blocks as one byte each (bridges/minecraft Grid.java). A place to stand is a block
cell for the feet: nothing solid in it (or a low slab or carpet to stand on), room for the head above, and
something to stand on below (or water to swim in, or a ladder to hold). Moves: the 8 neighbours on the same
level; one up (a jump, or a stair); down by up to MAX_DROP blocks; up and down ladders. Diagonals never cut a
corner. Lava, fire, magma, cactus, berry bushes and cobwebs are never stepped in or on; doors and gates count
as walls for now (the walk doesn't open them). Powder snow can't hold you up (you sink into it) but can be
waded through (the game slows you only a little and boosts jumps in it; it freezes you after 7 s, so it costs
more). Falls: a walk off an edge carries the player on a block or two before landing, so a drop also counts
what lies past its landing; and stands beside a deadly drop cost more (keep away from cliff edges).

Breaking and building, as Baritone does (when the adapter allows it): mine through what's in the way (walking
on, stepping up, stepping down), dig straight down, pillar up (jump and place a block under the feet), and
bridge a gap (place the missing floor block, sneaking at the edge). Never: a block next to water or lava
(it would flow in), under sand or gravel (it would fall), a door, bedrock and the like, or what isn't loaded.
Each move carries its work, in order: ('break', cell) and ('place', cell, against, 'bridge' or 'pillar').

All coordinates here are Minecraft's own (the adapter mirrors X around this).
"""
from __future__ import annotations

import base64
import heapq
import math
import time
from dataclasses import dataclass, field

AIR, WATER, DANGER, SOLID, LOW, TALL, CLIMB, DOOR, PARTIAL, UNKNOWN, POWDER = range(11)
PASS = {AIR, WATER, CLIMB, POWDER}  # the body can be in these
SOFT = {WATER, POWDER}              # falling into these does no harm
FLOOR = {SOLID, PARTIAL, TALL, LOW}  # these hold you up from below
MAX_DROP = 3                        # blocks: a fall this far costs no health
COST_DIAG = math.sqrt(2.0)
COST_JUMP = 0.6
COST_DROP = 0.4                     # per block fallen
COST_WATER = 4.0                    # swimming is slow (and a fight or a stop there sinks the player): land first
COST_CLIMB = 1.5
COST_POWDER = 3.0                   # wading through powder snow (it freezes after 7 s)
COST_EDGE = 2.0                     # standing next to a fall that would hurt
NEAR_GOAL = 1.6                     # blocks across: a stand this near the goal reaches it
# Costs are in blocks walked; the game's own times converted (Baritone reckons in ticks: walking a block takes
# 20 / 4.317 = 4.63 ticks).
TICKS_PER_BLOCK = 20.0 / 4.317
COST_BREAK = 2.0 / TICKS_PER_BLOCK  # per block broken, on top of its time to break (the bridge's, in 4 ticks)
COST_PLACE = 20.0 / TICKS_PER_BLOCK  # a block placed (bridging): turning round and placing, about a second
COST_PILLAR = COST_PLACE + COST_JUMP  # a jump and a block placed under the feet
H_WEIGHT = 1.5                      # weighted A*: a good route fast rather than the best one slowly
UNBREAKABLE = 127                   # (the bridge's hardness byte: bedrock, obsidian, lava, unloaded)


@dataclass
class Grid:
    min: tuple[int, int, int]
    size: tuple[int, int, int]
    data: bytes
    hard: bytes | None = None    # time to break, 4 ticks a unit (+128: falls, sand and gravel); None: unbreakable

    @classmethod
    def from_bridge(cls, d: dict) -> 'Grid':
        hard = base64.b64decode(d['hard']) if d.get('hard') else None
        return cls(tuple(int(v) for v in d['min']), tuple(int(v) for v in d['size']), base64.b64decode(d['data']),
                   hard)

    def hardness(self, x: int, y: int, z: int) -> int:
        """The bridge's hardness byte (UNBREAKABLE outside the box, or with no hardness known)."""
        lx, ly, lz = x - self.min[0], y - self.min[1], z - self.min[2]
        sx, sy, sz = self.size
        if self.hard is None or not (0 <= lx < sx and 0 <= ly < sy and 0 <= lz < sz):
            return UNBREAKABLE
        return self.hard[(ly * sz + lz) * sx + lx]

    def at(self, x: int, y: int, z: int) -> int:
        lx, ly, lz = x - self.min[0], y - self.min[1], z - self.min[2]
        sx, sy, sz = self.size
        if not (0 <= lx < sx and 0 <= ly < sy and 0 <= lz < sz):
            return UNKNOWN
        return self.data[(ly * sz + lz) * sx + lx]


@dataclass
class Path:
    points: list[tuple[float, float, float]]          # feet positions, block centres
    reaches_goal: bool
    timed_out: bool = False                           # stopped short by the time or the box: plan on from the end
    jumps: list[tuple[tuple, tuple]] = field(default_factory=list)   # (from, to) where a jump is needed
    expanded: int = 0
    length: float = 0.0
    # in walking order: ('break', from, cell), ('place', from, cell, against, kind), ('step', from, to)
    # (from, to: feet positions; cell, against: block cells)
    actions: list = field(default_factory=list)
    cells: list = field(default_factory=list)         # every step's feet position (points: only the turns)


def stand_height(g: Grid, x: int, y: int, z: int) -> float | None:
    """The feet's height when standing in cell (x, y, z), or None when one can't stand there."""
    here = g.at(x, y, z)
    if here == LOW:
        if g.at(x, y + 1, z) in PASS and g.at(x, y + 2, z) in PASS:
            return y + 0.5
        return None
    if here not in PASS or g.at(x, y + 1, z) not in PASS:
        return None
    if here in (WATER, CLIMB) or g.at(x, y - 1, z) in FLOOR:  # (powder: only with ground under it)
        return float(y)
    return None


def find(g: Grid, start: tuple[int, int, int], goal: tuple[int, int, int], budget_s: float = 1.0,
         max_nodes: int = 200_000, can_break: bool = False, blocks: int = 0, kind: str = 'near',
         box=None, no_place=frozenset()) -> Path | None:
    """A route from the stand nearest start toward goal (cells). None: nowhere to stand near the start.
    can_break: mine through blocks; blocks: how many blocks there are to build with (bridging, pillaring).
    kind, what counts as there (Baritone's goals): 'near' within NEAR_GOAL across and 2 up or down; 'block'
    standing next to the goal block, facing a side of it that's open (at the feet, the head, or just below the
    feet, its top open); 'column' within NEAR_GOAL across at any height (a village, a biome). box ((min x, y,
    z), (max x, y, z), the max outside it): anywhere inside it across counts as there too (a village's area)."""
    s = _nearest_stand(g, start)
    if s is None:
        return None
    s = (*s, 0)                                       # (x, y, z, 1 when standing on a block placed on the way)
    gx, gy, gz = goal

    def h(c):
        return H_WEIGHT * math.sqrt((c[0] - gx) ** 2 + (c[1] - gy) ** 2 * 0.5 + (c[2] - gz) ** 2)

    ctx = _Ctx(g, {}, can_break and g.hard is not None, blocks, frozenset(no_place))
    open_ = [(h(s), 0.0, s)]
    came: dict = {s: None}
    cost = {s: 0.0}
    best = (h(s), s)
    t0 = time.perf_counter()
    expanded = 0
    timed_out = False
    end = None
    while open_:
        _f, c_cost, c = heapq.heappop(open_)
        if c_cost > cost.get(c, math.inf):
            continue
        expanded += 1
        hc = h(c)
        if hc < best[0]:
            best = (hc, c)
        if _there(g, c, goal, kind) or (box is not None and _inside(c, box)):
            end = c
            break
        if expanded >= max_nodes or (expanded % 512 == 0 and time.perf_counter() - t0 > budget_s):
            timed_out = True
            break
        for n, step, work in _all_moves(ctx, c):
            nc = c_cost + step
            if nc < cost.get(n, math.inf):
                cost[n] = nc
                came[n] = (c, work)
                heapq.heappush(open_, (nc + h(n), nc, n))
    reached = end is not None
    if end is None:
        end = best[1]
        # the box's edge or the time ran out (not: no way at all) when the best is at the box's edge
        if not timed_out:
            timed_out = _at_edge(g, end)
    cells, works = [], []
    c = end
    while c is not None:
        cells.append(c)
        prev = came[c]
        works.append(prev[1] if prev else ())
        c = prev[0] if prev else None
    cells.reverse()
    works.reverse()                                   # works[i]: what's done on the way into cells[i]
    pts = [(x + 0.5, (None if placed else stand_height(g, x, y, z)) or float(y), z + 0.5)
           for x, y, z, placed in cells]
    jumps, actions = [], []
    for i in range(len(cells) - 1):
        work = works[i + 1]
        for w in work:
            actions.append((w[0], pts[i], *w[1:]))
        pillar = any(w[0] == 'place' and w[3] == 'pillar' for w in work)
        if cells[i + 1][1] > cells[i][1] and g.at(*cells[i][:3]) != CLIMB and pts[i + 1][1] - pts[i][1] > 0.6 \
                and not pillar:
            jumps.append((pts[i], pts[i + 1]))
            actions.append(('step', pts[i], pts[i + 1]))
    keep = {a[1] for a in actions} | {pts[i + 1] for i in range(len(cells) - 1) if works[i + 1]}
    path = Path(_simplify(pts, jumps, keep), reached, timed_out, jumps, expanded)
    path.actions = actions
    path.cells = pts
    path.length = sum(math.dist(a, b) for a, b in zip(path.points, path.points[1:]))
    return path


@dataclass
class _Ctx:
    g: Grid
    falls: dict
    can_break: bool
    blocks: int
    no_place: frozenset = frozenset()   # cells where placing failed lately (not planned there again)


def _breakable(g: Grid, x: int, y: int, z: int) -> float | None:
    """The cost of breaking cell (x, y, z), or None when it mustn't be broken."""
    k = g.at(x, y, z)
    if k in (AIR, WATER, DANGER, UNKNOWN, DOOR, POWDER):
        return None
    hb = g.hardness(x, y, z)
    if hb & 127 >= UNBREAKABLE:
        return None
    if g.hardness(x, y + 1, z) & 128 and g.at(x, y + 1, z) not in PASS:
        return None                                   # sand or gravel above would fall in
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
        nk = g.at(x + dx, y + dy, z + dz)
        if nk == WATER or (nk == DANGER and g.hardness(x + dx, y + dy, z + dz) & 127 >= UNBREAKABLE):
            return None                               # water or lava would flow in
    return COST_BREAK + (hb & 127) * 4.0 / TICKS_PER_BLOCK


def _open(ctx: _Ctx, cells) -> tuple[float, tuple] | None:
    """Cost and work to make these cells room for the body (breaking what's there), or None."""
    total, work = 0.0, []
    for c in cells:
        if ctx.g.at(*c) in PASS:
            continue
        if not ctx.can_break:
            return None
        b = _breakable(ctx.g, *c)
        if b is None:
            return None
        total += b
        work.append(('break', c))
    work.sort(key=lambda w: -w[1][1])                 # the top first
    return total, tuple(work)


def _all_moves(ctx: _Ctx, c):
    """Every move from node c: (next node, cost, work)."""
    g = ctx.g
    x, y, z, placed = c
    plain = set()
    for n, step in _moves(g, (x, y, z), ctx.falls):
        plain.add(n)
        yield (*n, 0), step, ()
    if not ctx.can_break and ctx.blocks <= 0:
        return
    here = g.at(x, y, z)
    floor_solid = placed or g.at(x, y - 1, z) == SOLID
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, nz = x + dx, z + dz
        if any(n[0] == nx and n[2] == nz for n in plain):
            continue
        # walk on, breaking what's in the way; or bridge a missing floor
        room = _open(ctx, ((nx, y, nz), (nx, y + 1, nz)))
        if room is not None:
            fk = g.at(nx, y - 1, nz)
            if fk in FLOOR:
                if room[1]:
                    yield (nx, y, nz, 0), 1.0 + room[0] + _edge_cost(g, nx, y, nz, ctx.falls), room[1]
                    continue
            elif fk in (AIR, WATER) and ctx.blocks > 0 and floor_solid and here not in (WATER, CLIMB) \
                    and (nx, y - 1, nz) not in ctx.no_place:
                work = room[1] + (('place', (nx, y - 1, nz), (x, y - 1, z), 'bridge'),)
                yield (nx, y, nz, 1), 1.0 + COST_PLACE + room[0] + _edge_cost(g, nx, y, nz, ctx.falls), work
                continue
        # step up one, breaking what's in the way (and room above the head here)
        if g.at(nx, y, nz) in FLOOR:
            room = _open(ctx, ((x, y + 2, z), (nx, y + 1, nz), (nx, y + 2, nz)))
            if room is not None and room[1]:
                yield (nx, y + 1, nz, 0), 1.0 + COST_JUMP + room[0] + _edge_cost(g, nx, y + 1, nz, ctx.falls), \
                    room[1]
                continue
        # step down one, breaking what's in the way
        if g.at(nx, y - 2, nz) in FLOOR:
            room = _open(ctx, ((nx, y + 1, nz), (nx, y, nz), (nx, y - 1, nz)))
            if room is not None and room[1]:
                yield (nx, y - 1, nz, 0), 1.0 + COST_DROP + room[0] + _edge_cost(g, nx, y - 1, nz, ctx.falls), \
                    room[1]
    # dig straight down (the floor here), onto ground
    if ctx.can_break and not placed and here not in (WATER, CLIMB) and g.at(x, y - 2, z) in FLOOR \
            and g.at(x, y - 1, z) not in PASS:
        b = _breakable(g, x, y - 1, z)
        if b is not None:
            yield (x, y - 1, z, 0), b + 0.5, (('break', (x, y - 1, z)),)
    # pillar up: jump and place a block under the feet (a jump needs two blocks of room above the head: with
    # one, the head hit the ceiling and the block never fitted under the feet, 2026-10-13)
    if ctx.blocks > 0 and here not in (WATER, CLIMB) and (placed or g.at(x, y - 1, z) in FLOOR) \
            and (x, y, z) not in ctx.no_place:
        room = _open(ctx, ((x, y + 2, z), (x, y + 3, z)))
        if room is not None:
            yield (x, y + 1, z, 1), COST_PILLAR + room[0], \
                room[1] + (('place', (x, y, z), (x, y - 1, z), 'pillar'),)


def _inside(c, box) -> bool:
    (x0, _y0, z0), (x1, _y1, z1) = box
    return x0 <= c[0] < x1 and z0 <= c[2] < z1


def _there(g: Grid, c, goal, kind: str) -> bool:
    x, y, z = c[0], c[1], c[2]
    gx, gy, gz = goal
    if kind == 'block':
        if abs(x - gx) + abs(z - gz) != 1:
            return False
        if gy in (y, y + 1):
            return True
        return gy == y - 1 and g.at(gx, y, gz) in PASS        # just below the feet in front: its top open
    if kind == 'column':
        return abs(x - gx) + abs(z - gz) <= NEAR_GOAL
    if kind == 'item':                                      # on its block (a dropped item, to pick it up)
        return x == gx and z == gz and abs(y - gy) <= 1
    return abs(x - gx) + abs(z - gz) <= NEAR_GOAL and abs(y - gy) <= 2


def _at_edge(g: Grid, c) -> bool:
    lx, lz = c[0] - g.min[0], c[2] - g.min[2]
    return lx <= 2 or lz <= 2 or lx >= g.size[0] - 3 or lz >= g.size[2] - 3


def _nearest_stand(g: Grid, c):
    x, y, z = c
    for r in range(0, 3):
        for dy in (0, -1, 1, -2, 2):
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if max(abs(dx), abs(dz)) != r:
                        continue
                    if stand_height(g, x + dx, y + dy, z + dz) is not None:
                        return (x + dx, y + dy, z + dz)
    return None


def _clear(g: Grid, x: int, y0: int, y1: int, z: int) -> bool:
    return all(g.at(x, y, z) in PASS for y in range(y0, y1 + 1))


def _fall(g: Grid, x: int, y: int, z: int, falls: dict | None = None) -> int:
    """How far one falls stepping into cell (x, y, z) (0: something to stand on there; water or powder snow
    below: 0, they break the fall; 9: farther than this box shows, or something deadly)."""
    key = (x, y, z)
    if falls is not None and key in falls:
        return falls[key]
    out = 9
    for d in range(0, 9):
        k = g.at(x, y - d, z)
        if k in SOFT:
            out = 0
            break
        if k not in PASS:
            out = 9 if k in (DANGER, UNKNOWN) else d - 1 if d else 0
            break
    if falls is not None:
        falls[key] = out
    return out


def _edge_cost(g: Grid, x: int, y: int, z: int, falls: dict) -> float:
    """COST_EDGE when a step to the side (4 ways) falls farther than MAX_DROP."""
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if _clear(g, x + dx, y, y + 1, z + dz) and _fall(g, x + dx, y, z + dz, falls) > MAX_DROP:
            return COST_EDGE
    return 0.0


def _overshoot_ok(g: Grid, x: int, y: int, z: int, dx: int, dz: int, d: int, falls: dict) -> bool:
    """Walking off an edge at height y and landing d lower in the next cell: the player carries on a block or
    two (more the farther the fall) before landing; nowhere there may the fall add up past MAX_DROP."""
    if g.at(x + dx, y - d, z + dz) in SOFT:
        return True
    for k in range(2, 3 + (d >= 2)):
        cx, cz = x + dx * k, z + dz * k
        if not _clear(g, cx, y - d, y - d + 1, cz):
            return True                                 # a wall: the player stops against it
        if d + _fall(g, cx, y - d, cz, falls) > MAX_DROP:
            return False
    return True


def _moves(g: Grid, c, falls: dict | None = None):
    if falls is None:
        falls = {}
    x, y, z = c
    here = g.at(x, y, z)
    swim = here == WATER
    if here == CLIMB:                                   # up and down a ladder
        for dy in (1, -1):
            if stand_height(g, x, y + dy, z) is not None or g.at(x, y + dy, z) == CLIMB:
                yield (x, y + dy, z), COST_CLIMB
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if dx == 0 and dz == 0:
                continue
            diag = dx != 0 and dz != 0
            base = COST_DIAG if diag else 1.0
            nx, nz = x + dx, z + dz
            # same level
            if stand_height(g, nx, y, nz) is not None and _corner_ok(g, x, y, z, dx, dz, 1):
                k = g.at(nx, y, nz)
                slow = COST_WATER if k == WATER or swim else COST_POWDER if k == POWDER else 1.0
                yield (nx, y, nz), base * slow + _edge_cost(g, nx, y, nz, falls)
                continue
            # one up: a jump (room above the head here), or a stair
            if stand_height(g, nx, y + 1, nz) is not None and g.at(x, y + 2, z) in PASS \
                    and _corner_ok(g, x, y + 1, z, dx, dz, 2) and not diag:
                yield (nx, y + 1, nz), base + COST_JUMP + _edge_cost(g, nx, y + 1, nz, falls)
                continue
            # down: walk off the edge and fall
            if not diag and _clear(g, nx, y, y + 1, nz):
                for d in range(1, MAX_DROP + 1):
                    if g.at(nx, y - d, nz) not in PASS:
                        break
                    if stand_height(g, nx, y - d, nz) is not None:
                        if _overshoot_ok(g, x, y, z, dx, dz, d, falls):
                            yield (nx, y - d, nz), base + COST_DROP * d + _edge_cost(g, nx, y - d, nz, falls)
                        break


def _corner_ok(g: Grid, x, y, z, dx, dz, h) -> bool:
    """A diagonal step doesn't clip either corner (both side cells clear for the body's height)."""
    if dx == 0 or dz == 0:
        return True
    return _clear(g, x + dx, y, y + h, z) and _clear(g, x, y, y + h, z + dz)


def _simplify(pts, jumps, keep=()):
    """Keep the corners, every jump's ends, where work is done and level changes; drop points on straight
    stretches."""
    if len(pts) <= 2:
        return pts
    keep_set = {p for j in jumps for p in j} | set(keep)
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        d1 = (b[0] - a[0], b[2] - a[2])
        d2 = (c[0] - b[0], c[2] - b[2])
        if d1 != d2 or b[1] != a[1] or c[1] != b[1] or b in keep_set:
            out.append(b)
    out.append(pts[-1])
    return out
