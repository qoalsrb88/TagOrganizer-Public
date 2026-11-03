"""노드 팩 동기화 — 코어, 런타임 팩, 청크 편집기 모듈을 ComfyUI_BMK_Nodes 폴더에 복사한다.

쓰는 파일은 아래 다섯 개뿐이고, 대상 폴더에서 아무것도 지우지 않는다.
    bmk_tag_organizer_core.py   → <노드 팩>/bmk_tag_organizer_core.py   (줄바꿈 LF 로 통일)
    build/tagpack.json.gz       → <노드 팩>/tag_organizer_data/tagpack.json.gz   (gzip 이 아니면 압축)
    extension/editor/editor.js  → <노드 팩>/js/tag_organizer/editor.js   (줄바꿈 LF 로 통일)
    extension/editor/model.js   → <노드 팩>/js/tag_organizer/model.js    (줄바꿈 LF 로 통일)
    (생성)                       → <노드 팩>/tag_organizer_data/pack_info.json

ComfyUI 는 js/ 아래의 .js 를 전부 확장으로 import 하므로, 두 편집기 모듈은 import 만으로 아무 일도 하지 않아야 한다.

안전장치
- 대상이 ComfyUI_BMK_Nodes 폴더인지 __init__.py 로 확인한다. 쓰는 경로가 링크(junction·symlink)를
  거쳐 대상 밖으로 이어지면 쓰지 않는다.
- 대상 파일이 지난 동기화 뒤 바뀌었으면(pack_info.json 에 적은 해시와 다르면) 멈춘다.
  처음 보는 파일이 이미 있을 때도 멈춘다. --force 로만 덮어쓴다. 코어와 편집기 모듈은 줄바꿈을 LF 로
  맞춘 뒤 비교하므로 git 의 CRLF 변환은 변경으로 보지 않는다.
- 실행마다 고유한 임시 파일에 쓴 뒤 os.replace 로 바꿔치기한다. pack_info.json 은 마지막에 쓴다.
  동시에 두 번 실행하는 경우까지 막는 잠금은 없다(혼자 쓰는 CLI).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import bmk_tag_organizer_core as core

CORE_REL = "bmk_tag_organizer_core.py"
DATA_REL = "tag_organizer_data"
PACK_REL = f"{DATA_REL}/tagpack.json.gz"
INFO_REL = f"{DATA_REL}/pack_info.json"
INFO_FORMAT = "tagorg-pack-info"
PACKAGE_MARKER = "ComfyUI_BMK_Nodes"
EDITOR_SRC = "extension/editor"             # 정본 저장소 안의 위치
EDITOR_REL = "js/tag_organizer"             # 노드 팩 안의 위치
EDITOR_FILES = ("editor.js", "model.js")
TEXT_FILES = frozenset({CORE_REL, *(f"{EDITOR_REL}/{name}" for name in EDITOR_FILES)})   # 줄바꿈을 LF 로 맞춰 쓰고 비교하는 파일
REPO_ROOT = Path(__file__).resolve().parent.parent
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SyncError(Exception):
    pass


class SyncConflict(SyncError):
    def __init__(self, paths: List[str]) -> None:
        super().__init__("지난 동기화 뒤 바뀐 파일이 있어 멈춥니다: " + ", ".join(paths)
                         + " (노드 팩에서 직접 고친 내용이면 정본인 TagOrganizer 에 옮긴 뒤 --force)")
        self.paths = paths


@dataclass
class Action:
    rel: str
    state: str            # create / update / unchanged / conflict
    new_hash: str
    old_hash: str = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(rel: str, data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n") if rel in TEXT_FILES else data


def content_hash(rel: str, data: bytes) -> str:
    return sha256_bytes(normalize(rel, data))


def validate_target(target: Path) -> Path:
    """대상 폴더를 확인하고 실제 경로를 돌려준다."""
    init = target / "__init__.py"
    if not target.is_dir():
        raise SyncError(f"대상 폴더가 없습니다: {target}")
    if not init.is_file() or PACKAGE_MARKER not in init.read_text(encoding="utf-8", errors="replace"):
        raise SyncError(f"ComfyUI_BMK_Nodes 폴더가 아닙니다(__init__.py 확인): {target}")
    return target.resolve()


def dest_path(root: Path, rel: str) -> Path:
    """root(실제 경로) 아래의 rel. 중간에 링크가 있어 다른 곳으로 이어지면 거부한다."""
    expected = root.joinpath(*rel.split("/"))
    real = expected.resolve()
    if os.path.normcase(str(real)) != os.path.normcase(str(expected)):
        raise SyncError(f"대상 밖으로 이어지는 경로(링크)라서 쓰지 않습니다: {rel} → {real}")
    return expected


def read_info(root: Path) -> Dict[str, Any]:
    """지난 동기화 기록. 없거나 형식이 틀리면 빈 dict, files 가 틀리면 files 만 비운다."""
    try:
        info = json.loads(dest_path(root, INFO_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError, SyncError):
        return {}
    if not isinstance(info, dict) or info.get("format") != INFO_FORMAT:
        return {}
    files = info.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and _SHA256.fullmatch(v) for k, v in files.items()
    ):
        info = {**info, "files": {}}
    return info


def load_pack_bytes(pack_path: Path) -> Tuple[bytes, Dict[str, Any]]:
    """배포할 gzip 바이트와 팩 내용. 압축 안 된 JSON 이면 압축하고, 이 코어로 읽히는지 확인한다."""
    if not pack_path.is_file():
        raise SyncError(f"팩이 없습니다: {pack_path} (먼저 build)")
    raw = pack_path.read_bytes()
    try:
        if raw[:2] == b"\x1f\x8b":
            gz, text = raw, gzip.decompress(raw)
        else:
            gz, text = gzip.compress(raw, mtime=0), raw
        pack = json.loads(text.decode("utf-8"))
        core.TagPack(pack)                  # 형식·스키마가 이 코어와 맞는지 확인
    except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SyncError(f"팩을 읽을 수 없습니다: {pack_path} ({e})") from e
    return gz, pack


def load_editor_files(repo: Path) -> Dict[str, bytes]:
    """노드 팩에 복사할 청크 편집기 모듈(상대 경로 → 내용)."""
    files = {}
    for name in EDITOR_FILES:
        path = repo.joinpath(*EDITOR_SRC.split("/"), name)
        if not path.is_file():
            raise SyncError(f"편집기 모듈이 없습니다: {path}")
        rel = f"{EDITOR_REL}/{name}"
        files[rel] = normalize(rel, path.read_bytes())
    return files


def plan(root: Path, files: Mapping[str, bytes], previous: Mapping[str, str]) -> List[Action]:
    """files: 상대 경로 → 쓸 내용. previous: 지난 동기화 때 기록한 해시."""
    actions = []
    for rel, data in files.items():
        new = content_hash(rel, data)
        dest = dest_path(root, rel)
        if not dest.exists():
            actions.append(Action(rel, "create", new))
            continue
        cur = content_hash(rel, dest.read_bytes())
        if cur == new:
            actions.append(Action(rel, "unchanged", new, cur))
        elif previous.get(rel) == cur:
            actions.append(Action(rel, "update", new, cur))
        else:
            actions.append(Action(rel, "conflict", new, cur))
    return actions


def source_revision(repo: Path) -> Dict[str, Any]:
    """정본 저장소의 커밋과, 동기화 대상 파일에 커밋 안 된 변경이 있는지."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                  timeout=10, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", CORE_REL, "data", EDITOR_SRC))
    return {"commit": commit, "dirty": dirty}


