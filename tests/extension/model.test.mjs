// extension/editor/model.js — node --test (tests/test_extension_core.py 가 부른다).
// 조작 결과는 Python Model 과의 비교(ModelParityTest)가 맡고, 여기서는 Python 쪽 비교에 넣지 않은 것만 본다.
import assert from "node:assert/strict";
import test from "node:test";
import { Model, formatGuessRule, parseGuessRules } from "../../extension/editor/model.js";
import { ArrangementEditor } from "../../extension/editor/editor.js";

test("빈 줄 표시는 앞 청크에 저장하고 줄을 나눈다", () => {
  const m = new Model({ rows: [["a", "b", "c"]], chunks: {
    a: { members: ["body/hair"] }, b: { members: ["body/eyes"] }, c: { custom: true },
  } });
  assert.equal(m.toggleGapBefore("a"), false);
  assert.equal(m.hasGapBefore("a"), false);
  assert.equal(m.toggleGapBefore("b"), true);
  assert.equal(m.hasGapBefore("b"), true);
  assert.deepEqual(m.rows, [["a"], ["b", "c"]]);
  assert.deepEqual(new Model(m.toJSON()).toJSON(), m.toJSON());
  m.toggleGapAfter("a");
  assert.equal(m.hasGapBefore("b"), false);
  assert.deepEqual(m.rows, [["a"], ["b", "c"]]);
  m.toggleGapAfter("a");
  m.joinPrevious("b");
  assert.equal(m.hasGapBefore("b"), false);
  assert.deepEqual(m.rows, [["a", "b", "c"]]);
  m.toggleGapAfter("b");
  m.moveChunk("c", 0, 1);
  assert.equal(m.hasGapBefore("b"), false);
  assert.equal(m.chunks.get("b").gap_after, true);
  m.mergeInto("b", "a");
  assert.equal(m.hasGapBefore("c"), false);
  m.toggleGapAfter("c");
  m.deleteChunk("c");
  assert.equal(m.chunks.has("c"), false);
});

test("빈 줄 띠에 놓으면 그 줄의 끝으로 옮긴다", () => {
  const row = {
    getBoundingClientRect: () => ({ top: 10, bottom: 60, height: 50 }),
    querySelector: () => ({ getBoundingClientRect: () => ({ top: 40, bottom: 60 }) }),
  };
  const editor = {
    drag: { src: { mi: -1 } }, model: { rows: [["a", "b"]] },
    canvasEl: { querySelectorAll: () => [row] },
    root: { getBoundingClientRect: () => ({ width: 400 }), offsetWidth: 400 },
  };
  for (const y of [40, 50, 60]) {
    assert.deepEqual(ArrangementEditor.prototype.dropTarget.call(editor, 100, y), { kind: "row", row: 0, pos: 2 });
  }
  assert.deepEqual(ArrangementEditor.prototype.dropTarget.call(editor, 100, 64), { kind: "newrow", row: 1 });
});

test("줄을 통째로 옮기면 빈 줄 표시가 따라간다", () => {
  const m = new Model({ rows: [["a", "b"], ["c"], ["d"]], chunks: {
    a: { members: ["body/hair"] }, b: { members: ["body/eyes"], gap_after: true }, c: { members: ["clothes"] }, d: { members: ["object"] },
  } });
  assert.equal(m.moveRow(0, 0), false);
  assert.equal(m.moveRow(0, 1), false);
  assert.equal(m.moveRow(0, 9), false);
  assert.equal(m.moveRow(-1, 0), false);
  assert.equal(m.moveRow(0, 2), true);
  assert.deepEqual(m.rows, [["c"], ["a", "b"], ["d"]]);
  assert.equal(m.hasGapBefore("d"), true);
  assert.equal(m.moveRow(2, 0), true);
  assert.deepEqual(m.rows, [["d"], ["c"], ["a", "b"]]);
});

