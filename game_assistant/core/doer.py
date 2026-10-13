"""Thinking orders through: a model that works out what an order takes and does it, one action at a time,
watching what happens (any game; first Minecraft).

"Get me some iron", "build me a little shelter", "go find a village and then get me a stone sword": the
model gets the order and the game's state (what's held, where the player is, what's around), and acts with
the character's skills by giving it one simple order at a time (act: what the instant rules and the skills
under them already do: walk somewhere, get or craft an item, fight). Each runs in the game until it's done;
the model gets what came of it (what Navi said: "Here we are", "I can't find any stone near here") and the new
state, and decides the next. It can look things up (the game's wiki), check where something is, survey the
ground round the player (drops, what's at their bottom, pools below), wait, tell
the player something, and finishes with what to tell the player.

The world (the app, DoerWorld) runs each order and reports. Through the spending guard; at most MAX_ACTIONS
actions an order; the player's "stop" ends it.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Protocol

import anthropic

from .spend import SpendGuard

MODEL = 'claude-haiku-5-5'
MAX_TOKENS = 2048
MAX_ACTIONS = 30
THINK_SAY = 5.0         # seconds of thinking before a word to the player that it's still working it out
PROGRESS_EVERY = 25.0   # seconds between progress lines while an action runs
START_WAIT = 2.0        # seconds for an order to get going (the frame thread picks it up)
ACTION_TIMEOUT = 900.0  # seconds one order may run

SYSTEM = """You control the player's character in {game} for them, with a fairy companion's voice (Navi): short,
cheerful lines. The player gave an order; work out everything it takes and do it, one action at a time.

