"""
상세페이지 메이커 — 이미지 스크래핑 + AI 카피 생성 + Pillow 레이아웃 합성
"""
import base64
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Optional, AsyncGenerator
from urllib.parse import urljoin, urlparse

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup

GEMINI_IMAGE_MODEL  = "gemini-2.5-flash-image"
GEMINI_VISION_MODEL = "gemini-2.5-flash"
GEMINI_BASE         = "https://generativelanguage.googleapis.com/v1beta/models"

SECTIONS = [
    {"key": "01_헤더",    "name": "헤더 배너",  "aspect": "9:16"},
    {"key": "02_특징요약", "name": "핵심 특징",  "aspect": "3:4"},
    {"key": "03_특징1",   "name": "특징 상세1", "aspect": "3:4"},
    {"key": "04_특징2",   "name": "특징 상세2", "aspect": "3:4"},
    {"key": "05_특징3",   "name": "특징 상세3", "aspect": "3:4"},
    {"key": "06_스펙",    "name": "상세 스펙",  "aspect": "3:4"},
    {"key": "07_사용법",  "name": "사용 방법",  "aspect": "3:4"},
    {"key": "08_CTA",     "name": "구매 유도",  "aspect": "3:4"},
]

def _hex_rgb(h: str, fallback=(44, 95, 138)) -> tuple:
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def _parse_features(raw: str) -> list:
    return [l.lstrip("•·*-– 1234567890.\t").strip() for l in raw.split("\n") if l.strip()]


def _parse_spec_rows(raw: str) -> list:
    rows = []
    for line in raw.split("\n"):
        for sep in (":", "：", " - "):
            if sep in line:
                k, v = line.split(sep, 1)
                rows.append((k.strip(), v.strip()))
                break
    return rows[:8]


def _parse_steps(raw: str) -> list:
    return [l.lstrip("1234567890. \t").strip() for l in raw.split("\n") if l.strip()][:4]


def _section_prompt(section_key: str, p: dict) -> str:
    name       = p.get("product_name", "product")
    main_color = p.get("main_color", "#2C5F8A")
    sub_color  = p.get("sub_color", "#F0F4F8")
    background = p.get("background", "clean white")
    mood       = p.get("mood", "modern, trustworthy")

    base = f"""Create a VISUAL BACKGROUND IMAGE for a Korean e-commerce product detail page.
⚠️ ABSOLUTE RULE: Do NOT include ANY text, letters, words, numbers, or writing anywhere in the image.
Only generate: product photos, decorative icons/graphics, color blocks, gradients, shapes, layouts.
Leave white/clean zones where Korean text will be overlaid later.

Product: {name} | Accent color: {main_color} | Secondary: {sub_color}
Background style: {background} | Mood: {mood}
Use the attached product photo as visual reference."""

    if "01_헤더" in section_key:
        return base + """

LAYOUT (9:16 tall banner):
- Full product photo centered, dramatic lighting
- Top 28%: dark semi-transparent overlay zone — keep completely clean (for title text)
- Bottom 18%: main accent color solid band — keep completely clean (for tagline)
- Edges: main color gradient vignette"""

    if "02_특징요약" in section_key:
        return base + """

LAYOUT (3:4 feature grid):
- Top 15%: pure white clean zone (for section title text)
- Remaining 85%: 2×2 grid of rounded card shapes with main accent color backgrounds
- Each card: simple relevant icon/symbol graphic ONLY (shield, ruler, lock, star) — absolutely no text
- Clean white background behind grid"""

    if "03_특징1" in section_key:
        return base + """

LAYOUT (3:4 feature detail):
- Top 15%: main accent color full-width solid band — completely clean (for title)
- Center: product photo with clean backdrop, good lighting
- Bottom 30%: white clean zone with very subtle horizontal lines (for bullet text)"""

    if "04_특징2" in section_key:
        return base + """

LAYOUT (3:4 feature detail):
- Top 15%: main accent color full-width solid band — completely clean (for title)
- Center: product shown from different angle
- Bottom 30%: white clean zone (for bullet text)"""

    if "05_특징3" in section_key:
        return base + """

LAYOUT (3:4 feature detail):
- Top 15%: main accent color full-width solid band — completely clean (for title)
- Center: product in lifestyle/use context
- Bottom 30%: white clean zone (for bullet text)"""

    if "06_스펙" in section_key:
        return base + """

LAYOUT (3:4 spec sheet):
- Top 13%: main accent color header band — completely clean (for "제품 스펙" title)
- Right 40%: product photo, clean shot
- Left 55% bottom half: white clean zone with 6 horizontal divider lines equally spaced
- Overall: clean, informational"""

    if "07_사용법" in section_key:
        return base + """

LAYOUT (3:4 usage steps):
- Top 12%: white clean zone (for "사용 방법" title)
- 4 equal quadrants: each with product-in-use scene illustration
- Bottom of each quadrant: white clean strip (for step text)
- Step number circles (①②③④) as decorative graphics only"""

    if "08_CTA" in section_key:
        return base + """

LAYOUT (3:4 call to action):
- Upper 38%: 3 circular icon graphics in a row (quality shield, gear, delivery truck) in main color — no text
- Middle 32%: white clean zone (for CTA button and headline)
- Bottom 30%: main accent color band (for benefit chips)"""

    return base

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

