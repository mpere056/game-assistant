"""The assistant app: chat window, fairy companion, voice in and out (docs/PLAN.md, "Runtime").

    Assistant.bat [game]      (default eldenring)

Threads:
- main (tk): the chat window. Typed and spoken questions both arrive here.
- fairy: 60 times a second, reads the game (adapter.snapshot), moves the companion and places the
  overlay. Never waits on the network.
- overlay: the fairy's own window and message loop (ui/fairy_overlay.py).
- speaker: Windows voice (voice/tts.py). push to talk: microphone and Whisper (voice/stt.py).
- one worker per question: local command first (core/commands.py), else the Haiku agent.
Settings: .local/settings.json (game_assistant/config.py). Log: runtime/chat-<date>.jsonl.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext

from . import config
from .core import commands, lookat
from .core.agent import Agent
from .core.autowalk import AutoWalk
from .core.combat import Fighter
from .core.plan import PlanRunner, steps_from
from .core.walk_commands import CommandResolver, WalkAction
from .core.companion import Companion
from .core.interface import CAP_CAMERA, CAP_NAVMESH, CAP_RAYCAST, CAP_SCREEN, GameNotRunning
from .core.spend import SpendCapReached
from .games import adapter_for

ROOT = Path(__file__).resolve().parents[1]
FPS = 60


def _own_window_in_front() -> bool:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value == os.getpid()


LEDGE_STEP, LEDGE_ROLL = 1.5, 3.0   # metres out the ground is probed for a step and for a roll
AHEAD_PROBE = 1.6                   # metres: the walk's look ahead for walls (stuck with none: break it)
WALL_NEAR = 1.6                     # metres: a wall or rock this near blocks stepping that way
LEDGE_DROP = 3.5                    # deeper than this below the feet: a ledge
MAX_CHASES = 3
ESCAPE_LINE_EVERY = 20.0            # seconds: "Too many! Getting some room!" at most this often
REKEY_AFTER = 0.6                   # seconds a held movement key may leave the character idle (_rekey)
COMING_RANGE = 20.0                 # metres: an enemy coming at the player this near is fought next
ON_THE_WAY = 6.0                    # metres: an enemy this near coming at the player on the way is fought
PLAN_TOO_LONG = 12.0                # a plan running this long is taken as hung
LEG_CHECK_EVERY = 1.0               # seconds between re-checks of the walk's next legs (collision far off isn't
                                    # loaded when a route is planned: a leg through a wall was accepted)
NO_ROUTE_KICK = 2.5                 # seconds a walk may go without a route before planning is restarted
# Orders the planner (core/plan.py) takes: several parts, or conditions, durations, shapes, "along the way"...
_PLAN_WORDS = re.compile(r"\b(until|till|while|along the way|on the way|on your way|without|circles?|straight|"
                         r"seconds?|minutes?|everything|everyone|alternat\w*|encounter\w*|wander\w*|patrol\w*|"
                         r"explore|back to|come back|return|then|after|afterwards|first|finally|each|times|"
                         r"meet|anything)\b", re.I)


def needs_plan(text: str) -> bool:
    return bool(_PLAN_WORDS.search(text)) or len(text.split()) > 16
CHAINS_FILE = ROOT / '.local' / 'eldenring' / 'chains.json'   # learned enemy combos
ALL_RANGE = 60.0                    # 'kill all of them': the next one within this many metres                      # a target that ran off this often: hand back
FIGHT_FROM = 12.0  # metres: a "fight" order this close starts fighting at once (else walk up first)
FOLLOW_MOVED = 3.0  # metres a character may move before the route to it is re-planned
ACTION_LINES = {'ladder': 'Climb the ladder!', 'lift': 'Take the lift!', 'jump': 'Jump across here!',
                'drop': 'Drop down here!'}


class App:
    def __init__(self, game: str):
        self.cfg = config.load()
        self.adapter = adapter_for(game)
        caps = self.adapter.capabilities
        self.companion = None
        self.overlay = None
        if self.cfg['fairy'] and {CAP_CAMERA, CAP_SCREEN} <= caps:
            from .ui.fairy_overlay import FairyOverlay, make_dpi_aware
            make_dpi_aware()
            self.companion = Companion(self.adapter.raycast if CAP_RAYCAST in caps else None, float(self.cfg['leash_m']),
                                       float(self.cfg['fairy_scale']))
            if CAP_NAVMESH in caps:  # the game's own walkable mesh (Get-GameData extracted it)
                self.companion.route_fn = self.adapter.route
            self.overlay = FairyOverlay(tuple(self.cfg['fairy_color']))
            self.overlay.bubble_scale = float(self.cfg['bubble_scale'])
            self.overlay.start()
        # Navi's speech bubble in the game (the overlay draws it); None when switched off.
        self.bubble = self.overlay if self.overlay and self.cfg['speech_bubble'] else None
        self.speaker = None
        if self.cfg['speak_answers']:
            from .voice import fairy_voice
            if self.cfg['voice_engine'] == 'fairy' and fairy_voice.available():
                self.speaker = fairy_voice.FairyVoice(
                    self.cfg['fairy_voice'], float(self.cfg['voice_pitch']), float(self.cfg['voice_speed']),
                    bool(self.cfg['voice_chime']), method=self.cfg['voice_method'],
                    pitch_hz=float(self.cfg['voice_pitch_hz']), size=float(self.cfg['voice_size']))
            else:  # Windows' own voice: no download needed
                from .voice.tts import Speaker
                self.speaker = Speaker(self.cfg['voice'], int(self.cfg['voice_rate']))
        self.agent = Agent(self.adapter, companion=self.companion)
        self.cmd_ctx = commands.Context(self.adapter, self.companion,
                                        stop_speech=self.speaker.stop if self.speaker else (lambda: None),
                                        stop_tasks=lambda: self._walk_request('stop', 'stop'))
        # F10 orders: the character walks by itself along Navi's route (phase 4b).
        self.walk = AutoWalk(sprint=bool(self.cfg['auto_walk_sprint']))
        # Fighting for the player ("fight that soldier"): dodges by the game's own attack timings.
        self._ledges: tuple = ((), ())    # where the ground drops away around the player (_ledge_loop)
        self._chases: dict = {}           # entity -> times a fight handed over to a chase (it ran)
        self.companion.plan_close = True  # the character walks by itself: a route even to close goals
        self.plan = PlanRunner(self)      # orders of several parts (core/plan.py)
        self._plan_cmd = None
        self._los: tuple = (None, None)    # (target, a clear line to it) from _ledge_loop
        self.fighter = Fighter(getattr(self.adapter, 'attack_windows', lambda m, a: []), self._poise_of,
                               lambda: self._ledges, self._load_chains(),
                               lambda eid: self._los[1] if self._los[0] == eid else None,
                               reach_of=getattr(self.adapter, 'attack_reach', lambda m, t, a: None),
                               weapon=getattr(self.adapter, 'player_weapon', lambda: None))
        self._fight_after = None          # the walk is going to a character to fight it
        self.resolver = CommandResolver(self.adapter, ask_model=self._model_order)
        self._walk_cmd = None             # ('start', point, name) or ('stop', why), applied by the fairy loop
        self._walk_route = None           # the companion route the walk follows
        self._route_goal = None           # where the route to a moving character was planned to
        self._took_over = 0.0             # when the player last pressed a movement key themselves
        self.keys = self.turner = None
        if self.cfg['auto_walk'] and self.companion and sys.platform == 'win32':
            from .ui.win_input import Keys, KeyWatch, Turner
            self.keys = Keys({**config.DEFAULTS['walk_keys'], **self.cfg['walk_keys']})   # (new keys: 'item')
            self.turner = Turner()
            KeyWatch(self._player_key)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.running = True
        self.log = ROOT / 'runtime' / f'chat-{datetime.now():%Y%m%d}.jsonl'
        self.log.parent.mkdir(exist_ok=True)
        self.fairy_ms = 0.0  # time spent per fairy frame, for the status line
        # Changed code on disk is only used after a restart: say so (a test once ran the old code).
        self._started = time.time()
        self._stale_checked, self._stale = 0.0, False
        self.last_view = None

        self._build_window()
        self.ptt = None
        try:
            from .voice.stt import PushToTalk
            self.ptt = PushToTalk(on_text=lambda t: self.q.put(('voice', t)), key=self.cfg['push_to_talk_key'],
                                  model=self.cfg['speech_model'], microphone=self.cfg['microphone'],
                                  on_state=lambda s: self.q.put(('voice_state', s)),
                                  vocabulary=getattr(self.adapter, 'vocabulary', ''),
                                  on_press=self.agent.capture_screen,
                                  command_key=self.cfg['command_key'] if self.keys else None,
                                  on_command=lambda t: self.q.put(('order', t)))
        except Exception as e:  # voice input is optional
            self.q.put(('voice_state', f'voice input unavailable: {e}'))
        if self.companion:
            threading.Thread(target=self._fairy_loop, name='fairy', daemon=True).start()
        if self.keys is not None and hasattr(self.adapter, 'raycast'):
            threading.Thread(target=self._ledge_loop, name='ledges', daemon=True).start()
        if hasattr(self.adapter, 'warm_up'):  # load game data now, not on the first question
            threading.Thread(target=self._warm_up, name='warm up', daemon=True).start()

    def _warm_up(self) -> None:
        for _ in range(120):  # until a character is in the world (up to about 10 minutes)
            self.adapter.warm_up()
            if getattr(self.adapter, '_places', None) is not None:
                return
            time.sleep(5)

    # ---- window ----

    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title(f'Game Assistant: {self.adapter.game}')
        self.root.geometry('460x540')
        self.root.attributes('-topmost', True)
        self.text = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, state=tk.DISABLED, font=('Segoe UI', 10))
        self.text.tag_config('you', foreground='#1f5fbf', font=('Segoe UI', 10, 'bold'))
        self.text.tag_config('meta', foreground='#888888', font=('Segoe UI', 8))
        self.text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))
        self.status = tk.Label(self.root, anchor='w', fg='#666666', font=('Segoe UI', 8))
        self.status.pack(fill=tk.X, padx=6)
        bar = tk.Frame(self.root)
        bar.pack(fill=tk.X, padx=6, pady=6)
        self.entry = tk.Entry(bar, font=('Segoe UI', 10))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind('<Return>', self._typed)
        self.ontop = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text='On top', variable=self.ontop,
                       command=lambda: self.root.attributes('-topmost', self.ontop.get())).pack(side=tk.RIGHT)
        key = self.cfg['push_to_talk_key']
        self.say('meta', f'Ask about what you see, or tell the fairy what to do ("go to that", "come back", '
                         f'"lead me there", "stop"). Hold {key} to talk. Hosted model: Claude Haiku 5.5, '
                         f'cap ${self.agent.guard.cap:.0f}/hour. Settings: .local/settings.json\n')
        if not os.environ.get('ANTHROPIC_API_KEY'):
            self.say('meta', 'No ANTHROPIC_API_KEY in this window: set it with setx and open a new window.\n')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.entry.focus_set()
        self.root.after(30, self._pump)

    def say(self, tag: str | None, s: str) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, s, tag or ())
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def _typed(self, _event=None) -> None:
        question = self.entry.get().strip()
        if question:
            self.entry.delete(0, tk.END)
            if question.startswith('/') and self.keys:  # "/go to the nearest grace": an order, like F10
                self.order(question[1:].strip(), 'typed')
            else:
                self.ask(question, 'typed')

    # ---- orders to the character (F10) ----

    def order(self, text: str, source: str) -> None:
        self.say('you', '\nYou' + (' (order, voice)' if source == 'voice' else ' (order)') + ': ')
        self.say(None, text + '\n')
        threading.Thread(target=self._order_work, args=(text,), daemon=True).start()

    def _next_order(self) -> bool:
        """The next part of a '... and then ...' order, if any: run it (on a worker thread, as orders are)."""
        then = getattr(self, '_then', None)
        if not then:
            return False
        nxt = then.pop(0)
        rest = list(then)

        def work():
            self._order_work(nxt)
            if rest and not getattr(self, '_then', None):   # the part didn't queue its own: keep the rest
                self._then = rest
        threading.Thread(target=work, daemon=True).start()
        return True

    def _order_work(self, text: str, from_plan_queue: bool = False) -> None:
        if not from_plan_queue and needs_plan(text) and self._can_plan():
            return self._plan_order(text)
        try:
            action = self.resolver.handle(text)
        except Exception as e:  # show it instead of dying
            action = WalkAction('none', f"Hmm, I couldn't work that out ({type(e).__name__}).")
        if action.kind == 'none' and not from_plan_queue and self._can_plan():
            return self._plan_order(text)              # the rules didn't get it: the planner might
        if action.kind in ('walk', 'stop'):
            self._plan_cmd = 'stop'                    # a new order replaces a plan
            self._fight_all = action.every if action.kind == 'walk' else None   # 'kill all of them'
            self._then = list(action.then) if action.kind == 'walk' else []      # '... and then ...'
        if action.kind == 'walk':
            self._walk_request('start', action.point, action.name, action.entity_id, action.attack)
        elif action.kind == 'stop':
            self._walk_request('stop', 'stop')
        self._navi_line(action.say)
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'t': datetime.now().isoformat(timespec='seconds'), 'order': text,
                                'action': action.kind, 'place': action.name}) + '\n')

    def _can_plan(self) -> bool:
        return getattr(self, 'agent', None) is not None and hasattr(self.agent, 'plan')

    def _plan_order(self, text: str) -> None:
        """An order of several parts: Navi answers at once, the model writes the steps (~1-2 s), the frame
        thread runs them (core/plan.py)."""
        self._navi_line('Okay! Let me work that out...')
        record = {'t': datetime.now().isoformat(timespec='seconds'), 'order': text, 'action': 'plan'}
        try:
            got = self.agent.plan(text, self.resolver.recent())
        except SpendCapReached as e:
            got, record['error'] = None, str(e)
        except Exception as e:  # show it instead of dying
            got, record['error'] = None, f'{type(e).__name__}: {e}'
        steps = steps_from((got or {}).get('steps'))
        if steps:
            self._fight_all, self._then = None, []
            self._plan_cmd = steps
            self._navi_line((got or {}).get('say') or "On it!")
        else:
            self._navi_line(f"Sorry, I couldn't work out how to do that." if 'error' not in record
                            else f"Sorry, I couldn't plan that ({record['error'][:60]}).")
        record['plan'] = [x.text() for x in steps]
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')

    # ---- the plan's world (core/plan.py PlanWorld) ----

    def plan_target(self, target: str):
        if target.startswith('#entity '):
            snap = self.adapter.snapshot()
            e = next((x for x in snap.entities if str(x.id) == target.split()[1] and not x.dead), None)
            return (tuple(e.pos), e.name or (self.adapter.knowledge(e.type_id) or {}).get('name'), e.id) if e else None
        if target == 'crosshair':
            a = self.resolver._crosshair()
        else:
            a = self.resolver.character(target) or self.resolver.handle(f'go to the {target}')
            self.resolver.pending = None
        if a is None:
            return None
        if a.kind == 'ask' and a.options:
            return (tuple(a.options[0][1]), a.options[0][0], None)
        if a.kind != 'walk' or a.point is None:
            return None
        return (tuple(a.point), a.name, a.entity_id)

    def plan_find(self, kind: str, max_range: float, not_kinds: list):
        skip = self.fighter.unreachable
        if kind in ('enemy', 'other', 'any', 'anything', 'enemies'):
            a = self.resolver.nearest_enemy(max_range, not_kinds, exclude=skip)
        else:
            a = self.resolver.character(kind, max_range=max_range, same_level=True, exclude=skip)
        return (tuple(a.point), a.name, a.entity_id) if a else None

    def plan_walk(self, point, name, entity_id, fight: bool) -> None:
        self._walk_request('start', point, name, entity_id, 'fight' if fight else False)

    def plan_stop(self) -> None:
        self._walk_request('stop', 'plan')

    def plan_say(self, line: str) -> None:
        self._navi_line(line)

    def _model_order(self, text: str) -> WalkAction | None:
        """Orders the instant rules didn't understand: one Haiku call picks the action."""
        try:
            r = self.agent.command(text)
        except SpendCapReached as e:
            return WalkAction('none', str(e))
        if not r:
            return None
        tool, inp = r['tool'], r['input']
        if tool == 'attack_character':
            return self.resolver._attack(inp.get('name', 'enemy'), 'hit')
        if tool == 'fight_character':
            return self.resolver._attack(inp.get('name', 'enemy'), 'fight')
        if tool == 'walk_to_character':
            got = self.resolver.character(inp.get('name', 'enemy'))
            return got or WalkAction('none', f"I don't see any {inp.get('name', 'of those')} near you.")
        if tool == 'walk_to_place':
            got = self.resolver._named(inp.get('name', ''))
            return got or WalkAction('none', f"I don't know where {inp.get('name', 'that')} is.")
        if tool == 'walk_to_nearest':
            return self.resolver.nearest(inp.get('kind', 'site of grace'))
        if tool == 'walk_to_crosshair':
            return self.resolver._crosshair()
        if tool == 'stop_walking':
            return WalkAction('stop', 'Stopping.')
        if tool == 'ask_player':
            options = []
            for name in inp.get('options', [])[:2]:
                res = [x for x in self.adapter.locate(name).get('results', []) if x.get('position')]
                if res:
                    options.append((res[0]['name'], res[0]['position']))
            return WalkAction('ask', inp.get('question', 'Where to?'), options=options)
        return None

    def _code_changed(self) -> bool:
        """Any of the assistant's source files changed since it started (checked every 5 s)."""
        now = time.time()
        if not self._stale and now - self._stale_checked > 5.0:
            self._stale_checked = now
            src = Path(__file__).resolve().parent
            try:
                self._stale = any(f.stat().st_mtime > self._started + 1 for f in src.rglob('*.py'))
            except OSError:
                pass
        return self._stale

    def _walk_request(self, what: str, *args) -> None:
        self._walk_cmd = (what, *args)

    def _player_key(self, _vk: int) -> None:
        """The player pressed a movement key themselves (keyboard hook thread): they take over."""
        self._took_over = time.monotonic()

    def _navi_line(self, line: str) -> None:
        if not line:
            return
        self.q.put(('meta', '\n' + line + '\n'))
        if self.bubble:
            self.bubble.bubble_say(line)
        if self.speaker:
            self.speaker.say(line)

    def _walk_step(self, snap, dt: float) -> None:
        """Auto-walk, once per fairy frame: follow Navi's route, press the keys, speak about events."""
        if self.keys is None:
            return
        cmd, self._walk_cmd = self._walk_cmd, None
        if cmd and cmd[0] == 'start':
            self.fighter.stop()
            entity = cmd[3] if len(cmd) > 3 else None
            mode = cmd[4] if len(cmd) > 4 else False
            self._fight_after = entity if mode == 'fight' else None
            near = entity is not None and snap.player and entity != getattr(self, '_via_route', None) and any(
                e.id == entity and math.dist(e.pos, snap.player.pos) < FIGHT_FROM for e in snap.entities)
            if self._fight_after is not None and near:  # close already: straight into the fight
                self.walk.stop()
                self.fighter.start(entity, cmd[2])
                self._fight_after = None
                self._took_over = 0.0
                cmd = None
        if cmd and cmd[0] == 'start':
            self.companion.guide(cmd[1], cmd[2])     # Navi plans the route and leads the way
            entity = cmd[3] if len(cmd) > 3 else None
            mode = cmd[4] if len(cmd) > 4 else False
            self.walk.start([], cmd[1], cmd[2], target_id=entity, attack=mode == 'hit' or mode is True)
            self._walk_route = None
            self._took_over = 0.0
            self._walk_entity = entity  # walking to a character: re-plan the route as it moves
            self._route_goal = cmd[1]
        elif cmd and cmd[0] == 'stop' and (self.walk.active or self.fighter.active):
            self.walk.stop(cmd[1])
            self.fighter.stop()
            self._fight_after = None
            self.companion.follow()
        cmd_plan, self._plan_cmd = self._plan_cmd, None
        if cmd_plan == 'stop':
            self.plan.stop()
        elif cmd_plan:
            self.plan.start(cmd_plan, snap)
        if self.plan.active and not (self.walk.active or self.fighter.active) \
                and time.monotonic() - self._took_over < 0.5:   # the player took over during a move step
            self.plan.stop()
            self.keys.release_all()
            self._navi_line("Okay, you've got it!")
        if self.plan.active:
            intent = self.plan.update(snap, dt, self.walk.active, self.fighter.active)
            if intent is not None:            # a move step: its keys (never off a ledge)
                if snap.screen is not None and snap.screen.focused and snap.camera:
                    keys = self.fighter._safe(intent.keys, math.degrees(math.atan2(snap.camera.forward[0],
                                                                                      snap.camera.forward[2])),
                                              roll=False)
                    self.keys.set(keys)
                    self._rekey(snap, dt)
                    if intent.turn:
                        yaw = math.degrees(math.atan2(snap.camera.forward[0], snap.camera.forward[2]))
                        self.turner.turn(intent.turn, yaw)
                else:
                    self.keys.release_all()
                self._walk_events()
                return
        if (self.walk.active or self.fighter.active) and time.monotonic() - self._took_over < 0.5:
            if self.fighter.active:
                self.fighter.stop()
                self.walk.events.append('stopped:you')
            self.walk.stop('you')
            self._fight_after = None
            self.companion.follow()
        if self.fighter.active:
            self._fight_step(snap, dt)
            return
        if not self.walk.active:
            self.companion.walking = False
            if self.keys.held:
                self.keys.release_all()
            self._walk_events()
            return
        if getattr(self, '_walk_entity', None) is not None:
            self._follow_entity(snap)
            if not self.walk.active:
                return
        self.companion.walking = True
        if self.companion.mode != 'guide' and not self.walk.route and self.walk.goal is not None:
            self.companion.guide(self.walk.goal, self.walk.name)   # (a line said meanwhile set it to follow)
        route = self.companion.route
        if route is not None and route is not self._walk_route:  # planned, re-planned or extended
            self.walk.set_route(route, self.companion.route_actions)
            self._walk_route = route
        if snap.player:
            self._last_player_pos = snap.player.pos
        self.walk.clear_line = self._los[1] if self._los[0] == self.walk.target_id else None
        self._kick_planning()
        wall, self._leg_wall = getattr(self, '_leg_wall', None), None
        if wall is not None and hasattr(self.adapter, 'note_wall') \
                and time.monotonic() - getattr(self, '_last_wall_replan', 0.0) > 3.0:
            self._last_wall_replan = time.monotonic()   # a wall across a leg ahead: note it, plan round it now
            self.adapter.note_wall(*wall)
            self.companion._route_time = -1e9
            self.companion.route = None
            self.walk.events.append('wall_ahead')
        if self._fight_on_the_way(snap):
            return
        intent = self.walk.update(snap, dt)
        if snap.screen is not None and snap.screen.focused and self.walk.active:
            self.keys.set(intent.keys)
            for t in intent.taps:
                self.keys.tap(t)
            if self._rekey(snap, dt):
                self.walk.events.append('rekey')
            if intent.turn and snap.camera:
                yaw = math.degrees(math.atan2(snap.camera.forward[0], snap.camera.forward[2]))
                self.turner.turn(intent.turn, yaw)
        else:
            self.keys.release_all()
        self._walk_trace(snap, intent, route)
        self._walk_events()

    def _rekey(self, snap, dt: float) -> bool:
        """A movement key held and the character standing idle (animation 0), not moving, for REKEY_AFTER s:
        the game didn't take the press (sent as the window came back into focus, it was lost and never sent
        again: walks "stuck" for 4 s with forward held and the character idle; 2026-10-12, three times).
        Release everything; the next frame presses again."""
        pl = snap.player
        moving = self.keys.held & {'forward', 'back', 'left', 'right'}
        if not moving or pl is None or pl.anim is None or pl.anim % 1_000_000 != 0:
            self._idle_held = None
            return False
        held = getattr(self, '_idle_held', None)
        if held is None or math.hypot(pl.pos[0] - held[1][0], pl.pos[2] - held[1][2]) > 0.1:
            self._idle_held = (0.0, pl.pos)
            return False
        self._idle_held = (held[0] + dt, held[1])
        if held[0] + dt < REKEY_AFTER:
            return False
        self._idle_held = None
        self.keys.release_all()
        return True

    def _fight_on_the_way(self, snap) -> bool:
        """Walking to the next enemy of a fight order (or a plan's fight), another enemy on the way comes at
        the player: fight it here (the walk doesn't dodge: a hit on the way, twentieth session)."""
        fighting = getattr(self, '_fight_after', None) is not None or getattr(self, '_fight_all', None) \
            or (self.plan.active and self.plan.step is not None and self.plan.step.do == 'fight')
        if not fighting or not snap.player:
            return False
        p = snap.player.pos
        for e in snap.entities:
            if not e.hostile or e.dead or abs(e.pos[1] - p[1]) > 4.0 or math.dist(e.pos, p) > ON_THE_WAY:
                continue
            attacking = any(self.fighter.windows(e.model, a) for a, _t, _l in (e.anims or ()) if a is not None)
            facing = e.facing is not None and ((e.facing[0] * (p[0] - e.pos[0]) + e.facing[2] * (p[2] - e.pos[2]))
                                               / (math.hypot(p[0] - e.pos[0], p[2] - e.pos[2]) or 1.0)) > 0.7
            if attacking or facing:
                self.walk.stop()
                self.fighter.start(e.id, e.name)
                self._fight_after = None
                return True
        return False

    def _kick_planning(self) -> None:
        """A walk without a route for NO_ROUTE_KICK s: restart the companion's planning (a plan that never came
        back left the walk routeless for 10 s, walking into a barricade)."""
        now = time.monotonic()
        if not self.walk.active or self.walk.route or self.companion.route is not None or self.walk.state == 'paused':
            self._routeless_since = None      # (not while paused: two minutes of restarts every 2.5 s, out of focus)
            return
        since = getattr(self, '_routeless_since', None)
        if since is None:
            self._routeless_since = now
        elif now - since > NO_ROUTE_KICK:
            # Only when no plan is running (or one has run far too long): restarting a running plan threw its
            # result away, every 2.5 s, and a plan here takes longer: no route for 12 s (twentieth session).
            planning = getattr(self.companion, '_planning', False)
            began = getattr(self, '_plan_seen_since', None)
            if planning:
                if began is None:
                    self._plan_seen_since = now
                    return
                if now - began < PLAN_TOO_LONG:
                    return
            self._plan_seen_since = None
            self._routeless_since = now
            self.companion._planning = False         # (a stale plan's result is ignored: its generation)
            self.companion._route_time = -1e9
            self.companion._plan_gen += 1
            self.walk.events.append('replan')

    def _walk_trace(self, snap, intent, route) -> None:
        """runtime/walk-<date>.jsonl: where the walk was, what it pressed and why, twice a second and at
        each event; every new route in full (to see from a session why a walk went wrong)."""
        now = time.monotonic()
        new_route = route is not None and route is not getattr(self, '_logged_route', None)
        if not (new_route or self.walk.events or now - getattr(self, '_last_walk_log', 0.0) > 0.5):
            return
        self._last_walk_log = now
        p = snap.player.pos if snap.player else None
        row = {'t': round(now, 3), 'state': self.walk.state, 'events': list(self.walk.events),
               'name': self.walk.name, 'goal': [round(v, 1) for v in self.walk.goal] if self.walk.goal else None,
               'pos': [round(v, 2) for v in p] if p else None, 'keys': sorted(intent.keys),
               'taps': list(intent.taps), 'turn': round(intent.turn, 1),
               'progress': getattr(self.walk, '_progress', None), 'route_points': len(self.walk.route),
               'cam_yaw': round(math.degrees(math.atan2(snap.camera.forward[0], snap.camera.forward[2])), 1)
               if snap.camera else None, 'anim': getattr(snap.player, 'anim', None) if snap.player else None,
               'entity': getattr(self, '_walk_entity', None),
               'planning': getattr(self.companion, '_planning', None), 'clear_line': self.walk.clear_line,
               'companion': getattr(self.companion, 'mode', None)}
        if new_route:
            self._logged_route = route
            pts = getattr(route, 'waypoints', route)
            row['route'] = {'complete': getattr(self.companion, 'route_complete', None),
                            'waypoints': [[round(v, 1) for v in w] for w in pts],
                            'actions': [str(a) for a in (self.companion.route_actions or [])][:20]}
        self._walk_log(row)

    def _walk_heading_point(self, p):
        """The point AHEAD_PROBE metres ahead of the player toward the walk's next waypoint (or its goal)."""
        route, i = self.walk.route, getattr(self.walk, '_progress', 0)
        q = route[min(i + 1, len(route) - 1)] if route else self.walk.goal
        if q is None:
            return None
        d = math.hypot(q[0] - p[0], q[2] - p[2])
        if d < 0.3:
            return None
        return (p[0] + (q[0] - p[0]) / d * AHEAD_PROBE, p[1], p[2] + (q[2] - p[2]) / d * AHEAD_PROBE)

    def _stuck_spot(self, p):
        """The point 1.2 m ahead of the player toward the walk's next waypoint (what it is stuck on)."""
        route, i = self.walk.route, getattr(self.walk, '_progress', 0)
        if not route:
            return None
        q = route[min(i, len(route) - 1)]
        d = math.hypot(q[0] - p[0], q[2] - p[2])
        if d < 0.3:
            q = route[min(i + 1, len(route) - 1)]
            d = math.hypot(q[0] - p[0], q[2] - p[2])
        if d < 0.3:
            return None
        return (p[0] + (q[0] - p[0]) / d * 1.2, p[1], p[2] + (q[2] - p[2]) / d * 1.2)

    def _walk_log(self, row: dict) -> None:
        try:
            path = ROOT / 'runtime' / f'walk-{datetime.now():%Y%m%d}.jsonl'
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, default=str) + '\n')
        except OSError:
            pass

    def _follow_entity(self, snap) -> None:
        """Walking to a character: the walk steers at its live position when close; further away the
        route is re-planned to where it is now once it has moved FOLLOW_MOVED metres."""
        e = next((x for x in snap.entities if x.id == self._walk_entity), None)
        if e is None or e.dead:
            return  # the walk itself notices ("target_gone")
        g = self._route_goal
        if g is None or math.hypot(e.pos[0] - g[0], e.pos[2] - g[2]) > FOLLOW_MOVED:
            self._route_goal = e.pos
            self.companion.guide(e.pos, self.walk.name)
            self._walk_route = None

    def _fight_step(self, snap, dt: float) -> None:
        """Fighting: the fighter's keys and taps every frame (only while the game is in front)."""
        intent = self.fighter.update(snap, dt)
        if snap.screen is not None and snap.screen.focused and self.fighter.active:
            self.keys.set(intent.keys)
            for t in intent.taps:
                self.keys.tap(t)
            if self._rekey(snap, dt):
                self.fighter.events.append('rekey')
            if intent.turn and snap.camera:
                yaw = math.degrees(math.atan2(snap.camera.forward[0], snap.camera.forward[2]))
                self.turner.turn(intent.turn, yaw)
        else:
            self.keys.release_all()
        now = time.monotonic()
        if (now - getattr(self, '_last_fight_log', 0.0) > 0.25 and self.fighter.debug) or self.fighter.events:
            self._last_fight_log = now
            self._fight_log({'t': round(now, 3), 'state': self.fighter.state, 'events': list(self.fighter.events),
                             'keys': sorted(intent.keys), 'taps': list(intent.taps),
                             'focused': bool(snap.screen and snap.screen.focused), 'held': sorted(self.keys.held),
                             **self.fighter.debug})
        if not self.fighter.active and self.fighter.chains_changed:   # a fight ended: keep what it learned
            self._save_chains()
        while self.fighter.events:
            ev = self.fighter.events.pop(0)
            if ev == 'not_focused':
                self._navi_line("Click back into the game and I'll keep fighting!")
                continue
            line = {'won': "Got it! It's down!", 'low_health': "You're hurt! Over to you: heal up!",
                    'target_gone': "It's gone!", 'timeout': "That's taking a while. Over to you!",
                    'next_target': "One down! Now the other one!",
                    'unreachable': "I can't reach that one!",
                    'no_flask': "I can't heal: no flask left, or none in your item slot!",
                    'escape': "Too many! Getting some room!",
                    'surrounded': "Too many of them! Over to you!"}.get(ev)
            if ev == 'unreachable':     # go on as if it were done, with the others
                ev = 'target_gone' if not getattr(self, '_fight_all', None) else 'won'
            if self.plan.active and ev != 'fled' and self.plan.on_event(ev):
                pass                                              # the plan goes on by itself
            elif ev == 'won' and getattr(self, '_fight_all', None):   # 'kill all of them': the next one
                coming = self._coming_at_me(snap)
                got = None if coming is not None else                     self.resolver.character(self._fight_all, max_range=ALL_RANGE, same_level=True,
                                            exclude=self.fighter.unreachable)
                if coming is not None:      # first whatever is coming at us, whatever it is (a wolf, a bat)
                    name = self._enemy_name(coming)
                    self._walk_request('start', tuple(coming.pos), name, coming.id, 'fight')
                    line = 'Got it! ' + (f'Now the {name} coming at us!' if name else 'Now the one coming at us!')
                elif got is not None:
                    self._walk_request('start', got.point, got.name, got.entity_id, 'fight')
                    line = (line + ' ' if line and 'reach' in line else 'Got it! ') + f"Next {got.name or 'one'}!"
                else:
                    self._fight_all = None
                    line = "Got it! That's all of them!"
                    if self._next_order():
                        line = "Got it! That's all of them! Now the next part!"
            elif ev in ('won', 'target_gone') and self._next_order():
                line = "Got it! Now the next part!"
            elif ev in ('low_health', 'timeout', 'fled', 'surrounded'):
                self._fight_all = None if ev != 'fled' else getattr(self, '_fight_all', None)
                if ev != 'fled':
                    self._then = []
            if ev == 'escape':   # a crowd: find open ground to run to (in the background), say so once
                threading.Thread(target=self._plan_escape, daemon=True).start()
                if time.monotonic() - getattr(self, '_last_escape_line', -1e9) < ESCAPE_LINE_EVERY:
                    line = None
                self._last_escape_line = time.monotonic()
            if ev == 'walled':  # a wall between: round it by the navmesh, then the fight again
                tid = self.fighter.target_id
                e = next((x for x in snap.entities if x.id == tid), None)
                if e is not None:
                    self._via_route = tid
                    self._walk_request('start', tuple(e.pos), self.fighter.name, tid, 'fight')
                    line = "There's a wall in the way. Going round!"
            if ev == 'fled':   # it ran off: follow it along the navmesh, fight again on arrival
                tid = self.fighter.target_id
                e = next((x for x in snap.entities if x.id == tid), None)
                n = self._chases.get(tid, 0) + 1
                self._chases[tid] = n
                if e is not None and n <= MAX_CHASES:
                    self._walk_request('start', tuple(e.pos), self.fighter.name, tid, 'fight')
                    line = "It's running! After it!"
                else:
                    line = "It keeps running away! Over to you."
            if line:
                self.companion.follow()
                self._navi_line(line)
        self._walk_events()

    def _ledge_loop(self) -> None:
        """While fighting, 10 times a second: rays straight down LEDGE_STEP and LEDGE_ROLL metres out in 8
        directions; where nothing is hit within LEDGE_DROP metres below the feet, the ground drops away
        (the fighter's straight steps don't use the navmesh: it once walked off a cliff after a deer)."""
        while True:
            time.sleep(0.1)
            moving = self.plan.active and self.plan.step is not None and self.plan.step.do == 'move'
            walking_to = self.walk.active          # (any walk: what is just ahead, and the target if any)
            if not (self.fighter.active or moving or walking_to):
                self._ledges = ((), ())
                continue
            try:
                snap = self.adapter.snapshot()
                if not snap.in_world or snap.player is None:
                    continue
                p = snap.player.pos
                rays = []
                for k in range(8):
                    a = math.radians(k * 45)
                    for dist in (LEDGE_STEP, LEDGE_ROLL):
                        x, z = p[0] + math.sin(a) * dist, p[2] + math.cos(a) * dist
                        rays.append(((x, p[1] + 2.0, z), (x, p[1] - LEDGE_DROP, z)))
                for k in range(8):   # walls and rocks within WALL_NEAR at chest height (backing off)
                    a = math.radians(k * 45)
                    rays.append(((p[0], p[1] + 1.0, p[2]),
                                 (p[0] + math.sin(a) * WALL_NEAR, p[1] + 1.0, p[2] + math.cos(a) * WALL_NEAR)))
                tid = self.fighter.target_id if self.fighter.active else self.walk.target_id
                target = next((e for e in snap.entities if e.id == tid), None)
                if target is not None:   # a clear line from the chest to it (a wall between: no swing)
                    a = (p[0], p[1] + 1.3, p[2])
                    b = target.center
                    d = math.dist(a, b)
                    if d > 0.5:
                        b = tuple(a[i] + (b[i] - a[i]) * (d - 0.4) / d for i in range(3))
                    rays.append((a, b))
                legs_at, legs = None, []
                if self.walk.active and self.walk.route and time.monotonic() - getattr(self, '_last_leg_check', 0.0) \
                        > LEG_CHECK_EVERY:
                    self._last_leg_check = time.monotonic()
                    route, i = self.walk.route, getattr(self.walk, '_progress', 0)
                    pts = [p] + list(route[i + 1:i + 3])
                    legs = [(u, v) for u, v in zip(pts, pts[1:]) if math.hypot(v[0] - u[0], v[2] - u[2]) > 1.0]
                    legs_at = len(rays)
                    for u, v in legs:
                        for h in (0.5, 1.2):
                            rays.append(((u[0], u[1] + h, u[2]), (v[0], v[1] + h, v[2])))
                ahead_at = None
                if self.walk.active:     # just ahead along the walk, knee and chest high (walls; not barrels)
                    q = self._walk_heading_point(p)
                    if q is not None:
                        ahead_at = len(rays)
                        for h in (0.6, 1.2, 0.35):   # (shin high: low walls and rubble; slopes by their normal)
                            rays.append(((p[0], p[1] + h, p[2]), (q[0], p[1] + h, q[2])))
                hits = self.adapter.raycast(rays, timeout=0.5)
                if target is not None:
                    self._los = (target.id, not hits[24].hit)
                low = hits[ahead_at + 2] if ahead_at is not None else None
                self.walk.ahead_blocked = None if ahead_at is None else \
                    any(hits[ahead_at + k].hit for k in range(2)) \
                    or bool(low.hit and low.normal and abs(low.normal[1]) < 0.6)
                if legs_at is not None:
                    for k, (u, v) in enumerate(legs):
                        for j in range(2):
                            h = hits[legs_at + 2 * k + j]
                            if h.hit and h.pos and h.normal and abs(h.normal[1]) < 0.6:
                                n = math.hypot(v[0] - u[0], v[2] - u[2]) or 1.0
                                self._leg_wall = (h.pos, ((v[0] - u[0]) / n, 0.0, (v[2] - u[2]) / n))
                                break
                walls = tuple(float((k * 45 + 180) % 360 - 180) for k in range(8) if hits[16 + k].hit)
                step = tuple(float((k * 45 + 180) % 360 - 180) for k in range(8) if not hits[2 * k].hit)
                roll = tuple(float((k * 45 + 180) % 360 - 180) for k in range(8)
                             if not hits[2 * k].hit or not hits[2 * k + 1].hit)
                self._ledges = (step, roll, walls)
            except Exception:
                self._ledges = ((), ())

    @staticmethod
    def _load_chains() -> dict:
        """Enemy combos learned in fights (core/combat.py), kept across runs: .local/<game>/chains.json."""
        try:
            return json.loads(CHAINS_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_chains(self) -> None:
        try:
            CHAINS_FILE.parent.mkdir(parents=True, exist_ok=True)
            CHAINS_FILE.write_text(json.dumps(self.fighter.chains, indent=1), encoding='utf-8')
            self.fighter.chains_changed = False
        except OSError:
            pass

    def _coming_at_me(self, snap):
        """The nearest enemy of any kind coming at the player (fighter._engaged, out to COMING_RANGE): fought
        before the next one of a 'kill all the soldiers' order (the user: "anything targeting me would get in
        the way")."""
        if not snap.player:
            return None
        p = snap.player.pos
        best = None
        for e in snap.entities:
            if e.id in self.fighter.unreachable or not self.fighter._engaged(e, p, reach=COMING_RANGE):
                continue
            d = math.dist(e.pos, p)
            if best is None or d < best[0]:
                best = (d, e)
        return best[1] if best else None

    def _enemy_name(self, e) -> str | None:
        if e.name:
            return e.name
        try:
            return (self.adapter.knowledge(e.type_id) or {}).get('name') if hasattr(self.adapter, 'knowledge') else None
        except Exception:
            return None

    def _plan_escape(self) -> None:
        """Open ground to run to out of a crowd, and a navmesh route there (the fighter follows it). Candidate
        spots 12 and 18 m away in 16 directions, none near an enemy nor toward them; scored on openness (rays
        all round at chest height: a corner scores low), distance from the enemies and heading away; the best
        few are planned to, the first reachable without a long detour taken. (Twenty-first session: running
        straight away from them backed into an enclosed corner and rolled into its wall again and again.)"""
        try:
            snap = self.adapter.snapshot()
            if not snap.in_world or not snap.player or not hasattr(self.adapter, 'route'):
                return
            p = snap.player.pos
            self.fighter.escape_note = 'planning'
            foes = [e for e in snap.entities if e.hostile and not e.dead and math.dist(e.pos, p) < 25.0]
            close = [math.atan2(e.pos[0] - p[0], e.pos[2] - p[2]) for e in foes if math.dist(e.pos, p) < 10.0]
            cands = []
            for k in range(16):
                a = math.radians(k * 22.5)
                # how far this way is from the nearest enemy's direction (degrees): with them on every side the
                # widest gap still counts (excluding all within 60 degrees of any left none when surrounded)
                clear = min((abs(math.degrees(math.atan2(math.sin(a - b), math.cos(a - b)))) for b in close),
                            default=180.0)
                for dist in (12.0, 18.0):
                    c = (p[0] + math.sin(a) * dist, p[1], p[2] + math.cos(a) * dist)
                    near = min((math.hypot(e.pos[0] - c[0], e.pos[2] - c[2]) for e in foes), default=99.0)
                    if near < 4.0 or clear < 25.0:
                        continue
                    cands.append((c, near, clear))
            if not cands:
                self.fighter.escape_note = 'no open way'
                return
            downs = self.adapter.raycast([((c[0], p[1] + 6.0, c[2]), (c[0], p[1] - 8.0, c[2])) for c, _n, _t in cands],
                                         timeout=1.0)
            grounded = [((c[0], h.pos[1], c[2]), n, t) for (c, n, t), h in zip(cands, downs) if h.hit and h.pos
                        and abs(h.pos[1] - p[1]) < 4.0]
            rays = []
            for c, _n, _t in grounded:
                for j in range(8):
                    b = math.radians(j * 45)
                    rays.append(((c[0], c[1] + 1.0, c[2]), (c[0] + math.sin(b) * 4.0, c[1] + 1.0,
                                                              c[2] + math.cos(b) * 4.0)))
            hits = self.adapter.raycast(rays, timeout=1.0) if rays else []
            scored = []
            for i, (c, near, clear) in enumerate(grounded):
                open_ = sum(1 for h in hits[8 * i:8 * i + 8] if not h.hit) / 8.0
                scored.append((open_ * 3.0 + min(near, 20.0) / 5.0 + min(clear, 90.0) / 45.0, c))
            scored.sort(reverse=True)
            for _score, c in scored[:3]:
                if not self.fighter.active or self.fighter._escaping_since is None:
                    return
                r = self.adapter.route(p, c, budget_s=0.8)
                if r is None or not r.reaches_goal:
                    continue
                length = sum(math.dist(u, v) for u, v in zip(r.waypoints, r.waypoints[1:]))
                if length > 2.0 * math.dist(p, c) + 4.0:
                    continue
                self.fighter.escape_route = list(r.waypoints)
                self.fighter.escape_note = f'route {length:.0f} m'
                return
            self.fighter.escape_note = 'no route'
        except Exception as ex:
            self.fighter.escape_note = f'error {type(ex).__name__}'
            return

    def _poise_of(self, type_id: int | None) -> float | None:
        """The enemy kind's poise (how much it takes to flinch it), from the game's NpcParam."""
        if type_id is None or not hasattr(self.adapter, 'knowledge'):
            return None
        return (self.adapter.knowledge(type_id) or {}).get('poise')

    def _fight_log(self, row: dict) -> None:
        """runtime/combat-<date>.jsonl: what the fighter saw and did, 4 times a second and at each
        roll or swing (to tune timings from real fights)."""
        try:
            path = ROOT / 'runtime' / f'combat-{datetime.now():%Y%m%d}.jsonl'
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, default=str) + '\n')
        except OSError:
            pass

    def _walk_events(self) -> None:
        while self.walk.events:
            ev = self.walk.events.pop(0)
            if ev == 'break':
                self._navi_line("Something's in the way. Let me smash it!")
            elif ev == 'stuck':  # avoid what's here (a gap link that doesn't work) and plan again
                if snap_pos := getattr(self, '_last_player_pos', None):
                    if hasattr(self.adapter, 'avoid_links_near'):
                        self.adapter.avoid_links_near(snap_pos)
                    ahead = self._stuck_spot(snap_pos)
                    if ahead is not None and hasattr(self.adapter, 'avoid_spot'):
                        self.adapter.avoid_spot(ahead)   # what it walked into (a campfire, a rock)
                self.companion.route = None
                self._walk_route = None
                self.walk.set_route([])
                self._navi_line("Hmm, something's in the way. Let me find another path!")
            elif ev == 'attacked':  # one hit landed (or swung): over to the player, still locked on
                self.companion.follow()
                self._navi_line('Got a hit in! Your turn!')
            elif ev == 'target_gone':
                self.companion.follow()
                self._navi_line("It's gone!")
                if self.plan.active:
                    self.plan.on_event(ev)
            elif ev == 'arrived':
                if self._fight_after is not None:  # walked up to it: now the fight
                    self._via_route = None
                    self.fighter.start(self._fight_after, self.walk.name)
                    self._fight_after = None
                elif self.plan.active:
                    self.plan.on_event(ev)
                else:
                    self._navi_line(f"Here we are: {self.walk.name}!" if self.walk.name else 'Here we are!')
                    self._next_order()
            elif ev == 'gave_up':
                self._then = []
                if self.plan.active and self.plan.on_event(ev):
                    continue
                self.companion.follow()
                self._navi_line("I can't get through here. Over to you!")
            elif ev == 'stopped:you':
                self._then, self._fight_all = [], None
                if self.plan.active:
                    self.plan.on_event(ev)
                self._navi_line("Okay, you've got it!")
            elif ev == 'paused:not_focused':
                self._navi_line("Click back into the game and I'll keep going.")

    def ask(self, question: str, source: str) -> None:
        if self.busy:
            # A new instant command still works while an answer is coming (for example "stop").
            reply = commands.handle(question, self.cmd_ctx)
            if reply is not None:
                self._show_exchange(question, source, reply)
            return
        self.busy = True
        self.say('you', '\nYou' + (' (voice)' if source == 'voice' else '') + ': ')
        self.say(None, question + '\n')
        self.say(None, 'Fairy: ' if self.companion else 'Assistant: ')
        threading.Thread(target=self._work, args=(question, source), daemon=True).start()

    def _show_exchange(self, question: str, source: str, reply: str) -> None:
        self.say('you', '\nYou' + (' (voice)' if source == 'voice' else '') + ': ')
        self.say(None, question + '\n')
        if reply:
            self.say(None, reply + '\n')

    # ---- answering (worker thread) ----

    def _work(self, question: str, source: str) -> None:
        record = {'t': datetime.now().isoformat(timespec='seconds'), 'source': source, 'question': question}
        try:
            reply = commands.handle(question, self.cmd_ctx)
            if reply is not None:  # an instant local command: no model call
                record['local_command'] = True
                if reply:
                    self.q.put(('text', reply))
                    if self.bubble:
                        self.bubble.bubble_say(reply)
                    if self.speaker:
                        self.speaker.say(reply)
                self.q.put(('meta', '\n(instant, no cost)\n'))
            else:
                if self.speaker:
                    self.speaker.stop()

                def on_text(s: str) -> None:
                    self.q.put(('text', s))
                    if self.bubble:
                        self.bubble.bubble_add(s)
                    if self.speaker:
                        self.speaker.feed(s)

                if self.bubble:
                    self.bubble.bubble_think()
                try:
                    ans = self.agent.ask(question, on_text=on_text)
                finally:
                    if self.bubble:
                        self.bubble.bubble_done()
                if self.speaker:
                    self.speaker.flush()
                spent = self.agent.guard.last_hour()
                first = f'{ans.first_word_s:.1f} s to first word, ' if ans.first_word_s else ''
                meta = f'\n{first}${ans.usd:.4f}, last hour ${spent:.2f} of ${self.agent.guard.cap:.0f}'
                if ans.tools_used:
                    meta += f', tools: {", ".join(ans.tools_used)}'
                if ans.notice:
                    meta += f'\n{ans.notice}'
                self.q.put(('meta', meta + '\n'))
                record.update(answer=ans.text, tools=ans.tools_used, first_word_s=ans.first_word_s,
                              total_s=round(ans.total_s, 2), usd=round(ans.usd, 6), notice=ans.notice)
        except SpendCapReached as e:
            self.q.put(('meta', f'\n{e}\n'))
            record['error'] = str(e)
        except Exception as e:  # show any API or game error in the window instead of dying
            self.q.put(('meta', f'\nError: {type(e).__name__}: {e}\n'))
            record['error'] = f'{type(e).__name__}: {e}'
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
        self.resolver.heard(question + '\n' + str(record.get('answer') or ''))   # 'kill all of them' after it
        self.q.put(('done', ''))

    # ---- the fairy (its own thread) ----

    def _fairy_loop(self) -> None:
        last = time.perf_counter()
        while self.running:
            start = time.perf_counter()
            try:
                snap = self.adapter.snapshot()
            except (GameNotRunning, RuntimeError):
                self.overlay.hide()
                self.overlay.set_bounds(None)
                time.sleep(1.0)
                last = time.perf_counter()
                continue
            self.companion.speaking = bool(self.speaker and self.speaker.speaking)
            if hasattr(self.adapter, 'observe'):  # the route planner learns where the player goes
                self.adapter.observe(snap)
            frame_dt = min(0.1, max(1e-3, start - last))   # the real time since the last frame (when frames are
            view = self.companion.update(snap, start - last)   # slow, the fighter's timing must still be real)
            self.last_view = view
            while self.companion.events:  # e.g. arrived somewhere it was guiding to
                event = self.companion.events.pop(0)
                if event.startswith('level:'):
                    _, name, dy = event.split(':', 2)
                    dy = int(dy)
                    line = (f"It's right below us, about {abs(dy)} metres down! There must be a way down nearby." if dy < 0
                            else f"It's right above us, about {dy} metres up! There must be a way up nearby.")
                    self.q.put(('meta', '\n' + line + '\n'))
                    if self.bubble:
                        self.bubble.bubble_say(line)
                    if self.speaker:
                        self.speaker.say(line)
                    continue
                if event.startswith('action:'):  # a ladder, lift, jump or drop just ahead on the route
                    line = ACTION_LINES.get(event.split(':', 1)[1])
                    if line:
                        self.q.put(('meta', '\n' + line + '\n'))
                        if self.bubble:
                            self.bubble.bubble_say(line)
                        if self.speaker:
                            self.speaker.say(line)
                    continue
                if event.startswith('no_way:'):
                    line = f"Hmm, I can't find a way to walk toward {event.split(':', 1)[1]} from here. Let's try another way!"
                    self.q.put(('meta', '\n' + line + '\n'))
                    if self.bubble:
                        self.bubble.bubble_say(line)
                    if self.speaker:
                        self.speaker.say(line)
                    continue
                if event.startswith('arrived:'):
                    if self.walk.active:  # walking there: say it when the walk itself arrives
                        continue
                    line = f"Here we are: {event.split(':', 1)[1]}!"
                    self.q.put(('meta', '\n' + line + '\n'))
                    if self.bubble:
                        self.bubble.bubble_say(line)
                    if self.speaker:
                        self.speaker.say(line)
            last = start
            try:
                self._walk_step(snap, frame_dt)
            except Exception as e:  # never leave keys held down
                if self.keys:
                    self.keys.release_all()
                self.walk.stop()
                self.q.put(('meta', f'\nAuto-walk error: {type(e).__name__}: {e}\n'))
            in_front = snap.screen is not None and (snap.screen.focused or _own_window_in_front())
            if view.visible and snap.screen and hasattr(self.speaker, 'pan'):  # the voice comes from the fairy
                self.speaker.pan = ((view.screen_x - snap.screen.x) / max(1, snap.screen.width)) * 2 - 1
            scr = snap.screen
            self.overlay.set_bounds((scr.x, scr.y, scr.width, scr.height) if in_front else None)
            if in_front and (view.visible or view.trail or view.edge):
                main = (view.screen_x, view.screen_y, view.size_px, view.opacity, view.speaking) if view.visible else None
                self.overlay.show_parts(main, view.trail, view.edge)
            else:
                self.overlay.hide()
            took = time.perf_counter() - start
            self.fairy_ms = self.fairy_ms * 0.95 + took * 1000 * 0.05
            time.sleep(max(0.0, 1 / FPS - took))

    # ---- tk side ----

    def _pump(self) -> None:
        try:
            while True:
                kind, s = self.q.get_nowait()
                if kind == 'text':
                    self.say(None, s)
                elif kind == 'meta':
                    self.say('meta', s)
                elif kind == 'voice':
                    self.ask(s, 'voice')
                elif kind == 'order':
                    self.order(s, 'voice')
                elif kind == 'voice_state':
                    self._voice_state = s
                else:
                    self.busy = False
        except queue.Empty:
            pass
        parts = []
        if self._code_changed():
            parts.append('NEW VERSION ON DISK: restart the assistant')
        state = getattr(self, '_voice_state', 'loading')
        parts.append({'loading': 'voice: loading speech model...', 'ready': f'hold {self.cfg["push_to_talk_key"]} to talk',
                      'listening': 'listening...', 'thinking': 'hearing you...'}.get(state, state))
        if self.companion:
            route = f' ({self.companion.route_source} route)' if self.companion.mode == 'guide' and self.companion.route else ''
            parts.append(f'fairy: {self.companion.mode}{route}{" (waiting at the leash)" if self.companion.waiting else ""}, '
                         f'{self.fairy_ms:.1f} ms/frame')
            if self.overlay and self.overlay.error:
                parts.append(f'overlay error: {self.overlay.error}')
        if self.speaker and self.speaker.error:
            parts.append(f'voice output unavailable: {self.speaker.error}')
        self.status.configure(text='   |   '.join(parts))
        self.root.after(30, self._pump)

    def close(self) -> None:
        self.running = False
        if self.overlay:
            self.overlay.close()
        if self.speaker:
            self.speaker.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    names = [x for x in sys.argv[1:] if not x.startswith('-')]
    try:
        App(names[0] if names else 'eldenring').run()
    except KeyError as e:
        sys.exit(e.args[0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
