"""관리 툴 외형 — 색 토큰(라이트·다크), 선 아이콘, Qt 스타일시트. 외부 파일 없음.

BMK AI Studio 의 `bmk_studio/appearance.py`(MIT, 같은 저자)를 바탕으로 했다. 두 앱이 같아 보이게 토큰·간격·
아이콘 모양·스타일시트를 그대로 따르고, 이 툴에 필요한 것만 더했다:
- 의미 색: danger(저장할 수 없음) · warning(참고) · notice_*(밖에서 바뀜 알림 띠). 화면에 색을 직접 쓰지 않고 이것만 쓴다.
- 등급 색 rating_g·s·q·e: Danbooru 관례(초록·노랑·주황·빨강)를 표 글자로 읽히는 중간 밝기로. 등급은 전체 이름으로 보인다
  (general·sensitive·questionable·explicit — rating_name).
- QTableView(태그 표), 체크 가능한 칩(등급 버튼), 일부만 체크(─) 표시, 카드(편집 미리보기), 바뀐 칸 표시.
- 아이콘: search · refresh · build · sync · download · dash.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

PATHS = {
    # Studio 와 같은 것
    'folder': '<path d="M3 6h7l2 3h9v11H3z"/>',
    'tag': '<path d="M3 3h9l9 9-9 9-9-9z"/><circle cx="8" cy="8" r="1"/>',
    'undo': '<path d="M8 4 3 9l5 5M3 9h11a6 6 0 0 1 0 12"/>',
    'save': '<path d="M4 3h13l3 3v15H4zM8 3v6h8V3M8 21v-8h8v8"/>',
    'external': '<path d="M14 4h6v6M20 4 10 14M18 13v6H5V6h6"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    'chevron-up': '<path d="m6 15 6-6 6 6"/>',
    'chevron-right': '<path d="m9 6 6 6-6 6"/>',
    'close': '<path d="M6 6l12 12M18 6 6 18"/>',
    'check': '<path d="m5 12 5 5L20 7"/>',
    'eye': '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    # 이 툴에서 더한 것(같은 24px 격자·선 굵기)
    'search': '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
    'refresh': '<path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4"/>',
    'build': '<path d="M12 3 3 7.5v9L12 21l9-4.5v-9zM3 7.5 12 12l9-4.5M12 12v9"/>',
    'sync': '<path d="M4 12h12M12 6l6 6-6 6M20 4v16"/>',
    'download': '<path d="M12 3v12M7 10l5 5 5-5M4 19h16"/>',
    'dash': '<path d="M6 12h12"/>',
}

# 간격 단계(px). 배치는 이 값에서 고른다
SPACE = (4, 8, 12, 16)

THEMES = {
    'light': dict(bg='#f2f3f5', surface='#ffffff', surface2='#e9ebef', fg='#1b1f26', muted='#6b7480', border='#d5d9e0',
                  accent='#0067c0', accent_hover='#005fb3', accent_pressed='#0053a0', on_accent='#ffffff',
                  selected='#dfeafb', disabled='#9aa2ad',
                  danger='#c42b1c', warning='#9d5d00', notice_bg='#fff4ce', notice_fg='#5c4400', notice_border='#e9cf7a',
                  rating_g='#2a7d45', rating_s='#8a6700', rating_q='#b0500f', rating_e='#c2334f'),
    'dark': dict(bg='#1c1e22', surface='#26292e', surface2='#30343a', fg='#e6e8eb', muted='#98a0aa', border='#3b4048',
                 accent='#4cc2ff', accent_hover='#6fd0ff', accent_pressed='#38adeb', on_accent='#0b1a26',
                 selected='#243a4e', disabled='#6b727c',
                 danger='#ff99a4', warning='#fce100', notice_bg='#433519', notice_fg='#fce9a8', notice_border='#6b5520',
                 rating_g='#7fcf98', rating_s='#e3c35a', rating_q='#f3a35f', rating_e='#ff8fa6'),
}
MODES = ('system', 'light', 'dark')
_mode = 'light'
applying = False        # apply 가 Windows 에 색 구성을 알리는 동안(그 신호로 다시 apply 하지 않게)


def system_mode() -> str:
    hints = QGuiApplication.styleHints()
    return 'dark' if hints.colorScheme() == Qt.ColorScheme.Dark else 'light'


def resolve(mode: str) -> str:
    """'system' 은 지금 Windows 설정(라이트/다크)으로."""
    return system_mode() if mode == 'system' else (mode if mode in THEMES else 'light')


def current() -> str:
    return _mode


def tokens(mode: Optional[str] = None) -> Dict[str, str]:
    return THEMES.get(mode or _mode, THEMES['light'])


def color(name: str) -> QColor:
    return QColor(tokens()[name])


RATING_NAMES = {"g": "general", "s": "sensitive", "q": "questionable", "e": "explicit"}
RATING_ORDER = {"": 0, "g": 1, "s": 2, "q": 3, "e": 4}        # 정렬: 없음 → general → … → explicit


def rating_name(rating: str) -> str:
    """g → general. 등급이 없거나 모르는 값은 그대로."""
    return RATING_NAMES.get(rating or "", rating or "")


def rating_color(rating: str) -> Optional[QColor]:
    return color(f"rating_{rating}") if rating in RATING_NAMES else None


def rating_dot(rating: str) -> QIcon:
    """등급 색 동그라미(등급이 아니면 같은 크기의 빈 칸 — 글자 줄이 맞게)."""
    pix = QPixmap(20, 20)
    pix.fill(Qt.GlobalColor.transparent)
    dot = rating_color(rating)
    if dot is not None:
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(3, 3, 14, 14)
        painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def rating_items(combo: QComboBox) -> QComboBox:
    """콤보의 등급 항목(데이터 g/s/q/e) 앞에 등급 색 동그라미. 콤보 글자색은 스타일시트가 정하므로 아이콘으로 단다.
    테마를 바꾸면 apply 가 다시 칠한다."""
    combo.setProperty('ratingItems', True)
    for i in range(combo.count()):
        value = combo.itemData(i)
        combo.setItemIcon(i, rating_dot(value) if value in RATING_NAMES else QIcon())
    return combo


def span(text: str, role: str, bold: bool = False) -> str:
    """미리보기·점검 글에 쓰는 색 글(이미 html escape 한 text). 색은 토큰에서."""
    weight = "font-weight:600;" if bold else ""
    return f"<span style='color:{tokens()[role]};{weight}'>{text}</span>"


def _svg(body: str, stroke_color: str, stroke: float = 1.7, size: int = 24, fill: str = 'none') -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">'
            f'<g fill="{fill}" stroke="{stroke_color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</g></svg>')


def icon(name: str, icon_color: Optional[str] = None) -> QIcon:
    icon_color = icon_color or QApplication.palette().color(QPalette.ColorRole.WindowText).name()
    pix = QPixmap(48, 48)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(_svg(PATHS[name], icon_color).encode())).render(painter)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def decorate(button, name: str):
    """버튼에 아이콘을 단다. 테마를 바꾸면 apply 가 같은 이름으로 다시 그린다."""
    button.setProperty('iconName', name)
    icon_color = tokens()['on_accent'] if button.property('primary') else tokens()['muted']
    button.setIcon(icon(name, icon_color))
    button.setIconSize(QSize(16, 16))
    return button


def primary(button):
    """그 화면에서 가장 중요한 동작 하나(채운 강조색)."""
    button.setProperty('primary', True)
    return button


def ghost(button):
    """덜 중요한 동작: 테두리·배경 없이, 올리면 드러남."""
    button.setProperty('ghost', True)
    return button


def caption(text: str, role: str = 'muted') -> QLabel:
    """글 역할: 'section'(묶음 제목), 'title', 'headline'(편집 중인 태그 이름), 'field'(폼 칸 이름), 'muted'(설명)."""
    widget = QLabel(text)
    widget.setProperty('role', role)
    return widget


def repolish(widget) -> None:
    """속성(dirty 등)을 바꾼 뒤 스타일시트를 다시 적용한다."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _asset(name: str, asset_color: str, size: int = 24) -> str:
    """스타일시트 url() 은 파일이 필요해서 아이콘·색 쌍마다 임시 폴더에 한 번 쓴다."""
    folder = Path(tempfile.gettempdir()) / 'tagorg-ui'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{asset_color.lstrip('#')}-{size}.svg"
    if not path.exists():
        path.write_text(_svg(PATHS[name], asset_color, 2.4, size), encoding='utf-8')
    return path.as_posix()


