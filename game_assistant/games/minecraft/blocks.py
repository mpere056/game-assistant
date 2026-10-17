"""Minecraft's blocks as the walk sees them: the grid the bridge reads, where one can stand, what's in sight.

The bridge reads a box of blocks as one byte each (bridges/minecraft Grid.java): what kind of thing is there
for walking (air, water, a full block, a slab, a fence, a ladder, a door, lava and the like, powder snow) and
how long each takes to break. A place to stand is a block cell for the feet: nothing solid in it (or a low slab
or carpet to stand on), room for the head above, and something to stand on below (or water to swim in, its
top, or a ladder to hold). What counts as there (_there), and whether a block can be seen and reached from a
cell (sees: the crosshair's way), are here too; the routes themselves are planned in pathing.py (Baritone's
way) and done in moving.py.

All coordinates here are Minecraft's own (the adapter mirrors X around this).
"""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field

AIR, WATER, DANGER, SOLID, LOW, TALL, CLIMB, DOOR, PARTIAL, UNKNOWN, POWDER = range(11)
PASS = {AIR, WATER, CLIMB}          # the body can be in these
SOFT = {WATER}                      # falling into these does no harm
# Powder snow is neither: the body sinks in it, can't climb out (without leather boots), and freezes (the walk
# waded into it in a grove, stuck, and the player lost 8 health, 2026-10-13). It's broken to get through, never
# walked or fallen into.
FLOOR = {SOLID, PARTIAL, TALL, LOW}  # these hold you up from below
MAX_DROP = 3                        # blocks: a fall this far costs no health (onto ground)...
MAX_WATER_DROP = 60                 # ...and into water, any fall (as far as this)
NEAR_GOAL = 1.6                     # blocks across: a stand this near the goal reaches it
UNBREAKABLE = 127                   # (the bridge's hardness byte: bedrock, obsidian, lava, unloaded)


@dataclass
class Grid:
    min: tuple[int, int, int]
    size: tuple[int, int, int]
    data: bytes
    hard: bytes | None = None    # time to break, 4 ticks a unit (+128: falls, sand and gravel); None: unbreakable
    keep: frozenset = frozenset()    # blocks never broken on the way (the one to mine: a route dug through it)
    seen: dict = field(default_factory=dict, repr=False)   # sees() worked out: (cell, block) -> bool

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
    cost: float = 0.0                                 # what it costs in all (blocks walked)


def stand_height(g: Grid, x: int, y: int, z: int) -> float | None:
    """The feet's height when standing in cell (x, y, z), or None when one can't stand there."""
    here = g.at(x, y, z)
    if here == LOW:
        if g.at(x, y + 1, z) in PASS and g.at(x, y + 2, z) in PASS:
            return y + 0.5
        return None
    if here not in PASS or g.at(x, y + 1, z) not in PASS:
        return None
    if here == WATER:
        # swimming: at the surface, the head out of the water (a route through deep water drowned the player and
        # lost them a lot of health, 2026-10-13)
        return float(y) if g.at(x, y + 1, z) not in (WATER,) else None
    if here == CLIMB or g.at(x, y - 1, z) in FLOOR:  # (powder: only with ground under it)
        return float(y)
    return None


def _breakable(g: Grid, x: int, y: int, z: int) -> float | None:
    """The ticks breaking cell (x, y, z) takes, or None when it mustn't be broken (a block in a line of sight,
    mined first)."""
    k = g.at(x, y, z)
    if k in (AIR, WATER, DANGER, UNKNOWN, DOOR) or (x, y, z) in g.keep:
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
    return (hb & 127) * 4.0


def _open_sky(c, sky) -> bool:
    """Standing at or above the top of the column (nothing over the head: the surface). sky: (x, z) -> the
    height where the open sky starts, or None (not known)."""
    top = sky(c[0], c[2])
    return top is not None and c[1] >= top


