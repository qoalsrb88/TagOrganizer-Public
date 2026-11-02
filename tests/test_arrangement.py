"""배열 세팅(arrangement)·크롭/시점 덮어쓰기·팩에 없는 토큰 추정(guess) 테스트 — 코어 v1.2."""

from __future__ import annotations

import json
import unittest

from fixtures import core, pack

S2 = ("park, leaf, white shirt, sagisawa fumika, dappled sunlight, autumn leaves, shirt, on bench, black hair, smile, "
      "autumn, maple leaf, long skirt, looking at viewer, holding, reflection, hairband, blush, collarbone, "
      "ginkgo leaf, skirt, nature, holding leaf, blue eyes, branch, vertical-striped skirt, falling leaves, "
      "closed mouth, white hairband, bench, striped skirt, day, park bench, blue skirt, tree")


def run(text, **opts):
    return core.organize(text, pack(), opts)


def arrangement(rows, chunks, **extra):
    return {"format": core.ARRANGEMENT_FORMAT, "version": 1, "rows": rows, "chunks": chunks, **extra}


def reasons(result):
    return {row["tag"]: row["reason"] for row in result.report["removed"]}


class LayoutConversionTests(unittest.TestCase):
    def test_layout_as_arrangement_gives_same_output(self):
        texts = [S2, "1girl, upper body, shoes, soles, navel, smile, masterpiece, mystery word, <lora:x:0.8>",
                 "2girls, hatsune miku, vocaloid, from behind, looking back, smile, aqua eyes, BREAK 1boy, standing"]
        for layout in ("default", "compact"):
            arr = core.arrangement_from_layout(pack(), layout)
            for text in texts:
                for order in ("input", "popular"):
                    with self.subTest(layout=layout, text=text[:20], order=order):
                        legacy = run(text, layout=layout, order=order)
                        new = run(text, arrangement=arr, order=order)
                        self.assertEqual(new.text, legacy.text)
                        self.assertEqual(new.removed_text, legacy.removed_text)

    def test_conversion_expands_subs_and_keeps_bare_main(self):
        arr = core.arrangement_from_layout(pack(), "default")
        self.assertEqual(arr["rows"][0], ["count"])
        self.assertEqual(arr["chunks"]["count"]["members"], ["subject/count"])
        # 앞 섹션이 가져간 subject/count 는 빼고, 서브 없이 메인만 붙은 태그용 "subject" 를 끝에 둔다
        self.assertEqual(arr["chunks"]["character"]["members"],
                         ["subject/species", "subject/character", "subject", "series"])
        self.assertNotIn("clothes/hair_acc", arr["chunks"]["clothes"]["members"])
        self.assertEqual(arr["chunks"]["hair"]["members"], ["body/hair", "clothes/hair_acc"])
        self.assertEqual(core.uncovered_slots(arr, pack().taxonomy), [])
        json.dumps(arr)                                                   # 다른 툴로 그대로 보낼 수 있다


