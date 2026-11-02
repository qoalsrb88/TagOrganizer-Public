"""관리 툴 2단계 테스트 — 미리보기 팩·빌드·노드 팩 반영·원본 가져오기·통계. 임시 폴더만 쓴다."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fixtures import SOURCE, builder, tagdb
from gui import ops
from test_gui import fixture_rows, make_env, make_store
from tools import importer, sync

MARKER = '"""ComfyUI_BMK_Nodes — 통합 로더"""\n'


def node_pack(tmp: str) -> Path:
    target = Path(tmp, "ComfyUI_BMK_Nodes")
    target.mkdir()
    (target / "__init__.py").write_text(MARKER, encoding="utf-8")
    return target


class OpsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.store, self.path = make_store(self.tmp)
        self.env = make_env(self.tmp, self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_and_versions(self):
        built = ops.build(self.env)
        self.assertTrue(self.env.pack_path.is_file())
        self.assertEqual(ops.built_version(self.env.pack_path), built.version)
        memory = ops.memory_inputs(self.store, self.env.extra())
        self.assertEqual(ops.version_of(memory), built.version)               # 메모리 = 파일이면 같은 버전
        self.assertEqual(ops.memory_pack(memory).version, built.version)
        self.store.apply(["smile"], {"rating": "s"})
        self.assertNotEqual(ops.version_of(ops.memory_inputs(self.store, self.env.extra())), built.version)
        self.assertIsNone(ops.built_version(Path(self.tmp, "none.json.gz")))

    def test_build_error(self):
        self.store.overlay["#NAME?"] = {**self.store.row("smile"), "name": "#NAME?"}
        tagdb.write_overlay(self.store.overlay, self.path)
        with self.assertRaises(builder.BuildError):
            ops.build(self.env)
        self.assertFalse(self.env.pack_path.exists())

    def test_sync_preview_writes_nothing(self):
        target = node_pack(self.tmp)
        built, actions = ops.sync_preview(self.env, target)
        self.assertEqual({a.state for a in actions}, {"create"})
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["__init__.py"])
        self.assertFalse(self.env.pack_path.exists())                          # build/ 도 그대로
        self.assertFalse(self.env.sync_file.exists())

    def test_sync_apply_and_conflict(self):
        target = node_pack(self.tmp)
        built, actions = ops.sync_apply(self.env, target)
        self.assertEqual({a.state for a in actions}, {"create"})
        self.assertTrue(self.env.pack_path.is_file())                          # CLI 처럼 build/ 에 빌드
        self.assertEqual(ops.sync_target(self.env), target.resolve())
        _, actions = ops.sync_preview(self.env, target)
        self.assertEqual({a.state for a in actions}, {"unchanged"})
        (target / sync.CORE_REL).write_text("# 노드 팩에서 직접 고침\n", encoding="utf-8")
        _, actions = ops.sync_preview(self.env, target)                        # 미리보기는 멈추지 않고 보여 줌
        self.assertEqual({a.rel: a.state for a in actions}[sync.CORE_REL], "conflict")
        with self.assertRaises(sync.SyncConflict):
            ops.sync_apply(self.env, target)
        self.assertEqual((target / sync.CORE_REL).read_text(encoding="utf-8"), "# 노드 팩에서 직접 고침\n")
        ops.sync_apply(self.env, target, force=True)
        self.assertNotEqual((target / sync.CORE_REL).read_text(encoding="utf-8"), "# 노드 팩에서 직접 고침\n")

    def test_built_version_of_broken_file(self):
        # 빌드가 쓰는 중인 파일을 읽어도(잘린 gzip) 예외 대신 None
        ops.build(self.env)
        self.env.pack_path.write_bytes(self.env.pack_path.read_bytes()[:5000])
        self.assertIsNone(ops.built_version(self.env.pack_path))

    def test_import_passes_dry_run(self):
        calls = []
        env = make_env(self.tmp, self.path, import_source=lambda *a, **kw: calls.append((a, kw)) or "보고서")
        self.assertEqual(ops.import_csv(env, Path("x_2026-11-01.csv"), "", "u", dry_run=True), "보고서")
        self.assertEqual(calls[0], ((Path("x_2026-11-01.csv"), None, None, "u", False), {"dry_run": True}))


class ImporterTests(unittest.TestCase):
    """importer.import_source 의 dry_run 과 '읽은 뒤 다른 곳에서 저장' 감지 — 모듈 경로를 임시 폴더로 바꾼다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.source_dir, self.reports = tmp / "source", tmp / "reports"
        self.source_dir.mkdir()
        self.overlay = tmp / "tags.csv"
        tagdb.write_overlay(fixture_rows(), self.overlay)
        self.manifest = self.source_dir / "sources.json"
        self.write_csv(self.source_dir / "danbooru_2026-10-01.csv", SOURCE)
        self.new_csv = tmp / "danbooru_2026-11-01.csv"
        self.write_csv(self.new_csv, [t for t in SOURCE if t[0] != "smile"] + [("brand_new_tag", 0, 5, ())])
        self.patches = [
            mock.patch.object(importer, "SOURCE_DIR", self.source_dir),
            mock.patch.object(importer, "REPORTS", self.reports),
            mock.patch.object(importer, "OVERLAY", self.overlay),
            mock.patch.object(importer, "read_overlay", lambda: tagdb.read_overlay(self.overlay)),
            mock.patch.object(tagdb, "MANIFEST", self.manifest),
        ]
        for p in self.patches:
            p.start()
        tagdb.write_manifest({"current": "danbooru_2026-10-01.csv", "files": [
            {"file": "danbooru_2026-10-01.csv", "date": "2026-10-01"}]})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    @staticmethod
    def write_csv(path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            for name, type_, count, aliases in rows:
                f.write(f'{name},{type_},{count},"{",".join(aliases)}"\n')

    def snapshot(self):
        return {p.relative_to(self._tmp.name).as_posix(): p.read_bytes()
                for p in Path(self._tmp.name).rglob("*") if p.is_file()}

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        report = importer.import_source(self.new_csv, dry_run=True)
        self.assertIn("원본에서 사라짐: smile", report)
        self.assertEqual(self.snapshot(), before)

    def test_import_writes(self):
        report = importer.import_source(self.new_csv)
        self.assertIn("원본에서 사라짐: smile", report)
        self.assertEqual(tagdb.read_overlay(self.overlay)["smile"]["missing_since"], "2026-11-01")
        self.assertEqual(tagdb.read_manifest()["current"], "danbooru_2026-11-01.csv")
        self.assertTrue((self.source_dir / "danbooru_2026-11-01.csv").is_file())
        self.assertTrue((self.reports / "import-2026-11-01.md").is_file())

    def test_overlay_saved_elsewhere_stops_import(self):
        real = tagdb.read_overlay

        def read_then_someone_saves():
            rows = real(self.overlay)
            outside = real(self.overlay)
            outside["blush"] = {**outside["blush"], "rating": "q"}
            tagdb.write_overlay(outside, self.overlay)              # 관리 툴이 그사이 저장
            return rows

        before_manifest = self.manifest.read_bytes()
        with mock.patch.object(importer, "read_overlay", read_then_someone_saves):
            with self.assertRaises(tagdb.OverlayChanged):
                importer.import_source(self.new_csv)
        self.assertEqual(tagdb.read_overlay(self.overlay)["blush"]["rating"], "q")     # 덮어쓰지 않음
        self.assertEqual(self.manifest.read_bytes(), before_manifest)
        self.assertFalse((self.source_dir / "danbooru_2026-11-01.csv").exists())


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from gui import jobs
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class WorkWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.app import MainWindow
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.store, self.path = make_store(self.tmp)
        self.env = make_env(self.tmp, self.path)
        with mock.patch("gui.app.QSettings") as settings:
            settings.return_value.value.return_value = None
            self.win = MainWindow(self.store, self.env)
        self.editor = self.win.editor

    def tearDown(self):
        self.wait()
        self.win._poll.stop()
        with mock.patch("gui.app.QSettings"):
            self.win.close()
        self.win.deleteLater()
        QTest.qWait(1)
        self._tmp.cleanup()

    def wait(self, timeout=15.0):
        end = time.time() + timeout
        while jobs.active and time.time() < end:
            QTest.qWait(20)
        QTest.qWait(20)
        self.assertEqual(jobs.active, 0, "백그라운드 작업이 끝나지 않음")

    def test_preview_uses_current_edits(self):
        page = self.win.preview_page
        page.ensure_pack()
        self.wait()
        self.assertIn("1girl", page.output.toPlainText())
        self.assertIn("hatsune miku", page.removed.toPlainText())             # character_swap 이 캐릭터를 뺌
        self.assertNotIn("smile", page.removed.toPlainText())
        self.store.apply(["smile"], {"primary": "subject/character"})          # 저장된 편집이 바로 반영되는지
        page.ensure_pack()
        self.wait()
        self.assertIn("smile", page.removed.toPlainText())
        self.assertEqual(page.pack_rev, self.store.revision)
        self.win.tabs.setCurrentWidget(page)
        self.win.show()                                                        # 보이는 탭만 편집 패널을 바꾼다
        self.wait()
        row = next(i for i in range(page.tokens.rowCount()) if page.tokens.item(i, 0).text() == "upper body")
        page.tokens.selectRow(row)
        self.assertEqual(self.editor.names, ["upper_body"])                    # 토큰을 고르면 편집 패널에

    def test_preview_selection_follows_tokens(self):
        # 리뷰 3: 이름 없는 토큰을 고르거나 다시 계산해 고른 토큰이 사라지면 편집 패널도 비운다
        self.win.tabs.setCurrentWidget(self.win.preview_page)
        self.win.show()
        page = self.win.preview_page
        page.input.setPlainText("smile, xyzzy_unknown")
        page.ensure_pack()
        self.wait()
        page.run()
        rows = {page.tokens.item(i, 0).text(): i for i in range(page.tokens.rowCount())}
        page.tokens.selectRow(rows["smile"])
        self.assertEqual(self.editor.names, ["smile"])
        page.tokens.selectRow(rows["xyzzy_unknown"])
        self.assertEqual(self.editor.names, [])
        page.tokens.selectRow(rows["smile"])
        page.input.setPlainText("smile, blush")                               # 다시 계산해도 smile 은 그대로 고름
        page.run()
        self.assertEqual(page.selected_names(), ["smile"])
        self.assertEqual(self.editor.names, ["smile"])
        page.input.setPlainText("blush")                                      # 사라지면 비움
        page.run()
        self.assertEqual(self.editor.names, [])

    def test_preview_bad_option(self):
        page = self.win.preview_page
        page.ensure_pack()
        self.wait()
        page.exclude.setText("nope/what")
        page.run()
        self.assertIn("알 수 없는", page.status.text())

    def test_build_button(self):
        work = self.win.work_page
        work.build()
        self.wait()
        self.assertTrue(self.env.pack_path.is_file())
        self.assertIn("팩 2026.10.01+", work.log.toPlainText())
        work.refresh_versions(force=True)
        self.wait()
        self.assertIn("최신", work.version_label.text())
        self.store.apply(["smile"], {"rating": "s"})
        work.refresh_versions()
        self.wait()
        self.assertIn("다시 빌드", work.version_label.text())

    def test_version_rechecked_after_outside_build(self):
        # 리뷰 4: 다른 곳(CLI)이 build/ 를 다시 빌드하면 편집이 없어도 다시 확인한다
        work = self.win.work_page
        work.build()
        self.wait()
        work.refresh_versions(force=True)
        self.wait()
        self.assertIn("최신", work.version_label.text())
        rows = tagdb.read_overlay(self.path)
        rows["smile"] = {**rows["smile"], "rating": "q"}
        pack, _ = builder.build_pack(*self._inputs(rows))
        time.sleep(0.02)
        builder.write_pack(pack, self.env.pack_path)
        work.refresh_versions()
        self.wait()
        self.assertIn("다시 빌드", work.version_label.text())

    def _inputs(self, rows):
        i = self.env.inputs()
        return i.source, i.meta, rows, i.extra, i.config

    def test_sync_buttons(self):
        work = self.win.work_page
        target = node_pack(self.tmp)
        work.target_edit.setText(str(target))
        work.sync_preview()
        self.wait()
        self.assertIn("새로 만듦", work.log.toPlainText())
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["__init__.py"])
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.No):
            work.sync_apply(force=False)
        self.wait()
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["__init__.py"])     # 취소하면 안 씀
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.Yes):
            work.sync_apply(force=False)
        self.wait()
        self.assertTrue((target / sync.PACK_REL).is_file())
        self.assertIn("노드 팩에서 커밋할 파일", work.log.toPlainText())
        (target / sync.CORE_REL).write_text("# 직접 고침\n", encoding="utf-8")
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.Yes), \
                mock.patch("gui.work.QMessageBox.warning", return_value=QMessageBox.No) as warn:
            work.sync_apply(force=False)
            self.wait()
        warn.assert_called_once()
        self.assertEqual((target / sync.CORE_REL).read_text(encoding="utf-8"), "# 직접 고침\n")   # 덮어쓰지 않음

    def test_force_retry_uses_confirmed_target(self):
        # 리뷰 1: 반영 도중 대상 칸을 바꿔도, 덮어쓰기는 처음 확인받은 폴더에만
        work = self.win.work_page
        first, other = node_pack(self.tmp), Path(self.tmp, "other")
        other.mkdir()
        second = node_pack(str(other))
        ops.sync_apply(self.env, first)
        (first / sync.CORE_REL).write_text("# 직접 고침\n", encoding="utf-8")
        work.target_edit.setText(str(first))
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.Yes), \
                mock.patch("gui.work.QMessageBox.warning", return_value=QMessageBox.Yes):
            work.sync_apply(force=False)
            work.target_edit.setText(str(second))                             # 도는 동안 바꿈
            self.wait()
            QTest.qWait(50)
            self.wait()
        self.assertNotEqual((first / sync.CORE_REL).read_text(encoding="utf-8"), "# 직접 고침\n")
        self.assertEqual(sorted(x.name for x in second.iterdir()), ["__init__.py"])

    def test_cannot_close_while_writing(self):
        # 리뷰 2: 원본 가져오기가 도는 동안 창을 닫지 않는다(작업 스레드가 끊기지 않게)
        gate = threading.Event()
        self.env.import_source = lambda *a, **kw: gate.wait(10) and "보고서"
        work = self.win.work_page
        work.csv_edit.setText(str(Path(self.tmp, "danbooru_2026-11-01.csv")))
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.Yes):
            work.import_csv(dry_run=False)
        QTest.qWait(30)
        with mock.patch("gui.app.QMessageBox.information") as info, mock.patch("gui.app.QSettings"):
            self.assertFalse(self.win.close())
        info.assert_called_once()
        gate.set()
        self.wait()
        with mock.patch("gui.app.QSettings"):
            self.assertTrue(self.win.close())

    def test_import_blocks_edits_and_reloads(self):
        gate = threading.Event()

        def fake_import(csv_path, date, name, url, move, dry_run=False):
            gate.wait(10)
            if not dry_run:                                     # 가져오기가 overlay 를 고친 것처럼
                rows = tagdb.read_overlay(self.path)
                rows["smile"] = {**rows["smile"], "missing_since": "2026-11-01"}
                tagdb.write_overlay(rows, self.path)
            return "# 원본 가져오기 2026-11-01\n- 원본에서 사라짐: smile\n"

        self.env.import_source = fake_import
        work = self.win.work_page
        work.csv_edit.setText(str(Path(self.tmp, "danbooru_2026-11-01.csv")))
        self.editor.set_names(["blush"])
        self.editor.rating_group.buttons()[2].click()                         # 입력 중이던 값
        with mock.patch("gui.work.QMessageBox.question", return_value=QMessageBox.Yes):
            work.import_csv(dry_run=False)
        QTest.qWait(50)
        self.assertEqual(self.win.busy, "원본 가져오기")
        self.assertFalse(self.editor.isEnabled())
        self.editor.save(advance=False)                                       # 가져오는 동안 저장 안 됨
        self.assertEqual(tagdb.read_overlay(self.path)["blush"]["rating"], "g")
        gate.set()
        self.wait()
        self.assertIsNone(self.win.busy)
        self.assertTrue(self.editor.isEnabled())
        self.assertEqual(self.store.row("smile")["missing_since"], "2026-11-01")     # 끝나면 다시 불러옴
        self.assertFalse(self.win.banner.isVisible())
        self.assertIn("원본에서 사라짐: smile", work.log.toPlainText())

    def test_import_preview_does_not_block(self):
        self.env.import_source = lambda *a, **kw: "보고서(미리보기)"
        work = self.win.work_page
        work.csv_edit.setText("x_2026-11-01.csv")
        work.import_csv(dry_run=True)
        self.assertIsNone(self.win.busy)
        self.wait()
        self.assertIn("아무것도 쓰지 않음", work.log.toPlainText())

    def test_preview_options_survive_restart(self):
        # 미리보기 위쪽 옵션(프리셋·등급·구도 규칙 등)을 고르면 끄고 다시 켜도 그대로
        from PySide6.QtCore import QSettings
        from gui.work import PreviewPage
        settings = QSettings(str(Path(self.tmp, "preview.ini")), QSettings.IniFormat)
        page = PreviewPage(self.store, self.env, settings=settings)
        page.preset.setCurrentIndex(page.preset.findData("sort_only"))
        page.max_rating.setCurrentIndex(page.max_rating.findData("s"))
        page.rules.setCurrentIndex(page.rules.findData("off"))
        page.order.setCurrentIndex(page.order.findData("alpha"))
        page.one_line.setChecked(True)
        page.exclude.setText("body/hair")
        page.show_removed.setChecked(False)
        page.result_tabs.setCurrentIndex(1)
        page.input.setPlainText("smile, blush")
        page.set_filter("removed")
        page.dialect.setCurrentIndex(page.dialect.findData("nai"))
        page.priority.setCurrentIndex(page.priority.findData("comfyui,nai,anima"))
        page.comments.setCurrentIndex(page.comments.findData("keep"))
        page.sentences.setChecked(False)
        page.save_state(settings)
        page.deleteLater()
        again = PreviewPage(self.store, self.env, settings=QSettings(str(Path(self.tmp, "preview.ini")),
                                                                       QSettings.IniFormat))
        self.assertEqual(again.options()["preset"], "sort_only")
        self.assertEqual(again.options()["max_rating"], "s")
        self.assertEqual(again.options()["rules"], "off")
        self.assertEqual(again.options()["order"], "alpha")
        self.assertFalse(again.options()["line_per_section"])
        self.assertEqual(again.options()["exclude"], "body/hair")
        self.assertFalse(again.show_removed.isChecked())
        self.assertEqual(again.result_tabs.currentIndex(), 1)
        self.assertEqual(again.input.toPlainText(), "smile, blush")
        self.assertEqual(again.filter, "removed")
        self.assertEqual(again.options()["input_dialect"], "nai")
        self.assertEqual(again.options()["dialect_priority"], "comfyui,nai,anima")
        self.assertFalse(again.priority.isEnabled())                          # 자동일 때만 우선순위를 고른다
        self.assertEqual(again.options()["comments"], "keep")
        self.assertFalse(again.options()["sentences"])
        # 없어진 프리셋·모르는 값·깨진 저장 값은 기본값 그대로
        settings.setValue("preview_options", '{"preset": "gone", "result_tab": 99, "one_line": "yes"}')
        third = PreviewPage(self.store, self.env, settings=settings)
        self.assertEqual(third.options()["preset"], "character_swap")
        self.assertEqual(third.result_tabs.currentIndex(), 0)
        self.assertTrue(third.options()["line_per_section"])
        settings.setValue("preview_options", '{"filter": [], "input_dialect": {"x": 1}}')   # 리뷰 4: 자료형이 다른 값
        PreviewPage(self.store, self.env, settings=settings).deleteLater()
        settings.setValue("preview_options", "{broken")
        PreviewPage(self.store, self.env, settings=settings).deleteLater()
        again.deleteLater()
        third.deleteLater()

    def test_preview_syntax_auto_comments_sentences(self):
        # 자동 문법(근거 표시)·NAI 숫자 닫기 경고·주석·문장이 화면에 보이는지
        from gui.work import rich_html
        page = self.win.preview_page
        self.assertEqual(page.options()["input_dialect"], "auto")                # 처음에는 자동
        self.assertTrue(page.priority.isEnabled())
        page.preset.setCurrentIndex(page.preset.findData("sort_only"))
        page.input.setPlainText("1.1::artist:as109::, 1girl // note, here\nshe stands in the rain. smile")
        page.ensure_pack()
        self.wait()
        page.run()
        status = page.status.text()
        self.assertIn("문법 자동 → <b>NAI</b>", status)
        self.assertIn("NAI ‘1.1::’", status)
        self.assertIn("경고: as109", status)
        self.assertIn("1.1::artist:as109 ::", page.output.toPlainText())
        self.assertNotIn("note", page.output.toPlainText())                     # 주석은 기본으로 뺌
        notes = {page.tokens.item(i, 0).text(): page.tokens.item(i, 5).text() for i in range(page.tokens.rowCount())}
        self.assertIn("주석", notes["// note, here"])
        self.assertIn("자연어 문장", notes["she stands in the rain."])
        self.assertIn("NAI 는 '109::'", notes["as109"])
        page.set_filter("syntax")
        shown = [page.tokens.item(i, 0).text() for i in range(page.tokens.rowCount()) if not page.tokens.isRowHidden(i)]
        self.assertEqual(shown, ["// note, here", "she stands in the rain."])
        page.set_filter("all")
        page.comments.setCurrentIndex(page.comments.findData("keep"))
        page.run()
        self.assertTrue(page.output.toPlainText().startswith("// note, here\n"))
        page.input.setPlainText("(smile:1.2), 1.1::blush::")
        page.run()
        self.assertIn("표기가 섞여", page.status.text())
        page.input.setPlainText("1girl, a girl walks. she smiles.")
        page.run()
        self.assertIn("a girl walks.</span></a> <a", rich_html(page.report))     # 문장 사이는 쉼표 없이
        page.sentences.setChecked(False)
        page.run()
        self.assertEqual(page.report["counts"]["sentences"], 0)
        page.dialect.setCurrentIndex(page.dialect.findData("comfyui"))
        self.assertFalse(page.priority.isEnabled())
        page.run()
        self.assertNotIn("문법 자동", page.status.text())

    def test_stats(self):
        page = self.win.stats_page
        page.refresh(force=True)
        self.wait()
        self.assertEqual(page.table.rowCount(), 5)
        self.assertEqual(page.table.item(0, 0).text(), "M1")
        self.assertIn("overlay", page.text.toPlainText())


if __name__ == "__main__":
    unittest.main()