FONT_DIR    = Path(__file__).parent / "fonts"
FONT_REG    = FONT_DIR / "NanumGothic.ttf"
FONT_BOLD   = FONT_DIR / "NanumGothicBold.ttf"

# 지원 폰트 목록 (key: UI 표시명, value: (regular_file, bold_file))
FONT_CATALOG = {
    "Pretendard": ("Pretendard.ttf",       "PretendardBold.ttf"),
    "나눔고딕":   ("NanumGothic.ttf",      "NanumGothicBold.ttf"),
    "나눔명조":   ("NanumMyeongjo.ttf",     "NanumMyeongjoBold.ttf"),
    "블랙한산스": ("BlackHanSans.ttf",      "BlackHanSans.ttf"),
    "도현":       ("DoHyeon.ttf",           "DoHyeon.ttf"),
    "주아":       ("Jua.ttf",               "Jua.ttf"),
}

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

_active_font_family: str = ""   # 현재 섹션에 적용할 글씨체

def _font(size: int, bold: bool = False, family: str = "") -> ImageFont.FreeTypeFont:
    fam = family or _active_font_family
    if fam and fam in FONT_CATALOG:
        reg_f, bold_f = FONT_CATALOG[fam]
        fname = bold_f if bold else reg_f
        path = FONT_DIR / fname
    else:
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


# ── Gemini AI 상세페이지 생성 ─────────────────────────────────────────

def _ov_wrap(d, text, font, x, y, max_w, fill, line_gap=5):
    """텍스트 줄바꿈 helper (overlay draw 전용)"""
    cur = ""
    for ch in text:
        if _text_w(d, cur + ch, font) > max_w and cur:
            d.text((x, y), cur, font=font, fill=fill)
            y += _text_h(d, cur, font) + line_gap
            cur = ch
        else:
            cur += ch
    if cur:
        d.text((x, y), cur, font=font, fill=fill)
        y += _text_h(d, cur, font) + line_gap
    return y


