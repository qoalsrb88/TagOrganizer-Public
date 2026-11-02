"""프롬프트 문법 테스트 — // 주석, __와일드카드__·{a|b}, 문장 끝 마침표(자연어 문장), 입력 문법 자동 인식.

공용 픽스처에 마침표·// 가 든 이름과 종족 태그를 더한 작은 팩을 이 파일 안에서 만든다(다른 테스트의 팩은 그대로).
"""

from __future__ import annotations

import unittest

from fixtures import EXTRA, SOURCE, builder, core, overlay_rows, source_tags, tagdb
from tools.tagdb import SourceTag

MORE = [
    ("st._louis_(azur_lane)", 4, 300), ("monkey_d._luffy", 4, 500), ("super_smash_bros.", 3, 400),
    ("a.i._voice", 3, 100), (".hack//sign", 3, 50), ("sp//dr", 1, 10),
    ("z.a.t.o._//_i_love_the_world_and_everything_in_it", 3, 5),
    ("cat_girl", 0, 900), ("dog_girl", 0, 400), ("snow_leopard_girl", 0, 20), ("cat", 0, 800), ("sheep", 0, 100),
    ("armpit_hair", 0, 50), ("red_eyes", 0, 1000), ("boy", 0, 10),
]
GENERAL = {   # 이름 → (primary, regions, identity)
    "cat_girl": ("subject/species", "", "1"), "dog_girl": ("subject/species", "", "1"),
    "snow_leopard_girl": ("subject/species", "", "1"), "cat": ("object/animal", "", ""),
    "sheep": ("object/animal", "", ""), "armpit_hair": ("body/lower", "arms", ""),
    "red_eyes": ("body/eyes", "eyes", "1"), "boy": ("subject/count", "", ""),
}

_PACK = None


def pack() -> core.TagPack:
    global _PACK
    if _PACK is None:
        rows = overlay_rows({name: {"name": name, "source": "danbooru", "primary": primary, "rating": "g",
                                    "regions": regions, "identity": identity, "status": "reviewed", "secondary": "",
                                    "by": "test", "confidence": "", "note": "", "missing_since": ""}
                             for name, (primary, regions, identity) in GENERAL.items()})
        tags = sorted(source_tags() + [SourceTag(n, t, c, ()) for n, t, c in MORE], key=lambda s: -s.count)
        meta = {"file": "fixture.csv", "date": "2026-10-01", "rows": len(SOURCE) + len(MORE)}
        _PACK = core.TagPack(builder.build_pack(tags, meta, rows, EXTRA, tagdb.load_config())[0])
    return _PACK


def run(text, **opts):
    return core.organize(text, pack(), opts)


def kinds(result):
    return [(t["text"], t["kind"]) for t in result.report["tokens"]]


