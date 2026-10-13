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
import math
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
# ...unless another order follows it: 'wait, kill the bat actually' is that order (2026-10-12 21:36: it stopped)
_STOP_LEAD = re.compile(r"^\W*(?:(?:no|oh|okay|ok|sorry|hmm)\b[\s,.!-]*)*(?:(?:wait|stop|hold on|cancel(?: that)?|"
                        r"never ?mind|scratch that)\b[\s,.!-]*(?:(?:no|actually|instead)\b[\s,.!-]*)*)+", re.I)
# (several in a row: 'wait, stop, no, not the Iron Golem...')
STOP_WORDS = 5
_NEAREST = re.compile(r"\b(nearest|closest|next|near(?:est)?by)\s+(?P<what>[a-z' -]+)", re.I)
_TARGET = re.compile(r"\b(go|walk|run|head|move|take|lead|bring|get|travel|guide)\b.*?\b(to|towards?|for)\s+"
                     r"(?P<target>.+)$", re.I)
_ATTACK = re.compile(r"\b(attack|hit|strike|fight|kill|smack|slash|stab|whack|engage|defeat|beat|take down|"
                     r"take out|finish)\b\s*(?P<target>.*)$", re.I)
# Any attack order is a whole fight (the user: "attack that soldier" should fight it, dodging); one hit
# and hand back only when asked for: "hit it once", "land one hit".
_ONE_HIT = re.compile(r"\b(once|one hit|a hit|single hit|one blow|one strike|just one)\b", re.I)
_PRONOUN = re.compile(r"^(it|him|her|them|that|this|that one|this one|that guy|this guy)?$", re.I)
_THAT = re.compile(r"^(that|there|it|this|that one|that place|over there|right there|this way)$", re.I)
_FILLER = re.compile(r"\b(please|in front of me|ahead of me|in front|ahead|near me|nearby|around here|"
                     r"right there|over there|for me|thanks|thank you|i mean|my character|you see|that is|"
                     r"actually|instead|first|now|then|right now)\b", re.I)
_ARTICLES = {'the', 'a', 'an', 'one', 'some', 'that', 'this', 'those', 'these'}
_NOT_NAME = {'of', 'from', 'me', 'us', 'too', 'also', 'again', 'for', 'another', 'other', 'more', 'as', 'well',
             'ones', 'guy', 'guys', 'next', 'them', 'either', 'any', 'all', 'every', 'each', 'around', 'here',
             'everyone', 'everything', 'left', 'remaining', 'rest'}
# Orders from the 2026-10-11 sessions the rules missed:
_NEXT_ONE = re.compile(r"\b(next|another) one\b|\bone more\b|\bthe next\b", re.I)  # 'now the next one please'
_ALL = re.compile(r"\b(all|every|each|everyone|the rest)\b", re.I)                   # 'kill all of the soldiers'
_IN_VIEW = re.compile(r"\b(in front|ahead|this|that|there|those|these)\b", re.I)    # 'this enemy in front of me'
VIEW_ANGLE = 30.0     # degrees from the camera's forward: "the one in front of me"
VIEW_RANGE = 60.0
ALL_RANGE = 60.0      # 'kill all of them': the next one within this many metres
HEIGHT_WEIGHT = 3.0   # choosing the nearest character: a block up or down counts as this many across
SAME_LEVEL = 8.0      # 'any enemy' and 'all of them' skip characters this far above or below the player
                      # (15:22: "all of them" went on to an Eagle 35 m up a cliff; the walk got stuck)
CONTEXT_SECONDS = 60.0  # what was said this recently gives "them / it / that" its meaning
FOLLOW_UP_SECONDS = 90.0  # an order without a verb this soon after an attack order is a correction
# 'kill the boar and then kill any remaining soldiers' (15:4x): one order after another. 'and' alone only
# splits when another order follows it ('kill the boar and the soldier' is one order... for two names).
_VERBS = r"(?:kill|attack|fight|hit|strike|defeat|beat|take|go|walk|run|head|move|lead|bring|stop|after|once|when)"
_THEN = re.compile(r"\s*,?\s*\b(?:and then|and after that|after that|afterwards|then|and also|and)\b\s*,?\s*"
                   r"(?=(?:[a-z']+\s+){0,3}?" + _VERBS + r"\b)", re.I)