def stylesheet(t: Dict[str, str]) -> str:
    bg, surface, surface2, fg, muted, border = t['bg'], t['surface'], t['surface2'], t['fg'], t['muted'], t['border']
    accent, accent_hover, accent_pressed, on_accent = t['accent'], t['accent_hover'], t['accent_pressed'], t['on_accent']
    selected, disabled = t['selected'], t['disabled']
    rating_g, rating_s, rating_q, rating_e = t['rating_g'], t['rating_s'], t['rating_q'], t['rating_e']
    chevron_down = _asset('chevron-down', muted)
    check, dash = _asset('check', on_accent, 12), _asset('dash', accent, 12)
    return f'''
    QWidget {{ font-family:"Segoe UI","Malgun Gothic"; font-size:13px; color:{fg}; }}
    QMainWindow, QDialog, QMessageBox {{ background:{bg}; }}
    QStatusBar {{ background:{bg}; color:{muted}; border-top:1px solid {border}; }}
    QStatusBar::item {{ border:0; }}
    QStatusBar QLabel {{ color:{muted}; }}
    QLabel {{ background:transparent; }}
    QLabel[role="section"] {{ font-size:12px; font-weight:700; color:{muted}; padding:8px 0 2px 0; }}
    QLabel[role="title"] {{ font-size:15px; font-weight:600; }}
    QLabel[role="headline"] {{ font-size:18px; font-weight:600; }}
    QLabel[role="field"] {{ color:{muted}; }}
    QLabel[role="field"][dirty="true"] {{ color:{accent}; font-weight:600; }}
    QLabel[role="muted"] {{ color:{muted}; }}
    QFrame[role="divider"] {{ background:{border}; border:0; min-height:1px; max-height:1px; }}
    QFrame[role="card"] {{ background:{surface}; border:1px solid {border}; border-radius:6px; }}
    QFrame#banner {{ background:{t['notice_bg']}; border:0; border-bottom:1px solid {t['notice_border']}; }}
    QFrame#banner QLabel {{ color:{t['notice_fg']}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:5px 8px; selection-background-color:{accent}; selection-color:{on_accent}; }}
    QListView, QTreeView, QListWidget, QTreeWidget {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:2px; selection-background-color:{selected}; outline:0; }}
    QTableView {{ background:{surface}; alternate-background-color:{bg}; color:{fg}; border:1px solid {border}; border-radius:6px; gridline-color:{surface2}; selection-background-color:{selected}; selection-color:{fg}; outline:0; }}
    QTableView::item {{ padding:0 6px; border:0; }}
    QTableView::item:selected {{ background:{selected}; color:{fg}; }}
    QTableCornerButton::section {{ background:{surface2}; border:0; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border:1px solid {accent}; }}
    QTableView:focus, QTreeView:focus, QListView:focus {{ border:1px solid {accent}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{ color:{disabled}; background:{bg}; }}
    QLineEdit[readOnly="true"], QPlainTextEdit[readOnly="true"] {{ background:{bg}; }}
    QPlainTextEdit[mono="true"] {{ font-family:"D2Coding","GulimChe","Consolas"; font-size:12px; }}
    QComboBox::drop-down {{ border:0; width:24px; subcontrol-origin:padding; subcontrol-position:center right; }}
    QComboBox::down-arrow {{ image:url({chevron_down}); width:14px; height:14px; }}
    QComboBox QAbstractItemView {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:4px; selection-background-color:{selected}; selection-color:{fg}; outline:0; }}
    QPushButton {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:6px 12px; min-height:18px; }}
    QPushButton:hover {{ background:{surface2}; }}
    QPushButton:pressed {{ background:{selected}; }}
    QPushButton:focus {{ border:1px solid {accent}; }}
    QPushButton:disabled {{ color:{disabled}; background:{bg}; }}
    QPushButton[primary="true"] {{ background:{accent}; color:{on_accent}; border:1px solid {accent}; font-weight:600; }}
    QPushButton[primary="true"]:hover {{ background:{accent_hover}; border-color:{accent_hover}; }}
    QPushButton[primary="true"]:pressed {{ background:{accent_pressed}; border-color:{accent_pressed}; }}
    QPushButton[primary="true"]:disabled {{ background:{surface2}; color:{disabled}; border-color:{border}; }}
    QPushButton[ghost="true"] {{ background:transparent; border:1px solid transparent; padding:4px 8px; color:{muted}; }}
    QPushButton[ghost="true"]:hover {{ background:{surface2}; color:{fg}; }}
    QPushButton[ghost="true"]:pressed {{ background:{selected}; }}
    QPushButton[ghost="true"]:disabled {{ background:transparent; color:{disabled}; }}
    QPushButton[chip="true"] {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:12px; padding:2px 12px; min-height:18px; min-width:20px; }}
    QPushButton[chip="true"]:hover {{ border-color:{accent}; }}
    QPushButton[chip="true"]:checked {{ background:{accent}; color:{on_accent}; border-color:{accent}; }}
    QPushButton[chip="true"]:disabled {{ background:{bg}; color:{disabled}; border-color:{border}; }}
    QPushButton[chip="true"]:checked:disabled {{ background:{surface2}; color:{muted}; }}
    QPushButton[chip="true"][present="true"]:!checked {{ border:1px solid {accent}; color:{accent}; }}
    QPushButton[chip="true"][rating="g"]:!checked:enabled {{ color:{rating_g}; }}
    QPushButton[chip="true"][rating="s"]:!checked:enabled {{ color:{rating_s}; }}
    QPushButton[chip="true"][rating="q"]:!checked:enabled {{ color:{rating_q}; }}
    QPushButton[chip="true"][rating="e"]:!checked:enabled {{ color:{rating_e}; }}
    QPushButton[chip="true"][rating="g"]:checked:enabled {{ background:{rating_g}; border-color:{rating_g}; color:{surface}; }}
    QPushButton[chip="true"][rating="s"]:checked:enabled {{ background:{rating_s}; border-color:{rating_s}; color:{surface}; }}
    QPushButton[chip="true"][rating="q"]:checked:enabled {{ background:{rating_q}; border-color:{rating_q}; color:{surface}; }}
    QPushButton[chip="true"][rating="e"]:checked:enabled {{ background:{rating_e}; border-color:{rating_e}; color:{surface}; }}
    QScrollArea[canvas="true"] {{ background:{surface}; border:1px solid {border}; border-radius:6px; }}
    QScrollArea[canvas="true"] > QWidget > QWidget {{ background:{surface}; }}
    QTextBrowser {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:5px 8px; }}
    QCheckBox, QRadioButton {{ spacing:8px; padding:3px 0; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width:16px; height:16px; border:1px solid {muted}; border-radius:4px; background:{surface}; }}
    QCheckBox::indicator:hover {{ border-color:{accent}; }}
    QCheckBox::indicator:checked {{ background:{accent}; border-color:{accent}; image:url({check}); }}
    QCheckBox::indicator:indeterminate {{ background:{selected}; border-color:{accent}; image:url({dash}); }}
    QCheckBox::indicator:disabled {{ border-color:{border}; background:{bg}; }}
    QCheckBox::indicator:checked:disabled {{ background:{border}; border-color:{border}; }}
    QCheckBox:disabled {{ color:{disabled}; }}
    QTabWidget::pane {{ border:0; }}
    QTabBar {{ background:transparent; qproperty-drawBase:0; }}
    QTabBar::tab {{ background:transparent; color:{muted}; padding:8px 12px; margin-right:1px; border-bottom:2px solid transparent; }}
    QTabBar::tab:hover {{ color:{fg}; }}
    QTabBar::tab:selected {{ color:{fg}; border-bottom:2px solid {accent}; font-weight:600; }}
    QListView::item, QTreeView::item, QListWidget::item, QTreeWidget::item {{ padding:4px 4px; border-radius:4px; }}
    QListView::item:hover, QTreeView::item:hover, QListWidget::item:hover {{ background:{surface2}; }}
    QListView::item:selected, QTreeView::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{ background:{selected}; color:{fg}; }}
    QHeaderView::section {{ background:{surface2}; color:{muted}; border:0; border-bottom:1px solid {border}; border-right:1px solid {border}; padding:5px 8px; font-weight:600; }}
    QScrollBar:vertical {{ background:transparent; width:12px; margin:0; }}
    QScrollBar::handle:vertical {{ background:{border}; border-radius:4px; min-height:28px; margin:2px 3px; }}
    QScrollBar::handle:vertical:hover {{ background:{muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; background:transparent; }}
    QScrollBar:horizontal {{ background:transparent; height:12px; margin:0; }}
    QScrollBar::handle:horizontal {{ background:{border}; border-radius:4px; min-width:28px; margin:3px 2px; }}
    QScrollBar::handle:horizontal:hover {{ background:{muted}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; background:transparent; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
    QMenuBar {{ background:{bg}; }}
    QMenuBar::item {{ padding:4px 10px; background:transparent; border-radius:4px; }}
    QMenuBar::item:selected {{ background:{surface2}; }}
    QMenu {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:6px; }}
    QMenu::item {{ padding:6px 24px 6px 12px; border-radius:4px; }}
    QMenu::item:selected {{ background:{selected}; }}
    QMenu::item:disabled {{ color:{disabled}; }}
    QMenu::separator {{ height:1px; background:{border}; margin:4px 8px; }}
    QMenu::indicator {{ width:14px; height:14px; }}
    QToolTip {{ background:{surface}; color:{fg}; border:1px solid {border}; border-radius:4px; padding:6px 8px; }}
    QSplitter::handle {{ background:transparent; }}
    QSplitter::handle:horizontal {{ width:6px; }}
    QSplitter::handle:vertical {{ height:6px; }}
    QSplitter::handle:hover {{ background:{selected}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:0; }}
    QDialogButtonBox QPushButton {{ min-width:72px; }}
    '''


