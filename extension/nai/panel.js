// NovelAI 미리보기 패널(확장 iframe). 입력창 글은 콘텐츠 스크립트(nai/content.js)가 포트로 보내고, 정렬은 여기서 한다.
// 페이지는 이 iframe 안을 볼 수 없다. 패널을 닫으면 프레임째 사라져 팩이 차지한 메모리도 돌아간다.

import { ArrangementEditor, el, field, openDialog, textInput } from "../editor/editor.js";
import { Model, gazeToggle, poseToggle, splitItems, viewToggle } from "../editor/model.js";
import { arrangementFromLayout, organize, palette, warningText } from "../engine/core.js";
import { listPresets, loadBoxSettings, loadImported, loadMasters, loadMasterOverrides, loadPack, loadSettings, putImported, saveBoxSetting, saveMasters } from "../engine/store.js";
import { boxGroup, boxSettings, fieldText, masterDrift, masterOverrides, masterWouldOverwrite, optionsFor, sameLines, sourceText, tidyMasters } from "../engine/text.js";

const HOSTNAME = "novelai.net";
const DRAFT_KEY = "nai_draft";
const EDITING_KEY = "nai_editing";
const SPLIT_KEY = "nai_editor_split";
const IMPORTED_PREFIX = "imported:";
const UNDO_LIMIT = 100;
const EDITOR_SIDE_FROM = 620;   // 이 폭부터 청크와 미리보기를 좌우로 놓는다
const EDITOR_PREVIEW_DELAY = 60;
const NOTICE_MS = 6000;
const MASTER_SAVE_DELAY = 250;
const PORT_NAME = "nai-panel";
const DIALECT_NAMES = { nai: "NAI", comfyui: "ComfyUI", anima: "Anima" };
const OVERRIDES = ["framing"];
const APPLIED = "적용했습니다. 되돌리기는 입력창에서 Ctrl+Z 입니다.";
const GENERATE_SETTLE_MS = 300;       // 넣은 글을 NovelAI 가 자기 상태에 반영할 시간
const GENERATE_LOCK_MIN_MS = 1500;    // 누른 뒤 NovelAI 버튼이 바뀌기를 기다리는 최소 시간(연타로 두 번 생성되지 않게)
const GENERATE_LOCK_MAX_MS = 60000;   // NovelAI 버튼이 돌아오지 않아도 이만큼 지나면 다시 누를 수 있다
const FALLBACK_COLOR = "#7a7c99";

const $ = (id) => document.getElementById(id);

let pack = null;
let quick = { framing: [], view: [], gaze: [], pose: [] };
let chipPalette = { quick, gaze_conflicts: {}, pose_conflicts: {}, view_conflicts: {}, view_lift: [] };
let settings = null;
let stored = {};
let presets = [];
let boxes = [];
let activeId = null;
let result = null;              // 지금 보이는 정렬 결과 {box, output, unchanged}
const overrides = {};           // 묶음별 크롭·시점·시선·포즈. 원본이 켜진 묶음만 저장한다(버튼 정렬에는 쓰지 않는다)
let masters = {};               // 입력창 id 별 {on, text, lastApplied}
let masterSaveTimer = 0;
let mastersDirty = false;
let dirtyMasters = new Set();   // 저장할 입력창 id. 아직 저장하지 않은 글은 다른 탭의 변경이 와도 지킨다
let confirmedImport = null;
let pendingImport = null;       // 머리글의 → 원본을 눌렀는데 입력창 목록이나 팩이 아직 오지 않았다
let pendingApply = null;        // 응답이 올 때까지 대상과 결과를 보관한다
let view = "chips";             // 보이는 탭: chips·text·master. 배열 편집 중에는 청크 화면이 chips·text 자리에 든다
let naiGenerate = null;         // NovelAI 의 Generate 버튼 {ready, label, cost}. 못 찾으면 null
let generateRun = false;        // 적용부터 누르기까지 진행 중
let pendingGenerate = null;
let generateLock = null;        // 누른 뒤 NovelAI 버튼이 돌아올 때까지 {at, label}

// 배열 편집: 편집 중인 배열은 입력창 묶음과 상관없이 하나다. 입력창을 옮겨 다니며 같은 배열로 미리 보고 적용한다.
let editor = null;
let editing = false;
let draft = null;               // {source: 프리셋 id, layout, arr: 배열 JSON 글("" 은 레이아웃 그대로), saved: 저장된 글}
let undoStack = [];
let redoStack = [];
let saveButton = null;
let undoButton = null;
let redoButton = null;

