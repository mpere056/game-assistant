"""Live adapter tests: the gate before the assistant may use a game (docs/ADAPTER.md).

    Check-Adapter.bat [game]            (or: .venv\\Scripts\\python -m game_assistant.tools.adapter_check [game])
    Check-Adapter.bat [game] --look     live readout of what the look-at resolver sees, until Ctrl+C

game defaults to eldenring (see game_assistant/games/__init__.py). For Elden Ring, start it with
Attack on Elden Ring's Play-EldenRing.bat and load a character first. The script asks you to do a
few simple things in the game (walk, look up, aim at someone) and checks the numbers. Results go
to the screen and to runtime/adapter-check-<time>.txt.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ..games import adapter_for
from ..core import lookat
from ..core.interface import GameNotRunning, dist

ROOT = Path(__file__).resolve().parents[2]
WAIT = 20.0  # seconds to do each thing that is asked


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.results: list[tuple[str, str]] = []

    def say(self, text: str = '') -> None:
        print(text)
        self.lines.append(text)

    def result(self, name: str, outcome: str, detail: str) -> None:
        self.results.append((name, outcome))
        self.say(f'  {outcome:<4}  {name}: {detail}')

    def save(self) -> Path:
        out = ROOT / 'runtime' / f'adapter-check-{datetime.now():%Y%m%d-%H%M%S}.txt'
        out.parent.mkdir(exist_ok=True)
        out.write_text('\n'.join(self.lines) + '\n', 'utf-8')
        return out


def wait_for(adapter, cond, timeout=WAIT):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = adapter.snapshot()
        v = cond(s)
        if v:
            return s, v
        time.sleep(0.1)
    return adapter.snapshot(), None


def nearby(snap, metres=40.0):
    if not snap.player:
        return []
    return sorted((e for e in snap.entities if not e.dead and dist(e.pos, snap.player.pos) < metres),
                  key=lambda e: dist(e.pos, snap.player.pos))


def run_checks(a) -> int:
    r = Report()
    r.say(f'Adapter check for {a.game}, {datetime.now():%Y-%m-%d %H:%M}')

    # 1. Bridge and character
    try:
        live = a.is_live()
    except GameNotRunning as e:
        r.say(str(e))
        return 1
    if not live:
        r.result('bridge', 'FAIL', 'the bridge exists but Elden Ring is not ticking (paused, closed or crashed?)')
        r.save()
        return 1
    s = a.snapshot()
    if not s.in_world:
        r.say('  Waiting for a character in the world (load your save)...')
        s, ok = wait_for(a, lambda x: x.in_world, 120)
        if not ok:
            r.result('character', 'FAIL', 'no character in the world after 2 minutes')
            r.save()
            return 1
    r.result('bridge', 'PASS', f'live, frame {s.frame}, area {s.area}, {len(s.entities)} characters published')

    # 2. Player position
    start = s.player.pos
    r.say('\n>> Walk or run a few steps in any direction now.')
    s, moved = wait_for(a, lambda x: x.player and dist(x.player.pos, start) > 2.0 and dist(x.player.pos, start))
    r.result('player position', 'PASS' if moved else 'FAIL',
             f'moved {moved:.1f} m' if moved else f'position did not change by 2 m in {WAIT:.0f} s')

    # 3. Camera direction
    r.say('\n>> Tilt the camera to look up at the sky.')
    s, up = wait_for(a, lambda x: x.camera and x.camera.forward[1] > 0.5 and x.camera.forward[1])
    r.result('camera looks up', 'PASS' if up else 'FAIL',
             f'forward.y = {up:.2f}' if up else 'the camera direction never pointed upward')
    r.say('\n>> Now tilt the camera down toward the ground in front of you.')
    s, down = wait_for(a, lambda x: x.camera and x.camera.forward[1] < -0.2 and x.camera.forward[1])
    r.result('camera looks down', 'PASS' if down else 'FAIL',
             f'forward.y = {down:.2f}' if down else 'the camera direction never pointed downward')

    # 4. Raycasts
    try:
        p = s.player.pos
        t0 = time.perf_counter()
        h = a.raycast([((p[0], p[1] + 2.0, p[2]), (p[0], p[1] - 10.0, p[2]))])[0]
        ms = (time.perf_counter() - t0) * 1000
        ok = h.hit and abs(h.pos[1] - p[1]) < 1.0
        r.result('raycast down hits the ground', 'PASS' if ok else 'FAIL',
                 f'ground {h.pos[1] - p[1]:+.2f} m from the feet, normal y {h.normal[1]:.2f}, answered in {ms:.0f} ms'
                 if h.hit else 'the ray hit nothing below the character')
        cam = s.camera
        hs = a.raycast([(cam.pos, tuple(cam.pos[i] + cam.forward[i] * 100 for i in range(3)))])
        r.result('raycast along the camera', 'PASS' if hs[0].hit else 'FAIL',
                 f'hit the {lookat.surface_kind(hs[0].normal)} at {hs[0].distance:.1f} m' if hs[0].hit
                 else 'no hit within 100 m while looking down')
    except TimeoutError as e:
        r.result('raycasts', 'FAIL', str(e))

    # 5. Characters, screen side (handedness) and look-at
    near = nearby(a.snapshot())
    if not near:
        r.result('characters', 'SKIP', 'nobody within 40 m: stand near an enemy or NPC and run the check again')
    else:
        e = near[0]
        r.result('characters', 'PASS', f'{len(near)} within 40 m; nearest: type {e.type_id}, model {e.model}, '
                 f'{"hostile" if e.hostile else "not hostile"}, {dist(e.pos, a.snapshot().player.pos):.1f} m, '
                 f'hp {e.health:.0f}/{e.max_health:.0f}')
        r.say(f'\n>> Turn the camera so that character ({e.model}) is on the RIGHT half of the screen, not centred.')

        def on_right(x):
            for c in x.entities:
                if c.id == e.id and x.camera:
                    sx, sy, depth = lookat.project(x.camera, c.center)
                    if depth > 0 and 0.3 < sx < 1.0 and abs(sy) < 1.0:
                        return sx
            return None
        s2, sx = wait_for(a, on_right)
        r.result('left and right on screen', 'PASS' if sx else 'FAIL',
                 f'screen x = {sx:+.2f} (right is positive)' if sx else
                 'never seen on the right half: if it was there, left and right are swapped in the adapter')

        r.say(f'\n>> Now put the centre of the screen on that character (or any character) and hold still.')

        def aimed(x):
            res = lookat.resolve(x, a.raycast)
            return res if res.kind in ('entity', 'target') and res.best.off_deg == 0 else None
        s3, res = wait_for(a, aimed)
        if res:
            f = lookat.facts(res, a.knowledge)
            r.result('look-at resolver', 'PASS', f'{res.best.entity.model} (type {res.best.entity.type_id}) at '
                     f'{res.best.distance:.1f} m, confidence {res.confidence}')
            r.say('        facts for the model: ' + json.dumps(f))
        else:
            r.result('look-at resolver', 'FAIL', 'never resolved a character at the crosshair')

    passed = sum(1 for _, o in r.results if o == 'PASS')
    failed = [n for n, o in r.results if o == 'FAIL']
    skipped = [n for n, o in r.results if o == 'SKIP']
    r.say(f'\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped.'
          + (' Elden Ring is CONNECTED.' if not failed and not skipped else ''))
    r.say(f'Saved to runtime/{r.save().name}')
    return 1 if failed else 0


def run_look(a) -> int:
    print('Live look-at readout. Point the camera at things. Ctrl+C to stop.')
    last = None
    try:
        while True:
            s = a.snapshot()
            res = lookat.resolve(s, a.raycast)
            f = lookat.facts(res, a.knowledge)
            if res.kind in ('entity', 'target'):
                e = res.best.entity
                line = (f'{res.kind}: {e.model} type {e.type_id} {"hostile" if e.hostile else "friendly"} '
                        f'{res.best.distance:.0f} m {res.best.side}, confidence {res.confidence}')
            elif res.kind == 'surface':
                line = f'surface: {f["surface"]["kind"]} at {f["surface"]["distance_m"]} m'
            else:
                line = 'nothing (sky, or nothing within range)'
            if line != last:
                print(f'{datetime.now():%H:%M:%S}  {line}')
                last = line
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    names = [x for x in sys.argv[1:] if not x.startswith('-')]
    try:
        adapter = adapter_for(names[0] if names else 'eldenring')
        sys.exit(run_look(adapter) if '--look' in sys.argv else run_checks(adapter))
    except KeyError as e:
        sys.exit(e.args[0])
    except GameNotRunning as e:
        sys.exit(e.args[0])