def _on_ground(g: Grid, c, ground) -> bool:
    """Standing on the land itself, not up in a tree (nor on a stump). ground: (x, z) -> the height of the first
    air above the land there, or None (not known); a slab or path block on the land is the land's top."""
    top = ground(c[0], c[2])
    if top is None:
        return False
    return c[1] == top or (c[1] == top - 1 and g.at(c[0], c[1], c[2]) in (LOW, PARTIAL))


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
    if kind == 'cell':                                      # that very block (a spot in cover: one beside it
        return (x, y, z) == tuple(goal)                     # was still in sight, 2026-10-16)
    if kind == 'exact':
        # that block (or one beside it) at that height: "the top of the tree" is on it, not 2 below in its leaves
        return abs(x - gx) + abs(z - gz) <= 1 and y == gy
    if kind == 'place':
        # a block to put there: in reach and in sight of the cell, and not standing in it (the game won't put a
        # block where the player is)
        return (x, y, z) != tuple(goal) and (x, y + 1, z) != tuple(goal) and sees(g, (x, y, z), goal)
    if kind == 'mine':
        # within reach of it, and some of it in sight from the eye (to mine it or use it): a crafting table
        # one block behind a wall was "near" and couldn't be opened, 2026-10-13
        return sees(g, (x, y, z), goal)
    if kind == 'column':                                    # (Baritone's GoalXZ: that spot across, any height)
        return abs(x - gx) + abs(z - gz) <= NEAR_GOAL
    if kind == 'level':                                     # (GoalYLevel: that height, anywhere)
        return y == gy
    if kind.startswith('within:'):                          # (GoalNear: within that many blocks of it)
        r = float(kind[7:])
        return (x - gx) ** 2 + (y - gy) ** 2 + (z - gz) ** 2 <= r * r
    if kind == 'ground':                                    # (find's ground: on the land anywhere)
        return False
    if kind == 'item':                                      # on its block (a dropped item, to pick it up)
        return x == gx and z == gz and abs(y - gy) <= 1
    return abs(x - gx) + abs(z - gz) <= NEAR_GOAL and abs(y - gy) <= 2


EYE = 1.62              # the eye above the feet (standing)
REACH = 4.5             # blocks from the eye a block can be mined or used at (the game's)...
PLAN_REACH = 4.1        # ...and where a walk to mine or use one ends: a little nearer (the aim's own reach, the
                        # game's, was 4.4 while routes ended at 4.5: two ores beside the player "could not be
                        # mined", skipped, and "no more iron ore near here", 2026-10-15)
# points on a face looked at: its middle and four 0.3 in from the edges (not nearer them: a line grazing a corner
# was "in sight" from the middle of the cell, and blocked from where the player really stood, 2026-10-13)
FACE_POINTS = ((0.5, 0.5), (0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7))


MAX_BLOCKERS = 1        # blocks in the way of a line of sight that are mined first (a leaf before a log in the canopy:
                        # the log was "no way" from the ground, 2026-10-15)


def sees(g: Grid, c, key, eye: float = EYE, reach: float = PLAN_REACH, blockers: int = MAX_BLOCKERS) -> bool:
    """Whether a player standing in cell c reaches block key and can see some of it: a point on a face of
    it turned toward the eye (its middle or near a corner), within reach, with nothing but what the body can
    be in between (air, water, a ladder) (as the game's crosshair works)."""
    ex, ey, ez = c[0] + 0.5, c[1] + eye, c[2] + 0.5
    kx, ky, kz = key
    if math.dist((ex, ey, ez), (kx + 0.5, ky + 0.5, kz + 0.5)) > reach + 0.9:
        return False
    memo = (c[0], c[1], c[2], kx, ky, kz, blockers)
    if memo in g.seen:
        return g.seen[memo]
    # (the cells the body is in are open, whatever the grid read before the way there dug them: from the bottom of
    # a shaft planned down to a stone, the eye "in" the dirt never saw it, 2026-10-13)
    own = {(c[0], c[1], c[2]), (c[0], c[1] + 1, c[2])}
    g.seen[memo] = got = _sees(g, (ex, ey, ez), key, reach, own, blockers, c[1])
    return got


