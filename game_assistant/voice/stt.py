"""Push-to-talk speech recognition, local: hold a key, speak, let go (phase 2b).

While the key is held the microphone is recorded (16 kHz mono); on release faster-whisper (an open
Whisper model on the CPU, int8, so the GPU stays with the game) turns it into text, which goes to the
same place as typed questions. The model downloads once (base.en: about 145 MB) and loads in the
background at start-up. The key is read with GetAsyncKeyState, so it works while the game has focus.
"""
from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3
VK = {f'F{i}': 0x6F + i for i in range(1, 13)}


def key_code(name: str) -> int:
    name = name.strip().upper()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name)
    raise ValueError(f'push-to-talk key "{name}" is not supported (use F1-F12 or a letter)')


class PushToTalk:
    def __init__(self, on_text: Callable[[str], None], key: str = 'F9', model: str = 'base.en', microphone: str = '',
                 on_state: Callable[[str], None] = lambda s: None, vocabulary: str = ''):
        self.on_text = on_text
        self.on_state = on_state      # 'loading', 'ready', 'listening', 'thinking', or an error text
        self.vk = key_code(key)
        self.model_name = model
        self.microphone = microphone
        # Game words (from the adapter) steer recognition: "Moonveil", not "Moonvale".
        self.prompt = vocabulary
        self.model = None
        self.error: str | None = None
        self.last_seconds: float | None = None  # transcription time of the last utterance
        threading.Thread(target=self._run, name='push to talk', daemon=True).start()

    def _device(self):
        if not self.microphone:
            return None
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0 and self.microphone.lower() in d['name'].lower():
                return i
        return None

    def _load(self) -> None:
        self.on_state('loading')
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.model_name, device='cpu', compute_type='int8')
        self.on_state('ready')

    def transcribe(self, audio) -> str:
        t0 = time.perf_counter()
        segments, _info = self.model.transcribe(audio, language='en', beam_size=1, vad_filter=True,
                                                initial_prompt=self.prompt or None)
        text = ' '.join(s.text.strip() for s in segments).strip()
        self.last_seconds = time.perf_counter() - t0
        return text

    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            self._load()
        except Exception as e:
            self.error = f'{type(e).__name__}: {e}'
            self.on_state(f'voice input unavailable: {self.error}')
            return
        get = ctypes.windll.user32.GetAsyncKeyState
        while True:
            if not get(self.vk) & 0x8000:
                time.sleep(0.02)
                continue
            self.on_state('listening')
            chunks = []
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', device=self._device(),
                                    callback=lambda data, frames, t, status: chunks.append(data.copy())):
                    while get(self.vk) & 0x8000:
                        time.sleep(0.02)
            except Exception as e:
                self.on_state(f'microphone error: {e}')
                time.sleep(1)
                continue
            audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype='float32')
            if len(audio) < SAMPLE_RATE * MIN_SECONDS:
                self.on_state('ready')
                continue
            self.on_state('thinking')
            try:
                text = self.transcribe(audio)
            except Exception as e:
                self.on_state(f'speech recognition error: {e}')
                continue
            self.on_state('ready')
            if text:
                self.on_text(text)
