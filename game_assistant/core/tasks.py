"""Doing a crafting plan (core/crafting.py) in the game: gather, place the station, craft (any game whose adapter
gives the world below; first Minecraft).

  gather   (the tool it needs into the hotbar first) walk to the nearest source block (the walk goes up to it,
           digging its way, and ends looking at it), mine it (the crosshair on it, the best tool, the attack
           button held till it's gone), pick up what it dropped (walking to it if it didn't come by itself);
           again until enough are held
  station  (into the hotbar first) take it into the hand and place it beside the player
  craft    open the crafting screen (the inventory for the hand's grid; the station, walked to if it's not in
           reach, for a bigger one), click the ingredients into the grid (pick a stack up, one into each cell
           with the right button, the rest put back), take the result (Shift held, then a click: as many as the
           grid makes, into the inventory; anything left on the cursor put down in an empty slot), close

Moving something into the hotbar: open the inventory, pick it up, click an empty hotbar slot (or swap with the
last one, putting what was there where it came from), close.

The world (the app, TaskWorld) walks, finds blocks and dropped items, and clicks the screen's slots; the
snapshot's extra gives what's held ('inventory', 'hotbar'), what the crosshair is on ('aim_block', 'aim_face',
'tool_slot', 'slot') and the open screen's slots and cursor ('container'). update() returns what to press each
frame; every change of step or phase goes to `trace` (the app writes it to runtime/task-<date>.jsonl).
"""
from __future__ import annotations

import math
import time
from typing import Protocol

from .skills import needs_blocks
from .skills import (DANGER, MISSING, NEEDS_TOOL, NO_SPOT, NO_TARGET, STUCK, TIMEOUT, UI, UNREACHABLE, Outcome,
                     Reason)
from .autowalk import Intent, aim_at

CLICK_GAP = 0.15        # seconds between clicks on a screen (each must reach the game before the next)
POINT_WAIT = 0.08       # seconds after moving the pointer before asking the game what's under it
POINT_TRIES = 4
POINT_SETTLE = 0.2      # seconds after moving the pointer before a click, where the game can't say what's under it
SHIFT_FIRST = 0.15      # seconds Shift is held before a shift-click (pressed in the same instant, the game
                        # missed it and the click picked the result up instead, 2026-10-13)
MINE_TIMEOUT = 25.0
PLACE_WALKS = 2         # walks toward a building's block before it's given up as out of reach
PLACE_TRIES = 2         # ...and times it's tried from where the player stands
USE_REACH = 4.0         # blocks from the eye: used from where the player is, not walked to first
EQUIP_LOOK_UP = 30.0    # degrees a frame the view's tilted up before armour's put on (to the top: the sky, no block)
HUNT_TRIES = 2          # times one creature is gone after before it's left for another
USE_TIMEOUT = 6.0       # seconds trying to use the tool on one thing before another is tried
HUNT_IDLE = 3.0         # seconds hunting with no walk or fight under way before looking again
CLEAR_TIMEOUT = 6.0     # seconds mining a block in the way of what's mined, at most
OPEN_TIMEOUT = 3.0
SPOT_TIME = 5.0         # seconds trying to put a block down in one spot before another is tried...
SPOTS = 3               # ...this many in all
ARRANGE_MOVES = 24      # moves laying out the hotbar at most (one more: it's not coming out as wanted)
COLLECT_WAIT = 1.2      # seconds for a mined block's drop to come to the player...
DIG_DROP_NEAR = 4.5     # ...else one lying this near is walked onto (a block dug on its own)...
FETCH_S = 6.0           # ...for this long at most
PICKUP_RANGE = 6.0      # blocks: dropped items this near are walked to
PICKUP_TRIES = 2        # times one dropped item is walked to before it's left (it didn't come: out of reach)
REACH = 4.3             # blocks: a station this near is used where it stands
STEP_TIMEOUT = 300.0
MAX_RECOVERIES = 2      # a task's stuck steps got going again (what they lacked got first), at most
COOK_SPARE = 15.0       # seconds over the cooking time before a smelt is given up
NO_FUEL_S = 4.0         # seconds with the furnace out and something left to cook: out of fuel
SETTLE = 0.4            # seconds for a screen to fill in (just opened), or the grid after the last click


class TaskWorld(Protocol):
    def task_find(self, blocks: tuple, exclude: set, near=None) -> tuple | None: ...   # (point, name, key)
    def task_items(self, items: tuple, radius: float) -> list: ...          # [point]: dropped items near
    def task_walk(self, point, name: str, beside=None, back=False) -> None: ...   # beside: right up beside
                                                                 # that block; back: it's come back from
    def task_stop(self) -> None: ...
    def task_say(self, line: str) -> None: ...
    def task_point(self, x: float, y: float) -> None: ...          # the pointer to (x, y): the game window's pixels
    def task_press(self, button: str) -> None: ...                 # click where the pointer is
    def task_place_spot(self, exclude=()) -> tuple | None: ...   # (aim point, against key, face, new block's
                                                                  # key); exclude: new blocks' keys tried
    def task_tool(self, need: str, have: dict) -> str | None: ...   # the held tool that will do for `need`
    def task_food(self, hotbar: list) -> int | None: ...          # the hotbar slot with the best food
    def task_aim(self, key, eye) -> tuple: ...   # (a point on the block the crosshair reaches it at, or None; the
                                                 # block in the way when none does)


def _cell(p) -> tuple:
    return (math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2]))


def _at(key) -> str:
    return ' '.join(str(int(v)) for v in key)


def _name(item: str) -> str:
    return item.split(':')[-1].replace('_', ' ')


def _slots(screen: dict) -> list:
    return (screen or {}).get('slots', [])


def _arrange_move(slots: list, layout: list) -> list | None:
    """The next move toward the hotbar as layout says: the clicks ((slot index, x, y, button)) taking the
    wanted stack to its hotbar slot (and what was there back where the stack came from), or None when it's as
    wanted (or nothing more can be: the item isn't held)."""
    bar = {int(str(s[5])[6:]): s for s in slots if str(s[5]).startswith('hotbar') and str(s[5])[6:].isdigit()}
    for i, want in enumerate(layout):
        if not want or i not in bar or bar[i][3] == want:
            continue
        # from the main inventory first, else from a hotbar slot that isn't already where it's wanted
        cands = [s for s in slots if s[3] == want and str(s[5]).startswith('main')]
        cands += [s for j, s in bar.items() if s[3] == want and j != i and (j >= len(layout) or layout[j] != want)]
        if not cands:
            continue
        src = max(cands, key=lambda s: s[4] or 0)        # (the biggest stack of it)
        dst = bar[i]
        clicks = [(src[0], src[1], src[2], 'left'), (dst[0], dst[1], dst[2], 'left')]
        if dst[3]:
            clicks.append((src[0], src[1], src[2], 'left'))   # (what was there: where the stack came from)
        return clicks
    return None


def step_text(s) -> str:
    """A step in words: 'get 3 oak log', 'craft 4 stick', 'place a crafting table'."""
    if s[0] == 'gather':
        return f'get {s[2]} {_name(s[1])}'
    if s[0] == 'craft':
        return f'craft {s[1].count * s[2]} {_name(s[1].result)}'
    if s[0] == 'station':
        return f'place a {_name(s[1])}'
    if s[0] == 'smelt':
        return f'smelt {s[2]} {_name(s[1].cells[0][0])} with {s[4]} {_name(s[3])}'
    if s[0] == 'dig':
        return f'mine the {s[3]} at {_at(s[1])}'
    if s[0] == 'place':
        return f'put {_name(s[2])} at {_at(s[1])}'
    if s[0] == 'offhand':
        return f'hold the {_name(s[1])} in the off hand'
    if s[0] == 'equip':
        return f'put on the {_name(s[1])}'
    if s[0] == 'use':
        return f"use {'the ' + _name(s[4]) + ' on ' if s[4] else ''}the {s[5]}"
    if s[0] == 'pickup':
        return 'pick up what lies about'
    if s[0] == 'arrange':
        return 'lay out the hotbar: ' + ', '.join(f'{i + 1} {_name(it)}' for i, it in enumerate(s[1]) if it)
    return s[0]