def _ov_center(d, text, font, y, W, fill):
    tw = _text_w(d, text, font)
    d.text(((W - tw) // 2, y), text, font=font, fill=fill)
    return y + _text_h(d, text, font)


def _ov_header(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    name = p.get("product_name", "상품명")
    category = p.get("category", "")
    feats = _parse_features(p.get("key_features", ""))
    tagline = feats[0][:20] if feats else name[:20]

    top_h = int(H * 0.28)
    bot_h = int(H * 0.18)
    pad = W // 18

    d.rectangle([0, 0, W, top_h], fill=(10, 10, 10, 170))
    d.rectangle([0, H - bot_h, W, H], fill=(*mc, 220))

    fc = _font(W // 24)
    d.text((pad, pad), category, font=fc, fill=(200, 200, 200, 255))

    fn = _font(W // 10, bold=True)
    y = pad + _text_h(d, category, fc) + pad // 2
    _ov_wrap(d, name, fn, pad, y, W - pad * 2, fill=(255, 255, 255, 255), line_gap=6)

    ft = _font(W // 14, bold=True)
    tw = _text_w(d, tagline, ft)
    ty = H - bot_h + (bot_h - _text_h(d, tagline, ft)) // 2
    d.text(((W - tw) // 2 + 2, ty + 2), tagline, font=ft, fill=(0, 0, 0, 80))
    d.text(((W - tw) // 2, ty), tagline, font=ft, fill=(255, 255, 255, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_features(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    feats = _parse_features(p.get("key_features", ""))[:4]
    while len(feats) < 4:
        feats.append("특징")

    title_h = int(H * 0.15)
    pad = W // 25

    d.rectangle([0, 0, W, title_h], fill=(255, 255, 255, 220))
    d.rectangle([0, title_h - 4, W, title_h], fill=(*mc, 255))

    ft = _font(W // 11, bold=True)
    _ov_center(d, "핵심 특징", ft, (title_h - _text_h(d, "핵심 특징", ft)) // 2, W, (*mc, 255))

    box_w = (W - pad * 3) // 2
    box_h = (H - title_h - pad * 3) // 2
    fb = _font(W // 17, bold=True)
    fd = _font(W // 23)

    for i, feat in enumerate(feats):
        col = i % 2
        row = i // 2
        bx = pad + col * (box_w + pad)
        by = title_h + pad + row * (box_h + pad)

        d.rounded_rectangle([bx, by, bx + box_w, by + box_h], radius=14,
                             fill=(255, 255, 255, 210))
        d.rounded_rectangle([bx, by, bx + box_w, by + box_h], radius=14,
                             outline=(*mc, 160), width=2)

        words = feat.split()
        title_part = " ".join(words[:3]) if len(words) >= 3 else feat[:14]
        desc_part = " ".join(words[3:]) if len(words) > 3 else ""

        ty = by + box_h // 3
        ttw = _text_w(d, title_part, fb)
        d.text((bx + (box_w - ttw) // 2, ty), title_part, font=fb, fill=(*mc, 255))

        if desc_part:
            ty2 = ty + _text_h(d, title_part, fb) + 8
            _ov_wrap(d, desc_part, fd, bx + pad, ty2, box_w - pad * 2,
                     fill=(60, 60, 60, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_feature_detail(img, W, H, p, idx):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    feats = _parse_features(p.get("key_features", ""))
    feat = feats[idx] if idx < len(feats) else "특징"
    bullets = [f for j, f in enumerate(feats) if j != idx][:3]
    if not bullets:
        bullets = [feat]

    title_h = int(H * 0.15)
    bot_h = int(H * 0.30)
    pad = W // 20

    d.rectangle([0, 0, W, title_h], fill=(*mc, 230))

    ft = _font(W // 12, bold=True)
    words = feat.split()
    title_text = " ".join(words[:5]) if len(words) > 5 else feat
    ty = (title_h - _text_h(d, title_text, ft)) // 2
    d.text((pad + 2, ty + 2), title_text, font=ft, fill=(0, 0, 0, 80))
    d.text((pad, ty), title_text, font=ft, fill=(255, 255, 255, 255))

    d.rectangle([0, H - bot_h, W, H], fill=(255, 255, 255, 225))

    fd = _font(W // 22)
    dot_r = W // 45
    by = H - bot_h + pad

    for bullet in bullets[:3]:
        cy = by + dot_r
        d.ellipse([pad, cy - dot_r, pad + dot_r * 2, cy + dot_r], fill=(*mc, 255))
        bx = pad + dot_r * 2 + 10
        by = _ov_wrap(d, bullet, fd, bx, by, W - bx - pad, fill=(40, 40, 40, 255))
        by += pad // 3

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_specs(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    rows = _parse_spec_rows(p.get("specs", ""))
    if not rows:
        rows = [(f"특징 {i+1}", f) for i, f in enumerate(_parse_features(p.get("key_features", ""))[:5])]

    title_h = int(H * 0.13)
    pad = W // 22

    d.rectangle([0, 0, W, title_h], fill=(*mc, 230))
    ft = _font(W // 12, bold=True)
    _ov_center(d, "제품 스펙", ft, (title_h - _text_h(d, "제품 스펙", ft)) // 2, W, (255, 255, 255, 255))

    table_y = int(H * 0.46)
    table_x = pad
    table_w = int(W * 0.88)

    d.rectangle([table_x, table_y, table_x + table_w, H - pad], fill=(255, 255, 255, 215))

    fk = _font(W // 20, bold=True)
    fv = _font(W // 22)
    n = min(len(rows), 6)
    row_h = max((H - pad - table_y) // n, 44) if n else 44
    key_col_w = int(table_w * 0.38)

    for i, (k, v) in enumerate(rows[:6]):
        ry = table_y + i * row_h
        if i % 2 == 1:
            d.rectangle([table_x, ry, table_x + table_w, ry + row_h], fill=(245, 245, 245, 180))
        d.line([table_x, ry, table_x + table_w, ry], fill=(210, 210, 210, 200), width=1)
        d.line([table_x + key_col_w, ry, table_x + key_col_w, ry + row_h],
               fill=(210, 210, 210, 200), width=1)
        vt = ry + (row_h - _text_h(d, k, fk)) // 2
        d.text((table_x + pad // 2, vt), k[:12], font=fk, fill=(*mc, 255))
        d.text((table_x + key_col_w + pad // 2, vt), v[:18], font=fv, fill=(50, 50, 50, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_usage(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    steps = _parse_steps(p.get("how_to_use", ""))
    if not steps:
        steps = _parse_features(p.get("key_features", ""))[:4]

    title_h = int(H * 0.12)
    pad = W // 22

    d.rectangle([0, 0, W, title_h], fill=(255, 255, 255, 225))
    d.rectangle([0, title_h - 3, W, title_h], fill=(*mc, 255))
    ft = _font(W // 11, bold=True)
    _ov_center(d, "사용 방법", ft, (title_h - _text_h(d, "사용 방법", ft)) // 2, W, (*mc, 255))

    cell_w = W // 2
    cell_h = (H - title_h) // 2
    fn = _font(W // 20, bold=True)
    fstep = _font(W // 26)

    for i, step in enumerate(steps[:4]):
        col = i % 2
        row = i // 2
        cx = col * cell_w
        cy = title_h + row * cell_h

        r = W // 22
        cmx = cx + cell_w // 2
        cmy = cy + cell_h // 4
        d.ellipse([cmx - r, cmy - r, cmx + r, cmy + r], fill=(*mc, 230))
        num = str(i + 1)
        nw = _text_w(d, num, fn)
        nh = _text_h(d, num, fn)
        d.text((cmx - nw // 2, cmy - nh // 2), num, font=fn, fill=(255, 255, 255, 255))

        text_y = cy + cell_h * 2 // 3
        d.rectangle([cx + 4, text_y, cx + cell_w - 4, cy + cell_h - 4],
                    fill=(255, 255, 255, 210))

        short = step[:22]
        _ov_center(d, short, fstep, text_y + 6, cx + cell_w,
                   (40, 40, 40, 255) if False else (40, 40, 40, 255))
        # centered within cell
        sw = _text_w(d, short, fstep)
        d.text((cx + (cell_w - sw) // 2, text_y + 6), short, font=fstep, fill=(40, 40, 40, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_cta(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#C85A2D"))
    feats = _parse_features(p.get("key_features", ""))
    name = p.get("product_name", "상품")

    pad = W // 20
    mid_h = int(H * 0.40)
    cta_h = int(H * 0.70)

    d.rectangle([0, mid_h, W, cta_h], fill=(255, 255, 255, 235))

    fh = _font(W // 11, bold=True)
    headline = "지금 바로 확인하세요!"
    _ov_center(d, headline, fh, mid_h + pad, W, (30, 30, 30, 255))

    fs = _font(W // 19)
    sub = f"{name[:14]} — 품질 보장"
    _ov_center(d, sub, fs, mid_h + pad + _text_h(d, headline, fh) + 12, W, (80, 80, 80, 255))

    btn_w = int(W * 0.68)
    btn_h = int(H * 0.09)
    btn_x = (W - btn_w) // 2
    btn_y = int(H * 0.56)
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h],
                         radius=btn_h // 2, fill=(*mc, 255))
    fb = _font(W // 13, bold=True)
    btn_text = "지금 구매하기"
    btw = _text_w(d, btn_text, fb)
    bth = _text_h(d, btn_text, fb)
    d.text((btn_x + (btn_w - btw) // 2, btn_y + (btn_h - bth) // 2),
           btn_text, font=fb, fill=(255, 255, 255, 255))

    d.rectangle([0, cta_h, W, H], fill=(*mc, 200))
    fc2 = _font(W // 21, bold=True)
    chip_h = int((H - cta_h) * 0.5)
    chip_y = cta_h + (H - cta_h - chip_h) // 2
    chips = [f[:8] for f in feats[:3]]
    if chips:
        chip_w = (W - pad * (len(chips) + 1)) // len(chips)
        for i, chip in enumerate(chips):
            chx = pad + i * (chip_w + pad)
            d.rounded_rectangle([chx, chip_y, chx + chip_w, chip_y + chip_h],
                                 radius=8, fill=(255, 255, 255, 60))
            ctw = _text_w(d, chip, fc2)
            d.text((chx + (chip_w - ctw) // 2,
                    chip_y + (chip_h - _text_h(d, chip, fc2)) // 2),
                   chip, font=fc2, fill=(255, 255, 255, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _overlay_text_on_image(section: dict, img_bytes: bytes, product: dict) -> bytes:
    """Gemini 생성 이미지 위에 정확한 한글 텍스트를 Pillow로 오버레이"""
    global _active_font_family
    _active_font_family = product.get("font_family", "")

    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    W, H = img.size
    key = section["key"]

    if "01_헤더" in key:
        out = _ov_header(img, W, H, product)
    elif "02_특징요약" in key:
        out = _ov_features(img, W, H, product)
    elif "03_특징1" in key:
        out = _ov_feature_detail(img, W, H, product, 0)
    elif "04_특징2" in key:
        out = _ov_feature_detail(img, W, H, product, 1)
    elif "05_특징3" in key:
        out = _ov_feature_detail(img, W, H, product, 2)
    elif "06_스펙" in key:
        out = _ov_specs(img, W, H, product)
    elif "07_사용법" in key:
        out = _ov_usage(img, W, H, product)
    elif "08_CTA" in key:
        out = _ov_cta(img, W, H, product)
    else:
        out = img.convert("RGB")

    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _gemini_headers() -> dict:
    key = os.getenv("GEMINI_API_KEY", "")
    return {"Content-Type": "application/json", "x-goog-api-key": key}


def _extract_json_images(obj, out: list, depth: int = 0):
    """JSON 트리에서 이미지 URL 재귀 추출"""
    if depth > 12:
        return
    if isinstance(obj, str):
        if obj.startswith("http") and any(x in obj for x in
                ('.jpg', '.jpeg', '.png', '.webp', 'phinf', '/image', 'cdn', 'img')):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_json_images(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _extract_json_images(item, out, depth + 1)


async def _run_gemini_analysis(title: str, page_text: str,
                               images_to_send: list, rep_image: str) -> dict:
    """제목 + 텍스트 + 이미지들 → Gemini 종합 분석 → 마케팅 카피 dict"""
    prompt = f"""당신은 한국 이커머스 마케팅 전문가입니다.
아래 상품 페이지 정보와 첨부된 이미지들을 종합 분석하여 고품질 상세페이지용 마케팅 카피를 개발해주세요.

⚠️ 중요: 이미지 안에 적힌 모든 텍스트(마케팅 문구, 스펙, 특징 설명 등)를 꼼꼼히 읽고 활용하세요.

페이지 제목: {title}
페이지 텍스트: {page_text[:1500] if page_text else "(없음)"}

아래 JSON 형식으로만 출력하라. 코드블록 없이 JSON만.
실제 상품 정보를 바탕으로 구체적이고 설득력 있는 한국어 마케팅 카피를 작성할 것.
key_features는 이미지에서 읽은 실제 특징과 장점을 마케팅 언어로 발전시켜 작성.

{{
  "product_name": "정확한 상품명",
  "category": "카테고리",
  "key_features": "• 특징1 (구체적 수치/소재 포함)\n• 특징2\n• 특징3\n• 특징4",
  "specs": "항목: 값\n항목: 값\n항목: 값",
  "how_to_use": "1. 사용법1\n2. 사용법2\n3. 사용법3\n4. 사용법4",
  "target_customer": "타겟 고객 설명 (구체적으로)",
  "main_color": "#XXXXXX",
  "sub_color": "#XXXXXX",
  "background": "배경 스타일",
  "mood": "분위기 키워드",
  "font_style": "폰트 스타일"
}}"""

    parts = [{"text": prompt}]
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for img_url in images_to_send:
            try:
                ir = await client.get(img_url)
                if ir.status_code == 200 and len(ir.content) > 5000:
                    ct = ir.headers.get("content-type", "image/jpeg").split(";")[0]
                    if ct.startswith("image/"):
                        parts.append({"inline_data": {
                            "mime_type": ct,
                            "data": base64.b64encode(ir.content).decode(),
                        }})
            except Exception:
                continue

    body = {"contents": [{"parts": parts}]}
    api_url = f"{GEMINI_BASE}/{GEMINI_VISION_MODEL}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(api_url, json=body, headers=_gemini_headers())
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"```[a-z]*", "", text).strip("`\n ")
            result = json.loads(text)
            result["_rep_image"] = rep_image
            return result
    except Exception as e:
        return {"error": f"분석 실패: {e}", "product_name": title, "_rep_image": rep_image}


async def _fetch_smartstore_data(product_url: str) -> tuple:
    """SmartStore URL → (title, page_text, main_imgs, detail_imgs)
    방법1: HTML + __NEXT_DATA__ 파싱 (우선)
    방법2: Naver 내부 API 폴백
    """
    import re as _re
    m = _re.search(r'/products?/(\d+)', product_url)
    product_no = m.group(1) if m else ""

    title, page_text, main_imgs, detail_imgs = "", "", [], []

    naver_headers = {
        **HEADERS,
        "Referer": "https://smartstore.naver.com/",
        "Origin": "https://smartstore.naver.com",
    }

    TEXT_KEYS = {
        'productName', 'name', 'description', 'detailContents', 'content',
        'htmlContent', 'imageAltText', 'representativeImageContent',
        'productDescription', 'shortDescription', 'catchphrase',
    }

    def _collect_texts(obj, acc, depth=0):
        if depth > 15: return
        if isinstance(obj, str) and len(obj.strip()) > 5:
            clean = re.sub(r'<[^>]+>', ' ', obj).strip()
            if clean:
                acc.append(clean)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in TEXT_KEYS:
                    _collect_texts(v, acc, depth + 1)
                elif isinstance(v, (dict, list)):
                    _collect_texts(v, acc, depth + 1)
        elif isinstance(obj, list):
            for x in obj[:20]:
                _collect_texts(x, acc, depth + 1)

    # ── 방법 1: HTML 직접 파싱 (__NEXT_DATA__) ───────────────────────
    try:
        async with httpx.AsyncClient(headers=naver_headers, timeout=25, follow_redirects=True) as client:
            r = await client.get(product_url)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")

                og_t = soup.find("meta", property="og:title")
                if og_t and og_t.get("content"):
                    title = og_t["content"].split(" : ")[0].strip()

                og_img = soup.find("meta", property="og:image")
                if og_img and og_img.get("content"):
                    u = og_img["content"]
                    if u.startswith("http"):
                        main_imgs.append(u)

                next_tag = soup.find("script", id="__NEXT_DATA__")
                if next_tag:
                    nd = json.loads(next_tag.string or "{}")
                    tmp_imgs: list = []
                    _extract_json_images(nd, tmp_imgs)
                    for u in tmp_imgs:
                        if _is_content_image(u) and u not in detail_imgs and u not in main_imgs:
                            detail_imgs.append(u)

                    acc: list = []
                    _collect_texts(nd, acc)
                    if acc:
                        if not title:
                            title = acc[0]
                        page_text = " ".join(acc)[:2000]
    except Exception:
        pass

    # ── 방법 2: Naver 내부 API 폴백 ──────────────────────────────────
    if (not page_text or not detail_imgs) and product_no:
        api_urls = [
            f"https://smartstore.naver.com/i/v1/products/{product_no}",
            f"https://smartstore.naver.com/i/v2/products/{product_no}",
        ]
        async with httpx.AsyncClient(headers=naver_headers, timeout=20, follow_redirects=True) as client:
            for api_url in api_urls:
                try:
                    r = await client.get(api_url)
                    if r.status_code == 200:
                        d = r.json()
                        tmp_imgs2: list = []
                        _extract_json_images(d, tmp_imgs2)
                        for u in tmp_imgs2:
                            if _is_content_image(u) and u not in detail_imgs and u not in main_imgs:
                                detail_imgs.append(u)
                        if not page_text:
                            acc2: list = []
                            _collect_texts(d, acc2)
                            if acc2:
                                if not title:
                                    title = acc2[0]
                                page_text = " ".join(acc2)[:2000]
                        break
                except Exception:
                    continue

    return title, page_text, main_imgs[:2], detail_imgs[:8]


async def _fetch_coupang_data(product_url: str) -> tuple:
    """Coupang URL → (title, page_text, main_imgs, detail_imgs)
    브라우저 헤더로 HTML 파싱 + script JSON 추출
    """
    title, page_text, main_imgs, detail_imgs = "", "", [], []

    coupang_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }

    try:
        async with httpx.AsyncClient(headers=coupang_headers, timeout=30, follow_redirects=True) as client:
            r = await client.get(product_url)
            if r.status_code != 200:
                return title, page_text, main_imgs, detail_imgs

            soup = BeautifulSoup(r.text, "html.parser")

            # 제목 추출
            for sel in ["h2.prod-buy-header__title", "#productTitle",
                        ".prod-title", "h1.prod-buy-header__title", ".prod-buy-header__title"]:
                el = soup.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title:
                og = soup.find("meta", property="og:title")
                if og and og.get("content"):
                    title = og["content"].split("|")[0].split(" - ")[0].strip()

            # og:image
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                main_imgs.append(og_img["content"])

            # script 태그에서 이미지 URL + 텍스트 추출
            for script in soup.find_all("script"):
                text = script.string or ""
                if len(text) < 100:
                    continue

                # 이미지 URL 패턴
                img_urls = re.findall(
                    r'["\'](https?://[^\'"]+\.(?:jpg|jpeg|png|webp)(?:\?[^\'"]*)?)["\']', text
                )
                for u in img_urls:
                    if not _is_content_image(u):
                        continue
                    if "coupangcdn" in u or "coupang" in u:
                        if u not in main_imgs and u not in detail_imgs:
                            if any(k in u for k in ("thumbnail", "itemimage", "medium", "large")):
                                main_imgs.append(u)
                            else:
                                detail_imgs.append(u)

                # 제품명
                if not title:
                    nm = re.search(r'"(?:productName|displayName|vendorItemName)"\s*:\s*"([^"]+)"', text)
                    if nm:
                        title = nm.group(1)

                # 설명 텍스트
                if not page_text:
                    for pat in [
                        r'"(?:description|htmlContent|detailContent|sellerSpecificContent)"\s*:\s*"([^"]{20,})"',
                        r'"(?:content|vendorItemName)"\s*:\s*"([^"]{10,})"',
                    ]:
                        parts = re.findall(pat, text)
                        if parts:
                            clean = [re.sub(r'<[^>]+>', ' ', p).strip() for p in parts[:10]]
                            page_text = " ".join(clean)[:2000]
                            break

            # HTML 메인 이미지
            if len(main_imgs) < 3:
                for sel in [".prod-image__detail img", ".thumbnail-image img",
                            "#repImage img", ".item-img img",
                            ".prod-image-container img", ".product-image-thumbnail img"]:
                    for img in soup.select(sel):
                        src = img.get("src") or img.get("data-src") or ""
                        if not src or src.startswith("data:"):
                            continue
                        if src.startswith("//"):
                            src = "https:" + src
                        if src not in main_imgs and _is_content_image(src):
                            main_imgs.append(src)

            # HTML 상세 이미지
            for sel in [".prod-description", "#productDescription",
                        ".prod-seller-description", "[class*='description']"]:
                el = soup.select_one(sel)
                if not el:
                    continue
                for img in el.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if not src or src.startswith("data:"):
                        continue
                    if src.startswith("//"):
                        src = "https:" + src
                    if src not in detail_imgs and src not in main_imgs and _is_content_image(src):
                        detail_imgs.append(src)

            # 텍스트 폴백
            if not page_text:
                for sel in [".prod-description", ".prod-seller-description",
                            ".product-description", "#itemDescription"]:
                    el = soup.select_one(sel)
                    if el:
                        page_text = el.get_text(" ", strip=True)[:2000]
                        break

    except Exception:
        pass

    return title, page_text, main_imgs[:3], detail_imgs[:8]


async def scrape_and_analyze_url(product_url: str) -> dict:
    """상품 URL → 페이지 텍스트 + 이미지들 스크래핑 → Gemini 종합 분석"""

    is_smartstore = any(d in product_url for d in ("smartstore.naver.com", "brand.naver.com"))
    is_coupang    = "coupang.com" in product_url

    # ── SmartStore ──────────────────────────────────────────────────
    if is_smartstore:
        ss_title, ss_text, ss_main, ss_detail = await _fetch_smartstore_data(product_url)
        if not ss_title and not ss_text and not ss_main and not ss_detail:
            return {"error": "스마트스토어 상품 정보를 가져오지 못했습니다. URL을 확인해주세요."}
        rep_image = ss_main[0] if ss_main else (ss_detail[0] if ss_detail else "")
        images_to_send = (ss_main[:1] + ss_detail[:4])[:5]
        return await _run_gemini_analysis(ss_title, ss_text, images_to_send, rep_image)

    # ── Coupang ─────────────────────────────────────────────────────
    if is_coupang:
        cp_title, cp_text, cp_main, cp_detail = await _fetch_coupang_data(product_url)
        if not cp_title and not cp_text and not cp_main and not cp_detail:
            return {"error": "쿠팡 상품 정보를 가져오지 못했습니다. URL을 확인해주세요."}
        rep_image = cp_main[0] if cp_main else (cp_detail[0] if cp_detail else "")
        images_to_send = (cp_main[:1] + cp_detail[:4])[:5]
        return await _run_gemini_analysis(cp_title, cp_text, images_to_send, rep_image)

    # ── 1. 일반 사이트 페이지 스크래핑 ────────────────────────────
    html = ""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=25, follow_redirects=True) as client:
            r = await client.get(product_url)
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return {"error": f"페이지 접근 실패: {e}"}

    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(product_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # ── 2. 제목 추출 ────────────────────────────────────────────────
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].split(" - ")[0].strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        t = soup.find("title")
        if t:
            title = t.get_text(strip=True).split(" - ")[0].strip()

    # ── 3. 페이지 설명 텍스트 추출 ──────────────────────────────────
    page_text = ""
    # SmartStore: __NEXT_DATA__ JSON 파싱
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    json_imgs = []
    if next_data_tag:
        try:
            nd = json.loads(next_data_tag.string or "{}")
            # 재귀적으로 이미지 URL 수집
            _extract_json_images(nd, json_imgs)
            # 텍스트 추출: productName, description 등
            def _find_texts(obj, keys, depth=0):
                if depth > 10 or not isinstance(obj, dict):
                    return {}
                result = {}
                for k, v in obj.items():
                    if k in keys and isinstance(v, str) and v:
                        result[k] = v
                    elif isinstance(v, (dict, list)):
                        result.update(_find_texts(v if isinstance(v, dict) else
                                                  {str(i): x for i, x in enumerate(v)},
                                                  keys, depth + 1))
                return result
            found = _find_texts(nd, {"productName", "detailContents", "description",
                                     "content", "productDescription"})
            page_text = " ".join(str(v) for v in found.values())[:2000]
        except Exception:
            pass

    # HTML 텍스트 (SmartStore 아니거나 텍스트 부족할 때)
    if len(page_text) < 100:
        for sel in ["#prdDetail", ".prdDetailArea", ".goods_description",
                    "#goods_detail", ".detail_content", ".product_detail",
                    ".se-main-container", "[class*='detail']", "article", "main"]:
            el = soup.select_one(sel)
            if el:
                page_text = el.get_text(" ", strip=True)[:2000]
                break

    # ── 4. 이미지 수집 ──────────────────────────────────────────────
    def _norm(src):
        if not src or src.startswith("data:"):
            return ""
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            return base + src
        return src

    main_imgs, detail_imgs, seen = [], [], set()

    # og:image 가장 대표 이미지
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        u = _norm(og_img["content"])
        if u:
            main_imgs.append(u)
            seen.add(u)

    # SmartStore JSON에서 수집한 이미지
    for u in json_imgs:
        u = _norm(u)
        if u and u not in seen and _is_content_image(u):
            seen.add(u)
            detail_imgs.append(u)

    # ① 메인 이미지 영역 (cafe24 / 일반 쇼핑몰)
    for sel in ["#mainImage", ".goods_img_wrap", ".keyImg", "#prdImgWrap",
                ".thumb_wrap", ".thumbnail_wrap", ".goods_thumbnails"]:
        el = soup.select_one(sel)
        if el:
            for img in el.find_all("img"):
                u = _norm(_get_img_src(img, base, product_url))
                if u and u not in seen and _is_content_image(u):
                    seen.add(u)
                    main_imgs.append(u)

    # cafe24 /product/big/ 이미지
    for img in soup.find_all("img"):
        src = _get_img_src(img, base, product_url)
        u = _norm(src)
        if u and "/product/big/" in u and u not in seen:
            seen.add(u)
            main_imgs.append(u)

    # ② 상세 이미지 영역
    for sel in ["#prdDetail", ".prdDetailArea", ".goods_description",
                "#goods_detail", ".detail_content", ".product_detail",
                ".se-main-container", ".smartstore-detail"]:
        el = soup.select_one(sel)
        if el:
            for img in el.find_all("img"):
                u = _norm(_get_img_src(img, base, product_url))
                if u and u not in seen and _is_content_image(u):
                    seen.add(u)
                    detail_imgs.append(u)
            break

    # ③ ec-data-src (cafe24)
    for img in soup.find_all("img", attrs={"ec-data-src": True}):
        u = _norm(_get_img_src(img, base, product_url))
        if u and u not in seen and _is_content_image(u):
            seen.add(u)
            detail_imgs.append(u)

    # ④ 폴백: 전체 스캔
    if not main_imgs and not detail_imgs:
        for img in soup.find_all("img"):
            u = _norm(_get_img_src(img, base, product_url))
            if not u or not _is_content_image(u) or u in seen:
                continue
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if (w and w < 100) or (h and h < 100):
                continue
            seen.add(u)
            detail_imgs.append(u)

    # 대표 이미지 (이미지 생성 시 시각 참조용)
    rep_image = main_imgs[0] if main_imgs else (detail_imgs[0] if detail_imgs else "")

    # ── 5. Gemini 종합 분석 ──────────────────────────────────────────
    images_to_send = (main_imgs[:1] + detail_imgs[:4])[:5]
    return await _run_gemini_analysis(title, page_text, images_to_send, rep_image)


async def analyze_product_image(image_url: str) -> dict:
    """단일 이미지 분석 (하위 호환용)"""
    return await scrape_and_analyze_url(image_url) if not image_url.startswith("http") \
        else await _analyze_single_image(image_url)


async def _analyze_single_image(image_url: str) -> dict:
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            img_b64 = base64.b64encode(r.content).decode()
            ct = r.headers.get("content-type", "image/jpeg").split(";")[0]
    except Exception as e:
        return {"error": f"이미지 다운로드 실패: {e}"}

    prompt = """상품 이미지를 분석하여 아래 JSON만 출력하라 (코드블록 없이).
{"product_name":"","category":"","key_features":"• \n• \n• \n• ","specs":"","how_to_use":"1. \n2. \n3. ","target_customer":"","main_color":"#2C5F8A","sub_color":"#F0F4F8","background":"깔끔한 화이트","mood":"모던, 신뢰감 있는","font_style":"굵은 고딕체"}"""

    body = {"contents": [{"parts": [{"text": prompt},
                                     {"inline_data": {"mime_type": ct, "data": img_b64}}]}]}
    url = f"{GEMINI_BASE}/{GEMINI_VISION_MODEL}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body, headers=_gemini_headers())
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"```[a-z]*", "", text).strip("`\n ")
            return json.loads(text)
    except Exception as e:
        return {"error": f"분석 실패: {e}", "product_name": ""}


async def _generate_one_section(section: dict, product: dict, img_b64: str, mime: str) -> bytes:
    """섹션 하나 Gemini 이미지 생성 → JPEG bytes"""
    prompt = _section_prompt(section["key"], product)
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": img_b64}},
        ]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": section["aspect"]},
        },
    }
    url = f"{GEMINI_BASE}/{GEMINI_IMAGE_MODEL}:generateContent"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=body, headers=_gemini_headers())
        resp.raise_for_status()
        data = resp.json()
        for part in data["candidates"][0]["content"]["parts"]:
            if "inlineData" in part:
                raw = base64.b64decode(part["inlineData"]["data"])
                return _overlay_text_on_image(section, raw, product)
    raise ValueError("이미지 데이터 없음")


async def stream_ai_sections(
    image_url: str,
    product_data: dict,
) -> AsyncGenerator[dict, None]:
    """각 섹션 순서대로 생성 → {key, name, image_b64, error} dict yield"""
    # 상품 이미지 다운로드 → base64
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            img_b64 = base64.b64encode(r.content).decode()
            mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
    except Exception as e:
        yield {"key": "error", "name": "오류", "error": str(e)}
        return

    for section in SECTIONS:
        try:
            img_bytes = await _generate_one_section(section, product_data, img_b64, mime)
            yield {
                "key":      section["key"],
                "name":     section["name"],
                "image_b64": base64.b64encode(img_bytes).decode(),
            }
        except Exception as e:
            yield {
                "key":   section["key"],
                "name":  section["name"],
                "error": str(e),
            }