test("빈 줄을 줄 사이에 놓고 옮긴다", () => {
  const m = new Model({ rows: [["a"], ["b"], ["c"]], chunks: {
    a: { members: ["body/hair"] }, b: { members: ["body/eyes"] }, c: { members: ["clothes"] },
  } });
  assert.equal(m.addGapAt(0), false);          // 맨 위에는 앞 청크가 없다
  assert.equal(m.addGapAt(1), true);
  assert.equal(m.addGapAt(1), false);          // 겹치지 않는다
  assert.equal(m.chunks.get("a").gap_after, true);
  assert.equal(m.moveGap(0, 2), true);
  assert.equal(m.chunks.get("a").gap_after, undefined);
  assert.equal(m.chunks.get("b").gap_after, true);
  assert.equal(m.moveGap(0, 2), false);        // 옮길 빈 줄이 없다
  assert.equal(m.moveGap(1, 2), false);        // 그 자리에 이미 있다
  assert.equal(m.addGapBottom(), 3);           // 맨 아래부터 빈자리를 찾는다
  assert.equal(m.addGapBottom(), 1);
  assert.equal(m.addGapBottom(), null);
});

test("줄 단위로 끌 때의 자리는 줄 가운데로 가른다", () => {
  const box = (top, bottom) => ({ getBoundingClientRect: () => ({ top, bottom }) });
  const editor = {
    canvasEl: { querySelectorAll: () => [box(0, 40), box(50, 90)], getBoundingClientRect: () => ({ left: 0, right: 200, top: 0, bottom: 120 }) },
    lineTarget: ArrangementEditor.prototype.lineTarget,
    drag: { src: { line: "row", row: 1 } },
  };
  const at = (y, x = 100) => ArrangementEditor.prototype.dropTarget.call(editor, x, y);
  assert.deepEqual(at(10), { kind: "between", row: 0 });
  assert.deepEqual(at(30), { kind: "between", row: 1 });
  assert.deepEqual(at(80), { kind: "between", row: 2 });
  editor.drag.src = { line: "gap", row: 0 };
  assert.deepEqual(at(10), { kind: "between", row: 1 });    // 빈 줄은 맨 위로 가지 않는다
  editor.drag.src = { line: "new" };
  assert.deepEqual(at(80), { kind: "between", row: 2 });
  assert.equal(at(80, 500), null);                          // 청크 칸 밖에 놓으면 넣지 않는다
});

test("추정 규칙 글을 읽고 쓴다", () => {
  const text = "holding * => action/holding\n\n# 메모\n* gloves => clothes/handwear | hands, feet | identity | rating=q\n";
  const rules = parseGuessRules(text);
  assert.deepEqual(rules, [
    { pattern: "holding *", primary: "action/holding" },
    { pattern: "* gloves", primary: "clothes/handwear", regions: ["hands", "feet"], identity: true, rating: "q" },
  ]);
  assert.equal(formatGuessRule(rules[0]), "holding * => action/holding");
  assert.equal(formatGuessRule(rules[1]), "* gloves => clothes/handwear | hands feet | identity | rating=q");
  assert.deepEqual(parseGuessRules(rules.map(formatGuessRule).join("\n")), rules);
});

test("다른 툴이 regions 를 글로 적은 규칙도 코어처럼 읽는다", () => {
  const rule = { pattern: "* gloves", primary: "clothes/handwear", regions: "hands, feet" };
  assert.equal(formatGuessRule(rule), "* gloves => clothes/handwear | hands feet");
  assert.equal(formatGuessRule({ ...rule, regions: "hands" }), "* gloves => clothes/handwear | hands");
  assert.equal(formatGuessRule({ ...rule, regions: [] }), "* gloves => clothes/handwear");
  assert.equal(formatGuessRule({ ...rule, regions: undefined }), "* gloves => clothes/handwear");
});

test("틀린 추정 규칙 줄은 줄 번호를 알린다", () => {
  assert.throws(() => parseGuessRules("a => b\nnot a rule"), /2번째 줄/);
});

test("모르는 키와 설명을 지킨다", () => {
  const model = new Model({ label: "a", description: "설명", author: "me", rows: [["x"]], chunks: { x: { members: ["body"] } } });
  const json = model.toJSON();
  assert.equal(json.description, "설명");
  assert.equal(json.author, "me");
  assert.deepEqual(new Model(json).toJSON(), json);
});
