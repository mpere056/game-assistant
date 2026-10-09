# Mod log

Every change and test, newest at the bottom. Numbers come with the `runtime/` file they came from.

## 2026-10-08: phase 1 built and tested (in the Attack on Elden Ring repository)
- Plan decided with the user: one core, one adapter per game (Elden Ring first, Minecraft later);
  Claude Haiku 5.5 for talking, routing and vision; Sonnet 5.5 for writing skills; code for
  anything fast; $3/hour spending cap; text first, push-to-talk voice with local speech soon after.
- Built: interface, look-at resolver, spending guard, Elden Ring adapter, live adapter tests,
  Haiku latency test; 12 offline tests pass.
- The Attack on Elden Ring bridge gained a separate assistant ray block (1024 rays, ~1 ms/frame).
- Live adapter tests: 9/9 PASS (Attack on Elden Ring runtime/assistant-adapter-check-20261008-180548.txt).
- Haiku 5.5 latency, 5 runs each, streaming (Attack on Elden Ring
  runtime/assistant-haiku-latency-20261008-181630.txt): first word 641 ms thinking off, 524 ms
  adaptive/low; tool call 596 / 551 ms; look_at called 5/5; 20 calls $0.0011. Chosen: adaptive
  thinking at low effort.

## 2026-10-08: moved to its own repository
- The user wants the assistant in its own repository because it covers several games.
- New layout: `game_assistant/core/` (shared), `game_assistant/games/<game>/` (one adapter per
  game, listed in `game_assistant/games/__init__.py`), `game_assistant/tools/`, `tests/`, `docs/`
  with `docs/games/<game>.md`. Launchers renamed: `Check-Adapter.bat [game]`, `Measure-Haiku.bat`,
  `Run-Tests.bat`, `Setup.bat`. Extracted data goes to `.local/<game>/`; logs to `runtime/`.
- After the move: 12 offline tests pass; the Elden Ring adapter reads the running bridge (title
  screen). The live adapter tests have not been rerun since the move.

## 2026-10-08: phase 2 built: chat window, Haiku agent, enemy data from memory
- Enemy data: instead of unpacking game files (which needs community unpacking tools), the loaded
  NpcParam table is read from the game's memory through the bridge's read-only mailbox
  (`games/eldenring/memory.py`, `params.py`): found by its struct name "NPC_PARAM_ST", header and
  row-size checks against the fromsoftware-rs layout (0x2E0). Facts: base HP, runes, damage taken per
  type, status buildup, immunities (`npc_facts`). Offline tests on a synthetic table pass; first
  version looked back from the name in one big read, which would fail at unreadable memory, now
  steps back in 64 KB pieces. Not yet run against the real game.
- Names: ordinary enemies have none in the game's data; optional community list in
  `.local/eldenring/npc_names.txt` (Paramdex ER/Names/NpcParam.txt, no licence, so never committed).
  Not downloaded yet (waiting for the user's OK).
- Agent (`core/agent.py`): Claude Haiku 5.5, adaptive thinking at low effort, streaming, tools
  look_at / nearby_characters / character_info, every call through the spending guard.
- Live check against a pretend scene (`tests/agent_live_check.py`, real API): correct facts, says
  when it doesn't know a name, asks "which one?" when ambiguous. Tool round trips made "what is
  that?" take 1.5-1.9 s to the first word, so each question now carries what is at the crosshair
  (about 150 tokens): "what is that?" 1.0 s, follow-up 0.7 s, about $0.0002-0.0004 per answer.
- Chat window (`Chat.bat`, tkinter, stays on top): streams answers, shows time to first word,
  cost and the hour's total; logs to runtime/chat-<date>.jsonl.

## 2026-10-08: first game test of the chat: two failures, both fixed (retest pending)
- Test (runtime/chat-20261008.jsonl): "what is that?" while a c4550 (NpcParam 45502040) stood
  36.6 m away on a ridge, just above the crosshair -> "a wall at 16.4 m". Afterwards the resolver
  did find it (screen 0.08, 0.17; confidence 0.63): the single visibility ray to the body's middle
  clipped the ridge, so the enemy counted as hidden. Fix: two rays (middle and upper chest); the
  enemy is visible if either is clear.
- Dead end: loading the enemy table searched all of the game's memory with the bridge's scan command.
  It ran over 5 minutes inside the game, our 60 s wait gave up ("couldn't reach the game", 62 s to
  the first word), and the scan kept the bridge mailbox busy for everyone, AoTTG2 plugin included.
  Fix: the scan is gone; tables are found through the game's own SoloParamRepository (static at
  RVA 0x3D85F58 in 1.17.1, as the bridge already uses it; NPC_PARAM_ST at holder 64, checked by
  struct name, other holders tried if not). If that fails, the assistant says so and still answers
  with positions and health.
