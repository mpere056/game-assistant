"""Elden Ring's whole walkable world as one graph, built once and loaded piece by piece while a route
is searched (the way Minecraft's Baritone keeps a compact chunk cache).

Built from the converted navmeshes (.local/eldenring/navmesh/*.npz, see navmesh.py and
tools/get_navmesh.py) into .local/eldenring/navgraph/:
- one file per map block, in world coordinates, with everything a search needs ready to use:
  face polygons and bounds, centres, neighbours inside the block with their shared edges, and links
  to faces of other blocks;
- index.json: each block's world map (60 = the Lands Between) and bounds.
Links between blocks are found once here (boundary edges of different blocks lying on each other:
tile borders; dungeons where they touch the world), not on every route.

At run time `WorldGraph` is a core.navmesh Source: it loads a block the first time the search (or a
locate) needs it and keeps the last few hundred in memory.
"""
from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

from ...core.navmesh import closest_on_poly, locate_in
from .navmesh import BORDER, DIR as MESH_DIR, JOIN_HEIGHT, JOIN_OVERLAP, JOIN_SIDE, Block, WorldNavmesh
from .places import OPEN_WORLDS

ROOT = Path(__file__).resolve().parents[3]
GRAPH_DIR = ROOT / '.local' / 'eldenring' / 'navgraph'
INDEX = GRAPH_DIR / 'index.json'
CONVERSIONS = ROOT / '.local' / 'eldenring' / 'legacy_conversions.json'  # saved by the adapter
FACE_BITS = 24  # node id = block number << 24 | face
# Dungeon entrances: a dungeon's mesh stops at its threshold and the open world's a few metres before
# it (measured at a cave mouth near Stormhill: a steady 5.8-6.4 m gap, 1.5 m up). Boundary edges of a
# dungeon and of the open world this close, facing each other, are linked.
GAP_ACROSS = 8.0
GAP_UP = 3.0
# Drops: from a ledge edge down to ground just beyond it (one way). The navmesh never joins faces
# across a drop, so without these a ledge in front of a cave was a dead end. 6 m is a deliberately
# safe fall; each drop on a chosen route is checked with a live ray before it is used.
DROP_MIN, DROP_MAX = 0.8, 6.0
DROP_OUT = (0.8, 1.6, 2.5)   # metres beyond the edge where the landing is looked for
# Steps: same-level ground just beyond a boundary edge, where the mesh has a seam or a missing strip
# (measured in a cave passage near Stormhill: no mesh for ~10 m of the passage, the last piece 2.4-2.8 m
# from the ground outside). Looked for at all the DROP_OUT distances; the far side of a tree trunk can
# match too, so steps cost extra in the search and are checked with knee and chest rays before use.
STEP_UP = 1.0
STEP_ACROSS = 3.5      # boundary edges at most this far apart (middle to middle) ...
STEP_FACING = 0.5      # ... and facing each other (cosine), at most STEP_UP apart in height
WALK, ENTRANCE, DROP, STEP = 0, 1, 2, 3  # link kinds


# ---- building (once, after extraction) ----

def save_conversions(conv: list[dict]) -> None:
    """The game's dungeon-to-world-map shifts (read from its memory by the adapter), so the graph can be
    built without the game running."""
    CONVERSIONS.parent.mkdir(parents=True, exist_ok=True)
    CONVERSIONS.write_text(json.dumps([{k: list(v) if isinstance(v, tuple) else v for k, v in c.items()}
                                       for c in conv]), encoding='utf-8')


def load_conversions() -> list[dict] | None:
    if not CONVERSIONS.exists():
        return None
    return [{k: tuple(v) if k in ('src', 'dst') else v for k, v in c.items()}
            for c in json.loads(CONVERSIONS.read_text(encoding='utf-8'))]


