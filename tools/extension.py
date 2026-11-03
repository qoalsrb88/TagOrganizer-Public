"""크롬 확장 자료 만들기 — 팩과 프리셋 목록을 extension/data 에 쓰고, 원하면 설치용 zip 도 만든다.

쓰는 파일은 아래 둘뿐이다(extension/data 는 git 제외). 확장 코드(extension/*)는 저장소에 있는 그대로 쓴다.
    build/tagpack.json.gz  → extension/data/tagpack.json.gz
    (생성)                  → extension/data/catalog.json   팩 버전·필터·레이아웃 이름, 배열 프리셋
"""

from __future__ import annotations

import gzip
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bmk_tag_organizer_core as core

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
DATA_REL = "data"
PACK_NAME = "tagpack.json.gz"
CATALOG_NAME = "catalog.json"
ZIP_SKIP_DIRS = frozenset({"__pycache__"})


def _read_arrangements(folder: Path, prefix: str, suffix: str, pack: core.TagPack,
                       warnings: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            core.load_arrangement(data, pack)
        except ValueError as e:
            warnings.append(f"{path.name}: 건너뜀 — {e}")
            continue
        items.append({"id": prefix + path.stem, "label": str(data.get("label") or path.stem) + suffix, "data": data})
    return items


def build_catalog(pack: core.TagPack, shared: Path, local: Optional[Path]) -> Tuple[Dict[str, Any], List[str]]:
    """배열은 공유 프리셋 다음 내 배열 순서다. 지금 분류 체계로 안 열리는 파일은 경고하고 뺀다."""
    warnings: List[str] = []
    arrangements = _read_arrangements(shared, "shared-", "", pack, warnings)
    if local is not None and local.is_dir():
        arrangements += _read_arrangements(local, "local-", " (내 배열)", pack, warnings)
    catalog = {
        "pack_version": pack.version,
        "tags": len(pack),
        "filters": {pid: spec.get("label", pid) for pid, spec in pack.presets.items()},
        "layouts": {lid: spec.get("label", lid) for lid, spec in pack.layouts.items()},
        "arrangements": arrangements,
    }
    return catalog, warnings


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_data(pack_path: Path, extension: Path = EXTENSION, shared: Optional[Path] = None,
               local: Optional[Path] = None) -> List[str]:
    """extension/data 를 채우고 경고를 돌려준다. 팩이 gzip 이 아니면 압축한다."""
    pack = core.load_pack(str(pack_path))
    catalog, warnings = build_catalog(pack, shared if shared is not None else ROOT / "data" / "arrangements", local)
    target = extension / DATA_REL
    target.mkdir(parents=True, exist_ok=True)
    raw = pack_path.read_bytes()
    _atomic_write(target / PACK_NAME, raw if raw[:2] == b"\x1f\x8b" else gzip.compress(raw, 9))
    _atomic_write(target / CATALOG_NAME, json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return warnings


def make_zip(out: Path, extension: Path = EXTENSION) -> int:
    """폴더 그대로 묶는다(압축을 풀어 '압축해제된 확장 프로그램 로드'로 설치). 파일 수를 돌려준다."""
    files = [p for p in sorted(extension.rglob("*")) if p.is_file() and not ZIP_SKIP_DIRS & set(p.parts)]
    if not (extension / DATA_REL / PACK_NAME).is_file():
        raise FileNotFoundError(f"{DATA_REL}/{PACK_NAME} 이 없습니다. 먼저 자료를 만드세요.")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(extension).as_posix())
    os.replace(tmp, out)
    return len(files)
