# Elden Ring

**Status: connected** (adapter tests 9/9 PASS, 2026-10-08).

## What it needs

| Item | Version | Notes |
|------|---------|-------|
| Elden Ring (Steam) | App Ver. 1.17.1 (`eldenring.exe` 2.7.1.0) | Offline only, through me3 |
| [Attack on Elden Ring](https://github.com/mpere056/attack-on-elden-ring) | a build with the assistant ray block (`AOER_OFF_ASSIST_RAYS`, added 2026-10-08) | Its `Play-EldenRing.bat` starts the game with the bridge; see its `docs/COMPATIBILITY.md` for me3 and the rest |

AoTTG2 does not need to be running. The bridge publishes everything the assistant reads whenever a
character is in the world, whether or not AoTTG2 is linked.

## What the adapter reads

Shared memory `Local\AoER_bridge_v1` (layout: `host/include/bridge_protocol.h` in the bridge repo):

- **State block** (0x100): camera position, target, up, field of view and aspect; player position,
  rotation and flags (alive, dead, busy); area id; the game window's client rectangle on the desktop
  and whether it has the focus (for the fairy overlay).
- **Entity table** (0x200000): characters within 80 m: id, NpcParam id (the type id), model
  (`c3661`), position, hitbox, health, hostile, dead.
- **Assistant ray block** (0x360000): up to 1024 rays per request through the game's own collision,
  served within about 1 ms per frame, separate from the AoTTG2 plugin's ray block so the two never
  collide. Measured: a single ray answers in about 10 ms.

It never writes game memory.

## Knowledge (phase 2)

Three sources, in this order; the assistant says which one an answer came from when it matters.

1. **The running game** (exact numbers): enemy stats from the loaded `NPC_PARAM_ST` table and Site of
   Grace map tiles and positions from `BONFIRE_WARP_PARAM_ST`, read through the bridge's read-only
   mailbox. Tables are found through the game's own `SoloParamRepository` (static pointer at RVA
   0x3D85F58 in 1.17.1, holders at +0x88, 0x48 each; NPC_PARAM_ST is holder 64, BONFIRE_WARP_PARAM_ST
   holder 13, checked by struct name). Layouts from [fromsoftware-rs](https://github.com/vswarte/fromsoftware-rs)
   59fbd3b (MIT); row sizes are checked against the tables. Because these are the tables the game
   uses, the numbers are also right for mods. A copy of the enemy rows goes to
   `.local/eldenring/npc_params.json`. Per enemy type: base HP, runes, damage taken per type
   (standard, slash, strike, pierce, magic, fire, lightning, holy), status buildup needed (poison,
   scarlet rot, bleed, frost, sleep, madness, death blight; 999 = immune). Base values: area
   scaling and buffs can change them in play.
2. **Community name lists** ([Paramdex](https://github.com/soulsmods/Paramdex) `ER/Names`, 14 files,
   about 1.4 MB, `Get-GameData.bat`, kept in `.local/eldenring/paramdex/` because Paramdex has no
   licence): enemy names (6,864 types), where every item is picked up (5,118 map lots), dropped
   (4,992 enemy lots) or sold (1,261 shop rows), all weapons, armour, talismans, items, Ashes of War
   and spells, 419 Sites of Grace, 471 map locations, and named enemy attacks. Open-world pickups
   only carry their map tile in the list; the grace table turns that into "near the Site of Grace X".
3. **The wikis** (Fextralife, Fandom) through web search, only for what the data can't say: attack
   patterns, strategies, lore, questlines. About $0.013 per searched answer.

Never used: the memory-wide scan command. Tried once (2026-10-08), it ran for many minutes inside the
game and blocked the mailbox for everyone.

## Places and coordinates (guiding)

Measured 2026-10-09: in the open world the bridge's published positions are global map coordinates,
position inside the map tile + 256 m x (tile X, 0, tile Z) (zone = area << 24), the same as the
game's place tables. Check: standing in Caelid, the nearest grace came out as Caelem Ruins at 88 m.

- Sites of Grace: `BONFIRE_WARP_PARAM_ST` (holder 43), area, tile and position at 0x20-0x2F.
- Map landmarks: `WORLD_MAP_POINT_PARAM_ST` (0x100 bytes, same offsets), 472 rows.
- Legacy dungeons (Stormveil, Leyndell, caves...) have their own maps;
  `WORLD_MAP_LEGACY_CONV_PARAM_ST` (0x30 bytes) places their points on the world map. Each source map
  has several rows; the first may point at another dungeon map (Stormveil -> area 34), so only rows
  whose destination is area 60 or 61 are used. All such rows agree.
- North is +Z, east is +X. Areas 60 (the Lands Between) and 61 (Realm of Shadow) are separate.
- Landmark rows whose name starts with a "Guidance" region (grace and starlight guidance lights)
  name several places at once and are skipped.
- Results: Forsaken Ruins 151 m west, Gael Tunnel 276 m south-west, Church of Elleh 1.6 km
  south-west, Stormveil Castle 1.9 km west, Leyndell 2.9 km north. Some places have no conversion
  (Leyndell Catacombs, area 35): their position is unknown.

## Findings

- Elden Ring is left-handed with Y up, like Unity: right = up × forward. Confirmed by the
  left/right adapter test.
- Characters' type ids are NpcParam ids (for example 36616040 for model c3661).

## Not read yet

- Player health, the lock-on target, inventory, official names (NpcName text). Lock-on and inventory addresses
  are to be found from community structure maps (fromsoftware-rs, Cheat Engine tables).
