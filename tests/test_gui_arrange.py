"""배열 세팅 화면 테스트 — 모델·파일은 Qt 없이, 청크 화면·패널·미리보기 연동은 PySide6 가 있으면 offscreen 으로."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fixtures import core, pack, tagdb
from gui import arrangement
from gui.arrangement import Model
from test_gui import make_env, make_store


def sample() -> Model:
    return Model(core.arrangement_from_layout(pack(), "default"))


class ModelTests(unittest.TestCase):
    def test_gap_toggle_split_join_and_round_trip(self):
        m = sample()
        m.join_previous("character")
        self.assertFalse(m.toggle_gap_before("count"))
        self.assertFalse(m.has_gap_before("count"))
        self.assertTrue(m.toggle_gap_before("character"))
        self.assertEqual(m.rows[:2], [["count"], ["character"]])
        self.assertTrue(m.has_gap_before("character"))
        self.assertEqual(Model(m.to_json()).to_json(), m.to_json())
        self.assertTrue(m.toggle_gap_after("count"))
        self.assertNotIn("gap_after", m.chunks["count"])
        self.assertEqual(m.rows[:2], [["count"], ["character"]])
        m.toggle_gap_after("count")
        self.assertTrue(m.join_previous("character"))
        self.assertNotIn("gap_after", m.chunks["count"])
        self.assertEqual(m.rows[0], ["count", "character"])

    def test_gap_moves_with_chunk_and_disappears_when_deleted(self):
        m = sample()
        m.toggle_gap_after("count")
        m.move_chunk("style", 0, 1)
        self.assertTrue(m.has_gap_before("style"))
        self.assertFalse(m.has_gap_before("character"))
        m.merge_into("count", "style")
        self.assertFalse(m.has_gap_before("character"))
        cid = m.add_custom("빈 묶음")
        m.toggle_gap_after(cid)
        self.assertTrue(m.chunks[cid]["gap_after"])
        m.delete_chunk(cid)
        self.assertNotIn(cid, m.chunks)

    def test_rows_move_break_join(self):
        m = sample()
        first = [r[0] for r in m.rows[:4]]
        self.assertEqual(first, ["count", "character", "style", "composition"])
        self.assertTrue(m.join_previous("character"))                       # 2번 줄을 1번 줄 끝에
        self.assertEqual(m.rows[0], ["count", "character"])
        self.assertTrue(m.move_chunk("style", 0, 1))                          # 1번 줄 가운데로
        self.assertEqual(m.rows[0], ["count", "style", "character"])
        self.assertFalse(m.move_chunk("style", 0, 2))                         # 제자리
        self.assertTrue(m.break_before("character"))
        self.assertEqual(m.rows[:2], [["count", "style"], ["character"]])
        self.assertTrue(m.break_after("count"))
        self.assertEqual(m.rows[:3], [["count"], ["style"], ["character"]])
        self.assertTrue(m.move_chunk_new_row("count", 3))                     # 빈 줄은 남지 않는다
        self.assertEqual(m.rows[:3], [["style"], ["character"], ["count"]])

    def test_shift_crosses_rows_like_text(self):
        m = sample()
        m.join_previous("character")
        self.assertTrue(m.shift("character", -1))
        self.assertEqual(m.rows[0], ["character", "count"])
        self.assertFalse(m.shift("character", -1))                            # 첫 줄 맨 앞
        self.assertTrue(m.shift("count", 1))                                  # 줄 끝 → 다음 줄 앞
        self.assertEqual(m.rows[:2], [["character"], ["count", "style"]])
        self.assertTrue(m.shift_row("style", -1))
        self.assertEqual(m.rows[:2], [["character", "style"], ["count"]])
        self.assertFalse(Model({"rows": [["a"]], "chunks": {"a": {"members": ["body"]}}}).shift_row("a", -1))

    def test_members_move_detach_merge(self):
        m = sample()
        self.assertEqual(m.members("character"), ["subject/species", "subject/character", "subject", "series"])
        m.sub_order = False
        self.assertTrue(m.move_member("character", 3, "character", 0))        # 같은 청크 안 → 서브 순서 켬
        self.assertEqual(m.members("character")[0], "series")
        self.assertTrue(m.sub_order)
        self.assertTrue(m.move_member("hair", 1, "clothes", 0))               # 머리 장식을 의상으로
        self.assertEqual(m.members("clothes")[0], "clothes/hair_acc")
        self.assertEqual(m.members("hair"), ["body/hair"])
        new = m.detach_member("character", 0, 0, 1)                           # series 를 1번 줄 count 뒤로
        self.assertEqual(new, "series")
        self.assertEqual(m.rows[0], ["count", "series"])
        self.assertTrue(m.merge_into("series", "count"))
        self.assertNotIn("series", m.chunks)
        self.assertEqual(m.members("count"), ["subject/count", "series"])
        r, p = m.where("eyes")
        new = m.detach_after("eyes", 0)                                       # 마지막 멤버를 빼면 원래 청크는 사라짐
        self.assertNotIn("eyes", m.chunks)
        self.assertEqual(m.where(new), (r, p))
        core.load_arrangement(m.to_json(), pack())                            # 언제나 코어가 받는 형식

    def test_custom_chunks(self):
        m = sample()
        cid = m.add_custom("내 태그", "#112233", ["my phrase", "x"], ["* armor"])
        self.assertEqual(m.rows[0], [cid])
        self.assertTrue(m.is_custom(cid))
        self.assertTrue(m.add_tags(cid, ["My_Phrase", "new one"]))           # 정규화해서 같은 것은 건너뜀
        self.assertEqual(m.chunks[cid]["tags"], ["my phrase", "x", "new one"])
        self.assertFalse(m.add_tags(cid, ["x"]))
        self.assertTrue(m.update_custom(cid, "", "", [], []))
        self.assertIn(cid, m.chunks)                                          # 빈 커스텀 청크도 남는다
        m.move_member("count", 0, cid, 0)
        self.assertTrue(m.delete_chunk(cid))                                  # 멤버가 있으면 일반 청크로
        self.assertFalse(m.is_custom(cid))
        self.assertFalse(m.delete_chunk("hair"))                              # 일반 청크는 못 지움
        r = core.organize("long hair, 1girl, my phrase", pack(), {"arrangement": m.to_json()})
        self.assertTrue(r.text.startswith("1girl"))

    def test_load_tidies_external_files(self):
        m = Model({"rows": [["a", "ghost", "a"], []], "chunks": {"a": {"members": "body/hair, body/eyes"},
                                                                 "b": {"members": ["clothes"]},
                                                                 "empty": {"members": []}}})
        self.assertEqual(m.rows, [["a"], ["b"]])                              # 없는 id·두 번·빈 줄을 치우고 미배치는 끝에
        self.assertEqual(m.members("a"), ["body/hair", "body/eyes"])
        self.assertNotIn("empty", m.chunks)
        self.assertEqual(m.add_slots(core.uncovered_slots(m.to_json(), pack().taxonomy)), "more")
        self.assertEqual(core.uncovered_slots(m.to_json(), pack().taxonomy), [])

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "arr")
            m = sample()
            m.label = "내 배열"
            path = folder / f"{arrangement.slug(m.label)}.json"
            arrangement.write_file(path, m.to_json())
            self.assertEqual(path.name, "내_배열.json")
            self.assertEqual(Model(arrangement.read_file(path)).dumps(), m.dumps())
            Path(folder, "junk.json").write_text("{}", encoding="utf-8")
            self.assertEqual([label for label, _ in arrangement.list_files(folder)], ["내 배열"])
            with self.assertRaises(ValueError):
                arrangement.read_file(Path(folder, "junk.json"))
            self.assertEqual(list(folder.glob(".tmp-*")), [])

    def test_keeps_description_and_other_tools_keys(self):
        data = sample().to_json() | {"description": "인물 중심", "author": "bmk", "x_node": {"w": 1}}
        out = Model(data).to_json()
        self.assertEqual((out["description"], out["author"], out["x_node"]), ("인물 중심", "bmk", {"w": 1}))
        self.assertEqual(list(out)[:4], ["format", "version", "label", "description"])
        self.assertNotIn("description", sample().to_json())                    # 비었으면 쓰지 않음

    def test_tolerate_drops_only_unknown_categories(self):
        data = sample().to_json()
        data["chunks"]["hair"]["members"] = ["body/hair", "body/old_sub", "gone"]
        data["chunks"]["mine"] = {"custom": True, "tags": ["my phrase"], "members": "body/old_sub"}
        data["rows"].append(["mine"])
        data["guess"] = [{"pattern": "holding *", "primary": "action/holding"},
                         {"pattern": "* gem", "primary": "object/old_sub"}]
        with self.assertRaises(ValueError):
            core.load_arrangement(data, pack())                                 # 코어는 그대로 엄격
        fixed, notes = arrangement.tolerate(data, pack())
        core.load_arrangement(fixed, pack())
        self.assertEqual(fixed["chunks"]["hair"]["members"], ["body/hair"])
        self.assertEqual(fixed["chunks"]["mine"]["members"], [])
        self.assertEqual(fixed["guess"], [{"pattern": "holding *", "primary": "action/holding"}])
        self.assertEqual(len(notes), 3)
        self.assertIn("body/old_sub, gone", notes[0])
        self.assertEqual(data["chunks"]["hair"]["members"], ["body/hair", "body/old_sub", "gone"])   # 원본은 그대로
        bad = sample().to_json()
        bad["chunks"]["hair"]["patterns"] = ["re:("]
        with self.assertRaises(ValueError):                                     # 분류 말고 다른 잘못은 거부
            core.load_arrangement(arrangement.tolerate(bad, pack())[0], pack())

    def test_parse_text_from_clipboard(self):
        text = "\ufeff" + json.dumps(sample().to_json(), separators=(",", ":"))
        self.assertEqual(Model(arrangement.parse_text(text)).dumps(), sample().dumps())
        for junk in ("", "not json", '{"format": "other"}', "[1, 2]"):
            with self.assertRaises(ValueError):
                arrangement.parse_text(junk)

    def test_share_id(self):
        self.assertEqual(arrangement.share_id("Portrait Basic!"), "portrait-basic")
        self.assertEqual(arrangement.share_id("인물 중심"), "arrangement")
        self.assertEqual(arrangement.share_id("인물 중심", "examples/portrait.json"), "portrait")      # 지금 파일 이름이 id 면
        self.assertEqual(arrangement.share_id("Upper v2", "examples/내_배열.json"), "upper-v2")
        self.assertEqual(arrangement.share_id("Upper", "layout:default"), "upper")

    def test_check_shared_files(self):
        config = tagdb.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "arrangements")
            self.assertEqual(arrangement.check_files(folder, config), [])         # 폴더가 없어도 됨
            arrangement.write_file(folder / "portrait-basic.json", sample().to_json())
            self.assertEqual(arrangement.check_files(folder, config), [])
            stale = sample().to_json()
            stale["chunks"]["hair"]["members"] = ["body/old_sub"]
            arrangement.write_file(folder / "Old_One.json", stale)
            problems = arrangement.check_files(folder, config)
            self.assertEqual(len(problems), 2)
            self.assertIn("Old_One.json: 파일 이름은", problems[0])
            self.assertIn("body/old_sub", problems[1])

    def test_repository_shared_presets_open(self):
        """저장소의 공유 프리셋이 지금 분류 체계로 그대로 열리는지(분류를 바꾸는 커밋에서 걸리게)."""
        self.assertEqual(arrangement.check_files(tagdb.SHARED_ARRANGEMENTS, tagdb.load_config()), [])


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
    HAVE_QT = True
except ImportError:                                  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class CanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.chunks import ChunkCanvas
        self.canvas = ChunkCanvas()
        self.canvas.resize(700, 400)
        self.model = sample()
        self.canvas.set_model(self.model, pack().taxonomy)
        self.changes = []
        self.canvas.changed.connect(self.changes.append)
        self.canvas.show()
        QTest.qWait(1)

    def tearDown(self):
        # Ctrl 을 붙인 마지막 키 입력 뒤에는 Qt 가 Ctrl 을 누른 상태로 기억한다(다음 테스트의 selectRow 가 더하기로 바뀜)
        QTest.keyClick(self.canvas, Qt.Key_Escape)
        self.canvas.close()
        self.canvas.deleteLater()

    def key(self, key, mods=None):          # 기본값에 Qt 를 쓰면 PySide6 없는 PC 에서 모듈을 읽다 멈춘다
        QTest.keyClick(self.canvas, key, Qt.NoModifier if mods is None else mods)

    def center(self, cid, mi=-1):
        box = next(b for b in self.canvas._boxes if b.cid == cid and b.mi == mi)
        return box.rect.center().toPoint()

    def context_menu(self, pos, choose=None):
        actions = {a.text(): a for a in self.canvas._context_menu(QPointF(pos)).actions()}
        if choose:
            self.assertIn(choose, actions)
            actions[choose].trigger()
        return list(actions)

    def test_gap_chunk_menus_and_before_first_chunk(self):
        labels = self.context_menu(self.center("count"), "아래에 빈 줄 넣기")
        self.assertNotIn("위에 빈 줄 넣기", labels)
        self.assertIn("아래 빈 줄 빼기", self.context_menu(self.center("count")))
        self.assertIn(0, self.canvas._gap_bands)
        self.context_menu(self.center("character"), "위 빈 줄 빼기")
        self.assertEqual(self.canvas._gap_bands, {})
        self.context_menu(self.center("character"), "위에 빈 줄 넣기")
        self.assertTrue(self.model.has_gap_before("character"))
        self.assertEqual(len(self.changes), 3)

    def test_gap_row_menu_band_and_drop_target(self):
        y = self.center("count").y()
        old_top = self.canvas._bands[1][0]
        self.context_menu(QPoint(4, y), "이 줄 아래에 빈 줄 넣기")
        band = self.canvas._gap_bands[0]
        self.assertGreater(self.canvas._bands[1][0], old_top)
        self.assertLessEqual(band.bottom(), self.canvas._bands[0][1])
        for by in (band.top(), band.center().y(), band.bottom()):
            self.assertEqual(self.canvas._row_target(QPointF(100, by)), ("row", 0, 1))
        self.assertEqual(self.canvas._row_target(QPointF(self.center("character")))[:2], ("row", 1))
        self.assertEqual(self.canvas._row_target(QPointF(100, self.canvas._bands[1][0] - 3)), ("newrow", 1))
        self.assertIn("이 줄 아래에 빈 줄 빼기", self.context_menu(QPoint(650, y)))
        self.context_menu(band.center().toPoint(), "빈 줄 빼기")
        self.assertEqual(self.canvas._bands[1][0], old_top)
        self.assertEqual(self.canvas._gap_bands, {})

    def test_drag_onto_gap_keeps_same_row_and_marks_middle_chunk(self):
        self.context_menu(self.center("count"), "아래에 빈 줄 넣기")
        band = self.canvas._gap_bands[0]
        self.drag(self.center("style"), band.center().toPoint())
        self.assertEqual(self.model.rows[0], ["count", "style"])
        self.assertTrue(self.canvas._chip_label("count").endswith("↵"))
        self.assertFalse(self.canvas.chunk_label("count").endswith("↵"))       # 메뉴에 쓰는 이름에는 없다
        self.assertNotIn(0, self.canvas._gap_bands)
        self.canvas.resize(260, 400)
        self.canvas.refresh()
        self.context_menu(self.center("style"), "아래에 빈 줄 넣기")
        band = self.canvas._gap_bands[0]
        self.assertGreater(band.top(), max(b.rect.bottom() for b in self.canvas._boxes if b.row == 0))
        self.assertEqual(self.canvas._row_target(band.center()), ("row", 0, 2))

    def row_y(self, row):
        return int(self.canvas._bands[row][0]) + 10

    def test_drag_row_by_gutter_and_blank_space(self):
        order = [list(r) for r in self.model.rows]
        self.drag(QPoint(10, self.row_y(0)), QPoint(10, self.row_y(2) + 2))     # 줄 번호를 2번 줄 앞으로
        self.assertEqual(self.model.rows[:2], [order[1], order[0]])
        self.assertEqual(self.model.rows[2:], order[2:])
        self.assertEqual(len(self.changes), 1)
        self.drag(QPoint(650, self.row_y(1)), QPoint(650, self.row_y(0)))       # 줄의 빈 자리로 끌어도 같다
        self.assertEqual(self.model.rows, order)
        self.drag(QPoint(10, self.row_y(0)), QPoint(10, self.row_y(0) + 4))     # 제자리면 바뀌지 않는다
        self.assertEqual(self.model.rows, order)
        self.assertEqual(len(self.changes), 2)

    def test_row_drag_keeps_gap_with_its_row_and_escape_cancels(self):
        self.context_menu(self.center("count"), "아래에 빈 줄 넣기")
        order = [list(r) for r in self.model.rows]
        self.drag(QPoint(10, self.row_y(0)), QPoint(10, self.row_y(2) + 2))
        self.assertEqual(self.model.rows[1], order[0])
        self.assertIn(1, self.canvas._gap_bands)
        QTest.mousePress(self.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(10, self.row_y(0)))
        QTest.mouseMove(self.canvas, QPoint(10, self.row_y(2)))
        self.key(Qt.Key_Escape)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(10, self.row_y(2)))
        self.assertEqual(self.model.rows[1], order[0])

    def test_undo_during_row_drag_cancels_when_row_is_gone(self):
        last = len(self.model.rows) - 1
        QTest.mousePress(self.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(10, self.row_y(last)))
        QTest.mouseMove(self.canvas, QPoint(10, self.row_y(0)))
        self.model.load({"rows": [["count"]], "chunks": {"count": {"members": ["subject/count"]}}})
        self.canvas.refresh()
        self.canvas.grab()                                                    # 그려도 잘못된 줄을 찾지 않는다
        QTest.mouseRelease(self.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(10, self.row_y(0)))
        self.assertEqual(self.model.rows, [["count"]])
        self.assertEqual(self.changes, [])

    def test_drag_gap_band_to_other_boundary(self):
        self.context_menu(self.center("count"), "아래에 빈 줄 넣기")
        last_of = lambda row: self.model.chunks[self.model.rows[row][-1]]
        band = self.canvas._gap_bands[0].center().toPoint()
        self.drag(band, QPoint(100, int(self.canvas._bands[1][0]) + 30))        # 1번 줄 가운데 아래 = 2번 줄 앞
        self.assertNotIn("gap_after", last_of(0))
        self.assertTrue(last_of(1)["gap_after"])
        band = self.canvas._gap_bands[1].center().toPoint()
        self.drag(band, QPoint(100, 2))                                         # 맨 위로는 못 가서 첫 줄 아래로
        self.assertTrue(last_of(0)["gap_after"])
        self.assertEqual(self.canvas._gap_bands.keys(), {0})

    def test_gap_button_click_uses_selection_or_bottom(self):
        from gui.chunks import GapButton
        button = GapButton(self.canvas)
        button.resize(24, 24)
        button.show()
        self.canvas.select("")
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(self.canvas._gap_bands.keys(), {len(self.model.rows) - 1})     # 고른 것이 없으면 맨 아래
        self.canvas.select("count")
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertTrue(self.model.chunks["count"]["gap_after"])
        before = len(self.changes)
        QTest.mouseClick(button, Qt.LeftButton)                                         # 이미 있으면 그대로
        self.assertEqual(len(self.changes), before)
        button.close()

    def test_gap_button_drag_drops_between_rows_and_cancels_outside(self):
        from gui.chunks import GapButton
        button = GapButton(self.canvas)
        button.setGeometry(0, 0, 24, 24)
        button.show()

        def move(global_point):
            local = button.mapFromGlobal(global_point)
            QApplication.sendEvent(button, QMouseEvent(QEvent.MouseMove, QPointF(local), QPointF(global_point), Qt.NoButton,
                                                       Qt.LeftButton, Qt.NoModifier))

        target = self.canvas.mapToGlobal(QPoint(120, int(self.canvas._bands[1][0]) - 2))
        QTest.mousePress(button, Qt.LeftButton, Qt.NoModifier, QPoint(12, 12))
        move(target)
        self.assertEqual(self.canvas._drop, ("between", 1))
        clicks = []
        button.clicked.connect(lambda: clicks.append(1))
        QTest.mouseRelease(button, Qt.LeftButton, Qt.NoModifier, button.mapFromGlobal(target))
        self.assertEqual(self.canvas._gap_bands.keys(), {0})
        self.assertEqual(clicks, [])                                                    # 끌면 clicked 가 나가지 않는다

        before = self.model.dumps()
        outside = self.canvas.mapToGlobal(QPoint(5000, 5000))
        QTest.mousePress(button, Qt.LeftButton, Qt.NoModifier, QPoint(12, 12))
        move(outside)
        self.assertIsNone(self.canvas._drop)
        QTest.mouseRelease(button, Qt.LeftButton, Qt.NoModifier, button.mapFromGlobal(outside))
        self.assertEqual(self.model.dumps(), before)
        button.close()

    def test_alt_arrows_move_whole_row(self):
        order = [list(r) for r in self.model.rows]
        self.canvas.setFocus()
        self.canvas.select(order[0][0])
        self.key(Qt.Key_Up, Qt.AltModifier)                                   # 맨 위에서는 그대로
        self.assertEqual(self.model.rows, order)
        self.key(Qt.Key_Down, Qt.AltModifier)
        self.assertEqual(self.model.rows[:2], [order[1], order[0]])
        self.assertEqual(self.canvas.sel, (order[0][0], -1))
        self.key(Qt.Key_Up, Qt.AltModifier)
        self.assertEqual(self.model.rows, order)

    def test_keyboard_edits(self):
        self.canvas.setFocus()
        self.canvas.select("character")
        self.key(Qt.Key_Backspace)                                            # 앞 줄과 합치기
        self.assertEqual(self.model.rows[0], ["count", "character"])
        self.key(Qt.Key_Left, Qt.ControlModifier)
        self.assertEqual(self.model.rows[0], ["character", "count"])
        self.key(Qt.Key_Right)                                                # 다음 청크 고르기
        self.assertEqual(self.canvas.sel, ("count", -1))
        self.key(Qt.Key_Return)                                               # 앞에서 줄바꿈
        self.assertEqual(self.model.rows[:2], [["character"], ["count"]])
        self.key(Qt.Key_Delete)
        self.assertTrue(self.model.chunks["count"].get("hidden"))
        self.key(Qt.Key_Up)
        self.assertEqual(self.canvas.sel, ("character", -1))
        self.key(Qt.Key_Space)                                                # 펼치고 → 로 서브에
        self.assertIn("character", self.canvas.expanded)
        self.key(Qt.Key_Right)
        self.assertEqual(self.canvas.sel, ("character", 0))
        self.key(Qt.Key_Right, Qt.ControlModifier)                            # 서브 순서 바꾸기
        self.assertEqual(self.model.members("character")[:2], ["subject/character", "subject/species"])
        self.key(Qt.Key_Return)                                               # 서브를 새 청크로
        self.assertEqual(self.model.rows[0], ["character", "subject.species"])
        self.assertEqual(len(self.changes), 6)

    def test_undo_key_is_canvas_own(self):
        asked = []
        self.canvas.undo_requested.connect(lambda: asked.append(True))
        self.canvas.setFocus()
        self.canvas.select("count")
        self.key(Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(asked, [True])

    def drag(self, start, end):
        QTest.mousePress(self.canvas, Qt.LeftButton, Qt.NoModifier, start)
        for i in range(1, 6):
            QTest.mouseMove(self.canvas, start + (end - start) * i / 5)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, Qt.NoModifier, end)

    def test_drag_chunk_onto_row_and_between_rows(self):
        target = self.center("count") + QPoint(40, 0)                         # 1번 줄 count 오른쪽
        self.drag(self.center("style"), target)
        self.assertEqual(self.model.rows[0], ["count", "style"])
        top, _ = self.canvas._bands[1]
        between = QPoint(self.center("count").x(), int(top) - 3)              # 1·2번 줄 사이 → 새 줄
        self.drag(self.center("style"), between)
        self.assertEqual(self.model.rows[:2], [["count"], ["style"]])

    def test_drag_member_to_other_chunk_and_out(self):
        self.canvas.toggle_expanded("hair")
        self.drag(self.center("hair", 1), self.center("clothes"))             # 머리 장식 → 의상(접힘: 끝에)
        self.assertEqual(self.model.members("clothes")[-1], "clothes/hair_acc")
        self.canvas.toggle_expanded("character")
        _, bottom = self.canvas._bands[-1]
        self.drag(self.center("character", 3), QPoint(60, int(bottom) + 8))  # 작품을 맨 아래 새 줄로
        self.assertEqual(self.model.rows[-1], ["series"])
        self.assertNotIn("series", self.model.members("character"))


@unittest.skipUnless(HAVE_QT, "PySide6 없음")
class PreviewArrangeTests(unittest.TestCase):
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
        self.page = self.win.preview_page
        self.panel = self.page.arrange
        self.page.ensure_pack()
        self.wait()

    def tearDown(self):
        self.wait()
        self.win._poll.stop()
        self.panel.saved = self.panel.model.dumps()                            # 닫을 때 저장 확인 창이 뜨지 않게
        with mock.patch("gui.app.QSettings"):
            self.win.close()
        self.win.deleteLater()
        QTest.qWait(1)
        self._tmp.cleanup()

    def test_close_asks_about_unsaved_arrangement(self):
        self.panel.model.toggle_hidden("count")
        self.panel._canvas_changed(self.panel.saved)
        with mock.patch("gui.arrange_panel.QMessageBox.question", return_value=QMessageBox.Cancel) as ask, \
                mock.patch("gui.app.QSettings"):
            self.assertFalse(self.win.close())                                 # 취소하면 닫지 않음
        ask.assert_called_once()
        with mock.patch("gui.arrange_panel.QMessageBox.question", return_value=QMessageBox.Save), \
                mock.patch("gui.arrange_panel.QInputDialog.getText", return_value=("닫기 전", True)):
            self.assertTrue(self.panel.confirm_close())
        self.assertTrue(Path(self.env.arrangements_dir, "닫기_전.json").is_file())

    def test_filter_drops_hidden_selection(self):
        self.win.tabs.setCurrentWidget(self.page)
        self.win.show()
        self.run_page("smile, odd phrase")
        self.page.tokens.selectRow(0)
        self.assertEqual(self.win.editor.names, ["smile"])
        self.page.set_filter("unknown")                                        # smile 이 숨으면 편집 패널도 비움
        self.assertEqual(self.win.editor.names, [])
        self.page.set_filter("all")
        self.page.tokens.selectRow(0)
        self.page.set_filter("unknown")
        self.page.run()                                                         # 다시 계산해도 숨은 행은 다시 고르지 않음
        self.assertEqual(self.page.selected_names(), [])

    def wait(self):
        from gui import jobs
        end = time.time() + 15
        while jobs.active and time.time() < end:
            QTest.qWait(20)
        QTest.qWait(20)

    def run_page(self, text):
        self.page.input.setPlainText(text)
        self.page.run()
        return self.page.report

    def test_framing_override_dims_hidden_tags(self):
        self.page.preset.setCurrentIndex(self.page.preset.findData("sort_only"))
        self.run_page("1girl, full body, shoes, smile")
        self.assertNotIn("shoes", self.page.removed.toPlainText())
        self.panel.set_quick("framing", "upper body")
        self.page.run()
        self.assertIn("full body  # override:framing", self.page.removed.toPlainText())
        self.assertIn("shoes  # rule:framing(upper body)", self.page.removed.toPlainText())
        self.assertIn("upper body", self.page.output.toPlainText())
        html = self.page.rich.toHtml()
        self.assertIn("line-through", html)                                    # 뺀 태그도 제자리에 흐리게
        self.assertIn("shoes", self.page.rich.toPlainText())
        self.page.show_removed.setChecked(False)
        self.assertNotIn("shoes", self.page.rich.toPlainText())
        chips = {b.property("tag"): b for b in self.panel._chips["framing"].buttons()}
        self.assertTrue(chips["full body"].property("present"))               # 프롬프트에 있던 크롭 표시

    def test_gaze_chips_select_several_and_clear_conflicts(self):
        self.run_page("1girl, looking down, smile")
        chips = {b.property("tag"): b for b in self.panel._chips["gaze"].buttons()}
        self.assertTrue(chips[""].isChecked())
        self.assertTrue(chips["looking down"].property("present"))
        chips["looking back"].click()
        chips["looking at viewer"].click()
        self.assertEqual(self.panel.gaze, "looking at viewer, looking back")
        self.assertFalse(chips[""].isChecked())
        self.assertTrue(chips["looking back"].text().startswith("✓"))
        self.page.run()
        self.assertIn("looking down  # override:gaze", self.page.removed.toPlainText())
        self.assertIn("looking back", self.page.output.toPlainText())
        chips["facing away"].click()
        self.assertEqual(self.panel.gaze, "facing away")
        self.assertFalse(chips["looking back"].isChecked())
        self.assertEqual(chips["looking back"].text(), "looking back")
        chips["facing away"].click()
        self.assertEqual(self.panel.gaze, "")
        self.assertTrue(chips[""].isChecked())
        chips["looking up"].click()
        chips[""].click()
        self.assertEqual(self.panel.gaze, "")
        self.assertTrue(chips[""].isChecked())
        self.assertFalse(chips["looking up"].isChecked())

    def test_gaze_chips_follow_view(self):
        chips = {b.property("tag"): b for b in self.panel._chips["gaze"].buttons()}
        self.panel.set_quick("gaze", "looking back, looking at viewer")
        self.panel.set_quick("view", "straight-on")
        self.run_page("1girl, smile")
        self.assertTrue(chips["looking back"].isHidden())
        self.assertFalse(chips["looking at viewer"].isHidden())
        self.assertTrue(chips["looking back"].isChecked())                     # 숨겨도 선택은 남는다
        self.assertFalse(chips[""].isChecked())                                # looking at viewer 는 살아 있다
        self.assertNotIn("looking back", self.page.output.toPlainText())
        self.panel.set_quick("view", "from behind")
        self.page.run()
        self.assertFalse(chips["looking back"].isHidden())
        self.assertIn("looking back", self.page.output.toPlainText())
        self.assertIn("smile", self.page.output.toPlainText())
        self.panel.set_quick("gaze", "looking back")
        self.panel.set_quick("view", "straight-on")
        self.page.run()
        self.assertTrue(chips[""].isChecked())                                 # 고른 것이 모두 막히면 자동
        self.panel.set_quick("view", "from behind")
        self.page.run()
        self.assertFalse(chips[""].isChecked())
        self.run_page("1girl, straight-on, looking back")                      # 프롬프트의 시점도 따른다
        self.panel.set_quick("view", "")
        self.page.run()
        self.assertTrue(chips["looking back"].isHidden())

    def test_pose_chips_select_several_and_keep_place_tags(self):
        self.run_page("1girl, sitting, on bed")
        chips = {b.property("tag"): b for b in self.panel._chips["pose"].buttons()}
        self.assertEqual(len(chips), 18)
        self.assertTrue(chips[""].isChecked())
        self.assertTrue(chips["sitting"].property("present"))
        chips["lying"].click()
        chips["on back"].click()
        self.assertEqual(self.panel.pose, "lying, on back")
        self.assertFalse(chips[""].isChecked())
        self.page.run()
        self.assertIn("sitting  # override:pose", self.page.removed.toPlainText())
        output = self.page.output.toPlainText()
        for tag in ("lying", "on back", "on bed"):
            self.assertIn(tag, output)
        chips["standing"].click()
        self.assertEqual(self.panel.pose, "standing")
        self.assertFalse(chips["lying"].isChecked())
        chips[""].click()
        self.assertEqual(self.panel.pose, "")
        self.assertTrue(chips[""].isChecked())

    def test_pose_chips_follow_framing(self):
        chips = {b.property("tag"): b for b in self.panel._chips["pose"].buttons()}
        self.panel.set_quick("pose", "standing")
        self.panel.set_quick("framing", "portrait")
        self.run_page("1girl, smile")
        self.assertTrue(chips["standing"].isHidden())
        self.assertFalse(chips[""].isHidden())
        self.assertTrue(chips["standing"].isChecked())                         # 숨겨도 선택은 남는다
        self.assertTrue(chips[""].isChecked())                                 # 고른 것이 모두 막히면 자동
        self.assertNotIn("standing", self.page.output.toPlainText())
        self.panel.set_quick("framing", "upper body")
        self.page.run()
        self.assertFalse(chips["standing"].isHidden())
        self.assertFalse(chips[""].isChecked())
        self.assertIn("standing", self.page.output.toPlainText())

    def test_view_chips_combine_and_conflict(self):
        chips = {b.property("tag"): b for b in self.panel._chips["view"].buttons()}
        self.run_page("1girl, ass, groin")
        chips["from behind"].click()
        chips["from side"].click()
        self.assertEqual(self.panel.view, "from side, from behind")
        self.assertFalse(chips[""].isChecked())
        self.page.run()
        self.assertNotIn("groin", self.page.output.toPlainText())
        self.assertIn("ass", self.page.output.toPlainText())
        chips["front view"].click()                                            # from behind 와 부딪친다
        self.assertEqual(self.panel.view, "front view, from side")
        self.assertFalse(chips["from behind"].isChecked())
        self.page.run()
        self.assertNotIn("ass", self.page.output.toPlainText())
        chips["multiple views"].click()
        self.page.run()
        chips["from behind"].click()
        self.assertEqual(self.panel.view, "front view, from side, from behind, multiple views")
        self.page.run()
        self.assertIn("ass", self.page.output.toPlainText())
        self.assertIn("groin", self.page.output.toPlainText())
        chips["multiple views"].click()                                        # 끄면 부딪치는 것 중 앞쪽만 남는다
        self.assertEqual(self.panel.view, "front view, from side")
        chips[""].click()
        self.assertEqual(self.panel.view, "")
        self.assertTrue(chips[""].isChecked())
        self.assertFalse(chips["front view"].isChecked())

    def test_filter_and_custom_chunk_from_tokens(self):
        self.page.preset.setCurrentIndex(self.page.preset.findData("sort_only"))
        self.run_page("smile, my odd phrase, holding chainsword, 1girl")
        self.page.set_filter("unknown")
        visible = [self.page.tokens.item(i, 0).text() for i in range(self.page.tokens.rowCount())
                   if not self.page.tokens.isRowHidden(i)]
        self.assertEqual(visible, ["my odd phrase", "holding chainsword"])
        self.assertEqual(self.page.filter_buttons["unknown"].text(), "팩에 없음 2")
        self.panel.guess_box.setChecked(True)
        self.page.run()
        self.assertEqual(self.page.filter_buttons["guess"].text(), "추정 1")
        self.page.set_filter("all")
        self.page.tokens.selectRow(1)
        with mock.patch("gui.arrange_panel.CustomChunkDialog.exec", return_value=QDialog.Accepted):
            cid = self.panel.new_custom(self.page.selected_texts())
        self.page.run()
        self.assertEqual(self.panel.model.chunks[cid]["tags"], ["my odd phrase"])
        self.assertTrue(self.page.output.toPlainText().startswith("my odd phrase,\n1girl"))
        self.panel.undo()
        self.page.run()
        self.assertNotIn(cid, self.panel.model.chunks)

    def test_save_reopen_and_dirty_switch(self):
        self.panel.model.join_previous("character")
        self.panel._canvas_changed(sample().dumps())
        self.assertTrue(self.panel.dirty())
        with mock.patch("gui.arrange_panel.QInputDialog.getText", return_value=("내 배열", True)):
            self.assertTrue(self.panel.save())
        path = Path(self.env.arrangements_dir, "내_배열.json")
        self.assertTrue(path.is_file())
        self.assertFalse(self.panel.dirty())
        self.assertEqual(self.panel.combo.currentData(), str(path))
        self.panel.model.toggle_hidden("count")
        self.panel._canvas_changed(self.panel.saved)
        index = self.panel.combo.findData("layout:compact")
        with mock.patch("gui.arrange_panel.QMessageBox.question", return_value=QMessageBox.No):
            self.panel._combo_chosen(index)
        self.assertEqual(self.panel.source, str(path))                         # 버리지 않으면 그대로
        with mock.patch("gui.arrange_panel.QMessageBox.question", return_value=QMessageBox.Yes):
            self.panel._combo_chosen(index)
        self.assertEqual(self.panel.source, "layout:compact")
        self.panel._combo_chosen(self.panel.combo.findData(str(path)))
        self.assertEqual(self.panel.model.rows[0], ["count", "character"])     # 저장한 배열을 다시 엶

    def shared_file(self, name="portrait-basic", **extra):
        data = sample().to_json() | {"label": "인물 기본", "description": "인물 중심 배열"} | extra
        path = Path(self.env.shared_arrangements_dir, f"{name}.json")
        arrangement.write_file(path, data)
        self.panel._fill_combo()
        return path

    def test_shared_preset_saves_as_mine(self):
        path = self.shared_file()
        original = path.read_text(encoding="utf-8")
        index = self.panel.combo.findData(str(path))
        self.assertEqual(self.panel.combo.itemText(index), "공유: 인물 기본")
        self.assertEqual(self.panel.combo.itemData(index, Qt.ToolTipRole), "인물 중심 배열")
        self.panel._combo_chosen(index)
        self.assertTrue(self.panel.is_shared(self.panel.source))
        self.assertIn("공유 프리셋", self.panel.state.text())
        self.panel.model.join_previous("character")
        self.panel._canvas_changed(self.panel.saved)
        with mock.patch("gui.arrange_panel.QInputDialog.getText", return_value=("내 인물", True)) as ask:
            self.assertTrue(self.panel.save())                                 # 공유본이 아니라 내 배열로
        ask.assert_called_once()
        mine = Path(self.env.arrangements_dir, "내_인물.json")
        self.assertEqual(self.panel.source, str(mine))
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(arrangement.read_file(mine)["description"], "인물 중심 배열")

    def publish(self, share, label="인물 기본", description="설명", overwrite=QMessageBox.Yes):
        from gui.arrange_panel import PublishDialog
        with mock.patch.object(PublishDialog, "exec", return_value=QDialog.Accepted), \
                mock.patch.object(PublishDialog, "values", return_value=(share, label, description)), \
                mock.patch("gui.arrange_panel.QMessageBox.question", return_value=overwrite) as confirm, \
                mock.patch("gui.arrange_panel.QMessageBox.information"):
            return self.panel.publish(), confirm

    def test_publish_to_shared_folder(self):
        self.panel.model.join_previous("character")
        self.panel._canvas_changed(self.panel.saved)
        done, confirm = self.publish("upper-focus", "상반신 중심", "얼굴·상반신 먼저")
        self.assertTrue(done)
        confirm.assert_not_called()                                            # 새 파일은 묻지 않음
        path = Path(self.env.shared_arrangements_dir, "upper-focus.json")
        data = arrangement.read_file(path)
        self.assertEqual((data["label"], data["description"]), ("상반신 중심", "얼굴·상반신 먼저"))
        self.assertEqual(data["rows"][0], ["count", "character"])
        self.assertEqual(self.panel.source, str(path))
        self.assertFalse(self.panel.dirty())
        self.assertEqual(arrangement.check_files(self.env.shared_arrangements_dir, self.store.config), [])
        self.panel.model.toggle_hidden("count")
        self.panel._canvas_changed(self.panel.saved)
        done, confirm = self.publish("upper-focus", overwrite=QMessageBox.No)  # 덮어쓰기는 묻고, 아니면 그대로
        self.assertFalse(done)
        confirm.assert_called_once()
        self.assertNotIn("hidden", json.dumps(arrangement.read_file(path)["chunks"]["count"]))
        self.assertEqual(self.panel.model.label, "상반신 중심")

    def test_publish_warns_about_custom_tags(self):
        from gui.arrange_panel import PublishDialog
        from PySide6.QtWidgets import QLabel

        def texts(custom):
            dialog = PublishDialog(self.panel, Path(self.env.shared_arrangements_dir), "x", "y", "", custom)
            found = " ".join(label.text() for label in dialog.findChildren(QLabel))
            dialog.deleteLater()
            return found

        self.assertNotIn("커스텀 청크", texts(0))
        self.assertIn("태그·패턴 3개", texts(3))                                # 개인 태그가 저장소로 간다는 경고
        self.panel.model.add_custom("내 묘사", tags=["my phrase", "other"], patterns=["* armor"])
        with mock.patch("gui.arrange_panel.PublishDialog", wraps=PublishDialog) as made, \
                mock.patch.object(PublishDialog, "exec", return_value=QDialog.Rejected):
            self.assertFalse(self.panel.publish())
        self.assertEqual(made.call_args[0][5], 3)

    def test_clipboard_round_trip(self):
        self.panel.model.join_previous("character")
        self.panel._canvas_changed(self.panel.saved)
        self.panel.copy_clipboard()
        text = QApplication.clipboard().text()
        self.assertNotIn("\n", text)                                          # 메신저에 붙이기 좋게 한 줄
        expected = self.panel.model.dumps()
        self.panel._open("layout:compact")
        self.panel.paste_clipboard()
        self.assertEqual(self.panel.model.dumps(), expected)
        self.assertEqual(self.panel.source, "")
        self.assertTrue(self.panel.dirty())
        QApplication.clipboard().setText("그냥 글")
        self.panel.saved = self.panel.model.dumps()
        with mock.patch("gui.arrange_panel.QMessageBox.warning") as warn:
            self.panel.paste_clipboard()
        self.assertIn("JSON", warn.call_args[0][2])
        self.assertEqual(self.panel.model.dumps(), expected)                  # 실패하면 그대로

    def test_opens_preset_from_older_taxonomy(self):
        stale = sample().to_json()
        stale["chunks"]["hair"]["members"] = ["body/hair", "body/old_sub"]
        path = self.shared_file("old-one", chunks=stale["chunks"])
        with mock.patch("gui.arrange_panel.QMessageBox.information") as info:
            self.assertTrue(self.panel._open(str(path)))
        self.assertIn("body/old_sub", info.call_args[0][2])
        self.assertEqual(self.panel.model.members("hair"), ["body/hair"])
        self.assertTrue(self.panel.dirty())                                    # 저장해야 파일에 반영

    def test_rating_names_in_preview(self):
        from gui import theme
        self.assertEqual(self.page.max_rating.currentText(), "최대 explicit")
        self.page.preset.setCurrentIndex(self.page.preset.findData("sort_only"))
        self.run_page("smile, nipples, my odd phrase")
        cells = {self.page.tokens.item(i, 0).text(): self.page.tokens.item(i, 3)
                 for i in range(self.page.tokens.rowCount())}
        self.assertEqual(cells["smile"].text(), "general")
        self.assertEqual(cells["smile"].foreground().color().name(), theme.tokens()["rating_g"])
        self.assertEqual(cells["nipples"].text(), "questionable")
        self.assertEqual(cells["my odd phrase"].text(), "")                   # 팩에 없는 토큰은 등급 없음

    def test_input_height_is_adjustable(self):
        self.assertIs(self.page.split.widget(0), self.page.input)
        self.assertEqual(self.page.input.maximumHeight(), 16777215)            # 고정 높이 없음(경계선으로 조절)

    def test_bad_guess_rule_line(self):
        from gui.arrange_panel import GuessRulesDialog
        rules = GuessRulesDialog.parse("holding * => object/weapon\n* gauntlets => clothes/handwear | hands\n# 메모")
        self.assertEqual(rules, [{"pattern": "holding *", "primary": "object/weapon"},
                                 {"pattern": "* gauntlets", "primary": "clothes/handwear", "regions": ["hands"]}])
        with self.assertRaises(ValueError):
            GuessRulesDialog.parse("no arrow here")
        # 리뷰 4: 가져온 규칙의 모든 칸(등급 포함)을 글로 옮겼다 되읽어도 그대로
        rule = {"pattern": "my *", "primary": "body", "regions": ["eyes"], "identity": True, "rating": "e"}
        self.assertEqual(GuessRulesDialog.parse(GuessRulesDialog.format_rule(rule)), [rule])


if __name__ == "__main__":
    unittest.main()
