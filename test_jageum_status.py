import asyncio
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

import main


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


class FakeIngestRequest:
    def __init__(self, payload: bytes):
        self.headers = {"x-ingest-secret": main.JAGEUM_INGEST_SECRET}
        self._payload = payload

    async def body(self):
        return self._payload


class FakeJsonRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class JageumStatusNormalizationTests(unittest.TestCase):
    def test_running_legacy_response_is_running(self):
        self.assertEqual(main._normalize_scrape_status({"running": True}), "running")

    def test_explicit_success_is_success(self):
        self.assertEqual(main._normalize_scrape_status({"state": "success"}), "success")
        self.assertEqual(main._normalize_scrape_status({"status": "done"}), "success")

    def test_running_false_alone_is_unknown_not_success(self):
        self.assertEqual(
            main._normalize_scrape_status({"running": False, "msg": "완료"}),
            "unknown",
        )

    @patch.object(main.httpx, "get", return_value=FakeResponse(payload={"running": True}))
    def test_running_status_response_is_running(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["state"], "running")
        self.assertNotIn("✅", body["msg"])

    @patch.object(main.httpx, "get", return_value=FakeResponse(payload={"state": "success"}))
    def test_success_status_response_is_success(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["state"], "success")
        self.assertIn("✅", body["msg"])

    @patch.object(main.httpx, "get", return_value=FakeResponse(payload={"status": "failed"}))
    def test_failed_status_response_is_failed(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["state"], "failed")
        self.assertIn("❌", body["msg"])

    @patch.object(main.httpx, "get", side_effect=httpx.TimeoutException("timeout"))
    def test_lightsail_timeout_returns_502_unknown(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["state"], "unknown")
        self.assertNotIn("✅", body["msg"])

    @patch.object(main.httpx, "get", return_value=FakeResponse(status_code=500, payload={}))
    def test_lightsail_500_returns_502_unknown(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["state"], "unknown")
        self.assertEqual(body["upstream_status"], 500)

    @patch.object(
        main.httpx,
        "get",
        return_value=FakeResponse(status_code=200, json_error=ValueError("bad json")),
    )
    def test_malformed_status_returns_502_unknown(self, _get):
        response = main._scrape_status_response("/scrape_jageum_status")
        body = response_json(response)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["state"], "unknown")


class JageumFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._cache = dict(main._JAGEUM_CACHE)
        self._status_cache = dict(main._JAGEUM_STATUS_CACHE)

    def tearDown(self):
        main._JAGEUM_CACHE.clear()
        main._JAGEUM_CACHE.update(self._cache)
        main._JAGEUM_STATUS_CACHE.clear()
        main._JAGEUM_STATUS_CACHE.update(self._status_cache)

    def test_previous_data_with_unconfirmed_status_is_unknown_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, "JAGEUM_FILE", Path(tmp) / "jageum_data.json"), \
                 patch.object(main, "JAGEUM_STATUS_FILE", Path(tmp) / "jageum_status.json"):
                main._JAGEUM_CACHE.update({
                    "data": {"자금현황": [{"잔액": 1}]},
                    "serving_fallback": True,
                })
                main._JAGEUM_STATUS_CACHE["data"] = {
                    "state": "success",
                    "last_published_at": "2026-08-17T00:00:00+00:00",
                }
                payload = main._jageum_status_payload()
                self.assertTrue(payload["has_data"])
                self.assertTrue(payload["serving_fallback"])
                self.assertEqual(payload["state"], "unknown")

    def test_explicit_failure_is_preserved_while_serving_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, "JAGEUM_FILE", Path(tmp) / "jageum_data.json"), \
                 patch.object(main, "JAGEUM_STATUS_FILE", Path(tmp) / "jageum_status.json"):
                main._JAGEUM_CACHE.update({
                    "data": {"자금현황": [{"잔액": 1}]},
                    "serving_fallback": True,
                })
                main._JAGEUM_STATUS_CACHE["data"] = {"state": "failed", "error": "scrape failed"}
                payload = main._jageum_status_payload()
                self.assertTrue(payload["has_data"])
                self.assertTrue(payload["serving_fallback"])
                self.assertEqual(payload["state"], "failed")

    def test_no_data_is_reported_without_guessing_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, "JAGEUM_FILE", Path(tmp) / "missing.json"), \
                 patch.object(main, "JAGEUM_STATUS_FILE", Path(tmp) / "missing-status.json"):
                main._JAGEUM_CACHE.update({"data": None, "serving_fallback": False})
                main._JAGEUM_STATUS_CACHE["data"] = None
                payload = main._jageum_status_payload()
                self.assertFalse(payload["has_data"])
                self.assertEqual(payload["state"], "unknown")

    def test_failed_collection_without_previous_data_stays_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, "JAGEUM_FILE", Path(tmp) / "missing.json"), \
                 patch.object(main, "JAGEUM_STATUS_FILE", Path(tmp) / "missing-status.json"):
                main._JAGEUM_CACHE.update({"data": None, "serving_fallback": False})
                main._JAGEUM_STATUS_CACHE["data"] = {
                    "state": "failed", "error": "collection failed"
                }
                payload = main._jageum_status_payload()
                self.assertFalse(payload["has_data"])
                self.assertEqual(payload["state"], "failed")
                self.assertEqual(payload["error"], "collection failed")

    def test_successful_ingest_records_snapshot_without_changing_payload(self):
        incoming = {
            "period": "2026-08",
            "months": {"2026-08": {"자금현황": [], "자금의증가": [], "자금의감소": []}},
            "months_list": ["2026-08"],
            "current_month": "2026-08",
            "자금현황": [{"계정명": "보통예금", "금일잔액": 100}],
            "자금의증가": [],
            "자금의감소": [],
        }
        raw = json.dumps(incoming, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            data_file = Path(tmp) / "jageum_data.json"
            status_file = Path(tmp) / "jageum_status.json"
            with patch.object(main, "JAGEUM_FILE", data_file), \
                 patch.object(main, "JAGEUM_STATUS_FILE", status_file), \
                 patch.object(main, "_kv_backup", return_value=None):
                main._JAGEUM_CACHE.update({"data": None, "serving_fallback": False})
                main._JAGEUM_STATUS_CACHE["data"] = None
                response = asyncio.run(main.jageum_ingest(FakeIngestRequest(raw)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(data_file.read_text(encoding="utf-8")), incoming)
                status = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertEqual(status["state"], "success")
                self.assertTrue(status["snapshot_id"].startswith("sha256:"))
                self.assertTrue(status["last_received_at"])
                self.assertTrue(status["last_published_at"])
                self.assertIsNone(status["source_as_of"])

    def test_received_time_is_never_substituted_for_source_as_of(self):
        self.assertIsNone(main._explicit_source_as_of({"period": "2026-08"}))


class JageumFrontendRegressionTests(unittest.TestCase):
    def test_only_explicit_success_uses_green_completion(self):
        html = Path("static/jageum.html").read_text(encoding="utf-8")
        match = re.search(
            r"function collectionStateUi\(state\)\{(?P<body>.*?)\n\}", html, re.S
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn("if(state==='success')", body)
        self.assertIn("✅ 데이터 수집이 완료되었습니다',color:'#059669'", body)
        self.assertIn("⚠️ 수집 상태를 확인할 수 없습니다',color:'#d97706'", body)
        self.assertIn("❌ 데이터 수집에 실패했습니다',color:'#dc2626'", body)
        self.assertNotIn("else{_jgFinish('✅", html)
        self.assertNotIn("else _finishRefresh('✅", html)
        self.assertIn("if(state==='success'&&typeof load==='function')load()", html)


class ManualDataContractTests(unittest.TestCase):
    NOW = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)

    def legacy_fixture(self):
        return {
            "선급금": [{"거래처": "거래처", "잔액": 153_421_926}],
            "카페24": 64_593_306,
            "수정일": {"선급금": "2026-08-14", "카페24": "2026-08-14"},
            "경리확인": {
                "선급금": "2026-08-14",
                "카페24": "2026-08-14",
                "대출": "2026-08-14",
            },
        }

    def test_legacy_values_migrate_without_changing_amounts(self):
        result = main._normalize_manual_payload(self.legacy_fixture(), now=self.NOW)
        datasets = result["_manual_meta"]["datasets"]
        prepaids = datasets["manual_prepaids"]
        cafe24 = datasets["settlement_cafe24"]
        loans = datasets["manual_loans"]

        self.assertEqual(sum(x["잔액"] for x in prepaids["items"]), 153_421_926)
        self.assertEqual(cafe24["value"], 64_593_306)
        self.assertEqual(prepaids["status"], "confirmed")
        self.assertEqual(cafe24["status"], "confirmed")
        self.assertEqual(prepaids["last_confirmed_at"], "2026-08-14")
        self.assertEqual(cafe24["last_confirmed_at"], "2026-08-14")
        self.assertIsNone(prepaids["confirmed_by"])
        self.assertNotIn("대출", result)
        self.assertEqual(loans["status"], "unknown")
        self.assertIsNone(loans["last_confirmed_at"])
        self.assertEqual(loans["legacy_reference"]["value"], 450_000_000)
        self.assertEqual(
            loans["legacy_reference"]["source"], "frontend_hardcoded_default"
        )
        self.assertEqual(loans["legacy_reference"]["checklist_confirmed_at"], "2026-08-14")

    def test_missing_value_is_unknown(self):
        result = main._normalize_manual_payload({}, now=self.NOW)
        datasets = result["_manual_meta"]["datasets"]
        self.assertEqual(datasets["manual_prepaids"]["status"], "unknown")
        self.assertEqual(datasets["settlement_cafe24"]["status"], "unknown")
        self.assertEqual(datasets["manual_loans"]["status"], "unknown")

    def test_explicit_zero_is_confirmed_not_unknown(self):
        result = main._normalize_manual_payload(
            {"카페24": 0},
            confirmed_by="경리",
            confirm_keys=["카페24"],
            changed_keys=["카페24"],
            now=self.NOW,
        )
        cafe24 = result["_manual_meta"]["datasets"]["settlement_cafe24"]
        self.assertEqual(cafe24["value"], 0)
        self.assertEqual(cafe24["status"], "confirmed")
        self.assertEqual(cafe24["confirmed_by"], "경리")

    def test_value_becomes_stale_after_seven_days(self):
        result = main._normalize_manual_payload(
            self.legacy_fixture(),
            now=datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc),
        )
        datasets = result["_manual_meta"]["datasets"]
        self.assertEqual(datasets["manual_prepaids"]["status"], "stale")
        self.assertEqual(datasets["settlement_cafe24"]["status"], "stale")

    def test_unchanged_confirmation_updates_confirmation_only(self):
        first = main._normalize_manual_payload(
            self.legacy_fixture(), now=self.NOW
        )
        before = first["_manual_meta"]["datasets"]["manual_prepaids"]
        confirmed = main._normalize_manual_payload(
            first,
            confirmed_by="경리",
            confirm_keys=["선급금"],
            now=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc),
        )
        after = confirmed["_manual_meta"]["datasets"]["manual_prepaids"]
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertNotEqual(after["last_confirmed_at"], before["last_confirmed_at"])
        self.assertEqual(after["confirmed_by"], "경리")

    def test_storage_error_retains_values_but_marks_error(self):
        result = main._normalize_manual_payload(
            self.legacy_fixture(), now=self.NOW, storage_error="KV timeout"
        )
        datasets = result["_manual_meta"]["datasets"]
        self.assertEqual(datasets["manual_prepaids"]["status"], "error")
        self.assertEqual(datasets["settlement_cafe24"]["status"], "error")
        self.assertEqual(
            sum(x["잔액"] for x in datasets["manual_prepaids"]["items"]),
            153_421_926,
        )
        self.assertEqual(datasets["manual_prepaids"]["status_before_error"], "confirmed")


