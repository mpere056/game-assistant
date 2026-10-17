# Moving in Minecraft, the way Baritone does it

The user asked for it on 2026-10-16: "look into https://github.com/cabaletta/baritone, and please do things the
way they do". Baritone is the quality bar for walking in Minecraft. This is how its model is used here.

## Licence

Baritone is under the LGPL-3.0. This repository is MIT and public. So Baritone's code is studied, not copied
or translated line by line. What's taken is:

- the design (moves that know their cost and how to do themselves; a path executor; goals);
- the game's physics numbers (ticks a block walked, fall times, jump heights);
- Baritone's default penalties.

The code is our own. Porting its code directly would make those files LGPL; that's the user's call to make.

## The model

**Moves** (`games/minecraft/pathing.py`). A route is a list of one-block moves. Each move knows its cost, the
blocks to break first, and the block to put down:

| Move | What it is |
|---|---|
| traverse | walk on (or bridge the floor: a block put against a side, or crouched backwards at the edge) |
| ascend | step up one: jump, breaking what's in the way including the block over the head; a block put first as the step if there's none |
| descend | step down one |
| fall | walk off an edge to the ground up to 3 down, into water from any height, or onto a ladder that catches the fall |
| diagonal | walk diagonally; round a corner along the wall when one side is blocked |
| pillar | jump and put a block under the feet; or climb a ladder; or swim up |
| downward | dig the floor and drop; or down a ladder |
| parkour | jump a gap: over 1 or 2 walking, over 3 at a run, up one over 1 or 2 at a run |
| leap | jump a gap any way (ours, not Baritone's): slanted, up one, level or down one, up to 4.6 blocks across; see below |

**Costs** are in game ticks, from the game's physics:

- walking a block takes 20/4.317 ticks, sprinting 20/5.612;
- a fall of n blocks takes the ticks gravity takes;
- Baritone's penalties: a jump 2, a block put down 20, a block broken 2 plus its break time.

The search is A*. Its estimate is Baritone's (a mix of straight and diagonal distance at sprint speed, a jump a
block up, half a two-block fall a block down), weighted a little: Python is slower than Baritone's Java. Out of
time, the best part of the way so far is taken: the first of a few weights whose end is over 5 blocks from the
start, as Baritone does.

**Several goals at once.** When gathering, one search runs toward the nearest 16 candidate blocks. The one its
way reaches first is the one taken, not the nearest as the crow flies. That's Baritone's mining.

**Hostile mobs** (Baritone's "avoidance"). A move into a cell within 8 blocks of a hostile mob costs 1.5 times
as much, compounding where spheres overlap. Spiders count only in the dark; neutral mobs only when provoked.

**Doing it** (`games/minecraft/moving.py`, the `Mover`). Each frame:

1. Which move the feet are in. Each move has the blocks the feet may be in while doing it. A later one means
   skip ahead; an earlier one means go back. Off the route (over 3 blocks, or over 2 for 3 s), plan again.
2. The move's blocks to break: each looked at and mined until the world says it's gone. The bridge sends the
   blocks round the feet every frame (bridge v20).
3. The move's own keys. Each move says when it's done (the feet in its block, on the ground). If it can't be
   done (fallen below where it started), plan again. A move taking 5 s longer than reckoned: plan again.

**Jumps any way** (the leap, our addition). Baritone's parkour goes along x or z only. On 2026-10-16 the player
jumped from a hill onto a crafting table two across and three along, one lower, at a walk, and back up at a run,
while the planner went round through the ravine below in 17 moves. A leap lands on a block up one, level or down
one (not lower: the fall from the jump's top would hurt).

- **Reach.** Each is reckoned with the game's own jump (0.85 of it counted on): walking if a walking jump
  carries far enough, else at a run.
- **Room.** The body's room is checked along the whole flight: taken off at the latest at full speed and just
  fast enough, and as early as it goes.
- **Speed.** All of that is worked out once for every way (`LEAP_TABLE`). Inside the grid, each block is looked
  up by its place in the grid's bytes. A planner step takes 1.45 times as long with leaps.
- **Cost.** A leap costs 6 ticks more than a straight jump, so a straight one is taken where it does as well.
- **Doing it.** The Mover runs along the line from the start's middle to the landing's, eases off if too fast
  to come down on it, and goes up at the latest just past the start block's edge. It lands crouched, and walks
  back on, crouched, if it slid off.

**Fewest jumps.** `check_way` most_jumps n: the quickest way if it has no more jumps than that, else the way
with the fewest (each jump over a gap costs 400 ticks more: `FEWEST_JUMPS`).

## Where this differs from Baritone, and why

Baritone runs inside the game. It sets the keys and the view each tick, at once. We press keys and turn the
mouse from outside, a frame or two late. So:

- **Jumps** are pressed ahead of the edge by the current speed times the key delay (0.07 s). A short jump goes
  up where it will come down in the landing's middle: the flight is reckoned from the speed with the game's
  air physics, never later than the edge allows. (Baritone jumps at the edge; a walking jump over one gap flies
  2.4 blocks, and overshot a one-block landing into the next gap.)
- **The jump key is held** until the feet leave the ground. A press shorter than the game's tick can fall
  between two ticks and never be seen.
- **Running.** In the game a run lasts until forward is let go. Baritone turns it off from inside; here,
  forward is let go for a frame before a walking jump or a fall, if still running.
- **Landings** with nothing to stand on just past them, where the way doesn't carry on straight, are made
  crouched. The game's crouch never goes over an edge. (A jump onto a pit's rim slid off it, 11 blocks down,
  2026-10-16.)
- **Falls** are steered in the air by the movement keys alone, not by turning. Turning round takes too many
  frames from outside.
- **The view.** The heading is read from the camera's right side when it looks near straight up or down (its
  forward has no level part there). Walking with the view tilted over 50 degrees brings it back level. On a
  jump's block, the turn to face the landing is made crouched in place. A running jump goes up at the edge.
