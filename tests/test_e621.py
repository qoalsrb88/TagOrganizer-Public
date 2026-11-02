"""두 번째 원본(e621) 테스트 — 원본 목록·가져오기, 빌드 층 순서(이름 > 별칭, Danbooru > 모델·내 태그 > e621 > 폐기),
자동 분류·자동 same_as, e621.csv 검사, 코어의 site 표시, 폐기 DB 행 옮기기(docs/e621.md)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import EXTRA, SOURCE, builder, core, overlay_rows, source_tags, tagdb
from tools import e621, extra, importer
from tools.tagdb import Site, SourceTag

META = {"file": "fixture.csv", "date": "2026-10-01", "rows": len(SOURCE)}

# (이름, e621 종류, 사용량, 별칭)
E6 = [
    ("anthro", 0, 9000, ("anthromorph",)),
    ("solo", 0, 5000, ("alone",)),                     # Danbooru 와 같은 이름 — tags.csv 분류
    ("fur", 0, 1000, ()),                               # e621.csv 로 분류
    ("butt", 0, 900, ()),                               # e621.csv same_as → thighs
    ("wink", 0, 800, ()),                               # Danbooru 별칭(→ one_eye_closed) — 자동 same_as
    ("back_view", 0, 300, ()),                          # Danbooru 별칭(→ from_behind) — 대상이 e621 에도 있어 보류
    ("from_behind", 0, 100, ()),
    ("headwear", 0, 120, ()),                           # Danbooru 별칭인데 대상이 meta(요청 태그) — 보류
    ("character_sheet", 5, 110, ()),                    # 종은 Danbooru 별칭이어도 종류로
    ("domestic_cat", 5, 700, ("house_cat",)),
    ("cute", 0, 500, ()),                               # 폐기 DB 와 겹침 — e621 이 이김
    ("masterpiece", 0, 40, ()),                         # 모델 DB 와 겹침 — 모델 DB 가 이김
    ("some_artist", 1, 50, ()), ("some_char", 4, 60, ()), ("some_series", 3, 70, ()),
    ("half-length_portrait", 7, 90, ("upper_body", "half_body")),
    ("contributor_x", 2, 30, ()), ("lore_x", 8, 30, ()),
    ("o_o", 0, 20, ()),
    ("gone_tag", 0, 10, ()),                            # Danbooru 에서 사라진 공유 이름 — tags.csv 분류가 남음
    ("full-length_portrait", 7, 85, ()),                # 구도 gate(full body 와 같은 줄)
    ("big_tree", 0, 60, ("tree",)),                     # e621 별칭에 Danbooru 이름 — same_as 후보(hint)
]


def e6_tags(rows=E6):
    return sorted((SourceTag(n, t, c, a) for n, t, c, a in rows), key=lambda s: -s.count)


def site_row(name, **fields):
    row = {k: "" for k in tagdb.SITE_FIELDS}
    row.update({"name": name, "source": "e621", "status": "reviewed", **fields})
    return row


def make_site(overlay=None, rows=E6):
    ov = {"fur": site_row("fur", primary="body/features", rating="g", identity="1"),
          "butt": site_row("butt", same_as="thighs")} if overlay is None else overlay
    return Site("e621", e6_tags(rows), {"file": "e621_fixture.csv", "date": "2026-10-01", "rows": len(rows)}, ov)


def danbooru_source():
    # 요청 태그(meta 로 분류)와 그 별칭 headwear 를 더한다
    return source_tags() + [SourceTag("headwear_request", 0, 50, ("headwear",))]


def danbooru_overlay():
    rows = overlay_rows({
        "headwear_request": {"name": "headwear_request", "source": "danbooru", "primary": "meta/technical",
                             "status": "reviewed"},
        "gone_tag": {"name": "gone_tag", "source": "danbooru", "primary": "object/prop", "rating": "g",
                     "status": "reviewed", "missing_since": "2026-11-01"},
    })
    return rows


DEPRECATED = [{"db": "deprecated", "name": "cute", "primary": "action/expression", "rating": "g",
               "status": "accepted", "use_instead": "smile"},
              {"db": "deprecated", "name": "old thing", "primary": "object/prop", "status": "accepted"}]


def build(site=None, extra_rows=None, overlay=None):
    pack, warnings = builder.build_pack(
        danbooru_source(), META, overlay if overlay is not None else danbooru_overlay(),
        list(EXTRA) + list(DEPRECATED if extra_rows is None else extra_rows), tagdb.load_config(),
        make_site() if site is None else site)
    return pack, warnings


def errors_of(**kw):
    try:
        build(**kw)
    except builder.BuildError as e:
        return e.errors
    raise AssertionError("BuildError 가 나야 합니다")


class BuildLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, cls.warnings = build()
        cls.data = data
        cls.pack = core.TagPack(data)

    def info(self, text):
        return self.pack.resolve(core.normalize_key(text, self.pack.kaomoji))

    def test_shared_name_uses_danbooru_row(self):
        info, via = self.info("solo")
        self.assertEqual((info.primary, info.site, info.count, via), ("subject/count", "", 8000, False))
        self.assertEqual(self.info("alone")[0].key, "solo")          # e621 별칭도 함께 쓰는 행으로

    def test_type_auto(self):
        self.assertEqual(self.info("some_artist")[0].primary, "style/artist")
        self.assertEqual(self.info("some_char")[0].primary, "subject/character")
        self.assertEqual(self.info("some_series")[0].primary, "series")
        cat = self.info("domestic_cat")[0]
        self.assertEqual((cat.primary, cat.identity, cat.type, cat.status, cat.site),
                         ("subject/species", True, 0, "auto", "e621"))
        self.assertEqual(self.info("house_cat")[0].key, "domestic cat")
        meta = self.info("half-length_portrait")[0]
        self.assertEqual((meta.primary, meta.type), ("meta/technical", 5))
        self.assertEqual(self.info("anthro")[0].primary, "etc")      # general 은 분류 전 보류

    def test_contributor_and_lore_are_left_out(self):
        self.assertIsNone(self.info("contributor_x")[0])
        self.assertIsNone(self.info("lore_x")[0])

    def test_e621_name_shadows_danbooru_alias_and_keeps_spelling(self):
        info, via = self.info("wink")
        self.assertFalse(via)
        self.assertEqual((info.key, info.primary, info.status, info.site),
                         ("wink", "action/expression", "auto", "e621"))     # one_eye_closed 의 분류(자동 same_as)
        out = core.organize("wink, smile", self.pack, core.Options()).text
        self.assertIn("wink", out)
        self.assertNotIn("one eye closed", out)

    def test_auto_same_as_skips_suspects_meta_targets_and_species(self):
        self.assertEqual(self.info("back_view")[0].primary, "etc")         # from_behind 도 e621 에 있음
        self.assertEqual(self.info("headwear")[0].primary, "etc")          # 대상이 meta(요청 태그)
        sheet = self.info("character_sheet")[0]
        self.assertEqual((sheet.primary, sheet.site), ("subject/species", "e621"))

    def test_e621_alias_does_not_take_danbooru_names(self):
        info, via = self.info("upper_body")
        self.assertEqual((info.key, via, info.site), ("upper body", False, ""))
        self.assertEqual(self.info("half_body")[0].key, "half-length portrait")

    def test_model_db_beats_e621_and_e621_beats_deprecated(self):
        self.assertEqual(self.info("masterpiece")[0].db, "model")
        cute = self.info("cute")[0]
        self.assertEqual((cute.db, cute.site, cute.primary), ("", "e621", "etc"))
        self.assertTrue(any("deprecated" in w and "1개" in w and "take-deprecated" in w for w in self.warnings))
        self.assertEqual(self.info("old thing")[0].db, "deprecated")
        self.assertEqual(list(self.data["extra"]), ["model", "deprecated"])   # 읽은 순서 그대로

    def test_site_overlay_rows(self):
        fur = self.info("fur")[0]
        self.assertEqual((fur.primary, fur.status, fur.identity), ("body/features", "reviewed", True))
        butt = self.info("butt")[0]
        self.assertEqual((butt.primary, butt.regions, butt.key), ("body/lower", ("thighs",), "butt"))

    def test_missing_shared_name_keeps_tags_csv_class(self):
        gone = self.info("gone_tag")[0]
        self.assertEqual((gone.primary, gone.site), ("object/prop", "e621"))

    def test_kaomoji_from_e621(self):
        self.assertIn("o_o", self.data["kaomoji"])
        self.assertEqual(self.info("o_o")[0].key, "o_o")

    def test_pack_site_keys_and_report(self):
        self.assertEqual(set(self.data["site"]), {"e621"})
        self.assertIn("wink", self.data["site"]["e621"])
        self.assertNotIn("solo", self.data["site"]["e621"])
        self.assertEqual(self.data["site_sources"]["e621"]["file"], "e621_fixture.csv")
        self.assertEqual(self.pack.sites, ("e621",))
        report = core.organize("anthro, solo", self.pack, core.Options()).report
        sites = {r["text"]: r.get("site") for r in report["tokens"]}
        self.assertEqual(sites, {"anthro": "e621", "solo": None})

    def test_extra_same_as_can_point_at_e621_row(self):
        pack, _ = build(extra_rows=list(DEPRECATED) + [{"db": "user", "name": "my fur", "same_as": "fur"}])
        info, _ = core.TagPack(pack).resolve("my fur")
        self.assertEqual((info.primary, info.db), ("body/features", "user"))


class BuildCompatTests(unittest.TestCase):
    def test_without_site_pack_is_unchanged(self):
        config = tagdb.load_config()
        args = (source_tags(), META, overlay_rows(), list(EXTRA), config)
        pack, _ = builder.build_pack(*args)
        self.assertNotIn("site", pack)
        self.assertEqual(pack["version"], f"2026.10.01+{builder.content_hash(overlay_rows(), EXTRA, config)}")
        info, via = core.TagPack(pack).resolve("wink")
        self.assertEqual((info.key, via, info.site), ("one eye closed", True, ""))

    def test_without_site_extra_order_is_read_order(self):
        # 두 번째 원본이 없으면 확장 DB 를 읽은 순서(model → deprecated → user)대로 합친다 — 같은 별칭은
        # 앞 DB 가 이기고 팩의 tags 순서도 예전과 같다
        rows = list(EXTRA) + [
            {"db": "deprecated", "name": "old one", "primary": "object/prop", "aliases": "shared alias"},
            {"db": "user", "name": "my one", "primary": "object/prop", "aliases": "shared alias"},
        ]
        pack, _ = builder.build_pack(source_tags(), META, overlay_rows(), rows, tagdb.load_config())
        self.assertEqual(pack["aliases"]["shared alias"], "old one")
        keys = [r[0] for r in pack["tags"]][-4:]
        self.assertEqual(keys, ["masterpiece", "score 9", "old one", "my one"])

    def test_site_changes_version(self):
        a, _ = build()
        b, _ = build(site=make_site(overlay={}))
        self.assertNotEqual(a["version"], b["version"])

    def test_old_core_reads_new_pack(self):
        data, _ = build()
        data = dict(data)
        del data["site"]
        self.assertEqual(core.TagPack(data).get("anthro").site, "")


class SiteProblemTests(unittest.TestCase):
    def test_danbooru_name_in_site_overlay(self):
        errs = errors_of(site=make_site(overlay={"solo": site_row("solo", primary="subject/count")}))
        self.assertTrue(any("e621[solo]" in e and "tags.csv" in e for e in errs), errs)

    def test_unknown_name_unless_missing(self):
        errs = errors_of(site=make_site(overlay={"nope": site_row("nope", primary="object/prop")}))
        self.assertTrue(any("e621[nope]" in e and "원본 CSV에 없는" in e for e in errs), errs)
        build(site=make_site(overlay={"nope": site_row("nope", primary="object/prop", missing_since="2026-11-01")}))

    def test_same_as_rules(self):
        errs = errors_of(site=make_site(overlay={"butt": site_row("butt", same_as="thighs", primary="body/lower")}))
        self.assertTrue(any("same_as 행은 분류 칸" in e for e in errs), errs)
        errs = errors_of(site=make_site(overlay={"butt": site_row("butt", same_as="fur"),
                                                 "fur": site_row("fur", same_as="thighs")}))
        self.assertTrue(any("도 same_as 행입니다" in e for e in errs), errs)
        errs = errors_of(site=make_site(overlay={"butt": site_row("butt", same_as="no such tag")}))
        self.assertTrue(any("same_as 대상이" in e for e in errs), errs)

    def test_check_extra_reports_site_problems(self):
        errs, _ = builder.check_extra(danbooru_source(), danbooru_overlay(), list(EXTRA), tagdb.load_config(),
                                      make_site(overlay={"solo": site_row("solo", primary="subject/count")}))
        self.assertTrue(any("e621[solo]" in e for e in errs), errs)


class TakeDeprecatedTests(unittest.TestCase):
    def test_moves_classified_general_and_drops_the_rest(self):
        rows_in = list(EXTRA) + DEPRECATED + [
            {"db": "deprecated", "name": "domestic cat", "primary": "object/animal", "status": "accepted"},
            {"db": "deprecated", "name": "anthro", "primary": "etc", "status": "accepted"},
        ]
        rows, kept, lines = e621.take_deprecated(danbooru_source(), danbooru_overlay(), rows_in, make_site(),
                                                 tagdb.load_config(), today="2026-10-29")
        cute = rows["cute"]
        self.assertEqual((cute["primary"], cute["status"], cute["source"]), ("action/expression", "conflict", "e621"))
        self.assertIn("Danbooru 대체: smile", cute["note"])
        self.assertNotIn("domestic_cat", rows)       # 종은 종류 자동 분류
        self.assertNotIn("anthro", rows)             # 보류 행은 옮기지 않음
        self.assertIn("fur", rows)                   # 원래 e621.csv 행은 그대로
        self.assertEqual([r["name"] for r in kept["deprecated"]], ["old thing"])
        self.assertIn("옮김 1", lines[0])
        # 옮긴 뒤 빌드하면 경고 없이 같은 분류
        site = make_site(overlay=rows)
        pack, warnings = build(site=site, extra_rows=[dict(r, db="deprecated") for r in kept["deprecated"]])
        self.assertFalse([w for w in warnings if "take-deprecated" in w])
        self.assertEqual(core.TagPack(pack).get("cute").primary, "action/expression")


class ReviewFixTests(unittest.TestCase):
    """가져오기와 분류 경계 사례."""

    def test_take_deprecated_resolves_same_as(self):
        # 4: 폐기 행 cute.same_as=wink 를 그대로 옮기면 wink 가 자동 same_as 라 연쇄 오류 — 대상의 분류로 풀어 옮긴다
        rows_in = list(EXTRA) + [{"db": "deprecated", "name": "cute", "same_as": "wink", "status": "accepted"}]
        rows, kept, _ = e621.take_deprecated(danbooru_source(), danbooru_overlay(), rows_in, make_site(),
                                             tagdb.load_config(), today="2026-10-29")
        self.assertEqual((rows["cute"]["same_as"], rows["cute"]["primary"]), ("", "action/expression"))
        self.assertIn("same_as wink", rows["cute"]["note"])
        build(site=make_site(overlay=rows), extra_rows=[dict(r, db="deprecated") for r in kept["deprecated"]])

    def test_take_deprecated_keeps_references_to_other_deprecated_rows(self):
        # 5: 겹치지 않는 폐기 행을 가리키는 내 태그 same_as 가 있어도 멈추지 않는다
        rows_in = list(EXTRA) + DEPRECATED + [{"db": "user", "name": "my thing", "same_as": "old thing"}]
        rows, kept, _ = e621.take_deprecated(danbooru_source(), danbooru_overlay(), rows_in, make_site(),
                                             tagdb.load_config())
        self.assertIn("cute", rows)
        self.assertEqual([r["name"] for r in kept["deprecated"]], ["old thing"])

    def test_take_deprecated_checks_result_before_returning(self):
        # 4: 옮긴 결과로 빌드가 깨지면 쓰기 전에 멈춘다(BuildError)
        bad = make_site(overlay={"fur": site_row("fur", same_as="no such tag")})
        with self.assertRaises(builder.BuildError):
            e621.take_deprecated(danbooru_source(), danbooru_overlay(), list(EXTRA) + DEPRECATED, bad,
                                 tagdb.load_config())

    def test_rename_does_not_copy_self_same_as(self):
        # 3: a.same_as=b 인데 a 가 b 의 별칭이 되면 b.same_as=b 를 만들지 않는다
        ov = {"a": site_row("a", same_as="b")}
        delta = importer.Delta(removed=["a"], renamed=[("a", "b")])
        changes = importer.apply_delta(ov, delta, "2026-11-01")
        self.assertNotIn("b", ov)
        self.assertTrue(any("자기 자신" in c for c in changes), changes)

    def test_danbooru_names_needs_registered_file(self):
        # 6: 등록된 Danbooru CSV 가 없으면 빈 목록으로 넘어가지 않는다
        missing = tagdb.SourceMissing(Path("d.csv"), {"file": "d.csv"})
        with mock.patch.object(importer, "current_source", side_effect=missing):
            with self.assertRaises(tagdb.SourceMissing):
                importer._danbooru_names()
        with mock.patch.object(importer, "current_source", side_effect=FileNotFoundError("미등록")):
            self.assertEqual(importer._danbooru_names(), frozenset())


class TagcompleteTests(unittest.TestCase):
    def test_site_overlay_rows_are_exported(self):
        from tools import tagcomplete
        site = make_site()
        pack, _ = build(site=site)
        rows = tagcomplete.rows_for(pack, list(EXTRA) + DEPRECATED, site=site)
        by_name = {r[0]: r for r in rows}
        self.assertEqual(by_name["fur"][2], "신체/이종 특징 · e621 태그")
        self.assertIn("butt", by_name)                       # same_as 행은 대상의 분류
        self.assertNotIn("wink", by_name)                    # 자동 same_as(e621.csv 에 없음)
        self.assertNotIn("domestic_cat", by_name)            # 종류 자동 분류
        only = tagcomplete.rows_for(pack, list(EXTRA), dbs=["model"], site=site)
        self.assertNotIn("fur", {r[0] for r in only})


class ExtraAddTests(unittest.TestCase):
    def test_e621_names_and_aliases_are_known(self):
        _, skipped, added = extra.add_names(list(EXTRA), "user", ["anthro", "anthromorph", "brand new"],
                                            danbooru_source(), site=make_site())
        self.assertEqual(added, 1)
        self.assertTrue(any(s.startswith("anthro (e621)") for s in skipped), skipped)
        self.assertTrue(any("e621 별칭 → anthro" in s for s in skipped), skipped)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.source_dir = tmp / "source"
        self.source_dir.mkdir()
        self.site_overlay = tmp / "e621.csv"
        self.patches = [
            mock.patch.object(tagdb, "MANIFEST", self.source_dir / "sources.json"),
            mock.patch.object(tagdb, "SOURCE_DIR", self.source_dir),
            mock.patch.object(importer, "SOURCE_DIR", self.source_dir),
            mock.patch.object(importer, "REPORTS", tmp / "reports"),
            mock.patch.object(importer, "E621_OVERLAY", self.site_overlay),
            mock.patch.object(importer, "_danbooru_names", lambda: frozenset({"solo"})),
        ]
        for p in self.patches:
            p.start()
        tagdb.write_manifest({"current": "danbooru_2026-10-01.csv", "files": [
            {"file": "danbooru_2026-10-01.csv", "date": "2026-10-01"}], "note": "그대로 남아야 함"})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    @staticmethod
    def write_csv(path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            for name, type_, count, aliases in rows:
                f.write(f'{name},{type_},{count},"{",".join(aliases)}"\n')

    def test_import_refuses_another_sites_file_name(self):
        # 다른 원본(Danbooru)이 쓰는 파일 이름으로 가져오면 그 파일을 덮어쓴다 — 멈춘다
        danbooru = self.source_dir / "danbooru_2026-10-01.csv"
        danbooru.write_text("solo,0,1,\n", encoding="utf-8")
        e6 = Path(self._tmp.name, "e621_2026-10-01.csv")
        self.write_csv(e6, [("fur", 0, 10, ())])
        with self.assertRaises(ValueError):
            importer.import_source(e6, site="e621", name="danbooru_2026-10-01.csv")
        self.assertEqual(danbooru.read_text(encoding="utf-8"), "solo,0,1,\n")

    def test_no_site_in_manifest(self):
        self.assertIsNone(tagdb.site_source())
        self.assertIsNone(tagdb.load_site())

    def test_import_site_and_delta(self):
        first = Path(self._tmp.name, "e621_2026-10-01.csv")
        self.write_csv(first, [("fur", 0, 10, ()), ("old_name", 0, 5, ())])
        importer.import_source(first, site="e621")
        manifest = json.loads((self.source_dir / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["current"], "danbooru_2026-10-01.csv")
        self.assertEqual(manifest["note"], "그대로 남아야 함")
        self.assertEqual(manifest["e621"]["current"], "e621_2026-10-01.csv")
        self.assertEqual(tagdb.site_source()[0], self.source_dir / "e621_2026-10-01.csv")
        # 분류 파일에 행을 두고 다음 원본: old_name 은 solo(Danbooru 이름)의 별칭이 되고 fur 는 사라짐
        tagdb.write_overlay({"fur": site_row("fur", primary="body/features"),
                             "old_name": site_row("old_name", primary="object/prop")},
                            self.site_overlay, fields=tagdb.SITE_FIELDS)
        second = Path(self._tmp.name, "e621_2026-11-01.csv")
        self.write_csv(second, [("solo", 0, 20, ("old_name",))])
        report = importer.import_source(second, site="e621")
        rows = tagdb.read_overlay(self.site_overlay, tagdb.SITE_FIELDS)
        self.assertEqual(rows["fur"]["missing_since"], "2026-11-01")
        self.assertNotIn("solo", rows)                                   # Danbooru 이름으로는 옮기지 않음
        self.assertIn("Danbooru 태그라 분류를 옮기지 않음", report)
        self.assertTrue((Path(self._tmp.name) / "reports" / "import-e621-2026-11-01.md").exists())
        site = tagdb.load_site(overlay_path=self.site_overlay)
        self.assertEqual((site.id, site.meta["date"], len(site.tags)), ("e621", "2026-11-01", 1))

    def test_missing_site_file_is_fetchable(self):
        tagdb.write_manifest({"current": "d.csv", "files": [], "e621": {"current": "e.csv", "files": [
            {"file": "e.csv", "date": "2026-10-01", "url": "https://example.invalid/e.csv", "sha256": "0" * 64}]}})
        with self.assertRaises(tagdb.SourceMissing) as cm:
            tagdb.site_source()
        self.assertTrue(cm.exception.fetchable)


class StoreSiteTests(unittest.TestCase):
    def test_preview_inputs_and_check_include_site(self):
        from gui import ops
        from gui.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "tags.csv")
            tagdb.write_overlay(danbooru_overlay(), path)
            loader = lambda: (danbooru_source(), tagdb.read_overlay(path), tagdb.load_config(),  # noqa: E731
                              {"file": "fixture.csv", "date": "2026-10-01"})
            store = Store(loader, path, committed=lambda: None, extra_dir=Path(tmp, "extra"),
                          site_loader=make_site)
            inputs = ops.memory_inputs(store)
            self.assertEqual(inputs.site.overlay, store.site.overlay)     # 복사본(미리보기 빌드는 백그라운드)
            self.assertEqual(ops.memory_pack(inputs).get("anthro").site, "e621")
            self.assertTrue(ops.version_of(inputs).endswith(builder.content_hash(
                inputs.overlay, inputs.extra, inputs.config, inputs.site)))
            self.assertTrue(any("e621 원본" in line for line in store.check().info))
            # 기본이 아닌 loader 면 실제 sources.json 의 e621 을 읽지 않는다
            plain = Store(loader, path, committed=lambda: None, extra_dir=Path(tmp, "extra"))
            self.assertIsNone(plain.site)


if __name__ == "__main__":
    unittest.main()
