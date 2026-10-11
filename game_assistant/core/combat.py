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
# Poise ("stance", NpcParam super armor durability): a normal enemy has 15-65 and flinches from every
# light attack outside its own swings' hyper armor (fight log 2026-10-11: each hit on a Godrick Soldier,
# poise 30, played a 0.4 s flinch, 8031/8032/8036, and it attacked 0.4-0.5 s after, its hit 0.8 s into
# that: about 1.2 s with no hit coming after each flinch).
LOW_POISE = 60.0        # up to this a light attack flinches it
PUNISH_LOW = 0.45       # a low-poise target recovering this much longer: a swing lands (~0.3 s) first
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
ROLL_FREE_AFTER = 0.55  # seconds into the player's roll (27XXX) it can act again: the animation runs ~1.8 s
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
COVERED_SLACK = 0.5     # a dodged hit is "covered" only for the hit it was: the same attack again later
                        # lands at another time and gets its own roll (it was never dodged again: two hits)


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
        self._covered: dict = {}     # (entity, anim, window start) -> when that dodged hit lands
        self._roll_keys: frozenset = frozenset()
        self._last_enemy_attack = 0.0
        self._poise: float | None = None
        self._combo = 0
        self._back_from: tuple | None = None   # (time, position) a step back started
        self._strafe_until = -1e9
        self._strafe_side = 'left'
        self._off_since: float | None = None   # locked on, but the camera points away from the target
        self._far_since: float | None = None   # the target out at CHASE_FROM since

    def start(self, target_id: int, name: str | None = None) -> None:
        self.active, self.target_id, self.name, self.state = True, target_id, name, 'closing'
        self._started, self._locked, self._covered = self.t, False, {}
        self._last_enemy_attack = self.t
        self._recovering = False
        self._poise, self._combo, self._back_from = None, 0, None

    def stop(self, why: str = '') -> None:
        if self.active and why:
            self.events.append(why)
        self.active, self.state = False, 'idle'

    # ---- every frame ----

    def update(self, snap: Snapshot, dt: float) -> Intent:
        intent = self._update(snap, dt)
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
        if e is None or e.dead or (e.health is not None and e.health <= 0):
            nxt = self._threat(snap, pl.pos, self.target_id)
            if nxt is not None:      # one down, another still on the player: that one next
                self._switch(nxt, 'next_target')
                e = nxt
            else:
                self.stop('target_gone' if e is None else 'won')
                return Intent()
        if pl.health is not None and pl.max_health and pl.health < LOW_HEALTH * pl.max_health:
            self.stop('low_health')
            return Intent()
        if self.t - self._started > MAX_SECONDS:
            self.stop('timeout')
            return Intent()
        p = pl.pos
        gap = _flat(p, e.pos) - e.radius
        if gap > RETARGET_FAR:       # it walked off while another one attacks: fight the attacker
            nxt = self._threat(snap, p, e.id)
            if nxt is not None and _flat(p, nxt.pos) - nxt.radius < gap - 2.0:
                self._switch(nxt, 'retarget')
                e, gap = nxt, _flat(p, nxt.pos) - nxt.radius
        if gap > CHASE_FROM:         # running off: the walk follows it along the navmesh
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
                      'poise': self._poise}
        err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
        taps = []
        # The game locks onto whoever is in front, not always the target: follow its choice, or unlock.
        if self._locked and abs(err) > LOCK_OFF:
            if self._off_since is None:
                self._off_since = self.t
            elif self.t - self._off_since >= LOCK_OFF_FOR:
                self._off_since = None
                aimed = self._aimed(snap, p)
                if aimed is not None and aimed.id != e.id:
                    self._switch(aimed, 'retarget')
                    e = aimed
                    gap = _flat(p, e.pos) - e.radius
                    err = _wrap(_yaw((e.pos[0] - p[0], 0.0, e.pos[2] - p[2])) - _yaw(snap.camera.forward))
                else:
                    self._locked = False
                    taps.append('lock')      # unlock; it turns the camera and locks again
        else:
            self._off_since = None
        if not self._locked and gap < LOCK_RANGE and abs(err) < RELOCK_WITHIN and not taps:
            self._locked = True
            taps.append('lock')
        doing = player_doing(pl.anim)
        if doing == 'dodging' and pl.anim_time is not None and pl.anim_time >= ROLL_FREE_AFTER:
            doing = 'free'           # the roll's long tail: free to act
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
            side = 'left' if self._roll_side == 'right' else 'right'
            other = 'right' if side == 'left' else 'left'
            choices = ([{side, 'forward'}, {other, 'forward'}] if ROLL_FORWARD else []) + [{side}, {other}]
            cam = _yaw(snap.camera.forward)
            safe = [frozenset(c) for c in choices if self._safe(frozenset(c), cam, roll=True) == frozenset(c)]
            if safe:   # (every way off a ledge: take the hit rather than fall)
                self._covered[soonest[1]] = self.t + soonest[0]
                self._covered = {k: v for k, v in self._covered.items() if v > self.t - 2.0}
                self._last_roll = self.t
                self._roll_keys = safe[0]
                self._roll_side = next(iter(safe[0] & {'left', 'right'}))
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
            return Intent(self._back_keys(p), 0.0, tuple(taps))
        if doing == 'free' and self.t - self._last_swing > 1.2:
            self._combo = 0
        # 3. Bait and punish. A swing locks the player in for PLAYER_COMMIT seconds, so it only goes in
        # when that fits: the target is recovering from an attack for at least that long (an opening),
        # or nothing will hit before the player could roll again. Between openings it waits at SPACING,
        # out of most attacks' reach. A passive enemy (no attack for PASSIVE_AFTER s) is attacked anyway.
        if e.anim is not None and self.windows(e.model, e.anim) or any(self.windows(e.model, a) for a, _t, _l in e.anims):
            self._last_enemy_attack = self.t
        idle_for = PASSIVE_LOW if low_poise else PASSIVE_AFTER
        passive = self.t - self._last_enemy_attack >= idle_for and self.t - self._started >= idle_for
        opening = self._opening(e, PUNISH_LOW if low_poise else PLAYER_COMMIT)
        reel = self._reeling(e)   # None, 'flinch' (our last hit landed) or 'broken'
        clear = soonest is None or soonest[0] > PLAYER_COMMIT + DODGE_LEAD
        go_in = (opening or reel or (passive and clear)) and self.t - self._last_roll >= ROLL_RECOVER
        self.debug.update(opening=opening, passive=passive, reel=reel, poise=self._poise, combo=self._combo)
        stamina_ok = st is None or st >= STAMINA_PER_SWING
        others = self._next_hit(snap, p, exclude=e.id)
        if others is not None and others[0] < PLAYER_COMMIT:   # another enemy's hit would land mid-swing
            go_in = False
            reel = None
        self.debug['others_hit_s'] = None if others is None else round(others[0], 3)
        # Chain: our hit landed and it's reeling: press again during our swing (the game buffers it
        # into the next combo attack), up to the combo cap (no cap while it's broken).
        cap = COMBO_LOW if low_poise else COMBO_HIGH
        chain = reel is not None and doing == 'attacking' and self.t - self._last_swing >= CHAIN_MIN \
            and (reel == 'broken' or self._combo < cap) and stamina_ok
        can_swing = go_in and doing == 'free' and self.t - self._last_swing >= SWING_EVERY and stamina_ok
        if gap <= ATTACK_REACH + (0.5 if chain else 0.0) and (can_swing or chain) and abs(err) < 40.0:
            self._last_swing = self.t
            self._combo += 1
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
        spacing = SPACING_LOW if low_poise else SPACING
        if gap < spacing - 0.4:  # 4b. too close between openings: step back out of reach
            self.state = 'spacing'
            return Intent(self._back_keys(p), turn, tuple(taps))
        self._back_from = None
        if gap > spacing + 1.0:  # 4c. too far: come to spacing distance
            self.state = 'closing'
            return Intent(frozenset(keys), turn, tuple(taps))
        self.state = 'waiting'
        return Intent(frozenset(), 0.0, tuple(taps))

    def _opening(self, e, need: float = PLAYER_COMMIT) -> bool:
        """The target is in an attack whose hits are all over and still needs at least `need` seconds to
        finish: PLAYER_COMMIT for a swing and a roll after it; less for a low-poise enemy our hit flinches."""
        speed = e.anim_speed if e.anim_speed > 0 else 1.0
        for anim, t, length in (e.anims or ((e.anim, e.anim_time, e.anim_length),)):
            w = self.windows(e.model, anim)
            if w and t > max(end for _s, end in w) and (length - t) / speed >= need:
                return True
        return False

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

    def _back_keys(self, p) -> frozenset:
        """Step back; when that doesn't move the player (a rock behind), strafe sideways for a moment."""
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
        return frozenset({'back'})

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
            blocked = self.ledges()[1 if roll else 0]
        except Exception:
            blocked = ()
        if not keys or not blocked:
            return frozenset(keys)
        rel = next((a for a, k in DIRECTIONS if set(k) == set(keys)), None)
        if rel is None:
            return frozenset(keys)

        def bad(r):
            return any(abs(_wrap(cam_yaw + r - b)) < LEDGE_NEAR for b in blocked)
        if not bad(rel):
            return frozenset(keys)
        if roll:
            return frozenset()
        for a, k in sorted(DIRECTIONS, key=lambda d: abs(_wrap(d[0] - rel))):
            if abs(_wrap(a - rel)) <= 90 and not bad(a):
                return frozenset(k)
        return frozenset()

    def _threat(self, snap: Snapshot, p, exclude: int | None):
        """The nearest other hostile enemy within RETARGET_NEAR that attacks or faces the player."""
        best = None
        for x in snap.entities:
            if x.id == exclude or not x.hostile or x.dead or (x.health is not None and x.health <= 0):
                continue
            gap = _flat(p, x.pos) - x.radius
            if gap > RETARGET_NEAR:
                continue
            playing = x.anims or ((x.anim, x.anim_time, x.anim_length),)
            attacking = any(self.windows(x.model, a) for a, _t, _l in playing)
            f = self._faces(x, p)
            if not attacking and (f is None or f < 0.5):
                continue
            if best is None or gap < best[0]:
                best = (gap, x)
        return best[1] if best else None

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
        self.target_id, self.name = e.id, None
        self._poise, self._combo = None, 0
        self._last_enemy_attack = self.t
        self.events.append(why)

    def _next_hit(self, snap: Snapshot, p, exclude: int | None = None) -> tuple[float, tuple] | None:
        """(seconds until the soonest hit lands on the player, its key), from any hostile enemy close
        enough and facing the player; 0 when a hitbox is out right now and no roll covered it."""
        best = None
        for e in snap.entities:
            if not e.hostile or e.dead or e.anim is None or e.id == exclude:
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
                    land = self.t + max(0.0, (start - t) / speed)
                    if key in self._covered and abs(self._covered[key] - land) < COVERED_SLACK:
                        continue
                    if t <= end:
                        wait = max(0.0, (start - t) / speed)
                        if best is None or wait < best[0]:
                            best = (wait, key)
                        break
        return best
