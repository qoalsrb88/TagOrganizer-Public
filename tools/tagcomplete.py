"""tagcomplete 확장 파일 내보내기 — 확장 DB(data/extra) → 자동완성(comfy-ex-tagcomplete)의 extra CSV.

손으로 관리하던 `extra-quality-tags.csv` 를 확장 DB 에서 만든다. 한 줄 = 태그 하나: `이름,종류,메모,별칭`.
- 이름: Danbooru 처럼 공백 대신 밑줄(자동완성이 넣을 때 밑줄을 공백으로 바꾼다). 이모티콘은 그대로.
- 종류: Danbooru 번호(0 일반·1 작가·3 작품·4 캐릭터·5 메타) — 자동완성의 색. 분류에서 정한다.
- 메모: 3번째 칸이 숫자가 아니면 사용량 대신 메모로 보인다 — 분류 이름과 DB(예: `이펙트/조명 · 내 태그`).
- 별칭: 확장 DB 의 aliases 를 쉼표로.
분류는 빌드한 팩에서 읽는다(same_as 행은 대상의 분류). UTF-8(BOM 없음 — 자동완성이 utf-8 로 읽어 BOM 이
첫 이름에 붙는다).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core

from .tagdb import Site, split_list

DB_LABELS = {"model": "모델 태그", "deprecated": "폐기 태그", "community": "커뮤니티 태그", "user": "내 태그", "e621": "e621 태그"}
CATEGORY = {"style/artist": 1, "series": 3, "subject/character": 4}     # 그 밖에 meta 는 5, 나머지 0


def category(primary: str) -> int:
    if primary in CATEGORY:
        return CATEGORY[primary]
    if primary.partition("/")[0] in CATEGORY:
        return CATEGORY[primary.partition("/")[0]]
    return 5 if primary.startswith("meta") else 0


def export_name(display: str) -> str:
    """자동완성 표기: 공백 → 밑줄. 이모티콘(밑줄이 글자)은 그대로."""
    return display if core.is_kaomoji_name(display) else display.strip().replace(" ", "_")


def rows_for(pack: Mapping[str, Any], extra: Sequence[Mapping[str, str]], dbs: Optional[Sequence[str]] = None,
             include_etc: bool = False, include_auto: bool = False,
             site: Optional[Site] = None) -> List[Tuple[str, int, str, str]]:
    """(이름, 종류, 메모, 별칭) — DB 순서 → 파일 안 순서, 그 뒤에 두 번째 원본(e621)의 분류 파일 행(이름순).
    보류(etc)·미분류는 include_etc, 종류로 자동 분류한 행(폐기된 캐릭터·작가 등 status auto)은 include_auto 일 때만.

    e621 은 분류 파일(e621.csv)에 행이 있는 태그만 넣는다(자동 분류한 12만여 개는 자동완성의 e621 목록 몫). 폐기
    DB 에서 e621.csv 로 옮긴 태그(female·clothing 등)가 예전처럼 자동완성에 남는다. dbs 에 원본 ID(e621)를 주면 고른다.
    """
    p = core.TagPack(pack)
    labels: Dict[str, str] = {}
    for c in pack["taxonomy"]["categories"]:
        labels[c["id"]] = c.get("label", c["id"])
        for s in c.get("subs", ()):
            labels[f"{c['id']}/{s['id']}"] = f"{labels[c['id']]}/{s.get('label', s['id'])}"
    by_key = {key: db for db, keys in (pack.get("extra") or {}).items() for key in keys}
    aliases_of = {}
    for r in extra:
        aliases_of[(r.get("db"), r.get("name"))] = split_list(r.get("aliases"))
    out = []

    def add(info: core.TagInfo, label: str, aliases: Sequence[str] = ()) -> None:
        if info.status == "auto" and not include_auto:
            return
        if info.primary == "etc" and not include_etc:
            return
        note = " · ".join(x for x in (labels.get(info.primary, "보류" if info.primary == "etc" else info.primary),
                                      label) if x)
        out.append((export_name(info.display), category(info.primary), note,
                    ",".join(export_name(a) for a in aliases)))

    for db, keys in (pack.get("extra") or {}).items():
        if dbs and db not in dbs:
            continue
        for key in keys:
            info = p.get(key)
            if info is None or by_key.get(key) != db:
                continue
            add(info, DB_LABELS.get(db, db), aliases_of.get((db, info.display), ()))
    if site is not None and (not dbs or site.id in dbs):
        for name in sorted(site.overlay):
            info = p.get(core.normalize_key(name, p.kaomoji))
            if info is not None and info.site == site.id:
                add(info, DB_LABELS.get(site.id, site.id))
    return out


def render(rows: Sequence[Tuple[str, int, str, str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for name, cat, note, alias in rows:
        w.writerow([name, cat, note, alias])
    return buf.getvalue().encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(out: Path, data: bytes, state_path: Path, force: bool = False) -> Tuple[str, Optional[Path]]:
    """(결과, 백업 경로). 결과: unchanged · created · updated · replaced(--force, 손으로 만든 파일을 백업하고 덮어씀).

    이 도구가 마지막으로 쓴 내용(state 의 sha256)과 지금 파일이 다르면(손으로 관리하던 파일, 밖에서 고친 파일)
    force 없이는 쓰지 않는다(FileExistsError). force 면 `<이름>.bak-<시각>` 으로 남기고 쓴다.
    """
    out = out.resolve()                         # 기억하는 대상은 절대 경로(다른 폴더에서 실행해도 같은 파일)
    state = read_state(state_path)
    backup = None
    if out.exists():
        current = out.read_bytes()
        mine = state.get("out") == str(out) and state.get("sha256") == sha256(current)
        if current == data:
            if mine:
                _save_state(state_path, out, data)
            return "unchanged", None            # 손으로 만든 파일이면 내용이 같아도 내 것으로 기록하지 않는다
        if not mine:
            if not force:
                raise FileExistsError(f"{out} 은 이 도구가 마지막으로 쓴 내용과 다릅니다(손으로 관리하던 파일이거나 밖에서 "
                                      "고침). 백업하고 덮어쓰려면 --force")
            backup = out.with_name(f"{out.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            backup.write_bytes(current)
        result = "updated" if mine else "replaced"
    else:
        result = "created"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out)
    _save_state(state_path, out, data)
    return result, backup


def read_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(path: Path, out: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"out": str(out), "sha256": sha256(data)}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
