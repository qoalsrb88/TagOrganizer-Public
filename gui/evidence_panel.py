"""편집 패널의 Danbooru 증거 칸 — 고른 태그 하나의 위키·다른 이름·함의·연관 태그(`tagorg evidence show` 와 같은 내용).

캐시(work/evidence/evidence.json, `tagorg evidence fetch` 와 같은 파일)에서 읽고, 없으면 "증거 받기"로 그 태그만 받아
캐시에 더한다(작업 스레드). 위키 본문과 태그 이름은 Danbooru 에서 온 외부 데이터라 HTML 로 해석하지 않는다
(평문 칸은 PlainText, 색을 입히는 칸은 escape).
"""

from __future__ import annotations

import html
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from tools import evidence

from . import jobs, ops, theme
from .store import Store

WIKI_CHARS = 1200                   # `tagorg evidence show` 의 기본값
NEEDED = {"wiki": "위키", "implies": "함의", "related": "연관 태그"}     # 다 있어야 받은 것(evidence show 와 같은 기준)
COSTUME = "official_alternate_costume"
STRONG, NOTABLE, FAINT = 0.7, 0.5, 0.3  # 지침 §7: 이벤트 이름 → clothes 는 의상 근거 0.7 이상, 일관된 연관은 0.5 이상
# 짧은 칸을 먼저, 긴 위키를 끝에(카드가 낮아도 연관 태그가 보이게)
FIELDS = (("related", "연관"), ("related_series", "작품·캐릭터"), ("implies", "함의"), ("other_names", "다른 이름"),
          ("wiki", "위키"))
SEP = "\u00a0· "                    # 가운뎃점 앞에서는 줄을 바꾸지 않는다
RELATED_TIP = ("이 태그가 붙은 게시물 중 그 태그도 붙은 비율. 1girl·solo 같은 공통 태그와 0.15 미만은 뺐습니다.\n"
               "강조색: official_alternate_costume, 이 DB 에서 clothes 로 분류된 태그 0.5 이상. 굵게: 0.7 이상"
               "(지침 §7 — 이벤트 이름은 특정 의상 근거 0.7 이상이면 clothes. hat·gloves 같은 일반 의류는 특정 의상이 아님).")

_lock = threading.Lock()            # 캐시 파일 읽기 → 받기 → 쓰기를 한 번에 하나씩(잇달아 받아도 앞의 것을 잃지 않게)


