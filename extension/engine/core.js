// BMK Tag Organizer Core (JS) — bmk_tag_organizer_core.py v1.5 의 organize 를 옮긴 것.
//
// 정본은 Python 코어다. 코어를 고치면 이 파일도 같이 고치고, tests/test_extension_core.py 가 같은 입력의
// 결과(text·removed_text·report 전부)를 두 코어에서 비교한다. 함수 순서와 이름을 Python 코어와 맞춰 두었다.
//
// Python 과 달라지기 쉬운 곳은 아래 도우미로 맞춘다.
// - \w \d \s \b 는 Python 에서 유니코드 기준이다(WORD·DIGIT·WS). JS 의 \w \b 는 ASCII 뿐이라 쓰지 않는다.
// - strip()·split() 의 공백 집합, float() 가 읽는 글, `.3f` 의 반올림(정확히 절반이면 짝수로), 코드 포인트 순 정렬.
// - 팩의 이름을 키로 쓰는 표는 Map 이다(객체는 숫자 같은 키의 순서가 바뀌고 __proto__ 가 위험하다).

export const PACK_FORMAT = "tagorg-pack";
export const PACK_SCHEMA = 1;
const COLUMNS = ["key", "type", "count", "primary", "rating", "secondary", "regions", "identity", "status", "display"];
const DEFAULTS = [null, 0, 0, "etc", "", [], [], 0, "", null];

const RATINGS = ["g", "s", "q", "e"];
export const DIALECTS = ["comfyui", "nai", "anima"];
const AUTO_DIALECT = "auto";
const INPUT_DIALECTS = [AUTO_DIALECT, ...DIALECTS];
const DIALECT_PRIORITY = ["nai", "comfyui", "anima"];
const COMMENT_MODES = ["remove", "keep"];
const RULE_GROUPS = ["framing", "view", "explicit"];
const RULE_MODES = ["off", "report", "remove"];
const ORDERS = ["input", "popular", "alpha"];
const SYSTEM_CATEGORIES = ["etc", "unknown", "special", "natural"];

const TYPE_CUSTOM = -1;
const TYPE_GUESS = -2;

const ARRANGEMENT_FORMAT = "tagorg-arrangement";
const ARRANGEMENT_VERSION = 1;
const PALETTE_FORMAT = "tagorg-palette";
const OVERRIDE_TARGETS = [["framing", "composition/framing"], ["view", "composition/angle"]];
const REST_CHUNK = "rest";
const DEFAULT_GUESS_HEADS = ["composition", "action", "clothes", "body", "object", "effect", "background"];

// ─── Python 문자열 도우미 ─────────────────────────────────────────

const WS = "\\t-\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const S = `[${WS}]`;
const WORD = "[\\p{L}\\p{N}_]";
const NOT_WORD_BEFORE = "(?<![\\p{L}\\p{N}_])";
const NOT_WORD_AFTER = "(?![\\p{L}\\p{N}_])";
const LETTER = "[\\p{L}\\p{Nl}\\p{No}]";   // [^\W\d_]
const D = "\\p{Nd}";

const WS_RUN = new RegExp(`${S}+`, "gu");
const WS_START = new RegExp(`^${S}+`, "u");
const WS_END = new RegExp(`${S}+$`, "u");
const ONE_WS = new RegExp(`^${S}$`, "u");
const ALNUM = /^[\p{L}\p{N}]$/u;
const ALPHA = /^\p{L}$/u;

const lstrip = (s) => s.replace(WS_START, "");
const rstrip = (s) => s.replace(WS_END, "");
const strip = (s) => rstrip(lstrip(s));
const isSpace = (ch) => ONE_WS.test(ch);
const isAlnum = (ch) => ALNUM.test(ch);
const splitWs = (s) => s.split(WS_RUN).filter(Boolean);
const cpLength = (s) => Array.from(s).length;
const cpSlice = (s, n) => (s.length <= n ? s : Array.from(s).slice(0, n).join(""));

function lstripChars(s, chars) {
  let i = 0;
  while (i < s.length && chars.includes(s[i])) i++;
  return s.slice(i);
}

function stripChars(s, chars) {
  let e = s.length;
  while (e > 0 && chars.includes(s[e - 1])) e--;
  return lstripChars(s.slice(0, e), chars);
}

function partition(s, sep) {
  const i = s.indexOf(sep);
  return i < 0 ? [s, "", ""] : [s.slice(0, i), sep, s.slice(i + sep.length)];
}

// i 바로 앞 글자(서로게이트 쌍이면 한 글자로).
function charBefore(s, i) {
  if (i <= 0) return "";
  const cp = s.codePointAt(i - 2 >= 0 ? i - 2 : 0);
  return i >= 2 && cp > 0xffff ? s.slice(i - 2, i) : s[i - 1];
}

function compareCp(a, b) {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < x.length && i < y.length; i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d) return d;
  }
  return x.length - y.length;
}

const sortedCp = (items) => [...items].sort(compareCp);

const FLOAT = /^[+-]?(?:\d+(?:_\d+)*(?:\.(?:\d+(?:_\d+)*)?)?|\.\d+(?:_\d+)*)(?:[eE][+-]?\d+(?:_\d+)*)?$/;
const FLOAT_WORD = /^([+-]?)(inf|infinity|nan)$/i;

// float(): 읽을 수 없으면 null.
function pyFloat(text) {
  const s = strip(text).normalize("NFKC");
  const word = FLOAT_WORD.exec(s);
  if (word) {
    if (word[2].toLowerCase() === "nan") return NaN;
    return word[1] === "-" ? -Infinity : Infinity;
  }
  return FLOAT.test(s) ? Number(s.replaceAll("_", "")) : null;
}

// f"{w:.{n}f}": 정확히 절반이면 짝수 쪽으로(toFixed 는 큰 쪽으로 간다).
function fixed(w, n) {
  if (Number.isNaN(w)) return "nan";
  if (!Number.isFinite(w)) return w > 0 ? "inf" : "-inf";
  const sign = w < 0 || Object.is(w, -0) ? "-" : "";
  const abs = Math.abs(w);
  if (abs >= 1e21) return `${sign}${BigInt(abs)}.${"0".repeat(n)}`;
  const exact = abs.toFixed(100);
  const dot = exact.indexOf(".");
  const kept = exact.slice(0, dot + 1 + n);
  if (/^50*$/.test(exact.slice(dot + 1 + n)) && Number(kept.at(-1)) % 2 === 0) return sign + kept;
  return sign + abs.toFixed(n);
}

const isMapping = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const anyIn = (items, set) => {
  for (const x of items || []) if (set.has(x)) return true;
  return false;
};

// ─── 정규화 ──────────────────────────────────────────────────────

export function normalizeKey(text, kaomoji = EMPTY_SET) {
  const t = strip(text).toLowerCase();
  if (kaomoji.has(t)) return t;
  return strip(t.replaceAll("_", " ").replace(WS_RUN, " "));
}

const EMPTY_SET = new Set();

// ─── 태그 팩 ─────────────────────────────────────────────────────

export class TagPack {
  constructor(data) {
    if (data.format !== PACK_FORMAT) throw new Error("태그 팩 형식이 아닙니다 (format != tagorg-pack).");
    if (data.schema !== PACK_SCHEMA) {
      throw new Error(`지원하지 않는 태그 팩 스키마입니다: ${data.schema} (지원: ${PACK_SCHEMA})`);
    }
    if (JSON.stringify(data.columns || []) !== JSON.stringify(COLUMNS)) {
      throw new Error("태그 팩의 columns 정의가 코어와 맞지 않습니다. 팩을 다시 빌드하세요.");
    }
    this.version = data.version ?? "";
    this.taxonomy = data.taxonomy;
    this.rules = data.rules || {};
    this.layouts = data.layouts;
    this.presets = data.presets;
    this.guess = data.guess || {};
    this.kaomoji = new Set(data.kaomoji || []);
    this._guessRules = null;
    this._rows = new Map();
    for (const row of data.tags) this._rows.set(row[0], row);
    this._aliases = new Map(Object.entries(data.aliases || {}));
    this._db = new Map();
    for (const [db, keys] of Object.entries(data.extra || {})) for (const key of keys) this._db.set(key, db);
    this._site = new Map();
    for (const [site, keys] of Object.entries(data.site || {})) for (const key of keys) this._site.set(key, site);
    this._infos = new Map();
    this.categories = new Map();
    for (const c of this.taxonomy.categories) {
      this.categories.set(c.id, new Set((c.subs || []).map((s) => s.id)));
    }
    for (const sys of SYSTEM_CATEGORIES) if (!this.categories.has(sys)) this.categories.set(sys, new Set());
  }

  get size() {
    return this._rows.size;
  }