def build(conversions: list[dict] | None = None, progress=print) -> dict:
    conversions = conversions if conversions is not None else load_conversions()
    if conversions is None:
        raise RuntimeError('run the assistant with the game once first (it saves the dungeon map shifts)')
    wn = WorldNavmesh(conversions)
    names = [n for n in wn.available if wn.offset(n) is not None]
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    index, cand = [], []
    legacy_boxes: dict[int, list] = {}
    blocks: dict[str, Block] = {}
    # Pass 1: every block's bounds (dungeon boxes decide which open-world edges may join them).
    for name in names:
        b = wn.block(name)
        if b is None or not b.n_faces:
            continue
        index.append({'name': name, 'area': b.area, 'lo': b.lo.tolist(), 'hi': b.hi.tolist(), 'faces': b.n_faces})
        if b.area in OPEN_WORLDS and not name.startswith(('m60', 'm61')):
            legacy_boxes.setdefault(b.area, []).append((b.lo, b.hi))
    num = {e['name']: i for i, e in enumerate(index)}
    progress(f'  {len(index)} blocks with walkable faces')
    # Pass 2: candidate boundary edges for joining.
    for e in index:
        b = wn.block(e['name'])
        join = b.bnd_join.copy()
        if e['name'].startswith(('m60', 'm61')):  # open-world edges inside a dungeon's box may join it
            mid = (b.bnd_a + b.bnd_b) / 2
            for lo, hi in legacy_boxes.get(b.area, []):
                join |= ((mid[:, 0] >= lo[0] - 2) & (mid[:, 0] <= hi[0] + 2) & (mid[:, 2] >= lo[2] - 2) &
                         (mid[:, 2] <= hi[2] + 2) & (mid[:, 1] >= lo[1] - 5) & (mid[:, 1] <= hi[1] + 5))
        sel = np.nonzero(join)[0]
        cand.append((num[e['name']], b.area, b.bnd_face[sel], b.bnd_a[sel], b.bnd_b[sel], b.bnd_va[sel], b.bnd_vb[sel]))
    links = _join_all(cand)
    progress(f'  {sum(len(v) for v in links.values()) // 2} links between blocks')
    gaps = _entrance_links(wn, index, num)
    for k, v in gaps.items():
        links.setdefault(k, []).extend(v)
    n_gaps = sum(len(v) for v in gaps.values()) // 2
    progress(f'  {n_gaps} entrance links (dungeon thresholds)')
    n_drops = 0
    for e in index:
        d = _drop_links(wn.block(e['name']), num[e['name']])
        if d:
            links.setdefault(num[e['name']], []).extend(d)
            n_drops += len(d)
    progress(f'  {n_drops} drops (one way, down ledges up to {DROP_MAX:.0f} m)')
    n_steps = 0
    for e in index:
        for bi, fi, bj, fj, a1, b1, a2, b2 in _step_links(wn, index, num, e):
            # The portal: each side's own edge (vertex numbers in its own block).
            links.setdefault(bi, []).append((fi, bj, fj, a1, b1, STEP))
            links.setdefault(bj, []).append((fj, bi, fi, a2, b2, STEP))
            n_steps += 1
    progress(f'  {n_steps} steps (gaps up to {STEP_ACROSS} m at the same level, both ways)')
    # Pass 3: one ready-to-search file per block.
    for e in index:
        b = wn.block(e['name'])
        _save_block(b, num[e['name']], links.get(num[e['name']], []))
    INDEX.write_text(json.dumps({'blocks': index}), encoding='utf-8')
    return {'blocks': len(index), 'entrance_links': n_gaps, 'drops': n_drops, 'steps': n_steps}


