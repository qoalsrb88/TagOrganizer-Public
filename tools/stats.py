"""분류 진행 현황 — 마일스톤별 사용량 커버리지와 status·카테고리 분포.

primary 가 etc 인 행은 "뜻이 불확실해 분류를 보류"한 결정이라 분류됨·확정에 넣지 않고 따로 센다.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Mapping, Optional, Sequence

from .tagdb import SourceTag

MILESTONES = (("M1", 500), ("M2", 2000), ("M3", 5000), ("M4", 10000), ("M5", None))
CLASSIFIED = ("seed", "llm", "accepted", "conflict", "reviewed")
CONFIRMED = ("accepted", "reviewed")


def coverage(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]]) -> List[Dict]:
    """general 태그 상위 N개 기준: 분류된 수와 사용량 비율(전체 general 사용량 대비)."""
    general = [t for t in source if t.type == 0]
    total = sum(t.count for t in general) or 1
    rows = []
    for label, n in MILESTONES:
        sub = general[:n] if n else general
        def share(statuses, etc: bool = False) -> Dict:
            hit = [t for t in sub if overlay.get(t.name, {}).get("primary")
                   and (overlay[t.name]["primary"] == "etc") == etc
                   and overlay[t.name].get("status") in statuses]
            return {"tags": len(hit), "usage": sum(t.count for t in hit) / total}
        rows.append({
            "milestone": label, "top": n or len(general), "range_usage": sum(t.count for t in sub) / total,
            "classified": share(CLASSIFIED), "confirmed": share(CONFIRMED), "reviewed": share(("reviewed",)),
            "etc": share(CLASSIFIED, etc=True),
        })
    return rows


def format_stats(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]],
                 pack_version: Optional[str] = None) -> str:
    types = Counter(t.type for t in source)
    lines = [
        f"원본 {len(source):,}개 — general {types[0]:,} · artist {types[1]:,} · copyright {types[3]:,}"
        f" · character {types[4]:,} · meta {types[5]:,}",
        f"overlay {len(overlay):,}행 — " + " · ".join(
            f"{k or '(빈값)'} {v:,}" for k, v in sorted(Counter(r.get('status', '') for r in overlay.values()).items())),
    ]
    if pack_version:
        lines.append(f"팩 버전 {pack_version}")
    lines += ["", "마일스톤  범위(general 상위)  범위 사용량   분류됨(전체)               확정(accepted+reviewed)     사람 확인(reviewed)"
              "  보류(etc)"]
    for r in coverage(source, overlay):
        c, f, v, e = r["classified"], r["confirmed"], r["reviewed"], r["etc"]
        lines.append(
            f"{r['milestone']:<8}  {r['top']:>16,}  {r['range_usage']:>10.1%}   "
            f"{c['tags']:>6,}개 / 사용량 {c['usage']:>6.1%}   {f['tags']:>6,}개 / 사용량 {f['usage']:>6.1%}   "
            f"{v['tags']:>6,}개   {e['tags']:>6,}개"
        )
    mains = Counter(r["primary"].partition("/")[0] for r in overlay.values() if r.get("primary"))
    if mains:
        lines += ["", "overlay 메인 카테고리: " + " · ".join(f"{k} {v:,}" for k, v in mains.most_common())]
    return "\n".join(lines)
