"""Plans: an F10 order of several parts, as steps code runs one after another.

"Go to the nearest site of grace and kill any enemies you meet on the way", "kill everything around here,
soldiers then the rest, until it's all dead", "walk in a circle for 10 seconds, then to Groveside Cave
without fighting, then around until you meet a soldier and kill it, then back": the model (agent.plan)
turns the order into steps; this runs them at the frame rate. Simple orders ("kill that soldier") don't
come here: the instant rules (walk_commands.py) handle them.

Steps (a few kinds, each with options):
  go     target: a place ('Groveside Cave', 'nearest site of grace'), a character kind ('Godrick Soldier'),
         'found' (the one an until_see found), 'crosshair', 'start' (where the plan began).
         engage: 'none' (walk past everything), 'attackers' (fight what comes at the player), 'all' (fight
         every enemy met within ENGAGE_RANGE, then go on). until_see: stop when a character of this kind
         is in sight (SEE_RANGE): it becomes 'found'.
  fight  target: a kind, 'enemy' (any hostile), 'found'. count: 'one' or 'all' (until none is left within
         FIGHT_RANGE). cycle: kinds to take in turn ('Godrick Soldier', 'other' = any other enemy).
  move   shape: 'circle', 'line' (straight ahead), 'wander' (to random points nearby). seconds.
         until_see as for go.
  wait   seconds.

The app is the world (PlanWorld): it walks, fights, finds characters and places, and reports the walk's
and the fighter's events here (on_event). A move step's keys come from update().
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Protocol

from .autowalk import Intent, _flat

ENGAGE_RANGE = 12.0     # metres: 'all' fights enemies this near on the way
ATTACKER_RANGE = 5.0    # metres: 'attackers' fights enemies this near (they are on the player)
SEE_RANGE = 35.0        # metres: until_see
FIGHT_RANGE = 60.0      # metres: 'all' looks this far for the next one
CIRCLE_TURN = 60.0      # degrees a second: a circle of ~4.5 m radius at a walk
WANDER_RADIUS = 30.0
DEFAULT_SECONDS = {'circle': 10.0, 'line': 4.0, 'wander': 90.0, 'wait': 3.0}
MAX_STEPS = 20
SEARCH_LIMIT = 300.0    # seconds an until_see search goes on before it gives up
TARGETS = ('place', 'character')


@dataclass
class Step:
    do: str                       # go, fight, move, wait
    target: str | None = None
    engage: str = 'none'
    until_see: str | None = None
    count: str = 'one'
    cycle: list = field(default_factory=list)
    shape: str = 'circle'
    seconds: float | None = None

    def text(self) -> str:
        bits = [self.do] + [f'{k}={v}' for k, v in (('target', self.target), ('engage', self.engage),
                                                     ('until_see', self.until_see), ('count', self.count),
                                                     ('cycle', self.cycle), ('shape', self.shape),
                                                     ('seconds', self.seconds))
                            if v and not (k == 'engage' and v == 'none') and not (k == 'count' and v == 'one')
                            and not (k == 'shape' and self.do != 'move')]
        return ' '.join(str(b) for b in bits)


def steps_from(raw) -> list[Step]:
    """Steps from the model's JSON, checked: unknown kinds dropped, options limited to what exists."""
    out = []
    for s in (raw or [])[:MAX_STEPS]:
        if not isinstance(s, dict) or s.get('do') not in ('go', 'fight', 'move', 'wait'):
            continue
        secs = s.get('seconds')
        try:
            secs = None if secs is None else max(0.5, min(600.0, float(secs)))
        except (TypeError, ValueError):
            secs = None
        out.append(Step(
            do=s['do'], target=(s.get('target') or None),
            engage=s.get('engage') if s.get('engage') in ('none', 'attackers', 'all') else 'none',
            until_see=s.get('until_see') or None,
            count='all' if s.get('count') == 'all' else 'one',
            cycle=[str(c) for c in (s.get('cycle') or []) if c][:6],
            shape=s.get('shape') if s.get('shape') in ('circle', 'line', 'wander') else 'circle',
            seconds=secs))
    return out


class PlanWorld(Protocol):
    def plan_target(self, target: str) -> tuple | None: ...           # (point, name, entity id or None)
    def plan_find(self, kind: str, max_range: float, not_kinds: list) -> tuple | None: ...   # same level
    def plan_walk(self, point, name, entity_id, fight: bool) -> None: ...
    def plan_stop(self) -> None: ...
    def plan_say(self, line: str) -> None: ...


