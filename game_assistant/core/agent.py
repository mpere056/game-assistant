"""The talking agent: one Claude Haiku 5.5 call with fact tools per question (docs/PLAN.md).

The model never sees raw game memory and never answers from its own memory of the game: it calls
tools that return exact facts from the adapter (what is at the crosshair, who is nearby, a
character type's data) and phrases them. The tool it picks is also the routing decision, so
there is no separate router. Game-independent: everything game-specific comes from the adapter.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from . import lookat, nav
from .companion import Companion
from .interface import CAP_KNOWLEDGE, CAP_PLACES, CAP_RAYCAST, CAP_SCREEN, CAP_SEARCH, GameAdapter, GameNotRunning, dist, dot, normalize, sub
from .spend import SpendGuard

MODEL = 'claude-haiku-5-5'
MAX_TOKENS = 1024         # thinking included: at 300, thinking about a picture used it all (an empty answer)
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 60  # past this, start a fresh conversation (keeps every request small)

SYSTEM = """You are a game assistant that the player talks to while playing {game}. Your answers are
spoken aloud.{persona}

How to answer:
- Be brief. Most answers are one short sentence, often under 12 words. Answer only what was asked:
  "can you see that?" gets what it is and about how far, not its stats. Give weaknesses, stats, drops
  or lore only when asked for them. No filler, no repeating the question, no explaining how you know.
