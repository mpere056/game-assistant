"""The fairy companion's movement: where it is in the world, every frame (docs/PLAN.md, phase 3).

Pure code, never a model. Modes:
- follow: floats beside the player's head on the camera's right, bobbing and drifting like Navi,
  kept away from the crosshair.
- show:   flies to an entity or a point (to show what the assistant is talking about), hovers there
          for a few seconds, then comes back.
- go:     flies to an entity or a point and stays there until told otherwise.
- lead:   flies ahead along a route (phase 4), waiting when the player falls behind.
Every target is clamped to the leash: never further than LEASH metres from the player. A target
beyond it makes the fairy wait at the edge, pointing the way (`waiting` is True).

update() takes a Snapshot and returns a FairyView: the world position plus what an overlay needs
(screen position, size, opacity). Game-independent: it only uses the adapter interface.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .interface import Camera, Snapshot, Vec3, add, dist, dot, length, normalize, scale, sub
from .lookat import project

LEASH = 75.0             # metres from the player, never further
HEAD = 1.75              # metres above the feet
SIDE = 0.85              # metres to the camera's right of the head while following
FOLLOW_STIFFNESS = 18.0  # spring toward the follow spot (higher = snappier)
TRAVEL_STIFFNESS = 6.0   # spring toward a target it flies to
MAX_SPEED = 14.0         # m/s
TELEPORT = 60.0          # further than this from its spot (a warp, a respawn): jump there
SHOW_SECONDS = 4.0
WORLD_RADIUS = 0.06      # the glow's core size in the world, metres
CROSSHAIR_CLEAR = 0.14   # screen half-size (-1..1 units) the fairy keeps out of while following
OCCLUSION_EVERY = 6      # frames between "is a wall in front of it?" rays

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


@dataclass
class _Target:
    entity_id: int | None = None
    point: Vec3 | None = None
    until: float | None = None   # for 'show': return after this time
    path: list[Vec3] = field(default_factory=list)  # for 'lead'


class Companion:
    def __init__(self, raycast: Raycast | None = None, leash: float = LEASH, scale: float = 1.0):
        self.raycast = raycast
        self.leash = leash
        self.scale = scale
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

    def _wanted(self, snap: Snapshot) -> Vec3:
        spot = None
        if self.mode in ('show', 'go'):
            if self.mode == 'show' and self.target.until is not None and self.t > self.target.until:
                self.follow()
            elif self.target.entity_id is not None:
                spot = self._entity_spot(snap, self.target.entity_id)
                if spot is None:  # it died or left: come back
                    self.follow()
            elif self.target.point is not None:
                pt = self.target.point
                spot = (pt[0], pt[1] + 1.0, pt[2]) if self.mode == 'show' else pt
        elif self.mode == 'lead':
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
        if self.pos is None or dist(self.pos, want) > TELEPORT:
            self.pos, self.vel = want, (0.0, 0.0, 0.0)
        # Critically damped spring toward the wanted spot, with a speed cap.
        k = FOLLOW_STIFFNESS if self.mode == 'follow' else TRAVEL_STIFFNESS
        c = 2.0 * math.sqrt(k)
        acc = sub(scale(sub(want, self.pos), k), scale(self.vel, c))
        vel = add(self.vel, scale(acc, dt))
        speed = length(vel)
        if speed > MAX_SPEED:
            vel = scale(vel, MAX_SPEED / speed)
        self.vel = vel
        self.pos = add(self.pos, scale(vel, dt))
        if self.mode == 'follow':
            self.pos = self._keep_off_crosshair(snap.camera, self.pos)
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
        target = 0.0 if not on_screen else (0.25 if self.occluded else 1.0)
        self.opacity += (target - self.opacity) * 0.2
        view = FairyView(on_screen and self.opacity > 0.02, self.pos, opacity=self.opacity, speaking=self.speaking,
                         mode=self.mode, waiting=self.waiting)
        if scr and on_screen:
            ty = math.tan(math.radians(cam.fov_y_deg) / 2)
            view.screen_x = scr.x + (x + 1) / 2 * scr.width
            view.screen_y = scr.y + (1 - y) / 2 * scr.height
            view.size_px = max(10.0, min(96.0, WORLD_RADIUS * self.scale / (depth * ty) * scr.height * 2.2))
        else:
            view.visible = False
        return view