def _file_sig(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def fetch_one(name: str, path: Path, get: evidence.Getter, delay: float):
    """캐시 파일을 새로 읽어 name 의 빠진 칸만 받고 다시 쓴다(작업 스레드). → (캐시, 파일 표시, 받은 수, 실패 메시지)."""
    with _lock:
        cache = evidence.read_cache(path)
        log: List[str] = []
        try:
            stats = evidence.fetch([name], cache, delay=delay, get=get, log=log.append)
        finally:
            evidence.write_cache(cache, path)       # 중간에 예외가 나도 받은 데까지
        return cache, _file_sig(path), stats, log


def _pairs(text: str) -> List[Tuple[str, float]]:
    """summarize 의 'pencil_skirt:0.80 necktie:0.61' → [(이름, 비율)]. 이름의 ':' 는 두고 마지막 ':' 로 나눈다."""
    out = []
    for token in text.split():
        name, _, freq = token.rpartition(":")
        out.append((name, float(freq)))
    return out


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return w


def _readable(pairs: Sequence[Tuple[str, float]]) -> List[str]:
    return [f"{name}\u00a0{freq:.2f}" for name, freq in pairs]


class EvidencePanel(QWidget):
    """고른 태그 하나의 Danbooru 증거. 고른 태그가 없거나 여러 개면 안내만 보인다."""

    def __init__(self, store: Store, path: Path = ops.EVIDENCE_CACHE, get: evidence.Getter = evidence.http_get,
                 delay: float = 0.5, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.path, self.get, self.delay = Path(path), get, delay
        self.name: Optional[str] = None
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.load_error = ""
        self._sig: Any = ()                 # 마지막으로 읽은 캐시 파일(변경 시각·크기). CLI 가 받아 바뀌면 다시 읽는다
        self.fetching: set = set()          # 받는 중인 태그
        self.errors: Dict[str, str] = {}    # 태그 → 마지막으로 받다가 난 실패
        self._build()
        self.set_name(None)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE[0])
        head = QHBoxLayout()
        self.status = QLabel(textFormat=Qt.RichText, wordWrap=True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.status, 1)
        self.fetch_btn = theme.decorate(theme.ghost(QPushButton("증거 받기")), "download")
        self.fetch_btn.setToolTip("Danbooru 에서 이 태그의 위키·함의·연관 태그를 받아 캐시에 더합니다"
                                  "(tagorg evidence fetch 와 같은 work/evidence/evidence.json)")
        self.fetch_btn.clicked.connect(self.fetch)
        head.addWidget(self.fetch_btn)
        self.head = _wrap(head)
        layout.addWidget(self.head)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        form.setHorizontalSpacing(theme.SPACE[2])
        form.setVerticalSpacing(theme.SPACE[0])
        self.fields: Dict[str, QLabel] = {}
        for key, text in FIELDS:
            value = QLabel(textFormat=Qt.RichText if key == "related" else Qt.PlainText, wordWrap=True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.fields[key] = value
            form.addRow(theme.caption(text, "field"), value)
        self.fields["related"].setToolTip(RELATED_TIP)
        content = QWidget()
        content.setLayout(form)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.card = QFrame()
        self.card.setProperty("role", "card")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(theme.SPACE[2], theme.SPACE[1], theme.SPACE[2], theme.SPACE[1])
        card_layout.addWidget(scroll)
        layout.addWidget(self.card, 1)      # 긴 위키는 카드 안에서 스크롤
        layout.addStretch()                 # 카드를 숨겼을 때 안내 줄을 위에 붙인다

    # ---- 보이기 ----

    def set_name(self, name: Optional[str]) -> None:
        self.name = name
        self.render()

    def render(self) -> None:
        """지금 태그의 증거를 다시 그린다(캐시 파일이 바뀌었으면 다시 읽음). 테마를 바꾼 뒤에도 부른다."""
        name = self.name
        if name is None:
            self.fetch_btn.hide()
            self.card.hide()
            self.head.show()
            self.status.setText(theme.span("태그 하나를 고르면 Danbooru 위키·함의·연관 태그를 보여 줍니다.", "muted"))
            return
        self._load()
        entry = self.cache.get(name)
        fetching = name in self.fetching
        missing = [k for k in NEEDED if k not in (entry or {})]
        self.fetch_btn.setVisible(bool(missing) or fetching)
        self.fetch_btn.setEnabled(not fetching)
        self.fetch_btn.setText("받는 중…" if fetching else "나머지 받기" if entry else "증거 받기")
        status = self._status(name, entry, missing, fetching)
        self.status.setText(status)
        self.head.setVisible(bool(status))      # 다 받은 태그는 안내 줄 없이 카드만(받은 날짜는 카드 풍선 도움말)
        self.card.setVisible(entry is not None)
        if entry is not None:
            self.card.setToolTip(f"{entry.get('fetched', '')} 에 받음 · {self.path}")
            self._fill(entry)

    def _load(self) -> None:
        """처음이거나 마지막으로 읽은 뒤 캐시 파일이 바뀌었을 때만 다시 읽는다."""
        sig = _file_sig(self.path)
        if sig == self._sig:
            return
        try:
            self.cache, self.load_error = evidence.read_cache(self.path), ""
        except (OSError, ValueError) as e:
            self.cache, self.load_error = {}, f"캐시를 읽지 못했습니다: {e}"
        self._sig = sig

    def _status(self, name: str, entry: Optional[Mapping[str, Any]], missing: List[str], fetching: bool) -> str:
        if fetching:
            return theme.span("Danbooru 에서 받는 중…", "muted")
        if name in self.errors:
            return theme.span(html.escape(self.errors[name]), "danger")
        if self.load_error:
            return theme.span(html.escape(self.load_error), "danger")
        if entry is None:
            return theme.span("캐시에 없음", "muted")
        if missing:
            return theme.span("·".join(NEEDED[k] for k in missing) + " 안 받음", "warning")
        return ""

    def _fill(self, entry: Mapping[str, Any]) -> None:
        ev = evidence.summarize(entry, wiki_chars=WIKI_CHARS)
        got = {k: ev[k] != "?" for k in NEEDED}
        related = _pairs(ev["related"]) if got["related"] else []
        series = _pairs(ev["related_series"]) if got["related"] else []
        for key, text, have in (("wiki", ev["wiki"], got["wiki"]),
                                ("other_names", SEP.join(ev["other_names"].split("|")), got["wiki"]),
                                ("implies", SEP.join(ev["implies"].split("|")), got["implies"]),
                                ("related_series", SEP.join(_readable(series)), got["related"])):
            self._plain(self.fields[key], text if have else "", ("위키 없음" if key == "wiki" else "없음")
                        if have else "받지 않음")
        self.fields["related"].setText(self._related_html(related) if related else
                                       theme.span("없음" if got["related"] else "받지 않음", "muted"))

    @staticmethod
    def _plain(label: QLabel, text: str, empty: str) -> None:
        """평문 칸. 비었으면 흐린 글자로 empty(없음·받지 않음)."""
        label.setText(text or empty)
        role = "" if text else "muted"
        if (label.property("role") or "") != role:
            label.setProperty("role", role)
            theme.repolish(label)

    def _related_html(self, pairs: Sequence[Tuple[str, float]]) -> str:
        """연관 태그를 'pencil_skirt 0.80' 로 잇는다. 의상 근거(official_alternate_costume, 이 DB 의 clothes 0.5 이상)는
        강조색(0.7 이상은 굵게), 0.3 미만은 흐리게. 이름은 외부 데이터라 escape."""
        parts = []
        for (name, freq), text in zip(pairs, _readable(pairs)):
            text = html.escape(text)
            clothes = (self.store.row(name).get("primary") or "").partition("/")[0] == "clothes"
            if name == COSTUME or (clothes and freq >= NOTABLE):
                parts.append(theme.span(text, "accent", bold=freq >= STRONG))
            elif freq < FAINT:
                parts.append(theme.span(text, "muted"))
            else:
                parts.append(text)
        return SEP.join(parts)

    # ---- 받기 ----

    def fetch(self) -> None:
        """고른 태그의 빠진 증거를 작업 스레드에서 받는다. 끝나면 캐시를 바꿔 끼우고 다시 그린다."""
        name = self.name
        if name is None or name in self.fetching:
            return
        self.fetching.add(name)
        self.errors.pop(name, None)
        self.render()
        path, get, delay = self.path, self.get, self.delay
        jobs.run(lambda: fetch_one(name, path, get, delay),
                 lambda result: self._fetched(name, result),
                 lambda error: self._failed(name, f"받지 못했습니다: {error}"))

    def _fetched(self, name: str, result) -> None:
        self.cache, self._sig, stats, log = result
        self.load_error = ""
        self.fetching.discard(name)
        if stats["failed"]:
            self.errors[name] = "일부를 받지 못했습니다 — " + " / ".join(log)
        self.render()

    def _failed(self, name: str, message: str) -> None:
        self.fetching.discard(name)
        self.errors[name] = message
        self.render()