  get(key) {
    const hit = this._infos.get(key);
    if (hit !== undefined) return hit;
    const row = this._rows.get(key);
    if (row === undefined) return null;
    const vals = [...row, ...DEFAULTS.slice(row.length)];
    const info = makeInfo({
      key: vals[0],
      type: Number(vals[1]),
      count: Number(vals[2]),
      primary: vals[3] || "etc",
      rating: vals[4] || "",
      secondary: vals[5] || [],
      regions: vals[6] || [],
      identity: Boolean(vals[7]),
      status: vals[8] || "",
      display: vals[9] || vals[0],
      db: this._db.get(key) || "",
      site: this._site.get(key) || "",
    });
    this._infos.set(key, info);
    return info;
  }

  resolve(key) {
    const info = this.get(key);
    if (info !== null) return [info, false];
    const target = this._aliases.get(key);
    if (target !== undefined) return [this.get(target), true];
    return [null, false];
  }

  isKnown(key) {
    return this._rows.has(key) || this._aliases.has(key);
  }
}

function makeInfo(fields) {
  const info = { db: "", site: "", ...fields };
  info.main = partition(info.primary, "/")[0];
  return info;
}

// ─── 선택자 ──────────────────────────────────────────────────────

class Selector {
  constructor(text, main, sub, identity) {
    this.text = text;
    this.main = main;
    this.sub = sub;
    this.identity = identity;
  }

  static parse(text) {
    const raw = strip(String(text)).toLowerCase();
    let sel = raw;
    let identity = false;
    if (sel.endsWith("@identity")) {
      identity = true;
      sel = sel.slice(0, -"@identity".length);
    }
    const [main, , sub] = partition(sel, "/");
    return new Selector(raw, strip(main), strip(sub), identity);
  }

  matches(category, identity = false) {
    if (this.main === "*") return !this.identity || identity;
    const [main, , sub] = partition(category, "/");
    if (main !== this.main) return false;
    if (this.sub && sub !== this.sub) return false;
    return !this.identity || identity;
  }

  validate(categories) {
    if (this.main === "*") return null;
    if (!categories.has(this.main)) return `알 수 없는 카테고리: ${this.text}`;
    if (this.sub && !categories.get(this.main).has(this.sub)) return `알 수 없는 서브 카테고리: ${this.text}`;
    return null;
  }
}

function parseSelectors(value) {
  if (!value || (Array.isArray(value) && !value.length)) return [];
  const items = typeof value === "string" ? value.split(/[,\n]/) : [...value];
  return items.filter((s) => strip(String(s))).map((s) => Selector.parse(s));
}

// ─── 옵션과 결과 ──────────────────────────────────────────────────

const OPTION_DEFAULTS = {
  preset: "sort_only",
  exclude: [],
  max_rating: "e",
  rules: "remove",
  rule_overrides: {},
  layout: "default",
  input_dialect: "comfyui",
  output_dialect: "",
  dialect_priority: "",
  comments: "remove",
  sentences: true,
  line_per_section: true,
  trailing_comma: true,
  dedupe: true,
  resolve_aliases: true,
  match_secondary: false,
  order: "input",
  arrangement: null,
  framing: "",
  view: "",
  guess: false,
};

function coerceOptions(value) {
  if (value === null || value === undefined) return { ...OPTION_DEFAULTS };
  if (!isMapping(value)) throw new TypeError("options 는 객체여야 합니다.");
  const unknown = Object.keys(value).filter((k) => !has(OPTION_DEFAULTS, k)).sort();
  if (unknown.length) throw new Error("알 수 없는 옵션: " + unknown.join(", "));
  return { ...OPTION_DEFAULTS, ...value };
}

function priorityOf(opts) {
  const raw = opts.dialect_priority;
  let items;
  if (typeof raw === "string") items = raw.replaceAll(">", ",").split(",").map((s) => strip(s).toLowerCase());
  else if (Array.isArray(raw)) items = raw.map((s) => strip(String(s)).toLowerCase());
  else throw new Error('dialect_priority 는 "nai, comfyui, anima" 같은 글이나 목록이어야 합니다.');
  items = items.filter(Boolean);
  if (items.some((s) => !DIALECTS.includes(s)) || new Set(items).size !== items.length) {
    throw new Error(`dialect_priority 는 ${DIALECTS.join(", ")} 를 겹치지 않게 적습니다: ${raw}`);
  }
  return [...items, ...DIALECT_PRIORITY.filter((d) => !items.includes(d))];
}

function ruleModes(opts) {
  if (typeof opts.rules === "string") return Object.fromEntries(RULE_GROUPS.map((g) => [g, opts.rules]));
  return { ...Object.fromEntries(RULE_GROUPS.map((g) => [g, "remove"])), ...(opts.rules || {}) };
}

function validateOptions(opts, pack) {
  if (!has(pack.presets, opts.preset)) {
    throw new Error(`알 수 없는 프리셋: ${opts.preset} (가능: ${Object.keys(pack.presets).join(", ")})`);
  }
  if (!has(pack.layouts, opts.layout)) {
    throw new Error(`알 수 없는 레이아웃: ${opts.layout} (가능: ${Object.keys(pack.layouts).join(", ")})`);
  }
  if (!RATINGS.includes(opts.max_rating)) throw new Error(`max_rating 은 ${RATINGS.join("/")} 중 하나여야 합니다.`);
  if (!INPUT_DIALECTS.includes(opts.input_dialect)) {
    throw new Error(`알 수 없는 문법: ${opts.input_dialect} (가능: ${INPUT_DIALECTS.join(", ")})`);
  }
  if (opts.output_dialect && !DIALECTS.includes(opts.output_dialect)) {
    throw new Error(`알 수 없는 출력 문법: ${opts.output_dialect} (가능: ${DIALECTS.join(", ")})`);
  }
  priorityOf(opts);
  if (!COMMENT_MODES.includes(opts.comments)) throw new Error(`comments 는 ${COMMENT_MODES.join("/")} 중 하나여야 합니다.`);
  if (!ORDERS.includes(opts.order)) throw new Error(`order 는 ${ORDERS.join("/")} 중 하나여야 합니다.`);
  for (const [group, mode] of Object.entries(ruleModes(opts))) {
    if (!RULE_GROUPS.includes(group) || !RULE_MODES.includes(mode)) {
      throw new Error(`규칙 설정이 잘못됐습니다: ${group}=${mode}`);
    }
  }
  for (const [rid, mode] of Object.entries(opts.rule_overrides)) {
    if (!RULE_MODES.includes(mode)) throw new Error(`규칙 설정이 잘못됐습니다: ${rid}=${mode}`);
  }
  const sels = [...parseSelectors(pack.presets[opts.preset].exclude || []), ...parseSelectors(opts.exclude)];
  const errors = sels.map((s) => s.validate(pack.categories)).filter(Boolean);
  if (errors.length) throw new Error(errors.join("; "));
  return sels;
}

function makeToken(fields) {
  return {
    text: "", weight: 1.0, verbatim: null, kind: "tag", opaque: false, prefix: "", core: "", key: "", info: null,
    alias_of: "", primary: "", removed: "", flagged: "", section: "", chunk: 0, order: 0, guess: "", injected: "",
    warning: "", slot: -1, member: 0, index: 0, ...fields,
  };
}

const tokenIdentity = (t) => Boolean(t.info && t.info.identity);

// ─── 파싱 ────────────────────────────────────────────────────────

const ESC_OPEN = "\x01";
const ESC_CLOSE = "\x02";
const BREAK_SRC = `${NOT_WORD_BEFORE}BREAK${NOT_WORD_AFTER}`;
const BREAK_G = new RegExp(BREAK_SRC, "gu");
const EXTRA_NET_SRC = `<${WORD}+:[^<>]+>`;
const EXTRA_NET_G = new RegExp(EXTRA_NET_SRC, "gu");
const EXTRA_NET_FULL = new RegExp(`^${EXTRA_NET_SRC}$`, "u");
const WILDCARD_SRC = "__[\\p{L}\\p{N}_./*\\-]+__";
const WILDCARD = new RegExp(WILDCARD_SRC, "u");
const WILDCARD_G = new RegExp(WILDCARD_SRC, "gu");
const NUMBER_SRC = `-?${D}+(?:\\.${D}+)?`;
const NAI_NUM = new RegExp(`(${NUMBER_SRC})::`, "uy");
const ACTION_PREFIX = /^(source|target|mutual)#/iu;

const protect = (s) => s.replaceAll("\\(", ESC_OPEN).replaceAll("\\)", ESC_CLOSE);
const restore = (s) => s.replaceAll(ESC_OPEN, "\\(").replaceAll(ESC_CLOSE, "\\)");
const unescapeText = (s) =>
  s.replaceAll(ESC_OPEN, "(").replaceAll(ESC_CLOSE, ")").replaceAll("\\(", "(").replaceAll("\\)", ")");
