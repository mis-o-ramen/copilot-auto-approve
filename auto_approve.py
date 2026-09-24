"""画面上に「Allow」ボタンが表示されたら検知してクリックするスクリプト。

images/ ディレクトリに置いたボタン画像(テンプレート)を OpenCV のテンプレート
マッチングで画面から探し、見つかったらその中心をクリックする。

使い方の例:
    python auto_approve.py                     # images/*.png を監視してクリック
    python auto_approve.py --dry-run           # 検知のみ(クリックしない)
    python auto_approve.py --test-image ss.png # スクリーンショット画像で検知テスト

停止: Ctrl+C、またはマウスを画面の左上隅に移動 (pyautogui の FAILSAFE)。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("auto_approve")

DEFAULT_IMAGE_DIR = Path(__file__).resolve().parent / "images"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


@dataclass
class Template:
    name: str
    image: np.ndarray  # グレースケール


@dataclass
class Match:
    template: str
    score: float
    # 検索画像(スクリーンショット)上のピクセル座標
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def load_templates(paths: list[Path]) -> list[Template]:
    """画像ファイル、またはディレクトリ内の画像をテンプレートとして読み込む。"""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES))
        else:
            files.append(p)

    templates = []
    for f in files:
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"画像を読み込めません: {f}")
        templates.append(Template(name=f.name, image=img))
    return templates


def find_best_match(
    haystack_gray: np.ndarray,
    templates: list[Template],
    threshold: float,
    scales: list[float],
) -> Match | None:
    """全テンプレート・全スケールの中で最もスコアの高い一致を返す(閾値未満なら None)。"""
    best: Match | None = None
    hh, hw = haystack_gray.shape[:2]
    for tpl in templates:
        for scale in scales:
            if scale == 1.0:
                needle = tpl.image
            else:
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                needle = cv2.resize(tpl.image, None, fx=scale, fy=scale, interpolation=interp)
            th, tw = needle.shape[:2]
            if th > hh or tw > hw or th < 4 or tw < 4:
                continue
            result = cv2.matchTemplate(haystack_gray, needle, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(result)
            if score >= threshold and (best is None or score > best.score):
                best = Match(tpl.name, float(score), x, y, tw, th)
    return best


class ScreenGrabber:
    """mss でモニターを撮影し、クリック用の座標へ変換する。

    macOS の Retina など、撮影画像のピクセル数と OS の座標系(ポイント)が
    異なる環境でも正しい位置をクリックできるよう、倍率を自動計算する。
    """

    def __init__(self, monitor_index: int, region: tuple[int, int, int, int] | None):
        import mss

        # mss 10 以降は mss.MSS、それ以前は mss.mss
        self._sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        monitors = self._sct.monitors  # [0] は全モニター合成、[1..] は個別
        if not 0 <= monitor_index < len(monitors):
            raise ValueError(
                f"--monitor は 0〜{len(monitors) - 1} で指定してください (0 = 全モニター)"
            )
        mon = dict(monitors[monitor_index])
        if region:
            left, top, width, height = region
            mon = {"left": mon["left"] + left, "top": mon["top"] + top,
                   "width": width, "height": height}
        self.area = mon

    def grab(self) -> tuple[np.ndarray, float, float]:
        """グレースケール画像と (x倍率, y倍率) を返す。倍率 = 画像px / OS座標。"""
        shot = self._sct.grab(self.area)
        img = np.asarray(shot)  # BGRA
        gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        sx = shot.width / self.area["width"]
        sy = shot.height / self.area["height"]
        return gray, sx, sy

    def to_screen(self, px: int, py: int, sx: float, sy: float) -> tuple[int, int]:
        return round(self.area["left"] + px / sx), round(self.area["top"] + py / sy)


def enable_windows_dpi_awareness() -> None:
    """Windows の表示スケーリング(125% など)で座標がずれないようにする。"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def click(x: int, y: int, restore_mouse: bool) -> None:
    import pyautogui

    pyautogui.FAILSAFE = True  # マウスを左上隅に動かすと FailSafeException で停止
    pyautogui.PAUSE = 0.05
    original = pyautogui.position()
    pyautogui.click(x, y)
    if restore_mouse:
        pyautogui.moveTo(original.x, original.y)


@dataclass
class WatchConfig:
    threshold: float = 0.85
    interval: float = 1.0
    cooldown: float = 1.5
    scales: list[float] = field(default_factory=lambda: [1.0])
    monitor: int = 0
    region: tuple[int, int, int, int] | None = None
    dry_run: bool = False
    restore_mouse: bool = True


HitCallback = Callable[[Match, int, int, bool], None]


