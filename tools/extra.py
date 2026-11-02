"""확장 DB(data/extra) 관리 — 이름 더하기, Danbooru 폐기 태그 받기, 배치 분류(내보내기·반영·확정).

이름은 원본 팩과 같이 소문자·밑줄→공백으로 적는다(이모티콘은 그대로). 원본 이름·원본 별칭·확장 DB 에 이미 있는
이름은 더하지 않는다. 분류 칸 규칙은 overlay 와 같다(docs/classification-guide.md §8).
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core

from . import batch, evidence
from .builder import E621_TYPES, TYPE_PRIMARY, _kaomoji, category_ids, danbooru_key
from .tagdb import EXTRA_FIELDS, LIST_SEP, Site, SourceTag, split_list

DEPRECATED = "deprecated"   # Danbooru 폐기 태그 DB
PAGE = 1000
# 반영할 때 결과 CSV 에서 더 읽는 칸(폐기 태그의 대체 태그 — 위키가 말하면 분류자가 적음)
EXTRA_RESULT_FIELDS = ("use_instead",)


def tag_name(name: str) -> str:
    """확장 DB 에 적을 이름이자 찾는 키: 소문자·밑줄→공백. 이모티콘처럼 밑줄이 글자인 이름은 소문자만
    (빌드가 확장 DB 의 이모티콘 모양 이름도 팩의 kaomoji 에 넣으므로 빌드 키와 같다)."""
    name = name.strip()
    low = name.lower()
    return low if core.is_kaomoji_name(low) else core.normalize_key(name)


def evidence_name(name: str) -> str:
    """증거 캐시(Danbooru 이름)의 키 — 공백을 밑줄로."""
    return name.strip().replace(" ", "_")


def known(source: Sequence[SourceTag], extra: Sequence[Mapping[str, str]],
          site: Optional[Site] = None) -> Dict[str, str]:
    """정규화 키 → 이미 어디에 있는지(원본 · 원본 별칭 → 대상 · DB:이름 · 두 번째 원본)."""
    kaomoji = _kaomoji(source, extra, site)
    out: Dict[str, str] = {}
    for t in source:
        out.setdefault(danbooru_key(t.name, t.type), "원본")
    for t in source:
        target = danbooru_key(t.name, t.type)
        for alias in t.aliases:
            if not alias.startswith("/"):
                out.setdefault(core.normalize_key(alias, kaomoji), f"원본 별칭 → {target}")
    for r in extra:
        out.setdefault(tag_name(r.get("name", "")), f"{r.get('db')}:{r.get('name')}")
    if site is not None:     # 두 번째 원본에 이미 있는 태그도 더하지 않는다(앞 층 확장 DB 가 이겨 e621 행을 가림)
        tags = [t for t in site.tags if t.type in E621_TYPES]
        for t in tags:
            out.setdefault(danbooru_key(t.name, t.type), site.id)
        for t in tags:
            for alias in t.aliases:
                if not alias.startswith("/"):
                    out.setdefault(core.normalize_key(alias, kaomoji), f"{site.id} 별칭 → {t.name}")
    return out


def add_names(extra: Sequence[Mapping[str, str]], db: str, names: Sequence[str], source: Sequence[SourceTag],
              fields: Optional[Mapping[str, str]] = None,
              per_name: Optional[Mapping[str, Mapping[str, str]]] = None,
              site: Optional[Site] = None) -> Tuple[List[Dict[str, str]], List[str], int]:
    """(그 DB 의 행 전체 — 새 행은 끝에, 건너뛴 이름과 사유, 새로 더한 수).

    fields 는 새 행 모두에, per_name[원래 이름] 은 그 행에만 채울 칸이다. 분류 칸을 비우면 보류 전(미분류)이다.
    """
    seen = known(source, extra, site)
    rows = [dict(r) for r in extra if r.get("db") == db]
    skipped: List[str] = []
    added = 0
    for raw in names:
        name = key = tag_name(raw)
        if not key:
            continue
        if key in seen:
            skipped.append(f"{raw} ({seen[key]})")
            continue
        row = {k: "" for k in EXTRA_FIELDS}
        row.update(fields or {})
        row.update((per_name or {}).get(raw, {}))
        row.update(name=name, db=db)
        rows.append(row)
        seen[key] = f"{db}:{name}"
        added += 1
    return rows, skipped, added


def lookup(extra: Sequence[Mapping[str, str]], raw: str, aliases: bool = False) -> Optional[Mapping[str, str]]:
    """프롬프트 표기(공백·밑줄·대문자·\\( \\))로 확장 DB 행을 찾는다. aliases 면 이름 대신 aliases 칸으로
    (빌드처럼 원본 별칭보다 뒤에 찾을 때)."""
    key = tag_name(raw.replace("\\(", "(").replace("\\)", ")"))
    if aliases:
        return next((r for r in extra if key in {tag_name(a) for a in split_list(r.get("aliases"))}), None)
    return next((r for r in extra if tag_name(r.get("name", "")) == key), None)


def db_rows(extra: Sequence[Mapping[str, str]], db: str,
            replaced: Sequence[Tuple[Mapping[str, str], Mapping[str, str]]] = ()) -> List[Mapping[str, str]]:
    """DB 하나의 행을 파일 순서대로(저장할 내용). replaced 는 (원래 행, 새 행) — 원래 행 바로 그 객체만 바꿔
    넣는다(같은 이름의 다른 행·다른 DB 의 동명 행은 그대로)."""
    by_id = {id(old): new for old, new in replaced}
    return [by_id.get(id(r), r) for r in extra if r.get("db") == db]


def read_name_file(path: Path) -> List[str]:
    """이름 목록 파일 — 한 줄에 하나, 또는 tagcomplete 형식 CSV(첫 칸이 이름, 따옴표 가능). 빈 줄·# 줄은 건너뛴다.
    UTF-8(BOM 가능)이 아니면 cp949 로 읽는다(한국어 윈도우 엑셀로 저장한 tagcomplete 파일)."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    names = []
    for fields in csv.reader(io.StringIO(text)):
        first = fields[0].strip() if fields else ""
        if first and not first.startswith("#"):
            names.append(first)
    return names


