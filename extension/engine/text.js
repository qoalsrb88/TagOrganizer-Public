// 입력창 글과 코어 결과 사이의 작은 처리(브라우저 API 를 쓰지 않아 node 로 시험한다).

import { organize } from "./core.js";

export const DEFAULT_SETTINGS = {
  default_preset: "layout:default",
  filter: "sort_only",
  max_rating: "e",
  rules: "remove",
  line_per_section: true,
  trailing_comma: true,
  guess: false,
  comments: "remove",
  output: "auto",
  nai_button: true,
};

// NovelAI 입력창은 세 묶음으로 설정을 따로 둔다. UC 는 뺄 태그를 적는 칸이라 구도 규칙으로 지우면 안 된다.
const BOX_DEFAULTS = { uc: { rules: "off" } };

export const boxGroup = (kind) => (kind === "uc" || kind === "character-uc" ? "uc" : kind === "character" ? "character" : "prompt");

// stored: {묶음: {preset, rules, guess}}. 없는 값은 전체 설정에서 가져온다.
export function boxSettings(settings, stored, kind) {
  const group = boxGroup(kind);
  return { ...settings, preset: settings.default_preset, ...BOX_DEFAULTS[group], ...stored?.[group] };
}

// 줄바꿈 표기만 다른 글인지(입력창은 문단 사이를 빈 줄로 읽는다).
export function sameLines(a, b) {
  const lines = (text) => text.replace(/\s*\n\s*/g, "\n").trim();
  return lines(a) === lines(b);
}

// 구분 표기 없는 조각 하나는 정렬할 것이 없다(오른쪽 클릭이 단어를 잡는 경우도 있다). 이때는 입력창 전체를 정렬한다.
export function isSortableSelection(text) {
  return /[,\n]/.test(text.replace(/^[\s,]+|[\s,]+$/g, "")) || /\bBREAK\b/.test(text);
}

// 일부만 고른 글의 강조가 잘렸으면 이유를 돌려준다(잘린 채 정렬하면 입력창의 강조가 깨진다).
export function selectionProblem(raw) {
  const text = raw.replace(/[:;=]-?[()[\]{}]/g, " ");   // :) ;( :] 같은 이모티콘 태그는 괄호로 세지 않는다
  for (const [open, close] of ["{}", "[]", "()"]) {
    let depth = 0;
    for (let i = 0; i < text.length && depth >= 0; i++) {
      if (text[i] === "\\") i++;
      else if (text[i] === open) depth++;
      else if (text[i] === close) depth--;
    }
    if (depth !== 0) return `선택이 ${open}${close} 강조의 중간에서 잘렸습니다`;
  }
  const marks = text.match(/::/g);
  if (marks && marks.length % 2) return "선택이 :: 강조의 중간에서 잘렸습니다";
  return "";
}

// 선택의 앞뒤 빈칸·쉼표를 결과에 그대로 붙인다(`a, [b, c,] d` 에서 뒤 쉼표가 사라지면 c d 가 붙는다).
export function keepEdges(original, organized) {
  const head = /^[\s,]*/.exec(original)[0];
  if (head.length === original.length) return original;
  const tail = /[\s,]*$/.exec(original)[0];
  return head + organized + tail;
}

export function optionsFor(settings, arrangement, layout, hostname) {
  const nai = /(^|\.)novelai\.net$/.test(hostname);
  const options = {
    preset: settings.filter,
    max_rating: settings.max_rating,
    rules: settings.rules,
    line_per_section: settings.line_per_section,
    trailing_comma: settings.trailing_comma,
    guess: settings.guess,
    comments: settings.comments,
    input_dialect: "auto",
    dialect_priority: nai ? "nai, comfyui, anima" : "comfyui, nai, anima",
    output_dialect: settings.output === "auto" ? (nai ? "nai" : "") : settings.output,
  };
  if (arrangement) options.arrangement = arrangement;
  else if (layout) options.layout = layout;
  if (settings.framing) options.framing = settings.framing;
  if (settings.view) options.view = settings.view;
  if (settings.gaze) options.gaze = settings.gaze;
  return options;
}

// 정렬 → 입력창에 넣을 글과 알림 내용.
export function organizeForField(text, pack, options, { partial = false } = {}) {
  const result = organize(text, pack, options);
  const { counts, removed, unknown, warnings } = result.report;
  return {
    text: result.text,
    output: partial ? keepEdges(text, result.text) : result.text,
    kept: counts.kept,
    removed: removed.map((r) => r.tag),
    removedReasons: removed.map((r) => r.reason),
    unknown,
    warnings: warnings.length,
    dialect: result.report.dialect,
  };
}

export function summary(done) {
  const parts = [`${done.kept}개 정렬`];
  if (done.removed.length) {
    const names = done.removed.slice(0, 6).join(", ");
    const more = done.removed.length > 6 ? ` 외 ${done.removed.length - 6}개` : "";
    parts.push(`${done.removed.length}개 뺌(${names}${more})`);
  }
  if (done.unknown.length) parts.push(`DB에 없음 ${done.unknown.length}개`);
  if (done.warnings) parts.push(`문법 경고 ${done.warnings}개`);
  return parts.join(" · ");
}
