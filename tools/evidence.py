"""Danbooru 증거 수집 — 이름만으로 뜻을 모르는 드문 태그를 분류할 때 배치에 붙일 위키·함의·연관 태그.

캐시(work/evidence/evidence.json, git 제외): {태그: 항목}. 항목의 키는 받은 것만 있다.
    wiki         위키 본문(DText 원문) 또는 None(위키 없음)
    other_names  위키의 다른 이름(일본어 등)
    implies      활성 함의(implication)의 대상 태그
    related      [[태그, 종류(0 general·1 작가·3 작품·4 캐릭터·5 메타), 비율], ...] — 비율은 이 태그가 붙은 게시물 중
                 그 태그도 붙은 비율(Danbooru related_tag 의 frequency)
    fetched      받은 날짜

로그인·키 없이 읽기 전용 공개 API만 쓰고, 요청 사이에 간격을 둔다. 이미 받은 칸은 다시 받지 않으므로
중간에 끊겨도 같은 명령으로 이어서 받는다.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence

API = "https://danbooru.donmai.us"
USER_AGENT = "TagOrganizer-evidence/1.0"
CHUNK = 40              # 위키·함의 묶음 조회 한 번에 넣는 태그 수(URL 길이 여유)
RELATED_LIMIT = 30
SAVE_EVERY = 50         # 연관 태그를 이만큼 받을 때마다 캐시를 저장(끊겨도 이어 받기)
# 어느 그림에나 붙어 뜻을 가르는 데 도움이 안 되는 태그 — 배치 요약에서 뺀다
UBIQUITOUS = frozenset({
    "1girl", "1boy", "solo", "solo_focus", "multiple_girls", "multiple_boys", "2girls", "2boys", "looking_at_viewer",
    "smile", "blush", "open_mouth", "closed_mouth", "long_hair", "short_hair", "medium_hair", "breasts",
    "simple_background", "white_background", "highres", "absurdres", "commentary_request", "commentary",
    "english_commentary", "translated", "hair_between_eyes", "bangs",
})

Getter = Callable[..., Any]   # (path, params, not_found_ok=False) → JSON


class FetchError(Exception):
    pass


def http_get(path: str, params: Mapping[str, Any], not_found_ok: bool = False, retries: int = 3,
             timeout: float = 30) -> Any:
    """GET → JSON. not_found_ok 이면 404 는 None(개별 위키 없음). 그 밖의 실패는 몇 번 다시 시도한 뒤 FetchError."""
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                if not_found_ok:
                    return None
                raise FetchError(f"{path}: 404") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            last = e
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise FetchError(f"{path}: {last}")


def read_cache(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_cache(cache: Mapping[str, Any], path: Path) -> None:
    """임시 파일에 쓴 뒤 바꿔 끼워, 쓰는 중에 끊겨도 캐시가 깨지지 않게 한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _chunks(names: Sequence[str], size: int) -> List[List[str]]:
    return [list(names[i:i + size]) for i in range(0, len(names), size)]


def fetch(
    names: Sequence[str],
    cache: MutableMapping[str, Dict[str, Any]],
    *,
    related: bool = True,
    delay: float = 0.8,
    get: Getter = http_get,
    save: Optional[Callable[[], None]] = None,
    log: Callable[[str], None] = lambda s: None,
) -> Dict[str, int]:
    """캐시에 없는 칸만 받는다. 반환: 받은 수와 실패 수(실패한 칸은 비워 두어 다음 실행에서 다시 받음).

    save 를 주면 묶음마다(연관 태그는 SAVE_EVERY 개마다), 그리고 중간에 끊겨도 받은 데까지 저장한다.
    """
    stats = {"wiki": 0, "implies": 0, "related": 0, "failed": 0}
    try:
        _fetch(list(dict.fromkeys(names)), cache, related, delay, get, save, log, stats)
    finally:
        if save:
            save()
    return stats


def _expect(data: Any, kind: type, path: str) -> Any:
    if not isinstance(data, kind):
        raise FetchError(f"{path}: 예상과 다른 응답 {type(data).__name__}")
    return data


def _set_wiki(entry: Dict[str, Any], w: Optional[Mapping[str, Any]], today: str) -> None:
    live = w if w and not w.get("is_deleted") else None
    entry["wiki"] = live["body"] if live else None
    entry["other_names"] = list(live.get("other_names") or ()) if live else []
    entry["fetched"] = today


