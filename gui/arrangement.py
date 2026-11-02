"""배열 세팅 편집 — 줄·청크·멤버(서브)를 옮기는 일과 파일 읽기·쓰기. Qt 없이 쓰고 테스트한다.

배열 세팅 JSON 형식은 docs/arrangement.md 에 있고 코어의 load_arrangement 가 검사한다. 청크 화면
(gui/chunks.py)은 이 모델의 메서드만 부른다. 편집기만의 약속:
- 사용자가 만든 청크에는 "custom": true 를 적는다(지울 수 있음). 멤버가 없는 일반 청크는 남기지 않는다.
- 정의만 하고 배치하지 않은 청크(다른 툴에서 만든 파일)는 불러올 때 맨 끝 줄에 놓는다.
- 배열 파일은 두 곳: 공유 프리셋 data/arrangements/*.json(git 으로 함께 배포, 파일 이름은 영문 id)과
  내 배열 local/arrangements/*.json(이 PC 전용, git 제외). 공유 프리셋은 check_files 로 지금 분류 체계에 맞는지 검사한다.
- 다른 PC·옛 분류 체계에서 만든 파일은 tolerate 로 지금 없는 분류만 빼고 연다(그 칸의 태그는 '나머지' 줄로).
- description 과 모르는 최상위 키는 그대로 두고 다시 쓴다(다른 툴이 넣은 정보를 지우지 않음).
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import bmk_tag_organizer_core as core
from tools.builder import category_ids

_ID_TRIM = re.compile(r"[^A-Za-z0-9_.\-]+")
_KNOWN_KEYS = frozenset({"format", "version", "label", "description", "sub_order", "rows", "chunks", "guess"})
SHARE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")       # 공유 프리셋 파일 이름(OS·URL·노드 어디서나 같게)


def split_items(value: Any) -> List[str]:
    """"a, b" 글이나 목록 → 항목 목록(코어가 받는 두 형식 그대로)."""
    items = re.split(r"[,\n]", value) if isinstance(value, str) else list(value or ())
    return [str(v).strip() for v in items if str(v).strip()]


def config_pack(config: Mapping[str, Any]) -> SimpleNamespace:
    """관리 툴 설정(store.config)으로 코어 편집기 함수(palette·load_arrangement 등)를 부를 때 쓰는 팩 대용."""
    return SimpleNamespace(taxonomy=config["taxonomy"], rules=config["rules"], layouts=config["layouts"],
                           categories=category_ids(config["taxonomy"]), kaomoji=frozenset(),
                           guess=dict(config.get("guess") or {}))


class Model:
    """배열 세팅 하나. 바꾸는 메서드는 바뀌었으면 True(또는 새 id)를 돌려준다."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.load(data)

    # ---- 읽기·쓰기 ----

    def load(self, data: Mapping[str, Any]) -> None:
        d = copy.deepcopy(dict(data))
        self.label = str(d.get("label", "") or "")
        self.description = str(d.get("description", "") or "")
        self.extra = {k: v for k, v in d.items() if k not in _KNOWN_KEYS}
        self.sub_order = bool(d.get("sub_order", True))
        self.guess: List[Dict[str, Any]] = [dict(g) for g in d.get("guess") or ()]
        self.chunks: Dict[str, Dict[str, Any]] = {}
        for cid, spec in (d.get("chunks") or {}).items():
            spec = dict(spec or {})
            for key in ("members", "tags", "patterns"):
                if key in spec:
                    spec[key] = split_items(spec[key])
            self.chunks[str(cid)] = spec
        self.rows: List[List[str]] = [[str(c) for c in ([r] if isinstance(r, str) else r or ())]
                                      for r in d.get("rows") or ()]
        placed = set(self.flat())
        self.rows += [[cid] for cid in self.chunks if cid not in placed]
        self._tidy()

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"format": core.ARRANGEMENT_FORMAT, "version": core.ARRANGEMENT_VERSION,
                               "label": self.label}
        if self.description:
            out["description"] = self.description
        out.update({"sub_order": self.sub_order, "rows": [list(r) for r in self.rows],
                    "chunks": copy.deepcopy(self.chunks)})
        if self.guess:
            out["guess"] = copy.deepcopy(self.guess)
        out.update(copy.deepcopy(self.extra))
        return out

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True)

    # ---- 찾기 ----

    def flat(self) -> List[str]:
        return [cid for row in self.rows for cid in row]

    def where(self, cid: str) -> Tuple[int, int]:
        for ri, row in enumerate(self.rows):
            if cid in row:
                return ri, row.index(cid)
        raise KeyError(cid)

    def members(self, cid: str) -> List[str]:
        return list(self.chunks[cid].get("members", ()))

    def is_custom(self, cid: str) -> bool:
        spec = self.chunks[cid]
        return bool(spec.get("custom") or spec.get("tags") or spec.get("patterns"))

    def custom_ids(self) -> List[str]:
        return [cid for cid in self.flat() if self.is_custom(cid)]

    # ---- 청크 옮기기·줄바꿈 ----

    def move_chunk(self, cid: str, row: int, pos: int) -> bool:
        """cid 를 rows[row] 의 pos 자리(옮기기 전 기준)로."""
        r0, p0 = self.where(cid)
        if r0 == row and pos in (p0, p0 + 1):
            return False
        if r0 == row and p0 < pos:
            pos -= 1
        self.rows[r0].pop(p0)
        self.rows[row].insert(pos, cid)
        self._tidy()
        return True

    def move_chunk_new_row(self, cid: str, row: int) -> bool:
        """rows[row] 앞에 새 줄을 만들어 cid 만 넣는다(row == 줄 수면 맨 끝)."""
        r0, p0 = self.where(cid)
        if len(self.rows[r0]) == 1 and row in (r0, r0 + 1):
            return False
        self.rows[r0].pop(p0)
        self.rows.insert(row, [cid])
        self._tidy()
        return True

    def break_before(self, cid: str) -> bool:
        r, p = self.where(cid)
        if p == 0:
            return False
        self.rows[r:r + 1] = [self.rows[r][:p], self.rows[r][p:]]
        return True

    def break_after(self, cid: str) -> bool:
        r, p = self.where(cid)
        if p == len(self.rows[r]) - 1:
            return False
        self.rows[r:r + 1] = [self.rows[r][:p + 1], self.rows[r][p + 1:]]
        return True

    def join_previous(self, cid: str) -> bool:
        """cid 의 줄을 앞 줄 끝에 붙인다(줄 맨 앞에서 Backspace)."""
        r, _ = self.where(cid)
        if r == 0:
            return False
        self.rows[r - 1] += self.rows.pop(r)
        return True

    def shift(self, cid: str, delta: int) -> bool:
        """줄 안에서 한 칸. 줄 끝을 넘으면 앞 줄 끝·다음 줄 앞으로(글자처럼)."""
        r, p = self.where(cid)
        q = p + delta
        if 0 <= q < len(self.rows[r]):
            row = self.rows[r]
            row[p], row[q] = row[q], row[p]
            return True
        target = r + delta
        if not 0 <= target < len(self.rows):
            return False
        self.rows[r].pop(p)
        if delta < 0:
            self.rows[target].append(cid)
        else:
            self.rows[target].insert(0, cid)
        self._tidy()
        return True

    def shift_row(self, cid: str, delta: int) -> bool:
        """위·아래 줄의 같은 자리로. 더 갈 줄이 없으면 혼자인 새 줄을 만든다(이미 혼자면 그대로)."""
        r, p = self.where(cid)
        target = r + delta
        if not 0 <= target < len(self.rows):
            if len(self.rows[r]) == 1:
                return False
            return self.move_chunk_new_row(cid, 0 if delta < 0 else len(self.rows))
        self.rows[r].pop(p)
        self.rows[target].insert(min(p, len(self.rows[target])), cid)
        self._tidy()
        return True

    # ---- 멤버(서브) ----

    def move_member(self, src: str, mi: int, dst: str, di: int) -> bool:
        """멤버를 dst 의 di 자리(옮기기 전 기준)로. 같은 청크 안에서 순서를 바꾸면 서브 순서를 켠다."""
        if src == dst and di in (mi, mi + 1):
            return False
        member = self.chunks[src]["members"].pop(mi)
        if src == dst and mi < di:
            di -= 1
        self.chunks[dst].setdefault("members", []).insert(di, member)
        if src == dst:
            self.sub_order = True
        self._tidy()
        return True

    def shift_member(self, cid: str, mi: int, delta: int) -> bool:
        members = self.chunks[cid].get("members", [])
        q = mi + delta
        if not 0 <= q < len(members):
            return False
        members[mi], members[q] = members[q], members[mi]
        self.sub_order = True
        return True

    def detach_member(self, src: str, mi: int, row: int, pos: Optional[int] = None) -> str:
        """멤버를 빼내 새 청크로 만든다. pos 가 None 이면 rows[row] 앞에 새 줄, 아니면 그 줄의 pos 자리."""
        member = self.chunks[src]["members"][mi]
        cid = self._new_id(member.replace("/", "."))
        self.chunks[cid] = {"members": [member]}
        if pos is None:
            self.rows.insert(row, [cid])
        else:
            self.rows[row].insert(pos, cid)
        self.chunks[src]["members"].pop(mi)
        self._tidy()
        return cid

    def detach_after(self, src: str, mi: int) -> str:
        """같은 줄, 원래 청크 바로 뒤에 새 청크로(키보드 Enter)."""
        r, p = self.where(src)
        return self.detach_member(src, mi, r, p + 1)

    def merge_into(self, src: str, dst: str) -> bool:
        if src == dst:
            return False
        a, b = self.chunks[src], self.chunks[dst]
        b.setdefault("members", []).extend(m for m in a.get("members", ()) if m not in b["members"])
        for key in ("tags", "patterns"):
            if a.get(key):
                b[key] = list(dict.fromkeys(list(b.get(key, ())) + list(a[key])))
                b["custom"] = True
        self._remove(src)
        return True

    def add_slots(self, slots: Sequence[str]) -> Optional[str]:
        """어느 청크에도 맞지 않는 칸을 새 청크(맨 끝 줄)로."""
        if not slots:
            return None
        cid = self._new_id("more")
        self.chunks[cid] = {"label": "빠진 분류", "members": list(slots)}
        self.rows.append([cid])
        return cid

    # ---- 청크 속성 ----

    def toggle_hidden(self, cid: str) -> bool:
        spec = self.chunks[cid]
        if spec.get("hidden"):
            spec.pop("hidden")
        else:
            spec["hidden"] = True
        return True

    def set_label(self, cid: str, label: str) -> bool:
        label = label.strip()
        if label == self.chunks[cid].get("label", ""):
            return False
        if label:
            self.chunks[cid]["label"] = label
        else:
            self.chunks[cid].pop("label", None)
        return True

    def set_color(self, cid: str, color: str) -> bool:
        if color == self.chunks[cid].get("color", ""):
            return False
        if color:
            self.chunks[cid]["color"] = color
        else:
            self.chunks[cid].pop("color", None)
        return True

    def add_custom(self, label: str, color: str = "", tags: Iterable[str] = (), patterns: Iterable[str] = (),
                   row: Optional[int] = None) -> str:
        """새 커스텀 청크를 혼자인 새 줄로(row 가 None 이면 맨 위 — 보통 품질·내 태그를 앞에 둔다)."""
        cid = self._new_id("custom")
        self.chunks[cid] = {"custom": True}
        self.update_custom(cid, label, color, tags, patterns)
        self.rows.insert(0 if row is None else row, [cid])
        return cid

    def update_custom(self, cid: str, label: str, color: str, tags: Iterable[str], patterns: Iterable[str]) -> bool:
        before = json.dumps(self.chunks[cid], sort_keys=True)
        spec = self.chunks[cid]
        spec["custom"] = True
        for key, values in (("tags", tags), ("patterns", patterns)):
            values = list(dict.fromkeys(v.strip() for v in values if v.strip()))
            if values:
                spec[key] = values
            else:
                spec.pop(key, None)
        self.set_label(cid, label)
        self.set_color(cid, color)
        return json.dumps(spec, sort_keys=True) != before

    def add_tags(self, cid: str, tags: Iterable[str]) -> bool:
        spec = self.chunks[cid]
        old = list(spec.get("tags", ()))
        have = {core.normalize_key(t) for t in old}
        new = [t for t in dict.fromkeys(t.strip() for t in tags) if t and core.normalize_key(t) not in have]
        if not new:
            return False
        spec["tags"] = old + new
        spec["custom"] = True
        return True

    def delete_chunk(self, cid: str) -> bool:
        """커스텀 청크를 지운다. 안에 옮겨 둔 멤버가 있으면 멤버만 남긴 일반 청크로 둔다."""
        spec = self.chunks[cid]
        if not self.is_custom(cid):
            return False
        if spec.get("members"):
            for key in ("custom", "tags", "patterns"):
                spec.pop(key, None)
        else:
            self._remove(cid)
        return True

    # ---- 내부 ----

    def _remove(self, cid: str) -> None:
        self.chunks.pop(cid, None)
        self.rows = [[c for c in row if c != cid] for row in self.rows]
        self._tidy()

    def _new_id(self, base: str) -> str:
        base = _ID_TRIM.sub("_", base).strip("_.-") or "chunk"
        cid, n = base, 2
        while cid in self.chunks:
            cid, n = f"{base}{n}", n + 1
        return cid

    def _tidy(self) -> None:
        """없는 id·두 번 놓인 id·빈 줄을 치우고, 멤버가 없는 일반 청크는 지운다."""
        for cid in [c for c, spec in self.chunks.items() if not spec.get("members") and not self.is_custom(c)]:
            del self.chunks[cid]
        seen = set()
        rows = []
        for row in self.rows:
            kept = [c for c in row if c in self.chunks and not (c in seen or seen.add(c))]
            if kept:
                rows.append(kept)
        self.rows = rows


