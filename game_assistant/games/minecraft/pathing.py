"""Walking in Minecraft the way Baritone does it (github.com/cabaletta/baritone, studied for this; our own code).

A route is a list of moves, each one block-sized thing the body does: walk on (traverse), step up (ascend), step
down (descend), walk off an edge and fall (fall), walk diagonally (diagonal), jump and put a block under the feet
(pillar), dig the floor and drop (downward), jump a gap (parkour). Each move knows what it costs, what must be
broken first and what block must be put down, and (moving.py) how it's done, frame by frame. What it costs is in
game ticks, from the game's own physics (walking a block takes 20 / 4.317 ticks; a fall of n blocks the ticks
gravity takes), plus Baritone's penalties for jumping, breaking and placing.

The search is A* from the player's feet to whatever counts as there (a goal, or any of several: the cheapest
reached is the one taken, as Baritone's mining does). Out of time, the best partial way so far is taken (the
first, of a few weights, that gets at least MIN_PART blocks from the start), and planned on from its end.

All coordinates are Minecraft's own (the adapter mirrors X round this). Cells are where the feet are.
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field, replace

from .blocks import (AIR, CLIMB, DANGER, DOOR, FLOOR, LOW, PARTIAL, PASS, POWDER, SOLID, TALL, UNBREAKABLE, UNKNOWN,
                     WATER, Grid, Path, _at_edge, _clear, _inside, _nearest_stand, _on_ground, _open_sky, _opened,
                     _simplify, _there, sight_cost, stand_height)

# ---- what things cost, in ticks (the game's own physics) ----
WALK = 20 / 4.317                   # a block walked
SPRINT = 20 / 5.612                 # ...sprinted
SPRINT_MULT = SPRINT / WALK
WATER_WALK = 20 / 2.2               # a block through water
SNEAK = 20 / 1.3                    # a block crouched (bridging backwards)
LADDER_UP = 20 / 2.35
LADDER_DOWN = 20 / 3.0
WALK_OFF = WALK * 0.8               # to an edge and 0.3 past it, where the fall starts
CENTER_AFTER_FALL = WALK - WALK_OFF
INF = 1_000_000.0
SQRT2 = math.sqrt(2.0)


def _fall_speed(ticks: int) -> float:
    return (0.98 ** ticks - 1) * -3.92


def fall_ticks(distance: float) -> float:
    """Ticks to fall this far from standing (gravity 0.08 a tick, drag 0.98)."""
    if distance <= 0:
        return 0.0
    left, t = distance, 0
    while True:
        v = _fall_speed(t)
        if left <= v:
            return t + left / v
        left -= v
        t += 1


FALL_N = [fall_ticks(i) for i in range(258)]
JUMP_ONE = fall_ticks(1.25) - fall_ticks(0.25)   # up a block: the rise to 1.25 less the fall back to 1

# Baritone's penalties (its defaults)
JUMP_PENALTY = 2.0
PLACE_PENALTY = 20.0
BREAK_PENALTY = 2.0
MAX_FALL = 3                        # blocks fallen onto ground with no harm
MAX_WATER_FALL = 60                 # ...into water: any
HEURISTIC = 3.563                   # ticks a block, the estimate of what's left (a sprinted block)
# (Python expands nodes some fifty times slower than Baritone's Java: the estimate's weighted a little, a good way
# fast rather than the best slowly; more toward a block in the ground, where the way is mostly dug)
H_WEIGHT = 1.25
IN_REACH = 3                        # blocks across (and up or down) a block to mine is got to from
SIDES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
H_BURIED = 2.0
CLIMB_FAR = 8                       # blocks up to the goal at least: a climb...
H_CLIMB = 2.5                       # ...weighted this much at least (greedier: found, if not the cheapest)
COEFFICIENTS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)   # weights for the best partial way, out of time
MIN_PART = 5.0                      # blocks from the start a partial way must get
FEWEST_JUMPS = 400.0                # ticks more a jump over a gap, for the way with the fewest of them
WATER_DIG = 5.0                     # breaking from in water takes this many times as long...
AIR_DIG = 5.0                       # ...and this many again not standing on anything (the game's)
GAP_HURTS = 100.0                   # ticks more a jump over a gap a fall into would hurt (not water: dry ground
                                    # past 2 down), so a way round is taken where there is one
BLOCK_GET_TICKS = 6 * WALK          # what one more block to build with costs to get (mined beside)
# climbing back up a block from a hole dug straight down (a step dug out of it, a jump): for a way come back
# along (what's gathered there is taken back)
CLIMB_BACK = WALK + JUMP_ONE + JUMP_PENALTY + 2 * (BREAK_PENALTY + 24.0)
# (pillaring with input sent from outside the game a frame late: the block goes under the feet near the top of
# the jump, and a ceiling one over the head stops the jump short of where it fits; so the cell over the head's
# is opened too: our addition to Baritone's pillar)
PILLAR_HEADROOM = True


@dataclass(frozen=True)
class Move:
    kind: str                       # 'traverse', 'ascend', 'descend', 'fall', 'diagonal', 'pillar', 'downward',
    #                                 'parkour', 'leap' (a jump any way)
    src: tuple                      # feet cell before
    dest: tuple                     # feet cell after
    cost: float                     # ticks
    breaks: tuple = ()              # cells broken first, in this order
    place: tuple | None = None      # the cell a block is put in (bridging, onto a step, a pillar)
    against: tuple | None = None    # the block it's put against (None: a pillar's, under the feet mid-jump)
    info: tuple = ()                # extra, as pairs: parkour ('dist', n), ('ascend', b), ('sprint', b); leap
    #                                 ('dist', blocks across), ('rise', -1 to 1), ('sprint', b);
    #                                 ('ladder', True); ('sneak', True) (bridging backwards)

    def get(self, key, default=None):
        for k, v in self.info:
            if k == key:
                return v
        return default


@dataclass
class Ctx:
    g: Grid
    can_break: bool = False
    blocks: int = 0                 # blocks there are to build with (0: never place)
    no_place: frozenset = frozenset()
    parkour: bool = True
    parkour_ascend: bool = True
    can_sprint: bool = True
    back: bool = False
    avoid: dict | None = None       # cell -> cost multiplier (round hostile creatures: Baritone's avoidance)
    falls: dict = field(default_factory=dict)
    mined: dict = field(default_factory=dict)
    no_jump: frozenset = frozenset()    # (from, to) jumps lately not made (too slow at the edge): not planned


# ---- the blocks ----

def _through(k) -> bool:            # the body can be in it
    return k in PASS


def _open_air(k) -> bool:           # nothing at all in it (a jump's arc)
    return k == AIR


def _on(k) -> bool:                 # holds the feet up from below
    return k in FLOOR


def _bad(k) -> bool:                # never walked into or onto (lava, fire, magma, cactus, berry bushes, cobweb)
    return k == DANGER


def mine_ticks(ctx: Ctx, x: int, y: int, z: int, falling: bool = False) -> float:
    """Ticks to clear cell (x, y, z) for the body: 0 when it's clear; INF when it mustn't (or can't) be broken.
    falling: what would fall into it from above (sand, gravel) is counted too."""
    key = (x, y, z, falling)
    got = ctx.mined.get(key)
    if got is not None:
        return got
    g = ctx.g
    k = g.at(x, y, z)
    if _through(k):
        out = 0.0
    elif not ctx.can_break or k in (DANGER, UNKNOWN, DOOR) or (x, y, z) in g.keep:
        out = INF
    else:
        hb = g.hardness(x, y, z)
        out = INF
        if hb & 127 < UNBREAKABLE and not _flows_in(g, x, y, z):
            out = (hb & 127) * 4.0 + BREAK_PENALTY
            above = g.at(x, y + 1, z)
            if g.hardness(x, y + 1, z) & 128 and not _through(above):
                out = out + mine_ticks(ctx, x, y + 1, z, True) if falling else INF
    ctx.mined[key] = out
    return out


def _flows_in(g: Grid, x: int, y: int, z: int) -> bool:
    """Water or lava beside it or above (not below) would flow in once it's broken."""
    for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0)):
        nk = g.at(x + dx, y + dy, z + dz)
        if nk == WATER or (nk == DANGER and g.hardness(x + dx, y + dy, z + dz) & 127 >= UNBREAKABLE):
            return True
    return False