function setState(message, warn = false) {
  $("state").textContent = message;
  $("state").classList.toggle("warn", warn);
}

let noticeTimer = 0;
function setNotice(message, warn = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent = message;
  $("notice").classList.toggle("warn", warn);
  if (message) noticeTimer = setTimeout(() => setNotice(""), NOTICE_MS);
}

function flushMasters() {
  clearTimeout(masterSaveTimer);
  if (!mastersDirty) return;
  mastersDirty = false;
  const changed = Object.fromEntries([...dirtyMasters].map((id) => [id, masters[id]]));
  dirtyMasters = new Set();
  saveMasters(changed, overrides).catch((e) => {
    mastersDirty = true;
    for (const id of Object.keys(changed)) dirtyMasters.add(id);
    setNotice(`원본을 저장하지 못했습니다: ${e.message}`, true);
  });
}

function storeMasters(id = null, delay = 0) {
  if (id) dirtyMasters.add(id);
  mastersDirty = true;
  clearTimeout(masterSaveTimer);
  if (delay) masterSaveTimer = setTimeout(flushMasters, delay);
  else flushMasters();
}

const masterFor = (box) => box && boxGroup(box.kind) !== "uc" ? masters[box.id] : undefined;
const inputText = (box) => sourceText(box?.text ?? "", masterFor(box));

function renderMaster(box, output = "") {
  const master = masterFor(box);
  $("master").hidden = !box || boxGroup(box.kind) === "uc";
  $("master_on").checked = Boolean(master?.on);
  renderTabs(box);
  const text = master?.text ?? "";
  // 값을 그대로 두면 입력 중 커서와 선택이 유지된다.
  if ($("master_text").value !== text) $("master_text").value = text;
  $("master_warning").hidden = !masterDrift(box?.text ?? "", master, output);
  if (confirmedImport && (confirmedImport.id !== box?.id || confirmedImport.text !== box.text || confirmedImport.previous !== text)) {
    confirmedImport = null;
  }
}

function renderTabs(box) {
  const hasMaster = Boolean(masterFor(box)?.on);
  if (view === "master" && !hasMaster) view = "chips";
  const showEditor = editing && view !== "master";
  $("editor").hidden = !showEditor;
  $("preview").hidden = showEditor;
  $("views").hidden = editing && !hasMaster;
  $("show_chips").textContent = editing ? "배열" : "색 미리보기";
  $("show_text").hidden = editing;
  $("show_master").hidden = !hasMaster;
  $("show_chips").setAttribute("aria-pressed", String(showEditor || view === "chips"));
  $("show_text").setAttribute("aria-pressed", String(!editing && view === "text"));
  $("show_master").setAttribute("aria-pressed", String(view === "master"));
  $("chips").hidden = view !== "chips";
  $("text").hidden = view !== "text";
  $("master_body").hidden = view !== "master";
}

// 입력창 글을 원본으로. 보관한 원본과 다르면 한 번 더 눌러야 바꾼다(풀버전 원본을 실수로 덮지 않게).
function importMaster(box) {
  if (!box || boxGroup(box.kind) === "uc") return;
  if (!box.text.trim()) {
    setNotice(`${box.label} 입력창이 비어 있어 가져오지 않았습니다.`, true);
    return;
  }
  const master = masterFor(box) || { on: false, text: "", lastApplied: "" };
  const kept = master.text?.trim() && !sameLines(box.text, master.text);
  if (kept && !(confirmedImport?.id === box.id && confirmedImport.text === box.text
    && confirmedImport.previous === master.text && confirmedImport.until > performance.now())) {
    confirmedImport = { id: box.id, text: box.text, previous: master.text, until: performance.now() + NOTICE_MS };
    setNotice("보관한 원본과 다릅니다. 원본을 바꾸려면 한 번 더 누르세요.", true);
    return;
  }
  masters[box.id] = { ...master, on: true, group: boxGroup(box.kind), lines: true, text: fieldText(box.text) };
  confirmedImport = null;
  storeMasters(box.id);
  view = "master";
  render();
  setNotice("입력창 글을 원본으로 가져왔습니다.");
}

function flushImport() {
  const box = boxes.find((b) => b.id === pendingImport);
  if (!pack || !box) return;
  pendingImport = null;
  importMaster(box);
}

// 입력창의 글꼴·줄 높이·여백을 원본 칸에 그대로 쓴다(NovelAI 테마를 따라가도록 값을 받아서).
function applyFieldLook(look) {
  const { padding, background, ...text } = look ?? {};
  const area = $("master_text");
  Object.assign(area.style, text);
  if (padding) area.style.padding = padding;
  if (background) area.style.background = background;
}

