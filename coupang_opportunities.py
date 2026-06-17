"""
쿠팡 소싱 기회 발굴 모듈
- Naver Shopping API: 시장 규모, 리뷰 경쟁, 가격 분포 (핵심 분석 데이터)
- Coupang Partners API: 로켓배송 비율 (쿠팡 진입 장벽)
- 두 데이터를 결합해 "소싱 기회 점수" 산출
"""
import asyncio
import os
from typing import Dict, List, Optional

import httpx

from coupang_api import CoupangPartnersAPI

# 자동 스캔 키워드 — 부자주방 채널 포커스 (주방·식당용품)
SCAN_KEYWORDS = [
    # 조리도구
    "집게", "국자", "주걱", "뒤집개", "소쿠리",
    # 식당용 소모품
    "앞치마", "행주", "비닐장갑",
    # 식기·용기
    "도마", "볼",
    # 식당 인테리어·집기
    "수저통", "메뉴판", "영수증함",
    # 주방 정리
    "냄비받침", "후크", "걸이",
    # 업소용 (네이버만 지원)
    "업소용냉장고", "스테인레스선반", "업소용가스레인지",
    "업소용싱크대", "업소용작업대", "냄비뚜껑거치대",
    "스테인레스냄비", "업소용접시", "주방선반",
]


def _coupang_client() -> CoupangPartnersAPI:
    return CoupangPartnersAPI(
        os.getenv("COUPANG_ACCESS_KEY", "").strip(),
        os.getenv("COUPANG_SECRET_KEY", "").strip(),
    )


async def _naver_keyword_analysis(keyword: str, client: httpx.AsyncClient) -> Dict:
    """네이버 쇼핑 API로 키워드 시장 분석"""
    cid = os.getenv("NAVER_CLIENT_ID", "").strip()
    csk = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not csk:
        return {}
    try:
        r = await client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": keyword, "display": 100, "sort": "sim"},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csk},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        total = int(data.get("total", 0))
        if not items:
            return {"total": total, "avg_reviews": 0, "avg_price": 0, "coupang_ratio": 0}

        prices = [int(i.get("lprice") or 0) for i in items if int(i.get("lprice") or 0) > 0]
        reviews = [int(i.get("reviewCount") or 0) for i in items]
        coupang_cnt = sum(1 for i in items if "쿠팡" in i.get("mallName", ""))

        return {
            "total": total,
            "avg_reviews": round(sum(reviews) / len(reviews)) if reviews else 0,
            "avg_price": round(sum(prices) / len(prices)) if prices else 0,
            "min_price": min(prices) if prices else 0,
            "coupang_ratio": round(coupang_cnt / len(items) * 100) if items else 0,
            "coupang_count": coupang_cnt,
            "sample_image": items[0].get("image", "") if items else "",
        }
    except Exception as e:
        print(f"[opp] naver {keyword}: {e}")
        return {}


async def _coupang_rocket_ratio(keyword: str, cp: CoupangPartnersAPI) -> Dict:
    """쿠팡 파트너스 API로 로켓 비율 조회"""
    try:
        resp = await cp.search(keyword, limit=20)
        products = cp.parse_products(resp)
        if not products:
            return {"rocket_ratio": -1, "product_count": 0}
        rocket_cnt = sum(1 for p in products if p.get("is_rocket") or p.get("is_rocket_wow"))
        return {
            "rocket_ratio": round(rocket_cnt / len(products) * 100),
            "product_count": len(products),
            "sample_image": products[0].get("image", "") if products else "",
            "landing_url": resp.get("data", {}).get("landingUrl", ""),
        }
    except Exception:
        return {"rocket_ratio": -1, "product_count": 0}


def _calc_opportunity_score(naver: Dict, coupang: Dict) -> int:
    """소싱 기회 점수 (0-100, 높을수록 진입 유리)"""
    score = 0

    # 1. 시장 수요 — 네이버 전체 검색 결과 수 (max 20점)
    total = naver.get("total", 0)
    if total >= 50000:    score += 20
    elif total >= 10000:  score += 15
    elif total >= 3000:   score += 10
    elif total >= 1000:   score += 5

    # 2. 리뷰 경쟁 낮음 — 리뷰 적을수록 진입 쉬움 (max 35점)
    avg_rev = naver.get("avg_reviews", 0)
    if avg_rev == 0:       score += 35
    elif avg_rev < 50:     score += 30
    elif avg_rev < 200:    score += 22
    elif avg_rev < 500:    score += 14
    elif avg_rev < 1000:   score += 6

    # 3. 쿠팡 로켓 낮음 — 쿠팡 직매입 적을수록 일반 판매자 기회 (max 30점)
    rr = coupang.get("rocket_ratio", -1)
    if rr == -1:           score += 15   # 데이터 없음: 중간값
    elif rr <= 10:         score += 30
    elif rr <= 30:         score += 22
    elif rr <= 50:         score += 12
    elif rr <= 70:         score += 5

    # 4. 가격대 적합 — 마진 나오는 가격대 (max 15점)
    avg_price = naver.get("avg_price", 0)
    if 5000 <= avg_price <= 50000:    score += 15
    elif 3000 <= avg_price <= 100000: score += 8

    return min(100, score)


def _opportunity_label(score: int) -> tuple:
    if score >= 70:
        return "소싱 강추", "#22c55e"
    elif score >= 50:
        return "진입 유리", "#3b82f6"
    elif score >= 35:
        return "검토 필요", "#f97316"
    else:
        return "경쟁 치열", "#ef4444"


async def scan_opportunities(keywords: Optional[List[str]] = None) -> List[Dict]:
    """키워드 목록 스캔 → 소싱 기회 점수 상위 순 반환"""
    if keywords is None:
        keywords = SCAN_KEYWORDS

    cp = _coupang_client()

    async with httpx.AsyncClient() as http:
        # 네이버 API: 동시 요청 (빠름)
        naver_tasks = [_naver_keyword_analysis(kw, http) for kw in keywords]
        naver_results = await asyncio.gather(*naver_tasks, return_exceptions=True)

    # 쿠팡 Partners API: 순차 (rate limit)
    coupang_results = []
    for kw in keywords:
        r = await _coupang_rocket_ratio(kw, cp)
        coupang_results.append(r)
        await asyncio.sleep(0.25)

    opportunities = []
    for kw, nv, cp_r in zip(keywords, naver_results, coupang_results):
        if isinstance(nv, Exception) or not nv:
            nv = {}
        score = _calc_opportunity_score(nv, cp_r)
        label, color = _opportunity_label(score)

        image = nv.get("sample_image") or cp_r.get("sample_image", "")

        opportunities.append({
            "keyword": kw,
            "score": score,
            "label": label,
            "color": color,
            "image": image,
            # 네이버 데이터
            "total": nv.get("total", 0),
            "avg_reviews": nv.get("avg_reviews", 0),
            "avg_price": nv.get("avg_price", 0),
            "min_price": nv.get("min_price", 0),
            "coupang_ratio": nv.get("coupang_ratio", 0),
            # 쿠팡 데이터
            "rocket_ratio": cp_r.get("rocket_ratio", -1),
            "cp_product_count": cp_r.get("product_count", 0),
            "landing_url": cp_r.get("landing_url", ""),
        })

    return sorted(opportunities, key=lambda x: -x["score"])
