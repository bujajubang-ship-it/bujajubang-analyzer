"""
쿠팡 소싱 분석 모듈
Partners 어필리에이트 API는 리뷰/평점 데이터 미제공.
로켓 비율 · 가격 분포 · 무료배송 비율로 경쟁강도를 측정한다.
"""
import asyncio
import math
import os
from typing import Dict, List, Optional

import httpx

from coupang_api import CoupangPartnersAPI

DEFAULT_KEYWORDS = [
    "집게", "주걱", "앞치마",
    "국자", "도마", "뒤집개",
]


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
            return len([i for i in items if "쿠팡" in i.get("mallName", "")])
    except Exception:
        return 0


def score_product(p: Dict) -> int:
    """개별 상품 소싱 기회 점수 (0-100, 높을수록 진입 유리)
    리뷰 데이터 미제공이므로 로켓 여부·가격대·무료배송으로 산정."""
    score = 0

    # 로켓 없음 = 일반 판매자 진입 가능 (핵심 지표)
    if not p.get("is_rocket") and not p.get("is_rocket_wow"):
        score += 40

    # 가격대 적합성 (소비자 주방용품 기준)
    price = p.get("price", 0)
    if 5000 <= price <= 30000:
        score += 35   # 최적 가격대
    elif 3000 <= price <= 60000:
        score += 25
    elif 1000 <= price <= 100000:
        score += 12

    # 무료배송 아님 = 아직 물류 경쟁 덜 치열
    if not p.get("is_free_shipping"):
        score += 15

    # 가격 절대값 보너스: 너무 싸거나 비싸면 소싱 마진 위험
    if price < 2000:
        score -= 10
    elif price > 200000:
        score -= 5

    return max(0, min(100, score))


def calc_competition(products: List[Dict], naver_count: int = 0) -> Dict:
    """쿠팡 기준 경쟁강도 점수 계산 (리뷰 없이 로켓·가격·무료배송 기반)"""
    empty = {
        "score": 0, "label": "데이터 없음", "color": "#6b7280",
        "rocket_ratio": 0, "free_shipping_ratio": 0,
        "avg_price": 0, "min_price": 0, "max_price": 0,
        "price_cv": 0, "product_count": 0,
        "breakdown": {"rocket": 0, "free_shipping": 0, "price_cv": 0, "volume": 0},
    }
    if not products:
        return empty

    n = len(products)
    prices = [p["price"] for p in products if p["price"] > 0]
    avg_price = sum(prices) / len(prices) if prices else 0
    price_std = math.sqrt(sum((x - avg_price) ** 2 for x in prices) / len(prices)) if len(prices) > 1 else 0
    price_cv = price_std / avg_price if avg_price else 0

    rocket_cnt = sum(1 for p in products if p.get("is_rocket") or p.get("is_rocket_wow"))
    rocket_ratio = rocket_cnt / n

    free_cnt = sum(1 for p in products if p.get("is_free_shipping"))
    free_ratio = free_cnt / n

    # 점수 산정 (높을수록 진입 어려움)
    s_rocket = rocket_ratio * 50              # 로켓 비율 (max 50)
    s_free   = free_ratio * 20               # 무료배송 비율 (max 20)
    s_cv     = max(0, (1 - price_cv) * 20)  # 가격 집중도 낮을수록 레드오션 (max 20)
    s_vol    = min(naver_count / 500 * 10, 10) if naver_count else 0  # 시장 규모 (max 10)

    score = round(s_rocket + s_free + s_cv + s_vol)
    score = max(0, min(100, score))

    if score >= 65:
        label, color = "진입 어려움", "#ef4444"
    elif score >= 40:
        label, color = "검토 필요", "#f97316"
    else:
        label, color = "진입 유리", "#22c55e"

    return {
        "score": score,
        "label": label,
        "color": color,
        "rocket_ratio": round(rocket_ratio * 100),
        "free_shipping_ratio": round(free_ratio * 100),
        "avg_price": round(avg_price),
        "min_price": min(prices) if prices else 0,
        "max_price": max(prices) if prices else 0,
        "price_cv": round(price_cv * 100),
        "product_count": n,
        "breakdown": {
            "rocket": round(s_rocket),
            "free_shipping": round(s_free),
            "price_cv": round(s_cv),
            "volume": round(s_vol),
        },
    }


def _price_band_analysis(products: List[Dict]) -> List[Dict]:
    """가격대별 경쟁 분포"""
    if not products:
        return []
    prices = [p["price"] for p in products if p["price"] > 0]
    if not prices:
        return []

    p_max = max(prices)
    step = p_max / 4
    bands = []
    for i in range(4):
        lo = round(step * i / 1000) * 1000
        hi = round(step * (i + 1) / 1000) * 1000
        in_band = [p for p in products if lo <= p["price"] < hi or (i == 3 and p["price"] >= lo)]
        if not in_band:
            continue
        rocket_cnt = sum(1 for p in in_band if p.get("is_rocket") or p.get("is_rocket_wow"))
        free_cnt   = sum(1 for p in in_band if p.get("is_free_shipping"))
        bands.append({
            "label": f"{lo:,}~{hi:,}원" if i < 3 else f"{lo:,}원~",
            "count": len(in_band),
            "rocket_ratio": round(rocket_cnt / len(in_band) * 100),
            "free_shipping_ratio": round(free_cnt / len(in_band) * 100),
            "avg_price": round(sum(p["price"] for p in in_band) / len(in_band)),
        })
    return bands


