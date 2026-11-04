// 확장의 자료: 팩(extension/data, `tagorg.py extension` 이 만든다)·프리셋 목록·설정·가져온 배열.
// 서비스 워커는 쉬면 내려가므로 팩은 쓸 때 읽는다(읽는 데 0.5초 안쪽). 패널도 같은 모듈을 쓴다.

import { TagPack } from "./core.js";
import { DEFAULT_SETTINGS, masterOverrides, tidyMasters } from "./text.js";

const IMPORTED_KEY = "imported_arrangements";
const SETTINGS_KEY = "settings";
const BOXES_KEY = "nai_boxes";
const MASTERS_KEY = "nai_masters";
const OVERRIDES_KEY = "nai_overrides";

let packPromise = null;
let catalogPromise = null;

async function readDataFile(name) {
  const response = await fetch(chrome.runtime.getURL(`data/${name}`));
  if (!response.ok) throw new Error(`data/${name} 을 읽지 못했습니다. python tagorg.py extension 으로 만드세요.`);
  return response;
}

export function loadPack() {
  packPromise ||= (async () => {
    const response = await readDataFile("tagpack.json.gz");
    const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
    return new TagPack(await new Response(stream).json());
  })().catch((e) => {
    packPromise = null;
    throw e;
  });
  return packPromise;
}

export function loadCatalog() {
  catalogPromise ||= readDataFile("catalog.json").then((r) => r.json()).catch((e) => {
    catalogPromise = null;
    throw e;
  });
  return catalogPromise;
}

export async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  return { ...DEFAULT_SETTINGS, ...(stored[SETTINGS_KEY] || {}) };
}

export const saveSettings = (settings) => chrome.storage.local.set({ [SETTINGS_KEY]: settings });

// NovelAI 입력창 묶음별 설정(engine/text.js 의 boxSettings 가 전체 설정과 합친다).
export async function loadBoxSettings() {
  const stored = await chrome.storage.local.get(BOXES_KEY);
  return stored[BOXES_KEY] || {};
}

export async function saveBoxSetting(group, patch) {
  const stored = await loadBoxSettings();
  await chrome.storage.local.set({ [BOXES_KEY]: { ...stored, [group]: { ...stored[group], ...patch } } });
}

export async function loadMasters() {
  const stored = await chrome.storage.local.get(MASTERS_KEY);
  return tidyMasters(stored[MASTERS_KEY] || {});
}

export async function loadMasterOverrides(masters) {
  const stored = await chrome.storage.local.get(OVERRIDES_KEY);
  return masterOverrides(masters, stored[OVERRIDES_KEY] || {});
}

// 원본과 그 묶음의 칩을 함께 저장한다. 꺼진 묶음의 칩은 남기지 않는다.
// 다른 탭의 패널이 고친 입력창을 덮지 않도록 바뀐 입력창만 합쳐 쓴다.
export async function saveMasters(changed, overrides) {
  const stored = await chrome.storage.local.get([MASTERS_KEY, OVERRIDES_KEY]);
  const masters = { ...(stored[MASTERS_KEY] || {}), ...changed };
  await chrome.storage.local.set({
    [MASTERS_KEY]: masters,
    [OVERRIDES_KEY]: masterOverrides(masters, { ...(stored[OVERRIDES_KEY] || {}), ...overrides }),
  });
}

export async function loadImported() {
  const stored = await chrome.storage.local.get(IMPORTED_KEY);
  return stored[IMPORTED_KEY] || [];
}

export const saveImported = (items) => chrome.storage.local.set({ [IMPORTED_KEY]: items });

// 같은 id 면 그 자리에서 바꾸고, 없으면 끝에 더한다.
export async function putImported(item) {
  const items = await loadImported();
  const at = items.findIndex((x) => x.id === item.id);
  if (at >= 0) items[at] = item;
  else items.push(item);
  await saveImported(items);
}

// 메뉴에 보일 프리셋: {id, label, layout?, arrangement?}. id 는 layout:… / pack:… / imported:…
export async function listPresets() {
  const catalog = await loadCatalog();
  const imported = await loadImported();
  return [
    ...Object.entries(catalog.layouts).map(([id, label]) => ({ id: `layout:${id}`, label, layout: id })),
    ...catalog.arrangements.map((a) => ({ id: `pack:${a.id}`, label: a.label, arrangement: a.data })),
    ...imported.map((a) => ({ id: `imported:${a.id}`, label: a.label, arrangement: a.data })),
  ];
}

export async function findPreset(id) {
  const presets = await listPresets();
  return presets.find((p) => p.id === id) || presets[0];
}
