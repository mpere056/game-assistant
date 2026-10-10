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

## What Navi knows and sees (updated 2026-10-10)

| Source | Gives | How |
|--------|-------|-----|
| The running game | exact enemy stats, positions, graces, landmarks, the player's place | read from memory through the bridge |
| Community name lists (Paramdex) | names of enemies, items and where items are found, dropped or sold | `Get-GameData.bat`, `.local/` |
| **Offline wiki** (Elden Ring Fandom wiki's database dump, CC BY-SA, about 4,500 articles) | what a place holds (graces, bosses, NPCs, loot), boss movesets and strategies, where to get items, questlines, mechanics (Spiritsprings...) | `Get-GameData.bat` downloads it and builds a local search index (SQLite full-text, lookups in milliseconds) |
| **The screen**, on demand | what is visible but not in the game facts: scenery, structures, effects (a gust), items on the ground, signs | one picture of the game window to Haiku (about $0.0002), only when a question needs it |
| The online wiki | last resort | web search, about $0.01 |

Tools: look_at (crosshair facts), nearby_characters, character_info, search_game_data, wiki,
where_am_i, where_is, nearest_places, items_near_me, look_at_screen, guide_to, fairy, web_search.
Next for "what am I looking at": read nearby map objects (lifts, Spiritsprings, chests, statues) from the
game's memory, so they are named exactly without a picture (research in the bridge).

## Runtime: how the pieces run together

One program, `Assistant.bat` (`game_assistant/app.py`), with these threads:

| Thread | Does | Rate | Waits on the network? |
|--------|------|------|-----------------------|
| Window (main) | the chat window; typed and spoken questions both arrive here | events | no |
| Fairy | reads the game (`snapshot()`), moves the companion, places the overlay | 60 per second | never |
| Overlay | the fairy's own transparent window and its message loop | 120 per second | never |
| Speaker | Windows voice; speaks each sentence as soon as it has streamed | events | no |
| Push to talk | watches the talk key, records the microphone, runs Whisper | while the key is held | no |
| One worker per question | an instant command first; otherwise the Haiku agent and its tools | per question | yes (Haiku) |

The adapter is shared: ray requests and mailbox reads are serialised with locks, because the fairy
thread and the agent can ask at the same moment. A question never blocks the fairy, and "stop" works
while an answer is still coming.

## Instant commands (no model, no network, no cost)

`game_assistant/core/commands.py`. Polite wrappers are ignored ("hey fairy, come back to me please").

| Say or type | Effect |
|-------------|--------|
| stop, wait, stay, hold on | speech stops, any task stops, the fairy holds where it is |
| come back, come here, follow me, back | the fairy returns to your side; tasks stop |
| go there, go to that | the fairy flies to what is at the crosshair (an enemy, or the spot) |
| show me that, point at that | the fairy flies there for a few seconds, then comes back |
| quiet, shush, stop talking | speech stops |

Everything else goes to Haiku, which can also move the fairy through its `fairy` tool (go, show,
lead, come back; targets: the crosshair, a nearby character, the nearest enemy).

## Testing without and with the game

1. **Offline tests** (`Run-Tests.bat`): look-at, spending cap, the param reader on a synthetic table,
   the facts, the fairy's movement (follow, show, leash, menus), navigation around a wall, commands.
2. **The demo scene** (`Assistant.bat demo`): a pretend game over the desktop (a walking character,
   one pretend wolf, a wall) to see and steer the fairy, use voice and the chat, with no game.
3. **Live checks with real Haiku calls** (`tests/agent_live_check.py`, pennies): answers from facts,
   item locations, wiki answers, moving the fairy.
4. **The game gates** in the phase table, played by you. A phase is done only when its gate passes.

## Spending cap

$3 per hour, rolling 60-minute window (`game_assistant/core/spend.py`, ledger in
`runtime/assistant-spend.jsonl`). Warns once at $2.40; at $3 hosted calls stop until the window
drops under the cap, and local features keep working. Expected use (about 50 questions plus an hour
of tasks) is well under $0.25, so reaching the cap means something is looping. Every call also has
a maximum output length.

## The fairy companion

The assistant gets a body: a small glowing fairy, like Navi in Zelda, that floats around your
character. It is the thing you talk to and that talks back; there is no text over it (the chat
window stays for typing and for a written record). Added 2026-10-08.

**What it does**

| Ability | How | Phase |
|---------|-----|-------|
| Floats beside and around you, bobbing and drifting like Navi; never blocks the crosshair | Code (companion controller) every frame | 3 |
| Flies at about 10 m/s (faster than running, slow enough to follow by eye; keeps up on Torrent), with a fading trail of about 2 s while it moves | Companion speed and acceleration caps; trail windows in the overlay | 3 |
| Shows what it is talking about: flies to the thing at the crosshair or the enemy it describes, then comes back | Look-at resolver and entity positions | 3 |
| "Go to X": flies to an enemy, an item spot, a grace or a place, but never further than the leash (75 m) and stays in view; if X is further it waits at the edge and points | Code; X comes from look-at, nearby characters or search_game_data | 3 |
| Glows or pulses while it speaks; its voice comes from where it is on screen (stereo pan) | Voice wrapper (phase 2b), then panned from its screen position | 2b + 3 |
| "Lead the way" to something nearby: flies ahead along a route you can walk, stopping when you fall behind | Navigation (walkability grid and A*) | 4 |
| "Take me to the Forsaken Ruins" (or to an item: "take me to the Moonveil"): flies ahead toward a place anywhere in this world, along a route you can walk (planned 150 m at a time from the ground itself, around cliffs), as far ahead as you can still see it (up to 100 m, never behind a hill), hovering like a beacon, and says when you arrive; a marker at the screen edge shows where it is when it is off-screen | Place positions from the game's own map tables, converted to your position | 4 (built) |
| Normally invisible to enemies; on command visible to them like a player, to draw an enemy's aggro | An in-world body through the game's bridge | 5 |
| Attacks on command: a tackle (dash in, hit, back off) that damages the enemy | In-world body plus the bridge's damage path | 5 |

**How it is drawn: two layers.**

1. **Look (game-independent, phase 3).** The fairy is drawn by the assistant as a small transparent,
   click-through, always-on-top window over the game, the same technique as Attack on Elden Ring's
   crosshair. Its 3D position is projected through the game's camera every frame; a ray from the
   camera tells when a wall is in front of it, and it fades out there. This works in any game that
   reports a camera, so Minecraft gets the same fairy. It hides while the game is in a menu or loading.
2. **Body (per game, phase 5).** For enemies to see and fight it, the game needs a real character at
   the fairy's position. In Elden Ring the bridge spawns a hidden ally character that cannot die (the
   same tricks the bridge already uses for the stand-in Tarnished and test enemies), keeps it pinned
   to the fairy's position, and switches it between "ignored by enemies" (the default) and "visible
   to enemies". Tackles send damage through the bridge's existing damage path, with a hit reaction;
   drawing aggro can also use the game's own "attract attention" throw that the bridge already uses.
   The fairy's look stays the overlay either way.

