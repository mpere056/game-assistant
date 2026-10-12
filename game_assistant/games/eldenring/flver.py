"""Elden Ring model files (FLVER2): the attachment points ("dummies") a character's hitboxes hang on, and how
far each can reach from the character's centre.

An attack's hitboxes (AtkParam: spheres or capsules of a radius between one or two dummies) move with the
bone their dummies are attached to (the sword, the hand, the head). With the bones of the rest pose, a
dummy's reach is how far it gets from the body's centre line when the limb it hangs on is stretched out:
the limb's root (the bone just below the spine or the root: a clavicle, a thigh, the neck) away from the
centre, plus the bone lengths down to the dummy's bone, plus the dummy's offset from that bone. That is
an upper bound for the swing itself; how far the character travels during the attack (a lunge, a dive)
is measured live by the fighter.

Format (64-bit FLVER2, little endian), from SoulsFormats FLVER2 (MIT, by TKGP):
  header 0x80: 'FLVER\\0' 'L\\0', u32 version, u32 dataOffset, u32 dataLength, i32 dummyCount,
               i32 materialCount, i32 boneCount, ...
  dummies (0x40 each): f32 position[3], u8 color[4], f32 forward[3], i16 referenceId, i16 parentBone,
               f32 upward[3], i16 attachBone, ...
  materials (0x20 each), then bones (0x80 each): f32 translation[3], i32 nameOffset (UTF-16),
               f32 rotation[3] (radians), i16 parent, i16 child, f32 scale[3], ...
A bone's local transform is scale * rotX * rotZ * rotY * translation (row vectors); its model transform
is local * parent's. A dummy's position is in its parent bone's space.
"""
from __future__ import annotations

import math
import struct

import numpy as np

_ROOTISH = ('spine', 'pelvis', 'master', 'root', 'neck')   # a limb starts just below these


def _matrix(t, r, s) -> np.ndarray:
    cx, sx = math.cos(r[0]), math.sin(r[0])
    cy, sy = math.cos(r[1]), math.sin(r[1])
    cz, sz = math.cos(r[2]), math.sin(r[2])
    S = np.diag([s[0], s[1], s[2], 1.0])
    RX = np.array([[1, 0, 0, 0], [0, cx, sx, 0], [0, -sx, cx, 0], [0, 0, 0, 1]])
    RZ = np.array([[cz, sz, 0, 0], [-sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    RY = np.array([[cy, 0, -sy, 0], [0, 1, 0, 0], [sy, 0, cy, 0], [0, 0, 0, 1]])
    T = np.eye(4)
    T[3, :3] = t
    return S @ RX @ RZ @ RY @ T


def parse(data: bytes):
    """(dummies [(ref id, position, parent bone, attach bone)], bones [(name, translation, rotation, parent,
    scale)])."""
    if data[:6] != b'FLVER\0':
        raise ValueError('not a FLVER file')
    n_dmy, n_mat, n_bone = struct.unpack_from('<iii', data, 0x14)
    o = 0x80
    dummies = []
    for _ in range(n_dmy):
        pos = struct.unpack_from('<3f', data, o)
        ref, parent = struct.unpack_from('<hh', data, o + 0x1C)
        attach = struct.unpack_from('<h', data, o + 0x2C)[0]
        dummies.append((ref, pos, parent, attach))
        o += 0x40
    o += n_mat * 0x20
    bones = []
    for _ in range(n_bone):
        t = struct.unpack_from('<3f', data, o)
        name_off = struct.unpack_from('<i', data, o + 0x0C)[0]
        rot = struct.unpack_from('<3f', data, o + 0x10)
        parent = struct.unpack_from('<h', data, o + 0x1C)[0]
        scale = struct.unpack_from('<3f', data, o + 0x20)
        name = ''
        if 0 < name_off < len(data):
            end = name_off
            while end + 1 < len(data) and data[end:end + 2] != b'\0\0':
                end += 2
            name = data[name_off:end].decode('utf-16-le', 'replace')
        bones.append((name, t, rot, parent, scale))
        o += 0x80
    return dummies, bones


def dummy_reach(data: bytes) -> dict[int, float]:
    """{dummy reference id: metres it can reach from the body's centre line} for one model."""
    dummies, bones = parse(data)
    n = len(bones)
    world: list = [None] * n

    def bone_world(i, depth=0):
        if world[i] is None:
            name, t, r, parent, s = bones[i]
            local = _matrix(t, r, s)
            world[i] = local @ bone_world(parent, depth + 1) if 0 <= parent < n and depth < 64 else local
        return world[i]

    def pos(i):
        return bone_world(i)[3, :3]

    out: dict[int, float] = {}
    for ref, p, parent, attach in dummies:
        if ref < 0:
            continue
        base = bone_world(parent) if 0 <= parent < n else np.eye(4)
        d = (np.array([p[0], p[1], p[2], 1.0]) @ base)[:3]
        reach = math.hypot(d[0], d[2])
        if 0 <= attach < n:
            chain, i = [], attach                  # attach bone up to the limb's root
            while 0 <= i < n and len(chain) < 40:
                parent_i = bones[i][3]
                chain.append(i)
                if parent_i < 0 or any(k in bones[parent_i][0].lower() for k in _ROOTISH):
                    break
                i = parent_i
            root = chain[-1]
            r = pos(root)
            length = sum(float(np.linalg.norm(pos(a) - pos(b))) for a, b in zip(chain, chain[1:]))
            length += float(np.linalg.norm(d - pos(attach)))
            reach = max(reach, math.hypot(r[0], r[2]) + length)
        out[ref] = round(max(out.get(ref, 0.0), reach), 2)
    return out