def _join_all(cand: list[tuple]) -> dict[int, list[tuple]]:
    """Links between boundary edges of different blocks that lie on each other (both directions).
    Each link's portal is its own side's edge (vertex numbers in that block)."""
    cell = 4.0
    buckets: dict[tuple, list] = {}
    edges = []
    for bid, area, faces, A, B, VA, VB in cand:
        mids = (A + B) / 2
        keys = np.floor(mids[:, [0, 2]] / cell).astype(np.int64)
        for k in range(len(faces)):
            idx = len(edges)
            edges.append((bid, int(faces[k]), A[k], B[k], int(VA[k]), int(VB[k])))
            buckets.setdefault((area, int(keys[k, 0]), int(keys[k, 1])), []).append(idx)
    out: dict[int, list] = {}
    for (area, kx, kz), mine in buckets.items():
        near = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                near += buckets.get((area, kx + dx, kz + dz), [])
        for i in mine:
            bi, fi, a, b, vai, vbi = edges[i]
            d = b - a
            L = math.hypot(d[0], d[2])
            if L < 1e-3:
                continue
            ux, uz = d[0] / L, d[2] / L
            for j in near:
                bj, fj, c, e, vaj, vbj = edges[j]
                if j <= i or bj == bi:
                    continue
                ts, ok = [], True
                for q in (c, e):
                    rx, rz = q[0] - a[0], q[2] - a[2]
                    t = rx * ux + rz * uz
                    if abs(rx * uz - rz * ux) > JOIN_SIDE or abs(q[1] - (a[1] + d[1] * min(max(t / L, 0), 1))) > JOIN_HEIGHT:
                        ok = False
                        break
                    ts.append(t)
                if not ok:
                    continue
                t0, t1 = max(0.0, min(ts)), min(L, max(ts))
                if t1 - t0 < JOIN_OVERLAP:
                    continue
                out.setdefault(bi, []).append((fi, bj, fj, vai, vbi, WALK))
                out.setdefault(bj, []).append((fj, bi, fi, vaj, vbj, WALK))
    return out


def _outward(b: Block) -> np.ndarray:
    """For each boundary edge: unit x-z direction pointing out of its face (away from the mesh)."""
    counts = np.diff(b.face_start)
    owner = np.repeat(np.arange(b.n_faces), counts)
    sums = np.zeros((b.n_faces, 3))
    np.add.at(sums, owner, b.v[b.face_vidx])
    cent = sums / np.maximum(counts, 1)[:, None]
    out = (b.bnd_a + b.bnd_b) / 2 - cent[b.bnd_face]
    out[:, 1] = 0
    return out / np.maximum(np.linalg.norm(out, axis=1), 1e-9)[:, None]


