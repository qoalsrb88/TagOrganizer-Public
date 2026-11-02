"""LLM 배치 분류 — 미분류 태그를 배치 파일로 내보내고, 분류 결과를 overlay 에 반영한다.

결과 CSV 열: name,primary,secondary,rating,regions,identity,confidence,note
(secondary·regions 는 | 로 구분. 지침: docs/classification-guide.md)
"""

from __future__ import annotations

import csv
import re
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

import bmk_tag_organizer_core as core

from . import evidence
from .builder import _valid_category, category_ids
from .tagdb import OVERLAY_FIELDS, SourceTag, split_list

RESULT_FIELDS = ("name", "primary", "secondary", "rating", "regions", "identity", "confidence", "note")
BATCH_FIELDS = ("name", "count", "aliases")
LOCKED = ("reviewed", "accepted")       # 자동 단계가 덮어쓰지 않는 status


def pending_general(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]],
                    top: int, include_classified: bool = False) -> List[SourceTag]:
    """general 상위 top 개 중 아직 분류하지 않은 태그(primary 가 비었거나 overlay 에 없음)."""
    out = []
    for t in [t for t in source if t.type == 0][:top]:
        row = overlay.get(t.name)
        if include_classified and (row is None or row.get("status") not in LOCKED):
            out.append(t)
        elif row is None or not row.get("primary"):
            out.append(t)
    return out


def held_etc(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]]) -> List[SourceTag]:
    """분류를 보류(primary=etc)한 general 태그 중 사람이 고치지 않은 것(reviewed 제외), 사용량 순."""
    return [t for t in source if t.type == 0 and (overlay.get(t.name) or {}).get("primary") == "etc"
            and overlay[t.name].get("status") != "reviewed"]


def head_key(name: str) -> str:
    """같은 계열을 묶는 키: 끝의 한정어 (작품)·(cosplay) 가 있으면 그것, 없으면 마지막 단어(대개 기본 명사)."""
    m = re.search(r"_(\([^()]*\))$", name)
    return m.group(1) if m else name.rsplit("_", 1)[-1]


def group_by_head(tags: Sequence[SourceTag]) -> List[SourceTag]:
    """같은 계열(*_bikini, *_(cosplay) 등)이 한 배치에 모이도록 정렬한다(작업자 안에서 일관성을 맞추기 쉽게)."""
    return sorted(tags, key=lambda t: (head_key(t.name), t.name))