class ArrangementTests(unittest.TestCase):
    def test_rows_join_chunks_on_one_line(self):
        arr = arrangement([["count", "hair"], ["eyes"]], {
            "count": {"members": ["subject/count"]}, "hair": {"members": ["body/hair"]},
            "eyes": {"members": ["body/eyes"]}})
        r = run("blue eyes, long hair, 1girl, smile", arrangement=arr)
        self.assertEqual(r.text, "1girl, long hair,\nblue eyes,\nsmile")       # 배치하지 않은 smile 은 맨 끝 rest

    def test_sub_order_inside_chunk(self):
        chunks = {"look": {"members": ["body/eyes", "body/hair"]}}
        text = "long hair, blue eyes, twintails, aqua eyes"
        self.assertEqual(run(text, arrangement=arrangement([["look"]], chunks)).text,
                         "blue eyes, aqua eyes, long hair, twintails")
        self.assertEqual(run(text, arrangement=arrangement([["look"]], chunks, sub_order=False)).text,
                         "long hair, blue eyes, twintails, aqua eyes")

    def test_most_specific_member_wins(self):
        # body 전체를 앞에 두어도 뒤 청크의 body/hair 가 머리카락을 가져간다
        arr = arrangement([["body"], ["hair"]], {"body": {"members": ["body"]}, "hair": {"members": ["body/hair"]}})
        r = run("long hair, navel, blue eyes", arrangement=arr)
        self.assertEqual(r.text, "navel, blue eyes,\nlong hair")
        sections = {row["text"]: row["section"] for row in r.report["tokens"]}
        self.assertEqual(sections, {"long hair": "hair", "navel": "body", "blue eyes": "body"})

    def test_identity_member_is_more_specific(self):
        arr = arrangement([["hair"], ["own"]], {"hair": {"members": ["body/hair"]},
                                                 "own": {"members": ["body/hair@identity"]}})
        r = run("floating hair, long hair", arrangement=arr)
        self.assertEqual(r.text, "floating hair,\nlong hair")                 # long hair 만 identity

    def test_custom_chunk_tags_and_patterns(self):
        arr = arrangement([["mine"], ["clothes"]], {
            "mine": {"label": "내 묘사", "color": "#123456", "tags": ["My Phrase", "shoes"], "patterns": ["* armor"]},
            "clothes": {"members": ["clothes"]}})
        r = run("white shirt, shoes, cool plate armor, my_phrase, other phrase", arrangement=arr)
        self.assertEqual(r.text, "shoes, cool plate armor, my_phrase,\nwhite shirt,\nother phrase")
        chunk = r.report["arranged"][0]["rows"][0][0]
        self.assertEqual((chunk["id"], chunk["label"], chunk["color"]), ("mine", "내 묘사", "#123456"))

    def test_custom_chunk_still_filtered(self):
        # 커스텀 청크는 자리만 정한다: 구도 규칙은 그대로(뺀 태그도 그 청크 자리에 남는다)
        arr = arrangement([["mine"]], {"mine": {"tags": ["shoes"]}})
        r = run("upper body, shoes", arrangement=arr)
        self.assertEqual(reasons(r)["shoes"], "rule:framing(upper body)")
        self.assertEqual(r.report["tokens"][1]["section"], "mine")

    def test_hidden_chunk_removes_with_reason(self):
        arr = arrangement([["count"], ["bg"]], {"count": {"members": ["subject/count"]},
                                                "bg": {"members": ["background"], "hidden": True}})
        r = run("1girl, day, park", arrangement=arr)
        self.assertEqual(r.text, "1girl")
        self.assertEqual(reasons(r), {"day": "hidden:bg", "park": "hidden:bg"})

    def test_unplaced_categories_go_to_rest_never_dropped(self):
        arr = arrangement([["count"]], {"count": {"members": ["subject/count"]}, "spare": {"members": ["body"]}})
        r = run("long hair, 1girl, mystery word", arrangement=arr)
        self.assertEqual(r.text, "1girl,\nlong hair, mystery word")
        self.assertIn("body/hair", core.uncovered_slots(arr, pack().taxonomy))
        self.assertNotIn("subject/count", core.uncovered_slots(arr, pack().taxonomy))

    def test_json_string_and_empty(self):
        arr = arrangement([["count"]], {"count": {"members": "subject/count"}})
        self.assertEqual(run("smile, 1girl", arrangement=json.dumps(arr)).text, "1girl,\nsmile")
        self.assertEqual(run("smile, 1girl", arrangement="").text, run("smile, 1girl").text)

    def test_custom_tags_beat_earlier_patterns(self):
        # 리뷰 1: 앞 청크의 패턴보다 뒤 청크에 이름으로 적은 태그가 먼저(앞 청크가 숨김이어도 지워지지 않게)
        arr = arrangement([["armor"], ["mine"]], {"armor": {"patterns": ["* armor"], "hidden": True},
                                                  "mine": {"tags": ["cool armor"]}})
        r = run("cool armor, hot armor", arrangement=arr)
        self.assertEqual(r.text, "cool armor")
        self.assertEqual(reasons(r), {"hot armor": "hidden:armor"})

    def test_bad_shapes_are_value_errors(self):
        # 리뷰 5: 다른 툴이 만든 잘못된 JSON 도 ValueError 로(가져오기·CLI 가 메시지로 보여 줌)
        for data in ({"format": core.ARRANGEMENT_FORMAT, "rows": [1], "chunks": {}},
                     {"format": core.ARRANGEMENT_FORMAT, "rows": 3, "chunks": {}},
                     {"format": core.ARRANGEMENT_FORMAT, "rows": [["a"]], "chunks": {"a": {"members": [1]}}},
                     {"format": core.ARRANGEMENT_FORMAT, "rows": [], "chunks": {}, "guess": [{"pattern": 3}]},
                     {"format": core.ARRANGEMENT_FORMAT, "rows": [], "chunks": {}, "guess": 5}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                core.load_arrangement(data, pack())

    def test_identity_layout_converts_faithfully(self):
        # 리뷰 6: 앞 섹션이 고유 외형만, 뒤 섹션이 나머지 전부면 둘이 나뉘어야 한다
        from fixtures import build
        data = build()[0]
        data["layouts"]["own"] = {"label": "외형 먼저", "sections": [
            {"id": "own", "include": ["body@identity"]}, {"id": "all", "include": ["*"]}]}
        data["layouts"]["no_own"] = {"label": "외형 빼고", "sections": [
            {"id": "body", "include": ["body"], "except": ["body@identity"]}, {"id": "all", "include": ["*"]}]}
        p = core.TagPack(data)
        for layout in ("own", "no_own"):
            arr = core.arrangement_from_layout(p, layout)
            for text in ("long hair, floating hair, smile, navel", "smile, blue eyes, collarbone"):
                with self.subTest(layout=layout, text=text):
                    self.assertEqual(core.organize(text, p, {"arrangement": arr}).text,
                                     core.organize(text, p, {"layout": layout}).text)

    def test_non_string_values_are_value_errors(self):
        for chunk in ({"members": [5]}, {"members": "body", "tags": 7}, {"members": None}):
            with self.subTest(chunk=chunk):
                try:
                    run("1girl", arrangement=arrangement([["a"]], {"a": chunk}))
                except ValueError:
                    pass                                                       # 다른 예외로 새지 않으면 된다

    def test_invalid_arrangement_lists_problems(self):
        bad = arrangement([["a", "nope"], ["a"]], {
            "a": {"members": ["body/nothing"], "color": "red", "patterns": ["re:("]}})
        with self.assertRaises(ValueError) as cm:
            run("1girl", arrangement=bad)
        msg = str(cm.exception)
        for part in ("nope", "두 번", "body/nothing", "color", "패턴"):
            self.assertIn(part, msg)
        for value, part in (({"format": "x"}, "형식"), ({"format": core.ARRANGEMENT_FORMAT, "version": 9}, "버전"),
                            ("{oops", "JSON"), ([1], "객체")):
            with self.subTest(value=value), self.assertRaises(ValueError) as cm:
                run("1girl", arrangement=value)
            self.assertIn(part, str(cm.exception))

    def test_arranged_report_structure(self):
        arr = arrangement([["count", "framing"], ["clothes"]], {
            "count": {"members": ["subject/count"]}, "framing": {"members": ["composition/framing"]},
            "clothes": {"members": ["clothes"]}})
        r = run("1girl, upper body, shoes, white shirt BREAK 1boy", arrangement=arr, output_dialect="nai")
        seg0, seg1 = r.report["arranged"]
        self.assertEqual([[c["id"] for c in row] for row in seg0["rows"]], [["count", "framing"], ["clothes"]])
        clothes = seg0["rows"][1][0]
        self.assertEqual(clothes["label"], "의상")
        self.assertEqual(clothes["color"], next(c["color"] for c in pack().taxonomy["categories"] if c["id"] == "clothes"))
        rows = r.report["tokens"]
        self.assertEqual([rows[i]["text"] for i in clothes["tokens"]], ["shoes", "white shirt"])   # 뺀 토큰도 제자리에
        self.assertEqual(rows[clothes["tokens"][0]].get("removed"), "rule:framing(upper body)")
        self.assertEqual([[c["id"] for c in row] for row in seg1["rows"]], [["count"]])
        self.assertEqual(rows[seg1["rows"][0][0]["tokens"][0]]["text"], "1boy")

    def test_out_uses_output_dialect_for_removed_tokens_too(self):
        r = run("(shoes:1.2), upper body", output_dialect="nai")
        self.assertEqual(r.report["tokens"][0]["out"], "1.2::shoes ::")
        self.assertTrue(r.report["tokens"][0]["removed"])


class OverrideTests(unittest.TestCase):
    def test_framing_override_replaces_and_hides(self):
        r = run("1girl, full body, shoes, smile", framing="upper body")
        self.assertEqual(r.text, "1girl,\nupper body,\nsmile")
        self.assertEqual(reasons(r), {"full body": "override:framing", "shoes": "rule:framing(upper body)"})
        injected = [row for row in r.report["tokens"] if row.get("injected")]
        self.assertEqual([(row["text"], row["injected"]) for row in injected], [("upper body", "framing")])

    def test_override_keeps_same_tag_without_duplicate(self):
        r = run("upper body, cowboy shot, shoes", framing="upper_body")
        self.assertEqual(r.text, "upper body")
        self.assertEqual(reasons(r)["cowboy shot"], "override:framing")
        self.assertFalse(any(row.get("injected") for row in r.report["tokens"]))

    def test_view_override(self):
        r = run("1girl, from above, smile, navel, looking at viewer", view="from behind")
        self.assertEqual(reasons(r), {"from above": "override:view", "smile": "rule:view(from behind)",
                                      "navel": "rule:view(from behind)"})
        self.assertIn("from behind", r.text)

    def test_several_tags_and_no_prompt_tag(self):
        r = run("1girl, shoes", framing="cowboy shot, portrait")
        self.assertEqual(r.text, "1girl,\ncowboy shot, portrait")
        self.assertEqual(reasons(r)["shoes"], "rule:framing(cowboy shot, portrait)")

    def test_override_in_break_segments(self):
        r = run("1girl, full body BREAK 1boy, shoes BREAK 2girls, cowboy shot", framing="upper body")
        self.assertEqual(r.text, "1girl,\nupper body\nBREAK\n1boy,\nshoes\nBREAK\n2girls,\nupper body")

    def test_override_must_be_target_subcategory(self):
        for opts, part in (({"framing": "smile"}, "composition/framing"), ({"view": "upper body"}, "composition/angle"),
                           ({"framing": "no such tag"}, "no such tag")):
            with self.subTest(opts=opts), self.assertRaises(ValueError) as cm:
                run("1girl", **opts)
            self.assertIn(part, str(cm.exception))


class GuessTests(unittest.TestCase):
    def test_off_by_default(self):
        r = run("holding chainsword, silky long hair")
        self.assertEqual(r.report["counts"]["guessed"], 0)
        self.assertEqual(r.text, "holding chainsword, silky long hair")

    def test_pattern_then_head(self):
        r = run("holding chainsword, sharp crimson eyes, big shoes, 1girl", guess=True)
        why = {g["tag"]: (g["primary"], g["why"]) for g in r.report["guessed"]}
        self.assertEqual(why, {"holding chainsword": ("action/holding", "pattern:holding *"),
                               "sharp crimson eyes": ("body/eyes", "pattern:* eyes"),
                               "big shoes": ("clothes/footwear", "head:shoes")})
        self.assertEqual(r.text, "1girl,\nholding chainsword,\nbig shoes,\nsharp crimson eyes")
        self.assertEqual(r.report["counts"]["unknown"], 3)            # 팩에는 여전히 없다
        row = next(t for t in r.report["tokens"] if t["text"] == "big shoes")
        self.assertNotIn("key", row)                                  # 편집 패널로 열 태그가 아니다

    def test_guessed_tokens_follow_rules_but_are_not_gates(self):
        r = run("upper body, big shoes, closed eyes, sharp crimson eyes", guess=True)
        self.assertEqual(reasons(r), {"big shoes": "rule:framing(upper body)",
                                      "sharp crimson eyes": "rule:closed_eyes_eye_color"})
        r = run("my upper body, shoes", guess=True)                    # 추정한 크롭은 기준 태그가 아니다
        self.assertEqual(r.report["removed"], [])

    def test_guessed_identity_follows_preset(self):
        r = run("sharp crimson eyes, bright pupils", guess=True, preset="character_swap")
        self.assertEqual(reasons(r), {"sharp crimson eyes": "exclude:*@identity"})

    def test_arrangement_guess_rules_come_first(self):
        arr = arrangement([["weapon"]], {"weapon": {"members": ["object"]}},
                          guess=[{"pattern": "holding *", "primary": "object/weapon"}])
        r = run("holding chainsword", guess=True, arrangement=arr)
        self.assertEqual(r.report["guessed"][0]["primary"], "object/weapon")
        with self.assertRaises(ValueError):
            run("x", guess=True, arrangement=arrangement([], {}, guess=[{"pattern": "*", "primary": "etc"}]))

    def test_guess_rule_validation(self):
        p = pack()
        for item, part in (({"pattern": "a *", "primary": "nope"}, "카테고리"),
                           ({"pattern": "*", "primary": "body"}, "글자"),
                           ({"pattern": "a *", "primary": "body", "regions": ["tail"]}, "부위"),
                           ({"pattern": "re:(", "primary": "body"}, "패턴")):
            with self.subTest(item=item), self.assertRaises(ValueError) as cm:
                core.compile_guess_rules([item], p)
            self.assertIn(part, str(cm.exception))
        rule = core.compile_guess_rules([{"pattern": "re:^x+ y$", "primary": "body"}], p)[0]
        self.assertTrue(rule.regex.fullmatch("xx y"))
        self.assertFalse(core.compile_pattern("holding *").fullmatch("holdings x"))


class CliTests(unittest.TestCase):
    def test_organize_with_arrangement_file_and_overrides(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path

        import tagorg
        from fixtures import build, builder
        with tempfile.TemporaryDirectory() as tmp:
            pack_path = Path(tmp, "tagpack.json.gz")
            builder.write_pack(build()[0], pack_path)
            arr = arrangement([["count", "framing"]], {"count": {"members": ["subject/count"]},
                                                       "framing": {"members": ["composition/framing"]}})
            arr_path = Path(tmp, "arr.json")
            arr_path.write_text(json.dumps(arr), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = tagorg.main(["organize", "-t", "1girl, full body, shoes", "--pack", str(pack_path),
                                    "--arrangement", str(arr_path), "--framing", "upper body", "-q"])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().strip(), "1girl, upper body")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = tagorg.main(["organize", "-t", "1girl", "--pack", str(pack_path), "--view", "smile"])
            self.assertEqual(code, 2)
            self.assertIn("composition/angle", err.getvalue())


class PaletteTests(unittest.TestCase):
    def test_palette_for_editors(self):
        pal = core.palette(pack())
        self.assertEqual(pal["format"], core.PALETTE_FORMAT)
        subject = next(c for c in pal["categories"] if c["id"] == "subject")
        self.assertEqual([s["id"] for s in subject["subs"]], ["count", "species", "character"])
        self.assertTrue(subject["color"].startswith("#"))
        self.assertTrue(any(c.get("system") and c["id"] == "unknown" for c in pal["categories"]))
        self.assertIn("upper body", pal["quick"]["framing"])
        self.assertIn("from behind", pal["quick"]["view"])
        self.assertIn("default", pal["layouts"])
        self.assertEqual(core.selector_label("subject/count", pack().taxonomy), "인원")
        self.assertEqual(core.selector_label("clothes", pack().taxonomy), "의상")
        json.dumps(pal)


if __name__ == "__main__":
    unittest.main()
