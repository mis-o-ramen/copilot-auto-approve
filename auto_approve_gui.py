"""Allow ボタン自動クリックの GUI 版 (tkinter)。

起動: python auto_approve_gui.py  (Windows でコンソールを出さないなら pythonw)
設定は同じフォルダの settings.json に保存される。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import ttk

import auto_approve as core
from tray import TrayIcon
from ui import THEME_CHOICES, Theme, ToggleSwitch

log = logging.getLogger("auto_approve.gui")

APP_DIR = core.app_dir()
SETTINGS_PATH = APP_DIR / "settings.json"
AUTO_OFF_CHOICES = {"なし": 0, "15分": 15, "30分": 30, "1時間": 60, "2時間": 120}
MAX_LOG_LINES = 200

# 最後の「操作中のため保留」からこの秒数以内なら「操作待ち」と表示する
WAIT_DISPLAY_SEC = 1.5


@dataclass
class Settings:
    always_on_top: bool = True
    dry_run: bool = False
    threshold: float = 0.85
    interval: float = 1.0
    auto_off: str = "なし"
    beep: bool = False
    start_on_launch: bool = False
    hotkey_enabled: bool = sys.platform != "darwin"  # macOS は tkinter と併用で不安定なため既定 OFF
    hotkey: str = "<ctrl>+<alt>+a"
    pause_enabled: bool = True
    pause_seconds: float = 1.5
    # pystray は macOS では tkinter のメインループと両立せず、Linux は環境依存のため Windows のみ既定 ON
    tray_enabled: bool = sys.platform == "win32"
    close_to_tray: bool = False
    theme: str = "system"  # system / light / dark
    show_details: bool = False
    geometry: str = ""

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> Settings:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            return cls(**known)
        except TypeError:
            return cls()

    def save(self, path: Path = SETTINGS_PATH) -> None:
        try:
            path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as e:
            log.warning("設定を保存できません: %s", e)


class GlobalHotkey:
    """pynput によるグローバルホットキー。pynput が無ければ何もしない。"""

    def __init__(self, callback):
        self._callback = callback
        self._listener = None

    def start(self, combo: str) -> str | None:
        """登録する。失敗時はエラーメッセージを返す。"""
        self.stop()
        try:
            from pynput import keyboard
        except ImportError as e:
            if e.name == "pynput":
                return "pynput 未インストール (pip install pynput)"
            return f"ホットキーを使えません: {e}"
        try:
            self._listener = keyboard.GlobalHotKeys({combo: self._callback})
            self._listener.start()
        except Exception as e:  # 書式エラーや権限不足
            self._listener = None
            return f"ホットキーを登録できません: {e}"
        return None

    @property
    def active(self) -> bool:
        return self._listener is not None

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class App:
    def __init__(self, root: tk.Tk, settings: Settings):
        self.root = root
        self.settings = settings
        self.events: queue.Queue[tuple] = queue.Queue()

        self.running = False
        self.generation = 0  # 停止済みワーカーからの遅延イベントを無視するため
        self.stop_event: threading.Event | None = None
        self.config: core.WatchConfig | None = None
        self.clicks = 0
        self.started_at = 0.0
        self.auto_off_at = 0.0
        self.error = ""
        self.last_wait_at = 0.0

        self.hotkey = GlobalHotkey(lambda: self.events.put(("hotkey",)))
        self.tray = TrayIcon(on_toggle=lambda: self.events.put(("hotkey",)),
                             on_show=lambda: self.events.put(("show",)),
                             on_quit=lambda: self.events.put(("quit",)))

        self._build_ui()
        self._apply_topmost()
        if settings.geometry:
            root.geometry(settings.geometry)
        self._apply_hotkey()
        self._apply_tray()
        self._render()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll)
        if settings.start_on_launch:
            root.after(300, self.start)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        s = self.settings
        root = self.root
        root.title("Auto Approve")
        root.resizable(False, False)
        self.theme = Theme(root)
        p = self.theme.apply(s.theme)
        f = self.theme.font
        sw = self.theme.switch_style
        px = 16

        # ----- ヘッダー: ステータス + スイッチ -----
        header = ttk.Frame(root, padding=(px, 14, px, 4))
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        self.dot = tk.Canvas(header, width=14, height=14, highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(1, 1, 13, 13, fill=p.off, outline="")
        self.dot.grid(row=0, column=0, padx=(0, 8))
        self.status_var = tk.StringVar()
        ttk.Label(header, textvariable=self.status_var, style="Title.TLabel").grid(
            row=0, column=1, sticky="w")
        self.switch = ToggleSwitch(header, command=self.toggle)
        self.switch.grid(row=0, column=2, rowspan=2, sticky="e")
        self.sub_var = tk.StringVar()
        self.theme.label(header, "muted", textvariable=self.sub_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ----- 統計タイル -----
        tiles = ttk.Frame(root, padding=(px, 8, px, 4))
        tiles.pack(fill="x")
        tiles.columnconfigure((0, 1), weight=1, uniform="tile")
        self.clicks_var = tk.StringVar(value="0")
        self.clicks_sub_var = tk.StringVar()
        self.last_var = tk.StringVar(value="--:--:--")
        self.last_detail_var = tk.StringVar(value="まだクリックしていません")
        self._tile(tiles, 0, "クリック", self.clicks_var, self.clicks_sub_var)
        self._tile(tiles, 1, "最終クリック", self.last_var, self.last_detail_var)

        # ----- クイック設定 -----
        quick = ttk.Frame(root, padding=(px, 8, px, 4))
        quick.pack(fill="x")
        self.topmost_var = tk.BooleanVar(value=s.always_on_top)
        self.dry_var = tk.BooleanVar(value=s.dry_run)
        ttk.Checkbutton(quick, text="最前面", variable=self.topmost_var, style=sw, takefocus=0,
                        command=self._on_topmost).pack(side="left")
        ttk.Checkbutton(quick, text="検知のみ", variable=self.dry_var, style=sw, takefocus=0,
                        command=self._on_settings_change).pack(side="left", padx=(16, 0))

        row = ttk.Frame(root, padding=(px, 4, px, 12))
        row.pack(fill="x")
        ttk.Label(row, text="自動OFF").pack(side="left")
        self.auto_off_var = tk.StringVar(value=s.auto_off if s.auto_off in AUTO_OFF_CHOICES
                                         else "なし")
        cb = ttk.Combobox(row, textvariable=self.auto_off_var, width=7, state="readonly",
                          values=list(AUTO_OFF_CHOICES), font=f(10))
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_auto_off_change())
        self.details_btn = ttk.Button(row, takefocus=0, style="Link.TButton",
                                      command=self._toggle_details)
        self.details_btn.pack(side="right")

        # ----- 詳細: 設定 / ログ のタブ -----
        self.details = ttk.Frame(root, padding=(px, 0, px, px))
        nb = ttk.Notebook(self.details)
        nb.pack(fill="both", expand=True)
        form = ttk.Frame(nb, padding=12)
        logf = ttk.Frame(nb, padding=8)
        nb.add(form, text="設定")
        nb.add(logf, text="ログ")
        form.columnconfigure(1, weight=1)
        r = 0

        def section(title: str) -> None:
            nonlocal r
            self.theme.label(form, "muted", text=title, style="Section.TLabel").grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(8 if r else 0, 4))
            r += 1

        def label(text: str) -> None:
            ttk.Label(form, text=text).grid(row=r, column=0, sticky="w", pady=3)

        section("検知")
        self.threshold_var = tk.DoubleVar(value=s.threshold)
        self.threshold_label = tk.StringVar()
        label("一致しきい値")
        ttk.Scale(form, from_=0.5, to=0.99, variable=self.threshold_var,
                  command=lambda v: self._on_settings_change()).grid(row=r, column=1, sticky="ew",
                                                                     padx=8)
        ttk.Label(form, textvariable=self.threshold_label, width=4).grid(row=r, column=2)
        r += 1

        self.interval_var = tk.StringVar(value=str(s.interval))
        label("チェック間隔 (秒)")
        sp = ttk.Spinbox(form, from_=0.2, to=10, increment=0.1, width=6, font=f(10),
                         textvariable=self.interval_var, command=self._on_settings_change)
        sp.grid(row=r, column=1, sticky="w", padx=8)
        sp.bind("<FocusOut>", lambda e: self._on_settings_change())
        r += 1

        self.pause_var = tk.BooleanVar(value=s.pause_enabled)
        self.pause_sec_var = tk.StringVar(value=str(s.pause_seconds))
        ttk.Checkbutton(form, text="操作中はクリックしない", variable=self.pause_var, style=sw,
                        takefocus=0, command=self._on_settings_change).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=3)
        r += 1
        label("  最後の操作から (秒)")
        ps = ttk.Spinbox(form, from_=0.5, to=10, increment=0.5, width=6, font=f(10),
                         textvariable=self.pause_sec_var, command=self._on_settings_change)
        ps.grid(row=r, column=1, sticky="w", padx=8)
        ps.bind("<FocusOut>", lambda e: self._on_settings_change())
        r += 1

        section("操作")
        self.hotkey_enabled_var = tk.BooleanVar(value=s.hotkey_enabled)
        self.hotkey_var = tk.StringVar(value=s.hotkey)
        ttk.Checkbutton(form, text="ホットキー", variable=self.hotkey_enabled_var, style=sw,
                        takefocus=0, command=self._apply_hotkey).grid(row=r, column=0, sticky="w",
                                                                      pady=3)
        he = ttk.Entry(form, textvariable=self.hotkey_var, width=14, font=f(10))
        he.grid(row=r, column=1, columnspan=2, sticky="ew", padx=8)
        he.bind("<Return>", lambda e: self._apply_hotkey())
        he.bind("<FocusOut>", lambda e: self._apply_hotkey())
        r += 1
        self.hotkey_status = tk.StringVar()
        self.theme.label(form, "muted", textvariable=self.hotkey_status,
                         style="Muted.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1

        self.beep_var = tk.BooleanVar(value=s.beep)
        self.autostart_var = tk.BooleanVar(value=s.start_on_launch)
        for text, var in (("クリック時に音を鳴らす", self.beep_var),
                          ("起動時に自動で ON", self.autostart_var)):
            ttk.Checkbutton(form, text=text, variable=var, style=sw, takefocus=0,
                            command=self._save).grid(row=r, column=0, columnspan=3, sticky="w",
                                                     pady=3)
            r += 1

        section("表示")
        self.theme_var = tk.StringVar(value=next(
            (k for k, v in THEME_CHOICES.items() if v == s.theme), "システム"))
        label("テーマ")
        tc = ttk.Combobox(form, textvariable=self.theme_var, width=8, state="readonly",
                          values=list(THEME_CHOICES), font=f(10))
        tc.grid(row=r, column=1, sticky="w", padx=8)
        tc.bind("<<ComboboxSelected>>", lambda e: self._on_theme_change())
        r += 1

        self.tray_var = tk.BooleanVar(value=s.tray_enabled)
        self.close_to_tray_var = tk.BooleanVar(value=s.close_to_tray)
        ttk.Checkbutton(form, text="トレイに常駐", variable=self.tray_var, style=sw, takefocus=0,
                        command=self._apply_tray).grid(row=r, column=0, columnspan=3, sticky="w",
                                                       pady=3)
        r += 1
        ttk.Checkbutton(form, text="× でトレイに格納", variable=self.close_to_tray_var, style=sw,
                        takefocus=0, command=self._save).grid(row=r, column=0, columnspan=3,
                                                              sticky="w", pady=3)
        r += 1
        self.tray_status = tk.StringVar()
        self.theme.label(form, "muted", textvariable=self.tray_status, style="Muted.TLabel",
                         wraplength=300).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        ttk.Button(form, text="画像フォルダを開く", takefocus=0,
                   command=self._open_images).grid(row=r, column=0, columnspan=3, sticky="w",
                                                   pady=(10, 0))

        self.log_box = tk.Listbox(logf, height=10, width=40, activestyle="none", bd=0,
                                  highlightthickness=0, font=f(9))
        self.log_box.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=sb.set)

        self._apply_palette()
        # sv-ttk は <<ThemeChanged>> で tk ウィジェットの色を上書きするので、その後にも適用する
        root.after(100, self._apply_palette)
        # 詳細の開閉でウィンドウ幅 (= スイッチの位置) が変わらないよう、開いた状態の幅に揃える
        self.details.pack(fill="both")
        root.update_idletasks()
        root.minsize(root.winfo_reqwidth(), 1)
        self.show_details = s.show_details
        self._layout_details()

        root.bind("<space>", self._on_space)

    def _tile(self, parent, col: int, caption: str, value: tk.StringVar,
              sub: tk.StringVar) -> None:
        tile = ttk.Frame(parent, style="Tile.TFrame", padding=(12, 8))
        tile.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        t = self.theme
        t.label(tile, "tile_muted", text=caption, style="TileCaption.TLabel").pack(anchor="w")
        t.label(tile, "tile", textvariable=value, style="TileValue.TLabel").pack(anchor="w")
        t.label(tile, "tile_muted", textvariable=sub, style="TileSub.TLabel").pack(anchor="w")

    def _apply_palette(self) -> None:
        """ttk 以外のウィジェットにテーマ色を反映する。"""
        p = self.theme.palette
        self.dot.configure(bg=p.bg)
        self.switch.configure(bg=p.bg)
        self.log_box.configure(bg=p.card, fg=p.fg, selectbackground=p.border,
                               selectforeground=p.fg)

    def _on_theme_change(self) -> None:
        self.theme.apply(THEME_CHOICES.get(self.theme_var.get(), "system"))
        self._apply_palette()
        self.root.after(100, self._apply_palette)
        self._render()
        self._save()

    def _layout_details(self) -> None:
        if self.show_details:
            self.details.pack(fill="both")
            self.details_btn.config(text="設定とログ ▴")
        else:
            self.details.pack_forget()
            self.details_btn.config(text="設定とログ ▾")

    def _toggle_details(self) -> None:
        self.show_details = not self.show_details
        self._layout_details()
        self._save()

    def _on_space(self, event) -> None:
        # 入力欄でのスペースは無視
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return
        self.toggle()

    # ---------- 状態表示 ----------
    def _render(self) -> None:
        p = self.theme.palette
        dry = self.dry_var.get()
        waiting = self.running and time.monotonic() - self.last_wait_at < WAIT_DISPLAY_SEC
        if self.error and not self.running:
            color, text, tray_text = p.error, "エラー", "Error"
        elif waiting:
            color, text, tray_text = p.wait, "操作待ち", "Waiting (user active)"
        elif self.running:
            color, text, tray_text = ((p.dry, "検知のみ", "Detect only") if dry
                                      else (p.on, "監視中", "ON"))
        else:
            color, text, tray_text = p.off, "停止中", "OFF"
        self.dot.itemconfig(self.dot_id, fill=color)
        self.status_var.set(text)
        self.switch.set_colors(p.dry if dry else p.on, p.track_off, p.bg)
        self.switch.set(self.running)
        self.root.title(f"{'[ON] ' if self.running else ''}Auto Approve")
        self.clicks_var.set(str(self.clicks))
        self.clicks_sub_var.set("検知のみモード" if dry else "このセッション")
        self.threshold_label.set(f"{self.threshold_var.get():.2f}")
        self.tray.update(color, f"Auto Approve: {tray_text}", self.running)

        if self.error and not self.running:
            self.sub_var.set(self.error)
        elif self.running:
            parts = [f"稼働 {fmt_duration(time.monotonic() - self.started_at)}"]
            if waiting:
                parts.insert(0, "操作中のためクリック保留")
            if self.auto_off_at:
                parts.append(f"自動OFFまで {fmt_duration(self.auto_off_at - time.monotonic())}")
            self.sub_var.set(" · ".join(parts))
        else:
            hint = "ボタン / スペースキー"
            if self.hotkey_enabled_var.get() and self.hotkey.active:
                hint += f" / {self.hotkey_var.get()}"
            self.sub_var.set(f"{hint} で ON")

    def _log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        log.info(msg)
        self.log_box.insert("end", line)
        if self.log_box.size() > MAX_LOG_LINES:
            self.log_box.delete(0, self.log_box.size() - MAX_LOG_LINES - 1)
        self.log_box.see("end")

    # ---------- ON / OFF ----------
    def toggle(self) -> None:
        if self.running:
            self.stop("手動で停止")
        else:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        try:
            templates = core.load_templates([core.DEFAULT_IMAGE_DIR])
        except (ValueError, FileNotFoundError) as e:
            templates, self.error = [], str(e)
        else:
            self.error = "" if templates else "images/ にボタン画像がありません"
        if not templates:
            self._log(f"開始できません: {self.error}")
            self._render()
            return

        self.config = self._make_config()
        self.stop_event = threading.Event()
        self.generation += 1
        self.running = True
        self.started_at = time.monotonic()
        self._reset_auto_off()
        threading.Thread(target=self._worker, daemon=True,
                         args=(self.generation, templates, self.config, self.stop_event)).start()
        self._log(f"ON: {', '.join(t.name for t in templates)}")
        self._render()

    def stop(self, reason: str) -> None:
        if not self.running:
            return
        self.running = False
        self.auto_off_at = 0.0
        if self.stop_event:
            self.stop_event.set()
        self._log(f"OFF: {reason}")
        self._render()

    def _worker(self, gen: int, templates, config: core.WatchConfig,
                stop: threading.Event) -> None:
        def on_hit(match: core.Match, x: int, y: int, clicked: bool) -> None:
            self.events.put(("hit", gen, match, x, y, clicked))

        def on_wait(match: core.Match) -> None:
            self.events.put(("wait", gen, match))

        try:
            core.watch(templates, config, stop, on_hit, on_wait=on_wait)
        except Exception as e:
            if core.is_failsafe(e):
                self.events.put(("failsafe", gen))
            else:
                log.exception("監視中にエラー")
                self.events.put(("error", gen, f"{type(e).__name__}: {e}"))

    def _poll(self) -> None:
        """ワーカー/ホットキーからのイベントをメインスレッドで処理する。"""
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        finally:
            # 途中で例外が出てもポーリングは止めない
            self.root.after(200, self._poll)
        if self.running and self.auto_off_at and time.monotonic() >= self.auto_off_at:
            self.stop("自動OFF タイマー")
        self._render()

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "hotkey":
            self.toggle()
            return
        if kind == "show":
            self._show_window()
            return
        if kind == "quit":
            self.quit()
            return
        if ev[1] != self.generation or not self.running:
            return  # 既に停止したワーカーからのイベント
        if kind == "hit":
            _, _, match, x, y, clicked = ev
            if clicked:
                self.clicks += 1
                self.last_var.set(time.strftime("%H:%M:%S"))
                self.last_detail_var.set(f"{match.template} · {match.score:.2f}")
                if self.beep_var.get():
                    self.root.bell()
            self._log(f"{'クリック' if clicked else '検知'}: {match.template} "
                      f"score={match.score:.3f} ({x}, {y})")
        elif kind == "wait":
            if time.monotonic() - self.last_wait_at >= WAIT_DISPLAY_SEC:
                self._log(f"操作中のためクリック保留: {ev[2].template}")
            self.last_wait_at = time.monotonic()
        elif kind == "failsafe":
            self.stop("FAILSAFE (マウスが画面左上隅)")
        elif kind == "error":
            self.error = ev[2]
            self.stop(f"エラー: {ev[2]}")

    # ---------- 設定 ----------
    def _make_config(self) -> core.WatchConfig:
        return core.WatchConfig(threshold=round(self.threshold_var.get(), 2),
                                interval=self._interval(), dry_run=self.dry_var.get(),
                                pause_when_active=self._pause_when_active())

    def _interval(self) -> float:
        try:
            return min(10.0, max(0.2, float(self.interval_var.get())))
        except ValueError:
            return self.settings.interval

    def _pause_seconds(self) -> float:
        try:
            return min(10.0, max(0.5, float(self.pause_sec_var.get())))
        except ValueError:
            return self.settings.pause_seconds

    def _pause_when_active(self) -> float:
        return self._pause_seconds() if self.pause_var.get() else 0.0

    def _on_settings_change(self) -> None:
        # 実行中の監視にも即時反映 (watch はループごとに config を読み直す)
        if self.config is not None:
            self.config.threshold = round(self.threshold_var.get(), 2)
            self.config.interval = self._interval()
            self.config.dry_run = self.dry_var.get()
            self.config.pause_when_active = self._pause_when_active()
        self._render()
        self._save()

    def _on_topmost(self) -> None:
        self._apply_topmost()
        self._save()

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", self.topmost_var.get())

    def _on_auto_off_change(self) -> None:
        if self.running:
            self._reset_auto_off()
        self._save()

    def _reset_auto_off(self) -> None:
        minutes = AUTO_OFF_CHOICES.get(self.auto_off_var.get(), 0)
        self.auto_off_at = time.monotonic() + minutes * 60 if minutes else 0.0

    def _apply_hotkey(self) -> None:
        if self.hotkey_enabled_var.get():
            err = self.hotkey.start(self.hotkey_var.get().strip())
            if err:
                log.warning("%s", err)
            self.hotkey_status.set(err or f"{self.hotkey_var.get()} で ON/OFF")
        else:
            self.hotkey.stop()
            self.hotkey_status.set("ホットキー無効")
        self._save()

    def _apply_tray(self) -> None:
        if self.tray_var.get():
            err = self.tray.start(self.theme.palette.off, "Auto Approve")
            self.tray_status.set(err or "")
            if err:
                self.tray_var.set(False)
        else:
            self.tray.stop()
            self.tray_status.set("")
        self._save()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self._apply_topmost()

    def _open_images(self) -> None:
        path = core.DEFAULT_IMAGE_DIR
        path.mkdir(exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _save(self) -> None:
        s = self.settings
        s.always_on_top = self.topmost_var.get()
        s.dry_run = self.dry_var.get()
        s.threshold = round(self.threshold_var.get(), 2)
        s.interval = self._interval()
        s.auto_off = self.auto_off_var.get()
        s.beep = self.beep_var.get()
        s.start_on_launch = self.autostart_var.get()
        s.hotkey_enabled = self.hotkey_enabled_var.get()
        s.hotkey = self.hotkey_var.get().strip()
        s.pause_enabled = self.pause_var.get()
        s.pause_seconds = self._pause_seconds()
        s.tray_enabled = self.tray_var.get()
        s.close_to_tray = self.close_to_tray_var.get()
        s.theme = THEME_CHOICES.get(self.theme_var.get(), "system")
        s.show_details = self.show_details
        s.save()

    def on_close(self) -> None:
        if self.tray.active and self.close_to_tray_var.get():
            self.root.withdraw()  # 監視は続ける。トレイの「ウィンドウを表示」で戻す
            return
        self.quit()

    def quit(self) -> None:
        self.tray.stop()
        if self.stop_event:
            self.stop_event.set()
        self.hotkey.stop()
        # サイズは詳細の開閉で変わるので位置だけ保存
        self.settings.geometry = f"+{self.root.winfo_x()}+{self.root.winfo_y()}"
        self._save()
        self.root.destroy()


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    # Tk より先に DPI 対応を宣言しておくと、Windows の高 DPI 環境で文字がぼやけない
    core.enable_windows_dpi_awareness()
    root = tk.Tk()
    App(root, Settings.load())
    root.mainloop()


if __name__ == "__main__":
    main()
