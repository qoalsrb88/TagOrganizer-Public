"""GUI 데이터 계층 — Qt 없이 분류를 찾고 고치고 저장한다(화면과 떼어 테스트할 수 있게).

고치기는 CLI `tagorg fix` 와 같은 apply_fix 를 쓴다. 검증·reviewed 잠금·note 정리가 CLI 와 똑같다.
고칠 때마다 바로 저장하고, 쓰기 직전에 overlay 가 밖에서(CLI·다른 세션·git checkout) 바뀌었는지
파일 해시로 확인해 덮어쓰지 않는다.

확장 DB(data/extra) 행도 같은 목록·같은 API 로 다룬다(Entry.db 가 DB ID). 고치기는 apply_fix_rows, 저장은 그 DB
파일만(파일마다 해시로 충돌 검사). 두 번째 원본(e621)에만 있는 태그도 같은 목록에 둔다(Entry.site). 고치기는
apply_fix_site, 저장은 e621.csv 만. 한 번에 고치는 태그는 원본(overlay)·확장 DB 하나·e621 중 한쪽으로 — 저장이 파일
하나로 끝나야 되돌리기·충돌 처리가 한 번에 맞는다.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core
from tools import batch, builder, extra, fix, lint, tagdb
from tools.tagdb import OVERLAY_FIELDS, SITE_FIELDS, SourceTag

EXTRA_TYPE = -2         # 확장 DB 행(Entry.db 에 DB ID)
TYPE_LABELS = {0: "일반", 1: "작가", 3: "작품", 4: "캐릭터", 5: "메타", -1: "원본에 없음", EXTRA_TYPE: "확장 DB"}
EXTRA_LABELS = {"model": "모델 태그", "deprecated": "폐기 태그", "user": "내 태그"}
EXTRA_SHORT = {"model": "모델", "deprecated": "폐기", "user": "내 태그"}      # 표의 좁은 종류 칸
UNCLASSIFIED = "-"      # 분류 필터: overlay 에 분류가 없는 general(새 원본을 import 한 뒤 생긴다)
AUTO = "auto"           # 상태: overlay 행이 없어 type 으로 자동 분류되는 태그(작가·작품·캐릭터·메타)
MAX_EDIT = 500          # 한 번에 고칠 수 있는 태그 수(전체 선택으로 22만 개를 고치는 실수 방지)
EXTRA_ALL = "extra"     # 종류 필터: 확장 DB 전부. "extra:<DB>" 는 그 DB 만
ORIGINAL = "original"   # 종류 필터: 원본 태그 전부(확장 DB·e621 제외 — 보류 작업 목록)
SITE_ALL = tagdb.E621   # 종류 필터: e621 에만 있는 태그 전부. "e621:<e621 종류>" 는 그 종류만
SITE_TYPE_LABELS = {0: "일반", 5: "종", 7: "메타", 1: "작가", 4: "캐릭터", 3: "작품"}     # e621 종류(필터 순서)
SITE_TARGET = "@site"   # _target 이 돌려주는 '고칠 곳 = e621.csv' 표시(DB ID 와 겹치지 않는 값)

Change = Tuple[str, Dict[str, str], Dict[str, str]]     # (이름, 전, 후). 전이 {} 이면 새 행
Loaded = Tuple[Sequence[SourceTag], Dict[str, Dict[str, str]], Mapping[str, Any], Dict[str, str]]


class ConflictError(RuntimeError):
    """불러온 뒤 overlay 파일이 밖에서 바뀌었다(덮어쓰지 않았다)."""


@dataclass(frozen=True)
class Entry:
    name: str
    type: int           # danbooru type. -1 이면 원본에서 사라진 overlay 행(missing_since), EXTRA_TYPE 이면 확장 DB
    count: int
    aliases: Tuple[str, ...] = ()
    db: str = ""        # 확장 DB 행의 DB ID
    site: str = ""      # 두 번째 원본(e621)에만 있는 태그면 그 원본 ID. type 은 e621 종류(0 일반·5 종·7 메타 …)


def db_label(db: str) -> str:
    return EXTRA_LABELS.get(db, db)


@dataclass
class Query:
    text: str = ""
    type: Any = 0                   # None 전체 · danbooru type · EXTRA_ALL · "extra:<DB>" · ORIGINAL · SITE_ALL · "e621:<종류>"
    primary: str = ""               # "" 전체 · 메인(clothes) · main/sub · etc · UNCLASSIFIED
    status: str = ""                # "" 전체 · overlay status · AUTO
    rating: Optional[str] = None    # None 전체 · "" 등급 없음 · g/s/q/e


@dataclass
class Result:
    changes: List[Change] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class CheckReport:
    errors: List[str]
    info: List[str]
    diff: List[Change]                          # 마지막 커밋 대비 바뀐 행. 커밋과 비교하지 못하면 빈 목록
    lint: List[Tuple[str, str, List[str]]]


def file_hash(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def load_files() -> Loaded:
    path, entry = tagdb.current_source()
    meta = {"file": entry.get("file", path.name), "date": entry.get("date", "")}
    return tagdb.read_source_csv(path), tagdb.read_overlay(), tagdb.load_config(), meta


class Store:
    def __init__(self, loader: Callable[[], Loaded] = load_files, overlay_path: Path = tagdb.OVERLAY,
                 committed: Optional[Callable[[], Optional[Dict[str, Dict[str, str]]]]] = None,
                 extra_dir: Optional[Path] = None,
                 site_loader: Optional[Callable[[], Optional[tagdb.Site]]] = None,
                 site_path: Optional[Path] = None) -> None:
        self._loader = loader
        self.overlay_path = Path(overlay_path)
        self.extra_dir = Path(extra_dir) if extra_dir is not None else tagdb.EXTRA_DIR
        self._committed = committed or (lambda: fix.committed_overlay(self.overlay_path))
        # 두 번째 원본(e621). 파일에서 읽는 기본 loader 일 때만 sources.json 의 e621 을 읽는다(테스트는 site_loader·
        # site_path 를 준다). 고친 행은 site_path(e621.csv)에 쓰고, 그 파일이 밖에서 바뀌면 다시 불러온다
        default = site_loader is None and loader is load_files
        self._site_loader = site_loader or (tagdb.load_site if default else (lambda: None))
        self.site_path: Optional[Path] = Path(site_path) if site_path else (tagdb.E621_OVERLAY if default else None)
        self.reload()

    # ---- 불러오기 ----

    def reload(self) -> None:
        """원본·overlay·설정을 다시 읽는다. 되돌리기 기록은 지운다(밖에서 바뀐 뒤에는 맞지 않는다)."""
        digest = file_hash(self.overlay_path)   # 읽기 전에 잡는다: 읽는 사이 바뀌면 다음 저장이 충돌로 멈춘다
        extra_digests = self._extra_digests()
        self._site_hash = file_hash(self.site_path) if self.site_path else None
        source, overlay, config, meta = self._loader()
        extra_rows = tagdb.read_extra(self.extra_dir)
        self.site: Optional[tagdb.Site] = self._site_loader()
        self.source = list(source)
        self.overlay = overlay
        self.config = config
        self.meta = dict(meta)
        by_name = {t.name: t for t in self.source}
        site_tags = [t for t in (self.site.tags if self.site else ()) if t.type in builder.E621_TYPES]
        site_by = {t.name: t for t in site_tags if t.name not in by_name}
        self.entries = [Entry(t.name, t.type, t.count, t.aliases) for t in self.source]       # 사용량 순
        # 원본에서 사라진 overlay 행(missing_since)이 지금 e621 태그면 e621 쪽으로 보인다(빌드·fix 와 같음)
        self.entries += [Entry(n, -1, 0) for n in sorted(overlay) if n not in by_name and n not in site_by]
        self._load_extra(extra_rows, {extra.tag_name(n) for n in site_by})
        front_keys = set(self._extra_keys)
        for t in site_tags:                     # 사용량 순. 모델·내 태그가 이긴 이름은 그 확장 DB 행으로 보인다
            if t.name in site_by and extra.tag_name(t.name) not in front_keys:
                self.entries.append(Entry(t.name, t.type, t.count, t.aliases, site=self.site.id))
        self.by_name = {e.name: e for e in self.entries}
        self._src_index = by_name
        self._aliases = fix.alias_index(self.source)
        self._site_by = {n: t for n, t in site_by.items() if self.is_site(n)}
        self._site_aliases = fix.alias_index(site_tags)
        self._site_target = self._site_auto_targets(site_tags)
        self._memo: Tuple[int, Dict[str, Optional[str]]] = (-1, {})
        self._heads: Optional[Dict[str, List[Entry]]] = None
        tax = config["taxonomy"]
        self.categories = tax["categories"]
        self.rated = {c["id"] for c in self.categories if c.get("rated")}
        self.regions = list(config["rules"].get("regions", ()))
        self.labels: Dict[str, str] = {"etc": "보류"}
        self.colors: Dict[str, str] = {}
        for c in list(self.categories) + list(tax.get("system", ())):
            self.labels.setdefault(c["id"], c.get("label", c["id"]))
            self.colors[c["id"]] = c.get("color", "")
            for s in c.get("subs", ()):
                self.labels[f"{c['id']}/{s['id']}"] = f"{self.labels[c['id']]}/{s.get('label', s['id'])}"
        self._hash = digest
        self._extra_hash = extra_digests
        self.undo_stack: List[List[Change]] = []
        self._undo_site: List[bool] = []        # undo_stack 과 짝 — 그 변경이 e621.csv 인가
        self.edited: set = set()                # 이번에 연 뒤로 고친 태그(상태 표시줄용)
        # 메모리의 분류가 바뀔 때마다 올라간다(미리보기 팩·통계가 다시 만들지 판단)
        self.revision = getattr(self, "revision", 0) + 1

    def _load_extra(self, rows: Sequence[Dict[str, str]], site_keys=()) -> None:
        """확장 DB 행을 DB 마다 파일 순서로 두고, 이름이 원본·overlay·앞 행과 겹치지 않는 행만 목록에 보인다
        (겹치는 행은 빌드도 건너뛰거나 멈춘다 — 저장할 때는 그대로 다시 쓴다). 폐기 DB(LATE_DBS) 행이 e621 태그와
        겹치면 e621 이 이긴다(빌드와 같음)."""
        self.extra_files: Dict[str, List[Dict[str, str]]] = {}
        self.extra_rows: Dict[str, Dict[str, str]] = {}
        self.extra_hidden: List[Dict[str, str]] = []
        taken = {e.name for e in self.entries}
        site_keys = set(site_keys)
        for r in rows:
            self.extra_files.setdefault(r["db"], []).append(r)
            name = r.get("name", "")
            late_hidden = r.get("db") in builder.LATE_DBS and extra.tag_name(name) in site_keys
            if not name or name in taken or late_hidden:
                self.extra_hidden.append(r)
                continue
            taken.add(name)
            self.extra_rows[name] = r
            self.entries.append(Entry(name, EXTRA_TYPE, 0, tuple(fix.split_list(r.get("aliases"))), r["db"]))
        self._index_extra()

    def _index_extra(self) -> None:
        """찾기 색인: 확장 DB 이름과 별칭(정규화 키 → 목록 이름). 별칭은 앞 행이 이긴다(빌드와 같음)."""
        self._extra_keys = {extra.tag_name(n): n for n in self.extra_rows}
        self._extra_aliases: Dict[str, str] = {}
        for name, r in self.extra_rows.items():
            for alias in fix.split_list(r.get("aliases")):
                self._extra_aliases.setdefault(extra.tag_name(alias), name)

    def _extra_digests(self) -> Dict[str, Optional[str]]:
        return {p.stem: file_hash(p) for p in tagdb.extra_files(self.extra_dir)}

    def external_change(self) -> bool:
        """불러온(마지막으로 저장한) 뒤 overlay·확장 DB·e621 분류 파일이 밖에서 바뀌었는가(새로 생기거나 없어진 DB
        포함)."""
        site_changed = self.site_path is not None and file_hash(self.site_path) != self._site_hash
        return file_hash(self.overlay_path) != self._hash or self._extra_digests() != self._extra_hash or site_changed

    def _site_auto_targets(self, site_tags: Sequence[SourceTag]) -> Dict[str, str]:
        """e621 이름 → 자동 same_as 대상(Danbooru 이름). 빌더(_merge_layers)와 같은 조건: e621 general·meta 이고, 이름이
        Danbooru 별칭이며, 그 대상이 Danbooru general 이고 e621 에 따로 없다. 대상 분류가 meta 인지는 그때그때 본다
        (primary_of) — 대상의 분류를 고치면 표시도 바로 따라간다."""
        if self.site is None:
            return {}
        kaomoji = builder._kaomoji(self.source, self.extra_list(), self.site)
        d_keys = {builder.danbooru_key(t.name, t.type): t for t in self.source}
        d_alias: Dict[str, str] = {}
        for t in self.source:
            for a in t.aliases:
                if not a.startswith("/"):
                    ak = core.normalize_key(a, kaomoji)
                    if ak and ak not in d_keys:
                        d_alias.setdefault(ak, t.name)
        own = {builder.danbooru_key(t.name, t.type) for t in site_tags}
        out = {}
        for name, t in self._site_by.items():
            target = d_alias.get(builder.danbooru_key(t.name, t.type)) if t.type in builder.AUTO_SAME_AS_TYPES else None
            if target and self._src_index[target].type == 0 and builder.danbooru_key(target, 0) not in own:
                out[name] = target
        return out

    def is_site(self, name: str) -> bool:
        e = self.by_name.get(name)
        return e is not None and bool(e.site)

    def extra_list(self) -> List[Dict[str, str]]:
        """지금 확장 DB 행 전부(DB 순서 → 파일 순서) — 미리보기 팩을 만들 때 tagdb.read_extra() 대신."""
        return [r for rows in self.extra_files.values() for r in rows]

    def is_extra(self, name: str) -> bool:
        return name in self.extra_rows

    # ---- 보기 ----

    def row(self, name: str) -> Mapping[str, str]:
        """목록 항목의 분류 행 — 출처별로(e621 은 e621.csv, 확장 DB 는 그 행, 원본은 overlay). 이름이 같은 다른 출처의
        행(Danbooru 에서 사라진 overlay 행 등)을 섞지 않는다."""
        e = self.by_name.get(name)
        if e is not None and e.site:
            return self.site.overlay.get(name) or {}
        if e is not None and e.db:
            return self.extra_rows.get(name) or {}
        return self.overlay.get(name) or self.extra_rows.get(name) or {}

    def resolve(self, text: str) -> Optional[str]:
        """프롬프트 표기(공백·대문자·\\(…\\)·별칭)를 태그 이름으로. 원본에 없는 overlay 행 이름도 받는다.
        찾는 순서는 팩과 같다: 원본 이름 → 확장 DB 이름 → 원본 별칭(peace sign 은 v 의 별칭이지만 model 의 행).
        e621 이 있으면 fix.locate(이름: Danbooru → 확장 DB → e621 → 폐기 DB, 별칭: 같은 순서)."""
        if self.site is not None:
            rev, memo = self._memo
            if rev != self.revision:
                memo = {}
                self._memo = (self.revision, memo)
            if text not in memo:
                memo[text] = self._resolve_site(text)
            return memo[text]
        return self._resolve_plain(text)

    def _resolve_site(self, text: str) -> Optional[str]:
        """e621 이 있을 때 — fix.locate 로 팩과 같은 순서. 확장 DB 는 목록에 보이는 행만(빌드도 숨은 행의 별칭은 쓰지
        않는다)."""
        if text in self._src_index:             # 흔한 경우(same_as 대상이 Danbooru 이름)는 바로
            return text
        front = [r for r in self.extra_rows.values() if r.get("db") not in builder.LATE_DBS]
        late = [r for r in self.extra_rows.values() if r.get("db") in builder.LATE_DBS]
        kind, found = fix.locate(text, self._src_index, self._aliases, self.overlay, front, late, self._site_by,
                                 self._site_aliases, extra.lookup)
        if kind == "site":
            return found if self.is_site(found) else None
        if kind == "ext":
            name = found.get("name", "")
            return name if self.extra_rows.get(name) is found else None
        name, _ = fix.resolve_name(found, self._src_index, self._aliases)
        if name is None and found in self.by_name:
            name = found
        return name

    def _resolve_plain(self, text: str) -> Optional[str]:
        name, via_alias = fix.resolve_name(text, self._src_index, self._aliases)
        if name is None and text in self.by_name:
            name = text
        if name is None or via_alias:
            key = extra.tag_name(text.replace("\\(", "(").replace("\\)", ")"))
            name = self._extra_keys.get(key, name)
            if name is None:                    # 확장 DB 별칭은 원본 별칭 다음(빌드와 같음)
                name = self._extra_aliases.get(key)
        return name

    def primary_of(self, entry: Entry) -> Tuple[str, str]:
        """(분류, 상태). overlay 에 분류가 없으면 type 자동 분류(AUTO) 또는 미분류(""). 확장 DB 의 same_as 행은
        대상의 분류(빌드와 같음)."""
        if entry.site:
            row = self.site.overlay.get(entry.name) or {}
            if row.get("same_as"):
                target = self.resolve(row["same_as"])
                ok = target in self.by_name and not self.row(target).get("same_as")
                return (self.primary_of(self.by_name[target])[0] if ok else ""), row.get("status", "")
            if row.get("primary"):
                return row["primary"], row.get("status", "")
            # e621.csv 행이 없으면 빌드와 같은 순서: Danbooru 에서 사라진 공유 이름의 tags.csv 분류 → 자동 same_as →
            # e621 종류 자동 분류 → 미분류
            ov = self.overlay.get(entry.name)
            if ov and ov.get("primary"):
                return ov["primary"], ov.get("status", "")
            target = self._site_target.get(entry.name)
            if target is not None:
                primary = self.primary_of(self.by_name[target])[0]
                if primary.partition("/")[0] != "meta":
                    return (primary, AUTO) if primary and primary != "etc" else ("", "")
            auto = builder.E621_TYPES.get(entry.type, (0, ""))[1]
            return (auto, AUTO) if auto else ("", "")
        if entry.db:
            row = self.extra_rows.get(entry.name) or {}
            if row.get("same_as"):
                # 대상이 다시 same_as 인 행이면(빌드는 연쇄를 오류로 막는다) 따라가지 않는다 — 순환해도 멈추게
                target = self.resolve(row["same_as"])
                ok = target in self.by_name and not self.row(target).get("same_as")
                return (self.primary_of(self.by_name[target])[0] if ok else ""), row.get("status", "")
            return row.get("primary", ""), row.get("status", "")
        row = self.overlay.get(entry.name)
        if row and row.get("primary"):
            return row["primary"], row.get("status", "")
        auto = builder.TYPE_PRIMARY.get(entry.type)
        return (auto, AUTO) if auto else ("", "")

    def find(self, q: Query) -> List[Entry]:
        """조건에 맞는 태그를 사용량 순으로. 검색어가 이름·별칭으로 정확히 맞는 태그는 종류 필터와 상관없이 맨 앞."""
        text = q.text.strip()
        needle = text.lower().replace("\\(", "(").replace("\\)", ")")
        name_needle = needle.replace(" ", "_")
        space_needle = needle.replace("_", " ")           # 확장 DB 이름은 공백
        exact = self.resolve(text) if text else None

        def type_ok(e: Entry) -> bool:
            t = q.type
            if t is None:
                return True
            if t == EXTRA_ALL:
                return bool(e.db)
            if t == ORIGINAL:
                return not e.db and not e.site
            if t == SITE_ALL:
                return bool(e.site)
            if isinstance(t, str) and t.startswith("extra:"):
                return e.db == t[len("extra:"):]
            if isinstance(t, str) and t.startswith(SITE_ALL + ":"):
                return bool(e.site) and str(e.type) == t[len(SITE_ALL) + 1:]
            return e.type == t and not e.site

        def keep(e: Entry, check_type: bool = True) -> bool:
            if check_type and not type_ok(e):
                return False
            primary, status = self.primary_of(e)
            if q.primary:
                if q.primary == UNCLASSIFIED:
                    if primary:
                        return False
                elif not (primary == q.primary or primary.startswith(q.primary + "/")):
                    return False
            if q.status and status != q.status:
                return False
            if q.rating is not None and (self.row(e.name).get("rating") or "") != q.rating:
                return False
            return True

        out = []
        for e in self.entries:
            if (text and name_needle not in e.name and space_needle not in e.name
                    and needle not in (self.row(e.name).get("note") or "").lower()):
                continue
            if e.name != exact and keep(e):
                out.append(e)
        if exact and keep(self.by_name[exact], check_type=False):
            out.insert(0, self.by_name[exact])
        return out

    def common(self, names: Sequence[str]) -> Dict[str, Optional[str]]:
        """고칠 수 있는 칸마다 모두 같은 값이면 그 값, 다르면 None(여러 태그를 골랐을 때 폼 표시용)."""
        out: Dict[str, Optional[str]] = {}
        for k in fix.EDITABLE:
            vals = {self.row(n).get(k, "") for n in names}
            out[k] = vals.pop() if len(vals) == 1 else None
        return out

    def similar(self, name: str, limit: int = 8) -> List[Tuple[Entry, Mapping[str, str]]]:
        """같은 계열(마지막 단어·끝의 한정어, batch --group head 와 같은 기준)에서 분류된 태그를 사용량 순으로."""
        if self._heads is None:
            heads: Dict[str, List[Entry]] = defaultdict(list)
            for e in self.entries:
                if e.type in (0, -1, EXTRA_TYPE):
                    heads[batch.head_key(e.name.replace(" ", "_"))].append(e)
            self._heads = heads
        out = []
        for e in self._heads.get(batch.head_key(name.replace(" ", "_")), ()):
            row = self.row(e.name)
            if e.name != name and row.get("primary") not in (None, "", "etc"):
                out.append((e, row))
                if len(out) >= limit:
                    break
        return out

    def counts(self) -> Dict[str, int]:
        general = [e for e in self.entries if e.type == 0 and not e.site]
        return {
            "overlay": len(self.overlay),
            "etc": sum(1 for r in self.overlay.values() if r.get("primary") == "etc"),
            "reviewed": sum(1 for r in self.overlay.values() if r.get("status") == "reviewed"),
            "unclassified": sum(1 for e in general if not self.row(e.name).get("primary")),
            "edited": len(self.edited),
            "extra": len(self.extra_rows),
            "site": sum(1 for e in self.entries if e.site),
        }

    # ---- 고치기 ----

    def preview(self, names: Sequence[str], updates: Mapping[str, str], etc: bool = False,
                by: Optional[str] = None) -> Result:
        """apply 와 같은 검증·결과를 보여 주기만 한다(파일·메모리를 바꾸지 않음)."""
        return self.preview_plan([(names, updates)], etc, by)

    def apply(self, names: Sequence[str], updates: Mapping[str, str], etc: bool = False,
              by: Optional[str] = None) -> Result:
        """고치고 바로 저장한다. 오류가 있으면 아무것도 바꾸지 않는다. 밖에서 바뀌었으면 ConflictError."""
        return self.apply_plan([(names, updates)], etc, by)

    def preview_plan(self, plan: Sequence[Tuple[Sequence[str], Mapping[str, str]]], etc: bool = False,
                     by: Optional[str] = None) -> Result:
        """태그 묶음마다 다른 수정 내용(여러 태그의 부위를 하나씩 더하고 뺄 때). 묶음끼리 이름이 겹치면 안 된다."""
        names = [n for group, _ in plan for n in group]
        if len(names) > MAX_EDIT:
            return Result(errors=[f"한 번에 {MAX_EDIT}개까지 고칠 수 있습니다({len(names):,}개 선택)"])
        db, error = self._target(names)
        if error:
            return Result(errors=[error])
        if db == SITE_TARGET:
            scratch = {n: dict(self.site.overlay[n]) for n in names if n in self.site.overlay}
            return self._run_plan(scratch, plan, etc, by or self.default_by(), site=True)
        if db:
            scratch = {n: dict(self.extra_rows[n]) for n in names}
            return self._run_plan(scratch, plan, etc, by or self.default_by(), extra_rows=True)
        scratch = {n: self.overlay[n] for n in names if n in self.overlay}
        return self._run_plan(scratch, plan, etc, by or self.default_by())

    def _target(self, names: Sequence[str]) -> Tuple[str, str]:
        """(확장 DB ID — 원본이면 "", e621 이면 SITE_TARGET, 오류). 한 번에 고치는 태그는 원본·확장 DB 하나·e621 중
        한쪽."""
        sites = [n for n in names if self.is_site(n)]
        if sites:
            if len(sites) != len(names):
                return "", "e621 태그는 원본·확장 DB 태그와 따로 고칩니다(한 번에 한쪽만 고르세요)"
            if self.site_path is None:
                return "", "e621 분류 파일 경로가 없어 고칠 수 없습니다"
            return SITE_TARGET, ""
        dbs = {self.extra_rows[n]["db"] for n in names if n in self.extra_rows}
        if dbs and any(n not in self.extra_rows for n in names):
            return "", "원본 태그와 확장 DB 태그는 따로 고칩니다(한 번에 한쪽만 고르세요)"
        if len(dbs) > 1:
            labels = ", ".join(db_label(d) for d in sorted(dbs))
            return "", f"확장 DB 는 하나씩 고칩니다({labels} — 한 DB 의 태그만 고르세요)"
        return (dbs.pop() if dbs else ""), ""

    def apply_plan(self, plan: Sequence[Tuple[Sequence[str], Mapping[str, str]]], etc: bool = False,
                   by: Optional[str] = None) -> Result:
        """preview_plan 이 통과하면 모든 묶음을 한 번에 고치고 저장한다(되돌리기 한 번에 전부 되돌아감)."""
        by = by or self.default_by()
        res = self.preview_plan(plan, etc, by)
        if res.errors or not res.changes:
            return Result([], res.errors, res.notes)
        db, _ = self._target([n for group, _ in plan for n in group])
        if db == SITE_TARGET:
            self._ensure_site_unchanged()
            res = self._run_plan(self.site.overlay, plan, etc, by, site=True)
            if res.errors:
                return res
            try:
                self._write_site()
            except (OSError, ConflictError):
                self._put_back(res.changes, self.site.overlay)
                raise
            self._done(res.changes, site=True)
            return res
        if db:
            self._ensure_extra_unchanged(db)
            table = {n: dict(self.extra_rows[n]) for group, _ in plan for n in group}
            res = self._run_plan(table, plan, etc, by, extra_rows=True)
            if res.errors:
                return res
            self._commit_extra(db, {n: after for n, _, after in res.changes})
            self._done(res.changes)
            return res
        self._ensure_unchanged()
        res = self._run_plan(self.overlay, plan, etc, by)
        if res.errors:                          # preview 와 같은 입력이라 오지 않지만, 왔다면 이미 되돌려져 있다
            return res
        try:
            self._write()
        except (OSError, ConflictError):
            self._put_back(res.changes)
            raise
        self._done(res.changes)
        return res

    def _done(self, changes: List[Change], site: bool = False) -> None:
        self.undo_stack.append(changes)
        self._undo_site.append(site)
        self.edited.update(n for n, _, _ in changes)
        self.revision += 1

    def _run_plan(self, overlay: Dict[str, Dict[str, str]], plan, etc: bool, by: str,
                  extra_rows: bool = False, site: bool = False) -> Result:
        """묶음마다 apply_fix(확장 DB 면 apply_fix_rows, e621 이면 apply_fix_site). 하나라도 틀리면 앞 묶음의 변경까지
        되돌리고 모든 묶음의 오류를 모아 알린다."""
        out = Result()
        for group, updates in plan:
            if site:
                changes, errors, notes = fix.apply_fix_site(overlay, group, updates, self.config, by, self.site.id,
                                                            etc=etc)
            elif extra_rows:
                changes, errors, notes = fix.apply_fix_rows(overlay, group, updates, self.config, by, etc=etc)
            else:
                changes, errors, notes = fix.apply_fix(overlay, group, updates, self.source, self.config, by, etc=etc)
            out.changes += changes
            out.errors += errors
            out.notes += notes
        if out.errors:
            for name, before, _ in out.changes:
                if before:
                    overlay[name] = before
                else:
                    overlay.pop(name, None)
            out.changes = []
        return out

    def undo(self) -> List[str]:
        """마지막 고치기를 되돌리고 저장한다(이전 status·by 까지 그대로). 되돌린 태그 이름."""
        if not self.undo_stack:
            return []
        changes = self.undo_stack[-1]
        if self._undo_site and self._undo_site[-1]:
            self._ensure_site_unchanged()
            current = [(n, dict(self.site.overlay.get(n) or {}), before) for n, before, _ in changes]
            self._put_back(changes, self.site.overlay)
            try:
                self._write_site()
            except (OSError, ConflictError):
                self._put_back(current, self.site.overlay)
                raise
            self.undo_stack.pop()
            self._undo_site.pop()
            self.revision += 1
            return [n for n, _, _ in changes]
        db = next((r.get("db") for _, before, after in changes for r in (before, after) if r.get("db")), "")
        if db:
            self._ensure_extra_unchanged(db)
            self._commit_extra(db, {n: before for n, before, _ in changes})
            self.undo_stack.pop()
            self._undo_site.pop()
            self.revision += 1
            return [n for n, _, _ in changes]
        self._ensure_unchanged()
        current = [(n, dict(self.overlay.get(n) or {}), before) for n, before, _ in changes]
        self._put_back(changes)
        try:
            self._write()
        except (OSError, ConflictError):
            self._put_back(current)
            raise
        self.undo_stack.pop()
        self._undo_site.pop()
        self.revision += 1
        return [n for n, _, _ in changes]

    @staticmethod
    def default_by() -> str:
        return f"user:{time.strftime('%Y-%m-%d')}"

    def _put_back(self, changes: Sequence[Change], rows: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        rows = self.overlay if rows is None else rows
        for name, before, _ in changes:
            if before:
                rows[name] = before
            else:
                rows.pop(name, None)

    def regions_plan(self, names: Sequence[str], updates: Mapping[str, str], add: Sequence[str],
                     drop: Sequence[str]) -> List[Tuple[List[str], Dict[str, str]]]:
        """여러 태그의 부위를 '켠 것은 더하고, 끈 것은 빼고, 나머지는 태그마다 그대로' 바꾸는 계획.

        결과 부위가 같은 태그끼리 묶는다. 바뀌지 않는 태그는 저장된 문자열(순서) 그대로 둔다.
        """
        groups: Dict[str, List[str]] = {}
        for name in names:
            cur = fix.split_list(self.row(name).get("regions"))
            new = [r for r in self.regions if (r in cur or r in add) and r not in drop]
            new += [r for r in cur if r not in self.regions and r not in drop]      # 어휘 밖 값은 검증이 알린다
            value = self.row(name).get("regions", "") if set(new) == set(cur) else "|".join(new)
            groups.setdefault(value, []).append(name)
        return [(group, {**updates, "regions": value}) for value, group in groups.items()]

    # ---- 확장 DB 에 새 태그 ----

    def add_extra(self, db: str, names: Sequence[str]) -> Tuple[List[str], List[str]]:
        """이름을 확장 DB 에 미분류로 더하고 바로 저장한다(`tagorg extra add` 와 같은 규칙 — 원본 이름·원본 별칭·
        확장 DB 에 이미 있으면 건너뜀). (더한 이름, 건너뛴 이름과 사유). 되돌리기로 지울 수 있다."""
        if not tagdb.EXTRA_ID.match(db):
            raise ValueError(f"DB ID 는 영문 소문자로 시작하고 소문자·숫자·밑줄만 씁니다: {db}")
        rows, skipped, added = extra.add_names(self.extra_list(), db, names, self.source, site=self.site)
        new = rows[len(rows) - added:]
        # 원본에서 사라진 overlay 행(missing_since)과 이름이 같으면 목록·되돌리기가 엉키므로 더하지 않는다
        skipped += [f"{r['name']} (원본에서 사라진 overlay 행)" for r in new if r["name"] in self.by_name]
        new = [r for r in new if r["name"] not in self.by_name]
        if not new:
            return [], skipped
        self._ensure_extra_unchanged(db)
        self._commit_extra(db, {r["name"]: r for r in new})
        self._done([(r["name"], {}, dict(r)) for r in new])
        return [r["name"] for r in new], skipped

    def _commit_extra(self, db: str, replaced: Mapping[str, Mapping[str, str]]) -> None:
        """DB 파일 하나에 바뀐 행을 반영해 저장하고(쓰기가 성공한 뒤에만) 메모리를 맞춘다.

        replaced: 이름 → 새 행. 새 행이 {} 면 그 행을 지우고(새로 더한 행 되돌리기), 파일에 없던 이름이면 끝에 더한다.
        """
        rows: List[Dict[str, str]] = []
        placed: Dict[str, Dict[str, str]] = {}      # 이름 → 이번에 넣은 그 행(같은 이름의 숨은 행과 섞이지 않게)
        done = set()
        for r in self.extra_files.get(db, ()):
            name = r.get("name", "")
            if name in replaced and self.extra_rows.get(name) is r:      # 목록에 보이는 그 행
                done.add(name)
                if replaced[name]:
                    placed[name] = dict(replaced[name], db=db)
                    rows.append(placed[name])
            else:
                rows.append(r)
        for name, row in replaced.items():
            if name not in done and row:
                placed[name] = dict(row, db=db)
                rows.append(placed[name])
        try:
            digest = tagdb.write_extra(db, rows, self.extra_dir, expected=self._extra_hash.get(db))
        except tagdb.OverlayChanged:
            raise ConflictError(self._CONFLICT) from None
        except ValueError as e:                 # 넘친 값·모르는 칸 — 쓰면 그 값을 잃는다
            raise ConflictError(f"{db}.csv 를 저장하지 않았습니다: {e}") from None
        self._extra_hash[db] = digest
        self.extra_files[db] = rows
        for name, r in placed.items():
            if name not in self.extra_rows:     # 새로 더한 행
                entry = Entry(name, EXTRA_TYPE, 0, tuple(fix.split_list(r.get("aliases"))), db)
                self.entries.append(entry)
                self.by_name[name] = entry
            self.extra_rows[name] = r
        for name, row in replaced.items():
            if not row and name in self.extra_rows:
                del self.extra_rows[name]
                entry = self.by_name.pop(name, None)
                if entry is not None:
                    self.entries.remove(entry)
        self._index_extra()
        self._heads = None

    def _ensure_extra_unchanged(self, db: str) -> None:
        if file_hash(tagdb.extra_path(db, self.extra_dir)) != self._extra_hash.get(db):
            raise ConflictError(self._CONFLICT)

    _CONFLICT = ("overlay·확장 DB 파일이 불러온 뒤 밖에서 바뀌었습니다(CLI·다른 세션·git). "
                 "덮어쓰지 않았습니다. 다시 불러온 뒤 저장하세요.")

    def _ensure_site_unchanged(self) -> None:
        if file_hash(self.site_path) != self._site_hash:
            raise ConflictError(self._CONFLICT)

    def _write_site(self) -> None:
        try:
            self._site_hash = tagdb.write_overlay(self.site.overlay, self.site_path, expected=self._site_hash,
                                                  fields=SITE_FIELDS)
        except tagdb.OverlayChanged:
            raise ConflictError(self._CONFLICT) from None

    def _ensure_unchanged(self) -> None:
        if self.external_change():
            raise ConflictError(self._CONFLICT)

    def _write(self) -> None:
        # 바꿔 넣기 직전에 한 번 더 비교하고(검사~쓰기 사이에 끼어든 저장), 기준 해시는 파일을 다시 읽지 않고
        # 내가 쓴 내용으로 잡는다(쓴 직후 다른 프로그램이 저장해도 그 내용을 내 것으로 착각하지 않게)
        try:
            self._hash = tagdb.write_overlay(self.overlay, self.overlay_path, expected=self._hash)
        except tagdb.OverlayChanged:
            raise ConflictError(self._CONFLICT) from None

    # ---- 점검 ----

    def check(self) -> CheckReport:
        """`tagorg check`(빌드 검사 + 마지막 커밋 대비) + 바뀐 행 목록 + `tagorg lint`."""
        committed = self._committed()
        errors, info = fix.check_overlay(self.overlay, self.source, self.config, committed)
        extra_errors, extra_warnings = builder.check_extra(self.source, self.overlay, self.extra_list(), self.config,
                                                           self.site)
        errors += extra_errors
        info += [f"확장 DB {len(self.extra_rows):,}행"] + [f"경고: {w}" for w in extra_warnings]
        if self.site is not None:
            info.append(f"{self.site.id} 원본 {len(self.site.tags):,}개 · {self.site.id}.csv {len(self.site.overlay):,}행")
        diff: List[Change] = []
        if committed is not None:
            for name in sorted(set(committed) | set(self.overlay)):
                old, new = committed.get(name) or {}, self.overlay.get(name) or {}
                if any((old.get(k) or "") != (new.get(k) or "") for k in OVERLAY_FIELDS):
                    diff.append((name, dict(old), dict(new)))
        return CheckReport(errors, info, diff, lint.pair_issues(self.overlay))
