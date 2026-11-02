"""태그 표 모델 — 22만 행을 그대로 담는다(거르기·정렬은 store.find 와 Python 정렬로 하고 표는 보여 주기만)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap

from . import theme
from .store import AUTO, EXTRA_SHORT, SITE_TYPE_LABELS, TYPE_LABELS, Entry, Store, db_label

COLUMNS = ("태그", "종류", "사용량", "분류", "등급", "부위", "상태", "메모")
COL_NAME, COL_TYPE, COL_COUNT, COL_PRIMARY, COL_RATING, COL_REGIONS, COL_STATUS, COL_NOTE = range(len(COLUMNS))
STATUS_LABELS = {AUTO: "자동(type)", "": "미분류"}


class TagTableModel(QAbstractTableModel):
    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.entries: List[Entry] = []
        self._rows: Dict[str, int] = {}
        self._icons: Dict[str, QIcon] = {}

    # ---- 내용 ----

    def set_entries(self, entries: Sequence[Entry]) -> None:
        self.beginResetModel()
        self.entries = list(entries)
        self._rows = {e.name: i for i, e in enumerate(self.entries)}
        self.endResetModel()

    def entry(self, row: int) -> Optional[Entry]:
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def row_of(self, name: str) -> int:
        return self._rows.get(name, -1)

    def refresh_names(self, names) -> None:
        """고친 태그의 행만 다시 그린다(필터는 다시 돌리지 않음 — 행이 갑자기 사라지지 않게)."""
        for name in names:
            row = self._rows.get(name)
            if row is not None:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    # ---- Qt ----

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return self._text(e, col)
        if role == Qt.TextAlignmentRole and col == COL_COUNT:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if col == COL_RATING:
                return theme.rating_color(self.store.row(e.name).get("rating", ""))
            primary, status = self.store.primary_of(e)
            if col in (COL_PRIMARY, COL_STATUS) and status in (AUTO, ""):
                return theme.color("muted")
            if col == COL_PRIMARY and primary == "etc":
                return theme.color("muted")
        if role == Qt.DecorationRole and col == COL_PRIMARY:
            primary, _ = self.store.primary_of(e)
            return self._icon(primary.partition("/")[0]) if primary else None
        if role == Qt.ToolTipRole:
            if col == COL_PRIMARY:
                primary, _ = self.store.primary_of(e)
                return self.store.labels.get(primary, primary) or None
            if col == COL_NOTE:
                return self.store.row(e.name).get("note") or None
            if col == COL_NAME and e.site:
                row = self.store.row(e.name)
                lines = [f"{e.site} {SITE_TYPE_LABELS.get(e.type, e.type)} — data/overlay/{e.site}.csv"]
                lines += [f"same_as: {row['same_as']}"] if row.get("same_as") else []
                lines += [] if row else ["e621.csv 행 없음 — 지금 분류는 자동(종류·Danbooru 별칭)"]
                shown = [a for a in e.aliases if not a.startswith("/")]
                return "\n".join(lines + (["별칭: " + ", ".join(shown[:20])] if shown else []))
            if col == COL_NAME and e.db:
                row = self.store.row(e.name)
                lines = [f"확장 DB {db_label(e.db)}"] + [f"{k}: {row[k]}" for k in
                                                       ("same_as", "models", "use_instead", "aliases") if row.get(k)]
                return "\n".join(lines)
            shown = [a for a in e.aliases if not a.startswith("/")] if col == COL_NAME else []
            if shown:
                return "별칭: " + ", ".join(shown[:20])
        return None

    def _text(self, e: Entry, col: int) -> str:
        row = self.store.row(e.name)
        if col == COL_NAME:
            return e.name
        if col == COL_TYPE:
            if e.site:
                return f"{e.site} {SITE_TYPE_LABELS.get(e.type, e.type)}"
            return EXTRA_SHORT.get(e.db, e.db) if e.db else TYPE_LABELS.get(e.type, str(e.type))
        if col == COL_COUNT:
            return "" if e.db else f"{e.count:,}"
        if col == COL_PRIMARY:
            primary, _ = self.store.primary_of(e)
            text = "보류(etc)" if primary == "etc" else primary or "(미분류)"
            return f"{text} (= {row['same_as']})" if row.get("same_as") else text
        if col == COL_RATING:
            return theme.rating_name(row.get("rating", ""))
        if col == COL_REGIONS:
            return row.get("regions", "").replace("|", ", ")
        if col == COL_STATUS:
            _, status = self.store.primary_of(e)
            return STATUS_LABELS.get(status, status)
        if col == COL_NOTE:
            return row.get("note", "")
        return ""

    def _icon(self, main: str) -> Optional[QIcon]:
        if main not in self._icons:
            pix = QPixmap(10, 10)
            pix.fill(QColor(self.store.colors.get(main) or self.store.colors.get("etc") or theme.tokens()["muted"]))
            self._icons[main] = QIcon(pix)
        return self._icons[main]

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        reverse = order == Qt.DescendingOrder
        if column == COL_COUNT:
            key = lambda e: (e.count, e.name)                       # noqa: E731
        elif column == COL_NAME:
            key = lambda e: e.name                                  # noqa: E731
        elif column == COL_RATING:                                  # 글자 순이 아니라 general → explicit
            key = lambda e: (theme.RATING_ORDER.get(self.store.row(e.name).get("rating", ""), 9), -e.count)  # noqa: E731
        else:
            key = lambda e: (self._text(e, column), -e.count)       # noqa: E731
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()                            # 선택·현재 행이 정렬 뒤에도 같은 태그를 가리키게
        names = [self.entries[i.row()].name for i in old]
        self.entries.sort(key=key, reverse=reverse)
        self._rows = {e.name: i for i, e in enumerate(self.entries)}
        self.changePersistentIndexList(old, [self.index(self._rows[n], i.column()) for n, i in zip(names, old)])
        self.layoutChanged.emit()
