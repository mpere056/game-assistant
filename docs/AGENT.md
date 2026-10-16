# Doing things: skills, world memory, goals

How the assistant carries out orders, from "stop" to "make me an iron pickaxe" to things it has never
done before. This replaces the fixed task list (core/tasks.py's plan computed once and run to the end)
and the recoveries patched onto it (MODLOG, 2026-10-13 to 2026-10-15).

## Why

Every failure in the Minecraft play-tests of 2026-10-13..15 sat between parts that each saw a slice:

| Part | Knew | Couldn't see |
|---|---|---|
| Crafting planner (core/crafting.py) | recipes, what's held, what's near | where things are, whether they can be reached, time |
| Route planner (games/minecraft/blocks.py) | blocks, digging, building | the inventory beyond "blocks to build with" |
| Task runner (core/tasks.py) | a step list made once at the start | whether the plan still made sense |
| Doer (core/doer.py) | the order and a few text tools | typed results; its orders went through text rules |

And three things were missing underneath: a plan never looked at again (14 down with 5 planks, a new
crafting table there was never weighed against climbing back to the old one), failures as sentences
rather than facts the next decision could use, and no memory of what had been done.

## The parts

### Skills (core/skills.py; each game's in games/<game>/skills.py)

A skill is one thing the character can do: move to a goal, gather an item, mine one block, craft, put a
block down, smelt, fight, flee, eat, explore. Every game has many; the core knows none by name. Each:

- `estimate(world, **args)`: about how long from here (seconds), what it needs first (preconditions:
  items, a tool, a station, a place), and what it gives (effects); or None when it can't be done.
- `start(**args)`, then `update(snap, dt) -> Intent` every frame, till it ends with an **Outcome**.
- **Outcome**: done or failed, and why as data (a **Reason**), what changed, how long it took, and a line
  for the player.

Reason kinds (data, not sentences): `missing` (item, how many), `needs_tool` (tool), `no_target`
(nothing of that kind near), `unreachable` (target, from where, the route's note: e.g. `needs_blocks` n),
`stuck` (no way on from here at all), `danger` (what), `interrupted` (by a fight, the player),
`timeout`, `ui` (a screen or the pointer didn't do what was wanted).

The skills reuse what exists: the walk and route planner are `move`; core/tasks.py's step handlers
become `gather`, `craft`, `place`, `smelt`, `dig`, `eat`; core/melee.py is `fight`.

### World memory (core/memory.py)

One record the parts share, filled by the skills as they run and by what the game shows:

- what was done: stations put down (where, when), blocks dug (shafts and tunnels), what was gathered;
- what was learned: targets that couldn't be reached and why (from where), resources seen, dangers;
- the situation now (the adapter's, e.g. Minecraft: on the ground or n above/below it, the ways out);
- what held items are kept for (the plan's reserve).

Its `summary()` goes into the doer's state ("crafting table put down at -12 67 -38, 14 above you").

### Goals and plans (core/goals.py, core/plans.py)

A goal is a fact to make true: have(item, n), at(place), placed(station), near(character). The planner
works back from it through the skills' estimates (what each needs, what it gives), costing every way in
seconds from where the player is: a station already there (the route's cost to reach it) against one
made and put down here; stone in reach against logs on the surface; a staircase dug against blocks
gathered and a pillar. The cheapest plan wins; nothing is special-cased.

A plan is a list of skill calls in **milestones**: the sub-goals on the way (have a wooden pickaxe, have
3 raw iron, have 3 ingots, have the iron pickaxe). It's planned again:

- when a step fails (always: its Reason becomes a fact the new plan works from: `unreachable` rules that
  target out from here; `needs_blocks` makes blocks a sub-goal, got from what's in reach when stuck;
  `needs_tool` makes the tool one);
- at a milestone, when the world has moved from what the plan expected (the player far from where it
  thought, the inventory not as expected, much longer taken than estimated);
- when something interrupts it (a fight, the player taking over).

A small plan (one milestone) runs to its end unless a step fails.

### The doer (core/doer.py)

The model above the planner, for what it has no goal for and for when it can't find a plan. Its tools
become typed: `get(item, n)` (runs the planner), `go_to`, `mine_at`, `put_down`, `fight`, `flee`,
`explore`, `situation` (the memory's summary and the survey), each answering with an Outcome. The text
rules stay for the player's spoken orders only.

### The test world (tests/sim/)

Whole orders run end to end in a small made-up block world with the real planners: terrain built per
case (plains, forest, a hole, a cave, snow, night), skills carried out on the world's blocks and
inventory (mining takes the block and gives the drop, crafting changes the inventory, moving follows the
route planner's route and takes its time). Cases: "an iron pickaxe from nothing" in each, "get me down",
"wood from a tree top", "out of a hole with nothing". Each play-test failure becomes a case.

## Stages

Status (2026-10-15): stages 1-3 built and passing the tests (368; the test world's cases: an iron pickaxe from
nothing, a stone pickaxe 14 down a hole with planks (a new table there, 10 s), and out of a hole with only a
pickaxe); not yet tried in the game. Next: stage 4.

1. **Typed outcomes and the world memory.** Outcome/Reason in the core; core/tasks.py's steps end with
   Outcomes (every failure a Reason); the walk's give-ups carry their route note as a Reason; the memory
   records stations put down, blocks dug, targets that couldn't be reached; the doer's state has its
   summary; the task's recovery works from the Reason (not the walk's last note).
2. **Goals, plans, milestones.** core/goals.py over the skills' estimates, with the route planner's costs
   (cheap estimates by distance and terrain; the real route when ways are close); station choice (use or
   make); plans in milestones, planned again on the triggers above. Replaces the fixed task list and the
   recoveries patched onto it.
3. **The test world** and the whole-order cases above, run with the tests.
4. **The doer's typed tools** and the situation summary.
5. **More skills, and skills written by the model** (PLAN.md phase 6): new skills made from the existing
   ones by Sonnet, tried in the test world before the game.

Each stage keeps the game working: the old paths stay till the new one passes the test world.
