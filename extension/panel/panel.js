// 도구 모음 패널: 붙여넣어 정렬하기, 설정, 배열 가져오기.

import { organize } from "../engine/core.js";
import { findPreset, listPresets, loadCatalog, loadImported, loadPack, loadSettings, saveImported, saveSettings } from "../engine/store.js";
import { optionsFor, summary, organizeForField } from "../engine/text.js";

const $ = (id) => document.getElementById(id);
const SELECTS = { default_preset: "default_preset", filter: "filter", max_rating: "max_rating", rules: "rules",
  output_dialect: "output", comments: "comments" };
const CHECKS = ["line_per_section", "guess", "nai_button"];

function setStatus(el, message, warn = false) {
  el.textContent = message;
  el.classList.toggle("warn", warn);
}

function fillSelect(select, items) {
  select.replaceChildren(...items.map(([value, label]) => new Option(label, value)));
}

async function fillPresets(settings) {
  const presets = await listPresets();
  fillSelect($("default_preset"), presets.map((p) => [p.id, p.label]));
  $("default_preset").value = settings.default_preset;
  if (!$("default_preset").value) $("default_preset").selectedIndex = 0;
}

async function renderImported() {
  const items = await loadImported();
  $("imported").replaceChildren(...items.map((item) => {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = item.label;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet";
    remove.textContent = "지우기";
    remove.addEventListener("click", async () => {
      await saveImported((await loadImported()).filter((x) => x.id !== item.id));
      await renderImported();
      await fillPresets(await loadSettings());
    });
    row.append(name, remove);
    return row;
  }));
}

async function sortInput() {
  const text = $("input").value;
  if (!text.trim()) return;
  setStatus($("status"), "정렬 중…");
  try {
    const [pack, settings] = await Promise.all([loadPack(), loadSettings()]);
    const preset = await findPreset($("default_preset").value);
    const done = organizeForField(text, pack, optionsFor(settings, preset.arrangement, preset.layout, ""));
    $("output").value = done.text;
    setStatus($("status"), summary(done));
  } catch (e) {
    setStatus($("status"), e.message, true);
  }
}

async function importArrangement() {
  const status = $("import_status");
  try {
    const data = JSON.parse($("import_json").value);
    organize("1girl", await loadPack(), { arrangement: data });   // 틀리면 코어의 오류 글을 보여 준다
    const label = String(data.label || "").trim() || "가져온 배열";
    const items = (await loadImported()).filter((x) => x.label !== label);
    await saveImported([...items, { id: String(Date.now()), label, data }]);
    $("import_json").value = "";
    setStatus(status, `추가했습니다: ${label}`);
    await renderImported();
    await fillPresets(await loadSettings());
  } catch (e) {
    setStatus(status, e.message, true);
  }
}

async function main() {
  const catalog = await loadCatalog();
  const settings = await loadSettings();
  fillSelect($("filter"), Object.entries(catalog.filters));
  await fillPresets(settings);
  for (const [id, key] of Object.entries(SELECTS)) {
    if (id !== "default_preset") $(id).value = settings[key];
    $(id).addEventListener("change", async () => {
      await saveSettings({ ...(await loadSettings()), [key]: $(id).value });
    });
  }
  for (const id of CHECKS) {
    $(id).checked = settings[id];
    $(id).addEventListener("change", async () => {
      await saveSettings({ ...(await loadSettings()), [id]: $(id).checked });
    });
  }
  $("run").addEventListener("click", sortInput);
  $("copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("output").value);
    setStatus($("status"), "복사했습니다");
  });
  $("import").addEventListener("click", importArrangement);
  $("about").textContent = `태그 ${catalog.tags.toLocaleString()}개 · 팩 ${catalog.pack_version} · 프롬프트는 이 브라우저 밖으로 나가지 않습니다`;
  await renderImported();
}

main().catch((e) => setStatus($("status"), e.message, true));
