import threading

import numpy as np

import auto_approve as aa
from tests.test_detector import make_button, make_screen


class FakeGrabber:
    """スクリーンショットの代わりに固定画像を返す。倍率 2 (Retina 相当)。"""

    def __init__(self, screen):
        self.screen = screen
        self.area = {"left": 100, "top": 50}

    def grab(self):
        return self.screen, 2.0, 2.0

    def to_screen(self, px, py, sx, sy):
        return aa.ScreenGrabber.to_screen(self, px, py, sx, sy)


def run(config, screen, max_hits=2):
    stop = threading.Event()
    hits, clicks = [], []

    def on_hit(match, x, y, clicked):
        hits.append((x, y, clicked))
        if len(hits) >= max_hits:
            stop.set()

    t = threading.Thread(target=aa.watch, args=([aa.Template("a", make_button())], config, stop,
                                                on_hit, FakeGrabber(screen),
                                                lambda x, y, r: clicks.append((x, y))))
    t.start()
    t.join(timeout=3)
    stop.set()
    t.join()
    return hits, clicks


def test_watch_clicks_with_scaled_coordinates():
    cfg = aa.WatchConfig(interval=0.01, cooldown=0.01)
    hits, clicks = run(cfg, make_screen(make_button()))
    # ボタン中心 (336, 164) px -> 倍率 2 で (168, 82) + 領域オフセット (100, 50)
    assert clicks == [(268, 132), (268, 132)]
    assert all(c for _, _, c in hits)


def test_watch_dry_run_does_not_click():
    cfg = aa.WatchConfig(interval=0.01, dry_run=True)
    hits, clicks = run(cfg, make_screen(make_button()))
    assert clicks == [] and len(hits) == 2 and not any(c for _, _, c in hits)


def test_watch_stops_promptly_when_idle():
    stop = threading.Event()
    cfg = aa.WatchConfig(interval=5)
    t = threading.Thread(target=aa.watch, args=([aa.Template("a", make_button())], cfg, stop,
                                                lambda *a: None, FakeGrabber(make_screen(None))))
    t.start()
    stop.set()
    t.join(timeout=1)
    assert not t.is_alive()
