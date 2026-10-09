"""Instant local commands: handled on this PC before any model call (docs/PLAN.md, "Layers").

Short, unambiguous phrases act at once, with no network and no cost. Anything else goes to the
model. Each handler returns the short reply to show (and speak), or None if the phrase isn't one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import lookat
from .companion import Companion
from .interface import GameAdapter, dist

_STOP = re.compile(r"^(stop|wait|stay( there)?|hold (on|it)|halt|freeze)[.!]*$", re.I)
_BACK = re.compile(r"^(come (back|here)|back|follow( me)?|return|to me)[.!]*$", re.I)
_GO_THAT = re.compile(r"^(go|fly|move)( over)? (to )?(that|there|it|this)[.!]*$", re.I)
_SHOW_THAT = re.compile(r"^(show me|point (at|to)|highlight) (that|it|this)[.!]*$", re.I)
_QUIET = re.compile(r"^(quiet|shush|hush|be quiet|shut up|stop talking|silence)[.!]*$", re.I)


@dataclass
class Context:
    adapter: GameAdapter
    companion: Companion | None
    stop_speech: Callable[[], None] = lambda: None
    stop_tasks: Callable[[], None] = lambda: None


def _crosshair_target(ctx: Context):
    """(entity_id, point) of what is at the crosshair, or (None, None)."""
    snap = ctx.adapter.snapshot()
    if not snap.in_world:
        return None, None
    r = lookat.resolve(snap, ctx.adapter.raycast)
    if r.kind in ('entity', 'target') and r.best:
        return r.best.entity.id, None
    if r.kind == 'surface' and r.surface and r.surface.pos:
        return None, r.surface.pos
    return None, None


_POLITE_START = re.compile(r"^((hey|ok|okay)\s+)?((fairy|navi|pixie)[,!]?\s+)?(please\s+)?", re.I)
_POLITE_END = re.compile(r"[\s,]+(please|now|right now|thanks|thank you|to me|for me|fairy)[.!]*$", re.I)


def normalize(text: str) -> str:
    """'Hey fairy, come back to me please!' -> 'come back'."""
    t = _POLITE_START.sub('', text.strip().rstrip('.!?'))
    while True:
        u = _POLITE_END.sub('', t)
        if u == t:
            return t.strip()
        t = u


def handle(text: str, ctx: Context) -> str | None:
    t = normalize(text)
    if _QUIET.match(t):
        ctx.stop_speech()
        return ''
    if _STOP.match(t):
        ctx.stop_speech()
        ctx.stop_tasks()
        if ctx.companion:
            ctx.companion.stop()
        return 'Stopped.'
    if _BACK.match(t):
        ctx.stop_tasks()
        if ctx.companion:
            ctx.companion.follow()
        return 'Coming back.'
    if ctx.companion and (_GO_THAT.match(t) or _SHOW_THAT.match(t)):
        entity_id, point = _crosshair_target(ctx)
        if entity_id is None and point is None:
            return "I can't tell what you're pointing at."
        if _SHOW_THAT.match(t):
            ctx.companion.show(entity_id=entity_id, point=point)
            return 'There.'
        if point is not None and ctx.companion.pos is not None and dist(point, ctx.companion.pos) < 0.5:
            return "I'm already there."
        ctx.companion.go(entity_id=entity_id, point=(point[0], point[1] + 1.2, point[2]) if point else None)
        return 'On my way.'
    return None
