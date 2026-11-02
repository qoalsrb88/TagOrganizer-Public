"""원본 CSV 가져오기와 델타 반영.

새 원본이 들어오면 이전 원본과 이름으로 대조해 overlay 에 반영한다(두 번째 원본 e621 은 e621.csv 에).
- type 이 바뀐 태그: 분류가 있으면 status=conflict (검수 대상)
- 사라진 태그: 행을 지우지 않고 missing_since 만 기록
- 사라진 이름이 새 원본에서 다른 태그의 별칭이 됐으면(이름 변경): 분류를 새 이름으로 복사(conflict)
- 다시 나타난 태그: missing_since 를 지운다
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Collection, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core

from .tagdb import (
    E621, E621_OVERLAY, OVERLAY, OVERLAY_FIELDS, REPORTS, SITE_FIELDS, SOURCE_DIR, SourceMissing, SourceTag,
    current_source, manifest_part, read_manifest, read_overlay, read_source_csv, sha256_file, write_manifest,
    write_overlay,
)

_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Delta:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    type_changed: List[Tuple[str, int, int]] = field(default_factory=list)
    renamed: List[Tuple[str, str]] = field(default_factory=list)   # (옛 이름, 새 이름)


def compute_delta(old: Sequence[SourceTag], new: Sequence[SourceTag]) -> Delta:
    old_by = {t.name: t for t in old}
    new_by = {t.name: t for t in new}
    delta = Delta()
    delta.added = [t.name for t in new if t.name not in old_by]
    delta.removed = [t.name for t in old if t.name not in new_by]
    delta.type_changed = [
        (t.name, old_by[t.name].type, t.type) for t in new if t.name in old_by and old_by[t.name].type != t.type
    ]
    delta.renamed = _renames(delta.removed, new)
    return delta


def _renames(removed: Sequence[str], new: Sequence[SourceTag]) -> List[Tuple[str, str]]:
    alias_to: Dict[str, str] = {}
    for t in new:                      # post_count 내림차순이므로 먼저 나온 쪽이 이긴다
        for a in t.aliases:
            alias_to.setdefault(a, t.name)
    return [(name, alias_to[name]) for name in removed if name in alias_to]


def overlay_delta(overlay: Mapping[str, Mapping[str, str]], new: Sequence[SourceTag]) -> Delta:
    """이전 원본 파일이 없을 때: overlay 와 새 원본만으로 사라진·다시 나타난·이름이 바뀐 행을 찾는다.

    type 변경은 이전 원본이 있어야 알 수 있어 비워 둔다. 사라진 행에 missing_since 를 적어 두지 않으면
    빌드가 그 행을 "이름이 깨진 행"으로 보고 멈춘다.
    """
    new_names = {t.name for t in new}
    delta = Delta()
    delta.removed = sorted(n for n in overlay if n not in new_names)
    delta.added = sorted(n for n in overlay if n in new_names and overlay[n].get("missing_since"))
    delta.renamed = _renames(delta.removed, new)
    return delta


def apply_delta(overlay: MutableMapping[str, Dict[str, str]], delta: Delta, version: str,
                blocked: Collection[str] = ()) -> List[str]:
    """overlay 를 고치고 바뀐 내용을 한 줄씩 돌려준다. blocked 이름으로는 이름 변경 분류를 옮기지 않는다."""
    changes: List[str] = []
    for name, old_t, new_t in delta.type_changed:
        row = overlay.get(name)
        if row and row.get("primary"):
            row["status"] = "conflict"
            row["note"] = _append_note(row.get("note", ""), f"type {old_t}->{new_t} ({version})")
            changes.append(f"type 변경 → conflict: {name} ({old_t}->{new_t})")
    renamed_from = dict(delta.renamed)
    for name in delta.removed:
        row = overlay.get(name)
        if row is None:
            continue
        if not row.get("missing_since"):
            row["missing_since"] = version
            changes.append(f"원본에서 사라짐: {name}")
        new_name = renamed_from.get(name)
        if new_name and new_name in blocked and (row.get("primary") or row.get("same_as")):
            changes.append(f"이름 변경 대상이 Danbooru 태그라 분류를 옮기지 않음(tags.csv 에서 분류): {name} → {new_name}")
        elif new_name and row.get("same_as") and core.normalize_key(row["same_as"]) == core.normalize_key(new_name):
            # a.same_as=b 인 a 가 b 로 합쳐짐 — 복사하면 b 가 자기 자신을 가리킨다(b 는 원래 분류 그대로)
            changes.append(f"이름 변경 대상이 same_as 대상 자기 자신이라 옮기지 않음: {name} → {new_name}")
        elif new_name and new_name not in overlay and (row.get("primary") or row.get("same_as")):
            copied = dict(row)
            copied.update(name=new_name, status="conflict", missing_since="",
                          note=_append_note(row.get("note", ""), f"renamed from {name} ({version})"))
            overlay[new_name] = copied
            changes.append(f"이름 변경으로 분류 복사 → conflict: {name} → {new_name}")
    for name in delta.added:
        row = overlay.get(name)
        if row and row.get("missing_since"):
            row["missing_since"] = ""
            changes.append(f"다시 나타남: {name}")
    return changes


def _append_note(note: str, text: str) -> str:
    return f"{note}; {text}" if note else text


def import_source(
    csv_path: Path,
    date: Optional[str] = None,
    name: Optional[str] = None,
    url: str = "",
    move: bool = False,
    dry_run: bool = False,
    site: str = "",
) -> str:
    """원본 CSV를 data/source 로 가져오고 overlay 에 델타를 반영한다. 보고서 문자열을 돌려준다.

    site 를 주면(e621) 두 번째 원본으로 가져온다 — sources.json 의 그 키에 기록하고, 델타는 그 분류 파일(e621.csv)에
    반영한다. 이름 변경으로 분류를 옮길 새 이름이 Danbooru 태그면 옮기지 않는다(같은 이름은 tags.csv 한 행).
    dry_run 이면 같은 보고서만 만들고 아무것도 쓰지 않는다(overlay·원본 복사·sources.json·reports).
    읽은 뒤 쓰기 전에 overlay 가 다른 곳(관리 툴 등)에서 저장됐으면 tagdb.OverlayChanged 로 멈춘다(그때까지 쓴 것 없음).
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if date is None:
        m = _DATE_IN_NAME.search(name or csv_path.name)
        if not m:
            raise ValueError("파일 이름에 날짜가 없습니다. --date YYYY-MM-DD 로 지정하세요.")
        date = m.group(1)
    dest_name = name or (csv_path.name if _DATE_IN_NAME.search(csv_path.name) else f"{site or 'danbooru'}_{date}.csv")
    dest = SOURCE_DIR / dest_name

    new_tags = read_source_csv(csv_path)
    digest = sha256_file(csv_path)
    manifest = read_manifest()
    prev_name = manifest_part(manifest, site).get("current")
    if prev_name == dest_name:
        raise ValueError(f"이미 현재 원본입니다: {dest_name}")
    for other in ("", E621):          # 다른 원본이 쓰는 파일 이름이면 그 파일을 덮어쓴다
        part = manifest_part(manifest, other)
        if other != site and dest_name in {part.get("current")} | {e.get("file") for e in part.get("files", ())}:
            raise ValueError(f"{other or 'danbooru'} 원본이 쓰는 파일 이름입니다: {dest_name} — --name 으로 다른 이름을 주세요")

    title = f"# {site} 원본 가져오기 {date}" if site else f"# 원본 가져오기 {date}"
    lines = [title, "", f"- 파일: `{dest_name}` ({len(new_tags):,}행, sha256 `{digest[:12]}…`)"]
    overlay_path, fields = (E621_OVERLAY, SITE_FIELDS) if site else (OVERLAY, OVERLAY_FIELDS)
    expected = sha256_file(overlay_path) if overlay_path.exists() else None
    overlay = read_overlay(overlay_path, fields) if site else read_overlay()
    blocked = _danbooru_names() if site else frozenset()
    changed = False
    if prev_name and (SOURCE_DIR / prev_name).exists():
        old_tags = read_source_csv(SOURCE_DIR / prev_name)
        delta = compute_delta(old_tags, new_tags)
        changes = apply_delta(overlay, delta, date, blocked)
        lines += [
            f"- 이전 원본: `{prev_name}` ({len(old_tags):,}행)",
            f"- 추가 {len(delta.added):,} · 삭제 {len(delta.removed):,} · type 변경 {len(delta.type_changed):,}"
            f" · 이름 변경 {len(delta.renamed):,}",
            "", "## overlay 변경", "",
        ]
        lines += [f"- {c}" for c in changes] or ["- 없음"]
        added = set(delta.added)
        new_general = [t for t in new_tags if t.type == 0][:5000]
        fresh = [t for t in new_general if t.name in added and t.name not in overlay]
        if fresh:
            lines += ["", "## general 상위 5,000 안에 새로 들어온 미분류 태그", ""]
            lines += [f"- {t.name} ({t.count:,})" for t in fresh[:200]]
        changed = True
    elif overlay:
        # 이전 원본 파일이 없으면 overlay 와 새 원본만 대조한다(사라진 행 표시는 빌드 검사에 필요)
        delta = overlay_delta(overlay, new_tags)
        changes = apply_delta(overlay, delta, date, blocked)
        why = f"이전 원본 `{prev_name}` 파일이 없어" if prev_name else "이전 원본 기록이 없어"
        lines += [
            f"- {why} overlay 와 새 원본만 대조함(type 변경은 확인하지 못함)",
            f"- 사라진 행 {len(delta.removed):,} · 다시 나타난 행 {len(delta.added):,} · 이름 변경 {len(delta.renamed):,}",
            "", "## overlay 변경", "",
        ]
        lines += [f"- {c}" for c in changes] or ["- 없음"]
        changed = True
    else:
        lines.append("- 첫 원본(델타 없음)")

    report = "\n".join(lines) + "\n"
    if dry_run:
        return report
    if changed:
        write_overlay(overlay, overlay_path, expected=expected, fields=fields)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    if csv_path.resolve() != dest.resolve():
        (shutil.move if move else shutil.copy2)(str(csv_path), str(dest))

    files = [e for e in manifest_part(manifest, site).get("files", ()) if e.get("file") != dest_name]
    files.append({
        "file": dest_name, "date": date, "rows": len(new_tags), "sha256": digest,
        "url": url, "imported": _date.today().isoformat(),
    })
    part = {"current": dest_name, "files": sorted(files, key=lambda e: e["date"])}
    # 다른 원본의 기록(최상위 Danbooru·e621 키)은 그대로 둔다
    write_manifest({**manifest, site: part} if site else {**manifest, **part})

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / (f"import-{site}-{date}.md" if site else f"import-{date}.md")).write_text(report, encoding="utf-8")
    return report


def _danbooru_names() -> frozenset:
    """지금 Danbooru 원본의 이름(두 번째 원본의 이름 변경이 분류를 옮기지 않을 이름). 원본을 아직 등록하지 않았으면
    빈 집합. 등록했는데 파일이 없으면 SourceMissing 그대로 — 빈 목록으로 넘어가면 Danbooru 이름에 분류를 옮긴다."""
    try:
        path, _ = current_source()
    except SourceMissing:
        raise
    except FileNotFoundError:
        return frozenset()
    return frozenset(t.name for t in read_source_csv(path))
