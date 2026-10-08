"""One adapter per game. Add a game by writing its adapter and listing it here."""
from __future__ import annotations

from importlib import import_module

from ..core.interface import GameAdapter

# name -> "module:class"
ADAPTERS = {
    'eldenring': 'game_assistant.games.eldenring.adapter:EldenRingAdapter',
}


def adapter_for(name: str) -> GameAdapter:
    if name not in ADAPTERS:
        raise KeyError(f'No adapter for "{name}". Known games: {", ".join(sorted(ADAPTERS))}')
    module, cls = ADAPTERS[name].split(':')
    return getattr(import_module(module), cls)()
