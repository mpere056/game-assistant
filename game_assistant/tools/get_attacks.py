"""Extract every character's attack timings from Elden Ring's animation files into
.local/eldenring/attacks.json (read-only from the installed game; nothing goes into the repository).

    .venv\\Scripts\\python -m game_assistant.tools.get_attacks

For each character model (chr/c####.anibnd.dcx, the .tae inside), every animation with attack
events: {"c4300": {"3010": [[0.43, 0.67], [1.77, 1.97]], ...}, ...} (seconds into the animation when
an attack's hitbox is active; see games/eldenring/tae.py). Animations that launch a projectile or
spell are kept too, under "bullets". Also, for reach (combat): each attack animation's behaviour judge ids
("judges": the AtkParam rows its hitboxes come from, through the character's BehaviorParam variation) and
each model's attachment points' reach from its centre ("dummy_reach", games/eldenring/flver.py: from
chr/c####.chrbnd.dcx; variants like c4311 have their own model). A few minutes.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from ..games.eldenring import archive as ar
from ..games.eldenring import flver, tae

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.local' / 'eldenring' / 'attacks.json'


def main() -> int:
    ar.fetch_support_files()
    t0 = time.perf_counter()
    archives = ar.Archives()
    files = [p for p in ar.names('/chr/c') if p.endswith('.anibnd.dcx') and archives.has(p)]
    by_model: dict[str, list[str]] = {}
    for p in files:
        m = re.match(r'/chr/(c\d{4})', p)
        if m:
            by_model.setdefault(m.group(1), []).append(p)
    out, n_attacks, errors = {}, 0, 0
    for model, paths in sorted(by_model.items()):
        # The .tae is usually in the main file; the _div parts hold the animations themselves.
        paths.sort(key=lambda p: '_div' in p)
        attacks, bullets, judges = {}, {}, {}
        for path in paths:
            try:
                bnd = ar.bnd4_files(ar.undcx(archives.read(path)))
            except Exception:
                errors += 1
                continue
            taes = [d for n, d in bnd.items() if n.lower().endswith('.tae')]
            for data in taes:
                try:
                    _fid, anims = tae.parse(data)
                    imps = tae.imports(data)
                    for aid, src in imps.items():   # events borrowed from another animation (5003008 <- 1003008)
                        for _ in range(8):           # (an import of an import: follow it)
                            if anims.get(src) or src not in imps:
                                break
                            src = imps[src]
                        if anims.get(src):
                            anims[aid] = anims[src]
                except Exception:
                    errors += 1
                    continue
                for aid, events in anims.items():
                    w = sorted({(round(s, 3), round(e, 3)) for s, e in tae.attack_windows(events)})
                    if w:  # (several attack events often share one window: kept once)
                        attacks[str(aid)] = [list(x) for x in w]
                        j = sorted({e.params[2] for e in events if e.type == tae.ATTACK and len(e.params) > 2
                                    and e.params[2] > 0})
                        if j:
                            judges[str(aid)] = j
                    b = sorted(round(e.start, 3) for e in events if e.type == tae.BULLET)
                    if b:
                        bullets[str(aid)] = b
            if taes:
                break
        if attacks or bullets:
            out[model] = {'attacks': attacks, 'bullets': bullets, 'judges': judges}
            n_attacks += len(attacks)
    # Models (variants too) and their attachment points' reach.
    n_models = 0
    for p in [x for x in ar.names('/chr/c') if x.endswith('.chrbnd.dcx') and archives.has(x)]:
        m = re.match(r'/chr/(c\d{4})\.chrbnd', p)
        if not m:
            continue
        try:
            bnd = ar.bnd4_files(ar.undcx(archives.read(p)))
            data = next((d for n, d in bnd.items() if n.lower().endswith('.flver')), None)
            reach = flver.dummy_reach(data) if data else {}
        except Exception:
            errors += 1
            continue
        if reach:
            out.setdefault(m.group(1), {})['dummy_reach'] = {str(k): v for k, v in reach.items()}
            n_models += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding='utf-8')
    print(f'{len(out)} character models, {n_attacks} attack animations, {n_models} with reach, {errors} unreadable files, '
          f'{time.perf_counter() - t0:.0f} s -> {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