def _find_sweet_spot(bands: List[Dict]) -> Optional[Dict]:
    """진입 최적 가격대: 로켓 비율 낮고 상품 수 충분한 구간"""
    if not bands:
        return None
    scored = []
    for b in bands:
        if b["count"] < 2:
            continue
        score = (100 - b["rocket_ratio"]) * 0.7 + (100 - b["free_shipping_ratio"]) * 0.3
        scored.append((score, b))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def build_insights(comp: Dict, bands: List[Dict], sweet_spot: Optional[Dict]) -> List[str]:
    insights = []
    rr = comp["rocket_ratio"]
    if rr >= 70:
        insights.append(f"🚀 로켓배송 {rr}% — 쿠팡 직매입 지배적. 일반 판매자 경쟁 매우 치열")
    elif rr >= 40:
        insights.append(f"🚀 로켓배송 {rr}% — 로켓·일반 혼재. 차별화 포인트 필요")
    else:
        insights.append(f"🚀 로켓배송 {rr}% — 일반 판매자 중심 시장. 진입 유리")

    fr = comp["free_shipping_ratio"]
    if fr >= 80:
        insights.append(f"🚚 무료배송 {fr}% — 배송비 경쟁 포화. 상품력·가격으로 차별화 필수")
    elif fr >= 50:
        insights.append(f"🚚 무료배송 {fr}% — 절반 이상 무료배송. 배송 조건 맞추면 경쟁력 있음")
    else:
        insights.append(f"🚚 무료배송 {fr}% — 배송비 경쟁 낮음. 묶음 구성으로 공략 가능")

    cv = comp["price_cv"]
    if cv < 20:
        insights.append(f"💰 가격 변동계수 {cv}% — 가격대 매우 집중. 박리다매 경쟁 주의")
    elif cv > 60:
        insights.append(f"💰 가격 변동계수 {cv}% — 가격대 다양. 프리미엄·저가 포지셔닝 모두 가능")
    else:
        insights.append(f"💰 가격 변동계수 {cv}% — 적당한 가격 분산. 포지셔닝 전략 중요")

    if sweet_spot:
        insights.append(
            f"🎯 추천 진입 가격대: {sweet_spot['label']} "
            f"(로켓 {sweet_spot['rocket_ratio']}% · 상품 {sweet_spot['count']}개)"
        )

    return insights


async def get_recommendations(keywords: Optional[List[str]] = None) -> List[Dict]:
    """키워드 순차 검색 → 소싱 점수 상위 상품 추출 (rate limit 방지로 순차 처리)"""
    import traceback
    if keywords is None:
        keywords = DEFAULT_KEYWORDS
    client = _coupang_client()

    all_products: List[Dict] = []
    for kw in keywords:
        try:
            resp = await client.search(kw, limit=10)
            parsed = client.parse_products(resp)
            print(f"[reco] {kw}: {len(parsed)}개")
            for p in parsed:
                p["keyword"] = kw
                p["sourcing_score"] = score_product(p)
                tags = []
                if not p.get("is_rocket") and not p.get("is_rocket_wow"):
                    tags.append("로켓 없음")
                if not p.get("is_free_shipping"):
                    tags.append("배송 경쟁 낮음")
                if 5000 <= p.get("price", 0) <= 30000:
                    tags.append("적정 가격대")
                p["tags"] = tags
                all_products.append(p)
            await asyncio.sleep(0.3)   # rate limit 방지
        except Exception as e:
            print(f"[reco] {kw} 오류: {e}")
            traceback.print_exc()
            continue

    seen: set = set()
    unique: List[Dict] = []
    for p in sorted(all_products, key=lambda x: -x["sourcing_score"]):
        pid = p.get("product_id")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(p)

    print(f"[reco] 최종 추천 상품: {len(unique)}개")
    return unique[:24]


async def analyze(keyword: str) -> Dict:
    client = _coupang_client()

    resp = await client.search(keyword, limit=100)
    products = client.parse_products(resp)
    landing_url = resp.get("data", {}).get("landingUrl", "")

    naver_count = await _naver_coupang_count(keyword)

    comp = calc_competition(products, naver_count)
    bands = _price_band_analysis(products)
    sweet_spot = _find_sweet_spot(bands)
    insights = build_insights(comp, bands, sweet_spot)

    non_rocket = [p for p in products if not p.get("is_rocket") and not p.get("is_rocket_wow")]

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
        "entry_candidates": non_rocket[:10],
    }
