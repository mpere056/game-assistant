"""A small Minecraft-like block world for playing whole orders end to end (docs/AGENT.md stage 3).

The real planners run in it: the goal planner (core/goals.py), the crafting planner with a book that costs in
seconds from where the player is (core/crafting.py), the route planner and its "build or dig" choice
(games/minecraft/blocks.py), the world memory (core/memory.py). What the skills do in the game by keys and
the mouse is done here on the world's blocks and the inventory: moving follows the planned route (what it
breaks is broken and picked up, what it puts down is taken from the inventory) and takes its time; mining takes
the block and gives its drop (with the tool it needs); crafting and smelting need their station in reach;
putting a station down puts it beside the player. Each failure ends with the same Outcome and Reason as in the
game.
"""
from __future__ import annotations

import math
from collections import Counter

from game_assistant.core import skills as S
from game_assistant.core.crafting import CantMake, Recipe, Source, plan, step_cost
from game_assistant.core.goals import GoalRunner
from game_assistant.core.memory import WorldMemory
from game_assistant.games.minecraft import blocks as B

M = 'minecraft:'
AIR = M + 'air'
SOLID = {M + n for n in ('grass_block', 'dirt', 'stone', 'cobblestone', 'oak_log', 'oak_leaves', 'oak_planks',
                         'iron_ore', 'coal_ore', 'crafting_table', 'furnace', 'bedrock')}
HARD = {M + 'dirt': 4, M + 'grass_block': 4, M + 'stone': 6, M + 'cobblestone': 6, M + 'oak_log': 15,
        M + 'oak_leaves': 1, M + 'oak_planks': 10, M + 'iron_ore': 8, M + 'coal_ore': 8, M + 'crafting_table': 10,
        M + 'furnace': 10, M + 'bedrock': 127}             # (the bridge's units: 4 ticks)
DROPS = {M + 'grass_block': M + 'dirt', M + 'stone': M + 'cobblestone', M + 'iron_ore': M + 'raw_iron',
         M + 'coal_ore': M + 'coal', M + 'oak_leaves': None}
BUILD = [M + 'dirt', M + 'cobblestone', M + 'oak_planks', M + 'oak_log']   # what's built with, first first
WALK = 4.317                    # blocks a second
CLIMB_S = 1.0                   # seconds a block up, reckoned (a pillar block or a step dug), for estimates
DIG_S = 1.0                     # ...and a block in the way
STUCK_REACH = 6.0
LEGS = 4                        # parts of a long way planned one after another

P = (M + 'oak_planks',)
S_ = (M + 'stick',)
C = (M + 'cobblestone',)
I = (M + 'iron_ingot',)
_ = ()


def shaped(result, count, rows):
    return Recipe(f'r:{result}', result, count, tuple(c for row in rows for c in row), len(rows[0]), len(rows))


RECIPES = {
    M + 'oak_planks': [Recipe('r:planks', M + 'oak_planks', 4, ((M + 'oak_log',),), 1, 1)],
    M + 'stick': [shaped(M + 'stick', 4, [[P], [P]])],
    M + 'crafting_table': [shaped(M + 'crafting_table', 1, [[P, P], [P, P]])],
    M + 'wooden_pickaxe': [shaped(M + 'wooden_pickaxe', 1, [[P, P, P], [_, S_, _], [_, S_, _]])],
    M + 'stone_pickaxe': [shaped(M + 'stone_pickaxe', 1, [[C, C, C], [_, S_, _], [_, S_, _]])],
    M + 'furnace': [shaped(M + 'furnace', 1, [[C, C, C], [C, _, C], [C, C, C]])],
    M + 'iron_ingot': [Recipe('r:ingot', M + 'iron_ingot', 1, ((M + 'raw_iron',),), shapeless=True,
                              station=M + 'furnace', time=200)],
    M + 'iron_pickaxe': [shaped(M + 'iron_pickaxe', 1, [[I, I, I], [_, S_, _], [_, S_, _]])],
}
SOURCES = {
    M + 'oak_log': [Source(M + 'oak_log', (M + 'oak_log',))],
    M + 'cobblestone': [Source(M + 'stone', (M + 'cobblestone',), M + 'wooden_pickaxe')],
    M + 'dirt': [Source(M + 'dirt', (M + 'dirt',)), Source(M + 'grass_block', (M + 'dirt',))],
    M + 'raw_iron': [Source(M + 'iron_ore', (M + 'raw_iron',), M + 'stone_pickaxe')],
    M + 'coal': [Source(M + 'coal_ore', (M + 'coal',), M + 'wooden_pickaxe')],
}
FUELS = {M + 'oak_log': 300, M + 'oak_planks': 300, M + 'coal': 1600, M + 'stick': 100}
PICKS = ['wooden', 'stone', 'iron', 'diamond']