def _tell_platform(mode: str) -> None:
    """창 제목 표시줄도 같은 색이 되도록 Windows 에 알린다(Qt 6.8+). 'system' 이면 알림을 거두고 Windows 설정을 따른다."""
    global applying
    hints = QGuiApplication.styleHints()
    if not hasattr(hints, 'setColorScheme'):
        return
    scheme = {'light': Qt.ColorScheme.Light, 'dark': Qt.ColorScheme.Dark}.get(mode, Qt.ColorScheme.Unknown)
    applying = True
    try:
        hints.setColorScheme(scheme)
    finally:
        applying = False


def apply(app, mode: str = 'system') -> str:
    """앱 전체에 테마를 적용하고 실제로 쓴 테마('light'/'dark')를 돌려준다. 버튼 아이콘도 새 색으로 다시 그린다."""
    global _mode
    _tell_platform(mode)
    _mode = resolve(mode)
    t = tokens()
    palette = QPalette()
    roles = {'Window': t['bg'], 'WindowText': t['fg'], 'Base': t['surface'], 'AlternateBase': t['bg'],
             'Text': t['fg'], 'Button': t['surface'], 'ButtonText': t['fg'], 'Highlight': t['selected'],
             'HighlightedText': t['fg'], 'ToolTipBase': t['surface'], 'ToolTipText': t['fg'],
             'PlaceholderText': t['muted'], 'Link': t['accent']}
    for role, value in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t['disabled']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(t))
    for widget in app.allWidgets():
        if isinstance(widget, QPushButton) and widget.property('iconName'):
            decorate(widget, widget.property('iconName'))
        elif isinstance(widget, QComboBox) and widget.property('ratingItems'):
            rating_items(widget)
    return _mode
