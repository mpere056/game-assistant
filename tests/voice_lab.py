"""Makes fairy-voice samples to compare by ear, and measures their pitch against a reference.

Run: .venv\\Scripts\\python -m tests.voice_lab [reference.wav]
Writes runtime/voice_samples/v2/*.wav. Two ways of raising the pitch:
- speedup: play faster (pitch and voice size rise together: a tiny creature, chipmunk-like when high)
- world:   the WORLD vocoder sets the pitch (to `target_hz`) and the voice size (formants) separately
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

from game_assistant.voice.fairy_voice import MODEL, VOICES, median_f0, pitch_shift, sparkle, world_shift

OUT = Path('runtime/voice_samples/v2')
LINE = "Hey! Listen! A Giant Dog, about fifty metres ahead. It's weak to fire!"


def load(p):
    w = wave.open(str(p))
    sr, ch = w.getframerate(), w.getnchannels()
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype('float32') / 32768
    return a.reshape(-1, ch).mean(axis=1), sr


def save(p, a, sr):
    pcm = (np.clip(a, -1, 1) * 32767).astype('<i2')
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    from kokoro_onnx import Kokoro
    ref = sys.argv[1] if len(sys.argv) > 1 else None
    target = 550.0
    if ref:
        a, sr = load(ref)
        target = median_f0(a, sr)
        print(f'reference pitch: {target:.0f} Hz')
    OUT.mkdir(parents=True, exist_ok=True)
    k = Kokoro(str(MODEL), str(VOICES))
    k.create('Hi.', voice='af_bella', lang='en-us')
    jobs = [
        ('A1_bella_speedup_1.6', 'af_bella', 'speedup', 1.6, None),
        ('A2_bella_speedup_2.0', 'af_bella', 'speedup', 2.0, None),
        ('A3_bella_speedup_2.4', 'af_bella', 'speedup', 2.4, None),
        ('B1_bella_navi_pitch_small_voice_1.15', 'af_bella', 'world', None, 1.15),
        ('B2_bella_navi_pitch_smaller_voice_1.3', 'af_bella', 'world', None, 1.3),
        ('B3_bella_navi_pitch_tiny_voice_1.45', 'af_bella', 'world', None, 1.45),
        ('B4_heart_navi_pitch_smaller_voice_1.3', 'af_heart', 'world', None, 1.3),
        ('B5_sky_navi_pitch_smaller_voice_1.3', 'af_sky', 'world', None, 1.3),
        ('B6_nicole_navi_pitch_smaller_voice_1.3', 'af_nicole', 'world', None, 1.3),
    ]
    for name, voice, method, factor, formant in jobs:
        t0 = time.time()
        if method == 'speedup':
            a, sr = k.create(LINE, voice=voice, speed=max(0.5, 1.08 / factor), lang='en-us')  # Kokoro: 0.5-2.0
            a = pitch_shift(np.asarray(a, 'float32'), factor) * 0.9
        else:
            a, sr = k.create(LINE, voice=voice, speed=1.12, lang='en-us')
            a = np.asarray(a, 'float32')
            a = world_shift(a, sr, target / max(1.0, median_f0(a, sr)), formant)
        took = time.time() - t0
        a = np.concatenate([sparkle(sr), np.zeros(int(sr * 0.05), 'float32'), a])
        save(OUT / f'{name}.wav', a, sr)
        print(f'{name:42s} pitch {median_f0(a, sr):4.0f} Hz, {len(a) / sr:.1f} s of audio, made in {took:.1f} s')


if __name__ == '__main__':
    main()
