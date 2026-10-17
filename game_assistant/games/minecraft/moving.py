"""Carrying out a Minecraft route (pathing.Move list), frame by frame, the way Baritone's PathExecutor and its
movements do (github.com/cabaletta/baritone, studied for this; our own code).

Each frame: where the feet are (their block); which move that's in (each move has the blocks the body may be in
while doing it: on from a later one, back to an earlier one, off the route: plan again); then the move itself:
first what's in its way is broken (each block looked at and mined till it's gone, as the world says now), then
its own keys. Each move says when it's done (the feet in its block, on the ground), or that it can't be (fallen
below where it started: plan again).

Unlike Baritone, which sets the game's keys and the view from inside it each tick, this presses keys and turns
the mouse from outside, a frame or two late: so a jump is pressed that much ahead of the edge (by the speed), and
a landing with a fall past it is made crouched (the game's sneak never goes over an edge).

Game coordinates inside (cells); the snapshot's (X mirrored) at the edges.
"""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field

from ...core.autowalk import Intent, _wrap, _yaw, aim_at, cam_yaw
from . import blocks as B

JUMP_LEAD = 0.07          # seconds between pressing a key and the game acting on it (a frame, the game's tick)
EDGE = 0.5                # the block's edge from its middle...
JUMP_AT = 0.6             # ...and where a jump over a gap goes up (the feet hold on to 0.8: the body's half width)
JUMP_AT_LONG = 0.7        # a walking jump over two: as late as it can (Baritone's)
FACING = 35.0             # degrees: walk on only when facing within this of the way
TURN_GAIN = 0.5           # of the heading's error turned a frame (walking)
TURN_MAX = 18.0           # degrees a frame at most
AIM_GAIN = 0.45           # ...aiming at a block
LANDED = 0.094            # the feet this near the block's floor: on it (lily pads, Baritone's)
MOVE_TIMEOUT_TICKS = 100  # a move taking this many ticks past what it was reckoned to: given up (plan again)
OFF_ROUTE = 2.0           # blocks from every block of the route: off it, a while...
OFF_ROUTE_S = 3.0         # ...this long: plan again
FAR_OFF = 3.0             # this far: plan again at once
SETTLE_S = 0.3            # a landing crouched at most this long
NO_PLACE_S = 3.0          # seconds trying to put a block down before it's given up
CENTRE_PILLAR = 0.17      # a pillar: this near the block's middle before jumping (Baritone's)
PROBE_EVERY = 0.3         # seconds between asking the bridge about a block (an older one, with no box each frame)
STEEP = 50.0              # degrees up or down: walking with the view further tilted than this, it's brought...
WALK_PITCH = 15.0         # ...to this, a little down (after a pillar it stayed straight down, 2026-10-16)
TURN_FIRST = 12.0         # degrees: on a jump's block, turned crouched in place till facing within this
EYE = 1.62                # the eyes over the feet (blocks)
AIR_LOW = 120             # the air (300 full, 15 seconds' worth) below which, the head under water, it goes up
SWIM_UP = 6               # blocks: in water this far below the move's own blocks, it swims up to them first
RUN_FROM = -0.3           # blocks before the jump's block's middle a run at it starts again (backed off to)
RUN_TRIES = 2             # runs at a jump not running at its point before it's given up
JOIN_NEAR = 1.5           # blocks across from a move's start, not in its blocks: walked onto it first, not given up
BOB = 0.6                # blocks: the feet this little over a water block's top (jump held, bobbing): in it


def _cell(p) -> tuple:
    return (math.floor(p[0]), math.floor(p[1] + 0.1251), math.floor(p[2]))


def _centre(c) -> tuple:
    return (c[0] + 0.5, float(c[1]), c[2] + 0.5)


def _mirror(v) -> tuple:
    return (-float(v[0]), float(v[1]), float(v[2]))


class Live:
    """What's in the blocks round the player now: the bridge's per-frame box (its 'near'), else the planning
    grid (as it was), what was asked of the bridge since (ask: an older bridge, with no box) over it."""

    def __init__(self, near: dict | None, grid: B.Grid | None, ask=None):
        self.g = None
        if near and near.get('data'):
            self.g = B.Grid(tuple(int(v) for v in near['min']), tuple(int(v) for v in near['size']),
                            base64.b64decode(near['data']))
        self.plan = grid
        self.ask = ask
        self.ask_only: set = set()   # (asked only of these: the move's own blocks to break and to put down)

    def at(self, c) -> int:
        k = self.g.at(*c) if self.g is not None else B.UNKNOWN
        if k == B.UNKNOWN and self.ask is not None and tuple(c) in self.ask_only:
            k = self.ask(tuple(c))
        if k == B.UNKNOWN and self.plan is not None:
            k = self.plan.at(*c)
        return k

    def through(self, c) -> bool:
        return self.at(c) in B.PASS

    def on(self, c) -> bool:
        return self.at(c) in B.FLOOR