def _breaks(ctx: Ctx, cells) -> tuple:
    return tuple(c for c in cells if not _through(ctx.g.at(*c)))


def place_ticks(ctx: Ctx, cell) -> float:
    if ctx.blocks <= 0 or cell in ctx.no_place or ctx.g.at(*cell) != AIR:
        return INF
    return PLACE_PENALTY


def _solid_face(k) -> bool:         # a block is put against it
    return k in (SOLID, PARTIAL)


def against_of(g: Grid, cell, not_toward=None):
    """A block beside or below cell to put a block against (not the one at not_toward), or None."""
    x, y, z = cell
    for d in ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)):
        n = (x + d[0], y + d[1], z + d[2])
        if not_toward is not None and (n[0], n[2]) == (not_toward[0], not_toward[2]) and n[1] == y:
            continue
        if _solid_face(g.at(*n)):
            return n
    return None


def _feet(ctx: Ctx, c) -> float:
    """The feet's height standing in c, as Baritone takes it: the node is stood in (what was in its cell was
    broken on the way there), on a slab in it half a block up."""
    x, y, z, placed = c
    return y + 0.5 if not placed and ctx.g.at(x, y, z) == LOW else float(y)


# ---- the moves ----

def moves(ctx: Ctx, c):
    """Every move from node c (x, y, z, placed: 1 when the floor is a block put there on the way): not a jump lately
    not made (ctx.no_jump); and digging from in water at what it costs there (the game: 5 times as long in water,
    and 5 times again not standing on anything: a bank dug into from the water took 30 s, bobbing, 2026-10-17)."""
    x, y, z, _placed = c
    g = ctx.g
    swim = g.at(x, y, z) == WATER
    slow = (WATER_DIG if _on(g.at(x, y - 1, z)) else WATER_DIG * AIR_DIG) if swim else 1.0
    for m in _moves(ctx, c):
        if m.kind in ('parkour', 'leap') and (m.src, m.dest) in ctx.no_jump:
            continue
        if slow > 1.0 and m.breaks:
            m = replace(m, cost=m.cost + (slow - 1.0) * sum(mine_ticks(ctx, *b) for b in m.breaks))
        yield m


def _moves(ctx: Ctx, c):
    x, y, z, placed = c
    g = ctx.g
    here = g.at(x, y, z)
    floor = g.at(x, y - 1, z)
    on_ladder = here == CLIMB or floor == CLIMB
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        m = _traverse(ctx, c, dx, dz, on_ladder)
        if m is not None:
            yield m
        m = _ascend(ctx, c, dx, dz, on_ladder)
        if m is not None:
            yield m
        m = _descend(ctx, c, dx, dz, on_ladder)
        if m is not None:
            yield m
        if ctx.parkour and not placed:
            m = _parkour(ctx, c, dx, dz)
            if m is not None:
                yield m
    if ctx.parkour:
        yield from _leaps(ctx, c)
    for dx, dz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        m = _diagonal(ctx, c, dx, dz, on_ladder)
        if m is not None:
            yield m
    m = _pillar(ctx, c)
    if m is not None:
        yield m
    m = _downward(ctx, c)
    if m is not None:
        yield m