class CommentTests(unittest.TestCase):
    def test_comments_are_removed_by_default(self):
        r = run("// pose block\n1girl, smile // note, here\nblush", line_per_section=False)
        self.assertEqual(r.text, "1girl, smile, blush")
        self.assertEqual(r.report["counts"]["comments"], 2)
        self.assertEqual(r.report["counts"]["unknown"], 0)                     # 주석은 팩에 없음으로 세지 않음
        self.assertEqual([x["reason"] for x in r.report["removed"]], ["comment", "comment"])
        self.assertIn("// note, here  # comment", r.removed_text)
        self.assertEqual(r.report["arranged"][0]["rows"][0][0]["id"], "comment")  # 색 미리보기 맨 위 줄

    def test_kept_comments_get_their_own_lines(self):
        r = run("1girl, smile // note, here", comments="keep", line_per_section=False)
        self.assertEqual(r.text, "// note, here\n1girl, smile")
        r = run("1girl // only\nBREAK\n// second\nBREAK\nsmile", comments="keep", line_per_section=False)
        # 주석만 있는 구간도 줄을 바꿔 BREAK·다음 태그가 주석에 묻히지 않게
        self.assertEqual(r.text, "// only\n1girl BREAK // second\n BREAK smile")
        r = run("// head\nsmile, 1girl", comments="keep")
        self.assertEqual(r.text, "// head\n1girl,\nsmile")

    def test_break_inside_comment_does_not_split(self):
        r = run("1girl // use BREAK later\nsmile")
        self.assertNotIn("BREAK", r.text)
        self.assertEqual(len(r.report["arranged"]), 1)

    def test_double_slash_inside_names_is_not_a_comment(self):
        r = run(".hack//sign, sp//dr, z.a.t.o. // i love the world and everything in it, smile")
        self.assertEqual(r.report["counts"]["comments"], 0)
        self.assertEqual(r.report["counts"]["unknown"], 0)
        r = run("she is happy. z.a.t.o. // i love the world and everything in it")   # 문장 뒤에 붙은 이름도
        self.assertEqual(r.report["counts"]["comments"], 0)
        r = run("see https://example.com/x, smile")
        self.assertEqual(r.report["counts"]["comments"], 0)

    def test_weighted_names_with_double_slash_are_not_comments(self):
        # 리뷰 1: (이름:1.2)·1.2::이름::·{이름} 처럼 감싼 팩 이름의 // 를 주석으로 지워 뒤 내용까지 잃었다
        name = "z.a.t.o. // i love the world and everything in it"
        for text, dialect in ((f"({name}:1.2), smile", "comfyui"), (f"1.2::{name}::, smile", "nai"),
                              ("{" + name + "}, smile", "nai")):
            r = run(text, input_dialect=dialect, sentences=False)
            self.assertEqual(r.report["counts"]["comments"], 0, text)
            self.assertEqual([t["text"] for t in r.report["tokens"]], [name, "smile"], text)
        self.assertEqual(run(f"({name}:1.2), smile", input_dialect="comfyui").report["tokens"][0]["weight"], 1.2)

    def test_comment_text_is_not_parsed(self):
        r = run("smile // (blush:1.2), {x|y}", input_dialect="auto")
        self.assertNotIn("comfyui", r.report["dialect"]["evidence"])
        self.assertEqual(r.text, "smile")


class WildcardTests(unittest.TestCase):
    def test_segments_with_wildcards_are_syntax_tokens(self):
        r = run("__haircolor__ hair, __pose__, smile, a __x__ b")
        self.assertEqual([k for _, k in kinds(r)], ["special", "special", "tag", "special"])
        self.assertEqual(r.report["counts"]["unknown"], 0)
        self.assertIn("__haircolor__ hair", r.text)

    def test_guess_places_wildcard_by_its_words(self):
        r = run("smile, __haircolor__ hair, __pose__", guess=True)
        row = r.report["tokens"][1]
        self.assertEqual((row["kind"], row["primary"], row["guess"]), ("special", "body/hair", "pattern:* hair"))
        self.assertEqual(row["section"], "hair")
        self.assertEqual(r.report["tokens"][2]["primary"], "special")             # 와일드카드뿐이면 문법 토큰 자리
        self.assertEqual(run("__haircolor__ hair").report["tokens"][0]["primary"], "special")   # 추정 끔

    def test_dynamic_choice_with_one_category_is_classified(self):
        r = run("1girl, {aqua|blue} eyes, smile")
        row = r.report["tokens"][1]
        self.assertEqual((row["kind"], row["primary"], row["guess"]),
                         ("special", "body/eyes", "choice:aqua eyes|blue eyes"))
        self.assertEqual(row["out"], "{aqua|blue} eyes")
        swap = run("1girl, {aqua|blue} eyes", preset="character_swap")          # 고유 외형이라 캐릭터 교체에서 빠짐
        self.assertEqual(swap.report["tokens"][1]["removed"], "exclude:*@identity")

    def test_dynamic_choice_stays_special_when_unsure(self):
        for text in ("{blue eyes|smile}", "{aqua|purple} eyes", "{2$$aqua|blue} eyes", "{__c__|blue} eyes"):
            self.assertEqual(run(text).report["tokens"][0]["primary"], "special", text)
        self.assertEqual(run("{smile}", input_dialect="nai").report["tokens"][0]["primary"], "action/expression")


