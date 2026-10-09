"""The talking agent: one Claude Haiku 5.5 call with fact tools per question (docs/PLAN.md).

The model never sees raw game memory and never answers from its own memory of the game: it calls
tools that return exact facts from the adapter (what is at the crosshair, who is nearby, a
character type's data) and phrases them. The tool it picks is also the routing decision, so
there is no separate router. Game-independent: everything game-specific comes from the adapter.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from . import lookat, nav
from .companion import Companion
from .interface import CAP_KNOWLEDGE, CAP_PLACES, CAP_RAYCAST, CAP_SEARCH, GameAdapter, GameNotRunning, dist, dot, normalize, sub
from .spend import SpendGuard

MODEL = 'claude-haiku-5-5'
MAX_TOKENS = 300          # answers are short and spoken; this caps a runaway reply
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
- web_search (if available) only for what the game data can't say: attack patterns, boss strategies,
  lore, questlines, puzzles, or when search_game_data finds nothing. Say briefly it's from the wiki.
- If what they mean is unclear (two enemies at the crosshair), ask one short question.
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
                       'castle, a cave) or to where an item is found: you fly ahead toward it, up to 100 m in '
                       'front of them, until they arrive. Returns its distance and compass direction.',
        'input_schema': {
            'type': 'object',
            'properties': {'place': {'type': 'string', 'description': 'Place or item name, e.g. "Forsaken Ruins".'}},
            'required': ['place'],
            'additionalProperties': False,
        },
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
                      and (t['name'] != 'guide_to' or (companion is not None and CAP_PLACES in adapter.capabilities))]
        sources = getattr(adapter, 'web_sources', None)
        if sources:  # Anthropic's server-side web search, limited to the game's wikis ($0.01 per search)
            self.tools.append({'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 2,
                               'allowed_domains': list(sources)})

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

    def _run_tool(self, name: str, inp: dict) -> tuple[str, bool]:
        fn = {'look_at': self.tool_look_at, 'nearby_characters': self.tool_nearby_characters,
              'character_info': self.tool_character_info, 'search_game_data': self.tool_search_game_data,
              'fairy': self.tool_fairy, 'guide_to': self.tool_guide_to}.get(name)
        if fn is None:
            return f'unknown tool {name}', True
        try:
            return json.dumps(fn(inp if isinstance(inp, dict) else {})), False
        except (GameNotRunning, TimeoutError, RuntimeError) as e:
            return f'the game could not be read: {e}', True

    # ---- one question ----

    def ask(self, text: str, on_text: Callable[[str], None] = lambda s: None) -> Answer:
        ans = Answer()
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = []
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
        self.messages.append({'role': 'user', 'content': f'<game_view_now>{view}</game_view_now>\n\n{text}'})
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