**Rules for the fairy**

- Movement is code, never a model: a model only names a target ("the dog on the left").
- It moves only when you ask; it never flies off on its own (it used to fly to whatever it was
  describing; the user didn't want that).
- It talks like Navi: bright, quick, very short, and answers only what was asked.
- It is never further than the leash from you and comes back if you move away; "come back" and
  "stop" are instant local commands.
- It only fights or draws aggro when you tell it to; it never starts a fight on its own.
- The game effects (spawning the body, damage, aggro) are done by the game's bridge on request, in
  offline single-player only. The assistant itself still never writes game memory.

## Navigation from the game's own navmesh (added 2026-10-10)

Raycasting the ground (nav.py) can't be made reliable enough for the character to walk itself: it
sees 150-300 m at most, coarse cells make cliffs look climbable, far ground isn't loaded, and caves,
bridges and water are invisible to it. Elden Ring ships the navmeshes its enemies use; we read those.

1. **Extract once** (`Get-GameData.bat navmesh`, about 20 minutes, 230 MB in `.local/`): the map
   blocks' navmesh containers are read out of the installed game's archives (read-only; archive
   format and public keys from Smithbox, MIT), the Havok navmesh pieces are converted by Soulstruct
   (GPL-3.0, its own Python 3.13 environment in `.local/tools`, never in this repository) into one
   compact file per block: 695 blocks, 4.5 million faces (150 open-world tiles are sea: empty).
2. **Plan** (`core/navmesh.py`, game-independent): the face under you, A* over faces, the funnel
   algorithm for straight legs, corners kept 0.7 m off walls. A goal that can't be walked to (an
   evergaol arena, behind a fog wall, up a lift) gives a route to the nearest walkable point.
