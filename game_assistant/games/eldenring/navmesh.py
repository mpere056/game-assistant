"""Elden Ring's own navmeshes, as one walkable mesh around a route (see core/navmesh.py).

The navmeshes come from the game files (tools/get_navmesh.py -> .local/eldenring/navmesh/*.npz), one
file per map block:
- open world tiles m60_XX_ZZ_00: 256 m squares; tile-local coordinates run -128..128 around the tile
  centre, so world = (XX * 256 + x, y, ZZ * 256 + z). Measured: the player stood 2 m from the
  centre of the face under them in m60_42_37_00.
- legacy dungeons, caves, catacombs (m10..m45): their own coordinates, shifted onto the world map by
  the game's conversion rows (the same shift the place index uses).
Each block's mesh is separate; where a boundary edge of one block lies on a boundary edge of another
(tile borders, cave mouths, castle gates) they are joined, measured geometrically.

Known gaps: the DLC isn't installed (no m61 navmeshes); special edges (jumps, ladders, lifts) are not
used yet; underground areas without a world conversion (Siofra, Ainsel...) aren't joined to it.
"""
from __future__ import annotations

import math
import re
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

from ...core.navmesh import NavMesh
from .places import OPEN_WORLDS, TILE

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / '.local' / 'eldenring' / 'navmesh'
BLOCK = re.compile(r'm(\d\d)_(\d\d)_(\d\d)_(\d\d)$')
JOIN_SIDE = 0.35      # boundary edges this close sideways (metres, seen from above) are joined...
JOIN_HEIGHT = 1.0     # ...if this close in height
JOIN_OVERLAP = 0.3    # ...and overlapping this much along the edge
BORDER = 127.0        # tile-local |x| or |z| beyond this: an edge on a tile border


class Block:
    """One map block's navmesh in world coordinates."""

    def __init__(self, name: str, area: int, offset: np.ndarray, z):
        self.name, self.area = name, area
        v = z['vertices'].astype(np.float64)
        self.local = v
        self.v = v + offset
        F, E = z['faces'], z['edges']
        self.face_data = F[:, 3] if F.shape[1] > 3 else np.zeros(len(F), np.int32)
        counts = F[:, 2].astype(np.int64)
        self.face_start = np.concatenate([[0], np.cumsum(counts)])
        # Polygon vertex indices: each face's edges in order, vertex a of each edge.
        edge_idx = np.concatenate([np.arange(s, s + c) for s, c in zip(F[:, 1], counts)]) if len(F) else np.zeros(0, int)
        self.face_vidx = E[edge_idx, 0].astype(np.int64)
        owner = np.repeat(np.arange(len(F)), counts)
        opp = E[edge_idx, 2]
        a, b = E[edge_idx, 0], E[edge_idx, 1]
        inner = (opp >= 0) & (owner < opp)  # each shared edge once
        self.link_a, self.link_b = owner[inner], opp[inner].astype(np.int64)
        self.link_va, self.link_vb = a[inner].astype(np.int64), b[inner].astype(np.int64)
        self.portal_a, self.portal_b = self.v[a[inner]], self.v[b[inner]]
        bnd = opp < 0
        self.bnd_face = owner[bnd]
        self.bnd_va, self.bnd_vb = a[bnd].astype(np.int64), b[bnd].astype(np.int64)
        self.bnd_a, self.bnd_b = self.v[a[bnd]], self.v[b[bnd]]
        la, lb = v[a[bnd]], v[b[bnd]]
        if area in OPEN_WORLDS and name.endswith('_00'):  # tile borders only
            mid = (la + lb) / 2
            self.bnd_join = (np.abs(mid[:, 0]) > BORDER) | (np.abs(mid[:, 2]) > BORDER)
        else:
            self.bnd_join = np.ones(len(la), bool)  # a dungeon may join the world anywhere
        # The game's own special edges (ladders, lifts, jumps, doors), ends moved to world coordinates.
        u = z['user'] if 'user' in z.files and z['user'].ndim == 2 and z['user'].shape[1] == 19 else np.zeros((0, 19))
        self.user = u.astype(np.float64)
        if len(self.user):
            self.user[:, 7:10] += offset
            self.user[:, 10:13] += offset
        self.lo = self.v.min(axis=0) if len(v) else np.zeros(3)
        self.hi = self.v.max(axis=0) if len(v) else np.zeros(3)
        self.n_faces = len(F)


