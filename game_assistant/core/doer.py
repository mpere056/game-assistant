"""Thinking orders through: a model that works out what an order takes and does it, one action at a time,
watching what happens (any game; first Minecraft).

"Get me some iron", "build me a little shelter", "go find a village and then get me a stone sword": the
model gets the order and the game's state (what's held, where the player is, what's around), and acts with
the character's skills one at a time through typed tools (docs/AGENT.md stage 4): get an item (the goal
planner works out and runs all it takes), go somewhere, mine one block, put one down, fight, flee, eat,
explore; and `act`, an order in words, for what those don't cover. Each runs in the game until it's done;
the model gets what came of it as data (done or not, why not as a Reason, what changed in what's held, what
Navi said, how long it took) and the new state, and decides the next. It can look things up (the game's wiki), check where something is, survey the
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
THINK_SAY = 1.0         # seconds before a first word to the player, if the model hasn't said one (the player
                        # hears something within a second of every order: 2026-10-16, "it should always respond
                        # to me within a second")
THINK_AGAIN = 4.0       # ...and between actions, before "still thinking"
OUTCOME_GRACE = 0.8     # seconds after a step ends for what's said about it to come in
PROGRESS_EVERY = 25.0   # seconds between progress lines while an action runs
START_WAIT = 2.0        # seconds for an order to get going (the frame thread picks it up)
ACTION_TIMEOUT = 900.0  # seconds one order may run
WAY_KEPT = 300.0        # seconds the last way check_way found is given to the next order ("okay, do the jump")

SYSTEM = """You control the player's character in {game} for them, with a fairy companion's voice (Navi): short,
cheerful lines. The player gave an order; work out everything it takes and do it, one action at a time.

