"""e621 2단계 도구 테스트 — 배치 고르기·증거(db_export)·내보내기·반영·확정, e621.csv 손 수정, e621 구도 gate."""

from __future__ import annotations

import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from fixtures import EXTRA, builder, core, tagdb
from test_e621 import DEPRECATED, build, danbooru_overlay, danbooru_source, make_site, site_row
from tools import e621, fix


def select(site=None, kinds=tuple(e621.KINDS), top=0):
    return e621.select(danbooru_source(), danbooru_overlay(), list(EXTRA) + DEPRECATED, site or make_site(),
                       tagdb.load_config(), kinds, top)


class SelectTests(unittest.TestCase):
    def test_kinds(self):
        site = make_site(overlay={"fur": site_row("fur", primary="body/features"),
                                  "cute": site_row("cute", primary="action/expression", status="conflict")})
        kinds = {r["name"]: r["kind"] for r in select(site, top=1)}
        self.assertEqual(kinds["cute"], "conflict")
        self.assertEqual(kinds["back_view"], "suspect")         # 별칭 대상이 e621 에도 있음
        self.assertEqual(kinds["headwear"], "suspect")          # 대상이 meta
        self.assertEqual(kinds["wink"], "auto")
        self.assertEqual(kinds["half-length_portrait"], "meta")
        self.assertEqual(kinds["big_tree"], "hint")
        self.assertEqual(kinds["anthro"], "top")                # general 사용량 1위
        self.assertNotIn("fur", kinds)                          # 이미 e621.csv 로 분류
        self.assertNotIn("solo", kinds)                         # Danbooru 와 같은 이름
        self.assertNotIn("domestic_cat", kinds)                 # 종은 종류 자동
        self.assertNotIn("masterpiece", kinds)                  # 모델 DB 가 이긴 이름

    def test_context_columns(self):
        rows = {r["name"]: r for r in select()}
        self.assertIn("별칭 대상 from behind=composition/angle", rows["back_view"]["danbooru"])
        self.assertIn("(e621 에도 따로 있음)", rows["back_view"]["danbooru"])
        self.assertIn("tree=background/scenery", rows["big_tree"]["danbooru"])
        self.assertTrue(rows["wink"]["current"].startswith("action/expression"))
        self.assertNotIn("anthro", rows)                        # top 을 주지 않으면 고르지 않음

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            select(kinds=["nope"])


class EvidenceExportTests(unittest.TestCase):
    def test_load_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for date in ("2024-12-24", "2025-05-01"):
                with gzip.open(folder / f"wiki_pages-{date}.csv.gz", "wt", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["id", "created_at", "updated_at", "title", "body", "creator_id", "updater_id",
                                "is_locked"])
                    w.writerow([1, "", "", "wink", f"thumb #5 [color=red]Closing[/color] one [[eye]] ({date})",
                                "", "", "f"])
            with open(folder / "tag_implications-2025-05-01.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "antecedent_name", "consequent_name", "created_at", "status"])
                w.writerow([1, "wink", "eyes_closed", "", "active"])
                w.writerow([2, "big_tree", "wink", "", "active"])
                w.writerow([3, "back_view", "wink", "", "deleted"])
            wiki, impl = e621.export_files(folder)
            self.assertEqual(wiki.name, "wiki_pages-2025-05-01.csv.gz")
            ev = e621.load_evidence({"wink", "back_view"}, wiki, impl)
            self.assertEqual(ev["wink"]["implies"], ["eyes_closed"])
            self.assertEqual(ev["wink"]["implied_by"], ["big_tree"])     # deleted 는 빼고
            self.assertIsNone(ev["back_view"]["wiki"])
            s = e621.summarize(ev["wink"])
            self.assertEqual(s["wiki"], "Closing one eye (2025-05-01)")
            self.assertEqual(e621.summarize(None)["wiki"], "?")
            paths = e621.export_batches(select(), folder / "out", "T", 3, ev, group=True)
            with open(paths[0], encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0]), list(e621.SITE_BATCH_FIELDS))
        self.assertEqual(rows[0]["kind"], "suspect")                    # group: 종류 순(conflict 없음)


class ApplyTests(unittest.TestCase):
    def apply(self, results, site=None):
        return e621.apply_results(
            [dict(r, _where="t.csv") for r in results], danbooru_source(), danbooru_overlay(),
            list(EXTRA) + DEPRECATED, site or make_site(), tagdb.load_config(), "claude:test")

    def test_classification_and_same_as(self):
        rows, n, skipped, errors = self.apply([
            {"name": "back_view", "primary": "composition/angle", "rating": "g", "confidence": "0.8"},
            {"name": "big tree", "same_as": "tree", "note": "같은 뜻"},
        ])
        self.assertEqual((n, errors), (2, []))
        self.assertEqual((rows["back_view"]["primary"], rows["back_view"]["status"], rows["back_view"]["source"]),
                         ("composition/angle", "llm", "e621"))
        self.assertEqual((rows["big_tree"]["same_as"], rows["big_tree"]["primary"]), ("tree", ""))
        info = core.TagPack(build(site=make_site(overlay=rows))[0]).get("big tree")
        self.assertEqual(info.primary, "background/scenery")
        self.assertEqual(e621.accept(rows), ["back_view", "big_tree"] if "back_view" < "big_tree" else ["big_tree",
                                                                                                         "back_view"])
        self.assertEqual(rows["back_view"]["status"], "accepted")

    def test_errors_leave_rows_unchanged(self):
        site = make_site()
        before = {k: dict(v) for k, v in site.overlay.items()}
        for bad in ({"name": "solo", "primary": "subject/count"},              # Danbooru 이름
                    {"name": "nope", "primary": "object/prop"},                 # 원본에 없음
                    {"name": "wink", "same_as": "smile", "primary": "action/expression"},
                    {"name": "wink", "primary": "no/such"},
                    {"name": "wink", "same_as": "no such tag"},                 # 빌드 검사
                    {"name": "wink", "same_as": "one_eye_closed", "confidence": "oops"}):
            rows, n, _, errors = self.apply([bad], site)
            self.assertTrue(errors, bad)
            self.assertEqual((n, rows), (0, before))

    def test_locked_rows_are_skipped(self):
        site = make_site(overlay={"fur": site_row("fur", primary="body/features", status="reviewed"),
                                  "cute": site_row("cute", primary="action/expression", status="conflict")})
        rows, n, skipped, errors = self.apply([{"name": "fur", "primary": "object/prop"},
                                               {"name": "cute", "primary": "action/expression", "rating": "g"}], site)
        self.assertEqual((n, errors), (1, []))
        self.assertEqual(rows["fur"]["primary"], "body/features")
        self.assertEqual(rows["cute"]["status"], "llm")
        self.assertTrue(any("fur" in s for s in skipped))