def _traverse(ctx: Ctx, c, dx, dz, on_ladder) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    nx, nz = x + dx, z + dz
    feet, head, under = (nx, y, nz), (nx, y + 1, nz), (nx, y - 1, nz)
    kf, kh, ku = g.at(*feet), g.at(*head), g.at(*under)
    src = (x, y, z)
    if kf == LOW:                                    # onto a slab or carpet in the cell: stepped on
        if not _through(kh) or not _through(g.at(nx, y + 2, nz)):
            return None
        return Move('traverse', src, feet, WALK)
    if _bad(kf) or _bad(kh):
        return None
    if _on(ku) or (kf == WATER and _through(kh)) or kf == CLIMB:
        water = WATER in (kf, kh)
        if water and kh == WATER:
            return None                              # (the head under: never swum under water)
        h1 = mine_ticks(ctx, *feet)
        h2 = mine_ticks(ctx, *head, falling=True)
        if h1 >= INF or h2 >= INF:
            return None
        wc = WATER_WALK if water else WALK
        if h1 == 0 and h2 == 0:
            if not water and ctx.can_sprint:
                wc *= SPRINT_MULT
        elif on_ladder:
            h1, h2 = h1 * 5, h2 * 5
        return Move('traverse', src, feet, wc + h1 + h2, _breaks(ctx, (head, feet)))
    # a bridge: the floor put down first
    if on_ladder or g.at(x, y, z) == WATER:
        return None
    pc = place_ticks(ctx, under)
    if pc >= INF:
        return None
    h1 = mine_ticks(ctx, *feet)
    h2 = mine_ticks(ctx, *head, falling=True)
    if h1 >= INF or h2 >= INF:
        return None
    against = against_of(g, under, not_toward=(x, y - 1, z))
    if against is not None:
        return Move('traverse', src, feet, WALK + pc + h1 + h2, _breaks(ctx, (head, feet)), under, against)
    # backwards, crouched at the edge, against the side of the block stood on
    if not (placed or _solid_face(g.at(x, y - 1, z))):
        return None
    return Move('traverse', src, feet, WALK * (SNEAK / WALK) + pc + h1 + h2, _breaks(ctx, (head, feet)), under,
                (x, y - 1, z), (('sneak', True),))


def _ascend(ctx: Ctx, c, dx, dz, on_ladder) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    nx, nz = x + dx, z + dz
    step = (nx, y, nz)
    ks = g.at(*step)
    if on_ladder and g.at(x, y - 1, z) == CLIMB:
        return None
    pc, place, against = 0.0, None, None
    if ks == TALL:
        return None                                  # (a fence or a wall: too high to jump onto)
    if not _on(ks) or ks == LOW:
        if ks == LOW:
            return None                              # (a slab in the step's cell: walked onto, a traverse)
        pc = place_ticks(ctx, step)
        if pc >= INF:
            return None
        against = against_of(g, step, not_toward=(x, y, z))
        if against is None:
            return None
        place = step
    if g.at(x, y + 3, z) not in PASS and g.hardness(x, y + 3, z) & 128 and not _through(g.at(x, y + 2, z)):
        return None                                  # (sand over the head would fall on it)
    hs = _feet(ctx, c)
    dest = (nx, y + 1, nz)
    kd = g.at(*dest)
    if _bad(kd) or _bad(g.at(nx, y + 2, nz)):
        return None
    hd = y + 1.5 if kd == LOW else y + 1.0
    rise = hd - hs
    if rise > 1.25:
        return None
    walk = WALK if rise <= 0.6 else max(JUMP_ONE, WALK) + JUMP_PENALTY
    cells = ((x, y + 2, z), (nx, y + 2, nz), dest) if kd != LOW else ((x, y + 2, z), (nx, y + 2, nz))
    total = walk + pc
    for i, cell in enumerate(cells):
        t = mine_ticks(ctx, *cell, falling=i == 1)
        if t >= INF:
            return None
        total += t
    if kd == LOW and not _through(g.at(nx, y + 3, nz)):
        return None
    return Move('ascend', (x, y, z), dest, total, _breaks(ctx, cells), place, against)


def _descend(ctx: Ctx, c, dx, dz, on_ladder) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    if on_ladder and g.at(x, y - 1, z) == CLIMB:
        return None
    nx, nz = x + dx, z + dz
    if g.at(nx, y - 1, nz) == WATER and g.at(nx, y, nz) != WATER:     # (into water a step down: at its top)
        cells = ((nx, y + 1, nz), (nx, y, nz))
        t = sum(mine_ticks(ctx, *cell, falling=i == 0) for i, cell in enumerate(cells))
        if t >= INF or any(_bad(g.at(*cell)) for cell in cells):
            return None
        return Move('descend', (x, y, z), (nx, y - 1, nz), WALK_OFF + FALL_N[1] + t, _breaks(ctx, cells))
    if g.at(nx, y - 1, nz) == LOW:                   # (a slab a step down: walked down onto, in its cell)
        cells = ((nx, y + 1, nz), (nx, y, nz))
        t = sum(mine_ticks(ctx, *cell, falling=i == 0) for i, cell in enumerate(cells))
        if t >= INF or any(_bad(g.at(*cell)) for cell in cells):
            return None
        return Move('descend', (x, y, z), (nx, y - 1, nz), WALK + t, _breaks(ctx, cells))
    cells = ((nx, y + 1, nz), (nx, y, nz), (nx, y - 1, nz))
    mines = 0.0
    for i, cell in enumerate(cells):
        t = mine_ticks(ctx, *cell, falling=i == 0)
        if t >= INF:
            return None
        mines += t
    if any(_bad(g.at(*cell)) for cell in cells):
        return None
    below = g.at(nx, y - 2, nz)
    if _on(below) and below != LOW:
        if g.at(nx, y - 1, nz) == CLIMB:
            return None
        cost = mines + WALK_OFF + max(FALL_N[1], CENTER_AFTER_FALL)     # (a step down: stepped back up)
        return Move('descend', (x, y, z), (nx, y - 1, nz), cost, _breaks(ctx, cells))
    if below == LOW:                                 # (a slab: stood on, in its own cell)
        if _through(g.at(nx, y - 1, nz)):
            return Move('descend', (x, y, z), (nx, y - 2, nz), mines + WALK_OFF + FALL_N[1],
                        _breaks(ctx, cells))
        return None
    return _fall(ctx, (x, y, z), nx, nz, mines, cells)


