"""편집 패널 — 고른 태그의 분류를 고친다. `tagorg fix` 와 같은 검증을 입력하는 동안 보여 주고, 저장하면 reviewed 로 잠근다.

태그 하나: 폼과 지금 값이 다른 칸만 고친다. 여러 개: 사용자가 건드린 칸만 모두에 같은 값으로 쓴다
(부위는 켠 것을 더하고 끈 것을 빼며, 일부에만 있는 부위는 태그마다 그대로 둔다).
"""

from __future__ import annotations

import html
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QCompleter, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)

from tools import extra, fix

from . import theme
from .arrange_panel import FlowLayout
from .evidence_panel import EvidencePanel
from .store import MAX_EDIT, SITE_TYPE_LABELS, TYPE_LABELS, ConflictError, Result, Store, db_label

RATINGS = ("", "g", "s", "q", "e")
RATING_TEXT = {"": "없음", **theme.RATING_NAMES}
EDITOR_HELP = theme.help_text(
    ("태그 고르기", "왼쪽 목록이나 미리보기 표에서 고르면 여기서 분류를 고칩니다"),
    ("여러 개", "Ctrl·Shift 로 고르면 건드린 칸만 모두에 같은 값으로 씁니다. '(여러 값)' 칸은 그대로 둡니다"),
    ("부위", "여러 개일 때 켠 것을 더하고 끈 것을 뺍니다. 일부에만 있는 부위(■)는 태그마다 그대로입니다"),
    ("Ctrl+S · Ctrl+Enter", "저장 · 저장 후 다음"),
)


def _same(key: str, new: str, old: str) -> bool:
    """폼 값과 저장된 값이 같은 뜻인가. 부위는 순서 무관(실제 11행이 규칙 순서와 다름), identity 는 빈 값 = 0."""
    if key == "regions":
        return set(fix.split_list(new)) == set(fix.split_list(old))
    if key == "secondary":
        return fix.split_list(new) == fix.split_list(old)
    if key == "identity":
        return (new or "0") == (old or "0")
    return new == old


def _identity_ok(primary: str) -> bool:
    return primary.partition("/")[0] == "body" or primary == "subject/species"


def _divider() -> QFrame:
    line = QFrame()
    line.setProperty("role", "divider")
    return line


def _swatch(hex_color: Optional[str]):
    pix = QPixmap(10, 10)
    pix.fill(QColor(hex_color or theme.tokens()["muted"]))
    return QIcon(pix)


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return w


