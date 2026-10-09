"""A pretend game for trying the assistant without a real game: `Assistant.bat demo`.

The "screen" is your whole primary monitor and nothing of the world is drawn except the fairy, so
you see the fairy float and fly over your desktop. The player walks slowly in a circle in front of
a fixed camera; one pretend enemy stands ahead. The ground is a flat plane and there is one wall.
All facts it returns say they are pretend.
"""
from __future__ import annotations

import ctypes
import math
import time

from ...core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_PLAYER, CAP_RAYCAST, CAP_SCREEN, Camera,
                               Entity, Player, RayHit, Screen, Snapshot, Vec3, cross, normalize, sub)

CAM_POS = (0.0, 3.0, -6.0)
LOOK_AT = (0.0, 1.0, 6.0)
WALL_Z = 14.0   # a 4 m high wall across the scene


class DemoAdapter:
    game = 'Demo scene (pretend game)'
    capabilities = frozenset({CAP_PLAYER, CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_KNOWLEDGE, CAP_SCREEN})
    vocabulary = 'Demo scene. Fairy, come back, go to that, wolf.'

    def __init__(self):
        self.t0 = time.perf_counter()
        user32 = ctypes.windll.user32
        self.w, self.h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def snapshot(self) -> Snapshot:
        t = time.perf_counter() - self.t0
        fwd = normalize(sub(LOOK_AT, CAM_POS))
        right = normalize(cross((0.0, 1.0, 0.0), fwd))  # left-handed, like Elden Ring
        up = cross(fwd, right)
        cam = Camera(CAM_POS, fwd, right, up, 48.0, self.w / self.h)
        p = (math.sin(t * 0.25) * 3.0, 0.0, 2.0 + math.cos(t * 0.25) * 2.0)
        wolf = Entity(1, 1, 'pretend wolf', (5.0, 0.0, 9.0), (5.0, 0.6, 9.0), 0.6, 1.2, True, False, 120, 120,
                      name='Pretend Wolf')
        return Snapshot(self.game, int(t * 60), True, Player(p, (0.0, 0.0, 1.0), 'alive'), cam, (wolf,), 'demo',
                        screen=Screen(0, 0, self.w, self.h, True))

    def raycast(self, rays: list[tuple[Vec3, Vec3]], timeout: float = 2.0) -> list[RayHit]:
        out = []
        for a, b in rays:
            hit = None
            if (a[2] - WALL_Z) * (b[2] - WALL_Z) < 0:
                k = (WALL_Z - a[2]) / (b[2] - a[2])
                p = tuple(a[i] + (b[i] - a[i]) * k for i in range(3))
                if 0 <= p[1] <= 4:
                    hit = RayHit(a, b, True, p, (0.0, 0.0, -1.0))
            if hit is None and a[1] > 0 >= b[1]:
                k = a[1] / (a[1] - b[1])
                hit = RayHit(a, b, True, tuple(a[i] + (b[i] - a[i]) * k for i in range(3)), (0.0, 1.0, 0.0))
            out.append(hit or RayHit(a, b, False))
        return out

    def knowledge(self, type_id: int) -> dict | None:
        if type_id == 1:
            return {'name': 'Pretend Wolf', 'base_hp': 120, 'damage_taken_percent': {'fire': 130, 'standard': 100},
                    'weak_to': ['fire'], 'resists': [], 'status_buildup_needed': {'bleed': 100}, 'immune_to': [],
                    'note': 'pretend data from the demo scene'}
        return None
