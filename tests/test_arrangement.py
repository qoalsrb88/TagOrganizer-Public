"""배열 세팅(arrangement)·크롭/시점 덮어쓰기·팩에 없는 토큰 추정(guess) 테스트 — 코어 v1.2."""

from __future__ import annotations

import json
import unittest

from fixtures import build, core, pack
from tools import tagdb

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


class GapTests(unittest.TestCase):
    def arr(self, rows=None, gaps=("hair",)):
        chunks = {"count": {"members": ["subject/count"]}, "hair": {"members": ["body/hair"]},
                  "eyes": {"members": ["body/eyes"]}}
        for cid in gaps:
            chunks[cid]["gap_after"] = True
        return arrangement(rows or [["count"], ["hair"], ["eyes"]], chunks)

    def row_ids(self, result, section=0):
        return [[c["id"] for c in row] for row in result.report["arranged"][section]["rows"]]

    def test_gap_after_populated_chunk(self):
        r = run("1girl, long hair, blue eyes", arrangement=self.arr())
        self.assertEqual(r.text, "1girl,\nlong hair,\n\nblue eyes")
        self.assertEqual(self.row_ids(r), [["count"], ["hair"], [], ["eyes"]])

    def test_empty_chunk_keeps_gap_between_output(self):
        for rows in ([["count"], ["hair"], ["eyes"]], [["count", "hair", "eyes"]]):
            with self.subTest(rows=rows):
                r = run("1girl, blue eyes", arrangement=self.arr(rows))
                self.assertEqual(r.text, "1girl,\n\nblue eyes")
                self.assertEqual(self.row_ids(r), [["count"], [], ["eyes"]])

    def test_hidden_and_removed_chunks_keep_gap(self):
        for hidden in (False, True):
            arr = self.arr()
            arr["chunks"]["hair"]["hidden"] = hidden
            opts = {} if hidden else {"exclude": "body/hair"}
            with self.subTest(hidden=hidden):
                r = run("1girl, long hair, blue eyes", arrangement=arr, **opts)
                self.assertEqual(r.text, "1girl,\n\nblue eyes")
                self.assertEqual(self.row_ids(r), [["count"], ["hair"], [], ["eyes"]])
                self.assertTrue(r.report["tokens"][1]["removed"])
                arr["chunks"]["count"]["gap_after"] = True
                r = run("1girl, long hair, blue eyes", arrangement=arr, **opts)
                self.assertEqual(r.text, "1girl,\n\nblue eyes")
                self.assertEqual(self.row_ids(r), [["count"], [], ["hair"], [], ["eyes"]])

    def test_no_leading_or_trailing_gap(self):
        arr = self.arr(gaps=("count", "hair", "eyes"))
        for text in ("", "1girl", "blue eyes", "long hair"):
            with self.subTest(text=text):
                r = run(text, arrangement=arr)
                self.assertEqual(r.text, text)
                self.assertNotIn([], self.row_ids(r))
        arr["chunks"]["count"]["hidden"] = True
        r = run("1girl, blue eyes", arrangement=arr)
        self.assertEqual(r.text, "blue eyes")
        self.assertEqual(self.row_ids(r), [["count"], [], ["eyes"]])
        arr["chunks"]["count"]["hidden"] = False
        arr["chunks"]["eyes"]["hidden"] = True
        self.assertEqual(run("1girl, blue eyes", arrangement=arr).text, "1girl")

    def test_overlapping_gaps_collapse(self):
        r = run("1girl, blue eyes", arrangement=self.arr(gaps=("count", "hair")))
        self.assertEqual(r.text, "1girl,\n\nblue eyes")
        self.assertEqual(self.row_ids(r), [["count"], [], ["eyes"]])

    def test_gap_splits_middle_of_row(self):
        r = run("1girl, long hair, blue eyes", arrangement=self.arr([["count", "hair", "eyes"]]))
        self.assertEqual(r.text, "1girl, long hair,\n\nblue eyes")
        self.assertEqual(self.row_ids(r), [["count", "hair"], [], ["eyes"]])

    def test_single_line_ignores_gap(self):
        arr = self.arr([["count", "hair", "eyes"]])
        plain = json.loads(json.dumps(arr))
        for spec in plain["chunks"].values():
            spec.pop("gap_after", None)
        self.assertEqual(run("1girl, long hair, blue eyes, smile", arrangement=arr, line_per_section=False).report["arranged"],
                         run("1girl, long hair, blue eyes, smile", arrangement=plain, line_per_section=False).report["arranged"])
        r = run("1girl, long hair, blue eyes, smile", arrangement=arr, line_per_section=False)
        self.assertEqual(r.text, "1girl, long hair, blue eyes, smile")

    def test_gap_without_trailing_comma(self):
        r = run("1girl, long hair, blue eyes", arrangement=self.arr(), trailing_comma=False)
        self.assertEqual(r.text, "1girl\nlong hair\n\nblue eyes")

    def test_sentence_before_gap_has_no_comma(self):
        arr = arrangement([["sentence", "count"]], {
            "sentence": {"members": ["natural"], "gap_after": True},
            "count": {"members": ["subject/count"]}})
        for comma in (False, True):
            with self.subTest(comma=comma):
                r = run("She holds an umbrella. 1girl", arrangement=arr, trailing_comma=comma)
                self.assertEqual(r.text, "She holds an umbrella.\n\n1girl")
        self.assertEqual(run("She holds an umbrella. 1girl", arrangement=arr, line_per_section=False).text,
                         "She holds an umbrella. 1girl")

    def test_break_sections_do_not_share_gap(self):
        r = run("1girl BREAK blue eyes BREAK 1boy, blue eyes", arrangement=self.arr(gaps=("count", "hair")))
        self.assertEqual(r.text, "1girl\nBREAK\nblue eyes\nBREAK\n1boy,\n\nblue eyes")
        self.assertEqual(self.row_ids(r, 0), [["count"]])
        self.assertEqual(self.row_ids(r, 1), [["eyes"]])
        self.assertEqual(self.row_ids(r, 2), [["count"], [], ["eyes"]])

    def test_gap_before_rest(self):
        r = run("blue eyes, smile", arrangement=self.arr(gaps=("eyes",)))
        self.assertEqual(r.text, "blue eyes,\n\nsmile")
        self.assertEqual(self.row_ids(r), [["eyes"], [], ["rest"]])

    def test_old_arrangement_without_gap_is_unchanged(self):
        arr = self.arr([["count", "hair"], ["eyes"]], gaps=())
        r = run("1girl, long hair, blue eyes", arrangement=arr)
        self.assertEqual(r.text, "1girl, long hair,\nblue eyes")
        self.assertEqual(self.row_ids(r), [["count", "hair"], ["eyes"]])
        arr["chunks"]["hair"]["gap_after"] = False
        self.assertEqual(run("1girl, long hair, blue eyes", arrangement=arr).text, r.text)


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
        r = run("1girl, from above, smile, navel", view="from behind")
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


