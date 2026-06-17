"""
쿠팡 소싱 분석 모듈
- Coupang Partners API: 실제 쿠팡 상품 데이터 (가격, 리뷰, 로켓 여부)
- Naver Shopping API: 시장 규모 교차 검증
"""
import math
import os
from typing import Dict, List

import httpx

from coupang_api import CoupangPartnersAPI


def _coupang_client() -> CoupangPartnersAPI:
    return CoupangPartnersAPI(
        os.getenv("COUPANG_ACCESS_KEY", ""),
        os.getenv("COUPANG_SECRET_KEY", ""),
    )


async def _naver_coupang_count(keyword: str) -> int:
    """네이버 쇼핑 API로 쿠팡 등록 상품 수 추정"""
    cid = os.getenv("NAVER_CLIENT_ID", "").strip()
    csk = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not csk:
        return 0
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://openapi.naver.com/v1/search/shop.json",
                params={"query": keyword, "display": 100, "sort": "sim"},
                headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csk},
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            coupang_items = [i for i in items if "쿠팡" in i.get("mallName", "")]
            return len(coupang_items)
    except Exception:
        return 0


def _price_band_analysis(products: List[Dict]) -> List[Dict]:
    """가격대별 경쟁 분포 분석"""
    if not products:
        return []

    prices = [p["price"] for p in products if p["price"] > 0]
    if not prices:
        return []

    p_max = max(prices)
    # 동적 구간 생성 (최대가 기준으로 4구간)
    step = p_max / 4
    bands = []
    for i in range(4):
        lo = round(step * i / 10000) * 10000
        hi = round(step * (i + 1) / 10000) * 10000
        in_band = [p for p in products if lo <= p["price"] < hi or (i == 3 and p["price"] >= lo)]
        if not in_band:
            continue
        rocket_cnt = sum(1 for p in in_band if p.get("is_rocket"))
        avg_reviews = round(sum(p.get("reviews", 0) for p in in_band) / len(in_band))
        bands.append({
            "label": f"{lo:,}~{hi:,}원" if i < 3 else f"{lo:,}원~",
            "count": len(in_band),
            "rocket_ratio": round(rocket_cnt / len(in_band) * 100),
            "avg_reviews": avg_reviews,
            "avg_price": round(sum(p["price"] for p in in_band) / len(in_band)),
        })
    return bands


def _find_sweet_spot(bands: List[Dict]) -> Dict | None:
    """진입 최적 가격대 찾기: 로켓 비율 낮고 리뷰수 적은 구간"""
    if not bands:
        return None
    # 로켓 비율 낮음 + 리뷰수 적음 + 상품 수 충분한 구간 우선
    scored = []
    for b in bands:
        if b["count"] < 3:
            continue
        score = (100 - b["rocket_ratio"]) * 0.6 + max(0, 100 - b["avg_reviews"] / 20) * 0.4
        scored.append((score, b))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def calc_competition(products: List[Dict], naver_count: int = 0) -> Dict:
    """쿠팡 기준 경쟁강도 점수 계산"""
    if not products:
        return {
            "score": 0, "label": "데이터 없음", "color": "#6b7280",
            "rocket_ratio": 0, "avg_reviews": 0, "high_review_ratio": 0,
            "avg_rating": 0, "avg_price": 0, "min_price": 0, "max_price": 0,
            "price_cv": 0, "breakdown": {"rocket": 0, "reviews": 0, "price_cv": 0, "high_review": 0},
        }

    n = len(products)
    prices = [p["price"] for p in products if p["price"] > 0]
    avg_price = sum(prices) / len(prices) if prices else 0
    price_std = math.sqrt(sum((p - avg_price) ** 2 for p in prices) / len(prices)) if len(prices) > 1 else 0
    price_cv = price_std / avg_price if avg_price else 0

    rocket_cnt = sum(1 for p in products if p.get("is_rocket") or p.get("is_rocket_wow"))
    rocket_ratio = rocket_cnt / n

    reviews = [p.get("reviews", 0) for p in products]
    avg_reviews = sum(reviews) / n if n else 0
    high_review_ratio = sum(1 for r in reviews if r >= 1000) / n if n else 0

    # 점수 산정 (높을수록 진입 어려움)
    s_rocket = rocket_ratio * 35            # 로켓 비율 (max 35)
    s_reviews = min(avg_reviews / 500 * 30, 30)  # 평균 리뷰수 (max 30)
    s_cv = max(0, (1 - price_cv) * 20)     # 가격 집중도 (낮은 CV = 레드오션)
    s_high = high_review_ratio * 15        # 1000리뷰+ 비율 (max 15)
    score = round(s_rocket + s_reviews + s_cv + s_high)
    score = max(0, min(100, score))

    if score >= 70:
        label, color = "진입 어려움", "#ef4444"
    elif score >= 45:
        label, color = "검토 필요", "#f97316"
    else:
        label, color = "진입 유리", "#22c55e"

    return {
        "score": score,
        "label": label,
        "color": color,
        "rocket_ratio": round(rocket_ratio * 100),
        "avg_reviews": round(avg_reviews),
        "high_review_ratio": round(high_review_ratio * 100),
        "avg_rating": round(sum(p.get("rating", 0) for p in products) / n, 1),
        "avg_price": round(avg_price),
        "min_price": min(prices) if prices else 0,
        "max_price": max(prices) if prices else 0,
        "price_cv": round(price_cv * 100),
        "breakdown": {
            "rocket": round(s_rocket),
            "reviews": round(s_reviews),
            "price_cv": round(s_cv),
            "high_review": round(s_high),
        },
    }


