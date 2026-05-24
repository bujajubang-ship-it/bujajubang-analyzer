import asyncio
import html
import math
import os
import re

import httpx


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


MALL_TYPE = {
    "쿠팡": "coupang",
    "11번가": "11st",
    "G마켓": "gmarket",
    "옥션": "auction",
    "SSG.COM": "ssg",
    "SSG닷컴": "ssg",
    "롯데온": "lotte",
    "네이버쇼핑": "naver",
    "위메프": "wemakeprice",
    "티몬": "tmon",
    "인터파크": "interpark",
    "오늘의집": "today",
    "29CM": "29cm",
}

MALL_EMOJI = {
    "coupang": "🛍️",
    "11st": "🔴",
    "gmarket": "🟡",
    "auction": "🟠",
    "ssg": "🟣",
    "lotte": "🔴",
    "naver": "🟢",
    "wemakeprice": "🔵",
    "tmon": "🟤",
    "interpark": "🟡",
    "today": "⚫",
    "29cm": "⚫",
    "other": "⬜",
}


def _mall_type(name: str) -> str:
    for k, v in MALL_TYPE.items():
        if k in name:
            return v
    return "other"


async def scrape(keyword: str, on_step=None) -> tuple[list[dict], int]:
    import inspect

    async def step(msg):
        if on_step:
            result = on_step(msg)
            if inspect.iscoroutine(result):
                await result

    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        raise RuntimeError("NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 .env 파일에 설정해주세요.")

    await step("네이버 쇼핑 API 검색 중...")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": keyword, "display": 100, "sort": "sim"},
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            },
        )
        r.raise_for_status()
        data = r.json()

    total = data.get("total", 0)
    items = data.get("items", [])

    await step(f"상품 {len(items)}개 수집 완료. 분석 중...")

    products = []
    for i, item in enumerate(items):
        name = _strip_tags(item.get("title", ""))
        if not name:
            continue

        lprice = int(item.get("lprice") or 0)
        hprice = int(item.get("hprice") or 0)
        if lprice == 0:
            continue

        mall_name = item.get("mallName", "기타")
        mtype = _mall_type(mall_name)
        brand = _strip_tags(item.get("brand") or item.get("maker") or "")
        cat = " > ".join(
            filter(None, [item.get(f"category{j}", "") for j in range(1, 5)])
        )

        products.append({
            "rank": i + 1,
            "name": name,
            "brand": brand or mall_name,
            "price": lprice,
            "original_price": hprice if hprice > lprice else lprice,
            "discount": round((hprice - lprice) / hprice * 100) if hprice > lprice else 0,
            "reviews": 0,
            "rating": 0.0,
            "delivery": mtype,
            "mall": mall_name,
            "category": cat,
            "ad": False,
            "url": item.get("link", ""),
        })

    # Re-rank
    for i, p in enumerate(products):
        p["rank"] = i + 1

    return products, total


def calc_competition(products: list) -> dict:
    if not products:
        return {
            "score": 0, "label": "데이터 없음", "color": "#6b7280",
            "mall_counts": {}, "top_mall": "-",
            "avg_price": 0, "min_price": 0, "max_price": 0,
            "price_range_ratio": 0, "unique_malls": 0,
        }

    n = len(products)
    prices = [p["price"] for p in products]
    avg_price = sum(prices) / n
    price_std = math.sqrt(sum((p - avg_price) ** 2 for p in prices) / n) if n > 1 else 0

    # 판매처 집계
    mall_counts: dict[str, int] = {}
    for p in products:
        mall_counts[p["mall"]] = mall_counts.get(p["mall"], 0) + 1
    top_mall = max(mall_counts, key=mall_counts.get) if mall_counts else "-"
    unique_malls = len(mall_counts)

    # 경쟁강도 계산
    # 1) 가격 밀집도 (가격 범위가 좁을수록 성숙 시장)
    price_cv = price_std / avg_price if avg_price else 0  # 변동계수
    score_price = max(0, 40 - price_cv * 60)  # CV 낮을수록 경쟁 치열

    # 2) 상품 수 (많을수록 경쟁 치열)
    score_volume = min(n / 100 * 30, 30)

    # 3) 판매처 집중도 (소수 판매처 독점일수록 진입 어려움)
    top_ratio = mall_counts.get(top_mall, 0) / n if n else 0
    score_concentration = top_ratio * 30  # 1개 판매처가 독점 → 30점

    score = round(score_price + score_volume + score_concentration)
    score = max(0, min(100, score))

    if score >= 80:
        label, color = "매우 치열", "#ef4444"
    elif score >= 60:
        label, color = "치열", "#f97316"
    elif score >= 40:
        label, color = "보통", "#eab308"
    else:
        label, color = "진입 유리", "#22c55e"

    price_range_ratio = round((max(prices) - min(prices)) / avg_price * 100) if avg_price else 0

    return {
        "score": score, "label": label, "color": color,
        "mall_counts": mall_counts,
        "top_mall": top_mall,
        "avg_price": round(avg_price),
        "min_price": min(prices),
        "max_price": max(prices),
        "price_range_ratio": price_range_ratio,
        "unique_malls": unique_malls,
    }