_AFTER_CLAUSE = re.compile(r"^(?:after|once|when)\b[^,]*?(?:,\s*|\s+(?=(?:please\s+)?" + _VERBS + r"\b))", re.I)
# Getting items (a game with recipes and gathering): 'get me a stone pickaxe', 'mine 10 cobblestone', 'craft
# some sticks'. ('get to the village', 'get me to the village' are walking. 'Draft': speech recognition's
# 'craft', 2026-10-13.)
_GET = re.compile(r"^(?:please\s+)?(?:can you\s+|could you\s+)?(?:go\s+)?(?:get|gather|collect|mine|chop|"
                  r"craft|draft|make|build|fetch|obtain|grab)\b(?:\s+(?:me|us|myself))?\s+(?!to\b|towards?\b|back\b|"
                  r"there\b|over\b|me\s+to\b)(?P<what>.+)$", re.I)
_AMOUNT = re.compile(r"^(?:(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|a couple of|a few|a stack of)|"
                     r"(?P<one>an?|one)|some|more)\s+", re.I)
ONE_OF = re.compile(r"(pickaxe|axe|shovel|sword|hoe|table|furnace|chest|bed|door|boat|bucket|shield|helmet|"
                    r"chestplate|leggings|boots|bow)s?$", re.I)
SOME = 8              # 'get me some cobblestone': this many
# Exploring: 'explore', 'explore 60 blocks north', 'go 100 blocks east', 'head west' (where the adapter can).
_EXPLORE = re.compile(r"^(?:please\s+)?(?:explore|wander|scout|go|walk|head|move|travel|run)\b(?:\s+(?:around|out|about|"
                      r"off))?(?:\s+(?:for\s+)?(?P<n>\d+)\s*(?:blocks?|metres?|meters?|m)?)?(?:\s+(?:to the\s+|towards?\s+"
                      r"(?:the\s+)?)?(?P<dir>north[- ]?east|north[- ]?west|south[- ]?east|south[- ]?west|north|south|east|"
                      r"west))?\W*$", re.I)
# Coordinates: 'go to 12 64 -30', 'take me to x 12 y 64 z -30' (the game's own, as it shows them).
_COORDS = re.compile(r"\b(?:go|walk|take me|head|move|travel|run|get)\b.*?\bto\s+(?:x\s*)?(?P<x>-?\d+(?:\.\d+)?)[\s,]+"
                     r"(?:y\s*)?(?P<y>-?\d+(?:\.\d+)?)[\s,]+(?:z\s*)?(?P<z>-?\d+(?:\.\d+)?)\W*$", re.I)
# Getting away: 'run away', 'get away from them', 'retreat' (from the hostile ones near, the way away from them).
_FLEE = re.compile(r"\b(run away|run off|flee|retreat|get away|escape|back off|get out of here)\b", re.I)
_EAT = re.compile(r"^(?:please\s+)?(?:eat|have something to eat|heal up|eat something|have a snack)\b", re.I)
FLEE_FROM = 24.0      # hostile ones this near count, running away
FLEE_TO = 28.0        # ...and the run goes this far
# A correction of the last walk order: 'no, not the Iron Golem, but like iron blocks' (2026-10-13: it stopped)
_CORRECTION = re.compile(r"^\W*not (?:the |that |this |those |an? )?(?P<no>[a-z' ]+?)\s*[,.]?\s*(?:but |rather |"
                         r"instead |i mean )+(?:like |the |some |a |an |more )*(?P<yes>[a-z' ]+?)\W*$", re.I)
_NOT_THIS = re.compile(r"\bnot (?:the |that |those |these |an? )?([a-z']+)", re.I)  # 'not the Eagle'
# How many: 'kill two soldiers', 'just two more', 'a couple of bats', '3 more' (2026-10-12: 'kill just two more'
# went to the planner as 'any enemy' twice; 'kill these soldiers' killed one and stopped).
_NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
            'ten': 10, 'couple': 2, 'pair': 2, 'few': 3, 'several': 3, 'both': 2}
_COUNT = re.compile(r"\b(?:just |only )?(?:a |the )?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|couple|pair|"
                    r"few|several|both)(?: of)?(?: (?:the|them|those|these))?\b(?P<more> more)?", re.I)
