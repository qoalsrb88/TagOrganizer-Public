// 퀵 세팅의 그림 조작: 크롭 틀(위아래 손잡이)과 방향·높이 다이얼. 칩과 같은 덮어쓰기 값을 다룬다.
// 그림은 고른 값을 갖지 않고 update 로 받은 값을 그린다. 끄는 동안의 각도만 잠깐 들고 있다가, 태그가 바뀔 때만
// onFraming(tag)·onView(tags) 로 알린다(빈 값은 자동). 어떤 태그를 맡는지는 palette 의 framing_frame·view_dial 이 정한다.
// import 만으로는 아무 일도 하지 않는다.

import { viewToggle } from "./model.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const AXES = ["direction", "height"];
const round = (v) => Math.round(v * 100) / 100;

// ─── 계산(DOM 없음) ───────────────────────────────────────────────

/** 그림이 맡는 태그: {framing: 틀 범위가 있는 크롭 태그, view: 다이얼 구간의 시점 태그}. 퀵 목록 순서. */
export function stageCovers(palette) {
  const frames = palette.framing_frame ?? {};
  const dialTags = new Set(AXES.flatMap((axis) => (palette.view_dial?.[axis] ?? []).map((zone) => zone.tag)));
  return {
    framing: (palette.quick?.framing ?? []).filter((tag) => frames[tag]),
    view: (palette.quick?.view ?? []).filter((tag) => dialTags.has(tag)),
  };
}

/** 틀의 위·아래(0~1)에 가장 가까운 크롭 태그. 같으면 앞의 것. */
export function nearestFrame(frames, top, bottom) {
  let best = null;
  let score = Infinity;
  for (const [tag, [t, b]] of Object.entries(frames)) {
    const s = Math.abs(t - top) + Math.abs(b - bottom);
    if (s < score - 1e-9) {
      best = tag;
      score = s;
    }
  }
  return best;
}

/** 각도가 든 다이얼 구간의 태그. 빈 구간이면 null. 방향은 좌우가 같은 태그라 절댓값으로 본다. */
export function dialZone(zones, angle, mirrored = false) {
  const a = mirrored ? Math.min(Math.abs(angle), 180) : angle;
  return zones.find((zone) => a >= zone.from && a <= zone.to)?.tag ?? null;
}

/**
 * 다이얼로 한 축(axisTags)을 tag 로 바꾼 새 시점 선택. 다른 축과 다이얼 밖 시점은 그대로 둔다.
 * add 면 그 축의 다른 태그를 두고 tag 만 켜거나 끈다(Ctrl·Shift 를 누르고 고르기). 부딪치는 시점은 칩과 같이 꺼진다.
 */
export function viewWithZone(palette, selected, axisTags, tag, { add = false, lifted = false } = {}) {
  if (add) return tag ? viewToggle(palette, selected, tag, lifted) : selected;
  const base = selected.filter((t) => !axisTags.includes(t));
  return tag ? viewToggle(palette, base, tag, lifted) : base;
}

// ─── 그리기 ───────────────────────────────────────────────────────

const svgEl = (doc, name, attrs = {}) => {
  const node = doc.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
};

const htmlEl = (doc, name, className, text = "") => {
  const node = doc.createElement(name);
  node.className = className;
  node.textContent = text;
  return node;
};

// a: 0 이 아래(피사체 정면), 오른쪽으로 돌수록 커진다
const polar = (cx, cy, r, a) => {
  const t = (a * Math.PI) / 180;
  return [round(cx + r * Math.sin(t)), round(cy + r * Math.cos(t))];
};