class TaskRunner:
    def __init__(self, world: TaskWorld):
        self.world = world
        self.active = False
        self.steps: list = []
        self.i = 0
        self.phase = ''
        self.events: list = []
        self.trace: list = []
        self.t = 0.0
        self.result = ''
        self.recoveries = 0
        self.outcome: Outcome | None = None   # how the last task ended (docs/AGENT.md)
        self.pos = None             # where the player is (last seen)
        self.gave_up_at = None      # where the last walk was given up
        self.calib = [0.0, 0.0]     # what the pointer turned out to be off by (window pixels): corrected for
        self.pointing = None

    # ---- control ----

    def start(self, steps: list, goal: str, count: int) -> None:
        self.outcome = None
        self.recoveries = 0
        self.gave_up_at = None
        self.steps, self.i, self.goal, self.count = list(steps), 0, goal, count
        self.active, self.t, self.result = True, 0.0, ''
        self.built: set = set()     # blocks of a building put down: never dug through on the way to the next
        self._begin()

    def stop(self, why: str = '') -> None:
        if self.active:
            self.active = False
            self.world.task_stop()
            self.result = f'stopped: {why}' if why else 'stopped'
            if why:
                self.events.append(f'stopped:{why}')

    def reserved(self) -> dict:
        """What the steps still to come use up, {item: how many} (every item that fits an ingredient, the fuel,
        a station to put down): the walk doesn't build with these (8 cobblestone dug for a furnace went into a
        pillar up out of the hole, and the furnace couldn't be made, 2026-10-15)."""
        out: dict = {}
        for s in self.steps[self.i:] if self.active else ():
            if s[0] in ('craft', 'smelt'):
                for fits, cells in s[1].needs():
                    for it in fits:
                        out[it] = out.get(it, 0) + cells * s[2]
                if s[0] == 'smelt':
                    out[s[3]] = out.get(s[3], 0) + s[4]
            elif s[0] in ('station', 'place', 'offhand', 'equip'):
                item = s[2] if s[0] == 'place' else s[1]
                out[item] = out.get(item, 0) + 1
        return out

    def keep(self) -> set:
        """The blocks the walk mustn't break: what's been built, and what's still to be built on."""
        return set(self.built) if self.active else set()

    def describe(self) -> str:
        bits, placing = [], {}
        for s in self.steps:
            if s[0] == 'place':
                placing[s[2]] = placing.get(s[2], 0) + 1
                continue
            if placing:
                bits.append('build with ' + ', '.join(f'{n} {_name(i)}' for i, n in placing.items()))
                placing = {}
            bits.append(step_text(s))
        if placing:
            bits.append('build with ' + ', '.join(f'{n} {_name(i)}' for i, n in placing.items()))
        return ', '.join(bits)

    def _begin(self) -> None:
        self.step_t0 = self.t
        self.step_from = self.pos   # where the step began: what's gathered is taken on from about here
        self.base, self.want = None, None
        self.skip: set = set()
        self.clicks: list = []
        self.last_click = -1e9
        self.target = None
        self.held_at = None
        self.tries = 0
        self.hotbar = None          # moving an item into the hotbar: (item, phase)
        self.item_tries: dict = {}  # dropped items walked to (by their block): how often (one may not come)
        self.beside: set = set()    # blocks walked right up beside (not reached from where the walk ended)
        self.hunted: dict = {}      # creatures gone after (id: times)
        self.place_tries = 0        # a building's block: times it was tried from where the player stood
        self.clearing = None        # (a block in the way of what's mined, a point on it, since): mined first
        self.cleared: set = set()   # blocks in the way mined (or tried) this step
        self.station_pt = None      # where to aim at the station: a point on it in reach and in sight (or None)
        self.station_eye = None     # ...from this eye
        self.walked_to_station = 0
        self.out_since = None       # cooking: the furnace out with something left to cook (since)
        self._set('start')

    def _set(self, phase: str, note: str = '') -> None:
        self.phase, self.since = phase, self.t
        step = self.steps[self.i] if self.active and self.i < len(self.steps) else None
        what = None
        if step is not None:
            what = f'{step[0]} {_name(step[1].result) if step[0] in ("craft", "smelt") else _name(str(step[1]))}'
        self.trace.append({'t': round(self.t, 2), 'step': self.i, 'what': what, 'phase': phase, 'note': note})

    def _next(self) -> None:
        # the step's done: told (a goal runner may plan on from here: then the task's started again)
        hook = getattr(self.world, 'task_step_done', None)
        if hook is not None and hook(self.i, self.t - getattr(self, 'step_t0', 0.0)):
            return
        self.i += 1
        if self.i >= len(self.steps):
            self.active = False
            self.events.append('done')
            self.result = 'done'
            self.outcome = Outcome(True, None, f'have {self.count} {_name(self.goal)}', took_s=self.t)
            self.ended_at = time.monotonic()
            self.trace.append({'t': round(self.t, 2), 'phase': 'done'})
            if self.steps and self.steps[-1][0] == 'station':
                self.world.task_say(f'Done! The {_name(str(self.steps[-1][1]))} is down.')
            else:
                self.world.task_say(f'Done! You have your {_name(self.goal)}.')
            return
        self._begin()

    def _fail(self, line: str, kind: str = UI, **data) -> None:
        """The task's over, not done: the line for the player, and why as data (the Outcome's Reason) for what
        decides next (docs/AGENT.md)."""
        self.active = False
        self.world.task_stop()
        self.events.append('failed')
        data = {k: (v.brief() if isinstance(v, Reason) else v) for k, v in data.items() if v is not None}
        self.outcome = Outcome(False, Reason(kind, {'step': self._what(), **data}), line, took_s=self.t)
        self.ended_at = time.monotonic()
        if kind in (UNREACHABLE, DANGER) and data.get('at') is not None:
            self._note('unreachable', what=str(data.get('what') or ''), at=data['at'], frm=self.pos,
                       reason=str(data.get('reason') or ''))
        self.result = f'failed: {line}'
        self.trace.append({'t': round(self.t, 2), 'phase': 'failed', 'note': line,
                           'reason': self.outcome.reason.brief()})
        self.world.task_say(line)

    def guarded(self) -> None:
        """Hostile creatures stay where this step was going (held off: they won't come away from it): the step
        fails with a danger Reason at that target, which is left out for a while (that tree, that crafting table;
        not the place), and the order plans round it (docs/CONDUCT.md)."""
        if not self.active:
            return
        t = self.target
        if self.phase == 'pickup' and t is not None and t[2] is None:   # (a dropped thing: that one's left)
            self.item_tries[_cell(t[0])] = PICKUP_TRIES
            self._set('find', 'its drop is guarded')
            return
        what = (t[1] if t else '') or self._what()
        at = tuple(t[2]) if t and t[2] is not None and isinstance(t[2], (tuple, list)) else None
        self._fail(f"There are hostile ones right by the {what}: going about it another way.", DANGER, what=what, at=at)

    def _what(self) -> str:
        if not self.steps or self.i >= len(self.steps):
            return ''
        step = self.steps[self.i]
        if step[0] == 'place':
            return f'place {_name(step[2])}'
        return f'{step[0]} {_name(step[1].result) if step[0] in ("craft", "smelt") else _name(str(step[1]))}'

    def _note(self, event: str, **data) -> None:
        """What was done or learned, for the world memory (the world passes it on, if it keeps one)."""
        note = getattr(self.world, 'task_note', None)
        if note is not None:
            try:
                note(event, **data)
            except Exception:
                pass

    def resume(self) -> None:
        """After something interrupted it (a fight): the step again from its start (what's held counts)."""
        if self.active:
            self._set('start', 'resumed')
            self.held_at = None
            self.clicks, self.pointing, self.hotbar = [], None, None

    def on_event(self, ev: str, reason: Reason | None = None) -> None:
        """The walk's events: 'arrived', 'gave_up'."""
        if not self.active:
            return
        if ev in ('arrived', 'gave_up') and self.phase == 'walk_place':
            self._set('start', 'walked')                 # (in reach from there, or not: it says, and tries again)
        elif ev == 'arrived' and self.phase in ('walk', 'pickup', 'to_station'):
            self._set({'walk': 'mine', 'pickup': 'find', 'to_station': 'open'}[self.phase])
        elif ev == 'gave_up' and self.phase == 'walk' and self.steps[self.i][0] == 'dig':
            step = self.steps[self.i]
            self._fail(f"I can't get to the {step[3]} at {_at(step[1])}.", UNREACHABLE, what=step[3], at=tuple(step[1]),
                       reason=reason)
        elif ev == 'gave_up' and self.phase == 'walk' and self._stuck_here(reason):
            pass                                         # (no way on from here at all: said, the step over)
        elif ev == 'gave_up' and self.phase in ('walk', 'to_station') and self._recover(reason):
            pass                                         # (what it lacked: got first, then this step again)
        elif ev == 'gave_up' and self.phase in ('walk', 'to_station'):
            if self.target is not None and self.target[2] is not None:
                self.skip.add(tuple(self.target[2]))   # can't get to that one: another
            self._set('find' if self.phase == 'walk' else 'start', 'gave up walking there')
        elif ev == 'gave_up' and self.phase == 'pickup':
            self._set('find')
        elif ev in ('arrived', 'gave_up') and self.phase == 'fetch':
            self._set('collect', 'fetched')              # (then on, it's come or not)
        elif ev in ('arrived', 'gave_up') and self.phase == 'go':
            self._set('aim', ev)                         # (in reach, or as near as it gets: tried from there)
        elif ev == 'arrived' and self.phase == 'approach':
            self.tries = 0
            self._set('use')
        elif ev in ('gave_up', 'arrived') and self.phase in ('hunt', 'approach', 'seek'):
            if ev == 'gave_up' and self.target is not None:
                self.skip.add(self.target[2])           # (can't get to that one: another)
            self._set('find')

    def _stuck_here(self, reason: Reason | None = None) -> bool:
        """Walks given up from the same spot, twice: the trouble is where the player is, not which one is walked to
        (walks to one log after another failed from the bottom of a hole, 2026-10-15): first what it lacks, if the
        world can say; else the step fails (and is thought through)."""
        here = self.pos
        last = self.gave_up_at
        self.gave_up_at = here
        if here is None or last is None or math.dist(here, last) > 1.5:
            return False
        if self._recover(reason):
            return True
        if reason is not None and needs_blocks(reason.get('route')):
            # not stuck: there's a way, with blocks to build it (the leaves of the tree stood under were "every way
            # out", and the order went back to the doer, 2026-10-16): the blocks are got first, by the plan
            self._fail("I'd need some blocks to build my way there.", UNREACHABLE, step=self._what(),
                       **{k: v for k, v in reason.data.items() if k != 'step'})
            return True
        self._fail("I can't get anywhere from here: every way out needs something I haven't got.", STUCK,
                   frm=self.pos, reason=reason)
        return True

    def _recover(self, reason: Reason | None = None) -> bool:
        """The walk for this step gave up: what it lacked, got first (the world says: blocks to build the way with,
        from what's near and cheapest), then this step again. Twice at most in a task. True: under way. (14 down
        for cobblestone, the crafting table 14 up, the cobblestone kept for the furnace: "I can't get to where I
        can reach the crafting table", the order over, 2026-10-15.)"""
        if self.recoveries >= MAX_RECOVERIES or not hasattr(self.world, 'task_recover'):
            return False
        try:
            got = self.world.task_recover(reason)
        except TypeError:                                # (a world that reads the walk itself)
            got = self.world.task_recover()
        except Exception:
            got = None
        if not got:
            return False
        steps, line = got
        self.recoveries += 1
        self.steps[self.i:self.i] = list(steps)
        self.trace.append({'t': round(self.t, 2), 'phase': 'recover', 'note': line})
        self.world.task_say(line)
        self._begin()
        return True

    # ---- every frame ----

    def update(self, snap, dt: float, walking: bool) -> Intent | None:
        """What to press now (None: nothing; the walk, when walking, presses its own)."""
        if not self.active:
            return None
        ex0 = snap.extra or {}
        if (snap.screen is not None and not snap.screen.focused) or (snap.menu and not ex0.get('container')):
            return Intent()                              # (paused: the clock stops; it timed out while the
                                                         # player was in another window, 2026-10-13)
        self.t += dt
        if self.t - self.since > STEP_TIMEOUT and self.phase != 'cook':   # (cooking has its own limit)
            self._fail("This is taking too long. Over to you!", TIMEOUT, what=self._what())
            return None
        step = self.steps[self.i]
        ex = snap.extra or {}
        inv = ex.get('inventory') or {}
        if snap.player is not None:
            self.pos = snap.player.pos
        if self.hotbar is not None:
            return self._to_hotbar(ex)
        if step[0] == 'gather':
            return self._gather(snap, ex, inv, step, walking)
        if step[0] == 'station':
            return self._station(snap, ex, step)
        if step[0] == 'eat':
            return self._eat(snap, ex)
        if step[0] == 'smelt':
            return self._smelt(snap, ex, inv, step)
        if step[0] == 'dig':
            return self._dig(snap, ex, inv, step)
        if step[0] == 'place':
            return self._place(snap, ex, inv, step)
        if step[0] == 'offhand':
            return self._offhand(ex, inv, step)
        if step[0] == 'equip':
            return self._equip(ex, inv, step)
        if step[0] == 'use':
            return self._use_on(snap, ex, inv, step)
        if step[0] == 'arrange':
            return self._arrange(ex, step)
        if step[0] == 'pickup':
            return self._pickup(step)
        return self._craft(snap, ex, inv, step, walking)

    # ---- picking up what lies about (Baritone's pickup) ----

    def _pickup(self, step) -> Intent | None:
        """('pickup', items or None (any), radius): each dropped item of those lying within radius walked onto,
        the nearest first, till none is left (one that won't come is left after PICKUP_TRIES)."""
        _p, items, radius = step
        if self.phase == 'start':
            self.item_tries = {}
            self._set('find')
        if self.phase == 'find':
            near = [p for p in self.world.task_items(items, radius)
                    if self.item_tries.get(_cell(p), 0) < PICKUP_TRIES]
            if not near:
                self._next()
                return None
            self.item_tries[_cell(near[0])] = self.item_tries.get(_cell(near[0]), 0) + 1
            self.target = (near[0], 'what lies here', None)
            self.world.task_walk(near[0], 'what lies here')
            self._set('pickup')
            return None
        if self.phase == 'pickup' and self.t - self.since > 8.0:
            self._set('find')
        return None

    # ---- laying out the hotbar (the inventory screen) ----

    def _arrange(self, ex, step) -> Intent:
        """('arrange', layout): the hotbar's slots as layout says (nine: an item, or None to leave it), each moved
        there from wherever it is in the inventory, one move at a time, the screen read again after each (a stack
        picked up, put on its slot; what was there put back where the stack came from). ("Move the iron into my
        hotbar and sort things" was "my orders don't reach the inventory slots", while crafting used the same
        screen, 2026-10-16.)"""
        layout = list(step[1])[:9]
        screen = ex.get('container')
        if self.phase == 'start':
            if screen and screen.get('menu') != 'InventoryMenu':
                self._set('close_first')
                return Intent(taps=('escape',))
            self.moves_made = 0
            self._set('open')
        if self.phase == 'close_first':
            if not screen:
                self._set('start')
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        if self.phase == 'open':
            if screen and screen.get('menu') == 'InventoryMenu':
                self._set('settle')
                return Intent()
            if self.t - self.since > OPEN_TIMEOUT:
                self._fail("I couldn't open the inventory.", UI)
                return Intent()
            if self.t - self.last_click > 1.0:
                self.last_click = self.t
                return Intent(taps=('inventory',))
            return Intent()
        if self.phase == 'settle':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.t - self.since < SETTLE:
                return Intent()
            slots = _slots(screen)
            carried = screen.get('carried') or ''
            if carried:                                  # (on the cursor: put down in an empty slot first)
                empty = next((s for s in slots if str(s[5]).startswith(('main', 'hotbar')) and not s[3]), None)
                if empty is None:
                    self._fail("There's no room in the inventory to put it down.", NO_SPOT)
                    return Intent(taps=('escape',))
                self.clicks = [(empty[0], empty[1], empty[2], 'left')]
                self._set('move', 'put down what was on the cursor')
                return Intent()
            move = _arrange_move(slots, layout)
            if move is None:
                self._set('close', 'laid out')
                return Intent(taps=('escape',))
            self.moves_made += 1
            if self.moves_made > ARRANGE_MOVES:
                self._fail("The hotbar wouldn't come out as wanted.", UI)
                return Intent(taps=('escape',))
            self.clicks = move
            self._set('move', json_short([c[0] for c in move]))
            return Intent()
        if self.phase == 'move':
            got = self._clicking(screen)
            if got == 'failed':
                self._fail("I couldn't get the pointer onto the right slot.", UI)
                return Intent(taps=('escape',))
            if got == 'done':
                self._set('settle', 'again')
            return Intent()
        if self.phase == 'close':
            if not screen:
                self._next()
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        return Intent()

    # ---- moving an item into the hotbar ----

    def _need_in_hotbar(self, ex, item: str) -> bool:
        """True (and the move started) when item is held but not in the hotbar."""
        hot = ex.get('hotbar') or []
        if any(s and s[0] == item for s in hot):
            return False
        if not (ex.get('inventory') or {}).get(item):
            return False
        self.hotbar = [item, 'open', self.t, -1e9]
        self.trace.append({'t': round(self.t, 2), 'phase': 'to_hotbar', 'note': item})
        return True

    def _to_hotbar(self, ex) -> Intent:
        item, phase, since, last = self.hotbar
        screen = ex.get('container')
        if self.t - since > 8.0:
            self.hotbar = None
            self._fail(f"I couldn't move the {_name(item)} into your hotbar.")
            return Intent(taps=('escape',)) if screen else Intent()
        if phase == 'open':
            if screen and screen.get('menu') == 'InventoryMenu':
                slots = _slots(screen)
                src = next((s for s in slots if str(s[5]).startswith('main') and s[3] == item), None)
                if src is None:                          # (it's in the hotbar already, or gone)
                    self.hotbar[1] = 'close'
                    return Intent()
                empty = next((s for s in slots if str(s[5]).startswith('hotbar') and not s[3]), None)
                dst = empty or next(s for s in slots if s[5] == 'hotbar8')
                self.clicks = [(src[0], src[1], src[2], 'left'), (dst[0], dst[1], dst[2], 'left')]
                if not empty:                            # swapped: what was there goes where it came from
                    self.clicks.append((src[0], src[1], src[2], 'left'))
                self.hotbar[1] = 'click'
                return Intent()
            if screen:
                return Intent(taps=('escape',)) if self.t - last > 1.0 and not self._mark() else Intent()
            if self.t - last > 1.0:
                self.hotbar[3] = self.t
                return Intent(taps=('inventory',))
            return Intent()
        if phase == 'click':
            got = self._clicking(screen)
            if got == 'done':
                self.hotbar[1] = 'close'
            elif got == 'failed':
                self.hotbar = None
                self._fail("I couldn't get the pointer onto the right slot.")
                return Intent(taps=('escape',))
            return Intent()
        if phase == 'close':
            if not screen:
                self.hotbar = None
                return Intent()
            if self.t - last > 0.6:
                self.hotbar[3] = self.t
                return Intent(taps=('escape',))
            return Intent()
        return Intent()

    def _clicking(self, screen) -> str:
        """Work through self.clicks ((slot index, x, y, button)): point at the slot, check with the game that it's
        the slot under the pointer (else correct by where the game says the pointer is, and point again), then
        click. 'busy', 'done', or 'failed' (the pointer never got onto it: nothing clicked; a click outside the
        window would have thrown what's held away, as it did twice, 2026-10-13)."""
        if not self.clicks:
            return 'done'
        if not screen:
            return 'failed'
        click = self.clicks[0]
        idx, x, y, button = click
        if self.pointing is None or self.pointing[0] is not click:
            if self.t - self.last_click < CLICK_GAP:
                return 'busy'
            self.world.task_point(x + self.calib[0], y + self.calib[1])
            self.pointing = (click, self.t, 0)
            return 'busy'
        _c, since, tries = self.pointing
        if self.t - since < POINT_WAIT:
            return 'busy'
        if screen.get('hovered') is None and self.t - since >= POINT_SETTLE:
            # (a bridge that doesn't say what's under the pointer: the move given time to land first. Sent in
            # the same instant as the move, a click landed where the pointer had been: three cobblestone into
            # one cell, 2026-10-13)
            self.world.task_press(button)
            self.clicks.pop(0)
            self.pointing = None
            self.last_click = self.t
            return 'busy'
        if screen.get('hovered') is None:
            return 'busy'
        if screen.get('hovered') == idx:
            self.world.task_press(button)
            self.clicks.pop(0)
            self.pointing = None
            self.last_click = self.t
            return 'busy'
        if tries >= POINT_TRIES:
            self.trace.append({'t': round(self.t, 2), 'phase': 'pointer', 'note': json_short(
                {'want': idx, 'at': [x, y], 'hovered': screen.get('hovered'), 'mouse': screen.get('mouse'),
                 'calib': self.calib})})
            return 'failed'
        m = screen.get('mouse')
        if m:
            self.calib[0] += x - m[0]
            self.calib[1] += y - m[1]
        self.world.task_point(x + self.calib[0], y + self.calib[1])
        self.pointing = (click, self.t, tries + 1)
        return 'busy'

    def _mark(self) -> bool:
        self.hotbar[3] = self.t
        return False

    # ---- gathering ----

    def _gather(self, snap, ex, inv, step, walking) -> Intent | None:
        _g, item, k, src = step
        if self.want is None:
            self.base = inv.get(item, 0)
            self.want = self.base + k
        if inv.get(item, 0) >= self.want:
            self.world.task_stop()
            self._next()
            return None
        if self.phase == 'start':
            if src.tool:                                 # the tool it needs: in the hotbar, to be picked
                tool = self.world.task_tool(src.tool, inv)
                if tool is None:
                    self._fail(f'I need a {_name(src.tool)} for {_name(item)}.', NEEDS_TOOL, tool=src.tool, item=item)
                    return None
                if self._need_in_hotbar(ex, tool):
                    return Intent()
            self._set('find')
        if self.phase in ('walk', 'mine') and src.tool and self.world.task_tool(src.tool, inv) is None:
            # (worn out on the way or at it: the walk there dug through stone with it till it broke, then the ore was
            # punched by hand, 15 s and nothing dropping, 2026-10-17: stopped, a new one made, as below)
            self.world.task_stop()
            self._fail(f'My {_name(src.tool)} broke: making another for the {_name(item)}.', NEEDS_TOOL,
                       tool=src.tool, item=item)
            return None
        if self.phase == 'find':
            # (the tool, again each time: one wears out and breaks; the step went on "mining" iron with a wooden
            # pickaxe, nothing dropping, the same ore walked to again and again, 2026-10-16. Failed: the plan's
            # made again from what's held, a new tool in it)
            if src.tool and self.world.task_tool(src.tool, inv) is None:
                self._fail(f'My {_name(src.tool)} is gone (worn out?): making another for the {_name(item)}.',
                           NEEDS_TOOL, tool=src.tool, item=item)
                return None
            near = [p for p in self.world.task_items(src.drops, PICKUP_RANGE)
                    if self.item_tries.get(_cell(p), 0) < PICKUP_TRIES]
            if near:                                   # dropped already (by this, or lying about)
                self.item_tries[_cell(near[0])] = self.item_tries.get(_cell(near[0]), 0) + 1
                self.target = (near[0], _name(item), None)
                self.world.task_walk(near[0], _name(item))
                self._set('pickup')
                return None
            if getattr(src, 'creature', False):
                return self._creature(item, src)
            try:                                       # (the quickest there and back to where the step began)
                got = self.world.task_find((src.block,), self.skip, near=self.step_from)
            except TypeError:
                got = self.world.task_find((src.block,), self.skip)
            if got is None:
                self._fail(f"I can't find any more {_name(src.block)} near here.", NO_TARGET, what=src.block, item=item)
                return None
            self.target = got
            self.tries = 0
            try:                                       # (more steps after: it's come back from)
                self.world.task_walk(got[0], got[1], back=self.i < len(self.steps) - 1)
            except TypeError:
                self.world.task_walk(got[0], got[1])
            self._set('walk', f'{got[1]} at {got[2]}')
            return None
        if self.phase in ('walk', 'pickup', 'hunt', 'approach', 'seek'):
            if self.phase == 'pickup' and self.t - self.since > 8.0:
                self._set('find')
            elif self.phase == 'seek' and self.target is not None \
                    and getattr(self.world, 'task_seen', lambda i: True)(self.target[2]):
                self.world.task_stop()                    # (in view now: hunted, or used on, from here)
                self._set('find', 'in sight')
            elif self.phase in ('hunt', 'approach') and not walking and self.t - self.since > HUNT_IDLE:
                self._set('find', 'the hunt ended')       # (no fight came of it: it's gone, or it's dead)
            return None
        if self.phase == 'mine' and getattr(src, 'how', 'mine') == 'use':
            return self._use(snap, ex, inv, src, block=True)
        if self.phase == 'use':
            return self._use(snap, ex, inv, src, block=False)
        if self.phase == 'mine':
            return self._mine(snap, ex, item)
        if self.phase == 'collect':
            if inv.get(item, 0) > self.got_before or self.t - self.since > COLLECT_WAIT:
                self._set('find')
            return Intent()
        return None

    def _dig(self, snap, ex, inv, step) -> Intent | None:
        """One block (the game's key, where to walk, its name): walked to where it's in reach and in sight, mined,
        what it drops picked up. ("Mine the iron ore at 12 56 -5" wasn't an order it knew, 2026-10-15.)"""
        _d, key, point, name = step
        if self.phase == 'start':
            self.dig_had = sum((ex.get('inventory') or {}).values())   # (to see whether what it drops came)
            self.dig_fetched = False
            self.target = (point, name, tuple(key))
            self.world.task_walk(point, name)
            self._set('walk', f'{name} at {_at(key)}')
            return None
        if self.phase == 'walk':
            return None
        if self.phase == 'mine':
            got = self._mine(snap, ex, '')
            if self.phase == 'find':                     # (left: it couldn't be mined)
                self._fail(f"I couldn't mine the {name} at {_at(key)}.", UNREACHABLE, what=name, at=tuple(key))
                return None
            return got
        if self.phase == 'collect':
            if self.t - self.since > COLLECT_WAIT:
                # what it dropped didn't come (it fell, or lay just out of reach): walked onto, once ("done!" four
                # times over iron ore, nothing got, the raw iron left lying, 2026-10-16)
                had = getattr(self, 'dig_had', None)
                fetched = getattr(self, 'dig_fetched', True)
                if had is not None and sum((ex.get('inventory') or {}).values()) <= had and not fetched:
                    drops = self.world.task_items(None, DIG_DROP_NEAR)
                    if drops:
                        self.dig_fetched = True
                        self.world.task_walk(drops[0], name)
                        self._set('fetch', 'what it dropped')
                        return None
                self._next()
            return Intent()
        if self.phase == 'fetch':
            if self.t - self.since > FETCH_S:
                self._next()
            return None
        return None

    def _mine(self, snap, ex, item) -> Intent:
        point, _name_, key = self.target
        cam = snap.camera
        centre = (point[0], point[1] + 0.5, point[2])
        # a point on it that can be seen (its middle was behind the log above it, seen from beside: skipped as
        # "could not mine it", 2026-10-13): asked as mining starts and when the eye has moved
        if self.held_at is None and self.clearing is None and (getattr(self, '_aim_eye', None) is None
                                                               or math.dist(cam.pos, self._aim_eye) > 0.4):
            self._aim_eye = cam.pos
            try:
                self._aim_pt, blocker = getattr(self.world, 'task_aim', lambda k, e: (None, None))(key, cam.pos)
            except Exception:
                self._aim_pt, blocker = None, None
            if self._aim_pt is None and blocker is not None:
                self._clear_first(tuple(blocker), cam)
        if self.clearing is not None:
            return self._clear(ex, cam)
        # not to be reached from here (no point on it in reach and in sight; or never on the crosshair): right up
        # beside it first, once, before it's left for another (two ores beside the one just mined were left as
        # "could not mine it", and then "no more iron ore near here", 2026-10-15)
        k = tuple(key)
        hidden = hasattr(self.world, 'task_aim') and getattr(self, '_aim_pt', None) is None
        if self.held_at is None and k not in self.beside and (hidden or self.t - self.since > 2.0):
            self.beside.add(k)
            self._aim_eye = self._aim_pt = None
            try:
                self.world.task_walk(point, _name_, beside=key)
            except TypeError:                            # (a world that can't)
                self.world.task_walk(point, _name_)
            self._set('walk', 'closer: right beside it')
            return None
        turn, tilt, err = aim_at(cam, getattr(self, '_aim_pt', None) or centre)
        aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(key)
        keys, taps = set(), []
        if aimed:
            tool = ex.get('tool_slot', -1)
            if tool is not None and tool >= 0 and ex.get('slot') != tool and self.t - self.last_click > 0.3:
                taps.append(f'slot{tool + 1}')
                self.last_click = self.t
            keys.add('attack')
            self.held_at = self.held_at or self.t
        elif self.held_at is not None and err < 12.0:
            self._note('dug', at=tuple(key))
            self.got_before = (ex.get('inventory') or {}).get(item, 0)
            self.held_at = None
            self._aim_eye = self._aim_pt = None
            self._set('collect')
            return Intent()
        if self.t - self.since > MINE_TIMEOUT or (self.held_at is None and err < 2.0 and self.t - self.since > 2.0):
            self.skip.add(tuple(key))                  # can't mine it (or can't see it): another one
            self.held_at = None
            self._aim_eye = self._aim_pt = None
            self._set('find', 'could not mine it')
            return Intent()
        return Intent(frozenset(keys), turn, tuple(taps), tilt)

    def _creature(self, item: str, src) -> None:
        """What a creature gives: the nearest of that kind (a white sheep, not shorn) walked up to, then fought (its
        drops: how 'kill') or the tool used on it (shears: how 'use'); then the step starts again (what came of it
        picked up, then another, till there are enough)."""
        got = getattr(self.world, 'task_creature', lambda *a: None)(src.block, src.variant, self.skip)
        if got is None:
            self._fail(f"I can't find any {_name(src.block)} near here for {_name(item)}.", NO_TARGET, what=src.block,
                       item=item)
            return None
        point, name, ident = got
        self.target = (point, name, ident)
        if not getattr(self.world, 'task_seen', lambda i: True)(ident):
            # beyond sight (found by the game's search): walked toward as a place, and looked for again once in
            # view (hunted by its id out of sight, it was "gone" at once: 16 sheep twice each, 3 s apiece,
            # 2026-10-17)
            self.world.task_walk(point, name)
            self._set('seek', f'{name} ({ident})')
            return None
        self.hunted[ident] = self.hunted.get(ident, 0) + 1
        if self.hunted[ident] >= HUNT_TRIES:
            self.skip.add(ident)                         # (it got away twice, or it's been done: another)
        if src.how == 'kill':
            self.world.task_hunt(point, name, ident)
            self._set('hunt', f'{name} ({ident})')
        else:
            self.world.task_approach(point, name, ident)
            self._set('approach', f'{name} ({ident})')
        return None

    def _use(self, snap, ex, inv, src, block: bool) -> Intent | None:
        """The tool (or the hand) used on what's targeted, a block or a creature (right-clicked, crouched only on a
        block): once, then what came of it is picked up and the step goes on. (Every interaction the game's data
        has, the same way: a sheep shorn, an armadillo brushed, a pumpkin carved, berries picked.)"""
        point, name, key = self.target
        cam = snap.camera
        if block:
            if getattr(self, '_aim_eye', None) is None or math.dist(cam.pos, self._aim_eye) > 0.4:
                self._aim_eye = cam.pos
                try:
                    self._aim_pt, _blocker = self.world.task_aim(key, cam.pos)
                except Exception:
                    self._aim_pt = None
            at = getattr(self, '_aim_pt', None) or (point[0], point[1] + 0.5, point[2])
            on_it = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(key)
        else:
            e = next((x for x in snap.entities if x.id == key and not x.dead), None)
            if e is None:
                self._set('find', 'it went')
                return Intent()
            at = e.center
            on_it = snap.target_id == key
        turn, tilt, _err = aim_at(cam, at)
        taps = []
        hot = ex.get('hotbar') or []
        tool = self.world.task_tool(src.tool, inv) if src.tool else None
        slot = next((i for i, s in enumerate(hot) if s and s[0] == tool), None) if tool else None
        if slot is not None and ex.get('slot') != slot:
            if self.t - self.last_click > 0.3:
                taps.append(f'slot{slot + 1}')
                self.last_click = self.t
        elif on_it and self.t - self.last_click > 0.5:
            if self.tries >= 1:                          # used: what came of it is picked up, then another
                self.skip.add(tuple(key) if block else key)
                self._aim_eye = self._aim_pt = None
                self._set('find', 'used')
                return Intent()
            taps.append('interact')
            self.tries += 1
            self.last_click = self.t
        if self.t - self.since > USE_TIMEOUT:
            self.skip.add(tuple(key) if block else key)
            self._aim_eye = self._aim_pt = None
            self._set('find', 'could not use it')
            return Intent()
        return Intent(frozenset({'sneak'}) if block else frozenset(), turn, tuple(taps), tilt)

    def _clear_first(self, blocker: tuple, cam) -> None:
        """One block between the eye and what's to be mined (a leaf before a log): mined first, as the route planner
        reckoned when it planned the walk here (it counts one such block as no hindrance: blocks.MAX_BLOCKERS);
        unless it's the floor stood on, put down by the player (a station), already tried, or out of reach. (The
        planner said "in sight" for a log behind a leaf, the mining said "hidden": walks right up beside it, round
        a tree, and the step given up, 2026-10-16.)"""
        feet = math.floor(cam.pos[1] - 1.62 + 0.01)
        mem = getattr(self.world, 'memory', None)
        if blocker in self.cleared or blocker[1] < feet or \
                (mem is not None and any(tuple(p.at) == blocker for p in mem.stations())):
            return
        try:
            pt, _ = self.world.task_aim(blocker, cam.pos)
        except Exception:
            pt = None
        if pt is None:
            return
        self.cleared.add(blocker)
        self.clearing = (blocker, pt, self.t)

    def _clear(self, ex, cam) -> Intent:
        """Mining the block in the way (self.clearing): till the crosshair leaves it (it's gone), then the aim at
        what's to be mined again."""
        blocker, pt, t0 = self.clearing
        turn, tilt, err = aim_at(cam, pt)
        on_it = ex.get('aim_block') is not None and tuple(ex['aim_block']) == blocker
        if on_it:
            return Intent(frozenset({'attack'}), turn, (), tilt)
        if (err < 3.0 and self.t - t0 > 0.6) or self.t - t0 > CLEAR_TIMEOUT:
            self._note('dug', at=blocker)
            self.clearing = None
            self._aim_eye = self._aim_pt = None            # (looked for again from here)
            self.since = self.t
            return Intent()
        return Intent(frozenset(), turn, (), tilt)

    # ---- eating ----

    def _eat(self, snap, ex) -> Intent | None:
        """The best food into the hand and the use button held till one is eaten (about 1.6 s)."""
        slot = self.world.task_food(ex.get('hotbar') or [])
        if slot is None:
            self._fail("I've nothing to eat in the hotbar.", MISSING, item='food', count=1)
            return None
        if self.phase == 'start':
            self.food_before = sum(n for _i, n in [(s[0], s[1]) for s in (ex.get('hotbar') or []) if s and s[0]])
            self._set('eating')
        taps = []
        if ex.get('slot') != slot:
            if self.t - self.last_click > 0.3:
                taps.append(f'slot{slot + 1}')
                self.last_click = self.t
            return Intent(frozenset(), 0.0, tuple(taps))
        held = sum(n for _i, n in [(s[0], s[1]) for s in (ex.get('hotbar') or []) if s and s[0]])
        if held < self.food_before or self.t - self.since > 4.0:
            self.world.task_say('Mm, that helps!' if held < self.food_before else "I couldn't eat just now.")
            self.active = False
            self.events.append('done')
            self.result = 'done' if held < self.food_before else 'failed: could not eat'
            return Intent()
        return Intent(frozenset({'item'}))

    # ---- placing the station ----

    def _station(self, snap, ex, step) -> Intent | None:
        item = step[1]
        if self.phase == 'start':
            if ex.get('container'):
                return Intent(taps=('escape',)) if self.t - self.last_click > 0.6 and self._click_mark() else Intent()
            if self._need_in_hotbar(ex, item):
                return Intent()
            hot = ex.get('hotbar') or []
            if not any(s and s[0] == item for s in hot):
                self._fail(f"I don't have a {_name(item)} to place.", MISSING, item=item, count=1)
                return None
            self.bad_spots = set()
            self.spot = self._spot()
            if self.spot is None:
                self._fail(f"I can't find a spot to put the {_name(item)}.", NO_SPOT, item=item)
                return None
            self.station_had = (ex.get('inventory') or {}).get(item, 0)
            self._set('aim')
        placed, intent = self._put(snap, ex, item)
        if placed:
            self._note('put_down', item=item, at=tuple(self.spot[3]))
            self._next()
            return Intent()
        if self.tries > 3 or self.t - self.since > SPOT_TIME:
            # not going down there (a creature stepped in, the crosshair never got onto it): another spot
            self.bad_spots.add(tuple(into))
            self.spot = None if len(self.bad_spots) >= SPOTS else self._spot()
            if self.spot is None:
                self._fail(f"I couldn't place the {_name(item)}.", NO_SPOT, item=item, tried=len(self.bad_spots))
                return None
            self.tries = 0
            self._set('aim', f'another spot: {_at(self.spot[3])}')
        return intent

    def _put(self, snap, ex, item: str) -> tuple[bool, Intent]:
        """Putting item down at self.spot ((aim point, the block clicked, its face, the new block's cell)), crouched
        (what's clicked is never used): (put down, what to press)."""
        hot = ex.get('hotbar') or []
        slot = next((i for i, s in enumerate(hot) if s and s[0] == item), None)
        point, against, face, into = self.spot
        cam = snap.camera
        turn, tilt, err = aim_at(cam, point)
        taps = []
        # there: one fewer held, or the crosshair on it where it goes (not just on something there: a flower in
        # that spot was taken for the table, and the pickaxe after it found no table, 2026-10-13)
        aim = tuple(ex['aim_block']) if ex.get('aim_block') is not None else None
        fewer = (ex.get('inventory') or {}).get(item, 0) < getattr(self, 'station_had', 0)
        if fewer or (aim == tuple(into) and ex.get('aim_id') == item):
            return True, Intent()
        # what the crosshair is on decides (as in the game): the block below's face: put it there; grass in the
        # spot itself: put over it (it takes the grass's place); a plant in the way (no collision): knocked out
        # (a click). Grass between was never seen, and nothing was ever clicked, 2026-10-13.
        if slot is not None and ex.get('slot') != slot:
            if self.t - self.last_click > 0.3:
                taps.append(f'slot{slot + 1}')
                self.last_click = self.t
        elif aim is not None and self.t - self.last_click > 0.4:
            if (aim == tuple(against) and ex.get('aim_face') == face) or \
                    (aim == tuple(into) and ex.get('aim_replaceable')):
                taps.append('interact')
                self.tries += 1
                self.last_click = self.t
            elif aim != tuple(against) and ex.get('aim_solid') is False:
                taps.append('attack')
                self.last_click = self.t
        return False, Intent(frozenset({'sneak'}), turn, tuple(taps), tilt)

    # ---- into the off hand (a shield) ----

    def _offhand(self, ex, inv, step) -> Intent | None:
        """('offhand', item): into the off hand (a shield, held up with the use button while the sword's in the
        main hand): picked in the hotbar, then the swap-hands key."""
        item = step[1]
        if ex.get('offhand') == item:
            self._next()
            return Intent()
        if inv.get(item, 0) <= 0:
            self._fail(f"I don't have a {_name(item)} to hold.", MISSING, item=item, count=1)
            return None
        if self._need_in_hotbar(ex, item):
            return Intent()
        hot = ex.get('hotbar') or []
        slot = next((i for i, s in enumerate(hot) if s and s[0] == item), None)
        if self.t - self.last_click < 0.5:
            return Intent()
        self.last_click = self.t
        if slot is not None and ex.get('slot') != slot:
            return Intent(taps=(f'slot{slot + 1}',))
        self.tries += 1
        if self.tries > 3:
            self._fail(f"I couldn't get the {_name(item)} into the off hand.", UI, what='off hand')
            return None
        return Intent(taps=('swap_hands',))

    def _use_on(self, snap, ex, inv, step) -> Intent | None:
        """('use', 'block' | 'creature', the block (the game's coordinates) or the creature's id, where it is
        (snapshot coordinates), the item to use (None: an empty hand), what it is): right-clicked, as a player
        does: a bed slept in, a door, a lever, a button, a chest opened, a saddle put on a horse, the horse
        mounted. Held first (or an empty hand: holding food, a horse is fed, not ridden), walked to where it's in
        reach and in sight, aimed at, used once, never crouched (crouched, what's held is used instead: a block
        put down). ("Sleep in the bed": "I can't lie down in it myself", 2026-10-17.)"""
        _u, kind, key, point, item, label = step
        cam = snap.camera
        if self.phase == 'start':
            if ex.get('container'):
                return Intent(taps=('escape',)) if self.t - self.last_click > 0.6 and self._click_mark() else Intent()
            hot = ex.get('hotbar') or []
            if item:
                if inv.get(item, 0) <= 0:
                    self._fail(f"I don't have a {_name(item)} to use on the {label}.", MISSING, item=item, count=1)
                    return None
                if self._need_in_hotbar(ex, item):
                    return Intent()
                slot = next((i for i, s in enumerate(hot) if s and s[0] == item), None)
            else:
                slot = next((i for i, s in enumerate(hot) if not s or not s[0]), None)
            if slot is not None and ex.get('slot') != slot:
                ready = self.t - self.last_click > 0.3 and self._click_mark()
                return Intent(taps=(f'slot{slot + 1}',)) if ready else Intent()
            self.tries, self._aim_eye, self._aim_pt = 0, None, None
            if math.dist(cam.pos, point) <= USE_REACH:
                self._set('aim', 'in reach')
            elif kind == 'creature':
                self.world.task_approach(point, label, key)
                self._set('go')
            else:
                try:
                    self.world.task_walk(point, label, beside=tuple(key))   # (right beside it: not into a bed)
                except TypeError:
                    self.world.task_walk(point, label)
                self._set('go')
            return Intent()
        if self.phase == 'go':
            return None
        if self.phase == 'aim':
            if kind == 'block':
                if self._aim_eye is None or math.dist(cam.pos, self._aim_eye) > 0.4:
                    self._aim_eye = cam.pos
                    try:
                        self._aim_pt, _blocker = self.world.task_aim(tuple(key), cam.pos)
                    except Exception:
                        self._aim_pt = None
                at = self._aim_pt or (point[0], point[1] + 0.5, point[2])
                on_it = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(key)
            else:
                e = next((x for x in snap.entities if x.id == key and not x.dead), None)
                if e is None:
                    self._fail(f"The {label} is gone.", NO_TARGET, what=label)
                    return None
                at, on_it = e.center, snap.target_id == key
            turn, tilt, _err = aim_at(cam, at)
            if on_it and self.t - self.last_click > 0.3:
                self.last_click = self.t
                self._set('used')
                return Intent(frozenset(), turn, ('interact',), tilt)
            if self.t - self.since > USE_TIMEOUT:
                self._fail(f"I couldn't get the {label} in my sights to use it.", UNREACHABLE, what=label)
                return None
            return Intent(frozenset(), turn, (), tilt)
        if self.phase == 'used':
            if self.t - self.since > 0.6:
                self._next()
            return Intent()
        return Intent()

    def _equip(self, ex, inv, step) -> Intent | None:
        """('equip', item): worn (a piece of armour): picked in the hotbar and used, looking up (used on a block in
        front, it would open that instead): the game puts it on, swapping it for what's worn there. Worse than what
        was worn (the armour points fewer after): used again, swapped back. ("Equip my armour": "my tools can only
        sort the hotbar", 2026-10-17.)"""
        item = step[1]
        armor = ex.get('armor')
        if self.phase == 'start':
            if inv.get(item, 0) <= 0:
                self._fail(f"I don't have a {_name(item)} to put on.", MISSING, item=item, count=1)
                return None
            if ex.get('container'):
                return Intent(taps=('escape',)) if self.t - self.last_click > 0.6 and self._click_mark() else Intent()
            if self._need_in_hotbar(ex, item):
                return Intent()
            hot = ex.get('hotbar') or []
            slot = next((i for i, s in enumerate(hot) if s and s[0] == item), None)
            if slot is None:
                return Intent()
            if ex.get('slot') != slot:
                ready = self.t - self.last_click > 0.3 and self._click_mark()
                return Intent(taps=(f'slot{slot + 1}',)) if ready else Intent()
            self.worn_before = (armor, inv.get(item, 0))
            self._set('wear')
            return Intent(pitch=-EQUIP_LOOK_UP)
        if self.phase == 'wear':
            if self.t - self.since < 0.25:
                return Intent(pitch=-EQUIP_LOOK_UP)       # (looking up first)
            self._set('check')
            self.last_click = self.t
            return Intent(taps=('interact',))
        if self.phase == 'check':
            before, had = self.worn_before
            if armor is not None and before is not None and armor < before:
                self._set('back')                        # (worse than what was worn: swapped back)
                return Intent(taps=('interact',))
            if (armor is not None and before is not None and armor > before) or inv.get(item, 0) < had:
                self._next()
                return Intent()
            if self.t - self.since > 1.0:
                self.tries += 1
                if self.tries > 2:
                    if armor:                            # (nothing changed, armour on: one as good is worn there,
                        self._next()                     # swapped for its like)
                        return Intent()
                    self._fail(f"I couldn't put the {_name(item)} on.", UI, what='armour')
                    return None
                self._set('wear')
            return Intent()
        if self.phase == 'back':
            if self.t - self.since > 0.5:
                self._next()
            return Intent()
        return Intent()

    # ---- building: one block put into its place ----

    def _place(self, snap, ex, inv, step) -> Intent | None:
        """A block of a building into its cell (('place', cell, item, point): the game's coordinates, and the
        cell's middle as the walk takes it): from where it's in reach and in sight (walked to, not standing in
        it), put down against a block beside it (core/build.py ordered them so there's one)."""
        _p, cell, item, point = step
        cell = tuple(cell)
        if self.phase == 'start':
            if ex.get('container'):
                return Intent(taps=('escape',)) if self.t - self.last_click > 0.6 and self._click_mark() else Intent()
            if inv.get(item, 0) <= 0:
                self._fail(f"I've run out of {_name(item)} to build with.", MISSING, item=item, count=1)
                return None
            if self._need_in_hotbar(ex, item):
                return Intent()
            self.station_had = inv.get(item, 0)
            try:
                self.spot = self.world.task_place_spot(set(), only=cell)
            except TypeError:
                self.spot = None
            if self.spot is None:
                if self.walked_to_station >= PLACE_WALKS:
                    self._fail(f"I can't get to where I can put the {_name(item)} at {_at(cell)}.", UNREACHABLE,
                               what=item, at=cell, frm=self.pos)
                    return None
                self.walked_to_station += 1
                self.world.task_walk(point, _name(item), place=cell)
                self._set('walk_place', _at(cell))
                return None
            self.tries = 0
            self._set('aim', _at(cell))
        if self.phase == 'walk_place':
            return None
        placed, intent = self._put(snap, ex, item)
        if placed:
            self.built.add(cell)
            self._next()
            return Intent()
        if self.tries > 3 or self.t - self.since > SPOT_TIME:
            self.place_tries += 1                      # (a creature in it, the crosshair never on it): again
            if self.place_tries > PLACE_TRIES:
                self._fail(f"I couldn't put the {_name(item)} at {_at(cell)}.", NO_SPOT, item=item, at=cell)
                return None
            self._set('start', 'again')
            return Intent()
        return intent

    def _spot(self):
        try:
            return self.world.task_place_spot(self.bad_spots)
        except TypeError:                              # (a world with no exclude)
            return self.world.task_place_spot()


    def _reach_station(self, snap, item: str) -> bool:
        """The station (a crafting table, a furnace) is in reach and in sight from here (True: self.target is it),
        or it's being walked to (False; or the task has failed, and said why). Used where it stands only when a
        point on it is in sight from the eye (near wasn't enough: from the bottom of a pit it was behind the wall,
        and never opened, 2026-10-13); else walked to where it is (the walk's goal: in reach and in sight); not in
        sight there after all (a line of sight that only just clears a block): right up beside it."""
        got = self.world.task_find((item,), set())
        if got is None:
            self._fail(f"I can't find a {_name(item)}.", NO_TARGET, what=item)
            return False
        self.target = got
        self._station_aim(snap)
        if self.station_pt is not None:
            return True
        if self.walked_to_station >= 2:
            self._fail(f"I can't get to where I can reach the {_name(item)} at {_at(got[2])}: something's in the "
                       f"way.", UNREACHABLE, what=item, at=tuple(got[2]), frm=self.pos)
            return False
        self.walked_to_station += 1
        if self.walked_to_station == 2:
            try:
                self.world.task_walk(got[0], got[1], beside=got[2])
            except TypeError:                            # (a world that can't)
                self.world.task_walk(got[0], got[1])
        else:
            self.world.task_walk(got[0], got[1])
        self._set('to_station')
        return False

    def _open_station(self, snap, ex) -> Intent:
        """Aim at the station (self.target) and click it, once a second while the crosshair is on it."""
        point, _n, key = self.target
        self._station_aim(snap)
        if self.station_pt is None and self.t - self.since > 1.0:
            self._set('start', 'not in sight from here')   # (walked there once; else it's said why)
            return Intent()
        turn, tilt, err = aim_at(snap.camera, self.station_pt or (point[0], point[1] + 0.5, point[2]))
        taps = ()
        if ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(key) and self.t - self.last_click > 1.0:
            taps = ('interact',)
            self.last_click = self.t
        return Intent(frozenset(), turn, taps, tilt)

    # ---- cooking (a furnace) ----

    def _smelt(self, snap, ex, inv, step) -> Intent | None:
        """Cooking in a station (a furnace): to it (in reach, in sight), open it, what's to cook into its input and
        the fuel into its fuel slot (as many as the plan says; anything else there out first), then wait, taking
        what's done out (Shift-click) as it comes, till there's as much as wanted."""
        rec, times, fuel, n_fuel = step[1], step[2], step[3], step[4]
        screen = ex.get('container')
        carried = (screen or {}).get('carried') or ''
        have = inv.get(rec.result, 0) + ((screen or {}).get('carried_count') or 0 if carried == rec.result else 0)
        name = _name(rec.station or 'furnace')
        if self.want is None:
            self.base = inv.get(rec.result, 0)
            self.want = self.base + times * rec.count
        if self.phase == 'start':
            if screen:
                self._set('close_first')
                return Intent(taps=('escape',))
            if not self._reach_station(snap, rec.station):
                return None
            self._set('open')
        if self.phase == 'close_first':
            if not screen:
                self._set('start')
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        if self.phase == 'to_station':
            return None
        if self.phase == 'open':
            if screen and any(s[5] == 'input' for s in _slots(screen)):
                self._set('settle')
                return Intent()
            if self.t - self.since > OPEN_TIMEOUT * 3:
                self._fail(f"I couldn't open the {name} at {_at(self.target[2])}: the crosshair never got onto it.")
                return None
            return self._open_station(snap, ex)
        if self.phase == 'settle':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.t - self.since < SETTLE:
                return Intent()
            slots = _slots(screen)
            inp = next(s for s in slots if s[5] == 'input')
            fu = next(s for s in slots if s[5] == 'fuel')
            fits = rec.cells[0]
            other = [s for s in (inp, fu) if s[3] and not ((s is inp and s[3] in fits) or (s is fu and s[3] == fuel))]
            if other:                                    # something else in it: out first (Shift-click)
                self.clicks = [(s[0], s[1], s[2], 'left') for s in other]
                self._set('clear', json_short({'out': [s[3] for s in other]}))
                return Intent(frozenset({'sneak'}))
            n_in = max(0, times - (inp[4] if inp[3] in fits else 0))
            n_fu = max(0, n_fuel - (fu[4] if fu[3] == fuel else 0))
            clicks = []
            for target, kinds, n, what in ((inp, fits, n_in, fits[0]), (fu, (fuel,), n_fu, fuel)):
                if n:
                    got = _put_clicks(slots, target, kinds, n)
                    if got is None:
                        self._fail(f"I don't have the {_name(what)} for the {name}.", MISSING, item=what, count=n)
                        return Intent(taps=('escape',))
                    clicks += got
            self.clicks = clicks
            self.out_since = None
            self._set('fill', json_short({'clicks': clicks}))
            return Intent()
        if self.phase == 'clear':
            if self.t - self.since < SHIFT_FIRST:
                return Intent(frozenset({'sneak'}))
            got = self._clicking(screen)
            if got == 'failed':
                self._fail(f"I couldn't get the pointer onto the {name}'s slots. Over to you!")
                return Intent(taps=('escape',))
            if got == 'done':
                self._set('settle', 'again')
            return Intent(frozenset({'sneak'}))
        if self.phase == 'fill':
            got = self._clicking(screen)
            if got == 'failed':
                self._fail(f"I couldn't get the pointer onto the {name}'s slots. Over to you!")
                return Intent(taps=('escape',))
            if got == 'done':
                self._set('cook')
            return Intent()
        if self.phase == 'cook':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.clicks:                              # taking what's done out (Shift held)
                if self._clicking(screen) == 'failed':
                    self._fail(f"I couldn't get the pointer onto the {name}'s slots. Over to you!")
                    return Intent(taps=('escape',))
                return Intent(frozenset({'sneak'}))
            if have >= self.want:
                self._set('close', f'have {have}')
                return Intent(taps=('escape',))
            slots = _slots(screen)
            res = next((s for s in slots if s[5] == 'result'), None)
            inp = next((s for s in slots if s[5] == 'input'), None)
            if res and res[3] == rec.result and self.t - self.last_click > 0.8 and self.t - self.since > SHIFT_FIRST:
                self.clicks = [(res[0], res[1], res[2], 'left')]
                return Intent(frozenset({'sneak'}))
            if self.t - self.since > times * max(1, rec.time) / 20.0 + COOK_SPARE:
                self._fail(f"The {name} didn't finish: {have - self.base} {_name(rec.result)} out of "
                           f"{self.want - self.base}.", TIMEOUT, what=rec.result)
                return Intent(taps=('escape',))
            # out, with something still to cook: out of fuel
            out = screen.get('lit') is False and inp is not None and inp[3]
            self.out_since = (self.out_since or self.t) if out else None
            if self.out_since is not None and self.t - self.out_since > NO_FUEL_S:
                self._fail(f"The {name} ran out of fuel with {inp[4]} {_name(inp[3])} still to cook.", MISSING,
                           item=fuel, count=1, fuel=True)
                return Intent(taps=('escape',))
            return Intent(frozenset({'sneak'}))
        if self.phase == 'close':
            if not screen:
                self._next()
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        return None

    def _station_aim(self, snap) -> None:
        """Where to aim at the station (self.target) from the eye now: a point on it in reach and in sight, or
        None. Asked again when the eye has moved."""
        cam = snap.camera
        if cam is None or self.target is None:
            self.station_pt = None
            return
        if self.station_eye is not None and math.dist(cam.pos, self.station_eye) < 0.3:
            return
        self.station_eye = cam.pos
        aim = getattr(self.world, 'task_aim', None)
        if aim is None:                                # (no way to look: near enough is taken as in reach)
            p = self.target[0]
            self.station_pt = (p[0], p[1] + 0.5, p[2]) if math.dist(snap.player.pos, p) <= REACH else None
            return
        try:
            self.station_pt = aim(self.target[2], cam.pos)[0]
        except Exception:
            self.station_pt = None

    def _click_mark(self) -> bool:
        self.last_click = self.t
        return True

    # ---- crafting ----

    def _craft(self, snap, ex, inv, step, walking) -> Intent | None:
        rec, times = step[1], step[2]
        screen = ex.get('container')
        big = rec.grid > 2
        carried = (screen or {}).get('carried') or ''
        have = inv.get(rec.result, 0) + ((screen or {}).get('carried_count') or 0 if carried == rec.result else 0)
        if self.want is None:
            self.base = inv.get(rec.result, 0)
            self.want = self.base + times * rec.count
        if self.phase == 'start':
            if screen:                                  # something else open: close it first
                self._set('close_first')
                return Intent(taps=('escape',))
            if big and not self._reach_station(snap, 'minecraft:crafting_table'):
                return None
            self._set('open')
        if self.phase == 'close_first':
            if not screen:
                self._set('start')
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        if self.phase == 'to_station':
            return None
        if self.phase == 'open':
            want_menu = 'CraftingMenu' if big else 'InventoryMenu'
            if screen and screen.get('menu') == want_menu:
                self._set('settle')                     # (a screen just opened: let it fill in first)
                return Intent()
            if self.t - self.since > OPEN_TIMEOUT * (3 if big else 1):
                if big and self.target is not None:
                    self._fail(f"I couldn't open the crafting table at {_at(self.target[2])}: the crosshair "
                               f"never got onto it.")
                else:
                    self._fail("I couldn't open the crafting screen.")
                return None
            if not big:
                if self.t - self.last_click > 1.0:
                    self.last_click = self.t
                    return Intent(taps=('inventory',))
                return Intent()
            return self._open_station(snap, ex)
        if self.phase == 'settle':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.t - self.since < SETTLE:
                return Intent()
            self.clicks = _fill_clicks(screen, rec, times)
            if self.clicks is None:
                self._fail(f"I don't have what the {_name(rec.result)} takes.", MISSING, item=rec.result, takes=True)
                return Intent(taps=('escape',))
            self._set('fill', json_short({'clicks': self.clicks, 'screen': _grid_view(screen)}))
            return Intent()
        if self.phase == 'fill':
            got = self._clicking(screen)
            if got == 'failed':
                self._fail("I couldn't get the pointer onto the crafting grid. Over to you!")
                return Intent(taps=('escape',))
            if got == 'done':
                self._set('check')
            return Intent()
        if self.phase == 'check':                      # is the grid as the recipe wants it?
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.t - self.since < SETTLE:
                return Intent()
            wrong = _grid_wrong(screen, rec, times)
            if not wrong:
                self._set('take')
                return Intent()
            self.tries += 1
            if self.tries > 1:
                self._fail(f"I couldn't lay out the {_name(rec.result)} in the grid. Over to you!")
                return Intent(taps=('escape',))
            self.clicks = [(sl[0], sl[1], sl[2], 'left') for sl in _slots(screen)
                           if str(sl[5]).startswith('grid') and sl[3]]     # each back to the inventory (Shift)
            self._set('clear', json_short({'wrong': wrong, 'screen': _grid_view(screen)}))
            return Intent(frozenset({'sneak'}))
        if self.phase == 'clear':
            if self.t - self.since < SHIFT_FIRST:
                return Intent(frozenset({'sneak'}))
            got = self._clicking(screen)
            if got == 'failed':
                self._fail("I couldn't get the pointer onto the crafting grid. Over to you!")
                return Intent(taps=('escape',))
            if got == 'done':
                self._set('settle', 'again')
            return Intent(frozenset({'sneak'}))
        if self.phase == 'take':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.clicks:                             # a click under way (onto the result, or putting down)
                if self._clicking(screen) == 'failed':
                    self._fail("I couldn't get the pointer onto the right slot. Over to you!")
                    return Intent(taps=('escape',))
                return Intent(frozenset({'sneak'}))
            if carried:                                 # on the cursor (a click that picked it up): put it down
                if self.t - self.last_click > CLICK_GAP * 2:
                    empty = next((s for s in _slots(screen) if str(s[5]).startswith(('main', 'hotbar'))
                                  and (not s[3] or s[3] == carried)), None)
                    if empty is not None:
                        self.clicks = [(empty[0], empty[1], empty[2], 'left')]
                return Intent(frozenset({'sneak'}))
            res = next((s for s in _slots(screen) if s[5] == 'result'), None)
            if have >= self.want or (res and not res[3] and self.t - self.since > 0.6 and have > self.base):
                self._set('close', f'have {have}')
                return Intent(taps=('escape',))
            if res and res[3] == rec.result and self.t - self.since >= SHIFT_FIRST \
                    and self.t - self.last_click > 0.4:
                self.clicks = [(res[0], res[1], res[2], 'left')]       # Shift held: as many as the grid makes
            elif self.t - self.since > 4.0 and self.t - self.last_click > 1.0:
                self.trace.append({'t': round(self.t, 2), 'phase': 'take', 'note': json_short(_grid_view(screen))})
                self._fail(f"The {_name(rec.result)} didn't come out. Over to you!")
                return Intent(taps=('escape',))
            return Intent(frozenset({'sneak'}))          # (Shift, held before the click)
        if self.phase == 'close':
            if not screen:
                self._next()
            elif self.t - self.since > 1.0:
                self.since = self.t
                return Intent(taps=('escape',))
            return Intent()
        return None