const escapeParens = (s) => s.replaceAll("(", "\\(").replaceAll(")", "\\)");

function matchAt(rx, text, i) {
  rx.lastIndex = i;
  return rx.exec(text);
}

function naiNumberCloses(text, end) {
  const rest = lstripChars(text.slice(end), " \t");
  return !rest || ",\n}]".includes(rest[0]);
}

const ABBREVIATIONS = new Set(["mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "ltd", "bros", "vs", "ver", "vol",
  "mt", "ft", "prof"]);
const SPAN_WORDS = 10;
const NUM_OPENERS_G = new RegExp(`${NUMBER_SRC}::`, "gu");
const COMFY_WEIGHT = `[+-]?(?:${D}+(?:\\.${D}*)?|\\.${D}+)(?:[eE][+-]?${D}+)?`;
const COMFY_WEIGHT_TAIL = new RegExp(`:${S}*${COMFY_WEIGHT}${S}*$`, "u");
const NAME_NUM = new RegExp(`^${NUMBER_SRC}::([\\s\\S]*?)(?:${S}*::)?$`, "u");
const SPAN_MARKS_G = /::|[{}[\]]/g;
const SENTENCE_LEFT_CHARS = ")]}'\"" + ESC_CLOSE;

function nameForms(text) {
  let s = strip(text).replaceAll("\\(", "(").replaceAll("\\)", ")");
  const forms = [strip(text), s];
  for (let k = 0; k < 4; k++) {
    const m = NAME_NUM.exec(s);
    if (m) s = strip(m[1]);
    else if (s.length > 1 && s[0] === "(" && s.at(-1) === ")") s = strip(s.slice(1, -1).replace(COMFY_WEIGHT_TAIL, ""));
    else if (s.length > 1 && ((s[0] === "{" && s.at(-1) === "}") || (s[0] === "[" && s.at(-1) === "]"))) s = strip(s.slice(1, -1));
    else break;
    forms.push(s);
  }
  return forms;
}

function sentenceEnd(text, i, left, known) {
  const nxt = i + 1 < text.length ? text[i + 1] : "";
  if (nxt && !isSpace(nxt)) return false;
  const prev = charBefore(text, i);
  if (!(isAlnum(prev) || SENTENCE_LEFT_CHARS.includes(prev))) return false;
  const words = splitWs(unescapeText(left).replace(NUM_OPENERS_G, " ")).map((w) => lstripChars(w, "{[")).filter(Boolean);
  if (!words.length) return false;
  if (known !== null) {
    const stops = [text.indexOf(",", i), text.indexOf("\n", i)].filter((p) => p >= 0);
    const end = stops.length ? Math.min(...stops) : text.length;
    const right = splitWs(unescapeText(text.slice(i + 1, end)).replace(SPAN_MARKS_G, " ")).slice(0, SPAN_WORDS);
    const lw = words.slice(-SPAN_WORDS);
    const heads = [];
    for (let a = 1; a <= lw.length; a++) heads.push(lw.slice(-a).join(" ") + ".");
    for (const h of heads) {
      for (let b = 1; b <= right.length; b++) if (known(h + " " + right.slice(0, b).join(" "))) return false;
    }
    if (heads.some((h) => known(h))) return true;
  }
  const word = lstripChars(words.at(-1).toLowerCase(), "([{\"'");
  return !((cpLength(word) === 1 && ALPHA.test(word)) || word.includes(".") || ABBREVIATIONS.has(word));
}

function splitSegments(text, dialect, known = null, sentences = false) {
  let out = [];
  let buf = "";
  let depth = 0;
  let numOpen = 0;
  let i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (sentences && ch === "." && depth === 0 && numOpen === 0 && sentenceEnd(text, i, buf, known)) {
      out.push(buf + ch);
      buf = "";
      i += 1;
      continue;
    }
    if (dialect === "nai") {
      if (numOpen && text.startsWith("::", i)) {
        numOpen -= 1;
        buf += "::";
        i += 2;
        continue;
      }
      const m = matchAt(NAI_NUM, text, i);
      if (m && (i === 0 || " ,\n{[".includes(text[i - 1]))) {
        const end = i + m[0].length;
        if (naiNumberCloses(text, end)) numOpen = Math.max(0, numOpen - 1);
        else numOpen += 1;
        buf += m[0];
        i = end;
        continue;
      }
    }
    if ("([{".includes(ch)) depth += 1;
    else if (")]}".includes(ch) && depth > 0) depth -= 1;
    if (",\n".includes(ch) && depth === 0 && numOpen === 0) {
      out.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
    i += 1;
  }
  out.push(buf);
  if (depth || numOpen) out = text.split(/[,\n]/);
  return out.map(strip).filter(Boolean);
}

function comfyGroups(s) {
  const result = [];
  let current = "";
  let nesting = 0;
  for (const ch of s) {
    if (ch === "(") {
      if (nesting === 0) {
        if (current) result.push(current);
        current = "(";
      } else {
        current += ch;
      }
      nesting += 1;
    } else if (ch === ")") {
      nesting -= 1;
      if (nesting === 0) {
        result.push(current + ")");
        current = "";
      } else {
        current += ch;
      }
    } else {
      current += ch;
    }
  }
  if (current) result.push(current);
  return result;
}

function comfyWeights(s, weight = 1.0, grouped = false) {
  const out = [];
  for (const x of comfyGroups(s)) {
    if (x.length >= 2 && x[0] === "(" && x.at(-1) === ")") {
      let inner = x.slice(1, -1);
      let w = weight * 1.1;
      const colon = inner.lastIndexOf(":");
      if (colon > 0) {
        const parsed = pyFloat(inner.slice(colon + 1));
        if (parsed !== null) {
          w = parsed;
          inner = inner.slice(0, colon);
        }
      }
      out.push(...comfyWeights(inner, w, true));
    } else {
      out.push([x, weight, grouped]);
    }
  }
  return out;
}

const NUMBER_TAIL = new RegExp(`${NUMBER_SRC}(?=\\n?$)`, "u");

function naiWeights(s, notes = null) {
  const out = [];
  const stack = [];
  let buf = "";

  const flush = () => {
    if (buf) {
      let w = 1.0;
      for (const [, m] of stack) w *= m;
      out.push([buf, w, stack.length > 0]);
      buf = "";
    }
  };
  const note = (number) => {
    if (notes !== null && out.length) notes.set(out.length - 1, "nai_number:" + number);
  };

  let i = 0;
  const n = s.length;
  while (i < n) {
    if (stack.length && stack.at(-1)[0] === "num" && s.startsWith("::", i)) {
      const tail = NUMBER_TAIL.exec(buf);
      flush();
      if (tail) note(tail[0]);
      stack.pop();
      i += 2;
      continue;
    }
    const m = matchAt(NAI_NUM, s, i);
    if (m && (i === 0 || " ,{[".includes(s[i - 1]))) {
      const end = i + m[0].length;
      if (naiNumberCloses(s, end)) {
        if (stack.length && stack.at(-1)[0] === "num") {
          buf += m[1];
          flush();
          note(m[1]);
          stack.pop();
        } else {
          buf += m[0];
        }
        i = end;
        continue;
      }
      flush();
      stack.push(["num", pyFloat(m[1])]);
      i = end;
      continue;
    }
    const ch = s[i];
    if (ch === "{") {
      flush();
      stack.push(["{", 1.05]);
    } else if (ch === "[") {
      flush();
      stack.push(["[", 1 / 1.05]);
    } else if (ch === "}" && stack.length && stack.at(-1)[0] === "{") {
      flush();
      stack.pop();
    } else if (ch === "]" && stack.length && stack.at(-1)[0] === "[") {
      flush();
      stack.pop();
    } else {
      buf += ch;
    }
    i += 1;
  }
  flush();
  return out;
}

const COMMENT_START_G = new RegExp(`(?:^|(?<=[${WS},]))//`, "gu");
const COMMENT_MARK = "\x03";

function stripComments(text, known = null) {
  const lines = [];
  const comments = [];
  for (const line of text.split("\n")) {
    let pos = -1;
    for (const m of line.matchAll(COMMENT_START_G)) {
      const mEnd = m.index + m[0].length;
      const start = line.lastIndexOf(",", m.index) + 1;
      const end = line.indexOf(",", mEnd);
      const seg = line.slice(start, end >= 0 ? end : line.length);
      const heads = [seg];
      for (let k = 0; k < m.index - start; k++) if (seg.startsWith(". ", k)) heads.push(seg.slice(k + 2));
      if (known !== null && heads.some((h) => nameForms(h).some((f) => known(f)))) continue;
      pos = m.index;
      break;
    }
    if (pos < 0) {
      lines.push(line);
      continue;
    }
    comments.push(strip(line.slice(pos)));
    lines.push(line.slice(0, pos) + COMMENT_MARK);
  }
  return [lines.join("\n"), comments];
}

