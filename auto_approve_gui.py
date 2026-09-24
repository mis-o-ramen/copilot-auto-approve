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

log = logging.getLogger("auto_approve.gui")

APP_DIR = core.app_dir()
SETTINGS_PATH = APP_DIR / "settings.json"
AUTO_OFF_CHOICES = {"なし": 0, "15分": 15, "30分": 30, "1時間": 60, "2時間": 120}
MAX_LOG_LINES = 200

COLOR_ON = "#2e9d4f"
COLOR_OFF = "#8a8a8a"
COLOR_DRY = "#2f7fd0"
COLOR_ERROR = "#d0453a"
COLOR_WAIT = "#d99a1e"
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
        pad = {"padx": 10, "pady": 4}

        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        self.dot = tk.Canvas(top, width=16, height=16, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 14, 14, fill=COLOR_OFF, outline="")
        self.dot.pack(side="left")
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var, font=("", 11, "bold")).pack(
            side="left", padx=(6, 0))

        # macOS では tk.Button の背景色が効かないため Label をボタンとして使う
        self.toggle_btn = tk.Label(top, width=6, font=("", 14, "bold"), fg="white",
                                   cursor="hand2", pady=4)
        self.toggle_btn.pack(side="right")
        self.toggle_btn.bind("<Button-1>", lambda e: self.toggle())

        info = ttk.Frame(root)
        info.pack(fill="x", **pad)
        self.clicks_var = tk.StringVar()
        self.last_var = tk.StringVar(value="最終クリック: -")
        self.sub_var = tk.StringVar()
        ttk.Label(info, textvariable=self.clicks_var).pack(anchor="w")
        ttk.Label(info, textvariable=self.last_var).pack(anchor="w")
        ttk.Label(info, textvariable=self.sub_var, foreground="#666").pack(anchor="w")

        opts = ttk.Frame(root)
        opts.pack(fill="x", **pad)
        self.topmost_var = tk.BooleanVar(value=s.always_on_top)
        self.dry_var = tk.BooleanVar(value=s.dry_run)
        ttk.Checkbutton(opts, text="最前面", variable=self.topmost_var, takefocus=0,
                        command=self._on_topmost).pack(side="left")
        ttk.Checkbutton(opts, text="検知のみ (クリックしない)", variable=self.dry_var,
                        takefocus=0, command=self._on_settings_change).pack(side="left", padx=8)

        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="自動OFF:").pack(side="left")
        self.auto_off_var = tk.StringVar(value=s.auto_off if s.auto_off in AUTO_OFF_CHOICES
                                         else "なし")
        cb = ttk.Combobox(row, textvariable=self.auto_off_var, width=7, state="readonly",
                          values=list(AUTO_OFF_CHOICES))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_auto_off_change())
        self.details_btn = ttk.Button(row, width=8, takefocus=0, command=self._toggle_details)
        self.details_btn.pack(side="right")

        # ----- 詳細 (設定・ログ) -----
        self.details = ttk.Frame(root)
        form = ttk.LabelFrame(self.details, text="設定")
        form.pack(fill="x", padx=10, pady=4)

        self.threshold_var = tk.DoubleVar(value=s.threshold)
        self.threshold_label = tk.StringVar()
        ttk.Label(form, text="一致しきい値").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Scale(form, from_=0.5, to=0.99, variable=self.threshold_var, length=140,
                  command=lambda v: self._on_settings_change()).grid(row=0, column=1, sticky="w")
        ttk.Label(form, textvariable=self.threshold_label, width=5).grid(row=0, column=2)

        self.interval_var = tk.StringVar(value=str(s.interval))
        ttk.Label(form, text="チェック間隔(秒)").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        sp = ttk.Spinbox(form, from_=0.2, to=10, increment=0.1, width=6,
                         textvariable=self.interval_var, command=self._on_settings_change)
        sp.grid(row=1, column=1, sticky="w")
        sp.bind("<FocusOut>", lambda e: self._on_settings_change())

        self.hotkey_enabled_var = tk.BooleanVar(value=s.hotkey_enabled)
        self.hotkey_var = tk.StringVar(value=s.hotkey)
        ttk.Checkbutton(form, text="ホットキー", variable=self.hotkey_enabled_var, takefocus=0,
                        command=self._apply_hotkey).grid(row=2, column=0, sticky="w", padx=6)
        he = ttk.Entry(form, textvariable=self.hotkey_var, width=18)
        he.grid(row=2, column=1, columnspan=2, sticky="w")
        he.bind("<Return>", lambda e: self._apply_hotkey())
        he.bind("<FocusOut>", lambda e: self._apply_hotkey())
        self.hotkey_status = tk.StringVar()
        ttk.Label(form, textvariable=self.hotkey_status, foreground="#666").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=6)

        self.beep_var = tk.BooleanVar(value=s.beep)
        self.autostart_var = tk.BooleanVar(value=s.start_on_launch)
        ttk.Checkbutton(form, text="クリック時に音を鳴らす", variable=self.beep_var, takefocus=0,
                        command=self._save).grid(row=4, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Checkbutton(form, text="起動時に自動で ON", variable=self.autostart_var, takefocus=0,
                        command=self._save).grid(row=5, column=0, columnspan=3, sticky="w", padx=6)

        self.pause_var = tk.BooleanVar(value=s.pause_enabled)
        self.pause_sec_var = tk.StringVar(value=str(s.pause_seconds))
        pf = ttk.Frame(form)
        pf.grid(row=6, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Checkbutton(pf, text="操作中はクリックしない (最後の操作から", variable=self.pause_var,
                        takefocus=0, command=self._on_settings_change).pack(side="left")
        ps = ttk.Spinbox(pf, from_=0.5, to=10, increment=0.5, width=4,
                         textvariable=self.pause_sec_var, command=self._on_settings_change)
        ps.pack(side="left")
        ps.bind("<FocusOut>", lambda e: self._on_settings_change())
        ttk.Label(pf, text="秒)").pack(side="left")

        self.tray_var = tk.BooleanVar(value=s.tray_enabled)
        self.close_to_tray_var = tk.BooleanVar(value=s.close_to_tray)
        tf = ttk.Frame(form)
        tf.grid(row=7, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Checkbutton(tf, text="トレイに常駐", variable=self.tray_var, takefocus=0,
                        command=self._apply_tray).pack(side="left")
        ttk.Checkbutton(tf, text="× でトレイに格納", variable=self.close_to_tray_var,
                        takefocus=0, command=self._save).pack(side="left", padx=8)
        self.tray_status = tk.StringVar()
        ttk.Label(form, textvariable=self.tray_status, foreground="#666").grid(
            row=8, column=0, columnspan=3, sticky="w", padx=6)

        ttk.Button(form, text="画像フォルダを開く", takefocus=0,
                   command=self._open_images).grid(row=9, column=0, columnspan=3, sticky="w",
                                                   padx=6, pady=4)

        logf = ttk.LabelFrame(self.details, text="ログ")
        logf.pack(fill="both", padx=10, pady=(4, 10))
        self.log_box = tk.Listbox(logf, height=8, width=48, activestyle="none")
        self.log_box.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=sb.set)

        self.show_details = s.show_details
        self._layout_details()

        root.bind("<space>", self._on_space)

    def _layout_details(self) -> None:
        if self.show_details:
            self.details.pack(fill="both")
            self.details_btn.config(text="詳細 ▲")
        else:
            self.details.pack_forget()
            self.details_btn.config(text="詳細 ▼")

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
        dry = self.dry_var.get()
        waiting = self.running and time.monotonic() - self.last_wait_at < WAIT_DISPLAY_SEC
        if self.error and not self.running:
            color, text, tray_text = COLOR_ERROR, "エラー", "Error"
        elif waiting:
            color, text, tray_text = COLOR_WAIT, "操作待ち", "Waiting (user active)"
        elif self.running:
            color, text, tray_text = ((COLOR_DRY, "検知のみ", "Detect only") if dry
                                      else (COLOR_ON, "監視中", "ON"))
        else:
            color, text, tray_text = COLOR_OFF, "停止中", "OFF"
        self.dot.itemconfig(self.dot_id, fill=color)
        self.status_var.set(text)
        self.toggle_btn.config(text="ON" if self.running else "OFF",
                               bg=COLOR_ON if self.running else COLOR_OFF)
        self.root.title(f"{'[ON] ' if self.running else ''}Auto Approve")
        self.clicks_var.set(f"クリック数: {self.clicks}" + ("  (検知のみモード)" if dry else ""))
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
            self.sub_var.set(" / ".join(parts))
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
                self.last_var.set(f"最終クリック: {time.strftime('%H:%M:%S')} "
                                  f"({match.template}, {match.score:.2f})")
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
            self.hotkey_status.set(err or f"{self.hotkey_var.get()} で ON/OFF")
        else:
            self.hotkey.stop()
            self.hotkey_status.set("ホットキー無効")
        self._save()

    def _apply_tray(self) -> None:
        if self.tray_var.get():
            err = self.tray.start(COLOR_OFF, "Auto Approve")
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