# ---- 파일 ----

def slug(label: str) -> str:
    return re.sub(r"[^\w\-]+", "_", label.strip()).strip("_") or "arrangement"


def share_id(label: str, source: str = "") -> str:
    """공유 프리셋 파일 이름 제안: 지금 파일 이름이 영문 id 면 그대로, 아니면 이름에서 영문·숫자만."""
    stem = Path(source).stem if source and not source.startswith("layout:") else ""
    if SHARE_ID.fullmatch(stem):
        return stem
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-_") or "arrangement"


def parse_text(text: str) -> Dict[str, Any]:
    """배열 세팅 JSON 글(파일·클립보드). 형식이 다르면 ValueError(내용 검사는 코어 load_arrangement)."""
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError as e:
        raise ValueError(f"JSON 을 읽지 못했습니다: {e}") from None
    if not isinstance(data, Mapping) or data.get("format") != core.ARRANGEMENT_FORMAT:
        raise ValueError(f"배열 세팅이 아닙니다(format 이 {core.ARRANGEMENT_FORMAT} 가 아님).")
    return dict(data)


def read_file(path: Path) -> Dict[str, Any]:
    return parse_text(Path(path).read_text(encoding="utf-8-sig"))


def tolerate(data: Mapping[str, Any], pack) -> Tuple[Dict[str, Any], List[str]]:
    """다른 PC·옛 분류 체계에서 만든 배열: 지금 분류 체계에 없는 멤버·추정 규칙만 빼고 (새 배열, 뺀 것 설명)을 준다.

    뺀 칸의 태그는 '나머지' 줄로 간다(버리지 않음). 그 밖의 잘못(패턴·색·모양)은 그대로 둬서 load_arrangement 가 거부한다.
    """
    d = copy.deepcopy(dict(data))
    notes: List[str] = []
    chunks = d.get("chunks")
    for cid, spec in (chunks.items() if isinstance(chunks, dict) else ()):
        if not isinstance(spec, dict) or not isinstance(spec.get("members"), (str, list, tuple)):
            continue
        members = split_items(spec["members"])
        dropped = [m for m in members if core.Selector.parse(m).validate(pack.categories)]
        if dropped:
            spec["members"] = [m for m in members if m not in dropped]
            notes.append(f"{spec.get('label') or cid}: {', '.join(dropped)}")
    guess = d.get("guess")
    if isinstance(guess, list):
        kept = []
        for rule in guess:
            primary = str(rule.get("primary", "")).strip() if isinstance(rule, dict) else ""
            if primary and core.Selector.parse(primary).validate(pack.categories):
                notes.append(f"추정 규칙 {rule.get('pattern', '')}: {primary}")
            else:
                kept.append(rule)
        d["guess"] = kept
    return d, notes


