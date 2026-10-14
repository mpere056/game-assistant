"""Fighting with a charged swing (core/melee.py) in a small simulated arena."""
import math
import unittest

from game_assistant.core.interface import Camera, Entity, Player, Screen, Snapshot
from game_assistant.core.melee import MeleeFighter

DT = 1 / 30


class Arena:
    """The player at the origin facing +z; one mob. Swings charge over `delay` s; a hit needs the crosshair on
    it (aim within 6 degrees) and reach; a mob walks at the player (stops 1 block off)."""

    def __init__(self, kind='zombie', at=(0.0, 0.0, 6.0), hp=20.0, delay=0.625, offhand='', swell_at=None):
        self.p, self.yaw, self.pitch = [0.0, 0.0, 0.0], 0.0, 0.0
        self.kind, self.e, self.hp, self.delay = kind, list(at), hp, delay
        self.since_swing, self.hits, self.swings_early = 10.0, 0, 0
        self.offhand, self.swell_at, self.t, self.health = offhand, swell_at, 0.0, 20.0
        self.blocked, self.walked_away = 0, 0.0
        self.taken, self.mob_cd = 0, 0.0
        self.py, self.vy, self.crits = 0.0, 0.0, 0     # the player's height off the ground; jumps
        self.others = []             # more mobs: [x, z] each, walking at the player and hitting like the first

    def fwd(self):
        r, pr = math.radians(self.yaw), math.radians(self.pitch)
        return (math.sin(r) * math.cos(pr), -math.sin(pr), math.cos(r) * math.cos(pr))

    def snap(self):
        eye = (self.p[0], 1.6, self.p[2])
        f = self.fwd()
        body = (self.e[0], self.e[1] + 1.1, self.e[2])
        d = [body[i] - eye[i] for i in range(3)]
        n = math.sqrt(sum(v * v for v in d))
        swell = 0.5 if self.swell_at is not None and self.t > self.swell_at and n < 4 else 0.0
        e = Entity(7, 1, f'minecraft:{self.kind}', tuple(self.e), body, 0.3, 1.95, True, self.hp <= 0, self.hp, 20.0,
                   name=self.kind, extra={'aggressive': True, 'swell': swell,
                                          'using_item': self.kind == 'skeleton'})
        cam = Camera(eye, f, (math.cos(math.radians(self.yaw)), 0, -math.sin(math.radians(self.yaw))), (0, 1, 0),
                     70, 1.7)
        for o in self.others:
            if len(o) < 3:
                o.append(20.0)
        others = tuple(Entity(20 + i, 1, 'minecraft:zombie', (o[0], 0.0, o[1]), (o[0], 1.1, o[1]), 0.3, 1.95, True,
                              o[2] <= 0, o[2], 20.0, name='zombie', extra={'aggressive': True})
                       for i, o in enumerate(self.others))
        aimed = None                                     # the crosshair: the nearest one within 6 degrees
        for x in (e,) + others:
            if x.dead:
                continue
            d2 = [x.center[i] - eye[i] for i in range(3)]
            n2 = math.sqrt(sum(v * v for v in d2))
            if n2 < 6 and sum(d2[i] / n2 * f[i] for i in range(3)) > math.cos(math.radians(6)) \
                    and (aimed is None or n2 < aimed[0]):
                aimed = (n2, x.id)
        on = aimed is not None and aimed[1] == 7
        self._aimed = aimed[1] if aimed else None
        return Snapshot('t', 1, True, Player(tuple(self.p), None, 'alive', self.health, 20.0), cam,
                        (e,) + others, None, target_id=self._aimed,
                        screen=Screen(0, 0, 800, 600, True),
                        extra={'attack_strength': min(1.0, self.since_swing / self.delay), 'offhand': self.offhand,
                               'on_ground': self.py <= 0.0, 'vy': self.vy,
                               'hotbar': [['minecraft:dirt', 5], ['minecraft:stone_sword', 1]], 'slot': 0})

    def step(self, intent):
        self.t += DT
        self.yaw += intent.turn
        self.pitch = max(-90, min(90, self.pitch + intent.pitch))
        self.since_swing += DT
        if 'attack' in intent.taps:
            s = self.snap()
            if s.extra['attack_strength'] < 0.95:
                self.swings_early += 1
            dmg = 6.0 * min(1.0, self.since_swing / self.delay)
            if self.py > 0.0 and self.vy < 0.0:          # falling: a critical hit
                dmg *= 1.5
                self.crits += 1
            if s.target_id == 7:
                self.hp -= dmg
                self.hits += 1
                kx, kz = self.e[0] - self.p[0], self.e[2] - self.p[2]   # knocked back 0.5
                kn = math.hypot(kx, kz) or 1.0
                self.e[0] += kx / kn * 0.5
                self.e[2] += kz / kn * 0.5
            elif s.target_id is not None and s.target_id >= 20:
                o = self.others[s.target_id - 20]
                o[2] -= dmg
                self.hits += 1
                kx, kz = o[0] - self.p[0], o[1] - self.p[2]
                kn = math.hypot(kx, kz) or 1.0
                o[0] += kx / kn * 0.5
                o[1] += kz / kn * 0.5
            self.since_swing = 0.0
        # mobs hit when the player is within 1.43 of their centre (once a second); creepers don't
        self.mob_cd -= DT
        if self.kind not in ('creeper', 'skeleton') and self.mob_cd <= 0:
            for mx, mz, alive in [(self.e[0], self.e[2], self.hp > 0)] + [(o[0], o[1], o[2] > 0) for o in self.others]:
                if alive and math.hypot(self.p[0] - mx, self.p[2] - mz) < 1.43:
                    self.taken += 1
                    self.mob_cd = 1.0
                    break
        if 'item' in intent.keys:
            self.blocked += 1
        if 'jump' in intent.keys and self.py <= 0.0:
            self.vy = 8.4                                # (a Minecraft jump: about 1.25 blocks, 0.6 s)
        if self.py > 0.0 or self.vy > 0.0:
            self.vy -= 28.0 * DT
            self.py = max(0.0, self.py + self.vy * DT)
            if self.py == 0.0:
                self.vy = 0.0
        f = self.fwd()
        fx = ('forward' in intent.keys) - ('back' in intent.keys)
        sx = ('right' in intent.keys) - ('left' in intent.keys)
        v = (5.6 if 'sprint' in intent.keys else 4.3) * DT
        r = math.radians(self.yaw)
        self.p[0] += (math.sin(r) * fx + math.cos(r) * sx) * v
        self.p[2] += (math.cos(r) * fx - math.sin(r) * sx) * v
        dx, dz = self.p[0] - self.e[0], self.p[2] - self.e[2]
        d = math.hypot(dx, dz)
        if d > 1.0 and self.hp > 0:                       # it walks at the player
            self.e[0] += dx / d * 2.3 * DT
            self.e[2] += dz / d * 2.3 * DT
        for o in self.others:
            ox, oz = self.p[0] - o[0], self.p[2] - o[1]
            od = math.hypot(ox, oz)
            if od > 1.0 and (len(o) < 3 or o[2] > 0):
                o[0] += ox / od * 2.3 * DT
                o[1] += oz / od * 2.3 * DT


