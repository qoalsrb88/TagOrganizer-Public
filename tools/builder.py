"""런타임 팩 빌드 — 원본 CSV + overlay + 확장 DB(data/extra) + 두 번째 원본(e621) + 설정 JSON → tagpack.json.gz.

overlay에 없는 태그는 type으로 자동 분류한다(작가·작품·캐릭터·메타). general 태그는
overlay에 없으면 etc(미분류)로 들어간다. 확장 DB 는 원본에 없는 태그만 더한다(겹치면 원본이
이긴다). 팩 버전은 원본 날짜 + 분류 데이터 해시라서 같은 입력이면 같은 팩이 나온다.

두 번째 원본(e621, docs/e621.md)이 있으면 이름을 층 순서대로 놓고 그다음 별칭을 더한다(이름 > 별칭):
Danbooru → 확장 DB(LATE_DBS 밖: 모델·내 태그) → e621 → LATE_DBS(폐기). Danbooru 와 같은 이름은 Danbooru 행(tags.csv
분류) 하나를 함께 쓰고, e621 에만 있는 이름은 e621.csv 분류 → 종류 자동 분류 → Danbooru 별칭이면 그 대상의 분류
(자동 same_as) 순으로 정한다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import bmk_tag_organizer_core as core

from .tagdb import (
    EXTRA_DUPLICATE, EXTRA_FIELDS, EXTRA_ID, EXTRA_OVERFLOW, RESTORE_HINT, STATUSES, Site, SourceTag, restore_hint,
    split_list,
)

TYPE_PRIMARY = {1: "style/artist", 3: "series", 4: "subject/character", 5: "meta/technical"}
# e621 종류 → (팩 type, 자동 분류). 팩 type 은 Danbooru 번호로 맞춘다(종은 general, 메타는 meta). 표에 없는 종류
# (contributor 2 · invalid 6 · lore 8)는 그림 내용이 아니라 팩에 넣지 않는다
E621_TYPES = {0: (0, ""), 1: (1, "style/artist"), 3: (3, "series"), 4: (4, "subject/character"),
              5: (0, "subject/species"), 7: (5, "meta/technical")}
AUTO_SAME_AS_TYPES = (0, 7)   # Danbooru 별칭인 e621 이름 중 그 대상의 분류를 쓰는 종류(general·meta). 종은 종류로
# 두 번째 원본 뒤에 합치는 확장 DB — Danbooru 에서 폐기된 태그라, e621 이 지금 쓰는 이름이면 e621 뜻이 맞다
LATE_DBS = ("deprecated",)
CLASS_FIELDS = ("secondary", "rating", "regions", "identity")    # primary 말고 분류 칸
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")                # 확장 DB models 칸의 모델 ID(nai, illustrious …)
_ROW_DEFAULTS = (None, 0, 0, "etc", "", [], [], 0, "", None)     # core.COLUMNS 순서, 팩 행 끝에서 줄이는 기본값


class BuildError(Exception):
    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = list(errors)


def danbooru_key(name: str, type_: int) -> str:
    if type_ == 0 and core.is_kaomoji_name(name):
        return name
    return core.normalize_key(name)


def category_ids(taxonomy: Mapping[str, Any]) -> Dict[str, Set[str]]:
    cats = {c["id"]: {s["id"] for s in c.get("subs", ())} for c in taxonomy["categories"]}
    for sys_cat in core.SYSTEM_CATEGORIES:
        cats.setdefault(sys_cat, set())
    return cats


def _valid_category(value: str, cats: Mapping[str, Set[str]]) -> bool:
    main, _, sub = value.partition("/")
    return main in cats and (not sub or sub in cats[main])


def _row(key: str, type_: int, count: int, primary: str, rating: str = "", secondary: Sequence[str] = (),
         regions: Sequence[str] = (), identity: int = 0, status: str = "", display: str = "") -> List[Any]:
    row: List[Any] = [key, type_, count, primary, rating, list(secondary), list(regions), identity, status,
                      display if display and display != key else None]
    while len(row) > 4 and row[-1] == _ROW_DEFAULTS[len(row) - 1]:
        row.pop()
    return row


def content_hash(overlay: Mapping, extra: Sequence, config: Mapping, site: Optional[Site] = None) -> str:
    # 확장 DB 행은 칸을 맞춰 넣는다(파일에서 읽은 행과 칸이 빠진 dict 가 같은 내용이면 같은 버전)
    extra_rows = [{k: r.get(k, "") for k in ("db",) + EXTRA_FIELDS} for r in extra]
    data: Dict[str, Any] = {"overlay": [overlay[k] for k in sorted(overlay)], "extra": extra_rows, "config": config}
    if site is not None:     # 두 번째 원본이 없으면 예전과 같은 해시(같은 팩 버전)
        data["site"] = {"id": site.id, "file": site.meta.get("file", ""),
                        "overlay": [site.overlay[k] for k in sorted(site.overlay)]}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def overlay_problems(overlay: Mapping[str, Mapping[str, str]], source_names: Set[str],
                     cats: Mapping[str, Set[str]], regions_vocab: Set[str]) -> List[str]:
    """overlay 행 검사(빌드를 막는 오류). 손 수정·엑셀 저장으로 깨진 행을 잡는다."""
    errors: List[str] = []
    broken_names = 0
    for name, ov in overlay.items():
        where = f"overlay[{name}]"
        errors += _field_problems(where, ov, cats, regions_vocab)
        if name not in source_names and not ov.get("missing_since"):
            # 원본에서 사라진 태그는 import 가 missing_since 를 적는다. 그런데도 없으면 이름이 깨진 것
            # (엑셀이 =_= 를 수식으로, 9/11 을 날짜로 바꾸는 등)이라 조용히 넘어가지 않는다.
            errors.append(f"{where} 원본 CSV에 없는 태그입니다(이름이 깨졌거나 잘못 입력됨)")
            broken_names += 1
    if broken_names:
        errors.append(RESTORE_HINT)
    return errors


def site_problems(site: Site, danbooru_names: Set[str], cats: Mapping[str, Set[str]],
                  regions_vocab: Set[str]) -> List[str]:
    """두 번째 원본의 분류 파일(e621.csv) 행 검사 — overlay 와 같은 칸 검사에 이름·same_as 규칙."""
    errors: List[str] = []
    names = {t.name for t in site.tags}
    broken_names = 0
    for name, row in site.overlay.items():
        where = f"{site.id}[{name}]"
        errors += _field_problems(where, row, cats, regions_vocab)
        if name in danbooru_names:
            errors.append(f"{where} Danbooru 에도 있는 태그입니다 — 같은 이름은 data/overlay/tags.csv 한 행으로 분류합니다"
                          f"(뜻이 {site.id} 쪽이면 그 행의 source 를 {site.id} 로)")
        elif name not in names and not row.get("missing_since"):
            errors.append(f"{where} {site.id} 원본 CSV에 없는 태그입니다(이름이 깨졌거나 잘못 입력됨)")
            broken_names += 1
        filled = [k for k in CLASS_FIELDS if row.get(k)]
        if row.get("same_as"):
            if row.get("primary") or filled:
                errors.append(f"{where} same_as 행은 분류 칸(primary·{'·'.join(CLASS_FIELDS)})을 비웁니다"
                              " — 분류는 대상 태그에서 가져옵니다")
        elif not row.get("primary") and filled:
            errors.append(f"{where} primary 가 비었는데 {'·'.join(filled)} 가 있습니다")
    if broken_names:
        errors.append(restore_hint(f"data/overlay/{site.id}.csv"))
    return errors


def _field_problems(where: str, row: Mapping[str, str], cats: Mapping[str, Set[str]],
                    regions_vocab: Set[str]) -> List[str]:
    """분류 칸(primary·secondary·rating·regions·identity·status) 검사 — overlay 와 확장 DB 가 같이 쓴다."""
    errors: List[str] = []
    primary = row.get("primary", "")
    if primary and not _valid_category(primary, cats):
        errors.append(f"{where} primary 가 분류 체계에 없습니다: {primary}")
    elif primary in core.SYSTEM_CATEGORIES:
        filled = [k for k in CLASS_FIELDS if row.get(k)]
        if primary != "etc":
            errors.append(f"{where} 시스템 카테고리는 etc 만 씁니다: {primary}")
        elif filled:
            errors.append(f"{where} etc(보류) 행은 {'·'.join(filled)} 를 비웁니다")
    for sec in split_list(row.get("secondary")):
        if not _valid_category(sec, cats):
            errors.append(f"{where} secondary 가 분류 체계에 없습니다: {sec}")
    if row.get("rating", "") not in ("",) + core.RATINGS:
        errors.append(f"{where} rating 은 g/s/q/e 중 하나여야 합니다: {row.get('rating')}")
    for reg in split_list(row.get("regions")):
        if reg not in regions_vocab:
            errors.append(f"{where} regions 에 없는 부위입니다: {reg}")
    if row.get("identity", "") not in ("", "0", "1"):
        errors.append(f"{where} identity 는 0 또는 1 이어야 합니다: {row.get('identity')}")
    if row.get("status", "") not in ("",) + STATUSES:
        errors.append(f"{where} status 가 잘못됐습니다: {row.get('status')}")
    return errors


def extra_problems(extra: Sequence[Mapping[str, str]], cats: Mapping[str, Set[str]], regions_vocab: Set[str],
                   kaomoji: Sequence[str] = ()) -> List[str]:
    """확장 DB 행 검사(빌드를 막는 오류). 원본과 겹치는지·same_as 대상과 연쇄는 _merge_extra 가 본다(check_extra)."""
    errors: List[str] = []
    seen: Dict[str, str] = {}
    reported: Set[Tuple[str, str]] = set()      # (DB, 칸) — 파일 단위 문제는 한 번만 알린다
    for row in extra:
        db, name = row.get("db", ""), row.get("name", "")
        where = f"extra[{db}:{name}]"
        if not EXTRA_ID.match(db) and (db, "db") not in reported:
            reported.add((db, "db"))
            errors.append(f"data/extra/{db}.csv 파일 이름(DB ID)은 영문 소문자로 시작하고 소문자·숫자·밑줄만 씁니다")
        for col in row:
            if col in EXTRA_FIELDS or col == "db" or (db, col) in reported:
                continue
            reported.add((db, col))
            if col == EXTRA_OVERFLOW:
                errors.append(f"extra[{db}] 값이 머리줄보다 많은 행이 있습니다({name}) — 쉼표가 든 값은 따옴표로 감쌉니다")
            elif col.endswith(EXTRA_DUPLICATE):
                errors.append(f"extra[{db}] 머리줄에 같은 칸이 두 번 있습니다: {col[:-len(EXTRA_DUPLICATE)]}")
            else:
                errors.append(f"extra[{db}] 모르는 칸입니다: {col} (쓸 수 있는 칸은 data/extra/README.md)")
        key = core.normalize_key(name, kaomoji)
        if not key:
            errors.append(f"{where} 이름이 비었습니다")
            continue
        if key in seen:
            errors.append(f"{where} 가 {seen[key]} 와 같은 태그입니다(확장 DB 에서 태그 하나는 한 곳에만)")
        else:
            seen[key] = f"{db}:{name}"
        errors += _field_problems(where, row, cats, regions_vocab)
        filled = [k for k in CLASS_FIELDS if row.get(k)]
        if row.get("same_as"):
            if row.get("primary") or filled:
                errors.append(f"{where} same_as 행은 분류 칸(primary·{'·'.join(CLASS_FIELDS)})을 비웁니다"
                              " — 분류는 대상 태그에서 가져옵니다")
        elif not row.get("primary") and filled:
            errors.append(f"{where} primary 가 비었는데 {'·'.join(filled)} 가 있습니다")
        for model in split_list(row.get("models")):
            if not MODEL_ID.match(model):
                errors.append(f"{where} models 는 영문 소문자·숫자·점·하이픈으로 된 ID 를 | 로 잇습니다: {model}")
    return errors


def check_extra(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]],
                extra: Sequence[Mapping[str, str]], config: Mapping[str, Any],
                site: Optional[Site] = None) -> Tuple[List[str], List[str]]:
    """(오류, 경고) — 빌드와 같은 확장 DB·두 번째 원본 검사(행 검사 + 원본과 겹침·same_as 대상·별칭 가림·use_instead).
    check 용."""
    errors: List[str] = []
    warnings: List[str] = []
    cats, regions_vocab = category_ids(config["taxonomy"]), set(config["rules"].get("regions", ()))
    kaomoji = _kaomoji(source, extra, site)
    errors += extra_problems(extra, cats, regions_vocab, kaomoji)
    if site is not None:
        errors += site_problems(site, {t.name for t in source}, cats, regions_vocab)
    rows, aliases = _source_rows(source, overlay, kaomoji, [])
    _merge_layers(extra, site, rows, aliases, overlay, kaomoji, errors, warnings)
    return errors, warnings


def _kaomoji(source: Sequence[SourceTag], extra: Sequence[Mapping[str, str]] = (),
             site: Optional[Site] = None) -> List[str]:
    """밑줄이 글자인 이름(정규화에서 밑줄을 공백으로 바꾸지 않음) — 원본(Danbooru·e621) general 이모티콘과 확장 DB 의
    이모티콘 모양 이름(소문자). 확장 DB 것도 넣어야 ^__^ 와 ^___^ 가 같은 키(^ ^)로 겹치지 않는다."""
    tags = list(source) + (list(site.tags) if site is not None else [])
    names = {t.name for t in tags if t.type == 0 and core.is_kaomoji_name(t.name)}
    names |= {n for n in (r.get("name", "").strip().lower() for r in extra) if core.is_kaomoji_name(n)}
    return sorted(names)


def build_pack(
    source: Sequence[SourceTag],
    source_meta: Mapping[str, Any],
    overlay: Mapping[str, Mapping[str, str]],
    extra: Sequence[Mapping[str, str]],
    config: Mapping[str, Any],
    site: Optional[Site] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """(팩, 경고 목록). 오류가 있으면 BuildError. site 는 두 번째 원본(e621, 없으면 Danbooru·확장 DB 만)."""
    errors: List[str] = []
    warnings: List[str] = []
    cats = category_ids(config["taxonomy"])
    regions_vocab = set(config["rules"].get("regions", ()))

    kaomoji = _kaomoji(source, extra, site)

    errors += overlay_problems(overlay, {t.name for t in source}, cats, regions_vocab)
    errors += extra_problems(extra, cats, regions_vocab, kaomoji)
    if site is not None:
        errors += site_problems(site, {t.name for t in source}, cats, regions_vocab)

    rows, aliases = _source_rows(source, overlay, kaomoji, warnings)
    extra_dbs, site_keys = _merge_layers(extra, site, rows, aliases, overlay, kaomoji, errors, warnings)

    rules = config["rules"]
    for group in ("framing", "view", "gaze"):
        cfg = rules.get(group) or {}
        for gate in cfg.get("gates", ()):
            for reg in gate.get("hide", ()):
                if reg not in regions_vocab:
                    errors.append(f"rules.{group} hide 에 없는 부위입니다: {reg}")
            _check_rule_tags(f"rules.{group}", gate.get("tags", ()), rows, aliases, warnings)
            _check_rule_tags(f"rules.{group}.reveal", (gate.get("reveal") or {}).keys(), rows, aliases, warnings)
            _check_rule_tags(f"rules.{group}.keep", gate.get("keep", ()), rows, aliases, warnings)
        for block in cfg.get("blocks", ()):
            _check_rule_tags(f"rules.{group}.blocks", list(block.get("tags", ())) + list(block.get("block", ())),
                             rows, aliases, warnings)
        _check_rule_tags(f"rules.{group}.exempt", cfg.get("exempt", ()), rows, aliases, warnings)
    ms = rules.get("multi_subject") or {}
    _check_rule_tags("rules.multi_subject", list(ms.get("any", ())) + list(ms.get("count_single", ())),
                     rows, aliases, warnings)
    for rule in rules.get("explicit", ()):
        rid = rule.get("id", "?")
        for k in ("when_any", "unless_any", "remove"):
            _check_rule_tags(f"rules.explicit[{rid}].{k}", rule.get(k, ()), rows, aliases, warnings)
        for sel in rule.get("remove_selectors", ()):
            err = core.Selector.parse(sel).validate(cats)
            if err:
                errors.append(f"rules.explicit[{rid}] {err}")

    for lid, layout in config["layouts"].items():
        for sec in layout.get("sections", ()):
            for sel in list(sec.get("include", ())) + list(sec.get("except", ())):
                err = core.Selector.parse(sel).validate(cats)
                if err:
                    errors.append(f"layouts[{lid}].{sec.get('id')} {err}")
    if "default" not in config["layouts"]:
        errors.append("layouts 에 default 가 없습니다")
    for pid, preset in config["presets"].items():
        for sel in preset.get("exclude", ()):
            err = core.Selector.parse(sel).validate(cats)
            if err:
                errors.append(f"presets[{pid}] {err}")
    if "sort_only" not in config["presets"]:
        errors.append("presets 에 sort_only 가 없습니다")

    # 배열 세팅의 크롭/시점/시선/포즈 퀵 목록과 팩에 없는 토큰 추정(guess.json)
    quick = rules.get("quick") or {}
    for group, target in core.OVERRIDE_TARGETS.items():
        _check_rule_tags(f"rules.quick.{group}", quick.get(group, ()), rows, aliases, warnings)
        lifts = set(quick.get(f"{group}_lift", ()))
        for tag in quick.get(group, ()):
            if tag in rows and rows[tag][3] != target and tag not in lifts:
                warnings.append(f"rules.quick.{group} '{tag}' 는 {target} 가 아닙니다({rows[tag][3]}) — 덮어쓸 수 없음")
    for group, title in (("gaze", "시선"), ("view", "시점"), ("pose", "포즈")):
        for tag, others in (quick.get(f"{group}_conflicts") or {}).items():
            for name in (tag, *others):
                if name not in quick.get(group, ()):
                    warnings.append(f"rules.quick.{group}_conflicts '{name}' 는 {title} 퀵 목록에 없습니다")
    pose_scope = (quick.get("pose_scope") or {}).get("tags", ())
    _check_rule_tags("rules.quick.pose_scope", pose_scope, rows, aliases, warnings)
    for tag in pose_scope:
        if tag in rows and rows[tag][3] != core.OVERRIDE_TARGETS["pose"]:
            warnings.append(f"rules.quick.pose_scope '{tag}' 는 {core.OVERRIDE_TARGETS['pose']} 가 아닙니다({rows[tag][3]}) — 바뀌지 않음")
    guess = dict(config.get("guess") or {})
    try:
        core.compile_guess_rules(guess.get("patterns", ()), SimpleNamespace(rules=rules, categories=cats), "guess")
    except ValueError as e:
        errors.append(str(e))
    for head in guess.get("head_categories", ()):
        if head not in cats or head in core.SYSTEM_CATEGORIES:
            errors.append(f"guess.head_categories 에 없는 카테고리입니다: {head}")
    species = guess.get("species") or {}
    for where, value in [("primary", species.get("primary"))] + [("animals", a) for a in species.get("animals", ())]:
        if value and (not _valid_category(value, cats) or value.partition("/")[0] in core.SYSTEM_CATEGORIES):
            errors.append(f"guess.species.{where} 에 없는 카테고리입니다: {value}")

    if errors:
        raise BuildError(errors)

    date = str(source_meta.get("date", "")).replace("-", ".")
    version = f"{date}+{content_hash(overlay, extra, config, site)}"
    pack: Dict[str, Any] = {
        "format": core.PACK_FORMAT,
        "schema": core.PACK_SCHEMA,
        "version": version,
        "source": {k: source_meta[k] for k in ("file", "date", "rows") if k in source_meta},
        "taxonomy": config["taxonomy"],
        "rules": config["rules"],
        "layouts": config["layouts"],
        "presets": config["presets"],
        "guess": guess,
        "kaomoji": kaomoji,
        "columns": list(core.COLUMNS),
        "tags": list(rows.values()),
        "aliases": aliases,
        "extra": extra_dbs,
    }
    if site is not None:
        # 두 번째 원본에서 온 행의 키(코어 v1.4 TagInfo.site)와 그 원본 파일. 옛 코어는 두 칸을 무시한다
        pack["site"] = {site.id: site_keys}
        pack["site_sources"] = {site.id: {k: site.meta[k] for k in ("file", "date", "rows") if k in site.meta}}
    return pack, warnings


def _source_rows(source: Sequence[SourceTag], overlay: Mapping[str, Mapping[str, str]], kaomoji: Sequence[str],
                 warnings: List[str]) -> Tuple[Dict[str, List[Any]], Dict[str, str]]:
    """원본 태그의 팩 행(overlay 분류, 없으면 type 자동·etc)과 원본 별칭."""
    rows: Dict[str, List[Any]] = {}
    for t in source:
        key = danbooru_key(t.name, t.type)
        if key in rows:
            warnings.append(f"정규화 후 키가 겹쳐 뒤쪽을 건너뜁니다: {t.name} → {key}")
            continue
        ov = overlay.get(t.name)
        if ov and ov.get("primary"):
            rows[key] = _row(
                key, t.type, t.count, ov["primary"], ov.get("rating", ""),
                split_list(ov.get("secondary")), split_list(ov.get("regions")),
                1 if ov.get("identity") == "1" else 0, ov.get("status", ""),
            )
        elif t.type in TYPE_PRIMARY:
            rows[key] = _row(key, t.type, t.count, TYPE_PRIMARY[t.type], status="auto")
        else:
            rows[key] = _row(key, t.type, t.count, "etc")

    aliases: Dict[str, str] = {}
    for t in source:
        target = danbooru_key(t.name, t.type)
        if target not in rows:
            continue
        for alias in t.aliases:
            if alias.startswith("/"):        # 자동완성 단축어(/lh 등)
                continue
            akey = core.normalize_key(alias, kaomoji)
            if akey and akey not in rows and akey not in aliases:
                aliases[akey] = target       # 같은 별칭이 여럿이면 post_count 큰 쪽(먼저 나온 쪽)
    return rows, aliases


def _merge_layers(extra: Sequence[Mapping[str, str]], site: Optional[Site], rows: Dict[str, List[Any]],
                  aliases: Dict[str, str], overlay: Mapping[str, Mapping[str, str]], kaomoji: Sequence[str],
                  errors: List[str], warnings: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """확장 DB 와 두 번째 원본을 Danbooru 의 rows·aliases 에 더한다. ({DB ID: [키]}, [두 번째 원본 키]) — 팩의
    extra·site.

    이름을 층 순서(Danbooru → 확장 DB(LATE_DBS 밖) → 두 번째 원본 → LATE_DBS)로 모두 놓은 뒤 별칭을 같은 순서로
    더한다(이름 > 별칭).
    - 앞 층과 이름이 겹치면 앞 층이 이긴다. 확장 DB 가 Danbooru 와 겹치면 경고(overlay 로 분류하라)하고, LATE_DBS 가
      두 번째 원본과 겹치면 한 줄로 묶어 경고한다(`tagorg e621 take-deprecated` 로 옮김). 두 번째 원본이 Danbooru·앞
      확장 DB 와 겹치는 것은 정상이다(Danbooru 와 같은 이름은 tags.csv 한 행, 모델·내 태그는 사용자가 고른 것).
    - 원본 별칭과 이름이 겹치면 이름이 별칭을 가린다(이름이 별칭보다 먼저 찾아지고, 출력에서 대표 이름으로 바뀌지
      않는다). 확장 DB 는 NAI 가 표기를 바꾼 태그(peace sign 은 v 의 별칭)를 그대로 내보내려는 경우라 same_as 가 그
      별칭의 대상을 가리키면 조용히, 아니면 경고한다. 두 번째 원본은 조용히 가린다(e621 표기 유지가 목적).
    - same_as 행(확장 DB·e621.csv·e621 자동)은 이름을 모두 놓은 뒤 대상(원본 이름·원본 별칭·same_as 가 아닌 행)의
      분류를 그대로 쓴다.
    """
    dbs: Dict[str, List[str]] = {}
    added: Set[str] = set()                                  # 확장 DB 로 놓은 키
    pending: Dict[str, Mapping[str, str]] = {}               # 확장 DB same_as 행
    site_keys: List[str] = []
    site_set: Set[str] = set()
    site_pending: Dict[str, Tuple[SourceTag, int, str, bool]] = {}   # 키 → (태그, 팩 type, same_as, 자동인가)
    late_overlap: List[str] = []
    danbooru_aliases = dict(aliases)                         # 가리기 전(자동 same_as 판단)

    def place(key: str, c: Mapping[str, str], row: List[Any], target: str = "") -> None:
        if key in aliases:
            if aliases[key] != target:
                warnings.append(f"extra[{c['db']}:{c['name']}] Danbooru 별칭({key} → {aliases[key]})을 가립니다"
                                f" — 출력에서 '{aliases[key]}' 로 바뀌지 않습니다. 분류도 같게 하려면 same_as={aliases[key]}")
            del aliases[key]
        rows[key] = row
        added.add(key)
        dbs.setdefault(c["db"], []).append(key)

    def take_extra(group: Sequence[Mapping[str, str]]) -> None:
        for c in group:
            key = core.normalize_key(c.get("name", ""), kaomoji)
            if not key or key in added or key in pending:
                continue                     # 빈 이름·확장 DB 끼리 중복은 extra_problems 가 오류로 알림
            if key in site_set:
                late_overlap.append(c["name"])
                continue
            if key in rows:
                warnings.append(f"extra[{c['db']}:{c['name']}] Danbooru 태그와 겹쳐 건너뜁니다(overlay 로 분류하세요)")
                continue
            if c.get("same_as"):
                pending[key] = c
                continue
            place(key, c, _row(
                key, core.TYPE_CUSTOM, 0, c.get("primary") or "etc", c.get("rating", ""),
                split_list(c.get("secondary")), split_list(c.get("regions")),
                1 if c.get("identity") == "1" else 0, c.get("status", ""), display=c["name"],
            ))

    def take_site(s: Site) -> None:
        site_tags = [t for t in s.tags if t.type in E621_TYPES]
        own = {danbooru_key(t.name, t.type) for t in site_tags}
        for t in site_tags:
            key = danbooru_key(t.name, t.type)
            if key in site_set:
                warnings.append(f"{s.id} 정규화 후 키가 겹쳐 뒤쪽을 건너뜁니다: {t.name} → {key}")
                continue
            if not key or key in rows or key in pending:
                continue                     # Danbooru 이름(분류는 tags.csv)·앞 층 확장 DB(모델·내 태그)가 이긴다
            ptype, auto = E621_TYPES[t.type]
            ov = s.overlay.get(t.name)
            if not (ov and (ov.get("primary") or ov.get("same_as"))):
                # Danbooru 에서 사라진 공유 이름(missing_since)의 분류는 tags.csv 에 남아 있다
                ov = overlay.get(t.name) if (overlay.get(t.name) or {}).get("primary") else None
            site_set.add(key)
            site_keys.append(key)
            aliases.pop(key, None)           # e621 이름이 원본·앞 층 별칭을 가린다(출력 표기 유지)
            target = danbooru_aliases.get(key)
            if ov and ov.get("same_as"):
                site_pending[key] = (t, ptype, ov["same_as"], False)
            elif ov:
                rows[key] = _row(key, ptype, t.count, ov["primary"], ov.get("rating", ""),
                                 split_list(ov.get("secondary")), split_list(ov.get("regions")),
                                 1 if ov.get("identity") == "1" else 0, ov.get("status", ""))
            elif (t.type in AUTO_SAME_AS_TYPES and target in rows and rows[target][1] == 0
                  and rows[target][3].partition("/")[0] != "meta" and target not in own):
                # Danbooru 별칭이면 그 대상의 분류. 대상이 e621 에도 따로 있으면(male 과 male focus) 뜻이 다를 수 있고,
                # meta 대상(headwear → headwear request)은 요청·관리 태그라 하지 않는다
                site_pending[key] = (t, ptype, target, True)
            elif auto:
                rows[key] = _row(key, ptype, t.count, auto, identity=1 if t.type == 5 else 0, status="auto")
            else:
                rows[key] = _row(key, ptype, t.count, "etc")

    if site is None:      # 두 번째 원본이 없으면 읽은 순서 그대로(팩 tags 순서·별칭 우선이 예전과 같게)
        front, late = list(extra), []
    else:
        front = [c for c in extra if c.get("db") not in LATE_DBS]
        late = [c for c in extra if c.get("db") in LATE_DBS]
    take_extra(front)
    if site is not None:
        take_site(site)
    take_extra(late)
    if late_overlap:
        warnings.append(f"확장 DB({'·'.join(LATE_DBS)}) 행 {len(late_overlap)}개가 {site.id if site else ''} 태그와 겹쳐 "
                        f"건너뜁니다 — `python tagorg.py e621 take-deprecated` 로 옮기세요: "
                        + ", ".join(late_overlap[:8]) + (" …" if len(late_overlap) > 8 else ""))

    for key, c in pending.items():
        tkey = core.normalize_key(c["same_as"], kaomoji)
        if tkey in pending or tkey in site_pending:
            errors.append(f"extra[{c['db']}:{c['name']}] same_as 대상 '{c['same_as']}' 도 same_as 행입니다"
                          " — 원래 태그를 바로 가리킵니다")
            continue
        target = tkey if tkey in rows else aliases.get(tkey)
        if target is None:
            errors.append(f"extra[{c['db']}:{c['name']}] same_as 대상이 원본·확장 DB 에 없습니다: {c['same_as']}")
            continue
        vals = list(rows[target]) + list(_ROW_DEFAULTS[len(rows[target]):])
        place(key, c, _row(key, core.TYPE_CUSTOM, 0, vals[3], vals[4], vals[5], vals[6], vals[7], vals[8],
                           display=c["name"]), target)
    for key, (t, ptype, same_as, auto_same) in site_pending.items():
        tkey = same_as if auto_same else core.normalize_key(same_as, kaomoji)
        if tkey in pending or tkey in site_pending:
            errors.append(f"{site.id}[{t.name}] same_as 대상 '{same_as}' 도 same_as 행입니다 — 원래 태그를 바로 가리킵니다")
            continue
        target = tkey if tkey in rows else aliases.get(tkey)
        if target is None:
            errors.append(f"{site.id}[{t.name}] same_as 대상이 원본·확장 DB 에 없습니다: {same_as}")
            continue
        vals = list(rows[target]) + list(_ROW_DEFAULTS[len(rows[target]):])
        rows[key] = _row(key, ptype, t.count, vals[3], vals[4], vals[5], vals[6], vals[7],
                         "auto" if auto_same else vals[8])

    def extra_aliases(group: Sequence[Mapping[str, str]]) -> None:
        for c in group:
            target = core.normalize_key(c.get("name", ""), kaomoji)
            if target not in added:
                continue
            for alias in split_list(c.get("aliases")):
                akey = core.normalize_key(alias, kaomoji)
                if akey and akey not in rows:
                    aliases.setdefault(akey, target)

    extra_aliases(front)
    if site is not None:
        for t in site.tags:
            target = danbooru_key(t.name, t.type)
            if t.type not in E621_TYPES or target not in rows:
                continue
            for alias in t.aliases:
                if alias.startswith("/"):
                    continue
                akey = core.normalize_key(alias, kaomoji)
                if akey and akey not in rows:
                    aliases.setdefault(akey, target)    # 앞 층(Danbooru·확장 DB) 별칭이 이긴다
    extra_aliases(late)
    # use_instead 는 참고용이라 팩에 없는 태그(사용량이 적어 원본에서 빠진 캐릭터 태그 등)도 맞는 값이다 — 오타를
    # 찾을 수 있게 알리되 한 줄로 묶는다
    missing = []
    for c in extra:
        if core.normalize_key(c.get("name", ""), kaomoji) not in added:
            continue
        for alt in split_list(c.get("use_instead")):
            akey = core.normalize_key(alt, kaomoji)
            if akey not in rows and akey not in aliases:
                missing.append(f"{c['db']}:{c['name']} → {alt}")
    if missing:
        warnings.append(f"extra use_instead 태그 {len(missing)}개가 원본·확장 DB 에 없습니다(참고용 — 오타만 고치면 됨): "
                        + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else ""))
    # DB 순서는 읽은 순서(EXTRA_ORDER) 그대로 — LATE_DBS 를 뒤에 합쳐도 팩의 extra 순서는 바뀌지 않게
    order = dict.fromkeys(c.get("db") for c in extra)
    return {db: dbs[db] for db in order if db in dbs}, site_keys


def _check_rule_tags(where: str, tags, rows: Mapping, aliases: Mapping, warnings: List[str]) -> None:
    for tag in tags:
        if tag in rows:
            continue
        if tag in aliases:
            warnings.append(f"{where} '{tag}' 는 별칭입니다. 대표 이름 '{aliases[tag]}' 를 쓰세요")
        else:
            warnings.append(f"{where} '{tag}' 가 원본·확장 DB 에 없습니다")


def write_pack(pack: Mapping[str, Any], path: Path) -> int:
    """gzip(mtime=0)으로 저장하고 바이트 수를 돌려준다. 같은 팩이면 같은 파일이 나온다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as gz:
            gz.write(raw)
    return path.stat().st_size