class WorldNavmesh:
    def __init__(self, conversions: list[dict], folder: Path = DIR, cache_blocks: int = 64):
        self.folder = folder
        self.conv = conversions
        self._cache: OrderedDict[str, Block] = OrderedDict()
        self._cache_size = cache_blocks
        self._lock = threading.Lock()
        self._legacy_bounds: dict[str, tuple] | None = None
        self.available = sorted(p.stem for p in folder.glob('m*.npz')) if folder.exists() else []

    @property
    def ok(self) -> bool:
        return bool(self.available)

    def offset(self, name: str) -> tuple[int, np.ndarray] | None:
        m = BLOCK.match(name)
        if not m:
            return None
        aa, bb, cc, dd = (int(g) for g in m.groups())
        if aa in OPEN_WORLDS:
            if dd != 0:
                return None
            return aa, np.array([bb * TILE, 0.0, cc * TILE])
        for c in self.conv:
            if c['src'] == (aa, bb, cc) and c['dst'][0] in OPEN_WORLDS:
                d = c['dst']
                dst = np.array([d[1] * TILE + c['dst_pos'][0], c['dst_pos'][1], d[2] * TILE + c['dst_pos'][2]])
                return d[0], dst - np.asarray(c['src_pos'], dtype=np.float64)
        return None

    def block(self, name: str) -> Block | None:
        with self._lock:
            if name in self._cache:
                self._cache.move_to_end(name)
                return self._cache[name]
        off = self.offset(name)
        path = self.folder / f'{name}.npz'
        if off is None or not path.exists():
            return None
        with np.load(path) as z:
            blk = Block(name, off[0], off[1], z)
        with self._lock:
            self._cache[name] = blk
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return blk

    def _legacy(self) -> dict[str, tuple]:
        """World bounds (area, lo, hi) of every non-tile block with a world conversion (computed
        once from the vertex arrays, about a second)."""
        if self._legacy_bounds is None:
            out = {}
            for name in self.available:
                m = BLOCK.match(name)
                if not m or int(m.group(1)) in OPEN_WORLDS:
                    continue
                off = self.offset(name)
                if off is None:
                    continue
                with np.load(self.folder / f'{name}.npz') as z:
                    v = z['vertices']
                if len(v):
                    out[name] = (off[0], v.min(axis=0) + off[1], v.max(axis=0) + off[1])
            self._legacy_bounds = out
        return self._legacy_bounds

    def blocks_in(self, area: int, lo: tuple[float, float], hi: tuple[float, float]) -> list[str]:
        """Blocks overlapping the x-z box lo..hi on world map `area`."""
        names = []
        for gx in range(math.floor((lo[0] + 128) / TILE), math.floor((hi[0] + 128) / TILE) + 1):
            for gz in range(math.floor((lo[1] + 128) / TILE), math.floor((hi[1] + 128) / TILE) + 1):
                n = f'm{area:02d}_{gx:02d}_{gz:02d}_00'
                if (self.folder / f'{n}.npz').exists():
                    names.append(n)
        for n, (a, blo, bhi) in self._legacy().items():
            if a == area and blo[0] <= hi[0] and bhi[0] >= lo[0] and blo[2] <= hi[1] and bhi[2] >= lo[1]:
                names.append(n)
        return names

    def mesh(self, area: int, lo: tuple[float, float], hi: tuple[float, float]) -> tuple[NavMesh, list[str]] | None:
        """One NavMesh of all blocks in the box, joined at shared boundary edges."""
        blocks = [b for b in (self.block(n) for n in self.blocks_in(area, lo, hi)) if b is not None and b.n_faces]
        if not blocks:
            return None
        v_off = f_off = fv_off = 0
        verts, fstarts, fvidx, la, lb, pa, pb, costs = [], [], [], [], [], [], [], []
        bnd = []  # per block: (global face ids, a, b, joinable)
        for blk in blocks:
            verts.append(blk.v)
            fstarts.append(blk.face_start[:-1] + fv_off)
            fvidx.append(blk.face_vidx + v_off)
            la.append(blk.link_a + f_off)
            lb.append(blk.link_b + f_off)
            pa.append(blk.portal_a)
            pb.append(blk.portal_b)
            costs.append(np.ones(blk.n_faces))
            bnd.append((blk.bnd_face + f_off, blk.bnd_a, blk.bnd_b, blk.bnd_join))
            v_off += len(blk.v)
            f_off += blk.n_faces
            fv_off += len(blk.face_vidx)
        jl = _join(bnd)
        face_vidx = np.concatenate(fvidx)
        face_start = np.concatenate(fstarts + [[len(face_vidx)]])
        link_a = np.concatenate(la + [jl[0]])
        link_b = np.concatenate(lb + [jl[1]])
        portal_a = np.concatenate(pa + [jl[2]])
        portal_b = np.concatenate(pb + [jl[3]])
        mesh = NavMesh(np.concatenate(verts), face_start, face_vidx, link_a, link_b, portal_a, portal_b,
                       np.concatenate(costs))
        mesh.joins = len(jl[0])
        return mesh, [b.name for b in blocks]