function renderBoxButtons() {
  const buttons = boxes.map((box) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = box.label;
    button.setAttribute("aria-pressed", String(box.id === activeId));
    button.addEventListener("click", () => port.postMessage({ type: "select", id: box.id }));
    return button;
  });
  $("boxes").replaceChildren(...buttons);
}

const quickChip = (label, pressed, onClick) => {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.textContent = label;
  chip.setAttribute("aria-pressed", String(pressed));
  chip.addEventListener("click", () => {
    onClick();
    // 켠 원본이 없는 묶음의 칩은 패널을 닫으면 잊는다
    if (Object.keys(masterOverrides(masters, overrides)).length) storeMasters();
  });
  return chip;
};

// 시선·포즈는 여러 개를 고른다. 지금 크롭·시점이 막는 태그는 숨기되 선택은 두어, 크롭·시점을 되돌리면 다시 든다.
function renderBlockable(name, toggle, group, blocked) {
  const selected = splitItems(overrides[group]?.[name]);
  const set = (tags) => {
    overrides[group] = { ...overrides[group], [name]: tags.join(", ") };
    render();
  };
  const chips = quick[name].filter((tag) => !blocked.includes(tag)).map((tag) =>
    quickChip(tag, selected.includes(tag), () => set(toggle(chipPalette, selected, tag))));
  // 고른 것이 모두 막히면 프롬프트 그대로이므로 자동으로 보인다
  const auto = !selected.some((tag) => !blocked.includes(tag));
  $(name).replaceChildren(quickChip("자동", auto, () => set([])), ...chips);
  $(name).parentElement.hidden = !quick[name].length;
}

// 시점도 여러 개를 고른다. 부딪치는 칩을 켜면 앞의 칩이 꺼지고, lift 칩(multiple views)을 켜면 함께 고를 수 있다.
function renderView(group, lifted) {
  const selected = splitItems(overrides[group]?.view);
  const set = (tags) => {
    overrides[group] = { ...overrides[group], view: tags.join(", ") };
    render();
  };
  const toggle = (tag) => {
    const next = viewToggle(chipPalette, selected, tag, lifted);
    const dropped = selected.filter((t) => t !== tag && !next.includes(t));
    if (dropped.length) setNotice(`${dropped.join(", ")}: ${tag} 와 함께 쓸 수 없어 껐습니다. ${chipPalette.view_lift.join(", ")} 를 켜면 함께 고를 수 있습니다.`);
    set(next);
  };
  const chips = quick.view.map((tag) => quickChip(tag, selected.includes(tag), () => toggle(tag)));
  $("view").replaceChildren(quickChip("자동", !selected.length, () => set([])), ...chips);
}

function renderOverrides(box, gazeBlocked = [], lifted = false, poseBlocked = []) {
  const group = boxGroup(box.kind);
  $("overrides").hidden = group === "uc";
  renderBlockable("gaze", gazeToggle, group, gazeBlocked);
  renderBlockable("pose", poseToggle, group, poseBlocked);
  renderView(group, lifted);
  for (const name of OVERRIDES) {
    const current = overrides[group]?.[name] || "";
    const chips = [["", "자동"], ...quick[name].map((tag) => [tag, tag])].map(([value, label]) =>
      quickChip(label, value === current, () => {
        overrides[group] = { ...overrides[group], [name]: value };
        render();
      }));
    $(name).replaceChildren(...chips);
  }
}

function tokenChip(token, chunk) {
  const chip = document.createElement("span");
  chip.className = "token";
  chip.style.setProperty("--c", chunk.color || FALLBACK_COLOR);
  chip.textContent = token.out;
  const notes = [chunk.label];
  if (token.removed) {
    chip.classList.add("removed");
    notes.push(`뺌: ${token.removed}`);
  }
  if (token.flagged) {
    chip.classList.add("flagged");
    notes.push(`알림: ${token.flagged}`);
  }
  if (token.guess) {
    chip.classList.add("guess");
    notes.push(`추정: ${token.guess}`);
  }
  if (token.injected) {
    chip.classList.add("injected");
    notes.push("덮어쓰기로 넣음");
  }
  if (token.warning) {
    chip.classList.add("warned");
    notes.push(warningText(token.warning));
  }
  chip.title = notes.join("\n");
  return chip;
}

function renderChips(report) {
  const parts = [];
  report.arranged.forEach((section, index) => {
    if (index) parts.push(Object.assign(document.createElement("div"), { className: "break", textContent: "BREAK" }));
    for (const row of section.rows) {
      const line = document.createElement("div");
      line.className = "line";
      for (const chunk of row) line.append(...chunk.tokens.map((i) => tokenChip(report.tokens[i], chunk)));
      parts.push(line);
    }
  });
  $("chips").replaceChildren(...parts);
}

