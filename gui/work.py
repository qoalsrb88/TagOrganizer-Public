"""미리보기·작업·통계 탭.

- 미리보기: 프롬프트를 지금 편집 상태(메모리 팩)로 정리해 본다(`tagorg organize` 와 같은 옵션). 토큰을 고르면 편집 패널에 열린다.
  배열 세팅 패널(gui/arrange_panel.py)로 청크 배치·크롭/시점/시선 덮어쓰기·팩에 없는 토큰 추정을 바꾸며 색 미리보기로 본다.
- 작업: 빌드(`build`), 노드 팩 반영(`sync`, 미리보기 먼저), 원본 가져오기(`import`, 미리보기 먼저).
- 통계: `stats` 의 마일스톤별 커버리지와 분포.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import bmk_tag_organizer_core as core
from tools import builder, sync, tagdb

from . import jobs, ops, theme
from .arrange_panel import ArrangementPanel
from .store import Store

SAMPLE = "1girl, hatsune miku, upper body, shoes, smile"


def _section(layout, title: str, desc: str, first: bool = False) -> None:
    """작업 탭의 묶음 머리: 구분선 · 제목 · 한 줄 설명(무엇을 쓰는지와 같은 CLI 명령)."""
    if not first:
        layout.addSpacing(theme.SPACE[1])
        line = QFrame()
        line.setProperty("role", "divider")
        layout.addWidget(line)
    layout.addWidget(theme.caption(title, "title"))
    note = theme.caption(desc, "muted")
    note.setWordWrap(True)
    layout.addWidget(note)


def _form(layout) -> QFormLayout:
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setHorizontalSpacing(theme.SPACE[2])
    form.setVerticalSpacing(theme.SPACE[1])
    layout.addLayout(form)
    return form


def _buttons(*buttons) -> QHBoxLayout:
    row = QHBoxLayout()
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    return row


def _mono(widget) -> None:
    # 한글도 고정폭인 글꼴을 먼저(stats 출력의 열 맞춤). 없으면 시스템 고정폭 글꼴.
    # 테마 스타일시트의 전체 글꼴 규칙이 setFont 를 덮으므로 속성으로도 표시한다(QPlainTextEdit[mono="true"])
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setFamilies(["D2Coding", "GulimChe", "굴림체", font.family()])
    widget.setFont(font)
    widget.setProperty("mono", True)


def _file_sig(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _combo(items, current=None) -> QComboBox:
    combo = QComboBox()
    for text, data in items:
        combo.addItem(text, data)
    if current is not None:
        combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


FILTERS = (("all", "전체"), ("unknown", "팩에 없음"), ("guess", "추정"), ("removed", "뺌"), ("flagged", "표시만"),
           ("etc", "보류"), ("syntax", "문법·주석·문장"))
SYNTAX_KINDS = ("special", "comment", "natural")
DIALECT_NAMES = {"nai": "NAI", "comfyui": "ComfyUI", "anima": "Anima"}


def _matches(row, key: str) -> bool:
    if key == "unknown":
        return row["kind"] == "unknown"
    if key == "guess":
        return bool(row.get("guess"))
    if key == "removed":
        return bool(row.get("removed"))
    if key == "flagged":
        return bool(row.get("flagged"))
    if key == "etc":
        return row["primary"] == "etc"
    if key == "syntax":
        return row["kind"] in SYNTAX_KINDS
    return True


def _priorities() -> List[tuple]:
    """자동 인식 우선순위 콤보 항목: 세 문법의 순서 6가지(기본 nai > comfyui > anima 가 처음)."""
    from itertools import permutations
    orders = [core.DIALECT_PRIORITY] + [o for o in permutations(core.DIALECT_PRIORITY) if o != core.DIALECT_PRIORITY]
    return [("자동 우선: " + " > ".join(DIALECT_NAMES[d] for d in o), ",".join(o)) for o in orders]


def dialect_status(dialect: dict) -> str:
    """report["dialect"] → 상태 줄 조각(HTML). 자동이면 알아낸 문법과 근거, 표기가 섞였으면 경고색."""
    if not dialect.get("auto"):
        return ""
    name = DIALECT_NAMES.get(dialect["input"], dialect["input"])
    evidence = dialect.get("evidence") or {}
    counts = dialect.get("counts") or {}
    signs = [f"{DIALECT_NAMES.get(d, d)} ‘{html.escape(v[0])}’"
             + (f" 외 {counts.get(d, len(v)) - 1}" if counts.get(d, len(v)) > 1 else "")
             for d, v in evidence.items() if v]
    why = " — " + ", ".join(signs) if signs else " — 표기 없음(우선순위 첫 문법)"
    text = f" · 문법 자동 → <b>{name}</b>{why}"
    if dialect.get("conflict"):
        return theme.span(text + " · 표기가 섞여 우선순위로 정함", "warning")
    return text


def _blend(color: str, base: str, alpha: float) -> str:
    """색을 바탕색에 alpha 만큼 섞은 불투명 색(색 미리보기 바탕 — 리치 텍스트는 반투명 바탕을 못 그린다)."""
    c, b = QColor(color), QColor(base)
    mix = [round(x * alpha + y * (1 - alpha)) for x, y in zip(c.getRgb()[:3], b.getRgb()[:3])]
    return QColor(*mix).name()


def _token_html(row, index: int, t, custom: bool = False) -> str:
    """토큰 하나. 커스텀 청크에 직접 넣은 토큰은 팩에 없어도 빨강으로 칠하지 않는다(사용자가 자리를 정함)."""
    text = html.escape(row.get("out") or row["text"])
    if row.get("removed"):
        style = [f"color:{t['disabled']}", "text-decoration:line-through"]
    elif row.get("flagged"):
        style = [f"color:{t['warning']}", "text-decoration:underline"]
    elif row["kind"] == "unknown" and not row.get("guess") and not custom:
        style = [f"color:{t['danger']}"]
    elif row["kind"] == "comment":
        style = [f"color:{t['muted']}", "font-style:italic"]
    else:
        style = [f"color:{t['fg']}"]
    if row.get("warning"):
        style.append(f"border-bottom:1px dotted {t['warning']}")
    if row.get("guess"):
        style.append("font-style:italic")
    if row.get("injected"):
        style.append("font-weight:600")
    return f"<a href='tok:{index}' style='text-decoration:none'><span style='{';'.join(style)}'>{text}</span></a>"


def rich_html(report, show_removed: bool = True) -> str:
    """report["arranged"] → 색 미리보기 HTML. 줄마다 청크를 옅은 분류 색 바탕으로, 뺀 태그는 제자리에 흐리게·취소선.

    줄 끝 쉼표는 그리지 않는다(정확한 출력은 텍스트 탭).
    """
    t = theme.tokens()
    alpha = 0.30 if theme.current() == "dark" else 0.16
    rows = report["tokens"]
    lines = []
    for si, seg in enumerate(report.get("arranged", ())):
        if si:
            lines.append(f"<span style='color:{t['muted']}'>BREAK</span>")
        gap = drawn = False
        for row in seg["rows"]:
            if not row:                                  # 빈 줄 자리: 앞뒤에 그릴 줄이 있을 때만
                gap = True
                continue
            parts = []
            for chunk in row:
                shown = [i for i in chunk["tokens"] if show_removed or not rows[i].get("removed")]
                if shown:
                    # 주석은 한 줄에 하나, 자연어 문장 뒤는 쉼표 대신 빈칸(텍스트 탭의 출력과 같게)
                    joined = ""
                    for k, i in enumerate(shown):
                        if k:
                            prev = rows[shown[k - 1]]["kind"]
                            joined += "<br>" if prev == "comment" else " " if prev == "natural" else ", "
                        joined += _token_html(rows[i], i, t, chunk.get("custom", False))
                    bg = _blend(chunk.get("color") or t["muted"], t["surface"], alpha)
                    parts.append(f"<span style='background-color:{bg}'>&nbsp;{joined}&nbsp;</span>")
            if parts:
                if gap and drawn:
                    lines.append("")
                lines.append(" ".join(parts))
                gap, drawn = False, True
    return "<div style='line-height:165%'>" + "<br>".join(lines) + "</div>"


class PreviewPage(QWidget):
    """지금 편집 상태로 프롬프트를 정리해 본다. 팩은 메모리에서 만들고(0.9초 남짓), 분류를 고치면 다시 만든다.

    배열 세팅 패널(청크·크롭/시점/시선 덮어쓰기·추정)을 바꾸면 바로 다시 정리한다. 색 미리보기는 남는 태그를 밝게,
    빼는 태그를 제자리에 흐리게 보여 준다. 입력·배열·결과·토큰 표 사이 경계선을 끌어 높이를 바꾼다(기억함).
    """

    open_names = Signal(list)
    add_extra_requested = Signal(list)      # 팩에 없는 토큰을 확장 DB 에 새 태그로(창이 대화상자를 띄움)

    def __init__(self, store: Store, env: ops.Env, parent=None, settings=None) -> None:
        super().__init__(parent)
        self.store = store
        self.env = env
        self.settings = settings
        self.pack: Optional[core.TagPack] = None
        self.pack_rev: Optional[int] = None
        self.building = False
        self.report: Optional[dict] = None
        self._rows: List[dict] = []
        self.filter = "all"
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.run)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[1], 0)
        layout.setSpacing(theme.SPACE[1])

        presets = self.store.config["presets"]
        self.preset = _combo([(p.get("label", pid), pid) for pid, p in presets.items()], "character_swap")
        self.max_rating = theme.rating_items(_combo([(f"최대 {theme.rating_name(r)}", r) for r in core.RATINGS], "e"))
        self.rules = _combo([("구도 규칙: 뺌", "remove"), ("구도 규칙: 표시만", "report"), ("구도 규칙: 끔", "off")])
        self.dialect = _combo([("입력: 자동", core.AUTO_DIALECT)] + [(f"입력 {d}", d) for d in core.DIALECTS],
                              core.AUTO_DIALECT)
        self.dialect.setToolTip("자동: 프롬프트의 표기로 문법을 고릅니다 — NAI 1.2::x ::·{x}, ComfyUI (x:1.2)·\\(, "
                                "Anima @작가. 표기가 섞였거나 없으면 옆의 우선순위로")
        self.priority = _combo(_priorities())
        self.priority.setToolTip("자동 인식에서 NAI 와 ComfyUI·Anima 표기가 함께 있을 때, 그리고 표기가 없을 때 앞의 문법을 "
                                 "씁니다. Anima 는 ComfyUI 의 상위 호환이라 @작가가 있으면 Anima 입니다")
        self.out_dialect = _combo([("출력: 입력과 같음", "")] + [(f"출력 {d}", d) for d in core.DIALECTS])
        self.order = _combo([("순서: 입력", "input"), ("순서: 사용량", "popular"), ("순서: 가나다", "alpha")])
        self.one_line = QCheckBox("한 줄로")
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("추가로 뺄 선택자 (예: subject/character, body/hair)")
        self.comments = _combo([("주석(//): 뺌", "remove"), ("주석(//): 남김", "keep")])
        self.comments.setToolTip("// 부터 줄 끝까지. 남기면 구간 맨 위에 한 줄씩 둡니다(같은 줄에 두면 뒤 태그까지 주석이 됨)")
        self.sentences = QCheckBox("마침표로 문장 나누기")
        self.sentences.setChecked(True)
        self.sentences.setToolTip("문장 끝 마침표(. 뒤 빈칸)를 쉼표처럼 나누고, 마침표로 끝나는 문장은 자연어 청크에 입력 "
                                  "그대로 둡니다. st. louis·monkey d. luffy 처럼 마침표가 든 태그는 그대로")
        bar = QHBoxLayout()
        for w in (self.preset, self.max_rating, self.rules, self.dialect, self.priority, self.out_dialect, self.order,
                  self.one_line):
            bar.addWidget(w)
            signal = w.toggled if isinstance(w, QCheckBox) else w.currentIndexChanged
            signal.connect(lambda *_: self._timer.start())
        bar.addStretch(1)
        layout.addLayout(bar)
        self.dialect.currentIndexChanged.connect(self._dialect_changed)
        self._dialect_changed()
        second = QHBoxLayout()
        second.addWidget(self.exclude, 1)
        for w in (self.comments, self.sentences):
            second.addWidget(w)
            signal = w.toggled if isinstance(w, QCheckBox) else w.currentIndexChanged
            signal.connect(lambda *_: self._timer.start())
        self.exclude.textChanged.connect(self._timer.start)
        layout.addLayout(second)
        self.status = theme.caption("", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.split = QSplitter(Qt.Vertical)
        self.input = QPlainTextEdit(SAMPLE)
        self.input.setPlaceholderText("정리할 프롬프트 — 아래 경계선을 끌어 높이를 바꿉니다")
        self.input.setMinimumHeight(44)
        self.input.textChanged.connect(self._timer.start)
        self.split.addWidget(self.input)

        # 가운데: 배열 조작(위) + 청크 화면 | 결과(색 미리보기·텍스트·뺀 태그)
        self.arrange = ArrangementPanel(self.store.config, self.env.arrangements_dir, self.settings,
                                        shared=self.env.shared_arrangements_dir)
        self.arrange.changed.connect(self._timer.start)
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(theme.SPACE[0])
        middle_layout.addWidget(self.arrange)
        self.outputs = QSplitter(Qt.Horizontal)
        self.outputs.addWidget(self.arrange.board)
        middle_layout.addWidget(self.outputs, 1)
        self.split.addWidget(middle)

        self.result_tabs = QTabWidget()
        rich_box = QWidget()
        rich_layout = QVBoxLayout(rich_box)
        rich_layout.setContentsMargins(0, theme.SPACE[0], 0, 0)
        rich_layout.setSpacing(theme.SPACE[0])
        self.rich = QTextBrowser()
        self.rich.setOpenLinks(False)
        self.rich.setPlaceholderText("정리된 프롬프트(색 미리보기)")
        self.rich.anchorClicked.connect(self._rich_clicked)
        self.rich.highlighted.connect(self._rich_hover)
        rich_layout.addWidget(self.rich, 1)
        legend_row = QHBoxLayout()
        self.legend = QLabel()
        self.legend.setProperty("role", "muted")
        legend_row.addWidget(self.legend, 1)
        self.show_removed = QCheckBox("뺀 태그도 보기")
        self.show_removed.setChecked(True)
        self.show_removed.toggled.connect(lambda *_: self._render_rich())
        legend_row.addWidget(self.show_removed)
        rich_layout.addLayout(legend_row)
        self.output = QPlainTextEdit(readOnly=True)
        self.output.setPlaceholderText("정리된 프롬프트")
        self.removed = QPlainTextEdit(readOnly=True)
        self.removed.setPlaceholderText("뺀 태그와 이유")
        self.result_tabs.addTab(rich_box, "색 미리보기")
        self.result_tabs.addTab(self.output, "텍스트")
        self.result_tabs.addTab(self.removed, "뺀 태그")
        copy = theme.ghost(QPushButton("결과 복사"))
        copy.setToolTip("정리된 프롬프트(텍스트)를 클립보드에")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.output.toPlainText()))
        self.result_tabs.setCornerWidget(copy)
        self.outputs.addWidget(self.result_tabs)
        self.outputs.setSizes([420, 580])

        tokens_box = QWidget()
        tokens_layout = QVBoxLayout(tokens_box)
        tokens_layout.setContentsMargins(0, 0, 0, 0)
        tokens_layout.setSpacing(theme.SPACE[0])
        filters = QHBoxLayout()
        filters.setSpacing(theme.SPACE[0])
        self.filter_group = QButtonGroup(self)
        self.filter_buttons = {}
        for key, label in FILTERS:
            button = QPushButton(label)
            button.setProperty("chip", True)
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self.filter_group.addButton(button)
            self.filter_buttons[key] = button
            filters.addWidget(button)
        filters.addStretch(1)
        filters.addWidget(theme.caption("오른쪽 버튼: 커스텀 청크에 넣기·복사", "muted"))
        tokens_layout.addLayout(filters)
        self.tokens = QTableWidget(0, 6)
        self.tokens.setHorizontalHeaderLabels(["토큰", "분류", "청크", "등급", "사용량", "비고"])
        self.tokens.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tokens.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tokens.verticalHeader().hide()
        self.tokens.verticalHeader().setDefaultSectionSize(26)
        self.tokens.setShowGrid(False)
        self.tokens.setAlternatingRowColors(True)
        self.tokens.horizontalHeader().setStretchLastSection(True)
        self.tokens.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in ((0, 220), (1, 170), (2, 110), (3, 44), (4, 90)):
            self.tokens.setColumnWidth(col, width)
        self.tokens.itemSelectionChanged.connect(self._token_selected)
        self.tokens.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tokens.customContextMenuRequested.connect(self._token_menu)
        tokens_layout.addWidget(self.tokens)
        self.split.addWidget(tokens_box)
        self.split.setChildrenCollapsible(False)
        self.split.setSizes([80, 480, 240])
        for key, splitter in (("preview_split", self.split), ("preview_outputs", self.outputs)):
            state = self.settings.value(key) if self.settings is not None else None
            if state:
                splitter.restoreState(state)
        layout.addWidget(self.split, 1)
        self._restore_options()
        for signal in (self.show_removed.toggled, self.result_tabs.currentChanged):
            signal.connect(lambda *_: self._save_options())
        self._update_legend()

    def save_state(self, settings) -> None:
        settings.setValue("preview_split", self.split.saveState())
        settings.setValue("preview_outputs", self.outputs.saveState())
        self._save_options()

    # ---- 옵션 기억(끄고 다시 켜도 그대로) ----

    def _option_widgets(self) -> dict:
        return {"preset": self.preset, "max_rating": self.max_rating, "rules": self.rules, "input_dialect": self.dialect,
                "dialect_priority": self.priority, "output_dialect": self.out_dialect, "order": self.order,
                "one_line": self.one_line, "exclude": self.exclude, "comments": self.comments,
                "sentences": self.sentences, "show_removed": self.show_removed, "result_tab": self.result_tabs,
                "input": self.input}

    def _dialect_changed(self, *_) -> None:
        self.priority.setEnabled(self.dialect.currentData() == core.AUTO_DIALECT)

    def _save_options(self) -> None:
        """콤보는 위치가 아니라 값(프리셋 id 등)으로 적는다 — 프리셋을 지우거나 순서를 바꿔도 다른 것이 골라지지 않게."""
        if self.settings is None:
            return
        state = {"filter": self.filter}
        for key, w in self._option_widgets().items():
            if isinstance(w, QComboBox):
                state[key] = w.currentData()
            elif isinstance(w, QCheckBox):
                state[key] = w.isChecked()
            elif isinstance(w, QLineEdit):
                state[key] = w.text()
            elif isinstance(w, QTabWidget):
                state[key] = w.currentIndex()
            elif isinstance(w, QPlainTextEdit):
                state[key] = w.toPlainText()
        self.settings.setValue("preview_options", json.dumps(state, ensure_ascii=False))

    def _restore_options(self) -> None:
        """기억한 값 중 지금도 있는 것만 되살린다(없어진 프리셋·모르는 값은 기본값 그대로)."""
        raw = self.settings.value("preview_options") if self.settings is not None else None
        try:
            state = json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            state = {}
        if not isinstance(state, dict):
            return
        for key, w in self._option_widgets().items():
            if key not in state:
                continue
            value = state[key]
            if isinstance(w, QComboBox):
                index = w.findData(value)
                if index >= 0:
                    w.setCurrentIndex(index)
            elif isinstance(w, QCheckBox) and isinstance(value, bool):
                w.setChecked(value)
            elif isinstance(w, QLineEdit) and isinstance(value, str):
                w.setText(value)
            elif isinstance(w, QTabWidget) and isinstance(value, int) and 0 <= value < w.count():
                w.setCurrentIndex(value)
            elif isinstance(w, QPlainTextEdit) and isinstance(value, str):
                w.setPlainText(value)
        if isinstance(state.get("filter"), str) and state["filter"] in self.filter_buttons:
            self.set_filter(state["filter"])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.ensure_pack()

    def mark_stale(self) -> None:
        """분류가 바뀌었다(저장·되돌리기·다시 불러오기). 보이고 있으면 바로 다시 만든다."""
        if self.isVisible():
            self.ensure_pack()

    def ensure_pack(self) -> None:
        if self.pack is not None and self.pack_rev == self.store.revision:
            self.run()
            return
        if self.building:
            return
        rev = self.store.revision
        try:
            inputs = ops.memory_inputs(self.store)
        except (OSError, ValueError) as e:
            self.status.setText(theme.span(f"확장 DB 를 읽지 못했습니다: {html.escape(str(e))}", "danger"))
            return
        self.building = True
        self.status.setText("팩을 만드는 중(지금 편집 상태)…")
        jobs.run(lambda: ops.memory_pack(inputs), lambda pack: self._built(pack, rev), self._build_failed)

    def _built(self, pack: core.TagPack, rev: int) -> None:
        self.building = False
        self.pack, self.pack_rev = pack, rev
        self.run()
        if rev != self.store.revision and self.isVisible():
            self.ensure_pack()                  # 만드는 사이 또 고쳤다

    def _build_failed(self, error: BaseException) -> None:
        self.building = False
        errors = error.errors if isinstance(error, builder.BuildError) else [str(error)]
        self.status.setText(theme.span("팩을 만들지 못했습니다(점검 탭 확인): " + html.escape("; ".join(errors[:5])),
                                       "danger"))

    def options(self) -> dict:
        opts = {
            "preset": self.preset.currentData(), "max_rating": self.max_rating.currentData(),
            "rules": self.rules.currentData(), "input_dialect": self.dialect.currentData(),
            "dialect_priority": self.priority.currentData(),
            "output_dialect": self.out_dialect.currentData(), "order": self.order.currentData(),
            "line_per_section": not self.one_line.isChecked(), "exclude": self.exclude.text(),
            "comments": self.comments.currentData(), "sentences": self.sentences.isChecked(),
        }
        opts.update(self.arrange.options())
        return opts

    def run(self) -> None:
        if self.pack is None:
            return
        t0 = time.perf_counter()
        try:
            result = core.organize(self.input.toPlainText(), self.pack, self.options())
        except ValueError as e:
            self.status.setText(theme.span(html.escape(str(e)), "danger"))
            return
        ms = (time.perf_counter() - t0) * 1000
        self.report = result.report
        self.output.setPlainText(result.text)
        self.removed.setPlainText(result.removed_text)
        c = result.report["counts"]
        stale = " · <b>분류를 고쳐 다시 만드는 중</b>" if self.pack_rev != self.store.revision else ""
        guessed = f" · 추정 {c['guessed']}" if self.arrange.guess_box.isChecked() else ""
        extra = "".join(f" · {label} {c[key]}" for key, label in (("sentences", "문장"), ("comments", "주석"))
                        if c.get(key))
        warn = ""
        if c.get("warnings"):
            first = result.report["warnings"][0]
            more = f" 외 {c['warnings'] - 1}" if c["warnings"] > 1 else ""
            warn = "<br>" + theme.span(f"경고: {html.escape(first['tag'])} — "
                                       f"{html.escape(core.warning_text(first['reason']))}{more}", "warning")
        self.status.setText(f"팩 {self.pack.version} (지금 편집 상태) · 처리 {ms:.1f}ms · 토큰 {c['total']} · "
                            f"뺌 {c['removed']} · 표시 {c['flagged']} · 미분류 {c['unclassified']} · "
                            f"알 수 없음 {c['unknown']}{guessed}{extra}"
                            f"{dialect_status(result.report['dialect'])}{stale}{warn}")
        self._render_rich()
        self.arrange.set_report(result.report)
        self._fill_tokens(result.report["tokens"])
        self._save_options()

    # ---- 색 미리보기 ----

    def _update_legend(self) -> None:
        t = theme.tokens()
        self.legend.setText(
            f"<span style='color:{t['fg']}'>밝게</span> 남음 · "
            f"<span style='color:{t['disabled']};text-decoration:line-through'>흐리게</span> 뺌 · "
            f"<span style='color:{t['danger']}'>빨강</span> 팩에 없음 · <i>기울임</i> 추정 · <b>굵게</b> 덮어씀 · "
            f"<span style='color:{t['warning']};text-decoration:underline'>밑줄</span> 표시만 · "
            f"<span style='color:{t['muted']}'><i>// 흐린 기울임</i></span> 주석")

    def _render_rich(self) -> None:
        self._update_legend()
        if self.report is None:
            return
        bar = self.rich.verticalScrollBar()
        keep = bar.value()
        self.rich.setHtml(rich_html(self.report, self.show_removed.isChecked()))
        bar.setValue(keep)

    def _rich_clicked(self, url) -> None:
        text = url.toString()
        if not text.startswith("tok:") or self.report is None:
            return
        index = int(text[4:])
        if self.tokens.isRowHidden(index):
            self.set_filter("all")
        self.tokens.selectRow(index)
        self.tokens.scrollToItem(self.tokens.item(index, 0))

    def _rich_hover(self, url) -> None:
        text = url.toString() if hasattr(url, "toString") else str(url)
        if not text.startswith("tok:") or self.report is None:
            self._update_legend()
            return
        row = self.report["tokens"][int(text[4:])]
        parts = [html.escape(row["text"]), html.escape(row["primary"]),
                 "청크 " + html.escape(self._chunk_labels().get(row.get("section", ""), row.get("section", "")))]
        for key, label in (("removed", "뺌"), ("flagged", "표시"), ("guess", "추정"), ("injected", "덮어씀")):
            if row.get(key):
                parts.append(f"{label}: {html.escape(str(row[key]))}")
        self.legend.setText(" · ".join(parts))

    def _chunk_labels(self) -> dict:
        if self.report is None:
            return {}
        return {c["id"]: c["label"] for seg in self.report.get("arranged", ()) for row in seg["rows"] for c in row}

    # ---- 토큰 표 ----

    def set_filter(self, key: str) -> None:
        self.filter = key
        self.filter_buttons[key].setChecked(True)
        self._apply_filter()
        self._save_options()

    def _apply_filter(self) -> None:
        """필터에 안 맞는 행을 숨긴다. 숨긴 행은 고르지 않은 것으로(보이지 않는 태그가 편집 패널에 남지 않게)."""
        sel = self.tokens.selectionModel()
        for i, row in enumerate(self._rows):
            hide = not _matches(row, self.filter)
            self.tokens.setRowHidden(i, hide)
            if hide and sel.isRowSelected(i, QModelIndex()):
                sel.select(self.tokens.model().index(i, 0), QItemSelectionModel.Deselect | QItemSelectionModel.Rows)

    def _fill_tokens(self, rows) -> None:
        keep = set(self.selected_names())
        labels = self._chunk_labels()
        self._rows = list(rows)
        self.tokens.blockSignals(True)
        self.tokens.clearSelection()
        self.tokens.setRowCount(len(rows))
        for i, r in enumerate(rows):
            note = []
            if r.get("removed"):
                note.append(f"뺌: {r['removed']}")
            if r.get("flagged"):
                note.append(f"표시: {r['flagged']}")
            if r.get("injected"):
                note.append(f"덮어쓰기로 넣음({r['injected']})")
            if r.get("alias_of"):
                note.append(f"별칭 {r['alias_of']} → {r.get('key', '')}")
            if r["kind"] == "unknown":
                note.append(f"팩에 없는 토큰 — 추정 {r['guess']}" if r.get("guess") else "팩에 없는 토큰")
            elif r["kind"] == "comment":
                note.append("주석" if r.get("removed") else "주석 — 구간 맨 위에 남김")
            elif r["kind"] == "natural":
                note.append("자연어 문장 — 입력 그대로")
            elif r["kind"] == "special":
                note.append(f"문법 토큰 — 자리 {r['guess']}" if r.get("guess") else "문법 토큰")
            if r.get("warning"):
                note.append("경고: " + core.warning_text(r["warning"]))
            primary = r["primary"]
            section = r.get("section", "")
            cells = [r["text"], "보류/미분류(etc)" if primary == "etc" else primary, labels.get(section, section),
                     theme.rating_name(r.get("rating", "")), f"{r['count']:,}" if r.get("count") is not None else "",
                     " · ".join(note)]
            name = self.store.resolve(r["key"]) if r.get("key") else None
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, name)
                if r.get("removed"):
                    item.setForeground(theme.color("muted"))
                elif r["kind"] == "unknown" and not r.get("guess"):
                    item.setForeground(theme.color("danger"))
                elif primary == "etc":
                    item.setForeground(theme.color("muted"))
                elif col == 3 and theme.rating_color(r.get("rating", "")) is not None:
                    item.setForeground(theme.rating_color(r["rating"]))
                if r.get("guess"):
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                if col == 2 and section:
                    item.setToolTip(section)
                if col == 4:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.tokens.setItem(i, col, item)
        for key, label in FILTERS:
            n = len(rows) if key == "all" else sum(1 for r in rows if _matches(r, key))
            self.filter_buttons[key].setText(f"{label} {n}")
        self._apply_filter()
        # 다시 계산해도 고른 태그는 이름으로 다시 고른다. 사라졌으면 편집 패널도 비운다(표와 패널이 어긋나지 않게)
        sel = self.tokens.selectionModel()
        found = False
        for i in range(self.tokens.rowCount()):
            if not self.tokens.isRowHidden(i) and self.tokens.item(i, 0).data(Qt.UserRole) in keep:
                sel.select(self.tokens.model().index(i, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
                found = True
        self.tokens.blockSignals(False)
        if keep and not found and self.isVisible():
            self.open_names.emit([])

    def _token_selected(self) -> None:
        # 이름이 없는 토큰(팩에 없음·커스텀)을 골라도 알린다: 편집 패널이 이전 태그를 붙잡고 있지 않게
        if self.isVisible():
            self.open_names.emit(list(dict.fromkeys(self.selected_names())))

    def selected_names(self) -> List[str]:
        return [n for n in (self.tokens.item(i.row(), 0).data(Qt.UserRole)
                            for i in self.tokens.selectionModel().selectedRows()) if n]

    def selected_texts(self) -> List[str]:
        """고른 토큰의 글자(팩에 없는 토큰도). 커스텀 청크에 넣을 때 쓴다."""
        rows = sorted(i.row() for i in self.tokens.selectionModel().selectedRows())
        return list(dict.fromkeys(self._rows[i]["text"] for i in rows if i < len(self._rows)))

    def _token_menu(self, pos) -> None:
        texts = self.selected_texts()
        if not texts:
            return
        menu = QMenu(self)
        add = menu.addMenu(f"커스텀 청크에 넣기 ({len(texts)}개)")
        for cid, label in self.arrange.custom_chunks():
            add.addAction(label, lambda c=cid: self.arrange.add_tags_to(c, texts))
        if self.arrange.custom_chunks():
            add.addSeparator()
        add.addAction("새 커스텀 청크…", lambda: self.arrange.new_custom(texts))
        unknown = self.selected_unknown()
        if unknown:
            menu.addAction(f"확장 DB 에 새 태그로 더하기 ({len(unknown)}개)…",
                           lambda: self.add_extra_requested.emit(unknown))
        menu.addAction("토큰 복사", lambda: QGuiApplication.clipboard().setText(", ".join(texts)))
        menu.exec(self.tokens.viewport().mapToGlobal(pos))

    def selected_unknown(self) -> List[str]:
        """고른 토큰 중 팩에 없는 것(추정한 것 포함)의 글자 — 확장 DB 에 더할 후보."""
        rows = sorted(i.row() for i in self.tokens.selectionModel().selectedRows())
        return list(dict.fromkeys(self._rows[i]["text"] for i in rows
                                  if i < len(self._rows) and self._rows[i]["kind"] == "unknown"))


class WorkPage(QWidget):
    """빌드·노드 팩 반영·원본 가져오기. 쓰는 일은 모두 확인을 받고, 도는 동안 편집을 막는다(on_busy)."""

    def __init__(self, store: Store, env: ops.Env, on_busy: Callable[[Optional[str]], None],
                 on_imported: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.env = env
        self.on_busy = on_busy
        self.on_imported = on_imported
        self.running: Optional[str] = None
        self._versions_rev: Optional[int] = None
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[1], 0)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, theme.SPACE[2], 0)
        layout.setSpacing(theme.SPACE[1])

        # 빌드
        self.version_label = theme.caption("", "muted")
        self.version_label.setWordWrap(True)
        self.build_btn = theme.decorate(theme.primary(QPushButton("빌드")), "build")
        self.build_btn.clicked.connect(self.build)
        _section(layout, "빌드", "build/tagpack.json.gz 를 만듭니다 · python tagorg.py build", first=True)
        line = QHBoxLayout()
        line.addWidget(self.version_label, 1)
        line.addWidget(self.build_btn)
        layout.addLayout(line)

        # 노드 팩 반영
        _section(layout, "노드 팩 반영", "ComfyUI_BMK_Nodes 에 코어·팩·청크 편집기를 복사합니다(빌드 포함, 커밋은 노드 팩에서 따로) · "
                                         "python tagorg.py sync")
        form = _form(layout)
        self.target_edit = QLineEdit(str(ops.sync_target(self.env) or ""))
        self.target_edit.setPlaceholderText("ComfyUI_BMK_Nodes 폴더")
        pick = theme.decorate(QPushButton("폴더…"), "folder")
        pick.clicked.connect(self._pick_target)
        line = QHBoxLayout()
        line.addWidget(self.target_edit, 1)
        line.addWidget(pick)
        form.addRow(theme.caption("대상", "field"), line)
        self.sync_preview_btn = theme.decorate(QPushButton("미리보기"), "eye")
        self.sync_preview_btn.setToolTip("임시 폴더에 빌드해 무엇을 쓸지 비교만 합니다(아무것도 쓰지 않음)")
        self.sync_preview_btn.clicked.connect(self.sync_preview)
        self.sync_btn = theme.decorate(theme.primary(QPushButton("반영…")), "sync")
        self.sync_btn.setToolTip("build/ 에 빌드한 뒤 노드 팩에 씁니다(확인을 받음). 커밋은 노드 팩에서 따로 합니다")
        self.sync_btn.clicked.connect(lambda: self.sync_apply(force=False))
        form.addRow("", _buttons(self.sync_preview_btn, self.sync_btn))

        # 원본 가져오기
        _section(layout, "원본 가져오기", "새 danbooru.csv 를 현재 원본으로 하고 overlay 에 델타를 반영합니다 · "
                                         "python tagorg.py import")
        form = _form(layout)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("예: danbooru_2026-11-01_pt20-ia-dd.csv")
        pick = theme.decorate(QPushButton("파일…"), "folder")
        pick.clicked.connect(self._pick_csv)
        line = QHBoxLayout()
        line.addWidget(self.csv_edit, 1)
        line.addWidget(pick)
        form.addRow(theme.caption("CSV", "field"), line)
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("YYYY-MM-DD (비우면 파일 이름에서)")
        form.addRow(theme.caption("날짜", "field"), self.date_edit)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("다시 받을 수 있는 주소(sources.json 에 기록)")
        form.addRow(theme.caption("URL", "field"), self.url_edit)
        self.move_box = QCheckBox("복사 대신 이동")
        form.addRow("", self.move_box)
        self.import_preview_btn = theme.decorate(QPushButton("미리보기"), "eye")
        self.import_preview_btn.setToolTip("델타 보고서만 만듭니다(아무것도 쓰지 않음)")
        self.import_preview_btn.clicked.connect(lambda: self.import_csv(dry_run=True))
        self.import_btn = theme.decorate(theme.primary(QPushButton("가져오기…")), "download")
        self.import_btn.setToolTip("overlay·sources.json·reports 를 고치고 CSV 를 data/source 로 복사합니다(확인을 받음)")
        self.import_btn.clicked.connect(lambda: self.import_csv(dry_run=False))
        form.addRow("", _buttons(self.import_preview_btn, self.import_btn))
        layout.addStretch(1)

        scroll = QScrollArea(widgetResizable=True, frameShape=QScrollArea.NoFrame)
        scroll.setWidget(body)
        split = QSplitter(Qt.Vertical)
        split.addWidget(scroll)
        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(theme.SPACE[0])
        log_layout.addWidget(theme.caption("결과", "section"))
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setPlaceholderText("빌드·반영·가져오기의 결과가 여기에 쌓입니다")
        _mono(self.log)
        log_layout.addWidget(self.log)
        split.addWidget(log_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([560, 240])
        outer.addWidget(split, 1)
        self.buttons = [self.build_btn, self.sync_preview_btn, self.sync_btn, self.import_preview_btn, self.import_btn]

    # ---- 공통 ----

    def write(self, title: str, lines) -> None:
        stamp = time.strftime("%H:%M:%S")
        text = "\n".join(lines) if not isinstance(lines, str) else lines
        self.log.appendPlainText(f"[{stamp}] {title}\n{text.rstrip()}\n")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _start(self, label: str, fn, done, failed, block_edits: bool) -> bool:
        if self.running:
            return False
        self.running = label
        for b in self.buttons:
            b.setEnabled(False)
        if block_edits:
            self.on_busy(label)

        def finish(handler, value) -> None:
            self.running = None
            for b in self.buttons:
                b.setEnabled(True)
            if block_edits:
                self.on_busy(None)
            handler(value)

        jobs.run(fn, lambda r: finish(done, r), lambda e: finish(failed, e))
        return True

    def _failed(self, title: str):
        def handler(error: BaseException) -> None:
            if isinstance(error, builder.BuildError):
                self.write(f"{title} 실패 — 빌드 오류 {len(error.errors)}개", [f"  - {e}" for e in error.errors])
            else:
                self.write(f"{title} 실패", f"  {type(error).__name__}: {error}")
            self.refresh_versions(force=True)
        return handler

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_versions()

    def mark_stale(self) -> None:
        if self.isVisible():
            self.refresh_versions()

    def refresh_versions(self, force: bool = False) -> None:
        """build/ 팩 버전과 지금 편집 상태의 버전을 비교해 보여 준다(빌드가 최신인지)."""
        key = (self.store.revision, _file_sig(self.env.pack_path))     # CLI 가 따로 빌드해도 다시 확인
        if self._versions_rev == key and not force:
            return
        rev = self.store.revision
        self._versions_rev = key
        try:
            inputs = ops.memory_inputs(self.store)
        except (OSError, ValueError) as e:
            self.version_label.setText(f"확장 DB 를 읽지 못했습니다: {e}")
            return
        path = self.env.pack_path
        jobs.run(lambda: (ops.built_version(path), ops.version_of(inputs)), lambda r: self._show_versions(*r, rev))

    def _show_versions(self, built: Optional[str], memory: str, rev: int) -> None:
        if rev != self.store.revision:
            return
        if built is None:
            text = f"빌드한 팩이 없습니다 — 지금 편집 상태 {memory}"
        elif built == memory:
            text = f"build/ 팩 {built} — 지금 편집 상태와 같음(최신)"
        else:
            text = (f"build/ 팩 {built} — 지금 편집 상태는 {memory}: " + theme.span("다시 빌드해야 합니다", "warning", True) +
                    " (노드 팩 반영은 빌드를 포함)")
        self.version_label.setText(text)

    # ---- 빌드 ----

    def build(self) -> None:
        def done(built: ops.Built) -> None:
            self.write("빌드", [built.summary()] + [f"경고: {w}" for w in built.warnings])
            self.refresh_versions(force=True)
        self._start("빌드", lambda: ops.build(self.env), done, self._failed("빌드"), block_edits=False)

    # ---- 노드 팩 반영 ----

    def _target(self) -> Optional[Path]:
        text = self.target_edit.text().strip()
        if not text:
            QMessageBox.information(self, "노드 팩 반영", "ComfyUI_BMK_Nodes 폴더를 지정하세요.")
            return None
        return Path(text)

    def _pick_target(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "ComfyUI_BMK_Nodes 폴더", self.target_edit.text())
        if path:
            self.target_edit.setText(path)

    def sync_preview(self) -> None:
        target = self._target()
        if target is None:
            return

        def done(result) -> None:
            built, actions = result
            conflicts = [a.rel for a in actions if a.state == "conflict"]
            lines = [f"임시 빌드 {built.version} 와 비교 — 아무것도 쓰지 않음: {target}"] + \
                    [f"  {line}" for line in ops.format_actions(actions)]
            if conflicts:
                lines.append("  노드 팩에서 직접 바뀐 파일이 있습니다. 그 내용이 필요하면 먼저 TagOrganizer 로 옮기세요.")
            self.write("노드 팩 반영 미리보기", lines)
        self._start("노드 팩 반영 미리보기", lambda: ops.sync_preview(self.env, target), done,
                    self._failed("노드 팩 반영 미리보기"), block_edits=False)

    def sync_apply(self, force: bool, target: Optional[Path] = None) -> None:
        """force 재시도는 처음 확인받은 target 을 그대로 받는다(그사이 대상 칸을 바꿔도 다른 폴더에 쓰지 않게)."""
        target = target or self._target()
        if target is None:
            return
        if not force:
            answer = QMessageBox.question(
                self, "노드 팩 반영",
                f"build/ 에 빌드한 뒤 다음 폴더에 코어·팩·청크 편집기를 씁니다:\n{target}\n\n"
                "쓰는 파일: bmk_tag_organizer_core.py, tag_organizer_data/ (커밋은 노드 팩에서 따로)\n계속할까요?")
            if answer != QMessageBox.Yes:
                return

        def done(result) -> None:
            built, actions = result
            lines = [built.summary(), f"대상: {target}"] + [f"  {line}" for line in ops.format_actions(actions)]
            if any(a.state != "unchanged" for a in actions):
                lines.append(f"노드 팩에서 커밋할 파일: {sync.CORE_REL}, {sync.DATA_REL}/")
            self.write("노드 팩 반영" + (" (덮어씀)" if force else ""), lines)
            self.refresh_versions(force=True)

        def failed(error: BaseException) -> None:
            if isinstance(error, sync.SyncConflict) and not force:
                self.write("노드 팩 반영 멈춤", f"  {error}")
                answer = QMessageBox.warning(
                    self, "노드 팩에서 바뀐 파일",
                    "지난 동기화 뒤 노드 팩에서 직접 바뀐 파일이 있습니다:\n  " + "\n  ".join(error.paths) +
                    "\n\n덮어쓰면 노드 팩에서 고친 내용이 사라집니다(노드 팩 git 에는 남음). 덮어쓸까요?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer == QMessageBox.Yes:
                    QTimer.singleShot(0, lambda: self.sync_apply(force=True, target=target))
                return
            self._failed("노드 팩 반영")(error)
        self._start("노드 팩 반영", lambda: ops.sync_apply(self.env, target, force), done, failed, block_edits=False)

    # ---- 원본 가져오기 ----

    def _pick_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "새 원본 CSV", self.csv_edit.text(), "CSV (*.csv)")
        if path:
            self.csv_edit.setText(path)

    def import_csv(self, dry_run: bool) -> None:
        text = self.csv_edit.text().strip()
        if not text:
            QMessageBox.information(self, "원본 가져오기", "가져올 CSV 파일을 고르세요.")
            return
        csv_path, date, url, move = Path(text), self.date_edit.text().strip(), self.url_edit.text().strip(), \
            self.move_box.isChecked()
        if not dry_run:
            answer = QMessageBox.question(
                self, "원본 가져오기",
                f"{csv_path.name} 을 현재 원본으로 가져옵니다.\n\n"
                "overlay(사라진 태그 표시·이름 변경·type 변경), data/source/sources.json, reports/ 를 고치고 "
                f"CSV 를 data/source 로 {'이동' if move else '복사'}합니다. 끝나면 다시 불러옵니다.\n"
                "먼저 '미리보기'로 보고서를 보는 것을 권합니다. 계속할까요?")
            if answer != QMessageBox.Yes:
                return

        def done(report: str) -> None:
            self.write("원본 가져오기 미리보기 — 아무것도 쓰지 않음" if dry_run else "원본 가져오기", report)
            if not dry_run:
                self.on_imported()

        def failed(error: BaseException) -> None:
            if isinstance(error, tagdb.OverlayChanged):
                self.write("원본 가져오기 멈춤", f"  {error} 다시 불러온 뒤 다시 하세요.")
                return
            self._failed("원본 가져오기")(error)
        title = "원본 가져오기 미리보기" if dry_run else "원본 가져오기"
        self._start(title, lambda: ops.import_csv(self.env, csv_path, date, url, move, dry_run=dry_run),
                    done, failed, block_edits=not dry_run)


class StatsPage(QWidget):
    """`tagorg stats` — 마일스톤별 사용량 커버리지와 status·카테고리 분포(지금 편집 상태)."""

    COLUMNS = ("마일스톤", "범위(general 상위)", "범위 사용량", "분류됨", "분류 사용량", "확정", "확정 사용량",
               "사람 확인", "보류(etc)")

    def __init__(self, store: Store, env: ops.Env, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.env = env
        self._rev: Optional[int] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[1], 0)
        layout.setSpacing(theme.SPACE[1])
        bar = QHBoxLayout()
        self.refresh_btn = theme.decorate(QPushButton("새로 고침"), "refresh")
        self.refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        self.summary = QLabel(wordWrap=True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.summary, 1)
        layout.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setMaximumHeight(200)
        layout.addWidget(self.table)
        self.text = QPlainTextEdit(readOnly=True)
        _mono(self.text)
        self.text.setPlaceholderText("python tagorg.py stats 출력")
        layout.addWidget(self.text, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def mark_stale(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        if self._rev == self.store.revision and not force:
            return
        rev = self.store.revision
        self._rev = rev
        inputs = ops.memory_inputs(self.store, [])
        path = self.env.pack_path
        self.summary.setText("계산하는 중…")
        jobs.run(lambda: (ops.coverage(inputs), ops.stats_text(inputs, ops.built_version(path))),
                 lambda r: self._show(*r, rev))

    def _show(self, rows, text: str, rev: int) -> None:
        if rev != self.store.revision:
            return
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            c, f, v, e = r["classified"], r["confirmed"], r["reviewed"], r["etc"]
            cells = [r["milestone"], f"{r['top']:,}", f"{r['range_usage']:.1%}", f"{c['tags']:,}", f"{c['usage']:.1%}",
                     f"{f['tags']:,}", f"{f['usage']:.1%}", f"{v['tags']:,}", f"{e['tags']:,}"]
            for col, cell in enumerate(cells):
                item = QTableWidgetItem(cell)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, col, item)
        self.summary.setText(text.splitlines()[0] if text else "")
        self.text.setPlainText(text)