- The running game's mailbox stayed blocked by that scan, so Elden Ring needs a restart before the
  next test.

## 2026-10-08: full Elden Ring knowledge: names, item locations, places, wiki search
- User: download whatever is needed; wants info on all enemies, attack patterns, places, and where
  items and gear are found.
- Downloaded 14 Paramdex ER/Names lists (about 1.4 MB) into `.local/eldenring/paramdex/` (never
  committed; Paramdex has no licence). `Get-GameData.bat` (also run by `Setup.bat`) fetches them.
- `games/eldenring/knowledge.py`: local search (under 0.2 s) for enemies (names, variants, named
  attacks), items (where picked up, dropped, sold) and places (graces, map locations, regions).
  Open-world pickups only carry a map tile; the Site of Grace table (`read_graces`, BONFIRE_WARP_PARAM_ST
  holder 13, 0xEC rows) turns tiles into "near the Site of Grace X". "LD" tags read as legacy dungeon.
  Named attacks are sparse in the data (Malenia: 9 named of 79 rows).
- Web search: Haiku 5.5 works with `web_search_20250305` (Malenia query: 1 search, 4 s, 14k input
  tokens); `web_search_20260209` returned invalid_tool_input errors and cost 18k tokens. Limited to
  the Fextralife and Fandom Elden Ring wikis, max 2 searches per question, $0.01 each counted by the
  spending guard.
- Agent: new tool search_game_data(kind, query) plus web search; prompt says wiki answers must say
  so. Live check (pretend scene, real API): Moonveil -> Gael Tunnel, Magma Wyrm (2.2 s, $0.001);
  Stormveil Cliffside -> Stormveil Castle; Waterfowl Dance -> wiki strategy, labelled (4.1 s, $0.013).
- Still not run against the real game (it was closed): the enemy and grace table reads.

## 2026-10-08: plan: the fairy companion
- User wants a Navi-like fairy floating around the character as the thing they talk to: no text
  over it; goes to things and leads the way within a distance where it stays visible; normally
  invisible to enemies but can become visible to draw aggro; can tackle enemies on command.
- Plan (docs/PLAN.md "The fairy companion"): two layers. Its look is a game-independent overlay
  (transparent always-on-top window projected through the game's camera, faded behind walls, hidden
  in menus) driven by a code-only companion controller with a leash (start 25 m). Its in-world body
  is per game: in Elden Ring a hidden, unkillable ally character spawned and pinned by the bridge,
  switchable between ignored and visible to enemies, striking through the bridge's damage path.
  Voice later panned to its screen position.
- Phases renumbered: 3 fairy look and movement, 4 navigation (the fairy leads the way, then the
  character walks itself), 5 fairy in the world (bridge work in Attack on Elden Ring), 6 skills,
  7 Minecraft. ADAPTER.md gains the Screen and Companion body parts; AGENTS.md rule 5 now says game
  effects are bridge requests made only when the player asks.

## 2026-10-09: plan improved; voice, the fairy and route planning built (not yet in game)
- Plan: runtime threads, instant commands, test strategy (offline tests, demo scene, live API checks,
  game gates), voice as built, phase 5 research steps, open questions. Code for later phases may be
  built ahead, but a phase is done only when its game gate passes.
- Interface: Snapshot.screen (window rectangle, focus) and Snapshot.menu; Elden Ring reads both from
  the state block (menus: loading only). Locks around rays and mailbox requests (two threads now).