def build_info(pack: Mapping[str, Any], hashes: Mapping[str, str], revision: Mapping[str, Any]) -> Dict[str, Any]:
    """노드가 시작할 때 읽는 작은 정보 파일(콤보 목록 등). 시각을 넣지 않아 같은 입력이면 같은 내용이다."""
    return {
        "format": INFO_FORMAT,
        "pack_version": pack.get("version", ""),
        "source": pack.get("source", {}),
        "presets": {pid: p.get("label", pid) for pid, p in pack["presets"].items()},
        "layouts": {lid: l.get("label", lid) for lid, l in pack["layouts"].items()},
        "quick": {g: list(((pack.get("rules") or {}).get("quick") or {}).get(g, ())) for g in ("framing", "view")},
        "synced_from": {"repo": "TagOrganizer", **revision},
        "files": dict(hashes),
    }


def _without_source(info: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in info.items() if k != "synced_from"}


def _write_atomic(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".sync-tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync(target: Path, core_path: Path, pack_path: Path, *, repo: Optional[Path] = None,
         editor_repo: Path = REPO_ROOT, force: bool = False, dry_run: bool = False) -> List[Action]:
    """동기화를 실행하고 파일별 동작을 돌려준다. 충돌이 있으면(그리고 force 가 아니면) 아무것도 쓰지 않는다."""
    root = validate_target(Path(target))
    pack_gz, pack = load_pack_bytes(Path(pack_path))
    files = {CORE_REL: normalize(CORE_REL, Path(core_path).read_bytes()), PACK_REL: pack_gz,
             **load_editor_files(Path(editor_repo))}

    old_info = read_info(root)
    actions = plan(root, files, old_info.get("files", {}))
    conflicts = [a.rel for a in actions if a.state == "conflict"]
    if conflicts and not force:
        raise SyncConflict(conflicts)

    hashes = {rel: content_hash(rel, data) for rel, data in files.items()}
    info = build_info(pack, hashes, source_revision(repo) if repo else {"commit": "", "dirty": False})
    info_bytes = (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    info_dest = dest_path(root, INFO_REL)
    if not info_dest.exists():
        info_action = Action(INFO_REL, "create", sha256_bytes(info_bytes))
    else:
        old_bytes = info_dest.read_bytes()
        same = all(a.state == "unchanged" for a in actions) and _without_source(old_info) == _without_source(info)
        # 복사한 파일·기록 내용이 그대로면 정본 커밋만 바뀌어도 다시 쓰지 않는다(노드 팩에 빈 변경이 생기지 않게).
        # --force 는 기록을 새로 쓴다.
        if (same and not force) or old_bytes == info_bytes:
            info_action = Action(INFO_REL, "unchanged", sha256_bytes(old_bytes), sha256_bytes(old_bytes))
        else:
            info_action = Action(INFO_REL, "update", sha256_bytes(info_bytes), sha256_bytes(old_bytes))
    actions.append(info_action)
    if dry_run:
        return actions
    for a in actions[:-1]:
        if a.state != "unchanged":
            _write_atomic(dest_path(root, a.rel), files[a.rel])
    if info_action.state != "unchanged":
        _write_atomic(info_dest, info_bytes)
    return actions