class SentenceTests(unittest.TestCase):
    def test_sentence_period_splits_like_comma(self):
        r = run("1girl, standing in the rain. she holds an umbrella. smile")
        self.assertEqual(kinds(r), [("1girl", "tag"), ("standing in the rain.", "natural"),
                                    ("she holds an umbrella.", "natural"), ("smile", "tag")])
        self.assertEqual(r.report["counts"]["sentences"], 2)
        self.assertEqual(r.report["counts"]["unknown"], 0)
        # 문장은 자연어 청크에 입력 순서대로, 빈칸으로 잇고 문장 뒤에는 쉼표를 붙이지 않는다
        self.assertEqual(r.text, "1girl,\nsmile,\nstanding in the rain. she holds an umbrella.")
        r = run("standing in the rain. odd thing, 1girl", line_per_section=False)
        self.assertEqual(r.text, "1girl, standing in the rain. odd thing")

    def test_lone_period_is_kept(self):
        r = run("1girl, ., smile")
        self.assertIn(".", [t["text"] for t in r.report["tokens"]])            # 조용히 버리지 않음

    def test_other_dialect_weights_do_not_hide_names(self):
        # 자체 점검: ComfyUI·Anima 입력에 NAI 식 가중치를 쓰면 0.8:: 가 붙은 단어 때문에 이름 검사가 빗나가 나뉨
        for text in ("0.8::monkey d. luffy::, smile", "{st. louis (azur lane)}, smile", "2::dr. nadja::, smile"):
            r = run(text, input_dialect="anima")
            self.assertEqual(r.report["counts"]["sentences"], 0, text)
            self.assertEqual(len(r.report["tokens"]), 2, text)

    def test_tag_with_period_drops_only_the_period(self):
        self.assertEqual(kinds(run("smile. blush")), [("smile", "tag"), ("blush", "tag")])
        self.assertEqual(kinds(run("long hair.")), [("long hair", "tag")])

    def test_names_with_periods_are_kept(self):
        text = "st. louis (azur lane), monkey d. luffy, super smash bros., a.i. voice, u.a. thing"
        r = run(text, input_dialect="nai")
        self.assertEqual([t for t, _ in kinds(r)][:4],
                         ["st. louis (azur lane)", "monkey d. luffy", "super smash bros.", "a.i. voice"])
        self.assertEqual(r.report["counts"]["sentences"], 0)
        r = run("she smiles. monkey d. luffy, super smash bros. smile", input_dialect="nai")
        self.assertEqual(kinds(r)[:2], [("she smiles.", "natural"), ("monkey d. luffy", "tag")])
        self.assertEqual(r.report["tokens"][2]["text"], "super smash bros.")    # 이름 끝 마침표 뒤에서 나눔
        self.assertEqual(r.report["tokens"][3]["text"], "smile")

    def test_periods_that_are_not_sentence_ends(self):
        self.assertEqual(kinds(run("(smile:1.5), ...")), [("smile", "tag"), ("...", "unknown")])
        self.assertEqual(kinds(run("1.5::a girl walks. she smiles::", input_dialect="nai")),
                         [("a girl walks. she smiles", "unknown")])             # 강조 안은 나누지 않음
        self.assertEqual(kinds(run("(a girl walks. she smiles:1.2)"))[0][1], "unknown")

    def test_sentences_can_be_turned_off(self):
        r = run("1girl, standing in the rain. she holds an umbrella. smile", sentences=False)
        self.assertEqual(kinds(r)[1], ("standing in the rain. she holds an umbrella. smile", "unknown"))

    def test_sorting_keeps_sentence_order(self):
        r = run("zebra walks here. apple falls down. smile, blush", order="alpha", line_per_section=False)
        self.assertEqual(r.text, "blush, smile, zebra walks here. apple falls down.")

    def test_sentence_chunk_in_arrangement(self):
        arr = core.arrangement_from_layout(pack(), "default")
        self.assertIn("natural", arr["chunks"])
        self.assertNotIn("natural", core.uncovered_slots(arr, pack().taxonomy))
        r = run("she smiles. 1girl", arrangement=arr)
        self.assertEqual(r.report["tokens"][0]["section"], "natural")