def fight(arena, seconds=20):
    f = MeleeFighter(lambda e: {'explodes': 'creeper' in e.model, 'ranged': 'skeleton' in e.model, 'reach': 0.83},
                     lambda hotbar: 1)
    f.start(7, arena.kind)
    taps = []
    for _ in range(int(seconds / DT)):
        intent = f.update(arena.snap(), DT)
        taps += list(intent.taps)
        arena.step(intent)
        if not f.active:
            break
    return f, taps


class MeleeTests(unittest.TestCase):
    def test_kills_a_zombie_with_charged_swings_only(self):
        a = Arena()
        f, taps = fight(a)
        self.assertIn('won', f.events)
        self.assertEqual(a.swings_early, 0)            # never a weak swing
        self.assertIn('slot2', taps)                    # the sword first
        self.assertEqual(f.kills[0][0], 7)

    def test_runs_from_a_swelling_creeper(self):
        # lit beside the player (by the game's rules: Field): out past 7, where it goes back down, before it blows
        fd = Field([('creeper', 0.0, 2.5, 10.0)])
        f, states = field_fight(fd, 10, seconds=6)
        self.assertIn('clear of a creeper', states)
        self.assertEqual(fd.blasted, 0.0)

    def test_zigzags_in_on_a_skeleton_and_holds_up_a_shield(self):
        a = Arena('skeleton', at=(0.0, 0.0, 12.0), offhand='minecraft:shield')
        sides = []
        f = MeleeFighter(lambda e: {'ranged': True, 'reach': 2.0})
        f.start(7)
        for _ in range(60):
            intent = f.update(a.snap(), DT)
            sides.append(('left' in intent.keys) - ('right' in intent.keys))
            a.step(intent)
        self.assertIn(1, sides)
        self.assertIn(-1, sides)
        self.assertGreater(a.blocked, 0)

    def test_on_low_health_gets_away_then_hands_over(self):
        a = Arena(at=(0.0, 0.0, 4.0))
        a.health = 4.0
        f, _ = fight(a, seconds=10)
        self.assertIn('low_health', f.events)              # handed over...
        self.assertGreater(math.hypot(a.p[0] - a.e[0], a.p[2] - a.e[2]), 5.0)   # ...well away from it
        self.assertEqual(a.taken, 0)

    def test_a_long_hit_and_back_off_fight_is_never_given_up_as_unreachable(self):
        a = Arena(hp=1000)                               # (a tough one: 20 s of hitting and backing off)
        f, _ = fight(a, seconds=20)
        self.assertNotIn('unreachable', f.events)
        self.assertGreater(a.hits, 15)

    def test_never_backs_into_the_water_behind(self):
        a = Arena(hp=1000, at=(0.0, 0.0, 3.0))
        water = {(0, -1): 'water', (1, -1): 'water', (-1, -1): 'water'}
        f = MeleeFighter(lambda e: {'reach': 0.83}, lambda h: None,
                         lambda: {'avoid': water if a.p[2] < 1.0 else {}, 'in_water': False})
        f.start(7)
        lowest = 0.0
        for _ in range(int(8 / DT)):
            a.step(f.update(a.snap(), DT))
            lowest = min(lowest, a.p[2])
        self.assertGreater(lowest, -0.3)                # the shore is at z = 0: it went along it, not in
        self.assertGreater(a.hits, 5)

    def test_kills_a_zombie_without_being_hit(self):
        a = Arena()
        f, _ = fight(a)
        self.assertIn('won', f.events)
        self.assertEqual(a.taken, 0)

    def test_kills_one_of_two_zombies_without_being_hit(self):
        a = Arena(at=(0.0, 0.0, 6.0))
        a.others = [[3.0, 7.0]]
        f, _ = fight(a)
        self.assertIn('won', f.events)
        self.assertEqual(a.taken, 0)


