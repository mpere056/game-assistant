"""The look-at resolver: what is the player looking at, from geometry alone (no screenshot).

Order (docs/PLAN.md, phase 2):
1. The game's own target (lock-on), if the adapter reports one.
2. Living entities near the line of sight, scored by how far their body is from the crosshair
   (in degrees), then by distance. Bodies are treated as spheres: a close enemy covers more of
   the screen than a far one.
3. Each candidate is checked for visibility with a ray from the camera; walls hide them.
4. If no entity is there, one ray straight ahead describes the surface the crosshair is on.

Pure functions on a Snapshot plus a raycast callable, so they can be tested without a game.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .interface import Camera, Entity, RayHit, Snapshot, Vec3, add, dot, length, normalize, scale, sub

# Tuning (starting values; measure with the adapter tests and adjust).
MAX_ENTITY_DISTANCE = 80.0   # metres; Elden Ring's bridge publishes characters within 80 m
CONE_DEG = 20.0              # how far outside a body the crosshair may be and still count (2026-10-09:
                             # 6 degrees was too strict; players don't aim exactly when asking)
SURE_DEG = 4.0               # within this of a body: as sure as if the crosshair were on it
TIE_DEG = 1.5                # two candidates scoring closer than this are ambiguous
HOSTILE_BONUS_DEG = 3.0      # an enemy wins over a friendly character this far off
DISTANCE_DEG_PER_M = 0.06    # a closer candidate wins small differences (1 degree per ~17 m)
SURFACE_RAY = 300.0          # metres, the ray straight ahead when no entity is aimed at
VISIBILITY_MARGIN = 0.5      # metres; a wall hit this close to the body does not hide it

Raycast = Callable[[list[tuple[Vec3, Vec3]]], list[RayHit]]


@dataclass
class Candidate:
    entity: Entity
    distance: float          # camera to body centre, metres
    off_deg: float           # degrees between the crosshair and the edge of the body (0 = on it)
    screen: tuple[float, float]  # -1..1 left..right, -1..1 bottom..top
    visible: bool | None = None  # None = not checked (no raycast available)

    @property
    def side(self) -> str:
        x, y = self.screen
        h = 'left' if x < -0.15 else 'right' if x > 0.15 else ''
        v = 'low' if y < -0.15 else 'high' if y > 0.15 else ''
        return ' '.join(w for w in (v, h) if w) or 'centre'


@dataclass
class LookResult:
    kind: str                # 'target', 'entity', 'surface' or 'nothing'
    confidence: float        # 0..1
    best: Candidate | None = None
    others: list[Candidate] = field(default_factory=list)   # runners-up, best first
    hidden: list[Candidate] = field(default_factory=list)   # near the crosshair but behind a wall
    surface: RayHit | None = None
    surface_kind: str | None = None  # 'ground', 'wall', 'slope' or 'ceiling'


def project(cam: Camera, p: Vec3) -> tuple[float, float, float]:
    """(x, y, depth): screen position -1..1 and distance along the view direction."""
    v = sub(p, cam.pos)
    depth = dot(v, cam.forward)
    if depth <= 1e-6:
        return (math.inf, math.inf, depth)
    ty = math.tan(math.radians(cam.fov_y_deg) / 2)
    tx = ty * cam.aspect
    return (dot(v, cam.right) / (depth * tx), dot(v, cam.up) / (depth * ty), depth)


def _candidate(cam: Camera, e: Entity) -> Candidate | None:
    v = sub(e.center, cam.pos)
    d = length(v)
    if d < 0.3 or d > MAX_ENTITY_DISTANCE:
        return None
    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot(normalize(v), cam.forward)))))
    size = max(e.radius, e.height / 2)
    angular_radius = math.degrees(math.atan2(size, d))
    off = max(0.0, angle - angular_radius)
    if off > CONE_DEG:
        return None
    x, y, depth = project(cam, e.center)
    if depth <= 0 or abs(x) > 1.2 or abs(y) > 1.2:
        return None
    return Candidate(e, d, off, (x, y))


def _score(c: Candidate) -> float:
    """Lower is a better match: degrees off the crosshair, adjusted for kind and distance."""
    return c.off_deg + c.distance * DISTANCE_DEG_PER_M - (HOSTILE_BONUS_DEG if c.entity.hostile else 0.0)


def surface_kind(normal: Vec3 | None) -> str | None:
    if normal is None:
        return None
    ny = normal[1]
    if ny > 0.75:
        return 'ground'
    if ny < -0.5:
        return 'ceiling'
    if abs(ny) < 0.35:
        return 'wall'
    return 'slope'


def resolve(snap: Snapshot, raycast: Raycast | None = None) -> LookResult:
    cam = snap.camera
    if cam is None or not snap.in_world:
        return LookResult('nothing', 0.0)

    living = [e for e in snap.entities if not e.dead]

    if snap.target_id is not None:
        for e in living:
            if e.id == snap.target_id:
                c = _candidate(cam, e) or Candidate(e, length(sub(e.center, cam.pos)), 0.0, (0.0, 0.0))
                c.visible = True
                return LookResult('target', 1.0, best=c)

    cands = sorted((c for c in (_candidate(cam, e) for e in living) if c), key=_score)
    hidden: list[Candidate] = []
    if cands and raycast is not None:
        # Two rays per candidate, to the middle of the body and to the upper chest, each stopping
        # short of the body: an enemy behind a low ridge or rock still counts as visible.
        rays = []
        for c in cands:
            e = c.entity
            for p in (e.center, add(e.center, (0.0, e.height * 0.3, 0.0))):
                to = sub(p, cam.pos)
                stop = max(0.0, length(to) - max(e.radius, 0.3))
                rays.append((cam.pos, add(cam.pos, scale(normalize(to), stop))))
        hits = raycast(rays)
        for i, c in enumerate(cands):
            clear = 0
            for h in hits[2 * i:2 * i + 2]:
                seg = length(sub(h.end, h.start))
                if not (h.hit and h.distance is not None and h.distance < seg - VISIBILITY_MARGIN):
                    clear += 1
            c.visible = clear > 0
        hidden = [c for c in cands if c.visible is False]
        cands = [c for c in cands if c.visible is not False]

    if cands:
        best, others = cands[0], cands[1:3]
        if best.off_deg <= SURE_DEG:
            confidence = 0.9
        else:
            confidence = max(0.35, 0.9 - 0.5 * (best.off_deg - SURE_DEG) / (CONE_DEG - SURE_DEG))
        if others and _score(others[0]) - _score(best) < TIE_DEG and abs(others[0].distance - best.distance) < 3.0:
            confidence = min(confidence, 0.5)  # two things right at the crosshair: ask which one
        return LookResult('entity', round(confidence, 2), best=best, others=others, hidden=hidden[:3])

    if raycast is None:
        return LookResult('nothing', 0.0, hidden=hidden[:3])
    end = add(cam.pos, scale(cam.forward, SURFACE_RAY))
    h = raycast([(cam.pos, end)])[0]
    if not h.hit:
        return LookResult('nothing', 0.8, hidden=hidden[:3], surface=h)
    return LookResult('surface', 0.8, hidden=hidden[:3], surface=h, surface_kind=surface_kind(h.normal))


def facts(result: LookResult, knowledge: Callable[[int], dict | None] | None = None) -> dict:
    """The short, exact facts a model is allowed to phrase. Nothing here is guessed."""

    def entity_facts(c: Candidate, side: bool = False) -> dict:
        e = c.entity
        known = knowledge(e.type_id) if (knowledge and e.type_id is not None) else None
        out = {
            'name': (known or {}).get('name') or e.name,
            'type_id': e.type_id,
            'model': e.model,
            'hostile': e.hostile,
            'distance_m': round(c.distance),
        }
        if side:  # only to tell several candidates apart
            out['side'] = c.side
        if e.health is not None and e.max_health:
            out['health'] = f'{e.health:.0f}/{e.max_health:.0f}'
        if known:
            out['data'] = {k: v for k, v in known.items() if k != 'name'}
        return out

    f: dict = {'looking_at': result.kind, 'confidence': result.confidence}
    if result.best:
        f['thing'] = entity_facts(result.best)
    if result.others:
        f['also_near_crosshair'] = [entity_facts(c, side=True) for c in result.others]
    if result.hidden:
        f['behind_cover'] = [entity_facts(c) for c in result.hidden]
    if result.kind == 'surface' and result.surface:
        f['surface'] = {'kind': result.surface_kind, 'distance_m': round(result.surface.distance or 0, 1)}
    if result.kind == 'nothing' and result.surface is not None:
        f['surface'] = {'kind': None, 'note': f'no surface within {SURFACE_RAY:.0f} m (open sky or far away)'}
    return f
