"""
상세페이지 메이커 — 이미지 스크래핑 + AI 카피 생성 + Pillow 레이아웃 합성
"""
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

FONT_DIR    = Path(__file__).parent / "fonts"
FONT_REG    = FONT_DIR / "NanumGothic.ttf"
FONT_BOLD   = FONT_DIR / "NanumGothicBold.ttf"

COUPANG_MAIN   = (1000, 1000)
COUPANG_DETAIL = 1000
NAVER_MAIN     = (1000, 1000)
NAVER_DETAIL   = 860

COLOR_SCHEMES = {
    "warm":    {"bg": (255, 248, 240), "banner": (200, 90,  45),  "accent": (200, 90,  45),  "text": (40, 25, 10),  "white": (255, 248, 240)},
    "cool":    {"bg": (238, 244, 255), "banner": (48,  90,  180), "accent": (48,  90,  180), "text": (15, 25, 55),  "white": (238, 244, 255)},
    "neutral": {"bg": (248, 246, 242), "banner": (90,  75,  60),  "accent": (90,  75,  60),  "text": (35, 30, 25),  "white": (248, 246, 242)},
    "green":   {"bg": (240, 250, 242), "banner": (38,  130, 75),  "accent": (38,  130, 75),  "text": (15, 45, 25),  "white": (240, 250, 242)},
}


# ── Font helpers ─────────────────────────────────────────────────────

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _text_h(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, font, y: int,
                   canvas_w: int, color, shadow=False):
    x = (canvas_w - _text_w(draw, text, font)) // 2
    if shadow:
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 60))
    draw.text((x, y), text, font=font, fill=color)
    return _text_h(draw, text, font)


