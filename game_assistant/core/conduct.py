"""One conductor: which single behaviour has the character, frame by frame (docs/CONDUCT.md).

The order the player gave (its walk, its task, its plan) has the character unless danger needs it: then one survival
behaviour does (fight, take cover, get away, hold ground, back to open ground), and when that's over the order goes
on from where things now are. Nothing else decides. (Eight parts each moved the character on their own triggers,
with hand-offs between them: "I won't walk in among them", then the order walked in; hold, give up, the order on,
hold again, 2026-10-16.)

Each frame the danger is weighed as one (core/threat.py: a Decision); the conductor:

  - starts the behaviour the decision asks for, when nothing's running (the order paused: the world's job);
  - keeps a running behaviour till it ends (commitment), unless one that ranks higher comes, after MIN_RUN s, and
    never while the order or the behaviour can't be cut into (mid-jump: a 10-block fall, 2026-10-16);
  - after a behaviour that ended without getting anywhere (held off: they stay where they are), doesn't start the
    same again against the same one for AGAIN_AFTER s: its outcome goes to the order instead (that step's target is
    guarded: planned round), which is what ends the loops;
  - logs each change, and why, as one line (runtime/conduct-<date>.jsonl).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# how much each survival behaviour outranks the others (higher cuts into lower)
RANK = {'cover': 4, 'fight': 3, 'away': 3, 'regroup': 3, 'hold': 2}
MIN_RUN = 1.5           # seconds a behaviour runs before another (higher) may cut into it
AGAIN_AFTER = 60.0      # seconds before a behaviour that got nowhere is tried again against the same one
NO_START_AGAIN = 5.0    # seconds before one that couldn't start at all (no cover near) is tried again, against any
                        # (two skeletons, each the nearest in turn: cover tried and not had every frame, the order
                        # stopped and started again each time, 2026-10-16)
NO_GOOD = ('held_off', 'unreachable', 'no_spot', 'timeout', 'target_gone')   # outcomes that got nowhere


@dataclass
class Running:
    kind: str                # 'fight', 'cover', 'away', 'hold', 'regroup'
    target: object = None    # the character it's about (its id)
    since: float = 0.0
    why: str = ''


@dataclass
class Conductor:
    log: object = None                       # (row: dict) -> None
    clock: object = time.monotonic
    running: Running | None = None
    tried: dict = field(default_factory=dict)   # (kind, target) -> when it ended getting nowhere

    def choose(self, decision, can_cut: bool = True) -> tuple[str, Running | None]:
        """What to do now about the decision (core/threat.Decision, or None: nothing weighed). ('keep', running):
        go on with it; ('start', new): start it (stop the one running first, if any); ('order', None): the order
        has the character. can_cut: the order and the running behaviour may be cut into now (not mid-jump)."""
        now = self.clock()
        want = decision.what if decision is not None and decision.what in RANK else None
        target = getattr(getattr(decision, 'target', None), 'id', None)
        if want is not None and (now - self.tried.get((want, target), -1e9) < AGAIN_AFTER
                                 or now - self.tried.get((want, '*'), -1e9) < NO_START_AGAIN):
            want = None                                   # (that got nowhere just now: not again)
        cur = self.running
        if cur is not None:
            if want is None or want == cur.kind or not can_cut or now - cur.since < MIN_RUN \
                    or RANK[want] <= RANK[cur.kind]:
                return 'keep', cur
            new = Running(want, target, now, getattr(decision, 'why', ''))
            self._log('switch', new, f'{cur.kind} -> {want}')
            self.running = new
            return 'start', new
        if want is None or not can_cut:
            return 'order', None
        new = Running(want, target, now, getattr(decision, 'why', ''))
        self._log('start', new, decision.why)
        self.running = new
        return 'start', new

    def follow_on(self, kind: str, target=None, why: str = '') -> Running:
        """The running behaviour turns into another (fighting boxed in: back to open ground, then held there)."""
        new = Running(kind, target, self.clock(), why)
        self._log('switch', new, f'{self.running.kind if self.running else "order"} -> {kind}: {why}')
        self.running = new
        return new

    def ended(self, outcome: str) -> Running | None:
        """The running behaviour is over (outcome: 'won', 'held_off', 'calm', ...). The order has the character
        again (the world takes it up from where things are). It, for the caller."""
        cur, self.running = self.running, None
        if cur is None:
            return None
        if outcome in NO_GOOD:
            self.tried[(cur.kind, cur.target)] = self.clock()
        if outcome == 'no_spot':
            self.tried[(cur.kind, '*')] = self.clock()
        self._log('ended', cur, outcome)
        return cur

    def _log(self, what: str, r: Running, why: str) -> None:
        if self.log is not None:
            try:
                self.log({'event': what, 'kind': r.kind, 'target': r.target, 'why': why})
            except Exception:
                pass
