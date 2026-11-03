// 실제 크롬 시험 공용: 임시 프로필의 헤드리스 크롬을 CDP pipe 로 움직인다.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const CDP_TIMEOUT_MS = 30000;

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function waitFor(fn, what, ms = 20000) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) throw new Error(`시간 초과: ${what}`);
    await sleep(100);
  }
}

export function launchChrome() {
  const tmp = mkdtempSync(join(tmpdir(), "tagorg-e2e-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-pipe", "--enable-unsafe-extension-debugging",
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1600,1000", `--user-data-dir=${join(tmp, "profile")}`, "about:blank"],
  { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });

  let nextId = 1;
  const pending = new Map();
  const listeners = [];
  let inbox = Buffer.alloc(0);
  chrome.stdio[4].on("data", (chunk) => {
    inbox = Buffer.concat([inbox, chunk]);
    for (let end = inbox.indexOf(0); end >= 0; end = inbox.indexOf(0)) {
      const msg = JSON.parse(inbox.subarray(0, end).toString("utf-8"));
      inbox = inbox.subarray(end + 1);
      if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else {
        for (const listener of listeners) listener(msg);
      }
    }
  });

  function send(method, params = {}, sessionId) {
    const id = nextId++;
    chrome.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
    return new Promise((resolve, reject) => {
      // 응답이 오지 않으면 시험이 멈추지 않고 실패하게 한다
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP 응답 없음: ${method}`));
      }, CDP_TIMEOUT_MS);
      pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });
    });
  }

  async function evaluate(sessionId, expression) {
    const { result, exceptionDetails } = await send("Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, userGesture: true }, sessionId);
    if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
    return result.value;
  }

  async function attach(targetId) {
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    await send("Runtime.enable", {}, sessionId);
    return sessionId;
  }

  async function close() {
    await send("Browser.close").catch(() => {});
    chrome.kill();
    await sleep(500);
    rmSync(tmp, { recursive: true, force: true, maxRetries: 3 });
  }

  return { send, evaluate, attach, close, listeners, tmp };
}
