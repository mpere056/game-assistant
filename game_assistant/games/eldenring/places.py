"""Where named places are, in the same coordinates as the player (for guiding: "lead me to X").

Measured 2026-10-09: in the open world the bridge publishes positions as global map coordinates,
global = position inside the map tile + 256 m x (tile X, 0, tile Z), the same as the game's own place
tables (Sites of Grace, map landmarks: area, tile, position). Legacy dungeons (Stormveil, Leyndell...)
have their own maps; the game's WORLD_MAP_LEGACY_CONV_PARAM_ST places their points on the world map.
So every grace and landmark gets a world position, and the player's position (open world or legacy
dungeon) converts into the same space. Areas 60 (the Lands Between) and 61 (Realm of Shadow) are
separate worlds: no direction between them.

Directions: north is +Z and east is +X on the world map (Liurnia lies north of Limgrave, Caelid east).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .knowledge import TAGGED, _matches, _norm, _rank

OPEN_WORLDS = (60, 61)
TILE = 256.0


@dataclass
class Place:
    name: str
    region: str | None
    kind: str                     # 'site of grace' or 'landmark'
    world: tuple[int, float, float, float] | None  # (60 or 61, x, y, z) on the world map, or None


def _global(area: int, gx: int, gz: int, pos) -> tuple[int, float, float, float]:
    return (area, gx * TILE + pos[0], pos[1], gz * TILE + pos[2])


class PlaceIndex:
    def __init__(self, graces: dict, points: dict, conversions: list[dict], names_graces: dict, names_points: dict):
        self.conv = conversions
        self.places: list[Place] = []
        for rid, g in graces.items():
            t = names_graces.get(rid)
            if not t:
                continue
            m = TAGGED.match(t)
            name, region = (m.group('what'), m.group('where')) if m else (t, None)
            self.places.append(Place(name, region, 'site of grace', self.to_world(g['area'], g['grid_x'], g['grid_z'], g['pos'])))
        for rid, p in points.items():
            t = names_points.get(rid)
            if not t:
                continue
            if 'Guidance' in t.split(' - ')[0]:
                continue  # rows for the guiding lights (grace, starlight), naming several places at once
            region, _, name = t.partition(' - ')
            if not name:
                name, region = t, None
            self.places.append(Place(name, region, 'landmark', self.to_world(p['area'], p['grid_x'], p['grid_z'], p['pos'])))

    def to_world(self, area: int, gx: int, gz: int, pos) -> tuple[int, float, float, float] | None:
        if area in OPEN_WORLDS:
            return _global(area, gx, gz, pos)
        for c in self.conv:  # a legacy dungeon: shift by the game's own conversion onto the world map
            if c['src'] == (area, gx, gz) and c['dst'][0] in OPEN_WORLDS:
                d = _global(c['dst'][0], c['dst'][1], c['dst'][2], c['dst_pos'])
                return (d[0], d[1] + pos[0] - c['src_pos'][0], d[2] + pos[1] - c['src_pos'][1], d[3] + pos[2] - c['src_pos'][2])
        return None

    def player_world(self, zone: int, pos) -> tuple[int, float, float, float] | None:
        """The player's world-map position, from the bridge's zone and published position."""
        area = zone >> 24
        if area in OPEN_WORLDS:
            return (area, pos[0], pos[1], pos[2])
        return self.to_world(area, (zone >> 16) & 0xFF, (zone >> 8) & 0xFF, pos)

    def find(self, query: str, limit: int = 5) -> list[Place]:
        hits = [p for p in self.places if _matches(query, p.name) or _matches(query, f'{p.region} {p.name}')]
        uniq = {}
        for p in sorted(hits, key=lambda p: (_rank(query, p.name), p.kind != 'site of grace')):
            uniq.setdefault((_norm(p.name), p.kind), p)
        return list(uniq.values())[:limit]


def compass(dx: float, dz: float) -> str:
    names = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west']
    return names[round(math.degrees(math.atan2(dx, dz)) / 45) % 8]


def place_words(text: str) -> list[str]:
    """Candidate place names inside a location text like "Gael Tunnel - Magma Wyrm"."""
    return [p.strip() for p in re.split(r'\s+-\s+|/|,', text) if p.strip()]
