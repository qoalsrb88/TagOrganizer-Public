// extension/engine/text.js — node --test (tests/test_extension_core.py 가 부른다)
import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_SETTINGS, boxGroup, boxSettings, isSortableSelection, keepEdges, masterDrift, masterOverrides, optionsFor, sameLines, selectionProblem, sourceText, summary } from "../../extension/engine/text.js";

test("구분 표기가 없는 조각은 정렬할 것이 없다", () => {
  assert.equal(isSortableSelection("smile"), false);
  assert.equal(isSortableSelection(" smile, "), false);
  assert.equal(isSortableSelection("smile, 1girl"), true);
  assert.equal(isSortableSelection("smile\n1girl"), true);
  assert.equal(isSortableSelection("smile BREAK 1girl"), true);
});

test("잘린 강조를 찾는다", () => {
  for (const ok of ["a, b", "{a}, [b], (c:1.2)", "1.2::a ::, b", ":), ;(, :], a", "star \\(symbol\\), b", "1.1::a::"]) {
    assert.equal(selectionProblem(ok), "", ok);
  }
  for (const bad of ["{a, b", "a}, b", "[a, b", "(a, b", "a), b", "1.2::a, b", "a ::, b"]) {
    assert.notEqual(selectionProblem(bad), "", bad);
  }
});

test("선택의 앞뒤 빈칸·쉼표를 지킨다", () => {
  assert.equal(keepEdges(" b, c, ", "c, b"), " c, b, ");
  assert.equal(keepEdges("b, c", "c, b"), "c, b");
  assert.equal(keepEdges(",\n b,\nc,\n", "c,\nb"), ",\n c,\nb,\n");
  assert.equal(keepEdges(" , ", ""), " , ");
});

test("NovelAI 는 nai 로 내보내고 다른 곳은 입력과 같게", () => {
  const nai = optionsFor(DEFAULT_SETTINGS, null, "default", "novelai.net");
  assert.equal(nai.output_dialect, "nai");
  assert.equal(nai.dialect_priority, "nai, comfyui, anima");
  assert.equal(nai.layout, "default");
  const other = optionsFor(DEFAULT_SETTINGS, { rows: [] }, undefined, "fakenovelai.net");
  assert.equal(other.output_dialect, "");
  assert.equal(other.dialect_priority, "comfyui, nai, anima");
  assert.deepEqual(other.arrangement, { rows: [] });
  assert.equal(optionsFor({ ...DEFAULT_SETTINGS, output: "anima" }, null, "default", "novelai.net").output_dialect, "anima");
});

test("크롭·시점·시선 덮어쓰기는 준 때만 옵션에 들어간다", () => {
  const plain = optionsFor(DEFAULT_SETTINGS, null, "default", "novelai.net");
  assert.equal("framing" in plain, false);
  assert.equal("view" in plain, false);
  assert.equal("gaze" in plain, false);
  const set = optionsFor({ ...DEFAULT_SETTINGS, framing: "upper body", view: "pov", gaze: "looking back, looking down" }, null, "default", "novelai.net");
  assert.equal(set.framing, "upper body");
  assert.equal(set.view, "pov");
  assert.equal(set.gaze, "looking back, looking down");
});

test("입력창 묶음: UC 는 구도 규칙을 끄고, 저장값이 이긴다", () => {
  assert.equal(boxGroup("prompt"), "prompt");
  assert.equal(boxGroup(undefined), "prompt");
  assert.equal(boxGroup("uc"), "uc");
  assert.equal(boxGroup("character"), "character");
  assert.equal(boxGroup("character-uc"), "uc");

  const global = { ...DEFAULT_SETTINGS, default_preset: "layout:x", rules: "report" };
  assert.equal(boxSettings(global, {}, "prompt").rules, "report");
  assert.equal(boxSettings(global, {}, "prompt").preset, "layout:x");
  assert.equal(boxSettings(global, {}, "uc").rules, "off");
  assert.equal(boxSettings(global, {}, "character-uc").rules, "off");
  assert.equal(boxSettings(global, undefined, "character").rules, "report");

  const stored = { uc: { rules: "remove", preset: "layout:u" }, character: { preset: "layout:c", guess: true } };
  assert.equal(boxSettings(global, stored, "uc").rules, "remove");
  assert.equal(boxSettings(global, stored, "uc").preset, "layout:u");
  assert.equal(boxSettings(global, stored, "character").preset, "layout:c");
  assert.equal(boxSettings(global, stored, "character").guess, true);
  assert.equal(boxSettings(global, stored, "prompt").preset, "layout:x");
});

test("입력창이 읽은 글과 결과가 줄바꿈 표기만 다르면 같다", () => {
  assert.equal(sameLines("a, b,\n\nc\n", "a, b,\nc"), true);
  assert.equal(sameLines("a, b,\n \nc", "a, b,\nc"), true);
  assert.equal(sameLines("a, b, c", "a, b,\nc"), false);
  assert.equal(sameLines("b, a", "a, b"), false);
});

