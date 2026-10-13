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
    def __init__(self, profile=lambda e: {}, weapon_slot=lambda hotbar: None, terrain=lambda: {}):
        self.profile = profile          # adapter.combat_profile(entity) -> {'explodes', 'ranged', 'reach'}
        self.weapon_slot = weapon_slot  # adapter.weapon_slot(hotbar) -> the slot to fight with
        self.terrain = terrain          # adapter.terrain() -> {'in_water', 'avoid': {(dx, dz): what}}
        self._ground = ({}, -1e9)
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

    def _footing(self, keys: set, cam, p, e) -> set:
        """Never step into water, lava or a deep drop when there's another way (the nearest direction to the one
        wanted that's safe, else stay); in water: swim up and make for land (a fight went to the bottom of a
        lake, 2026-10-13)."""
        if self.t - self._ground[1] > TERRAIN_EVERY:
            try:
                self._ground = (self.terrain() or {}, self.t)
            except Exception:
                self._ground = ({}, self.t)
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
                self.events.append('won')
            else:
                self.events.append('target_gone')
            self.stop()
            return Intent()
        if e.health is not None and e.max_health and e.health < e.max_health:
            self.seen_hurt = True
        if pl.health is not None and pl.max_health and pl.health <= pl.max_health * LOW_HEALTH:
            self.events.append('low_health')
            self.stop()
            return Intent()
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
        # hit
        if strength >= CHARGED and on_target and reach_d <= REACH and err < 12.0:
            taps.append('attack')
            self.hit_at = self.t
            self.state = 'hitting'
        # move: back off right after a hit (always from a creeper), else close in to the edge of reach
        if self.t - self.hit_at < AFTER_HIT * (2.0 if prof.get('explodes') else 1.0):
            keys.add('back')
            self.state = 'backing'
        elif across > HOLD_AT or reach_d > REACH:
            keys.add('forward')
            if across > 6.0 and not prof.get('explodes'):
                keys.add('sprint')
            if prof.get('ranged') and across > 3.0:      # zig-zag in on a shooter
                if self.t - self.zig_at > ZIGZAG:
                    self.zig, self.zig_at = -self.zig, self.t
                keys.add('left' if self.zig > 0 else 'right')
            if e.pos[1] - p[1] > 0.6 and across < 2.5:    # it's up a step
                keys.add('jump')
            self.state = 'closing'
        elif across < TOO_CLOSE:
            keys.add('back')
            self.state = 'spacing'
        else:
            self.state = 'waiting'                       # in reach: the swing charging
        # unreachable: out of reach for a while, and never nearer than before (backing off after hits doesn't
        # count: that gave up on a zombie mid-fight, 2026-10-13)
        if reach_d <= REACH or reach_d < self.nearest - 0.3:
            self.in_reach_at = self.t
            self.nearest = min(self.nearest, reach_d)
        elif self.t - self.in_reach_at > STUCK_S:
            keys.add('jump')
            if self.t - self.in_reach_at > STUCK_S * 2:
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
