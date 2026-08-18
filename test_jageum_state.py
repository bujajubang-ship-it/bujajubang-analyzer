import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import main
from jageum_state import (
    DATASETS,
    atomic_write_json,
    briefing_contract,
    cashflow_shadow,
    evaluate_all,
    evaluate_dataset,
    load_state_file,
    migrate_state,
)


NOW = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)


def payload(bank_rows=6, bank_date="2026-08-18", receivable_total=300, settlement_total=300):
    months = ["2026-07", "2026-08"]
    pl_values = {
        "1. 매출": [1000, 1200],
        "2. 매출원가": [600, 700],
        "3. 매출총이익": [400, 500],
        "4. 판매비 및 일반관리비": [100, 150],
        "5. 영업손익": [300, 350],
        "8. 법인세비용차감전순손익": [280, 330],
        "9. 법인세비용": [20, 30],
        "12. 당기순이익": [260, 300],
    }
    pl_rows = [{"과목": name, "major": True, "월별": dict(zip(months, values)), "집계": sum(values)}
               for name, values in pl_values.items()]
    banks = [{"계정명": "보통예금", "거래처코드": f"A{i}", "거래처명": f"은행{i}",
              "이월잔액": i, "증가": i, "감소": 0, "금일잔액": 100+i}
             for i in range(bank_rows)]
    rec_items = [] if receivable_total == 0 else [
        {"거래처명": "가", "잔액": 100}, {"거래처명": "나", "잔액": receivable_total-100}
    ]
    cp_items = [] if settlement_total == 0 else [
        {"date": "2026-08-20", "amount": 100, "type": "WEEKLY"},
        {"date": "2026-08-27", "amount": settlement_total-100, "type": "WEEKLY"},
    ]
    return {
        "period": "2026-08", "current_month": "2026-08", "months_list": months,
        "months": {month: {"자금현황": banks, "자금의증가": [], "자금의감소": []} for month in months},
        "자금현황": banks,
        "자금의증가": [{"일자": bank_date, "상대계정명": "상품매출", "거래처명": "쿠팡", "적요": "정산", "금액": 1000}],
        "자금의감소": [{"일자": bank_date, "상대계정명": "상품매입", "거래처명": "공급사", "적요": "매입", "금액": 500}],
        "손익월별": {"당기명": "제17기", "months": months, "rows": pl_rows},
        "미수금": {"total": receivable_total, "items": rec_items},
        "정산예정": {
            "기준": "2026-08-18",
            "쿠팡": {"정산예정": settlement_total, "미구매확정": 0, "items": cp_items},
            "네이버": {"지급예정": settlement_total, "미확정": settlement_total, "확정대기": 0,
                      "건수": 0 if settlement_total == 0 else 2, "기준": "2026-08-18"},
        },
    }


def project_payload():
    teams = {
        "쇼핑몰": {"매출": 700, "매출원가": 400, "판관비": 100, "영업손익": 200},
        "대표": {"매출": 500, "매출원가": 300, "판관비": 50, "영업손익": 150},
        "미분류": {"매출": 0, "매출원가": 0, "판관비": 0, "영업손익": 0},
    }
    return {"months": ["2026-08"], "data": {"2026-08": teams},
            "chk": {"2026-08": {"매출": 1200, "매출원가": 700, "판관비": 150, "영업손익": 350}},
            "proj": {"2026-08": {"쇼핑몰": teams["쇼핑몰"], "대표프로젝트": teams["대표"]}}}