class ManualSaveReliabilityTests(unittest.TestCase):
    def test_kv_failure_is_partial_not_success(self):
        req = FakeJsonRequest({
            "data": {"카페24": 0},
            "confirm_keys": ["카페24"],
            "changed_keys": ["카페24"],
        })
        with patch.object(main, "_jageum_auth", return_value=True), \
             patch.object(main, "_jageum_who", return_value="경리"), \
             patch.object(main, "_manual_atomic_write", return_value={"ok": True, "error": None}), \
             patch.object(main, "_kv_backup_checked", return_value={"ok": False, "error": "timeout"}):
            response = asyncio.run(main.jageum_manual(req))
        body = response_json(response)
        self.assertEqual(response.status_code, 207)
        self.assertFalse(body["ok"])
        self.assertTrue(body["saved"])
        self.assertEqual(body["state"], "partial")
        self.assertTrue(body["storage"]["local"]["ok"])
        self.assertFalse(body["storage"]["kv"]["ok"])
        self.assertEqual(
            body["manual"]["_manual_meta"]["datasets"]["settlement_cafe24"]["status"],
            "error",
        )

    def test_both_storage_failures_return_503(self):
        req = FakeJsonRequest({
            "data": {"카페24": 100},
            "confirm_keys": ["카페24"],
            "changed_keys": ["카페24"],
        })
        with patch.object(main, "_jageum_auth", return_value=True), \
             patch.object(main, "_jageum_who", return_value="경리"), \
             patch.object(main, "_manual_atomic_write", return_value={"ok": False, "error": "disk"}), \
             patch.object(main, "_kv_backup_checked", return_value={"ok": False, "error": "network"}):
            response = asyncio.run(main.jageum_manual(req))
        body = response_json(response)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(body["ok"])
        self.assertFalse(body["saved"])
        self.assertEqual(body["state"], "failed")

    def test_local_and_kv_success_are_complete_success(self):
        req = FakeJsonRequest({
            "data": {"대출": [{"거래처": "대출", "잔액": 450_000_000}]},
            "confirm_keys": ["대출"],
            "changed_keys": ["대출"],
        })
        with patch.object(main, "_jageum_auth", return_value=True), \
             patch.object(main, "_jageum_who", return_value="경리"), \
             patch.object(main, "_manual_atomic_write", return_value={"ok": True, "error": None}), \
             patch.object(main, "_kv_backup_checked", return_value={"ok": True, "error": None}):
            response = asyncio.run(main.jageum_manual(req))
        body = response_json(response)
        loan = body["manual"]["_manual_meta"]["datasets"]["manual_loans"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(loan["status"], "confirmed")
        self.assertNotIn("legacy_reference", loan)

    def test_checked_kv_rejection_is_failure_even_with_http_200(self):
        with patch.object(
            main.httpx, "post", return_value=FakeResponse(200, {"ok": False, "error": "rejected"})
        ):
            result = main._kv_backup_checked("jageum_manual", {})
        self.assertFalse(result["ok"])
        self.assertIn("rejected", result["error"])

    def test_storage_metadata_write_failure_is_partial_and_error(self):
        req = FakeJsonRequest({
            "data": {"카페24": 1},
            "confirm_keys": ["카페24"],
            "changed_keys": ["카페24"],
        })
        with patch.object(main, "_jageum_auth", return_value=True), \
             patch.object(main, "_jageum_who", return_value="경리"), \
             patch.object(main, "_manual_atomic_write", side_effect=[
                 {"ok": True, "error": None},
                 {"ok": False, "error": "metadata write failed"},
             ]), \
             patch.object(main, "_kv_backup_checked", return_value={"ok": True, "error": None}):
            response = asyncio.run(main.jageum_manual(req))
        body = response_json(response)
        self.assertEqual(response.status_code, 207)
        self.assertFalse(body["ok"])
        self.assertTrue(body["saved"])
        self.assertEqual(
            body["manual"]["_manual_meta"]["datasets"]["settlement_cafe24"]["status"],
            "error",
        )


class ManualRestoreReliabilityTests(unittest.TestCase):
    def test_restore_failure_is_error_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(main, "JAGEUM_MANUAL_FILE", Path(tmp) / "missing.json"), \
             patch.object(main.httpx, "get", side_effect=httpx.TimeoutException("timeout")):
            result = main._load_manual()
        datasets = result["_manual_meta"]["datasets"]
        self.assertEqual(datasets["manual_prepaids"]["status"], "error")
        self.assertEqual(datasets["settlement_cafe24"]["status"], "error")
        self.assertEqual(datasets["manual_loans"]["status"], "error")

    def test_absent_local_and_absent_kv_are_unknown_not_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(main, "JAGEUM_MANUAL_FILE", Path(tmp) / "missing.json"), \
             patch.object(main.httpx, "get", return_value=FakeResponse(200, {"ok": True, "data": None})):
            result = main._load_manual()
        datasets = result["_manual_meta"]["datasets"]
        self.assertEqual(datasets["manual_prepaids"]["status"], "unknown")
        self.assertEqual(datasets["settlement_cafe24"]["status"], "unknown")
        self.assertEqual(datasets["manual_loans"]["status"], "unknown")


class ManualFrontendRegressionTests(unittest.TestCase):
    def test_weekly_manual_confirmation_ui_and_provisional_loan_are_present(self):
        html = Path("static/jageum.html").read_text(encoding="utf-8")
        self.assertIn("최근 7일 자금 확인 ${done}/${weekly.length} 완료", html)
        self.assertIn(
            "이카운트에서 자동 확인되지 않는 항목을 경리가 7일에 한 번 확인합니다.",
            html,
        )
        self.assertIn("금액 변동 없음 / 확인 완료", html)
        self.assertIn("${date?date+' 확인':'확인 기록 없음'}", html)
        self.assertIn('<summary style="cursor:pointer;', html)
        self.assertIn("<b>월간 운영 체크</b>", html)
        self.assertNotIn("<details open", html)
        self.assertIn("기존 참고금액", html)
        self.assertIn("관리상 잠정값 포함", html)
        self.assertIn("legacy_reference", html)
        self.assertIn("local?'성공':'실패'", html)
        self.assertIn("KV 백업 ${kv?'성공':'실패'}", html)


if __name__ == "__main__":
    unittest.main()
