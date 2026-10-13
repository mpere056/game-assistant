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
from typing import Protocol

from .autowalk import Intent, aim_at

CLICK_GAP = 0.15        # seconds between clicks on a screen (each must reach the game before the next)
POINT_WAIT = 0.08       # seconds after moving the pointer before asking the game what's under it
POINT_TRIES = 4
POINT_SETTLE = 0.2      # seconds after moving the pointer before a click, where the game can't say what's under it
SHIFT_FIRST = 0.15      # seconds Shift is held before a shift-click (pressed in the same instant, the game
                        # missed it and the click picked the result up instead, 2026-10-13)
MINE_TIMEOUT = 25.0
OPEN_TIMEOUT = 3.0
COLLECT_WAIT = 1.2      # seconds for a mined block's drop to come to the player
PICKUP_RANGE = 6.0      # blocks: dropped items this near are walked to
PICKUP_TRIES = 2        # times one dropped item is walked to before it's left (it didn't come: out of reach)
REACH = 4.3             # blocks: a station this near is used where it stands
STEP_TIMEOUT = 300.0
SETTLE = 0.4            # seconds for a screen to fill in (just opened), or the grid after the last click


class TaskWorld(Protocol):
    def task_find(self, blocks: tuple, exclude: set) -> tuple | None: ...   # (point, name, key): nearest
    def task_items(self, items: tuple, radius: float) -> list: ...          # [point]: dropped items near
    def task_walk(self, point, name: str) -> None: ...
    def task_stop(self) -> None: ...
    def task_say(self, line: str) -> None: ...
    def task_point(self, x: float, y: float) -> None: ...          # the pointer to (x, y): the game window's pixels
    def task_press(self, button: str) -> None: ...                 # click where the pointer is
    def task_place_spot(self) -> tuple | None: ...   # (aim point, against key, face, new block's key)
    def task_tool(self, need: str, have: dict) -> str | None: ...   # the held tool that will do for `need`


def _cell(p) -> tuple:
    return (math.floor(p[0]), math.floor(p[1] + 0.01), math.floor(p[2]))


def _name(item: str) -> str:
    return item.split(':')[-1].replace('_', ' ')