function isSpecial(seg, dialect) {
  const s = strip(seg);
  const low = s.toLowerCase();
  if (EXTRA_NET_FULL.test(s) || WILDCARD.test(s) || low.startsWith("embedding:")) return true;
  if (dialect !== "nai" && s.includes("{") && s.includes("|")) return true;
  if (dialect === "nai" && low.startsWith("text:")) return true;
  return false;
}

function splitPrefix(text, dialect, kaomoji = EMPTY_SET) {
  const t = strip(text);
  if (t.toLowerCase().startsWith("artist:")) return ["artist", strip(t.slice("artist:".length))];
  if (dialect === "anima" && t.startsWith("@") && t.length > 1 && !kaomoji.has(t.toLowerCase())) {
    return ["artist", strip(t.slice(1))];
  }
  const m = ACTION_PREFIX.exec(t);
  if (m) return [m[1].toLowerCase() + "#", strip(t.slice(m[0].length))];
  return ["", t];
}

function knownFn(pack) {
  if (!pack) return null;
  return (s) => pack.isKnown(normalizeKey(s, pack.kaomoji));
}

function sentence(seg, known) {
  const s = rstrip(seg);
  if (s.length < 2 || !s.endsWith(".") || s.endsWith("..")) return null;
  const before = charBefore(s, s.length - 1);
  if (!(isAlnum(before) || SENTENCE_LEFT_CHARS.includes(before))) return null;
  const literal = unescapeText(s);
  if (known !== null && known(literal)) return null;
  const body = strip(literal.slice(0, -1));
  if ((known !== null && known(body)) || !body.includes(" ")) return "";
  return s;
}

export function parsePrompt(text, dialect = "comfyui", pack = null, sentences = false) {
  const kaomoji = pack ? pack.kaomoji : EMPTY_SET;
  const known = knownFn(pack);
  const protectedText = protect(text.replace(EXTRA_NET_G, (m) => "," + m + ","));
  const tokens = [];
  for (let seg of splitSegments(protectedText, dialect, known, sentences)) {
    let raw = restore(seg);
    if (isSpecial(raw, dialect)) {
      tokens.push(makeToken({ text: raw, verbatim: raw, kind: "special" }));
      continue;
    }
    if (sentences) {
      const natural = sentence(seg, known);
      if (natural) {
        const literal = strip(unescapeText(natural));
        tokens.push(makeToken({ text: literal, verbatim: strip(restore(natural)), kind: "natural", primary: "natural" }));
        continue;
      }
      if (natural === "") {
        seg = rstrip(seg).slice(0, -1);
        raw = restore(seg);
      }
    }
    if (dialect !== "nai" && pack) {
      const literal = unescapeText(seg);
      const [, core] = splitPrefix(literal, dialect, kaomoji);
      if (pack.isKnown(normalizeKey(core, kaomoji))) {
        tokens.push(makeToken({ text: literal, verbatim: raw }));
        continue;
      }
    }
    const notes = new Map();
    const pieces = dialect === "nai" ? naiWeights(seg, notes) : comfyWeights(seg);
    const grouped = pieces.some(([, , g]) => g);
    const plain = pieces.some(([p, , g]) => !g && strip(p));
    if (grouped && plain) {
      tokens.push(makeToken({ text: unescapeText(seg), verbatim: raw, kind: "unknown", opaque: true }));
      continue;
    }
    const sub = [];
    pieces.forEach(([ptext, weight], pi) => {
      const before = sub.length;
      for (const piece of ptext.split(",")) {
        const part = strip(unescapeText(piece));
        if (part) sub.push(makeToken({ text: part, weight }));
      }
      if (notes.has(pi) && sub.length > before) sub.at(-1).warning = notes.get(pi);
    });
    if (sub.length === 1 && !grouped) sub[0].verbatim = raw;
    tokens.push(...sub);
  }
  return tokens;
}

export function warningText(reason) {
  const [kind, , value] = partition(reason, ":");
  if (kind === "nai_number") {
    return `NAI 는 '${value}::' 의 ${value} 를 가중치로 읽습니다 — 닫기로 읽고, 출력은 '${value} ::' 로 띄웁니다`;
  }
  return reason;
}

const DIALECT_SIGNS = {
  nai: [
    new RegExp(`(?<![\\p{L}\\p{N}_.])${NUMBER_SRC}::`, "gu"),
    new RegExp(`\\{[^{}|\\n]*${LETTER}[^{}|\\n]*\\}`, "gu"),
    new RegExp(`\\[[^\\[\\]|:\\n]*${LETTER}[^\\[\\]|:\\n]*\\]`, "gu"),
    new RegExp(`(?:^|[,\\n])${S}*(?:source|target|mutual)#`, "giu"),
    new RegExp(`(?:^|[,\\n])${S}*text:`, "giu"),
  ],
  comfyui: [
    new RegExp(`\\([^()\\n]*:${S}*${COMFY_WEIGHT}${S}*\\)`, "gu"),
    /\\[()]/gu,
    EXTRA_NET_G,
    new RegExp(`${NOT_WORD_BEFORE}embedding:`, "giu"),
    /\{[^{}\n]*\|[^{}\n]*\}/gu,
  ],
};
const ANIMA_ARTIST_G = new RegExp(`(?:^|[,\\n])[${WS}(]*(@[^${WS},()@][^,\\n()]*)`, "gu");
const EVIDENCE_CHARS = 40;

export function detectDialect(rawText, priority = DIALECT_PRIORITY, kaomoji = EMPTY_SET, known = null) {
  const kao = new Set(kaomoji);
  const text = rawText.replace(BREAK_G, ",");
  const evidence = {};
  const add = (dialect, value) => (evidence[dialect] ||= []).push(value);
  for (const [dialect, signs] of Object.entries(DIALECT_SIGNS)) {
    for (const rx of signs) {
      for (const m of text.matchAll(rx)) add(dialect, cpSlice(stripChars(m[0], " ,\n"), EVIDENCE_CHARS));
    }
  }
  for (const m of text.matchAll(ANIMA_ARTIST_G)) {
    const name = strip(m[1]);
    if (kao.has(name.toLowerCase()) || kao.has(normalizeKey(name, kao)) || (known !== null && known(name))) continue;
    add("anima", cpSlice(name, EVIDENCE_CHARS));
  }
  const order = [...priority, ...DIALECT_PRIORITY.filter((d) => !priority.includes(d))];
  const comfy = evidence.anima ? "anima" : "comfyui";
  const hasNai = Boolean(evidence.nai);
  const hasComfy = Boolean(evidence.comfyui || evidence.anima);
  let dialect;
  if (hasNai && hasComfy) dialect = order.indexOf("nai") <= order.indexOf(comfy) ? "nai" : comfy;
  else if (hasNai) dialect = "nai";
  else if (hasComfy) dialect = comfy;
  else dialect = order[0];
  const pick = (fn) => Object.fromEntries(Object.entries(evidence).map(([d, v]) => [d, fn(v)]));
  return { dialect, evidence: pick((v) => v.slice(0, 5)), counts: pick((v) => v.length), conflict: hasNai && hasComfy };
}

// ─── 분류·필터·규칙 ───────────────────────────────────────────────

const CHOICE_SRC = "\\{([^{}]*)\\}";
const CHOICE_LIMIT = 64;

function choiceInfo(text, pack) {
  if (text.includes("$$") || text.includes("::") || WILDCARD.test(text)) return null;
  const groups = [...text.matchAll(new RegExp(CHOICE_SRC, "g"))].map((m) => m[1]);
  if (!groups.length || groups.some((g) => !g.includes("|"))) return null;
  const options = groups.map((g) => g.split("|"));
  let combos = 1;
  for (const o of options) combos *= o.length;
  if (combos > CHOICE_LIMIT) return null;
  const pieces = text.split(new RegExp(CHOICE_SRC));
  let names = [""];
  pieces.forEach((piece, k) => {
    const alts = k % 2 ? options[(k - 1) / 2] : [piece];
    names = names.flatMap((n) => alts.map((a) => n + a));
  });
  const infos = [];
  for (const name of names) {
    const [info] = pack.resolve(normalizeKey(unescapeText(name), pack.kaomoji));
    if (info === null || SYSTEM_CATEGORIES.includes(info.main)) return null;
    infos.push(info);
  }
  const shapes = new Set(infos.map((i) => JSON.stringify([i.primary, i.identity, sortedCp(i.regions)])));
  if (shapes.size !== 1) return null;
  let rating = "";
  for (const i of infos) {
    if (RATINGS.includes(i.rating) && RATINGS.indexOf(i.rating) > RATINGS.indexOf(rating)) rating = i.rating;
  }
  const first = infos[0];
  const merged = makeInfo({ key: normalizeKey(text, pack.kaomoji), type: TYPE_GUESS, count: 0, primary: first.primary,
    rating, secondary: [], regions: first.regions, identity: first.identity, status: "guess", display: text });
  return [merged, "choice:" + infos.map((i) => i.key).join("|")];
}

