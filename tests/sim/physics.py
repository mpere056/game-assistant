"""A Minecraft player's movement, tick by tick, in a world of blocks: for trying the walk's moves end to end
(games/minecraft/moving.py) without the game.

The numbers are the game's (LivingEntity.travel, Player): 20 ticks a second; on the ground the keys push 0.1 a
tick (sprinting 0.13, crouched 0.3 of it) and the speed keeps 0.546 of itself each tick; in the air 0.02 (0.026
sprinting) and 0.91; gravity 0.08 a tick, kept 0.98; a jump 0.42 up (sprinting, 0.2 more the way faced), not
again for 10 ticks while held; a step up to 0.6 taken in the stride; crouched on the ground, never over an edge
(more than 0.6 down); the body 0.6 across, 1.8 high (1.5 crouched), the eye at 1.62 (1.27). Ladders: up 0.2 a
tick pushing into one or jumping, down at most 0.15. Water: no ground under, the speed kept 0.8 a tick, sinking
0.02, rising 0.04 jump held. A block is broken by holding attack on it (its time, in ticks: the hardness byte times
4), put down by 'interact' on a face (the hotbar's selected slot holding blocks). The keys reach the game a frame
late, as from outside it.
"""
from __future__ import annotations

import base64
import math

from game_assistant.core.interface import Camera, Player, Screen, Snapshot
from game_assistant.games.minecraft import blocks as B

TICK = 0.05
WIDTH, HEIGHT, CROUCHED = 0.6, 1.8, 1.5
EYE, EYE_CROUCHED = 1.62, 1.27
STEP = 0.6
GROUND_F = 0.546
AIR_F = 0.91
REACH = 4.5
TOP = {B.SOLID: 1.0, B.PARTIAL: 1.0, B.LOW: 0.5, B.TALL: 1.5, B.DOOR: 1.0, B.POWDER: 0.0}