def _slots(screen: dict) -> list:
    return (screen or {}).get('slots', [])


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
        self.calib = [0.0, 0.0]     # what the pointer turned out to be off by (window pixels): corrected for
        self.pointing = None

    # ---- control ----

    def start(self, steps: list, goal: str, count: int) -> None:
        self.steps, self.i, self.goal, self.count = list(steps), 0, goal, count
        self.active, self.t, self.result = True, 0.0, ''
        self._begin()

    def stop(self, why: str = '') -> None:
        if self.active:
            self.active = False
            self.world.task_stop()
            self.result = f'stopped: {why}' if why else 'stopped'
            if why:
                self.events.append(f'stopped:{why}')

    def describe(self) -> str:
        bits = []
        for s in self.steps:
            if s[0] == 'gather':
                bits.append(f'get {s[2]} {_name(s[1])}')
            elif s[0] == 'craft':
                bits.append(f'craft {s[1].count * s[2]} {_name(s[1].result)}')
            elif s[0] == 'station':
                bits.append(f'place a {_name(s[1])}')
        return ', '.join(bits)

    def _begin(self) -> None:
        self.base, self.want = None, None
        self.skip: set = set()
        self.clicks: list = []
        self.last_click = -1e9
        self.target = None
        self.held_at = None
        self.tries = 0
        self.hotbar = None          # moving an item into the hotbar: (item, phase)
        self.item_tries: dict = {}  # dropped items walked to (by their block): how often (one may not come)
        self._set('start')

    def _set(self, phase: str, note: str = '') -> None:
        self.phase, self.since = phase, self.t
        step = self.steps[self.i] if self.active and self.i < len(self.steps) else None
        what = None
        if step is not None:
            what = f'{step[0]} {_name(step[1].result) if step[0] == "craft" else _name(step[1])}'
        self.trace.append({'t': round(self.t, 2), 'step': self.i, 'what': what, 'phase': phase, 'note': note})

    def _next(self) -> None:
        self.i += 1
        if self.i >= len(self.steps):
            self.active = False
            self.events.append('done')
            self.result = 'done'
            self.trace.append({'t': round(self.t, 2), 'phase': 'done'})
            self.world.task_say(f'Done! You have your {_name(self.goal)}.')
            return
        self._begin()

    def _fail(self, line: str) -> None:
        self.active = False
        self.world.task_stop()
        self.events.append('failed')
        self.result = f'failed: {line}'
        self.trace.append({'t': round(self.t, 2), 'phase': 'failed', 'note': line})
        self.world.task_say(line)

    def resume(self) -> None:
        """After something interrupted it (a fight): the step again from its start (what's held counts)."""
        if self.active:
            self._set('start', 'resumed')
            self.held_at = None
            self.clicks, self.pointing, self.hotbar = [], None, None

    def on_event(self, ev: str) -> None:
        """The walk's events: 'arrived', 'gave_up'."""
        if not self.active:
            return
        if ev == 'arrived' and self.phase in ('walk', 'pickup', 'to_station'):
            self._set({'walk': 'mine', 'pickup': 'find', 'to_station': 'open'}[self.phase])
        elif ev == 'gave_up' and self.phase in ('walk', 'to_station'):
            if self.target is not None and self.target[2] is not None:
                self.skip.add(tuple(self.target[2]))   # can't get to that one: another
            self._set('find' if self.phase == 'walk' else 'start', 'gave up walking there')
        elif ev == 'gave_up' and self.phase == 'pickup':
            self._set('find')

    # ---- every frame ----

    def update(self, snap, dt: float, walking: bool) -> Intent | None:
        """What to press now (None: nothing; the walk, when walking, presses its own)."""
        self.t += dt
        if not self.active:
            return None
        if self.t - self.since > STEP_TIMEOUT:
            self._fail("This is taking too long. Over to you!")
            return None
        step = self.steps[self.i]
        ex = snap.extra or {}
        inv = ex.get('inventory') or {}
        if self.hotbar is not None:
            return self._to_hotbar(ex)
        if step[0] == 'gather':
            return self._gather(snap, ex, inv, step, walking)
        if step[0] == 'station':
            return self._station(snap, ex, step)
        return self._craft(snap, ex, inv, step, walking)

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
                    self._fail(f'I need a {_name(src.tool)} for {_name(item)}.')
                    return None
                if self._need_in_hotbar(ex, tool):
                    return Intent()
            self._set('find')
        if self.phase == 'find':
            near = [p for p in self.world.task_items(src.drops, PICKUP_RANGE)
                    if self.item_tries.get(_cell(p), 0) < PICKUP_TRIES]
            if near:                                   # dropped already (by this, or lying about)
                self.item_tries[_cell(near[0])] = self.item_tries.get(_cell(near[0]), 0) + 1
                self.target = (near[0], _name(item), None)
                self.world.task_walk(near[0], _name(item))
                self._set('pickup')
                return None
            got = self.world.task_find((src.block,), self.skip)
            if got is None:
                self._fail(f"I can't find any more {_name(src.block)} near here.")
                return None
            self.target = got
            self.world.task_walk(got[0], got[1])
            self._set('walk', f'{got[1]} at {got[2]}')
            return None
        if self.phase in ('walk', 'pickup'):
            if self.phase == 'pickup' and self.t - self.since > 8.0:
                self._set('find')
            return None
        if self.phase == 'mine':
            return self._mine(snap, ex, item)
        if self.phase == 'collect':
            if inv.get(item, 0) > self.got_before or self.t - self.since > COLLECT_WAIT:
                self._set('find')
            return Intent()
        return None

    def _mine(self, snap, ex, item) -> Intent:
        point, _name_, key = self.target
        cam = snap.camera
        centre = (point[0], point[1] + 0.5, point[2])
        turn, tilt, err = aim_at(cam, centre)
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
            self.got_before = (ex.get('inventory') or {}).get(item, 0)
            self.held_at = None
            self._set('collect')
            return Intent()
        if self.t - self.since > MINE_TIMEOUT or (self.held_at is None and err < 2.0 and self.t - self.since > 2.0):
            self.skip.add(tuple(key))                  # can't mine it (or can't see it): another one
            self.held_at = None
            self._set('find', 'could not mine it')
            return Intent()
        return Intent(frozenset(keys), turn, tuple(taps), tilt)

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
                self._fail(f"I don't have a {_name(item)} to place.")
                return None
            self.spot = self.world.task_place_spot()
            if self.spot is None:
                self._fail(f"I can't find a spot to put the {_name(item)}.")
                return None
            self._set('aim')
        hot = ex.get('hotbar') or []
        slot = next((i for i, s in enumerate(hot) if s and s[0] == item), None)
        point, against, face, into = self.spot
        cam = snap.camera
        turn, tilt, err = aim_at(cam, point)
        taps = []
        if ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(into):
            self._next()                               # it's there
            return Intent()
        if slot is not None and ex.get('slot') != slot and self.t - self.last_click > 0.3:
            taps.append(f'slot{slot + 1}')
            self.last_click = self.t
        elif ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(against) \
                and ex.get('aim_face') == face and self.t - self.last_click > 0.5:
            taps.append('interact')
            self.last_click = self.t
            self.tries += 1
        if self.tries > 4 or self.t - self.since > 10.0:
            self._fail(f"I couldn't place the {_name(item)}.")
            return None
        return Intent(frozenset({'sneak'}), turn, tuple(taps), tilt)

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
            if big:
                got = self.world.task_find(('minecraft:crafting_table',), set())
                if got is None:
                    self._fail("I can't find a crafting table.")
                    return None
                self.target = got
                me = snap.player.pos
                if math.dist(me, got[0]) > REACH:
                    self.world.task_walk(got[0], got[1])
                    self._set('to_station')
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
                self._fail("I couldn't open the crafting screen.")
                return None
            if not big:
                if self.t - self.last_click > 1.0:
                    self.last_click = self.t
                    return Intent(taps=('inventory',))
                return Intent()
            point, _n, key = self.target
            turn, tilt, err = aim_at(snap.camera, (point[0], point[1] + 0.5, point[2]))
            taps = ()
            if ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(key) \
                    and self.t - self.last_click > 1.0:
                taps = ('interact',)
                self.last_click = self.t
            return Intent(frozenset(), turn, taps, tilt)
        if self.phase == 'settle':
            if not screen:
                self._set('start', 'the screen closed')
                return Intent()
            if self.t - self.since < SETTLE:
                return Intent()
            self.clicks = _fill_clicks(screen, rec, times)
            if self.clicks is None:
                self._fail(f"I don't have what the {_name(rec.result)} takes.")
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
