"""TagOrganizer 관리 툴 — 태그 목록·보류(etc)·점검·미리보기·작업·통계 탭과 편집 패널.

    python tagorg.py gui        (또는 tagorg_gui.pyw 더블클릭)

고칠 때마다 data/overlay/tags.csv 에 바로 저장한다(CLI `tagorg fix` 와 같은 검증, reviewed 로 잠금).
빌드·노드 팩 반영·원본 가져오기는 작업 탭에서(CLI 와 같은 함수), 커밋은 지금처럼 git 으로 한다.
"""

from __future__ import annotations

import sys
from typing import List, Optional, Sequence

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from tools import tagdb

from . import ops, theme
from .editor import EditorPanel
from .evidence_panel import EvidencePanel
from .pages import AddExtraDialog, CheckPage, TagListPage
from .store import ConflictError, Store, db_label
from .work import PreviewPage, StatsPage, WorkPage

EXTERNAL_POLL_MS = 3000


class MainWindow(QMainWindow):
    def __init__(self, store: Store, env: Optional[ops.Env] = None) -> None:
        super().__init__()
        self.store = store
        self.env = env or ops.Env()
        self.busy: Optional[str] = None         # 원본 가져오기처럼 overlay 를 쓰는 작업이 도는 동안: 편집을 막는다
        self.setWindowTitle("TagOrganizer 관리")
        self.list_page = TagListPage(store)
        self.queue_page = TagListPage(store, queue=True)
        self.check_page = CheckPage(store)
        self.preview_page = PreviewPage(store, self.env, settings=QSettings("BMK", "TagOrganizer"))
        self.work_page = WorkPage(store, self.env, on_busy=self.set_busy, on_imported=self.reload)
        self.stats_page = StatsPage(store, self.env)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.list_page, "태그 목록")
        self.tabs.addTab(self.queue_page, "보류(etc)")
        self.tabs.addTab(self.check_page, "점검")
        self.tabs.addTab(self.preview_page, "미리보기")
        self.tabs.addTab(self.work_page, "작업")
        self.tabs.addTab(self.stats_page, "통계")
        self.editor = EditorPanel(store, evidence=EvidencePanel(store, self.env.evidence_cache, self.env.evidence_get,
                                                                self.env.evidence_delay))

        self.banner = QFrame(objectName="banner")      # 색은 테마 토큰(notice_*)
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(theme.SPACE[3], theme.SPACE[1], theme.SPACE[3], theme.SPACE[1])
        row.addWidget(QLabel("overlay·확장 DB 파일이 밖에서 바뀌었습니다(CLI·다른 세션·git). 다시 불러오기 전에는 "
                             "저장하지 않습니다."), 1)
        reload_btn = theme.decorate(QPushButton("다시 불러오기 (F5)"), "refresh")
        reload_btn.clicked.connect(self.reload)
        row.addWidget(reload_btn)
        self.banner.hide()

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.editor)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        self.status = QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)
        self._make_actions()

        for page in (self.list_page, self.queue_page):
            page.selection_changed.connect(lambda names, p=page: self._page_selected(p, names))
        self.check_page.open_names.connect(self.editor.set_names)
        self.preview_page.open_names.connect(self.editor.set_names)
        self.list_page.add_requested.connect(self.add_extra)
        self.preview_page.add_extra_requested.connect(self.add_extra)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.editor.saved.connect(self._saved)
        self.editor.before_save = self._flush_selection

        self._poll = QTimer(self, interval=EXTERNAL_POLL_MS)
        self._poll.timeout.connect(self._check_external)
        self._poll.start()
        # 메서드로 연결한다: 창이 지워지면 연결도 끊긴다(람다는 지워진 창을 부른다)
        QGuiApplication.instance().applicationStateChanged.connect(self._app_state)

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._scheme_changed)

        settings = QSettings("BMK", "TagOrganizer")
        if settings.value("geometry"):
            self.restoreGeometry(settings.value("geometry"))
        else:
            self.resize(1500, 900)
        if settings.value("splitter"):
            self.splitter.restoreState(settings.value("splitter"))
        self._update_status()

    def _make_actions(self) -> None:
        menu = self.menuBar().addMenu("파일")
        self.reload_action = QAction("다시 불러오기", self, shortcut=QKeySequence(Qt.Key_F5), triggered=self.reload)
        menu.addAction(self.reload_action)
        menu.addSeparator()
        menu.addAction(QAction("끝내기", self, shortcut=QKeySequence.Quit, triggered=self.close))
        menu = self.menuBar().addMenu("편집")
        self.undo_action = QAction("되돌리기", self, shortcut=QKeySequence.Undo, triggered=self.undo)
        menu.addAction(self.undo_action)
        menu.addSeparator()
        menu.addAction(QAction("저장", self, shortcut=QKeySequence.Save,
                               triggered=lambda: self.editor.save(advance=False)))
        menu.addAction(QAction("저장 후 다음", self, shortcut=QKeySequence(Qt.CTRL | Qt.Key_Return),
                               triggered=lambda: self.editor.save(advance=True)))
        self.addAction(QAction(self, shortcut=QKeySequence(Qt.CTRL | Qt.Key_Enter),
                               triggered=lambda: self.editor.save(advance=True)))
        menu.addAction(QAction("찾기", self, shortcut=QKeySequence.Find, triggered=self._focus_search))
        menu = self.menuBar().addMenu("보기")
        theme_menu = menu.addMenu("테마")
        self.theme_mode = QSettings("BMK", "TagOrganizer").value("theme") or "system"
        group = QActionGroup(self)
        for mode, label in (("system", "Windows 설정 따르기"), ("light", "라이트"), ("dark", "다크")):
            action = QAction(label, self, checkable=True, checked=mode == self.theme_mode)
            action.setData(mode)
            group.addAction(action)
            theme_menu.addAction(action)
        group.triggered.connect(lambda action: self.set_theme(action.data()))
        self._update_undo()

    # ---- 선택 ----

    def _current_list(self) -> Optional[TagListPage]:
        page = self.tabs.currentWidget()
        return page if isinstance(page, TagListPage) else None

    def _page_selected(self, page: TagListPage, names: List[str]) -> None:
        if page is self.tabs.currentWidget():
            self.editor.set_names(names)

    def _tab_changed(self, _index: int) -> None:
        page = self.tabs.currentWidget()
        if isinstance(page, (TagListPage, CheckPage, PreviewPage)):
            self.editor.set_names(page.selected_names())

    def _flush_selection(self) -> bool:
        """다른 태그를 고른 직후(편집 패널이 아직 이전 태그일 때) 저장하면, 저장하지 않고 고른 태그를 불러온다.
        overlay 를 쓰는 작업(원본 가져오기)이 도는 동안에도 저장하지 않는다."""
        if self.busy:
            self.statusBar().showMessage(f"{self.busy} 중에는 저장하지 않습니다.", 5000)
            return True
        page = self._current_list()
        if page is not None and page.flush_selection():
            self.statusBar().showMessage("고른 태그가 바뀌어 저장하지 않았습니다. 새 태그를 불러왔습니다.", 5000)
            return True
        return False

    def _focus_search(self) -> None:
        page = self._current_list()
        if page is None:
            self.tabs.setCurrentWidget(self.list_page)
            page = self.list_page
        page.search.setFocus()
        page.search.selectAll()

    # ---- 저장 뒤 ----

    def _saved(self, names: List[str], advance: bool) -> None:
        current = self._current_list()
        self._after_change(names, advance_page=current if advance else None)
        if not (advance and current):
            self.editor.set_names(self.editor.names)

    def _after_change(self, names: Sequence[str], advance_page: Optional[TagListPage] = None) -> None:
        for page in (self.list_page, self.queue_page):
            page.after_save(names, advance=page is advance_page)
        self._mark_stale()
        self._update_status()
        self._update_undo()

    def _mark_stale(self) -> None:
        for page in (self.check_page, self.preview_page, self.work_page, self.stats_page):
            page.mark_stale()

    def set_busy(self, label: Optional[str]) -> None:
        """overlay 를 쓰는 작업 동안 편집·되돌리기·다시 불러오기를 막는다(작업이 읽은 뒤 끼어든 저장이 덮이지 않게)."""
        self.busy = label
        self.editor.setEnabled(not label)
        self.reload_action.setEnabled(not label)
        if label:
            self.undo_action.setEnabled(False)
            self.statusBar().showMessage(f"{label} 중…")
        else:
            self._update_undo()
            self.statusBar().clearMessage()

    def undo(self) -> None:
        if not self.store.undo_stack or self.busy:
            return
        try:
            names = self.store.undo()
        except ConflictError as e:
            QMessageBox.warning(self, "다른 곳에서 바뀜", f"{e}\n\n되돌리기 기록은 다시 불러오면 지워집니다.")
            self._show_banner()
            return
        except OSError as e:
            QMessageBox.critical(self, "되돌리기 실패", f"overlay 를 쓰지 못했습니다: {e}")
            return
        self._after_change(names)
        self.editor.refresh()
        self.statusBar().showMessage("되돌림: " + ", ".join(names[:10]) + (" …" if len(names) > 10 else ""), 8000)

    def add_extra(self, names: Sequence[str] = ()) -> None:
        """확장 DB 에 새 태그(이름만). 더한 뒤 태그 목록을 그 DB 로 거르고 새 태그를 골라 편집 패널에 연다."""
        if self.busy:
            return
        dialog = AddExtraDialog(self.store, names, self)
        if dialog.exec() != QDialog.Accepted:
            return
        db, names = dialog.values()
        if not names:
            return
        try:
            added, skipped = self.store.add_extra(db, names)
        except ConflictError as e:
            QMessageBox.warning(self, "다른 곳에서 바뀜", f"{e}\n\n지금 다시 불러옵니다. 다시 더해 주세요.")
            self.reload()
            return
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "더하지 못함", str(e))
            return
        if skipped:
            QMessageBox.information(self, "이미 있는 태그", f"{len(skipped)}개는 이미 있어 건너뛰었습니다:\n\n"
                                    + "\n".join(skipped[:30]) + ("\n…" if len(skipped) > 30 else ""))
        if not added:
            return
        self.tabs.setCurrentWidget(self.list_page)
        self.list_page.show_db(db, added)
        self._after_change(added)
        self.statusBar().showMessage(f"{db_label(db)}에 {len(added)}개 더함 — 분류해 저장하세요(되돌리기로 지움)", 8000)

    def reload(self) -> None:
        if self.busy:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.store.reload()
            finally:
                QApplication.restoreOverrideCursor()
        except (tagdb.OverlayError, FileNotFoundError, ValueError) as e:
            QMessageBox.critical(self, "불러오기 실패", str(e))
            return
        self.banner.hide()
        for page in (self.list_page, self.queue_page):
            page.refresh(keep=True, notify=False)
        self.preview_page.arrange.reload_config(self.store.config)
        self._mark_stale()
        self.editor.refresh()
        self._update_status()
        self._update_undo()
        self.statusBar().showMessage("다시 불러왔습니다", 5000)

    # ---- 상태 ----

    # ---- 테마 ----

    def set_theme(self, mode: str, save: bool = True) -> None:
        """테마를 바꾸고 기억한다. 스타일시트가 못 닿는 곳(표 글자색·미리보기 글·아이콘)은 다시 그린다."""
        self.theme_mode = mode
        if save:
            QSettings("BMK", "TagOrganizer").setValue("theme", mode)
        theme.apply(QApplication.instance(), mode)
        for page in (self.list_page, self.queue_page):
            page.restyle()
        self.editor._update_preview()
        self.editor.evidence.render()
        self.preview_page.arrange.canvas.update()
        self.preview_page.arrange.restyle()
        self.preview_page.run()
        self.work_page.refresh_versions(force=True)
        self.check_page.mark_stale()

    def _scheme_changed(self, *_args) -> None:
        if self.theme_mode == "system" and not theme.applying:
            self.set_theme("system", save=False)       # Windows 에서 라이트/다크를 바꿨다

    def _app_state(self, state) -> None:
        if state == Qt.ApplicationActive:
            self._check_external()

    def _check_external(self) -> None:
        if not self.busy and not self.banner.isVisible() and self.store.external_change():
            self._show_banner()

    def _show_banner(self) -> None:
        self.banner.show()

    def _update_undo(self) -> None:
        stack = self.store.undo_stack
        self.undo_action.setEnabled(bool(stack))
        if stack:
            names = [n for n, _, _ in stack[-1]]
            self.undo_action.setText("되돌리기: " + ", ".join(names[:3]) + (" …" if len(names) > 3 else ""))
        else:
            self.undo_action.setText("되돌리기")

    def _update_status(self) -> None:
        c = self.store.counts()
        parts = [f"원본 {self.store.meta.get('file', '')}", f"overlay {c['overlay']:,}행", f"보류 {c['etc']:,}",
                 f"reviewed {c['reviewed']:,}"]
        if c["unclassified"]:
            parts.append(f"미분류 general {c['unclassified']:,}")
        parts.append(f"확장 DB {c['extra']:,}행")
        if c.get("site"):
            parts.append(f"{self.store.site.id} {c['site']:,}개")
        if c["edited"]:
            parts.append(f"이번에 고친 태그 {c['edited']:,} — 노드 팩 반영은 작업 탭, 커밋은 git")
        self.status.setText("  ·  ".join(parts))
        self.tabs.setTabText(1, f"보류(etc) {c['etc']:,}")

    def closeEvent(self, event) -> None:
        if self.work_page.running:
            # 작업 스레드는 창과 함께 끊긴다: 원본 가져오기가 overlay 만 쓰고 sources.json 을 못 쓴 채 남을 수 있다
            QMessageBox.information(self, "작업 중", f"{self.work_page.running} 이(가) 끝난 뒤에 닫으세요.")
            event.ignore()
            return
        if not self.preview_page.arrange.confirm_close():
            event.ignore()                  # 저장하지 않은 배열 변경(분류 편집과 달리 바로 저장되지 않음)
            return
        settings = QSettings("BMK", "TagOrganizer")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter", self.splitter.saveState())
        self.preview_page.save_state(settings)
        super().closeEvent(event)


