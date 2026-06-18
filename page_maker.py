"""
상세페이지 메이커 — 외부 쇼핑몰 이미지 스크래핑 + 로고 삽입 + 플랫폼별 리사이즈
"""
import io
import zipfile
import httpx
from PIL import Image
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import Optional

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 플랫폼별 이미지 규격
COUPANG_MAIN   = (1000, 1000)   # 메인 이미지: 정사각형
COUPANG_DETAIL = 1000           # 상세 이미지: 너비
NAVER_MAIN     = (1000, 1000)
NAVER_DETAIL   = 860            # 네이버 스마트스토어 상세: 860px 너비


def _get_img_src(img, base: str, page_url: str) -> str:
    """img 태그에서 실제 이미지 URL 추출 (cafe24 ec-data-src 포함)"""
    src = (img.get("src") or img.get("data-src") or img.get("data-original")
           or img.get("ec-data-src") or img.get("data-ec-src") or "")
    if not src or src.startswith("data:"):
        return ""
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = base + src
    elif not src.startswith("http"):
        src = urljoin(page_url, src)
    return src


def _is_content_image(src: str) -> bool:
    low = src.lower()
    skip = ["logo", "blank", "spacer", "pixel", "tracking",
            ".gif", "layout/", "close_btn", "naver_pay",
            "btn_count", "btn_coupon", "btn_price",
            "ico_pay", "ico_under", "star5",
            "skin/base", "skin/admin", "echosting"]
    return not any(k in low for k in skip)


async def scrape_images(url: str) -> dict:
    """상품 페이지에서 이미지 URL 목록과 텍스트 추출 (cafe24 최적화)"""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return {"error": str(e), "images": [], "title": "", "description": ""}

    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # 제목 — og:title > <title> > DOM 요소 순으로 시도
    title = ""
    og_title = soup.select_one('meta[property="og:title"]')
    if og_title and og_title.get("content"):
        title = og_title["content"].split(" - ")[0].strip()
    if not title:
        t = soup.select_one("title")
        if t:
            title = t.get_text(strip=True).split(" - ")[0].strip()
    if not title:
        for sel in ["#prdName", ".goods_name", ".product_name", "#goods_name",
                    ".prd-name", "h1"]:
            el = soup.select_one(sel)
            txt = el.get_text(strip=True) if el else ""
            if txt and not txt.startswith("#"):
                title = txt
                break

    images = []
    seen = set()

    def _add(src):
        if src and src not in seen and _is_content_image(src):
            seen.add(src)
            images.append(src)

    # ① 메인(대표) 이미지 — 상품 썸네일/big 이미지 우선
    MAIN_SELECTORS = [
        "#mainImage", ".goods_img_wrap", ".keyImg",
        ".thumb_wrap", ".thumbnail_wrap", ".goods_thumbnails",
        ".prdImgWrap", "#prdImgWrap", ".product_img",
    ]
    for sel in MAIN_SELECTORS:
        el = soup.select_one(sel)
        if el:
            for img in el.find_all("img"):
                _add(_get_img_src(img, base, url))
            if images:
                break

    # cafe24 product/big URL 패턴 — 메인 이미지 직접 추출
    for img in soup.find_all("img"):
        src = _get_img_src(img, base, url)
        if src and "/product/big/" in src:
            _add(src)

    # ② 상세 이미지 영역
    DETAIL_SELECTORS = [
        "#prdDetail", ".prdDetailArea", ".goods_description",
        "#goods_detail", ".detail_content", ".product_detail",
        ".prd-detail", "#detail_image", ".detail_image",
    ]
    detail_el = None
    for sel in DETAIL_SELECTORS:
        el = soup.select_one(sel)
        if el:
            detail_el = el
            break

    if detail_el:
        for img in detail_el.find_all("img"):
            src = _get_img_src(img, base, url)
            if src and _is_content_image(src):
                _add(src)

    # cafe24 NNEditor (상세 이미지) — ec-data-src로 직접 추출
    for img in soup.find_all("img", attrs={"ec-data-src": True}):
        src = _get_img_src(img, base, url)
        if src and _is_content_image(src):
            _add(src)

    # ③ 아무것도 못 찾으면 전체 페이지 스캔 (크기 필터 적용)
    if not images:
        for img in soup.find_all("img"):
            src = _get_img_src(img, base, url)
            if not src:
                continue
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if (w and w < 100) or (h and h < 100):
                continue
            _add(src)

    # description
    desc_el = soup.select_one(".goods_description, #prdDetail, .detail_content")
    description = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

    return {"images": images, "title": title, "description": description, "error": ""}


