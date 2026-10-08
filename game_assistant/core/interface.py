"""The game-independent interface every game adapter provides.

The core (look-at, answers, skills, navigation) only ever uses what is defined here, so it runs
unchanged on any game whose adapter passes the adapter tests. Spec: docs/ADAPTER.md.

Conventions
- Positions are metres, Y up, in the adapter's world frame (any origin, any handedness).
- The camera carries its own forward, right and up vectors, so the core never needs to know
  the game's handedness.
- Every field the game cannot provide is None. The core must cope with that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

Vec3 = tuple[float, float, float]


# ---- small vector helpers --------------------------------------------------------------

def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3) -> Vec3:
    n = length(a)
    return (0.0, 0.0, 0.0) if n == 0 else scale(a, 1.0 / n)


def dist(a: Vec3, b: Vec3) -> float:
    return length(sub(a, b))


# ---- what a game reports --------------------------------------------------------------

@dataclass(frozen=True)
class Camera:
    pos: Vec3
    forward: Vec3        # unit vectors, mutually perpendicular
    right: Vec3
    up: Vec3
    fov_y_deg: float     # vertical field of view
    aspect: float        # width / height


@dataclass(frozen=True)
class Player:
    pos: Vec3            # feet
    facing: Vec3 | None  # unit vector the character faces, if known
    state: str           # 'alive', 'dead' or 'busy' (loading, cutscene, menu)
    health: float | None = None
    max_health: float | None = None


@dataclass(frozen=True)
class Entity:
    id: int              # stable while the entity exists
    type_id: int | None  # the game's own type id (Elden Ring: NpcParam id); key for knowledge lookups
    model: str | None    # the game's model or class name, if any (Elden Ring: "c4300")
    pos: Vec3            # feet
    center: Vec3         # middle of the body
    radius: float        # rough horizontal size, metres
    height: float        # metres
    hostile: bool
    dead: bool
    health: float | None = None
    max_health: float | None = None
    name: str | None = None  # only when the game itself provides one; knowledge() fills the rest


@dataclass(frozen=True)
class RayHit:
    start: Vec3
    end: Vec3
    hit: bool
    pos: Vec3 | None = None
    normal: Vec3 | None = None

    @property
    def distance(self) -> float | None:
        return dist(self.start, self.pos) if self.hit and self.pos else None


@dataclass(frozen=True)
class Snapshot:
    game: str
    frame: int           # increases while the game runs
    in_world: bool       # a character is loaded and usable
    player: Player | None
    camera: Camera | None
    entities: tuple[Entity, ...]
    area: str | None     # zone or map id as text, if known
    target_id: int | None = None  # the game's own target (lock-on, interaction), if known


# Capability names an adapter may list. The core checks these before using a part.
CAP_PLAYER = 'player'
CAP_CAMERA = 'camera'
CAP_ENTITIES = 'entities'
CAP_RAYCAST = 'raycast'
CAP_TARGET = 'target'          # the game's own lock-on / interaction target
CAP_KNOWLEDGE = 'knowledge'    # names and facts by type id
CAP_ACTIONS = 'actions'        # phase 3
CAP_PLACES = 'places'          # named places / fast travel (phase 3)


class GameAdapter(Protocol):
    game: str
    capabilities: frozenset[str]

    def snapshot(self) -> Snapshot:
        """A consistent copy of the current game state. Raises GameNotRunning if unavailable."""

    def raycast(self, rays: list[tuple[Vec3, Vec3]], timeout: float = 2.0) -> list[RayHit]:
        """Cast segments through the game's own collision (terrain and props, not characters)."""

    def knowledge(self, type_id: int) -> dict | None:
        """Facts about an entity type from the game's own data (name, resistances...), or None."""


class GameNotRunning(RuntimeError):
    """The game or its bridge is not running."""
