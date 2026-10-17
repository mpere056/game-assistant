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
  station_near(item) whether that station is near (a crafting table for recipes bigger than the hand's grid; a
                     furnace for what's cooked)
  fuels()            {item: ticks it burns} (what cooking takes; optional)

A book that knows the world costs in seconds from where the player is (docs/AGENT.md), with:
  gather_cost(source, n)  getting n from the nearest of that source that can be got at (the way there, the
                          mining): seconds, or None (none to be got at from here)
  station_cost(item)      getting to a station of that kind that's there and can be used: seconds, or None
Then a station already there is weighed against one made and put down here (14 down a hole with 5 planks held,
a new crafting table there beat climbing back to the old one, which the plan made at the start never saw,
2026-10-15); without them, the old reckoning (a count, the distance as a tie-break).

The plan is a list of steps: ('gather', item, how many, Source), ('craft', Recipe, times), ('station', item)
(place the station), ('smelt', Recipe, times, fuel item, how many of it) (cook in the station: iron ore's raw
iron into ingots). Tools are kept, not used up; everything else a craft or a smelt takes is, fuel too.
("Get me an iron pickaxe" had no plan: the ingots are smelted, and only crafting was known, 2026-10-13.)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Protocol

MAX_DEPTH = 10
PLAN_S = 4.0            # seconds a plan may take to work out at most (a bed's every wool and dye: minutes)
GATHER_COST = 3.0       # per item gathered, plus the distance to the nearest source / 10
CRAFT_COST = 1.0        # per craft
SMELT_COST = 2.0        # per item cooked (it takes a while), on top of the craft
CRAFT_S = 3.0           # seconds a craft takes (the screen opened, the grid filled, the result taken)
PLACE_S = 2.0           # ...putting a station down
MIN_FUEL = 200          # ticks: fuels that burn less than this (a stick, a sapling) aren't planned with


@dataclass(frozen=True)
class Recipe:
    id: str
    result: str
    count: int
    cells: tuple            # each cell: a tuple of the items that fit ('' cells: () ), row by row
    w: int = 1
    h: int = 1
    shapeless: bool = False
    station: str | None = None   # cooked in this (a furnace), not crafted on a grid
    time: int = 0                # ticks to cook one

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
    block: str                # what gives it: the block, or the creature (on 'creature')
    drops: tuple
    tool: str | None = None   # the least tool it needs (None: by hand); used on it, the item that's used
    on: str = 'block'         # 'block' or 'creature'
    how: str = 'mine'         # 'mine' (a block broken), 'kill' (a creature's drops), 'use' (the tool used on it,
                              # right-clicked: a sheep shorn, a berry bush picked)
    per: float = 1.0          # how many each one gives, on average
    variant: tuple = ()       # which kind of it: ((property, value), ...) (a white sheep, not shorn)

    @property
    def creature(self) -> bool:
        return self.on == 'creature'


class Book(Protocol):
    hand_grid: int
    station: str                                  # the item that is the crafting station (the 3x3 grid)
    def recipes(self, item: str) -> list[Recipe]: ...
    def sources(self, item: str) -> list[Source]: ...
    def tool_ok(self, need: str, have: dict) -> bool: ...
    def nearby(self, blocks: tuple) -> float | None: ...
    def station_near(self, item: str) -> bool: ...


class CantMake(Exception):
    pass


def plan(book: Book, item: str, count: int, have: dict) -> list[tuple]:
    """The steps to end up holding `count` of `item` (counting what's held). CantMake: no way known."""
    return plan_cost(book, item, count, have)[1]


def plan_cost(book: Book, item: str, count: int, have: dict, deadline: float | None = None) -> tuple[float, list]:
    """(what the cheapest plan costs, its steps): seconds with a book that knows the world, else a count. Worked
    out in PLAN_S at most (or by deadline, time.monotonic()): what isn't found by then isn't a way."""
    prefetch = getattr(book, 'prefetch', None)
    if prefetch is not None:                           # (where everything it may need is: asked once)
        prefetch(item)
    st = _State(dict(have), {})
    st.deadline = deadline if deadline is not None else time.monotonic() + PLAN_S
    cost = _need(book, st, item, count, 0, frozenset(), keep=True)
    if cost is None:
        if time.monotonic() > st.deadline:
            raise CantMake(f'working out how to get {_name(item)} took too long')
        raise CantMake(st.why or f"I don't know a way to get {item}")
    return cost, _late_stations(book, _merge(book, st.steps, have), have)


def step_cost(book, step) -> float:
    """About what one step costs (seconds with a book that knows the world), for a plan's milestones."""
    if step[0] == 'gather':
        got = _gather_cost(book, step[3], step[2])
        return got if got is not None else 0.0
    if step[0] == 'craft':
        return _craft_cost(book)
    if step[0] == 'smelt':
        return _smelt_cost(book, step[1], step[2])
    if step[0] == 'station':
        return _place_cost(book)
    return 0.0


def _seconds(book) -> bool:
    return getattr(book, 'gather_cost', None) is not None


def _gather_cost(book, src, k: int) -> float | None:
    if _seconds(book):
        return book.gather_cost(src, k)
    d = book.nearby((src.block,))
    return None if d is None else GATHER_COST * k + d / 10.0


def _craft_cost(book) -> float:
    return CRAFT_S if _seconds(book) else CRAFT_COST


def _place_cost(book) -> float:
    return PLACE_S if _seconds(book) else 0.0


def _smelt_cost(book, rec, times: int) -> float:
    return rec.time * times / 20.0 if _seconds(book) else SMELT_COST * times


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


def _late_stations(book, steps: list, have: dict) -> list:
    """Each station put down just before it's first used, where the work is (a furnace put down by the crafting
    table before the iron was dug, 23 blocks below: climbed back up to it to smelt, 2026-10-16)."""
    out = list(steps)
    for item in [s[1] for s in steps if s[0] == 'station']:
        i = next(k for k, s in enumerate(out) if s[0] == 'station' and s[1] == item)
        j = next((k for k in range(i + 1, len(out)) if _uses(book, out[k], item)), None)
        if j is None or j == i + 1:
            continue
        trial = out[:i] + out[i + 1:j] + [out[i]] + out[j:]
        if _replays(book, trial, have):
            out = trial
    return out


def _uses(book, step, station: str) -> bool:
    if step[0] == 'smelt':
        return step[1].station == station
    if step[0] == 'craft':
        rec = step[1]
        return (rec.station or (book.station if rec.grid > book.hand_grid else None)) == station
    return False


def plan_all(book, need: dict, have: dict, deadline: float | None = None) -> list:
    """The steps to end up holding need ({item: how many}) all at once (a building's blocks of several kinds):
    each planned in turn from what the ones before leave, never using up what they got."""
    inv, steps = dict(have), []
    kept: dict = {}
    for item, n in need.items():
        if n <= 0:
            continue
        free = {k: v - kept.get(k, 0) for k, v in inv.items()}
        got = plan(book, item, n, free) if deadline is None else plan_cost(book, item, n, free, deadline)[1]
        after = _after(book, got, free)
        if after is None:
            raise CantMake(f"I can't work out how to get {_name(item)} as well")
        steps += got
        inv = {k: v + kept.get(k, 0) for k, v in after.items()}
        kept[item] = kept.get(item, 0) + n
    return steps


def _replays(book, steps: list, have: dict) -> bool:
    return _after(book, steps, have) is not None


def _after(book, steps: list, have: dict) -> dict | None:
    """What's held after the steps (None: one of them can't be done with what's there by then)."""
    inv = dict(have)
    for s in steps:
        if s[0] == 'gather':
            if s[3].tool and not book.tool_ok(s[3].tool, inv):
                return None
            inv[s[1]] = inv.get(s[1], 0) + s[2]
        elif s[0] == 'station':
            inv[s[1]] = inv.get(s[1], 0) - 1
            if inv[s[1]] < 0:
                return None
        elif s[0] in ('craft', 'smelt'):
            rec, times = s[1], s[2]
            if s[0] == 'smelt':
                inv[s[3]] = inv.get(s[3], 0) - s[4]
                if inv[s[3]] < 0:
                    return None
            for fits, cells in rec.needs():
                n = cells * times
                for it in fits:
                    take = min(n, max(0, inv.get(it, 0)))
                    inv[it] = inv.get(it, 0) - take
                    n -= take
                if n > 0:
                    return None
            inv[rec.result] = inv.get(rec.result, 0) + times * rec.count
    return inv


@dataclass
class _State:
    have: dict
    stations: dict                 # station item -> what using one that's there costs (None: none; 0: put down
                                   # by the plan, or got to already); asked once each
    steps: list = None
    why: str = ''

    def __post_init__(self):
        self.steps = self.steps or []

    deadline: float = math.inf
    unit: dict = None              # item -> about what one more costs (the guide), shared by every copy
    gathers: dict = None           # (source, how many) -> what gathering them costs, asked once, shared

    def copy(self) -> '_State':
        return _State(dict(self.have), dict(self.stations), list(self.steps), self.why, self.deadline, self.unit,
                      self.gathers)


def _gather(book, st: _State, src, k: int) -> float | None:
    """_gather_cost, asked once a plan (the world isn't asked again for each way tried)."""
    if st.gathers is None:
        st.gathers = {}
    key = (src, k)
    if key not in st.gathers:
        st.gathers[key] = _gather_cost(book, src, k)
    return st.gathers[key]


def _unit(book, st: _State, item: str) -> float:
    """About what one more of item costs from here, the cheapest way, each ingredient at its own cheapest (what's
    held: nothing): only a guide to which ways are worked out in full (all of them, for a bed's every wool and dye
    and what makes them: half a million tried in 20 s, 2026-10-16). math.inf: no way known."""
    if st.unit is None:
        st.unit = {}
    if item not in st.unit:
        _guide(book, st, item)
    return 0.0 if st.have.get(item, 0) > 0 else st.unit.get(item, math.inf)


def _guide(book, st: _State, root: str) -> None:
    """The guide for root and all it may take (its recipes' ingredients, tools, stations, all the way down):
    each item's cost from the others', again till none gets cheaper (the recipes go round in circles: a bed from a
    dyed bed, dye from flowers, wool from dyed wool; a cost worked out down one path, cut where it came round,
    said "no way" for white wool, 2026-10-16)."""
    items, todo, seen = [], [root], set()
    while todo and len(seen) < GUIDE_ITEMS:
        it = todo.pop()
        if it in seen or it in st.unit:
            continue
        seen.add(it)
        items.append(it)
        for src in book.sources(it):
            if src.tool:
                todo.append(src.tool)
        for rec in book.recipes(it):
            need = rec.station or (book.station if rec.grid > book.hand_grid else None)
            if need:
                todo.append(need)
            todo.extend(f for fits, _n in rec.needs() for f in fits)
    unit = {it: math.inf for it in items}

    def look(x):
        if st.have.get(x, 0) > 0:
            return 0.0
        return unit[x] if x in unit else st.unit.get(x, math.inf)
    for _round in range(GUIDE_ROUNDS):
        changed = False
        for it in items:
            best = unit[it]
            makeable = bool(book.recipes(it))
            for src in book.sources(it):
                if not (makeable and src.block == it):
                    best = min(best, _source_guess(book, st, src, 1, look))
            for rec in book.recipes(it):
                best = min(best, _recipe_guess(book, st, rec, 1, look) / max(1, rec.count))
            if best < unit[it] - 1e-9:
                unit[it], changed = best, True
        if not changed:
            break
    st.unit.update(unit)


def _source_guess(book, st: _State, src, k: int, look) -> float:
    g = _gather(book, st, src, k)
    if g is None:
        return math.inf
    if src.tool and not book.tool_ok(src.tool, st.have):
        g += look(src.tool)
    return g


def _recipe_guess(book, st: _State, rec, k: int, look) -> float:
    times = math.ceil(k / max(1, rec.count))
    c = _craft_cost(book) if not rec.station else _smelt_cost(book, rec, times)
    need = rec.station or (book.station if rec.grid > book.hand_grid else None)
    if need:
        use = _station_use(book, st, need)
        c += use if use is not None else look(need) + _place_cost(book)
    for fits, cells in rec.needs():
        c += cells * times * min((0.0 if st.have.get(f, 0) >= cells * times else look(f)) for f in fits)
        if c == math.inf:
            break
    return c


def _station_use(book, st: _State, item: str) -> float | None:
    """What getting to a station of that kind that's there costs (None: none can be used)."""
    if item not in st.stations:
        sc = getattr(book, 'station_cost', None)
        if sc is not None:
            st.stations[item] = sc(item)
        else:
            try:
                near = bool(book.station_near(item))
            except TypeError:                              # (a book with the crafting table only)
                near = item == book.station and bool(book.station_near())
            st.stations[item] = 0.0 if near else None
    return st.stations[item]


def _fuel(book, st: _State, ticks: int, depth: int, seen: frozenset):
    """Fuel for `ticks` of cooking: (item, how many, cost); st updated with it. What's held first (one kind,
    enough of it); else the cheapest of what's held in part, what the land gives near (logs, coal) and what's
    made from what's held (planks from logs). None: no way. (By what burns longest alone, it only ever tried
    lava, coal blocks and boats.)"""
    burn = {k: v for k, v in (getattr(book, 'fuels', lambda: {})() or {}).items()
            if isinstance(v, (int, float)) and v >= MIN_FUEL}
    if not burn:
        return None
    for it in sorted(burn, key=lambda i: -burn[i]):
        n = math.ceil(ticks / burn[it])
        if st.have.get(it, 0) >= n:
            st.have[it] -= n
            return it, n, 0.0
    held = {h for h, n in st.have.items() if n > 0}
    cands = [it for it in burn if it in held or book.sources(it)
             or any(all(any(x in held for x in fits) for fits, _n in r.needs()) for r in book.recipes(it))]
    best = None
    # (the cheapest for what it takes, by the guide: by what burns longest, a block of coal, nine coal, was taken
    # over the one coal enough for two ingots, 2026-10-16)
    for it in sorted(cands, key=lambda i: (i not in held, _unit(book, st, i) * math.ceil(ticks / burn[i]),
                                           -burn[i]))[:MAX_TRIES]:
        n = math.ceil(ticks / burn[it])
        trial = st.copy()
        c = _need(book, trial, it, n, depth, seen)
        if c is not None and (best is None or c < best[0]):
            best = (c, trial, it, n)
    if best is None:
        return None
    c, trial, it, n = best
    st.have, st.stations, st.steps = trial.have, trial.stations, trial.steps
    return it, n, c


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
    if time.monotonic() > st.deadline:
        st.why = st.why or f'working out how to get {_name(item)} took too long'
        return None
    seen = seen | {item}
    best = None
    makeable = bool(book.recipes(item))
    # a thing that's made is made, not taken off what's built (the block is the thing itself, and it has a recipe:
    # a crafting table, planks, a furnace): "get a crafting table" mined the one the player had put down, then put
    # it back, 2026-10-13. What the land gives (logs, stone, ores) is gathered.
    srcs = [src for src in book.sources(item) if not (makeable and src.block == item)]
    # the ways worked out in full: the cheapest few by the guide (each ingredient at its cheapest), not all of them
    look = lambda x: _unit(book, st, x)  # noqa: E731
    ways = sorted([(_source_guess(book, st, src, k, look), 0, i) for i, src in enumerate(srcs)] +
                  [(_recipe_guess(book, st, rec, k, look), 1, i) for i, rec in enumerate(book.recipes(item))])
    ways = [w for w in ways if w[0] < math.inf][:WAYS]
    for src in [srcs[i] for _g, kind, i in ways if kind == 0]:
        g = _gather(book, st, src, k)
        if g is None:
            continue
        trial = st.copy()
        cost = 0.0
        if src.tool and not book.tool_ok(src.tool, trial.have):
            c = _need(book, trial, src.tool, 1, depth + 1, seen, keep=True)
            if c is None:
                st.why = st.why or f'I need a {_name(src.tool)} for {_name(item)}'
                continue
            cost += c
        cost += g
        trial.steps.append(('gather', item, k, src))
        trial.have[item] = trial.have.get(item, 0) + k
        if best is None or cost < best[0]:
            best = (cost, trial)
    for rec in [book.recipes(item)[i] for _g, kind, i in ways if kind == 1]:
        trial = st.copy()
        times = math.ceil(k / max(1, rec.count))
        cost = _craft_cost(book)
        ok = True
        need = rec.station or (book.station if rec.grid > book.hand_grid else None)
        if need:
            # one that's there (getting to it), or one made and put down here: the cheaper
            use = _station_use(book, trial, need)
            if use is None or use > 0.0:
                made = trial.copy()
                c_make = _need(book, made, need, 1, depth + 1, seen)
                if c_make is not None and (use is None or c_make + _place_cost(book) < use):
                    trial, use = made, c_make + _place_cost(book)
                    trial.steps.append(('station', need))
            if use is None:
                continue
            cost += use
            trial.stations[need] = 0.0                    # (there from now on)
        for fits, cells in rec.needs():
            c = _ingredient(book, trial, fits, cells * times, depth + 1, seen)
            if c is None:
                ok = False
                break
            cost += c
        if not ok:
            continue
        if rec.station:                                   # cooked: fuel for as long as it takes
            got = _fuel(book, trial, times * max(1, rec.time), depth + 1, seen)
            if got is None:
                st.why = st.why or f"I've nothing to burn in the {_name(rec.station)}"
                continue
            f_item, f_n, c = got
            cost += c + _smelt_cost(book, rec, times)
            trial.steps.append(('smelt', rec, times, f_item, f_n))
        else:
            trial.steps.append(('craft', rec, times))
        trial.have[item] = trial.have.get(item, 0) + times * rec.count
        if best is None or cost < best[0]:
            best = (cost, trial)
    if best is None:
        st.why = st.why or f"I can't find a way to get {_name(item)} near here"
        return None
    st.have, st.stations, st.steps = best[1].have, best[1].stations, best[1].steps
    return best[0]


def _ingredient(book, st: _State, fits: tuple, n: int, depth: int, seen: frozenset) -> float | None:
    """n of any one of the items that fit (what's held first; else the cheapest to get)."""
    for it in fits:
        if st.have.get(it, 0) >= n:
            st.have[it] -= n
            return 0.0
    best = None
    # the held ones first, then those made from something held ('oak' planks from oak logs), then the cheapest
    stems = {h.split(':')[-1].split('_')[0] for h, n in st.have.items() if n > 0}
    order = sorted(fits, key=lambda it: (-st.have.get(it, 0), it.split(':')[-1].split('_')[0] not in stems,
                                         _unit(book, st, it)))
    for it in order[:MAX_TRIES]:
        trial = st.copy()
        c = _need(book, trial, it, n, depth, seen)
        if c is not None and (best is None or c < best[0]):
            best = (c, trial)
    if best is None:
        return None
    st.have, st.stations, st.steps = best[1].have, best[1].stations, best[1].steps
    return best[0]


MAX_TRIES = 3           # of the items that fit an ingredient, this many are tried (the held ones first, then
                        # the cheapest by the guide)
GUIDE_ITEMS = 800       # items the guide looks through at most (all a plan may take)
GUIDE_ROUNDS = 12       # times round them at most (each cost from the others', till none gets cheaper)
WAYS = 2                # of the ways to get an item (sources, recipes), this many are worked out in full


def _name(item: str) -> str:
    return item.split(':')[-1].replace('_', ' ')
