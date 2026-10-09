# Status

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
