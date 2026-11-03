// tests/test_extension_core.py 가 부른다: 배열 모델 조작을 JS 로 되풀이해 단계마다의 결과를 JSON 으로 쓴다.
// 사용: node run_model.mjs <cases.json> <out.json>
//   cases: [{data, ops: [[Python 메서드 이름, ...인자]], palette}]
import { readFileSync, writeFileSync } from "node:fs";
import { Model, Taxonomy } from "../../extension/editor/model.js";

const camel = (name) => name.replace(/_(\w)/g, (_, c) => c.toUpperCase());

const [casesPath, outPath] = process.argv.slice(2);
const cases = JSON.parse(readFileSync(casesPath, "utf-8"));
const results = cases.map(({ data, ops, palette }) => {
  const model = new Model(data);
  const steps = ops.map(([name, ...args]) => {
    const ret = model[camel(name)](...args);
    return { ret: ret ?? null, json: model.toJSON() };
  });
  const tax = new Taxonomy(palette);
  const specs = Object.fromEntries(model.chunks);
  return {
    loaded: new Model(data).toJSON(),
    steps,
    labels: Object.fromEntries(Object.entries(specs).map(([cid, spec]) => [cid, tax.chunkLabel(spec)])),
    colors: Object.fromEntries(Object.entries(specs).map(([cid, spec]) => [cid, tax.chunkColor(spec)])),
    selectors: Object.values(specs).flatMap((spec) => spec.members ?? []).map((m) => tax.selectorLabel(m)),
    uncovered: tax.uncovered(model),
  };
});
writeFileSync(outPath, JSON.stringify(results));