def _fall(ctx: Ctx, src, nx, nz, mines, cells) -> Move | None:
    """Walking off the edge into column (nx, nz) and falling to what's below: ground within MAX_FALL, or water
    (any height), or a ladder or vines that catch the fall."""
    g = ctx.g
    x, y, z = src
    if mines and g.hardness(nx, y + 2, nz) & 128 and not _through(g.at(nx, y + 2, nz)):
        return None
    if not _through(g.at(nx, y - 2, nz)):
        return None
    if g.at(nx, y - 2, nz) == WATER:                 # (water two down: in at its top)
        return Move('fall', src, (nx, y - 2, nz), WALK_OFF + FALL_N[2] + mines, _breaks(ctx, cells),
                    info=(('height', 2),))
    so_far, start = 0.0, y
    for h in range(3, MAX_WATER_FALL + 2):
        ny = y - h
        k = g.at(nx, ny, nz)
        if k == UNKNOWN:
            return None
        free = h - (y - start)
        cost = WALK_OFF + FALL_N[min(free, 257)] + mines + so_far
        if k == WATER:
            # into water: down to its top, the head out there
            if _through(g.at(nx, ny + 1, nz)) and g.at(nx, ny + 1, nz) != WATER:
                return Move('fall', src, (nx, ny, nz), cost + (CLIMB_BACK * (h - 1) if ctx.back else 0.0),
                            _breaks(ctx, cells), info=(('height', h - 1),))
            continue
        if k == CLIMB and free <= 11:               # a ladder or vines: caught, and climbed down from there
            so_far += FALL_N[free - 1] + LADDER_DOWN
            start = ny
            continue
        if _through(k):
            continue
        if not _on(k) or k == LOW:
            return None
        if free <= MAX_FALL + 1:
            dest = (nx, ny + 1, nz)
            return Move('fall', src, dest, cost + (CLIMB_BACK * (y - dest[1] - 1) if ctx.back else 0.0),
                        _breaks(ctx, cells), info=(('height', y - dest[1]),))
        return None
    return None


def _diagonal(ctx: Ctx, c, dx, dz, on_ladder) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    if on_ladder:
        return None
    nx, nz = x + dx, z + dz
    kf, kh = g.at(nx, y, nz), g.at(nx, y + 1, nz)
    if not _through(kf) or not _through(kh) or _bad(kf) or _bad(kh) or kh == WATER:
        return None
    if not _on(g.at(nx, y - 1, nz)) and kf != WATER:
        return None
    if _bad(g.at(x, y - 1, nz)) or _bad(g.at(nx, y - 1, z)):
        return None
    if g.at(nx, y - 1, nz) == LOW or g.at(x, y - 1, z) == LOW:
        return None
    mult = WATER_WALK if WATER in (g.at(x, y, z), kf) else WALK
    a = (g.at(x, y, nz), g.at(x, y + 1, nz))
    b = (g.at(nx, y, z), g.at(nx, y + 1, z))
    a_open = all(_through(k) for k in a)
    b_open = all(_through(k) for k in b)
    if not a_open and not b_open:
        return None
    if (a_open and any(_bad(k) for k in a)) or (b_open and any(_bad(k) for k in b)):
        return None
    if a_open and b_open:
        if mult == WALK and ctx.can_sprint:
            mult *= SPRINT_MULT
    else:
        mult *= SQRT2 - 0.001                        # (round the corner: along the wall)
    return Move('diagonal', (x, y, z), (nx, y, nz), mult * SQRT2)


def _pillar(ctx: Ctx, c) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    here, below = g.at(x, y, z), g.at(x, y - 1, z)
    ladder = here == CLIMB
    top = (x, y + 2, z)
    if g.at(*top) == DOOR:
        return None
    if here == WATER:
        if g.at(*top) == WATER and g.at(x, y + 1, z) == WATER:
            return Move('pillar', (x, y, z), (x, y + 1, z), LADDER_UP, info=(('swim', True),))
        return None
    if not ladder:
        if below == CLIMB or below == LOW or (not placed and not _on(below)):
            return None
        if ctx.blocks <= 0 or (x, y, z) in ctx.no_place or here != AIR:
            return None
    hard = mine_ticks(ctx, *top, falling=True)
    if hard >= INF:
        return None
    if ladder:
        if g.at(*top) == CLIMB:
            hard = 0.0
        return Move('pillar', (x, y, z), (x, y + 1, z), LADDER_UP + hard * 5, _breaks(ctx, (top,)),
                    info=(('ladder', True),))
    cells = (top,)
    if PILLAR_HEADROOM:
        extra = mine_ticks(ctx, x, y + 3, z, falling=True)
        if extra >= INF:
            return None
        hard += extra
        cells = ((x, y + 3, z), top)
    return Move('pillar', (x, y, z), (x, y + 1, z), JUMP_ONE + PLACE_PENALTY + JUMP_PENALTY + hard + (
        0.1 if placed else 0.0), _breaks(ctx, cells), (x, y, z))


def _downward(ctx: Ctx, c) -> Move | None:
    x, y, z, placed = c
    g = ctx.g
    if placed or g.at(x, y, z) == WATER:
        return None
    if not _on(g.at(x, y - 2, z)) or g.at(x, y - 2, z) == LOW:
        return None
    down = g.at(x, y - 1, z)
    if down == CLIMB:
        return Move('downward', (x, y, z), (x, y - 1, z), LADDER_DOWN, info=(('ladder', True),))
    t = mine_ticks(ctx, x, y - 1, z)
    if t >= INF or t == 0:
        return None
    return Move('downward', (x, y, z), (x, y - 1, z), FALL_N[1] + t + (CLIMB_BACK if ctx.back else 0.0),
                ((x, y - 1, z),))


def _jump_ticks(dist: int) -> float:
    return {2: WALK * 2, 3: WALK * 3, 4: SPRINT * 4}[dist]


