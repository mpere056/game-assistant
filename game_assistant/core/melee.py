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
  flee    a creeper about to blow (swelling): run straight away till it's done or far; never stand next to one
  give up on low health (over to the player), a target that can't be got at, or a minute's fight

What each kind of character is (explodes, shoots, how far it reaches) comes from the adapter (combat_profile);
its state (swelling, aggressive, drawing a bow) from the snapshot's entity extras. The app drives this like the
other fighter (core/combat.py): start(target), update(snap, dt) -> Intent, events ('won', 'target_gone',
'low_health', 'unreachable', 'timeout').
"""
from __future__ import annotations

import math
import threading

from .autowalk import Intent, _flat, _wrap, _yaw, aim_at

REACH = 3.0             # blocks from the eye to the target's body that a swing reaches
HOLD_AT = 2.4           # stop closing in this near (centre to centre, across)
TOO_CLOSE = 1.1         # back off when nearer
CHARGED = 0.95          # attack strength for a full-damage swing
AFTER_HIT = 0.35        # seconds of backing off after a hit
ZIGZAG = 0.6            # seconds each way, closing in on a shooter
CREEPER_RUN = 7.0       # blocks: run from a swelling creeper till this far
LOW_HEALTH = 0.3        # of max health: over to the player
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
CREEPER_KEEP = 3.5      # never nearer a creeper than this, but to hit it
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
    f = (cam.forward[0], cam.forward[2])
    n = math.hypot(*f) or 1.0
    f = (f[0] / n, f[1] / n)
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


def _cell_of(d) -> tuple:
    """The neighbouring cell (dx, dz) a direction points at."""
    a = math.atan2(d[1], d[0])
    k = round(a / (math.pi / 4)) % 8
    return DIRS8[k]


def _keys_toward(d, cam) -> set:
    """The movement keys that go most nearly in direction d (x, z)."""
    want = _yaw((d[0], 0.0, d[1]))
    err = _wrap(want - _yaw(cam.forward))
    best = min(KEY_ANGLES.items(), key=lambda kv: abs(_wrap(err - kv[1])))
    return set(best[0])


class MeleeFighter:
    def __init__(self, profile=lambda e: {}, weapon_slot=lambda hotbar: None, terrain=lambda: {}, route_fn=None):
        self.profile = profile          # adapter.combat_profile(entity) -> {'explodes', 'ranged', 'reach'}
        self.weapon_slot = weapon_slot  # adapter.weapon_slot(hotbar) -> the slot to fight with
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
        ours = e.radius + HIT_AT                                  # where we can hit it from (centre to centre)
        self._ground_now(p)
        ground = self._ground[0] or {}
        avoid = ground.get('avoid') or {}
        in_water = bool(ground.get('in_water'))
        wet = {c for c, k in avoid.items() if k == 'water'}
        if in_water:                          # (all round is water: keeping out of it, it never moved, 2026-10-13)
            avoid = {c: k for c, k in avoid.items() if k != 'water'}
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
                reach = CREEPER_KEEP
            else:
                reach = o.radius + PLAYER_RADIUS + (po.get('reach') or MOB_REACH) + KEEP_MARGIN
                if not (charged and o.id == e.id):
                    reach += CHARGING_MARGIN                      # (while ours charges it keeps coming)
            foes.append((o, reach))
        just_hit = self.t - self.hit_at < AFTER_HIT * (2.0 if prof.get('explodes') else 1.0)
        scored = []
        for c in [(0, 0)] + DIRS8:
            m = math.hypot(*c)
            if m and _toward(c, avoid):
                continue
            u = (c[0] / m, c[1] / m) if m else (0.0, 0.0)
            q = (p[0] + u[0] * WALK_SPEED * LOOK_AHEAD, p[2] + u[1] * WALK_SPEED * LOOK_AHEAD)
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
            if m == 0:
                score += 0.05                                     # (standing still, all else equal)
            if in_water:
                score += 0.0 if (m == 0 or c in wet) else LAND_PULL   # out onto land
            scored.append((score, c))
        # the best way the keys really go (8 ways round the camera, not the world: up to 22 degrees off)
        best = None
        for _score, c in sorted(scored, key=lambda x: -x[0]):
            if c == (0, 0) or not _toward(_dir_of(_keys_toward(c, snap.camera), snap.camera), avoid):
                best = c
                break
        keys = {'jump'} if in_water else set()   # (afloat)
        d = _flat(p, e.pos)
        self.state = ('backing' if just_hit else 'closing' if charged else 'spacing') +             (' (others near)' if len(foes) > 1 else '')
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
            self.events.append('low_health')
            self.stop()
            return Intent()
        self.state = 'retreating'
        self._ground_now(p)
        ground = self._ground[0] or {}
        avoid = ground.get('avoid') or {}
        if ground.get('in_water'):
            avoid = {c: k for c, k in avoid.items() if k != 'water'}
        best, best_score = None, -math.inf
        for c in DIRS8:
            if _toward(c, avoid):
                continue
            m = math.hypot(*c)
            q = (p[0] + c[0] / m * 2.0, p[2] + c[1] / m * 2.0)
            score = min(math.hypot(q[0] - o.pos[0], q[1] - o.pos[2]) for o in foes)
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
        avoid = ground.get('avoid') or {}
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

    def start(self, target_id: int, name: str | None = None) -> None:
        self.active, self.state, self.target_id, self.name = True, 'closing', target_id, name
        self.started = self.t
        self.hit_at = -1e9
        self.in_reach_at = self.t      # last time it was within reach (or nearer than ever)
        self.nearest = math.inf
        self.zig = 1
        self.zig_at = self.t
        self.seen_hurt = False
        self.armed = False
        self.fleeing = False
        self.retreating = None       # low on health: getting away (since)
        self.route = None            # a way to the target when there's no straight one: [cells]
        self.route_for = None        # (where the target was when it was planned, when)
        self._planning = False
        self._blocked_since = None
        self.jumped_at = -1e9
        self.primary = None          # the one asked for, while one that came at us is fought first

    def stop(self, why: str = '') -> None:
        self.active, self.state = False, 'idle'

    # ---- every frame ----

    def update(self, snap, dt: float) -> Intent:
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
            if e is not None or self.seen_hurt:
                self.events.append('won')
            else:
                self.events.append('target_gone')
            self.stop()
            return Intent()
        # another one closing in on the player, nearer than the target: it first (self-defence; then back)
        p0 = pl.pos
        d_t = _flat(p0, e.pos)
        threat = min((x for x in snap.entities if x.id != e.id and x.hostile and not x.dead
                      and (x.extra or {}).get('aggressive', False) and x.id not in self.unreachable
                      and abs(x.pos[1] - p0[1]) < 2.5 and _flat(p0, x.pos) < SWITCH_NEAR
                      and _flat(p0, x.pos) < d_t - SWITCH_GAP),
                     key=lambda x: _flat(p0, x.pos), default=None)
        if threat is not None:
            if self.primary is None:
                self.primary = self.target_id
            self.target_id, e, self.seen_hurt = threat.id, threat, False
            self.in_reach_at, self.nearest = self.t, math.inf
        if e.health is not None and e.max_health and e.health < e.max_health:
            self.seen_hurt = True
        if pl.health is not None and pl.max_health and pl.health <= pl.max_health * LOW_HEALTH \
                and not self.retreating:
            self.retreating = self.t                     # (handed over standing still, it died: 2026-10-13)
        if self.retreating:
            return self._retreat(snap)
        if self.t - self.started > FIGHT_TIMEOUT:
            self.events.append('timeout')
            self.stop()
            return Intent()
        prof = self.profile(e) or {}
        st = e.extra or {}
        p = pl.pos
        body = (e.pos[0], e.pos[1] + e.height * 0.6, e.pos[2])
        eye = cam.pos
        across = _flat(p, e.pos) - e.radius
        reach_d = math.dist(eye, body) - e.radius
        turn, tilt, err = aim_at(cam, body, 0.5)
        keys, taps = set(), []
        if not self.armed:                               # the best weapon in hand first
            self.armed = True
            slot = self.weapon_slot(ex.get('hotbar') or [])
            if slot is not None and slot != ex.get('slot'):
                taps.append(f'slot{slot + 1}')
        strength = ex.get('attack_strength', 1.0)
        on_target = snap.target_id == e.id
        self.debug = {'target': e.id, 'across': round(across, 2), 'reach': round(reach_d, 2),
                      'strength': round(strength or 0, 2), 'on_target': on_target, 'aim_err': round(err, 1),
                      'swell': st.get('swell'), 'aggressive': st.get('aggressive'), 'using': st.get('using_item')}
        # a creeper about to blow: run
        if prof.get('explodes') and (st.get('swell') or 0) > 0.05:
            self.fleeing = True                          # (till far enough: it calms down as one gets away)
        if self.fleeing:
            if across > CREEPER_RUN:
                self.fleeing = False
            else:
                self.state = 'fleeing'
                return Intent(frozenset({'back', 'sprint'}), turn, (), tilt)
        # hit: charged, on it, in reach; a critical hit (falling: half as much again) when the jump's under way
        self._ground_now(p)
        in_water = bool((self._ground[0] or {}).get('in_water'))
        on_ground = ex.get('on_ground', True)
        falling = not on_ground and (ex.get('vy') or 0.0) < -0.05
        crit_ok = self._crit_ok(snap, e, prof, in_water)
        if strength >= CHARGED and on_target and reach_d <= REACH and err < 12.0:
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
        moves = self._spacing(snap, e, prof, strength >= STEP_IN_FROM or 'attack' in taps, across)
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
        keys = self._footing(keys, cam, p, e) if not self._steered else keys
        # a shield up against arrows and a hit coming in, while the swing isn't ready
        if ex.get('offhand') in SHIELDS and 'attack' not in taps:
            shooting = prof.get('ranged') and st.get('using_item') and across > 2.5
            coming = st.get('aggressive') and reach_d < (prof.get('reach') or 2.0) + 0.8 and strength < CHARGED
            if shooting or coming:
                keys.add('item')
                keys.discard('sprint')
                self.state = 'blocking'
        return Intent(frozenset(keys), turn, tuple(taps), tilt)