function stateLine(report, ms) {
  const c = report.counts;
  const { dialect } = report;
  const parts = [`처리 ${ms.toFixed(1)}ms`, `토큰 ${c.total}`, `뺌 ${c.removed}`];
  if (c.flagged) parts.push(`알림 ${c.flagged}`);
  if (c.unclassified) parts.push(`미분류 ${c.unclassified}`);
  parts.push(`DB에 없음 ${c.unknown}`);
  if (c.guessed) parts.push(`추정 ${c.guessed}`);
  parts.push(`문법 ${dialect.auto ? `자동(${DIALECT_NAMES[dialect.input] || dialect.input}) → ` : ""}${DIALECT_NAMES[dialect.output] || dialect.output}`);
  if (c.warnings) parts.push(`문법 경고 ${c.warnings}`);
  return parts.join(" · ");
}

function clearResult(message, warn = false) {
  result = null;
  $("chips").replaceChildren();
  $("text").value = "";
  $("apply").disabled = true;
  $("apply").textContent = "적용";
  $("copy").disabled = true;
  setState(message, warn);
  renderGenerate();
}

const activeBox = () => boxes.find((b) => b.id === activeId);

// 지금 입력창에 쓸 옵션. 편집 중이면 저장한 프리셋 대신 편집 중인 배열을 쓴다.
function currentOptions(box) {
  const effective = boxSettings(settings, stored, box?.kind);
  const merged = { ...effective, ...overrides[boxGroup(box?.kind)] };
  if (editing && draft) {
    return draft.arr ? optionsFor(merged, JSON.parse(draft.arr), null, HOSTNAME) : optionsFor(merged, null, draft.layout, HOSTNAME);
  }
  const preset = presetOf(effective.preset) || presets[0];
  return optionsFor(merged, preset.arrangement, preset.layout, HOSTNAME);
}

function render() {
  renderBoxButtons();
  if (!pack) return;
  $("state").hidden = editing;
  const box = activeBox();
  renderMaster(box);
  if (!box) {
    $("overrides").hidden = true;
    clearResult("보이는 입력창이 없습니다");
    return;
  }
  renderOverrides(box);
  const group = boxGroup(box.kind);
  const effective = boxSettings(settings, stored, box.kind);
  $("preset").value = editing && draft ? draft.source : effective.preset;
  if ($("preset").selectedIndex < 0) $("preset").selectedIndex = 0;
  $("rules").value = effective.rules;
  $("guess").checked = effective.guess;
  const source = inputText(box);
  if (!source.trim()) {
    $("state").hidden = false;
    clearResult(masterFor(box)?.on ? "원본 프롬프트가 비어 있습니다" : `${box.label} 입력창이 비어 있습니다`);
    return;
  }
  let sorted;
  const started = performance.now();
  try {
    sorted = organize(source, pack, currentOptions(box));
  } catch (e) {
    $("state").hidden = false;
    clearResult(e.message, true);
    return;
  }
  const ms = performance.now() - started;
  const unchanged = sameLines(box.text, sorted.text);
  result = { box, output: sorted.text, unchanged };
  renderMaster(box, sorted.text);
  renderOverrides(box, sorted.report.gaze_blocked, sorted.report.view_lifted, sorted.report.pose_blocked);
  renderChips(sorted.report);
  $("text").value = sorted.text;
  setState(stateLine(sorted.report, ms));
  $("copy").disabled = !sorted.text;
  $("apply").disabled = Boolean(pendingApply) || generateRun || unchanged || !sorted.text.trim();
  $("apply").textContent = unchanged ? "정렬됨" : "적용";
  renderGenerate();
  if (!sorted.text.trim()) setNotice(`모두 빠집니다 (${sorted.report.counts.removed}개). 적용할 수 없습니다.`, true);
}

// ─── 적용 후 생성 ────────────────────────────────────────────────

