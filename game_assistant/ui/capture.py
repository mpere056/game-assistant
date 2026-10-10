"""The game's own picture, from its window (Windows only).

PrintWindow with PW_RENDERFULLCONTENT asks the window manager for the window's own pixels, so the
fairy, its speech bubble and any window covering the game are not in it (a screen grab had Navi and
an old bubble over the very thing being asked about). About 30 ms for 1280x720.
The game window is found by its client area's place and size on the screen (from the adapter), so
nothing here is game-specific.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

from PIL import Image

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2
GWL_EXSTYLE = -20
# Overlays drawn over games (Discord's, Steam's, our own fairy) are layered, click-through or tool
# windows, often exactly the game's size: never take one of them for the game.
OVERLAY_STYLES = 0x80000 | 0x20 | 0x80  # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]


class _BIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', ctypes.c_long), ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


def find_window(x: int, y: int, width: int, height: int) -> int | None:
    """The top-level window (not ours, not an overlay) whose client area is exactly at (x, y) with
    this size."""
    found = []
    me = os.getpid()

    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & OVERLAY_STYLES:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            return True
        r = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        p = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(p))
        if (p.x, p.y, r.right, r.bottom) == (x, y, width, height):
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows(ENUMPROC(check), 0)
    return found[0] if found else None


def window_picture(hwnd: int) -> Image.Image | None:
    """The window's client area as an RGB picture, or None if it came back empty (all black)."""
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    try:
        gdi32.SelectObject(mdc, bmp)
        if not user32.PrintWindow(hwnd, mdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    if max(hi for _lo, hi in img.resize((64, 36)).getextrema()) < 8:
        return None
    return img
