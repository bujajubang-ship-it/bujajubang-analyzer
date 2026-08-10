"""쿠팡 WING 셀러(마켓플레이스) OpenAPI 클라이언트.

- 인증: CEA HMAC-SHA256 (파트너스 API와 동일 서명 방식)
- 키: COUPANG_WING_ACCESS_KEY / COUPANG_WING_SECRET_KEY / COUPANG_WING_VENDOR_ID (.env)
- 현재는 읽기 전용 메서드만. 재고/가격/쿠폰 변경(쓰기)은 안전장치 합의 후 추가.
"""
import hmac
import hashlib
import datetime
import httpx

BASE_URL = "https://api-gateway.coupang.com"


def _auth(method: str, path_with_query: str, access_key: str, secret_key: str) -> str:
    parts = path_with_query.split("?", 1)
    path = parts[0]
    query = parts[1] if len(parts) > 1 else ""
    dt = datetime.datetime.utcnow()
    signed_date = dt.strftime("%y%m%d") + "T" + dt.strftime("%H%M%S") + "Z"
    message = signed_date + method + path + query
    sig = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={sig}"


class CoupangWing:
    def __init__(self, access_key: str, secret_key: str, vendor_id: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.vendor_id = vendor_id

    def _headers(self, method: str, path_with_query: str) -> dict:
        return {
            "Authorization": _auth(method, path_with_query, self.access_key, self.secret_key),
            "Content-Type": "application/json;charset=UTF-8",
        }

    async def list_products(self, max_per_page: int = 10, next_token: str = "") -> tuple:
        """판매자 본인 등록상품 목록 (읽기 전용, 연결 검증용)."""
        q = f"vendorId={self.vendor_id}&maxPerPage={max_per_page}"
        if next_token:
            q += f"&nextToken={next_token}"
        path = f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products?{q}"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(BASE_URL + path, headers=self._headers("GET", path))
            return r.status_code, r.text

    async def get_item_inventory(self, vendor_item_id) -> tuple:
        """옵션(vendorItem) 단위 수량/가격/판매상태 조회 (읽기 전용)."""
        path = f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vendor_item_id}/inventories"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(BASE_URL + path, headers=self._headers("GET", path))
            return r.status_code, r.text

    async def get_product_detail(self, seller_product_id) -> tuple:
        """등록상품 상세 (옵션 vendorItemId, 수량, 가격 포함)."""
        path = f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(BASE_URL + path, headers=self._headers("GET", path))
            return r.status_code, r.text
