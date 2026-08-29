"""자금 AI 비서용 결정론적 현금흐름 근거와 OpenAI 요청 구성.

모델이 수많은 원장 행을 직접 훑으며 숫자를 다시 계산하지 않도록, 거래 분류·합계·
대표 근거행·데이터 한계를 코드로 먼저 만든다. 기존 대시보드 KPI는 변경하지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


FINANCE_MODEL = "gpt-5.6-sol"

CATEGORY_LABELS = {
    "owner_withdrawal": "대표자 관련 회사자금 유출(인출·가지급금·가수금 상환 포함)",
    "owner_contribution": "대표자 관련 회사자금 유입(가수금·개인자금 투입 포함)",
    "solar_capex": "태양광 설비 투자",
    "solar_income": "태양광 관련 수입",
    "other_capex": "기계·시설 등 기타 투자",
    "loan_repayment": "대출·차입금 원금 상환",
    "loan_inflow": "대출·차입금 유입",
    "prepaid_outflow": "선급금·선금 지급",
    "tax_outflow": "세금 납부",
    "inventory_supplier_outflow": "상품·원재료·매입처 지급",
    "operating_inflow": "영업 현금 유입",
    "operating_outflow": "기타 영업 현금 유출",
    "other_inflow": "기타·미분류 유입",
    "other_outflow": "기타·미분류 유출",
    "internal_transfer": "내부 계좌이체",
}

_COMPLEX_QUESTION = re.compile(
    r"왜|원인|분석|진단|비교|추세|늘었|줄었|증가|감소|안\s*모|현금흐름|"
    r"통장|자금|인출|가지급|가수금|태양광|설비|투자|선급금|선금|미수금|"
    r"정산|재고|매입|상환|대출|세금|수익.*현금|매출.*돈|돈.*매출",
    re.I,
)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return 0.0
    return 0.0


def _text(row: dict, *keys: str) -> str:
    return " ".join(str(row.get(key) or "") for key in keys).strip()


def classify_finance_transaction(row: dict, direction: str) -> tuple[str, str]:
    """원장 한 행을 보수적으로 분류한다. direction은 ``in`` 또는 ``out``이다."""
    account = _text(row, "상대계정명", "계정명")
    party_memo = _text(row, "거래처명", "적요")
    core = f"{account} {party_memo}".strip()
    all_text = f"{core} {_text(row, '프로젝트명', '부서명')}".strip()

    if not account or re.search(r"내부이체|계좌이체|자금이동|대체입금|대체출금", all_text):
        return "internal_transfer", "계정 미지정 또는 내부 자금이동 표시"

    # 프로젝트명에 '대표'가 있다는 이유만으로 개인 인출로 보지 않는다.
    if re.search(r"가수금|가지급금|인출금|대표이사|대표자|주주.?임원|조준연|이효순|개인자금", core):
        if direction == "out":
            return "owner_withdrawal", "대표자·임원 관련 계정/거래처/적요"
        return "owner_contribution", "대표자·임원 관련 계정/거래처/적요"

    if direction == "out" and re.search(r"태양광|태양열|종보전기|태양전지|발전설비", core):
        return "solar_capex", "태양광·발전설비 관련 출금"
    if direction == "in" and re.search(r"태양광|태양열|한전경산|전력판매|발전수익", core):
        return "solar_income", "태양광·전력판매 관련 입금"
    if direction == "out" and re.search(r"기계장치|시설장치|유형자산|비품구입|차량운반구", account):
        return "other_capex", "자산 계정으로 지출"

    if re.search(r"차입금|대출|원금상환|원금 상환", core):
        if direction == "out":
            return "loan_repayment", "대출·차입금 원금 관련 출금"
        return "loan_inflow", "대출·차입금 관련 입금"
    if direction == "out" and re.search(r"선급금|선급비용|선금 지급|계약금 지급", core):
        return "prepaid_outflow", "선급금·선금 관련 출금"
    if direction == "out" and re.search(r"법인세|부가세|소득세|지방세|세금과공과|국세|세무서|구청", core):
        return "tax_outflow", "세금·공과 관련 출금"
    if direction == "out" and re.search(r"상품매입|원재료|외상매입금|매입채무|상품 대금|매입대금", core):
        return "inventory_supplier_outflow", "상품·원재료·매입처 관련 출금"

    if direction == "in" and re.search(r"매출|외상매출금|쿠팡|네이버|카페24|카페이십사|판매", core):
        return "operating_inflow", "매출·플랫폼 정산 관련 입금"
    if direction == "out" and re.search(
        r"매입|급여|법인카드|수수료|운반|광고|임차|보험|복리|소모품|지급수수료", core
    ):
        return "operating_outflow", "통상 영업비용 관련 출금"
    return ("other_inflow", "분류 규칙에 없는 입금") if direction == "in" else (
        "other_outflow", "분류 규칙에 없는 출금"
    )


def _public_transaction(row: dict, category: str, reason: str) -> dict:
    return {
        "date": str(row.get("일자") or "")[:10],
        "amount": round(_number(row.get("금액"))),
        "account": str(row.get("상대계정명") or row.get("계정명") or "")[:80],
        "counterparty": str(row.get("거래처명") or "")[:80],
        "memo": str(row.get("적요") or "")[:120],
        "project": str(row.get("프로젝트명") or "")[:80],
        "category": category,
        "classification_reason": reason,
    }


def _manual_amount(manual: dict, key: str) -> float | None:
    if key not in manual:
        return None
    value = manual.get(key)
    if isinstance(value, list):
        return sum(_number(row.get("잔액")) for row in value if isinstance(row, dict))
    return _number(value)


def _snapshot_changes(snapshots: list[dict] | None) -> dict:
    rows = [row for row in (snapshots or []) if isinstance(row, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("date") or ""))]
    rows.sort(key=lambda row: row["date"])
    if not rows:
        return {"available": False, "reason": "자산 스냅샷 없음"}
    latest = rows[-1]
    latest_date = date.fromisoformat(latest["date"])
    candidates = [row for row in rows[:-1] if date.fromisoformat(row["date"]) <= latest_date - timedelta(days=28)]
    previous = candidates[-1] if candidates else (rows[0] if len(rows) > 1 else None)
    fields = ("통장", "정산예정", "미수금", "선급금", "투자", "대출", "실질총자산", "순자산")
    changes = {}
    for field in fields:
        current = _number(latest.get(field)) if field in latest else None
        prior = _number(previous.get(field)) if previous and field in previous else None
        changes[field] = {
            "current": round(current) if current is not None else None,
            "previous": round(prior) if prior is not None else None,
            "change": round(current - prior) if current is not None and prior is not None else None,
        }
    return {
        "available": True,
        "latest_date": latest["date"],
        "comparison_date": previous.get("date") if previous else None,
        "changes": changes,
    }


def build_cash_driver_context(payload: dict, manual: dict | None = None,
                              asset_snapshots: list[dict] | None = None,
                              max_months: int = 6) -> dict:
    """최근 원장 전체에서 현금 유출입 원인과 근거 행을 집계한다."""
    manual = manual if isinstance(manual, dict) else {}
    months_map = payload.get("months") if isinstance(payload.get("months"), dict) else {}
    periods = sorted(months_map)[-max_months:]
    if not periods:
        period = str(payload.get("current_month") or payload.get("period") or "current")
        periods = [period]
        months_map = {period: payload}

    category_totals: dict[str, dict] = {}
    monthly = []
    total_rows = classified_rows = uncertain_rows = internal_rows = 0
    for period in periods:
        month = months_map.get(period) if isinstance(months_map.get(period), dict) else {}
        month_categories: dict[str, float] = defaultdict(float)
        cash_in = cash_out = 0.0
        for direction, key in (("in", "자금의증가"), ("out", "자금의감소")):
            for row in month.get(key) or []:
                if not isinstance(row, dict):
                    continue
                amount = _number(row.get("금액"))
                if amount == 0:
                    continue
                total_rows += 1
                category, reason = classify_finance_transaction(row, direction)
                if category == "internal_transfer":
                    internal_rows += 1
                else:
                    if category.startswith("other_"): uncertain_rows += 1
                    else: classified_rows += 1
                    if direction == "in": cash_in += amount
                    else: cash_out += amount
                month_categories[category] += amount
                record = category_totals.setdefault(category, {
                    "label": CATEGORY_LABELS[category], "amount": 0.0, "count": 0,
                    "counterparties": defaultdict(float), "evidence": [],
                })
                record["amount"] += amount
                record["count"] += 1
                party = str(row.get("거래처명") or "(거래처 미기재)")[:80]
                record["counterparties"][party] += amount
                record["evidence"].append(_public_transaction(row, category, reason))
        monthly.append({
            "period": period,
            "cash_in": round(cash_in),
            "cash_out": round(cash_out),
            "net_cash_flow": round(cash_in - cash_out),
            "drivers": {key: round(value) for key, value in sorted(month_categories.items()) if key != "internal_transfer"},
        })

    compact_categories = {}
    for category, record in category_totals.items():
        if category == "internal_transfer":
            continue
        compact_categories[category] = {
            "label": record["label"],
            "amount": round(record["amount"]),
            "count": record["count"],
            "top_counterparties": [
                {"name": name, "amount": round(amount)}
                for name, amount in sorted(record["counterparties"].items(), key=lambda item: item[1], reverse=True)[:5]
            ],
            "largest_transactions": sorted(record["evidence"], key=lambda item: item["amount"], reverse=True)[:5],
        }

    settlement = payload.get("정산예정") if isinstance(payload.get("정산예정"), dict) else {}
    coupang = settlement.get("쿠팡") if isinstance(settlement.get("쿠팡"), dict) else {}
    naver = settlement.get("네이버") if isinstance(settlement.get("네이버"), dict) else {}
    receivables = payload.get("미수금") if isinstance(payload.get("미수금"), dict) else {}
    tied_cash = {
        "receivables": round(_number(receivables.get("total"))),
        "settlement_coupang": round(_number(coupang.get("정산예정")) + _number(coupang.get("미구매확정"))),
        "settlement_naver": round(_number(naver.get("지급예정"))),
        "settlement_cafe24_manual": round(_manual_amount(manual, "카페24") or 0),
        "supplier_prepaids_manual": round(_manual_amount(manual, "선급금") or 0),
    }
    loan = _manual_amount(manual, "대출")
    return {
        "calculation_version": "finance-assistant-cash-drivers-v1",
        "periods": periods,
        "monthly_cash_flow": monthly,
        "driver_totals": compact_categories,
        "cash_tied_up_now": tied_cash,
        "manual_loan_balance": round(loan) if loan is not None else None,
        "asset_snapshot_comparison": _snapshot_changes(asset_snapshots),
        "coverage": {
            "ledger_rows_analyzed": total_rows,
            "rule_classified_rows": classified_rows,
            "other_or_uncertain_rows": uncertain_rows,
            "internal_transfer_rows_excluded": internal_rows,
        },
        "limitations": [
            "거래 분류는 계정·거래처·적요 기반 보수적 규칙이며 largest_transactions 원문 근거를 함께 확인해야 합니다.",
            "상품·매입처 출금은 재고 매입 가능성을 뜻할 뿐 실제 재고 증가를 확정하지 않습니다.",
            "재고 수량·매입채무의 기간별 잔액 데이터가 없으면 그 원인은 모른다고 답해야 합니다.",
            "내부 계좌이체는 회사 전체 현금 증감에서 제외합니다.",
        ],
    }


def choose_reasoning_effort(messages: list[dict]) -> str:
    last_user = next((str(item.get("content") or "") for item in reversed(messages)
                      if item.get("role") == "user"), "")
    return "medium" if _COMPLEX_QUESTION.search(last_user) else "low"


def build_openai_response_body(instructions: str, messages: list[dict],
                               safety_identifier: str) -> dict:
    clean_messages = [
        {"role": item.get("role"), "content": str(item.get("content") or "")}
        for item in messages[-12:] if item.get("role") in ("user", "assistant")
    ]
    effort = choose_reasoning_effort(clean_messages)
    return {
        "model": FINANCE_MODEL,
        "instructions": instructions,
        "input": clean_messages,
        "reasoning": {"effort": effort},
        "text": {"verbosity": "medium"},
        "max_output_tokens": 3200 if effort == "medium" else 1800,
        "safety_identifier": safety_identifier,
        "store": False,
        "stream": True,
    }


def response_text_delta(event: dict) -> str:
    return str(event.get("delta") or "") if event.get("type") == "response.output_text.delta" else ""
