"""Extract Elden Ring's navmeshes (the game's own walkable-area meshes) into .local/eldenring/navmesh/.

    .venv\\Scripts\\python -m game_assistant.tools.get_navmesh [--workers 4] [--only m60_42_37_00 ...]

Steps (each is skipped when already done):
1. Download the archive keys and file-name list (Smithbox, MIT) into .local/eldenring/archive/.
2. Create .local/tools/soulstruct-venv (Python 3.13 via uv) with Soulstruct's Havok reader (GPL-3.0,
   from GitHub; it stays in .local and is only run as a separate program).
3. For each map block with a navmesh: read its container out of Data0-3.bdt (read-only), write
   the n* pieces to a temporary folder, convert them to one .npz (navmesh_convert.py), and delete
   the temporary pieces. About 700 blocks; a block that is already converted is skipped.
Nothing is written to the game folder and nothing extracted goes into the repository.
"""
from __future__ import annotations

import argparse
import os
import ntpath
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..games.eldenring import archive as ar

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.local' / 'eldenring' / 'navmesh'
VENV = ROOT / '.local' / 'tools' / 'soulstruct-venv'
TOOL_PY = VENV / 'Scripts' / 'python.exe'
CONVERT = Path(__file__).with_name('navmesh_convert.py')
PACKAGES = ['soulstruct @ git+https://github.com/Grimrukh/soulstruct',
            'soulstruct-havok @ git+https://github.com/Grimrukh/soulstruct-havok']


def ensure_tool() -> None:
    if TOOL_PY.exists():
        return
    print('Setting up the navmesh reader (Python 3.13 + Soulstruct, about 50 MB)...')
    subprocess.run(['uv', 'python', 'install', '3.13'], check=False)
    found = subprocess.run(['uv', 'python', 'find', '3.13'], capture_output=True, text=True).stdout.strip()
    if not found or not Path(found).exists():  # uv's version link can fail; use the newest 3.13 folder
        base = Path.home() / 'AppData' / 'Roaming' / 'uv' / 'python'
        cands = sorted(base.glob('cpython-3.13.*-windows-x86_64-none/python.exe'))
        if not cands:
            raise SystemExit('Python 3.13 could not be installed with uv')
        found = str(cands[-1])
    subprocess.run([found, '-m', 'venv', str(VENV)], check=True)
    subprocess.run([str(TOOL_PY), '-m', 'pip', 'install', '-q', *PACKAGES], check=True)


def blocks_with_navmesh(archives: ar.Archives) -> list[tuple[str, str]]:
    out = []
    for path in ar.names('.nvmhktbnd.dcx'):
        if archives.has(path):
            out.append((path.split('/')[-1].split('.')[0], path))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args()
    ar.fetch_support_files()
    ensure_tool()
    print('Reading the archive index (about 35 s the first time)...')
    archives = ar.Archives()
    todo = [(b, p) for b, p in blocks_with_navmesh(archives)
            if (not args.only or b in args.only) and not (OUT / f'{b}.npz').exists()]
    print(f'{len(todo)} map blocks to convert')
    if not todo:
        return
    tmp = OUT / f'tmp-{os.getpid()}'  # per run: two runs at once must not share (or delete) it
    tmp.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    # Extract every block's pieces first (fast), then convert in parallel (the slow part).
    lists = [[] for _ in range(max(1, args.workers))]
    for k, (block, path) in enumerate(todo):
        folder = tmp / block
        folder.mkdir(exist_ok=True)
        for name, data in ar.bnd4_files(ar.undcx(archives.read(path))).items():
            short = ntpath.basename(name)
            if short.startswith('n') and short.endswith('.hkx'):
                (folder / short).write_bytes(data)
        lists[k % len(lists)].append(f'{folder}\t{OUT / (block + ".npz")}')
    print(f'extracted in {time.perf_counter() - t0:.0f} s; converting with {len(lists)} workers...')
    procs = []
    for i, lines in enumerate(lists):
        if not lines:
            continue
        lf = tmp / f'list{i}.txt'
        lf.write_text('\n'.join(lines), encoding='utf-8')
        procs.append(subprocess.Popen([str(TOOL_PY), str(CONVERT), str(lf)], stdout=subprocess.PIPE, text=True))
    done = errors = 0
    for pr in procs:
        for line in pr.stdout:
            done += 1
            errors += 'ERROR' in line
            if 'ERROR' in line or done % 25 == 0:
                print(f'[{done}/{len(todo)}, {time.perf_counter() - t0:.0f} s] {line.strip()}', flush=True)
        pr.wait()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'done: {done} blocks, {errors} errors, {time.perf_counter() - t0:.0f} s')
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
