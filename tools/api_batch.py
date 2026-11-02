"""Claude Message Batches 로 LLM 배치 분류 — 사용법은 docs/api-batch.md.

    plan    배치 CSV → 요청(requests.jsonl)과 토큰·비용 추정. API 를 부르지 않고 SDK 도 필요 없다(--count 만 예외).
    submit  배치를 만들고 manifest.json 에 batch id 를 적는다. 같은 run·단계는 한 번만(--resubmit 으로만 다시).
    status  진행 상황.
    collect 결과 → work/results/<run>/ CSV, 원응답·사용량, 원장 한 줄, 다시 보낼 태그(retry.csv).
    sample  파일럿용 표본 배치(기존 배치 파일에서 보류·분류 비율을 지켜 뽑음).

단계는 classify(분류)와 review(검토 — 분류 결과를 보고 고칠 행만 낸다) 둘이다. 결과 CSV 는 batch.RESULT_FIELDS 형식이라
`batch apply --dry-run --batch` 관문을 그대로 쓴다. 키는 local/anthropic.env 에서만 읽고, 환경 변수의 다른 자격 증명·주소는 쓰지 않는다.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import bmk_tag_organizer_core as core

from . import batch, tagdb
from .builder import category_ids

WORK = tagdb.ROOT / "work"
KEY_FILE = tagdb.ROOT / "local" / "anthropic.env"
LEDGER = tagdb.ROOT / "local" / "api" / "ledger.csv"
GUIDE = tagdb.ROOT / "docs" / "classification-guide.md"

API_BASE = "https://api.anthropic.com"
# SDK 초기화 시 외부 세션의 인증·라우팅 설정을 물려받지 않도록 제외한다
IGNORED_ENV = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE")

STAGES = ("classify", "review")
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# USD / 100만 토큰: (입력, 출력, 캐시 읽기 배수). 공개 표준 단가 2026-10-06 — 바뀌면 여기만 고친다. Batch 는 전부 절반.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.1),
    "claude-sonnet-5-5": (2.00, 10.00, 0.1),
    "claude-opus-5-5": (4.00, 20.00, 0.05),
}
HAIKU_LONG = (0.50, 2.50)       # Haiku 5.5 는 프롬프트가 10만 토큰을 넘으면 이 단가
LONG_PROMPT = 100_000
CACHE_WRITE_1H = 2.0
BATCH_DISCOUNT = 0.5
EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINK_PER_TAG = {"low": 50, "medium": 150, "high": 300, "xhigh": 600, "max": 1000}   # 가정 — 파일럿에서 실측
OUT_PER_ROW = 70                # 결과 1행(JSON) 토큰
REVIEW_FIX_SHARE = 0.05         # 검토가 고칠 행의 비율(E1: 2,050개 중 27건)
ASCII_PER_TOKEN = 2.7           # 토큰 추정: ASCII 2.7자, 그 밖(한글 등) 1자 = 1토큰(보수적)
PRECEDENTS_PER_HEAD = 5
CONFIRMED = ("accepted", "reviewed")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PART_SEP = "__"                 # custom_id = <배치 파일 이름>__<번호>
LEDGER_FIELDS = ("date", "run", "stage", "model", "batch_id", "requests", "succeeded", "failed_tags",
                 "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "usd")


class ApiError(RuntimeError):
    pass


# ---------------------------------------------------------------- 경로


def run_dir(run: str, stage: str, work: Optional[Path] = None) -> Path:
    return (work or WORK) / "api" / run / stage


def results_dir(run: str, work: Optional[Path] = None) -> Path:
    return (work or WORK) / "results" / run


def check_run_name(run: str) -> None:
    if not NAME_RE.match(run) or len(run) > 40:
        raise ApiError(f"run 이름은 영숫자·-·_ 40자 이하여야 합니다: {run!r}")


def resolve_model(name: str) -> str:
    model = MODELS.get(name, name)
    if model not in PRICES:
        raise ApiError(f"단가를 모르는 모델입니다: {name} (쓸 수 있는 것: {', '.join(MODELS)} 또는 {', '.join(PRICES)})")
    return model


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 요청 만들기


def estimate_tokens(text: str) -> int:
    n_ascii = sum(1 for ch in text if ord(ch) < 128)
    return math.ceil(round(n_ascii / ASCII_PER_TOKEN + (len(text) - n_ascii), 6))


def taxonomy_text(taxonomy: Mapping[str, Any]) -> str:
    lines = []
    for c in taxonomy["categories"]:
        rated = " · 등급 대상" if c.get("rated") else ""
        lines.append(f"- {c['id']} ({c.get('label', '')}){rated}: {c.get('desc', '')}")
        for s in c.get("subs", ()):
            lines.append(f"  - {c['id']}/{s['id']} ({s.get('label', '')}): {s.get('desc', '')}")
    lines.append("- etc: 분류 보류(뜻이 불확실 — 지침 §7). 다른 칸은 비운다.")
    return "\n".join(lines)


CLASSIFY_ROLE = (
    "너는 Danbooru general 태그 분류자다. 아래 지침이 판단 기준이고, 분류 체계 목록의 id 만 쓴다.\n"
    "사용자 메시지에는 같은 계열의 확정 분류(선례)와 분류할 태그 배치가 CSV 로 온다. "
    "배치의 wiki·other_names·implies·related·related_series 칸은 Danbooru 에서 받은 증거다(지침 §7 '증거로 다시 분류할 때')."
)
OUTPUT_RULES = """## 출력
- 배치의 모든 태그를 입력 순서대로 한 행씩 rows 에 담는다. name 은 입력 그대로(밑줄 포함) 쓴다.
- secondary·regions 는 배열(없으면 []), rating·identity 는 해당이 없으면 "", confidence 는 0.5~1.0.
- 보류(etc)면 secondary·regions 는 [], rating·identity 는 "", confidence 는 null, note 는 `의미 불확실`(짐작이 있으면 `의미 불확실 · …`).
- note 는 판단이 애매하거나 증거로 정했을 때 근거를 한국어 한 줄로 짧게 쓴다(`위키: …`, `연관: pencil_skirt 0.80 → 의상`). 없으면 "".
- 선례는 같은 계열의 확정 분류다. 지침 §6 '선례 우선'대로 맞추고, 배치 안의 같은 계열 태그끼리도 같은 방식으로 맞춘다."""
REVIEW_RULES = """## 검토자 역할
너는 분류자가 낸 결과를 검토한다. 분류를 처음부터 다시 하는 것이 아니라 고칠 행만 낸다.
- 지침·보강 규칙(§6)·선례와 어긋나는 행만 rows 에 모든 칸을 채운 고친 값으로 담고, reason 에 이유를 한국어 한 줄로 쓴다. 맞는 행은 내지 않는다. 고칠 것이 없으면 rows 는 [].
- 뜻을 아는데 보류(etc)했거나, 위키·연관 근거 없이 이름만 보고 추측해 분류했는지 본다(근거가 없으면 보류가 맞다).
- 지침 §7 의 0.7 기준은 글자 그대로 적용한다(0.69 는 미달, 다른 태그와 합산하지 않음).
- 등급은 §2·§6 기준으로, regions 는 §3 표대로 본다. 형식 규칙은 위 '출력' 절과 같다."""


def system_blocks(guide: str, taxonomy: Mapping[str, Any], regions: Sequence[str], stage: str) -> List[Dict[str, Any]]:
    """요청마다 같은 바이트여야 캐시가 맞는다 — 날짜·run 이름 같은 바뀌는 값을 넣지 않는다."""
    tail = [f"## 분류 체계\n{taxonomy_text(taxonomy)}", f"## 부위 어휘\n{', '.join(regions)}", OUTPUT_RULES]
    if stage == "review":
        tail.append(REVIEW_RULES)
    return [
        {"type": "text", "text": f"{CLASSIFY_ROLE}\n\n{guide.strip()}"},
        {"type": "text", "text": "\n\n".join(tail), "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


def output_schema(taxonomy: Mapping[str, Any], regions: Sequence[str], review: bool = False) -> Dict[str, Any]:
    cats = []
    for c in taxonomy["categories"]:
        cats.append(c["id"])
        cats += [f"{c['id']}/{s['id']}" for s in c.get("subs", ())]
    props: Dict[str, Any] = {
        "name": {"type": "string"},
        "primary": {"type": "string", "enum": cats + ["etc"]},
        "secondary": {"type": "array", "items": {"type": "string", "enum": cats}},
        "rating": {"type": "string", "enum": ["", *core.RATINGS]},
        "regions": {"type": "array", "items": {"type": "string", "enum": list(regions)}},
        "identity": {"type": "string", "enum": ["", "0", "1"]},
        "confidence": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "note": {"type": "string"},
    }
    if review:
        props["reason"] = {"type": "string"}
    row = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return {"type": "object", "properties": {"rows": {"type": "array", "items": row}},
            "required": ["rows"], "additionalProperties": False}


def read_batch(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
        fields = list(reader.fieldnames or ())
    if "name" not in fields:
        raise ApiError(f"{path.name} 에 name 열이 없습니다")
    return fields, rows


def _csv_text(fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(fields)
    for r in rows:
        w.writerow([r.get(k, "") for k in fields])
    return buf.getvalue()


PRECEDENT_FIELDS = ("name", "primary", "secondary", "rating", "regions", "identity")


def precedent_index(overlay: Mapping[str, Mapping[str, str]], hidden: Set[str]) -> Dict[str, List[Mapping[str, str]]]:
    """계열(batch.head_key) → 확정·비보류 행(이름 순). hidden(이번에 분류하는 태그)은 답이 새지 않게 뺀다."""
    index: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    for name in sorted(overlay):
        row = overlay[name]
        primary = row.get("primary") or ""
        if name in hidden or row.get("status") not in CONFIRMED or not primary or primary in core.SYSTEM_CATEGORIES:
            continue
        index[batch.head_key(name)].append(row)
    return index


def precedents_for(names: Sequence[str], index: Mapping[str, List[Mapping[str, str]]],
                   per_head: int = PRECEDENTS_PER_HEAD) -> List[Mapping[str, str]]:
    out: List[Mapping[str, str]] = []
    for head in sorted({batch.head_key(n) for n in names}):
        out += index.get(head, [])[:per_head]
    return out


def classify_user_text(fields: Sequence[str], rows: Sequence[Mapping[str, str]],
                       precedents: Sequence[Mapping[str, str]]) -> str:
    prec = _csv_text(PRECEDENT_FIELDS, precedents) if precedents else "(없음)\n"
    return (f"## 선례 (같은 계열의 확정 분류)\n{prec}\n"
            f"## 분류할 태그 {len(rows)}개\n{_csv_text(fields, rows)}\n"
            "모든 태그를 입력 순서대로 한 행씩 rows 에 담는다.")


def review_user_text(fields: Sequence[str], rows: Sequence[Mapping[str, str]],
                     precedents: Sequence[Mapping[str, str]], results: Sequence[Mapping[str, str]]) -> str:
    prec = _csv_text(PRECEDENT_FIELDS, precedents) if precedents else "(없음)\n"
    return (f"## 선례 (같은 계열의 확정 분류)\n{prec}\n"
            f"## 배치 {len(rows)}개 (증거 포함)\n{_csv_text(fields, rows)}\n"
            f"## 분류자 결과\n{_csv_text(batch.RESULT_FIELDS, results)}\n"
            "고칠 행만 rows 에 담는다(고칠 것이 없으면 빈 배열).")


def request_params(model: str, effort: str, max_tokens: int, system: List[Dict[str, Any]], user_text: str,
                   schema: Mapping[str, Any]) -> Dict[str, Any]:
    # thinking 은 생략(adaptive 기본), temperature 는 보내지 않는다(Haiku 5.5 는 400)
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }


def request_tokens(params: Mapping[str, Any]) -> int:
    text = "".join(b["text"] for b in params["system"]) + params["messages"][0]["content"]
    schema = json.dumps(params["output_config"]["format"]["schema"], ensure_ascii=False)
    return estimate_tokens(text) + estimate_tokens(schema)


def batch_usd(model: str, input_tokens: float, output_tokens: float, cache_read: float = 0.0,
              cache_write: float = 0.0) -> float:
    """Batch 단가(절반) 환산. 한 요청 단위로 부른다 — Haiku 는 프롬프트 길이로 단가표가 갈린다."""
    in_rate, out_rate, read_mult = PRICES[model]
    if model == MODELS["haiku"] and input_tokens + cache_read + cache_write > LONG_PROMPT:
        in_rate, out_rate = HAIKU_LONG
    usd = (input_tokens * in_rate + cache_read * in_rate * read_mult + cache_write * in_rate * CACHE_WRITE_1H
           + output_tokens * out_rate) / 1_000_000
    return usd * BATCH_DISCOUNT


def _plan_skeleton(run: str, stage: str, model: str, effort: str, max_tokens: int) -> Dict[str, Any]:
    if effort not in EFFORTS:
        raise ApiError(f"effort 는 {', '.join(EFFORTS)} 중 하나입니다: {effort}")
    return {"run": run, "stage": stage, "model": model, "effort": effort, "max_tokens": max_tokens,
            "created": _now(), "parts": [], "requests": []}


def _finish_estimate(plan: Dict[str, Any]) -> Dict[str, Any]:
    """입력은 최악(어느 요청도 캐시를 못 맞혀 system 몫을 매번 1시간 캐시 쓰기 = 입력 단가 2배)으로 잡는다.
    usd 는 출력을 태그 수로 추정한 예상값, max_usd 는 요청마다 출력이 max_tokens 까지 다 나온 경우의 상한이다
    — submit 의 --max-usd 는 max_usd 와 비교한다. 입력을 글자 수로 추정했으면(--count 없이) 상한도 추정이다."""
    think = THINK_PER_TAG[plan["effort"]]
    per_row = OUT_PER_ROW * (REVIEW_FIX_SHARE if plan["stage"] == "review" else 1)
    total_in = total_out = total_write = usd = max_usd = 0.0
    for part, req in zip(plan["parts"], plan["requests"]):
        tin = part.get("counted_tokens") or request_tokens(req["params"])
        cached = min(tin, estimate_tokens("".join(b["text"] for b in req["params"]["system"])))
        tout = math.ceil(len(part["names"]) * (per_row + think))
        part["est_input_tokens"], part["est_output_tokens"] = tin, tout
        total_in += tin
        total_write += cached
        total_out += tout
        usd += batch_usd(plan["model"], tin - cached, tout, cache_write=cached)
        max_usd += batch_usd(plan["model"], tin - cached, req["params"]["max_tokens"], cache_write=cached)
    plan["estimate"] = {"input_tokens": int(total_in), "cache_write_tokens": int(total_write),
                        "output_tokens": int(total_out), "usd": round(usd, 4), "max_usd": round(max_usd, 4),
                        "tags": sum(len(p["names"]) for p in plan["parts"]),
                        "counted": all(p.get("counted_tokens") for p in plan["parts"]) if plan["parts"] else False}
    return plan


def plan_classify(run: str, files: Sequence[Path], overlay: Mapping[str, Mapping[str, str]],
                  config: Mapping[str, Any], guide: str, model: str = "claude-haiku-5-5", effort: str = "medium",
                  max_tokens: int = 16000, chunk: int = 50) -> Dict[str, Any]:
    check_run_name(run)
    plan = _plan_skeleton(run, "classify", model, effort, max_tokens)
    regions = list(config["rules"].get("regions", ()))
    system = system_blocks(guide, config["taxonomy"], regions, "classify")
    schema = output_schema(config["taxonomy"], regions)
    loaded = []
    seen: Dict[str, str] = {}
    stems: Dict[str, Path] = {}
    for path in files:
        fields, rows = read_batch(path)
        if not NAME_RE.match(path.stem) or len(path.stem) > 50:
            raise ApiError(f"배치 파일 이름은 영숫자·-·_ 50자 이하여야 합니다(custom_id 로 씀): {path.name}")
        stem_key = path.stem.casefold()              # Windows 는 대소문자만 다른 파일 이름이 같은 파일이다
        if stem_key in stems:
            # 요청 id(<이름>__n)와 결과 파일(<이름>.csv)이 겹친다 — 예: 서로 다른 run 의 retry.csv 둘
            raise ApiError(f"이름이 같은 배치 파일이 둘 있습니다: {stems[stem_key]}, {path} — 하나의 이름을 바꾸세요")
        stems[stem_key] = path
        for r in rows:
            if r["name"] in seen:
                raise ApiError(f"같은 태그가 두 배치에 있습니다: {r['name']} ({seen[r['name']]}, {path.name})")
            seen[r["name"]] = path.name
        loaded.append((path, fields, rows))
    if not seen:
        raise ApiError("분류할 태그가 없습니다")
    index = precedent_index(overlay, set(seen))
    for path, fields, rows in loaded:
        for i in range(0, len(rows), chunk):
            part_rows = rows[i:i + chunk]
            names = [r["name"] for r in part_rows]
            custom_id = f"{path.stem}{PART_SEP}{i // chunk + 1}"
            text = classify_user_text(fields, part_rows, precedents_for(names, index))
            plan["parts"].append({"custom_id": custom_id, "file": str(path.resolve()), "stem": path.stem,
                                  "names": names})
            plan["requests"].append({"custom_id": custom_id,
                                     "params": request_params(model, effort, max_tokens, system, text, schema)})
    return _finish_estimate(plan)


def plan_review(run: str, overlay: Mapping[str, Mapping[str, str]], config: Mapping[str, Any], guide: str,
                model: str = "claude-sonnet-5-5", effort: str = "medium", max_tokens: int = 16000,
                work: Optional[Path] = None) -> Tuple[Dict[str, Any], List[str]]:
    """분류 단계의 요청 묶음을 그대로 따라 검토 요청을 만든다. (계획, 분류 결과가 없어 뺀 태그)."""
    check_run_name(run)
    base = read_json(run_dir(run, "classify", work) / "plan.json")
    if not (run_dir(run, "classify", work) / "manifest.json").exists():
        raise ApiError(f"{run} 의 분류 단계를 아직 제출하지 않았습니다")
    plan = _plan_skeleton(run, "review", model, effort, max_tokens)
    regions = list(config["rules"].get("regions", ()))
    system = system_blocks(guide, config["taxonomy"], regions, "review")
    schema = output_schema(config["taxonomy"], regions, review=True)
    results: Dict[str, Dict[str, str]] = {}
    for stem in sorted({p["stem"] for p in base["parts"]}):
        path = results_dir(run, work) / f"{stem}.csv"
        if not path.exists():
            raise ApiError(f"분류 결과가 없습니다: {path} — 먼저 api collect {run}")
        for r in batch.read_results([path]):
            results[r["name"]] = {k: r[k] for k in batch.RESULT_FIELDS}
    hidden = {n for p in base["parts"] for n in p["names"]}
    index = precedent_index(overlay, hidden)
    batch_rows: Dict[str, Tuple[List[str], Dict[str, Dict[str, str]]]] = {}
    skipped: List[str] = []
    for part in base["parts"]:
        if part["file"] not in batch_rows:
            fields, rows = read_batch(Path(part["file"]))
            batch_rows[part["file"]] = (fields, {r["name"]: r for r in rows})
        fields, by_name = batch_rows[part["file"]]
        names = [n for n in part["names"] if n in results and n in by_name]
        skipped += [n for n in part["names"] if n not in names]
        if not names:
            continue
        text = review_user_text(fields, [by_name[n] for n in names], precedents_for(names, index),
                                [results[n] for n in names])
        # base: 검토자에게 보여 준 분류 결과 — collect 는 지금 CSV 가 아니라 이 값에 수정을 덮는다
        plan["parts"].append({"custom_id": part["custom_id"], "file": part["file"], "stem": part["stem"],
                              "names": names, "base": {n: results[n] for n in names}})
        plan["requests"].append({"custom_id": part["custom_id"],
                                 "params": request_params(model, effort, max_tokens, system, text, schema)})
    if not plan["parts"]:
        raise ApiError("검토할 분류 결과가 없습니다")
    return _finish_estimate(plan), skipped


def count_plan(plan: Dict[str, Any], client: Any) -> Dict[str, Any]:
    """count_tokens(무료)로 요청마다 입력 토큰을 정확히 잰다. 출력은 여전히 추정이다."""
    for part, req in zip(plan["parts"], plan["requests"]):
        p = req["params"]
        res = client.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"],
                                           extra_body={"output_config": {"format": p["output_config"]["format"]}})
        part["counted_tokens"] = int(res.input_tokens)
    return _finish_estimate(plan)


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ApiError(f"파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


LOCK_NAME = "submit.lock"


@contextlib.contextmanager
def _locked(d: Path, run: str, stage: str):
    """run·단계 하나의 상태 파일(plan·requests·pending·manifest)을 바꾸는 plan·submit·adopt 는 이 잠금 안에서만 한다."""
    d.mkdir(parents=True, exist_ok=True)
    lock = d / LOCK_NAME
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise ApiError(f"같은 run 의 다른 plan·submit·adopt 가 진행 중이거나 비정상 종료했습니다: {lock} — 진행 중이 아니면 "
                       "이 파일을 지우세요(submit 중이었다면 먼저 " + ADOPT_HINT.format(run=run, stage=stage) + ")") from None
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def check_unsubmitted(run: str, stage: str, work: Optional[Path] = None, include_lock: bool = True) -> None:
    d = run_dir(run, stage, work)
    for name in ("manifest.json", "pending.json") + ((LOCK_NAME,) if include_lock else ()):
        if (d / name).exists():
            raise ApiError(f"{run} 의 {stage} 단계는 이미 제출했거나 제출 중입니다({d / name}). "
                           "계획을 바꾸려면 새 run 이름을 쓰세요")


def write_plan(plan: Mapping[str, Any], work: Optional[Path] = None) -> Path:
    """requests.jsonl 의 sha256 을 plan.json 에 적는다 — submit 은 둘이 맞을 때만 낸다(중간에 끊긴 저장 대비)."""
    d = run_dir(plan["run"], plan["stage"], work)
    with _locked(d, plan["run"], plan["stage"]):
        check_unsubmitted(plan["run"], plan["stage"], work, include_lock=False)
        data = "".join(json.dumps(req, ensure_ascii=False) + "\n" for req in plan["requests"]).encode("utf-8")
        tmp = d / "requests.jsonl.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / "requests.jsonl")
        saved = {k: v for k, v in plan.items() if k != "requests"}
        saved["requests_sha256"] = hashlib.sha256(data).hexdigest()
        _write_json(d / "plan.json", saved)
    return d


def format_plan(plan: Mapping[str, Any], work: Optional[Path] = None) -> str:
    e = plan["estimate"]
    stage = "분류" if plan["stage"] == "classify" else "검토"
    how = "count_tokens 로 잼" if e["counted"] else "글자 수로 추정"
    lines = [
        f"run {plan['run']} · {stage} · {plan['model']} (effort {plan['effort']}) · 요청 {len(plan['parts'])}개 · 태그 {e['tags']:,}개",
        f"입력 약 {e['input_tokens']:,} 토큰({how}) · 출력(사고 포함, 추정) 약 {e['output_tokens']:,}",
        f"예상 ${e['usd']:.2f} · 최대 ${e['max_usd']:.2f}(요청마다 출력이 max_tokens {plan['max_tokens']:,} 까지 나올 때) — "
        f"Batch 50% 할인, 캐시를 하나도 못 맞힌다고 가정(system 약 {e['cache_write_tokens']:,} 토큰은 1시간 캐시 쓰기 2배)",
        f"요청: {run_dir(plan['run'], plan['stage'], work) / 'requests.jsonl'}",
        f"제출: python tagorg.py api submit {plan['run']} --stage {plan['stage']} --max-usd <최대 이상의 상한>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 키·클라이언트


def read_api_key(path: Optional[Path] = None) -> str:
    path = path or KEY_FILE
    if not path.exists():
        raise ApiError(f"키 파일이 없습니다: {path}\n"
                       "  한 줄로 ANTHROPIC_API_KEY=sk-ant-… 를 적습니다. "
                       "local/ 은 git 에 올라가지 않습니다.")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "ANTHROPIC_API_KEY" and value.strip():
            return value.strip().strip("\"'")
    raise ApiError(f"{path} 에 ANTHROPIC_API_KEY= 줄이 없습니다")


def make_client(api_key: str) -> Any:
    try:
        import anthropic
    except ImportError as e:
        raise ApiError("anthropic SDK 가 없습니다 — pip install -r requirements-api.txt") from e
    # 키·주소는 이 도구가 정한 것만 쓴다. SDK 가 생성자에서 읽는 환경 변수(다른 계정 토큰·프록시 주소)를 잠시 치운다
    saved = {k: os.environ.pop(k) for k in IGNORED_ENV if k in os.environ}
    try:
        return anthropic.Anthropic(api_key=api_key, base_url=API_BASE, max_retries=3)
    finally:
        os.environ.update(saved)


# ---------------------------------------------------------------- 제출·상태


ADOPT_HINT = ("Console(Batches)에서 그 시각에 배치가 만들어졌는지 확인하세요 — 있으면 `api adopt {run} <batch id> "
              "--stage {stage}`, 없으면 `api submit {run} --stage {stage} --resubmit …`")


def _check_submittable(d: Path, run: str, stage: str, resubmit: bool) -> None:
    manifest_path, pending_path = d / "manifest.json", d / "pending.json"
    if manifest_path.exists() and not resubmit:
        raise ApiError(f"이미 제출했습니다: {read_json(manifest_path).get('batch_id')} — 다시 내려면 --resubmit "
                       "(먼저 낸 배치도 과금됩니다)")
    if pending_path.exists() and not resubmit:
        p = read_json(pending_path)
        known = f"batch id {p['batch_id']}" if p.get("batch_id") else "결과 모름"
        raise ApiError(f"지난 제출({p.get('started')}, {p.get('error') or '중단됨'})이 끝나지 않은 채 남아 있습니다 "
                       f"({known}). " + ADOPT_HINT.format(run=run, stage=stage))


def submit(run: str, stage: str, make_client_fn: Callable[[], Any], max_usd: float, resubmit: bool = False,
           work: Optional[Path] = None) -> Dict[str, Any]:
    """검사(중복·상한·계획 일치)를 모두 통과한 뒤에야 클라이언트를 만든다.

    이중 과금 방지:
    - plan·submit·adopt 는 run·단계 잠금(submit.lock) 안에서만 상태 파일을 읽고 쓴다(계획 교체·동시 제출·adopt 경쟁).
    - 만들기 요청은 SDK 자동 재시도를 끈다(멱등성 키가 없어 응답만 끊긴 요청을 다시 보내면 배치가 둘 생긴다).
    - 만들기 전에 pending.json 을 남긴다. 서버가 4xx 로 거절했을 때만 지우고, 응답을 모르는 실패(연결·시간 초과·5xx)는
      남겨 다음 submit·status·collect 가 확인을 요구한다. --resubmit 이 지난 시도의 pending 을 만나면 pending-<시각>.json
      으로 보관한다(그 시도가 만든 배치 id 를 잃지 않게).
    - --resubmit 의 먼저 낸 manifest 는 새 배치가 만들어진 뒤에야 manifest-<시각>.json 으로 보관한다.
    - --max-usd 는 상한(출력이 max_tokens 까지 다 나온 경우)과 비교한다.
    """
    d = run_dir(run, stage, work)
    read_json(d / "plan.json")                          # 없는 run 이면 폴더를 만들기 전에 멈춘다
    with _locked(d, run, stage):
        plan = read_json(d / "plan.json")
        _check_submittable(d, run, stage, resubmit)
        ceiling = plan["estimate"]["max_usd"]
        if ceiling > max_usd:
            raise ApiError(f"최대 비용 ${ceiling:.2f}(예상 ${plan['estimate']['usd']:.2f})가 상한 --max-usd {max_usd:.2f} 를 "
                           "넘어 제출하지 않았습니다 — 상한을 올리거나 --max-tokens 를 줄여 다시 plan 하세요")
        data = (d / "requests.jsonl").read_bytes()
        if hashlib.sha256(data).hexdigest() != plan.get("requests_sha256"):
            raise ApiError("requests.jsonl 이 plan.json 과 맞지 않습니다 — api plan 을 다시 실행하세요")
        requests = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
        client = make_client_fn()
        pending_path = d / "pending.json"
        if pending_path.exists():
            os.replace(pending_path, d / f"pending-{_stamp()}.json")
        pending = {"started": _now(), "requests": len(requests), "requests_sha256": plan["requests_sha256"]}
        _write_json(pending_path, pending)
        try:
            created = client.with_options(max_retries=0).messages.batches.create(requests=requests)
        except Exception as e:
            code = getattr(e, "status_code", None)
            if isinstance(code, int) and 400 <= code < 500:  # 서버가 거절 — 배치는 만들어지지 않았다
                pending_path.unlink()
                raise
            _write_json(pending_path, {**pending, "error": f"{type(e).__name__}: {e}"[:300]})
            raise ApiError(f"제출 응답을 받지 못했습니다({type(e).__name__}) — 배치가 만들어졌을 수 있습니다. "
                           + ADOPT_HINT.format(run=run, stage=stage)) from e
        _write_json(pending_path, {**pending, "batch_id": created.id})    # manifest 쓰기가 실패해도 id 는 남게
        manifest = {"batch_id": created.id, "run": run, "stage": stage, "model": plan["model"],
                    "effort": plan["effort"], "requests": len(requests), "estimate_usd": plan["estimate"]["usd"],
                    "max_cost_usd": ceiling, "max_usd": max_usd, "requests_sha256": plan["requests_sha256"],
                    "submitted": _now(), "expires_at": _iso(getattr(created, "expires_at", None)),
                    "processing_status": getattr(created, "processing_status", "")}
        _replace_manifest(d, manifest)
        pending_path.unlink()
        return manifest


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _replace_manifest(d: Path, manifest: Mapping[str, Any]) -> None:
    path = d / "manifest.json"
    if path.exists():
        os.replace(path, d / f"manifest-{_stamp()}.json")
    _write_json(path, manifest)


def adopt(run: str, stage: str, client: Any, batch_id: str, work: Optional[Path] = None) -> Dict[str, Any]:
    """응답을 못 받은 제출(pending.json)이 실제로 만든 배치를 이 run·단계의 manifest 로 받아들인다.
    --resubmit 이 응답을 못 받은 경우(먼저 낸 manifest 와 새 pending 이 함께 있음)는 먼저 낸 manifest 를 보관하고 바꾼다."""
    d = run_dir(run, stage, work)
    read_json(d / "plan.json")
    with _locked(d, run, stage):
        plan = read_json(d / "plan.json")
        manifest_path, pending_path = d / "manifest.json", d / "pending.json"
        if manifest_path.exists() and not pending_path.exists():
            raise ApiError(f"이미 manifest 가 있고 응답을 기다리는 제출(pending.json)이 없습니다: "
                           f"{read_json(manifest_path).get('batch_id')}")
        b = client.messages.batches.retrieve(batch_id)
        counts = b.request_counts
        total = sum(int(getattr(counts, k, 0) or 0)
                    for k in ("processing", "succeeded", "errored", "canceled", "expired"))
        if total != len(plan["parts"]):
            raise ApiError(f"{batch_id} 의 요청 수({total})가 계획({len(plan['parts'])})과 다릅니다 — 다른 배치입니다")
        manifest = {"batch_id": batch_id, "run": run, "stage": stage, "model": plan["model"],
                    "effort": plan["effort"], "requests": total, "estimate_usd": plan["estimate"]["usd"],
                    "max_cost_usd": plan["estimate"]["max_usd"], "requests_sha256": plan["requests_sha256"],
                    "adopted": _now(), "expires_at": _iso(getattr(b, "expires_at", None)),
                    "processing_status": b.processing_status}
        _replace_manifest(d, manifest)
        pending_path.unlink(missing_ok=True)
        return manifest


def _iso(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def read_manifest(run: str, stage: str, work: Optional[Path] = None) -> Dict[str, Any]:
    d = run_dir(run, stage, work)
    if not (d / "manifest.json").exists() and (d / "pending.json").exists():
        raise ApiError(f"{run} 의 {stage} 제출이 끝나지 않았습니다(pending.json). " + ADOPT_HINT.format(run=run, stage=stage))
    return read_json(d / "manifest.json")


def status(run: str, stage: str, client: Any, work: Optional[Path] = None) -> Dict[str, Any]:
    manifest = read_manifest(run, stage, work)
    b = client.messages.batches.retrieve(manifest["batch_id"])
    counts = b.request_counts
    return {"batch_id": manifest["batch_id"], "processing_status": b.processing_status,
            "counts": {k: getattr(counts, k, 0) for k in ("processing", "succeeded", "errored", "canceled", "expired")},
            "ended_at": _iso(getattr(b, "ended_at", None)), "expires_at": _iso(getattr(b, "expires_at", None))}


# ---------------------------------------------------------------- 결과 받기


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(obj).items()}
    return obj


def _usage(msg: Any) -> Dict[str, int]:
    u = getattr(msg, "usage", None)
    get = (lambda k: int(getattr(u, k, 0) or 0)) if u is not None else (lambda k: 0)
    return {"input": get("input_tokens"), "output": get("output_tokens"),
            "cache_read": get("cache_read_input_tokens"), "cache_write": get("cache_creation_input_tokens")}


def _error_type(result: Any) -> str:
    err = getattr(result, "error", None)
    inner = getattr(err, "error", err)
    return getattr(inner, "type", "") or ""


def to_result_row(obj: Mapping[str, Any]) -> Dict[str, str]:
    primary = str(obj.get("primary") or "")
    return {
        "name": str(obj.get("name") or "").strip(),
        "primary": primary,
        "secondary": _join_list(obj.get("secondary")),
        "rating": str(obj.get("rating") or ""),
        "regions": _join_list(obj.get("regions")),
        "identity": str(obj.get("identity") or ""),
        # 보류 행의 confidence 는 비운다(지침 §7 형식). 숫자가 아니면 그대로 두어 validate_row 가 잡게 한다
        "confidence": "" if primary == "etc" else _format_confidence(obj.get("confidence")),
        "note": " ".join(str(obj.get("note") or "").split()),
    }


def _format_confidence(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _join_list(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return tagdb.LIST_SEP.join(str(v) for v in value)


def parse_part(part: Mapping[str, Any], data: Mapping[str, Any], cats, regions,
               review: bool = False) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str], List[str]]:
    """한 요청의 JSON 출력 → ({태그: 결과 행}, {태그: 실패 사유}, 경고). review 면 고친 행만 오고 빠진 태그는 실패가 아니다."""
    expected = list(part["names"])
    known = set(expected)
    got: Dict[str, Dict[str, str]] = {}
    failed: Dict[str, str] = {}
    warnings: List[str] = []
    for obj in data.get("rows") or []:
        row = to_result_row(obj)
        name = row["name"]
        if name not in known and name.replace(" ", "_") in known:
            name = row["name"] = name.replace(" ", "_")
        if name not in known:
            warnings.append(f"{part['custom_id']}: 요청에 없는 태그 {name!r} 는 버림")
            continue
        if name in got or name in failed:
            warnings.append(f"{part['custom_id']}: {name} 가 두 번 나와 첫 행만 씀")
            continue
        errs = batch.validate_row(row, cats, regions)
        if errs:
            failed[name] = "형식: " + ", ".join(errs)
            continue
        if review:
            row["reason"] = " ".join(str(obj.get("reason") or "").split())
        got[name] = row
    if not review:
        for name in expected:
            if name not in got and name not in failed:
                failed[name] = "결과에 없음"
    return got, failed, warnings


def _message_text(msg: Any) -> str:
    return "".join(getattr(b, "text", "") for b in (getattr(msg, "content", None) or ())
                   if getattr(b, "type", "") == "text")


def _write_results(path: Path, rows: Sequence[Mapping[str, str]], fields: Sequence[str] = batch.RESULT_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def collect(run: str, stage: str, client: Any, config: Mapping[str, Any], work: Optional[Path] = None,
            ledger: Optional[Path] = None) -> Dict[str, Any]:
    d = run_dir(run, stage, work)
    plan = read_json(d / "plan.json")
    manifest = read_manifest(run, stage, work)
    if manifest.get("requests_sha256") and manifest["requests_sha256"] != plan.get("requests_sha256"):
        # 낸 요청과 지금 계획이 다르면 custom_id·태그 묶음을 믿을 수 없다
        raise ApiError(f"제출한 요청과 지금 plan.json 이 다릅니다({d}) — 제출 뒤에 계획이 바뀌었습니다")
    b = client.messages.batches.retrieve(manifest["batch_id"])
    if b.processing_status != "ended":
        return {"ended": False, "processing_status": b.processing_status}
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    parts = {p["custom_id"]: p for p in plan["parts"]}
    review = stage == "review"
    got: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)      # custom_id → {태그: 행}
    failed: Dict[str, str] = {}
    part_state: Dict[str, str] = {}
    warnings: List[str] = []
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    usd = 0.0
    with open(d / "raw.jsonl", "w", encoding="utf-8", newline="\n") as raw:
        for res in client.messages.batches.results(manifest["batch_id"]):
            raw.write(json.dumps(_jsonable(res), ensure_ascii=False) + "\n")
            part = parts.get(res.custom_id)
            if part is None:
                warnings.append(f"계획에 없는 custom_id {res.custom_id} 는 버림")
                continue
            r = res.result
            kind = getattr(r, "type", "")
            state = kind
            if kind == "succeeded":
                msg = r.message
                u = _usage(msg)
                for k in totals:
                    totals[k] += u[k]
                usd += batch_usd(plan["model"], u["input"], u["output"], u["cache_read"], u["cache_write"])
                stop = getattr(msg, "stop_reason", "")
                if stop == "refusal":
                    details = getattr(msg, "stop_details", None)
                    state = "refusal:" + (getattr(details, "category", None) or "?")
                elif stop == "max_tokens":
                    state = "max_tokens"
                else:
                    try:
                        data = json.loads(_message_text(msg))
                    except ValueError:
                        state = "json 아님"
                    else:
                        rows, bad, warns = parse_part(part, data, cats, regions, review=review)
                        got[part["custom_id"]] = rows
                        failed.update(bad)
                        warnings += warns
                        state = "ok"
            elif kind == "errored":
                state = "errored:" + (_error_type(r) or "?")
            part_state[part["custom_id"]] = state
            if state != "ok":
                for n in part["names"]:
                    failed[n] = state
    for cid, part in parts.items():
        if cid not in part_state:
            part_state[cid] = "결과 없음"
            for n in part["names"]:
                failed[n] = "결과 없음"
    out = results_dir(run, work)
    by_stem: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for p in plan["parts"]:
        by_stem[p["stem"]].append(p)
    summary: Dict[str, Any] = {"ended": True, "batch_id": manifest["batch_id"], "parts": part_state,
                               "warnings": warnings, "usage": totals, "usd": round(usd, 4)}
    if review:
        summary.update(_write_review(run, plan, got, failed, out))
        warnings += summary.pop("review_warnings")
    else:
        written = 0
        for stem, stem_parts in by_stem.items():
            rows = [got[p["custom_id"]][n] for p in stem_parts for n in p["names"] if n in got[p["custom_id"]]]
            _write_results(out / f"{stem}.csv", rows)
            written += len(rows)
        summary["written"] = written
        summary["retry"] = _write_retry(d / "retry.csv", plan["parts"], failed) if failed else ""
        summary["failed"] = failed
    first = "collected" not in manifest
    if first:
        _append_ledger(ledger or LEDGER, run, stage, plan, manifest, part_state, failed, totals, usd)
        manifest["collected"] = _now()
        _write_json(d / "manifest.json", manifest)
    summary["ledger"] = first
    _write_json(d / "usage.json", {k: summary[k] for k in ("batch_id", "parts", "usage", "usd")})
    return summary


def _write_retry(path: Path, parts: Sequence[Mapping[str, Any]], failed: Mapping[str, str]) -> str:
    """실패한 태그를 원래 배치 행(증거 칸 포함) 그대로 모은다 — 그대로 api plan 에 다시 넣을 수 있다."""
    fields: List[str] = []
    rows: List[Dict[str, str]] = []
    cache: Dict[str, Dict[str, Dict[str, str]]] = {}
    for p in parts:
        if not any(n in failed for n in p["names"]):
            continue
        if p["file"] not in cache:
            f, rs = read_batch(Path(p["file"]))
            fields += [k for k in f if k not in fields]
            cache[p["file"]] = {r["name"]: r for r in rs}
        rows += [cache[p["file"]][n] for n in p["names"] if n in failed and n in cache[p["file"]]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_csv_text(fields, rows))
    return str(path)


def _write_review(run: str, plan: Mapping[str, Any], fixes: Mapping[str, Mapping[str, Dict[str, str]]],
                  failed: Mapping[str, str], out: Path) -> Dict[str, Any]:
    """검토자에게 보여 준 분류 결과(검토 계획의 base)에 수정을 덮어 final/ 을 만들고, 고친 행(review/<stem>-fix.csv)·
    바뀐 칸(review/changes.csv)을 남긴다. 검토 요청이 실패한 태그는 분류 결과 그대로 final 에 들어가고
    review/unreviewed.csv 에 남는다. 검토 계획 뒤에 분류 CSV 가 바뀌었으면 경고만 한다(final 은 검토한 값 기준)."""
    changes: List[Dict[str, str]] = []
    unreviewed: List[Dict[str, str]] = []
    drifted: List[str] = []
    stems: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for p in plan["parts"]:
        stems[p["stem"]].append(p)
    for stem, stem_parts in stems.items():
        current_path = out / f"{stem}.csv"
        current = ({r["name"]: {k: r[k] for k in batch.RESULT_FIELDS} for r in batch.read_results([current_path])}
                   if current_path.exists() else {})
        final: Dict[str, Dict[str, str]] = {}
        fix_rows = []
        for p in stem_parts:
            for n in p["names"]:
                base = dict(p["base"][n])
                final[n] = base
                if current.get(n) != base:
                    drifted.append(n)
                if n in failed:
                    unreviewed.append({"name": n, "reason": failed[n]})
                    continue
                fix = fixes.get(p["custom_id"], {}).get(n)
                if fix is None:
                    continue
                fix_rows.append(fix)
                diff = [f"{k}: {base[k]} → {fix[k]}" for k in batch.RESULT_FIELDS[1:-1] if base[k] != fix[k]]
                if diff:
                    changes.append({"name": n, "file": stem, "changes": " · ".join(diff), "reason": fix.get("reason", "")})
                final[n] = {k: fix[k] for k in batch.RESULT_FIELDS}
        _write_results(out / "review" / f"{stem}-fix.csv", fix_rows, batch.RESULT_FIELDS + ("reason",))
        _write_results(out / "final" / f"{stem}.csv", list(final.values()))
    _write_results(out / "review" / "changes.csv", changes, ("name", "file", "changes", "reason"))
    if unreviewed:
        _write_results(out / "review" / "unreviewed.csv", unreviewed, ("name", "reason"))
    else:
        (out / "review" / "unreviewed.csv").unlink(missing_ok=True)
    warnings = ([f"검토 계획 뒤에 분류 결과가 바뀐 태그 {len(drifted)}개(final 은 검토한 값 기준): "
                 + ", ".join(drifted[:10])] if drifted else [])
    return {"changes": len(changes), "unreviewed": len(unreviewed), "fixes": sum(len(v) for v in fixes.values()),
            "review_warnings": warnings}


def _append_ledger(path: Path, run: str, stage: str, plan: Mapping[str, Any], manifest: Mapping[str, Any],
                   part_state: Mapping[str, str], failed: Mapping[str, str], totals: Mapping[str, int],
                   usd: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(LEDGER_FIELDS)
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), run, stage, plan["model"], manifest["batch_id"],
                    len(part_state), sum(1 for s in part_state.values() if s == "ok"), len(failed),
                    totals["input"], totals["cache_read"], totals["cache_write"], totals["output"], f"{usd:.4f}"])


def format_collect(run: str, stage: str, s: Mapping[str, Any], work: Optional[Path] = None) -> str:
    if not s["ended"]:
        return f"아직 끝나지 않았습니다 — {s['processing_status']} (api status {run} --stage {stage})"
    u = s["usage"]
    ok = sum(1 for v in s["parts"].values() if v == "ok")
    lines = [f"{run} · {stage}: 요청 {len(s['parts'])}개 중 성공 {ok}개 · "
             f"입력 {u['input']:,} · 캐시 읽기 {u['cache_read']:,} · 캐시 쓰기 {u['cache_write']:,} · 출력 {u['output']:,} 토큰"
             f" · 약 ${s['usd']:.2f}" + ("" if s["ledger"] else " (원장에는 이미 적음)")]
    for cid, state in s["parts"].items():
        if state != "ok":
            lines.append(f"  {cid}: {state}")
    out = results_dir(run, work)
    if stage == "review":
        lines.append(f"검토 수정 {s['fixes']}행 · 바뀐 태그 {s['changes']}개 → {out / 'review' / 'changes.csv'}")
        lines.append(f"반영본: {out / 'final'}")
        if s["unreviewed"]:
            lines.append(f"검토 못 한 태그 {s['unreviewed']}개(분류 결과 그대로 final 에) → {out / 'review' / 'unreviewed.csv'}")
    else:
        lines.append(f"결과 {s['written']:,}행 → {out}")
        if s["failed"]:
            reasons: Dict[str, int] = defaultdict(int)
            for v in s["failed"].values():
                reasons[v.split(":")[0]] += 1
            lines.append(f"다시 보낼 태그 {len(s['failed'])}개(" + ", ".join(f"{k} {v}" for k, v in reasons.items())
                         + f") → {s['retry']}")
    for w in s["warnings"][:20]:
        lines.append(f"  경고: {w}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 파일럿 표본


def sample(files: Sequence[Path], overlay: Mapping[str, Mapping[str, str]], n: int, out_dir: Path, name: str,
           size: int = 50, salt: str = "pilot") -> List[Path]:
    """기존 배치 파일에서 n 개를 뽑는다. 지금 overlay 의 보류(etc)·분류 비율을 지키고, 이름 해시 순이라 결정적이다."""
    check_run_name(name)
    pattern = re.compile(rf"^{re.escape(name)}-\d+\.csv$", re.IGNORECASE)    # Windows 파일 이름은 대소문자 무시
    old = sorted(p.name for p in out_dir.glob(f"{name}-*.csv") if pattern.match(p.name)) if out_dir.exists() else []
    if old:
        # 다시 만들면 크기가 달라 옛 파일이 남고, `<name>-*.csv` 로 계획할 때 섞인다
        raise ApiError(f"이미 있는 표본 이름입니다: {out_dir / old[0]} 외 {len(old) - 1}개 — 다른 이름을 쓰거나 지우세요")
    fields: List[str] = []
    pool: Dict[str, Dict[str, str]] = {}
    for path in files:
        f, rows = read_batch(path)
        fields += [k for k in f if k not in fields]
        for r in rows:
            pool.setdefault(r["name"], r)
    groups: Dict[bool, List[str]] = {True: [], False: []}
    for nm in pool:
        groups[(overlay.get(nm) or {}).get("primary") == "etc"].append(nm)
    total = len(pool)
    if n >= total:
        picked = sorted(pool)
    else:
        n_etc = round(n * len(groups[True]) / total) if total else 0
        key = lambda nm: hashlib.sha1((salt + nm).encode("utf-8")).hexdigest()   # noqa: E731
        picked = sorted(groups[True], key=key)[:n_etc] + sorted(groups[False], key=key)[:n - n_etc]
    picked = sorted(picked, key=lambda nm: (batch.head_key(nm), nm))
    out_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str((len(picked) + size - 1) // size)))
    paths = []
    for i in range(0, len(picked), size):
        path = out_dir / f"{name}-{i // size + 1:0{width}d}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(_csv_text(fields, [pool[nm] for nm in picked[i:i + size]]))
        paths.append(path)
    return paths
