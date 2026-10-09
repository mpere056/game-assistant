"""Draws the fairy over the game: a small glowing orb with flapping wings (Windows only).

A transparent, click-through, always-on-top layered window of our own; it never touches the game's
rendering. Sprites (several sizes, four wing positions) are drawn once at start-up with soft,
per-pixel alpha; each frame only moves the window, picks a sprite and sets its overall opacity.
Runs on its own thread (a window needs a message loop); other threads only call show()/hide().
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import threading
import time

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
ULW_ALPHA, PM_REMOVE, AC_SRC_ALPHA = 2, 1, 1
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 1, 2, 0x10

SIZES = [12, 16, 20, 26, 32, 40, 50, 62, 76, 96]
FLAPS = [1.0, 0.72, 0.42, 0.72]          # wing length per frame
COLOR = (120, 214, 255)                  # Navi-like light blue (R, G, B)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('style', wt.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
                ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH), ('lpszMenuName', wt.LPCWSTR),
                ('lpszClassName', wt.LPCWSTR), ('hIconSm', wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long), ('biYPelsPerMeter', ctypes.c_long),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte), ('SourceConstantAlpha', ctypes.c_ubyte),
                ('AlphaFormat', ctypes.c_ubyte)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.GetDC.restype = wt.HDC
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def make_dpi_aware() -> None:
    """Use physical pixels, like the game, so screen positions line up on scaled displays."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware v2
    except (AttributeError, OSError):
        pass


def _smooth(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def draw_sprite(n: int, flap: float, color=COLOR) -> bytes:
    """Premultiplied BGRA pixels (top-down) of an n x n fairy: a white-hot core in a soft coloured
    glow, with two translucent wings."""
    out = bytearray(n * n * 4)
    c = (n - 1) / 2
    core = n * 0.11
    glow = n * 0.22
    wings = []
    for side in (-1, 1):
        wx, wy = c + side * n * 0.2, c - n * 0.08
        ang = math.radians(35 * side)
        wings.append((wx, wy, n * 0.21 * flap, n * 0.1, math.cos(ang), math.sin(ang)))
    for y in range(n):
        for x in range(n):
            dx, dy = x - c, y - c
            d = math.hypot(dx, dy)
            a_glow = math.exp(-(d / glow) ** 2) * 0.9
            whiteness = 1.0 - _smooth(0.0, core * 1.6, d)
            a_core = 1.0 - _smooth(core * 0.8, core * 1.4, d)
            a_wing = 0.0
            for wx, wy, ra, rb, ca, sa in wings:
                ux, uy = x - wx, y - wy
                rx, ry = ux * ca + uy * sa, -ux * sa + uy * ca
                q = (rx / max(ra, 0.5)) ** 2 + (ry / max(rb, 0.5)) ** 2
                if q < 1.0:
                    a_wing = max(a_wing, 0.6 * (1.0 - q) ** 0.5)
            a = max(a_glow, a_core, a_wing)
            if a <= 0.004:
                continue
            w = max(whiteness, a_wing * 0.8)
            r = color[0] + (255 - color[0]) * w
            g = color[1] + (255 - color[1]) * w
            b = color[2] + (255 - color[2]) * w
            i = (y * n + x) * 4
            out[i] = int(b * a)
            out[i + 1] = int(g * a)
            out[i + 2] = int(r * a)
            out[i + 3] = int(255 * a)
    return bytes(out)


class FairyOverlay:
    def __init__(self, color=COLOR):
        self.color = color
        self._lock = threading.Lock()
        self._want: tuple | None = None   # (x, y, size_px, opacity, speaking) or None = hidden
        self._stop = False
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: str | None = None

    # ---- called from any thread ----

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='fairy overlay', daemon=True)
            self._thread.start()

    def show(self, x: float, y: float, size_px: float, opacity: float, speaking: bool = False) -> None:
        with self._lock:
            self._want = (x, y, size_px, opacity, speaking)

    def hide(self) -> None:
        with self._lock:
            self._want = None

    def close(self) -> None:
        self._stop = True

    # ---- the window thread ----

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:  # never take the assistant down with the overlay
            self.error = f'{type(e).__name__}: {e}'
            self.ready.set()

    def _loop(self) -> None:
        self._proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        inst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=inst,
                         lpszClassName='GameAssistantFairy')
        user32.RegisterClassExW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                      'GameAssistantFairy', 'Fairy', WS_POPUP, 0, 0, 32, 32, None, None, inst, None)
        if not hwnd:
            raise OSError(f'could not create the overlay window ({ctypes.get_last_error()})')
        screen = user32.GetDC(None)
        sprites = {}
        for n in SIZES:
            for k, flap in enumerate(FLAPS):
                dc = gdi32.CreateCompatibleDC(screen)
                bi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=n, biHeight=-n, biPlanes=1, biBitCount=32)
                bits = ctypes.c_void_p()
                bmp = gdi32.CreateDIBSection(screen, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
                data = draw_sprite(n, flap, self.color)
                ctypes.memmove(bits, data, len(data))
                gdi32.SelectObject(dc, bmp)
                sprites[(n, k)] = dc
        self.ready.set()
        shown = False
        msg = wt.MSG()
        t0 = time.perf_counter()
        while not self._stop:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            with self._lock:
                want = self._want
            if want is None or want[3] <= 0.02:
                if shown:
                    user32.ShowWindow(hwnd, SW_HIDE)
                    shown = False
                time.sleep(0.03)
                continue
            x, y, size, opacity, speaking = want
            t = time.perf_counter() - t0
            if speaking:  # pulse and grow a little while it talks
                size *= 1.15 + 0.1 * math.sin(t * 9.0)
                opacity = min(1.0, opacity * (0.85 + 0.15 * math.sin(t * 9.0)))
            n = min(SIZES, key=lambda s: abs(s - size))
            k = int(t * 14) % len(FLAPS)
            pos = wt.POINT(int(x - n / 2), int(y - n / 2))
            sz = wt.SIZE(n, n)
            src = wt.POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, int(255 * max(0.0, min(1.0, opacity))), AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(pos), ctypes.byref(sz), sprites[(n, k)],
                                       ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
            if not shown:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                shown = True
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            time.sleep(1 / 120)
        user32.DestroyWindow(hwnd)
