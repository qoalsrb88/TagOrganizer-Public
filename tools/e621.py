"""두 번째 원본(e621) 관리 — 폐기 DB 와 겹치는 행 옮기기, 사용량 커버리지, 배치 분류(docs/e621.md).

e621 은 Danbooru 가 폐기한 넓은 태그(female·clothing·fur)를 지금도 쓴다. 빌드는 그런 폐기 DB 행을 e621 태그에
밀려 건너뛰므로(층 순서: e621 → 폐기 DB), 그 분류를 e621.csv 로 옮겨 다시 볼 수 있게 한다.

배치 분류(2단계): 분류할 e621 태그를 종류(KINDS)별로 골라 증거와 함께 내보내고, 결과를 e621.csv 에 반영한다.
증거는 e621 DB 내보내기(db_export)의 위키·함의 CSV 다. 이 PC 에서 e621.net 은 HTTP 451 이라, 그 내보내기를 옮겨
둔 공개 사본(아카이브 저장소의 raw/e621)을 work/evidence/e621/ 에 받아 쓴다(같은 형식이면 어느 사본이든).
"""

from __future__ import annotations

import csv
import gzip
import re
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

import bmk_tag_organizer_core as core

from . import batch, evidence
from .builder import E621_TYPES, LATE_DBS, BuildError, _kaomoji, build_pack, category_ids, danbooru_key
from .tagdb import SITE_FIELDS, Site, SourceTag, split_list

KEEP_TYPES = (0, 7)       # 폐기 DB 분류를 옮겨 쓰는 e621 종류(general·meta). 나머지는 e621 종류 자동 분류가 낫다
COVERAGE_TOPS = (500, 2000, 5000, 10000, None)
# 배치로 고르는 대상(앞이 먼저 — 태그 하나는 한 종류로)
KINDS = {
    "conflict": "폐기 DB 에서 옮긴 분류(확인 전) — e621 뜻으로 다시 본다",
    "suspect": "Danbooru 별칭인데 대상이 e621 에도 따로 있거나 meta 라 자동 same_as 를 하지 않은 태그 — 보류 상태",
    "auto": "Danbooru 별칭이라 그 대상의 분류를 자동으로 쓰는 태그 — 같은 뜻인지 확인",
    "meta": "e621 메타 태그(자동 meta/technical) — 구도·연도·매체 등으로",
    "hint": "e621 별칭에 Danbooru 태그 이름이 든 미분류 태그 — 같은 뜻(same_as)인지, 아니면 직접 분류",
    "top": "사용량 상위의 미분류 general",
}
TYPE_LABELS = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "species", 7: "meta"}
SITE_BATCH_FIELDS = ("name", "kind", "type", "count", "aliases", "current", "danbooru",
                     "wiki", "implies", "implied_by")
SITE_RESULT_EXTRA = ("same_as",)       # 결과 CSV 에 더 읽는 칸(같은 뜻이면 대상 태그, 분류 칸은 비움)
_EXPORT_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# e621 DText 중 뜻과 상관없는 것: 게시물 그림(thumb #1234), 색·위첨자 서식
_E621_NOISE = re.compile(r"thumb #\d+|\[/?(color(=[^\]]*)?|sup|sub|section(,expanded)?(=[^\]]*)?)\]", re.I)


def overlapping(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]],
                extra: Sequence[Mapping[str, str]], site: Site,
                config: Mapping[str, Any]) -> Tuple[Dict[str, SourceTag], core.TagPack]:
    """(빌드가 e621 태그에 밀려 건너뛰는 LATE_DBS 행의 키 → 그 e621 태그, 지금 입력 그대로 빌드한 팩).
    팩의 site 키로 정한다(빌드와 같은 판단 — LATE_DBS 는 e621 뒤라 넣어도 site 키는 같고, 다른 확장 DB 가 겹치지
    않는 폐기 행을 same_as 로 가리켜도 빌드가 멈추지 않는다)."""
    pack, _ = build_pack(source, {"date": ""}, overlay, extra, config, site)
    keys = set(pack["site"][site.id])
    by_key: Dict[str, SourceTag] = {}
    for t in site.tags:
        if t.type in E621_TYPES:
            by_key.setdefault(danbooru_key(t.name, t.type), t)
    kaomoji = _kaomoji(source, extra, site)
    out: Dict[str, SourceTag] = {}
    for r in extra:
        if r.get("db") not in LATE_DBS:
            continue
        key = core.normalize_key(r.get("name", ""), kaomoji)
        if key in keys and key in by_key:
            out[key] = by_key[key]
    return out, core.TagPack(pack)