class ViewRegionTests(unittest.TestCase):
    """시점에 따라 몸의 앞면·뒷면에만 있는 태그가 빠진다."""

    REAR = {"median furrow", "dimples of venus", "ass", "butt crack", "underbutt"}
    FRONT = {"mons pubis", "groin", "pubic tattoo", "navel"}
    PROMPT = "1girl, median furrow, dimples of venus, ass, butt crack, underbutt, mons pubis, groin, pubic tattoo, navel"

    def removed_by(self, **opts):
        return set(reasons(run(self.PROMPT, **opts)))

    def test_front_views_hide_rear_only(self):
        for view in ("straight-on", "front view", "three quarter view"):
            with self.subTest(view=view):
                self.assertEqual(self.removed_by(view=view), self.REAR)

    def test_from_behind_hides_front_only(self):
        self.assertEqual(self.removed_by(view="from behind"), self.FRONT)

    def test_side_and_elevation_hide_nothing(self):
        for view in ("from side", "from above", "from below", "dutch angle", "pov"):
            with self.subTest(view=view):
                self.assertEqual(self.removed_by(view=view), set())

    def test_both_sides_stay_in_every_view(self):
        for view in ("straight-on", "front view", "three quarter view", "from behind", "from side"):
            with self.subTest(view=view):
                self.assertEqual(reasons(run("1girl, wide hips, skirt, shirt", view=view)), {})

    def test_combined_views_keep_the_directional_rule(self):
        self.assertEqual(self.removed_by(view="from behind, from side"), self.FRONT)
        self.assertEqual(self.removed_by(view="from behind, from below"), self.FRONT)
        self.assertEqual(self.removed_by(view="three quarter view, dutch angle"), self.REAR)
        self.assertEqual(self.removed_by(view="front view, from side"), self.REAR)

    def test_rear_side_view_shows_face(self):
        for view in ("from behind, from side", "from behind, profile"):
            with self.subTest(view=view):
                self.assertEqual(reasons(run("1girl, smile, navel", view=view)), {"navel": "rule:view(from behind)"})

    def test_straight_on_with_from_behind_is_a_rear_view(self):
        r = run(self.PROMPT + ", from behind, straight-on")
        self.assertEqual(set(reasons(r)), self.FRONT)

    def test_opposite_views_cancel_without_multiple_views(self):
        self.assertEqual(self.removed_by(view="front view, from behind"), set())

    def test_multiple_views_turns_view_rules_off(self):
        self.assertEqual(self.removed_by(view="front view, from behind, multiple views"), set())
        self.assertEqual(self.removed_by(view="from above, from below, multiple views"), set())

    def test_crop_hides_both_sides_of_the_hips(self):
        r = run("1girl, upper body, ass, groin, wide hips, navel")
        self.assertEqual(set(reasons(r)), {"ass", "groin", "wide hips"})
        r = run("1girl, portrait, ass, groin, wide hips")
        self.assertEqual(set(reasons(r)), {"ass", "groin", "wide hips"})

    def test_lower_body_hides_the_torso(self):
        r = run("1girl, lower body, navel, median furrow, smile, detached sleeves, ass, groin, thighs")
        self.assertEqual(set(reasons(r)), {"navel", "median furrow", "smile", "detached sleeves"})

    def test_back_of_the_legs(self):
        self.assertEqual(reasons(run("1girl, kneepits", view="front view")), {"kneepits": "rule:view(front view)"})
        self.assertEqual(reasons(run("1girl, kneepits", view="from behind")), {})
        self.assertEqual(reasons(run("1girl, kneepits", framing="cowboy shot")), {"kneepits": "rule:framing(cowboy shot)"})
        self.assertEqual(reasons(run("1girl, kneepits", framing="full body")), {})

    def test_several_subjects_turn_view_rules_off(self):
        self.assertEqual(reasons(run("2girls, ass, groin", view="front view")), {})