function classify(tokens, pack, opts) {
  for (const t of tokens) {
    if (t.kind === "natural" || t.kind === "comment") continue;
    if (t.kind === "special") {
      t.primary = "special";
      const hit = opts.input_dialect !== "nai" ? choiceInfo(t.text, pack) : null;
      if (hit !== null) {
        [t.info, t.guess] = hit;
        t.primary = t.info.primary;
      }
      continue;
    }
    if (t.opaque) {
      t.primary = "unknown";
      continue;
    }
    [t.prefix, t.core] = splitPrefix(t.text, opts.input_dialect, pack.kaomoji);
    const key = normalizeKey(t.core, pack.kaomoji);
    const [info, viaAlias] = pack.resolve(key);
    t.info = info;
    t.key = info !== null ? info.key : key;
    if (viaAlias) t.alias_of = key;
    if (info !== null) {
      t.kind = "tag";
      t.primary = info.primary;
    }
    if (t.prefix === "artist" && (info === null || info.main === "etc")) {
      t.kind = "tag";
      t.primary = "style/artist";
    } else if (info === null) {
      t.kind = "unknown";
      t.primary = "unknown";
    }
  }
}

function dedupe(tokens) {
  const seen = new Set();
  for (const t of tokens) {
    if (t.kind === "special" || t.kind === "comment") continue;
    const ident = t.kind === "tag" ? `${t.prefix}\0${t.key}` : `?\0${strip(t.text).toLowerCase()}`;
    if (seen.has(ident)) t.removed = "duplicate";
    else seen.add(ident);
  }
}

// ─── 추정(guess) ─────────────────────────────────────────────────

function patternSuffix(pattern) {
  const p = strip(String(pattern));
  if (p.toLowerCase().startsWith("re:") || !p.startsWith("*") || p.slice(1).includes("*")) return "";
  return normalizeKey(p.slice(1));
}

const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

// 사용자 정규식(re:): Python 의 이름 묶음 표기만 바꾼다. u 로 안 읽히는 표기(\- 등)는 u 없이 다시 읽는다.
function compileUserRegex(source) {
  const src = `^(?:${source.replace(/\(\?P</g, "(?<").replace(/\(\?P=(\w+)\)/g, "\\k<$1>")})$`;
  try {
    return new RegExp(src, "iu");
  } catch {
    return new RegExp(src, "i");
  }
}

function compilePattern(pattern) {
  const p = strip(String(pattern));
  if (p.toLowerCase().startsWith("re:")) return compileUserRegex(strip(p.slice(3)));
  const key = normalizeKey(p);
  if (!strip(key.replaceAll("*", ""))) throw new Error(`패턴에 글자가 없습니다: ${pattern}`);
  return new RegExp(`^(?:${key.split("*").map(escapeRegex).join("[^\\n]+")})$`, "u");
}

function categoryError(primary, categories) {
  const [main, , sub] = partition(primary, "/");
  if (!categories.has(main) || SYSTEM_CATEGORIES.includes(main)) return `알 수 없는 카테고리: ${primary}`;
  if (sub && !categories.get(main).has(sub)) return `알 수 없는 서브 카테고리: ${primary}`;
  return null;
}

function strList(value) {
  let items;
  if (typeof value === "string") items = value.split(/[,\n]/);
  else if (Array.isArray(value)) items = value;
  else items = value === null || value === undefined ? [] : [value];
  return items.map((v) => strip(String(v))).filter(Boolean);
}

function compileGuessRules(items, pack, where = "guess") {
  if (!Array.isArray(items)) throw new Error(`${where} 는 규칙 목록이어야 합니다`);
  const rules = [];
  const errors = [];
  const vocab = new Set((pack.rules || {}).regions || []);
  items.forEach((item, i) => {
    if (!isMapping(item)) {
      errors.push(`${where}[${i}] 는 {pattern, primary} 여야 합니다`);
      return;
    }
    const text = strip(String(item.pattern ?? ""));
    const primary = strip(String(item.primary ?? "")).toLowerCase();
    const err = primary ? categoryError(primary, pack.categories) : "primary 가 없습니다";
    if (err) {
      errors.push(`${where}[${i}] ${err}`);
      return;
    }
    let regex;
    try {
      regex = compilePattern(text);
    } catch (e) {
      errors.push(`${where}[${i}] 패턴이 잘못됐습니다: ${text} (${e.message})`);
      return;
    }
    const regions = strList(item.regions);
    const bad = regions.filter((r) => vocab.size && !vocab.has(r));
    if (bad.length) errors.push(`${where}[${i}] 없는 부위입니다: ${bad.join(", ")}`);
    const rating = String(item.rating || "");
    if (rating && !RATINGS.includes(rating)) errors.push(`${where}[${i}] rating 은 g/s/q/e 중 하나여야 합니다: ${rating}`);
    rules.push({ text, regex, primary, regions, rating, identity: Boolean(item.identity), suffix: patternSuffix(text) });
  });
  if (errors.length) throw new Error(errors.join("; "));
  return rules;
}

function packGuessRules(pack) {
  if (pack._guessRules === null) pack._guessRules = compileGuessRules(pack.guess.patterns || [], pack, "pack.guess");
  return pack._guessRules;
}

const isHeadType = (info) => info.type === 0 || info.type === TYPE_CUSTOM;

function headHit(words, pack, heads) {
  for (let i = 1; i < words.length; i++) {
    const [info] = pack.resolve(words.slice(i).join(" "));
    if (info !== null && isHeadType(info) && heads.has(info.main)) {
      return [info, [info.primary, info.regions, info.rating, info.identity, "head:" + info.key]];
    }
  }
  return null;
}

function speciesHit(words, pack) {
  const cfg = pack.guess.species || {};
  const primary = cfg.primary || "";
  if (!primary || words.length < 2 || !(cfg.suffixes || []).includes(words.at(-1))) return null;
  for (let i = 1; i < words.length - 1; i++) {
    const [info] = pack.resolve(words.slice(i).join(" "));
    if (info !== null && isHeadType(info) && info.primary === primary) {
      return [info.primary, info.regions, info.rating, info.identity, "species:" + info.key];
    }
  }
  const animals = new Set(cfg.animals || []);
  for (let i = 0; i < words.length - 1; i++) {
    const [info] = pack.resolve(words.slice(i, -1).join(" "));
    if (info !== null && isHeadType(info) && animals.has(info.primary)) return [primary, [], "", true, "animal:" + info.key];
  }
  return null;
}

function guessTokens(tokens, pack, rules, heads) {
  for (const t of tokens) {
    let key = t.key;
    if (t.kind === "special") {
      if (t.guess || !WILDCARD.test(t.text)) continue;
      key = normalizeKey(t.text.replace(WILDCARD_G, " * "));
      if (!strip(key.replaceAll("*", ""))) continue;
    } else if (t.kind !== "unknown" || t.opaque || !t.key) {
      continue;
    }
    const words = key.split(" ");
    const head = headHit(words, pack, heads);
    let hit = null;
    for (const rule of rules) {
      if (rule.regex.test(key)) {
        hit = [rule.primary, rule.regions, rule.rating, rule.identity, "pattern:" + rule.text];
        if (rule.suffix && head !== null && head[0].key.endsWith(" " + rule.suffix)) hit = head[1];
        break;
      }
    }
    if (hit === null && head !== null) hit = head[1];
    if (hit === null) hit = speciesHit(words, pack);
    if (hit === null) continue;
    const [primary, regions, rating, identity, why] = hit;
    t.primary = primary;
    t.guess = why;
    t.info = makeInfo({ key, type: TYPE_GUESS, count: 0, primary, rating, secondary: [], regions: [...regions], identity,
      status: "guess", display: t.core || t.text });
  }
}

// ─── 크롭·시점 덮어쓰기 ───────────────────────────────────────────

function overridesOf(pack, opts) {
  const out = [];
  for (const [group, target] of OVERRIDE_TARGETS) {
    const infos = [];
    for (const name of String(opts[group] || "").split(/[,\n]/)) {
      if (!strip(name)) continue;
      const [info] = pack.resolve(normalizeKey(name, pack.kaomoji));
      if (info === null || info.primary !== target) {
        throw new Error(`${group} 덮어쓰기는 ${target} 태그여야 합니다: ${strip(name)}`);
      }
      if (!infos.some((i) => i.key === info.key)) infos.push(info);
    }
    if (infos.length) out.push([group, target, infos]);
  }
  return out;
}

