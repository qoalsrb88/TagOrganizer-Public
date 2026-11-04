"""청크 편집 화면 — NovelAI 청크처럼 줄마다 분류 색 묶음을 놓고, 끌거나 키보드로 순서·줄바꿈을 정한다.

- 한 줄 = 출력 한 줄. 줄 안의 청크는 ', ' 로 이어진다. 왼쪽 숫자가 줄 번호.
- 청크를 펼치면(▸ 누르기·더블클릭·Space) 안의 서브(멤버)가 보인다. 서브를 끌어 순서를 바꾸면(= 청크 안 태그 순서),
  다른 청크에 놓으면 옮겨지고, 청크 밖(줄 사이·다른 청크 옆)에 놓으면 새 청크가 된다.
- 키보드: ←→ 고르기(펼친 청크는 → 로 서브에 들어감), ↑↓ 위·아래 줄, Ctrl+←→ 옮기기, Ctrl+↑↓ 다른 줄로,
  Enter 앞에서 줄바꿈(서브면 새 청크로 분리), Shift+Enter 뒤에서 줄바꿈, Backspace 앞 줄과 합치기,
  Space 펼치기·접기, Delete 숨기기(그 청크의 태그를 뺌), F2 이름, Ctrl+Z·Ctrl+Y 되돌리기·다시.
- 색은 분류 체계(taxonomy) 색을 옅게 칠한다. 화면 색은 테마 토큰만 쓴다.
모델은 gui/arrangement.py 의 Model. 바꾸면 changed(바꾸기 전 JSON) 를 보낸다 — 패널이 되돌리기 기록과 미리보기를 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QInputDialog, QMenu, QScrollArea, QSizePolicy, QToolTip, QWidget,
)

import bmk_tag_organizer_core as core

from . import theme
from .arrangement import Model

GUTTER = 28        # 줄 번호 칸
MARGIN = 8
H = 30             # 청크 한 줄 높이
MH = 22            # 서브 알약 높이
PAD = 10
GAP = 6            # 청크 사이
ROW_GAP = 12       # 줄 사이
GAP_H = 20         # 빈 줄 표시 띠
WRAP_GAP = 4       # 한 줄이 넘쳐 접힐 때
WRAP_INDENT = 16
MGAP = 4           # 서브 사이
MPAD = 8
ARROW = 14
EDGE = 5           # 줄 위·아래 가장자리: 여기에 놓으면 새 줄


@dataclass
class _Box:
    cid: str
    mi: int                     # -1: 청크, 0..: 서브(멤버) 순번
    rect: QRectF
    row: int
    pos: int
    arrow: Optional[QRectF] = None


class ChunkCanvas(QWidget):
    changed = Signal(str)               # 바꾸기 전 모델 JSON
    selection_changed = Signal(str)     # 고른 청크 id(없으면 "")
    edit_custom = Signal(str)           # 커스텀 청크 편집 요청
    new_custom = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model: Optional[Model] = None
        self.taxonomy: Mapping[str, Any] = {}
        self.counts: Dict[str, Tuple[int, int, str]] = {}       # id → (남음, 뺌, 설명)
        self.expanded: Set[str] = set()
        self.sel: Optional[Tuple[str, int]] = None
        self._boxes: List[_Box] = []
        self._bands: List[Tuple[float, float]] = []
        self._gap_bands: Dict[int, QRectF] = {}
        self._press: Optional[Tuple[QPointF, Optional[_Box]]] = None
        self._drag: Optional[_Box] = None
        self._drop: Optional[tuple] = None
        self._mouse = QPointF()
        self._width = -1
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setAccessibleName("청크 배열")

    # ---- 바깥에서 ----

    def set_model(self, model: Model, taxonomy: Mapping[str, Any]) -> None:
        self.model, self.taxonomy = model, taxonomy
        self.refresh()

    def set_counts(self, counts: Dict[str, Tuple[int, int, str]]) -> None:
        if counts != self.counts:
            self.counts = counts
            self.refresh()

    def refresh(self) -> None:
        """모델이 바뀌었다(되돌리기 등). 사라진 청크의 선택·펼침을 지우고 다시 배치한다."""
        if self.model is not None:
            self.expanded &= set(self.model.chunks)
            if self.sel and (self.sel[0] not in self.model.chunks or self.sel[1] >= len(self.model.members(self.sel[0]))):
                self.sel = (self.sel[0], -1) if self.sel[0] in self.model.chunks else None
        self._relayout()
        self.update()

    def set_all_expanded(self, on: bool) -> None:
        if self.model is not None:
            self.expanded = {c for c in self.model.chunks if self.model.members(c)} if on else set()
            if not on and self.sel:
                self.sel = (self.sel[0], -1)
            self.refresh()

    def select(self, cid: str, mi: int = -1) -> None:
        self.sel = (cid, mi) if cid else None
        self.update()
        self._ensure_visible()
        self.selection_changed.emit(cid or "")

    # ---- 표시 이름 ----

    def chunk_label(self, cid: str) -> str:
        label = core.chunk_label(self.model.chunks[cid], self.taxonomy) or cid
        return ("★ " + label) if self.model.is_custom(cid) else label

    def _chip_label(self, cid: str) -> str:
        """청크 칩에 쓰는 이름. 줄 중간 청크의 빈 줄은 띠로 그릴 수 없어 ↵ 로 표시한다."""
        r, p = self.model.where(cid)
        mid_gap = self.model.chunks[cid].get("gap_after") and p < len(self.model.rows[r]) - 1
        return self.chunk_label(cid) + (" ↵" if mid_gap else "")

    def member_label(self, cid: str, member: str) -> str:
        label = core.selector_label(member, self.taxonomy)
        sel = core.Selector.parse(member)
        if not sel.sub and sel.main != "*":
            siblings = [core.Selector.parse(m) for m in self.model.members(cid)]
            if any(s.main == sel.main and s.sub for s in siblings):
                label += " 그 밖"            # 서브 없이 메인만 붙은 태그(와 다른 청크에 없는 서브)
        return label

    def _color(self, selector_or_spec) -> QColor:
        if isinstance(selector_or_spec, str):
            spec = {"members": [selector_or_spec]}
        else:
            spec = selector_or_spec
        return QColor(core.chunk_color(spec, self.taxonomy) or theme.tokens()["muted"])

    # ---- 배치 ----

    def _small(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(12)
        return font

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.width() != self._width:
            self._relayout()

    def _measure(self, cid: str, fm: QFontMetricsF, fms: QFontMetricsF, max_w: float):
        label = self._chip_label(cid)
        count = self.counts.get(cid)
        badge = str(count[0]) if count else ""
        x = PAD + fm.horizontalAdvance(label)
        if badge:
            x += 6 + fms.horizontalAdvance(badge) + 10
        members = self.model.members(cid)
        arrow = QRectF(x + 4, 0, ARROW if members else 0, H)
        head_end = arrow.right()
        if cid not in self.expanded or not members:
            return head_end + PAD, H, [], arrow
        mx0 = head_end + 6
        x, y = mx0, (H - MH) / 2
        rects: List[QRectF] = []
        right = mx0
        for m in members:
            mw = fms.horizontalAdvance(self.member_label(cid, m)) + 2 * MPAD
            if x > mx0 and x + mw > max_w - PAD:
                x, y = mx0, y + MH + MGAP
            rects.append(QRectF(x, y, mw, MH))
            right = max(right, x + mw)
            x += mw + MGAP
        return right + PAD, y + MH + (H - MH) / 2, rects, arrow

    def _relayout(self) -> None:
        self._width = self.width()
        self._boxes, self._bands = [], []
        self._gap_bands = {}
        if self.model is None:
            self.setMinimumHeight(0)
            return
        fm, fms = QFontMetricsF(self.font()), QFontMetricsF(self._small())
        right_edge = max(self.width(), 240) - MARGIN
        y = float(MARGIN)
        for ri, row in enumerate(self.model.rows):
            top, x, line_h = y, float(GUTTER), 0.0
            for pi, cid in enumerate(row):
                w, h, members, arrow = self._measure(cid, fm, fms, right_edge - GUTTER)
                if x > GUTTER and x + w > right_edge:
                    x, y, line_h = GUTTER + WRAP_INDENT, y + line_h + WRAP_GAP, 0.0
                self._boxes.append(_Box(cid, -1, QRectF(x, y, w, h), ri, pi, arrow.translated(x, y)))
                for mi, rect in enumerate(members):
                    self._boxes.append(_Box(cid, mi, rect.translated(x, y), ri, pi))
                x += w + GAP
                line_h = max(line_h, h)
            y += line_h
            if self.model.chunks[row[-1]].get("gap_after"):
                y += WRAP_GAP
                self._gap_bands[ri] = QRectF(GUTTER, y, right_edge - GUTTER, GAP_H)
                y += GAP_H
            self._bands.append((top, y))
            y += ROW_GAP
        self.setMinimumHeight(int(y + H))                 # 맨 아래에 새 줄로 놓을 자리

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.minimumHeight())
        return size

    # ---- 찾기 ----

    def _chunk_box(self, cid: str) -> Optional[_Box]:
        return next((b for b in self._boxes if b.cid == cid and b.mi < 0), None)

    def _member_boxes(self, cid: str) -> List[_Box]:
        return [b for b in self._boxes if b.cid == cid and b.mi >= 0]

    def _hit(self, pos: QPointF) -> Optional[_Box]:
        """서브 알약 → 청크 순으로."""
        for b in self._boxes:
            if b.mi >= 0 and b.rect.contains(pos):
                return b
        return next((b for b in self._boxes if b.mi < 0 and b.rect.contains(pos)), None)

    @staticmethod
    def _line_pos(boxes: List[_Box], pos: QPointF, index: Callable[[_Box], int], count: int) -> int:
        """한 줄 안 끼울 자리: pos 가 있는(가장 가까운) 시각 줄에서 가운데가 pos 보다 오른쪽인 첫 상자 앞."""
        if not boxes:
            return count
        tops = sorted({round(b.rect.top()) for b in boxes})
        line = min(tops, key=lambda t: 0 if t <= pos.y() <= t + H else min(abs(pos.y() - t), abs(pos.y() - t - H)))
        same = [b for b in boxes if round(b.rect.top()) == line]
        for b in same:
            if b.rect.center().x() > pos.x():
                return index(b)
        return index(same[-1]) + 1

    def _row_target(self, pos: QPointF) -> tuple:
        for ri, (top, bottom) in enumerate(self._bands):
            gap = self._gap_bands.get(ri)
            if gap is not None and gap.top() <= pos.y() <= gap.bottom():
                return ("row", ri, len(self.model.rows[ri]))
            if pos.y() < top + EDGE:
                return ("newrow", ri)
            if pos.y() <= bottom - EDGE:
                boxes = [b for b in self._boxes if b.row == ri and b.mi < 0]
                return ("row", ri, self._line_pos(boxes, pos, lambda b: b.pos, len(boxes)))
        return ("newrow", len(self._bands))

    def _target(self, pos: QPointF) -> Optional[tuple]:
        drag = self._drag
        if drag is None:
            return None
        if drag.mi >= 0:
            over = next((b for b in self._boxes if b.mi < 0 and b.rect.contains(pos)), None)
            if over is not None:
                members = self._member_boxes(over.cid)
                n = len(self.model.members(over.cid))
                di = self._line_pos(members, pos, lambda b: b.mi, n) if members else n
                return ("member", over.cid, di)
        return self._row_target(pos)

    # ---- 바꾸기 ----

    def _edit(self, fn: Callable, *args):
        before = self.model.dumps()
        result = fn(*args)
        if self.model.dumps() != before:
            self.refresh()
            self.changed.emit(before)
        return result

    def _apply_drop(self, drag: _Box, drop: tuple) -> None:
        if drag.mi < 0:
            if drop[0] == "row":
                self._edit(self.model.move_chunk, drag.cid, drop[1], drop[2])
            else:
                self._edit(self.model.move_chunk_new_row, drag.cid, drop[1])
            self.select(drag.cid)
            return
        if drop[0] == "member":
            dst, di = drop[1], drop[2]
            if dst == drag.cid and drag.mi < di:
                di -= 1
            self._edit(self.model.move_member, drag.cid, drag.mi, drop[1], drop[2])
            self.select(dst, di if dst in self.expanded else -1)
            return
        new = self._edit(self.model.detach_member, drag.cid, drag.mi, drop[1], drop[2] if drop[0] == "row" else None)
        self.select(new)

    def toggle_expanded(self, cid: str) -> None:
        if cid in self.expanded:
            self.expanded.discard(cid)
            if self.sel and self.sel[0] == cid:
                self.sel = (cid, -1)
        elif self.model.members(cid):
            self.expanded.add(cid)
        self.refresh()

    def rename(self, cid: str) -> None:
        current = self.model.chunks[cid].get("label", "") or core.chunk_label(self.model.chunks[cid], self.taxonomy)
        text, ok = QInputDialog.getText(self, "청크 이름", "이름(비우면 분류 이름):", text=current)
        if ok:
            default = core.chunk_label({"members": self.model.members(cid)}, self.taxonomy)
            self._edit(self.model.set_label, cid, "" if text.strip() == default else text)

    def recolor(self, cid: str) -> None:
        color = QColorDialog.getColor(self._color(self.model.chunks[cid]), self, "청크 색")
        if color.isValid():
            self._edit(self.model.set_color, cid, color.name())

    # ---- 그리기 ----

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = theme.tokens()
        if self.model is None or not self._boxes:
            p.setPen(QColor(t["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "청크가 없습니다")
            return
        p.setFont(self._small())
        p.setPen(QColor(t["muted"]))
        for ri, (top, _bottom) in enumerate(self._bands):
            p.drawText(QRectF(0, top, GUTTER - 8, H), Qt.AlignRight | Qt.AlignVCenter, str(ri + 1))
        for rect in self._gap_bands.values():
            p.setPen(QPen(QColor(t["muted"]), 1, Qt.DashLine))
            p.drawLine(rect.topLeft(), rect.topRight())
            p.drawText(rect, Qt.AlignCenter, "빈 줄")
        for b in self._boxes:
            faded = self._drag is not None and self._drag.cid == b.cid and self._drag.mi in (-1, b.mi)
            if b.mi < 0:
                self._paint_chunk(p, b, t, faded)
            else:
                self._paint_member(p, b, t, faded)
        self._paint_drop(p, t)
        self._paint_ghost(p, t)

    def _fill(self, color: QColor, strong: bool = False) -> QColor:
        c = QColor(color)
        dark = theme.current() == "dark"
        c.setAlphaF((0.30 if dark else 0.20) if strong else (0.20 if dark else 0.12))
        return c

    def _edge(self, color: QColor) -> QColor:
        c = QColor(color)
        c.setAlphaF(0.80 if theme.current() == "dark" else 0.60)
        return c

    def _paint_chunk(self, p: QPainter, b: _Box, t: Dict[str, str], faded: bool) -> None:
        spec = self.model.chunks[b.cid]
        color = self._color(spec)
        hidden = bool(spec.get("hidden"))
        selected = self.sel == (b.cid, -1) and self.hasFocus()
        rect = b.rect.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setOpacity(0.35 if faded else 1.0)
        p.fillPath(path, self._fill(color) if not hidden else QColor(0, 0, 0, 0))
        pen = QPen(QColor(t["accent"]) if self.sel == (b.cid, -1) else self._edge(color), 2 if selected else 1)
        if hidden:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        fm = QFontMetricsF(self.font())
        label = self._chip_label(b.cid)
        lw = fm.horizontalAdvance(label)
        label_rect = QRectF(rect.left() + PAD, rect.top(), lw + 2, H)
        p.setFont(self.font())
        p.setPen(QColor(t["muted"] if hidden else t["fg"]))
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        if hidden:
            y = rect.top() + H / 2
            p.drawLine(QPointF(label_rect.left(), y), QPointF(label_rect.left() + lw, y))
        count = self.counts.get(b.cid)
        if count:
            fms = QFontMetricsF(self._small())
            text = str(count[0])
            bw = fms.horizontalAdvance(text) + 10
            badge = QRectF(label_rect.left() + lw + 6, rect.top() + (H - 16) / 2, bw, 16)
            bpath = QPainterPath()
            bpath.addRoundedRect(badge, 8, 8)
            p.fillPath(bpath, QColor(t["surface2"]))
            p.setFont(self._small())
            p.setPen(QColor(t["fg"] if count[0] else t["muted"]))
            p.drawText(badge, Qt.AlignCenter, text)
        if b.arrow is not None and b.arrow.width():
            c = QPointF(b.arrow.center().x(), rect.top() + H / 2)
            d = -1 if b.cid in self.expanded else 1           # 펼쳤으면 ◂, 접었으면 ▸
            tri = QPolygonF([QPointF(c.x() - 3 * d, c.y() - 5), QPointF(c.x() + 4 * d, c.y()),
                             QPointF(c.x() - 3 * d, c.y() + 5)])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t["fg"] if not hidden else t["muted"]))
            p.drawPolygon(tri)
            p.setBrush(Qt.NoBrush)
        p.setOpacity(1.0)

    def _paint_member(self, p: QPainter, b: _Box, t: Dict[str, str], faded: bool) -> None:
        member = self.model.members(b.cid)[b.mi]
        color = self._color(member)
        rect = b.rect.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, MH / 2, MH / 2)
        p.setOpacity(0.35 if faded else 1.0)
        p.fillPath(path, self._fill(color, strong=True))
        selected = self.sel == (b.cid, b.mi)
        p.setPen(QPen(QColor(t["accent"]) if selected else self._edge(color), 2 if selected else 1))
        p.drawPath(path)
        p.setFont(self._small())
        p.setPen(QColor(t["fg"]))
        p.drawText(rect, Qt.AlignCenter, self.member_label(b.cid, member))
        p.setOpacity(1.0)

    def _paint_drop(self, p: QPainter, t: Dict[str, str]) -> None:
        d = self._drop
        if d is None:
            return
        p.setPen(QPen(QColor(t["accent"]), 2))
        if d[0] == "row":
            boxes = sorted((b for b in self._boxes if b.row == d[1] and b.mi < 0), key=lambda b: b.pos)
            if d[2] < len(boxes):
                r = boxes[d[2]].rect
                x = r.left() - GAP / 2
            else:
                r = boxes[-1].rect
                x = r.right() + GAP / 2
            p.drawLine(QPointF(x, r.top() - 2), QPointF(x, r.top() + H + 2))
        elif d[0] == "newrow":
            if d[1] < len(self._bands):
                y = self._bands[d[1]][0] - ROW_GAP / 2
            else:
                y = self._bands[-1][1] + ROW_GAP / 2 if self._bands else MARGIN
            p.drawLine(QPointF(GUTTER, y), QPointF(self.width() - MARGIN, y))
        else:
            box = self._chunk_box(d[1])
            if box is None:
                return
            path = QPainterPath()
            path.addRoundedRect(box.rect.adjusted(-1, -1, 1, 1), 7, 7)
            p.drawPath(path)
            members = sorted(self._member_boxes(d[1]), key=lambda b: b.mi)
            if members:
                if d[2] < len(members):
                    r = members[d[2]].rect
                    x = r.left() - MGAP / 2
                else:
                    r = members[-1].rect
                    x = r.right() + MGAP / 2
                p.drawLine(QPointF(x, r.top() - 1), QPointF(x, r.bottom() + 1))

    def _paint_ghost(self, p: QPainter, t: Dict[str, str]) -> None:
        drag = self._drag
        if drag is None:
            return
        if drag.mi < 0:
            text, color = self.chunk_label(drag.cid), self._color(self.model.chunks[drag.cid])
        else:
            member = self.model.members(drag.cid)[drag.mi]
            text, color = self.member_label(drag.cid, member), self._color(member)
        fm = QFontMetricsF(self._small())
        rect = QRectF(self._mouse.x() + 10, self._mouse.y() + 6, fm.horizontalAdvance(text) + 2 * MPAD, MH)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setOpacity(0.85)
        p.fillPath(path, QColor(t["surface"]))
        p.fillPath(path, self._fill(color, strong=True))
        p.setPen(QPen(self._edge(color), 1))
        p.drawPath(path)
        p.setFont(self._small())
        p.setPen(QColor(t["fg"]))
        p.drawText(rect, Qt.AlignCenter, text)
        p.setOpacity(1.0)

    # ---- 마우스 ----

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.MouseFocusReason)
        pos = event.position()
        box = self._hit(pos)
        if event.button() == Qt.LeftButton:
            if box is not None and box.mi < 0 and box.arrow is not None and box.arrow.width() and \
                    box.arrow.contains(pos):
                self.toggle_expanded(box.cid)
                self.select(box.cid)
                return
            self._press = (pos, box)
        if box is not None:
            self.select(box.cid, box.mi)
        elif event.button() == Qt.LeftButton:
            self.select("")

    def mouseMoveEvent(self, event) -> None:
        self._mouse = event.position()
        if self._press and self._drag is None and event.buttons() & Qt.LeftButton:
            start, box = self._press
            if box is not None and (self._mouse - start).manhattanLength() >= QApplication.startDragDistance():
                self._drag = box
        if self._drag is not None:
            self._drop = self._target(self._mouse)
            self._autoscroll()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        drag, drop = self._drag, self._drop
        self._press = self._drag = self._drop = None
        if drag is not None and drop is not None:
            self._apply_drop(drag, drop)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        box = self._hit(event.position())
        if box is None:
            return
        if box.mi < 0 and not self.model.members(box.cid) and self.model.is_custom(box.cid):
            self.edit_custom.emit(box.cid)
        elif box.mi < 0:
            self.toggle_expanded(box.cid)

    def _scroll_area(self) -> Optional[QScrollArea]:
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w

    def _autoscroll(self) -> None:
        area = self._scroll_area()
        if area is not None:
            area.ensureVisible(int(self._mouse.x()), int(self._mouse.y()), 0, 24)

    def _ensure_visible(self) -> None:
        area = self._scroll_area()
        if area is None or self.sel is None:
            return
        box = next((b for b in self._boxes if (b.cid, b.mi) == self.sel), None)
        if box is not None:
            area.ensureVisible(int(box.rect.center().x()), int(box.rect.center().y()),
                               int(box.rect.width() / 2) + 8, int(box.rect.height() / 2) + 8)

    # ---- 키보드 ----

    _OWN_SHORTCUTS = (QKeySequence.Undo, QKeySequence.Redo)

    def event(self, event) -> bool:
        if event.type() == QEvent.ShortcutOverride and any(event.matches(k) for k in self._OWN_SHORTCUTS):
            event.accept()              # 창의 Ctrl+Z(분류 되돌리기)보다 먼저: 여기서는 배열 되돌리기
            return True
        if event.type() == QEvent.ToolTip:
            self._tooltip(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if self.model is None:
            return super().keyPressEvent(event)
        if event.matches(QKeySequence.Undo):
            return self.undo_requested.emit()
        if event.matches(QKeySequence.Redo):
            return self.redo_requested.emit()
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if key == Qt.Key_Escape and self._drag is not None:
            self._press = self._drag = self._drop = None
            return self.update()
        flat = self.model.flat()
        if not flat:
            return super().keyPressEvent(event)
        if self.sel is None:
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
                return self.select(flat[0])
            return super().keyPressEvent(event)
        cid, mi = self.sel
        if key in (Qt.Key_Left, Qt.Key_Right):
            d = -1 if key == Qt.Key_Left else 1
            if ctrl and mi >= 0:
                if self._edit(self.model.shift_member, cid, mi, d):
                    self.select(cid, mi + d)
            elif ctrl:
                self._edit(self.model.shift, cid, d)
                self.select(cid)
            else:
                self._step(cid, mi, d)
        elif key in (Qt.Key_Up, Qt.Key_Down):
            d = -1 if key == Qt.Key_Up else 1
            if ctrl:
                self._edit(self.model.shift_row, cid, d)
                self.select(cid)
            else:
                self._step_row(cid, d)
        elif key in (Qt.Key_Return, Qt.Key_Enter) and not ctrl:
            if mi >= 0:
                self.select(self._edit(self.model.detach_after, cid, mi))
            else:
                self._edit(self.model.break_after if shift else self.model.break_before, cid)
                self.select(cid)
        elif key == Qt.Key_Backspace and mi < 0:
            if self.model.where(cid)[1] == 0:
                self._edit(self.model.join_previous, cid)
                self.select(cid)
        elif key == Qt.Key_Space:
            self.toggle_expanded(cid)
        elif key == Qt.Key_Delete and mi < 0:
            self._edit(self.model.toggle_hidden, cid)
        elif key == Qt.Key_F2 and mi < 0:
            self.rename(cid)
        elif key == Qt.Key_Home:
            self.select(flat[0])
        elif key == Qt.Key_End:
            self.select(flat[-1])
        else:
            return super().keyPressEvent(event)

    def _step(self, cid: str, mi: int, d: int) -> None:
        flat = self.model.flat()
        n = len(self.model.members(cid)) if cid in self.expanded else 0
        if d > 0 and mi + 1 < n:
            return self.select(cid, mi + 1)                  # 펼친 청크는 → 로 서브에 들어간다
        if d < 0 and mi >= 0:
            return self.select(cid, mi - 1)                  # 첫 서브에서 ← 면 청크로
        i = flat.index(cid) + d
        if 0 <= i < len(flat):
            self.select(flat[i])

    def _step_row(self, cid: str, d: int) -> None:
        r, _ = self.model.where(cid)
        target = r + d
        if not 0 <= target < len(self.model.rows):
            return
        here = self._chunk_box(cid)
        x = here.rect.center().x() if here else 0
        choices = [b for b in self._boxes if b.row == target and b.mi < 0]
        best = min(choices, key=lambda b: abs(b.rect.center().x() - x))
        self.select(best.cid)

    # ---- 메뉴·도움말 ----

    def contextMenuEvent(self, event) -> None:
        if self.model is not None:
            self._context_menu(QPointF(event.pos())).exec(event.globalPos())

    def _context_menu(self, pos: QPointF) -> QMenu:
        box = self._hit(pos)
        menu = QMenu(self)
        if box is None:
            row = next((ri for ri, (top, bottom) in enumerate(self._bands) if top <= pos.y() <= bottom), None)
            if row is not None:
                cid = self.model.rows[row][-1]
                gap = self._gap_bands.get(row)
                label = ("빈 줄 빼기" if gap is not None and gap.contains(pos) else
                         "이 줄 아래에 빈 줄 빼기" if self.model.chunks[cid].get("gap_after") else
                         "이 줄 아래에 빈 줄 넣기")
                menu.addAction(label, lambda: self._edit(self.model.toggle_gap_after, cid))
                menu.addSeparator()
            menu.addAction("새 커스텀 청크…", self.new_custom.emit)
            menu.addSeparator()
            menu.addAction("모두 펼치기", lambda: self.set_all_expanded(True))
            menu.addAction("모두 접기", lambda: self.set_all_expanded(False))
            return menu
        cid = box.cid
        others = [c for c in self.model.flat() if c != cid]
        if box.mi >= 0:
            mi = box.mi
            menu.addAction("새 청크로 분리 (Enter)", lambda: self.select(self._edit(self.model.detach_after, cid, mi)))
            move = menu.addMenu("다른 청크로 옮기기")
            for other in others:
                move.addAction(self.chunk_label(other),
                               lambda o=other: self._edit(self.model.move_member, cid, mi, o,
                                                          len(self.model.members(o))))
            return menu
        spec = self.model.chunks[cid]
        if self.model.members(cid):
            menu.addAction("접기 (Space)" if cid in self.expanded else "펼치기 (Space)",
                           lambda: self.toggle_expanded(cid))
        menu.addAction("앞에서 줄바꿈 (Enter)", lambda: self._edit(self.model.break_before, cid))
        menu.addAction("뒤에서 줄바꿈 (Shift+Enter)", lambda: self._edit(self.model.break_after, cid))
        menu.addAction("아래 빈 줄 빼기" if spec.get("gap_after") else "아래에 빈 줄 넣기",
                       lambda: self._edit(self.model.toggle_gap_after, cid))
        if self.model.flat()[0] != cid:
            menu.addAction("위 빈 줄 빼기" if self.model.has_gap_before(cid) else "위에 빈 줄 넣기",
                           lambda: self._edit(self.model.toggle_gap_before, cid))
        menu.addAction("앞 줄과 합치기 (Backspace)", lambda: self._edit(self.model.join_previous, cid))
        menu.addSeparator()
        menu.addAction("보이기" if spec.get("hidden") else "숨기기 — 이 청크의 태그를 뺌 (Delete)",
                       lambda: self._edit(self.model.toggle_hidden, cid))
        menu.addAction("이름 바꾸기… (F2)", lambda: self.rename(cid))
        menu.addAction("색 바꾸기…", lambda: self.recolor(cid))
        if spec.get("color"):
            menu.addAction("분류 색으로", lambda: self._edit(self.model.set_color, cid, ""))
        merge = menu.addMenu("다른 청크에 합치기")
        for other in others:
            merge.addAction(self.chunk_label(other), lambda o=other: self._edit(self.model.merge_into, cid, o))
        if self.model.is_custom(cid):
            menu.addSeparator()
            menu.addAction("커스텀 청크 편집…", lambda: self.edit_custom.emit(cid))
            menu.addAction("커스텀 청크 지우기", lambda: self._edit(self.model.delete_chunk, cid))
        menu.addSeparator()
        menu.addAction("새 커스텀 청크…", self.new_custom.emit)
        return menu

    def _tooltip(self, event) -> None:
        box = self._hit(QPointF(event.pos()))
        if box is None:
            QToolTip.hideText()
            return
        if box.mi >= 0:
            member = self.model.members(box.cid)[box.mi]
            text = f"{self.member_label(box.cid, member)} — {member}\n끌어서 순서 바꾸기·다른 청크로·밖으로(새 청크)"
        else:
            spec = self.model.chunks[box.cid]
            lines = [f"{self.chunk_label(box.cid)}  ({box.cid})"]
            if spec.get("members"):
                lines.append("분류: " + ", ".join(spec["members"]))
            if spec.get("tags"):
                lines.append("태그: " + ", ".join(spec["tags"][:12]) + (" …" if len(spec["tags"]) > 12 else ""))
            if spec.get("patterns"):
                lines.append("패턴: " + ", ".join(spec["patterns"]))
            if spec.get("hidden"):
                lines.append("숨김: 이 청크의 태그를 뺍니다")
            count = self.counts.get(box.cid)
            if count:
                lines.append(f"이 프롬프트: 남음 {count[0]} · 뺌 {count[1]}" + (f"\n{count[2]}" if count[2] else ""))
            text = "\n".join(lines)
        QToolTip.showText(event.globalPos(), text, self)
