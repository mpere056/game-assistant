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
UNSTICK_S = 0.35      # stuck: back off this long...
# Stuck with nothing a ray can see ahead: most likely something breakable (barrels, crates, pots: they block the
# character, no ray sees them). Swing at it (BREAK_SWINGS, BREAK_EVERY apart, walking on), then go on; at most
# BREAK_TRIES times in one place before stepping round it instead. (The user: "maybe it should try hitting them,
# because maybe it's likely they're breakable" - the walk is mostly in places it has never been.)
BREAK_SWINGS = 2
BREAK_EVERY = 0.7
BREAK_TRIES = 2
BREAK_PLACE = 2.0      # metres: tries within this of one another are the same place
AROUND_S = 1.2        # ...then go diagonally forward to one side this long (sides in turn): round a stack of
                      # barrels or crates the rays can't see (breakable objects: 2026-10-12 at Gatefront)
CORNER_REACH = 1.0    # don't steer past the next corner of the route until this close to it (narrow
                      # passages: steering 3.5 m ahead cut corners into the rock)
ACTION_AT = 1.5       # metres from a ladder/lift/jump start to act on it
WORK_AT = 0.45        # blocks from where a block is broken or placed to start on it (Minecraft)
STEP_AT = 0.4         # precise walking: this near a cell's middle is on it...
STEP_LOOK = 6         # ...looking this many cells ahead for the one the player is on
STEP_OFF = 1.4        # this far from the line to the next cell: off the route, re-plan
STEP_FACING = 25.0    # walk on only when facing the next cell within this (else turn first: no drifting into a
                      # corner)
STEP_TURN_RATE = 540.0
STEP_WAIT = 12.0      # seconds at the end of a part-planned route with no more of it coming: give up
STEP_TURN_GAIN = 14.0
WORK_TIMEOUT = 15.0   # seconds: give up on breaking one block...
LEAP_TIMEOUT = 4.0    # seconds for a jump over a gap, run-up and all
LEAP_AT = 0.32        # blocks past the middle of the block jumped from (its edge is at 0.5): jump
LEAP_FACING = 6.0     # degrees: facing the landing this squarely before running at it
LANDED = 0.6          # blocks from the landing block's middle: landed
PLACE_TIMEOUT = 5.0   # ...or placing one (it failed for 15 s, four times over, jumping on the spot, 2026-10-13)
# Walking to a character (and attacking it): steered at its live position every frame once within
# CHASE_DIRECT metres; further away along the route, which the caller re-plans as it moves.
CHASE_DIRECT = 20.0
# Straight at the character only when the navmesh agrees: its route there bending ROUTE_BENDS off the straight
# line means something the rays don't see is between (2026-10-12 at Gatefront: chasing a soldier 6-18 m off with
# a clear line at chest height, it ran into a low obstacle twice, stuck 4-7 s each time, while the route went
# round it). After getting stuck chasing, the route only for STUCK_ROUTE_S.
ROUTE_BENDS = 2.0
STUCK_ROUTE_S = 8.0
RESUME_GRACE = 2.0    # seconds after a pause (the game window out of focus) before "stuck" counts again
CHASE_LEVEL = 4.0     # ...and no more than this many metres above or below (an Eagle 35 m up a cliff, 18 m
                      # away on the map: straight at it walked into the cliff and got stuck)
LOCK_RANGE = 12.0     # attack: lock on (the camera then follows the target by itself)
ATTACK_REACH = 1.8    # attack: swing when this close to the target's body (metres from its edge)
ATTACK_FACING = 30.0  # ...and the camera points within this many degrees of it
AFTER_HIT_S = 0.6     # attack: wait this long after the swing, then hand back to the player
HOSTILE_STOP = 4.5    # ...a hostile one: out of its reach
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
    pitch: float = 0.0                  # degrees to tilt the camera now (+ down): aiming at a block


@dataclass
class _Action:
    what: str
    start: Vec3
    end: Vec3
    done: bool = False
    info: dict = field(default_factory=dict)   # break/place: the block ('key'), face, kind...
    t0: float | None = None                    # working on it since
    held: float | None = None                  # mining since (the crosshair on the block)
    seen: bool = False
    probed: bool = False                       # asked whether the block is still there
    aim: tuple | None = None                   # where to aim to break it (a point on it in sight)
    aim_eye: tuple | None = None               # ...as seen from here
    blocker: tuple | None = None               # a block in front of it, broken first
    phase: str = ''
    last: float = -1e9                         # the last press (slot, place, jump)