class SimWorld:
    def __init__(self, size=(40, 40, 40)):
        self.size = size
        self.blocks: dict = {}
        self.player = (0, 0, 0)          # the feet's cell
        self.inv: Counter = Counter()
        self.t = 0.0
        self.memory = WorldMemory(clock=lambda: self.t)
        self.reserve: dict = {}
        self.trace: list = []

    # ---- the world ----

    def at(self, c) -> str:
        return self.blocks.get(tuple(c), AIR)

    def put(self, c, block) -> None:
        if block == AIR:
            self.blocks.pop(tuple(c), None)
        else:
            self.blocks[tuple(c)] = block

    def fill(self, x0, x1, y0, y1, z0, z1, block) -> None:
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.put((x, y, z), block)

    def grid(self, lo, hi) -> B.Grid:
        sx, sy, sz = (hi[i] - lo[i] + 1 for i in range(3))
        data, hard = bytearray(sx * sy * sz), bytearray(sx * sy * sz)
        for y in range(sy):
            for z in range(sz):
                for x in range(sx):
                    b = self.at((lo[0] + x, lo[1] + y, lo[2] + z))
                    i = (y * sz + z) * sx + x
                    data[i] = B.SOLID if b in SOLID else B.AIR
                    hard[i] = HARD.get(b, 0)
        return B.Grid(tuple(lo), (sx, sy, sz), bytes(data), bytes(hard))

    def cells(self, ids) -> list:
        ids = set(ids)
        p = self.player
        return sorted((c for c, b in self.blocks.items() if b in ids), key=lambda c: math.dist(c, p))

    def spare(self) -> int:
        return sum(max(0, self.inv[b] - self.reserve.get(b, 0)) for b in BUILD)

    # ---- moving ----

    def route_to(self, target, kind='mine', back=False):
        s, t = self.player, tuple(target)
        lo = [min(s[i], t[i]) - (8 if i != 1 else 12) for i in range(3)]
        hi = [max(s[i], t[i]) + (8 if i != 1 else 12) for i in range(3)]
        lo[1], hi[1] = max(lo[1], 0), min(hi[1], self.size[1] + 10)
        return B.plan_route(self.grid(lo, hi), s, t, self.spare(), budget_s=3.0, can_break=True, kind=kind,
                            back=back)

    def walk(self, path) -> None:
        for a in path.actions:
            if a[0] == 'break':
                self.dig(a[2])
            elif a[0] == 'place':
                block = next(b for b in BUILD if self.inv[b] - self.reserve.get(b, 0) > 0)
                self.inv[block] -= 1
                self.put(a[2], block)
        end = path.cells[-1]
        self.player = (math.floor(end[0]), math.floor(end[1] + 0.01), math.floor(end[2]))
        self.t += path.cost / WALK

    def dig(self, c) -> None:
        b = self.at(c)
        drop = DROPS.get(b, b)
        if drop:
            self.inv[drop] += 1
        self.put(c, AIR)
        self.memory.dug_at(c)
        self.t += HARD.get(b, 4) * 4 / 20.0

    # ---- the skills (a plan's steps) ----

    def run(self, step) -> S.Outcome:
        out = getattr(self, '_' + step[0])(*step[1:])
        self.trace.append((round(self.t, 1), step[0], _what(step), out.brief()))
        return out

    def _reach(self, what: str, cells, kind='mine', back=False):
        """To the nearest of cells that can be got at (in reach, in sight): (the cell, None), or (None, the failed
        Outcome)."""
        last = None
        for c in [c for c in cells if not self.memory.is_unreachable(c, self.player)][:4]:
            for _leg in range(LEGS):                     # (a long way: along the part planned, then on)
                path, note = self.route_to(c, kind, back)
                if path is not None and path.reaches_goal:
                    self.walk(path)
                    return c, None
                if path is None or not path.timed_out or len(path.cells) < 2:
                    break
                self.walk(path)
            last = (c, note)
            self.memory.couldnt_reach(what, c, self.player, note.get('found') or '')
        if last is None:
            return None, S.failed(S.NO_TARGET, f"I can't find a {what}.", what=what)
        return None, S.failed(S.UNREACHABLE, f"I can't get to the {what}.", what=what, at=last[0],
                              frm=self.player, route=last[1])

    def _gather(self, item, k, src) -> S.Outcome:
        want = self.inv[item] + k
        while self.inv[item] < want:
            if src.tool and not _tool_ok(src.tool, self.inv):
                return S.failed(S.NEEDS_TOOL, f'I need a {src.tool}.', tool=src.tool, item=item)
            got, failed = self._reach(src.block, self.cells((src.block,)), back=True)
            if failed is not None:
                return failed
            self.clear_view(got)
            self.dig(got)
        return S.done()

    def clear_view(self, c) -> None:
        """What's in the way of the line of sight to c (a block the route planner counted as mined first): dug."""
        x, y, z = self.player
        e = (x + 0.5, y + B.EYE, z + 0.5)
        q = (c[0] + 0.5, c[1] + 0.5, c[2] + 0.5)
        n = max(2, int(math.dist(e, q) * 8))
        for i in range(1, n):
            cell = tuple(math.floor(e[j] + (q[j] - e[j]) * i / n) for j in range(3))
            if cell != tuple(c) and cell[1] >= y and self.at(cell) in SOLID and self.at(cell) != M + 'bedrock':
                self.dig(cell)

    def _grid_here(self, c):
        lo = [min(self.player[i], c[i]) - 6 for i in range(3)]
        hi = [max(self.player[i], c[i]) + 6 for i in range(3)]
        return self.grid(lo, hi)

    def _station(self, item) -> S.Outcome:
        if self.inv[item] < 1:
            return S.failed(S.MISSING, f"I don't have a {item}.", item=item, count=1)
        x, y, z = self.player
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            c = (x + dx, y, z + dz)
            if self.at(c) == AIR and self.at((c[0], y - 1, c[2])) in SOLID:
                self.inv[item] -= 1
                self.put(c, item)
                self.memory.put_down(item, c)
                self.t += 2.0
                return S.done()
        for dy in (1,):                                    # (walled in: dug into the wall beside, at the feet)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c = (x + dx, y, z + dz)
                if self.at(c) in SOLID and self.at(c) != M + 'bedrock':
                    self.dig(c)
                    self.inv[item] -= 1
                    self.put(c, item)
                    self.memory.put_down(item, c)
                    self.t += 2.0
                    return S.done()
        return S.failed(S.NO_SPOT, 'Nowhere to put it.', item=item)

    def _use_station(self, item):
        cells = [c for c in self.cells((item,))]
        if not cells:
            return S.failed(S.NO_TARGET, f"I can't find a {item}.", what=item)
        g = self._grid_here(cells[0])
        if B.sees(g, self.player, cells[0], reach=B.REACH):
            self.clear_view(cells[0])
            return None
        got, failed = self._reach(item, cells)
        if got is not None:
            self.clear_view(got)
        return failed

    def _craft(self, rec, times) -> S.Outcome:
        if rec.grid > 2:
            failed = self._use_station(M + 'crafting_table')
            if failed is not None:
                return failed
        if not self._take(rec, times):
            return S.failed(S.MISSING, f"I don't have what the {rec.result} takes.", item=rec.result)
        self.inv[rec.result] += rec.count * times
        self.t += 3.0
        return S.done()

    def _smelt(self, rec, times, fuel, n) -> S.Outcome:
        failed = self._use_station(rec.station)
        if failed is not None:
            return failed
        if self.inv[fuel] < n or not self._take(rec, times):
            return S.failed(S.MISSING, "I don't have what it takes.", item=rec.result)
        self.inv[fuel] -= n
        self.inv[rec.result] += rec.count * times
        self.t += rec.time * times / 20.0 + 3.0
        return S.done()

    def _take(self, rec, times) -> bool:
        inv = Counter(self.inv)
        for fits, cells in rec.needs():
            n = cells * times
            for it in fits:
                k = min(n, inv[it])
                inv[it] -= k
                n -= k
            if n > 0:
                return False
        self.inv = inv
        return True


