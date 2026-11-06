// 서비스 워커: 오른쪽 클릭 메뉴·단축키 → 입력창에서 글을 읽고(page/field.js), 정렬하고, 다시 넣는다.
// activeTab 만 쓰므로 사용자가 메뉴·단축키를 누른 탭에만 들어간다.

import { findPreset, listPresets, loadBoxSettings, loadPack, loadSettings, saveBoxSetting } from "./engine/store.js";
import { boxGroup, boxSettings, isSortableSelection, optionsFor, organizeForField, sameLines, selectionProblem, summary } from "./engine/text.js";

const ROOT_MENU = "tagorg";
const PRESET_PREFIX = "preset|";
const PANEL_MENU = "panel";

// create 는 콜백으로 lastError 를 읽어야 "Unchecked runtime.lastError" 로 남지 않는다.
const addMenu = (item) =>
  new Promise((resolve) => {
    chrome.contextMenus.create(item, () => {
      if (chrome.runtime.lastError) console.warn(chrome.runtime.lastError.message);
      resolve();
    });
  });

async function buildMenus() {
  let presets = null;
  let failure = null;
  try {
    presets = await listPresets();
  } catch (e) {
    failure = e;
  }
  // 목록을 다 읽은 뒤에 지우고 만든다. 그 사이에 기다리면 다른 재구성이 끼어들어 같은 id 를 두 번 만든다.
  await chrome.contextMenus.removeAll();
  const contexts = ["editable"];
  await addMenu({ id: ROOT_MENU, title: "프롬프트 정렬", contexts });
  if (failure) {
    await addMenu({ id: PANEL_MENU, parentId: ROOT_MENU, title: "자료가 없습니다 — 설정 열기", contexts });
    console.error(failure);
    return;
  }
  for (const preset of presets) {
    await addMenu({ id: PRESET_PREFIX + preset.id, parentId: ROOT_MENU, title: preset.label, contexts });
  }
}

// 설치·시작·가져오기 변경이 겹쳐 불려도 한 번에 하나씩 차례로 만든다.
let menuQueue = Promise.resolve();
function rebuildMenus() {
  menuQueue = menuQueue.then(buildMenus).catch((e) => console.error(e));
  return menuQueue;
}

const inFrames = (tabId, frameIds, details) =>
  chrome.scripting.executeScript({
    target: frameIds ? { tabId, frameIds } : { tabId, allFrames: true },
    ...details,
  });

function callField(tabId, frameId, method, ...args) {
  return inFrames(tabId, [frameId], {
    func: (name, values) => globalThis.__tagorgField[name](...values),
    args: [method, args],
  }).then((results) => results[0]?.result);
}

const notify = (tabId, frameId, message, kind) => callField(tabId, frameId, "toast", message, kind);

// 글을 고른 프레임을 찾는다. 메뉴는 프레임을 알려 주고, 단축키는 전 프레임에서 포커스가 있는 곳을 찾는다.
async function capture(tabId, frameId) {
  const frameIds = frameId === undefined ? null : [frameId];
  await inFrames(tabId, frameIds, { files: ["page/field.js"] });
  const results = await inFrames(tabId, frameIds, { func: () => globalThis.__tagorgField.capture() });
  const hit = results.find((r) => r.result);
  return hit ? { frameId: hit.frameId, field: hit.result } : null;
}

// 글 하나를 정렬한다. 못 하면 {problem}. box: NovelAI 입력창 종류(그 묶음의 설정을 쓴다).
async function sortText({ text, partial = false, singleLine = false, presetId, hostname, box }) {
  if (!text.trim()) return { problem: "정렬할 글이 없습니다" };
  const problem = partial ? selectionProblem(text) : "";
  if (problem) return { problem: `${problem}. 강조 전체를 포함해 고르세요.` };
  const [pack, global] = await Promise.all([loadPack(), loadSettings()]);
  const settings = box ? boxSettings(global, await loadBoxSettings(), box) : global;
  const preset = await findPreset(presetId || settings.preset || settings.default_preset);
  const options = optionsFor(settings, preset.arrangement, preset.layout, hostname);
  if (singleLine) options.line_per_section = false;   // input 은 줄바꿈을 받지 않는다
  const done = organizeForField(text, pack, options, { partial });
  if (!done.text.trim()) return { problem: `모두 빠져서 바꾸지 않았습니다 (${done.removed.length}개)` };
  return { output: done.output, label: preset.label, summary: summary(done), unchanged: sameLines(text, done.output) };
}

async function run(tab, frameId, presetId) {
  let target = null;
  try {
    target = await capture(tab.id, frameId);
    if (!target) {
      // 단축키는 입력창이 아닌 곳에서 눌러도 조용히 넘어간다
      if (frameId !== undefined) await notify(tab.id, frameId, "입력창을 찾지 못했습니다", "warn");
      return;
    }
    const { field } = target;
    let { text, partial } = field;
    if (partial && !isSortableSelection(text)) {
      ({ text, partial } = await callField(tab.id, target.frameId, "selectAll"));
    }
    const sorted = await sortText({ text, partial, singleLine: field.singleLine, presetId,
      hostname: new URL(tab.url || "http://x").hostname });
    if (sorted.problem) {
      await notify(tab.id, target.frameId, sorted.problem, "warn");
      return;
    }
    const applied = await callField(tab.id, target.frameId, "apply", text, sorted.output);
    if (applied.ok) await notify(tab.id, target.frameId, `${sorted.label}: ${sorted.summary}`, "ok");
    else await notify(tab.id, target.frameId, applied.reason, "warn");
  } catch (e) {
    console.error(e);
    if (target) await notify(tab.id, target.frameId, `정렬하지 못했습니다: ${e.message}`, "warn").catch(() => {});
  }
}

chrome.runtime.onInstalled.addListener(rebuildMenus);
chrome.runtime.onStartup.addListener(rebuildMenus);
chrome.storage.onChanged.addListener((changes) => {
  if (changes.imported_arrangements) rebuildMenus();
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === PANEL_MENU) {
    chrome.tabs.create({ url: chrome.runtime.getURL("panel/panel.html") });
  } else if (String(info.menuItemId).startsWith(PRESET_PREFIX)) {
    run(tab, info.frameId ?? 0, String(info.menuItemId).slice(PRESET_PREFIX.length));
  }
});

chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "organize-selection" && tab) run(tab);
});

// NovelAI 버튼(nai/content.js). 글을 읽고 넣는 일은 그쪽에서 하고 여기서는 정렬만 한다.
async function naiSort({ text, partial, whole, kind, presetId, hostname }) {
  const widened = partial && !isSortableSelection(text);
  const sorted = await sortText({ text: widened ? whole : text, partial: partial && !widened, presetId, hostname, box: kind });
  return { ...sorted, widened, text: widened ? whole : text };
}

async function naiMenu({ kind }) {
  const [presets, settings, stored] = await Promise.all([listPresets(), loadSettings(), loadBoxSettings()]);
  return { presets: presets.map(({ id, label }) => ({ id, label })), chosen: boxSettings(settings, stored, kind).preset };
}

async function naiChoose({ kind, presetId }) {
  await saveBoxSetting(boxGroup(kind), { preset: presetId });
  return {};
}

const NAI_HANDLERS = { "nai-sort": naiSort, "nai-menu": naiMenu, "nai-choose": naiChoose };

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const handler = NAI_HANDLERS[message?.type];
  if (!handler || sender.id !== chrome.runtime.id || !sender.tab) return undefined;
  handler(message).then(respond, (e) => {
    console.error(e);
    respond({ problem: `정렬하지 못했습니다: ${e.message}` });
  });
  return true;
});
