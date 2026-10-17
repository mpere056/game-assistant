"""Minecraft's own data, read from the game installed on this computer (as the Elden Ring adapter has its game's
data in .local/eldenring/): recipes, loot tables (what each block and creature drops, what shearing gives), tags,
structures (village houses...), advancements and the English names. Extracted by `Get-GameData.bat minecraft`
from the installed client jar (read only) into .local/minecraft/<version>/ (never committed: AGENTS.md).

Read from it: everything the world gives besides mining, by its loot tables (Drop): what creatures drop killed
(string from spiders: "make me a bed" had no plan, the wool coming only from creatures, 2026-10-16), and what's
got by using something on a creature or a block (shearing a sheep, brushing an armadillo, carving a pumpkin,
harvesting berries, tilling), with how many on average and which kind of it (a sheep's colour, not shorn). The
loot tables say what and from what; the verb (their folder: 'shearing', 'brush', 'carve', 'harvest', 'till')
says how, and the adapter knows each verb's item (shears, a brush, a hoe): the game's words, not each thing's.
"""
from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / '.local' / 'minecraft'
GAME_VERSION = '26.3'          # the version the bridge is built for (bridges/minecraft/gradle.properties)
WANT = ('data/', 'assets/minecraft/lang/en_us.json')


def game_dir() -> Path:
    return Path(os.environ.get('APPDATA', '')) / '.minecraft'


def extract(version: str = GAME_VERSION) -> dict:
    """The game's data out of its installed client jar into DIR/version (the jar is only read). {'files', 'bytes'}."""
    jar = game_dir() / 'versions' / version / f'{version}.jar'
    if not jar.exists():
        raise FileNotFoundError(f'no Minecraft {version} installed ({jar})')
    out = DIR / version
    n = size = 0
    with zipfile.ZipFile(jar) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(WANT):
                continue
            target = out / info.filename
            if '..' in Path(info.filename).parts:
                continue                                  # (never outside the folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            n, size = n + 1, size + info.file_size
    return {'files': n, 'bytes': size, 'to': str(out)}


@dataclass(frozen=True)
class Drop:
    """Something the world gives besides mining: from what, how, how many on average, which kind of it."""
    target: str                # the creature's type id ('minecraft:sheep'), or the block's ('sweet_berry_bush')
    on: str                    # 'creature' or 'block'
    verb: str                  # 'kill' (a creature's drops), or the loot table's folder: 'shearing', 'brush'...
    per: float                 # how many on average, each time
    variant: tuple = ()        # ((property, value), ...): only that kind ('color', 'white')


LEARNED = 'learned.json'   # ways learned beyond the game's data (the wiki): .local/minecraft/learned.json


def learned() -> list:
    """Ways to get things the game's data doesn't have (in its code: milk from a cow with a bucket), learned from
    the community's wiki by the doer: [{'item', 'target', 'on', 'using', 'per'}]."""
    try:
        got = json.loads((DIR / LEARNED).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [w for w in got if isinstance(w, dict) and w.get('item') and w.get('target')]


def learn(way: dict) -> list:
    """One more way (the same item from the same thing: replaced). The whole list after."""
    ways = [w for w in learned() if (w['item'], w['target']) != (way['item'], way['target'])] + [way]
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / LEARNED).write_text(json.dumps(ways, indent=1), encoding='utf-8')
    return ways


# loot table types: what they're got from (the rest, a block mined, a chest, fishing, bartering: not these)
TYPES = {'minecraft:entity': 'creature', 'minecraft:shearing': 'creature', 'minecraft:entity_interact': 'creature',
         'minecraft:block_interact': 'block'}


@dataclass
class GameData:
    version: str = GAME_VERSION
    _drops: dict | None = field(default=None, repr=False)

    @property
    def dir(self) -> Path:
        return DIR / self.version / 'data' / 'minecraft'

    def available(self) -> bool:
        return (self.dir / 'loot_table').is_dir()

    def _table(self, name: str):
        """A loot table by name ('entities/sheep', or 'minecraft:entities/sheep/white')."""
        name = name.split(':', 1)[-1]
        p = self.dir / 'loot_table' / f'{name}.json'
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def drops(self) -> dict:
        """{item: [Drop]}: everything the loot tables say creatures and blocks give besides mining: a creature
        killed, or something used on a creature or a block (each table at the top of its folder: what it's from is
        its name, how is the folder; tables below are kinds of it, reached from those)."""
        if self._drops is not None:
            return self._drops
        out: dict = {}
        if self.available():
            for folder in sorted(p for p in (self.dir / 'loot_table').iterdir() if p.is_dir()):
                for p in sorted(folder.glob('*.json')):
                    t = self._table(f'{folder.name}/{p.stem}')
                    on = TYPES.get((t or {}).get('type'))
                    if on is None:
                        continue
                    verb = 'kill' if folder.name == 'entities' else folder.name
                    target = _target(t) or 'minecraft:' + p.stem
                    for item, per, variant in _items(self, t, 1.0, (), 0):
                        if per > 0:
                            out.setdefault(item, []).append(Drop(target, on, verb, round(per, 3), variant))
        self._drops = out
        return out


