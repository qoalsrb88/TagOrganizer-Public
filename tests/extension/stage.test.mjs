// extension/editor/stage.js 의 계산 — node --test (tests/test_extension_core.py 가 부른다). 그리기와 끌기는 e2e_nai.mjs 가 본다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dialZone, stageCovers, viewWithZone } from "../../extension/editor/stage.js";

const quick = JSON.parse(readFileSync(new URL("../../data/rules.json", import.meta.url), "utf-8")).quick;
const palette = { quick: { framing: quick.framing, view: quick.view }, view_conflicts: quick.view_conflicts, view_lift: quick.view_lift,
  framing_frame: quick.framing_frame, view_dial: quick.view_dial };
const direction = quick.view_dial.direction;
const height = quick.view_dial.height;
const dirTags = direction.map((z) => z.tag);
const heightTags = height.map((z) => z.tag);

test("그림이 맡는 태그는 퀵 목록 순서", () => {
  const covers = stageCovers(palette);
  assert.deepEqual(covers.framing, quick.framing);
  assert.deepEqual(covers.view, ["front view", "three quarter view", "from side", "from behind", "from above", "from below"]);
  assert.deepEqual(stageCovers({ quick: palette.quick }), { framing: [], view: [] });
});

test("다이얼 구간: 방향은 좌우가 같고 높이의 가운데는 수평", () => {
  assert.equal(dialZone(direction, 0, true), "front view");
  assert.equal(dialZone(direction, -40, true), "three quarter view");
  assert.equal(dialZone(direction, 90, true), "from side");
  assert.equal(dialZone(direction, -179, true), "from behind");
  assert.equal(dialZone(direction, 200, true), "from behind");
  assert.equal(dialZone(height, 0), null);
  assert.equal(dialZone(height, 45), "from above");
  assert.equal(dialZone(height, -45), "from below");
  for (const zone of direction) assert.equal(dialZone(direction, zone.at, true), zone.tag);
  for (const zone of height) assert.equal(dialZone(height, zone.at), zone.tag);
});

test("다이얼은 한 축만 바꾸고 다른 시점은 남긴다", () => {
  assert.deepEqual(viewWithZone(palette, ["three quarter view", "from above", "pov"], dirTags, "from side"), ["from side", "from above", "pov"]);
  assert.deepEqual(viewWithZone(palette, ["three quarter view", "from above"], heightTags, null), ["three quarter view"]);
  assert.deepEqual(viewWithZone(palette, ["from above"], heightTags, "from below"), ["from below"]);
  // 부딪치는 시점은 칩처럼 꺼지고, lift 면 함께 남는다
  assert.deepEqual(viewWithZone(palette, ["straight-on"], dirTags, "from behind"), ["from behind"]);
  assert.deepEqual(viewWithZone(palette, ["straight-on"], dirTags, "from behind", { lifted: true }), ["straight-on", "from behind"]);
  // Ctrl·Shift: 같은 축의 다른 태그를 두고 켜거나 끈다
  assert.deepEqual(viewWithZone(palette, ["front view"], dirTags, "from side", { add: true }), ["front view", "from side"]);
  assert.deepEqual(viewWithZone(palette, ["front view", "from side"], dirTags, "from side", { add: true }), ["front view"]);
  assert.deepEqual(viewWithZone(palette, ["front view"], dirTags, "from behind", { add: true }), ["from behind"]);
});
