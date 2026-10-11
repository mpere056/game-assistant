# Status

**2026-10-10, latest: F10 orders and auto-walk (phase 4b), learned links, ladders and lifts.** Built and
tested offline only (73 tests); the user chose to test later. Hold F10 (or type "/go to ...") and say
"go to the nearest site of grace": the character walks Navi's route by itself (mouse turns the camera,
W/A/S/D, Space to run on long straights), climbs ladders, waits on lifts, jumps where the game marks
jumps; any movement key you press yourself takes over at once. The route planner also learns
connections from where you walk. First things to check in game: does the camera turn the right amount
(mouse sensitivity is learned), do Space-to-run and E-for-ladders match your key bindings, does
takeover feel instant.
Also: a broken line in app.py (from the ladder change) stopped the app from starting; fixed in
mpere056/game-assistant#8 with a test that compiles every file.

**2026-10-10, latest: walking routes from the game's own navmesh.** Elden Ring's navmeshes (what its
enemies walk on) are extracted from the installed game into `.local/` (`Get-GameData.bat navmesh`,
done on this PC: 695 blocks, 4.5 million faces) and Navi's guiding plans over them. Checked against the
running game without playing: the way up to Stormhill Evergaol (546 m for 78 m straight, which the old
planner couldn't find). Not yet tried in game by the user. Next: try it; user edges (jumps, ladders,
lifts); faster planning of long routes; then phase 4b (the character walks the route).
Also today: offline wiki, screen pictures of the game window only, the in-game speech bubble.

**2026-10-08: phase 1 done.**

- The shared core: interface, look-at resolver, $3/hour spending guard. 12 offline tests pass
  (`Run-Tests.bat`).
- Elden Ring adapter: **connected**, 9/9 live adapter tests PASS
  (bridge live; ground ray 0.02 m from the feet in 10 ms; left/right correct; look-at named
  c3661 / NpcParam 36616040 at 10.7 m, confidence 0.9).
- Claude Haiku 5.5 from this PC: first word 524 ms median, tool call 551 ms, with adaptive
  thinking at low effort (chosen). 20 calls cost $0.0011.
- The code moved here from the Attack on Elden Ring repository on 2026-10-08 (that repository
  keeps the bridge, including the assistant ray block). The live tests above ran before the move;
  after the move only the offline tests and a live read of the bridge (title screen) were run.

**2026-10-09, latest: the fairy's voice.** Kokoro neural voice af_heart at 440 Hz, voice size 1.3,
breath-noise fix, sparkle, panned to the fairy; picked by the user from samples. In the app it starts
speaking about 0.6 s after an answer starts streaming. Not yet heard in game.

**2026-10-09, later: Navi-style answers, no flying unless asked, guiding to far places.** Tested
against the running game with real Haiku calls (not yet played by the user):
"Can you see something in front of me?" -> "Yes! A Large Putrid Corpse, about 46 metres ahead.";
"Lead me to the Forsaken Ruins." -> "This way! About 150 metres west, slightly downhill." (the fairy
guides, up to 100 m ahead). 31 offline tests pass.

**2026-10-09: first game test of the whole assistant went well** (questions, the fairy; leash raised
to 75 m on request). Phases 2, 2b, 3 and part of 4 were built and tested without the game first. 27 offline tests
pass; the demo scene and live API checks pass. Nothing new has run in Elden Ring yet.

- Phase 2, questions: Haiku agent with look_at, nearby_characters, character_info,
  search_game_data and wiki web search; enemy stats and grace tiles read from the game's memory
  through its param repository; names and item locations from the Paramdex lists.
- Phase 2b, voice: used for a whole game test on 2026-10-09 ("working great"). Hold F9, faster-whisper base.en on the CPU with an Elden Ring vocabulary hint
  (exact on a test sentence, 0.9 s); answers spoken by Windows' Zira sentence by sentence.
- Phase 3, the fairy: overlay window (glow and wings, 0.1 ms per frame in the demo), companion
  controller (follow off the crosshair, show, go, leash 75 m, hidden when the game is not in front or
  loading), instant commands, the agent's fairy tool.
- Phase 4, part: route planning (raycast grid, A*, lazy wall checks) and "lead the way".

**Next, with the game running:**
1. Check the enemy and grace tables read correctly (the first test showed the old memory search
   was too slow; the param repository path has not run in game yet).
2. `Assistant.bat`: questions, then the fairy (does it sit beside the head, off the crosshair, and
   line up with the game while the camera turns?), then voice (F9), then "lead me there".
3. Phase 5 research (the fairy's body in the world) only after phase 3's gate passes.

Known gaps: in-game menus are not detected (the fairy hides only during loading and when the game
isn't in front); lock-on target not read; the character doesn't walk itself yet.
