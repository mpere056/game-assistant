"""Fighting where a swing must charge up and is aimed with the camera (no lock-on, no roll): Minecraft first.

The player's next swing does full damage only when charged (attack_strength 1: a sword about 0.6 s after the last
swing, a fist 0.25 s); a hit lands on what the crosshair is on, within reach (3 blocks). So:

  aim     turn and tilt the camera onto the target's body every frame
  close   walk at it (jumping up a step), zig-zagging when it shoots (a skeleton's arrows); stop at the edge of
          reach and back off a little if it's on top of the player
  hit     when charged, the crosshair on it, and within reach: one click; then step back a moment (out of its
          own reach while the swing charges again: hit and run)
  block   with a shield in the off hand: hold it up while a shooter draws its bow, or a fighter is about to
          hit and the swing isn't charged yet
  creepers  whichever one is fought: never near one unless to hit it; one swelling (its 1.5 s fuse lit when
          the player came within 3) is got away from at once, out past 7 (where it calms down; its blast hurts
          nothing past 6). With a weapon that kills it in a few hits: hit, then away till it has calmed; with
          nothing (a fist: 20 hits): let it blow up harmlessly (near enough to light it, then held 6.4-6.8 off
          till it goes)
  give up on low health (getting away first, from what can be outrun; what can't, a spider, is fought on), a
          target that can't be got at, or a minute's fight

What each kind of character is (explodes, shoots, how far it reaches) comes from the adapter (combat_profile);
its state (swelling, aggressive, drawing a bow) from the snapshot's entity extras. The app drives this like the
other fighter (core/combat.py): start(target), update(snap, dt) -> Intent, events ('won', 'target_gone',
'low_health', 'unreachable', 'timeout').
"""
from __future__ import annotations

import math
import threading

from .autowalk import Intent, _flat, _wrap, _yaw, aim_at, cam_yaw, level_forward

REACH = 3.0             # blocks from the eye to the target's body that a swing reaches
HOLD_AT = 2.4           # stop closing in this near (centre to centre, across)
TOO_CLOSE = 1.1         # back off when nearer
CHARGED = 0.95          # attack strength for a full-damage swing
AFTER_HIT = 0.35        # seconds of backing off after a hit
ZIGZAG = 0.6            # seconds each way, closing in on a shooter
IGNITE = 3.0            # a creeper lights its fuse (1.5 s) with the player this near (feet to feet)...
CALM = 7.0              # ...and puts it out, going back down, with the player farther than this
CALM_MARGIN = 0.6       # (out past CALM by this much: the positions read lag a frame)
BAIT_HOLD = (6.4, 6.8)  # nothing to kill it with: held here while it swells, it blows up hurting nothing (past 6)
BAIT_IN = 2.6           # ...after coming this near to light it
KILL_DAMAGE = 4.0       # a weapon hitting this hard (a sword, an axe) kills a creeper in a few hits; less (a fist,
                        # 1: two creepers hurt the player while it punched at one, 2026-10-13): baited instead
BLAST_LOOK = 0.15       # seconds ahead each way is tried, getting clear of a blast (the band held is narrow)
LOW_HEALTH = 0.3        # of max health: over to the player
START_HEALTH = 0.5      # ...and below this, no new fight is started (nor the next of several): away first (one
                        # zombie after another down to 3 health, the next one started, and the player died,
                        # 2026-10-16)
ROOM_ENOUGH = 10        # cells to be got to from a spot within a few steps: enough; fewer (a corner, a dead end)
ROOM_WEIGHT = 2.5       # ...count against it this much at most (as a pull toward open ground, everywhere, it
                        # walked into the zombies to get there: twice the hits, in the test arena)
PATH_AT = (0.5, 1.0)    # blocks along a way that must be ground to stand on (2 blocks on barred every way near a
                        # wall, sliding along it too)
BLOCKED_MOVE = 0.12     # blocks moved in BLOCKED_AFTER s with keys held: blocked (a wall, a fence, a creature)
BLOCKED_AFTER = 0.4
BLOCKED_FOR = 1.5       # seconds a way found blocked isn't tried
MOB_DPS = 3.0           # damage a second one fighter up close does (Minecraft's zombie: 3 a hit, one a second;
                        # reckoned at half that, a zombie in a corner took the player from 10 to dead in 4 s with bare
                        # hands, the fight judged winnable, 2026-10-16)
KITE = 0.15             # ...out on ground with room (hit, back off out of reach: three or four zombies held off
                        # 26 s without a hit taken, 2026-10-16), this much of it; in a corner, all of it
RETREAT_TRY = 1.2       # seconds of getting away with one still within RETREAT_GAIN: no way away; fought instead
RETREAT_GAIN = 2.0      # (boxed in at the bottom of a pit, 6 s "retreating" with four zombies on the player, not one
                        # swing back: dead, 2026-10-16)
HOLD_REACH = 3.5        # holding ground: only what comes this near is gone for...
HOLD_WAIT = 10.0        # ...and if none comes in this long: 'held_off' (they're staying where they are)
EXPOSED_COST = 1.5      # a way where one shooting at the player sees them, against the rest
EXPOSED_EVERY = 0.3     # seconds between looks at where those shooting see the player
SHOOTERS_NEAR = 16.0    # blocks: those shooting this near count
ODDS_MARGIN = 0.6       # a fight is taken on when what it would cost is under this much of the health there is
ODDS_EVERY = 1.0        # seconds between looks at the odds in a fight (more come)
PERCH_HIGH = 2          # blocks up a pillar out of a ground fighter's reach (its reach doesn't go up past its top)
PERCH_APEX = 1.0        # the jump's speed up (blocks a second) under which a block is put under the feet
PERCH_UP_S = 4.0        # seconds to get up there before it's given up (a ceiling, no block in hand)
PERCH_DONE = 6.0        # none of them this near...
PERCH_WATCH = 16.0      # ...and none set on the player this near: the fight's over
PERCH_TIMEOUT = 240.0   # seconds up there at most (bare hands take long, but nothing reaches us)
SWING_S = 0.625         # seconds a weapon's swing charges, when what's held isn't it (a sword's)
FIGHT_TIMEOUT = 60.0
STUCK_S = 4.0           # seconds out of reach without getting any nearer: jump; twice that: unreachable
SWITCH_NEAR = 8.0       # another hostile one after the player this near...
SWITCH_GAP = 0.5        # ...and this much nearer than the target: fought first (the nearest attacker always)
PLAYER_RADIUS = 0.3     # the player's half width
MOB_REACH = 0.83        # how far past their own and the player's width most fighters hit (Minecraft's mobs)
KEEP_MARGIN = 0.6       # kept out of a fighter's reach by this much more...
CHARGING_MARGIN = 0.5   # ...and this much while the swing charges (it keeps walking at us)
OTHERS_MARGIN = 0.8
HIT_AT = 2.7            # step in, charged, to this far from the target's edge (our reach is 3 from the eye)
BLAST_STUCK = 0.15      # seconds getting clear of a blast, the player got less than...
BLAST_STUCK_PART = 0.35 # ...this much of a walk's worth on the way taken: that way's blocked
CREEPER_FIRST = 4.5     # a creeper this near: fought before the target (it comes on and lights)
CREEPER_KEEP = 3.8      # never nearer a creeper than this (it lights at 3), but to hit it
FOE_RANGE = 8.0         # hostile ones this near count in where to stand
WALK_SPEED = 4.3        # blocks a second, the player walking (any way)
MOB_SPEED = 2.3         # ...and a fighter coming at them
LOOK_AHEAD = 0.45       # seconds ahead each way is tried (out of reach)...
STEP_AHEAD = 0.15       # ...and for how near the target it brings us
CLEAR_ENOUGH = 0.5      # blocks out of every reach (on top of the margins): as good as more (farther wasted
                        # time walking back in: "it's taking way too long to kill anything", 2026-10-13)...
