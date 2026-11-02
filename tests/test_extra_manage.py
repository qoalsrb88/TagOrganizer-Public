"""확장 DB 3단계 테스트 — 손 수정(fix), 충돌 검사 저장, tagcomplete 내보내기."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fixtures import SOURCE, builder, overlay_rows, source_tags, tagdb
from tools import extra, fix, tagcomplete

CONFIG = tagdb.load_config()


def row(db, name, **fields):
    return {**{k: "" for k in tagdb.EXTRA_FIELDS}, "db": db, "name": name, **fields}


class FixRowsTests(unittest.TestCase):
    def setUp(self):
        self.rows = {
            "aqua legwear": row("deprecated", "aqua legwear", primary="clothes/legwear", rating="g", status="accepted",
                                note="LLM 근거", confidence="0.9", use_instead="aqua_thighhighs"),
            "peace sign": row("model", "peace sign", same_as="v", models="nai", status="accepted"),
            "new tag": row("user", "new tag"),
        }

    def test_classify_and_lock(self):
        changes, errors, _ = fix.apply_fix_rows(self.rows, ["aqua legwear", "new tag"],
                                                {"rating": "s", "primary": "clothes/legwear"}, CONFIG, "user:t")
        self.assertEqual(errors, [])
        after = self.rows["aqua legwear"]
        self.assertEqual((after["rating"], after["status"], after["by"], after["confidence"], after["note"],
                          after["use_instead"], after["db"]),
                         ("s", "reviewed", "user:t", "", "LLM 근거", "aqua_thighhighs", "deprecated"))
        self.assertEqual(self.rows["new tag"]["note"], "")                 # 분류가 바뀌면 이전 메모를 지움
        self.assertEqual([n for n, _, _ in changes], ["aqua legwear", "new tag"])

    def test_extra_fields_and_models_check(self):
        _, errors, _ = fix.apply_fix_rows(self.rows, ["aqua legwear"], {"models": "NAI v4"}, CONFIG, "user:t")
        self.assertTrue(errors and "models" in errors[0])
        self.assertEqual(self.rows["aqua legwear"]["models"], "")          # 오류면 아무것도 바꾸지 않음
        changes, errors, _ = fix.apply_fix_rows(self.rows, ["aqua legwear"],
                                                {"use_instead": "a | b", "aliases": "x"}, CONFIG, "user:t")
        self.assertEqual((errors, self.rows["aqua legwear"]["use_instead"]), ([], "a|b"))

    def test_same_as_rows(self):
        _, errors, _ = fix.apply_fix_rows(self.rows, ["peace sign"], {"primary": "action/gesture"}, CONFIG, "user:t")
        self.assertIn("same_as", errors[0])
        _, errors, _ = fix.apply_fix_rows(self.rows, ["peace sign"], {}, CONFIG, "user:t", etc=True)
        self.assertIn("same_as", errors[0])
        changes, errors, _ = fix.apply_fix_rows(self.rows, ["peace sign"], {"note": "NAI", "models": "nai|nai4.5"},
                                                CONFIG, "user:t")
        after = self.rows["peace sign"]
        self.assertEqual((errors, after["primary"], after["same_as"], after["models"], after["status"]),
                         ([], "", "v", "nai|nai4.5", "reviewed"))
        _, _, notes = fix.apply_fix_rows(self.rows, ["peace sign"], {"note": "NAI"}, CONFIG, "user:t")
        self.assertIn("바뀐 것 없음", notes[-1])

    def test_unknown_and_overlay_only(self):
        _, errors, _ = fix.apply_fix_rows(self.rows, ["nope"], {"primary": "meta/quality"}, CONFIG, "user:t")
        self.assertIn("확장 DB 에 없는 태그", errors[0])
        overlay = overlay_rows()
        _, errors, _ = fix.apply_fix(overlay, ["smile"], {"models": "nai"}, source_tags(), CONFIG, "user:t")
        self.assertIn("확장 DB 태그에만", errors[0])

    def test_split_args_extra_fields(self):
        names, updates = fix.split_args(["peace sign", "models=nai", "use_instead=v", "=_="])
        self.assertEqual((names, updates), (["peace sign", "=_="], {"models": "nai", "use_instead": "v"}))


class LookupAndWriteTests(unittest.TestCase):
    def test_lookup_and_db_rows(self):
        rows = [row("user", "kirby \\(series\\)"), row("user", "cinematic lighting"), row("model", "score_9")]
        rows[0]["name"] = "kirby (series)"
        self.assertEqual(extra.lookup(rows, "Kirby_\\(series\\)")["name"], "kirby (series)")
        self.assertEqual(extra.lookup(rows, "CINEMATIC_LIGHTING")["name"], "cinematic lighting")
        self.assertEqual(extra.lookup(rows, "score 9")["name"], "score_9")
        self.assertIsNone(extra.lookup(rows, "nothing"))
        rows[1]["aliases"] = "dramatic lighting|cine light"
        self.assertIsNone(extra.lookup(rows, "dramatic_lighting"))                  # 이름으로는 없음
        self.assertEqual(extra.lookup(rows, "Dramatic_Lighting", aliases=True)["name"], "cinematic lighting")
        new = row("user", "cinematic lighting", primary="effect/lighting")
        self.assertEqual(extra.db_rows(rows, "user", [(rows[1], new)]), [rows[0], new])

    def test_db_rows_replaces_that_row_only(self):
        # 같은 이름이 다른 DB·같은 DB 에 또 있어도(빌드 오류 상태) 고른 그 행만 바꾼다
        model_x, user_x, user_x2 = row("model", "x", note="m"), row("user", "x", note="u"), row("user", "x", note="u2")
        rows = [model_x, user_x, user_x2]
        new = dict(model_x, note="고침")
        self.assertEqual(extra.db_rows(rows, "model", [(model_x, new)]), [new])
        self.assertEqual(extra.db_rows(rows, "user", [(model_x, new)]), [user_x, user_x2])

    def test_write_extra_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            digest = tagdb.write_extra("user", [row("user", "a")], folder)
            self.assertEqual(digest, tagdb.sha256_file(tagdb.extra_path("user", folder)))
            tagdb.write_extra("user", [row("user", "b")], folder, expected=digest)       # 기대한 그대로면 씀
            with self.assertRaises(tagdb.OverlayChanged):
                tagdb.write_extra("user", [row("user", "c")], folder, expected=digest)   # 그사이 바뀜
            self.assertEqual([r["name"] for r in tagdb.read_extra(folder)], ["b"])
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["user.csv"])     # 임시 파일 남지 않음


class TagcompleteTests(unittest.TestCase):
    EXTRA = [
        row("model", "masterpiece", primary="meta/quality", status="reviewed", models="nai"),
        row("model", "wink", same_as="one_eye_closed", status="accepted"),
        row("deprecated", "aqua legwear", primary="clothes/legwear", rating="g", status="accepted",
            aliases="aqua stockings"),
        row("deprecated", "claire", primary="etc", status="accepted"),
        row("deprecated", "old artist", primary="style/artist", status="auto"),
        row("user", "cinematic lighting", primary="effect/lighting", status="accepted"),
        row("user", "^__^", primary="action/expression", status="accepted"),
    ]

    def _pack(self):
        pack, _ = builder.build_pack(source_tags(), {"file": "f.csv", "date": "2026-10-01", "rows": len(SOURCE)},
                                     overlay_rows(), self.EXTRA, CONFIG)
        return pack

    def test_rows(self):
        rows = tagcomplete.rows_for(self._pack(), self.EXTRA)
        self.assertEqual([r[0] for r in rows], ["masterpiece", "wink", "aqua_legwear", "cinematic_lighting", "^__^"])
        by = {r[0]: r for r in rows}
        self.assertEqual(by["wink"][1:3], (0, "행동/표정 · 모델 태그"))         # same_as 는 대상의 분류
        self.assertEqual(by["masterpiece"][1:3], (5, "메타/품질 · 모델 태그"))
        self.assertEqual(by["aqua_legwear"][1:], (0, "의상/레그웨어 · 폐기 태그", "aqua_stockings"))
        everything = tagcomplete.rows_for(self._pack(), self.EXTRA, include_etc=True, include_auto=True)
        self.assertEqual({r[0]: r[1] for r in everything}["old_artist"], 1)
        self.assertIn("claire", [r[0] for r in everything])
        self.assertEqual([r[0] for r in tagcomplete.rows_for(self._pack(), self.EXTRA, dbs=["user"])],
                         ["cinematic_lighting", "^__^"])

    def test_render_no_bom_and_quotes(self):
        data = tagcomplete.render([("a_b", 0, "메모, 쉼표", "x,y")])
        self.assertEqual(data.decode("utf-8"), 'a_b,0,"메모, 쉼표","x,y"\n')

    def test_write_guards_hand_made_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, state = Path(tmp, "tags", "extra.csv"), Path(tmp, "state.json")
            self.assertEqual(tagcomplete.write(out, b"a\n", state), ("created", None))
            self.assertEqual(tagcomplete.write(out, b"a\n", state), ("unchanged", None))
            self.assertEqual(tagcomplete.write(out, b"b\n", state), ("updated", None))    # 내가 쓴 파일은 그냥 갱신
            out.write_bytes(b"hand made\n")                                               # 밖에서 고침
            with self.assertRaises(FileExistsError):
                tagcomplete.write(out, b"c\n", state)
            self.assertEqual(out.read_bytes(), b"hand made\n")
            result, backup = tagcomplete.write(out, b"c\n", state, force=True)
            self.assertEqual((result, backup.read_bytes(), out.read_bytes()), ("replaced", b"hand made\n", b"c\n"))

    def test_same_content_hand_made_file_is_not_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, state = Path(tmp, "extra.csv"), Path(tmp, "state.json")
            out.write_bytes(b"a\n")                                                       # 손으로 만든 파일
            self.assertEqual(tagcomplete.write(out, b"a\n", state), ("unchanged", None))
            self.assertFalse(state.exists())                                             # 내 것으로 기록하지 않음
            with self.assertRaises(FileExistsError):
                tagcomplete.write(out, b"b\n", state)

    def test_state_keeps_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                tagcomplete.write(Path("rel.csv"), b"a\n", Path(tmp, "state.json"))
            finally:
                os.chdir(cwd)
            saved = Path(tagcomplete.read_state(Path(tmp, "state.json"))["out"])
            self.assertTrue(saved.is_absolute())
            self.assertEqual(saved.read_bytes(), b"a\n")


if __name__ == "__main__":
    unittest.main()
