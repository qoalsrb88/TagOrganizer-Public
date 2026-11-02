"""왼쪽 탭 — 태그 목록(`tagorg list` 대신), 보류(etc) 작업, 점검(`tagorg check`·`lint`)."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QTableView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tools import fix
from tools.tagdb import STATUSES

from . import theme
from .models import COL_COUNT, COL_NAME, COL_PRIMARY, COL_RATING, COL_REGIONS, COL_STATUS, COL_TYPE, TagTableModel
from .store import (AUTO, EXTRA_ALL, ORIGINAL, SITE_ALL, SITE_TYPE_LABELS, TYPE_LABELS, UNCLASSIFIED, Query, Store,
                    db_label)


class TagListPage(QWidget):
    """태그 표와 필터. queue=True 이면 보류(etc) 태그만 사용량 순으로 보여 주는 작업 목록이다."""

    selection_changed = Signal(list)
    add_requested = Signal(list)        # "+ 새 태그" — 확장 DB 에 더할 이름(빈 목록이면 대화상자에서 적음)

    def __init__(self, store: Store, queue: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.queue = queue
        self._sel_timer = QTimer(self, singleShot=True, interval=40)
        self._sel_timer.timeout.connect(lambda: self.selection_changed.emit(self.selected_names()))
        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(lambda: self.refresh(keep=False))
        self._build()
        self.refresh(keep=False)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[1], 0)
        layout.setSpacing(theme.SPACE[1])
        bar = QHBoxLayout()
        bar.setSpacing(theme.SPACE[1])
        self.search = QLineEdit(clearButtonEnabled=True)
        self.search.setPlaceholderText("찾기: 이름·별칭·메모 (공백·대문자·프롬프트 표기 그대로 써도 됨)  Ctrl+F")
        self._search_icon = self.search.addAction(theme.icon("search", theme.tokens()["muted"]),
                                                  QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        bar.addWidget(self.search, 3)
        self.type_combo = self._combo([("일반", 0), ("전체 종류", None)] +
                                      [(TYPE_LABELS[t], t) for t in (1, 3, 4, 5, -1)] +
                                      [("확장 DB 전부", EXTRA_ALL)] +
                                      [(f"확장: {db_label(db)}", f"extra:{db}") for db in self.store.extra_files] +
                                      self._site_items())
        self.type_combo.setToolTip("종류(원본 태그) 또는 출처(확장 DB — 원본에 없는 모델·폐기·내 태그, e621 — e621 에만 "
                                   "있는 태그)")
        self.main_combo = self._combo([("전체 분류", "")] +
                                      [(f"{c.get('label', c['id'])} {c['id']}", c["id"]) for c in self.store.categories] +
                                      [("보류 etc", "etc"), ("미분류", UNCLASSIFIED)])
        self.sub_combo = self._combo([("전체 서브", "")])
        self.status_combo = self._combo([("전체 상태", "")] + [(s, s) for s in STATUSES] + [("자동(type)", AUTO)])
        self.rating_combo = theme.rating_items(self._combo(
            [("전체 등급", None), ("등급 없음", "")] + [(name, r) for r, name in theme.RATING_NAMES.items()]))
        self.main_combo.currentIndexChanged.connect(self._main_changed)
        for combo in (self.type_combo, self.main_combo, self.sub_combo, self.status_combo, self.rating_combo):
            combo.setVisible(not self.queue)
            bar.addWidget(combo)
        self.count_label = theme.caption("", "muted")
        bar.addWidget(self.count_label)
        if not self.queue:
            self.add_btn = theme.decorate(QPushButton("새 태그"), "tag")
            self.add_btn.setToolTip("원본에 없는 태그를 확장 DB 에 더합니다(이름만 — 분류는 더한 뒤 편집 패널에서)")
            self.add_btn.clicked.connect(lambda: self.add_requested.emit([]))
            bar.addWidget(self.add_btn)
        layout.addLayout(bar)
        if self.queue:
            hint = theme.caption("뜻이 불확실해 분류를 보류한 태그입니다(사용량 순). 분류해 저장하면 목록에서 빠지고, "
                                 "'저장 후 다음'(Ctrl+Enter)은 바로 다음 태그로 넘어갑니다. 계속 보류하려면 메모만 고쳐도 "
                                 "됩니다.", "muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        self.model = TagTableModel(self.store, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(26)
        self.view.setShowGrid(False)
        header = self.view.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in ((COL_NAME, 240), (COL_TYPE, 56), (COL_COUNT, 76), (COL_PRIMARY, 170),
                           (COL_REGIONS, 130), (COL_STATUS, 76)):
            self.view.setColumnWidth(col, width)
        if self.queue:      # 보류 목록은 분류 칸이 모두 비어 있으니 메모(뜻 짐작)를 넓게
            for col in (COL_TYPE, COL_PRIMARY, COL_RATING, COL_REGIONS):
                self.view.setColumnHidden(col, True)
            self.view.setColumnWidth(COL_NAME, 260)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(COL_COUNT, Qt.DescendingOrder)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._sel_timer.start())
        layout.addWidget(self.view, 1)
        for combo in (self.type_combo, self.sub_combo, self.status_combo, self.rating_combo):
            combo.currentIndexChanged.connect(lambda _: self.refresh(keep=False))

    @staticmethod
    def _combo(items) -> QComboBox:
        combo = QComboBox()
        for text, data in items:
            combo.addItem(text, data)
        return combo

    def _main_changed(self, _index: int) -> None:
        main = self.main_combo.currentData()
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        self.sub_combo.addItem("전체 서브", "")
        cat = next((c for c in self.store.categories if c["id"] == main), None)
        for s in (cat or {}).get("subs", ()):
            self.sub_combo.addItem(f"{s.get('label', s['id'])} {s['id']}", s["id"])
        self.sub_combo.setEnabled(cat is not None)
        self.sub_combo.blockSignals(False)
        self.refresh(keep=False)

    def restyle(self) -> None:
        """테마를 바꾼 뒤: 표 글자색(모델이 토큰에서 읽음)과 검색 아이콘을 다시 그린다."""
        self._search_icon.setIcon(theme.icon("search", theme.tokens()["muted"]))
        self.view.viewport().update()

    # ---- 내용 ----

    def query(self) -> Query:
        if self.queue:      # 원본 태그의 보류만(확장 DB 의 보류는 대부분 동명이인 이름이라 태그 목록에서)
            return Query(text=self.search.text(), type=ORIGINAL, primary="etc")
        main, sub = self.main_combo.currentData(), self.sub_combo.currentData()
        return Query(text=self.search.text(), type=self.type_combo.currentData(),
                     primary=f"{main}/{sub}" if main and sub else main or "",
                     status=self.status_combo.currentData() or "", rating=self.rating_combo.currentData())

    def refresh(self, keep: bool = True, notify: bool = True) -> None:
        """필터를 다시 돌린다. keep 이면 고른 태그를 (남아 있는 만큼) 그대로 고른다.

        notify=False 면 편집 패널에 선택 변경을 알리지 않는다(저장 직후 패널이 고친 태그를 계속 보여 주게).
        """
        names = self.selected_names() if keep else []
        current = self.current_name() if keep else None
        entries = self.store.find(self.query())
        self.view.selectionModel().blockSignals(not notify)
        try:
            self.model.set_entries(entries)
            header = self.view.horizontalHeader()
            if header.sortIndicatorSection() != COL_COUNT or header.sortIndicatorOrder() != Qt.DescendingOrder:
                self.model.sort(header.sortIndicatorSection(), header.sortIndicatorOrder())
            self.count_label.setText(f"{len(entries):,}개")
            if names:
                self.select_names(names, current)
        finally:
            self.view.selectionModel().blockSignals(False)
        if notify:
            self._sel_timer.start()
        self.view.viewport().update()

    def _site_items(self):
        """종류 필터의 e621 항목(sources.json 에 e621 이 있을 때)."""
        site = self.store.site
        if site is None:
            return []
        return ([(f"{site.id} 전부", SITE_ALL)] +
                [(f"{site.id}: {label}", f"{SITE_ALL}:{t}") for t, label in SITE_TYPE_LABELS.items()])

    def show_db(self, db: str, names: Sequence[str]) -> None:
        """확장 DB 하나만 보이게 거르고 그 태그들을 고른다(새 태그를 더한 뒤)."""
        data = f"extra:{db}"
        if self.type_combo.findData(data) < 0:
            self.type_combo.addItem(f"확장: {db_label(db)}", data)
        for combo in (self.type_combo, self.main_combo, self.status_combo, self.rating_combo):
            combo.blockSignals(True)
        self.type_combo.setCurrentIndex(self.type_combo.findData(data))
        self.main_combo.setCurrentIndex(0)
        self.status_combo.setCurrentIndex(0)
        self.rating_combo.setCurrentIndex(0)
        for combo in (self.type_combo, self.main_combo, self.status_combo, self.rating_combo):
            combo.blockSignals(False)
        self._main_changed(0)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh(keep=False, notify=False)
        self.select_names(names)
        self._sel_timer.start()

    def selected_names(self) -> List[str]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        return [self.model.entries[r].name for r in rows if r < len(self.model.entries)]

    def current_name(self) -> Optional[str]:
        e = self.model.entry(self.view.currentIndex().row())
        return e.name if e else None

    def current_row(self) -> int:
        return self.view.currentIndex().row()

    def flush_selection(self) -> bool:
        """편집 패널에 아직 알리지 않은 선택 변경이 있으면 지금 알린다. 있었으면 True."""
        if not self._sel_timer.isActive():
            return False
        self._sel_timer.stop()
        self.selection_changed.emit(self.selected_names())
        return True

    def select_names(self, names: Sequence[str], current: Optional[str] = None) -> None:
        sel = self.view.selectionModel()
        sel.clearSelection()
        rows = [self.model.row_of(n) for n in names]
        rows = [r for r in rows if r >= 0]
        for r in rows:
            sel.select(self.model.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        cur = self.model.row_of(current) if current else -1
        cur = cur if cur >= 0 else (rows[0] if rows else -1)
        if cur >= 0:
            sel.setCurrentIndex(self.model.index(cur, 0), QItemSelectionModel.NoUpdate)
            self.view.scrollTo(self.model.index(cur, 0))

    def select_row(self, row: int) -> None:
        if not self.model.entries:
            self.view.selectionModel().clearSelection()
            self._sel_timer.start()
            return
        row = max(0, min(row, len(self.model.entries) - 1))
        self.view.selectRow(row)
        self.view.scrollTo(self.model.index(row, 0))

    def after_save(self, saved: Sequence[str], advance: bool) -> None:
        """저장·되돌리기 뒤 정리. 보류 목록은 분류된 태그가 빠지도록 다시 거르고, 태그 목록은 그 행만 다시 그린다
        (필터에서 벗어나도 다음에 거를 때까지 남겨 둔다). advance 면 다음 태그를 골라 편집 패널에 알린다."""
        # 다음 태그는 거르기 전에 이름으로 정해 둔다: 지금 행 뒤에서 이번에 고르거나 고친 것이 아닌 첫 태그
        # (여러 개를 저장해 목록에서 빠지면 행 번호가 당겨져 하나를 건너뛰므로 번호로 정하지 않는다)
        skip = set(saved) | set(self.selected_names())
        following = self.model.entries[self.current_row() + 1:] if advance else []
        nxt = next((e.name for e in following if e.name not in skip), None)
        if self.queue:
            self.refresh(keep=True, notify=False)
        else:
            self.model.refresh_names(saved)
        if not advance:
            return
        row = self.model.row_of(nxt) if nxt else -1
        self.select_row(row if row >= 0 else len(self.model.entries) - 1)


class AddExtraDialog(QDialog):
    """확장 DB 에 새 태그 — 이름만 넣고(원본·확장 DB 에 이미 있으면 건너뜀), 분류는 더한 뒤 편집 패널에서."""

    def __init__(self, store: Store, names: Sequence[str] = (), parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("확장 DB 에 새 태그")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.db_combo = QComboBox(editable=True)
        dbs = list(dict.fromkeys(["user", *store.extra_files]))
        for db in dbs:
            self.db_combo.addItem(f"{db} — {db_label(db)}" if db_label(db) != db else db, db)
        self.db_combo.setToolTip("data/extra/<DB>.csv. 새 이름을 적으면 새 DB 파일을 만듭니다(영문 소문자·숫자·밑줄)")
        self.names_edit = QPlainTextEdit("\n".join(names))
        self.names_edit.setPlaceholderText("한 줄에 하나(쉼표로 나눠도 됨). 공백·밑줄·대문자는 그대로 적어도 됩니다.")
        self.names_edit.setMinimumSize(420, 160)
        form.addRow("DB", self.db_combo)
        form.addRow("이름", self.names_edit)
        layout.addLayout(form)
        hint = theme.caption("이름은 소문자·공백으로 저장합니다(Cinematic_Lighting → cinematic lighting). 원본 태그·별칭이나 "
                             "이미 확장 DB 에 있는 이름은 건너뜁니다.", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        """(DB ID, 이름 목록)."""
        text = self.db_combo.currentText().strip()
        index = self.db_combo.findText(text)
        db = self.db_combo.itemData(index) if index >= 0 else text.split(" ")[0]
        names = [n.strip() for line in self.names_edit.toPlainText().splitlines() for n in line.split(",")]
        return db, [n for n in names if n]


class CheckPage(QWidget):
    """overlay 점검: 빌드와 같은 행 검사, 마지막 커밋 대비 바뀐 행, 수식어 쌍 불일치(lint)."""

    open_names = Signal(list)

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.stale = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[1], 0)
        layout.setSpacing(theme.SPACE[1])
        bar = QHBoxLayout()
        self.run_btn = theme.decorate(QPushButton("다시 점검"), "refresh")
        self.run_btn.clicked.connect(self.run)
        self.summary = QLabel()
        bar.addWidget(self.run_btn)
        bar.addWidget(self.summary, 1)
        layout.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["항목", "커밋 / 기준", "지금 / 차이"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 330)
        self.tree.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.tree, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.stale:
            QTimer.singleShot(0, self.run)

    def mark_stale(self) -> None:
        self.stale = True
        if self.isVisible():
            self.run()

    def run(self) -> None:
        selected = {n for item in self.tree.selectedItems() for n in (item.data(0, Qt.UserRole) or ())}
        report = self.store.check()
        self.stale = False
        self.tree.clear()
        if report.errors:
            self.summary.setText(theme.span(f"이상 {len(report.errors)}건", "danger", bold=True)
                                 + " — 고치기 전에는 빌드가 멈춥니다")
        else:
            self.summary.setText(f"이상 없음 — overlay {len(self.store.overlay):,}행 · 확장 DB "
                                 f"{len(self.store.extra_rows):,}행")
        if report.errors:
            group = self._group(f"오류 {len(report.errors)}건", expanded=True)
            for e in report.errors:
                item = QTreeWidgetItem(group, [e])
                item.setForeground(0, theme.color("danger"))
                item.setFirstColumnSpanned(True)
            group.setForeground(0, theme.color("danger"))
        group = self._group("참고", expanded=True)
        for line in report.info:
            QTreeWidgetItem(group, [line]).setFirstColumnSpanned(True)
        group = self._group(f"마지막 커밋 대비 바뀐 행 {len(report.diff):,}개 — 커밋 전에 볼 목록", expanded=True)
        for name, old, new in report.diff:
            item = QTreeWidgetItem(group, [name, fix.format_row(old) if old else "(없음)",
                                           fix.format_row(new) if new else "(지워짐)"])
            item.setData(0, Qt.UserRole, [name])
            item.setToolTip(2, item.text(2))
        group = self._group(f"수식어 쌍 불일치 {len(report.lint):,}개 — 의도한 차이일 수 있음(다시 볼 목록)",
                            expanded=not report.diff)
        for name, base, diffs in report.lint:
            item = QTreeWidgetItem(group, [name, f"vs {base}", "; ".join(diffs)])
            item.setData(0, Qt.UserRole, [name])
            item.setToolTip(2, item.text(2))
        if selected:
            self._reselect(selected)

    def _group(self, text: str, expanded: bool) -> QTreeWidgetItem:
        group = QTreeWidgetItem(self.tree, [text])
        group.setFirstColumnSpanned(True)
        f = group.font(0)
        f.setBold(True)
        group.setFont(0, f)
        group.setExpanded(expanded)
        return group

    def _reselect(self, names) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if set(item.data(0, Qt.UserRole) or ()) & names:
                    item.setSelected(True)
        self.tree.blockSignals(False)

    def _selected(self) -> None:
        names = [n for item in self.tree.selectedItems() for n in (item.data(0, Qt.UserRole) or ())]
        if names:
            self.open_names.emit(list(dict.fromkeys(names)))

    def selected_names(self) -> List[str]:
        return [n for item in self.tree.selectedItems() for n in (item.data(0, Qt.UserRole) or ())]
