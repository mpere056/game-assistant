"""Goals and plans: getting something done, planned again when the world turns out other than expected
(docs/AGENT.md, stage 2).

A goal ("have 1 iron pickaxe") is planned from where things are (core/crafting.py with a book that costs in
seconds from where the player is: a station already there against one made here, stone in reach against
logs on the surface). The plan's steps are cut into milestones: the tools, the stations and the cooked things
on the way, and the goal itself. It's planned again:

  a step failed      always: its Reason is a fact the planner works from (the memory has it: a station that
                     couldn't be got at from here; stuck: only what's in reach)
  at a milestone     when it took much longer than it was reckoned to (DRIFT): the world isn't as the plan
                     thought
  after an interruption (a fight, the player taking over): the caller says so (replan())

A plan of one milestone runs to its end unless a step fails. MAX_REPLANS at most, and never the same plan
again after a failure (the same first step for the same reason: nothing learned, it would fail the same):
then it's over to what's above (the doer).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .crafting import CantMake

MAX_REPLANS = 4
DRIFT = 2.0             # a milestone taking this many times its reckoning...
DRIFT_SLACK = 20.0      # ...and this many seconds over it: planned again


def milestones(steps: list, goal: str) -> list[int]:
    """The indices of the steps that end a milestone: a tool made (a later gather needs it), a station made or
    put down, something cooked, the goal (and the last step)."""
    tools = {s[3].tool for s in steps if s[0] == 'gather' and s[3].tool}
    placed = {s[1] for s in steps if s[0] == 'station'}
    ends = []
    for i, s in enumerate(steps):
        if s[0] == 'station' or s[0] == 'smelt':
            ends.append(i)
        elif s[0] == 'craft' and (s[1].result in tools or s[1].result in placed or s[1].result == goal):
            ends.append(i)
    if steps and (not ends or ends[-1] != len(steps) - 1):
        ends.append(len(steps) - 1)
    return ends


@dataclass
class GoalRunner:
    """planner(item, total, after) -> steps (raises CantMake): plans from how things are now; after: the
    Outcome that made it plan again (None at the start). estimate(step) -> seconds."""
    planner: object
    estimate: object = None
    active: bool = False
    item: str = ''
    total: int = 0
    steps: list = field(default_factory=list)
    ends: list = field(default_factory=list)
    replans: int = 0
    took: float = 0.0           # seconds into the milestone under way
    log: list = field(default_factory=list)   # (what happened, the plan's first steps): for the trace

    def start(self, item: str, total: int, steps: list | None = None) -> list:
        self.item, self.total, self.replans, self.active = item, total, 0, True
        return self._use(steps if steps is not None else self.planner(item, total, None), 'start')

    def _use(self, steps: list, why: str) -> list:
        self.steps, self.ends, self.took = list(steps), milestones(steps, self.item), 0.0
        self.log.append((why, [_brief(s) for s in steps[:4]]))
        return self.steps

    def stop(self) -> None:
        self.active = False

    def step_done(self, i: int, took_s: float) -> list | None:
        """Step i of the plan is done, after took_s: a new plan from here when a milestone ran long, else None."""
        if not self.active:
            return None
        self.took += took_s
        if i not in self.ends:
            return None
        start = max([e + 1 for e in self.ends if e < i], default=0)
        reckoned = sum(self._estimate(s) for s in self.steps[start:i + 1])
        took, self.took = self.took, 0.0
        if i == len(self.steps) - 1:
            # the plan's end: met? (a plan for what a step lacked, blocks to build the way with, ends short of
            # the goal: on from here). The planner says what's left: nothing when it's met.
            try:
                rest = self.planner(self.item, self.total, None)
            except CantMake:
                rest = []
            if not rest:
                self.active = False
                return None
            return self._use(rest, 'on from the end of a plan')
        if took <= reckoned * DRIFT + DRIFT_SLACK:
            return None
        return self.replan(None, f'milestone {i} took {round(took)} s, reckoned {round(reckoned)}')

    def failed(self, outcome) -> list | None:
        """A step failed: a new plan from here that's not the same as the one that failed, or None (over)."""
        if not self.active:
            return None
        got = self.replan(outcome, 'failed: ' + (outcome.reason.brief() if outcome and outcome.reason else '?'))
        if got is None:
            self.active = False
        return got

    def replan(self, after, why: str) -> list | None:
        if self.replans >= MAX_REPLANS:
            return None
        self.replans += 1
        try:
            steps = self.planner(self.item, self.total, after)
        except CantMake as e:
            self.log.append((f'{why}: no plan ({e})', []))
            return None
        # the same first step for the same reason again: nothing learned (unless what failed was one thing at one
        # place: the memory leaves it out now, and another may do)
        if after is not None and steps and _brief(steps[0]) == _failed_step(after) \
                and not (after.reason is not None and after.reason.get('at')):
            self.log.append((f'{why}: the same again', [_brief(s) for s in steps[:4]]))
            return None                                  # (nothing learned: it would fail the same way)
        if not steps:
            self.active = False
            return []
        return self._use(steps, why)

    def _estimate(self, step) -> float:
        if self.estimate is None:
            return 0.0
        try:
            return float(self.estimate(step) or 0.0)
        except Exception:
            return 0.0


def _brief(step) -> str:
    """'gather raw iron', 'craft iron pickaxe', 'station crafting table', 'smelt iron ingot'."""
    what = step[1].result if step[0] in ('craft', 'smelt') else str(step[1])
    return f"{step[0]} {what.split(':')[-1].replace('_', ' ')}"


def _failed_step(outcome) -> str | None:
    step = (outcome.reason.get('step') if outcome is not None and outcome.reason is not None else None) or ''
    return step or None