def valid(m) -> set:
    """The blocks the feet may be in while doing move m (Baritone's valid positions)."""
    s, d = m.src, m.dest
    dx, dz = d[0] - s[0], d[2] - s[2]
    if m.kind == 'traverse' or m.kind in ('pillar', 'downward'):
        return {s, d}
    if m.kind == 'ascend':
        prior = (s[0] - dx, s[1] + 1, s[2] - dz)
        return {s, (s[0], s[1] + 1, s[2]), d, prior, (prior[0], prior[1] + 1, prior[2])}
    if m.kind == 'descend':
        return {s, (d[0], d[1] + 1, d[2]), d}
    if m.kind == 'fall':
        return {s} | {(d[0], y, d[2]) for y in range(d[1], s[1] + 1)}
    if m.kind == 'diagonal':
        return {s, d, (s[0], s[1], d[2]), (d[0], s[1], s[2])}
    if m.kind == 'parkour':
        n = max(abs(dx), abs(dz))
        ux, uz = (dx // n, dz // n) if n else (0, 0)
        out = set()
        for i in range(n + 1):
            for y in (0, 1):
                out.add((s[0] + ux * i, s[1] + y, s[2] + uz * i))
        return out | {d}
    if m.kind == 'leap':                             # (along the line, at the start's height and the landing's)
        n = max(1, int(math.hypot(dx, dz) / 0.25))
        out = {s, d}
        for i in range(n + 1):
            x, z = s[0] + 0.5 + dx * i / n, s[2] + 0.5 + dz * i / n
            for y in {s[1], s[1] + 1, d[1], d[1] + 1}:
                out.add((math.floor(x), y, math.floor(z)))
        return out
    return {s, d}


@dataclass
class _State:
    phase: str = 'prep'          # 'prep' (breaking what's in the way), 'run', 'settle'
    t0: float = 0.0
    ticks: float = 0.0
    breaking: tuple | None = None
    aim: tuple | None = None     # where to aim at the block being broken (snapshot coordinates)
    aim_eye: tuple | None = None
    placing_since: float | None = None
    last: float = -1e9           # the last tap
    extra: dict = field(default_factory=dict)


class Mover:
    """Does a route of pathing moves (AutoWalk hands its frames here when the route has moves).

    update(snap, dt) -> (Intent, status): status None (under way), 'done' (the last move done), 'replan' (can't
    go on as planned: plan again from here; why in .why)."""

    def __init__(self, aimer=None, probe=None):
        self.aimer = aimer           # (block key, eye: snapshot coordinates) -> (a point on it in sight, blocker)
        self.probe = probe
        self.moves: list = []
        self.i = 0
        self.grid = None
        self.st = _State()
        self.t = 0.0
        self.why = ''
        self.dug: list = []          # blocks broken (the app's memory)
        self.failed: list = []       # ('place', {'into': cell}) a block that couldn't be put down
        self.events: list = []
        self._off_since = None
        self._prev = None            # (t, game position)
        self._probes: dict = {}      # cell -> (when asked, kind): an older bridge
        self.state = 'idle'

    def start(self, moves, grid=None) -> None:
        cur = self.current if self.moves else None
        self.moves, self.i, self.grid = list(moves), 0, grid
        self._off_since = None
        # the move under way in the new route too (planned again, or on from the end of a part): on with it as it
        # is, not started over (Baritone splices the next part on; started over, a block half mined was let go
        # and begun again, every 1.6 s down a tunnel, 2026-10-16)
        if cur is not None:
            for j, m in enumerate(self.moves):
                if (m.kind, m.src, m.dest) == (cur.kind, cur.src, cur.dest):
                    self.i, self._fresh = j, False
                    return
        self.st = _State(t0=self.t)
        self._fresh = True           # (where along it the feet are: looked for all along it, the first frame; a
                                     # long way planned further while walked is handed over whole again)

    @property
    def current(self):
        return self.moves[self.i] if 0 <= self.i < len(self.moves) else None

    # ---- each frame ----

    def update(self, snap, dt: float):
        self.t += dt
        if not self.moves or self.i >= len(self.moves):
            return Intent(), 'done'
        ex = snap.extra or {}
        p = _mirror(snap.player.pos)
        vel = self._velocity(p, ex, dt)
        live = Live(ex.get('near'), self.grid, None if ex.get('near') else self._asked)
        feet = _cell(p)
        below = (feet[0], feet[1] - 1, feet[2])
        if p[1] - feet[1] < BOB and live.at(below) == B.WATER and live.at(feet) != B.WATER:
            feet = below                                   # (bobbing at the top, jump held: swimming in its top)
        self._place_on_route(feet)
        m = self.current
        live.ask_only = set(m.breaks) | ({m.place} if m.place else set())
        # under water below the move's own blocks (sunk, or the way planned from the top over the feet): up to them
        # first, not off the route (each new route began at the top, out of reach below it: planned again every
        # half second, sinking, 2026-10-17)
        if live.at(feet) == B.WATER and m.kind not in ('fall', 'downward') and feet not in valid(m) \
                and any(c[0] == feet[0] and c[2] == feet[2] and 0 < c[1] - feet[1] <= SWIM_UP for c in valid(m)):
            self.state = 'swimming'
            self._off_since = None
            return Intent(frozenset({'jump'})), None
        # off the route: every block of it far from the feet
        d = min(math.dist(p, _centre(c)) for mv in self.moves[max(0, self.i - 2):self.i + 6] for c in valid(mv))
        if d > FAR_OFF and m.kind != 'fall':
            return self._replan('off the route')
        if d > OFF_ROUTE and m.kind != 'fall':
            self._off_since = self._off_since or self.t
            if self.t - self._off_since > OFF_ROUTE_S:
                return self._replan('off the route a while')
        else:
            self._off_since = None
        self.st.ticks += dt * 20.0
        if self.st.ticks > m.cost + MOVE_TIMEOUT_TICKS + (40.0 if m.place else 0.0):
            return self._replan(f'{m.kind} took too long')
        intent, status = self._move(m, snap, p, vel, feet, live, ex)
        intent = self._water(intent, m, snap, p, feet, live, ex)
        if status == 'success':
            self.i += 1
            self.st = _State(t0=self.t)
            if self.i >= len(self.moves):
                return Intent(frozenset(intent.keys & {'sneak'})), 'done'
            return self.update(snap, 0.0) if dt else (intent, None)
        if status == 'fail':
            return self._replan(self.why or f'{m.kind} failed')
        return intent, None

    def _asked(self, c) -> int:
        """An older bridge (no blocks round the feet each frame): what's in c, asked (every PROBE_EVERY s at most),
        as AIR or SOLID; UNKNOWN when it can't be asked (the planning grid then)."""
        if self.probe is None:
            return B.UNKNOWN
        got = self._probes.get(c)
        if got is None or self.t - got[0] > PROBE_EVERY:
            try:
                solid = self.probe(c)
            except Exception:
                solid = None
            got = self._probes[c] = (self.t, B.UNKNOWN if solid is None else B.SOLID if solid else B.AIR)
        return got[1]

    def _replan(self, why: str):
        self.why = why
        self.events.append('replan')
        self.moves, self.i = [], 0
        return Intent(), 'replan'

    def _velocity(self, p, ex, dt):
        """Blocks a tick, across (the game's, else from the last frame)."""
        if ex.get('vx') is not None and ex.get('vz') is not None:
            v = (float(ex['vx']), float(ex.get('vy') or 0.0), float(ex['vz']))   # (the game's, as here)
        elif self._prev is not None and self.t - self._prev[0] > 1e-3:
            q, span = self._prev[1], self.t - self._prev[0]
            v = ((p[0] - q[0]) / span / 20.0, float(ex.get('vy') or 0.0), (p[2] - q[2]) / span / 20.0)
        else:
            v = (0.0, float(ex.get('vy') or 0.0), 0.0)
        self._prev = (self.t, p)
        return v

    def _place_on_route(self, feet) -> None:
        """Which move the feet are in: an earlier one (knocked back), or a later one (skipped ahead)."""
        if getattr(self, '_fresh', False):
            self._fresh = False
            for j, mv in enumerate(self.moves):
                if feet in valid(mv) and feet != mv.dest:
                    self.i = j
                    return
        m = self.current
        if feet in valid(m):
            return
        for j in range(0, self.i):
            if feet in valid(self.moves[j]):
                self.i, self.st = j, _State(t0=self.t)
                return
        for j in range(self.i + 2, min(len(self.moves), self.i + 8)):
            if feet in valid(self.moves[j]) and feet != self.moves[j].dest:
                self.i, self.st = j, _State(t0=self.t)
                return

    # ---- keys and the view ----

    def _toward(self, snap, target, keys=(), sprint=False, pitch_to=None) -> Intent:
        """Walk at target (game coordinates): turn to face it, forward once facing it."""
        cam = snap.camera
        t = _mirror(target)
        p = snap.player.pos
        err = _wrap(_yaw((t[0] - p[0], 0.0, t[2] - p[2])) - cam_yaw(cam))
        turn = max(-TURN_MAX, min(TURN_MAX, err * TURN_GAIN))
        ks = set(keys)
        if abs(err) < FACING:
            ks.add('forward')
            if sprint:
                ks.add('sprint')
        tilt = 0.0
        cur = math.degrees(math.asin(max(-1.0, min(1.0, -cam.forward[1]))))
        if pitch_to is None and abs(cur) > STEEP:
            pitch_to = WALK_PITCH                          # (looking far up or down: the view brought level)
        if pitch_to is not None:
            tilt = (pitch_to - cur) * AIM_GAIN
        return Intent(frozenset(ks), turn, (), tilt)

    def _look(self, snap, point_game, keys=(), taps=()) -> tuple[Intent, float]:
        turn, tilt, err = aim_at(snap.camera, _mirror(point_game), AIM_GAIN)
        return Intent(frozenset(keys), turn, tuple(taps), tilt), err

    # ---- breaking what's in the way (every move, first) ----

    def _prep(self, m, snap, p, live, ex):
        """None when nothing of m's is in the way; else the Intent breaking the first one there."""
        for c in m.breaks:
            if live.through(c):
                continue
            st = self.st
            if st.breaking != c:
                st.breaking, st.aim, st.aim_eye = c, None, None
            eye = snap.camera.pos
            if self.aimer is not None and (st.aim_eye is None or math.dist(eye, st.aim_eye) > 0.25):
                st.aim_eye = eye
                try:
                    st.aim, _blocker = self.aimer(c, eye)
                except Exception:
                    st.aim = None
            target = st.aim if st.aim is not None else _mirror((c[0] + 0.5, c[1] + 0.5, c[2] + 0.5))
            turn, tilt, err = aim_at(snap.camera, target, AIM_GAIN)
            keys, taps = set(), []
            aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(c)
            if aimed or err < 3.0:
                tool = ex.get('tool_slot', -1)
                if aimed and tool is not None and tool >= 0 and ex.get('slot') != tool and self.t - st.last > 0.3:
                    taps.append(f'slot{tool + 1}')
                    st.last = self.t
                # (what the crosshair's on, if not it, is in front of it: broken too; never the floor stood on)
                on = ex.get('aim_block')
                if aimed or (on is not None and tuple(on)[1] >= math.floor(p[1] + 0.01)):
                    keys.add('attack')
            self.state = 'breaking'
            return Intent(frozenset(keys), turn, tuple(taps), tilt)
        if self.st.breaking is not None:
            self.dug.append(self.st.breaking)
            self.st.breaking = None
        return None

    # ---- putting a block down against another ----

    def _place(self, m, snap, p, live, ex, keys=()):
        """The Intent putting m's block down (against m.against, crouched), or None when it's there."""
        c = m.place
        if live.on(c):
            self.st.placing_since = None
            return None
        st = self.st
        st.placing_since = st.placing_since or self.t
        if self.t - st.placing_since > NO_PLACE_S:
            self.failed.append(('place', {'into': tuple(c)}))
            self.events.append('work_failed:place')
            self.why = 'a block would not go down'
            return 'fail'
        slot = ex.get('block_slot', -1)
        if slot is None or slot < 0:
            self.why = 'no blocks to build with'
            return 'fail'
        taps = []
        if ex.get('slot') != slot and self.t - st.last > 0.3:
            taps.append(f'slot{slot + 1}')
            st.last = self.t
        a = m.against
        face = (a[0] + 0.5 + (c[0] - a[0]) * 0.5, a[1] + 0.5 + (c[1] - a[1]) * 0.5, a[2] + 0.5 + (c[2] - a[2]) * 0.5)
        intent, err = self._look(snap, face, set(keys) | {'sneak'})
        aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(a)
        if aimed and ex.get('aim_face') == _face_name(c, a) and ex.get('slot') == slot and self.t - st.last > 0.25:
            taps.append('interact')
            st.last = self.t
        self.state = 'building'
        return Intent(intent.keys, intent.turn, tuple(taps), intent.pitch)

    # ---- the moves ----

    def _move(self, m, snap, p, vel, feet, live, ex):
        prep = self._prep(m, snap, p, live, ex) if m.kind != 'diagonal' else None
        if prep is not None:
            if m.kind in ('ascend', 'pillar', 'parkour', 'leap') and p[1] < m.src[1] - 0.5:
                self.why = 'fell'
                return Intent(), 'fail'
            return prep, None
        k = m.kind
        self.state = 'walking' if k in ('traverse', 'diagonal', 'descend') else 'parkour' if k == 'leap' else k
        return getattr(self, '_' + k)(m, snap, p, vel, feet, live, ex)

    def _water(self, intent, m, snap, p, feet, live, ex) -> Intent:
        """In water, whatever the move (Baritone's way: one rule for all of them, not each its own): never at a run
        (a run in water is the dive-swim, the way the view looks: a little down, it went under); jump held, which
        keeps the head up, unless the way goes down into the water; the view level while swimming on; and with
        the head under and the air running low, straight up first. (Swimming across a lake, only the straight
        moves held jump: the diagonal ones sank, off the route, again and again, the air nearly gone,
        2026-10-17.)"""
        eye = (feet[0], math.floor(p[1] + EYE), feet[2])
        under = live.at(eye) == B.WATER
        if not under and live.at(feet) != B.WATER:
            return intent
        keys = set(intent.keys) - {'sprint'}
        air = ex.get('air')
        down = m.dest[1] < feet[1] and live.at(m.dest) == B.WATER
        tilt = intent.pitch
        if under and air is not None and air < AIR_LOW:
            keys = {'jump'}                                # (up for air before all else)
        elif not down:
            keys.add('jump')
        if 'forward' in keys and 'attack' not in keys and not intent.taps:
            cur = math.degrees(math.asin(max(-1.0, min(1.0, -snap.camera.forward[1]))))
            tilt = (0.0 - cur) * AIM_GAIN
        return Intent(frozenset(keys), intent.turn, intent.taps, tilt)

    def _sprint_ok(self, m, live) -> bool:
        """Sprinting on (Baritone's PathExecutor: a clear walk, not into anything bad, hunger allowing)."""
        nxt = self.moves[self.i + 1] if self.i + 1 < len(self.moves) else None
        if nxt is not None and nxt.kind in ('parkour', 'leap') and not nxt.get('sprint'):
            return False                                   # (a walking jump next: not at a run, it'd overshoot)
        return nxt is not None and nxt.kind in ('traverse', 'diagonal', 'parkour', 'leap', 'ascend') and not m.breaks \
            and m.place is None

    def _traverse(self, m, snap, p, vel, feet, live, ex):
        d = m.dest
        if m.place is not None and not live.on(m.place):
            return self._bridge(m, snap, p, live, ex)
        if feet == d:
            return Intent(), 'success'
        if feet[1] < d[1] and live.at(m.src) != B.CLIMB:
            return Intent(frozenset({'jump', 'forward'})), None          # (lower than it: up)
        food = ex.get('food')
        sprint = self._sprint_ok(m, live) and (food is None or food > 6)
        keys = ('jump',) if live.at(feet) == B.WATER else ()
        return self._toward(snap, _centre(d), keys, sprint), None

    def _bridge(self, m, snap, p, live, ex):
        d, s = m.dest, m.src
        if m.get('sneak'):                               # backwards: crouched out over the edge, then turn round
            out = (p[0] - (s[0] + 0.5)) * (d[0] - s[0]) + (p[2] - (s[2] + 0.5)) * (d[2] - s[2])
            ex_ = self.st.extra
            if out < 0.72 and not ex_.get('edge'):
                walk = self._toward(snap, _centre(d), ('sneak',))
                moved = math.hypot(p[0] - ex_.get('x', p[0]), p[2] - ex_.get('z', p[2]))
                ex_.update(x=p[0], z=p[2])
                # (at the edge when it's out over it, or pushing forward at it and going nowhere: the crouch holds
                # it there; not while still turning to it)
                if 'forward' in walk.keys and moved < 0.002:
                    ex_['stall'] = ex_.get('stall') or self.t
                else:
                    ex_['stall'] = None
                if ex_['stall'] is None or self.t - ex_['stall'] < 0.25:
                    return walk, None
            ex_['edge'] = True
        else:
            ax = math.hypot(p[0] - (d[0] + 0.5), p[2] - (d[2] + 0.5))
            if ax > 1.35:                                 # (near enough the gap to reach the block it's put on)
                return self._toward(snap, _centre(d), ('sneak',)), None
        got = self._place(m, snap, p, live, ex)
        if got == 'fail':
            return Intent(), 'fail'
        return (got or Intent(frozenset({'sneak'}))), None

    def _ascend(self, m, snap, p, vel, feet, live, ex):
        s, d = m.src, m.dest
        if p[1] < s[1] - 0.5:
            self.why = 'fell'
            return Intent(), 'fail'
        dx, dz = d[0] - s[0], d[2] - s[2]
        if feet == d or feet == (d[0] + dx, d[1], d[2] + dz):
            return Intent(), 'success'
        if m.place is not None and not live.on(m.place):
            got = self._place(m, snap, p, live, ex)
            if got == 'fail':
                return Intent(), 'fail'
            if self.t - (self.st.placing_since or self.t) > 0.5:      # (in the way of it: back a little)
                got = Intent(got.keys | {'back'}, got.turn, got.taps, got.pitch)
            return got, None
        intent = self._toward(snap, _centre(d))
        if live.at(m.place or (d[0], s[1], d[2])) == B.LOW or (live.at(s) == B.LOW):
            return intent, None                           # (onto or off a slab: a step, not a jump)
        if feet == (s[0], s[1] + 1, s[2]):
            return intent, None                           # (up already)
        flat = abs(dx) * abs(d[0] + 0.5 - p[0]) + abs(dz) * abs(d[2] + 0.5 - p[2])
        side = abs(dz) * abs(d[0] + 0.5 - p[0]) + abs(dx) * abs(d[2] + 0.5 - p[2])
        lateral = abs(dx * vel[2]) + abs(dz * vel[0])
        if lateral > 0.1:
            return intent, None
        bonk_clear = all(live.through((s[0] + ox, s[1] + 2, s[2] + oz))
                         for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if bonk_clear or (flat <= 1.2 and side <= 0.2):
            return Intent(intent.keys | {'jump'}, intent.turn, intent.taps, intent.pitch), None
        return intent, None

    def _descend(self, m, snap, p, vel, feet, live, ex):
        s, d = m.src, m.dest
        dx, dz = d[0] - s[0], d[2] - s[2]
        fake = (d[0] + dx, d[1], d[2] + dz)
        if (feet == d or feet == fake) and (live.at(d) == B.WATER or p[1] - d[1] < 0.5):
            return Intent(), 'success'
        into = (d[0] + dx, d[1], d[2] + dz)
        safe = any(live.at((into[0], into[1] + y, into[2])) == B.DANGER for y in range(3))
        if safe:                                           # (something bad past it: no further than the block)
            target = (s[0] + 0.5 + (d[0] - s[0]) * 0.83, d[1], s[2] + 0.5 + (d[2] - s[2]) * 0.83)
            return self._toward(snap, target), None
        st = self.st
        st.extra['n'] = st.extra.get('n', 0) + 1
        from_start = math.hypot(p[0] - (s[0] + 0.5), p[2] - (s[2] + 0.5))
        if feet == d and math.hypot(p[0] - d[0] - 0.5, p[2] - d[2] - 0.5) <= 0.25:
            return Intent(), None
        if st.extra['n'] < 20 and from_start < 1.25:
            return self._toward(snap, _centre(fake)), None   # (off the edge at once: past the block)
        return self._toward(snap, _centre(d)), None

    def _fall(self, m, snap, p, vel, feet, live, ex):
        d = m.dest
        on_ground = ex.get('on_ground')
        if feet == d and (p[1] - d[1] < LANDED or live.at(d) == B.WATER) and on_ground is not False:
            return Intent(), 'success'
        cx, cz = d[0] + 0.5, d[2] + 0.5
        if on_ground is not False and feet == m.src:     # walking off the edge: not at a run (it carries on)
            if ex.get('sprinting'):
                return Intent(frozenset(), self._toward(snap, (cx, d[1], cz)).turn), None
            return self._toward(snap, (cx, d[1], cz)), None
        # in the air: steered at the landing's middle by the keys alone (no time to turn: Baritone turns at once
        # from inside the game; a fall onto a ledge one wide was carried past it, turning round), crouched for the
        # touch-down near it (never slid off its edge)
        keys = set()
        if p[1] - d[1] < 0.8 and vel[1] < 0:
            keys.add('sneak')
        ahead = (p[0] + vel[0] * 4, p[2] + vel[2] * 4)
        if abs(ahead[0] - cx) > 0.1 or abs(ahead[1] - cz) > 0.1:
            return self._push(snap, (cx - ahead[0], cz - ahead[1]), keys), None
        return Intent(frozenset(keys)), None

    def _push(self, snap, way, keys=()) -> Intent:
        """The keys moving the way (game x, z) from where the view faces, without turning (Baritone's
        moveTowardsWithoutRotation)."""
        from ...core.autowalk import DIRECTIONS
        want = _yaw((-way[0], 0.0, way[1]))               # (the snapshot's X is the game's mirrored)
        err = _wrap(want - cam_yaw(snap.camera))
        best = min(DIRECTIONS, key=lambda dk: abs(_wrap(err - dk[0])))[1]
        return Intent(frozenset(set(best) | set(keys)))

    def _diagonal(self, m, snap, p, vel, feet, live, ex):
        d = m.dest
        if feet == d:
            return Intent(), 'success'
        if feet not in valid(m) and not (ex.get('on_ground') is False
                                         and (feet[0], feet[1] - 1, feet[2]) in valid(m)):
            # (a hop over its blocks isn't off it: out of water onto the bank, jump still held a frame, 2026-10-17)
            s = m.src
            if math.hypot(p[0] - s[0] - 0.5, p[2] - s[2] - 0.5) < JOIN_NEAR and abs(p[1] - s[1]) < 1.0:
                # (right by its start, not in it (on a bed beside it): onto it first, not given up: given up at
                # once, the same way was planned 10 times a second for 15 s, 2026-10-17)
                return self._toward(snap, _centre(s)), None
            self.why = 'off the diagonal'
            return Intent(), 'fail'
        s = m.src
        sides = [(s[0], s[1], d[2]), (s[0], s[1] + 1, d[2]), (d[0], s[1], s[2]), (d[0], s[1] + 1, s[2])]
        food = ex.get('food')
        sprint = all(live.through(c) for c in sides) and (food is None or food > 6) and self._sprint_ok(m, live)
        return self._toward(snap, _centre(d), (), sprint), None

    def _pillar(self, m, snap, p, vel, feet, live, ex):
        s, d = m.src, m.dest
        if p[1] < s[1] - 0.5:
            self.why = 'fell'
            return Intent(), 'fail'
        if m.get('ladder') or m.get('swim'):
            if feet == d:
                return Intent(), 'success'
            intent = self._toward(snap, _centre(d), ('jump',))
            return intent, None
        there = live.on(s)
        if feet == d and there:
            return Intent(), 'success'
        slot = ex.get('block_slot', -1)
        if slot is None or slot < 0:
            self.why = 'no blocks to build with'
            return Intent(), 'fail'
        st = self.st
        taps = []
        if ex.get('slot') != slot and self.t - st.last > 0.3:
            taps.append(f'slot{slot + 1}')
            st.last = self.t
        off = math.hypot(p[0] - (d[0] + 0.5), p[2] - (d[2] + 0.5))
        keys = {'sneak'}
        # looking straight down at the block stood on (where the new one goes on top of)
        turn, tilt, err = aim_at(snap.camera, _mirror((s[0] + 0.5, s[1] - 0.5, s[2] + 0.5)), AIM_GAIN)
        if off > CENTRE_PILLAR:
            move = self._toward(snap, _centre(d), keys)
            return Intent(move.keys, move.turn, tuple(taps), tilt), None
        flat_motion = math.hypot(vel[0], vel[2])
        if flat_motion < 0.05 and p[1] < d[1]:
            keys.add('jump')
        if not there:
            under = (s[0], s[1] - 1, s[2])
            aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) in (tuple(under), tuple(s))
            if not live.through(s) and live.at(s) != B.AIR:   # (grass and the like in the way: broken)
                keys.discard('jump')
                keys.add('attack')
            elif aimed and p[1] > d[1] + 0.05 and self.t - st.last > 0.15:
                taps.append('interact')
                st.last = self.t
        self.state = 'pillar'
        return Intent(frozenset(keys), turn * 0.0, tuple(taps), tilt), None

    def _downward(self, m, snap, p, vel, feet, live, ex):
        d = m.dest
        if feet == d:
            return Intent(), 'success'
        if feet not in valid(m):
            self.why = 'off the way down'
            return Intent(), 'fail'
        if m.get('ladder'):
            return Intent(), None                          # (on a ladder, nothing held: down it)
        st = self.st
        st.extra['n'] = st.extra.get('n', 0) + 1
        if st.extra['n'] < 10 and math.hypot(p[0] - d[0] - 0.5, p[2] - d[2] - 0.5) < 0.2:
            return Intent(), None
        return self._toward(snap, _centre(d)), None

    def _parkour(self, m, snap, p, vel, feet, live, ex):
        s, d = m.src, m.dest
        if p[1] < s[1] - 0.5 and feet != d:
            self.why = 'the jump fell short'
            self.events.append('work_failed:parkour')
            return Intent(), 'fail'
        dist = m.get('dist') or 2
        ascend = bool(m.get('ascend'))
        n = max(abs(d[0] - s[0]), abs(d[2] - s[2]))
        ux, uz = (d[0] - s[0]) / n, (d[2] - s[2]) / n
        sprint = dist >= 4 or ascend
        on_ground = ex.get('on_ground')
        st = self.st
        landing = (d[0] + 0.5, d[1], d[2] + 0.5)
        past = (p[0] - landing[0]) * ux + (p[2] - landing[2]) * uz
        brake = self._brake_after(m, live)
        if st.phase == 'settle':                           # down on it: crouched till the speed's gone
            speed = math.hypot(vel[0], vel[2])
            if self.t - st.extra['down'] > SETTLE_S or speed < 0.02:
                return Intent(frozenset({'sneak'})), 'success'
            return Intent(frozenset({'sneak', 'back'} if past > 0.15 else {'sneak'})), None
        if feet == d and (p[1] - d[1] < LANDED or on_ground):
            if brake:
                st.phase, st.extra['down'] = 'settle', self.t
                return Intent(frozenset({'sneak', 'back'} if past > 0.15 else {'sneak'})), None
            return Intent(), 'success'
        intent = self._toward(snap, landing, (), sprint)
        out = (p[0] - (s[0] + 0.5)) * ux + (p[2] - (s[2] + 0.5)) * uz   # past the start block's middle
        flying = on_ground is False or p[1] - s[1] > 0.0001
        if not flying and self.t - st.extra.get('jump', -1e9) < 0.25:
            # (held till the feet are off the ground: a press shorter than the game's tick can fall between two
            # and never be seen)
            return Intent(intent.keys | {'jump'}, intent.turn), None
        if flying or feet != s:
            if flying and past > -0.3 and brake:          # over the landing: crouched for the touch-down
                return Intent(frozenset({'sneak'}), intent.turn), None
            if not flying and feet == (s[0] + round(ux), s[1], s[2] + round(uz)):
                held = self._at_a_run(m, sprint, out, -1e9, 0.0, ex, intent)   # (not running: no jump either)
                if held is not None:
                    return held
                st.extra['jump'] = self.t
                return Intent(intent.keys | {'jump'}, intent.turn), None    # (over the gap already: up)
            if flying:
                return intent, None
            # somewhere else (behind the start): back to it first
            return self._toward(snap, _centre(s)), None
        if abs(_facing_err(snap, landing)) >= TURN_FIRST:
            # (not facing it yet: turned in place, crouched, never walked on sideways off the block)
            return Intent(frozenset({'sneak'}), intent.turn), None
        if not sprint and ex.get('sprinting'):
            # a walking jump, still at a run from before: forward let go a frame (in the game a run lasts till
            # forward's let go: Baritone turns it off from inside; at a run, the jump overshot a one-block rim
            # by three)
            return Intent(frozenset(), intent.turn), None
        along = vel[0] * ux + vel[2] * uz                 # blocks a tick, the way of the jump
        speed = abs(along) * 20.0
        at = JUMP_AT_LONG if dist == 3 and not ascend else JUMP_AT
        # (a short walking jump goes up where it comes down in the landing's middle, not at the edge: from there a
        # walking jump over one flies 2.4, and overshot a one-block landing into the next gap; never later than the
        # edge. A jump at a run is at the edge, as Baritone's: one over three, gone up 0.3 before the middle on a
        # speed read too high just after a step up, came down 0.3 short and fell 5, 2026-10-16)
        if not sprint:
            fly = flight(max(0.0, along), bool(ex.get('sprinting')), 1.0 if ascend else 0.0)
            at = min(at, max(-0.2, n - fly - 0.15))
        held = self._at_a_run(m, sprint, out, at, speed, ex, intent)
        if held is not None:
            return held
        if out + speed * JUMP_LEAD >= at and abs(_facing_err(snap, landing)) < 12.0:
            st.extra['jump'] = self.t
            return Intent(intent.keys | {'jump'}, intent.turn), None
        return intent, None

    def _leap(self, m, snap, p, vel, feet, live, ex):
        """A jump any way (pathing._leaps), as a straight one but along the line from the start's middle to the
        landing's: up at the latest just past the start block's edge that way, early enough to come down in the
        landing's middle; down on it crouched (a landing that way is often one block: a crafting table's top)."""
        s, d = m.src, m.dest
        if p[1] < min(s[1], d[1]) - 0.5 and feet != d:
            self.why = 'the jump fell short'
            self.events.append('work_failed:leap')
            return Intent(), 'fail'
        dx, dz = d[0] - s[0], d[2] - s[2]
        dist = math.hypot(dx, dz)
        ux, uz = dx / dist, dz / dist
        edge = 0.5 / max(abs(ux), abs(uz))               # the start block's edge, along the line
        sprint = bool(m.get('sprint'))
        on_ground = ex.get('on_ground')
        st = self.st
        landing = (d[0] + 0.5, d[1], d[2] + 0.5)
        past = (p[0] - landing[0]) * ux + (p[2] - landing[2]) * uz
        if st.phase == 'settle':                           # down on it: crouched till the speed's gone
            speed = math.hypot(vel[0], vel[2])
            if feet != d and on_ground is not False:      # (slid off it onto what's past: back on, crouched)
                return self._toward(snap, landing, ('sneak',)), None
            if feet == d and (self.t - st.extra['down'] > SETTLE_S or speed < 0.02):
                return Intent(frozenset({'sneak'})), 'success'
            return Intent(frozenset({'sneak', 'back'} if past > 0.15 else {'sneak'})), None
        if feet == d and (p[1] - d[1] < LANDED or on_ground):
            st.phase, st.extra['down'] = 'settle', self.t
            return Intent(frozenset({'sneak', 'back'} if past > 0.15 else {'sneak'})), None
        intent = self._toward(snap, landing, (), sprint)
        out = (p[0] - (s[0] + 0.5)) * ux + (p[2] - (s[2] + 0.5)) * uz   # past the start block's middle
        flying = on_ground is False or abs(p[1] - s[1]) > 0.0001
        if not flying and self.t - st.extra.get('jump', -1e9) < 0.25:
            return Intent(intent.keys | {'jump'}, intent.turn), None    # (held till the feet are off the ground)
        if flying:
            if past > -0.3 and p[1] - d[1] < 0.8:        # over the landing, coming down: crouched for it
                return Intent(frozenset({'sneak'}), intent.turn), None
            return intent, None
        if feet != s:
            first = (s[0] + round(ux * 0.75), s[1], s[2] + round(uz * 0.75))
            if feet == first:                              # (out over the gap already: up)
                held = self._at_a_run(m, sprint, out, -1e9, 0.0, ex, intent)
                if held is not None:
                    return held
                st.extra['jump'] = self.t
                return Intent(intent.keys | {'jump'}, intent.turn), None
            return self._toward(snap, _centre(s)), None    # (somewhere else: back to the start first)
        facing = abs(_facing_err(snap, landing))
        if facing >= TURN_FIRST:
            # (not facing it yet: turned in place, crouched: walked on while turning, a jump off a pillar's top
            # went off its side, 2026-10-16)
            return Intent(frozenset({'sneak'}), intent.turn), None
        if not sprint and ex.get('sprinting'):
            return Intent(frozenset(), intent.turn), None  # (a walking jump: the run from before let go a frame)
        along = vel[0] * ux + vel[2] * uz                 # blocks a tick, the way of the jump
        speed = abs(along) * 20.0
        fly = flight(max(0.0, along), bool(ex.get('sprinting')) or sprint, float(m.get('rise') or 0))
        # (at a run, down a little short of the middle: the speed carries it on after; and never far from the
        # edge: the speed read can be too high, as _parkour's)
        at = min(edge + 0.1, max(edge - 0.3 if sprint else -0.2, dist - fly - (0.3 if sprint else 0.1)))
        if out < at and max(out, -0.2) + fly > dist + edge - 0.3:
            return Intent(frozenset(), intent.turn), None  # (too fast to come down on it: eased off)
        held = self._at_a_run(m, sprint, out, at, speed, ex, intent)
        if held is not None:
            return held
        if out + speed * JUMP_LEAD >= at and facing < 10.0:
            st.extra['jump'] = self.t
            return Intent(intent.keys | {'jump'}, intent.turn), None
        return intent, None

    def _at_a_run(self, m, sprint, out, at, speed, ex, intent):
        """A jump at a run goes up only running (the game says it's sprinting): the run's boost is most of its
        reach (from a standstill at a run a jump flies 3 blocks, walking at a walk's start 1.1). Not running at the
        jump's point: stopped at the edge, crouched, backed off into the block and run at again; after RUN_TRIES,
        given up (not planned again a while). None: on as it is. (Jumps over three went up from a step down or a
        turn not running, fell short, the last into a ravine 35 down: dead, 2026-10-17.)"""
        st = self.st
        if not sprint or ex.get('sprinting') is None:
            return None
        if st.extra.get('backing'):
            if out > RUN_FROM:
                return Intent(frozenset({'back', 'sneak'}), intent.turn), None
            st.extra['backing'] = False                    # (back far enough: run at it again, or given up there,
            if st.extra.get('runs', 0) >= RUN_TRIES:       # away from the edge: given up at it, the run carried
                self.why = 'not running at the jump'       # on over it, planning again, keys let go)
                self.failed.append(('jump', {'from': m.src, 'to': m.dest}))
                return Intent(frozenset({'sneak'})), 'fail'
        if ex.get('sprinting') or out + speed * JUMP_LEAD < at:
            return None
        st.extra['runs'] = st.extra.get('runs', 0) + 1
        st.extra['backing'] = True
        return Intent(frozenset({'sneak'}), intent.turn), None

    def _brake_after(self, m, live) -> bool:
        """Whether a jump's landing is made crouched: a fall that hurts just past it, the way not going on
        straight that way (our addition: Baritone, inside the game, turns at once)."""
        s, d = m.src, m.dest
        n = max(abs(d[0] - s[0]), abs(d[2] - s[2]))
        ux, uz = (d[0] - s[0]) // n, (d[2] - s[2]) // n
        nxt = self.moves[self.i + 1] if self.i + 1 < len(self.moves) else None
        if nxt is not None and nxt.kind != 'pillar' \
                and (_sign(nxt.dest[0] - nxt.src[0]), _sign(nxt.dest[2] - nxt.src[2])) == (ux, uz):
            return False                                   # (on straight that way: the speed's wanted)
        beyond = (d[0] + ux, d[1], d[2] + uz)
        if not live.through(beyond):
            return False                                   # (a wall past it: stopped by it)
        return not live.on((beyond[0], beyond[1] - 1, beyond[2]))   # (ground past it: the slide does no harm)


def flight(v: float, sprint: bool, rise: float = 0.0) -> float:
    """How far a jump carries across (blocks), from v blocks a tick the way it goes, forward held, till the feet
    come back down to rise (the game's: 0.42 up, gravity 0.08 kept 0.98; across kept 0.91, pushed 0.02 a tick,
    0.026 at a run, which also adds 0.2 at the jump)."""
    x, y, vy = 0.0, 0.0, 0.42
    v += 0.2 if sprint else 0.0
    push = 0.026 if sprint else 0.02
    for _ in range(40):
        v += push
        x += v
        y += vy
        vy = (vy - 0.08) * 0.98
        v *= 0.91
        if vy < 0 and y <= rise:
            return x
    return x


def _sign(v) -> int:
    return (v > 0) - (v < 0)


def _facing_err(snap, point_game) -> float:
    t = _mirror(point_game)
    p = snap.player.pos
    return _wrap(_yaw((t[0] - p[0], 0.0, t[2] - p[2])) - cam_yaw(snap.camera))


_FACE_NAMES = {(0, 1, 0): 'up', (0, -1, 0): 'down', (1, 0, 0): 'east', (-1, 0, 0): 'west', (0, 0, 1): 'south',
               (0, 0, -1): 'north'}


def _face_name(into, against) -> str:
    return _FACE_NAMES.get((into[0] - against[0], into[1] - against[1], into[2] - against[2]), '')