- Each message starts with <game_view_now>: exact facts about what is at the centre of the screen
  when the player asked (the same as the look_at tool). Use it for "what is that" questions. For
  anything else about the game state (who is nearby, another enemy's data), call a tool. Use only
  facts from game_view_now or tools.
- Never invent names, numbers, abilities, locations or lore. If there is no name, say you don't know
  its name. If a fact is missing, say so.
- damage_taken_percent: 100 is normal damage, above 100 is a weakness, below 100 a resistance.
  status_buildup_needed: lower numbers trigger sooner; immune_to: statuses that never build up.
  weak_to: types clearly above the enemy's usual; takes_extra_from_everything: every type does extra
  damage (say that rather than listing every type).
- Facts are base values from the game data; mention that only if asked how exact they are.
- For "where is X", "where do I find X", "what drops X": call search_game_data and give the one or two
  most useful places, not the whole list, unless asked for all.
- wiki: an offline copy of the Elden Ring wiki. Use it for attack patterns and boss strategies
  (aspect fight), where to find things (location), what a place holds (here), questlines (quest),
  mechanics and anything else the game data can't say. web_search only if the wiki has nothing.
- "What is there to do here?", "where am I?": call where_am_i, then wiki with its wiki_page_for_here
  and aspect "here". "Where is X?": where_is. "Anything good near me?": items_near_me.
- look_at_screen shows you the game picture. Use it when the question is about something visible that
  game_view_now doesn't cover: scenery, a structure, a gust or glow, an item on the ground, a sign, a
  puzzle. Then name what you see and use the wiki for what it does ("that's a Spiritspring: ...").
  For "what is that?" or "how do I get up/past this?", if the game facts only show ground or a wall
  at the crosshair, always look_at_screen before answering: never answer "just a wall". Pictures
  from earlier questions are gone: look again for each new question about what is visible. Navi
  (the fairy) is not in the picture.
- Menus: you can read the inventory, equipment, map and other menus from the picture (a picture comes
  with the question when they mention one). Never say you can't see their screen or inventory
  without having looked.
- If what they mean is unclear (two enemies at the crosshair), ask one short question.
- If a place has a height_note, mention it briefly ("it's down in the tunnel, about 60 metres below").
- Round distances ("about 50 metres"). Directions are relative to the camera: ahead, left, right,
  behind; for far places use the compass too. Never describe where things are on the screen.
- Plain text, no markdown, no lists. Don't mention ids, refs or other internal numbers.
- Mention only the thing asked about; leave out other things nearby unless the player asks.

Examples of the right length:
  "Can you see that?" -> "Yes! A Giant Dog, about 50 metres ahead."
  "What about those over there?" -> "Three Putrid Corpses, about 45 metres to the right."
  "What's it weak to?" -> "Fire and slash!"
  "Lead me to the Forsaken Ruins." -> "This way! About 150 metres west."
  "Where's the Moonveil?" -> "In Gael Tunnel, guarded by a Magma Wyrm."
"""

PERSONA = """
You are a small glowing fairy companion floating beside the player's character, like the guiding
fairy in an adventure game. Talk like one: bright, quick, a little eager ("Look!", "Over there!",
"Hey!" now and then, not every time), always short.
You move only when the player asks you to; never fly anywhere on your own.
- "Go to", "fly to", "show me" or "lead me to" something nearby (an enemy, what they're looking at):
  use the fairy tool. Your range for those is {leash:.0f} metres.
- "Take me to", "guide me to" or "lead me to" a place or an item somewhere in the world (a grace, a
  ruin, a castle, "the Moonveil"): use guide_to. You fly ahead toward it, up to {guide:.0f} metres in
  front of the player, and keep going as they follow, so tell them which way and roughly how far."""


TOOLS = [
    {
        'name': 'look_at',
        'description': 'What the player is looking at right now (the thing at the centre of the screen), '
                       'with its facts from the game data. Call this for "what is that", "what am I looking at", '
                       '"is that dangerous", or questions about "this/that enemy".',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'nearby_characters',
        'description': 'Living characters near the player, nearest first: name if known, type id, hostile or '
                       'not, distance in metres, direction from the camera, health.',
        'input_schema': {
            'type': 'object',
            'properties': {'max_distance_m': {'type': 'number', 'description': 'Search radius, default 40, max 80.'}},
            'additionalProperties': False,
        },
    },
    {
        'name': 'search_game_data',
        'description': 'Search the game data by name. kind "item": where an item, weapon, armour, talisman or '
                       'spell is found in the world, which enemies drop it, and who sells it. kind "enemy": '
                       'matching enemy types with their stats and named attacks. kind "place": Sites of Grace '
                       'and map locations with their region.',
        'input_schema': {
            'type': 'object',
            'properties': {'kind': {'type': 'string', 'enum': ['item', 'enemy', 'place']},
                           'query': {'type': 'string', 'description': 'Name or part of a name, e.g. "moonveil".'}},
            'required': ['kind', 'query'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'fairy',
        'description': 'Move the fairy (you). action "go": fly to the target and stay; "show": fly to it for a '
                       'few seconds then come back; "lead": guide the player there along a walkable route; '
                       '"come_back": return to the player. target "crosshair" is what the player is looking at; '
                       '"character" needs ref from nearby_characters; "nearest_enemy" picks the closest hostile.',
        'input_schema': {
            'type': 'object',
            'properties': {'action': {'type': 'string', 'enum': ['go', 'show', 'lead', 'come_back']},
                           'target': {'type': 'string', 'enum': ['crosshair', 'character', 'nearest_enemy']},
                           'ref': {'type': 'integer', 'description': 'A character ref from nearby_characters.'}},
            'required': ['action'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'guide_to',
        'description': 'Guide the player to a named place anywhere in this world (a Site of Grace, a ruin, a '
                       'castle, a cave) or to where an item is found: you fly ahead along a walkable route until '
                       'they arrive. Returns its distance and compass direction. For "the nearest grace", pass '
                       'place "nearest site of grace" (or "nearest landmark").',
        'input_schema': {
            'type': 'object',
            'properties': {'place': {'type': 'string', 'description': 'Place or item name, e.g. "Forsaken Ruins".'}},
            'required': ['place'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'nearest_places',
        'description': 'The Sites of Grace (or landmarks) nearest the player, nearest first, with distance and '
                       'compass direction. Use for "nearest grace", "closest site of grace", "what is near me". '
                       'Never answer "nearest" questions from search_game_data: it does not know distances.',
        'input_schema': {
            'type': 'object',
            'properties': {'kind': {'type': 'string', 'enum': ['site of grace', 'landmark']}},
            'additionalProperties': False,
        },
    },
    {
        'name': 'wiki',
        'description': 'The offline Elden Ring wiki. topic: a page name (boss, enemy, item, place, NPC, mechanic). '
                       'aspect: overview, location (where to find / how to get), fight (moveset, strategy), here '
                       '(what a place holds: graces, bosses, NPCs, loot), walkthrough, quest, stats.',
        'input_schema': {
            'type': 'object',
            'properties': {'topic': {'type': 'string'},
                           'aspect': {'type': 'string', 'enum': ['overview', 'location', 'fight', 'here', 'walkthrough',
                                                                 'quest', 'stats']}},
            'required': ['topic'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'where_am_i',
        'description': "The player's surroundings by name: nearest Sites of Grace and landmarks with distance and "
                       'direction, the world, and the wiki page that best describes this spot.',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'where_is',
        'description': 'Where a named place (or the place an item is found) is from the player: distance, compass '
                       'direction, height difference. Does not move you; use guide_to to lead the way.',
        'input_schema': {'type': 'object', 'properties': {'place': {'type': 'string'}}, 'required': ['place'],
                         'additionalProperties': False},
    },
    {
        'name': 'items_near_me',
        'description': 'Items, weapons, armour, talismans, spells and Ashes of War picked up near the player '
                       '(open world: by map square; named places within about 400 m).',
        'input_schema': {
            'type': 'object',
            'properties': {'kind': {'type': 'string', 'enum': ['weapon', 'armour', 'talisman', 'item', 'ash of war', 'spell']}},
            'additionalProperties': False,
        },
    },
    {
        'name': 'look_at_screen',
        'description': 'A picture of the game screen right now, for questions about something visible that the '
                       'game facts do not cover (scenery, structures, effects like gusts or glows, items, signs).',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'character_info',
        'description': 'Facts from the game data about a character type, by the type_id another tool returned: '
                       'name, base HP, damage taken per damage type, status buildup needed, immunities.',
        'input_schema': {
            'type': 'object',
            'properties': {'type_id': {'type': 'integer'}},
            'required': ['type_id'],
            'additionalProperties': False,
        },
    },
]


# A picture taken when the talk key went down is used if the question arrives within this many
# seconds (holding the key while talking, plus speech recognition).
EARLY_SCREEN_MAX_AGE = 30.0

# Questions about something on the screen that the game facts can't give (menus are not read from
# memory yet): the picture goes with the question itself, which saves a round trip. Haiku chose not
# to look when asked about an open inventory and said it couldn't see it.
SCREEN_WORDS = re.compile(
    r"\b(inventory|menu|map|equipment|equipped|key items?|items? tab|tab|status|stats|level up|"
    r"on (my|the) screen|this (screen|menu|tab|page|item|message)|i have (the|my) \w+ open|"
    r"what does (this|it) say|read (this|that))\b", re.I)

LOOK_HINTS = """Picture 1: the whole game screen now. Picture 2: its middle third, zoomed (the crosshair is its centre).
Name the most notable thing near the centre, as the game calls it. Elden Ring things that are easy to miss:
- Spiritspring: a tall column of pale white mist or wind against a cliff, rising from a misty swirl on the ground. It
  looks like a waterfall, but in Elden Ring a white misty column at a cliff foot is usually a Spiritspring (jump into it
  on Torrent to be carried up the cliff). Say waterfall only if you see falling water landing in a pool or stream.
- Site of Grace: a small golden light with gold rays pointing the way; Stake of Marika: a wooden stake with a faint glow.
- Lift: a round stone platform with a pressure plate or a small pedestal; Waygate / Sending Gate: a glowing portal or archway.
- Fog wall: a golden or white mist in a doorway (a boss or area); an Evergaol: a stone circle with a blue barrier.
- Items on the ground: small white-gold glowing motes; messages: glowing orange writing on the ground; bloodstains: red pools.
- Chests, Imp statues (need Stonesword Keys), Minor Erdtrees, ruins, churches, caves, catacomb doors, Divine Towers.
Then use the wiki for what it does or how to use it. If nothing stands out, say what is there in a few words."""



def _block_type(b) -> str | None:
    return b.get('type') if isinstance(b, dict) else getattr(b, 'type', None)

@dataclass
class Answer:
    text: str = ''
    first_word_s: float | None = None
    total_s: float = 0.0
    usd: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    notice: str | None = None  # spending warning, or why nothing was sent
    view_entity: int | None = None  # the entity at the crosshair when asked (the fairy can point at it)


def direction(cam, pos) -> str:
    v = normalize(sub(pos, cam.pos))
    ahead, right = dot(v, cam.forward), dot(v, cam.right)
    angle = math.degrees(math.atan2(right, ahead))
    if abs(angle) <= 30:
        return 'ahead'
    if abs(angle) >= 135:
        return 'behind'
    return 'right' if angle > 0 else 'left'


COMMAND_SYSTEM = """The player of {game} gave an order for their character (it walks there by itself, with
Navi leading), or attacks. Turn it into exactly one action by calling one action tool: attack_character
(walk up to a character or enemy and land one hit), walk_to_character (a
character or enemy near the player, by name, or "enemy" for the nearest hostile one: the list of
characters around is given), walk_to_place (a named place: a Site of Grace, landmark, dungeon, ruin),
walk_to_nearest (the nearest place of a kind), walk_to_crosshair ("that", "over there"), stop_walking,
or ask_player when it is unclear (one short question, at most two options). Enemies and characters are
not places: never send the player to where an item is found instead. Use where_is or nearest_places
first only to check a place name. Never answer with text alone."""

COMMAND_TOOLS = [
    {'name': 'attack_character', 'description': "Walk up to the nearest character with this name (as listed), "
     "or 'enemy' for the nearest hostile one, and land one hit on it.",
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                      'additionalProperties': False}},
    {'name': 'walk_to_character', 'description': "Walk to the nearest character near the player with this "
     "name (as listed), or 'enemy' for the nearest hostile one.",
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                      'additionalProperties': False}},
    {'name': 'walk_to_place', 'description': 'Walk to a named place (as the game names it).',
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                      'additionalProperties': False}},
    {'name': 'walk_to_nearest', 'description': "Walk to the nearest place of a kind: 'site of grace' or 'landmark'.",
     'input_schema': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['site of grace', 'landmark']}},
                      'required': ['kind'], 'additionalProperties': False}},
    {'name': 'walk_to_crosshair', 'description': 'Walk to what the player is looking at.',
     'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'stop_walking', 'description': 'Stop walking.',
     'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'ask_player', 'description': 'Ask one short question when the order is unclear.',
     'input_schema': {'type': 'object', 'properties': {'question': {'type': 'string'},
                                                       'options': {'type': 'array', 'items': {'type': 'string'}}},
                      'required': ['question'], 'additionalProperties': False}},
]


class Agent:
    def __init__(self, adapter: GameAdapter, guard: SpendGuard | None = None, client: anthropic.Anthropic | None = None,
                 companion: Companion | None = None):
        self.adapter = adapter
        self.guard = guard or SpendGuard()
        self.client = client or anthropic.Anthropic()
        self.companion = companion
        persona = PERSONA.format(leash=companion.leash, guide=companion.guide_ahead) if companion else ''
        self.system = SYSTEM.format(game=adapter.game, persona=persona)
        self.messages: list[dict] = []
        self.tools = [t for t in TOOLS
                      if (t['name'] != 'search_game_data' or CAP_SEARCH in adapter.capabilities)
                      and (t['name'] != 'fairy' or companion is not None)
                      and (t['name'] != 'guide_to' or (companion is not None and CAP_PLACES in adapter.capabilities))
                      and (t['name'] not in ('nearest_places', 'where_am_i', 'where_is', 'items_near_me')
                           or CAP_PLACES in adapter.capabilities)
                      and (t['name'] != 'wiki' or getattr(adapter, 'wiki', None) is not None)
                      and (t['name'] != 'look_at_screen' or CAP_SCREEN in adapter.capabilities)]
        sources = getattr(adapter, 'web_sources', None)
        if sources:  # Anthropic's server-side web search, limited to the game's wikis ($0.01 per search)
            self.tools.append({'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 2,
                               'allowed_domains': list(sources)})

    # ---- F10 orders the instant rules didn't understand ----

    def command(self, text: str) -> dict | None:
        """One action for an order, from Haiku: {'tool': name, 'input': {...}} or None. Its own short
        exchange (not the question history); a few cents of a cent; through the spending guard."""
        tools = [t for t in self.tools if t.get('name') in ('where_is', 'nearest_places')] + COMMAND_TOOLS
        around = self._characters_around()
        msgs = [{'role': 'user', 'content': f'<characters_around>{around}</characters_around>\n\n{text}'}]
        system = COMMAND_SYSTEM.format(game=self.adapter.game)
        for _ in range(3):
            self.guard.check()
            msg = self.client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system, tools=tools,
                                              messages=msgs, thinking={'type': 'adaptive'},
                                              output_config={'effort': 'low'})
            self.guard.record(MODEL, msg.usage, 'command')
            uses = [b for b in msg.content if b.type == 'tool_use']
            for b in uses:
                if b.name in {t['name'] for t in COMMAND_TOOLS}:
                    return {'tool': b.name, 'input': dict(b.input)}
            if not uses:
                return None
            msgs.append({'role': 'assistant', 'content': msg.content})
            msgs.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': b.id, 'content': self._run_tool(b.name, b.input)[0]}
                for b in uses]})
        return None

    def _characters_around(self, limit: int = 12) -> str:
        """'Godrick Soldier (enemy) 39 m; Deer 33 m; ...' for the command prompt."""
        try:
            snap = self.adapter.snapshot()
        except (GameNotRunning, TimeoutError, RuntimeError):
            return 'unknown'
        if not snap.in_world or not snap.player:
            return 'unknown'
        p = snap.player.pos
        rows = []
        for e in snap.entities:
            if e.dead:
                continue
            k = self._knowledge(e.type_id) if e.type_id is not None else None
            name = e.name or (k or {}).get('name') or 'unknown character'
            rows.append((dist(p, e.pos), f"{name}{' (enemy)' if e.hostile else ''} {dist(p, e.pos):.0f} m"))
        rows.sort()
        return '; '.join(r for _, r in rows[:limit]) or 'none'

    # ---- tools: exact facts from the adapter ----

    def _knowledge(self, type_id):
        """Game data for a type, or a short error the model can relay. Positions and health still
        work when the game data can't be read."""
        if CAP_KNOWLEDGE not in self.adapter.capabilities:
            return None
        try:
            return self.adapter.knowledge(type_id)
        except (TimeoutError, RuntimeError) as e:
            return {'data_error': f'the game data could not be read: {e}'}

    def tool_look_at(self, _input: dict) -> dict:
        snap = self.adapter.snapshot()
        if not snap.in_world:
            return {'error': 'no character in the world right now (loading, menu or title screen)'}
        return lookat.facts(lookat.resolve(snap, self.adapter.raycast), self._knowledge)

    def tool_nearby_characters(self, inp: dict) -> dict:
        radius = max(1.0, min(80.0, float(inp.get('max_distance_m') or 40.0)))
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player or not snap.camera:
            return {'error': 'no character in the world right now'}
        near = sorted((e for e in snap.entities if not e.dead and dist(e.pos, snap.player.pos) <= radius),
                      key=lambda e: dist(e.pos, snap.player.pos))[:8]
        out = []
        for e in near:
            known = self._knowledge(e.type_id) if e.type_id is not None else None
            item = {'ref': e.id, 'name': (known or {}).get('name') or e.name, 'type_id': e.type_id, 'hostile': e.hostile,
                    'distance_m': round(dist(e.pos, snap.player.pos), 1), 'direction': direction(snap.camera, e.center)}
            if e.health is not None and e.max_health:
                item['health'] = f'{e.health:.0f}/{e.max_health:.0f}'
            out.append(item)
        return {'radius_m': radius, 'count': len(out), 'characters': out}

    def tool_character_info(self, inp: dict) -> dict:
        tid = inp.get('type_id')
        if not isinstance(tid, int):
            return {'error': 'type_id must be an integer from another tool'}
        facts = self._knowledge(tid)
        return facts if facts else {'error': f'no game data for type {tid}'}

    def tool_search_game_data(self, inp: dict) -> dict:
        kind, query = inp.get('kind'), str(inp.get('query') or '').strip()
        if kind not in ('item', 'enemy', 'place') or not query:
            return {'error': 'need kind (item, enemy or place) and a query'}
        return self.adapter.search(kind, query)

    def tool_fairy(self, inp: dict) -> dict:
        c = self.companion
        if c is None:
            return {'error': 'no fairy in this session'}
        action, target = inp.get('action'), inp.get('target') or 'crosshair'
        if action == 'come_back':
            c.follow()
            return {'done': 'coming back to the player'}
        snap = self.adapter.snapshot()
        if not snap.in_world or not snap.player:
            return {'error': 'no character in the world right now'}
        entity_id, point = None, None
        if target == 'crosshair':
            r = lookat.resolve(snap, self.adapter.raycast)
            if r.kind in ('entity', 'target') and r.best:
                entity_id = r.best.entity.id
            elif r.kind == 'surface' and r.surface and r.surface.pos:
                point = r.surface.pos
            else:
                return {'error': 'nothing at the crosshair to go to'}
        elif target == 'nearest_enemy':
            foes = [e for e in snap.entities if e.hostile and not e.dead]
            if not foes:
                return {'error': 'no living enemy nearby'}
            entity_id = min(foes, key=lambda e: dist(e.pos, snap.player.pos)).id
        else:
            ref = inp.get('ref')
            match = [e for e in snap.entities if e.id == ref and not e.dead]
            if not match:
                return {'error': 'that character is not nearby any more; call nearby_characters again'}
            entity_id = ref
        goal = point or next((e.pos for e in snap.entities if e.id == entity_id), None)
        far = goal is not None and dist(goal, snap.player.pos) > c.leash
        if action == 'lead':
            if CAP_RAYCAST not in self.adapter.capabilities or goal is None:
                return {'error': 'cannot plan a route here'}
            route = nav.plan(self.adapter.raycast, snap.player.pos, goal)
            if route is None:
                return {'error': 'no walkable route found from here'}
            c.lead(route.waypoints)
            return {'done': 'leading the way', 'route_reaches_target': route.reaches_goal,
                    'note': '' if route.reaches_goal else 'the route goes as far as the nearby area allows; follow it and I will plan the rest'}
        if action == 'show':
            c.show(entity_id=entity_id, point=point)
        else:
            c.go(entity_id=entity_id, point=(point[0], point[1] + 1.2, point[2]) if point else None)
        return {'done': f'flying there ({action})', 'beyond_leash': far,
                'distance_m': round(dist(goal, snap.player.pos), 1) if goal else None}

    def tool_guide_to(self, inp: dict) -> dict:
        place = str(inp.get('place') or '').strip()
        if not place or self.companion is None:
            return {'error': 'need a place name'}
        low = place.lower()
        if any(w in low for w in ('nearest', 'closest', 'nearby')) and hasattr(self.adapter, 'nearest_places'):
            found = self.adapter.nearest_places('landmark' if 'landmark' in low else 'site of grace', limit=3)
        else:
            found = self.adapter.locate(place)
        if 'error' in found:
            return found
        for r in found.get('results', []):
            if r.get('position') is not None:
                self.companion.guide(r['position'], r['name'])
                out = {'guiding_to': r['name'], 'region': r.get('region'), 'kind': r.get('kind'),
                       'distance_m': r['distance_m'], 'compass': r['compass'], 'height_diff_m': r.get('height_diff_m')}
                snap = self.adapter.snapshot()
                if snap.camera:
                    out['direction_from_camera'] = direction(snap.camera, r['position'])
                if found.get('found_via_item'):
                    out['found_via_item'] = found['found_via_item']
                return out
        notes = [f"{r['name']}: {r.get('note')}" for r in found.get('results', [])][:3]
        return {'error': 'no reachable place with that name', 'details': notes or found.get('note')}

    def tool_nearest_places(self, inp: dict) -> dict:
        res = self.adapter.nearest_places(inp.get('kind') or 'site of grace', limit=5)
        for r in res.get('results', []):
            r.pop('position', None)  # internal
        return res

    def tool_wiki(self, inp: dict) -> dict:
        w = getattr(self.adapter, 'wiki', None)
        if w is None or not w.ok:
            return {'error': 'no offline wiki for this game (run Get-GameData.bat)'}
        return w.lookup(str(inp.get('topic') or ''), inp.get('aspect') or 'overview')

    def tool_where_am_i(self, _inp: dict) -> dict:
        return self.adapter.where_am_i()

    def tool_where_is(self, inp: dict) -> dict:
        res = self.adapter.locate(str(inp.get('place') or ''))
        for r in res.get('results', []):
            r.pop('position', None)
        return res

    def tool_items_near_me(self, inp: dict) -> dict:
        return self.adapter.items_near_me(inp.get('kind'))

    def grab_screen(self) -> list | dict:
        """Two JPEGs of the game window (only the game's own picture): the whole view, about 1024 px
        wide, and the middle third at full detail, so small or faint things near the crosshair show."""
        import base64
        import io
        from PIL import ImageGrab

        from ..ui import capture
        snap = self.adapter.snapshot()
        scr = snap.screen
        if scr is None:
            return {'error': 'the game window position is unknown'}
        # The game's own pixels: no fairy, no speech bubble, nothing covering it.
        hwnd = capture.find_window(scr.x, scr.y, scr.width, scr.height)
        img = capture.window_picture(hwnd) if hwnd else None
        if img is None:  # a screen grab instead: only while the game is in front (never other windows)
            if not scr.focused:
                return {'error': 'the game is not the active window, so its picture would show other windows; '
                                 'ask the player to click back into the game and ask again'}
            img = ImageGrab.grab(bbox=(scr.x, scr.y, scr.x + scr.width, scr.y + scr.height), all_screens=True).convert('RGB')
        w, h = img.size
        centre = img.crop((w // 3, h // 3, w - w // 3, h - h // 3))
        if centre.width > 768:
            centre = centre.resize((768, round(centre.height * 768 / centre.width)))
        if w > 1024:
            img = img.resize((1024, round(h * 1024 / w)))

        def block(im):
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=82)
            return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                                'data': base64.b64encode(buf.getvalue()).decode('ascii')}}
        return [block(img), block(centre), {'type': 'text', 'text': LOOK_HINTS}]

    def capture_screen(self) -> None:
        """Take the picture now (the talk key went down), so look_at_screen shows what the player was
        looking at when they started asking, and doesn't spend time grabbing it later."""
        try:
            pic = self.grab_screen()
        except Exception as e:  # the game may be closed: look_at_screen will say so later
            pic = {'error': f'the screen could not be captured: {e}'}
        self._early_screen = (time.monotonic(), pic)

    def tool_look_at_screen(self, _inp: dict):
        early, self._early_screen = getattr(self, '_early_screen', None), None
        if early and time.monotonic() - early[0] < EARLY_SCREEN_MAX_AGE and isinstance(early[1], list):
            return early[1]
        return self.grab_screen()

    def _run_tool(self, name: str, inp: dict) -> tuple[str, bool]:
        fn = {'nearest_places': self.tool_nearest_places, 'look_at': self.tool_look_at, 'nearby_characters': self.tool_nearby_characters,
              'character_info': self.tool_character_info, 'search_game_data': self.tool_search_game_data,
              'fairy': self.tool_fairy, 'guide_to': self.tool_guide_to, 'wiki': self.tool_wiki,
              'where_am_i': self.tool_where_am_i, 'where_is': self.tool_where_is,
              'items_near_me': self.tool_items_near_me, 'look_at_screen': self.tool_look_at_screen}.get(name)
        if fn is None:
            return f'unknown tool {name}', True
        try:
            out = fn(inp if isinstance(inp, dict) else {})
            return (out if isinstance(out, list) else json.dumps(out)), False  # a list: content blocks (an image)
        except (GameNotRunning, TimeoutError, RuntimeError) as e:
            return f'the game could not be read: {e}', True

    # ---- one question ----

    def _drop_old_pictures(self) -> None:
        """Pictures from earlier questions become a note: they are out of date (the model answered
        from an old one instead of looking again), and each costs about 1,200 tokens per request."""
        changed = False
        for m in self.messages:
            if m['role'] != 'user' or not isinstance(m['content'], list):
                continue
            if any(_block_type(b) == 'image' for b in m['content']):  # attached to a question
                m['content'] = [b for b in m['content'] if _block_type(b) != 'image'] + [
                    {'type': 'text', 'text': '(picture removed)'}]
                changed = True
            for block in m['content']:
                if isinstance(block, dict) and block.get('type') == 'tool_result' and isinstance(block.get('content'), list):
                    if any(c.get('type') == 'image' for c in block['content']):
                        block['content'] = [{'type': 'text', 'text': '(picture removed)'}]
                        changed = True
        if changed:  # thinking blocks are signed against what came before them: earlier ones must go too
            for m in self.messages:
                if m['role'] == 'assistant' and isinstance(m['content'], list):
                    kept = [b for b in m['content'] if _block_type(b) not in ('thinking', 'redacted_thinking')]
                    m['content'] = kept or [{'type': 'text', 'text': '...'}]

    def ask(self, text: str, on_text: Callable[[str], None] = lambda s: None) -> Answer:
        ans = Answer()
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = []
        self._drop_old_pictures()
        # Attach what is at the crosshair right now, so the most common questions ("what is that?")
        # need one model call instead of two. It costs about 150 input tokens.
        try:
            snap = self.adapter.snapshot()
            if snap.in_world:
                res = lookat.resolve(snap, self.adapter.raycast)
                if res.kind in ('entity', 'target') and res.best and res.confidence >= 0.6:
                    ans.view_entity = res.best.entity.id
                view = json.dumps(lookat.facts(res, self._knowledge))
            else:
                view = json.dumps({'error': 'no character in the world right now (loading, menu or title screen)'})
        except (GameNotRunning, TimeoutError, RuntimeError) as e:
            view = json.dumps({'error': f'the game could not be read: {e}'})
        question = f'<game_view_now>{view}</game_view_now>\n\n{text}'
        picture = None
        if CAP_SCREEN in self.adapter.capabilities and SCREEN_WORDS.search(text):
            try:
                picture = self.tool_look_at_screen({})
            except (GameNotRunning, TimeoutError, RuntimeError, OSError):
                picture = None  # the model can still ask for one
        if isinstance(picture, list):
            ans.tools_used.append('picture')
            self.messages.append({'role': 'user', 'content': picture + [{'type': 'text', 'text': question}]})
        else:
            self.messages.append({'role': 'user', 'content': question})
        t0 = time.perf_counter()
        for _ in range(MAX_TOOL_ROUNDS):
            warning = self.guard.check()  # raises SpendCapReached at the cap
            if warning:
                ans.notice = warning
            with self.client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS, system=self.system, tools=self.tools, messages=self.messages,
                thinking={'type': 'adaptive'}, output_config={'effort': 'low'},
            ) as stream:
                for event in stream:
                    if event.type == 'content_block_delta' and event.delta.type == 'text_delta':
                        if ans.first_word_s is None:
                            ans.first_word_s = time.perf_counter() - t0
                        ans.text += event.delta.text
                        on_text(event.delta.text)
                msg = stream.get_final_message()
            ans.usd += self.guard.record(MODEL, msg.usage, 'chat')
            self.messages.append({'role': 'assistant', 'content': msg.content})
            ans.tools_used += [b.name for b in msg.content if b.type == 'server_tool_use']
            if msg.stop_reason == 'refusal':
                ans.notice = 'The model declined to answer that.'
                break
            if msg.stop_reason == 'pause_turn':  # a long server-side web search: let it continue
                continue
            if msg.stop_reason == 'max_tokens' and not ans.text.strip():
                ans.notice = 'The model ran out of room before answering; ask again.'
            if msg.stop_reason != 'tool_use':
                break
            results = []
            for block in msg.content:
                if block.type == 'tool_use':
                    ans.tools_used.append(block.name)
                    out, is_error = self._run_tool(block.name, block.input)
                    results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': out, 'is_error': is_error})
            self.messages.append({'role': 'user', 'content': results})
        else:
            ans.notice = 'stopped after too many tool calls'
        ans.total_s = time.perf_counter() - t0
        return ans
