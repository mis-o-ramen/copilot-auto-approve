"""Windows 用ランチャー: ダブルクリックでコンソールを出さずに GUI を起動する。

.venv があればその pythonw で起動し直す (依存パッケージを venv に入れた場合用)。
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
venv_pythonw = here / ".venv" / "Scripts" / "pythonw.exe"
if venv_pythonw.exists() and Path(sys.executable).resolve() != venv_pythonw.resolve():
    subprocess.Popen([str(venv_pythonw), str(here / "auto_approve_gui.py")], cwd=here)
    sys.exit(0)

sys.path.insert(0, str(here))
from auto_approve_gui import main  # noqa: E402

main()