class EditorPanel(QWidget):
    saved = Signal(list, bool)      # (고친 태그 이름, 다음 태그로 넘어갈지)

    def __init__(self, store: Store, parent=None, evidence: Optional[EvidencePanel] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.evidence = evidence or EvidencePanel(store)     # 태그 하나를 고르면 Danbooru 증거(위키·연관 태그)
        self.names: List[str] = []
        self.dirty: set = set()
        self._loading = False
        self._last: Optional[Result] = None
        self._shown_main: Optional[str] = None
        self.touched_regions: set = set()       # 여러 태그일 때 사용자가 누른 부위(누른 것만 더하고 뺀다)
        self.before_save: Optional[Callable[[], bool]] = None     # True 를 돌려주면 저장하지 않는다(선택 전환 중)
        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._update_preview)
        self._build()
        self.set_names([])

    # ---- 화면 ----

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE[3], theme.SPACE[2], theme.SPACE[3], theme.SPACE[2])
        outer.setSpacing(theme.SPACE[1])

        self.title = theme.caption("", "headline")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title.setWordWrap(True)
        self.wiki_btn = theme.decorate(theme.ghost(QPushButton("위키")), "external")
        self.posts_btn = theme.decorate(theme.ghost(QPushButton("게시물")), "external")
        self.wiki_btn.setToolTip("Danbooru 위키에서 이 태그의 뜻을 봅니다(브라우저)")
        self.posts_btn.setToolTip("Danbooru 에서 이 태그가 붙은 게시물을 봅니다(브라우저)")
        self.wiki_btn.clicked.connect(lambda: self._open_danbooru("wiki_pages/"))
        self.posts_btn.clicked.connect(lambda: self._open_danbooru("posts?tags="))
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(self.wiki_btn, 0, Qt.AlignTop)
        head.addWidget(self.posts_btn, 0, Qt.AlignTop)
        self.help_btn = theme.HelpButton(EDITOR_HELP)
        head.addWidget(self.help_btn, 0, Qt.AlignVCenter)
        self.info = theme.caption("", "muted")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.state = theme.caption("", "muted")
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addLayout(head)
        outer.addWidget(self.info)
        outer.addWidget(self.state)

        outer.addSpacing(theme.SPACE[1])
        outer.addWidget(_divider())

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(theme.SPACE[2])
        form.setVerticalSpacing(theme.SPACE[1])
        self.field_labels: Dict[str, QLabel] = {}

        self.main_combo = QComboBox()
        self.main_combo.setPlaceholderText("(여러 값)")
        for c in self.store.categories:                 # 표의 분류 칩과 같은 색
            self.main_combo.addItem(_swatch(c.get("color")), f"{c.get('label', c['id'])}  {c['id']}", c["id"])
        self.main_combo.insertSeparator(self.main_combo.count())
        self.main_combo.addItem(_swatch(self.store.colors.get("etc")), "보류  etc (뜻이 불확실)", "etc")
        self.main_combo.activated.connect(self._main_activated)
        self.sub_combo = QComboBox()
        self.sub_combo.setPlaceholderText("(여러 값)")
        self.sub_combo.activated.connect(lambda _: self._touch("primary"))
        self._add_row(form, "primary", "메인", self.main_combo)
        self._add_row(form, "sub", "서브", self.sub_combo)

        self.rating_group = QButtonGroup(self)
        rating_row = FlowLayout(spacing=theme.SPACE[0])       # 전체 이름이라 좁으면 두 줄로
        for r in RATINGS:
            b = QPushButton(RATING_TEXT[r], checkable=True)
            b.setProperty("chip", True)
            b.setProperty("rating", r)
            self.rating_group.addButton(b)
            rating_row.addWidget(b)
        self.rating_group.buttonClicked.connect(lambda _: self._touch("rating"))
        self._add_row(form, "rating", "등급", _wrap(rating_row))

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.SPACE[2])
        grid.setVerticalSpacing(0)
        self.region_boxes: Dict[str, QCheckBox] = {}
        for i, reg in enumerate(self.store.regions):
            box = QCheckBox(reg)
            box.clicked.connect(lambda _=False, r=reg: self._region_clicked(r))
            self.region_boxes[reg] = box
            grid.addWidget(box, i // 3, i % 3)
        self._add_row(form, "regions", "부위", _wrap(grid))

        self.identity_box = QCheckBox("캐릭터 고유 외형 (body·subject/species 만)")
        self.identity_box.clicked.connect(self._identity_clicked)
        self._add_row(form, "identity", "identity", self.identity_box)

        self.secondary_edit = QLineEdit()
        self.secondary_edit.setProperty("ph", "main/sub 를 | 로 이어서 (보통 비움)")
        cats = [c["id"] for c in self.store.categories] + [
            f"{c['id']}/{s['id']}" for c in self.store.categories for s in c.get("subs", ())]
        completer = QCompleter(sorted(cats), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.secondary_edit.setCompleter(completer)
        self.secondary_edit.textEdited.connect(lambda _: self._touch("secondary"))
        self._add_row(form, "secondary", "보조 분류", self.secondary_edit)

        self.note_edit = QLineEdit()
        self.note_edit.setProperty("ph", "메모(근거·뜻 짐작)")
        self.note_edit.textEdited.connect(lambda _: self._touch("note"))
        self._add_row(form, "note", "메모", self.note_edit)

        outer.addLayout(form)

        self.message = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(self.message)
        card = QFrame()
        card.setProperty("role", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(theme.SPACE[2], theme.SPACE[1], theme.SPACE[2], theme.SPACE[1])
        card_layout.addWidget(scroll)
        card.setMinimumHeight(80)
        card.setMaximumHeight(130)              # 남는 높이는 아래 참고 탭(증거)에
        outer.addSpacing(theme.SPACE[1])
        outer.addWidget(card)

        buttons = QHBoxLayout()
        self.reset_btn = theme.decorate(theme.ghost(QPushButton("입력 취소")), "undo")
        self.reset_btn.setToolTip("폼을 지금 저장된 값으로 되돌림 (Esc)")
        self.reset_btn.clicked.connect(lambda: self.set_names(self.names))
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=lambda: self.set_names(self.names),
                  context=Qt.WidgetWithChildrenShortcut)
        self.save_btn = theme.decorate(QPushButton("저장"), "save")
        self.save_btn.setToolTip("Ctrl+S")
        self.save_btn.clicked.connect(lambda: self.save(advance=False))
        self.next_btn = theme.decorate(theme.primary(QPushButton("저장 후 다음")), "chevron-right")
        self.next_btn.setToolTip("Ctrl+Enter")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(lambda: self.save(advance=True))
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.next_btn)
        outer.addLayout(buttons)

        # 참고 자료: 증거와 선례를 탭으로 겹쳐 남는 높이를 함께 쓴다(창 높이 900 에서 폼이 눌리지 않게)
        self.refs = QTabWidget()
        self.refs.addTab(self.evidence, "Danbooru 증거")
        similar = QVBoxLayout()
        similar.setSpacing(theme.SPACE[0])
        self.similar_list = QListWidget()
        self.similar_list.itemDoubleClicked.connect(self._take_precedent)
        similar.addWidget(self.similar_list, 1)
        self.refs.addTab(_wrap(similar), "같은 계열 선례")
        self.refs.setTabToolTip(1, "더블클릭하면 그 분류를 폼에 가져옵니다")
        self.refs.setMinimumHeight(150)
        outer.addWidget(self.refs, 1)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def _add_row(self, form: QFormLayout, key: str, text: str, widget: QWidget) -> None:
        label = theme.caption(text, "field")
        self.field_labels[key] = label
        form.addRow(label, widget)

    # ---- 고른 태그 불러오기 ----

    def set_names(self, names: Sequence[str]) -> None:
        """고른 태그를 폼에 불러온다(입력하던 값은 버린다). 원본에도 overlay 에도 없는 이름(점검 탭의 지워진 행)은 뺀다."""
        self.names = [n for n in dict.fromkeys(names) if n in self.store.by_name]
        self.dirty = set()
        self.touched_regions = set()
        self._loading = True
        mixed = "(여러 값)" if len(self.names) > 1 else "(고르세요)"
        self.main_combo.setPlaceholderText(mixed)
        self.sub_combo.setPlaceholderText(mixed)
        try:
            self._show_header()
            values = self.store.common(self.names) if self.names else {k: "" for k in fix.EDITABLE}
            self._fill(values)
        finally:
            self._loading = False
        self._show_similar()
        # 증거 캐시·Danbooru 주소는 Danbooru 표기(밑줄) — 확장 DB 이름은 공백이라 바꿔 넣는다
        self.evidence.set_name(extra.evidence_name(self.names[0]) if len(self.names) == 1 else None)
        self._mark_dirty()
        self._update_preview()

    def refresh(self) -> None:
        """저장된 값이 밖에서 바뀌었을 때(다시 불러오기·되돌리기): 폼을 최신 값으로 다시 채우되, 사용자가 건드린
        칸만 입력하던 값으로 되살린다(건드리지 않은 칸이 옛 값으로 저장되지 않게)."""
        snap, dirty, touched = self._snapshot(), set(self.dirty), set(self.touched_regions)
        self.set_names(self.names)
        if not dirty:
            return
        self._loading = True
        try:
            self._restore(snap)
        finally:
            self._loading = False
        self.dirty, self.touched_regions = dirty, touched
        self._sync_enabled()
        self._mark_dirty()
        self._update_preview()

    def _snapshot(self) -> Dict[str, object]:
        """건드린 칸의 위젯 상태."""
        snap: Dict[str, object] = {}
        if "primary" in self.dirty:
            snap["primary"] = (self.main_combo.currentData(), self.sub_combo.currentData())
        if "rating" in self.dirty:
            b = self.rating_group.checkedButton()
            snap["rating"] = b.property("rating") if b else None
        if "regions" in self.dirty:
            regs = self.touched_regions if len(self.names) > 1 else list(self.region_boxes)
            snap["regions"] = {r: self.region_boxes[r].checkState() for r in regs}
        if "identity" in self.dirty:
            snap["identity"] = self.identity_box.checkState()
        for key, edit in (("secondary", self.secondary_edit), ("note", self.note_edit)):
            if key in self.dirty:
                snap[key] = edit.text()
        return snap

    def _restore(self, snap: Dict[str, object]) -> None:
        if "primary" in snap:
            main, sub = snap["primary"]
            self.main_combo.setCurrentIndex(self.main_combo.findData(main) if main is not None else -1)
            self._fill_subs(main)
            if main is not None:
                self.sub_combo.setCurrentIndex(max(0, self.sub_combo.findData(sub)))
        if "rating" in snap:
            for b in self.rating_group.buttons():
                if b.property("rating") == snap["rating"]:
                    b.setChecked(True)
        for reg, state in snap.get("regions", {}).items():
            box = self.region_boxes[reg]
            box.setTristate(box.isTristate() or state == Qt.PartiallyChecked)
            box.setCheckState(state)
        if "identity" in snap:
            self.identity_box.setTristate(snap["identity"] == Qt.PartiallyChecked)
            self.identity_box.setCheckState(snap["identity"])
        for key, edit in (("secondary", self.secondary_edit), ("note", self.note_edit)):
            if key in snap:
                edit.setText(snap[key])

    def _show_header(self) -> None:
        self._fill_header()
        for label in (self.info, self.state):       # 할 말이 없으면 자리를 차지하지 않는다
            label.setVisible(bool(label.text()))

    def _fill_header(self) -> None:
        enabled = bool(self.names) and len(self.names) <= MAX_EDIT
        for w in (self.main_combo, self.sub_combo, self.secondary_edit, self.note_edit, self.identity_box,
                  *self.region_boxes.values(), *self.rating_group.buttons(), self.reset_btn):
            w.setEnabled(enabled)
        single = len(self.names) == 1
        self.wiki_btn.setVisible(single)
        self.posts_btn.setVisible(single)
        if not self.names:
            self.title.setText("태그를 고르세요")
            self.info.setText("")
            self.state.setText("")
            return
        if not single:
            shown = ", ".join(self.names[:8]) + (" …" if len(self.names) > 8 else "")
            self.title.setText(f"{len(self.names):,}개 선택")
            self.info.setText(shown)
            self.state.setText("" if enabled else f"한 번에 {MAX_EDIT}개까지 고칠 수 있습니다.")
            return
        name = self.names[0]
        e = self.store.by_name[name]
        row = self.store.row(name)
        self.title.setText(name)
        shown = [a for a in e.aliases if not a.startswith("/")]        # /lh 같은 Danbooru 검색 단축어는 뺀다
        aliases = f" · 별칭 {', '.join(shown[:6])}{' …' if len(shown) > 6 else ''}" if shown else ""
        if e.site:
            state = (f" · same_as {row['same_as']} — 분류를 고르면 same_as 를 지우고 직접 분류합니다" if row.get("same_as")
                     else "" if row else " — e621.csv 행 없음: 지금 분류는 자동(종류·Danbooru 별칭), 고치면 행이 생깁니다")
            self.info.setText(f"{e.site} {SITE_TYPE_LABELS.get(e.type, e.type)} · {e.count:,}회 "
                              f"(data/overlay/{e.site}.csv){aliases}{state}")
        elif e.db:
            fields = [f"{label} {row[k]}" for k, label in (("same_as", "대상"), ("models", "모델"),
                                                           ("use_instead", "대신 쓸 태그")) if row.get(k)]
            self.info.setText(" · ".join([f"확장 DB {db_label(e.db)}(data/extra/{e.db}.csv)"] + fields) + aliases
                              + (" — same_as 행은 대상의 분류를 씁니다(메모만 고침)" if row.get("same_as") else ""))
        else:
            self.info.setText(f"{TYPE_LABELS.get(e.type, e.type)} · {e.count:,}회{aliases}")
        if row:
            parts = [row.get("status") or "-", row.get("by") or ""]
            if row.get("confidence"):
                parts.append(f"신뢰도 {row['confidence']}")
            if row.get("missing_since"):
                parts.append(f"원본에서 사라짐({row['missing_since']})")
            self.state.setText("지금: " + " · ".join(p for p in parts if p))
        else:
            primary, _ = self.store.primary_of(e)
            if e.site:
                text = f"{e.site}.csv 행 없음 — " + ("자동 분류(종류·Danbooru 별칭) " + primary if primary else "미분류")
            else:
                text = "overlay 행 없음 — " + ("type 자동 분류 " + primary if primary else "미분류")
            self.state.setText("지금: " + text)

    def _fill(self, values: Dict[str, Optional[str]]) -> None:
        primary = values["primary"]
        if primary is None:
            self.main_combo.setCurrentIndex(-1)
            self._fill_subs(None)
        else:
            main, _, sub = primary.partition("/")
            self.main_combo.setCurrentIndex(self.main_combo.findData(main) if main else -1)
            self._fill_subs(main or None)
            self.sub_combo.setCurrentIndex(max(0, self.sub_combo.findData(sub)))
        rating = values["rating"]
        self.rating_group.setExclusive(False)
        for b in self.rating_group.buttons():
            b.setChecked(rating is not None and b.property("rating") == rating)
        self.rating_group.setExclusive(True)
        regions = values["regions"]
        sets = [set(fix.split_list(self.store.row(n).get("regions"))) for n in self.names]
        if regions is not None:
            chosen, mixed = set(fix.split_list(regions)), set()
        else:                                   # 모두에 있는 부위는 켜고, 일부에만 있는 부위는 ■
            chosen = set.intersection(*sets) if sets else set()
            mixed = set().union(*sets) - chosen
        for reg, box in self.region_boxes.items():
            box.setTristate(reg in mixed)       # 일부에만 있는 부위만 3상태(나머지는 한 번 누르면 바로 켜짐)
            box.setCheckState(Qt.PartiallyChecked if reg in mixed else Qt.Checked if reg in chosen else Qt.Unchecked)
        identity = values["identity"]
        self.identity_box.setTristate(identity is None)
        self.identity_box.setCheckState(Qt.PartiallyChecked if identity is None
                                        else Qt.Checked if identity == "1" else Qt.Unchecked)
        for edit, key in ((self.secondary_edit, "secondary"), (self.note_edit, "note")):
            edit.setText(values[key] or "")
            edit.setPlaceholderText("(여러 값)" if values[key] is None else edit.property("ph"))
        self._sync_enabled()

    def _fill_subs(self, main: Optional[str]) -> None:
        self._shown_main = main
        self.sub_combo.clear()
        if main is None:
            self.sub_combo.setCurrentIndex(-1)
            return
        self.sub_combo.addItem("(없음)", "")
        cat = next((c for c in self.store.categories if c["id"] == main), None)
        for s in (cat or {}).get("subs", ()):
            self.sub_combo.addItem(f"{s.get('label', s['id'])}  {s['id']}", s["id"])

    def _show_similar(self) -> None:
        self.similar_list.clear()
        if len(self.names) != 1:
            return
        for e, row in self.store.similar(self.names[0]):
            text = f"{e.name}  —  {row['primary']} · {theme.rating_name(row.get('rating', '')) or '-'}"
            if row.get("regions"):
                text += f" · {row['regions'].replace('|', ', ')}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, e.name)
            item.setToolTip(f"{e.count:,}회 · {row.get('status')}")
            self.similar_list.addItem(item)
        if not self.similar_list.count():
            self.similar_list.addItem("(분류된 같은 계열 태그 없음)")

    # ---- 입력 ----

    def _touch(self, key: str) -> None:
        if self._loading:
            return
        self.dirty.add(key)
        self._sync_enabled()
        self._mark_dirty()
        self._timer.start()

    def _main_activated(self, _index: int) -> None:
        main = self.main_combo.currentData()
        if main != self._shown_main:        # 같은 메인을 다시 고르면 서브를 지우지 않는다
            self._fill_subs(main)
            self.sub_combo.setCurrentIndex(0)
        self._touch("primary")

    def _region_clicked(self, region: str) -> None:
        # 여러 태그: 누른 부위만 바꾼다 — 켜면 모두에 더하고, 끄면 모두에서 빼고, '일부만'(■)으로 돌리면 그대로
        self.touched_regions.add(region)
        self._touch("regions")

    def _identity_clicked(self) -> None:
        if self.identity_box.isTristate():
            self.identity_box.setTristate(False)
        self._touch("identity")

    def _is_etc(self) -> bool:
        return self.main_combo.currentData() == "etc"

    def _sync_enabled(self) -> None:
        if not self.names or len(self.names) > MAX_EDIT:
            return
        # 확장 DB 의 same_as 행은 대상의 분류를 쓴다 — 메모만. e621 의 same_as 행은 분류를 주면 same_as 를 지운다
        locked = any(self.store.row(n).get("same_as") and not self.store.is_site(n) for n in self.names)
        self.main_combo.setEnabled(not locked)
        etc = self._is_etc() or locked
        for w in (self.sub_combo, self.secondary_edit, self.identity_box,
                  *self.region_boxes.values(), *self.rating_group.buttons()):
            w.setEnabled(not etc)
        self.sub_combo.setEnabled(not etc and self.main_combo.currentIndex() >= 0)
        self.identity_box.setEnabled(not etc and self._identity_applies() is not False)

    def _primary_value(self) -> Optional[str]:
        main = self.main_combo.currentData()
        if main is None:
            return None
        sub = self.sub_combo.currentData() or ""
        return f"{main}/{sub}" if sub else main

    def _identity_applies(self) -> Optional[bool]:
        """identity 를 쓸 수 있는 분류인가. 메인이 '(여러 값)'이면 고른 태그가 모두 그런 분류일 때만."""
        primary = self._primary_value()
        if primary is not None:
            return _identity_ok(primary)
        if not self.names:
            return None
        return all(_identity_ok(self.store.row(n).get("primary", "")) for n in self.names)

    def _values(self) -> Dict[str, Optional[str]]:
        """폼의 값. 여러 값 상태로 둔 칸은 None."""
        checked = self.rating_group.checkedButton()
        regions = None
        if not any(b.checkState() == Qt.PartiallyChecked for b in self.region_boxes.values()):
            regions = "|".join(r for r, b in self.region_boxes.items() if b.isChecked())
        applies = self._identity_applies()
        if applies is False:
            identity = ""
        elif self.identity_box.checkState() == Qt.PartiallyChecked:
            identity = None
        else:
            identity = "1" if self.identity_box.isChecked() else "0"
        return {
            "primary": self._primary_value(),
            "rating": checked.property("rating") if checked else None,
            "regions": regions,
            "identity": identity,
            "secondary": "|".join(fix.split_list(self.secondary_edit.text())),
            "note": self.note_edit.text().strip(),
        }

    def updates(self) -> Dict[str, str]:
        """apply_fix 에 넘길 수정 내용."""
        values = self._values()
        if self._is_etc():
            keys = ["note"]
        else:
            keys = list(fix.EDITABLE)
        if len(self.names) == 1:
            row = self.store.row(self.names[0])
            out = {}
            for k in keys:
                if values[k] is None or _same(k, values[k], row.get(k) or ""):
                    continue
                if k == "identity" and "identity" not in self.dirty:
                    continue        # 폼이 값을 정규화(0/빈 값)해서 생기는 차이는 건드린 것이 아니다
                out[k] = values[k]
            # 메인을 바꾸면서 identity 가 해당 없게 되면 지운다(body → clothes 등)
            if "primary" in out and self._identity_applies() is False and row.get("identity"):
                out["identity"] = ""
            return out
        touched = set(self.dirty) | ({"identity"} if "primary" in self.dirty and self._identity_applies() is False
                                      else set())
        touched.discard("regions")              # 여러 태그의 부위는 plan() 이 태그마다 계산한다
        return {k: values[k] for k in keys if k in touched and values[k] is not None}

    def plan(self) -> List[Tuple[List[str], Dict[str, str]]]:
        """(태그 묶음, 수정 내용) 목록. 보통 한 묶음이고, 여러 태그의 부위를 바꿀 때만 결과 부위별로 나뉜다."""
        updates = self.updates()
        if len(self.names) > 1 and "regions" in self.dirty and not self._is_etc():
            states = {r: self.region_boxes[r].checkState() for r in self.touched_regions}
            add = [r for r, st in states.items() if st == Qt.Checked]
            drop = [r for r, st in states.items() if st == Qt.Unchecked]
            return self.store.regions_plan(self.names, updates, add, drop)
        return [(self.names, updates)]

    def _mark_dirty(self) -> None:
        keys = self.dirty | ({"primary"} if "sub" in self.dirty else set())
        for key, label in self.field_labels.items():
            on = key in keys or (key == "sub" and "primary" in keys)
            if bool(label.property("dirty")) != on:
                label.setProperty("dirty", on)          # 강조색(스타일시트)
                theme.repolish(label)

    # ---- 미리보기·저장 ----

    def _update_preview(self) -> None:
        if not self.names:
            self.message.setText("")
            self.save_btn.setText("저장")
            self.save_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        plan = self.plan()
        res = self.store.preview_plan(plan, etc=self._is_etc())
        self._last = res
        parts = []
        if res.errors:
            parts.append(theme.span("저장할 수 없음", "danger", bold=True) + "<br>" +
                         "<br>".join(theme.span(f"• {html.escape(e)}", "danger") for e in res.errors))
        notes = [n for n in res.notes if not n.endswith("바뀐 것 없음")]
        if notes:
            parts.append("<br>".join(theme.span(f"참고: {html.escape(n)}", "warning") for n in notes))
        if not res.errors:
            if not res.changes:
                parts.append(theme.span("바뀐 것 없음 (이미 reviewed)", "muted"))
            elif len(self.names) == 1:
                _, before, after = res.changes[0]
                parts.append(theme.span(f"전: {html.escape(fix.format_row(before))}", "muted") +
                             f"<br><b>후: {html.escape(fix.format_row(after))}</b>")
            else:
                parts.append(f"<b>{len(res.changes):,}개를 고칩니다</b> (나머지 {len(self.names) - len(res.changes):,}개는 바뀐 것 없음)")
        self.message.setText("<br><br>".join(parts))
        ok = not res.errors and bool(res.changes)
        self.save_btn.setEnabled(ok)
        self.next_btn.setEnabled(ok or (not res.errors and bool(self.names)))
        lock_only = ok and not any(updates for _, updates in plan)
        self.save_btn.setText("확인(잠금)" if lock_only else "저장")
        self.save_btn.setToolTip("바꾼 것 없이 reviewed 로 잠급니다(자동 단계가 덮어쓰지 않음) · Ctrl+S" if lock_only
                                 else "Ctrl+S")

    def save(self, advance: bool) -> None:
        if self.before_save is not None and self.before_save():
            return                              # 목록에서 다른 태그를 고르는 중이었다 — 그 태그를 먼저 불러왔다
        if not self.names:
            return
        self._timer.stop()
        plan, etc = self.plan(), self._is_etc()
        res = self.store.preview_plan(plan, etc=etc)
        if res.errors:
            self._update_preview()
            return
        if not res.changes:
            if advance:
                self.saved.emit([], True)       # 바뀐 것이 없으면 저장 없이 다음 태그로
            return
        try:
            res = self.store.apply_plan(plan, etc=etc)
        except ConflictError as e:
            QMessageBox.warning(self, "다른 곳에서 바뀜", f"{e}\n\n지금 다시 불러옵니다. 입력한 값은 폼에 남아 있으니 다시 저장하세요.")
            self.window().reload()
            return
        except OSError as e:
            QMessageBox.critical(self, "저장 실패", f"overlay 를 쓰지 못했습니다: {e}\n\n엑셀 등 다른 프로그램이 파일을 열고 "
                                                    "있으면 닫고 다시 저장하세요. 아무것도 바꾸지 않았습니다.")
            return
        if res.errors:
            self._update_preview()
            return
        self.saved.emit([n for n, _, _ in res.changes], advance)

    def _take_precedent(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.UserRole)
        if not name or not self.names:
            return
        row = self.store.row(name)
        values = {k: row.get(k, "") for k in fix.EDITABLE}
        values["note"] = self.note_edit.text()
        self._loading = True
        try:
            self._fill(values)
        finally:
            self._loading = False
        self.dirty |= {"primary", "rating", "regions", "identity", "secondary"}
        self._sync_enabled()
        self._mark_dirty()
        self._update_preview()

    def _open_danbooru(self, path: str) -> None:
        if len(self.names) == 1:
            name = extra.evidence_name(self.names[0])
            QDesktopServices.openUrl(QUrl(f"https://danbooru.donmai.us/{path}{quote(name, safe='')}"))
