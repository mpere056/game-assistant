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
        self.resolver = CommandResolver(self.adapter, ask_model=self._model_order)
        self._walk_cmd = None             # ('start', point, name) or ('stop', why), applied by the fairy loop
        self._walk_route = None           # the companion route the walk follows
        self._route_goal = None           # where the route to a moving character was planned to
        self._took_over = 0.0             # when the player last pressed a movement key themselves
        self._last_enemy_line = -1e9
        self.keys = self.turner = None
        if self.cfg['auto_walk'] and self.companion and sys.platform == 'win32':
            from .ui.win_input import Keys, KeyWatch, Turner
            self.keys = Keys(dict(self.cfg['walk_keys']))
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

    def _order_work(self, text: str) -> None:
        try:
            action = self.resolver.handle(text)
        except Exception as e:  # show it instead of dying
            action = WalkAction('none', f"Hmm, I couldn't work that out ({type(e).__name__}).")
        if action.kind == 'walk':
            self._walk_request('start', action.point, action.name, action.entity_id, action.attack)
        elif action.kind == 'stop':
            self._walk_request('stop', 'stop')
        self._navi_line(action.say)
        with self.log.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'t': datetime.now().isoformat(timespec='seconds'), 'order': text,
                                'action': action.kind, 'place': action.name}) + '\n')

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
            got = self.resolver._attack(inp.get('name', 'enemy'))
            return got
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
            self.companion.guide(cmd[1], cmd[2])     # Navi plans the route and leads the way
            entity = cmd[3] if len(cmd) > 3 else None
            self.walk.start([], cmd[1], cmd[2], target_id=entity, attack=bool(cmd[4]) if len(cmd) > 4 else False)
            self._walk_route = None
            self._took_over = 0.0
            self._walk_entity = entity  # walking to a character: re-plan the route as it moves
            self._route_goal = cmd[1]
        elif cmd and cmd[0] == 'stop' and self.walk.active:
            self.walk.stop(cmd[1])
            self.companion.follow()
        if self.walk.active and time.monotonic() - self._took_over < 0.5:
            self.walk.stop('you')
            self.companion.follow()
        if not self.walk.active:
            if self.keys.held:
                self.keys.release_all()
            self._walk_events()
            return
        if getattr(self, '_walk_entity', None) is not None:
            self._follow_entity(snap)
            if not self.walk.active:
                return
        route = self.companion.route
        if route is not None and route is not self._walk_route:  # planned, re-planned or extended
            self.walk.set_route(route, self.companion.route_actions)
            self._walk_route = route
        if snap.player:
            self._last_player_pos = snap.player.pos
        intent = self.walk.update(snap, dt)
        if snap.screen is not None and snap.screen.focused and self.walk.active:
            self.keys.set(intent.keys)
            for t in intent.taps:
                self.keys.tap(t)
            if intent.turn and snap.camera:
                yaw = math.degrees(math.atan2(snap.camera.forward[0], snap.camera.forward[2]))
                self.turner.turn(intent.turn, yaw)
        else:
            self.keys.release_all()
        self._walk_events()

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

    def _walk_events(self) -> None:
        while self.walk.events:
            ev = self.walk.events.pop(0)
            if ev.startswith('enemy:'):
                if time.monotonic() - self._last_enemy_line > 10.0:
                    self._last_enemy_line = time.monotonic()
                    self._navi_line('Careful, an enemy ahead!')
            elif ev == 'stuck':  # avoid what's here (a gap link that doesn't work) and plan again
                if snap_pos := getattr(self, '_last_player_pos', None):
                    if hasattr(self.adapter, 'avoid_links_near'):
                        self.adapter.avoid_links_near(snap_pos)
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
            elif ev == 'arrived':
                self._navi_line(f"Here we are: {self.walk.name}!" if self.walk.name else 'Here we are!')
            elif ev == 'gave_up':
                self.companion.follow()
                self._navi_line("I can't get through here. Over to you!")
            elif ev == 'stopped:you':
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
            view = self.companion.update(snap, start - last)
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
                self._walk_step(snap, 1 / FPS)
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