def _join(bnd: list[tuple]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Links between boundary edges of different blocks that lie on each other."""
    faces, A, B, block_of, join = [], [], [], [], []
    for k, (f, a, b, j) in enumerate(bnd):
        faces.append(f)
        A.append(a)
        B.append(b)
        block_of.append(np.full(len(f), k))
        join.append(j)
    if not faces:
        return (np.zeros(0, np.int64),) * 2 + (np.zeros((0, 3)),) * 2
    faces, A, B = np.concatenate(faces), np.concatenate(A), np.concatenate(B)
    block_of, join = np.concatenate(block_of), np.concatenate(join)
    sel = np.nonzero(join)[0]
    cell = 4.0
    mids = (A + B) / 2
    keys = np.floor(mids[:, [0, 2]] / cell).astype(np.int64)
    buckets: dict[tuple[int, int], list[int]] = {}
    for i in sel.tolist():
        buckets.setdefault((int(keys[i, 0]), int(keys[i, 1])), []).append(i)
    out_a, out_b, out_pa, out_pb = [], [], [], []
    seen = set()
    for i in sel.tolist():
        kx, kz = int(keys[i, 0]), int(keys[i, 1])
        a, b = A[i], B[i]
        d = b - a
        L = math.hypot(d[0], d[2])
        if L < 1e-3:
            continue
        ux, uz = d[0] / L, d[2] / L
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for j in buckets.get((kx + dx, kz + dz), ()):
                    if j <= i or block_of[j] == block_of[i] or (i, j) in seen:
                        continue
                    c, e = A[j], B[j]
                    ts, ok = [], True
                    for q in (c, e):
                        rx, rz = q[0] - a[0], q[2] - a[2]
                        t = rx * ux + rz * uz
                        side = abs(rx * uz - rz * ux)
                        h = a[1] + d[1] * min(max(t / L, 0), 1)
                        if side > JOIN_SIDE or abs(q[1] - h) > JOIN_HEIGHT:
                            ok = False
                            break
                        ts.append(t)
                    if not ok:
                        continue
                    t0, t1 = max(0.0, min(ts)), min(L, max(ts))
                    if t1 - t0 < JOIN_OVERLAP:
                        continue
                    seen.add((i, j))
                    out_a.append(faces[i])
                    out_b.append(faces[j])
                    out_pa.append(a + d * (t0 / L))
                    out_pb.append(a + d * (t1 / L))
    if not out_a:
        return (np.zeros(0, np.int64),) * 2 + (np.zeros((0, 3)),) * 2
    return np.array(out_a), np.array(out_b), np.array(out_pa), np.array(out_pb)