class DatasetStateContractTests(unittest.TestCase):
    def test_latest_collection_success_contract_is_consistent(self):
        envelope = evaluate_all(payload(), project_payload(), now=NOW)
        self.assertEqual(set(envelope["datasets"]), set(DATASETS))
        for dataset, state in envelope["datasets"].items():
            self.assertEqual(state["dataset"], dataset)
            self.assertEqual(state["mode"], "shadow")
            self.assertEqual(state["latest_attempt"]["status"], "success")
            self.assertFalse(state["fallback"])
            self.assertTrue(state["served_snapshot"]["snapshot_id"].startswith("sha256:"))
            self.assertIn("rows", state["served_snapshot"])
            self.assertIn("freshness", state)

    def test_latest_failure_keeps_previous_normal_snapshot(self):
        good = evaluate_dataset("receivables", payload(), now=NOW)
        failed = evaluate_dataset("receivables", payload(), previous=good, now=NOW,
                                  attempt_status="failed", attempt_error="collector timeout")
        self.assertEqual(failed["latest_attempt"]["status"], "failed")
        self.assertEqual(failed["served_snapshot"], good["served_snapshot"])
        self.assertTrue(failed["fallback"])

    def test_latest_failure_without_previous_has_no_snapshot(self):
        failed = evaluate_dataset("receivables", payload(), now=NOW,
                                  attempt_status="failed", attempt_error="collector timeout")
        self.assertIsNone(failed["served_snapshot"])
        self.assertFalse(failed["fallback"])

    def test_restored_lkg_is_validated_without_claiming_collection_success(self):
        observed = evaluate_dataset("receivables", payload(), now=NOW, attempt_status="observed")
        self.assertEqual(observed["latest_attempt"]["status"], "unknown")
        self.assertIsNotNone(observed["served_snapshot"])
        self.assertFalse(observed["fallback"])

    def test_empty_bank_candidate_is_rejected(self):
        state = evaluate_dataset("bank_balances", payload(bank_rows=0), now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "failed")
        self.assertIn("비정상 empty", state["validation"]["errors"])

    def test_explicit_zero_is_not_missing(self):
        state = evaluate_dataset("settlement_coupang", payload(settlement_total=0), now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "success")
        self.assertTrue(state["validation"]["checks"]["explicit_zero"])
        self.assertIn("실제 0원인지 확인 필요", state["validation"]["warnings"])

    def test_missing_settlement_value_is_failure(self):
        data = payload(); del data["정산예정"]["쿠팡"]["정산예정"]
        state = evaluate_dataset("settlement_coupang", data, now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "failed")
        self.assertFalse(state["validation"]["checks"]["value_present"])

    def test_rows_drop_rejects_candidate_and_keeps_lkg(self):
        old = evaluate_dataset("bank_balances", payload(bank_rows=10), now=NOW)
        new = evaluate_dataset("bank_balances", payload(bank_rows=1), previous=old, now=NOW)
        self.assertEqual(new["latest_attempt"]["status"], "failed")
        self.assertTrue(new["fallback"])
        self.assertEqual(new["served_snapshot"], old["served_snapshot"])
        self.assertTrue(any("rows 급락" in error for error in new["validation"]["errors"]))

    def test_source_as_of_period_mismatch_is_rejected(self):
        data = payload(bank_date="2026-07-31")
        data["정산예정"]["기준"] = "2026-07-31"
        state = evaluate_dataset("bank_balances", data, now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "failed")
        self.assertTrue(any("source_as_of" in error for error in state["validation"]["errors"]))

    def test_stale_is_visible_without_erasing_valid_snapshot(self):
        data = payload(bank_date="2026-08-01")
        data["정산예정"]["기준"] = "2026-08-01"
        state = evaluate_dataset("bank_balances", data, now=NOW)
        self.assertEqual(state["freshness"]["status"], "stale")
        self.assertEqual(state["latest_attempt"]["status"], "success")
        self.assertIsNotNone(state["served_snapshot"])

    def test_monthly_pl_distinguishes_blank_from_zero(self):
        data = payload()
        revenue = next(row for row in data["손익월별"]["rows"] if row["과목"] == "1. 매출")
        revenue["월별"]["2026-08"] = None
        state = evaluate_dataset("monthly_pl", data, now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "failed")
        self.assertGreater(state["validation"]["checks"]["blank_cells"], 0)

    def test_monthly_pl_equations_are_checked(self):
        data = payload()
        gross = next(row for row in data["손익월별"]["rows"] if row["과목"] == "3. 매출총이익")
        gross["월별"]["2026-08"] = 501
        state = evaluate_dataset("monthly_pl", data, now=NOW)
        self.assertEqual(state["latest_attempt"]["status"], "failed")
        self.assertTrue(state["validation"]["checks"]["equation_errors"])

    def test_project_pl_matches_monthly_totals_and_reports_unclassified(self):
        state = evaluate_dataset("project_pl", payload(), project_payload(), now=NOW)
        self.assertTrue(state["validation"]["checks"]["monthly_pl_match"])
        self.assertIn("unclassified", state["validation"]["metrics"])


