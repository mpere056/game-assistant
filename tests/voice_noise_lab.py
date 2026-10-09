"""Finds what removes the 'blowing into the mic' noise from the WORLD-shifted fairy voice.

Run: .venv\\Scripts\\python -m tests.voice_noise_lab
Writes runtime/voice_samples/v3/*.wav and prints two noise measures per variant:
- gaps:   loudness where the original voice was quiet (between and at the ends of words), dB below the
          voice. More negative is cleaner.
- rumble: share of energy below 300 Hz (the voice itself is around 550 Hz). Lower is cleaner.
"""
import time
import wave
from pathlib import Path

import numpy as np

from game_assistant.voice.fairy_voice import MODEL, VOICES, median_f0, sparkle
from game_assistant.voice.fairy_voice import world_shift as real_world_shift

OUT = Path('runtime/voice_samples/v3')
LINE = "Hey! Listen! A Giant Dog, about fifty metres ahead. It's weak to fire!"


def save(p, a, sr):
    pcm = (np.clip(a, -1, 1) * 32767).astype('<i2')
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def highpass(a, sr, hz):
    n = len(a)
    spec = np.fft.rfft(a)
    f = np.fft.rfftfreq(n, 1 / sr)
    gain = np.clip((f - hz * 0.6) / (hz * 0.4), 0, 1) ** 2  # smooth roll-off up to `hz`
    return np.fft.irfft(spec * gain, n).astype('float32')


def envelope(a, sr, ms=12):
    k = max(1, int(sr * ms / 1000))
    return np.sqrt(np.convolve(a.astype(np.float64) ** 2, np.ones(k) / k, mode='same'))


def gate(out, ref, sr):
    """Never louder than the original voice was at that moment (scaled to the overall level)."""
    e_out, e_ref = envelope(out, sr), envelope(ref, sr)
    scale = np.sqrt(np.mean(out.astype(np.float64) ** 2)) / max(1e-9, np.sqrt(np.mean(ref.astype(np.float64) ** 2)))
    g = np.minimum(1.0, (e_ref * scale * 1.3 + 1e-6) / (e_out + 1e-6))
    g = np.convolve(g, np.ones(int(sr * 0.006)) / int(sr * 0.006), mode='same')  # no clicks
    return (out * g).astype('float32')


def shift(a, sr, target_hz, size, tracker='dio', breath=1.0):
    import pyworld as pw
    x = a.astype(np.float64)
    if tracker == 'harvest':
        f0, t = pw.harvest(x, sr, f0_floor=70, f0_ceil=800, frame_period=5.0)
    else:
        f0, t = pw.dio(x, sr, f0_floor=70, f0_ceil=800)
        f0 = pw.stonemask(x, f0, t, sr)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    if breath != 1.0:  # voiced frames: less noise mixed into the voice
        voiced = f0 > 0
        ap[voiced] = ap[voiced] * breath
    bins = sp.shape[1]
    src = np.clip(np.arange(bins) / size, 0, bins - 1)
    sp2 = np.stack([np.interp(src, np.arange(bins), r) for r in sp])
    ap2 = np.stack([np.interp(src, np.arange(bins), r) for r in ap])
    own = median_f0(a, sr) or 200.0
    y = pw.synthesize(f0 * (target_hz / own), sp2, ap2, sr)[:len(a)]
    return (y / max(1e-6, float(np.max(np.abs(y)))) * 0.9).astype('float32')


def measure(out, ref, sr):
    e_ref, e_out = envelope(ref, sr), envelope(out, sr)
    quiet = e_ref < 0.04 * e_ref.max()
    loud = e_ref > 0.25 * e_ref.max()
    gaps = 20 * np.log10((e_out[quiet].mean() + 1e-9) / (e_out[loud].mean() + 1e-9))
    spec = np.abs(np.fft.rfft(out)) ** 2
    f = np.fft.rfftfreq(len(out), 1 / sr)
    return gaps, 100 * spec[f < 300].sum() / spec.sum()


def main():
    from kokoro_onnx import Kokoro
    OUT.mkdir(parents=True, exist_ok=True)
    k = Kokoro(str(MODEL), str(VOICES))
    for voice, size, label in (('af_heart', 1.3, 'B4_heart_1.3'), ('af_bella', 1.45, 'B3_bella_1.45')):
        a, sr = k.create(LINE, voice=voice, speed=1.12, lang='en-us')
        a = np.asarray(a, 'float32')
        variants = {
            'v0_as_before': lambda: shift(a, sr, 550, size, 'dio'),
            'v1_better_pitch_tracking': lambda: shift(a, sr, 550, size, 'harvest'),
            'v2_plus_less_breath': lambda: shift(a, sr, 550, size, 'harvest', breath=0.3),
            'v3_plus_filter_and_gate': lambda: gate(highpass(shift(a, sr, 550, size, 'harvest', breath=0.3), sr, 280), a, sr),
            'v4_what_the_game_plays': lambda: real_world_shift(a, sr, 550 / (median_f0(a, sr) or 200.0), size),
        }
        for name, make in variants.items():
            t0 = time.time()
            out = make()
            took = time.time() - t0
            gaps, rumble = measure(out, a, sr)
            full = np.concatenate([sparkle(sr), np.zeros(int(sr * 0.05), 'float32'), out])
            save(OUT / f'{label}_{name}.wav', full, sr)
            print(f'{label:14s} {name:26s} gaps {gaps:6.1f} dB, rumble {rumble:4.1f} %, processing {took:.2f} s')


if __name__ == '__main__':
    main()
