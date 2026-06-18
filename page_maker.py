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


async def scrape_images(url: str) -> dict:
    """상품 페이지에서 이미지 URL 목록과 텍스트 추출"""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return {"error": str(e), "images": [], "title": "", "description": ""}

    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # 제목
    title = ""
    for sel in ["h1", "h2", ".goods_name", ".product_name", "#goods_name", ".prd-name", "#prdName"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break

    # 상세페이지 이미지: 상세 영역 우선, 없으면 전체 페이지 img 태그
    detail_selectors = [
        "#prdDetail", ".prdDetailArea", ".goods_description",
        "#goods_detail", ".detail_content", ".product_detail",
        ".prd-detail", "#detail_image", ".detail_image",
        "[id*='detail']", "[class*='detail']",
    ]
    detail_el = None
    for sel in detail_selectors:
        el = soup.select_one(sel)
        if el:
            detail_el = el
            break

    search_root = detail_el if detail_el else soup

    images = []
    seen = set()
    for img in search_root.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = base + src
        elif not src.startswith("http"):
            src = urljoin(url, src)

        # 너무 작은 이미지(아이콘 등) 필터
        w = int(img.get("width") or 0)
        h = int(img.get("height") or 0)
        if (w and w < 100) or (h and h < 100):
            continue
        if src not in seen and _is_content_image(src):
            seen.add(src)
            images.append(src)

    # description 텍스트
    desc_el = soup.select_one(".goods_description, #prdDetail, .detail_content")
    description = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

    return {"images": images, "title": title, "description": description, "error": ""}


def _is_content_image(src: str) -> bool:
    low = src.lower()
    skip = ["banner", "logo", "icon", "blank", "spacer", "pixel", "tracking",
            "button", "bullet", "arrow", "badge", ".gif"]
    return not any(k in low for k in skip)


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