function applyOverrides(input, overrides, inject) {
  let tokens = input;
  for (const [group, target, infos] of overrides) {
    const keys = new Set(infos.map((i) => i.key));
    let pos = null;
    tokens.forEach((t, idx) => {
      if (t.removed || t.kind === "special" || t.primary !== target) return;
      pos = idx;
      if (!(t.kind === "tag" && keys.has(t.key))) t.removed = `override:${group}`;
    });
    if (pos === null && !inject) continue;
    const have = new Set(tokens.filter((t) => t.kind === "tag" && !t.removed).map((t) => t.key));
    const fresh = infos.filter((i) => !have.has(i.key)).map((i) =>
      makeToken({ text: i.display, kind: "tag", core: i.display, key: i.key, info: i, primary: i.primary, injected: group }));
    const at = pos === null ? tokens.length : pos + 1;
    tokens = [...tokens.slice(0, at), ...fresh, ...tokens.slice(at)];
  }
  return tokens;
}

function applyFilters(tokens, sels, opts) {
  const limit = RATINGS.indexOf(opts.max_rating);
  for (const t of tokens) {
    if (t.removed) continue;
    const cats = [t.primary];
    if (opts.match_secondary && t.info !== null) cats.push(...t.info.secondary);
    for (const sel of sels) {
      if (cats.some((c) => sel.matches(c, tokenIdentity(t)))) {
        t.removed = `exclude:${sel.text}`;
        break;
      }
    }
    if (t.removed) continue;
    const rating = t.info !== null ? t.info.rating : "";
    if (RATINGS.includes(rating) && RATINGS.indexOf(rating) > limit) t.removed = `rating:${rating}>${opts.max_rating}`;
  }
}

function act(t, mode, reason) {
  if (mode === "remove") t.removed = reason;
  else if (mode === "report" && !t.flagged) t.flagged = reason;
}

function isMultiSubject(present, rules) {
  const cfg = rules.multi_subject || {};
  if (anyIn(cfg.any, present)) return true;
  return (cfg.count_single || []).filter((x, i, a) => present.has(x) && a.indexOf(x) === i).length >= 2;
}

function applyRules(tokens, pack, opts) {
  const rules = pack.rules;
  const modes = ruleModes(opts);
  const alive = tokens.filter((t) => !t.removed);
  const present = new Set(alive.filter((t) => t.kind === "tag").map((t) => t.key));
  const multi = isMultiSubject(present, rules);

  const hidden = new Map();
  for (const group of ["framing", "view"]) {
    const mode = modes[group];
    const cfg = rules[group] || {};
    if (mode === "off" || anyIn(cfg.exempt, present)) continue;
    if (group === "view" && multi && (cfg.multi_subject_exempt ?? true)) continue;
    const active = (cfg.gates || []).filter((g) => anyIn(g.tags, present));
    if (!active.length) continue;
    let regions = new Set(active[0].hide || []);
    for (const g of active.slice(1)) {
      const next = new Set(g.hide || []);
      regions = new Set([...regions].filter((r) => next.has(r)));
    }
    for (const g of active) {
      for (const [tag, revealed] of Object.entries(g.reveal || {})) {
        if (present.has(tag)) for (const r of revealed) regions.delete(r);
      }
    }
    const gateTags = new Set(active.flatMap((g) => g.tags).filter((tag) => present.has(tag)));
    const gates = sortedCp(gateTags).join(", ");
    for (const region of regions) if (!hidden.has(region)) hidden.set(region, [group, mode, gates]);
  }

  if (hidden.size) {
    for (const t of alive) {
      if (t.info === null || !t.info.regions.length) continue;
      const regs = [...new Set(t.info.regions)];
      if (!regs.every((r) => hidden.has(r))) continue;
      const unique = new Map(regs.map((r) => [JSON.stringify(hidden.get(r)), hidden.get(r)]));
      const entries = [...unique.values()].sort((a, b) =>
        compareCp(a[0], b[0]) || compareCp(a[1], b[1]) || compareCp(a[2], b[2]));
      const mode = entries.every((e) => e[1] === "remove") ? "remove" : "report";
      act(t, mode, "rule:" + entries.map(([g, , gates]) => `${g}(${gates})`).join("; "));
    }
  }

  for (const rule of rules.explicit || []) {
    const rid = rule.id || "";
    const mode = opts.rule_overrides[rid] || ((rule.default ?? "on") !== "off" ? modes.explicit : "off");
    if (mode === "off") continue;
    if (!anyIn(rule.when_any, present) || anyIn(rule.unless_any, present)) continue;
    if (multi && rule.multi_subject_exempt) continue;
    const targets = new Set(rule.remove || []);
    const sels = parseSelectors(rule.remove_selectors || []);
    for (const t of alive) {
      if (t.removed || (t.kind !== "tag" && !t.guess)) continue;
      if (targets.has(t.key) || sels.some((s) => s.matches(t.primary, tokenIdentity(t)))) act(t, mode, `rule:${rid}`);
    }
  }
}

// ─── 배치·출력 ────────────────────────────────────────────────────

function fmtWeight(w) {
  return fixed(w, 3).replace(/0+$/, "").replace(/\.+$/, "");
}

function naiEmphasis(body, w) {
  for (let k = 1; k < 6; k++) {
    if (Math.abs(w - 1.05 ** k) < 1e-6) return "{".repeat(k) + body + "}".repeat(k);
    if (Math.abs(w - 1.05 ** -k) < 1e-6) return "[".repeat(k) + body + "]".repeat(k);
  }
  return `${fmtWeight(w)}::${body} ::`;
}

const VERBATIM_KINDS = ["special", "comment", "natural"];

function display(t, opts) {
  if (VERBATIM_KINDS.includes(t.kind) || t.opaque) return t.verbatim || t.text;
  if (t.info !== null) {
    if (t.alias_of && !opts.resolve_aliases) return t.alias_of;
    return t.info.display;
  }
  if (t.kind === "tag") return normalizeKey(t.core);
  return t.core || t.text;
}

function formatToken(t, opts, outDialect) {
  if (VERBATIM_KINDS.includes(t.kind) || t.opaque) return t.verbatim || t.text;
  if (t.kind === "unknown" && t.verbatim !== null && outDialect === opts.input_dialect) return t.verbatim;
  let body = display(t, opts);
  if (t.prefix === "artist") body = (outDialect === "anima" ? "@" : "artist:") + body;
  else if (t.prefix) body = t.prefix + body;
  if (outDialect !== "nai") body = escapeParens(body);
  if (Math.abs(t.weight - 1.0) > 1e-9) {
    if (outDialect === "nai") return naiEmphasis(body, t.weight);
    return `(${body}:${fmtWeight(t.weight)})`;
  }
  return body;
}

// ─── 배열 세팅·배치 ───────────────────────────────────────────────

const COLOR = /^#[0-9a-fA-F]{6}$/;
const CHUNK_ID = new RegExp("^[\\p{L}\\p{N}_.\\-]+$", "u");

function makeChunk(id, label, color, extra = {}) {
  return { id, label, color, members: [], tags: new Set(), patterns: [], hidden: false, excepts: [], ...extra };
}

function categoryMeta(taxonomy) {
  const meta = new Map();
  for (const c of taxonomy.categories || []) {
    meta.set(c.id, { label: c.label ?? c.id, color: c.color ?? "",
      subs: new Map((c.subs || []).map((s) => [s.id, s.label ?? s.id])) });
  }
  for (const s of taxonomy.system || []) {
    if (!meta.has(s.id)) meta.set(s.id, { label: s.label ?? s.id, color: s.color ?? "", subs: new Map() });
  }
  return meta;
}

function selectorLabel(sel, taxonomy) {
  const s = Selector.parse(sel);
  const meta = categoryMeta(taxonomy).get(s.main);
  let label;
  if (s.main === "*") label = "전부";
  else if (meta === undefined) label = s.text;
  else if (s.sub) label = meta.subs.get(s.sub) ?? s.sub;
  else label = meta.label;
  return label + (s.identity ? " (고유 외형)" : "");
}

function defaultLabel(members, taxonomy) {
  if (!members.length) return "";
  if (members.length === 1) return selectorLabel(members[0].text, taxonomy);
  const meta = categoryMeta(taxonomy);
  const mains = [...new Set(members.map((m) => m.main))];
  return mains.slice(0, 2).map((m) => (m === "*" ? "전부" : meta.get(m)?.label ?? m)).join("·");
}

