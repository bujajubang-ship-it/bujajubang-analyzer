#!/usr/bin/env python3
"""📊 시장조사 — 쿠팡 윙 원천 데이터로 소싱 후보와 업소용 시장을 본다.

데이터는 wing_slim.db 한 덩어리(약 54MB). 영구 디스크(/var/data)에 두고 읽기만 한다.
만드는 법: 맥에서 `python3 ~/wing-viewer/make_slim_db.py` → 업로드 API로 올린다.

카테고리 758개(주방 746 + 업소용 20) · 상품 78,480 · 검색어 74,994.
"""
import json
import os
import re
import shutil
import sqlite3

from fastapi import HTTPException
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse


def _guard(request: Request):
    """소싱 사이트 로그인과 같은 잠금. 업로드는 시크릿으로 따로 본다."""
    from main import _site_auth
    if not _site_auth(request):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")


router = APIRouter(prefix="/market/api", dependencies=[Depends(_guard)])
upload_router = APIRouter(prefix="/market/api")   # 업로드는 로그인 대신 시크릿으로

DATA_DIR = os.getenv("DATA_DIR", ".")
DB = os.path.join(DATA_DIR, "wing_slim.db")
META_FILE = os.path.join(DATA_DIR, "cat_meta.json")
UPLOAD_SECRET = os.getenv("MARKET_UPLOAD_SECRET", "bj-market-2026")

_con = None
_cat_meta = None


def con():
    """DB는 읽기 전용으로 한 번만 연다. 업로드로 갈아끼우면 다시 연다."""
    global _con
    if _con is None:
        if not os.path.exists(DB):
            return None
        _con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, check_same_thread=False)
        _con.row_factory = sqlite3.Row
    return _con


def cat_meta():
    global _cat_meta
    if _cat_meta is None:
        try:
            _cat_meta = json.load(open(META_FILE, encoding="utf-8")).get("meta", {})
        except Exception:
            _cat_meta = {}
    return _cat_meta


def rows(sql, args=()):
    c = con()
    if c is None:
        return []
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    c = con()
    if c is None:
        return None
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def search_words(q):
    q = re.sub(r'["\'()*:^-]', " ", q or "").strip()
    return [w for w in q.split() if w]


def add_scope(top, where, args, alias="c.", p_alias=None):
    """top=-1 주방 전체 · top=-2 업소용(식당·카페·매장)."""
    if top == -1:
        where.append("%skitchen = 1" % alias)
    elif top == -2:
        where.append("%sbiz = 1" % (p_alias or alias))
    elif top:
        where.append("%stop_code = ?" % alias)
        args.append(top)


def add_text_filter(q, col, fts_table, join, where, args):
    """빠른 색인(FTS)은 3글자 이상만 찾는다. '냄비'처럼 두 글자가 섞이면 LIKE로 넘긴다."""
    words = search_words(q)
    if not words:
        return join
    if all(len(w) >= 3 for w in words):
        join += " JOIN %s f ON f.rowid = %s.rowid" % (fts_table, col.split(".")[0])
        where.append("%s MATCH ?" % fts_table)
        args.append(" ".join('"%s"' % w for w in words))
    else:
        for w in words:
            where.append("%s LIKE ?" % col)
            args.append("%" + w + "%")
    return join


# 표 제목을 눌러 정렬할 때 쓰는 열 정의 (방향은 dir 파라미터로 따로 받는다)
P_COL = {
    "sales": "sales_28d", "conv": "CAST(sales_28d AS REAL)/NULLIF(pv_28d,0)",
    "gv": "glance_view", "growth": "gv_var", "clicks": "srp_clicks", "ctr": "ctr",
    "price": "avg_price", "review": "rating_count", "launch": "launch_date",
    "impression": "impression", "rank": "rank", "name": "name",
}
C_COL = {
    "sales": "sales_sum", "gv": "glance_view", "growth": "gv_var", "ads": "ads_pct",
    "review": "med_review", "new": "new_ratio", "price": "avg_price",
    "conc": "CAST(gv_top10 AS REAL)/NULLIF(gv_top100,0)", "name": "path",
    "nprod": "n_products",
}
K_COL = {
    "volume": "search_volume", "growth": "sv_var", "clicks": "srp_click",
    "ctr": "ctr", "price": "avg_price", "cats": "n_cats", "name": "keyword",
}


