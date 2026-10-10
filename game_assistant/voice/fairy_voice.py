"""The fairy's voice: a local neural voice (Kokoro), raised in pitch, with a sparkle and stereo pan.

Kokoro (82M parameters, Apache 2.0, https://github.com/thewh1teagle/kokoro-onnx) runs on the CPU from
two files in .local/voice/ (Get-VoiceModel.bat). Two ways to make it sound like a fairy:
- 'world' (default): the WORLD vocoder (pyworld) sets the pitch to `pitch_hz` (Navi measures about
  558 Hz; Kokoro's voices speak around 200 Hz) and makes the voice `size` times smaller (formants),
  separately, so it stays clear.
- 'speedup': each sentence is made slower and played back `pitch` times faster: pitch and voice size
  rise together (a tiny-creature sound, chipmunk-like when high).
A short two-note sparkle plays before an answer.
Audio is ours (sounddevice), so it is panned left or right to where the fairy is on screen.

Same interface as tts.Speaker (feed, flush, say, stop, speaking, error), so the app can use either.
"""
from __future__ import annotations

import math
import queue
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / '.local' / 'voice' / 'kokoro-v1.0.int8.onnx'
VOICES = ROOT / '.local' / 'voice' / 'voices-v1.0.bin'
SOURCE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
_SENTENCE_END = re.compile(r'([.!?])(\s+|$)')


def available() -> bool:
    return MODEL.exists() and VOICES.exists()


def sparkle(sr: int, volume: float = 0.18):
    """Two quick bell notes (G6, C7) with a soft shimmer: the fairy's 'ting'."""
    import numpy as np
    out = []
    for f in (1568.0, 2093.0):
        n = int(sr * 0.09)
        t = np.arange(n) / sr
        env = np.exp(-t * 28.0)
        tone = np.sin(2 * math.pi * f * t) + 0.35 * np.sin(2 * math.pi * f * 2.01 * t)
        out.append(tone * env)
    return (np.concatenate(out) * volume / 1.35).astype('float32')


