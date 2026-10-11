"""F10 commands: turn "go to the nearest site of grace" into one action for the character (phase 4b).

Order of attempts, fastest first:
1. An answer to Navi's last question ("The Gatefront grace or the Agheel Lake one?"), for ASK_SECONDS.
2. Stop ("stop", "never mind, stop", "ok wait").
3. "the nearest/closest X" anywhere in the order: X a kind of place (grace, landmark), else a character
   around the player by name ("Godrick soldier") or kind ("enemy").
4. "that", "there", "it": what the player is looking at.
5. The target after "go / walk / take me ... to": first characters around the player whose name matches
   (forgiving: speech recognition heard "gondric" for "Godrick"), then places by name. A place is never
   reached through an item ("Godrick Soldier" once matched "Godrick Soldier Ashes" -> Warmaster's Shack).
6. Otherwise the model (`ask_model`, Haiku with a small command prompt), if given.

Ambiguity becomes a question from Navi instead of a guess: two places of that kind about equally
near (the second within NEAR_TIE of the first), or several different places matching a name.
Filler ("please", "in front of me", "I mean", "my character") is ignored.
"""
from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import lookat
from .commands import normalize
from .interface import GameAdapter, GameNotRunning, Vec3

ASK_SECONDS = 10.0
NEAR_TIE = 1.15     # the second nearest within 15 % of the nearest: ask which
CHARACTER_RANGE = 150.0  # metres: characters this near can be walked to by name
NAME_MATCH = 0.72   # how alike a heard word and a name word must be (difflib ratio)

_STOP = re.compile(r"\b(stop|halt|cancel|never ?mind|wait)\b", re.I)  # in a short order (STOP_WORDS words)
STOP_WORDS = 5
_NEAREST = re.compile(r"\b(nearest|closest|next|near(?:est)?by)\s+(?P<what>[a-z' -]+)", re.I)
_TARGET = re.compile(r"\b(go|walk|run|head|move|take|lead|bring|get|travel|guide)\b.*?\b(to|towards?|for)\s+"
                     r"(?P<target>.+)$", re.I)
_ATTACK = re.compile(r"\b(attack|hit|strike|fight|kill|smack|slash|stab|whack|engage)\b\s*(?P<target>.*)$", re.I)
_PRONOUN = re.compile(r"^(it|him|her|them|that|this|that one|this one|that guy|this guy)?$", re.I)
_THAT = re.compile(r"^(that|there|it|this|that one|that place|over there|right there|this way)$", re.I)
_FILLER = re.compile(r"\b(please|in front of me|ahead of me|in front|ahead|near me|nearby|around here|"
                     r"right there|over there|for me|thanks|thank you|i mean|my character|you see|that is)\b", re.I)
_ARTICLES = {'the', 'a', 'an', 'one', 'some', 'that', 'this', 'those', 'these'}
_GENERIC_ENEMY = {'enemy', 'enemies', 'monster', 'monsters', 'foe', 'foes', 'hostile', 'baddie', 'mob'}
_PLACE_WORDS = re.compile(r'grace|landmark|ruin|cave|church|castle|tower|place|shack|camp|fort|catacomb|tunnel'
                          r'|bridge|lake|village|manor|evergaol|gate', re.I)
KINDS = {'grace': 'site of grace', 'site of grace': 'site of grace', 'sites of grace': 'site of grace',
         'bonfire': 'site of grace', 'checkpoint': 'site of grace', 'landmark': 'landmark', 'place': None}


@dataclass
class WalkAction:
    kind: str                     # 'walk', 'stop', 'ask', 'none'
    say: str                      # Navi's short reply (spoken)
    point: Vec3 | None = None     # where to walk (snapshot coordinates)
    name: str | None = None
    entity_id: int | None = None  # walking to a character: follow it if it moves
    attack: bool = False          # ...and land one hit on it
    options: list = field(default_factory=list)  # for 'ask': [(name, point)]


def _clean(text: str) -> str:
    t = _FILLER.sub(' ', text.lower())
    t = re.sub(r"[^a-z0-9' -]", ' ', t)
    words = [w for w in t.split() if w not in _ARTICLES]
    return ' '.join(words).strip()


def _word_like(heard: str, name_word: str) -> bool:
    h, n = heard.rstrip('s'), name_word.rstrip('s')
    return h == n or n.startswith(h) or difflib.SequenceMatcher(None, h, n).ratio() >= NAME_MATCH


