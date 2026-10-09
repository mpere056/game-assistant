"""Spoken answers with Windows' own voices (SAPI): free, local, no download.

Answers stream in from the model; feed() collects text and speaks each sentence as soon as it is
complete, so the first words come out while the rest is still being written. stop() cuts speech off
at once. SAPI is a COM object, so it lives on its own thread.
"""
from __future__ import annotations

import queue
import re
import threading

SVSF_ASYNC, SVSF_PURGE = 1, 2
_SENTENCE_END = re.compile(r'([.!?])(\s+|$)')


class Speaker:
    def __init__(self, voice: str = 'Zira', rate: int = 1):
        self.voice_name = voice
        self.rate = rate
        self._q: queue.Queue = queue.Queue()
        self._buf = ''
        self._speaking = False
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name='speaker', daemon=True)
        self._thread.start()

    @property
    def speaking(self) -> bool:
        return self._speaking or not self._q.empty()

    def feed(self, text: str) -> None:
        """Add streamed answer text; complete sentences are spoken right away."""
        self._buf += text
        while True:
            m = _SENTENCE_END.search(self._buf)
            if not m:
                break
            sentence, self._buf = self._buf[:m.end(1)], self._buf[m.end():]
            self.say(sentence)

    def flush(self) -> None:
        """Speak whatever is left once the answer is complete."""
        if self._buf.strip():
            self.say(self._buf)
        self._buf = ''

    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self._q.put(('say', text))

    def stop(self) -> None:
        self._buf = ''
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(('stop', None))

    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            v = win32com.client.Dispatch('SAPI.SpVoice')
            voices = v.GetVoices()
            for i in range(voices.Count):
                if self.voice_name.lower() in voices.Item(i).GetDescription().lower():
                    v.Voice = voices.Item(i)
                    break
            v.Rate = self.rate
        except Exception as e:
            self.error = f'{type(e).__name__}: {e}'
            return
        while True:
            try:
                kind, text = self._q.get(timeout=0.05)
            except queue.Empty:
                self._speaking = v.Status.RunningState == 2  # 2 = speaking
                continue
            if kind == 'stop':
                v.Speak('', SVSF_ASYNC | SVSF_PURGE)
                self._speaking = False
            else:
                self._speaking = True
                v.Speak(text, SVSF_ASYNC)
