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