def take_deprecated(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]],
                    extra: Sequence[Mapping[str, str]], site: Site, config: Mapping[str, Any],
                    today: Optional[str] = None) -> Tuple[Dict[str, Dict[str, str]], Dict[str, List[Dict[str, str]]], List[str]]:
    """(새 e621.csv 행 전체, LATE_DBS 마다 남는 행, 보고 줄).

    - e621 general·meta 와 겹치고 분류된(보류 아님) 행: 분류를 e621.csv 로 복사해 status=conflict(e621 뜻으로 다시 볼
      것). use_instead·메모는 note 로. same_as 행은 지금 대상의 분류로 풀어 쓴다(대상이 e621 의 자동 same_as 면 그대로
      옮겼을 때 연쇄 오류). e621.csv 에 이미 행이 있으면 그 행을 둔다.
    - 그 밖(종·작가·캐릭터·작품, 보류 행): e621 종류 자동 분류(종은 subject/species)나 보류가 맡는다 — 옮기지 않는다.
    - 겹친 폐기 DB 행은 모두 지운다(확장 DB README: 원본에 들어오면 이 행을 지운다).
    결과로 다시 빌드해 보고, 깨지면 쓰기 전에 builder.BuildError 를 낸다.
    """
    today = today or _date.today().isoformat()
    hits, pack = overlapping(source, overlay, extra, site, config)
    kaomoji = _kaomoji(source, extra, site)
    rows = {k: dict(v) for k, v in site.overlay.items()}
    kept: Dict[str, List[Dict[str, str]]] = {}
    moved, already, dropped = [], [], []
    for r in extra:
        db = r.get("db")
        if db not in LATE_DBS:
            continue
        key = core.normalize_key(r.get("name", ""), kaomoji)
        t = hits.get(key)
        if t is None:
            kept.setdefault(db, []).append({k: v for k, v in r.items() if k != "db"})
            continue
        cls = {k: r.get(k, "") for k in ("primary", "secondary", "rating", "regions", "identity")}
        if r.get("same_as"):
            target, _ = pack.resolve(core.normalize_key(r["same_as"], pack.kaomoji))
            cls = {"primary": target.primary, "secondary": "|".join(target.secondary), "rating": target.rating,
                   "regions": "|".join(target.regions), "identity": "1" if target.identity else ""} if target else {}
        primary = cls.get("primary") or "etc"
        if t.type not in KEEP_TYPES or primary == "etc":
            dropped.append(f"{r['name']} → {t.name}({_type_label(t.type)}, {r.get('primary') or '보류'})")
            continue
        if t.name in rows:
            already.append(t.name)
            continue
        note = [f"폐기 DB 에서 옮김({today}) — {site.id} 뜻으로 확인 전"]
        if r.get("same_as"):
            note.append(f"same_as {r['same_as']} 를 그 분류로 풀어 씀")
        if r.get("use_instead"):
            note.append("Danbooru 대체: " + ", ".join(split_list(r["use_instead"])))
        if r.get("note"):
            note.append(r["note"])
        new = {k: "" for k in SITE_FIELDS}
        new.update(cls)
        new.update({k: r.get(k, "") for k in ("by", "confidence")})
        new.update(name=t.name, source=site.id, status="conflict", note="; ".join(note))
        rows[t.name] = new
        moved.append(f"{r['name']} → {t.name} ({primary})")
    for db in {r.get("db") for r in extra if r.get("db") in LATE_DBS}:
        kept.setdefault(db, [])
    # 옮긴 결과로 다시 빌드해 본다(쓰기 전에 — 깨지면 BuildError)
    after = [r for r in extra if r.get("db") not in LATE_DBS] + [dict(r, db=db) for db, rs in kept.items() for r in rs]
    build_pack(source, {"date": ""}, overlay, after, config, Site(site.id, site.tags, site.meta, rows))
    lines = [f"폐기 DB 와 겹치는 {site.id} 태그 {len(hits):,}개 — 분류 옮김 {len(moved):,} · 이미 {site.id}.csv 에 있음 "
             f"{len(already):,} · 옮기지 않고 지움(종류 자동 분류·보류) {len(dropped):,}"]
    lines += [f"  옮김: {m}" for m in moved[:20]] + (["  …"] if len(moved) > 20 else [])
    lines += [f"  지움: {d}" for d in dropped[:20]] + (["  …"] if len(dropped) > 20 else [])
    return rows, kept, lines