def _fetch(names, cache, related, delay, get, save, log, stats) -> None:
    today = _date.today().isoformat()

    # 위키 — title_comma 로 묶어 받는다. 이름에 쉼표가 있으면 묶음 검색이 쪼개므로 하나씩 받는다.
    need = [n for n in names if "wiki" not in cache.get(n, {})]
    for chunk in _chunks([n for n in need if "," not in n], CHUNK):
        try:
            data = _expect(get("/wiki_pages.json", {"search[title_comma]": ",".join(chunk),
                                                    "only": "title,body,other_names,is_deleted", "limit": 100}),
                           list, "/wiki_pages.json")
        except FetchError as e:
            log(f"위키 실패({len(chunk)}개): {e}")
            stats["failed"] += len(chunk)
            continue
        found = {w["title"]: w for w in data}
        for n in chunk:
            _set_wiki(cache.setdefault(n, {}), found.get(n), today)
            stats["wiki"] += 1
        if save:
            save()
        time.sleep(delay)
    for n in [n for n in need if "," in n]:
        try:
            w = get("/wiki_pages/" + urllib.parse.quote(n, safe="") + ".json", {}, not_found_ok=True)
        except FetchError as e:
            log(f"위키 실패 {n}: {e}")
            stats["failed"] += 1
            continue
        _set_wiki(cache.setdefault(n, {}), w, today)
        stats["wiki"] += 1
        time.sleep(delay)

    # 함의 — 활성(active)만. 쉼표 이름은 정확히 일치하는 검색으로 하나씩.
    need = [n for n in names if "implies" not in cache.get(n, {})]
    plain = _chunks([n for n in need if "," not in n], CHUNK)
    for chunk in plain + [[n] for n in need if "," in n]:
        key = "search[antecedent_name]" if len(chunk) == 1 and "," in chunk[0] else "search[antecedent_name_comma]"
        try:
            data = _expect(get("/tag_implications.json", {key: ",".join(chunk), "search[status]": "active",
                                                          "only": "antecedent_name,consequent_name",
                                                          "limit": 1000}),
                           list, "/tag_implications.json")
        except FetchError as e:
            log(f"함의 실패({len(chunk)}개): {e}")
            stats["failed"] += len(chunk)
            continue
        found: Dict[str, List[str]] = {}
        for d in data:
            found.setdefault(d["antecedent_name"], []).append(d["consequent_name"])
        for n in chunk:
            cache.setdefault(n, {})["implies"] = sorted(found.get(n, ()))
            stats["implies"] += 1
        if save:
            save()
        time.sleep(delay)

    # 연관 태그 — 태그마다 한 번
    if not related:
        return
    need = [n for n in names if "related" not in cache.get(n, {})]
    for i, n in enumerate(need, 1):
        try:
            data = _expect(get("/related_tag.json", {"query": n, "limit": RELATED_LIMIT}), dict, "/related_tag.json")
        except FetchError as e:
            log(f"연관 실패 {n}: {e}")
            stats["failed"] += 1
            continue
        rel = []
        for t in data.get("related_tags") or ():
            tag = t.get("tag") or {}
            if tag.get("name") and tag["name"] != n:
                rel.append([tag["name"], tag.get("category", 0), round(float(t.get("frequency", 0)), 3)])
        cache.setdefault(n, {})["related"] = rel
        stats["related"] += 1
        if save and i % SAVE_EVERY == 0:
            save()
            log(f"연관 {i}/{len(need)}")
        time.sleep(delay)


_LINK = re.compile(r"\[\[([^|\]]*)\|([^\]]*)\]\]|\[\[([^\]]*)\]\]")


def clean_wiki(body: Optional[str]) -> str:
    """DText → 한 줄 평문. 링크는 보이는 글자만, 게시물·자산 참조와 서식 태그는 지운다."""
    if not body:
        return ""
    s = _LINK.sub(lambda m: m.group(2) or m.group(3), body)
    s = re.sub(r"\{\{([^}]*)\}\}", r"\1", s)                       # {{태그 검색}} → 태그
    s = re.sub(r'"([^"]*)":\[?https?://[^\s\]]+\]?', r"\1", s)     # "글자":주소 → 글자
    s = re.sub(r"!(post|asset) #\d+:?", "", s)
    s = re.sub(r"\[/?(i|b|u|s|tn|spoiler|expand[^\]]*|quote|code|nodtext)\]", "", s, flags=re.I)
    s = re.sub(r"(?m)^\s*h[1-6]#?[\w-]*\.\s*", "", s)               # h4. 머리글
    return re.sub(r"\s+", " ", s).strip()


def summarize(entry: Optional[Mapping[str, Any]], wiki_chars: int = 600, n_general: int = 15,
              n_series: int = 5, min_freq: float = 0.15) -> Dict[str, str]:
    """배치 CSV 칸으로 줄인 증거. 받지 않은 칸은 '?', 받았는데 없으면 빈 칸."""
    entry = entry or {}
    out = {}
    if "wiki" not in entry:
        out["wiki"] = "?"
    else:
        text = clean_wiki(entry["wiki"])
        out["wiki"] = text[:wiki_chars] + ("…" if len(text) > wiki_chars else "")
    out["other_names"] = "|".join(entry.get("other_names") or ())
    out["implies"] = "?" if "implies" not in entry else "|".join(entry["implies"])
    if "related" not in entry:
        out["related"] = out["related_series"] = "?"
    else:
        rel = entry["related"]
        general = [f"{n}:{f:.2f}" for n, c, f in rel if c == 0 and n not in UBIQUITOUS and f >= min_freq]
        series = [f"{n}:{f:.2f}" for n, c, f in rel if c in (3, 4) and f >= min_freq]
        out["related"] = " ".join(general[:n_general])
        out["related_series"] = " ".join(series[:n_series])
    return out


EVIDENCE_FIELDS = ("wiki", "other_names", "implies", "related", "related_series")