How you act: with the tools that do things ({typed}). Moving is go_to: it plans the whole way itself,
jumps (running ones too), climbs, bridges and digs; how ("without placing blocks", "without digging") is its
options, never a separate order. Before saying whether a way, a jump or a climb can be made, check it
(check_way); never guess. Each is carried out in the game till it's done; then
you get what came of it as data: ok (done or not); why not (kind: missing, needs_tool, no_target, unreachable,
stuck, no_spot, danger, interrupted, timeout, ui; with what, where and the route's note); what changed in
what's held; what was said; the seconds it took; and the game's state again. Anything the player asks to have
built (a house, a wall, a tower, a bridge), however big, is worked out as `build` parts: where it goes,
its shape and sizes as asked (or sensible ones), what it's made of; never said to be beyond you. `situation` gives the whole
situation as data (what was done this session: stations put down and where; the plan under way) when the
state isn't enough.

`get` plans all an item takes (tools, stations, smelting, what creatures and blocks give: killed, or an
item used on them, shears on a sheep, the hand on a berry bush) from where the player is, the quickest way, and
plans again by itself when a step fails; when it comes back not done, its why is what it couldn't get round:
work from that (another way, a tool, blocks, somewhere else). `how_to_get` shows that plan and its time
without doing anything: use it to weigh ways. When `get` knows no way to get something the game has, look it
up on the wiki: if it's got by using an item on a creature or a block, `learn` it, then `get` it. `act` is
for what the typed tools don't cover: one simple order in words, the kind the character understands:
{examples}

Think about what the order needs first (what has to be got or made before what; what's already held), then
act step by step. Do exactly what the player asked for: never take something else instead because it's near
or alike (not another kind of creature, not another item); if the player said which one, it's that one.

What the game lets a player do (the inventory and the hotbar, crafting, chests, armour, right-clicking anything:
`use`) the character can do too: use the tool for it; when no tool fits, say it's a skill you don't have yet,
never that the game won't let you (the hotbar was "my orders don't reach the inventory slots" while crafting used
that very screen, 2026-10-16).

Find out before saying something can't be done: survey the ground, check where things are, try. When there is
no direct way (none near), reason about where it would be and how to get to it: where_is finds places, kinds
of block and creatures anywhere the game has loaded; the wiki tells you about the game (where things live or
spawn, what they need, when); then go there, explore, or wait for the right time, and check again. Names of
regions are whole regions, often far off; the shape of the land near (hills, a mountain, high or low ground,
a valley), named or not, is in the survey's land: "the nearest hills" are the nearest high ground there,
unless the player names the region. The state says whether the player stands on the ground or on something
above it: "the ground" is the land itself.

Be efficient: where_is gives a few of each with about how long getting to each takes; take the quickest, and
weigh other ways too (something that comes out at certain times may be quicker found by waiting for then);
think whether getting something first makes it much quicker overall (a better tool before a lot of digging:
where_is gives how long digging there would take with what's held and with each tool); and do whatever is
quickest in all.

If an action fails, think about why (its why says) and try another way; don't repeat the same failing action
more than twice. Use what's already there: a station standing near is gone to and used, never made again or
taken apart to get one; the why says when it couldn't be used (out of reach, in the way).

Ask the player first (finish with the question) only when the order itself would bring a risk or a cost they
may not expect (a long way at night with enemies about, a fight when weak, using up something rare); otherwise
just do it. Each action's say is spoken as it starts: give one only when it tells the player something new (why
this way, what's needed first), not for every step; the player hears nothing else of the actions while you work.
When the order is done, or can't be done, call finish with one short sentence (under 15 words) for the player:
what's done, or why not; for a question, the answer. More only if the player asks for it. Say only what the order was about: never the coordinates, health, food,
time or the creatures around unless the order asked about them. The state is for your thinking, not to be read
out. Enemies are watched and warned of apart from you (the companion hides or fights for the player), so leave
them out unless one stops the order. ("Jump onto that block": "Made it!", not "Made it! I'm standing at 334, 82,
-420. Health is 10 of 20, and a spider and skeletons are about 15 to 20 blocks off, so stay alert.", 2026-10-16.)
Never act beyond what the order asks.

Keep the player alive before all else: never start or go on with a fight on low health or when outnumbered;
get away first (flee), eat when food is held and the player is hurt or hungry, and only then carry on. Before
going where enemies are (a fight, or a place they stand at, at night), look at what the player is armed with
(the state says): with nothing to fight with, get a weapon first when it's quick, and what the game's notes say
helps; a fight that came back `outmatched` is one to come back to better armed, or in daylight. The
recent conversation (if given) says what earlier orders were: a follow-up ("no, that one specifically")
changes or narrows the last one.

Unfinished earlier orders (the state lists them) were cut short by a newer one. One that adds to them ("also",
"while you're doing this", "and then") is done as well: the earlier ones too (first, unless doing them together
is quicker). One that fixes something in their way (a tool broke, out of a hole, eat) is done, and then they're
carried on with, without being asked. One that replaces them ("stop", "forget that", "instead", another plan)
leaves them. Say which in finish's earlier.
{notes}"""

SAY_NOTE = ('say: one short line for the player on what you are doing and why (spoken as it starts: "No '
            'pickaxe, and it\'s stone all the way down: making one first!"); leave it out when it tells them '
            'nothing new.')
_SAY = {'say': {'type': 'string'}}
_XYZ = {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'z': {'type': 'number'}}


def _tool(name: str, description: str, props: dict, required=(), acts: bool = True) -> dict:
    props = {**props, **(_SAY if acts else {})}
    return {'name': name, 'description': description + (' ' + SAY_NOTE if acts else ''),
            'input_schema': {'type': 'object', 'properties': props, 'required': list(required),
                             'additionalProperties': False}}


_ITEM = {'item': {'type': 'string'}, 'count': {'type': 'integer'}}
# The typed tools (docs/AGENT.md stage 4): each one thing the character does, given as data (no words to be
# read wrongly: the doer's "place a crafting table" wasn't understood, 2026-10-13). A game offers the ones it
# has (DoerWorld.doer_tools).
TYPED_TOOLS = [
    _tool('get', 'Get count more of an item (default 1): gathers, mines, crafts and smelts all it takes, tools and '
          'stations too, hunts creatures or uses a tool on them or on blocks (shears on a sheep), the quickest way '
          'from here, planning again on the way when a step fails. item: its name or the game\'s id.',
          _ITEM, ['item']),
    _tool('how_to_get', 'How `get` would get count more of an item from here, without doing anything: the steps '
          'and about how long each takes (seconds), or why it can\'t be done.', _ITEM, ['item'], acts=False),
    _tool('go_to', 'Walk to the game\'s coordinates x y z (as where_is and survey give them; a block there, an '
          'ore: to where it can be reached; a fall into water there is fine; x z alone: that spot at any height; '
          'y alone: down or up to that height where the player is; within: to within that many blocks of x y z), '
          'or to a place, kind of block or '
          'creature by name (place: "village", "iron ore", "zombie", "the ground", "the surface", "that" for what '
          'the player looks at). It digs and builds its way where it must, and jumps gaps (at a run too) by '
          'itself. onto true (with place: a kind of block or "that"): stood on its top, not beside it, for "jump/get '
          'onto ..."; a way check_way found is done by the go_to it gives. place_blocks false: never '
          'puts a block down (the player said "without placing blocks"); dig false: never digs; fewest_jumps: '
          'the way with the fewest jumps over gaps.',
          {**_XYZ, 'place': {'type': 'string'}, 'onto': {'type': 'boolean'}, 'within': {'type': 'number'},
           'place_blocks': {'type': 'boolean'}, 'dig': {'type': 'boolean'}, 'fewest_jumps': {'type': 'boolean'}}),
    _tool('mine_at', 'Mine one block by where it is (x y z, as where_is gives it): walks to where it can reach '
          'it, digging on the way if it must. what: what the block is.', {**_XYZ, 'what': {'type': 'string'}},
          ['x', 'y', 'z']),
    _tool('put_down', 'Put a held block or station (a crafting table, a furnace) down near the player.',
          {'item': {'type': 'string'}}, ['item']),
    _tool('fight', 'Walk up to a creature and fight it till it\'s down. target: its kind or name ("enemy": the '
          'nearest hostile one); count: how many of them; all: every one of that kind near.',
          {'target': {'type': 'string'}, 'count': {'type': 'integer'}, 'all': {'type': 'boolean'}}, ['target']),
    _tool('flee', 'Run away from the hostile ones near.', {}),
    _tool('eat', 'Eat something held.', {}),
    _tool('follow', 'Keep after a creature, this far off (distance, default 3), till another order (Baritone\'s '
          'follow).', {'target': {'type': 'string'}, 'distance': {'type': 'number'}}, ['target']),
    _tool('pickup', 'Pick up the dropped items lying about within radius (default 12): items (names) or all.',
          {'items': {'type': 'array', 'items': {'type': 'string'}}, 'radius': {'type': 'number'}}),
    _tool('tunnel', 'Dig a tunnel straight on from where the player stands: direction (north, south, east, west; '
          'none: the way the camera faces), length (default 16), width (1) and height (2).',
          {'direction': {'type': 'string'}, 'length': {'type': 'integer'}, 'width': {'type': 'integer'},
           'height': {'type': 'integer'}}),
    _tool('inventory', "Lay out the hotbar from what's held (the inventory screen): items to have in it; slots "
          '{"1": item, ...} for exact places (1-9); sort: the usual layout (weapon, tools, food, blocks, then the '
          'rest).', {'items': {'type': 'array', 'items': {'type': 'string'}}, 'slots': {'type': 'object'},
                     'sort': {'type': 'boolean'}}),
    _tool('use', 'Use (right-click) a block or a creature, as a player does: sleep in a bed, open a door, a gate, '
          'a chest or a trapdoor, flip a lever, press a button, put a saddle on a horse (item "saddle"), mount it '
          '(no item), feed, tame or breed (the food as item). target: "that" (what the player looks at), a kind '
          'of block ("bed": the nearest), or a creature ("horse"); or x y z for a block. Walks to it first. '
          'item: what to hold (none: an empty hand).',
          {**_XYZ, 'target': {'type': 'string'}, 'item': {'type': 'string'}}),
    _tool('equip', "Put on what's held to wear: the best armour for each of the head, chest, legs and feet (one "
          "worse than what's worn is swapped back), and a shield into an empty off hand. items: only these.",
          {'items': {'type': 'array', 'items': {'type': 'string'}}}),
    _tool('explore', 'Walk out to see more: direction (north, south-east...; none: the way the camera faces), '
          'distance in blocks (default 64).', {'direction': {'type': 'string'}, 'distance': {'type': 'number'}}),
    _tool('build', 'Build a structure from parts, each a box of one block (the game\'s coordinates: from and to are '
          'its corners, both included): shape "fill" (every cell), "walls" (its four sides, no floor or top: a '
          'room\'s walls), "hollow" (its whole outside), "clear" (emptied: a door or window gap, the room inside; '
          'what\'s there is mined). Laid in order, a later part winning. It gets the blocks first (gathering, '
          'crafting what it takes), mines what\'s in the way, then puts each down from the bottom up, each against '
          'one already there. A house: a floor (fill), walls on it, the inside cleared, a door gap in a wall (clear, '
          '1 wide, 2 high, at the floor\'s top), a roof (fill) on the walls; on open, flat ground near the player '
          '(survey), with blocks held or quickest got (how_to_get).',
          {'parts': {'type': 'array', 'items': {'type': 'object', 'properties': {
              'shape': {'type': 'string', 'enum': ['fill', 'walls', 'hollow', 'clear']},
              'from': {'type': 'object', 'properties': _XYZ, 'required': ['x', 'y', 'z']},
              'to': {'type': 'object', 'properties': _XYZ, 'required': ['x', 'y', 'z']},
              'item': {'type': 'string'}}, 'required': ['shape', 'from', 'to']}}}, ['parts']),
    _tool('learn', 'Record a way to get an item that `get` doesn\'t know (it said there\'s no way), learned from '
          'the wiki: using an item (or the hand: no using) on a creature or a block gives it (milk: a bucket used '
          'on a cow). `get` plans with it from then on, this time and every time after.',
          {'item': {'type': 'string'}, 'target': {'type': 'string'},
           'on': {'type': 'string', 'enum': ['creature', 'block']}, 'using': {'type': 'string'},
           'per': {'type': 'number'}}, ['item', 'target', 'on'], acts=False),
    _tool('situation', 'The player\'s situation now, as data: health and food, where (and on the ground or not), '
          'what\'s held, what was done this session (stations put down and where, what couldn\'t be got to), the '
          'order under way and its plan, what\'s around.', {}, acts=False),
]
TYPED_ACTS = {t['name'] for t in TYPED_TOOLS} - {'how_to_get', 'situation'}

TOOLS = [
    {'name': 'act', 'description': 'Give the character one simple order in words (for what the typed tools '
     'don\'t cover) and wait until it is done. Returns what came of it and the state after. ' + SAY_NOTE,
     'input_schema': {'type': 'object', 'properties': {'order': {'type': 'string'}, 'say': {'type': 'string'}},
                      'required': ['order'], 'additionalProperties': False}},
    {'name': 'where_is', 'description': 'How far and which way the nearest place, kind of block or character of '
     'this name is (nothing is done).',
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                      'additionalProperties': False}},
    {'name': 'waypoint', 'description': 'Places the player named to come back to (Baritone\'s waypoints), kept '
     'between sessions: action set (here, or at x y z, under a name), get (where one is), list (all, with where '
     'the player last died), delete. To go to one: go_to its x y z.',
     'input_schema': {'type': 'object', 'properties': {'action': {'type': 'string',
                                                                  'enum': ['set', 'get', 'list', 'delete']},
                                                       'name': {'type': 'string'}, **_XYZ},
                      'required': ['action'], 'additionalProperties': False}},
    {'name': 'check_way', 'description': 'Whether the player could get to a spot, and how, by the planner (nothing '
     'is done): to x y z (where the feet would be) or "that" (onto the top of the block looked at); with or without '
     'putting blocks down (place_blocks) or digging (dig); most_jumps: in no more jumps over gaps than that (the '
     'way with the fewest is looked for: "can you do it in one jump?"). Says can or not, the jumps (each: across, '
     'slanted or straight, at a run or not, up or down a block), drops, blocks put down and dug, seconds, and '
     'go_to: the go_to that does that very way (to do it, call go_to with exactly those). For "could you jump/get '
     'onto/over/across ...". Never answer such a question from an earlier check: check it as asked.',
     'input_schema': {'type': 'object', 'properties': {**_XYZ, 'target': {'type': 'string'},
                                                       'place_blocks': {'type': 'boolean'},
                                                       'dig': {'type': 'boolean'},
                                                       'most_jumps': {'type': 'integer'}},
                      'additionalProperties': False}},
    {'name': 'survey', 'description': 'The ground round the player from the game itself (in the dark too): where '
     'it drops away in each direction, how far down and what is at the bottom (water and how deep, ground, lava), '
     'walls, pools of water below, the trees near (the height of each and where to stand on its top), and the lie '
     'of the land: whether the player is on the ground or above or below it, the hills near (each top, how much '
     'it rises, the nearest point on it, and points along the top to walk), and the highest ground each way; with '
     'coordinates to go to. trees_within: how far out to list trees (blocks, up to 64; the nearest and the '
     'tallest are given). land_within: how far out to read the land (blocks, up to 120).',
     'input_schema': {'type': 'object', 'properties': {'trees_within': {'type': 'integer'},
                                                       'land_within': {'type': 'integer'}},
                      'additionalProperties': False}},
    {'name': 'say', 'description': 'Tell the player something now (one short sentence), and go on.',
     'input_schema': {'type': 'object', 'properties': {'line': {'type': 'string'}}, 'required': ['line'],
                      'additionalProperties': False}},
    {'name': 'wait', 'description': 'Wait a few seconds (at most 30), then get the state again.',
     'input_schema': {'type': 'object', 'properties': {'seconds': {'type': 'number'}}, 'required': ['seconds'],
                      'additionalProperties': False}},
    {'name': 'finish', 'description': 'The order is done (or can\'t be): what to tell the player (line: one short '
     'sentence on what was asked and nothing else: no coordinates, health or creatures about unless asked; an '
     'answer is its gist: "Yes: one jump, a block down, no blocks needed."); and, when the '
     'state lists unfinished earlier orders, what\'s become of them: done (done now too), dropped (the player '
     'replaced them or doesn\'t want them now), or open (still to do: carried on with later).',
     'input_schema': {'type': 'object', 'properties': {'line': {'type': 'string'},
                                                       'earlier': {'type': 'string',
                                                                   'enum': ['done', 'dropped', 'open']}},
                      'required': ['line'], 'additionalProperties': False}},
]


class DoerWorld(Protocol):
    def doer_state(self) -> str: ...                 # what's held, where, health, what's around
    def doer_act(self, order: str) -> str | None: ...   # start it; None if it started, else why not
    def doer_busy(self) -> bool: ...                 # an order is still running
    def doer_said_since(self, t: float) -> list: ...  # what Navi said since monotonic time t
    def doer_where(self, name: str) -> str: ...
    def doer_say(self, line: str) -> None: ...
    def doer_stop(self) -> None: ...
    # (optional) the typed tools: which this game has; start one (None if it started, else why not); how an item
    # would be got; the situation as data; what's held (for what an order changed); how the last order ended
    # def doer_tools(self) -> set: ...
    # def doer_do(self, tool: str, args: dict) -> str | None: ...
    # def doer_how_to_get(self, item: str, count: int) -> str: ...
    # def doer_situation(self) -> str: ...
    # def doer_inventory(self) -> dict: ...
    # def doer_outcome(self, since: float) -> dict | None: ...


class Doer:
    def __init__(self, world: DoerWorld, game: str, examples: str, notes: str = '', web_sources=(),
                 guard: SpendGuard | None = None, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.world = world
        self.guard = guard or SpendGuard()
        self.client = client or anthropic.Anthropic()
        self.model = model
        have = set(getattr(world, 'doer_tools', lambda: ())())
        typed = [t for t in TYPED_TOOLS if t['name'] in have]
        self.system = SYSTEM.format(game=game, examples=examples, notes=notes,
                                    typed=', '.join(t['name'] for t in typed) + ', act' if typed else 'act')
        self.tools = typed + [t for t in TOOLS if (t['name'] != 'check_way' or hasattr(world, 'doer_check_way'))
                              and (t['name'] != 'waypoint' or hasattr(world, 'doer_waypoint'))]
        if web_sources:
            self.tools.append({'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3,
                               'allowed_domains': list(web_sources)})
        self.log: list = []
        self.last_way = None             # (when, the order, the go_to) of the last way check_way found
        self._order_no = 0              # the order being thought through now (a newer one ends the older)
        self._stop = False
        self._mine = threading.local()   # which order this thread is thinking through

    @property
    def cancelled(self) -> bool:
        """The order this thread thinks through is over: stopped, or a newer one came (two at once shared one
        log, and one died of it: "make me a bed" and "hello, can you hear me?", 2026-10-16)."""
        return self._stop or getattr(self._mine, 'run', None) != self._order_no

    def cancel(self) -> None:
        self._stop = True
        self.world.doer_stop()

    def run(self, order: str, recent: str = '', log: list | None = None) -> str:
        """Do the order (blocking: on a worker thread). What was said at the end. recent: the conversation
        just before (earlier orders and what came of them), for follow-ups. log: where its steps go."""
        self._order_no += 1
        me = self._mine.run = self._order_no
        self._stop = False
        self.earlier = None              # (what finish said became of the unfinished earlier orders)
        self.log = log if log is not None else []
        log = self.log
        said_any = [False]

        def gone():
            return self._stop or self._order_no != me
        convo = f'<recent_conversation>{recent}</recent_conversation>\n' if recent else ''
        if self.last_way is not None and time.monotonic() - self.last_way[0] < WAY_KEPT:
            # (the way checked is the one done: "okay, do the jump" walked to beside the table, not onto it,
            # 2026-10-16)
            convo += (f'<way_checked_last order="{self.last_way[1]}">to do that way: go_to '
                      f'{json.dumps(self.last_way[2])}</way_checked_last>\n')
        msgs = [{'role': 'user',
                 'content': f'{convo}<state>{self.world.doer_state()}</state>\n\nThe order: {order}'}]
        actions = 0
        for _turn in range(MAX_ACTIONS * 2):
            if self.cancelled:
                return ''
            self.guard.check()
            # (thinking for more than THINK_SAY s with nothing said yet: the player hears it's being worked out)
            def still():
                if not gone():
                    said_any[0] = True
                    self.world.doer_say('Still thinking this through...' if said_any[0] and actions
                                        else 'Working out how to do that...')
            timer = threading.Timer(THINK_AGAIN if said_any[0] else THINK_SAY, still)
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
            thought = ' '.join(getattr(b, 'text', '') for b in msg.content if b.type == 'text').strip()
            self._log({'order': order, 'turn': _turn, 'thought': thought or None,
                       'calls': [{'tool': b.name, 'input': dict(b.input)} for b in uses]})
            if not uses:
                # (an answer in words, not by finish: said too. "Can you jump onto that crafting table?" was
                # worked out, answered, and never heard, 2026-10-16)
                text = ' '.join(b.text for b in msg.content if b.type == 'text').strip()
                if text and not self.cancelled:
                    self.world.doer_say(text)
                return text
            msgs.append({'role': 'assistant', 'content': msg.content})
            results = []
            for b in uses:
                if self.cancelled:
                    return ''
                inp = dict(b.input)
                log.append({'tool': b.name, 'input': inp})
                if b.name == 'finish':
                    self.earlier = inp.get('earlier')
                    line = str(inp.get('line') or 'Done!')
                    self.world.doer_say(line)
                    return line
                if b.name == 'act' or b.name in TYPED_ACTS:
                    actions += 1
                    if actions > MAX_ACTIONS:
                        self.world.doer_say("That's a lot of steps. I'll stop here.")
                        return ''
                    if inp.get('say'):
                        said_any[0] = True
                        self.world.doer_say(str(inp['say']))
                    if b.name == 'act':
                        out = self._act(str(inp.get('order') or ''))
                    else:
                        args = {k: v for k, v in inp.items() if k != 'say'}
                        out = self._run(lambda: self.world.doer_do(b.name, args))
                elif b.name == 'how_to_get':
                    out = self.world.doer_how_to_get(str(inp.get('item') or ''), max(1, int(inp.get('count') or 1)))
                elif b.name == 'situation':
                    out = self.world.doer_situation()
                elif b.name == 'learn':
                    out = self.world.doer_learn({k: v for k, v in inp.items() if k != 'say'})
                elif b.name == 'where_is':
                    out = self.world.doer_where(str(inp.get('name') or ''))
                elif b.name == 'check_way':
                    out = getattr(self.world, 'doer_check_way', lambda a: 'no way checks in this game')(dict(inp))
                    try:
                        way = json.loads(out).get('go_to')
                    except (ValueError, AttributeError):
                        way = None
                    if way:
                        self.last_way = (time.monotonic(), order, way)
                elif b.name == 'waypoint':
                    out = getattr(self.world, 'doer_waypoint', lambda a: 'no waypoints in this game')(dict(inp))
                elif b.name == 'survey':
                    out = getattr(self.world, 'doer_survey', lambda **k: 'no survey in this game')(
                        trees_within=int(inp.get('trees_within') or 24),
                        land_within=int(inp.get('land_within') or 96))
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
                log[-1]['result'] = out if not isinstance(out, list) else '(picture)'
                self._log({'order': order, 'tool': b.name, 'result': log[-1]['result']})
                results.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': out})
            msgs.append({'role': 'user', 'content': results})
        return ''

    def _log(self, row: dict) -> None:
        try:
            getattr(self.world, 'doer_log', lambda r: None)(row)
        except Exception:
            pass

    def _act(self, order: str) -> str:
        return self._run(lambda: self.world.doer_act(order))

    def _run(self, start) -> str:
        """Start an order (start() -> None, or why it couldn't start), wait till it's done, and say what came of
        it as data: ok and why not, what changed in what's held, what was said, how long it took; the state."""
        t0 = time.monotonic()
        held = getattr(self.world, 'doer_inventory', lambda: None)()
        why = start()
        if why is not None:
            return json.dumps({'ok': False, 'not_started': why})
        started = time.monotonic()
        while not self.world.doer_busy() and time.monotonic() - started < START_WAIT and not self.cancelled:
            time.sleep(0.1)
        told = time.monotonic()
        while True:
            while self.world.doer_busy() and time.monotonic() - started < ACTION_TIMEOUT and not self.cancelled:
                time.sleep(0.2)
                if time.monotonic() - told > PROGRESS_EVERY:          # a long one: how it's going
                    told = time.monotonic()
                    line = getattr(self.world, 'doer_progress', lambda: None)()
                    if line and line != getattr(self, '_last_progress', None):   # (not the same line again)
                        self._last_progress = line
                        self.world.doer_say(line)
            time.sleep(OUTCOME_GRACE)                     # (what's said as it ends: "I can't get through here")
            # under way again (a step failed and the goal planned on from there): not over yet
            if not self.world.doer_busy() or time.monotonic() - started >= ACTION_TIMEOUT or self.cancelled:
                break
        said = self.world.doer_said_since(t0)
        state = self.world.doer_state()
        out = {}
        # how it ended, as data (docs/AGENT.md): done, or why not (what's missing, what couldn't be got at...)
        ended = getattr(self.world, 'doer_outcome', lambda since: None)(t0)
        if ended:
            out['ok'] = ended.get('ok')
            if ended.get('why'):
                out['why'] = ended['why']
        if held is not None:
            now = getattr(self.world, 'doer_inventory', lambda: None)() or {}
            changed = {k: now.get(k, 0) - held.get(k, 0) for k in {*held, *now} if now.get(k, 0) != held.get(k, 0)}
            out['changed'] = {k.split(':')[-1]: (f'+{v}' if v > 0 else str(v)) for k, v in sorted(changed.items())}
        out['said'] = said or ['(nothing was said)']
        out['seconds'] = round(time.monotonic() - t0)
        return json.dumps(out, default=str) + f'\n<state>{state}</state>'