def build_insights(comp: Dict, bands: List[Dict], sweet_spot: Dict | None) -> List[str]:
    insights = []
    rr = comp["rocket_ratio"]
    if rr >= 70:
        insights.append(f"🚀 로켓배송 {rr}% — 쿠팡 직매입이 지배적. 가격 경쟁 매우 치열")
    elif rr >= 40:
        insights.append(f"🚀 로켓배송 {rr}% — 일반 판매자도 기회 있음")
    else:
        insights.append(f"🚀 로켓배송 {rr}% — 일반 판매자 중심 시장. 진입 유리")

    ar = comp["avg_reviews"]
    if ar >= 2000:
        insights.append(f"⭐ 평균 리뷰 {ar:,}개 — 매우 성숙한 시장. 신규 진입 시 초기 리뷰 확보가 관건")
    elif ar >= 500:
        insights.append(f"⭐ 평균 리뷰 {ar:,}개 — 중간 수준. 차별화된 상품으로 진입 가능")
    else:
        insights.append(f"⭐ 평균 리뷰 {ar:,}개 — 리뷰 경쟁 낮음. 빠른 진입으로 선점 가능")

    hrr = comp["high_review_ratio"]
    if hrr >= 50:
        insights.append(f"📊 상품의 {hrr}%가 1000리뷰+ — 포화 시장")
    elif hrr >= 20:
        insights.append(f"📊 상품의 {hrr}%가 1000리뷰+ — 일부 포화, 틈새 발굴 필요")

    if sweet_spot:
        insights.append(
            f"🎯 추천 진입 가격대: {sweet_spot['label']} "
            f"(로켓 {sweet_spot['rocket_ratio']}%, 평균 리뷰 {sweet_spot['avg_reviews']:,}개)"
        )

    cv = comp["price_cv"]
    if cv < 20:
        insights.append(f"💰 가격 변동계수 {cv}% — 가격대 매우 집중. 가격 경쟁 치열")
    elif cv > 60:
        insights.append(f"💰 가격 변동계수 {cv}% — 가격대 넓음. 포지셔닝 전략 중요")

    return insights


async def analyze(keyword: str) -> Dict:
    client = _coupang_client()

    # 쿠팡파트너스 API: 최대 100개
    resp = await client.search(keyword, limit=100)
    products = client.parse_products(resp)
    landing_url = resp.get("data", {}).get("landingUrl", "")

    # 네이버 쇼핑: 쿠팡 상품 수 교차 검증 (백그라운드)
    naver_count = await _naver_coupang_count(keyword)

    comp = calc_competition(products, naver_count)
    bands = _price_band_analysis(products)
    sweet_spot = _find_sweet_spot(bands)
    insights = build_insights(comp, bands, sweet_spot)

    # 소싱 추천 상품 (리뷰 적고 로켓 아닌 것 우선)
    non_rocket = [p for p in products if not p.get("is_rocket") and not p.get("is_rocket_wow")]
    low_review = sorted(non_rocket, key=lambda p: p.get("reviews", 0))

    return {
        "keyword": keyword,
        "total_from_api": len(products),
        "naver_coupang_count": naver_count,
        "landing_url": landing_url,
        "competition": comp,
        "price_bands": bands,
        "sweet_spot": sweet_spot,
        "insights": insights,
        "products": products[:50],
        "entry_candidates": low_review[:10],
    }