class FixSiteTests(unittest.TestCase):
    def test_new_row_and_same_as(self):
        rows = {"butt": site_row("butt", same_as="thighs", status="accepted")}
        config = tagdb.load_config()
        changes, errors, notes = fix.apply_fix_site(rows, ["wink"], {"primary": "action/expression", "rating": "g"},
                                                    config, "user:test", "e621")
        self.assertEqual(errors, [])
        self.assertEqual((rows["wink"]["primary"], rows["wink"]["status"], rows["wink"]["source"]),
                         ("action/expression", "reviewed", "e621"))
        fix.apply_fix_site(rows, ["butt"], {"note": "확인함"}, config, "user:test", "e621")
        self.assertEqual((rows["butt"]["same_as"], rows["butt"]["note"], rows["butt"]["status"]),
                         ("thighs", "확인함", "reviewed"))
        changes, errors, notes = fix.apply_fix_site(rows, ["butt"], {"primary": "body/lower", "rating": "g"}, config,
                                                    "user:test", "e621")
        self.assertEqual((rows["butt"]["same_as"], rows["butt"]["primary"]), ("", "body/lower"))
        self.assertTrue(any("same_as" in n for n in notes))
        _, errors, _ = fix.apply_fix_site(rows, ["fur"], {"models": "nai"}, config, "user:test", "e621")
        self.assertTrue(errors)


class FixLookupTests(unittest.TestCase):
    """fix 가 팩과 같은 순서로 찾는지(CLI 를 --dry-run 으로)."""

    def run_fix(self, argv, extra_rows=None, site=None):
        import contextlib
        import io
        from unittest import mock

        import tagorg
        out = io.StringIO()
        with mock.patch.object(tagdb, "current_source", return_value=(Path("d.csv"), {"file": "d.csv"})),                 mock.patch.object(tagdb, "read_source_csv", return_value=danbooru_source()),                 mock.patch.object(tagdb, "read_overlay", return_value=danbooru_overlay()),                 mock.patch.object(tagdb, "read_extra", return_value=list(EXTRA) + list(extra_rows or DEPRECATED)),                 mock.patch.object(tagdb, "load_site", return_value=site or make_site()),                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tagorg.main(["fix"] + argv + ["--dry-run"])
        return code, out.getvalue()

    def test_current_e621_name_beats_missing_tags_csv_row(self):
        # 1: gone_tag 은 tags.csv 에 missing_since 행이 남았지만 지금은 e621 태그 — e621.csv 를 고친다
        code, out = self.run_fix(["gone_tag", "primary=body/features"])
        self.assertEqual(code, 0, out)
        self.assertIn("gone_tag (e621.csv)", out)

    def test_alias_order_front_extra_then_e621_then_deprecated(self):
        # 2: 같은 별칭을 폐기·내 태그·e621 이 다 가지면 빌드는 내 태그를 고른다
        rows = [{"db": "deprecated", "name": "old", "primary": "object/prop", "aliases": "shared"},
                {"db": "user", "name": "custom", "primary": "object/prop", "aliases": "shared"}]
        from test_e621 import E6
        site = make_site(rows=E6 + [("new_tag", 0, 10, ("shared",))])
        code, out = self.run_fix(["shared", "primary=object/symbol"], extra_rows=rows, site=site)
        self.assertEqual(code, 0, out)
        self.assertIn("custom (확장 DB user)", out)

    def test_e621_alias_of_shared_name(self):
        # 3: alone 은 e621 solo 의 별칭이고 solo 는 Danbooru 와 같은 이름 — tags.csv 의 solo 를 고친다
        code, out = self.run_fix(["alone", "primary=subject/count", "note=x"])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("solo\n"), out)


class GateTests(unittest.TestCase):
    def test_e621_framing_gates(self):
        pack = core.TagPack(build()[0])
        opts = core.Options()
        out = core.organize("portrait, full-length portrait, shoes, smile", pack, opts)
        self.assertIn("shoes", out.text)                         # 전신 초상이 함께면 portrait 가 발을 가리지 않음
        out = core.organize("half-length portrait, shoes, smile", pack, opts)
        self.assertNotIn("shoes", out.text)
        self.assertIn("rule:framing", out.removed_text)


if __name__ == "__main__":
    unittest.main()
