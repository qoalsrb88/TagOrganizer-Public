// NovelAI 전용 UI 를 실제 크롬으로 시험한다(수동 실행 — 인터넷의 novelai.net 을 연다. 로그인·생성 요청은 하지 않는다).
//
//   python tagorg.py extension --no-build
//   node tests/extension/e2e_nai.mjs
//
// 임시 프로필의 헤드리스 크롬에 확장 사본을 그대로(권한을 더하지 않고) 올린다. 입력과 클릭은 실제 입력 이벤트로 보낸다.
// 화면을 보려면 E2E_SHOT=폴더 를 주면 스크린샷을 남긴다.
import { cpSync, existsSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launchChrome, sleep, waitFor } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const NAI_URL = "https://novelai.net/image";
const WORD_PROMPT = "smile, 1girl, shoes, upper body, long hair, white shirt, masterpiece";

const browser = launchChrome();
const { send, evaluate, attach, listeners } = browser;
const copy = join(browser.tmp, "ext");
cpSync(join(ROOT, "extension"), copy, { recursive: true });
if (!existsSync(join(copy, "data", "tagpack.json.gz"))) throw new Error("먼저 python tagorg.py extension 을 실행하세요");

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok || detail === undefined ? "" : `  ${JSON.stringify(detail)}`}`);
}

const squeeze = (text) => text.replace(/\s+/g, "");

try {
  const { id: extId } = await send("Extensions.loadUnpacked", { path: copy });
  const swTarget = await waitFor(async () => {
    const { targetInfos } = await send("Target.getTargets");
    return targetInfos.find((t) => t.type === "service_worker" && t.url.includes(extId));
  }, "서비스 워커");
  const sw = await attach(swTarget.targetId);
  const stored = (key) => evaluate(sw, `chrome.storage.local.get(${JSON.stringify(key)}).then((x) => x[${JSON.stringify(key)}] ?? null)`);

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const page = await attach(targetId);
  await send("Page.enable", {}, page);
  const pageErrors = [];
  listeners.push((m) => {
    if (m.method === "Runtime.exceptionThrown" && String(m.params.exceptionDetails.url || m.params.exceptionDetails.exception?.description || "").includes("chrome-extension")) {
      pageErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  });
  await send("Page.navigate", { url: NAI_URL }, page);
  await send("Page.bringToFront", {}, page);
  const ev = (expression) => evaluate(page, expression);
  await waitFor(() => ev("!!document.querySelector('.ProseMirror')").catch(() => false), "NovelAI 입력창");
  await sleep(1500);
  await ev("[...document.querySelectorAll('.modal-close')].filter((el) => el.getBoundingClientRect().width > 0)[0]?.click()");
  await sleep(1200);

  const VISIBLE = "[...document.querySelectorAll('.ProseMirror')].filter((el) => el.getBoundingClientRect().width > 0 && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }))";
  const click = async (x, y) => {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y }, page);
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 }, page);
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 }, page);
    await sleep(250);
  };
  const clickExpr = async (expression) => {
    const at = await ev(`(() => { const el = ${expression}; if (!el) return null; el.scrollIntoView({ block: 'nearest' }); const r = el.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
    if (!at) throw new Error(`찾지 못함: ${expression}`);
    await click(at[0], at[1]);
  };
  const key = async (name, code, vk, { modifiers = 0, command } = {}) => {
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", modifiers, key: name, code, windowsVirtualKeyCode: vk, ...(command ? { commands: [command] } : {}) }, page);
    await send("Input.dispatchKeyEvent", { type: "keyUp", modifiers, key: name, code, windowsVirtualKeyCode: vk }, page);
  };
  const type = async (index, text) => {
    // NovelAI 의 태그 자동완성 목록을 닫는다(덮인 채 누르면 목록의 태그가 들어간다)
    if (await ev("document.activeElement?.classList.contains('ProseMirror')")) {
      await key("Escape", "Escape", 27);
      await sleep(100);
    }
    const at = await ev(`(() => { const el = ${VISIBLE}[${index}]; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return [r.x + r.width / 2, r.y + Math.min(r.height / 2, 20)]; })()`);
    await sleep(150);
    await click(at[0], at[1]);
    await key("a", "KeyA", 65, { modifiers: 2, command: "selectAll" });
    await sleep(100);
    if (text) await send("Input.insertText", { text }, page);
    else await key("Delete", "Delete", 46, { command: "deleteForward" });
    await sleep(350);
  };
  const boxText = (index) => ev(`${VISIBLE}[${index}].innerText`);
  const undo = async () => {
    await key("z", "KeyZ", 90, { modifiers: 2, command: "undo" });
    await sleep(350);
  };
  const part = (name) => `document.getElementById('tagorg-nai-button')?.shadowRoot.querySelector('.${name}')`;
  const toast = () => ev("document.getElementById('tagorg-toast-host')?.shadowRoot.textContent || ''");
  const clearToast = () => ev("document.getElementById('tagorg-toast-host')?.remove()");
  const toastHas = async (text) => {
    await waitFor(async () => (await toast()).includes(text), `알림 "${text}"`, 8000).catch(() => {});
    return toast();
  };
  const sortByButton = async (expected) => {
    await clearToast();
    await clickExpr(part("main"));
    return toastHas(expected);
  };
  const narrow = async () => {
    await send("Emulation.setDeviceMetricsOverride", { width: 800, height: 900, deviceScaleFactor: 1, mobile: false }, page);
    await sleep(1000);
  };
  const wide = async () => {
    await send("Emulation.clearDeviceMetricsOverride", {}, page);
    await sleep(1000);
  };
  const screenshot = async (name) => {
    if (!process.env.E2E_SHOT) return;
    const { data } = await send("Page.captureScreenshot", { format: "png" }, page);
    writeFileSync(join(process.env.E2E_SHOT, name), Buffer.from(data, "base64"));
  };

  // ─── 버튼 ───────────────────────────────────────────────────────
  await waitFor(() => ev("!!document.getElementById('tagorg-nai-button')"), "버튼");
  check("머리글에 버튼이 붙음", await ev("!!document.getElementById('tagorg-nai-button').closest('.image-gen-prompt-main')"));
  check("기본 폭에서는 글자를 뺌", await ev("document.getElementById('tagorg-nai-button').classList.contains('compact')"));

  await type(0, WORD_PROMPT);
  let message = await sortByButton("개 정렬");
  let text = await boxText(0);
  check("버튼을 누르면 정렬", text.startsWith("1girl"), text);
  check("구도 규칙으로 빠진 태그를 알림", !text.includes("shoes") && message.includes("shoes"), { text, message });
  check("알림에 입력창 이름", message.startsWith("Prompt"), message);
  check("입력창에 포커스가 남음", await ev(`document.activeElement === ${VISIBLE}[0]`));
  const sortedFirst = text;

  message = await sortByButton("이미 정렬돼");
  check("이미 정렬된 글은 다시 넣지 않음", message.includes("이미 정렬돼"), message);

  await undo();
  check("실제 Ctrl+Z 로 되돌림", squeeze(await boxText(0)) === squeeze(WORD_PROMPT), await boxText(0));

  await clearToast();
  await clickExpr(part("main"));
  await toastHas("개 정렬");
  await narrow();
  const remade = await boxText(0);
  check("입력창을 새로 만들어도 정렬된 글이 나옴(앱이 그 글을 앎)", squeeze(remade) === squeeze(sortedFirst), remade);
  check("좁은 배치에도 버튼이 붙음", await ev("!!document.getElementById('tagorg-nai-button')?.isConnected"));
  check("머리글이 넓으면 글자를 보임", await ev("!document.getElementById('tagorg-nai-button').classList.contains('compact')"));
  await wide();

  // ─── 일부 선택 ──────────────────────────────────────────────────
  const select = (start, end) => ev(`(async () => {
    const el = ${VISIBLE}[0];
    el.focus();
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode();
    const range = document.createRange();
    range.setStart(node, ${start});
    range.setEnd(node, ${end});
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    await new Promise((r) => setTimeout(r, 150));
  })()`);
  await type(0, "keep, smile, 1girl, tail");
  await select(6, 18);
  await sortByButton("개 정렬");
  text = await boxText(0);
  check("고른 부분만 정렬하고 앞뒤를 지킴", squeeze(text) === squeeze("keep, 1girl,\nsmile, tail") && text.startsWith("keep, 1girl,"), text);

  await type(0, "1.2::a, smile, 1girl ::");
  await select(0, 13);
  message = await sortByButton("잘렸습니다");
  check("잘린 강조는 바꾸지 않고 알림", message.includes("잘렸습니다") && (await boxText(0)).trim() === "1.2::a, smile, 1girl ::", message);

  await type(0, "smile, 1girl");
  await select(0, 5);
  await sortByButton("개 정렬");
  check("조각 하나만 고르면 전체 정렬", (await boxText(0)).startsWith("1girl"), await boxText(0));

  // ─── 대상·UC ────────────────────────────────────────────────────
  await type(0, WORD_PROMPT);
  await type(1, "shoes, upper body, lowres");
  message = await sortByButton("개 정렬");
  text = await boxText(1);
  check("마지막으로 누른 입력창이 대상", message.startsWith("UC") && squeeze(await boxText(0)) === squeeze(WORD_PROMPT), message);
  check("UC 는 구도 규칙으로 태그를 빼지 않음", text.includes("shoes") && text.includes("upper body") && text.includes("lowres"), text);

  // ─── 메뉴 ───────────────────────────────────────────────────────
  const MENU = "document.getElementById('tagorg-nai-menu')?.shadowRoot";
  const menuItems = () => ev(`[...(${MENU}?.querySelectorAll('button') || [])].map((b) => b.innerText.replace(/\\s+/g, ' ').trim())`);
  const menuItem = (label) => `[...${MENU}.querySelectorAll('button')].find((b) => b.innerText.includes(${JSON.stringify(label)}))`;
  const openMenu = async () => {
    await clickExpr(part("more"));
    await waitFor(() => ev(`!!${MENU}`), "메뉴");
  };
  await openMenu();
  let items = await menuItems();
  check("메뉴에 대상·프리셋·모두 정렬·패널", items.some((x) => x.includes("Prompt")) && items.some((x) => x.includes("UC")) && items.some((x) => x.includes("모두 정렬")) && items.some((x) => x.includes("미리보기 패널")), items);
  check("입력창 포커스 유지", await ev(`document.activeElement === ${VISIBLE}[1]`));
  await key("Escape", "Escape", 27);
  await sleep(150);
  check("Esc 로 메뉴 닫힘", !(await ev(`!!${MENU}`)));
  await openMenu();
  await clickExpr(`${menuItem("Prompt")}`);
  await waitFor(() => ev(`!!${MENU}`), "메뉴 다시 그림");
  await sleep(300);
  const checkedTarget = await ev(`[...${MENU}.querySelectorAll('button')].filter((b) => b.querySelector('.mark').textContent).map((b) => b.innerText.trim())`);
  check("메뉴에서 대상을 바꿈", checkedTarget.some((x) => x.includes("Prompt")), checkedTarget);
  const layouts = await evaluate(sw, "fetch(chrome.runtime.getURL('data/catalog.json')).then((r) => r.json()).then((c) => [...Object.values(c.layouts), ...c.arrangements.map((a) => a.label)])");
  const other = layouts.find((label) => !checkedTarget.some((x) => x.includes(label)));
  await clearToast();
  await clickExpr(menuItem(other));
  message = await toastHas("개 정렬");
  check("프리셋을 고르면 그 프리셋으로 정렬", message.includes(other), { other, message });
  const saved = await stored("nai_boxes");
  check("고른 프리셋을 입력창 묶음에 기억", Boolean(saved?.prompt?.preset), saved);
  await openMenu();
  const marked = await ev(`[...${MENU}.querySelectorAll('button')].filter((b) => b.querySelector('.mark').textContent).map((b) => b.innerText.trim())`);
  check("메뉴에 고른 프리셋 표시", marked.some((x) => x.includes(other)), marked);
  await click(200, 27);   // 윗줄의 빈 곳(화면 가운데 예시 그림을 누르면 프롬프트가 바뀐다)
  await sleep(150);
  check("바깥을 누르면 메뉴 닫힘", !(await ev(`!!${MENU}`)));
  await evaluate(sw, "chrome.storage.local.remove('nai_boxes')");

  await type(0, WORD_PROMPT);
  await type(1, "lowres, shoes");
  await openMenu();
  await clearToast();
  await clickExpr(menuItem("모두 정렬"));
  message = await toastHas("UC");
  check("보이는 입력창 모두 정렬", (await boxText(0)).startsWith("1girl") && message.includes("Prompt") && message.includes("UC"), { message, a: await boxText(0) });

  // ─── 패널 ───────────────────────────────────────────────────────
  const stackedCount = await ev(`${VISIBLE}.length`);
  const PANEL = "document.getElementById('tagorg-nai-panel')?.shadowRoot";
  const attachFrame = async () => {
    const target = await waitFor(async () => {
      const { targetInfos } = await send("Target.getTargets", { filter: [{}] });
      return targetInfos.find((t) => t.type === "iframe" && t.url.includes("nai/panel.html"));
    }, "패널 iframe");
    const frame = await attach(target.targetId);
    listeners.push((m) => {
      if (m.method === "Runtime.exceptionThrown" && m.sessionId === frame) pageErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    });
    return frame;
  };
  const inFrame = async (frame, expression) => evaluate(frame, expression);
  const frameOffset = () => ev(`(() => { const r = ${PANEL}.querySelector('iframe').getBoundingClientRect(); return [r.x, r.y]; })()`);
  const clickInFrame = async (frame, expression) => {
    const at = await inFrame(frame, `(() => { const el = ${expression}; el.scrollIntoView({ block: 'nearest' }); const r = el.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
    const offset = await frameOffset();
    await click(offset[0] + at[0], offset[1] + at[1]);
  };
  const frameReady = (frame) => waitFor(() => inFrame(frame, "document.querySelectorAll('#boxes button').length > 0 && !document.getElementById('state').textContent.includes('읽는 중')").catch(() => false), "패널 준비");
  const previewText = (frame) => inFrame(frame, "document.getElementById('text').value");

  await type(0, WORD_PROMPT);
  await openMenu();
  await clickExpr(menuItem("미리보기 패널"));
  await waitFor(() => ev(`!!${PANEL}`), "패널");
  let frame = await attachFrame();
  await frameReady(frame);
  const buttonsInPanel = await inFrame(frame, "[...document.querySelectorAll('#boxes button')].map((b) => b.textContent)");
  check("패널에 보이는 입력창 목록", buttonsInPanel.length === stackedCount && buttonsInPanel[0] === "Prompt", buttonsInPanel);

  const rect = await ev("(() => { const r = document.getElementById('tagorg-nai-panel').getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()");
  const sideRight = await ev("[...document.querySelectorAll('.image-gen-panel-sidebar')].find((e) => e.getBoundingClientRect().width > 0).getBoundingClientRect().right");
  check("패널이 입력창 옆에 놓임", rect.left >= sideRight, { rect, sideRight });
  check("패널이 입력창을 가리지 않음", await ev(`(() => { const el = ${VISIBLE}[0]; const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return el.contains(top); })()`));
  check("페이지는 패널 안을 읽을 수 없음", (await ev(`${PANEL}.querySelector('iframe').contentDocument === null`)) === true);

  await sleep(200);
  check("색 미리보기에 토큰이 그려짐", (await inFrame(frame, "document.querySelectorAll('#chips .token').length")) >= 6);
  check("뺀 태그는 흐리게 남음", await inFrame(frame, "[...document.querySelectorAll('#chips .token.removed')].some((t) => t.textContent === 'shoes')"));
  check("미리보기는 입력창을 바꾸지 않음", squeeze(await boxText(0)) === squeeze(WORD_PROMPT));
  check("상태 줄에 처리 시간과 문법", /처리 .*ms.*문법/.test(await inFrame(frame, "document.getElementById('state').textContent")), await inFrame(frame, "document.getElementById('state').textContent"));
  await screenshot("nai_panel.png");

  // 살아 있는 미리보기: 입력하면 바로 바뀐다
  await type(0, "blue eyes, 1girl");
  await waitFor(async () => (await previewText(frame)).startsWith("1girl"), "입력 따라가기", 3000).catch(() => {});
  check("입력하면 미리보기가 바뀜", (await previewText(frame)).includes("blue eyes"), await previewText(frame));

  // 입력창을 누르면 패널 대상도 바뀐다
  await type(1, "lowres, shoes");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로 전환", 3000).catch(() => {});
  check("입력창을 누르면 패널 대상이 바뀜", await inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"));
  check("UC 에서는 크롭·시점을 숨김", await inFrame(frame, "document.getElementById('overrides').hidden"));
  check("UC 에서는 원본 칸을 숨김", await inFrame(frame, "document.getElementById('master').hidden"));
  check("UC 는 구도 규칙 끔으로 시작", (await inFrame(frame, "document.getElementById('rules').value")) === "off");

  await clickInFrame(frame, "document.querySelectorAll('#boxes button')[0]");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'Prompt'"), "Prompt 로 전환", 3000).catch(() => {});
  check("패널에서 대상을 바꿈", await inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'Prompt'"));

  await type(0, WORD_PROMPT);
  await sleep(300);

  // 크롭 덮어쓰기: 미리보기에만, 저장하지 않음
  await clickInFrame(frame, "[...document.querySelectorAll('#framing button')].find((b) => b.textContent === 'full body')");
  await sleep(100);
  let preview = await previewText(frame);
  check("크롭 덮어쓰기가 미리보기에 들어감", preview.includes("full body") && !preview.includes("upper body") && preview.includes("shoes"), preview);
  check("넣은 태그 표시", await inFrame(frame, "[...document.querySelectorAll('#chips .token.injected')].some((t) => t.textContent === 'full body')"));
  await clickInFrame(frame, "[...document.querySelectorAll('#framing button')].find((b) => b.textContent === '자동')");
  await sleep(100);
  check("자동으로 돌리면 덮어쓰기 해제", (await previewText(frame)).includes("upper body"));

  // 시선 덮어쓰기: 여러 개, 함께 못 쓰는 태그는 꺼지고, 시점이 막는 태그는 사라진다
  const gazeChip = (text) => `[...document.querySelectorAll('#gaze button')].find((b) => b.textContent === '${text}')`;
  const pressed = (text) => inFrame(frame, `${gazeChip(text)}?.getAttribute('aria-pressed') === 'true'`);
  await clickInFrame(frame, gazeChip("looking back"));
  await sleep(100);
  await clickInFrame(frame, gazeChip("looking at viewer"));
  await sleep(100);
  preview = await previewText(frame);
  check("시선을 여러 개 고름", preview.includes("looking back") && preview.includes("looking at viewer"), preview);
  await clickInFrame(frame, gazeChip("facing away"));
  await sleep(100);
  check("함께 못 쓰는 시선은 꺼짐", (await pressed("facing away")) && !(await pressed("looking back")));
  await clickInFrame(frame, gazeChip("looking back"));
  await sleep(100);
  await clickInFrame(frame, "[...document.querySelectorAll('#view button')].find((b) => b.textContent === 'straight-on')");
  await sleep(100);
  check("시점이 막는 시선은 목록에서 사라짐", await inFrame(frame, `!${gazeChip("looking back")}`));
  check("고른 시선이 모두 막히면 자동으로 보임", await pressed("자동"));
  check("막힌 시선은 프롬프트에 넣지 않음", !(await previewText(frame)).includes("looking back"));
  await clickInFrame(frame, "[...document.querySelectorAll('#view button')].find((b) => b.textContent === '자동')");
  await sleep(100);
  check("시점을 되돌리면 선택이 돌아옴", await pressed("looking back"));
  await clickInFrame(frame, gazeChip("자동"));
  await sleep(100);
  check("시선 자동으로 해제", !(await previewText(frame)).includes("looking back"));

  // 구도 규칙·추정·프리셋: 입력창 묶음별로 저장
  await inFrame(frame, "(() => { const el = document.getElementById('rules'); el.value = 'off'; el.dispatchEvent(new Event('change')); })()");
  await sleep(150);
  check("구도 규칙을 끄면 shoes 가 남음", (await previewText(frame)).includes("shoes"));
  check("묶음별로 저장", (await stored("nai_boxes"))?.prompt?.rules === "off", await stored("nai_boxes"));
  await inFrame(frame, "(() => { const el = document.getElementById('rules'); el.value = 'remove'; el.dispatchEvent(new Event('change')); })()");
  await sleep(150);
  const presetCount = await inFrame(frame, "document.getElementById('preset').options.length");
  check("프리셋 목록", presetCount >= 2, presetCount);
  const before = await previewText(frame);
  const changed = await inFrame(frame, `(async () => {
    const el = document.getElementById('preset');
    const out = [];
    for (let i = 0; i < el.options.length; i++) {
      el.selectedIndex = i;
      el.dispatchEvent(new Event('change'));
      await new Promise((r) => setTimeout(r, 30));
      out.push(document.getElementById('text').value);
    }
    el.selectedIndex = 0;
    el.dispatchEvent(new Event('change'));
    return out;
  })()`);
  check("프리셋을 바꾸면 미리보기가 바뀜", new Set(changed).size > 1, changed.length);
  check("처음 프리셋으로 돌아옴", changed.includes(before));

  // 보기 전환
  await clickInFrame(frame, "document.getElementById('show_text')");
  check("텍스트 보기", await inFrame(frame, "!document.getElementById('text').hidden && document.getElementById('chips').hidden"));
  await screenshot("nai_panel_text.png");
  await clickInFrame(frame, "document.getElementById('show_chips')");

  // 복사
  await clickInFrame(frame, "document.getElementById('copy')");
  await sleep(200);
  check("결과 복사", (await inFrame(frame, "document.getElementById('notice').textContent")).includes("복사했습니다"), await inFrame(frame, "document.getElementById('notice').textContent"));

  // 적용과 되돌리기
  preview = await previewText(frame);
  await clickInFrame(frame, "document.getElementById('apply')");
  await sleep(500);
  check("적용하면 입력창이 바뀜", squeeze(await boxText(0)) === squeeze(preview), await boxText(0));
  check("적용 뒤 정렬됨 표시", await inFrame(frame, "document.getElementById('apply').disabled && document.getElementById('apply').textContent === '정렬됨'"), await inFrame(frame, "document.getElementById('apply').textContent"));
  check("적용 안내", (await inFrame(frame, "document.getElementById('notice').textContent")).includes("Ctrl+Z"));
  check("적용 뒤 포커스는 입력창", await ev(`document.activeElement === ${VISIBLE}[0]`));
  await undo();
  await sleep(200);
  check("적용을 Ctrl+Z 로 되돌림", squeeze(await boxText(0)) === squeeze(WORD_PROMPT), await boxText(0));
  check("되돌리면 다시 적용 가능", await inFrame(frame, "!document.getElementById('apply').disabled"));

  // 패널을 연 채로 NovelAI 쪽 조작이 되는지 + 끌기·크기·접기
  const title = await ev(`(() => { const r = ${PANEL}.querySelector('.title span').getBoundingClientRect(); return [r.x + 40, r.y + r.height / 2]; })()`);
  const drag = async (from, to) => {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from[0], y: from[1] }, page);
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: from[0], y: from[1], button: "left", clickCount: 1 }, page);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: (from[0] + to[0]) / 2, y: (from[1] + to[1]) / 2, button: "left", buttons: 1 }, page);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: to[0], y: to[1], button: "left", buttons: 1 }, page);
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: to[0], y: to[1], button: "left", clickCount: 1 }, page);
    await sleep(200);
  };
  const panelBox = () => ev("(() => { const r = document.getElementById('tagorg-nai-panel').getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }; })()");
  const start = await panelBox();
  await drag(title, [title[0] + 60, title[1] + 40]);
  const moved = await panelBox();
  check("제목 줄을 끌면 옮겨짐", moved.left === start.left + 60 && moved.top === start.top + 40, { start, moved });
  const grip = await ev(`(() => { const r = ${PANEL}.querySelector('.grip').getBoundingClientRect(); return [r.x + 7, r.y + 7]; })()`);
  await drag(grip, [grip[0] - 80, grip[1] - 50]);
  const sized = await panelBox();
  check("모서리를 끌면 크기가 바뀜", sized.width === moved.width - 80 && sized.height === moved.height - 50, { moved, sized });
  const remembered = await stored("nai_panel");
  check("위치와 크기를 기억", Math.round(remembered?.left) === sized.left && Math.round(remembered?.width) === sized.width, remembered);
  await clickExpr(`${PANEL}.querySelector('.fold')`);
  const folded = await panelBox();
  check("접으면 제목 줄만 남음", folded.height <= 34, folded);
  check("접기 단추 글이 바뀜", (await ev(`${PANEL}.querySelector('.fold').textContent`)) === "펼치기");
  await clickExpr(`${PANEL}.querySelector('.fold')`);
  check("펼치면 크기가 돌아옴", (await panelBox()).height === sized.height);

  // 폭 전환으로 입력창이 새로 만들어져도 패널이 따라감
  await narrow();
  const inside = await ev("(() => { const r = document.getElementById('tagorg-nai-panel').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; })()");
  check("좁아져도 패널이 화면 안", inside);
  await screenshot("nai_panel_narrow.png");
  // 좁은 배치의 입력창은 접힌 시트 안에 있고 패널에 덮이므로 편집기 명령으로 넣는다
  await ev(`(async () => {
    const el = ${VISIBLE}[0];
    el.focus();
    getSelection().selectAllChildren(el);
    await new Promise((r) => setTimeout(r, 100));
    document.execCommand('insertText', false, 'pink eyes, 1girl');
  })()`);
  await waitFor(async () => (await previewText(frame)).includes("pink eyes"), "재구성 뒤 입력 따라가기", 3000).catch(() => {});
  check("입력창이 새로 만들어져도 패널이 따라감", (await previewText(frame)).includes("pink eyes"), await previewText(frame));
  await wide();

  // Esc 와 닫기, 다시 열면 같은 자리
  await clickInFrame(frame, "document.getElementById('state')");
  await key("Escape", "Escape", 27);
  await sleep(300);
  check("Esc 로 패널 닫힘", !(await ev(`!!${PANEL}`)));
  await openMenu();
  await clickExpr(menuItem("미리보기 패널"));
  await waitFor(() => ev(`!!${PANEL}`), "패널 다시 열기");
  frame = await attachFrame();
  await frameReady(frame);
  const reopened = await panelBox();
  check("다시 열면 같은 자리", reopened.left === sized.left && reopened.width === sized.width, { reopened, sized });
  await clickExpr(`${PANEL}.querySelector('.close')`);
  await sleep(200);
  check("닫기 단추", !(await ev(`!!${PANEL}`)));

  // ─── 배열 편집 ──────────────────────────────────────────────────
  const openEditor = async () => {
    await openMenu();
    await clickExpr(menuItem("미리보기 패널"));
    await waitFor(() => ev(`!!${PANEL}`), "패널");
    const opened = await attachFrame();
    await frameReady(opened);
    return opened;
  };
  const closePanel = async () => {
    await clickExpr(`${PANEL}.querySelector('.close')`);
    await sleep(200);
  };
  const pagePoint = async (f, expression) => {
    const at = await inFrame(f, `(() => { const el = ${expression}; el.scrollIntoView({ block: 'nearest' }); const r = el.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
    const offset = await frameOffset();
    return [offset[0] + at[0], offset[1] + at[1]];
  };
  const dragTo = async (from, to, { release = true } = {}) => {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from[0], y: from[1] }, page);
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: from[0], y: from[1], button: "left", clickCount: 1, buttons: 1 }, page);
    for (const t of [0.25, 0.5, 0.75, 1]) {
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from[0] + (to[0] - from[0]) * t, y: from[1] + (to[1] - from[1]) * t, button: "left", buttons: 1 }, page);
      await sleep(30);
    }
    if (release) await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: to[0], y: to[1], button: "left", clickCount: 1 }, page);
    await sleep(200);
  };
  const rightClick = async (at) => {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at[0], y: at[1] }, page);
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: at[0], y: at[1], button: "right", clickCount: 1, buttons: 2 }, page);
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: at[0], y: at[1], button: "right", clickCount: 1 }, page);
    await sleep(200);
  };
  const chunkOfTag = (tag) => `document.querySelector('.bmk-to-chunk[data-cid="' + CSS.escape([...document.querySelectorAll('.bmk-to-tok')].find((t) => t.textContent === ${JSON.stringify(tag)}).closest('.bmk-to-grp').dataset.cid) + '"]')`;
  const barButton = (label) => `[...document.querySelectorAll('.bmk-to-bar button')].find((b) => b.textContent === ${JSON.stringify(label)})`;
  const menuEntry = (label) => `[...document.querySelectorAll('.bmk-to-menu .it')].find((i) => i.textContent.includes(${JSON.stringify(label)}))`;
  const dialogButton = (label) => `[...document.querySelectorAll('.bmk-to-dialog button')].find((b) => b.textContent === ${JSON.stringify(label)})`;
  const firstLine = async (f) => (await previewText(f)).split("\n")[0];
  const stateOf = (f) => inFrame(f, "document.querySelector('.bmk-to-state').textContent");

  // 원본 고정: 적용으로 빠진 태그도 다음 시점에서 다시 쓴다.
  const masterPrompt = "1girl, smile, blue eyes, looking back, full body, shoes";
  const masterText = () => inFrame(frame, "document.getElementById('master_text').value");
  const viewChip = (tag) => `[...document.querySelectorAll('#view button')].find((b) => b.textContent === ${JSON.stringify(tag)})`;
  const writeMaster = async (text) => {
    await clickInFrame(frame, "document.getElementById('show_master')");
    await clickInFrame(frame, "document.getElementById('master_text')");
    await key("a", "KeyA", 65, { modifiers: 2, command: "selectAll" });
    if (text) await send("Input.insertText", { text }, page);
    else await key("Delete", "Delete", 46, { command: "deleteForward" });
  };
  const applyMaster = async () => {
    const expected = await previewText(frame);
    await clickInFrame(frame, "document.getElementById('apply')");
    await waitFor(async () => squeeze(await boxText(0)) === squeeze(expected), "원본 결과 적용");
    await waitFor(async () => (await stored("nai_masters"))?.prompt?.lastApplied === expected, "적용 결과 저장");
    return expected;
  };
  await type(0, masterPrompt);
  frame = await openEditor();
  check("원본을 켜기 전에는 토글만 보임", await inFrame(frame, "!document.getElementById('master').hidden && document.getElementById('master_body').hidden && document.getElementById('show_master').hidden"));
  await clickInFrame(frame, "document.getElementById('master_on')");
  check("처음 켜면 입력창으로 원본을 채움", (await masterText()) === masterPrompt);
  check("켜면 원본 탭이 생기고 칸은 탭 안에 있음", await inFrame(frame, "!document.getElementById('show_master').hidden && document.getElementById('master_body').hidden && !document.getElementById('chips').hidden"));
  await clickInFrame(frame, "document.getElementById('show_master')");
  check("원본 탭은 미리보기 자리를 쓰고 가져오기가 보임", await inFrame(frame, "!document.getElementById('master_body').hidden && document.getElementById('chips').hidden && !document.getElementById('master_import').hidden && document.getElementById('master_text').getBoundingClientRect().height > 100"));
  await screenshot("nai_master_tab.png");
  await clickInFrame(frame, "document.getElementById('show_chips')");
  await clickInFrame(frame, viewChip("straight-on"));
  preview = await applyMaster();
  check("원본 적용: 결과에서만 시선이 빠짐", !preview.includes("looking back") && (await masterText()) === masterPrompt, preview);
  check("원본과 달라도 입력창이 결과와 같으면 정렬됨", await inFrame(frame, "document.getElementById('apply').disabled && document.getElementById('apply').textContent === '정렬됨'"));
  await screenshot("nai_master.png");
  await clickInFrame(frame, viewChip("from behind"));
  check("시점을 바꿔도 마지막 적용 글이면 경고 없음", await inFrame(frame, "document.getElementById('master_warning').hidden"));
  const masterOutput = await applyMaster();
  check("다른 시점에서 빠졌던 태그가 돌아옴", masterOutput.includes("looking back") && masterOutput.includes("from behind") && (await masterText()) === masterPrompt, masterOutput);
  await closePanel();
  frame = await openEditor();
  check("다시 열어도 원본과 켜짐 유지", (await masterText()) === masterPrompt && await inFrame(frame, "document.getElementById('master_on').checked"));
  check("다시 열어도 칩과 결과 유지", await inFrame(frame, `${viewChip("from behind")}.getAttribute('aria-pressed') === 'true'`) && (await previewText(frame)) === masterOutput);

  await type(0, "");
  check("빈 입력창에도 원본으로 적용 가능하고 경고 없음", await inFrame(frame, "!document.getElementById('apply').disabled && document.getElementById('master_warning').hidden") && (await previewText(frame)) === masterOutput);
  await applyMaster();
  await type(0, "1girl, red hair");
  await waitFor(() => inFrame(frame, "!document.getElementById('master_warning').hidden"), "직접 수정 경고");
  check("직접 수정은 경고하되 적용을 막지 않음", await inFrame(frame, "!document.getElementById('apply').disabled") && (await previewText(frame)) === masterOutput);
  await clickInFrame(frame, "document.getElementById('show_master')");
  check("직접 수정 경고는 원본 탭에서도 보임", await inFrame(frame, "!document.getElementById('master_warning').hidden"));
  await clickInFrame(frame, "document.getElementById('master_import')");
  check("가져오기 첫 클릭은 원본을 지킴", (await masterText()) === masterPrompt && (await inFrame(frame, "document.getElementById('notice').textContent")).includes("한 번 더"));
  await type(0, "1girl, green hair");
  await clickInFrame(frame, "document.getElementById('master_import')");
  check("확인 사이 입력창이 바뀌면 다시 확인", (await masterText()) === masterPrompt);
  await clickInFrame(frame, "document.getElementById('master_import')");
  check("가져오기 두 번째 클릭으로 원본 교체", (await masterText()) === "1girl, green hair" && (await previewText(frame)).includes("green hair"));
  check("가져온 뒤에는 경고 없음", await inFrame(frame, "document.getElementById('master_warning').hidden"));
  await clickInFrame(frame, "document.getElementById('show_chips')");
  // 다른 탭의 패널이 저장한 입력창은 이 패널의 저장이 덮지 않는다
  await evaluate(sw, "chrome.storage.local.get('nai_masters').then((x) => chrome.storage.local.set({ nai_masters: { ...x.nai_masters, 'base-prompt': { on: true, group: 'prompt', text: 'other tab', lastApplied: '' } } }))");
  await sleep(150);
  await clickInFrame(frame, viewChip("from side"));
  await sleep(150);
  check("다른 탭의 원본을 지우지 않음", (await stored("nai_masters"))?.["base-prompt"]?.text === "other tab", await stored("nai_masters"));
  await evaluate(sw, "chrome.storage.local.get('nai_masters').then(({ nai_masters: { 'base-prompt': _, ...rest } }) => chrome.storage.local.set({ nai_masters: rest }))");
  await clickInFrame(frame, viewChip("from side"));
  await sleep(100);

  await clickInFrame(frame, "document.getElementById('edit_toggle')");
  await waitFor(() => inFrame(frame, "document.querySelectorAll('.bmk-to-tok').length > 0"), "원본 배열 미리보기");
  const editedMaster = `${masterPrompt}, pink hair`;
  await writeMaster(editedMaster);
  await waitFor(async () => (await stored("nai_masters"))?.prompt?.text === editedMaster, "원본 입력 저장");
  check("배열 편집 중에도 원본 탭으로 고칠 수 있음", await inFrame(frame, "document.getElementById('editor').hidden && !document.getElementById('preview').hidden && !document.getElementById('master_body').hidden && document.getElementById('show_text').hidden") && (await boxText(0)).trim() === "1girl, green hair");
  await clickInFrame(frame, "document.getElementById('show_chips')");
  check("배열 탭으로 돌아오면 청크 화면", await inFrame(frame, "!document.getElementById('editor').hidden && document.getElementById('preview').hidden && document.getElementById('show_chips').textContent === '배열'"));
  await waitFor(() => inFrame(frame, "[...document.querySelectorAll('.bmk-to-tok')].some((t) => t.textContent === 'pink hair')"), "원본으로 배열 미리보기");
  await clickInFrame(frame, "document.getElementById('show_master')");
  const masterRect = await panelBox();
  const masterGrip = await ev(`(() => { const r = ${PANEL}.querySelector('.grip').getBoundingClientRect(); return [r.x + 7, r.y + 7]; })()`);
  await drag(masterGrip, [masterGrip[0] + 420 - masterRect.width, masterGrip[1]]);
  check("420px 패널에서 원본 줄이 넘치지 않음", await inFrame(frame, "innerWidth <= 420 && document.documentElement.scrollWidth <= innerWidth && document.getElementById('master_text').getBoundingClientRect().right <= innerWidth"));
  await writeMaster("");
  check("빈 원본은 입력창으로 대체하지 않고 안내", await inFrame(frame, "document.getElementById('apply').disabled && !document.getElementById('state').hidden && document.getElementById('state').textContent.includes('원본 프롬프트가 비어')"));
  await writeMaster(editedMaster);
  await clickInFrame(frame, "document.getElementById('master_on')");
  check("끄면 원본 탭이 사라지고 청크 화면으로", await inFrame(frame, "document.getElementById('show_master').hidden && document.getElementById('views').hidden && !document.getElementById('editor').hidden"));
  check("끄면 입력창을 쓰고 원본은 남김", (await previewText(frame)).includes("green hair") && (await masterText()) === editedMaster);
  await clickInFrame(frame, "document.getElementById('master_on')");
  check("다시 켜면 보관한 원본을 씀", (await masterText()) === editedMaster && (await previewText(frame)).includes("pink hair"));
  await clickInFrame(frame, "document.getElementById('edit_toggle')");
  await clickInFrame(frame, "document.getElementById('master_on')");
  await waitFor(async () => !(await stored("nai_overrides"))?.prompt, "꺼진 묶음의 칩 저장 해제");
  await closePanel();
  frame = await openEditor();
  check("꺼진 원본은 보관하고 칩은 잊음", (await masterText()) === editedMaster && await inFrame(frame, `!document.getElementById('master_on').checked && ${viewChip("자동")}.getAttribute('aria-pressed') === 'true'`));
  await closePanel();
  await evaluate(sw, "chrome.storage.local.remove(['nai_masters', 'nai_overrides', 'nai_editing'])");

  // 적용 후 생성: NovelAI 의 Generate 를 한 번 누른다. 시험에서는 눌림만 세고 NovelAI 로 넘기지 않는다(생성 요청 없음)
  await ev(`document.addEventListener('click', (e) => {
    if (!e.target.closest?.('.image-gen-generate-button')) return;
    window.__tagorgGenerate = (window.__tagorgGenerate || 0) + 1;
    e.stopImmediatePropagation();
    e.preventDefault();
  }, true)`);
  const genPressed = async () => (await ev("window.__tagorgGenerate")) || 0;
  const NAI_GENERATE = "[...document.querySelectorAll('.image-gen-generate-button')].find((b) => b.getBoundingClientRect().width > 0)";
  const genButton = "document.getElementById('generate')";
  const generateText = () => inFrame(frame, `${genButton}.textContent`);
  const genNotice = () => inFrame(frame, "document.getElementById('notice').textContent");
  await type(0, WORD_PROMPT);
  frame = await openEditor();
  const genPlanned = await previewText(frame);
  check("적용할 것이 있으면 비용과 함께 적용 후 생성", /^적용 후 생성 · \d+$/.test(await generateText()), await generateText());
  await screenshot("nai_generate.png");
  await clickInFrame(frame, genButton);
  await waitFor(async () => (await genPressed()) === 1, "Generate 눌림");
  check("입력창에 결과를 넣고 Generate 를 한 번 누름", squeeze(await boxText(0)) === squeeze(genPlanned) && (await genPressed()) === 1, { box: await boxText(0), genPlanned });
  check("누른 뒤에는 잠김", await inFrame(frame, `${genButton}.disabled`) && (await generateText()) === "생성 중…", await generateText());
  await clickInFrame(frame, genButton);
  await sleep(100);
  check("잠긴 동안 다시 눌러도 그대로", (await genPressed()) === 1, await genPressed());
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "잠금 풀림", 4000);
  check("넣을 것이 없으면 생성", /^생성 · \d+$/.test(await generateText()), await generateText());
  await clickInFrame(frame, genButton);
  await waitFor(async () => (await genPressed()) === 2, "두 번째 Generate 눌림");
  check("이미 넣었으면 글을 건드리지 않고 누름", squeeze(await boxText(0)) === squeeze(genPlanned) && (await genNotice()).includes("생성을 눌렀습니다"), await genNotice());
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "잠금 풀림", 4000);
  await ev(`${NAI_GENERATE}.disabled = true`);
  await waitFor(() => inFrame(frame, `${genButton}.disabled`), "NovelAI 버튼을 따라 꺼짐", 3000);
  check("NovelAI 버튼이 꺼지면 함께 꺼짐", await inFrame(frame, `${genButton}.disabled`));
  await ev("[...document.querySelectorAll('.image-gen-generate-button')].forEach((b) => { b.disabled = false; })");
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "NovelAI 버튼을 따라 켜짐", 3000);
  await ev(`(() => { const b = ${NAI_GENERATE}; b.dataset.tagorgGenerate = '1'; b.classList.remove('image-gen-generate-button'); })()`);
  await waitFor(() => inFrame(frame, `${genButton}.hidden`), "NovelAI 버튼을 못 찾으면 숨김", 3000);
  check("NovelAI 버튼을 못 찾으면 숨기고 적용이 주 버튼", await inFrame(frame, `${genButton}.hidden && document.getElementById('apply').classList.contains('primary')`));
  await ev("document.querySelector('[data-tagorg-generate]').classList.add('image-gen-generate-button')");
  await waitFor(() => inFrame(frame, `!${genButton}.hidden`), "NovelAI 버튼을 다시 찾음", 3000);
  await undo();
  // 원본: 직접 고친 입력창은 생성하지 않고, 원본을 켠 다른 입력창도 함께 넣는다
  await clickInFrame(frame, "document.getElementById('master_on')");
  await clickInFrame(frame, viewChip("straight-on"));
  const genMasterPlanned = await previewText(frame);
  let genCount = await genPressed();
  await clickInFrame(frame, genButton);
  await waitFor(async () => (await genPressed()) === genCount + 1, "원본 결과로 Generate 눌림");
  check("원본 결과를 넣고 누름", squeeze(await boxText(0)) === squeeze(genMasterPlanned));
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "잠금 풀림", 4000);
  await type(0, "1girl, red hair");
  await waitFor(() => inFrame(frame, "!document.getElementById('master_warning').hidden"), "직접 수정 경고");
  genCount = await genPressed();
  await clickInFrame(frame, genButton);
  await sleep(200);
  check("직접 고친 입력창은 생성하지 않고 알림", (await genPressed()) === genCount && (await genNotice()).includes("직접 고쳤습니다") && (await boxText(0)).trim() === "1girl, red hair", await genNotice());
  await type(0, "");
  await type(1, "lowres, upper body, masterpiece");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로", 3000);
  check("UC 를 고른 채로도 원본이 켜진 입력창을 함께 넣을 계획", /^적용 후 생성/.test(await generateText()), await generateText());
  await clickInFrame(frame, genButton);
  await waitFor(async () => (await genPressed()) === genCount + 1, "UC 에서 Generate 눌림");
  check("원본이 켜진 입력창도 함께 넣고 대상은 그대로", squeeze(await boxText(0)) === squeeze(genMasterPlanned) && await inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), await boxText(0));
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "잠금 풀림", 4000);
  await type(0, "");
  await clickInFrame(frame, "document.querySelector('#boxes button:nth-child(2)')");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로 돌아옴", 3000);
  genCount = await genPressed();
  await clickInFrame(frame, genButton);
  await waitFor(async () => (await genPressed()) === genCount + 1, "UC 를 그대로 두고 Generate 눌림");
  await sleep(200);
  check("다른 입력창만 넣어도 대상은 그대로", squeeze(await boxText(0)) === squeeze(genMasterPlanned) && await inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), await boxText(0));
  // 넣은 뒤 누르기 전에 글이 바뀌면 누르지 않는다
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "잠금 풀림", 4000);
  await type(0, "");
  await clickInFrame(frame, "document.querySelector('#boxes button:nth-child(2)')");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로 돌아옴", 3000);
  genCount = await genPressed();
  await ev(`(() => { const el = ${VISIBLE}[0]; const seen = new MutationObserver(() => { seen.disconnect(); setTimeout(() => el.querySelector('p').append(' extra'), 120); }); seen.observe(el, { childList: true, subtree: true, characterData: true }); })()`);
  await clickInFrame(frame, genButton);
  await sleep(900);
  check("넣은 뒤 글이 바뀌면 누르지 않음", (await genPressed()) === genCount && (await genNotice()).includes("글이 바뀌어서"), await genNotice());
  await waitFor(() => inFrame(frame, `!${genButton}.disabled`), "버튼이 다시 켜짐", 3000);
  // 아직 적용하지 않은 원본이 다른 입력창의 글을 덮으면 누르지 않는다
  await evaluate(sw, "chrome.storage.local.get('nai_masters').then((x) => chrome.storage.local.set({ nai_masters: { ...x.nai_masters, prompt: { ...x.nai_masters.prompt, lastApplied: '' } } }))");
  await sleep(200);
  await type(0, "1girl, blue hair");
  await clickInFrame(frame, "document.querySelector('#boxes button:nth-child(2)')");
  await waitFor(() => inFrame(frame, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로 돌아옴", 3000);
  await clickInFrame(frame, genButton);
  await sleep(300);
  check("한 번도 넣지 않은 원본이 다른 입력창의 글을 덮으면 누르지 않음", (await genPressed()) === genCount && (await boxText(0)).trim() === "1girl, blue hair" && (await genNotice()).includes("직접 고쳤습니다"), { box: await boxText(0), notice: await genNotice() });
  // 원본을 켠 다른 입력창의 결과가 비면 이전 글로 생성하지 않는다
  await evaluate(sw, "chrome.storage.local.get('nai_masters').then((x) => chrome.storage.local.set({ nai_masters: { ...x.nai_masters, prompt: { ...x.nai_masters.prompt, text: '' } } }))");
  await sleep(300);
  await clickInFrame(frame, genButton);
  await sleep(300);
  check("원본을 켠 다른 입력창이 비면 누르지 않음", (await genPressed()) === genCount && (await genNotice()).includes("비어 있어"), await genNotice());
  await type(1, "");
  await closePanel();
  await evaluate(sw, "chrome.storage.local.remove(['nai_masters', 'nai_overrides', 'nai_editing'])");
  await type(0, WORD_PROMPT);

  // 왼쪽 칸 옆에 붙이기: 그림 칸에 여백만 주고, 칸을 못 찾으면 떠 있는 창으로 돌아간다
  frame = await openEditor();
  const dockButton = `${PANEL}.querySelector('.dock')`;
  const geometry = () => ev(`(() => {
    const box = (el) => { const r = el.getBoundingClientRect(); return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width) }; };
    const panel = document.getElementById('tagorg-nai-panel');
    return { side: box(document.querySelector('.image-gen-panel-sidebar')), stage: box(document.querySelector('.image-gen-results-stage')), panel: panel && box(panel), docked: panel?.classList.contains('docked') };
  })()`);
  const dockFree = await geometry();
  check("처음에는 떠 있고 붙이기 버튼이 보임", !dockFree.docked && (await ev(`${dockButton}.textContent`)) === "붙이기");
  await clickExpr(dockButton);
  await sleep(300);
  let dockAt = await geometry();
  check("왼쪽 칸 바로 옆에 붙음", dockAt.docked && dockAt.panel.left === dockAt.side.right && dockAt.panel.top === dockAt.side.top && dockAt.panel.bottom === dockAt.side.bottom, dockAt);
  check("그림 칸이 패널 오른쪽에서 시작하고 오른쪽 끝은 그대로", dockAt.stage.left === dockAt.panel.right && dockAt.stage.right === dockFree.stage.right, { dockAt, dockFree });
  check("붙이기 상태를 기억", (await stored("nai_panel_dock"))?.on === true);
  await screenshot("nai_docked.png");
  await type(0, `${WORD_PROMPT}, blush`);
  await sleep(200);
  dockAt = await geometry();
  check("입력해도 붙은 채 그대로", dockAt.docked && dockAt.stage.left === dockAt.panel.right, dockAt);
  const dockGrip = await ev(`(() => { const r = ${PANEL}.querySelector('.grip').getBoundingClientRect(); return [r.x + 3, r.y + r.height / 2]; })()`);
  const widthBefore = dockAt.panel.width;
  await drag(dockGrip, [dockGrip[0] + 60, dockGrip[1]]);
  dockAt = await geometry();
  check("오른쪽 가장자리로 폭을 바꾸면 그림 칸도 따라옴", dockAt.panel.width === widthBefore + 60 && dockAt.stage.left === dockAt.panel.right, dockAt);
  check("폭을 기억", (await stored("nai_panel_dock"))?.width === widthBefore + 60, await stored("nai_panel_dock"));
  await clickExpr(`${PANEL}.querySelector('.fold')`);
  await sleep(200);
  dockAt = await geometry();
  check("접으면 그림 칸이 원래 폭", dockAt.stage.left === dockFree.stage.left && dockAt.stage.width === dockFree.stage.width, dockAt);
  await clickExpr(`${PANEL}.querySelector('.fold')`);
  await sleep(200);
  dockAt = await geometry();
  check("펼치면 다시 밀림", dockAt.stage.left === dockAt.panel.right, dockAt);
  await closePanel();
  dockAt = await geometry();
  check("닫으면 그림 칸이 원래 폭", dockAt.stage.left === dockFree.stage.left && dockAt.stage.width === dockFree.stage.width, dockAt);
  frame = await openEditor();
  await sleep(300);
  dockAt = await geometry();
  check("다시 열어도 붙음", dockAt.docked && dockAt.panel.width === widthBefore + 60 && dockAt.stage.left === dockAt.panel.right, dockAt);
  await ev("document.querySelector('.image-gen-results-stage').classList.replace('image-gen-results-stage', 'tagorg-stage-test')");
  await sleep(300);
  dockAt = await ev(`(() => { const panel = document.getElementById('tagorg-nai-panel'); const stage = document.querySelector('.tagorg-stage-test'); return { docked: panel.classList.contains('docked'), left: Math.round(stage.getBoundingClientRect().left), dockHidden: ${dockButton}.hidden }; })()`);
  check("그림 칸을 못 찾으면 떠 있는 창으로 돌아가고 여백을 지움", !dockAt.docked && dockAt.left === dockFree.stage.left && dockAt.dockHidden, dockAt);
  await ev("document.querySelector('.tagorg-stage-test').classList.replace('tagorg-stage-test', 'image-gen-results-stage')");
  await sleep(300);
  dockAt = await geometry();
  check("다시 찾으면 붙음", dockAt.docked && dockAt.stage.left === dockAt.panel.right, dockAt);
  await send("Emulation.setDeviceMetricsOverride", { width: 1000, height: 800, deviceScaleFactor: 1, mobile: false }, page);
  await sleep(500);
  dockAt = await geometry();
  check("그림 칸이 충분히 남지 않으면 떠 있는 창", !dockAt.docked && dockAt.stage.left === dockAt.side.right, dockAt);
  await send("Emulation.clearDeviceMetricsOverride", {}, page);
  await sleep(500);
  dockAt = await geometry();
  check("넓어지면 다시 붙음", dockAt.docked && dockAt.stage.left === dockAt.panel.right, dockAt);
  await clickExpr(dockButton);
  await sleep(300);
  dockAt = await geometry();
  check("띄우기로 돌아오면 여백을 지움", !dockAt.docked && dockAt.stage.left === dockFree.stage.left && (await stored("nai_panel_dock"))?.on === false, dockAt);
  await closePanel();
  await evaluate(sw, "chrome.storage.local.remove(['nai_panel', 'nai_panel_dock'])");

  // 시점 칩: 여러 개를 고르고, 부딪치면 꺼지고, multiple views 를 켜면 함께 고른다
  await type(0, "1girl, ass, groin, navel");
  frame = await openEditor();
  const viewOn = (tag) => inFrame(frame, `${viewChip(tag)}.getAttribute('aria-pressed') === 'true'`);
  const pickView = async (tag) => {
    await clickInFrame(frame, viewChip(tag));
    await sleep(100);
  };
  await pickView("from behind");
  await pickView("from side");
  preview = await previewText(frame);
  check("시점을 여러 개 고름", (await viewOn("from behind")) && (await viewOn("from side")) && preview.includes("from side, from behind"), preview);
  check("측후면은 앞면 태그를 뺌", preview.includes("ass") && !preview.includes("groin") && !preview.includes("navel"), preview);
  await pickView("front view");
  preview = await previewText(frame);
  check("부딪치는 시점은 꺼짐", (await viewOn("front view")) && !(await viewOn("from behind")) && (await viewOn("from side")), preview);
  check("끈 까닭과 multiple views 를 알림", (await inFrame(frame, "document.getElementById('notice').textContent")).includes("multiple views"));
  check("정면은 뒷면 태그를 뺌", !preview.includes("ass") && preview.includes("groin") && preview.includes("navel"), preview);
  await pickView("multiple views");
  await pickView("from behind");
  preview = await previewText(frame);
  check("multiple views 를 켜면 함께 고름", (await viewOn("front view")) && (await viewOn("from behind")) && preview.includes("multiple views"), preview);
  check("multiple views 면 시점 규칙을 쓰지 않음", preview.includes("ass") && preview.includes("groin"), preview);
  await pickView("multiple views");
  check("multiple views 를 끄면 부딪치는 칩은 앞쪽만 남음", (await viewOn("front view")) && !(await viewOn("from behind")));
  await pickView("자동");
  check("시점 자동으로 해제", await viewOn("자동"));

  // 빈 줄: 입력창에 빈 문단으로 들어가고, 빈 줄만 달라도 적용할 수 있다
  const gapArrangement = { format: "tagorg-arrangement", version: 1, label: "빈 줄 시험", rows: [["who"], ["rest"]],
    chunks: { who: { members: ["subject/count"], gap_after: true }, rest: { members: ["*"] } } };
  await evaluate(sw, `chrome.storage.local.set({ imported_arrangements: [{ id: "gap", label: "빈 줄 시험", data: ${JSON.stringify(gapArrangement)} }] })`);
  await sleep(300);
  const pickPreset = async (id) => {
    await inFrame(frame, `(() => { const el = document.getElementById('preset'); el.value = ${JSON.stringify(id)}; el.dispatchEvent(new Event('change')); })()`);
    await sleep(200);
  };
  await type(0, "1girl, smile");
  await sleep(300);
  await pickPreset("imported:gap");
  check("빈 줄이 든 결과는 적용할 수 있음", await inFrame(frame, "!document.getElementById('apply').disabled"), await previewText(frame));
  check("결과 글에 빈 줄", (await previewText(frame)) === "1girl,\n\nsmile", await previewText(frame));
  check("색 미리보기에 빈 줄 자리", await inFrame(frame, "(() => { const line = document.querySelector('#chips .line:empty'); return Boolean(line) && line.getBoundingClientRect().height > 0; })()"));
  await clickInFrame(frame, "document.getElementById('apply')");
  await waitFor(() => ev(`${VISIBLE}[0].querySelectorAll('p').length === 3`), "빈 문단 적용");
  check("입력창에 빈 문단이 들어감", await ev(`!${VISIBLE}[0].querySelectorAll('p')[1].textContent.trim()`), await ev(`${VISIBLE}[0].innerHTML`));
  await waitFor(() => inFrame(frame, "document.getElementById('apply').textContent === '정렬됨'"), "빈 줄 적용 뒤 정렬됨");
  await pickPreset("layout:default");
  check("빈 줄이 없는 배열로 바꾸면 다시 적용할 수 있음", await inFrame(frame, "!document.getElementById('apply').disabled"), await boxText(0));
  await closePanel();
  await evaluate(sw, "chrome.storage.local.remove(['imported_arrangements', 'nai_boxes', 'nai_editing'])");

  await type(0, WORD_PROMPT);
  await sleep(700);
  let edit = await openEditor();
  await clickInFrame(edit, "document.getElementById('edit_toggle')");
  await waitFor(() => inFrame(edit, "document.querySelectorAll('.bmk-to-chunk').length > 3 && document.querySelectorAll('.bmk-to-tok').length > 3"), "청크와 미리보기");
  check("배열 편집을 열면 청크가 보임", await inFrame(edit, "!document.getElementById('editor').hidden && document.getElementById('preview').hidden"));
  check("넓으면 청크와 미리보기를 좌우로", await inFrame(edit, "document.querySelector('.bmk-to-body').classList.contains('side')"));
  check("저장은 바꾸기 전에는 꺼짐", await inFrame(edit, `${barButton("저장")}.disabled`));
  await screenshot("nai_editor.png");

  const before3 = await previewText(edit);
  const MOVED = "upper body";
  const topEdge = async () => {
    await inFrame(edit, "document.querySelector('.bmk-to-canvas').scrollTop = 0");
    const at = await inFrame(edit, "(() => { const r = document.querySelector('.bmk-to-row').getBoundingClientRect(); return [r.x + 40, r.y + 1]; })()");
    const off = await frameOffset();
    return [off[0] + at[0], off[1] + at[1]];
  };
  const moveToTop = async (tag) => {
    const to = await topEdge();
    const from = await pagePoint(edit, chunkOfTag(tag));
    await dragTo(from, to);
  };
  await moveToTop(MOVED);
  await waitFor(async () => (await firstLine(edit)).startsWith(MOVED), "끌기 반영", 3000).catch(() => {});
  check("청크를 맨 위로 끌면 첫 줄이 됨", (await firstLine(edit)).startsWith(MOVED), await previewText(edit));
  check("미리보기 칸도 따라옴", await waitFor(() => inFrame(edit, "document.querySelector('.bmk-to-preview .bmk-to-line')?.textContent.startsWith('upper body')"), "미리보기", 3000).catch(() => false));
  check("수정됨 표시", (await stateOf(edit)).includes("수정됨"), await stateOf(edit));
  check("저장하지 않은 변경을 저장소에 남김", (await stored("nai_draft"))?.arr?.includes("chunks") === true);

  await clickInFrame(edit, barButton("되돌리기"));
  await sleep(150);
  check("되돌리기 단추", squeeze(await previewText(edit)) === squeeze(before3), await previewText(edit));
  await clickInFrame(edit, barButton("다시 실행"));
  await sleep(150);
  check("다시 실행 단추", (await firstLine(edit)).startsWith(MOVED));
  await clickInFrame(edit, "document.querySelector('.bmk-to-status')");
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);
  check("패널 안 Ctrl+Z 는 배열을 되돌림", squeeze(await previewText(edit)) === squeeze(before3) && squeeze(await boxText(0)) === squeeze(WORD_PROMPT));
  await key("y", "KeyY", 89, { modifiers: 2 });
  await sleep(150);
  check("패널 안 Ctrl+Y 는 다시 실행", (await firstLine(edit)).startsWith(MOVED));

  // 편집기 밖에서 놓아도 끌기가 끝난다
  const outside = await ev("(() => { const r = document.getElementById('tagorg-nai-panel').getBoundingClientRect(); return [r.left - 30, r.top + 200]; })()");
  await dragTo(await pagePoint(edit, chunkOfTag("smile")), outside);
  const back = await pagePoint(edit, "document.querySelector('.bmk-to-status')");
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: back[0], y: back[1] }, page);
  await sleep(150);
  check("패널 밖에서 놓아도 끌기가 끝남", await inFrame(edit, "!document.querySelector('.bmk-to-ghost')"));
  if ((await stored("nai_draft"))?.arr && !(await firstLine(edit)).startsWith(MOVED)) await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(100);

  // 저장하지 않은 배열로 적용: 입력창에 들어가고, 입력창의 Ctrl+Z 는 입력창을 되돌린다
  const previewDraft = await previewText(edit);
  await clickInFrame(edit, "document.getElementById('apply')");
  await sleep(500);
  check("편집 중인 배열로 적용", squeeze(await boxText(0)) === squeeze(previewDraft) && (await boxText(0)).startsWith(MOVED), await boxText(0));
  await undo();
  await sleep(200);
  check("입력창 Ctrl+Z 는 배열을 건드리지 않음", squeeze(await boxText(0)) === squeeze(WORD_PROMPT) && (await firstLine(edit)).startsWith(MOVED), await boxText(0));

  await clearToast();
  await clickExpr(part("main"));
  await toastHas("개 정렬");
  check("버튼은 저장한 프리셋을 씀(편집 중인 배열 아님)", !(await boxText(0)).startsWith(MOVED), await boxText(0));
  await undo();
  await sleep(200);

  // 입력창을 옮겨도 편집 중인 배열은 그대로
  await type(1, "lowres, upper body");
  await waitFor(() => inFrame(edit, "document.querySelector('#boxes button[aria-pressed=true]')?.textContent === 'UC'"), "UC 로", 3000).catch(() => {});
  check("입력창을 옮겨도 편집 중인 배열 유지", (await firstLine(edit)).startsWith(MOVED) && (await stateOf(edit)).includes("수정됨"), await previewText(edit));
  await type(0, WORD_PROMPT);
  await sleep(300);

  // 우클릭 메뉴: 숨기기
  await rightClick(await pagePoint(edit, chunkOfTag("smile")));
  check("우클릭 메뉴가 뜸", await inFrame(edit, "!!document.querySelector('.bmk-to-menu')"));
  await screenshot("nai_editor_menu.png");
  await clickInFrame(edit, menuEntry("숨기기"));
  await sleep(200);
  check("숨기면 그 태그가 빠짐", !(await previewText(edit)).includes("smile"), await previewText(edit));
  check("빠진 태그는 흐리게 남음", await inFrame(edit, "[...document.querySelectorAll('.bmk-to-tok.rm')].some((t) => t.textContent === 'smile')"));
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);

  // 우클릭 메뉴: 빈 줄
  await rightClick(await pagePoint(edit, chunkOfTag("smile")));
  await clickInFrame(edit, menuEntry("아래에 빈 줄 넣기"));
  await sleep(200);
  check("청크 아래에 빈 줄 띠가 보임", await inFrame(edit, "!!document.querySelector('.bmk-to-gap')"));
  check("빈 줄이 결과에 들어감", (await previewText(edit)).includes("\n\n"), await previewText(edit));
  check("미리보기 칸에 빈 줄 자리", await inFrame(edit, "(() => { const line = document.querySelector('.bmk-to-preview .bmk-to-line:empty'); return Boolean(line) && line.getBoundingClientRect().height > 0; })()"));
  await screenshot("nai_editor_gap.png");
  await rightClick(await pagePoint(edit, "document.querySelector('.bmk-to-gap')"));
  await clickInFrame(edit, menuEntry("빈 줄 빼기"));
  await sleep(200);
  check("띠를 우클릭해 빈 줄을 뺌", await inFrame(edit, "!document.querySelector('.bmk-to-gap')") && !(await previewText(edit)).includes("\n\n"), await previewText(edit));
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(100);
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);

  // 줄째 옮기기와 구석 +
  const rowOrder = () => inFrame(edit, "[...document.querySelectorAll('.bmk-to-row')].map((r) => r.querySelector('.bmk-to-chunk').dataset.cid)");
  const gapRows = () => inFrame(edit, "[...document.querySelectorAll('.bmk-to-row')].flatMap((r, i) => (r.querySelector('.bmk-to-gap') ? [i] : []))");
  const rowGutter = (n) => `document.querySelectorAll('.bmk-to-row')[${n}].querySelector('.bmk-to-gutter')`;
  const gapTool = "document.querySelector('.bmk-to-tool[data-act=gap]')";
  const aboveRow = async (n) => {
    const at = await inFrame(edit, `(() => { const r = document.querySelectorAll('.bmk-to-row')[${n}].getBoundingClientRect(); return [r.x + 12, r.y - 2]; })()`);
    const off = await frameOffset();
    return [off[0] + at[0], off[1] + at[1]];
  };
  const order0 = await rowOrder();
  await dragTo(await pagePoint(edit, rowGutter(0)), await aboveRow(2));
  const order1 = await rowOrder();
  check("줄 번호를 끌면 줄째 옮겨짐", order1[0] === order0[1] && order1[1] === order0[0] && order1.length === order0.length, order1);
  check("줄을 놓으면 끌기 표시가 사라짐", await inFrame(edit, "!document.querySelector('.bmk-to-ghost') && !document.querySelector('.bmk-to-row.dragging')"));
  await screenshot("nai_editor_row_moved.png");
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);
  check("줄 옮기기도 되돌려짐", JSON.stringify(await rowOrder()) === JSON.stringify(order0));
  await dragTo(await pagePoint(edit, gapTool), await aboveRow(2));
  await sleep(200);
  check("구석 + 를 끌어 놓으면 그 줄 사이에 빈 줄", JSON.stringify(await gapRows()) === "[1]", await gapRows());
  check("끌어 넣은 빈 줄이 결과에 들어감", (await previewText(edit)).includes("\n\n"), await previewText(edit));
  await screenshot("nai_editor_gap_dragged.png");
  await dragTo(await pagePoint(edit, "document.querySelector('.bmk-to-gap')"), await aboveRow(1));
  await sleep(200);
  check("빈 줄 띠를 끌면 다른 줄 사이로 옮겨짐", JSON.stringify(await gapRows()) === "[0]", await gapRows());
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(100);
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);
  check("빈 줄 끌기도 되돌려짐", JSON.stringify(await gapRows()) === "[]", await gapRows());
  const rowsNow = (await rowOrder()).length;
  await clickInFrame(edit, gapTool);
  await sleep(200);
  check("구석 + 를 누르면 맨 아래에 빈 줄", JSON.stringify(await gapRows()) === JSON.stringify([rowsNow - 1]), await gapRows());
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(150);
  await clickInFrame(edit, "document.querySelector('.bmk-to-tool[data-act=help]')");
  await sleep(100);
  check("? 를 누르면 도움말", await inFrame(edit, "document.querySelector('.bmk-to-help')?.textContent.includes('줄 번호')"));
  await screenshot("nai_editor_help.png");
  await clickInFrame(edit, "document.querySelector('.bmk-to-status')");
  await sleep(100);
  check("밖을 누르면 도움말이 닫힘", await inFrame(edit, "!document.querySelector('.bmk-to-help')"));

  // 펼치기와 멤버 빼내기
  const clothes = chunkOfTag("white shirt");
  const clothesAt = await pagePoint(edit, clothes);
  await click(clothesAt[0], clothesAt[1]);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: clothesAt[0], y: clothesAt[1], button: "left", clickCount: 2 }, page);
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: clothesAt[0], y: clothesAt[1], button: "left", clickCount: 2 }, page);
  await sleep(200);
  const memberCount = await inFrame(edit, "document.querySelectorAll('.bmk-to-member').length");
  check("더블클릭으로 펼침", memberCount > 0, memberCount);
  if (memberCount) {
    const chunksBefore = await inFrame(edit, "document.querySelectorAll('.bmk-to-chunk').length");
    const from = await pagePoint(edit, "document.querySelector('.bmk-to-member')");
    // 보이는 줄 중 청크가 없는 오른쪽 빈자리
    const blank = await inFrame(edit, `(() => {
      const c = document.querySelector('.bmk-to-canvas').getBoundingClientRect();
      for (const row of document.querySelectorAll('.bmk-to-row')) {
        const r = row.getBoundingClientRect();
        const x = c.right - 14;
        const y = r.y + r.height / 2;
        if (y > c.top + 8 && y < c.bottom - 8 && !document.elementFromPoint(x, y)?.closest('.bmk-to-chunk')) return [x, y];
      }
      return null;
    })()`);
    const off = await frameOffset();
    await dragTo(from, [off[0] + blank[0], off[1] + blank[1]]);
    check("멤버를 밖으로 끌면 새 청크", (await inFrame(edit, "document.querySelectorAll('.bmk-to-chunk').length")) === chunksBefore + 1);
    await key("z", "KeyZ", 90, { modifiers: 2 });
    await sleep(150);
  }

  // 커스텀 청크
  await clickInFrame(edit, "document.querySelector('[data-act=custom]')");
  check("커스텀 청크 대화상자", await inFrame(edit, "!!document.querySelector('.bmk-to-dialog')"));
  await screenshot("nai_editor_dialog.png");
  await inFrame(edit, `(() => {
    const fields = document.querySelectorAll('.bmk-to-dialog input[type=text], .bmk-to-dialog textarea');
    fields[0].value = '내 묘사';
    fields[1].value = 'long hair';
    fields[2].value = 're:(';
  })()`);
  await clickInFrame(edit, dialogButton("확인"));
  await sleep(200);
  check("틀린 패턴은 대화상자에서 알림", (await inFrame(edit, "document.querySelector('.bmk-to-dialog .err')?.textContent || ''")).length > 0);
  await inFrame(edit, "document.querySelectorAll('.bmk-to-dialog textarea')[1].value = ''");
  await clickInFrame(edit, dialogButton("확인"));
  await sleep(250);
  check("커스텀 청크가 맨 위에 생김", (await firstLine(edit)).startsWith("long hair"), await previewText(edit));

  // 추정 규칙
  await clickInFrame(edit, "document.querySelector('[data-act=more]')");
  await clickInFrame(edit, menuEntry("추정 규칙"));
  await inFrame(edit, "document.querySelector('.bmk-to-dialog textarea').value = 'zzz * => 없는분류'");
  await clickInFrame(edit, dialogButton("확인"));
  await sleep(200);
  check("틀린 추정 규칙은 알림", (await inFrame(edit, "document.querySelector('.bmk-to-dialog .err')?.textContent || ''")).length > 0);
  await inFrame(edit, "document.querySelector('.bmk-to-dialog textarea').value = 'zzz * => clothes'");
  await clickInFrame(edit, dialogButton("확인"));
  await sleep(200);
  check("추정 규칙이 배열에 들어감", JSON.parse((await stored("nai_draft")).arr).guess?.[0]?.pattern === "zzz *", (await stored("nai_draft")).arr.slice(0, 80));

  // 저장: 레이아웃에서 시작했으므로 이름을 묻는다
  await clickInFrame(edit, barButton("저장"));
  await sleep(150);
  check("레이아웃을 고쳐 저장하면 이름을 물음", await inFrame(edit, "!!document.querySelector('.bmk-to-dialog')"));
  await inFrame(edit, "document.querySelector('.bmk-to-dialog input[type=text]').value = '시험 배열'");
  await clickInFrame(edit, dialogButton("저장"));
  await sleep(400);
  let imported = await stored("imported_arrangements");
  check("프리셋으로 저장", imported?.[0]?.label === "시험 배열" && imported[0].data.label === "시험 배열", imported?.map((x) => x.label));
  check("저장 뒤 수정됨 표시가 사라짐", !(await stateOf(edit)).includes("수정됨") && (await stateOf(edit)).includes("시험 배열"), await stateOf(edit));
  check("저장 뒤 저장소의 임시본이 사라짐", (await stored("nai_draft")) === null);
  check("저장 안내", (await inFrame(edit, "document.getElementById('notice').textContent")).includes("저장했습니다"));
  check("목록에서 저장한 배열이 골라짐", (await inFrame(edit, "document.getElementById('preset').selectedOptions[0].textContent")) === "시험 배열");
  check("입력창 묶음의 프리셋으로 기억", (await stored("nai_boxes"))?.prompt?.preset === `imported:${imported[0].id}`, await stored("nai_boxes"));

  const savedPreview = await previewText(edit);
  await clearToast();
  await clickExpr(part("main"));
  await toastHas("시험 배열");
  check("저장한 배열을 버튼이 씀", squeeze(await boxText(0)) === squeeze(savedPreview), await boxText(0));
  await undo();
  await sleep(200);

  // 내 배열은 저장하면 그 자리에 덮어쓴다
  await moveToTop("1girl");
  await sleep(150);
  await clickInFrame(edit, barButton("저장"));
  await sleep(300);
  imported = await stored("imported_arrangements");
  check("내 배열은 묻지 않고 덮어씀", imported.length === 1 && !(await inFrame(edit, "!!document.querySelector('.bmk-to-dialog')")) && !(await stateOf(edit)).includes("수정됨"), imported.length);

  // 저장하지 않고 닫아도 이어서 열림
  await moveToTop(MOVED);
  await sleep(150);
  const unsaved = await previewText(edit);
  await closePanel();
  edit = await openEditor();
  await waitFor(() => inFrame(edit, "document.querySelectorAll('.bmk-to-chunk').length > 0"), "다시 열기");
  await sleep(200);
  check("다시 열면 편집 상태와 변경이 이어짐", (await stateOf(edit)).includes("수정됨") && squeeze(await previewText(edit)) === squeeze(unsaved), { state: await stateOf(edit), text: await previewText(edit) });

  // 변경이 있을 때 다른 배열을 고르면 묻는다
  await inFrame(edit, "(() => { const el = document.getElementById('preset'); el.selectedIndex = 0; el.dispatchEvent(new Event('change')); })()");
  await sleep(150);
  check("변경이 있으면 버릴지 물음", await inFrame(edit, "!!document.querySelector('.bmk-to-dialog')"));
  await clickInFrame(edit, dialogButton("취소"));
  await sleep(150);
  check("취소하면 그대로", (await stateOf(edit)).includes("수정됨") && (await inFrame(edit, "document.getElementById('preset').selectedOptions[0].textContent")) === "시험 배열");
  await inFrame(edit, "(() => { const el = document.getElementById('preset'); el.selectedIndex = 0; el.dispatchEvent(new Event('change')); })()");
  await sleep(150);
  await clickInFrame(edit, dialogButton("버리고 열기"));
  await sleep(300);
  check("버리면 고른 배열이 열림", !(await stateOf(edit)).includes("수정됨") && (await stored("nai_draft")) === null, await stateOf(edit));

  // JSON 보기·붙여넣기와 파일 내보내기
  await clickInFrame(edit, "document.querySelector('[data-act=more]')");
  await clickInFrame(edit, menuEntry("JSON"));
  check("JSON 대화상자에 지금 배열", (await inFrame(edit, "document.querySelector('.bmk-to-dialog textarea').value")).includes("tagorg-arrangement"));
  await inFrame(edit, "document.querySelector('.bmk-to-dialog textarea').value = '{ 틀린 JSON'");
  await clickInFrame(edit, dialogButton("적용"));
  await sleep(150);
  check("틀린 JSON 은 알림", (await inFrame(edit, "document.querySelector('.bmk-to-dialog .err').textContent")).length > 0);
  await inFrame(edit, `document.querySelector('.bmk-to-dialog textarea').value = ${JSON.stringify(JSON.stringify({ format: "tagorg-arrangement", version: 1, label: "붙인 배열", author: "시험", rows: [["all"]], chunks: { all: { members: ["*"] } } }))}`);
  await clickInFrame(edit, dialogButton("적용"));
  await sleep(250);
  check("붙여 넣은 배열이 열림", (await inFrame(edit, "document.querySelectorAll('.bmk-to-chunk').length")) === 1);
  await inFrame(edit, "(() => { const make = URL.createObjectURL; URL.createObjectURL = (blob) => { globalThis.__exported = blob; return make.call(URL, blob); }; })()");
  await clickInFrame(edit, "document.querySelector('[data-act=export]')");
  await sleep(200);
  const exported = JSON.parse(await inFrame(edit, "globalThis.__exported.text()"));
  check("파일로 내보내기(다른 툴의 키 유지)", exported.label === "붙인 배열" && exported.author === "시험" && exported.format === "tagorg-arrangement", exported);
  await key("z", "KeyZ", 90, { modifiers: 2 });
  await sleep(200);

  // 편집을 끄면 저장한 프리셋으로 돌아온다
  await clickInFrame(edit, "document.getElementById('edit_toggle')");
  await sleep(200);
  check("편집을 끄면 미리보기로 돌아옴", await inFrame(edit, "document.getElementById('editor').hidden && !document.getElementById('preview').hidden && document.querySelectorAll('#chips .token').length > 0"));
  await closePanel();
  await evaluate(sw, "chrome.storage.local.remove(['nai_boxes', 'nai_draft', 'nai_editing', 'imported_arrangements'])");
  await sleep(150);

  // ─── 캐릭터 ─────────────────────────────────────────────────────
  await clickExpr("(() => { const root = [...document.querySelectorAll('.image-gen-character-prompts')].find((e) => e.getBoundingClientRect().width > 0); return root.querySelector('.image-gen-character-prompts-header button') || root.querySelector('button'); })()");
  await sleep(300);
  await clickExpr("[...document.querySelectorAll('button')].filter((x) => x.getBoundingClientRect().width > 0).find((x) => /^female$/i.test(x.innerText.trim()))");
  await sleep(900);
  const characterIndex = await ev(`${VISIBLE}.findIndex((el) => el.closest('[class*="prompt-input-box-character-prompts"]'))`);
  check("캐릭터 입력창을 찾음", characterIndex >= 0, characterIndex);
  await type(characterIndex, "smile, 1girl, long hair, blue eyes");
  await sleep(800);   // 편집기가 바로 앞 입력과 한 번의 되돌리기로 묶지 않게
  message = await sortByButton("개 정렬");
  check("캐릭터 입력창 정렬", message.startsWith("캐릭터 1") && (await boxText(characterIndex)).startsWith("1girl"), { message, text: await boxText(characterIndex) });
  const characterSorted = await boxText(characterIndex);
  await undo();
  check("캐릭터 입력창 되돌리기", squeeze(await boxText(characterIndex)) === squeeze("smile, 1girl, long hair, blue eyes"), await boxText(characterIndex));
  await clickExpr(part("main"));
  await toastHas("개 정렬");
  await narrow();
  const narrowCharacter = await ev(`[...document.querySelectorAll('[class*="prompt-input-box-character-prompts"] .ProseMirror')].filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.innerText)`);
  check("캐릭터 입력창도 앱이 그 글을 앎", narrowCharacter.some((t) => squeeze(t) === squeeze(characterSorted)), narrowCharacter);
  await wide();
  check("Base Prompt 이름", (await ev("!!document.querySelector('.prompt-input-box-base-prompt')")));

  // ─── 탭 배치(화면을 되돌릴 단추가 탭 안에 있어 뒤쪽에서 시험한다) ─
  const tabButton = (name) => `[...document.querySelectorAll('.image-gen-prompt-main button')].find((x) => x.getBoundingClientRect().width > 0 && x.innerText.trim() === ${JSON.stringify(name)})`;
  await clickExpr("[...document.querySelectorAll('.image-gen-prompt-main')].find((e) => e.getBoundingClientRect().width > 0).children[2]?.querySelector('button')");
  await sleep(600);
  check("탭 배치에서도 버튼이 붙음", await ev("!!document.getElementById('tagorg-nai-button')?.isConnected"));
  check("탭 배치: 숨은 탭은 대상이 아님", await ev(`!${VISIBLE}.some((el) => el.closest('.prompt-input-box-undesired-content'))`));
  const headerHeight = await ev("document.getElementById('tagorg-nai-button').parentElement.parentElement.getBoundingClientRect().height");
  check("탭 배치 머리글이 두 줄로 밀리지 않음", headerHeight < 50, headerHeight);
  await screenshot("nai_tabs.png");
  await clickExpr(tabButton("Undesired Content"));
  await sleep(300);
  await type(0, "shoes, lowres");
  message = await sortByButton("개 정렬");
  check("탭을 바꾸면 그 입력창이 대상", message.startsWith("UC"), message);

  // ─── 머리글을 못 찾을 때와 끄기 ─────────────────────────────────
  await ev("document.querySelectorAll('.image-gen-prompt-main').forEach((e) => { e.classList.remove('image-gen-prompt-main'); e.classList.add('tagorg-test-main'); })");
  await sleep(300);
  check("머리글을 못 찾으면 구석에 띄움", await ev("document.getElementById('tagorg-nai-button')?.classList.contains('floating') && getComputedStyle(document.getElementById('tagorg-nai-button')).position === 'fixed'"));
  await screenshot("nai_floating.png");
  await ev("document.querySelectorAll('.tagorg-test-main').forEach((e) => { e.classList.remove('tagorg-test-main'); e.classList.add('image-gen-prompt-main'); })");
  await sleep(300);
  check("머리글이 돌아오면 다시 붙음", await ev("!!document.getElementById('tagorg-nai-button')?.closest('.image-gen-prompt-main')"));

  await evaluate(sw, "chrome.storage.local.get('settings').then((x) => chrome.storage.local.set({ settings: { ...(x.settings || {}), nai_button: false } }))");
  await sleep(300);
  check("설정으로 끄면 버튼이 사라짐", !(await ev("!!document.getElementById('tagorg-nai-button')?.isConnected")));
  await evaluate(sw, "chrome.storage.local.get('settings').then((x) => chrome.storage.local.set({ settings: { ...x.settings, nai_button: true } }))");
  await sleep(300);
  check("켜면 돌아옴", await ev("!!document.getElementById('tagorg-nai-button')?.isConnected"));

  // ─── 다른 화면 ──────────────────────────────────────────────────
  await ev("history.pushState({}, '', '/stories'); document.body.classList.toggle('tagorg-test-tick')");
  await sleep(300);
  check("이미지 화면이 아니면 버튼이 없음", !(await ev("!!document.getElementById('tagorg-nai-button')?.isConnected")));
  await ev("history.pushState({}, '', '/image'); document.body.classList.toggle('tagorg-test-tick')");
  await sleep(300);
  check("이미지 화면으로 돌아오면 다시 붙음", await ev("!!document.getElementById('tagorg-nai-button')?.isConnected"));

  // ─── 확장을 다시 불러온 뒤의 낡은 탭 ─────────────────────────────
  await evaluate(sw, "setTimeout(() => chrome.runtime.reload(), 50), 1").catch(() => {});
  await sleep(1500);
  await clearToast();
  await clickExpr(part("main"));
  message = await toastHas("새로고침");
  check("확장을 다시 불러온 탭은 새로고침 안내", message.includes("새로고침"), message);

  check("확장 코드의 예외 없음", pageErrors.length === 0, pageErrors);
} catch (e) {
  failures++;
  console.log(`FAIL 중단: ${e.message}`);
} finally {
  await browser.close();
}

console.log(failures ? `실패 ${failures}건` : "모두 통과");
process.exit(failures ? 1 : 0);