# ---- Danbooru 폐기 태그 ----

def fetch_deprecated(get: evidence.Getter = evidence.http_get, delay: float = 0.8) -> List[Dict[str, Any]]:
    """Danbooru 폐기(is_deprecated) 태그 전부 — [{name, post_count, category}], 사용량 순.

    사용량 순 페이지는 0회 태그끼리 순서가 정해지지 않아 페이지 경계에서 빠지거나 겹칠 수 있으므로, id 커서
    (page=b<id>)로 넘긴다.
    """
    rows: List[Dict[str, Any]] = []
    cursor = None
    while True:
        params: Dict[str, Any] = {"search[is_deprecated]": "true", "limit": PAGE,
                                  "only": "id,name,post_count,category"}
        if cursor is not None:
            params["page"] = f"b{cursor}"
        data = get("/tags.json", params)
        if not isinstance(data, list):
            raise evidence.FetchError(f"/tags.json: 예상과 다른 응답 {type(data).__name__}")
        rows += data
        if len(data) < PAGE:
            break
        cursor = min(int(r["id"]) for r in data)
        time.sleep(delay)
    unique = {r["name"]: r for r in rows}
    return sorted(({"name": r["name"], "post_count": int(r.get("post_count") or 0), "category": int(r["category"])}
                   for r in unique.values()), key=lambda r: (-r["post_count"], r["name"]))


def add_deprecated(tags: Sequence[Mapping[str, Any]], extra: Sequence[Mapping[str, str]],
                   source: Sequence[SourceTag], site: Optional[Site] = None) -> Tuple[List[Dict[str, str]], List[str], int]:
    """폐기 태그를 deprecated DB 에 더한다. general 이 아닌 태그(작가·작품·캐릭터·메타)는 원본과 같이 종류로
    자동 분류(status auto)하고, general 은 분류 칸을 비운다(배치로 분류). 두 번째 원본(e621)에 있는 이름은 더하지
    않는다(빌드가 e621 뒤에 합쳐 어차피 가려진다)."""
    per_name = {}
    for t in tags:
        primary = TYPE_PRIMARY.get(t["category"])
        if primary:
            per_name[t["name"]] = {"primary": primary, "status": "auto"}
    return add_names(extra, DEPRECATED, [t["name"] for t in tags], source, per_name=per_name, site=site)


def record_post_counts(tags: Sequence[Mapping[str, Any]], cache: MutableMapping[str, Dict[str, Any]]) -> None:
    """받은 사용량을 증거 캐시에 적는다(배치의 count 칸). 게시물이 0 이면 연관 태그도 없으므로 빈 목록으로 둔다
    — 태그마다 한 번씩 묻는 연관 태그 요청을 아낀다."""
    today = _date.today().isoformat()
    for t in tags:
        entry = cache.setdefault(t["name"], {})
        entry["post_count"] = t["post_count"]
        if t["post_count"] == 0 and "related" not in entry:
            entry["related"] = []
            entry.setdefault("fetched", today)


# ---- 배치 분류 ----

def pending(extra: Sequence[Mapping[str, str]], db: str, held: bool = False) -> List[Mapping[str, str]]:
    """db 의 미분류 행(primary 빈칸, same_as 아님). held 면 보류(etc) 행 중 사람이 고치지 않은 것(reviewed 제외)."""
    out = []
    for r in extra:
        if r.get("db") != db or r.get("same_as"):
            continue
        if held:
            if r.get("primary") == "etc" and r.get("status") != "reviewed":
                out.append(r)
        elif not r.get("primary"):
            out.append(r)
    return out


