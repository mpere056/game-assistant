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

## 2026-10-12: the c4351 soldier, measured in a simulation built from the logs
- Twenty-fourth session (user): much better, but a hard time with the last enemy. It was a c4351 soldier (poise
  65, the 1003XXX moveset): 90 s, 9 swings, 3 hits taken, "timeout". From its log: it chains attack into attack
  (1003000->1003001, 1003006->1003008 six times of six, ...), and counters out of every flinch (8200 for 0.22 s,
  then 1003012, three of three).
- A duel simulation replays it from the log (scratch: duelsim.py; its moves, what follows each and when, the
  game's hit windows, learned lunges, its counter; the player's roll and swing timings, and no control of a
  roll before ~1.0 s, measured from the log). The merged fighter there: no kill in 90 s, ~6 hits landed, ~7
  taken, like the real fight. Now: 70% killed, ~11 landed, ~4 taken (60 fights).
- What changed: (1) a roll press in a roll's tail only comes out at ~0.95 s; when that is after the hit it isn't
  pressed (it rolled through the soldier's recovery: the opening flag was up while it rolled, twice). (2)
  Openings against middle- and high-poise enemies are counted to the next attack's hit, not its start (the
  soldier's next attack starts ~0.7 s after a hit, but hits 1.6 s after: "no opening" all fight). (3) Counters
  out of a flinch are learned (seeded: c4350's 1003012); against one, one swing, not a combo (the second swing
  was still going when the counter landed). (4) A long hit's roll covers its start (a running attack timed for
  its middle connected at once). (5) No flask within an enemy's longest learned lunge (4.5 m: hit at 6 m).
- Tried and dropped (worse in the simulation): holding still instead of closing in while the target winds up;
  more or less sideways in away rolls from lunges. 233 tests pass.

## 2026-10-12: Man-Bats, and where an attack leaves its attacker
- Twenty-fifth session (user): the c4351 soldier killed twice (43-44 s, 0 and 2 hits taken); but three Man-Bats
  (c4200): no hit landed, the user took over. Their log: a dive (1003011) from 6-9 m through the player at
  ~1.05 s, a glide on and a hover 2-4 m past it until ~2.5 s, then off to ~10 m (1003014) and back; close-ins
  (1003010) and a three-hit flurry (1003009) at ~2 m. The fighter: 3 swings in 50 s, each pressed 0.5 s into a
  roll, which comes out only at ~0.77 s (measured from the log: 0.5-0.8 s presses all came out at 0.74-0.80 s
  as rolling attacks, later ones at once) while the bat glided from 1.9 to 3.2 m: none landed. 35 rolls in
  50 s, some for bats 5 m off and leaving; its stamina ran out (25 samples recovering).
- A swing is now judged by where the target will be when its hit lands: the delay to come out of a roll plus
  the hit's 0.35 s, the target's speed away (half of it: a glide slows down).
- Where each attack leaves the attacker is learned (REST_AFTER s after its last hit, relative to the player's
  place and the line at its start; seeded: the bat's dive, ~2 m past the player). A roll landing within reach
  of that spot is preferred (rolling "through" a dive went toward where it came from, ~5 m from where it then
  hovered). A soldier's lunge leaves it in front: through rolls were already landing there.
- No lunge assumed for an attack not yet measured when its attacker is leaving fast, and a lunge is now
  measured toward where the player stood (a bat flying off counted as a 'lunge').
- A bat simulation from the log (scratch: batsim.py, shared player mechanics in fightsim.py).
- Kept per enemy (the user: a general strategy, plus each enemy's own data, and learning one enemy must not
  make others worse): everything learned is keyed by the enemy's base model (combos, attacks ending on their
  own, lunges, reaches, armoured attacks, counters, where attacks leave it); the general rules are the game's
  mechanics (rolls, swings, timings). Each change was checked on all three simulations (the c4351 duel, the
  bats, a pack of soldiers): the roll toward where an attack leaves its attacker helped the bats but raised the
  damage in the pack by a quarter (it outweighed landing next to another soldier), so it applies one on one
  only (no other enemy within 6 m). Simulations (means): duel 61% killed, 11 hits landed, 4.2 taken (merged
  #14: 0%, 6.5, 7.0); bats 6.0 landed, 1.8 of 3 killed, 8.5 taken (#14: 4.8, 1.3, 9.3); pack unchanged.
  236 tests pass.

## 2026-10-12: orders in parts at Agheel Lake North
- Twenty-sixth session (user, screenshot): combat much better; but several orders went wrong. From the chat and
  combat logs: (1) 'kill the bat and the soldier(s)': on the way a Kaiden Sellsword (the "soldier", on a horse)
  came at the player and was fought first (as it should), but its death counted as the bat's: "Now the next
  part!", and the next part, 'kill the soldier', found no "soldier" (the name doesn't say so; and it was down
  already), so the planner began a wander-search for Godrick Soldiers. (2) 'Wait, kill the bat actually.'
  stopped (a short order with 'wait'). (3) "Here we are: Man-Bat!" from the fairy while the character fought.
- Now: the enemy an order (part) names is remembered; a fight won against another one sends it back to that
  one ("Now back to the Man-Bat!") instead of on to the next part. A next part whose enemy isn't around is
  said and skipped, not searched for. A name that matches nobody around goes to the model with the characters
  around ('the soldier' -> Kaiden Sellsword) before giving up. A stop word in front of an order ('wait, ...',
  'no wait, ...', 'hold on, ...', 'cancel that and ...') is that order; 'actually', 'instead', 'first' and
  'now' aren't part of a name. The fairy says nothing on its own while the character fights.

## 2026-10-12: plurals and counts in orders; learned data checked against harm
- Twenty-seventh session (user): orders still misunderstood at times. From the logs: 'Please kill these
  soldiers.' killed one and stopped ('kill this soldier too' had to follow; 'kill those bats', 'kill these
  bats', '... and the soldiers' the same: only 'all/every/any/remaining' meant all); 'Actually, please kill just
  two more.' went to the planner as two fights with any enemy (the conversation it saw had no orders in it);
  and with 'kill two soldiers' my last change (unmatched words to the model) would have made it one fight.
- Now: a plural of the name found ('soldiers', 'bats'), 'them/those/these' mean all of them; a number ('two',
  '3', 'a couple of', 'both', 'two more': the kind just fought) means that many, counted as each one of that kind
  dies (two falling in one fight count two), then "That's all I was asked for!". Orders and Navi's answers go
  into the conversation the model and the planner see. All the orders in the chat logs replayed through the
  rules (scratch: replay_orders.py): plurals, counts, corrections and stop-then-order now read right; misheard
  names ('that boy', 'this poor', 'that guard') go to the model with the characters around.
- Combat: with a c4351 and a soldier both on the player the target changed 21 times in two minutes (the game's
  lock-on going back and forth, followed): another enemy the lock moves to is now taken only when it attacks,
  is clearly nearer (2 m), or the target isn't on the player.
- Learned data checked against harm (the user: learning about one enemy mustn't make it worse): the bat
  simulation recalibrated to what the last session measured (the dive ends 0.6 m short of the player's place,
  the close-in 1.85 m past it) showed the fighter worse with what it had learned about bats (1.5 of 3 killed)
  than with nothing (2.4). Dropping each learned table in turn: the "counters": a bat's fly-off out of a flinch
  (1006001, its hitbox far away) was taken for a counter, so it swung once instead of a combo. A counter now
  only counts if it reaches the player when it hits; the unchecked table is ignored. Bats now 2.5 of 3 killed,
  7.8 hits landed, 6 taken; the c4351 duel and the pack unchanged (no learned c4350 table hurts there).
  244 tests pass.

## 2026-10-12: Minecraft Java 26.3: the bridge mod and the adapter (phase 7)
- The user: make the assistant work for Minecraft Java 26.3. Choices (asked): the bridge inside this repository
  (`bridges/minecraft/`), build tools downloaded into the project, Fabric and the mod installed by me, talking
  and the fairy first.
- Fabric chosen: Fabric API has full 26.3 releases (0.162.0+26.3, 2026-10-06); NeoForge only betas. Versions
  from Fabric's example mod (26.3 branch): Loader 0.19.5, Loom 1.18-SNAPSHOT, Gradle 9.7.1, Java 25.
  Minecraft 26.x is no longer obfuscated: the mod uses the game's own names, checked with javap against the
  26.3 jars (several changed since 1.21: `Camera.forwardVector()/upVector()/leftVector()/getFov()`,
  `GameRenderer.mainCamera()`, `Gui.screen()`; the window and input are SDL now, not GLFW).
- Downloads (allowed): Temurin JDK 25.0.4.1 (141 MB zip) into `.local/tools/`; the Fabric example mod template;
  the Fabric installer 1.1.2 (sha1 checked) and Fabric API 0.162.0+26.3 (sha512 checked). The build fetched
  Gradle, Loom and Minecraft into `.local/gradle`. The installer added "fabric-loader-26.3" to the launcher
  (profiles backed up to `.local/tools/dl-fabric/`); Fabric API and `gabridge-0.1.0.jar` are in
  `%APPDATA%\.minecraft\mods`. Minecraft and its launcher were running meanwhile: the new installation shows
  after a restart.
- The bridge (Bridge.java, Snapshots.java, Rays.java, a mixin at the end of `Minecraft.runTick`): every frame
  while asked, a snapshot made on the game's thread; raycasts queued to the next frame; a local socket.
  Publishes nothing on a server. Builds clean (`Build-MinecraftBridge.bat`).
- The adapter: thread-local connections, a 10 ms snapshot cache, X mirrored both ways (Minecraft is
  right-handed; the core's "clockwise is right"), creature kinds keyed by a CRC of their id, the game's
  crosshair creature as the target and its block for "what is that?" (a new optional `crosshair_facts()`
  the look-at tool adds), Minecraft Wiki web search, speech vocabulary, Minecraft's controls (sprint Ctrl,
  jump Space) as the game's key defaults (the user's own changes still win).
- The core: the agent's instructions had Elden Ring-only parts (resistance fields, the offline wiki, Sites
  of Grace, example answers, picture hints); moved word for word to the Elden Ring adapter
  (`prompt_notes`, `prompt_examples`, `look_hints`); its prompt is the same apart from one line's place and
  "use the wiki for what it does" now "look up what it does". The fighter's learned data is per game
  (`.local/<game>/chains.json`). The adapter check's messages name the game.
- Tests: 6 for the adapter against a stand-in bridge (left/right, camera, window, creatures, crosshair,
  rays both ways, menus, servers, no bridge). Not yet run in the game.

## 2026-10-13: Minecraft live; places and walking
- First run in the game (user, screenshot): connected; snapshot 0.9 ms, two rays 4.5 ms; "what is that animal?"
  (panda, 13 blocks; ocelot) right; taming answered from the Minecraft Wiki; the speech bubble shows. "Where am
  I?" had no tool ("I can't read your exact location").
- Bridge: `world` in each snapshot (biome, the overworld clock's time of day and day, rain, thunder, light;
  26.x keeps time in world clocks: `Level.getOverworldClockTime()`); `names` and `locate` (structures, structure
  tags, biomes: the game's own findNearestMapStructure / findClosestBiome3d, run on the single-player server's
  thread); `grid` (one byte per block for walking).
- Adapter: where_am_i (dimension, biome, F3 coordinates, day, time, weather, light), locate (what players call
  places matched to the game's ids: tags first, then structures, then biomes; a few aliases such as "temple",
  "end portal"), route (A* over the grid, legs of up to 48 blocks planned on as the player walks; step-ups as
  jump actions). Capabilities: places and navmesh. The agent hides tools the game lacks (Elden Ring's kinds of
  place, items near me); "take me to the nearest X" uses the game's own search when it has no fixed kinds; F10
  "go to the nearest village" likewise.
- Tests: 7 for the pathfinder on built worlds (flat, a terrace jump, round a wall, a lava strip, a 3-block drop,
  a water channel, a ladder up a cliff, a far goal planned on). Not yet tried in the game.

## 2026-10-13: Minecraft walking, first try (user): stuck in a grove, fall damage
- "Take me to the nearest village" (1004 blocks north-west, a snowy grove on a mountain): walked 45 blocks
  down the slope, said "Jump across here!" at every step up, then stood at the end of its first leg, backed
  off blind into powder snow, and found no way on ("I can't find a way to walk toward Village"). The user took
  fall damage on the way down.
- Why (walk log, and the planner run on the live blocks): the grove's powder snow counted as deadly, so the
  planner was walled in (61 places reachable); walking off a 3-block drop carries the player 1-2 blocks on, and
  past a narrow ledge that was a 6-7 block fall (89.8 -> 82, 2.7 blocks in 0.17 s); backing off when stuck
  doesn't look where it goes.
- Fixes: powder snow is its own kind (the bridge), waded through at a cost (the game slows you 10% in it and
  boosts jumps; freezing starts after 7 s); a drop is only taken when the cells past its landing don't add up to
  more than 3 blocks; stands beside a deadly drop cost more; step-ups are silent 'step' actions; in Minecraft
  the walk walks (no sprint: farther past ledges, and food), never swings at what's ahead (breaks blocks), and
  sneaks while backing off (sneaking never walks off an edge). With powder snow wadeable, the planner gets out
  of that grove (310 places, a leg to the box's edge). Tests: 3 more (a narrow ledge over a long fall, powder
  snow, keeping off a cliff edge). Not yet tried in the game.

## 2026-10-13: Minecraft: breaking and building on the way (Baritone-style)
- The user: the walk "should be able to break blocks and place blocks where helpful, just like baritone would".
- Bridge: the grid also gives each block's hardness (127: bedrock, obsidian, lava...; +128: sand and gravel);
  the snapshot gives the hotbar, the selected slot, the first slot of throwaway blocks (dirt, cobblestone,
  stone, netherrack, planks...) and how many, and the slot that breaks the crosshair's block fastest.
- Planner (games/minecraft/blocks.py): more moves, each with its work: walk on, step up or step down breaking
  what's in the way (the top block first); dig straight down; pillar up (jump, place under the feet); bridge
  (place the missing floor against the block stood on). Never breaks next to water or lava, under sand or
  gravel, doors, or what can't be broken. Weighted A* (heuristic x1.5): a 60-block mountain crossing went from
  out of time (15k nodes, 1.5 s) to 0.7 s.
- Walk (core/autowalk.py): 'break' and 'place' route actions: stop where the work is, aim the camera at the
  block (turn and tilt: Intent.pitch, Turner.look), pick the fastest tool, hold the attack button while the
  block is under the crosshair (Keys holds mouse buttons now), done when it's gone; pillar: look down, jump,
  place at the top; bridge: sneak to the edge, turn round, place against the side of the block stood on.
  Snapshot.extra carries the crosshair's block and the hotbar for this. Gives up on one block after 15 s.
- Tests: 5 planner (a tunnel through a wall, never by water, pillaring a cliff, bridging a gap, digging down),
  2 walk (mining the block in the way then walking on; pillaring). Not yet tried in the game.

## 2026-10-13: Minecraft walking, second try (user): cut corners into a tree; Elden Ring words in an order
- "Please take me to some cobblestone": the order model offered "the nearest Site of Grace or a landmark".
  The order and plan prompts and the tools' descriptions named Elden Ring's places and characters. Now each
  adapter gives its `order_words` (and `place_kinds`); the core has neutral defaults; Minecraft's prompts and
  tools were checked to hold no Elden Ring words. walk_to_nearest is offered only to games with fixed kinds.
- "Take me to the nearest village": no breaking or placing ever started; it walked into an oak log, backed off
  blind, and gave up. The planned route went round the log (the planner run on the live blocks there): the
  walk's smooth pursuit (aiming 3.5 m ahead along the route) cut the corner. Built for Elden Ring's open
  ground, it doesn't fit a world of one-block obstacles.
- The user: build it into the assistant (not the Baritone mod), so what works here carries to other games;
  at least as good as Baritone.
- Walk: a precise mode (any game with a grid can turn it on: `walk_precise`): the route is every cell; the walk
  goes from cell middle to cell middle, turning first and walking only when facing the next cell within 25
  degrees; holds jump when the next cell is higher (steps, ladders, water); off the line to the next cell by
  1.4 or fallen below it, or stuck: re-plans at once from where it is (no blind backing off). A route
  extended while walking carries on from the player's cell. Breaking and placing start within 0.45 of the
  cell.