class PlanRunner:
    def __init__(self, world: PlanWorld):
        self.world = world
        self.steps: list[Step] = []
        self.i = -1
        self.active = False
        self.found: int | None = None       # what an until_see found ('found')
        self.start_pos = None
        self.t = 0.0
        self._step_t = 0.0
        self._engaged = False               # a go step paused for a fight on the way
        self._goal = None                   # (point, name, entity id) of the go step
        self._cycle_i = 0
        self._fighting = False              # a fight step's fight is under way
        self._wander_to = None
        self._here = None                   # the player's position, last frame
        self._center = None                 # a wander step wanders around where it began

    @property
    def step(self) -> Step | None:
        return self.steps[self.i] if self.active and 0 <= self.i < len(self.steps) else None

    def start(self, steps: list[Step], snap) -> None:
        self.steps, self.i, self.active, self.found = list(steps), -1, bool(steps), None
        self.start_pos = snap.player.pos if snap and snap.player else None
        self._here = self.start_pos
        if self.active:
            self._next()

    def stop(self, why: str = '') -> None:
        if self.active and why:
            self.world.plan_say(why)
        self.active, self.steps, self.i = False, [], -1

    # ---- the steps ----

    def _next(self) -> None:
        self.i += 1
        self._step_t, self._engaged, self._fighting, self._goal, self._wander_to = self.t, False, False, None, None
        self._cycle_i = 0
        self._center = self._here or self.start_pos
        s = self.step
        if s is None:
            self.active = False
            self.world.plan_say("All done!")
            return
        if s.do == 'go':
            self._goal = self._target(s.target)
            if self._goal is None:
                self.world.plan_say(f"I can't find {s.target or 'that'}. Skipping it.")
                return self._next()
            self.world.plan_walk(*self._goal, fight=False)
        elif s.do == 'fight':
            if not self._fight_next(first=True):
                self.world.plan_say(f"I don't see any {s.target or 'enemy'} here." if s.count == 'one'
                                    else 'Nothing left to fight here.')
                return self._next()
        elif s.do == 'move':
            self.world.plan_stop()
            if s.shape == 'wander':
                self._wander()

    def _target(self, target: str | None):
        if target == 'found':
            return self.world.plan_target(f'#entity {self.found}') if self.found is not None else None
        if target == 'start':
            return (self.start_pos, 'where we started', None) if self.start_pos else None
        return self.world.plan_target(target or 'crosshair')

    def _fight_next(self, first: bool = False) -> bool:
        """Start the fight step's next fight: its target, or the next kind of the cycle with one left."""
        s = self.step
        if s.target == 'found' or (not s.cycle and s.target == 'found'):
            got = self._target('found') if first else None
        else:
            kinds = s.cycle or [s.target or 'enemy']
            got = None
            for k in range(len(kinds)):
                kind = kinds[(self._cycle_i + k) % len(kinds)]
                got = self.world.plan_find(kind, FIGHT_RANGE, [c for c in kinds if c not in (kind, 'other')])
                if got is not None:
                    self._cycle_i = (self._cycle_i + k + 1) % len(kinds)   # alternate: the next kind next time
                    break
        if got is None:
            return False
        self._fighting = True
        self.world.plan_walk(*got, fight=True)
        return True

    def _wander(self) -> None:
        p = self._center or self._here or self.start_pos
        if p is None:
            return
        a = random.uniform(0, 2 * math.pi)
        r = random.uniform(WANDER_RADIUS / 3, WANDER_RADIUS)
        self._wander_to = (p[0] + math.sin(a) * r, p[1], p[2] + math.cos(a) * r)
        self.world.plan_walk(self._wander_to, None, None, fight=False)

    # ---- every frame ----

    def update(self, snap, dt: float, walking: bool, fighting: bool) -> Intent | None:
        """A move step's keys (else None: the walk or the fight presses them)."""
        self.t += dt
        s = self.step
        if s is None or snap is None or not snap.player:
            return None
        self._here = snap.player.pos
        elapsed = self.t - self._step_t
        if s.until_see and elapsed > SEARCH_LIMIT and not fighting:
            self.world.plan_stop()
            self.world.plan_say(f"I couldn't find any {s.until_see}. Moving on.")
            self._next()
            return None
        secs = s.seconds or DEFAULT_SECONDS.get(s.shape if s.do == 'move' else s.do)
        if s.until_see and not fighting:
            got = self.world.plan_find(s.until_see, SEE_RANGE, [])
            if got is not None:
                self.found = got[2]
                self.world.plan_stop()
                self.world.plan_say(f'There! A {got[1] or s.until_see}!')
                self._next()
                return None
        if s.do == 'go' and s.engage != 'none' and not fighting and not self._engaged:
            rng = ENGAGE_RANGE if s.engage == 'all' else ATTACKER_RANGE
            got = self.world.plan_find('enemy', rng, [])
            if got is not None:
                self._engaged = True
                self.world.plan_walk(*got, fight=True)
                return None
        if s.do == 'wait':
            if elapsed >= secs:
                self._next()
            return None
        if s.do == 'move':
            if secs and elapsed >= secs and not (s.until_see and s.shape == 'wander' and s.seconds is None):
                self.world.plan_stop()
                self._next()
                return None
            if s.shape == 'circle':
                return Intent(frozenset({'forward'}), CIRCLE_TURN * dt, ())
            if s.shape == 'line':
                return Intent(frozenset({'forward'}), 0.0, ())
            if s.shape == 'wander' and not walking and not fighting:
                self._wander()
        return None

    def on_event(self, ev: str) -> bool:
        """The walk's or the fighter's event. True: the plan took care of what comes next."""
        s = self.step
        if s is None:
            return False
        if ev in ('stopped:you', 'low_health', 'timeout'):
            self.stop({'low_health': "You're hurt! I'll stop the plan here.",
                       'timeout': "That fight took too long. Stopping the plan."}.get(ev, ''))
            return False
        if ev == 'gave_up':
            if s.do == 'move' and s.shape == 'wander':
                self._wander()
                return True
            self.stop("I can't get through here. Stopping the plan.")
            return False
        if ev in ('won', 'target_gone'):
            if s.do == 'go' and self._engaged:     # back on the way
                self._engaged = False
                self.world.plan_walk(*self._goal, fight=False)
                return True
            if s.do == 'fight':
                self._fighting = False
                if s.count == 'all' and self._fight_next():
                    return True
                self._next()
                return True
            if s.do == 'move' and s.shape == 'wander':
                self._wander()
                return True
        if ev == 'arrived':
            if s.do == 'go' and not self._engaged:
                self._next()
                return True
            if s.do == 'move' and s.shape == 'wander':
                self._wander()
                return True
        return False