def median_f0(a, sr: int, lo: int = 70, hi: int = 1600) -> float:
    """Median pitch of the voiced parts (autocorrelation), 0 if none found."""
    import numpy as np
    fr = int(sr * 0.04)
    out = []
    for i in range(0, len(a) - fr, fr // 2):
        x = a[i:i + fr] * np.hanning(fr)
        if np.sqrt(np.mean(x ** 2)) < 0.02:
            continue
        c = np.correlate(x, x, 'full')[fr - 1:]
        k = int(sr / hi) + int(np.argmax(c[int(sr / hi):int(sr / lo)]))
        if c[k] > 0.35 * c[0]:
            out.append(sr / k)
    return float(np.median(out)) if out else 0.0


def highpass(a, sr: int, hz: float):
    """Remove everything below `hz` (smooth roll-off): the voice at ~550 Hz has nothing useful there."""
    import numpy as np
    n = len(a)
    f = np.fft.rfftfreq(n, 1 / sr)
    gain = np.clip((f - hz * 0.6) / (hz * 0.4), 0, 1) ** 2
    return np.fft.irfft(np.fft.rfft(a) * gain, n).astype('float32')


def _envelope(a, sr: int, ms: float = 12):
    import numpy as np
    k = max(1, int(sr * ms / 1000))
    return np.sqrt(np.convolve(a.astype(np.float64) ** 2, np.ones(k) / k, mode='same'))


def gate(out, ref, sr: int):
    """Never louder than the original voice was at that moment (scaled to the overall level), so noise
    in gaps and at word ends can't stand out."""
    import numpy as np
    e_out, e_ref = _envelope(out, sr), _envelope(ref, sr)
    scale = np.sqrt(np.mean(out.astype(np.float64) ** 2)) / max(1e-9, np.sqrt(np.mean(ref.astype(np.float64) ** 2)))
    g = np.minimum(1.0, (e_ref * scale * 1.3 + 1e-6) / (e_out + 1e-6))
    w = max(1, int(sr * 0.006))
    g = np.convolve(g, np.ones(w) / w, mode='same')  # smooth, no clicks
    return (out * g).astype('float32')


def fill_gaps(f0, max_frames: int = 12):
    """Bridge short unvoiced runs inside speech (up to max_frames of 5 ms): the fast dio tracker's
    mistakes, which WORLD would otherwise fill with breath noise."""
    import numpy as np
    f0 = f0.copy()
    v = f0 > 0
    i, n = 0, len(f0)
    while i < n:
        if v[i]:
            i += 1
            continue
        j = i
        while j < n and not v[j]:
            j += 1
        if i > 0 and j < n and j - i <= max_frames:
            f0[i:j] = np.linspace(f0[i - 1], f0[j], j - i + 2)[1:-1]
        i = j
    return f0


def world_shift(a, sr: int, f0_factor: float, formant_factor: float, breath: float = 0.3, low_cut_hz: float = 280.0,
                tracker: str = 'dio'):
    """Raise the pitch by f0_factor and the voice size (spectral envelope) by formant_factor, apart.

    Cleaned up against a 'blowing into the mic' noise the user heard (2026-10-09): dio misjudged voiced
    sounds as noise (4.5 % of the energy was rumble below 300 Hz). Its short gaps are now bridged
    (`fill_gaps`; rumble 0.4 %, 0.35 s per 4 s of speech), or `tracker='harvest'` (0 %, but 1.1 s); less
    breath noise in voiced frames (`breath`), a high-pass at `low_cut_hz`, and a gate that keeps the
    result no louder than the original voice at each moment."""
    import numpy as np
    import pyworld as pw
    x = a.astype(np.float64)
    if tracker == 'harvest':
        f0, t = pw.harvest(x, sr, f0_floor=70, f0_ceil=800, frame_period=5.0)
    else:
        f0, t = pw.dio(x, sr, f0_floor=70, f0_ceil=800, frame_period=5.0)
        f0 = fill_gaps(pw.stonemask(x, f0, t, sr))
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    voiced = f0 > 0
    ap[voiced] = ap[voiced] * breath
    bins = sp.shape[1]
    src = np.clip(np.arange(bins) / formant_factor, 0, bins - 1)  # stretch the envelope upward
    sp2 = np.stack([np.interp(src, np.arange(bins), row) for row in sp])
    ap2 = np.stack([np.interp(src, np.arange(bins), row) for row in ap])
    y = pw.synthesize(f0 * f0_factor, sp2, ap2, sr)[:len(a)].astype('float32')
    y = gate(highpass(y, sr, low_cut_hz), a, sr)
    return (y / max(1e-6, float(np.max(np.abs(y)))) * 0.9).astype('float32')


def pitch_shift(audio, factor: float):
    """Raise the pitch by `factor` by playing it faster (shorter); tempo rises by the same factor."""
    import numpy as np
    if abs(factor - 1.0) < 1e-3:
        return audio
    n = int(len(audio) / factor)
    return np.interp(np.arange(n) * factor, np.arange(len(audio)), audio).astype('float32')


SAMPLE_RATE = 24000          # Kokoro's output rate
WORKERS = 2                  # pieces prepared at the same time
THREADS_PER_WORKER = 4       # CPU threads each; leaves the rest of the processor to the game
_PIECE_BREAK = re.compile(r'(?<=[,;:])\s+|\s+(?=(?:and|but|so|or|then)\s)')
MIN_PIECE_WORDS = 3          # never speak a piece shorter than this on its own (except whole sentences)
LONG_PIECE_WORDS = 7         # sentences longer than this are split at commas and "and/but/so/or/then"


def split_pieces(sentence: str) -> list[str]:
    """A sentence in speakable pieces: whole if short, else at commas and conjunctions."""
    if len(sentence.split()) <= LONG_PIECE_WORDS:
        return [sentence]
    parts = [p.strip() for p in _PIECE_BREAK.split(sentence) if p and p.strip()]
    out: list[str] = []
    for part in parts:
        if out and (len(out[-1].split()) < MIN_PIECE_WORDS or len(part.split()) < MIN_PIECE_WORDS):
            out[-1] = f'{out[-1]} {part}'
        else:
            out.append(part)
    return out


class FairyVoice:
    """Speaks streamed answers in pieces. Timeline of an answer:
    - the sparkle plays the moment the first text arrives;
    - each sentence (split at commas and conjunctions when long) is made by one of WORKERS workers as
      soon as its text is complete, while earlier pieces play;
    - pieces go into one continuous output stream in order, so they join without gaps or clicks;
    - short phrases ("Hey!", "This way!") are cached and come back instantly.
    """

    def __init__(self, voice: str = 'af_heart', pitch: float = 1.25, speed: float = 1.12, chime: bool = True,
                 volume: float = 0.9, method: str = 'world', pitch_hz: float = 440.0, size: float = 1.3):
        self.voice, self.pitch, self.speed, self.chime, self.volume = voice, pitch, speed, chime, volume
        self.method, self.pitch_hz, self.size = method, pitch_hz, size
        self._voice_hz: float | None = None  # the chosen voice's own pitch, measured once
        self.pan = 0.0               # -1 left .. +1 right; set by the app from the fairy's screen position
        self.error: str | None = None
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()      # (gen, seq, text)
        self._done: dict[int, object] = {}           # seq -> mono audio, waiting for its turn
        self._next_seq = 0                           # next piece to hand out
        self._play_seq = 0                           # next piece to play
        self._gen = 0                                # bumped by stop(): stale pieces are dropped
        self._buf = ''
        self._first_of_answer = True
        self._out = None                             # numpy buffer being played (stereo)
        self._out_pos = 0
        self._queue_out: list = []                   # stereo blocks waiting for the output stream
        self._cache: dict[str, object] = {}          # short phrase -> mono audio
        self.last_timings: list[tuple[str, float]] = []  # (event, time) for measuring
        self.gap_seconds = 0.0                       # silence heard mid-answer while waiting for a piece
        threading.Thread(target=self._start, name='fairy voice', daemon=True).start()

    # ---- same interface as tts.Speaker ----

    @property
    def speaking(self) -> bool:
        with self._lock:
            return bool(self._out is not None or self._queue_out or self._done or self._play_seq < self._next_seq)

    def feed(self, text: str) -> None:
        """Add streamed answer text. Complete sentences are spoken at once; a long sentence is also
        cut at a comma as soon as the comma has arrived, so its first part starts sooner."""
        if self._first_of_answer and text.strip():
            self._first_of_answer = False
            if self.chime:
                self._play_now(sparkle(SAMPLE_RATE))
        self._buf += text
        while True:
            m = _SENTENCE_END.search(self._buf)
            if m:
                sentence, self._buf = self._buf[:m.end(1)], self._buf[m.end():]
                for piece in split_pieces(sentence):
                    self._enqueue(piece)
                continue
            m = re.search(r'[,;:]\s', self._buf)  # an unfinished sentence: its first clause is ready
            if m and len(self._buf[:m.start()].split()) >= LONG_PIECE_WORDS - 2:
                piece, self._buf = self._buf[:m.start() + 1], self._buf[m.end():]
                self._enqueue(piece)
                continue
            break

    def flush(self) -> None:
        if self._buf.strip():
            for piece in split_pieces(self._buf.strip()):
                self._enqueue(piece)
        self._buf = ''
        self._first_of_answer = True

    def say(self, text: str) -> None:
        """A whole short line (instant commands, arrivals)."""
        self.feed(text if text.rstrip()[-1:] in '.!?' else text + '.')
        self.flush()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._buf = ''
            self._first_of_answer = True
            self._done.clear()
            self._play_seq = self._next_seq
            self._out, self._out_pos = None, 0
            self._queue_out.clear()
        while not self._jobs.empty():
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break

    # ---- internals ----

    def _enqueue(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            gen = self._gen
        self.last_timings.append((f'text: {text}', time.perf_counter()))
        cached = self._cache.get(text.lower())
        if cached is not None:
            self._finish(gen, seq, cached)
        else:
            self._jobs.put((gen, seq, text))

    def _stereo(self, mono):
        import numpy as np
        p = max(-1.0, min(1.0, self.pan)) * 0.6  # equal-power pan, gentle: never only one ear
        left, right = math.cos((p + 1) * math.pi / 4), math.sin((p + 1) * math.pi / 4)
        return np.stack([mono * left, mono * right], axis=1).astype('float32')

    def _play_now(self, mono) -> None:
        with self._lock:
            self._queue_out.append(self._stereo(mono))

    def _finish(self, gen: int, seq: int, mono) -> None:
        import numpy as np
        with self._lock:
            if gen != self._gen:
                return
            self._done[seq] = mono
            while self._play_seq in self._done:  # hand pieces to the output strictly in order
                piece = self._done.pop(self._play_seq)
                self._play_seq += 1
                self._queue_out.append(self._stereo(np.concatenate([piece, np.zeros(int(SAMPLE_RATE * 0.04), 'float32')])))

    def _make_audio(self, tts, text: str):
        import numpy as np
        if self.method == 'world':
            audio, sr = tts.create(text, voice=self.voice, speed=min(2.0, max(0.5, self.speed)), lang='en-us')
            audio = np.asarray(audio, dtype='float32')
            return world_shift(audio, sr, self.pitch_hz / (self._voice_hz or 200.0), self.size) * self.volume
        audio, sr = tts.create(text, voice=self.voice, speed=min(2.0, max(0.5, self.speed / self.pitch)), lang='en-us')
        return pitch_shift(np.asarray(audio, dtype='float32'), self.pitch) * self.volume

    def _start(self) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            import sounddevice as sd
            from kokoro_onnx import Kokoro
            workers = []
            for _ in range(WORKERS):
                o = ort.SessionOptions()
                o.intra_op_num_threads = THREADS_PER_WORKER
                o.inter_op_num_threads = 1
                sess = ort.InferenceSession(str(MODEL), o, providers=['CPUExecutionProvider'])
                workers.append(Kokoro.from_session(sess, str(VOICES)))
            # Warm up, and measure this voice's own pitch once from a full sentence.
            warm, wsr = workers[0].create('Hello there, it is good to see you again today.', voice=self.voice,
                                          speed=1.0, lang='en-us')
            self._voice_hz = median_f0(np.asarray(warm, dtype='float32'), wsr) or 200.0
            for w in workers[1:]:
                w.create('Hi.', voice=self.voice, speed=1.0, lang='en-us')
            stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype='float32', callback=self._callback,
                                     blocksize=0, latency='low')
            stream.start()
            self._stream = stream  # keep it alive
        except Exception as e:
            self.error = f'{type(e).__name__}: {e}'
            self.ready.set()
            return
        for i, w in enumerate(workers):
            threading.Thread(target=self._work, args=(w,), name=f'fairy voice worker {i}', daemon=True).start()
        self.ready.set()

    def _work(self, tts) -> None:
        while True:
            gen, seq, text = self._jobs.get()
            if gen != self._gen:
                continue
            try:
                mono = self._make_audio(tts, text)
            except Exception as e:
                self.error = f'{type(e).__name__}: {e}'
                import numpy as np
                mono = np.zeros(1, 'float32')  # keep the order moving
            if len(text.split()) <= 3:
                if len(self._cache) > 64:
                    self._cache.clear()
                self._cache[text.lower()] = mono
            self.last_timings.append((f'ready: {text}', time.perf_counter()))
            self._finish(gen, seq, mono)

    def _callback(self, outdata, frames, time_info, status) -> None:
        """The sound card asks for the next `frames` samples: copy from the queued pieces, else silence."""
        filled = 0
        with self._lock:
            while filled < frames:
                if self._out is None:
                    if not self._queue_out:
                        break
                    self._out, self._out_pos = self._queue_out.pop(0), 0
                    self.last_timings.append(('play', time.perf_counter()))
                n = min(frames - filled, len(self._out) - self._out_pos)
                outdata[filled:filled + n] = self._out[self._out_pos:self._out_pos + n]
                filled += n
                self._out_pos += n
                if self._out_pos >= len(self._out):
                    self._out = None
        if filled < frames:
            outdata[filled:] = 0
            if self._play_seq < self._next_seq:  # silence while a piece is still being made: a gap
                self.gap_seconds += (frames - filled) / SAMPLE_RATE