def _draw_wrapped(draw: ImageDraw.ImageDraw, text: str, font, x: int, y: int,
                  max_w: int, color, line_gap: int = 8) -> int:
    """줄바꿈하며 텍스트 그리기 → 마지막 y 반환"""
    lines, cur = [], ""
    for ch in text:
        if _text_w(draw, cur + ch, font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    for line in lines:
        draw.text((x, y), line, font=font, fill=color)
        y += _text_h(draw, line, font) + line_gap
    return y


# ── AI copy generation ───────────────────────────────────────────────

async def generate_product_copy(title: str, description: str = "") -> dict:
    """Claude Haiku로 제품 마케팅 카피 생성"""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _default_copy(title)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""상품명: {title}
{("상세정보: " + description[:200]) if description else ""}

한국 온라인 쇼핑몰 상세페이지에 넣을 마케팅 카피를 작성하세요.
JSON만 반환 (코드블록 없이):
{{
  "headline": "강렬한 헤드라인 최대 16자",
  "tagline": "핵심 가치를 담은 한 줄 최대 26자",
  "features": [
    {{"icon": "이모지", "title": "특징명 5자이내", "desc": "설명 최대 14자"}},
    {{"icon": "이모지", "title": "특징명 5자이내", "desc": "설명 최대 14자"}},
    {{"icon": "이모지", "title": "특징명 5자이내", "desc": "설명 최대 14자"}}
  ],
  "cta": "구매 유도 문구 최대 12자",
  "color_scheme": "warm 또는 cool 또는 neutral 또는 green 중 제품에 적합한 것"
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"[pagemaker] copy gen: {e}")

    return _default_copy(title)


def _default_copy(title: str) -> dict:
    return {
        "headline": (title[:14] + "…") if len(title) > 14 else title or "프리미엄 주방용품",
        "tagline": "품질로 증명하는 업소용 필수템",
        "features": [
            {"icon": "✅", "title": "고품질 소재", "desc": "내구성 검증된 재질"},
            {"icon": "🏪", "title": "업소용 규격", "desc": "위생 기준 충족"},
            {"icon": "💰", "title": "합리적 가격", "desc": "가성비 최강"},
        ],
        "cta": "지금 바로 구매",
        "color_scheme": "neutral",
    }


# ── Detail page composition ──────────────────────────────────────────

def _section_hero(W: int, copy: dict, sc: dict) -> Image.Image:
    """섹션 1: 컬러 배너 + 헤드라인 + 태그라인"""
    H = 300
    img = Image.new("RGB", (W, H), sc["banner"])
    draw = ImageDraw.Draw(img)

    # 그라디언트 효과 (아래로 살짝 밝아짐)
    for y in range(H):
        t = y / H
        r = int(sc["banner"][0] + (20 * t))
        g = int(sc["banner"][1] + (15 * t))
        b = int(sc["banner"][2] + (10 * t))
        draw.line([(0, y), (W, y)], fill=(min(r,255), min(g,255), min(b,255)))

    f_head = _font(58, bold=True)
    f_tag  = _font(30)

    _draw_centered(draw, copy["headline"], f_head, 80, W, (255, 255, 255), shadow=True)
    _draw_centered(draw, copy["tagline"],  f_tag,  165, W, (255, 255, 255, 200))

    # 하단 장식 라인
    lw = 80
    lx = (W - lw) // 2
    draw.rectangle([lx, 230, lx + lw, 233], fill=(255, 255, 255, 120))

    return img


def _section_product(W: int, product_img: Image.Image, copy: dict, sc: dict) -> Image.Image:
    """섹션 2: 제품 이미지 + 특징 목록"""
    pad = 40
    img_area = W // 2 - pad
    H = max(520, img_area + pad * 2)

    bg = Image.new("RGB", (W, H), sc["bg"])
    draw = ImageDraw.Draw(bg)

    # 제품 이미지 (왼쪽)
    thumb = product_img.copy().convert("RGB")
    thumb.thumbnail((img_area, img_area), Image.LANCZOS)
    ix = pad
    iy = (H - thumb.height) // 2
    bg.paste(thumb, (ix, iy))

    # 오른쪽 텍스트 영역
    tx = W // 2 + pad // 2
    ty = 60
    right_w = W // 2 - pad - pad // 2

    f_feat_title = _font(26, bold=True)
    f_feat_desc  = _font(22)
    f_cta        = _font(24, bold=True)

    # 구분선
    draw.rectangle([W // 2 - 1, 40, W // 2, H - 40], fill=(220, 220, 220))

    for feat in copy.get("features", []):
        icon  = feat.get("icon", "•")
        title = feat.get("title", "")
        desc  = feat.get("desc", "")

        # 아이콘 배경 동그라미
        r = 22
        draw.ellipse([tx, ty, tx + r*2, ty + r*2], fill=sc["accent"])
        draw.text((tx + 6, ty + 4), icon, font=_font(20), fill=(255,255,255))

        draw.text((tx + r*2 + 14, ty + 2),  title, font=f_feat_title, fill=sc["text"])
        draw.text((tx + r*2 + 14, ty + 32), desc,  font=f_feat_desc,  fill=(100, 100, 100))
        ty += 90

    # CTA 버튼
    ty += 10
    cta_text = copy.get("cta", "지금 바로 구매")
    btn_w = _text_w(draw, cta_text, f_cta) + 60
    btn_h = 52
    bx = tx
    draw.rounded_rectangle([bx, ty, bx + btn_w, ty + btn_h], radius=26, fill=sc["accent"])
    draw.text((bx + 30, ty + 12), cta_text, font=f_cta, fill=(255, 255, 255))

    return bg


def _section_features(W: int, copy: dict, sc: dict) -> Image.Image:
    """섹션 3: 3컬럼 특징 카드"""
    H = 240
    bg = Image.new("RGB", (W, H), sc["accent"])
    draw = ImageDraw.Draw(bg)

    features = copy.get("features", [])[:3]
    col_w = W // max(len(features), 1)
    f_icon  = _font(38)
    f_title = _font(22, bold=True)
    f_desc  = _font(18)

    for i, feat in enumerate(features):
        cx = col_w * i + col_w // 2
        # 세로 구분선
        if i > 0:
            draw.line([(col_w * i, 30), (col_w * i, H - 30)], fill=(255,255,255,80), width=1)

        draw.text((cx - _text_w(draw, feat["icon"], f_icon)//2, 30),
                  feat["icon"], font=f_icon, fill=(255,255,255))
        draw.text((cx - _text_w(draw, feat["title"], f_title)//2, 90),
                  feat["title"], font=f_title, fill=(255,255,255))
        _draw_wrapped(draw, feat["desc"], f_desc,
                      cx - col_w//2 + 20, 128, col_w - 40, (255,255,255,180))

    return bg


def _section_footer(W: int, logo: Optional[Image.Image], sc: dict) -> Image.Image:
    """섹션 4: 브랜드 푸터"""
    H = 160
    bg = Image.new("RGB", (W, H), sc["bg"])
    draw = ImageDraw.Draw(bg)

    # 상단 구분선
    draw.line([(60, 0), (W - 60, 0)], fill=(200, 200, 200), width=1)

    if logo:
        logo_h = 80
        ratio = logo_h / logo.height
        logo_w = int(logo.width * ratio)
        logo_r = logo.resize((logo_w, logo_h), Image.LANCZOS).convert("RGBA")
        lx = (W - logo_w) // 2
        ly = (H - logo_h) // 2
        bg.paste(logo_r, (lx, ly), logo_r)
    else:
        f = _font(28, bold=True)
        draw.text(((W - _text_w(draw, "부자주방", f)) // 2, 60),
                  "부자주방", font=f, fill=sc["accent"])

    return bg


def compose_detail_page(
    product_imgs: list,
    copy: dict,
    logo: Optional[Image.Image],
) -> Image.Image:
    """제품 이미지 + 카피로 상세페이지 스타일 합성"""
    W  = 1000
    sc = COLOR_SCHEMES.get(copy.get("color_scheme", "neutral"), COLOR_SCHEMES["neutral"])

    sections = [_section_hero(W, copy, sc)]

    for img in product_imgs:
        sections.append(_section_product(W, img, copy, sc))

    sections.append(_section_features(W, copy, sc))
    sections.append(_section_footer(W, logo, sc))

    total_h = sum(s.height for s in sections)
    canvas = Image.new("RGB", (W, total_h), sc["bg"])
    y = 0
    for s in sections:
        canvas.paste(s, (0, y))
        y += s.height

    return canvas


# ── Scraping ─────────────────────────────────────────────────────────

def _get_img_src(img, base: str, page_url: str) -> str:
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
    skip = ["logo", "blank", "spacer", "pixel", "tracking", ".gif",
            "layout/", "close_btn", "naver_pay", "btn_count", "btn_coupon",
            "btn_price", "ico_pay", "ico_under", "star5",
            "skin/base", "skin/admin", "echosting"]
    return not any(k in low for k in skip)


async def scrape_images(url: str) -> dict:
    """상품 페이지에서 이미지 URL + 제목 추출 (cafe24 최적화)"""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return {"error": str(e), "images": [], "title": "", "description": ""}

    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # 제목: og:title > <title> > DOM
    title = ""
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        title = og["content"].split(" - ")[0].strip()
    if not title:
        t = soup.select_one("title")
        if t:
            title = t.get_text(strip=True).split(" - ")[0].strip()
    if not title:
        for sel in ["#prdName", ".goods_name", ".product_name", "#goods_name", ".prd-name"]:
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

    # ① 메인 이미지 영역
    for sel in ["#mainImage", ".goods_img_wrap", ".keyImg", ".thumb_wrap",
                ".thumbnail_wrap", ".goods_thumbnails", ".prdImgWrap", "#prdImgWrap"]:
        el = soup.select_one(sel)
        if el:
            for img in el.find_all("img"):
                _add(_get_img_src(img, base, url))
            if images:
                break

    # cafe24 product/big 직접 추출
    for img in soup.find_all("img"):
        src = _get_img_src(img, base, url)
        if src and "/product/big/" in src:
            _add(src)

    # ② 상세 이미지 영역
    for sel in ["#prdDetail", ".prdDetailArea", ".goods_description",
                "#goods_detail", ".detail_content", ".product_detail", ".prd-detail"]:
        el = soup.select_one(sel)
        if el:
            for img in el.find_all("img"):
                _add(_get_img_src(img, base, url))
            break

    # cafe24 ec-data-src 직접 추출
    for img in soup.find_all("img", attrs={"ec-data-src": True}):
        _add(_get_img_src(img, base, url))

    # ③ 폴백: 전체 스캔
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

    desc_el = soup.select_one(".goods_description, #prdDetail, .detail_content")
    description = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

    return {"images": images, "title": title, "description": description, "error": ""}


# ── Image processing helpers ─────────────────────────────────────────

def _open_image(data: bytes) -> Optional[Image.Image]:
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


# ── Logo detection & replacement ─────────────────────────────────────

def _ncc_search(base: np.ndarray, tmpl: np.ndarray) -> tuple:
    """Normalized cross-correlation — 스트라이드 기반 템플릿 매칭, (x, y, score) 반환"""
    bh, bw = base.shape
    th, tw = tmpl.shape
    if th > bh or tw > bw:
        return (0, 0, -1.0)

    # 템플릿 정규화
    t = tmpl.astype(np.float32) - tmpl.mean()
    denom_t = np.sqrt((t ** 2).sum()) + 1e-8
    t /= denom_t

    stride = max(1, min(th, tw) // 8)
    best_score, best_x, best_y = -1.0, 0, 0

    for y in range(0, bh - th + 1, stride):
        for x in range(0, bw - tw + 1, stride):
            patch = base[y:y + th, x:x + tw].astype(np.float32)
            p = patch - patch.mean()
            denom_p = np.sqrt((p ** 2).sum()) + 1e-8
            score = float((p / denom_p * t).sum())
            if score > best_score:
                best_score, best_x, best_y = score, x, y

    # 정밀 탐색
    if stride > 1:
        for y in range(max(0, best_y - stride), min(bh - th + 1, best_y + stride + 1)):
            for x in range(max(0, best_x - stride), min(bw - tw + 1, best_x + stride + 1)):
                patch = base[y:y + th, x:x + tw].astype(np.float32)
                p = patch - patch.mean()
                denom_p = np.sqrt((p ** 2).sum()) + 1e-8
                score = float((p / denom_p * t).sum())
                if score > best_score:
                    best_score, best_x, best_y = score, x, y

    return (best_x, best_y, best_score)


def find_logo_region(
    base_img: Image.Image,
    template_img: Image.Image,
    threshold: float = 0.52,
) -> Optional[tuple]:
    """
    base_img 에서 template_img 와 가장 비슷한 영역 탐색.
    여러 스케일을 시도해 가장 높은 매칭 점수의 (x, y, w, h) 반환.
    threshold 미만이면 None.
    """
    base_gray = np.array(base_img.convert("L"))
    tmpl_orig = template_img.convert("L")
    ow, oh = tmpl_orig.size

    best_score, best_result = threshold, None

    for scale in [0.6, 0.75, 0.9, 1.0, 1.15, 1.35]:
        tw = max(12, int(ow * scale))
        th = max(12, int(oh * scale))
        tmpl = np.array(tmpl_orig.resize((tw, th), Image.LANCZOS))
        x, y, score = _ncc_search(base_gray, tmpl)
        if score > best_score:
            best_score = score
            best_result = (x, y, tw, th)

    return best_result


def _sample_bg_color(img: Image.Image, x: int, y: int, w: int, h: int) -> tuple:
    """로고 영역 주변 픽셀의 평균 색상 추출"""
    iw, ih = img.size
    margin = max(6, min(w, h) // 4)
    rgba = img.convert("RGBA")
    samples = []

    def sample(px, py):
        if 0 <= px < iw and 0 <= py < ih:
            samples.append(rgba.getpixel((px, py))[:3])

    for px in range(max(0, x - margin), min(iw, x + w + margin)):
        sample(px, y - margin)
        sample(px, y + h + margin)
    for py in range(max(0, y - margin), min(ih, y + h + margin)):
        sample(x - margin, py)
        sample(x + w + margin, py)

    if not samples:
        return (255, 255, 255, 255)
    r = int(sum(s[0] for s in samples) / len(samples))
    g = int(sum(s[1] for s in samples) / len(samples))
    b = int(sum(s[2] for s in samples) / len(samples))
    return (r, g, b, 255)


def replace_logo(
    base_img: Image.Image,
    region: tuple,           # (x, y, w, h)
    new_logo: Optional[Image.Image],
    keep_position: bool = True,
) -> Image.Image:
    """
    region 을 배경색으로 지우고, 같은 위치에 new_logo 배치.
    new_logo=None 이면 지우기만.
    """
    img = base_img.copy().convert("RGBA")
    x, y, w, h = region

    bg = _sample_bg_color(img, x, y, w, h)
    draw = ImageDraw.Draw(img)
    draw.rectangle([x, y, x + w, y + h], fill=bg)

    if new_logo and keep_position:
        logo_r = new_logo.convert("RGBA").resize((w, h), Image.LANCZOS)
        img.paste(logo_r, (x, y), logo_r)

    return img


def _place_logo(base_img: Image.Image, logo: Image.Image,
                position: str, size_pct: float) -> Image.Image:
    bw, bh = base_img.size
    logo_w = max(40, int(bw * size_pct))
    logo_h = int(logo.height * (logo_w / logo.width))
    logo_r = logo.resize((logo_w, logo_h), Image.LANCZOS)
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
    result.paste(logo_r, (x, y), logo_r)
    return result


def _resize_square(img: Image.Image, size: tuple) -> Image.Image:
    rgb = img.convert("RGB")
    rgb.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(rgb, ((size[0] - rgb.width) // 2, (size[1] - rgb.height) // 2))
    return canvas


def _resize_width(img: Image.Image, width: int) -> Image.Image:
    if img.width <= 0:
        return img
    new_h = int(img.height * width / img.width)
    return img.convert("RGB").resize((width, new_h), Image.LANCZOS)


def _to_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ── Main zip builder ─────────────────────────────────────────────────

async def build_processed_zip(
    image_urls: list[str],
    logo_bytes: Optional[bytes],
    logo_position: str,
    logo_size_pct: float,
    is_main: list[bool],
    use_ai: bool = False,
    product_title: str = "",
    product_desc: str = "",
    remove_logo_bytes: Optional[bytes] = None,
) -> bytes:
    """이미지 다운로드 → (AI 합성 or 로고) → 플랫폼별 리사이즈 → zip"""

    logo = None
    if logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            pass

    # 이미지 다운로드
    downloaded: list[tuple[Image.Image, bool]] = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for url, im in zip(image_urls, is_main):
            try:
                r = await client.get(url)
                r.raise_for_status()
                img = _open_image(r.content)
                if img:
                    downloaded.append((img, im))
            except Exception:
                pass

    # 제거할 로고 템플릿
    remove_tmpl = None
    if remove_logo_bytes:
        try:
            remove_tmpl = Image.open(io.BytesIO(remove_logo_bytes)).convert("RGBA")
        except Exception:
            pass

    def _process_img(img: Image.Image) -> Image.Image:
        """로고 제거 → 우리 로고 삽입"""
        if remove_tmpl:
            region = find_logo_region(img, remove_tmpl)
            if region:
                img = replace_logo(img, region, logo)
                return img  # 로고 제거 후 같은 위치에 삽입했으므로 추가 삽입 불필요
        if logo:
            img = _place_logo(img, logo, logo_position, logo_size_pct)
        return img

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:

        if use_ai and downloaded:
            # AI 카피 생성
            copy = await generate_product_copy(product_title, product_desc)

            # 메인 이미지
            main_imgs = [img for img, im in downloaded if im]
            if not main_imgs:
                main_imgs = [downloaded[0][0]]

            for i, img in enumerate(main_imgs):
                out = _process_img(img.copy())
                fname = f"{i+1:02d}"
                zf.writestr(f"coupang/main_{fname}.jpg", _to_bytes(_resize_square(out, COUPANG_MAIN)))
                zf.writestr(f"naver/main_{fname}.jpg",   _to_bytes(_resize_square(out, NAVER_MAIN)))

            # 상세 이미지: AI 합성
            detail_imgs = [img for img, im in downloaded if not im] or [img for img, _ in downloaded]
            composed = compose_detail_page(detail_imgs, copy, logo)
            zf.writestr("coupang/detail_page.jpg", _to_bytes(_resize_width(composed, COUPANG_DETAIL)))
            zf.writestr("naver/detail_page.jpg",   _to_bytes(_resize_width(composed, NAVER_DETAIL)))

        else:
            # 기본 모드: 로고 제거(옵션) + 로고 삽입
            for idx, (img, im) in enumerate(downloaded):
                out = _process_img(img.copy())
                fname = f"{idx+1:02d}"
                if im:
                    zf.writestr(f"coupang/main_{fname}.jpg",   _to_bytes(_resize_square(out, COUPANG_MAIN)))
                    zf.writestr(f"naver/main_{fname}.jpg",     _to_bytes(_resize_square(out, NAVER_MAIN)))
                else:
                    zf.writestr(f"coupang/detail_{fname}.jpg", _to_bytes(_resize_width(out, COUPANG_DETAIL)))
                    zf.writestr(f"naver/detail_{fname}.jpg",   _to_bytes(_resize_width(out, NAVER_DETAIL)))

    zip_buf.seek(0)
    return zip_buf.read()
