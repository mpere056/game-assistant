"""Auto-walk: the character follows a route by itself (phase 4b, the F10 "go to..." commands).

Game-independent: every frame it reads the snapshot and says what to press (`Intent`): which movement
keys to hold (forward, back, left, right, sprint), how far to turn the camera (degrees, by mouse), and
one-off taps (interact for a ladder, jump). The game adapter turns that into real input.

Steering: the movement keys are the one of 8 directions (relative to the camera) nearest the way to
go, so the character heads the right way at once; meanwhile the camera turns toward it, so after a
moment it is just "forward". It follows the route a few metres ahead of the player (pure pursuit).

It stops by itself when it arrives (ARRIVE metres across, ARRIVE_UP in height), or gives up when
stuck STUCK_GIVE_UP times in a minute (less than STUCK_MOVE metres in STUCK_SECONDS while walking: the
first time it backs off and sidesteps, then it asks for a new route that avoids the spot),
and the caller stops it the moment the player presses a movement key or says "stop". In a menu,
on a loading screen, while dead, or while the game isn't the active window it pauses (no keys held).
Enemies near the way ahead are reported ("Careful!") but it keeps walking (the user's choice).

Ladders, lifts, jumps (from the route's actions): a ladder gets an interact tap at its foot (or top)
and then forward (up) or back (down) until the other end's height; a lift: stand at its spot until
the height changes to the other end's (or LIFT_WAIT seconds); a jump: a jump tap at the edge while
moving. First versions: not yet tried in the game.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .interface import Snapshot, Vec3

ARRIVE = 3.0          # metres across from the goal...
ARRIVE_UP = 3.0       # ...and in height
LOOKAHEAD = 3.5       # metres along the route ahead of the player to steer toward
SPRINT_STRAIGHT = 20.0  # run when the route ahead stays this long within SPRINT_BEND degrees
SPRINT_BEND = 25.0
SPRINT_KEEP = 10.0    # ...and keep running until the straight ahead is shorter than this
SPRINT_MIN_S = 1.5    # running lasts at least this long: in Elden Ring a short tap of the run key
                      # is a backstep or a roll, so it must never flicker on and off
TURN_RATE = 240.0     # degrees per second, at most, for the camera
TURN_GAIN = 6.0       # per second: turn this many times the angle error (capped by TURN_RATE)
STUCK_SECONDS = 2.0
STUCK_MOVE = 0.5
STUCK_GIVE_UP = 3     # this many stucks within STUCK_WINDOW seconds: give up (re-planning takes seconds,
STUCK_WINDOW = 60.0   # so "two within 10 s" never happened at a cave mouth: it said "something's in
                      # the way" again and again)
UNSTICK_S = 0.8       # first, back off and sidestep this long before trying again
CORNER_REACH = 1.0    # don't steer past the next corner of the route until this close to it (narrow
                      # passages: steering 3.5 m ahead cut corners into the rock)
ENEMY_NEAR = 8.0      # metres from the player or from the next ENEMY_AHEAD metres of the route
ENEMY_AHEAD = 30.0
ENEMY_REPEAT = 30.0   # seconds before the same enemy is mentioned again
ACTION_AT = 1.5       # metres from a ladder/lift/jump start to act on it
# Walking to a character (and attacking it): steered at its live position every frame once within
# CHASE_DIRECT metres; further away along the route, which the caller re-plans as it moves.
CHASE_DIRECT = 20.0
LOCK_RANGE = 12.0     # attack: lock on (the camera then follows the target by itself)
ATTACK_REACH = 1.8    # attack: swing when this close to the target's body (metres from its edge)
ATTACK_FACING = 30.0  # ...and the camera points within this many degrees of it
AFTER_HIT_S = 0.6     # attack: wait this long after the swing, then hand back to the player
REACH_CHARACTER = 2.5 # just walking to a character: stop this close to it
LIFT_WAIT = 25.0
LADDER_TIMEOUT = 30.0

DIRECTIONS = [  # (angle from the camera's forward, clockwise, degrees) -> keys
    (0, ('forward',)), (45, ('forward', 'right')), (90, ('right',)), (135, ('back', 'right')),
    (180, ('back',)), (-135, ('back', 'left')), (-90, ('left',)), (-45, ('forward', 'left')),
]


@dataclass
class Intent:
    keys: frozenset = frozenset()       # held this frame: forward, back, left, right, sprint
    turn: float = 0.0                   # degrees to turn the camera now (+ to the right)
    taps: tuple = ()                    # pressed once now: 'interact', 'jump'


@dataclass
class _Action:
    what: str
    start: Vec3
    end: Vec3
    done: bool = False


def _yaw(v) -> float:
    """Heading of a vector, degrees clockwise from +z (north) toward +x (east)."""
    return math.degrees(math.atan2(v[0], v[2]))


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _flat(a: Vec3, b: Vec3) -> float:
    return math.hypot(b[0] - a[0], b[2] - a[2])


@dataclass
class AutoWalk:
    sprint: bool = True                 # run on long straight stretches (setting auto_walk_sprint)
    active: bool = False
    route: list = field(default_factory=list)
    goal: Vec3 | None = None
    name: str | None = None
    events: list = field(default_factory=list)   # 'arrived', 'stuck', 'gave_up', 'enemy:<id>', 'paused:<why>',
                                                 # 'target_gone', 'attacked'
    state: str = 'idle'                 # idle, walking, paused, ladder, lift
    t: float = 0.0

    def __post_init__(self):
        self._progress = 0              # route segment the player is on
        self._stuck_since = None
        self._stuck_pos = None
        self._stucks: list[float] = []  # times of recent stucks
        self._unstick_until = -1.0
        self._unstick_side = 'left'
        self._enemy_said: dict[int, float] = {}
        self._actions: list[_Action] = []
        self._special = None            # (action, started at)
        self._paused_why = None
        self._sprint_since = None       # when running started (None: walking)
        self.target_id: int | None = None  # walking to (or attacking) this character
        self.attack = False
        self._locked = False
        self._swung_at: float | None = None

    # ---- commands ----

    def start(self, route: list[Vec3], goal: Vec3, name: str | None = None, actions=(),
              target_id: int | None = None, attack: bool = False) -> None:
        self.active, self.goal, self.name, self.state = True, goal, name, 'walking'
        self.target_id, self.attack, self._locked, self._swung_at = target_id, attack, False, None
        self.set_route(route, actions)
        self._stuck_since = self._stuck_pos = None
        self._stucks = []
        self._unstick_until = -1.0
        self._special = None

    def set_route(self, route: list[Vec3], actions=()) -> None:
        """A new or extended route (re-planned, or planned further while walking)."""
        self.route = list(route)
        self._progress = 0
        self._actions = [_Action(a[0], a[1], a[2] if len(a) > 2 else a[1]) for a in actions]

    def stop(self, why: str = '') -> None:
        if self.active and why:
            self.events.append(f'stopped:{why}')
        self.active, self.state = False, 'idle'
        self._special = None

    # ---- every frame ----

    def update(self, snap: Snapshot, dt: float) -> Intent:
        self.t += dt
        if not self.active:
            return Intent()
        why = self._pause_reason(snap)
        if why:
            if self._paused_why != why:
                self.events.append(f'paused:{why}')
            self._paused_why = why
            self.state = 'paused'
            self._stuck_since = None
            return Intent()
        self._paused_why = None
        p = snap.player.pos
        if self.target_id is not None:
            chase = self._chase(snap, p, dt)
            if chase is not None:
                return chase
        elif self.goal and _flat(p, self.goal) < ARRIVE and abs(self.goal[1] - p[1]) < ARRIVE_UP:
            self.events.append('arrived')
            self.stop()
            return Intent()
        if not self.route:
            return Intent()
        self._watch_enemies(snap, p)
        special = self._special_action(snap, p)
        if special is not None:
            return special
        if self.t < self._unstick_until:  # backing off and sidestepping after getting stuck
            self.state = 'unsticking'
            return Intent(frozenset({'back', self._unstick_side}))
        target, straight = self._pursuit(p)
        cam = snap.camera
        cam_yaw = _yaw(cam.forward)
        want = _yaw((target[0] - p[0], 0.0, target[2] - p[2]))
        err = _wrap(want - cam_yaw)
        keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1])
        if self._run(straight, err):
            keys.add('sprint')
        turn = max(-TURN_RATE * dt, min(TURN_RATE * dt, err * TURN_GAIN * dt))
        self.state = 'walking'
        if self._check_stuck(p):
            return Intent()
        return Intent(frozenset(keys), turn)

    # ---- parts ----

    def _chase(self, snap: Snapshot, p: Vec3, dt: float) -> Intent | None:
        """Walking to or attacking a character: live position every frame. None: follow the route."""
        e = next((x for x in snap.entities if x.id == self.target_id), None)
        if self._swung_at is not None:  # the hit is out: a moment for it to land, then over to the player
            if self.t - self._swung_at >= AFTER_HIT_S:
                self.events.append('attacked')
                self.stop()
            return Intent()
        if e is None or e.dead:
            self.events.append('target_gone')
            self.stop()
            return Intent()
        self.goal = e.pos
        gap = _flat(p, e.pos) - e.radius
        cam_yaw = _yaw(snap.camera.forward)
        want = _yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2]))
        err = _wrap(want - cam_yaw)
        taps = []
        if self.attack and not self._locked and gap < LOCK_RANGE:
            self._locked = True
            taps.append('lock')
        if self.attack and gap <= ATTACK_REACH and abs(err) <= ATTACK_FACING:
            self._swung_at = self.t
            return Intent(taps=tuple(taps) + ('attack',))
        if not self.attack and gap <= REACH_CHARACTER:
            self.events.append('arrived')
            self.stop()
            return Intent()
        if _flat(p, e.pos) > CHASE_DIRECT and self.route:
            return None if not taps else Intent(taps=tuple(taps))
        # Close: straight at it. Locked on, the camera follows by itself: don't fight it with the mouse.
        keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1])
        turn = 0.0 if self._locked else max(-TURN_RATE * dt, min(TURN_RATE * dt, err * TURN_GAIN * dt))
        self.state = 'chasing'
        if self._check_stuck(p):
            return Intent()
        return Intent(frozenset(keys), turn, tuple(taps))

    def _pause_reason(self, snap: Snapshot) -> str | None:
        if not snap.in_world or snap.player is None or snap.camera is None:
            return 'loading'
        if snap.menu:
            return 'menu'
        if snap.player.state == 'dead':
            return 'dead'
        if snap.player.state == 'busy':
            return 'busy'
        if snap.screen is not None and not snap.screen.focused:
            return 'not_focused'
        return None

    def _pursuit(self, p: Vec3) -> tuple[Vec3, float]:
        """The point LOOKAHEAD metres along the route past the player's nearest point, and how far the
        route ahead runs straight from there (for running)."""
        pts = self.route
        best, best_d, best_t = self._progress, math.inf, 0.0
        for i in range(self._progress, min(len(pts) - 1, self._progress + 8)):
            a, b = pts[i], pts[i + 1]
            ab = (b[0] - a[0], b[2] - a[2])
            L2 = ab[0] ** 2 + ab[1] ** 2
            t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[2] - a[2]) * ab[1]) / L2))
            q = (a[0] + ab[0] * t, a[2] + ab[1] * t)
            d = math.hypot(p[0] - q[0], p[2] - q[1])
            if d < best_d:
                best, best_d, best_t = i, d, t
        self._progress = best
        if len(pts) == 1:
            return pts[0], 0.0
        # Walk LOOKAHEAD metres along the route from the nearest point.
        i, t = best, best_t
        left = LOOKAHEAD
        while i < len(pts) - 1:
            a, b = pts[i], pts[i + 1]
            seg = _flat(a, b)
            rest = seg * (1 - t)
            if rest >= left and seg > 1e-6:
                k = t + left / seg
                target = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)
                break
            left -= rest
            i, t = i + 1, 0.0
        else:
            target = pts[-1]
        # Don't cut the next corner: until within CORNER_REACH of it, steer at it, not past it.
        nxt = pts[min(best + 1, len(pts) - 1)]
        if best + 1 < len(pts) - 1 and _flat(p, nxt) > CORNER_REACH and _flat(p, nxt) < _flat(p, target) + LOOKAHEAD:
            a = pts[best]
            turn = abs(_wrap(_yaw((pts[best + 2][0] - nxt[0], 0, pts[best + 2][2] - nxt[2])) -
                             _yaw((nxt[0] - a[0], 0, nxt[2] - a[2]))))
            if turn > 20.0:
                target = nxt
        # Straight stretch ahead: until the route bends more than SPRINT_BEND.
        straight, j = 0.0, best
        if j < len(pts) - 1:
            base = _yaw((pts[j + 1][0] - pts[j][0], 0, pts[j + 1][2] - pts[j][2]))
            while j < len(pts) - 1:
                seg_yaw = _yaw((pts[j + 1][0] - pts[j][0], 0, pts[j + 1][2] - pts[j][2]))
                if abs(_wrap(seg_yaw - base)) > SPRINT_BEND:
                    break
                straight += _flat(pts[j], pts[j + 1]) * (1 - (best_t if j == best else 0))
                j += 1
        return target, straight

    def _run(self, straight: float, err: float) -> bool:
        """Run or walk, with hysteresis and a minimum time (no short taps of the run key)."""
        if not self.sprint:
            return False
        if self._sprint_since is None:
            if straight >= SPRINT_STRAIGHT and abs(err) < 20.0:
                self._sprint_since = self.t
        elif self.t - self._sprint_since >= SPRINT_MIN_S and (straight < SPRINT_KEEP or abs(err) > 35.0):
            self._sprint_since = None
        return self._sprint_since is not None

    def _check_stuck(self, p: Vec3) -> bool:
        """True when the walk just gave up."""
        if self._stuck_since is None:
            self._stuck_since, self._stuck_pos = self.t, p
            return False
        if _flat(p, self._stuck_pos) > STUCK_MOVE:
            self._stuck_since, self._stuck_pos = self.t, p
            return False
        if self.t - self._stuck_since < STUCK_SECONDS:
            return False
        self._stuck_since, self._stuck_pos = self.t, p
        self._stucks = [s for s in self._stucks if self.t - s < STUCK_WINDOW] + [self.t]
        if len(self._stucks) >= STUCK_GIVE_UP:
            self.events.append('gave_up')
            self.stop()
            return True
        if len(self._stucks) == 1:  # first: back off and sidestep, then try the same way again
            self._unstick_until = self.t + UNSTICK_S
            self._unstick_side = 'left' if self._unstick_side == 'right' else 'right'
            self.events.append('unstick')
            return False
        self.events.append('stuck')  # again: the caller avoids what's here and plans a new route
        return False

    def _watch_enemies(self, snap: Snapshot, p: Vec3) -> None:
        ahead = self.route[self._progress:self._progress + 12]
        for e in snap.entities:
            if not e.hostile or e.dead or e.id == self.target_id:  # not the one we're going for
                continue
            near = _flat(e.pos, p) < ENEMY_NEAR or any(
                _flat(e.pos, q) < ENEMY_NEAR and _flat(q, p) < ENEMY_AHEAD for q in ahead)
            if near and self.t - self._enemy_said.get(e.id, -1e9) > ENEMY_REPEAT:
                self._enemy_said[e.id] = self.t
                self.events.append(f'enemy:{e.id}')

    def _special_action(self, snap: Snapshot, p: Vec3) -> Intent | None:
        """Ladders, lifts, jumps: None when walking normally."""
        if self._special is not None:
            act, since = self._special
            if act.what == 'ladder':
                up = act.end[1] > act.start[1]
                if abs(p[1] - act.end[1]) < 1.0 or self.t - since > LADDER_TIMEOUT:
                    act.done, self._special = True, None
                    return None
                self.state = 'ladder'
                return Intent(frozenset({'forward' if up else 'back'}))
            if act.what == 'lift':
                if abs(p[1] - act.end[1]) < 1.5 or self.t - since > LIFT_WAIT:
                    act.done, self._special = True, None
                    return None
                self.state = 'lift'
                return Intent()
        for act in self._actions:
            if act.done or _flat(p, act.start) > ACTION_AT or abs(p[1] - act.start[1]) > 3.0:
                continue
            if act.what == 'ladder':
                self._special = (act, self.t)
                self._stuck_since = None
                return Intent(taps=('interact',))
            if act.what == 'lift':
                self._special = (act, self.t)
                self._stuck_since = None
                return Intent()
            if act.what == 'jump':
                act.done = True
                return Intent(frozenset({'forward'}), taps=('jump',))
        return None
