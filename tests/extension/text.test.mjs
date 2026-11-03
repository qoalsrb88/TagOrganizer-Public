// extension/engine/text.js — node --test (tests/test_extension_core.py 가 부른다)
import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_SETTINGS, boxGroup, boxSettings, isSortableSelection, keepEdges, optionsFor, sameLines, selectionProblem, summary } from "../../extension/engine/text.js";

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

test("크롭·시점 덮어쓰기는 준 때만 옵션에 들어간다", () => {
  const plain = optionsFor(DEFAULT_SETTINGS, null, "default", "novelai.net");
  assert.equal("framing" in plain, false);
  assert.equal("view" in plain, false);
  const set = optionsFor({ ...DEFAULT_SETTINGS, framing: "upper body", view: "pov" }, null, "default", "novelai.net");
  assert.equal(set.framing, "upper body");
  assert.equal(set.view, "pov");
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

test("알림 글에 뺀 태그를 적는다", () => {
  const done = { kept: 3, removed: ["a", "b", "c", "d", "e", "f", "g"], unknown: ["x"], warnings: 1 };
  assert.equal(summary(done), "3개 정렬 · 7개 뺌(a, b, c, d, e, f 외 1개) · DB에 없음 1개 · 문법 경고 1개");
  assert.equal(summary({ kept: 2, removed: [], unknown: [], warnings: 0 }), "2개 정렬");
});