// 생성에 들어갈 입력창: 원본을 켠 다른 보이는 입력창과 지금 입력창(마지막에 넣어 대상이 그대로 남게 한다).
// Anlas 를 쓰는 동작이라, 원본이 비었거나 직접 고친 글을 덮게 되면 넘어가지 않고 이유를 돌려준다.
function generatePlan() {
  const items = [];
  let problem = "";
  const edited = (box) => `${box.label} 입력창을 직접 고쳤습니다. 그 입력창을 고른 뒤 원본으로 가져오거나 적용하고 나서 생성하세요.`;
  for (const box of boxes) {
    const master = masterFor(box);
    if (box.id === result.box.id || !master?.on) continue;
    // 원본이 비어도 칩의 태그는 결과에 들어가므로 원본 글을 먼저 본다(지금 입력창의 빈 원본 안내와 같은 기준)
    const source = inputText(box);
    const text = source.trim() ? organize(source, pack, currentOptions(box)).text : "";
    if (!text.trim()) problem ||= `${box.label} 원본이 비어 있어 생성하지 않았습니다.`;
    else if (masterWouldOverwrite(box.text, master, text)) problem ||= edited(box);
    items.push({ box, output: text, apply: !sameLines(box.text, text) });
  }
  if (masterDrift(result.box.text, masterFor(result.box), result.output)) problem ||= edited(result.box);
  items.push({ box: result.box, output: result.output, apply: !result.unchanged });
  return { items, problem };
}

function generateLocked() {
  if (!generateLock) return false;
  const age = performance.now() - generateLock.at;
  const back = naiGenerate?.ready && naiGenerate.label === generateLock.label;
  if (age < GENERATE_LOCK_MAX_MS && (age < GENERATE_LOCK_MIN_MS || !back)) return true;
  generateLock = null;
  return false;
}

function renderGenerate() {
  const button = $("generate");
  button.hidden = !naiGenerate;
  $("apply").classList.toggle("primary", !naiGenerate);
  if (!naiGenerate) return;
  const busy = generateRun || generateLocked();
  let plan = null;
  if (result?.output.trim() && !busy) {
    try {
      plan = generatePlan();
    } catch (e) {
      console.warn("[TagOrganize] 생성 계획을 만들지 못했습니다:", e);
    }
  }
  const applying = plan?.items.some((item) => item.apply);
  const label = busy ? "생성 중…" : applying ? "적용 후 생성" : "생성";
  button.textContent = naiGenerate.cost && !busy ? `${label} · ${naiGenerate.cost}` : label;
  button.title = `${applying ? "입력창에 결과를 넣고 " : ""}NovelAI 의 Generate 를 한 번 누릅니다${naiGenerate.cost ? ` (${naiGenerate.cost} Anlas)` : ""}`;
  button.disabled = busy || Boolean(pendingApply) || !naiGenerate.ready || !plan;
}

const postApply = (box, output) => new Promise((resolve) => {
  pendingApply = { id: box.id, output, resolve };
  port.postMessage({ type: "apply", id: box.id, expected: box.text, text: output });
});

// expected: 누르는 순간 입력창마다 있어야 할 글. 콘텐츠 스크립트가 누르기 직전에 대조한다.
const postGenerate = (expected) => new Promise((resolve) => {
  pendingGenerate = resolve;
  port.postMessage({ type: "generate", expected });
});

async function applyAndGenerate() {
  if (!result?.output.trim() || !naiGenerate?.ready || pendingApply || generateRun || generateLocked()) return;
  let plan;
  try {
    plan = generatePlan();
  } catch (e) {
    setNotice(e.message, true);
    return;
  }
  if (plan.problem) {
    setNotice(plan.problem, true);
    return;
  }
  const writes = plan.items.filter((item) => item.apply);
  const target = activeId;
  generateRun = true;
  render();
  try {
    for (const { box, output } of writes) {
      const applied = await postApply(box, output);
      if (!applied.ok) {
        setNotice(applied.reason, true);
        return;
      }
    }
    if (writes.at(-1) && writes.at(-1).box.id !== target) port.postMessage({ type: "select", id: target });   // 넣은 입력창으로 대상이 옮겨 갔다
    if (writes.length) await new Promise((resolve) => setTimeout(resolve, GENERATE_SETTLE_MS));
    const clicked = await postGenerate(plan.items.map(({ box, output }) => ({ id: box.id, text: output })));
    if (clicked.ok) {
      generateLock = { at: performance.now(), label: clicked.label };
      setTimeout(render, GENERATE_LOCK_MIN_MS + 50);
      setTimeout(render, GENERATE_LOCK_MAX_MS + 50);
      setNotice(writes.length ? "입력창에 넣고 생성을 눌렀습니다." : "생성을 눌렀습니다.");
    } else setNotice(clicked.reason, true);
  } finally {
    generateRun = false;
    render();
  }
}

function fillPresets() {
  $("preset").replaceChildren(...presets.map((p) => new Option(p.label, p.id)));
}

