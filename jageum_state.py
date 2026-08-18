"""자금 데이터셋 상태 계약, shadow LKG, 브리핑 입력을 만드는 순수 로직.

기존 자금 화면의 원본 JSON은 건드리지 않는다. 이 모듈은 각 데이터셋의 후보를
검증하고 메타데이터 snapshot만 원자적으로 발행한다. ``mode=shadow`` 동안에는
이 상태가 기존 화면 숫자의 source가 되지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 2
CALCULATION_VERSION = "jageum-briefing-v1"
DATASETS = (
    "bank_balances",
    "monthly_pl",
    "project_pl",
    "receivables",
    "settlement_coupang",
    "settlement_naver",
)

STALE_AFTER_DAYS = {
    "bank_balances": 2,
    "monthly_pl": 35,
    "project_pl": 35,
    "receivables": 7,
    "settlement_coupang": 2,
    "settlement_naver": 2,
}


def utc_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _today(now: datetime | None = None) -> date:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.date()


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _month_bounds(month: str, now: datetime | None = None) -> tuple[str | None, str | None, str | None]:
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        return None, None, None
    year, mon = map(int, month.split("-"))
    if mon < 1 or mon > 12:
        return None, None, None
    start = date(year, mon, 1)
    end = date(year, mon, monthrange(year, mon)[1])
    source = min(end, _today(now)) if start <= _today(now) else start
    return start.isoformat(), end.isoformat(), source.isoformat()


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:20]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sum_numeric(rows: list[dict], key: str) -> float:
    return sum(float(row.get(key, 0)) for row in rows if _number(row.get(key)))


def _row_change(previous: dict | None, current_rows: int, errors: list[str], warnings: list[str]) -> None:
    old = (((previous or {}).get("validation") or {}).get("metrics") or {}).get("rows")
    if not _number(old) or old < 5:
        return
    ratio = current_rows / old
    if ratio < 0.30:
        errors.append(f"rows 급락: 이전 {int(old)} → 현재 {current_rows}")
    elif ratio > 4.0 and current_rows - old >= 20:
        errors.append(f"rows 급증: 이전 {int(old)} → 현재 {current_rows}")
    elif ratio < 0.60 or ratio > 2.0:
        warnings.append(f"rows 변화 확인 필요: 이전 {int(old)} → 현재 {current_rows}")


def _freshness(dataset: str, source_as_of: str | None, now: datetime | None) -> dict:
    checked = utc_iso(now)
    parsed = _parse_date(source_as_of)
    limit = STALE_AFTER_DAYS[dataset]
    if not parsed:
        return {"status": "unknown", "age_days": None, "stale_after_days": limit, "checked_at": checked}
    age = (_today(now) - parsed).days
    return {
        "status": "stale" if age > limit else "fresh",
        "age_days": age,
        "stale_after_days": limit,
        "checked_at": checked,
    }


def _global_source(payload: dict) -> str | None:
    meta = payload.get("_meta")
    for value in ((meta or {}).get("source_as_of") if isinstance(meta, dict) else None,
                  payload.get("source_as_of")):
        if _parse_date(value):
            return str(value)[:10]
    # 현재 Lightsail은 하나의 실행에서 이카운트·정산 데이터를 묶어 dashboard.json을 만든다.
    # 명시 메타가 없는 legacy payload에서는 같은 실행의 정산 기준일을 관찰 기준일로 쓴다.
    settlement = payload.get("정산예정")
    if isinstance(settlement, dict) and _parse_date(settlement.get("기준")):
        return str(settlement["기준"])[:10]
    dates = []
    for key in ("자금의증가", "자금의감소"):
        for row in payload.get(key) or []:
            if isinstance(row, dict) and _parse_date(row.get("일자")):
                dates.append(str(row["일자"])[:10].replace("/", "-"))
    return max(dates) if dates else None


def _extract_bank(payload: dict, now: datetime | None) -> dict:
    source_rows = payload.get("자금현황")
    if isinstance(source_rows, list):
        bank_rows = [row for row in source_rows if isinstance(row, dict) and
                     any(word in str(row.get("계정명") or "") for word in ("보통예금", "당좌예금", "현금"))]
        rows = bank_rows if bank_rows else source_rows
    else:
        rows = source_rows
    period = payload.get("current_month") or payload.get("period")
    start, end, month_source = _month_bounds(period, now)
    return {"raw": rows, "rows": rows, "period_start": start, "period_end": end,
            "source_as_of": _global_source(payload) or month_source}


def _extract_monthly_pl(payload: dict, now: datetime | None) -> dict:
    raw = payload.get("손익월별")
    months = raw.get("months") if isinstance(raw, dict) else None
    latest = months[-1] if isinstance(months, list) and months else None
    first = months[0] if isinstance(months, list) and months else None
    first_start, _, _ = _month_bounds(first, now)
    _, latest_end, source = _month_bounds(latest, now)
    return {"raw": raw, "rows": raw.get("rows") if isinstance(raw, dict) else None,
            "period_start": first_start, "period_end": latest_end, "source_as_of": source,
            "months": months or [], "latest_month": latest}


def _extract_project(project: dict | None, now: datetime | None) -> dict:
    raw = project if isinstance(project, dict) else None
    months = raw.get("months") if raw else None
    latest = months[-1] if isinstance(months, list) and months else None
    first = months[0] if isinstance(months, list) and months else None
    first_start, _, _ = _month_bounds(first, now)
    _, latest_end, source = _month_bounds(latest, now)
    latest_projects = ((raw.get("proj") or {}).get(latest) or {}) if raw and latest else {}
    rows = [{"project": key, **(value if isinstance(value, dict) else {})}
            for key, value in latest_projects.items()]
    return {"raw": raw, "rows": rows, "period_start": first_start, "period_end": latest_end,
            "source_as_of": source, "months": months or [], "latest_month": latest}


def _extract_receivables(payload: dict, now: datetime | None) -> dict:
    raw = payload.get("미수금")
    period = payload.get("current_month") or payload.get("period")
    start, end, month_source = _month_bounds(period, now)
    return {"raw": raw, "rows": raw.get("items") if isinstance(raw, dict) else None,
            "period_start": start, "period_end": end,
            "source_as_of": _global_source(payload) or month_source}


def _extract_settlement(payload: dict, channel: str, now: datetime | None) -> dict:
    settle = payload.get("정산예정")
    raw = settle.get(channel) if isinstance(settle, dict) else None
    source = None
    if isinstance(raw, dict):
        source = raw.get("기준")
    if not _parse_date(source) and isinstance(settle, dict):
        source = settle.get("기준")
    source_date = _parse_date(source)
    start = source_date.replace(day=1).isoformat() if source_date else None
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        rows = raw["items"]
    elif channel == "네이버" and isinstance(raw, dict):
        # 네이버 응답은 상세행 없이 총액·건수만 주므로 summary 한 행을 명시한다.
        rows = [{"summary": True, "count": raw.get("건수"), "amount": raw.get("지급예정")}]
    else:
        rows = None
    return {"raw": raw, "rows": rows,
            "period_start": start, "period_end": source_date.isoformat() if source_date else None,
            "source_as_of": source_date.isoformat() if source_date else None}


def _base_validation(dataset: str, ext: dict, previous: dict | None) -> tuple[list[str], list[str], dict, dict]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    raw, rows = ext.get("raw"), ext.get("rows")
    if raw is None:
        errors.append("데이터셋이 없습니다")
    elif not isinstance(raw, (dict, list)):
        errors.append("데이터셋 형식이 올바르지 않습니다")
    if rows is None:
        errors.append("rows가 없습니다")
        row_count = 0
    elif not isinstance(rows, list):
        errors.append("rows 형식이 목록이 아닙니다")
        row_count = 0
    else:
        row_count = len(rows)
    metrics["rows"] = row_count
    checks["rows_present"] = rows is not None
    checks["empty"] = row_count == 0
    if dataset in ("bank_balances", "monthly_pl", "project_pl") and isinstance(rows, list) and not rows:
        errors.append("비정상 empty")
    source_date = _parse_date(ext.get("source_as_of"))
    period_start = _parse_date(ext.get("period_start"))
    period_end = _parse_date(ext.get("period_end"))
    if not source_date:
        errors.append("source_as_of를 확인할 수 없습니다")
    if not period_start or not period_end:
        errors.append("기간을 확인할 수 없습니다")
    elif ext["period_start"] > ext["period_end"]:
        errors.append("기간 시작과 종료가 맞지 않습니다")
    elif source_date and not (period_start <= source_date <= period_end):
        errors.append("source_as_of와 데이터 기간이 맞지 않습니다")
    _row_change(previous, row_count, errors, warnings)
    return errors, warnings, checks, metrics


def _validate_bank(ext: dict, previous: dict | None) -> dict:
    errors, warnings, checks, metrics = _base_validation("bank_balances", ext, previous)
    rows = ext.get("rows") if isinstance(ext.get("rows"), list) else []
    required = ("계정명", "금일잔액")
    bad_types, missing = 0, 0
    ids, accounts = [], []
    for row in rows:
        if not isinstance(row, dict):
            bad_types += 1; continue
        if any(key not in row for key in required):
            missing += 1
        if not _number(row.get("금일잔액")):
            bad_types += 1
        ident = (str(row.get("계정명") or ""), str(row.get("거래처코드") or row.get("거래처명") or ""))
        ids.append(ident); accounts.append("|".join(ident))
    if missing: errors.append(f"필수 계좌 값 누락 {missing}건")
    if bad_types: errors.append(f"잔액 데이터 타입 오류 {bad_types}건")
    dup = len(ids) - len(set(ids))
    if dup: errors.append(f"중복 계좌 {dup}건")
    total = _sum_numeric(rows, "금일잔액")
    metrics.update({"total_balance": total, "account_count": len(rows), "accounts": sorted(accounts)})
    old_accounts = set(((((previous or {}).get("validation") or {}).get("metrics") or {}).get("accounts") or []))
    gone = sorted(old_accounts - set(accounts))
    added = sorted(set(accounts) - old_accounts)
    if gone: errors.append(f"기존 계좌 누락 {len(gone)}개")
    if added and old_accounts: warnings.append(f"새 계좌 {len(added)}개")
    old_total = (((previous or {}).get("validation") or {}).get("metrics") or {}).get("total_balance")
    if _number(old_total) and old_total and abs(total-old_total)/abs(old_total) > 1.0:
        warnings.append("총잔액이 과거 정상 범위보다 크게 변했습니다")
    checks.update({"duplicates": dup, "missing_accounts": gone, "new_accounts": added,
                   "explicit_zero_count": sum(1 for row in rows if row.get("금일잔액") == 0)})
    return _validation(errors, warnings, checks, metrics)


def _pl_row_map(rows: list) -> tuple[dict, list[str]]:
    result, dup = {}, []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = re.sub(r"^\s*\d+\.\s*", "", str(row.get("과목") or "")).strip()
        if name in result: dup.append(name)
        result[name] = row
    return result, dup


def _validate_monthly_pl(ext: dict, previous: dict | None) -> dict:
    errors, warnings, checks, metrics = _base_validation("monthly_pl", ext, previous)
    rows = ext.get("rows") if isinstance(ext.get("rows"), list) else []
    months = ext.get("months") or []
    row_map, dup = _pl_row_map(rows)
    required = ("매출", "매출원가", "매출총이익", "판매비 및 일반관리비", "영업손익", "당기순이익")
    missing_rows = [name for name in required if name not in row_map]
    if missing_rows: errors.append("필수 손익 행 누락: " + ", ".join(missing_rows))
    if dup: errors.append("중복 손익 행: " + ", ".join(sorted(set(dup))))
    blank_cells, type_errors, equation_errors = [], [], []
    for month in months:
        vals = {}
        for name in required:
            monthly = row_map.get(name, {}).get("월별")
            if not isinstance(monthly, dict) or month not in monthly or monthly[month] is None:
                blank_cells.append(f"{month} {name}")
            elif not _number(monthly[month]):
                type_errors.append(f"{month} {name}")
            else:
                vals[name] = monthly[month]
        if len(vals) == len(required):
            if abs(vals["매출"] - vals["매출원가"] - vals["매출총이익"]) > 0.5:
                equation_errors.append(f"{month} 매출총이익")
            if abs(vals["매출총이익"] - vals["판매비 및 일반관리비"] - vals["영업손익"]) > 0.5:
                equation_errors.append(f"{month} 영업손익")
        pretax = row_map.get("법인세비용차감전순손익", {}).get("월별", {}).get(month)
        tax = row_map.get("법인세비용", {}).get("월별", {}).get(month)
        net = row_map.get("당기순이익", {}).get("월별", {}).get(month)
        if all(_number(v) for v in (pretax, tax, net)) and abs(pretax - tax - net) > 0.5:
            equation_errors.append(f"{month} 당기순이익")
    if blank_cells: errors.append(f"빈 손익 셀 {len(blank_cells)}개 (0과 구분됨)")
    if type_errors: errors.append(f"손익 데이터 타입 오류 {len(type_errors)}개")
    if equation_errors: errors.append("손익 검산 불일치: " + ", ".join(equation_errors[:8]))
    latest = ext.get("latest_month")
    def latest_value(name: str):
        return row_map.get(name, {}).get("월별", {}).get(latest)
    metrics.update({"months": len(months), "latest_month": latest,
                    "revenue": latest_value("매출"), "gross_profit": latest_value("매출총이익"),
                    "operating_profit": latest_value("영업손익"), "net_income": latest_value("당기순이익")})
    revenue_history = [row_map.get("매출", {}).get("월별", {}).get(month) for month in months[:-1]]
    revenue_history = sorted(abs(value) for value in revenue_history if _number(value) and value != 0)
    latest_revenue = latest_value("매출")
    if revenue_history and _number(latest_revenue):
        median = revenue_history[len(revenue_history)//2]
        if median and (abs(latest_revenue) > median*3 or abs(latest_revenue) < median*0.15):
            warnings.append("최근 매출이 과거 정상 범위와 크게 다릅니다")
    checks.update({"missing_required_rows": missing_rows, "blank_cells": len(blank_cells),
                   "explicit_zero_cells": sum(1 for row in rows for value in ((row.get("월별") or {}).values() if isinstance(row, dict) else []) if value == 0),
                   "equation_errors": equation_errors, "duplicates": sorted(set(dup))})
    return _validation(errors, warnings, checks, metrics)


def _validate_project(ext: dict, previous: dict | None) -> dict:
    errors, warnings, checks, metrics = _base_validation("project_pl", ext, previous)
    raw = ext.get("raw") if isinstance(ext.get("raw"), dict) else {}
    latest = ext.get("latest_month")
    teams = (raw.get("data") or {}).get(latest)
    chk = (raw.get("chk") or {}).get(latest)
    if not isinstance(teams, dict) or not teams:
        errors.append("기준월 팀별 손익이 없습니다")
        teams = {}
    if not isinstance(chk, dict):
        errors.append("monthly_pl 비교값이 없습니다")
        chk = {}
    fields = ("매출", "매출원가", "판관비", "영업손익")
    totals = {field: 0 for field in fields}
    type_errors = 0
    for values in teams.values():
        if not isinstance(values, dict): type_errors += 1; continue
        for field in fields:
            if not _number(values.get(field)): type_errors += 1
            else: totals[field] += values[field]
    if type_errors: errors.append(f"프로젝트 손익 데이터 타입 오류 {type_errors}건")
    diffs = {field: totals[field] - chk.get(field) if _number(chk.get(field)) else None for field in fields}
    mismatch = {field: value for field, value in diffs.items() if value is not None and abs(value) > 1}
    if mismatch: errors.append("monthly_pl 합계와 불일치: " + ", ".join(mismatch))
    unc = teams.get("미분류") if isinstance(teams.get("미분류"), dict) else {}
    metrics.update({"latest_month": latest, "project_count": len(ext.get("rows") or []),
                    "team_count": len(teams), "totals": totals, "monthly_pl_difference": diffs,
                    "unclassified": {key: unc.get(key, 0) for key in fields}})
    checks.update({"monthly_pl_match": not mismatch, "unclassified_present": bool(unc),
                   "period_match": latest in (raw.get("data") or {}) and latest in (raw.get("chk") or {})})
    return _validation(errors, warnings, checks, metrics)


def _validate_receivables(ext: dict, previous: dict | None) -> dict:
    errors, warnings, checks, metrics = _base_validation("receivables", ext, previous)
    raw = ext.get("raw") if isinstance(ext.get("raw"), dict) else {}
    rows = ext.get("rows") if isinstance(ext.get("rows"), list) else []
    total = raw.get("total")
    if not _number(total): errors.append("미수금 총액 타입 오류")
    bad = [row for row in rows if not isinstance(row, dict) or not _number(row.get("잔액"))]
    if bad: errors.append(f"미수금 행 타입 오류 {len(bad)}건")
    names = [str(row.get("거래처명") or "").strip() for row in rows if isinstance(row, dict)]
    dup = len([name for name in names if name]) - len(set(name for name in names if name))
    if dup: errors.append(f"중복 미수 거래처 {dup}건")
    row_total = _sum_numeric(rows, "잔액")
    if _number(total) and abs(total - row_total) > 1: errors.append("미수금 총액과 행 합계 불일치")
    old_total = (((previous or {}).get("validation") or {}).get("metrics") or {}).get("total")
    if _number(old_total) and old_total and abs(total - old_total) / abs(old_total) > 1.0:
        warnings.append("미수금 총액 급변")
    metrics.update({"total": total, "item_count": len(rows)})
    checks.update({"duplicates": dup, "sum_match": _number(total) and abs(total-row_total) <= 1,
                   "explicit_zero": total == 0})
    return _validation(errors, warnings, checks, metrics)


def _validate_settlement(dataset: str, ext: dict, previous: dict | None) -> dict:
    errors, warnings, checks, metrics = _base_validation(dataset, ext, previous)
    raw = ext.get("raw") if isinstance(ext.get("raw"), dict) else {}
    if dataset == "settlement_coupang":
        total, count = raw.get("정산예정"), len(raw.get("items") or []) if isinstance(raw.get("items"), list) else None
        rows = raw.get("items") if isinstance(raw.get("items"), list) else []
        if not _number(total): errors.append("쿠팡 총 정산예정 타입 오류")
        if _number(total) and total != 0 and not rows: errors.append("쿠팡 총액은 있으나 지급예정 행이 없습니다")
        if _number(total) and rows and abs(total - _sum_numeric(rows, "amount")) > 1: errors.append("쿠팡 총액과 지급예정 행 합계 불일치")
        ids = [(row.get("date"), row.get("amount"), row.get("type")) for row in rows if isinstance(row, dict)]
        dup = len(ids)-len(set(ids))
    else:
        total, count = raw.get("지급예정"), raw.get("건수")
        if not _number(total): errors.append("네이버 총 정산예정 타입 오류")
        if not isinstance(count, int) or isinstance(count, bool): errors.append("네이버 건수 타입 오류")
        dup = 0
    if dup: errors.append(f"중복 정산 행 {dup}건")
    if total == 0:
        warnings.append("실제 0원인지 확인 필요")
    old_total = (((previous or {}).get("validation") or {}).get("metrics") or {}).get("total")
    if _number(old_total) and old_total and _number(total) and abs(total-old_total)/abs(old_total) > 1.5:
        warnings.append("정산예정 총액 급변")
    metrics.update({"total": total, "item_count": count, "rows": count if isinstance(count, int) else metrics["rows"]})
    checks.update({"duplicates": dup, "explicit_zero": total == 0, "value_present": "정산예정" in raw if dataset == "settlement_coupang" else "지급예정" in raw})
    return _validation(errors, warnings, checks, metrics)


def _validation(errors: list[str], warnings: list[str], checks: dict, metrics: dict) -> dict:
    return {"status": "failed" if errors else ("warning" if warnings else "passed"),
            "errors": errors, "warnings": warnings, "checks": checks, "metrics": metrics}


def evaluate_dataset(dataset: str, payload: dict, project_payload: dict | None = None,
                     previous: dict | None = None, now: datetime | None = None,
                     attempt_status: str = "success", attempt_error: str | None = None) -> dict:
    """후보 한 개를 검증하고 성공할 때만 served_snapshot을 교체한다."""
    started = utc_iso(now)
    if dataset == "bank_balances": ext = _extract_bank(payload, now)
    elif dataset == "monthly_pl": ext = _extract_monthly_pl(payload, now)
    elif dataset == "project_pl": ext = _extract_project(project_payload, now)
    elif dataset == "receivables": ext = _extract_receivables(payload, now)
    elif dataset == "settlement_coupang": ext = _extract_settlement(payload, "쿠팡", now)
    elif dataset == "settlement_naver": ext = _extract_settlement(payload, "네이버", now)
    else: raise ValueError(f"unsupported dataset: {dataset}")
    if dataset == "bank_balances": validation = _validate_bank(ext, previous)
    elif dataset == "monthly_pl": validation = _validate_monthly_pl(ext, previous)
    elif dataset == "project_pl": validation = _validate_project(ext, previous)
    elif dataset == "receivables": validation = _validate_receivables(ext, previous)
    else: validation = _validate_settlement(dataset, ext, previous)
    freshness = _freshness(dataset, ext.get("source_as_of"), now)
    if freshness["status"] == "stale":
        validation["warnings"].append(f"오래된 값: {freshness['age_days']}일 전 기준")
        if validation["status"] == "passed": validation["status"] = "warning"
    old_snapshot = (previous or {}).get("served_snapshot")
    history = list((previous or {}).get("previous_snapshots") or [])
    collection_failed = attempt_status not in ("success", "completed")
    rejected = collection_failed or validation["status"] == "failed"
    if rejected:
        err = attempt_error or ("; ".join(validation["errors"][:3]) if validation["errors"] else "수집 실패")
        served = old_snapshot
        fallback = bool(old_snapshot)
        latest = {"status": "failed", "attempted_at": started, "completed_at": utc_iso(now), "error": err[:500]}
    else:
        raw_hash = _canonical_hash(ext.get("raw"))
        served = {"snapshot_id": raw_hash, "source_as_of": ext.get("source_as_of"),
                  "last_success_at": utc_iso(now), "received_at": started, "published_at": utc_iso(now),
                  "period_start": ext.get("period_start"), "period_end": ext.get("period_end"),
                  "rows": validation["metrics"].get("rows", 0),
                  "metrics": validation["metrics"]}
        if old_snapshot and old_snapshot.get("snapshot_id") != raw_hash:
            archived = dict(old_snapshot)
            archived["metrics"] = (((previous or {}).get("validation") or {}).get("metrics") or {})
            history = ([archived] + history)[:3]
        fallback = False
        latest = {"status": "success", "attempted_at": started, "completed_at": utc_iso(now), "error": None}
    return {"dataset": dataset, "mode": "shadow", "latest_attempt": latest,
            "served_snapshot": served, "fallback": fallback, "validation": validation,
            "freshness": freshness, "previous_snapshots": history}


def evaluate_all(payload: dict, project_payload: dict | None = None, previous_states: dict | None = None,
                 now: datetime | None = None, attempt_status: str = "success",
                 attempt_error: str | None = None) -> dict:
    previous_states = previous_states if isinstance(previous_states, dict) else {}
    contracts = {}
    for dataset in DATASETS:
        per_status, per_error = attempt_status, attempt_error
        if dataset == "project_pl" and not project_payload:
            per_status, per_error = "failed", "프로젝트 손익을 불러오지 못했습니다"
        contracts[dataset] = evaluate_dataset(dataset, payload, project_payload, previous_states.get(dataset),
                                              now, per_status, per_error)
    return {"contract_version": CONTRACT_VERSION, "mode": "shadow", "updated_at": utc_iso(now),
            "datasets": contracts}


def atomic_write_json(path: Path, value: dict) -> None:
    """같은 디렉터리의 임시 파일을 fsync한 뒤 rename해 반쪽 JSON을 만들지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists(): tmp.unlink()
        except OSError:
            pass


