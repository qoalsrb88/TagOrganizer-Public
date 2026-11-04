"""JS 코어(extension/engine/core.js)와 Python 코어가 같은 결과를 내는지 비교한다.

입력은 코어 테스트 파일의 글자 상수(문법 경계 사례가 모여 있다) + 고정 시드로 섞은 프롬프트이고, 옵션 조합과
공유 배열 프리셋을 돌려가며 text·removed_text·report 전부를 비교한다. 실제 팩(build/tagpack.json.gz)이 있으면 그것으로도
비교한다. Node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import ast
import gzip
import json
import math
import random
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

import fixtures
import test_syntax
from fixtures import ROOT, core
from gui.arrangement import Model
from tools import extension

RUNNER = ROOT / "tests" / "extension" / "run_core.mjs"
MODEL_RUNNER = ROOT / "tests" / "extension" / "run_model.mjs"
REAL_PACK = ROOT / "build" / "tagpack.json.gz"
# 코어 테스트의 작은 팩들(test_syntax 는 마침표·// 가 든 이름과 종족 태그를 더한 팩)
LITERAL_SOURCES = ("test_core.py", "test_syntax.py", "test_arrangement.py", "test_extra.py", "test_e621.py")

HANDWRITTEN = [
    "",
    " , ,\n",
    "smile, 1girl, shoes, upper body, hatsune miku, blue hair, 1.2::long hair ::, outdoors, {{sky}}, from side",
    "1.1::year 2024::, 1.1::as109::, 2::a, b ::, -1::bad ::, 0.5::x::",
    "{{{a}}}, [[b]], {[c]}, {d, e}, {unclosed, tag",
    "(a:1.2), ((b)), (c:.8), (d:+1.2), (e:1e-1), (f:nan), (g:inf), (h:1.0625), (i:0.0625), (j:-0.0), (k:1_0)",
    "a cat (sleeping:1.2), star_(symbol), star \\(symbol\\), (star \\(symbol\\):1.1)",
    "<lora:foo:0.8> 1girl, embedding:bar, __hair__, __haircolor__ hair, {red|blue} eyes, {2$$a|b}, text:hello",
    "1girl // 주석, 이어짐\nsmile, .hack//sign, sp//dr // 뒤 주석\n// 맨 앞 주석\nhttps://example.com",
    "1girl, smile BREAK 2girls, shoes BREAK // 주석만\nBREAK",
    "한글BREAK태그, BREAKfast, a BREAK b",
    "She holds an umbrella. It is raining, smile. monkey d. luffy, st. louis (azur lane), super smash bros. melee",
    "@artist, @ichigo, artist:wlop, artist:nobody, source#hug, target#kiss, mutual#hold, @_@, ^_^, >_<",
    "SOLE_FEMALE, Long_Hair, longhair, 1GIRL, 1girl, 1girl, Wink",
    "１.２::전각 숫자 ::, 태그١, 𠮷野家, 𠮷., 😀 smile., é., naïve café",
    "tab\tin, nbsp in, line sep, 　ideographic　, form\x0cfeed, \x1cfs",
    "upper body, shoes, from behind, navel, nipples, sex, full body, 2girls, mirror, reference sheet",
    "holding chainsword, thick armpit hair, sparkling closed eyes, russian blue cat girl, merino sheep girl, corgi boy",
    "\r\n1girl,\r\nsmile\r\n",
    "dr. stone is here. mr. big, u.a. high, mt. fuji rises. a.i. voice, e. coli, vs. battle, ver. 2",
    "a,b;c, d|e, f:g, h::i, j :: k, ::, ::::, 1::, 1.5::",
]

OPTIONS: List[Dict[str, Any]] = [
    {},
    {"input_dialect": "nai"},
    {"input_dialect": "anima"},
    {"input_dialect": "auto"},
    {"input_dialect": "auto", "output_dialect": "nai", "dialect_priority": "comfyui, nai"},
    {"input_dialect": "nai", "output_dialect": "comfyui", "line_per_section": False},
    {"input_dialect": "comfyui", "output_dialect": "anima", "trailing_comma": False},
    {"preset": "character_swap", "max_rating": "s", "guess": True},
    {"preset": "pose_reference", "rules": "report", "order": "popular", "input_dialect": "auto"},
    {"rules": "off", "order": "alpha", "dedupe": False, "resolve_aliases": False, "comments": "keep"},
    {"rules": {"framing": "off", "view": "report"}, "match_secondary": True, "sentences": False},
    {"framing": "upper body", "view": "from behind", "guess": True, "input_dialect": "auto", "comments": "keep"},
    {"view": "straight-on", "gaze": "looking back, looking at viewer"},
    {"framing": "head out of frame", "gaze": "facing viewer, looking at viewer", "rules": "report"},
    {"view": "from side", "gaze": "looking back, sideways glance", "rules": {"view": "off"}},
    {"view": "from behind, from side", "gaze": "looking at viewer"},
    {"view": "front view, from behind, multiple views", "framing": "upper body"},
    {"view": "multiple views"},
    {"view": "three quarter view, dutch angle"},
]

INVALID_OPTIONS: List[Dict[str, Any]] = [
    {"preset": "nope"}, {"layout": "nope"}, {"max_rating": "x"}, {"input_dialect": "x"}, {"output_dialect": "x"},
    {"comments": "x"}, {"order": "x"}, {"rules": "x"}, {"exclude": "nope"}, {"exclude": "clothes/nope"},
    {"dialect_priority": "nai, nai"}, {"framing": "smile"}, {"gaze": "smile"}, {"view": "reference sheet"}, {"nope": 1},
    {"arrangement": "{"}, {"arrangement": []}, {"arrangement": {"format": "x", "rows": [["a"]]}},
    {"arrangement": {"rows": [["a"]], "chunks": {}}},
    {"arrangement": {"rows": [["a"], ["a"]], "chunks": {"a": {"members": ["body"]}}}},
    {"arrangement": {"rows": [["a b"]], "chunks": {"a b": {"members": ["nope"], "color": "red", "patterns": ["*"]}}}},
]

CUSTOM_ARRANGEMENT = {
    "format": "tagorg-arrangement", "version": 1, "label": "내 배열", "sub_order": True,
    "rows": [["mine"], ["count", "character"], ["hair", "body", "body_again"], ["hidden"], ["rest"]],
    "chunks": {
        "mine": {"custom": True, "tags": ["smile", "내 묘사"], "patterns": ["* armor", "re:hold.*"], "color": "#e11d48"},
        "count": {"members": ["subject/count"]},
        "character": {"members": ["subject/species", "subject/character", "subject", "series"]},
        "hair": {"members": ["body/hair"]},
        "body": {"members": ["body", "*@identity"]},
        "body_again": {"members": ["body", "clothes/top"]},
        "hidden": {"members": ["clothes"], "hidden": True},
        "rest": {"members": ["*"]},
    },
    "guess": [{"pattern": "my *", "primary": "object", "rating": "s", "identity": True}],
}


def literal_prompts() -> List[str]:
    """코어 테스트 파일의 글자 상수 중 프롬프트처럼 보이는 것."""
    found: Dict[str, None] = {}
    for name in LITERAL_SOURCES:
        tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 0 < len(node.value) < 400:
                found[node.value] = None
    return list(found)


def fuzz_prompts(names: List[str], count: int, seed: int) -> List[str]:
    """이름과 강조·구분 표기를 섞은 프롬프트(시드 고정)."""
    rng = random.Random(seed)
    wraps = ["{}", "{}", "{}", "{{{}}}", "[{}]", "({})", "({}:1.2)", "1.3::{} ::", "1.1::{}::", "{}.", "artist:{}", "@{}",
             "{}_x", "my {}", "// {}", "<lora:{}:0.5>", "__{}__", "{{{}|blue}} eyes"]
    seps = [", ", ", ", ", ", ",", ",\n", " BREAK ", ". ", " // "]
    out = []
    for _ in range(count):
        parts: List[str] = []
        for _ in range(rng.randint(1, 12)):
            parts.append(rng.choice(wraps).format(rng.choice(names)))
            parts.append(rng.choice(seps))
        out.append("".join(parts[:-1]))
    return out


def shared_arrangements() -> List[Dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "data" / "arrangements").glob("*.json"))]


def python_result(text: str, pack: core.TagPack, options: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = core.organize(text, pack, dict(options))
    except ValueError as e:
        return {"error": str(e)}
    return {"text": r.text, "removed_text": r.removed_text, "report": r.report}


def diff(a: Any, b: Any, path: str = "") -> Iterable[str]:
    """Python 결과 a 와 JS 결과 b 의 다른 곳. 숫자는 값이 같으면 같다(1.0 == 1)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                yield f"{path}.{key}: 한쪽에만 있음 ({a.get(key)!r} / {b.get(key)!r})"
            else:
                yield from diff(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            yield f"{path}: 길이 {len(a)} / {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            yield from diff(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, (int, float)) and math.isnan(a):
        if b == b or b is None:
            yield f"{path}: {a!r} / {b!r}"
    elif a != b:
        yield f"{path}: {a!r} / {b!r}"


def jsonable(value: Any) -> Any:
    """JSON 은 inf·nan 을 null 로 내보내므로 비교 전에 Python 쪽도 null 로 맞춘다."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


# 오류 글에 언어별 파서 메시지가 들어가는 경우는 앞부분만 비교한다
_LOOSE_ERRORS = ("배열 세팅 JSON 을 읽지 못했습니다",)


@unittest.skipUnless(shutil.which("node"), "node 가 없음")
class ParityTest(unittest.TestCase):
    def run_js(self, pack_arg: Path, cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            cases_path, out_path = Path(tmp) / "cases.json", Path(tmp) / "out.json"
            cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            subprocess.run(["node", str(RUNNER), str(pack_arg), str(cases_path), str(out_path)], check=True,
                           capture_output=True, encoding="utf-8", timeout=600)
            return json.loads(out_path.read_text(encoding="utf-8"))

    def compare(self, pack_arg: Path, pack: core.TagPack, cases: List[Dict[str, Any]]) -> None:
        results = self.run_js(pack_arg, cases)
        self.assertEqual(len(results), len(cases))
        failures: List[str] = []
        for case, js in zip(cases, results):
            py = jsonable(python_result(case["text"], pack, case["options"]))
            if "error" in py and "error" in js and py["error"].startswith(_LOOSE_ERRORS):
                continue
            found = list(diff(py, js))
            if found:
                options = {k: ("..." if k == "arrangement" else v) for k, v in case["options"].items()}
                failures.append(f"{case['text']!r} {options}\n    " + "\n    ".join(found[:6]))
        if failures:
            self.fail(f"{len(cases)}건 중 {len(failures)}건이 다름\n" + "\n".join(failures[:20]))

    def cases_for(self, pack: core.TagPack, names: List[str], fuzz: int) -> List[Dict[str, Any]]:
        prompts = HANDWRITTEN + literal_prompts()
        cases = [{"text": p, "options": o} for p in prompts for o in OPTIONS]
        random_prompts = fuzz_prompts(names, fuzz, seed=20261102)
        cases += [{"text": p, "options": OPTIONS[i % len(OPTIONS)]} for i, p in enumerate(random_prompts)]
        arrangements = [CUSTOM_ARRANGEMENT] + shared_arrangements()
        for arrangement in arrangements:
            for p in HANDWRITTEN + random_prompts[:40]:
                cases.append({"text": p, "options": {"arrangement": arrangement, "guess": True}})
                cases.append({"text": p, "options": {"arrangement": json.dumps(arrangement), "input_dialect": "auto",
                                                     "output_dialect": "nai", "order": "popular"}})
        cases += [{"text": "1girl, smile", "options": o} for o in INVALID_OPTIONS]
        return cases

    def test_fixture_pack(self):
        data, _ = fixtures.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pack.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            pack = core.TagPack(data)
            self.compare(path, pack, self.cases_for(pack, sorted(pack._rows), 1500))
            [js_palette] = self.run_js(path, [{"want": "palette"}])
            self.assertEqual(list(diff(core.palette(pack), js_palette)), [])
            self.assertTrue(js_palette["quick"]["framing"], "크롭 퀵 목록이 비었음")
            self.assertTrue(js_palette["quick"]["gaze"], "시선 퀵 목록이 비었음")
            pal = core.palette(pack)
            selections = [[], ["looking back"], ["looking back", "looking at viewer"], ["facing away"], ["looking up", "sideways glance"]]
            toggles = [{"want": "gaze_toggle", "selected": s, "tag": t} for s in selections for t in pal["quick"]["gaze"]]
            self.assertEqual(self.run_js(path, toggles),
                             [core.gaze_toggle(pal, c["selected"], c["tag"]) for c in toggles])
            view_sets = [[], ["front view"], ["from behind", "from side"], ["from above"], ["front view", "from behind", "multiple views"]]
            toggles = [{"want": "view_toggle", "selected": s, "tag": t, "lifted": lifted}
                       for s in view_sets for t in pal["quick"]["view"] for lifted in (False, True)]
            self.assertEqual(self.run_js(path, toggles),
                             [core.view_toggle(pal, c["selected"], c["tag"], c["lifted"]) for c in toggles])
            self.compare_layouts(path, pack)

    def test_layout_with_identity_and_except(self):
        """실제 레이아웃에 없는 경우: 섹션이 고유 외형만 가져가거나 except 로 뺀다."""
        data, _ = fixtures.build()
        data["layouts"]["edge"] = {"sections": [
            {"id": "looks", "label": "외형", "include": ["body@identity"]},
            {"id": "cloth", "include": ["clothes", "body"], "except": ["clothes/top"]},
            {"id": "none", "include": ["clothes"], "except": ["clothes"]},
            {"id": "all", "label": "나머지", "include": ["*"]},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pack.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            pack = core.TagPack(data)
            self.compare_layouts(path, pack)
        members = core.arrangement_from_layout(pack, "edge")["chunks"]["looks"]["members"]
        self.assertTrue(members and all(m.endswith("@identity") for m in members), members)

    def compare_layouts(self, pack_arg: Path, pack: core.TagPack) -> None:
        [js_layouts] = self.run_js(pack_arg, [{"want": "layouts"}])
        expected = {lid: core.arrangement_from_layout(pack, lid) for lid in pack.layouts}
        self.assertTrue(expected)
        self.assertEqual(list(diff(expected, js_layouts)), [])

    def test_syntax_fixture_pack(self):
        pack = test_syntax.pack()
        data = {"format": core.PACK_FORMAT, "schema": core.PACK_SCHEMA, "columns": list(core.COLUMNS),
                "version": pack.version, "taxonomy": pack.taxonomy, "rules": pack.rules, "layouts": pack.layouts,
                "presets": pack.presets, "guess": pack.guess, "kaomoji": sorted(pack.kaomoji),
                "tags": list(pack._rows.values()), "aliases": pack._aliases,
                "extra": {db: [k for k, v in pack._db.items() if v == db] for db in pack.extra_dbs},
                "site": {site: [k for k, v in pack._site.items() if v == site] for site in pack.sites}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pack.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.compare(path, pack, self.cases_for(pack, sorted(pack._rows), 1000))

    @unittest.skipUnless(REAL_PACK.is_file(), "build/tagpack.json.gz 가 없음")
    def test_real_pack(self):
        pack = core.load_pack(str(REAL_PACK))
        names = sorted(pack._rows)
        rng = random.Random(7)
        sample = rng.sample(names, 3000) + [n for n in names if any(c in n for c in ".(/:") ][:3000]
        self.compare(REAL_PACK, pack, self.cases_for(pack, sample, 1500))
        self.compare_layouts(REAL_PACK, pack)


def random_ops(data: Dict[str, Any], rng: random.Random, count: int) -> Iterable[List[Any]]:
    """지금 상태에서 할 수 있는 조작을 고른다(Python Model 을 돌리며 만든다). 단계마다 [조작, 결과, JSON] 을 낸다."""
    model = Model(data)
    for _ in range(count):
        ids = model.flat()
        if not ids:
            op = ["add_custom", "내 태그", "", ["smile"], ["* armor"], None]
        else:
            cid = rng.choice(ids)
            other = cid if rng.random() < 0.3 else rng.choice(ids)   # 같은 청크 안에서 멤버를 옮기는 경우도 나오게
            members = model.members(cid)
            row = rng.randrange(len(model.rows))
            mi = rng.randrange(len(members)) if members else None
            choices: List[List[Any]] = [
                ["move_chunk", cid, row, rng.randint(0, len(model.rows[row]))],
                ["move_chunk_new_row", cid, rng.randint(0, len(model.rows))],
                ["break_before", cid], ["break_after", cid], ["join_previous", cid],
                ["toggle_hidden", cid],
                ["set_label", cid, rng.choice(["", "새 이름", "  여백  "])],
                ["set_color", cid, rng.choice(["", "#112233"])],
                ["merge_into", cid, other],
                ["delete_chunk", cid],
                ["add_slots", rng.choice([[], ["body/hair", "etc"]])],
                ["add_custom", "내 묘사", rng.choice(["", "#e11d48"]), ["smile", " smile ", "내 태그"], ["* armor"], rng.choice([None, 0])],
                ["update_custom", cid, "고침", "", [" a ", "a", "b"], []],
            ]
            if mi is not None:
                other_members = model.members(other)
                choices += [
                    ["move_member", cid, mi, other, rng.randint(0, len(other_members))],
                    ["detach_member", cid, mi, row, rng.choice([None, rng.randint(0, len(model.rows[row]))])],
                    ["detach_after", cid, mi],
                ]
            op = rng.choice(choices)
        ret = getattr(model, op[0])(*op[1:])
        yield op, ret, model.to_json()


@unittest.skipUnless(shutil.which("node"), "node 가 없음")
class ModelParityTest(unittest.TestCase):
    """editor/model.js 가 관리 툴의 Model(gui/arrangement.py)과 같은 조작 결과를 내는지 본다."""

    STEPS = 60

    def starts(self, pack: core.TagPack) -> List[Dict[str, Any]]:
        messy = {"label": 3, "description": "설명", "author": "me", "sub_order": False,
                 "rows": ["a", ["b", "b", "없음"], [], None], "guess": [{"pattern": "my *", "primary": "object"}],
                 "chunks": {"a": {"members": "body/hair, clothes"}, "b": {"tags": "smile, 내 태그\nwink", "patterns": ["* armor"]},
                            "c": {"members": []}, "d": {"members": ["*"], "hidden": True}, "e": None}}
        # 한 줄에 청크가 여럿이어야 같은 줄 안에서 옮기는 경우가 나온다
        wide = {"rows": [["a", "b", "c", "d", "e"], ["f", "g", "h"]],
                "chunks": {cid: {"members": [f"{main}/{cid}" for main in ("body", "clothes", "action", "object")]}
                           for cid in "abcdefgh"}}
        starts = [core.arrangement_from_layout(pack, lid) for lid in pack.layouts]
        return starts + [CUSTOM_ARRANGEMENT, messy, wide, wide, {"rows": [], "chunks": {}}] + shared_arrangements()

    def test_same_results(self):
        pack = fixtures.pack()
        palette = core.palette(pack)
        rng = random.Random(20261103)
        cases, expected = [], []
        for data in self.starts(pack) * 3:
            ops, rets, jsons = [], [], []
            for op, ret, snapshot in random_ops(data, rng, self.STEPS):
                ops.append(op)
                rets.append(ret)
                jsons.append(snapshot)
            cases.append({"data": data, "ops": ops, "palette": palette})
            expected.append((data, rets, jsons))
        with tempfile.TemporaryDirectory() as tmp:
            cases_path, out_path = Path(tmp) / "cases.json", Path(tmp) / "out.json"
            cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            subprocess.run(["node", str(MODEL_RUNNER), str(cases_path), str(out_path)], check=True,
                           capture_output=True, encoding="utf-8", timeout=120)
            results = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(len(results), len(cases))
        kinds = set()
        for case, (data, rets, jsons), js in zip(cases, expected, results):
            self.assertEqual(list(diff(Model(data).to_json(), js["loaded"])), [], "불러온 결과가 다름")
            for op, ret, snapshot, step in zip(case["ops"], rets, jsons, js["steps"]):
                kinds.add(op[0])
                found = list(diff(snapshot, step["json"]))
                self.assertEqual(found, [], f"{op} 뒤의 배열이 다름")
                self.assertEqual(ret, step["ret"], f"{op} 의 결과가 다름")
            last = jsons[-1]
            taxonomy = pack.taxonomy
            self.assertEqual({c: core.chunk_label(s, taxonomy) for c, s in last["chunks"].items()}, js["labels"])
            self.assertEqual({c: core.chunk_color(s, taxonomy) for c, s in last["chunks"].items()}, js["colors"])
            self.assertEqual([core.selector_label(m, taxonomy) for s in last["chunks"].values() for m in s.get("members", ())],
                             js["selectors"])
            self.assertEqual(core.uncovered_slots(last, taxonomy), js["uncovered"])
        self.assertGreaterEqual(len(kinds), 15, kinds)


class TextHelpersTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node 가 없음")
    def test_node_suite(self):
        result = subprocess.run(["node", "--test", str(ROOT / "tests" / "extension" / "text.test.mjs"),
                                 str(ROOT / "tests" / "extension" / "model.test.mjs")],
                                capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ExtensionDataTest(unittest.TestCase):
    def test_manifest_files_exist(self):
        root = ROOT / "extension"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue((root / manifest["background"]["service_worker"]).is_file())
        self.assertTrue((root / manifest["action"]["default_popup"]).is_file())

    def test_novelai_module_stays_on_novelai(self):
        """사이트 접근은 NovelAI 콘텐츠 스크립트뿐이다. 다른 사이트는 메뉴·단축키를 누른 탭의 activeTab 만 쓴다."""
        root = ROOT / "extension"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("host_permissions", manifest)
        [script] = manifest["content_scripts"]
        self.assertEqual(script["matches"], ["https://novelai.net/*"])
        self.assertEqual(script["js"][0], "page/field.js", "nai/content.js 가 쓰므로 먼저 읽어야 함")
        for name in script["js"]:
            self.assertTrue((root / name).is_file(), name)
        [exposed] = manifest["web_accessible_resources"]
        self.assertEqual(exposed["resources"], ["nai/panel.html"])
        self.assertEqual(exposed["matches"], ["https://novelai.net/*"])
        self.assertTrue((root / exposed["resources"][0]).is_file())

    def test_write_data_and_zip(self):
        data, _ = fixtures.build()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_path = tmp_path / "pack.json"
            pack_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            ext = tmp_path / "ext"
            (ext / "panel").mkdir(parents=True)
            (ext / "manifest.json").write_text("{}", encoding="utf-8")
            (ext / "panel" / "panel.js").write_text("", encoding="utf-8")
            shared, local = tmp_path / "shared", tmp_path / "local"
            shared.mkdir()
            local.mkdir()
            good = {"format": "tagorg-arrangement", "version": 1, "label": "좋은 배열", "rows": [["a"]],
                    "chunks": {"a": {"members": ["body"]}}}
            (shared / "good.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
            (shared / "bad.json").write_text(json.dumps({"rows": [["a"]], "chunks": {"a": {"members": ["nope"]}}}),
                                             encoding="utf-8")
            (local / "mine.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")

            warnings = extension.write_data(pack_path, ext, shared, local)
            self.assertEqual(len(warnings), 1)
            self.assertIn("bad.json", warnings[0])
            self.assertEqual(gzip.decompress((ext / "data" / extension.PACK_NAME).read_bytes()), pack_path.read_bytes())
            catalog = json.loads((ext / "data" / extension.CATALOG_NAME).read_text(encoding="utf-8"))
            self.assertEqual([a["id"] for a in catalog["arrangements"]], ["shared-good", "local-mine"])
            self.assertEqual(catalog["arrangements"][1]["label"], "좋은 배열 (내 배열)")
            self.assertIn("sort_only", catalog["filters"])
            self.assertIn("default", catalog["layouts"])

            out = tmp_path / "out" / "ext.zip"
            self.assertEqual(extension.make_zip(out, ext), 4)
            with zipfile.ZipFile(out) as z:
                self.assertIn("panel/panel.js", z.namelist())

    def test_zip_needs_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                extension.make_zip(Path(tmp) / "x.zip", Path(tmp))


if __name__ == "__main__":
    unittest.main()