class CommandResolver:
    def __init__(self, adapter: GameAdapter, ask_model: Callable[[str], WalkAction | None] | None = None):
        self.adapter = adapter
        self.ask_model = ask_model
        self.pending: tuple[float, list] | None = None   # (asked at, options)

    def handle(self, text: str) -> WalkAction:
        try:
            return self._handle(text)
        except GameNotRunning:
            return WalkAction('none', "I can't see the game right now.")

    def _handle(self, text: str) -> WalkAction:
        t = normalize(text).strip().rstrip('?')
        if self.pending and time.monotonic() - self.pending[0] < ASK_SECONDS:
            choice = self._choose(t, self.pending[1])
            if choice is not None:
                self.pending = None
                return self._walk(*choice)
        self.pending = None
        if _STOP.search(t) and len(t.split()) <= STOP_WORDS:
            return WalkAction('stop', 'Stopping.')
        m = _ATTACK.search(t)
        if m:
            return self._attack(m.group('target'))
        m = _NEAREST.search(t)
        if m:
            what = _clean(m.group('what'))
            if what in KINDS:
                return self._nearest(KINDS[what] or 'site of grace')
            got = self.character(what)
            if got is not None:
                return got
            if _PLACE_WORDS.search(what):
                return self._nearest('site of grace' if 'grace' in what else 'landmark')
            return WalkAction('none', f"I don't see any {what or 'of those'} near you.")
        m = _TARGET.search(t)
        target = _clean(m.group('target')) if m else ''
        if m and (_THAT.match(target) or not target):
            return self._crosshair()
        if target:
            got = self.character(target) or self._named(target)
            if got is not None:
                return got
        if self.ask_model is not None:
            got = self.ask_model(text)
            if got is not None:
                if got.kind == 'ask' and got.options:
                    self.pending = (time.monotonic(), got.options)
                return got
        return WalkAction('none', "I didn't catch where to go.")

    # ---- the kinds of order ----

    def nearest(self, kind: str) -> WalkAction:
        return self._nearest(kind)

    def _nearest(self, kind: str) -> WalkAction:
        res = [r for r in self.adapter.nearest_places(kind, limit=3).get('results', []) if r.get('position')]
        if not res:
            return WalkAction('none', f"I don't know where the nearest {kind} is.")
        a = res[0]
        if len(res) > 1 and res[1]['distance_m'] <= a['distance_m'] * NEAR_TIE and res[1]['name'] != a['name']:
            return self._ask([(a['name'], a['position']), (res[1]['name'], res[1]['position'])])
        return self._walk(a['name'], a['position'])

    def character(self, what: str) -> WalkAction | None:
        """The nearest living character within CHARACTER_RANGE whose name matches the words (forgiving),
        or any hostile one for 'enemy'. None when nothing around matches."""
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player:
            return None
        words = set(re.findall(r"[a-z0-9']+", _clean(what)))
        generic = bool(words & _GENERIC_ENEMY)
        words -= _GENERIC_ENEMY
        if not words and not generic:
            return None
        p = snap.player.pos
        best = None
        for e in snap.entities:
            if e.dead or (generic and not e.hostile):
                continue
            d = ((e.pos[0] - p[0]) ** 2 + (e.pos[2] - p[2]) ** 2) ** 0.5
            if d > CHARACTER_RANGE:
                continue
            name = e.name
            if not name and e.type_id is not None and hasattr(self.adapter, 'knowledge'):
                try:
                    name = (self.adapter.knowledge(e.type_id) or {}).get('name')
                except Exception:
                    name = None
            name_words = re.findall(r"[a-z0-9']+", (name or '').lower())
            if words and not all(any(_word_like(w, n) for n in name_words) for w in words):
                continue
            if best is None or d < best[0]:
                best = (d, e, name)
        if best is None:
            return None
        d, e, name = best
        label = name or 'that enemy'
        return WalkAction('walk', f"There's a {label} {round(d)} metres away. Let's go!", tuple(e.pos), label, e.id)

    def _attack(self, target: str) -> WalkAction:
        """'attack the nearest Godrick soldier', 'hit that enemy', 'attack it': walk up and land one hit.
        A pronoun means what the player is looking at, else the nearest enemy."""
        words = _clean(re.sub(r"\b(nearest|closest|next)\b", ' ', target))
        got = None
        if _PRONOUN.match(words):
            snap = self.adapter.snapshot()
            r = lookat.resolve(snap, self.adapter.raycast) if snap.in_world and snap.camera else None
            if r is not None and r.kind in ('entity', 'target') and r.best and not r.best.entity.dead:
                e = r.best.entity
                got = WalkAction('walk', '', tuple(e.pos), e.name or 'that enemy', e.id)
            words = 'enemy'
        got = got or self.character(words or 'enemy')
        if got is None:
            return WalkAction('none', f"I don't see any {words or 'enemy'} to attack.")
        got.attack = True
        got.say = f"Attacking the {got.name}!" if got.name and got.name != 'that enemy' else 'Attacking!'
        return got

    def _crosshair(self) -> WalkAction:
        snap = self.adapter.snapshot()
        r = lookat.resolve(snap, self.adapter.raycast)
        if r.kind in ('entity', 'target') and r.best:
            e = r.best.entity
            return WalkAction('walk', 'On my way!', tuple(e.pos), e.name, e.id)
        if r.kind == 'surface' and r.surface and r.surface.pos:
            return self._walk(None, r.surface.pos)
        return WalkAction('none', "I can't tell where you mean.")

    def _named(self, name: str) -> WalkAction | None:
        got = self.adapter.locate(name)
        if got.get('found_via_item'):  # an item's location is not a place to walk to by name
            return None
        res = [r for r in got.get('results', []) if r.get('position')]
        if not res:
            return None
        names = []
        for r in res:
            if r['name'] not in [n for n, _ in names]:
                names.append((r['name'], r['position']))
        exact = [x for x in names if x[0].lower() == name.lower().strip()]
        if exact or len(names) == 1:
            return self._walk(*(exact or names)[0])
        return self._ask(names[:2])

    # ---- helpers ----

    def _walk(self, name: str | None, point) -> WalkAction:
        return WalkAction('walk', f"Let's go to {name}!" if name else "On my way!", tuple(point), name)

    def _ask(self, options: list) -> WalkAction:
        self.pending = (time.monotonic(), options)
        q = ' or '.join(f'the {n}' if not n.lower().startswith('the ') else n for n, _ in options)
        return WalkAction('ask', f'{q[0].upper()}{q[1:]}?', options=options)

    @staticmethod
    def _choose(text: str, options: list):
        t = text.lower()
        if re.search(r"\b(first|former|nearest|closest|that one|the one|yes|yeah)\b", t):
            return options[0]
        if re.search(r"\b(second|latter|other|last)\b", t):
            return options[-1]
        words = set(re.findall(r'\w+', t)) - {'the', 'a', 'one', 'to', 'go', 'please'}
        best, score = None, 0
        for name, point in options:
            s = len(words & set(re.findall(r'\w+', name.lower())))
            if s > score:
                best, score = (name, point), s
        return best
