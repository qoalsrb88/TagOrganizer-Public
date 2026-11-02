"""관리 툴을 띄우기 전 점검 — PySide6 가 없거나 낡았으면 지금 실행 중인 Python 에 설치를 제안한다.

PySide6 없이 돌아야 하므로 표준 라이브러리만 쓴다. 다른 PC 에서 처음 받아 실행할 때 흔한 경우:
- `tagorg_gui.pyw` 더블클릭은 py 런처의 기본 Python 으로 뜬다. PATH 의 `pip` 가 다른 Python 을 가리키면
  `pip install` 을 해도 계속 없다고 나온다. 그래서 설치는 언제나 이 Python 으로(`<sys.executable> -m pip`) 한다.
- 설치는 됐는데 Qt 를 못 불러옴(설치가 깨짐): 이미 이 프로세스가 일부 DLL 을 잡고 있어(Windows) 다시 설치가
  실패하므로 자동으로 하지 않고 명령만 알린다.
설치한 뒤에는 새 프로세스로 다시 띄운다(새로 생긴 user site 경로를 지금 프로세스는 모른다).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements-gui.txt"
INSTALL_LOG = ROOT / "local" / "gui-install.log"     # pip --log (git 제외 폴더)
RETRY_ENV = "TAGORG_GUI_INSTALLED"                    # 설치 뒤 다시 띄운 프로세스: 또 설치를 묻지 않는다
QT_MODULES = ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtSvg")
DISTRIBUTIONS = ("PySide6-Essentials", "PySide6")     # 버전을 읽을 배포 이름(Essentials 가 실제 모듈을 담는다)
TITLE = "TagOrganizer"


@dataclass(frozen=True)
class Problem:
    kind: str       # missing | old | broken
    detail: str


def required_version(path: Path = REQUIREMENTS) -> Tuple[int, ...]:
    """requirements-gui.txt 의 `PySide6…>=X.Y` 에서 최소 버전. 없으면 ()."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        m = re.match(r"\s*pyside6(?:[-_]essentials)?\s*>=\s*([\d.]+)", line, re.I)
        if m:
            return tuple(int(x) for x in m.group(1).split(".") if x)
    return ()


def installed_version() -> Optional[Tuple[int, ...]]:
    """설치 정보(metadata)에서 읽는다 — PySide6 를 import 하지 않아야 낡은 판을 다시 설치할 수 있다."""
    for dist in DISTRIBUTIONS:
        try:
            text = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
        return tuple(int(x) for x in re.findall(r"\d+", text)[:3])
    return None


def check(importer: Callable[[str], object] = importlib.import_module) -> Optional[Problem]:
    """None 이면 바로 띄울 수 있다."""
    if importlib.util.find_spec("PySide6") is None:
        return Problem("missing", "PySide6 가 설치되어 있지 않습니다.")
    need, have = required_version(), installed_version()
    if need and have and have[:len(need)] < need:
        return Problem("old", f"PySide6 {_dotted(have)} 이 설치되어 있습니다(필요: {_dotted(need)} 이상).")
    try:
        for name in QT_MODULES:
            importer(name)
    except ImportError as e:
        return Problem("broken", f"PySide6 를 불러오지 못했습니다: {e}")
    return None


