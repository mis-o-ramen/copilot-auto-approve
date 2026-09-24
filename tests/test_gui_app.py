"""App の結合テスト。tkinter とディスプレイが無い環境ではスキップ。"""
import os
import sys

import pytest

tk = pytest.importorskip("tkinter")
gui = pytest.importorskip("auto_approve_gui")
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("DISPLAY がありません", allow_module_level=True)


class FakeTray:
    def __init__(self):
        self.active = False
        self.updates = []

    def start(self, color, title):
        self.active = True

    def update(self, color, title, running):
        assert title.isascii()
        self.updates.append((title, running))

    def stop(self):
        self.active = False


@pytest.fixture(scope="module")
def root():
    # Windows の CI では Tk() を何度も作り直すと tk.tcl の読み込みに失敗することがあるため 1 つを共有する
    r = tk.Tk()
    yield r
    r.destroy()


@pytest.fixture
def app(root, monkeypatch):
    monkeypatch.setattr(gui.Settings, "save", lambda self, path=None: None)
    monkeypatch.setattr(gui, "TrayIcon", lambda **kw: FakeTray())
    a = gui.App(root, gui.Settings(hotkey_enabled=False, tray_enabled=True, close_to_tray=True))
    yield a
    # 次のテスト用に root を空に戻す
    for after_id in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(after_id)
    for child in root.winfo_children():
        child.destroy()
    root.deiconify()


def test_close_to_tray_and_restore(app):
    assert app.tray.active
    app.on_close()
    app.root.update()
    assert app.root.state() == "withdrawn"
    app._handle(("show",))
    app.root.update()
    assert app.root.state() == "normal"


def test_tray_toggle_without_images_reports_error(app, monkeypatch, tmp_path):
    monkeypatch.setattr(gui.core, "DEFAULT_IMAGE_DIR", tmp_path)
    app._handle(("hotkey",))  # トレイの ON/OFF もホットキーと同じイベント
    assert not app.running and "画像" in app.error
    app._render()
    assert app.tray.updates[-1] == ("Auto Approve: Error", False)


def test_wait_event_shows_waiting(app):
    app.running, app.generation = True, 1
    app._handle(("wait", 1, gui.core.Match("allow.png", 0.9, 0, 0, 1, 1)))
    app._render()
    assert app.status_var.get() == "操作待ち"
    assert app.tray.updates[-1][0] == "Auto Approve: Waiting (user active)"


def test_theme_switch_recolors_labels(app):
    app.theme_var.set("ダーク")
    app._on_theme_change()
    dark = app.theme.palette
    app.theme_var.set("ライト")
    app._on_theme_change()
    light = app.theme.palette
    assert dark != light
    fg = {str(w.cget("foreground")) for w, kind in app.theme._labels if kind == "muted"}
    assert fg == {light.muted}


def test_switch_follows_running_state(app):
    app.running = True
    app._render()
    assert app.switch._on
    app.running = False
    app._render()
    assert not app.switch._on