function defaultColor(members, taxonomy) {
  const meta = categoryMeta(taxonomy);
  for (const m of members) if (meta.get(m.main)?.color) return meta.get(m.main).color;
  return meta.get("etc")?.color ?? "";
}

function restChunk(taxonomy, cid = REST_CHUNK) {
  return makeChunk(cid, "나머지", categoryMeta(taxonomy).get("etc")?.color ?? "");
}

function hasArrangement(value) {
  if (typeof value === "string") return Boolean(strip(value));
  if (isMapping(value)) return Object.keys(value).length > 0;
  return Array.isArray(value) ? value.length > 0 : Boolean(value);
}

function loadArrangement(input, pack) {
  let data = input;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch (e) {
      throw new Error(`배열 세팅 JSON 을 읽지 못했습니다: ${e.message}`);
    }
  }
  if (!isMapping(data)) throw new Error("배열 세팅은 JSON 객체여야 합니다.");
  if ((data.format ?? ARRANGEMENT_FORMAT) !== ARRANGEMENT_FORMAT) {
    throw new Error(`배열 세팅 형식이 아닙니다 (format != ${ARRANGEMENT_FORMAT}).`);
  }
  if ((data.version ?? ARRANGEMENT_VERSION) !== ARRANGEMENT_VERSION) {
    throw new Error(`지원하지 않는 배열 세팅 버전입니다: ${data.version} (지원: ${ARRANGEMENT_VERSION})`);
  }
  const specs = data.chunks || {};
  if (!isMapping(specs)) throw new Error("배열 세팅의 chunks 는 {id: 청크} 객체여야 합니다.");
  const rowsData = data.rows || [];
  if (!Array.isArray(rowsData)) throw new Error("배열 세팅의 rows 는 줄 목록이어야 합니다.");
  const errors = [];
  const rows = [];
  const placed = new Set();
  rowsData.forEach((row, ri) => {
    if (typeof row !== "string" && !Array.isArray(row)) {
      errors.push(`rows[${ri}] 는 청크 id 목록이어야 합니다`);
      return;
    }
    const out = [];
    for (const raw of typeof row === "string" ? [row] : row) {
      const cid = String(raw);
      if (!has(specs, cid)) {
        errors.push(`rows[${ri}] 에 정의하지 않은 청크가 있습니다: ${cid}`);
      } else if (placed.has(cid)) {
        errors.push(`청크를 두 번 배치했습니다: ${cid}`);
      } else {
        placed.add(cid);
        out.push(loadChunk(cid, specs[cid], pack, errors));
      }
    }
    rows.push(out);
  });
  let guess = [];
  try {
    guess = compileGuessRules(data.guess || [], pack, "guess");
  } catch (e) {
    errors.push(e.message);
  }
  if (errors.length) throw new Error("배열 세팅이 잘못됐습니다: " + errors.join("; "));
  const rest = restChunk(pack.taxonomy, placed.has(REST_CHUNK) ? "_" + REST_CHUNK : REST_CHUNK);
  return { rows, rest, label: String(data.label || ""), first_match: false, sub_order: Boolean(data.sub_order ?? true), guess };
}

function loadChunk(cid, spec, pack, errors) {
  const where = `chunks[${cid}]`;
  if (!CHUNK_ID.test(cid)) errors.push(`${where} id 는 글자·숫자·_ . - 만 씁니다`);
  if (!isMapping(spec)) {
    errors.push(`${where} 는 객체여야 합니다`);
    return makeChunk(cid, cid, "");
  }
  const members = parseSelectors(strList(spec.members));
  for (const sel of members) {
    const err = sel.validate(pack.categories);
    if (err) errors.push(`${where} ${err}`);
  }
  const tags = new Set(strList(spec.tags).map((t) => normalizeKey(t, pack.kaomoji)));
  const patterns = [];
  for (const p of strList(spec.patterns)) {
    try {
      patterns.push(compilePattern(p));
    } catch (e) {
      errors.push(`${where} 패턴이 잘못됐습니다: ${p} (${e.message})`);
    }
  }
  const color = String(spec.color || "");
  if (color && !COLOR.test(color)) errors.push(`${where} color 는 #rrggbb 여야 합니다: ${color}`);
  const label = String(spec.label || "") || defaultLabel(members, pack.taxonomy) || cid;
  return makeChunk(cid, label, color || defaultColor(members, pack.taxonomy),
    { members, tags, patterns, hidden: Boolean(spec.hidden) });
}

function layoutPlan(pack, name) {
  const rows = [];
  for (const sec of pack.layouts[name].sections) {
    const inc = parseSelectors(sec.include || []);
    const exc = parseSelectors(sec.except || []);
    rows.push([makeChunk(sec.id, sec.label ?? sec.id, defaultColor(inc, pack.taxonomy), { members: inc, excepts: exc })]);
  }
  return { rows, rest: restChunk(pack.taxonomy), label: pack.layouts[name].label ?? name, first_match: true,
    sub_order: false, guess: [] };
}

function specificity(sel) {
  return (sel.main === "*" ? 0 : sel.sub ? 2 : 1) + (sel.identity ? 0.5 : 0);
}

function place(tokens, plan, pack) {
  const flat = plan.rows.flat();
  const custom = flat.map((c, i) => [i, c]).filter(([, c]) => c.tags.size || c.patterns.length);
  for (const t of tokens) {
    let slot = -1;
    let member = 0;
    const names = [...new Set([t.key, t.alias_of, normalizeKey(t.core || t.text, pack.kaomoji)].filter(Boolean))];
    for (const [i, c] of custom) {
      if (names.some((n) => c.tags.has(n))) {
        slot = i;
        break;
      }
    }
    if (slot < 0) {
      for (const [i, c] of custom) {
        if (c.patterns.some((p) => names.some((n) => p.test(n)))) {
          slot = i;
          break;
        }
      }
    }
    const identity = tokenIdentity(t);
    if (slot < 0 && plan.first_match) {
      for (let i = 0; i < flat.length; i++) {
        const c = flat[i];
        if (c.members.some((s) => s.matches(t.primary, identity)) && !c.excepts.some((s) => s.matches(t.primary, identity))) {
          slot = i;
          break;
        }
      }
    } else if (slot < 0) {
      let best = null;
      flat.forEach((c, i) => {
        c.members.forEach((s, m) => {
          if (s.matches(t.primary, identity)) {
            const score = specificity(s);
            // 점수가 같으면 앞 줄·앞 청크·앞 멤버(먼저 본 것)가 이긴다
            if (best === null || score > best[0]) best = [score, i, m];
          }
        });
      });
      if (best !== null) [, slot, member] = best;
    }
    t.slot = slot;
    t.member = member;
    const chunk = slot >= 0 ? flat[slot] : plan.rest;
    t.section = chunk.id;
    if (chunk.hidden && !t.removed) t.removed = `hidden:${chunk.id}`;
  }
}

function sortedChunk(input, plan, opts) {
  let items = input;
  if (opts.order !== "input") {
    const sentences = items.filter((x) => x.kind === "natural");
    items = items.filter((x) => x.kind !== "natural");
    if (opts.order === "popular") {
      const count = (x) => (x.info !== null ? x.info.count : -1);
      items = [...items].sort((a, b) => count(b) - count(a));
    } else if (opts.order === "alpha") {
      items = [...items].sort((a, b) => compareCp(display(a, opts), display(b, opts)));
    }
    items = [...items, ...sentences];
  }
  if (plan.sub_order) items = [...items].sort((a, b) => a.member - b.member);
  return items;
}

function join(parts, sep, afterSentence = " ") {
  let out = "";
  parts.forEach(([text], k) => {
    if (k) out += parts[k - 1][1] ? afterSentence : sep;
    out += text;
  });
  return out;
}

function render(tokens, plan, opts, outDialect) {
  const bySlot = new Map();
  for (const t of tokens) {
    if (!bySlot.has(t.slot)) bySlot.set(t.slot, []);
    bySlot.get(t.slot).push(t);
  }
  const rows = [];
  let start = 0;
  for (const row of plan.rows) {
    rows.push(row.map((c, k) => [start + k, c]));
    start += row.length;
  }
  rows.push([[-1, plan.rest]]);
  const lines = [];
  const arranged = [];
  for (const row of rows) {
    const parts = [];
    const chunks = [];
    for (const [slot, c] of row) {
      const found = bySlot.get(slot);
      if (!found || !found.length) continue;
      const items = sortedChunk(found, plan, opts);
      const kept = items.filter((t) => !t.removed);
      if (kept.length) {
        const text = join(kept.map((t) => [formatToken(t, opts, outDialect), t.kind === "natural"]), ", ");
        parts.push([text, kept.at(-1).kind === "natural"]);
      }
      chunks.push({ id: c.id, label: c.label, color: c.color, hidden: c.hidden,
        custom: Boolean(c.tags.size || c.patterns.length), tokens: items.map((t) => t.index) });
    }
    if (parts.length) lines.push([join(parts, ", "), parts.at(-1)[1]]);
    if (chunks.length) arranged.push(chunks);
  }
  return [lines, arranged];
}