def main(argv: Optional[Sequence[str]] = None) -> int:
    app = QApplication.instance() or QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("TagOrganizer")
    app.setStyle("Fusion")                              # Studio 와 같다: 스타일시트가 모든 위젯에 고르게 먹는다
    theme.apply(app, QSettings("BMK", "TagOrganizer").value("theme") or "system")
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        store = load_store()
    except (tagdb.OverlayError, OSError, ValueError) as e:
        QApplication.restoreOverrideCursor()
        QMessageBox.critical(None, "TagOrganizer — 불러오기 실패", str(e))
        return 1
    window = MainWindow(store)
    QApplication.restoreOverrideCursor()
    window.show()
    return app.exec()


def load_store() -> Store:
    """원본 CSV 는 git 에 넣지 않으므로 다른 PC 에서 처음 열면 없다 — sources.json 의 주소에서 받을지 묻는다.
    Danbooru 와 두 번째 원본(e621)이 차례로 없을 수 있어, 받은 파일마다 한 번씩 다시 연다."""
    fetched = set()
    while True:
        try:
            return Store()
        except tagdb.SourceMissing as e:
            if not e.fetchable or e.path in fetched:
                raise
            QApplication.restoreOverrideCursor()
            answer = QMessageBox.question(
                None, "TagOrganizer — 원본 CSV 없음",
                f"원본 CSV가 없습니다: {e.path}\n\n지금 받을까요? (받은 뒤 sources.json 의 sha256 과 맞는지 확인합니다)\n"
                f"{tagdb.download_url(e.entry['url'])}")
            if answer != QMessageBox.Yes:
                raise
            QApplication.setOverrideCursor(Qt.WaitCursor)
            tagdb.fetch_source(e.entry, e.path)
            fetched.add(e.path)
