"""Fighting a target for the player (the F10 "fight that soldier" orders): code at the frame rate, no
model in the loop (a model's hundreds of milliseconds are too slow to dodge a swing).

Every frame it reads the snapshot: the target and every other enemy nearby, each with the animation it
plays and how far into it (the game reports these), and asks `windows(model, anim)` for that
animation's attack windows: seconds into it when its hitbox is out (from the game's own animation
files). From that it knows when a hit is about to land, and decides:

1. Dodge: an enemy that faces the player and is close enough will hit within DODGE_LEAD seconds (or
   its hitbox is out right now and no dodge covered it): roll to the side (alternating sides).
2. Back off: stamina below LOW_STAMINA: step back until it is above RECOVERED_STAMINA.
3. Attack (bait and punish): when the target's attack is over and it still needs time to recover, or
   when it is reeling from our hit (a flinch: chain the next light attack of the combo while it can't
   act), or staggered for long (a stance break: keep hitting; a light attack from the front ripostes).
   How much time a punish needs depends on the target's poise: a normal enemy (poise up to LOW_POISE)
   flinches from a light attack, so the first hit already stops it.
4. Otherwise wait at SPACING, out of most attacks' reach (strafing when terrain stops a step back).

It locks on first (the camera then follows the target, so it never turns the camera itself). It
stops and hands back when the target dies ('won'), the player's health falls below LOW_HEALTH
('low_health'), the target is lost, or after MAX_SECONDS. Same Intent as auto-walk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .autowalk import DIRECTIONS, Intent, _flat, _wrap, _yaw
from .interface import Snapshot

# Rolling, from how Elden Ring works (community frame data, eldenring.wiki.gg "Invincibility Frames"):
# a roll is invincible for 14 frames (~0.23 s; 13 with a heavy load) from its start; it starts when the
# key is released (we hold it TAP_HOLD = 0.06 s) plus a frame or two of input delay. Pressing 0.16 s
# before the hit starts the roll ~0.06 s before it: invincible over the hit's first ~0.17 s (most attack
# windows are 0.07-0.2 s long). At 0.25 s the invincibility ran out before longer swings ended.
DODGE_LEAD = 0.16       # seconds before a hit lands to press roll
DODGE_EVERY = 0.55      # a roll pressed this far into a roll is buffered (earlier presses are lost)...
ROLL_AGAIN = 0.95       # ...and comes out only ~0.95 s into the first roll (fight logs: presses 0.50-0.65 s in
                        # all rolled at 0.95-1.0 s): a hit sooner than this after the one rolled can't be rolled
ROLL_FORWARD = True     # roll through the attack (forward-left/right, toward the locked-on enemy), the
                        # usual advice; backward rolls get caught by long reaches
THREAT_REACH = 4.0      # metres from an enemy's body edge a melee hit can reach (plus its size): 3.2 missed a
                        # lunge from 3.7 m (eighth session)
FACING_COS = 0.3        # an enemy counts as aiming at the player within ~72 degrees of facing them
ATTACK_REACH = 1.8      # metres from the target's body edge to swing (a straight sword; see WEAPON_REACH)
SAFE_GAP = 1.1          # no hit landing within this many seconds: safe to swing. A swing takes about a
                        # second to recover from and a roll can't start before: at 0.66 s a hit landed
                        # through the roll (fight log 2026-10-11)
ROLL_RECOVER = 0.5      # no swing this soon after a roll (was 0.75: in a 39 s fight it let most of a soldier's
                        # 0.3-0.7 s openings go by): the attack press right after the roll press
                        # cancelled the roll (in Elden Ring the later input wins)
SWING_EVERY = 0.9       # seconds between light attacks
STAMINA_PER_SWING = 20.0
LOW_STAMINA = 15.0
RECOVERED_STAMINA = 50.0
LOW_HEALTH = 0.35       # share of max health: hand back to the player (no flasks left)
LOW_HEALTH_FLASKS = 0.2 # ...while it can still heal itself
LOCK_RANGE = 12.0
MAX_SECONDS = 90.0
PLAYER_COMMIT = 1.35    # seconds a light attack locks the player in before a roll can cancel it (logs:
                        # a roll pressed 1.1 s into the swing was ignored; at 1.48 and 1.6 s it rolled)
SPACING = 3.0           # metres from the target's body edge to wait at between openings: most attacks
                        # don't reach, and it steps in only to punish (bait and punish)
PASSIVE_AFTER = 2.5     # an enemy that hasn't attacked for this long while we wait close by: go in anyway
CLOSE_THREAT = 2.5      # within this many metres any attack counts, whatever way the enemy faces
ROLL_DIR_HOLD = 0.3     # keep the roll's direction key held this long: the roll happens on the release of
                        # its key, and without a direction then it was a backstep (fight log: 27020)
# Poise ("stance", NpcParam super armor durability): a normal enemy has 15-65 and flinches from every
# light attack outside its own swings' hyper armor (fight log 2026-10-11: each hit on a Godrick Soldier,
# poise 30, played a 0.4 s flinch, 8031/8032/8036, and it attacked 0.4-0.5 s after, its hit 0.8 s into
# that: about 1.2 s with no hit coming after each flinch).
LOW_POISE = 60.0        # up to this a light attack flinches it
PUNISH_LOW = 0.45       # a low-poise target recovering this much longer: a swing lands (~0.3 s) first
# A middle tier (seventeenth session: a stronger soldier, c4351, poise 65 - just over LOW_POISE - was fought
# with the rules for tough enemies: openings needing 1.35 s it never left, an idle wait of 2.5 s: 27 s, not
# one swing, 521 -> 10 health). Up to MID_POISE it still flinches from most hits.
MID_POISE = 120.0
PUNISH_MID = 0.8        # a middle-tier target recovering this much longer: an opening
ROLL_SWING_OUT = 0.77   # a swing pressed early in a roll comes out this far into it (the log: 0.74-0.8 s)
LEAVING_FAST = 1.5      # m/s away from the player: leaving (no lunge assumed for an unknown attack)
LEAVING_WEIGHT = 0.5     # its speed away counts this much (a glide slows down: the bats came to a hover)
HIT_LANDS = 0.4         # seconds from a swing's press to its hit landing (and flinching)
PUNISH_MID_HIT = 1.0    # a middle-tier target's next hit at least this far off: its next attack may already be
                        # coming (our hit lands first and flinches it)
NEXT_WINDUP = 0.5       # an attack's first hit at least this far in when not known
PASSIVE_MID = 0.5       # ...and not attacking for this long, nothing coming: go in
SAFE_REACH_IN = 0.3     # attack-when-safe swings only from this far inside the reach (a swing from 2.4 m
                        # went short and the soldier's attack landed)
AFTER_ESCAPE = 6.0      # after getting room: fight the nearest, never chase back into the group
PASSIVE_LOW = 1.0       # a low-poise target not attacking for this long: go in
COMBO_LOW = 3           # light attacks in a row while it keeps flinching (low poise)
COMBO_HIGH = 2          # ...and against sturdier ones
CHAIN_MIN = 0.3         # seconds after a press before the next combo press (one per swing)
BROKEN_REACTION = 1.0   # a reaction animation this long: staggered / stance broken: hit without a cap
BACK_CHECK = 0.5        # stepping back this long and moved less than BACK_MIN metres: blocked (a rock)
BACK_MIN = 0.4
STRAFE_FOR = 0.8        # then strafe sideways this long instead
SPACING_LOW = 2.2       # against a low-poise enemy wait closer: at 3 m the walk in took so long that the
                        # soldier had chained its next attack before the swing (fourth fight log)
ROLL_FREE_AFTER = 0.5   # seconds into the player's roll (27XXX) it can act again: the animation runs ~1.8 s
                        # but rolls pressed 0.58 s and 1.03 s into it came out (fourth fight log)
# Several enemies (fifth fight log: the target walked off to 30 m while another soldier attacked; the game's
# lock-on was on the attacker, the fighter kept "closing" on the first one, dodged 18 times, never swung).
RETARGET_FAR = 5.0      # the target this far away and another enemy within RETARGET_NEAR attacking or
RETARGET_NEAR = 4.0     # facing the player: fight that one instead
LOCK_OFF = 45.0         # locked on, and the camera this many degrees off the target for LOCK_OFF_FOR s:
LOCK_OFF_FOR = 0.6      # the game locked someone else: fight that one, or unlock and turn to the target
LOCK_AIM = 20.0         # an enemy within this many degrees of the camera's forward is the locked one
RELOCK_WITHIN = 25.0    # lock on again only once the camera points at the target this closely
# Ledges (sixth session: chasing a fleeing animal, it walked off a cliff and died). The app probes the
# ground around the player (ledges()); no step or roll goes where it drops away.
LEDGE_NEAR = 30.0       # a probed direction within this many degrees of a move blocks that move
CHASE_FROM = 14.0       # the target this far (running off) for CHASE_AFTER s: hand over to the navmesh walk
CHASE_AFTER = 1.0       # (route-aware, sprints) and fight again on arrival: 'fled'
IFRAME_START = 0.05     # seconds from a roll's start to its invincibility
IFRAMES = 0.23         # a roll's invincibility (14 frames)
LONG_SHIFT = 0.05       # a long hit's roll pressed at most this much later than DODGE_LEAD
LONG_HIT = 0.2          # a hit this long (a swing and its bullet) outlasts the invincibility: roll away from
                        # it (back-left/right), not through it, timed to its middle
# Follow-ups (seventh session: a soldier's 3003000 -> 3003001 combo hits 0.69 s after its first hit; a boar's
# charge 3000 hits three times 0.5 s apart, then 3002 hits as it starts): a hit that soon after the one being
# rolled can't get its own roll. Then the roll goes away (back-left/right: out of reach and out of a charge's
# line), not through the enemy. Combos are learned as they are seen (chains: {base model: {attack: [[switch
# time, next attack]]}}) and kept by the app.
CHAIN_SEEN = 0.35       # a new attack this far in, right after another: a chain
# Eighth session (two soldiers, a wall): the game's lock-on was on one soldier and the target the other, so
# 15 openings went by with the camera off the target (no swing); a soldier behind a wall was "closed in on"
# for 10 s at a constant 2.05 m; a swing at an idle soldier hit its shield.
LOCK_NEARER = 2.0       # metres: the game's lock on another this much nearer than the target is followed
LOCK_FOLLOW_NEAR = 6.0  # locked on to another enemy this near: it is the target, at once
CLOSE_CHECK = 0.8       # closing in this long and the gap shrank less than CLOSE_MIN (or a wall is between):
CLOSE_MIN = 0.25        # blocked: step sideways DETOUR_FOR s (sides in turn); after MAX_DETOURS: unreachable
DETOUR_FOR = 0.7
MAX_DETOURS = 3
WALLED_FOR = 1.0        # closing with a wall in the line this long: the route round it
GUARD_CHECK = 0.7       # a swing at an enemy not attacking, and no flinch this long after: it blocked (a shield):
                        # only openings after its attacks from then on
# Interrupting (ninth session): a light attack lands ~0.3-0.4 s after the press, and a low-poise enemy hit in
# its wind-up flinches out of the attack (fight logs: 3003000 0.45 s in, cut short by 3008031). So with its
# next hit still INTERRUPT_LEAD away, strike first. An attack that doesn't flinch (no reaction GUARD_CHECK
# after) is armoured: not tried again.
INTERRUPT_LEAD = 0.75
# Facing (sixteenth session: one on one, 89 s, 5 swings: in reach with nothing coming, it stood with the camera
# turned away: it only turned the camera when it believed the lock was off, and that belief is often wrong
# (the game drops and moves the lock by itself); "closing in" at 0.45 m then sidestepped as "blocked"; and
# one swing that went wide was taken for a shield, which stopped the attacks for the rest of the fight.)
TURN_IF = 25.0          # degrees: the camera this far off the target is turned to it, lock or no lock (a lock
                        # on another enemy moves along with the mouse)
SWING_TOWARD = 10.0     # degrees: further off, the swing holds the direction to the target (unlocked, the
                        # character turns into its attack that way)
GUARD_FACING = 20.0     # a shield is only concluded from a swing made with the camera this close to the target
GUARD_FOR = 8.0         # ...and only believed this long
CHARGE_SPEED = 1.5      # m/s toward the player: running in (see _charging)
CHARGE_WINDUP = 0.55    # seconds an enemy arriving still needs before its hit (a soldier's quickest: 0.57 s)
SAFE_SWING = 0.75       # a normal enemy is struck whenever no hit (from anyone) can land within this long
# Reach (eleventh session: a bat whose dives were misjudged; the user asked for each enemy's real reach and
# the player's weapon's). An enemy attack threatens when the player's centre is within its reach (game data:
# hit spheres on the model, stretched out; reach_of) plus how far the attacker travels until the hit (learned
# per attack: LUNGE_DEFAULT until seen; or its speed toward the player now, whichever is more) plus the
# player's own size and a margin. Unknown reach: the old fixed THREAT_REACH.
PLAYER_RADIUS = 0.4
REACH_MARGIN = 0.4
LUNGE_DEFAULT = 1.5
LUNGE_MAX = 12.0
# The player's reach (from the target's body edge) by weapon type (EquipParamWeapon wepType), then measured:
# a swing that took health off the target at a longer gap raises it (MY_REACH_GROW at most).
WEAPON_REACH = {1: 1.4, 3: 1.8, 5: 2.1, 7: 2.4, 9: 1.8, 11: 2.1, 13: 1.9, 14: 1.9, 15: 2.0, 16: 2.2, 17: 1.7,
                19: 2.1, 21: 1.7, 23: 2.0, 24: 2.0, 25: 2.4, 28: 2.6, 29: 2.5, 31: 2.4, 35: 1.2, 37: 1.3, 39: 2.8,
                41: 2.4}
MY_REACH_GROW = 0.7
# Groups (eleventh session: 47 target switches in 45 s against several soldiers, 4 swings, death). One target
# is kept at least TARGET_HOLD s (sooner only when it dies, gets far or can't be reached); when the game's lock
# drifts to another enemy meanwhile it locks back on to its own target instead of switching. A new target:
# in front first (easy to lock on to, already facing the player), then the weakest (one attacker fewer
# soonest), then the nearest (target_score). Every enemy's attacks are dodged whichever is the target.
TARGET_HOLD = 2.5
GROUP_NEAR = 6.0        # enemies this near the player make a group: wait further out, back off away from them
GROUP_SPACING = 1.0     # ...this much further
# Healing (the user: "only when it's safe ... aware of how long it takes to heal and until you can dodge
# again"): a flask (the item key) locks the player in a drinking animation; no roll until it ends.
# Crowds (2026-10-12: eight soldiers within 6 m; their swings came ~0.5 s apart from different soldiers, a
# roll can follow a roll only after ~0.95 s: four hits, each while a second roll waited to come out, and
# death). Rolling can't answer that: get out of the middle. With CROWD or more within CROWD_RANGE it sprints
# away from their middle (the nearest clear way; still rolling when a hit comes, the sprint let go just
# before one: sprint and roll share a key) until at most one is within CROWD_CLEAR, then fights whoever
# came first (chasers string out). Caught in a crowd CROWD_GIVE_UP times in a fight: over to the player.
CROWD = 4               # (was 3: twenty-third session, rolls chosen in the world took no hit in 36 s among 3-6
CROWD_HURT = 3          # soldiers, but escaping from every 3 left no time to fight: 0 swings, then it gave up.
CROWD_HURT_S = 4.0      # Now 3 only when one of them hit the player within CROWD_HURT_S.)
CROWD_RANGE = 5.0
CROWD_CLEAR = 8.0
CROWD_MAX_S = 8.0       # one escape at most this long
CROWD_GIVE_UP = 3
# (2026-10-12, second crowd: the escape walked backwards: still locked on (a slow backpedal) and the sprint
# dropped whenever a hit was near, nearly always with 5-7 soldiers: they kept pace. Now the lock goes off
# for the escape, the sprint stays held, and a roll needed while sprinting lets go of the sprint for one frame
# first (sprint and roll share a key: the roll comes out on a fresh press). Handing back in a crowd killed the
# player: now it first runs clear (nobody within CLEAR_FOR_HANDBACK, or HANDBACK_RUN_S), then hands back.)
CLEAR_FOR_HANDBACK = 15.0
ESCAPE_REACHED = 1.5    # metres: an escape route's waypoint this near is passed
SPRINT_LET_GO = 0.25    # seconds before a roll the sprint key is let go (one frame wasn't enough)
HANDBACK_RUN_S = 12.0
SWING_CAME_OUT = 0.35   # a swing press with no attack animation by then was swallowed (pressed in a roll):
                        # it doesn't hold back the next swing
HEAL_AT = 0.5           # share of max health
HEAL_VULNERABLE = 1.6   # seconds from the press until a roll is possible again (drinking, conservative)
HEAL_EVERY = 4.0
REACH_GUESS = 3.0       # metres an enemy's attack is taken to reach (from its centre) when judging safety
FLEEING_SPEED = 2.5     # metres a second away from the player: don't chase it in the open (a bat after a dive)
SOLO_SEEN = 2           # an attack seen to end on its own this often: rolled through (else away)
SOLO_WAIT = 0.6         # no attack this long after one ended: it ended on its own
# Counters (twenty-fourth session, a c4351 soldier: each time our hit flinched it, 8200 for 0.22 s, it went
# straight into 1003012, hit 0.77 s in, three times of three): an attack starting within COUNTER_WITHIN of a
# flinch is its counter. Against one known (COUNTER_SEEN times) no combo: the second swing was still going when
# the counter landed; after one swing a roll is possible in time.
COUNTER_WITHIN = 0.5
COUNTER_SEEN = 1
COUNTER_NEAR = 3.5      # metres from its body edge at the counter's hit: it came at the player (not flying off)
# (counters were first kept as '_counter', unchecked; that table is ignored: '_counters' holds checked ones)
COVERED_SLACK = 0.5     # a dodged hit is "covered" only for the hit it was: the same attack again later
                        # lands at another time and gets its own roll (it was never dodged again: two hits)
# Twenty-second session (2026-10-12, Gatefront): 39 of 62 hits in a day's fights came right after a roll. The roll
# keys were "back" and "forward" relative to the camera, and with the lock off (escaping, or the game dropped it)
# the camera points anywhere: rolls landed next to the other soldiers or into a wall, and the next hit came before
# a roll could again (~0.95 s). Now a roll's direction is chosen in the world (_pick_roll): where it lands, away
# from the attacker when its next hit follows too soon, past its side otherwise, away from every other enemy.
ROLL_DIST = 2.6         # metres a roll carries the player (medium load)
LUNGE_SIDEWAYS = 1.5     # an away roll from a lunge: this much for going sideways (out of its line)
PUNISH_SPOT = 5.0       # a roll landing within reach of where the attack leaves the attacker (_rest_spot)
PUNISH_ALONE = 6.0      # metres: no other enemy this near, for the roll to go for the punish spot
REST_AFTER = 0.4        # seconds after an attack's last hit its attacker's place is taken as where it leaves it
REST_SEED = {'c4200': {1003011: (2.0, 0.0)}}   # a Man-Bat's dive: ~2 m past the player (twenty-fifth session)
ROLL_CROWD = 4.0        # metres: another enemy this near where a roll lands counts against it
LURK_GAP = 2.0          # metres: another enemy this near (from its body edge) and facing the player can start an
LURK_FACING = 0.5       # attack and hit during our swing (~0.6-0.8 s): no swing while one stands there
# Attacks that don't flinch from a hit in their wind-up (hyper armour): a soldier's long thrust (x003008: a swing
# into its wind-up landed, a reaction layer 4009000 played over it, and it hit anyway; three times that day).
# Learned ones are kept with the combos (chains '_armored').
ARMORED_SEED = {'c4310': (1003008, 3003008, 4003008, 5003008)}
COUNTER_SEED = {'c4350': (1003012,)}    # counters seen (each time of three, twenty-fourth session)
# Whatever is coming at the player is fought, whatever it is (the user: "anything targeting me would get in the
# way"): engaged = attacking, or facing the player and near or coming closer. The target changes to an engaged
# enemy that scores SWITCH_MARGIN better (target_score) once the current one has been held TARGET_HOLD.
ENGAGED_RANGE = 8.0
STALE_S = 20.0          # seconds without a live game frame: the fight is over
SWITCH_MARGIN = 1.5


def _base(model: str | None) -> str:
    """Variants (c4311...c4316) play their base model's (c4310) animations."""
    return (model or '')[:4] + '0'