function changeBoxSetting(patch) {
  const box = boxes.find((b) => b.id === activeId);
  if (!box) return;
  const group = boxGroup(box.kind);
  stored = { ...stored, [group]: { ...stored[group], ...patch } };
  saveBoxSetting(group, patch);
  render();
}

function showView(name) {
  view = name;
  renderTabs(activeBox());
  if (editing && name !== "master") editor.refresh();
  if (name === "master") $("master_text").focus();
}

// ─── 배열 편집 ───────────────────────────────────────────────────

const draftDirty = () => Boolean(draft) && draft.arr !== draft.saved;
const presetOf = (id) => presets.find((p) => p.id === id);

function draftState() {
  if (!draft) return "";
  const name = presetOf(draft.source)?.label ?? "배열";
  if (!draftDirty()) return name;
  return draft.source.startsWith(IMPORTED_PREFIX) ? `${name} · 수정됨` : `${name} · 수정됨 — 저장하면 내 배열로`;
}

function storeDraft() {
  if (draftDirty()) chrome.storage.local.set({ [DRAFT_KEY]: draft });
  else chrome.storage.local.remove(DRAFT_KEY);
}

function openDraft(id) {
  const preset = presetOf(id) || presets[0];
  const arr = preset.arrangement ? JSON.stringify(new Model(preset.arrangement).toJSON()) : "";
  draft = { source: preset.id, layout: preset.layout || null, arr, saved: arr };
  undoStack = [];
  redoStack = [];
  storeDraft();
}

function afterDraftChange({ refresh = false } = {}) {
  storeDraft();
  if (refresh) editor.refresh();
  else editor.renderBar();
  updateEditButtons();
  render();
}

function updateEditButtons() {
  if (!saveButton) return;
  saveButton.disabled = !draftDirty();
  undoButton.disabled = !undoStack.length;
  redoButton.disabled = !redoStack.length;
}

function stepDraft(from, to) {
  if (!from.length) return;
  to.push(draft.arr);
  draft.arr = from.pop();
  afterDraftChange({ refresh: true });
}

// 프리셋에 쓴다. id 가 없으면 새로 만든다. 저장한 배열을 지금 입력창 묶음의 프리셋으로 삼는다.
async function writePreset(id, label) {
  const model = new Model(editor.model.toJSON());
  model.label = label;
  const data = model.toJSON();
  const item = { id: id || String(Date.now()), label, data };
  await putImported(item);
  presets = await listPresets();
  fillPresets();
  const arr = JSON.stringify(data);
  draft = { source: `${IMPORTED_PREFIX}${item.id}`, layout: null, arr, saved: arr };
  storeDraft();
  const box = activeBox();
  if (box) changeBoxSetting({ preset: draft.source });
  afterDraftChange({ refresh: true });
  setNotice(`저장했습니다: ${label}`);
}

function saveDraftAs() {
  let confirmed = null;
  openDialog(
    "배열 저장",
    (panel) => {
      const name = field(panel, "배열 이름", textInput(editor.model.label || "내 배열"));
      return () => name.value.trim();
    },
    async (label) => {
      if (!label) return "이름을 적으세요.";
      const same = (await loadImported()).find((item) => item.label === label);
      if (same && `${IMPORTED_PREFIX}${same.id}` !== draft.source && confirmed !== label) {
        confirmed = label;
        return `"${label}" 이(가) 이미 있습니다. 덮어쓰려면 저장을 한 번 더 누르세요.`;
      }
      await writePreset(same?.id, label);
      return null;
    },
    "저장",
  );
}

function saveDraft() {
  if (!draft.source.startsWith(IMPORTED_PREFIX)) {
    saveDraftAs();       // 레이아웃과 공유·내 배열(팩에 든 것)은 고치지 않고 새 배열로 저장한다
    return;
  }
  writePreset(draft.source.slice(IMPORTED_PREFIX.length), presetOf(draft.source)?.label || editor.model.label || "내 배열")
    .catch((e) => setNotice(e.message, true));
}

// 저장하지 않은 변경을 버릴지 묻고 바꾼다.
function replaceDraft(id, then) {
  const go = () => {
    openDraft(id);
    then?.();
    afterDraftChange({ refresh: true });
  };
  if (!draftDirty()) {
    go();
    return;
  }
  render();    // 답할 때까지 목록은 지금 배열을 가리킨다
  openDialog(
    "저장하지 않은 변경",
    (panel) => {
      panel.append(el("div", "", `${draftState()}\n이 변경을 버리고 다른 배열을 열까요?`));
      return () => null;
    },
    async () => {
      go();
      return null;
    },
    "버리고 열기",
  );
}