def migrate_state(value: Any) -> dict:
    """없거나 구버전인 상태도 안전하게 v2 shadow envelope로 읽는다."""
    if not isinstance(value, dict):
        return {"contract_version": CONTRACT_VERSION, "mode": "shadow", "updated_at": None, "datasets": {}}
    if value.get("contract_version") == CONTRACT_VERSION and isinstance(value.get("datasets"), dict):
        out = dict(value); out["mode"] = "shadow"; return out
    datasets = value.get("datasets") if isinstance(value.get("datasets"), dict) else value
    clean = {key: item for key, item in datasets.items() if key in DATASETS and isinstance(item, dict)}
    return {"contract_version": CONTRACT_VERSION, "mode": "shadow", "updated_at": value.get("updated_at"), "datasets": clean}


def load_state_file(path: Path) -> dict:
    try:
        return migrate_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return migrate_state(None)


def classify_cashflow(row: dict, direction: str) -> str:
    """기존 KPI를 바꾸지 않는 보수적 shadow 분류."""
    text = " ".join(str(row.get(key) or "") for key in ("상대계정명", "거래처명", "적요", "프로젝트명"))
    account = str(row.get("상대계정명") or "").strip()
    if not account or re.search(r"내부이체|계좌이체|자금이동", text): return "internal_transfer"
    if re.search(r"대표|가수금|가지급금|인출금|조준연|이효순", text): return "owner_related"
    if re.search(r"차입|대출|원금상환|차입금", text): return "financing"
    if re.search(r"태양광|태양열|종보전기|기계장치|시설장치|유형자산|보증금|투자", text): return "investing"
    if direction == "in" and re.search(r"국세|세무서|세금.*환급|부가세.*환급|정부지원|보조금", text): return "unclassified"
    if re.search(r"매출|외상매출금|쿠팡|네이버|카페24|카페이십사|판매", text): return "operating"
    if direction == "out" and re.search(r"매입|급여|법인카드|수수료|운반|광고|임차|보험|복리|소모품|세금과공과", text): return "operating"
    return "unclassified"


