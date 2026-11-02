"""분류 DB 파일 입출력 — 원본 CSV(Danbooru·e621), overlay, 확장 DB(data/extra), 설정 JSON, 원본 목록(sources.json)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE_DIR = DATA / "source"
MANIFEST = SOURCE_DIR / "sources.json"
OVERLAY = DATA / "overlay" / "tags.csv"
# 두 번째 원본(docs/e621.md). sources.json 의 이 키에 Danbooru 와 같은 모양({current, files})으로 적는다
E621 = "e621"
E621_OVERLAY = DATA / "overlay" / "e621.csv"     # e621 에만 있는 태그의 분류(Danbooru 와 같은 이름은 tags.csv)
EXTRA_DIR = DATA / "extra"        # 확장 DB: 원본 CSV 에 없는 태그. 파일 하나 = DB 하나, 파일 이름 = DB ID
SHARED_ARRANGEMENTS = DATA / "arrangements"   # 공유 배열 프리셋(관리 툴이 쓰고 git 으로 배포)
REPORTS = ROOT / "reports"
BUILD = ROOT / "build"
PACK_PATH = BUILD / "tagpack.json.gz"

OVERLAY_FIELDS = (
    "name", "source", "primary", "secondary", "rating", "regions",
    "identity", "status", "by", "confidence", "note", "missing_since",
)
# e621.csv 칸 — overlay 칸에 same_as(표기만 다른 태그: 대상의 분류를 그대로 씀)를 더한다. source 는 늘 e621
SITE_FIELDS = OVERLAY_FIELDS + ("same_as",)
# accepted = LLM 분류를 사용자 위임으로 확정(사람이 하나씩 보지는 않음). reviewed = 사람이 확인·수정.
STATUSES = ("auto", "seed", "llm", "accepted", "conflict", "reviewed")
LIST_SEP = "|"

# 확장 DB 칸. 분류 칸은 overlay 와 같고, 뒤의 다섯 칸이 확장 DB 전용이다(data/extra/README.md).
EXTRA_FIELDS = (
    "name", "primary", "secondary", "rating", "regions", "identity", "status", "by", "confidence", "note",
    "aliases", "models", "same_as", "use_instead", "ref",
)
EXTRA_ORDER = ("model", "deprecated", "user")    # 읽는 순서(관리 툴 표시 순서). 그 밖의 DB 는 이름순으로 뒤에
EXTRA_ID = re.compile(r"^[a-z][a-z0-9_]*$")       # DB ID(= 파일 이름)
EXTRA_OVERFLOW = "_overflow"                     # 값이 머리줄보다 많은 행의 남는 값(검사가 알림)
EXTRA_DUPLICATE = "#dup"                         # 머리줄에 두 번 나온 칸의 뒤쪽 이름 끝(검사가 알림)


@dataclass(frozen=True)
class SourceTag:
    name: str
    type: int
    count: int
    aliases: Tuple[str, ...]


def read_source_csv(path: Path) -> List[SourceTag]:
    """danbooru.csv (name,type,post_count,"alias1,alias2") — 헤더 없음, post_count 내림차순."""
    tags: List[SourceTag] = []
    with open(path, encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.reader(f), 1):
            if not row or not row[0]:
                continue
            if len(row) < 3:
                raise ValueError(f"{path.name}:{i} 열이 부족합니다: {row}")
            aliases = tuple(a for a in (row[3].split(",") if len(row) > 3 else ()) if a)
            tags.append(SourceTag(row[0], int(row[1]), int(row[2]), aliases))
    tags.sort(key=lambda t: -t.count)
    return tags


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_manifest() -> Dict:
    if not MANIFEST.exists():
        return {"current": None, "files": []}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def write_manifest(manifest: Mapping) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def manifest_part(manifest: Mapping, site: str = "") -> Mapping:
    """원본 하나의 {current, files}. Danbooru(site 빈 값)는 sources.json 최상위, 두 번째 원본은 그 키."""
    return manifest if not site else (manifest.get(site) or {})


def manifest_entry(part: Mapping, name: str) -> Dict:
    return next((e for e in part.get("files", ()) if e["file"] == name), {"file": name})


def current_source() -> Tuple[Path, Dict]:
    """sources.json 의 current 항목과 그 CSV 경로. 파일이 없으면 받을 URL과 함께 알려준다."""
    manifest = read_manifest()
    name = manifest.get("current")
    if not name:
        raise FileNotFoundError("원본 CSV가 아직 없습니다. 먼저 `python tagorg.py import <csv> --date YYYY-MM-DD` 를 실행하세요.")
    entry = manifest_entry(manifest, name)
    path = SOURCE_DIR / name
    if not path.exists():
        raise SourceMissing(path, entry)
    return path, entry


def site_source(site: str = E621) -> Optional[Tuple[Path, Dict]]:
    """두 번째 원본(sources.json 의 site 키)의 current 항목과 CSV 경로. 그 원본을 아직 쓰지 않으면(키가 없으면) None,
    파일이 없으면 SourceMissing(current_source 와 같음)."""
    part = manifest_part(read_manifest(), site)
    name = part.get("current")
    if not name:
        return None
    entry = manifest_entry(part, name)
    path = SOURCE_DIR / name
    if not path.exists():
        raise SourceMissing(path, entry)
    return path, entry


@dataclass
class Site:
    """두 번째 원본(e621)의 빌드 재료 — 원본 태그, sources.json 항목(file·date·rows), 분류 파일(e621.csv) 행."""
    id: str
    tags: List[SourceTag]
    meta: Dict[str, Any]
    overlay: Dict[str, Dict[str, str]]


def load_site(site: str = E621, overlay_path: Path = E621_OVERLAY) -> Optional[Site]:
    """두 번째 원본을 읽는다. sources.json 에 없으면 None(빌드는 Danbooru·확장 DB 만으로)."""
    found = site_source(site)
    if found is None:
        return None
    path, entry = found
    tags = read_source_csv(path)
    meta = {"file": entry.get("file", path.name), "date": entry.get("date", ""), "rows": len(tags)}
    return Site(site, tags, meta, read_overlay(overlay_path, SITE_FIELDS))


class SourceMissing(FileNotFoundError):
    """sources.json 의 현재 원본 CSV 가 없음(git 에 넣지 않으므로 다른 PC 에서 처음 받으면 이렇다)."""

    def __init__(self, path: Path, entry: Mapping) -> None:
        self.path, self.entry = path, dict(entry)
        self.fetchable = bool(entry.get("url") and entry.get("sha256"))
        how = ("`python tagorg.py fetch` 로 받을 수 있습니다(sha256 확인)." if self.fetchable else
               f"다음 주소에서 받아 그 경로에 두세요: {entry.get('url') or '(sources.json 에 URL 없음)'}")
        super().__init__(f"원본 CSV가 없습니다: {path}\n{how}")


def download_url(url: str) -> str:
    """GitHub 파일 페이지(…/blob/…) 주소는 파일 자체(…/raw/…)로 바꾼다. 다른 주소는 그대로."""
    return re.sub(r"^(https://github\.com/[^/]+/[^/]+)/blob/", r"\1/raw/", url)


def fetch_source(entry: Mapping, dest: Path, opener=None, timeout: float = 60) -> Path:
    """sources.json 항목의 원본 CSV 를 받아 sha256 이 같을 때만 dest 에 둔다(다르면 아무것도 쓰지 않음)."""
    opener = opener or urllib.request.urlopen
    url, expected = entry.get("url"), (entry.get("sha256") or "").lower()
    if not url or not expected:
        raise ValueError(f"sources.json 에 {entry.get('file', dest.name)} 의 url·sha256 이 없어 받을 수 없습니다.")
    request = urllib.request.Request(download_url(url), headers={"User-Agent": "TagOrganizer"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    tmp = Path(tmp_name)
    h = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f, opener(request, timeout=timeout) as r:
            for block in iter(lambda: r.read(1 << 20), b""):
                h.update(block)
                f.write(block)
        if h.hexdigest() != expected:
            raise ValueError(f"받은 파일의 sha256 이 sources.json 과 다릅니다({h.hexdigest()[:12]}… ≠ {expected[:12]}…). "
                             "쓰지 않았습니다.")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dest


class OverlayError(ValueError):
    """overlay 파일이 손상됨(인코딩·열·중복 행). 메시지에 복구 방법을 담는다."""


def restore_hint(rel: str = "data/overlay/tags.csv") -> str:
    return (f"엑셀로 저장하다 깨졌을 수 있습니다. `git diff {rel}` 로 확인하고, "
            f"되돌리려면 `git checkout -- {rel}`. 손 수정은 `python tagorg.py fix` 를 권장합니다.")


RESTORE_HINT = restore_hint()


def read_overlay(path: Path = OVERLAY, fields: Sequence[str] = OVERLAY_FIELDS) -> Dict[str, Dict[str, str]]:
    """overlay 형식 파일(tags.csv, e621.csv 는 fields=SITE_FIELDS)을 이름 → 행으로."""
    if not path.exists():
        return {}
    hint = restore_hint(f"data/overlay/{path.name}")
    rows: Dict[str, Dict[str, str]] = {}
    dupes: List[str] = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = set(fields) - set(reader.fieldnames or ())
            if missing:
                raise OverlayError(f"{path.name} 에 열이 없습니다: {', '.join(sorted(missing))}. {hint}")
            for row in reader:
                clean = {k: (row.get(k) or "").strip() for k in fields}
                if clean["name"] in rows:
                    dupes.append(clean["name"])
                rows[clean["name"]] = clean
    except UnicodeDecodeError as e:
        raise OverlayError(f"{path.name} 이 UTF-8 이 아닙니다(바이트 {e.start}). 엑셀에서는 'CSV UTF-8' 형식으로 "
                           f"저장해야 합니다. {hint}") from None
    if dupes:
        shown = ", ".join(sorted(set(dupes))[:10])
        raise OverlayError(f"{path.name} 에 같은 이름의 행이 겹칩니다({len(dupes)}행): {shown}. {hint}")
    return rows


class OverlayChanged(RuntimeError):
    """바꿔 넣기 직전의 파일(overlay·확장 DB)이 기대한 내용(해시)과 달랐다 — 그사이 다른 프로그램이 저장했다.
    쓰지 않았다."""


_ANY = object()


def write_overlay(rows: Mapping[str, Mapping[str, str]], path: Path = OVERLAY, expected=_ANY,
                  fields: Sequence[str] = OVERLAY_FIELDS) -> str:
    """name 순으로 정렬해 저장한다(git diff 가 깔끔하도록). 엑셀용 UTF-8 BOM. 쓴 내용의 sha256 을 돌려준다.

    저장마다 이름이 다른 임시 파일에 다 쓴 뒤 바꿔 넣는다. 쓰는 도중 멈춰도(GUI 는 고칠 때마다 저장) 정본이
    반쯤 쓰인 채 남지 않고, 두 프로그램이 동시에 저장해도 서로의 임시 파일을 덮어쓰지 않는다.
    expected(sha256, 파일이 없었으면 None)를 주면 바꿔 넣기 직전에 지금 파일과 비교해 다르면 OverlayChanged.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    for name in sorted(rows):
        writer.writerow({k: rows[name].get(k, "") for k in fields} | {"name": name})
    return _replace_file(path, buf.getvalue().encode("utf-8-sig"), expected)


