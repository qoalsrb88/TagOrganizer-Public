// tests/test_extension_core.py 가 부른다: JS 코어로 케이스를 돌려 결과를 JSON 으로 쓴다.
// 사용: node run_core.mjs <tagpack.json 또는 .gz> <cases.json> <out.json>
import { readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { gazeToggle, viewToggle } from "../../extension/editor/model.js";
import { TagPack, arrangementFromLayout, organize, palette } from "../../extension/engine/core.js";

const [packPath, casesPath, outPath] = process.argv.slice(2);
const raw = readFileSync(packPath);
const pack = new TagPack(JSON.parse((packPath.endsWith(".gz") ? gunzipSync(raw) : raw).toString("utf-8")));
const cases = JSON.parse(readFileSync(casesPath, "utf-8"));
const results = cases.map(({ text, options, want, selected, tag, lifted }) => {
  if (want === "palette") return palette(pack);
  if (want === "gaze_toggle") return gazeToggle(palette(pack), selected, tag);
  if (want === "view_toggle") return viewToggle(palette(pack), selected, tag, lifted);
  if (want === "layouts") return Object.fromEntries(Object.keys(pack.layouts).map((id) => [id, arrangementFromLayout(pack, id)]));
  try {
    const { text: out, removed_text, report } = organize(text, pack, options);
    return { text: out, removed_text, report };
  } catch (e) {
    return { error: e.message };
  }
});
writeFileSync(outPath, JSON.stringify(results));
