from __future__ import annotations
import asyncio
import json
import os
import re
import random
import math
import smtplib
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import quote as urlquote
from pathlib import Path
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
import base64
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from scraper import scrape, calc_competition
from supplier_1688 import fetch_1688_suppliers
from sourcing_data import SOURCING_CANDIDATES
from coupang_api import CoupangPartnersAPI
from coupang_analysis import analyze as coupang_analyze, get_recommendations as coupang_reco
from coupang_opportunities import scan_opportunities
from page_maker import scrape_images, build_processed_zip, analyze_product_image, stream_ai_sections, scrape_and_analyze_url

load_dotenv()

def _coupang_client() -> CoupangPartnersAPI | None:
    ak = os.getenv("COUPANG_ACCESS_KEY", "").strip()
    sk = os.getenv("COUPANG_SECRET_KEY", "").strip()
    return CoupangPartnersAPI(ak, sk) if ak and sk else None

app = FastAPI(title="쿠팡 상품 분석기")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SearchRequest(BaseModel):
    keyword: str


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


DUMMY_POOL = [
    {"name": "{kw} 업소용 2도어 스텐 300L", "brand": "린나이", "price": 485000, "mall": "쿠팡"},
    {"name": "{kw} 스탠딩 대용량 500L 4도어", "brand": "삼성전자", "price": 1250000, "mall": "G마켓"},
    {"name": "업소용 {kw} 직냉식 300L 화이트", "brand": "대우루컴즈", "price": 320000, "mall": "11번가"},
    {"name": "{kw} 영업용 소형 150L 실버", "brand": "클라쎄", "price": 198000, "mall": "쿠팡"},
    {"name": "상업용 {kw} 2도어 450L", "brand": "하이마트", "price": 620000, "mall": "SSG.COM"},
    {"name": "{kw} 유리도어 업소용", "brand": "아이스트로", "price": 285000, "mall": "옥션"},
    {"name": "음식점 {kw} 직냉 2단", "brand": "동양매직", "price": 375000, "mall": "쿠팡"},
    {"name": "{kw} 업소용 대용량 600L", "brand": "LG전자", "price": 1680000, "mall": "롯데온"},
    {"name": "주방 {kw} 소형 120L 실버", "brand": "캐리어냉장", "price": 155000, "mall": "쿠팡"},
    {"name": "{kw} 영업용 350L 간냉식", "brand": "위니아", "price": 430000, "mall": "11번가"},
    {"name": "업소용 {kw} 냉동냉장 250L", "brand": "파세코", "price": 510000, "mall": "G마켓"},
    {"name": "{kw} 가정업소용 400L", "brand": "코웨이", "price": 390000, "mall": "쿠팡"},
    {"name": "식당용 {kw} 2도어 스텐", "brand": "신일", "price": 268000, "mall": "위메프"},
    {"name": "{kw} 직냉 업소용 200L", "brand": "오텍캐리어", "price": 235000, "mall": "옥션"},
    {"name": "상업용 {kw} 올스텐 300L", "brand": "나우이엔씨", "price": 445000, "mall": "인터파크"},
    {"name": "{kw} 업소용 미니 80L", "brand": "아이엠", "price": 128000, "mall": "쿠팡"},
    {"name": "업소 {kw} 찬합 280L", "brand": "삼아스틸", "price": 355000, "mall": "11번가"},
    {"name": "{kw} 음료 쇼케이스 350L", "brand": "에스코", "price": 580000, "mall": "G마켓"},
    {"name": "카페 {kw} 수직형 450L", "brand": "그레이스", "price": 695000, "mall": "쿠팡"},
    {"name": "{kw} 업소용 바퀴형 250L", "brand": "라셀르", "price": 412000, "mall": "SSG.COM"},
]