def export_batches(rows: Sequence[Mapping[str, str]], out_dir: Path, name: str, size: int, group: bool = False,
                   cache: Optional[Mapping[str, Mapping[str, Any]]] = None) -> List[Path]:
    """batch.export_batches 와 같은 형식. 이름은 Danbooru 표기(밑줄), count 는 증거 캐시의 사용량(없으면 빈 칸)."""
    tags = []
    for r in rows:
        ev = evidence_name(r["name"])
        count = (cache or {}).get(ev, {}).get("post_count", "")
        tags.append(SourceTag(ev, 0, count, tuple(split_list(r.get("aliases")))))   # type: ignore[arg-type]
    if group:
        tags = batch.group_by_head(tags)
    return batch.export_batches(tags, out_dir, name, size, cache)


def apply_results(results: Sequence[Mapping[str, str]], extra: Sequence[Mapping[str, str]], db: str,
                  config: Mapping[str, Any], by: str, status: str = "llm",
                  reclassify_etc: bool = False) -> Tuple[List[Dict[str, str]], int, List[str], List[str]]:
    """(그 DB 의 행 전체, 반영 수, 건너뜀 사유, 오류). 오류가 하나라도 있으면 아무것도 반영하지 않는다.

    batch.apply_results 와 같은 규칙: 확정(reviewed·accepted) 행은 건너뛰고, reclassify_etc 면 accepted 보류(etc)
    행은 다시 분류한다. same_as 행은 대상의 분류를 쓰므로 받지 않는다.
    """
    cats = category_ids(config["taxonomy"])
    regions = set(config["rules"].get("regions", ()))
    rows = [dict(r) for r in extra if r.get("db") == db]
    index = {tag_name(r["name"]): i for i, r in enumerate(rows)}
    errors: List[str] = []
    skipped: List[str] = []
    staged: Dict[int, Mapping[str, str]] = {}
    for res in results:
        where = res.get("_where", res["name"])
        i = index.get(tag_name(res["name"]))
        if i is None:
            errors.append(f"{where} {db} 에 없는 태그: {res['name']}")
            continue
        errs = batch.validate_row(res, cats, regions)
        if errs:
            errors.append(f"{where} {res['name']}: " + ", ".join(errs))
            continue
        if i in staged:
            errors.append(f"{where} 같은 태그가 두 번 나옵니다: {res['name']}")
            continue
        current = rows[i]
        if current.get("same_as"):
            errors.append(f"{where} same_as 행은 대상의 분류를 씁니다: {current['name']}")
            continue
        reopen = reclassify_etc and current.get("status") == "accepted" and current.get("primary") == "etc"
        if current.get("status") in batch.LOCKED and not reopen:
            skipped.append(f"{current['name']} (이미 {current['status']})")
            continue
        staged[i] = res
    if errors:
        return rows, 0, skipped, errors
    for i, res in staged.items():
        rows[i].update(
            primary=res["primary"], secondary=LIST_SEP.join(split_list(res.get("secondary"))),
            rating=res.get("rating", ""), regions=LIST_SEP.join(split_list(res.get("regions"))),
            identity=res.get("identity", ""), status=status, by=by, confidence=res.get("confidence", ""),
            note=res.get("note", ""),
        )
        if "use_instead" in res:          # 결과 CSV 에 칸이 있으면(빈 값이면 지움) — 칸이 없으면 그대로
            rows[i]["use_instead"] = LIST_SEP.join(split_list(res["use_instead"]))
    return rows, len(staged), skipped, []


def accept(rows: Sequence[MutableMapping[str, str]]) -> List[str]:
    """LLM·시드 분류를 확정(accepted)한다(review.accept 와 같은 위임 규칙)."""
    done = []
    for row in rows:
        if row.get("primary") and row.get("status") in ("llm", "seed"):
            row["status"] = "accepted"
            done.append(row["name"])
    return done


def summary(extra: Sequence[Mapping[str, str]]) -> List[str]:
    """DB 마다 행 수와 분류됨·보류·미분류·same_as 수."""
    lines = []
    for db in dict.fromkeys(r["db"] for r in extra):
        rows = [r for r in extra if r["db"] == db]
        same = sum(1 for r in rows if r.get("same_as"))
        held = sum(1 for r in rows if r.get("primary") == "etc")
        todo = sum(1 for r in rows if not r.get("primary") and not r.get("same_as"))
        lines.append(f"{db}: {len(rows):,}행 — 분류 {len(rows) - same - held - todo:,} · 보류(etc) {held:,}"
                     f" · 미분류 {todo:,}" + (f" · same_as {same:,}" if same else ""))
    return lines