def _replace_file(path: Path, data: bytes, expected=_ANY) -> str:
    """임시 파일에 다 쓴 뒤 바꿔 넣는다(write_overlay·write_extra 공용). 쓴 내용의 sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if expected is not _ANY:
            current = sha256_file(path) if path.exists() else None
            if current != expected:
                raise OverlayChanged(f"{path.name} 이 그사이 다른 프로그램에서 저장됐습니다. 쓰지 않았습니다.")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows: 백신·색인기가 잠깐 잡고 있으면 실패한다. 엑셀이 열고 있으면 끝까지 실패한다
                if attempt == 4:
                    raise
                time.sleep(0.1)
    finally:
        if tmp.exists():
            tmp.unlink()
    return hashlib.sha256(data).hexdigest()


def extra_files(folder: Path = EXTRA_DIR) -> List[Path]:
    """확장 DB 파일을 EXTRA_ORDER 순서로(그 밖의 DB 는 이름순으로 뒤에)."""
    if not folder.is_dir():
        return []
    rank = {db: i for i, db in enumerate(EXTRA_ORDER)}
    return sorted((p for p in folder.glob("*.csv") if p.is_file()), key=lambda p: (rank.get(p.stem, len(rank)), p.stem))


def read_extra(folder: Path = EXTRA_DIR) -> List[Dict[str, str]]:
    """확장 DB 행 전부(DB 순서 → 파일 안 순서). 행마다 db(파일 이름)를 붙인다.

    칸 검사는 빌드가 한다(builder.extra_problems). 값이 머리줄보다 많은 행은 EXTRA_OVERFLOW 칸에, 머리줄에 두 번
    나온 칸의 뒤쪽은 이름 끝에 EXTRA_DUPLICATE 를 붙여 남겨 검사가 알리게 한다. 모든 칸이 빈 행(엑셀이 끝에
    남기는 ",,,,")은 건너뛴다.
    """
    rows: List[Dict[str, str]] = []
    for path in extra_files(folder):
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            names: List[str] = []
            for col in (c.strip() for c in next(reader, [])):
                names.append(col + EXTRA_DUPLICATE if col in names else col)
            for values in reader:
                row = {name: (values[i].strip() if i < len(values) else "") for i, name in enumerate(names)}
                rest = [v.strip() for v in values[len(names):] if v.strip()]
                if rest:
                    row[EXTRA_OVERFLOW] = LIST_SEP.join(rest)
                if not any(row.values()):
                    continue
                row["db"] = path.stem
                rows.append(row)
    return rows


def extra_path(db: str, folder: Path = EXTRA_DIR) -> Path:
    return folder / f"{db}.csv"


def write_extra(db: str, rows: Sequence[Mapping[str, str]], folder: Path = EXTRA_DIR, expected=_ANY) -> str:
    """확장 DB 하나를 주어진 행 순서 그대로 저장하고 쓴 내용의 sha256 을 돌려준다(엑셀용 UTF-8 BOM, 임시 파일에
    다 쓴 뒤 바꿔 넣기 — write_overlay 와 같고, expected 를 주면 바꿔 넣기 직전에 비교해 다르면 OverlayChanged).

    모르는 칸(넘친 값·중복 머리줄 포함)이 있는 행은 저장하면 그 값을 잃으므로 쓰지 않고 ValueError.
    """
    if not EXTRA_ID.match(db):
        raise ValueError(f"DB ID 가 잘못됐습니다: {db}")
    for row in rows:
        unknown = sorted(k for k in row if k not in EXTRA_FIELDS and k != "db")
        if unknown:
            raise ValueError(f"{db}.csv 의 {row.get('name')} 행에 모르는 칸이 있어 저장하지 않습니다: {', '.join(unknown)}"
                             " — 파일을 먼저 고치세요(python tagorg.py check)")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXTRA_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in EXTRA_FIELDS})
    return _replace_file(extra_path(db, folder), buf.getvalue().encode("utf-8-sig"), expected)


def load_json(name: str) -> Dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def load_config() -> Dict[str, Dict]:
    return {
        "taxonomy": load_json("taxonomy.json"),
        "rules": load_json("rules.json"),
        "layouts": load_json("layouts.json")["layouts"],
        "presets": load_json("presets.json")["presets"],
        "guess": {k: v for k, v in load_json("guess.json").items() if k in ("head_categories", "species", "patterns")},
    }


def split_list(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(LIST_SEP) if v.strip()]