- Planner costs as Baritone reckons them, in game ticks (shown in blocks walked, 4.63 ticks each): breaking
  takes the time the game would with the best tool in the hotbar (the bridge works it out per block: tool
  speed / hardness / 30, or / 100 without the right tool), plus 2 ticks; a block placed 20 ticks.
- Places: kinds of block too ("take me to some cobblestone": stone or cobblestone; wood, iron, coal, diamond...;
  any block by name): the bridge's find_blocks searches the loaded world round the player (8 to 64 blocks
  out, 32 up and down), nearest first.
- Tests: 2 precise walk (round a corner without clipping it; a route extended mid-walk). Not yet tried in game.

## 2026-10-13: Minecraft walking, third try (user): "here we are" on dirt; the village walk stopped
- "Take me to some cobblestone": stone 1-2 blocks under the player; the walk began digging, then said "Here we
  are: Stone!" (within 3 blocks of the goal counted as there). The user: it must be in front of me; I need to
  see it.
- "Take me to the nearest village": walked well for 80 s, then stood still. A fresh route arrived, the walk
  took itself to be off it (planned from where the player stood a moment before) and asked for a new plan
  while the planning thread was still extending the old one; that thread then failed on the route it had just
  lost (None), and the "planning" flag stayed set: no plan ever again.
- Fixes: the planning thread always clears the flag (try/finally) and stops extending a route that's gone;
  companion.replan() drops a running plan; off the route, the walk first carries on from the route's cell
  nearest the player. Goals as Baritone's: a block (stand beside it, facing an open side of it, at the feet,
  the head, or just below the feet, then turn to look at it: a new 'look' route action), a column (a
  village or a biome: that far across at any height; far legs too), else near the point. Step by step, the
  walk says it's there only at the end of a complete route (else it waits there for the rest; 12 s with
  nothing: gives up), and the fairy leaves it to the walk.
- Live (read only): cobblestone 3 blocks down: dig 3, stand beside the stone, look at it; the village: a 45-cell
  first leg. Tests: 3 more (a block goal, a column goal, arriving and looking). Not yet tried in the game.

## 2026-10-13: Minecraft walking, fourth try (user): cobblestone and the village both reached
- The user: it brought me to cobblestone, then to a village, walking well. At the village the fairy said
  "It's right below us, about 33 metres down": a structure's /locate position has no height (the player's
  height when asking was used), and the fairy's arrival check compared heights. The user: a village is an
  area; anywhere in it counts.
- Places can be areas: the bridge's structure_box gives a structure's whole bounding box once its chunk is
  loaded (asked when within 192 blocks); adapter.goal_shape(point) tells the core what counts as there
  ({'area'}, {'column'}: height unknown, or the point); the fairy counts anywhere in the area (4 m round its
  edge) as arrived, and never "above/below" for an area or a column; the planner ends at the first block
  inside the area; the distance to a place with an area is to its nearest edge. "Here we are" was also said
  twice (by the walk, then by the fairy still guiding): the fairy stops guiding when the walk arrives.
- Tests: 1 (an area goal reached at its edge). Not yet tried in the game.

## 2026-10-13: orders: "some iron" went to the Iron Golem; gathering and crafting (built, not yet tried)
- "Take me to some iron": "There's a Iron Golem 27 metres away". A character whose name was only partly said
  ('iron' of Iron Golem) now loses to a place or kind of block that matches; then "Wait, stop, no, not the Iron
  Golem, but like Iron Blocks" was "Stopping." (only one stop word at the start was taken off; no "not X but
  Y"): several stop words in a row come off, and "not X, but Y" just after a walk order walks to Y, never to X.
  Kinds of block (iron ore or deepslate iron ore) go to the nearest without asking which. "a"/"an" and the
  game's distance unit (Minecraft: blocks) in Navi's line.
- Getting items, for any game with recipes and gathering (the user: it should mine and craft for me):
  core/crafting.py plans from the game's own data (a Book: recipes, what each block drops and the least tool
  for it, what's near) what to gather and craft, in order, choosing the cheapest way (the kind of wood that
  grows near; what's held first); tools kept, ingredients used up; one trip per kind of thing gathered (merged
  where the plan still replays). "A stone pickaxe from nothing": 3 logs, 12 planks, a crafting table (placed),
  sticks, a wooden pickaxe, 3 cobblestone, the stone pickaxe. core/tasks.py does it: walk to the nearest source
  (digging), mine it with the best tool, pick up the drop; open the inventory (2x2) or walk to a crafting table
  (3x3) and use it; click the ingredients into the grid (pick a stack up, one per cell with the right button,
  put the rest back), shift-click the result, close; place the table beside the player.
- Bridge: recipes (every shaped and shapeless crafting recipe, from the server's recipe manager), sources (each
  block's drops and least tool, from the game's loot tables tried with no tool, then each tool), the whole
  inventory, dropped items' ids, and the open container screen's slots (where each is on the window, what's in
  it, the grid and the result).
