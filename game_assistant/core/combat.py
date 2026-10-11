"""Fighting a target for the player (the F10 "fight that soldier" orders): code at the frame rate, no
model in the loop (a model's hundreds of milliseconds are too slow to dodge a swing).

Every frame it reads the snapshot: the target and every other enemy nearby, each with the animation it
plays and how far into it (the game reports these), and asks `windows(model, anim)` for that
animation's attack windows: seconds into it when its hitbox is out (from the game's own animation
files). From that it knows when a hit is about to land, and decides:

1. Dodge: an enemy that faces the player and is close enough will hit within DODGE_LEAD seconds (or
   its hitbox is out right now and no dodge covered it): roll to the side (alternating sides).
2. Back off: stamina below LOW_STAMINA: step back until it is above RECOVERED_STAMINA.
3. Attack: the target within ATTACK_REACH, no hit coming for SAFE_GAP seconds, stamina to spare, and
   the last swing SWING_EVERY seconds ago: one light attack.
4. Otherwise close in (or wait, out of reach, while a nearby enemy winds up).

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
DODGE_EVERY = 0.55      # a light/medium roll recovers in about this long: then another (combos)
ROLL_FORWARD = True     # roll through the attack (forward-left/right, toward the locked-on enemy), the
                        # usual advice; backward rolls get caught by long reaches
THREAT_REACH = 3.2      # metres from an enemy's body edge a melee hit can reach (plus its size)
FACING_COS = 0.3        # an enemy counts as aiming at the player within ~72 degrees of facing them
ATTACK_REACH = 1.8      # metres from the target's body edge to swing
SAFE_GAP = 1.1          # no hit landing within this many seconds: safe to swing. A swing takes about a
                        # second to recover from and a roll can't start before: at 0.66 s a hit landed
                        # through the roll (fight log 2026-10-11)
ROLL_RECOVER = 0.75     # no swing this soon after a roll: the attack press right after the roll press
                        # cancelled the roll (in Elden Ring the later input wins)
SWING_EVERY = 0.9       # seconds between light attacks
STAMINA_PER_SWING = 20.0
LOW_STAMINA = 15.0
RECOVERED_STAMINA = 50.0
LOW_HEALTH = 0.35       # share of max health: hand back to the player
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
PUNISH_AFTER = 0.5      # the target's attack is over (its last window passed) and it still needs this many
                        # seconds to finish: an opening to swing


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
    active: bool = False
    target_id: int | None = None
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
        self._covered: set = set()   # (entity, anim, window start) already dodged
        self._roll_keys: frozenset = frozenset()
        self._last_enemy_attack = 0.0

    def start(self, target_id: int, name: str | None = None) -> None:
        self.active, self.target_id, self.name, self.state = True, target_id, name, 'closing'
        self._started, self._locked, self._covered = self.t, False, set()
        self._last_enemy_attack = self.t
        self._recovering = False

    def stop(self, why: str = '') -> None:
        if self.active and why:
            self.events.append(why)
        self.active, self.state = False, 'idle'

    # ---- every frame ----

    def update(self, snap: Snapshot, dt: float) -> Intent:
        self.t += dt
        if not self.active:
            return Intent()
        if not snap.in_world or snap.player is None or snap.camera is None or snap.menu:
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
        if e is None:
            self.stop('target_gone')
            return Intent()
        if e.dead or (e.health is not None and e.health <= 0):
            self.stop('won')
            return Intent()
        if pl.health is not None and pl.max_health and pl.health < LOW_HEALTH * pl.max_health:
            self.stop('low_health')
            return Intent()
        if self.t - self._started > MAX_SECONDS:
            self.stop('timeout')
            return Intent()
        p = pl.pos
        gap = _flat(p, e.pos) - e.radius
        self.debug = {'target_anims': [(a, round(t, 2)) for a, t, _ in (e.anims or ())],
                      'target_windows': {a: self.windows(e.model, a) for a, _t, _ in (e.anims or ())
                                         if self.windows(e.model, a)},
                      'gap': round(gap, 2), 'stamina': pl.stamina,
                      'hp': pl.health, 'facing_me': self._faces(e, p),
                      'my_anim': (pl.anim, round(pl.anim_time, 2)) if pl.anim is not None else None}
        err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
        taps = []
        if not self._locked and gap < LOCK_RANGE:
            self._locked = True
            taps.append('lock')
        doing = player_doing(pl.anim)
        self.debug['me'] = doing
        # 0. Rolling: keep its direction held until the roll has started.
        if self.t - self._last_roll < ROLL_DIR_HOLD:
            return Intent(self._roll_keys, 0.0, tuple(taps))
        # 1. A hit about to land: roll to the side (a press while attacking or staggered is ignored by
        # the game: press anyway, it's the best there is, but the swing rules below avoid getting there).
        soonest = self._next_hit(snap, p)
        self.debug['next_hit_s'] = None if soonest is None else round(soonest[0], 3)
        # (a roll can follow a roll after DODGE_EVERY: a soldier's two-swing chain hit the player while a
        # "not while dodging" rule blocked the second roll)
        if soonest is not None and soonest[0] <= DODGE_LEAD and self.t - self._last_roll >= DODGE_EVERY:
            self._covered.add(soonest[1])
            self._last_roll = self.t
            self._roll_side = 'left' if self._roll_side == 'right' else 'right'
            self._roll_keys = frozenset({self._roll_side, 'forward'} if ROLL_FORWARD else {self._roll_side})
            self.state = 'dodging'
            self.events.append('dodge')
            return Intent(self._roll_keys, 0.0, tuple(taps) + ('roll',))
        # 2. Out of stamina: back off until it has come back.
        st, st_max = pl.stamina, pl.max_stamina
        if st is not None:
            if st < LOW_STAMINA:
                self._recovering = True
            elif self._recovering and st >= min(RECOVERED_STAMINA, st_max or RECOVERED_STAMINA):
                self._recovering = False
        if self._recovering:
            self.state = 'recovering'
            return Intent(frozenset({'back'}), 0.0, tuple(taps))
        # 3. Bait and punish. A swing locks the player in for PLAYER_COMMIT seconds, so it only goes in
        # when that fits: the target is recovering from an attack for at least that long (an opening),
        # or nothing will hit before the player could roll again. Between openings it waits at SPACING,
        # out of most attacks' reach. A passive enemy (no attack for PASSIVE_AFTER s) is attacked anyway.
        if e.anim is not None and self.windows(e.model, e.anim) or any(self.windows(e.model, a) for a, _t, _l in e.anims):
            self._last_enemy_attack = self.t
        passive = self.t - self._last_enemy_attack >= PASSIVE_AFTER and self.t - self._started >= PASSIVE_AFTER
        opening = self._opening(e)
        clear = soonest is None or soonest[0] > PLAYER_COMMIT + DODGE_LEAD
        go_in = (opening or (passive and clear)) and self.t - self._last_roll >= ROLL_RECOVER
        self.debug.update(opening=opening, passive=passive)
        can_swing = go_in and doing == 'free' and self.t - self._last_swing >= SWING_EVERY \
            and (st is None or st >= STAMINA_PER_SWING)
        if gap <= ATTACK_REACH and can_swing and abs(err) < 40.0:
            self._last_swing = self.t
            self.state = 'attacking'
            self.events.append('swing')
            return Intent(frozenset(), 0.0, tuple(taps) + ('attack',))
        keys = set(min(DIRECTIONS, key=lambda d: abs(_wrap(err - d[0])))[1])
        turn = 0.0 if self._locked else max(-240 * dt, min(240 * dt, err * 6 * dt))
        if can_swing:  # 4a. an opening: in to reach, fast
            self.state = 'closing'
            return Intent(frozenset(keys), turn, tuple(taps))
        if doing != 'free':  # in our own swing or staggered: nothing to do but wait
            self.state = 'waiting'
            return Intent(frozenset(), 0.0, tuple(taps))
        if gap < SPACING - 0.4:  # 4b. too close between openings: step back out of reach
            self.state = 'spacing'
            return Intent(frozenset({'back'}), turn, tuple(taps))
        if gap > SPACING + 1.0:  # 4c. too far: come to spacing distance
            self.state = 'closing'
            return Intent(frozenset(keys), turn, tuple(taps))
        self.state = 'waiting'
        return Intent(frozenset(), 0.0, tuple(taps))

    def _opening(self, e) -> bool:
        """The target is in an attack whose hits are all over and still needs at least PLAYER_COMMIT
        seconds to finish: time for one swing and a roll after it, if it attacks again at once."""
        speed = e.anim_speed if e.anim_speed > 0 else 1.0
        for anim, t, length in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            w = self.windows(e.model, anim)
            if w and t > max(end for _s, end in w) and (length - t) / speed >= PLAYER_COMMIT:
                return True
        return False

    @staticmethod
    def _faces(e, p) -> float | None:
        if e.facing is None:
            return None
        d = (p[0] - e.pos[0], p[2] - e.pos[2])
        n = math.hypot(*d) or 1.0
        return round((e.facing[0] * d[0] + e.facing[2] * d[1]) / n, 2)

    def _next_hit(self, snap: Snapshot, p) -> tuple[float, tuple] | None:
        """(seconds until the soonest hit lands on the player, its key), from any hostile enemy close
        enough and facing the player; 0 when a hitbox is out right now and no roll covered it."""
        best = None
        for e in snap.entities:
            if not e.hostile or e.dead or e.anim is None:
                continue
            gap = _flat(p, e.pos) - e.radius
            if gap > THREAT_REACH + e.radius:  # big enemies reach further
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
                    if key in self._covered:
                        continue
                    if t <= end:
                        wait = max(0.0, (start - t) / speed)
                        if best is None or wait < best[0]:
                            best = (wait, key)
                        break
        return best
