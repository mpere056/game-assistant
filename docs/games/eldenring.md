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
  rotation and flags (alive, dead, busy); area id.
- **Entity table** (0x200000): characters within 80 m: id, NpcParam id (the type id), model
  (`c3661`), position, hitbox, health, hostile, dead.
- **Assistant ray block** (0x360000): up to 1024 rays per request through the game's own collision,
  served within about 1 ms per frame, separate from the AoTTG2 plugin's ray block so the two never
  collide. Measured: a single ray answers in about 10 ms.

It never writes game memory.

## Findings

- Elden Ring is left-handed with Y up, like Unity: right = up × forward. Confirmed by the
  left/right adapter test.
- Characters' type ids are NpcParam ids (for example 36616040 for model c3661). Names and
  resistances need the game's data extracted (phase 2).

## Not read yet

- Player health, the lock-on target, inventory, grace locations. Lock-on and inventory addresses
  are to be found from community structure maps (fromsoftware-rs, Cheat Engine tables).
