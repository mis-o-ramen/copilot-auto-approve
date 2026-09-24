"""タスクトレイ (macOS ではメニューバー) 常駐アイコン。pystray が無ければ無効。

pystray のコールバックは別スレッドで呼ばれるので、呼び出し側でメインスレッドへ渡すこと。
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("auto_approve.tray")


def make_icon_image(color: str, size: int = 64):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 8
    d.ellipse((m, m, size - m, size - m), fill=color, outline="white", width=max(2, size // 16))
    return img


class TrayIcon:
    def __init__(self, on_toggle: Callable[[], None], on_show: Callable[[], None],
                 on_quit: Callable[[], None]):
        self._on_toggle = on_toggle
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon = None
        self._running = False
        self._state: tuple[str, str] | None = None

    @property
    def active(self) -> bool:
        return self._icon is not None

    def start(self, color: str, title: str) -> str | None:
        """アイコンを表示する。失敗時はエラーメッセージを返す。"""
        if self._icon is not None:
            return None
        try:
            import pystray
        except ImportError as e:
            return "pystray 未インストール (pip install pystray pillow)" \
                if e.name in ("pystray", "PIL") else f"トレイを使えません: {e}"

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: "OFF にする" if self._running else "ON にする",
                             lambda icon, item: self._on_toggle(), default=True),
            pystray.MenuItem("ウィンドウを表示", lambda icon, item: self._on_show()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了", lambda icon, item: self._on_quit()),
        )
        try:
            self._icon = pystray.Icon("auto_approve", make_icon_image(color), title, menu)
            self._icon.run_detached()
        except Exception as e:
            self._icon = None
            log.warning("トレイアイコンを表示できません: %s", e)
            return f"トレイアイコンを表示できません: {e}"
        self._state = (color, title)
        return None

    def update(self, color: str, title: str, running: bool) -> None:
        """状態を反映する。バックエンドのエラーで呼び出し元を止めないよう例外は握りつぶす。

        title は ASCII にすること (Linux の Xorg バックエンドは Latin-1 しか扱えない)。
        """
        if self._icon is None:
            return
        changed_running = running != self._running
        self._running = running
        try:
            if (color, title) != self._state:
                self._state = (color, title)
                self._icon.icon = make_icon_image(color)
                self._icon.title = title
            if changed_running:
                self._icon.update_menu()
        except Exception as e:
            log.warning("トレイアイコンを更新できません: %s", e)

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
            self._state = None