class ViewChipTests(unittest.TestCase):
    def test_override_takes_several_views(self):
        r = run("1girl, from side, black hair", view="from behind, from below")
        self.assertEqual(reasons(r), {"from side": "override:view"})
        injected = [row["text"] for row in r.report["tokens"] if row.get("injected")]
        self.assertEqual(injected, ["from behind", "from below"])

    def test_multiple_views_alone_keeps_prompt_views(self):
        r = run("1girl, from side, from behind", view="multiple views")
        self.assertEqual(reasons(r), {})
        self.assertIn("multiple views", r.text)
        self.assertFalse(r.report["view_lifted"])                              # 칩을 끄면 다시 막아야 한다

    def test_multiple_views_with_a_view_replaces_prompt_views(self):
        r = run("1girl, from side", view="from behind, multiple views")
        self.assertEqual(reasons(r), {"from side": "override:view"})
        self.assertIn("multiple views", r.text)

    def test_only_listed_lift_tags_pass_the_category_check(self):
        with self.assertRaises(ValueError):
            run("1girl", view="reference sheet")

    def test_view_lifted_follows_the_prompt(self):
        self.assertFalse(run("1girl, from side").report["view_lifted"])
        self.assertTrue(run("1girl, mirror, from side").report["view_lifted"])
        self.assertTrue(run("1girl, multiple views", view="multiple views").report["view_lifted"])

    def toggle(self, selected, tag, lifted=False):
        return core.view_toggle(core.palette(pack()), selected, tag, lifted)

    def test_toggle_combines_compatible_views(self):
        self.assertEqual(self.toggle(["from behind"], "from side"), ["from side", "from behind"])
        self.assertEqual(self.toggle(["from behind"], "from below"), ["from behind", "from below"])
        self.assertEqual(self.toggle(["three quarter view"], "dutch angle"), ["three quarter view", "dutch angle"])
        self.assertEqual(self.toggle(["front view", "from side", "pov"], "dutch angle"),
                         ["front view", "from side", "pov", "dutch angle"])

    def test_toggle_turns_conflicts_off(self):
        self.assertEqual(self.toggle(["front view"], "from behind"), ["from behind"])
        self.assertEqual(self.toggle(["from behind", "from side"], "straight-on"), ["straight-on", "from side"])
        self.assertEqual(self.toggle(["from above"], "from below"), ["from below"])
        self.assertEqual(self.toggle(["from behind"], "three quarter view"), ["three quarter view"])

    def test_toggle_off_removes(self):
        self.assertEqual(self.toggle(["front view", "from side"], "from side"), ["front view"])

    def test_multiple_views_lifts_conflicts(self):
        self.assertEqual(self.toggle(["front view", "multiple views"], "from behind"),
                         ["front view", "from behind", "multiple views"])
        self.assertEqual(self.toggle(["from above", "from below"], "multiple views"),
                         ["from above", "from below", "multiple views"])
        self.assertEqual(self.toggle(["front view"], "from behind", lifted=True), ["front view", "from behind"])

    def test_turning_multiple_views_off_keeps_the_first_of_a_conflict(self):
        self.assertEqual(self.toggle(["front view", "from behind", "multiple views"], "multiple views"), ["front view"])
        self.assertEqual(self.toggle(["front view", "from behind", "multiple views"], "multiple views", lifted=True),
                         ["front view", "from behind"])


