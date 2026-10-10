"""Convert Elden Ring navmesh pieces (Havok 2018 tagfiles, .hkx) into compact NumPy files.

Runs in its own environment, `.local/tools/soulstruct-venv` (Python 3.13), because it uses
Soulstruct's Havok reader (soulstruct + soulstruct-havok by Grimrukh, GPL-3.0, installed from
GitHub, never copied into this repository). `get_navmesh.py` creates that environment and calls this
script; you don't run it by hand.

    python navmesh_convert.py <folder with *.hkx> <output .npz>

One .npz per map block, all its pieces together:
  vertices  float32 (V, 3)   map-local metres (x, y up, z)
  faces     int32   (F, 4)   piece, first edge index, edge count, face data (Havok faceData)
  edges     int32   (E, 5)   vertex a, vertex b, opposite face (-1: boundary), flags, edge data
  pieces    str     (P,)     piece names (n31_03_00_00_000200 ...)
  user      float32 (U, 19)  special edges (Havok user edges: ladders, jumps, lifts...): piece, type A,
                             type B, cost A->B, cost B->A, direction (3 both ways), space, end A x y z,
                             end B x y z, half size A x y z, half size B x y z
Face and vertex indices in `faces`/`edges` are global within the file (piece offsets applied).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soulstruct.havok.types.hk2018 as _hk2018
import soulstruct.havok.types.hk2018._hkcd as _hkcd

for _name in dir(_hkcd):  # upstream bug (soulstruct-havok 1.5.0): hk2018/__init__ doesn't import _hkcd
    if _name.startswith('hkcd'):
        setattr(_hk2018, _name, getattr(_hkcd, _name))
from soulstruct.havok.fromsoft.eldenring.file_types import NavmeshHKX  # noqa: E402

NO_FACE = 0xFFFFFFFF


def convert(folder: Path, out: Path) -> dict:
    if not folder.is_dir():  # never write an empty result for pieces that went missing
        raise FileNotFoundError(f'no folder {folder}')
    verts, faces, edges, pieces, user = [], [], [], [], []
    v_off = f_off = e_off = 0
    for path in sorted(folder.glob('*.hkx')):
        if not path.name.startswith('n'):  # o* pieces: a coarser copy (big characters); 9xxxxx: empty
            continue
        hkx = NavmeshHKX.from_path(path)
        nm = next(nv.variant for nv in hkx.root.namedVariants if nv.name == 'hkaiNavMesh')
        if not nm.faces:
            continue
        p = len(pieces)
        pieces.append(path.stem)
        v = np.asarray(nm.vertices, dtype=np.float32)[:, :3]
        verts.append(v)
        fdata = list(nm.faceData) if nm.faceDataStriding == 1 else [0] * len(nm.faces)
        edata = list(nm.edgeData) if nm.edgeDataStriding == 1 else [0] * len(nm.edges)
        for f, fd in zip(nm.faces, fdata):
            faces.append((p, f.startEdgeIndex + e_off, f.numEdges, fd))
        for e, ed in zip(nm.edges, edata):
            opp = -1 if e.oppositeFace == NO_FACE else e.oppositeFace + f_off
            edges.append((e.a + v_off, e.b + v_off, opp, e.flags, ed))
        for nv in hkx.root.namedVariants:
            if nv.name == 'hkaiUserEdgeSetupArray':
                for s in nv.variant.edgeSetups:
                    user.append(_user_edge(p, s))
        v_off += len(v)
        f_off += len(nm.faces)
        e_off += len(nm.edges)
    np.savez_compressed(
        out,
        vertices=np.concatenate(verts) if verts else np.zeros((0, 3), np.float32),
        faces=np.asarray(faces, dtype=np.int64).astype(np.int32).reshape(-1, 4),
        edges=np.asarray(edges, dtype=np.int64).astype(np.int32).reshape(-1, 5),
        pieces=np.asarray(pieces),
        user=np.asarray([u for u in user if u is not None], dtype=np.float32).reshape(-1, 19),
    )
    return {'pieces': len(pieces), 'vertices': v_off, 'faces': f_off, 'edges': e_off, 'user_edges': len(user)}


def _user_edge(piece: int, setup) -> tuple | None:
    """One special edge: its two ends (box centres from the boxes' transforms), types, costs, direction."""
    try:
        def centre(obb):
            t = list(obb.transform) if not hasattr(obb.transform, 'translation') else None
            if t is not None and len(t) >= 15:
                return float(t[12]), float(t[13]), float(t[14])
            tr = obb.transform.translation
            return float(tr[0]), float(tr[1]), float(tr[2])
        a, b = centre(setup.obbA), centre(setup.obbB)
        ha, hb = setup.obbA.halfExtents, setup.obbB.halfExtents
        return (piece, int(setup.userDataA), int(setup.userDataB), float(setup.costAtoB), float(setup.costBtoA),
                int(setup.direction), int(setup.space), *a, *b, float(ha[0]), float(ha[1]), float(ha[2]),
                float(hb[0]), float(hb[1]), float(hb[2]))
    except Exception:  # an unknown layout: skip it
        return None


if __name__ == '__main__':
    if len(sys.argv) == 3:
        print(convert(Path(sys.argv[1]), Path(sys.argv[2])))
    else:  # many: <list file with "folder<TAB>out" lines>
        for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
            src, dst = line.split('\t')
            try:
                info = convert(Path(src), Path(dst))
                print(Path(dst).stem, info, flush=True)
            except Exception as e:  # report and go on with the next map
                print(Path(dst).stem, 'ERROR', type(e).__name__, e, flush=True)
