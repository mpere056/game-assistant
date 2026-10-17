"""What's dangerous round the player, as one picture, and what to do about it all together (any game with
hostile characters; first Minecraft).

Each part used to see a slice: the walk's defence the ones within 6 blocks after the player, a check for the ones
ahead where it's going, another for ones that shoot, the fighter only the one it fought. A zombie up close and a
skeleton shooting from 8 blocks off fell between them: the zombie was fought with bare hands while the skeleton shot
the player dead (2026-10-16). Here every hostile one near is a Threat (how it hurts: up close, shooting, blowing
up; whether it's set on the player; for one that shoots, whether it can see the player), and decide() weighs them
all at once:

  cover   one that shoots can see the player and it isn't a fight to take on in the open (others up close, the
          player hurt or unarmed, more than one shooting, or the odds): out of their sight (the ones up close follow,
          and are fought there, out of the arrows)
  fight   the one to fight first: the nearest up close after the player; else one that shoots, near, alone, the
          player well and armed
  away    ones that must come up close are after the player, still some way off, and it isn't a fight to win
          (unarmed, hurt, no room) with nothing to build up out of their reach with: away from them now, while
          they're slower than a walk (a zombie unseen till 6 blocks off, the player at half health and bare-handed,
          dead in a corner, 2026-10-16)
  hold    ones that must come up close are where the walk is going: met on open ground, never gone in after
  none    nothing to do

The adapter says what kind each is (combat_profile: 'ranged', 'explodes', 'neutral') and whether one can see the
player (a line of sight, by the game's rays).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

NEAR = 24.0             # blocks: hostile ones this near are weighed
UP_CLOSE = 6.0          # blocks: one after the player this near, that must come up close, is fought
SHOOTER_NEAR = 16.0     # blocks: one that shoots, set on the player, this near counts
NOTICE = 16.0           # blocks: ones that must come up close, after the player, this near are weighed (not only
                        # when they're up close)
SHOOTER_FIGHT = 10.0    # ...and is fought (alone, the player well and armed, the odds good) when this near
GOAL_NEAR = 8.0         # blocks: ones this near where the walk's going are "there"
WELL = 0.5              # of max health: under this, the player isn't to stand in the open being shot at


@dataclass
class Threat:
    e: object               # the character (core.interface.Entity)
    kind: str               # 'melee', 'ranged' or 'explosive'
    dist: float
    after: bool             # set on the player (aggressive; a bow drawn)
    sees: bool | None = None   # one that shoots: the player in its sight (None: not known)


@dataclass
class Decision:
    what: str               # 'none', 'fight', 'cover', 'hold'
    target: object = None   # the one to fight, hold for, or (cover) the nearest of those shooting
    shooters: tuple = ()    # cover: every one that shoots and sees the player
    why: str = ''


def threats(snap, profile, near: float = NEAR) -> list[Threat]:
    """Every hostile character within near, nearest first (neutral ones not provoked left out)."""
    if not snap.player:
        return []
    p = snap.player.pos
    out = []
    for e in snap.entities:
        if not e.hostile or e.dead:
            continue
        d = math.dist(e.pos, p)
        if d > near:
            continue
        prof = profile(e) or {}
        st = e.extra or {}
        if prof.get('neutral'):
            continue
        kind = 'explosive' if prof.get('explodes') else 'ranged' if prof.get('ranged') else 'melee'
        after = bool(st.get('aggressive')) or (kind == 'ranged' and bool(st.get('using_item')))
        out.append(Threat(e, kind, d, after))
    return sorted(out, key=lambda t: t.dist)


def decide(snap, ts: list[Threat], goal=None, armed: bool = True, winnable=lambda near=None: True,
           perch: bool = False) -> Decision:
    """What to do about all of them (see the module's doc). winnable(near): whether the fight with those within
    near can be won; perch: blocks are held to fight from up out of the reach of those that must come up close."""
    if not snap.player or not ts:
        return Decision('none')
    pl = snap.player
    p = pl.pos
    hurt = pl.health is not None and pl.max_health and pl.health < pl.max_health * WELL
    shooting = [t for t in ts if t.kind == 'ranged' and t.after and t.dist < SHOOTER_NEAR and t.sees is not False]
    close = [t for t in ts if t.kind in ('melee', 'explosive') and t.after and t.dist < UP_CLOSE
             and abs(t.e.pos[1] - p[1]) < 3.0]
    if shooting and (close or hurt or not armed or len(shooting) > 1 or not winnable()):
        why = 'others up close' if close else 'hurt' if hurt else 'unarmed' if not armed else \
            'more than one shooting' if len(shooting) > 1 else 'a fight not to take on in the open'
        return Decision('cover', shooting[0].e, tuple(t.e for t in shooting), why)
    if close:
        return Decision('fight', close[0].e, why='after the player, up close')
    coming = [t for t in ts if t.kind == 'melee' and t.after and t.dist < NOTICE and abs(t.e.pos[1] - p[1]) < 4.0]
    if coming and not perch and not winnable(NOTICE):
        return Decision('away', coming[0].e, why='after the player, not a fight to win')
    if shooting:
        t = shooting[0]
        if t.dist < SHOOTER_FIGHT:
            return Decision('fight', t.e, why='shooting, near, alone')
        return Decision('cover', t.e, tuple(x.e for x in shooting), 'shooting from afar')
    if goal is not None:
        to_goal = math.dist(p, goal)
        ahead = [t for t in ts if t.kind == 'melee' and (math.dist(t.e.pos, goal) < GOAL_NEAR
                                                          or t.after and math.dist(t.e.pos, goal) < to_goal - 2.0)]
        if ahead and to_goal > 2.0:
            return Decision('hold', ahead[0].e, why='where the walk is going')
    return Decision('none')
