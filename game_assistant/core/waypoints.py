"""Places the player names to come back to ("remember this as home", "take me home"), as Baritone's waypoints:
kept on disk per game (.local/<game>/waypoints.json, never in the repository), with where in the game's own
coordinates and which world part (a Minecraft dimension). The game's own marks (where the player last died)
are listed alongside, never stored.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class Waypoints:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._marks: dict | None = None

    def _load(self) -> dict:
        if self._marks is None:
            try:
                self._marks = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self._marks = {}
        return self._marks

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._marks, indent=1), encoding='utf-8')
        except OSError:
            pass

    @staticmethod
    def _key(name: str) -> str:
        return ' '.join(str(name or '').lower().replace('_', ' ').split())

    def set(self, name: str, pos, where: str | None = None) -> dict:
        key = self._key(name)
        if not key:
            raise ValueError('a waypoint needs a name')
        mark = {'name': name.strip(), 'x': round(pos[0]), 'y': round(pos[1]), 'z': round(pos[2]),
                'where': where, 'set': time.strftime('%Y-%m-%d %H:%M')}
        self._load()[key] = mark
        self._save()
        return mark

    def get(self, name: str) -> dict | None:
        marks = self._load()
        key = self._key(name)
        if key in marks:
            return marks[key]
        near = [m for k, m in marks.items() if key and (key in k or k in key)]
        return near[0] if len(near) == 1 else None

    def delete(self, name: str) -> bool:
        key = self._key(name)
        if key in self._load():
            del self._marks[key]
            self._save()
            return True
        return False

    def all(self) -> list:
        return list(self._load().values())
