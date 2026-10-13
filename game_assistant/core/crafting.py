"""Getting an item: what to gather and what to craft, in order (any game with recipes and gathering).

"Get me a stone pickaxe" with nothing in hand: logs (by hand), planks, a crafting table (placed), sticks, a
wooden pickaxe, then stone (with the wooden pickaxe), then the stone pickaxe. plan() works this out from the
game's own data, given by the adapter through a Book:

  recipes(item)      the recipes that make it (Recipe: the grid's cells, each the items that fit; how many it makes)
  sources(item)      the blocks (or other things in the world) that give it when broken, and the least tool each
                     needs (Source)
  tool_ok(need, have)  whether a held tool will do for one that needs `need` (a better one of the same kind does)
  nearby(blocks)     how far the nearest of these is (None: none known near): gathering is planned only from
                     what's near
  station_near()     whether a crafting station is near (for recipes bigger than the hand's grid)

The plan is a list of steps: ('gather', item, how many, Source), ('craft', Recipe, times), ('station', item)
(place the station). Tools are kept, not used up; everything else a craft takes is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

MAX_DEPTH = 10
GATHER_COST = 3.0       # per item gathered, plus the distance to the nearest source / 10
CRAFT_COST = 1.0        # per craft


@dataclass(frozen=True)
class Recipe:
    id: str
    result: str
    count: int
    cells: tuple            # each cell: a tuple of the items that fit ('' cells: () ), row by row
    w: int = 1
    h: int = 1
    shapeless: bool = False

    @property
    def grid(self) -> int:
        """The crafting grid it needs (2: the hand's, 3: a crafting table)."""
        if self.shapeless:
            return 2 if len([c for c in self.cells if c]) <= 4 else 3
        return max(self.w, self.h)

    def needs(self) -> list[tuple[tuple, int]]:
        """[(items that fit, how many cells)] per kind of ingredient."""
        out: dict[tuple, int] = {}
        for c in self.cells:
            if c:
                out[c] = out.get(c, 0) + 1
        return list(out.items())


@dataclass(frozen=True)
class Source:
    block: str
    drops: tuple
    tool: str | None = None   # the least tool it needs to drop anything (None: by hand)


class Book(Protocol):
    hand_grid: int
    station: str                                  # the item that is the crafting station
    def recipes(self, item: str) -> list[Recipe]: ...
    def sources(self, item: str) -> list[Source]: ...
    def tool_ok(self, need: str, have: dict) -> bool: ...
    def nearby(self, blocks: tuple) -> float | None: ...
    def station_near(self) -> bool: ...


class CantMake(Exception):
    pass


def plan(book: Book, item: str, count: int, have: dict) -> list[tuple]:
    """The steps to end up holding `count` of `item` (counting what's held). CantMake: no way known."""
    st = _State(dict(have), book.station_near())
    cost = _need(book, st, item, count, 0, frozenset(), keep=True)
    if cost is None:
        raise CantMake(st.why or f"I don't know a way to get {item}")
    return _merge(book, st.steps, have)


def _merge(book, steps: list, have: dict) -> list:
    """One trip for each kind of thing gathered, and one craft per recipe, where that still works out (the plan
    replayed: nothing used before it's there): logs once for the table and the pickaxe, not twice."""
    out = list(steps)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(out):
            for j in range(i + 1, len(out)):
                b = out[j]
                if a[0] != b[0] or a[0] not in ('gather', 'craft') or a[1] != b[1] \
                        or (a[0] == 'gather' and a[3] != b[3]):
                    continue
                merged = (a[0], a[1], a[2] + b[2]) + a[3:]
                trial = out[:i] + [merged] + out[i + 1:j] + out[j + 1:]
                if _replays(book, trial, have):
                    out, changed = trial, True
                    break
            if changed:
                break
    return out


def _replays(book, steps: list, have: dict) -> bool:
    inv = dict(have)
    for s in steps:
        if s[0] == 'gather':
            if s[3].tool and not book.tool_ok(s[3].tool, inv):
                return False
            inv[s[1]] = inv.get(s[1], 0) + s[2]
        elif s[0] == 'station':
            inv[s[1]] = inv.get(s[1], 0) - 1
            if inv[s[1]] < 0:
                return False
        elif s[0] == 'craft':
            rec, times = s[1], s[2]
            for fits, cells in rec.needs():
                n = cells * times
                for it in fits:
                    take = min(n, max(0, inv.get(it, 0)))
                    inv[it] = inv.get(it, 0) - take
                    n -= take
                if n > 0:
                    return False
            inv[rec.result] = inv.get(rec.result, 0) + times * rec.count
    return True


@dataclass
class _State:
    have: dict
    station: bool
    steps: list = None
    why: str = ''

    def __post_init__(self):
        self.steps = self.steps or []

    def copy(self) -> '_State':
        return _State(dict(self.have), self.station, list(self.steps), self.why)


def _need(book, st: _State, item: str, n: int, depth: int, seen: frozenset, keep: bool = False) -> float | None:
    """Make sure n of item are held; take them (unless keep: tools). Its cost, or None (st unchanged then)."""
    short = n - st.have.get(item, 0)
    cost = 0.0
    if short > 0:
        got = _produce(book, st, item, short, depth, seen)
        if got is None:
            return None
        cost = got
    if not keep:
        st.have[item] = st.have.get(item, 0) - n
    return cost


def _produce(book, st: _State, item: str, k: int, depth: int, seen: frozenset) -> float | None:
    """Gather or craft k more of item: the cheapest way; st updated with it. None: no way."""
    if depth > MAX_DEPTH or item in seen:
        return None
    seen = seen | {item}
    best = None
    for src in book.sources(item):
        d = book.nearby((src.block,))
        if d is None:
            continue
        trial = st.copy()
        cost = 0.0
        if src.tool and not book.tool_ok(src.tool, trial.have):
            c = _need(book, trial, src.tool, 1, depth + 1, seen, keep=True)
            if c is None:
                st.why = st.why or f'I need a {_name(src.tool)} for {_name(item)}'
                continue
            cost += c
        cost += GATHER_COST * k + d / 10.0
        trial.steps.append(('gather', item, k, src))
        trial.have[item] = trial.have.get(item, 0) + k
        if best is None or cost < best[0]:
            best = (cost, trial)
    for rec in book.recipes(item):
        trial = st.copy()
        times = math.ceil(k / max(1, rec.count))
        cost = CRAFT_COST
        ok = True
        if rec.grid > book.hand_grid and not trial.station:
            c = _need(book, trial, book.station, 1, depth + 1, seen)
            if c is None:
                continue
            cost += c
            trial.steps.append(('station', book.station))
            trial.station = True
        for fits, cells in rec.needs():
            c = _ingredient(book, trial, fits, cells * times, depth + 1, seen)
            if c is None:
                ok = False
                break
            cost += c
        if not ok:
            continue
        trial.steps.append(('craft', rec, times))
        trial.have[item] = trial.have.get(item, 0) + times * rec.count
        if best is None or cost < best[0]:
            best = (cost, trial)
    if best is None:
        st.why = st.why or f"I can't find a way to get {_name(item)} near here"
        return None
    st.have, st.station, st.steps = best[1].have, best[1].station, best[1].steps
    return best[0]


def _ingredient(book, st: _State, fits: tuple, n: int, depth: int, seen: frozenset) -> float | None:
    """n of any one of the items that fit (what's held first; else the cheapest to get)."""
    for it in fits:
        if st.have.get(it, 0) >= n:
            st.have[it] -= n
            return 0.0
    best = None
    # the held ones first, then those made from something held ('oak' planks from oak logs)
    stems = {h.split(':')[-1].split('_')[0] for h, n in st.have.items() if n > 0}
    order = sorted(fits, key=lambda it: (-st.have.get(it, 0), it.split(':')[-1].split('_')[0] not in stems))
    for it in order[:MAX_TRIES]:
        trial = st.copy()
        c = _need(book, trial, it, n, depth, seen)
        if c is not None and (best is None or c < best[0]):
            best = (c, trial)
    if best is None:
        return None
    st.have, st.station, st.steps = best[1].have, best[1].station, best[1].steps
    return best[0]


MAX_TRIES = 16          # of the items that fit an ingredient, this many are tried (the held ones first)


def _name(item: str) -> str:
    return item.split(':')[-1].replace('_', ' ')