function editorHost() {
  return {
    sideFrom: EDITOR_SIDE_FROM,
    previewDelay: EDITOR_PREVIEW_DELAY,
    split: 0,
    arrValue: () => draft?.arr ?? "",
    layoutId: () => draft?.layout,
    setArrValue(value) {
      if (!draft || value === draft.arr) return;
      undoStack.push(draft.arr);
      if (undoStack.length > UNDO_LIMIT) undoStack.shift();
      redoStack = [];
      draft.arr = value;
      afterDraftChange();
    },
    values() {
      const effective = boxSettings(settings, stored, activeBox()?.kind);
      return { rules: effective.rules, guess: effective.guess, ...overrides[boxGroup(activeBox()?.kind)] };
    },
    inputText: () => inputText(activeBox()),
    loadPalette: async () => ({
      palette: palette(pack),
      layouts: Object.fromEntries(Object.keys(pack.layouts).map((id) => [id, arrangementFromLayout(pack, id)])),
    }),
    async preview({ text, arrangement }) {
      const box = activeBox();
      const options = currentOptions(box);
      if (arrangement) {
        options.arrangement = arrangement;
        delete options.layout;
      }
      const result = organize(text, pack, options);
      const { report } = result;
      return { text: result.text, removed: result.removed_text, arrangement: report.arrangement, counts: report.counts,
        arranged: report.arranged, tokens: report.tokens, dialect: report.dialect,
        warnings: report.warnings.map((w) => ({ ...w, text: warningText(w.reason) })) };
    },
    async validate(json) {
      try {
        organize("", pack, { arrangement: json });
        return null;
      } catch (e) {
        return e.message;
      }
    },
    renderState(box) {
      box.textContent = draftState();
      box.title = draftState();
    },
    previewNote: () => (activeBox() ? "" : "보이는 입력창이 없습니다"),
    saveSplit: (value) => chrome.storage.local.set({ [SPLIT_KEY]: value }),
    guessDefaults: () => pack.guess?.patterns ?? [],
  };
}

function actionButton(label, title, onClick) {
  const button = el("button", "bmk-to-btn", label);
  button.type = "button";
  button.title = title;
  button.addEventListener("click", onClick);
  return button;
}

function ensureEditor(split) {
  if (editor) return;
  const host = editorHost();
  host.split = split;
  editor = new ArrangementEditor(host);
  saveButton = actionButton("저장", "프리셋에 저장합니다(레이아웃·공유 배열은 새 이름으로)", saveDraft);
  undoButton = actionButton("되돌리기", "되돌리기(Ctrl+Z)", () => stepDraft(undoStack, redoStack));
  redoButton = actionButton("다시 실행", "다시 실행(Ctrl+Y)", () => stepDraft(redoStack, undoStack));
  const saveAs = actionButton("다른 이름으로…", "새 이름으로 저장합니다", saveDraftAs);
  editor.root.querySelector(".bmk-to-bar").prepend(saveButton, saveAs, undoButton, redoButton);
  $("editor").append(editor.root);
}

async function setEditing(on) {
  editing = on;
  chrome.storage.local.set({ [EDITING_KEY]: on });
  $("edit_toggle").setAttribute("aria-pressed", String(on));
  if (on) {
    if (!draftDirty()) openDraft(boxSettings(settings, stored, activeBox()?.kind).preset);
    const { [SPLIT_KEY]: split } = await chrome.storage.local.get(SPLIT_KEY);
    ensureEditor(split);
    if (view === "master") view = "chips";
  }
  renderTabs(activeBox());
  port.postMessage({ type: "fit", editing: on });
  if (on) editor.refresh();
  updateEditButtons();
  render();
}

const tab = await chrome.tabs.getCurrent();
const port = chrome.tabs.connect(tab.id, { name: PORT_NAME, frameId: 0 });

port.onMessage.addListener((message) => {
  if (message.type === "boxes") {
    boxes = message.boxes;
    activeId = message.active;
    naiGenerate = message.generate ?? null;
    applyFieldLook(message.look);
    render();
    flushImport();
  } else if (message.type === "import") {
    pendingImport = message.id;
    flushImport();
  } else if (message.type === "fonts") {
    // 글꼴은 확장 쪽 문서에 따로 올려야 쓸 수 있다. 쓰는 글자 범위의 파일만 브라우저가 읽는다.
    for (const { family, weight, range, src } of message.faces) {
      document.fonts.add(new FontFace(family, `url("${src}")`, { weight, unicodeRange: range }));
    }
  } else if (message.type === "applied") {
    const done = pendingApply;
    // 원본을 켜지 않은 입력창의 글은 저장하지 않는다
    if (message.ok && done && masters[done.id]?.on) {
      masters[done.id] = { ...masters[done.id], lastApplied: done.output };
      storeMasters(done.id);
    }
    pendingApply = null;
    done?.resolve(message);
    render();
  } else if (message.type === "generated") {
    const done = pendingGenerate;
    pendingGenerate = null;
    done?.(message);
  }
});

