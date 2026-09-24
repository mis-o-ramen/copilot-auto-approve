"""ユーザーがキーボード/マウスを操作中かどうかの判定。

OS の「最後の入力からの経過秒数」を使う。自動クリック自体も入力として数えられるため、
自分のクリック直後の入力は除外する。OS の API が使えない環境ではマウス移動で代用する。
"""

from __future__ import annotations

import sys
import time
from typing import Callable

# 自分のクリック (click → moveTo) の後、OS の入力記録に反映されるまでの余裕
SELF_INPUT_GRACE = 0.3


def _idle_windows() -> Callable[[], float]:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = wintypes.DWORD

    def idle() -> float:
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo failed")
        # DWORD の tick は約 49.7 日で一周するので差分も 32bit で取る
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    return idle


def _idle_macos() -> Callable[[], float]:
    import Quartz  # pyautogui の依存 (pyobjc-framework-Quartz) に含まれる

    any_event = getattr(Quartz, "kCGAnyInputEventType", 0xFFFFFFFF)
    state = Quartz.kCGEventSourceStateCombinedSessionState

    def idle() -> float:
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(state, any_event))

    return idle


def _idle_x11() -> Callable[[], float]:
    from Xlib import display  # pyautogui の依存 (python-xlib)

    d = display.Display()
    if not d.has_extension("MIT-SCREEN-SAVER"):
        raise OSError("MIT-SCREEN-SAVER extension not available")
    root = d.screen().root

    def idle() -> float:
        return d.screensaver_query_info(root).idle / 1000.0

    return idle


def system_idle_source() -> Callable[[], float] | None:
    """OS の最終入力からの経過秒数を返す関数。取得できなければ None。"""
    factory = {"win32": _idle_windows, "darwin": _idle_macos}.get(sys.platform, _idle_x11)
    try:
        fn = factory()
        fn()
        return fn
    except Exception:
        return None


class MouseMoveIdle:
    """OS の API が使えない場合の代用: マウスが動いていない秒数。"""

    def __init__(self, position: Callable[[], tuple[int, int]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if position is None:
            import pyautogui

            def position() -> tuple[int, int]:
                p = pyautogui.position()
                return p.x, p.y

        self._position = position
        self._clock = clock
        self._last_pos = position()
        self._last_move = clock()

    def __call__(self) -> float:
        pos = self._position()
        now = self._clock()
        if pos != self._last_pos:
            self._last_pos = pos
            self._last_move = now
        return now - self._last_move


class UserActivity:
    def __init__(self, idle_source: Callable[[], float] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._idle = idle_source or system_idle_source() or MouseMoveIdle()
        self._clock = clock
        self._self_input_at: float | None = None

    def mark_self_input(self) -> None:
        """自動クリックした直後に呼ぶ。"""
        self._self_input_at = self._clock()

    def is_active(self, required_idle: float) -> bool:
        """最後の入力から required_idle 秒経っていなければ True (=操作中)。"""
        idle = self._idle()
        if idle >= required_idle:
            return False
        if self._self_input_at is not None:
            # 最後の入力が自分のクリックなら、ユーザー操作とはみなさない
            since_self = self._clock() - self._self_input_at
            if idle >= since_self - SELF_INPUT_GRACE:
                return False
        return True
