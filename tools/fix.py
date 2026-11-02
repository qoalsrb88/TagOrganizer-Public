"""손 수정 — 태그 분류를 명령 한 줄로 고치고(검증 후 reviewed 로 잠금), 조건으로 찾고, overlay 손상을 점검한다.

원본 태그는 overlay 를(apply_fix), 확장 DB(data/extra) 태그는 그 DB 의 행을(apply_fix_rows), e621 에만 있는 태그는
e621.csv 의 행을(apply_fix_site) 같은 규칙으로 고친다.

overlay CSV 를 엑셀로 열어 저장하면 =_= · +_+ · @_@ 같은 이름은 수식으로, 9/11 은 날짜로 바뀌어 분류가 깨진다.
손으로 고칠 때는 `tagorg fix` 를 쓰고, 그래도 CSV 를 직접 고쳤다면 `tagorg check` 로 확인한다.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .batch import validate_row
from .builder import MODEL_ID, category_ids, overlay_problems
from .tagdb import OVERLAY, OVERLAY_FIELDS, ROOT, SITE_FIELDS, SourceTag, split_list

EDITABLE = ("primary", "secondary", "rating", "regions", "identity", "note")
CLASS_FIELDS = ("primary", "secondary", "rating", "regions", "identity")
EXTRA_EDITABLE = ("models", "use_instead", "aliases", "ref")      # 확장 DB 행에만 있는 칸
LIST_FIELDS = ("secondary", "regions", "models", "use_instead", "aliases")
_ASSIGN = re.compile(r"^(" + "|".join(EDITABLE + EXTRA_EDITABLE) + r")=(.*)$", re.S)


def split_args(tokens: Sequence[str]) -> Tuple[List[str], Dict[str, str]]:
    """`필드=값` 은 수정 내용, 나머지는 태그 이름. (=_= 같은 이름은 필드 이름으로 시작하지 않아 이름으로 남는다.)"""
    names, updates = [], {}
    for tok in tokens:
        m = _ASSIGN.match(tok)
        if m:
            updates[m.group(1)] = m.group(2).strip()
        else:
            names.append(tok)
    return names, updates


def resolve_name(raw: str, by_name: Mapping[str, SourceTag], aliases: Mapping[str, str]) -> Tuple[Optional[str], str]:
    """(원본 태그 이름, 안내). 공백 → 밑줄, 대문자, 프롬프트식 \\( \\) 와 별칭을 받아 준다.

    역슬래시가 이름의 일부인 태그(\\(^o^)/, \\m/)가 있으므로 입력 그대로를 먼저 찾고, 그다음에 이스케이프를 푼다.
    """
    text = raw.strip()
    unescaped = text.replace("\\(", "(").replace("\\)", ")")
    variants = [text] + ([unescaped] if unescaped != text else [])
    forms = [c for v in variants for c in (v, v.lower(), v.lower().replace(" ", "_"))]
    for cand in forms:
        if cand in by_name:
            return cand, ""
    for cand in forms:
        if cand.lower() in aliases:
            return aliases[cand.lower()], f"별칭 {raw} → {aliases[cand.lower()]}"
    return None, ""


def locate(raw: str, by_name: Mapping[str, SourceTag], aliases: Mapping[str, str],
           overlay: Mapping[str, Mapping[str, str]], front: Sequence[Mapping[str, str]],
           late: Sequence[Mapping[str, str]], site_by: Mapping[str, SourceTag], site_aliases: Mapping[str, str],
           lookup: Any) -> Tuple[str, Any]:
    """프롬프트 표기 하나를 팩과 같은 순서로 찾는다. ("src", 이름) — overlay(tags.csv) 행, ("ext", 행) — 확장 DB 행,
    ("site", e621 이름) — e621.csv 행. 못 찾으면 ("src", raw)(apply_fix 가 오류로 알림).

    이름: Danbooru → 확장 DB(front) → e621(site_by — Danbooru 와 같은 이름 뺌) → 폐기 DB(late).
    별칭: Danbooru → 확장 DB(front) → e621(site_aliases — Danbooru 와 같은 이름의 별칭 포함, 찾은 이름이 Danbooru·
    확장 DB 이름이면 그 행) → 폐기 DB. Danbooru 에서 사라진 overlay 행(missing_since)은 마지막. lookup 은 extra.lookup.
    """
    name, _ = resolve_name(raw, by_name, {})
    if name is not None:
        return "src", raw
    row = lookup(front, raw)
    if row is not None:
        return "ext", row
    name, _ = resolve_name(raw, site_by, {})
    if name is not None:
        return "site", name
    row = lookup(late, raw)
    if row is not None:
        return "ext", row
    name, _ = resolve_name(raw, {}, aliases)
    if name is not None:
        return "src", raw
    row = lookup(front, raw, aliases=True)
    if row is not None:
        return "ext", row
    name, _ = resolve_name(raw, {}, site_aliases)
    if name is not None:
        if name in by_name:
            return "src", name
        row = lookup(front, name)
        return ("ext", row) if row is not None else ("site", name)
    row = lookup(late, raw, aliases=True)
    if row is not None:
        return "ext", row
    return "src", raw


def alias_index(source: Sequence[SourceTag]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for t in source:
        for a in t.aliases:
            if not a.startswith("/"):
                out.setdefault(a.lower(), t.name)
    return out


def apply_fix(
    overlay: MutableMapping[str, Dict[str, str]],
    names: Sequence[str],
    updates: Mapping[str, str],
    source: Sequence[SourceTag],
    config: Mapping[str, Any],
    by: str,
    etc: bool = False,
) -> Tuple[List[Tuple[str, Dict[str, str], Dict[str, str]]], List[str], List[str]]:
    """(바뀐 행 [(이름, 전, 후)], 오류, 안내). 오류가 하나라도 있으면 아무것도 바꾸지 않는다.

    고친 행은 status=reviewed 가 되어 자동 단계(batch apply·accept)가 덮어쓰지 않는다.
    primary 가 바뀌면 이전 분류의 note(LLM 판단 근거·"의미 불확실")는 지운다(note= 로 새로 줄 수 있다).
    """
    by_name = {t.name: t for t in source}
    aliases = alias_index(source)
    errors: List[str] = []
    notes: List[str] = []
    if etc and set(updates) - {"note"}:
        errors.append("--etc 와 함께 줄 수 있는 것은 note= 뿐입니다")
    extra_only = sorted(set(updates) & set(EXTRA_EDITABLE))
    if extra_only:
        errors.append(f"{'·'.join(extra_only)} 는 확장 DB 태그에만 있는 칸입니다")
    staged: Dict[str, Dict[str, str]] = {}
    for raw in names:
        name, hint = resolve_name(raw, by_name, aliases)
        if name is None and raw in overlay:
            name = raw                                  # 원본에서 사라진(missing_since) 행도 고칠 수 있게
        if name is None:
            errors.append(f"원본에 없는 태그: {raw}")
            continue
        if hint:
            notes.append(hint)
        if name in staged:
            continue
        before = dict(overlay.get(name) or {k: "" for k in OVERLAY_FIELDS}, name=name)
        after = dict(before)
        after["source"] = after.get("source") or "danbooru"
        after = _fix_row(name, before, after, updates, config, by, etc, EDITABLE, errors, notes)
        if after is not None:
            staged[name] = after
    if errors:
        return [], errors, notes
    changes = []
    for name, after in staged.items():
        before = dict(overlay.get(name) or {})
        overlay[name] = after
        changes.append((name, before, after))
    return changes, [], notes


def apply_fix_site(
    rows: MutableMapping[str, Dict[str, str]],
    names: Sequence[str],
    updates: Mapping[str, str],
    config: Mapping[str, Any],
    by: str,
    site_id: str,
    etc: bool = False,
) -> Tuple[List[Tuple[str, Dict[str, str], Dict[str, str]]], List[str], List[str]]:
    """두 번째 원본의 분류 파일(e621.csv, 이름 → 행) 행을 apply_fix 와 같은 규칙·반환값으로 고친다. names 는
    e621 원본 이름(호출한 쪽이 찾음). 행이 없으면(종류 자동·자동 same_as) 새로 만든다. same_as 행에 분류를 주면
    same_as 를 지우고 직접 분류한다(메모만 주면 same_as 그대로)."""
    errors: List[str] = []
    notes: List[str] = []
    if etc and set(updates) - {"note"}:
        errors.append("--etc 와 함께 줄 수 있는 것은 note= 뿐입니다")
    extra_only = sorted(set(updates) & set(EXTRA_EDITABLE))
    if extra_only:
        errors.append(f"{'·'.join(extra_only)} 는 확장 DB 태그에만 있는 칸입니다")
    staged: Dict[str, Dict[str, str]] = {}
    for name in names:
        if name in staged:
            continue
        before = dict(rows.get(name) or {k: "" for k in SITE_FIELDS}, name=name)
        after = dict(before, source=site_id)
        if before.get("same_as") and not etc and not set(updates) & set(CLASS_FIELDS):
            _set_updates(after, updates)
            if after.get("note", "") == before.get("note", "") and before.get("status") == "reviewed":
                notes.append(f"{name}: 바뀐 것 없음")
                continue
            after.update(status="reviewed", by=by)
            staged[name] = after
            continue
        if before.get("same_as"):
            notes.append(f"{name}: same_as {before['same_as']} 를 지우고 직접 분류합니다")
            after["same_as"] = ""
        after = _fix_row(name, before, after, updates, config, by, etc, EDITABLE + ("same_as",), errors, notes)
        if after is not None:
            staged[name] = after
    if errors:
        return [], errors, notes
    changes = []
    for name, after in staged.items():
        before = dict(rows.get(name) or {})
        rows[name] = after
        changes.append((name, before, after))
    return changes, [], notes


def apply_fix_rows(
    rows: MutableMapping[str, Dict[str, str]],
    names: Sequence[str],
    updates: Mapping[str, str],
    config: Mapping[str, Any],
    by: str,
    etc: bool = False,
) -> Tuple[List[Tuple[str, Dict[str, str], Dict[str, str]]], List[str], List[str]]:
    """확장 DB 행(이름 → 행, 행에 db 칸)을 apply_fix 와 같은 규칙·반환값으로 고친다.

    분류 칸 말고 models·use_instead·aliases·ref 도 고칠 수 있다. same_as 행은 대상의 분류를 쓰므로 분류 칸
    (과 --etc)은 고칠 수 없다 — 메모와 확장 DB 칸만.
    """
    errors: List[str] = []
    notes: List[str] = []
    if etc and set(updates) - {"note"}:
        errors.append("--etc 와 함께 줄 수 있는 것은 note= 뿐입니다")
    staged: Dict[str, Dict[str, str]] = {}
    for name in names:
        if name not in rows:
            errors.append(f"확장 DB 에 없는 태그: {name}")
            continue
        if name in staged:
            continue
        before = dict(rows[name])
        after = dict(before)
        if before.get("same_as"):
            touched = sorted(set(updates) & set(CLASS_FIELDS))
            if etc or touched:
                errors.append(f"{name}: same_as 행은 대상({before['same_as']})의 분류를 씁니다 — 분류를 고치려면 "
                              f"data/extra/{before.get('db')}.csv 에서 same_as 를 지웁니다")
                continue
            after = _fix_extra_fields(name, before, after, updates, by, errors, notes)
        else:
            after = _fix_row(name, before, after, updates, config, by, etc, EDITABLE + EXTRA_EDITABLE, errors, notes)
        if after is not None:
            staged[name] = after
    if errors:
        return [], errors, notes
    changes = []
    for name, after in staged.items():
        before = dict(rows[name])
        rows[name] = after
        changes.append((name, before, after))
    return changes, [], notes


def _set_updates(after: Dict[str, str], updates: Mapping[str, str]) -> None:
    for k, v in updates.items():
        after[k] = "|".join(split_list(v)) if k in LIST_FIELDS else v


def _models_ok(name: str, after: Mapping[str, str], errors: List[str]) -> bool:
    bad = [m for m in split_list(after.get("models")) if not MODEL_ID.match(m)]
    if bad:
        errors.append(f"{name}: models 는 영문 소문자·숫자·점·하이픈으로 된 ID 를 | 로 잇습니다: {', '.join(bad)}")
    return not bad


def _fix_extra_fields(name: str, before: Mapping[str, str], after: Dict[str, str], updates: Mapping[str, str],
                      by: str, errors: List[str], notes: List[str]) -> Optional[Dict[str, str]]:
    """same_as 행: 메모·확장 DB 칸만 고친다(분류 검증 없음 — 분류는 대상에서 온다)."""
    _set_updates(after, updates)
    if not _models_ok(name, after, errors):
        return None
    unchanged = all(before.get(k, "") == after.get(k, "") for k in ("note",) + EXTRA_EDITABLE)
    if unchanged and before.get("status") == "reviewed":
        notes.append(f"{name}: 바뀐 것 없음")
        return None
    after.update(status="reviewed", by=by)
    return after


def _fix_row(name: str, before: Mapping[str, str], after: Dict[str, str], updates: Mapping[str, str],
             config: Mapping[str, Any], by: str, etc: bool, compare: Sequence[str], errors: List[str],
             notes: List[str]) -> Optional[Dict[str, str]]:
    """한 행을 고친 결과(검증 통과), 바뀐 것이 없으면 None. 오류·안내는 목록에 더한다. compare 는 '바뀐 것 없음'을
    가르는 칸이다."""
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    rated = {c["id"] for c in config["taxonomy"]["categories"] if c.get("rated")}
    if etc:
        after.update(primary="etc", secondary="", rating="", regions="", identity="")
    _set_updates(after, updates)
    if after.get("primary", "") != before.get("primary", "") and "note" not in updates:
        after["note"] = ""
    if not after.get("primary"):
        errors.append(f"{name}: primary 가 비었습니다(분류하거나 --etc 로 보류)")
        return None
    errs = validate_row(after, cats, regions)
    if errs:
        errors.append(f"{name}: " + ", ".join(errs))
        return None
    if not _models_ok(name, after, errors):
        return None
    main = after["primary"].partition("/")[0]
    if main in rated and not after.get("rating"):
        notes.append(f"{name}: {main} 는 등급 대상인데 rating 이 비었습니다(필터가 g 로 봅니다)")
    if main not in rated and main != "etc" and after.get("rating") and "rating" not in updates:
        notes.append(f"{name}: {main} 는 보통 rating 을 비웁니다(이전 값 {after['rating']} 이 남음 — rating= 로 지움)")
    if main not in rated and main != "etc" and after.get("regions") and "regions" not in updates:
        notes.append(f"{name}: {main} 에 regions 가 남아 있으면 구도 규칙이 이 태그를 뺄 수 있습니다(regions= 로 지움)")
    if after.get("identity") and main != "body" and after["primary"] != "subject/species":
        notes.append(f"{name}: identity 는 body·subject/species 에만 씁니다(identity= 로 지움)")
    if all(before.get(k, "") == after.get(k, "") for k in compare) and before.get("status") == "reviewed":
        notes.append(f"{name}: 바뀐 것 없음")
        return None
    after.update(status="reviewed", by=by, confidence="")
    return after


def format_row(row: Mapping[str, str]) -> str:
    if row and row.get("same_as"):
        return f"same_as {row['same_as']} [{row.get('status') or '-'}]" + (f" — {row['note']}" if row.get("note") else "")
    if not row or not row.get("primary"):
        return "(분류 없음)"
    if row["primary"] == "etc":
        return f"etc(보류) [{row.get('status') or '-'}]" + (f" — {row['note']}" if row.get("note") else "")
    parts = [row["primary"], f"rating={row.get('rating') or '-'}"]
    if row.get("regions"):
        parts.append(f"regions={row['regions']}")
    if row.get("identity"):
        parts.append(f"identity={row['identity']}")
    if row.get("secondary"):
        parts.append(f"secondary={row['secondary']}")
    parts.append(f"[{row.get('status') or '-'}]")
    if row.get("note"):
        parts.append(f"— {row['note']}")
    return " ".join(parts)


def find_rows(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], primary: Optional[str] = None,
              status: Optional[str] = None, rating: Optional[str] = None,
              grep: Optional[str] = None) -> List[Tuple[int, Mapping[str, str]]]:
    """조건에 맞는 overlay 행을 사용량 순으로. primary 는 메인(clothes) 또는 main/sub, etc 는 보류 행."""
    counts = {t.name: t.count for t in source}
    out = []
    for name, row in overlay.items():
        p = row.get("primary", "")
        if primary and not (p == primary or p.startswith(primary + "/")):
            continue
        if status and row.get("status") != status:
            continue
        if rating is not None and (row.get("rating") or "") != rating:
            continue
        if grep and grep.lower() not in name.lower() and grep.lower() not in (row.get("note") or "").lower():
            continue
        out.append((counts.get(name, 0), row))
    out.sort(key=lambda x: (-x[0], x[1]["name"]))
    return out


def committed_overlay(path: Path = OVERLAY) -> Optional[Dict[str, Dict[str, str]]]:
    """git HEAD 의 overlay. git 이 없거나 커밋 전이면 None."""
    rel = path.resolve().relative_to(ROOT).as_posix()
    try:
        res = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    text = res.stdout.decode("utf-8-sig", errors="replace")
    return {r["name"]: r for r in csv.DictReader(io.StringIO(text, newline=""))}


def check_overlay(overlay: Mapping[str, Mapping[str, str]], source: Sequence[SourceTag], config: Mapping[str, Any],
                  committed: Optional[Mapping[str, Mapping[str, str]]]) -> Tuple[List[str], List[str]]:
    """(오류, 참고). 빌드와 같은 행 검사 + 마지막 커밋 대비 사라진 행."""
    errors = overlay_problems(overlay, {t.name for t in source}, category_ids(config["taxonomy"]),
                              set(config["rules"].get("regions", ())))
    info: List[str] = []
    if committed is None:
        info.append("git 커밋과 비교하지 못했습니다(git 이 없거나 아직 커밋 전)")
    else:
        gone = sorted(set(committed) - set(overlay))
        if gone:
            errors.append(f"마지막 커밋에 있던 행 {len(gone)}개가 없어졌습니다: {', '.join(gone[:15])}"
                          + (" …" if len(gone) > 15 else ""))
        changed = sum(1 for n, r in overlay.items()
                      if n in committed and any((committed[n].get(k) or "") != (r.get(k) or "") for k in OVERLAY_FIELDS))
        added = len(set(overlay) - set(committed))
        info.append(f"마지막 커밋 대비: 바뀐 행 {changed} · 새 행 {added} · 없어진 행 {len(gone)}")
    return errors, info