class GuessTests(unittest.TestCase):
    """[묘사] [종족] girl|boy 같은 종족 추정, 그리고 기존 태그로 정답률을 잰 패턴."""

    def guess(self, text):
        row = run(text, guess=True).report["tokens"][0]
        return row["primary"], row.get("guess", "")

    def test_species_from_known_species_tail(self):
        self.assertEqual(self.guess("russian blue cat girl"), ("subject/species", "species:cat girl"))
        self.assertEqual(self.guess("huge dog girl"), ("subject/species", "species:dog girl"))
        self.assertEqual(self.guess("tiny snow leopard girl"), ("subject/species", "species:snow leopard girl"))
        r = run("russian blue cat girl, smile", guess=True, preset="character_swap")
        self.assertEqual(r.report["tokens"][0]["removed"], "exclude:*@identity")    # 종족은 고유 외형

    def test_species_from_animal(self):
        self.assertEqual(self.guess("merino sheep girl"), ("subject/species", "animal:sheep"))
        self.assertEqual(self.guess("cat boy"), ("subject/species", "animal:cat"))
        self.assertTrue(run("merino sheep girl", guess=True).report["tokens"][0].get("guess"))

    def test_species_needs_girl_or_boy(self):
        self.assertEqual(self.guess("merino sheep"), ("object/animal", "head:sheep"))   # girl·boy 가 아니면 동물 그대로
        self.assertEqual(self.guess("baseball girl"), ("unknown", ""))          # 앞부분이 동물·종족이 아님
        self.assertEqual(self.guess("merino sheep girl")[0], "subject/species")
        self.assertEqual(run("merino sheep girl").report["tokens"][0]["primary"], "unknown")   # 추정 끔

    def test_longer_head_beats_fixed_suffix_pattern(self):
        # `* hair` 패턴(머리카락)보다 그 끝말로 끝나는 더 긴 태그(armpit hair)가 맞다
        self.assertEqual(self.guess("thick armpit hair"), ("body/lower", "head:armpit hair"))
        self.assertEqual(self.guess("glowing closed eyes"), ("action/expression", "head:closed eyes"))
        self.assertEqual(self.guess("crimson hair"), ("body/hair", "pattern:* hair"))
        self.assertEqual(self.guess("holding shirt"), ("action/holding", "pattern:holding *"))   # 앞이 정해진 패턴

    def test_build_checks_species_config(self):
        config = tagdb.load_config()
        self.assertEqual(config["guess"]["species"]["primary"], "subject/species")   # 팩까지 간다
        config["guess"] = dict(config["guess"], species={"suffixes": ["girl"], "primary": "subject/nope",
                                                         "animals": ["unknown"]})
        with self.assertRaises(builder.BuildError) as ctx:
            builder.build_pack(source_tags(), {"file": "f.csv", "date": "2026-10-01", "rows": len(SOURCE)},
                               overlay_rows(), EXTRA, config)
        self.assertIn("guess.species.primary", str(ctx.exception.errors))
        self.assertIn("guess.species.animals", str(ctx.exception.errors))

    def test_new_patterns(self):
        for text, primary in (("hand on own knee", "action"), ("grabbing tail", "action"),
                              ("pointing at moon", "action/gesture"), ("axolotl print", "clothes/detail"),
                              ("elbow focus", "composition/focus"), ("lotus tattoo", "body/skin"),
                              ("star censor", "meta/technical"), (r"saber \(cosplay\)", "clothes"),
                              (r"mochi \(food\)", "object/food")):
            self.assertEqual(self.guess(text)[0], primary, text)


