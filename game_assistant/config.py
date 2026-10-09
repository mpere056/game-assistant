"""Settings the player may change, in .local/settings.json (created with defaults on first run).

Not committed: it is per machine. Unknown keys are ignored; missing keys take the defaults.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / '.local' / 'settings.json'

DEFAULTS = {
    'fairy': True,                    # show the fairy companion over the game
    'fairy_color': [120, 214, 255],   # R, G, B of its glow
    'fairy_scale': 1.0,               # its size on screen (1 = normal)
    'leash_m': 75.0,                  # never further than this from your character
    'speak_answers': True,            # read answers aloud
    'voice': 'Zira',                  # part of a Windows voice name (Zira, David...)
    'voice_rate': 1,                  # -10 (slow) .. 10 (fast)
    'push_to_talk_key': 'F9',         # hold to talk: F1-F12, or a single letter
    'speech_model': 'base.en',        # faster-whisper model: tiny.en, base.en, small.en
    'microphone': '',                 # part of the microphone's name; '' = the Windows default
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if FILE.exists():
        try:
            cfg.update({k: v for k, v in json.loads(FILE.read_text('utf-8')).items() if k in DEFAULTS})
        except ValueError:
            pass  # a broken file: keep the defaults rather than fail
    else:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(DEFAULTS, indent=2) + '\n', 'utf-8')
    return cfg
