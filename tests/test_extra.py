"""확장 DB(data/extra) 테스트 — 읽기, 빌드 병합(원본 우선·same_as·별칭 가림), 검사, 코어의 db 표시."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from fixtures import EXTRA, SOURCE, builder, core, overlay_rows, source_tags, tagdb


def build_with(extra):
    """(팩, 확장 DB 경고). 작은 원본이라 rules 의 태그가 없다는 경고는 뺀다."""
    pack, warnings = builder.build_pack(
        source_tags(), {"file": "fixture.csv", "date": "2026-10-01", "rows": len(SOURCE)},
        overlay_rows(), list(EXTRA) + list(extra), tagdb.load_config())
    return pack, [w for w in warnings if w.startswith("extra")]


def row(db, name, **fields):
    return {"db": db, "name": name, **fields}


def build_errors(extra):
    try:
        build_with(extra)
    except builder.BuildError as e:
        return e.errors
    raise AssertionError("BuildError 가 나야 합니다")


class ReadExtraTests(unittest.TestCase):
    def test_db_order_and_db_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            for db in ("zeta", "user", "model", "alpha", "deprecated"):
                Path(tmp, f"{db}.csv").write_text(f"name,primary\n{db} tag,meta/quality\n", encoding="utf-8")
            Path(tmp, "README.md").write_text("DB 가 아님", encoding="utf-8")
            rows = tagdb.read_extra(Path(tmp))
        self.assertEqual([r["db"] for r in rows], ["model", "deprecated", "user", "alpha", "zeta"])
        self.assertEqual(rows[0]["name"], "model tag")

    def test_bom_blank_rows_and_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "user.csv").write_bytes("name,primary,note\n a ,meta/quality,x\n,,\n\nb,meta/quality,1,2\n"
                                              "c,meta/quality,,\nd\n".encode("utf-8-sig"))
            rows = tagdb.read_extra(Path(tmp))
        self.assertEqual([r["name"] for r in rows], ["a", "b", "c", "d"])   # 앞뒤 공백 정리, 빈 행 건너뜀
        self.assertEqual([r.get(tagdb.EXTRA_OVERFLOW) for r in rows], [None, "2", None, None])  # 빈 남는 칸은 무시
        self.assertEqual((rows[3]["primary"], rows[3]["note"]), ("", ""))   # 모자란 칸은 빈 값

    def test_duplicate_header_is_kept_apart(self):
        # csv.DictReader 는 두 번째 primary 의 빈 값으로 첫 값을 조용히 덮어쓴다 — 따로 남겨 검사가 알리게
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "user.csv").write_text("name,primary,primary\nmy tag,meta/quality,\n", encoding="utf-8")
            rows = tagdb.read_extra(Path(tmp))
        self.assertEqual(rows[0]["primary"], "meta/quality")
        errors = build_errors(rows)
        self.assertEqual(errors, ["extra[user] 머리줄에 같은 칸이 두 번 있습니다: primary"])

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(tagdb.read_extra(Path(tmp, "none")), [])


class MergeTests(unittest.TestCase):
    def test_rows_db_and_display(self):
        pack, warnings = build_with([row("user", "cinematic_lighting", primary="effect/lighting", rating="g"),
                                     row("deprecated", "cute")])
        self.assertEqual(warnings, [])
        p = core.TagPack(pack)
        info = p.get("cinematic lighting")
        self.assertEqual((info.display, info.type, info.primary, info.rating, info.db),
                         ("cinematic_lighting", core.TYPE_CUSTOM, "effect/lighting", "g", "user"))
        self.assertEqual((p.get("cute").primary, p.get("cute").db), ("etc", "deprecated"))  # 분류 전 = 보류
        self.assertEqual(p.get("smile").db, "")
        self.assertEqual(pack["extra"], {"model": ["masterpiece", "score 9"], "user": ["cinematic lighting"],
                                         "deprecated": ["cute"]})
        self.assertEqual(p.extra_dbs, ("model", "user", "deprecated"))

    def test_danbooru_name_wins(self):
        pack, warnings = build_with([row("user", "smile", primary="meta/quality")])
        p = core.TagPack(pack)
        self.assertEqual((p.get("smile").primary, p.get("smile").db), ("action/expression", ""))
        self.assertTrue(any("smile" in w and "겹쳐 건너뜁니다" in w for w in warnings))
        self.assertNotIn("user", pack["extra"])

    def test_same_as_keeps_spelling_and_copies_classification(self):
        # NAI 가 표기를 바꾼 경우: wink 는 one_eye_closed 의 Danbooru 별칭이라 그대로면 출력에서 바뀐다
        pack, warnings = build_with([row("model", "wink", same_as="one_eye_closed", models="nai")])
        self.assertEqual(warnings, [])                       # 별칭 대상을 가리키는 same_as 는 조용히
        self.assertNotIn("wink", pack["aliases"])
        p = core.TagPack(pack)
        info, via_alias = p.resolve("wink")
        target = p.get("one eye closed")
        self.assertFalse(via_alias)
        self.assertEqual((info.key, info.display, info.db, info.type), ("wink", "wink", "model", core.TYPE_CUSTOM))
        self.assertEqual((info.primary, info.rating, info.regions, info.identity, info.status),
                         (target.primary, target.rating, target.regions, target.identity, target.status))
        result = core.organize("1girl, wink", p)
        self.assertIn("wink", result.text)
        self.assertNotIn("one eye closed", result.text)

    def test_same_as_through_alias_and_extra_row(self):
        pack, _ = build_with([row("user", "my hair", same_as="longhair"),            # 원본 별칭 → long_hair
                              row("user", "glow light", primary="effect/lighting"),
                              row("user", "glowing light", same_as="glow_light")])   # same_as 가 아닌 확장 DB 행
        p = core.TagPack(pack)
        self.assertEqual((p.get("my hair").primary, p.get("my hair").identity), ("body/hair", True))
        self.assertEqual(p.get("glowing light").primary, "effect/lighting")

    def test_shadowing_alias_without_same_as_warns(self):
        pack, warnings = build_with([row("user", "back_view", primary="composition/angle")])
        self.assertTrue(any("별칭" in w and "from behind" in w for w in warnings))
        self.assertEqual(core.TagPack(pack).resolve("back view")[0].key, "back view")

    def test_aliases_and_use_instead(self):
        pack, warnings = build_with([row("deprecated", "pretty", aliases="prettty", use_instead="smile"),
                                     row("deprecated", "cute", use_instead="adorableness")])
        p = core.TagPack(pack)
        self.assertEqual(p.resolve("prettty")[0].key, "pretty")
        self.assertEqual([w for w in warnings if "use_instead" in w],
                         ["extra use_instead 태그 1개가 원본·확장 DB 에 없습니다(참고용 — 오타만 고치면 됨): "
                          "deprecated:cute → adorableness"])

    def test_use_instead_sees_later_aliases(self):
        _, warnings = build_with([row("deprecated", "cute", use_instead="prettty"),
                                  row("user", "pretty", primary="action/expression", aliases="prettty")])
        self.assertEqual(warnings, [])

    def test_same_as_row_skipped_by_source_is_not_a_chain(self):
        # 첫 행은 원본 이름과 겹쳐 빠지므로 둘째 행의 same_as 는 원본 one_eye_closed 를 가리킨다
        pack, warnings = build_with([row("user", "one eye closed", same_as="closed_eyes"),
                                     row("model", "wink", same_as="one_eye_closed")])
        self.assertTrue(any("겹쳐 건너뜁니다" in w for w in warnings))
        self.assertEqual(core.TagPack(pack).get("wink").primary, "action/expression")

    def test_check_extra_matches_build(self):
        config = tagdb.load_config()

        def check(extra):
            return builder.check_extra(source_tags(), overlay_rows(), list(EXTRA) + extra, config)

        self.assertEqual(check([row("user", "^ ^", same_as="^_^")]), ([], []))     # 이모티콘 정규화도 빌드와 같게
        errors, _ = check([row("user", "my tag", same_as="no such tag")])
        self.assertEqual(errors, ["extra[user:my tag] same_as 대상이 원본·확장 DB 에 없습니다: no such tag"])
        errors, warnings = check([row("user", "smile", primary="meta/quality")])
        self.assertEqual((errors, len(warnings)), ([], 1))

    def test_version_changes_with_extra(self):
        self.assertNotEqual(build_with([])[0]["version"], build_with([row("user", "x tag")])[0]["version"])


class ProblemTests(unittest.TestCase):
    def test_same_as_problems(self):
        errors = build_errors([row("user", "a tag", same_as="smile", primary="action/expression"),
                               row("user", "b tag", same_as="no such tag"),
                               row("user", "c tag", same_as="smile"), row("user", "d tag", same_as="c tag")])
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("same_as 행은 분류 칸", errors[0])
        self.assertTrue(any("same_as 대상이 원본·확장 DB 에 없습니다: no such tag" in e for e in errors))
        self.assertTrue(any("extra[user:d tag]" in e and "도 same_as 행" in e for e in errors))

    def test_duplicate_across_dbs(self):
        errors = build_errors([row("user", "Foo_Tag", primary="meta/quality"), row("deprecated", "foo tag")])
        self.assertEqual(errors, ["extra[deprecated:foo tag] 가 user:Foo_Tag 와 같은 태그입니다"
                                  "(확장 DB 에서 태그 하나는 한 곳에만)"])

    def test_row_and_file_problems(self):
        errors = build_errors([
            row("My DB", "a tag", primary="meta/quality"),
            row("user", "b tag", rating="g"),                                   # primary 없이 분류 칸
            row("user", "c tag", primary="clothes/nope", models="NAI v4"),
            row("user", "d tag", primay="meta/quality"), row("user", "e tag", primay="meta/quality"),
            row("user", "f tag", **{tagdb.EXTRA_OVERFLOW: "x"}),
            row("user", "", primary="meta/quality"),
            row("user", "g tag", primary="unknown"),
        ])
        expected = [
            "파일 이름(DB ID)", "primary 가 비었는데 rating", "primary 가 분류 체계에 없습니다: clothes/nope",
            "models 는", "모르는 칸입니다: primay", "값이 머리줄보다 많은", "이름이 비었습니다",
            "시스템 카테고리는 etc 만",
        ]
        self.assertEqual(len(errors), len(expected), errors)     # 모르는 칸은 DB 마다 한 번만
        for text in expected:
            self.assertTrue(any(text in e for e in errors), text)

    def test_real_data_is_valid(self):
        """저장소의 data/extra 가 빌드 검사를 통과하고, 파일마다 머리줄이 EXTRA_FIELDS 그대로다."""
        config = tagdb.load_config()
        rows = tagdb.read_extra()
        self.assertEqual(builder.extra_problems(rows, builder.category_ids(config["taxonomy"]),
                                                set(config["rules"].get("regions", ()))), [])
        files = tagdb.extra_files()
        self.assertEqual([p.stem for p in files][:3], list(tagdb.EXTRA_ORDER))
        for path in files:
            with open(path, encoding="utf-8-sig", newline="") as f:
                self.assertEqual(tuple(next(csv.reader(f))), tagdb.EXTRA_FIELDS, path.name)
        self.assertTrue(any(r["db"] == "model" and r["name"] == "masterpiece" for r in rows))


class CoreTests(unittest.TestCase):
    def test_report_has_db(self):
        p = core.TagPack(build_with([])[0])
        tokens = {r["text"]: r for r in core.organize("masterpiece, smile", p).report["tokens"]}
        self.assertEqual(tokens["masterpiece"]["db"], "model")
        self.assertNotIn("db", tokens["smile"])

    def test_old_pack_without_extra(self):
        pack = build_with([])[0]
        del pack["extra"]
        p = core.TagPack(pack)
        self.assertEqual(p.extra_dbs, ())
        self.assertEqual((p.get("masterpiece").type, p.get("masterpiece").db), (core.TYPE_CUSTOM, ""))


if __name__ == "__main__":
    unittest.main()
