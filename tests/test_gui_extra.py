"""관리 툴의 확장 DB(data/extra) — 목록·찾기·고치기·되돌리기·새 태그·충돌(데이터 계층은 Qt 없이, 화면은 offscreen)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import tagdb
from gui import ops
from gui.store import EXTRA_ALL, ORIGINAL, ConflictError, Query
from test_gui import fixture_rows, make_env, make_store


def row(db, name, **fields):
    return {**{k: "" for k in tagdb.EXTRA_FIELDS}, "db": db, "name": name, **fields}


EXTRA_ROWS = [
    row("model", "masterpiece", primary="meta/quality", status="reviewed", models="nai"),
    row("model", "wink", same_as="one_eye_closed", status="accepted", models="nai"),   # 원본 별칭 wink 를 가림
    row("deprecated", "aqua legwear", primary="clothes/legwear", rating="g", regions="thighs|lower_legs",
        status="accepted", note="LLM 근거", use_instead="aqua_thighhighs"),
    row("deprecated", "claire", primary="etc", status="accepted", note="사람 이름"),
    row("user", "cinematic lighting", primary="effect/lighting", status="accepted"),
]


class StoreExtraTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store, self.path = make_store(self._tmp.name, extra_rows=EXTRA_ROWS)
        self.extra_dir = Path(self._tmp.name, "extra")

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, **query):
        return [e.name for e in self.store.find(Query(**query))]

    def disk(self, db):
        return {r["name"]: r for r in tagdb.read_extra(self.extra_dir) if r["db"] == db}

    def test_entries_and_filters(self):
        self.assertEqual(self.names(type=EXTRA_ALL),
                         ["masterpiece", "wink", "aqua legwear", "claire", "cinematic lighting"])
        self.assertEqual(self.names(type="extra:deprecated"), ["aqua legwear", "claire"])
        self.assertNotIn("aqua legwear", self.names(type=0))                  # 기본(일반)은 원본만
        self.assertIn("claire", self.names(type=None, primary="etc"))
        self.assertNotIn("claire", self.names(type=ORIGINAL, primary="etc"))  # 보류 작업 목록은 원본만
        self.assertEqual(self.store.by_name["aqua legwear"].db, "deprecated")

    def test_search_and_resolve(self):
        self.assertEqual(self.names(type=None, text="aqua_legwear"), ["aqua legwear"])
        self.assertEqual(self.names(type=None, text="Cinematic Lighting")[0], "cinematic lighting")
        self.assertEqual(self.store.resolve("Wink"), "wink")                  # 확장 DB 이름이 원본 별칭보다 먼저
        self.assertEqual(self.store.resolve("one eye closed"), "one_eye_closed")
        self.assertEqual(self.names(type=0, text="wink")[0], "wink")          # 정확히 맞으면 종류 필터와 상관없이

    def test_primary_of(self):
        self.assertEqual(self.store.primary_of(self.store.by_name["wink"]), ("action/expression", "accepted"))
        self.assertEqual(self.store.primary_of(self.store.by_name["aqua legwear"]), ("clothes/legwear", "accepted"))

    def test_apply_writes_that_db_only(self):
        overlay_before = self.path.read_bytes()
        user_before = tagdb.extra_path("user", self.extra_dir).read_bytes()
        res = self.store.apply(["aqua legwear"], {"rating": "s"})
        self.assertEqual(res.errors, [])
        on_disk = self.disk("deprecated")["aqua legwear"]
        self.assertEqual((on_disk["rating"], on_disk["status"], on_disk["note"], on_disk["use_instead"]),
                         ("s", "reviewed", "LLM 근거", "aqua_thighhighs"))
        self.assertEqual([r["name"] for r in tagdb.read_extra(self.extra_dir) if r["db"] == "deprecated"],
                         ["aqua legwear", "claire"])                           # 파일 순서 그대로
        self.assertEqual(self.path.read_bytes(), overlay_before)
        self.assertEqual(tagdb.extra_path("user", self.extra_dir).read_bytes(), user_before)
        self.assertFalse(self.store.external_change())
        self.assertEqual(self.store.undo(), ["aqua legwear"])
        self.assertEqual(self.disk("deprecated")["aqua legwear"]["status"], "accepted")
        self.assertEqual(self.store.row("aqua legwear")["rating"], "g")

    def test_one_table_per_save(self):
        res = self.store.preview(["smile", "aqua legwear"], {"rating": "s"})
        self.assertIn("따로 고칩니다", res.errors[0])
        res = self.store.preview(["aqua legwear", "cinematic lighting"], {"note": "x"})
        self.assertIn("하나씩", res.errors[0])

    def test_same_as_only_note(self):
        res = self.store.preview(["wink"], {"primary": "action/gesture"})
        self.assertIn("same_as", res.errors[0])
        res = self.store.apply(["wink"], {"note": "NAI 표기"})
        self.assertEqual(res.errors, [])
        self.assertEqual(self.disk("model")["wink"]["note"], "NAI 표기")

    def test_add_extra_and_undo(self):
        added, skipped = self.store.add_extra("user", ["Rim_Lighting", "smile", "cinematic lighting", "longhair"])
        self.assertEqual(added, ["rim lighting"])
        self.assertEqual(len(skipped), 3)
        self.assertEqual(list(self.disk("user")), ["cinematic lighting", "rim lighting"])
        self.assertEqual(self.names(type="extra:user", text="rim"), ["rim lighting"])
        self.assertEqual(self.store.row("rim lighting")["primary"], "")       # 분류 전
        res = self.store.apply(["rim lighting"], {"primary": "effect/lighting"})
        self.assertEqual(res.errors, [])
        self.store.undo()                                                     # 분류 되돌림
        self.assertEqual(self.store.row("rim lighting")["primary"], "")
        self.assertEqual(self.store.undo(), ["rim lighting"])                 # 더하기 되돌림
        self.assertEqual(list(self.disk("user")), ["cinematic lighting"])
        self.assertNotIn("rim lighting", self.store.by_name)
        self.assertEqual(self.names(type="extra:user"), ["cinematic lighting"])

    def test_add_to_new_db(self):
        added, _ = self.store.add_extra("mine", ["x tag"])
        self.assertEqual(added, ["x tag"])
        self.assertEqual(list(self.disk("mine")), ["x tag"])
        self.assertFalse(self.store.external_change())
        with self.assertRaises(ValueError):
            self.store.add_extra("My DB", ["y tag"])

    def test_external_change_is_not_overwritten(self):
        path = tagdb.extra_path("deprecated", self.extra_dir)
        rows = list(self.disk("deprecated").values())
        rows[1] = dict(rows[1], note="밖에서 고침")
        tagdb.write_extra("deprecated", rows, self.extra_dir)
        self.assertTrue(self.store.external_change())
        with self.assertRaises(ConflictError):
            self.store.apply(["aqua legwear"], {"rating": "s"})
        self.assertEqual(self.store.row("aqua legwear")["rating"], "g")        # 메모리도 그대로
        self.assertEqual(self.disk("deprecated")["claire"]["note"], "밖에서 고침")
        with self.assertRaises(ConflictError):
            self.store.add_extra("deprecated", ["new one"])
        path.unlink()                                                          # 파일이 없어져도 바뀐 것
        self.store.reload()
        self.assertNotIn("aqua legwear", self.store.by_name)

    def test_new_file_outside_is_change(self):
        tagdb.write_extra("other", [row("other", "z tag")], self.extra_dir)
        self.assertTrue(self.store.external_change())

    def test_check_and_memory_inputs(self):
        report = self.store.check()
        self.assertEqual(report.errors, [])
        self.assertIn("확장 DB 5행", report.info)
        inputs = ops.memory_inputs(self.store)
        self.assertEqual([r["name"] for r in inputs.extra], [r["name"] for r in EXTRA_ROWS])


class StoreExtraEdgeTests(unittest.TestCase):
    """교차 리뷰에서 나온 경계: 같은 이름 두 행, overlay 에만 남은 이름, 순환 same_as, 확장 DB 별칭."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def open(self, extra_rows, overlay=None):
        store, path = make_store(self._tmp.name, overlay, extra_rows=extra_rows)
        return store, Path(self._tmp.name, "extra")

    def test_duplicate_name_in_one_db(self):
        store, folder = self.open([row("user", "dup", primary="meta/quality", note="첫째", status="accepted"),
                                   row("user", "dup", primary="meta/genre", note="둘째", status="accepted")])
        self.assertEqual(store.row("dup")["note"], "첫째")                        # 앞 행만 보임
        store.apply(["dup"], {"note": "고침"})
        store.apply(["dup"], {"note": "또 고침"})
        self.assertEqual([r["note"] for r in tagdb.read_extra(folder)], ["또 고침", "둘째"])
        store.undo()
        store.undo()
        self.assertEqual([r["note"] for r in tagdb.read_extra(folder)], ["첫째", "둘째"])
        self.assertEqual(store.row("dup")["note"], "첫째")

    def test_add_name_of_overlay_only_row(self):
        rows = fixture_rows()
        rows["retired"] = dict(rows["smile"], name="retired", missing_since="2026.09")
        store, folder = self.open(EXTRA_ROWS, rows)
        added, skipped = store.add_extra("user", ["retired", "fresh tag"])
        self.assertEqual(added, ["fresh tag"])
        self.assertTrue(any("retired" in s for s in skipped))
        self.assertEqual(store.row("retired")["missing_since"], "2026.09")

    def test_cyclic_same_as_does_not_recurse(self):
        store, _ = self.open([row("user", "a tag", same_as="b tag"), row("user", "b tag", same_as="a tag")])
        self.assertEqual([e.name for e in store.find(Query(type=EXTRA_ALL))], ["a tag", "b tag"])
        self.assertEqual(store.primary_of(store.by_name["a tag"])[0], "")
        self.assertTrue(any("same_as" in e for e in store.check().errors))

    def test_extra_alias_resolves_after_source_alias(self):
        store, _ = self.open([row("user", "cinematic lighting", primary="effect/lighting", aliases="dramatic lighting|wink")])
        self.assertEqual(store.resolve("Dramatic_Lighting"), "cinematic lighting")
        self.assertEqual(store.resolve("wink"), "one_eye_closed")                # 원본 별칭이 먼저
        self.assertEqual([e.name for e in store.find(Query(type=0, text="dramatic lighting"))][:1],
                         ["cinematic lighting"])


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class WindowExtraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.app import MainWindow
        self._tmp = tempfile.TemporaryDirectory()
        self.store, self.path = make_store(self._tmp.name, fixture_rows(), extra_rows=EXTRA_ROWS)
        self.extra_dir = Path(self._tmp.name, "extra")
        self.env = make_env(self._tmp.name, self.path)
        with mock.patch("gui.app.QSettings") as settings:
            settings.return_value.value.return_value = None
            self.win = MainWindow(self.store, self.env)
        self.editor = self.win.editor
        self.page = self.win.list_page

    def tearDown(self):
        self.win._poll.stop()
        with mock.patch("gui.app.QSettings"):
            self.win.close()
        self.win.deleteLater()
        QTest.qWait(1)
        self._tmp.cleanup()

    def show_extra(self):
        combo = self.page.type_combo
        combo.setCurrentIndex(combo.findData(EXTRA_ALL))
        QTest.qWait(10)

    def select(self, names):
        self.page.select_names(names)
        self.page._sel_timer.start()
        QTest.qWait(80)

    def test_type_filter_lists_extra(self):
        self.assertGreaterEqual(self.page.type_combo.findData("extra:user"), 0)
        self.show_extra()
        self.assertEqual(self.page.model.rowCount(), 5)
        row = self.page.model.row_of("aqua legwear")
        self.assertEqual(self.page.model.data(self.page.model.index(row, 1)), "폐기")
        wink = self.page.model.row_of("wink")
        self.assertIn("= one_eye_closed", self.page.model.data(self.page.model.index(wink, 3)))

    def test_edit_extra_row(self):
        self.show_extra()
        self.select(["aqua legwear"])
        self.assertEqual(self.editor.names, ["aqua legwear"])
        self.assertIn("폐기 태그", self.editor.info.text())
        button = next(b for b in self.editor.rating_group.buttons() if b.property("rating") == "s")
        button.click()
        overlay_before = self.path.read_bytes()
        self.editor.save(advance=False)
        on_disk = {r["name"]: r for r in tagdb.read_extra(self.extra_dir)}["aqua legwear"]
        self.assertEqual((on_disk["rating"], on_disk["status"]), ("s", "reviewed"))
        self.assertEqual(self.path.read_bytes(), overlay_before)
        self.assertEqual(self.editor.evidence.name, "aqua_legwear")            # 증거는 Danbooru 표기로

    def test_same_as_locks_classification(self):
        self.show_extra()
        self.select(["wink"])
        self.assertFalse(self.editor.main_combo.isEnabled())
        self.assertTrue(self.editor.note_edit.isEnabled())
        self.editor.save(advance=False)                                        # 바꾼 것 없이 확인(잠금)
        on_disk = {r["name"]: r for r in tagdb.read_extra(self.extra_dir)}["wink"]
        self.assertEqual((on_disk["status"], on_disk["same_as"], on_disk["primary"]), ("reviewed", "one_eye_closed", ""))

    def test_add_dialog(self):
        from gui.pages import AddExtraDialog
        with mock.patch.object(AddExtraDialog, "exec", return_value=QDialog.Accepted), \
                mock.patch.object(AddExtraDialog, "values", return_value=("user", ["Rim_Lighting", "smile"])), \
                mock.patch("gui.app.QMessageBox") as box:
            self.win.add_extra()
            QTest.qWait(80)
        self.assertTrue(box.information.called)                                # smile 은 원본이라 건너뜀
        self.assertEqual(self.page.type_combo.currentData(), "extra:user")
        self.assertEqual(self.editor.names, ["rim lighting"])
        self.assertIn("rim lighting", {r["name"] for r in tagdb.read_extra(self.extra_dir)})
        self.win.undo()
        self.assertNotIn("rim lighting", {r["name"] for r in tagdb.read_extra(self.extra_dir)})


if __name__ == "__main__":
    unittest.main()
