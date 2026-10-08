# Game Assistant

An AI assistant you talk to while you play. It answers questions about the game, such as "what am
I looking at, and what is it weak to?", from the game's own data, and later walks you places and
does tasks for you. It is built to work across games: everything game-specific lives in one
adapter per game, and the rest is shared.

**Games:** Elden Ring (connected). Minecraft planned next. Others possible later.

**Status:** phase 1 done. The Elden Ring adapter passes its live tests, the look-at resolver names
what is at the crosshair from geometry alone, and Claude Haiku 5.5 answers in about half a second.
Phase 2 (asking questions in a chat window) is next. See [docs/STATUS.md](docs/STATUS.md).

## How it works

- **Code does everything fast**: reading game state, working out what you are looking at,
  pathfinding and safety checks. It costs nothing and never waits on a network.
- **Claude Haiku 5.5** talks to you, picks the right tool for each request and phrases the exact
  facts the game gives it. **Claude Sonnet 5.5** writes new skills (phase 4).
- **Facts come from the game's own data**, never from a model's memory. When a lookup finds
  nothing, it says so.
- **A $3/hour spending cap** guards every hosted call.
- **Voice** (push-to-talk, local speech recognition and speech) comes after text works.

Full plan: [docs/PLAN.md](docs/PLAN.md). Game adapter interface: [docs/ADAPTER.md](docs/ADAPTER.md).

## Setup

1. Python 3.12. Run `Setup.bat`: it creates `.venv` and installs the Anthropic SDK.
2. An Anthropic API key from the [Claude Console](https://platform.claude.com) (API Keys page),
   saved outside the project: `setx ANTHROPIC_API_KEY "sk-ant-..."`, then open a new window.
   Never put the key in a file in this project.
3. The game's bridge. Elden Ring: [Attack on Elden Ring](https://github.com/mpere056/attack-on-elden-ring)
   (see [docs/games/eldenring.md](docs/games/eldenring.md)).

## Tools

| Command | What it does |
|---------|--------------|
| `Check-Adapter.bat` | Live adapter tests for a game (default Elden Ring); all PASS = connected |
| `Check-Adapter.bat --look` | Live readout of what the look-at resolver sees |
| `Measure-Haiku.bat` | Measures Claude Haiku 5.5's speed from your PC (about 20 tiny calls, under a cent) |
| `Run-Tests.bat` | Offline tests of the shared core |

## Rules

Offline single-player only; never touches anti-cheat. Extracted game data stays in `.local/`,
never in the repository. No credentials in the repository. See [AGENTS.md](AGENTS.md).

## Licence

MIT. See [LICENSE](LICENSE).
