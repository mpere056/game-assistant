"""Building: a structure given as parts (boxes of one block each), worked out into the blocks to put down, in an
order each can be put down in (any game with blocks placed one at a time: first Minecraft).

"Make me a house 5 by 5, 3 high" (2026-10-16: "my building tools only lay one block at a time right where I
stand", and no house): the doer works out where and what (a floor, walls with a door gap, a roof) as parts; this
turns them into cells and the order; the task (core/tasks.py, 'place' steps) walks within reach of each and puts
it down against a block already there.

A part: {'shape': 'fill' | 'walls' | 'hollow' | 'clear', 'from': (x, y, z), 'to': (x, y, z), 'item': id}
  fill    every cell of the box
  walls   the box's four sides (no floor, no top): a room's walls
  hollow  the box's outside (sides, floor and top)
  clear   every cell of the box empty (a door, a window, the room inside): what's there is mined
Parts are laid one after another: a later one wins (walls, then a clear for the door in them).
"""
from __future__ import annotations

MAX_CELLS = 2000        # blocks one build may have at most
AIR = ''                # (a cell to be empty)
NEIGHBOURS = ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0))


class BadPlan(ValueError):
    """The parts don't make a structure that can be built (said why)."""


def _box(part):
    try:
        a = [int(round(float(v))) for v in _xyz(part['from'])]
        b = [int(round(float(v))) for v in _xyz(part['to'])]
    except (KeyError, TypeError, ValueError):
        raise BadPlan(f"a part needs 'from' and 'to' (x, y, z): {part}")
    return [min(a[i], b[i]) for i in range(3)], [max(a[i], b[i]) for i in range(3)]


def _xyz(v):
    if isinstance(v, dict):
        return v['x'], v['y'], v['z']
    return tuple(v)


def expand(parts: list) -> dict:
    """{cell: item, or AIR (to be empty)} from the parts, in order (a later part wins)."""
    out: dict = {}
    for part in parts or []:
        shape = str(part.get('shape') or 'fill').lower()
        lo, hi = _box(part)
        item = '' if shape == 'clear' else str(part.get('item') or '')
        if shape != 'clear' and not item:
            raise BadPlan(f'a {shape} part needs an item: {part}')
        n = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
        if n > MAX_CELLS * 4:
            raise BadPlan(f'a part of {n} blocks is too big')
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    side = x in (lo[0], hi[0]) or z in (lo[2], hi[2])
                    end = y in (lo[1], hi[1])
                    if shape == 'walls' and not side:
                        continue
                    if shape == 'hollow' and not (side or end):
                        continue
                    if shape not in ('fill', 'walls', 'hollow', 'clear'):
                        raise BadPlan(f'no shape called {shape!r} (fill, walls, hollow, clear)')
                    out[(x, y, z)] = item
    if sum(1 for v in out.values() if v) > MAX_CELLS:
        raise BadPlan(f'{sum(1 for v in out.values() if v)} blocks is more than one build can take ({MAX_CELLS})')
    return out


def needs(targets: dict, now: dict) -> dict:
    """{item: how many} to put down: what isn't there already (now: {cell: 'same' | 'other' | 'air'})."""
    out: dict = {}
    for cell, item in targets.items():
        if item and now.get(cell) != 'same':
            out[item] = out.get(item, 0) + 1
    return out


def order(targets: dict, now: dict) -> tuple[list, list]:
    """(cells to empty first (something else is there), [(cell, item)] to put down in an order each can go:
    from the bottom up, each beside a block that's there by then (the ground, or one put down before it)).
    BadPlan: some can't be put down at all (in the air, nothing ever beside them)."""
    dig = sorted((c for c, item in targets.items() if now.get(c) == 'other' or (not item and now.get(c) == 'same')),
                 key=lambda c: -c[1])                    # (from the top down: nothing left hanging to fall)
    solid = {c for c, s in now.items() if s in ('same', 'other')} - set(dig)
    todo = {c for c, item in targets.items() if item and now.get(c) != 'same'}
    out = []

    def held(c):
        return any((c[0] + d[0], c[1] + d[1], c[2] + d[2]) in solid or now.get(
            (c[0] + d[0], c[1] + d[1], c[2] + d[2])) == 'ground' for d in NEIGHBOURS)
    while todo:
        ready = [c for c in todo if held(c)]
        if not ready:
            raise BadPlan(f'{len(todo)} blocks would be in the air with nothing beside them '
                          f'(the lowest at {min(todo, key=lambda c: c[1])})')
        y = min(c[1] for c in ready)
        layer = sorted((c for c in ready if c[1] == y), key=lambda c: (c[0], c[2]))
        for c in layer:
            out.append((c, targets[c]))
            solid.add(c)
            todo.discard(c)
    return dig, out
