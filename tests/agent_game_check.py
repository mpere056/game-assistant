"""Ask the real agent questions against the running game (real Haiku calls, pennies).
Run with Elden Ring up: .venv\\Scripts\\python -m tests.agent_game_check ["question" ...]"""
import io
import sys

from game_assistant.core.agent import Agent
from game_assistant.core.companion import Companion
from game_assistant.games import adapter_for
from game_assistant.ui.fairy_overlay import make_dpi_aware

DEFAULT = ["Where's Stormveil?", 'Is there a weapon I can get that is close by?', 'What is there to do around here?',
           'What is that in front of me?', 'How do I beat Margit?']


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    make_dpi_aware()
    a = adapter_for('eldenring')
    a.warm_up()
    c = Companion(a.raycast)
    c.update(a.snapshot(), 1 / 60)
    agent = Agent(a, companion=c)
    for q in sys.argv[1:] or DEFAULT:
        r = agent.ask(q)
        print(f'Q: {q}\nA: {r.text.strip()}\n   tools {r.tools_used}, ${r.usd:.4f}, '
              f'first word {r.first_word_s and round(r.first_word_s, 1)} s\n')


if __name__ == '__main__':
    main()