CLEAR_CHARGED = 0.4     # ...with the swing charged (stepping in to hit)
CLEAR_OTHERS = 1.5      # out of the other ones' reach: as good as more...
CLEAR_CHARGED_OTHERS = 0.6   # ...with the swing charged (else two side by side: never stepping in to hit one)
STEP_IN_FROM = 0.75     # attack strength from which to step in (arriving as it's charged)
CRIT_FROM = 0.85        # ...and to jump for a critical hit (falling at the top of the charge)
CRIT_WAIT = 0.6         # seconds after a jump to wait for the fall before hitting anyway
CRIT_EVERY = 0.9        # seconds between jumps
CRIT_CLEAR = 4.0        # no other hostile one this near for a jump
IDLE_HALVES = 1.0       # seconds of waiting, ready, that halve the cost of a step into water
WATER_COST = 1.0        # a step into water, against the rest (out of reaches, nearer the target): slow, not barred
LAND_PULL = 1.5         # in water: how much more a way onto land counts
RETREAT_CLEAR = 12.0    # low on health: away till no hostile one is this near...
RETREAT_S = 8.0         # ...or this long
BLOCKED_S = 0.6         # seconds with no straight way in before a route is planned
ROUTE_AGE = 3.0         # seconds before a route to a moving target is planned again
TERRAIN_EVERY = 0.25    # seconds between looks at the ground round the player (water, lava, drops)
SHIELDS = ('minecraft:shield',)


TOWARD = 0.75          # cos of the angle within which a move heads into a cell to avoid
DIRS8 = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
KEY_ANGLES = {('forward',): 0, ('forward', 'right'): 45, ('right',): 90, ('back', 'right'): 135, ('back',): 180,
              ('back', 'left'): -135, ('left',): -90, ('forward', 'left'): -45}


def _dir_of(keys: set, cam) -> tuple:
    """The way (x, z) these movement keys take the player, with the camera as it is."""
    fx = ('forward' in keys) - ('back' in keys)
    sx = ('right' in keys) - ('left' in keys)
    f = level_forward(cam)
    r = (cam.right[0], cam.right[2])
    n = math.hypot(*r) or 1.0
    r = (r[0] / n, r[1] / n)
    return (f[0] * fx + r[0] * sx, f[1] * fx + r[1] * sx)


def _toward(d, avoid: dict) -> bool:
    """Whether moving along d heads into one of the cells to avoid (within about 40 degrees of it: a way along
    the shore that drifts toward the water a little each step still ends in it)."""
    n = math.hypot(*d)
    if n < 1e-6:
        return False
    for (cx, cz) in avoid:
        m = math.hypot(cx, cz)
        if (d[0] * cx + d[1] * cz) / (n * m) > TOWARD:
            return True
    return False


def _to_box(eye, e) -> tuple[float, tuple]:
    """How far the eye is from the nearest point of a creature's body (its box: as the game measures a swing's
    reach), and that point."""
    lo = (e.pos[0] - e.radius, e.pos[1], e.pos[2] - e.radius)
    hi = (e.pos[0] + e.radius, e.pos[1] + e.height, e.pos[2] + e.radius)
    q = tuple(min(max(eye[i], lo[i]), hi[i]) for i in range(3))
    return math.dist(eye, q), q


def _swell(e) -> float:
    """How far a creeper's fuse has burnt (0: not lit; 1: going off)."""
    return float((e.extra or {}).get('swell') or 0.0)


def _cell_of(d) -> tuple:
    """The neighbouring cell (dx, dz) a direction points at."""
    a = math.atan2(d[1], d[0])
    k = round(a / (math.pi / 4)) % 8
    return DIRS8[k]


def _keys_toward(d, cam) -> set:
    """The movement keys that go most nearly in direction d (x, z)."""
    want = _yaw((d[0], 0.0, d[1]))
    err = _wrap(want - cam_yaw(cam))
    best = min(KEY_ANGLES.items(), key=lambda kv: abs(_wrap(err - kv[1])))
    return set(best[0])