def console_python() -> str:
    """pythonw.exe 대신 옆의 python.exe — 콘솔 창에 pip 진행 상황이 보이게."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def install_command(problem: Problem, python: Optional[str] = None) -> List[str]:
    cmd = [python or console_python(), "-m", "pip", "install"]
    if problem.kind == "broken":
        cmd += ["--upgrade", "--force-reinstall"]
    return cmd + ["-r", str(REQUIREMENTS)]


def ensure(dialog: bool, relaunch: Optional[Sequence[str]] = None) -> Optional[int]:
    """None 이면 GUI 를 띄워도 된다. 정수면 그 코드로 끝낸다(설치 뒤 다시 띄운 GUI 의 종료 코드 포함).

    dialog=True 는 콘솔 없는 실행(.pyw): 메시지 창으로 묻고, pip 는 새 콘솔 창에서 돈다.
    """
    problem = check()
    if problem is None:
        return None
    manual = _quote(install_command(problem))
    where = f"Python: {sys.executable} ({_dotted(sys.version_info[:3])})"
    if os.environ.get(RETRY_ENV):
        _tell(dialog, f"설치했지만 아직 쓸 수 없습니다.\n{problem.detail}\n\n{where}\n\n직접 설치:\n{manual}")
        return 1
    if problem.kind == "broken":
        _tell(dialog, f"{problem.detail}\n\n{where}\n\n{'이 창을 닫은 뒤 ' if dialog else ''}명령 창에서 다시 설치하세요:\n{manual}")
        return 1
    question = (f"{problem.detail}\n\n{where}\n"
                "다른 Python 에 설치되어 있어도 이 Python 에는 따로 설치해야 합니다.\n\n"
                f"지금 이 Python 에 설치할까요?{' (콘솔 창에 진행 상황이 보입니다)' if dialog else ''}\n\n"
                f"직접 설치:\n{manual}")
    if not _ask(dialog, question):
        return 1
    ok, errors = install(problem, new_console=dialog)
    if not ok:
        shown = "\n".join(errors[-6:]) or "(오류 내용 없음)"
        _tell(dialog, f"설치하지 못했습니다.\n\n{shown}\n\n전체 기록: {INSTALL_LOG}\n\n{where}\n\n직접 설치:\n{manual}")
        return 1
    argv = list(relaunch) if relaunch is not None else [sys.executable, *sys.argv]
    return subprocess.call(argv, env={**os.environ, RETRY_ENV: "1"})


def install(problem: Problem, new_console: bool) -> Tuple[bool, List[str]]:
    """pip 로 설치. (성공, 오류 줄). pip 가 없으면 ensurepip 로 먼저 깐다."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if new_console else 0
    python = console_python()
    try:
        INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        INSTALL_LOG.unlink(missing_ok=True)             # pip --log 는 이어 쓴다: 이번 실행만 남긴다
        if importlib.util.find_spec("pip") is None:
            if subprocess.call([python, "-m", "ensurepip", "--upgrade"], creationflags=flags) != 0:
                return False, ["이 Python 에 pip 가 없고 ensurepip 로도 설치하지 못했습니다."]
        code = subprocess.call(install_command(problem, python) + ["--log", str(INSTALL_LOG)], creationflags=flags)
    except OSError as e:                                # 로그 폴더 권한, python 실행 실패 등
        return False, [f"설치를 시작하지 못했습니다: {e}"]
    if code == 0:
        return True, []
    return False, _log_errors() or [f"pip 종료 코드 {code}"]


def report_crash(exc: BaseException) -> None:
    """콘솔 없는 실행에서 시작하다 죽으면 아무것도 안 보이므로 창으로 알리고 기록을 남긴다."""
    import traceback
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log = ROOT / "local" / "gui-error.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
        saved = f"\n\n전체 기록: {log}"
    except OSError:
        saved = ""
    _tell(True, f"관리 툴을 시작하지 못했습니다.\n\n{text[-1500:]}{saved}")


def _log_errors() -> List[str]:
    try:
        lines = INSTALL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found = [line[line.index("ERROR:"):] for line in lines if "ERROR:" in line]     # 앞의 시각을 뗀다
    found = [x if len(x) <= 300 else x[:300] + "…" for x in found]     # 버전 목록 줄은 아주 길다
    return [x for i, x in enumerate(found) if x not in found[:i]]


def _dotted(version: Sequence[int]) -> str:
    return ".".join(str(x) for x in version)


def _quote(cmd: Sequence[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else shlex.join(cmd)


def _ask(dialog: bool, text: str) -> bool:
    if dialog:
        return _message_box(text, question=True)
    print(text, file=sys.stderr)
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input("설치할까요? [y/N] ").strip().lower() in ("y", "yes", "예", "ㅛ")
    except EOFError:
        return False


def _tell(dialog: bool, text: str) -> None:
    if dialog:
        _message_box(text, question=False)
    else:
        print(text, file=sys.stderr)


def _message_box(text: str, question: bool) -> bool:
    if os.name == "nt":
        import ctypes
        style = (0x04 | 0x20) if question else 0x10    # MB_YESNO|MB_ICONQUESTION / MB_ICONERROR
        return ctypes.windll.user32.MessageBoxW(None, text, TITLE, style | 0x10000) == 6   # MB_SETFOREGROUND, IDYES
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        try:
            if question:
                return bool(messagebox.askyesno(TITLE, text))
            messagebox.showerror(TITLE, text)
            return False
        finally:
            root.destroy()
    except Exception:
        print(text, file=sys.stderr)
        return False