def order_by(col, dir_, table_cols, fallback):
    """표 제목 클릭 정렬. 허용된 열 이름만 쓴다(임의 SQL 방지)."""
    if col and col in table_cols:
        d = "ASC" if str(dir_).lower() == "asc" else "DESC"
        # 값이 없는 행은 어느 방향으로 정렬하든 항상 뒤로 보낸다
        return "(%s) IS NULL, (%s) %s" % (table_cols[col], table_cols[col], d)
    return fallback


P_SORT = {
    "sales": "sales_28d DESC",
    "conv": "CAST(sales_28d AS REAL)/NULLIF(pv_28d,0) DESC",
    "gv": "glance_view DESC",
    "ctr": "ctr DESC",
    "clicks": "srp_clicks DESC",
    "growth": "gv_var DESC",
    "new": "launch_date DESC",
    "cheap": "avg_price ASC",
    "expensive": "avg_price DESC",
    "review_low": "rating_count ASC, glance_view DESC",
    "rank": "cat_code, rank",
}
C_SORT = {
    "sales": "sales_sum DESC",
    "gv": "glance_view DESC",
    "growth": "gv_var DESC",
    "ads_low": "ads_pct ASC, glance_view DESC",
    "review_low": "med_review ASC, glance_view DESC",
    "new": "new_ratio DESC, glance_view DESC",
    "cheap": "avg_price ASC",
    "expensive": "avg_price DESC",
    "name": "path",
}
K_SORT = {
    "volume": "search_volume DESC",
    "growth": "sv_var DESC",
    "clicks": "srp_click DESC",
    "ctr": "ctr DESC",
    "cheap": "avg_price ASC",
    "expensive": "avg_price DESC",
}


@router.get("/meta")
def api_meta():
    if con() is None:
        return {"ready": False, "error": "데이터가 아직 올라오지 않았습니다."}
    m = {r["k"]: r["v"] for r in rows("SELECT k,v FROM meta")}
    tops = rows("""SELECT top_code AS code, top_name AS name, COUNT(*) AS n, SUM(glance_view) AS gv
                   FROM category GROUP BY top_code ORDER BY gv DESC""")
    kit = one("SELECT COUNT(*) n, SUM(glance_view) gv, SUM(n_products) np FROM category WHERE kitchen=1")
    return {"ready": True,
            "period": [m.get("period_from"), m.get("period_to")],
            "built_at": m.get("built_at"), "tops": tops, "kitchen": kit,
            "n_products": one("SELECT COUNT(*) c FROM product")["c"],
            "n_categories": one("SELECT COUNT(*) c FROM category")["c"],
            "n_fresh": one("SELECT COUNT(*) c FROM product WHERE fresh=1")["c"]}


@router.get("/categories")
def api_categories(top: int = 0, q: str = "", sort: str = "gv",
                   col: str = "", dir: str = "desc",
                   min_gv: int = 0, max_ads: float = 100,
                   min_price: int = 0, max_price: int = 0,
                   limit: int = 100, offset: int = 0):
    where, args = ["glance_view >= ?", "ads_pct <= ?"], [min_gv, max_ads]
    if min_price:
        where.append("avg_price >= ?")
        args.append(min_price)
    if max_price:
        where.append("avg_price <= ?")
        args.append(max_price)
    add_scope(top, where, args, "")
    if q.strip():
        where.append("path LIKE ?")
        args.append("%" + q.strip() + "%")
    w = " AND ".join(where)
    total = one("SELECT COUNT(*) c FROM category WHERE " + w, args)["c"]
    order = order_by(col, dir, C_COL, C_SORT.get(sort, C_SORT["gv"]))
    items = rows("SELECT * FROM category WHERE %s ORDER BY %s LIMIT ? OFFSET ?"
                 % (w, order), args + [min(limit, 500), offset])
    return {"total": total, "items": items}


@router.get("/category/{code}")
def api_category(code: int):
    c = one("SELECT * FROM category WHERE code = ?", (code,))
    if not c:
        return JSONResponse({"error": "없는 카테고리"}, status_code=404)
    return {
        "category": c,
        "reg": cat_meta().get(str(code)),
        "products": rows("SELECT * FROM product WHERE cat_code=? ORDER BY rank", (code,)),
        "keywords": rows("SELECT * FROM keyword WHERE cat_code=? ORDER BY rank", (code,)),
        "brands": rows("SELECT * FROM brand WHERE cat_code=? ORDER BY rank LIMIT 30", (code,)),
    }