- **Jumps at a run** go up only when the game says the player is running: the run's boost is most of the
  reach (from a standstill at a run, 3 blocks; at a walk's start, 1.1). Not running at the edge: stopped there
  crouched, backed off into the block, run at again; after two runs, given up from back there (not at the edge)
  and left out of plans for 2 minutes. With food at 6 or less (no running in the game), none are planned.
- **Gaps a fall into would hurt** (dry ground more than 2 down, lava, nothing) make a jump over them 100 ticks
  dearer, so a way round is taken where there is one. Digging from in water is costed as the game takes it: 5
  times as long, 25 not standing on anything.
- **Water**, one rule for every move (as Baritone does in its movement update): in water, never at a run (a run
  in water is the dive-swim, the way the view looks), jump held to keep the head up unless the way goes down
  into the water, and the view level while swimming on. With the head under and the air below 120 of 300, it
  goes straight up first.
- **Pillars** open the block two over the head too. The block goes under the feet near the top of the jump;
  a ceiling one over the head cuts the jump short of where it fits.

## Tests

- `tests/sim/physics.py` is a world with the game's movement, tick by tick: ground and air control, gravity,
  the jump and its run boost, step-up, the crouch edge stop, ladders, water, breaking and placing through the
  crosshair, and keys a frame late.
- `tests/test_moving.py` plans routes and does them in that world: walking, corners, steps, falls (onto a
  one-wide ledge), swimming, ladders, jumps over 1, 2 and 3, up one over a gap, onto a one-block rim, two in a
  row, digging up a hill, through a wall, down, up a cliff with blocks, a pillar, a bridge.
- `tests/test_minecraft_paths.py` tests the planner alone.

## Skills, as Baritone's commands

The user (2026-10-16): "there should be skills to do most things Baritone can do, like all the navigation stuff".
Each is a skill the doer has as a typed tool (core/doer.py); the words-only orders reach some directly.

| Baritone | Here | Notes |
|---|---|---|
| `goto x y z`, `goto <block>`, `click` | `go_to` x y z, a place or kind of block, "that" | the route planner's goals (blocks._there); onto: stood on the block's top |
| `goto x z` (GoalXZ) | `go_to` x z | that spot across, at any height |
| `goto y` (GoalYLevel) | `go_to` y | down or up to that height where the player is |
| GoalNear | `go_to` x y z within r | within r blocks of it |
| GoalComposite (mining's) | gathering: the quickest of the nearest 16 by one search | |
| GoalGetToBlock | goals 'mine' (in reach and sight), 'block' (beside it) | |
| GoalRunAway | `flee` | away from the hostile ones near |
| `mine <block> [n]` | `get` (gathers and crafts all it takes), `mine_at` | |
| `follow <entity>` | `follow` target, distance | walked after again whenever it's farther than distance |
| `pickup` | `pickup` items, radius | each dropped one walked onto |
| `tunnel` | `tunnel` direction, length, width, height | a cleared box straight on |
| `explore`, `thisway` | `explore` direction, distance | |
| `surface` | `go_to` "the surface" | |
| `build`, `sel fill/clear` | `build` parts (fill, walls, hollow, clear) | |
| `find` | `where_is` | |
| `waypoints` | `waypoint` set, get, list, delete | kept in .local/<game>/waypoints.json; the last death listed too |
| `cancel`, `pause` | "stop"; the game's own pause | |
| path options (allowPlace, allowBreak) | `go_to` place_blocks, dig | |
| `eta` | `check_way` seconds | and whether a way can be made at all |

Not here (yet): `farm` (crops: needs their growth from the bridge), `elytra`, `axis`, `invert`, `backfill`, exploring
to unseen ground (Baritone's explore goes to chunks it hasn't loaded).
