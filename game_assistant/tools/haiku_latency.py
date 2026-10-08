"""Measures Claude Haiku 5.5's real speed from this PC (phase 1 gate).

    Measure-Haiku.bat           (or: .venv\\Scripts\\python -m game_assistant.tools.haiku_latency)

Needs an Anthropic API key in the ANTHROPIC_API_KEY environment variable (never put it in the
repository). Makes about 20 small calls, well under one cent in total, each counted against the
hourly spending cap. Compares thinking off against adaptive thinking at low effort, for a plain
answer and for a tool call, and saves the numbers to runtime/haiku-latency-<time>.txt.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

from ..core.spend import SpendGuard

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'claude-haiku-5-5'
RUNS = 5

SYSTEM = (
    'You are a game assistant inside Elden Ring. Answer in one or two short sentences. '
    'Use only the facts you are given or that a tool returns; if a fact is missing, say you do not know.'
)
FACTS = ('Facts: looking_at=entity, confidence=0.9, thing={name: "Godrick Soldier", hostile: true, '
         'distance_m: 12.4, where_on_screen: "centre", health: "212/212"}')
QUESTION = 'What am I looking at, and is it dangerous?'

LOOK_TOOL = {
    'name': 'look_at',
    'description': 'Returns exact facts about what the player is looking at right now, from the game itself.',
    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
}

CONFIGS = [
    ('thinking off', {'thinking': {'type': 'disabled'}, 'output_config': {'effort': 'low'}}),
    ('adaptive thinking, low effort', {'thinking': {'type': 'adaptive'}, 'output_config': {'effort': 'low'}}),
]


def one_call(client, guard, extra, tools) -> dict:
    warning = guard.check()
    if warning:
        print(warning)
    kwargs = dict(model=MODEL, max_tokens=300, system=SYSTEM, **extra)
    if tools:
        kwargs['tools'] = [LOOK_TOOL]
        kwargs['messages'] = [{'role': 'user', 'content': QUESTION}]
    else:
        kwargs['messages'] = [{'role': 'user', 'content': f'{FACTS}\n\n{QUESTION}'}]
    t0 = time.perf_counter()
    first = None
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            if first is None:
                if event.type == 'content_block_delta' and event.delta.type == 'text_delta' and not tools:
                    first = time.perf_counter() - t0
                elif event.type == 'content_block_start' and event.content_block.type == 'tool_use' and tools:
                    first = time.perf_counter() - t0
        msg = stream.get_final_message()
    total = time.perf_counter() - t0
    usd = guard.record(MODEL, msg.usage, 'latency test')
    text = ''.join(b.text for b in msg.content if b.type == 'text')
    called = any(b.type == 'tool_use' and b.name == 'look_at' for b in msg.content)
    return dict(first=first, total=total, usd=usd, text=text, called=called, stop=msg.stop_reason,
                out=msg.usage.output_tokens)


def main() -> int:
    if not os.environ.get('ANTHROPIC_API_KEY'):
        sys.exit('No Anthropic API key in this window. If you just ran setx, close this window and open a new one\n'
                 '(setx only reaches windows opened afterwards), or set it for this window only:\n'
                 '  $env:ANTHROPIC_API_KEY = "sk-ant-..."   (PowerShell)\n'
                 '  set ANTHROPIC_API_KEY=sk-ant-...        (Command Prompt)\n'
                 'Do not save the key in any file in this project.')
    client = anthropic.Anthropic()
    try:
        client.models.retrieve(MODEL)  # free; fails fast if the key is wrong
    except anthropic.AuthenticationError:
        sys.exit('The API key was rejected. Use a key from the API Keys page of the Claude Console '
                 '(platform.claude.com); API keys start with sk-ant-api.')
    except anthropic.APIError as e:
        sys.exit(f'The API refused the check: {type(e).__name__}: {e}')
    guard = SpendGuard()
    lines = [f'Haiku 5.5 latency from this PC, {datetime.now():%Y-%m-%d %H:%M}, {RUNS} runs each']
    total_usd = 0.0
    for tools in (False, True):
        for name, extra in CONFIGS:
            runs = [one_call(client, guard, extra, tools) for _ in range(RUNS)]
            total_usd += sum(x['usd'] for x in runs)
            firsts = [x['first'] for x in runs if x['first'] is not None]
            label = f'{"tool call" if tools else "answer from facts"}, {name}'
            what = 'time to the tool call' if tools else 'time to first word'
            if firsts:
                line = (f'{label}: {what} median {statistics.median(firsts) * 1000:.0f} ms '
                        f'(min {min(firsts) * 1000:.0f}, max {max(firsts) * 1000:.0f}); '
                        f'whole reply median {statistics.median(x["total"] for x in runs) * 1000:.0f} ms; '
                        f'output tokens median {statistics.median(x["out"] for x in runs):.0f}')
            else:
                line = f'{label}: no {"tool call" if tools else "text"} came back (stop reasons: {[x["stop"] for x in runs]})'
            if tools:
                line += f'; called look_at in {sum(x["called"] for x in runs)}/{RUNS}'
            else:
                line += f'\n    sample answer: {runs[-1]["text"].strip()!r}'
            print(line)
            lines.append(line)
    lines.append(f'Cost of this test: ${total_usd:.4f}; last hour total ${guard.last_hour():.4f} of ${guard.cap:.2f} cap')
    print(lines[-1])
    out = ROOT / 'runtime' / f'haiku-latency-{datetime.now():%Y%m%d-%H%M%S}.txt'
    out.parent.mkdir(exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', 'utf-8')
    print(f'Saved to runtime/{out.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
