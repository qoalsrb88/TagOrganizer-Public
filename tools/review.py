"""검수 — 검수용 CSV 내보내기/반영, 로컬 분류 샘플과의 대조.

대조 결과는 검수할 태그를 고르는 표시(flags)로만 쓰고
샘플의 값을 overlay 에 복사하지 않는다.
"""

from __future__ import annotations

import csv
import re
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .batch import validate_row
from .builder import category_ids
from .tagdb import SourceTag, split_list

REVIEW_FIELDS = ("approve", "name", "count", "primary", "secondary", "rating", "regions", "identity",
                 "confidence", "flags", "note")
APPROVE = {"y", "yes", "o", "ok", "1", "v", "✓", "✔"}
REJECT = {"n", "no", "x", "0"}
LOW_CONFIDENCE = 0.75

# 샘플 TAG_DB 카테고리 → 우리 메인 카테고리(대략)
SAMPLE_MAIN = {
    "appearance": "body", "top": "clothes", "bottom": "clothes", "fullbody": "clothes", "outer": "clothes",
    "legwear": "clothes", "accessories": "clothes", "pose": "action", "expression": "action",
    "background": "background", "composition": "composition", "lighting": "effect",
}


def load_sample(path: Path) -> Dict[str, Tuple[str, bool]]:
    """샘플 TAG_DB(JS) → {danbooru 이름: (샘플 카테고리, nsfw 여부)}."""
    src = path.read_text(encoding="utf-8")
    out: Dict[str, Tuple[str, bool]] = {}
    for cat, body in re.findall(r"'(\w+)': \{(.*?)\n    \}", src, re.S):
        for kind in ("sfw", "nsfw"):
            m = re.search(kind + r": \[(.*?)\]", body, re.S)
            for tag in re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1) if m else ""):
                out.setdefault(tag.replace(" ", "_"), (cat, kind == "nsfw"))
    return out


def sample_flags(row: Mapping[str, str], sample: Optional[Mapping[str, Tuple[str, bool]]]) -> List[str]:
    if not sample or row["name"] not in sample:
        return []
    cat, nsfw = sample[row["name"]]
    flags = []
    mapped = SAMPLE_MAIN.get(cat)
    ours = row.get("primary", "").partition("/")[0]
    if mapped and ours and mapped != ours:
        flags.append(f"sample:{cat}")
    rating = row.get("rating", "")
    if nsfw and rating in ("", "g"):
        flags.append("sample:nsfw")
    if not nsfw and rating in ("q", "e"):
        flags.append("sample:sfw")
    return flags


def review_rows(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], top: int,
                statuses: Sequence[str] = ("llm", "conflict", "seed"),
                sample: Optional[Mapping[str, Tuple[str, bool]]] = None) -> List[Dict[str, str]]:
    rows = []
    for t in [t for t in source if t.type == 0][:top]:
        ov = overlay.get(t.name)
        if not ov or ov.get("status") not in statuses:
            continue
        flags = []
        if ov.get("status") == "conflict":
            flags.append("conflict")
        try:
            if ov.get("confidence") and float(ov["confidence"]) < LOW_CONFIDENCE:
                flags.append("low_conf")
        except ValueError:
            pass
        flags += sample_flags(ov, sample)
        rows.append({
            "approve": "", "name": t.name, "count": str(t.count),
            **{k: ov.get(k, "") for k in ("primary", "secondary", "rating", "regions", "identity", "confidence", "note")},
            "flags": " ".join(flags),
        })
    return rows


def write_review(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def accept(source: Sequence[SourceTag], overlay: MutableMapping[str, Dict[str, str]],
           top: Optional[int] = None) -> List[str]:
    """LLM·시드 분류를 확정(accepted)한다. top 이 있으면 general 상위 top 개 안의 행만.
    사람 검수를 생략하고 분류를 위임받았을 때 쓴다(2026-10-26 사용자 결정)."""
    if top is None:
        names = list(overlay)
    else:
        names = [t.name for t in [t for t in source if t.type == 0][:top]]
    done = []
    for name in names:
        row = overlay.get(name)
        if row and row.get("primary") and row.get("status") in ("llm", "seed"):
            row["status"] = "accepted"
            done.append(name)
    return done


def apply_review(path: Path, overlay: MutableMapping[str, Dict[str, str]], config: Mapping[str, Any],
                 by: Optional[str] = None) -> Tuple[Dict[str, int], List[str]]:
    """approve 열이 승인이면 행의 값(수정 포함)으로 reviewed, 반려면 conflict. 빈 칸은 그대로 둔다."""
    by = by or f"user:{_date.today().isoformat()}"
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    errors: List[str] = []
    staged: List[Tuple[str, str, Dict[str, str]]] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            mark = row.get("approve", "").lower()
            name = row.get("name", "")
            if not mark:
                counts["skipped"] += 1
                continue
            if name not in overlay:
                errors.append(f"{path.name}:{i} overlay 에 없는 태그: {name}")
                continue
            if mark in APPROVE:
                errs = validate_row(row, cats, regions)
                if errs:
                    errors.append(f"{path.name}:{i} {name}: " + ", ".join(errs))
                    continue
                staged.append(("approve", name, row))
            elif mark in REJECT:
                staged.append(("reject", name, row))
            else:
                errors.append(f"{path.name}:{i} approve 값을 알 수 없습니다: {row.get('approve')!r} (승인 y/o, 반려 n/x)")
    if errors:
        return counts, errors
    for action, name, row in staged:
        ov = overlay[name]
        if action == "approve":
            ov.update(
                primary=row["primary"], secondary="|".join(split_list(row.get("secondary"))),
                rating=row.get("rating", ""), regions="|".join(split_list(row.get("regions"))),
                identity=row.get("identity", ""), note=row.get("note", ""), status="reviewed", by=by,
            )
            counts["approved"] += 1
        else:
            ov.update(status="conflict", note=row.get("note", "") or ov.get("note", ""), by=by)
            counts["rejected"] += 1
    return counts, []
