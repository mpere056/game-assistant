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

**Next: phase 2**, "what am I looking at, and what is it weak to?":
1. Extract NpcParam (names, resistances) from Elden Ring's game data into `.local/eldenring/`.
   Needs a community tool for reading Elden Ring's data files: ask the user before downloading.
2. Find the lock-on target in memory.
3. The Haiku agent with fact tools (`look_at`, `entity_info`) and a text chat window.
4. Gate: near a known enemy, "what is that and what is it weak to?" gives the right name and
   resistances that match the game's data; time to first word measured.
