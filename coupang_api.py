import hmac
import hashlib
import datetime
import httpx
from urllib.parse import quote
from typing import List, Dict, Optional


def _generate_auth(method: str, url: str, secret_key: str, access_key: str) -> str:
    """쿠팡파트너스 API HMAC-SHA256 서명 생성"""
    parts = url.split("?", 1)
    path = parts[0]
    query = parts[1] if len(parts) > 1 else ""

    dt = datetime.datetime.utcnow()
    signed_date = dt.strftime("%y%m%d") + "T" + dt.strftime("%H%M%S") + "Z"

    # 핵심: path + query를 ?없이 붙임 (공식 Java SDK 방식)
    message = signed_date + method + path + query
    sig = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={sig}"


class CoupangPartnersAPI:
    BASE_URL = "https://api-gateway.coupang.com"

    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key

    def _headers(self, method: str, url: str) -> dict:
        return {
            "Authorization": _generate_auth(method, url, self.secret_key, self.access_key),
            "Content-Type": "application/json;charset=UTF-8",
        }

    async def search(self, keyword: str, limit: int = 20, sub_id: str = "") -> Dict:
        """상품 검색 — keyword 기준 최대 100건"""
        # 한글 키워드는 URL 인코딩 필요 (서명도 인코딩된 값으로 계산)
        params = f"keyword={quote(keyword)}&limit={limit}"
        if sub_id:
            params += f"&subId={sub_id}"
        url = f"/v2/providers/affiliate_open_api/apis/openapi/products/search?{params}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.BASE_URL + url, headers=self._headers("GET", url))
            r.raise_for_status()
            return r.json()

    async def best_sellers(self, category_id: str = "1", limit: int = 20) -> Dict:
        """카테고리별 베스트셀러"""
        url = f"/v2/providers/affiliate_open_api/apis/openapi/products/bestcategories/{category_id}?limit={limit}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.BASE_URL + url, headers=self._headers("GET", url))
            r.raise_for_status()
            return r.json()

    def parse_products(self, resp: Dict) -> List[Dict]:
        """API 응답에서 상품 리스트 정규화
        Partners 어필리에이트 API는 avgRating/reviewCount를 제공하지 않음."""
        raw = resp.get("data", {}).get("productData", [])
        products = []
        for i, p in enumerate(raw):
            products.append({
                "rank": i + 1,
                "product_id": p.get("productId"),
                "name": p.get("productName", ""),
                "price": p.get("productPrice", 0),
                "image": p.get("productImage", ""),
                "url": p.get("productUrl", ""),
                "is_rocket": p.get("isRocket", False),
                "is_rocket_wow": p.get("isRocketWow", False),
                "is_free_shipping": p.get("isFreeShipping", False),
                "category_name": p.get("categoryName", ""),
            })
        return products
