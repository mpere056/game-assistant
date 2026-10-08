# Plan

Decided 2026-10-08. Focus: Elden Ring first; Minecraft later. Other games (Valheim, Palworld,
No Man's Sky, Persona 4, Hitman 2, Age of Empires IV, Bloons TD 6) are possibilities only.

## Principles

- Give each decision to the cheapest, fastest layer that can make it correctly: code first, then
  Claude Haiku 5.5, then Claude Sonnet 5.5 only for writing new skills.
- One core for every game; one adapter per game ([ADAPTER.md](ADAPTER.md)).
- Moving never waits on a model: steering, pathfinding and safety checks run as code every frame.
  Models decide *what* to do; code does it.
- Exact facts come from the game's own data, never from a model's memory. When a lookup finds
  nothing, the assistant says so.
- Every layer's fallback is to stop and tell you.

## Layers

| Layer | Runs on | Decides | Cost |
|-------|---------|---------|------|
| Controller and monitors (steering, A*, stuck, arrived, danger) | your PC | button presses each frame; when to stop or report | $0 |
| Instant command rules ("stop", "pause", "again", a saved skill's exact name) | your PC | acts at once, no network | $0 |
| Claude Haiku 5.5 (`claude-haiku-5-5`), streaming, tool use, low effort | hosted | answers, picks tools, starts skills, reads a screenshot when state can't answer | $0.10 in / $0.50 out per million tokens |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | hosted | writes and fixes new skills | $2 in / $10 out per million tokens |
| Laya or another local model | your PC | only if logs show code can't make a fast judgment | $0 |

One Haiku call with tools replaces a separate router and answering model: the tool it picks is
the routing decision. DeepSeek stays a swappable alternative to compare on the same questions.

## Spending cap

$3 per hour, rolling 60-minute window (`game_assistant/core/spend.py`, ledger in
`runtime/assistant-spend.jsonl`). Warns once at $2.40; at $3 hosted calls stop until the window
drops under the cap, and local features keep working. Expected use (about 50 questions plus an hour
of tasks) is well under $0.25, so reaching the cap means something is looping. Every call also has
a maximum output length.

## Voice

Text first. Voice comes right after phase 2, as a wrapper around the same agent:

- Push-to-talk key; speech to text on the CPU with an open Whisper model (faster-whisper);
  Windows speech recognition as the fallback.
- Text to speech on the CPU with a small open voice (Piper or Kokoro); Windows voices as the fallback.
  Each sentence is spoken as soon as Haiku has streamed it.
- Spoken answers are one or two sentences; details stay in the text window.
- Push-to-talk or "stop" interrupts speech at once.
- Hosted real-time voice only if a cheap option appears; it would count toward the cap.
- Check that the voice models don't cost Elden Ring frames.

## Phases

Do not start a phase until the previous gate passes.

| # | Phase | Gate | Status |
|---|-------|------|--------|
| 1 | Adapter interface, Elden Ring adapter, adapter tests, spending cap; measure Haiku 5.5 speed | `Check-Adapter.bat` all PASS; `Measure-Haiku.bat` numbers recorded | **Done 2026-10-08.** Adapter tests 9/9 PASS. Haiku 5.5: first word 524 ms median, tool call 551 ms, adaptive thinking at low effort (chosen); 20 calls $0.0011 |
| 2 | "What am I looking at?": look-at resolver (built in phase 1), lock-on target, NpcParam names and resistances extracted from game data to `.local/eldenring/`, Haiku agent with fact tools, text chat window | Ask "what is that and what is it weak to?" near a known enemy: correct name, resistances match the game's data; time to first word measured | not started |
| 2b | Voice (see above) | Spoken question and answer; frame rate unchanged | not started |
| 3 | Walking: raycast walkability grid, A*, `goto()`, monitors, instant "stop", grace locations and fast travel | The player walks between two points around a wall; "stop" halts in under 100 ms | not started |
| 4 | Skills: sandbox, skill library, skill writer (Sonnet 5.5), dry runs | One sentence makes a working skill, such as sorting items | not started |
| 5 | Minecraft: new bridge (client mod) and adapter only | Questions and `goto()` work with no change to the core | later |

## What to measure

| Metric | Target (starting guess) |
|--------|-------------------------|
| Time to first word of an answer | about 1 s |
| "stop" to the character stopping | under 100 ms |
| Look-at accuracy on fixed test spots | 9 out of 10 |
| Cost per active hour | under $0.25 |
| Adapter tests | all pass before using a game |

## Open questions

- Which voice should it speak with?
- Can Haiku 5.5 write simple skills well enough to skip Sonnet 5.5 for them?
- Where Elden Ring keeps the lock-on target and the inventory in memory (start from fromsoftware-rs
  and Cheat Engine tables).
- Which Minecraft version and mod loader to pin, when we get there.