- Fairy (phase 3): `core/companion.py` (follow beside the head, kept off the crosshair; show; go;
  lead; leash 25 m; spring motion; fades behind walls; hidden in menus); `ui/fairy_overlay.py`
  (layered click-through window, 40 pre-drawn glow-and-wing sprites, per-frame position and opacity,
  pulses while speaking). Wings were faint in the first render; made larger and brighter.
- Commands (`core/commands.py`): stop, come back, go there, show me that, quiet; polite wrappers
  ignored ("hey fairy, come back to me please"). Found when the agent got "come back to me".
- Agent: fairy persona, `fairy` tool (go/show/lead/come_back; crosshair, character ref, nearest
  enemy); the fairy flies to the thing at the crosshair for "what is that?" questions.
- Navigation (phase 4, part): `core/nav.py` grid of downward rays, A*, lazy wall rays; offline: a
  route around a 24 m wall in under 1,200 rays.
- Voice (2b): `voice/tts.py` (Windows SAPI, Zira, sentence streaming, stop), `voice/stt.py`
  (push-to-talk F9, sounddevice, faster-whisper base.en int8 on the CPU). Offline check: Windows read
  a question into a WAV; Whisper heard "Moonvale Catana ... week 2" (0.7-0.85 s); with the Elden Ring
  vocabulary as initial prompt it was exact (0.94 s). Fuzzy name search added for near misses
  (moonvale -> Moonveil, malina -> Melina, stormvale castle -> Stormveil Castle).
- App (`app.py`, `Assistant.bat`, `Chat.bat`): chat window, fairy loop at 60 Hz, overlay, speaker,
  push-to-talk, settings in `.local/settings.json`. Demo scene (`games/demo`) and visual check
  (`tests/fairy_demo_check.py`, crops only around the fairy): follow, show the wolf, come back all
  drawn correctly. Agent in the demo: "fly over to the wolf" -> go, "lead me to the wolf" -> lead,
  "come back to me" -> follow, about $0.0005 and 1.2 s each.
- 27 offline tests pass. Nothing new has run in Elden Ring yet.

## 2026-10-09: enemy and grace tables read in game
- First run in game: the param repository chain was right (bridge RVA 0x3D85F58), but the tables were
  not found: fromsoftware-rs's INDEX values are not the holder numbers (holder 64 holds
  GRASS_TYPE_PARAM_ST), and the name-offset sanity limit (2 MB) skipped big tables whose struct name
  sits several MB in. Limit raised to 256 MB; measured holders: NPC_PARAM_ST 6, BONFIRE_WARP_PARAM_ST 43.
- Now: 7,045 NpcParam rows in 0.18 s, 422 graces in 0.41 s. Spot checks: Malenia resists magic,
  lightning, holy (60 %), bleed 154, immune to madness and death blight; Exile Soldier weak to
  lightning; Rivers of Blood -> "near the Site of Grace Church of Repose (Flame Peak)".
- Some enemies take extra damage from every type (Giant Dog 110-140 %): "weak to" is now a type at
  least 15 points above that enemy's median, plus a takes_extra_from_everything flag.

## 2026-10-09: first game test of the full assistant: "working really well"
- User: answers and the fairy work well; the fairy "looks really good", and they like how it moves
  and stays beside them. One issue: "go to the enemy" refused a Giant Dog 39 m away because of the
  25 m leash. They want at least 75 m.
- Leash raised to 75 m (default, the user's .local/settings.json, docs). The fairy grows small far
  away (10 px minimum) and the look-at and entity lists reach 80 m, so 75 m stays usable.

## 2026-10-09: look-at forgives loose aiming; voice confirmed in game
- User: the whole previous game test was done by voice (push-to-talk), "working great". But "what
  is that?" sometimes missed an enemy unless it was right at the centre of the screen.
- Look-at: an entity now counts up to 20 degrees outside its body (was 6); within 4 degrees it is as
  sure as dead centre, then confidence falls to 0.35 at 20. Ranking: degrees off, plus 1 degree per
  ~17 m of distance, minus 3 degrees for enemies (so an enemy wins over a friendly character at a
  similar angle). Two candidates within 1.5 points of each other still make it ask which one.
- Live check: a Giant Dog 54 m away, 5.7 degrees off the crosshair -> found, confidence 0.85.
  30 offline tests pass (3 new).
