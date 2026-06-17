"""
cninsider API를 통해 1688 실력상가 상품 데이터를 가져옵니다.
"""
import httpx
from urllib.parse import unquote, quote

CNINSIDER_API = "https://saas.cninsidererp.com/dev-api/mall/product/searchKeywordProduct"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.cninsider.co.kr/",
    "Origin": "https://www.cninsider.co.kr",
}

def _is_pro_seller(item: dict) -> bool:
    ids = item.get("sellerIdentities", [])
    return "powerful_merchants" in ids or "tp_member" in ids

def _medal_label(level: str) -> str:
    try:
        n = int(level)
        return "⭐" * min(n, 5)
    except:
        return ""

async def fetch_1688_suppliers(keyword: str, top_n: int = 3) -> list[dict]:
    """
    cninsider에서 키워드로 실력상가 상품 검색.
    반환: [{name, badge, image, price, month_sold, rating, url, medal}, ...]
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=12) as client:
            r = await client.get(
                CNINSIDER_API,
                params={"pageSize": 40, "page": 1, "keyword": keyword, "filter": "", "type": 1},
            )
            r.raise_for_status()
            items = r.json()["data"]["KeywordProduct"]["data"]
    except Exception:
        return []

    # 실력상가 필터링 + 월판매 높은 순 정렬
    pro_items = [i for i in items if _is_pro_seller(i)]
    pro_items.sort(key=lambda x: x.get("monthSold", 0), reverse=True)

    result = []
    for item in pro_items[:top_n]:
        seller_data = item.get("sellerDataInfo", {})
        medal = _medal_label(seller_data.get("tradeMedalLevel", "0"))
        rating = float(seller_data.get("compositeServiceScore", item.get("tradeScore", 0)) or 0)
        month_sold = item.get("monthSold", 0)
        price = item.get("priceInfo", {}).get("price", "")
        promo_price = item.get("priceInfo", {}).get("promotionPrice", "")

        offer_id = item.get("offerId", "")
        if offer_id:
            url = f"https://www.cninsider.co.kr/mall/#/detail?id={offer_id}"
        else:
            search_kw = item.get("subjectTrans") or item.get("subject", "")
            url = f"https://www.cninsider.co.kr/mall/#/search?keyword={quote(search_kw, safe='')}" if search_kw else item.get("promotionURL", "")

        result.append({
            "name": item.get("subject", ""),
            "name_kr": item.get("subjectTrans", ""),
            "badge": True,
            "image": item.get("imageUrl", ""),
            "price": promo_price or price,
            "month_sold": month_sold,
            "rating": rating,
            "medal": medal,
            "repurchase": item.get("repurchaseRate", ""),
            "url": url,
            "offer_id": item.get("offerId", ""),
        })

    return result
