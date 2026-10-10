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
    from ..games.eldenring import wiki
    try:  # the offline wiki (Fandom database dump, CC BY-SA), then its search index
        wiki.DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(wiki.DUMP_URL, timeout=120) as r:
            wiki.DUMP_7Z.write_bytes(r.read())
        stats = wiki.build()
        print(f"  wiki: {stats['articles']:,} articles, {stats['aliases']:,} other names -> {wiki.DB}")
    except Exception as e:
        failed += 1
        print(f'  wiki FAILED: {e}')
    return 1 if failed else 0


def get_voice() -> int:
    """The fairy's neural voice (Kokoro int8 model and voices, about 120 MB) into .local/voice/."""
    from ..voice import fairy_voice as fv
    fv.MODEL.parent.mkdir(parents=True, exist_ok=True)
    failed = 0
    for target in (fv.MODEL, fv.VOICES):
        if target.exists():
            print(f'  {target.name}  already there')
            continue
        try:
            with urllib.request.urlopen(fv.SOURCE + target.name, timeout=120) as r:
                data = r.read()
            target.write_bytes(data)
            print(f'  {target.name}  {len(data):,} bytes')
        except OSError as e:
            failed += 1
            print(f'  {target.name}  FAILED: {e}')
    return 1 if failed else 0


def get_navmesh() -> int:
    """Elden Ring's own navmeshes from the installed game (read-only), for walking routes: about
    20 minutes once, 230 MB in .local/eldenring/navmesh/. See tools/get_navmesh.py."""
    import subprocess
    code = subprocess.call([sys.executable, '-m', 'game_assistant.tools.get_navmesh', '--workers', '3'])
    if code == 0:  # then the walkable world graph (links between blocks, drops, steps)
        code = subprocess.call([sys.executable, '-m', 'game_assistant.tools.get_navmesh', '--graph'])
    return code


GAMES = {'eldenring': get_eldenring, 'voice': get_voice, 'navmesh': get_navmesh}


def main() -> int:
    names = [x for x in sys.argv[1:] if not x.startswith('-')]
    game = names[0] if names else 'eldenring'
    if game not in GAMES:
        sys.exit(f'No data to get for "{game}". Known games: {", ".join(GAMES)}')
    print(f'Getting game data for {game}:')
    return GAMES[game]()


if __name__ == '__main__':
    sys.exit(main())