def _open_image(data: bytes) -> Optional[Image.Image]:
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def _place_logo(base_img: Image.Image, logo: Image.Image,
                position: str, size_pct: float) -> Image.Image:
    """
    position: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | 'center'
    size_pct: 로고 너비를 기준 이미지 너비의 몇 % 로 할지 (0.05 ~ 0.3)
    """
    bw, bh = base_img.size
    logo_w = max(40, int(bw * size_pct))
    ratio = logo_w / logo.width
    logo_h = int(logo.height * ratio)
    logo_resized = logo.resize((logo_w, logo_h), Image.LANCZOS)

    pad = max(10, int(bw * 0.02))
    pos_map = {
        "top-left":     (pad, pad),
        "top-right":    (bw - logo_w - pad, pad),
        "bottom-left":  (pad, bh - logo_h - pad),
        "bottom-right": (bw - logo_w - pad, bh - logo_h - pad),
        "center":       ((bw - logo_w) // 2, (bh - logo_h) // 2),
    }
    x, y = pos_map.get(position, pos_map["bottom-right"])

    result = base_img.copy()
    result.paste(logo_resized, (x, y), logo_resized)
    return result


def _resize_square(img: Image.Image, size: tuple, bg_color=(255, 255, 255)) -> Image.Image:
    """비율 유지하며 정사각형 캔버스에 배치"""
    img_rgb = img.convert("RGB")
    img_rgb.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGB", size, bg_color)
    offset = ((size[0] - img_rgb.width) // 2, (size[1] - img_rgb.height) // 2)
    canvas.paste(img_rgb, offset)
    return canvas


def _resize_width(img: Image.Image, width: int) -> Image.Image:
    """너비를 고정하고 높이는 비율 유지"""
    if img.width <= 0:
        return img
    ratio = width / img.width
    new_h = int(img.height * ratio)
    return img.convert("RGB").resize((width, new_h), Image.LANCZOS)


def _img_to_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


async def build_processed_zip(
    image_urls: list[str],
    logo_bytes: Optional[bytes],
    logo_position: str,
    logo_size_pct: float,
    is_main: list[bool],  # 각 이미지가 메인(정사각형)인지 상세페이지인지
) -> bytes:
    """이미지 다운로드 → 로고 삽입 → 쿠팡/네이버 리사이즈 → zip 반환"""

    logo = None
    if logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            logo = None

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            for idx, (url, is_m) in enumerate(zip(image_urls, is_main)):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    img = _open_image(resp.content)
                    if img is None:
                        continue
                except Exception:
                    continue

                # 로고 삽입
                if logo:
                    img = _place_logo(img, logo, logo_position, logo_size_pct)

                fname = f"{idx+1:02d}"

                if is_m:
                    # 메인 이미지: 정사각형
                    cp = _resize_square(img, COUPANG_MAIN)
                    nv = _resize_square(img, NAVER_MAIN)
                    zf.writestr(f"coupang/main_{fname}.jpg", _img_to_bytes(cp))
                    zf.writestr(f"naver/main_{fname}.jpg", _img_to_bytes(nv))
                else:
                    # 상세 이미지: 너비 고정
                    cp = _resize_width(img, COUPANG_DETAIL)
                    nv = _resize_width(img, NAVER_DETAIL)
                    zf.writestr(f"coupang/detail_{fname}.jpg", _img_to_bytes(cp))
                    zf.writestr(f"naver/detail_{fname}.jpg", _img_to_bytes(nv))

    zip_buf.seek(0)
    return zip_buf.read()