class GazeTests(unittest.TestCase):
    def test_replaces_gaze_tags_with_several_chosen(self):
        r = run("1girl, looking down, smile", gaze="looking back, looking at viewer")
        self.assertEqual(reasons(r), {"looking down": "override:gaze"})
        injected = [(row["text"], row["injected"]) for row in r.report["tokens"] if row.get("injected")]
        self.assertEqual(injected, [("looking back", "gaze"), ("looking at viewer", "gaze")])

    def test_facing_hides_tags_that_need_open_eyes(self):
        reason = "rule:gaze(facing viewer)"
        r = run("1girl, looking at viewer, blue eyes, closed eyes, one eye closed, smile", gaze="facing viewer")
        self.assertEqual(reasons(r), {"looking at viewer": "override:gaze", "blue eyes": reason, "one eye closed": reason})
        self.assertNotIn("blue eyes", r.text)
        self.assertIn("closed eyes", r.text)
        self.assertIn("smile", r.text)

    def test_facing_typed_in_prompt_counts_too(self):
        r = run("1girl, facing viewer, blue eyes")
        self.assertEqual(reasons(r), {"blue eyes": "rule:gaze(facing viewer)"})

    def test_facing_with_looking_keeps_eyes(self):
        for prompt, gaze in (("1girl, blue eyes", "facing to the side, looking up"), ("1girl, facing viewer, looking up, blue eyes", ""),
                             ("1girl, facing viewer, sideways glance, blue eyes", "")):
            with self.subTest(prompt=prompt, gaze=gaze):
                self.assertEqual(reasons(run(prompt, gaze=gaze)), {})

    def test_facing_rule_follows_modes_and_exemptions(self):
        self.assertEqual(reasons(run("1girl, facing viewer, blue eyes", rules="off")), {})
        reported = run("1girl, facing viewer, blue eyes", rules="report")
        self.assertEqual(reasons(reported), {})
        self.assertIn("blue eyes", reported.text)
        self.assertEqual(reasons(run("1girl, facing viewer, blue eyes", rules={"gaze": "off"})), {})
        self.assertEqual(reasons(run("2girls, facing viewer, blue eyes")), {})         # 다른 사람의 눈일 수 있다
        self.assertEqual(reasons(run("1girl, multiple views, facing viewer, blue eyes")), {})

    def test_facing_about_to_be_blocked_does_not_hide_eyes(self):
        r = run("1girl, straight-on, facing away, blue eyes")
        self.assertEqual(reasons(r), {"facing away": "rule:view(straight-on)"})
        self.assertIn("blue eyes", r.text)
        r = run("1girl, straight-on, facing away, facing viewer, blue eyes")               # 남는 facing 만 기준이 된다
        self.assertEqual(reasons(r), {"facing away": "rule:view(straight-on)", "blue eyes": "rule:gaze(facing viewer)"})
        reported = run("1girl, straight-on, facing away, blue eyes", rules={"view": "report"})   # 표시만 하면 facing 이 남는다
        self.assertEqual(reasons(reported), {"blue eyes": "rule:gaze(facing away)"})

    def test_keep_only_spares_tags_from_its_own_group(self):
        r = run("1girl, closed eyes, facing viewer, head out of frame")
        self.assertEqual(reasons(r), {"closed eyes": "rule:framing(head out of frame)"})

    def test_must_be_gaze_tag(self):
        with self.assertRaises(ValueError) as cm:
            run("1girl", gaze="smile")
        self.assertIn("action/gaze", str(cm.exception))

    def test_from_behind_keeps_face_only_when_head_turned(self):
        for gaze in ("looking back", "looking at viewer", "looking back, sideways glance", "facing back"):
            with self.subTest(gaze=gaze):
                r = run("1girl, smile, navel", view="from behind", gaze=gaze)
                self.assertEqual(reasons(r), {"navel": "rule:view(from behind)"})
        r = run("1girl, smile, navel", view="from behind", gaze="facing away")
        self.assertEqual(reasons(r), {"smile": "rule:view(from behind)", "navel": "rule:view(from behind)"})

    def test_straight_on_removes_turned_head_tags(self):
        r = run("1girl, straight-on, looking back, facing away, looking at viewer")
        self.assertEqual(reasons(r), {"looking back": "rule:view(straight-on)", "facing away": "rule:view(straight-on)"})
        self.assertEqual(r.report["gaze_blocked"], ["looking back", "facing away", "facing back", "facing to the side"])

    def test_view_chip_also_drives_blocks(self):
        r = run("1girl, from behind, looking back", view="straight-on")
        self.assertEqual(reasons(r), {"from behind": "override:view", "looking back": "rule:view(straight-on)"})
        self.assertIn("looking back", r.report["gaze_blocked"])

    def test_blocked_choice_is_ignored_and_prompt_gaze_stays(self):
        r = run("1girl, looking up", view="straight-on", gaze="looking back")
        self.assertEqual(reasons(r), {})
        self.assertNotIn("looking back", r.text)
        self.assertIn("looking up", r.text)

    def test_only_allowed_choices_replace(self):
        r = run("1girl, looking up", view="straight-on", gaze="looking back, looking at viewer")
        self.assertEqual(reasons(r), {"looking up": "override:gaze"})
        self.assertIn("looking at viewer", r.text)
        self.assertNotIn("looking back", r.text)

    def test_side_and_behind_block_nothing(self):
        for view in ("from side", "from behind"):
            with self.subTest(view=view):
                r = run("1girl, looking back, sideways glance", view=view)
                self.assertEqual(reasons(r), {})
                self.assertEqual(r.report["gaze_blocked"], [])

    def test_several_view_tags_block_only_what_all_block(self):
        r = run("1girl, from behind, straight-on, facing away")
        self.assertEqual(reasons(r), {})
        self.assertEqual(r.report["gaze_blocked"], [])
        r = run("1girl, profile, straight-on, facing viewer, facing away")
        self.assertEqual(reasons(r), {"facing away": "rule:view(profile, straight-on)"})

    def test_profile_blocks_frontal_faces(self):
        r = run("1girl, profile, facing viewer, looking at viewer")
        self.assertEqual(reasons(r), {"facing viewer": "rule:view(profile)"})

    def test_head_out_of_frame_blocks_only_eyes_visible_tags(self):
        for framing in ("head out of frame", "lower body"):
            with self.subTest(framing=framing):
                r = run(f"1girl, {framing}, looking at viewer, facing viewer")
                self.assertEqual(reasons(r), {"looking at viewer": f"rule:framing({framing})"})
                self.assertFalse(any(t.startswith("facing") for t in r.report["gaze_blocked"]))
                self.assertEqual(len(r.report["gaze_blocked"]), 8)

    def test_exempt_scenes_block_nothing(self):
        for text in ("2girls, straight-on, looking back", "2girls, head out of frame, looking at viewer",
                     "1girl, 1boy, straight-on, looking back", "1girl, multiple views, straight-on, looking back"):
            with self.subTest(text=text):
                r = run(text)
                self.assertEqual(reasons(r), {})
                self.assertEqual(r.report["gaze_blocked"], [])

    def test_rule_modes(self):
        r = run("1girl, straight-on", rules="off", gaze="looking back")
        self.assertEqual(reasons(r), {})
        self.assertIn("looking back", r.text)
        self.assertEqual(r.report["gaze_blocked"], [])
        r = run("1girl, straight-on", rules="report", gaze="looking back")
        self.assertIn("looking back", r.text)
        self.assertEqual(r.report["flagged"], [{"tag": "looking back", "reason": "rule:view(straight-on)"}])

    def test_only_first_break_segment_decides_chips(self):
        r = run("1girl, straight-on BREAK 1boy, looking back")
        self.assertEqual(reasons(r), {})
        self.assertIn("looking back", r.report["gaze_blocked"])
        r = run("1girl BREAK 1boy, straight-on, looking back")
        self.assertEqual(reasons(r), {"looking back": "rule:view(straight-on)"})
        self.assertEqual(r.report["gaze_blocked"], [])

    def test_view_chip_keeps_facing_tags(self):
        r = run("1girl, from above, facing away", view="from side")
        self.assertEqual(reasons(r), {"from above": "override:view"})

    def test_toggle_turns_off_conflicts_both_ways(self):
        pal = core.palette(pack())
        toggle = core.gaze_toggle
        self.assertEqual(toggle(pal, [], "looking back"), ["looking back"])
        self.assertEqual(toggle(pal, ["looking back"], "looking at viewer"), ["looking at viewer", "looking back"])
        self.assertEqual(toggle(pal, ["looking back", "looking at viewer"], "looking back"), ["looking at viewer"])
        self.assertEqual(toggle(pal, ["looking up"], "looking down"), ["looking down"])
        self.assertEqual(toggle(pal, ["looking down"], "looking up"), ["looking up"])
        self.assertEqual(toggle(pal, ["looking back", "looking at viewer"], "facing away"), ["facing away"])
        self.assertEqual(toggle(pal, ["facing away"], "looking back"), ["looking back"])
        self.assertEqual(toggle(pal, ["looking back"], "sideways glance"), ["looking back", "sideways glance"])

    def test_rule_tags_are_real_gaze_tags(self):
        rows = tagdb.read_overlay()
        rules = tagdb.load_config()["rules"]
        quick = rules["quick"]
        for tag in quick["gaze"]:
            self.assertEqual(rows[tag.replace(" ", "_")]["primary"], "action/gaze", tag)
        named = {name for tag, others in quick["gaze_conflicts"].items() for name in (tag, *others)}
        for group in ("framing", "view"):
            named |= {tag for block in rules[group]["blocks"] for tag in block["block"]}
        self.assertEqual(named - set(quick["gaze"]) - set(quick["pose"]), set())


