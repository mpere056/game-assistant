"""The world memory: what was done and learned this session, shared by every part (docs/AGENT.md).

Filled by the skills as they run: stations put down (where, when), blocks dug, things that couldn't be
got at (and why, from where), what was seen. The planner and the doer read it ("a crafting table put down
at -12 67 -38, 14 above you": the order down there had no idea it had put one there, 2026-10-15).
Game-agnostic: positions are the game's own coordinates, kinds are the game's own names.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

MAX_DUG = 400            # blocks dug kept (the oldest let go)
MAX_UNREACHABLE = 50
UNREACHABLE_FOR = 120.0  # seconds a target that couldn't be got at stays left out (the world changes: tried again)
SUMMARY_NEAR = 64.0      # blocks: stations this near are told of


@dataclass
class Placed:
    item: str
    at: tuple
    t: float


@dataclass
class Unreachable:
    what: str
    at: tuple | None
    frm: tuple | None
    reason: str
    t: float


@dataclass
class WorldMemory:
    placed: list = field(default_factory=list)        # [Placed]: stations and blocks put down
    dug: list = field(default_factory=list)           # [(cell, t)]
    unreachable: list = field(default_factory=list)   # [Unreachable]
    seen: dict = field(default_factory=dict)          # kind -> {cell: t}
    clock: object = time.monotonic

    # ---- recording ----

    def put_down(self, item: str, at) -> None:
        at = tuple(int(v) for v in at)
        self.placed = [p for p in self.placed if p.at != at] + [Placed(item, at, self.clock())]

    def removed(self, at) -> None:
        """A block there is gone (mined, broken): a station there is no more."""
        at = tuple(int(v) for v in at)
        self.placed = [p for p in self.placed if p.at != at]

    def dug_at(self, cell) -> None:
        cell = tuple(int(v) for v in cell)
        self.removed(cell)
        self.dug.append((cell, self.clock()))
        if len(self.dug) > MAX_DUG:
            self.dug = self.dug[-MAX_DUG:]

    def couldnt_reach(self, what: str, at=None, frm=None, reason: str = '') -> None:
        at = tuple(int(v) for v in at) if at is not None else None
        frm = tuple(round(v, 1) for v in frm) if frm is not None else None
        self.unreachable = [u for u in self.unreachable if u.at != at or at is None] + \
            [Unreachable(what, at, frm, reason, self.clock())]
        self.unreachable = self.unreachable[-MAX_UNREACHABLE:]

    def saw(self, kind: str, cells) -> None:
        now = self.clock()
        known = self.seen.setdefault(kind, {})
        for c in cells:
            known[tuple(int(v) for v in c)] = now

    # ---- reading ----

    def stations(self, item: str | None = None) -> list:
        return [p for p in self.placed if item is None or p.item == item]

    def is_unreachable(self, at, frm=None, within: float = 2.0) -> bool:
        """Lately found not to be got at (from about here, when frm is given)."""
        at = tuple(int(v) for v in at)
        now = self.clock()
        for u in self.unreachable:
            if u.at == at and now - u.t < UNREACHABLE_FOR and \
                    (frm is None or u.frm is None or math.dist(u.frm, frm) <= within):
                return True
        return False

    def summary(self, me=None) -> str:
        """For the doer: the stations put down near (and how far up or down), and what lately couldn't be got at."""
        bits = []
        for p in self.placed:
            if me is not None and math.dist(p.at, me) > SUMMARY_NEAR:
                continue
            where = f'{p.at[0]} {p.at[1]} {p.at[2]}'
            if me is not None:
                dy = p.at[1] - math.floor(me[1])
                where += f' ({abs(dy)} {"above" if dy > 0 else "below"} you)' if dy else ' (your level)'
            bits.append(f'{p.item.split(":")[-1].replace("_", " ")} put down at {where}')
        now = self.clock()
        for u in self.unreachable[-3:]:
            if now - u.t < UNREACHABLE_FOR:
                bits.append(f"couldn't get to the {u.what}" + (f' ({u.reason})' if u.reason else ''))
        return '; '.join(bits) or 'nothing yet'