def watch(
    templates: list[Template],
    config: WatchConfig,
    stop: threading.Event,
    on_hit: HitCallback,
    grabber: ScreenGrabber | None = None,
    clicker: Callable[[int, int, bool], None] = click,
) -> None:
    """stop がセットされるまで画面を監視し、見つけたらクリックする。

    on_hit(match, x, y, clicked) は検知のたびに呼ばれる (x, y はクリック座標)。
    mss はスレッドごとに初期化が必要なため、grabber は呼び出したスレッド内で作る。
    """
    enable_windows_dpi_awareness()
    grabber = grabber or ScreenGrabber(config.monitor, config.region)
    while not stop.is_set():
        started = time.monotonic()
        gray, sx, sy = grabber.grab()
        match = find_best_match(gray, templates, config.threshold, config.scales)
        if match:
            x, y = grabber.to_screen(*match.center, sx, sy)
            if not config.dry_run:
                clicker(x, y, config.restore_mouse)
            on_hit(match, x, y, not config.dry_run)
            if not config.dry_run:
                # 同じボタンを連打しないよう、ボタンが消えるまで少し待つ
                stop.wait(config.cooldown)
                continue
        stop.wait(max(0.0, config.interval - (time.monotonic() - started)))


def is_failsafe(e: BaseException) -> bool:
    """pyautogui.FailSafeException か (pyautogui を import せずに判定)。"""
    return type(e).__name__ == "FailSafeException"


def run_test_image(args: argparse.Namespace, templates: list[Template]) -> int:
    img = cv2.imread(str(args.test_image), cv2.IMREAD_GRAYSCALE)
    if img is None:
        log.error("画像を読み込めません: %s", args.test_image)
        return 2
    match = find_best_match(img, templates, args.threshold, args.scales)
    if match is None:
        print("見つかりませんでした")
        return 1
    print(f"検知: {match.template} score={match.score:.3f} "
          f"box=({match.x}, {match.y}, {match.w}, {match.h}) center={match.center}")
    return 0


def run_watch(args: argparse.Namespace, templates: list[Template]) -> int:
    config = WatchConfig(
        threshold=args.threshold, interval=args.interval, cooldown=args.cooldown,
        scales=args.scales, monitor=args.monitor, region=args.region,
        dry_run=args.dry_run, restore_mouse=not args.no_restore_mouse,
    )
    log.info("監視開始: テンプレート=%s 閾値=%.2f 間隔=%.1fs%s",
             [t.name for t in templates], config.threshold, config.interval,
             " (dry-run)" if config.dry_run else "")

    stop = threading.Event()
    clicks = 0

    def on_hit(match: Match, x: int, y: int, clicked: bool) -> None:
        nonlocal clicks
        if clicked:
            clicks += 1
            log.info("クリック #%d: %s score=%.3f -> (%d, %d)",
                     clicks, match.template, match.score, x, y)
        else:
            log.info("検知: %s score=%.3f -> (%d, %d) [クリックせず]",
                     match.template, match.score, x, y)
        if args.once:
            stop.set()

    watch(templates, config, stop, on_hit)
    return 0


def parse_region(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(v) for v in value.split(","))
    except ValueError:
        parts = ()
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("left,top,width,height の形式で指定してください")
    return parts  # type: ignore[return-value]


def parse_scales(value: str) -> list[float]:
    try:
        scales = [float(v) for v in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("例: 1.0 または 0.8,1.0,1.25")
    if not scales or any(s <= 0 for s in scales):
        raise argparse.ArgumentTypeError("倍率は正の数で指定してください")
    return scales


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="画面上の Allow ボタンを検知して自動クリックします")
    p.add_argument("templates", nargs="*", type=Path, default=[DEFAULT_IMAGE_DIR],
                   help="ボタン画像ファイルまたはディレクトリ (既定: images/)")
    p.add_argument("-t", "--threshold", type=float, default=0.85,
                   help="一致とみなすスコア 0〜1 (既定: 0.85)")
    p.add_argument("-i", "--interval", type=float, default=1.0,
                   help="画面チェックの間隔 秒 (既定: 1.0)")
    p.add_argument("--cooldown", type=float, default=1.5,
                   help="クリック後に待つ秒数 (既定: 1.5)")
    p.add_argument("--scales", type=parse_scales, default=[1.0],
                   help="テンプレートの拡大率 カンマ区切り (既定: 1.0, 例: 0.8,1.0,1.25)")
    p.add_argument("--monitor", type=int, default=0,
                   help="対象モニター番号 0=全モニター, 1=メイン... (既定: 0)")
    p.add_argument("--region", type=parse_region,
                   help="モニター内の検索範囲 left,top,width,height (高速化・誤検知防止)")
    p.add_argument("--dry-run", action="store_true", help="検知のみ行いクリックしない")
    p.add_argument("--once", action="store_true", help="1回クリックしたら終了")
    p.add_argument("--no-restore-mouse", action="store_true",
                   help="クリック後にマウスを元の位置へ戻さない")
    p.add_argument("--test-image", type=Path,
                   help="画面の代わりに画像ファイルで検知テストして終了")
    p.add_argument("-v", "--verbose", action="store_true", help="詳細ログ")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    try:
        templates = load_templates(args.templates)
    except (ValueError, FileNotFoundError) as e:
        log.error("%s", e)
        return 2
    if not templates:
        log.error("ボタン画像がありません。images/ に Allow ボタンの画像 (PNG) を置いてください")
        return 2

    if args.test_image:
        return run_test_image(args, templates)

    try:
        return run_watch(args, templates)
    except KeyboardInterrupt:
        log.info("停止しました")
        return 0
    except Exception as e:
        if is_failsafe(e):
            log.info("FAILSAFE: マウスが画面隅に移動したため停止しました")
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