def _gap_hurts(g: Grid, x: int, y: int, z: int) -> bool:
    """A fall into the gap at (x, z), from a jump at height y, would hurt: no water to fall into, and no ground
    within 2 down (a jump's fall goes past 3 blocks from there)."""
    for k in range(1, MAX_WATER_FALL):
        cell = g.at(x, y - k, z)
        if cell == WATER:
            return False
        if _bad(cell):
            return True                                  # (lava and the like)
        if _on(cell) or cell == UNKNOWN:
            return k >= 3
    return True


def _parkour(ctx: Ctx, c, dx, dz) -> Move | None:
    """A jump over a gap, straight on: over 1 or 2 walking, over 3 sprinting; up a block over 1 or 2 sprinting.
    The landing's far side mustn't be lava and the like (the jump carries on a little past it)."""
    x, y, z, placed = c
    g = ctx.g
    ax, az = x + dx, z + dz
    if not _open_air(g.at(ax, y, az)):
        return None
    under = g.at(ax, y - 1, az)
    if _on(under):
        return None                                  # (ground: walked)
    if _bad(under):
        return None
    if not _open_air(g.at(ax, y + 1, az)) or not _open_air(g.at(ax, y + 2, az)) \
            or not _open_air(g.at(x, y + 2, z)):
        return None
    floor = g.at(x, y - 1, z)
    if floor in (CLIMB, LOW, PARTIAL) or g.at(x, y, z) in (WATER, CLIMB, LOW):
        return None
    if not _on(floor):
        return None
    # (over a gap a fall into would hurt: dearer, a way round taken where there is one: a jump over three fell
    # short into a ravine 35 down, dead, 2026-10-17)
    run = ctx.can_sprint
    hurts = GAP_HURTS if _gap_hurts(g, ax, y, az) else 0.0
    most = 4 if run else 3
    for i in range(2, most + 1):
        tx, tz = x + dx * i, z + dz * i
        if not _open_air(g.at(tx, y + 1, tz)) or not _open_air(g.at(tx, y + 2, tz)):
            return None
        into = g.at(tx, y, tz)
        if not _open_air(into):
            if i <= 3 and ctx.parkour_ascend and run and _on(into) and into != LOW \
                    and _overshoot_ok(g, tx + dx, y + 1, tz + dz):
                return Move('parkour', (x, y, z), (tx, y + 1, tz), i * SPRINT + JUMP_PENALTY + hurts,
                            info=(('dist', i), ('ascend', True), ('sprint', True)))
            return None
        land = g.at(tx, y - 1, tz)
        if _on(land) and land != LOW:
            if _overshoot_ok(g, tx + dx, y, tz + dz):
                return Move('parkour', (x, y, z), (tx, y, tz), _jump_ticks(i) + JUMP_PENALTY + hurts,
                            info=(('dist', i), ('ascend', False), ('sprint', i >= 4)))
            return None
        if not _open_air(g.at(tx, y + 3, tz)):
            return None
    return None


# A jump any way, not only straight on (our addition: Baritone's parkour is along x or z only). The player:
# "I can even jump back to the block from the crafting table with one sprinting jump... It can even do it without
# a sprinting jump", 2026-10-16: two across and three along, down one, and the planner went the long way round
# through the ravine below.
LEAP_WALK = 4.317 / 20              # blocks a tick, walking...
LEAP_SPRINT = 5.612 / 20            # ...and at a run
LEAP_MARGIN = 0.85                  # of the flight reckoned, counted on (the run-up's speed, the key's delay)
LEAP_LATE = 0.1                     # past the block's edge the jump goes up at the latest (the feet hold to 0.3)
LEAP_IN = 0.15                      # into the landing block the body must come down at least
LEAP_EARLY = -0.2                   # the jump goes up this far before the block's middle at the earliest
LEAP_MAX = 4.6                      # blocks across, middle to middle, at most
LEAP_PENALTY = 6.0                  # ticks more than a straight jump (any way's the less sure: straight one first)
BODY_HALF, BODY_TALL = 0.3, 1.8


def _arc(v: float, sprint: bool):
    """A jump's (across, up) each tick from v blocks a tick, forward held (the game's, as moving.flight)."""
    x, y, vy = 0.0, 0.0, 0.42
    v += 0.2 if sprint else 0.0
    push = 0.026 if sprint else 0.02
    for _ in range(40):
        v += push
        x += v
        y += vy
        vy = (vy - 0.08) * 0.98
        v *= 0.91
        yield x, y, vy


def _flight_to(v: float, sprint: bool, rise: float) -> float:
    for x, y, vy in _arc(v, sprint):
        if vy < 0 and y <= rise:
            return x
    return x


def _body_cells(cx: float, cz: float, fy: float):
    """The cells the body is in (feet at fy, its middle at cx cz), from the start cell's corner."""
    for bx in range(math.floor(cx - BODY_HALF), math.floor(cx + BODY_HALF - 1e-6) + 1):
        for bz in range(math.floor(cz - BODY_HALF), math.floor(cz + BODY_HALF - 1e-6) + 1):
            for by in range(math.floor(fy + 1e-6), math.floor(fy + BODY_TALL - 1e-6) + 1):
                yield (bx, by, bz)