def _type_label(t: int) -> str:
    return {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "species", 7: "meta"}.get(t, str(t))


def coverage(site: Site, pack: core.TagPack) -> List[Dict[str, Any]]:
    """e621 general 상위 N개 기준 사용량 커버리지(팩으로 판단 — 같은 이름은 Danbooru 분류, e621 행은 e621.csv·자동).

    classified = 보류(etc)가 아님, confirmed = 그중 자동(auto)·옮긴 뒤 확인 전(conflict)이 아님.
    """
    general = [t for t in site.tags if t.type == 0]
    total = sum(t.count for t in general) or 1
    out = []
    for n in COVERAGE_TOPS:
        sub = general[:n] if n else general
        hit = conf = 0
        held = 0
        for t in sub:
            info, _ = pack.resolve(core.normalize_key(t.name, pack.kaomoji))
            if info is None or info.primary == "etc":
                held += 1
                continue
            hit += t.count
            if info.status not in ("auto", "conflict", ""):
                conf += t.count
        out.append({"top": n or len(general), "range": sum(t.count for t in sub) / total,
                    "classified": hit / total, "confirmed": conf / total, "etc": held})
    return out


def breakdown(site: Site, pack: core.TagPack) -> Dict[str, Tuple[int, int]]:
    """e621 general 이 어디서 분류되는지: 이름 · (태그 수, 사용량)."""
    out: Dict[str, List[int]] = {}
    for t in site.tags:
        if t.type != 0:
            continue
        info, via_alias = pack.resolve(core.normalize_key(t.name, pack.kaomoji))
        if info is None:
            kind = "팩에 없음"
        elif info.db:
            kind = f"확장 DB({info.db})"
        elif not info.site:
            kind = "Danbooru 와 같은 이름(tags.csv)"
        elif info.primary == "etc":
            kind = f"{site.id} 보류·미분류"
        elif info.status == "auto":
            kind = f"{site.id} 자동 same_as(Danbooru 별칭)"
        elif info.status == "conflict":
            kind = f"{site.id} 폐기 DB 에서 옮김(확인 전)"
        else:
            kind = f"{site.id}.csv 분류"
        c = out.setdefault(kind, [0, 0])
        c[0] += 1
        c[1] += t.count
    return {k: (v[0], v[1]) for k, v in out.items()}


def format_stats(site: Site, pack_data: Mapping[str, Any]) -> str:
    """`tagorg stats --site e621` — pack_data 는 build_pack 이 만든 팩(이 원본 포함)."""
    pack = core.TagPack(pack_data)
    general = [t for t in site.tags if t.type == 0]
    total = sum(t.count for t in general) or 1
    lines = [f"{site.id} 원본 {len(site.tags):,}개 ({site.meta.get('file', '')}) — 팩의 {site.id} 행 "
             f"{len((pack_data.get('site') or {}).get(site.id, ())):,}개 · {site.id}.csv {len(site.overlay):,}행",
             f"팩 버전 {pack.version}", "", f"{site.id} general 이 분류되는 곳(태그 수 · 사용량):"]
    for kind, (n, c) in sorted(breakdown(site, pack).items(), key=lambda kv: -kv[1][1]):
        lines.append(f"  {kind:<34} {n:>7,}개 · {c / total:>6.1%}")
    lines += ["", "범위(general 상위)  범위 사용량   분류됨(보류 아님)   확인됨(auto·conflict 제외)   보류·미분류"]
    for r in coverage(site, pack):
        lines.append(f"{r['top']:>17,}  {r['range']:>10.1%}   {r['classified']:>14.1%}   {r['confirmed']:>22.1%}"
                     f"   {r['etc']:>8,}개")
    return "\n".join(lines)


