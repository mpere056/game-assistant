# Minecraft (Java Edition 26.3)

The assistant talks to Minecraft through the **Game Assistant Bridge**, a small client-side Fabric mod in
`bridges/minecraft/` (kept in this repository at the user's choice, 2026-10-12). It is read-only: it reports
the game's state and casts rays; it never changes the world. It only works in single-player: in a
multiplayer world it publishes nothing.

## What the bridge reports

Every frame, on the game's own thread (a mixin at the end of `Minecraft.runTick`), while the assistant is
asking:

- the player: position, eye position, yaw and pitch, health, food, armour, air, held item, dead, creative; the
  hotbar, the selected slot, the first slot of blocks to build with and how many
- the crosshair's block: also the hotbar slot that breaks it fastest
- the camera: position, forward / up / left vectors, the current field of view
- the creatures within 64 blocks: id, type (`minecraft:zombie`), name, position, size, facing, hostile
  (`Enemy`), health, dead
- what the crosshair is on, as the game picks it: a creature, or a block (its name, id, position, face)
- the window: position, size, focus; whether a menu is open or the game is paused
- the world where the player stands: biome, time of day (the overworld clock), day, rain, thunder, light

and on request, rays through the blocks' collision shapes (`Level.clip`, done at the next frame).

## The connection

`127.0.0.1:47821`, one JSON request per line, one JSON line back:

| request | answer |
|---|---|
| `{"op":"snapshot"}` | the latest frame (above) |
| `{"op":"raycast","rays":[[x1,y1,z1,x2,y2,z2],...]}` | `{"hits":[[1,x,y,z,nx,ny,nz,"minecraft:stone"],[0],...]}` |
| `{"op":"grid","min":[x,y,z],"size":[sx,sy,sz]}` | `{"data": base64, "hard": base64}` (hard: each block's time to break with the hotbar's best tool, 4 ticks a unit, 127 unbreakable, +128 falls): one byte per block (air, water, danger, solid, low, tall, climbable, door, partial, unloaded, powder snow), x fastest, then z, then y |
| `{"op":"names"}` | the game's structure, structure tag and biome ids |
| `{"op":"locate","kind":"structure"\|"biome","id":"minecraft:village"}` | `{"found":true,"pos":[x,y,z]}` (the game's own /locate, on the single-player server's thread) |
| `{"op":"find_blocks","ids":["minecraft:stone"],"max":8}`, `{"op":"block_ids"}` | the nearest blocks of those kinds (loaded world, up to 64 blocks out), nearest first; every block id |
| `{"op":"recipes"}`, `{"op":"sources"}` | every crafting recipe (cells of the items that fit, result, count); each block's drops and the least tool for them |
| `{"op":"ping"}` | `{"pong":true,"frame":n}` |

The adapter (`game_assistant/games/minecraft/adapter.py`) keeps one connection per thread (a raycast waits
for the next frame). Minecraft's world is right-handed (+X east, +Z south); the core's headings take
"clockwise seen from above" as right, so the adapter mirrors X both ways.

## Setting up (done 2026-10-12)

1. **Build tools** (inside the project, nothing system-wide): Eclipse Temurin JDK 25 in
   `.local/tools/jdk-25.*` (from adoptium.net); Gradle, Fabric Loom and Minecraft's own files are fetched by
   the build into `.local/gradle`.
2. **Fabric** in the launcher: the official Fabric installer 1.1.2, `client -mcversion 26.3 -loader 0.19.5`,
   which adds the "fabric-loader-26.3" installation. Fabric API 0.162.0+26.3 (from Modrinth, checksum
   checked) in `%APPDATA%\.minecraft\mods`.
3. **The bridge**: `Build-MinecraftBridge.bat` builds it and copies `gabridge-*.jar` into the mods folder.
4. **The game's data**: `Get-GameData.bat minecraft` copies the game's own data (recipes, loot tables, tags,
   structures, the English names: about 10 MB) out of the installed client jar (only read) into
   `.local/minecraft/<version>/`, never committed (games/minecraft/data.py). From it: what creatures drop,
   killed or shorn (wool from sheep, string from spiders: what a bed takes).

Versions (from Fabric's example mod, 26.3 branch): Minecraft 26.3, Fabric Loader 0.19.5, Loom 1.18-SNAPSHOT,
Fabric API 0.162.0+26.3, Gradle 9.7.1, Java 25. Minecraft 26.x isn't obfuscated: the mod uses the game's own
names (checked with `javap` against the 26.3 jars: `Camera.forwardVector()`, `Gui.screen()`,
`GameRenderer.mainCamera()`, ...).

## Using it

1. Start Minecraft with the **fabric-loader-26.3** installation; open a single-player world.
2. `Assistant.bat minecraft`
3. `Check-Adapter.bat minecraft` runs the live adapter tests.

## Status

| Part | Status |
|---|---|
| Bridge mod builds | yes (2026-10-12) |
| Adapter (player, camera, creatures, crosshair, rays, window, menus) | built; tested against a stand-in bridge (tests/test_minecraft.py); not yet in the game |
| Questions ("what is that?", nearby creatures, Minecraft Wiki search) | built, not yet tried |
| The fairy (overlay) | should work unchanged; not yet tried |
| Places: "where am I" (biome, coordinates, time, weather), "where is / take me to the nearest village" (any structure, structure group or biome, as /locate) | built 2026-10-13, not yet tried |
| Walking, Baritone's way (docs/MOVEMENT.md): routes of one-block moves (walk, step up, step down, fall, diagonal, pillar, dig down, jump a gap: over 1 or 2 walking, over 3 at a run, up one over a gap at a run), costs in game ticks (`games/minecraft/pathing.py`); each move done frame by frame by its own rules (`games/minecraft/moving.py`): what's in its way broken first, as the world says each frame (bridge v20); swims, ladders; mines through and builds (bridges, pillars, steps) with throwaway blocks; never next to water or lava, under sand or gravel; round hostile mobs where it can (Baritone's avoidance); a block to gather chosen by whose way is quickest | rebuilt 2026-10-16; tested end to end in a world with the game's movement (tests/test_moving.py); not yet tried in game |
| Getting items: "get me a stone pickaxe", "mine 10 cobblestone", "craft sticks" (core/crafting.py plans from the game's recipes and loot tables; core/tasks.py gathers, places a crafting table, clicks the grid) | built 2026-10-13, tested on stand-ins; not yet tried in game |
| Fighting (core/melee.py): the best weapon in hand; aim with the camera; swing only fully charged, with the crosshair on it and in reach; back off after each hit; zig-zag in on shooters; a shield up against a drawn bow or a coming hit; run from a swelling creeper till 7 blocks off; over to the player at 30% health. During tasks, a hostile one on the player is fought off first, then the task goes on | built 2026-10-13, tested in a simulated arena; not yet tried in game |