def _leap_one(dx: int, dz: int, dy: int, sprint: bool):
    """A jump to (dx, dy, dz) from a cell, walking or at a run, worked out once for every cell: (the cells the
    body goes through, from the start cell, the start's own left out; ticks; blocks across), or None when that
    way of jumping doesn't carry far enough. The flights the body's room is for: taken off at the latest, at full
    speed and just fast enough, and as early as it goes (too fast for it, the walk eases off: moving._leap)."""
    d = math.hypot(dx, dz)
    ux, uz = dx / d, dz / d
    edge = 0.5 / max(abs(ux), abs(uz))               # the start block's edge, along the line
    late = edge + LEAP_LATE
    need = d - edge + LEAP_IN - late                 # across from the latest take-off into the landing block
    top = LEAP_SPRINT if sprint else LEAP_WALK
    if _flight_to(top, sprint, float(dy)) * LEAP_MARGIN < need:
        return None
    slow = next((top * i / 10 for i in range(1, 11) if _flight_to(top * i / 10, sprint, float(dy)) >= need), top)
    fly = _flight_to(top, sprint, float(dy))
    cells = set()
    for t, v in {(late, top), (late, slow), (max(LEAP_EARLY, min(late, d - fly)), top)}:
        pts = [(i * 0.2, 0.0) for i in range(int(max(t, 0.0) / 0.2) + 1)]
        for across, up, vy in _arc(v, sprint):
            if vy < 0 and up <= dy:
                break
            pts.append((min(t + across, d), up))
        for s, up in pts:
            cells.update(_body_cells(0.5 + ux * s, 0.5 + uz * s, up))
    cells -= {(0, 0, 0), (0, 1, 0), (0, 2, 0)}
    return tuple(sorted(cells, key=lambda c: abs(c[0]) + abs(c[2]))), \
        d * (SPRINT if sprint else WALK) + JUMP_PENALTY + LEAP_PENALTY, round(d, 2)


def _leap_table():
    """{the cell the line goes into first: [(dx, dz, dy, walking or None, at a run or None, past the landing)]}."""
    out = {}
    for dx in range(-4, 5):
        for dz in range(-4, 5):
            d = math.hypot(dx, dz)
            if max(abs(dx), abs(dz)) <= 1 or d > LEAP_MAX:
                continue
            first = (round(dx / d * 0.75), round(dz / d * 0.75))
            for dy in (1, 0, -1):
                if (dx == 0 or dz == 0) and dy >= 0:
                    continue                         # (straight on, level or up: _parkour's)
                walk, run = _leap_one(dx, dz, dy, False), _leap_one(dx, dz, dy, True)
                if walk or run:
                    out.setdefault(first, []).append((dx, dz, dy, walk, run, (round(dx / d), round(dz / d))))
    return out


LEAP_TABLE = _leap_table()
_LEAP_OFFS: dict = {}                # (grid width, depth) -> _leap_offsets: LEAP_TABLE by places in the grid's bytes


def _leap_offsets(sx: int, sz: int) -> list:
    """LEAP_TABLE with each cell as its place in the bytes of a grid sx wide and sz deep, from the start cell's
    (worked out once a grid shape)."""
    def at(cx, cy, cz):
        return (cy * sz + cz) * sx + cx
    out = []
    for first, ways in LEAP_TABLE.items():
        fx, fz = first
        rows = []
        for dx, dz, dy, walk, run, past in ways:
            rows.append((dx, dz, dy, at(dx, dy - 1, dz), at(dx, dy, dz), at(dx, dy + 1, dz),
                         at(dx + past[0], dy, dz + past[1]), at(dx + past[0], dy + 1, dz + past[1]),
                         walk, run, walk and tuple(at(*c) for c in walk[0]), run and tuple(at(*c) for c in run[0])))
        out.append((first, at(fx, -1, fz), at(fx, 0, fz), at(fx, 1, fz), at(fx, 2, fz), rows))
    return out


def _leaps(ctx: Ctx, c):
    """Jumps any way over a gap, onto a block up one, level or down one (never more: the fall from the jump's
    top would hurt), walking where a walking jump carries far enough, else at a run. Each is reckoned with the
    game's own jump (LEAP_MARGIN of it counted on), the whole flight's room checked for the body (worked out
    once: LEAP_TABLE). The straight ones level or up are _parkour's."""
    x, y, z, placed = c
    g = ctx.g
    floor = g.at(x, y - 1, z)
    if placed or not _on(floor) or floor in (CLIMB, LOW, PARTIAL) or g.at(x, y, z) != AIR \
            or not _open_air(g.at(x, y + 1, z)) or not _open_air(g.at(x, y + 2, z)):
        return
    sx, sy, sz = g.size
    lx, ly, lz = x - g.min[0], y - g.min[1], z - g.min[2]
    if 5 <= lx < sx - 5 and 2 <= ly < sy - 6 and 5 <= lz < sz - 5:
        # (the whole reach inside the grid: each cell looked up by its place in the grid's bytes; a planner
        # step 1.6 times as long with these jumps, not 7, 2026-10-16)
        table = _LEAP_OFFS.get((sx, sz))
        if table is None:
            table = _LEAP_OFFS[(sx, sz)] = _leap_offsets(sx, sz)
        data, b = g.data, (ly * sz + lz) * sx + lx
        for first, under, f0, f1, f2, rows in table:      # (into a gap first: else it's walked)
            k = data[b + under]
            if k in FLOOR or k == DANGER or data[b + f0] != AIR or data[b + f1] != AIR or data[b + f2] != AIR:
                continue
            hurts = GAP_HURTS if _gap_hurts(g, x + first[0], y, z + first[1]) else 0.0
            for dx, dz, dy, land, feet, head, p0, p1, walk, run, wo, ro in rows:
                k = data[b + land]
                if k not in FLOOR or k in (LOW, TALL) or data[b + feet] != AIR or data[b + head] != AIR:
                    continue
                way, offs = (walk, wo) if walk else (run, ro) if ctx.can_sprint else (None, None)
                if way is None or any(data[b + o] != AIR for o in offs) \
                        or data[b + p0] == DANGER or data[b + p1] == DANGER:
                    continue
                yield Move('leap', (x, y, z), (x + dx, y + dy, z + dz), way[1] + hurts,
                           info=(('dist', way[2]), ('rise', dy), ('sprint', way is run)))
        return
    for first, ways in LEAP_TABLE.items():
        fx, fz = x + first[0], z + first[1]
        under = g.at(fx, y - 1, fz)
        if under in FLOOR or under == DANGER or g.at(fx, y, fz) != AIR or g.at(fx, y + 1, fz) != AIR \
                or g.at(fx, y + 2, fz) != AIR:
            continue
        for dx, dz, dy, walk, run, past in ways:
            tx, ty, tz = x + dx, y + dy, z + dz
            land = g.at(tx, ty - 1, tz)
            if not _on(land) or land in (LOW, TALL) or not _open_air(g.at(tx, ty, tz)) \
                    or not _open_air(g.at(tx, ty + 1, tz)):
                continue
            way = walk or (run if ctx.can_sprint else None)
            if way is None or not all(_open_air(g.at(x + cx, y + cy, z + cz)) for cx, cy, cz in way[0]) \
                    or not _overshoot_ok(g, tx + past[0], ty, tz + past[1]):
                continue
            hurts = GAP_HURTS if _gap_hurts(g, fx, y, fz) else 0.0
            yield Move('leap', (x, y, z), (tx, ty, tz), way[1] + hurts,
                       info=(('dist', way[2]), ('rise', dy), ('sprint', way is run)))


