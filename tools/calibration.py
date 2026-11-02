"""병렬 분류 보정 — 확정된 태그로 작업자 품질을 미리 잰다.

make : 확정 태그(accepted·reviewed) 중 n 개를 메인 카테고리별로 고르게 뽑아 답을 가린 배치를 만들고,
       그 태그를 뺀 선례 파일(precedents.csv)을 같이 만든다(작업자가 답을 볼 수 없게).
score: 작업자 결과를 확정값과 비교해 메인·primary·등급·부위·identity 일치율과 작업자 간 일치율을 낸다.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from .batch import BATCH_FIELDS
from .tagdb import SourceTag, split_list

CONFIRMED = ("accepted", "reviewed")
PRECEDENT_FIELDS = ("name", "primary", "secondary", "rating", "regions", "identity", "note")
IDENTITY_MAINS = ("body", "subject")


def _stable(name: str, salt: str) -> str:
    return hashlib.sha1((salt + name).encode("utf-8")).hexdigest()


def pick(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], n: int,
         salt: str = "cal") -> List[SourceTag]:
    """메인 카테고리별로 sqrt(크기)에 비례해 나누고(최소 5개), 이름 해시 순으로 결정적으로 뽑는다."""
    by_main: Dict[str, List[SourceTag]] = defaultdict(list)
    for t in source:
        row = overlay.get(t.name)
        if t.type == 0 and row and row.get("primary") and row.get("status") in CONFIRMED:
            by_main[row["primary"].partition("/")[0]].append(t)
    weights = {m: math.sqrt(len(v)) for m, v in by_main.items()}
    total = sum(weights.values()) or 1
    quota = {m: max(5, round(n * w / total)) for m, w in weights.items()}
    picked: List[SourceTag] = []
    for m, tags in sorted(by_main.items()):
        picked += sorted(tags, key=lambda t: _stable(t.name, salt))[:quota[m]]
    return sorted(picked, key=lambda t: -t.count)[:n] if len(picked) > n else picked


def write_calibration(tags: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "CAL.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(BATCH_FIELDS)
        for t in tags:
            w.writerow([t.name, t.count, "|".join(a for a in t.aliases if not a.startswith("/"))[:120]])
    hidden = {t.name for t in tags}
    with open(out_dir / "precedents.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PRECEDENT_FIELDS, lineterminator="\n")
        w.writeheader()
        for name in sorted(overlay):
            row = overlay[name]
            if name not in hidden and row.get("primary") and row.get("status") in CONFIRMED:
                w.writerow({k: row.get(k, "") for k in PRECEDENT_FIELDS})


def _norm(row: Mapping[str, str]) -> Dict[str, object]:
    primary = row.get("primary", "")
    main = primary.partition("/")[0]
    return {
        "held": main == "etc",          # 보류 판단(분류했는가, 보류했는가)
        "main": main,
        "primary": primary,
        "rating": row.get("rating", "") or "",
        "regions": frozenset(split_list(row.get("regions"))),
        "identity": (row.get("identity") or "0") if main in IDENTITY_MAINS else "-",
    }


def score(results: Mapping[str, Sequence[Mapping[str, str]]], overlay: Mapping[str, Mapping[str, str]]) -> Dict:
    """results: 작업자 이름 → 결과 행 목록."""
    fields = ("held", "main", "primary", "rating", "regions", "identity")
    report: Dict = {"workers": {}, "confusions": [], "agreement": {}}
    by_worker: Dict[str, Dict[str, Dict]] = {}
    confusion: Counter = Counter()
    for worker, rows in results.items():
        got = {r["name"]: _norm(r) for r in rows}
        by_worker[worker] = got
        hits = Counter()
        n = 0
        for name, g in got.items():
            truth = overlay.get(name)
            if not truth:
                continue
            t = _norm(truth)
            n += 1
            for f in fields:
                hits[f] += g[f] == t[f]
            if g["primary"] != t["primary"]:
                confusion[(t["primary"], g["primary"])] += 1
        report["workers"][worker] = {"n": n, **{f: hits[f] / n if n else 0.0 for f in fields}}
    report["confusions"] = [{"truth": a, "worker": b, "count": c} for (a, b), c in confusion.most_common(25)]
    names = set.intersection(*(set(v) for v in by_worker.values())) if by_worker else set()
    if len(by_worker) > 1 and names:
        same_primary = sum(1 for nm in names if len({w[nm]["primary"] for w in by_worker.values()}) == 1)
        same_main = sum(1 for nm in names if len({w[nm]["main"] for w in by_worker.values()}) == 1)
        report["agreement"] = {"tags": len(names), "all_same_primary": same_primary / len(names),
                               "all_same_main": same_main / len(names)}
    report["mean"] = {f: sum(w[f] for w in report["workers"].values()) / len(report["workers"])
                      for f in fields} if report["workers"] else {}
    return report


def passed(report: Mapping, main_min: float, primary_min: float, held_min: float = 0.0) -> bool:
    m = report.get("mean", {})
    return bool(m) and m["main"] >= main_min and m["primary"] >= primary_min and m["held"] >= held_min


def format_report(report: Mapping, main_min: float, primary_min: float, held_min: float = 0.0) -> str:
    lines = ["작업자       태그   보류    메인    primary  등급    부위    identity"]
    for w, s in report["workers"].items():
        lines.append(f"{w:<10} {s['n']:>5}  {s['held']:>6.1%}  {s['main']:>6.1%}  {s['primary']:>6.1%}"
                     f"  {s['rating']:>6.1%}  {s['regions']:>6.1%}  {s['identity']:>6.1%}")
    m = report.get("mean", {})
    if m:
        lines.append(f"{'평균':<10} {'':>5}  {m['held']:>6.1%}  {m['main']:>6.1%}  {m['primary']:>6.1%}"
                     f"  {m['rating']:>6.1%}  {m['regions']:>6.1%}  {m['identity']:>6.1%}")
    a = report.get("agreement")
    if a:
        lines.append(f"작업자 간: 메인 전원 일치 {a['all_same_main']:.1%} · primary 전원 일치 {a['all_same_primary']:.1%}"
                     f" ({a['tags']}개)")
    held = f", 보류 판단 ≥ {held_min:.0%}" if held_min else ""
    ok = passed(report, main_min, primary_min, held_min)
    lines.append(f"기준(평균 메인 ≥ {main_min:.0%}, primary ≥ {primary_min:.0%}{held}): {'통과' if ok else '미달'}")
    if report["confusions"]:
        lines.append("자주 엇갈린 primary (확정값 → 작업자):")
        for c in report["confusions"][:15]:
            lines.append(f"  {c['truth']} → {c['worker']}: {c['count']}")
    return "\n".join(lines)
