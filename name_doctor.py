#!/usr/bin/env python3
"""📝 상품명 처방 — 잘 팔리는 경쟁 상품이 쓰는 말 중 내 상품명에 빠진 것을 찾는다.

쿠팡은 상품명에 든 말로 검색에 걸린다. 그래서 같은 물건이라도
'전골냄비 인덕션'이라 쓴 사람과 '전골 냄비'라 쓴 사람의 노출이 다르다.

방법
  1) 내 상품이 어느 카테고리인지 (쿠팡 카테고리 추천 API로 미리 판정 → my_products.json)
  2) 그 카테고리에서 잘 나가는 상품들이 상품명에 공통으로 쓰는 말을 뽑고
  3) 그 말이 실제로 검색되는 말인지(검색량) 대조해서
  4) 내 상품명에 없는 것만 처방한다.
"""
import json
import os
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import market_api as M

router = APIRouter(prefix="/market/api/doctor", dependencies=[Depends(M._guard)])
upload_router = APIRouter(prefix="/market/api/doctor")

MY_FILE = os.path.join(M.DATA_DIR, "my_products.json")
_my = None

# 상품명에 흔하지만 검색에는 도움이 안 되는 말
STOP = {"개입", "세트", "무료", "무료배송", "당일발송", "특가", "할인", "정품", "국내산",
        "제작", "박스", "이벤트", "사은품", "증정", "최저가", "인기", "추천", "신상"}


def my_products():
    global _my
    if _my is None:
        try:
            _my = json.load(open(MY_FILE, encoding="utf-8"))
        except Exception:
            _my = []
    return _my


def tokens(s):
    """상품명을 검색에 쓰이는 낱말로 쪼갠다. 숫자·기호·치수는 뺀다."""
    s = re.sub(r"[\[\](){}/,·+×~\-_|]", " ", s or "")
    out = []
    for w in s.split():
        w = w.strip()
        if len(w) < 2 or w in STOP:
            continue
        if re.fullmatch(r"[\d.]+[a-zA-Z]*", w):     # 30cm, 1.5L, 2024 같은 것
            continue
        out.append(w)
    return out


@router.get("/my")
def api_my(q: str = "", limit: int = 100):
    """내 상품 목록. 카테고리가 판정된 것만."""
    items = my_products()
    if q.strip():
        items = [x for x in items if q.strip() in x.get("name", "")]
    # 내 시장조사 DB에 있는 카테고리인지 표시
    codes = {r["code"] for r in M.rows("SELECT code FROM category")}
    out = []
    for x in items[:limit]:
        out.append({**x, "known": x.get("cat_code") in codes})
    return {"total": len(items), "items": out,
            "ready": bool(items)}


@router.get("/prescribe")
def api_prescribe(name: str = "", cat: int = 0, top_n: int = 20):
    """상품명 하나를 넣으면 그 카테고리 상위 상품이 쓰는 말과 대조해 처방한다."""
    if not name.strip():
        return JSONResponse({"error": "상품명이 필요합니다"}, status_code=400)

    if not cat:
        for x in my_products():
            if x.get("name") == name:
                cat = x.get("cat_code") or 0
                break
    if not cat:
        return {"error": "이 상품의 카테고리를 모릅니다. 카테고리를 골라주세요."}

    c = M.one("SELECT * FROM category WHERE code = ?", (cat,))
    if not c:
        return {"error": "이 카테고리는 시장조사 데이터에 없습니다(주방·업소용 758개 밖)."}

    prods = M.rows("""SELECT name, rank, glance_view, sales_28d FROM product
                      WHERE cat_code=? AND sales_dup=0 ORDER BY rank""", (cat,))
    if len(prods) < 10:
        return {"error": "이 카테고리는 비교할 상품이 너무 적습니다."}

    top = [p for p in prods if p["rank"] <= top_n]
    bot = [p for p in prods if p["rank"] > top_n * 2]
    ct = Counter(w for p in top for w in set(tokens(p["name"])))
    cb = Counter(w for p in bot for w in set(tokens(p["name"])))

    # 그 카테고리에서 실제로 검색되는 말 (검색어를 낱말로 쪼개 검색량을 합산)
    kw_weight = Counter()
    for k in M.rows("SELECT keyword, search_volume FROM keyword WHERE cat_code=? ORDER BY rank", (cat,)):
        for w in tokens(k["keyword"]):
            kw_weight[w] += k["search_volume"] or 0

    # 남의 브랜드명은 절대 처방하지 않는다. 상품명에 넣으면 쿠팡 제재 대상이다.
    # 브랜드명 '전체'와 똑같은 말만 뺀다 — 그래야 '업소용냉장고 우성' 같은 브랜드 때문에
    # '업소용'(검색량 59,099) 같은 핵심 일반어까지 같이 사라지지 않는다.
    brands = set()
    for b in M.rows("SELECT brand_name FROM brand WHERE cat_code=?", (cat,)):
        bn = (b["brand_name"] or "").strip()
        if bn:
            brands.add(bn)
            brands.add(bn.replace(" ", ""))

    mine = set(tokens(name))
    rows = []
    for w, a in ct.items():
        if a < 2 or w in brands:
            continue
        ra = a * 100.0 / max(len(top), 1)
        rb = cb.get(w, 0) * 100.0 / max(len(bot), 1)
        rows.append({
            "word": w,
            "top_pct": round(ra),          # 상위 상품 중 몇 %가 쓰는가
            "bot_pct": round(rb),          # 하위 상품 중 몇 %가 쓰는가
            "gap": round(ra - rb),         # 클수록 상위만 쓰는 말
            "search": kw_weight.get(w, 0),  # 이 말이 실제로 검색되는 양
            "have": w in mine,             # 내 상품명에 이미 있는가
        })
    # 상위가 많이 쓰고 + 검색도 되는 순
    # 실제로 검색되는 말을 앞세운다 — 상위가 쓴다고 다 좋은 게 아니라 검색이 돼야 한다
    rows.sort(key=lambda r: (r["gap"] + (40 if r["search"] >= 1000 else 15 if r["search"] else 0)),
              reverse=True)

    missing = [r for r in rows if not r["have"] and (r["search"] >= 100 or r["gap"] >= 12)][:12]
    useless = [w for w in mine if w not in ct and not kw_weight.get(w)][:12]

    return {
        "category": {"code": c["code"], "name": c["name"], "path": c["path"],
                     "med_review": c["med_review"], "avg_price": c["avg_price"]},
        "my_name": name,
        "compared": {"top": len(top), "bottom": len(bot)},
        "missing": missing,      # 넣으면 좋을 말
        "useless": useless,      # 아무도 안 쓰고 검색도 안 되는 말
        "all": rows[:25],
        "top_examples": [p["name"] for p in top[:5]],
    }


@upload_router.post("/upload-my")
async def upload_my(request: Request):
    """내 상품+카테고리 판정 결과(my_products.json)를 올린다."""
    global _my
    if request.headers.get("x-secret") != M.UPLOAD_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.body()
    try:
        n = len(json.loads(body))
    except Exception as e:
        return JSONResponse({"error": "JSON이 아닙니다: %s" % e}, status_code=400)
    with open(MY_FILE, "wb") as f:
        f.write(body)
    _my = None
    return {"ok": True, "products": n}