def target_score(x, p, cam=None) -> float:
    """Lower is a better target: metres away, plus a metre for each 30 degrees off where the camera looks
    (in front: easy to lock on to), minus up to 2 for missing health (the weakest: one attacker fewer soonest)."""
    d = _flat(p, x.pos) - x.radius
    if cam is not None:
        off = abs(_wrap(_yaw((x.pos[0] - p[0], 0.0, x.pos[2] - p[2])) - _yaw(cam.forward)))
        d += off / 30.0
    if x.health is not None and x.max_health:
        d -= 2.0 * (1.0 - max(0.0, min(1.0, x.health / x.max_health)))
    return d


def reacting(anim: int | None) -> bool:
    """An enemy's hit reaction (Elden Ring's numbering: 8XXX flinches and staggers, 9XXX the parts that
    play with them; fight log: 3008031/3008032/3008036 and 3009411/3009412 right after each hit)."""
    return anim is not None and 8000 <= anim % 1_000_000 < 10000


def player_doing(anim: int | None) -> str:
    """What the player's own animation means (Elden Ring's numbering, seen in fight logs): attacking
    (aXXX_03XXXX: 23030000, 23030010), staggered by a hit (5XXX: 5101, 5102, 5203), dodging (27XXX:
    27020), otherwise free (idle 0, moving 2XXXX). While attacking or staggered a roll press is
    ignored (fight log 2026-10-11: presses at 1.1 s and 1.5 s into the player's own swing did nothing)."""
    if anim is None:
        return 'free'
    a = anim % 1_000_000
    if 30000 <= a < 40000:
        return 'attacking'
    if 5000 <= a < 10000:
        return 'staggered'
    if 27000 <= a < 28000:
        return 'dodging'
    return 'free'


