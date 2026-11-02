"""TagOrganizer 관리 툴(GUI) 실행 파일 — 더블클릭하면 콘솔 없이 뜬다. `python tagorg.py gui` 와 같다.

PySide6 가 필요하다(requirements-gui.txt). 없거나 낡았으면 이 파일을 연 Python 에 설치할지 묻는다(gui/bootstrap.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui import bootstrap  # noqa: E402 — 표준 라이브러리만 쓴다

try:
    code = bootstrap.ensure(dialog=True)
    if code is None:
        from gui.app import main
        code = main()
except Exception as e:
    # pythonw 는 콘솔이 없어 오류가 보이지 않으므로 창으로 알린다(SystemExit·KeyboardInterrupt 는 그대로)
    bootstrap.report_crash(e)
    code = 1
sys.exit(code)
