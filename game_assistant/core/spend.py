"""Hourly spending cap for hosted model calls (docs/PLAN.md, "Spending cap").

Every hosted call records its token usage here. The cost of the last 60 minutes is summed from a
ledger file in runtime/, so the cap holds across restarts. At 80 % of the cap the assistant warns
once; at the cap, hosted calls are refused until the window drops back under it. Local features
(walking, saved skills, monitors, "stop") never go through this guard.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'runtime' / 'assistant-spend.jsonl'

HOURLY_CAP_USD = 3.00
WARN_FRACTION = 0.8
WINDOW_S = 3600

# Published per-million-token prices (USD), October 2026. Cache reads are charged here at the full
# input price, which overestimates slightly and so errs on the safe side.
PRICES = {
    'claude-haiku-5-5': (0.10, 0.50),   # prompts up to 100K tokens
    'claude-sonnet-5-5': (2.00, 10.00),
}


class SpendCapReached(RuntimeError):
    pass


def cost(model: str, usage) -> float:
    """USD for one response's `usage` (an API usage object or a dict)."""
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    inp = (get('input_tokens') or 0) + (get('cache_read_input_tokens') or 0) + (get('cache_creation_input_tokens') or 0)
    out = get('output_tokens') or 0
    pin, pout = PRICES[model]
    return (inp * pin + out * pout) / 1e6


class SpendGuard:
    def __init__(self, cap: float = HOURLY_CAP_USD, ledger: Path = LEDGER):
        self.cap = cap
        self.ledger = ledger
        self._warned = False

    def _entries(self, now: float) -> list[dict]:
        if not self.ledger.exists():
            return []
        out = []
        for line in self.ledger.read_text('utf-8').splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if now - e.get('t', 0) < WINDOW_S:
                out.append(e)
        return out

    def last_hour(self) -> float:
        return sum(e['usd'] for e in self._entries(time.time()))

    def check(self) -> str | None:
        """Call before a hosted call. Raises at the cap; returns a one-time warning near it."""
        spent = self.last_hour()
        if spent >= self.cap:
            raise SpendCapReached(f'Hourly spending cap reached (${spent:.2f} of ${self.cap:.2f} in the last hour). '
                                  'Hosted calls are paused; local features keep working.')
        if spent >= self.cap * WARN_FRACTION and not self._warned:
            self._warned = True
            return f'Heads up: ${spent:.2f} of the ${self.cap:.2f} hourly cap used.'
        return None

    def record(self, model: str, usage, what: str = '') -> float:
        usd = cost(model, usage)
        self.ledger.parent.mkdir(exist_ok=True)
        with self.ledger.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'t': time.time(), 'model': model, 'usd': round(usd, 6), 'what': what}) + '\n')
        return usd