# ---- 증거: e621 DB 내보내기(db_export)의 위키·함의 ----

def export_files(folder: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """폴더에서 파일 이름의 날짜가 가장 늦은 wiki_pages-*.csv(.gz) 와 tag_implications-*.csv(.gz). 없으면 None."""
    def latest(prefix: str) -> Optional[Path]:
        if not folder.is_dir():
            return None
        def order(p: Path) -> Tuple[str, str]:
            m = _EXPORT_DATE.search(p.name)
            return (m.group(1) if m else "", p.name)
        files = sorted(folder.glob(f"{prefix}-*.csv*"), key=order)
        return files[-1] if files else None
    return latest("wiki_pages"), latest("tag_implications")


def _open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def load_evidence(names: Set[str], wiki_path: Optional[Path], impl_path: Optional[Path],
                  counts: Optional[Mapping[str, int]] = None, implied_by: int = 12) -> Dict[str, Dict[str, Any]]:
    """{e621 이름: {"wiki": 본문 또는 None, "implies": [...], "implied_by": [...]}} — names 의 태그만.
    파일을 주지 않은 칸은 넣지 않는다(요약에서 '?'). implied_by 는 사용량(counts) 순 앞쪽만."""
    out: Dict[str, Dict[str, Any]] = {n: {} for n in names}
    csv.field_size_limit(1 << 30)
    if wiki_path is not None:
        for entry in out.values():
            entry["wiki"] = None
        with _open_csv(wiki_path) as f:
            for row in csv.DictReader(f):
                entry = out.get(row.get("title", ""))
                if entry is not None:
                    entry["wiki"] = row.get("body") or None
    if impl_path is not None:
        for entry in out.values():
            entry["implies"], entry["implied_by"] = [], []
        with _open_csv(impl_path) as f:
            for row in csv.DictReader(f):
                if row.get("status") != "active":
                    continue
                a, c = row.get("antecedent_name", ""), row.get("consequent_name", "")
                if a in out:
                    out[a]["implies"].append(c)
                if c in out:
                    out[c]["implied_by"].append(a)
        counts = counts or {}
        for entry in out.values():
            entry["implied_by"] = sorted(entry["implied_by"], key=lambda n: -counts.get(n, 0))[:implied_by]
    return out


def summarize(entry: Optional[Mapping[str, Any]], wiki_chars: int = 600) -> Dict[str, str]:
    """배치 칸으로 줄인 증거. 받지 않은 칸은 '?', 받았는데 없으면 빈 칸(위키 문법은 Danbooru 와 같은 DText)."""
    entry = entry or {}
    out = {}
    if "wiki" not in entry:
        out["wiki"] = "?"
    else:
        text = _E621_NOISE.sub(" ", evidence.clean_wiki(entry["wiki"]))
        text = re.sub(r"\s+", " ", text).strip()
        out["wiki"] = text[:wiki_chars] + ("…" if len(text) > wiki_chars else "")
    for k in ("implies", "implied_by"):
        out[k] = "?" if k not in entry else "|".join(entry[k])
    return out


# ---- 배치: 고르기·내보내기·반영·확정 ----

def _cls(info: Optional[core.TagInfo]) -> str:
    if info is None:
        return "(없음)"
    if info.primary == "etc":
        return "etc"
    parts = [info.primary]
    if info.rating:
        parts.append(f"r={info.rating}")
    if info.regions:
        parts.append("reg=" + "|".join(info.regions))
    if info.identity:
        parts.append("id=1")
    if info.secondary:
        parts.append("sec=" + "|".join(info.secondary))
    return " ".join(parts)


def select(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], extra: Sequence[Mapping[str, str]],
           site: Site, config: Mapping[str, Any], kinds: Sequence[str], top: int = 0) -> List[Dict[str, str]]:
    """배치 행(SITE_BATCH_FIELDS 의 증거 칸 빼고) — 사용량 순. 태그 하나는 KINDS 에서 앞선 종류 하나로.

    e621 에만 있는 general·meta 만 고른다(Danbooru 와 같은 이름은 tags.csv, 종·작가·캐릭터·작품은 종류 자동).
    e621.csv 에 이미 분류(보류 포함)된 행은 conflict(확인 전)만 고른다. top 은 general 사용량 순위.
    """
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ValueError(f"모르는 종류: {', '.join(unknown)} (쓸 수 있는 것: {', '.join(KINDS)})")
    pack_data, _ = build_pack(source, {"date": ""}, overlay, extra, config, site)
    pack = core.TagPack(pack_data)
    site_keys = set(pack_data["site"][site.id])
    kaomoji = pack.kaomoji
    d_keys = {danbooru_key(t.name, t.type) for t in source}
    d_alias: Dict[str, str] = {}                         # 빌드와 같은 Danbooru 별칭(가리기 전)
    for t in source:
        target = danbooru_key(t.name, t.type)
        for a in t.aliases:
            if not a.startswith("/"):
                ak = core.normalize_key(a, kaomoji)
                if ak and ak not in d_keys:
                    d_alias.setdefault(ak, target)
    own = {danbooru_key(t.name, t.type) for t in site.tags if t.type in E621_TYPES}
    out: List[Dict[str, str]] = []
    rank = 0
    for t in site.tags:
        if t.type == 0:
            rank += 1
        if t.type not in (0, 7):
            continue
        key = danbooru_key(t.name, t.type)
        if key not in site_keys:
            continue
        row = site.overlay.get(t.name) or {}
        info = pack.get(key)
        target = d_alias.get(key)
        tinfo = pack.get(target) if target else None
        auto_ok = tinfo is not None and tinfo.type == 0 and tinfo.main != "meta" and target not in own
        hints = []
        for a in t.aliases:
            ak = core.normalize_key(a, kaomoji)
            if ak in d_keys and ak != target:
                hints.append(f"{ak}={_cls(pack.get(ak))}")
        if row.get("status") == "conflict":
            kind = "conflict"
        elif row.get("primary") or row.get("same_as"):
            continue
        elif target is not None and not auto_ok:
            kind = "suspect"
        elif auto_ok:
            kind = "auto"
        elif t.type == 7:
            kind = "meta"
        elif hints:
            kind = "hint"
        elif top and rank <= top:
            kind = "top"
        else:
            continue
        if kind not in kinds:
            continue
        danbooru = []
        if target is not None:
            danbooru.append(f"별칭 대상 {target}={_cls(tinfo)}" + (" (e621 에도 따로 있음)" if target in own else ""))
        danbooru += hints[:4]
        current = _cls(info) + (f" [{info.status}]" if info is not None and info.status else "")
        if row.get("note"):
            current += f" — {row['note']}"
        out.append({"name": t.name, "kind": kind, "type": TYPE_LABELS.get(t.type, str(t.type)), "count": str(t.count),
                    "aliases": "|".join(a for a in t.aliases if not a.startswith("/"))[:120],
                    "current": current, "danbooru": " ; ".join(danbooru)})
    return out


