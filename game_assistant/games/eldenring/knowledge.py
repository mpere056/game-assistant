"""Elden Ring knowledge from community name lists (Paramdex), searched locally.

The lists live in .local/eldenring/paramdex/ (downloaded by Get-GameData.bat, never committed: the
Paramdex project has no licence). Each line is "<param row id> <name>"; for item lots the name
says where the item is, for example "[Gael Tunnel - Magma Wyrm] Moonveil". Exact numbers (stats,
resistances) come from the running game instead; see params.py.

Source: https://github.com/soulsmods/Paramdex, folder ER/Names.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / '.local' / 'eldenring' / 'paramdex'
SOURCE = 'https://raw.githubusercontent.com/soulsmods/Paramdex/master/ER/Names/'
FILES = ['NpcParam', 'BonfireWarpParam', 'ItemLotParam_map', 'ItemLotParam_enemy', 'EquipParamWeapon',
         'EquipParamProtector', 'EquipParamAccessory', 'EquipParamGoods', 'EquipParamGem', 'Magic',
         'ShopLineupParam', 'WorldMapPointParam', 'AtkParam_Npc', 'MapDefaultInfoParam']
ITEM_FILES = {'EquipParamWeapon': 'weapon', 'EquipParamProtector': 'armour', 'EquipParamAccessory': 'talisman',
              'EquipParamGoods': 'item', 'EquipParamGem': 'ash of war', 'Magic': 'spell'}
TAGGED = re.compile(r'^\[(?P<where>[^\]]*)\]\s*(?P<what>.*)$')


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def _load(name: str) -> dict[int, str]:
    p = DATA / f'{name}.txt'
    out: dict[int, str] = {}
    if not p.exists():
        return out
    for line in p.read_text('utf-8', 'replace').splitlines():
        rid, _, text = line.strip().partition(' ')
        if rid.lstrip('-').isdigit() and text.strip():
            out[int(rid)] = text.strip()
    return out


def _matches(query: str, text: str) -> bool:
    words = _norm(query).split()
    t = _norm(text)
    return bool(words) and all(w in t for w in words)


def _rank(query: str, text: str) -> tuple:
    q, t = _norm(query), _norm(text)
    return (t != q, not t.startswith(q), len(t))


class EldenRingKnowledge:
    def __init__(self) -> None:
        self.lists = {name: _load(name) for name in FILES}
        self.graces: dict[int, dict] = {}  # from the running game (params.read_graces), if available

    def set_graces(self, graces: dict[int, dict]) -> None:
        self.graces = graces

    def tile_place(self, area: int, gx: int, gz: int) -> str:
        """A readable place for an open-world map tile, from the graces in or next to it."""
        names = self.lists['BonfireWarpParam']
        best: list[tuple[int, str]] = []
        for rid, g in self.graces.items():
            if g['area'] == area and rid in names:
                d = max(abs(g['grid_x'] - gx), abs(g['grid_z'] - gz))
                if d <= 1:
                    best.append((d, names[rid]))
        world = 'Realm of Shadow' if area == 61 else 'the Lands Between'
        if not best:
            return f'open world ({world}, map tile {gx},{gz})'
        best.sort()
        near = []
        for d, t in best:
            m = TAGGED.match(t)
            label = f"{m.group('what')} ({m.group('where')})" if m else t
            if label not in near:
                near.append(label)
        where = 'near' if best[0][0] == 0 else 'not far from'
        return f"open world, {where} the Site of Grace {near[0]}" + (f', also close to {", ".join(near[1:3])}' if len(near) > 1 else '')

    def _lot_place(self, lot_id: int) -> str | None:
        s = str(lot_id)
        if len(s) == 10 and s[0] in '12' and s[1] == '0':
            return self.tile_place(60 if s[0] == '1' else 61, int(s[2:4]), int(s[4:6]))
        return None

    @property
    def available(self) -> bool:
        return bool(self.lists['NpcParam'])

    def npc_name(self, type_id: int) -> str | None:
        return self.lists['NpcParam'].get(type_id)

    # ---- searches (each returns short, exact facts for the model) ----

    def find_enemy(self, query: str, limit: int = 5) -> list[dict]:
        groups: dict[str, list[int]] = defaultdict(list)
        for rid, name in self.lists['NpcParam'].items():
            if _matches(query, name):
                groups[name].append(rid)
        out = []
        for name in sorted(groups, key=lambda n: _rank(query, n))[:limit]:
            base = re.sub(r'\s*\(.*\)$', '', name)
            first = _norm(base.split(',')[0])  # attack lists say "[Malenia]" for "Malenia, Blade of Miquella"
            attacks = sorted({m.group('what') for t in self.lists['AtkParam_Npc'].values()
                              if (m := TAGGED.match(t)) and m.group('what')
                              and _norm(m.group('where')) in (_norm(base), first)})
            out.append({'name': name, 'type_ids': sorted(groups[name])[:6], 'variants': len(groups[name]),
                        'named_attacks_in_game_data': attacks[:20]})
        return out

    def find_item(self, query: str, limit: int = 5) -> list[dict]:
        items = []
        for fname, kind in ITEM_FILES.items():
            for rid, name in self.lists[fname].items():
                if _matches(query, name):
                    items.append((name, kind))
        seen, out = set(), []
        for name, kind in sorted(items, key=lambda x: _rank(query, x[0])):
            if name in seen:
                continue
            seen.add(name)
            out.append({'name': name, 'kind': kind, **self.where_is(name)})
            if len(out) >= limit:
                break
        return out

    def where_is(self, item_name: str, limit: int = 12) -> dict:
        """Where an item (exact name) is picked up, dropped or sold, from the lot and shop lists."""
        want = _norm(item_name)

        def places(fname):
            found = []
            for rid, t in self.lists[fname].items():
                m = TAGGED.match(t)
                if m and _norm(m.group('what')) == want:
                    where = m.group('where')
                    where = re.sub(r'^LD - ', 'legacy dungeon: ', where)
                    where = where.replace('Corpse - Unknown', 'on a corpse (exact spot not recorded)')
                    found.append(where)
                elif not m and fname == 'ItemLotParam_map' and _norm(t) == want:
                    place = self._lot_place(rid)  # open-world pickups carry only their map tile
                    if place:
                        found.append(place)
            found = sorted(set(found), key=lambda w: (w.startswith('open world ('), w))  # named places first
            return found[:limit] + ([f'... and {len(found) - limit} more'] if len(found) > limit else [])

        return {'found_in_world': places('ItemLotParam_map'), 'dropped_by': places('ItemLotParam_enemy'),
                'sold_by': places('ShopLineupParam')}

    def find_place(self, query: str, limit: int = 8) -> list[dict]:
        out = []
        for rid, t in self.lists['BonfireWarpParam'].items():
            m = TAGGED.match(t)
            name, region = (m.group('what'), m.group('where')) if m else (t, None)
            if _matches(query, t):
                out.append({'name': name, 'region': region, 'kind': 'site of grace'})
        for rid, t in self.lists['WorldMapPointParam'].items():
            region, _, name = t.partition(' - ')
            if name and _matches(query, t):
                out.append({'name': name, 'region': region, 'kind': 'map location'})
        uniq = {(o['name'], o['kind']): o for o in out}
        return sorted(uniq.values(), key=lambda o: _rank(query, o['name']))[:limit]

    def search(self, kind: str, query: str) -> dict:
        if not self.available:
            return {'error': 'the Elden Ring name lists are not installed: run Get-GameData.bat'}
        fn = {'enemy': self.find_enemy, 'item': self.find_item, 'place': self.find_place}.get(kind)
        if fn is None:
            return {'error': f'unknown kind {kind}; use enemy, item or place'}
        results = fn(query)
        corrected = None
        if not results:  # a near-miss spelling (often from speech): try the closest known name
            corrected = self.closest_name(kind, query)
            if corrected:
                results = fn(corrected)
        return {'kind': kind, 'query': query, 'results': results,
                'source': 'community name lists (Paramdex)' if results else 'nothing matched; try other words',
                **({'searched_instead': corrected} if corrected else {})}

    def closest_name(self, kind: str, query: str) -> str | None:
        import difflib
        if kind == 'enemy':
            names = set(self.lists['NpcParam'].values())
        elif kind == 'place':
            names = {TAGGED.sub(r'\g<what>', t) for t in self.lists['BonfireWarpParam'].values()}
            names |= {t.partition(' - ')[2] for t in self.lists['WorldMapPointParam'].values()}
        else:
            names = {n for f in ITEM_FILES for n in self.lists[f].values()}
        by_norm = {_norm(n): n for n in names if n}
        q = _norm(query)
        best = difflib.get_close_matches(q, list(by_norm), n=1, cutoff=0.75)
        if not best:  # also try matching the query against the start of longer names
            best = difflib.get_close_matches(q, [k[:len(q) + 2] for k in by_norm], n=1, cutoff=0.8)
            best = [next(k for k in by_norm if k.startswith(best[0]))] if best else []
        return by_norm[best[0]] if best else None