- Orders: "get me a stone pickaxe", "mine 10 cobblestone", "craft some sticks", "chop some trees" ("get me to
  the village" stays a walk). Tests: 4 planner, 3 tasks (filling a table's grid, gathering), 2 orders.

## 2026-10-13: crafting, first tries (user); orders thought through
- "Craft me some sticks": 8 sticks came, but Navi said "The stick didn't come out". Shift was pressed in the
  same instant as the click on the result, and the game took a plain click: one craft onto the cursor, which
  the inventory count doesn't hold; after 3 s it gave up and closed the screen, which put the cursor's sticks
  into the inventory. Same with the crafting table, which then landed in the main inventory, and "get me a
  stone pickaxe" couldn't place it ("not in your hotbar"). Also "Draft me some sticks" (speech recognition):
  the rules didn't know 'draft' and the fallback model had no crafting action ("I can't craft items").
- Fixes: Shift is held (as a key, through the frames) for 0.15 s before the click on the result; anything on
  the cursor is put down in an empty slot and counted; moving an item into the hotbar (open the inventory, pick
  it up, an empty hotbar slot or a swap) before placing it, and before mining with a tool that's only in the
  main inventory; 'draft' is 'craft'; the fallback model has get_item. A task log: runtime/task-<date>.jsonl.
- The user: give it generic orders and let it think through what's needed, not fixed steps. core/doer.py: a
  model (Haiku, medium effort, through the spending guard) gets the order and the state (holding, where,
  health, what's around) and acts one simple order at a time through the rules and skills ('get me a stone
  pickaxe', 'mine 10 cobblestone', 'take me to the village', 'kill the zombie'), each run in the game till done;
  it gets back what Navi said meanwhile and the new state, and decides the next; it can check where something
  is, search the game's wiki, wait, tell the player something; at most 30 actions; a new order or "stop" ends
  it. Used where the adapter says so (Minecraft: use_doer): for orders of several parts and orders the rules
  don't get. Elden Ring keeps its plan steps.
- Tests: 2 crafting screen (shift held first; a result on the cursor put down), 1 doer (acts, sees a failure
  and outcomes, finishes). Not yet tried in the game.

## 2026-10-13: crafting, second tries (user)
- "Craft me two sticks": 4 (a stick recipe makes 4; two can't be made). Navi now says so first ("Sticks come 4
  at a time, so I'll make 4").
- "Get me a stone pickaxe": placed the table (moved into the hotbar first: worked), mined 3 cobblestone, opened
  the table, 8 clicks to lay out the pickaxe, then no pickaxe in the result slot ("didn't come out"); the
  items went back to the inventory when the screen closed. The task log didn't say what the grid held. Now:
  0.4 s for a just-opened screen to fill in before the first click; after the last click the grid is checked
  against the recipe, and if it's wrong everything in it is shift-clicked back and it's laid out once more,
  twice as slowly; the clicks, the grid's slots (where on the window) and what they held go to the task log.
  Not yet tried in the game.

## 2026-10-13: crafting, third try (user): stuck picking up; cobblestone thrown away
- "Get me a stone pickaxe" mined one cobblestone, then walked to it over and over (0.03 s apart, 26 s): it lay
  in the hole one block below; the walk counted 1.6 blocks off as there, and the player never came within
  pick-up reach. Also 7 cobblestone lay 19 blocks away: the two earlier tries at the crafting table had
  clicked outside the window with the stack held, which throws it (and is why no pickaxe came out). The
  inventory's own clicks had landed right; the table's hadn't (cause not known from the log).
- Clicks are checked now (any game with a "slot under the pointer" readout can do the same): the pointer is
  moved onto the slot, the game is asked which slot is under it (the bridge: the screen's hovered slot, the
  pointer's position, the window's frame), and only then is it clicked; if it's another, the pointer's error is
  measured from where the game says it is, corrected for (kept for the next clicks), and it points again; after
  4 tries nothing is clicked and the task stops. Dropped items are walked onto (their own block: 'item' goals),
  and one walked to twice without coming is left.
- Tests: the pointer landing 7 and 12 pixels off is corrected before any click (the crafting done).
- Fourth try: "I couldn't get the pointer onto the crafting grid": the game still ran the bridge from before
  (no "hovered" in its answers: Minecraft hadn't been restarted). The user had seen the try before: sticks on
  the grid, but all 3 cobblestone in one cell. That fits the cause: each click was sent in the same instant as
  the pointer's move and the game took it where the pointer had been (three right-clicks into the first cell;
  a stack thrown when a click landed outside the window). Now, with a bridge that can't say what's under the
  pointer, each click waits 0.2 s after the move. The bridge has a version (ping); the adapter says when the
  game runs an older one ("Restart Minecraft to load the new one!").

## 2026-10-13: a stone pickaxe from nothing (user); fighting in Minecraft
- The user spawned with nothing but a crafting table nearby: "Give me a stone pickaxe" got wood, made planks,
  sticks and a wooden pickaxe, mined cobblestone and made the stone pickaxe ("That was great"). The order went
  through the doer ("Okay! Let me think..."), which gave it to the task and summed it up after.
- Fighting (core/melee.py, any game where a swing charges and is aimed with the camera; the adapter's
  fighter_style 'melee'): the best weapon into the hand (a sword, an axe, a tool, an empty hand: not a block);
  the camera on the target's body every frame; a swing only when charged (attack strength from the bridge),
  the crosshair on it and in reach (3 blocks); back off for a moment after each hit (twice as long from a
  creeper); zig-zag in on shooters (skeletons, pillagers...); a shield (off hand) up against a drawn bow or a
  hit coming while the swing charges; from a swelling creeper, run till 7 blocks off; a step up: jump; no
  nearer for 6 s: unreachable; over to the player at 30% health; a minute: timeout. The bridge: attack
  strength and delay, hurt time, on the ground, the off hand, blocking; each mob's aggression, swelling
  (creepers), item in use (a bow drawn). Bridge version 8.
- During a task, a hostile mob after the player (3.5 blocks, or 6 when just hurt) is fought off first, then the
  task goes on from its step's start.
- Tests: 4 in a simulated arena (a zombie killed with full swings only; running from a creeper; zig-zag and a
  shield against a skeleton; low health). Not yet tried in the game.

## 2026-10-13: first Minecraft fight (user): stopped mid-fight, sank to the bottom of the water
- Merged the Minecraft work (PR #17).
- "Kill a zombie for me": it closed in, hit with full swings and backed off, well; then "I can't reach that one!"
  and stopped, and the player sank. The fight log: "unreachable" counted from the nearest it had ever been
  (1.5 blocks, early on); backing off after each hit never got that near again, so after 6 s it gave up mid-
  fight. And its moves went wherever the keys pointed, into the water by the shore.
- Fixes: unreachable only after 8 s out of reach (and no nearer than ever): a hit-and-back-off fight goes on.
  The ground round the player is looked at four times a second (the adapter's terrain(): water, lava or a
  drop of more than 3 in each of the 8 cells around): a move that heads into one (within about 40 degrees:
  backing along the shore drifted in a little at a time) turns to the nearest safe way round, else stays; in
  water it swims up and makes for land. Walking routes weigh swimming more (4 times a block walked, was 2.5).
- Tests: 2 (a 20 s fight never given up; backing along a shore never into the water). Not yet tried in game.

## 2026-10-13: "kill a zombie" went for an Enderman; a hit taken; orders thought through further
- Merged the fighting fixes (PR #18).
- "Find and kill a zombie": none within 64 blocks; the doer gave "kill the zombie", the rules found none, and the
  command model they fall back on picked "the nearest thing like it": the Enderman. Now the doer's orders go to
  the rules only (what they can't find, the doer is told and thinks on), and the command model may never take
  another kind of creature instead. The doer's prompt: do exactly what was asked (not something near or alike);
  with no direct way, reason where it would be (where_is now finds creatures anywhere the server has loaded:
  the bridge's find_entities; the wiki for where things live or spawn), go there, explore ("explore 80 blocks
  north", a new order: any game whose adapter gives explore_point), or wait for the right time; it gets the
  recent conversation, so "no, a zombie specifically" narrows the last order.
- "It took a hit it shouldn't have": the fighter stood still while the swing charged, and the zombie walked into
  reach. Now where to stand is chosen each frame: each of the 8 ways and staying put is tried 0.45 s ahead (the
  player at a walk, every hostile one near coming at them); the way that keeps out of every reach (Minecraft's
  melee reach: 0.83 past both widths, plus margins) wins, and with the swing charged, one that brings the
  target to the edge of our reach (2.6 past its width). Never into water, lava, a drop or a wall (the adapter's
  terrain now marks walls), checked on the way the keys really go (8 ways round the camera, up to 22 degrees
  off), the ground looked at again on every new block. The nearest one after the player is fought first, then
  back to the one asked for (only aggressive ones: an Enderman minding its own business is left alone).
- The simulated arena's mobs now hit back (within 1.43 of their centre, once a second) and can all be hit and
  killed. Hits taken: one zombie 0, two 0, three around 0, two behind 0; surrounded by four or five: 1.
- Bridge version 9. Tests: the arena's (no hits taken; three around, two behind). Not yet tried in the game.

## 2026-10-13: a fight with three drowned (user): slow, one hit taken, gave up in the water
- It fought the drowned and the two that came over (the nearest-attacker rule worked), but: "way too
  defensive", it took a hit, and then "I can't reach that one!" and stopped, with the player in the water.
- The fight log: the whole fight was in water. The fighter kept out of water cells, and in water every way is
  water: it never moved (30 s "waiting" at the surface, the target on the lake floor), took the hit when one
  swam up to 0.7 blocks, and gave up as unreachable, leaving the player among them.
- Fixes: in water, water isn't kept out of (lava, drops and walls still are), and every way onto land counts
  more: out of the water first, then fight from the shore; never given up as unreachable while in water. Faster
  kills: out of reach by just a little (0.5 past the margins, was 1.5: it walked back in each time); stepping in
  from 75% charged (arriving as it's ready); critical hits (a jump at 85% charged, the hit on the way down:
  half as much again) when nobody else hostile is within 4 blocks and not at shooters or creepers.
- The arena: jumps and critical hits. One zombie: 2.4 s (was 4.2), 3 hits (2 critical), none taken; three
  around: 7.6 s (was 11.6), none taken; surrounded by four or five: 1 taken. Tests: in water it makes for land
  and never gives up there. Not yet tried in the game.

## 2026-10-13: "kill a zombie" from the surface (user): it worked, but slowly and with idle spells
- The doer found zombies 46 blocks down (where_is: anywhere loaded), walked there and killed one. The walk took
  455 s: 330 s mining stone by hand (the player held only dirt: 7.5 s a block, about 1 s with a wooden
  pickaxe), 84 s "building", of which 60 s were four placements failing for 15 s each (the user: "just jumping
  every now and then and not doing anything").
- The failed placements were pillars: a jump needs two blocks of room above the head and the planner cleared
  one, so the head hit the ceiling and the block never fitted under the feet. Now two are cleared; a placement
  gives up after 5 s (was 15), and the walk plans again at once, never placing in that cell for 2 minutes.
- Efficiency: the bridge's dig_estimate (the straight way there: blocks in it, seconds with what's held and
  with each pickaxe, a shovel, an axe), given with where_is; the doer is told to weigh getting a tool first
  ("a pickaxe before digging down through stone") and do what's quickest in all.
- Telling the player: each step the doer takes has a short line said as it starts (what and why); thinking for
  more than 5 s: "Working out how to do that..." / "Still thinking this through..."; every 25 s of a long step,
  how it's going ("Still digging to the Zombie: 20 blocks across, 30 down"; the task's step; the fight).
- Bridge version 10. Tests: the doer's line before its step. Not yet tried in the game.

## 2026-10-13: "kill five zombies or skeletons" stopped at once; digging without a pickaxe again
- The order went to the doer, which said "I've got nothing to fight with, so I'm making a wooden sword first!"
  and the task began; then "Sorry, I got stuck working that out (AttributeError)": the new progress line read a
  craft step's recipe as a name. Fixed.
- "Kill three zombies": where_is answered "There's a Zombie 16 blocks away" (the nearest across: it was 42 down
  in a cave) and it dug down after it. Choosing the nearest character, height now counts three times over; the
  line says how far up or down; where_is gives the doer the height and the dig (dig_estimate) for one in sight too.
- The user: it shouldn't break cobblestone without a pickaxe unless a pickaxe isn't easy to make; the quickest
  way in all. When a route has 3 or more blocks slower than 3 s to break, the adapter weighs a tool once per
  goal (tool_advice): the straight dig there with what's held against each tool (dig_estimate) plus the time to
  make it (core/crafting.py's plan for it: ~5 s an item gathered, a walk per kind, ~4 s a craft); worth 20 s or
  more in all: the walk stops, a task makes the tool ("That's a lot of stone to dig through! Making a wooden
  pickaxe first: about 250 seconds quicker."), then the walk goes on.
- Tests: tool advice (a pickaxe for a long dig through stone; not for a short one, one held, or few hard blocks).
  Not yet tried in the game.

## 2026-10-13: merged PR #19; water, stop-start digging, and digging chosen too readily (user)
- Committing PR #19 swept in docs/LAUNCH.md (a private plan, not mine: `git add -A`); it was taken out of the
  commit and the branch force-pushed before merging. Only files changed are staged by name from now on.
- Stop-start digging: each block took ~1.5 s to mine (a wooden pickaxe: right), then ~2.5 s aiming at a block
  already gone (the route planned again while digging still listed the blocks just dug: "never in sight" after
  1.5 s). The walk now asks whether a block is still there as work on it starts (the adapter's block_solid) and
  goes straight on past one that's gone.
- Water: routes could swim through deep water (any water block was a place to be); swimming is at the surface
  now (the head out of the water), and the walk holds jump when the player sinks below the route.
- Digging chosen too readily ("at night it could find more zombies more efficiently than digging so far down"):
  where_is gives the doer up to 4 of each (in sight and anywhere loaded) with about how long getting to each
  takes (walking, plus digging straight there with what's held), and the doer is told to take the quickest and
  weigh other ways (waiting or looking round on its own level at night for what spawns in the dark).
- Six lines joined by a lost backslash-newline (shell escaping) put right; a test now catches them.
- Tests: a block already dug is skipped at once. Not yet tried in the game.

## 2026-10-13: "ride this water down into the cave and drop safely into a pool" (user)
- Water navigation worked. The order above: the doer answered it couldn't see the cave's depth or whether there
  was a pool at the bottom. The user: it should at least try (go to the edge and look; game data); not hard-coded.
- The doer can survey now (the adapter's survey(), from the game's blocks, in the dark too): in each of the 8
  directions where the ground drops away, how far out and down, and what's at the bottom (water and how deep,
  ground, lava), walls, and the pools of water below with coordinates; and give coordinate orders ("go to 12 40
  -30": the game's own, as where_is and survey give them). It's told to find out before saying something can't
  be done. (A screenshot tool was tried and taken out again: the user prefers surveying, for now.)
- The walk takes a fall of any height into water (Minecraft's: no harm), when the water reaches a block past the
  landing too (walking off an edge carries the player on); onto ground still 3 at most.
- Tests: a 19-block drop into a pool taken (onto ground not); the survey finds a drop and the pool at its bottom.
  Not yet tried in the game.

## 2026-10-13: the cave drop worked; then the player died fighting a zombie (user)
- "Ride the water down into the cave and drop safely": surveyed, found the pool 21 down, went to it, no harm.
- "Kill five zombies or skeletons": the fighter stood 16 s "waiting" 5.2 blocks from a zombie that stood still
  (no straight way to it: the fighter has no routes) and gave it up twice; the doer then said "take me to the
  nearest zombie", and the walk went right up to it (into its reach: health 10 to 4); the fighter handed over at
  low health ("heal up!"), the doer ordered the fight again at 4 health, it handed over again at once, and with
  no one acting the zombie killed the player.
- Fixes: on low health the fighter gets away first (the way farthest from every hostile one near, sprinting,
  never into water, lava, a drop or a wall) till none is within 12 or 8 s, then hands over. With no straight way
  in, it plans a route (adapter.route, on a thread; again as the target moves) and follows it with the camera on
  the target (none that needs digging or building). Stepping in charged is judged a short step on (a whole step
  overshot the edge of reach and it waited half a block out of it). Walking up to a hostile one stops out of its
  reach (4.5). The doer: the player alive before all else (no fights on low health or outnumbered; run away, eat,
  keep clear of creepers first). New orders: "run away" (the way from the hostile ones near), "eat something"
  (the best food in the hotbar), "take me to the surface" (the open sky: the bridge's heights; the planner takes
  any way up, out of a cave or dug straight up, whichever is quicker).
- The arena: none taken with one to four zombies (four round the player: 0, was 1). Bridge version 11. Tests:
  retreating then handing over; a route round a wall; to the surface by the way out; running away. Not yet tried
  in the game.

## 2026-10-13: "kill that spider" found no way (user): the sea under a cliff; parkour
- The user, on a cliff 14 above the sea with the spider across it: "Hmm, I can't find a way". The planner's box
  went 12 up and down from the player: it never saw the sea. Now the box reaches the lowest ground (or water) and
  the highest between here and there (the bridge's heights): the live route was found at once (36 blocks: a
  14-block drop into the sea, a swim, up the far slope).
- The user: it should know it can jump into water safely, and how running and jumping work. Parkour in the
  planner (straight on, the 4 ways): a walking jump over a 1-block gap, a sprinting one over 2 (or over 1 and up
  1), a sprinting one from a run-up block over 3; landing level or 1 lower, with room for the arc; dearer when
  missing it would hurt (a deep drop, lava). The walk does it: back to the run-up block first for 3; face the
  landing squarely; walk or sprint at the edge; jump 0.32 past the middle of the block (the edge is at 0.5); keep
  going till landed, then on along the route from there; missed and fallen: plan again. Minecraft's notes for
  the models say what the walk takes into account (jumps, falls into water, swimming, mining with a pickaxe made
  first, pillars, bridges), so "go there" is one order however rough the way.
- Tests: jumps of 1, 2 (sprint) and 3 (sprint, run-up), none over 4; up 1 over 1; a simulated sprint jump over
  a 2-block gap (jumped just before the edge, landed, arrived). Not yet tried in the game.

## 2026-10-13: a zombie in the water (user): the fighter waited on the shore
- Merged parkour (PR #21).
- The fight went well on land; when the zombie went into the water, the fighter planned a route to it (through
  the water) and then held still: the footing check barred every step into water ("routing (held: hazard)", 40 s).
  The user: land is preferred, but not when it's much slower; commands done as efficiently as they can be.
- Water is now a cost in fights, not a bar (lava, drops and walls still are): a step into it costs 1, halved
  for every second spent ready to hit with nothing in reach (waiting on the shore is the slow way); a planned
  route through water is followed. Walking routes weigh swimming at its time (2.2 a block, was 4: half pace).
- Tests: in after one that stays in the water (hits, never unreachable); land fights unchanged (none taken with
  one to four zombies). Not yet tried in the game.

## 2026-10-13: "take me to the top of one of these trees" (user); keeping safe on every order
- The doer chose the top of a trunk (-7 68 -13, 7 up); the walk found no way (no blocks held to pillar with) but
  never gave up: it planned the same few blocks again and again, and the doer, waiting on it, said "Still on the
  way: 4 blocks across, 7 up" for minutes. The user: get dirt to build up with, or another way; think it through.
- A walk getting no nearer its goal (nor digging, building or jumping) for 20 s gives up. When a route finds no
  way and few blocks are held, the planner tries again as if 64 were held: if that works, Navi says how many it
  would take ("I'd need about 7 blocks to build my way there"), which the doer hears. A walk from a simple order
  (the rules') that finds no way goes to the doer to think through. The doer is told the ways round a walk with no
  way: blocks to pillar or bridge with (mine some dirt first; the walk uses them itself), a tool, a way round.
  The same progress line isn't said twice running.
- The user: by default, keep safe from being attacked. On any walk (an order's, a task's, the doer's), a hostile
  one after the player within 6 blocks (or nearer when the player was just hurt) is fought off first by the
  fighter (out of its reach), then the walk goes on (the adapter's defend_on_walks; Minecraft's on).
- The user asked for fuller logs: runtime/doer-<date>.jsonl, written as it happens (each turn's thinking text and
  calls, each result); the walk log's rows carry the planner's route_note (the goal kind, the box, there / part
  / no way, nodes searched, blocks held, blocks it would take).
- Tests pass. Not yet tried in the game.

## 2026-10-13: "take me up the tallest nearest tree ... put me on top of it" (user): stuck in a loop
- The doer meant well ("No way up yet and nothing in my hands, so I'll chop some birch logs for blocks to build up
  with!") but the task chopping logs got 2 and then walked for 20 s at each higher log, three times ("no way":
  above the head, no block to stand on), and logs weren't blocks to build with for the walk anyway. The user: place
  blocks under the character, craft the logs into planks for more, walk up a hill and build across, parkour...
  (the new doer and walk logs showed all of it).
- Mining: a block to mine is reached from anywhere within reach ('mine' goals): beside it from one below to three
  above the feet, or under it in its own column 2 to 4 up (a trunk mined from the bottom), the line from the eyes
  clear. Building blocks: dirt and stone first, then planks, then logs (the bridge: block_slot and the count;
  version 12). A route with no way at all gives up at once (not 20 s of the same). Coordinate orders are exact
  goals (that block, or one beside it, at that height: on the top, not 2 below in the leaves). The survey lists the
  trees near (each one's foot, height and where to stand on its top), so "the tallest nearest tree" is a lookup;
  the doer knows logs make planks, four each, to build with.
- Live: the survey listed five trees (heights 3 to 7). Tests: mining within reach (beside and from under a trunk;
  not past a log in the way, nor 5 up); up onto a tree top through its leaves with 8 blocks (pillared; none
  held: no way). Not yet tried in the game.

## 2026-10-13: up the tallest tree (user: it got there, then trouble); from the new logs
- It reached the top of a 7-high birch (pillared with planks it had crafted, broke through the leaves). Then:
  "the tallest tree within 50 blocks" was answered "you're already on the tallest": the survey's trees came from a
  block search that stops at the first ring with anything in it (8 blocks out). Now find_blocks can search a whole
  radius (bridge version 13); the survey lists the trees within trees_within (the doer chooses, up to 64), the
  nearest and the tallest.
- Going back to the birch with no blocks left: the walk found no way and stopped, but the doer read the outcome the
  moment the walk ended and missed "I can't get through here" and the blocks it would take; it waited 30 s and
  tried the same again. The doer now gives a step 0.8 s after it ends for what's said about it.
- "Chop 3 logs" (the old code, before mining within reach) ran 6 minutes and timed out while the player was in
  another window; "I couldn't open the crafting screen" for planks, the same (the game out of focus: no E reaches
  it). A task's clock now stops while the game isn't in front or is paused.
- Live: 12 trees round the player, the tallest 10 high (the old bridge still). Tests pass. Not yet tried in game.

## 2026-10-13: "the top of the tallest tree within 50 blocks" (user): no way up, a crash, the same tries again
- The doer found the tallest (an 11-high oak) and sent the walk to its top; with no blocks held the walk found no
  way, and the doer tried the same twice more ("I can't get through here"), then a 10-high one. One try had 2
  blocks (from leaves broken): the planner made a pillar of more than 2 (it doesn't count blocks as it places them)
  and ran out half way up. Then "Auto-walk error: IndexError": work that failed cleared the route to plan again,
  and the walk read it in the same frame.
- Fixes: a cleared route is never read (no crash). A plan placing more blocks than are held isn't followed: the
  walk stops at once and says how many it takes. And then it gets them itself, as with a tool: the quickest of
  dirt dug near, planks from logs held or chopped, cobblestone (core/crafting.py plans each; live, with nothing
  held: 4 logs into 16 planks, about 40 s, against 14 dirt, about 85 s), 4 over, then on with the walk ("I need
  about 10 blocks to build my way there: getting oak planks first!"); once per goal. The check "with blocks, would
  there be a way" gets 1.5 s (0.5 found nothing for an 11-high climb).
- Two more lines joined by the shell (the test caught them) put right. Tests pass. Not yet tried in the game.

## 2026-10-13: up a tree "much more efficiently" (user): it tunnelled across the canopy instead of climbing
- The log: on a hill beside the tree at y104, the walk broke its way straight across through the leaves, two blocks
  at a time (about 18, two of them logs at 3.5 s each by hand), placed one block, tunnelled on at y105, then dug 4
  down to the target: the trunk's top, inside the leaves, 2 below the top of the tree.
- Why: in the planner, a step straight on breaking what's in the way ended the moves for that side, so stepping up
  one (break ahead-up and over the head, jump) was never weighed wherever a tunnel straight on was possible. Now
  all of them are, and the cheapest wins. Breaking costs more too (8 ticks a block over its time: about 0.7 s a
  leaf in the log, most of it aiming and the next step), and the survey's "stand on top at" for a tree is the
  top of its leaves, not of its trunk.
- Tests: a canopy 3 deep reached from the ground in 3 breaks up a staircase (was 8 across at the bottom, and not
  there); out from inside leaves 6 deep, a staircase turning as it needs (no blocks) or partly pillared (blocks),
  always up. Tests pass. Not yet tried in the game.

## 2026-10-13: "take me to the ground, then walk 50 blocks on top of the nearest hills" (user): wrong on both
- On a tree top, "the ground" became "the surface" (the open sky over the head), which a tree top has: "Here we
  are: the surface!", "You're already on the ground". And "the nearest hills" were looked up as a biome by name:
  the Windswept Hills, 1,946 blocks off, with hills (unnamed) beside the player.
- The ground: the bridge's heights (version 14) can give the land's own height, under the trees ("ground": logs,
  leaves, big mushrooms, bamboo and anything with nothing to stand on passed over, down to the first other
  block or water). "The ground" ("get me down", "off this tree") is a goal of its own: on the land anywhere
  round the player (a slab or path block on it counts; a stump doesn't), the quickest way down (or up out of a
  cave). Through an oak's leaves: 3 quick breaks and a 3-block drop; where the lowest leaves are too high to
  drop from safely, down the trunk instead. The state the doer reads says where the player stands: on the
  ground, n blocks above it (up a tree or on something built), or n below it (underground).
- The land (core/terrain.py, for any game that can give the ground's height): from a height map round the
  player (96 blocks; the doer can ask up to 120), the hills: tops that are the highest for about 28 blocks
  round and rise 6 or more above the land round them; each one's top, how much it rises, the nearest point on
  its top, and a line along the top (from the top each way, a step at a time to the highest ground ahead,
  while in the upper half of the hill), points about 8 blocks apart to 'go to' in turn; one ridge is one hill.
  And the highest ground each way. In the survey as "land". Live (sky heights, the old bridge): a hill 33 high
  east, its top's nearest point 27 blocks off, 112 blocks along; read and worked out in 0.03 s.
- The doer is told: biomes are named regions, often far; the land near, named or not, is in the survey's land
  ("the nearest hills": the nearest high ground there, unless a region is named). And when to ask first (the
  user liked "it's night with zombies about: head there anyway, or wait until morning?"): only when doing it
  brings a risk or cost the player may not expect; otherwise just do it.
- Tests: the terrain (flat land: none; a ridge: one hill, a line along it end to end; the nearer first; each
  way; unknown columns), down from a tree top to the ground (and there at once when on it), the survey's land.
  Tests pass. The bridge builds; installing it needs Minecraft closed (Build-MinecraftBridge.bat).

## 2026-10-13: two creepers hurt the player, then a spider killed them while nothing fought it (user)
- The combat log: the walk (exploring) stopped for a creeper coming at the player and fought it with a fist (an
  oak log held: one hit landed in 18 s, of 20 health). Lit, it was run from ('back', 'sprint', the camera on it);
  then a spider came nearer and became the target, and the 'fleeing' switch stayed on: 'back' was now away
  from the spider, with the camera turned to it, not away from the creeper. The player went on walking backwards
  from the spider, 0.7 blocks off, never swinging, till they died (health and the creepers near weren't logged).
- Creepers now (core/melee.py), whichever one is fought: a lit one (its swell: the fuse burns 1.5 s from the
  player coming within 3) within 7.6 is got away from before anything else, the way that keeps clear of every
  lit one (its blast hurts nothing past 6; past 7 it goes back down), not into lava, a drop or a wall, not near
  an unlit one. With a weapon that kills one in a few hits (a sword or an axe: the adapter's weapon_damage), hit
  when it's calm, then away till it has gone back down; with less (a fist, 1): lit on purpose (within 2.6) and
  held 6.4-6.8 off till it blows up, harmlessly. An unlit one is kept 3.8 off unless it's being hit.
- When the one fought is gone (blown up, killed) and another is after the player within 8, it's fought next
  ('next_target'), not stood still. Low on health, the fighter gets away only from what can be outrun: a spider,
  cave spider, wolf, enderman, baby zombie and the like ('fast' in the adapter's profile; babies from the bridge,
  version 15) are fought on; after getting away, one still on the player is fought, not handed over.
- The combat log has the player's health and the hostile ones within 12 (kind, distance, swell).
- Tests, by the game's rules (a Field: creepers light within 3, stand still while lit, go back down past 7, blow
  up at 30 ticks, the blast as the game reckons it; spiders at 5 blocks a second): a fist lets a creeper blow up
  hurting nothing; a sword kills one, never blown up; the case here (a lit creeper beside the player, another
  near, a spider coming, a fist): no blast damage, the spider fought and killed; a spider on low health fought,
  not backed off from. Two more lines joined by the shell, and one in the tests, put right; the test for them
  now catches more kinds. Tests pass. The bridge is built and installed (restart Minecraft). Not tried in game.

## 2026-10-13: "still a bunch of issues" (user): the logs of a stone pickaxe from nothing (158 s) and a walk order
- The pickaxe took 158 s, about 75 of them lost. Back to the crafting table from the pit dug for stone (5 deep, 2
  wide), the walk went round the same cycle for a minute: two breaks, a jump at a wall, plan again, the same two
  breaks. The first break of each never had its block on the crosshair (no attack in the log): aimed at the
  block's middle, it was hidden behind another, and after 1.5 s 'never in sight' was taken as already broken. And
  the first log of the tree, beside the player at their feet: its middle was behind the log above it, seen from
  the eye; skipped after 2 s as "could not mine it" (a log left floating).
- Now, breaking for a route and mining for a task aim at a point on the block that's in sight: the adapter's
  aim_point casts rays (the bridge's, through the game's own shapes) at 5 points on each face turned toward the
  eye, within reach, and takes the nearest that the ray reaches first; asked as the work starts and when the eye
  has moved. Hidden altogether: the block in front is broken first (never one below the feet). Never in sight and
  still there (asked of the game): failed work, planned again, not walked into as if gone.
- "Walk around 50 meters around this hill": the doer's "walk a loop of about 50 blocks round the base of this
  hill" wasn't understood and it explored 50 north instead. The survey's land now says where the player stands:
  how much the spot rises above the land round it, and on a rise, its top and a loop round its foot (from the
  top out 16 ways to a quarter of the way up, in order); each hill has its loop too. The doer is told a way
  through several points is one order ("go to x y z, then go to x y z, ...": it splits into one after another).
- Tests: the walk breaks the block in front of a hidden one first, then it; one never seen but still there is
  failed work and planned again; a log under another is aimed at on its near side, and a hidden one gives the
  block in front; the rise stood on, with a loop round its foot. Tests pass. Not yet tried in the game (it was
  closed); no new bridge needed.

## 2026-10-13: "Make me a stone pickaxe" (user): "I can't find a crafting table" with one in the hotbar
- In a flower forest: the logs, planks and a table crafted, then the table to put down: 0.2 s later the step was
  taken as done and the sticks crafted; the pickaxe then found no table (it was still held). The spot chosen had a
  flower in it (no collision: open to the grid), and "it's there" was the crosshair on that spot, which the
  flower filled.
- Now it's there when one fewer is held, or the crosshair is on it in that spot by its id (the snapshot gives the
  aimed block's id: aim_id); a plant in the spot (the crosshair on it there, something else) is knocked out first.
- Test: a flower in the spot is knocked out and the table really put down. Tests pass. Not yet tried in game.

## 2026-10-13: a stone pickaxe made, with "a bunch of issues" (user): a table it couldn't reach, mined and put back, nowhere to put it
- The log: the table was put down by the tree; the stone dug 5 down beside it; then "craft stone pickaxe" opened at
  once, the table within 4.3 straight-line, but behind the pit's wall: 9 s, "I couldn't open the crafting screen",
  twice. The doer then ordered "craft a crafting table": the plan's cheapest way was to mine the one standing there.
  Then "I can't find a spot to put the crafting table" twice on open plains (stood in a dip where dirt had been dug,
  every cell round the feet ground; animals about), till it walked 6 blocks on. "Place a crafting table" wasn't an
  order it knew.
- Reach (root): "can reach and use (or mine) a block from here" is now one thing everywhere, as the game reckons it:
  within 4.5 of the eye to a point on a face turned toward it (its middle or near a corner), nothing in between but
  what the body can be in (blocks.sees). The planner's goal for mining and using is that (its old rules, beside it from
  1 below to 3 above, were wrong both ways: a block 5 up beside the player is in reach; a table behind a wall wasn't
  out of it); a station is used where it stands only when a point on it is in sight from the eye (the adapter's rays),
  else walked to where it is (in reach, in sight); aimed at that point; and if that fails it says where the table is
  and why.
- Built things (root): a thing that has a recipe is made, not taken off the world where the block is the thing itself
  (a table, planks, a furnace: someone's build); what the land gives (logs, stone, ores) is gathered.
- Putting down (root): any open cell within reach (not the player's body, not where a creature stands: their cells
  from their size), beside a solid block whose face toward it the eye can see (by the game's rays); on the ground near
  the feet first. A new order puts a held block down ("place a crafting table").
- The doer is told: use what's there (gone to and used, never made again or taken apart).
- Tests: the reach rule both ways (in sight 3 and 5 up; 7 up out of reach; a wall between), a table up beside a pit
  climbed to; built things not taken apart; a spot found from a dip with a sheep beside; "place a crafting table".
  Tests pass. Not yet tried in game (the world was closed).

## 2026-10-13: "still getting stuck with placing the crafting table" (user, a grassy hill): four times 10 s, nothing clicked
- The task log: each try 'aim', then 10 s, "I couldn't place the crafting table"; never one click. The spot was chosen
  with rays through the blocks' collision shapes, which grass hasn't got: "in sight". But the crosshair stops on
  the blocks' outlines, and grass has one: aimed at the ground's top, it was on grass (crouched, the eye 0.35 lower,
  the line down to the spot passes through the grass in front), neither the block below nor the spot, and nothing
  was ever clicked.
- Root: what's "in sight" is now what the crosshair would be on. The bridge's rays (version 16) can go through the
  outlines (as the crosshair) and say what they stop on: its cell, whether a block put there takes its place
  (replaceable: grass) and whether it's solid; the snapshot says the same of what the crosshair is on. Aiming at a
  block to break or mine it and choosing a spot to put one down use those rays; the spot from the eye as it will be
  (crouched, 1.27).
- Putting down goes by what the crosshair is on, as the game does: the block below's face, put it there; grass in
  the spot itself, put over it (it takes the grass's place, no need to knock it out); a plant in the way, knocked out
  with a click. A spot it can't put it in within 5 s (a creature stepped in) is left for another, three in all.
- Tests: a flower in the spot; grass in the way; grass in the spot put over; a spot in grass all round, the crosshair
  landing on its grass or face. Tests pass. The bridge is built and installed (restart Minecraft). Not tried in game.

## 2026-10-13: "doesn't realize how to reach the crafting table from certain angles"; idle while mining down (user)
- The table: in a jungle, the stone dug down beside it, the last cobblestone mined from a hole 2 deep; the walk to the
  table arrived at once (the planner: in reach and in sight) and the game's rays said not in sight: failed. The
  planner looked at points 0.15 in from a face's edges, from the cell's middle: the line to the table's top corner
  cleared the grass block's corner by 0.03; from where the player stood (0.06 off the middle) it didn't.
- Mining down (25 s for 5 blocks): 3 s standing before the first break and 10 s at the bottom. The planner ran out
  of time (8192 nodes, 1.5 s) each time: from the bottom of the shaft it planned, the eye was "in" the dirt the
  plan itself dug (the grid as read before), so the stone was never in sight, and the search went on across the
  whole surface. Its best part-way route ended in the stone itself: dug through, then waiting at the end of a route
  for the rest, planned slowly, and "could not mine it" (gone).
- Root fixes: in sight from a cell counts its own two cells as open (the body is there); the points looked at are
  0.3 in from the edges (a line only grazing a corner isn't one); the line walked cell by cell (a voxel walk, each
  cell once) and each cell's answer kept for the plan; the block to mine or use is never broken on the way; toward a
  block in the ground each block up or down counts as three blocks' walk in the search's estimate (it's dug). The
  live jungle ground: down to stones 6-10 below in 0.1-0.6 s (were: not found in 1.6 s).
- And if the table still isn't in sight where the walk ends, it's walked to right beside it before saying why.
- Tests: down to a stone quickly, never through it; a line that only grazes a block isn't in sight (out of the hole
  first). Tests pass. Not yet tried in game.

## 2026-10-13: "get me an iron pickaxe" (user, a snowy grove): no way to plan it; stuck freezing in powder snow
- The doer: "I can't find a way to get iron pickaxe near here" (twice): iron ingots are smelted in a furnace, and
  the planner knew crafting and gathering only. It went on to "mine 3 iron ore": one mined, then the walk stood in
  powder snow (the planner had it as passable: wading, a little slower) going forward and jumping for 30 s, four
  routes planned and given up, health 20 to 12.5 (freezing). Then "I can't find any more iron ore near here". And
  where_is("iron_ore") was "no block called iron_ore" (the game's own id).
- Smelting: the bridge (version 17) gives the furnace's recipes (one in, one out, the ticks to cook it), every fuel
  and how long it burns (the game's own furnace asked, item by item: in 26.3 it depends on the item), and the
  furnace screen's slots (input, fuel, result) and whether it's lit. The planner (core/crafting.py) has stations by
  item (a crafting table for the 3x3 grid, a furnace for what's cooked; each asked once whether one's near) and
  smelt steps with their fuel: what's held first; else the cheapest of what's held, what the land gives near (logs,
  coal) and what's made from what's held (planks) (by burn time alone it only ever tried lava, coal blocks and
  boats). An iron pickaxe holding a stone one: 8 cobblestone, a furnace crafted and put down, 3 raw iron, 2 logs,
  smelt, craft. The task (core/tasks.py): to the furnace (in reach and in sight, as the crafting table), open it,
  anything else in it out, exactly so many in and so much fuel, then wait, taking what's done out as it comes, till
  there's enough; out of fuel or too long: says so. Getting to a station is one way for both now.
- Powder snow: not passable and not a soft landing any more: never walked, stood or fallen into; broken to get
  through; stood in already, the way out is planned from there. The survey names it.
- Names: the game's ids are understood (iron_ore, minecraft:iron_ore) for places and items.
- Tests: an iron pickaxe plan (smelting, fuel held first); a furnace worked by the game's rules (exactly 3 in, 2 logs,
  3 ingots out); never into powder snow (a jump over it), out of it when sunk in it. Tests pass. The bridge is built
  and installed (restart Minecraft). Not tried in the game.

## 2026-10-15: "repeating things too much, way too many responses" (user): eight lines for "get me down from here"
- "Okay! Let me think...", the doer's say for its first act, the walk's own "Let's go to the surface!" and "Here we
  are: the surface!", the doer's say for its second act, "On my way!", "Here we are: 2 68 -5!", and a two-sentence
  finish. And the doer had sent it to the surface (the open sky: a tree top is on it) for "get me down", so a second
  walk was needed.
- While the doer runs an order, only its own lines are shown and spoken (its says, progress, the finish) and urgent
  ones (an enemy coming, low health, the game out of focus); each action's own lines are kept for the doer to read,
  not shown. No "Okay! Let me think...": the doer's first line comes in a moment, or "Working out how to do that..."
  after 3 s. The doer is told: a say only when it's news, the finish one short sentence (a danger only if near and it
  matters); and the orders: the ground (down from a tree, a roof: "get me down") and the surface (up out of a cave).
- Test: while the doer runs, an action's line is kept but not shown, the doer's and an urgent one are. Tests pass.

## 2026-10-15: an iron pickaxe made (user), with a lot of time lost on the way
- "I don't have what the furnace takes" after 8 cobblestone were mined: dug 8 straight down for them, then back up to
  the crafting table the walk pillared out of the hole with the cobblestone (6 placed). The doer had them mined again
  (the pillar: "Cobblestone at (19, 65, -5)"...).
- Raw iron: 47 s for 2, then "I can't find any more iron ore near here": two ores beside the one just mined were left
  as "could not mine it" (walks ended in the planner's reach, 4.5 from the eye; the aim's was 4.4: no point found,
  the crosshair never on them, skipped after 2 s). The doer's "mine the iron ore at 12 56 -5" wasn't an order; its
  "go to 12 56 -5" (the ore's own cell, an 'exact' goal: stood in, dug toward) went 54 s and failed.
- What the task's steps to come use up (ingredients, the fuel, a station to put down) is reserved: the walk builds only
  with what's over (the adapter now picks the block to build with, from the hotbar, past the reserve).
- Reach: the game's 4.5 for aiming; walks to mine or use a block end at 4.1 (a little nearer: the aim has room).
- Mining: a block not to be reached from where the walk ended (no point in reach and in sight, or never on the
  crosshair) is walked right up beside first, once, before it's left for another.
- Orders: "mine the iron ore at x y z" (one block, by where it is: walked to in reach, mined, picked up); "go to x y
  z" where a block is (an ore): to where it can be reached, not into it.
- Tests: what's reserved; built with what's over only; a block walked up beside before it's left; the order. Tests
  pass. Not yet tried in the game.

## 2026-10-15: "Auto-walk error: TypeError: 'NoneType' object is not subscriptable" at start-up, every frame (user)
- The last round's reserve line went in between starting a task and saying its plan ("First: ..."), so that line
  ran every frame, with no task order: None[0]. The tests' app had no adapter, so the new line never ran there.
- Put back; a test runs frames with an adapter and no task. Errors in the frame loop are shown once (again after
  30 s) and each one's traceback is kept in runtime/errors-<date>.log. Tests pass.

## 2026-10-15: "it mined down and got all its materials but now doesn't know how to get up" (user)
- An iron pickaxe from nothing: all its ingredients got, 14 down in a shaft dug straight down; back up to the crafting
  table: the only way planned was a pillar of 13, with 7 blocks spare (the cobblestone kept for the furnace and the
  stone pickaxe): "needs blocks", then "I can't get to where I can reach the crafting table", and the order over.
  The planner doesn't count blocks (it knows only whether there are any), there was no way that dug instead, the task
  had no way to get what it lacked, and a task an order started never went to the doer when it failed.
- The user's ask: not hardcoded; better ways of going about things, for tasks never met before. In layers:
  1. The planner weighs ways by what they cost: the cheapest way building with more blocks than are spare isn't
     taken blind: the way that digs (a staircase up) is planned too, and the cheaper of it and "get the blocks
     (6 walked each), then build" is taken. A route's cost is given (Path.cost). Live, 14 down: building 64 + 6
     blocks to get 36 = 100 against digging 215: the blocks (mined in the hole), then the pillar.
  2. A task's stuck step is got going: the walk says what it lacked (the route note); the world turns it into steps
     to get it (blocks: the quickest of dirt, cobblestone, planks; over what's kept for what's to come), they go in
     before the step, and the step again (twice at most a task).
  3. Forethought: a gathering walk with more steps after it is a round trip; ways down that can't be walked back up
     (a hole dug straight down, a drop of more than a block) cost the climb back too, so a staircase down wins when
     it's cheaper in all (a stone 6 down: a staircase, not a shaft).
  4. Reasoning: a task an order started that fails (after all that) is thought through by the doer, told what
     stopped it and where, instead of the order ending. The doer is where what was never met before goes.
- Tests: a staircase when the way is come back along; a stuck step gets what it lacks first, then goes on (twice at
  most); a failed task is thought through, once. Tests pass. Not yet tried in the game.

## 2026-10-15: "stuck in a loop and really not reasoning properly" (user): the way out needed the way out
- At the bottom of the hole (14 down): the walk to the crafting table needed 6 more blocks; the task's fix was "getting
  oak planks first" (3 logs, cheaper than 10 stone by the count), and the logs were up on the surface: walks to one log
  after another, each "no way", from the same spot; the fix again (twice); "It's right above us... There must be a
  way up nearby" and "I can't find a way..." every plan.
- Stuck, what's got to get out can't itself need the way out: the fix's gathering counts only what's within 6 blocks
  (or made from what's held): live, dirt or the stone round it, not planks (no logs that near). Walks given up from the
  same spot twice: the spot's the trouble, not the target: what it lacks first if the world can say, else the step
  fails (and the doer thinks it through), not the next target and the next. The guide's "it's right above us" isn't
  said where the walk plans its own way, and passing lines (no way here) aren't said again within 30 s; through
  _navi_line, so quiet while the doer works.
- Test: walks given up from the same spot twice: the step stops. Tests pass. Not yet tried in the game.

## 2026-10-15: stepping back: how it goes about things (docs/AGENT.md), stage 1
- The user: rethink how the assistant breaks orders down and reasons, for all situations, not patched case by case.
  The play-tests' failures all sat between parts that each saw a slice (crafting planner: items; route planner:
  blocks; task runner: a list made once; doer: text), with a plan never looked at again, failures as sentences, and no
  memory. docs/AGENT.md: skills (each game many, with estimates and typed Outcomes), a world memory, a goal planner
  over the skills costed in seconds from where the player is, plans in milestones planned again on failure, at a
  milestone when the world has moved from what was expected, or on an interruption (not after every step: the user),
  the doer above it with typed tools, and a test world playing whole orders. Stages 1-5; PLAN.md phase 6 points to it.
- Stage 1, typed outcomes and the world memory: core/skills.py (Outcome, Reason kinds: missing, needs_tool, no_target,
  unreachable, stuck, no_spot, danger, interrupted, timeout, ui); every task failure ends with an Outcome and its Reason
  (the line for the player kept); done is an Outcome too. core/memory.py: stations put down, blocks dug (by tasks and
  the walk), what couldn't be got at (from where, for a while), what was seen; its summary is in the doer's state
  ("crafting table put down at -12 67 -38 (14 above you)"). The walk's give-up reaches the task as a Reason (its route
  note), and the task's recovery works from that. The doer's act results carry the task's Outcome as data. The state
  says when the player is at the bottom of a hole (the land round the column far above): it said "on the ground" 14
  down a shaft.
- Tests: the memory (stations told with how far up, mined away; what couldn't be got at, left out for a while, from
  where), give-ups as Reasons, a task's Outcome failed (no_target) and done, a station put down noted. Tests pass.

## 2026-10-15: docs/AGENT.md stages 2 and 3: goals planned again, and the test world
- Costs in seconds from where the player is: core/crafting.py's book can give gather_cost(source, n) (the way to the
  nearest that can be got at, and the mining) and station_cost(item) (to one that's there); then a station is chosen
  by cost: one there (getting to it) or one made and put down here. The game's book (adapter): the nearest few of a
  block (asked again after 8 s), less those the memory says couldn't be got at from here, reckoned across and up
  (1 s a block) or down. plan_cost() and step_cost() give what a plan and a step cost.
- core/goals.py: GoalRunner over a planner (plans from what's held now; [] when it's held). Milestones: the tools, the
  stations and the cooked things on the way, and the goal. Planned again when a step fails (its Reason: the memory
  has what couldn't be got at; "needs blocks": blocks first, from what's in reach; stuck: only what's in reach), at a
  milestone that took more than twice its reckoning and 20 s over, and at the plan's end short of the goal (a plan
  for what was lacking): never into the same failure again (the same first step, unless what failed was one thing at
  one place), four times at most; no plan: over to the doer. In the app: item orders (gather, craft, smelt) run under
  it; the task says when each step's done and how long it took; a new order stops the old goal; the task's own
  recovery stands aside for it; targets the memory has as out of reach from here aren't walked to again.
- The route planner: "short of blocks: dig or get them" is one function for the game and the test world
  (blocks.plan_route); a line of sight may have one block in the way that's mined first (not below the feet: a log in
  the canopy behind a leaf was "no way"); toward a block in the ground the search's estimate is weighted 4 times (an
  ore 13 off through stone wasn't reached in 3 s).
- The test world (tests/sim/world.py): blocks, an inventory, the real planners; moving follows the planned route
  (what it breaks is picked up, what it puts down comes from the inventory), long ways a part at a time; mining,
  crafting, smelting, putting down with their stations and tools; each failure the same Outcome as in the game.
  Cases (tests/test_sim_orders.py): an iron pickaxe from nothing (13 steps, one milestone planned again), 14 down a
  hole with 5 planks (a table made and put down there: 10 s), out of a hole with only a pickaxe (dug out, the
  stone dug for the stone pickaxe). Tests pass (368). Not yet tried in the game (it was closed).

## 2026-10-15: the iron pickaxe made with the new goal runner (user: "worked well"); jumping for nothing; the surface
- Under the goal runner the iron pickaxe from a hole went through ("Change of plan: craft 1 iron pickaxe" at the end).
  But "multiple times it jumped doing nothing": the walk log has, digging a staircase up to the ores, a level every 6-7
  s with forward and jump held 1.5-2 s at the same spot, then "replan" and more breaking. A step up was jumped from up
  to 1.4 off the next cell's middle; under the low roof of a staircase dug up the jump is cut short, and from that far
  back it came down short of the step. Now a step up is jumped only up against it (within 1.0 of the next cell's
  middle: up against it is 0.8) and facing it, as Baritone does with a roof over the head; a ladder (straight up) as
  before. The walk log has what the walk is at (the block broken or put down, the blocker), for the next time.
- "Take me to the surface": "Here we are: the surface!" at once, at the bottom of a shaft open to the sky (the
  surface was the open sky over the column). The surface is now the land's level round the column (low in the 5 x 5
  round it, from the land's own heights under the trees): a shaft's floor and a cave are under it, a cliff's foot isn't.
  Live, 13 down the shaft: the surface 13 up.
- A line joined by the shell (two strings run together) put right; the test for joined lines catches those too.
  Tests pass.

## 2026-10-16: stage 4 (docs/AGENT.md): the doer's typed tools and the situation
- The doer gave every order in words, read by the rules meant for the player's speech ("place a crafting table" not
  understood, 2026-10-13; "mine the iron ore at ..." the same, 2026-10-15). Now it has typed tools, each one thing the
  character does given as data: get (item, count: the goal planner, planning again on the way), how_to_get (the plan
  and its seconds, nothing done), go_to (x y z, or a place by name), mine_at, put_down, fight (target, count, all),
  flee, eat, explore, situation. A game offers the ones it has (DoerWorld.doer_tools); `act` stays for orders in
  words the typed ones don't cover. The resolver builds the same actions from the data (CommandResolver.typed).
- Each comes back as data: ok, why not (the Reason: missing, unreachable with the route's note, danger...), what
  changed in what's held, what was said, the seconds. Walks and fights now end with one too (arrived; gave up with
  the route's Reason; taken over by the player: interrupted; a fight won, or low health / surrounded: danger, gone:
  no_target, timeout, unreachable), not only tasks.
- An order planned again mid way (a step failed, the goal planned on from there) is waited for: for a moment nothing
  runs, and that was read as the end.
- situation: health, food (now in the snapshot), where, what's held, the world memory as data (stations put down
  near with how far up or down, what couldn't be got to, blocks dug), the order under way and its plan, what's around.
- The doer's prompt rewritten for the typed tools; its Minecraft know-how (blocks to pillar with, a pickaxe for stone,
  creepers, the dark, several points in one order) moved to the adapter (doer_notes): core/doer.py is any game's.
- Tests pass (379). Not yet tried in the game (it was closed).

## 2026-10-16: the iron pickaxe's waste, beds, houses, the game's own data
- The iron pickaxe got made, slowly, with idle stretches (task and walk logs of 01:31-01:37):
  - The first try gave up after a minute, "stuck" on oak logs. Logs up a trunk had a leaf in the way. The route
    planner counts one block in the way of what's mined as no hindrance (it's mined first), but the mining
    walked "right up beside it" round the tree instead (33 cells for a log 4 off, a missed jump), then gave up.
    Now the block in the way is mined first (the aim already said which it was).
  - The two give-ups were read as "every way out needs something": a way that only needs blocks to build is now
    unreachable with the route's note, and the goal gets the blocks first, not the doer.
  - Cobblestone: each stone was the nearest, so it dug straight down 16 blocks from the crafting table, the last
    6 a fall into a cave, then climbed back up (34 s) for the furnace. Now the block gathered is the quickest
    there and back to where the step began (climbing reckoned), and one under the feet only where the fall after
    it is safe.
  - The furnace was put down by the table before the iron was dug, 23 blocks below, then climbed back to (36 s).
    Now each station is put down just before its first use.
- "Make me a bed": no answer. Working out a plan asked the game for each ingredient's blocks one kind at a time,
  every wool and dye for six colours of bed, each a search of a million blocks on the game's thread: the game
  froze. Now:
  - Bridge v18: one search finds the nearest of every kind (find_each), and chunk sections that can't hold the
    kinds are passed over whole. Entities carry a sheep's colour and whether it's shorn.
  - Planning has a time limit (4 s). "A bed" plans each colour and takes the quickest.
  - Wool comes only from sheep. The game's own data is now extracted (`Get-GameData.bat minecraft`, 9,885
    files, 10 MB, into .local/minecraft/26.3/): from its loot tables, what every creature drops (83 items), so a
    plan can hunt (a white sheep for white wool, a spider for string). Creatures that may be someone's (cats,
    dogs, horses, villagers) are never hunted. The task hunts with the fighter, then picks up the drops.
- "Make me a house, 5 by 5, 3 high": "my building tools only lay one block at a time". Now there's building
  (core/build.py): the doer gives a structure as parts (fill, walls, hollow, clear, laid in order), which become
  the blocks, put down from the bottom up, each against one already there. A build gets its blocks first (the
  planner), mines what's in the way, walks to where each block is in reach and sight (not standing in it), and
  never digs through what it has built. The doer has a `build` tool and is told nothing asked to be built is
  beyond it.
- Tests pass (393). The bridge needs Minecraft restarted to load v18. None of this tried in the game yet.
- (later, the user: "if it has all the game's data, it should know about shearing; we can't be hardcoding every
  single thing") Hunting was a special case of something general: the game's loot tables name every way the
  world gives things besides mining, by their type and folder (entities: killed; shearing; brush; carve; harvest;
  till). Now all of them are read the same way (what it gives, from which creature or block, which kind of it, how
  many on average, the chance: conditions inside all_of/any_of, one entry picked of several; a creature's special
  cases, riding a horse, count as never). A source says how it's got: mined, killed, or an item used on it. The one
  use skill walks up to the creature (or the block), holds the item, right-clicks once, picks up what came of it.
  The adapter knows only the game's five verbs' items (shearing and carve: shears, brush: a brush, till: a hoe,
  harvest: the hand). What isn't in the data (in the game's code: milk is a bucket used on a cow) the doer looks
  up on the wiki and records with a `learn` tool (.local/minecraft/learned.json); the planner reads it from then on.
  Tests pass (395).

## 2026-10-16: "way too slow, it should always respond within a second"; killed backed into a corner
- "Make me a bed" (chat and doer logs, 02:14-02:20): every plan for a bed ran into the 4 s limit ("took too long").
  Profiled live: 553,321 ways tried in 20 s (all the wool, dye, string and flower ways a bed can take, each worked
  out in full). Now a guide first: about what one of each item costs from here, each from the others' costs round
  and round till none gets cheaper (the recipes go in circles: a bed from a dyed bed; a guide worked out down one
  path said "no way" for white wool where it came round); then only the cheapest two ways of each item, and three
  of the items that fit an ingredient, are worked out in full. A white bed: 0.11 s (2 logs, a crafting table, 12
  string from spiders, 3 wool); an iron pickaxe, shears: under 0.05 s. Fuel by what it costs for what it takes (a
  coal block, nine coal, had been chosen to smelt two ingots).
- The first "make me a bed" and "hello, can you hear me?" were thought through at once and shared one step log:
  the bed order died of an IndexError, and its "Sorry" was kept back (other lines are, while the doer works).
  Now a newer order ends the older (each with its own log); errors are always said.
- Something is said within a second of every order: the doer's "Working out how to do that..." after 1 s (was 3)
  if the model hasn't spoken yet; with the planner quick, item orders are answered by the rules at once.
- The fights (combat log 02:21-02:25): backing off a zombie, "back" held while it closed from 2.1 to 0.5 (backed
  into something: only the 8 cells beside the player were known); hurt to 3 of 20, the next zombie of "kill the
  zombies" was taken on, and the player died. Then, fetching the dropped things, "you're hurt, over to you" and the
  walk went on among the zombies: dead again. Now:
  - The fighter sees the ground 4 blocks round (adapter terrain(): where it can stand, and how much room there is
    from a spot, 3 steps out). A way is only taken over ground to stand on (1 block on; 2 barred sliding along
    walls), and a spot with little room (a corner, a dead end) counts against it. Keys held one way with the
    player not moving: that way's blocked for 1.5 s. In a test arena (a wall behind and to one side, 1 to 3
    zombies): 18 hits taken in all against 36.
  - No new fight (nor the next of several) below half health: away first. Hurt: everything stops (the walk
    isn't taken up again, the order's other parts dropped), and food held is eaten.
- Tests pass (400). Not yet tried in the game.

## 2026-10-16: killed again, going back for the dropped things
- Combat log 02:37: just after dying (empty-handed), "go to those coordinates" (where the things lay) at night; four
  zombies there, 17 health to none in 4 s, swinging a fist (1 a hit, a zombie has 20). The player: the zombies
  were where the things were, so they couldn't be gone round; they have to be fought, without being hit. Now:
  - The fighter weighs the odds (every second): the time to bring down every one coming at the player, by the
    damage a second of the best weapon held, against what they'd do meanwhile. One that can't be won on the
    ground, with blocks held and only fighters that must get up close (not skeletons, not creepers): up a
    2-block pillar (looking down, a block under the feet at the top of each jump), crouched, and each one that
    comes in reach hit from above; they can't reach the player up there. Else away if they can be outrun
    ('outmatched': everything stops, as when hurt); else fought. In the test arena: four zombies, bare hands,
    up two blocks, all down, not one hit taken.
  - Walks go round hostile creatures (cells within 4 blocks of one cost more, as Baritone's do), but one at the
    goal (what's walked to).
  - Where the player last died (bridge v19: the game keeps it, the recovery compass's): "where I died" is a
    place to ask about and go to; the doer's state has it, and what the player is armed with ('nothing, fists
    only'), and the doer gets a weapon first before going among enemies (and blocks to stand on: the notes).
- Tests pass (404). Minecraft must be restarted for bridge v19.
- Killed a third time, going back again (02:47; "I was still backed into a corner"). The live world round where it
  happened: the player's things lay at the bottom of a pit, a pocket of about 10 open cells at y 65, reached by
  steps from the north-west. The walk went down into it, four zombies there; 4.8 s fought off at full health, then
  (bare-handed, nothing to build a pillar with) "outmatched", and 6 s "retreating" with the zombies within 1.5 the
  whole time: boxed in, every way blocked, the keys changing each quarter second, and not one swing back. Now:
  - A retreat that hasn't got within RETREAT_GAIN of clear in RETREAT_TRY s is given up: hit back (a test: boxed in
    3 by 3, two zombies: 4 swings in 4 s, against none before).
  - The odds weigh the room there is: on open ground hit and back off takes few hits (three or four zombies held
    off 26 s without one, earlier today); in a corner every one counts in full.
  - On a walk, hostile ones where it's going (or between) are met on open ground: held there if there's room round
    the player, else from the last open ground passed (tracked as it walks); the fighter holds ground (fights what
    comes near, never in after them into less room); if none comes in 10 s ('held_off') the walk isn't taken on
    into them: the player is told, and asked whether to get a weapon and blocks first, or wait for daylight.
  - The fight's log has where the player was and the room round them.
- Tests pass (407).
- A fourth time (02:55, "get my items where I died"): the doer rightly made a wooden sword first (empty-handed, two
  skeletons near), but:
  - Two skeletons 6 and 9 blocks off, not after the player, held the walk 10 s ("There's 2 Skeletons ahead! I'll
    take them on here"), three times. The user: "the character was just doing nothing because of caution for
    those skeletons." Now ground is held only for fighters that must come up close (not ones that shoot or blow
    up), and only those after the player or where it's going.
  - The oak log taken was the tree at the pit's edge, by the zombies. Now each hostile creature near a block to
    gather counts against it (30 s), for every gather.
  - On the way, a sprinting jump on the pit's rim was missed: 13 blocks down, among the zombies, hurt; boxed in on
    the slope (room 8, then 5), bare-handed, dead. The jumps' risk was judged by the least fall in the gap (one
    shallow cell made a deep gap "safe"); now by the worst, and over a fall that would hurt only a walking jump
    over one block, level, is taken (never a sprinting one): the way round, or a block across.
  - Boxed in (a retreat that gets nowhere): the way back to the open ground passed is planned, round them, and the
    fight held there; else hit back. Holding ground is in the fight's log too.
- Tests pass (410).

## 2026-10-16: "it should've been able to kill the skeleton, it was within reach"; shields
- Combat log 03:0x: a skeleton up on a ledge, 2.8 across: "waiting" (out of reach), then jumping (out of reach
  4 s: jump), then "I can't reach that one!", shot meanwhile from 10 health to 2; then dead. Reach was measured
  from the eye to the middle of its body less its width: for one up a ledge, its middle 1.5 over the eye was out of
  reach while its feet were in it. Now to the nearest point of its body (its box, as the game measures a swing),
  aimed at the part of it nearest when it's well up or down, and stepped in nearer across the higher it is
  (2.7 across was right only level with it). A test arena with the game's crosshair (a ray through the body's
  box): one up a ledge, hit from below.
- The user: a shield before a fight with a skeleton; one shooting while doing other things: hidden from or fought,
  depending. Now:
  - A fight order against one that shoots (the adapter's fight_gear: a shield in the off hand): the shield got
    first when it's quick enough (the planner's reckoning, 3 minutes at most), put in the off hand (a task step:
    picked in the hotbar, the swap-hands key), then the fight (the fighter already holds it up against arrows).
  - On a walk, one that shoots set on the player within 16 blocks: fought if within 10, alone, the player over
    half health and the odds good; else into cover: the nearest spot within 6 where a block stands between its
    eye and the player's head (the game's own rays), kept still there till it's given up 3 s (or 10 s), then on.
- Tests pass (414). Not yet tried in the game.

## 2026-10-16: "significantly more fixing": a gear errand among skeletons, jumps, one picture of the danger
- "Kill any nearby skeletons" (10:19): a shield first, from nothing (logs, a pickaxe, stone, iron, smelting): on the
  way, the old crafting table at the pit's edge was gone back to; a zombie fought with bare hands while a skeleton 5
  to 10 blocks off shot the player from 12 to dead. The fighter saw only the zombie.
- Jumps over gaps: 6 of 14 "missed" (walk logs). Traced frame by frame, 4 of them were made: the player landed,
  and the walk held forward on (landed was "within 0.6 of the landing's middle at its height", which a jump goes past
  in the air), over the landing and off the next edge (once the pit's rim: 13 down among the zombies) or into a
  wall. Now landed is down on the ground again after the jump, on or near the landing (a block lower too), and
  forward is let go over the landing; a sprinting jump runs from the back of its block, for speed. (Sprinting
  jumps were briefly turned off; the user: they're an important mechanic, improve them: on, with the fix.) A test:
  a 2-block gap onto a 1.8-deep landing with a drop past it: landed, not off it.
- A crafting table or furnace that's there, and blocks to gather, count as dearer for hostile creatures near them.
- One picture of the danger (core/threat.py): every hostile one near (up close, shooting, blowing up; set on the
  player; for those shooting, whether they can see the player: the game's rays, every 0.5 s), weighed at once:
  cover from those shooting when it isn't a fight for the open (others up close, hurt, unarmed, more than one,
  the odds); else the nearest up close fought; else one shooting near and alone fought; else held for, where the
  walk's going. The same on a walk and on a task (the task's own check saw only ones within 6 up close). Replaces
  the walk's defence, the check for what's ahead, and the shooter rules.
- The fighter, fighting one up close, keeps out of the sight of those shooting (each way scored by how many of them
  would see the player there: one batch of the game's rays every 0.3 s); its odds count those shooting in full
  (backing off is no help against arrows). Covers are out of the sight of every one shooting.
- Tests pass (421). Not yet tried in the game.
- 10:37: "Skeleton's shooting at us: into cover!", then nothing: the player stood in its sight. The hiding check ran
  before the walk's own update each frame and returned while the walk to cover was under way: the walk never took
  a step (a bug of the last change). Now the walk to cover goes on; in cover the task (and the walk it was on) wait;
  one up close that followed into cover is fought there. A test runs the frames: the walk to cover goes.
- Tests pass (422).
- 10:38-10:44 (killed by a zombie; "multiple failed jumps, didn't notice a zombie until it was too close, inefficient
  pathing"):
  - The jump that "failed": into cover was ordered mid-jump; the walk started over in the air, a fall of 10 (20 to
    10 health). The weighing of the danger now waits while the player's in the air (a jump under way, or not on the
    ground).
  - At 10 health and bare-handed, gathering logs, a zombie was let come to 6 blocks before anything was done; then
    away (boxed in), fought in a corner: 10 to dead in 4 s. Now ones that must come up close are weighed from 16
    blocks: when it's no fight to win (and there's nothing to build up out of their reach with), away from them at
    once, while they're slower than a walk; on with the order when they've lost the player or fallen 18 behind (or
    fought, if one catches up). And a zombie up close is reckoned at 3 a second (its hit, once a second), not 1.5:
    a corner with bare hands at half health is no fight to win.
- Tests pass (424).

## 2026-10-16: one conductor (docs/CONDUCT.md)
- The run at 10:5x: the shield errand back to the pit; hold ground; "held off", "I won't walk in among them"; the
  errand on, into the pit; a fall on the way to cover (18.9 to 10.9); hold ground again in the pit; and again. Each
  part did what its rule said; eight parts moved the character on their own triggers, with hand-offs between them.
  The user: step back, maybe a better architecture. Written up in docs/CONDUCT.md; the user agreed to the conductor,
  and not to remembered danger zones (the character must be free to go into new places and back to old ones).
- Built: core/conduct.py, one place that decides each frame whether the order or one survival behaviour (fight,
  cover, away, hold, regroup) has the character, from the danger weighed as one (core/threat.py). A behaviour runs
  till it ends (commitment), a higher one cuts in only after 1.5 s and never mid-jump; one that got nowhere (held
  off) isn't started again against the same creature for a minute: its outcome goes to the order instead (the
  task's step fails with a danger Reason at that target, left out a while: that tree, not the place; the plan goes
  round it; a walk with nothing else to it is over, and the player asked). The order is paused and taken up again
  only by the conductor (the task's step from its start; the walk planned again from here). Gone: `_safety`,
  `_hiding`/`_hidden`, `_hold_after`, the cover/away/hold walks' own hand-offs, the fight events' own walks. A
  survival walk's arrival is its own (the paused task took it for its own). Behaviours wait by the frames' time.
  Every change of who has the character is logged with why: runtime/conduct-<date>.jsonl.
- Tests pass (429). Not yet tried in the game.
- 11:42 (killed; the conductor's first play-test). The decision log (and the task log) showed:
  - Cover couldn't be had (no spot near) after the order had been paused: the order stopped, then started again
    the next frame; with two skeletons each the nearest in turn, every frame: the task "resumed" over and over, its
    walk never under way (none logged since). Now the order's paused only once a survival behaviour is under way,
    and one that can't start (no cover) isn't tried again for 5 s against any.
  - After the order's own fight ("kill the skeletons": outmatched, "over to you"), nothing guarded the player: a
    zombie killed them where they stood. Now the conductor also deals with danger for the player whenever they've
    not been at the controls for 3 s (no order needed).
  - A pickup guarded by hostile ones failed the whole step (a dropped log has no block to leave out): now that
    drop is left, the step goes on.
  - The tests wrote into the real decision log (runtime/conduct-...): no more.
- Tests pass (432).
- 12:11 (killed in the pit; "still big problems, reason more deeply"). The logs put together, one link at a time:
  1. "Kill the skeletons around here", bare-handed. The fight's gear asked only for a shield (never a weapon), so
     the errand went for oak logs, with the last order's "kill all of them" still set.
  2. The tree it chose stood on the rim of the pit, with six hostile ones 8-12 blocks off below. Only ones within 6
     blocks of a tree counted against it.
  3. The way there ended with a sprinting jump up onto the rim. It came down 0.44 past the landing's middle, nothing
     held, slid 0.37 on and off the far edge: 11 blocks down (walk log 97040.2-97041.6).
  4. At the bottom, in a 1-by-1 hole with one step up out of it, the walk got out once and went back in (a spider sat
     on the step). A zombie came: the conductor's fight ("after the player, up close").
  5. 15 full-strength hits, all on the zombie; it never went down: fists (1 a hit). The fighter knew it couldn't
     win, but both ways out were barred: the pillar by two skeletons 8 off (not after anyone, in the dark), getting
     away by the spider (not after anyone, by day). At low health it "stood". A second zombie came; 17 to 0 in 5 s.
     The conductor logged the death as "won".
  6. Also: two parts still started fights. The old "an enemy on the way" check ran beside the conductor. It went
     by the facing direction, not by being set on the player. It ran on the gear errand because of the stale
     "kill all" order. Each of its fights ended with the task "resumed". A pause left over from a held-off
     behaviour "resumed" the next task.
- Changed (each with a test made from the run):
  - Jumps land crouched: from letting go of forward in the air to the touch-down and its settling (up to 0.35 s,
    back held when past the middle). Minecraft's sneak never goes over an edge. (A test world with momentum: the
    same landing without it slides off.)
  - The planner makes no sprinting jump onto a rim with a fall that would hurt past it (it comes down near the far
    edge, at speed); a walking jump, or the way round.
  - A block to gather counts hostile ones up to 16 blocks off against it, less the farther (one set on the player
    half as much again).
  - A fight order bare-handed gets the quickest weapon first (the planner's reckoning: a wooden or a stone sword),
    then the shield against one that shoots; each one is skipped if it can't be had in time. The order's own state
    is set before its gear errand starts.
  - Minecraft: the conductor guards fight orders' walks too (another one up close, after the player, is fought;
    then on to the one the order's for, as a fight). The old on-the-way check is for games without a conductor
    (Elden Ring).
  - The fighter: ones that shoot or blow up bar the pillar only when they're set on the player; a fast one
    (a spider) bars getting away only when it's set on the player. The pillar goes 2 over the highest ground next
    to the player (in that hole: 3). At low health with no way away: up the pillar if it can, before standing.
  - A death ends a survival behaviour as "died", not "won". A held-off behaviour, a new order or a new task clears
    the paused order.
  - The fight log gains what's in hand and the target's health (whether hits land, and with what).
- Tests pass (443). Not yet tried in the game.

## 2026-10-16: walking rebuilt the Baritone way (docs/MOVEMENT.md)
- The run at 12:30 ("kill all the skeletons nearby"):
  - **The far tree.** It walked 34 blocks to a tree, past two trees 5-7 blocks off; its route passed within 3
    blocks of them. My danger rule from the round before (hostile mobs up to 16 blocks off count against a tree)
    counted skeletons 13 blocks off and 10 down in the pit.
  - **The jumps.** Its planned jumps were walking ones, and the rule against running jumps onto rims cut others.
  - **The climb.** Back to the crafting table it dug a staircase up through the hill instead of walking back up
    the slope it had come down. Jumping in place at each step, re-planning; then the pause.
- The user: "look into https://github.com/cabaletta/baritone, and please do things the way they do. You can use
  their code too." Baritone is LGPL-3.0 and this repo is MIT and public, so its design, the game's physics numbers
  and its default penalties are used; the code is our own (docs/MOVEMENT.md, Licence).
- New:
  - `games/minecraft/pathing.py`: Baritone's moves (traverse, bridge, ascend with a step put down, descend, fall,
    diagonal round corners, pillar, ladder, swim up, downward, parkour), costs in ticks. A* with its estimate,
    the best part of the way out of time, and several goals at once.
  - `games/minecraft/moving.py`: each move done frame by frame by its own rules (Baritone's PathExecutor and
    movements). Our changes for keys sent from outside the game:
    - jumps pressed ahead by the speed, short ones timed to land in the middle;
    - the jump key held until airborne;
    - a run dropped before walking jumps and falls;
    - crouched landings with nothing past them;
    - falls steered by the keys alone;
    - pillars open the block two over the head.
  - `tests/sim/physics.py`: the game's movement, tick by tick; `tests/test_moving.py`: 18 routes done end to end
    in it. Along the way it found:
    - a jump key held a frame missed by the game's tick;
    - a walking jump at a run overshooting a one-block rim by three;
    - a walking jump flying 2.4, past a one-block island into the next gap;
    - a fall carried past a one-wide ledge while turning round;
    - a bridge "at the edge" while still turning to it.
  - Bridge v20: the blocks round the feet every frame (7×8×7), and the player's speed across, whether they're up
    against something, crouching or running. With an older bridge, the mover asks about its own blocks.
  - Choosing a block to gather: one search toward the nearest 16, the first one reached taken (Baritone's
    mining). The danger rule from the round before is gone.
  - Hostile mobs on routes: Baritone's avoidance (a move within 8 of one costs 1.5 times, compounding).
- Gone: the old search in blocks.py (blocks.py keeps the grid, the kinds, standing, sight and the goals); the
  rules not Baritone's: walking jumps only over harmful falls, no running jumps onto rims, the cost of standing
  by a cliff edge, refusing drops onto narrow ledges (the fall's now steered onto them). The walk's old
  step-by-step doing of routes stays only as a fallback where no mover is set.
- A long way planned further while walked is handed to the mover whole again: it finds where the feet are along all of it (it looked only 8 moves ahead, and would have planned again).
- Tests pass (458). Not yet tried in the game: restart Minecraft (bridge v20) and the assistant.

## 2026-10-16: the first play-test of the Baritone-way walk (14:30)
- The order: "gather four wood, make a stone sword and a stone pickaxe, then two more wood from a different tree".
  The nearest tree was taken (the new choice). Then the stone and a skeleton:
  - **The stone.** The first stone "in reach" needed a block of dirt mined out of the line of sight. The goal
    counted that block as free, so a stone behind dirt looked as cheap as one in plain sight. Then a 3-block fall
    into a hole for the next one. That's a move Baritone takes too; health was still 20 after it, so the damage
    was the skeleton's.
  - **The cover.** A skeleton shot; "into cover". The walk there stopped a block short and stood 10 s, then
    hid at a spot the skeleton could still see. Health went from 20 to 3.8 in 22 s.
    - At the end of the route the mover had finished, but the walk's old end-of-route code waited for the
      route's own action list (steps, breaks), done by the mover but never marked done. That stall hit any walk
      whose last move had work in it.
    - The spot was tested for sight at the player's own height, not the height of the ground there; with the
      head only; and "there" was the loose near-it goal (a block short counted).
    - In cover, nothing looked again whether the skeleton still saw the player, or noticed the hits.
- Changed:
  - When the mover's done, the route's own work is marked done (only looking at what was come for is left).
  - Cover: the spot tested at the height the feet would be there, head and body both out of sight; the walk ends
    on that very block (a new 'cell' goal). In cover, seen by the shooter for 0.6 s or hit: that cover's given
    up (not taken again for a minute) and the next is looked for at once.
  - To mine a block: the block in the way of the line to it is broken first, and now counts in the cost (its
    break time). The estimate for a block to mine runs to within reach of it, not to the block itself, and is
    weighted more only for one shut in on every side. (The double weight for any solid target let a dearer
    buried stone finish first.)
- Baritone itself never fights; for mobs it only has avoidance in its routes (here since the last round).
  Keeping the player safe is the conductor's (docs/CONDUCT.md).
- Tests pass (461). Not yet tried in the game.

## 2026-10-16: the long play-test (15:46-15:58): items back, a shield, iron armor, down a mine
- The user: "mining upwards especially works perfectly now". What the logs showed went wrong:
  - **Out of the hole.** "Take me to the surface" from 36 down, and a walk to a tree from there: plans found
    ("there", 48 cells), then a re-plan every 3 s for a minute, "I can't find a way". A way with a pillar in it
    put the pillar's block down against nothing; turning the route into the walk's actions raised. The companion
    swallowed the error, so it looked like no way at all.
  - **The pickaxe wore out down the mine.** The gather step checked for its tool only as it started. With the
    stone pickaxe gone it went on "mining" iron with the wooden one, nothing dropping, the same ore walked to again
    and again, until the player said so.
  - **Orders cut short.** "While you're doing this, can you also make me full iron armor?" stopped the shield
    under way, and it was never taken up again. After "the pickaxe broke, get another" it made the pickaxe, then
    told the player to leave, instead of going on with the armor. "No, finish what you're doing" was needed.
  - **Mine at.** "Done! You have your iron ore" four times, nothing got; the raw iron lay there until asked again.
  - **Dropped items** were "creature in sight" to the doer, with no coordinates: its go_to there couldn't start.
  - **Tunnelling.** Down the mine a new route came every 1.6 s, and each started the move under way over. The
    companion re-planned whenever a partial route had less than 50 m left: made for open ground, it's always so
    digging through stone.
- Changed:
  - A pillar's block is put down on the one under it. A route plan that raises is logged (runtime/errors-...),
    not hidden.
  - The gather step checks its tool each time it looks for the next block. With the tool gone it fails with
    needs_tool, and the goal runner plans again from what's held, a new tool in it.
  - Orders cut short by a newer one are kept, shown to the doer in the state ("unfinished earlier orders").
    One that adds to them ("also", "while you're doing this") has them done too; one that fixes something in
    their way is done and then they're carried on with; one that replaces them leaves them. finish says which
    (earlier: done, dropped, open).
  - A dug block whose drop didn't come is walked onto, once.
  - where_is: a dropped item is "item lying on the ground", with its coordinates.
  - Walking block by block, the next part's planned when 8 blocks are left, not 50 m. A new route that has the
    move under way in it goes on with that move as it is (Baritone splices). A stopped walk leaves nothing to the
    next.
- Tests pass (465). Not yet tried in the game.

## 2026-10-16: the inventory skill (16:40)
- "There's iron in my inventory but not in my hotbar, can you move it there, and sort things as well": "my orders
  don't reach the inventory slots, and the game kept rejecting the request". The doer had no tool for the
  inventory. Its `act` went to the words-only orders, which took it for a walk ("I didn't catch where to go"), and
  it told the player the game wouldn't let it. Crafting uses that very screen, and a step already moved tools
  into the hotbar inside tasks, but no order could reach it.
- New skill: lay out the hotbar (core/tasks.py, the 'arrange' step). The inventory screen is opened, one move at a
  time: the wanted stack picked up and put on its slot, what was there put back where the stack came from, the
  screen read again after each; what's left on the cursor put down; closed. The layout is the game's
  (adapter.hotbar_layout): items to have in the hotbar; sort, the usual way players keep it (the best weapon,
  pickaxe, axe, shovel, food, blocks to build with, a torch, then what was asked for, then what else was there);
  or slot by slot.
- The doer has it as a typed tool (`inventory`: items, slots, sort). Words-only orders ("put the cobblestone in
  my hotbar", "sort my inventory") go to it too.
- The doer's told: what the game lets a player do, the character can do too; with no tool for it, it's a skill
  not had yet, never "the game won't let me".
- Tests pass (468). Not yet tried in the game.

## 2026-10-16: understanding orders about how to move (16:51-16:57)
- "Without placing any blocks, could you jump from here onto that crafting table?" (asked by voice): "Yes! Walk
  about two blocks closer, then sprint-jump", then "probably not: a standing start clears about one block".
  Guessed, the ground never looked at; the planner would have known.
- "Jump on top of that crafting table without placing any blocks": the doer tried "back up as far as you can,
  then sprint-jump onto the crafting table" through act three times. Each was "I didn't catch where to go":
  act takes only simple orders, and the walk plans its own jumps. Then go_to, which put down 2 cobblestone: no
  walk could be told not to.
- New:
  - check_way (the doer's and the voice agent's): whether the player could get to a spot and how, by the planner
    on the real ground, nothing done. To what's looked at (onto its top) or x y z, with or without putting blocks
    down or digging. Can or not; each jump (the gap, at a run or not, up a block); drops; blocks put down and dug;
    seconds.
  - go_to's place_blocks and dig: a walk that never puts a block down, or never digs (the planner plans without).
    Set on every walk, so a later one there doesn't keep them.
  - The doer's told: moving is go_to, jumps and climbs and how included; check before saying a way can or can't
    be made, never guess. An act it can't follow says so, and what to use instead.
- Tests pass (469). Not yet tried in the game.

## 2026-10-16: the navigation skills, as Baritone's commands
- The user: "there should be skills to do most things Baritone can do, like all the navigation stuff". Laid side by
  side (docs/MOVEMENT.md, Skills): most were here already (go_to, get and mine_at, explore, the surface, build,
  where_is, flee). New:
  - Goals: go_to x z (GoalXZ: that spot at any height), y (GoalYLevel: down or up to that height), within r
    (GoalNear). The planner's goal tests and estimate have them.
  - follow (Baritone's follow): a creature walked after again whenever it's farther than the distance asked, till
    another order, the player taking over, or it's gone.
  - pickup (Baritone's pickup): the dropped items within a radius (or of some kinds), each walked onto.
  - tunnel (Baritone's tunnel): straight on from the player (a direction, or the way the camera faces), its
    length, width and height: a cleared box, built by the build skill.
  - waypoint (Baritone's waypoints): named places kept between sessions (.local/<game>/waypoints.json); set, get,
    list (with the last death), delete; gone to with go_to.
- Not yet: farm, elytra, axis, invert, backfill, exploring to unseen ground.
- Tests pass (476). Not yet tried in the game.

## 2026-10-16: a question answered in words was never heard
- Play-test: "Can you jump onto that crafting table without placing any blocks?" sat at "Working out how to do
  that...". The doer had checked (check_way: yes, 8.4 s, one running jump up over a gap) and answered in plain
  words instead of calling finish; words without a tool call were returned, never said. Now they're said too.
- Then "okay, please do the jump" walked to beside the table, not onto it: the doer's go_to asked for the
  "crafting table" by name (a block's goal: stood beside it), not the top check_way had found. Now:
  - go_to has onto (with a kind of block or "that"): the cell over the block, stood on. "onto ...", "on top of
    ...", "up on ..." in the place's words do the same.
  - check_way gives the go_to that does the very way it found.
  - The doer keeps the last way checked (5 min) and gives it to the next order, for "okay, do it".
- Tests pass (479). Not yet tried in the game.

## 2026-10-16: jumps any way (the leap), and the fewest jumps
- Play-test: "Can you do it with just one jump?" got "I can't: two jumps, 17 steps". The way to the crafting
  table went down through the ravine and back up. The player: "I can even jump back to the block from the
  crafting table with one sprinting jump... It can even do it without a sprinting jump". The table is two across
  and three along, one lower. Every jump the planner knew went straight along x or z (Baritone's parkour), and
  never down.
- New move, the leap (pathing._leaps, moving.Mover._leap): docs/MOVEMENT.md has the details.
  - Any way over a gap, onto a block up one, level or down one. Walking where a walking jump carries far enough
    (0.85 of the game's own jump counted on), else at a run.
  - The body's room is checked along the whole flight, all worked out once (LEAP_TABLE).
  - It costs a little more than a straight jump. The executor eases off if too fast, lands crouched, and walks
    back on if it slid off.
  - On the real terrain (read from the game): down onto the table, one walking jump (1.2 s, was 7 s and 17
    moves); back up, one jump at a run. Both are as the player found by hand.
  - Planner speed: a step was 7 times as long at first; worked out ahead and indexed straight into the grid's
    bytes, it's now 1.45 times as long.
- check_way most_jumps: the quickest way if it's within that many jumps, else the way with the fewest
  (FEWEST_JUMPS a jump), and can or not. go_to fewest_jumps does that way.
- The doer is told not to answer such a question from an earlier check.
- Tests: in the movement sim, the hill-and-table jump down at a walk and back up at a run, a slanted gap level,
  and fewest jumps walking round. All 483 pass. Not yet tried in the game.

## 2026-10-16: the doer's lines kept to what was asked
- Play-test: the jumps worked, but every reply was padded with the state: "Made it! ... I'm standing on top of
  it at 334, 82, -420. Health is 10 of 20, and a spider and skeletons are about 15 to 20 blocks off, so stay
  alert." The player: "too verbose ... nothing to do with what I asked". Each step's result carries the state,
  and the doer read it out.
- The doer's instructions and its finish tool now say:
  - only what the order was about: no coordinates, health, food, time or creatures around unless asked;
  - the state is for thinking, not for reading out;
  - enemies are watched and warned of apart from the doer;
  - an answer is its gist.
- Asking first is for a risk the order itself brings, not for enemies merely about.
- Replayed both orders against the real model with a stand-in world, twice (in the scratchpad, not the
  repository). For example: "Yes: one slanted jump at a run, up one block, and no blocks need placing or
  digging." and "Made it! I jumped onto that block without placing any blocks." Tests pass (483).

## 2026-10-16: "make a bed": a missed jump, a stuck walk, a purple bed from spiders
Play-test, from the logs:
- **The jump that fell short.** A running jump over three (parkour), just after a step up, went up 0.3 before
  its block's middle. The early take-off is meant for short walking jumps; here it ran on a speed read too high
  just after the step. It came down 0.3 short, hit the landing's side and fell 5. Now a running jump always goes
  up at the edge, as Baritone's does; a running leap goes up no earlier than 0.3 before the edge.
- **The jump off the pillar, and 40 s standing still.** The escape from the spiders ("up out of their reach")
  pillared up looking straight down, and the view stayed so. Straight down, the camera's forward has no level
  part (the game sends (0, -1, 0) exactly), so the heading read from it was 0 or 180 whatever the turn. A leap
  off the pillar's top went off its side (the fall), and the walk after turned and turned, never seeing itself
  turn, then gave up: "every way out needs something I haven't got".
  - core/autowalk.py: level_forward and cam_yaw read the heading from the camera's right side when its forward
    is near straight up or down. Every heading read (walk, moves, combat, melee, the app) uses them.
  - The Mover brings the view back level (15 degrees down) when walking with it tilted over 50 degrees.
  - On a jump's block, the Mover turns in place, crouched, till facing within 12 degrees; never walks on
    sideways off the block.
- **The purple bed.** "A bed" matches 16 colours, and only the first 6 in the book were planned; white wasn't
  among them, so every plan needed dye. Now, among items matched equally well, the white (undyed) one comes
  first. "A red bed" is still the red one.
- **12 string from one spider.** A creature's drops were costed as the walk to the nearest one plus n kills,
  however many there were about.
  - Now: the walk out to the farthest of the ones it takes, and 60 s for each one more than are about.
  - Creatures beyond the snapshot's 62 m (the game's whole-world search, as where_is) are counted and hunted
    too. Their kind (a sheep's colour) isn't known from there: 20 s more each, looked at again when near.
  - A white bed from here: shears from the raw iron held and 3 shorn sheep, about 2 minutes (it was "12 string,
    111 s"; with one spider about it's really ~13 minutes).
- **Planning speed.** With sheep found, planning weighed every smelting fuel (for the shears' iron), and each
  fuel's block was searched on its own: 56 searches, 5.7 s. The one search done up front now covers the
  stations and the fuels, and the far creature search is cached for 2 s: under 1 s.
- Tests: in the sim, straight down off a pillar and a walk; a running jump over three after a step (its
  take-off point; the sim records them now, and its camera rounds as the game's does). Each fails on the old
  code. Also the bed's colour, creatures counted, and ones beyond sight found. All 488 pass.

## 2026-10-17: the bed: sheep out of sight, and a wooden sword burnt
- **Stuck on the wool.** The sheep the plan now finds beyond sight (the game's search, past the snapshot's
  62 m) were hunted by their id. The walk, not seeing them, said "target gone" at once: 16 sheep, twice each,
  3 s apiece.
  - A creature beyond sight is now walked toward as a place (the task's 'seek'). Once it's in view
    (TaskWorld.task_seen: in the snapshot), the walk stops and the step picks it again: hunted, or used on
    (shears), from there.
- **A wooden sword in the furnace.** Shears' two iron ingots were smelted with "2 wooden swords": the player's
  own, and one crafted to burn. Fuels held were tried first, and a sword is a fuel in the game's data. A sword
  burns for one item; the two planks it's made of, for three.
  - Tools and weapons (swords, pickaxes, axes, shovels, hoes, bows, crossbows, fishing rods, shields, spears)
    are no longer fuel (_Book.fuels). Shears from here now: 2 logs gathered and burnt.
- Tests: seeking a creature out of sight, then hunting it once in view; tools never burnt. All 490 pass.

## 2026-10-17: swimming, one rule for every move
- Play-test: crossing a lake for the sheep, the character went under and nearly ran out of air ("it seems to
  have forgotten how to navigate through water").
  - From the walk log: the way across was mostly diagonal moves. Only the straight move held jump in water,
    which keeps the head up; the diagonal ones didn't, so they sank. They were also made at a run, and a run in
    water is the dive-swim, the way the view looks (a little down).
  - Off the route, planned again, sank again.
- Now the Mover has one water rule after every move's own keys (Mover._water), as Baritone's movement update
  does it:
  - in water, never at a run;
  - jump held, unless the way goes down into the water;
  - the view level while swimming on;
  - head under and air below 120 (of 300): straight up first.
- The adapter passes the player's air on (the bridge already sent it).
- The movement sim now has the dive-swim (a run in water swims the way the view looks, down too) and air
  (less a tick with the eyes under, drowning at the end).
- Tests: across a deep lake slantwise (fails without the rule: sank); up from the lake's bottom with little
  air (never drowns). All 492 pass.

## 2026-10-17: swimming, part two: bobbing, sinking between routes, and up to the way
- Play-test (after the water rule): "still makes me sink". The walk log: on the lake, the diagonal moves now
  held jump. But:
  - With jump held at the top, the body bobs a little out of the water, and the diagonal move gave up "off the
    diagonal" (its feet block one over its own).
  - Planning again, the walk held no keys at all, so the player sank.
  - Each new route began at the top, 2 blocks over the sunk feet; its first move, a diagonal, gave up at once
    again. Planned again every half second for 14 s; the air at 37 of 300 when the player paused.
- Now:
  - **Bobbing.** The feet a little over a water block's top (under BOB, 0.6) count as in it (Mover.update): a
    move's blocks, and the water rule.
  - **Afloat between routes.** The walk itself (core/autowalk.py, AutoWalk._afloat), while active and not on a
    move of the mover's: in water (the snapshot says), jump held and never at a run, whatever it's doing
    (planning again, waiting for the way, chasing). The adapter sets in_water and head_under from the blocks
    round the feet the bridge sends each frame.
  - **Up to the way.** In water, below one of the move's own blocks in the same column (up to 6), it swims up
    to it first, not "off the route".
  - **A hop isn't off.** A diagonal move in the air one block over its own blocks isn't off it (out of water
    onto the bank, jump still held a frame).
- Tests: sunk 3 under the way's start, its first move diagonal, the air low: up, then across, never drowning
  (fails without swimming up). Kept afloat between routes. All 493 pass.

## 2026-10-17: killed by spiders on the bed errand
- Play-test: across the lake fine this time (the swimming fixes held). Then, at night, at 7 of 20 health, no sword
  (a pickaxe the best to hit with), two spiders came:
  - The fighter went up a pillar "out of their reach", but spiders climb walls.
  - At the top it called the fight "won" (none within 6), with a spider after the player 9 off. The walk
    stepped off the pillar to "open ground", started up again, and was bitten from 7.4 to 0.
  - "Make me a bed" had gone straight to the task (the quick path, not the doer), so nothing weighed setting
    out unarmed among enemies at night.
- Now:
  - **No pillar against climbers or fliers.** The combat profile has climbs (spiders, cave spiders) and flies
    (phantoms, vexes, bees, ghasts, blazes, breezes). One of them after the player bars the pillar (melee
    _can_perch); one by, not after anyone, doesn't.
  - **Up there till it's really over.** None within 6 and none after the player within 16 (PERCH_WATCH); while
    one's after the player farther off, it's watched, crouched.
  - **Armed means a sword or better.** fight_gear wants a weapon below a wooden sword's 4 (SWORD_DAMAGE): a
    pickaxe isn't armed, an axe is.
  - **Errands among enemies.** An errand (a task) set about with a hostile one within 24 (ERRAND_FOES) first gets
    what a fight with the nearest wants, when it's quick (the same as a fight order's gear: _gear_for), and, under
    half health with food to hand, a bite (App._errand_gear). "Enemies about: a stone sword first!"
- Tests: climbers barring the pillar and keeping it up (and a crash this found: none near, one after the player
  farther off); a pickaxe isn't armed, an axe is; an errand among enemies gets a sword first, none about as it
  was; hurt with food, a bite first. All 496 pass.
- Not yet: food when there's none to hand (killing a sheep for the wool gives mutton too; the doer's prompt
  covers eating, the quick task path doesn't fetch food).

## 2026-10-17: an equip skill (armour on, a shield in the off hand)
- Play-test: the bed was made. Then "Can you equip any armor or whatever equipment?" only sorted the hotbar, and
  asked again: "I couldn't equip the iron chestplate or the shield: my tools can only sort the hotbar". The doer
  had tried "equip the iron chestplate" as an order in words (not understood), then the hotbar tool.
- There was no skill for it. Now:
  - **Tool:** equip (typed, the doer's; items: only these), offered where the game has equipment.
  - **What to put on:** the adapter's equipment() picks the best piece held for each of the head, chest, legs
    and feet (by tier), and a shield into an empty off hand (the existing 'offhand' step).
  - **The task step 'equip':** the piece into the hotbar if it's not there, picked, the view tilted up (used on
    a block in front, it would open that instead), then used: the game puts it on, swapping it for what's worn
    there.
  - **Checked by:** the armour points (the bridge sent them; now in the snapshot) and the count carried (worn
    pieces aren't counted). Fewer points after: worse than what was worn, used again to swap it back. Nothing
    changed twice with armour on: one as good is worn.
- Live: from what's held now, the plan is the iron chestplate on, the shield into the off hand.
- Tests: put on looking up; a worse one swapped back; the best of each part and a shield. All 499 pass. Not yet
  tried in the game.

## 2026-10-17: a use skill (right-click a block or a creature)
- Play-test: the armour went on. Then "sleep in the bed for me first, then get me a saddle": the doer walked to
  the bed, tried "sleep in the bed" and "sleep" as orders in words (not understood), and said "I can't lie down
  in it myself, so you'll need to right-click the bed". There was no skill for right-clicking.
- Now, one general skill (not one per thing):
  - **Tool:** use (typed, the doer's). A block or a creature right-clicked as a player does: sleep in a bed,
    open a door, gate, chest or trapdoor, flip a lever, press a button, put a saddle on a horse, mount it, feed,
    tame or breed.
  - **Targets:** "that" (the crosshair's: a block or a creature), x y z, a creature by name, or the nearest block
    of a kind.
  - **item:** what to hold; none means an empty hand (holding food, a horse is fed, not ridden).
  - **The task step 'use':** held first, walked to right beside a block (not into a bed) or up to a creature
    (following it), aimed at, right-clicked once, never crouched (crouched, a held block would be put down
    instead).
  - **Block names:** a kind of block matched equally by several ids is searched for as all of them ("the bed" was
    a black bed only; the player's white bed wasn't found).
- The doer's instructions name `use` among what a player can do.
- Live: "use bed" goes to the white bed at 187 67 -408; "use horse" to the horse; "use that" to the crosshair's
  block; "saddle on the horse" says there's no saddle held.
- Tests: a bed walked to, beside it, right-clicked once with an empty hand, not crouched; a saddle used on a
  horse in reach. All 501 pass. Not yet tried in the game.

## 2026-10-17: shorter answers by default
- The player: "can you make the text less? ... if I want more details then I can ask it for more, but by
  default it should be less text". "How do I ride these?" got every step: right-click, the bucking, the hearts,
  the saddle in the inventory, the keys, the jump (4 sentences).
- The voice assistant's instructions (core/agent.py) now say:
  - one short sentence, under 15 words; a second only when one can't answer at all;
  - the player asks for more ("tell me more", "how?"); never added before then;
  - "How do I": the first step or the gist; "Why": the reason in a few words; no "or" alternatives unless
    asked.
- The doer's finish line: one short sentence, under 15 words, more only if asked.
- Replayed against the real model (in the scratchpad, not the repository; the running game for context):
  - Before the last tightening: 13 to 31 words.
  - After, on questions not in the instructions: 8 to 16 words, one sentence each ("How do I tame a wolf?":
    "Right-click a wild wolf with bones; each bone has about a one-in-three chance to tame it.").
- Tests pass (501).

## 2026-10-17: jumps at a run that weren't, a ravine, and digging from the water
- Play-test: the bed slept in, the saddle made. Then "go back to those horses": dead, "fell from a high place".
- From the walk log:
  - Five jumps at a run fell short on the way, four into water: from a turn made crouched in place, after a
    step down, never running at the jump. The last, over three, straight after a step down, into a ravine 35
    deep.
  - The game's physics: the run's boost at the jump is most of the reach. From a standstill at a run a jump
    flies 3 blocks; at a walk's start, 1.1.
  - Climbing out of the water, it dug into the bank's block for 15 to 30 s, bobbing (the crosshair off the
    block), where mining takes 25 times as long. The planner had costed the dig as on dry ground.
- Now:
  - **The executor (Mover._at_a_run, both jump moves).** A jump at a run goes up only when the game says the
    player's running. Not running at the edge: stopped crouched, backed off into the block (to 0.3 short of its
    middle), run at again. After two runs it gives up from back there (giving up at the edge, the run carried
    on over it, keys let go while planning again), and the jump's left out of plans for 2 minutes
    (note_failed_work 'jump', the planner's no_jump). The old "out over the gap already: up" branch jumped
    without that check: it has it too.
  - **The planner.** No jumps at a run planned with food at 6 or less (the game won't run). A jump over a gap a
    fall into would hurt (dry ground more than 2 down, lava, nothing; water is fine) costs 100 ticks more, so a
    way round is taken where there is one. Digging from in water costs 5 times as much, 25 not standing on
    anything (the game's).
  - Tried and dropped: a run-up block behind every jump at a run. It ruled out the player's own jump from the
    crafting table back to the hill; standing starts are fine when the run's really on.
- The movement sim can refuse to run (can_run), to test it.
- Tests: never up at a run not running (stops at the edge, backs off, gives up, the jump left out); a gap that
  would hurt gone round, one with water jumped; out of the water where nothing need be dug. All 504 pass. The
  planner's step time on the live terrain is unchanged.
- The user: "it should be determining the most efficient, and most efficient is probably to bridge". Bridging
  was already a move (a block put against a side, or crouched backwards at the edge), weighed with the jump,
  the way round and digging; the test above had no blocks held. Checked: over a gap a fall would hurt, three
  wide, with 16 blocks held the plan bridges it (3 put down, about 6 s: cheaper than the jump with its risk);
  with none, it jumps (cheaper than 30 blocks round); over water it jumps either way. Test: bridged in the sim
  with blocks held (done, never below the start), jumped with none. On the fatal run the player held 64
  cobblestone: with the risk costed, that ravine would have been bridged. All 505 pass.

## 2026-10-17: lag and getting stuck: plans one after another
- Play-test: the things fetched from the death spot, then "go ride one of the horses": "major lag issues, and it
  getting stuck". From the walk log, two things kept the computer busy planning:
  - **Planned 10 times a second for 15 s, standing still on the bed.** Each new way's first move, a diagonal,
    gave up at once ("off the diagonal": the feet beside its start, on the bed, not in its blocks). Planned
    again at once, the same way, the same again. Each plan also read the box of blocks from the game, which the
    bridge does on the game's own thread: the game hitched with each.
  - **Out of a ravine 38 up, each plan searched 3 s and came back with 1 to 6 blocks of way**, planned again
    and again. Live, on that ravine: a 1.5 s plan took 3.1 s (a second whole search, "would blocks help?", ran
    each time the first ran out of time), and found no way at all; weighted more, 13 moves, 14 up.
- Now:
  - **Planning paced** (companion._start_plan). Planned again from the same spot (within 0.75) for the same goal:
    0.25 s after the last, then twice as long each time, up to 4 s; moved on, at once.
  - **A diagonal begun from beside its start** (within 1.5 across, on its level) walks onto it first, not given
    up.
  - **The box of blocks reused** for the same box within 1 s (adapter.route), not read from the game again.
  - **"Would blocks help?"** searched only when there's no way at all, not on a long way out of time.
  - **A long way up** (8 or more blocks to the goal) is searched weighted 2.5 at least (greedier: a way found,
    if not the cheapest).
  - Live, on the same ravine: the plan now 1.6 s (was 3.1), 13 moves up to 44 (was none); the companion plans
    on from its end.
- Tests: plans from one spot paced (under 12 in 10 s, not 100), at once when moved on; a slant way begun from
  beside its start (fails without). All 507 pass. The planner's step time elsewhere is unchanged.

## 2026-10-17: stuck in the ravine: blocks in the pack, plans on the frame loop's time
- Play-test: "ride the horse", from the ravine 38 down: "it might have gotten stuck"; the status bar showed
  115 ms a frame (about 1 normally).
- From the walk log and the game:
  - It dug a staircase up out of stone, 6 to 7 s a block: 35 to 44 in a minute. The player held 70 cobblestone
    and 47 diorite, all in the pack, none in the hotbar; only the hotbar's are built with and planned with, so
    the planner had nothing to pillar with.
  - It planned all the while (9,000 positions, about 1.5 s each, again near each short way's end). The search
    ran on a thread in the assistant's own process and held Python's lock: the frame loop slowed to 115 ms,
    and with it the aiming and the digging.
  - Then plan after plan ran out of time with one block of way, from the same spot: stuck.
- Now:
  - **Planning in a process of its own** (pathing.run, pathing.use_worker; the app turns it on through the
    adapter's plan_in_worker). The frame loop is never held up by it. If the worker fails, it plans here as
    before. Checked: a separate process, started in 0.24 s, the same ways.
  - **Blocks to hand** (adapter.blocks_to_hand, App._blocks_to_hand). A walk or an errand set about with no
    blocks to build with in the hotbar, and 8 or more held, puts the most plentiful into the hotbar first (the
    hotbar skill), then goes. Not for a fight or a follow. Live: cobblestone into the empty slot 9.
  - **A longer plan after next to none.** Planned again from about the same spot after the last plan ran out of
    time with 3 blocks of way or fewer: twice the time, up to 8 s (adapter.route; the worker means the game
    doesn't feel it). Live, the ravine's plan needed 9,000 positions, right at 1.5 s: inline it just made it,
    in the worker (8% slower) it didn't, and got no way.
- Tests: blocks put in the hotbar when only in the pack (none when some are there already); the hotbar step
  before a walk and in front of an errand, not before a fight; a plan in the worker the same as here. All 510
  pass.

## 2026-10-17: blown up by a creeper; the shield left in the pack
- Play-test: "overall way better than before". Out of the ravine, a zombie, a skeleton and more zombies fought on
  the way to sleep; then "blown up by Creeper". "I don't know if it used a shield or not when fighting the
  skeleton."
- From the combat log:
  - **The shield:** not used. The wooden sword the whole fight, nothing in the off hand. The shield came back
    with the things picked up where the player had died, into the pack, not the off hand. That skeleton fight
    was the conductor's (defending on the way), not a fight order, so "gear first" never ran. (The fighter
    holds a shield up against a drawing bow when it's in the off hand; it wasn't.)
  - **The creeper:** it backed off one creeper as it lit (it went out), then fought a zombie with the creeper 3
    to 5 off. The creeper came on and lit (0.07 to 1.0); for those 2.5 s the fighter held 'back' and the player
    didn't move (a step behind, the map not showing it; "spacing (blocked)" just before): blown up, 12.8 to 0.
- Now:
  - **Ready first** (App._blocks_to_hand, which also puts blocks in the hotbar). A walk or an errand set about
    with armour or a shield held but not worn puts them on first (the equip skill), then goes.
  - **A creeper near comes first.** Within 4.5 (CREEPER_FIRST) it's the target, nearer than the one fought or
    not.
  - **Out of a blast**, a way that's blocked is found at once. On the way taken, under 0.35 of a walk's worth in
    0.15 s (stopped, or sliding along a wall) means blocked, with the diagonals either side of it; then
    another way. Before, the creeper getaway didn't look at blocked ways at all, and the watch over every move
    takes 0.4 s windows against a 1.5 s fuse. Jump is held while getting away (up any step).
- Tests: a creeper lit 2.6 off with a wall behind that the map doesn't show; a creeper near fought before a
  spider. In the creeper sim: 14.7 health lost before (backed into the wall the whole fuse), 3.6 now (about 3
  is the least, sideways at once). Ready first with a shield in the pack. All 512 pass.

## 2026-10-17: "go get my stuff" made a sword and a shield instead
- Play-test: "it started doing all this other stuff after the nearby enemies were dead ... instead of finishing
  my command of picking up my stuff". Shot dead by a skeleton.
- From the doer and task logs:
  - The doer went to the death spot ("Here we are: 198 64 -411"), then pickup with items ["all"]. "all"
    matched no item's name, so the step looked for none of them: done at once, the things still lying there.
  - The errand gear rule (added the round before) then set a wooden sword and a shield first, among the
    skeletons. The shield's iron meant logs, planks, mining and smelting: 17 steps, walked out among them. The
    player's own sword and shield lay at the spot all the while.
- Now:
  - **pickup:** words that mean any ("all", "everything", "my stuff"...), or names that match no item, mean any
    item (walk_commands _ANY_ITEM).
  - **Errand gear:** only a weapon, only when it's quick (ERRAND_GEAR_S, 40 s by the planner's reckoning), never
    a shield (that waits for a fight order, where getting it's worth the time), and never before picking things
    up.
- Tests: "all", "everything", "my stuff" and unknown names pick up anything, "dirt" only dirt; no gear before a
  pickup errand, a sword (no shield) before another. All 514 pass.
- Seen, not changed this round: the cover it went to from the second skeleton kept turning out exposed (cover,
  "exposed", again, while the health ran down from 8 to 0).

## 2026-10-17: no taking over without an order
- The user: "It keeps taking over control of me before I gave it any commands. It shouldn't take over control
  unless I've given it a command it's trying to finish up." (Cover from drowned, a spider, a zombie, with no
  order given.)
- The conductor dealt with danger on an order or when the player had been away from the controls 3 s
  (IDLE_GUARD), added 2026-10-16 after a zombie killed the player standing after "over to you".
- Now that second case is the player's choice, off by default: setting guard_when_idle (.local/settings.json).
  With no order under way, it never takes the controls. The other place that acts by itself, eating when
  hurt, runs only when an order is stopped for being hurt.
- Test: no order, the player away from the controls: not taken over; with guard_when_idle, guarded. All 514
  pass.

## 2026-10-17: mining on with a broken pickaxe
- Play-test: "mine 10 iron ore": "it's trying to break stone and stuff without a pickaxe".
- From the logs and the game: the plan was right (the stone pickaxe picked up where the player died was held:
  "get 10 raw iron"). It mined 8 (raw iron 8 now). Then the stone pickaxe broke, on the walk to the next ore,
  digging the way there through stone. The task looked at the tool only when picking the next ore ('find'),
  so the walk dug on by hand and the ore was punched: 18 s and more on one block, nothing dropping. (The
  bridge reckons each block's break time with the hotbar's best tool: by hand, stone 7.5 s, iron ore 15 s:
  slow, not impossible, so the walk dug on.)
- Now the gather step looks at the tool all the while it walks to a block and mines it, not only when it picks
  the next: gone, the walk's stopped and the step fails "My stone pickaxe broke: making another" (NEEDS_TOOL).
  The plan's made again from what's held, a new pickaxe in it, as when it was found gone before the next ore.
- Test: the pickaxe broken on the way: the walk stopped, the step failed NEEDS_TOOL. All 515 pass.