3. **Elden Ring layer** (`games/eldenring/navmesh.py`): open-world tiles and dungeons are put in
   world coordinates and joined where their edges meet; `adapter.route(start, goal)`
   (capability `navmesh`). Navi's guiding uses it first and raycasting only where there's no navmesh.

Measured from the player near Stormhill (2026-10-10): Stormhill Evergaol 546 m walking for 78 m
straight (the way up the plateau the raycast planner couldn't find), 4 s to plan; Gatefront Ruins and
Church of Elleh 0.03 s; Stormveil Castle stops 146 m short (the castle meshes join the world only where
they touch), 13 s.

Next (user agreed 2026-10-10, after comparing with Minecraft's Baritone): join the blocks once at
extraction time (no stitching per route), and plan with a time limit (~1 s): start on the best partial
route toward the goal and plan further while moving, so even long trips start at once.

Not yet: jumps, ladders, lifts and doors (Havok "user edges" are in the files, not used yet); what
two face markings mean (one marks evergaol arenas); underground areas without a world conversion;
faster mesh building for long routes; the DLC (not installed).

## Two talk keys: ask Navi, or command your character (added 2026-10-10)

| Key (setting) | What it is for | Who acts |
|---------------|----------------|----------|
| **F9** `push_to_talk_key` | Talk to Navi: questions, and Navi's own moves ("lead me to the nearest grace", "go to that") | Navi answers and flies; your character is yours |
| **F10** `command_key` | Orders for your character: "go to the nearest site of grace", "walk to that ruin", "follow Navi", "stop" | your character walks on its own, and Navi leads the way along the same route |

F8 is not used: the Attack on Elden Ring bridge uses it to switch between the games.

**How a command runs**

1. **Hear it.** The same push-to-talk and speech recognition as F9, tagged as a command.
2. **Understand it.** Short, clear commands are handled on this PC ("stop", "follow Navi"). Anything
   else goes to Haiku with a command prompt and the same game tools, which turns it into one action:
   walk to a place, a thing at the crosshair, a character, or the nearest grace or landmark.
3. **Ask when unsure, through Navi.** If two targets are about equally good (two graces within 15 % of
   each other's distance, two enemies at the crosshair) or the order is vague ("go over there"), Navi
   asks one short question out loud. The command waits; the next thing you say or type, on either key,
   answers it. Ten seconds of silence cancels.
4. **Plan the route.** The same walkable route as Navi's guiding (`nav.plan`, WIDE or FAR), re-planned
   as you go. Navi flies ahead along that same route, so what you see and where you walk agree.
5. **Walk.** The auto-walk controller (code, every frame) steers along the route by holding the game's
   movement keys: it works out the direction to the next waypoint relative to the camera and holds the
   matching W/A/S/D combination (eight directions); it runs (holds sprint) on long straight stretches,
   which the user chose (setting `auto_walk_sprint`, on).
   In Attack on Elden Ring's linked mode the same keys reach AoTTG2, which moves the character.
6. **Watch.** The monitors from "Watching a running skill": arrived (15 m across and 6 m in height),
   stuck (less than 0.5 m in 2 s: re-plan; twice: stop and say so), an enemy within 8 m of you or the
   route (keep walking, as the user chose; Navi warns: "Careful, enemies ahead!"), a fall, a menu or
   loading screen (pause).
7. **You stay in charge.** Pressing any movement key yourself, saying or typing "stop" on either key,
   or opening a menu stops the walk at once and releases every key it held. Physical key presses are
   told apart from the controller's own (Windows marks injected input), so the takeover check can't
   be fooled by the walk itself.

**Why it fits:** the route planner, place lookups, nearest-place search, arrival rules, Navi's guiding
and the monitors already exist or are planned; the new parts are the second key, the command prompt,
the clarification turn, the keyboard steering and the takeover check. Commands are also where skills
(phase 6) will start from ("sort my items" on F10 runs a skill).

**Limits at first:** no jumping, climbing, ladders, lifts, swimming or riding Torrent; routes that need
them are not found (Navi says so). Elden Ring must have the keyboard focus for its keys to work.

## Voice

A wrapper around the same agent; the text window stays. Built 2026-10-09:

- **Talk:** hold the push-to-talk key (default F9; it works while the game has focus). The microphone
  is recorded while held; on release, faster-whisper (`base.en`, int8, on the CPU so the GPU stays
  with the game) turns it into text. Each game gives a vocabulary hint, so "Moonveil" is not heard as
  "Moonvale". Measured: a 5-second question transcribed in 0.7 to 0.9 s.
- **Hear:** the fairy's own voice (`voice/fairy_voice.py`): Kokoro, a local neural voice on the CPU
  (voice af_heart), raised to 440 Hz and made smaller (voice size 1.3) with the WORLD vocoder, which
  sets pitch and size separately, cleaned of breath noise (harvest pitch tracking, less breath,
  280 Hz high-pass, a gate), with a sparkle before each answer and panned to where the fairy is on
  screen. Chosen by ear by the user from several rounds of samples (Navi measures about 558 Hz; 440
  sounded best). The sparkle plays the moment an answer starts; the text is spoken in pieces (sentences, cut
  at commas when long) made by two workers in parallel and played in order through one continuous
  stream: first words about 0.5-0.9 s after the text starts, under a second of silence mid-answer. Windows' own voice stays as the fallback (`voice_engine: windows`).
- **Interrupt:** a new question, "stop" or "quiet" cuts speech off at once.
- **Looked into, not used (2026-10-09):** the only Navi-trained voice found is a fan-made RVC voice
  converter trained on about two seconds of game audio and unfinished; heavy (PyTorch), a grey area
  (Nintendo audio, the voice actor's performance) and unlikely to beat the tuned voice. Real Navi
  exclamations ("Hey!", "Listen!") from the player's own recording before the answer remain an
  option. Hosted real-time voice only if a cheap option appears; it would count toward the cap.
- Settings (talk key, microphone, speech model, voice, pitch, size, speed, sparkle): `.local/settings.json`.

- Push-to-talk key; speech to text on the CPU with an open Whisper model (faster-whisper);
  Windows speech recognition as the fallback.
- Text to speech on the CPU with a small open voice (Piper or Kokoro); Windows voices as the fallback.
  Each sentence is spoken as soon as Haiku has streamed it.
- Spoken answers are one or two sentences; details stay in the text window.
- Push-to-talk or "stop" interrupts speech at once.
- Hosted real-time voice only if a cheap option appears; it would count toward the cap.
- Check that the voice models don't cost Elden Ring frames.

## Phases

Gates are passed in order. Code for a later phase may be built ahead (tested offline and in the demo
scene), but a phase counts as done only when its game gate passes.

| # | Phase | Gate | Status |
|---|-------|------|--------|
| 1 | Adapter interface, Elden Ring adapter, adapter tests, spending cap; measure Haiku 5.5 speed | `Check-Adapter.bat` all PASS; `Measure-Haiku.bat` numbers recorded | **Done 2026-10-08.** Adapter tests 9/9 PASS. Haiku 5.5: first word 524 ms median, tool call 551 ms, adaptive thinking at low effort (chosen); 20 calls $0.0011 |
| 2 | "What am I looking at?": look-at resolver (built in phase 1), lock-on target, NpcParam names and resistances extracted from game data to `.local/eldenring/`, Haiku agent with fact tools, text chat window | Ask "what is that and what is it weak to?" near a known enemy: correct name, resistances match the game's data; time to first word measured | **Game test passed 2026-10-09** (user: "working really well"). Exact numbers from the game's memory; names, item locations, drops, shops and places from community lists; attack patterns, strategies and lore from a wiki search |
| 2b | Voice (see above) | Spoken question and answer; frame rate unchanged | **Game test passed 2026-10-09** (a whole session by voice). Offline: Windows voice into Whisper exact with the game vocabulary, 0.9 s |
| 3 | Fairy companion, look and movement: overlay renderer (projection, wall fading, hidden in menus), companion controller (float, follow, leash), fly to what it talks about, "go to X", "come back", instant "stop" | The fairy follows you for 10 minutes without blocking the crosshair or drifting off; "go to that enemy" flies to the right one and stays within the leash | **Built 2026-10-09**, tested offline and in the demo scene (follow, show, go, leash, menus, instant commands, the agent's fairy tool). First game test 2026-10-09: looks and moves well; leash raised to 75 m on request. Gate (10 minutes, crosshair, right target) still to confirm |
| 4 | Navigation, part 1: raycast walkability grid, A*, the fairy leads the way along it, places and nearest places, guiding to far places | The fairy leads you between two points around a wall and waits when you fall behind; guiding to a named or nearest place gets you there by a walkable way | **Route planning and "lead the way" built 2026-10-09** (offline: around a wall in under 1,200 rays). Guiding to far places built 2026-10-09: every Site of Grace and map landmark (and legacy dungeons through the game's conversion table) gets a position relative to you; tested in game (Forsaken Ruins 151 m west, Stormveil Castle 1.9 km west). **2026-10-10: routes from the game's own navmesh** (see "Navigation from the game's own navmesh"); not yet tried by the user in game. Not built: the character walking itself, fast travel |
| 4b | Commands and auto-walk: the F10 command key, the command prompt, clarification questions through Navi, keyboard steering along the route with Navi leading, monitors (arrived, stuck, enemy, fall, menu), instant takeover | "Go to the nearest site of grace" on F10 walks there and stops within 15 m (and 6 m in height); a movement key or "stop" halts it in under 100 ms; two equally near graces make Navi ask which | not started |
| 5 | Fairy in the world (Elden Ring bridge work, in Attack on Elden Ring): hidden, unkillable ally body pinned to the fairy; ignored-by-enemies / visible-to-enemies switch; draw aggro; tackle attack through the damage path | Invisible to enemies by default; on command one chosen enemy turns to it; a tackle damages that enemy with a hit reaction and the fairy backs off | not started; research steps below |
| 6 | Skills: sandbox, skill library, skill writer (Sonnet 5.5), dry runs | One sentence makes a working skill, such as sorting items | not started |
| 7 | Minecraft: new bridge (client mod) and adapter only; the fairy's look works unchanged | Questions, the fairy and `goto()` work with no change to the core | later |

## Phase 5 research (Elden Ring bridge), before writing code

Done in Attack on Elden Ring, step by step, each tested in game before the next:

1. **Spawn and hold a body.** Reuse the bridge's character creation (the debug creator it uses for
   test enemies) to spawn one ally character; pin it to a position each frame with gravity off and
   NoDead on, and hide its model, as the stand-in Tarnished does. Check that it never takes damage
   and never falls.
2. **Ignored vs targeted.** Find what makes enemies ignore or notice a character: team type, the AI's
   targeting flags, or despawning it when not needed. Measure: with "ignored", a nearby enemy never
   turns to it; with "visible", it does.
3. **Draw aggro.** Make one chosen enemy turn to the body: first the bridge's existing "attract
   attention" throw from the body's position, then team switching if that is not enough.
4. **Tackle.** Move the fairy into the enemy and back (the companion's new "tackle" mode), and send a
   damage request through the bridge's damage queue with a hit reaction. Damage per tackle is a
   setting.
5. **Safety.** The body is removed when the assistant closes or the game loads, and never exists in
   online play (the bridge only runs offline anyway).

## What to measure

| Metric | Target (starting guess) |
|--------|-------------------------|
| Time to first word of an answer | about 1 s |
| "stop" to the character stopping | under 100 ms |
| Look-at accuracy on fixed test spots | 9 out of 10 |
| Cost per active hour | under $0.25 |
| Adapter tests | all pass before using a game |
| Fairy overlay cost | under 1 ms per frame on the CPU; Elden Ring's frame rate unchanged |
| Fairy screen position vs the game | within a few pixels while the camera turns (no visible lag) |

## Open questions

- Can Haiku 5.5 write simple skills well enough to skip Sonnet 5.5 for them?
- Where Elden Ring keeps the lock-on target and the inventory in memory (start from fromsoftware-rs
  and Cheat Engine tables).
- Which Minecraft version and mod loader to pin, when we get there.
- Decided 2026-10-10: command key F10; auto-walk runs on long stretches; near enemies it keeps going
  and Navi warns. Open: should it ever call Torrent?
- The fairy's look: colour, size, a name? Its leash distance (75 m). Colour, size and leash are
  settings already (`.local/settings.json`).
- Menus in Elden Ring: the fairy hides during loading screens and when the game is not in front, but
  in-game menus are not detected yet.
