"""PyInstaller で配布用アプリをビルドする。

    pip install -r requirements.txt pyinstaller
    python build.py

出力 (Python が入っていない PC でもそのまま動く):
    Windows: dist/AutoApprove/AutoApprove.exe  (フォルダごと配布)
    macOS:   dist/AutoApprove.app
    ボタン画像は実行ファイル (macOS は .app) と同じ場所の images/ に置く。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
NAME = "AutoApprove"


def main() -> None:
    PyInstaller.__main__.run([
        str(ROOT / "auto_approve_gui.py"),
        "--name", NAME,
        "--windowed",  # コンソールを出さない (macOS は .app を作る)
        "--noconfirm",
        "--clean",
        # OS ごとのバックエンドを動的 import しているため明示的に同梱する
        "--collect-submodules", "pynput",
        "--collect-submodules", "pystray",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ])

    # auto_approve.app_dir() と同じ規則で images/ を配置する
    if sys.platform == "darwin":
        app_dir = DIST
    else:
        app_dir = DIST / NAME
    images = app_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "images" / "README.md", images / "README.md")
    for f in (ROOT / "images").iterdir():
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            shutil.copy2(f, images / f.name)
    print(f"\nビルド完了: {app_dir}\nボタン画像の置き場所: {images}")


if __name__ == "__main__":
    main()
