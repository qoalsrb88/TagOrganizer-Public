// 페이지에 넣는 스크립트: 입력창의 글을 읽고 바꾼다. textarea·input 과 contenteditable(NovelAI 의 ProseMirror 등)을 다룬다.
//
// 글은 편집기가 자기 상태를 고치도록 넣는다(DOM 을 직접 고치면 ProseMirror 가 모른다). 그래야 Ctrl+Z 도 된다.
// - contenteditable: paste 이벤트(여러 줄이 문단으로 들어간다). 편집기가 받지 않으면 execCommand('insertText').
// - textarea·input: execCommand('insertText'). 안 되면 값을 바꾸고 input 이벤트(React 도 알도록 원래 setter 로).
(() => {
  if (globalThis.__tagorgField) return;

  const TEXT_INPUTS = new Set(["text", "search", "url", ""]);
  const SETTLE_MS = 60;   // 고친 선택을 편집기가 읽을 시간(selectionchange)
  const TOAST_MS = 5000;
  const TOAST_ID = "tagorg-toast-host";

  let target = null;

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const squeeze = (text) => text.replace(/\s+/g, "");

  function deepActive() {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return el;
  }

  const isTextControl = (el) =>
    el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && TEXT_INPUTS.has(el.type));

  function editableRoot(node) {
    let el = node instanceof Element ? node : node?.parentElement;
    if (!el || !el.isContentEditable) return null;
    while (el.parentElement && el.parentElement.isContentEditable) el = el.parentElement;
    return el;
  }

  function capture() {
    if (!document.hasFocus()) return null;
    const active = deepActive();
    if (isTextControl(active) && !active.readOnly && !active.disabled) {
      const { selectionStart: start, selectionEnd: end, value } = active;
      const partial = start !== end && !(start === 0 && end === value.length);
      target = { el: active, kind: "control", partial };
      return { text: partial ? value.slice(start, end) : value, partial, singleLine: active instanceof HTMLInputElement };
    }
    const root = editableRoot(getSelection()?.anchorNode) || editableRoot(active);
    return root ? captureEditable(root) : null;
  }

  // contenteditable 하나를 대상으로 잡는다. 그 안에 고른 글이 있으면 일부 선택이다.
  function captureEditable(root) {
    const selection = getSelection();
    const selected = selection.rangeCount && !selection.isCollapsed && root.contains(selection.focusNode)
      ? selection.toString() || selection.getRangeAt(0).toString() : "";
    const whole = root.innerText;
    const partial = Boolean(selected.trim()) && squeeze(selected) !== squeeze(whole);
    target = { el: root, kind: "editable", partial };
    return { text: partial ? selected : whole, partial };
  }

  // 일부 선택이 정렬할 것이 없는 조각일 때 입력창 전체로 넓힌다.
  function selectAll() {
    target.partial = false;
    return { text: target.kind === "control" ? target.el.value : target.el.innerText, partial: false };
  }

  function currentText() {
    const { el, kind, partial } = target;
    if (kind === "control") return partial ? el.value.slice(el.selectionStart, el.selectionEnd) : el.value;
    if (!partial) return el.innerText;
    const selection = getSelection();
    return selection.toString() || (selection.rangeCount ? selection.getRangeAt(0).toString() : "");
  }

  async function selectTarget() {
    const { el, kind, partial } = target;
    el.focus();
    if (partial) return;
    if (kind === "control") {
      el.select();
    } else {
      const range = document.createRange();
      range.selectNodeContents(el);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    await wait(SETTLE_MS);
  }

  function setControlValue(el, text) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const { selectionStart: start, selectionEnd: end, value } = el;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value.slice(0, start) + text + value.slice(end));
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
  }

  function insertEditable(el, text) {
    const data = new DataTransfer();
    data.setData("text/plain", text);
    const paste = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
    el.dispatchEvent(paste);
    if (!paste.defaultPrevented) document.execCommand("insertText", false, text);
  }

  // expected: capture 때 읽은 글. 그 사이 입력창이 바뀌었으면 건드리지 않는다.
  async function apply(expected, text) {
    if (!target?.el?.isConnected) return { ok: false, reason: "입력창이 사라져서 바꾸지 않았습니다" };
    if (squeeze(currentText()) !== squeeze(expected)) {
      return { ok: false, reason: "그 사이 글이 바뀌어서 바꾸지 않았습니다. 다시 실행하세요." };
    }
    const { el, kind } = target;
    await selectTarget();
    if (kind === "control") {
      if (!document.execCommand("insertText", false, text)) setControlValue(el, text);
    } else {
      insertEditable(el, text);
    }
    await wait(SETTLE_MS);
    const now = kind === "control" ? el.value : el.innerText;
    if (!squeeze(now).includes(squeeze(text))) {
      return { ok: false, reason: "이 입력창이 글 교체를 받지 않았습니다" };
    }
    return { ok: true };
  }

  // 페이지 CSP 에 걸리지 않도록 style 속성만 쓴다. 글은 textContent 로만 넣는다.
  function toast(message, kind = "ok") {
    document.getElementById(TOAST_ID)?.remove();
    const host = document.createElement("div");
    host.id = TOAST_ID;
    host.style.cssText = "all:initial;position:fixed;left:50%;bottom:24px;transform:translateX(-50%);"
      + "z-index:2147483647;pointer-events:none;";
    const box = document.createElement("div");
    box.textContent = message;
    box.style.cssText = "max-width:min(640px,90vw);padding:10px 14px;border-radius:8px;font:13px/1.5 system-ui,sans-serif;"
      + "color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.35);overflow-wrap:anywhere;white-space:pre-line;"
      + `background:${kind === "warn" ? "#92400e" : "#1f2937"};`;
    host.attachShadow({ mode: "open" }).append(box);
    document.documentElement.append(host);
    setTimeout(() => host.remove(), TOAST_MS);
  }

  globalThis.__tagorgField = { capture, captureEditable, selectAll, apply, toast };
})();
