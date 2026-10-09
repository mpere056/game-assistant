"""Downloads the community name lists a game's knowledge needs into .local/ (never committed).

    Get-GameData.bat [game]      (default eldenring)

Elden Ring: Paramdex ER/Names lists (https://github.com/soulsmods/Paramdex), about 1.4 MB.
"""
from __future__ import annotations

import sys
import urllib.request

from ..games.eldenring import knowledge as er


def get_eldenring() -> int:
    er.DATA.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in er.FILES:
        url = f'{er.SOURCE}{name}.txt'
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            (er.DATA / f'{name}.txt').write_bytes(data)
            print(f'  {name}.txt  {len(data):,} bytes')
        except OSError as e:
            failed += 1
            print(f'  {name}.txt  FAILED: {e}')
    print(f'Saved to {er.DATA}' + (f' ({failed} failed)' if failed else ''))
    return 1 if failed else 0


GAMES = {'eldenring': get_eldenring}


def main() -> int:
    names = [x for x in sys.argv[1:] if not x.startswith('-')]
    game = names[0] if names else 'eldenring'
    if game not in GAMES:
        sys.exit(f'No data to get for "{game}". Known games: {", ".join(GAMES)}')
    print(f'Getting game data for {game}:')
    return GAMES[game]()


if __name__ == '__main__':
    sys.exit(main())