test("입력창의 빈 문단은 결과의 빈 줄과 같고, 빈 줄이 다르면 다른 글이다", () => {
  assert.equal(sameLines("a,\n\n\n\n\nb", "a,\n\nb"), true);
  assert.equal(sameLines("a,\n\n\n\n\n\n\n\nb", "a,\n\n\nb"), true);
  assert.equal(sameLines("a,\n\nb", "a,\n\nb"), false);
  assert.equal(sameLines("a,\n\n\n\n\nb", "a,\nb"), false);
  assert.equal(sameLines("a,\n\n \n\nb", "a,\n\nb"), true);
  assert.equal(sameLines("a,\n\n \n\nb", "a,\nb"), false);
});

test("원본이 켜져 있으면 빈 입력창에도 원본을 쓰고, 끄면 입력창을 쓴다", () => {
  const master = { on: true, text: "1girl, looking back, shoes", lastApplied: "1girl" };
  assert.equal(sourceText("1girl", master), master.text);
  assert.equal(sourceText("", master), master.text);
  assert.equal(sourceText("1girl", { ...master, on: false }), "1girl");
  assert.equal(sourceText("1girl"), "1girl");
  assert.equal(sourceText("1girl", { on: true, text: "" }), "");
  assert.equal(sourceText("1girl", { on: true }), "");
  assert.equal(master.text, "1girl, looking back, shoes");
});

test("원본은 입력창 id 별로 따로 고른다", () => {
  const masters = {
    prompt: { on: true, text: "1girl" },
    "base-prompt": { on: true, text: "outdoors" },
    "character-prompts-0": { on: true, text: "blue eyes" },
    "character-prompts-1": { on: false, text: "long hair" },
  };
  assert.equal(sourceText("smile", masters.prompt), "1girl");
  assert.equal(sourceText("smile", masters["base-prompt"]), "outdoors");
  assert.equal(sourceText("smile", masters["character-prompts-0"]), "blue eyes");
  assert.equal(sourceText("smile", masters["character-prompts-1"]), "smile");
});

test("직접 고친 입력창은 마지막 적용과 현재 결과 모두 다를 때만 알린다", () => {
  const master = { on: true, text: "1girl, shoes, smile", lastApplied: "1girl,\nsmile" };
  const output = "1girl,\nshoes,\nsmile";
  assert.equal(masterDrift("1girl, blue eyes", master, output), true);
  assert.equal(masterDrift("1girl,\n\nsmile\n", master, output), false);
  assert.equal(masterDrift("1girl,\r\n shoes,\n\nsmile", master, output), false);
  assert.equal(masterDrift(" \n ", master, output), false);
  assert.equal(masterDrift("1girl, blue eyes", { ...master, on: false }, output), false);
  assert.equal(masterDrift("1girl, blue eyes", { ...master, lastApplied: "" }, output), false);
  assert.equal(masterDrift("1girl, blue eyes", { on: true, text: "1girl" }, output), false);
  assert.equal(masterDrift("1girl, blue eyes", undefined, output), false);
  // 입력창 글을 원본으로 가져왔으면 적용해도 사라질 변경이 없다
  assert.equal(masterDrift("1girl, blue eyes", { ...master, text: "1girl, blue eyes" }, output), false);
});

test("원본이 켜진 묶음의 칩만 남긴다", () => {
  const overrides = {
    prompt: { framing: "upper body", view: "from side", gaze: "looking back" },
    character: { view: "from behind" },
    uc: { view: "front view" },
  };
  const masters = {
    prompt: { on: false, group: "prompt" },
    "base-prompt": { on: true, group: "prompt" },
    "character-prompts-0": { on: false, group: "character" },
    "character-prompts-1": { on: true, group: "character" },
  };
  assert.deepEqual(masterOverrides(masters, overrides), {
    prompt: overrides.prompt,
    character: { framing: "", view: "from behind", gaze: "" },
  });
  assert.deepEqual(masterOverrides({ ...masters, "base-prompt": { on: false, group: "prompt" } }, overrides), {
    character: { framing: "", view: "from behind", gaze: "" },
  });
  assert.deepEqual(masterOverrides({}, overrides), {});
  assert.deepEqual(masterOverrides(masters, {}), {});
  assert.deepEqual(overrides.character, { view: "from behind" });
});

test("알림 글에 뺀 태그를 적는다", () => {
  const done = { kept: 3, removed: ["a", "b", "c", "d", "e", "f", "g"], unknown: ["x"], warnings: 1 };
  assert.equal(summary(done), "3개 정렬 · 7개 뺌(a, b, c, d, e, f 외 1개) · DB에 없음 1개 · 문법 경고 1개");
  assert.equal(summary({ kept: 2, removed: [], unknown: [], warnings: 0 }), "2개 정렬");
});