class DialectTests(unittest.TestCase):
    def detect(self, text, **kw):
        return core.detect_dialect(text, **kw)["dialect"]

    def test_signs(self):
        self.assertEqual(self.detect("1.1::smile::, 1girl"), "nai")
        self.assertEqual(self.detect("{smile}, [blush]"), "nai")
        self.assertEqual(self.detect("source#hug, 1girl"), "nai")
        self.assertEqual(self.detect("(smile:1.2), 1girl"), "comfyui")
        self.assertEqual(self.detect(r"star \(symbol\)"), "comfyui")
        self.assertEqual(self.detect("<lora:x:0.8>, {red|blue} hair"), "comfyui")
        self.assertEqual(self.detect("@wlop, 1girl"), "anima")
        # Anima 는 ComfyUI 의 상위 호환 — @작가가 있으면 ComfyUI 표기와 같이 있어도 anima
        self.assertEqual(self.detect("@wlop, (smile:1.2)"), "anima")
        self.assertEqual(self.detect("@_@, smile", kaomoji={"@_@"}), "nai")
        self.assertEqual(self.detect("star (symbol), hatsune miku (cosplay)"), "nai")   # 숫자 없는 괄호는 신호 아님

    def test_break_is_a_boundary_for_signs(self):
        # 리뷰 2: BREAK 바로 뒤의 @작가를 놓쳐 NAI 로 읽고 작가를 팩에 없음으로 뒀다
        self.assertEqual(self.detect("1girl BREAK @wlop"), "anima")
        r = run("1girl BREAK @wlop", input_dialect="auto", output_dialect="comfyui", line_per_section=False)
        self.assertEqual(r.text, "1girl BREAK artist:wlop")

    def test_comfy_weights_that_float_reads(self):
        # 리뷰 3: 파서는 float() 로 .8·+1.2·1e-1 을 읽는데 자동 인식은 놓쳤다
        for text in ("(smile:.8)", "(smile:+1.2)", "(smile:1e-1)", "(smile:1.)"):
            self.assertEqual(self.detect(text), "comfyui", text)
        self.assertEqual(run("(smile:.8)", input_dialect="auto", output_dialect="nai").text, "0.8::smile ::")

    def test_pack_names_starting_with_at_are_not_anima(self):
        known = {"@ichigo", "@ symbol"}
        self.assertEqual(self.detect("@ichigo, smile", known=lambda s: s in known), "nai")
        self.assertEqual(self.detect("@ichigo, smile"), "anima")                 # 팩을 모르면 신호로 봄
        self.assertEqual(self.detect("@ichigo, @wlop", known=lambda s: s in known), "anima")

    def test_priority_decides_conflicts_and_empty(self):
        mixed = "1.1::smile::, (blush:1.2)"
        result = core.detect_dialect(mixed)
        self.assertEqual((result["dialect"], result["conflict"]), ("nai", True))
        self.assertEqual(self.detect(mixed, priority=("comfyui", "nai", "anima")), "comfyui")
        self.assertEqual(self.detect("1.1::smile::, @wlop", priority=("anima", "nai")), "anima")
        self.assertEqual(self.detect("1girl, smile"), "nai")
        self.assertEqual(self.detect("1girl, smile", priority=("anima",)), "anima")
        self.assertEqual(result["evidence"], {"nai": ["1.1::"], "comfyui": ["(blush:1.2)"]})

    def test_organize_auto(self):
        r = run("1.1::smile::, 1girl", input_dialect="auto", line_per_section=False)
        self.assertEqual(r.report["dialect"]["input"], "nai")
        self.assertEqual(r.report["dialect"]["output"], "nai")
        self.assertTrue(r.report["dialect"]["auto"])
        self.assertEqual(r.text, "1girl, 1.1::smile ::")
        r = run("@wlop, smile", input_dialect="auto", output_dialect="comfyui", line_per_section=False)
        self.assertEqual(r.text, "artist:wlop, smile")
        r = run("1.1::smile::, (blush:1.2)", input_dialect="auto", dialect_priority="comfyui > nai")
        self.assertEqual(r.report["dialect"]["input"], "comfyui")
        self.assertTrue(r.report["dialect"]["conflict"])
        self.assertFalse(run("smile").report["dialect"]["auto"])

    def test_bad_dialect_options(self):
        for opts in ({"input_dialect": "sdxl"}, {"output_dialect": "auto"}, {"dialect_priority": "nai, nai"},
                     {"dialect_priority": "nai, sdxl"}, {"dialect_priority": 3}, {"comments": "hide"}):
            with self.assertRaises(ValueError, msg=opts):
                run("smile", **opts)


if __name__ == "__main__":
    unittest.main()