def sight_cost(g: Grid, c, key, eye: float = EYE, reach: float = PLAN_REACH) -> float | None:
    """What it takes, from standing in cell c, to have block key in reach and in sight: 0 (seen as it is), the
    ticks breaking the one block in the way of the cheapest line takes, or None (neither). (A stone "in sight"
    past a block of dirt counted as seen: the dirt was dug first, a stone in plain sight a little farther passed
    over, 2026-10-16.)"""
    if sees(g, c, key, eye, reach, blockers=0):
        return 0.0
    if not sees(g, c, key, eye, reach, blockers=1):
        return None
    memo = ('cost', c[0], c[1], c[2], *key)
    if memo in g.seen:
        return g.seen[memo]
    e = (c[0] + 0.5, c[1] + eye, c[2] + 0.5)
    own = {(c[0], c[1], c[2]), (c[0], c[1] + 1, c[2])}
    best = None
    for axis in range(3):
        for side in (0.0, 1.0):
            face = key[axis] + side
            if (e[axis] - face) * (1.0 if side else -1.0) <= 0.0:
                continue
            a, b = [i for i in range(3) if i != axis]
            for u, v in FACE_POINTS:
                q = [0.0, 0.0, 0.0]
                q[axis], q[a], q[b] = face, key[a] + u, key[b] + v
                if math.dist(e, q) > reach:
                    continue
                found = []
                if _clear_line(g, e, q, key, own, 1, c[1], found) and found:
                    t = (_breakable(g, *found[0]) or 0.0) + BREAK_EXTRA
                    best = t if best is None else min(best, t)
    g.seen[memo] = best
    return best


BREAK_EXTRA = 2.0       # ticks a block broken costs past its time to break (Baritone's penalty)


def _sees(g: Grid, e, key, reach: float, own=frozenset(), blockers: int = 0, feet: int | None = None) -> bool:
    best = False
    for axis in range(3):
        for side in (0.0, 1.0):
            face = key[axis] + side
            if (e[axis] - face) * (1.0 if side else -1.0) <= 0.0:
                continue                                  # (turned away from the eye)
            a, b = [i for i in range(3) if i != axis]
            for u, v in FACE_POINTS:
                q = [0.0, 0.0, 0.0]
                q[axis], q[a], q[b] = face, key[a] + u, key[b] + v
                if math.dist(e, q) > reach:
                    continue
                if _clear_line(g, e, q, key, own, blockers, feet):
                    return True
    return best


def _clear_line(g: Grid, e, q, key, own=frozenset(), blockers: int = 0, feet: int | None = None,
                found: list | None = None) -> bool:
    """Nothing in the way from e to q (a point on block key's face): every cell the line passes through on the
    way (each once, in order: a voxel walk) is one the body can be in, till key's own; but for up to `blockers`
    that can be mined first (not below the feet: never the floor stood on; found: those, added to it)."""
    cell = [math.floor(e[0]), math.floor(e[1]), math.floor(e[2])]
    d = [q[i] - e[i] for i in range(3)]
    step, t_max, t_delta = [0, 0, 0], [math.inf] * 3, [math.inf] * 3
    for i in range(3):
        if d[i] > 0:
            step[i], t_max[i], t_delta[i] = 1, (cell[i] + 1 - e[i]) / d[i], 1 / d[i]
        elif d[i] < 0:
            step[i], t_max[i], t_delta[i] = -1, (cell[i] - e[i]) / d[i], -1 / d[i]
    key = tuple(key)
    while True:
        if tuple(cell) == key:
            return True
        if g.at(*cell) not in PASS and tuple(cell) not in own:
            if blockers <= 0 or (feet is not None and cell[1] < feet) or _breakable(g, *cell) is None:
                return False
            blockers -= 1
            if found is not None:
                found.append(tuple(cell))
        i = min(range(3), key=lambda j: t_max[j])
        if t_max[i] > 1.0:
            return True                                   # (at the point: on the face)
        cell[i] += step[i]
        t_max[i] += t_delta[i]


def _at_edge(g: Grid, c) -> bool:
    lx, lz = c[0] - g.min[0], c[2] - g.min[2]
    return lx <= 2 or lz <= 2 or lx >= g.size[0] - 3 or lz >= g.size[2] - 3




def _opened(g: Grid, cells) -> Grid:
    """A copy of the grid with these cells open (air)."""
    data = bytearray(g.data)
    for x, y, z in cells:
        lx, ly, lz = x - g.min[0], y - g.min[1], z - g.min[2]
        sx, sy, sz = g.size
        if 0 <= lx < sx and 0 <= ly < sy and 0 <= lz < sz:
            data[(ly * sz + lz) * sx + lx] = AIR
    return Grid(g.min, g.size, bytes(data), g.hard)


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
    """How far one falls stepping into cell (x, y, z) (0: something to stand on there; water below: 0, it
    breaks the fall; 9: farther than this box shows, or something deadly: lava, powder snow)."""
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
            out = 9 if k in (DANGER, UNKNOWN, POWDER) else d - 1 if d else 0
            break
    if falls is not None:
        falls[key] = out
    return out


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