$("preset").addEventListener("change", () => {
  const id = $("preset").value;
  if (editing) replaceDraft(id, () => changeBoxSetting({ preset: id }));
  else changeBoxSetting({ preset: id });
});
$("edit_toggle").addEventListener("click", () => setEditing(!editing));
$("rules").addEventListener("change", () => changeBoxSetting({ rules: $("rules").value }));
$("guess").addEventListener("change", () => changeBoxSetting({ guess: $("guess").checked }));
$("master_on").addEventListener("change", () => {
  const box = activeBox();
  if (!box || boxGroup(box.kind) === "uc") return;
  const master = masterFor(box) || { on: false, text: "", lastApplied: "" };
  const on = $("master_on").checked;
  masters[box.id] = { ...master, on, group: boxGroup(box.kind), lines: true, text: on && !master.text.trim() ? fieldText(box.text) : master.text };
  confirmedImport = null;
  storeMasters(box.id);
  if (on && !masters[box.id].text.trim()) view = "master";     // 붙여 넣을 자리를 바로 보여 준다
  render();
  if (view === "master") $("master_text").focus();
});
$("master_text").addEventListener("input", () => {
  const box = activeBox();
  const master = masterFor(box);
  if (!master?.on) return;
  masters[box.id] = { ...master, text: $("master_text").value };
  confirmedImport = null;
  storeMasters(box.id, MASTER_SAVE_DELAY);
  render();
});
$("master_text").addEventListener("change", flushMasters);
$("master_import").addEventListener("click", () => importMaster(activeBox()));
addEventListener("pagehide", flushMasters);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) flushMasters();
});
$("show_chips").addEventListener("click", () => showView("chips"));
$("show_text").addEventListener("click", () => showView("text"));
$("show_master").addEventListener("click", () => showView("master"));
$("apply").addEventListener("click", async () => {
  if (!result || result.unchanged || !result.output.trim() || pendingApply || generateRun) return;
  $("apply").disabled = true;
  const applied = await postApply(result.box, result.output);
  if (applied.ok) setNotice(APPLIED);
  else setNotice(applied.reason, true);
});
$("generate").addEventListener("click", applyAndGenerate);
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("text").value);
    setNotice("복사했습니다");
  } catch (e) {
    setNotice(`복사하지 못했습니다: ${e.message}`, true);
  }
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    flushMasters();
    port.postMessage({ type: "close" });
    return;
  }
  // 글을 쓰는 칸의 Ctrl+Z 는 그 칸의 것이다
  if (!editing || !(event.ctrlKey || event.metaKey) || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === "z" && !event.shiftKey) stepDraft(undoStack, redoStack);
  else if (key === "y" || (key === "z" && event.shiftKey)) stepDraft(redoStack, undoStack);
  else return;
  event.preventDefault();
});

chrome.storage.onChanged.addListener(async (changes) => {
  if (changes.nai_masters) {
    // 다른 탭이 고친 원본을 받되, 이 패널에서 아직 저장하지 않은 글은 둔다
    const pending = Object.fromEntries([...dirtyMasters].map((id) => [id, masters[id]]));
    masters = { ...tidyMasters(changes.nai_masters.newValue || {}), ...pending };
  }
  if (!changes.settings && !changes.nai_boxes && !changes.imported_arrangements && !changes.nai_masters) return;
  if (changes.settings) settings = await loadSettings();
  if (changes.nai_boxes) stored = await loadBoxSettings();
  if (changes.imported_arrangements) {
    presets = await listPresets();
    fillPresets();
  }
  render();
});

try {
  [settings, stored, presets, masters] = await Promise.all([loadSettings(), loadBoxSettings(), listPresets(), loadMasters()]);
  Object.assign(overrides, await loadMasterOverrides(masters));
  fillPresets();
  pack = await loadPack();
  chipPalette = palette(pack);
  ({ quick } = chipPalette);
  const saved = await chrome.storage.local.get([DRAFT_KEY, EDITING_KEY]);
  draft = saved[DRAFT_KEY] || null;     // 저장하지 않고 닫은 변경을 잇는다
  render();
  if (saved[EDITING_KEY]) await setEditing(true);
  flushImport();
} catch (e) {
  console.error(e);
  clearResult(e.message, true);
}