@dataclass
class Fighter:
    windows: Callable[[str | None, int | None], list]   # (model, anim) -> [(start, end)] attack windows
    poise_of: Callable[[int | None], float | None] = lambda type_id: None  # NpcParam poise of a kind
    ledges: Callable[[], tuple] = lambda: ((), ())  # (world headings a step drops away, ... a roll does)
    chains: dict = field(default_factory=dict)      # learned combos, see CHAIN_SEEN
    sees: Callable[[int], bool | None] = lambda entity_id: None   # a clear line to it (None: not known)
    reach_of: Callable = lambda model, type_id, anim: None         # metres an attack reaches (game data)
    weapon: Callable = lambda: None                                  # (weapon id, wepType) in the right hand
    active: bool = False
    target_id: int | None = None
    goal_id: int | None = None                  # the one the order named (start): kept unless another attacks
    name: str | None = None
    events: list = field(default_factory=list)  # 'won', 'low_health', 'target_gone', 'timeout', 'dodge', 'swing'
    state: str = 'idle'                         # closing, attacking, dodging, recovering, waiting
    t: float = 0.0

    def __post_init__(self):
        self.debug: dict = {}          # what it saw this frame (the app writes it to the combat log)
        self._started = 0.0
        self._locked = False
        self._last_roll = -1e9
        self._last_swing = -1e9
        self._roll_side = 'right'
        self._recovering = False
        self._covered: dict = {}     # (entity, anim, window start) -> when that dodged hit lands
        self._roll_keys: frozenset = frozenset()
        self._last_enemy_attack = 0.0
        self._poise: float | None = None
        self._combo = 0
        self._back_from: tuple | None = None   # (time, position) a step back started
        self._strafe_until = -1e9
        self._strafe_side = 'left'
        self._off_since: float | None = None   # locked on, but the camera points away from the target
        self.unreachable: set = set()          # targets given up on (a wall): not chosen again this session
        self.walled: set = set()               # targets once handed to the walk to get round a wall
        self._far_since: float | None = None   # the target out at CHASE_FROM since
        self._attacking: dict = {}             # entity -> (attack, its time, when seen): to learn chains
        self._close_ref: tuple | None = None   # (time, gap) closing in began or last made progress
        self._detour_until = -1e9
        self._detour_side = 'left'
        self._detours = 0
        self._walled_since: float | None = None
        self._swing_check: list | None = None  # [time, target, it was attacking, it reacted]
        self._guarding: dict = {}              # target -> when it blocked a swing (a shield, GUARD_FOR)
        self._armored: set = set()             # (base model, attack) a swing didn't interrupt
        self._vel: dict = {}                   # entity -> (vx, vz) smoothed, m/s
        self._last_pos: dict = {}              # entity -> (position, time)
        self._lunge_from: dict = {}            # entity -> (attack, position at its start)
        self.my_reach = ATTACK_REACH
        self._weapon = None
        self._target_since = 0.0
        self._healing_until = -1e9
        self._last_heal = -1e9
        self._heal_check: tuple | None = None   # (when to check, health at the press)
        self._flask_fails = 0
        self._escaping_since: float | None = None   # sprinting out of a crowd since
        self._after_escape = -1e9                   # until then: the nearest only, no chasing back
        self._escapes = 0
        self._final: str | None = None              # running clear before handing back ('surrounded', ...)
        self.escape_route: list | None = None       # set by the app: a navmesh route to open ground (escape)
        self._escape_i = 0
        self.escape_note: str | None = None         # set by the app: how finding open ground went (the log)
        self._last_keys: frozenset = frozenset()
        self._swing_pending: float | None = None
        self.chains_changed = False
        self.kills: list = []                       # (entity id, type id) of each target killed, in turn
        for base, anims in ARMORED_SEED.items():
            self._armored.update((base, a) for a in anims)
        for base, anims in (self.chains or {}).get('_armored', {}).items():
            self._armored.update((base, int(a)) for a in anims)

    def start(self, target_id: int, name: str | None = None) -> None:
        self.active, self.target_id, self.name, self.state = True, target_id, name, 'closing'
        self.goal_id = target_id
        self._started, self._locked, self._covered = self.t, False, {}
        self._live_at = self.t
        self._last_enemy_attack = self.t
        self._recovering = False
        self._poise, self._combo, self._back_from = None, 0, None
        self._close_ref, self._detours, self._swing_check, self._guarding = None, 0, None, {}
        self._walled_since = None
        self._target_since = self.t
        self._escaping_since, self._escapes = None, 0
        self._final, self._swing_pending = None, None
        try:
            w = self.weapon()
        except Exception:
            w = None
        if w:
            self._weapon = w
            learned = self.chains.get('_my_reach', {}).get(str(w[0]))
            self.my_reach = learned or WEAPON_REACH.get(w[1], ATTACK_REACH)

    def stop(self, why: str = '') -> None:
        if self.active and why:
            self.events.append(why)
        self.active, self.state = False, 'idle'
        if why == 'unreachable' and self.target_id is not None:
            self.unreachable.add(self.target_id)

    # ---- every frame ----

    def update(self, snap: Snapshot, dt: float) -> Intent:
        intent = self._update(snap, dt)
        self._last_keys = intent.keys
        if intent.keys and self.state != 'dodging' and snap.camera is not None:   # never off a ledge
            keys = self._safe(intent.keys, _yaw(snap.camera.forward), roll=False)
            if keys != intent.keys:
                self.debug['ledge'] = sorted(intent.keys)
                intent = Intent(keys, intent.turn, intent.taps)
        return intent

    def _update(self, snap: Snapshot, dt: float) -> Intent:
        self.t += dt
        if not self.active:
            return Intent()
        # No live game for STALE_S (a loading screen after a death, a menu, the game stopped: the frame number
        # stands still): the fight is over. (Twenty-third session: a fight stayed "on" through a death and the
        # assistant logged that frozen moment 4 times a second for three hours.)
        last = getattr(self, '_last_frame', None)
        if last is not None and snap.frame != last:
            self._frames_count = True              # (an adapter whose frame number counts; tests' stays put)
        live = snap.in_world and snap.player is not None and snap.camera is not None and not snap.menu \
            and (snap.frame != last or not getattr(self, '_frames_count', False))
        self._last_frame = snap.frame
        if live:
            self._live_at = self.t
        elif self.t - getattr(self, '_live_at', self.t) > STALE_S:
            self.stop('target_gone')
            return Intent()
        if not snap.in_world or snap.player is None or snap.camera is None or snap.menu:
            self.debug = {}
            return Intent()
        if snap.screen is not None and not snap.screen.focused:  # the keys can't reach the game
            if self.state != 'paused':
                self.events.append('not_focused')
            self.state = 'paused'
            return Intent()
        pl = snap.player
        if pl.state == 'dead':
            self.stop('low_health')
            return Intent()
        e = next((x for x in snap.entities if x.id == self.target_id), None)
        if e is None or e.dead or (e.health is not None and e.health <= 0):
            if e is not None and all(k[0] != e.id for k in self.kills):
                self.kills.append((e.id, e.type_id))   # (for 'kill two soldiers': each one counts)
            nxt = self._threat(snap, pl.pos, self.target_id, cam=snap.camera)
            goal = next((x for x in snap.entities if x.id == self.goal_id and not x.dead
                         and (x.health is None or x.health > 0)), None) if self.goal_id != self.target_id else None
            if nxt is None and goal is not None:   # what came at us is down: back to the one asked for
                nxt = goal
            if nxt is not None:      # one down, another still on the player: that one next
                self._switch(nxt, 'next_target')
                e = nxt
            else:
                self.stop('target_gone' if e is None else 'won')
                return Intent()
        low = LOW_HEALTH_FLASKS if self._flask_fails < 2 else LOW_HEALTH
        if pl.health is not None and pl.max_health and pl.health < low * pl.max_health and self._final is None:
            near = any(x.hostile and not x.dead and _flat(pl.pos, x.pos) < CROWD_CLEAR for x in snap.entities)
            if not near:
                self.stop('low_health')
                return Intent()
            self._final = 'low_health'          # enemies close: run clear first, then hand back
            self._escaping_since = self.t
        if self.t - max(self._started, getattr(self, '_timer_from', 0.0)) > MAX_SECONDS:   # per target (a group: long)
            self.stop('timeout')
            return Intent()
        p = pl.pos
        self._last_pos_player = p
        gap = _flat(p, e.pos) - e.radius
        if gap > RETARGET_FAR and self._may_switch(gap):   # it walked off while another attacks: that one
            nxt = self._threat(snap, p, e.id, cam=snap.camera)
            if nxt is not None and _flat(p, nxt.pos) - nxt.radius < gap - 2.0 and self._may_leave(e, nxt):
                self._switch(nxt, 'retarget')
                e, gap = nxt, _flat(p, nxt.pos) - nxt.radius
        elif self._may_switch(gap) and self._escaping_since is None:
            # another one is on the player and a much better target (a wolf biting while the soldier stands off)
            nxt = self._threat(snap, p, e.id, cam=snap.camera)
            if nxt is not None and target_score(nxt, p) < target_score(e, p) - SWITCH_MARGIN \
                    and self._may_leave(e, nxt):   # (behind counts)
                self._switch(nxt, 'retarget')
                e, gap = nxt, _flat(p, nxt.pos) - nxt.radius
        if gap > CHASE_FROM and self._escaping_since is None and self.t >= self._after_escape:
            # running off: the walk follows it along the navmesh (not after an escape: that ran us back
            # into the group they left behind; seventeenth session, a 424-damage hit)
            if self._far_since is None:
                self._far_since = self.t
            elif self.t - self._far_since >= CHASE_AFTER:
                self.stop('fled')
                return Intent()
        else:
            self._far_since = None
        if self._poise is None:
            try:
                self._poise = self.poise_of(e.type_id)
            except Exception:
                self._poise = None
            self._poise = -1.0 if self._poise is None else float(self._poise)
        low_poise = self._poise <= LOW_POISE  # unknown (-1, wildlife) counts as low
        self.debug = {'target_anims': [(a, round(t, 2)) for a, t, _ in (e.anims or ())],
                      'target_windows': {a: self.windows(e.model, a) for a, _t, _ in (e.anims or ())
                                         if self.windows(e.model, a)},
                      'gap': round(gap, 2), 'stamina': pl.stamina,
                      'hp': pl.health, 'facing_me': self._faces(e, p),
                      'my_anim': (pl.anim, round(pl.anim_time, 2)) if pl.anim is not None else None,
                      'poise': self._poise, 'model': e.model, 'my_reach': self.my_reach,
                      'vel': tuple(round(v, 1) for v in self._vel.get(e.id, (0.0, 0.0))),
                      'locked': self._locked}
        err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
        self.debug['cam_err'] = round(err, 1)
        self.debug.update(pos=(round(p[0], 1), round(p[2], 1)), cam_yaw=round(_yaw(snap.camera.forward)),
                          near=self._near_log(snap, p), target=e.id % 10000)
        if self._escaping_since is not None:
            self.debug['escape'] = {'route': len(self.escape_route or ()), 'at': self._escape_i,
                                    'note': self.escape_note}
        taps = []
        # The game locks onto whoever is in front, not always the target: follow its choice, or unlock.
        aimed = self._aimed(snap, p) if self._locked and abs(err) > LOCK_OFF else None
        if aimed is not None and aimed.id != e.id and _flat(p, aimed.pos) - aimed.radius < LOCK_FOLLOW_NEAR \
                and self._may_switch(gap) and self._may_leave(e, aimed) and self._lock_worth(e, aimed, p):
            self._switch(aimed, 'retarget')   # the camera is on it: swing at it (not at one off to the side)
            e = aimed
            gap = _flat(p, e.pos) - e.radius
            err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
            self._off_since = None
        if self._locked and abs(err) > LOCK_OFF:
            if self._off_since is None:
                self._off_since = self.t
            elif self.t - self._off_since >= LOCK_OFF_FOR:
                self._off_since = None
                aimed = self._aimed(snap, p)
                if aimed is not None and aimed.id != e.id and self._may_switch(gap) and self._may_leave(e, aimed) \
                        and self._lock_worth(e, aimed, p):
                    self._switch(aimed, 'retarget')
                    e = aimed
                    gap = _flat(p, e.pos) - e.radius
                    err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
                else:
                    # The camera stays off the target: the game's lock is gone (out of range) or on another.
                    # Don't press lock (if the game already dropped it, that press locks someone else): stop
                    # believing in it, turn the camera to the target (the mouse also moves a lock that way),
                    # and lock on once it is in front.
                    self._locked = False
        else:
            self._off_since = None
        if not self._locked and gap < LOCK_RANGE and abs(err) < RELOCK_WITHIN and not taps \
                and self._escaping_since is None:
            self._locked = True
            taps.append('lock')
        doing = player_doing(pl.anim)
        if doing == 'dodging' and pl.anim_time is not None and pl.anim_time >= ROLL_FREE_AFTER:
            doing = 'free'           # the roll's long tail: free to act
        self.debug['me'] = doing
        if self._swing_pending is not None and pl.anim is not None and self.t - self._swing_pending >= SWING_CAME_OUT:
            if doing != 'attacking':          # the press was swallowed (in a roll): not a swing
                self._last_swing = -1e9
                self._combo = max(0, self._combo - 1)
            self._swing_pending = None
        self._learn_chains(snap, p)    # (every frame: also while a roll is held, when hits land)
        self._track(snap, p)
        self._learn_reach(snap, pl, p)
        # 0. Rolling: keep its direction held until the roll has started.
        if self.t - self._last_roll < ROLL_DIR_HOLD:
            return Intent(self._roll_keys, 0.0, tuple(taps))
        # 1. A hit about to land: roll to the side (a press while attacking or staggered is ignored by
        # the game: press anyway, it's the best there is, but the swing rules below avoid getting there).
        soonest = self._next_hit(snap, p)
        self.debug['next_hit_s'] = None if soonest is None else round(soonest[0], 3)
        # (a roll can follow a roll after DODGE_EVERY: a soldier's two-swing chain hit the player while a
        # "not while dodging" rule blocked the second roll)
        long_hit = soonest is not None and soonest[2] > LONG_HIT
        # invincible ~0.10-0.33 s after the press: a long hit's invincibility starts a little before it, not
        # centred on it (twenty-fourth session: a running attack timed for its middle connected at its start)
        lead = DODGE_LEAD - min(LONG_SHIFT, (soonest[2] - 0.11) / 2) if long_hit else DODGE_LEAD
        if soonest is not None and soonest[0] <= max(0.0, lead) and self.t - self._last_roll >= DODGE_EVERY \
                and 'sprint' in self._last_keys:
            return Intent(frozenset(), 0.0, tuple(taps))   # the sprint's key up first: the roll next frame
        if soonest is not None and soonest[0] <= max(0.0, lead) and self.t - self._last_roll >= DODGE_EVERY \
                and self._roll_too_late(pl, soonest):
            # (twenty-fourth session: a roll pressed 0.57 s into a roll came out at ~1.0 s, after the hit, and
            # then rolled right through the soldier's recovery: the opening missed twice in one fight)
            self.state = 'evading'
            self.debug['evade'] = True
            if doing != 'free':
                return Intent(frozenset(), 0.0, tuple(taps))
            att = soonest[3]
            off = _yaw((p[0] - att.pos[0], 0.0, p[2] - att.pos[2]))
            keys = frozenset(min(DIRECTIONS, key=lambda d: abs(_wrap(off - _yaw(snap.camera.forward) - d[0])))[1])
            return Intent(keys, 0.0, tuple(taps))
        if soonest is not None and soonest[0] <= max(0.0, lead) and self.t - self._last_roll >= DODGE_EVERY:
            follow = self._follow_up(soonest)
            then = self._next_hit(snap, p, exclude=soonest[3].id)   # the other enemies' next hit
            if then is not None and (follow is None or then[0] - soonest[0] < follow):
                follow = max(0.0, then[0] - soonest[0])
            # Through it only when nothing can follow too soon: a known follow-up after ROLL_AGAIN, or none
            # and the attack seen to end on its own at least SOLO_SEEN times; otherwise away (twentieth
            # session: five hits from follow-ups of attacks not seen before, after forward rolls)
            solo = self.chains.get('_solo', {}).get(_base(soonest[3].model), {}).get(str(soonest[4]), 0)
            escape = long_hit or (follow is not None and follow < ROLL_AGAIN) or (follow is None and solo < SOLO_SEEN)
            self.debug['follow_up_s'] = None if follow is None else round(follow, 2)
            pick = self._pick_roll(snap, p, soonest, escape, _yaw(snap.camera.forward))
            if pick is not None:   # (every way off a ledge: take the hit rather than fall)
                self._covered[soonest[1]] = self.t + soonest[0]
                self._covered = {k: v for k, v in self._covered.items() if v > self.t - 2.0}
                self._last_roll = self.t
                self._roll_keys = pick[1]
                self._roll_side = next(iter(pick[1] & {'left', 'right'}), self._roll_side)
                self.debug['roll_yaw'] = round(pick[2])
                self.debug['roll_away'] = escape
                self.state = 'dodging'
                self.events.append('dodge')
                return Intent(self._roll_keys, 0.0, tuple(taps) + ('roll',))
        away = self._away_yaw(snap, p, e)
        cam_yaw = _yaw(snap.camera.forward)
        escape = self._crowd_step(snap, p, soonest, doing, taps, away, cam_yaw)
        if escape is not None:
            return escape
        heal = self._heal_step(snap, pl, p, soonest, doing, taps, away, cam_yaw)
        if heal is not None:
            return heal
        # 2. Out of stamina: back off until it has come back.
        st, st_max = pl.stamina, pl.max_stamina
        if st is not None:
            if st < LOW_STAMINA:
                self._recovering = True
            elif self._recovering and st >= min(RECOVERED_STAMINA, st_max or RECOVERED_STAMINA):
                self._recovering = False
        if self._recovering:
            self.state = 'recovering'
            return Intent(self._back_keys(p, away, cam_yaw), 0.0, tuple(taps))
        if doing == 'free' and self.t - self._last_swing > 1.2:
            self._combo = 0
        # 3. Bait and punish. A swing locks the player in for PLAYER_COMMIT seconds, so it only goes in
        # when that fits: the target is recovering from an attack for at least that long (an opening),
        # or nothing will hit before the player could roll again. Between openings it waits at SPACING,
        # out of most attacks' reach. A passive enemy (no attack for PASSIVE_AFTER s) is attacked anyway.
        attacking = bool(e.anim is not None and self.windows(e.model, e.anim)
                         or any(self.windows(e.model, a) for a, _t, _l in e.anims))
        if attacking:
            self._last_enemy_attack = self.t
        self._check_guard(e)
        mid_poise = not low_poise and self._poise <= MID_POISE
        idle_for = PASSIVE_LOW if low_poise else PASSIVE_MID if mid_poise else PASSIVE_AFTER
        passive = self.t - self._last_enemy_attack >= idle_for and self.t - self._started >= idle_for \
            and not self._shielded(e)   # a shield: only after its attacks
        opening = self._opening(e, PUNISH_LOW) if low_poise else self._opening(e, HIT_LANDS, PUNISH_MID_HIT) \
            if mid_poise else self._opening(e, HIT_LANDS, PLAYER_COMMIT + DODGE_LEAD)
        reel = self._reeling(e)   # None, 'flinch' (our last hit landed) or 'broken'
        clear = soonest is None or soonest[0] > PLAYER_COMMIT + DODGE_LEAD
        wind_up = self._wind_up(e)          # seconds to its next hit, in an attack's wind-up
        interrupt = self._poise is not None and 0.0 <= self._poise <= LOW_POISE and wind_up is not None and wind_up >= INTERRUPT_LEAD \
            and (_base(e.model), self._attack_anim(e)) not in self._armored
        # A normal enemy (known poise <= LOW_POISE) when nothing can hit before our hit lands and flinches it:
        # no waiting for an opening, nor for it to draw its weapon (fifteenth session: it walked up and waited).
        safe_now = self._poise is not None and 0.0 <= self._poise <= LOW_POISE \
            and (soonest is None or soonest[0] >= SAFE_SWING) and not self._shielded(e) and wind_up is None
        # (in an attack's wind-up only the interrupt rule, which knows attacks that don't flinch: a swing into
        # 4003008 traded a hit, twentieth session)
        # ...the swing itself then from SAFE_REACH_IN inside the reach (it walks in that far first: the margin
        # on the decision to go in left it standing just out of reach, waiting; eighteenth session)
        reach = self.my_reach - SAFE_REACH_IN if safe_now and not (opening or reel or interrupt or passive) \
            else self.my_reach
        go_in = (opening or reel or interrupt or safe_now or (passive and clear)) \
                and self.t - self._last_roll >= ROLL_RECOVER
        self.debug['interrupt'] = interrupt
        self.debug['safe_now'] = safe_now
        self.debug.update(opening=opening, passive=passive, reel=reel, poise=self._poise, combo=self._combo)
        stamina_ok = st is None or st >= STAMINA_PER_SWING
        others = self._next_hit(snap, p, exclude=e.id)
        if others is not None and others[0] < PLAYER_COMMIT:   # another enemy's hit would land mid-swing
            go_in = False
            reel = None
        # another one running in would arrive and swing while we're stuck in ours (one swing: it still has to wind
        # up when it arrives; a combo goes on longer)
        charger = self._charging(snap, p, e.id, PLAYER_COMMIT - CHARGE_WINDUP)
        if charger:
            go_in = False
        if reel is not None and (charger or self._charging(snap, p, e.id, PLAYER_COMMIT)):
            reel = None
        self.debug['charger'] = charger
        lurking = self._lurking(snap, p, e.id)
        if lurking and reel != 'broken':   # another one right there, facing us: it would hit us mid-swing
            go_in = False
            reel = None
        self.debug['lurking'] = lurking
        self.debug['others_hit_s'] = None if others is None else round(others[0], 3)
        # Chain: our hit landed and it's reeling: press again during our swing (the game buffers it
        # into the next combo attack), up to the combo cap (no cap while it's broken).
        cap = COMBO_LOW if low_poise else COMBO_HIGH
        if reel == 'flinch' and self._counters(e):
            cap = 1                                  # it counters out of a flinch: one swing, then ready to roll
        self.debug['counters'] = bool(self._counters(e))
        chain = reel is not None and doing == 'attacking' and self.t - self._last_swing >= CHAIN_MIN \
            and (reel == 'broken' or self._combo < cap) and stamina_ok
        can_swing = go_in and doing == 'free' and self.t - self._last_swing >= SWING_EVERY and stamina_ok
        walled = self.sees(e.id) is False
        self.debug['walled'] = walled
        # Where it will be when the hit lands: a swing pressed in a roll comes out ~0.77 s into it (a rolling
        # attack), its hit ~0.35 s after; meanwhile the target moves (twenty-fifth session: three swings at bats
        # pressed 0.5 s into rolls, at 1.9 m, came out as the bat flew off to 3.2 m: none landed).
        out_in = max(0.0, ROLL_SWING_OUT - pl.anim_time) if player_doing(pl.anim) == 'dodging' else 0.0
        vx, vz = self._vel.get(e.id, (0.0, 0.0))
        dd = _flat(p, e.pos) or 1e-6
        leaving = (vx * (e.pos[0] - p[0]) + vz * (e.pos[2] - p[2])) / dd     # m/s away from the player
        gap_hit = gap + max(0.0, leaving) * (out_in + HIT_LANDS) * LEAVING_WEIGHT
        self.debug['gap_hit'] = round(gap_hit, 2)
        if gap <= reach + (0.5 if chain else 0.0) and (can_swing or chain) and abs(err) < 40.0 \
                and not walled and (chain or gap_hit <= reach):
            if not chain:
                self._swing_check = [self.t, e.id, attacking, False, self._attack_anim(e), _base(e.model),
                                     e.health, gap, err, wind_up is not None]
                self._swing_pending = self.t
            self._close_ref, self._detours = None, 0
            self._last_swing = self.t
            self._combo += 1
            self.state = 'attacking'
            self.events.append('swing')
            toward = frozenset(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1]) \
                if abs(err) > SWING_TOWARD else frozenset()
            return Intent(toward, 0.0, tuple(taps) + ('attack',))
        keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1])
        turn = 0.0 if abs(err) < TURN_IF else max(-240 * dt, min(240 * dt, err * 6 * dt))
        if can_swing and gap <= reach and not walled:   # in reach, the camera off it: turn, don't walk
            self.state = 'turning'
            self._close_ref = None
            return Intent(frozenset(), turn, tuple(taps))
        if can_swing:  # 4a. an opening: in to reach, fast
            return self._closing(keys, turn, taps, gap, walled, e, p)
        if doing != 'free':  # in our own swing or staggered: nothing to do but wait
            self.state = 'waiting'
            return Intent(frozenset(), 0.0, tuple(taps))
        spacing = SPACING_LOW if low_poise or mid_poise else SPACING
        group = sum(1 for x in snap.entities if x.hostile and not x.dead and _flat(p, x.pos) < GROUP_NEAR)
        if group >= 2:
            spacing += GROUP_SPACING
        self.debug['group'] = group
        if gap < spacing - 0.4 and not self._recovering_from_attack(e):
            # 4b. too close between openings: step back out of reach (not while it recovers from an attack:
            # that is when to be close)
            self.state = 'spacing'
            return Intent(self._back_keys(p, away, cam_yaw), turn, tuple(taps))
        self._back_from = None
        if self.t < self._after_escape and gap > CROWD_RANGE and not walled:
            self.state = 'waiting'             # just got room: let the chasers come, one at a time
            return Intent(frozenset(), turn, tuple(taps))
        if gap > spacing + 1.0 or walled:  # 4c. too far (or behind a wall): come to spacing distance
            return self._closing(keys, turn, taps, gap, walled, e, p)
        self._close_ref = None
        self.state = 'waiting'
        return Intent(frozenset(), 0.0, tuple(taps))

    def _opening(self, e, need: float = PLAYER_COMMIT, need_hit: float | None = None) -> bool:
        """The target is in an attack whose hits are all over and still needs at least `need` seconds to
        finish: PLAYER_COMMIT for a swing and a roll after it; less for a low-poise enemy our hit flinches.
        An attack it is known to chain into another (chains) ends where that one starts (eighth session: a
        swing into 3002's "recovery" met the 3000 it chains into: three hits). With need_hit: the next attack
        may start after `need` (our hit lands first) as long as its first hit is need_hit away (twenty-fourth
        session: a c4351 soldier chains every attack into the next ~0.7 s after its hit, but that one's hit
        comes 1.6 s after: "no opening" all fight, 9 swings in 90 s)."""
        speed = e.anim_speed if e.anim_speed > 0 else 1.0
        known = self.chains.get(_base(e.model), {})
        for anim, t, length in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            w = self.windows(e.model, anim)
            if not w or t <= max(end_ for _s, end_ in w):
                continue
            follows = known.get(str(anim), [])
            end = min([length] + [sw for sw, _nxt in follows])
            if need_hit is None:
                if (end - t) / speed >= need:
                    return True
                continue
            hits = [length + NEXT_WINDUP] + [sw + (self.windows(e.model, nxt) or [(NEXT_WINDUP, 0)])[0][0]
                                            for sw, nxt in follows]
            if (end - t) / speed >= need and (min(hits) - t) / speed >= need_hit:
                return True
        return False

    def _attack_anim(self, e) -> int | None:
        """The attack the target is playing (one with hit windows), or None."""
        for a, _t, _l in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            if a is not None and self.windows(e.model, a):
                return a
        return None

    def _wind_up(self, e) -> float | None:
        """Seconds until the target's attack's first hit, while it hasn't come yet (None otherwise)."""
        speed = e.anim_speed if e.anim_speed > 0 else 1.0
        for a, t, _l in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            w = self.windows(e.model, a) if a is not None else []
            if w and t < w[0][0]:
                return (w[0][0] - t) / speed
        return None

    def _recovering_from_attack(self, e) -> bool:
        for a, t, _l in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            w = self.windows(e.model, a) if a is not None else []
            if w and t > max(end for _s, end in w):
                return True
        return False

    def _closing(self, keys, turn, taps, gap: float, walled: bool, e=None, p=None) -> Intent:
        """Closing in on the target; blocked (no progress, or a wall between): sideways around it. A target
        flying or running away fast isn't chased in the open: wait for it to come back (a bat after a dive)."""
        self.state = 'closing'
        if e is not None and p is not None:
            vx, vz = self._vel.get(e.id, (0.0, 0.0))
            d = _flat(p, e.pos) or 1e-6
            away = (vx * (e.pos[0] - p[0]) + vz * (e.pos[2] - p[2])) / d
            if away > FLEEING_SPEED:
                self.state = 'waiting'
                self._close_ref = None
                return Intent(frozenset(), 0.0, tuple(taps))
            if math.hypot(vx, vz) > 1.5:      # it moves: the gap not shrinking isn't a wall
                self._close_ref = None
        if walled:   # a wall stays between for WALLED_FOR s: round it by the route (or give it up)
            if self._walled_since is None:
                self._walled_since = self.t
            elif self.t - self._walled_since >= WALLED_FOR:
                self._walled_since = None
                first = self.target_id not in self.walled
                self.walled.add(self.target_id)
                self.stop('walled' if first else 'unreachable')
                return Intent()
        else:
            self._walled_since = None
        if self.t < self._detour_until:
            return Intent(frozenset({self._detour_side}), turn, tuple(taps))
        if self._close_ref is None or gap < self._close_ref[1] - CLOSE_MIN:
            self._close_ref = (self.t, gap)
        elif self.t - self._close_ref[0] >= CLOSE_CHECK or (walled and self.t - self._close_ref[0] >= 0.3):
            self._detours += 1
            if self._detours > MAX_DETOURS:
                # first: the walk takes it round by the navmesh and the fight starts again there
                first = self.target_id not in self.walled
                self.walled.add(self.target_id)
                self.stop('walled' if first else 'unreachable')
                return Intent()
            self._detour_side = 'right' if self._detour_side == 'left' else 'left'
            self._detour_until = self.t + DETOUR_FOR
            self._close_ref = None
            self.events.append('blocked')
            return Intent(frozenset({self._detour_side}), turn, tuple(taps))
        return Intent(frozenset(keys), turn, tuple(taps))

    def _check_guard(self, e) -> None:
        """After a swing at a target that wasn't attacking: did it flinch? If not, it blocked."""
        c = self._swing_check
        if c is None or c[1] != e.id:
            return
        since = self.t - c[0]
        playing = e.anims or ((e.anim, e.anim_time, e.anim_length),)
        if c[2] and c[4] is not None:
            # in its attack: flinched only if the attack was cut short (a reaction can be a layer over an attack
            # that goes on: 4009000 over 4003008, which then hit; twenty-second session)
            if not any(a == c[4] for a, _t, _l in playing):
                c[3] = True
        elif any(reacting(a) and t < since for a, t, _l in playing):
            c[3] = True
        if since >= GUARD_CHECK:
            landed = c[6] is not None and e.health is not None and e.health < c[6] - 0.5
            if landed:            # it took the hit: our reach is at least that gap
                self._learn_my_reach(c[7])
            in_reach = c[7] <= self.my_reach - 0.3 and abs(c[8]) <= GUARD_FACING
            if not c[2] and not c[3] and not landed and in_reach:
                self._guarding[e.id] = self.t   # in reach and in front, no damage, no flinch: a shield
                self.events.append('guarded')
            elif c[2] and not c[3] and landed and c[4] is not None and (len(c) < 10 or c[9]):
                # hit it in its attack's wind-up and the attack went on: armoured (kept with the combos)
                self._armored.add((c[5], c[4]))
                known = self.chains.setdefault('_armored', {}).setdefault(c[5], [])
                if c[4] not in known:
                    known.append(c[4])
                    self.chains_changed = True
                self.events.append('armored')
            self._swing_check = None

    def _learn_my_reach(self, gap: float) -> None:
        if self._weapon is None:
            return
        prior = WEAPON_REACH.get(self._weapon[1], ATTACK_REACH)
        new = min(prior + MY_REACH_GROW, max(self.my_reach, gap + 0.1))
        if new > self.my_reach + 0.05:
            self.my_reach = round(new, 2)
            self.chains.setdefault('_my_reach', {})[str(self._weapon[0])] = self.my_reach
            self.chains_changed = True

    def _track(self, snap: Snapshot, p) -> None:
        """Each enemy near the player: its velocity (smoothed), and how far each attack carries it from its
        start to its first hit (a lunge, a dive): learned per attack."""
        for e in snap.entities:
            if not e.hostile or e.dead or _flat(p, e.pos) > 30.0:
                continue
            last = self._last_pos.get(e.id)
            self._last_pos[e.id] = (e.pos, self.t)
            if last is not None and self.t - last[1] > 1e-3:
                dt = self.t - last[1]
                vx, vz = (e.pos[0] - last[0][0]) / dt, (e.pos[2] - last[0][2]) / dt
                ox, oz = self._vel.get(e.id, (vx, vz))
                k = min(1.0, dt / 0.15)
                self._vel[e.id] = (ox + (vx - ox) * k, oz + (vz - oz) * k)
            anim = self._attack_anim(e)
            if anim is None:
                self._lunge_from.pop(e.id, None)
                continue
            t = next((t for a, t, _l in (e.anims or ((e.anim, e.anim_time, e.anim_length),)) if a == anim), 0.0)
            self._learn_rest(e, anim, t, p)
            start = self._lunge_from.get(e.id)
            if start is None or start[0] != anim:
                if t < 0.3:
                    self._lunge_from[e.id] = (anim, e.pos, p)
                continue
            w = self.windows(e.model, anim)
            if w and t >= w[0][0] and start[1] is not None:
                # toward where the player stood at its start (a bat flying off isn't lunging)
                e0, p0 = start[1], start[2] if len(start) > 2 else p
                n = _flat(e0, p0) or 1e-6
                moved = ((e.pos[0] - e0[0]) * (p0[0] - e0[0]) + (e.pos[2] - e0[2]) * (p0[2] - e0[2])) / n
                moved = min(LUNGE_MAX, max(0.0, moved))
                table = self.chains.setdefault('_lunge', {}).setdefault(_base(e.model), {})
                if moved > table.get(str(anim), -1.0) + 0.1:
                    table[str(anim)] = round(moved, 2)
                    self.chains_changed = True
                self._lunge_from[e.id] = (anim, None)        # measured once per attack

    def _learn_rest(self, e, anim, t, p) -> None:
        """Where an attack leaves the attacker REST_AFTER s after its last hit: (along, across) metres from where
        the player stood when it started, along the line from the attacker to the player then (a bat's dive:
        ~2 m past the player; a soldier's lunge: in front of the player). Learned per attack (chains '_rest')."""
        rest = self.__dict__.setdefault('_rest_from', {})
        cur = rest.get(e.id)
        if cur is None or cur[0] != anim:
            if t < 0.3:
                rest[e.id] = [anim, e.pos, p, False]
            return
        w = self.windows(e.model, anim)
        if cur[3] or not w or t < max(end for _s, end in w) + REST_AFTER:
            return
        cur[3] = True
        e0, p0 = cur[1], cur[2]
        ux, uz = p0[0] - e0[0], p0[2] - e0[2]
        n = math.hypot(ux, uz)
        if n < 0.5:
            return
        ux, uz = ux / n, uz / n
        rx, rz = e.pos[0] - p0[0], e.pos[2] - p0[2]
        along, across = rx * ux + rz * uz, rx * uz - rz * ux
        table = self.chains.setdefault('_rest', {}).setdefault(_base(e.model), {})
        old = table.get(str(anim))
        if old is None:
            table[str(anim)] = [round(along, 2), round(across, 2), 1]
        else:
            k = min(old[2], 9)
            table[str(anim)] = [round((old[0] * k + along) / (k + 1), 2), round((old[1] * k + across) / (k + 1), 2),
                                old[2] + 1]
        self.chains_changed = True

    def _rest_spot(self, e, anim):
        """Where this attack (now playing) will leave the attacker, in the world, or None (not learned)."""
        known = self.chains.get('_rest', {}).get(_base(e.model), {}).get(str(anim)) \
            or REST_SEED.get(_base(e.model), {}).get(anim)
        cur = self.__dict__.get('_rest_from', {}).get(e.id)
        if not known or cur is None or cur[0] != anim:
            return None
        e0, p0 = cur[1], cur[2]
        ux, uz = p0[0] - e0[0], p0[2] - e0[2]
        n = math.hypot(ux, uz)
        if n < 0.5:
            return None
        ux, uz = ux / n, uz / n
        return (p0[0] + known[0] * ux + known[1] * uz, p0[1], p0[2] + known[0] * uz - known[1] * ux)

    def _learn_reach(self, snap: Snapshot, pl, p) -> None:
        """Health just went down: the enemy whose hit window is out now hit from this far. More than its
        reach from the data (the weapon models aren't in the model files: every Godrick Soldier variant has the
        same sword points, spear or not): that attack's reach is raised, kept with the combos."""
        hp = pl.health
        last = getattr(self, '_last_hp', None)
        self._last_hp = hp
        if hp is None or last is None or hp >= last - 0.5:
            return
        self._last_hurt = self.t
        best = None
        for x in snap.entities:
            if not x.hostile or x.dead:
                continue
            for a, t, _l in (x.anims or ((x.anim, x.anim_time, x.anim_length),)):
                w = self.windows(x.model, a) if a is not None else []
                if any(s0 - 0.1 <= t <= e0 + 0.15 for s0, e0 in w):
                    d = _flat(p, x.pos)
                    if best is None or d < best[0]:
                        best = (d, x, a)
        if best is None:
            return
        d, x, a = best
        try:
            data = self.reach_of(x.model, x.type_id, a) or 0.0
        except Exception:
            data = 0.0
        need = round(d - PLAYER_RADIUS - REACH_MARGIN + 0.2, 2)   # so the same hit counts as in reach
        table = self.chains.setdefault('_reach', {}).setdefault(_base(x.model), {})
        if need > max(data, table.get(str(a), 0.0)) + 0.1:
            table[str(a)] = need
            self.chains_changed = True
            self.events.append('learned_reach')

    def _threatens(self, e, p, anim, wait: float) -> bool | None:
        """Whether this attack can reach the player by the time it hits (None: its reach isn't known)."""
        try:
            reach = self.reach_of(e.model, e.type_id, anim)
        except Exception:
            reach = None
        learned = self.chains.get('_reach', {}).get(_base(e.model), {}).get(str(anim))
        if learned is not None:                # hits landed from further away than the data says
            reach = max(reach or 0.0, learned)
        if reach is None:
            return None
        if reach >= 20.0:          # a projectile or spell
            return _flat(p, e.pos) < reach
        lunge = self.chains.get('_lunge', {}).get(_base(e.model), {}).get(str(anim))
        vx, vz = self._vel.get(e.id, (0.0, 0.0))
        d = _flat(p, e.pos) or 1e-6
        toward = (vx * (p[0] - e.pos[0]) + vz * (p[2] - e.pos[2])) / d
        travel = max(LUNGE_DEFAULT if lunge is None else lunge, max(0.0, toward) * wait)
        if toward < -LEAVING_FAST and lunge is None:
            # flying off fast: no lunge assumed toward the player (twenty-fifth session: rolls for bats 5 m away
            # and leaving, 35 rolls in 50 s, the stamina gone)
            travel = 0.0
        return d <= reach + travel + PLAYER_RADIUS + REACH_MARGIN

    def _reeling(self, e) -> str | None:
        """'broken' when the target plays a long hit reaction (a stagger or stance break), 'flinch' when
        a reaction started after our last press (our hit landed), else None. Not while it also plays an
        attack with a hit still to come (hyper armor: the flinch is only a layer)."""
        playing = e.anims or ((e.anim, e.anim_time, e.anim_length),)
        for a, t, _l in playing:
            w = self.windows(e.model, a)
            if w and t <= max(end for _s, end in w):
                return None
        out = None
        for anim, t, length in playing:
            if not reacting(anim):
                continue
            if length and length >= BROKEN_REACTION and length - t > 0.3:
                return 'broken'
            if t < self.t - self._last_swing:
                out = 'flinch'
        return out

    def _lock_worth(self, e, aimed, p) -> bool:
        """Follow the game's lock-on to another enemy only when that one attacks or is clearly nearer, or the
        target isn't on the player (two enemies close by: the game's lock went back and forth between them and
        so did the target, 21 times in two minutes, twenty-seventh session; the camera is turned to the target
        anyway)."""
        return self._attack_anim(aimed) is not None or _flat(p, aimed.pos) < _flat(p, e.pos) - LOCK_NEARER \
            or not self._engaged(e, p)

    def _may_leave(self, e, other) -> bool:
        """The one the player asked for is left only for an enemy actually attacking (twenty-third session: told
        to kill a boar 8 m off, it followed the game's lock-on to a soldier coming over, and the boar lived)."""
        if e.id != self.goal_id or self._attack_anim(other) is not None:
            return True
        p = self._last_pos_player
        near = _flat(p, other.pos) - other.radius
        return near < LOCK_FOLLOW_NEAR and near < _flat(p, e.pos) - e.radius   # (or right here, nearer than it)

    def _may_switch(self, gap: float) -> bool:
        """Another target only after TARGET_HOLD s on this one, or when this one is far off."""
        return self.t - self._target_since >= TARGET_HOLD or gap > RETARGET_FAR + 3.0

    def _away_yaw(self, snap: Snapshot, p, e) -> float:
        """The heading away from the enemies near the player (or the target alone): of 16 headings the one
        most away from them, each weighted by nearness, so with enemies on several sides it is the widest gap
        between them (away from their middle led past the next soldier; twenty-second session); not into a
        wall or off a ledge the probes found while another way is clear."""
        near = [x for x in snap.entities if x.hostile and not x.dead and abs(x.pos[1] - p[1]) < 4.0
                and _flat(p, x.pos) < GROUP_NEAR + 2.0]
        if all(x.id != e.id for x in near):
            near.append(e)
        try:
            got = self.ledges()
            blocked = tuple(got[0]) + (tuple(got[2]) if len(got) > 2 else ())
        except Exception:
            blocked = ()
        bearings = [(_yaw((x.pos[0] - p[0], 0.0, x.pos[2] - p[2])), 1.0 / max(1.0, _flat(p, x.pos))) for x in near]
        total = sum(w for _a, w in bearings) or 1.0
        best = None
        for k in range(16):
            h = k * 22.5 - 180.0
            s = -sum(w * math.cos(math.radians(h - a)) for a, w in bearings) / total
            if any(abs(_wrap(h - b)) < LEDGE_NEAR for b in blocked):
                s -= 1.0
            if best is None or s > best[0]:
                best = (s, h)
        return best[1]

    def _charging(self, snap: Snapshot, p, exclude, horizon: float = PLAYER_COMMIT) -> bool:
        """Another enemy running toward the player that would be in reach within horizon s (2026-10-12: the
        third swing of a combo on one soldier while the next ran in from 5.4 m: its swing landed during ours).
        For one swing the horizon is shorter by CHARGE_WINDUP: arriving, it still has to swing (twenty-third
        session: in a pack someone always ran in, and that alone held back every swing)."""
        for x in snap.entities:
            if x.id == exclude or not x.hostile or x.dead or abs(x.pos[1] - p[1]) > 4.0:
                continue
            d = _flat(p, x.pos)
            if d > 15.0:
                continue
            vx, vz = self._vel.get(x.id, (0.0, 0.0))
            toward = (vx * (p[0] - x.pos[0]) + vz * (p[2] - x.pos[2])) / (d or 1e-6)
            if toward > CHARGE_SPEED and d - toward * horizon <= REACH_GUESS + PLAYER_RADIUS:
                return True
        return False

    def _near_log(self, snap: Snapshot, p) -> list:
        """For the combat log: the enemies near the player, nearest first: [id, model, metres, heading from the
        player, facing the player, (attack, seconds into it, its first hit) or None] (a hit from one that isn't
        the target couldn't be told apart before)."""
        out = []
        for x in sorted((x for x in snap.entities if x.hostile and not x.dead and _flat(p, x.pos) < 12.0),
                        key=lambda x: _flat(p, x.pos))[:6]:
            a = self._attack_anim(x)
            att = None
            if a is not None:
                t = next((t for b, t, _l in (x.anims or ((x.anim, x.anim_time, x.anim_length),)) if b == a), 0.0)
                w = self.windows(x.model, a)
                att = (a, round(t, 2), w[0][0] if w else None)
            out.append((x.id % 10000, x.model, round(_flat(p, x.pos), 1),
                        round(_yaw((x.pos[0] - p[0], 0.0, x.pos[2] - p[2]))), self._faces(x, p), att))
        return out

    def _lurking(self, snap: Snapshot, p, exclude) -> bool:
        """Another enemy within LURK_GAP of the player, attacking or facing it: it can start an attack and land
        it while the player is still in a swing (twenty-second session: hits from soldiers beside the target)."""
        for x in snap.entities:
            if x.id == exclude or not x.hostile or x.dead or abs(x.pos[1] - p[1]) > 4.0:
                continue
            if _flat(p, x.pos) - x.radius > LURK_GAP:
                continue
            if self._attack_anim(x) is not None or (self._faces(x, p) or 0.0) >= LURK_FACING:
                return True
        return False

    def _shielded(self, e) -> bool:
        return self.t - self._guarding.get(e.id, -1e9) < GUARD_FOR

    def _crowd_step(self, snap: Snapshot, p, soonest, doing: str, taps, away: float, cam_yaw: float):
        """Out of the middle of a crowd (CROWD): sprint away from them, then fight the first to follow."""
        def within(r):
            return sum(1 for x in snap.entities if x.hostile and not x.dead and abs(x.pos[1] - p[1]) < 4.0
                       and _flat(p, x.pos) < r)
        crowd = within(CROWD_RANGE)
        self.debug['crowd'] = crowd
        if self._final is not None:           # running clear, then over to the player
            if within(CLEAR_FOR_HANDBACK) == 0 or self.t - self._escaping_since > HANDBACK_RUN_S:
                self.stop(self._final)
                return Intent()
        elif self._escaping_since is None:
            hurt = self.t - getattr(self, '_last_hurt', -1e9) < CROWD_HURT_S
            if crowd < CROWD and not (hurt and crowd >= CROWD_HURT):
                return None
            self._escapes += 1
            self._escaping_since = self.t
            self.escape_route, self._escape_i = None, 0
            pl = snap.player
            healthy = pl.health is None or not pl.max_health or pl.health >= HEAL_AT * pl.max_health
            if self._escapes > CROWD_GIVE_UP and not healthy:
                # no shaking them and getting hurt: run clear, then over to the player (with full health it
                # handed back mid-fight, twenty-third session: it goes on instead)
                self._final = 'surrounded'
            self.events.append('escape')
        elif within(CROWD_CLEAR) <= 1 or self.t - self._escaping_since > CROWD_MAX_S:
            self._escaping_since = None        # out: fight whoever came first (the nearest)
            self.escape_route, self._escape_i = None, 0
            self._target_since = -1e9          # (a new target may be chosen at once)
            self._after_escape = self.t + AFTER_ESCAPE
            near = [x for x in snap.entities if x.hostile and not x.dead and abs(x.pos[1] - p[1]) < 4.0
                    and x.id not in self.unreachable]
            if near:
                nearest = min(near, key=lambda x: _flat(p, x.pos))
                if nearest.id != self.target_id:
                    self._switch(nearest, 'retarget')
            return None
        if self._locked:                      # locked on, moving is a slow backpedal: lock off to run
            self._locked = False
            taps = list(taps) + ['lock']
        if doing not in ('free', 'dodging'):
            return Intent(frozenset(), 0.0, tuple(taps))
        heading = self._escape_heading(p)
        if heading is not None:    # along the route to open ground the app found (not into a corner)
            rel = _wrap(heading - cam_yaw)
            keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(rel - d[0])))[1])
        else:                      # (until it comes: the widest way out between them, round walls and drops;
            rel = _wrap(away - cam_yaw)   # no sidestepping when slow: rolls cut it short, it flailed)
            keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(rel - d[0])))[1])
        if soonest is None or soonest[0] > DODGE_LEAD + SPRINT_LET_GO:
            keys.add('sprint')        # (let go well before a roll: 9 of 12 rolls pressed in a sprint didn't come)
        self.state = 'escaping'
        return Intent(frozenset(keys), 0.0, tuple(taps))

    def _escape_heading(self, p) -> float | None:
        """The heading along the escape route (its next waypoint not yet reached), or None."""
        route = self.escape_route
        if not route:
            return None
        while self._escape_i < len(route) - 1 and _flat(p, route[self._escape_i]) < ESCAPE_REACHED:
            self._escape_i += 1
        q = route[self._escape_i]
        if _flat(p, q) < ESCAPE_REACHED:
            return None
        return _yaw((q[0] - p[0], 0.0, q[2] - p[2]))

    def _roll_too_late(self, pl, soonest) -> bool:
        """In a roll's first ROLL_AGAIN s a roll press is held until then: true when the roll would only start
        after the hit has begun (pressing then rolls through the recovery that follows instead)."""
        if player_doing(pl.anim) != 'dodging' or pl.anim_time is None or pl.anim_time >= ROLL_AGAIN:
            return False
        return (ROLL_AGAIN - pl.anim_time) + IFRAME_START > soonest[0] + 0.03

    def _pick_roll(self, snap: Snapshot, p, soonest, away: bool, cam: float):
        """Where to roll: (score, keys, world heading), or None when every way drops away. The keys are read
        by the game relative to the camera, locked on or not, so each of the 8 directions is turned into the
        world and judged by where the roll lands (ROLL_DIST on): away (the attacker's next hit comes too soon
        to roll again) as far from the attacker as it gets, sideways to a lunge; through, past its side (45
        degrees off the line to it), close enough to punish. Either way away from every other enemy near the
        landing spot, along the escape route when getting room, not into a wall while another way is clear."""
        e = soonest[3]
        try:
            got = self.ledges()
            drops = tuple(got[1]) if len(got) > 1 else ()
            walls = tuple(got[2]) if len(got) > 2 else ()
        except Exception:
            drops, walls = (), ()
        others = [x for x in snap.entities if x.hostile and not x.dead and x.id != e.id
                  and abs(x.pos[1] - p[1]) < 4.0 and _flat(p, x.pos) < 10.0]
        heading = self._escape_heading(p) if self._escaping_since is not None else None
        at = _yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2]))
        lunge = self.chains.get('_lunge', {}).get(_base(e.model), {}).get(str(soonest[4]), 0.0)
        vx, vz = self._vel.get(e.id, (0.0, 0.0))
        d = _flat(p, e.pos) or 1e-6
        rushing = lunge > 2.0 or (vx * (p[0] - e.pos[0]) + vz * (p[2] - e.pos[2])) / d > 3.0
        through = 45.0 if ROLL_FORWARD else 90.0
        # Where the attack will leave it: land within reach of that (twenty-fifth session: rolling "through" a bat's
        # dive, toward where it came from, ended 5 m from where it hovered after passing: no swing ever landed)
        # (one on one only: with others near, landing next to one of them weighs more; it raised the damage in
        # a pack of soldiers by a quarter in the simulation)
        alone = not any(_flat(p, x.pos) - x.radius < PUNISH_ALONE for x in others)
        spot = self._rest_spot(e, soonest[4]) if alone and (not away or self._follow_up(soonest) is None) else None
        self.debug['rest_spot'] = None if spot is None else (round(spot[0], 1), round(spot[2], 1))
        best = None
        for rel, keys in DIRECTIONS:
            w = _wrap(cam + rel)
            if any(abs(_wrap(w - b)) < LEDGE_NEAR for b in drops):
                continue
            r = math.radians(w)
            q = (p[0] + math.sin(r) * ROLL_DIST, p[1], p[2] + math.cos(r) * ROLL_DIST)
            off = abs(_wrap(w - at))                  # 0: at the attacker, 180: straight away
            if away:
                score = _flat(q, e.pos) + (LUNGE_SIDEWAYS * math.sin(math.radians(off)) if rushing else 0.0)
            else:
                score = 3.0 - abs(off - through) / 30.0
            for x in others:
                dq = _flat(q, x.pos) - x.radius
                if dq < ROLL_CROWD:
                    score -= (ROLL_CROWD - dq) * (2.0 if self._attack_anim(x) is not None else 1.0)
            if spot is not None and 0.6 < _flat(q, spot) - e.radius <= self.my_reach - 0.2:
                score += PUNISH_SPOT
            if heading is not None:
                score += 3.0 * math.cos(math.radians(_wrap(w - heading)))
            if any(abs(_wrap(w - b)) < LEDGE_NEAR for b in walls):
                score -= 4.0                          # short of the wall, still in reach
            if self._roll_side in keys:
                score -= 0.05                         # (sides in turn, other things equal)
            if best is None or score > best[0]:
                best = (score, frozenset(keys), w)
        return best

    def _heal_step(self, snap: Snapshot, pl, p, soonest, doing: str, taps, away: float, cam_yaw: float):
        """Drinking a flask at HEAL_AT health, only when no hit can come before a roll is possible again:
        no predicted hit within HEAL_VULNERABLE, and no enemy near enough to close in and swing by then
        (its speed toward the player, a lunge, its reach). Not safe: back away to make room first. A drink
        that heals nothing twice (no flasks, or no flask in the item slot): no more tries."""
        if self.t < self._healing_until:            # drinking: no keys (a roll can't come out anyway)
            self.state = 'healing'
            return Intent(frozenset(), 0.0, tuple(taps))
        if self._heal_check is not None and self.t >= self._heal_check[0]:
            healed = pl.health is not None and pl.health > self._heal_check[1] + 1
            self._flask_fails = 0 if healed else self._flask_fails + 1
            if not healed and self._flask_fails == 2:
                self.events.append('no_flask')
            self._heal_check = None
        if pl.health is None or not pl.max_health or pl.health > HEAL_AT * pl.max_health:
            return None
        if self._flask_fails >= 2 or self.t - self._last_heal < HEAL_EVERY:
            return None
        safe = doing == 'free' and (soonest is None or soonest[0] > HEAL_VULNERABLE + 0.3)
        if safe:
            for x in snap.entities:
                if not x.hostile or x.dead or abs(x.pos[1] - p[1]) > 4.0:
                    continue
                d = _flat(p, x.pos)
                vx, vz = self._vel.get(x.id, (0.0, 0.0))
                toward = (vx * (p[0] - x.pos[0]) + vz * (p[2] - x.pos[2])) / (d or 1e-6)
                lunge = max(self.chains.get('_lunge', {}).get(_base(x.model), {}).values(), default=LUNGE_DEFAULT)
                # (its longest lunge: twenty-fourth session, a flask at 6 m from a soldier with a 4.5 m lunge)
                if d < REACH_GUESS + max(LUNGE_DEFAULT, lunge) + max(0.0, toward) * HEAL_VULNERABLE + PLAYER_RADIUS:
                    safe = False
                    break
        if safe:
            self._healing_until = self.t + HEAL_VULNERABLE
            self._last_heal = self.t
            self._heal_check = (self.t + HEAL_VULNERABLE + 0.6, pl.health)
            self.state = 'healing'
            self.events.append('heal')
            return Intent(frozenset(), 0.0, tuple(taps) + ('item',))
        self.state = 'retreating'                   # make room to drink
        return Intent(self._back_keys(p, away, cam_yaw), 0.0, tuple(taps))

    def _back_keys(self, p, away: float | None = None, cam_yaw: float = 0.0) -> frozenset:
        """Step away (from the enemies' middle when given, else straight back); the frame's terrain check
        (_safe: drops and walls) turns it to the nearest clear way. When that doesn't move the player (a
        rock behind), strafe sideways for a moment."""
        if self.t < self._strafe_until:
            return frozenset({self._strafe_side})
        if self._back_from is None:
            self._back_from = (self.t, p)
        elif self.t - self._back_from[0] >= BACK_CHECK:
            moved = _flat(p, self._back_from[1])
            self._back_from = (self.t, p)
            if moved < BACK_MIN:
                self._strafe_side = 'right' if self._strafe_side == 'left' else 'left'
                self._strafe_until = self.t + STRAFE_FOR
                self.events.append('blocked')
                return frozenset({self._strafe_side})
        if away is None:
            return frozenset({'back'})
        rel = _wrap(away - cam_yaw)
        return frozenset(min(DIRECTIONS, key=lambda d: abs(_wrap(rel - d[0])))[1])

    @staticmethod
    def _faces(e, p) -> float | None:
        if e.facing is None:
            return None
        d = (p[0] - e.pos[0], p[2] - e.pos[2])
        n = math.hypot(*d) or 1.0
        return round((e.facing[0] * d[0] + e.facing[2] * d[1]) / n, 2)

    def _safe(self, keys: frozenset, cam_yaw: float, roll: bool) -> frozenset:
        """The keys, unless that way the ground drops away (ledges()): then the nearest safe direction within
        90 degrees, else no movement. For a roll only the keys themselves or nothing."""
        try:
            got = self.ledges()
            blocked = tuple(got[1 if roll else 0]) + (() if roll or len(got) < 3 else tuple(got[2]))
        except Exception:
            blocked = ()
        if not keys or not blocked:
            return frozenset(keys)
        extra = set(keys) - {'forward', 'back', 'left', 'right'}     # sprint rides along
        keys = frozenset(set(keys) - extra)
        rel = next((a for a, k in DIRECTIONS if set(k) == set(keys)), None)
        if rel is None:
            return frozenset(keys | extra)

        def bad(r):
            return any(abs(_wrap(cam_yaw + r - b)) < LEDGE_NEAR for b in blocked)
        if not bad(rel):
            return frozenset(keys | extra)
        if roll:
            return frozenset()
        for a, k in sorted(DIRECTIONS, key=lambda d: abs(_wrap(d[0] - rel))):
            if abs(_wrap(a - rel)) <= 90 and not bad(a):
                return frozenset(set(k) | extra)
        return frozenset()

    def _threat(self, snap: Snapshot, p, exclude: int | None, cam=None):
        """The best other enemy engaged with the player (_engaged), by target_score, or None."""
        best = None
        for x in snap.entities:
            if x.id == exclude or (x.health is not None and x.health <= 0) or x.id in self.unreachable \
                    or not self._engaged(x, p):
                continue
            score = target_score(x, p, cam)
            if best is None or score < best[0]:
                best = (score, x)
        return best[1] if best else None

    def _engaged(self, x, p, reach: float = ENGAGED_RANGE) -> bool:
        """A living hostile enemy on the player's level that is on the player: attacking (within ENGAGED_RANGE),
        or facing the player and within RETARGET_NEAR, or facing the player and coming closer (whatever it is:
        a wolf joining a fight with soldiers is fought too)."""
        if not x.hostile or x.dead or abs(x.pos[1] - p[1]) > 4.0:
            return False
        d = _flat(p, x.pos)
        gap = d - x.radius
        if gap > reach:
            return False
        if self._attack_anim(x) is not None:
            return True
        f = self._faces(x, p)
        if f is None or f < 0.5:
            return False
        if gap <= RETARGET_NEAR:
            return True
        vx, vz = self._vel.get(x.id, (0.0, 0.0))
        return (vx * (p[0] - x.pos[0]) + vz * (p[2] - x.pos[2])) / (d or 1e-6) > 1.0

    @staticmethod
    def _aimed(snap: Snapshot, p):
        """The living hostile enemy the camera points at (who the game's lock-on is on), or None."""
        cam = _yaw(snap.camera.forward)
        best = None
        for x in snap.entities:
            if not x.hostile or x.dead or _flat(p, x.pos) > 20.0:
                continue
            off = abs(_wrap(_yaw((x.pos[0] - p[0], 0.0, x.pos[2] - p[2])) - cam))
            if off < LOCK_AIM and (best is None or off < best[0]):
                best = (off, x)
        return best[1] if best else None

    def _switch(self, e, why: str) -> None:
        self._target_since = self._timer_from = self.t
        self.target_id, self.name = e.id, None
        self._poise, self._combo = None, 0
        self._close_ref, self._detours, self._swing_check = None, 0, None
        self._last_enemy_attack = self.t
        self.events.append(why)

    def _counters(self, e) -> bool:
        """Whether this kind of enemy is known to counter out of a flinch."""
        base = _base(e.model)
        return base in COUNTER_SEED or \
            any(n >= COUNTER_SEEN for n in self.chains.get('_counters', {}).get(base, {}).values())

    def _follow_up(self, hit) -> float | None:
        """Seconds from this hit's start to the same enemy's next hit: a later window of the same attack,
        or the first hit of an attack learned to follow it (chains), or None."""
        _wait, _key, _len, e, anim, start, speed = hit
        gaps = [s - start for s, _e in self.windows(e.model, anim) if s > start + 0.05]
        for switch, nxt in self.chains.get(_base(e.model), {}).get(str(anim), []):
            w = self.windows(e.model, nxt)
            if w:
                gaps.append(switch - start + w[0][0])
        gaps = [g / speed for g in gaps if g > 0]
        return min(gaps) if gaps else None

    def _learn_chains(self, snap: Snapshot, p) -> None:
        """An enemy near the player going from one attack straight into another: remember it. An attack
        followed by none within SOLO_WAIT: count it as ending on its own."""
        ended = self.__dict__.setdefault('_ended', {})
        flinched = self.__dict__.setdefault('_flinched', {})
        for e in snap.entities:
            if not e.hostile or e.dead or e.model is None or _flat(p, e.pos) > 15.0:
                continue
            every = e.anims or ((e.anim, e.anim_time, e.anim_length),)
            if any(a is not None and 8000 <= a % 1_000_000 < 9000 for a, _t, _l in every):
                flinched[e.id] = self.t                     # (8XXX: a flinch, not the 9XXX layers)
            playing = [(a, t) for a, t, _l in every if a is not None and self.windows(e.model, a)]
            if not playing:
                prev = self._attacking.get(e.id)
                if prev is not None and e.id not in ended:
                    ended[e.id] = (prev[0], self.t)
                done = ended.get(e.id)
                if done is not None and self.t - done[1] >= SOLO_WAIT:
                    table = self.chains.setdefault('_solo', {}).setdefault(_base(e.model), {})
                    table[str(done[0])] = table.get(str(done[0]), 0) + 1
                    self.chains_changed = True
                    del ended[e.id]
                    self._attacking.pop(e.id, None)
                continue
            ended.pop(e.id, None)
            anim, t = min(playing, key=lambda x: x[1])      # the newest attack
            prev = self._attacking.get(e.id)
            pending = self.__dict__.setdefault('_counter_pending', {})
            if t <= CHAIN_SEEN and self.t - flinched.get(e.id, -1e9) <= COUNTER_WITHIN \
                    and (prev is None or prev[0] != anim):
                pending[e.id] = anim                       # out of a flinch straight into an attack...
                flinched.pop(e.id, None)
            if pending.get(e.id) == anim:
                w = self.windows(e.model, anim)
                if w and t >= w[0][0]:
                    # ...that reaches the player when it hits: its counter (kept; COUNTER_SEEN times: no combos).
                    # (A bat flinching flies off, 1006001 with a hitbox far away: taken for a counter, it held the
                    # combos back on bats; twenty-seventh session, in the simulation 2.5 -> 1.3 bats killed.)
                    del pending[e.id]
                    if self._threatens(e, p, anim, 0.0) is not False and _flat(p, e.pos) - e.radius < COUNTER_NEAR:
                        table = self.chains.setdefault('_counters', {}).setdefault(_base(e.model), {})
                        table[str(anim)] = table.get(str(anim), 0) + 1
                        self.chains_changed = True
            self._attacking[e.id] = (anim, t, self.t)
            if prev is None or prev[0] == anim or t > CHAIN_SEEN or self.t - prev[2] > 0.4:
                continue
            switch = round(prev[1] + (self.t - prev[2]) - t, 2)
            known = self.chains.setdefault(_base(e.model), {}).setdefault(str(prev[0]), [])
            for item in known:
                if item[1] == anim:
                    if switch < item[0]:
                        item[0] = switch
                        self.chains_changed = True
                    break
            else:
                known.append([switch, anim])
                self.chains_changed = True

    def _next_hit(self, snap: Snapshot, p, exclude: int | None = None) -> tuple | None:
        """(seconds until the soonest hit lands on the player, its key), from any hostile enemy close
        enough and facing the player; 0 when a hitbox is out right now and no roll covered it."""
        best = None
        for e in snap.entities:
            if not e.hostile or e.dead or e.anim is None or e.id == exclude:
                continue
            gap = _flat(p, e.pos) - e.radius
            if gap > 30.0:
                continue
            # Facing only filters other enemies further away: the one we fight, or anyone close, turns
            # toward the player during the wind-up (three hits came from attacks skipped as "facing away").
            if e.id != self.target_id and gap > CLOSE_THREAT and e.facing is not None:
                d = (p[0] - e.pos[0], p[2] - e.pos[2])
                n = math.hypot(*d) or 1.0
                if (e.facing[0] * d[0] + e.facing[2] * d[1]) / n < FACING_COS:
                    continue
            speed = e.anim_speed if e.anim_speed > 0 else 1.0
            playing = e.anims or ((e.anim, e.anim_time, e.anim_length),)
            for anim, t, _length in playing:  # several play at once: any of them may be the attack
                for start, end in self.windows(e.model, anim):
                    key = (e.id, anim, start)
                    land = self.t + max(0.0, (start - t) / speed)
                    if key in self._covered and abs(self._covered[key] - land) < COVERED_SLACK:
                        continue
                    if t <= end:
                        wait = max(0.0, (start - t) / speed)
                        near = self._threatens(e, p, anim, wait)
                        if near is False or (near is None and gap > THREAT_REACH + e.radius):
                            break      # out of its reach (or, reach unknown, beyond the fixed distance)
                        if best is None or wait < best[0]:
                            best = (wait, key, (end - start) / speed, e, anim, start, speed)
                        break
        return best