def _entrance_links(wn: WorldNavmesh, index: list[dict], num: dict) -> dict[int, list[tuple]]:
    """Links across the small gap between a dungeon's mesh and the open world's at its entrance."""
    out: dict[int, list] = {}
    tiles = [e for e in index if e['name'].startswith(('m60', 'm61'))]
    for e in index:
        if e['name'].startswith(('m60', 'm61')):
            continue
        d = wn.block(e['name'])
        lo, hi = np.array(e['lo']) - GAP_ACROSS, np.array(e['hi']) + GAP_ACROSS
        near = [t for t in tiles if t['area'] == e['area'] and t['lo'][0] <= hi[0] and t['hi'][0] >= lo[0]
                and t['lo'][2] <= hi[2] and t['hi'][2] >= lo[2]]
        if not near:
            continue
        dm = (d.bnd_a + d.bnd_b) / 2
        dout = _outward(d)
        cell = GAP_ACROSS
        grid: dict[tuple, list] = {}
        world = []
        for t in near:
            w = wn.block(t['name'])
            wm = (w.bnd_a + w.bnd_b) / 2
            inside = ((wm[:, 0] >= lo[0]) & (wm[:, 0] <= hi[0]) & (wm[:, 2] >= lo[2]) & (wm[:, 2] <= hi[2]))
            wout = _outward(w)
            for k in np.nonzero(inside)[0].tolist():
                grid.setdefault((int(wm[k, 0] // cell), int(wm[k, 2] // cell)), []).append(len(world))
                world.append((num[t['name']], int(w.bnd_face[k]), wm[k], wout[k], int(w.bnd_va[k]), int(w.bnd_vb[k])))
        if not world:
            continue
        for i in range(len(dm)):
            m = dm[i]
            gx, gz = int(m[0] // cell), int(m[2] // cell)
            best = None
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((gx + dx, gz + dz), ()):
                        wb, wf, wm_, wo, wa, wbb = world[j]
                        gap = wm_ - m
                        across = math.hypot(gap[0], gap[2])
                        if across > GAP_ACROSS or abs(gap[1]) > GAP_UP or across < 1e-3:
                            continue
                        u = (gap[0] / across, gap[2] / across)
                        # Facing each other: the dungeon edge opens toward the world edge and back.
                        if dout[i][0] * u[0] + dout[i][2] * u[1] < 0.5 or -(wo[0] * u[0] + wo[2] * u[1]) < 0.5:
                            continue
                        if best is None or across < best[0]:
                            best = (across, wb, wf, wa, wbb)
            if best is not None:
                _, wb, wf, wa, wbb = best
                db, df = num[e['name']], int(d.bnd_face[i])
                out.setdefault(db, []).append((df, wb, wf, int(d.bnd_va[i]), int(d.bnd_vb[i]), ENTRANCE))
                out.setdefault(wb, []).append((wf, db, df, wa, wbb, ENTRANCE))
    return out


def _drop_links(b: Block, bid: int) -> list[tuple]:
    """One-way links from boundary edges down to a face of the same block just beyond them."""
    from ...core.navmesh import height_in
    n = b.n_faces
    counts = np.diff(b.face_start)
    owner = np.repeat(np.arange(n), counts)
    pts = b.v[b.face_vidx]
    lo = np.full((n, 3), np.inf)
    hi = np.full((n, 3), -np.inf)
    np.minimum.at(lo, owner, pts)
    np.maximum.at(hi, owner, pts)
    cell = 4.0
    grid: dict[tuple, list] = {}
    gx0, gz0 = np.floor(lo[:, 0] / cell).astype(int), np.floor(lo[:, 2] / cell).astype(int)
    gx1, gz1 = np.floor(hi[:, 0] / cell).astype(int), np.floor(hi[:, 2] / cell).astype(int)
    for f in range(n):
        if (gx1[f] - gx0[f] + 1) * (gz1[f] - gz0[f] + 1) > 400:  # a huge face: index its corners' cells only
            cells = {(gx0[f], gz0[f]), (gx1[f], gz1[f]), (gx0[f], gz1[f]), (gx1[f], gz0[f])}
        else:
            cells = [(x, z) for x in range(gx0[f], gx1[f] + 1) for z in range(gz0[f], gz1[f] + 1)]
        for c in cells:
            grid.setdefault(c, []).append(f)
    out_dir = _outward(b)
    mids = (b.bnd_a + b.bnd_b) / 2
    links, seen = [], set()
    for i in range(len(mids)):
        m, o, f0 = mids[i], out_dir[i], int(b.bnd_face[i])
        for dist in DROP_OUT:
            qx, qz = m[0] + o[0] * dist, m[2] + o[2] * dist
            best = None
            for f in grid.get((int(qx // cell), int(qz // cell)), ()):
                if f == f0 or not (lo[f, 0] <= qx <= hi[f, 0] and lo[f, 2] <= qz <= hi[f, 2]):
                    continue
                if hi[f, 1] < m[1] - DROP_MAX - 1 or lo[f, 1] > m[1] + STEP_UP + 1:
                    continue
                h = height_in(b.v[b.face_vidx[b.face_start[f]:b.face_start[f + 1]]], qx, qz)
                if h is None:
                    continue
                if m[1] - DROP_MAX <= h <= m[1] - DROP_MIN and (best is None or h > best[0]):
                    best = (h, f)
            if best is not None:
                if (f0, best[1]) not in seen:
                    seen.add((f0, best[1]))
                    links.append((f0, bid, best[1], int(b.bnd_va[i]), int(b.bnd_vb[i]), DROP))
                break
    return links


def _step_links(wn: WorldNavmesh, index: list[dict], num: dict, e: dict) -> list[tuple]:
    """Links across small gaps in the mesh at the same level: a boundary edge of block e and a boundary
    edge (of e or a block overlapping it) facing each other across at most STEP_ACROSS metres.
    Each pair is made once, by the block with the smaller number, in both directions."""
    me = num[e['name']]
    lo, hi = np.array(e['lo']) - STEP_ACROSS, np.array(e['hi']) + STEP_ACROSS
    blocks = [x for x in index if x['area'] == e['area'] and num[x['name']] >= me and x['lo'][0] <= hi[0]
              and x['hi'][0] >= lo[0] and x['lo'][2] <= hi[2] and x['hi'][2] >= lo[2]]
    mids, outs, owner, face, A, B = [], [], [], [], [], []  # A, B: vertex numbers in the owner block
    for x in blocks:
        b = wn.block(x['name'])
        m = (b.bnd_a + b.bnd_b) / 2
        keep = (m[:, 0] >= lo[0]) & (m[:, 0] <= hi[0]) & (m[:, 2] >= lo[2]) & (m[:, 2] <= hi[2])
        mids.append(m[keep])
        outs.append(_outward(b)[keep])
        owner.append(np.full(int(keep.sum()), num[x['name']]))
        face.append(b.bnd_face[keep])
        A.append(b.bnd_va[keep])
        B.append(b.bnd_vb[keep])
    M, O, W, F = np.concatenate(mids), np.concatenate(outs), np.concatenate(owner), np.concatenate(face)
    A, B = np.concatenate(A), np.concatenate(B)
    cell = STEP_ACROSS
    keys = np.floor(M[:, [0, 2]] / cell).astype(np.int64)
    grid: dict[tuple, list] = {}
    for k, (kx, kz) in enumerate(keys.tolist()):
        grid.setdefault((kx, kz), []).append(k)
    grid = {c: np.array(v) for c, v in grid.items()}
    links = []
    mine = np.nonzero(W == me)[0]
    for i in mine.tolist():
        kx, kz = keys[i]
        cand = [grid[c] for c in ((kx + dx, kz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)) if c in grid]
        if not cand:
            continue
        j = np.concatenate(cand)
        j = j[(j > i) | (W[j] != me)]  # each pair once
        if not len(j):
            continue
        gap = M[j] - M[i]
        across = np.hypot(gap[:, 0], gap[:, 2])
        ok = (across <= STEP_ACROSS) & (across > 0.05) & (np.abs(gap[:, 1]) <= STEP_UP) & ((W[j] != me) | (F[j] != F[i]))
        if not ok.any():
            continue
        j, gap, across = j[ok], gap[ok], across[ok]
        ux, uz = gap[:, 0] / across, gap[:, 2] / across
        face_i = O[i, 0] * ux + O[i, 2] * uz
        face_j = -(O[j, 0] * ux + O[j, 2] * uz)
        ok = (face_i >= STEP_FACING) & (face_j >= STEP_FACING)
        for k in j[ok].tolist():
            links.append((int(W[i]), int(F[i]), int(W[k]), int(F[k]), int(A[i]), int(B[i]), int(A[k]), int(B[k])))
    return links


def _save_block(b: Block, bid: int, links: list[tuple]) -> None:
    n = b.n_faces
    counts = np.diff(b.face_start)
    owner = np.repeat(np.arange(n), counts)
    pts = b.v[b.face_vidx]
    sums = np.zeros((n, 3))
    np.add.at(sums, owner, pts)
    centre = sums / np.maximum(counts, 1)[:, None]
    lo = np.full((n, 3), np.inf)
    hi = np.full((n, 3), -np.inf)
    np.minimum.at(lo, owner, pts)
    np.maximum.at(hi, owner, pts)
    a = np.concatenate([b.link_a, b.link_b]).astype(np.int64)
    c = np.concatenate([b.link_b, b.link_a]).astype(np.int64)
    va = np.concatenate([b.link_va, b.link_vb])
    vb = np.concatenate([b.link_vb, b.link_va])
    order = np.argsort(a, kind='stable')
    nb_start = np.searchsorted(a[order], np.arange(n + 1))
    # Links to other faces (other blocks, drops, steps): face, block, its face, kind, edge vertices here.
    ext = np.array([(f, ob, of, k, x, y) for f, ob, of, x, y, k in links], dtype=np.int32).reshape(-1, 6)
    i32, f32 = np.int32, np.float32  # 32-bit floats: 1 mm at 20 km
    np.savez(GRAPH_DIR / f'{b.name}.npz', bid=bid, v=b.v.astype(f32), face_start=b.face_start.astype(i32),
             face_vidx=b.face_vidx.astype(i32), centre=centre.astype(f32), lo=lo.astype(f32), hi=hi.astype(f32),
             nb_start=nb_start.astype(i32), nb_face=c[order].astype(i32), nb_va=va[order].astype(i32),
             nb_vb=vb[order].astype(i32), ext=ext, face_data=b.face_data)


# ---- searching (a core.navmesh Source) ----

class _Loaded:
    __slots__ = ('bid', 'v', 'fs', 'fv', 'lo', 'hi', 'cent', 'nbrs', 'cost')

    def __init__(self, bid: int, z):
        self.bid = bid
        self.v, self.fs, self.fv = z['v'], z['face_start'], z['face_vidx']
        self.lo, self.hi = z['lo'], z['hi']
        n = len(self.fs) - 1
        self.cent = [tuple(c) for c in z['centre'].tolist()]
        base = bid << FACE_BITS
        vl = [tuple(r) for r in self.v.astype(np.float64).tolist()]
        ns = z['nb_start'].tolist()
        nf = (z['nb_face'].astype(np.int64) + base).tolist()
        va, vb = z['nb_va'].tolist(), z['nb_vb'].tolist()
        self.nbrs = [[(nf[k], vl[va[k]], vl[vb[k]]) for k in range(ns[f], ns[f + 1])] for f in range(n)]
        for f, ob, of, kind, x, y in z['ext'].tolist():
            self.nbrs[f].append(((ob << FACE_BITS) | of, vl[x], vl[y], kind))
        self.cost = [1.0] * n

    def poly(self, f: int) -> np.ndarray:
        return self.v[self.fv[self.fs[f]:self.fs[f + 1]]]


class WorldGraph:
    def __init__(self, folder: Path = GRAPH_DIR, cache_blocks: int = 300):
        self.folder = folder
        self.ok = (folder / 'index.json').exists()
        idx = json.loads((folder / 'index.json').read_text(encoding='utf-8'))['blocks'] if self.ok else []
        self.names = [e['name'] for e in idx]
        self.areas = np.array([e['area'] for e in idx]) if idx else np.zeros(0, int)
        self.lo = np.array([e['lo'] for e in idx]) if idx else np.zeros((0, 3))
        self.hi = np.array([e['hi'] for e in idx]) if idx else np.zeros((0, 3))
        self.area = 60
        self.banned: set[tuple[int, int]] = set()  # (from node, to node) links found blocked in the game
        self._cache: OrderedDict[int, _Loaded] = OrderedDict()
        self._size = cache_blocks
        self._lock = threading.Lock()
        self.loads = 0

    def block(self, bid: int) -> _Loaded:
        with self._lock:
            blk = self._cache.get(bid)
            if blk is not None:
                self._cache.move_to_end(bid)
                return blk
        with np.load(self.folder / f'{self.names[bid]}.npz') as z:
            blk = _Loaded(bid, z)
        self.loads += 1
        with self._lock:
            self._cache[bid] = blk
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return blk

    # Source
    def locate(self, p, max_below=4.0, max_above=2.5, max_side=6.0):
        x, y, z = p
        m = max_side
        cand = np.nonzero((self.areas == self.area) & (self.lo[:, 0] - m <= x) & (x <= self.hi[:, 0] + m) &
                          (self.lo[:, 2] - m <= z) & (z <= self.hi[:, 2] + m) &
                          (self.lo[:, 1] - max_above - m <= y) & (y <= self.hi[:, 1] + max_below + m))[0]
        best = None
        for bid in cand.tolist():
            blk = self.block(bid)
            r = locate_in(blk.lo, blk.hi, blk.poly, p, max_below, max_above, max_side)
            if r is not None and (best is None or r[2] < best[2]):
                best = ((bid << FACE_BITS) | r[0], r[1], r[2])
        return best

    def neighbours(self, node):
        nb = self.block(node >> FACE_BITS).nbrs[node & ((1 << FACE_BITS) - 1)]
        if self.banned:
            nb = [x for x in nb if (node, x[0]) not in self.banned]
        return nb

    def centre(self, node):
        return self.block(node >> FACE_BITS).cent[node & ((1 << FACE_BITS) - 1)]

    def cost(self, node):
        return self.block(node >> FACE_BITS).cost[node & ((1 << FACE_BITS) - 1)]

    def closest_on(self, node, p):
        return closest_on_poly(self.block(node >> FACE_BITS).poly(node & ((1 << FACE_BITS) - 1)), p)

    def block_name(self, node) -> str:
        return self.names[node >> FACE_BITS]
