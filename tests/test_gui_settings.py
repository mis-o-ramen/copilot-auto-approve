import json

import pytest

gui = pytest.importorskip("auto_approve_gui")  # tkinter が無い環境ではスキップ


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = gui.Settings(threshold=0.9, auto_off="30分", always_on_top=False)
    s.save(path)
    assert gui.Settings.load(path) == s


def test_settings_ignores_unknown_and_broken(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"threshold": 0.7, "unknown": 1}), encoding="utf-8")
    assert gui.Settings.load(path).threshold == 0.7
    path.write_text("{broken", encoding="utf-8")
    assert gui.Settings.load(path) == gui.Settings()


def test_fmt_duration():
    assert gui.fmt_duration(65) == "1:05"
    assert gui.fmt_duration(3725) == "1:02:05"
    assert gui.fmt_duration(-3) == "0:00"
