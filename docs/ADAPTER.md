# Game adapter interface

Each game gets an adapter: a small module that reads that game's bridge and translates it into one
shared, game-independent interface (`game_assistant/core/interface.py`); adapters live in
`game_assistant/games/<game>/` and are listed in `game_assistant/games/__init__.py`. The core (look-at, answers,
skills, navigation) only talks to that interface, so it runs unchanged on every connected game.

## Parts

| Part | What it provides | Elden Ring source | Status |
|------|------------------|-------------------|--------|
| Player | position, facing, state (alive, dead, busy), health | bridge state block | position, facing, state: yes. Health: not published yet |
| Camera | position, forward, right and up vectors, vertical field of view, aspect | bridge state block (`camPos`, `camTarget`, `camUp`, `fovYDeg`, `aspect`) | yes |
| Entities | id, type id, model, feet and body centre, size, hostile, dead, health | bridge entity table (characters within 80 m; type id = NpcParam id) | yes |
| Target | the game's own lock-on or interaction target | not read yet | phase 2 |
| Raycast | segments through the game's collision: hit point and surface normal | `CSPhysWorld::CastRay` through the assistant's own ray block (`AOER_OFF_ASSIST_RAYS`) | yes |
| Knowledge | facts by type id: name, resistances, drops; item names | game data extracted to `.local/eldenring/` | phase 2 |
| Places | `locate(name)`: where a named place (or an item's location) is from the player: distance, compass direction, position | Sites of Grace, map landmarks and the legacy-dungeon conversion table, read from the game | yes (2026-10-09) |
| Navmesh | `route(start, goal)`: a walkable route over the game's own navigation mesh (waypoints, whether it reaches the goal, length) | the game's navmesh files, extracted once from the installed game into `.local/` (`Get-GameData.bat navmesh`) | yes (2026-10-10) |
| Actions | hold and release named movements (forward, back, left, right, sprint), taps (interact, jump), camera turns, and whether the player is pressing movement keys themselves (physical, not injected) | Windows SendInput scan codes and relative mouse moves while the game has the focus (ui/win_input.py); a low-level keyboard hook tells the player's own keys from injected ones; key bindings in settings (`walk_keys`) | built 2026-10-10 (phase 4b), not yet tried in game |
| Screen | the game window's position and size on the desktop, and whether a menu or loading screen is up (the fairy overlay hides then) | state block: `winX/Y/W/H`; menus not detected yet | phase 3 |
| Companion body | the fairy's in-world character: spawn and remove, place at a position each frame, ignored by or visible to enemies, strike an entity (damage plus hit reaction), draw an entity's attention | Attack on Elden Ring bridge (to build: spawn, NoDead, hide and pin like the stand-in; the damage queue; the "attract attention" throw) | phase 5 |
| Capabilities | which parts this game has | listed in the adapter | yes |

Later, for games without a single character to steer, two optional parts are added when the first
such game arrives (not before): **units and selection** (strategy, tower defence) and **menus and
turns** (turn-based games).

## Rules

- Positions are metres with Y up, in the game's own world frame (any origin, any handedness).
- The camera carries its own forward, right and up vectors, so the core never needs to know the
  game's handedness. The adapter works them out; the adapter tests check left and right.
- Any field the game cannot provide is `None`; the core must cope (for example, no lock-on target
  means look-at uses geometry only).
- Names and facts come only from the game's own data through `knowledge()`, never from a model's
  memory.
- An adapter only reads state, casts rays and sends input. Game effects that need the game itself
  (the fairy's body, its damage and aggro) are requests to the game's bridge, which carries them out;
  the assistant never writes game memory. Offline single-player only.

## Adapter tests (the gate before the assistant may use a game)

`Check-Adapter.bat [game]` runs them live, asking you to do simple things in the game:

1. **Bridge:** the game is ticking and a character is in the world.
2. **Player position:** walk a few steps; the position must change by more than 2 m.
3. **Camera:** look up, then down; the camera's forward vector must follow.
4. **Raycasts:** a ray straight down hits the ground within 1 m of the feet; a ray along the camera
   (looking down) hits something.
5. **Characters:** at least one character within 40 m is published (skipped if nobody is near).
6. **Left and right:** put that character on the right half of the screen; its screen position must
   come out on the right. A failure means the adapter has left and right swapped.
7. **Look-at:** put the crosshair on a character; the resolver must name it.

A game is **connected** when every test passes. The report is saved to
`runtime/adapter-check-<time>.txt`.

## Games

| Game | Status | Notes |
|------|--------|-------|
| Elden Ring | connected (9/9 PASS, 2026-10-08) | [games/eldenring.md](games/eldenring.md) |
| Minecraft | planned | client-side mod bridge; exact crosshair target from the game |