def check_files(folder: Path, config: Mapping[str, Any]) -> List[str]:
    """공유 프리셋 검사: 파일 이름이 영문 id 인지, 지금 분류 체계로 그대로(빼는 것 없이) 열리는지. 문제 목록."""
    pack = config_pack(config)
    problems = []
    for path in sorted(Path(folder).glob("*.json")) if Path(folder).is_dir() else ():
        where = f"{Path(folder).name}/{path.name}"
        if not SHARE_ID.fullmatch(path.stem):
            problems.append(f"{where}: 파일 이름은 영문 소문자·숫자·_ - 로 씁니다(예: portrait-basic.json)")
        try:
            core.load_arrangement(read_file(path), pack)
        except (OSError, ValueError) as e:
            problems.append(f"{where}: {e}")
    return problems


def write_file(path: Path, data: Mapping[str, Any]) -> None:
    """같은 폴더의 임시 파일에 쓴 뒤 바꿔 넣는다(쓰다 끊겨도 원래 파일이 남게)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def list_entries(folder: Path) -> List[Tuple[str, Path, str]]:
    """(표시 이름, 경로, 설명) 이름순. 못 읽는 파일은 건너뛴다."""
    out = []
    for path in sorted(Path(folder).glob("*.json")) if Path(folder).is_dir() else ():
        try:
            data = read_file(path)
        except (OSError, ValueError):
            continue
        out.append((str(data.get("label") or path.stem), path, str(data.get("description") or "")))
    return sorted(out, key=lambda item: item[0].lower())


def list_files(folder: Path) -> List[Tuple[str, Path]]:
    """(표시 이름, 경로) 이름순. 못 읽는 파일은 건너뛴다."""
    return [(label, path) for label, path, _ in list_entries(folder)]