class SimBook:
    """core/crafting.py's book for the test world: costs in seconds from where the player is."""
    hand_grid = 2
    station = M + 'crafting_table'

    def __init__(self, world: SimWorld, within: float | None = None):
        self.w, self.within = world, within

    def recipes(self, item):
        return RECIPES.get(item, [])

    def sources(self, item):
        return SOURCES.get(item, [])

    def fuels(self):
        return FUELS

    def tool_ok(self, need, have):
        return _tool_ok(need, have)

    def _cells(self, block):
        p = self.w.player
        return [c for c in self.w.cells((block,)) if not self.w.memory.is_unreachable(c, p)
                and (self.within is None or math.dist(c, p) <= self.within)]

    def nearby(self, blocks_):
        cells = [c for b in blocks_ for c in self._cells(b)]
        return min((math.dist(c, self.w.player) for c in cells), default=None)

    def station_near(self, item=None):
        d = self.nearby((item or self.station,))
        return d is not None and d < 32

    def travel(self, c) -> float:
        """Seconds to get within reach of c, reckoned: across, up (a step dug or a block put down each), and the
        blocks in the way on a straight line (dug)."""
        p = self.w.player
        flat = math.hypot(c[0] - p[0], c[2] - p[2])
        up = max(0, c[1] - p[1] - 2)
        n = max(1, int(math.dist(p, c) * 2))
        dig = sum(1 for i in range(1, n) if self.w.at(tuple(round(p[j] + (c[j] - p[j]) * i / n) for j in range(3)))
                  in SOLID) / 2
        return max(0.0, flat - 3.0) / WALK + up * CLIMB_S + dig * DIG_S

    def gather_cost(self, src, n):
        cells = self._cells(src.block)
        if not cells:
            return None
        return self.travel(cells[0]) + n * (HARD.get(src.block, 4) * 4 / 20.0 + 0.5)

    def station_cost(self, item):
        cells = self._cells(item)
        return self.travel(cells[0]) if cells else None


