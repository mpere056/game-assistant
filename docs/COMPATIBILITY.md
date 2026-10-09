# Compatibility and dependencies

| Item | Version tested | Link | Notes |
|------|----------------|------|-------|
| Windows | 10 Enterprise 22H2 (10.0.19045) | | RTX 2060 (6 GB), 16 GB RAM |
| Python | 3.12.10 | [python.org](https://www.python.org/downloads/) | `Setup.bat` makes a project-local `.venv` |
| pywin32 | 312 | [pypi](https://pypi.org/project/pywin32/) | Windows voices (SAPI) for spoken answers |
| sounddevice | 0.5.6 | [pypi](https://pypi.org/project/sounddevice/) | microphone for push-to-talk |
| faster-whisper | 1.2.1 (CTranslate2 4.8.2) | [pypi](https://pypi.org/project/faster-whisper/) | local speech recognition; model `base.en` (Systran/faster-whisper-base.en, about 145 MB, from Hugging Face on first start) |
| Windows voices | Microsoft Zira / David Desktop | built into Windows | default Zira |
| Pillow | 12.3.0 | [pypi](https://pypi.org/project/pillow/) | only for the screenshot checks in `tests/` |
| Anthropic Python SDK (`anthropic`) | 1.12.1 | [pypi.org/project/anthropic](https://pypi.org/project/anthropic/) | pinned in `requirements.txt` |
| Claude Haiku 5.5 | `claude-haiku-5-5` | [pricing](https://platform.claude.com/docs/en/about-claude/pricing) | $0.10 input / $0.50 output per million tokens (prompts up to 100K) |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | same | $2 / $10 per million tokens; phase 4 |

You need your own Anthropic API key in the `ANTHROPIC_API_KEY` environment variable.

## Data

| Item | Source | Notes |
|------|--------|-------|
| Elden Ring name lists | [Paramdex](https://github.com/soulsmods/Paramdex) `ER/Names` (latest when downloaded) | `Get-GameData.bat`; kept in `.local/`, never committed (no licence) |
| Web search | Anthropic web search tool `web_search_20250305` | Haiku 5.5 accepts this version; `web_search_20260209` returned errors with Haiku. $0.01 per search |

## Per game

| Game | Version | Bridge |
|------|---------|--------|
| Elden Ring | App Ver. 1.17.1 (`eldenring.exe` 2.7.1.0) | [Attack on Elden Ring](https://github.com/mpere056/attack-on-elden-ring), a build with the assistant ray block; see [games/eldenring.md](games/eldenring.md) |
