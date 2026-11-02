"""확장 DB 관리 도구 테스트 — 이름 더하기, 폐기 태그 받기, 확장 DB 저장, 배치 내보내기·반영·확정."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import builder, core, overlay_rows, source_tags, tagdb
from tools import batch, extra

CONFIG = tagdb.load_config()


def row(db, name, **fields):
    return {**{k: "" for k in tagdb.EXTRA_FIELDS}, "db": db, "name": name, **fields}


EXTRA = [row("model", "masterpiece", primary="meta/quality", status="reviewed")]


class NameTests(unittest.TestCase):
    def test_tag_name(self):
        self.assertEqual(extra.tag_name(" Cinematic_Lighting "), "cinematic lighting")
        self.assertEqual(extra.tag_name("^_^"), "^_^")
        self.assertEqual(extra.tag_name("CINEMATIC_LIGHTING"), "cinematic lighting")   # 대문자도 이모티콘이 아님
        self.assertEqual(extra.tag_name("T_T"), "t_t")
        self.assertEqual(extra.evidence_name("aqua legwear"), "aqua_legwear")

    def test_extension_kaomoji_stay_apart(self):
        rows, skipped, added = extra.add_names(EXTRA, "user", ["^__^", "^___^", "^__^"], source_tags())
        self.assertEqual(([r["name"] for r in rows], added), (["^__^", "^___^"], 2))
        self.assertEqual(skipped, ["^__^ (user:^__^)"])
        pack, _ = builder.build_pack(source_tags(), {"date": "2026-10-01"}, overlay_rows(),
                                     EXTRA + rows, CONFIG)
        self.assertEqual(pack["extra"]["user"], ["^__^", "^___^"])
        self.assertTrue({"^__^", "^___^", "^_^"} <= set(pack["kaomoji"]))
        self.assertEqual(core.TagPack(pack).resolve(core.normalize_key("^___^", pack["kaomoji"]))[0].key, "^___^")

    def test_add_names_skips_known(self):
        rows, skipped, added = extra.add_names(
            EXTRA + [row("user", "old tag", primary="meta/quality")], "user",
            ["Cinematic_Lighting", "long_hair", "longhair", "Masterpiece", "cinematic lighting", "^_^", "new one"],
            source_tags(), fields={"models": "nai"}, per_name={"new one": {"note": "메모"}})
        self.assertEqual(added, 2)
        self.assertEqual([r["name"] for r in rows], ["old tag", "cinematic lighting", "new one"])
        self.assertEqual((rows[1]["models"], rows[1]["primary"], rows[1]["db"]), ("nai", "", "user"))
        self.assertEqual(rows[2]["note"], "메모")
        self.assertEqual(skipped, ["long_hair (원본)", "longhair (원본 별칭 → long hair)",
                                   "Masterpiece (model:masterpiece)", "cinematic lighting (user:cinematic lighting)",
                                   "^_^ (원본)"])

    def test_read_name_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "list.csv")
            path.write_text("# 메모\nmasterpiece,5,Quality tag,,\n\ncute\n vivid colors ,5,Color,\n"
                            '"cinematic_lighting",0,10\n"foo,bar",0,10\n', encoding="utf-8-sig")
            self.assertEqual(extra.read_name_file(path),
                             ["masterpiece", "cute", "vivid colors", "cinematic_lighting", "foo,bar"])
            path.write_bytes("halation,5,이펙트\n".encode("cp949"))              # 한국어 윈도우 엑셀 저장본
            self.assertEqual(extra.read_name_file(path), ["halation"])


class DeprecatedTests(unittest.TestCase):
    def test_fetch_uses_id_cursor_and_sorts(self):
        pages = {None: [{"id": 9, "name": "b_tag", "post_count": 0, "category": 0},
                        {"id": 8, "name": "a_tag", "post_count": 0, "category": 0}],
                 "b8": [{"id": 5, "name": "c_tag", "post_count": 30, "category": 4},
                        {"id": 4, "name": "a_tag", "post_count": 0, "category": 0}],
                 "b4": [{"id": 1, "name": "d_tag", "post_count": 2, "category": 0}]}
        calls = []

        def get(path, params, not_found_ok=False):
            calls.append(params.get("page"))
            self.assertEqual((path, params["search[is_deprecated]"]), ("/tags.json", "true"))
            return pages[params.get("page")]

        with mock.patch.object(extra, "PAGE", 2):
            tags = extra.fetch_deprecated(get=get, delay=0)
        self.assertEqual(calls, [None, "b8", "b4"])
        self.assertEqual([t["name"] for t in tags], ["c_tag", "d_tag", "a_tag", "b_tag"])  # 사용량 → 이름 순, 중복 없음

    def test_add_deprecated(self):
        tags = [{"name": "aqua_legwear", "post_count": 0, "category": 0},
                {"name": "old_character", "post_count": 3, "category": 4},
                {"name": "smile", "post_count": 9, "category": 0}]       # 원본에 남아 있는 폐기 태그
        rows, skipped, added = extra.add_deprecated(tags, EXTRA, source_tags())
        self.assertEqual(added, 2)
        self.assertEqual([(r["name"], r["primary"], r["status"]) for r in rows],
                         [("aqua legwear", "", ""), ("old character", "subject/character", "auto")])
        self.assertEqual(skipped, ["smile (원본)"])

    def test_record_post_counts(self):
        cache = {"used": {"related": [["x", 0, 0.5]]}, "gone": {"related": [["y", 0, 0.9]]}}
        extra.record_post_counts([{"name": "used", "post_count": 5}, {"name": "gone", "post_count": 0},
                                  {"name": "new", "post_count": 0}], cache)
        self.assertEqual(cache["used"], {"related": [["x", 0, 0.5]], "post_count": 5})
        self.assertEqual(cache["gone"]["related"], [["y", 0, 0.9]])          # 받은 것은 그대로
        self.assertEqual((cache["new"]["related"], cache["new"]["post_count"]), ([], 0))


class WriteTests(unittest.TestCase):
    def test_round_trip_keeps_order(self):
        rows = [row("user", "zeta", primary="meta/quality", note="쉼표, 따옴표\" 포함"), row("user", "alpha")]
        with tempfile.TemporaryDirectory() as tmp:
            tagdb.write_extra("user", rows, Path(tmp))
            self.assertTrue(tagdb.extra_path("user", Path(tmp)).read_bytes().startswith(b"\xef\xbb\xbf"))
            back = tagdb.read_extra(Path(tmp))
        self.assertEqual(back, rows)

    def test_refuses_unknown_columns_and_bad_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                tagdb.write_extra("user", [row("user", "a", **{tagdb.EXTRA_OVERFLOW: "x"})], Path(tmp))
            with self.assertRaises(ValueError):
                tagdb.write_extra("My DB", [row("My DB", "a")], Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.extra = EXTRA + [
            row("deprecated", "aqua legwear"), row("deprecated", "pretty", primary="etc", status="accepted"),
            row("deprecated", "kept", primary="meta/quality", status="reviewed"),
            row("deprecated", "nai name", same_as="smile"), row("user", "other"),
        ]

    def test_pending_and_export(self):
        self.assertEqual([r["name"] for r in extra.pending(self.extra, "deprecated")], ["aqua legwear"])
        self.assertEqual([r["name"] for r in extra.pending(self.extra, "deprecated", held=True)], ["pretty"])
        cache = {"aqua_legwear": {"post_count": 0, "wiki": "Deprecated. Use [[aqua thighhighs]].", "implies": [],
                                  "related": []}}
        with tempfile.TemporaryDirectory() as tmp:
            paths = extra.export_batches(extra.pending(self.extra, "deprecated"), Path(tmp), "D1", 100, True, cache)
            with open(paths[0], encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual((rows[0]["name"], rows[0]["count"], rows[0]["related"]), ("aqua_legwear", "0", ""))
        self.assertIn("aqua thighhighs", rows[0]["wiki"])

    def _results(self, tmp, lines):
        path = Path(tmp, "D1-1.csv")
        path.write_text("name,primary,secondary,rating,regions,identity,confidence,note,use_instead\n"
                        + "".join(line + "\n" for line in lines), encoding="utf-8")
        return batch.read_results([path], optional=extra.EXTRA_RESULT_FIELDS)

    def test_apply_and_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._results(tmp, ["aqua_legwear,clothes/legwear,,g,thighs|lower_legs,0,0.9,옛 이름,aqua_thighhighs",
                                          "pretty,meta/genre,,,,,0.7,,",
                                          "kept,etc,,,,,,의미 불확실,"])
        rows, n, skipped, errors = extra.apply_results(results, self.extra, "deprecated", CONFIG, "claude:t",
                                                       reclassify_etc=True)
        self.assertEqual((n, errors, skipped), (2, [], ["kept (이미 reviewed)"]))
        by_name = {r["name"]: r for r in rows}
        aqua = by_name["aqua legwear"]
        self.assertEqual((aqua["primary"], aqua["rating"], aqua["regions"], aqua["identity"], aqua["status"],
                          aqua["by"], aqua["use_instead"], aqua["note"]),
                         ("clothes/legwear", "g", "thighs|lower_legs", "0", "llm", "claude:t", "aqua_thighhighs", "옛 이름"))
        self.assertEqual(by_name["pretty"]["primary"], "meta/genre")          # accepted 보류는 --etc 로 다시
        self.assertEqual(by_name["kept"]["primary"], "meta/quality")
        self.assertEqual([r["name"] for r in rows], ["aqua legwear", "pretty", "kept", "nai name"])   # 순서 유지
        self.assertEqual(extra.accept(rows), ["aqua legwear", "pretty"])
        self.assertEqual(by_name["aqua legwear"]["status"], "accepted")

    def test_apply_errors_change_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._results(tmp, ["aqua_legwear,clothes/nope,,,,,,,", "other,meta/quality,,,,,,,",
                                          "nai_name,action/expression,,g,,,,,"])
        rows, n, _, errors = extra.apply_results(results, self.extra, "deprecated", CONFIG, "claude:t")
        self.assertEqual(n, 0)
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(any("deprecated 에 없는 태그: other" in e for e in errors))
        self.assertTrue(any("same_as 행" in e for e in errors))
        self.assertEqual(rows[0]["primary"], "")

    def test_accepted_etc_needs_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._results(tmp, ["pretty,meta/genre,,,,,0.7,,"])
        _, n, skipped, _ = extra.apply_results(results, self.extra, "deprecated", CONFIG, "claude:t")
        self.assertEqual((n, skipped), (0, ["pretty (이미 accepted)"]))

    def test_use_instead_blank_clears_missing_keeps(self):
        base = [row("deprecated", "aqua legwear", primary="clothes/legwear", status="llm", use_instead="old_tag")]
        with tempfile.TemporaryDirectory() as tmp:
            blank = self._results(tmp, ["aqua_legwear,clothes/legwear,,g,,,0.9,,"])
            path = Path(tmp, "D2-1.csv")
            path.write_text("name,primary,rating\naqua_legwear,clothes/legwear,g\n", encoding="utf-8")
            missing = batch.read_results([path], optional=extra.EXTRA_RESULT_FIELDS)
        self.assertNotIn("use_instead", missing[0])
        rows, *_ = extra.apply_results(missing, base, "deprecated", CONFIG, "claude:t")
        self.assertEqual(rows[0]["use_instead"], "old_tag")
        rows, *_ = extra.apply_results(blank, base, "deprecated", CONFIG, "claude:t")
        self.assertEqual(rows[0]["use_instead"], "")

    def test_summary(self):
        self.assertEqual(extra.summary(self.extra)[1],
                         "deprecated: 4행 — 분류 1 · 보류(etc) 1 · 미분류 1 · same_as 1")


if __name__ == "__main__":
    unittest.main()
