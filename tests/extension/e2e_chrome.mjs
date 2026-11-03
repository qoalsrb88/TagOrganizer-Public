// 실제 크롬으로 확장을 시험한다(수동 실행 — 인터넷의 novelai.net 을 연다. 로그인·생성 요청은 하지 않는다).
//
//   python tagorg.py extension --no-build
//   node tests/extension/e2e_chrome.mjs
//
// 임시 프로필의 헤드리스 크롬에 임시 사본을 올린다. 네이티브 오른쪽 클릭 메뉴는 CDP 로 누를 수 없어서, 사본에는
// host_permissions 를 더하고 메뉴가 부르는 run() 을 서비스 워커에서 직접 부른다(메뉴 등록은 따로 확인).
import { cpSync, existsSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launchChrome, sleep, waitFor } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const NAI_URL = "https://novelai.net/image";

const browser = launchChrome();
const { send, evaluate, attach } = browser;
const copy = join(browser.tmp, "ext");
cpSync(join(ROOT, "extension"), copy, { recursive: true });
if (!existsSync(join(copy, "data", "tagpack.json.gz"))) throw new Error("먼저 python tagorg.py extension 을 실행하세요");
const manifest = JSON.parse(readFileSync(join(copy, "manifest.json"), "utf-8"));
manifest.host_permissions = ["<all_urls>"];
writeFileSync(join(copy, "manifest.json"), JSON.stringify(manifest));
appendFileSync(join(copy, "background.js"), "\nglobalThis.__test = { run, rebuildMenus, listPresets, loadPack };\n");

let failures = 0;
function check(name, ok, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `  ${JSON.stringify(detail)}`}`);
}

const LOCAL_PAGE = `<!doctype html><meta charset=utf-8><title>t</title>
<textarea id=area>smile, 1girl, shoes, hatsune miku</textarea>
<input id=line value="smile, 1girl">
<div id=rich contenteditable=true><p>smile, 1girl, blue hair</p></div>
<script>window.events=[];area.addEventListener('input',e=>events.push(e.inputType));</script>`;

const server = createServer((_, res) => {
  res.setHeader("content-type", "text/html; charset=utf-8");
  res.end(LOCAL_PAGE);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const LOCAL_URL = `http://127.0.0.1:${server.address().port}/`;