def make_dummy(keyword: str):
    random.seed(hash(keyword) % 9999)
    sample = random.sample(DUMMY_POOL, min(20, len(DUMMY_POOL)))
    from scraper import _mall_type
    products = []
    for i, p in enumerate(sample):
        noise = random.uniform(0.85, 1.15)
        price = round(p["price"] * noise / 100) * 100
        products.append({
            "rank": i + 1,
            "name": p["name"].replace("{kw}", keyword),
            "brand": p["brand"],
            "price": price,
            "original_price": price,
            "discount": 0,
            "reviews": 0,
            "rating": 0.0,
            "delivery": _mall_type(p["mall"]),
            "mall": p["mall"],
            "category": "",
            "ad": False,
            "url": "",
        })
    return products


@app.get("/")
async def root():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    return {
        "naver": bool(os.getenv("NAVER_CLIENT_ID")),
        "coupang": bool(os.getenv("COUPANG_ACCESS_KEY")),
    }


@app.get("/api/coupang/search")
async def coupang_search(keyword: str, limit: int = 20):
    """쿠팡파트너스 API로 실제 상품 검색"""
    client = _coupang_client()
    if not client:
        return JSONResponse({"error": "COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 환경변수 미설정"}, status_code=500)
    try:
        resp = await client.search(keyword, limit=min(limit, 100))
        products = client.parse_products(resp)
        return JSONResponse({
            "keyword": keyword,
            "count": len(products),
            "products": products,
            "landing_url": resp.get("data", {}).get("landingUrl", ""),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/coupang/debug")
async def coupang_debug(keyword: str = "집게"):
    """원시 API 응답 확인용 디버그 엔드포인트"""
    client = _coupang_client()
    if not client:
        return JSONResponse({"error": "키 미설정"}, status_code=500)
    try:
        resp = await client.search(keyword, limit=5)
        return JSONResponse({"raw": resp})
    except Exception as e:
        return JSONResponse({"error": str(e), "type": type(e).__name__}, status_code=500)


@app.get("/api/coupang/debug-reco")
async def coupang_debug_reco():
    """추천 키워드별 결과 수 진단"""
    from coupang_analysis import DEFAULT_KEYWORDS
    client = _coupang_client()
    if not client:
        return JSONResponse({"error": "키 미설정"}, status_code=500)
    results = {}
    for kw in DEFAULT_KEYWORDS:
        try:
            resp = await client.search(kw, limit=5)
            products = client.parse_products(resp)
            results[kw] = {"count": len(products), "rCode": resp.get("rCode")}
        except Exception as e:
            results[kw] = {"error": str(e)}
        import asyncio
        await asyncio.sleep(0.3)
    return JSONResponse(results)


@app.get("/api/coupang/analyze")
async def coupang_analyze_endpoint(keyword: str):
    """쿠팡 소싱 분석 — Partners API + Naver API 통합"""
    if not os.getenv("COUPANG_ACCESS_KEY"):
        return JSONResponse({"error": "COUPANG_ACCESS_KEY 환경변수 미설정"}, status_code=500)
    try:
        result = await coupang_analyze(keyword)
        return JSONResponse(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/coupang/opportunities")
async def coupang_opportunities_endpoint():
    """네이버 + 쿠팡 파트너스 결합 소싱 기회 스캔"""
    try:
        results = await scan_opportunities()
        return JSONResponse({"opportunities": results})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/coupang/recommendations")
async def coupang_recommendations():
    """쿠팡 소싱 추천 상품 — 복수 키워드 병렬 검색 후 소싱 점수 상위 반환"""
    if not os.getenv("COUPANG_ACCESS_KEY"):
        return JSONResponse({"error": "COUPANG_ACCESS_KEY 미설정"}, status_code=500)
    try:
        products = await coupang_reco()
        return JSONResponse({"products": products})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/coupang/bestsellers")
async def coupang_bestsellers(category_id: str = "1", limit: int = 20):
    """쿠팡파트너스 API 카테고리별 베스트셀러"""
    client = _coupang_client()
    if not client:
        return JSONResponse({"error": "COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 환경변수 미설정"}, status_code=500)
    try:
        resp = await client.best_sellers(category_id, limit=min(limit, 100))
        products = client.parse_products(resp)
        return JSONResponse({"category_id": category_id, "count": len(products), "products": products})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/search")
async def search(req: SearchRequest):
    kw = req.keyword.strip()

    async def stream():
        if not kw:
            yield sse({"step": "error", "message": "키워드를 입력해주세요."})
            return

        products, total, is_dummy = [], 0, False

        try:
            yield sse({"step": "start", "message": "네이버 쇼핑 API 검색 중..."})

            step_msgs: list[str] = []
            products, total = await scrape(kw, on_step=lambda m: step_msgs.append(m))

            for msg in step_msgs:
                yield sse({"step": "progress", "message": msg})

            if not products:
                raise RuntimeError("검색 결과가 없습니다. 다른 키워드를 시도해주세요.")

            yield sse({"step": "progress", "message": f"상품 {len(products)}개 수집 완료!"})

        except Exception as e:
            err = str(e)
            if "NAVER_CLIENT" in err:
                yield sse({"step": "error", "message": err})
                return
            yield sse({"step": "progress", "message": f"API 오류 — 더미 데이터로 대체합니다: {err}"})
            products = make_dummy(kw)
            total = random.randint(500, 5000)
            is_dummy = True

        stats = calc_competition(products)

        yield sse({
            "step": "done",
            "keyword": kw,
            "total_results": total,
            "products": products[:50],
            "stats": stats,
            "is_dummy": is_dummy,
            "source": "네이버 쇼핑",
        })

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 소싱 추천 ────────────────────────────────────────────────────────

# 카테고리별 신뢰 공급업체 풀
_SUPPLIER_POOL = {
    "주방소품":   [("厨房硅胶用品总厂",True,4.9,"월 5,200건",6),("防烫厨具旗舰店",True,4.8,"월 3,100건",4),("厨房百货批发中心",False,4.5,"월 980건",2)],
    "조리도구":   [("不锈钢厨具批发",True,4.9,"월 6,800건",7),("商用炊具专营店",True,4.7,"월 2,900건",5),("烹饪工具制造厂",True,4.8,"월 4,100건",4)],
    "냉장고 용품":[("冰箱收纳专营",True,4.8,"월 3,400건",5),("透明储物用品厂",True,4.7,"월 2,100건",3),("厨房整理百货",False,4.4,"월 750건",2)],
    "싱크대 용품":[("厨房卫浴用品厂",True,4.9,"월 4,200건",6),("水槽配件专营",True,4.6,"월 1,800건",4),("沥水置物架厂",True,4.7,"월 2,500건",3)],
    "수납/정리":  [("厨房收纳旗舰",True,4.8,"월 5,600건",5),("置物架制造厂",True,4.9,"월 7,200건",8),("家居整理批发",False,4.5,"월 1,200건",3)],
    "식기류":     [("不锈钢餐具厂",True,4.9,"월 8,400건",9),("商用餐厅用品",True,4.8,"월 5,300건",6),("韩式餐具批发",True,4.7,"월 3,100건",4)],
    "위생용품":   [("一次性卫生用品厂",True,4.8,"월 9,100건",6),("厨房清洁专营",True,4.7,"월 4,500건",5),("商用清洁用品",False,4.4,"월 1,600건",2)],
    "포장용품":   [("食品包装材料厂",True,4.9,"월 12,000건",7),("外卖包装专营",True,4.8,"월 8,300건",5),("一次性餐盒批发",True,4.7,"월 5,400건",4)],
    "배달용품":   [("外卖保温箱厂",True,4.8,"월 3,900건",5),("商用送餐用品",True,4.7,"월 2,700건",4),("保温包装专营",False,4.5,"월 1,100건",2)],
    "카페용품":   [("咖啡器具旗舰",True,4.9,"월 4,600건",6),("奶茶饮品用品厂",True,4.8,"월 3,200건",4),("咖啡配件专营",True,4.7,"월 2,100건",3)],
    "제과/제빵":  [("烘焙器具专营",True,4.9,"월 5,800건",7),("蛋糕模具制造",True,4.8,"월 3,700건",5),("烘焙用品批发",True,4.7,"월 2,400건",3)],
    "홀용품":     [("餐厅用品旗舰",True,4.8,"월 3,300건",5),("商用餐饮备品",True,4.7,"월 2,100건",4),("酒店餐具批发",False,4.5,"월 890건",2)],
    "안전용품":   [("厨房安全用品厂",True,4.8,"월 2,100건",5),("防护用品专营",True,4.7,"월 1,600건",3),("商用安全器材",False,4.4,"월 720건",2)],
    "주방기기":   [("商用厨房设备厂",True,4.9,"월 1,800건",8),("厨房电器旗舰",True,4.8,"월 2,300건",6),("专业厨具制造",True,4.7,"월 1,400건",4)],
    "주방 의류":  [("厨师服装专营",True,4.8,"월 3,600건",5),("餐饮工作服厂",True,4.7,"월 2,200건",4),("防水围裙制造",False,4.5,"월 980건",2)],
    "환경용품":   [("环保餐具批发",True,4.8,"월 2,400건",5),("垃圾分类用品",True,4.6,"월 1,300건",3),("厨余处理用品",False,4.4,"월 680건",2)],
}
_DEFAULT_SUPPLIERS = [("厨房用品旗舰店",True,4.8,"월 3,500건",5),("商用厨具专营",True,4.7,"월 2,100건",4),("厨房百货批发",False,4.5,"월 950건",2)]

def _get_suppliers(c: dict) -> list[dict]:
    pool = _SUPPLIER_POOL.get(c["category"], _DEFAULT_SUPPLIERS)
    seed = c["id"] % len(pool)
    selected = pool[seed:seed+2] + pool[:max(0, 2-(len(pool)-seed))]
    # 1688 키워드로 실력상가(实力商家) 필터 검색 URL
    kw = c["keywords_1688"][0] if c.get("keywords_1688") else c["name"]
    result = []
    for name, badge, rating, sales, years in selected[:2]:
        # 실력상가 뱃지 있는 경우 1688 实力商家 필터로, 없으면 일반 검색
        if badge:
            url = (
                "https://s.1688.com/selloffer/offerlist.htm"
                "?keywords=" + kw +
                "&n=y&ispro=true&sortType=va_asc"
            )
        else:
            url = (
                "https://s.1688.com/selloffer/offerlist.htm"
                "?keywords=" + kw +
                "&n=y&sortType=va_asc"
            )
        result.append({"name": name, "badge": badge, "rating": rating, "sales": sales, "years": years, "url": url})
    return result

_image_cache: dict[str, str] = {}


async def _fetch_image(name: str) -> str:
    if name in _image_cache:
        return _image_cache[name]
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return ""

    async def _search(query: str, client: httpx.AsyncClient) -> str:
        r = await client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": query, "display": 1, "sort": "sim"},
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        return items[0].get("image", "") if items else ""

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            url = await _search(name, client)
            if not url:
                # Strip numbers/units (20p, 3L, 10개 etc.) and take first 3 words
                simplified = re.sub(r'\s*\d+[a-zA-Z가-힣]*', '', name).strip()
                words = simplified.split()
                if len(words) > 3:
                    simplified = ' '.join(words[:3])
                if simplified and simplified != name:
                    url = await _search(simplified, client)
            _image_cache[name] = url
            return url
    except Exception:
        return ""


def _get_moq(selling: int) -> int:
    if selling < 10000: return 50
    if selling < 20000: return 30
    if selling < 35000: return 20
    return 10


def _enrich_candidate(c: dict) -> dict:
    commission = round(c["selling"] * 0.108)
    net = c["selling"] - c["sourcing"] - c["logistics"] - commission
    margin_rate = round(net / c["selling"] * 100)
    comp = c["competition"]["intensity"]
    s_competition = max(0, 35 - round(comp * 0.35))
    s_margin = min(30, round(margin_rate * 0.4))
    s_relevance = 18 if c["channel"] == "both" else 12
    s_customer = 12 if c["customer_fit"] else 4
    score = s_competition + s_margin + s_relevance + s_customer
    comp_label = "낮음" if comp < 30 else ("보통" if comp < 50 else "높음")
    price_ok = 15000 <= c["selling"] <= 50000

    moq = _get_moq(c["selling"])
    total_purchase = c["sourcing"] * moq
    total_profit = net * moq
    break_even = math.ceil(total_purchase / net) if net > 0 else 0

    if score >= 80:   rec_label, rec_cls = "소싱추천", "high"
    elif score >= 60: rec_label, rec_cls = "검토",    "mid"
    else:             rec_label, rec_cls = "비추",    "low"

    return {
        **c,
        "margin": {"net": net, "rate": margin_rate, "commission": commission},
        "score": score,
        "rec_label": rec_label, "rec_cls": rec_cls,
        "score_breakdown": {"competition": s_competition, "margin": s_margin, "relevance": s_relevance, "customer_fit": s_customer},
        "competition": {**c["competition"], "label": comp_label},
        "moq": {"qty": moq, "total_purchase": total_purchase, "total_profit": total_profit, "break_even": break_even},
        "filters": {
            "reviews_ok": c["competition"]["top_reviews"] <= 500,
            "rocket_ok": c["competition"]["rocket_ratio"] <= 50,
            "price_ok": price_ok,
        },
        "suppliers": _get_suppliers(c),
    }


PER_PAGE = 100

@app.get("/api/coupang-market")
async def coupang_market(keyword: str):
    """네이버 쇼핑 API로 쿠팡 시장 데이터 추출"""
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return JSONResponse({"error": "no key"}, status_code=500)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://openapi.naver.com/v1/search/shop.json",
                params={"query": keyword, "display": 100, "sort": "sim"},
                headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    items = data.get("items", [])
    coupang = [i for i in items if "쿠팡" in i.get("mallName", "")]
    prices = [int(i["lprice"]) for i in coupang if int(i.get("lprice") or 0) > 0]

    return JSONResponse({
        "total": data.get("total", 0),
        "coupang_count": len(coupang),
        "coupang_ratio": round(len(coupang) / len(items) * 100) if items else 0,
        "price_min": min(prices) if prices else 0,
        "price_avg": round(sum(prices) / len(prices)) if prices else 0,
        "price_max": max(prices) if prices else 0,
    })

@app.get("/api/sourcing/suppliers")
async def get_real_suppliers(keyword: str = ""):
    """1688 실력상가 실제 상품 데이터 반환"""
    if not keyword:
        return JSONResponse({"suppliers": []})
    suppliers = await fetch_1688_suppliers(keyword, top_n=3)
    return JSONResponse({"suppliers": suppliers})

@app.get("/api/sourcing")
async def get_sourcing(channel: str = "all", page: int = 1):
    enriched = [_enrich_candidate(c) for c in SOURCING_CANDIDATES]
    if channel in ("coupang", "both"):
        enriched = [c for c in enriched if c["channel"] == channel]
    enriched.sort(key=lambda x: x["score"], reverse=True)
    for i, c in enumerate(enriched):
        c["rank"] = i + 1

    total = len(enriched)
    total_pages = math.ceil(total / PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PER_PAGE
    page_items = enriched[start:start + PER_PAGE]

    avg_margin = round(sum(c["margin"]["rate"] for c in page_items) / len(page_items)) if page_items else 0
    return {
        "candidates": page_items,
        "meta": {
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "avg_margin": avg_margin,
            "last_run": "2026-05-26 00:00 (월)",
            "next_run": "2026-06-02 00:00 (월)",
            "is_dummy": True,
        },
    }


@app.get("/api/image")
async def get_image(name: str):
    url = await _fetch_image(name)
    return {"url": url}


# ── Memo API (서버 저장 — 브라우저/기기 무관하게 유지) ──────────────
MEMOS_FILE = Path("memos.json")

def _load_memos() -> dict:
    try:
        return json.loads(MEMOS_FILE.read_text(encoding="utf-8")) if MEMOS_FILE.exists() else {}
    except Exception:
        return {}

def _save_memos(memos: dict):
    MEMOS_FILE.write_text(json.dumps(memos, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/memos")
async def get_memos():
    return _load_memos()


@app.post("/api/memos/{memo_id}")
async def upsert_memo(memo_id: str, request: Request):
    data = await request.json()
    memos = _load_memos()
    memos[memo_id] = data
    _save_memos(memos)
    asyncio.create_task(_send_memo_email(data.get("name", ""), data.get("text", ""), data.get("savedAt", "")))
    return {"ok": True}


@app.delete("/api/memos/{memo_id}")
async def delete_memo(memo_id: str):
    memos = _load_memos()
    memos.pop(memo_id, None)
    _save_memos(memos)
    return {"ok": True}


# ── 품목 진행상황 트래커 ─────────────────────────────────────────────
TRACKER_FILE = Path("tracker.json")

def _load_tracker() -> dict:
    try:
        return json.loads(TRACKER_FILE.read_text(encoding="utf-8")) if TRACKER_FILE.exists() else {}
    except Exception:
        return {}

def _save_tracker(data: dict):
    TRACKER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/tracker")
async def get_tracker():
    return _load_tracker()


TRACKER_FIELDS = (
    "name", "pipeline", "stage",
    "link1688", "orderOption", "unitPrice", "orderQty",
    "coupangLink", "initialPrice", "expectedRevenue", "memo",
)


@app.post("/api/tracker")
async def create_tracker(request: Request):
    data = await request.json()
    items = _load_tracker()
    new_id = str(max([int(k) for k in items.keys() if k.isdigit()] or [0]) + 1)
    item = {k: "" for k in TRACKER_FIELDS}
    item["pipeline"] = data.get("pipeline", "sourcing")
    item["stage"] = data.get("stage", "keyword")
    for k in TRACKER_FIELDS:
        if k in data:
            item[k] = data[k]
    items[new_id] = item
    _save_tracker(items)
    return {"ok": True, "id": new_id}


@app.put("/api/tracker/{item_id}")
async def update_tracker(item_id: str, request: Request):
    data = await request.json()
    items = _load_tracker()
    if item_id not in items:
        return {"ok": False}
    items[item_id].update({k: v for k, v in data.items() if k in TRACKER_FIELDS})
    _save_tracker(items)
    return {"ok": True}


@app.delete("/api/tracker/{item_id}")
async def delete_tracker(item_id: str):
    items = _load_tracker()
    items.pop(item_id, None)
    _save_tracker(items)
    return {"ok": True}


# ── 메모 이메일 발송 ─────────────────────────────────────────────────
MEMO_TO = "tjdrjs0007@naver.com"

def _send_memo_email_sync(name: str, text: str, saved_at: str):
    sender = os.getenv("EMAIL_FROM", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "").strip()
    if not sender or not password:
        return

    naver_url = f"https://search.shopping.naver.com/search/all?query={urlquote(name)}"
    site_url = "https://bujajubang-analyzer.onrender.com"

    html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <div style="background:#fefce8;border:1.5px solid #fde047;border-radius:12px;padding:20px 24px;margin-bottom:16px">
        <div style="font-size:13px;color:#92400e;font-weight:700;margin-bottom:8px">📌 소싱 메모</div>
        <div style="font-size:18px;font-weight:800;color:#111827;margin-bottom:12px">{name}</div>
        <div style="font-size:15px;color:#374151;line-height:1.7;white-space:pre-wrap">{text}</div>
      </div>
      <div style="display:flex;gap:10px;margin-bottom:16px">
        <a href="{naver_url}" style="flex:1;display:block;padding:12px;background:#03c75a;color:#fff;text-align:center;border-radius:8px;font-weight:700;text-decoration:none;font-size:14px">🔍 네이버 쇼핑 검색</a>
        <a href="{site_url}" style="flex:1;display:block;padding:12px;background:#111827;color:#fff;text-align:center;border-radius:8px;font-weight:700;text-decoration:none;font-size:14px">📦 소싱 사이트 열기</a>
      </div>
      <div style="font-size:12px;color:#9ca3af;text-align:right">{saved_at}</div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📌 소싱 메모: {name}"
    msg["From"] = sender
    msg["To"] = MEMO_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, MEMO_TO, msg.as_string())
    except Exception as e:
        print(f"[email] 발송 실패: {e}")


async def _send_memo_email(name: str, text: str, saved_at: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_memo_email_sync, name, text, saved_at)


@app.get("/api/myip")
async def my_ip():
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get("https://api.ipify.org?format=json")
        return r.json()


@app.post("/api/sms/send")
async def sms_send_proxy(request: Request):
    """알리고 SMS 발송 프록시 — Vercel에서 호출, Render 고정 IP로 발송"""
    data = await request.json()
    phone = data.get("phone", "")
    msg = data.get("msg", "")
    if not phone or not msg:
        return JSONResponse({"result_code": "-1", "message": "파라미터 누락"}, status_code=400)

    body = urllib.parse.urlencode({
        "key": os.getenv("ALIGO_API_KEY", ""),
        "user_id": os.getenv("ALIGO_USER_ID", ""),
        "sender": os.getenv("ALIGO_SENDER", ""),
        "receiver": phone,
        "msg": msg,
    })
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://apis.aligo.in/send/",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    return JSONResponse(r.json())


# ── 상세페이지 메이커 ──────────────────────────────────────────────────

@app.post("/api/pagemaker/scrape")
async def pagemaker_scrape(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "URL이 필요합니다"}, status_code=400)
    result = await scrape_images(url)
    return JSONResponse(result)


@app.post("/api/pagemaker/process")
async def pagemaker_process(request: Request):
    import base64
    data = await request.json()
    image_urls: list = data.get("images", [])
    is_main: list = data.get("is_main", [False] * len(image_urls))
    logo_b64: str = data.get("logo_b64", "")
    logo_position: str = data.get("logo_position", "bottom-right")
    logo_size_pct: float = float(data.get("logo_size_pct", 0.15))
    use_ai: bool = bool(data.get("use_ai", False))
    product_title: str = data.get("product_title", "")
    product_desc: str = data.get("product_desc", "")
    remove_logo_b64: str = data.get("remove_logo_b64", "")

    if not image_urls:
        return JSONResponse({"error": "이미지가 없습니다"}, status_code=400)

    logo_bytes        = base64.b64decode(logo_b64)        if logo_b64        else None
    remove_logo_bytes = base64.b64decode(remove_logo_b64) if remove_logo_b64 else None

    zip_data = await build_processed_zip(
        image_urls=image_urls,
        logo_bytes=logo_bytes,
        logo_position=logo_position,
        logo_size_pct=logo_size_pct,
        is_main=is_main,
        use_ai=use_ai,
        product_title=product_title,
        product_desc=product_desc,
        remove_logo_bytes=remove_logo_bytes,
    )

    return StreamingResponse(
        iter([zip_data]),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=detail_images.zip"},
    )


@app.post("/api/pagemaker/ai-analyze")
async def pm_ai_analyze(request: Request):
    data = await request.json()
    image_url = (data.get("image_url") or "").strip()
    if not image_url:
        return JSONResponse({"error": "image_url 필요"}, status_code=400)
    result = await analyze_product_image(image_url)
    return JSONResponse(result)


@app.post("/api/pagemaker/analyze-url")
async def pm_analyze_url(request: Request):
    """상품 URL → 페이지 전체 스크래핑 + Gemini 종합 분석 (텍스트+이미지)"""
    data = await request.json()
    product_url = (data.get("url") or "").strip()
    if not product_url:
        return JSONResponse({"error": "url 필요"}, status_code=400)
    result = await scrape_and_analyze_url(product_url)
    return JSONResponse(result)


@app.post("/api/pagemaker/ai-stream")
async def pm_ai_stream(request: Request):
    data = await request.json()
    image_url    = (data.get("image_url") or "").strip()
    product_data = data.get("product_data") or {}

    if not image_url:
        return JSONResponse({"error": "image_url 필요"}, status_code=400)

    async def generate():
        async for event in stream_ai_sections(image_url, product_data):
            yield sse(event)
        yield sse({"done": True})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ===== 이카운트 자금일보 대시보드 =====
JAGEUM_FILE = Path("jageum_data.json")
JAGEUM_USER = os.getenv("JAGEUM_USER", "buja")
JAGEUM_PASS = os.getenv("JAGEUM_PASS", "1234")
JAGEUM_INGEST_SECRET = os.getenv("JAGEUM_INGEST_SECRET", "bj-ecount-2026-ingest")

def _jageum_auth(request: Request) -> bool:
    h = request.headers.get("authorization", "")
    if h.startswith("Basic "):
        try:
            u, p = base64.b64decode(h[6:]).decode().split(":", 1)
            return u == JAGEUM_USER and p == JAGEUM_PASS
        except Exception:
            return False
    return False

_AUTH401 = Response("로그인이 필요합니다", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="jageum"'})

@app.get("/jageum")
def jageum_page(request: Request):
    if not _jageum_auth(request):
        return _AUTH401
    return FileResponse("static/jageum.html", headers={"Cache-Control": "no-store"})

JAGEUM_MANUAL_FILE = Path("jageum_manual.json")

@app.get("/jageum/api/data")
def jageum_data(request: Request):
    if not _jageum_auth(request):
        return _AUTH401
    d = json.loads(JAGEUM_FILE.read_text(encoding="utf-8")) if JAGEUM_FILE.exists() else {"period": "", "자금현황": [], "자금의증가": [], "자금의감소": []}
    if JAGEUM_MANUAL_FILE.exists():
        try:
            d["수동입력"] = json.loads(JAGEUM_MANUAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            d["수동입력"] = {}
    return JSONResponse(d)

@app.post("/jageum/api/ingest")
async def jageum_ingest(request: Request):
    if request.headers.get("x-ingest-secret", "") != JAGEUM_INGEST_SECRET:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    body = await request.body()
    JAGEUM_FILE.write_text(body.decode("utf-8"), encoding="utf-8")
    return JSONResponse({"ok": True})

@app.post("/jageum/api/manual")
async def jageum_manual(request: Request):
    if not _jageum_auth(request):
        return _AUTH401
    body = await request.body()
    JAGEUM_MANUAL_FILE.write_text(body.decode("utf-8"), encoding="utf-8")
    return JSONResponse({"ok": True})


# ===== CN메이커 (CN인사이더 → 부자주방 상세페이지) — Lightsail 중개 =====
CNMAKER_BASE = os.getenv("CNMAKER_BASE", "http://43.200.232.189")
CNMAKER_SECRET = os.getenv("CNMAKER_SECRET", "bj-cnmaker-2026")

@app.get("/cnmaker")
def cnmaker_page():
    return FileResponse("static/cnmaker.html", headers={"Cache-Control": "no-store"})

@app.post("/cnmaker/api/start")
async def cnmaker_start(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip()
    if "cninsider" not in url:
        return JSONResponse({"error": "CN인사이더 상품 URL을 넣어주세요"}, status_code=400)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{CNMAKER_BASE}/cnmaker/start",
                              json={"url": url}, headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

@app.get("/cnmaker/api/status")
async def cnmaker_status(job: str):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{CNMAKER_BASE}/cnmaker/status",
                             params={"job": job}, headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

@app.get("/cnmaker/api/result")
async def cnmaker_result(job: str):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{CNMAKER_BASE}/cnmaker/result", params={"job": job})
        return Response(content=r.content, media_type="image/jpeg")


app.mount("/static", StaticFiles(directory="static"), name="static")
