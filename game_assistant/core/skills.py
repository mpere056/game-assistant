"""Skills: what the character can do, and how each ends (docs/AGENT.md).

A skill (move, gather, craft, put down, smelt, fight...: each game has many, the core knows none by name)
ends with an Outcome: done or failed, and why as data (a Reason) the next decision works from, not a
sentence ("I can't get to where I can reach the crafting table" was something to say; "unreachable: the
crafting table at -12 67 -38, from 14 below, the way needs 13 blocks and 7 are spare" is something to plan
from, 2026-10-15).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Reason kinds
MISSING = 'missing'          # an item it takes isn't held: {'item', 'count'}
NEEDS_TOOL = 'needs_tool'    # {'tool', 'for'}
NO_TARGET = 'no_target'      # nothing of that kind near: {'what'}
UNREACHABLE = 'unreachable'  # {'what', 'at', 'from', 'route': the route planner's note (e.g. blocks it would take)}
STUCK = 'stuck'              # no way on from here at all: {'from', 'tried'}
NO_SPOT = 'no_spot'          # nowhere to put a block down: {'item'}
DANGER = 'danger'            # {'what'}
INTERRUPTED = 'interrupted'  # {'by'}
TIMEOUT = 'timeout'          # {'what'}
UI = 'ui'                    # a screen, a click or the pointer didn't do what was wanted: {'what'}


@dataclass(frozen=True)
class Reason:
    kind: str
    data: dict = field(default_factory=dict, hash=False, compare=False)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def brief(self) -> str:
        """For logs and the doer: 'unreachable what=crafting table at=(-12, 67, -38) ...'."""
        return ' '.join([self.kind] + [f'{k}={v}' for k, v in self.data.items()])


@dataclass
class Outcome:
    ok: bool
    reason: Reason | None = None
    line: str = ''               # what to tell the player
    changed: dict = field(default_factory=dict)   # what changed (items got or used, blocks put down or dug...)
    took_s: float = 0.0

    def brief(self) -> str:
        if self.ok:
            return 'done' + (f' ({self.line})' if self.line else '')
        return 'failed: ' + (self.reason.brief() if self.reason else self.line)


def done(line: str = '', **changed) -> Outcome:
    return Outcome(True, None, line, dict(changed))


def failed(kind: str, line: str, **data) -> Outcome:
    return Outcome(False, Reason(kind, dict(data)), line)


def route_reason(what: str, at, note: dict | None, frm=None) -> Reason:
    """A walk given up, as a Reason: from the route planner's note (blocks it would take; no way at all)."""
    note = dict(note or {})
    kind = STUCK if note.get('found') == 'no way' and not note.get('blocks_it_would_take') else UNREACHABLE
    return Reason(kind, {'what': what, 'at': at, 'from': frm, 'route': note})
