"""미리보기·작업·통계 탭의 일 — 미리보기 팩, 빌드, 노드 팩 반영(sync), 원본 가져오기(import), 통계. Qt 없이 쓰고 테스트한다.

CLI(`tagorg build`·`sync`·`import`·`stats`)와 같은 함수를 같은 순서로 부른다. 빌드와 sync 는 CLI 처럼 파일에서
읽어 만든다(정본 그대로). 미리보기 팩만 관리 툴 메모리(저장된 편집 그대로)로 만든다.
화면은 0.5~5초 걸리는 이 일들을 백그라운드 스레드에서 부른다.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core
from tools import builder, evidence, importer, stats, sync, tagdb
from tools.tagdb import SourceTag

SYNC_TARGET = tagdb.ROOT / "local" / "sync.json"    # tagorg.py 의 SYNC_TARGET 과 같은 파일(마지막 동기화 대상)
EVIDENCE_CACHE = tagdb.ROOT / "work" / "evidence" / "evidence.json"   # tagorg.py 의 EVIDENCE_CACHE 와 같은 파일
ARRANGEMENTS = tagdb.ROOT / "local" / "arrangements"                 # 미리보기 탭에서 저장한 배열 세팅
SYNC_LABELS = {"create": "새로 만듦", "update": "갱신", "unchanged": "변경 없음",
               "conflict": "노드 팩에서 바뀜(덮어쓰려면 확인 필요)"}


@dataclass
class Inputs:
    """팩을 만드는 재료. CLI build 와 같다."""
    source: Sequence[SourceTag]
    meta: Dict[str, Any]                    # file · date · rows
    overlay: Mapping[str, Mapping[str, str]]
    extra: Sequence[Mapping[str, str]]      # 확장 DB 행(data/extra)
    config: Mapping[str, Any]
    site: Optional[tagdb.Site] = None       # 두 번째 원본(e621) — sources.json 에 없으면 None


def file_inputs() -> Inputs:
    """`tagorg build` 와 같은 입력(파일에서 새로 읽음)."""
    path, entry = tagdb.current_source()
    source = tagdb.read_source_csv(path)
    meta = {"file": entry.get("file", path.name), "date": entry.get("date", ""), "rows": len(source)}
    return Inputs(source, meta, tagdb.read_overlay(), tagdb.read_extra(), tagdb.load_config(), tagdb.load_site())


def memory_inputs(store, extra: Optional[Sequence[Mapping[str, str]]] = None) -> Inputs:
    """관리 툴 메모리의 입력. overlay 는 얕은 복사(apply_fix 는 행을 새 dict 로 바꿔 넣으므로 스냅숏이 된다).
    확장 DB 는 주지 않으면 store 의 것(관리 툴에서 고치고 더한 것). e621 은 store 가 읽은 것(읽기만 함)."""
    rows = store.extra_list() if extra is None else extra
    site = getattr(store, "site", None)
    if site is not None:            # e621.csv 행도 얕은 복사(미리보기 빌드는 백그라운드 — 그사이 편집이 dict 를 바꾼다)
        site = tagdb.Site(site.id, site.tags, site.meta, dict(site.overlay))
    return Inputs(store.source, {**store.meta, "rows": len(store.source)}, dict(store.overlay), list(rows),
                  store.config, site)


@dataclass
class Env:
    """화면이 쓰는 경로와 입력. 테스트는 임시 폴더로 바꾼다."""
    pack_path: Path = tagdb.PACK_PATH
    sync_file: Path = SYNC_TARGET
    core_path: Path = tagdb.ROOT / sync.CORE_REL
    repo: Optional[Path] = tagdb.ROOT
    inputs: Callable[[], Inputs] = file_inputs
    extra: Callable[[], List[Dict[str, str]]] = tagdb.read_extra
    import_source: Callable[..., str] = importer.import_source
    evidence_cache: Path = EVIDENCE_CACHE
    evidence_get: Callable[..., Any] = evidence.http_get     # Danbooru 조회(테스트는 가짜)
    evidence_delay: float = 0.5                              # 요청 사이 간격(evidence show 와 같음)
    arrangements_dir: Path = ARRANGEMENTS                    # 저장한 배열 세팅(이 PC 전용)
    shared_arrangements_dir: Path = tagdb.SHARED_ARRANGEMENTS   # 공유 배열 프리셋(저장소에 커밋)


@dataclass
class Built:
    version: str
    tags: int
    aliases: int
    size: int
    path: Path
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"팩 {self.version} — 태그 {self.tags:,} · 별칭 {self.aliases:,} · {self.size / 1e6:.1f} MB → "
                f"{self.path}")


# ---- 팩 ----

def build(env: Env, out: Optional[Path] = None) -> Built:
    """`tagorg build`. 오류가 있으면 builder.BuildError(오류 목록)."""
    inputs = env.inputs()
    pack, warnings = builder.build_pack(inputs.source, inputs.meta, inputs.overlay, inputs.extra, inputs.config,
                                        inputs.site)
    out = Path(out or env.pack_path)
    size = builder.write_pack(pack, out)
    return Built(pack["version"], len(pack["tags"]), len(pack["aliases"]), size, out, warnings)


def memory_pack(inputs: Inputs) -> core.TagPack:
    """미리보기용 팩(파일에 쓰지 않음)."""
    pack, _ = builder.build_pack(inputs.source, inputs.meta, inputs.overlay, inputs.extra, inputs.config, inputs.site)
    return core.TagPack(pack)


def version_of(inputs: Inputs) -> str:
    """이 입력으로 빌드하면 나올 팩 버전(build_pack 과 같은 식)."""
    date = str(inputs.meta.get("date", "")).replace("-", ".")
    return f"{date}+{builder.content_hash(inputs.overlay, inputs.extra, inputs.config, inputs.site)}"


def built_version(path: Path) -> Optional[str]:
    """빌드해 둔 팩의 버전. 없거나 못 읽으면 None."""
    try:
        return core.read_pack_data(str(path)).get("version")
    except Exception:           # noqa: BLE001 — 빌드가 쓰는 중이면 zlib.error 등 무엇이든 날 수 있다
        return None


# ---- 노드 팩 반영 ----

def sync_target(env: Env) -> Optional[Path]:
    try:
        return Path(json.loads(env.sync_file.read_text(encoding="utf-8"))["nodes_dir"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def sync_preview(env: Env, target: Path) -> Tuple[Built, List[sync.Action]]:
    """`tagorg sync --dry-run`: 임시 폴더에 빌드해 비교만 한다(build/ 도 노드 팩도 바꾸지 않음).

    CLI 는 충돌이 있으면 바로 멈추지만, 미리보기는 충돌한 파일도 목록에 보여 준다(force 로 비교만 하고 쓰지 않음).
    """
    with tempfile.TemporaryDirectory() as tmp:
        built = build(env, Path(tmp) / "tagpack.json.gz")
        try:
            actions = sync.sync(target, env.core_path, built.path, repo=env.repo, dry_run=True)
        except sync.SyncConflict:
            actions = sync.sync(target, env.core_path, built.path, repo=env.repo, force=True, dry_run=True)
    return built, actions


def sync_apply(env: Env, target: Path, force: bool = False) -> Tuple[Built, List[sync.Action]]:
    """`tagorg sync [--force]`: build/ 에 빌드한 뒤 노드 팩에 쓴다. 노드 팩에서 바뀐 파일이 있으면 sync.SyncConflict."""
    built = build(env)
    actions = sync.sync(target, env.core_path, built.path, repo=env.repo, force=force)
    env.sync_file.parent.mkdir(parents=True, exist_ok=True)
    env.sync_file.write_text(json.dumps({"nodes_dir": str(Path(target).resolve())}, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return built, actions


def format_actions(actions: Sequence[sync.Action]) -> List[str]:
    return [f"{SYNC_LABELS.get(a.state, a.state)}  {a.rel}" for a in actions]


# ---- 원본 가져오기 ----

def import_csv(env: Env, csv_path: Path, date: str = "", url: str = "", move: bool = False,
               dry_run: bool = False) -> str:
    """`tagorg import [--dry-run]`. 날짜를 비우면 파일 이름에서 찾는다."""
    return env.import_source(Path(csv_path), date or None, None, url, move, dry_run=dry_run)


# ---- 통계 ----

def coverage(inputs: Inputs) -> List[Dict]:
    return stats.coverage(inputs.source, inputs.overlay)


def stats_text(inputs: Inputs, pack_version: Optional[str]) -> str:
    """`tagorg stats` 와 같은 글."""
    return stats.format_stats(inputs.source, inputs.overlay, pack_version)