How you act: call `act` with one simple order in plain words, the kind the character's skills understand:
{examples}
Each runs in the game until it's done; then you get what came of it and the game's state again. Think about
what the order needs first (what has to be got or made before what; what's already held), then act step by
step. Do exactly what the player asked for: never take something else instead because it's near or alike
(not another kind of creature, not another item); if the player said which one, it's that one. Find out before saying something can't be done: survey the
ground, check where things are, try. When there is no direct way (none near), reason about where it
would be and how to get to it: where_is finds places, kinds
of block and creatures anywhere the game has loaded; the wiki tells you about the game (where things live or
spawn, what they need, when); then go there, explore (an order like 'explore 80 blocks north'), or wait for
the right time, and check again. Be efficient: where_is gives a few of each with about how long getting
to each takes; take the quickest, and weigh other ways too (a creature that spawns in the dark may be quicker
found by waiting or looking around on the player's own level, at night, than by digging down to one), think
whether getting something first makes it much quicker overall (a pickaxe before digging down through stone: where_is gives how
long digging there would take with what's held and with each pickaxe; better tools for a lot of mining), and
do whatever is quickest in all. If an action fails, think about why (the outcome says) and try another way;
don't repeat the same failing action more than twice. Keep the player told: each act's `say` says what you're
doing and why, in a few words. When the order is done, or can't be done, call finish
with one or two short sentences for the player. Never act beyond what the order asks. Keep the player alive
before all else: never start or go on with a fight on low health or when outnumbered; get away first ('run
away'), eat ('eat something') when food is held, keep clear of creepers, and only then carry on. The recent
conversation (if given) says what earlier orders were: a follow-up ("no, a zombie specifically") changes or
narrows the last one.
{notes}"""

TOOLS = [
    {'name': 'act', 'description': 'Give the character one simple order and wait until it is done. Returns '
     'what came of it (what Navi said while doing it) and the state after. say: one short line for the player '
     'on what you are doing and why (spoken as it starts: "No pickaxe, and it\'s stone all the way down: making '
     'one first!").',
     'input_schema': {'type': 'object', 'properties': {'order': {'type': 'string'}, 'say': {'type': 'string'}},
                      'required': ['order'], 'additionalProperties': False}},
    {'name': 'where_is', 'description': 'How far and which way the nearest place, kind of block or character of '
     'this name is (nothing is done).',
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                      'additionalProperties': False}},
    {'name': 'survey', 'description': 'The ground round the player from the game itself (in the dark too): where '
     'it drops away in each direction, how far down and what is at the bottom (water and how deep, ground, lava), '
     'walls, and pools of water below, with coordinates to go to.',
     'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'say', 'description': 'Tell the player something now (one short sentence), and go on.',
     'input_schema': {'type': 'object', 'properties': {'line': {'type': 'string'}}, 'required': ['line'],
                      'additionalProperties': False}},
    {'name': 'wait', 'description': 'Wait a few seconds (at most 30), then get the state again.',
     'input_schema': {'type': 'object', 'properties': {'seconds': {'type': 'number'}}, 'required': ['seconds'],
                      'additionalProperties': False}},
    {'name': 'finish', 'description': 'The order is done (or can\'t be): what to tell the player.',
     'input_schema': {'type': 'object', 'properties': {'line': {'type': 'string'}}, 'required': ['line'],
                      'additionalProperties': False}},
]


class DoerWorld(Protocol):
    def doer_state(self) -> str: ...                 # what's held, where, health, what's around
    def doer_act(self, order: str) -> str | None: ...   # start it; None if it started, else why not
    def doer_busy(self) -> bool: ...                 # an order is still running
    def doer_said_since(self, t: float) -> list: ...  # what Navi said since monotonic time t
    def doer_where(self, name: str) -> str: ...
    def doer_say(self, line: str) -> None: ...
    def doer_stop(self) -> None: ...


class Doer:
    def __init__(self, world: DoerWorld, game: str, examples: str, notes: str = '', web_sources=(),
                 guard: SpendGuard | None = None, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.world = world
        self.guard = guard or SpendGuard()
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.system = SYSTEM.format(game=game, examples=examples, notes=notes)
        self.tools = list(TOOLS)
        if web_sources:
            self.tools.append({'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3,
                               'allowed_domains': list(web_sources)})
        self.cancelled = False
        self.log: list = []

    def cancel(self) -> None:
        self.cancelled = True
        self.world.doer_stop()

    def run(self, order: str, recent: str = '') -> str:
        """Do the order (blocking: on a worker thread). What was said at the end. recent: the conversation
        just before (earlier orders and what came of them), for follow-ups."""
        self.cancelled = False
        convo = f'<recent_conversation>{recent}</recent_conversation>\n' if recent else ''
        msgs = [{'role': 'user',
                 'content': f'{convo}<state>{self.world.doer_state()}</state>\n\nThe order: {order}'}]
        actions = 0
        for _turn in range(MAX_ACTIONS * 2):
            if self.cancelled:
                return ''
            self.guard.check()
            # (thinking for more than THINK_SAY s: the player hears it's still being worked out)
            timer = threading.Timer(THINK_SAY, lambda: self.cancelled or self.world.doer_say(
                'Still thinking this through...' if actions else 'Working out how to do that...'))
            timer.daemon = True
            timer.start()
            try:
                msg = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=self.system,
                                                  tools=self.tools, messages=msgs, thinking={'type': 'adaptive'},
                                                  output_config={'effort': 'medium'})
            finally:
                timer.cancel()
            self.guard.record(self.model, msg.usage, 'doer')
            uses = [b for b in msg.content if b.type == 'tool_use']
            if not uses:
                text = ' '.join(b.text for b in msg.content if b.type == 'text').strip()
                return text
            msgs.append({'role': 'assistant', 'content': msg.content})
            results = []
            for b in uses:
                if self.cancelled:
                    return ''
                inp = dict(b.input)
                self.log.append({'tool': b.name, 'input': inp})
                if b.name == 'finish':
                    line = str(inp.get('line') or 'Done!')
                    self.world.doer_say(line)
                    return line
                if b.name == 'act':
                    actions += 1
                    if actions > MAX_ACTIONS:
                        self.world.doer_say("That's a lot of steps. I'll stop here.")
                        return ''
                    if inp.get('say'):
                        self.world.doer_say(str(inp['say']))
                    out = self._act(str(inp.get('order') or ''))
                elif b.name == 'where_is':
                    out = self.world.doer_where(str(inp.get('name') or ''))
                elif b.name == 'survey':
                    out = getattr(self.world, 'doer_survey', lambda: 'no survey in this game')()
                elif b.name == 'say':
                    self.world.doer_say(str(inp.get('line') or ''))
                    out = 'said'
                elif b.name == 'wait':
                    end = time.monotonic() + max(0.0, min(30.0, float(inp.get('seconds') or 1)))
                    while time.monotonic() < end and not self.cancelled:
                        time.sleep(0.2)
                    out = f'waited. <state>{self.world.doer_state()}</state>'
                else:
                    out = f'unknown tool {b.name}'
                self.log[-1]['result'] = out if not isinstance(out, list) else '(picture)'
                results.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': out})
            msgs.append({'role': 'user', 'content': results})
        return ''

    def _act(self, order: str) -> str:
        t0 = time.monotonic()
        why = self.world.doer_act(order)
        if why is not None:
            return f'not done: {why}'
        started = time.monotonic()
        while not self.world.doer_busy() and time.monotonic() - started < START_WAIT and not self.cancelled:
            time.sleep(0.1)
        told = time.monotonic()
        while self.world.doer_busy() and time.monotonic() - started < ACTION_TIMEOUT and not self.cancelled:
            time.sleep(0.2)
            if time.monotonic() - told > PROGRESS_EVERY:          # a long one: how it's going
                told = time.monotonic()
                line = getattr(self.world, 'doer_progress', lambda: None)()
                if line:
                    self.world.doer_say(line)
        said = self.world.doer_said_since(t0)
        state = self.world.doer_state()
        return json.dumps({'outcome': said or ['(nothing was said)'], 'seconds': round(time.monotonic() - t0)}) \
            + f'\n<state>{state}</state>'
