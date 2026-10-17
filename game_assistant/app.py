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
from .core.autowalk import AutoWalk, cam_yaw
from .core.combat import Fighter
from .core.tasks import TaskRunner
from .core.memory import WorldMemory
from .core.goals import GoalRunner
from .core.skills import DANGER, INTERRUPTED, NO_TARGET, TIMEOUT, UNREACHABLE, needs_blocks, route_reason
from .core.plan import PlanRunner, steps_from
from .core.walk_commands import CommandResolver, WalkAction
from .core.companion import Companion
from .core.interface import CAP_CAMERA, CAP_NAVMESH, CAP_RAYCAST, CAP_SCREEN, GameNotRunning
from .core.spend import SpendCapReached
from .games import adapter_for

ROOT = Path(__file__).resolve().parents[1]
ONCE_LINE = 30.0     # seconds before the same passing line (no way here, it's above us) is said again
ERROR_AGAIN = 30.0   # seconds before the same error in a frame loop is shown again (it's logged once)
FPS = 60


def _work_now(walk):
    """For the walk log: what the walk is at (a block broken or put down: its key; a step up: to where)."""
    sp = getattr(walk, '_special', None)
    act = sp[0] if sp else None
    if act is None:
        return None
    return {'what': act.what, 'key': act.info.get('key') or act.info.get('into'), 'blocker': getattr(act, 'blocker', None),
            'phase': getattr(act, 'phase', '')}


def _game_pos(adapter, p):
    """A snapshot position in the game's own coordinates (as the memory keeps them)."""
    fn = getattr(adapter, 'game_coords', None)
    return fn(p) if fn is not None else tuple(p)


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
_ATTACK_WORDS = re.compile(r"\b(kill|attack|fight|hit|strike|defeat|beat|take (?:down|out)|finish)\b", re.I)
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
CHAINS_FILE = '.local/{game}/chains.json'   # learned enemy combos (and the rest the fighter learns), per game
ALL_RANGE = 60.0                    # 'kill all of them': the next one within this many metres                      # a target that ran off this often: hand back
FOLLOW_SLACK = 1.5   # blocks a followed character may get past its distance before it's walked after again
ACT_HINT = (" (act takes only one simple order of the kinds listed; moving anywhere, jumps and climbs and how"
            " (without placing blocks, without digging) included, is go_to and its options; whether a way can be"
            " made is check_way)")
WALK_SPEED_MPS = 4.3   # a walk's pace, for the doer's estimates (Minecraft: blocks a second)
BLOCK_SOURCES = ['minecraft:dirt', 'minecraft:cobblestone', 'minecraft:netherrack']   # blocks to build with, got
STUCK_REACH = 6.0   # blocks: stuck, what's got to get out is got from this near (or made from what's held)
# how a fight's end reads for the doer: done, or failed with this Reason kind
FIGHT_ENDS = {'won': (), 'low_health': (DANGER,), 'surrounded': (DANGER,), 'outmatched': (DANGER,),
              'held_off': (DANGER,),
              'target_gone': (NO_TARGET,),
              'timeout': (TIMEOUT,), 'unreachable': (UNREACHABLE,)}
BLOCKS_SPARE = 4      # blocks got beyond what the way takes
NO_PROGRESS_S = 20.0  # seconds a walk may go without getting nearer its goal (or digging or building) before it gives up
AWARE_AGAIN = 6.0   # seconds before that's done again (the fight over, on with the walk)
OPEN_EVERY = 0.5    # seconds between looks at the room round the player on a walk (the last open ground passed)
SIGHT_EVERY = 0.5    # seconds between looks at whether those shooting can see the player
COVER_AGAIN = 8.0    # seconds before cover is looked for again
HIDE_CALM = 3.0      # seconds the shooter's given up (bow down, or not set on the player) before going on
COVER_SEEN_S = 0.6   # seconds seen by it in cover before that cover's given up (another looked for)...
BAD_COVER_S = 60.0   # ...and that spot not taken again for this long
AWAY_TO = 24.0       # blocks gone, getting away from those coming
AWAY_CLEAR = 18.0    # ...and how far behind they're to be to go on
IDLE_GUARD = 3.0     # seconds the player's not at the controls before what's dangerous is dealt with for them
FOLLOWED_NEAR = 5.0  # blocks: one up close after the player this near in cover is fought there
HIDE_MAX = 10.0      # seconds in cover at most
GEAR_MAX_S = 180.0  # seconds' getting (the planner's reckoning) a fight's gear may take before it's fought without
ERRAND_FOES = 24.0  # blocks: an errand set about with a hostile one this near gets what a fight with it wants first
ERRAND_GEAR_S = 40.0  # seconds' getting a weapon may take, an errand set about among enemies (else without)
ERRAND_HURT = 0.5   # ...and, health under this much of full with food to hand, a bite first too
OPEN_BACK = 14.0    # blocks: open ground this near is gone back to, to fight from
FIGHT_FROM = 12.0  # metres: a "fight" order this close starts fighting at once (else walk up first)
FOLLOW_MOVED = 3.0  # metres a character may move before the route to it is re-planned
ACTION_LINES = {'ladder': 'Climb the ladder!', 'lift': 'Take the lift!', 'jump': 'Jump across here!',
                'drop': 'Drop down here!'}