try {
  const { id: extId } = await send("Extensions.loadUnpacked", { path: copy });
  console.log("확장 id", extId);
  const worker = await waitFor(async () => {
    const { targetInfos } = await send("Target.getTargets");
    return targetInfos.find((t) => t.type === "service_worker" && t.url.includes(extId));
  }, "서비스 워커");
  const sw = await attach(worker.targetId);
  await waitFor(() => evaluate(sw, "typeof globalThis.__test === 'object' && typeof chrome === 'object'"), "서비스 워커 준비");

  const openTab = async (url) => {
    const { targetId } = await send("Target.createTarget", { url });
    const session = await attach(targetId);
    await waitFor(() => evaluate(session, "location.href !== 'about:blank' && document.readyState === 'complete'"), url);
    return session;
  };
  const runInSw = (urlPattern, presetId) => evaluate(sw, `(async () => {
    const [tab] = await chrome.tabs.query({ url: ${JSON.stringify(urlPattern)} });
    await globalThis.__test.run(tab, 0, ${JSON.stringify(presetId)});
  })()`);

  // 메뉴와 프리셋
  const presets = await evaluate(sw, "globalThis.__test.listPresets().then((items) => items.map((p) => p.id))");
  check("프리셋 목록에 기본 레이아웃", presets.includes("layout:default"), presets);
  await evaluate(sw, "globalThis.__test.rebuildMenus()");
  check("메뉴 다시 만들기 오류 없음", (await evaluate(sw, "chrome.runtime.lastError ? chrome.runtime.lastError.message : ''")) === "");

  // 성능: 서비스 워커가 처음 팩을 읽고 정렬하는 시간
  const elapsed = await evaluate(sw, `(async () => {
    const t = performance.now();
    await globalThis.__test.loadPack();
    return performance.now() - t;
  })()`);
  console.log(`팩 읽기 ${Math.round(elapsed)}ms`);

  // 일반 페이지: textarea·input·contenteditable
  const local = await openTab(LOCAL_URL);
  await send("Page.bringToFront", {}, local);
  await send("Emulation.setFocusEmulationEnabled", { enabled: true }, local);
  await evaluate(local, "area.focus(); area.select()");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  const areaValue = await evaluate(local, "area.value");
  check("textarea 전체 정렬", areaValue.startsWith("1girl,\nhatsune miku") && areaValue.includes("smile"), areaValue);
  check("textarea input 이벤트", (await evaluate(local, "events.length")) > 0);
  await evaluate(local, "document.execCommand('undo')");
  check("textarea 되돌리기", (await evaluate(local, "area.value")) === "smile, 1girl, shoes, hatsune miku", await evaluate(local, "area.value"));

  await evaluate(local, "area.value = 'keep, smile, 1girl, tail'; area.focus(); area.setSelectionRange(6, 18)");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  const partial = await evaluate(local, "area.value");
  check("textarea 일부 선택: 앞뒤를 지킴", partial.startsWith("keep, 1girl,\nsmile") && partial.endsWith(", tail"), partial);

  await evaluate(local, "area.value = '1.2::a, smile, 1girl ::'; area.focus(); area.setSelectionRange(0, 13)");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  check("잘린 강조는 바꾸지 않음", (await evaluate(local, "area.value")) === "1.2::a, smile, 1girl ::", await evaluate(local, "area.value"));
  const warned = await evaluate(local, "document.getElementById('tagorg-toast-host')?.shadowRoot.textContent || ''");
  check("잘린 강조 알림", warned.includes("잘렸습니다"), warned);

  await evaluate(local, "area.value = 'smile, 1girl'; area.focus(); area.setSelectionRange(0, 5)");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  check("조각 하나만 고르면 전체 정렬", (await evaluate(local, "area.value")).startsWith("1girl"), await evaluate(local, "area.value"));

  await evaluate(local, "line.focus(); line.select()");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  check("한 줄 입력칸", (await evaluate(local, "line.value")).startsWith("1girl"), await evaluate(local, "line.value"));

  await evaluate(local, "rich.focus(); getSelection().selectAllChildren(rich)");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  const richText = await evaluate(local, "rich.innerText");
  check("일반 contenteditable", richText.startsWith("1girl") && richText.includes("blue hair"), richText);

  const toast = await evaluate(local, "document.getElementById('tagorg-toast-host')?.shadowRoot.textContent || ''");
  check("정렬 알림", toast.includes("개 정렬"), toast);

  // 한 줄 입력칸에는 줄바꿈 없이
  await evaluate(local, "line.value = 'smile, hatsune miku, 1girl'; line.focus(); line.select()");
  await runInSw(`${LOCAL_URL}*`, "layout:default");
  check("한 줄 입력칸은 쉼표로 이음", (await evaluate(local, "line.value")) === "1girl, hatsune miku, smile", await evaluate(local, "line.value"));

  // 패널: 붙여넣어 정렬, 설정 저장, 배열 가져오기
  const panel = await openTab(`chrome-extension://${extId}/panel/panel.html`);
  await waitFor(() => evaluate(panel, "Boolean(document.getElementById('about')?.textContent)"), "패널");
  await evaluate(panel, `(() => {
    const input = document.getElementById("input");
    input.value = "smile, 1girl, hatsune miku";
    document.getElementById("run").click();
  })()`);
  await waitFor(() => evaluate(panel, "document.getElementById('output').value !== ''"), "패널 정렬");
  check("패널 정렬", (await evaluate(panel, "document.getElementById('output').value")).startsWith("1girl"));
  await evaluate(panel, `(() => {
    const el = document.getElementById("max_rating");
    el.value = "s";
    el.dispatchEvent(new Event("change"));
  })()`);
  await sleep(200);
  check("패널 설정 저장", (await evaluate(sw, "chrome.storage.local.get('settings').then((x) => x.settings.max_rating)")) === "s");
  await evaluate(panel, `(() => {
    const el = document.getElementById("max_rating");
    el.value = "e";
    el.dispatchEvent(new Event("change"));
    document.getElementById("import_json").value = JSON.stringify({ format: "tagorg-arrangement", version: 1, label: "가져온 시험",
      rows: [["a"]], chunks: { a: { members: ["*"] } } });
    document.getElementById("import").click();
  })()`);
  await waitFor(() => evaluate(panel, "document.getElementById('imported').textContent.includes('가져온 시험')"), "배열 가져오기");
  await sleep(200);
  check("가져온 배열이 프리셋에 보임", (await evaluate(sw, "globalThis.__test.listPresets().then((items) => items.map((p) => p.label))")).includes("가져온 시험"));
  await evaluate(panel, `(() => {
    document.getElementById("import_json").value = "{ 틀린 JSON";
    document.getElementById("import").click();
  })()`);
  await waitFor(() => evaluate(panel, "document.getElementById('import_status').classList.contains('warn')"), "가져오기 오류 표시");
  check("틀린 JSON 은 알림", true);
  if (process.env.E2E_SHOT) {
    const { data } = await send("Page.captureScreenshot", { format: "png" }, panel);
    writeFileSync(process.env.E2E_SHOT, Buffer.from(data, "base64"));
  }

  // NovelAI (로그인 없이 보이는 입력창)
  const nai = await openTab(NAI_URL);
  await send("Page.bringToFront", {}, nai);
  await send("Emulation.setFocusEmulationEnabled", { enabled: true }, nai);
  await waitFor(() => evaluate(nai, "!!document.querySelector('.ProseMirror')"), "NovelAI 입력창");
  const naiModel = "document.querySelector('.ProseMirror').pmViewDesc.node";
  const typeNai = (text) => evaluate(nai, `(async () => {
    const el = document.querySelector('.ProseMirror');
    el.focus();
    getSelection().selectAllChildren(el);
    await new Promise((r) => setTimeout(r, 100));
    document.execCommand('delete');
    document.execCommand('insertText', false, ${JSON.stringify(text)});
    await new Promise((r) => setTimeout(r, 300));
  })()`);
  const readNai = () => evaluate(nai, `(() => {
    const doc = ${naiModel}; const out = [];
    doc.forEach((node) => out.push(node.textContent));
    return out;
  })()`);

  await typeNai("smile, 1girl, shoes, hatsune miku, 1.2::long hair ::, {{sky}}");
  await evaluate(nai, "getSelection().removeAllRanges(); document.querySelector('.ProseMirror').focus()");
  await runInSw("https://novelai.net/*", "layout:default");
  const whole = await readNai();
  console.log("NovelAI 전체:", whole);
  check("NovelAI 커서만 있을 때 전체 정렬", whole[0] === "1girl," && whole.length > 2, whole);
  check("NovelAI 문법 유지", whole.join("\n").includes("1.2::long hair ::") && whole.join("\n").includes("{{sky}}"), whole);

  await evaluate(nai, "getSelection().removeAllRanges(); document.querySelector('.ProseMirror').focus()");
  await typeNai("keep, smile, 1girl, tail");
  await evaluate(nai, `(async () => {
    const el = document.querySelector('.ProseMirror');
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const node = walker.nextNode();
    const range = document.createRange();
    range.setStart(node, 6);
    range.setEnd(node, 18);
    const s = getSelection();
    s.removeAllRanges();
    s.addRange(range);
    await new Promise((r) => setTimeout(r, 150));
  })()`);
  await runInSw("https://novelai.net/*", "layout:default");
  const part = await readNai();
  console.log("NovelAI 일부:", part);
  check("NovelAI 일부 선택: 앞뒤를 지킴", part.join("\n") === "keep, 1girl,\nsmile, tail", part);

  // 시험 글을 지운다
  await typeNai("");
  check("시험 글 지움", (await readNai()).join("") === "");
} finally {
  server.close();
  await browser.close();
}

console.log(failures ? `실패 ${failures}건` : "모두 통과");
process.exit(failures ? 1 : 0);