@router.get("/products")
def api_products(q: str = "", cat: int = 0, top: int = 0, sort: str = "gv",
                 col: str = "", dir: str = "desc",
                 min_gv: int = 0, max_review: int = 0, since: str = "",
                 min_sales: int = 0, max_price: int = 0, min_price: int = 0,
                 limit: int = 60, offset: int = 0):
    join = "FROM product p JOIN category c ON c.code = p.cat_code"
    where, args = ["p.glance_view >= ?"], [min_gv]
    join = add_text_filter(q, "p.name", "product_fts", join, where, args)
    if cat:
        where.append("p.cat_code = ?")
        args.append(cat)
    add_scope(top, where, args, p_alias="p.")
    if min_sales:
        where.append("p.sales_28d >= ?")
        args.append(min_sales)
    if min_sales or sort in ("sales", "conv") or col in ("sales", "conv"):
        where.append("p.sales_dup = 0")
    if max_review:
        where.append("p.rating_count <= ?")
        args.append(max_review)
    if since:
        where.append("p.launch_date >= ?")
        args.append(since)
    if min_price:
        where.append("p.avg_price >= ?")
        args.append(min_price)
    if max_price:
        where.append("p.avg_price <= ?")
        args.append(max_price)
    w = " AND ".join(where)
    order = order_by(col, dir, P_COL, P_SORT.get(sort, P_SORT["gv"]))
    order = re.sub(r"\b(glance_view|ctr|srp_clicks|gv_var|launch_date|avg_price|rating_count|cat_code|rank|sales_28d|pv_28d|impression|name)\b",
                   r"p.\1", order)
    total = one("SELECT COUNT(*) c %s WHERE %s" % (join, w), args)["c"]
    items = rows("""SELECT p.*, c.name AS cat_name, c.path AS cat_path, c.top_name
                    %s WHERE %s ORDER BY %s LIMIT ? OFFSET ?""" % (join, w, order),
                 args + [min(limit, 300), offset])
    return {"total": total, "items": items}


@router.get("/keywords")
def api_keywords(q: str = "", cat: int = 0, top: int = 0, sort: str = "volume",
                 col: str = "", dir: str = "desc",
                 min_volume: int = 0, group: int = 1, limit: int = 60, offset: int = 0):
    """같은 '냄비'가 카테고리마다 한 줄씩 들어있다. 기본은 검색어당 한 줄로 묶는다."""
    join = "FROM keyword k JOIN category c ON c.code = k.cat_code"
    where, args = ["k.search_volume >= ?"], [min_volume]
    join = add_text_filter(q, "k.keyword", "keyword_fts", join, where, args)
    if cat:
        where.append("k.cat_code = ?")
        args.append(cat)
    add_scope(top, where, args)
    w = " AND ".join(where)
    order = order_by(col, dir, K_COL, K_SORT.get(sort, K_SORT["volume"]))
    order = re.sub(r"\b(search_volume|sv_var|srp_click|ctr|avg_price|keyword)\b", r"k.\1", order)
    if not group:
        total = one("SELECT COUNT(*) c %s WHERE %s" % (join, w), args)["c"]
        items = rows("""SELECT k.*, c.name AS cat_name, c.path AS cat_path
                        %s WHERE %s ORDER BY %s LIMIT ? OFFSET ?""" % (join, w, order),
                     args + [min(limit, 300), offset])
        return {"total": total, "items": items}

    for c_ in ("search_volume", "srp_click", "n_cats", "keyword"):
        order = order.replace("k." + c_, c_)
    grouped = """
        WITH g AS (
          SELECT k.keyword, k.sv_var, k.avg_price, k.price_start, k.price_end, k.ctr,
                 k.cat_code, c.name AS cat_name, c.path AS cat_path,
                 ROW_NUMBER() OVER (PARTITION BY k.keyword ORDER BY k.srp_click DESC) AS rn,
                 SUM(k.srp_click) OVER (PARTITION BY k.keyword) AS srp_click,
                 COUNT(*) OVER (PARTITION BY k.keyword) AS n_cats,
                 MAX(k.search_volume) OVER (PARTITION BY k.keyword) AS search_volume
          %s WHERE %s
        )
        SELECT * FROM g WHERE rn = 1""" % (join, w)
    total = one("SELECT COUNT(*) c FROM (%s)" % grouped, args)["c"]
    items = rows("%s ORDER BY %s LIMIT ? OFFSET ?" % (grouped, order),
                 args + [min(limit, 300), offset])
    return {"total": total, "items": items}


