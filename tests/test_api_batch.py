"""API 배치 분류(tools/api_batch.py, tagorg api …) 테스트 — 가짜 클라이언트만 쓰고 네트워크·실제 API 는 부르지 않는다.

SDK 왕복 테스트는 설치된 anthropic SDK 를 httpx 가짜 전송으로 돌려, 요청 직렬화와 결과 해석을 실제 SDK 객체로 확인한다
(SDK 가 없으면 건너뜀).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fixtures import overlay_rows, tagdb
from tools import api_batch, calibration

import tagorg

CONFIG = tagdb.load_config()
GUIDE = api_batch.GUIDE.read_text(encoding="utf-8")
FIELDS = ("name", "count", "aliases", "wiki", "other_names", "implies", "related", "related_series")
TAGS = [
    ("red_skirt", "50", "", "A red skirt.", "", "skirt", "pleated_skirt:0.40", ""),
    ("blue_skirt", "40", "", "", "", "skirt", "", ""),
    ("mystery_x", "30", "", "", "", "", "", "some_game:0.90"),
]
HAIKU = "claude-haiku-5-5"


def write_batch(folder: Path, stem: str = "B1-01", tags=TAGS) -> Path:
    path = folder / f"{stem}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(tags)
    return path


def row(name, primary="clothes/bottom", **kw):
    base = {"name": name, "primary": primary, "secondary": [], "rating": "g", "regions": ["hips", "thighs"],
            "identity": "", "confidence": 0.9, "note": ""}
    base.update(kw)
    return base


def etc_row(name):
    # 보류인데 confidence 를 채워 와도 지침 형식대로 비운다
    return row(name, "etc", rating="", regions=[], confidence=0.5, note="의미 불확실 · 게임 추정")


def message(rows, stop="end_turn", usage=(1000, 200, 0, 0), details=None):
    content = [SimpleNamespace(type="thinking", thinking="", signature="sig"),
               SimpleNamespace(type="text", text=json.dumps({"rows": rows}, ensure_ascii=False))]
    u = SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1], cache_read_input_tokens=usage[2],
                        cache_creation_input_tokens=usage[3])
    return SimpleNamespace(content=content, stop_reason=stop, stop_details=details, usage=u)


def ok(cid, rows, **kw):
    return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="succeeded", message=message(rows, **kw)))


def errored(cid, kind="overloaded_error"):
    err = SimpleNamespace(type="error", error=SimpleNamespace(type=kind, message="x"))
    return SimpleNamespace(custom_id=cid, result=SimpleNamespace(type="errored", error=err))


class Rejected(Exception):
    status_code = 400                     # SDK 의 APIStatusError 처럼 서버가 거절한 오류


class FakeClient:
    def __init__(self, results=(), status="ended", create_error=None, counts=None):
        self.created = []
        self.counted = []
        self.options = []
        self.results_list = list(results)
        self.status = status
        self.create_error = create_error
        self.counts = counts or {"processing": 0, "succeeded": 1, "errored": 0, "canceled": 0, "expired": 0}
        self.messages = SimpleNamespace(batches=self, count_tokens=self.count_tokens)

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    def create(self, requests):
        if self.create_error is not None:
            raise self.create_error
        self.created.append(list(requests))
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}", processing_status="in_progress",
                               expires_at=datetime(2026, 10, 30, tzinfo=timezone.utc))

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self.status, ended_at=None, expires_at=None,
                               request_counts=SimpleNamespace(**self.counts))

    def results(self, batch_id):
        return iter(self.results_list)

    def count_tokens(self, **kw):
        self.counted.append(kw)
        return SimpleNamespace(input_tokens=1234)


class Workspace(unittest.TestCase):
    """임시 work/·원장 폴더와 배치 파일 하나(B1-01: 태그 3개 → chunk 2 로 요청 2개)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.ledger = self.root / "local" / "ledger.csv"
        (self.root / "batches").mkdir()
        self.batch = write_batch(self.root / "batches")
        self.overlay = overlay_rows()

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, run="T1", **kw):
        plan = api_batch.plan_classify(run, [self.batch], self.overlay, CONFIG, GUIDE, chunk=2, **kw)
        api_batch.write_plan(plan, self.work)
        return plan

    def submit(self, client, run="T1", stage="classify", max_usd=5.0, **kw):
        return api_batch.submit(run, stage, lambda: client, max_usd, work=self.work, **kw)

    def collect(self, client, run="T1", stage="classify"):
        return api_batch.collect(run, stage, client, CONFIG, work=self.work, ledger=self.ledger)

    def read_csv(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))


