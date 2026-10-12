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

## 2026-10-09: Navi-style answers; guiding to far places
- User: answers too long and full of meaningless extras ("high on your screen, so it's distant
  rather than right in front of you"); the fairy flew to the enemy without being asked; "can you see
  them?" got their weaknesses. They want less text, in the style of Navi. And "lead me to the
  Forsaken Ruins" failed ("I only lead to things within my range... no position"): the fairy should
  head up to 100 m in the direction to go.
- Style: new instructions (spoken, one short sentence, answer only what was asked, round distances,
  never describe screen positions, mention only the thing asked about) and a Navi persona (bright,
  quick, "Look!"/"Hey!" now and then); five example exchanges fixed the remaining extras. The main
  thing's screen position is no longer in the facts (kept only to tell several candidates apart).
  Max answer 300 tokens. The fairy moves only when asked: the automatic "fly to what I describe" is gone.
- Places: the bridge's open-world positions are global map coordinates (tile x 256 + position),
  the same as the game's place tables: nearest grace from Caelid came out as Caelem Ruins at 88 m.
  `games/eldenring/places.py` gives every Site of Grace and map landmark a world position (legacy
  dungeons through WORLD_MAP_LEGACY_CONV_PARAM_ST; first try mapped Stormveil to area 34 and called it
  "the other world": only conversion rows into areas 60/61 are used now). `adapter.locate(name)`
  also accepts items (Moonveil -> Gael Tunnel). Forsaken Ruins 151 m west, Stormveil 1.9 km west.
- Fairy guide mode: flies ahead toward the place, up to 100 m in front of the player (not held by
  the 75 m leash), hovering 4 m above the ground under it (a downward ray every 20 frames), at least
  18 px on screen; within 15 m it shows the spot and the app says "Here we are: <place>!".
  Agent tool guide_to(place). Map data now loads in the background at start-up (1.9 s), so a first
  "lead me to" doesn't wait for it (it took 3.5 s to the first word before).
- Live with real Haiku calls in the user's game: "Can you see something in front of me?" -> "Yes! A
  Large Putrid Corpse, about 46 metres ahead." (9 words, 0.7 s); "What about those things over
  there?" -> "Two Rotten Putrid Corpses, about 42 and 50 metres to the right."; "What is it weak to?"
  -> "Fire, mainly! It takes extra damage from everything, too."; "Lead me to the Forsaken Ruins." ->
  "This way! About 150 metres west, slightly downhill." (fairy guiding). 31 offline tests pass.

## 2026-10-09: the fairy's own voice (Kokoro, pitched up, sparkle, panned)
- User: the Windows voice sounds like bland generic TTS; wants it more like Navi.
- `voice/fairy_voice.py`: Kokoro neural voice (kokoro-onnx 0.6.1, int8 model 92 MB + voices 28 MB
  from the kokoro-onnx v1.0 release, in .local/voice/, `Get-VoiceModel.bat`), on the CPU. Each
  sentence is made at speed/pitch and played back pitch times faster: pitch up 1.25x (about 4
  semitones) at normal speaking speed. A two-note sparkle (G6, C7) before each answer. Played through
  sounddevice, so it is panned (gently, at most 60 %) to the fairy's screen position. Same interface as
  the Windows speaker, which stays the fallback ('voice_engine': 'windows' or the model missing).
- Measured: 54 voices (15 English female); a 4.5 s line takes about 2 s to make; streamed answer:
  sparkle and first words 0.6 s after the text started, stop cuts off at once.
- Samples for the user to choose from: runtime/voice_samples/ (af_bella, af_sky, af_heart, af_nicole
  with the fairy effect, and af_bella plain). Default af_bella; settings: fairy_voice, voice_pitch,
  voice_speed, voice_chime.

## 2026-10-09: fairy voice tuned against a Navi reference
- User liked the Kokoro samples ("way better") and sent a reference recording of Navi (1.6 s, three
  short lines, read from their Downloads folder, not copied into the project) and asked for samples
  with the actual effects.
- Measured: Navi's pitch is about 558 Hz (lines 0.1-0.3 s long); the first samples were only about
  250 Hz (pitch 1.25x on Kokoro voices that speak around 200 Hz). Brightness was already similar.
- New method 'world' (default): the WORLD vocoder (pyworld 0.3.5) sets the pitch to 550 Hz and makes the
  voice smaller (formants x1.3) separately, so it stays clear; 'speedup' (pitch and size together)
  kept. Kokoro only speaks at 0.5-2.0x speed: the 2.4x speed-up sample failed until speed was clamped.
- Samples (tests/voice_lab.py, runtime/voice_samples/v2): speed-up 1.6/2.0/2.4x (316/393/471 Hz);
  WORLD bella at 545 Hz with voice size 1.15/1.3/1.45, heart 533 Hz, sky 516 Hz, nicole (breathy, the
  pitch measure misreads it). Real voice code with WORLD: first sound 0.6 s, as before.
- Settings: voice_method, voice_pitch_hz (550), voice_size (1.3), voice_speed (1.12), fairy_voice.

## 2026-10-09: fairy voice: "blowing into the mic" noise removed; heart voice chosen
- User's picks: B4 (af_heart, size 1.3), then B3 (af_bella, size 1.45). Both sometimes sounded "like
  blowing into the mic": in B4 at the end of "giant", around "dog", after "ahead" and in "it's weak to
  fire", not in "hey listen".
- Cause, measured (tests/voice_noise_lab.py): the dio pitch tracker misjudged voiced sounds, and WORLD
  filled them with noise; 4.5 % of B4's energy was rumble below 300 Hz (1.1 % in B3), where a 550 Hz
  voice has nothing.
- Fix in `fairy_voice.world_shift`: harvest pitch tracker (rumble 4.5 % -> 0.8 %), breath noise in
  voiced frames x0.3, high-pass at 280 Hz (rumble -> 0 %), and a gate that keeps the result no louder
  than the original voice at each moment (gaps between words 2 dB quieter: -31.7 -> -33.8 dB).
  Processing 0.46 -> 1.22 s for a 4.5 s line; in the streamed voice the first sound still comes 0.6 s
  after the answer starts. Default and the user's settings: af_heart, size 1.3, 550 Hz.
- Before/after samples: runtime/voice_samples/v3 (v4_what_the_game_plays = exactly the game's voice).

## 2026-10-09: fairy voice a little lower (550 -> 500 Hz)
- User: the cleaned-up samples (v2-v4 for both voices) are "pretty good" but a little too high.
- Samples with the game's own processing at 500, 470 and 440 Hz for af_heart 1.3 and af_bella 1.45
  (runtime/voice_samples/v4; measured 500/462/429 and 490/462/429 Hz). Default and the user's setting
  now 500 Hz, waiting for their pick.

## 2026-10-09: fairy voice set to 440 Hz
- User: bella_1.45_440Hz and heart_1.3_440Hz are both "pretty good", "way better" than before. Default
  and the user's setting: af_heart, size 1.3, 440 Hz (bella 1.45 at 440 Hz is the alternative).

## 2026-10-09: Navi-trained voices looked into; heart 440 Hz built in
- User asked whether a TTS trained on Navi's voice exists. Found one fan-made RVC v2 voice converter
  ("Navi [Legend of Zelda] (RVC V2) (650 Epochs)", voice-models.com), trained on about two seconds
  of ripped lines and described by its uploader as unfinished; it would need PyTorch and two more
  models (0.5-1 GB), a pickle file to load safely, and is a grey area (Nintendo audio, the voice
  actor's performance). Not downloaded. Offered instead: real Navi exclamations from the user's own
  recording before the answer. The user chose to keep heart_1.3_440Hz for now.
- Checked the whole app starts with FairyVoice af_heart, 'world', 440 Hz, size 1.3 (the default and
  the user's settings); a test line started playing 0.94 s after being sent (a whole sentence at
  once; streamed answers start sooner), no errors. 31 offline tests pass.

## 2026-10-09: faster voice (streamed pieces), the fairy flies instead of jumping, a trail
- User (game test): answers much better, but the voice takes longer than the Windows voice, and after a
  quick first sentence ("Hey!", "Slash and fire!") there were a few seconds of silence. When sent
  somewhere the fairy "almost teleports", so they lose track of it; wants it faster than them but
  followable, with a short trail.
- Voice timing measured per 4 s sentence: Kokoro 1.35 s, harvest 0.61 s, rest 0.3 s (2.3 s): a short
  first sentence finished long before the next was ready. Kokoro fp32 model: no faster (1.24 vs 1.28 s;
  deleted again). dio with short gaps bridged: rumble 0.4 % (harvest 0 %, old dio 1.4-4.5 %) at a third
  of the time. Two Kokoro sessions of 4 threads each: two halves of a sentence in 0.99 s vs 1.3 s whole.
- FairyVoice rewritten: the sparkle plays the moment text arrives; sentences are cut into pieces (at
  commas and and/but/so/or/then when longer than 7 words; a long sentence's first clause goes as soon
  as its comma arrives); two workers (4 threads each, leaving the rest of the CPU to the game) make
  pieces in parallel; pieces play in order through one continuous output stream; short phrases cached.
  Streamed at Haiku's pace: sparkle 0.0 s, first words 0.46-0.90 s after the text starts, silence
  mid-answer 0.42-0.71 s in total (was several seconds).
- Fairy movement: the "teleport" was a bug: the warp rule snapped it to any target more than 60 m from
  the fairy (guide targets are 100 m ahead). Now it only snaps back if it is 180 m from the player.
  Travel at 10 m/s (running is about 6), at least 1.3x the player's speed (Torrent), acceleration capped
  at 14 m/s^2. New test: a 70 m trip starts gently, never exceeds 10.5 m/s.
- Trail: ten small wingless glows at the fairy's places in the last 0.32 s, shrinking and fading, shown
  only while it moves faster than 90 px/s (each its own layered window). Seen working in a capture.
- 32 offline tests pass; the app starts with the new voice (a test line: 0.26 s of silence in it).

## 2026-10-09: longer trail (2 s)
- User: voice and movement "much better"; wants more of a trail, about 2 s instead of a third of one.
- Trail: 30 dots sampled every 1/15 s over 2 s (was 10 over 0.32 s), each shown only if the fairy was
  moving faster than 90 px/s at that moment, smaller and fainter with age. Checked with the real overlay
  (no screenshot): 17 dots while flying 600 px in 1.5 s; after it stopped, 11 dots at 1.0 s, 2 at
  1.7 s, none at 2.5 s, so the trail fades instead of vanishing. 32 offline tests pass.

## 2026-10-09: trail fixed when turning; guiding stays in sight; off-screen marker
- User: the longer trail is "kinda messed up" when turning around; still sometimes hard to follow the
  fairy while guiding (maybe beyond 70 m, behind terrain or the other side of a hill).
- Trail: it was kept in screen positions, so turning the camera smeared it across the screen. Now the
  companion keeps world positions (30 samples over 2 s, only where it moved faster than 2.5 m/s) and
  projects them through the current camera every frame; the overlay only draws the dots it is given.
- Guiding: instead of always 100 m ahead, it takes the furthest spot toward the goal (up to 100 m, in
  10 m steps, at least 15 m) that the camera can see: one batch of downward rays for the ground and one
  of sight rays, every 15 frames, smoothed. Hovers 6 m above the ground (was 4), at least 24 px on
  screen (was 18), and fades only to 50 % behind terrain while travelling (25 % while following).
- Off-screen marker: when it is travelling and outside the picture (or behind the camera), a pulsing
  glow sits just inside the screen's edge in its direction (camera-space direction behind the camera,
  where the projection doesn't apply; the first version returned infinity there).
- Tests: 3 new (trail drawn where it is in the world after the camera turns; a 30 m ridge at 40 m keeps
  the guiding fairy between 14 and 40 m; marker when behind). 35 pass. Demo scene, counting the overlay's
  windows: following 1 fairy / 0 dots; flying off to the side 11 dots + marker; behind the camera the
  marker only; back again fairy + 12 dots.

## 2026-10-09: guiding follows walkable ground (no more leading over cliffs)
- User: easier to see now, but the fairy crossed a cliff edge (screenshot at a cliff in Caelid); it
  should guide with the terrain in mind.
- Measured: the assistant ray block answers about 12,000 rays/s (2,601 rays in 0.22 s).
- nav.py: two scales (`Profile`): LOCAL (2 m cells, 30 m, as before) and WIDE (5 m cells, 150 m around
  the player, 3,721 downward rays). Steps may rise up to 3.5 m or drop up to 4.5 m per 5 m cell (x1.4
  diagonally); bigger changes are cliffs. The goal end is the reachable cell nearest the goal (flood
  fill), so a goal across a cliff still gets the closest walkable approach. Rays go out in chunks of
  256 so a background plan never holds the ray block long.
- Guide mode: plans a WIDE route on a background thread, flies along it (as far as the player can see,
  up to 100 m, heights from the route's own floor), re-plans near the end of a partial route (50 m), when
  the player is 25 m off it, or every 12 s; straight-line fallback until the first plan is ready.
- Test (new): plateau with a 30 m cliff and a ramp to one side: the route stays on the ramp beside the
  cliff and the fairy heads for it. 36 tests pass.
- Live: from the user's spot toward the Forsaken Ruins (216 m away): planned in 0.55 s with 4,843 rays,
  47 waypoints, 281 m of winding route, biggest rise 2.3 m and drop 1.4 m between waypoints; it ends
  short (the ruins are past the 150 m area), to be re-planned on the way.

## 2026-10-09: "nearest grace" fixed; guiding when walled in
- User: guiding gave "really weird directions": on a cliff above water, the fairy wanted them to go
  forward (screenshot: fairy high up by a tower).
- Cause (chat log): "take me to the nearest Site of Grace" had no way to sort by distance, so the
  agent searched by name and guided to the First Mt. Gelmir Campsite, 4 km north-west. The nearest
  grace was Smoldering Church, 36 m south-east. While planning (and if planning fails) the fairy also
  flew a straight line, taking "ground" heights from the tower top.
- Fix: `places.nearest()`, `adapter.nearest_places(kind)`, agent tool nearest_places, and guide_to
  understands "nearest site of grace / landmark". Live: "Take me to the nearest Site of Grace." ->
  "This way! The Smoldering Church is about 36 metres south-east, just behind you." (route complete).
- Water: a downward ray over the water ahead hit a flat surface 78 m below, with no attribute bits to
  tell water from ground (attr 0); the cliff (an 88 m drop 5 m ahead) already keeps routes away.
- Far goals (over 250 m): plan with WIDE (5 m, 150 m) and a new FAR profile (8 m cells, 300 m, 5,625
  rays, 0.8 s live) and keep the route that gets closer. From the user's spot only 790 of 3,535 WIDE
  cells were reachable without a big drop (a walled-in cliff top), so no route got closer to Mt. Gelmir:
  now the fairy says "I can't find a way to walk toward <place> from here" once, instead of pointing
  over the edge. While no route exists it waits 15 m ahead toward the goal (straight line only when
  it cannot look at the ground at all, which an existing test caught). 36 tests pass.
- Known limit: 5-8 m cells can read a steep but walkable slope as a cliff.

## 2026-10-10: arrival checks height
- User: guided to the nearest grace (Rear Gael Tunnel Entrance), the fairy said "here we are" while
  they stood on the ground above it; the grace was lower down, in the tunnel (about 67 m below).
- Arrival now needs within 15 m across AND within 6 m in height. Right above or below the place, the
  fairy flies down (or up) to the real spot for 6 s and says "It's right below us, about 67 metres
  down! There must be a way down nearby." Nearest-place ordering uses 3D distance, and places more
  than 15 m higher or lower carry a height_note the agent mentions ("down in the tunnel").
- New test (above a place is not arriving); 37 pass. Live after the user had walked down: Rear Gael
  Tunnel Entrance 11 m south, 1 m lower; Gael Tunnel 129 m, "about 59 m above you".
- Known limit: routes are planned on the top surface (downward rays), so the fairy can't lead into a
  cave or tunnel; it says where the place is and leaves the way down to the player.

## 2026-10-10: plan: a second talk key for commands (auto-walk)
- User: F9 stays "talk to Navi"; another key gives orders to the character ("go to the nearest site
  of grace" and the character walks there on its own) while Navi still leads the way; Navi asks for
  clarification when two places are about equally close or the order is vague.
- Plan (docs/PLAN.md "Two talk keys"): F10 (`command_key`; F8 is the AoER bridge's switch key).
  Command -> local rules or Haiku with a command prompt -> one action; ambiguity -> one spoken question
  from Navi, answered on either key (10 s timeout); the same walkable route Navi guides along; an
  auto-walk controller holds W/A/S/D toward the next waypoint relative to the camera; the planned
  monitors (arrived with height, stuck, enemy near, fall, menu); instant takeover on any physical
  movement key (injected input is told apart) or "stop". No jumps, ladders, lifts, swimming or Torrent
  at first. New phase 4b; phase 4 is now the navigation and guiding part (built).

## 2026-10-10: offline wiki, screen look, where am I / where is / items near me
- User (screenshot of the chat): "Where's the Stormveil?" -> "the game data doesn't give its position";
  "a weapon close by?" -> "I can't scan for loot nearby"; "how do I get up this cliff?" and "what's
  that gust?" (a Spiritspring) unanswered. They want more data downloaded and indexed so Navi doesn't
  search online, and questions like "what is there to do here?"; Spiritspring was an example of
  "know what I'm looking at and answer".
- Decisions: command key F10; auto-walk runs on long stretches; near enemies it keeps going, Navi warns.
- Offline wiki (`games/eldenring/wiki.py`): the Elden Ring Fandom wiki's database dump (4 MB 7z, Feb
  2026, CC BY-SA), unpacked with py7zr, parsed into 4,482 articles (Nightreign, unused content and
  dialogue pages left out) with 2,667 other names, sections as plain text, infobox fields; SQLite FTS5
  index (21 MB) in .local/eldenring/wiki/; lookups under 10 ms. Aspects map questions to sections
  (fight -> Moveset/Phase/Strategy, here -> Sites of Grace/Bosses/NPCs/Notable Loot...). Table cell
  attributes leaked into text; the fix first went in with a literal backspace (shell escaping of ),
  found by checking the file's bytes.
- New tools: wiki, where_am_i (nearest graces/landmarks + the wiki page for here), where_is (locate
  without guiding), items_near_me (open-world pickups by map square; named places within 400 m),
  look_at_screen (a JPEG of the game window, 1024 px, to Haiku; tool results may now be content blocks).
- Live with real calls: "Where's Stormveil?" -> 1.5 km west, 208 m up; "a weapon close by?" ->
  Moonveil, 145 m east in Gael Tunnel; "what is there to do around here?" -> Magma Wyrm, Moonveil,
  Alexander, Somber Smithing Stones, Cross-Naginata; "how do I beat Margit?" -> moveset advice from
  the wiki; no web searches; $0.0014-0.0024 each. 37 tests pass.
- "What am I looking at": look_at_screen now sends the whole view plus the middle third zoomed, with
  hints for easy-to-miss things (Spiritsprings, graces, lifts, fog walls, items, messages...), and the
  prompt says to look before ever answering "just a wall". It only takes the picture when the game has
  the keyboard focus: a live check with this chat over the game sent the chat window (Navi said so),
  so a covered game now gets "click back into the game" instead. The Spiritspring itself is untested
  until the game is in front.
- The picture is now taken when the talk key goes down (on its own thread, so the microphone starts
  at once), and look_at_screen uses it if the question comes within 30 s, once; otherwise it grabs
  then. It shows what you were looking at when you started asking, and the grab is done by the time
  Haiku asks. 39 tests pass.

## 2026-10-10: Navi's speech bubble in the game
- User: "make the text from navi appear in game as well... a cute chat bubble coming from navi with
  the text streamed".
- ui/bubble.py draws it with PIL at twice the size (smooth curves and text): white, light blue rim
  (the fairy's colour), soft shadow, a tail toward the fairy; text wrapped to 30 % of the game width,
  2.4 % of its height tall; long answers keep their last 6 lines. About 8 ms per drawing (37 ms before
  the shadow blur and rim moved off the 2x picture).
- One more layered click-through window in the overlay: "..." dots while Navi thinks, then the answer
  typed in word by word as it streams (45 characters/s, faster if it falls behind), redrawn at most
  every 40 ms; it follows the fairy smoothly, points at the edge marker when the fairy is off-screen,
  flips left/below to stay inside the game window, stays 3 s + 0.05 s per character (max 12 s), fades
  in 0.6 s. Arrivals and instant replies show too. Hidden whenever the game isn't in front.
- Settings: speech_bubble (on), bubble_scale. Checked with a fake fairy and game window (window
  state, not screenshots): thinking 80x69, the answer 399x91 at a 720p window, gone after ~8 s,
  flipped inside the corner. 43 tests pass.

## 2026-10-10: the Spiritspring stopped being recognised
- User (screenshot, OBS running): "What is that in front of me? How do I use it?" -> an empty answer,
  then "just a wall... a glowing blue light" twice with no new look. Asked whether OBS was the cause:
  no.
- Causes, from runtime/chat log and live checks:
  1. MAX_TOKENS 300 includes adaptive thinking; thinking about the picture used it all -> empty
     answer (stop_reason max_tokens). Now 1024, and an empty max_tokens answer says so.
  2. The screen grab had Navi (the "glowing blue light") and the previous bubble over the gust.
     Now ui/capture.py takes the game window's own pixels with PrintWindow(PW_CLIENTONLY |
     PW_RENDERFULLCONTENT): no overlays, works even when the game is covered, ~30 ms. The first
     match by client rect was the Discord overlay (a transparent window exactly over the game, so
     the picture came back black and it fell back to the screen grab); layered, click-through and
     tool windows are now skipped. The screen grab remains only as a fallback while focused.
  3. Follow-up questions answered from the old picture in the history. Earlier pictures are now
     replaced by "(picture removed)" (also saves ~1,200 tokens per request); earlier thinking blocks
     are dropped with them, since they're signed against the conversation before them (400 error
     otherwise).
  4. On the clean picture Haiku said "waterfall": the hints now describe how a Spiritspring differs.
  5. Then it hedged "the wiki didn't confirm it": wiki pages lost every {{template}}, including item
     descriptions ({{Description|EN_line1=...}}), {{PAGENAME}}, {{ER}}, quotes and the drop, shop,
     enemy and quest item tables. These are now expanded into text (about 3,000 descriptions came
     back). Names match without accents ("Kale" -> Merchant Kalé, before: Mule). The index is built
     beside the old one and swapped in at the next start, because the running assistant holds it.
- Live: "What is that in front of me?" -> "a Spiritspring... jump into it on Torrent, it carries you
  up the cliff". 43 tests pass.

## 2026-10-10: "I can't see your inventory from here"
- User: asked about the Spectral Steed Whistle with the inventory open; Navi said it couldn't see the
  inventory. Log: it called search_game_data, not look_at_screen (the prompt only named scenery);
  menus aren't detected from memory, and speech recognition heard "back proceed"/"Spectral Seed".
- Now a word check (inventory, menu, map, equipment, key items, tab, "on my screen", "I have my X
  open", "what does this say"...) attaches the picture to the question itself: no extra round trip
  (1.4 s to the first word live). The prompt says menus can be read from the picture and never to
  say it can't see the screen without looking. Pictures attached to questions are removed from the
  history like tool pictures. Vocabulary: Spectral Steed Whistle, Spiritspring, inventory, Key Items,
  Stonesword Key, Kalé.
- Live (inventory closed at the time): "I have my inventory open. What can you see?" -> "I don't see
  an inventory open... the game world"; "what is on my screen?" -> "A Spiritspring, just to your
  right". 43 tests pass.

## 2026-10-10: navigation from the game's own navmesh (research and first build)
- User: guiding "is way better but not always working"; it must be reliable before commands make the
  character walk. Screenshot: "lead me to the Crucible Knight" (Stormhill Evergaol, 106 m NW, 47 m up)
  -> "I can't find a way".
- Diagnosis (runtime grid pictures): the evergaol is on a plateau ringed by cliffs; the way up is
  outside the raycast planner's 150 m grid, so its best was the cliff foot (62 m short). One run also
  failed because 12 wall-check rounds ran out among tree trunks (now 40). Coarse wide grids (12 m
  cells over 600 m) find "routes" but a 40 m cliff looks like three 10 m steps there; beyond ~300 m
  most rays find no floor. Raycasting can't be made reliable for auto-walk.
- User chose: the game's navmesh. First the archives were not decrypted (the action was blocked and
  the user asked); the user then allowed decrypting the archives, any modding tool, and reading the
  navmesh from memory.
- Archive layer (games/eldenring/archive.py), format from Smithbox (MIT): Data0-3.bhd RSA public-key
  decrypt (256 -> 255-byte blocks, pure Python pow; ~35 s once, cached decrypted in .local), BHD5 index,
  64-bit name hash (x0x85), AES-128-ECB ranges (`cryptography` 50.0.2 added), DCX/KRAK via the game's
  oo2core_6_win64.dll (data at 0x4C), BND4. Keys and file-name list are downloaded into .local, not
  committed. 86,585 of 130,875 dictionary names found; 695 navmesh containers (304 more are DLC, not
  installed). Mistakes on the way: the key text was sliced at an earlier mention of its name; the DCX
  data offset formula was wrong (Oodle returned 0); a heredoc put a literal NUL into the source.
- Navmesh files: /map/mAA/<block>/<block>.nvmhktbnd.dcx, a BND4 of Havok 2018 tagfiles: n* pieces
  (detailed hkaiNavMesh + query tree + user edges), o* pieces (a coarser copy, probably for big
  characters), 9xxxxx pieces (empty). Open-world tiles are one piece of 10-12k faces; tile-local
  coordinates run -128..128 around the tile centre (world = tile * 256 + local). Check: the player
  stood 2.08 m from the centre of the face under them (m60_42_37_00).
- Havok reader: Soulstruct (Grimrukh, GPL-3.0, Python 3.13) in its own venv under .local/tools,
  installed from GitHub (PyPI 1.5.0 lacks hkcdStaticAabbTree; and hk2018/__init__ never imports
  _hkcd, so the converter registers those types itself). uv's 3.13 link failed; the venv is made from
  the downloaded interpreter directly. tools/navmesh_convert.py (runs there) writes one .npz per block:
  vertices, faces (+faceData), edges (opposite face, flags, edgeData). ~5-10 s per open-world tile.
- tools/get_navmesh.py does it all (keys, tool venv, extract, convert in 3 workers). A first run was
  stopped to add faceData; then two runs shared one temp folder and each deleted it under the other,
  leaving 561 empty results: temp folders are now per run, the converter refuses a missing folder,
  empties were deleted and re-converted.
- core/navmesh.py (game-independent): locate the face under a point (or the nearest edge within 6 m),
  A* over faces (climbing costs extra), simple stupid funnel, corners pushed 0.7 m off the wall along
  the turn's bisector. Tests: L corridor (one corner, 0.7 m off it), open field (straight), a gap
  (no route), two levels (the right one is chosen), off-mesh start.
- games/eldenring/navmesh.py: blocks to world coordinates (tiles by their index, dungeons by the
  game's conversion rows), joined where boundary edges of different blocks lie on each other (0.35 m
  sideways, 1 m height, 0.3 m overlap). adapter.route() (CAP_NAVMESH when navmeshes are extracted)
  plans in world coordinates with 300 m, then 900 m margins; the companion's guide mode uses it first,
  raycasting as fallback; the status line shows which planner made the route.
- First live try (only tile 42_37 converted then): start face 1.6 m from the player, evergaol face
  0.2 m from its marker, no connection inside the one tile (the plateau), as expected.
- Full extraction: 695 blocks, 4,525,403 faces, 232 MB, 874 s for the last 561 (3 workers). 150
  open-world tiles are empty: their pieces are the 5 KB empty placeholder (sea at the map's edges).
- Face data on tile 42_37 (drawn): 0x20000 = the Stormhill Evergaol arena (a 60 m circle); 0x1000000 =
  small patches in ruins and a tower (unknown). Routes to a goal that can't be walked to now end at the
  nearest reachable point (reaches_goal False) instead of failing.
- adapter.route from the player near Stormhill: Stormhill Evergaol 546 m (78 m straight), 4.0 s
  (mesh of 9 tiles + 9 dungeons, 173,139 faces, 609 joins; A* 0.15 s); Gatefront Ruins 111 m and
  Church of Elleh 198 m, 0.03 s each (cached mesh); Stormveil Castle 712 m, stops 146 m short, 13 s
  (the 900 m retry). Duplicate waypoints (< 0.3 m apart) are dropped. 48 tests pass.
- User restarted the assistant and tried navmesh guiding in game: "things are working really well".
- Compared with Baritone (Minecraft): same core (A* + steering + re-planning); its speed comes from the
  ready-made voxel grid and time-limited, segmented planning. User liked "plan for about a second,
  start on the best partial route, plan further while moving" -> planned next, with joining the
  blocks once at extraction time and movement types from the navmesh's user edges.

## 2026-10-10: Baritone-style planning (whole-world graph, lazy loading, time budget, own process)
- User: Navi must answer instantly; waiting a little before navigation starts is fine.
- Measured the old per-route mesh: 9 tiles build in 0.6 s; 3.0 of 3.3 s was joining two dungeon
  blocks' 34-40k edges, which found nothing: dungeons sit 50-90 m from the open-world mesh.
- games/eldenring/navgraph.py builds once (Get-GameData) a whole-world graph in .local/eldenring/
  navgraph: per block ready-to-search arrays + links: 43,062 tile/block joins; 13,125 entrance links
  (dungeon threshold gaps, measured 5.8-6.4 m at a cave mouth; edges must face each other); 1.6 M
  drops (one way, 0.8-6 m down, 0.8/1.6/2.5 m beyond an edge) and steps (same level 0.8 m beyond an
  edge: a seam or thin cut). ~7-8 min.
- core/navmesh.search: any Source; A* with a time budget returning the best partial route (timed_out)
  or, when the faces run out, the nearest walkable point; special links reported. NavMesh is now one
  Source; WorldGraph loads blocks on demand (LRU 300).
- core/route_worker.py: searches run in their own process at below-normal priority (a search on a
  thread would compete with the fairy loop and answer streaming under Python's GIL); a dead worker is
  noticed within 0.25 s. adapter.route uses it with a 3 s budget; drops, steps and entrances on a
  route are checked with live rays (knee/chest) and banned + re-planned if blocked (up to 4 times).
  The companion sets off on a timed-out route and plans the rest from its end (up to 8 pieces).
- Measured from the earlier spot near Stormhill: Stormhill Evergaol 545 m, 0.98 s cold / 0.25 s warm;
  a 1 km trip: 3.1 s for 1,162 m ending 18 m short (timed out: continued while moving).
- Open: the player now stands on a cave-mouth ledge that the graph says is enclosed (896 faces: the
  ledge and the cave; no same-level ground within 3 m, no drop within 6 m). How the player got there
  is unknown (a bigger drop? a jump?). Idea: learn links from the player's own movement.
- 50 tests pass (new: time budget, one-way drop).
- User (3 screenshots): the "ledge" is a cave entrance: a walkable rock passage from the cave out to
  the forest at ground level. Measured: the cave's mesh ends ~12 m before the outside ground; the
  passage has only one 17-face piece, 2.4-2.8 m from the outside ground at the same level.
- Point-sampled steps (0.8 m, then 0.8/1.6/2.5 m) missed it; edge-to-edge matching (boundary edges
  facing each other, <= 3.5 m apart, <= 1 m in height) found 5 pairs (2.5-3.3 m). Steps are now
  edge-to-edge, both ways (4.58 M); drops stay sampled (517 k). Special links cost extra in the search
  (entrance +2, drop +4, step +8 m) so they're used only when needed; steps are ray-checked at knee and
  chest height.
- Graph files: 2.1 GB with coordinates per link -> 730 MB with vertex numbers and 32-bit floats.
  Build ~15 min. The dungeon map shifts are saved by the adapter (legacy_conversions.json, 196 rows) so
  the graph can be built without the game; Get-GameData.bat navmesh now builds it after extraction.
- From the cave entrance (adapter.route: worker + live checks): passage outside->inside connected;
  Stormhill Evergaol 604 m, 1.7 s; Gatefront Ruins 169 m and Church of Elleh 166 m, 0.02-0.12 s;
  Stormveil Castle reached (995 m, through an entrance link), 7.8 s; Agheel Lake North 265 m;
  Fort Haight West and Lake-Facing Cliffs ~1 km: best part after 3 s (~200 m short, continued while
  moving).

## 2026-10-10: ladders, lifts, jumps and doors from the game's own special edges
- User: add jumps, ladders and lifts.
- The navmesh files hold 670 Havok user edges (75 blocks; Stormveil 50): two boxes (ends), a type
  number, costs, direction. The converter now keeps them all (19 values; a first try said 20 and one
  block with exactly 20 edges reshaped "fine" into nonsense: re-converted). By type, over all of them:
  8 = ladders (410: ends 2.2 m apart, 3-61 m in height, cost = height, both ways), 0 = lifts (198:
  straight above each other, 22-150 m), 4 = jumps (31: ~2 m, one way), 1 = doors (11: 3.3 m, level),
  2 = unknown (21: 1-14 m across, +-13 m; left out). Ends sit ~1 m above the mesh under their box
  (median 0.4-1.4 m from the box foot; 46 of 1,276 ends found no face).
- Graph links: 736 ladder, 336 lift, 30 jump, 20 door (directions as the game marks them); extra
  search cost ladder +5, lift +20, jump +3, door +1 m; no ray checks (the game authored them).
- Routes report ladders, lifts, jumps and drops; the companion raises action:<what> once within 8 m and
  Navi says "Climb the ladder!", "Take the lift!", "Jump across here!", "Drop down here!".
  A test caught that the companion wouldn't plan at all without raycasting even with the navmesh
  planner: fixed.
- Checked offline (bottom to top of real ones): Stormveil ladder 14.6 m: 15 m route via the ladder
  (250 m walk without it); Stormveil lift 116 m: reached via the lift (unreachable without);
  m15 ladder 21 m and m13 lift 82 m: reached only with them. 51 tests pass.

## 2026-10-10: learned links; F10 orders and auto-walk (phase 4b)
- User: commit and merge, don't test yet, continue with the next things.
- Learned links: the adapter tells the route planner's process where the player is ~4 times a second;
  a move between faces the graph doesn't connect within 3 links becomes a link (kind 8, cost +1 m),
  saved in .local/eldenring/navgraph/learned.json. Teleports (>15 m between samples), long pauses,
  menus and loading are ignored. Live: 167 frames of observations, planner stayed responsive.
- core/autowalk.py: per frame, keys (the nearest of 8 directions relative to the camera) + a camera
  turn toward a point 3.5 m ahead on the route; run on straights >= 20 m with hysteresis and at least
  1.5 s (a short tap of Space is a backstep/roll in Elden Ring); arrived 3 m/3 m; stuck (<0.5 m in 2 s)
  -> new route, twice within 10 s -> give up; pauses in menus, loading, death, when not focused; enemy
  within 8 m of the player or the next 30 m of route -> warning, keeps walking; ladder: interact tap
  then forward/back to the other end's height; lift: wait for the height change (25 s max); jump: jump
  tap. 8 tests in a simulated world (L route within 3 m, run never shorter than 1.5 s, ...).
- ui/win_input.py: SendInput scan codes (dwExtraInfo marked), relative mouse for the camera with
  degrees-per-count learned from the camera's own turn, all keys released on stop/pause/exit; a
  WH_KEYBOARD_LL hook: physical (not injected) W/A/S/D/Space/Shift -> takeover.
- core/walk_commands.py: answer to Navi's question (10 s), stop, nearest grace/landmark (asks when the
  second is within 15 %), "that" (crosshair), a named place; else Haiku with a command prompt and
  action tools (Agent.command). Live: "head over to that church" -> crosshair, "back to the castle" ->
  Castle Stormveil, "somewhere I can rest" -> nearest grace, $0.0011 for 5. "never mind, stop" went to
  Haiku: stop now matches in short orders.
- App: one push-to-talk listener for F9 (questions) and F10 (orders); "/..." typed is an order; the walk
  runs in the fairy loop on Navi's route (Navi plans and leads); Navi speaks: start, "Careful, an enemy
  ahead!", stuck, give up, "Okay, you've got it!", "Click back into the game...". Settings: command_key,
  auto_walk, auto_walk_sprint, walk_keys (Elden Ring defaults W/A/S/D, Space run, E interact, F jump).
- Found while wiring: app.py on main didn't parse (the ladder announcement line had real line breaks in
  a string, from shell escaping); no test imported the app. Hotfix PR #8 with tests that compile every
  file and import the app; another heredoc then put backspace characters into a regex: a test now
  rejects control characters in source files. Scripts with backslashes are written as files now.
- 73 tests pass. Not tried in the game.

## 2026-10-10: first F10 test in game: wrong target, stuck at a cave mouth
- User (screenshot): F10 "Walk to the nearest Godrick soldier" (twice) -> "Let's go to Groveside Cave!";
  the character walked to the cave entrance, then "Hmm, something's in the way" again and again.
  Also "Here we are: Groveside Cave!" came while the walk went on.
- Cause 1: "the nearest X" only knew places; an unknown X fell back to the nearest landmark. Now
  "nearest <name>" finds the nearest living character with that name (knowledge names), "enemy" means
  any hostile; the walk follows the character (re-aims when it moved 6 m; "It's gone!" if it dies).
- Cause 2 (checked live: the route from there reaches the cave grace, 37 m, ends 1 m from it): the walk
  steered 3.5 m ahead on the route and cut corners into the rock (a 90-degree bend: 3.3 m off the route
  in the simulation); and "give up after two stucks within 10 s" never happened because re-planning
  takes seconds. Now: no steering past a sharp corner until within 1 m of it (same bend: < 1 m);
  first stuck: back off and sidestep 0.8 s; second: the made-up links (entrance, step, drop, learned)
  of the route within 5 m are avoided and the route re-planned; third within a minute: give up.
- During a walk Navi says "Here we are" when the walk arrives (3 m), not at Navi's 15 m.
- 77 tests pass (new: corridor bend, stuck sequence, nearest character by name / enemy / none).

## 2026-10-10: second F10 test: orders for the Godrick Soldier still went wrong
- User (2 screenshots): "Go to the Godrick Soldier." -> "The Warmaster's Shack or the Volcano Manor
  Request: Istvan?"; "Go to the nearest Godrick soldier in front of me." -> "Let's go to Groveside
  Cave!"; "I want you to walk my character to the nearest Godrick soldier, please." -> Haiku: "soldiers
  are enemies, not fixed spots... Warmaster's Shack ...?".
- The orders were at 22:24, the earlier fixes saved at 21:05; the Groveside reply is only possible
  with the code from before them, so the app most likely hadn't been restarted. The status line now
  says "NEW VERSION ON DISK: restart the assistant" when source files are newer than the app.
- Real gaps anyway: named orders searched places first, and place search falls back to items
  ("Godrick Soldier Ashes" -> Warmaster's Shack); filler ("in front of me", "please", "I mean") broke
  matching; "gondric" (speech recognition) didn't match "Godrick"; Haiku had no character action.
- core/walk_commands.py rewritten: "nearest/closest X" anywhere in the order; the target after
  go/walk/take ... to; characters within 150 m first with forgiving word matching (difflib >= 0.72 or a
  prefix), then places, never via an item; filler removed. Haiku's command prompt gets the characters
  around ("Godrick Soldier (enemy) 39 m; ...") and a walk_to_character action. An order without the game
  says "I can't see the game right now." instead of raising.
- Test: all six of the user's phrasings resolve to the soldier (entity 11), instantly, with a place
  search answering like the real one. 79 tests pass. Live check not possible: the game was closed.

## 2026-10-10: third F10 test: soldier orders work; cave still stuck; moving targets; one hit
- User (screenshot): "Go to the nearest Godrick soldier" now works ("There's a Godrick Soldier 30 metres
  away", "Here we are"); "Go to the Site of Grace that's in the nearest cave" still stuck at the rock
  beside the cave mouth. Moving characters: the walk went to where it was, not where it is. Wants it
  to start combat: land one hit. Also asked about Laya / the NVIDIA model for real-time combat.
- Cave, measured live from where the character stood: the route climbed a rock and used a made-up
  drop link; two of its straight legs ran through rock (rays hit 0.1 m and 0.4 m along). Now every route
  is checked before use: rays along each straight leg (first 80 m) at knee and chest height; a blocked
  leg -> the planner avoids faces within 0.8 m of the hit (WorldGraph.avoid) and made-up links within
  3 m, and plans again (up to 6 times; kept for the session). That made the route clear but short of
  the cave (the passage has no navmesh). Bridge: when the navmesh route ends within 40 m of the goal,
  the raycast planner (1 m cells, rays from 1.8 m above the feet so the overhang doesn't hide the
  floor; 6 m start: no floor found) finishes it. Result: 27 m into Groveside Cave, 1.2 m from the
  grace, no leg through a wall. (First try skipped the bridge after a timed-out search; fixed.)
- Moving targets: within 20 m the walk steers at the character's live position every frame; further,
  the route is re-planned when it has moved 3 m (was 6 m, and only re-aimed the route).
- Attack (code, no model in the loop): "attack the nearest Godrick soldier", "hit that enemy",
  "attack it" (the crosshair, else the nearest enemy), Haiku tool attack_character. Lock on (Q) within
  12 m, close to 1.8 m from its body, swing (left mouse button) when facing within 30 degrees, wait
  0.6 s, hand back ("Got a hit in! Your turn!"), still locked on. Target dies on the way: "It's gone!".
- Laya / NVIDIA models for combat: kept out of the real-time loop (any model is hundreds of ms per
  decision; a vision model would compete with the game on the 6 GB GPU). Combat timing belongs to code
  at 60 Hz on memory data (next: enemy animation state via the bridge); a model picks what to do.
- 83 tests pass (new: moving target reached where it is now, lock -> one swing -> hand back, target
  dies, attack orders). Not tried in game.

## 2026-10-11: fighting for the player (dodge by the game's own attack timings)
- User (screenshot): Q locks on and left mouse attacks as expected; the camera is fine; no getting
  stuck. Wants real combat: see when enemies attack, dodge, keep attacking. Asked about Laya / the
  NVIDIA model earlier: kept out of the real-time loop (too slow, GPU contention); code at 60 Hz.
- Research: the bridge already read the player's animation (CSChrTimeActModule queue); the entity
  table had none. Through the debug mailbox (WorldChrMan +0x1F1D0 character list, handle at ChrIns
  +0x08, modules +0x190, time-act +0x18): enemies' animations readable, 7 ms per character. Entity ids
  are handles, not addresses (the first probe failed on that).
- Bridge (AoER repo): new assistant combat block at 0x370000: every published entity's animation,
  time, length, speed; the player's animation, HP, FP, stamina (stamina 0x154 / max 0x158, FP 0x148,
  measured). Hot-reloaded into the running game; 4 microseconds per read.
- Attack timings: games/eldenring/tae.py reads TAE files (64-bit TAE3, layout from Smithbox's template,
  MIT); event type 1 = attack hitbox active (start, end seconds). Animation ids in the files equal the
  ids the game reports (live: c4300 3026, c4440 2000 found). tools/get_attacks.py: 289 models with
  animation files, 170 with attacks, 4,805 attack animations, 6 s -> .local/eldenring/attacks.json.
  119 models (variants such as c4071, c6001) hold only a skeleton and borrow animations from a model
  not yet identified (not the family base: c4070/c6000 lack their animation 43000).
- Adapter: snapshot entities get facing, anim, anim_time, anim_length, anim_speed; the player stamina,
  FP, anim; attack_windows(model, anim); capability combat.
- core/combat.py Fighter: lock on; roll sideways (alternating) when a facing enemy within reach will
  hit within 0.22 s (or its hitbox is out and no roll covered it), at most every 0.75 s; swing when in
  reach (1.8 m), no hit within 0.6 s, stamina >= 20, every 0.9 s; back off below 15 stamina until 50;
  hand back below 35 % health; stop when it dies / is lost / after 90 s. Orders: "fight/kill/take down"
  = fight until it's down (walks up first when further than 12 m); "attack/hit" = one hit. Haiku tool
  fight_character. Key taps no longer pause the loop 50 ms (released on the next frame).
- "Drop down here!" was said three times for drops close together: the same kind of line now at most
  once per 6 s.
- 92 tests pass (new: roll just before a hit, two rolls for a two-hit combo, no swing while a hit is
  coming, closing in, stamina, hand back, enemy facing away; fight order walks up then fights, close
  fight starts at once, takeover stops the fight). Not tried in game.

## 2026-10-11: first fights: the second Godrick Soldier hit repeatedly, no dodges
- User (screenshot): "Fight the nearest Godrick Soldier" twice: both killed ("Got it! It's down!");
  the first never swung; the second landed several hits and the fighter never rolled.
- Cause: the Godrick Soldier is model c4311, a variant without animation files of its own (one of
  the ~120 without attack timings): the fighter saw no attacks coming. Its animations are its base
  model's: c4310 (animation-only, no chrbnd); live, c4311 played 3043000, one of c4310's 1,165. Rule:
  a model without timings uses c###0. It covers 107 of 259 models without timings (the rest are
  mostly DLC). Attack windows are de-duplicated (several attack events often share one window).
- Combat log: runtime/combat-<date>.jsonl, 4 times a second and at each roll or swing: state, keys,
  taps, the target's animation and time, its attack windows, distance, whether it faces the player,
  the next hit expected, stamina, health. 93 tests pass.

## 2026-10-11: fight 3: still no dodges; the combat log showed why
- User: the fighter killed the soldier but never dodged; it took several hits.
- runtime/combat-20261011.jsonl (63 rows): the target's animation read 3043000 the whole fight (not an
  attack; health fell 673 -> 524 -> 470 -> 416 -> 331), next_hit never set, and the soldier "faced"
  the player at -0.84..-0.99 (i.e. away) while attacking.
- Dumping whole CSChrTimeActModule queues (mailbox): several animations play at once and each frame
  writes an entry per animation. c4311: 3000704 (4 s, time advancing) and 3043000 (0.03 s layer)
  alternate; the newest entry (read_idx), all the bridge published, is the layer. Goats blend up to 4.
- Bridge: the combat block (version 2) now copies each queue (10 entries, write/read index, speed);
  the adapter takes each distinct animation from the newest entries (up to 4, longest first) into
  Entity.anims, and the fighter checks every one for attack windows. Facing: the entity table's
  rotation maps the model's -Z to its facing (was +Z). Live after a hot reload: c4311 shows
  [(3000704, 4 s), (3043000, 0.03 s)], goats 2-3 animations; facing values positive toward the player.
- 95 tests pass (new: the soldier's real queue layout; a dodge of an attack playing beside a layer).
  Not yet fought in game with this.

## 2026-10-11: fight 4: attacks now seen, still no dodge (the fight log showed why)
- User: still no dodging. runtime/combat-20261011.jsonl: the soldier's attacks were now seen
  (3003000, 3003003, 3003006, 3003008 with windows; facing 0.94-0.99), 3 roll decisions at 0.21-0.22 s
  before the hit, but: (1) the frame after each roll press it pressed attack (the hit window was
  "covered", so nothing seemed to be coming): the later input wins in Elden Ring: no roll; (2) at
  296.66 it swung with a hit 0.66 s away; the roll at 297.09 couldn't start out of its own swing:
  hit; (3) in that fight stamina stayed 97 through 8 swings and 3 rolls: no input reached the game
  (the first fight's swings did cost stamina); unknown why: the log didn't record focus or held keys.
- Also: the player's own animation never showed: its idle is animation 0, skipped as "empty".
- Fixes: no swing within 0.75 s after a roll; swing only with no hit for 1.1 s (was 0.6); taps held
  60 ms (a roll happens on the key's release; one frame could be missed), released by a later frame,
  never by waiting; the fighter pauses when the game isn't in front (Navi: "Click back into the game
  and I'll keep fighting!"); the log now has focused, held keys and the player's own animation;
  entries with length > 0 are real (animation 0 included). Live: player animation 0 at 1.2 s.
- 97 tests pass (new: the two moments from the log replayed).

## 2026-10-11: fight 5: three soldiers down, still no rolls (the player's own animation shows why)
- User: fought 3 enemies; no rolling; maybe one block.
- Log with the player's animation: swings work (23030000/23030010, stamina drops). Roll presses came
  while the player was in its own attack (0.18, 1.1 and 1.48 s in) or staggered by a hit (5101, 5102):
  ignored by the game, except once: 27020 with stamina 97 -> 89 (probably a backstep: the direction key
  was released one frame after the press, and the roll happens on the key's release; likely the "block").
  Swinging every 0.9 s chained attacks, so a hit always found the player mid-swing.
- Changes: player_doing() reads the player's animation (attacking aXXX_03XXXX, staggered 5XXX, dodging
  27XXX, free); one swing at a time (only when the player is free again); swing in an opening (the
  target's attack windows are over and it still needs >= 0.5 s) or when nothing hits for 1.1 s; the roll's
  direction is held 0.3 s; roll pressed 0.25 s before the hit (was 0.22).
- 101 tests pass (new: reading the player's animation, one swing at a time, roll keeps its direction,
  swing in the opening). Not yet fought in game.

## 2026-10-11: how people fight in Elden Ring (research) -> roll timing and direction
- User: look into how people normally fight, optimally, maybe different per enemy.
- Sources: eldenring.wiki.gg "Invincibility Frames" (14 i-frames for light/medium rolls, 13 heavy, from
  the roll's start), Fextralife "Equip Load", Bandai Namco's combat guide, TheGamer, Windows Central:
  dodge a split second before the hit; roll through/into attacks rather than away; punish after big
  attacks with a short combo, don't trade; manage stamina. The offline Fandom wiki has almost no fighting
  advice for common enemies (Godrick Soldier: lore and drops only). The per-enemy part players learn
  (delayed swings, combos, wind-ups) is exactly what the extracted attack timings give.
- Changes: press roll 0.16 s before the hit (was 0.25: the roll started ~0.19 s early and its 0.23 s of
  invincibility ran out during longer swings); roll forward-left/right (through, while locked on);
  another roll after 0.55 s (was 0.75) for combos. PLAN.md: "Fighting for the player" with per-enemy next
  steps (reach per attack, projectiles, grabs, parries, learning from fight logs, healing). 101 tests pass.

## 2026-10-11: fight 6: rolling works; remaining hits analysed one by one
- User: "it was doing a bunch of rolls this time! It actually dodged the hits multiple times"; still
  several hits. Wants none (one on one), using what we know of the enemies' timing.
- Each hit in runtime/combat-20261011.jsonl (6 hits, 4 rolls, 31 swings, 1 win):
  1. a two-swing chain (3003000 then 3003001, 0.08 s warning seen): the second roll was blocked by
     "not while dodging" although a roll can follow a roll after ~0.5 s;
  2-3 and 5. attacks skipped because the soldier faced 90-130 degrees away at that moment (it turns
     during the wind-up); one swing went straight into such an attack;
  4 and 6. staggered from the previous hit (5203 lasts ~1.8 s, no roll possible): chains;
  also: a light attack (23030000) can't be rolled out of for ~1.3 s (logs: 1.1 s ignored, 1.48 and
  1.6 s rolled), and swings came with hits 0.5-1.1 s away.
- Changes (core/combat.py): roll again after 0.55 s even while rolling; facing filters only other
  enemies further than 2.5 m (never the target); bait and punish: wait at 3 m (out of most reaches),
  step in only for an opening (the target's hits are over and it needs >= 1.35 s more) or a passive
  enemy (no attack for 2.5 s) with nothing landing within 1.35 + 0.16 s; back out after; never swing
  while still in our own swing or staggered.
- 104 tests pass (new: two-swing chain gets two rolls, no swing that can't be rolled out of, punish a
  long recovery, facing only for others far away, spacing then in for a passive enemy). Not yet fought.

## 2026-10-11: combat: hitting while it reels, poise, rocks behind
- Third fight (user): dodged most hits and kept out of reach, but never attacked; once a rock stopped
  the step back and a hit landed. The user finished the soldier by hand.
- Why no attacks: openings needed >= 1.35 s of recovery after the enemy's last hit; the Godrick
  Soldier's recoveries are shorter (3003005: hit 0.4-0.6 s, ends ~1.5 s) and it chains attacks.
- From runtime/combat-20261011.jsonl (real rows): each landed hit made the soldier play a ~0.4 s
  flinch (3008031/3008032/3008036 with 3009411/3009412) 0.27-0.53 s after the press; it attacked
  0.4-0.5 s later (3003000, hit 0.8 s in): ~1.2 s with no hit coming. The second hit of a combo
  re-flinched it 0.26 s after its press.
- Poise: params.py read `toughness` (0x19C), which Elden Ring leaves at 0 for every enemy; poise is
  `superArmorDurability` (f32 at 0x2A8, offsets checked against the Paramdex NpcParam def: row size
  0x2E0). Live: Godrick Soldier, Land Squirt, Watcher Stones 30; wildlife -1 (none).
- Changes (core/combat.py): low-poise targets (<= 60) are punished with >= 0.45 s of recovery left and
  engaged after 1 s idle; a reaction animation that started after our press means the hit landed:
  press the next combo attack (3 in a row, 2 for sturdier enemies; none while the reaction is a layer
  on an attack with hits to come); a reaction >= 1 s (stagger, stance break) is hit without a cap;
  a step back that moves < 0.4 m in 0.5 s strafes sideways for 0.8 s. The app passes the poise from
  NpcParam; tests no longer write to the real combat log (earlier test rows are mixed into
  combat-20261011.jsonl: the real ones have `target_anims`).
- 112 tests pass (new: reaction animations, combo chain up to the cap, fewer against sturdy enemies,
  no chain without a flinch, no opening from a layered flinch, keep hitting a stance break, short
  punish only for low poise (log replay of 3003005), strafe past a rock). Not yet fought.

## 2026-10-11: combat: repeated attacks dodged again, act in the roll's tail
- Fourth fight (user): still no attacks. Log (runtime/combat-20261011.jsonl, 13:51): 6 rolls, 1 swing,
  3 hits taken, handed back at low health.
- Hit 1 (9.6 s) and hit 3 (15.4 s): the soldier repeated an attack already rolled once (3003003, 3003005);
  "covered" was keyed by animation only, so a repeat never got a roll. Now a cover holds only for the
  hit time it was (within 0.5 s). Hit 3 came while the player was still staggered by hit 2 (5323, 1.85 s).
- Hit 2 (13.5 s): a roll was pressed 0.15 s ahead of 3003008 while the player played 22101 (1.0 s in);
  no roll came out. 22101 is not identified yet.
- No attacks: openings were found (op=1 in 17 rows) but the player's roll animation (27120) runs ~1.8 s
  and counted as busy the whole time; rolls pressed 0.58 s and 1.03 s into it came out, so from 0.55 s
  into a roll the player counts as free. Low-poise enemies are now awaited at 2.2 m (not 3 m): walking in
  from 3 m took long enough for the soldier to chain its next attack.
- Log rows of roll frames now carry the poise too (an analysis filtered on it and missed the rolls).
- 115 tests pass (new: the same attack again gets its own roll; free in the roll's tail; closer spacing
  for low poise).
- Orders: "Attack that nearest soldier" (14:20) was a one-hit order by the old rule (only "fight / kill /
  defeat" fought), so it hit once and handed back. Now every attack order fights (dodging until the enemy
  is down); one hit only when asked: "hit it once", "land one hit" (and the model's command prompt says
  the same). "hit on the soldier" no longer keeps "on" in the name. 115 tests pass.

## 2026-10-11: combat: several enemies
- Fifth session (user): "attack the nearest soldier" killed it (5 swings in 3 s, no hits taken). Then
  "kill the next nearest one": it picked a soldier 2.4 m away that then walked off (2100, facing away,
  to 30 m) while another soldier attacked; the game's lock-on was on the attacker, so "closing" on the
  first pressed back (the camera faced the attacker). 18 rolls, 2 hits taken, no swing; the user took over.
- Changes (core/combat.py): the target further than 5 m and another enemy within 4 m attacking or facing
  the player: fight that one. Locked on but the camera more than 45 degrees off the target for 0.6 s: the
  game locked someone else: fight the enemy the camera points at (within 20 degrees), or unlock, turn,
  and lock again once the camera points at the target. The target down and another one on the player:
  carry on ("One down! Now the other one!"). No punish or combo press while another enemy's hit would
  land before the player could roll again.
- 120 tests pass (new: attacker over a walker, follow the game's lock-on, unlock and turn, next enemy
  after a kill, no punish into another enemy's swing).

## 2026-10-11: orders with extra words; ledges; runaways
- Sixth session (user): combat works well (soldiers, a boar: rolled its attack, killed it). Problems:
  "Can you kill one of the deer for me?", "kill this Godrick soldier from me", "kill the next one too"
  got "I don't see any ... to attack" (runtime/chat-20261011.jsonl 14:39-14:41): "of", "from me", "too"
  stayed in the name. Then, chasing a fleeing deer in a straight line (the fighter's own steps, no
  navmesh), it switched to another animal and walked off a cliff (player 4050/4000 falling, then 17090).
- Orders: words that are never part of a name are dropped (of, from, me, too, also, again, another...);
  "the next one" / "another one" means the kind last attacked.
- Ledges: while fighting, the app casts 16 rays down (8 directions, 1.5 m and 3 m out, to 3.5 m below
  the feet) 10 times a second (2.5 ms live, all ground found on flat ground); no step goes toward a drop
  (the nearest safe direction within 90 degrees, else none) and no roll (the other side, a plain side
  roll, else no roll: a hit rather than a fall).
- Runaways: the target over 14 m away for 1 s: the fight hands over to the navmesh walk (sprints, follows
  it) and fights again on arrival ("It's running! After it!"); after 3 chases: "It keeps running away!
  Over to you."
- 127 tests pass (new: the session's phrases, "the next one", no step toward a drop, only safe ways, roll
  away from a drop, no roll when every way drops, runaway handed to the walk).

## 2026-10-11: orders from the field; a walk log
- Committed the combat work (PR #11). The user reported orders still misunderstood and paths still
  struggling (with a screenshot that did not come through).
- Orders that went wrong (runtime/chat-20261011.jsonl): "now the next one please" (no verb: nothing);
  "there was a boar around here, can you kill that too?" (nearest enemy, not the boar); "kill this enemy
  in front of me" (a Springhare behind, the nearest hostile); "kill all of the soldiers around here"
  (nothing, twice). Now: "the next one / another one / one more" alone fights the kind last fought; a kind
  named anywhere in the order fills in "that / it" (whole words only: "here" must not match "Deer");
  "this / that / in front" prefers the nearest match within 30 degrees of where the camera looks; "all"
  fights them one after another (the next of that kind within 60 m after each kill: "Got it! Next
  Godrick Soldier!", then "That's all of them!").
- Walks had no log, so a bad path could not be traced. runtime/walk-<date>.jsonl now has, twice a second
  and at each event: state, events, position, goal, keys, camera heading, progress along the route, the
  player's animation; and each new route in full (waypoints, complete or partial, ladder/lift/jump
  actions). Tests write to neither log.
- 133 tests pass (new: the four session orders, whole-word mentions, the walk log).
- Screenshot session (15:21-15:22, chat + the new walk log): "How many soldiers ... around here?" (Navi:
  three Godrick Soldiers), then "Can you kill all of them from me?": the order alone has no kind, so it
  fought "any enemy", then went on to an Eagle 35 m up a cliff (walk log: goal y 150.5, player 116; 18 m
  on the map, so the walk went straight at it: unstick, stuck, gave up). "Oh no, not the Eagle, all the
  soldiers." had no verb: the model asked a question.
- Now: questions and Navi's answers of the last minute give "them / those / all of them" a kind ("that /
  it" stays what the player looks at); an order without a verb within 90 s of an attack order that names
  a kind is a correction ("not the X" excluded); "any enemy" and "all of them" skip characters more than
  8 m above or below the player; a walk to a character goes straight at it only within 4 m of its height
  (else along the route). 138 tests pass (new: the screenshot session, a character far above).

## 2026-10-11: bullets: hits after the swing window
- "Kill all of them" sweep (user): every soldier in the area killed, one hit taken. Combat log: a Godrick
  Soldier's 3003008; the forward roll was pressed at 1.02 s and came out (27120), covering the swing
  window 1.167-1.233 s and carrying the player into the soldier (gap 2.3 -> 0.5 m); the hit landed after
  the roll (between 1.28 and 1.57 s). The same attack hit the same way in the fourth fight. The game data
  has a bullet for 3003008 at 1.2 s (a separate hitbox: a projectile or lingering sweep) the fighter
  never read.
- Now (adapter): bullets of attack animations (3XXX) count as hits for 0.3 s from launch, merged with
  overlapping swing windows (3003008: 1.167-1.5 s; 3003009 1.067-1.367; the 3003007 throw 1.5-1.8);
  bullets of other animations (the alert shout 3020021, flinch effects) are not hits. (combat) A hit
  longer than 0.2 s outlasts the roll's invincibility: roll away (back-left/right), pressed so the
  invincibility (~0.10-0.33 s after the press) is centred on it. 140 tests pass.

## 2026-10-11: combos and multi-hit charges: roll away
- Seventh session (user): the 3003008 bullet attack was dodged; two other hits. (1) A soldier's 3003000 ->
  3003001: the first rolled forward (into it, gap 0.76 m), a second roll pressed 0.58 s into the first
  only came out at ~0.95 s, after 3003001's hit. (2) A boar's charge 3000 (hits at 1.633, 2.133, 2.633 s):
  the first rolled forward into it, then 3002 hit as it started (window 0.0-0.2 s, no warning).
- Measured in the fight log: a roll pressed 0.50-0.65 s into a roll is buffered and comes out at
  0.95-1.0 s; earlier presses are lost. So a hit within ~0.95 s of the one rolled can't get its own roll.
- Now: the fighter knows the follow-up of a hit (a later window of the same attack, or the next attack of
  a learned combo); when it comes sooner than 0.95 s, the roll goes away (back-left/right: out of reach,
  out of a charge's line) instead of through. Combos are learned from what enemies near the player do
  (attack A then straight into B: A's time at the switch), kept in .local/eldenring/chains.json; seeded
  from today's log for the Godrick Soldier (3003000 -> 3003001 seen 20 times, switch at 0.92 s: rolled
  away; 3003001 -> 3003002/3003003 and others leave time for a second roll: still rolled through). The
  fight log now has the target's model. 144 tests pass.

## 2026-10-11: orders in sequence
- User (screenshot): "Alright, please kill the boar and then kill any remaining soldiers." -> "I don't see
  any boar and then kill soldiers to attack."; the same with "...and then after you've killed the boar,
  kill any soldiers around here": everything after "kill" was taken as the name.
- Now: an order is split at "and then / then / after that / and" (when another order follows) and at
  "and" between names of an attack ("kill the boar and the soldier"); a leading "after you've killed X,"
  is dropped. The first part runs; the app runs the next when it is done (the target down, "all of them"
  done, a walk arrived); stopping, taking over, low health or giving up clears the rest. "any / remaining
  soldiers" (a plural) means all of them. 147 tests pass.

## 2026-10-11: plans: general orders through the model, run by code
- The user wants general comprehension rather than rules case by case, and orders like "go to the nearest
  site of grace and kill any enemies you encounter along the way", "kill everything around here,
  alternating soldiers then other things, until everything is dead", and a nine-part order (circles, a
  line, a walk without fighting, wander until a soldier is met and kill it, ..., walk back).
- Design: the model writes a plan in a small step language, code runs it (core/plan.py: go, fight, move,
  wait, with engage / until_see / count / cycle / shape / seconds; agent.plan with one make_plan tool).
  Orders with several parts or condition words go to the planner (app.needs_plan); a rule result of
  "none" does too; simple orders keep the instant rules. A new order, or the player taking over, stops a
  plan; low health or a walk that can't get through stops it with a line. Move steps use the ledge probes.
  A wander wanders around where it began; an until_see search gives up after 5 minutes.
- Live (Haiku, the running game): the three example orders gave the right plans in 1.3-1.7 s, e.g. the
  nine-part one: move circle 10 s, move line, move circle 5 s, go Groveside Cave, move wander until_see
  Godrick Soldier, fight found, go Gatefront Ruins until_see Godrick Soldier, fight found, go Groveside Cave.
- 158 tests pass (new: tests/test_plan.py: the model's JSON checked, go with engage all and none, kill
  everything alternating kinds, until_see then fight found then go back, circle then line timing, takeover,
  unknown place skipped, routing, wander centre and search limit; an app-level plan run).

## 2026-10-11: walls, the lock on the other soldier, shields, a walk after a pause
- Eighth session (user, 16:35-16:37; combat and walk logs): (1) a soldier behind a wall: the fighter held
  forward for 10 s at a constant 2.05 m, just out of reach; (2) one soldier fought well; (3) two soldiers:
  most attacks dodged, 4 hits taken, one swing (into a shield): the game's lock was on one soldier while
  the target was the other, so 15 openings went by with the camera 90+ degrees off the target (no swing),
  and the target flipped 4 times in 3 s; the one swing went into 3002's "recovery" right as it chained
  into 3000 (three hits); a hit from 3.7 m was outside the 3.2 m watched; a second soldier's hit came
  ~0.4 s after the one being rolled (no second roll possible); (4) the walk to Groveside Cave gave up:
  after a pause (window out of focus) the character stood 1.8 s with forward held, a false "stuck".
- Combat: locked on to an enemy within 6 m: that one is the target at once; openings end where a learned
  combo goes on (chains); another enemy's hit within 0.95 s of the one rolled: roll away; hits watched from
  4 m; a ray from the chest to the target 10 times a second (with the ledge probes): no swing through a
  wall; closing that gains < 0.25 m in 0.8 s (or a wall between) steps sideways 0.7 s, sides in turn, and
  after 3 tries gives the target up ("I can't reach that one!"; "all of them" and plans go on with others
  and don't pick it again); a swing at an idle enemy with no flinch within 0.7 s: it blocked (a shield):
  only openings after its attacks from then on.
- Walk: 2 s after a pause before "stuck" counts; a stuck walk also marks the spot 1.2 m ahead as one to
  route around (props like the campfire are not in the navmesh).
- 165 tests pass. Live: the line-of-sight ray to a soldier 38 m away was blocked by terrain 3.6 m out.

## 2026-10-11: walls by the navmesh; quicker punishes
- Ninth session (user, 20:31-20:35): (1) "kill all the soldiers": one 39 s fight, 22 rolls, no damage, only 4
  swings; (2) the next soldier, 13 m away behind a ruin wall (screenshot): the walk went straight at it
  for 17 s with no route at all, stuck, gave up. The user asked whether the navmesh knows such walls.
- It does: the planner routes round that wall (east along it, then north through a gap: 0.1-2.3 s from
  every point the walk stood on). The walk never had the route: the companion counts a goal within 15 m
  as reached (Navi's "here we are") and so never planned one, and the walk went straight at characters
  within 20 m regardless of walls.
- Now: the companion plans a route to close goals too while the character walks itself; the walk goes
  straight at a character only with a clear line (the chest-to-target ray, now also cast while walking
  to a character) and only counts as arrived with one; a fight with a wall in the line for 1 s (or no
  way round after 3 sidesteps) hands over to the walk by the route, which starts the fight again on
  arrival ("There's a wall in the way. Going round!"); only a second time is the target given up.
- Quicker punishes (the 39 s fight): an action is possible 0.5 s into a roll (was 0.55, and no swing for
  0.75 s after a roll press: most 0.3-0.7 s openings went by; an attack late in a roll comes out as a
  rolling attack); it no longer steps back out of reach while the target recovers from an attack; and a
  low-poise enemy (known poise <= 60) in an attack's wind-up with its hit still >= 0.75 s away is struck
  first (a light attack lands in ~0.3-0.4 s and flinches it out of the attack, as the logs showed); an
  attack that doesn't flinch is remembered (armoured) and not tried again.
- 171 tests pass.

## 2026-10-11: blocked gaps stay blocked
- Tenth session (user, 21:56, screenshot at a Gatefront barricade): the walk now followed the navmesh route
  but got stuck at a wooden barricade with a tarp, between the player and a soldier.
- Found: the game's navmesh has a gap there (the faces on the two sides aren't joined), but the made-up
  "step" links (edge to edge across gaps up to 3.5 m, added for cave mouths) bridged it. The route check
  found the step link blocked and banned it, but there were many across the same gap: each of the six
  checks banned one, and the route still ran through the barricade. (Leg rays at the spot: a vertical
  surface 0.4-2.5 m along at every height.) Avoid spots also did nothing there: they only matched a face
  by its centre, and the faces are 5-8 m across.
- Now: a made-up link found blocked blocks every made-up link within 4 m of it (navgraph link_blocks); a
  wall a leg ray finds blocks the navmesh crossing there (the faces just before and past it; inside one
  face, that face costs 60 m more); a stuck walk blocks the crossing ahead too. Found blocks are kept in
  .local/eldenring/world_blocks.json (a barricade found once stays known).
- Live: from the stuck spot the route now goes north round the barricade (27 m, every leg clear); from
  where the walk began, east round it (36 m); a fresh adapter loads the blocks and goes round at once.
- 173 tests pass.

## 2026-10-11: real reach; groups; terrain when backing off; healing
- Eleventh session (user): navigation round the barricade worked; combat good; but a bat's reach was
  misjudged, and a fight against several enemies ended in death (log: 45 s, 32 rolls, 4 swings, 47 target
  switches). The user asked for each enemy's real reach and the player's weapon's, steady targeting that
  still reacts to every enemy, backing off with terrain in mind, and healing only when safe.
- Reach from the game's data: an attack animation's hit events name behaviour judges (TAE event 1, param 2);
  BehaviorParam row 200000000 + the NPC's behaviorVariationId * 1000 + judge (holder 12; refType 0 ->
  AtkParam_Npc, holder 7; 1 -> a bullet); AtkParam hit spheres (16: radius, one or two attachment points);
  attachment points' reach from the model's centre with the limb stretched out (games/eldenring/flver.py,
  FLVER2 dummies and bones from chr/c####.chrbnd.dcx; get_attacks now also saves "judges" and
  "dummy_reach"). Godrick Soldier swings: 2.6 m from its centre (sword tip 2.3 + 0.3; the thrust 2.34);
  bat: wings/claws 2.2-2.3 m, dives a 1.0 m body slam (all the distance is the dive).
- How far an attack carries the enemy (a lunge, a dive) is learned live per attack (kept with the combos),
  1.5 m until seen, or its current speed toward the player times the time to the hit if more. An attack
  threatens when the player is within reach + travel + 0.4 + 0.4 m; unknown reach: the old fixed 4 m.
- The player's weapon: the right-hand slot from the player's game data (GameDataMan 0x3D61F98 -> +0x08
  -> equipped ids at +0x398, found by scanning and checked against EquipParamWeapon: Longsword +2) and its
  wepType; reach by type (straight sword 1.8 m from the target's edge, spear 2.4, ...), raised by swings
  that took health off the target at longer gaps (at most +0.7). "Armoured" now needs the hit to have
  landed (health down) without a flinch; "shield" needs the swing to have been in reach.
- Bat: a target flying off fast (> 2.5 m/s away) isn't chased; a moving target's gap not shrinking isn't a
  wall. Learning (combos, lunges) now runs every frame, also while a roll's direction is held.
- Groups: a target is kept 2.5 s at least (sooner: dead, far, unreachable); when the game's lock drifts to
  another enemy meanwhile it unlocks and locks back on to its own; a new target by score: in front, then
  the weakest, then the nearest. In a group (2+ within 6 m) it waits 1 m further out and backs away from
  the enemies' middle.
- Terrain: 8 rays at chest height find walls and rocks within 1.6 m (with the ledge probes); stepping and
  backing off go the nearest clear way (no wall, no drop).
- Healing: at 50 % health a flask (the item key, R by default; new keys are now merged into saved
  settings) only when no hit is predicted within the 1.6 s the drinking leaves no roll, and no enemy can
  close in and swing by then (its speed, a lunge, its reach); otherwise it backs away to make room first.
  A drink that heals nothing twice: "no flask left, or none in your item slot", no more tries. Handing
  back at 20 % health while it can heal, 35 % when it can't.
- 183 tests pass.
- Twelfth session (user, 23:53): flask drinking worked (331 -> 581 health), but the last soldier was fought
  for 33 s with no roll and one swing. Log: target_windows empty for every attack. Cause (a regression from
  this round): get_attacks now gives variant models (c4311) an entry of their own holding only
  "dummy_reach", and attack_windows used that entry instead of falling back to the base model (c4310) with
  the timings: no attack was seen, so no rolls and no openings. Fixed (fall back when an entry has no
  "attacks"); a regression test. 184 tests pass.

## 2026-10-12: crowds: out of the middle
- Thirteenth session (user): fights better; six soldiers killed one after another without damage; then a
  group: up to eight soldiers within 6 m (runtime/combat-20261012.jsonl, 22 s, 18 rolls, 1 swing, 673 -> 173,
  death). Every hit came the same way: swings from different soldiers ~0.5 s apart, while a second roll
  (pressed 0.46-0.68 s into the first) waited to come out at ~0.95 s.
- Rolling can't answer a crowd. Now with 3+ enemies within 5 m it sprints away from their middle (the
  nearest clear way; still rolling when a hit comes, with the sprint let go 0.6 s before a predicted hit:
  sprint and roll share a key) until at most one is within 8 m (or 8 s), then fights the nearest (chasers
  string out). Caught in a crowd a fourth time in a fight: "Too many of them! Over to you!". 187 tests pass.
- Fourteenth session (user): the crowd escape ran "ok" but hits still landed and the player died; and many
  openings were missed. Log: a 24 s fight with one soldier, 5 swings: several times in reach with the camera
  90 degrees off the target (no swing: the game had dropped the lock when the soldier walked 9 m off, the
  fighter still believed in it; and its "unlock" press then locked someone else); a swing pressed during a
  roll was swallowed but still held the next swing back 0.9 s. The crowd (5-7 soldiers): the escape was a
  backpedal (locked on) without sprint (dropped near hits, nearly always), so they kept pace; hits landed
  while a second roll waited; "surrounded" handed back in the middle of them.
- Now: no lock press when the camera stays off the target: the belief is dropped, the camera turned to the
  target, and lock on once it's in front; a swing press with no attack animation 0.35 s later doesn't count;
  the escape turns the lock off and keeps the sprint held (a roll lets go of it for one frame first); giving
  up on a crowd, or low health with enemies within 8 m, first runs clear (nobody within 15 m, or 12 s), then
  hands back. 189 tests pass.

## 2026-10-12: imported attacks, attacking when safe, reach learned from hits
- Fifteenth session (user): still many missed openings; hits even one on one ("maybe it's not taking the
  enemies' weapons into account"); and on arrival it waited for soldiers to draw their weapons.
- Hits: five of 179 came from 5003008 (a weapon moveset), which had no hit timings: its TAE entry has no
  events and imports another animation's (anim file header type 1, source id at +0x18: 1003008). The parser
  ignored imports. Now tae.imports and get_attacks follow them (14 more attack animations; 5003008:
  1.533-1.6 s, reach 2.6 m). The other hits: rolls that couldn't come out in time (a roll right after a
  roll, or during a drink).
- Weapons: all 221 Godrick Soldier NpcParam rows share behaviour variation 43100, and all six model
  variants (c4311-c4316) have the same sword attachment points: the weapon models aren't in the character
  files. So reach is now also learned from hits: when health drops while an enemy's hit window is out, an
  attack's reach is raised to the distance it hit from (kept with the combos, as "_reach").
- Attacking: a normal enemy (known poise <= 60) is now struck whenever no hit from anyone can land within
  0.75 s (a light attack lands in ~0.35 s and flinches it), no shield seen: no waiting for an opening or for
  it to draw its weapon. 193 tests pass.
- Sixteenth session (user): attacks now start at once, but many openings still went by; one on one it
  "kept dodging and barely attacked", the camera often facing away. Log (89 s, 47 rolls, 5 swings): of 199
  moments in reach without a swing, many had nothing coming and the fighter free, but the camera was more
  than 40 degrees off the target (no swing allowed): it only turned the camera when it believed the lock
  was off, a belief the game often breaks; "closing in" at 0.45 m then sidestepped as "blocked" (9 times);
  and one swing that went wide was taken for a shield, which turned the attack-when-safe rule off for the
  rest of the fight.
- Now: the camera is turned to the target whenever it is more than 25 degrees off, lock or no lock (a lock
  on another enemy moves along); in reach with the camera off it turns instead of walking; a swing more than
  10 degrees off holds the direction to the target (unlocked, the character turns into it); a shield is
  only concluded from a swing within 20 degrees of the target, and believed 8 s. The fight log has the
  camera's offset ("cam_err") and the lock belief. The test simulation now turns its camera. 197 tests pass.

## 2026-10-12: after an escape, the middle tier, no "enemy ahead"
- Seventeenth session (user): better; but after getting room it ran back into a group; several hits; a
  stronger unit among the soldiers; and remove "Careful, an enemy ahead!".
- Log: (1) the escape got 20 m clear, then its old target (in the group) was "fleeing" (> 14 m), so the walk
  chased it straight back into four enemies: a 424-damage hit. Now during an escape and for 6 s after it
  there is no chase; the fight takes the nearest enemy, and waits for chasers further than 5 m instead of
  running at them. (2) A swing from 2.4 m fell short and the soldier's attack landed: attack-when-safe now
  only from 0.3 m inside the reach. (3) The stronger unit (c4351, poise 65, multi-hit attacks) was fought
  with the rules for tough enemies (poise > 60): openings needing 1.35 s it never left, a 2.5 s idle wait:
  27 s without a swing, 521 -> 10 health. Now a middle tier up to poise 120: openings with 0.8 s left, an
  idle wait of 0.5 s, the closer spacing (interrupting and attack-when-safe stay for <= 60).
- The "Careful, an enemy ahead!" warning is gone (autowalk no longer watches for enemies along the route).
- 200 tests pass.
- Eighteenth session (user): attacks waited for soldiers to draw again, and the walk got stuck at the Gatefront
  barricade again. (1) A regression from the last change: the "0.3 m inside the reach" margin was put on the
  decision to go in, so an idle soldier just out of reach was never walked up to. Now the margin is only on
  the swing (it walks in that far first); a regression test. (2) Walk log: the walk to the soldier behind the
  barricade had no route for its whole 10 s (the planner, asked afterwards from the same spot, routed round it
  in 2.1 s), so it went straight at the soldier into the barricade. Now a walk with a wall between it and its
  target and no route waits for one instead; a walk without a route for 2.5 s restarts the companion's
  planning; the walk log has the planning flag, the clear line and the companion's mode. 202 tests pass.

## 2026-10-12: barrels; a soldier running in
- Nineteenth session (user, screenshot): stuck against a stack of barrels at Gatefront; and a hit that should
  have been dodged.
- Barrels: live rays pass through them with every filter tried (terrain 0x5D and 18 others): breakable
  objects block the character but no ray sees them; and they stand inside the 6.6 m navmesh face the
  character stood on, so no face (or crossing) could be avoided: the re-plan after "stuck" was the same
  straight leg. Now: a stuck spot is kept (world_blocks.json, "stuck_spots"); its face is not used by later
  plans; a route leg passing within 1 m of one gets a waypoint 1.8 m beside it (the shorter side not near
  another stuck spot); and a stuck walk backs off 0.35 s then goes diagonally forward to one side 1.2 s
  (sides in turn) before trying again. Live: the Gatefront route now goes round the barrels at (60.2, 65.5).
- The hit: the third swing of a combo on one soldier while the next ran in from 5.4 m; it swung on arrival,
  during ours (no roll possible). Now no swing or combo press while another enemy runs toward the player
  (> 1.5 m/s) close enough to arrive and swing before a roll is possible again.
- 205 tests pass.
- The user: learning from bumping into things only helps the second time, and the assistant mostly goes
  where it has never been; things that block the character but no ray sees are likely breakable: hit them.
  Now a stuck walk with nothing seen just ahead (two rays, knee and chest high, 1.6 m toward the next
  waypoint, cast with the ledge probes during any walk) swings at it twice while walking into it ("Something's
  in the way. Let me smash it!"), at most twice in one place; a wall seen ahead, or breaking that didn't clear
  it: stepping round and re-planning as before (stuck spots are still kept as a fallback). 207 tests pass.

## 2026-10-12: smashing for real; walls found on the way; rolls away by default
- Twentieth session (user): many hits that should have been dodged; stuck at walls the navigation should know;
  "Something's in the way. Let me smash it!" without a swing.
- Smashing (and stepping round) never happened when walking to a character: the chase returned before those
  moves (four "smash" lines, no swing). Now they come first, in both ways of walking; a test.
- Walls: (a) a leg through a wall was accepted at planning time (17+ m off; that collision likely wasn't
  loaded yet): the walk now re-checks its next two legs with rays every second, and a wall found blocks that
  crossing (kept) and the route is planned again at once. (b) After "stuck", no route came for 12 s: my
  planning restart (every 2.5 s) threw away the running plan's result each time (a plan here takes longer).
  Now it restarts only when no plan runs, or one has run 12 s.
- Hits (9): five came from an attack following too soon after another (a roll only every ~0.95 s), after
  forward rolls. The soldiers chain a lot (the logs: 42 attacks seen followed by another, 2 ending alone), so
  rolls now go away from the attacker unless the attack is known to end on its own (seen twice: learned and
  kept, "_solo"; seeded from the logs). One swing in a wind-up met an attack that doesn't flinch: attack-when-
  safe no longer fires in an attack's wind-up (the interrupt rule, which knows armoured attacks, does). One
  hit while walking between fights: during a fight order, an enemy within 6 m coming at the player is fought
  there. Rolls in a sprint mostly didn't come out (9 of 12): the escape lets go of the sprint 0.25 s before a
  predicted hit (rolls in 221xx animations came out 34 of 35: fine).
- 212 tests pass.

## 2026-10-12: escaping to open ground
- Twenty-first session (user, screenshot): "Too many! Getting some room!" again and again while it rolled
  into a wall: the escape ran straight away from the enemies' middle (walls only checked 1.6 m round, rolls
  not at all) into an enclosed corner. The user: escape with proper navigation, toward open space.
- Now the app picks open ground when an escape starts: spots 12 and 18 m away in 16 directions, none within
  6 m of an enemy nor toward one; scored on openness (8 rays round each at chest height, 4 m: a corner scores
  low), distance from the enemies and heading away; the best three are planned to on the navmesh, the first
  reachable without a long detour taken. The fighter follows that route at a sprint, and rolls along it;
  until it comes, straight away round walls and drops as before. Rolls never go into a wall the probes see
  when another way is clear. The escape line is said at most every 20 s. Live (no enemies): open ground 12 m
  west, route in 2.2 s. 214 tests pass.

## 2026-10-12: rolls chosen in the world, whatever comes at the player, lost key presses
- Twenty-second session (user): "kill all the soldiers around here" still took too many hits (673 -> 59 health
  in a crowd it never got out of, 673 -> 26 in a fight with soldiers and a wolf); other enemies joining (wolves,
  bats) must be fought too; navigation still gets stuck.
- The day's 62 hits, by what came before: 39 right after a roll, 10 while staggered, 9 during our own swing,
  4 unpredicted. The rolls: keys "back"/"forward" are relative to the camera, and with the lock off (escaping,
  or the game dropped it) the camera points anywhere, so "away" rolls landed by other soldiers or into walls;
  pressed in a roll's tail they only come out ~0.95 s in, after the next hit. Now each of the 8 directions is
  turned into the world and judged by where the roll lands (2.6 m on): away from the attacker when its next hit
  follows too soon (sideways to a lunge), else past its side; away from every other enemy near the landing
  spot; along the escape route; not into a wall while another way is clear; never off a ledge.
- Backing off and escaping head for the widest gap between the enemies near (16 headings, each enemy weighted
  by nearness, walls and drops counted), not away from their middle; the escape no longer sidesteps when slow
  (rolls cut its steps short: it flailed for 15 s pinned at 0.45 m). The open-ground search no longer drops
  every direction within 60 degrees of an enemy (surrounded, it found none): it scores how far each way is
  from them. The combat log now has the player's position, every enemy near (attack, timing, heading,
  facing), the roll's heading and how the open-ground search went (hits from others couldn't be told apart).
- Swings: none while another enemy stands within 2 m facing the player (it can hit mid-swing). A soldier's
  long thrust (x003008) doesn't flinch: a swing into its wind-up landed, a reaction layer played over it and it
  hit anyway (three times). Seeded as armoured, and the learning fixed: a reaction alone doesn't count as
  interrupted when the attack goes on (learned ones kept with the combos).
- Whatever is on the player is fought: an engaged enemy (attacking, or facing and near or coming closer) that
  is a much better target than the current one is switched to (a wolf biting while the soldier stands off);
  when a target dies the next is any engaged enemy within 8 m; a "kill all the soldiers" order fights whatever
  is coming at the player (any kind, 20 m) before walking to the next soldier.
- Navigation: three walks "stuck" with forward held and the character idle (animation 0): the press sent as the
  window came back into focus was lost and never sent again. Now a movement key held 0.6 s with the character
  idle and not moving is released and pressed again. Chasing a soldier with a clear line at chest height, it ran
  into something low twice (stuck 4-7 s; smashing didn't help) while the navmesh route went round: now it goes
  straight at a character only when the route there is straight (no waypoint 2 m off the line), and after
  getting stuck chasing it follows the route for 8 s; the walk's look-ahead also probes shin-high (walls only,
  by the hit's normal). No planning restarts while paused; the walk and fighter get the real frame time.
- 224 tests pass.

## 2026-10-12: fighting packs instead of fleeing them; a walk left without a route
- Twenty-third session (user): better, but it still didn't kill all the soldiers. The log: four soldiers killed
  one at a time, and no hit taken in the whole fight (the rolls chosen in the world: 0 damage in 36 s among 3-6
  soldiers). Then a pack of 6-8: an escape from every 3 within 5 m, four in 36 s, the pack (as fast as a sprint)
  back on it each time, not one swing, and "surrounded" handed back with full health (673/673). In that pack
  4+ were within 5 m only 5 of 38 s.
- Now an escape needs 4 within 5 m (or 3 while it is being hit: a hit within 4 s); "surrounded" only when it is
  also hurt (below half health); otherwise it goes on. A soldier running in held back every swing in a pack
  (someone always runs in): for one swing it now counts only one that could arrive and still wind up its hit
  within the swing (a combo keeps the full time). The fight's 90 s limit is per target, not per fight.
- A boar asked for was left for a soldier coming over (the game's lock-on moved to it): the enemy named in the
  order is only left for one that attacks (or one right there, nearer), and after that one is down it goes
  back to the named one.
- A walk to a soldier behind a wall waited 15 s with no route: the fairy took "within 15 m" as arrived,
  switched to showing it and threw away the route it was planning. While the character walks itself and has
  no route, the fairy keeps planning (and is put back to guiding if a line switched it to following).
- The old assistant (before a restart) kept a fight "on" through a death and logged that frozen moment 4 times
  a second for three hours (17 MB): a fight now ends after 20 s without a live game frame, and nothing is
  logged while out of the game.
- A rough pack simulation (scratch) reproduced the flee-forever loop; it is too harsh on long thrusts to tune
  damage with. 229 tests pass.