class PoseTests(unittest.TestCase):
    def test_replaces_pose_tags_with_several_chosen(self):
        r = run("1girl, sitting, wariza, smile", pose="lying, on back")
        self.assertEqual(reasons(r), {"sitting": "override:pose", "wariza": "override:pose"})
        injected = [(row["text"], row["injected"]) for row in r.report["tokens"] if row.get("injected")]
        self.assertEqual(injected, [("lying", "pose"), ("on back", "pose")])

    def test_place_and_restraint_tags_stay(self):
        r = run("1girl, sitting, on bed, on bench, bound", pose="kneeling")
        self.assertEqual(reasons(r), {"sitting": "override:pose"})
        for tag in ("on bed", "on bench", "bound", "kneeling"):
            self.assertIn(tag, r.text)

    def test_pose_tags_outside_the_chips_are_replaced(self):
        r = run("1girl, yokozuwari, sitting on stairs", pose="standing")
        self.assertEqual(reasons(r), {"yokozuwari": "override:pose", "sitting on stairs": "override:pose"})

    def test_chosen_tag_already_in_prompt_stays_in_place(self):
        r = run("1girl, lying, sitting", pose="lying")
        self.assertEqual(reasons(r), {"sitting": "override:pose"})
        self.assertEqual([row for row in r.report["tokens"] if row.get("injected")], [])

    def test_alias_is_replaced_too(self):
        r = run("1girl, crouching", pose="standing")
        self.assertEqual(reasons(r), {"squatting": "override:pose"})

    def test_only_first_section_gets_injected(self):
        r = run("1girl, sitting BREAK smile BREAK lying", pose="standing")
        kept = [row["text"] for row in r.report["tokens"] if not row.get("removed")]
        self.assertEqual(kept.count("standing"), 2)
        self.assertEqual(reasons(r), {"sitting": "override:pose", "lying": "override:pose"})

    def test_must_be_posture_tag(self):
        with self.assertRaises(ValueError) as cm:
            run("1girl", pose="smile")
        self.assertIn("action/posture", str(cm.exception))

    def test_blocked_by_face_crops(self):
        for crop in ("portrait", "close-up"):
            with self.subTest(crop=crop):
                r = run("1girl, smile", framing=crop, pose="standing")
                self.assertNotIn("standing", r.text)
                self.assertEqual(r.report["pose_blocked"], list(core.palette(pack())["quick"]["pose"]))

    def test_prompt_pose_is_dropped_under_face_crop_and_place_stays(self):
        r = run("1girl, portrait, sitting, on bed")
        self.assertEqual(reasons(r), {"sitting": "rule:framing(portrait)"})

    def test_other_crops_do_not_block(self):
        for crop in ("upper body", "cowboy shot", "full body", "lower body"):
            with self.subTest(crop=crop):
                r = run("1girl", framing=crop, pose="kneeling")
                self.assertIn("kneeling", r.text)
                self.assertEqual(r.report["pose_blocked"], [])

    def test_block_follows_rule_modes_and_people(self):
        r = run("1girl, portrait", rules="off", pose="sitting")
        self.assertIn("sitting", r.text)
        self.assertEqual(r.report["pose_blocked"], [])
        r = run("2girls, portrait", pose="sitting")
        self.assertIn("sitting", r.text)
        self.assertEqual(r.report["pose_blocked"], [])
        r = run("1girl, portrait, multiple views", pose="sitting")
        self.assertIn("sitting", r.text)

    def test_rating_filter_applies_to_chosen_pose(self):
        r = run("1girl", pose="all fours", max_rating="g")
        self.assertNotIn("all fours", r.text)
        self.assertIn("all fours", r.report["removed"][0]["tag"])

    def test_old_pack_without_scope_only_adds(self):
        data = build()[0]
        del data["rules"]["quick"]["pose"], data["rules"]["quick"]["pose_scope"]
        r = core.organize("1girl, sitting, on bed", core.TagPack(data), {"pose": "kneeling"})
        self.assertEqual({row["tag"] for row in r.report["removed"]}, set())
        self.assertIn("kneeling", r.text)

    def test_toggle_keeps_implied_pairs_and_turns_off_conflicts_both_ways(self):
        pal = core.palette(pack())
        toggle = core.pose_toggle
        self.assertEqual(toggle(pal, [], "lying"), ["lying"])
        self.assertEqual(toggle(pal, ["lying"], "on back"), ["lying", "on back"])
        self.assertEqual(toggle(pal, ["lying", "on back"], "on back"), ["lying"])
        self.assertEqual(toggle(pal, ["on back"], "on stomach"), ["on stomach"])
        self.assertEqual(toggle(pal, ["sitting"], "wariza"), ["sitting", "wariza"])
        self.assertEqual(toggle(pal, ["wariza"], "seiza"), ["seiza"])
        self.assertEqual(toggle(pal, ["sitting", "wariza"], "standing"), ["standing"])
        self.assertEqual(toggle(pal, ["standing"], "sitting"), ["sitting"])
        self.assertEqual(toggle(pal, ["kneeling"], "on one knee"), ["kneeling", "on one knee"])
        self.assertEqual(toggle(pal, ["kneeling"], "squatting"), ["squatting"])
        self.assertEqual(toggle(pal, ["standing"], "leaning forward"), ["standing", "leaning forward"])
        self.assertEqual(toggle(pal, ["leaning forward"], "leaning back"), ["leaning back"])
        self.assertEqual(toggle(pal, ["lying"], "leaning back"), ["leaning back"])
        self.assertEqual(toggle(pal, ["standing on one leg"], "contrapposto"), ["contrapposto"])
        self.assertEqual(toggle(pal, ["standing"], "contrapposto"), ["standing", "contrapposto"])

    def test_rule_tags_are_real_posture_tags(self):
        rows = {**tagdb.read_overlay(tagdb.E621_OVERLAY, tagdb.SITE_FIELDS), **tagdb.read_overlay()}
        quick = tagdb.load_config()["rules"]["quick"]
        self.assertEqual(len(quick["pose"]), 17)
        for tag in quick["pose"] + quick["pose_scope"]["tags"]:
            row = rows[tag.replace(" ", "_")]
            primary = rows[row["same_as"]]["primary"] if row.get("same_as") else row["primary"]
            self.assertEqual(primary, "action/posture", tag)
        named = {name for tag, others in quick["pose_conflicts"].items() for name in (tag, *others)}
        self.assertEqual(named - set(quick["pose"]), set())
        self.assertEqual(set(quick["pose"]) & set(quick["pose_scope"]["tags"]), set())
        for tag in ("on bed", "bound", "against wall", "floating"):
            self.assertNotIn(tag, quick["pose_scope"]["tags"])