def _tool_ok(need, have) -> bool:
    kind = need.split('_')[-1]
    tier = need.split(':')[-1].split('_')[0]
    if kind != 'pickaxe' or tier not in PICKS:
        return have.get(need, 0) > 0
    return any(have.get(f'{M}{t}_pickaxe', 0) > 0 for t in PICKS[PICKS.index(tier):])


def _what(step) -> str:
    return (step[1].result if step[0] in ('craft', 'smelt') else str(step[1])).split(':')[-1]


def plan_for(world: SimWorld, item: str, total: int, after=None) -> list:
    """The goal planner's planner here: from what's held now ([] when it's held). A way that needs blocks to
    build with that aren't spare: blocks first, from what's in reach (stuck: what's got for the way out can't
    need the way out); stuck: only what's in reach."""
    if world.inv[item] >= total:
        return []
    route = (after.reason.get('route') or {}) if after is not None and after.reason is not None else {}
    stuck = after is not None and after.reason is not None and after.reason.kind == S.STUCK
    book = SimBook(world, within=STUCK_REACH if stuck or route.get('found') == 'needs blocks' else None)
    world.reserve = {}
    if route.get('found') == 'needs blocks':
        need = int(route.get('blocks_it_would_take') or 0) - int(route.get('blocks_to_build_with') or 0) + 4
        for block in BUILD[:2]:
            try:
                return plan(book, block, world.inv[block] + need, dict(world.inv))
            except CantMake:
                continue
    steps = plan(book, item, total, dict(world.inv))
    world.reserve = _reserved(steps)
    return steps


def _reserved(steps) -> dict:
    out: dict = {}
    for s in steps:
        if s[0] in ('craft', 'smelt'):
            for fits, cells in s[1].needs():
                for it in fits:
                    out[it] = out.get(it, 0) + cells * s[2]
            if s[0] == 'smelt':
                out[s[3]] = out.get(s[3], 0) + s[4]
        elif s[0] == 'station':
            out[s[1]] = out.get(s[1], 0) + 1
    return out


def run_goal(world: SimWorld, item: str, count: int = 1, max_steps: int = 60):
    """An order played to its end: (the last Outcome, the goal runner)."""
    total = world.inv[item] + count
    goal = GoalRunner(lambda it, n, after: plan_for(world, it, n, after),
                      lambda step: step_cost(SimBook(world), step))
    steps = goal.start(item, total)
    i, done = 0, 0
    out = S.done()
    while i < len(steps) and done < max_steps:
        t0 = world.t
        out = world.run(steps[i])
        done += 1
        if not out.ok:
            steps = goal.failed(out)
            if steps is None:
                return out, goal
            i = 0
            continue
        world.reserve = _reserved(steps[i + 1:])
        new = goal.step_done(i, world.t - t0)
        if new is not None:
            steps, i = new, 0
            continue
        i += 1
    if world.inv[item] >= total:
        return S.done(), goal
    return out if not out.ok else S.failed(S.TIMEOUT, 'too many steps', what=item), goal
