"""TagOrganizer 명령줄 도구.

    python tagorg.py import <csv> [--date YYYY-MM-DD] [--name 파일명] [--url 주소] [--move] [--dry-run] [--site e621]
    python tagorg.py build
    python tagorg.py stats [--site e621]
    python tagorg.py e621 take-deprecated [--dry-run]   # 폐기 DB 에서 e621 태그와 겹치는 행 → data/overlay/e621.csv
    python tagorg.py e621 batch export --select conflict suspect auto meta hint --size 150 --name F1 [--group]
    python tagorg.py e621 batch apply "work/results/F1-*.csv"   # → e621.csv(status llm), 그다음 e621 accept
    python tagorg.py organize [-i 파일 | -t "프롬프트"] [--preset ID] [--exclude "a, b"] ...
                              [--arrangement 배열.json] [--framing "upper body"] [--view "from behind"] [--gaze "looking back, looking at viewer"] [--pose "lying, on back"] [--guess]
    python tagorg.py lookup <태그> [<태그> ...]
    python tagorg.py batch export --top 500 --size 100 --name M1
    python tagorg.py batch apply work/results/M1-*.csv [--by claude:YYYY-MM-DD]
    python tagorg.py evidence fetch --etc         # 보류(etc) 태그의 Danbooru 위키·함의·연관 태그 받기
    python tagorg.py evidence show <태그> [<태그> ...]
    python tagorg.py batch export --etc --evidence --size 250 --name E1   # 보류 태그를 증거와 함께 배치로
    python tagorg.py batch apply work/results/E1-*.csv --etc             # 확정된 보류 행도 다시 분류
    python tagorg.py api plan work/batches/E2-*.csv --run E2 [--model haiku] [--effort medium] [--count]
    python tagorg.py api submit E2 --max-usd 3    # Claude Message Batches 제출(키: local/anthropic.env)
    python tagorg.py api adopt E2 <batch id>      # 응답을 못 받은 제출이 실제로 만든 배치를 받아들임
    python tagorg.py api status E2 / api collect E2   # 결과 → work/results/E2/, 원장 local/api/ledger.csv
    python tagorg.py api plan --run E2 --stage review [--model sonnet]   # 분류 결과를 검토(고칠 행만) → final/
    python tagorg.py api sample work/batches/E1-*.csv --n 200 --name P1  # 파일럿 표본(보류·분류 비율 유지)
    python tagorg.py review export --top 500 [--sample 샘플.txt] [--out work/review/M1.csv]
    python tagorg.py review apply work/review/M1.csv
    python tagorg.py accept [--top 2000]          # LLM 분류를 검수 없이 확정(accepted)
    python tagorg.py extra add user "cinematic lighting" [--file 목록.csv] [--models nai]   # 확장 DB 에 이름(미분류)
    python tagorg.py extra deprecated [--dry-run]  # Danbooru 폐기 태그 → data/extra/deprecated.csv
    python tagorg.py extra stats                  # 확장 DB 마다 분류·보류·미분류 수
    python tagorg.py export tagcomplete [--out <tagcomplete tags 폴더>] [--dry-run] [--force]   # 자동완성 extra CSV
    python tagorg.py evidence fetch --db deprecated --no-related   # 확장 DB 의 분류할 행의 증거
    python tagorg.py batch export --db deprecated --evidence --group head --size 250 --name D1
    python tagorg.py batch apply "work/results/D1-*.csv" --db deprecated [--etc]
    python tagorg.py accept --db deprecated       # 확장 DB 의 LLM 분류 확정
    python tagorg.py lint                         # 분류 일관성 점검
    python tagorg.py fix <태그> [<태그> ...] [primary=… rating=… regions=… identity=… secondary=… note=…] [--etc]
    python tagorg.py list [--primary etc] [--status S] [--rating R] [--grep 글자] [--limit 50]
    python tagorg.py check                        # overlay 손상 점검(손 수정·엑셀 저장 뒤), 공유 배열 검사
    python tagorg.py sync --to <ComfyUI_BMK_Nodes 폴더> [--dry-run] [--force] [--no-build]
    python tagorg.py fetch                        # 다른 PC 처음: sources.json 의 원본 CSV 받기(sha256 확인)
    python tagorg.py gui                          # 관리 툴(목록·편집·보류·점검·미리보기·작업·통계). PySide6 필요
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bmk_tag_organizer_core as core  # noqa: E402
from gui import arrangement  # noqa: E402  (Qt 없음)
from tools import api_batch, batch, builder, calibration, e621, evidence, extension, extra, fix, importer, lint, review, stats, sync, tagcomplete, tagdb  # noqa: E402,E501

WORK = tagdb.ROOT / "work"
EVIDENCE_CACHE = WORK / "evidence" / "evidence.json"   # Danbooru 증거 캐시(git 제외, 다시 받을 수 있음)
E621_EXPORT_DIR = WORK / "evidence" / "e621"   # e621 DB 내보내기 사본(wiki_pages·tag_implications, git 제외)
SYNC_TARGET = tagdb.ROOT / "local" / "sync.json"   # 마지막 동기화 대상(로컬 전용, git 제외)
TAGCOMPLETE_STATE = tagdb.ROOT / "local" / "tagcomplete.json"   # 마지막 tagcomplete 내보내기 대상·내용 해시


def _cmd_import(args: argparse.Namespace) -> int:
    print(importer.import_source(Path(args.csv), args.date, args.name, args.url, args.move, dry_run=args.dry_run,
                                 site=args.site or ""))
    if args.dry_run:
        print("(미리보기 — 아무것도 쓰지 않음)")
    return 0


def _danbooru() -> tuple:
    """(원본 태그, 팩 source 메타) — build·check 등이 같이 쓴다."""
    path, entry = tagdb.current_source()
    source = tagdb.read_source_csv(path)
    return source, {"file": entry.get("file", path.name), "date": entry.get("date", ""), "rows": len(source)}


def _cmd_build(args: argparse.Namespace) -> int:
    source, meta = _danbooru()
    try:
        pack, warnings = builder.build_pack(
            source, meta, tagdb.read_overlay(), tagdb.read_extra(), tagdb.load_config(), tagdb.load_site())
    except builder.BuildError as e:
        print(f"빌드 실패 — 오류 {len(e.errors)}개:", file=sys.stderr)
        for err in e.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else tagdb.PACK_PATH
    size = builder.write_pack(pack, out)
    for w in warnings:
        print(f"경고: {w}")
    sites = "".join(f" · {s} {len(keys):,}" for s, keys in (pack.get("site") or {}).items())
    print(f"팩 {pack['version']} — 태그 {len(pack['tags']):,}{sites} · 별칭 {len(pack['aliases']):,}"
          f" · {size / 1e6:.1f} MB → {out}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    if args.site:
        site = tagdb.load_site(args.site)
        if site is None:
            print(f"sources.json 에 {args.site} 원본이 없습니다(python tagorg.py import <csv> --site {args.site}).",
                  file=sys.stderr)
            return 1
        source, meta = _danbooru()
        pack, _ = builder.build_pack(source, meta, tagdb.read_overlay(), tagdb.read_extra(), tagdb.load_config(), site)
        print(e621.format_stats(site, pack))
        return 0
    path, _ = tagdb.current_source()
    version = None
    if tagdb.PACK_PATH.exists():
        version = core.read_pack_data(str(tagdb.PACK_PATH)).get("version")
    print(stats.format_stats(tagdb.read_source_csv(path), tagdb.read_overlay(), version))
    return 0


def _read_input(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.input and args.input != "-":
        return Path(args.input).read_text(encoding="utf-8-sig")
    return sys.stdin.buffer.read().decode("utf-8-sig")


def _cmd_organize(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    pack = core.load_pack(args.pack)
    t1 = time.perf_counter()
    try:
        options = core.Options(
            preset=args.preset, exclude=args.exclude or (), max_rating=args.max_rating, rules=args.rules,
            layout=args.layout, input_dialect=args.dialect, output_dialect=args.out_dialect or "",
            line_per_section=not args.one_line, trailing_comma=not args.no_trailing_comma,
            dedupe=not args.no_dedupe, resolve_aliases=not args.keep_aliases, order=args.order,
            arrangement=Path(args.arrangement).read_text(encoding="utf-8-sig") if args.arrangement else None,
            framing=args.framing or "", view=args.view or "", gaze=args.gaze or "", pose=args.pose or "", guess=args.guess,
            dialect_priority=args.priority or "", comments=args.comments, sentences=not args.no_sentences,
        )
        result = core.organize(_read_input(args), pack, options)
    except (OSError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    t2 = time.perf_counter()
    if args.json:
        print(json.dumps(result.report, ensure_ascii=False, indent=2))
        return 0
    print(result.text)
    if result.removed_text and not args.quiet:
        print("\n--- 뺀 태그 ---")
        print(result.removed_text)
    if not args.quiet:
        c = result.report["counts"]
        d = result.report["dialect"]
        auto = (f" · 문법 자동 → {d['input']}" + (" (표기가 섞임)" if d.get("conflict") else "")) if d["auto"] else ""
        print(f"\n--- 팩 {pack.version} · 로딩 {t1 - t0:.2f}s · 처리 {(t2 - t1) * 1000:.1f}ms · "
              f"토큰 {c['total']} · 뺌 {c['removed']} · 미분류 {c['unclassified']} · 알 수 없음 {c['unknown']}{auto}",
              file=sys.stderr)
        for w in result.report["warnings"]:
            print(f"경고: {w['tag']} — {core.warning_text(w['reason'])}", file=sys.stderr)
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    pack = core.load_pack(args.pack)
    for tag in args.tags:
        key = core.normalize_key(tag, pack.kaomoji)
        info, via_alias = pack.resolve(key)
        if info is None:
            print(f"{tag}: 없음")
            continue
        alias = f" (별칭 → {info.key})" if via_alias else ""
        print(f"{tag}{alias}: {info.primary} · rating={info.rating or '-'} · type={info.type} · "
              f"count={info.count:,} · status={info.status or '-'} · regions={'|'.join(info.regions) or '-'}"
              f" · identity={int(info.identity)}" + (f" · db={info.db}" if info.db else "")
              + (f" · site={info.site}" if info.site else ""))
    return 0


def _expand(patterns) -> list:
    paths = []
    for pat in patterns:
        p = Path(pat)
        hits = sorted(p.parent.glob(p.name)) if any(ch in p.name for ch in "*?[") else [p]
        paths += hits
    if not paths:
        raise FileNotFoundError("파일이 없습니다: " + " ".join(patterns))
    return paths


def _cmd_batch_export_extra(args: argparse.Namespace) -> int:
    rows = extra.pending(tagdb.read_extra(), args.db, held=args.etc)
    cache = None
    if args.evidence:
        cache = evidence.read_cache(Path(args.cache) if args.cache else EVIDENCE_CACHE)
        missing = [r["name"] for r in rows
                   if not {"wiki", "implies", "related"} <= set(cache.get(extra.evidence_name(r["name"]), ()))]
        if missing:
            print(f"증거를 다 받지 못한 태그 {len(missing)}개는 빈 칸이 '?' 로 나갑니다(evidence fetch --db 로 받기): "
                  + ", ".join(missing[:10]), file=sys.stderr)
    out_dir = Path(args.out) if args.out else WORK / "batches"
    paths = extra.export_batches(rows, out_dir, args.name, args.size, args.group == "head", cache)
    print(f"{args.db} {'보류(etc)' if args.etc else '미분류'} {len(rows)}개 → 배치 {len(paths)}개")
    for p in paths:
        print(f"  {p}")
    return 0


def _cmd_batch_export(args: argparse.Namespace) -> int:
    if args.db:
        return _cmd_batch_export_extra(args)
    path, _ = tagdb.current_source()
    source, overlay = tagdb.read_source_csv(path), tagdb.read_overlay()
    if args.etc:
        tags = batch.held_etc(source, overlay)
    else:
        tags = batch.pending_general(source, overlay, args.top, args.all)
    if args.group == "head":
        tags = batch.group_by_head(tags)
    cache = None
    if args.evidence:
        cache = evidence.read_cache(Path(args.cache) if args.cache else EVIDENCE_CACHE)
        missing = [t.name for t in tags if not {"wiki", "implies", "related"} <= set(cache.get(t.name, ()))]
        if missing:
            print(f"증거를 다 받지 못한 태그 {len(missing)}개는 빈 칸이 '?' 로 나갑니다(evidence fetch 로 받기): "
                  + ", ".join(missing[:10]), file=sys.stderr)
    out_dir = Path(args.out) if args.out else WORK / "batches"
    paths = batch.export_batches(tags, out_dir, args.name, args.size, cache)
    print(f"{'보류(etc)' if args.etc else '미분류'} {len(tags)}개 → 배치 {len(paths)}개")
    for p in paths:
        print(f"  {p}")
    return 0


def _batch_coverage(results, batch_files, key=lambda name: name) -> list:
    """결과가 원래 배치의 태그를 빠짐없이, 그리고 그것만 담았는지(오류 목록)."""
    expected = set()
    for p in _expand(batch_files):
        with open(p, encoding="utf-8-sig", newline="") as f:
            expected |= {key(row["name"]) for row in csv.DictReader(f)}
    got = {key(r["name"]) for r in results}
    errors = []
    missing, unexpected = sorted(expected - got), sorted(got - expected)
    if missing:
        errors.append(f"배치에 있는데 결과에 없는 태그 {len(missing)}개: {', '.join(missing[:20])}")
    if unexpected:
        errors.append(f"배치에 없는 태그 {len(unexpected)}개: {', '.join(unexpected[:20])}")
    return errors


def _cmd_batch_apply_extra(args: argparse.Namespace) -> int:
    results = batch.read_results(_expand(args.files), optional=extra.EXTRA_RESULT_FIELDS)
    errors = _batch_coverage(results, args.batch, extra.tag_name) if args.batch else []
    rows, n, skipped, apply_errors = extra.apply_results(
        results, tagdb.read_extra(), args.db, tagdb.load_config(), args.by or batch.default_by(),
        status=args.status, reclassify_etc=args.etc)
    errors += apply_errors
    if errors:
        print(f"반영하지 않음 — 오류 {len(errors)}개:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"검증 통과 — {len(results)}행 (반영하지 않음, --dry-run). 반영되면 {n}개, 건너뜀 {len(skipped)}개")
        for s in skipped[:20]:
            print(f"  건너뜀: {s}")
        return 0
    tagdb.write_extra(args.db, rows)
    print(f"{args.db} 반영 {n}개 (status={args.status}), 건너뜀 {len(skipped)}개")
    for s in skipped[:50]:
        print(f"  건너뜀: {s}")
    return 0


def _cmd_batch_apply(args: argparse.Namespace) -> int:
    if args.db:
        return _cmd_batch_apply_extra(args)
    path, _ = tagdb.current_source()
    overlay = tagdb.read_overlay()
    results = batch.read_results(_expand(args.files))
    errors = _batch_coverage(results, args.batch) if args.batch else []
    n, skipped, apply_errors = batch.apply_results(
        results, overlay, tagdb.read_source_csv(path), tagdb.load_config(), args.by or batch.default_by(),
        status=args.status, reclassify_etc=args.etc)
    errors += apply_errors
    if errors:
        print(f"반영하지 않음 — 오류 {len(errors)}개:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"검증 통과 — {len(results)}행 (반영하지 않음, --dry-run). 반영되면 {n}개, 건너뜀 {len(skipped)}개")
        for s in skipped[:20]:
            print(f"  건너뜀: {s}")
        return 0
    tagdb.write_overlay(overlay)
    print(f"overlay 반영 {n}개 (status={args.status}), 건너뜀 {len(skipped)}개")
    for s in skipped:
        print(f"  건너뜀: {s}")
    return 0


def _cmd_review_export(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    sample = review.load_sample(Path(args.sample)) if args.sample else None
    rows = review.review_rows(tagdb.read_source_csv(path), tagdb.read_overlay(), args.top, sample=sample)
    out = Path(args.out) if args.out else WORK / "review" / f"review-top{args.top}.csv"
    review.write_review(rows, out)
    flagged = [r for r in rows if r["flags"]]
    print(f"검수 대상 {len(rows)}개 (표시 있음 {len(flagged)}개) → {out}")
    counts = Counter(f.split(":")[0] if f.startswith("sample") else f for r in rows for f in r["flags"].split())
    for flag, n in counts.most_common():
        print(f"  {flag}: {n}")
    return 0


def _cmd_review_apply(args: argparse.Namespace) -> int:
    overlay = tagdb.read_overlay()
    counts, errors = review.apply_review(Path(args.file), overlay, tagdb.load_config(), args.by)
    if errors:
        print(f"반영하지 않음 — 오류 {len(errors)}개:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    tagdb.write_overlay(overlay)
    print(f"승인 {counts['approved']} · 반려 {counts['rejected']} · 빈 칸 {counts['skipped']}")
    return 0


def _cmd_accept(args: argparse.Namespace) -> int:
    if args.db:
        rows = [r for r in tagdb.read_extra() if r["db"] == args.db]
        done = extra.accept(rows)
        tagdb.write_extra(args.db, rows)
        print(f"확정(accepted) {len(done):,}개 — 확장 DB {args.db} 의 llm·seed 행")
        return 0
    path, _ = tagdb.current_source()
    overlay = tagdb.read_overlay()
    done = review.accept(tagdb.read_source_csv(path), overlay, args.top)
    tagdb.write_overlay(overlay)
    scope = f"general 상위 {args.top:,}개 안" if args.top else "overlay 전체"
    print(f"확정(accepted) {len(done):,}개 — {scope}의 llm·seed 행")
    return 0


def _print_extra_add(db: str, added: int, skipped: list, dry_run: bool) -> None:
    print(f"{db}: 새로 더함 {added:,} · 이미 있음 {len(skipped):,}" + (" (미리보기 — 쓰지 않음)" if dry_run else ""))
    for s in skipped[:15]:
        print(f"  건너뜀: {s}")
    if len(skipped) > 15:
        print(f"  … {len(skipped) - 15:,}개 더")


def _cmd_extra_add(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    names = list(args.names) + [n for f in (args.file or ()) for n in extra.read_name_file(Path(f))]
    if not names:
        print("더할 이름이 없습니다 — 이름이나 --file 을 주세요.", file=sys.stderr)
        return 1
    fields = {k: v for k, v in (("models", args.models), ("note", args.note), ("ref", args.ref)) if v}
    rows, skipped, added = extra.add_names(tagdb.read_extra(), args.db, names, tagdb.read_source_csv(path), fields,
                                           site=tagdb.load_site())
    _print_extra_add(args.db, added, skipped, args.dry_run)
    if not args.dry_run and added:
        tagdb.write_extra(args.db, rows)
        print(f"→ {tagdb.extra_path(args.db)}")
    return 0


def _cmd_extra_deprecated(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    print("Danbooru 폐기 태그 목록을 받습니다…", flush=True)
    tags = extra.fetch_deprecated(delay=args.delay)
    kinds = Counter(t["category"] for t in tags)
    print(f"폐기 태그 {len(tags):,}개 (general {kinds.get(0, 0):,} · 그 밖 {len(tags) - kinds.get(0, 0):,}"
          f" — 종류로 자동 분류) · 게시물 있음 {sum(1 for t in tags if t['post_count']):,}")
    rows, skipped, added = extra.add_deprecated(tags, tagdb.read_extra(), tagdb.read_source_csv(path),
                                                tagdb.load_site())
    _print_extra_add(extra.DEPRECATED, added, skipped, args.dry_run)
    if args.dry_run:
        return 0
    if added:
        tagdb.write_extra(extra.DEPRECATED, rows)
        print(f"→ {tagdb.extra_path(extra.DEPRECATED)}")
    cache_path = Path(args.cache) if args.cache else EVIDENCE_CACHE
    cache = evidence.read_cache(cache_path)
    extra.record_post_counts(tags, cache)
    evidence.write_cache(cache, cache_path)
    print(f"사용량 → 증거 캐시 {cache_path}")
    return 0


def _cmd_extra_stats(args: argparse.Namespace) -> int:
    for line in extra.summary(tagdb.read_extra()):
        print(line)
    return 0


def _cmd_export_tagcomplete(args: argparse.Namespace) -> int:
    state = tagcomplete.read_state(TAGCOMPLETE_STATE)
    out = Path(args.out) if args.out else (Path(state["out"]) if state.get("out") else None)
    if out is None:
        print("내보낼 곳이 없습니다. 처음 한 번 --out <파일 또는 tagcomplete 의 tags 폴더> 를 주세요"
              " (예: ...\\custom_nodes\\comfy-ex-tagcomplete\\tags\\extra-quality-tags.csv).", file=sys.stderr)
        return 1
    if out.is_dir():
        out = out / "extra-quality-tags.csv"
    source, meta = _danbooru()
    rows_all = tagdb.read_extra()
    site = tagdb.load_site()
    try:
        pack, _ = builder.build_pack(source, meta, tagdb.read_overlay(), rows_all, tagdb.load_config(), site)
    except builder.BuildError as e:
        print(f"빌드 검사에서 멈춤 — 오류 {len(e.errors)}개(python tagorg.py check):", file=sys.stderr)
        for err in e.errors[:20]:
            print(f"  - {err}", file=sys.stderr)
        return 1
    rows = tagcomplete.rows_for(pack, rows_all, args.db, args.include_etc, args.include_auto, site)
    data = tagcomplete.render(rows)
    kinds = Counter(note.rsplit(" · ", 1)[-1] for _, _, note, _ in rows)
    print(f"{len(rows):,}개 ({' · '.join(f'{k} {n:,}' for k, n in kinds.items())}) → {out}")
    if args.dry_run:
        for line in data.decode("utf-8").splitlines()[:10]:
            print(f"  {line}")
        print("(미리보기 — 쓰지 않음)")
        return 0
    try:
        result, backup = tagcomplete.write(out, data, TAGCOMPLETE_STATE, force=args.force)
    except FileExistsError as e:
        print(f"쓰지 않음: {e}", file=sys.stderr)
        return 1
    label = {"unchanged": "변경 없음", "created": "새로 만듦", "updated": "갱신",
             "replaced": "덮어씀(--force)"}[result]
    print(f"{label}" + (f" — 이전 파일은 {backup}" if backup else ""))
    print("자동완성에 반영하려면 ComfyUI 에서 tagcomplete 의 extra 파일을 다시 고르거나 새로 고침합니다.")
    return 0


def _cmd_calib_make(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    overlay = tagdb.read_overlay()
    tags = calibration.pick(tagdb.read_source_csv(path), overlay, args.n, args.salt)
    out = Path(args.out) if args.out else WORK / "calibration"
    calibration.write_calibration(tags, overlay, out)
    mains = Counter(overlay[t.name]["primary"].partition("/")[0] for t in tags)
    print(f"보정 세트 {len(tags)}개 → {out / 'CAL.csv'} (선례: {out / 'precedents.csv'})")
    print("  " + " · ".join(f"{m} {c}" for m, c in mains.most_common()))
    return 0


def _calib_results(patterns) -> dict:
    """파일 하나 = 작업자 하나. 폴더를 주면 그 안의 CSV 전부가 작업자 하나(api collect 의 run 결과 폴더)."""
    results = {}
    for p in _expand(patterns):
        if p.is_dir():
            files = sorted(p.glob("*.csv"))
            if not files:
                raise FileNotFoundError(f"CSV 가 없는 폴더입니다: {p}")
            results[p.name if p.name != "final" else f"{p.parent.name}/final"] = batch.read_results(files)
        else:
            results[p.stem] = batch.read_results([p])
    return results


def _cmd_calib_score(args: argparse.Namespace) -> int:
    overlay = tagdb.read_overlay()
    results = _calib_results(args.files)
    report = calibration.score(results, overlay)
    print(calibration.format_report(report, args.main_min, args.primary_min, args.held_min))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if calibration.passed(report, args.main_min, args.primary_min, args.held_min) else 2


def _api_client():
    return api_batch.make_client(api_batch.read_api_key())


def _cmd_api_plan(args: argparse.Namespace) -> int:
    if args.chunk < 1 or args.max_tokens < 1:
        print("--chunk·--max-tokens 는 1 이상이어야 합니다.", file=sys.stderr)
        return 1
    api_batch.check_run_name(args.run)
    api_batch.check_unsubmitted(args.run, args.stage)
    overlay, config = tagdb.read_overlay(), tagdb.load_config()
    guide = api_batch.GUIDE.read_text(encoding="utf-8")
    if args.stage == "review":
        if args.files:
            print("검토 계획은 배치 파일을 받지 않습니다(분류 단계의 요청 묶음을 따름).", file=sys.stderr)
            return 1
        plan, skipped = api_batch.plan_review(args.run, overlay, config, guide,
                                              api_batch.resolve_model(args.model or "sonnet"), args.effort,
                                              args.max_tokens)
        if skipped:
            print(f"분류 결과가 없어 검토에서 뺀 태그 {len(skipped)}개(retry 로 따로): {', '.join(skipped[:10])}",
                  file=sys.stderr)
    else:
        if not args.files:
            print("배치 CSV 를 주세요(예: work/batches/E2-*.csv).", file=sys.stderr)
            return 1
        plan = api_batch.plan_classify(args.run, _expand(args.files), overlay, config, guide,
                                       api_batch.resolve_model(args.model or "haiku"), args.effort,
                                       args.max_tokens, args.chunk)
    if args.count:
        plan = api_batch.count_plan(plan, _api_client())
    api_batch.write_plan(plan)
    print(api_batch.format_plan(plan))
    return 0


def _cmd_api_submit(args: argparse.Namespace) -> int:
    m = api_batch.submit(args.run, args.stage, _api_client, args.max_usd, args.resubmit)
    print(f"제출했습니다: {m['batch_id']} · 요청 {m['requests']}개 · 예상 ${m['estimate_usd']:.2f}"
          f" · 최대 ${m['max_cost_usd']:.2f} (상한 ${m['max_usd']:.2f})")
    print(f"결과 보관 기한 전에 받으세요(만료 {m['expires_at'] or '?'}): "
          f"python tagorg.py api status {args.run} --stage {args.stage}")
    return 0


def _cmd_api_adopt(args: argparse.Namespace) -> int:
    m = api_batch.adopt(args.run, args.stage, _api_client(), args.batch_id)
    print(f"받아들였습니다: {m['batch_id']} · 요청 {m['requests']}개 · {m['processing_status']}")
    return 0


def _cmd_api_status(args: argparse.Namespace) -> int:
    s = api_batch.status(args.run, args.stage, _api_client())
    c = s["counts"]
    print(f"{s['batch_id']}: {s['processing_status']} · 진행 {c['processing']} · 성공 {c['succeeded']}"
          f" · 오류 {c['errored']} · 취소 {c['canceled']} · 만료 {c['expired']}")
    if s["processing_status"] == "ended":
        print(f"끝남 {s['ended_at']} — python tagorg.py api collect {args.run} --stage {args.stage}")
    return 0


def _cmd_api_collect(args: argparse.Namespace) -> int:
    s = api_batch.collect(args.run, args.stage, _api_client(), tagdb.load_config())
    print(api_batch.format_collect(args.run, args.stage, s))
    if not s["ended"]:
        return 2
    return 2 if s.get("failed") or s.get("unreviewed") else 0


def _cmd_api_sample(args: argparse.Namespace) -> int:
    if args.n < 1 or args.size < 1:
        print("--n·--size 는 1 이상이어야 합니다.", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else WORK / "batches"
    paths = api_batch.sample(_expand(args.files), tagdb.read_overlay(), args.n, out, args.name, args.size, args.salt)
    print(f"표본 → 배치 {len(paths)}개")
    for p in paths:
        print(f"  {p}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    overlay = tagdb.read_overlay()
    print(lint.format_issues(lint.pair_issues(overlay), overlay))
    return 0


def _extra_fields_text(row) -> str:
    parts = [f"{k}={row[k]}" for k in fix.EXTRA_EDITABLE if row.get(k)]
    return (" · " + " · ".join(parts)) if parts else ""


def _cmd_fix(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    source = tagdb.read_source_csv(path)
    overlay = tagdb.read_overlay()
    rows_all = tagdb.read_extra()
    names, updates = fix.split_args(args.items)
    if not names:
        print("고칠 태그 이름을 주세요.", file=sys.stderr)
        return 1
    # 팩과 같은 순서로 찾는다: 이름(Danbooru → 확장 DB → e621 → 폐기 DB) → 별칭(Danbooru → 확장 DB → e621 → 폐기 DB).
    # peace sign 은 v 의 별칭이지만 model 의 행이 가리고, anthro 는 furry 의 별칭이지만 e621 이름이 가린다
    by_name = {t.name: t for t in source}
    aliases = fix.alias_index(source)
    site = tagdb.load_site()
    site_tags = [] if site is None else [t for t in site.tags if t.type in builder.E621_TYPES]
    site_by = {t.name: t for t in site_tags if t.name not in by_name}
    # 두 번째 원본이 없으면 빌드처럼 확장 DB 를 읽은 순서 그대로(폐기 DB 를 뒤로 미루지 않음)
    late = [r for r in rows_all if site is not None and r.get("db") in builder.LATE_DBS]
    front = [r for r in rows_all if site is None or r.get("db") not in builder.LATE_DBS]
    site_aliases = fix.alias_index(site_tags)
    src_names, ext_rows, originals, site_names = [], {}, {}, []
    for raw in names:
        kind, found = fix.locate(raw, by_name, aliases, overlay, front, late, site_by, site_aliases, extra.lookup)
        if kind == "site":
            site_names.append(found)
            continue
        if kind == "src":
            src_names.append(found)
            continue
        row = found
        if row["name"] in originals and originals[row["name"]] is not row:
            print(f"확장 DB 에 이름이 같은 행이 둘 있어 고치지 않습니다: {row['name']} — python tagorg.py check",
                  file=sys.stderr)
            return 1
        else:
            ext_rows[row["name"]] = dict(row)
            originals[row["name"]] = row
    if not updates and not args.etc:
        for raw in src_names:
            name, hint = fix.resolve_name(raw, by_name, aliases)
            name = name or (raw if raw in overlay else None)
            if name is None:
                print(f"{raw}: 원본·확장 DB 에 없는 태그")
                continue
            count = f"{by_name[name].count:,}회" if name in by_name else "원본에 없음"
            print(f"{name} ({count}){' · ' + hint if hint else ''}: {fix.format_row(overlay.get(name, {}))}")
        for name, row in ext_rows.items():
            print(f"{name} (확장 DB {row['db']}): {fix.format_row(row)}{_extra_fields_text(row)}")
        for name in dict.fromkeys(site_names):
            row = site.overlay.get(name)
            shown = fix.format_row(row) if row else f"({site.id}.csv 행 없음 — 종류 자동 분류·자동 same_as·보류)"
            print(f"{name} ({site.id} {site_by[name].count:,}회): {shown}")
        return 0
    by = args.by or f"user:{time.strftime('%Y-%m-%d')}"
    config = tagdb.load_config()
    changes, errors, notes = ([], [], []) if not src_names else fix.apply_fix(
        overlay, src_names, updates, source, config, by, etc=args.etc)
    if ext_rows:
        ext_changes, ext_errors, ext_notes = fix.apply_fix_rows(ext_rows, list(ext_rows), updates, config, by,
                                                                 etc=args.etc)
        changes += ext_changes
        errors += ext_errors
        notes += ext_notes
    site_changes = []
    if site_names:
        site_changes, site_errors, site_notes = fix.apply_fix_site(site.overlay, site_names, updates, config, by,
                                                                   site.id, etc=args.etc)
        errors += site_errors
        notes += site_notes
    for n in notes:
        print(f"참고: {n}")
    if errors:
        print(f"고치지 않음 — 오류 {len(errors)}개:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    site_done = {name for name, _, _ in site_changes}
    for name, before, after in changes + site_changes:
        where = f" (확장 DB {after['db']})" if after.get("db") else (f" ({site.id}.csv)" if name in site_done else "")
        print(f"{name}{where}\n  전: {fix.format_row(before)}{_extra_fields_text(before)}"
              f"\n  후: {fix.format_row(after)}{_extra_fields_text(after)}")
    if not changes and not site_changes:
        return 0
    if args.dry_run:
        print(f"{len(changes) + len(site_changes)}개 — 쓰지 않음(--dry-run)")
        return 0
    if any(not after.get("db") for _, _, after in changes):
        tagdb.write_overlay(overlay)
    replaced = [(originals[name], after) for name, _, after in changes if after.get("db")]
    for db in dict.fromkeys(after["db"] for _, after in replaced):
        tagdb.write_extra(db, extra.db_rows(rows_all, db, replaced))
    if site_changes:
        tagdb.write_overlay(site.overlay, tagdb.E621_OVERLAY, fields=tagdb.SITE_FIELDS)
    print(f"{len(changes) + len(site_changes)}개 고침(status=reviewed). 반영: python tagorg.py build"
          " (노드 팩은 python tagorg.py sync)")
    return 0


def _evidence_names(args: argparse.Namespace, overlay) -> list:
    names = [n.strip().replace(" ", "_") for n in args.tags]
    if getattr(args, "db", None):
        # 확장 DB 의 분류할 행: 미분류, --etc 면 보류(reviewed 제외)
        names += [extra.evidence_name(r["name"]) for r in extra.pending(tagdb.read_extra(), args.db, held=args.etc)]
    elif getattr(args, "etc", False):
        # 원본 CSV 없이도 돌게 overlay 만 본다(보류 행은 모두 general)
        names += sorted(n for n, r in overlay.items() if r.get("primary") == "etc" and r.get("status") != "reviewed")
    return list(dict.fromkeys(n for n in names if n))


def _cmd_evidence_fetch(args: argparse.Namespace) -> int:
    names = _evidence_names(args, tagdb.read_overlay())
    if not names:
        print("받을 태그가 없습니다 — 태그 이름이나 --etc 를 주세요.", file=sys.stderr)
        return 1
    cache_path = Path(args.cache) if args.cache else EVIDENCE_CACHE
    cache = evidence.read_cache(cache_path)
    if args.refresh:
        for n in names:
            cache.pop(n, None)
    print(f"{len(names):,}개 — 위키·함의는 {evidence.CHUNK}개씩 묶어, 연관 태그는 하나씩 받습니다 → {cache_path}",
          flush=True)
    result = evidence.fetch(names, cache, related=not args.no_related, delay=args.delay,
                            save=lambda: evidence.write_cache(cache, cache_path),
                            log=lambda m: print(m, flush=True))
    have = {k: sum(1 for n in names if k in cache.get(n, {})) for k in ("wiki", "implies", "related")}
    wiki = sum(1 for n in names if cache.get(n, {}).get("wiki"))
    print(f"이번에 받음: 위키 {result['wiki']:,} · 함의 {result['implies']:,} · 연관 {result['related']:,}"
          f" · 실패 {result['failed']:,}")
    print(f"캐시: 위키 {have['wiki']:,}(본문 있음 {wiki:,}) · 함의 {have['implies']:,} · 연관 {have['related']:,}"
          f" / {len(names):,}")
    return 2 if result["failed"] else 0


def _cmd_evidence_show(args: argparse.Namespace) -> int:
    cache_path = Path(args.cache) if args.cache else EVIDENCE_CACHE
    cache = evidence.read_cache(cache_path)
    names = _evidence_names(args, {})
    missing = [n for n in names if not {"wiki", "implies", "related"} <= set(cache.get(n, ()))]
    if missing and not args.offline:
        evidence.fetch(missing, cache, delay=0.5)
        evidence.write_cache(cache, cache_path)
    for n in names:
        ev = evidence.summarize(cache.get(n), wiki_chars=args.chars)
        print(f"■ {n}")
        for k in evidence.EVIDENCE_FIELDS:
            if ev[k]:
                print(f"  {k}: {ev[k]}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    rows = fix.find_rows(tagdb.read_source_csv(path), tagdb.read_overlay(), args.primary, args.status,
                         args.rating, args.grep)
    shown = rows if args.limit <= 0 else rows[:args.limit]
    for count, row in shown:
        print(f"{count:>9,}  {row['name']}  {fix.format_row(row)}")
    more = f" (위 {len(shown):,}개만, 전부는 --limit 0)" if len(shown) < len(rows) else ""
    print(f"— {len(rows):,}개{more}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    path, _ = tagdb.current_source()
    source = tagdb.read_source_csv(path)
    overlay = tagdb.read_overlay()
    config = tagdb.load_config()
    errors, info = fix.check_overlay(overlay, source, config, fix.committed_overlay())
    errors += arrangement.check_files(tagdb.SHARED_ARRANGEMENTS, config)    # 공유 배열이 지금 분류 체계로 열리는지
    extra = tagdb.read_extra()
    site = tagdb.load_site()
    extra_errors, extra_warnings = builder.check_extra(source, overlay, extra, config, site)
    errors += extra_errors
    for line in info:
        print(line)
    for w in extra_warnings:
        print(f"경고: {w}")
    if errors:
        print(f"이상 {len(errors)}건:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    site_note = f" · {site.id}.csv {len(site.overlay):,}행" if site is not None else ""
    print(f"이상 없음 — overlay {len(overlay):,}행 · 확장 DB {len(extra):,}행{site_note}")
    return 0


def _cmd_e621_take(args: argparse.Namespace) -> int:
    # 읽기 전에 해시를 잡는다: 읽는 사이 관리 툴 등이 저장하면 쓰기가 OverlayChanged 로 멈춘다
    expected = {db: tagdb.sha256_file(tagdb.extra_path(db)) for db in builder.LATE_DBS
                if tagdb.extra_path(db).exists()}
    site_hash = tagdb.sha256_file(tagdb.E621_OVERLAY) if tagdb.E621_OVERLAY.exists() else None
    site = tagdb.load_site()
    if site is None:
        print("sources.json 에 e621 원본이 없습니다(python tagorg.py import <csv> --site e621).", file=sys.stderr)
        return 1
    source, _ = _danbooru()
    rows_all = tagdb.read_extra()
    try:
        rows, kept, lines = e621.take_deprecated(source, tagdb.read_overlay(), rows_all, site, tagdb.load_config())
    except builder.BuildError as e:
        print(f"빌드 검사에서 멈춤 — 오류 {len(e.errors)}개(python tagorg.py check):", file=sys.stderr)
        for err in e.errors[:20]:
            print(f"  - {err}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    removed = sum(1 for r in rows_all if r.get("db") in builder.LATE_DBS) - sum(len(v) for v in kept.values())
    if args.dry_run:
        print("(미리보기 — 쓰지 않음)")
        return 0
    if not removed:
        print("옮길 행이 없습니다.")
        return 0
    # e621.csv 를 먼저 쓴다: 둘째(폐기 DB)가 실패해도 분류는 남고, 다시 실행하면 이미 있는 행으로 넘어간다
    tagdb.write_overlay(rows, tagdb.E621_OVERLAY, expected=site_hash, fields=tagdb.SITE_FIELDS)
    for db, db_rows in kept.items():
        tagdb.write_extra(db, db_rows, expected=expected.get(db))
    print(f"→ {tagdb.E621_OVERLAY} ({len(rows):,}행), 폐기 DB 에서 {removed:,}행 지움")
    return 0


def _site_or_fail():
    site = tagdb.load_site()
    if site is None:
        print("sources.json 에 e621 원본이 없습니다(python tagorg.py import <csv> --site e621).", file=sys.stderr)
    return site


def _cmd_e621_export(args: argparse.Namespace) -> int:
    site = _site_or_fail()
    if site is None:
        return 1
    source, _ = _danbooru()
    rows = e621.select(source, tagdb.read_overlay(), tagdb.read_extra(), site, tagdb.load_config(),
                       args.select, args.top)
    if not rows:
        print("고를 태그가 없습니다.")
        return 0
    ev = None
    if not args.no_evidence:
        wiki, impl = e621.export_files(Path(args.evidence_dir) if args.evidence_dir else E621_EXPORT_DIR)
        if wiki is None and impl is None:
            print(f"경고: e621 DB 내보내기(wiki_pages·tag_implications)가 없어 증거 칸이 '?' 입니다 — "
                  f"{E621_EXPORT_DIR} 에 두세요(docs/e621.md)", file=sys.stderr)
        ev = e621.load_evidence({r["name"] for r in rows}, wiki, impl, {t.name: t.count for t in site.tags})
        with_wiki = sum(1 for r in rows if (ev.get(r["name"]) or {}).get("wiki"))
        print(f"증거: 위키 {with_wiki:,}/{len(rows):,}"
              + "".join(f" · {p.name}" for p in (wiki, impl) if p is not None))
    out = Path(args.out) if args.out else WORK / "batches"
    paths = e621.export_batches(rows, out, args.name, args.size, ev, group=args.group)
    kinds = Counter(r["kind"] for r in rows)
    print(f"{len(rows):,}개 ({' · '.join(f'{k} {n:,}' for k, n in kinds.items())}) → 배치 {len(paths)}개: "
          f"{paths[0].name} … {paths[-1].name}")
    return 0


def _cmd_e621_apply(args: argparse.Namespace) -> int:
    # 읽기 전에 해시를 잡는다: 읽는 사이 다른 곳에서 저장하면 쓰기가 OverlayChanged 로 멈춘다
    expected = tagdb.sha256_file(tagdb.E621_OVERLAY) if tagdb.E621_OVERLAY.exists() else None
    site = _site_or_fail()
    if site is None:
        return 1
    source, _ = _danbooru()
    results = batch.read_results(_expand(args.files), optional=e621.SITE_RESULT_EXTRA)
    rows, n, skipped, errors = e621.apply_results(results, source, tagdb.read_overlay(), tagdb.read_extra(), site,
                                                  tagdb.load_config(), args.by or batch.default_by())
    for s in skipped[:20]:
        print(f"건너뜀: {s}")
    if errors:
        print(f"반영하지 않음 — 오류 {len(errors)}개:", file=sys.stderr)
        for e in errors[:50]:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"{n:,}개 반영 가능 — 쓰지 않음(--dry-run)")
        return 0
    tagdb.write_overlay(rows, tagdb.E621_OVERLAY, expected=expected, fields=tagdb.SITE_FIELDS)
    print(f"{n:,}개 반영(status=llm) → {tagdb.E621_OVERLAY}. 확정: python tagorg.py e621 accept")
    return 0


def _cmd_e621_accept(args: argparse.Namespace) -> int:
    expected = tagdb.sha256_file(tagdb.E621_OVERLAY) if tagdb.E621_OVERLAY.exists() else None
    rows = tagdb.read_overlay(tagdb.E621_OVERLAY, tagdb.SITE_FIELDS)
    done = e621.accept(rows)
    if done:
        tagdb.write_overlay(rows, tagdb.E621_OVERLAY, expected=expected, fields=tagdb.SITE_FIELDS)
    print(f"{len(done):,}개 확정(accepted)")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    if args.to:
        target = Path(args.to)
    elif SYNC_TARGET.exists():
        target = Path(json.loads(SYNC_TARGET.read_text(encoding="utf-8"))["nodes_dir"])
    else:
        print("대상이 없습니다. --to <ComfyUI_BMK_Nodes 폴더> 를 지정하세요.", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        pack_path = Path(args.pack) if args.pack else tagdb.PACK_PATH
        if not args.no_build and not args.pack:
            # 미리보기는 build/ 도 바꾸지 않도록 임시 폴더에 빌드한다
            if args.dry_run:
                pack_path = Path(tmp) / "tagpack.json.gz"
            code = _cmd_build(argparse.Namespace(out=str(pack_path) if args.dry_run else None))
            if code:
                return code
        try:
            actions = sync.sync(target, tagdb.ROOT / sync.CORE_REL, pack_path, repo=tagdb.ROOT,
                                force=args.force, dry_run=args.dry_run)
        except sync.SyncError as e:
            print(f"동기화 중단: {e}", file=sys.stderr)
            return 1
    label = {"create": "새로 만듦", "update": "갱신", "unchanged": "변경 없음", "conflict": "덮어씀(--force)"}
    print(("미리보기 — 아무것도 쓰지 않음: " if args.dry_run else "동기화: ") + str(target))
    for a in actions:
        print(f"  {label[a.state]:<12} {a.rel}")
    if not args.dry_run:
        SYNC_TARGET.parent.mkdir(parents=True, exist_ok=True)
        SYNC_TARGET.write_text(json.dumps({"nodes_dir": str(target.resolve())}, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        if any(a.state != "unchanged" for a in actions):
            print(f"노드 팩에서 커밋할 파일: {sync.CORE_REL}, {sync.DATA_REL}/, {sync.EDITOR_REL}/")
    return 0


def _cmd_extension(args: argparse.Namespace) -> int:
    pack_path = Path(args.pack) if args.pack else tagdb.PACK_PATH
    if not args.no_build and not args.pack:
        code = _cmd_build(argparse.Namespace(out=None))
        if code:
            return code
    local = None if args.no_local else tagdb.ROOT / "local" / "arrangements"
    warnings = extension.write_data(pack_path, local=local)
    for w in warnings:
        print(f"경고: {w}", file=sys.stderr)
    print(f"확장 자료: {extension.EXTENSION / extension.DATA_REL}")
    if args.zip:
        out = Path(args.zip)
        print(f"설치용 zip: {out} (파일 {extension.make_zip(out)}개)")
    print("크롬 chrome://extensions → 개발자 모드 → '압축해제된 확장 프로그램 로드' → extension 폴더")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    """Danbooru 와 두 번째 원본(sources.json 에 있으면) 둘 다 받는다."""
    try:
        found = [tagdb.current_source()]
    except tagdb.SourceMissing as e:
        found = [(e.path, e.entry)]
    except FileNotFoundError as e:       # sources.json 에 current 가 없음
        print(e, file=sys.stderr)
        return 1
    try:
        site = tagdb.site_source()
    except tagdb.SourceMissing as e:
        site = (e.path, e.entry)
    if site is not None:
        found.append(site)
    code = 0
    for path, entry in found:
        if path.exists():
            if not entry.get("sha256") or tagdb.sha256_file(path) == entry["sha256"].lower():
                print(f"이미 있습니다: {path}")
                continue
            print(f"sha256 이 sources.json 과 달라 다시 받습니다: {path}")
        print(f"받는 중: {tagdb.download_url(entry.get('url') or '')}")
        try:
            tagdb.fetch_source(entry, path)
        except (OSError, ValueError) as e:
            print(f"받지 못했습니다: {e}", file=sys.stderr)
            code = 1
            continue
        print(f"받았습니다(sha256 확인): {path}")
    return code


def _cmd_gui(args: argparse.Namespace) -> int:
    from gui import bootstrap
    code = bootstrap.ensure(dialog=False)
    if code is not None:
        return code
    from gui.app import main as gui_main
    return gui_main([sys.argv[0]])


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="tagorg", description="Danbooru 프롬프트 태그 정리기")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="원본 CSV를 가져오고 overlay 에 델타 반영")
    s.add_argument("csv")
    s.add_argument("--date", help="원본 날짜 YYYY-MM-DD (파일 이름에 없을 때)")
    s.add_argument("--name", help="data/source 에 저장할 파일 이름")
    s.add_argument("--url", default="", help="다시 받을 수 있는 주소(sources.json 에 기록)")
    s.add_argument("--move", action="store_true", help="복사 대신 이동")
    s.add_argument("--dry-run", action="store_true", help="보고서만 보여 주고 아무것도 쓰지 않음")
    s.add_argument("--site", choices=(tagdb.E621,), help="두 번째 원본으로 가져오기(분류 파일 data/overlay/e621.csv)")
    s.set_defaults(func=_cmd_import)

    s = sub.add_parser("build", help="런타임 팩 빌드")
    s.add_argument("--out", help=f"출력 경로 (기본 {tagdb.PACK_PATH.relative_to(tagdb.ROOT)})")
    s.set_defaults(func=_cmd_build)

    s = sub.add_parser("stats", help="분류 진행 현황")
    s.add_argument("--site", choices=(tagdb.E621,), help="두 번째 원본의 general 사용량 커버리지")
    s.set_defaults(func=_cmd_stats)

    s = sub.add_parser("e621", help="두 번째 원본(e621) 관리")
    e_sub = s.add_subparsers(dest="e621_cmd", required=True)
    t = e_sub.add_parser("take-deprecated", help="폐기 DB 에서 e621 태그와 겹치는 행을 e621.csv 로 옮김(conflict)")
    t.add_argument("--dry-run", action="store_true", help="보고만 하고 쓰지 않음")
    t.set_defaults(func=_cmd_e621_take)
    t = e_sub.add_parser("batch", help="e621 태그 배치 분류(내보내기·반영)")
    b_sub = t.add_subparsers(dest="e621_batch_cmd", required=True)
    x = b_sub.add_parser("export", help="분류할 e621 태그를 증거(e621 위키·함의)와 함께 배치 CSV 로")
    x.add_argument("--select", nargs="+", default=["conflict", "suspect", "auto", "meta", "hint"],
                   help="고를 종류: " + " · ".join(f"{k}({v})" for k, v in e621.KINDS.items()))
    x.add_argument("--top", type=int, default=0, help="top 종류: general 사용량 상위 N 안의 미분류")
    x.add_argument("--size", type=int, default=150)
    x.add_argument("--name", required=True, help="배치 이름(파일 <이름>-01.csv …)")
    x.add_argument("--out", help="배치 폴더 (기본 work/batches)")
    x.add_argument("--group", action="store_true", help="종류 → 같은 계열(끝 단어) 순으로 묶음")
    x.add_argument("--evidence-dir", help=f"e621 DB 내보내기 폴더 (기본 {E621_EXPORT_DIR.relative_to(tagdb.ROOT)})")
    x.add_argument("--no-evidence", action="store_true", help="증거 칸 없이(모두 '?')")
    x.set_defaults(func=_cmd_e621_export)
    x = b_sub.add_parser("apply", help="결과 CSV(분류 칸 또는 same_as)를 e621.csv 에 반영(status=llm)")
    x.add_argument("files", nargs="+")
    x.add_argument("--by", help="분류한 주체 (기본 claude:오늘)")
    x.add_argument("--dry-run", action="store_true", help="검사만 하고 쓰지 않음")
    x.set_defaults(func=_cmd_e621_apply)
    t = e_sub.add_parser("accept", help="e621.csv 의 LLM 분류(llm)를 확정(accepted)")
    t.set_defaults(func=_cmd_e621_accept)

    s = sub.add_parser("organize", help="프롬프트 정리")
    s.add_argument("-i", "--input", help="입력 파일 (생략하거나 - 이면 표준 입력)")
    s.add_argument("-t", "--text", help="입력 문자열")
    s.add_argument("--preset", default="sort_only")
    s.add_argument("--exclude", help='추가로 뺄 선택자, 예: "subject/character, body/hair"')
    s.add_argument("--max-rating", default="e", choices=core.RATINGS)
    s.add_argument("--rules", default="remove", choices=core.RULE_MODES)
    s.add_argument("--layout", default="default")
    s.add_argument("--dialect", default="comfyui", choices=core.INPUT_DIALECTS,
                   help="입력 문법(auto 면 프롬프트의 표기로 알아냄)")
    s.add_argument("--priority", help='auto 의 우선순위, 예: "nai, comfyui, anima"(기본) — 표기가 섞였거나 없을 때')
    s.add_argument("--out-dialect", choices=core.DIALECTS)
    s.add_argument("--comments", default="remove", choices=core.COMMENT_MODES,
                   help="// 주석: remove(뺌, 기본) / keep(구간 맨 위에 한 줄씩)")
    s.add_argument("--no-sentences", action="store_true", help="문장 끝 마침표를 쉼표처럼 나누지 않음")
    s.add_argument("--order", default="input", choices=core.ORDERS)
    s.add_argument("--one-line", action="store_true", help="섹션별 줄바꿈 없이 한 줄로")
    s.add_argument("--no-trailing-comma", action="store_true")
    s.add_argument("--no-dedupe", action="store_true")
    s.add_argument("--keep-aliases", action="store_true", help="별칭을 대표 이름으로 바꾸지 않음")
    s.add_argument("--arrangement", help="배열 세팅 JSON 파일(관리 툴에서 저장·내보낸 것). 주면 --layout 대신 씀")
    s.add_argument("--framing", help='크롭 덮어쓰기, 예: "upper body" (프롬프트의 크롭 태그를 바꿈)')
    s.add_argument("--view", help='시점 덮어쓰기, 예: "from behind"')
    s.add_argument("--gaze", help='시선 덮어쓰기(쉼표로 여럿), 예: "looking back, looking at viewer"')
    s.add_argument("--pose", help='포즈 덮어쓰기(쉼표로 여럿), 예: "lying, on back" (장소 태그는 남김)')
    s.add_argument("--guess", action="store_true", help="팩에 없는 토큰의 분류를 패턴·끝말로 추정")
    s.add_argument("--json", action="store_true", help="보고서(JSON)만 출력")
    s.add_argument("-q", "--quiet", action="store_true")
    s.add_argument("--pack", help="팩 경로 (기본: build/tagpack.json.gz)")
    s.set_defaults(func=_cmd_organize)

    s = sub.add_parser("lookup", help="태그 분류 조회")
    s.add_argument("tags", nargs="+")
    s.add_argument("--pack")
    s.set_defaults(func=_cmd_lookup)

    s = sub.add_parser("batch", help="LLM 배치 분류 (내보내기·반영)")
    bsub = s.add_subparsers(dest="batch_cmd", required=True)
    b = bsub.add_parser("export", help="general 상위 N개 중 미분류 태그를 배치 파일로")
    b.add_argument("--top", type=int, default=500)
    b.add_argument("--size", type=int, default=100)
    b.add_argument("--name", default="batch")
    b.add_argument("--all", action="store_true", help="이미 분류된 태그도 포함(reviewed 제외)")
    b.add_argument("--out", help="출력 폴더 (기본 work/batches)")
    b.add_argument("--group", choices=("count", "head"), default="count",
                   help="배치 순서: count=사용량 순, head=같은 계열(기본 명사·한정어)끼리 모음")
    b.add_argument("--etc", action="store_true", help="미분류 대신 보류(etc) 태그를 내보냄(reviewed 제외, --top 무시)")
    b.add_argument("--evidence", action="store_true",
                   help="Danbooru 증거 칸(wiki·other_names·implies·related·related_series)을 붙임 — evidence fetch 먼저")
    b.add_argument("--cache", help="증거 캐시 경로 (기본 work/evidence/evidence.json)")
    b.add_argument("--db", help="확장 DB(data/extra/<DB>.csv)의 미분류 행(--etc 면 보류 행)을 내보냄 — --top·--all 무시")
    b.set_defaults(func=_cmd_batch_export)
    b = bsub.add_parser("apply", help="분류 결과 CSV를 overlay 에 반영")
    b.add_argument("files", nargs="+")
    b.add_argument("--by", help="분류 주체 (기본 claude:오늘 날짜)")
    b.add_argument("--status", default="llm", choices=("llm", "seed"))
    b.add_argument("--dry-run", action="store_true", help="검증만 하고 overlay 에 쓰지 않음")
    b.add_argument("--batch", nargs="+", help="원래 배치 파일 — 결과가 같은 태그를 빠짐없이 담았는지 확인")
    b.add_argument("--etc", action="store_true",
                   help="확정(accepted)된 보류(etc) 행도 다시 분류해 덮어씀(reviewed 는 그대로)")
    b.add_argument("--db", help="overlay 대신 확장 DB(data/extra/<DB>.csv)에 반영(결과의 use_instead 칸도)")
    b.set_defaults(func=_cmd_batch_apply)

    s = sub.add_parser("evidence", help="Danbooru 증거(위키·함의·연관 태그) 받기·보기 — 드문 태그 분류용")
    esub = s.add_subparsers(dest="evidence_cmd", required=True)
    e = esub.add_parser("fetch", help="캐시에 없는 증거를 받음(끊겨도 같은 명령으로 이어 받기)")
    e.add_argument("tags", nargs="*", help="태그 이름(공백 가능)")
    e.add_argument("--etc", action="store_true", help="보류(etc) 태그 전부(reviewed 제외). --db 와 함께면 그 DB 의 보류 행")
    e.add_argument("--db", help="확장 DB(data/extra/<DB>.csv)의 미분류 행")
    e.add_argument("--no-related", action="store_true", help="연관 태그는 받지 않음(태그마다 요청 1번이라 느림)")
    e.add_argument("--delay", type=float, default=0.8, help="요청 사이 간격(초)")
    e.add_argument("--refresh", action="store_true", help="이미 받은 것도 다시 받음")
    e.add_argument("--cache", help="캐시 경로 (기본 work/evidence/evidence.json)")
    e.set_defaults(func=_cmd_evidence_fetch)
    e = esub.add_parser("show", help="태그의 증거 요약(캐시에 없으면 받음)")
    e.add_argument("tags", nargs="+")
    e.add_argument("--offline", action="store_true", help="받지 않고 캐시만 봄")
    e.add_argument("--chars", type=int, default=1200, help="위키를 몇 글자까지 보일지")
    e.add_argument("--cache", help="캐시 경로 (기본 work/evidence/evidence.json)")
    e.set_defaults(func=_cmd_evidence_show)

    s = sub.add_parser("review", help="검수 CSV (내보내기·반영)")
    rsub = s.add_subparsers(dest="review_cmd", required=True)
    r = rsub.add_parser("export", help="검수 대상(llm·conflict)을 엑셀용 CSV로")
    r.add_argument("--top", type=int, default=500)
    r.add_argument("--sample", help="로컬 분류 샘플(대조용, 저장소 밖 파일)")
    r.add_argument("--out", help="출력 경로 (기본 work/review/review-topN.csv)")
    r.set_defaults(func=_cmd_review_export)
    r = rsub.add_parser("apply", help="approve 열을 채운 검수 CSV를 반영")
    r.add_argument("file")
    r.add_argument("--by", help="검수자 (기본 user:오늘 날짜)")
    r.set_defaults(func=_cmd_review_apply)

    s = sub.add_parser("accept", help="LLM·시드 분류를 사람 검수 없이 확정(accepted)")
    s.add_argument("--top", type=int, help="general 상위 N개 안의 행만 (생략하면 전체)")
    s.set_defaults(func=_cmd_accept)

    s.add_argument("--db", help="확장 DB(data/extra/<DB>.csv)의 llm·seed 행을 확정")

    s = sub.add_parser("extra", help="확장 DB(data/extra) — 원본에 없는 태그 더하기·폐기 태그 받기·현황")
    xsub = s.add_subparsers(dest="extra_cmd", required=True)
    x = xsub.add_parser("add", help="이름을 확장 DB 에 더함(분류 칸은 비움 — batch --db 나 손으로 분류)")
    x.add_argument("db", help="DB ID (model·deprecated·user 등 — 없으면 새 파일)")
    x.add_argument("names", nargs="*", help="태그 이름(공백·밑줄 어느 쪽이든)")
    x.add_argument("--file", nargs="+", help="이름 목록 파일(한 줄에 하나, 또는 tagcomplete CSV 의 첫 칸)")
    x.add_argument("--models", help="새 행의 models 칸 (예: nai|illustrious)")
    x.add_argument("--note", help="새 행의 note 칸")
    x.add_argument("--ref", help="새 행의 ref 칸(근거 링크)")
    x.add_argument("--dry-run", action="store_true", help="무엇을 더할지 보고 쓰지 않음")
    x.set_defaults(func=_cmd_extra_add)
    x = xsub.add_parser("deprecated", help="Danbooru 폐기 태그를 받아 deprecated DB 에 새 이름을 더함(사용량은 증거 캐시에)")
    x.add_argument("--delay", type=float, default=0.8, help="요청 사이 간격(초)")
    x.add_argument("--cache", help="증거 캐시 경로 (기본 work/evidence/evidence.json)")
    x.add_argument("--dry-run", action="store_true", help="받아서 무엇을 더할지 보고 쓰지 않음")
    x.set_defaults(func=_cmd_extra_deprecated)
    x = xsub.add_parser("stats", help="확장 DB 마다 분류·보류·미분류 수")
    x.set_defaults(func=_cmd_extra_stats)

    s = sub.add_parser("export", help="내보내기 — 자동완성(tagcomplete)의 extra CSV")
    esub2 = s.add_subparsers(dest="export_cmd", required=True)
    x = esub2.add_parser("tagcomplete", help="확장 DB → tagcomplete extra CSV(이름,종류,메모,별칭). 대상은 기억함")
    x.add_argument("--out", help="파일 또는 tagcomplete 의 tags 폴더(폴더면 extra-quality-tags.csv). 처음 한 번만")
    x.add_argument("--db", nargs="+", help="넣을 DB·원본(e621 = e621.csv 에 분류한 태그) (기본 전부)")
    x.add_argument("--include-etc", action="store_true", help="보류(etc) 행도 넣음")
    x.add_argument("--include-auto", action="store_true", help="종류로 자동 분류한 행(폐기된 캐릭터·작가 등)도 넣음")
    x.add_argument("--force", action="store_true",
                   help="이 도구가 쓴 파일이 아니어도(손으로 관리하던 파일) 백업하고 덮어씀")
    x.add_argument("--dry-run", action="store_true", help="무엇을 쓸지 보고 쓰지 않음")
    x.set_defaults(func=_cmd_export_tagcomplete)

    s = sub.add_parser("lint", help="분류 일관성 점검(색·무늬 수식어 쌍)")
    s.set_defaults(func=_cmd_lint)

    s = sub.add_parser("fix", help="태그 분류를 손으로 고침(검증 후 reviewed 로 잠금). 값 없이 쓰면 현재 분류 보기")
    s.add_argument("items", nargs="+", metavar="태그|필드=값",
                   help="태그 이름(공백·별칭 가능)과 primary= secondary= rating= regions= identity= note= "
                        "(빈 값이면 지움, 여러 개는 |)")
    s.add_argument("--etc", action="store_true", help="분류 보류(primary=etc, 나머지 칸 비움)")
    s.add_argument("--by", help="고친 사람 (기본 user:오늘 날짜)")
    s.add_argument("--dry-run", action="store_true", help="바뀔 내용만 보여 줌")
    s.set_defaults(func=_cmd_fix)

    s = sub.add_parser("list", help="조건에 맞는 분류를 사용량 순으로 (예: --primary etc 는 보류 목록)")
    s.add_argument("--primary", help="메인(clothes) 또는 main/sub, etc")
    s.add_argument("--status", choices=tagdb.STATUSES)
    s.add_argument("--rating", choices=("",) + core.RATINGS, help="빈 문자열이면 등급 없는 행")
    s.add_argument("--grep", help="이름이나 note 에 들어간 글자")
    s.add_argument("--limit", type=int, default=50, help="보여 줄 개수 (0 이면 전부)")
    s.set_defaults(func=_cmd_list)

    s = sub.add_parser("check", help="overlay 손상 점검(빌드 검사 + 마지막 커밋 대비 사라진 행), 공유 배열 프리셋 검사")
    s.set_defaults(func=_cmd_check)

    s = sub.add_parser("calib", help="병렬 분류 보정(확정 태그로 작업자 품질 측정)")
    csub = s.add_subparsers(dest="calib_cmd", required=True)
    c = csub.add_parser("make", help="답을 가린 보정 배치와 선례 파일 만들기")
    c.add_argument("--n", type=int, default=300)
    c.add_argument("--salt", default="cal", help="뽑기 순서를 바꾸는 값")
    c.add_argument("--out", help="출력 폴더 (기본 work/calibration)")
    c.set_defaults(func=_cmd_calib_make)
    c = csub.add_parser("score", help="작업자 결과를 확정값과 비교")
    c.add_argument("files", nargs="+", help="결과 CSV(파일 하나 = 작업자 하나) 또는 폴더(안의 CSV 전부 = 작업자 하나)")
    c.add_argument("--main-min", type=float, default=0.95)
    c.add_argument("--primary-min", type=float, default=0.90)
    c.add_argument("--held-min", type=float, default=0.0, help="보류 판단 일치율 기준(파일럿은 0.9)")
    c.add_argument("--json", help="상세 결과 JSON 저장 경로")
    c.set_defaults(func=_cmd_calib_score)

    s = sub.add_parser("api", help="Claude Message Batches 로 배치 분류(사용법은 docs/api-batch.md)")
    asub = s.add_subparsers(dest="api_cmd", required=True)
    a = asub.add_parser("plan", help="요청을 만들고 토큰·비용을 추정(API 호출 없음 — --count 만 무료 count_tokens)")
    a.add_argument("files", nargs="*", help="배치 CSV(분류 단계). 검토 단계는 비움")
    a.add_argument("--run", required=True, help="run 이름(영숫자·-·_), 예: E2")
    a.add_argument("--stage", choices=api_batch.STAGES, default="classify")
    a.add_argument("--model", help="haiku·sonnet·opus 또는 모델 id (기본: 분류 haiku, 검토 sonnet)")
    a.add_argument("--effort", choices=api_batch.EFFORTS, default="medium")
    a.add_argument("--max-tokens", type=int, default=16000, help="요청당 출력 상한(사고 포함)")
    a.add_argument("--chunk", type=int, default=50, help="요청 하나에 넣을 태그 수(분류 단계)")
    a.add_argument("--count", action="store_true", help="count_tokens(무료)로 입력 토큰을 정확히 잼 — 키 필요")
    a.set_defaults(func=_cmd_api_plan)
    a = asub.add_parser("submit", help="배치 제출 — 예상 비용이 --max-usd 를 넘거나 이미 제출했으면 멈춤")
    a.add_argument("run")
    a.add_argument("--stage", choices=api_batch.STAGES, default="classify")
    a.add_argument("--max-usd", type=float, required=True, help="예상 비용 상한(USD, Batch 단가)")
    a.add_argument("--resubmit", action="store_true", help="이미 제출한 run·단계를 다시 제출(먼저 낸 배치도 과금됨)")
    a.set_defaults(func=_cmd_api_submit)
    a = asub.add_parser("adopt", help="응답을 못 받은 제출이 실제로 만든 배치(Console 에서 확인한 id)를 이 run 으로 받아들임")
    a.add_argument("run")
    a.add_argument("batch_id")
    a.add_argument("--stage", choices=api_batch.STAGES, default="classify")
    a.set_defaults(func=_cmd_api_adopt)
    a = asub.add_parser("status", help="제출한 배치의 진행 상황")
    a.add_argument("run")
    a.add_argument("--stage", choices=api_batch.STAGES, default="classify")
    a.set_defaults(func=_cmd_api_status)
    a = asub.add_parser("collect", help="결과를 CSV 로 받고 원장(local/api/ledger.csv)에 적음")
    a.add_argument("run")
    a.add_argument("--stage", choices=api_batch.STAGES, default="classify")
    a.set_defaults(func=_cmd_api_collect)
    a = asub.add_parser("sample", help="파일럿 표본 배치 — 지금 overlay 의 보류·분류 비율을 지켜 뽑음")
    a.add_argument("files", nargs="+", help="뽑을 배치 CSV(예: work/batches/E1-*.csv)")
    a.add_argument("--name", required=True, help="표본 배치 이름(예: P1 → P1-01.csv …)")
    a.add_argument("--n", type=int, default=200)
    a.add_argument("--size", type=int, default=50)
    a.add_argument("--salt", default="pilot", help="뽑기 순서를 바꾸는 값")
    a.add_argument("--out", help="출력 폴더 (기본 work/batches)")
    a.set_defaults(func=_cmd_api_sample)

    s = sub.add_parser("sync", help="코어·팩·청크 편집기를 ComfyUI_BMK_Nodes 폴더로 복사 (빌드 포함)")
    s.add_argument("--to", help="ComfyUI_BMK_Nodes 폴더 (생략하면 지난번 대상)")
    s.add_argument("--dry-run", action="store_true", help="무엇을 쓸지 보여주기만 함")
    s.add_argument("--force", action="store_true", help="노드 팩에서 바뀐 파일도 덮어씀")
    s.add_argument("--no-build", action="store_true", help="빌드하지 않고 기존 build/tagpack.json.gz 사용")
    s.add_argument("--pack", help="다른 팩 파일 사용(빌드 생략)")
    s.set_defaults(func=_cmd_sync)

    s = sub.add_parser("extension", help="크롬 확장 자료(팩·프리셋 목록)를 extension/data 에 만듦 (빌드 포함)")
    s.add_argument("--no-build", action="store_true", help="빌드하지 않고 기존 build/tagpack.json.gz 사용")
    s.add_argument("--pack", help="다른 팩 파일 사용(빌드 생략)")
    s.add_argument("--no-local", action="store_true", help="내 배열(local/arrangements)을 넣지 않음")
    s.add_argument("--zip", help="설치용 zip 도 이 경로에 만듦")
    s.set_defaults(func=_cmd_extension)

    s = sub.add_parser("fetch", help="sources.json 의 현재 원본 CSV를 받아 sha256 확인 후 data/source 에 둠(다른 PC 처음)")
    s.set_defaults(func=_cmd_fetch)

    s = sub.add_parser("gui", help="관리 툴(태그 목록·편집·보류·점검). PySide6 필요(없으면 설치를 물음)")
    s.set_defaults(func=_cmd_gui)

    # fix 는 `태그 --etc note=…` 처럼 옵션 뒤에도 태그·필드=값을 받는다(argparse 는 그 뒤 위치 인자를 못 받음)
    args, extra = p.parse_known_args(argv)
    if extra:
        if args.cmd != "fix" or any(x.startswith("--") for x in extra):
            p.error("알 수 없는 인자: " + " ".join(extra))
        args.items += extra
    try:
        return args.func(args)
    except tagdb.OverlayError as e:
        print(f"overlay 오류: {e}", file=sys.stderr)
        return 1
    except tagdb.OverlayChanged as e:
        print(f"중단: {e} 다시 실행하세요.", file=sys.stderr)
        return 1
    except api_batch.ApiError as e:
        print(f"api: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        if type(e).__module__.split(".")[0] != "anthropic":    # SDK 는 선택 설치라 클래스를 미리 가져오지 않는다
            raise
        print(f"API 오류({type(e).__name__}): {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