def _target(table) -> str | None:
    """The block a table's condition names (match_block: 'minecraft:sweet_berry_bush'), if any."""
    for pool in (table or {}).get('pools') or []:
        for e in pool.get('entries') or []:
            for c in _conds(e) + _conds(pool):
                if str(c.get('type', '')).endswith('match_block') and isinstance(c.get('blocks'), str):
                    return c['blocks']
    return None


def _conds(e) -> list:
    c = e.get('conditions') or e.get('condition') or []
    return c if isinstance(c, list) else [c]


def _mods(e) -> list:
    m = e.get('functions') or e.get('modifier') or []
    return m if isinstance(m, list) else [m]


def _mean(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        if v.get('type', '').endswith('uniform') or ('min' in v and 'max' in v):
            lo, hi = float(_mean(v.get('min', 0))), float(_mean(v.get('max', 0)))
            if lo < 0 <= hi:                              # (0 or more: what's below 0 counts as none)
                return hi * hi / (2 * (hi - lo)) if hi > lo else 0.0
            return (lo + hi) / 2
        if 'n' in v and 'p' in v:                         # binomial
            return float(v['n']) * float(v['p'])
        if 'value' in v:
            return _mean(v['value'])
    return 1.0


def _chance_and_variant(conds) -> tuple[float, tuple]:
    """(the chance the conditions are met, the kind of creature they need). Conditions on the creature beyond its
    kind (riding a horse, a baby on a chicken, on fire) count as never: special cases, not a way to get it."""
    chance, variant = 1.0, []
    for c in conds:
        kind = str(c.get('type', c.get('condition', ''))).split(':')[-1]
        if kind == 'all_of':
            ch, v = _chance_and_variant(c.get('terms') or [])
            chance, variant = chance * ch, variant + list(v)
        elif kind == 'any_of':
            opts = [_chance_and_variant([t]) for t in c.get('terms') or []]
            if opts:
                ch, v = max(opts, key=lambda o: o[0])
                chance, variant = chance * ch, variant + list(v)
        elif kind == 'random_chance':
            chance *= float(_mean(c.get('chance', 1.0)))
        elif kind == 'random_chance_with_enchanted_bonus':
            chance *= float(_mean(c.get('unenchanted_chance', 0.0)))
        elif kind == 'entity_properties' and c.get('entity') == 'this':
            pred = c.get('predicate') or {}
            if any(k != 'minecraft:components' and not k.startswith('minecraft:type_specific') for k in pred):
                chance = 0.0
            for k, v in (pred.get('minecraft:components') or {}).items():
                variant.append((k.split('/')[-1], v))
            for spec in [v for k, v in pred.items() if k.startswith('minecraft:type_specific')]:
                if isinstance(spec, dict):
                    variant.extend((k, v) for k, v in spec.items() if not isinstance(v, (dict, list)))
        # (the rest: killed by the player, the weapon, the damage: taken as met, a rough reckoning)
    return chance, tuple(variant)


def _items(data: GameData, table, chance: float, variant: tuple, depth: int):
    """(item, how many on average, the kind of creature) for every item a table can give."""
    if not table or depth > 4:
        return
    for pool in table.get('pools') or []:
        got = _chance_and_variant(_conds(pool))
        rolls = _mean(pool.get('rolls', 1))
        entries = pool.get('entries') or []
        yield from _entries(data, entries, chance * got[0] * rolls, variant + got[1], depth, share=True)


def _entries(data, entries, chance, variant, depth, share: bool):
    """share: one of them is picked, by weight (a pool's entries: iron, a carrot or a potato); else each has its
    own conditions (alternatives) or they all come (a group)."""
    total = sum(float(e.get('weight', 1)) for e in entries) if share else 1.0
    for e in entries:
        c, v = _chance_and_variant(_conds(e))
        c *= chance * (float(e.get('weight', 1)) / total if share and total else 1.0)
        v = variant + v
        kind = str(e.get('type', '')).split(':')[-1]
        if kind == 'item' and e.get('name'):
            n = 1.0
            for m in _mods(e):
                if str(m.get('function', m.get('type', ''))).split(':')[-1] == 'set_count':
                    n = _mean(m.get('count', 1))
            yield e['name'], c * n, v
        elif kind == 'loot_table' and isinstance(e.get('value', e.get('name')), str):
            yield from _items(data, data._table(e.get('value', e.get('name'))), c, v, depth + 1)
        elif kind in ('alternatives', 'group', 'sequence'):
            yield from _entries(data, e.get('children') or [], c, v, depth, share=False)