def json_short(o) -> str:
    import json
    return json.dumps(o, default=str)[:1500]


def _grid_view(screen: dict) -> dict:
    """The grid, the result and the cursor, for the task log: {role: [item, count, x, y]}."""
    out = {s[5]: [s[3], s[4], s[1], s[2]] for s in _slots(screen) if str(s[5]).startswith(('grid', 'result'))}
    out['carried'] = [(screen or {}).get('carried'), (screen or {}).get('carried_count')]
    out['menu'] = (screen or {}).get('menu')
    return out


def _cells(rec, g: int) -> dict:
    """Grid index -> the items that fit there (the recipe at the grid's top left)."""
    if rec.shapeless:
        return {i: c for i, c in enumerate(c for c in rec.cells if c)}
    return {r * g + c: rec.cells[r * rec.w + c] for r in range(rec.h) for c in range(rec.w)
            if rec.cells[r * rec.w + c]}


def _grid_wrong(screen: dict, rec, times: int) -> list:
    """What in the grid differs from the recipe laid out `times` deep: [(index, has, wants)]."""
    grid = {int(s[5][4:]): s for s in _slots(screen) if str(s[5]).startswith('grid')}
    g = 3 if len(grid) >= 9 else 2
    want = _cells(rec, g)
    wrong = []
    for i, s in grid.items():
        fits = want.get(i)
        if fits is None:
            if s[3]:
                wrong.append((i, s[3], ''))
        elif s[3] not in fits or s[4] < times:
            wrong.append((i, f'{s[4]} {s[3]}', fits[0]))
    return wrong


