"""GUI の見た目: テーマ (sv-ttk)・配色・フォント・トグルスイッチ。

sv-ttk / darkdetect が無い環境でも標準の ttk で動くようにフォールバックする。
"""

from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

THEME_CHOICES = {"システム": "system", "ライト": "light", "ダーク": "dark"}


@dataclass(frozen=True)
class Palette:
    bg: str
    card: str
    fg: str
    muted: str
    border: str
    track_off: str
    # ステータス色
    on: str
    off: str
    dry: str
    wait: str
    error: str


LIGHT = Palette(bg="#fafafa", card="#ffffff", fg="#1c1c1c", muted="#6b6b6b", border="#e3e3e3",
                track_off="#c4c4c4", on="#16a34a", off="#9aa0a6", dry="#2563eb", wait="#d97706",
                error="#dc2626")
DARK = Palette(bg="#1c1c1c", card="#2b2b2b", fg="#f3f3f3", muted="#a3a3a3", border="#3b3b3b",
               track_off="#5a5a5a", on="#22c55e", off="#8b8f94", dry="#60a5fa", wait="#fbbf24",
               error="#f87171")


def resolve_mode(mode: str) -> str:
    """"system" を OS の設定に従って "light" / "dark" に解決する。"""
    if mode in ("light", "dark"):
        return mode
    try:
        import darkdetect

        return "dark" if darkdetect.isDark() else "light"
    except Exception:
        return "light"


def _ui_font_family() -> str:
    families = set(tkfont.families())
    candidates = {
        "win32": ["Yu Gothic UI", "Meiryo UI", "Segoe UI"],
        "darwin": ["Hiragino Sans", "Helvetica Neue"],
    }.get(sys.platform, ["Noto Sans CJK JP", "Noto Sans JP", "DejaVu Sans"])
    for name in candidates:
        if name in families:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


class Theme:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.style = ttk.Style(root)
        self.family = _ui_font_family()
        self.mode = "light"
        self.palette = LIGHT
        self.has_sv_ttk = True
        self._labels: list[tuple[ttk.Label, str]] = []

    def font(self, size: int = 10, weight: str = "normal") -> tuple:
        return (self.family, size, weight)

    def apply(self, mode: str) -> Palette:
        self.mode = resolve_mode(mode)
        try:
            import sv_ttk

            sv_ttk.set_theme(self.mode, self.root)
        except Exception:
            self.has_sv_ttk = False
            if "clam" in self.style.theme_names():
                self.style.theme_use("clam")
        p = self.palette = DARK if self.mode == "dark" else LIGHT
        if not self.has_sv_ttk:
            self.style.configure(".", background=p.bg, foreground=p.fg)
        self.root.configure(bg=p.bg)

        s = self.style
        f = self.font
        s.configure("TLabel", font=f(10))
        s.configure("TCheckbutton", font=f(10))
        s.configure("Title.TLabel", font=f(17, "bold"))
        s.configure("Muted.TLabel", font=f(9))
        s.configure("Section.TLabel", font=f(9, "bold"))
        s.configure("Tile.TFrame", background=p.card)
        s.configure("TileCaption.TLabel", font=f(9))
        s.configure("TileValue.TLabel", font=f(18, "bold"))
        s.configure("TileSub.TLabel", font=f(8))
        s.configure("Link.TButton", font=f(9))
        # sv-ttk のスイッチ。無ければ通常のチェックボックス
        self.switch_style = "Switch.TCheckbutton" if self.has_sv_ttk else "TCheckbutton"
        self.recolor()
        return p

    # sv-ttk ではスタイル側の Label の文字色・背景色が効かないため、ウィジェットに直接設定する
    _LABEL_COLORS = {
        "muted": lambda p: {"foreground": p.muted},
        "tile": lambda p: {"foreground": p.fg, "background": p.card},
        "tile_muted": lambda p: {"foreground": p.muted, "background": p.card},
    }

    def label(self, parent, kind: str, **kw) -> ttk.Label:
        """テーマ変更時に色を追従させるラベルを作る。"""
        w = ttk.Label(parent, **kw, **self._LABEL_COLORS[kind](self.palette))
        self._labels.append((w, kind))
        return w

    def recolor(self) -> None:
        for w, kind in self._labels:
            w.configure(**self._LABEL_COLORS[kind](self.palette))


class ToggleSwitch(tk.Canvas):
    """大きめの ON/OFF スイッチ (Canvas で描画、ノブがスライドする)。"""

    W, H = 60, 32
    STEPS = 6

    def __init__(self, master, command: Callable[[], None], **kw):
        super().__init__(master, width=self.W, height=self.H, highlightthickness=0, bd=0,
                         cursor="hand2", **kw)
        self._command = command
        self._on = False
        self._pos = 0.0  # ノブ位置 0.0 (左) 〜 1.0 (右)
        self._anim = None
        self._colors = ("#16a34a", "#c4c4c4", "#fafafa")
        self.bind("<Button-1>", lambda e: self._command())
        self._draw()

    def set_colors(self, on_color: str, off_color: str, bg: str) -> None:
        if (on_color, off_color, bg) != self._colors:
            self._colors = (on_color, off_color, bg)
            self.configure(bg=bg)
            self._draw()

    def set(self, on: bool) -> None:
        if on == self._on:
            return
        self._on = on
        if self._anim:
            self.after_cancel(self._anim)
        self._step()

    def _step(self) -> None:
        target = 1.0 if self._on else 0.0
        delta = 1.0 / self.STEPS
        if abs(self._pos - target) <= delta:
            self._pos = target
            self._anim = None
        else:
            self._pos += delta if target > self._pos else -delta
            self._anim = self.after(16, self._step)
        self._draw()

    def _draw(self) -> None:
        on_color, off_color, bg = self._colors
        self.delete("all")
        w, h = self.W, self.H
        r = h / 2
        track = on_color if self._on else off_color
        # 角丸のトラック
        self.create_oval(0, 0, h, h, fill=track, outline=track)
        self.create_oval(w - h, 0, w, h, fill=track, outline=track)
        self.create_rectangle(r, 0, w - r, h, fill=track, outline=track)
        # ノブ
        m = 3
        x = m + self._pos * (w - h)
        self.create_oval(x, m, x + h - 2 * m, h - m, fill="white", outline="white")
