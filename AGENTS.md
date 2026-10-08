# AGENTS.md

## Project
Game Assistant: an AI assistant the user talks to while playing, which answers questions from the
game's own data and later acts in the game. One shared core, one adapter per game. Elden Ring
first (through the Attack on Elden Ring bridge), Minecraft planned next.

## Hard rules, never break these

1. **Never write game assets, decompiled code, or extracted game data into this repository.**
   Extracted data (names, parameters) lives in `.local/<game>/` (ignored).
2. **`.gitignore` is a whitelist.** It ignores everything and includes only source and docs.
3. **Do not run `git commit` unless the user asked.**
4. **Do not touch anything outside this project folder** unless the user explicitly names the path.
   Game installs are read-only. Each game's bridge lives in its own repository.
5. **Offline single-player only.** Never play online, never circumvent, patch or probe anti-cheat.
6. **Never put credentials in the repo.** The API key lives in the user's environment
   (`ANTHROPIC_API_KEY`), never in a file here.
7. **Every hosted model call goes through the spending guard** (`game_assistant/core/spend.py`,
   $3/hour).

## How to work

- **Plan before code.** The plan is `docs/PLAN.md`; the adapter interface is `docs/ADAPTER.md`.
  One phase at a time; each ends with its test gate.
- **The core never imports a game.** Game-specific code lives only in `game_assistant/games/<game>/`.
- **Facts from data, not from models.** A model only phrases facts that code looked up.
- **Measure, don't guess.** Latency, cost and accuracy numbers go in `MODLOG.md` with the file in
  `runtime/` they came from.
- **Tell the user how to test it**: the exact command and what they should see.
- **Explain in plain language.** The user is not the programmer.

## Honesty

- Untested means "not tested". Never imply verification that didn't happen.
- After two failed real attempts at the same problem, stop and update `docs/STATUS.md`.
- Record dead ends in `MODLOG.md` alongside successes.

## Keep these files updated

- `MODLOG.md`: an entry after every change.
- `docs/STATUS.md`: where things stand, for a fresh session to pick up.
- `README.md`: status and tools.

## Environment

- OS: Windows 10 Enterprise 19045, NVIDIA RTX 2060 (6 GB), 16 GB RAM
- Python 3.12.10, project-local `.venv` (`Setup.bat`), Anthropic SDK 1.12.1
- Models: Claude Haiku 5.5 (`claude-haiku-5-5`), Claude Sonnet 5.5 (`claude-sonnet-5-5`)
- Elden Ring bridge: https://github.com/mpere056/attack-on-elden-ring (see `docs/games/eldenring.md`)
- Agent: Claude Code