def _overshoot_ok(g: Grid, x: int, y: int, z: int) -> bool:
    """Past the landing (walked into after it anyway): nothing that hurts."""
    return not _bad(g.at(x, y, z)) and not _bad(g.at(x, y + 1, z))


# ---- the search ----

def _h_block(c, goal, kind: str) -> float:
    """Baritone's estimate: across, a mix of straight and diagonal at a sprint's ticks; up, a jump a block;
    down, half a two-block fall a block."""
    ax, az = abs(c[0] - goal[0]), abs(c[2] - goal[2])
    dy = c[1] - goal[1]
    if kind == 'level':                              # (any spot across: only the height counts)
        ax = az = 0
    reach = IN_REACH if kind in ('mine', 'place') else float(kind[7:]) if kind.startswith('within:') else 0
    if reach:                                        # (got to from within reach of it, not at it)
        ax, az = max(0, ax - reach), max(0, az - reach)
        dy = max(0, dy - reach) if dy > 0 else min(0, dy + reach)
    straight, diag = abs(ax - az), min(ax, az)
    h = (diag * SQRT2 + straight) * HEURISTIC
    if kind == 'column':
        return h
    if dy > 0:
        h += FALL_N[2] / 2 * dy
    elif dy < 0:
        h += -dy * JUMP_ONE
    return h


def find(g: Grid, start, goal, budget_s: float = 1.0, max_nodes: int = 200_000, can_break: bool = False,
         blocks: int = 0, kind: str = 'near', box=None, no_place=frozenset(), sky=None, parkour: bool = True,
         ground=None, back: bool = False, keep=frozenset(), foes=None, goals=None, can_sprint: bool = True,
         jump_extra: float = 0.0, no_jump=frozenset()):
    """A route (blocks.Path, with .moves) from the stand nearest start to what counts as there: goal (a cell)
    by kind (blocks._there), or any of goals (several cells: the cheapest reached is taken, path.goal says
    which), or inside box, at the open sky, on the ground. None: nowhere to stand near the start. foes: cell ->
    a cost multiplier for moves into it (round hostile creatures). jump_extra: ticks more for each jump over a
    gap (FEWEST_JUMPS: the way with the fewest). Out of time or out of the box: the best part of the way
    (path.reaches_goal False, path.timed_out True)."""
    sx, sy, sz = start
    if POWDER in (g.at(sx, sy, sz), g.at(sx, sy + 1, sz)) and g.at(sx, sy - 1, sz) in FLOOR | {POWDER}:
        g = _opened(g, [(sx, sy, sz), (sx, sy + 1, sz)])
    s0 = _nearest_stand(g, start)
    if s0 is None:
        return None
    targets = [tuple(t) for t in (goals or [goal])]
    keep_cells = frozenset(targets) if kind in ('mine', 'block', 'place') else frozenset()
    g.keep = keep_cells | frozenset(keep)
    ctx = Ctx(g, can_break and g.hard is not None, blocks, frozenset(no_place), parkour, True, can_sprint, back,
              foes, no_jump=frozenset(no_jump))
    # (in the ground, every side shut: the way there's mostly dug, and the estimate weighted more)
    buried = kind == 'mine' and all(all(g.at(t[0] + o[0], t[1] + o[1], t[2] + o[2]) not in PASS for o in SIDES)
                                    for t in targets)
    weight = H_BURIED if buried else H_WEIGHT
    # (a long way up: weighted more, the estimate of a block climbed far under what climbing costs. Out of a ravine
    # 38 up, each plan searched 3 s and came back with 1 to 6 blocks of way, again and again, 2026-10-17)
    if kind != 'column' and min(t[1] for t in targets) - start[1] >= CLIMB_FAR:
        weight = max(weight, H_CLIMB)

    def h(c):
        return weight * min(_h_block(c, t, kind) for t in targets)

    def there(c):
        """(what's got to from c, what more it takes there: ticks), or None. To mine one: the block in the
        way of the line to it, broken first, counts (sight_cost)."""
        if kind == 'mine':
            best = None
            for t in targets:
                extra = sight_cost(g, c, t)
                if extra is not None and (best is None or extra < best[1]):
                    best = (t, extra)
            return best
        for t in targets:
            if _there(g, c, t, kind):
                return (t, 0.0)
        if (box is not None and _inside(c, box)) or (sky is not None and _open_sky(c, sky)) \
                or (ground is not None and _on_ground(g, c, ground)):
            return (targets[0], 0.0)
        return None

    s = (*s0, 0)
    h0 = h(s)
    open_ = [(h0, 0.0, 0, s)]
    came: dict = {s: None}
    cost = {s: 0.0}
    best = [(h0, s)] * len(COEFFICIENTS)            # per weight: the node with the least h + g / weight
    t0 = time.perf_counter()
    expanded, seq = 0, 0
    timed_out, end, reached = False, None, None
    while open_:
        _f, c_cost, _n, c = heapq.heappop(open_)
        if c[0] == 'there':                           # (a way's end, with what more it takes there: the cheapest)
            end, reached = c[1], c[2]
            break
        if c_cost > cost.get(c, math.inf):
            continue
        expanded += 1
        hc = h(c)
        for i, k in enumerate(COEFFICIENTS):
            v = hc + c_cost / k
            if v < best[i][0]:
                best[i] = (v, c)
        got = there(c)
        if got is not None:
            if got[1] <= 0.0:
                end, reached = c, got[0]
                break
            seq += 1                                  # (there, with a block to break first: weighed as a way on)
            heapq.heappush(open_, (c_cost + got[1], c_cost + got[1], seq, ('there', c, got[0])))
        if expanded >= max_nodes or (expanded % 256 == 0 and time.perf_counter() - t0 > budget_s):
            timed_out = True
            break
        for m in moves(ctx, c):
            step = m.cost
            if foes:
                step *= foes.get(m.dest, 1.0)
            if jump_extra and m.kind in ('parkour', 'leap'):
                step += jump_extra
            nc = c_cost + step
            n = (*m.dest, 1 if m.place is not None and m.place[1] == m.dest[1] - 1 else 0)
            if nc < cost.get(n, math.inf):
                cost[n] = nc
                came[n] = (c, m)
                seq += 1
                heapq.heappush(open_, (nc + h(n), nc, seq, n))
    if end is None:
        # the best part of the way: the first weight whose best is far enough from the start
        end = best[0][1]
        for _v, c in best:
            if math.dist(c[:3], s[:3]) > MIN_PART:
                end = c
                break
        if not timed_out:
            timed_out = _at_edge(g, end)
    ms, c = [], end
    while came[c] is not None:
        prev, m = came[c]
        ms.append(m)
        c = prev
    ms.reverse()
    return _as_path(g, s, ms, end, reached, timed_out, expanded, cost.get(end, 0.0))


