"""관리 툴(GUI) 테스트 — 데이터 계층(store)은 Qt 없이, 화면은 PySide6 가 있으면 offscreen 으로."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fixtures import EXTRA, ROOT, SOURCE, overlay_rows, source_tags, tagdb
from gui import ops
from gui import store as gstore
from gui.store import AUTO, UNCLASSIFIED, ConflictError, Query, Store
from tools import evidence, sync

META = {"file": "fixture.csv", "date": "2026-10-01"}


def make_store(tmp: str, rows=None, extra_rows=None):
    path = Path(tmp, "tags.csv")
    tagdb.write_overlay(rows if rows is not None else fixture_rows(), path)
    committed = {"rows": tagdb.read_overlay(path)}
    extra_dir = Path(tmp, "extra")                       # 실제 data/extra 가 아니라 픽스처 확장 DB
    for db in dict.fromkeys(r["db"] for r in (EXTRA if extra_rows is None else extra_rows)):
        tagdb.write_extra(db, [r for r in (EXTRA if extra_rows is None else extra_rows) if r["db"] == db], extra_dir)
    loader = lambda: (source_tags(), tagdb.read_overlay(path), tagdb.load_config(), dict(META))  # noqa: E731
    return Store(loader, path, committed=lambda: committed["rows"], extra_dir=extra_dir), path


def make_env(tmp: str, overlay_path: Path, **kw) -> ops.Env:
    """작업·미리보기 탭용 경로를 모두 임시 폴더로(실제 build/·local/·노드 팩·data/source 를 건드리지 않게)."""
    def inputs():
        return ops.Inputs(source_tags(), {**META, "rows": len(SOURCE)}, tagdb.read_overlay(overlay_path),
                          list(EXTRA), tagdb.load_config())

    def no_import(*args, **kwargs):
        raise AssertionError("테스트가 실제 원본 가져오기를 부름")

    def no_network(*args, **kwargs):
        raise AssertionError("테스트가 Danbooru 를 부름")

    defaults = dict(pack_path=Path(tmp, "build", "tagpack.json.gz"), sync_file=Path(tmp, "local", "sync.json"),
                    core_path=ROOT / sync.CORE_REL, repo=None, inputs=inputs, extra=lambda: list(EXTRA),
                    import_source=no_import, evidence_cache=Path(tmp, "evidence", "evidence.json"),
                    evidence_get=no_network, evidence_delay=0, arrangements_dir=Path(tmp, "arrangements"),
                    shared_arrangements_dir=Path(tmp, "shared"))
    return ops.Env(**{**defaults, **kw})


def fixture_rows():
    rows = overlay_rows()
    rows["smile"]["status"] = "accepted"
    rows["smile"]["note"] = "LLM 근거"
    for name in ("maple_leaf", "ginkgo_leaf"):
        rows[name] = {**rows[name], "primary": "etc", "rating": "", "regions": "", "status": "accepted",
                      "note": "의미 불확실"}
    return rows


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store, self.path = make_store(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, **kw):
        return [e.name for e in self.store.find(Query(**kw))]

    def test_find_filters(self):
        self.assertEqual(self.names(primary="body/hair")[:2], ["long_hair", "very_long_hair"])   # 사용량 순
        self.assertTrue(all(n in ("maple_leaf", "ginkgo_leaf") for n in self.names(primary="etc")))
        self.assertEqual(self.names(primary=UNCLASSIFIED), ["unclassified_thing"])
        self.assertEqual(self.names(type=None, status=AUTO)[:2], ["vocaloid", "hatsune_miku"])
        self.assertNotIn("hatsune_miku", self.names())                                            # 기본은 일반만
        self.assertEqual(self.names(type=None, primary="subject/character"), ["hatsune_miku", "sagisawa_fumika"])
        self.assertEqual(self.names(rating="q"), ["nipples"])
        self.assertEqual(self.names(status="accepted", primary="action"), ["smile"])

    def test_search(self):
        self.assertEqual(self.names(text="Long Hair")[:2], ["long_hair", "very_long_hair"])
        self.assertEqual(self.names(text=r"star \(symbol\)"), ["star_(symbol)"])
        self.assertEqual(self.names(text="wink")[0], "one_eye_closed")          # 별칭으로 정확히 맞으면 맨 앞
        self.assertEqual(self.names(text="hatsune miku")[0], "hatsune_miku")    # 종류 필터와 상관없이
        self.assertEqual(self.names(text="불확실"), ["maple_leaf", "ginkgo_leaf"])  # 메모

    def test_primary_of(self):
        by = self.store.by_name
        self.assertEqual(self.store.primary_of(by["wlop"]), ("style/artist", AUTO))
        self.assertEqual(self.store.primary_of(by["unclassified_thing"]), ("", ""))
        self.assertEqual(self.store.primary_of(by["smile"]), ("action/expression", "accepted"))

    def test_apply_saves_and_locks(self):
        res = self.store.apply(["smile"], {"rating": "s"})
        self.assertEqual(res.errors, [])
        row = tagdb.read_overlay(self.path)["smile"]
        self.assertEqual((row["rating"], row["status"]), ("s", "reviewed"))
        self.assertEqual(row["note"], "LLM 근거")                     # primary 를 안 바꾸면 note 는 남는다
        self.assertFalse(self.store.external_change())
        self.assertEqual(self.store.counts()["edited"], 1)

    def test_errors_write_nothing(self):
        before = self.path.read_bytes()
        res = self.store.apply(["smile", "blush"], {"primary": "clothes/nope"})
        self.assertEqual(len(res.errors), 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.row("smile")["primary"], "action/expression")
        self.assertEqual(self.store.undo_stack, [])

    def test_preview_changes_nothing(self):
        before = self.path.read_bytes()
        res = self.store.preview(["smile"], {"primary": "object/prop"})
        self.assertEqual(res.changes[0][2]["primary"], "object/prop")
        self.assertEqual(self.store.row("smile")["primary"], "action/expression")
        self.assertEqual(self.path.read_bytes(), before)

    def test_conflict_is_not_overwritten(self):
        outside = tagdb.read_overlay(self.path)
        outside["blush"] = {**outside["blush"], "rating": "q"}
        tagdb.write_overlay(outside, self.path)                     # CLI·다른 세션이 고침
        self.assertTrue(self.store.external_change())
        with self.assertRaises(ConflictError):
            self.store.apply(["smile"], {"rating": "s"})
        self.assertEqual(self.store.row("smile")["rating"], "g")
        self.assertEqual(tagdb.read_overlay(self.path)["blush"]["rating"], "q")
        self.store.reload()
        self.assertEqual(self.store.row("blush")["rating"], "q")
        self.store.apply(["smile"], {"rating": "s"})
        on_disk = tagdb.read_overlay(self.path)
        self.assertEqual((on_disk["smile"]["rating"], on_disk["blush"]["rating"]), ("s", "q"))

    def test_undo_restores_previous_row(self):
        original = dict(self.store.row("smile"))
        self.store.apply(["smile"], {"primary": "action/gesture"})
        self.store.apply(["unclassified_thing"], {"note": "뜻 모름"}, etc=True)   # 새 행
        self.assertIn("unclassified_thing", tagdb.read_overlay(self.path))
        self.assertEqual(self.store.undo(), ["unclassified_thing"])
        self.assertNotIn("unclassified_thing", tagdb.read_overlay(self.path))
        self.assertEqual(self.store.undo(), ["smile"])
        self.assertEqual(tagdb.read_overlay(self.path)["smile"], original)    # status·note·by 까지 그대로
        self.assertEqual(self.store.undo(), [])

    def test_undo_refuses_after_external_change(self):
        self.store.apply(["smile"], {"rating": "s"})
        self.path.write_text(self.path.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8-sig")
        with self.assertRaises(ConflictError):
            self.store.undo()
        self.assertEqual(len(self.store.undo_stack), 1)

    def test_write_failure_rolls_back(self):
        before = self.path.read_bytes()
        with mock.patch.object(gstore.tagdb, "write_overlay", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                self.store.apply(["smile"], {"rating": "s"})
        self.assertEqual(self.store.row("smile")["rating"], "g")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.undo_stack, [])

    def test_max_edit(self):
        names = [f"x{i}" for i in range(gstore.MAX_EDIT + 1)]
        self.assertIn("까지", self.store.apply(names, {"rating": "s"}).errors[0])
        self.assertIn("까지", self.store.preview(names, {"rating": "s"}).errors[0])

    def test_common_and_similar(self):
        common = self.store.common(["smile", "blush"])
        self.assertEqual((common["primary"], common["rating"], common["note"]), ("action/expression", "g", None))
        self.assertIsNone(self.store.common(["smile", "long_hair"])["primary"])
        similar = [e.name for e, _ in self.store.similar("white_shirt")]
        self.assertEqual(similar, ["shirt", "grey_shirt"])                    # 같은 계열, 사용량 순, 자신 제외
        self.assertNotIn("maple_leaf", [e.name for e, _ in self.store.similar("leaf")])   # 보류 행은 선례가 아님

    def test_regions_plan(self):
        plan = dict((v["regions"], g) for g, v in self.store.regions_plan(["smile", "long_hair"], {}, ["hips"], []))
        self.assertEqual(plan, {"face|hips": ["smile"], "head|hips": ["long_hair"]})
        plan = self.store.regions_plan(["smile", "long_hair"], {"rating": "s"}, [], ["face"])
        self.assertEqual(sorted((g[0], u["regions"], u["rating"]) for g, u in plan),
                         [("long_hair", "head", "s"), ("smile", "", "s")])
        self.store.overlay["shirt"]["regions"] = "back|front_torso"          # 규칙 순서와 다른 저장 순서
        plan = self.store.regions_plan(["shirt"], {}, ["front_torso"], [])
        self.assertEqual(plan, [(["shirt"], {"regions": "back|front_torso"})])   # 안 바뀌면 그대로

    def test_plan_is_all_or_nothing(self):
        before = self.path.read_bytes()
        res = self.store.apply_plan([(["smile"], {"rating": "s"}), (["blush"], {"primary": "clothes/nope"})])
        self.assertEqual((res.changes, len(res.errors)), ([], 1))
        self.assertEqual(self.store.row("smile")["rating"], "g")
        self.assertEqual(self.path.read_bytes(), before)
        res = self.store.apply_plan([(["smile"], {"rating": "s"}), (["blush"], {"rating": "q"})])
        self.assertEqual(len(res.changes), 2)
        self.assertEqual(len(self.store.undo_stack), 1)                        # 되돌리기 한 번에 둘 다
        self.assertEqual(sorted(self.store.undo()), ["blush", "smile"])

    def test_hash_is_what_we_wrote(self):
        # 리뷰 2: 내가 바꿔 넣은 직후 다른 프로그램이 저장해도, 그 내용을 내 저장으로 착각하지 않는다
        real = tagdb.write_overlay

        def write_then_outside(rows, path, expected):
            digest = real(rows, path, expected=expected)
            outside = tagdb.read_overlay(path)
            outside["blush"] = {**outside["blush"], "rating": "q"}
            real(outside, path)
            return digest

        with mock.patch.object(gstore.tagdb, "write_overlay", side_effect=write_then_outside):
            self.store.apply(["smile"], {"rating": "s"})
        self.assertTrue(self.store.external_change())
        with self.assertRaises(ConflictError):
            self.store.apply(["long_hair"], {"rating": "s"})
        self.assertEqual(tagdb.read_overlay(self.path)["blush"]["rating"], "q")     # 밖의 저장이 남음

    def test_change_between_check_and_swap(self):
        # 리뷰 2: 검사(_ensure_unchanged)와 바꿔 넣기 사이에 끼어든 저장도 덮어쓰지 않는다
        real = tagdb.write_overlay

        def outside_then_write(rows, path, expected):
            outside = tagdb.read_overlay(path)
            outside["blush"] = {**outside["blush"], "rating": "q"}
            real(outside, path)
            return real(rows, path, expected=expected)

        with mock.patch.object(gstore.tagdb, "write_overlay", side_effect=outside_then_write):
            with self.assertRaises(ConflictError):
                self.store.apply(["smile"], {"rating": "s"})
        self.assertEqual(self.store.row("smile")["rating"], "g")                      # 메모리도 되돌림
        self.assertEqual(self.store.undo_stack, [])
        on_disk = tagdb.read_overlay(self.path)
        self.assertEqual((on_disk["smile"]["rating"], on_disk["blush"]["rating"]), ("g", "q"))

    def test_check(self):
        report = self.store.check()
        self.assertEqual((report.errors, report.diff), ([], []))
        self.store.apply(["smile"], {"rating": "s"})
        report = self.store.check()
        self.assertEqual([n for n, _, _ in report.diff], ["smile"])
        self.assertEqual(report.diff[0][1]["rating"], "g")
        self.assertIsInstance(report.lint, list)


class WriteOverlayTests(unittest.TestCase):
    def test_failed_replace_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "tags.csv")
            tagdb.write_overlay(overlay_rows(), path)
            before = path.read_bytes()
            with mock.patch.object(tagdb.os, "replace", side_effect=PermissionError("locked")), \
                    mock.patch.object(tagdb.time, "sleep"):
                with self.assertRaises(PermissionError):
                    tagdb.write_overlay({}, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["tags.csv"])   # 임시 파일 안 남음

    def test_temp_file_is_unique_and_hash_returned(self):
        # 리뷰 1: 다른 프로그램이 쓰는 중인 임시 파일을 덮어쓰거나 지우지 않는다
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "tags.csv")
            other = Path(tmp, "tags.csv.tmp")
            other.write_text("다른 프로그램이 쓰는 중", encoding="utf-8")
            digest = tagdb.write_overlay(overlay_rows(), path)
            self.assertEqual(other.read_text(encoding="utf-8"), "다른 프로그램이 쓰는 중")
            self.assertEqual(digest, tagdb.sha256_file(path))
            with self.assertRaises(tagdb.OverlayChanged):
                tagdb.write_overlay({}, path, expected="0" * 64)
            self.assertEqual(tagdb.sha256_file(path), digest)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["tags.csv", "tags.csv.tmp"])


class ThemeSourceTests(unittest.TestCase):
    def test_no_hard_coded_colors(self):
        # 화면 색은 gui/theme.py 의 토큰만 쓴다(다크 테마에서 깨지지 않게)
        import re
        gui_dir = Path(__file__).resolve().parent.parent / "gui"
        for path in gui_dir.glob("*.py"):
            if path.name == "theme.py":
                continue
            hits = re.findall(r"#[0-9a-fA-F]{6}\b", path.read_text(encoding="utf-8"))
            self.assertEqual(hits, [], path.name)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.win = None
        self.open(fixture_rows())

    def open(self, rows):
        from gui.app import MainWindow
        if self.win is not None:
            self.close_window()
        self.store, self.path = make_store(self._tmp.name, rows)
        self.env = make_env(self._tmp.name, self.path)
        with mock.patch("gui.app.QSettings") as settings:
            settings.return_value.value.return_value = None
            self.win = MainWindow(self.store, self.env)
        self.editor = self.win.editor

    def close_window(self):
        self.win._poll.stop()
        with mock.patch("gui.app.QSettings"):
            self.win.close()
        self.win.deleteLater()
        QTest.qWait(1)

    def tearDown(self):
        self.close_window()
        self._tmp.cleanup()

    def select(self, page, names):
        page.select_names(names)
        page._sel_timer.start()
        QTest.qWait(80)

    def rating_button(self, rating):
        return next(b for b in self.editor.rating_group.buttons() if b.property("rating") == rating)

    def choose_main(self, main, sub=""):
        combo = self.editor.main_combo
        combo.setCurrentIndex(combo.findData(main))
        combo.activated.emit(combo.currentIndex())
        sub_combo = self.editor.sub_combo
        sub_combo.setCurrentIndex(sub_combo.findData(sub))
        sub_combo.activated.emit(sub_combo.currentIndex())

    def test_edit_single_tag(self):
        self.select(self.win.list_page, ["smile"])
        self.assertEqual(self.editor.names, ["smile"])
        self.assertEqual(self.editor.updates(), {})
        self.rating_button("s").click()
        self.assertEqual(self.editor.updates(), {"rating": "s"})
        self.editor._update_preview()
        self.assertIn("후:", self.editor.message.text())
        self.editor.save(advance=False)
        on_disk = tagdb.read_overlay(self.path)["smile"]
        self.assertEqual((on_disk["rating"], on_disk["status"], on_disk["note"]), ("s", "reviewed", "LLM 근거"))
        self.assertEqual(self.editor.names, ["smile"])                      # 저장 뒤에도 같은 태그
        self.win.undo()
        self.assertEqual(tagdb.read_overlay(self.path)["smile"]["status"], "accepted")

    def test_lock_without_changes(self):
        self.select(self.win.list_page, ["smile"])
        self.editor._update_preview()
        self.assertIn("잠금", self.editor.save_btn.text())
        self.editor.save(advance=False)
        self.assertEqual(tagdb.read_overlay(self.path)["smile"]["status"], "reviewed")

    def test_change_main_clears_identity_and_note(self):
        self.select(self.win.list_page, ["long_hair"])
        self.choose_main("clothes", "headwear")
        updates = self.editor.updates()
        self.assertEqual((updates["primary"], updates["identity"]), ("clothes/headwear", ""))
        self.editor.save(advance=False)
        row = tagdb.read_overlay(self.path)["long_hair"]
        self.assertEqual((row["primary"], row["identity"]), ("clothes/headwear", ""))

    def test_multi_select_only_touched_fields(self):
        self.select(self.win.list_page, ["smile", "long_hair"])
        self.assertEqual(len(self.editor.names), 2)
        self.assertEqual(self.editor.main_combo.currentIndex(), -1)          # 메인이 달라 '(여러 값)'
        self.rating_button("s").click()
        self.assertEqual(self.editor.updates(), {"rating": "s"})
        self.editor.save(advance=False)
        rows = tagdb.read_overlay(self.path)
        self.assertEqual((rows["smile"]["rating"], rows["long_hair"]["rating"]), ("s", "s"))
        self.assertEqual((rows["smile"]["primary"], rows["long_hair"]["primary"]), ("action/expression", "body/hair"))
        self.assertEqual(rows["long_hair"]["regions"], "head")

    def test_multi_select_regions_add(self):
        self.select(self.win.list_page, ["smile", "long_hair"])
        hips, face = self.editor.region_boxes["hips"], self.editor.region_boxes["face"]
        self.assertEqual(face.checkState(), Qt.PartiallyChecked)               # 일부에만 있음
        self.assertFalse(hips.isTristate())
        hips.click()
        self.assertEqual(hips.checkState(), Qt.Checked)                        # 한 번 누르면 바로 켜짐
        self.editor.save(advance=False)
        rows = tagdb.read_overlay(self.path)
        self.assertEqual((rows["smile"]["regions"], rows["long_hair"]["regions"]), ("face|hips", "head|hips"))
        self.assertEqual((rows["smile"]["status"], rows["long_hair"]["status"]), ("reviewed", "reviewed"))

    def test_single_tag_no_spurious_changes(self):
        self.store.overlay["shirt"]["regions"] = "back|front_torso"            # 규칙 순서와 다름(실제 11행)
        self.store.overlay["1girl"]["identity"] = "0"                          # body 밖의 identity(실제 13행)
        for name in ("shirt", "1girl"):
            self.editor.set_names([name])
            self.assertEqual(self.editor.updates(), {}, name)

    def test_same_main_keeps_sub(self):
        self.select(self.win.list_page, ["smile"])
        combo = self.editor.main_combo
        combo.activated.emit(combo.currentIndex())                             # 같은 메인을 다시 고름
        self.assertEqual(self.editor.updates(), {})

    def test_queue_save_and_next(self):
        queue = self.win.queue_page
        self.win.tabs.setCurrentWidget(queue)
        self.assertEqual([e.name for e in queue.model.entries], ["maple_leaf", "ginkgo_leaf"])
        self.select(queue, ["maple_leaf"])
        self.choose_main("object", "nature")
        self.rating_button("g").click()
        self.editor.save(advance=True)
        QTest.qWait(80)
        self.assertEqual([e.name for e in queue.model.entries], ["ginkgo_leaf"])   # 분류하면 목록에서 빠짐
        self.assertEqual(self.editor.names, ["ginkgo_leaf"])                      # 다음 태그
        row = tagdb.read_overlay(self.path)["maple_leaf"]
        self.assertEqual((row["primary"], row["rating"], row["note"]), ("object/nature", "g", ""))
        self.assertIn("보류(etc) 1", self.win.tabs.tabText(1))

    def test_precedent_fills_form(self):
        self.win.tabs.setCurrentWidget(self.win.queue_page)
        self.select(self.win.queue_page, ["maple_leaf"])
        item = next(self.editor.similar_list.item(i) for i in range(self.editor.similar_list.count())
                    if self.editor.similar_list.item(i).data(Qt.UserRole) == "leaf")
        self.editor._take_precedent(item)
        self.assertEqual(self.editor.updates()["primary"], "object/nature")

    def test_conflict_keeps_input_and_reloads(self):
        self.select(self.win.list_page, ["smile"])
        self.rating_button("q").click()
        outside = tagdb.read_overlay(self.path)
        outside["blush"] = {**outside["blush"], "rating": "s"}
        tagdb.write_overlay(outside, self.path)
        with mock.patch("gui.editor.QMessageBox.warning") as warn:
            self.editor.save(advance=False)
        warn.assert_called_once()
        self.assertEqual(tagdb.read_overlay(self.path)["smile"]["rating"], "g")   # 덮어쓰지 않음
        self.assertEqual(self.store.row("blush")["rating"], "s")                    # 다시 불러옴
        self.assertEqual(self.editor.updates(), {"rating": "q"})                    # 입력은 남음
        self.editor.save(advance=False)
        self.assertEqual(tagdb.read_overlay(self.path)["smile"]["rating"], "q")

    def test_conflict_keeps_only_touched_fields(self):
        # 리뷰 3: 충돌 뒤 다시 저장해도, 건드리지 않은 칸(밖에서 바뀐 등급)을 옛 값으로 되돌리지 않는다
        self.select(self.win.list_page, ["smile"])
        self.editor.note_edit.setText("손 메모")
        self.editor.note_edit.textEdited.emit("손 메모")
        outside = tagdb.read_overlay(self.path)
        outside["smile"] = {**outside["smile"], "rating": "s"}
        tagdb.write_overlay(outside, self.path)
        with mock.patch("gui.editor.QMessageBox.warning"):
            self.editor.save(advance=False)
        self.assertEqual(self.editor.updates(), {"note": "손 메모"})
        self.assertEqual(self.rating_button("s").isChecked(), True)              # 폼도 최신 등급
        self.editor.save(advance=False)
        row = tagdb.read_overlay(self.path)["smile"]
        self.assertEqual((row["rating"], row["note"]), ("s", "손 메모"))

    def test_save_right_after_selection_change(self):
        # 리뷰 4: 다른 태그를 고른 직후(패널이 아직 이전 태그) 저장하면 이전 태그를 저장하지 않는다
        self.select(self.win.list_page, ["smile"])
        self.rating_button("s").click()
        self.win.list_page.select_names(["blush"])
        self.win.list_page._sel_timer.start()
        self.editor.save(advance=False)
        self.assertEqual(self.editor.names, ["blush"])
        rows = tagdb.read_overlay(self.path)
        self.assertEqual((rows["smile"]["rating"], rows["smile"]["status"]), ("g", "accepted"))
        self.assertEqual(rows["blush"]["status"], "reviewed")                       # (fixture 값 그대로)

    def test_multi_select_keeps_common_regions(self):
        # 리뷰 5: 모두에 있는 부위는 켜진 채로 보이고, 다른 부위를 켜도 지워지지 않는다
        self.select(self.win.list_page, ["shirt", "collarbone"])           # front_torso|back, front_torso
        boxes = self.editor.region_boxes
        self.assertEqual(boxes["front_torso"].checkState(), Qt.Checked)
        self.assertEqual(boxes["back"].checkState(), Qt.PartiallyChecked)
        boxes["hands"].click()
        self.editor.save(advance=False)
        rows = tagdb.read_overlay(self.path)
        self.assertEqual((rows["shirt"]["regions"], rows["collarbone"]["regions"]),
                         ("front_torso|back|hands", "front_torso|hands"))

    def test_multi_select_identity_needs_all_applicable(self):
        # 리뷰 6: body 와 clothes 를 함께 고르면 identity 를 켤 수 없다
        self.select(self.win.list_page, ["long_hair", "shirt"])
        self.assertFalse(self.editor.identity_box.isEnabled())
        self.assertNotIn("identity", self.editor.updates())
        self.select(self.win.list_page, ["long_hair", "aqua_eyes"])
        self.assertTrue(self.editor.identity_box.isEnabled())

    def test_queue_multi_save_does_not_skip(self):
        # 리뷰 7: 보류 목록에서 여러 개를 저장하고 다음으로 가도 다음 태그를 건너뛰지 않는다
        rows = fixture_rows()
        for name in ("branch", "park_bench"):
            rows[name] = {**rows[name], "primary": "etc", "rating": "", "regions": "", "note": "의미 불확실"}
        self.open(rows)
        queue = self.win.queue_page
        self.win.tabs.setCurrentWidget(queue)
        self.assertEqual([e.name for e in queue.model.entries], ["branch", "maple_leaf", "park_bench", "ginkgo_leaf"])
        queue.select_names(["branch", "maple_leaf"], current="maple_leaf")
        queue._sel_timer.start()
        QTest.qWait(80)
        self.choose_main("object", "nature")
        self.editor.save(advance=True)
        QTest.qWait(80)
        self.assertEqual([e.name for e in queue.model.entries], ["park_bench", "ginkgo_leaf"])
        self.assertEqual(self.editor.names, ["park_bench"])

    def test_theme_switch(self):
        from gui import theme
        app = QApplication.instance()
        try:
            self.win.set_theme("dark", save=False)
            self.assertEqual(theme.current(), "dark")
            self.assertIn(theme.tokens("dark")["surface"], app.styleSheet())
            self.assertEqual(app.palette().color(app.palette().ColorRole.Window).name(), theme.tokens("dark")["bg"])
            self.select(self.win.list_page, ["unclassified_thing"])
            self.editor._update_preview()
            self.assertIn(theme.tokens("dark")["danger"], self.editor.message.text())   # 오류 글도 테마 색
            self.win.set_theme("light", save=False)
            self.assertIn(theme.tokens("light")["danger"], self.editor.message.text())
            self.assertIn(theme.resolve("system"), ("light", "dark"))
        finally:
            app.setStyleSheet("")

    def test_rating_full_names_and_colors(self):
        from gui import theme
        from gui.models import COL_RATING
        model = self.win.list_page.model
        smile = model.index([e.name for e in model.entries].index("smile"), COL_RATING)
        self.assertEqual(model.data(smile), "general")
        self.assertEqual(model.data(smile, Qt.ForegroundRole).name(), theme.tokens()["rating_g"])
        full = ["general", "sensitive", "questionable", "explicit"]
        self.assertEqual([self.rating_button(r).text() for r in "gsqe"], full)
        combo = self.win.list_page.rating_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["전체 등급", "등급 없음"] + full)
        def dot(rating):                                                    # 콤보 항목 앞 등급 색 동그라미의 가운데 색
            image = combo.itemIcon(combo.findData(rating)).pixmap(20, 20).toImage()
            return image.pixelColor(image.width() // 2, image.height() // 2).name()
        self.assertEqual(dot("e"), theme.tokens()["rating_e"])
        model.sort(COL_RATING)                                             # 글자 순이 아니라 없음 → general → explicit
        order = [theme.RATING_ORDER.get(self.store.row(e.name).get("rating", ""), 9) for e in model.entries]
        self.assertEqual(order, sorted(order))
        self.assertGreater(len(set(order)), 2)
        app = QApplication.instance()
        try:
            self.win.set_theme("dark", save=False)                          # 테마를 바꾸면 콤보 항목 색도
            self.assertEqual(dot("e"), theme.tokens("dark")["rating_e"])
            self.assertIn(theme.tokens("dark")["rating_q"], app.styleSheet())
            smile = model.index([e.name for e in model.entries].index("smile"), COL_RATING)   # 정렬 뒤 자리
            self.assertEqual(model.data(smile, Qt.ForegroundRole).name(), theme.tokens("dark")["rating_g"])
        finally:
            self.win.set_theme("light", save=False)
            app.setStyleSheet("")

    def test_dirty_label_marked(self):
        self.select(self.win.list_page, ["smile"])
        label = self.editor.field_labels["rating"]
        self.assertFalse(label.property("dirty"))
        self.rating_button("s").click()
        self.assertTrue(label.property("dirty"))
        self.editor.set_names(["smile"])
        self.assertFalse(label.property("dirty"))

    def test_check_page(self):
        self.store.apply(["smile"], {"rating": "s"})
        self.win.check_page.run()
        tree = self.win.check_page.tree
        groups = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        self.assertTrue(any(g.startswith("마지막 커밋 대비 바뀐 행 1개") for g in groups))
        self.assertIn("이상 없음", self.win.check_page.summary.text())


NB = "\u00a0"        # 증거 칸은 '이름 0.80' 사이와 가운뎃점 앞에서 줄을 바꾸지 않는다

CACHED = {
    "maple_leaf": {"wiki": "<b>단풍</b> [[leaf|잎]] <img src=x>", "other_names": ["もみじ", "단풍잎"],
                   "implies": ["leaf"], "fetched": "2026-10-27",
                   "related": [["official_alternate_costume", 0, 0.72], ["black_skirt", 0, 0.55], ["1girl", 0, 0.9],
                               ["tree", 0, 0.6], ["shirt", 0, 0.4], ["day", 0, 0.2], ["vocaloid", 3, 0.9],
                               ["hatsune_miku", 4, 0.5]]},
    "ginkgo_leaf": {"wiki": None, "other_names": [], "fetched": "2026-10-27"},      # 위키만 받음
}


def fake_danbooru(calls, fail=None, gate=None):
    """Danbooru 공개 API 흉내(네트워크 없음). calls 에 (경로, 조회한 태그)를 쌓는다.
    fail: 이 예외를 던진다. gate=(태그, Event): 그 태그를 조회하면 Event 가 설 때까지 기다린다."""
    def get(path, params, not_found_ok=False):
        names = (params.get("search[title_comma]") or params.get("search[antecedent_name_comma]")
                 or params.get("query") or "")
        calls.append((path, names))
        if gate is not None and gate[0] in names.split(","):
            gate[1].wait(5)
        if fail is not None:
            raise fail
        if path == "/wiki_pages.json":
            return [{"title": n, "body": f"{n} 위키", "other_names": ["別名"], "is_deleted": False}
                    for n in names.split(",")]
        if path == "/tag_implications.json":
            return [{"antecedent_name": n, "consequent_name": "leaf"} for n in names.split(",")]
        return {"related_tags": [{"tag": {"name": "official_alternate_costume", "category": 0}, "frequency": 0.81},
                                 {"tag": {"name": names, "category": 0}, "frequency": 1.0}]}
    return get


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class EvidenceTests(unittest.TestCase):
    """편집 패널의 Danbooru 증거 칸 — 캐시 읽기, 받기(작업 스레드, 가짜 getter), 실패 표시."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.app import MainWindow
        self._tmp = tempfile.TemporaryDirectory()
        self.store, self.path = make_store(self._tmp.name)
        self.cache = Path(self._tmp.name, "evidence", "evidence.json")
        self.calls = []
        self.env = make_env(self._tmp.name, self.path, evidence_cache=self.cache)
        with mock.patch("gui.app.QSettings") as settings:
            settings.return_value.value.return_value = None
            self.win = MainWindow(self.store, self.env)
        self.editor = self.win.editor
        self.ev = self.editor.evidence

    def tearDown(self):
        self.wait()
        self.win._poll.stop()
        with mock.patch("gui.app.QSettings"):
            self.win.close()
        self.win.deleteLater()
        QTest.qWait(1)
        self._tmp.cleanup()

    def wait(self, timeout=10.0):
        from gui import jobs
        end = time.time() + timeout
        while jobs.active and time.time() < end:
            QTest.qWait(20)
        self.assertEqual(jobs.active, 0, "백그라운드 작업이 끝나지 않음")

    def select(self, page, names):
        self.win.tabs.setCurrentWidget(page)
        page.select_names(names)
        page._sel_timer.start()
        QTest.qWait(80)

    def write_cache(self, cache):
        evidence.write_cache(cache, self.cache)

    def shown(self, widget):
        return widget.isVisibleTo(self.ev)

    def test_shows_cached_evidence_in_both_tabs(self):
        from gui import theme
        self.write_cache(CACHED)
        self.assertIs(self.editor.refs.currentWidget(), self.ev)            # 참고 탭은 증거가 먼저
        self.select(self.win.queue_page, ["maple_leaf"])
        self.assertTrue(self.shown(self.ev.card))
        self.assertFalse(self.shown(self.ev.fetch_btn))                    # 다 받은 태그는 버튼 없이
        wiki = self.ev.fields["wiki"]
        self.assertEqual(wiki.textFormat(), Qt.PlainText)                  # 외부 글은 HTML 로 해석하지 않는다
        self.assertEqual(wiki.text(), "<b>단풍</b> 잎 <img src=x>")
        self.assertEqual(self.ev.fields["other_names"].text(), f"もみじ{NB}· 단풍잎")
        self.assertEqual(self.ev.fields["implies"].text(), "leaf")
        self.assertEqual(self.ev.fields["related_series"].text(), f"vocaloid{NB}0.90{NB}· hatsune_miku{NB}0.50")
        related = self.ev.fields["related"].text()
        accent, muted = theme.tokens()["accent"], theme.tokens()["muted"]
        self.assertIn(f"<span style='color:{accent};font-weight:600;'>official_alternate_costume{NB}0.72</span>",
                      related)                                             # 0.7 이상 의상 근거는 굵게
        self.assertIn(f"<span style='color:{accent};'>black_skirt{NB}0.55</span>", related)    # clothes 0.5 이상
        self.assertIn(f"{NB}· tree{NB}0.60{NB}· shirt{NB}0.40{NB}· ", related)  # 의상이 아니거나 0.5 미만은 그대로
        self.assertIn(f"<span style='color:{muted};'>day{NB}0.20</span>", related)             # 0.3 미만은 흐리게
        self.assertNotIn("1girl", related)                                 # 공통 태그는 summarize 가 뺀다

        self.select(self.win.queue_page, ["ginkgo_leaf"])                  # 일부만 받음
        self.assertTrue(self.shown(self.ev.fetch_btn))
        self.assertEqual(self.ev.fetch_btn.text(), "나머지 받기")
        self.assertIn("함의·연관 태그 안 받음", self.ev.status.text())
        self.assertEqual(self.ev.fields["wiki"].text(), "위키 없음")
        self.assertEqual(self.ev.fields["wiki"].property("role"), "muted")
        self.assertIn("받지 않음", self.ev.fields["related"].text())

        self.select(self.win.list_page, ["smile"])                         # 태그 목록 탭에서도 같은 칸
        self.assertIs(self.editor.evidence, self.ev)
        self.assertFalse(self.shown(self.ev.card))
        self.assertTrue(self.shown(self.ev.fetch_btn))
        self.assertEqual(self.ev.fetch_btn.text(), "증거 받기")
        self.assertIn("캐시에 없음", self.ev.status.text())

        self.write_cache({**CACHED, "smile": {**CACHED["maple_leaf"], "wiki": "웃는 얼굴"}})    # CLI 가 받음
        self.select(self.win.list_page, ["smile", "blush"])                # 여러 개는 안내만
        self.assertFalse(self.shown(self.ev.card))
        self.assertIn("태그 하나를 고르면", self.ev.status.text())
        self.select(self.win.list_page, ["smile"])
        self.assertEqual(self.ev.fields["wiki"].text(), "웃는 얼굴")         # 바뀐 캐시 파일을 다시 읽음

    def test_fetch_updates_panel_and_cache(self):
        self.ev.get = fake_danbooru(self.calls)
        self.select(self.win.queue_page, ["ginkgo_leaf"])
        self.assertFalse(self.cache.exists())
        self.ev.fetch_btn.click()
        self.assertFalse(self.ev.fetch_btn.isEnabled())                    # 받는 동안 다시 누르지 못함
        self.assertEqual(self.ev.fetch_btn.text(), "받는 중…")
        self.wait()
        self.assertEqual([p for p, _ in self.calls], ["/wiki_pages.json", "/tag_implications.json",
                                                      "/related_tag.json"])
        self.assertTrue(self.shown(self.ev.card))                          # 바로 다시 그림
        self.assertFalse(self.shown(self.ev.fetch_btn))
        self.assertEqual(self.ev.fields["wiki"].text(), "ginkgo_leaf 위키")
        self.assertIn(f"official_alternate_costume{NB}0.81", self.ev.fields["related"].text())
        on_disk = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(set(on_disk["ginkgo_leaf"]), {"wiki", "other_names", "implies", "related", "fetched"})
        self.select(self.win.queue_page, ["maple_leaf"])
        self.select(self.win.queue_page, ["ginkgo_leaf"])
        self.assertEqual(len(self.calls), 3)                               # 다시 고르면 캐시에서

    def test_fetch_failure_is_shown_and_retryable(self):
        from gui import theme
        self.write_cache(CACHED)
        self.select(self.win.list_page, ["smile"])
        self.ev.fetch_btn.click()                                          # make_env 의 getter 는 예외를 던진다
        self.wait()
        self.assertIn(theme.tokens()["danger"], self.ev.status.text())
        self.assertIn("받지 못했습니다: 테스트가 Danbooru 를 부름", self.ev.status.text())
        self.ev.get = fake_danbooru(self.calls, fail=evidence.FetchError("/wiki_pages.json: 503"))
        self.ev.fetch_btn.click()
        self.wait()
        self.assertIn("일부를 받지 못했습니다", self.ev.status.text())     # fetch 가 기록한 실패
        self.assertIn("503", self.ev.status.text())
        self.assertTrue(self.ev.fetch_btn.isEnabled())
        self.assertEqual(self.ev.fetch_btn.text(), "증거 받기")
        self.assertNotIn("smile", evidence.read_cache(self.cache))         # 실패한 칸은 비워 둔다
        self.assertIn("maple_leaf", evidence.read_cache(self.cache))       # 있던 캐시는 그대로
        self.ev.get = fake_danbooru(self.calls)
        self.ev.fetch_btn.click()
        self.wait()
        self.assertEqual(self.ev.fields["wiki"].text(), "smile 위키")
        self.assertNotIn(theme.tokens()["danger"], self.ev.status.text())

    def test_overlapping_fetches_keep_both(self):
        # 앞 태그를 받는 동안 다른 태그를 받아도, 캐시 파일을 한 번에 하나씩 고쳐 앞의 것을 잃지 않는다
        gate = ("maple_leaf", threading.Event())
        self.ev.get = fake_danbooru(self.calls, gate=gate)
        self.select(self.win.queue_page, ["maple_leaf"])
        self.ev.fetch_btn.click()
        self.select(self.win.queue_page, ["ginkgo_leaf"])
        self.assertTrue(self.ev.fetch_btn.isEnabled())                     # 받는 중 표시는 그 태그에만
        self.ev.fetch_btn.click()
        self.select(self.win.queue_page, ["maple_leaf"])
        self.assertEqual(self.ev.fetch_btn.text(), "받는 중…")
        gate[1].set()
        self.wait()
        self.assertEqual(set(evidence.read_cache(self.cache)), {"maple_leaf", "ginkgo_leaf"})
        self.assertEqual(self.ev.fields["wiki"].text(), "maple_leaf 위키")


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class LoadStoreTests(unittest.TestCase):
    """처음 받은 PC: 원본 CSV 가 없으면 받을지 묻고, 받은 뒤 다시 불러온다."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def load(self, missing, answer):
        from gui import app
        loaded = object()
        with mock.patch.object(app, "Store", side_effect=[missing, loaded]), \
                mock.patch.object(app.QMessageBox, "question", return_value=answer) as ask, \
                mock.patch.object(app.tagdb, "fetch_source") as fetch:
            try:
                return app.load_store() is loaded, ask, fetch
            finally:
                app.QApplication.restoreOverrideCursor()

    def missing(self, **entry):
        return tagdb.SourceMissing(Path("src.csv"), {"file": "src.csv", **entry})

    def test_fetch_then_reload(self):
        from gui.app import QMessageBox
        missing = self.missing(url="https://github.com/o/r/blob/main/src.csv", sha256="ab")
        loaded, ask, fetch = self.load(missing, QMessageBox.Yes)
        self.assertTrue(loaded)
        fetch.assert_called_once_with(missing.entry, missing.path)
        self.assertIn("https://github.com/o/r/raw/main/src.csv", ask.call_args[0][2])

    def test_declined_or_unfetchable_reports_missing(self):
        from gui.app import QMessageBox
        with self.assertRaises(tagdb.SourceMissing):
            self.load(self.missing(url="https://x", sha256="ab"), QMessageBox.No)
        with self.assertRaises(tagdb.SourceMissing):
            self.load(self.missing(url="https://x"), QMessageBox.Yes)       # sha256 이 없으면 묻지도 않는다


if __name__ == "__main__":
    unittest.main()
