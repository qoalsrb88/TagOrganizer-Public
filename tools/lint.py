"""분류 일관성 점검 — '수식어_기본아이템' 태그가 기본 아이템과 같은 분류·등급인지 본다.

예: purple_bikini ↔ bikini, grey_sweater ↔ sweater. 색·무늬·길이 같은 수식어는 분류를 바꾸지 않아야 한다.
다만 상태 수식어(torn_, open_, see-through_)나 뜻이 다른 쌍(collar ↔ frilled_collar)은 의도한 차이이므로
결과는 "다시 볼 목록"이지 오류 목록이 아니다. 여러 분류자(병렬 배치)가 만든 결과를 맞출 때 쓴다.
"""

from __future__ import annotations

from typing import List, Mapping, Tuple

# 분류를 바꾸지 않아야 하는 수식어(색·무늬·장식). 상태 수식어(torn, open, see-through 등)는 넣지 않는다.
NEUTRAL_MODIFIERS = frozenset({
    "white", "black", "red", "blue", "green", "yellow", "pink", "purple", "orange", "brown", "grey", "aqua",
    "gold", "multicolored", "two-tone", "striped", "plaid", "frilled", "print", "polka_dot", "lace-trimmed",
    "fur-trimmed", "ribbon-trimmed", "vertical-striped", "diagonal-striped", "checkered", "argyle",
    "floral_print", "cow_print", "leopard_print", "animal_print", "star_print", "heart_print", "fishnet",
    "lace", "denim", "leather", "latex", "single", "asymmetrical", "mismatched", "uneven", "layered",
})


def pair_issues(overlay: Mapping[str, Mapping[str, str]]) -> List[Tuple[str, str, List[str]]]:
    """(태그, 기본 아이템, 차이 목록). 분류된 행끼리만 비교한다."""
    classified = {n: r for n, r in overlay.items() if r.get("primary")}
    out = []
    for name, row in classified.items():
        for i, ch in enumerate(name):
            if ch != "_":
                continue
            mod, base = name[:i], name[i + 1:]
            if mod not in NEUTRAL_MODIFIERS or base not in classified:
                continue
            b = classified[base]
            diffs = []
            if row["primary"] != b["primary"]:
                diffs.append(f"primary {row['primary']} ≠ {b['primary']}")
            if (row.get("rating") or "") != (b.get("rating") or ""):
                diffs.append(f"rating {row.get('rating') or '-'} ≠ {b.get('rating') or '-'}")
            if diffs:
                out.append((name, base, diffs))
            break
    return sorted(out)


def format_issues(issues: List[Tuple[str, str, List[str]]], overlay: Mapping[str, Mapping[str, str]]) -> str:
    lines = [f"수식어 쌍 불일치 {len(issues)}개 (의도한 차이일 수 있음 — 다시 볼 목록)"]
    for name, base, diffs in issues:
        lines.append(f"  {name} ({overlay[name].get('status') or '-'}) vs {base}: {'; '.join(diffs)}")
    return "\n".join(lines)
