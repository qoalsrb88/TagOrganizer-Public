// extension/editor/model.js — node --test (tests/test_extension_core.py 가 부른다).
// 조작 결과는 Python Model 과의 비교(ModelParityTest)가 맡고, 여기서는 Python 쪽 비교에 넣지 않은 것만 본다.
import assert from "node:assert/strict";
import test from "node:test";
import { Model, formatGuessRule, parseGuessRules } from "../../extension/editor/model.js";

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