class App:
    def __init__(self, game: str):
        self.cfg = config.load()
        self.adapter = adapter_for(game)
        # (route planning in a process of its own, where the game has one: never holds up the frame loop)
        getattr(self.adapter, 'plan_in_worker', lambda: None)()
        self._chains_file = ROOT / CHAINS_FILE.format(game=game)
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
        self.walk = AutoWalk(sprint=bool(self.cfg['auto_walk_sprint']) and getattr(self.adapter, 'walk_sprint', True),
                             breaks=getattr(self.adapter, 'walk_breaks', True),
                             precise=getattr(self.adapter, 'walk_precise', False))
        self.walk.probe = getattr(self.adapter, 'block_solid', None)
        self.walk.aimer = getattr(self.adapter, 'aim_point', None)
        self.walk.mover = self.adapter.mover() if hasattr(self.adapter, 'mover') else None   # (the game's moves)
        if self.companion is not None:
            self.companion.on_error = self._error                     # (a route plan that raised: logged)
        if self.companion is not None:
            self.companion.walk_decides = self.walk.precise
            self.companion.shape_fn = getattr(self.adapter, 'goal_shape', None)
        # Fighting for the player ("fight that soldier"): dodges by the game's own attack timings.
        self._ledges: tuple = ((), ())    # where the ground drops away around the player (_ledge_loop)
        self._chases: dict = {}           # entity -> times a fight handed over to a chase (it ran)
        self.companion.plan_close = True  # the character walks by itself: a route even to close goals
        self.plan = PlanRunner(self)      # orders of several parts (core/plan.py)
        self.task = TaskRunner(self)      # getting items: gather and craft (core/tasks.py)
        self.memory = WorldMemory()        # what was done and learned this session (core/memory.py)
        self.adapter.memory = self.memory
        self.goal = GoalRunner(self._plan_goal, self._step_cost)   # item orders, planned again (core/goals.py)
        self.walk.dug = []
        self._task_cmd = None
        self._plan_cmd = None
        self._los: tuple = (None, None)    # (target, a clear line to it) from _ledge_loop
        self.fighter = Fighter(getattr(self.adapter, 'attack_windows', lambda m, a: []), self._poise_of,
                               lambda: self._ledges, self._load_chains(),
                               lambda eid: self._los[1] if self._los[0] == eid else None,
                               reach_of=getattr(self.adapter, 'attack_reach', lambda m, t, a: None),
                               weapon=getattr(self.adapter, 'player_weapon', lambda: None))
        if getattr(self.adapter, 'fighter_style', None) == 'melee':   # a charged swing aimed with the camera
            from .core.melee import MeleeFighter
            self.fighter = MeleeFighter(getattr(self.adapter, 'combat_profile', lambda e: {}),
                                        getattr(self.adapter, 'weapon_slot', lambda h: None),
                                        getattr(self.adapter, 'terrain', lambda: {}),
                                        getattr(self.adapter, 'route', None),
                                        getattr(self.adapter, 'weapon_damage', lambda h: None),
                                        getattr(self.adapter, 'sight_pairs', None))
        self._fight_after = None          # the walk is going to a character to fight it
        self.resolver = CommandResolver(self.adapter, ask_model=self._model_order)
        self.doer = None              # orders thought through step by step (core/doer.py), where the game has it
        if getattr(self.adapter, 'use_doer', False) and getattr(self, 'agent', None) is not None:
            from .core.doer import Doer
            self.doer = Doer(self, self.adapter.game, getattr(self.adapter, 'doer_examples', ''),
                             notes='\n'.join(n for n in (getattr(self.adapter, 'prompt_notes', ''),
                                                         getattr(self.adapter, 'doer_notes', '')) if n),
                             web_sources=getattr(self.adapter, 'web_sources', ()) or (), guard=self.agent.guard)
        self._walk_cmd = None             # ('start', point, name) or ('stop', why), applied by the fairy loop
        self._walk_route = None           # the companion route the walk follows
        self._route_goal = None           # where the route to a moving character was planned to
        self._took_over = 0.0             # when the player last pressed a movement key themselves
        self.keys = self.turner = None
        if self.cfg['auto_walk'] and self.companion and sys.platform == 'win32':
            from .ui.win_input import Keys, KeyWatch, Turner
            # the defaults, then the game's own (Minecraft sprints on Ctrl, jumps on Space), then what the user changed
            mine = {k: v for k, v in self.cfg['walk_keys'].items() if config.DEFAULTS['walk_keys'].get(k) != v}
            self.keys = Keys({**config.DEFAULTS['walk_keys'], **(getattr(self.adapter, 'default_keys', None) or {}),
                              **mine})
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
            self._order_work(nxt, queued=True)
            if rest and not getattr(self, '_then', None):   # the part didn't queue its own: keep the rest
                self._then = rest
        threading.Thread(target=work, daemon=True).start()
        return True

    def _order_work(self, text: str, from_plan_queue: bool = False, queued: bool = False) -> None:
        if not getattr(self, '_bridge_checked', False) and hasattr(getattr(self, 'adapter', None), 'bridge_note'):
            self._bridge_checked = True
            try:
                note = self.adapter.bridge_note()
            except Exception:
                note = None
            if note:
                self._navi_line(note)
        doer = getattr(self, 'doer', None)
        if doer is not None and getattr(self, '_doer_busy', False):
            doer.cancel()                              # a new order replaces the one being thought through
        if doer is not None and not from_plan_queue and needs_plan(text):
            return self._doer_order(text)
        if not from_plan_queue and needs_plan(text) and self._can_plan():
            return self._plan_order(text)
        try:
            action = self.resolver.handle(text) if doer is None else self.resolver.handle(text, use_model=False)
        except Exception as e:  # show it instead of dying
            action = WalkAction('none', f"Hmm, I couldn't work that out ({type(e).__name__}).")
        if doer is not None and action.kind == 'none' and not from_plan_queue:
            return self._doer_order(text)              # the rules didn't get it: think it through
        if action.kind == 'none' and queued and _ATTACK_WORDS.search(text):
            # the next part of an order, its enemy not around (killed already: 'kill the bat and the soldier'
            # when the soldier attacked first and went down; 2026-10-12 21:35 it started a search instead)
            self._navi_line(action.say)
            if not self._next_order():
                self._navi_line("That's everything!")
            return
        if action.kind == 'none' and not from_plan_queue and self._can_plan():
            return self._plan_order(text)              # the rules didn't get it: the planner might
        self._start_action(action, text)
        self._navi_line(action.say)
        # the order and the answer are part of the conversation: 'two more', 'them too' a minute later mean
        # these (2026-10-12: 'kill just two more' after soldiers became 'any enemy' twice)
        self.resolver.heard(f'Player ordered: {text}\nNavi: {action.say}')
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'t': datetime.now().isoformat(timespec='seconds'), 'order': text,
                                'action': action.kind, 'place': action.name}) + '\n')

    # ---- thinking orders through (core/doer.py) ----

    def _doer_order(self, text: str) -> None:
        # (no "Okay! Let me think..." first: the doer's first line comes in a moment, or "Working out how to do
        # that..." if it takes a while)
        self._doer_busy = True
        me = self._doer_turn = getattr(self, '_doer_turn', 0) + 1     # (a newer order ends this one)
        record = {'t': datetime.now().isoformat(timespec='seconds'), 'order': text, 'action': 'doer'}
        steps: list = []
        earlier = list(getattr(self, '_open_orders', []))   # (cut short by this one, or before)
        self._open_orders = earlier + [text]
        try:
            record['said'] = self.doer.run(text, self.resolver.recent(), log=steps)
            if self._doer_turn == me:                    # (not cut short itself: this one's over)
                became = getattr(self.doer, 'earlier', None)
                record['earlier'] = became
                self._open_orders = earlier if became == 'open' else []
        except SpendCapReached as e:
            self._navi_line(str(e), urgent=True)
            record['error'] = str(e)
        except Exception as e:  # show it (urgent: while the doer's busy, other lines are kept back) instead of dying
            self._navi_line(f"Sorry, I got stuck working that out ({type(e).__name__}).", urgent=True)
            record['error'] = f'{type(e).__name__}: {e}'
            self._error('Doer', e)
        finally:
            if self._doer_turn == me:
                self._doer_busy = False
        record['steps'] = steps[-40:]
        self.resolver.heard(f'Player ordered: {text}\nNavi: {record.get("said") or ""}')
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str) + '\n')

    def doer_state(self) -> str:
        bits = []
        try:
            snap = self.adapter.snapshot()
        except Exception:
            return 'the game is not reachable'
        if snap.player:
            if snap.player.health is not None:
                bits.append(f'health {snap.player.health:.0f}/{snap.player.max_health or 0:.0f}')
        if hasattr(self.adapter, 'where_am_i'):
            try:
                w = self.adapter.where_am_i()
                bits.append('where: ' + ', '.join(f'{k} {v}' for k, v in w.items() if v not in (None, '')))
            except Exception:
                pass
        mem = getattr(self, 'memory', None)
        if mem is not None and snap.player:
            bits.append('done this session: ' + mem.summary(_game_pos(self.adapter, snap.player.pos)))
        open_ = [o for o in getattr(self, '_open_orders', [])[:-1]][-4:]
        if open_:                                        # (a newer order cut them short: carried on, unless replaced)
            bits.append('unfinished earlier orders (oldest first): ' + '; '.join(f'"{o}"' for o in open_))
        inv = (snap.extra or {}).get('inventory')
        if inv is not None:
            bits.append('holding: ' + (', '.join(f'{n} {k.split(":")[-1]}' for k, n in inv.items()) or 'nothing'))
        armed = self._armed(snap)
        if armed:
            bits.append(armed)
        death = (snap.extra or {}).get('last_death')
        if death:
            bits.append(f'last died at {death[0]} {death[1]} {death[2]}')
        try:
            bits.append('around: ' + self.agent._characters_around(8))
        except Exception:
            pass
        return '; '.join(bits)

    def doer_act(self, order: str):
        try:
            # (the rules only: what they don't find, the doer is told and thinks on; a second model picking
            # "the nearest thing like it" went for an Enderman for "kill the zombie", 2026-10-13)
            action = self.resolver.handle(order, use_model=False)
        except Exception as e:
            return f'{type(e).__name__}: {e}'
        if action.kind in ('none', 'ask'):
            # (what it didn't get, and what to use instead: "back up and sprint-jump onto the crafting table"
            # three times over, each "I didn't catch where to go", 2026-10-16)
            return (action.say or "didn't understand that order") + ACT_HINT
        self._start_action(action, order)
        self._navi_line(action.say)
        return None

    def doer_tools(self) -> set:
        """The doer's typed tools this game has (core/doer.py TYPED_TOOLS)."""
        a = self.adapter
        have = {'go_to', 'fight', 'situation'}
        if hasattr(a, 'match_item'):
            have.add('put_down')
            if hasattr(a, 'book'):
                have |= {'get', 'how_to_get'}
        if hasattr(a, 'coords_point') and getattr(a, 'can_break', False):
            have.add('mine_at')
        if hasattr(a, 'explore_point'):
            have |= {'flee', 'explore'}
        if hasattr(a, 'food_slot'):
            have.add('eat')
        if hasattr(a, 'hotbar_layout'):
            have.add('inventory')
        if hasattr(a, 'equipment'):
            have.add('equip')
        if hasattr(a, 'coords_point') and hasattr(a, 'aim_point'):
            have.add('use')
        if hasattr(a, 'items_near'):
            have.add('pickup')
        if hasattr(a, 'game_coords') and hasattr(a, 'cells_now'):
            have.add('tunnel')
        have.add('follow')
        if hasattr(a, 'cells_now') and hasattr(a, 'place_spot'):
            have.add('build')
        if hasattr(a, 'learn'):
            have.add('learn')
        return have

    def doer_do(self, tool: str, args: dict):
        """A typed order (no words read): started, or why not (which one, when there are two alike)."""
        try:
            action = self.resolver.typed(tool, args)
        except Exception as e:
            return f'{type(e).__name__}: {e}'
        if action.kind == 'ask' and action.options:
            return 'which one? ' + '; '.join(
                f"the {n} at {' '.join(str(round(v)) for v in _game_pos(self.adapter, p))}" for n, p in action.options)
        if action.kind in ('none', 'ask'):
            return action.say or 'not done'
        self._start_action(action, f'{tool} {json.dumps(args)}')
        self._navi_line(action.say)
        return None

    def doer_learn(self, way: dict) -> str:
        """A way to get an item the game's data doesn't have, from the wiki: kept (the game's learned ways), and
        the recipe book read again with it."""
        fn = getattr(self.adapter, 'learn', None)
        if fn is None:
            return 'nothing to learn into in this game'
        try:
            return fn(way)
        except Exception as e:
            return f'{type(e).__name__}: {e}'

    def doer_inventory(self) -> dict:
        return self._held()

    def doer_waypoint(self, args: dict) -> str:
        """Named places to come back to (core/waypoints.py): set, get, list, delete; as data."""
        from .core.waypoints import Waypoints
        if getattr(self, 'waypoints', None) is None:
            game = str(getattr(self.adapter, 'game', 'game')).lower().replace(' ', '_')
            self.waypoints = Waypoints(ROOT / '.local' / game / 'waypoints.json')
        act, name = str(args.get('action') or 'list'), str(args.get('name') or '').strip()
        try:
            snap = self.adapter.snapshot()
        except Exception:
            snap = None
        try:
            if act == 'set':
                xyz = [args.get(k) for k in ('x', 'y', 'z')]
                if None in xyz:
                    if snap is None or snap.player is None:
                        return json.dumps({'error': "I can't see where you are"})
                    xyz = _game_pos(self.adapter, snap.player.pos)
                return json.dumps({'set': self.waypoints.set(name, xyz, snap.area if snap else None)})
            if act == 'get':
                got = self.waypoints.get(name)
                return json.dumps(got or {'error': f'no waypoint called {name!r}',
                                          'have': [m['name'] for m in self.waypoints.all()]})
            if act == 'delete':
                return json.dumps({'deleted': self.waypoints.delete(name)})
            out = {'waypoints': self.waypoints.all()}
            death = ((snap.extra or {}) if snap else {}).get('last_death')
            if death:
                out['last_death'] = {'x': death[0], 'y': death[1], 'z': death[2], 'where': death[3] if len(death) > 3
                                     else None}
            return json.dumps(out)
        except ValueError as e:
            return json.dumps({'error': str(e)})

    def doer_check_way(self, args: dict) -> str:
        """Whether the player could get to a spot and how (the planner's way; nothing done): data."""
        fn = getattr(self.adapter, 'way_check', None)
        if fn is None:
            return json.dumps({'error': 'no way checks in this game'})
        xyz = [args.get(k) for k in ('x', 'y', 'z')]
        target = tuple(float(v) for v in xyz) if None not in xyz else (args.get('target') or 'that')
        try:
            most = args.get('most_jumps')
            return json.dumps(fn(target, args.get('place_blocks') is not False, args.get('dig') is not False,
                                 **({'most_jumps': int(most)} if most is not None else {})))
        except Exception as e:
            return json.dumps({'error': f'{type(e).__name__}: {e}'})

    def doer_how_to_get(self, item: str, count: int) -> str:
        """The plan `get` would follow from here, each step with about how long it takes; or why not."""
        from .core.tasks import step_text
        try:
            got = self.resolver.get_item(item, count)
        except Exception as e:
            return json.dumps({'error': f'{type(e).__name__}: {e}'})
        if got is None:
            return json.dumps({'error': f'no item called {item!r} in this game'})
        if got.kind != 'task':
            return json.dumps({'can': False, 'why': got.say})
        rows = [{'step': step_text(s), 'seconds': round(self._step_cost(s))} for s in got.steps]
        return json.dumps({'item': got.name, 'count': got.count, 'steps': rows,
                           'seconds': sum(r['seconds'] for r in rows)})

    def doer_situation(self) -> str:
        """The situation as data (docs/AGENT.md stage 4): health, food, where, held, what was done this
        session, the order under way and its plan, what's around."""
        try:
            snap = self.adapter.snapshot()
        except Exception:
            return json.dumps({'error': 'the game is not reachable'})
        out = {}
        pl = snap.player
        if pl is not None and pl.health is not None:
            out['health'] = f'{pl.health:.0f}/{pl.max_health or 0:.0f}'
        extra = snap.extra or {}
        if extra.get('food') is not None:
            out['food'] = f"{extra['food']}/20"
        if hasattr(self.adapter, 'where_am_i'):
            try:
                out['where'] = self.adapter.where_am_i()
            except Exception:
                pass
        if extra.get('inventory') is not None:
            out['holding'] = {k.split(':')[-1]: n for k, n in extra['inventory'].items()}
        mem = getattr(self, 'memory', None)
        if mem is not None and pl is not None:
            out['this_session'] = mem.as_data(_game_pos(self.adapter, pl.pos))
        task, goal = getattr(self, 'task', None), getattr(self, 'goal', None)
        if task is not None and task.active:
            out['under_way'] = {'steps': task.describe(), 'step': task.i + 1, 'of': len(task.steps)}
            if goal is not None and goal.active:
                out['under_way']['goal'] = f'{goal.total} {goal.item.split(":")[-1]}'
        elif self.walk.active:
            out['under_way'] = {'walk_to': self.walk.name or 'a spot', 'state': self.walk.state}
        elif self.fighter.active:
            out['under_way'] = {'fighting': self.fighter.name or 'an enemy'}
        try:
            out['around'] = self.agent._characters_around(8)
        except Exception:
            pass
        return json.dumps(out, default=str)

    def _ended_as(self, ok: bool, kind: str | None = None, **data) -> None:
        """How a walk or a fight ended, as data, for the doer (a task keeps its own Outcome)."""
        self._ended = (time.monotonic(), {'ok': ok, 'why': {'kind': kind, **data} if kind else None})

    def _armed(self, snap) -> str | None:
        """What the player has to fight with ('armed: stone sword (5 a hit)', 'armed: nothing, fists only'): the
        doer weighs a fight or a night walk by it (just after dying, bare-handed, "off we go" among four zombies,
        2026-10-16)."""
        a = getattr(self, 'adapter', None)
        if a is None or not hasattr(a, 'weapon_damage') or not hasattr(a, 'weapon_slot'):
            return None
        hot = (snap.extra or {}).get('hotbar') or []
        try:
            slot, dmg = a.weapon_slot(hot), a.weapon_damage(hot)
        except Exception:
            return None
        item = (hot[slot][0] if slot is not None and slot < len(hot) and hot[slot] else '') or ''
        if not item or (dmg or 0) <= 1.0:
            return 'armed: nothing, fists only'
        return f"armed: {item.split(':')[-1].replace('_', ' ')} ({dmg:g} a hit)"

    def doer_busy(self) -> bool:
        return bool(self.walk.active or self.fighter.active or getattr(self, 'task', None) and self.task.active
                    or getattr(self, '_walk_cmd', None) or getattr(self, '_task_cmd', None))

    def doer_outcome(self, since: float):
        """How the task the doer's last act ran ended, as data: {'ok', 'why': {kind, ...}}; None: no task ended."""
        task = getattr(self, 'task', None)
        o = getattr(task, 'outcome', None)
        t_task = getattr(task, 'ended_at', 0.0) if o is not None else -1e18
        t_end, ended = getattr(self, '_ended', (-1e18, None))
        if max(t_task, t_end) < since:
            return None
        if t_end > t_task:                               # (a walk or a fight, after the last task)
            return ended
        return {'ok': o.ok, 'why': ({'kind': o.reason.kind, **o.reason.data} if o.reason else None)}

    def doer_said_since(self, t: float) -> list:
        return [line for at, line in getattr(self, '_said', []) if at >= t]

    def doer_where(self, name: str) -> str:
        """Where the nearest few of something are, for the doer to weigh: places, kinds of block and creatures
        (in sight, and anywhere loaded), each with how far across and up or down, and about how long getting
        there takes (walking, plus digging straight there with what's held: a zombie "16 blocks away" was 42
        down, and digging after it took minutes, 2026-10-13)."""
        try:
            me = self.adapter.snapshot().player.pos
        except Exception:
            me = None
        rows = []
        try:
            res = self.adapter.locate(name)
        except Exception as e:
            res = {'error': type(e).__name__}
        for r in res.get('results', [])[:4]:
            rows.append({k: r.get(k) for k in ('name', 'kind', 'distance_m', 'compass', 'height_diff_m',
                                               'coordinates')} | {'_pos': r.get('position')})
        got = self.resolver.character(name)
        if got is not None and got.point is not None and me is not None:
            # (a dropped item is no creature: "Wooden Pickaxe, creature in sight", and with no coordinates the
            # doer's go_to there couldn't start, 2026-10-16)
            try:
                ent = next((e for e in self.adapter.snapshot().entities if e.id == got.entity_id), None)
            except Exception:
                ent = None
            lying = ent is not None and bool((ent.extra or {}).get('item'))
            at = _game_pos(self.adapter, got.point)
            rows.insert(0, {'name': got.name, 'kind': 'item lying on the ground' if lying else 'creature in sight',
                            'distance_m': round(math.hypot(got.point[0] - me[0], got.point[2] - me[2])),
                            'height_diff_m': round(got.point[1] - me[1]),
                            'coordinates': {'x': math.floor(at[0]), 'y': math.floor(at[1] + 0.01),
                                            'z': math.floor(at[2])},
                            '_pos': tuple(got.point)})
        for row in rows[:4]:
            pos = row.pop('_pos', None)
            walk_s = (row.get('distance_m') or 0) / WALK_SPEED_MPS
            dig_s = 0.0
            if pos is not None and abs(row.get('height_diff_m') or 0) > 3 and hasattr(self.adapter, 'dig_estimate'):
                try:
                    est = self.adapter.dig_estimate(pos)
                except Exception:
                    est = None
                if est:
                    row['digging_straight_there'] = est
                    dig_s = est.get('seconds_with_what_is_held') or 0.0
            row['about_seconds_to_get_there'] = round(walk_s + dig_s)
        for row in rows[4:]:
            row.pop('_pos', None)
        return json.dumps(rows[:6] or {'error': res.get('error', 'not found')})

    def doer_survey(self, trees_within: int = 24, land_within: int = 96):
        if not hasattr(self.adapter, 'survey'):
            return 'no survey in this game'
        try:
            return json.dumps(self.adapter.survey(trees_within=max(4, min(64, trees_within)),
                                                  land_within=max(16, min(120, land_within))))
        except Exception as e:
            return f'{type(e).__name__}: {e}'

    def doer_progress(self) -> str | None:
        """A line on how a long order is going (spoken every so often while it runs)."""
        try:
            snap = self.adapter.snapshot()
        except Exception:
            return None
        if self.fighter.active:
            return f"Still fighting the {self.fighter.name or 'enemy'}!"
        if getattr(self, 'task', None) is not None and self.task.active:
            step = self.task.steps[self.task.i] if self.task.i < len(self.task.steps) else None
            if step is not None:
                # (a craft step holds a Recipe, not a name: reading it as one stopped an order, 2026-10-13)
                item = step[1].result if step[0] in ('craft', 'smelt') else step[2] if step[0] == 'place' \
                    else str(step[1])
                name = item.split(':')[-1].replace('_', ' ')
                what = {'gather': f'getting {name}', 'station': f'setting up the {name}', 'craft': f'crafting {name}',
                        'smelt': f'smelting {name}', 'place': 'building', 'dig': 'clearing the way'}
                return f"Still {what.get(step[0], 'working')}: step {self.task.i + 1} of {len(self.task.steps)}."
        if self.walk.active and snap.player and self.walk.goal:
            p, g = snap.player.pos, self.walk.goal
            left = round(math.hypot(g[0] - p[0], g[2] - p[2]))
            down = round(p[1] - g[1])
            doing = {'breaking': 'digging', 'building': 'building my way', 'waiting': 'working out the way'}.get(
                self.walk.state, 'on the way')
            depth = f', {down} down' if down > 3 else f', {-down} up' if down < -3 else ''
            return f"Still {doing} to the {self.walk.name or 'spot'}: {left} blocks across{depth}."
        return None

    def doer_log(self, row: dict) -> None:
        """runtime/doer-<date>.jsonl: each step of an order being thought through, as it happens (what it
        thought, what it did, what came of it)."""
        try:
            path = ROOT / 'runtime' / f'doer-{datetime.now():%Y%m%d}.jsonl'
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'t': datetime.now().isoformat(timespec='seconds'), **row}, default=str) + '\n')
        except OSError:
            pass

    def doer_say(self, line: str) -> None:
        self._navi_line(line, urgent=True)

    def doer_stop(self) -> None:
        self._walk_request('stop', 'stop')
        self._task_cmd = 'stop'

    def _gear_first(self, action) -> bool:
        """A fight order against one the game says wants gear (a shield against one that shoots): got first, when
        it's quick enough (the planner's reckoning), then the fight (the user: "it should get a shield before trying
        to kill a skeleton"). True when it went to get it."""
        if getattr(getattr(self, 'adapter', None), 'fight_gear', None) is None or action.kind != 'walk' \
                or not action.attack or action.entity_id is None:
            return False
        try:
            snap = self.adapter.snapshot()
            e = next((x for x in snap.entities if x.id == action.entity_id), None)
        except Exception:
            return False
        got = self._gear_for(e, snap) if e is not None else None
        if not got:
            return False
        steps, got = got
        self._then_walk = (action.point, action.name, action.entity_id, action.attack)
        self._task_cmd = (steps, got[0], 1)
        what = ' and a '.join(i.split(':')[-1].replace('_', ' ') for i in got)
        self._navi_line(f"Getting a {what} first, then the {e.name or 'fight'}!", urgent=True)
        return True

    def _errand_gear(self, action) -> None:
        """An errand (a task) set about with hostile ones about (ERRAND_FOES): a weapon got first when it's quick
        (ERRAND_GEAR_S), then the errand. ("Make me a bed" at night, at 7 health, a pickaxe the best to hit with: two
        spiders, dead, 2026-10-17.) Not a shield (its iron: mined and smelted among the skeletons), and never for
        picking things up (the player's own sword and shield lay where they'd died: a sword and a shield were made
        first, 17 steps, the skeletons shooting, dead again, 2026-10-17): those wait for a fight order."""
        a = getattr(self, 'adapter', None)
        if getattr(a, 'fight_gear', None) is None or action.kind != 'task' \
                or any(s and s[0] == 'pickup' for s in action.steps or ()):
            return
        try:
            snap = a.snapshot()
        except Exception:
            return
        p = snap.player.pos if snap.player else None
        if p is None:
            return
        prof = getattr(a, 'combat_profile', lambda e: {})
        foes = [e for e in snap.entities if e.hostile and not e.dead and not prof(e).get('neutral')
                and math.dist(e.pos, p) < ERRAND_FOES]
        if not foes:
            return
        got = self._gear_for(min(foes, key=lambda e: math.dist(e.pos, p)), snap, hand_only=True,
                             most_s=ERRAND_GEAR_S)
        steps, items = got if got and action.name not in got[1] else ([], [])
        pl, ex = snap.player, snap.extra or {}
        hurt = pl.health is not None and pl.max_health and pl.health < pl.max_health * ERRAND_HURT
        eat = hurt and getattr(a, 'food_slot', lambda h: None)(ex.get('hotbar') or []) is not None
        if eat:
            steps = [('eat', None)] + steps              # (hurt among them, food to hand: eaten first)
        if not steps:
            return
        self._task_cmd = (steps + list(action.steps), action.name, action.count)
        what = ' and a '.join(i.split(':')[-1].replace('_', ' ') for i in items)
        first = ' and '.join(x for x in ('a bite' if eat else '', f'a {what}' if items else '') if x)
        self._navi_line(f"Enemies about: {first} first!", urgent=True)

    def _gear_for(self, e, snap, hand_only: bool = False, most_s: float = GEAR_MAX_S):
        """(the steps getting what a fight with e wants and isn't there, the items got), when it's quick enough
        (most_s, the planner's reckoning); None when nothing's wanted, or nothing can be had in time. hand_only:
        a weapon, not what goes in the off hand."""
        try:
            need = self.adapter.fight_gear(e, snap.extra or {})
        except Exception:
            return None
        if not need:
            return None
        have = dict(((snap.extra or {}).get('inventory') or {}))
        steps, secs, got = [], 0.0, []
        for items, where in need:                        # (in the order given: a weapon before a shield)
            if hand_only and where != 'hand':
                continue
            items = (items,) if isinstance(items, str) else tuple(items)
            held = next((i for i in items if have.get(i, 0) > 0), None)
            if held is None:
                best = None                              # the quickest of those that would do
                for item in items:
                    way = self.resolver.get_item(item, 1)
                    if way is None or way.kind != 'task':
                        continue
                    cost = sum(self._step_cost(s) for s in way.steps)
                    if best is None or cost < best[0]:
                        best = (cost, item, way)
                if best is None or secs + best[0] > most_s:
                    continue                             # (none to be had, or too long: fought without it)
                secs += best[0]
                steps += best[2].steps
                held = best[1]
                got.append(held)
            if where == 'offhand':
                steps.append(('offhand', held))
        return (steps, got) if got else None

    def _blocks_to_hand(self, action) -> bool:
        """A walk or an errand set about ready first: armour and a shield held but not worn, put on (the equip
        skill: after the things were picked up where the player died, the shield stayed in the pack, and a
        skeleton was fought without it, 2026-10-17); no blocks to build with in the hotbar but some held, put
        there (the hotbar skill: only the hotbar's are built with; a ravine was dug out of step by step, 6 s a
        block, 70 cobblestone in the pack, 2026-10-17). Then it. True when a walk waits for it."""
        a = getattr(self, 'adapter', None)
        if a is None or action.kind not in ('walk', 'task'):
            return False
        if action.kind == 'walk' and (action.attack or getattr(action, 'follow', None)):
            return False                                 # (a fight or a follow: at once)
        first = []
        try:
            if hasattr(a, 'equipment'):
                ex = a.snapshot().extra or {}
                first += a.equipment(ex.get('inventory') or {}, ex.get('offhand'))
            step = a.blocks_to_hand() if hasattr(a, 'blocks_to_hand') else None
        except Exception:
            first, step = [], None
        if step is not None:
            first.append(step)
        if not first or (action.kind == 'task' and action.name in ('equipment', 'hotbar')):
            return False
        if action.kind == 'task':
            steps, name, count = self._task_cmd
            self._task_cmd = (first + list(steps), name, count)
            return False
        self._then_walk = (action.point, action.name, action.entity_id, action.attack)
        self._task_cmd = (first, 'ready', 1)
        return True

    def _start_action(self, action, text: str) -> None:
        if action.kind in ('walk', 'stop', 'task') and getattr(self, 'goal', None) is not None:
            self.goal.stop()                             # (a new order: the last one's goal is over)
        if action.kind in ('walk', 'stop', 'task'):
            self._plan_cmd = 'stop'                    # a new order replaces a plan
            self._task_cmd = 'stop' if action.kind != 'task' else (action.steps, action.name, action.count)
            self._errand_gear(action)
            # the enemy this order (part) is about: other kills on the way don't finish it (_back_to_order)
            self._order_target = action.entity_id if action.kind == 'walk' and action.attack else None
            self._fight_all = action.every if action.kind == 'walk' else None   # 'kill all of them'
            self._fight_left = action.count if action.kind == 'walk' and action.every else None   # 'kill two'
            self._kills_read = len(self.fighter.kills)
            self._then = list(action.then) if action.kind == 'walk' else []      # '... and then ...'
            self._paused_order = None                    # (a new order: the last one's not taken up again)
        # (the order's own state first: a gear errand ran with the last order's "kill all of them" still set, and
        # fights were started on the way with whatever faced the player, 2026-10-16)
        if action.kind == 'walk' and action.attack and self._gear_first(action):
            return
        if self._blocks_to_hand(action):
            return
        if action.kind in ('walk', 'stop', 'task'):       # (following: only till the next order)
            self._follow = ((action.entity_id, action.name, action.follow)
                            if action.kind == 'walk' and getattr(action, 'follow', None) else None)
        if action.kind == 'walk':
            self._walk_request('start', action.point, action.name, action.entity_id, action.attack)
            self._last_order = None if getattr(self, '_doer_busy', False) else text   # (to think on if no way)
        elif action.kind == 'task':
            self._walk_request('stop', 'task')
            self._task_order = None if getattr(self, '_doer_busy', False) else text   # (to think on if it fails)
        elif action.kind == 'stop':
            self._walk_request('stop', 'stop')

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
        self.resolver.heard(f'Player ordered: {text}\nNavi: {(got or {}).get("say") or ""}')
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

    # ---- the task's world (core/tasks.py TaskWorld) ----

    def task_find(self, blocks: tuple, exclude: set, near=None):
        """The block of these kinds to go to: the quickest there and on to near (a snapshot position: where what's
        got is taken next), or the nearest."""
        try:
            got = self.adapter.locate_blocks(blocks, to_mine=True,
                                             near=_game_pos(self.adapter, near) if near is not None else None)
        except TypeError:
            got = self.adapter.locate_blocks(blocks)
        mem = getattr(self, 'memory', None)
        me = getattr(self, '_last_player_pos', None)
        me = _game_pos(self.adapter, me) if me is not None else None
        for r in got.get('results', []):
            c = r['coordinates']
            key = (c['x'], c['y'], c['z'])
            if key not in exclude and not (mem is not None and mem.is_unreachable(key, me)):
                return (r['position'], r['name'], key)
        return None

    def task_items(self, items: tuple, radius: float) -> list:
        return self.adapter.items_near(items, radius)

    def task_walk(self, point, name: str, beside=None, back=False, place=None) -> None:
        if beside is not None and hasattr(self.adapter, 'goal_beside'):
            self.adapter.goal_beside(point, beside)
        if place is not None and hasattr(self.adapter, 'goal_place'):
            self.adapter.goal_place(point, place)
        if back and hasattr(self.adapter, 'goal_back'):
            self.adapter.goal_back(point)
        self._walk_request('start', point, name, None, False)

    def task_stop(self) -> None:
        self._walk_request('stop', 'task')

    def task_creature(self, kind: str, variant, exclude):
        """The nearest creature of that kind (and variant) to hunt: (point, name, id), or None."""
        fn = getattr(self.adapter, 'creature_near', None)
        return fn(kind, dict(variant or ()), set(exclude)) if fn is not None else None

    def task_seen(self, ident) -> bool:
        """Whether that creature is in the snapshot (in sight of the walk), not only found by the game's search."""
        try:
            return any(e.id == ident for e in self.adapter.snapshot().entities)
        except Exception:
            return True

    def task_approach(self, point, name: str, ident) -> None:
        """Up to it (following it if it moves), to use something on it."""
        self._walk_request('start', tuple(point), name, ident, False)

    def task_hunt(self, point, name: str, ident) -> None:
        """Up to it and the fight (the task goes on when it's over)."""
        self._walk_request('start', tuple(point), name, ident, 'fight')

    def task_say(self, line: str) -> None:
        self._navi_line(line)

    def task_point(self, x: float, y: float) -> None:
        snap = self.adapter.snapshot()
        if snap.screen is None or not snap.screen.focused:
            return
        from .ui import win_input
        win_input.point_at(snap.screen.x + x, snap.screen.y + y)

    def task_press(self, button: str) -> None:
        snap = self.adapter.snapshot()
        if snap.screen is None or not snap.screen.focused:
            return
        from .ui import win_input
        win_input.press(button)

    def task_note(self, event: str, **data) -> None:
        """What a task did or learned, into the world memory."""
        mem = getattr(self, 'memory', None)
        if mem is None:
            return
        if event == 'put_down':
            mem.put_down(data['item'], data['at'])
        elif event == 'dug':
            mem.dug_at(data['at'])
        elif event == 'unreachable':
            mem.couldnt_reach(data.get('what', ''), data.get('at'), data.get('frm'), data.get('reason', ''))

    def task_aim(self, key, eye):
        fn = getattr(self.adapter, 'aim_point', None)
        return fn(key, eye) if fn is not None else (None, None)

    def task_place_spot(self, exclude=(), only=None):
        return self.adapter.place_spot(exclude, only=only) if only is not None else self.adapter.place_spot(exclude)

    def task_food(self, hotbar: list):
        return getattr(self.adapter, 'food_slot', lambda h: None)(hotbar)

    def task_tool(self, need: str, have: dict):
        book = self.adapter.book()
        held = [i for i in have if have[i] > 0 and book.tool_ok(need, {i: 1})]
        return min(held, key=len) if held else None

    def task_recover(self, reason=None):
        if getattr(getattr(self, 'goal', None), 'active', False):
            return None                                  # (the goal runner plans what's lacking itself)
        """A task's walk gave up: what it lacked, as steps to get it first, and a line for the player; or None.
        From why it gave up (the Reason's route note: blocks to build the way with, more than are spare)."""
        note = (reason.get('route') if reason is not None else None) or getattr(self.adapter, 'route_note', None) \
            or {}
        if note.get('found') != 'needs blocks':
            return None
        need = int(note.get('blocks_it_would_take') or 0) - int(note.get('blocks_to_build_with') or 0)
        # stuck: what's got for the way out can't itself need the way out (planks from logs up on the surface,
        # 14 up, were "cheapest": walks to them failed one after another; the stone round it was right there,
        # 2026-10-15): only what's in reach from here, or made from what's held
        got = self._blocks_plan(need, within=STUCK_REACH)
        if got is None:
            return None
        _secs, item, steps = got
        return steps, (f"No way up from here without about {need} more blocks: getting "
                       f"{item.split(':')[-1].replace('_', ' ')} first!")

    def _blocks_plan(self, need: int, within: float | None = None):
        """(about how long, the item, the steps) to get `need` blocks to build with, a few over: the quickest of what
        the game can give (dirt dug near, planks from logs held, cobblestone...), over what's held (and kept for
        what's to come). None: no way, or too many."""
        if need <= 0 or need > 64 or not hasattr(self.adapter, 'book'):
            return None
        from .core.crafting import CantMake, plan
        try:
            snap = self.adapter.snapshot()
            have = dict(((snap.extra or {}).get('inventory') or {}))
            book = self.adapter.book()
        except Exception:
            return None
        n = need + BLOCKS_SPARE
        best = None
        book.within = within
        try:
            for item in BLOCK_SOURCES + [i for i in have if i.endswith('_planks')] \
                    + [i.replace('_log', '_planks') for i in have if i.endswith('_log')]:
                try:
                    steps = plan(book, item, have.get(item, 0) + n, have)
                except CantMake:
                    continue
                secs = sum(15.0 + 5.0 * s[2] if s[0] == 'gather' else 4.0 for s in steps)
                if best is None or secs < best[0]:
                    best = (secs, item, steps)
        finally:
            book.within = None
        return best

    def _blocks_first(self, need: int) -> bool:
        """The way there needs blocks to build with that aren't held: get them first (the quickest of what the
        game can give: dirt dug near, planks from logs held, cobblestone...), a few over, then the walk again
        (like a tool first). True when it switched."""
        if getattr(self, 'task', None) is None or self.task.active \
                or getattr(self, '_blocks_tried', None) == self.walk.goal:
            return False
        best = self._blocks_plan(need)
        if best is None:
            return False
        n = need + BLOCKS_SPARE
        self._blocks_tried = self.walk.goal                      # (once per goal: no loop)
        self._resume_walk = (self.walk.goal, self.walk.name, getattr(self, '_walk_entity', None),
                             'fight' if self._fight_after is not None else False)
        self.walk.stop()
        self.keys.release_all()
        self.task.start(best[2], best[1], n)
        self._navi_line(f"I need about {need} blocks to build my way there: getting "
                        f"{best[1].split(':')[-1].replace('_', ' ')} first!")
        return True

    def _tool_first(self, snap) -> bool:
        """A route with a lot of slow digging that a tool would make much quicker, all told: make the tool first
        (a task), then the walk again (adapter.tool_advice). True when it switched."""
        if not hasattr(getattr(self, 'adapter', None), 'tool_advice') or getattr(self, 'task', None) is None \
                or self.task.active \
                or self.walk.goal is None:
            return False
        try:
            advice = self.adapter.tool_advice(self.walk.goal)
        except Exception:
            advice = None
        if not advice:
            return False
        tool, gain, steps, mostly = advice
        self._resume_walk = (self.walk.goal, self.walk.name, getattr(self, '_walk_entity', None),
                             'fight' if self._fight_after is not None else False)
        self.walk.stop()
        self.keys.release_all()
        self.task.start(steps, tool, 1)
        what = mostly[0].replace('_', ' ') if mostly else 'rock'
        self._navi_line(f"That's a lot of {what} to dig through! Making a {tool.split(':')[-1].replace('_', ' ')} "
                        f"first: about {gain} seconds quicker.")
        return True

    def _watch_progress(self, snap) -> None:
        """A walk getting no nearer its goal (nor digging or building its way) for NO_PROGRESS_S gives up, so
        the order can be thought through another way (it planned the same few blocks again and again for
        minutes, the top of a tree it had no blocks to climb to, 2026-10-13)."""
        if not self.walk.active or not snap.player or self.walk.goal is None or self.walk.target_id is not None:
            self._progress_watch = None
            return
        p, g = snap.player.pos, self.walk.goal
        d = math.hypot(g[0] - p[0], g[2] - p[2]) + abs(g[1] - p[1])
        now = time.monotonic()
        w = getattr(self, '_progress_watch', None)
        working = self.walk.state in ('breaking', 'building', 'parkour', 'paused')
        if w is None or w[0] != g or d < w[1] - 1.0 or working:
            self._progress_watch = (g, d if w is None or w[0] != g else min(d, w[1]), now)
            return
        if now - w[2] > NO_PROGRESS_S:
            self._progress_watch = None
            self.walk.events.append('gave_up')
            self.walk.stop('no progress')

    # ---- the conductor (core/conduct.py, docs/CONDUCT.md): the order, or one survival behaviour ----

    def _assess(self, snap, leave=()):
        """What's dangerous round the player weighed as one (core/threat.py): a Decision, or None (nothing to weigh:
        not this game's, a screen open). leave: ids of ones not weighed (the one a fight order is for)."""
        from .core import threat
        a = getattr(self, 'adapter', None)
        if not getattr(a, 'defend_on_walks', False) or not snap.player or (snap.extra or {}).get('container'):
            return None
        ts = [t for t in threat.threats(snap, getattr(a, 'combat_profile', lambda e: {})) if t.e.id not in leave]
        if not ts:
            self._track_open(snap)
            return threat.Decision('none')
        self._sight_of(snap, ts)
        armed = (getattr(a, 'weapon_damage', lambda h: None)((snap.extra or {}).get('hotbar') or []) or 2.0) > 1.0
        win = getattr(self.fighter, '_winnable', None)
        dec = threat.decide(snap, ts, self.walk.goal if self.walk.active else None, armed=armed,
                            winnable=lambda near=None: True if win is None else
                            (win(snap) if near is None else win(snap, near)),
                            perch=getattr(self.fighter, '_can_perch', lambda s: False)(snap))
        dec.threats = ts
        if dec.what == 'none':
            self._track_open(snap)
        return dec

    def _conduct(self, snap, dt: float) -> bool:
        """The one place that decides whether the order or a survival behaviour has the character this frame
        (docs/CONDUCT.md). True when a survival behaviour had it (and ran)."""
        from .core.conduct import Conductor
        self._sv_t = getattr(self, '_sv_t', 0.0) + dt      # (the frames' own time: what the behaviours wait by)
        c = getattr(self, 'conductor', None)
        if c is None:
            c = self.conductor = Conductor(log=self._conduct_log, clock=lambda: self._sv_t)
        if not getattr(getattr(self, 'adapter', None), 'defend_on_walks', False) or not snap.player:
            return False
        if c.running is not None and time.monotonic() - self._took_over < 0.5:   # the player took over
            c.ended('stopped:you')
            self._paused_order = None
            self.fighter.stop()
            self.walk.stop('you')
            if getattr(self, 'task', None) is not None and self.task.active:
                self.task.stop()
            return False
        if c.running is None:
            ordered = self.walk.active or getattr(getattr(self, 'task', None), 'active', False) or self.plan.active \
                or getattr(self, '_paused_order', None) is not None
            # on an order (not one that's itself a fight: that order's own), or the player not at the controls a
            # while (after "over to you" nothing guarded them, and a zombie killed them where they stood,
            # 2026-10-16)
            # (only if the player chose it, guard_when_idle: "It shouldn't take over control unless I've given it
            # a command it's trying to finish up", 2026-10-17)
            idle = bool((getattr(self, 'cfg', None) or {}).get('guard_when_idle')) \
                and time.monotonic() - self._took_over > IDLE_GUARD and not self.keys.held
            if not (ordered or idle) or self.fighter.active:      # (a fight under way: that order's own)
                return False
        can_cut = self.walk.state != 'parkour' and (snap.extra or {}).get('on_ground') is not False
        # a fight order on its way to the one it's for: that one's the order's, and of the rest only one up close
        # after the player is the conductor's (fought, then on to it) (a gear errand fought whatever faced the
        # player on the way, outside all this, 2026-10-16)
        mine = (self._fight_after, getattr(self, '_order_target', None)) if self._fight_after is not None else ()
        dec = self._assess(snap, leave=mine)
        if dec is not None and mine and c.running is None and dec.what != 'fight':
            from .core.threat import Decision
            dec = Decision('none')
        action, r = c.choose(dec, can_cut)
        if action == 'order':
            if getattr(self, '_paused_order', None) is not None:
                self._resume_order()
            return False
        if action == 'start':
            self.fighter.stop()                          # (a switch: what was running ends)
            if not self._sv_begin(r, dec, snap):         # (the order's paused only once it's under way)
                c.ended('no_spot')
                return False
        out = self._sv_tick(r, snap, dt)
        if out is not None:
            self._sv_after(c.ended(out), out, snap)
        return True

    def _conduct_log(self, row: dict) -> None:
        """runtime/conduct-<date>.jsonl: each change of who has the character, and why."""
        try:
            pl = self.adapter.snapshot().player
            row = {**row, 'hp': round(pl.health, 1) if pl and pl.health is not None else None}
        except Exception:
            pass
        try:
            path = ROOT / 'runtime' / f'conduct-{datetime.now():%Y%m%d}.jsonl'
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'t': datetime.now().isoformat(timespec='seconds'), **row}, default=str) + '\n')
        except OSError:
            pass

    def _pause_order(self) -> None:
        """The order waits (its walk stopped, its task not run) while a survival behaviour has the character."""
        if getattr(self, '_paused_order', None) is not None:
            return
        spec = None
        if self.walk.active and not (getattr(self, 'task', None) is not None and self.task.active):
            spec = (self.walk.goal, self.walk.name, getattr(self, '_walk_entity', None),
                    'fight' if self._fight_after is not None else False)   # (a fight order: still one)
        self._paused_order = {'walk': spec}
        self.walk.stop()
        self.keys.release_all()

    def _resume_order(self) -> None:
        """The order on again from where things now are: the task's step from its start (it finds and plans
        afresh), or the walk planned again from here."""
        p, self._paused_order = self._paused_order, None
        if getattr(self, 'task', None) is not None and self.task.active:
            self.task.resume()
        elif p and p.get('walk'):
            self._walk_request('start', *p['walk'])

    def _sv_walking(self) -> bool:
        c = getattr(self, 'conductor', None)
        return c is not None and c.running is not None and getattr(c.running, 'phase', None) == 'walk'

    def _sv_begin(self, r, dec, snap) -> bool:
        """A survival behaviour starts (r: the conductor's Running). False: it can't (no cover near)."""
        from .core import melee
        p = snap.player.pos
        e = getattr(dec, 'target', None)
        name = (getattr(e, 'name', None) or 'monster')
        art = 'An' if name[0].lower() in 'aeiou' else 'A'
        r.phase, r.spot = 'fight', None
        if r.kind in ('fight', 'hold'):
            self._pause_order()
        if r.kind == 'fight':
            self.fighter.start(e.id, e.name)
            say = f"{art} {name} after us! Dealing with it first."
        elif r.kind == 'hold':
            try:
                room = (self.adapter.terrain() or {}).get('room')
            except Exception:
                room = None
            here = room(p[0], p[2]) if room is not None else melee.ROOM_ENOUGH
            back = getattr(self, '_last_open', None)
            same = [t for t in getattr(dec, 'threats', []) if t.kind == 'melee' and t.e.name == e.name]
            what = f"{len(same)} {name}s" if len(same) > 1 else f"a {name}"
            if here < melee.ROOM_ENOUGH and back is not None and math.dist(back, p) < OPEN_BACK:
                r.kind, r.phase, r.spot = 'regroup', 'walk', back
                self._walk_request('start', back, 'open ground', None, False)
                say = f"There's {what} ahead, and it's tight here: back to open ground to take them on!"
            else:
                self.fighter.start(e.id, e.name, hold=True)
                say = f"There's {what} ahead! I'll take them on here, where there's room."
        elif r.kind == 'cover':
            shooters = list(getattr(dec, 'shooters', ()) or ())
            fn = getattr(self.adapter, 'cover_spot', None)
            self.adapter.cover_avoid = set(getattr(self, '_bad_cover', {}))   # (spots seen in lately: not again)
            try:
                spot = fn([(x.pos[0], x.pos[1] + x.height * 0.85, x.pos[2]) for x in shooters]) if fn else None
            except Exception:
                spot = None
            if spot is None:
                return False
            self._pause_order()
            r.phase, r.spot = 'walk', tuple(spot)
            self._walk_request('start', tuple(spot), 'cover', None, False)
            with_ = ' with others up close' if dec.why == 'others up close' else ''
            say = f"{name}'s shooting at us{with_}: into cover!"
        elif r.kind == 'away':
            coming = [t.e for t in getattr(dec, 'threats', []) if t.kind == 'melee' and t.after] or [e]
            vx = vz = 0.0
            for x in coming:
                dx, dz = p[0] - x.pos[0], p[2] - x.pos[2]
                d = math.hypot(dx, dz) or 0.1
                vx, vz = vx + dx / d / d, vz + dz / d / d
            fn = getattr(self.adapter, 'explore_point', None)
            try:
                point = fn(None, AWAY_TO, vector=(vx, vz)) if fn else None
            except Exception:
                point = None
            if point is None:
                return False
            self._pause_order()
            r.phase, r.spot = 'walk', tuple(point)
            self._walk_request('start', tuple(point), 'away', None, False)
            say = f"{art} {name} coming, and it's no fight for us now: getting away from it!"
        else:
            return False
        self._navi_line(say, urgent=True)
        return True

    def _sv_tick(self, r, snap, dt: float):
        """The running survival behaviour, a frame. Its outcome when it's over ('won', 'held_off', 'calm', 'yield',
        ...), else None."""
        now = getattr(self, '_sv_t', 0.0)
        pl = snap.player
        if pl.state == 'dead' or pl.health is not None and pl.health <= 0:
            self.fighter.stop()                          # (the log had a death as "won", 2026-10-16)
            self.walk.stop()
            return 'died'
        if r.phase == 'fight':
            if getattr(self, '_fight_boxed', False):     # boxed in: back to the open ground passed, held there
                self._fight_boxed = False
                back = getattr(self, '_last_open', None)
                p = snap.player.pos
                if back is not None and 3.0 < math.dist(back, p) < OPEN_BACK:
                    tgt = self.fighter.target_id
                    self.fighter.stop()
                    nr = self.conductor.follow_on('regroup', tgt, 'boxed in')
                    nr.phase, nr.spot = 'walk', back
                    self._walk_request('start', back, 'open ground', None, False)
                    self._navi_line("Boxed in here! Back to open ground to fight them there!", urgent=True)
                    return None
            self._fight_outcome = None
            self._fight_step(snap, dt)
            if not self.fighter.active:
                return getattr(self, '_fight_outcome', None) or 'won'
            return None
        if r.phase == 'walk':
            if self.walk.active or self._walk_cmd is not None:
                self._walk_tail(snap, dt)
                return None
            if r.kind == 'regroup':                      # on open ground: they're met here
                if r.target is not None:
                    self.fighter.start(r.target, None, hold=True)
                    r.phase = 'fight'
                    return None
                return 'calm'
            r.phase, r.wait_since, r.calm = 'wait', now, None
            r.hp_in, r.seen_since = snap.player.health, None
        if r.phase == 'wait':                            # in cover, or got away: still, till they've given up
            if self.keys.held:
                self.keys.release_all()
            p = snap.player.pos
            if r.kind == 'cover':
                # still seen there, or hit there: that cover's no good (stood in it, seen and shot from 20 health
                # to 4, 2026-10-16): another, or what else the danger asks
                seen = (getattr(self, '_sight_cache', None) or (0.0, {}))[1].get(r.target)
                r.seen_since = (r.seen_since or now) if seen else None
                hp = snap.player.health
                if (r.seen_since is not None and now - r.seen_since > COVER_SEEN_S) or                         (hp is not None and getattr(r, 'hp_in', None) is not None and hp < r.hp_in - 1.0):
                    return 'exposed'
            prof = getattr(getattr(self, 'adapter', None), 'combat_profile', lambda x: {})
            if any(x.hostile and not x.dead and (x.extra or {}).get('aggressive') and not (prof(x) or {}).get('ranged')
                   and math.dist(x.pos, p) < FOLLOWED_NEAR for x in snap.entities):
                return 'yield'                           # (one followed: the conductor has it fought)
            e = next((x for x in snap.entities if x.id == r.target and not x.dead), None)
            st = (e.extra or {}) if e is not None else {}
            if r.kind == 'away':
                calm = e is None or not st.get('aggressive') or math.dist(e.pos, p) > AWAY_CLEAR
            else:
                calm = e is None or not st.get('aggressive') or not st.get('using_item')
            r.calm = (r.calm or now) if calm else None
            if (r.calm is not None and now - r.calm > HIDE_CALM) or now - r.wait_since > HIDE_MAX:
                return 'calm'
        return None

    def _sv_after(self, r, out: str, snap) -> None:
        """What a survival behaviour's end means for the order. Held off (they stay where the order was going): that
        step's target is guarded, and planned round (another tree, another way); a walk with nothing else to it is
        over, and the player asked. Hurt: everything's stopped already (_hurt_stop). Else the order goes on (the
        conductor resumes it when nothing else is wanted)."""
        if out == 'held_off':
            task = getattr(self, 'task', None)
            self._paused_order = None                    # (what's next is the order's own, from where it is: a
                                                         # later task "resumed" by this, 2026-10-16)
            if task is not None and task.active:
                task.guarded()
            else:
                self._paused_order = None
                self._navi_line("They're staying right where we were going. I won't walk in among them like this: "
                                "shall I get a weapon and some blocks first?", urgent=True)
        elif out in ('low_health', 'outmatched', 'surrounded', 'died'):
            self._paused_order = None
        elif out == 'exposed' and getattr(r, 'spot', None) is not None:   # (that spot: not again a while)
            bad = getattr(self, '_bad_cover', {})
            now = time.monotonic()
            bad = {c: t for c, t in bad.items() if now - t < BAD_COVER_S}
            bad[(math.floor(r.spot[0]), math.floor(r.spot[2]))] = now      # (across: snapshot x, z)
            self._bad_cover = bad

    def _sight_of(self, snap, ts) -> None:
        """Whether each one that shoots, set on the player, can see them (the game's rays), every SIGHT_EVERY s."""
        fn = getattr(self.adapter, 'in_sight', None)
        shooters = [t for t in ts if t.kind == 'ranged' and t.after]
        if fn is None or not shooters:
            return
        now = time.monotonic()
        cache = getattr(self, '_sight_cache', None)
        if cache is None or now - cache[0] > SIGHT_EVERY:
            p = snap.player.pos
            try:
                seen = fn([(t.e.pos[0], t.e.pos[1] + t.e.height * 0.85, t.e.pos[2]) for t in shooters],
                          (p[0], p[1] + 1.5, p[2]))
            except Exception:
                seen = [None] * len(shooters)
            cache = self._sight_cache = (now, {t.e.id: v for t, v in zip(shooters, seen)})
        for t in shooters:
            t.sees = cache[1].get(t.e.id)

    def _track_open(self, snap) -> None:
        """Where there's room round the player, as it walks: the last open ground passed (fought from, when what's
        ahead comes, or boxed in)."""
        from .core import melee
        now = time.monotonic()
        if not self.walk.active or now - getattr(self, '_open_at_t', -1e9) < OPEN_EVERY:
            return
        self._open_at_t = now
        try:
            room = (self.adapter.terrain() or {}).get('room')
            p = snap.player.pos
            if room is not None and room(p[0], p[2]) >= melee.ROOM_ENOUGH:
                self._last_open = tuple(p)
        except Exception:
            pass

    # ---- goals (core/goals.py): an item order planned from where things are, and again when they aren't so ----

    def _goal_for(self, steps, item, count) -> None:
        """An order for an item (gathered, crafted, smelted) runs under the goal runner; others (eat, put down,
        one block) don't."""
        goal = getattr(self, 'goal', None)
        if goal is None:
            return
        goal.stop()
        kinds = {s[0] for s in steps}
        if not steps or not kinds <= {'gather', 'craft', 'smelt', 'station'} or kinds == {'station'}:
            return
        have = self._held().get(item, 0)
        goal.start(item, have + count if have else count, steps)
        self._goal_count = count

    def _held(self) -> dict:
        try:
            return dict(((self.adapter.snapshot().extra or {}).get('inventory') or {}))
        except Exception:
            return {}

    def _plan_goal(self, item: str, total: int, after=None) -> list:
        """The goal runner's planner: from what's held now ([] when it's held). A way that needs blocks to build
        with that aren't spare: blocks first, from what's in reach (stuck, what's got for the way out can't
        need the way out); stuck: only what's in reach."""
        from .core.crafting import plan
        have = self._held()
        if have.get(item, 0) >= total:
            return []
        reason = getattr(after, 'reason', None)
        route = (reason.get('route') or {}) if reason is not None else {}
        if isinstance(route, str):
            route = {}
        need = needs_blocks(route)
        if need:                                         # (found 'needs blocks', or 'no way' without them)
            got = self._blocks_plan(need, within=STUCK_REACH)
            if got is not None:
                return got[2]
        book = self.adapter.book()
        book.within = STUCK_REACH if reason is not None and reason.kind == 'stuck' else None
        try:
            return plan(book, item, total, have)
        finally:
            book.within = None

    def _step_cost(self, step) -> float:
        from .core.crafting import step_cost
        try:
            return step_cost(self.adapter.book(), step)
        except Exception:
            return 0.0

    def task_step_done(self, i: int, took: float) -> bool:
        """A task's step is done: the goal runner may plan on from here (a milestone that ran long, or the plan's
        end short of the goal). True: the task's started again with the new plan."""
        goal = getattr(self, 'goal', None)
        if goal is None or not goal.active:
            return False
        new = goal.step_done(i, took)
        if not new:
            return False
        self._replan_start(new, 'Change of plan')
        return True

    def _replan_start(self, steps, why: str) -> None:
        self.task.start(steps, self.goal.item, getattr(self, '_goal_count', 1))
        self._navi_line(f'{why}: {self.task.describe()}.')

    def _goal_failed(self) -> bool:
        """A task under the goal runner failed: planned again from here with what was learned (its Reason).
        True: under way again."""
        goal = getattr(self, 'goal', None)
        if goal is None or not goal.active or self.task.active or str(self.task.result) == 'done':
            return False
        o = getattr(self.task, 'outcome', None)
        if o is None or o.ok:
            return False
        new = goal.failed(o)
        if new is None:
            return False                                 # (no plan from here: thought through, above)
        if not new:
            self.task.result = 'done'                    # (it's held after all)
            return True
        self._replan_start(new, 'New plan')
        return True

    def _task_failed_think(self) -> None:
        """A task an order started (the rules', not the doer's) has failed: the doer thinks it through from there,
        told what stopped it (what's held counts), instead of the order ending (the iron pickaxe's ingredients
        all got, then no way up out of the hole, and over, 2026-10-15). Once per order."""
        if self._goal_failed():
            return                                       # (planned again: on from here)
        order = getattr(self, '_task_order', None)
        if not order or self.task.active or not str(self.task.result).startswith('failed'):
            return
        self._task_order = None
        if getattr(self, 'doer', None) is None or getattr(self, '_doer_busy', False):
            return
        why = str(self.task.result)[len('failed: '):]
        threading.Thread(target=self._doer_order, args=(
            f"{order}\n(The task doing it stopped: \"{why}\" (step {self.task.i + 1} of {len(self.task.steps)}: "
            f"{self.task.describe()}). What's held now counts: work out what's in the way and how to get past it, "
            f"then carry the order on.)",), daemon=True).start()

    def _task_trace(self) -> None:
        """runtime/task-<date>.jsonl: each step and phase of a task (getting items), as it changes."""
        rows, self.task.trace = self.task.trace, []
        if not rows:
            return
        try:
            path = ROOT / 'runtime' / f'task-{datetime.now():%Y%m%d}.jsonl'
            with path.open('a', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, default=str) + '\n')
        except OSError:
            pass

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
            return self.resolver._attack(inp.get('name', 'enemy'), 'hit', ask=False)
        if tool == 'fight_character':
            return self.resolver._attack(inp.get('name', 'enemy'), 'fight', ask=False)
        if tool == 'walk_to_character':
            got = self.resolver.character(inp.get('name', 'enemy'))
            return got or WalkAction('none', f"I don't see any {inp.get('name', 'of those')} near you.")
        if tool == 'walk_to_place':
            got = self.resolver._named(inp.get('name', ''))
            return got or WalkAction('none', f"I don't know where {inp.get('name', 'that')} is.")
        if tool == 'walk_to_nearest':
            kinds = tuple(getattr(self.adapter, 'place_kinds', None) or ()) or ('',)
            return self.resolver.nearest(inp.get('kind') or kinds[0])
        if tool == 'get_item':
            n = inp.get('count')
            got = self.resolver._get_item(f"{n} {inp.get('item', '')}" if n else f"some {inp.get('item', '')}")
            return got or WalkAction('none', f"I don't know how to get {inp.get('item', 'that')}.")
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

    def _error(self, where: str, e: Exception) -> None:
        """An error in a loop that runs every frame: shown once (again only after ERROR_AGAIN s), its traceback
        kept in runtime/errors-<date>.log (one every frame flooded the window, with no way to see where it was,
        2026-10-15)."""
        import traceback
        key = f'{where}: {type(e).__name__}: {e}'
        seen = getattr(self, '_errors_seen', None)
        if seen is None:
            seen = self._errors_seen = {}
        now = time.monotonic()
        if now - seen.get(key, -1e9) < ERROR_AGAIN:
            return
        seen[key] = now
        self.q.put(('meta', f'\n{where} error: {type(e).__name__}: {e}\n'))
        try:
            with (ROOT / 'runtime' / f'errors-{datetime.now():%Y%m%d}.log').open('a', encoding='utf-8') as f:
                f.write(f'{datetime.now().isoformat(timespec="seconds")} {key}\n{traceback.format_exc()}\n')
        except OSError:
            pass

    def _once_line(self, line: str) -> None:
        """A line not said again within ONCE_LINE s (\"It's right above us...\" and \"I can't find a way...\" over
        and over, every plan, 2026-10-15)."""
        said = getattr(self, '_once_said', None)
        if said is None:
            said = self._once_said = {}
        now = time.monotonic()
        if now - said.get(line, -1e9) < ONCE_LINE:
            return
        said[line] = now
        self._navi_line(line)

    def _navi_line(self, line: str, urgent: bool = False) -> None:
        """A line from Navi: shown and spoken, and kept (the doer reads what came of its actions). While the doer
        runs an order, only its own lines (doer_say) and urgent ones (danger, health, the game out of focus) are
        shown: each action's own "Let's go to...", "Here we are..." on top of the doer's made eight lines of one
        "get me down from here", 2026-10-15."""
        if not line:
            return
        self._said = [x for x in getattr(self, '_said', []) if time.monotonic() - x[0] < 600] \
            + [(time.monotonic(), line)]
        if getattr(self, '_doer_busy', False) and not urgent:
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
            if cmd[1] != 'task':
                self._follow = None
            self.walk.stop(cmd[1])
            self.fighter.stop()
            self._fight_after = None
            self.companion.follow()
        if getattr(self, '_follow', None) and not self.walk.active and self._walk_cmd is None:
            self._follow_tick(snap)
        cmd_plan, self._plan_cmd = self._plan_cmd, None
        if cmd_plan == 'stop':
            self.plan.stop()
        elif cmd_plan:
            self.plan.start(cmd_plan, snap)
        cmd_task, self._task_cmd = getattr(self, '_task_cmd', None), None
        task = getattr(self, 'task', None)
        if task is None:
            self.task = task = TaskRunner(self)
        if cmd_task == 'stop':
            self.task.stop()
        elif cmd_task:
            self.task.start(*cmd_task)
            self._paused_order = None                    # (a new task: nothing paused before it taken up again)
            self._goal_for(*cmd_task)
            if getattr(self, '_then_walk', None) is not None:   # (gear first: then the fight it was for)
                self._resume_walk, self._then_walk = self._then_walk, None
            if cmd_task[0] and cmd_task[0][0][0] != 'eat':
                self._navi_line(f'First: {self.task.describe()}.')
        adapter = getattr(self, 'adapter', None)
        if adapter is not None:              # what the task's steps to come use up: not built with on the way
            adapter.reserve = self.task.reserved() if self.task.active else {}
            adapter.keep_blocks = self.task.keep()
        if getattr(self, 'memory', None) is not None and self.walk.dug:   # blocks the walk broke: remembered
            for cell in self.walk.dug:
                self.memory.dug_at(cell)
            self.walk.dug = []
        if self.task.active and time.monotonic() - self._took_over < 0.5:   # the player took over
            self.task.stop()
            self.keys.release_all()
            self._navi_line("Okay, you've got it!")
        if self.task.trace:
            self._task_trace()
        self._task_failed_think()
        conducting = getattr(self, 'conductor', None) is not None and self.conductor.running is not None
        if self.task.active and self.fighter.active and not conducting:   # the task's own fight (a hunt): then on
            self._fight_step(snap, dt)
            if not self.fighter.active:
                self.task.resume()
            return
        if self._conduct(snap, dt):               # danger: a survival behaviour has the character (docs/CONDUCT.md)
            return
        if not self.task.active and not self.fighter.active and getattr(self, '_resume_walk', None):
            # the tool's made, or the one that came at us is dealt with: on with the walk
            goal, name, entity, mode = self._resume_walk
            self._resume_walk = None
            self._walk_request('start', goal, name, entity, mode)
            return
        if self.task.active:
            intent = self.task.update(snap, dt, self.walk.active)
            if intent is not None and not self.walk.active:   # its own presses (mining, placing, screens)
                if snap.screen is not None and snap.screen.focused:
                    self.keys.set(intent.keys)
                    for t in intent.taps:
                        self.keys.tap(t)
                    if intent.turn and snap.camera:
                        yaw = cam_yaw(snap.camera)
                        self.turner.turn(intent.turn, yaw)
                    if intent.pitch:
                        self.turner.look(intent.pitch)
                else:
                    self.keys.release_all()
                self._walk_events()
                return
        if self.plan.active and not (self.walk.active or self.fighter.active) \
                and time.monotonic() - self._took_over < 0.5:   # the player took over during a move step
            self.plan.stop()
            self.keys.release_all()
            self._navi_line("Okay, you've got it!")
        if self.plan.active:
            intent = self.plan.update(snap, dt, self.walk.active, self.fighter.active)
            if intent is not None:            # a move step: its keys (never off a ledge)
                if snap.screen is not None and snap.screen.focused and snap.camera:
                    keys = self.fighter._safe(intent.keys, cam_yaw(snap.camera), roll=False)
                    self.keys.set(keys)
                    self._rekey(snap, dt)
                    if intent.turn:
                        yaw = cam_yaw(snap.camera)
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
        self._walk_tail(snap, dt)

    def _follow_tick(self, snap) -> None:
        """Following a character (Baritone's follow): whenever it's more than its distance off, walked after
        again; till another order, the player taking over, or it's gone."""
        ident, name, keep = self._follow
        if time.monotonic() - self._took_over < 0.5:
            self._follow = None
            return
        ent = next((e for e in snap.entities if e.id == ident), None)
        if ent is None or ent.dead or snap.player is None:
            self._follow = None
            self._navi_line(f"I've lost the {name or 'one I was following'}.")
            return
        if math.hypot(ent.pos[0] - snap.player.pos[0], ent.pos[2] - snap.player.pos[2]) > keep + FOLLOW_SLACK:
            self._walk_request('start', tuple(ent.pos), name, ident, False)

    def _walk_tail(self, snap, dt: float) -> None:
        """The walk under way, a frame: its route, its keys, its events (the order's walk, or a survival one)."""
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
            self.walk.set_route(route, self.companion.route_actions, self.companion.route_complete,
                                getattr(self.companion, 'route_moves', None), getattr(self.companion, 'route_grid', None))
            self._walk_route = route
            if self._tool_first(snap):
                return
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
        if not getattr(getattr(self, 'adapter', None), 'defend_on_walks', False) and self._fight_on_the_way(snap):
            return                                       # (where there's no conductor for it: docs/CONDUCT.md)
        intent = self.walk.update(snap, dt)
        self._watch_progress(snap)
        if snap.screen is not None and snap.screen.focused and self.walk.active:
            self.keys.set(intent.keys)
            for t in intent.taps:
                self.keys.tap(t)
            if self._rekey(snap, dt):
                self.walk.events.append('rekey')
            if intent.turn and snap.camera:
                yaw = cam_yaw(snap.camera)
                self.turner.turn(intent.turn, yaw)
            if intent.pitch:
                self.turner.look(intent.pitch)
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
               'cam_yaw': round(cam_yaw(snap.camera), 1)
               if snap.camera else None, 'anim': getattr(snap.player, 'anim', None) if snap.player else None,
               'entity': getattr(self, '_walk_entity', None),
               'planning': getattr(self.companion, '_planning', None), 'clear_line': self.walk.clear_line,
               'route_note': getattr(getattr(self, 'adapter', None), 'route_note', None),
               'companion': getattr(self.companion, 'mode', None), 'work': _work_now(self.walk)}
        mv = getattr(self.walk, 'mover', None)
        if mv is not None and getattr(self.walk, '_moving', False) and mv.current is not None:
            m = mv.current                               # (the move under way: Baritone's way, moving.py)
            row['move'] = {'i': mv.i, 'of': len(mv.moves), 'kind': m.kind, 'src': m.src, 'dest': m.dest,
                           'phase': mv.st.phase, 'breaking': mv.st.breaking, 'state': mv.state}
        if getattr(mv, 'why', ''):
            row['move_why'], mv.why = mv.why, ''
        if new_route:
            self._logged_route = route
            pts = getattr(route, 'waypoints', route)
            row['route'] = {'complete': getattr(self.companion, 'route_complete', None),
                            'waypoints': [[round(v, 1) for v in w] for w in pts],
                            'actions': [str(a) for a in (self.companion.route_actions or [])][:20],
                            'moves': [f'{m.kind} {m.src}->{m.dest}' + (f' break {len(m.breaks)}' if m.breaks else '')
                                      + (' place' if m.place else '') + (f' {dict(m.info)}' if m.info else '')
                                      for m in (getattr(self.companion, 'route_moves', None) or [])][:40]}
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
                yaw = cam_yaw(snap.camera)
                self.turner.turn(intent.turn, yaw)
            if getattr(intent, 'pitch', 0.0):
                self.turner.look(intent.pitch)
        else:
            self.keys.release_all()
        now = time.monotonic()
        if (now - getattr(self, '_last_fight_log', 0.0) > 0.25 and self.fighter.debug) or self.fighter.events:
            self._last_fight_log = now
            # (the health, and the hostile ones near with their swell: two creepers hurt the player and the log
            # had neither, 2026-10-13)
            pl = snap.player
            near = []
            if pl is not None:
                for o in snap.entities:
                    if o.hostile and not o.dead and math.dist(o.pos, pl.pos) < 12.0:
                        near.append([o.id, (o.model or '').split(':')[-1], round(math.dist(o.pos, pl.pos), 1),
                                     (o.extra or {}).get('swell')])
            hot, slot = (snap.extra or {}).get('hotbar') or [], (snap.extra or {}).get('slot')
            hand = hot[slot][0] if isinstance(slot, int) and 0 <= slot < len(hot) and hot[slot] else None
            self._fight_log({'t': round(now, 3), 'state': self.fighter.state, 'events': list(self.fighter.events),
                             'hand': hand,
                             'keys': sorted(intent.keys), 'taps': list(intent.taps),
                             'focused': bool(snap.screen and snap.screen.focused), 'held': sorted(self.keys.held),
                             'hp': pl.health if pl is not None else None, 'near': near,
                             # (where, and the room round it: a fight lost in a corner had neither, 2026-10-16)
                             'pos': [round(v, 2) for v in _game_pos(self.adapter, pl.pos)] if pl is not None
                             and getattr(self, 'adapter', None) is not None else None,
                             'room': getattr(self.fighter, 'room_here', lambda p: None)(pl.pos) if pl is not None
                             else None,
                             **self.fighter.debug})
        if not self.fighter.active and self.fighter.chains_changed:   # a fight ended: keep what it learned
            self._save_chains()
        self._count_kills()
        while self.fighter.events:
            ev = self.fighter.events.pop(0)
            if ev in FIGHT_ENDS:
                self._fight_outcome = ev                 # (how it ended: for the conductor)
            if ev == 'not_focused':
                self._navi_line("Click back into the game and I'll keep fighting!", urgent=True)
                continue
            line = {'won': "Got it! It's down!", 'low_health': "You're hurt! Over to you: heal up!",
                    'outmatched': "Too many for what we've got to fight with: I got us away. Over to you!",
                    'held_off': None, 'boxed': None,
                    'target_gone': "It's gone!", 'timeout': "That's taking a while. Over to you!",
                    'next_target': "One down! Now the other one!",
                    'unreachable': "I can't reach that one!",
                    'no_flask': "I can't heal: no flask left, or none in your item slot!",
                    'escape': "Too many! Getting some room!",
                    'perch': "Too many to take on down here: up out of their reach!",
                    'surrounded': "Too many of them! Over to you!"}.get(ev)
            if ev in FIGHT_ENDS:
                self._ended_as(ev == 'won', *FIGHT_ENDS[ev], what=self.fighter.name or 'enemy')
            if ev == 'unreachable':     # go on as if it were done, with the others
                ev = 'target_gone' if not getattr(self, '_fight_all', None) else 'won'
            if self.plan.active and ev != 'fled' and self.plan.on_event(ev):
                pass                                              # the plan goes on by itself
            elif ev == 'won' and self._back_to_order(snap):
                line = "Got it! Now back to the " + (self._order_name or 'one you asked for') + '!'
            elif ev == 'won' and getattr(self, '_fight_all', None):   # 'kill all of them': the next one
                coming = self._coming_at_me(snap)
                left = getattr(self, '_fight_left', None)
                got = None if coming is not None or (left is not None and left <= 0) else \
                    self.resolver.character(self._fight_all, max_range=ALL_RANGE, same_level=True,
                                            exclude=self.fighter.unreachable)
                if left is not None and left <= 0 and coming is None:    # 'kill two soldiers': that's two
                    self._fight_all, self._fight_left = None, None
                    line = "Got it! That's all I was asked for!"
                    if self._next_order():
                        line = "Got it! That's all I was asked for! Now the next part!"
                elif coming is not None:    # first whatever is coming at us, whatever it is (a wolf, a bat)
                    name = self._enemy_name(coming)
                    self._order_target = None
                    self._walk_request('start', tuple(coming.pos), name, coming.id, 'fight')
                    line = 'Got it! ' + (f'Now the {name} coming at us!' if name else 'Now the one coming at us!')
                elif got is not None:
                    self._order_target = got.entity_id
                    self._walk_request('start', got.point, got.name, got.entity_id, 'fight')
                    line = (line + ' ' if line and 'reach' in line else 'Got it! ') + f"Next {got.name or 'one'}!"
                else:
                    self._fight_all = None
                    line = "Got it! That's all of them!"
                    if self._next_order():
                        line = "Got it! That's all of them! Now the next part!"
            elif ev in ('won', 'target_gone') and self._next_order():
                line = "Got it! Now the next part!"
            elif ev == 'boxed':                          # (no way away on foot: the conductor's to deal with)
                self._fight_boxed, line = True, None
            elif ev == 'held_off':                       # (they stay where they are: the conductor's)
                self._then, self._fight_all, line = [], None, None
            elif ev in ('low_health', 'timeout', 'fled', 'surrounded', 'outmatched'):
                self._fight_all = None if ev != 'fled' else getattr(self, '_fight_all', None)
                if ev != 'fled':
                    self._then = []
                if ev in ('low_health', 'surrounded', 'outmatched'):
                    eating = self._hurt_stop(snap)
                    if ev != 'outmatched':
                        line = eating or line
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
                self._navi_line(line, urgent=ev in ('low_health', 'escape', 'surrounded', 'timeout', 'unreachable',
                                                    'outmatched', 'boxed', 'held_off'))
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

    def _load_chains(self) -> dict:
        """Enemy combos learned in fights (core/combat.py), kept across runs: .local/<game>/chains.json."""
        try:
            return json.loads(self._chains_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_chains(self) -> None:
        try:
            self._chains_file.parent.mkdir(parents=True, exist_ok=True)
            self._chains_file.write_text(json.dumps(self.fighter.chains, indent=1), encoding='utf-8')
            self.fighter.chains_changed = False
        except OSError:
            pass

    def _count_kills(self) -> None:
        """'Kill two soldiers': each soldier killed counts, also ones killed in the same fight (two at once)."""
        kills = self.fighter.kills
        start = getattr(self, '_kills_read', len(kills))
        self._kills_read = len(kills)
        kind, left = getattr(self, '_fight_all', None), getattr(self, '_fight_left', None)
        if not kind or left is None:
            return
        for _id, type_id in kills[start:]:
            name = None
            try:
                name = (self.adapter.knowledge(type_id) or {}).get('name') if hasattr(self.adapter, 'knowledge') \
                    and type_id is not None else None
            except Exception:
                pass
            if kind == 'enemy' or name is None or name == kind:
                self._fight_left = left = left - 1

    def _hurt_stop(self, snap) -> str | None:
        """Hurt (got away first, or no way away): everything stops, the walk it was on isn't taken up again (it
        went on for the dropped things among the zombies after "you're hurt", and the player died, 2026-10-16);
        food held is eaten. The line to say instead, or None."""
        self._resume_walk = None
        self._fight_all, self._then, self._order_target = None, [], None
        if getattr(self, 'goal', None) is not None:
            self.goal.stop()
        task = getattr(self, 'task', None)
        if task is not None and task.active:
            task.stop('hurt')
        self.walk.stop()
        self.keys.release_all()
        hot = (snap.extra or {}).get('hotbar') or []
        fn = getattr(self.adapter, 'food_slot', None) if getattr(self, 'adapter', None) is not None else None
        if fn is not None and task is not None and fn(hot) is not None:
            self._task_cmd = ([('eat', None)], 'food', 1)
            return "You're hurt! I've stopped, and I'm eating something. Over to you after that!"
        return None

    def _back_to_order(self, snap) -> bool:
        """A fight won against another enemy than the one the order named (it came at the player on the way):
        back to that one if it's still around (2026-10-12 21:35: 'kill the bat and the soldier', the soldier
        attacked first; its death counted as the bat's and the order went on to 'the soldier')."""
        tgt = getattr(self, '_order_target', None)
        if tgt is None:
            return False
        if tgt == self.fighter.target_id:          # the one asked for is down: done with it
            self._order_target = None
            return False
        e = next((x for x in snap.entities if x.id == tgt and not x.dead and (x.health is None or x.health > 0)),
                 None)
        if e is None or not snap.player or math.dist(e.pos, snap.player.pos) > ALL_RANGE:
            self._order_target = None
            return False
        self._order_name = self._enemy_name(e)
        self._walk_request('start', tuple(e.pos), self._order_name, e.id, 'fight')
        return True

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
            if ev in ('arrived', 'gave_up') and self._sv_walking():   # (into cover, away: not the task's walk)
                if self.companion is not None and self.companion.mode == 'guide':
                    self.companion.follow()
                continue
            if ev == 'break':
                self._navi_line("Something's in the way. Let me smash it!")
            elif ev.startswith('work_failed:'):    # a block it couldn't place: planned elsewhere from now on
                while self.walk.failed_work:
                    what, info = self.walk.failed_work.pop(0)
                    if hasattr(self.adapter, 'note_failed_work'):
                        self.adapter.note_failed_work(what, info)
            elif ev == 'replan':  # the step-by-step walk can't go on as planned (or is off the route): plan again
                self.companion.replan()
                self._walk_route = None
                self.walk.set_route([])
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
                elif getattr(self, 'task', None) is not None and self.task.active:
                    if self.companion is not None and self.companion.mode == 'guide':
                        self.companion.follow()
                    self.task.on_event(ev)
                else:
                    if self.companion is not None and self.companion.mode == 'guide':
                        self.companion.follow()     # (else the fairy said "Here we are" a second time)
                    self._ended_as(True)
                    self._navi_line(f"Here we are: {self.walk.name}!" if self.walk.name else 'Here we are!')
                    self._next_order()
            elif ev == 'gave_up':
                self._then = []
                if self.plan.active and self.plan.on_event(ev):
                    continue
                if getattr(self, 'task', None) is not None and self.task.active:
                    note = getattr(getattr(self, 'adapter', None), 'route_note', None)
                    me = getattr(self, '_last_player_pos', None)
                    self.task.on_event(ev, route_reason(self.walk.name or 'there', None, note,
                                                        tuple(me) if me else None))
                    continue
                self.companion.follow()
                me = getattr(self, '_last_player_pos', None)
                why = route_reason(self.walk.name or 'there', None, getattr(self.adapter, 'route_note', None),
                                   tuple(me) if me else None)
                self._ended_as(False, why.kind, **why.data)
                need = (getattr(self.adapter, 'route_note', None) or {}).get('blocks_it_would_take')
                if need:                                 # (what would do it: told, for the doer to act on)
                    self._navi_line(f"I'd need about {need} blocks to build my way there (dirt or cobblestone).")
                order = getattr(self, '_last_order', None)
                if getattr(self, 'doer', None) is not None and not getattr(self, '_doer_busy', False) and order:
                    self._last_order = None              # (once: the doer's own walks don't come back here)
                    self._navi_line("No straight way there. Let me think about how!")
                    threading.Thread(target=self._doer_order, args=(
                        f"{order}\n(The walk found no way there on its own: work out how, e.g. blocks to pillar up or "
                        f"bridge with, a tool, or another way round.)",), daemon=True).start()
                    continue
                self._navi_line("I can't get through here. Over to you!")
            elif ev == 'stopped:you':
                self._ended_as(False, INTERRUPTED, by='the player')
                self._then, self._fight_all = [], None
                if self.plan.active:
                    self.plan.on_event(ev)
                self._navi_line("Okay, you've got it!")
            elif ev == 'paused:not_focused':
                self._navi_line("Click back into the game and I'll keep going.", urgent=True)

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
                    if self.walk.precise:   # (a walk that plans its own way up or down: the guess is noise)
                        continue
                    _, name, dy = event.split(':', 2)
                    dy = int(dy)
                    line = (f"It's right below us, about {abs(dy)} metres down! There must be a way down nearby." if dy < 0
                            else f"It's right above us, about {dy} metres up! There must be a way up nearby.")
                    self._once_line(line)
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
                    note = getattr(getattr(self, 'adapter', None), 'route_note', None) or {}
                    if self.walk.active and self.walk.precise and note.get('found') == 'needs blocks' \
                            and self._blocks_first(int(note.get('blocks_it_would_take') or 0)):
                        continue                                  # (getting blocks to build with first)
                    if self.walk.active and self.walk.precise and note.get('found') in ('no way', 'needs blocks'):
                        self.walk.events.append('gave_up')        # (no way at all: over at once)
                        self.walk.stop('no way')
                    line = f"Hmm, I can't find a way to walk toward {event.split(':', 1)[1]} from here. Let's try another way!"
                    self._once_line(line)
                    continue
                if event.startswith('arrived:'):
                    if self.walk.active or self.fighter.active:  # walking there (it says so), or fighting
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
                self._error('Auto-walk', e)
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