def export_batches(rows: Sequence[Mapping[str, str]], out_dir: Path, name: str, size: int,
                   ev: Optional[Mapping[str, Mapping[str, Any]]] = None, group: bool = False) -> List[Path]:
    """배치 CSV(SITE_BATCH_FIELDS). ev 는 load_evidence 결과(없으면 증거 칸 '?'). group 이면 종류 → 같은 계열 순."""
    if group:
        order = list(KINDS)
        rows = sorted(rows, key=lambda r: (order.index(r["kind"]), batch.head_key(r["name"]), r["name"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str((len(rows) + size - 1) // size)))
    paths = []
    for i in range(0, len(rows), size):
        path = out_dir / f"{name}-{i // size + 1:0{width}d}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(SITE_BATCH_FIELDS)
            for r in rows[i:i + size]:
                s = summarize((ev or {}).get(r["name"])) if ev is not None else dict.fromkeys(
                    ("wiki", "implies", "implied_by"), "?")
                w.writerow([r[k] for k in SITE_BATCH_FIELDS[:7]] + [s["wiki"], s["implies"], s["implied_by"]])
        paths.append(path)
    return paths


def apply_results(results: Sequence[Mapping[str, str]], source: Sequence[SourceTag],
                  overlay: Mapping[str, Mapping[str, str]], extra: Sequence[Mapping[str, str]], site: Site,
                  config: Mapping[str, Any], by: str,
                  status: str = "llm") -> Tuple[Dict[str, Dict[str, str]], int, List[str], List[str]]:
    """(새 e621.csv 행 전체, 반영 수, 건너뜀, 오류). 오류가 하나라도 있으면 아무것도 바꾸지 않는다.

    결과 행은 분류 칸(batch.RESULT_FIELDS) 또는 same_as(같은 뜻인 태그 — 분류 칸은 비움). 사람이 고친(reviewed)·확정한
    (accepted) 행은 건너뛴다(conflict·llm 은 덮어씀). Danbooru 와 같은 이름은 tags.csv 몫이라 오류. 반영한 결과로
    다시 빌드해 보고(same_as 대상·연쇄) 깨지면 오류.
    """
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    names = {t.name for t in site.tags if t.type in E621_TYPES}
    d_names = {t.name for t in source}
    rows = {k: dict(v) for k, v in site.overlay.items()}
    errors: List[str] = []
    skipped: List[str] = []
    staged: Dict[str, Mapping[str, str]] = {}
    for r in results:
        name = r["name"]
        if name not in names and name.replace(" ", "_") in names:
            name = name.replace(" ", "_")
        where = r.get("_where", name)
        if name in d_names:
            errors.append(f"{where} Danbooru 에도 있는 태그입니다(tags.csv 에서 분류): {name}")
            continue
        if name not in names:
            errors.append(f"{where} {site.id} 원본에 없는 태그: {r['name']}")
            continue
        if r.get("same_as"):
            filled = [k for k in ("primary", "secondary", "rating", "regions", "identity") if r.get(k)]
            if filled:
                errors.append(f"{where} {name}: same_as 행은 분류 칸({'·'.join(filled)})을 비웁니다")
                continue
            # 분류 칸이 빈 행은 validate_row 를 거치지 않으므로 confidence 만 따로 본다(교차 리뷰)
            errs = [e for e in batch.validate_row(dict(r, primary="etc"), cats, regions) if e.startswith("confidence")]
            if errs:
                errors.append(f"{where} {name}: " + ", ".join(errs))
                continue
        else:
            errs = batch.validate_row(r, cats, regions)
            if errs:
                errors.append(f"{where} {name}: " + ", ".join(errs))
                continue
        if name in staged:
            errors.append(f"{where} 같은 태그가 두 번 나옵니다: {name}")
            continue
        current = rows.get(name) or {}
        if current.get("status") in batch.LOCKED:
            skipped.append(f"{name} (이미 {current['status']})")
            continue
        staged[name] = r
    unchanged = {k: dict(v) for k, v in site.overlay.items()}
    if errors:
        return unchanged, 0, skipped, errors
    for name, r in staged.items():
        base = dict(rows.get(name) or {k: "" for k in SITE_FIELDS})
        same_as = r.get("same_as", "")
        cls = {k: "" for k in ("primary", "secondary", "rating", "regions", "identity")} if same_as else {
            "primary": r["primary"], "secondary": "|".join(split_list(r.get("secondary"))),
            "rating": r.get("rating", ""), "regions": "|".join(split_list(r.get("regions"))),
            "identity": r.get("identity", "")}
        base.update(cls)
        base.update(name=name, source=site.id, same_as=same_as, status=status, by=by,
                    confidence=r.get("confidence", ""), note=r.get("note", ""))
        rows[name] = base
    try:
        build_pack(source, {"date": ""}, overlay, extra, config, Site(site.id, site.tags, site.meta, rows))
    except BuildError as e:
        return unchanged, 0, skipped, e.errors[:30]
    return rows, len(staged), skipped, []


def accept(rows: MutableMapping[str, Dict[str, str]]) -> List[str]:
    """LLM 분류(llm)를 확정(accepted)한다 — 사람 검수를 생략하고 분류를 위임받은 방식(review.accept 와 같음)."""
    done = []
    for name, row in rows.items():
        if (row.get("primary") or row.get("same_as")) and row.get("status") in ("llm", "seed"):
            row["status"] = "accepted"
            done.append(name)
    return done