@router.get("/opportunity")
def api_opportunity(top: int = 0, mode: str = "sales",
                    col: str = "", dir: str = "desc",
                    min_gv: int = 500, max_review: int = 100,
                    since: str = "2025-01-01", min_price: int = 0, max_price: int = 0,
                    limit: int = 60, offset: int = 0):
    """sales: 많이 팔리는데 리뷰 적은 것 · conv: 조회 대비 잘 팔리는 것
    product: 조회수 높고 리뷰 적은 것 · category: 경쟁 덜한 카테고리"""
    if mode == "category":
        where, args = ["glance_view >= ?"], [min_gv * 10]
        add_scope(top, where, args, "")
        w = " AND ".join(where)
        total = one("SELECT COUNT(*) c FROM category WHERE " + w, args)["c"]
        items = rows("""SELECT *, ROUND(glance_view/(med_review+50.0)/(ads_pct+5.0)*100,1) AS score
                        FROM category WHERE %s ORDER BY %s LIMIT ? OFFSET ?"""
                     % (w, order_by(col, dir, C_COL, "score DESC")),
                     args + [min(limit, 300), offset])
        return {"total": total, "items": items, "mode": "category"}

    where = ["p.glance_view >= ?", "p.rating_count <= ?", "p.launch_date >= ?"]
    args = [min_gv, max_review, since]
    add_scope(top, where, args, p_alias="p.")
    if mode in ("sales", "conv"):
        where.append("p.sales_28d IS NOT NULL")
        where.append("p.sales_dup = 0")
        if mode == "conv":
            where.append("p.pv_28d > 0")
    if min_price:
        where.append("p.avg_price >= ?")
        args.append(min_price)
    if max_price:
        where.append("p.avg_price <= ?")
        args.append(max_price)
    w = " AND ".join(where)
    total = one("SELECT COUNT(*) c FROM product p JOIN category c ON c.code=p.cat_code WHERE " + w, args)["c"]
    score = {
        "sales": "ROUND(p.sales_28d/(p.rating_count+20.0)*100,1)",
        "conv": "ROUND(CAST(p.sales_28d AS REAL)/p.pv_28d*100,2)",
    }.get(mode, "ROUND(p.glance_view/(p.rating_count+20.0),1)")
    o = order_by(col, dir, P_COL, "score DESC")
    o = re.sub(r"\b(glance_view|ctr|srp_clicks|gv_var|launch_date|avg_price|rating_count|rank|sales_28d|pv_28d|impression|name)\b",
               r"p.\1", o)
    items = rows("""SELECT p.*, c.name AS cat_name, c.path AS cat_path, c.top_name, %s AS score
                    FROM product p JOIN category c ON c.code=p.cat_code
                    WHERE %s ORDER BY %s LIMIT ? OFFSET ?""" % (score, w, o),
                 args + [min(limit, 300), offset])
    return {"total": total, "items": items, "mode": mode}


@router.get("/item/{item_id}")
def api_item(item_id: int):
    return {"items": rows("""SELECT p.*, c.name AS cat_name, c.path AS cat_path
                             FROM product p JOIN category c ON c.code=p.cat_code
                             WHERE p.item_id=? ORDER BY p.glance_view DESC""", (item_id,))}


@upload_router.post("/upload-db")
async def upload_db(request: Request):
    """맥에서 새로 구운 DB를 올린다(약 54MB). 올리면 바로 갈아끼운다.

      curl -X POST -H "x-secret: ..." --data-binary @wing_slim.db \
           https://.../market/api/upload-db
    """
    global _con
    if request.headers.get("x-secret") != UPLOAD_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    tmp = DB + ".tmp"
    n = 0
    with open(tmp, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            n += len(chunk)
    # 제대로 된 SQLite인지 확인하고 갈아끼운다
    try:
        t = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
        cnt = t.execute("SELECT COUNT(*) FROM category").fetchone()[0]
        t.close()
    except Exception as e:
        os.remove(tmp)
        return JSONResponse({"error": "DB가 아니거나 깨졌습니다: %s" % e}, status_code=400)
    if _con:
        _con.close()
        _con = None
    shutil.move(tmp, DB)
    return {"ok": True, "bytes": n, "mb": round(n / 1024 / 1024, 1), "categories": cnt}


@upload_router.post("/upload-meta")
async def upload_meta(request: Request):
    """카테고리 등록요건(cat_meta.json)을 올린다."""
    global _cat_meta
    if request.headers.get("x-secret") != UPLOAD_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.body()
    with open(META_FILE, "wb") as f:
        f.write(body)
    _cat_meta = None
    return {"ok": True, "bytes": len(body)}