def _put_clicks(slots: list, target, fits: tuple, n: int) -> list | None:
    """The clicks that put n of one kind of what fits into a slot (a furnace's input, its fuel): pick a held stack
    up, one into the slot per right click, the rest put back. None: not that many held of one kind."""
    held: dict = {}
    for s in slots:
        if str(s[5]).startswith(('hotbar', 'main')) and s[3] in fits and s[4] > 0:
            held.setdefault(s[3], []).append(s)
    kinds = sorted(held.values(), key=lambda ss: -sum(s[4] for s in ss))
    if not kinds or sum(s[4] for s in kinds[0]) < n:
        return None
    src = sorted(kinds[0], key=lambda s: -s[4])
    clicks, carried, cur = [], 0, None
    for _ in range(n):
        if carried == 0:
            cur = src.pop(0)
            clicks.append((cur[0], cur[1], cur[2], 'left'))
            carried = cur[4]
        clicks.append((target[0], target[1], target[2], 'right'))
        carried -= 1
    if carried > 0:
        clicks.append((cur[0], cur[1], cur[2], 'left'))
    return clicks


def _fill_clicks(screen: dict, rec, times: int) -> list | None:
    """The clicks that put `times` of each ingredient into the grid: [(slot index, x, y, 'left'|'right')], or None
    when what's held isn't enough. Slots: [index, x, y, item, count, role]."""
    slots = _slots(screen)
    grid = {int(s[5][4:]): s for s in slots if str(s[5]).startswith('grid')}
    g = 3 if len(grid) >= 9 else 2
    held = [s for s in slots if str(s[5]).startswith(('hotbar', 'main')) and s[3]]
    cells = list(_cells(rec, g).items())
    if any(i not in grid for i, _c in cells):
        return None
    groups: dict = {}
    for i, fits in cells:
        groups.setdefault(fits, []).append(i)
    clicks = []
    left = {s[0]: s[4] for s in held}
    for fits, targets in groups.items():
        need = len(targets) * times
        by_kind: dict = {}
        for s in held:
            if s[3] in fits and left[s[0]] > 0:
                by_kind.setdefault(s[3], []).append(s)
        # one kind of item for the group (all the planks the same), the kind there's most of
        kinds = sorted(by_kind.items(), key=lambda kv: -sum(left[s[0]] for s in kv[1]))
        if not kinds or sum(left[s[0]] for s in kinds[0][1]) < need:
            return None
        src = sorted(kinds[0][1], key=lambda s: -left[s[0]])
        order = [t for _ in range(times) for t in targets]
        carried, cur = 0, None
        for t in order:
            if carried == 0:
                cur = src.pop(0)
                clicks.append((cur[0], cur[1], cur[2], 'left'))   # pick the stack up
                carried, left[cur[0]] = left[cur[0]], 0
            slot = grid[t]
            clicks.append((slot[0], slot[1], slot[2], 'right'))  # one into this cell
            carried -= 1
        if carried > 0:
            clicks.append((cur[0], cur[1], cur[2], 'left'))      # the rest back where it was
            left[cur[0]] = carried
    return clicks
