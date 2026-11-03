"""미리보기 탭의 배열 세팅 패널 — 배열 고르기·저장, 크롭/시점/시선 퀵 덮어쓰기, 청크 화면, 커스텀 청크·추정 규칙.

- 배열: 레이아웃 프리셋(기본·간단히), 공유 프리셋(data/arrangements, git 으로 배포), 내 배열(local/arrangements,
  이 PC 전용) 중에서 고른다. 저장은 언제나 내 배열로(공유 프리셋을 고쳐도) — 공유본은 "공유로 올리기"로만 쓴다.
  공유·파일 메뉴: 공유로 올리기, 클립보드 복사·붙여넣기(파일 없이 주고받기), 파일 가져오기·내보내기(다른 툴로).
  같은 JSON 을 ComfyUI 노드·Studio 가 읽는다(docs/arrangement.md).
- 다른 PC·옛 분류 체계의 배열은 지금 없는 분류만 빼고 연다(빠진 칸의 태그는 '나머지' 줄로, 무엇을 뺐는지 알림).
- 크롭/시점: 고르면 프롬프트의 크롭·시점 태그를 그 태그로 바꿔 미리보기에 바로 반영한다(organize 의 framing·view).
  "자동"은 프롬프트 그대로. 프롬프트에 있는 태그는 테두리로 표시한다.
- 시선: 여러 개를 고른다(organize 의 gaze). 함께 쓸 수 없는 태그는 켜는 순간 꺼지고, 지금 크롭·시점과 맞지 않는 태그는
  숨긴다(report 의 gaze_blocked). 숨겨도 선택은 남아 시점을 되돌리면 다시 든다.
- 팩에 없는 토큰 추정(guess)은 켜고 끄는 미리보기 옵션이고, 추정 규칙(내 패턴)은 배열에 저장된다.
되돌리기(Ctrl+Z, 청크 화면에서)는 이 패널의 기록이다 — 분류 편집의 되돌리기와 따로 간다.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QWidgetItem,
)

import bmk_tag_organizer_core as core

from . import theme
from .arrangement import (
    SHARE_ID, Model, config_pack, list_entries, parse_text, read_file, share_id, slug, split_items, tolerate,
    write_file,
)
from .chunks import ChunkCanvas

UNDO_LIMIT = 100


class FlowLayout(QLayout):
    """넘치면 다음 줄로 접는 가로 배치(칩 목록)."""

    def __init__(self, parent=None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: List[QWidgetItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, apply: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():          # 숨긴 칩은 자리를 차지하지 않는다
                continue
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right():
                x, y, line_h = rect.x(), y + line_h + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def _chip(text: str, data: str, tip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("chip", True)
    button.setCheckable(True)
    button.setProperty("tag", data)
    button.setFocusPolicy(Qt.TabFocus)
    if tip:
        button.setToolTip(tip)
    return button


def _swatch(color: str) -> QIcon:
    pix = QPixmap(14, 14)
    pix.fill(QColor(color or theme.tokens()["muted"]))
    return QIcon(pix)


class CustomChunkDialog(QDialog):
    """커스텀 청크: 이름·색·태그(정식 태그가 아니어도 됨)·패턴(* 와일드카드, re: 정규식)."""

    def __init__(self, parent, taxonomy, label: str = "", color: str = "", tags: Sequence[str] = (),
                 patterns: Sequence[str] = (), title: str = "커스텀 청크") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.taxonomy = taxonomy
        self.color = color
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("예: 내 의상 묘사")
        form.addRow(theme.caption("이름", "field"), self.label_edit)
        self.color_btn = QPushButton("색 고르기…")
        self.color_btn.clicked.connect(self._pick)
        reset = theme.ghost(QPushButton("기본 색"))
        reset.clicked.connect(lambda: self._set_color(""))
        row = QHBoxLayout()
        row.addWidget(self.color_btn)
        row.addWidget(reset)
        row.addStretch(1)
        form.addRow(theme.caption("색", "field"), row)
        self.tags_edit = QPlainTextEdit("\n".join(tags))
        self.tags_edit.setPlaceholderText("한 줄에 하나(쉼표로 여럿도 됨)\nintricate detailed clothes\nhigh-tech material")
        form.addRow(theme.caption("태그", "field"), self.tags_edit)
        self.patterns_edit = QPlainTextEdit("\n".join(patterns))
        self.patterns_edit.setPlaceholderText("* armor\nre:^cyber.*")
        self.patterns_edit.setMaximumHeight(90)
        form.addRow(theme.caption("패턴", "field"), self.patterns_edit)
        layout.addLayout(form)
        note = theme.caption("태그와 패턴에 맞는 토큰은 분류와 상관없이 이 청크로 갑니다(팩에 없는 토큰도). "
                             "* 는 한 글자 이상, re: 로 시작하면 정규식(소문자·공백으로 정규화한 이름에 전체 일치).", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = theme.caption("", "muted")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._set_color(color)

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self.color or theme.tokens()["muted"]), self, "청크 색")
        if color.isValid():
            self._set_color(color.name())

    def _set_color(self, color: str) -> None:
        self.color = color
        shown = color or core.chunk_color({"members": []}, self.taxonomy)
        self.color_btn.setIcon(_swatch(shown))

    def values(self) -> Tuple[str, str, List[str], List[str]]:
        return (self.label_edit.text().strip(), self.color, split_items(self.tags_edit.toPlainText()),
                [p.strip() for p in self.patterns_edit.toPlainText().splitlines() if p.strip()])

    def _accept(self) -> None:
        _, _, tags, patterns = self.values()
        for p in patterns:
            try:
                core.compile_pattern(p)
            except Exception as e:  # noqa: BLE001 — re.error·ValueError
                self.error.setText(theme.span(f"패턴이 잘못됐습니다: {p} ({e})", "danger"))
                return
        if not tags and not patterns and not self.label_edit.text().strip():
            self.error.setText(theme.span("이름이나 태그를 적으세요.", "danger"))
            return
        self.accept()


class PublishDialog(QDialog):
    """공유로 올리기: 파일 이름(영문 id)·표시 이름·설명. data/arrangements/<id>.json 으로 쓴다."""

    def __init__(self, parent, folder: Path, share: str, label: str, description: str, custom: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("공유 프리셋으로 올리기")
        self.resize(460, 260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.id_edit = QLineEdit(share)
        self.id_edit.setPlaceholderText("portrait-basic")
        form.addRow(theme.caption("파일 이름", "field"), self.id_edit)
        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("예: 인물 중심 기본")
        form.addRow(theme.caption("표시 이름", "field"), self.label_edit)
        self.desc_edit = QPlainTextEdit(description)
        self.desc_edit.setPlaceholderText("어떤 프롬프트에 맞는지, 무엇을 앞에 두는지(목록에 마우스를 올리면 보임)")
        self.desc_edit.setMaximumHeight(80)
        form.addRow(theme.caption("설명", "field"), self.desc_edit)
        layout.addLayout(form)
        note = theme.caption(f"{folder.parent.name}/{folder.name}/<파일 이름>.json 에 씁니다. 저장소에 올리려면 git 으로 "
                             "커밋하세요. 파일 이름은 영문 소문자·숫자·_ - (다른 OS·ComfyUI 노드에서도 같은 이름).", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        if custom:
            # 커스텀 청크에는 개인 태그가 들어갈 수 있어서 내 배열을 local/ 에 둔다
            warn = QLabel(theme.span(f"커스텀 청크의 태그·패턴 {custom}개도 함께 올라갑니다. "
                                     "저장소를 공개하면 누구나 봅니다.", "warning"))
            warn.setWordWrap(True)
            layout.addWidget(warn)
        self.error = theme.caption("", "muted")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Tuple[str, str, str]:
        return (self.id_edit.text().strip().removesuffix(".json"), self.label_edit.text().strip(),
                self.desc_edit.toPlainText().strip())

    def _accept(self) -> None:
        share, label, _ = self.values()
        if not SHARE_ID.fullmatch(share):
            self.error.setText(theme.span("파일 이름은 영문 소문자·숫자로 시작하고 _ - 만 섞어 씁니다.", "danger"))
            return
        if not label:
            self.error.setText(theme.span("표시 이름을 적으세요.", "danger"))
            return
        self.accept()


class GuessRulesDialog(QDialog):
    """배열에 저장하는 추정 패턴(팩 기본 패턴보다 먼저). 한 줄에 `패턴 => 분류 | 부위 부위 | identity | rating=s`.

    규칙의 모든 칸(부위·고유 외형·등급)을 글로 옮기고 되읽는다 — 가져온 배열의 규칙을 열었다 닫아도 그대로.
    """

    def __init__(self, parent, vpack, rules: Sequence[Mapping[str, Any]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("추정 규칙")
        self.vpack = vpack
        self.rules: List[Dict[str, Any]] = []
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(theme.caption("내 패턴 — 팩의 기본 패턴보다 먼저 봅니다", "section"))
        self.edit = QPlainTextEdit("\n".join(self.format_rule(r) for r in rules))
        self.edit.setPlaceholderText("holding * => action/holding\n* gauntlets => clothes/handwear | hands\n"
                                     "* nipples => body/torso | front_torso | rating=q")
        layout.addWidget(self.edit, 2)
        layout.addWidget(theme.caption("기본 패턴(data/guess.json) — 맞는 것이 없으면 끝말(뒤쪽 단어가 팩의 태그인지)을 봅니다",
                                       "section"))
        defaults = QPlainTextEdit("\n".join(self.format_rule(r) for r in vpack.guess.get("patterns", ())), readOnly=True)
        layout.addWidget(defaults, 1)
        self.error = theme.caption("", "muted")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def format_rule(rule: Mapping[str, Any]) -> str:
        text = f"{rule.get('pattern', '')} => {rule.get('primary', '')}"
        if rule.get("regions"):
            text += " | " + " ".join(rule["regions"])
        if rule.get("identity"):
            text += " | identity"
        if rule.get("rating"):
            text += f" | rating={rule['rating']}"
        return text

    @staticmethod
    def parse(text: str) -> List[Dict[str, Any]]:
        rules = []
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=>" not in line:
                raise ValueError(f"{n}번째 줄: '패턴 => 분류' 형식이 아닙니다: {line}")
            pattern, _, rest = line.partition("=>")
            parts = [p.strip() for p in rest.split("|")]
            rule: Dict[str, Any] = {"pattern": pattern.strip(), "primary": parts[0]}
            for extra in parts[1:]:
                words = extra.replace(",", " ").split()
                if words == ["identity"]:
                    rule["identity"] = True
                elif len(words) == 1 and words[0].startswith("rating="):
                    rule["rating"] = words[0][len("rating="):]
                elif words:
                    rule["regions"] = words
            rules.append(rule)
        return rules

    def _accept(self) -> None:
        try:
            rules = self.parse(self.edit.toPlainText())
            core.compile_guess_rules(rules, self.vpack)
        except ValueError as e:
            self.error.setText(theme.span(str(e), "danger"))
            return
        self.rules = rules
        self.accept()


class ArrangementPanel(QWidget):
    changed = Signal()                  # 미리보기를 다시 돌릴 때(배열·덮어쓰기·추정 켜기)

    def __init__(self, config: Mapping[str, Any], folder: Path, settings=None, parent=None,
                 shared: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.folder = Path(folder)
        self.shared = Path(shared) if shared is not None else None
        self.settings = settings
        self.framing = ""
        self.view = ""
        self.gaze = ""                          # 고른 시선 태그(쉼표로 여럿)
        self.source = "layout:default"          # layout:<id> / 파일 경로 / ""(가져왔고 아직 저장 안 함)
        self.undo_stack: List[str] = []
        self.redo_stack: List[str] = []
        self._chips: Dict[str, QButtonGroup] = {}
        self._set_config(config)
        self.model = Model(core.arrangement_from_layout(self.vpack, "default"))
        self.saved = self.model.dumps()
        self._build()
        self._restore()

    # ---- 만들기 ----

    def _set_config(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self.vpack = config_pack(config)
        self.palette = core.palette(self.vpack)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE[0])

        top = QHBoxLayout()
        top.addWidget(theme.caption("배열", "field"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        self.combo.activated.connect(self._combo_chosen)
        top.addWidget(self.combo)
        self.save_btn = theme.decorate(QPushButton("저장"), "save")
        self.save_btn.setToolTip("지금 배열을 파일에 저장합니다(레이아웃에서 시작했으면 이름을 묻습니다)")
        self.save_btn.clicked.connect(self.save)
        save_as = QPushButton("다른 이름으로…")
        save_as.clicked.connect(self.save_as)
        share = QPushButton("공유·파일")
        share.setToolTip("공유 프리셋으로 올리기, 클립보드로 주고받기, 파일 가져오기·내보내기")
        menu = QMenu(share)
        menu.addAction("공유 프리셋으로 올리기…", self.publish).setEnabled(self.shared is not None)
        menu.addAction("공유 폴더 열기", self.open_shared_folder).setEnabled(self.shared is not None)
        menu.addSeparator()
        menu.addAction("클립보드로 복사", self.copy_clipboard)
        menu.addAction("클립보드에서 붙여넣기", self.paste_clipboard)
        menu.addSeparator()
        menu.addAction("파일 가져오기…", self.import_file).setToolTip("다른 툴·PC 에서 만든 배열 세팅 JSON 을 엽니다")
        menu.addAction("파일 내보내기…", self.export_file)
        menu.setToolTipsVisible(True)
        share.setMenu(menu)
        self.undo_btn = theme.decorate(theme.ghost(QPushButton("되돌리기")), "undo")
        self.undo_btn.setToolTip("배열 편집 되돌리기(청크 화면에서 Ctrl+Z, 다시는 Ctrl+Y)")
        self.undo_btn.clicked.connect(self.undo)
        for w in (self.save_btn, save_as, share, self.undo_btn):
            top.addWidget(w)
        top.addStretch(1)
        self.state = theme.caption("", "muted")
        top.addWidget(self.state)
        layout.addLayout(top)

        rules = self.config["rules"]
        hides = {}
        for gate in (rules.get("framing") or {}).get("gates", ()) + (rules.get("view") or {}).get("gates", ()):
            for tag in gate.get("tags", ()):
                hides[tag] = ", ".join(gate.get("hide", ())) or "없음"
        for group, title in (("framing", "크롭"), ("view", "시점")):
            flow = FlowLayout(spacing=theme.SPACE[0])
            label = theme.caption(title, "field")
            label.setMinimumWidth(28)
            flow.addWidget(label)
            buttons = QButtonGroup(self)
            buttons.setExclusive(True)
            auto = _chip("자동", "", "프롬프트 그대로")
            auto.setChecked(True)
            buttons.addButton(auto)
            flow.addWidget(auto)
            for tag in self.palette["quick"][group]:
                tip = f"프롬프트의 {title} 태그를 '{tag}' 로 바꿉니다"
                if tag in hides:
                    tip += f" — 가리는 부위: {hides[tag]}"
                chip = _chip(tag, tag, tip)
                buttons.addButton(chip)
                flow.addWidget(chip)
            buttons.buttonClicked.connect(lambda b, g=group: self._quick(g, b.property("tag")))
            self._chips[group] = buttons
            holder = QWidget()
            holder.setLayout(flow)
            layout.addWidget(holder)
        self._build_gaze(layout)

        opts = QHBoxLayout()
        self.sub_order_box = QCheckBox("청크 안은 서브 순서대로")
        self.sub_order_box.setToolTip("켜면 청크 안 태그를 펼친 서브 순서로 놓습니다. 끄면 입력(또는 고른 순서 옵션) 순서")
        self.sub_order_box.toggled.connect(self._sub_order)
        self.guess_box = QCheckBox("팩에 없는 토큰 추정")
        self.guess_box.setToolTip("holding * · * eyes 같은 패턴과 끝말(뒤쪽 단어가 팩의 태그인지)로 팩에 없는 토큰을 "
                                  "분류에 임시로 넣습니다(출력 글자는 그대로, 기울임으로 표시)")
        self.guess_box.toggled.connect(self._guess_toggled)
        rules_btn = theme.ghost(QPushButton("추정 규칙…"))
        rules_btn.clicked.connect(self.edit_guess_rules)
        custom_btn = QPushButton("+ 커스텀 청크")
        custom_btn.setToolTip("내가 쓰는 태그(팩에 없어도 됨)를 모아 원하는 자리에 놓는 청크")
        custom_btn.clicked.connect(lambda: self.new_custom())
        expand = theme.ghost(QPushButton("모두 펼치기"))
        expand.clicked.connect(lambda: self.canvas.set_all_expanded(True))
        collapse = theme.ghost(QPushButton("모두 접기"))
        collapse.clicked.connect(lambda: self.canvas.set_all_expanded(False))
        for w in (self.sub_order_box, self.guess_box, rules_btn, custom_btn, expand, collapse):
            opts.addWidget(w)
        opts.addStretch(1)
        layout.addLayout(opts)

        # 청크 화면은 따로(self.board): 미리보기 탭이 색 미리보기 옆에 놓는다
        self.board = QWidget()
        board = QVBoxLayout(self.board)
        board.setContentsMargins(0, 0, 0, 0)
        board.setSpacing(theme.SPACE[0])
        self.canvas = ChunkCanvas()
        self.canvas.changed.connect(self._canvas_changed)
        self.canvas.edit_custom.connect(self.edit_custom)
        self.canvas.new_custom.connect(lambda: self.new_custom())
        self.canvas.undo_requested.connect(self.undo)
        self.canvas.redo_requested.connect(self.redo)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(self.canvas)
        scroll.setMinimumHeight(90)
        scroll.setProperty("canvas", True)
        board.addWidget(scroll, 1)
        hint = theme.caption("끌어서 옮기기 · 줄 사이에 놓으면 새 줄 · Enter 줄바꿈 · Backspace 앞 줄과 합치기 · "
                             "Space 펼치기 · Delete 숨기기 · 오른쪽 버튼 메뉴", "muted")
        hint.setWordWrap(True)
        board.addWidget(hint)
        self.warning = QLabel(wordWrap=True)
        self.warning.linkActivated.connect(lambda _: self._add_uncovered())
        board.addWidget(self.warning)
        self.warning.hide()
        self.canvas.set_model(self.model, self.config["taxonomy"])

    def _build_gaze(self, layout) -> None:
        """시선 칩: 여러 개를 고른다. 자동은 고른 것이 없을 때만 켜져 있다."""
        tags = self.palette["quick"].get("gaze") or ()
        if not tags:
            return
        flow = FlowLayout(spacing=theme.SPACE[0])
        label = theme.caption("시선", "field")
        label.setMinimumWidth(28)
        flow.addWidget(label)
        buttons = QButtonGroup(self)
        buttons.setExclusive(False)
        auto = _chip("자동", "", "프롬프트 그대로")
        auto.setChecked(True)
        buttons.addButton(auto)
        flow.addWidget(auto)
        for tag in tags:
            what = "눈을 감거나 가렸을 때" if tag.startswith("facing") else "눈이 보일 때"
            chip = _chip(tag, tag, f"프롬프트의 시선 태그를 고른 태그들로 바꿉니다({what}) — 여러 개 고를 수 있음")
            buttons.addButton(chip)
            flow.addWidget(chip)
        buttons.buttonClicked.connect(lambda b: self._gaze_clicked(b.property("tag")))
        self._chips["gaze"] = buttons
        holder = QWidget()
        holder.setLayout(flow)
        layout.addWidget(holder)

    # ---- 바깥에서 ----

    def options(self) -> Dict[str, Any]:
        return {"arrangement": self.model.to_json(), "framing": self.framing, "view": self.view, "gaze": self.gaze,
                "guess": self.guess_box.isChecked()}

    def set_report(self, report: Mapping[str, Any]) -> None:
        """미리보기 결과로 청크별 태그 수와 프롬프트에 있는 크롭·시점 태그를 보여 준다."""
        counts: Dict[str, List[Any]] = {}
        for row in report.get("tokens", ()):
            entry = counts.setdefault(row.get("section", ""), [0, 0, []])
            entry[1 if row.get("removed") else 0] += 1
            entry[2].append(("× " if row.get("removed") else "") + row["text"])
        self.canvas.set_counts({cid: (k, r, ", ".join(names[:12]) + (" …" if len(names) > 12 else ""))
                                for cid, (k, r, names) in counts.items()})
        present = {row.get("key") for row in report.get("tokens", ()) if row.get("kind") == "tag"
                   and not row.get("injected")}
        for group, buttons in self._chips.items():
            for b in buttons.buttons():
                tag = b.property("tag")
                mark = bool(tag) and tag in present
                if b.property("present") != mark:
                    b.setProperty("present", mark)
                    theme.repolish(b)
        if "gaze" in self._chips:
            blocked = set(report.get("gaze_blocked", ()))
            for b in self._chips["gaze"].buttons():
                b.setVisible(b.property("tag") not in blocked)

    def custom_chunks(self) -> List[Tuple[str, str]]:
        return [(cid, self.canvas.chunk_label(cid)) for cid in self.model.custom_ids()]

    def add_tags_to(self, cid: str, tags: Iterable[str]) -> None:
        self._apply(lambda: self.model.add_tags(cid, tags))
        self.canvas.select(cid)

    def reload_config(self, config: Mapping[str, Any]) -> None:
        """분류 체계·규칙을 다시 읽었다(F5). 칩 목록은 다음 실행 때 바뀐다."""
        self._set_config(config)
        self.canvas.set_model(self.model, self.config["taxonomy"])
        self._update_state()

    # ---- 크롭·시점·시선·옵션 ----

    def _quick(self, group: str, tag: str) -> None:
        setattr(self, group, tag or "")
        self.changed.emit()

    def set_quick(self, group: str, tag: str) -> None:
        if group == "gaze":
            self._set_gaze(split_items(tag))
            return
        for b in self._chips[group].buttons():
            if (b.property("tag") or "") == tag:
                b.setChecked(True)
        self._quick(group, tag)

    def _gaze_clicked(self, tag: str) -> None:
        selected = split_items(self.gaze)
        self._set_gaze(core.gaze_toggle(self.palette, selected, tag) if tag else [])

    def _set_gaze(self, tags: Sequence[str]) -> None:
        for b in self._chips["gaze"].buttons():
            tag = b.property("tag")
            on = tag in tags if tag else not tags
            b.setChecked(on)
            b.setText(("✓ " if on else "") + tag if tag else "자동")
        self._quick("gaze", ", ".join(tags))

    def _sub_order(self, on: bool) -> None:
        if on != self.model.sub_order:
            self._apply(lambda: setattr(self.model, "sub_order", on))

    def _guess_toggled(self, on: bool) -> None:
        if self.settings is not None:
            self.settings.setValue("arrangement_guess", on)
        self.changed.emit()

    def edit_guess_rules(self) -> None:
        dialog = GuessRulesDialog(self, self.vpack, self.model.guess)
        if dialog.exec() == QDialog.Accepted:
            rules = dialog.rules
            self._apply(lambda: setattr(self.model, "guess", rules))
            if rules and not self.guess_box.isChecked():
                self.guess_box.setChecked(True)

    # ---- 커스텀 청크 ----

    def new_custom(self, tags: Sequence[str] = ()) -> Optional[str]:
        dialog = CustomChunkDialog(self, self.config["taxonomy"], tags=tags, title="새 커스텀 청크")
        if dialog.exec() != QDialog.Accepted:
            return None
        label, color, tags, patterns = dialog.values()
        holder: List[str] = []
        self._apply(lambda: holder.append(self.model.add_custom(label or "내 태그", color, tags, patterns)))
        self.canvas.select(holder[0])
        return holder[0]

    def edit_custom(self, cid: str) -> None:
        spec = self.model.chunks[cid]
        dialog = CustomChunkDialog(self, self.config["taxonomy"], spec.get("label", ""), spec.get("color", ""),
                                   spec.get("tags", ()), spec.get("patterns", ()))
        if dialog.exec() == QDialog.Accepted:
            label, color, tags, patterns = dialog.values()
            self._apply(lambda: self.model.update_custom(cid, label, color, tags, patterns))

    # ---- 되돌리기 ----

    def _apply(self, fn) -> None:
        before = self.model.dumps()
        fn()
        self.model.load(self.model.to_json())          # 정리(_tidy) 규칙을 똑같이
        if self.model.dumps() != before:
            self._canvas_changed(before)
            self.canvas.refresh()

    def _canvas_changed(self, before: str) -> None:
        self.undo_stack.append(before)
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        self._after_edit()

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self.model.dumps())
            self.model.load(json.loads(self.undo_stack.pop()))
            self.canvas.refresh()
            self._after_edit()

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self.model.dumps())
            self.model.load(json.loads(self.redo_stack.pop()))
            self.canvas.refresh()
            self._after_edit()

    def _after_edit(self) -> None:
        self.sub_order_box.blockSignals(True)
        self.sub_order_box.setChecked(self.model.sub_order)
        self.sub_order_box.blockSignals(False)
        self._update_state()
        self.changed.emit()

    def dirty(self) -> bool:
        return self.model.dumps() != self.saved

    def confirm_close(self) -> bool:
        """창을 닫기 전에: 저장하지 않은 배열 변경이 있으면 저장할지 묻는다. 닫아도 되면 True."""
        if not self.dirty():
            return True
        answer = QMessageBox.question(self, "배열 세팅", "저장하지 않은 배열 변경이 있습니다. 저장할까요?",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def _update_state(self) -> None:
        self.undo_btn.setEnabled(bool(self.undo_stack))
        if self.source.startswith("layout:"):
            text = "레이아웃에서 시작 — 바꾸면 저장할 때 이름을 묻습니다" if not self.dirty() else "수정됨(저장 안 함)"
        elif not self.source:
            text = "가져옴(저장 안 함)"
        elif self.is_shared(self.source):
            text = ("수정됨 — 저장하면 내 배열로(공유본은 그대로)" if self.dirty()
                    else f"공유 프리셋: {Path(self.source).name}")
        else:
            text = "수정됨(저장 안 함)" if self.dirty() else f"저장됨: {Path(self.source).name}"
        self.state.setText(text)
        missing = core.uncovered_slots(self.model.to_json(), self.config["taxonomy"])
        if missing:
            shown = ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")
            self.warning.setText(theme.span(f"어느 청크에도 없는 분류: {html.escape(shown)} — 맨 끝 '나머지' 줄로 갑니다. ",
                                            "warning") + "<a href='add'>새 청크로 넣기</a>")
            self.warning.show()
        else:
            self.warning.hide()

    def _add_uncovered(self) -> None:
        missing = core.uncovered_slots(self.model.to_json(), self.config["taxonomy"])
        self._apply(lambda: self.model.add_slots(missing))

    # ---- 고르기·파일 ----

    def _fill_combo(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for lid, label in self.palette["layouts"].items():
            self.combo.addItem(f"레이아웃: {label}", f"layout:{lid}")
        for prefix, folder in (("공유: ", self.shared), ("", self.folder)):
            entries = list_entries(folder) if folder is not None else []
            if entries:
                self.combo.insertSeparator(self.combo.count())
            for label, path, description in entries:
                self.combo.addItem(prefix + label, str(path))
                if description:
                    self.combo.setItemData(self.combo.count() - 1, description, Qt.ToolTipRole)
        if not self.source:
            self.combo.addItem(f"{self.model.label or '가져온 배열'} (저장 안 함)", "")
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.source)))
        self.combo.blockSignals(False)

    def _restore(self) -> None:
        last = self.settings.value("arrangement") if self.settings is not None else None
        guess = self.settings.value("arrangement_guess") if self.settings is not None else None
        self.guess_box.blockSignals(True)
        self.guess_box.setChecked(guess in (True, "true"))
        self.guess_box.blockSignals(False)
        if isinstance(last, str) and last and not self._open(last, quiet=True):
            last = None
        if not last:
            self._open("layout:default", quiet=True)

    def is_shared(self, source: str) -> bool:
        if self.shared is None or not source or source.startswith("layout:"):
            return False
        return Path(source).resolve().parent == self.shared.resolve()

    def _open(self, source: str, quiet: bool = False) -> bool:
        notes: List[str] = []
        try:
            if source.startswith("layout:"):
                data = core.arrangement_from_layout(self.vpack, source[len("layout:"):])
            else:
                data, notes = tolerate(read_file(Path(source)), self.vpack)
                core.load_arrangement(data, self.vpack)
        except (OSError, KeyError, ValueError) as e:
            if not quiet:
                QMessageBox.warning(self, "배열 열기", f"열지 못했습니다: {e}")
            return False
        self._set_model(Model(data), source, saved=not notes)       # 뺀 것이 있으면 저장해야 파일에 반영된다
        self._tell_dropped(notes, quiet)
        return True

    def _tell_dropped(self, notes: Sequence[str], quiet: bool = False) -> None:
        if notes and not quiet:
            QMessageBox.information(
                self, "배열 열기", "지금 분류 체계에 없는 분류를 빼고 열었습니다(그 태그는 맨 끝 '나머지' 줄로 갑니다).\n\n"
                + "\n".join(notes[:12]) + ("\n…" if len(notes) > 12 else ""))

    def _set_model(self, model: Model, source: str, saved: bool = True) -> None:
        self.model = model
        self.source = source
        self.saved = model.dumps() if saved else ""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.canvas.set_model(model, self.config["taxonomy"])
        if self.settings is not None and source:
            self.settings.setValue("arrangement", source)
        self._fill_combo()
        self._after_edit()

    def _combo_chosen(self, index: int) -> None:
        source = self.combo.itemData(index)
        if source == self.source or source is None:
            return
        if self.dirty() and QMessageBox.question(
                self, "배열", "저장하지 않은 배열 변경이 있습니다. 버리고 바꿀까요?") != QMessageBox.Yes:
            self.combo.setCurrentIndex(max(0, self.combo.findData(self.source)))
            return
        if not self._open(source):
            self.combo.setCurrentIndex(max(0, self.combo.findData(self.source)))

    def save(self) -> bool:
        if not self.source or self.source.startswith("layout:") or self.is_shared(self.source):
            return self.save_as()                       # 공유본은 "공유로 올리기"로만 고친다
        return self._write(Path(self.source))

    def save_as(self) -> bool:
        name, ok = QInputDialog.getText(self, "배열 저장", "배열 이름:", text=self.model.label or "내 배열")
        if not ok or not name.strip():
            return False
        path = self.folder / f"{slug(name)}.json"
        if path.exists() and str(path) != self.source and QMessageBox.question(
                self, "배열 저장", f"{path.name} 이(가) 이미 있습니다. 덮어쓸까요?") != QMessageBox.Yes:
            return False
        self.model.label = name.strip()
        return self._write(path)

    def _write(self, path: Path) -> bool:
        try:
            core.load_arrangement(self.model.to_json(), self.vpack)
            write_file(path, self.model.to_json())
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "배열 저장", f"저장하지 못했습니다: {e}")
            return False
        self.source = str(path)
        self.saved = self.model.dumps()
        if self.settings is not None:
            self.settings.setValue("arrangement", self.source)
        self._fill_combo()
        self._update_state()
        return True

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "배열 가져오기", str(self.folder), "배열 세팅 (*.json)")
        if path:
            self._take(lambda: read_file(Path(path)), "배열 가져오기")

    def paste_clipboard(self) -> None:
        self._take(lambda: parse_text(QGuiApplication.clipboard().text()), "클립보드에서 붙여넣기")

    def _take(self, get, title: str) -> None:
        """밖에서 온 배열(파일·클립보드)을 저장하지 않은 새 배열로 연다."""
        if self.dirty() and QMessageBox.question(
                self, "배열", "저장하지 않은 배열 변경이 있습니다. 버리고 가져올까요?") != QMessageBox.Yes:
            return
        try:
            data, notes = tolerate(get(), self.vpack)
            core.load_arrangement(data, self.vpack)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, title, f"가져오지 못했습니다: {e}")
            return
        self._set_model(Model(data), "", saved=False)
        self._tell_dropped(notes)

    def copy_clipboard(self) -> None:
        """한 줄 JSON — 메신저·이슈에 붙여 넣어 파일 없이 주고받는다."""
        QGuiApplication.clipboard().setText(json.dumps(self.model.to_json(), ensure_ascii=False, separators=(",", ":")))
        self.state.setText("클립보드에 복사했습니다")

    def publish(self) -> bool:
        """지금 배열을 공유 프리셋(data/arrangements/<id>.json)으로 쓰고 그것을 연다. 커밋은 git 으로."""
        if self.shared is None:
            return False
        custom = sum(len(self.model.chunks[cid].get(key) or ()) for cid in self.model.custom_ids()
                     for key in ("tags", "patterns"))
        dialog = PublishDialog(self, self.shared, share_id(self.model.label, self.source), self.model.label,
                               self.model.description, custom)
        if dialog.exec() != QDialog.Accepted:
            return False
        share, label, description = dialog.values()
        path = self.shared / f"{share}.json"
        if path.exists() and QMessageBox.question(
                self, "공유 프리셋", f"공유 프리셋 {path.name} 을(를) 지금 배열로 바꿀까요?") != QMessageBox.Yes:
            return False
        before = (self.model.label, self.model.description)
        self.model.label, self.model.description = label, description
        if not self._write(path):
            self.model.label, self.model.description = before
            return False
        QMessageBox.information(self, "공유 프리셋", f"썼습니다: {path}\n\n저장소에 올리려면 이 파일을 git 으로 커밋하세요.")
        return True

    def open_shared_folder(self) -> None:
        if self.shared is not None:
            self.shared.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.shared)))

    def export_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "배열 내보내기", f"{slug(self.model.label or 'arrangement')}.json",
                                              "배열 세팅 (*.json)")
        if path:
            try:
                write_file(Path(path), self.model.to_json())
            except OSError as e:
                QMessageBox.critical(self, "배열 내보내기", f"쓰지 못했습니다: {e}")