class MeleeFighter:
    def __init__(self, profile=lambda e: {}, weapon_slot=lambda hotbar: None, terrain=lambda: {}, route_fn=None,
                 weapon_damage=lambda hotbar: None, sight=None):
        self.profile = profile          # adapter.combat_profile(entity) -> {'explodes', 'ranged', 'reach', 'fast'}
        self.weapon_slot = weapon_slot  # adapter.weapon_slot(hotbar) -> the slot to fight with
        self.weapon_damage = weapon_damage   # adapter.weapon_damage(hotbar) -> the best one's damage (None: not known)
        self.sight = sight              # adapter.sight_pairs([(eye, point)]) -> [in sight]: where those shooting see
        self._exposed = (-1e9, None, {})   # (when, the player's cell, {way: how many shooting see it there})
        self.terrain = terrain          # adapter.terrain() -> {'in_water', 'avoid': {(dx, dz): what}}
        self.route_fn = route_fn        # adapter.route(start, goal): a way to a target there's no straight way to
        self._ground = ({}, -1e9)
        self._steered = False
        self.active = False
        self.state = 'idle'
        self.events: list = []
        self.kills: list = []
        self.unreachable: set = set()
        self.target_id = None
        self.name = None
        self.debug: dict = {}
        # (what the app reads from the other fighter: none of it applies here)
        self.chains: dict = {}
        self.chains_changed = False
        self.escape_note = None
        self.escape_route = None
        self._escaping_since = None
        self.t = 0.0

    def windows(self, model, anim) -> list:
        return []

    def _safe(self, keys, cam_yaw, roll=False):
        return frozenset(keys)

    def _engaged(self, e, p, reach: float = 6.0) -> bool:
        """A hostile one this near that's after the player (aggressive)."""
        if not e.hostile or e.dead:
            return False
        if math.dist(e.pos, p) > reach:
            return False
        return bool((e.extra or {}).get('aggressive', True))

    def _spacing(self, snap, e, prof: dict, charged: bool, across: float) -> set:
        """The way to move (or stand): each way (the 8 and staying) is tried a moment ahead (LOOK_AHEAD s), the
        player at walking pace and every hostile one near coming at them; the way kept farthest out of all their
        reaches wins, and, with the swing charged (or a shooter), one that brings the target to the edge of our
        reach. Never into water, lava, a drop or a wall. (Adding up "away from each" cancelled out between a
        zombie ahead and two behind, and walked into the one ahead, 2026-10-13.)"""
        p = snap.player.pos
        # where we can hit it from (centre to centre, across): nearer across when it's up or down from the eye (2.8
        # across, a skeleton up on a ledge was out of reach, and waited for, shot at meanwhile, 2026-10-16)
        eye_y = snap.camera.pos[1] if snap.camera is not None else p[1] + 1.62
        up = max(0.0, e.pos[1] - eye_y, eye_y - (e.pos[1] + e.height))
        ours = e.radius + min(HIT_AT, math.sqrt(max(0.25, (REACH - 0.3) ** 2 - up * up)))
        self._ground_now(p)
        ground = self._ground[0] or {}
        avoid = ground.get('avoid') or {}
        in_water = bool(ground.get('in_water'))
        wet = {c for c, k in avoid.items() if k == 'water'}
        # water is slow, not forbidden (lava, drops and walls are): kept out of it, the fighter waited 40 s on
        # the shore for a zombie in the water, 2026-10-13. Going in costs WATER_COST; out of it, land pulls.
        avoid = {c: k for c, k in avoid.items() if k != 'water'}
        self._steered = True
        foes = []
        for o in snap.entities:
            if o.dead or not o.hostile:
                continue
            if o.id != e.id and not (o.extra or {}).get('aggressive', False):
                continue                                          # (an Enderman minding its own business)
            if _flat(p, o.pos) > FOE_RANGE or abs(o.pos[1] - p[1]) > 3.0:
                continue
            po = self.profile(o) or {}
            if po.get('ranged'):
                reach = 0.0                                       # (a shooter doesn't hit up close)
            elif po.get('explodes'):
                # (stepping in to hit the one fought, calm and charged: in, the hit knocks it back; kept off
                # while ours charged, it backed off across the map with the creeper after it)
                reach = CALM + CALM_MARGIN if _swell(o) > 0.02 else 0.0 if (charged and o.id == e.id) \
                    else CREEPER_KEEP
            else:
                reach = o.radius + PLAYER_RADIUS + (po.get('reach') or MOB_REACH) + KEEP_MARGIN
                if not (charged and o.id == e.id):
                    reach += CHARGING_MARGIN                      # (while ours charges it keeps coming)
            foes.append((o, reach))
        just_hit = self.t - self.hit_at < AFTER_HIT * (2.0 if prof.get('explodes') else 1.0)
        scored = []
        open_at, room = ground.get('open'), ground.get('room')
        for c in [(0, 0)] + DIRS8:
            m = math.hypot(*c)
            if m and (_toward(c, avoid) or self.blocked.get(c, -1.0) > self.t):
                continue
            u = (c[0] / m, c[1] / m) if m else (0.0, 0.0)
            q = (p[0] + u[0] * WALK_SPEED * LOOK_AHEAD, p[2] + u[1] * WALK_SPEED * LOOK_AHEAD)
            # all the way there on ground to stand on (one cell round the player wasn't enough: backing off a step
            # at a time, it went into a dead end, and a zombie killed the player there, 2026-10-16)
            if m and open_at is not None and not in_water and not all(
                    open_at(p[0] + u[0] * s, p[2] + u[1] * s) for s in PATH_AT):
                continue
            if m and getattr(self, 'hold', False) and room is not None and room(*q) < ROOM_ENOUGH <= room(p[0], p[2]):
                continue                                          # (holding ground: never in after them)
            qs = (p[0] + u[0] * WALK_SPEED * STEP_AHEAD, p[2] + u[1] * WALK_SPEED * STEP_AHEAD)
            clear_t, clear_o = math.inf, math.inf                 # out of the target's reach; out of the others'
            stepping_in = charged and not just_hit
            for o, reach in foes:
                if o.id == e.id and stepping_in:
                    # (charged, stepping in: the hit comes first and knocks it back; judged a short step on, it
                    # standing still, or it never stepped in: waiting half a block out of reach, 2026-10-13)
                    c_ = math.hypot(qs[0] - o.pos[0], qs[1] - o.pos[2]) - reach
                    clear_t = min(clear_t, c_)
                    continue
                v = (p[0] - o.pos[0], p[2] - o.pos[2])
                dv = math.hypot(*v) or 1e-6
                ahead = MOB_SPEED * LOOK_AHEAD if dv > 1.0 else 0.0
                mo = (o.pos[0] + v[0] / dv * ahead, o.pos[2] + v[1] / dv * ahead)
                c_ = math.hypot(q[0] - mo[0], q[1] - mo[1]) - reach
                if o.id == e.id:
                    clear_t = min(clear_t, c_)
                else:
                    clear_o = min(clear_o, c_)
            # (charged: a little room from the target is enough, more would keep it from stepping in to hit;
            # from the others, always as much as can be had: letting them close in while stepping in to hit took
            # a hit with three around)
            enough = CLEAR_CHARGED if (charged and not just_hit) else CLEAR_ENOUGH
            score = 0.0
            if clear_t < math.inf:
                score += min(clear_t, enough) * 3.0
            if clear_o < math.inf:                                # (their reach has the margins in already)
                score += min(clear_o, CLEAR_CHARGED_OTHERS if (charged and not just_hit) else CLEAR_OTHERS) * 3.0
            clear = min(clear_t, clear_o)
            if clear < 0:
                score -= 10.0 - clear                             # into a reach: only the least bad of those
            # (the distance to the target a short step on: a whole LOOK_AHEAD's walk overshot the edge of our
            # reach, and standing 0.5 out of it scored better: it waited there, 2026-10-13)
            dt_ = math.hypot(qs[0] - e.pos[0], qs[1] - e.pos[2])
            if (charged and not just_hit) or prof.get('ranged'):
                score -= abs(dt_ - ours) * 1.5                    # in to the edge of our reach
            else:
                score -= max(0.0, dt_ - (ours + 1.0)) * 1.0       # not drifting far off while it charges
            if room is not None and not in_water:            # room to move from there: never into a corner
                score -= max(0, ROOM_ENOUGH - room(*q)) / ROOM_ENOUGH * ROOM_WEIGHT
            score -= self._seen_at(snap, e, c, q) * EXPOSED_COST   # (out of the arrows, fighting another)
            if m == 0:
                score += 0.05                                     # (standing still, all else equal)
            if in_water:
                score += 0.0 if (m == 0 or c in wet) else LAND_PULL   # out onto land
            elif m and c in wet:
                # in: only when it pays, and more readily the longer we've been ready to hit with nothing in
                # reach (waiting on the shore for one that stays in the water is the slow way)
                score -= WATER_COST / (1.0 + self.idle_ready / IDLE_HALVES)
            scored.append((score, c))
        # the best way the keys really go (8 ways round the camera, not the world: up to 22 degrees off)
        best = None
        for _score, c in sorted(scored, key=lambda x: -x[0]):
            if c == (0, 0) or not _toward(_dir_of(_keys_toward(c, snap.camera), snap.camera), avoid):
                best = c
                break
        keys = {'jump'} if in_water else set()   # (afloat)
        d = _flat(p, e.pos)
        self.state = ('backing' if just_hit else 'closing' if charged else 'spacing') + \
            (' (others near)' if len(foes) > 1 else '')
        if best is None or best == (0, 0):
            self.state = 'waiting' + (' (others near)' if len(foes) > 1 else '')
            return keys
        keys |= _keys_toward(best, snap.camera)
        if keys & {'forward'} and not keys & {'left', 'right', 'back'} and d > 6.0 and not prof.get('explodes'):
            keys.add('sprint')
        if prof.get('ranged') and d > 3.0 and 'forward' in keys:     # zig-zag in on a shooter
            if self.t - self.zig_at > ZIGZAG:
                self.zig, self.zig_at = -self.zig, self.t
            keys.discard('left')
            keys.discard('right')
            keys.add('left' if self.zig > 0 else 'right')
        if e.pos[1] - snap.player.pos[1] > 0.6 and d < 3.0 and 'forward' in keys:   # it's up a step
            keys.add('jump')
        return keys

    def _follow_route(self, snap, e) -> set | None:
        """Keys along a route to the target (planned on a thread, again when it has moved 2 blocks or after
        ROUTE_AGE s), the camera still on it. None: no route (yet), or one that needs blocks broken or placed."""
        p = snap.player.pos
        stale = self.route_for is None or math.dist(self.route_for[0], e.pos) > 2.0 \
            or self.t - self.route_for[1] > ROUTE_AGE
        if stale and not self._planning and self.route_fn is not None:
            self._planning = True
            start, goal = tuple(p), tuple(e.pos)
            self.route_for = (goal, self.t)

            def plan():
                try:
                    r = self.route_fn(start, goal)
                    ok = r is not None and len(r.waypoints) >= 2 and not any(
                        a[0] in ('break', 'place') for a in getattr(r, 'actions', []))
                    self.route = list(r.waypoints) if ok else []
                except Exception:
                    self.route = []
                finally:
                    self._planning = False
            threading.Thread(target=plan, name='fight route', daemon=True).start()
        if not self.route:
            if self.route == [] and self.t - self._blocked_since > STUCK_S:
                self.in_reach_at = min(self.in_reach_at, self.t - STUCK_S * 2)   # no way at all: unreachable
            return None
        while len(self.route) > 1 and _flat(p, self.route[0]) < 0.5 and abs(p[1] - self.route[0][1]) < 1.0:
            self.route.pop(0)
        nxt = self.route[0]
        d = (nxt[0] - p[0], nxt[2] - p[2])
        if math.hypot(*d) < 0.15:
            return set()
        keys = _keys_toward(d, snap.camera)
        if nxt[1] - p[1] > 0.4:
            keys.add('jump')
        self.state = 'routing'
        return keys

    def _retreat(self, snap) -> Intent:
        """Low on health: away from every hostile one near (the way kept farthest from all of them, never into
        water, lava, a drop or a wall), sprinting, till none is within RETREAT_CLEAR or RETREAT_S is up; then
        over to the player ('low_health')."""
        p = snap.player.pos
        foes = [o for o in snap.entities if o.hostile and not o.dead and _flat(p, o.pos) < RETREAT_CLEAR
                and abs(o.pos[1] - p[1]) < 4.0]
        if not foes or self.t - self.retreating > RETREAT_S:
            if any(_flat(p, o.pos) < 3.0 and (o.extra or {}).get('aggressive', False) for o in foes):
                self.retreating, self.stand = None, True     # still on the player: fought on, not handed over
                return Intent()
            self.events.append(self.retreat_why or 'low_health')
            self.stop()
            return Intent()
        self.state = 'retreating'
        nearest = min(_flat(p, o.pos) for o in foes)
        if self.t - self.retreating > RETREAT_TRY and nearest < RETREAT_GAIN:
            self.retreating, self.stand = None, True     # (no way away: hit back, the best there is...)
            self.events.append('boxed')                  # (...unless a way out is planned: the app's)
            return Intent()
        self._ground_now(p)
        ground = self._ground[0] or {}
        avoid = ground.get('avoid') or {}
        if ground.get('in_water'):
            avoid = {c: k for c, k in avoid.items() if k != 'water'}
        best, best_score = None, -math.inf
        open_at, room = ground.get('open'), ground.get('room')
        for c in DIRS8:
            if _toward(c, avoid) or self.blocked.get(c, -1.0) > self.t:
                continue
            m = math.hypot(*c)
            q = (p[0] + c[0] / m * 2.0, p[2] + c[1] / m * 2.0)
            if open_at is not None and not all(open_at(p[0] + c[0] / m * s, p[2] + c[1] / m * s)
                                               for s in PATH_AT):
                continue
            score = min(math.hypot(q[0] - o.pos[0], q[1] - o.pos[2]) for o in foes)
            if room is not None:
                score -= max(0, ROOM_ENOUGH - room(*q)) / ROOM_ENOUGH * ROOM_WEIGHT
            if score > best_score:
                best, best_score = c, score
        near = min(foes, key=lambda o: _flat(p, o.pos))
        turn, tilt, _err = aim_at(snap.camera, (near.pos[0], near.pos[1] + 1.0, near.pos[2]), 0.3)
        if best is None:
            return Intent(frozenset({'jump'} if ground.get('in_water') else ()), turn, (), tilt)
        keys = _keys_toward(best, snap.camera)
        if keys == {'forward'}:
            keys.add('sprint')
        if ground.get('in_water'):
            keys.add('jump')
        return Intent(frozenset(keys), turn, (), tilt)

    def _weak(self, snap) -> bool:
        """Nothing held that kills a creeper in a few hits (not known: a weapon is assumed)."""
        dmg = self.weapon_damage((snap.extra or {}).get('hotbar') or [])
        return dmg is not None and dmg < KILL_DAMAGE

    def _blast(self, snap, e) -> Intent | None:
        """A creeper near with its fuse lit (whichever one is fought): away from it, out past CALM, before
        anything else (when it's the target and there's nothing to kill it with: held BAIT_HOLD off till it blows
        up). The way kept clear of every lit one, never into lava, a drop or a wall, not near an unlit one, a
        little away from the other fighters' reach. None: no creeper near is lit. (The fighter ran 'back' from
        the camera, which had turned to a spider: not away from the creeper, 2026-10-13.)"""
        p = snap.player.pos
        lit, unlit = [], []
        for o in snap.entities:
            if o.dead or not (self.profile(o) or {}).get('explodes'):
                continue
            d = math.dist(o.pos, p)
            if _swell(o) > 0.02 and d < CALM + CALM_MARGIN:
                lit.append(o)
            elif d < 12.0:
                unlit.append(o)
        if not lit:
            self._blast_at = None
            return None
        # the way taken not getting the player on that way for BLAST_STUCK s (under BLAST_STUCK_PART of a walk's
        # worth: stopped, or sliding along a wall): blocked, at once (the watch over every move takes 0.4 s
        # windows: the fuse is 1.5; backed into a step, blown up, 2026-10-17)
        last = getattr(self, '_blast_at', None)
        if last is not None and self.t - last[0] >= BLAST_STUCK:
            t0, at, way, c = last
            gone = (p[0] - at[0]) * c[0] + (p[2] - at[2]) * c[1]
            if gone < WALK_SPEED * (self.t - t0) * BLAST_STUCK_PART:
                k = DIRS8.index(way)                     # (and either side of it: a wall, slid along slowly)
                for w in (DIRS8[k - 1], way, DIRS8[(k + 1) % 8]):
                    self.blocked[w] = self.t + BLOCKED_FOR
            self._blast_at = None
        bait = len(lit) == 1 and lit[0].id == e.id and self._weak(snap)
        lo, hi = BAIT_HOLD if bait else (CALM + CALM_MARGIN, math.inf)
        self._ground_now(p)
        ground = self._ground[0] or {}
        avoid = {c: k for c, k in (ground.get('avoid') or {}).items() if k != 'water'}
        others = [o for o in snap.entities if o.hostile and not o.dead and o not in lit and o not in unlit
                  and (o.extra or {}).get('aggressive', False) and _flat(p, o.pos) < FOE_RANGE]
        best = None
        for c in [(0.0, 0.0)] + [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 30)]:
            # (nor a way found blocked: it backed into a step for 2.5 s, not moving, the fuse burning, blown up,
            # 2026-10-17)
            if c != (0.0, 0.0) and (_toward(c, avoid) or self.blocked.get(_cell_of(c), -1.0) > self.t):
                continue
            q = (p[0] + c[0] * WALK_SPEED * BLAST_LOOK, p[1], p[2] + c[1] * WALK_SPEED * BLAST_LOOK)
            score = 0.0
            for o in lit:
                d = math.dist(q, o.pos)
                score -= max(0.0, lo - d) * 10.0 + (max(0.0, d - hi) if bait else 0.0)
            for o in unlit:
                score -= max(0.0, CREEPER_KEEP - math.dist(q, o.pos)) * 3.0
            for o in others:
                score -= max(0.0, 2.5 - _flat(q, o.pos)) * 0.5
            if c == (0.0, 0.0):
                score += 0.01
            if best is None or score > best[0]:
                best = (score, c)
        near = min(lit, key=lambda o: math.dist(o.pos, p))
        turn, tilt, _err = aim_at(snap.camera, (near.pos[0], near.pos[1] + 1.0, near.pos[2]), 0.5)
        self.state = 'baiting a creeper' if bait else 'clear of a creeper'
        keys = set() if best is None or best[1] == (0.0, 0.0) else _keys_toward(best[1], snap.camera)
        if keys:
            way = _cell_of(best[1])
            last = getattr(self, '_blast_at', None)
            if last is None or last[2] != way:
                self._blast_at = (self.t, p, way, best[1])
        if ground.get('in_water') or keys:
            keys.add('jump')                             # (up any step on the way: it's not walked up)
        return Intent(frozenset(keys), turn, (), tilt)

    def _seen_at(self, snap, e, c, q) -> int:
        """How many of those shooting at the player (but the one fought) would see them at q (the way c from here):
        worked out for every way at once, again each EXPOSED_EVERY s or when the player's on another block (a
        zombie fought in the open while a skeleton shot the player dead from 8 blocks, 2026-10-16)."""
        if self.sight is None:
            return 0
        p = snap.player.pos
        cell = (math.floor(p[0]), math.floor(p[2]))
        when, at, seen = self._exposed
        if self.t - when > EXPOSED_EVERY or at != cell:
            shooters = [o for o in snap.entities if o.hostile and not o.dead and o.id != e.id
                        and (o.extra or {}).get('aggressive') and (self.profile(o) or {}).get('ranged')
                        and _flat(p, o.pos) < SHOOTERS_NEAR]
            seen = {}
            if shooters:
                ways = [(0, 0)] + DIRS8
                pts = []
                for w in ways:
                    m = math.hypot(*w) or 1.0
                    s_ = WALK_SPEED * LOOK_AHEAD if w != (0, 0) else 0.0
                    pts.append((p[0] + w[0] / m * s_, p[1] + 1.5, p[2] + w[1] / m * s_))
                pairs = [((o.pos[0], o.pos[1] + o.height * 0.85, o.pos[2]), pt) for pt in pts for o in shooters]
                try:
                    got = self.sight(pairs)
                except Exception:
                    got = [False] * len(pairs)
                k = len(shooters)
                seen = {w: sum(1 for v in got[i * k:(i + 1) * k] if v) for i, w in enumerate(ways)}
            self._exposed = (self.t, cell, seen)
        return seen.get(c, 0)

    def room_here(self, p) -> int | None:
        """The room round p (cells to be got to in a few steps), as last read: for the fight's log."""
        room = (self._ground[0] or {}).get('room') if self._ground else None
        try:
            return room(p[0], p[2]) if room is not None else None
        except Exception:
            return None

    def _can_perch(self, snap) -> bool:
        """Whether to fight from up a pillar: blocks to build it with held, and no one near that shoots or blows up
        set on the player (up there it's no help against them; ones not after the player don't count: two
        skeletons standing in a dark pit kept the player down in it with two zombies, bare-handed, till dead,
        2026-10-16)."""
        ex = snap.extra or {}
        if int(ex.get('blocks') or 0) < self._perch_high(snap) \
                or (ex.get('block_slot') is None or ex.get('block_slot') < 0):
            return False
        p = snap.player.pos
        for o in snap.entities:
            if o.hostile and not o.dead and _flat(p, o.pos) < FOE_RANGE * 2:
                prof, st = self.profile(o) or {}, o.extra or {}
                if (prof.get('ranged') or prof.get('explodes')) and (st.get('aggressive') or st.get('using_item')):
                    return False
                if (prof.get('climbs') or prof.get('flies')) and st.get('aggressive'):
                    return False                         # (they come up after: two spiders did, dead, 2026-10-17)
        return True

    def _perch_high(self, snap) -> int:
        """How many blocks up out of reach: PERCH_HIGH over the highest ground next to the player (in a hole with
        a step up out of it, two up was still in reach of one standing on the step)."""
        p = snap.player.pos
        self._ground_now(p)
        floor = (self._ground[0] or {}).get('floor')
        if floor is None:
            return PERCH_HIGH
        top = math.floor(p[1] + 0.01)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                try:
                    h = floor(p[0] + dx, p[2] + dz)
                except Exception:
                    h = None
                if h is not None:
                    top = max(top, h)
        return PERCH_HIGH + top - math.floor(p[1] + 0.01)

    def _perch(self, snap) -> Intent:
        """Up PERCH_HIGH blocks (looking down: jump, a block put under the feet at the top of each jump), then,
        crouched (not to fall off), every one that comes within reach hit from above: a fighter on the ground
        can't reach the player two blocks up, the player reaches down to it (the way to fight a crowd)."""
        pl, cam, ex = snap.player, snap.camera, snap.extra or {}
        pr = self.perch
        p = pl.pos
        foes = [o for o in snap.entities if o.hostile and not o.dead and _flat(p, o.pos) < PERCH_DONE
                and abs(o.pos[1] - p[1]) < 5.0]
        if pr['phase'] == 'up':
            self.state = 'pillaring up'
            on_ground = ex.get('on_ground', True)
            if p[1] >= pr['base'] + pr.get('high', PERCH_HIGH) - 0.05 and on_ground:
                pr['phase'], pr['since'] = 'top', self.t
            elif self.t - pr['since'] > PERCH_UP_S:          # (a ceiling, nothing to put a block on): away instead
                self.perch = None
                if self._outrun(snap):
                    self.retreating, self.retreat_why = self.t, 'outmatched'
                return Intent()
            else:
                taps = []
                slot = ex.get('block_slot')
                if slot is not None and slot >= 0 and ex.get('slot') != slot:
                    taps.append(f'slot{slot + 1}')
                    return Intent(frozenset(), 0.0, tuple(taps), 0.0)
                turn, tilt, _err = aim_at(cam, (p[0], p[1] - 1.0, p[2]), 0.5)
                keys = {'jump'}
                vy = ex.get('vy') or 0.0
                if not on_ground and vy < PERCH_APEX and self.t - pr['placed'] > 0.3:
                    taps.append('interact')                  # (at the top of the jump: under the feet)
                    pr['placed'] = self.t
                return Intent(frozenset(keys), turn, tuple(taps), tilt)
        self.state = 'fighting from above'
        near = [o for o in foes if _flat(p, o.pos) < REACH + 0.5]
        # (over only with none near and none set on the player about: "won" with a spider after the player 9 off,
        # it came down and was bitten to death, 2026-10-17)
        after = [o for o in snap.entities if o.hostile and not o.dead and (o.extra or {}).get('aggressive')
                 and _flat(p, o.pos) < PERCH_WATCH]
        if (not foes and not after) or self.t - pr['since'] > PERCH_TIMEOUT:
            self.events.append('won' if not foes and not after else 'timeout')
            self.perch = None
            self.stop()
            return Intent()
        o = min(near or foes or after, key=lambda x: _flat(p, x.pos))   # (none near: watched, crouched)
        self.target_id = o.id
        body = (o.pos[0], o.pos[1] + o.height * 0.7, o.pos[2])
        turn, tilt, _err = aim_at(cam, body, 0.5)
        taps = []
        slot = self.weapon_slot(ex.get('hotbar') or [])
        if slot is not None and slot != ex.get('slot') and self.t - pr['placed'] > 0.3:
            taps.append(f'slot{slot + 1}')
            pr['placed'] = self.t
        elif snap.target_id is not None and any(x.id == snap.target_id for x in near) \
                and (ex.get('attack_strength', 1.0) or 0.0) >= CHARGED:
            taps.append('attack')
            self.hit_at = self.t
        return Intent(frozenset({'sneak'}), turn, tuple(taps), tilt)

    def _start_perch(self, snap) -> Intent:
        self.perch = {'phase': 'up', 'base': snap.player.pos[1], 'since': self.t, 'placed': -1e9,
                      'high': self._perch_high(snap)}
        self.events.append('perch')
        return self._perch(snap)

    def _winnable(self, snap, near: float = FOE_RANGE) -> bool:
        """Whether the fight can be won from here: the time to bring down every one coming at the player, by the
        damage a second of the best weapon held, against what they'd do meanwhile (each about MOB_DPS, falling one
        by one), under ODDS_MARGIN of the health there is. (Not known: a weapon damage: taken as winnable.)"""
        pl, ex = snap.player, snap.extra or {}
        if pl is None or pl.health is None:
            return True
        hot = ex.get('hotbar') or []
        dmg = self.weapon_damage(hot)
        if dmg is None:
            return True
        held = ex.get('slot') is not None and self.weapon_slot(hot) == ex.get('slot')
        delay = (ex.get('attack_delay') or 0) / 20.0 if held else 0.0
        dps = dmg / max(0.25, delay or (SWING_S if dmg > 1.0 else 0.25))
        p = pl.pos
        foes = [o for o in snap.entities if o.hostile and not o.dead and (o.extra or {}).get('aggressive', False)
                and _flat(p, o.pos) < near and abs(o.pos[1] - p[1]) < 3.0
                and not (self.profile(o) or {}).get('explodes')]
        if not foes:
            return True
        self._ground_now(p)
        room = (self._ground[0] or {}).get('room')
        kite = KITE if room is None or room(p[0], p[2]) >= ROOM_ENOUGH else 1.0
        kill_s = sum(o.health if o.health is not None else 20.0 for o in foes) / dps
        shooting = sum(1 for o in foes if (self.profile(o) or {}).get('ranged'))
        # (backing off out of reach is no help against arrows: those shooting count in full)
        taken = MOB_DPS * (kite * (len(foes) - shooting) + shooting) * kill_s * 0.5
        return taken < pl.health * ODDS_MARGIN

    def _outrun(self, snap) -> bool:
        """Whether the hostile ones near can be got away from (not a spider set on the player: it's as quick as a
        sprint; one by day, not after anyone, doesn't follow: one sat by the player in a pit for 15 s and was
        why it never tried to get away from the zombies, 2026-10-16)."""
        p = snap.player.pos
        return not any(o.hostile and not o.dead and _flat(p, o.pos) < RETREAT_CLEAR
                       and (self.profile(o) or {}).get('fast') and (o.extra or {}).get('aggressive', True)
                       for o in snap.entities)

    def _crit_ok(self, snap, e, prof, in_water: bool) -> bool:
        """Whether to jump for critical hits: on land, not at a shooter or a creeper, nobody else hostile within
        CRIT_CLEAR (a jump can't be steered away from them)."""
        if in_water or prof.get('ranged') or prof.get('explodes'):
            return False
        p = snap.player.pos
        return not any(o.id != e.id and o.hostile and not o.dead and (o.extra or {}).get('aggressive', False)
                       and _flat(p, o.pos) < CRIT_CLEAR for o in snap.entities)

    def _ground_now(self, p=None) -> None:
        """The ground round the player, fresh: again each TERRAIN_EVERY s, and as soon as the player is on another
        block (a quarter second at a walk is a block: it went past the shore before it looked again)."""
        cell = (math.floor(p[0]), math.floor(p[2])) if p is not None else None
        if self.t - self._ground[1] > TERRAIN_EVERY or (cell is not None and cell != getattr(self, '_ground_cell', cell)):
            self._ground_cell = cell
            try:
                self._ground = (self.terrain() or {}, self.t)
            except Exception:
                self._ground = ({}, self.t)

    def _footing(self, keys: set, cam, p, e) -> set:
        """Never step into water, lava or a deep drop when there's another way (the nearest direction to the one
        wanted that's safe, else stay); in water: swim up and make for land (a fight went to the bottom of a
        lake, 2026-10-13)."""
        self._ground_now()
        ground = self._ground[0]
        avoid = {c: k for c, k in (ground.get('avoid') or {}).items() if k != 'water'}   # (water: slow, not barred)
        moves = {'forward', 'back', 'left', 'right'}
        if ground.get('in_water'):
            keys.add('jump')
            dry = [d for d in DIRS8 if d not in avoid]
            if dry:                                      # the way to land nearest the way it wanted to go
                want = _dir_of(keys, cam) if keys & moves else (e.pos[0] - p[0], e.pos[2] - p[2])
                best = max(dry, key=lambda d: d[0] * want[0] + d[1] * want[1])
                keys -= moves
                keys |= _keys_toward(best, cam)
            return keys
        if not avoid or not keys & moves:
            return keys
        d = _dir_of(keys, cam)
        if not _toward(d, avoid):
            return keys
        extra = keys - moves
        for turn in (45, -45, 90, -90):                  # the nearest safe way round
            r = math.radians(turn)
            alt = (d[0] * math.cos(r) + d[1] * math.sin(r), -d[0] * math.sin(r) + d[1] * math.cos(r))
            if not _toward(_dir_of(_keys_toward(alt, cam), cam), avoid):
                self.state += ' (round water)' if 'water' in avoid.values() else ' (round a drop)'
                return extra | _keys_toward(alt, cam)
        self.state += ' (held: hazard)'
        return extra - {'sprint'}

    # ---- control ----

    def start(self, target_id: int, name: str | None = None, hold: bool = False) -> None:
        """hold: fight only what comes near, from where the player is (open ground, before a pit full of them),
        never going in after them; none comes in HOLD_WAIT s: 'held_off'."""
        self.active, self.state, self.target_id, self.name = True, 'closing', target_id, name
        self.hold, self.hold_since = hold, self.t
        self.started = self.t
        self.hit_at = -1e9
        self.in_reach_at = self.t      # last time it was within reach (or nearer than ever)
        self.nearest = math.inf
        self.zig = 1
        self.zig_at = self.t
        self.seen_hurt = False
        self.armed = False
        self.retreating = None       # low on health: getting away (since)
        self.stand = False           # low on health, but fought on (what's near can't be outrun)
        self.route = None            # a way to the target when there's no straight one: [cells]
        self.route_for = None        # (where the target was when it was planned, when)
        self._planning = False
        self._blocked_since = None
        self.jumped_at = -1e9
        self.idle_ready = 0.0        # seconds ready to hit with the target out of reach (lately)
        self.primary = None          # the one asked for, while one that came at us is fought first
        self.fresh = True            # (its first frame: a new fight isn't started on low health)
        self.odds_at = -1e9          # when the odds were last looked at
        self.retreat_why = None      # 'low_health' or 'outmatched': what's said when the retreat's over
        self.perch = None            # up a pillar out of their reach: {'phase': 'up' | 'top', 'base', 'since', 'placed'}
        self.blocked: dict = {}      # (dx, dz) -> till when: ways found blocked (keys held, nothing moved)
        self._moved = None           # (since, where, the way the keys went)

    def stop(self, why: str = '') -> None:
        self.active, self.state = False, 'idle'

    # ---- every frame ----

    def update(self, snap, dt: float) -> Intent:
        intent = self._update(snap, dt)
        self._watch_moves(snap, intent)
        return intent

    def _watch_moves(self, snap, intent) -> None:
        """Keys held one way and the player not moving: that way's blocked (a wall the map didn't show, a fence, a
        creature in the way), not tried again for a while (backing into a wall, it kept pressing back while the
        zombie hit it, 2026-10-16)."""
        moves = set(intent.keys) & {'forward', 'back', 'left', 'right'}
        if not self.active or not moves or snap.player is None or snap.camera is None:
            self._moved = None
            return
        way = _cell_of(_dir_of(moves, snap.camera))
        p = snap.player.pos
        moved = getattr(self, '_moved', None)
        if moved is None or moved[2] != way:
            self._moved = (self.t, p, way)
            return
        since, at, _way = moved
        if self.t - since >= BLOCKED_AFTER:
            if _flat(p, at) < BLOCKED_MOVE:
                self.blocked[way] = self.t + BLOCKED_FOR
                self.state += ' (blocked)'
            self._moved = (self.t, p, way)

    def _update(self, snap, dt: float) -> Intent:
        self.t += dt
        if not self.active:
            return Intent()
        if snap.screen is not None and not snap.screen.focused:
            return Intent()
        pl, cam, ex = snap.player, snap.camera, snap.extra or {}
        e = next((x for x in snap.entities if x.id == self.target_id), None)
        if e is None or e.dead:
            if e is not None or self.seen_hurt:
                self.kills.append((self.target_id, e.type_id if e else None))
            back = self.primary is not None and any(x.id == self.primary and not x.dead for x in snap.entities)
            if back:                                     # one that came at us is down: back to the one asked for
                self.target_id, self.primary, self.seen_hurt = self.primary, None, False
                self.in_reach_at, self.nearest = self.t, math.inf
                return Intent()
            # another one on the player: it next, not stood still (a creeper blew up, and a spider bit the player
            # to death while nothing fought it, 2026-10-13)
            p = pl.pos
            nxt = min((x for x in snap.entities if x.hostile and not x.dead and x.id != self.target_id
                       and x.id not in self.unreachable and (x.extra or {}).get('aggressive', False)
                       and _flat(p, x.pos) < SWITCH_NEAR and abs(x.pos[1] - p[1]) < 2.5),
                      key=lambda x: _flat(p, x.pos), default=None)
            if nxt is not None:
                self.events.append('next_target')
                self.target_id, self.name, self.seen_hurt = nxt.id, nxt.name, False
                self.in_reach_at, self.nearest, self.started = self.t, math.inf, self.t
                self.fresh = True                        # (only on, if well enough)
                return Intent()
            if e is not None or self.seen_hurt:
                self.events.append('won')
            else:
                self.events.append('target_gone')
            self.stop()
            return Intent()
        # a lit creeper near (whichever one is fought): away from it before anything else
        blast = self._blast(snap, e)
        if blast is not None:
            self.debug = {'target': e.id, 'across': round(_flat(pl.pos, e.pos) - e.radius, 2),
                          'swell': (e.extra or {}).get('swell')}
            return blast
        if self.perch is not None:                       # up out of their reach: fought from there
            return self._perch(snap)
        # another one closing in on the player, nearer than the target: it first (self-defence; then back)
        p0 = pl.pos
        d_t = _flat(p0, e.pos)
        threat = min((x for x in snap.entities if x.id != e.id and x.hostile and not x.dead
                      and (x.extra or {}).get('aggressive', False) and x.id not in self.unreachable
                      and abs(x.pos[1] - p0[1]) < 2.5 and _flat(p0, x.pos) < SWITCH_NEAR
                      and _flat(p0, x.pos) < d_t - SWITCH_GAP),
                     key=lambda x: _flat(p0, x.pos), default=None)
        # a creeper this near comes first, nearer than the target or not (a zombie fought with a creeper 3 to 5
        # off: it came on, lit, and blew the player up, 2026-10-17)
        creeper = min((x for x in snap.entities if x.id != e.id and not x.dead and x.id not in self.unreachable
                       and (self.profile(x) or {}).get('explodes') and abs(x.pos[1] - p0[1]) < 2.5
                       and _flat(p0, x.pos) < CREEPER_FIRST), key=lambda x: _flat(p0, x.pos), default=None)
        if creeper is not None and not (self.profile(e) or {}).get('explodes'):
            threat = creeper
        if threat is not None:
            if self.primary is None:
                self.primary = self.target_id
            self.target_id, e, self.seen_hurt = threat.id, threat, False
            self.in_reach_at, self.nearest = self.t, math.inf
        if e.health is not None and e.max_health and e.health < e.max_health:
            self.seen_hurt = True
        low = LOW_HEALTH
        if self.fresh:                                   # a new fight (or the next of several): only when well
            self.fresh, low = False, START_HEALTH
        if self.t - self.odds_at > ODDS_EVERY and not self.retreating and not self.stand:
            self.odds_at = self.t
            # one that can't be won on the ground (bare-handed against four zombies where the player's things lay:
            # dead in 4 s, 2026-10-16): up out of their reach and fought from there, with blocks to stand on;
            # else away, if they can be outrun; else fought (nothing better)
            if not self._winnable(snap):
                if self._can_perch(snap):
                    return self._start_perch(snap)
                if self._outrun(snap):
                    self.retreating, self.retreat_why = self.t, 'outmatched'
        if pl.health is not None and pl.max_health and pl.health <= pl.max_health * low \
                and not self.retreating and not self.stand:
            self.retreat_why = 'low_health'
            if self._can_perch(snap):                    # (up out of reach: no more hits from those up close)
                return self._start_perch(snap)
            if self._outrun(snap):
                self.retreating = self.t                 # (handed over standing still, it died: 2026-10-13)
            else:
                self.stand = True                        # (a spider: backing off from it, it bit till death)
        if self.retreating:
            return self._retreat(snap)
        if getattr(self, 'hold', False):
            p1 = pl.pos
            near = [o for o in snap.entities if o.hostile and not o.dead and _flat(p1, o.pos) - o.radius < HOLD_REACH
                    and abs(o.pos[1] - p1[1]) < 2.5]
            if not near:
                if self.t - self.hold_since > HOLD_WAIT:
                    self.events.append('held_off')
                    self.stop()
                    return Intent()
                self.state = 'holding ground'
                self.debug = {'target': e.id, 'across': round(_flat(p1, e.pos) - e.radius, 2)}   # (logged too)
                turn, tilt, _err = aim_at(cam, (e.pos[0], e.pos[1] + e.height * 0.6, e.pos[2]), 0.3)
                return Intent(frozenset(), turn, (), tilt)
            self.hold_since = self.t
            if e not in near:
                e = min(near, key=lambda o: _flat(p1, o.pos))
                self.target_id = e.id
        if self.t - self.started > FIGHT_TIMEOUT:
            self.events.append('timeout')
            self.stop()
            return Intent()
        prof = self.profile(e) or {}
        st = e.extra or {}
        p = pl.pos
        eye = cam.pos
        across = _flat(p, e.pos) - e.radius
        reach_d, near_pt = _to_box(eye, e)
        # aimed at its middle; one up on a ledge (or down in a hole): at the part of it nearest (the middle was
        # out of reach of a skeleton a block and a half up, while its feet were in it: "I can't reach that one",
        # shot at point-blank meanwhile, 2026-10-16)
        body = (e.pos[0], e.pos[1] + e.height * 0.6, e.pos[2])
        if abs(body[1] - eye[1]) > 1.0 and math.dist(eye, body) > REACH - 0.2:
            body = (body[0], min(max(near_pt[1], e.pos[1] + 0.15), e.pos[1] + e.height - 0.15), body[2])
        turn, tilt, err = aim_at(cam, body, 0.5)
        keys, taps = set(), []
        if not self.armed:                               # the best weapon in hand first
            self.armed = True
            slot = self.weapon_slot(ex.get('hotbar') or [])
            if slot is not None and slot != ex.get('slot'):
                taps.append(f'slot{slot + 1}')
        strength = ex.get('attack_strength', 1.0)
        on_target = snap.target_id == e.id
        self.debug = {'target': e.id, 'target_hp': e.health, 'across': round(across, 2), 'reach': round(reach_d, 2),
                      'strength': round(strength or 0, 2), 'on_target': on_target, 'aim_err': round(err, 1),
                      'swell': st.get('swell'), 'aggressive': st.get('aggressive'), 'using': st.get('using_item')}
        # a creeper with nothing to kill it with: near enough to light it (then _blast holds off till it blows)
        if prof.get('explodes') and self._weak(snap) and _swell(e) <= 0.02:
            self.state = 'lighting a creeper'
            keys = set() if math.dist(p, e.pos) < BAIT_IN else {'forward'}
            return Intent(frozenset(self._footing(keys, cam, p, e)), turn, tuple(taps), tilt)
        # hit: charged, on it, in reach; a critical hit (falling: half as much again) when the jump's under way
        self._ground_now(p)
        in_water = bool((self._ground[0] or {}).get('in_water'))
        on_ground = ex.get('on_ground', True)
        falling = not on_ground and (ex.get('vy') or 0.0) < -0.05
        crit_ok = self._crit_ok(snap, e, prof, in_water)
        if strength >= CHARGED and on_target and reach_d <= REACH and err < 12.0 \
                and (not prof.get('explodes') or _swell(e) <= 0.02):
            if falling or not crit_ok or self.t - self.jumped_at > CRIT_WAIT:
                taps.append('attack')
                self.hit_at = self.t
                self.state = 'hitting' + (' (critical)' if falling else '')
        # a jump for a critical hit: charged (or nearly), in reach, on the ground, nobody else close
        if crit_ok and on_ground and strength >= CRIT_FROM and reach_d <= REACH - 0.2 and 'attack' not in taps \
                and self.t - self.jumped_at > CRIT_EVERY:
            keys.add('jump')
            self.jumped_at = self.t
        # move: keep out of its reach while the swing charges; step in to the edge of ours when charged (from a
        # little before: it arrives as the swing is ready)
        if strength >= CHARGED and reach_d > REACH and 'attack' not in taps:
            self.idle_ready += dt                        # ready to hit, nothing in reach: time going to waste
        else:
            self.idle_ready = max(0.0, self.idle_ready - dt * 2.0)
        calm = not prof.get('explodes') or _swell(e) <= 0.02
        moves = self._spacing(snap, e, prof, (strength >= STEP_IN_FROM or 'attack' in taps) and calm, across)
        # no straight way in (a wall, a drop, a step it can't take) while out of reach: a route to it
        out_of_reach = reach_d > REACH + 0.3 and not prof.get('explodes')
        if out_of_reach and (self.state.startswith(('waiting', 'closing')) and not moves & {'forward', 'left', 'right'}
                             or abs(e.pos[1] - p[1]) > 1.5):
            self._blocked_since = self._blocked_since or self.t
        elif not out_of_reach:
            self._blocked_since, self.route = None, None
        if self._blocked_since is not None and self.t - self._blocked_since > BLOCKED_S:
            routed = self._follow_route(snap, e)
            if routed is not None:
                moves = routed
        keys |= moves
        # in water: to land first (fought from the shore: slow and sinking in it, where drowned are at home)
        if in_water:
            self.state += ' (in water)'
        # unreachable: out of reach for a while, and never nearer than before (backing off after hits doesn't
        # count: that gave up on a zombie mid-fight, 2026-10-13)
        if reach_d <= REACH or reach_d < self.nearest - 0.3:
            self.in_reach_at = self.t
            self.nearest = min(self.nearest, reach_d)
        elif self.t - self.in_reach_at > STUCK_S:
            keys.add('jump')
            if self.t - self.in_reach_at > STUCK_S * 2 and not in_water:
                self.unreachable.add(e.id)
                self.events.append('unreachable')
                self.stop()
                return Intent()
        keys = self._footing(keys, cam, p, e)
        # a shield up against arrows and a hit coming in, while the swing isn't ready
        if ex.get('offhand') in SHIELDS and 'attack' not in taps:
            shooting = prof.get('ranged') and st.get('using_item') and across > 2.5
            coming = st.get('aggressive') and reach_d < (prof.get('reach') or 2.0) + 0.8 and strength < CHARGED
            if shooting or coming:
                keys.add('item')
                keys.discard('sprint')
                self.state = 'blocking'
        return Intent(frozenset(keys), turn, tuple(taps), tilt)
