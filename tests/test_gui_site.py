"""관리 툴 데이터 계층(Store)의 e621 태그 — 목록·종류 필터·분류 표시·찾기 순서·e621.csv 저장·되돌리기·충돌."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixtures import EXTRA, builder, core, tagdb
from gui.store import AUTO, SITE_ALL, ConflictError, Query, Store
from test_e621 import DEPRECATED, E6, danbooru_overlay, danbooru_source, make_site, site_row

META = {"file": "fixture.csv", "date": "2026-10-01"}


class StoreSiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = self.make()

    def make(self, extra_rows=None, site_rows=E6, overlay=None):
        tmp = Path(self._tmp.name)
        self.overlay_path = tmp / "tags.csv"
        tagdb.write_overlay(overlay or danbooru_overlay(), self.overlay_path)
        extra_dir = tmp / "extra"
        rows = list(EXTRA) + DEPRECATED if extra_rows is None else extra_rows
        for db in dict.fromkeys(r["db"] for r in rows):
            tagdb.write_extra(db, [r for r in rows if r["db"] == db], extra_dir)
        self.site_path = tmp / "e621.csv"
        tagdb.write_overlay(make_site().overlay, self.site_path, fields=tagdb.SITE_FIELDS)

        def site_loader():
            s = make_site(rows=site_rows)
            return tagdb.Site(s.id, s.tags, s.meta, tagdb.read_overlay(self.site_path, tagdb.SITE_FIELDS))

        loader = lambda: (danbooru_source(), tagdb.read_overlay(self.overlay_path), tagdb.load_config(),  # noqa: E731
                          dict(META))
        return Store(loader, self.overlay_path, committed=lambda: None, extra_dir=extra_dir,
                     site_loader=site_loader, site_path=self.site_path)

    def tearDown(self):
        self._tmp.cleanup()

    # 분류 저장 경계 사례

    def test_primary_of_matches_build(self):
        # 화면의 분류 표시는 빌드한 팩과 같아야 한다(자동 분류 규칙을 화면에서 다시 계산하므로 지킴)
        st = self.store
        pack = core.TagPack(builder.build_pack(st.source, {"date": ""}, st.overlay, st.extra_list(), st.config,
                                               st.site)[0])
        for e in st.entries:
            if e.site:
                info = pack.get(core.normalize_key(e.name, pack.kaomoji))
                expect = "" if info.primary == "etc" else info.primary
                self.assertEqual(st.primary_of(e)[0], expect, e.name)

    def test_auto_same_as_follows_target_edit(self):
        # 1: wink 는 one_eye_closed 의 분류를 자동으로 쓴다 — 대상을 고치면 표시도 바뀐다
        st = self.store
        self.assertEqual(st.apply(["one_eye_closed"], {"primary": "object/prop", "rating": "g"}).errors, [])
        self.assertEqual(st.primary_of(st.by_name["wink"])[0], "object/prop")

    def test_resolve_skips_hidden_extra_rows(self):
        # 2: Danbooru 와 겹쳐 숨은 모델 행의 별칭이 보이는 내 태그 행의 같은 별칭을 가리지 않는다
        st = self.make(extra_rows=list(EXTRA) + [
            {"db": "model", "name": "solo", "primary": "meta/quality", "aliases": "shared"},
            {"db": "user", "name": "custom", "primary": "object/prop", "aliases": "shared"}])
        self.assertEqual(st.resolve("shared"), "custom")

    def test_row_of_extra_entry_ignores_missing_overlay_row(self):
        # 3: tags.csv 의 사라진 행·e621·모델 DB 가 같은 이름이면 목록은 모델 행 — row() 도 모델 행
        overlay = danbooru_overlay()
        overlay["retired"] = dict(overlay["gone_tag"], name="retired", rating="e", note="옛 값")
        st = self.make(extra_rows=list(EXTRA) + [{"db": "model", "name": "retired", "primary": "meta/quality"}],
                       site_rows=E6 + [("retired", 0, 10, ())], overlay=overlay)
        self.assertEqual(st.by_name["retired"].db, "model")
        self.assertEqual(st.row("retired").get("primary"), "meta/quality")
        self.assertEqual(st.row("retired").get("rating", ""), "")

    def test_preview_inputs_do_not_share_site_rows(self):
        # 4: 미리보기 빌드(백그라운드)와 편집이 같은 dict 를 쓰지 않는다
        from gui import ops
        inputs = ops.memory_inputs(self.store)
        self.assertIsNot(inputs.site.overlay, self.store.site.overlay)
        self.assertEqual(inputs.site.overlay, self.store.site.overlay)

    def names(self, **q):
        return [e.name for e in self.store.find(Query(**q))]

    def test_entries_and_layers(self):
        st = self.store
        self.assertTrue(st.is_site("anthro"))
        self.assertFalse(st.is_site("solo"))                    # Danbooru 와 같은 이름 — tags.csv 행
        self.assertEqual(st.by_name["masterpiece"].db, "model")  # 모델 DB 가 이긴 이름
        self.assertTrue(st.is_site("cute"))                     # 폐기 DB 행은 e621 에 밀림
        self.assertNotIn("cute", st.extra_rows)
        self.assertEqual(st.by_name["old thing"].db, "deprecated")
        self.assertTrue(st.is_site("gone_tag"))                 # 사라진 공유 이름은 e621 쪽으로
        self.assertNotIn("contributor_x", st.by_name)           # contributor·lore 는 넣지 않음
        self.assertEqual(st.counts()["site"], sum(1 for e in st.entries if e.site))

    def test_type_filters(self):
        site = self.names(type=SITE_ALL)
        self.assertIn("anthro", site)
        self.assertNotIn("solo", site)
        general = self.names(type=0)
        self.assertIn("smile", general)
        self.assertNotIn("anthro", general)                     # '일반' 은 Danbooru 만
        self.assertEqual(self.names(type=f"{SITE_ALL}:5"), ["domestic_cat", "character_sheet"])
        self.assertNotIn("anthro", self.names(type="original", primary="etc"))

    def test_primary_of(self):
        st = self.store
        p = lambda n: st.primary_of(st.by_name[n])               # noqa: E731
        self.assertEqual(p("fur"), ("body/features", "reviewed"))
        self.assertEqual(p("butt")[0], "body/lower")             # same_as thighs
        self.assertEqual(p("wink"), ("action/expression", AUTO))  # 자동 same_as(Danbooru 별칭)
        self.assertEqual(p("domestic_cat"), ("subject/species", AUTO))
        self.assertEqual(p("anthro"), ("", ""))                  # 미분류
        self.assertEqual(p("gone_tag")[0], "object/prop")        # tags.csv 의 사라진 행 분류
        self.assertIn("anthro", self.names(type=SITE_ALL, primary="-"))

    def test_resolve_order(self):
        st = self.store
        self.assertEqual(st.resolve("anthromorph"), "anthro")    # e621 별칭
        self.assertEqual(st.resolve("alone"), "solo")            # Danbooru 와 같은 이름의 e621 별칭
        self.assertEqual(st.resolve("cute"), "cute")
        self.assertEqual(st.resolve("wink"), "wink")             # e621 이름이 Danbooru 별칭을 가림
        self.assertEqual(st.resolve("masterpiece"), "masterpiece")
        self.assertEqual(self.names(text="anthromorph")[0], "anthro")

    def test_edit_undo_and_file(self):
        st = self.store
        rev = st.revision
        res = st.apply(["anthro"], {"primary": "subject/species", "identity": "1"})
        self.assertEqual(res.errors, [])
        disk = tagdb.read_overlay(self.site_path, tagdb.SITE_FIELDS)
        self.assertEqual((disk["anthro"]["primary"], disk["anthro"]["status"], disk["anthro"]["source"]),
                         ("subject/species", "reviewed", "e621"))
        self.assertGreater(st.revision, rev)
        self.assertEqual(st.primary_of(st.by_name["anthro"])[0], "subject/species")
        self.assertNotIn("anthro", tagdb.read_overlay(self.overlay_path))     # tags.csv 는 그대로
        self.assertEqual(st.undo(), ["anthro"])
        self.assertNotIn("anthro", tagdb.read_overlay(self.site_path, tagdb.SITE_FIELDS))
        self.assertEqual(st.primary_of(st.by_name["anthro"]), ("", ""))

    def test_same_as_row_gets_own_class(self):
        st = self.store
        self.assertEqual(st.apply(["butt"], {"primary": "body/lower", "rating": "s", "regions": "hips"}).errors, [])
        row = tagdb.read_overlay(self.site_path, tagdb.SITE_FIELDS)["butt"]
        self.assertEqual((row["same_as"], row["primary"], row["rating"]), ("", "body/lower", "s"))

    def test_mixed_targets_and_conflict(self):
        st = self.store
        self.assertTrue(st.preview(["anthro", "smile"], {"primary": "object/prop"}).errors)
        tagdb.write_overlay({"fur": site_row("fur", primary="object/prop")}, self.site_path,
                            fields=tagdb.SITE_FIELDS)                          # 밖에서 고침
        with self.assertRaises(ConflictError):
            st.apply(["anthro"], {"primary": "subject/species"})
        self.assertTrue(st.external_change())


if __name__ == "__main__":
    unittest.main()