class World:
    def __init__(self, size=(32, 24, 32)):
        self.size = size
        self.data = bytearray(size[0] * size[1] * size[2])
        self.hard = bytearray(size[0] * size[1] * size[2])
        self.pos = [0.5, 1.0, 0.5]
        self.vel = [0.0, 0.0, 0.0]
        self.yaw, self.pitch = 0.0, 20.0
        self.on_ground = False
        self.collided = False
        self.sprinting = False
        self.crouching = False
        self.jump_delay = 0
        self.takeoffs = []
        self.can_run = True               # (False: never sprints, whatever's pressed: as the game won't, hungry)
        self.air = 300                    # (the game's: 300 full; under water 1 a tick less, 0: drowning)
        self.lowest_air = 300
        self.drowned = 0.0                # drowning damage taken
        self.held = frozenset()
        self.slot = 0
        self.hotbar = [('minecraft:dirt', 64)] + [('', 0)] * 8
        self.breaking = None             # (cell, ticks so far)
        self.placed: list = []
        self.broken: list = []
        self.t = 0.0
        self._acc = 0.0
        self._queue: list = []            # intents on their way to the game (a frame late)
        self.lowest = math.inf
        self.fall_start = None
        self.hurt = 0.0                   # fall damage taken (blocks fallen past 3)
        self.frame = 0

    # ---- the blocks ----

    def put(self, c, kind, hard=2):
        x, y, z = c
        sx, sy, sz = self.size
        if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
            i = (y * sz + z) * sx + x
            self.data[i] = kind
            self.hard[i] = hard if kind != B.AIR else 0

    def fill(self, x0, x1, y0, y1, z0, z1, kind, hard=2):
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.put((x, y, z), kind, hard)

    def at(self, x, y, z):
        sx, sy, sz = self.size
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            return B.SOLID if y < 0 else B.AIR
        return self.data[(y * sz + z) * sx + x]

    def grid(self) -> B.Grid:
        return B.Grid((0, 0, 0), self.size, bytes(self.data), bytes(self.hard))

    def _boxes(self, x, y, z):
        """The collision boxes of block (x, y, z)."""
        k = self.at(x, y, z)
        top = TOP.get(k)
        if top is None or top == 0.0:
            return []
        return [(x, y, z, x + 1, y + top, z + 1)]

    # ---- the body ----

    def box(self, p=None, crouch=None):
        p = p or self.pos
        h = CROUCHED if (self.crouching if crouch is None else crouch) else HEIGHT
        return (p[0] - WIDTH / 2, p[1], p[2] - WIDTH / 2, p[0] + WIDTH / 2, p[1] + h, p[2] + WIDTH / 2)

    def _hits(self, box):
        out = []
        for x in range(math.floor(box[0]), math.floor(box[3] - 1e-9) + 1):
            for y in range(math.floor(box[1]) - 1, math.floor(box[4] - 1e-9) + 1):
                for z in range(math.floor(box[2]), math.floor(box[5] - 1e-9) + 1):
                    for b in self._boxes(x, y, z):
                        if b[0] < box[3] - 1e-7 and b[3] > box[0] + 1e-7 and b[1] < box[4] - 1e-7 and \
                                b[4] > box[1] + 1e-7 and b[2] < box[5] - 1e-7 and b[5] > box[2] + 1e-7:
                            out.append(b)
        return out

    def _free(self, box) -> bool:
        return not self._hits(box)

    def _sweep(self, box, axis, d):
        """How far box can move along axis (0, 1, 2) up to d before hitting a block."""
        if d == 0:
            return 0.0
        lo, hi = axis, axis + 3
        moved = list(box)
        moved[lo] += d
        moved[hi] += d
        span = (min(box[lo], moved[lo]), max(box[hi], moved[hi]))
        test = list(box)
        test[lo], test[hi] = span
        for b in self._hits(tuple(test)):
            if d > 0:
                d = min(d, b[lo] - box[hi])
            else:
                d = max(d, b[hi] - box[lo])
        return d

    def _move(self, dx, dy, dz):
        box = self.box()
        # crouched on the ground: never over an edge (Player.maybeBackOffFromEdge)
        if self.crouching and self.on_ground and dy <= 0:
            def unsupported(ddx, ddz):
                b = (box[0] + ddx, box[1] - STEP, box[2] + ddz, box[3] + ddx, box[4] - STEP + 0.0, box[5] + ddz)
                return self._free((b[0], b[1], b[2], b[3], box[1], b[5]))
            while dx != 0 and unsupported(dx, 0):
                dx = 0.0 if abs(dx) < 0.05 else dx - math.copysign(0.05, dx)
            while dz != 0 and unsupported(0, dz):
                dz = 0.0 if abs(dz) < 0.05 else dz - math.copysign(0.05, dz)
            while dx != 0 and dz != 0 and unsupported(dx, dz):
                dx = 0.0 if abs(dx) < 0.05 else dx - math.copysign(0.05, dx)
                dz = 0.0 if abs(dz) < 0.05 else dz - math.copysign(0.05, dz)
        want = (dx, dy, dz)
        ry = self._sweep(box, 1, dy)
        box = (box[0], box[1] + ry, box[2], box[3], box[4] + ry, box[5])
        rx = self._sweep(box, 0, dx)
        box = (box[0] + rx, box[1], box[2], box[3] + rx, box[4], box[5])
        rz = self._sweep(box, 2, dz)
        box = (box[0], box[1], box[2] + rz, box[3], box[4], box[5] + rz)
        on_ground = dy < 0 and ry > dy + 1e-9
        stepped = False
        if (abs(rx - dx) > 1e-9 or abs(rz - dz) > 1e-9) and (self.on_ground or on_ground):
            # a step up, in the stride
            b0 = self.box()
            up = self._sweep(b0, 1, STEP)
            b1 = (b0[0], b0[1] + up, b0[2], b0[3], b0[4] + up, b0[5])
            sx = self._sweep(b1, 0, dx)
            b1 = (b1[0] + sx, b1[1], b1[2], b1[3] + sx, b1[4], b1[5])
            sz = self._sweep(b1, 2, dz)
            b1 = (b1[0], b1[1], b1[2] + sz, b1[3], b1[4], b1[5] + sz)
            down = self._sweep(b1, 1, -up)
            b1 = (b1[0], b1[1] + down, b1[2], b1[3], b1[4] + down, b1[5])
            if sx * sx + sz * sz > rx * rx + rz * rz + 1e-9:
                box, rx, rz, ry = b1, sx, sz, b1[1] - self.box()[1]
                on_ground, stepped = True, True
        self.pos = [(box[0] + box[3]) / 2, box[1], (box[2] + box[5]) / 2]
        self.collided = abs(rx - want[0]) > 1e-9 or abs(rz - want[2]) > 1e-9
        if abs(ry - dy) > 1e-9 and not stepped:
            self.vel[1] = 0.0
        if abs(rx - dx) > 1e-9:
            self.vel[0] = 0.0
        if abs(rz - dz) > 1e-9:
            self.vel[2] = 0.0
        self.on_ground = on_ground or (stepped and True)

    def _in(self, kind) -> bool:
        b = self.box()
        for x in range(math.floor(b[0]), math.floor(b[3] - 1e-9) + 1):
            for y in range(math.floor(b[1]), math.floor(b[4] - 1e-9) + 1):
                for z in range(math.floor(b[2]), math.floor(b[5] - 1e-9) + 1):
                    if self.at(x, y, z) == kind:
                        return True
        return False

    def tick(self):
        keys = self.held
        fwd = ('forward' in keys) - ('back' in keys)
        side = ('left' in keys) - ('right' in keys)
        self.crouching = 'sneak' in keys and (self.on_ground or self.crouching or True)
        if self.crouching and not self._free(self.box(crouch=False)):
            pass
        if 'sprint' in keys and fwd > 0 and not self.crouching and self.can_run:
            self.sprinting = True
        if fwd <= 0 or self.collided and self.sprinting and abs(self.vel[0]) + abs(self.vel[2]) < 0.01:
            self.sprinting = False
        if self.crouching:
            fwd, side = fwd * 0.3, side * 0.3
        water = self._in(B.WATER)
        ladder = self._in(B.CLIMB)
        # jump
        if self.jump_delay > 0:
            self.jump_delay -= 1
        if 'jump' in keys:
            if water:
                self.vel[1] += 0.04
            elif self.on_ground and self.jump_delay == 0:
                self.takeoffs.append(tuple(self.pos))      # (where each jump went up: the tests look)
                self.vel[1] = 0.42
                if self.sprinting:
                    r = math.radians(self.yaw)
                    self.vel[0] += -math.sin(r) * 0.2
                    self.vel[2] += math.cos(r) * 0.2
                self.jump_delay = 10
        else:
            self.jump_delay = 0
        # the keys' push, the way faced
        if water and self.sprinting and fwd > 0:
            # (a run in water is the dive-swim: the way the view looks, down too)
            r, pr = math.radians(self.yaw), math.radians(self.pitch)
            self.vel[0] += -math.sin(r) * math.cos(pr) * 0.03
            self.vel[1] += -math.sin(pr) * 0.03
            self.vel[2] += math.cos(r) * math.cos(pr) * 0.03
            fwd = side = 0
        if water:
            accel = 0.02
        elif self.on_ground:
            accel = 0.13 if self.sprinting else 0.1
        else:
            accel = 0.026 if self.sprinting else 0.02
        n = math.hypot(fwd, side)
        if n > 1e-9:
            if n > 1:
                fwd, side = fwd / n, side / n
            r = math.radians(self.yaw)
            # (Minecraft: forward is (-sin yaw, cos yaw); left is (cos yaw, sin yaw))
            self.vel[0] += (-math.sin(r) * fwd + math.cos(r) * side) * accel
            self.vel[2] += (math.cos(r) * fwd + math.sin(r) * side) * accel
        if ladder:
            self.vel[0] = max(-0.15, min(0.15, self.vel[0]))
            self.vel[2] = max(-0.15, min(0.15, self.vel[2]))
            self.vel[1] = max(self.vel[1], -0.15)
            if self.crouching and self.vel[1] < 0:
                self.vel[1] = 0.0
        was_ground = self.on_ground
        y0 = self.pos[1]
        self._move(*self.vel)
        if ladder and (self.collided or 'jump' in keys):
            self.vel[1] = 0.2
        # fall damage: what's fallen past 3 blocks
        if not self.on_ground and self.fall_start is None:
            self.fall_start = max(y0, self.pos[1])
        if self.fall_start is not None:
            self.fall_start = max(self.fall_start, self.pos[1]) if self.vel[1] > 0 else self.fall_start
        if self.on_ground and self.fall_start is not None:
            fell = self.fall_start - self.pos[1]
            if fell > 3.0 and not water:
                self.hurt += fell - 3.0
            self.fall_start = None
        if water or ladder:
            self.fall_start = None
        if water:
            self.vel = [self.vel[0] * 0.8, (self.vel[1] - 0.02) * 0.8, self.vel[2] * 0.8]
        else:
            f = GROUND_F if self.on_ground else AIR_F
            self.vel = [self.vel[0] * f, (self.vel[1] - 0.08) * 0.98, self.vel[2] * f]
        self.lowest = min(self.lowest, self.pos[1])
        e = self.eye()
        if self.at(math.floor(e[0]), math.floor(e[1]), math.floor(e[2])) == B.WATER:
            self.air -= 1
            if self.air <= -20:
                self.air, self.drowned = 0, self.drowned + 1.0
        else:
            self.air = min(300, self.air + 4)
        self.lowest_air = min(self.lowest_air, self.air)
        self._work()

    # ---- breaking and building ----

    def eye(self):
        return (self.pos[0], self.pos[1] + (EYE_CROUCHED if self.crouching else EYE), self.pos[2])

    def forward(self):
        # (rounded as the game's own sine table is: straight down it gives (0, -1, 0) exactly, nothing across)
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        cp = round(math.cos(p), 6)
        return (-math.sin(y) * cp, -math.sin(p), math.cos(y) * cp)

    def aimed(self):
        """(the block the crosshair is on, its face) or (None, None)."""
        e, d = self.eye(), self.forward()
        cell = [math.floor(e[i]) for i in range(3)]
        step, t_max, t_delta = [0, 0, 0], [math.inf] * 3, [math.inf] * 3
        for i in range(3):
            if d[i] > 0:
                step[i], t_max[i], t_delta[i] = 1, (cell[i] + 1 - e[i]) / d[i], 1 / d[i]
            elif d[i] < 0:
                step[i], t_max[i], t_delta[i] = -1, (cell[i] - e[i]) / d[i], -1 / d[i]
        face = None
        t = 0.0
        while t <= REACH:
            k = self.at(*cell)
            if k not in (B.AIR, B.WATER) and (k != B.LOW or (e[1] + d[1] * t) - cell[1] < 0.5 + 1e-6):
                return tuple(cell), face
            i = min(range(3), key=lambda j: t_max[j])
            t = t_max[i]
            cell[i] += step[i]
            t_max[i] += t_delta[i]
            face = {(0, 1): 'west', (0, -1): 'east', (1, 1): 'down', (1, -1): 'up', (2, 1): 'north',
                    (2, -1): 'south'}[(i, step[i])]
        return None, None

    def _work(self):
        cell, face = self.aimed()
        if 'attack' in self.held and cell is not None:
            if self.breaking is None or self.breaking[0] != cell:
                self.breaking = (cell, 0)
            c, n = self.breaking
            sx, sy, sz = self.size
            need = max(1, self.hard[(c[1] * sz + c[2]) * sx + c[0]] * 4) if self._inside(c) else 1
            if not self.on_ground:
                need *= 5
            n += 1
            if n >= need:
                self.put(c, B.AIR)
                self.broken.append(c)
                self.breaking = None
            else:
                self.breaking = (c, n)
        else:
            self.breaking = None

    def _inside(self, c):
        sx, sy, sz = self.size
        return 0 <= c[0] < sx and 0 <= c[1] < sy and 0 <= c[2] < sz

    def _place(self):
        cell, face = self.aimed()
        item, count = self.hotbar[self.slot]
        if cell is None or count <= 0 or item != 'minecraft:dirt':
            return
        d = {'up': (0, 1, 0), 'down': (0, -1, 0), 'east': (1, 0, 0), 'west': (-1, 0, 0), 'south': (0, 0, 1),
             'north': (0, 0, -1)}[face]
        into = (cell[0] + d[0], cell[1] + d[1], cell[2] + d[2])
        if self.at(*into) not in (B.AIR, B.WATER):
            return
        b = self.box()
        if b[0] < into[0] + 1 and b[3] > into[0] and b[1] < into[1] + 1 and b[4] > into[1] and \
                b[2] < into[2] + 1 and b[5] > into[2]:
            return                                      # (the body's in it)
        self.put(into, B.SOLID)
        self.placed.append(into)
        self.hotbar[self.slot] = (item, count - 1)

    # ---- the frame ----

    def apply(self, intent, dt):
        """The walk's keys and view for this frame, reaching the game a frame later; then the game's ticks."""
        self._queue.append(intent)
        if len(self._queue) > 1:
            it = self._queue.pop(0)
            self.yaw += it.turn
            self.pitch = max(-90.0, min(90.0, self.pitch + it.pitch))
            self.held = frozenset(it.keys) | (frozenset({'jump'}) if 'jump' in it.taps else frozenset())
            for tap in it.taps:
                if tap.startswith('slot'):
                    self.slot = int(tap[4:]) - 1
                elif tap == 'interact':
                    self._place()
        self._acc += dt
        while self._acc >= TICK:
            self._acc -= TICK
            self.t += TICK
            self.tick()
        self.frame += 1

    def snap(self) -> Snapshot:
        m = lambda v: (-v[0], v[1], v[2])               # (the snapshot's X is the game's mirrored)
        e = self.eye()
        f = self.forward()
        right = (math.cos(math.radians(self.yaw)), 0.0, math.sin(math.radians(self.yaw)))   # (the game's left)
        cam = Camera(m(e), m(f), m((-right[0], 0.0, -right[2])), (0.0, 1.0, 0.0), 70.0, 1.7)
        cell, face = self.aimed()
        feet = (math.floor(self.pos[0]), math.floor(self.pos[1] + 0.01), math.floor(self.pos[2]))
        near = self._near(feet)
        extra = {'aim_block': cell, 'aim_face': face, 'slot': self.slot, 'block_slot': 0, 'tool_slot': -1,
                 'blocks': self.hotbar[0][1], 'hotbar': list(self.hotbar), 'on_ground': self.on_ground,
                 'vy': self.vel[1], 'vx': self.vel[0], 'vz': self.vel[2], 'collided': self.collided,
                 'crouching': self.crouching, 'sprinting': self.sprinting, 'food': 20, 'near': near,
                 'air': max(0, self.air)}
        pl = Player(m(tuple(self.pos)), m(f), 'alive', 20.0, 20.0)
        return Snapshot('minecraft', self.frame, True, pl, cam, (), None, screen=Screen(0, 0, 800, 600, True),
                        menu=False, extra=extra)

    def _near(self, feet):
        r, down, up = 3, 3, 4
        x0, y0, z0 = feet[0] - r, feet[1] - down, feet[2] - r
        sx, sy, sz = 2 * r + 1, down + up + 1, 2 * r + 1
        out = bytearray(sx * sy * sz)
        i = 0
        for y in range(sy):
            for z in range(sz):
                for x in range(sx):
                    out[i] = self.at(x0 + x, y0 + y, z0 + z)
                    i += 1
        return {'min': [x0, y0, z0], 'size': [sx, sy, sz], 'data': base64.b64encode(bytes(out)).decode()}

    def feet(self):
        return (math.floor(self.pos[0]), math.floor(self.pos[1] + 0.1251), math.floor(self.pos[2]))


def run(world: World, moves, grid=None, seconds=30.0, fps=30, mover=None):
    """The moves done in the world by the walk's mover: its last status ('done', 'replan', or None: out of
    time), and the mover."""
    from game_assistant.games.minecraft.moving import Mover
    mv = mover or Mover()
    mv.start(moves, grid if grid is not None else world.grid())
    status = None
    for _ in range(int(seconds * fps)):
        intent, status = mv.update(world.snap(), 1 / fps)
        world.apply(intent, 1 / fps)
        if status in ('done', 'replan'):
            for _ in range(10):                           # (the last keys reach the game; it settles)
                world.apply(intent if status == 'done' else type(intent)(), 1 / fps)
            break
    return status, mv