def _as_path(g: Grid, s, ms, end, reached, timed_out, expanded, total) -> Path:
    """The moves as the walk's Path (points, cells, actions as before; moves, the way itself)."""
    cells = [s] + [(*m.dest, 0) for m in ms]

    def point(c):
        x, y, z = c[:3]
        sh = stand_height(g, x, y, z)
        return (x + 0.5, sh if sh is not None else float(y), z + 0.5)

    pts = [point(c) for c in cells]
    actions, jumps = [], []
    for i, m in enumerate(ms):
        a, b = pts[i], pts[i + 1]
        for cell in m.breaks:
            actions.append(('break', a, cell))
        if m.place is not None:                      # (a pillar's block goes on the one under it)
            against = m.against or (m.place[0], m.place[1] - 1, m.place[2])
            actions.append(('place', a, m.place, against, 'pillar' if m.kind == 'pillar' else 'bridge'))
        if m.kind == 'parkour':                       # (as before: from, the landing's cell, gap, sprint, run-up)
            actions.append(('parkour', a, m.dest, m.get('dist') - 1, m.get('sprint'), None))
            jumps.append((a, b))
        elif m.kind == 'leap':
            jumps.append((a, b))
        elif m.kind == 'ascend' and b[1] - a[1] > 0.6:
            actions.append(('step', a, b))
            jumps.append((a, b))
    keep = {p for i, m in enumerate(ms) if m.breaks or m.place or m.kind != 'traverse' for p in (pts[i], pts[i + 1])}
    path = Path(_simplify(pts, jumps, keep), reached is not None, timed_out, jumps, expanded)
    path.actions = actions
    path.cells = pts
    path.cost = total
    path.length = sum(math.dist(a, b) for a, b in zip(path.points, path.points[1:]))
    path.moves = ms
    path.goal = reached
    return path


def plan_route(g: Grid, start, goal, have: int, **kw):
    """A route with what's spare to build with (have blocks): (the path, a note), as blocks.plan_route: the
    cheapest way building with more than are spare isn't taken blind: the way that digs instead is planned too,
    and taken unless getting the blocks first (BLOCK_GET_TICKS each) and building is cheaper; then no path, and
    the note says how many it would take."""
    path = find(g, start, goal, blocks=have, **kw)
    note = {'found': None if path is None else ('there' if path.reaches_goal else
                                                'part' if path.timed_out else 'no way'),
            'expanded': path.expanded if path else 0, 'cells': len(path.cells) if path else 0,
            'blocks_to_build_with': have}
    placing = sum(1 for m in path.moves if m.place is not None) if path is not None else 0
    if path is None or placing <= have:
        return path, note
    try:
        dug = find(g, start, goal, blocks=0, **kw)
    except Exception:
        dug = None
    if dug is not None and (dug.reaches_goal or dug.timed_out) \
            and dug.cost <= path.cost + (placing - have) * BLOCK_GET_TICKS:
        note.update(found='there' if dug.reaches_goal else 'part', dug_instead_of_building=placing,
                    expanded=dug.expanded, cells=len(dug.cells))
        return dug, note
    note.update(found='needs blocks', blocks_it_would_take=placing)
    return None, note


# ---- planning in a process of its own ----
# (the search in this process, on a thread, held Python's lock: the frame loop ran at 115 ms a frame while a long
# climb was planned again and again, the walk sluggish, 2026-10-17. In a process of its own it never holds the
# frame loop up.)
WORKER = False                      # the app turns it on (tests plan here)
_POOL = None


def use_worker(on: bool = True) -> None:
    global WORKER
    WORKER = on


def _pool():
    global _POOL
    if _POOL is None:
        from concurrent.futures import ProcessPoolExecutor
        _POOL = ProcessPoolExecutor(max_workers=1)
    return _POOL


def run(fn: str, *a, **kw):
    """find or plan_route (by name), in the worker process when WORKER is on, else here; here too if the worker
    fails (then planned as before)."""
    f = {'find': find, 'plan_route': plan_route}[fn]
    if not WORKER:
        return f(*a, **kw)
    try:
        return _pool().submit(f, *a, **kw).result(timeout=float(kw.get('budget_s', 1.0)) * 4 + 10.0)
    except Exception:
        return f(*a, **kw)