class RequestTests(Workspace):
    def test_system_blocks_are_byte_stable_for_cache(self):
        a = api_batch.plan_classify("A", [self.batch], self.overlay, CONFIG, GUIDE, chunk=2)
        b = api_batch.plan_classify("B", [self.batch], self.overlay, CONFIG, GUIDE, chunk=1)
        systems = {json.dumps(r["params"]["system"], ensure_ascii=False) for p in (a, b) for r in p["requests"]}
        self.assertEqual(len(systems), 1)                    # run 이름·묶음 크기와 상관없이 같은 바이트
        system = a["requests"][0]["params"]["system"]
        self.assertEqual(system[-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertIn("드문 태그와 분류 보류", system[0]["text"])      # 지침 전문
        self.assertIn("clothes/bottom", system[1]["text"])          # 분류 체계
        review = api_batch.system_blocks(GUIDE, CONFIG["taxonomy"], CONFIG["rules"]["regions"], "review")
        self.assertEqual(review[0], system[0])
        self.assertIn("검토자 역할", review[1]["text"])

    def test_params_have_no_sampling_or_thinking(self):
        p = self.plan()["requests"][0]["params"]
        self.assertEqual(set(p), {"model", "max_tokens", "system", "messages", "output_config"})
        self.assertEqual(p["output_config"]["effort"], "medium")
        self.assertEqual(p["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(p["messages"][-1]["role"], "user")      # 프리필 없음

    def test_schema_enums_come_from_taxonomy(self):
        regions = CONFIG["rules"]["regions"]
        schema = api_batch.output_schema(CONFIG["taxonomy"], regions)
        item = schema["properties"]["rows"]["items"]
        props = item["properties"]
        self.assertIn("etc", props["primary"]["enum"])
        self.assertIn("clothes/bottom", props["primary"]["enum"])
        self.assertIn("clothes", props["primary"]["enum"])
        self.assertNotIn("etc", props["secondary"]["items"]["enum"])
        self.assertNotIn("unknown", props["primary"]["enum"])
        self.assertEqual(props["regions"]["items"]["enum"], list(regions))
        self.assertEqual(props["rating"]["enum"], ["", "g", "s", "q", "e"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(set(item["required"]), set(props))
        self.assertNotIn("reason", props)
        review = api_batch.output_schema(CONFIG["taxonomy"], regions, review=True)
        self.assertIn("reason", review["properties"]["rows"]["items"]["required"])

    def test_chunks_and_precedents_hide_run_tags(self):
        plan = self.plan()
        self.assertEqual([p["custom_id"] for p in plan["parts"]], ["B1-01__1", "B1-01__2"])
        self.assertEqual(plan["parts"][0]["names"], ["red_skirt", "blue_skirt"])
        text = plan["requests"][0]["params"]["messages"][0]["content"]
        self.assertIn("black_skirt,clothes/bottom,,g,hips|thighs,", text)   # 같은 계열 선례
        self.assertNotIn("\nblue_skirt,clothes/bottom", text)                # 이번에 분류하는 태그는 선례에서 뺌
        self.assertIn("red_skirt,50,,A red skirt.", text)                   # 증거 칸 그대로
        precedents = text.split("## 분류할")[0]
        self.assertEqual(precedents.count("_skirt,") + precedents.count("\nskirt,"), 5)   # 계열당 5행
        self.assertEqual(plan["estimate"]["tags"], 3)
        self.assertFalse(plan["estimate"]["counted"])

    def test_plan_rejects_bad_inputs(self):
        with self.assertRaises(api_batch.ApiError):
            api_batch.plan_classify("bad name", [self.batch], self.overlay, CONFIG, GUIDE)
        dup = write_batch(self.root / "batches", "B1-02", TAGS[:1])
        with self.assertRaises(api_batch.ApiError) as cm:
            api_batch.plan_classify("T1", [self.batch, dup], self.overlay, CONFIG, GUIDE)
        self.assertIn("red_skirt", str(cm.exception))
        odd = write_batch(self.root / "batches", "B 1", TAGS[2:])
        with self.assertRaises(api_batch.ApiError):
            api_batch.plan_classify("T1", [odd], self.overlay, CONFIG, GUIDE)
        with self.assertRaises(api_batch.ApiError):
            api_batch.resolve_model("gpt-x")
        with self.assertRaises(api_batch.ApiError):
            api_batch.plan_classify("T1", [self.batch], self.overlay, CONFIG, GUIDE, effort="huge")

    def test_estimate_assumes_cache_writes(self):
        plan = self.plan()
        expected = cheaper = 0.0
        for part, req in zip(plan["parts"], plan["requests"]):
            cached = api_batch.estimate_tokens("".join(b["text"] for b in req["params"]["system"]))
            tin, tout = part["est_input_tokens"], part["est_output_tokens"]
            expected += api_batch.batch_usd(HAIKU, tin - cached, tout, cache_write=cached)
            cheaper += api_batch.batch_usd(HAIKU, tin, tout)
        self.assertAlmostEqual(plan["estimate"]["usd"], round(expected, 4))
        self.assertGreater(expected, cheaper)                 # 최악(1시간 캐시 쓰기 2배)으로 잡는다
        self.assertGreater(plan["estimate"]["cache_write_tokens"], 5000)

    def test_same_stem_in_two_folders_is_refused(self):
        a = write_batch(self.root / "batches", "retry", TAGS[:1])
        (self.root / "other").mkdir()
        b = write_batch(self.root / "other", "retry", TAGS[1:])
        with self.assertRaises(api_batch.ApiError) as cm:
            api_batch.plan_classify("T1", [a, b], self.overlay, CONFIG, GUIDE)
        self.assertIn("이름이 같은", str(cm.exception))
        (self.root / "third").mkdir()
        c = write_batch(self.root / "third", "Retry", TAGS[1:])        # Windows 에서는 retry.csv 와 같은 파일
        with self.assertRaises(api_batch.ApiError):
            api_batch.plan_classify("T1", [a, c], self.overlay, CONFIG, GUIDE)

    def test_ceiling_uses_max_tokens(self):
        plan = self.plan()
        ceiling = 0.0
        for part, req in zip(plan["parts"], plan["requests"]):
            cached = api_batch.estimate_tokens("".join(b["text"] for b in req["params"]["system"]))
            ceiling += api_batch.batch_usd(HAIKU, part["est_input_tokens"] - cached, 16000, cache_write=cached)
        e = plan["estimate"]
        self.assertAlmostEqual(e["max_usd"], round(ceiling, 4))
        self.assertGreater(e["max_usd"], e["usd"])
        client = FakeClient()
        with self.assertRaises(api_batch.ApiError) as cm:       # 예상은 넘지만 최대에는 못 미치는 상한
            self.submit(client, max_usd=(e["usd"] + e["max_usd"]) / 2)
        self.assertIn("최대 비용", str(cm.exception))
        self.assertEqual(client.created, [])
        self.assertEqual(self.submit(client, max_usd=e["max_usd"])["max_cost_usd"], e["max_usd"])

    def test_costs(self):
        self.assertAlmostEqual(api_batch.batch_usd("claude-sonnet-5-5", 1_000_000, 0), 1.0)
        self.assertAlmostEqual(api_batch.batch_usd("claude-sonnet-5-5", 0, 1_000_000), 5.0)
        self.assertAlmostEqual(api_batch.batch_usd(HAIKU, 50_000, 0), 0.0025)
        self.assertAlmostEqual(api_batch.batch_usd(HAIKU, 150_000, 0), 0.0375)   # 10만 토큰 넘으면 높은 단가
        # 캐시 읽기는 입력 단가의 0.1(Opus 0.05), 1시간 캐시 쓰기는 2배
        self.assertAlmostEqual(api_batch.batch_usd("claude-sonnet-5-5", 0, 0, cache_read=1_000_000), 0.1)
        self.assertAlmostEqual(api_batch.batch_usd("claude-opus-5-5", 0, 0, cache_read=1_000_000), 0.1)
        self.assertAlmostEqual(api_batch.batch_usd("claude-sonnet-5-5", 0, 0, cache_write=1_000_000), 2.0)
        self.assertEqual(api_batch.estimate_tokens("abc" * 9), 10)
        self.assertEqual(api_batch.estimate_tokens("한글"), 2)

    def test_count_tokens_replaces_estimate(self):
        client = FakeClient()
        plan = api_batch.count_plan(api_batch.plan_classify("T1", [self.batch], self.overlay, CONFIG, GUIDE,
                                                            chunk=2), client)
        self.assertTrue(plan["estimate"]["counted"])
        self.assertEqual(plan["estimate"]["input_tokens"], 1234 * 2)
        self.assertEqual(len(client.counted), 2)
        sent = client.counted[0]
        self.assertEqual(sent["model"], HAIKU)
        self.assertIn("format", sent["extra_body"]["output_config"])


class SubmitTests(Workspace):
    def test_over_cap_refuses_before_making_client(self):
        self.plan()

        def boom():
            raise AssertionError("클라이언트를 만들면 안 됨")

        with self.assertRaises(api_batch.ApiError) as cm:
            api_batch.submit("T1", "classify", boom, 0.0000001, work=self.work)
        self.assertIn("상한", str(cm.exception))
        self.assertFalse((api_batch.run_dir("T1", "classify", self.work) / "manifest.json").exists())

    def test_submit_once_then_resubmit(self):
        plan = self.plan()
        client = FakeClient()
        m = self.submit(client)
        self.assertEqual(m["batch_id"], "msgbatch_1")
        self.assertEqual(m["expires_at"], "2026-10-30T00:00:00+00:00")
        self.assertEqual([r["custom_id"] for r in client.created[0]], ["B1-01__1", "B1-01__2"])
        self.assertEqual(client.created[0], plan["requests"])
        with self.assertRaises(api_batch.ApiError):
            self.submit(client)                                # 같은 run·단계는 한 번만
        with self.assertRaises(api_batch.ApiError):
            self.plan()                                        # 제출한 계획을 덮어쓰지 않음
        self.assertEqual(self.submit(client, resubmit=True)["batch_id"], "msgbatch_2")
        d = api_batch.run_dir("T1", "classify", self.work)
        self.assertEqual(len(list(d.glob("manifest-*.json"))), 1)   # 먼저 낸 manifest 는 보관

    def test_missing_plan(self):
        with self.assertRaises(api_batch.ApiError):
            self.submit(FakeClient(), run="NOPE")

    def test_create_is_not_auto_retried(self):
        self.plan()
        client = FakeClient()
        self.submit(client)
        self.assertEqual(client.options, [{"max_retries": 0}])   # 멱등성 키가 없어 재시도하면 배치가 둘 생김

    def test_tampered_requests_refused(self):
        self.plan()
        path = api_batch.run_dir("T1", "classify", self.work) / "requests.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace(HAIKU, "claude-sonnet-5-5"), encoding="utf-8")
        client = FakeClient()
        with self.assertRaises(api_batch.ApiError) as cm:
            self.submit(client)
        self.assertIn("맞지 않습니다", str(cm.exception))
        self.assertEqual(client.created, [])

    def test_unknown_failure_keeps_old_manifest_and_blocks(self):
        self.plan()
        self.submit(FakeClient())
        d = api_batch.run_dir("T1", "classify", self.work)
        with self.assertRaises(api_batch.ApiError) as cm:
            self.submit(FakeClient(create_error=ConnectionError("reset")), resubmit=True)
        self.assertIn("adopt", str(cm.exception))
        self.assertEqual(api_batch.read_json(d / "manifest.json")["batch_id"], "msgbatch_1")   # 먼저 낸 것 그대로
        self.assertIn("ConnectionError", api_batch.read_json(d / "pending.json")["error"])
        self.assertFalse((d / "submit.lock").exists())
        self.assertEqual(api_batch.status("T1", "classify", FakeClient(), self.work)["batch_id"], "msgbatch_1")

    def test_pending_without_manifest_then_adopt(self):
        self.plan()
        with self.assertRaises(api_batch.ApiError):
            self.submit(FakeClient(create_error=TimeoutError()))
        with self.assertRaises(api_batch.ApiError) as cm:
            self.submit(FakeClient())                            # 결과를 모르면 다시 내지 않음
        self.assertIn("adopt", str(cm.exception))
        with self.assertRaises(api_batch.ApiError):
            api_batch.status("T1", "classify", FakeClient(), self.work)
        with self.assertRaises(api_batch.ApiError):
            self.plan()                                          # 제출 중인 계획을 덮어쓰지 않음
        with self.assertRaises(api_batch.ApiError) as cm:       # 요청 수가 다른 배치는 받지 않음
            api_batch.adopt("T1", "classify", FakeClient(), "msgbatch_x", self.work)
        self.assertIn("다른 배치", str(cm.exception))
        two = FakeClient(counts={"processing": 2, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0},
                         status="in_progress")
        m = api_batch.adopt("T1", "classify", two, "msgbatch_x", self.work)
        self.assertEqual(m["batch_id"], "msgbatch_x")
        d = api_batch.run_dir("T1", "classify", self.work)
        self.assertFalse((d / "pending.json").exists())
        with self.assertRaises(api_batch.ApiError):
            api_batch.adopt("T1", "classify", two, "msgbatch_y", self.work)

    def test_resubmit_lost_response_then_adopt_replaces_manifest(self):
        self.plan()
        self.submit(FakeClient())                                # A
        d = api_batch.run_dir("T1", "classify", self.work)
        with self.assertRaises(api_batch.ApiError) as cm:      # B 를 냈는데 응답만 끊김
            self.submit(FakeClient(create_error=ConnectionError("reset")), resubmit=True)
        self.assertIn("adopt", str(cm.exception))
        two = FakeClient(counts={"processing": 2, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0})
        m = api_batch.adopt("T1", "classify", two, "msgbatch_B", self.work)
        self.assertEqual(m["batch_id"], "msgbatch_B")
        archived = [api_batch.read_json(p)["batch_id"] for p in d.glob("manifest-*.json")]
        self.assertEqual(archived, ["msgbatch_1"])               # A 는 보관
        self.assertFalse((d / "pending.json").exists())

    def test_resubmit_keeps_previous_pending_record(self):
        self.plan()
        d = api_batch.run_dir("T1", "classify", self.work)
        # 지난 시도: 배치 A 는 만들어졌는데 manifest 저장만 실패해 pending 에 id 만 남음
        (d / "pending.json").write_text(json.dumps({"started": "x", "batch_id": "msgbatch_A"}), encoding="utf-8")
        with self.assertRaises(Rejected):
            self.submit(FakeClient(create_error=Rejected("rate")), resubmit=True)
        kept = [api_batch.read_json(p).get("batch_id") for p in d.glob("pending-*.json")]
        self.assertEqual(kept, ["msgbatch_A"])                   # 거절된 이번 시도만 지우고 A 의 기록은 남김
        self.assertFalse((d / "pending.json").exists())

    def test_plan_and_adopt_respect_the_lock(self):
        self.plan()
        d = api_batch.run_dir("T1", "classify", self.work)
        (d / "submit.lock").write_text("", encoding="utf-8")
        plan = api_batch.plan_classify("T1", [self.batch], self.overlay, CONFIG, GUIDE, chunk=1)
        with self.assertRaises(api_batch.ApiError):
            api_batch.write_plan(plan, self.work)                # 제출 중 계획 교체 금지
        with self.assertRaises(api_batch.ApiError):
            api_batch.adopt("T1", "classify", FakeClient(), "msgbatch_x", self.work)
        self.assertEqual(len(api_batch.read_json(d / "plan.json")["parts"]), 2)

    def test_collect_refuses_plan_changed_after_submit(self):
        self.plan()
        self.submit(FakeClient())
        d = api_batch.run_dir("T1", "classify", self.work)
        plan = api_batch.read_json(d / "plan.json")
        plan["requests_sha256"] = "0" * 64
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(api_batch.ApiError) as cm:
            self.collect(FakeClient())
        self.assertIn("계획이 바뀌었습니다", str(cm.exception))

    def test_rejected_request_clears_pending(self):
        self.plan()
        with self.assertRaises(Rejected):
            self.submit(FakeClient(create_error=Rejected("invalid_request")))
        d = api_batch.run_dir("T1", "classify", self.work)
        self.assertFalse((d / "pending.json").exists())
        self.assertFalse((d / "manifest.json").exists())
        self.assertEqual(self.submit(FakeClient())["batch_id"], "msgbatch_1")   # 고친 뒤 다시 낼 수 있음

    def test_lock_blocks_concurrent_submit(self):
        self.plan()
        d = api_batch.run_dir("T1", "classify", self.work)
        (d / "submit.lock").write_text("", encoding="utf-8")
        client = FakeClient()
        with self.assertRaises(api_batch.ApiError) as cm:
            self.submit(client)
        self.assertIn("submit.lock", str(cm.exception))
        self.assertEqual(client.created, [])
        self.assertTrue((d / "submit.lock").exists())            # 남의 잠금은 지우지 않음


class CollectTests(Workspace):
    def test_collect_writes_results_retry_and_ledger_once(self):
        self.plan()
        self.submit(FakeClient())
        client = FakeClient([
            ok("B1-01__1", [row("red skirt"), row("blue_skirt"), row("not_in_batch")], usage=(20000, 3000, 0, 9000)),
            ok("B1-01__2", [], stop="refusal", details=SimpleNamespace(category="general_harms"), usage=(500, 10, 0, 0)),
        ])
        s = self.collect(client)
        self.assertTrue(s["ended"])
        self.assertEqual(s["parts"], {"B1-01__1": "ok", "B1-01__2": "refusal:general_harms"})
        rows = self.read_csv(self.work / "results" / "T1" / "B1-01.csv")
        self.assertEqual([r["name"] for r in rows], ["red_skirt", "blue_skirt"])      # 공백 이름을 밑줄로
        self.assertEqual(rows[0]["regions"], "hips|thighs")
        self.assertEqual(rows[0]["confidence"], "0.9")
        self.assertTrue(any("not_in_batch" in w for w in s["warnings"]))
        self.assertEqual(s["failed"], {"mystery_x": "refusal:general_harms"})
        retry = self.read_csv(s["retry"])
        self.assertEqual([(r["name"], r["related_series"]) for r in retry], [("mystery_x", "some_game:0.90")])
        self.assertEqual(s["usage"], {"input": 20500, "output": 3010, "cache_read": 0, "cache_write": 9000})
        expected = (api_batch.batch_usd(HAIKU, 20000, 3000, 0, 9000) + api_batch.batch_usd(HAIKU, 500, 10))
        self.assertAlmostEqual(s["usd"], round(expected, 4))
        ledger = self.read_csv(self.ledger)
        self.assertEqual(len(ledger), 1)
        self.assertEqual((ledger[0]["run"], ledger[0]["succeeded"], ledger[0]["failed_tags"]), ("T1", "1", "1"))
        raw = (api_batch.run_dir("T1", "classify", self.work) / "raw.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(raw.splitlines()), 2)
        again = self.collect(client)                       # 다시 받아도 원장은 한 번만
        self.assertFalse(again["ledger"])
        self.assertEqual(len(self.read_csv(self.ledger)), 1)

    def test_etc_rows_invalid_rows_and_errors(self):
        self.plan()
        self.submit(FakeClient())
        bad = row("blue_skirt", rating="x")                  # schema 밖 값은 validate_row 가 잡아 재시도로
        s = self.collect(FakeClient([ok("B1-01__1", [row("red_skirt"), bad]), errored("B1-01__2")]))
        self.assertTrue(s["failed"]["blue_skirt"].startswith("형식"))
        self.assertEqual(s["failed"]["mystery_x"], "errored:overloaded_error")
        self.assertEqual(s["parts"]["B1-01__2"], "errored:overloaded_error")

    def test_etc_confidence_cleared_and_missing_result(self):
        self.plan()
        self.submit(FakeClient())
        s = self.collect(FakeClient([ok("B1-01__2", [etc_row("mystery_x")])]))
        rows = {r["name"]: r for r in self.read_csv(self.work / "results" / "T1" / "B1-01.csv")}
        self.assertEqual(rows["mystery_x"]["confidence"], "")
        self.assertEqual(rows["mystery_x"]["note"], "의미 불확실 · 게임 추정")
        self.assertEqual(s["parts"]["B1-01__1"], "결과 없음")
        self.assertEqual(set(s["failed"]), {"red_skirt", "blue_skirt"})

    def test_max_tokens_and_not_json(self):
        self.plan()
        self.submit(FakeClient())
        cut = ok("B1-01__1", [row("red_skirt")], stop="max_tokens")
        garbage = ok("B1-01__2", [])
        garbage.result.message.content[-1].text = "not json"
        s = self.collect(FakeClient([cut, garbage]))
        self.assertEqual(s["parts"], {"B1-01__1": "max_tokens", "B1-01__2": "json 아님"})
        self.assertEqual(len(s["failed"]), 3)

    def test_not_ended(self):
        self.plan()
        self.submit(FakeClient())
        s = self.collect(FakeClient(status="in_progress"))
        self.assertFalse(s["ended"])
        self.assertFalse(self.ledger.exists())


class ReviewTests(Workspace):
    def classify(self):
        self.plan()
        self.submit(FakeClient())
        self.collect(FakeClient([ok("B1-01__1", [row("red_skirt"), row("blue_skirt")]),
                                 ok("B1-01__2", [etc_row("mystery_x")])]))

    def test_review_needs_collected_classify(self):
        self.plan()
        with self.assertRaises(api_batch.ApiError):           # 아직 제출 전
            api_batch.plan_review("T1", self.overlay, CONFIG, GUIDE, work=self.work)
        self.submit(FakeClient())
        with self.assertRaises(api_batch.ApiError):           # 결과를 받기 전
            api_batch.plan_review("T1", self.overlay, CONFIG, GUIDE, work=self.work)

    def test_review_merges_fixes_into_final(self):
        self.classify()
        plan, skipped = api_batch.plan_review("T1", self.overlay, CONFIG, GUIDE, work=self.work)
        self.assertEqual(skipped, [])
        self.assertEqual(plan["model"], "claude-sonnet-5-5")
        self.assertEqual([p["custom_id"] for p in plan["parts"]], ["B1-01__1", "B1-01__2"])
        text = plan["requests"][0]["params"]["messages"][0]["content"]
        self.assertIn("## 분류자 결과", text)
        self.assertIn("red_skirt,clothes/bottom,,g,hips|thighs,,0.9,", text)
        self.assertIn("reason", json.dumps(plan["requests"][0]["params"]["output_config"]))
        api_batch.write_plan(plan, self.work)
        self.submit(FakeClient(), stage="review")
        fix = row("blue_skirt", "clothes/top", regions=["front_torso", "back"], reason="상의로 쓰임")
        s = self.collect(FakeClient([ok("B1-01__1", [fix]), errored("B1-01__2")]), stage="review")
        out = self.work / "results" / "T1"
        final = {r["name"]: r for r in self.read_csv(out / "final" / "B1-01.csv")}
        self.assertEqual(final["blue_skirt"]["primary"], "clothes/top")
        self.assertEqual(final["red_skirt"]["primary"], "clothes/bottom")
        self.assertEqual(final["mystery_x"]["primary"], "etc")             # 검토 실패 → 분류 결과 그대로
        self.assertNotIn("reason", final["blue_skirt"])
        changes = self.read_csv(out / "review" / "changes.csv")
        self.assertEqual(len(changes), 1)
        self.assertIn("primary: clothes/bottom → clothes/top", changes[0]["changes"])
        self.assertEqual(changes[0]["reason"], "상의로 쓰임")
        self.assertEqual(self.read_csv(out / "review" / "B1-01-fix.csv")[0]["reason"], "상의로 쓰임")
        self.assertEqual([r["name"] for r in self.read_csv(out / "review" / "unreviewed.csv")], ["mystery_x"])
        self.assertEqual((s["changes"], s["unreviewed"], s["fixes"]), (1, 1, 1))
        self.assertEqual([r["stage"] for r in self.read_csv(self.ledger)], ["classify", "review"])

    def test_review_merges_against_reviewed_snapshot(self):
        self.classify()
        plan, _ = api_batch.plan_review("T1", self.overlay, CONFIG, GUIDE, work=self.work)
        api_batch.write_plan(plan, self.work)
        self.submit(FakeClient(), stage="review")
        # 검토를 낸 뒤 분류 결과가 바뀜(예: 분류 재제출) — 검토자는 원래 값을 보고 '고칠 것 없음'
        path = self.work / "results" / "T1" / "B1-01.csv"
        path.write_text(path.read_text(encoding="utf-8").replace("red_skirt,clothes/bottom", "red_skirt,clothes/top"),
                        encoding="utf-8")
        s = self.collect(FakeClient([ok("B1-01__1", []), ok("B1-01__2", [])]), stage="review")
        final = {r["name"]: r for r in self.read_csv(self.work / "results" / "T1" / "final" / "B1-01.csv")}
        self.assertEqual(final["red_skirt"]["primary"], "clothes/bottom")      # 검토한 값 기준
        self.assertTrue(any("바뀐 태그 1개" in w for w in s["warnings"]))
        self.assertEqual(s["unreviewed"], 0)


class KeyAndClientTests(unittest.TestCase):
    def test_read_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "anthropic.env")
            with self.assertRaises(api_batch.ApiError) as cm:
                api_batch.read_api_key(path)
            self.assertIn("ANTHROPIC_API_KEY=", str(cm.exception))
            path.write_text("﻿# 테스트 설정\nOTHER=1\nANTHROPIC_API_KEY = \"sk-ant-test\"\n", encoding="utf-8")
            self.assertEqual(api_batch.read_api_key(path), "sk-ant-test")
            path.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
            with self.assertRaises(api_batch.ApiError):
                api_batch.read_api_key(path)

    def test_plan_needs_no_sdk(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules, {"anthropic": None}):
            path = write_batch(Path(tmp))
            plan = api_batch.plan_classify("T1", [path], overlay_rows(), CONFIG, GUIDE)
            self.assertEqual(len(plan["parts"]), 1)
            with self.assertRaises(api_batch.ApiError) as cm:
                api_batch.make_client("sk-ant-test")
            self.assertIn("requirements-api.txt", str(cm.exception))

    def test_client_ignores_inherited_base_url_and_token(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest("anthropic SDK 없음")
        env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9/proxy", "ANTHROPIC_AUTH_TOKEN": "other-account"}
        with mock.patch.dict(os.environ, env):
            client = api_batch.make_client("sk-ant-test")
            self.assertEqual(os.environ["ANTHROPIC_BASE_URL"], env["ANTHROPIC_BASE_URL"])   # 환경은 되돌림
        self.assertTrue(str(client.base_url).startswith("https://api.anthropic.com"))
        self.assertIsNone(client.auth_token)
        self.assertEqual(client.api_key, "sk-ant-test")


class SdkRoundTripTests(Workspace):
    """실제 SDK 객체로 제출·결과 해석을 확인한다(전송만 가짜)."""

    def setUp(self):
        super().setUp()
        try:
            import anthropic
            import httpx
        except ImportError:
            self.skipTest("anthropic SDK 없음")
        self.sent = []
        base = "https://api.anthropic.com/v1/messages/batches"
        batch_obj = {"id": "msgbatch_01", "type": "message_batch", "processing_status": "ended",
                     "request_counts": {"processing": 0, "succeeded": 2, "errored": 0, "canceled": 0, "expired": 0},
                     "created_at": "2026-10-29T00:00:00Z", "ended_at": "2026-10-29T00:10:00Z",
                     "expires_at": "2026-10-30T00:00:00Z", "archived_at": None, "cancel_initiated_at": None,
                     "results_url": f"{base}/msgbatch_01/results"}

        def line(cid, rows):
            text = json.dumps({"rows": rows}, ensure_ascii=False)
            msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": HAIKU,
                   "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                               {"type": "text", "text": text}],
                   "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 70,
                             "cache_creation_input_tokens": 0}}
            return json.dumps({"custom_id": cid, "result": {"type": "succeeded", "message": msg}}, ensure_ascii=False)

        jsonl = "\n".join([line("B1-01__2", [etc_row("mystery_x")]),
                           line("B1-01__1", [row("red_skirt"), row("blue_skirt")])]) + "\n"

        def handler(request):
            url = str(request.url)
            if request.method == "POST" and url == base:
                self.sent.append(json.loads(request.content))
                self.headers = request.headers
                return httpx.Response(200, json={**batch_obj, "processing_status": "in_progress", "ended_at": None,
                                                 "results_url": None})
            if url == f"{base}/msgbatch_01":
                return httpx.Response(200, json=batch_obj)
            if url == batch_obj["results_url"]:
                return httpx.Response(200, content=jsonl.encode("utf-8"),
                                      headers={"content-type": "application/binary"})
            return httpx.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": url}})

        self.client = anthropic.Anthropic(api_key="sk-ant-test", base_url="https://api.anthropic.com",
                                          http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_submit_and_collect_with_real_sdk(self):
        plan = self.plan()
        m = self.submit(self.client)
        self.assertEqual(m["batch_id"], "msgbatch_01")
        self.assertEqual(self.headers["x-api-key"], "sk-ant-test")
        body = self.sent[0]
        self.assertEqual(body["requests"], plan["requests"])        # output_config·cache_control ttl 그대로
        self.assertEqual(body["requests"][0]["params"]["system"][-1]["cache_control"]["ttl"], "1h")
        s = self.collect(self.client)
        self.assertEqual(s["parts"], {"B1-01__2": "ok", "B1-01__1": "ok"})
        rows = self.read_csv(self.work / "results" / "T1" / "B1-01.csv")
        self.assertEqual([r["name"] for r in rows], ["red_skirt", "blue_skirt", "mystery_x"])   # 요청 순서대로
        self.assertEqual(s["usage"]["cache_read"], 140)
        raw = (api_batch.run_dir("T1", "classify", self.work) / "raw.jsonl").read_text(encoding="utf-8")
        self.assertIn('"custom_id": "B1-01__1"', raw)


class SampleTests(Workspace):
    def test_sample_keeps_held_share_and_is_deterministic(self):
        tags = [(f"tag_{i:02d}", "10", "", "", "", "", "", "") for i in range(10)]
        src = write_batch(self.root / "batches", "E9-01", tags)
        overlay = {f"tag_{i:02d}": {"primary": "etc" if i < 4 else "object/prop"} for i in range(10)}
        out = self.root / "pilot"
        paths = api_batch.sample([src], overlay, 5, out, "P1", size=2)
        self.assertEqual([p.name for p in paths], ["P1-01.csv", "P1-02.csv", "P1-03.csv"])
        names = [r["name"] for p in paths for r in self.read_csv(p)]
        self.assertEqual(len(names), 5)
        self.assertEqual(sum(1 for n in names if overlay[n]["primary"] == "etc"), 2)    # 4/10 비율
        self.assertEqual(list(self.read_csv(paths[0])[0]), list(FIELDS))
        again = api_batch.sample([src], overlay, 5, self.root / "again", "P1", size=2)
        self.assertEqual(names, [r["name"] for p in again for r in self.read_csv(p)])
        with self.assertRaises(api_batch.ApiError):           # 같은 이름으로 다시 만들면 옛 파일이 섞이므로 거부
            api_batch.sample([src], overlay, 3, out, "P1", size=2)
        with self.assertRaises(api_batch.ApiError):           # 대소문자만 바꿔도(Windows 에서는 같은 파일)
            api_batch.sample([src], overlay, 3, out, "p1", size=2)


class CalibrationHeldTests(unittest.TestCase):
    def test_held_agreement(self):
        ov = overlay_rows({"mystery_x": {"name": "mystery_x", "primary": "etc", "status": "accepted"}})
        worker = [{"name": "mystery_x", "primary": "object/prop"}, {"name": "navel", "primary": "body/torso",
                                                                     "rating": "g", "regions": "front_torso"}]
        report = calibration.score({"w": worker}, ov)
        self.assertEqual(report["workers"]["w"]["held"], 0.5)
        self.assertFalse(calibration.passed(report, 0.0, 0.0, held_min=0.9))
        self.assertTrue(calibration.passed(report, 0.0, 0.0))
        self.assertIn("보류 판단 ≥ 90%", calibration.format_report(report, 0.0, 0.0, 0.9))


class CliTests(Workspace):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(api_batch, "WORK", self.work), mock.patch.object(api_batch, "LEDGER", self.ledger), \
                mock.patch.object(tagorg.tagdb, "read_overlay", lambda *a, **k: self.overlay), \
                mock.patch.object(tagorg, "_api_client", lambda: self.client):
            code = tagorg.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_plan_submit_status_collect(self):
        self.client = FakeClient()
        code, out, _ = self.run_cli("api", "plan", str(self.batch), "--run", "C1", "--chunk", "2")
        self.assertEqual(code, 0)
        self.assertIn("요청 2개 · 태그 3개", out)
        code, _, err = self.run_cli("api", "submit", "C1", "--max-usd", "0.0000001")
        self.assertEqual(code, 1)
        self.assertIn("상한", err)
        self.assertEqual(self.client.created, [])
        code, out, _ = self.run_cli("api", "submit", "C1", "--max-usd", "1")
        self.assertEqual(code, 0)
        self.assertIn("msgbatch_1", out)
        code, out, _ = self.run_cli("api", "status", "C1")
        self.assertIn("ended", out)
        self.assertIn("api collect C1", out)
        self.client = FakeClient([ok("B1-01__1", [row("red_skirt"), row("blue_skirt")]),
                                  ok("B1-01__2", [etc_row("mystery_x")])])
        code, out, _ = self.run_cli("api", "collect", "C1")
        self.assertEqual(code, 0)
        self.assertIn("결과 3행", out)
        code, out, _ = self.run_cli("calib", "score", str(self.work / "results" / "C1"))
        self.assertIn("C1", out)

    def test_plan_argument_errors(self):
        self.client = FakeClient()
        code, _, err = self.run_cli("api", "plan", str(self.batch), "--run", "C2", "--stage", "review")
        self.assertEqual(code, 1)
        self.assertIn("배치 파일을 받지 않습니다", err)
        code, _, err = self.run_cli("api", "plan", "--run", "C2")
        self.assertEqual(code, 1)
        code, _, err = self.run_cli("api", "plan", str(self.batch), "--run", "C2", "--model", "gpt-x")
        self.assertEqual(code, 1)
        self.assertIn("단가를 모르는 모델", err)


if __name__ == "__main__":
    unittest.main()
