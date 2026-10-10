"""Navi's speech bubble: a rounded white bubble with a light blue rim and a little tail pointing at
the fairy, drawn with PIL into premultiplied BGRA pixels for a layered window (fairy_overlay.py).

Drawn at twice the size and scaled down, so the curves and text are smooth. The text is wrapped to a
maximum width; a long answer keeps its last MAX_LINES lines (the newest words stay visible).
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

MAX_LINES = 6
FILL = (255, 255, 255, 238)
RIM = (120, 214, 255, 255)        # the fairy's light blue
INK = (28, 40, 72, 255)
SHADOW = (10, 20, 50, 90)
SS = 2                            # supersampling factor
FONTS = ('seguisb.ttf', 'segoeui.ttf', 'arial.ttf')


@lru_cache(maxsize=8)
def _font(px: int):
    folder = Path('C:/Windows/Fonts')
    for name in FONTS:
        if (folder / name).exists():
            return ImageFont.truetype(str(folder / name), px)
    return ImageFont.load_default(px)


def wrap(text: str, font, max_w: float) -> list[str]:
    lines = []
    for para in text.split('\n'):
        line = ''
        for word in para.split(' '):
            trial = f'{line} {word}' if line else word
            if font.getlength(trial) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines or ['']


def render(text: str, font_px: int, max_text_w: int, tail: str = 'bl', dots: int = -1):
    """Returns (width, height, premultiplied BGRA bytes, (tip_x, tip_y)): the tail's tip in the
    picture, which goes next to the fairy. tail: 'bl', 'br', 'tl' or 'tr' (bottom/top, left/right).
    dots >= 0 draws the "thinking" dots instead of text, with dot number `dots % 3` brightest."""
    f = _font(font_px * SS)
    pad, radius, rim = int(font_px * 0.75) * SS, int(font_px * 0.9) * SS, max(2, font_px // 9) * SS
    tail_h, tail_w = int(font_px * 0.8) * SS, int(font_px * 0.9) * SS
    line_h = int(font_px * 1.3) * SS
    if dots >= 0:
        lines, text_w = [], int(font_px * 2.6) * SS
    else:
        lines = wrap(text, f, max_text_w * SS)[-MAX_LINES:]
        text_w = max(int(f.getlength(ln)) for ln in lines) if lines else 0
        text_w = max(text_w, font_px * SS)
    box_w = text_w + 2 * pad
    box_h = (len(lines) if lines else 1) * line_h + 2 * pad - (line_h - font_px * SS) // 2
    margin = 6 * SS                          # room for the shadow
    w, h = box_w + 2 * margin, box_h + tail_h + 2 * margin
    top = 'b' in tail                        # tail below the box: the box sits at the top
    bx0, by0 = margin, margin + (0 if top else tail_h)
    bx1, by1 = bx0 + box_w, by0 + box_h
    # The tail: a small triangle near one corner, leaning out toward the fairy.
    left = 'l' in tail
    base_x = bx0 + radius + tail_w * 0.3 if left else bx1 - radius - tail_w * 0.3
    tip_x = base_x - tail_w * 0.6 if left else base_x + tail_w * 0.6
    if top:
        tri = [(base_x - tail_w / 2, by1 - rim), (base_x + tail_w / 2, by1 - rim), (tip_x, by1 + tail_h)]
        tip = (tip_x, by1 + tail_h)
    else:
        tri = [(base_x - tail_w / 2, by0 + rim), (base_x + tail_w / 2, by0 + rim), (tip_x, by0 - tail_h)]
        tip = (tip_x, by0 - tail_h)

    # A soft shadow (blurred at the final size, which is four times cheaper), then the rim, then
    # the white inside drawn smaller by the rim's width.
    sw, sh = math.ceil(w / SS), math.ceil(h / SS)
    mask = Image.new('L', (sw, sh), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((bx0 / SS, by0 / SS + 2, bx1 / SS, by1 / SS + 2), radius / SS, fill=SHADOW[3])
    md.polygon([(x / SS, y / SS + 2) for x, y in tri], fill=SHADOW[3])
    shadow = Image.new('RGBA', (sw, sh), SHADOW[:3] + (0,))
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(4)))
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((bx0, by0, bx1, by1), radius, fill=RIM)
    d.polygon(tri, fill=RIM)
    d.rounded_rectangle((bx0 + rim, by0 + rim, bx1 - rim, by1 - rim), radius - rim, fill=FILL)
    k = rim * 1.9  # the tail's inside: narrower and shorter by about the rim
    inner_tip = (tip[0] + (base_x - tip[0]) * k / tail_h * 0.5, tip[1] + (k if top else -k))
    d.polygon([(tri[0][0] + rim, tri[0][1] - (rim if top else -rim)), (tri[1][0] - rim, tri[1][1] - (rim if top else -rim)),
               inner_tip], fill=FILL)
    if dots >= 0:
        cy = (by0 + by1) / 2
        r = font_px * 0.22 * SS
        for i in range(3):
            cx = bx0 + pad + (i + 0.5) * text_w / 3
            a = 255 if i == dots % 3 else 110
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RIM[:3] + (a,))
    else:
        y = by0 + pad - (line_h - font_px * SS) // 4
        for ln in lines:
            d.text((bx0 + pad, y), ln, font=f, fill=INK)
            y += line_h
    out = shadow
    out.alpha_composite(img.resize((sw, sh), Image.BOX))
    return out.width, out.height, _bgra_premultiplied(out), (round(tip[0] / SS), round(tip[1] / SS))


def _bgra_premultiplied(img: Image.Image) -> bytes:
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    alpha = a[..., 3:4]
    rgb = (a[..., :3] * alpha + 127) // 255
    return np.concatenate([rgb[..., ::-1], alpha], axis=-1).astype(np.uint8).tobytes()


def choose_tail(size: tuple[int, int], anchor: tuple[float, float], bounds: tuple[int, int, int, int]) -> str:
    """Above and to the right of the fairy if that fits in the game window, else flipped."""
    bx, by, bw, bh = bounds
    w, h = size
    right = anchor[0] + w <= bx + bw
    above = anchor[1] - h >= by
    return ('b' if above else 't') + ('l' if right else 'r')


def position(size: tuple[int, int], tip: tuple[int, int], anchor: tuple[float, float],
             bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    """The window's top-left so the tail's tip sits at `anchor`, kept inside the game window."""
    bx, by, bw, bh = bounds
    x = round(anchor[0] - tip[0])
    y = round(anchor[1] - tip[1])
    x = max(bx, min(x, bx + bw - size[0]))
    y = max(by, min(y, by + bh - size[1]))
    return x, y
