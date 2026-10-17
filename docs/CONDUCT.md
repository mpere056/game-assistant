# One conductor: acting safely while doing what was asked

How the assistant decides what the character does from moment to moment, with danger about. This follows
[AGENT.md](AGENT.md) (skills, world memory, goals) and replaces the safety rules patched onto it on 2026-10-16.

## Why

The character died six times on 2026-10-16, and each fix exposed the next failure. The logs show a pattern under
all of them, not six separate bugs:

| What happened | What it shows |
|---|---|
| "I won't walk in among them", then the order's next step walked straight into the pit | Two parts drove the character with no one deciding between them |
| Hold ground, "held off" after 10 s, the order goes on, hold ground again: a loop | Nothing remembered what was just learned (that pit is full of zombies) |
| A shield errand from nothing sent to the tree and the crafting table by that pit | Plans are made blind to danger, and remade blind to it after it stopped them |
| Into cover ordered mid-jump: a 10-block fall | A behaviour cut into another with no notion of a moment that mustn't be interrupted |
| A zombie let come to 6 blocks while gathering at half health | Danger was only looked at by rules that each saw one slice |
| Every fix tested piece by piece; each play-test found the next interaction | No test ran the whole thing against hostile creatures |

Today eight things can move the character, each started by its own trigger, with hand-offs bolted on between
them: the walk, the task runner, the fighter, the goal runner, the doer, the plan runner, and the safety rules
(cover, getting away, holding ground). The hand-offs (`_resume_walk`, `_then_walk`, `_hold_after`, `_hiding`,
`task.resume()`) are where the loops and contradictions come from.

## The parts

### 1. One conductor (core/conduct.py)

One place decides, every frame, which single behaviour has control of the character. Nothing else presses keys.

- **Behaviours**: everything the character does becomes one, with the same shape: `start`, `tick -> keys or an
  Outcome`, `estimate`, and `interruptible()` (false mid-jump, mid-placement, in a screen). The order's steps
  (walk, gather, craft, place, build) and the survival ones (fight, take cover, get away, hold ground, pillar up,
  eat, retreat) are all behaviours. They're the existing code, moved behind this interface.
- **Priorities**: survival, then the order. The conductor scores the candidates from the situation (below):
  urgency x priority.
- **Commitment**: the running behaviour keeps control until it ends, or until another beats it by a clear margin
  (hysteresis), and never while it says it can't be interrupted. No flipping between two behaviours each frame.
- **Coming back**: when a survival behaviour ends, the conductor goes back to the order, not by replaying a stored
  walk, but by asking the order "what next, given what's changed?" (the goal runner plans again when the world
  changed).
- **One decision log**: every change of behaviour, with why, in one file (today the reasons are spread over the
  walk, task, fight and doer logs).

### 2. The situation (core/situation.py: a world model with danger in it)

What every decision reads, kept up to date each frame instead of re-derived by each rule:

- **Creatures tracked over time**: where each hostile one is, which way and how fast it's going, whether it's after
  the player, when it was last seen. Not just this frame's list.
- **No remembered danger zones** (the user: the character must be free to go into new places, and back to old
  ones): danger comes from the creatures themselves, as they are now (and tracked a little while out of sight).
  What's learned is about a target: "held off: three zombies stand at that tree" makes that tree (that crafting
  table) a step that failed with a `danger` Reason, so the order plans round it (another tree) or asks; the place
  itself stays open.
- **The player**: health, food, armed with what, a shield or not, blocks to build with, in the air or not.
- **The ground round the player**: room, cover from those shooting (sight lines), ways out.
- **A danger cost for any place**: from the creatures near it (and night or day). One function.

### 3. Plans priced by danger (core/crafting.py, games/minecraft/blocks.py)

The planners use the situation's danger cost: a tree, a crafting table or an ore with hostile creatures by it costs
what the danger is worth, routes go round them, and a whole plan whose every way goes by them becomes "not without
risk" (the doer asks the player then, and waits for the answer, instead of announcing a question and carrying on).
When survival stops a step, the step fails with a `danger` Reason at that place, and the goal runner plans round it.

### 4. A test world with hostile creatures (tests/sim)

Today's test world has blocks, items and recipes. It gets creatures too, by the game's rules:

- **Creatures**: zombies that walk at the player and hit, skeletons that keep their distance and shoot along a
  sight line, spiders that climb and are fast, creepers that blow up; night and day.
- **Damage**: falls and health.
- **Close to the game**: the conductor and the real behaviours run against it frame by frame, simplified movement
  but the same decisions.
- **Scenarios**: each death today becomes one, run with every test:
  - shield errand at night, skeletons about, nothing in hand
  - things at the bottom of a pit with zombies
  - gathering at half health, a zombie coming
  - a skeleton shooting while a zombie's up close
  - a jump over a gap with a drop past the landing
- **Measured**: survived, done or not, how long, health lost.

This is what has been missing: the fixes have been checked piece by piece, and the game was the only place where
the pieces met.

## Stages

Status (2026-10-16): the conductor is built (core/conduct.py; the app's `_conduct`): one place decides, the order
paused and taken up again only there, the old hand-offs (`_hiding`, `_hold_after`, the safety rules' walks) gone,
a held-off step failing at its target (planned round), the decision log (runtime/conduct-<date>.jsonl). The test
world with creatures (stage 1) is next; not yet tried in the game.

1. **The test world with creatures**, and today's deaths as scenarios. Run against the code as it is, most should
   fail; that's the baseline.
2. **The situation**: creatures tracked, the danger cost; the planners priced by it.
3. **The conductor**: the behaviours moved behind one interface, priorities and commitment, the hand-offs removed,
   the decision log.
4. **The scenarios pass**, then play-tests; each new failure becomes a scenario first, fixed second.

Each stage keeps the game working; the old paths go only when the new ones pass the scenarios.
