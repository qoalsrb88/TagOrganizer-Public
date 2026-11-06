// extension/editor/glyphs.js — node --test (tests/test_extension_core.py 가 부른다).
// 그림 데이터는 extension/editor/glyphs.json 하나이고, 관리 툴(gui/glyphs.py)도 같은 파일을 읽는다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { drawGlyph, glyph, glyphSpec } from "../../extension/editor/glyphs.js";

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf-8"));
const rules = read("../../data/rules.json");
const data = read("../../extension/editor/glyphs.json");
const GROUPS = ["framing", "view", "gaze", "pose"];

// DOM 없이 그린 결과를 보는 작은 문서
const fakeDoc = {
  createElementNS: (ns, name) => ({
    ns, name, attrs: {}, children: [],
    setAttribute(key, value) { this.attrs[key] = value; },
    append(...nodes) { this.children.push(...nodes); },
  }),
};
const walk = (node) => [node, ...(node.children ?? []).flatMap(walk)];

test("퀵 목록의 태그마다 그림이 있고, 그림은 glyphs.json 의 것이다", () => {
  for (const group of GROUPS) {
    for (const tag of rules.quick[group]) {
      assert.ok(glyphSpec(group, tag), `${group}: ${tag}`);
      assert.deepEqual(glyphSpec(group, tag), data[group][tag], `${group}: ${tag}`);
    }
    // 퀵 목록에 없는 태그의 그림은 쓰이지 않는다
    for (const tag of Object.keys(data[group])) assert.ok(rules.quick[group].includes(tag), `${group}: ${tag}`);
  }
  assert.deepEqual(Object.keys(data).filter((key) => key !== "note"), GROUPS);
  assert.equal(glyphSpec("pose", "flying"), null);
  assert.equal(glyphSpec("pose", "constructor"), null);
  assert.equal(glyphSpec("note", "standing"), null);
  assert.equal(glyph("view", "flying", { doc: fakeDoc }), null);
});

test("그림 데이터는 상자 안의 숫자로만 그린다", () => {
  for (const group of GROUPS) {
    for (const [tag, spec] of Object.entries(data[group])) {
      assert.deepEqual(Object.keys(spec).filter((k) => !["box", "w", "parts", "clip"].includes(k)), [], tag);
      const [w, h] = spec.box;
      const parts = [...spec.parts, ...(spec.clip?.parts ?? [])];
      for (const part of parts) {
        const kinds = ["l", "c", "d", "r"].filter((k) => k in part);
        assert.equal(kinds.length, 1, `${tag}: ${JSON.stringify(part)}`);
        const nums = part.l ?? part.r ?? (part.c ? part.c.slice(0, 2) : []);
        for (const v of nums) assert.ok(Number.isFinite(v) && v >= 0 && v <= Math.max(w, h), `${tag}: ${v}`);
        if (part.l) assert.equal(part.l.length % 2, 0, `${tag}: 짝이 안 맞는 좌표`);
      }
      if (spec.clip) {
        const [top, bottom] = spec.clip.y;
        assert.ok(top >= 0 && top < bottom && bottom <= h, `${tag}: clip ${spec.clip.y}`);
      }
    }
  }
});

test("SVG 로 그리면 글자색을 따르고 크롭은 틀 안만 진하게 한다", () => {
  const svg = drawGlyph(glyphSpec("pose", "standing"), { size: 18, doc: fakeDoc });
  assert.equal(svg.name, "svg");
  assert.equal(svg.attrs.viewBox, "0 0 28 28");
  assert.equal(svg.attrs.height, "18");
  assert.equal(svg.attrs["aria-hidden"], "true");
  assert.equal(walk(svg).find((n) => n.name === "g").attrs.stroke, "currentColor");

  const crops = [glyph("framing", "portrait", { doc: fakeDoc }), glyph("framing", "full body", { doc: fakeDoc })];
  const ids = crops.map((c) => walk(c).find((n) => n.name === "clipPath").attrs.id);
  assert.notEqual(ids[0], ids[1]);
  for (const [i, crop] of crops.entries()) {
    assert.ok(walk(crop).some((n) => n.attrs["clip-path"] === `url(#${ids[i]})`));
  }
  const rect = walk(crops[0]).find((n) => n.name === "clipPath").children[0];
  assert.deepEqual([rect.attrs.y, rect.attrs.height], ["0.4", String(8.8 - 0.4)]);
});
