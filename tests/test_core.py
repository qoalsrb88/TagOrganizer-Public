"""코어 파이프라인 테스트 — 정렬·필터 시나리오와 프롬프트 문법."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fixtures import builder, core, pack


def run(text, **opts):
    return core.organize(text, pack(), opts)


class ScenarioTests(unittest.TestCase):
    def test_s1_character_swap_keeps_pose_and_state(self):
        text = ("1girl, solo, hatsune miku, vocaloid, aqua eyes, aqua hair, twintails, very long hair, "
                "floating hair, grey shirt, black skirt, standing, looking at viewer, smile, simple background")
        r = run(text, preset="character_swap", exclude="clothes/top, clothes/bottom")
        self.assertEqual(
            r.text,
            "1girl, solo,\nlooking at viewer,\nstanding,\nsmile,\nfloating hair,\nsimple background",
        )
        removed = {row["tag"]: row["reason"] for row in r.report["removed"]}
        self.assertEqual(removed["hatsune miku"], "exclude:subject/character")
        self.assertEqual(removed["vocaloid"], "exclude:series")
        self.assertEqual(removed["aqua hair"], "exclude:*@identity")
        self.assertEqual(removed["grey shirt"], "exclude:clothes/top")
        self.assertNotIn("floating hair", removed)

    def test_s2_auto_layout_reproduces_memo(self):
        text = ("park, leaf, white shirt, sagisawa fumika, dappled sunlight, autumn leaves, shirt, on bench, "
                "black hair, smile, autumn, maple leaf, long skirt, looking at viewer, holding, reflection, "
                "hairband, blush, collarbone, ginkgo leaf, skirt, nature, holding leaf, blue eyes, branch, "
                "vertical-striped skirt, falling leaves, closed mouth, white hairband, bench, striped skirt, "
                "day, park bench, blue skirt, tree")
        expected = (
            "sagisawa fumika,\n"
            "looking at viewer,\n"
            "on bench, holding, holding leaf,\n"
            "white shirt, shirt, long skirt, skirt, vertical-striped skirt, striped skirt, blue skirt,\n"
            "collarbone,\n"
            "smile, blush, closed mouth,\n"
            "blue eyes,\n"
            "black hair, hairband, white hairband,\n"
            "leaf, autumn leaves, maple leaf, ginkgo leaf, branch, bench, park bench, tree,\n"
            "dappled sunlight, reflection, falling leaves,\n"
            "park, autumn, nature, day"
        )
        self.assertEqual(run(text).text, expected)

    def test_s3_upper_body_drops_feet_tags(self):
        r = run("1girl, upper body, shoes, soles, toenails, navel, smile")
        self.assertEqual(r.text, "1girl,\nupper body,\nnavel,\nsmile")
        reasons = {row["tag"]: row["reason"] for row in r.report["removed"]}
        self.assertEqual(reasons["shoes"], "rule:framing(upper body)")

    def test_s3_from_behind_drops_front_and_face(self):
        r = run("1girl, from behind, navel, smile, black hair")
        self.assertEqual(r.text, "1girl,\nfrom behind,\nblack hair")

    def test_s3_looking_back_reveals_face(self):
        r = run("1girl, from behind, looking back, navel, smile")
        self.assertEqual(r.text, "1girl,\nfrom behind,\nlooking back,\nsmile")

    def test_s3_multiple_views_disables_rules(self):
        r = run("1girl, multiple views, upper body, from behind, shoes, navel")
        self.assertEqual(r.report["counts"]["removed"], 0)

    def test_s3_multi_subject_disables_view_only(self):
        r = run("2girls, from behind, upper body, navel, shoes")
        kept = [t["text"] for t in r.report["tokens"] if "removed" not in t]
        self.assertIn("navel", kept)
        self.assertNotIn("shoes", kept)

    def test_s3_one_girl_one_boy_counts_as_multi(self):
        r = run("1girl, 1boy, from behind, navel")
        self.assertEqual(r.report["counts"]["removed"], 0)

    def test_s3_partial_region_survives(self):
        r = run("1girl, cowboy shot, thighhighs, shoes")
        kept = [t["text"] for t in r.report["tokens"] if "removed" not in t]
        self.assertIn("thighhighs", kept)
        self.assertNotIn("shoes", kept)

    def test_s3_conflicting_framing_hides_only_common_regions(self):
        r = run("1girl, upper body, full body, shoes")
        self.assertEqual(r.report["counts"]["removed"], 0)

    def test_s3_report_mode_flags_without_removing(self):
        r = run("1girl, upper body, shoes", rules="report")
        self.assertIn("shoes", r.text)
        self.assertEqual(r.report["flagged"], [{"tag": "shoes", "reason": "rule:framing(upper body)"}])

    def test_s3_rules_off(self):
        self.assertIn("shoes", run("upper body, shoes", rules="off").text)

    def test_s3_rule_ignores_excluded_gate(self):
        # 구도를 빼면 그 구도의 제약도 사라진다
        r = run("1girl, upper body, shoes", exclude="composition")
        self.assertIn("shoes", r.text)

    def test_explicit_rule_high_angle(self):
        r = run("from above, depth of field, blurry background, smile")
        self.assertEqual(r.text, "from above,\nsmile")

    def test_closed_eyes_hide_eye_tags(self):
        # 행동(눈 감기)이 신체(눈 색)보다 우선 — 2026-10-26 사용자 의견
        self.assertEqual(run("1girl, closed eyes, blue eyes, smile").text, "1girl,\nclosed eyes, smile")
        self.assertNotIn("blue eyes", run("^_^, blue eyes").text)
        self.assertIn("blue eyes", run("closed eyes, one eye closed, blue eyes").text)
        self.assertIn("blue eyes", run("2girls, closed eyes, blue eyes").text)          # 누구 눈인지 모름
        self.assertIn("blue eyes", run("multiple views, closed eyes, blue eyes").text)
        self.assertIn("blue eyes", run("closed eyes, blue eyes",
                                       rule_overrides={"closed_eyes_eye_color": "off"}).text)
        self.assertIn("blue eyes", run("closed eyes, blue eyes", rules="off").text)

    def test_s4_max_rating(self):
        r = run("1girl, cleavage, nipples, sex, smile", max_rating="s")
        self.assertEqual(r.text, "1girl,\ncleavage,\nsmile")
        reasons = {row["tag"]: row["reason"] for row in r.report["removed"]}
        self.assertEqual(reasons["nipples"], "rating:q>s")
        self.assertNotIn("cleavage", run("cleavage, smile", max_rating="g").text)


class ParseTests(unittest.TestCase):
    def tokens(self, text, dialect="comfyui", with_pack=False):
        return [(t.text, round(t.weight, 4), t.kind)
                for t in core.parse_prompt(text, dialect, pack() if with_pack else None)]

    def test_comfy_weights(self):
        self.assertEqual(
            self.tokens("(smile:1.2), ((blush)), (a, b:0.8), [x]"),
            [("smile", 1.2, "tag"), ("blush", 1.21, "tag"), ("a", 0.8, "tag"), ("b", 0.8, "tag"),
             ("[x]", 1.0, "tag")],
        )

    def test_comfy_bad_weight_keeps_text(self):
        self.assertEqual(self.tokens("(re:zero)"), [("re:zero", 1.1, "tag")])

    def test_escaped_parens_are_literal(self):
        self.assertEqual(self.tokens(r"star \(symbol\)"), [("star (symbol)", 1.0, "tag")])
        self.assertEqual(self.tokens(r"(star \(symbol\):1.2)"), [("star (symbol)", 1.2, "tag")])

    def test_known_tag_with_unescaped_parens(self):
        self.assertEqual(self.tokens("star (symbol)", with_pack=True), [("star (symbol)", 1.0, "tag")])

    def test_inline_emphasis_is_kept_verbatim(self):
        toks = core.parse_prompt("a cat (sleeping:1.2), smile", "comfyui", None)
        self.assertTrue(toks[0].opaque)
        self.assertEqual(toks[0].verbatim, "a cat (sleeping:1.2)")

    def test_special_tokens(self):
        kinds = self.tokens("1girl <lora:foo:0.8>, embedding:bad, __hair__, {red|blue} hair")
        self.assertEqual([k for _, _, k in kinds], ["tag", "special", "special", "special", "special"])

    def test_unbalanced_parens_fall_back(self):
        self.assertEqual([t for t, _, _ in self.tokens("(smile, blush")], ["(smile", "blush"])

    def test_nai_weights(self):
        self.assertEqual(
            self.tokens("{smile}, [[blush]], 1.3::black hair, blue eyes::, star (symbol)", "nai"),
            [("smile", 1.05, "tag"), ("blush", round(1 / 1.05 ** 2, 4), "tag"), ("black hair", 1.3, "tag"),
             ("blue eyes", 1.3, "tag"), ("star (symbol)", 1.0, "tag")],
        )

    def test_nai_number_before_close_is_text(self):
        # 숫자로 끝나는 태그를 빈칸 없이 닫아도 닫기로 읽고, NAI 가 그 숫자를 가중치로 읽는다는 경고를 남긴다
        toks = core.parse_prompt("1.1:: artist:as109::, 1.1::year 2024::, 2::1girl::", "nai", None)
        self.assertEqual([(t.text, round(t.weight, 4)) for t in toks],
                         [("artist:as109", 1.1), ("year 2024", 1.1), ("1girl", 2.0)])
        self.assertEqual([t.warning for t in toks], ["nai_number:109", "nai_number:2024", ""])
        self.assertEqual(self.tokens("1.1::artist:as109 ::", "nai"), [("artist:as109", 1.1, "tag")])
        self.assertEqual(core.parse_prompt("1.1::artist:as109 ::", "nai", None)[0].warning, "")
        # 닫을 강조가 없는 "N::" 는 글자 그대로(예전에는 2024 가 사라졌다)
        self.assertEqual(self.tokens("year 2024::, smile", "nai"), [("year 2024::", 1.0, "tag"), ("smile", 1.0, "tag")])


class FormatTests(unittest.TestCase):
    def test_comfy_output_escapes_and_weights(self):
        r = run(r"(star \(symbol\):1.2), smile", line_per_section=False)
        self.assertEqual(r.text, r"smile, (star \(symbol\):1.2)")

    def test_comfy_to_nai(self):
        r = run(r"(smile:1.2), star \(symbol\), artist:wlop", output_dialect="nai", line_per_section=False)
        self.assertEqual(r.text, "artist:wlop, 1.2::smile ::, star (symbol)")

    def test_nai_number_emphasis_closes_with_space(self):
        r = run("1.1:: artist:as109::, 1.1::year 2024::", input_dialect="nai", line_per_section=False)
        self.assertEqual(r.text, "1.1::artist:as109 ::, 1.1::year 2024 ::")
        self.assertEqual(r.report["counts"]["warnings"], 2)
        self.assertEqual(r.report["warnings"][0], {"tag": "as109", "reason": "nai_number:109"})
        self.assertEqual(run("(smile:0.6)", output_dialect="nai").text, "0.6::smile ::")

    def test_nai_roundtrip_keeps_braces(self):
        r = run("{smile}, [[blush]], 1.3::shirt::", input_dialect="nai", line_per_section=False)
        self.assertEqual(r.text, "1.3::shirt ::, {smile}, [[blush]]")
        r = run("{smile}", input_dialect="nai", output_dialect="comfyui")
        self.assertEqual(r.text, "(smile:1.05)")

    def test_anima_artist_prefix(self):
        r = run("@wlop, smile", input_dialect="anima", line_per_section=False)
        self.assertEqual(r.text, "@wlop, smile")
        r = run("@wlop, smile", input_dialect="anima", output_dialect="comfyui", line_per_section=False)
        self.assertEqual(r.text, "artist:wlop, smile")

    def test_artist_with_parens(self):
        self.assertEqual(run("artist:ruu (tksymkw)").text, r"artist:ruu \(tksymkw\)")

    def test_unknown_artist_is_kept_as_artist(self):
        r = run("artist:Somebody_New, smile")
        self.assertEqual(r.text, "artist:somebody new,\nsmile")

    def test_normalization_and_aliases(self):
        self.assertEqual(run("Long_Hair, longhair, sole_female", line_per_section=False).text, "1girl, long hair")
        r = run("longhair", resolve_aliases=False)
        self.assertEqual(r.text, "longhair")

    def test_kaomoji_keep_underscores(self):
        self.assertEqual(run("^_^, >_<", line_per_section=False).text, "^_^, >_<")

    def test_custom_tag_display(self):
        self.assertEqual(run("score 9, Masterpiece", line_per_section=False).text, "score_9, masterpiece")

    def test_dedupe_keeps_first(self):
        r = run("smile, (smile:1.2), Smile", line_per_section=False)
        self.assertEqual(r.text, "smile")
        self.assertEqual(r.report["counts"]["removed"], 2)
        self.assertEqual(run("smile, smile", dedupe=False, line_per_section=False).text, "smile, smile")

    def test_unknown_and_unclassified_never_dropped(self):
        r = run("Some_Trigger, unclassified thing, smile", preset="character_outfit_swap")
        self.assertEqual(r.text, "smile,\nSome_Trigger, unclassified thing")
        self.assertEqual(r.report["unknown"], ["Some_Trigger"])
        self.assertEqual(r.report["unclassified"], ["unclassified thing"])

    def test_break_chunks_are_organized_separately(self):
        r = run("smile, 1girl BREAK blue eyes, solo")
        self.assertEqual(r.text, "1girl,\nsmile\nBREAK\nsolo,\nblue eyes")

    def test_trailing_comma_option(self):
        self.assertEqual(run("smile, 1girl", trailing_comma=False).text, "1girl\nsmile")

    def test_order_popular(self):
        r = run("blush, smile", order="popular", line_per_section=False)
        self.assertEqual(r.text, "smile, blush")

    def test_compact_layout(self):
        r = run("smile, 1girl, simple background, shirt", layout="compact")
        self.assertEqual(r.text, "1girl,\nsmile,\nshirt,\nsimple background")

    def test_secondary_match_option(self):
        p = core.TagPack(builder.build_pack(*_build_args({"smile": {"secondary": "body/face"}}))[0])
        self.assertIn("smile", core.organize("smile", p, {"exclude": "body/face"}).text)
        self.assertEqual(core.organize("smile", p, {"exclude": "body/face", "match_secondary": True}).text, "")


class OptionTests(unittest.TestCase):
    def test_invalid_options(self):
        for opts in ({"preset": "nope"}, {"layout": "nope"}, {"max_rating": "x"}, {"exclude": "clothes/nope"},
                     {"rules": "maybe"}, {"input_dialect": "a1111"}, {"order": "random"}, {"bogus": 1}):
            with self.assertRaises(ValueError, msg=opts):
                run("smile", **opts)


class PackLoadTests(unittest.TestCase):
    def test_load_pack_caches_and_reloads(self):
        p, _ = builder.build_pack(*_build_args())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tagpack.json.gz")
            builder.write_pack(p, Path(path))
            first = core.load_pack(path)
            self.assertIs(core.load_pack(path), first)
            os.utime(path, ns=(1, 1))
            self.assertIsNot(core.load_pack(path), first)

    def test_rejects_wrong_format(self):
        with self.assertRaises(ValueError):
            core.TagPack({"format": "other"})


def _build_args(overrides=None):
    from fixtures import EXTRA, SOURCE, overlay_rows, source_tags, tagdb
    rows = overlay_rows()
    for name, fields in (overrides or {}).items():
        rows[name] = {**rows[name], **fields}
    return (source_tags(), {"file": "fixture.csv", "date": "2026-10-01", "rows": len(SOURCE)},
            rows, EXTRA, tagdb.load_config())


if __name__ == "__main__":
    unittest.main()