def cashflow_shadow(payload: dict) -> dict:
    categories = ("operating", "investing", "financing", "owner_related", "internal_transfer", "unclassified")
    result = {direction: {key: {"amount": 0, "rows": 0} for key in categories} for direction in ("in", "out")}
    for direction, key in (("in", "자금의증가"), ("out", "자금의감소")):
        for row in payload.get(key) or []:
            if not isinstance(row, dict): continue
            category = classify_cashflow(row, direction)
            amount = row.get("금액") if _number(row.get("금액")) else 0
            result[direction][category]["amount"] += amount
            result[direction][category]["rows"] += 1
    old_in = sum(row.get("금액", 0) for row in payload.get("자금의증가") or []
                 if isinstance(row, dict) and str(row.get("상대계정명") or "").strip()
                 and not re.search(r"한국전력|한전경산|태양광|전력판매", str(row.get("거래처명") or "")+str(row.get("적요") or "")))
    new_in = result["in"]["operating"]["amount"]
    return {"mode": "shadow", "calculation_version": "cashflow-classification-v1",
            "categories": result, "comparison": {"current_operating_in": old_in,
            "shadow_operating_in": new_in, "difference": new_in-old_in}}


def briefing_contract(payload: dict, state_envelope: dict, classification: dict | None = None) -> dict:
    states = (state_envelope or {}).get("datasets") or {}
    period = payload.get("current_month") or payload.get("period")
    def metric(name: str, value: Any, dataset: str, unit: str = "KRW") -> dict:
        state = states.get(dataset) or {}; snap = state.get("served_snapshot") or {}
        return {"name": name, "value": value if _number(value) else None, "unit": unit, "period": period,
                "source_as_of": snap.get("source_as_of"), "snapshot_id": snap.get("snapshot_id"),
                "status": (state.get("latest_attempt") or {}).get("status", "unknown"),
                "fallback": bool(state.get("fallback")), "calculation_version": CALCULATION_VERSION}
    metrics = []
    for dataset, key, name in (("bank_balances", "total_balance", "bank_balance"),
                               ("monthly_pl", "revenue", "revenue"),
                               ("monthly_pl", "operating_profit", "operating_profit"),
                               ("monthly_pl", "net_income", "net_income"),
                               ("receivables", "total", "receivables"),
                               ("settlement_coupang", "total", "settlement_coupang"),
                               ("settlement_naver", "total", "settlement_naver")):
        state = states.get(dataset) or {}
        if state.get("fallback"):
            value = (((state.get("served_snapshot") or {}).get("metrics") or {}).get(key))
        else:
            value = (((state.get("validation") or {}).get("metrics") or {}).get(key))
        metrics.append(metric(name, value, dataset))
    classification = classification or cashflow_shadow(payload)
    metrics.append(metric("shadow_operating_in", classification["comparison"]["shadow_operating_in"], "bank_balances"))
    anomalies, limitations = [], []
    for dataset, state in states.items():
        latest, validation, fresh = state.get("latest_attempt") or {}, state.get("validation") or {}, state.get("freshness") or {}
        if latest.get("status") == "failed": anomalies.append({"type": "collection_failed", "dataset": dataset, "message": latest.get("error")})
        if state.get("fallback"): anomalies.append({"type": "fallback", "dataset": dataset, "message": "이전 정상본 사용 중"})
        if fresh.get("status") == "stale": anomalies.append({"type": "stale", "dataset": dataset, "message": f"{fresh.get('age_days')}일 전 기준"})
        for warning in validation.get("warnings") or []: anomalies.append({"type": "validation_warning", "dataset": dataset, "message": warning})
        if not state.get("served_snapshot"): limitations.append(f"{dataset}: 정상 snapshot 없음")
    # 같은 원본 안의 전월과 비교해 AI가 숫자를 다시 계산하거나 원인을 지어내지 않게 한다.
    pl = payload.get("손익월별") if isinstance(payload.get("손익월별"), dict) else {}
    months = pl.get("months") if isinstance(pl.get("months"), list) else []
    revenue_row = next((row for row in pl.get("rows") or [] if isinstance(row, dict) and
                        re.sub(r"^\s*\d+\.\s*", "", str(row.get("과목") or "")).strip() == "매출"), {})
    revenue_monthly = revenue_row.get("월별") if isinstance(revenue_row.get("월별"), dict) else {}
    current_month = months[-1] if months else None
    previous_month = months[-2] if len(months) > 1 else None
    revenue_comparison = {"current": revenue_monthly.get(current_month), "previous": revenue_monthly.get(previous_month),
                          "current_period": current_month, "previous_period": previous_month, "change": None}
    if _number(revenue_comparison["current"]) and _number(revenue_comparison["previous"]):
        revenue_comparison["change"] = revenue_comparison["current"] - revenue_comparison["previous"]
    def month_cash(month: str | None):
        rows = ((payload.get("months") or {}).get(month) or {}).get("자금현황") if month else None
        if not isinstance(rows, list): return None
        return _sum_numeric([row for row in rows if isinstance(row, dict) and "보통예금" in str(row.get("계정명") or "")], "금일잔액")
    current_cash, previous_cash = month_cash(current_month), month_cash(previous_month)
    cash_comparison = {"current": current_cash, "previous": previous_cash, "current_period": current_month,
                       "previous_period": previous_month,
                       "change": current_cash-previous_cash if _number(current_cash) and _number(previous_cash) else None}
    if _number(revenue_comparison["change"]) and revenue_comparison["change"] > 0 and \
       _number(cash_comparison["change"]) and cash_comparison["change"] < 0:
        anomalies.append({"type": "revenue_up_cash_down", "dataset": "monthly_pl,bank_balances",
                          "message": "매출은 증가했지만 통장 잔액은 감소했습니다"})
    historical_comparisons = {}
    for dataset, metric_key in (("receivables", "total"), ("settlement_coupang", "total"), ("settlement_naver", "total")):
        state = states.get(dataset) or {}
        source_metrics = ((state.get("served_snapshot") or {}).get("metrics") or {}) if state.get("fallback") else ((state.get("validation") or {}).get("metrics") or {})
        current = source_metrics.get(metric_key)
        prior = (((state.get("previous_snapshots") or [{}])[0].get("metrics") or {}).get(metric_key))
        historical_comparisons[dataset] = {"current": current, "previous": prior,
                                           "change": current-prior if _number(current) and _number(prior) else None}
        if dataset == "receivables" and _number(current) and _number(prior) and prior and current > prior*1.5:
            anomalies.append({"type": "receivables_increase", "dataset": dataset, "message": "미수금이 이전 정상본보다 크게 증가했습니다"})
    unclassified_cash = sum(classification["categories"][direction]["unclassified"]["amount"] for direction in ("in", "out"))
    unclassified_rows = sum(classification["categories"][direction]["unclassified"]["rows"] for direction in ("in", "out"))
    if unclassified_rows:
        anomalies.append({"type": "unclassified_cashflow", "dataset": "bank_balances",
                          "message": f"미분류 입출금 {unclassified_rows}건", "value": unclassified_cash})
    card_unc = (payload.get("법인카드") or {}).get("미분류") if isinstance(payload.get("법인카드"), dict) else None
    if isinstance(card_unc, dict) and (card_unc.get("건수") or 0) > 0:
        anomalies.append({"type": "unclassified_cards", "dataset": "corporate_cards",
                          "message": f"미분류 카드 {card_unc.get('건수')}건", "value": card_unc.get("합")})
    coupon_items = (((payload.get("정산예정") or {}).get("쿠팡") or {}).get("items") or [])
    reference = _parse_date((payload.get("정산예정") or {}).get("기준")) if isinstance(payload.get("정산예정"), dict) else None
    overdue = [row for row in coupon_items if isinstance(row, dict) and _parse_date(row.get("date")) and reference and
               (reference-_parse_date(row.get("date"))).days > 7]
    if overdue:
        anomalies.append({"type": "settlement_long_outstanding", "dataset": "settlement_coupang",
                          "message": f"기준일보다 7일 넘게 지난 정산예정 {len(overdue)}건"})
    limitations.extend(["shadow 상태 계약은 아직 기존 화면 숫자를 바꾸지 않습니다",
                        "원인 정보가 없는 변화는 원인을 추정하지 않습니다",
                        "네이버 정산은 상세 지급예정일 없이 총액·건수만 제공됩니다"])
    return {"period": period, "datasets": {key: {"status": (value.get("latest_attempt") or {}).get("status", "unknown"),
            "fallback": bool(value.get("fallback")), "source_as_of": (value.get("served_snapshot") or {}).get("source_as_of"),
            "snapshot_id": (value.get("served_snapshot") or {}).get("snapshot_id")} for key, value in states.items()},
            "metrics": metrics, "comparisons": {"cashflow_classification": classification["comparison"],
            "revenue": revenue_comparison, "bank_balance": cash_comparison, **historical_comparisons},
            "anomalies": anomalies, "limitations": limitations,
            "calculation_version": CALCULATION_VERSION}
