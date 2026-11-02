"""분류 DB 도구 테스트 — 빌드, 델타 반영, 통계, overlay 입출력."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import build, builder, core, overlay_rows, source_tags, tagdb
from tools import batch, calibration, evidence, fix, importer, lint, review, stats
from tools.tagdb import SourceTag


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.pack, self.warnings = build()
        self.p = core.TagPack(self.pack)

    def test_type_auto_classification(self):
        self.assertEqual(self.p.get("hatsune miku").primary, "subject/character")
        self.assertEqual(self.p.get("hatsune miku").status, "auto")
        self.assertEqual(self.p.get("vocaloid").primary, "series")
        self.assertEqual(self.p.get("wlop").primary, "style/artist")
        self.assertEqual(self.p.get("unclassified thing").primary, "etc")

    def test_overlay_wins_over_type(self):
        self.assertEqual(self.p.get("highres").primary, "meta/quality")

    def test_kaomoji_and_aliases(self):
        self.assertIn("^_^", self.p.kaomoji)
        self.assertEqual(self.p.resolve("back view")[0].key, "from behind")
        self.assertEqual(self.p.resolve("character sheet")[0].key, "reference sheet")

    def test_custom_display(self):
        info = self.p.get("score 9")
        self.assertEqual((info.display, info.type, info.primary), ("score_9", core.TYPE_CUSTOM, "meta/quality"))

    def test_invalid_overlay_is_rejected(self):
        bad = overlay_rows({"smile": {**overlay_rows()["smile"], "primary": "clothes/nope", "rating": "x"}})
        with self.assertRaises(builder.BuildError) as cm:
            build(bad)
        self.assertEqual(len(cm.exception.errors), 2)

    def test_broken_overlay_name_is_rejected(self):
        # 엑셀이 =_= 를 #NAME? 로, 9/11 을 날짜로 바꾼 경우처럼 원본에 없는 이름은 빌드를 막는다
        bad = overlay_rows({"#NAME?": {**overlay_rows()["smile"], "name": "#NAME?"}})
        with self.assertRaises(builder.BuildError) as cm:
            build(bad)
        self.assertTrue(any("#NAME?" in e for e in cm.exception.errors))
        self.assertTrue(any("git checkout" in e for e in cm.exception.errors))
        gone = overlay_rows({"#NAME?": {**overlay_rows()["smile"], "name": "#NAME?", "missing_since": "2026.09"}})
        build(gone)                                     # import 가 사라졌다고 적은 행은 통과

    def test_etc_row_with_fields_is_rejected(self):
        bad = overlay_rows({"smile": {**overlay_rows()["smile"], "primary": "etc"}})
        with self.assertRaises(builder.BuildError) as cm:
            build(bad)
        self.assertIn("etc", cm.exception.errors[0])

    def test_version_is_deterministic(self):
        again, _ = build()
        self.assertEqual(again["version"], self.pack["version"])
        changed = overlay_rows({"smile": {**overlay_rows()["smile"], "rating": "s"}})
        self.assertNotEqual(build(changed)[0]["version"], self.pack["version"])

    def test_write_pack_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a.json.gz"), Path(tmp, "b.json.gz")
            builder.write_pack(self.pack, a)
            builder.write_pack(build()[0], b)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_real_config_has_no_dangling_rule_tags_in_fixture_scope(self):
        # 실제 rules.json 의 태그 중 픽스처에 없는 것은 경고로만 남는다(오류 아님)
        self.assertTrue(all("rules." in w for w in self.warnings))


class DeltaTests(unittest.TestCase):
    OLD = [SourceTag("a_tag", 0, 100, ()), SourceTag("old_name", 0, 90, ()), SourceTag("gone", 0, 80, ()),
           SourceTag("shifty", 0, 70, ()), SourceTag("back_again", 0, 60, ())]
    NEW = [SourceTag("a_tag", 0, 120, ()), SourceTag("new_name", 0, 95, ("old_name",)),
           SourceTag("shifty", 4, 75, ()), SourceTag("fresh", 0, 50, ()), SourceTag("back_again", 0, 65, ())]

    def test_compute_delta(self):
        d = importer.compute_delta(self.OLD, self.NEW)
        self.assertEqual(d.added, ["new_name", "fresh"])
        self.assertEqual(d.removed, ["old_name", "gone"])
        self.assertEqual(d.type_changed, [("shifty", 0, 4)])
        self.assertEqual(d.renamed, [("old_name", "new_name")])

    def test_apply_delta(self):
        ov = {
            "old_name": {"name": "old_name", "primary": "body/hair", "status": "reviewed", "note": ""},
            "gone": {"name": "gone", "primary": "object", "status": "llm", "note": ""},
            "shifty": {"name": "shifty", "primary": "action", "status": "reviewed", "note": ""},
            "back_again": {"name": "back_again", "primary": "object", "status": "llm", "missing_since": "2026-04-01"},
        }
        delta = importer.compute_delta(self.OLD, self.NEW)
        delta.added.append("back_again")      # 이전 원본에서 사라졌다가 다시 나타난 경우
        changes = importer.apply_delta(ov, delta, "2026-11-01")
        self.assertEqual(ov["shifty"]["status"], "conflict")
        self.assertIn("type 0->4", ov["shifty"]["note"])
        self.assertEqual(ov["gone"]["missing_since"], "2026-11-01")
        self.assertEqual(ov["old_name"]["missing_since"], "2026-11-01")
        self.assertEqual((ov["new_name"]["primary"], ov["new_name"]["status"]), ("body/hair", "conflict"))
        self.assertEqual(ov["back_again"]["missing_since"], "")
        self.assertEqual(len(changes), 5)

    def test_overlay_delta_without_previous_source(self):
        # 이전 원본 파일이 없어도 사라진 행에 missing_since 를 적어야 빌드 검사를 통과한다
        ov = {
            "old_name": {"name": "old_name", "primary": "body/hair", "status": "reviewed", "note": ""},
            "gone": {"name": "gone", "primary": "object", "status": "llm", "note": ""},
            "a_tag": {"name": "a_tag", "primary": "object", "status": "llm", "note": ""},
            "back_again": {"name": "back_again", "primary": "object", "status": "llm", "missing_since": "2026-04-01"},
        }
        delta = importer.overlay_delta(ov, self.NEW)
        self.assertEqual((delta.removed, delta.added, delta.type_changed), (["gone", "old_name"], ["back_again"], []))
        self.assertEqual(delta.renamed, [("old_name", "new_name")])
        importer.apply_delta(ov, delta, "2026-11-01")
        self.assertEqual((ov["gone"]["missing_since"], ov["back_again"]["missing_since"]), ("2026-11-01", ""))
        self.assertEqual(ov["new_name"]["status"], "conflict")
        self.assertEqual(ov["a_tag"].get("missing_since", ""), "")


class StatsTests(unittest.TestCase):
    def test_coverage_counts_usage(self):
        src = [SourceTag("a", 0, 60, ()), SourceTag("b", 0, 30, ()), SourceTag("c", 0, 10, ()),
               SourceTag("artist", 1, 999, ())]
        ov = {"a": {"primary": "object", "status": "reviewed"}, "c": {"primary": "object", "status": "llm"}}
        rows = {r["milestone"]: r for r in stats.coverage(src, ov)}
        self.assertAlmostEqual(rows["M5"]["classified"]["usage"], 0.7)
        self.assertAlmostEqual(rows["M5"]["reviewed"]["usage"], 0.6)
        self.assertEqual(rows["M5"]["top"], 3)

    def test_etc_counted_apart(self):
        src = [SourceTag("a", 0, 60, ()), SourceTag("b", 0, 40, ())]
        ov = {"a": {"primary": "object", "status": "accepted"}, "b": {"primary": "etc", "status": "accepted"}}
        m5 = {r["milestone"]: r for r in stats.coverage(src, ov)}["M5"]
        self.assertAlmostEqual(m5["confirmed"]["usage"], 0.6)
        self.assertEqual((m5["classified"]["tags"], m5["etc"]["tags"]), (1, 1))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.source = source_tags()
        self.config = tagdb.load_config()

    def result(self, name, primary="body/hair", **kw):
        row = {"name": name, "primary": primary, "secondary": "", "rating": "g", "regions": "head",
               "identity": "1", "confidence": "0.9", "note": "", "_where": "t.csv:2"}
        row.update(kw)
        return row

    def test_pending_skips_classified(self):
        ov = {"1girl": {"primary": "subject/count", "status": "llm"}}
        names = [t.name for t in batch.pending_general(self.source, ov, 3)]
        self.assertEqual(names, ["solo", "long_hair"])

    def test_apply_writes_llm_rows(self):
        ov = {}
        n, skipped, errors = batch.apply_results([self.result("long hair")], ov, self.source, self.config, "claude:t")
        self.assertEqual((n, skipped, errors), (1, [], []))
        self.assertEqual((ov["long_hair"]["status"], ov["long_hair"]["by"]), ("llm", "claude:t"))

    def test_etc_rows(self):
        ov = {}
        ok = self.result("solo", primary="etc", rating="", regions="", identity="", note="의미 불확실")
        n, _, errors = batch.apply_results([ok], ov, self.source, self.config, "x")
        self.assertEqual((n, errors, ov["solo"]["primary"]), (1, [], "etc"))
        bad = [self.result("solo", primary="etc"), self.result("long_hair", primary="unknown", rating="",
                                                                regions="", identity="")]
        _, _, errors = batch.apply_results(bad, {}, self.source, self.config, "x")
        self.assertEqual(len(errors), 2)
        self.assertIn("rating", errors[0])

    def test_group_by_head(self):
        tags = [SourceTag(n, 0, 1, ()) for n in ("red_skirt", "miku_(cosplay)", "skirt", "bikini", "a_(cosplay)")]
        self.assertEqual([t.name for t in batch.group_by_head(tags)],
                         ["a_(cosplay)", "miku_(cosplay)", "bikini", "red_skirt", "skirt"])
        self.assertEqual(batch.head_key("cipher_(honkai:_star_rail)_(cosplay)"), "(cosplay)")

    def test_apply_rejects_everything_on_error(self):
        ov = {}
        rows = [self.result("long_hair"), self.result("solo", primary="clothes/nope"), self.result("nope_tag")]
        n, _, errors = batch.apply_results(rows, ov, self.source, self.config, "x")
        self.assertEqual((n, len(errors), ov), (0, 2, {}))

    def test_apply_never_overwrites_reviewed(self):
        ov = {"long_hair": {"name": "long_hair", "primary": "body/hair", "status": "reviewed"}}
        n, skipped, _ = batch.apply_results([self.result("long_hair", primary="object")], ov, self.source,
                                            self.config, "x")
        self.assertEqual((n, ov["long_hair"]["primary"]), (0, "body/hair"))
        self.assertEqual(len(skipped), 1)

    def test_apply_never_overwrites_accepted(self):
        ov = {"long_hair": {"name": "long_hair", "primary": "body/hair", "status": "accepted"}}
        n, skipped, _ = batch.apply_results([self.result("long_hair", primary="object")], ov, self.source,
                                            self.config, "x")
        self.assertEqual((n, ov["long_hair"]["primary"], len(skipped)), (0, "body/hair", 1))

    def test_accept_marks_llm_and_seed_only(self):
        ov = {
            "1girl": {"primary": "subject/count", "status": "llm"},
            "solo": {"primary": "subject/count", "status": "reviewed"},
            "long_hair": {"primary": "body/hair", "status": "seed"},
            "smile": {"primary": "action/expression", "status": "conflict"},
            "blush": {"primary": "", "status": "llm"},
        }
        done = review.accept(self.source, ov)
        self.assertEqual(sorted(done), ["1girl", "long_hair"])
        self.assertEqual([ov[n]["status"] for n in ("solo", "smile", "blush")], ["reviewed", "conflict", "llm"])

    def test_accept_top_limits_scope(self):
        ov = {"1girl": {"primary": "subject/count", "status": "llm"},
              "tree": {"primary": "background/scenery", "status": "llm"}}
        self.assertEqual(review.accept(self.source, ov, top=3), ["1girl"])
        self.assertEqual(ov["tree"]["status"], "llm")

    def test_apply_rejects_duplicates(self):
        _, _, errors = batch.apply_results([self.result("solo"), self.result("solo")], {}, self.source,
                                           self.config, "x")
        self.assertEqual(len(errors), 1)

    def test_held_etc_skips_reviewed_and_classified(self):
        ov = {"solo": {"primary": "etc", "status": "accepted"}, "long_hair": {"primary": "etc", "status": "reviewed"},
              "smile": {"primary": "action/expression", "status": "accepted"}, "blush": {"primary": "etc", "status": "llm"},
              "hatsune_miku": {"primary": "etc", "status": "accepted"}}
        self.assertEqual([t.name for t in batch.held_etc(self.source, ov)], ["solo", "blush"])

    def test_reclassify_etc_reopens_only_accepted_etc(self):
        ov = {"solo": {"name": "solo", "primary": "etc", "status": "accepted", "note": "의미 불확실"},
              "long_hair": {"name": "long_hair", "primary": "etc", "status": "reviewed"},
              "smile": {"name": "smile", "primary": "action/expression", "status": "accepted"}}
        rows = [self.result("solo", primary="subject/count", rating="", regions="", identity=""),
                self.result("long_hair"), self.result("smile", primary="object", regions="", identity="")]
        n, skipped, errors = batch.apply_results(rows, ov, self.source, self.config, "x")
        self.assertEqual((n, len(skipped)), (0, 3))       # 플래그 없이는 그대로 잠김
        n, skipped, errors = batch.apply_results(rows, ov, self.source, self.config, "x", reclassify_etc=True)
        self.assertEqual((n, errors), (1, []))
        self.assertEqual((ov["solo"]["primary"], ov["solo"]["status"]), ("subject/count", "llm"))
        self.assertEqual((ov["long_hair"]["primary"], ov["smile"]["primary"]), ("etc", "action/expression"))
        self.assertEqual(sorted(s.split()[0] for s in skipped), ["long_hair", "smile"])

    def test_export_with_evidence_columns(self):
        tags = [t for t in self.source if t.name in ("solo", "long_hair")]
        cache = {"solo": {"wiki": "A [[person|character]] alone.", "other_names": ["ソロ"], "implies": [],
                          "related": [["1girl", 0, 0.9], ["bench", 0, 0.5], ["vocaloid", 3, 0.3]]}}
        with tempfile.TemporaryDirectory() as d:
            [path] = batch.export_batches(tags, Path(d), "E", 10, cache)
            import csv
            with open(path, encoding="utf-8", newline="") as f:
                rows = {r["name"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["solo"]["wiki"], "A character alone.")
        self.assertEqual((rows["solo"]["related"], rows["solo"]["related_series"]), ("bench:0.50", "vocaloid:0.30"))
        self.assertEqual((rows["long_hair"]["wiki"], rows["long_hair"]["related"]), ("?", "?"))


class EvidenceTests(unittest.TestCase):
    def fake(self, wiki=None, impl=None, related=None, fail=(), missing=()):
        calls = []

        def get(path, params, not_found_ok=False):
            calls.append((path, dict(params)))
            if path in fail:
                raise evidence.FetchError(path)
            if path in missing:
                return None
            if path == "/wiki_pages.json":
                titles = params["search[title_comma]"].split(",")
                return [{"title": t, "body": wiki[t], "other_names": ["x"]} for t in titles if t in (wiki or {})]
            if path.startswith("/wiki_pages/"):
                assert not_found_ok
                return None
            if path == "/tag_implications.json":
                names = (params["search[antecedent_name_comma]"].split(",") if "search[antecedent_name_comma]" in params
                         else [params["search[antecedent_name]"]])
                return [{"antecedent_name": a, "consequent_name": c} for a, c in (impl or ()) if a in names]
            if path == "/related_tag.json":
                q = params["query"]
                return {"related_tags": [{"tag": {"name": q, "category": 0}, "frequency": 1.0}] + [
                    {"tag": {"name": n, "category": c}, "frequency": f} for n, c, f in (related or {}).get(q, ())]}
            raise AssertionError(path)
        return get, calls

    def test_fetch_fills_cache_and_resumes(self):
        get, calls = self.fake(wiki={"smersh": "chest rig"},
                               impl=[("smersh", "load_bearing_vest"), ("a,b", "comma_parent")],
                               related={"smersh": [("gun", 0, 0.97)]})
        cache = {}
        stats = evidence.fetch(["smersh", "navigator", "a,b"], cache, delay=0, get=get)
        self.assertEqual(cache["smersh"]["wiki"], "chest rig")
        self.assertEqual(cache["smersh"]["implies"], ["load_bearing_vest"])
        self.assertEqual(cache["smersh"]["related"], [["gun", 0, 0.97]])      # 자기 자신은 뺀다
        self.assertIsNone(cache["navigator"]["wiki"])
        self.assertEqual(cache["navigator"]["implies"], [])
        self.assertIsNone(cache["a,b"]["wiki"])                               # 쉼표 이름은 하나씩
        self.assertEqual(cache["a,b"]["implies"], ["comma_parent"])           # 정확히 일치하는 검색으로
        self.assertEqual((stats["wiki"], stats["related"], stats["failed"]), (3, 3, 0))
        calls.clear()
        evidence.fetch(["smersh", "navigator"], cache, delay=0, get=get)
        self.assertEqual(calls, [])                                           # 받은 칸은 다시 받지 않음

    def test_failure_leaves_field_for_retry(self):
        get, _ = self.fake(wiki={}, fail=("/related_tag.json",))
        cache = {}
        stats = evidence.fetch(["navigator"], cache, delay=0, get=get)
        self.assertEqual(stats["failed"], 1)
        self.assertNotIn("related", cache["navigator"])
        self.assertEqual(evidence.summarize(cache["navigator"])["related"], "?")

    def test_empty_list_response_is_failure_not_absence(self):
        get, _ = self.fake(missing=("/wiki_pages.json", "/tag_implications.json", "/related_tag.json"))
        cache = {}
        stats = evidence.fetch(["navigator"], cache, delay=0, get=get)
        self.assertEqual((stats["failed"], cache), (3, {}))

    def test_interrupt_keeps_what_was_fetched(self):
        base, _ = self.fake(wiki={"smersh": "chest rig"})

        def get(path, params, not_found_ok=False):
            if path == "/related_tag.json":
                raise KeyboardInterrupt
            return base(path, params, not_found_ok)
        saved = []
        cache = {}
        with self.assertRaises(KeyboardInterrupt):
            evidence.fetch(["smersh"], cache, delay=0, get=get, save=lambda: saved.append(dict(cache)))
        self.assertEqual(saved[-1]["smersh"]["wiki"], "chest rig")
        self.assertIn("implies", saved[-1]["smersh"])

    def test_clean_wiki(self):
        body = ('h4. Overview\r\nThe [[uniform|outfit]] of [[Idolmaster]] {{official_alternate_costume}}.\r\n'
                '* !post #123: [i]Haruka[/i]\r\n"Twitter":https://x.com/a')
        self.assertEqual(evidence.clean_wiki(body),
                         "Overview The outfit of Idolmaster official_alternate_costume. * Haruka Twitter")
        self.assertEqual(evidence.clean_wiki(None), "")

    def test_summarize_filters_noise(self):
        entry = {"wiki": "x" * 700, "other_names": [], "implies": ["a", "b"],
                 "related": [["solo", 0, 0.9], ["necktie", 0, 0.61], ["rare", 0, 0.05], ["airi", 4, 0.75]]}
        s = evidence.summarize(entry)
        self.assertTrue(s["wiki"].endswith("…") and len(s["wiki"]) == 601)
        self.assertEqual((s["implies"], s["related"], s["related_series"]), ("a|b", "necktie:0.61", "airi:0.75"))
        self.assertEqual(evidence.summarize(None)["wiki"], "?")

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "evidence.json"
            evidence.write_cache({"한글": {"wiki": None}}, path)
            self.assertEqual(evidence.read_cache(path), {"한글": {"wiki": None}})
            self.assertEqual(list(path.parent.iterdir()), [path])            # 임시 파일이 남지 않음


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.ov = {
            "long_hair": {"name": "long_hair", "primary": "body/hair", "rating": "g", "regions": "head",
                          "identity": "1", "status": "llm", "confidence": "0.6", "secondary": "", "note": ""},
            "smile": {"name": "smile", "primary": "object/symbol", "rating": "q", "regions": "",
                      "identity": "", "status": "llm", "confidence": "0.9", "secondary": "", "note": ""},
            "solo": {"name": "solo", "primary": "subject/count", "status": "reviewed"},
        }

    def test_rows_and_flags(self):
        sample = {"smile": ("expression", False)}
        rows = review.review_rows(source_tags(), self.ov, 100, sample=sample)
        flags = {r["name"]: r["flags"] for r in rows}
        self.assertEqual(flags, {"long_hair": "low_conf", "smile": "sample:expression sample:sfw"})

    def test_apply_review(self):
        rows = review.review_rows(source_tags(), self.ov, 100)
        rows[0]["approve"] = "y"                       # long_hair 승인
        rows[1].update(approve="y", primary="action/expression", rating="g", regions="face")  # smile 수정 후 승인
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "r.csv")
            review.write_review(rows, path)
            counts, errors = review.apply_review(path, self.ov, tagdb.load_config(), by="user:t")
        self.assertEqual((counts["approved"], errors), (2, []))
        self.assertEqual((self.ov["smile"]["primary"], self.ov["smile"]["status"]), ("action/expression", "reviewed"))
        self.assertEqual(self.ov["long_hair"]["by"], "user:t")

    def test_reject_and_bad_mark(self):
        rows = review.review_rows(source_tags(), self.ov, 100)
        rows[0]["approve"] = "n"
        rows[1]["approve"] = "maybe"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "r.csv")
            review.write_review(rows, path)
            _, errors = review.apply_review(path, self.ov, tagdb.load_config())
            self.assertEqual(len(errors), 1)
            self.assertEqual(self.ov["long_hair"]["status"], "llm")   # 오류가 있으면 아무것도 반영 안 함
            rows[1]["approve"] = ""
            review.write_review(rows, path)
            counts, errors = review.apply_review(path, self.ov, tagdb.load_config())
        self.assertEqual((counts["rejected"], counts["skipped"], errors), (1, 1, []))
        self.assertEqual(self.ov["long_hair"]["status"], "conflict")


class LintTests(unittest.TestCase):
    def test_neutral_modifier_pairs(self):
        ov = {
            "skirt": {"primary": "clothes/bottom", "rating": "g"},
            "blue_skirt": {"primary": "clothes/bottom", "rating": "g"},          # 일치
            "red_skirt": {"primary": "clothes/top", "rating": "g"},              # 분류 다름
            "black_skirt": {"primary": "clothes/bottom", "rating": "s"},         # 등급 다름
            "torn_skirt": {"primary": "clothes/state", "rating": "g"},           # 상태 수식어 → 비교 안 함
            "white_unknown": {"primary": "object", "rating": "g"},               # 기본 아이템 없음
        }
        issues = {name: diffs for name, _, diffs in lint.pair_issues(ov)}
        self.assertEqual(sorted(issues), ["black_skirt", "red_skirt"])
        self.assertIn("rating", issues["black_skirt"][0])


class CalibrationTests(unittest.TestCase):
    def test_pick_hides_answers(self):
        ov = overlay_rows()
        ov["smile"]["status"] = "llm"                  # 미확정은 뽑지 않음
        picked = calibration.pick(source_tags(), ov, 3)
        names = {t.name for t in picked}
        self.assertLessEqual(len(picked), 3)
        self.assertNotIn("smile", names)
        self.assertTrue(all(t.type == 0 for t in picked))
        with tempfile.TemporaryDirectory() as tmp:
            calibration.write_calibration(picked, ov, Path(tmp))
            cal = Path(tmp, "CAL.csv").read_text(encoding="utf-8")
            precedents = Path(tmp, "precedents.csv").read_text(encoding="utf-8")
        self.assertNotIn("primary", cal.splitlines()[0])
        for name in names:
            self.assertIn(name, cal)
            self.assertNotIn(f"\n{name},", precedents)

    def test_score(self):
        ov = overlay_rows()
        truth = [{k: ov[n][k] for k in ("name", "primary", "rating", "regions", "identity")}
                 for n in ("long_hair", "navel")]
        wrong = [dict(truth[0]), dict(truth[1], primary="clothes/top")]
        report = calibration.score({"w1": truth, "w2": wrong}, ov)
        self.assertEqual(report["workers"]["w1"]["primary"], 1.0)
        self.assertEqual(report["workers"]["w2"]["primary"], 0.5)
        self.assertEqual(report["agreement"]["all_same_primary"], 0.5)
        self.assertEqual(report["confusions"][0]["worker"], "clothes/top")
        self.assertIn("미달", calibration.format_report(report, 0.95, 0.9))


class FixTests(unittest.TestCase):
    def setUp(self):
        self.source = source_tags()
        self.config = tagdb.load_config()
        self.ov = overlay_rows()

    def run_fix(self, tokens, etc=False):
        names, updates = fix.split_args(tokens)
        return fix.apply_fix(self.ov, names, updates, self.source, self.config, "user:t", etc=etc)

    def test_split_args_keeps_kaomoji_names(self):
        self.assertEqual(fix.split_args(["=_=", "rating=s", "note=a=b", "regions="]),
                         (["=_="], {"rating": "s", "note": "a=b", "regions": ""}))

    def test_resolve_name(self):
        by_name = {t.name: t for t in self.source}
        aliases = fix.alias_index(self.source)
        self.assertEqual(fix.resolve_name("Long Hair", by_name, aliases)[0], "long_hair")
        self.assertEqual(fix.resolve_name(r"star \(symbol\)", by_name, aliases)[0], "star_(symbol)")
        name, hint = fix.resolve_name("wink", by_name, aliases)
        self.assertEqual(name, "one_eye_closed")
        self.assertIn("별칭", hint)
        self.assertEqual(fix.resolve_name("^_^", by_name, aliases)[0], "^_^")
        # 역슬래시가 이름의 일부인 태그는 입력 그대로 먼저 찾는다
        backslash = {r"\(^o^)/": SourceTag(r"\(^o^)/", 0, 30, ()), "(^o^)/": SourceTag("(^o^)/", 0, 5, ())}
        self.assertEqual(fix.resolve_name(r"\(^o^)/", backslash, {})[0], r"\(^o^)/")
        self.assertEqual(fix.resolve_name(r"\(^o^)/", {"(^o^)/": backslash["(^o^)/"]}, {})[0], "(^o^)/")

    def test_fix_locks_row_as_reviewed(self):
        self.ov["smile"]["note"] = "LLM 근거"
        changes, errors, _ = self.run_fix(["smile", "primary=action/gesture", "rating=s"])
        self.assertEqual(errors, [])
        row = self.ov["smile"]
        self.assertEqual((row["primary"], row["rating"], row["status"], row["by"]), ("action/gesture", "s", "reviewed", "user:t"))
        self.assertEqual(row["note"], "")              # primary 가 바뀌면 이전 note 는 지움
        self.assertEqual(changes[0][1]["primary"], "action/expression")

    def test_fix_new_row_and_etc(self):
        changes, errors, _ = self.run_fix(["unclassified thing", "note=뜻 모름"], etc=True)
        self.assertEqual(errors, [])
        row = self.ov["unclassified_thing"]
        self.assertEqual((row["primary"], row["rating"], row["note"], row["source"]), ("etc", "", "뜻 모름", "danbooru"))
        _, errors, _ = self.run_fix(["smile", "rating=s"], etc=True)
        self.assertTrue(errors)

    def test_fix_is_all_or_nothing(self):
        before = dict(self.ov["smile"])
        changes, errors, _ = self.run_fix(["smile", "no_such_tag", "rating=s"])
        self.assertEqual((changes, len(errors)), ([], 1))
        changes, errors, _ = self.run_fix(["smile", "blush", "primary=clothes/nope"])
        self.assertEqual((changes, len(errors)), ([], 2))
        self.assertEqual(self.ov["smile"], before)

    def test_fix_notes(self):
        _, _, notes = self.run_fix(["long_hair", "primary=series"])
        self.assertTrue(any("rating" in n for n in notes) and any("identity" in n for n in notes))
        self.assertTrue(any("regions" in n for n in notes))
        self.ov["smile"]["status"] = "reviewed"
        changes, _, notes = self.run_fix(["smile", "rating=g"])
        self.assertEqual(changes, [])
        self.assertIn("바뀐 것 없음", notes[-1])

    def test_find_rows(self):
        self.ov["unclassified_thing"] = {"name": "unclassified_thing", "primary": "etc", "note": "의미 불확실"}
        hair = [r["name"] for _, r in fix.find_rows(self.source, self.ov, primary="body/hair")]
        self.assertEqual(hair[:2], ["long_hair", "very_long_hair"])  # 사용량 순
        self.assertEqual([r["name"] for _, r in fix.find_rows(self.source, self.ov, primary="etc")],
                         ["unclassified_thing"])
        self.assertEqual([r["name"] for _, r in fix.find_rows(self.source, self.ov, grep="불확실")],
                         ["unclassified_thing"])
        self.assertTrue(all(r["primary"].startswith("body/")
                            for _, r in fix.find_rows(self.source, self.ov, primary="body")))

    def test_check_overlay(self):
        committed = {k: dict(v) for k, v in self.ov.items()}
        errors, info = fix.check_overlay(self.ov, self.source, self.config, committed)
        self.assertEqual(errors, [])
        del self.ov["smile"]
        self.ov["#NAME?"] = {**committed["blush"], "name": "#NAME?"}
        errors, info = fix.check_overlay(self.ov, self.source, self.config, committed)
        self.assertTrue(any("#NAME?" in e for e in errors))
        self.assertTrue(any("없어졌습니다" in e and "smile" in e for e in errors))
        self.assertIn("없어진 행 1", info[0])
        _, info = fix.check_overlay(self.ov, self.source, self.config, None)
        self.assertIn("비교하지 못했습니다", info[0])


class OverlayIOTests(unittest.TestCase):
    def test_roundtrip_sorted_with_bom(self):
        rows = overlay_rows()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "tags.csv")
            tagdb.write_overlay(rows, path)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", raw)
            back = tagdb.read_overlay(path)
            self.assertEqual(list(back), sorted(rows))
            self.assertEqual(back["navel"]["regions"], "front_torso")

    def test_damaged_overlay_is_reported(self):
        rows = overlay_rows()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "tags.csv")
            tagdb.write_overlay(rows, path)
            text = path.read_text(encoding="utf-8-sig")
            lines = text.splitlines(keepends=True)
            path.write_text(text + lines[1], encoding="utf-8-sig")          # 같은 행이 두 번
            with self.assertRaises(tagdb.OverlayError) as cm:
                tagdb.read_overlay(path)
            self.assertIn("겹칩니다", str(cm.exception))
            path.write_bytes((text + "x,danbooru,object,,g,,,llm,,,메모,\n").encode("cp949"))
            with self.assertRaises(tagdb.OverlayError) as cm:              # 엑셀의 "CSV(쉼표로 분리)" 저장
                tagdb.read_overlay(path)
            self.assertIn("UTF-8", str(cm.exception))
            path.write_text("name,primary\nsmile,object\n", encoding="utf-8")
            with self.assertRaises(tagdb.OverlayError):
                tagdb.read_overlay(path)

    def test_source_csv_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "d.csv")
            path.write_text('b,0,5,\na,0,9,"x,y"\n', encoding="utf-8")
            tags = tagdb.read_source_csv(path)
            self.assertEqual([(t.name, t.aliases) for t in tags], [("a", ("x", "y")), ("b", ())])


if __name__ == "__main__":
    unittest.main()
