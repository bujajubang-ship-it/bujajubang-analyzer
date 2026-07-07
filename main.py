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
async def root(request: Request):
    # 소싱 사이트 전체 잠금 — 사장·경리·소싱직원 로그인 허용
    if not _site_auth(request):
        return FileResponse("static/site_login.html", headers={"Cache-Control": "no-store"})
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})

@app.get("/api/me")
async def api_me(request: Request):
    return JSONResponse({"role": _jageum_role(request)})

@app.get("/danga")
def danga_view_page(request: Request):
    # 단가/마진 열람 — 로그인한 전 직원(영업팀 포함) 접근 가능
    if not _jageum_role(request):
        return FileResponse("static/site_login.html", headers={"Cache-Control": "no-store"})
    return FileResponse("static/danga_view.html", headers={"Cache-Control": "no-store"})


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


# ── 영구 저장소 경로 (Render Persistent Disk /var/data) ──────────────
# DATA_DIR 환경변수 있으면 그 경로(영구 디스크), 없으면 로컬(fallback)
DATA_DIR = Path(os.getenv("DATA_DIR", "."))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path(".")
def data_path(name):
    return DATA_DIR / name

# ── Memo API (서버 저장 — 브라우저/기기 무관하게 유지) ──────────────
MEMOS_FILE = data_path("memos.json")

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
TRACKER_FILE = data_path("tracker.json")

def _load_tracker() -> dict:
    try:
        return json.loads(TRACKER_FILE.read_text(encoding="utf-8")) if TRACKER_FILE.exists() else {}
    except Exception:
        return {}

def _save_tracker(data: dict):
    TRACKER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 거래처 단가 / 마진 (직원 공유) ───────────────────────────────────
DANGA_FILE = data_path("danga.json")

def _load_danga() -> list:
    try:
        return json.loads(DANGA_FILE.read_text(encoding="utf-8")) if DANGA_FILE.exists() else []
    except Exception:
        return []

def _save_danga(rows: list):
    DANGA_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

@app.get("/api/danga")
async def get_danga():
    return _load_danga()

@app.post("/api/danga")
async def save_danga(request: Request):
    rows = await request.json()
    if not isinstance(rows, list):
        return {"ok": False, "error": "list required"}
    _save_danga(rows)
    return {"ok": True, "count": len(rows)}

# ── 쿠팡 실판매가 (Lightsail이 Wing API로 수집→여기로 전송, 브라우저는 조회) ──
COUPANG_PRODUCTS_FILE = data_path("coupang_products.json")
COUPANG_INGEST_SECRET = os.getenv("COUPANG_INGEST_SECRET", "bj-coupang-ingest-2026")

@app.get("/api/coupang_products")
async def get_coupang_products():
    try:
        return json.loads(COUPANG_PRODUCTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "updated": ""}

@app.post("/api/coupang_products/ingest")
async def ingest_coupang_products(request: Request):
    if request.headers.get("x-secret") != COUPANG_INGEST_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    data = await request.json()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return JSONResponse({"error": "bad payload"}, status_code=400)
    COUPANG_PRODUCTS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(data.get("items", []))}


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
JAGEUM_FILE = data_path("jageum_data.json")
JAGEUM_USER = os.getenv("JAGEUM_USER", "buja")
JAGEUM_PASS = os.getenv("JAGEUM_PASS", "1234")
JAGEUM_INGEST_SECRET = os.getenv("JAGEUM_INGEST_SECRET", "bj-ecount-2026-ingest")
# 사장님(대표) 계정 — 결재함 권한
JAGEUM_BOSS_USER = os.getenv("JAGEUM_BOSS_USER", "1111")
JAGEUM_BOSS_PASS = os.getenv("JAGEUM_BOSS_PASS", "3131")
# 소싱직원 계정 — 소싱 사이트(/, /cnmaker)만 접근, 자금 대시보드는 못 봄
# 소싱 사이트 직원 계정들 (자금 대시보드는 못 봄) — {아이디: (비번, 역할)}
SITE_STAFF_ACCOUNTS = {
    os.getenv("DESIGN_USER", "buja2"): (os.getenv("DESIGN_PASS", "3030"), "design"),    # 디자이너
    os.getenv("SOURCING_USER", "sosing"): (os.getenv("SOURCING_PASS", "3030"), "sourcing"),  # 소싱직원
    os.getenv("CS_USER", "cs"): (os.getenv("CS_PASS", "1234"), "cs"),  # CS직원
}
# 영업사원 계정 (자금 대시보드의 '영업 결산' 탭만 접근) — {아이디: (비번, 담당자이름)}
SALES_ACCOUNTS = {
    os.getenv("SALES1_USER", "김성주"): (os.getenv("SALES1_PASS", "1940"), "김성주"),
    os.getenv("SALES2_USER", "성기민"): (os.getenv("SALES2_PASS", "7746"), "성기민"),
}
# 역할 → 표시 이름 (채팅·결재에 누가 올렸는지 구분)
ROLE_NAMES = {"boss": "사장님", "staff": "경리", "design": "디자이너", "sourcing": "소싱직원", "cs": "CS직원", "sales": "영업사원"}

import hashlib, hmac, time
_JAGEUM_SECRET = os.getenv("JAGEUM_TOKEN_SECRET", "bj-jageum-token-2026")