function sectorPath(cx, cy, r0, r1, a0, a1) {
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M${x0} ${y0}A${r1} ${r1} 0 ${large} 0 ${x1} ${y1}L${x2} ${y2}A${r0} ${r0} 0 ${large} 1 ${x3} ${y3}Z`;
}

// 크롭: 선 사람의 키 0~1 을 그림의 y 로
const CROP = { w: 64, h: 156, top: 8, height: 140, pad: 3 };
const cropY = (f) => CROP.top + f * CROP.height;
const FIGURE = [
  ["circle", { cx: 32, cy: 19, r: 10 }],
  ["polyline", { points: "32,29 32,80" }],
  ["polyline", { points: "18,37 46,37" }],
  ["polyline", { points: "26,82 38,82" }],
  ["polyline", { points: "18,37 14,60 12,82" }],
  ["polyline", { points: "46,37 50,60 52,82" }],
  ["polyline", { points: "27,84 26,114 25,147" }],
  ["polyline", { points: "37,84 38,114 39,147" }],
];

// 방향(위에서 본 원, 아래가 정면)·높이(옆에서 본 반원, 오른쪽이 카메라)
const DIR = { cx: 42, cy: 42, r0: 12, r1: 30, cam: 37 };
const HEIGHT = { cx: 8, cy: 42, r0: 12, r1: 30, cam: 36 };

export class QuickStage {
  constructor({ palette, onFraming, onView, doc = globalThis.document }) {
    this.doc = doc;
    this.palette = palette;
    this.onFraming = onFraming;
    this.onView = onView;
    this.frames = palette.framing_frame ?? {};
    this.dial = Object.fromEntries(AXES.map((axis) => [axis, [...(palette.view_dial?.[axis] ?? [])].sort((a, b) => a.from - b.from)]));
    this.axisTags = Object.fromEntries(AXES.map((axis) => [axis, this.dial[axis].map((zone) => zone.tag)]));
    this.covers = stageCovers(palette);
    this.state = { framing: "", view: [], present: new Set(), lifted: false };
    this.drag = null;          // 끄는 중 {kind, angle}
    this.side = 1;             // 방향 카메라를 놓는 쪽(오른쪽 1·왼쪽 -1). 마지막으로 끈 쪽을 따른다
    this.root = this.build();
  }

  /** framing: 고른 크롭("" 은 자동), view: 고른 시점들([] 은 자동), present: 프롬프트에 든 태그, lifted: 시점이 부딪치지 않음. */
  update({ framing = "", view = [], present = new Set(), lifted = false } = {}) {
    this.state = { framing, view: [...view], present, lifted };
    this.paint();
  }

  // ─── 값 ─────────────────────────────────────────────────────────

  promptFraming() {
    return this.covers.framing.find((tag) => this.state.present.has(tag)) ?? "";
  }

  promptView() {
    return (this.palette.quick?.view ?? []).filter((tag) => this.state.present.has(tag));
  }

  // 그림에서 바꾸기 시작할 때의 시점: 고른 것이 없으면 프롬프트 값에서 시작한다(다른 축이 사라지지 않게)
  viewBase() {
    return this.state.view.length ? this.state.view : this.promptView();
  }

  emitView(next) {
    const now = this.state.view;
    if (next.length === now.length && next.every((t, i) => t === now[i])) return;
    this.onView?.(next);
  }

  setZone(axis, tag, add = false) {
    this.emitView(viewWithZone(this.palette, this.viewBase(), this.axisTags[axis], tag, { add, lifted: this.state.lifted }));
  }

  setFraming(tag) {
    if (tag !== this.state.framing) this.onFraming?.(tag);
  }

  // ─── 만들기 ─────────────────────────────────────────────────────

  build() {
    const doc = this.doc;
    const root = htmlEl(doc, "div", "qs");
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "크롭·시점 그림");

    const crop = htmlEl(doc, "div", "qs-col qs-crop");
    const cropHead = htmlEl(doc, "div", "qs-head");
    this.cropAuto = htmlEl(doc, "button", "chip auto qs-auto", "자동");
    this.cropAuto.type = "button";
    this.cropAuto.addEventListener("click", () => this.setFraming(""));
    cropHead.append(htmlEl(doc, "span", "qs-label", "크롭"), this.cropAuto);
    this.cropSvg = svgEl(doc, "svg", { viewBox: `0 0 ${CROP.w} ${CROP.h}`, width: CROP.w, height: CROP.h, class: "qs-svg qs-crop-svg",
      role: "slider", tabindex: 0, "aria-label": "크롭", "aria-valuemin": 0, "aria-valuemax": Math.max(0, this.covers.framing.length - 1) });
    const figure = svgEl(doc, "g", { class: "qs-figure" });
    for (const [name, attrs] of FIGURE) figure.append(svgEl(doc, name, attrs));
    this.dimTop = svgEl(doc, "rect", { class: "qs-dim", x: 0, y: 0, width: CROP.w });
    this.dimBottom = svgEl(doc, "rect", { class: "qs-dim", x: 0, width: CROP.w });
    this.cropGhost = svgEl(doc, "rect", { class: "qs-ghost", x: 4, width: CROP.w - 8, rx: 2 });
    this.cropFrame = svgEl(doc, "rect", { class: "qs-frame", x: 3, width: CROP.w - 6, rx: 2 });
    this.handles = ["top", "bottom"].map((edge) => svgEl(doc, "rect", { class: "qs-handle", x: CROP.w / 2 - 11, width: 22, height: 7, rx: 3, "data-edge": edge }));
    this.cropSvg.append(figure, this.dimTop, this.dimBottom, this.cropGhost, this.cropFrame, ...this.handles);
    this.cropRead = htmlEl(doc, "div", "qs-read");
    crop.append(cropHead, this.cropSvg, this.cropRead);

    const dials = htmlEl(doc, "div", "qs-col qs-dials");
    const dialHead = htmlEl(doc, "div", "qs-head");
    dialHead.append(htmlEl(doc, "span", "qs-label", "방향 · 높이"));
    dials.append(dialHead);
    const row = htmlEl(doc, "div", "qs-dial-row");
    this.dirSvg = this.buildDirection();
    this.heightSvg = this.buildHeight();
    row.append(this.dirSvg, this.heightSvg);
    this.dirRead = htmlEl(doc, "div", "qs-read");
    this.heightRead = htmlEl(doc, "div", "qs-read");
    dials.append(row, this.dirRead, this.heightRead);

    root.append(crop, dials);
    this.listen();
    return root;
  }

  buildDirection() {
    const doc = this.doc;
    const { cx, cy, r0, r1 } = DIR;
    const svg = svgEl(doc, "svg", { viewBox: "0 0 84 84", width: 84, height: 84, class: "qs-svg qs-dir-svg", role: "slider", tabindex: 0,
      "aria-label": "방향", "aria-valuemin": 0, "aria-valuemax": Math.max(0, this.dial.direction.length - 1) });
    const title = svgEl(doc, "title");
    title.textContent = "끌어서 방향을 고릅니다. 아래가 정면이고 좌우는 같은 태그입니다. Ctrl·Shift 를 누르고 누르면 방향을 더하거나 뺍니다.";
    svg.append(title, svgEl(doc, "circle", { class: "qs-track", cx, cy, r: DIR.cam }));
    this.dirZones = [];
    for (const zone of this.dial.direction) {
      const spans = zone.from === 0 ? [[-zone.to, zone.to]] : zone.to === 180 ? [[zone.from, 360 - zone.from]]
        : [[zone.from, zone.to], [-zone.to, -zone.from]];
      for (const [a0, a1] of spans) {
        const path = svgEl(doc, "path", { class: "qs-zone", d: sectorPath(cx, cy, r0, r1, a0, a1), "data-tag": zone.tag });
        svg.append(path);
        this.dirZones.push(path);
      }
    }
    svg.append(svgEl(doc, "circle", { class: "qs-subject", cx, cy, r: 6 }),
      svgEl(doc, "path", { class: "qs-subject", d: `M${cx - 3} ${cy + 5} L${cx} ${cy + 9} L${cx + 3} ${cy + 5}` }));
    this.dirMarks = this.dial.direction.map(() => {
      const mark = svgEl(doc, "circle", { class: "qs-cam", r: 4.5 });
      svg.append(mark);
      return mark;
    });
    return svg;
  }

  buildHeight() {
    const doc = this.doc;
    const { cx, cy, r0, r1 } = HEIGHT;
    const svg = svgEl(doc, "svg", { viewBox: "0 0 48 84", width: 48, height: 84, class: "qs-svg qs-height-svg", role: "slider", tabindex: 0,
      "aria-label": "높이" });
    const title = svgEl(doc, "title");
    title.textContent = "끌어서 카메라 높이를 고릅니다. 가운데는 수평(태그 없음)입니다.";
    svg.append(title);
    // 다이얼 구간 사이의 빈 곳은 수평
    const zones = [];
    let from = -90;
    for (const zone of this.dial.height) {
      if (zone.from > from) zones.push({ tag: null, from, to: zone.from });
      zones.push(zone);
      from = Math.max(from, zone.to);
    }
    if (from < 90) zones.push({ tag: null, from, to: 90 });
    this.heightSteps = zones;
    this.heightZones = zones.map((zone) => {
      const path = svgEl(doc, "path", { class: "qs-zone", d: sectorPath(cx, cy, r0, r1, 90 + zone.from, 90 + zone.to), "data-tag": zone.tag ?? "" });
      svg.append(path);
      return path;
    });
    svg.setAttribute("aria-valuemin", 0);
    svg.setAttribute("aria-valuemax", Math.max(0, zones.length - 1));
    svg.append(svgEl(doc, "circle", { class: "qs-subject", cx, cy: cy - 7, r: 3 }),
      svgEl(doc, "polyline", { class: "qs-subject", points: `${cx},${cy - 4} ${cx},${cy + 10}` }));
    this.heightMark = svgEl(doc, "circle", { class: "qs-cam", r: 4.5 });
    svg.append(this.heightMark);
    return svg;
  }

  // ─── 조작 ───────────────────────────────────────────────────────

  point(svg, event) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const p = svg.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    return p.matrixTransform(ctm.inverse());
  }

  listen() {
    const start = (svg, kind) => (event) => {
      if (event.button !== 0) return;
      const p = this.point(svg, event);
      if (!p) return;
      event.preventDefault();
      svg.focus({ preventScroll: true });
      if (kind === "direction" && (event.ctrlKey || event.shiftKey || event.metaKey)) {
        const angle = Math.atan2(p.x - DIR.cx, p.y - DIR.cy) * 180 / Math.PI;
        if (Math.abs(angle) > 1) this.side = Math.sign(angle);
        const tag = dialZone(this.dial.direction, angle, true);
        if (tag) this.setZone("direction", tag, true);
        this.paint();
        return;
      }
      let what = kind;
      if (kind === "crop") {
        // 누른 곳에 가까운 쪽 손잡이를 끈다
        const [top, bottom] = this.frameOf(this.state.framing || this.promptFraming());
        const f = (p.y - CROP.top) / CROP.height;
        what = Math.abs(f - top) <= Math.abs(f - bottom) ? "top" : "bottom";
      }
      this.drag = { kind: what, svg };
      svg.setPointerCapture(event.pointerId);
      this.move(p);
    };
    const move = (event) => {
      if (!this.drag) return;
      const p = this.point(this.drag.svg, event);
      if (p) this.move(p);
    };
    const end = () => {
      if (!this.drag) return;
      this.drag = null;
      this.paint();
    };
    for (const [svg, kind] of [[this.cropSvg, "crop"], [this.dirSvg, "direction"], [this.heightSvg, "height"]]) {
      svg.addEventListener("pointerdown", start(svg, kind));
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerup", end);
      svg.addEventListener("pointercancel", end);
      svg.addEventListener("keydown", (event) => this.key(kind, event));
    }
  }

  frameOf(tag) {
    return this.frames[tag] ?? [0, 1];
  }

  move(p) {
    const drag = this.drag;
    if (drag.kind === "top" || drag.kind === "bottom") {
      const f = Math.min(1, Math.max(0, (p.y - CROP.top) / CROP.height));
      const [top, bottom] = this.frameOf(this.state.framing || this.promptFraming());
      const covered = Object.fromEntries(this.covers.framing.map((tag) => [tag, this.frames[tag]]));
      const tag = nearestFrame(covered, drag.kind === "top" ? Math.min(f, bottom) : top, drag.kind === "bottom" ? Math.max(f, top) : bottom);
      if (tag) this.setFraming(tag);
    } else if (drag.kind === "direction") {
      drag.angle = Math.atan2(p.x - DIR.cx, p.y - DIR.cy) * 180 / Math.PI;
      if (Math.abs(drag.angle) > 1) this.side = Math.sign(drag.angle);
      this.setZone("direction", dialZone(this.dial.direction, drag.angle, true));
    } else {
      drag.angle = Math.max(-90, Math.min(90, Math.atan2(-(p.y - HEIGHT.cy), p.x - HEIGHT.cx) * 180 / Math.PI));
      this.setZone("height", dialZone(this.dial.height, drag.angle));
    }
    this.paint();
  }

  key(kind, event) {
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[event.key];
    const clear = event.key === "Delete" || event.key === "Backspace";
    if (step === undefined && !clear && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    if (kind === "crop") {
      const list = this.covers.framing;
      if (clear) return this.setFraming("");
      const now = list.indexOf(this.state.framing || this.promptFraming());
      const at = event.key === "Home" ? 0 : event.key === "End" ? list.length - 1 : now < 0 ? 0 : now + step;
      if (list[at]) this.setFraming(list[at]);
    } else if (kind === "direction") {
      const tags = this.axisTags.direction;
      if (clear) return this.setZone("direction", null);
      const now = tags.findIndex((tag) => this.viewBase().includes(tag));
      const at = event.key === "Home" ? 0 : event.key === "End" ? tags.length - 1 : now < 0 ? 0 : now + step;
      if (tags[at]) this.setZone("direction", tags[at]);
    } else {
      // 위 화살표가 높은 쪽
      const steps = this.heightSteps;
      if (clear) return this.setZone("height", null);
      const now = steps.findIndex((zone) => zone.tag && this.viewBase().includes(zone.tag));
      const level = steps.findIndex((zone) => !zone.tag);
      const from = now < 0 ? level : now;
      const at = event.key === "Home" ? steps.length - 1 : event.key === "End" ? 0 : from - step;
      if (steps[at]) this.setZone("height", steps[at].tag);
    }
  }

  // ─── 칠하기 ─────────────────────────────────────────────────────

  paint() {
    const { framing, view } = this.state;
    const pickedFrame = framing && this.frames[framing] ? framing : "";
    const promptFrame = this.promptFraming();
    const shown = pickedFrame || promptFrame;
    const [top, bottom] = this.frameOf(shown);
    const y0 = Math.max(1, round(cropY(top) - CROP.pad));
    const y1 = Math.min(CROP.h - 1, round(cropY(bottom) + CROP.pad));
    this.dimTop.setAttribute("height", String(shown ? y0 : 0));
    this.dimBottom.setAttribute("y", String(y1));
    this.dimBottom.setAttribute("height", String(shown ? CROP.h - y1 : 0));
    this.cropFrame.setAttribute("y", String(y0));
    this.cropFrame.setAttribute("height", String(y1 - y0));
    this.cropFrame.classList.toggle("on", Boolean(pickedFrame));
    this.cropFrame.classList.toggle("hidden", !shown);
    const ghost = pickedFrame && promptFrame && promptFrame !== pickedFrame ? this.frameOf(promptFrame) : null;
    this.cropGhost.classList.toggle("hidden", !ghost);
    if (ghost) {
      this.cropGhost.setAttribute("y", String(round(cropY(ghost[0]) - CROP.pad + 1)));
      this.cropGhost.setAttribute("height", String(round((ghost[1] - ghost[0]) * CROP.height + 2 * CROP.pad - 2)));
    }
    this.handles[0].setAttribute("y", String(round(y0 - 3.5)));
    this.handles[1].setAttribute("y", String(round(y1 - 3.5)));
    for (const handle of this.handles) handle.classList.toggle("on", Boolean(pickedFrame));
    this.cropAuto.setAttribute("aria-pressed", String(!framing));
    this.setRead(this.cropRead, pickedFrame || (promptFrame ? promptFrame : "자동"), Boolean(pickedFrame));
    this.cropSvg.setAttribute("aria-valuenow", String(Math.max(0, this.covers.framing.indexOf(shown))));
    this.cropSvg.setAttribute("aria-valuetext", pickedFrame || (promptFrame ? `자동(${promptFrame})` : "자동"));

    const picked = view.length > 0;
    const tags = picked ? view : this.promptView();
    const promptTags = this.promptView();
    for (const path of [...this.dirZones, ...this.heightZones]) {
      const tag = path.getAttribute("data-tag");
      const on = tag ? tags.includes(tag) : !tags.some((t) => this.axisTags.height.includes(t)) && path.ownerSVGElement === this.heightSvg;
      path.classList.toggle("on", on && picked);
      path.classList.toggle("det", on && !picked);
      path.classList.toggle("prompt", picked && Boolean(tag) && promptTags.includes(tag) && !tags.includes(tag));
    }
    const dirTags = this.dial.direction.filter((zone) => tags.includes(zone.tag));
    this.dirMarks.forEach((mark, i) => {
      const dragging = this.drag?.kind === "direction" && this.drag.angle !== undefined;
      const zone = dirTags[i];
      const show = dragging ? i === 0 : Boolean(zone);
      mark.classList.toggle("hidden", !show);
      if (!show) return;
      const [x, y] = polar(DIR.cx, DIR.cy, DIR.cam, dragging ? this.drag.angle : zone.at * (zone.at % 180 ? this.side : 1));
      mark.setAttribute("cx", String(x));
      mark.setAttribute("cy", String(y));
      mark.classList.toggle("on", picked || dragging);
    });
    const heightZone = this.dial.height.find((zone) => tags.includes(zone.tag));
    const e = this.drag?.kind === "height" && this.drag.angle !== undefined ? this.drag.angle : heightZone?.at ?? 0;
    const [hx, hy] = polar(HEIGHT.cx, HEIGHT.cy, HEIGHT.cam, 90 + e);
    this.heightMark.setAttribute("cx", String(hx));
    this.heightMark.setAttribute("cy", String(hy));
    this.heightMark.classList.toggle("on", picked);

    const dirText = dirTags.map((zone) => zone.tag).join(" + ");
    this.setRead(this.dirRead, dirText || (picked ? "방향 태그 없음" : "자동"), picked);
    this.setRead(this.heightRead, heightZone?.tag || (picked || tags.length ? "수평" : "자동"), picked);
    const dirIndex = this.dial.direction.findIndex((zone) => tags.includes(zone.tag));
    this.dirSvg.setAttribute("aria-valuenow", String(Math.max(0, dirIndex)));
    this.dirSvg.setAttribute("aria-valuetext", dirText || "없음");
    const heightIndex = this.heightSteps.findIndex((zone) => (heightZone ? zone.tag === heightZone.tag : !zone.tag));
    this.heightSvg.setAttribute("aria-valuenow", String(Math.max(0, heightIndex)));
    this.heightSvg.setAttribute("aria-valuetext", heightZone?.tag || "수평");
  }

  setRead(node, text, picked) {
    node.textContent = text;
    node.classList.toggle("on", picked);
  }
}
