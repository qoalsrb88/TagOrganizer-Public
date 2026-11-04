// NovelAI 전용 콘텐츠 스크립트: 입력창 머리글에 정렬 버튼을 붙이고, 화면을 가리지 않는 미리보기 패널(확장 iframe)을 띄운다.
//
// - NovelAI 의 React 가 머리글을 다시 만들면(배치 전환·폭 전환) 버튼이 떨어지므로 MutationObserver 로 다시 붙인다.
// - 입력창은 NovelAI 의 의미 있는 클래스(prompt-input-box-*)로 구분한다. sc-… 클래스는 빌드마다 바뀐다.
//   머리글을 못 찾으면 화면 구석에 띄운다(NovelAI 화면이 바뀌어도 쓸 수 있게). 오른쪽 클릭 메뉴는 따로 남는다.
// - 보이는 입력창만 다룬다. 모바일 배치의 복제본(크기 0)과 탭 배치의 숨은 탭(visibility)은 포커스를 받지 못한다.
// - 글 읽기·넣기는 page/field.js 가 하고, 정렬은 서비스 워커(버튼)나 패널 iframe(미리보기)이 한다.
// - 패널을 왼쪽 칸 옆에 붙이면 그림 칸에 왼쪽 여백만 준다. 패널은 NovelAI 의 React 트리 밖에 있으므로, 화면 구조가 바뀌어
//   칸을 못 찾아도 떠 있는 창으로 돌아갈 뿐 망가지지 않는다.
(() => {
  if (globalThis.__tagorgNai) return;
  globalThis.__tagorgNai = true;

  const field = globalThis.__tagorgField;
  const BUTTON_ID = "tagorg-nai-button";
  const MENU_ID = "tagorg-nai-menu";
  const PANEL_ID = "tagorg-nai-panel";
  const PANEL_KEY = "nai_panel";
  const DOCK_KEY = "nai_panel_dock";
  const DOCK_WIDTH = 440;
  const DOCK_MIN = 360;
  const DOCK_FOLDED = 96;
  const STAGE_MIN = 320;       // 붙였을 때 그림 칸에 남길 폭. 이만큼 남지 않으면 떠 있는 창으로 돌아간다
  const PORT_NAME = "nai-panel";
  const IMAGE_PATH = "/image";
  const COMPACT_BELOW = 520;   // 머리글이 이보다 좁으면 글자와 작은 단추를 빼서 탭이 다음 줄로 밀리지 않게 한다
  const PANEL_SIZE = { width: 760, height: 680 };
  const PANEL_EDIT_SIZE = { width: 980, height: 820 };   // 배열 편집을 켜면 이 크기까지 늘린다
  const PANEL_MIN = { width: 420, height: 300 };
  const PANEL_MARGIN = 8;
  const PANEL_TOP = 60;
  const TITLE_HEIGHT = 30;
  const Z_INDEX = 2147483000;
  const STALE = "확장을 다시 불러왔습니다. 페이지를 새로고침하세요.";

  const BUTTON_CSS = `
    :host { all: initial; display: inline-flex; vertical-align: middle; margin-right: 8px; }
    :host(.compact) { margin-right: 4px; }
    :host(.floating) { position: fixed; right: 16px; bottom: 16px; margin: 0; z-index: ${Z_INDEX}; }
    .split { display: inline-flex; border: 1px solid #f5f3c2; border-radius: 4px; overflow: hidden; font: 600 13px/1 system-ui, sans-serif; }
    button { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 8px; color: #fff; background: #13152c; cursor: pointer; }
    button:hover { background: #24264a; }
    button:focus-visible { outline: 2px solid #f5f3c2; outline-offset: -2px; }
    button + button { border-left: 1px solid #44466a; padding: 0 6px; }
    button[aria-busy="true"] { opacity: .6; cursor: progress; }
    :host(.compact) .label, :host(.compact) .open, :host(.compact) .to-master { display: none; }
    button[disabled] { opacity: .45; cursor: default; }
    button[disabled]:hover { background: #13152c; }
    :host(.compact) button { padding: 0 4px; }
  `;
  const MENU_CSS = `
    :host { all: initial; position: fixed; z-index: ${Z_INDEX + 1}; }
    .menu { min-width: 220px; max-width: 360px; overflow-y: auto; padding: 4px; color: #e6e6f0; background: #13152c; border: 1px solid #3a3c5a; border-radius: 6px; box-shadow: 0 8px 28px rgba(0, 0, 0, .55); font: 13px/1.4 system-ui, sans-serif; }
    .head { padding: 6px 8px 2px; color: #a0a2bd; font-size: 11px; }
    button { all: unset; box-sizing: border-box; display: flex; gap: 6px; width: 100%; padding: 5px 8px; border-radius: 4px; cursor: pointer; overflow-wrap: anywhere; }
    button:hover, button:focus-visible { background: #24264a; }
    .mark { flex: 0 0 12px; color: #f5f3c2; }
    hr { margin: 4px 0; border: 0; border-top: 1px solid #2a2c44; }
  `;
  const PANEL_CSS = `
    :host { all: initial; position: fixed; z-index: ${Z_INDEX}; }
    .box { display: flex; flex-direction: column; width: 100%; height: 100%; overflow: hidden; background: #101124; border: 1px solid #3a3c5a; border-radius: 8px; box-shadow: 0 12px 40px rgba(0, 0, 0, .55); font: 13px/1 system-ui, sans-serif; color: #e6e6f0; }
    .title { flex: 0 0 ${TITLE_HEIGHT}px; display: flex; align-items: center; gap: 4px; padding: 0 4px 0 10px; background: #181a35; cursor: move; touch-action: none; user-select: none; }
    .title span { flex: 1; font-weight: 600; }
    button { all: unset; box-sizing: border-box; padding: 4px 8px; border-radius: 4px; cursor: pointer; }
    button:hover, button:focus-visible { background: #2a2c50; }
    iframe { flex: 1; width: 100%; min-height: 0; border: 0; background: #101124; }
    .grip { position: absolute; right: 0; bottom: 0; width: 14px; height: 14px; cursor: nwse-resize; touch-action: none; background: linear-gradient(135deg, transparent 50%, #5a5c80 50%); border-bottom-right-radius: 8px; }
    :host(.collapsed) iframe, :host(.collapsed) .grip { display: none; }
    button[hidden] { display: none; }
    :host(.docked) .box { border-width: 0 1px 0 0; border-radius: 0; box-shadow: none; }
    :host(.docked) .title { cursor: default; }
    :host(.docked) .grip { top: 0; bottom: 0; width: 6px; height: auto; cursor: ew-resize; background: transparent; border-radius: 0; }
    :host(.docked) .grip:hover { background: #5a5c80; }
    :host(.docked.collapsed) .title span, :host(.docked.collapsed) .dock { display: none; }
  `;

  const sheet = (css) => {
    const style = new CSSStyleSheet();
    style.replaceSync(css);
    return style;
  };
  const make = (tag, props = {}, ...children) => {
    const el = Object.assign(document.createElement(tag), props);
    el.append(...children);
    return el;
  };
  const alive = () => Boolean(chrome.runtime?.id);

  // ─── 입력창 ──────────────────────────────────────────────────────

  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  };

  function describe(name) {
    if (name === "prompt") return { kind: "prompt", label: "Prompt" };
    if (name === "base-prompt") return { kind: "prompt", label: "Base Prompt" };
    if (name === "undesired-content") return { kind: "uc", label: "UC" };
    const character = /^character-prompts-(\d+)(-undesired-content)?$/.exec(name);
    if (character) return character[2]
      ? { kind: "character-uc", label: `캐릭터 ${character[1]} UC` }
      : { kind: "character", label: `캐릭터 ${character[1]}` };
    return { kind: "prompt", label: "입력창" };
  }

  function boxes() {
    return [...document.querySelectorAll(".ProseMirror")].filter(shown).map((el, index) => {
      const owner = el.closest('[class*="prompt-input-box-"]');
      const name = /prompt-input-box-([\w-]+)/.exec(owner?.className || "")?.[1] || `box-${index}`;
      return { el, id: name, ...describe(name) };
    });
  }

  // 대상: 마지막으로 누른 입력창. 안 보이면 맨 앞 입력창.
  let lastEl = null;
  const targetBox = (list = boxes()) => list.find((box) => box.el === lastEl) || list[0] || null;

  document.addEventListener("focusin", (event) => {
    const el = event.target?.closest?.(".ProseMirror");
    if (el && el !== lastEl) {
      lastEl = el;
      schedule();
    }
  }, true);

  // ─── 정렬 ────────────────────────────────────────────────────────

  async function sortBox(box, { presetId, wholeOnly = false } = {}) {
    const captured = field.captureEditable(box.el);
    const { text, partial } = wholeOnly ? field.selectAll() : captured;
    const reply = await chrome.runtime.sendMessage({ type: "nai-sort", text, partial, whole: box.el.innerText, kind: box.kind,
      presetId, hostname: location.hostname });
    if (reply.problem) return { ok: false, message: `${box.label}: ${reply.problem}` };
    if (reply.widened) field.selectAll();
    if (reply.unchanged) return { ok: true, message: `${box.label}: 이미 정렬돼 있습니다` };
    const applied = await field.apply(reply.text, reply.output);
    return applied.ok
      ? { ok: true, message: `${box.label} · ${reply.label}: ${reply.summary}` }
      : { ok: false, message: `${box.label}: ${applied.reason}` };
  }

  // 메뉴·버튼에서 부르는 작업을 하나씩 돌리고 결과를 알린다.
  let busy = false;
  async function runTask(task) {
    if (busy) return;
    if (!alive()) {
      field.toast(STALE, "warn");
      return;
    }
    busy = true;
    ui.main?.setAttribute("aria-busy", "true");
    try {
      const results = await task();
      field.toast(results.map((r) => r.message).join("\n"), results.every((r) => r.ok) ? "ok" : "warn");
    } catch (e) {
      console.error(e);
      field.toast(alive() ? `정렬하지 못했습니다: ${e.message}` : STALE, "warn");
    } finally {
      busy = false;
      ui.main?.removeAttribute("aria-busy");
    }
  }

  function sortTarget(presetId) {
    return runTask(async () => {
      const box = targetBox();
      return box ? [await sortBox(box, { presetId })] : [{ ok: false, message: "입력창을 찾지 못했습니다" }];
    });
  }

  function sortAll() {
    return runTask(async () => {
      const list = boxes().filter((box) => box.el.innerText.trim());
      if (!list.length) return [{ ok: false, message: "정렬할 글이 없습니다" }];
      const results = [];
      for (const box of list) results.push(await sortBox(box, { wholeOnly: true }));
      return results;
    });
  }

  // ─── 버튼 ────────────────────────────────────────────────────────

  const ui = { host: null, main: null, toMaster: null };

  function buildButton() {
    const host = make("span", { id: BUTTON_ID });
    const root = host.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet(BUTTON_CSS)];
    const main = make("button", { className: "main", type: "button" }, make("span", { textContent: "⇅" }),
      make("span", { className: "label", textContent: "TagOrganize" }));
    const more = make("button", { className: "more", type: "button", textContent: "▾", title: "프리셋·대상·패널" });
    more.setAttribute("aria-label", "프리셋·대상·패널");
    more.setAttribute("aria-haspopup", "menu");
    const open = make("button", { className: "open", type: "button", textContent: "▤", title: "미리보기 패널" });
    open.setAttribute("aria-label", "미리보기 패널");
    const toMaster = make("button", { className: "to-master", type: "button", textContent: "→ 원본" });
    const split = make("div", { className: "split" }, main, more, open, toMaster);
    root.append(split);
    // 입력창의 포커스와 선택을 빼앗지 않는다(고른 부분만 정렬하려면 선택이 남아 있어야 한다)
    split.addEventListener("mousedown", (event) => event.preventDefault());
    main.addEventListener("click", () => {
      closeMenu();
      sortTarget();
    });
    more.addEventListener("click", () => (document.getElementById(MENU_ID) ? closeMenu() : openMenu(more)));
    open.addEventListener("click", () => {
      closeMenu();
      openPanel();
    });
    toMaster.addEventListener("click", sendToMaster);
    Object.assign(ui, { host, main, toMaster });
  }

  const headerOf = (group) => group?.parentElement || null;

  const headerSize = new ResizeObserver(() => updateCompact());
  function updateCompact() {
    const header = ui.host?.isConnected && !ui.host.classList.contains("floating") ? headerOf(ui.host.parentElement) : null;
    ui.host?.classList.toggle("compact", Boolean(header) && header.clientWidth < COMPACT_BELOW);
  }

  function findAnchor() {
    const main = [...document.querySelectorAll(".image-gen-prompt-main")].find(shown);
    return main?.firstElementChild?.lastElementChild || null;
  }

  let enabled = true;
  function mount(list) {
    if (!enabled) {
      ui.host?.remove();
      closeMenu();
      closePanel();
      return;
    }
    if (!ui.host) buildButton();
    const group = list.length ? findAnchor() : null;
    if (group) {
      if (ui.host.parentElement !== group) {
        ui.host.classList.remove("floating");
        group.prepend(ui.host);
        headerSize.disconnect();
        headerSize.observe(headerOf(group));
        updateCompact();
      }
    } else if (list.length) {
      if (ui.host.parentElement !== document.documentElement) {
        ui.host.classList.add("floating");
        document.documentElement.append(ui.host);
        updateCompact();
      }
    } else {
      ui.host.remove();
    }
    const box = targetBox(list);
    ui.main.title = box ? `${box.label} 정렬` : "프롬프트 정렬";
    const uc = box?.kind === "uc" || box?.kind === "character-uc";
    ui.toMaster.disabled = !box || uc;
    ui.toMaster.title = uc ? "UC 에는 원본이 없습니다" : `${box?.label ?? "입력창"} 글을 TagOrganize 원본으로 가져옵니다`;
    ui.toMaster.setAttribute("aria-label", ui.toMaster.title);
  }

  // 머리글의 → 원본: 패널을 열고 지금 입력창의 글을 원본으로 넣는다. 입력창 글은 그대로 둔다.
  let pendingImport = null;
  async function sendToMaster() {
    closeMenu();
    const box = targetBox();
    if (!box || box.kind === "uc" || box.kind === "character-uc") return;
    if (!alive()) {
      field.toast(STALE, "warn");
      return;
    }
    await openPanel();
    if (panelPort) panelPort.postMessage({ type: "import", id: box.id });
    else pendingImport = box.id;      // 패널이 연결되면 보낸다
  }

  // ─── 메뉴 ────────────────────────────────────────────────────────

  function closeMenu() {
    document.getElementById(MENU_ID)?.remove();
  }

  function menuItem(label, onPick, checked) {
    const item = make("button", { type: "button" }, make("span", { className: "mark", textContent: checked ? "✓" : "" }),
      make("span", { textContent: label }));
    item.setAttribute("role", "menuitem");
    item.addEventListener("click", onPick);
    return item;
  }

  async function openMenu(anchor) {
    if (!alive()) {
      field.toast(STALE, "warn");
      return;
    }
    const list = boxes();
    const target = targetBox(list);
    const kind = target?.kind;
    const { presets, chosen, problem } = await chrome.runtime.sendMessage({ type: "nai-menu", kind });
    if (problem) {
      field.toast(problem, "warn");
      return;
    }
    closeMenu();
    const host = make("div", { id: MENU_ID });
    const root = host.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet(MENU_CSS)];
    const menu = make("div", { className: "menu" });
    menu.setAttribute("role", "menu");
    menu.addEventListener("mousedown", (event) => event.preventDefault());
    if (list.length > 1) {
      menu.append(make("div", { className: "head", textContent: "대상" }));
      for (const box of list) {
        menu.append(menuItem(box.label, () => {
          lastEl = box.el;
          schedule();
          openMenu(anchor);
        }, box === target));
      }
      menu.append(make("hr"));
    }
    menu.append(make("div", { className: "head", textContent: target ? `프리셋 (${target.label})` : "프리셋" }));
    for (const preset of presets) {
      menu.append(menuItem(preset.label, async () => {
        closeMenu();
        if (alive()) await chrome.runtime.sendMessage({ type: "nai-choose", kind, presetId: preset.id });
        sortTarget(preset.id);
      }, preset.id === chosen));
    }
    menu.append(make("hr"));
    menu.append(menuItem("보이는 입력창 모두 정렬", () => {
      closeMenu();
      sortAll();
    }));
    menu.append(menuItem("미리보기 패널", () => {
      closeMenu();
      openPanel();
    }));
    if (target && target.kind !== "uc" && target.kind !== "character-uc") menu.append(menuItem("입력창 글을 원본으로", sendToMaster));
    root.append(menu);
    document.documentElement.append(host);
    const rect = anchor.getBoundingClientRect();
    menu.style.maxHeight = `${Math.max(160, innerHeight - rect.bottom - 16)}px`;
    host.style.top = `${rect.bottom + 4}px`;
    host.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - menu.offsetWidth - 8))}px`;
  }

  // 메뉴 밖을 누르거나 Esc·화면 스크롤이면 닫는다.
  const outsideMenu = (event) => {
    const menu = document.getElementById(MENU_ID);
    return menu && !event.composedPath().includes(menu) && !event.composedPath().includes(ui.host);
  };
  document.addEventListener("mousedown", (event) => { if (outsideMenu(event)) closeMenu(); }, true);
  document.addEventListener("scroll", (event) => { if (outsideMenu(event)) closeMenu(); }, true);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); }, true);

  // ─── 패널 ────────────────────────────────────────────────────────

  let panelPort = null;
  let panelRect = null;
  let rectBeforeEdit = null;     // 배열 편집으로 늘리기 전 크기. 손으로 바꾸면 그 크기를 그대로 쓴다
  let dock = { on: false, width: DOCK_WIDTH };
  let pushed = null;             // 왼쪽 여백을 준 그림 칸
  let watchedSide = null;
  const sideSize = new ResizeObserver(() => {
    const panel = document.getElementById(PANEL_ID);
    if (panel) placePanel(panel);
  });

  // 배열 편집을 켜면 늘리고, 끄면 돌려놓는다(저장하지 않는다).
  function fitPanel(editing) {
    const panel = document.getElementById(PANEL_ID);
    if (!panel) return;
    if (editing && !rectBeforeEdit) {
      rectBeforeEdit = panelRect;
      panelRect = { ...panelRect, width: Math.max(panelRect.width, PANEL_EDIT_SIZE.width),
        height: Math.max(panelRect.height, PANEL_EDIT_SIZE.height) };
    } else if (!editing && rectBeforeEdit) {
      panelRect = rectBeforeEdit;
      rectBeforeEdit = null;
    }
    placePanel(panel);
  }

  function defaultPanelRect() {
    const side = [...document.querySelectorAll(".image-gen-panel-sidebar")].find(shown);
    const left = side ? side.getBoundingClientRect().right + PANEL_MARGIN : PANEL_MARGIN;
    return { left, top: PANEL_TOP, ...PANEL_SIZE };
  }

  function clampRect(rect) {
    const width = Math.max(PANEL_MIN.width, Math.min(rect.width, innerWidth - PANEL_MARGIN * 2));
    const height = Math.max(PANEL_MIN.height, Math.min(rect.height, innerHeight - PANEL_MARGIN * 2));
    return { width, height,
      left: Math.max(PANEL_MARGIN, Math.min(rect.left, innerWidth - width - PANEL_MARGIN)),
      top: Math.max(PANEL_MARGIN, Math.min(rect.top, innerHeight - height - PANEL_MARGIN)) };
  }

  // 왼쪽 칸과 그림 칸. 그림 칸은 왼쪽 칸과 같은 줄에 놓인 자식까지 올라가 찾는다(여백을 줄 곳).
  function dockTarget() {
    const side = [...document.querySelectorAll(".image-gen-panel-sidebar")].find(shown);
    let results = [...document.querySelectorAll(".image-gen-results-stage")].find(shown);
    if (!side || !results) return null;
    while (results.parentElement && !results.parentElement.contains(side)) results = results.parentElement;
    return results.parentElement && !results.contains(side) ? { side, results } : null;
  }

  function dockRect(wanted = dock.on) {
    if (!wanted) return null;
    const target = dockTarget();
    if (!target) return null;
    const room = target.results.getBoundingClientRect().width + (parseFloat(target.results.style.marginLeft) || 0);
    const width = Math.min(Math.max(dock.width, DOCK_MIN), room - STAGE_MIN);
    if (width < DOCK_MIN) return null;
    const side = target.side.getBoundingClientRect();
    return { ...target, left: side.right, top: side.top, height: side.height, width };
  }

  function pushResults(results, width) {
    let changed = false;
    if (pushed && pushed !== results) {
      pushed.style.marginLeft = "";
      changed = true;
    }
    pushed = width ? results : null;
    const value = width ? `${width}px` : "";
    if (results && results.style.marginLeft !== value) {
      results.style.marginLeft = value;
      changed = true;
    }
    if (changed) dispatchEvent(new Event("resize"));     // NovelAI 가 그림 크기를 새 폭으로 다시 재게 한다
  }

  function watchSide(side) {
    if (side === watchedSide) return;
    sideSize.disconnect();
    watchedSide = side;
    if (side) sideSize.observe(side);
  }

  function placePanel(host) {
    const collapsed = host.classList.contains("collapsed");
    const docked = dockRect();
    host.classList.toggle("docked", Boolean(docked));
    const button = host.shadowRoot.querySelector(".dock");
    button.textContent = docked ? "띄우기" : "붙이기";
    button.title = docked ? "떠 있는 창으로 돌아갑니다" : "왼쪽 칸 옆에 붙여 그림을 가리지 않게 합니다";
    button.hidden = !docked && !dockRect(true);
    watchSide(docked?.side ?? null);
    if (docked) {
      host.style.left = `${docked.left}px`;
      host.style.top = `${docked.top}px`;
      host.style.width = `${collapsed ? DOCK_FOLDED : docked.width}px`;
      host.style.height = collapsed ? `${TITLE_HEIGHT + 2}px` : `${docked.height}px`;
      pushResults(docked.results, collapsed ? 0 : docked.width);
      return;
    }
    pushResults(null, 0);
    const rect = clampRect(panelRect);
    host.style.left = `${rect.left}px`;
    host.style.top = `${rect.top}px`;
    host.style.width = `${rect.width}px`;
    host.style.height = host.classList.contains("collapsed") ? `${TITLE_HEIGHT + 2}px` : `${rect.height}px`;
  }

  // 끌기: iframe 이 포인터를 가로채지 않게 끄는 동안만 막는다.
  function dragWith(handle, frame, onMove) {
    handle.addEventListener("pointerdown", (start) => {
      const host = handle.getRootNode().host;
      if (start.target.closest?.("button") || (handle.classList.contains("title") && host.classList.contains("docked"))) return;
      start.preventDefault();
      handle.setPointerCapture(start.pointerId);
      frame.style.pointerEvents = "none";
      const from = { ...panelRect, docked: host.getBoundingClientRect().width };
      const move = (event) => onMove(from, event.clientX - start.clientX, event.clientY - start.clientY);
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        frame.style.pointerEvents = "";
        if (host.classList.contains("docked")) {
          dock = { ...dock, width: Math.round(host.getBoundingClientRect().width) };
          if (alive()) chrome.storage.local.set({ [DOCK_KEY]: dock });
          return;
        }
        panelRect = clampRect(panelRect);
        rectBeforeEdit = null;
        if (alive()) chrome.storage.local.set({ [PANEL_KEY]: panelRect });
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
  }

  async function openPanel() {
    if (!alive()) {
      field.toast(STALE, "warn");
      return;
    }
    const existing = document.getElementById(PANEL_ID);
    if (existing) {
      existing.classList.remove("collapsed");
      existing.shadowRoot.querySelector(".fold").textContent = "접기";
      placePanel(existing);
      return;
    }
    const saved = await chrome.storage.local.get([PANEL_KEY, DOCK_KEY]);
    panelRect = saved[PANEL_KEY] || defaultPanelRect();
    dock = { on: false, width: DOCK_WIDTH, ...saved[DOCK_KEY] };
    rectBeforeEdit = null;
    if (document.getElementById(PANEL_ID)) return;
    const host = make("div", { id: PANEL_ID });
    const root = host.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet(PANEL_CSS)];
    const dockButton = make("button", { className: "dock", type: "button", textContent: "붙이기" });
    const fold = make("button", { className: "fold", type: "button", textContent: "접기" });
    const close = make("button", { className: "close", type: "button", textContent: "닫기" });
    const title = make("div", { className: "title" }, make("span", { textContent: "TagOrganize" }), dockButton, fold, close);
    const frame = make("iframe", { title: "TagOrganize 미리보기", src: chrome.runtime.getURL("nai/panel.html") });
    frame.allow = "clipboard-write";
    const grip = make("div", { className: "grip" });
    root.append(make("div", { className: "box" }, title, frame, grip));
    dockButton.addEventListener("click", () => {
      dock = { ...dock, on: !host.classList.contains("docked") };
      host.classList.remove("collapsed");
      fold.textContent = "접기";
      placePanel(host);
      if (alive()) chrome.storage.local.set({ [DOCK_KEY]: dock });
    });
    fold.addEventListener("click", () => {
      fold.textContent = host.classList.toggle("collapsed") ? "펼치기" : "접기";
      placePanel(host);
    });
    close.addEventListener("click", closePanel);
    dragWith(title, frame, (from, dx, dy) => {
      panelRect = { ...from, left: from.left + dx, top: from.top + dy };
      placePanel(host);
    });
    dragWith(grip, frame, (from, dx, dy) => {
      if (host.classList.contains("docked")) dock = { ...dock, width: from.docked + dx };
      else panelRect = { ...from, width: from.width + dx, height: from.height + dy };
      placePanel(host);
    });
    placePanel(host);
    document.documentElement.append(host);
  }

  function closePanel() {
    document.getElementById(PANEL_ID)?.remove();
    pushResults(null, 0);
    watchSide(null);
  }

  addEventListener("resize", () => {
    const panel = document.getElementById(PANEL_ID);
    if (panel) placePanel(panel);
    closeMenu();
  });

  // ─── 패널과 주고받기 ─────────────────────────────────────────────

  let lastPushed = "";
  let watched = [];
  const textWatcher = new MutationObserver(() => schedule());

  // NovelAI 의 Generate 버튼. 비용은 글자만 바뀌므로 화면 전체를 보는 감시로는 알 수 없어 따로 본다.
  const generateButton = () => [...document.querySelectorAll(".image-gen-generate-button")].find(shown) || null;
  const generateReady = (button) => !button.disabled && button.getAttribute("aria-disabled") !== "true";
  let watchedGenerate = null;
  const generateWatcher = new MutationObserver(() => schedule());

  function watchGenerate(button) {
    if (button === watchedGenerate) return;
    watchedGenerate = button;
    generateWatcher.disconnect();
    if (button) {
      generateWatcher.observe(button, { characterData: true, childList: true, subtree: true, attributes: true,
        attributeFilter: ["disabled", "aria-disabled"] });
    }
  }

  function generateState(button) {
    if (!button) return null;
    const label = button.textContent.trim();
    return { ready: generateReady(button), label, cost: /(\d[\d,]*)\s*Anlas/i.exec(label)?.[1] || "" };
  }

  // 패널이 계획을 세운 뒤 입력창이나 NovelAI 버튼이 바뀌었으면 누르지 않는다. 글은 빈칸을 빼고 비교한다(문단 편집기의 줄바꿈).
  function clickGenerate(expected) {
    const button = generateButton();
    if (!button) return { ok: false, reason: "NovelAI 의 생성 버튼을 찾지 못했습니다" };
    if (!generateReady(button)) return { ok: false, reason: "NovelAI 가 지금은 생성할 수 없는 상태입니다" };
    const squeeze = (text) => text.replace(/\s+/g, "");
    const list = boxes();
    for (const { id, text } of expected) {
      const box = list.find((b) => b.id === id);
      if (!box) return { ok: false, reason: "입력창이 사라져서 생성하지 않았습니다" };
      if (squeeze(box.el.innerText) !== squeeze(text)) return { ok: false, reason: `그 사이 ${box.label} 글이 바뀌어서 생성하지 않았습니다` };
    }
    const { label } = generateState(button);
    button.click();
    return { ok: true, label };
  }

  function watchTexts(list) {
    if (list.length === watched.length && list.every((box, i) => box.el === watched[i])) return;
    watched = list.map((box) => box.el);
    textWatcher.disconnect();
    for (const el of watched) textWatcher.observe(el, { characterData: true, childList: true, subtree: true });
  }

  // 입력창의 모양. 글자 줄은 문단(p)에, 여백과 바탕은 입력창을 감싼 칸에 있다.
  function fieldLook(box) {
    if (!box) return null;
    const text = getComputedStyle(box.el.querySelector("p") || box.el);
    const wrap = box.el.parentElement ? getComputedStyle(box.el.parentElement) : null;
    return { fontFamily: text.fontFamily, fontSize: text.fontSize, fontWeight: text.fontWeight, lineHeight: text.lineHeight,
      letterSpacing: text.letterSpacing, color: text.color, fontFeatureSettings: text.fontFeatureSettings, wordBreak: text.wordBreak,
      overflowWrap: text.overflowWrap, padding: wrap?.padding, background: wrap?.backgroundColor };
  }

  // 입력창 글꼴의 파일들. 패널은 확장 쪽 문서라 페이지의 글꼴을 그대로 쓸 수 없어 주소를 넘긴다(같은 사이트의 글꼴만).
  function fieldFaces(box) {
    const text = getComputedStyle(box.el);
    const family = text.fontFamily.split(",")[0].replace(/["']/g, "").trim();
    const faces = [];
    for (const sheet of document.styleSheets) {
      let rules;
      try {
        rules = sheet.cssRules;
      } catch {
        continue;       // 다른 사이트의 스타일시트는 읽을 수 없다
      }
      for (const rule of rules) {
        if (!(rule instanceof CSSFontFaceRule) || rule.style.getPropertyValue("font-weight") !== text.fontWeight) continue;
        if (rule.style.getPropertyValue("font-family").replace(/["']/g, "").trim() !== family) continue;
        const src = /url\(["']?([^"')]+)/.exec(rule.style.getPropertyValue("src"))?.[1];
        if (!src) continue;
        const url = new URL(src, sheet.href || location.href);
        if (url.origin === location.origin) {
          faces.push({ family, weight: text.fontWeight, range: rule.style.getPropertyValue("unicode-range") || "U+0-10FFFF", src: url.href });
        }
      }
    }
    return faces;
  }

  let fontsSent = false;
  function pushBoxes(list) {
    if (!panelPort) return;
    const button = generateButton();
    watchGenerate(button);
    const target = targetBox(list);
    if (!fontsSent && target) {
      const faces = fieldFaces(target);
      if (faces.length) {
        panelPort.postMessage({ type: "fonts", faces });
        fontsSent = true;
      }
    }
    const payload = JSON.stringify({ type: "boxes", active: target?.id || null, generate: generateState(button), look: fieldLook(target),
      boxes: list.map(({ id, kind, label, el }) => ({ id, kind, label, text: el.innerText })) });
    if (payload === lastPushed) return;
    lastPushed = payload;
    panelPort.postMessage(JSON.parse(payload));
  }

  async function onPanelMessage(message, port) {
    if (message.type === "select") {
      lastEl = boxes().find((box) => box.id === message.id)?.el || lastEl;
      schedule();
    } else if (message.type === "close") {
      closePanel();
    } else if (message.type === "fit") {
      fitPanel(message.editing);
    } else if (message.type === "apply") {
      const box = boxes().find((b) => b.id === message.id);
      if (!box) {
        port.postMessage({ type: "applied", ok: false, reason: "입력창이 사라졌습니다" });
        return;
      }
      lastEl = box.el;
      field.captureEditable(box.el);
      field.selectAll();
      const result = await field.apply(message.expected, message.text);
      port.postMessage({ type: "applied", ...result });
    } else if (message.type === "generate") {
      port.postMessage({ type: "generated", ...clickGenerate(message.expected) });
    }
  }

  chrome.runtime.onConnect.addListener((port) => {
    if (port.name !== PORT_NAME) return;
    panelPort = port;
    lastPushed = "";
    fontsSent = false;
    port.onMessage.addListener(onPanelMessage);
    port.onDisconnect.addListener(() => {
      if (panelPort !== port) return;
      panelPort = null;
      watched = [];
      textWatcher.disconnect();
      watchGenerate(null);
      if (!alive()) closePanel();
    });
    schedule();
    if (pendingImport) {
      port.postMessage({ type: "import", id: pendingImport });
      pendingImport = null;
    }
  });

  // ─── 화면 변화 따라가기 ──────────────────────────────────────────

  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      // 다른 화면(스토리 편집기 등)에서는 아무것도 하지 않는다. 화면이 페이지 이동 없이 바뀌므로 매번 본다
      const onImagePage = location.pathname.startsWith(IMAGE_PATH);
      if (!onImagePage) closePanel();
      const list = onImagePage ? boxes() : [];
      mount(list);
      const panel = document.getElementById(PANEL_ID);
      if (panel && dock.on) placePanel(panel);    // NovelAI 가 칸을 다시 만들면 여백도 다시 준다
      if (panelPort) {
        watchTexts(list);
        pushBoxes(list);
      }
    });
  }

  // 탭 전환은 클래스만 바꾸므로 class 변화도 본다
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });

  chrome.storage.local.get("settings").then((stored) => {
    enabled = stored.settings?.nai_button !== false;
    schedule();
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (!changes.settings) return;
    enabled = changes.settings.newValue?.nai_button !== false;
    schedule();
  });
})();
