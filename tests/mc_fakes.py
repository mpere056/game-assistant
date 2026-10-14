"""Stand-ins for the Minecraft bridge in tests."""
import math


def fake_sight(solid, plants=None):
    """MinecraftAdapter._sight over a made-up world: solid(cell) -> bool; plants: {cell: replaceable} (no
    collision, but they stop the crosshair, as the game's outlines do). Rays in the game's coordinates."""
    plants = plants or {}

    def sight(rays):
        out = []
        for s, e in rays:
            hit = None
            prev = None
            for i in range(1, 401):
                q = tuple(s[j] + (e[j] - s[j]) * i / 400 for j in range(3))
                c = tuple(math.floor(v) for v in q)
                if solid(c) or c in plants:
                    n = tuple(prev[j] - c[j] for j in range(3)) if prev else (0, 0, 0)
                    hit = {'at': q, 'normal': n, 'block': c, 'id': None, 'replaceable': plants.get(c, False),
                           'solid': solid(c)}
                    break
                prev = c
            out.append(hit)
        return out
    return sight