export function organize(text, pack, options = null) {
  let opts = coerceOptions(options);
  const sels = validateOptions(opts, pack);
  const plan = hasArrangement(opts.arrangement) ? loadArrangement(opts.arrangement, pack) : layoutPlan(pack, opts.layout);
  const overrides = overridesOf(pack, opts);
  const guessRules = opts.guess ? [...plan.guess, ...packGuessRules(pack)] : [];
  const heads = new Set(pack.guess.head_categories ?? DEFAULT_GUESS_HEADS);
  const [body, comments] = stripComments(text.replaceAll("\r\n", "\n"), knownFn(pack));
  let detected = null;
  if (opts.input_dialect === AUTO_DIALECT) {
    detected = detectDialect(body.replaceAll(COMMENT_MARK, ""), priorityOf(opts), pack.kaomoji, knownFn(pack));
    opts = { ...opts, input_dialect: detected.dialect };
  }
  const outDialect = opts.output_dialect || opts.input_dialect;

  let allTokens = [];
  const chunkTexts = [];
  const arranged = [];
  let taken = 0;
  body.split(new RegExp(BREAK_SRC, "u")).forEach((chunk, ci) => {
    const count = chunk.split(COMMENT_MARK).length - 1;
    const notes = comments.slice(taken, taken + count).map((c) => makeToken({ text: c, verbatim: c, kind: "comment",
      primary: "comment", section: "comment", removed: opts.comments === "keep" ? "" : "comment" }));
    taken += count;
    let tokens = parsePrompt(chunk.replaceAll(COMMENT_MARK, ""), opts.input_dialect, pack, opts.sentences);
    classify(tokens, pack, opts);
    if (opts.guess) guessTokens(tokens, pack, guessRules, heads);
    if (overrides.length) tokens = applyOverrides(tokens, overrides, ci === 0);
    [...notes, ...tokens].forEach((t, i) => {
      t.chunk = ci;
      t.order = allTokens.length + i;
      t.index = allTokens.length + i;
    });
    if (opts.dedupe) dedupe(tokens);
    applyFilters(tokens, sels, opts);
    applyRules(tokens, pack, opts);
    place(tokens, plan, pack);
    const [lines, renderedRows] = render(tokens, plan, opts, outDialect);
    let rows = renderedRows;
    let out = opts.line_per_section ? join(lines, opts.trailing_comma ? ",\n" : "\n", "\n") : join(lines, ", ");
    const kept = notes.filter((t) => !t.removed).map((t) => t.text);
    if (kept.length) {
      out = out ? kept.join("\n") + "\n" + out : kept.join("\n") + (opts.line_per_section ? "" : "\n");
    }
    if (out) chunkTexts.push(out);
    if (notes.length) {
      rows = [[{ id: "comment", label: "주석", color: "", hidden: false, custom: false, tokens: notes.map((t) => t.index) }], ...rows];
    }
    arranged.push({ rows });
    allTokens = [...allTokens, ...notes, ...tokens];
  });

  const joiner = opts.line_per_section ? "\nBREAK\n" : " BREAK ";
  const removedText = allTokens.filter((t) => t.removed).map((t) => `${display(t, opts)}  # ${t.removed}`).join("\n");
  const report = makeReport(allTokens, pack, opts, outDialect, detected);
  report.arrangement = hasArrangement(opts.arrangement) ? plan.label : "";
  report.arranged = arranged;
  return { text: chunkTexts.join(joiner), removed_text: removedText, report, tokens: allTokens };
}

function makeReport(tokens, pack, opts, outDialect, detected) {
  const rows = tokens.map((t) => {
    const row = { text: display(t, opts), kind: t.kind, primary: t.primary, section: t.section,
      out: formatToken(t, opts, outDialect) };
    if (t.info !== null && !t.guess) {
      Object.assign(row, { key: t.key, rating: t.info.rating, status: t.info.status, count: t.info.count });
      if (t.info.db) row.db = t.info.db;
      if (t.info.site) row.site = t.info.site;
    }
    if (Math.abs(t.weight - 1.0) > 1e-9) row.weight = Number(fixed(t.weight, 4));
    for (const name of ["prefix", "alias_of", "removed", "flagged", "guess", "injected", "warning"]) {
      if (t[name]) row[name] = t[name];
    }
    return row;
  });
  const unknown = rows.filter((r) => r.kind === "unknown").map((r) => r.text);
  const unclassified = rows.filter((r) => r.primary === "etc").map((r) => r.text);
  const dialect = { input: opts.input_dialect, output: outDialect, auto: detected !== null };
  if (detected) Object.assign(dialect, { evidence: detected.evidence, counts: detected.counts, conflict: detected.conflict });
  const count = (fn) => tokens.filter(fn).length;
  return {
    pack_version: pack.version,
    preset: opts.preset,
    layout: opts.layout,
    dialect,
    counts: {
      total: rows.length,
      kept: count((t) => !t.removed),
      removed: count((t) => t.removed),
      flagged: count((t) => t.flagged),
      unknown: unknown.length,
      unclassified: unclassified.length,
      guessed: count((t) => t.guess),
      warnings: count((t) => t.warning),
      comments: count((t) => t.kind === "comment"),
      sentences: count((t) => t.kind === "natural"),
    },
    unknown,
    unclassified,
    guessed: rows.filter((r) => has(r, "guess")).map((r) => ({ tag: r.text, primary: r.primary, why: r.guess })),
    removed: rows.filter((r) => has(r, "removed")).map((r) => ({ tag: r.text, reason: r.removed })),
    flagged: rows.filter((r) => has(r, "flagged")).map((r) => ({ tag: r.text, reason: r.flagged })),
    warnings: rows.filter((r) => has(r, "warning")).map((r) => ({ tag: r.text, reason: r.warning })),
    tokens: rows,
  };
}

// ─── 편집기용 ─────────────────────────────────────────────────────

export function palette(pack) {
  const tax = pack.taxonomy;
  const cats = (tax.categories ?? []).map((c) => ({ id: c.id, label: c.label ?? c.id, color: c.color ?? "",
    subs: (c.subs ?? []).map((s) => ({ id: s.id, label: s.label ?? s.id })) }));
  cats.push(...(tax.system ?? []).map((s) => ({ id: s.id, label: s.label ?? s.id, color: s.color ?? "", subs: [], system: true })));
  const quick = pack.rules?.quick || {};
  return { format: PALETTE_FORMAT, categories: cats,
    quick: Object.fromEntries(OVERRIDE_TARGETS.map(([group]) => [group, [...(quick[group] ?? [])]])),
    layouts: Object.fromEntries(Object.entries(pack.layouts).map(([lid, lay]) => [lid, lay.label ?? lid])) };
}

function categorySlots(taxonomy) {
  const out = [];
  for (const c of taxonomy.categories ?? []) {
    for (const s of c.subs ?? []) out.push(`${c.id}/${s.id}`);
    out.push(c.id);
  }
  return out.concat(SYSTEM_CATEGORIES);
}

// 레이아웃 프리셋 → 같은 결과를 내는 배열 세팅(편집을 시작할 때). 자세한 규칙은 Python 코어의 설명 참고.
export function arrangementFromLayout(pack, layout = "default", subOrder = false) {
  const spec = pack.layouts[layout];
  const slots = categorySlots(pack.taxonomy);
  const claimed = new Set();
  const rows = [];
  const chunks = {};
  for (const sec of spec.sections) {
    const exc = parseSelectors(sec.except ?? []);
    const got = new Map();     // 이 섹션이 가져간 칸 → (나머지 false, 고유 외형 true)
    for (const sel of parseSelectors(sec.include ?? [])) {
      for (const slot of slots) {
        for (const ident of [false, true]) {
          const key = `${slot}|${ident}`;
          if (claimed.has(key) || !sel.matches(slot, ident) || exc.some((e) => e.matches(slot, ident))) continue;
          claimed.add(key);
          if (!got.has(slot)) got.set(slot, new Set());
          got.get(slot).add(ident);
        }
      }
    }
    const members = [...got].map(([slot, idents]) => (idents.has(false) ? slot : `${slot}@identity`));
    if (members.length) {
      chunks[sec.id] = { label: sec.label ?? sec.id, members };
      rows.push([sec.id]);
    }
  }
  return { format: ARRANGEMENT_FORMAT, version: ARRANGEMENT_VERSION, label: spec.label ?? layout, sub_order: subOrder, rows, chunks };
}