class AroundTests(unittest.TestCase):
    def test_three_around_and_two_behind_without_being_hit(self):
        for others in ([[-5.0, 2.0], [5.0, -1.0]], [[-2.0, -3.0], [2.0, -3.0]]):
            a = Arena(at=(0.0, 0.0, 6.0))
            a.others = [list(o) for o in others]
            hit = []

            class F(MeleeFighter):
                pass

            f = MeleeFighter(lambda e: {'reach': 0.83}, lambda h: None)
            f.start(7)
            for _ in range(int(25 / DT)):
                intent = f.update(a.snap(), DT)
                a.step(intent)
                # the arena's others die when hit: drop any the fighter has killed
                if f.target_id != 7 and 'attack' in intent.taps:
                    hit.append(f.target_id)
                if not f.active:
                    break
            self.assertNotIn('unreachable', f.events, others)
            self.assertLessEqual(a.taken, 1, others)

    def test_in_water_it_makes_for_land_and_never_gives_up_there(self):
        # water everywhere west of x = 3, land east of it; the target out of reach in the water
        a = Arena(hp=1000, at=(-4.0, 0.0, 0.0))
        a.e[1] = -3.0                                    # (on the lake floor: never in reach)

        def terrain():
            wet = a.p[0] < 3.0
            water = {c: 'water' for c in [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
                     if a.p[0] + c[0] < 3.0}
            return {'in_water': wet, 'avoid': water}
        f = MeleeFighter(lambda e: {'reach': 0.83}, lambda h: None, terrain)
        f.start(7)
        gave_up_wet = False
        for _ in range(int(6 / DT)):
            a.step(f.update(a.snap(), DT))
            if 'unreachable' in f.events and a.p[0] < 3.0:
                gave_up_wet = True
            if not f.active:
                break
        self.assertGreater(a.p[0], 3.0)                 # out on the land
        self.assertFalse(gave_up_wet)


class RouteInTests(unittest.TestCase):
    def test_goes_round_a_wall_by_a_route_when_there_is_no_straight_way(self):
        from types import SimpleNamespace as NS
        import time as _time
        a = Arena(hp=1000, at=(0.0, 0.0, 6.0))
        a.step_mob = False
        wall = {(0, 1): 'wall', (1, 1): 'wall', (-1, 1): 'wall'}      # (north of the player, while z < 3)

        def terrain():
            return {'in_water': False, 'avoid': wall if a.p[2] < 3.0 and -1.0 < a.p[0] < 2.0 else {}}

        def route(start, goal):
            return NS(waypoints=[tuple(start), (3.0, 0.0, 0.0), (3.0, 0.0, 4.0), (1.0, 0.0, 5.0)], actions=[])
        f = MeleeFighter(lambda e: {'reach': 0.83}, lambda h: None, terrain, route)
        f.start(7)
        went_round, states = False, set()
        for _ in range(int(12 / DT)):
            intent = f.update(a.snap(), DT)
            states.add(f.state.split(' ')[0])
            fx, fz = a.e[0], a.e[2]
            a.step(intent)
            a.e[0], a.e[2] = fx, fz                      # (it stands still behind its wall)
            went_round = went_round or a.p[0] > 2.0
            _time.sleep(0)                               # (the route is planned on a thread)
            if not f.active:
                break
        self.assertIn('routing', states)
        self.assertTrue(went_round)
        self.assertGreater(a.hits, 0)

    def test_goes_into_the_water_after_one_that_stays_there(self):
        a = Arena(hp=1000, at=(0.0, 0.0, 6.0))
        water_dirs = {(0, 1): 'water', (1, 1): 'water', (-1, 1): 'water'}

        def terrain():                                   # the shore at z = 3: water beyond
            if a.p[2] > 3.0:
                return {'in_water': True, 'avoid': {c: 'water' for c in
                                                    [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (1, -1)]}}
            return {'in_water': False, 'avoid': water_dirs if a.p[2] > 2.0 else {}}
        f = MeleeFighter(lambda e: {'reach': 0.83}, lambda h: None, terrain)
        f.start(7)
        for _ in range(int(8 / DT)):
            intent = f.update(a.snap(), DT)
            fx, fz = a.e[0], a.e[2]
            a.step(intent)
            a.e[0], a.e[2] = fx, fz                      # (it stays out in the water)
            if not f.active:
                break
        self.assertGreater(a.hits, 3)                    # in after it, not waiting on the shore
        self.assertNotIn('unreachable', f.events)


class Field:
    """Creepers and spiders by the game's rules, the player as in Arena (facing +z at the origin). A creeper
    walks at the player (2 blocks a second) and lights its fuse with the player within 3 (feet to feet), then
    stands still while it swells (20/28 a second); farther than 7 it goes back down (as fast) and walks on. At
    30/28 it blows up: within 6 the player takes ((1 - d/6)^2 + (1 - d/6)) / 2 * 42 + 1. A spider runs at the
    player (5 a second) and bites for 2 within 1.8 of its middle, once a second. Swings: a stone sword (5, 0.625
    s to charge) or a fist (1, 0.25 s)."""

    def __init__(self, mobs, sword=True):
        self.p, self.yaw, self.pitch, self.t, self.health = [0.0, 0.0, 0.0], 0.0, 0.0, 0.0, 20.0
        self.sword, self.since_swing = sword, 10.0
        self.mobs = [{'id': 10 + i, 'kind': k, 'pos': [x, 0.0, z], 'hp': 20.0 if k == 'creeper' else 16.0,
                      'swell': sw, 'lit': sw > 0, 'cd': 0.0, 'gone': False}
                     for i, (k, x, z, sw) in enumerate(mobs)]
        self.blasted = 0.0
        self.bitten = 0.0

    def fwd(self):
        r, pr = math.radians(self.yaw), math.radians(self.pitch)
        return (math.sin(r) * math.cos(pr), -math.sin(pr), math.cos(r) * math.cos(pr))

    def snap(self):
        eye = (self.p[0], 1.6, self.p[2])
        f = self.fwd()
        ents, aimed = [], None
        for m in self.mobs:
            if m['gone']:
                continue
            h = 1.7 if m['kind'] == 'creeper' else 0.9
            body = (m['pos'][0], h * 0.6, m['pos'][2])
            e = Entity(m['id'], 1, f"minecraft:{m['kind']}", tuple(m['pos']), body,
                       0.3 if m['kind'] == 'creeper' else 0.7, h, True, m['hp'] <= 0, m['hp'], 20.0, name=m['kind'],
                       extra={'aggressive': True, 'swell': m['swell'] / 28.0 if m['kind'] == 'creeper' else None})
            ents.append(e)
            d = [body[i] - eye[i] for i in range(3)]
            n = math.sqrt(sum(v * v for v in d))
            if m['hp'] > 0 and n - e.radius < 3.0 and sum(d[i] / n * f[i] for i in range(3)) > math.cos(
                    math.radians(8)) and (aimed is None or n < aimed[0]):
                aimed = (n, m['id'])
        cam = Camera(eye, f, (math.cos(math.radians(self.yaw)), 0, -math.sin(math.radians(self.yaw))), (0, 1, 0),
                     70, 1.7)
        hand = ['minecraft:stone_sword', 1] if self.sword else ['minecraft:oak_log', 1]
        return Snapshot('t', 1, True, Player(tuple(self.p), None, 'alive', self.health, 20.0), cam, tuple(ents),
                        None, target_id=aimed[1] if aimed else None, screen=Screen(0, 0, 800, 600, True),
                        extra={'attack_strength': min(1.0, self.since_swing / (0.625 if self.sword else 0.25)),
                               'on_ground': True, 'vy': 0.0, 'hotbar': [hand], 'slot': 0})

    def step(self, intent):
        self.t += DT
        self.yaw += intent.turn
        self.pitch = max(-90, min(90, self.pitch + intent.pitch))
        self.since_swing += DT
        if 'attack' in intent.taps:
            s = self.snap()
            dmg = (5.0 if self.sword else 1.0) * (1.0 if s.extra['attack_strength'] >= 0.95 else 0.2)
            m = next((m for m in self.mobs if m['id'] == s.target_id), None)
            if m is not None:
                m['hp'] -= dmg
                kx, kz = m['pos'][0] - self.p[0], m['pos'][2] - self.p[2]
                kn = math.hypot(kx, kz) or 1.0
                m['pos'][0] += kx / kn * 0.8
                m['pos'][2] += kz / kn * 0.8
                if m['hp'] <= 0:
                    m['gone'] = True
            self.since_swing = 0.0
        fx = ('forward' in intent.keys) - ('back' in intent.keys)
        sx = ('right' in intent.keys) - ('left' in intent.keys)
        v = (5.6 if 'sprint' in intent.keys and fx > 0 else 4.3) * DT
        n = math.hypot(fx, sx) or 1.0
        r = math.radians(self.yaw)
        self.p[0] += (math.sin(r) * fx + math.cos(r) * sx) / n * v
        self.p[2] += (math.cos(r) * fx - math.sin(r) * sx) / n * v
        for m in self.mobs:
            if m['gone']:
                continue
            dx, dz = self.p[0] - m['pos'][0], self.p[2] - m['pos'][2]
            d = math.hypot(dx, dz)
            if m['kind'] == 'creeper':
                if d < 3.0:
                    m['lit'] = True
                elif d > 7.0:
                    m['lit'] = False
                m['swell'] = max(0.0, m['swell'] + (20.0 if m['lit'] else -20.0) * DT)
                if m['swell'] >= 30.0:
                    m['gone'] = True
                    if d < 6.0:
                        k = 1.0 - d / 6.0
                        self.blasted += (k * k + k) / 2.0 * 42.0 + 1.0
                    continue
                if not m['lit'] and m['swell'] == 0.0 and d > 1.0:
                    m['pos'][0] += dx / d * 2.0 * DT
                    m['pos'][2] += dz / d * 2.0 * DT
            else:
                if d > 1.2:
                    m['pos'][0] += dx / d * 5.0 * DT
                    m['pos'][2] += dz / d * 5.0 * DT
                m['cd'] -= DT
                if d < 1.8 and m['cd'] <= 0:
                    self.bitten += 2.0
                    m['cd'] = 1.0
        self.health = 20.0 - self.blasted - self.bitten


def field_fight(fd, target, seconds=40):
    from game_assistant.games.minecraft.adapter import MinecraftAdapter
    a = MinecraftAdapter.__new__(MinecraftAdapter)
    f = MeleeFighter(a.combat_profile, lambda h: 0, weapon_damage=a.weapon_damage)
    f.start(target, 'creeper')
    states = []
    for _ in range(int(seconds / DT)):
        intent = f.update(fd.snap(), DT)
        fd.step(intent)
        states.append(f.state)
        if not f.active or fd.health <= 0:
            break
    return f, states


class CreeperTests(unittest.TestCase):
    def test_with_nothing_to_kill_it_a_creeper_is_let_blow_up_harmlessly(self):
        fd = Field([('creeper', 0.0, 9.0, 0.0)], sword=False)
        f, states = field_fight(fd, 10)
        self.assertTrue(fd.mobs[0]['gone'] and fd.mobs[0]['hp'] > 0, states[-5:])   # it blew up...
        self.assertEqual(fd.blasted, 0.0)                                            # ...hurting nothing
        self.assertIn('baiting a creeper', states)

    def test_with_a_sword_a_creeper_is_hit_and_run_from_till_dead(self):
        fd = Field([('creeper', 0.0, 9.0, 0.0)])
        f, states = field_fight(fd, 10)
        self.assertLessEqual(fd.mobs[0]['hp'], 0, states[-5:])
        self.assertEqual(fd.blasted, 0.0)
        self.assertIn('won', f.events)

    def test_two_creepers_and_a_spider_none_of_them_hurts_much(self):
        # (2026-10-13: one creeper lit beside the player, another near, a spider coming; bare-handed; the player
        # was blown up, then bitten to death as the fighter backed off from the spider)
        fd = Field([('creeper', 0.0, 2.5, 8.0), ('creeper', 5.0, 6.0, 0.0), ('spider', -8.0, -8.0, 0.0)],
                   sword=False)
        f, states = field_fight(fd, 10, seconds=60)
        self.assertEqual(fd.blasted, 0.0, states[::30])
        self.assertTrue(fd.mobs[2]['gone'], (fd.mobs[2]['hp'], states[::30]))           # the spider fought...
        self.assertGreater(fd.health, 0)                                                # ...and outlived

    def test_a_spider_is_fought_on_low_health_not_backed_off_from(self):
        fd = Field([('spider', 0.0, 4.0, 0.0)])
        fd.bitten = 14.0                                     # 6 health left: low
        fd.health = 6.0
        f, states = field_fight(fd, 10, seconds=20)
        self.assertNotIn('retreating', states)
        self.assertTrue(fd.mobs[0]['gone'], states[::30])
