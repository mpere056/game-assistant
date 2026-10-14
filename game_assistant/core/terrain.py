"""The lie of the land from a height map (any game that can give the ground's height across an area).

"Walk 50 blocks on top of the nearest hills": the nearest *biome* called hills was 1,946 blocks away, while
the player stood beside hills of their own (2026-10-13). Hills here are the land's shape, named or not:

  hills(m, me)      the high points that rise well above the land round them, nearest first: each top, how
                    much it rises, the nearest point on its top, and a line along the top (a ridge to walk)
  each_way(m, me)   the highest ground in each of the 8 directions
  here(m, me)       the player's own spot: how much it rises above the land round it, and on a rise, its top and a
                    loop round its foot ("walk round this hill": the rise stood on wasn't among the hills, 2026-10-13)

The map: the height of the ground in each column (where one stands on the land itself: under the trees, not
on them); None where it isn't known.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

CELL = 8                # blocks: the map is looked at in squares this big (each square's highest point)
WINDOW = 3              # squares each way: a hill's top is the highest within this (here: 56 blocks across)
MIN_RISE = 6            # blocks a top must rise above the lowest land round it to count as a hill
STEP = 4                # blocks between points followed along a top
MAX_ALONG = 96          # blocks followed along a top each way
COMPASS = ('north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west')


@dataclass
class HeightMap:
    x0: int
    z0: int
    sx: int
    sz: int
    h: list                 # x fastest; None: not known

    def at(self, x: int, z: int):
        if not (self.x0 <= x < self.x0 + self.sx and self.z0 <= z < self.z0 + self.sz):
            return None
        return self.h[(z - self.z0) * self.sx + (x - self.x0)]

    def near(self, x: int, z: int, r: int = 1):
        """The highest known within r (a ridge read through a single low column: a path, a hole)."""
        best = None
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                v = self.at(x + dx, z + dz)
                if v is not None and (best is None or v > best[2]):
                    best = (x + dx, z + dz, v)
        return best


def compass(dx: float, dz: float) -> str:
    """North is -z, east is +x (as the game shows them)."""
    a = math.degrees(math.atan2(dx, -dz)) % 360
    return COMPASS[int((a + 22.5) // 45) % 8]


def ground_at(m: HeightMap, x: int, z: int):
    """The ground's height at a column, else the middle of what's known round it."""
    v = m.at(x, z)
    if v is not None:
        return v
    known = sorted(v for dz in range(-2, 3) for dx in range(-2, 3) if (v := m.at(x + dx, z + dz)) is not None)
    return known[len(known) // 2] if known else None


def _cells(m: HeightMap):
    """{(i, j): (peak x, z, height, the square's mean height)} for each square with anything known."""
    out = {}
    for j in range(math.ceil(m.sz / CELL)):
        for i in range(math.ceil(m.sx / CELL)):
            best, total, n = None, 0, 0
            for z in range(m.z0 + j * CELL, min(m.z0 + m.sz, m.z0 + (j + 1) * CELL)):
                for x in range(m.x0 + i * CELL, min(m.x0 + m.sx, m.x0 + (i + 1) * CELL)):
                    v = m.at(x, z)
                    if v is None:
                        continue
                    total, n = total + v, n + 1
                    if best is None or v > best[2]:
                        best = (x, z, v)
            if best is not None:
                out[(i, j)] = (*best, total / n)
    return out


def hills(m: HeightMap, me: tuple, most: int = 5) -> list[dict]:
    """The hills in the map, nearest first (me: the player's (x, y, z), y their feet)."""
    cells = _cells(m)
    mine = ground_at(m, int(me[0]), int(me[2]))
    ni, nj = math.ceil(m.sx / CELL), math.ceil(m.sz / CELL)
    found = []
    for (i, j), (px, pz, ph, _mean) in cells.items():
        around = [cells[(i + a, j + b)] for a in range(-WINDOW, WINDOW + 1) for b in range(-WINDOW, WINDOW + 1)
                  if (i + a, j + b) in cells]
        # the highest within the window (ties: the first), and the lowest land round it
        if any(c[2] > ph or (c[2] == ph and (c[1], c[0]) < (pz, px)) for c in around):
            continue
        base = min(c[3] for c in around)
        rise = ph - base
        if rise < MIN_RISE:
            continue
        edge = i < WINDOW or j < WINDOW or i >= ni - WINDOW or j >= nj - WINDOW
        line = ridge(m, (px, pz, ph), base + rise * 0.5)
        near = min(line, key=lambda p: math.hypot(p[0] - me[0], p[2] - me[2]))
        along = sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(line, line[1:]))
        found.append({
            'top': {'x': px, 'y': ph, 'z': pz},
            'distance': round(math.hypot(px - me[0], pz - me[2])),
            'compass': compass(px - me[0], pz - me[2]),
            'rises_above_the_land_round_it': round(rise),
            'top_above_your_ground': None if mine is None else ph - mine,
            'nearest_point_on_top': {'x': near[0], 'y': near[1], 'z': near[2],
                                     'distance': round(math.hypot(near[0] - me[0], near[2] - me[2]))},
            # every other point (about 8 blocks apart), end to end: 'go to' each in turn walks the top
            'along_the_top': {'length_blocks': round(along),
                              'points': [{'x': p[0], 'y': p[1], 'z': p[2]} for p in _thin(line)]},
            'round_its_foot': _loop(m, (px, ph, pz), base + rise * FOOT),
            'may_go_on_past_what_was_read': edge,
            '_line': line,
        })
    # one ridge, one hill: a top on (or by) another's line is the same hill (the higher kept)
    found.sort(key=lambda r: -r['top']['y'])
    kept = []
    for r in found:
        t = r['top']
        if not any(math.hypot(p[0] - t['x'], p[2] - t['z']) <= STEP * 1.5 for k in kept for p in k['_line']):
            kept.append(r)
    for r in kept:
        del r['_line']
    kept.sort(key=lambda r: r['nearest_point_on_top']['distance'])
    return kept[:most]


def _thin(line: list) -> list:
    out = line[::2]
    if line and out[-1] != line[-1]:
        out.append(line[-1])
    return out


def ridge(m: HeightMap, peak: tuple, floor: float) -> list[tuple]:
    """A line along a hill's top through its peak (x, z, height): from the peak each way, a step at a time to the
    highest ground ahead (within 45 degrees of the way it's going), while above floor. Points (x, y, z), end
    to end."""
    px, pz, ph = peak
    heads = [(math.sin(math.radians(a)), -math.cos(math.radians(a))) for a in range(0, 360, 360 // 16)]

    def ahead(x, z, k, dist):
        dx, dz = heads[k % 16]
        return m.near(round(x + dx * dist), round(z + dz * dist))

    first = sorted(((p[2], k) for k in range(16) if (p := ahead(px, pz, k, STEP * 2)) is not None), reverse=True)
    arms = []
    for _h, k in first:
        if _h < floor:
            break
        if all(min((k - a) % 16, (a - k) % 16) >= 4 for a in arms):     # 90 degrees or more from the first
            arms.append(k)
        if len(arms) == 2:
            break
    lines = []
    on = [(px, pz)]                         # (both arms: the second turned and came back along the first)

    def fresh(p):
        return all(math.hypot(p[0] - q[0], p[1] - q[1]) > STEP * 0.75 for q in on)

    for k in arms:
        x, z, pts, went = px, pz, [], 0
        while went < MAX_ALONG:
            best = None
            for t in (k - 2, k - 1, k, k + 1, k + 2):
                p = ahead(x, z, t, STEP)
                if p is not None and fresh(p) and (best is None or p[2] > best[0][2]):
                    best = (p, t)
            if best is None or best[0][2] < floor:
                break
            (x, z, h), k = best
            on.append((x, z))
            pts.append((x, h, z))
            went += STEP
        lines.append(pts)
    a = lines[0] if lines else []
    b = lines[1] if len(lines) > 1 else []
    return list(reversed(b)) + [(px, ph, pz)] + a


FOOT = 0.25            # of a hill's rise above the land round it: its foot, for a loop round it
HERE_RADIUS = 24       # blocks round the player looked at for the rise they stand on
HERE_RISE = 4          # blocks above the land round it (its lowest fifth) that make a rise


def _loop(m: HeightMap, top: tuple, low: float) -> dict:
    """A loop round a hill near its foot: from its top out each of 16 ways till the ground is down to low;
    those points in order round, and its length."""
    tx, _ty, tz = top
    pts = []
    for k in range(16):
        a = math.radians(k * 22.5)
        dx, dz = math.sin(a), -math.cos(a)
        last = None
        for r in range(2, 96):
            x, z = round(tx + dx * r), round(tz + dz * r)
            v = m.at(x, z)
            if v is None:
                break
            last = (x, v, z)
            if v <= low:
                break
        if last is not None:
            pts.append(last)
    length = sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(pts, pts[1:] + pts[:1])) if pts else 0
    return {'length_blocks': round(length), 'points': [{'x': p[0], 'y': p[1], 'z': p[2]} for p in pts[::2]]}


def here(m: HeightMap, me: tuple) -> dict:
    """The player's own spot against the land round it (HERE_RADIUS): how much it rises; on a rise, its top
    (the highest within half that) and a loop round its foot."""
    x, z = int(me[0]), int(me[2])
    mine = ground_at(m, x, z)
    if mine is None:
        return {}
    ring = sorted(v for dz in range(-HERE_RADIUS, HERE_RADIUS + 1, 2) for dx in range(-HERE_RADIUS, HERE_RADIUS + 1, 2)
                  if dx * dx + dz * dz <= HERE_RADIUS ** 2 and (v := m.at(x + dx, z + dz)) is not None)
    if not ring:
        return {}
    low = ring[len(ring) // 5]
    out = {'rises_above_the_land_round_it': mine - low}
    if mine - low >= HERE_RISE:
        r = HERE_RADIUS // 2
        top = max(((x + dx, v, z + dz) for dz in range(-r, r + 1) for dx in range(-r, r + 1)
                   if (v := m.at(x + dx, z + dz)) is not None), key=lambda t: t[1])
        out['on_a_rise'] = True
        out['its_top'] = {'x': top[0], 'y': top[1], 'z': top[2]}
        out['round_its_foot'] = _loop(m, top, low + (top[1] - low) * FOOT)
    return out


def each_way(m: HeightMap, me: tuple) -> list[dict]:
    """The highest ground in each of the 8 directions (within the map), and how far."""
    mine = ground_at(m, int(me[0]), int(me[2]))
    out = []
    for n, name in enumerate(COMPASS):
        a = math.radians(n * 45)
        dx, dz = math.sin(a), -math.cos(a)
        best = None
        for r in range(4, max(m.sx, m.sz)):
            x, z = round(me[0] + dx * r), round(me[2] + dz * r)
            if not (m.x0 <= x < m.x0 + m.sx and m.z0 <= z < m.z0 + m.sz):
                break
            v = m.at(x, z)
            if v is not None and (best is None or v > best[1]):
                best = ((x, v, z), v, r)
        if best is not None:
            (x, v, z), _v, r = best
            out.append({'direction': name, 'highest': {'x': x, 'y': v, 'z': z}, 'distance': r,
                        'above_your_ground': None if mine is None else v - mine})
    return out