class ChipLayoutTests(unittest.TestCase):
    def test_grid_and_groups_place_each_quick_tag_once(self):
        quick = tagdb.load_config()["rules"]["quick"]
        grid = quick["gaze_grid"]
        self.assertEqual(sorted(t for col in grid for t in (col.get("looking"), col.get("facing")) if t), sorted(quick["gaze"]))
        for col in grid:
            self.assertTrue(col["label"], col)
            self.assertTrue(col.get("looking") or col.get("facing"), col)
            self.assertFalse((col.get("looking") or "").startswith("facing"), col)
            self.assertTrue((col.get("facing") or "facing").startswith("facing"), col)
        groups = quick["pose_groups"]
        self.assertEqual([t for g in groups for t in g["tags"]], quick["pose"])
        self.assertTrue(all(g["label"] for g in groups))

    def test_builder_warns_about_misplaced_layout_tags(self):
        config = tagdb.load_config()
        quick = config["rules"]["quick"]
        quick["gaze_grid"] = quick["gaze_grid"] + [{"label": "x", "looking": "looking at viewer", "facing": "facing nowhere"}]
        quick["pose_groups"] = quick["pose_groups"] + [{"label": "y", "tags": ["flying"]}]
        _, warnings = build(config=config)
        self.assertIn("rules.quick.gaze_grid 'looking at viewer' 가 두 번 나옵니다", warnings)
        self.assertIn("rules.quick.gaze_grid 'facing nowhere' 는 gaze 퀵 목록에 없습니다", warnings)
        self.assertIn("rules.quick.pose_groups 'flying' 는 pose 퀵 목록에 없습니다", warnings)
        _, clean = build()
        self.assertFalse([w for w in clean if "gaze_grid" in w or "pose_groups" in w], clean)

    def test_old_packs_have_empty_layouts(self):
        data, _ = build()
        del data["rules"]["quick"]["gaze_grid"], data["rules"]["quick"]["pose_groups"]
        pal = core.palette(core.TagPack(data))
        self.assertEqual((pal["gaze_grid"], pal["pose_groups"]), ([], []))


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
        self.assertIn("looking back", pal["quick"]["gaze"])
        self.assertIn("facing back", pal["gaze_conflicts"]["looking back"])
        self.assertIn({"label": "곁눈", "looking": "sideways glance"}, pal["gaze_grid"])
        self.assertEqual(pal["pose_groups"][0], {"label": "서기", "tags": ["standing", "standing on one leg", "contrapposto"]})
        self.assertIn("default", pal["layouts"])
        self.assertEqual(core.selector_label("subject/count", pack().taxonomy), "인원")
        self.assertEqual(core.selector_label("clothes", pack().taxonomy), "의상")
        json.dumps(pal)


if __name__ == "__main__":
    unittest.main()
