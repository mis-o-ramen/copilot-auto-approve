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

# pynput / pystray は実行時に OS 別のバックエンドを import する。collect-submodules は
# ビルド時に import できたものしか拾わない (例: ディスプレイの無い環境の _xorg) ため明示する
BACKEND = {"win32": "_win32", "darwin": "_darwin"}.get(sys.platform, "_xorg")
HIDDEN_IMPORTS = [
    f"pynput.keyboard.{BACKEND}",
    f"pynput.mouse.{BACKEND}",
    f"pynput._util.{BACKEND.lstrip('_')}",
    f"pystray.{BACKEND}",
]


def main() -> None:
    # Windows でリダイレクト/CI 実行時は cp1252 などになり日本語の print が失敗するため
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    PyInstaller.__main__.run([
        str(ROOT / "auto_approve_gui.py"),
        "--name", NAME,
        "--windowed",  # コンソールを出さない (macOS は .app を作る)
        "--noconfirm",
        "--clean",
        # OS ごとのバックエンドを動的 import しているため明示的に同梱する
        "--collect-submodules", "pynput",
        "--collect-submodules", "pystray",
        "--collect-data", "sv_ttk",  # テーマの tcl / 画像ファイル
        *[arg for mod in HIDDEN_IMPORTS for arg in ("--hidden-import", mod)],
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
