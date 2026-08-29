import os
import unittest
from pathlib import Path
from unittest.mock import patch

from finance_assistant import (
    FINANCE_MODEL,
    build_cash_driver_context,
    build_openai_response_body,
    choose_reasoning_effort,
    classify_finance_transaction,
    response_text_delta,
)


def tx(amount, account, party="", memo="", project="", day="2026-08-10"):
    return {"일자": day, "금액": amount, "상대계정명": account, "거래처명": party,
            "적요": memo, "프로젝트명": project}


class TransactionClassificationTests(unittest.TestCase):
    def test_representative_project_name_alone_is_not_owner_withdrawal(self):
        row = tx(1_000_000, "상품매입", "주방업체", project="대표프로젝트")
        category, _ = classify_finance_transaction(row, "out")
        self.assertEqual(category, "inventory_supplier_outflow")

    def test_owner_money_is_split_by_direction(self):
        row = tx(30_000_000, "가지급금", "대표이사", "대표자 인출")
        self.assertEqual(classify_finance_transaction(row, "out")[0], "owner_withdrawal")
        self.assertEqual(classify_finance_transaction(row, "in")[0], "owner_contribution")

    def test_solar_prepaid_loan_and_tax_are_distinct(self):
        cases = [
            (tx(50_000_000, "시설장치", "종보전기", "태양광 설비"), "solar_capex"),
            (tx(20_000_000, "선급금", "납품업체", "선금 지급"), "prepaid_outflow"),
            (tx(10_000_000, "장기차입금", "은행", "원금상환"), "loan_repayment"),
            (tx(8_000_000, "세금과공과", "세무서", "부가세"), "tax_outflow"),
        ]
        for row, expected in cases:
            self.assertEqual(classify_finance_transaction(row, "out")[0], expected)


class CashDriverContextTests(unittest.TestCase):
    def payload(self):
        ordinary = [tx(100_000+i, "지급수수료", f"일반거래처{i}") for i in range(30)]
        return {
            "current_month": "2026-08",
            "months": {
                "2026-07": {
                    "자금의증가": [tx(120_000_000, "상품매출", "고객사")],
                    "자금의감소": [tx(10_000_000, "상품매입", "공급사")],
                },
                "2026-08": {
                    "자금의증가": [tx(150_000_000, "상품매출", "고객사")],
                    "자금의감소": ordinary + [
                        tx(40_000_000, "가지급금", "대표이사", "대표자 인출"),
                        tx(70_000_000, "시설장치", "종보전기", "태양광 설비 투자"),
                        tx(25_000_000, "선급금", "대림주방", "선금 지급"),
                        tx(999_999_999, "", "내부", "계좌이체"),
                    ],
                },
            },
            "정산예정": {
                "쿠팡": {"정산예정": 12_000_000, "미구매확정": 3_000_000},
                "네이버": {"지급예정": 4_000_000},
            },
            "미수금": {"total": 60_000_000},
        }

    def test_many_ledger_rows_still_surface_material_cash_drivers(self):
        manual = {"선급금": [{"잔액": 80_000_000}], "카페24": 5_000_000,
                  "대출": [{"잔액": 300_000_000}]}
        snapshots = [
            {"date": "2026-07-01", "통장": 200_000_000, "선급금": 30_000_000},
            {"date": "2026-08-15", "통장": 90_000_000, "선급금": 80_000_000},
        ]
        result = build_cash_driver_context(self.payload(), manual, snapshots)
        self.assertEqual(result["driver_totals"]["owner_withdrawal"]["amount"], 40_000_000)
        self.assertEqual(result["driver_totals"]["solar_capex"]["amount"], 70_000_000)
        self.assertEqual(result["driver_totals"]["prepaid_outflow"]["amount"], 25_000_000)
        self.assertEqual(result["cash_tied_up_now"]["supplier_prepaids_manual"], 80_000_000)
        self.assertEqual(result["cash_tied_up_now"]["receivables"], 60_000_000)
        self.assertEqual(result["asset_snapshot_comparison"]["changes"]["통장"]["change"], -110_000_000)
        self.assertEqual(result["asset_snapshot_comparison"]["changes"]["선급금"]["change"], 50_000_000)
        self.assertNotIn("internal_transfer", result["driver_totals"])
        evidence = result["driver_totals"]["solar_capex"]["largest_transactions"][0]
        self.assertEqual(evidence["counterparty"], "종보전기")
        self.assertIn("태양광", evidence["memo"])

    def test_context_states_data_limitations_without_inventing_inventory_balance(self):
        result = build_cash_driver_context(self.payload())
        self.assertTrue(any("재고" in item for item in result["limitations"]))
        self.assertNotIn("inventory_balance", result)


class OpenAIRequestTests(unittest.TestCase):
    def test_simple_chat_is_low_reasoning_and_financial_diagnosis_is_medium(self):
        self.assertEqual(choose_reasoning_effort([{"role": "user", "content": "안녕하세요"}]), "low")
        self.assertEqual(choose_reasoning_effort([{"role": "user", "content": "매출은 느는데 왜 통장에 돈이 안 모여?"}]), "medium")

    def test_responses_request_uses_flagship_streaming_and_no_storage(self):
        body = build_openai_response_body("rules", [{"role": "user", "content": "왜 돈이 없지?"}], "safe")
        self.assertEqual(body["model"], FINANCE_MODEL)
        self.assertEqual(body["model"], "gpt-5.6-sol")
        self.assertEqual(body["reasoning"]["effort"], "medium")
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"])
        self.assertEqual(body["safety_identifier"], "safe")

    def test_stream_delta_parser_ignores_other_events(self):
        self.assertEqual(response_text_delta({"type": "response.output_text.delta", "delta": "답"}), "답")
        self.assertEqual(response_text_delta({"type": "response.completed", "delta": "잘못"}), "")

    def test_backend_and_frontend_contain_streaming_fallback_contract(self):
        main = Path("main.py").read_text(encoding="utf-8")
        html = Path("static/jageum.html").read_text(encoding="utf-8")
        self.assertIn('@app.post("/jageum/api/chat/stream")', main)
        self.assertIn("OPENAI_API_KEY", main)
        self.assertIn("response.output_text.delta", Path("finance_assistant.py").read_text(encoding="utf-8"))
        self.assertIn("/jageum/api/chat/stream", html)
        self.assertIn("chatClaudeFallback", html)


class StreamEndpointTests(unittest.TestCase):
    def test_missing_openai_key_returns_explicit_safe_fallback(self):
        import main
        from fastapi.testclient import TestClient

        with patch.object(main, "_jageum_auth", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = TestClient(main.app).post(
                "/jageum/api/chat/stream",
                json={"messages": [{"role": "user", "content": "돈이 왜 안 모이지?"}]},
            )
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["fallback"])


if __name__ == "__main__":
    unittest.main()