def _make_token(role: str, who: str = "") -> str:
    """role|who(b64)|expiry|sig 세션 토큰 (24h). who는 한글 가능 → base64로 ASCII화(쿠키 제약)"""
    exp = str(int(time.time()) + 86400)
    who_b64 = base64.urlsafe_b64encode((who or "").encode()).decode()
    msg = f"{role}|{who_b64}|{exp}"
    sig = hmac.new(_JAGEUM_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{msg}|{sig}"

def _check_token(tok: str):
    """반환: (role, who). 실패 시 ('', '')"""
    try:
        role, who_b64, exp, sig = tok.split("|")
        msg = f"{role}|{who_b64}|{exp}"
        good = hmac.new(_JAGEUM_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(sig, good) and int(exp) > time.time():
            who = base64.urlsafe_b64decode(who_b64.encode()).decode()
            return role, who
    except Exception:
        pass
    return "", ""

def _jageum_role(request: Request) -> str:
    tok = request.cookies.get("jg_session", "")
    return _check_token(tok)[0] if tok else ""

def _jageum_who(request: Request) -> str:
    """로그인한 사람의 이름(담당자) — 영업 딜 배정용"""
    tok = request.cookies.get("jg_session", "")
    return _check_token(tok)[1] if tok else ""

def _jageum_auth(request: Request) -> bool:
    # 회사 자금(매출·지출·개인자산 등): 사장·경리만
    return _jageum_role(request) in ("boss", "staff")

def _jageum_page_auth(request: Request) -> bool:
    # 자금 대시보드 페이지 로드: 사장·경리·영업사원 (영업은 영업결산 탭만 봄)
    return _jageum_role(request) in ("boss", "staff", "sales")

def _sales_auth(request: Request) -> bool:
    # 영업 결산 데이터: 사장·경리·영업사원
    return _jageum_role(request) in ("boss", "staff", "sales")

def _site_auth(request: Request) -> bool:
    # 소싱 사이트(/·/cnmaker): 로그인한 모두 (사장·경리·디자이너·소싱직원·CS)
    return _jageum_role(request) in ("boss", "staff", "design", "sourcing", "cs")

_AUTH401 = JSONResponse({"error": "로그인 필요"}, status_code=401)

@app.get("/jageum")
def jageum_page(request: Request):
    if not _jageum_page_auth(request):
        return FileResponse("static/jageum_login.html", headers={"Cache-Control": "no-store"})
    return FileResponse("static/jageum.html", headers={"Cache-Control": "no-store"})

@app.get("/jageum/login")
def jageum_login_page():
    return FileResponse("static/jageum_login.html", headers={"Cache-Control": "no-store"})

@app.post("/jageum/api/login")
async def jageum_login(request: Request):
    data = await request.json()
    u = (data.get("id") or "").strip()
    p = (data.get("pw") or "")
    role = ""; who = ""
    if u == JAGEUM_BOSS_USER and p == JAGEUM_BOSS_PASS:
        role = "boss"; who = "사장님"
    elif u == JAGEUM_USER and p == JAGEUM_PASS:
        role = "staff"; who = "경리"
    elif u in SITE_STAFF_ACCOUNTS and p == SITE_STAFF_ACCOUNTS[u][0]:
        role = SITE_STAFF_ACCOUNTS[u][1]; who = ROLE_NAMES.get(role, role)
    elif u in SALES_ACCOUNTS and p == SALES_ACCOUNTS[u][0]:
        role = "sales"; who = SALES_ACCOUNTS[u][1]  # 담당자 이름
    if not role:
        return JSONResponse({"error": "아이디 또는 비밀번호가 맞지 않습니다"}, status_code=401)
    resp = JSONResponse({"ok": True, "role": role, "who": who})
    resp.set_cookie("jg_session", _make_token(role, who), max_age=86400,
                    httponly=True, samesite="lax")
    return resp

@app.post("/jageum/api/logout")
async def jageum_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("jg_session")
    return resp

JAGEUM_MANUAL_FILE = data_path("jageum_manual.json")

# ===== 영구 백업 (Lightsail KV) — Render 무료플랜은 재배포/재시작 시 런타임 파일이 초기화되므로,
#       결재·수동입력을 항상 켜진 Lightsail 서버에 자동 백업하고, 로컬이 비면 복원한다. =====
_KV_BASE = os.getenv("CNMAKER_BASE", "http://43.200.232.189")
_KV_SECRET = os.getenv("CNMAKER_SECRET", "bj-cnmaker-2026")
def _kv_backup(key, obj):
    try:
        httpx.post(f"{_KV_BASE}/kv/{key}", json=obj, headers={"x-secret": _KV_SECRET}, timeout=6)
    except Exception:
        pass
def _kv_restore(key):
    try:
        r = httpx.get(f"{_KV_BASE}/kv/{key}", headers={"x-secret": _KV_SECRET}, timeout=6)
        if r.status_code == 200:
            return r.json().get("data")
    except Exception:
        pass
    return None
def _load_manual():
    if JAGEUM_MANUAL_FILE.exists():
        try:
            return json.loads(JAGEUM_MANUAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    data = _kv_restore("jageum_manual")   # 로컬 없음(재배포 초기화) → Lightsail 백업 복원
    if isinstance(data, dict):
        try:
            JAGEUM_MANUAL_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return data
    return {}

@app.get("/jageum/api/data")
def jageum_data(request: Request):
    role = _jageum_role(request)
    if role not in ("boss", "staff", "sales"):
        return _AUTH401
    # 영업사원은 회사 자금 데이터 접근 불가 — 역할·이름만 (프론트가 영업결산 탭만 노출)
    if role == "sales":
        return JSONResponse({"_role": "sales", "_who": _jageum_who(request)})
    d = json.loads(JAGEUM_FILE.read_text(encoding="utf-8")) if JAGEUM_FILE.exists() else {"period": "", "자금현황": [], "자금의증가": [], "자금의감소": []}
    d["수동입력"] = _load_manual()
    d["_role"] = role
    d["_who"] = _jageum_who(request)
    return JSONResponse(d)

# ===== 영업 결산 (딜 관리) =====
SALES_DEALS_FILE = data_path("sales_deals.json")

def _load_deals():
    if SALES_DEALS_FILE.exists():
        try:
            return json.loads(SALES_DEALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_deals(items):
    SALES_DEALS_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

@app.get("/jageum/api/sales")
def sales_list(request: Request):
    if not _sales_auth(request):
        return _AUTH401
    role = _jageum_role(request); who = _jageum_who(request)
    deals = _load_deals()
    if role == "sales":  # 영업사원: 자기 거래처만
        deals = [x for x in deals if x.get("담당자") == who]
    return JSONResponse({"deals": deals, "_role": role, "_who": who})

@app.post("/jageum/api/sales")
async def sales_save(request: Request):
    if not _sales_auth(request):
        return _AUTH401
    body = await request.json()
    who = _jageum_who(request)
    role = _jageum_role(request)
    items = _load_deals()
    d = body.get("deal") or {}
    if role == "sales":
        d["담당자"] = who  # 영업사원은 담당자 항상 본인 강제(신규·수정 모두)
    did = d.get("id")
    if did:  # 수정
        found = False
        for i, x in enumerate(items):
            if x.get("id") == did:
                if role == "sales" and x.get("담당자") != who:  # 남의 거래처 수정 차단
                    return JSONResponse({"error": "본인 거래처만 수정할 수 있어요"}, status_code=403)
                items[i] = {**x, **d}; found = True
                break
        if not found:
            return JSONResponse({"error": "거래처를 찾을 수 없어요"}, status_code=404)
    else:  # 신규
        d["id"] = (max([x.get("id", 0) for x in items], default=0) + 1)
        items.insert(0, d)
    _save_deals(items)
    return JSONResponse({"ok": True, "deal": d})

@app.post("/jageum/api/sales/delete")
async def sales_delete(request: Request):
    if not _sales_auth(request):
        return _AUTH401
    body = await request.json()
    did = body.get("id")
    who = _jageum_who(request); role = _jageum_role(request)
    items = _load_deals()
    if role == "sales":  # 본인 거래처만 삭제
        target = next((x for x in items if x.get("id") == did), None)
        if target and target.get("담당자") != who:
            return JSONResponse({"error": "본인 거래처만 삭제할 수 있어요"}, status_code=403)
    items = [x for x in items if x.get("id") != did]
    _save_deals(items)
    return JSONResponse({"ok": True})

@app.get("/jageum/api/sale_vouchers")
def sale_vouchers(request: Request):
    # 이카운트 판매조회 전표(매출·원가·이익) — 마감 시 클릭으로 가져옴
    if not _sales_auth(request):
        return _AUTH401
    role = _jageum_role(request); who = _jageum_who(request)
    d = json.loads(JAGEUM_FILE.read_text(encoding="utf-8")) if JAGEUM_FILE.exists() else {}
    sales = d.get("영업손익", {}) or {}
    out = []
    for month, sd in sales.items():
        for v in (sd.get("전표") or []):
            out.append({"일자No": v.get("일자No", ""), "거래처": v.get("거래처", ""),
                        "담당자": v.get("담당자", ""), "매출": v.get("매출", 0),
                        "원가": v.get("원가", 0), "이익": v.get("이익", 0),
                        "원가확인필요": v.get("원가확인필요", False), "품목": v.get("품목요약", "")})
    if role == "sales":  # 영업사원은 본인 전표만
        out = [v for v in out if v.get("담당자") == who]
    out.sort(key=lambda v: v.get("일자No", ""), reverse=True)
    return JSONResponse({"vouchers": out})

@app.get("/jageum/api/deposits")
def sales_deposits(request: Request):
    # 실제 입금 내역(자금일보 자금의증가) — 영업이 딜에 계약금/중도금으로 연결
    if not _sales_auth(request):
        return _AUTH401
    d = json.loads(JAGEUM_FILE.read_text(encoding="utf-8")) if JAGEUM_FILE.exists() else {}
    inc = d.get("자금의증가", [])
    # 이미 딜에 연결된 입금 key 집합
    linked = {}
    for deal in _load_deals():
        for L in (deal.get("입금연결") or []):
            linked[L.get("key")] = {"deal": deal.get("거래처", ""), "종류": L.get("종류", "")}
    out = []
    for r in inc:
        amt = r.get("금액") or 0
        if not amt or amt <= 0:
            continue
        key = f"{r.get('일자','')}|{r.get('거래처명','')}|{(r.get('적요') or '')}|{amt}"
        out.append({"key": key, "date": r.get("일자", ""), "거래처": r.get("거래처명", ""),
                    "적요": r.get("적요", "") or "", "금액": amt, "프로젝트": r.get("프로젝트명", "") or "",
                    "linked": linked.get(key)})
    return JSONResponse({"deposits": out})

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
    try:
        _kv_backup("jageum_manual", json.loads(body.decode("utf-8")))
    except Exception:
        pass
    return JSONResponse({"ok": True})


# ===== 사장님 개인 자산 (boss 전용) =====
JAGEUM_PERSONAL_FILE = data_path("jageum_personal.json")
_NV_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None

def _boss_only(request: Request) -> bool:
    return _jageum_role(request) == "boss"

@app.get("/jageum/api/personal")
def jageum_personal_get(request: Request):
    if not _boss_only(request):
        return _AUTH401
    d = {}
    if JAGEUM_PERSONAL_FILE.exists():
        try:
            d = json.loads(JAGEUM_PERSONAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    return JSONResponse(d)

@app.post("/jageum/api/personal")
async def jageum_personal_post(request: Request):
    if not _boss_only(request):
        return _AUTH401
    body = await request.body()
    JAGEUM_PERSONAL_FILE.write_text(body.decode("utf-8"), encoding="utf-8")
    return JSONResponse({"ok": True})

async def _nv_quote(client, market, code):
    """현재가 + 전일대비 등락 반환: {price, change, rate} (rate=%, 부호 포함)."""
    try:
        if market == "KR":
            r = await client.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}")
            ds = r.json().get("datas") or []
            if not ds:
                return None
            x = ds[0]
            up = (x.get("compareToPreviousPrice") or {}).get("code") in ("2", "1")  # 상승/상한
            chg = _num(x.get("compareToPreviousClosePriceRaw"))
            rate = _num2(x.get("fluctuationsRatioRaw"))
            return {"price": _num(x.get("closePriceRaw") or x.get("closePrice")),
                    "change": chg if up else -abs(chg), "rate": rate if up else -abs(rate)}
        else:
            r = await client.get(f"https://api.stock.naver.com/stock/{code}/basic")
            j = r.json()
            up = (j.get("compareToPreviousPrice") or {}).get("code") in ("2", "1")
            chg = _num2(j.get("compareToPreviousClosePrice"))
            rate = _num2(j.get("fluctuationsRatio"))
            return {"price": _num2(j.get("closePrice")),
                    "change": chg if up else -abs(chg), "rate": rate if up else -abs(rate)}
    except Exception:
        return None

def _num2(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return 0

@app.get("/jageum/api/personal/prices")
async def jageum_personal_prices(request: Request):
    if not _boss_only(request):
        return _AUTH401
    d = {}
    if JAGEUM_PERSONAL_FILE.exists():
        try:
            d = json.loads(JAGEUM_PERSONAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    stocks = d.get("stocks") or []
    prices, quotes, fx = {}, {}, None
    async with httpx.AsyncClient(headers=_NV_H, timeout=15) as c:
        try:
            r = await c.get("https://m.stock.naver.com/api/json/marketindex/marketIndexExchangeListJson.nhn")
            for it in r.json()["result"]["normalList"]:
                if it.get("reutersCode") == "FX_USDKRW" or it.get("exchangeCode") == "FX_USDKRW":
                    fx = _num(it.get("closePrice")); break
        except Exception:
            pass
        for s in stocks:
            code = s.get("code")
            if code and code not in prices:
                q = await _nv_quote(c, s.get("market", "KR"), code)
                prices[code] = (q or {}).get("price") if q else None
                if q:
                    quotes[code] = q
    return JSONResponse({"fx_usdkrw": fx, "prices": prices, "quotes": quotes})

@app.get("/jageum/api/personal/search")
async def jageum_personal_search(request: Request, q: str = ""):
    if not _boss_only(request):
        return _AUTH401
    q = (q or "").strip()
    if not q:
        return JSONResponse({"items": []})
    items = []
    try:
        async with httpx.AsyncClient(headers=_NV_H, timeout=12) as c:
            r = await c.get("https://m.stock.naver.com/front-api/search/autoComplete",
                            params={"query": q, "target": "stock"})
            for it in (r.json().get("result") or {}).get("items", [])[:8]:
                tc = it.get("typeCode", "")
                kr = tc in ("KOSPI", "KOSDAQ")
                items.append({"name": it.get("name"),
                              "code": it.get("code") if kr else it.get("reutersCode"),
                              "market": "KR" if kr else "US",
                              "typeName": it.get("typeName") or tc})
    except Exception:
        pass
    return JSONResponse({"items": items})

JAGEUM_LIFE_FILE = data_path("jageum_life.json")

@app.get("/jageum/api/life")
def jageum_life_get(request: Request):
    if not _boss_only(request):
        return _AUTH401
    d = {}
    if JAGEUM_LIFE_FILE.exists():
        try:
            d = json.loads(JAGEUM_LIFE_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    return JSONResponse(d)

@app.post("/jageum/api/life")
async def jageum_life_post(request: Request):
    if not _boss_only(request):
        return _AUTH401
    body = await request.body()
    JAGEUM_LIFE_FILE.write_text(body.decode("utf-8"), encoding="utf-8")
    return JSONResponse({"ok": True})

@app.post("/jageum/api/life/compare")
async def jageum_life_compare(request: Request):
    if not _boss_only(request):
        return _AUTH401
    data = await request.json()
    text = (data.get("text") or "").strip()
    title = (data.get("title") or "").strip()
    if not text and not title:
        return JSONResponse({"error": "내용을 적어주세요."})
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "AI 키 미설정"}, status_code=500)
    life = {}
    if JAGEUM_LIFE_FILE.exists():
        try:
            life = json.loads(JAGEUM_LIFE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    past = life.get("과거", []) or []
    summ = life.get("과거요약", {}) or {}
    titles = "\n".join(f"{p.get('date','')} | {p.get('title','')}" for p in past[:320])
    prompt = f"""너는 이 사람의 '인생 방향 코치'야. 이 사람이 2025년에 쓴 블로그 200편을 분석한 '생각의 지도'와 전체 글 제목 목록이 아래에 있어.

[생각의 지도]
{json.dumps(summ, ensure_ascii=False)}

[과거 글 제목 목록 (날짜 | 제목)]
{titles}

[지금 새로 쓴 글]
제목: {title}
내용: {text}

이 새 글이 과거의 고민·방향·가치관과 얼마나 맞아떨어지는지 분석해줘. 아래 JSON만 출력(코드블록 없이 순수 JSON):
{{"정합성":"일관" 또는 "발전" 또는 "변화" 또는 "새로움",
 "한줄":"한 문장 평가",
 "연결주제":["생각의지도 핵심주제 중 연결되는 것 1-3개"],
 "관련과거글":[{{"date":"YYYY-MM-DD","title":"실제 목록의 제목","연결":"어떻게 통하는지 또는 달라졌는지 한 문장"}}],
 "코멘트":"과거 맥락에서 본 통찰·조언 2-3문장. 응원하되 솔직하게."}}
관련과거글은 위 목록에 실제로 있는 것만, 가장 관련 깊은 2-4개."""
    body = {"model": "claude-opus-4-8", "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
            r.raise_for_status()
            d = r.json()
            txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        txt = re.sub(r"```[a-z]*", "", txt).strip("`\n ")
        s, e = txt.find("{"), txt.rfind("}")
        return JSONResponse(json.loads(txt[s:e+1]))
    except Exception as ex:
        return JSONResponse({"error": f"비교 실패: {ex}"}, status_code=500)

@app.post("/jageum/api/life/chat")
async def jageum_life_chat(request: Request):
    if not _boss_only(request):
        return _AUTH401
    data = await request.json()
    messages = data.get("messages", [])
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "AI 키 미설정"}, status_code=500)
    life = {}
    if JAGEUM_LIFE_FILE.exists():
        try:
            life = json.loads(JAGEUM_LIFE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    summ = life.get("과거요약", {}) or {}
    past = life.get("과거", []) or []
    notes = life.get("글", []) or []
    # 최근 인생노트 글 본문(맥락), 과거 블로그는 제목+요약으로 압축
    note_ctx = "\n".join(f"- [{n.get('date','')}] {n.get('title','')}: {(n.get('body','') or '')[:300]}" for n in notes[:25])
    past_titles = "\n".join(f"{p.get('date','')} | {p.get('title','')}" for p in past[:200])
    system = f"""너는 이 사람의 가장 가까운 친구이자 인생 고민 상담가야. 이 사람을 누구보다 깊이 안다.
이 사람은 '부자주방'(업소용 주방기기 셀러) 유튜버·사업가이고, 자영업 시장을 바꾸는 기술기업을 꿈꾼다.

[이 사람의 생각의 지도 — 2025년 블로그 200편 AI 분석]
{json.dumps(summ, ensure_ascii=False)}

[과거에 쓴 글 제목들 (날짜 | 제목) — 필요하면 구체적으로 인용]
{past_titles}

[최근 인생노트에 직접 적은 생각들]
{note_ctx or "(아직 없음)"}

[대화 원칙]
- 똑똑하고 솔직한 친구처럼. 가볍게 공감만 하지 말고, 이 사람의 과거 고민·가치관(원띵·선택과집중·대체불가능성·자기단련·큰비전)과 연결해서 깊이 있게 대화해.
- 과거에 비슷한 고민을 했으면 "예전에 ○○ 글에서 이런 얘기 했었잖아" 식으로 자연스럽게 짚어줘.
- 결론을 강요하지 말고, 좋은 질문으로 스스로 답을 찾게 도와. 필요할 땐 직설적인 조언도.
- 따뜻하지만 통찰 있게. 존댓말 대신 친근한 반말~해요체 섞어서 친구처럼.
- 사업 자금 얘기가 아니라 인생·방향·내면 고민에 집중해."""
    body = {"model": "claude-opus-4-8", "max_tokens": 2000, "system": system,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages[-20:]]}
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
            if r.status_code != 200:
                return JSONResponse({"error": f"AI 오류: {r.text[:200]}"}, status_code=500)
            rd = r.json()
        text = "".join(b.get("text", "") for b in rd.get("content", []) if b.get("type") == "text")
        return JSONResponse({"reply": text})
    except Exception as ex:
        return JSONResponse({"error": f"오류: {ex}"}, status_code=500)

@app.get("/jageum/api/personal/chart")
async def jageum_personal_chart(request: Request, market: str = "KR", code: str = "", days: int = 120):
    if not _boss_only(request):
        return _AUTH401
    import datetime as _dt
    days = max(7, min(int(days or 120), 800))
    end = _dt.date.today()
    start = end - _dt.timedelta(days=days)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    seg = "domestic" if market == "KR" else "foreign"
    url = f"https://api.stock.naver.com/chart/{seg}/item/{code}/day?startDateTime={s}&endDateTime={e}"
    pts = []
    try:
        async with httpx.AsyncClient(headers=_NV_H, timeout=15) as c:
            r = await c.get(url)
            for row in r.json():
                d = str(row.get("localDate") or "")
                cp = _num(row.get("closePrice"))
                if len(d) == 8 and cp is not None:
                    pts.append({"d": f"{d[2:4]}.{d[4:6]}.{d[6:8]}", "c": cp,
                                "o": _num(row.get("openPrice")), "h": _num(row.get("highPrice")),
                                "l": _num(row.get("lowPrice"))})
    except Exception:
        pass
    return JSONResponse({"points": pts})


# ===== 자금 대시보드 AI 채팅 + 결재함 =====
JAGEUM_APPROVALS_FILE = data_path("jageum_approvals.json")

def _load_approvals():
    if JAGEUM_APPROVALS_FILE.exists():
        try:
            return json.loads(JAGEUM_APPROVALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    data = _kv_restore("jageum_approvals")   # 로컬 없음(재배포 초기화) → Lightsail 백업 복원
    if isinstance(data, list):
        try:
            JAGEUM_APPROVALS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return data
    return []

def _save_approvals(items):
    JAGEUM_APPROVALS_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    _kv_backup("jageum_approvals", items)

def _jageum_summary() -> str:
    """대시보드 데이터를 AI가 보기 좋게 요약(토큰 절약)."""
    if not JAGEUM_FILE.exists():
        return "(자금 데이터 없음)"
    try:
        d = json.loads(JAGEUM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return "(데이터 로드 실패)"
    months = d.get("months", {})
    lines = ["[자금 대시보드 데이터 요약]"]
    def real(r): return (r.get("상대계정명") or "").strip() != ""
    # 월별 영업입금/지출 + 팀별
    def dept(r):
        t = (r.get("거래처명","")+r.get("적요","")+r.get("상대계정명",""))
        import re as _re
        if _re.search("종보전기|한국전력|전력매출", t): return "태양광"
        pj = r.get("프로젝트명","")
        if "쇼핑몰" in pj: return "쇼핑몰"
        if "성기민" in pj: return "영업·성기민"
        if "김성주" in pj: return "영업·김성주"
        if "대표" in pj: return "영업·대표"
        return "본사·공통"
    for k in sorted(months.keys())[-6:]:
        m = months[k]
        inc = sum(r["금액"] for r in m.get("자금의증가",[]) if real(r))
        dec = sum(r["금액"] for r in m.get("자금의감소",[]) if real(r))
        teams = {}
        for r in m.get("자금의증가",[]):
            if real(r): teams.setdefault(dept(r),[0,0])[0]+=r["금액"]
        for r in m.get("자금의감소",[]):
            if real(r): teams.setdefault(dept(r),[0,0])[1]+=r["금액"]
        ts = " / ".join(f"{t}:순익{(v[0]-v[1])//10000}만" for t,v in teams.items())
        lines.append(f"{k}: 영업입금 {inc//10000}만, 지출 {dec//10000}만 | 팀별 {ts}")
    # 손익
    pl = d.get("손익",{})
    if pl.get("rows"):
        major = [r for r in pl["rows"] if r.get("major")]
        lines.append("[손익계산서(올해)] " + " / ".join(f"{r['과목']}:{r['당기']//10000}만" for r in major))
    # 정산예정·미수금
    s = d.get("정산예정",{})
    if s:
        cou = (s.get("쿠팡",{}).get("정산예정",0)+s.get("쿠팡",{}).get("미구매확정",0))
        nav = s.get("네이버",{}).get("지급예정",0)
        lines.append(f"[정산예정] 쿠팡 {cou//10000}만, 네이버 {nav//10000}만")
    if d.get("미수금"):
        lines.append(f"[미수금] {d['미수금'].get('total',0)//10000}만")
    # 경리 수기입력 항목 + 최종수정일 (AI가 '언제 기준 얼마'를 안내할 수 있게)
    man = _load_manual()
    md = man.get("수정일", {}) or {}
    def _sum_rows(v):
        try:
            return sum((x.get("잔액") or 0) for x in v) if isinstance(v, list) else (v or 0)
        except Exception:
            return 0
    manual_lines = []
    for key, label in [("미수금","미수금(B2B 받을 돈)"), ("선급금","선급금(거래처 선지급)"),
                       ("카페24","카페24 묶인돈"), ("대출","대출 잔액")]:
        if key in man:
            amt = _sum_rows(man[key])
            when = md.get(key, "")
            manual_lines.append(f"  - {label}: {amt//10000}만" + (f" ({when} 경리 수정 기준)" if when else " (수정일 미기록)"))
        elif key in md:
            manual_lines.append(f"  - {label}: ({md[key]} 확인)")
    if manual_lines:
        lines.append("[경리 수기입력 현황 — 이카운트로 자동조회 안 되는 항목, 경리가 직접 입력. '언제 기준 얼마'를 물으면 아래 수정일로 답하세요]")
        lines.extend(manual_lines)
    return "\n".join(lines)

@app.post("/jageum/api/chat")
async def jageum_chat(request: Request):
    if not _jageum_auth(request):
        return _AUTH401
    data = await request.json()
    messages = data.get("messages", [])  # [{role, content}]
    # who는 클라이언트 값이 아니라 로그인 계정(역할)으로 강제 — 사칭 방지
    who = "사장님" if _jageum_role(request) == "boss" else "경리"
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY 미설정"}, status_code=500)
    is_boss = _jageum_role(request) == "boss"
    role_rule = ""
    if not is_boss:
        role_rule = """
[중요·보안] 지금 대화 상대는 '경리'입니다. 회사(법인) 자금·업무 관련만 답하세요.
- 사장님 개인 자산(주식·부동산·예금·대출·저축목표), 개인 자금 대시보드 기능, 인생 노트/비전보드 등 '개인' 영역은 **존재 여부조차 언급하지 마세요.** 물어보면 "그 부분은 제가 도와드릴 수 없어요. 회사 자금·경리 업무를 도와드릴게요"라고만 답하세요.
- 경리 업무(아래 가이드)와 회사 자금일보·법인카드·정산·미수금 등에 집중하세요.

[경리 업무 가이드 — 어제 사장님과 최종 정리한 내용]
경리가 매달 챙길 일은 '대시보드에서 직접 입력·확인'과 '이카운트 전표 작성 시 입력' 두 가지로 나뉩니다.
① 대시보드에서 입력·수정 (📋 경리 체크리스트 카드에서, 끝나면 '확인하기'로 ✅):
  - 미수금(B2B 받을 돈): 이카운트 자동값이 기본. 실제와 다르면 수정.
  - 선급금(거래처 선지급): 입출금에 안 잡히는 선금 잔액 직접 입력.
  - 카페24 묶인돈: 카페24는 정산 API가 없어 자동 불가 → 직접 입력 필수.
  - 대출(부채): 갚은 만큼 잔액 줄이기.
  - 선금·수량 체크 품목/거래처: 까다로운 거래처·품목 등록(영업손익 카드).
  - 법인카드 미분류 분류: 카드 사용내역 중 카테고리(상대계정) 미지정 건을 이카운트 카드매입조회에서 분류. (현재 미분류가 많아 핵심 업무)
② 이카운트 전표 작성 시 입력:
  - 출금 전표 프로젝트명 = 현장(거래처)명 → 매출과 비용을 현장 단위로 연결하는 키.
  - 입금 전표 프로젝트명 = '현장명-담당자' → 수금률·담당자 실적 귀속.
경리가 위 항목을 매달 빠짐없이 확인하면, AI 자동수집 + 경리 수기입력이 합쳐져 자산 집계가 완성됩니다."""

    system = f"""당신은 부자홀딩스(업소용 주방기기 셀러)의 자금 대시보드를 돕는 AI 비서입니다.
지금 '{who}'님과 대화 중입니다. 아래 실제 자금 데이터를 보고 한국어 존댓말로 친절하고 실무적으로 답하세요.

{_jageum_summary()}
{role_rule}

규칙:
- 숫자는 실제 데이터 기반으로 구체적으로 답하세요. (만원 단위)
- 대시보드 기능 수정/추가가 필요한 협의가 정리되면, 답변 맨 끝에 별도 줄로 다음 형식을 정확히 출력하세요:
[결재요청] 제목 | 무엇을 어떻게 바꿀지 한 문장 요약
- 단순 상담/질문이면 결재요청을 넣지 마세요. 실제로 코드/기능 변경이 필요할 때만.
- 모르는 건 모른다고 하세요."""
    body = {
        "model": "claude-opus-4-8", "max_tokens": 1500, "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages[-12:]],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        if r.status_code != 200:
            return JSONResponse({"error": f"AI 오류: {r.text[:200]}"}, status_code=500)
        rd = r.json()
    text = "".join(b.get("text","") for b in rd.get("content",[]) if b.get("type")=="text")
    # 결재요청 추출
    approval = None
    import re as _re
    mt = _re.search(r"\[결재요청\]\s*(.+?)\s*\|\s*(.+)", text)
    if mt:
        title, desc = mt.group(1).strip(), mt.group(2).strip()
        text = text[:mt.start()].rstrip()
        items = _load_approvals()
        aid = (max([a["id"] for a in items], default=0) + 1)
        items.append({"id": aid, "title": title, "desc": desc, "who": who,
                      "status": "대기", "ts": "", "thread": []})
        _save_approvals(items)
        approval = {"id": aid, "title": title, "desc": desc}
    return JSONResponse({"reply": text, "approval": approval})

@app.get("/jageum/api/approvals")
async def jageum_approvals(request: Request):
    # 결재함은 사장님(대표)만 조회 가능
    if _jageum_role(request) != "boss":
        return JSONResponse({"items": [], "forbidden": True})
    return JSONResponse({"items": _load_approvals()})

@app.post("/jageum/api/approvals/{aid}")
async def jageum_approval_act(aid: int, request: Request):
    if _jageum_role(request) != "boss":
        return JSONResponse({"error": "권한 없음 (대표 전용)"}, status_code=403)
    data = await request.json()
    action = data.get("action")  # approve / reject
    items = _load_approvals()
    for a in items:
        if a["id"] == aid:
            a["status"] = "승인" if action == "approve" else "반려"
    _save_approvals(items)
    return JSONResponse({"ok": True})

@app.post("/jageum/api/approvals/{aid}/chat")
async def jageum_approval_chat(aid: int, request: Request):
    """결재 안건에 대해 사장님이 AI와 구체적으로 대화(무슨 기능을 어떻게 바꾸는지)."""
    if _jageum_role(request) != "boss":
        return JSONResponse({"error": "권한 없음 (대표 전용)"}, status_code=403)
    data = await request.json()
    msg = (data.get("message") or "").strip()
    items = _load_approvals()
    appr = next((a for a in items if a["id"] == aid), None)
    if not appr:
        return JSONResponse({"error": "없는 안건"}, status_code=404)
    thread = appr.setdefault("thread", [])
    if not msg:
        return JSONResponse({"thread": thread})
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY 미설정"}, status_code=500)
    system = f"""당신은 부자홀딩스 자금 대시보드의 '코드/기능 변경 안건'을 사장님께 설명하는 AI입니다.
아래 결재 안건에 대해, 사장님이 '무슨 기능을 / 어떻게 바꾸는지'를 구체적으로 이해하도록 쉬운 한국어로 설명하세요.
- 기술용어는 최소화하고, 실무 관점(무엇이 어떻게 달라지는지)으로 설명.
- 바꾸면 좋은 점과 주의할 점(부작용·비용)도 함께 짚으세요.
- 사장님이 승인/반려를 판단하도록 돕되, 결정은 사장님 몫입니다. 필요하면 되물어 확인하세요.

[결재 안건]
제목: {appr.get('title','')}
요청 내용: {appr.get('desc','')}
요청자: {appr.get('who','')}"""
    conv = [{"role": m.get("role"), "content": m.get("text", "")} for m in thread if m.get("role") in ("user", "assistant")]
    conv.append({"role": "user", "content": msg})
    body = {"model": "claude-opus-4-8", "max_tokens": 1200, "system": system, "messages": conv[-16:]}
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        if r.status_code != 200:
            return JSONResponse({"error": f"AI 오류: {r.text[:200]}"}, status_code=500)
        rd = r.json()
    reply = "".join(b.get("text", "") for b in rd.get("content", []) if b.get("type") == "text")
    thread.append({"role": "user", "text": msg})
    thread.append({"role": "assistant", "text": reply})
    _save_approvals(items)
    return JSONResponse({"reply": reply, "thread": thread})


# ===== 소싱 사이트 AI 개선 상담 + 결재함 =====
SITE_APPROVALS_FILE = data_path("site_approvals.json")

def _load_site_approvals():
    if SITE_APPROVALS_FILE.exists():
        try:
            return json.loads(SITE_APPROVALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_site_approvals(items):
    SITE_APPROVALS_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

@app.post("/api/site_chat")
async def site_chat(request: Request):
    if not _site_auth(request):
        return _AUTH401
    data = await request.json()
    messages = data.get("messages", [])
    role = _jageum_role(request)
    is_boss = role == "boss"
    who = ROLE_NAMES.get(role, "직원")  # 사장님/경리/디자이너/소싱직원 — 결재에 누가 올렸는지 구분
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY 미설정"}, status_code=500)
    role_rule = ("당신은 지금 '사장님(대표)'과 대화 중입니다. 대표는 결재 권한이 있습니다."
                 if is_boss else
                 "당신은 지금 '직원'과 대화 중입니다. 직원은 코드/기능을 직접 바꿀 수 없고, 변경은 대표 결재가 필요합니다. 변경안이 정리되면 결재요청으로 대표에게 올리세요.")
    system = f"""당신은 부자홀딩스(업소용 주방기기 셀러)의 소싱·쿠팡 사업 웹사이트를 함께 개선하는 AI 파트너입니다.
지금 '{who}'님과 대화 중입니다. 한국어 존댓말로, 사이트·소싱·쿠팡 사업의 개선 아이디어를 실무적으로 상담하세요.

이 사이트 구성: 📋쿠팡 품목 진행상황(소싱/로켓 파이프라인) · 📦소싱 추천 · 🔍키워드 분석 · 🎨페이지 메이커 · 🏭CN메이커(상세페이지 자동생성).

{role_rule}

규칙:
- 사이트 기능·UX·소싱 전략·쿠팡 운영 개선을 구체적으로 제안하세요.
- 실제로 코드/기능 변경(개발)이 필요한 안이 정리되면, 답변 맨 끝에 별도 줄로 정확히 이 형식을 출력하세요:
[결재요청] 제목 | 무엇을 어떻게 바꿀지 한 문장 요약
- 단순 상담·질문·설명이면 결재요청을 넣지 마세요. 실제 개발 작업이 필요할 때만.
- 모르는 건 모른다고 하세요."""
    body = {
        "model": "claude-opus-4-8", "max_tokens": 1500, "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages[-12:]],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        if r.status_code != 200:
            return JSONResponse({"error": f"AI 오류: {r.text[:200]}"}, status_code=500)
        rd = r.json()
    text = "".join(b.get("text","") for b in rd.get("content",[]) if b.get("type")=="text")
    approval = None
    import re as _re
    mt = _re.search(r"\[결재요청\]\s*(.+?)\s*\|\s*(.+)", text)
    if mt:
        title, desc = mt.group(1).strip(), mt.group(2).strip()
        text = text[:mt.start()].rstrip()
        items = _load_site_approvals()
        aid = (max([a["id"] for a in items], default=0) + 1)
        items.append({"id": aid, "title": title, "desc": desc, "who": who,
                      "status": "대기", "ts": ""})
        _save_site_approvals(items)
        approval = {"id": aid, "title": title, "desc": desc}
    return JSONResponse({"reply": text, "approval": approval})

@app.get("/api/site_approvals")
async def site_approvals_list(request: Request):
    # 결재함은 사장님(대표)만
    if _jageum_role(request) != "boss":
        return JSONResponse({"items": [], "forbidden": True})
    return JSONResponse({"items": _load_site_approvals()})

@app.post("/api/site_approvals/{aid}")
async def site_approval_act(aid: int, request: Request):
    if _jageum_role(request) != "boss":
        return JSONResponse({"error": "권한 없음 (대표 전용)"}, status_code=403)
    data = await request.json()
    action = data.get("action")
    items = _load_site_approvals()
    for a in items:
        if a["id"] == aid:
            a["status"] = "승인" if action == "approve" else "반려"
    _save_site_approvals(items)
    return JSONResponse({"ok": True})


# ===== CN메이커 (CN인사이더 → 부자주방 상세페이지) — Lightsail 중개 =====
CNMAKER_BASE = os.getenv("CNMAKER_BASE", "http://43.200.232.189")
CNMAKER_SECRET = os.getenv("CNMAKER_SECRET", "bj-cnmaker-2026")

@app.get("/cnmaker")
def cnmaker_page(request: Request):
    if not _site_auth(request):
        return FileResponse("static/site_login.html", headers={"Cache-Control": "no-store"})
    return FileResponse("static/cnmaker.html", headers={"Cache-Control": "no-store"})

@app.post("/cnmaker/api/start")
async def cnmaker_start(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url.startswith("http"):
        return JSONResponse({"error": "상품 URL을 넣어주세요"}, status_code=400)
    category = (data.get("category") or "kitchen").strip()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{CNMAKER_BASE}/cnmaker/start",
                              json={"url": url, "category": category}, headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

@app.post("/cnmaker/api/start_imgs")
async def cnmaker_start_imgs(request: Request):
    data = await request.json()
    images = data.get("images") or []
    title = (data.get("title") or "").strip()
    category = (data.get("category") or "kitchen").strip()
    if not images:
        return JSONResponse({"error": "이미지를 업로드해주세요"}, status_code=400)
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(f"{CNMAKER_BASE}/cnmaker/start_imgs",
                              json={"images": images, "title": title, "category": category},
                              headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

@app.get("/cnmaker/api/status")
async def cnmaker_status(job: str):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{CNMAKER_BASE}/cnmaker/status",
                             params={"job": job}, headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

@app.get("/cnmaker/api/history")
async def cnmaker_history():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{CNMAKER_BASE}/cnmaker/history", headers={"x-secret": CNMAKER_SECRET})
        return JSONResponse(r.json(), status_code=r.status_code)

# ── CN메이커 로고 자동삽입 (부자홀딩스/부자주방 로고) ──
CN_LOGO_PATH = "static/logo_bujajubang.png"
CN_LOGO_CFG = data_path("cnmaker_logo.json")
def _cn_logo_cfg():
    try:
        return json.loads(CN_LOGO_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True, "position": "bottom-right", "size_pct": 0.15, "margin_pct": 0.03}
def _cn_apply_logo(img_bytes: bytes) -> bytes:
    cfg = _cn_logo_cfg()
    if not cfg.get("enabled"):
        return img_bytes
    try:
        import io as _io
        from PIL import Image as _Img
        base = _Img.open(_io.BytesIO(img_bytes)).convert("RGBA")
        logo = _Img.open(CN_LOGO_PATH).convert("RGBA")
        W, H = base.size
        lw = max(1, int(W * float(cfg.get("size_pct", 0.15))))
        lh = max(1, int(logo.height * lw / logo.width))
        logo = logo.resize((lw, lh))
        m = int(W * float(cfg.get("margin_pct", 0.03)))
        pos = str(cfg.get("position", "bottom-right"))
        x = m if "left" in pos else (W - lw - m if "right" in pos else (W - lw) // 2)
        y = m if "top" in pos else (H - lh - m)
        base.alpha_composite(logo, (x, y))
        out = _io.BytesIO(); base.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return img_bytes

@app.get("/cnmaker/api/logo_config")
async def cn_logo_get():
    return _cn_logo_cfg()

@app.post("/cnmaker/api/logo_config")
async def cn_logo_set(request: Request):
    d = await request.json()
    cfg = {
        "enabled": bool(d.get("enabled", True)),
        "position": str(d.get("position", "bottom-right")),
        "size_pct": max(0.03, min(0.5, float(d.get("size_pct", 0.15)))),
        "margin_pct": max(0.0, min(0.2, float(d.get("margin_pct", 0.03)))),
    }
    CN_LOGO_CFG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "cfg": cfg}

@app.get("/cnmaker/api/result")
async def cnmaker_result(job: str, thumb: str = ""):
    params = {"job": job}
    if thumb:
        params["thumb"] = "1"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{CNMAKER_BASE}/cnmaker/result", params=params)
        content = r.content
        if not thumb and r.status_code == 200:
            content = _cn_apply_logo(content)  # 상세 이미지에 로고 자동합성
        return Response(content=content, media_type="image/jpeg")


app.mount("/static", StaticFiles(directory="static"), name="static")
