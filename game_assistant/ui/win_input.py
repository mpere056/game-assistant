"""Keyboard and mouse input for auto-walk (Windows): hold keys, turn the camera, and notice the player.

- Keys are sent with SendInput as hardware scan codes (games read those), only while the game is the
  active window (the caller checks), and every held key is released on stop, on pause and at exit.
- The camera turns by relative mouse movement. How many degrees one mouse count turns depends on the
  game's sensitivity setting, so it is measured while walking: the camera's own yaw change against
  the counts sent (`Turner`).
- `KeyWatch` is a low-level keyboard hook on its own thread: Windows marks injected key presses, so a
  movement key the player presses themselves is told apart from ours, and stops the walk at once.
"""
from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x2, 0x8
MOUSEEVENTF_MOVE = 0x1
MOUSE_BUTTONS = {'LMB': (0x2, 0x4), 'RMB': (0x8, 0x10), 'MMB': (0x20, 0x40)}  # (down, up) flags
MARK = 0x47414149  # "GAAI": our own input, in dwExtraInfo
WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN = 13, 0x100, 0x104
LLKHF_INJECTED = 0x10

# Scan codes (set 1) of keys auto-walk may use.
SCAN = {'W': 0x11, 'A': 0x1E, 'S': 0x1F, 'D': 0x20, 'E': 0x12, 'F': 0x21, 'Q': 0x10, 'R': 0x13, 'X': 0x2D,
        'SPACE': 0x39, 'LSHIFT': 0x2A, 'LCTRL': 0x1D, 'TAB': 0x0F, 'C': 0x2E, 'V': 0x2F}
# Virtual-key codes the watcher treats as "the player is moving": WASD, space, shift.
MOVE_VKS = {0x57, 0x41, 0x53, 0x44, 0x20, 0x10, 0xA0, 0xA1}

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wt.DWORD), ('wParamL', wt.WORD), ('wParamH', wt.WORD)]


class _U(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _key(scan: int, up: bool) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, MARK)
    return i


def _mouse(dx: int, dy: int) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, MARK)
    return i


def _send(items: list[INPUT]) -> None:
    if items:
        arr = (INPUT * len(items))(*items)
        user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


class Keys:
    """Holds a set of named actions as keys (bindings from the settings), releasing the rest."""

    def __init__(self, bindings: dict[str, str]):
        self.bindings = {k: v.upper() for k, v in bindings.items()}
        self.held: set[str] = set()
        atexit.register(self.release_all)

    def set(self, actions) -> None:
        want = {a for a in actions if self.bindings.get(a) in SCAN}
        items = [_key(SCAN[self.bindings[a]], True) for a in self.held - want]
        items += [_key(SCAN[self.bindings[a]], False) for a in want - self.held]
        _send(items)
        self.held = want

    def tap(self, action: str) -> None:
        k = self.bindings.get(action)
        if k in MOUSE_BUTTONS:
            down, up = MOUSE_BUTTONS[k]
            for flag in (down, up):
                i = INPUT(type=INPUT_MOUSE)
                i.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, MARK)
                _send([i])
                if flag == down:
                    time.sleep(0.05)
            return
        if k in SCAN:
            _send([_key(SCAN[k], False)])
            time.sleep(0.05)
            _send([_key(SCAN[k], True)])

    def release_all(self) -> None:
        if self.held:
            _send([_key(SCAN[self.bindings[a]], True) for a in self.held])
        self.held = set()


class Turner:
    """Turns the camera by mouse, learning degrees per count from what the camera actually does."""

    def __init__(self, deg_per_count: float = 0.08):
        self.k = deg_per_count
        self._sent = 0.0
        self._yaw0: float | None = None

    def turn(self, degrees: float, cam_yaw: float) -> None:
        # Learn from the last turn: the yaw change per count sent.
        if self._yaw0 is not None and abs(self._sent) >= 20:
            got = (cam_yaw - self._yaw0 + 180.0) % 360.0 - 180.0
            if abs(got) > 0.2 and got * self._sent > 0:
                k = got / self._sent
                self.k = min(1.0, max(0.005, 0.8 * self.k + 0.2 * k))
        counts = int(round(degrees / self.k))
        counts = max(-400, min(400, counts))
        if counts:
            _send([_mouse(counts, 0)])
        self._yaw0, self._sent = cam_yaw, counts


class KeyWatch:
    """Calls on_player_key(vk) when the player presses a movement key themselves (not injected)."""

    def __init__(self, on_player_key):
        self.on_player_key = on_player_key
        self._thread = threading.Thread(target=self._run, name='key watch', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        class KBDLL(ctypes.Structure):
            _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                        ('dwExtraInfo', ULONG_PTR)]

        user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t

        def proc(code, wparam, lparam):
            if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLL)).contents
                if not (kb.flags & LLKHF_INJECTED) and kb.vkCode in MOVE_VKS:
                    try:
                        self.on_player_key(kb.vkCode)
                    except Exception:
                        pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = proc_type(proc)  # keep a reference
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_type, wt.HINSTANCE, wt.DWORD]
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