def _yaw(v) -> float:
    """Heading of a vector, degrees clockwise from +z (north) toward +x (east)."""
    return math.degrees(math.atan2(v[0], v[2]))


def aim_at(cam, point: Vec3, dt_k: float = 0.35) -> tuple[float, float, float]:
    """(turn, tilt, error in degrees) to bring the crosshair onto point: dt_k of the way this frame."""
    d = (point[0] - cam.pos[0], point[1] - cam.pos[1], point[2] - cam.pos[2])
    ey = _wrap(_yaw(d) - _yaw(cam.forward))
    want_pitch = math.degrees(math.atan2(-d[1], math.hypot(d[0], d[2])))
    cur_pitch = math.degrees(math.asin(max(-1.0, min(1.0, -cam.forward[1]))))
    ep = want_pitch - cur_pitch
    if math.hypot(d[0], d[2]) < 0.15:
        ey = 0.0                                  # straight down: any heading will do
    return ey * dt_k, ep * dt_k, math.hypot(ey, ep)


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _flat(a: Vec3, b: Vec3) -> float:
    return math.hypot(b[0] - a[0], b[2] - a[2])


@dataclass
class AutoWalk:
    sprint: bool = True                 # run on long straight stretches (setting auto_walk_sprint)
    breaks: bool = True                 # stuck with nothing seen ahead: swing at it (the adapter's walk_breaks)
    # Step by step (the adapter's walk_precise): the route is every cell of a grid (Minecraft's blocks); the walk
    # goes from cell to cell, never cutting a corner, jumping where the next cell is higher, and re-plans at
    # once when it can't go on or is off the route (Baritone's way). Else: smooth pursuit (open ground).
    precise: bool = False
    failed_work: list = field(default_factory=list)   # (what, info) of work that failed: the app tells the adapter
    # Whether a block is still there (the adapter's block_solid(key)), asked as work on it starts: a route planned
    # again while digging still had the blocks just dug, and each was aimed at for 1.5 s before being given up
    # (digging stopped after every block or two, 2026-10-13). None: not known (aim and see).
    probe: object = None
    aimer: object = None    # (block key, eye) -> (a point on it the crosshair reaches it at, or None; the block in
                            # the way when none does): set by the app (adapter.aim_point)
    active: bool = False
    route: list = field(default_factory=list)
    goal: Vec3 | None = None
    name: str | None = None
    events: list = field(default_factory=list)   # 'arrived', 'stuck', 'gave_up', 'paused:<why>',
                                                 # 'target_gone', 'attacked'
    state: str = 'idle'                 # idle, walking, paused, ladder, lift
    t: float = 0.0

    def __post_init__(self):
        self._progress = 0              # route segment the player is on
        self._stuck_since = None
        self._stuck_pos = None
        self._stucks: list[float] = []  # times of recent stucks
        self._unstick_until = -1.0
        self._around_until = -1.0
        self._break_swings: list = []        # times to swing (breaking something)
        self._breaks: list = []              # (where, how many times) breaking was tried
        self._unstick_side = 'left'
        self._route_only_until = -1.0
        self._actions: list[_Action] = []
        self._special = None            # (action, started at)
        self._paused_why = None
        self._sprint_since = None       # when running started (None: walking)
        self.target_id: int | None = None  # walking to (or attacking) this character
        self.attack = False
        self._locked = False
        self._swung_at: float | None = None

    # ---- commands ----

    clear_line: bool | None = None      # set by the app: a clear line to the target character (None: not known)
    ahead_blocked: bool | None = None   # set by the app: a ray sees something just ahead in the walking direction

    def start(self, route: list[Vec3], goal: Vec3, name: str | None = None, actions=(),
              target_id: int | None = None, attack: bool = False) -> None:
        self.active, self.goal, self.name, self.state = True, goal, name, 'walking'
        self.target_id, self.attack, self._locked, self._swung_at = target_id, attack, False, None
        self.set_route(route, actions)
        self._stuck_since = self._stuck_pos = None
        self._waiting_since = None
        self._stucks = []
        self._unstick_until = -1.0
        self._special = None

    def set_route(self, route: list[Vec3], actions=(), complete: bool = True) -> None:
        """A new or extended route (re-planned, or planned further while walking). complete: it ends at the
        goal (else at the end of what's planned so far)."""
        self.route = list(route)
        self.route_complete = complete
        self._progress = 0
        self._actions = [_Action(a[0], a[1], a[2] if len(a) > 2 else a[1], info=a[3] if len(a) > 3 else {})
                         for a in actions]

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
            self._resumed_at = None
            return Intent()
        if self._paused_why is not None:   # back in the game: it takes a moment to take keys again
            self._resumed_at = self.t
        self._paused_why = None
        p = snap.player.pos
        # (before both ways of walking: walking to a character said "let me smash it" and never swung,
        # 2026-10-12, because the chase returned first)
        if self._break_swings:            # breaking what blocks the way: swing at it, walking into it
            self.state = 'breaking'
            taps = ()
            if self.t >= self._break_swings[0]:
                self._break_swings.pop(0)
                taps = ('attack',)
            if not self._break_swings:
                self._stuck_since = None
            return Intent(frozenset({'forward'}), 0.0, taps)
        # (sneaking, where the game has it: Minecraft's sneak never walks off an edge; backing off blind
        # walked off a mountain ledge, 2026-10-13)
        if self.t < self._unstick_until:  # backing off after getting stuck...
            self.state = 'unsticking'
            return Intent(frozenset({'back', self._unstick_side, 'sneak'}))
        if self.t < self._around_until:   # ...then round it, diagonally forward to one side
            self.state = 'unsticking'
            return Intent(frozenset({'forward', self._unstick_side, 'sneak'}))
        if self.target_id is not None:
            chase = self._chase(snap, p, dt)
            if chase is not None:
                return chase
        elif self.goal and not self.precise and _flat(p, self.goal) < ARRIVE and abs(self.goal[1] - p[1]) < ARRIVE_UP:
            self.events.append('arrived')
            self.stop()
            return Intent()
        if not self.route:
            return Intent()
        special = self._special_action(snap, p)
        if special is not None:
            return special
        if not self.route:
            return Intent()                               # (work that failed just now cleared it: planned again;
                                                          # reading it anyway was an IndexError, 2026-10-13)
        if self.precise:
            return self._step(snap, p, dt)
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

    def _step(self, snap: Snapshot, p: Vec3, dt: float) -> Intent:
        """Precise walking: on to the next cell of the route."""
        pts = self.route
        i = self._progress
        if _flat(p, pts[i]) > 1.2 or abs(p[1] - pts[i][1]) > 1.5:
            # (a new or extended route starts counting from its first cell: find where the player is on it)
            near = min(range(len(pts)), key=lambda j: _flat(p, pts[j]) + abs(p[1] - pts[j][1]))
            if _flat(p, pts[near]) + abs(p[1] - pts[near][1]) < 2.0:
                i = near
        for j in range(min(len(pts) - 1, i + STEP_LOOK), i, -1):   # (on a cell further on: from there)
            if _flat(p, pts[j]) < STEP_AT and abs(p[1] - pts[j][1]) < 0.8:
                i = j
                break
        self._progress = i
        target = pts[min(i + 1, len(pts) - 1)]
        here = pts[i]
        # off the route: too far from the line between this cell and the next, or fallen below them
        a, b = here, target
        ab = (b[0] - a[0], b[2] - a[2])
        L2 = ab[0] ** 2 + ab[1] ** 2
        t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[2] - a[2]) * ab[1]) / L2))
        off = math.hypot(p[0] - (a[0] + ab[0] * t), p[2] - (a[2] + ab[1] * t))
        if off > STEP_OFF or p[1] < min(a[1], b[1]) - 1.5:
            # (a fresh route planned from where the player stood a moment ago: on from its cell nearest the
            # player, if one is near; else plan again from here)
            near = min(range(len(pts)), key=lambda j: _flat(p, pts[j]) + abs(p[1] - pts[j][1]))
            if near != i and _flat(p, pts[near]) < STEP_OFF and abs(p[1] - pts[near][1]) < 1.3:
                self._progress = near
                return self._step(snap, p, dt)
            self.state = 'off_route'
            self.events.append('replan')
            self.route, self._progress, self._stuck_since = [], 0, None
            return Intent()
        cam = snap.camera
        flat = _flat(p, target)
        rise = target[1] - p[1]
        keys = set()
        turn = 0.0
        if flat > 0.12:
            err = _wrap(_yaw((target[0] - p[0], 0.0, target[2] - p[2])) - _yaw(cam.forward))
            turn = max(-STEP_TURN_RATE * dt, min(STEP_TURN_RATE * dt, err * STEP_TURN_GAIN * dt))
            if abs(err) < STEP_FACING:
                keys.add('forward')
        if rise > 0.4 and flat < 1.4:
            keys.add('jump')                  # a step up (held: the game jumps as soon as it can), a ladder, water
        elif p[1] < here[1] - 0.15:
            keys.add('jump')                  # sinking below the route (in water): swim up, keep the head out
        if i >= len(pts) - 1 or (i == len(pts) - 2 and flat < STEP_AT and abs(rise) < 0.8):
            # at the end of the route: arrived (its last actions done: looking at the block), or wait for
            # the rest of it to be planned
            if any(not a.done and _flat(a.start, p) < 1.5 for a in self._actions):
                return Intent(frozenset(keys), turn)
            if self.route_complete:
                self.events.append('arrived')
                self.stop()
                return Intent()
            self.state = 'waiting'
            self._waiting_since = self._waiting_since or self.t
            if self.t - self._waiting_since > STEP_WAIT:
                self.events.append('gave_up')     # no way on found from here
                self.stop()
            return Intent(frozenset(keys), turn)
        self._waiting_since = None
        self.state = 'walking'
        if self._check_stuck(p):
            return Intent()
        return Intent(frozenset(keys), turn)

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
        # (walking up to a hostile one, stop out of its reach: "take me to the zombie" went right up to it and
        # it hit the player down to 4 health, 2026-10-13)
        stop_at = HOSTILE_STOP if e.hostile else REACH_CHARACTER
        if not self.attack and gap <= stop_at and self.clear_line is not False:
            self.events.append('arrived')
            self.stop()
            return Intent()
        # Straight at it only when close, on its level, with nothing between (a wall: round by the route)
        if (_flat(p, e.pos) > CHASE_DIRECT or abs(e.pos[1] - p[1]) > CHASE_LEVEL or self.clear_line is False
                or (_flat(p, e.pos) > 3.0 and (self.t < self._route_only_until or self._route_bends(p)))) \
                and self.route:
            return None if not taps else Intent(taps=tuple(taps))
        if self.clear_line is False and not self.route:
            # A wall between and no route yet: wait for one rather than walk into the wall (eighteenth session:
            # no route came for 10 s and it walked into the Gatefront barricade again)
            self.state = 'waiting_route'
            self._stuck_since = None
            return Intent(taps=tuple(taps))
        # Close: straight at it. Locked on, the camera follows by itself: don't fight it with the mouse.
        keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1])
        turn = 0.0 if self._locked else max(-TURN_RATE * dt, min(TURN_RATE * dt, err * TURN_GAIN * dt))
        self.state = 'chasing'
        if self._check_stuck(p):
            return Intent()
        return Intent(frozenset(keys), turn, tuple(taps))

    def _route_bends(self, p: Vec3) -> bool:
        """Whether the route ahead strays more than ROUTE_BENDS from the straight line to its end."""
        pts = self.route[self._progress + 1:]
        if len(pts) < 2:
            return False
        end = pts[-1]
        dx, dz = end[0] - p[0], end[2] - p[2]
        n = math.hypot(dx, dz)
        if n < 1e-6:
            return False
        for q in pts[:-1]:
            t = ((q[0] - p[0]) * dx + (q[2] - p[2]) * dz) / (n * n)
            if 0.0 < t < 1.0 and abs((q[0] - p[0]) * dz - (q[2] - p[2]) * dx) / n > ROUTE_BENDS:
                return True
        return False

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

    def _try_break(self, p: Vec3) -> bool:
        """Stuck with nothing seen ahead: swing at it (it is likely breakable), unless tried enough here."""
        for i, (where, n) in enumerate(self._breaks):
            if _flat(where, p) < BREAK_PLACE:
                if n >= BREAK_TRIES:
                    return False
                self._breaks[i] = (where, n + 1)
                break
        else:
            self._breaks.append((p, 1))
        self._break_swings = [self.t + k * BREAK_EVERY for k in range(BREAK_SWINGS)]
        self._stuck_since = None
        self.events.append('break')
        return True

    def _check_stuck(self, p: Vec3) -> bool:
        """True when the walk just gave up."""
        resumed = getattr(self, '_resumed_at', None)
        if resumed is not None and self.t - resumed < RESUME_GRACE:
            # (16:37: back in the game after a pause, the character stood 1.8 s with forward held: a
            # false "stuck" that used up one of the three tries before giving up)
            self._stuck_since = None
            return False
        if self._stuck_since is None:
            self._stuck_since, self._stuck_pos = self.t, p
            return False
        if self.breaks and self.ahead_blocked is False and _flat(p, self._stuck_pos) <= STUCK_MOVE \
                and self.t - self._stuck_since >= STUCK_SECONDS and self._try_break(p):
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
        if self.target_id is not None:   # chasing: the navmesh route from now on, for a while
            self._route_only_until = self.t + STUCK_ROUTE_S
        if self.precise:                 # the route is exact: re-plan from here at once rather than back off blind
            self.events.append('replan')
            self.route, self._progress = [], 0
            return False
        # back off, then round it to one side (the other side next time)
        self._unstick_until = self.t + UNSTICK_S
        self._around_until = self._unstick_until + AROUND_S
        self._unstick_side = 'left' if self._unstick_side == 'right' else 'right'
        if len(self._stucks) == 1:
            self.events.append('unstick')
            return False
        self.events.append('stuck')  # again: the caller blocks what's here and plans a new route
        return False

    def _special_action(self, snap: Snapshot, p: Vec3) -> Intent | None:
        """Ladders, lifts, jumps, breaking and placing blocks: None when walking normally."""
        if self._special is not None and self._special[0].what == 'parkour':
            act = self._special[0]
            got = self._leap(act, snap, p)
            if got is not None:
                return got
            act.done, self._special = True, None
            self._stuck_since = None
            return None
        if self._special is not None and self._special[0].what in ('break', 'place', 'look'):
            act = self._special[0]
            got = self._work(act, snap, p)
            if got is not None:
                return got
            act.done, self._special = True, None
            self._stuck_since = None
            return None
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
            if act.what == 'step' and self.precise:
                continue                                  # (the step walk jumps at every step up by itself)
            if act.what in ('jump', 'step'):
                act.done = True
                return Intent(frozenset({'forward'}), taps=('jump',))
            if act.what == 'parkour':
                if _flat(p, act.start) > WORK_AT or abs(p[1] - act.start[1]) > 0.8:
                    return None                           # walk on up to where it's jumped from first
                act.t0 = self.t
                act.phase = 'runup' if act.info.get('runup') else 'go'
                self._special = (act, self.t)
                self.events.append('work:parkour')
                return self._special_action(snap, p)
            if act.what in ('break', 'place', 'look'):
                if _flat(p, act.start) > WORK_AT or abs(p[1] - act.start[1]) > 1.2:
                    return None                           # walk on up to it first (nothing after it before it)
                if act.what == 'break' and self.probe is not None and act.info.get('key') is not None \
                        and not act.probed:
                    act.probed = True                     # (once, as work on it starts)
                    try:
                        there = self.probe(tuple(act.info['key']))
                    except Exception:
                        there = None
                    if there is False:                    # dug already: on to the next at once
                        act.done = True
                        continue
                act.t0 = self.t
                self._special = (act, self.t)
                self.events.append(f'work:{act.what}')
                return self._special_action(snap, p)
        return None

    def _leap(self, act: _Action, snap: Snapshot, p: Vec3) -> Intent | None:
        """A jump over a gap (Minecraft parkour): (back to the run-up block first, for a long one) face the
        landing, walk or sprint at the edge, jump just before it, keep going till landed. None: landed, or
        missed (then fallen: the walk plans again from where it is)."""
        cam = snap.camera
        land, start = act.end, act.start
        self.state = 'parkour'
        if self.t - act.t0 > LEAP_TIMEOUT or p[1] < min(start[1], land[1]) - 1.5:
            self.events.append('work_failed:parkour')
            if self.precise:
                self.events.append('replan')
                self.route, self._progress = [], 0
            return None
        if _flat(p, land) < LANDED and abs(p[1] - land[1]) < 0.6 and self.t - act.last > 0.2:
            if self.route:                                # on it: on along the route from the landing
                self._progress = min(range(len(self.route)), key=lambda j: _flat(land, self.route[j])
                                     + abs(land[1] - self.route[j][1]))
            return None
        d = (land[0] - start[0], land[2] - start[2])
        n = math.hypot(*d) or 1.0
        u = (d[0] / n, d[1] / n)
        want = _yaw((u[0], 0.0, u[1]))
        err = _wrap(want - _yaw(cam.forward))
        turn = max(-STEP_TURN_RATE / 30.0, min(STEP_TURN_RATE / 30.0, err * 0.5))
        if act.phase == 'runup':                          # back to the block behind the edge, facing on
            back = act.info['runup']
            if _flat(p, back) > 0.35:
                keys = {'back'} if abs(err) < 30.0 else set()
                return Intent(frozenset(keys), turn)
            act.phase = 'go'
        if abs(err) > LEAP_FACING and act.phase == 'go':
            return Intent(frozenset(), turn)              # face it squarely first
        act.phase = 'running' if act.phase == 'go' else act.phase
        keys = {'forward'}
        if act.info.get('sprint'):
            keys.add('sprint')
        out = (p[0] - start[0]) * u[0] + (p[2] - start[2]) * u[1]   # how far past the start block's middle
        taps = ()
        if act.phase == 'running' and out > LEAP_AT:
            taps = ('jump',)                              # at the edge
            act.phase, act.last = 'flying', self.t
        return Intent(frozenset(keys), turn, taps)

    def _aim(self, cam, point: Vec3, dt_k: float = 0.35) -> tuple[float, float, float]:
        return aim_at(cam, point, dt_k)

    def _aim_break(self, act: _Action, cam, p: Vec3) -> None:
        """Where to aim to break act's block: a point on it the crosshair reaches (self.aimer), asked as the work
        starts and again when the eye has moved; hidden, the block in front first (act.blocker), unless it's
        below the feet (never the floor stood on)."""
        if self.aimer is None or act.held is not None:
            return
        if act.aim_eye is not None and math.dist(cam.pos, act.aim_eye) < 0.25:   # (crouching: 0.35 lower)
            return
        act.aim_eye = cam.pos
        target = act.blocker or tuple(act.info.get('key') or ())
        try:
            pt, blocker = self.aimer(target, cam.pos)
        except Exception:
            pt, blocker = None, None
        if pt is not None:
            act.aim = pt
            return
        if act.blocker is None and blocker is not None and blocker[1] >= math.floor(p[1] + 0.01):
            act.blocker, act.aim = tuple(blocker), None
            act.aim_eye = None                            # (where to aim at it: asked next frame)

    def _work(self, act: _Action, snap: Snapshot, p: Vec3) -> Intent | None:
        """Breaking or placing a block for the route (Minecraft): aim, choose the hotbar slot, mine or place.
        None: done (or given up: the walk goes on, and gets stuck and re-plans if it must)."""
        ex, cam, info = snap.extra or {}, snap.camera, act.info
        limit = PLACE_TIMEOUT if act.what == 'place' else WORK_TIMEOUT
        if cam is None or self.t - act.t0 > limit:
            if cam is not None:
                self.events.append(f'work_failed:{act.what}')
                self.failed_work.append((act.what, dict(act.info)))
                if self.precise:
                    self.events.append('replan')           # a new way, not through what failed
                    self.route, self._progress = [], 0
            return None
        if act.what == 'look':                            # turn to face it (what the player came for)
            turn, tilt, err = self._aim(cam, act.end)
            self.state = 'looking'
            if err < 3.0 or self.t - act.t0 > 3.0:
                return None
            return Intent(frozenset(), turn, (), tilt)
        key = tuple(info.get('key') or ())
        aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) == key
        keys, taps = {'sneak'}, []
        self.state = 'breaking' if act.what == 'break' else 'building'
        if act.what == 'break':
            self._aim_break(act, cam, p)
            target = act.blocker or key
            aimed = ex.get('aim_block') is not None and tuple(ex['aim_block']) == tuple(target)
            turn, tilt, err = self._aim(cam, act.aim or act.end)
            if aimed:
                act.seen = True
                tool = ex.get('tool_slot', -1)
                if tool is not None and tool >= 0 and ex.get('slot') != tool and self.t - act.last > 0.3:
                    taps.append(f'slot{tool + 1}')
                    act.last = self.t
                keys.add('attack')
                act.held = act.held or self.t
            elif act.held is not None and err < 10.0:
                if act.blocker is None:
                    return None                           # it was on the crosshair and is gone: broken
                act.blocker, act.held, act.aim, act.seen = None, None, None, False   # what was in front: now it
                act.t0 = self.t
            elif not act.seen and err < 2.0 and self.t - act.t0 > 1.5:
                # never in sight: gone already, or hidden. Still there: failed, and planned again (taken as gone,
                # the walk jumped at the wall it was, again and again, 2026-10-13)
                there = None
                if self.probe is not None:
                    try:
                        there = self.probe(key)
                    except Exception:
                        there = None
                if there:
                    self.events.append('work_failed:break')
                    self.failed_work.append((act.what, dict(act.info)))
                    if self.precise:
                        self.events.append('replan')
                        self.route, self._progress = [], 0
                return None
            return Intent(frozenset(keys), turn, tuple(taps), tilt)
        into = tuple(info.get('into') or ())
        if ex.get('aim_block') is not None and tuple(ex['aim_block']) == into:
            return None                                   # placed: the crosshair is on the new block
        slot = ex.get('block_slot', -1)
        if slot is None or slot < 0:
            self.events.append('work_failed:no_blocks')
            return None
        if ex.get('slot') != slot and self.t - act.last > 0.3:
            taps.append(f'slot{slot + 1}')
            act.last = self.t
        if info.get('kind') == 'pillar':                  # look down, jump, place under the feet at the top
            turn, tilt, err = self._aim(cam, act.end)
            rise = p[1] - act.start[1]
            if rise > 0.95 and act.phase == 'placed' and self.t - act.last > 0.35:
                return None                               # standing on it
            if err < 12.0 and rise < 0.1 and act.phase != 'jumped' or (act.phase == 'jumped' and
                                                                         self.t - act.last > 1.2):
                taps.append('jump')
                act.phase, act.last = 'jumped', self.t
            elif act.phase in ('jumped', 'placed') and rise > 1.0 and aimed and self.t - act.last > 0.12:
                taps.append('interact')
                act.phase, act.last = 'placed', self.t
            return Intent(frozenset({'sneak'}) if rise < 0.1 else frozenset(), turn, tuple(taps), tilt)
        # a bridge: sneak to the edge (sneaking never walks off it), then turn round and place the floor ahead
        gap = info.get('gap') or act.end
        if act.phase == '':
            d = (gap[0] - act.start[0], gap[2] - act.start[2])
            n = math.hypot(*d) or 1.0
            out = ((p[0] - act.start[0]) * d[0] + (p[2] - act.start[2]) * d[1]) / n
            moving = act.held is None or _flat(p, act.end) < act.held - 0.02
            act.held = _flat(p, act.end)
            if out < 0.72 and (moving or self.t - act.t0 < 0.4) and self.t - act.t0 < 3.0:
                want = _yaw((gap[0] - p[0], 0.0, gap[2] - p[2]))
                err = _wrap(want - _yaw(cam.forward))
                return Intent(frozenset({'sneak', 'forward'}), err * 0.35, tuple(taps))
            act.phase = 'edge'
        turn, tilt, err = self._aim(cam, act.end)
        if aimed and ex.get('aim_face') == info.get('face') and ex.get('slot') == slot and self.t - act.last > 0.3:
            taps.append('interact')
            act.last = self.t
        return Intent(frozenset({'sneak'}), turn, tuple(taps), tilt)