class StateStorageTests(unittest.TestCase):
    def test_atomic_publish_retains_previous_snapshot_on_invalid_candidate(self):
        old = evaluate_dataset("receivables", payload(), now=NOW)
        bad_data = payload(); bad_data["미수금"] = {"total": 999, "items": []}
        new = evaluate_dataset("receivables", bad_data, previous=old, now=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            atomic_write_json(path, {"contract_version": 2, "datasets": {"receivables": new}})
            loaded = load_state_file(path)
        self.assertEqual(loaded["datasets"]["receivables"]["served_snapshot"], old["served_snapshot"])

    def test_migration_accepts_legacy_dataset_map(self):
        migrated = migrate_state({"bank_balances": {"dataset": "bank_balances"}, "unknown": {}})
        self.assertEqual(migrated["contract_version"], 2)
        self.assertEqual(set(migrated["datasets"]), {"bank_balances"})

    def test_kv_failure_does_not_discard_local_atomic_state(self):
        envelope = evaluate_all(payload(), project_payload(), now=NOW)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(main, "JAGEUM_DATASET_STATE_FILE", Path(tmp) / "state.json"), \
             patch.object(main, "_kv_backup_checked", return_value={"ok": False, "error": "timeout"}):
            result = main._publish_dataset_state_envelope(envelope)
            loaded = load_state_file(Path(tmp) / "state.json")
        self.assertTrue(result["local"]["ok"])
        self.assertFalse(result["kv"]["ok"])
        self.assertEqual(set(loaded["datasets"]), set(DATASETS))

    def test_render_restart_restores_state_from_kv(self):
        envelope = evaluate_all(payload(), project_payload(), now=NOW)
        old_cache = main._JAGEUM_DATASET_STATE_CACHE.get("data")
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(main, "JAGEUM_DATASET_STATE_FILE", Path(tmp) / "missing.json"), \
                 patch.object(main, "_kv_restore", return_value=envelope):
                main._JAGEUM_DATASET_STATE_CACHE["data"] = None
                restored = main._load_dataset_state_envelope()
                self.assertEqual(set(restored["datasets"]), set(DATASETS))
                self.assertTrue((Path(tmp) / "missing.json").exists())
        finally:
            main._JAGEUM_DATASET_STATE_CACHE["data"] = old_cache

    def test_existing_phase2_state_adds_source_basis_without_changing_snapshot_id(self):
        envelope = evaluate_all(payload(), project_payload(), now=NOW)
        envelope["initialization"] = "legacy_lkg_observation"
        original_ids = {}
        for key, state in envelope["datasets"].items():
            original_ids[key] = state["served_snapshot"]["snapshot_id"]
            state["served_snapshot"].pop("source_as_of_kind", None)
        with patch.object(main, "_load_dataset_state_envelope", return_value=envelope), \
             patch.object(main, "_publish_dataset_state_envelope", return_value={"local": {"ok": True}, "kv": {"ok": True}}):
            migrated = main._seed_or_load_dataset_states(payload=payload(), project_payload=project_payload())
        for key, state in migrated["datasets"].items():
            self.assertEqual(state["served_snapshot"]["snapshot_id"], original_ids[key])
            self.assertTrue(state["served_snapshot"]["source_as_of_kind"])


class ShadowAndBriefingTests(unittest.TestCase):
    def test_cashflow_shadow_separates_non_operating_inflows(self):
        data = payload()
        data["자금의증가"] += [
            {"상대계정명": "단기차입금", "거래처명": "은행", "적요": "대출 실행", "금액": 500},
            {"상대계정명": "가수금", "거래처명": "대표", "적요": "대표 입금", "금액": 300},
            {"상대계정명": "잡이익", "거래처명": "국세청", "적요": "부가세 환급", "금액": 200},
        ]
        result = cashflow_shadow(data)
        self.assertEqual(result["categories"]["in"]["financing"]["amount"], 500)
        self.assertEqual(result["categories"]["in"]["owner_related"]["amount"], 300)
        self.assertEqual(result["categories"]["in"]["unclassified"]["amount"], 200)
        self.assertEqual(result["categories"]["in"]["operating"]["amount"], 1000)

    def test_briefing_contract_contains_metrics_not_raw_rows(self):
        data = payload(); states = evaluate_all(data, project_payload(), now=NOW)
        result = briefing_contract(data, states)
        self.assertIn("metrics", result)
        self.assertIn("anomalies", result)
        self.assertIn("limitations", result)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("자금의증가", encoded)
        for item in result["metrics"]:
            self.assertIn("snapshot_id", item)
            self.assertIn("calculation_version", item)

    def test_backend_flags_revenue_up_while_cash_down(self):
        data = payload()
        data["months"]["2026-07"]["자금현황"] = [
            {**row, "금일잔액": row["금일잔액"]+1000} for row in data["자금현황"]
        ]
        states = evaluate_all(data, project_payload(), now=NOW)
        result = briefing_contract(data, states)
        self.assertTrue(any(item["type"] == "revenue_up_cash_down" for item in result["anomalies"]))
        self.assertGreater(result["comparisons"]["revenue"]["change"], 0)
        self.assertLess(result["comparisons"]["bank_balance"]["change"], 0)

    def test_receivables_growth_uses_previous_snapshot_metrics(self):
        old = evaluate_all(payload(receivable_total=300), project_payload(), now=NOW)
        new = evaluate_all(payload(receivable_total=500), project_payload(), previous_states=old["datasets"], now=NOW)
        result = briefing_contract(payload(receivable_total=500), new)
        self.assertTrue(any(item["type"] == "receivables_increase" for item in result["anomalies"]))

    def test_briefing_uses_lkg_metric_when_latest_candidate_failed(self):
        data = payload(receivable_total=300)
        good = evaluate_all(data, project_payload(), now=NOW)
        bad = payload(receivable_total=999); bad["미수금"]["items"] = []
        failed = evaluate_all(bad, project_payload(), previous_states=good["datasets"], now=NOW)
        result = briefing_contract(bad, failed)
        receivable = next(item for item in result["metrics"] if item["name"] == "receivables")
        self.assertEqual(receivable["value"], 300)
        self.assertTrue(receivable["fallback"])


class EndpointPermissionTests(unittest.TestCase):
    class Request:
        def __init__(self, role):
            self.cookies = {"jg_session": main._make_token(role, "테스트")}

    def test_boss_and_staff_can_read_dataset_contract(self):
        envelope = evaluate_all(payload(), project_payload(), now=NOW)
        with patch.object(main, "_seed_or_load_dataset_states", return_value=envelope):
            for role in ("boss", "staff"):
                response = main.jageum_dataset_states(self.Request(role))
                self.assertEqual(response.status_code, 200)

    def test_sales_and_unauthenticated_users_cannot_read_company_metrics(self):
        for role in ("sales", ""):
            response = main.jageum_dataset_states(self.Request(role))
            self.assertEqual(response.status_code, 401)


class FrontendTrustUiTests(unittest.TestCase):
    def test_compact_status_summary_and_easy_labels_are_present(self):
        html = Path("static/jageum.html").read_text(encoding="utf-8")
        self.assertIn('id="dataset_status_card"', html)
        self.assertIn("이전 정상본", html)
        self.assertIn("확인 필요", html)
        self.assertIn("수집 실패", html)
        self.assertIn("수동 확인", html)
        self.assertIn("오래된 값", html)
        for dataset in DATASETS:
            self.assertIn(dataset, html)

    def test_existing_business_numbers_remain_on_legacy_render_path(self):
        html = Path("static/jageum.html").read_text(encoding="utf-8")
        self.assertIn("const tin=sum(incBiz,x=>x.금액)", html)
        self.assertIn("k_net.textContent=won(tin-tout)", html)
        self.assertIn("const cash=sum(bal,x=>x.금일잔액||0)", html)
        self.assertIn("function loanList(){ return manualGet('대출',LOAN_SEED); }", html)


if __name__ == "__main__":
    unittest.main()
