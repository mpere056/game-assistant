"""The fairy companion's movement: where it is in the world, every frame (docs/PLAN.md, phase 3).

Pure code, never a model. Modes:
- follow: floats beside the player's head on the camera's right, bobbing and drifting like Navi,
  kept away from the crosshair.
- show:   flies to an entity or a point (to show what the assistant is talking about), hovers there
          for a few seconds, then comes back.
- go:     flies to an entity or a point and stays there until told otherwise.
- lead:   flies ahead along a route (phase 4), waiting when the player falls behind.
- guide:  for far places (a grace, a landmark 2 km away): plans a walkable route (nav.WIDE: 150 m
          around the player, no cliffs) in the background and flies ahead along it, as far as the
          player can see (up to GUIDE_AHEAD), hovering BEACON_HEIGHT above the ground; re-plans as the
          player moves. Within ARRIVE metres it announces the arrival.
Every target except guide's is clamped to the leash: never further than LEASH metres from the player. A target
beyond it makes the fairy wait at the edge, pointing the way (`waiting` is True).

update() takes a Snapshot and returns a FairyView: the world position plus what an overlay needs
(screen position, size, opacity). Game-independent: it only uses the adapter interface.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import nav
from .interface import Camera, Snapshot, Vec3, add, dist, dot, length, normalize, scale, sub
from .lookat import project

LEASH = 75.0             # metres from the player, never further
HEAD = 1.75              # metres above the feet
SIDE = 0.85              # metres to the camera's right of the head while following
FOLLOW_STIFFNESS = 18.0  # spring toward the follow spot (higher = snappier)
TRAVEL_STIFFNESS = 6.0   # spring toward a target it flies to
TRAVEL_SPEED = 10.0      # m/s when flying somewhere: faster than running (~6 m/s), easy to follow by eye
FOLLOW_SPEED = 14.0      # m/s cap while following (it stays close anyway)
MAX_ACCEL = 14.0         # m/s^2: speeds up and slows down smoothly instead of darting
KEEP_UP = 1.3            # flies at least this many times the player's speed (on Torrent, for example)
TELEPORT = 180.0         # further than this from the PLAYER (a warp, a respawn): jump back beside them.
                         # (Was "60 m from its target", which made it jump instead of fly to far targets.)
SHOW_SECONDS = 4.0
WORLD_RADIUS = 0.06      # the glow's core size in the world, metres
CROSSHAIR_CLEAR = 0.14   # screen half-size (-1..1 units) the fairy keeps out of while following
OCCLUSION_EVERY = 6      # frames between "is a wall in front of it?" rays
GUIDE_AHEAD = 100.0      # guide mode: at most this far ahead of the player (the user asked for 100 m)
BEACON_HEIGHT = 6.0      # guide mode: metres above the ground under it (high, to show over rocks and crests)
GUIDE_NEAREST = 15.0     # guide mode: never closer ahead than this
GUIDE_STEP = 10.0        # guide mode: candidate spots every this many metres toward the goal
SIGHT_EVERY = 15         # guide mode: frames between "which spot ahead can the player see?" checks
REPLAN_NEAR_END = 50.0   # guide mode: re-plan when the player is this close to the end of a partial route
REPLAN_OFF_ROUTE = 25.0  # guide mode: ...or this far from the route
REPLAN_EVERY = 12.0      # guide mode: ...or this many seconds after the last plan
NO_PROGRESS = 15.0       # guide mode: a route must bring the player at least this much closer, else "no way" 
TRAIL_SECONDS = 2.0      # the trail: world positions of the last 2 s (the user asked for about 2 s)
TRAIL_SAMPLES = 30
TRAIL_MIN_SPEED = 2.5    # m/s: a place where it moved slower than this leaves no trail dot
ARRIVE = 15.0            # guide mode: this close (horizontally)...
ARRIVE_HEIGHT = 6.0      # ...and this close in height counts as arrived (the user was told "here we are"
                         # standing on the ground above a grace that was 67 m down inside a tunnel)
GROUND_EVERY = 20        # guide mode: frames between ground checks under the fairy

Raycast = Callable[[list[tuple[Vec3, Vec3]]], list]


@dataclass
class FairyView:
    visible: bool
    pos: Vec3 | None = None
    screen_x: float = 0.0     # desktop pixels (centre of the glow)
    screen_y: float = 0.0
    size_px: float = 0.0      # the glow's diameter on screen
    opacity: float = 1.0      # 0..1 (faded behind walls)
    speaking: bool = False
    mode: str = 'follow'
    waiting: bool = False     # held at the leash, pointing toward something further
    trail: list = field(default_factory=list)  # [(screen x, screen y, size px, opacity)], oldest last
    edge: tuple | None = None # (screen x, screen y): where to mark it at the screen's edge when it's off-screen


@dataclass
class _Target:
    entity_id: int | None = None
    point: Vec3 | None = None
    until: float | None = None   # for 'show': return after this time
    name: str | None = None      # for 'guide': the place's name (for the arrival message)
    path: list[Vec3] = field(default_factory=list)  # for 'lead'


class Companion:
    def __init__(self, raycast: Raycast | None = None, leash: float = LEASH, scale: float = 1.0,
                 guide_ahead: float = GUIDE_AHEAD):
        self.raycast = raycast
        self.leash = leash
        self.scale = scale
        self.guide_ahead = guide_ahead
        self.events: list[str] = []   # things to tell the player ("arrived:<place>"), taken by the app
        self._ground: float | None = None
        self._last_player: Vec3 | None = None
        self.player_speed = 0.0       # m/s, smoothed
        self.trail: list[tuple[float, Vec3, bool]] = []  # (time, world position, moving fast), newest last
        self._sight_ahead: float | None = None  # guide mode: how far ahead the player can still see it
        self.plan_async = True        # plan routes on a background thread (tests plan inline)
        self.route: list[Vec3] | None = None    # guide mode: walkable waypoints toward the goal
        self.route_complete = False   # the route reaches the goal (else it ends as close as the area allows)
        self._route_time = 0.0
        self._planning = False
        self._said_no_way = False
        self._plan_gen = 0            # bumped by every new guide: late plans for an old goal are dropped
        self.pos: Vec3 | None = None
        self.vel: Vec3 = (0.0, 0.0, 0.0)
        self.mode = 'follow'
        self.target = _Target()
        self.t = 0.0
        self.frame = 0
        self.occluded = False
        self.opacity = 0.0
        self.speaking = False
        self.waiting = False

    # ---- commands (instant, local) ----

    def follow(self) -> None:
        self.mode, self.target = 'follow', _Target()

    def show(self, entity_id: int | None = None, point: Vec3 | None = None, seconds: float = SHOW_SECONDS) -> None:
        self.mode, self.target = 'show', _Target(entity_id, point, self.t + seconds)

    def go(self, entity_id: int | None = None, point: Vec3 | None = None) -> None:
        self.mode, self.target = 'go', _Target(entity_id, point)

    def lead(self, path: list[Vec3]) -> None:
        self.mode, self.target = 'lead', _Target(path=list(path))

    def guide(self, point: Vec3, name: str | None = None) -> None:
        self.mode, self.target = 'guide', _Target(point=point, name=name)
        self._ground = None
        self._sight_ahead = None
        self.route, self.route_complete, self._route_time = None, False, 0.0
        self._said_no_way = False
        self._plan_gen += 1

    def _cast(self, rays):
        return self.raycast(rays)

    def _start_plan(self, start: Vec3, goal: Vec3) -> None:
        if self._planning or not self.raycast:
            return
        self._planning = True
        gen = self._plan_gen

        def work():
            far = math.hypot(goal[0] - start[0], goal[2] - start[2]) > 250.0
            best, best_gain = None, -math.inf
            for profile in ((nav.WIDE, nav.FAR) if far else (nav.WIDE,)):
                try:
                    route = nav.plan(self._cast, start, goal, profile=profile)
                except Exception:
                    route = None
                if route and len(route.waypoints) >= 2:
                    end = route.waypoints[-1]
                    gain = math.hypot(goal[0] - start[0], goal[2] - start[2]) - math.hypot(goal[0] - end[0], goal[2] - end[2])
                    if route.reaches_goal or gain > best_gain:
                        best, best_gain = route, (math.inf if route.reaches_goal else gain)
                    if route.reaches_goal:
                        break
            if gen == self._plan_gen and self.mode == 'guide':
                if best is not None and best_gain >= NO_PROGRESS:
                    self.route, self.route_complete = best.waypoints, best.reaches_goal
                elif not self._said_no_way:  # walled in by cliffs or water: say so instead of pointing over them
                    self._said_no_way = True
                    self.route = None
                    self.events.append(f'no_way:{self.target.name or "there"}')
                self._route_time = self.t
            self._planning = False

        if self.plan_async:
            threading.Thread(target=work, name='fairy route', daemon=True).start()
        else:
            work()

    def _route_ahead(self, p: Vec3) -> tuple[list[Vec3], float]:
        """The route from the point nearest the player onward, and how far the player is from it."""
        pts = self.route
        best, best_d, best_i = pts[0], math.inf, 0
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            ab = (b[0] - a[0], b[2] - a[2])
            l2 = ab[0] ** 2 + ab[1] ** 2 or 1e-9
            k = max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[2] - a[2]) * ab[1]) / l2))
            q = (a[0] + ab[0] * k, a[1] + (b[1] - a[1]) * k, a[2] + ab[1] * k)
            d = math.hypot(p[0] - q[0], p[2] - q[2])
            if d < best_d:
                best, best_d, best_i = q, d, i
        return [best] + pts[best_i + 1:], best_d

    @staticmethod
    def _along(path: list[Vec3], distance: float) -> Vec3:
        """The point `distance` metres along a polyline (horizontal length), or its end."""
        left = distance
        for a, b in zip(path, path[1:]):
            seg = math.hypot(b[0] - a[0], b[2] - a[2])
            if seg >= left and seg > 0:
                k = left / seg
                return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)
            left -= seg
        return path[-1]

    def stop(self) -> None:
        """Hold still where it is (it still keeps to the leash)."""
        if self.pos is not None:
            self.mode, self.target = 'go', _Target(point=self.pos)

    # ---- per frame ----

    def _follow_spot(self, snap: Snapshot) -> Vec3:
        p, cam = snap.player.pos, snap.camera
        head = (p[0], p[1] + HEAD, p[2])
        # Drift slowly around the spot and bob up and down, like Navi.
        drift = (math.sin(self.t * 0.7) * 0.25, math.sin(self.t * 2.1) * 0.12, math.sin(self.t * 0.5 + 1.3) * 0.25)
        right = cam.right if cam else (1.0, 0.0, 0.0)
        flat_right = normalize((right[0], 0.0, right[2])) if cam else right
        return add(add(head, scale(flat_right, SIDE)), drift)

    def _entity_spot(self, snap: Snapshot, entity_id: int) -> Vec3 | None:
        for e in snap.entities:
            if e.id == entity_id and not e.dead:
                above = (e.center[0], e.center[1] + e.height * 0.5 + 0.5, e.center[2])
                # Hover a little on the player's side of it, so it reads as "this one".
                toward = normalize(sub(snap.player.pos, e.pos))
                return add(above, scale((toward[0], 0.0, toward[2]), max(e.radius, 0.4) + 0.4))
        return None

    def _lead_spot(self, snap: Snapshot) -> Vec3 | None:
        path = self.target.path
        p = snap.player.pos
        # Drop waypoints the player has reached; fly to the first one ahead, at most 8 m ahead of them.
        while len(path) > 1 and dist(path[0], p) < 3.0:
            path.pop(0)
        if not path:
            return None
        goal = path[0]
        if dist(goal, p) > 8.0:
            goal = add(p, scale(normalize(sub(goal, p)), 8.0))
        return (goal[0], goal[1] + 1.5, goal[2])

    def _guide_spot(self, snap: Snapshot) -> Vec3 | None:
        """Ahead of the player toward the goal, along a walkable route when one is planned (so it never
        leads over a cliff), as far as the player's camera can see it (up to guide_ahead), hovering
        BEACON_HEIGHT above the ground; nearer when hills or rocks hide the far spots."""
        goal, p = self.target.point, snap.player.pos
        if math.hypot(goal[0] - p[0], goal[2] - p[2]) < ARRIVE:
            dy = goal[1] - p[1]
            if abs(dy) <= ARRIVE_HEIGHT:
                self.events.append(f'arrived:{self.target.name or "there"}')
            else:  # right above or below it (a cave, a tunnel, a cliff): show where it really is
                self.events.append(f'level:{self.target.name or "it"}:{round(dy)}')
            self.show(point=goal, seconds=SHOW_SECONDS * 1.5)
            return None
        # Plan, or re-plan: no route yet, near the end of a partial one, off the route, or stale.
        if self.route is None:
            if not self._planning and self.t - self._route_time > 1.0:
                self._start_plan(p, goal)
            if self.raycast:
                # No route yet (planning takes about half a second) or none found: wait a little way ahead
                # in the goal's direction rather than fly far out over terrain it hasn't checked.
                d = math.hypot(goal[0] - p[0], goal[2] - p[2])
                k = min(1.0, GUIDE_NEAREST / max(d, 1e-6))
                path = [p, (p[0] + (goal[0] - p[0]) * k, p[1], p[2] + (goal[2] - p[2]) * k)]
            else:  # no way to look at the ground at all: straight toward the goal
                path = [p, goal]
        else:
            path, off = self._route_ahead(p)
            path_len = sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(path, path[1:]))
            if not self._planning and ((not self.route_complete and path_len < REPLAN_NEAR_END)
                                       or off > REPLAN_OFF_ROUTE or self.t - self._route_time > REPLAN_EVERY):
                self._start_plan(p, goal)
            if self.route_complete:
                path = path + [goal]
        total = sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(path, path[1:]))
        far = min(self.guide_ahead, total)
        if self.raycast and snap.camera and (self._sight_ahead is None or self.frame % SIGHT_EVERY == 0):
            cands = []
            a = far
            while a >= min(GUIDE_NEAREST, far) - 0.01 or not cands:
                cands.append(a)
                a -= GUIDE_STEP
            cands = cands[:12]
            try:
                pts = [self._along(path, a) for a in cands]
                if self.route is None:  # straight line: find the ground under each spot
                    downs = [((q[0], q[1] + 150.0, q[2]), (q[0], q[1] - 150.0, q[2])) for q in pts]
                    grounds = [h.pos[1] if h.hit and h.pos else q[1] for h, q in zip(self.raycast(downs), pts)]
                else:  # along the route: its waypoints are already on the ground
                    grounds = [q[1] for q in pts]
                spots = [(q[0], g + BEACON_HEIGHT, q[2]) for q, g in zip(pts, grounds)]
                eye = snap.camera.pos
                sights = self.raycast([(eye, sp) for sp in spots])
                chosen = None
                for a, sp, h in zip(cands, spots, sights):  # furthest first
                    if not (h.hit and h.distance is not None and h.distance < dist(eye, sp) - 1.0):
                        chosen = a
                        break
                a = chosen if chosen is not None else cands[-1]
                self._sight_ahead = a if self._sight_ahead is None else self._sight_ahead + (a - self._sight_ahead) * 0.5
            except Exception:
                self._sight_ahead = self._sight_ahead or min(far, 40.0)
        ahead = min(far, self._sight_ahead if self._sight_ahead is not None else far)
        q = self._along(path, ahead)
        if self.route is None and self._ground is None and self.raycast:
            try:
                h = self.raycast([((q[0], q[1] + 150.0, q[2]), (q[0], q[1] - 150.0, q[2]))])[0]
                self._ground = h.pos[1] if h.hit and h.pos else None
            except Exception:
                pass
        base = q[1] if self.route is not None else (self._ground if self._ground is not None else q[1])
        bob = math.sin(self.t * 2.0) * 0.4
        return (q[0], base + BEACON_HEIGHT + bob, q[2])

    def _wanted(self, snap: Snapshot) -> Vec3:
        spot = None
        if self.mode == 'guide':
            spot = self._guide_spot(snap)
            if spot is not None:
                self.waiting = False
                return spot  # far guiding is not held by the leash; GUIDE_AHEAD limits it
        if spot is None and self.mode in ('show', 'go'):
            if self.mode == 'show' and self.target.until is not None and self.t > self.target.until:
                self.follow()
            elif self.target.entity_id is not None:
                spot = self._entity_spot(snap, self.target.entity_id)
                if spot is None:  # it died or left: come back
                    self.follow()
            elif self.target.point is not None:
                pt = self.target.point
                spot = (pt[0], pt[1] + 1.0, pt[2]) if self.mode == 'show' else pt
        if self.mode == 'lead':
            spot = self._lead_spot(snap)
            if spot is None:
                self.follow()
        if spot is None:
            spot = self._follow_spot(snap)
        # Leash: never further than the leash from the player.
        p = snap.player.pos
        head = (p[0], p[1] + HEAD, p[2])
        d = dist(spot, head)
        self.waiting = d > self.leash
        if self.waiting:
            spot = add(head, scale(normalize(sub(spot, head)), self.leash))
        return spot

    def _keep_off_crosshair(self, cam: Camera, pos: Vec3) -> Vec3:
        x, y, depth = project(cam, pos)
        if depth <= 0 or abs(x) > CROSSHAIR_CLEAR or abs(y) > CROSSHAIR_CLEAR * 1.6:
            return pos
        # Slide sideways (away from the centre) just enough to clear the crosshair.
        tx = math.tan(math.radians(cam.fov_y_deg) / 2) * cam.aspect
        side = 1.0 if x >= 0 else -1.0
        need = (CROSSHAIR_CLEAR * side - x) * depth * tx
        return add(pos, scale(cam.right, need))

    def update(self, snap: Snapshot, dt: float) -> FairyView:
        dt = max(0.0, min(dt, 0.1))
        self.t += dt
        self.frame += 1
        if not (snap.in_world and snap.player and snap.camera) or snap.menu:
            self.opacity = 0.0
            return FairyView(False, self.pos, mode=self.mode)
        want = self._wanted(snap)
        p = snap.player.pos
        if self._last_player is not None and dt > 0:
            moved = dist(p, self._last_player) / dt
            if moved < 60:  # ignore warps
                self.player_speed += (moved - self.player_speed) * min(1.0, dt * 4)
        self._last_player = p
        if self.pos is None or dist(self.pos, p) > TELEPORT:
            self.pos, self.vel = want, (0.0, 0.0, 0.0)
        # Critically damped spring toward the wanted spot, with a speed cap.
        k = FOLLOW_STIFFNESS if self.mode == 'follow' else TRAVEL_STIFFNESS
        c = 2.0 * math.sqrt(k)
        acc = sub(scale(sub(want, self.pos), k), scale(self.vel, c))
        if self.mode != 'follow':  # travelling: smooth acceleration, so the eye can follow it
            a = length(acc)
            if a > MAX_ACCEL:
                acc = scale(acc, MAX_ACCEL / a)
        vel = add(self.vel, scale(acc, dt))
        cap = FOLLOW_SPEED if self.mode == 'follow' else max(TRAVEL_SPEED, self.player_speed * KEEP_UP)
        speed = length(vel)
        if speed > cap:
            vel = scale(vel, cap / speed)
        self.vel = vel
        self.pos = add(self.pos, scale(vel, dt))
        if self.mode == 'follow':
            self.pos = self._keep_off_crosshair(snap.camera, self.pos)
        if not self.trail or self.t - self.trail[-1][0] >= TRAIL_SECONDS / TRAIL_SAMPLES:
            fast = length(self.vel) >= TRAIL_MIN_SPEED
            self.trail.append((self.t, self.pos, fast))
            while self.trail and self.t - self.trail[0][0] > TRAIL_SECONDS:
                self.trail.pop(0)
        return self._view(snap)

    def _view(self, snap: Snapshot) -> FairyView:
        cam, scr = snap.camera, snap.screen
        x, y, depth = project(cam, self.pos)
        on_screen = depth > 0.2 and abs(x) <= 1.05 and abs(y) <= 1.05
        # Is a wall between the camera and the fairy? Checked every few frames.
        if self.raycast and on_screen and self.frame % OCCLUSION_EVERY == 0:
            try:
                h = self.raycast([(cam.pos, self.pos)])[0]
                self.occluded = bool(h.hit and h.distance is not None and h.distance < dist(cam.pos, self.pos) - 0.3)
            except Exception:
                self.occluded = False
        hidden_level = 0.5 if self.mode in ('guide', 'go', 'show', 'lead') else 0.25  # travelling: show through terrain
        target = 0.0 if not on_screen else (hidden_level if self.occluded else 1.0)
        self.opacity += (target - self.opacity) * 0.2
        view = FairyView(on_screen and self.opacity > 0.02, self.pos, opacity=self.opacity, speaking=self.speaking,
                         mode=self.mode, waiting=self.waiting)
        if scr and on_screen:
            ty = math.tan(math.radians(cam.fov_y_deg) / 2)
            view.screen_x = scr.x + (x + 1) / 2 * scr.width
            view.screen_y = scr.y + (1 - y) / 2 * scr.height
            smallest = 24.0 if self.mode == 'guide' else 10.0  # a far beacon must stay easy to spot
            view.size_px = max(smallest, min(96.0, WORLD_RADIUS * self.scale / (depth * ty) * scr.height * 2.2))
        else:
            view.visible = False
        if scr:
            view.trail = self._trail_on_screen(cam, scr)
            if not on_screen and self.mode != 'follow':  # off-screen or behind: mark it at the screen's edge
                view.edge = self._edge_mark(cam, scr, x, y, depth)
        return view

    def _trail_on_screen(self, cam, scr) -> list:
        """The trail's world positions seen through the current camera (so it stays put in the world when
        the camera turns), newest first, smaller and fainter with age."""
        out = []
        ty = math.tan(math.radians(cam.fov_y_deg) / 2)
        for t, pos, fast in reversed(self.trail[:-1]):
            age = self.t - t
            if not fast or age >= TRAIL_SECONDS:
                continue
            x, y, depth = project(cam, pos)
            if depth <= 0.2 or abs(x) > 1.05 or abs(y) > 1.05:
                continue
            fade = 1.0 - age / TRAIL_SECONDS
            size = max(8.0, min(70.0, WORLD_RADIUS * self.scale / (depth * ty) * scr.height * 2.2)) * (0.75 - 0.4 * age / TRAIL_SECONDS)
            out.append((scr.x + (x + 1) / 2 * scr.width, scr.y + (1 - y) / 2 * scr.height, size,
                        max(0.15, self.opacity) * 0.55 * fade ** 1.3))
        return out

    def _edge_mark(self, cam, scr, x, y, depth) -> tuple:
        """A point just inside the screen's edge, in the direction of the off-screen fairy (also when it
        is behind the camera, where the screen projection doesn't apply)."""
        v = sub(self.pos, cam.pos)
        sx, sy = dot(v, cam.right), dot(v, cam.up)
        if depth <= 0.2 and abs(sx) < 1e-6 and abs(sy) < 1e-6:
            sx = 1.0
        if depth > 0.2 and math.isfinite(x) and math.isfinite(y):
            sx, sy = x, y  # in front but outside the picture: the projection gives the right side
        k = 0.9 / max(abs(sx), abs(sy), 1e-6)
        ex, ey = max(-0.9, min(0.9, sx * k)), max(-0.9, min(0.9, sy * k))
        return (scr.x + (ex + 1) / 2 * scr.width, scr.y + (1 - ey) / 2 * scr.height)