def export_batches(tags: Sequence[SourceTag], out_dir: Path, name: str, size: int,
                   evidence_cache: Optional[Mapping[str, Mapping[str, Any]]] = None) -> List[Path]:
    """evidence_cache 를 주면 Danbooru 증거(위키·함의·연관 태그) 칸을 덧붙인다(tools/evidence.py)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    width = max(2, len(str((len(tags) + size - 1) // size)))
    fields = BATCH_FIELDS + (evidence.EVIDENCE_FIELDS if evidence_cache is not None else ())
    for i in range(0, len(tags), size):
        path = out_dir / f"{name}-{i // size + 1:0{width}d}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(fields)
            for t in tags[i:i + size]:
                row = [t.name, t.count, "|".join(a for a in t.aliases if not a.startswith("/"))[:120]]
                if evidence_cache is not None:
                    ev = evidence.summarize(evidence_cache.get(t.name))
                    row += [ev[k] for k in evidence.EVIDENCE_FIELDS]
                w.writerow(row)
        paths.append(path)
    return paths


def validate_row(row: Mapping[str, str], cats: Mapping[str, Set[str]], regions: Set[str]) -> List[str]:
    errs = []
    if not row.get("primary") or not _valid_category(row["primary"], cats):
        errs.append(f"primary={row.get('primary')!r}")
    elif row["primary"] in core.SYSTEM_CATEGORIES:
        # 원본 태그에 쓸 수 있는 시스템 카테고리는 etc(뜻이 불확실해 분류를 보류) 하나이고, 다른 칸은 비운다
        if row["primary"] != "etc":
            errs.append(f"primary={row['primary']!r} (원본 태그에는 etc 만)")
        filled = [k for k in ("secondary", "rating", "regions", "identity") if row.get(k)]
        if filled:
            errs.append("etc 는 " + "·".join(filled) + " 를 비운다")
    for sec in split_list(row.get("secondary")):
        if not _valid_category(sec, cats):
            errs.append(f"secondary={sec!r}")
    if row.get("rating", "") not in ("",) + core.RATINGS:
        errs.append(f"rating={row.get('rating')!r}")
    for reg in split_list(row.get("regions")):
        if reg not in regions:
            errs.append(f"region={reg!r}")
    if row.get("identity", "") not in ("", "0", "1"):
        errs.append(f"identity={row.get('identity')!r}")
    conf = row.get("confidence", "")
    if conf:
        try:
            if not 0 <= float(conf) <= 1:
                raise ValueError
        except ValueError:
            errs.append(f"confidence={conf!r}")
    return errs


def read_results(paths: Sequence[Path], optional: Sequence[str] = ()) -> List[Dict[str, str]]:
    """결과 CSV 행(RESULT_FIELDS 와, 파일에 있는 optional 칸만 — 앞뒤 공백 정리). 행마다 _where(파일:줄)."""
    rows = []
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = {"name", "primary"} - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"{path.name} 에 열이 없습니다: {', '.join(sorted(missing))}")
            fields = RESULT_FIELDS + tuple(k for k in optional if k in (reader.fieldnames or ()))
            for i, row in enumerate(reader, 2):
                clean = {k: (row.get(k) or "").strip() for k in fields}
                clean["_where"] = f"{path.name}:{i}"
                rows.append(clean)
    return rows


def apply_results(
    results: Sequence[Mapping[str, str]],
    overlay: MutableMapping[str, Dict[str, str]],
    source: Sequence[SourceTag],
    config: Mapping[str, Any],
    by: str,
    status: str = "llm",
    reclassify_etc: bool = False,
) -> Tuple[int, List[str], List[str]]:
    """(반영 수, 건너뜀 사유, 오류). 오류가 하나라도 있으면 아무것도 반영하지 않는다.

    reclassify_etc: 확정(accepted)된 보류(etc) 행도 덮어쓴다(증거로 다시 분류할 때). reviewed 는 언제나 건너뛴다.
    """
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    by_name = {t.name: t for t in source}
    errors, skipped, staged = [], [], {}
    for row in results:
        name = row["name"]
        if name not in by_name and name.replace(" ", "_") in by_name:
            name = name.replace(" ", "_")
        where = row.get("_where", name)
        if name not in by_name:
            errors.append(f"{where} 원본에 없는 태그: {row['name']}")
            continue
        errs = validate_row(row, cats, regions)
        if errs:
            errors.append(f"{where} {name}: " + ", ".join(errs))
            continue
        if name in staged:
            errors.append(f"{where} 같은 태그가 두 번 나옵니다: {name}")
            continue
        current = overlay.get(name)
        reopen = (reclassify_etc and current is not None and current.get("status") == "accepted"
                  and current.get("primary") == "etc")
        if current and current.get("status") in LOCKED and not reopen:
            skipped.append(f"{name} (이미 {current['status']})")
            continue
        staged[name] = row
    if errors:
        return 0, skipped, errors
    for name, row in staged.items():
        base = dict(overlay.get(name) or {k: "" for k in OVERLAY_FIELDS})
        base.update(
            name=name, source=base.get("source") or "danbooru", primary=row["primary"],
            secondary="|".join(split_list(row.get("secondary"))), rating=row.get("rating", ""),
            regions="|".join(split_list(row.get("regions"))), identity=row.get("identity", ""),
            status=status, by=by, confidence=row.get("confidence", ""), note=row.get("note", ""),
        )
        overlay[name] = base
    return len(staged), skipped, []


def default_by(prefix: str = "claude") -> str:
    return f"{prefix}:{_date.today().isoformat()}"