_SAY_NUMBER = {2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five', 6: 'Six', 7: 'Seven', 8: 'Eight', 9: 'Nine', 10: 'Ten'}
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
    attack: str | bool = False    # ...and attack it: 'hit' (one hit, then hand back) or 'fight' (until it's down)
    every: str | None = None      # 'kill all of them': after each kill, the next of this kind nearby
    count: int | None = None      # 'kill two soldiers': that many of them (with every), None: all
    then: list = field(default_factory=list)  # 'kill the boar and then the soldiers': the orders after this
    options: list = field(default_factory=list)  # for 'ask': [(name, point)]
    steps: list = field(default_factory=list)    # for 'task': what to gather and craft (core/crafting.py)


def split_orders(text: str) -> list[str]:
    """'kill the boar and then after you've killed the boar, kill any soldiers' -> ['kill the boar', 'kill any
    soldiers']: the orders in a sentence, in turn (an 'after you've killed X,' at the start is dropped)."""
    out = []
    for part in _THEN.split(text):
        part = _AFTER_CLAUSE.sub('', part.strip(' ,.')).strip(' ,.')
        m = _ATTACK.search(part)
        names = re.split(r"\s*,?\s*\band\b\s*", m.group('target')) if m else []
        if len(names) > 1 and all(n.strip() for n in names):   # 'kill the boar and the soldier': each in turn
            out.append(part[:m.start('target')] + names[0])
            out += [f'{m.group(1)} {n}' for n in names[1:]]
        elif part:
            out.append(part)
    return out or [text]


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
        self.last_kind: str | None = None                # the last enemy kind attacked ('the next one')
        self.last_attack_t = -1e9                        # when the last attack order was understood
        self._heard: list = []                           # (time, text) of the conversation, newest last
        self.last_walk_t = -1e9                          # when the last walk order was understood

    def heard(self, text: str) -> None:
        """Something said in the conversation (a question and Navi's answer): 'kill all of them' right after
        'Three Godrick Soldiers nearby' means the soldiers."""
        now = time.monotonic()
        self._heard = [(t, x) for t, x in self._heard if now - t < CONTEXT_SECONDS] + [(now, text)]

    def handle(self, text: str, use_model: bool = True) -> WalkAction:
        """use_model False: the rules only (what they don't get is 'none': the caller thinks it through)."""
        saved = self.ask_model
        if not use_model:
            self.ask_model = None
        try:
            return self._handle(text)
        except GameNotRunning:
            return WalkAction('none', "I can't see the game right now.")
        finally:
            self.ask_model = saved

    def _handle(self, text: str) -> WalkAction:
        t = normalize(text).strip().rstrip('?')
        parts = split_orders(t)
        while len(parts) > 1 and not _STOP_LEAD.sub('', parts[0]).strip():
            parts = parts[1:]   # 'cancel that and kill the bat': the new order replaces the current one
        if len(parts) == 1 and parts[0] != t:
            return self._handle(parts[0])
        if len(parts) > 1:   # one at a time: the app runs the next when this one is done
            first = self._handle(parts[0])
            if first.kind == 'walk':
                first.then = parts[1:]
                first.say = f'{first.say} Then the next part!'
            return first
        if self.pending and time.monotonic() - self.pending[0] < ASK_SECONDS:
            choice = self._choose(t, self.pending[1])
            if choice is not None:
                self.pending = None
                return self._walk(*choice)
        self.pending = None
        rest = _STOP_LEAD.sub('', t).strip()
        if rest != t and rest and (_ATTACK.search(rest) or _TARGET.search(rest) or _NEAREST.search(rest)):
            return self._handle(rest)                    # a new order instead of the current one
        m = _CORRECTION.match(rest)
        if m and time.monotonic() - self.last_walk_t < FOLLOW_UP_SECONDS and not _ATTACK.search(rest):
            # 'not X, but Y' just after a walk order: walk to Y instead (and not to anything called X)
            got = self._place_or_character(_clean(m.group('yes')), avoid=_clean(m.group('no')))
            if got is not None:
                return got
        if _STOP.search(t) and len(t.split()) <= STOP_WORDS:
            return WalkAction('stop', 'Stopping.')
        if _FLEE.search(rest or t) and hasattr(self.adapter, 'explore_point'):
            got = self._flee()
            if got is not None:
                return got
        if _EAT.match(rest or t) and hasattr(self.adapter, 'food_slot'):
            return WalkAction('task', 'Eating!', name='food', count=1, steps=[('eat', None)])
        m = _COORDS.search(rest or t)
        if m and hasattr(self.adapter, 'coords_point'):
            x, y, z = (float(m.group(k)) for k in ('x', 'y', 'z'))
            point = self.adapter.coords_point(x, y, z)
            self.last_walk_t = time.monotonic()
            return WalkAction('walk', 'On my way!', tuple(point), f'{x:g} {y:g} {z:g}')
        m = _EXPLORE.match(rest or t)
        if m and hasattr(self.adapter, 'explore_point') and (m.group('dir') or m.group('n') or
                                                             re.match(r"\W*(explore|wander|scout)", rest or t, re.I)):
            point = self.adapter.explore_point(m.group('dir'), float(m.group('n')) if m.group('n') else None)
            if point is not None:
                where = (m.group('dir') or 'ahead').lower()
                self.last_walk_t = time.monotonic()
                return WalkAction('walk', f"Exploring {where}!", tuple(point), f'{where} ({m.group("n") or 64})')
        m = _GET.match(rest or t)
        if m and hasattr(self.adapter, 'book'):
            got = self._get_item(m.group('what'))
            if got is not None:
                return got
        m = _ATTACK.search(t)
        if m:
            return self._attack(_ONE_HIT.sub(' ', m.group('target')), 'hit' if _ONE_HIT.search(t) else 'fight',
                                text=t)
        if self.last_kind and _NEXT_ONE.search(t) and not _TARGET.search(t):   # 'now the next one please'
            return self._attack('next one', 'fight', text=t)
        if time.monotonic() - self.last_attack_t < FOLLOW_UP_SECONDS and not _TARGET.search(t):
            # a correction of the last attack order: 'oh no, not the Eagle, all the soldiers'
            named = self._mentioned(t, context=False)
            if named:
                return self._attack(('all ' if _ALL.search(_NOT_THIS.sub(' ', t)) else '') + named, 'fight', text=t)
        m = _NEAREST.search(t)
        if m:
            what = _clean(m.group('what'))
            if getattr(self.adapter, 'place_kinds', None) == ():   # a game that finds the nearest by name
                got = self._place_or_character(what)
                return got or WalkAction('none', f"I can't find any {what or 'of those'} near you.")
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
            got = self._place_or_character(target)
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

    def character(self, what: str, in_view: bool = False, max_range: float = CHARACTER_RANGE,
                  same_level: bool = False, exclude=()) -> WalkAction | None:
        """The nearest living character within max_range whose name matches the words (forgiving), or any
        hostile one for 'enemy'. in_view ('this one in front of me'): the nearest of those within VIEW_ANGLE
        of where the camera looks, if any. None when nothing around matches."""
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player:
            return None
        words = set(re.findall(r"[a-z0-9']+", _clean(what)))
        generic = bool(words & _GENERIC_ENEMY)
        words -= _GENERIC_ENEMY
        if not words and not generic:
            return None
        same_level = same_level or generic   # 'any enemy': only ones the player can walk to
        p = snap.player.pos
        best = None
        viewed = None
        for e in snap.entities:
            if e.dead or (generic and not e.hostile) or e.id in exclude:
                continue
            d = ((e.pos[0] - p[0]) ** 2 + (e.pos[2] - p[2]) ** 2) ** 0.5
            if d > max_range or (same_level and abs(e.pos[1] - p[1]) > SAME_LEVEL):
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
            # (height counts three times over: "16 blocks away" was a zombie 42 down in a cave, 2026-10-13)
            far = d + HEIGHT_WEIGHT * abs(e.pos[1] - p[1])
            if best is None or far < best[3]:
                best = (d, e, name, far)
            if in_view and snap.camera is not None and d <= VIEW_RANGE and d > 0.1:
                fx, fz = snap.camera.forward[0], snap.camera.forward[2]
                n = (fx * fx + fz * fz) ** 0.5 or 1.0
                cos = ((e.pos[0] - p[0]) * fx + (e.pos[2] - p[2]) * fz) / (n * d)
                if cos >= math.cos(math.radians(VIEW_ANGLE)) and (viewed is None or d < viewed[0]):
                    viewed = (d, e, name)
        best = viewed or (best[:3] if best else None)
        if best is None:
            return None
        d, e, name = best
        label = name or 'that enemy'
        self.last_walk_t = time.monotonic()
        unit = getattr(self.adapter, 'distance_unit', 'metres')
        an = 'an' if label[:1].lower() in 'aeiou' else 'a'
        dy = round(e.pos[1] - p[1])
        level = f' and {abs(dy)} {"down" if dy < 0 else "up"}' if abs(dy) > 3 else ''
        return WalkAction('walk', f"There's {an} {label} {round(d)} {unit} away{level}. Let's go!", tuple(e.pos), label,
                          e.id)

    def _attack(self, target: str, mode: str = 'fight', text: str = '', ask: bool = True) -> WalkAction:
        """'attack the nearest Godrick soldier', 'hit that enemy', 'attack it': walk up and fight it
        ('hit it once': land one hit). Without a name: a kind named earlier in the order ('there was a boar
        around here, kill that too'), else the kind last fought ('the next one'), else what the player looks
        at, else the nearest enemy. 'this ... in front of me' prefers the one in view; 'all of them' goes on
        to the next of that kind after each kill."""
        every = bool(_ALL.search(target)) or bool(re.search(r"\b(any|remaining)\b", target, re.I)
                                                  and re.search(r"[a-z]{3,}s\b", target.lower()))   # 'any soldiers'
        count = None
        m = _COUNT.search(target)
        if m and not re.match(r"\s*(one|in front|that|this)\b", target[m.end():], re.I) \
                and (m.group(1).lower() != 'one' or m.group('more')):
            n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1).lower()]
            target = (target[:m.start()] + ' ' + target[m.end():]).strip()
            if n > 1:
                count, every = n, True
            elif not target.strip(' .,!'):
                target = 'next one'                   # 'one more'
        if re.search(r"\b(them|those|these)\b", target, re.I):
            every = True                              # 'kill those', 'these bats': all of them
        in_view = bool(_IN_VIEW.search(target)) and not re.search(r"\b(nearest|closest|next)\b", target, re.I)
        words = _clean(re.sub(r"\b(nearest|closest|next)\b", ' ', target))
        words = re.sub(r"^(on|at|against|into|onto)\b\s*", '', words)   # 'land one hit on the soldier'
        # 'kill this soldier from me', 'one of the deer', 'the next one too': not part of a name
        words = ' '.join(w for w in words.split() if w not in _NOT_NAME)
        if not words or _PRONOUN.match(words):
            # the conversation only for many ('all of them', 'those'); 'that / it' is what the player looks at
            plural = every or bool(re.search(r"\b(them|those|these)\b", target, re.I))
            named = self._mentioned(text, context=plural)
            if named:
                words = named
            elif re.search(r"\b(next|another|other|more|again)\b", target, re.I) and self.last_kind:
                words = self.last_kind   # 'kill the next one': another of the kind just fought
            elif every and self.last_kind:
                words = self.last_kind
        got = None
        if _PRONOUN.match(words):
            snap = self.adapter.snapshot()
            r = lookat.resolve(snap, self.adapter.raycast) if snap.in_world and snap.camera else None
            if r is not None and r.kind in ('entity', 'target') and r.best and not r.best.entity.dead:
                e = r.best.entity
                got = WalkAction('walk', '', tuple(e.pos), e.name or 'that enemy', e.id)
            words = 'enemy'
        got = got or self.character(words or 'enemy', in_view=in_view, same_level=every)
        if got is not None and got.name and not every:
            # 'kill the soldiers', 'these bats': a plural of the name found means all of them
            names = re.findall(r"[a-z0-9']+", got.name.lower())
            if any(len(w) > 3 and w.endswith('s') and w not in names and any(_word_like(w, n) for n in names)
                   for w in words.split()):
                every = True
        if got is None and ask and self.ask_model is not None and words and words != 'enemy':
            # The words match no name around: the model picks from the characters around (it knows them by
            # name): 'kill the soldier' for a Kaiden Sellsword (2026-10-12 21:35: a search for soldiers began
            # instead). (Through _model_order, which comes back here with ask=False.)
            got = self.ask_model(text or f'kill the {words}')
            if got is not None and got.kind != 'walk':
                got = None
            if got is not None:
                return got
        if got is None:
            return WalkAction('none', f"I don't see any {words or 'enemy'} to attack.")
        got.attack = mode
        self.last_attack_t = time.monotonic()
        if got.name and got.name != 'that enemy':
            self.last_kind = got.name
        what = f'the {got.name}' if got.name and got.name != 'that enemy' else 'it'
        got.say = f"Let's take {what} down!" if mode == 'fight' else f"Attacking {what}!"
        if every and mode == 'fight':
            got.every = got.name if got.name and got.name != 'that enemy' else 'enemy'
            got.count = count
            got.say = f"Let's take them all down! The nearest {got.name or 'one'} first!" if count is None else \
                f"{_SAY_NUMBER.get(count, str(count))} of them! The nearest {got.name or 'one'} first!"
        return got

    def recent(self) -> str:
        """The conversation of the last minute, for the planner."""
        now = time.monotonic()
        return '\n'.join(x for t, x in self._heard if now - t < CONTEXT_SECONDS)

    def nearest_enemy(self, max_range: float, not_kinds=(), exclude=()) -> WalkAction | None:
        """The nearest living hostile on the player's level whose name is none of not_kinds ('other')."""
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player:
            return None
        banned = [set(re.findall(r"[a-z']+", k.lower())) for k in not_kinds]
        p, best = snap.player.pos, None
        for e in snap.entities:
            if e.dead or not e.hostile or abs(e.pos[1] - p[1]) > SAME_LEVEL or e.id in exclude:
                continue
            d = ((e.pos[0] - p[0]) ** 2 + (e.pos[2] - p[2]) ** 2) ** 0.5
            if d > max_range or (best is not None and d >= best[0]):
                continue
            name = e.name
            if not name and e.type_id is not None and hasattr(self.adapter, 'knowledge'):
                try:
                    name = (self.adapter.knowledge(e.type_id) or {}).get('name')
                except Exception:
                    name = None
            words = set(re.findall(r"[a-z']+", (name or '').lower()))
            if any(b and b <= words for b in banned):
                continue
            best = (d, e, name)
        if best is None:
            return None
        d, e, name = best
        return WalkAction('walk', '', tuple(e.pos), name or 'that enemy', e.id)

    def _mentioned(self, text: str, context: bool = True) -> str | None:
        """The name of a kind of character around the player that the order names somewhere ('I think there
        was a boar around here. Can you kill that too?' -> 'Boar'), else (context) the conversation of the
        last minute names ('Three Godrick Soldiers nearby' -> 'kill all of them'). Strict matching: whole
        words; 'not the Eagle' excludes the Eagle."""
        got = self._names_in(_NOT_THIS.sub(' ', text or ''), _NOT_THIS.findall(text or ''))
        if got is None and context:
            for _t, said in reversed(self._heard):   # newest first
                got = self._names_in(said, _NOT_THIS.findall(text or ''))
                if got:
                    break
        return got

    def _names_in(self, text: str, excluded=()) -> str | None:
        if not text:
            return None
        heard = {w.rstrip('s') for w in re.findall(r"[a-z']+", text.lower()) if len(w) >= 3}
        heard -= {w.rstrip('s') for w in _NOT_NAME | _ARTICLES | _GENERIC_ENEMY}
        banned = {w.rstrip('s') for x in excluded for w in re.findall(r"[a-z']+", x.lower()) if len(w) >= 3}
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player:
            return None
        best = None
        for e in snap.entities:
            if e.dead:
                continue
            name = e.name
            if not name and e.type_id is not None and hasattr(self.adapter, 'knowledge'):
                try:
                    name = (self.adapter.knowledge(e.type_id) or {}).get('name')
                except Exception:
                    name = None
            if not name:
                continue
            name_words = {w.rstrip('s') for w in re.findall(r"[a-z']+", name.lower()) if len(w) >= 3}
            if name_words & banned:
                continue
            n = len(name_words & heard)
            if n and (best is None or n > best[0]):
                best = (n, name)
        return best[1] if best else None

    def _crosshair(self) -> WalkAction:
        snap = self.adapter.snapshot()
        r = lookat.resolve(snap, self.adapter.raycast)
        if r.kind in ('entity', 'target') and r.best:
            e = r.best.entity
            return WalkAction('walk', 'On my way!', tuple(e.pos), e.name, e.id)
        if r.kind == 'surface' and r.surface and r.surface.pos:
            return self._walk(None, r.surface.pos)
        return WalkAction('none', "I can't tell where you mean.")

    def _flee(self) -> WalkAction | None:
        """The way away from the hostile ones near (from where they are, weighted to the nearer), FLEE_TO on."""
        snap = self.adapter.snapshot()
        if not snap.player:
            return None
        p = snap.player.pos
        vx = vz = 0.0
        for e in snap.entities:
            if not e.hostile or e.dead:
                continue
            dx, dz = p[0] - e.pos[0], p[2] - e.pos[2]
            d = math.hypot(dx, dz)
            if 0.1 < d < FLEE_FROM:
                vx, vz = vx + dx / d / d, vz + dz / d / d
        if math.hypot(vx, vz) < 1e-6:
            return WalkAction('none', "There's nothing to run from near here.")
        point = self.adapter.explore_point(None, FLEE_TO, vector=(vx, vz))
        if point is None:
            return None
        self.last_walk_t = time.monotonic()
        return WalkAction('walk', 'Running for it!', tuple(point), 'away')

    def _get_item(self, what: str) -> WalkAction | None:
        """'a stone pickaxe', '10 cobblestone', 'some sticks': a task (gather and craft), or None (not an item)."""
        from .crafting import CantMake, plan
        what = _FILLER.sub(' ', what.lower()).strip(' .!?')
        n = None
        m = _AMOUNT.match(what + ' ')
        if m:
            word = (m.group('n') or '').lower()
            n = 1 if m.group('one') else int(word) if word.isdigit() else \
                {'a couple of': 2, 'a few': 3, 'a stack of': 64}.get(word, _NUMBERS.get(word))
            what = what[m.end():].strip() if m.end() <= len(what) else ''
        items = self.adapter.match_item(what)
        if not items:
            return None
        if n is None:
            n = 1 if ONE_OF.search(what) else SOME
        snap = self.adapter.snapshot()
        have = dict(((snap.extra or {}).get('inventory') or {}))
        why = ''
        for item in items:
            try:
                steps = plan(self.adapter.book(), item, have.get(item, 0) + n if have.get(item, 0) else n, have)
            except CantMake as e:
                why = why or str(e)
                continue
            name = item.split(':')[-1].replace('_', ' ')
            if not steps:
                return WalkAction('none', f'You already have {name}!')
            said = name if n == 1 or name.endswith(('s', 'ore', 'iron', 'gold', 'copper', 'dirt', 'stone')) \
                    else name + 's'
            say = f"On it! I'll get {'a' if n == 1 else n} {said}."
            last = steps[-1]
            if last[0] == 'craft' and last[1].result == item and last[1].count > 1 \
                    and (n - have.get(item, 0)) % last[1].count:
                made = last[1].count * last[2]        # ('craft 2 sticks': they come 4 at a time)
                say = f"{name.capitalize()}s come {last[1].count} at a time, so I'll make {made}."
            return WalkAction('task', say, name=item, count=n, steps=steps)
        return WalkAction('none', f"{why or 'I do not know how to get that'}.")

    def _place_or_character(self, what: str, avoid: str = '') -> WalkAction | None:
        """A character near the player by that name, or a place (or kind of block) by that name. A character
        whose name was only partly said loses to a place that matches ('some iron': iron ore, not the Iron
        Golem, 2026-10-13); avoid: words of what the player said not to go to."""
        heard = set(re.findall(r"[a-z0-9']+", what))
        no = set(re.findall(r"[a-z0-9']+", avoid))
        got = self.character(what)
        if got is not None and no and got.name and all(any(_word_like(w, n) for n in got.name.lower().split())
                                                       for w in no):
            got = None                                   # that's the one they said not to
        if got is not None and got.name:
            name_words = [n for n in re.findall(r"[a-z0-9']+", got.name.lower()) if n not in _NOT_NAME]
            whole = all(any(_word_like(h, n) for h in heard) for n in name_words)
            if not whole:
                place = self._named(what)
                if place is not None:
                    return place
        return got or self._named(what)

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
        if all(r.get('kind') == 'block' for r in res):   # kinds of block (iron ore or deepslate iron ore): the nearest
            return self._walk(res[0]['name'], res[0]['position'])
        if exact or len(names) == 1:
            return self._walk(*(exact or names)[0])
        return self._ask(names[:2])

    # ---- helpers ----

    def _walk(self, name: str | None, point) -> WalkAction:
        self.last_walk_t = time.monotonic()
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
