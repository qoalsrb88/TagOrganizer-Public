"""백그라운드 작업 — 오래 걸리는 일(빌드·sync·import·미리보기 팩)을 스레드에서 돌리고, 결과는 화면 스레드로 넘긴다."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    # 화면 스레드에서 만든 객체라서, 작업 스레드가 보낸 신호는 화면 스레드에서 처리된다(큐 연결)
    finished = Signal(object)


_relay: Optional[_Relay] = None
active = 0          # 돌고 있는 작업 수(테스트가 기다릴 때 씀)


def run(fn: Callable[[], Any], done: Callable[[Any], None],
        failed: Optional[Callable[[BaseException], None]] = None) -> None:
    """fn 을 스레드에서 돌리고 화면 스레드에서 done(결과) 또는 failed(예외)를 부른다."""
    global _relay, active
    if _relay is None:
        _relay = _Relay()
        _relay.finished.connect(_deliver)
    relay = _relay
    active += 1

    def work() -> None:
        try:
            result, error = fn(), None
        except Exception as e:          # noqa: BLE001 — 화면에 그대로 보여 준다
            result, error = None, e
        relay.finished.emit((done, failed, result, error))

    threading.Thread(target=work, daemon=True).start()


def _deliver(payload) -> None:
    global active
    done, failed, result, error = payload
    active -= 1
    try:
        if error is None:
            done(result)
        elif failed is not None:
            failed(error)
        else:
            raise error
    except RuntimeError as e:
        # 창을 닫은 뒤 끝난 작업: 콜백의 위젯이 이미 지워졌다
        if "already deleted" not in str(e):
            raise
