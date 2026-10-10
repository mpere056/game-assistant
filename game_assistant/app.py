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
from .core.companion import Companion
from .core.interface import CAP_CAMERA, CAP_RAYCAST, CAP_SCREEN, GameNotRunning
from .core.spend import SpendCapReached
from .games import adapter_for

ROOT = Path(__file__).resolve().parents[1]
FPS = 60


def _own_window_in_front() -> bool:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value == os.getpid()


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
            self.overlay = FairyOverlay(tuple(self.cfg['fairy_color']))
            self.overlay.start()
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
                                        stop_speech=self.speaker.stop if self.speaker else (lambda: None))
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.running = True
        self.log = ROOT / 'runtime' / f'chat-{datetime.now():%Y%m%d}.jsonl'
        self.log.parent.mkdir(exist_ok=True)
        self.fairy_ms = 0.0  # time spent per fairy frame, for the status line
        self.last_view = None

        self._build_window()
        self.ptt = None
        try:
            from .voice.stt import PushToTalk
            self.ptt = PushToTalk(on_text=lambda t: self.q.put(('voice', t)), key=self.cfg['push_to_talk_key'],
                                  model=self.cfg['speech_model'], microphone=self.cfg['microphone'],
                                  on_state=lambda s: self.q.put(('voice_state', s)),
                                  vocabulary=getattr(self.adapter, 'vocabulary', ''))
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
            self.ask(question, 'typed')

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
                    if self.speaker:
                        self.speaker.say(reply)
                self.q.put(('meta', '\n(instant, no cost)\n'))
            else:
                if self.speaker:
                    self.speaker.stop()

                def on_text(s: str) -> None:
                    self.q.put(('text', s))
                    if self.speaker:
                        self.speaker.feed(s)

                ans = self.agent.ask(question, on_text=on_text)
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
                time.sleep(1.0)
                last = time.perf_counter()
                continue
            self.companion.speaking = bool(self.speaker and self.speaker.speaking)
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
                    if self.speaker:
                        self.speaker.say(line)
                    continue
                if event.startswith('no_way:'):
                    line = f"Hmm, I can't find a way to walk toward {event.split(':', 1)[1]} from here. Let's try another way!"
                    self.q.put(('meta', '\n' + line + '\n'))
                    if self.speaker:
                        self.speaker.say(line)
                    continue
                if event.startswith('arrived:'):
                    line = f"Here we are: {event.split(':', 1)[1]}!"
                    self.q.put(('meta', '\n' + line + '\n'))
                    if self.speaker:
                        self.speaker.say(line)
            last = start
            in_front = snap.screen is not None and (snap.screen.focused or _own_window_in_front())
            if view.visible and snap.screen and hasattr(self.speaker, 'pan'):  # the voice comes from the fairy
                self.speaker.pan = ((view.screen_x - snap.screen.x) / max(1, snap.screen.width)) * 2 - 1
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
                elif kind == 'voice_state':
                    self._voice_state = s
                else:
                    self.busy = False
        except queue.Empty:
            pass
        parts = []
        state = getattr(self, '_voice_state', 'loading')
        parts.append({'loading': 'voice: loading speech model...', 'ready': f'hold {self.cfg["push_to_talk_key"]} to talk',
                      'listening': 'listening...', 'thinking': 'hearing you...'}.get(state, state))
        if self.companion:
            parts.append(f'fairy: {self.companion.mode}{" (waiting at the leash)" if self.companion.waiting else ""}, '
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
