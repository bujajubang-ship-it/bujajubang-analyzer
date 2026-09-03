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
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from bs4 import BeautifulSoup

GEMINI_IMAGE_MODEL  = "gemini-2.5-flash-image"
GEMINI_VISION_MODEL = "gemini-2.5-flash"
GEMINI_BASE         = "https://generativelanguage.googleapis.com/v1beta/models"

# 분석·카피는 Claude Opus 4.8 (한국어 마케팅·상품이해 최상)
CLAUDE_ANALYSIS_MODEL = "claude-opus-4-8"


async def _claude_vision_json(prompt: str, images: list, max_tokens: int = 3000) -> dict:
    """프롬프트 + 이미지(URL 또는 (mime,b64)) → Claude Opus 4.8 Vision → JSON dict.
    images: list of either str(url) or tuple(mime, b64).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY 미설정"}
    content = [{"type": "text", "text": prompt}]
    # 이미지 다운로드/첨부 (최대 8장, Claude 한도 고려)
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for img in images[:8]:
            try:
                if isinstance(img, tuple):
                    mime, b64 = img
                else:
                    ir = await client.get(img)
                    if ir.status_code != 200 or len(ir.content) < 5000:
                        continue
                    mime = ir.headers.get("content-type", "image/jpeg").split(";")[0]
                    if not mime.startswith("image/"):
                        continue
                    b64 = base64.b64encode(ir.content).decode()
                if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    mime = "image/jpeg"
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": b64}})
            except Exception:
                continue
    body = {
        "model": CLAUDE_ANALYSIS_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages",
                                 json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    text = re.sub(r"```[a-z]*", "", text).strip("`\n ")
    # JSON 본문만 안전 추출
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        text = text[s:e + 1]
    return json.loads(text)

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
FONT_REG    = FONT_DIR / "Pretendard.ttf"       # 부자주방 기본 글씨체
FONT_BOLD   = FONT_DIR / "PretendardBold.ttf"

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
            model=CLAUDE_ANALYSIS_MODEL,
            max_tokens=1200,
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

def _clean(text: str) -> str:
    """마크다운 기호(**, ##, *) 및 불필요한 특수문자 제거"""
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s*', '', text)
    return text.strip(" :—-")


def _chip_label(feat: str) -> str:
    """CTA 칩용 짧은 레이블 — 콜론/대시 앞 첫 4단어"""
    clean = _clean(feat)
    # 콜론이나 대시 앞까지 자르기
    for sep in (':', '—', ' - ', ': '):
        if sep in clean:
            clean = clean.split(sep)[0].strip()
            break
    words = clean.split()
    return " ".join(words[:4]) if words else clean[:12]


def _fit_font(d, text: str, max_w: int, start: int, bold: bool = False, min_sz: int = 16):
    """텍스트가 max_w 안에 들어오도록 폰트 크기 자동 축소"""
    sz = start
    while sz >= min_sz:
        f = _font(sz, bold)
        if _text_w(d, text, f) <= max_w:
            return f
        sz -= 2
    return _font(min_sz, bold)


def _ov_wrap(d, text, font, x, y, max_w, max_h, fill, line_gap=4):
    """줄바꿈 + 박스 높이 초과 시 클립"""
    line_h = _text_h(d, "가", font) + line_gap
    cur, used = "", 0
    for ch in text:
        if _text_w(d, cur + ch, font) > max_w and cur:
            if used + line_h > max_h:
                break
            d.text((x, y + used), cur, font=font, fill=fill)
            used += line_h
            cur = ch
        else:
            cur += ch
    if cur and used + line_h <= max_h:
        d.text((x, y + used), cur, font=font, fill=fill)
        used += line_h
    return y + used


def _ov_center(d, text, font, y, W, fill):
    tw = _text_w(d, text, font)
    d.text(((W - tw) // 2, y), text, font=font, fill=fill)
    return y + _text_h(d, text, font)


def _ov_header(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    name = _clean(p.get("product_name", "상품명"))
    category = _clean(p.get("category", ""))
    feats = [_clean(f) for f in _parse_features(p.get("key_features", ""))]
    tagline = _chip_label(feats[0]) if feats else name

    top_h = int(H * 0.26)
    bot_h = int(H * 0.16)
    pad = W // 16

    # 상단 어두운 오버레이 (제목 영역)
    d.rectangle([0, 0, W, top_h], fill=(8, 8, 8, 175))
    # 하단 색상 밴드 (태그라인 영역)
    d.rectangle([0, H - bot_h, W, H], fill=(*mc, 225))

    # 카테고리
    fc = _font(W // 26)
    d.text((pad, pad), category[:20], font=fc, fill=(200, 200, 200, 220))

    # 상품명 (폰트 자동 축소 + 줄바꿈)
    fn_size = W // 9
    fn = _fit_font(d, name, W - pad * 2, fn_size, bold=True, min_sz=W // 16)
    y0 = pad + _text_h(d, "가", fc) + pad // 2
    _ov_wrap(d, name, fn, pad, y0, W - pad * 2, top_h - y0 - pad // 2,
             fill=(255, 255, 255, 255), line_gap=6)

    # 태그라인
    tl = tagline[:24]
    ft = _fit_font(d, tl, W - pad * 2, W // 13, bold=True, min_sz=W // 18)
    tw = _text_w(d, tl, ft)
    ty = H - bot_h + (bot_h - _text_h(d, tl, ft)) // 2
    d.text(((W - tw) // 2 + 2, ty + 2), tl, font=ft, fill=(0, 0, 0, 70))
    d.text(((W - tw) // 2, ty), tl, font=ft, fill=(255, 255, 255, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_features(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    raw_feats = _parse_features(p.get("key_features", ""))[:4]
    feats = [_clean(f) for f in raw_feats]
    while len(feats) < 4:
        feats.append("특징")

    title_h = int(H * 0.13)
    pad = W // 22

    # 타이틀 바
    d.rectangle([0, 0, W, title_h], fill=(*mc, 240))
    ft = _fit_font(d, "핵심 특징", W - pad * 2, W // 10, bold=True)
    _ov_center(d, "핵심 특징", ft, (title_h - _text_h(d, "핵심 특징", ft)) // 2, W, (255, 255, 255, 255))

    # 2x2 카드 그리드
    gap = pad
    box_w = (W - gap * 3) // 2
    box_h = (H - title_h - gap * 3) // 2

    for i, feat in enumerate(feats):
        col, row = i % 2, i // 2
        bx = gap + col * (box_w + gap)
        by = title_h + gap + row * (box_h + gap)

        # 카드 배경
        d.rounded_rectangle([bx, by, bx + box_w, by + box_h],
                             radius=12, fill=(255, 255, 255, 215))
        d.rounded_rectangle([bx, by, bx + box_w, by + box_h],
                             radius=12, outline=(*mc, 140), width=2)

        # 제목 라인 (카드 상단 25% 영역)
        title_zone_h = int(box_h * 0.42)
        text_pad = pad // 2
        inner_w = box_w - text_pad * 2

        # 첫 줄을 굵게, 나머지는 보통으로
        words = feat.split()
        first_line = " ".join(words[:4]) if len(words) >= 4 else feat
        rest_line  = " ".join(words[4:]) if len(words) > 4 else ""

        fb = _fit_font(d, first_line, inner_w, W // 16, bold=True, min_sz=W // 24)
        ty0 = by + text_pad + int(box_h * 0.12)
        lh  = _text_h(d, first_line, fb)
        flx = bx + (box_w - _text_w(d, first_line, fb)) // 2
        d.text((flx, ty0), first_line, font=fb, fill=(*mc, 255))

        if rest_line:
            fd = _fit_font(d, rest_line, inner_w, W // 20, min_sz=W // 28)
            ty1 = ty0 + lh + 6
            max_rest_h = by + box_h - ty1 - text_pad
            _ov_wrap(d, rest_line, fd, bx + text_pad, ty1,
                     inner_w, max_rest_h, fill=(55, 55, 55, 230), line_gap=4)

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_feature_detail(img, W, H, p, idx):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    feats = [_clean(f) for f in _parse_features(p.get("key_features", ""))]
    feat = feats[idx] if idx < len(feats) else "특징"
    bullets = [f for j, f in enumerate(feats) if j != idx][:3]
    if not bullets:
        bullets = [feat]

    title_h = int(H * 0.14)
    bot_h   = int(H * 0.40)   # 패널 더 크게
    pad     = W // 18

    # 상단 색상 바 — 제목
    d.rectangle([0, 0, W, title_h], fill=(*mc, 235))
    # 제목 최대 두 단어로 제한, 자동 축소
    title_words = feat.split()
    title_text = " ".join(title_words[:6]) if len(title_words) > 6 else feat
    ft = _fit_font(d, title_text, W - pad * 2, W // 11, bold=True, min_sz=W // 18)
    ty = (title_h - _text_h(d, title_text, ft)) // 2
    shadow_color = (0, 0, 0, 70)
    d.text((pad + 2, ty + 2), title_text, font=ft, fill=shadow_color)
    d.text((pad, ty), title_text, font=ft, fill=(255, 255, 255, 255))

    # 하단 흰색 패널 — 불릿
    d.rectangle([0, H - bot_h, W, H], fill=(255, 255, 255, 230))

    fd   = _font(W // 21)
    dot_r = max(6, W // 42)
    by   = H - bot_h + pad
    lh   = _text_h(d, "가", fd) + 5

    for bullet in bullets[:3]:
        if by + lh > H - pad // 2:
            break
        cy = by + dot_r
        d.ellipse([pad, cy - dot_r, pad + dot_r * 2, cy + dot_r], fill=(*mc, 255))
        bx  = pad + dot_r * 2 + 10
        avail_h = H - pad // 2 - by
        by = _ov_wrap(d, bullet, fd, bx, by, W - bx - pad,
                      avail_h, fill=(35, 35, 35, 240), line_gap=4)
        by += pad // 4

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_specs(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    rows = [(_clean(k), _clean(v)) for k, v in _parse_spec_rows(p.get("specs", ""))]
    if not rows:
        rows = [(f"특징 {i+1}", _clean(f)) for i, f in
                enumerate(_parse_features(p.get("key_features", ""))[:6])]

    title_h = int(H * 0.12)
    pad     = W // 20

    # 타이틀 바
    d.rectangle([0, 0, W, title_h], fill=(*mc, 235))
    ft = _fit_font(d, "제품 스펙", W - pad * 2, W // 10, bold=True)
    _ov_center(d, "제품 스펙", ft, (title_h - _text_h(d, "제품 스펙", ft)) // 2,
               W, (255, 255, 255, 255))

    # 테이블
    table_y  = int(H * 0.44)
    table_x  = pad
    table_w  = W - pad * 2
    table_h  = H - table_y - pad

    d.rounded_rectangle([table_x, table_y, table_x + table_w, H - pad],
                        radius=10, fill=(255, 255, 255, 220))

    n = min(len(rows), 7)
    if n == 0:
        return Image.alpha_composite(img, ov).convert("RGB")

    row_h    = max(table_h // n, 40)
    key_w    = int(table_w * 0.36)
    val_w    = table_w - key_w

    fk = _font(W // 22, bold=True)
    fv = _font(W // 23)

    for i, (k, v) in enumerate(rows[:n]):
        ry = table_y + i * row_h
        if i % 2 == 1:
            d.rectangle([table_x, ry, table_x + table_w, ry + row_h],
                        fill=(245, 247, 250, 150))
        d.line([table_x + 4, ry, table_x + table_w - 4, ry],
               fill=(210, 215, 220, 180), width=1)
        d.line([table_x + key_w, ry, table_x + key_w, ry + row_h],
               fill=(210, 215, 220, 180), width=1)

        # 키 — 자동 축소
        fk_fit = _fit_font(d, k, key_w - pad, W // 22, bold=True, min_sz=W // 30)
        vt = ry + (row_h - _text_h(d, k, fk_fit)) // 2
        d.text((table_x + pad // 2, vt), k, font=fk_fit, fill=(*mc, 240))

        # 값 — 자동 축소
        fv_fit = _fit_font(d, v, val_w - pad, W // 23, min_sz=W // 30)
        vt2 = ry + (row_h - _text_h(d, v, fv_fit)) // 2
        d.text((table_x + key_w + pad // 2, vt2), v, font=fv_fit, fill=(45, 45, 45, 240))

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_usage(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    steps = [_clean(s) for s in _parse_steps(p.get("how_to_use", ""))]
    if not steps:
        steps = [_clean(f) for f in _parse_features(p.get("key_features", ""))[:4]]

    title_h = int(H * 0.11)
    pad     = W // 20

    # 타이틀 바
    d.rectangle([0, 0, W, title_h], fill=(255, 255, 255, 230))
    d.rectangle([0, title_h - 3, W, title_h], fill=(*mc, 255))
    ft = _fit_font(d, "사용 방법", W - pad * 2, W // 10, bold=True)
    _ov_center(d, "사용 방법", ft, (title_h - _text_h(d, "사용 방법", ft)) // 2,
               W, (*mc, 255))

    # 2x2 셀 그리드
    cell_w = W // 2
    cell_h = (H - title_h) // 2
    circ_r = W // 20
    fn = _font(W // 18, bold=True)

    for i, step in enumerate(steps[:4]):
        col, row = i % 2, i // 2
        cx0 = col * cell_w      # 셀 좌측 x
        cy0 = title_h + row * cell_h   # 셀 상단 y

        # 원 + 숫자
        cmx = cx0 + cell_w // 2
        cmy = cy0 + int(cell_h * 0.28)
        d.ellipse([cmx - circ_r, cmy - circ_r, cmx + circ_r, cmy + circ_r],
                  fill=(*mc, 235))
        num = str(i + 1)
        nw  = _text_w(d, num, fn)
        nh  = _text_h(d, num, fn)
        d.text((cmx - nw // 2, cmy - nh // 2), num, font=fn,
               fill=(255, 255, 255, 255))

        # 텍스트 박스
        tb_margin = pad // 2
        tx0  = cx0 + tb_margin
        tx1  = cx0 + cell_w - tb_margin
        ty0  = cy0 + int(cell_h * 0.54)
        ty1  = cy0 + cell_h - tb_margin
        box_w_inner = tx1 - tx0
        box_h_inner = ty1 - ty0

        d.rounded_rectangle([tx0, ty0, tx1, ty1],
                            radius=8, fill=(255, 255, 255, 215))

        fs = _fit_font(d, step, box_w_inner - pad // 2, W // 24, min_sz=W // 32)
        _ov_wrap(d, step, fs, tx0 + pad // 4, ty0 + pad // 4,
                 box_w_inner - pad // 2, box_h_inner - pad // 2,
                 fill=(35, 35, 35, 240), line_gap=3)

    return Image.alpha_composite(img, ov).convert("RGB")


def _ov_cta(img, W, H, p):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    mc = _hex_rgb(p.get("main_color", "#2C5F8A"))
    feats = _parse_features(p.get("key_features", ""))
    name  = _clean(p.get("product_name", "상품"))

    pad    = W // 18
    mid_h  = int(H * 0.38)
    cta_h  = int(H * 0.72)

    # 흰 패널 (헤드라인 + 버튼 영역)
    d.rectangle([0, mid_h, W, cta_h], fill=(255, 255, 255, 240))

    # 헤드라인
    headline = "지금 바로 확인하세요!"
    fh = _fit_font(d, headline, W - pad * 2, W // 10, bold=True)
    _ov_center(d, headline, fh, mid_h + pad, W, (25, 25, 25, 255))

    # 서브텍스트 (상품명)
    sub = name[:20] if len(name) <= 20 else name[:18] + "…"
    fs_sub = _fit_font(d, sub, W - pad * 2, W // 17, min_sz=W // 24)
    sub_y  = mid_h + pad + _text_h(d, headline, fh) + 10
    _ov_center(d, sub, fs_sub, sub_y, W, (75, 75, 75, 240))

    # 구매 버튼
    btn_w = int(W * 0.70)
    btn_h = int(H * 0.085)
    btn_x = (W - btn_w) // 2
    btn_y = int(H * 0.565)
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h],
                        radius=btn_h // 2, fill=(*mc, 255))
    fb = _fit_font(d, "지금 구매하기", btn_w - pad, W // 12, bold=True)
    btw = _text_w(d, "지금 구매하기", fb)
    bth = _text_h(d, "지금 구매하기", fb)
    d.text((btn_x + (btn_w - btw) // 2, btn_y + (btn_h - bth) // 2),
           "지금 구매하기", font=fb, fill=(255, 255, 255, 255))

    # 하단 색상 밴드 — 특징 칩 (가독성 위주로 개선)
    d.rectangle([0, cta_h, W, H], fill=(*mc, 210))
    chip_area_h = H - cta_h
    # 칩 레이블: 콜론/대시 앞 첫 4단어만 추출
    chip_labels = [_chip_label(f) for f in feats[:3]]
    n_chips = len(chip_labels)
    if n_chips == 0:
        return Image.alpha_composite(img, ov).convert("RGB")

    chip_h   = int(chip_area_h * 0.68)
    chip_y   = cta_h + (chip_area_h - chip_h) // 2
    chip_gap = pad // 2
    chip_w   = (W - chip_gap * (n_chips + 1)) // n_chips

    for i, label in enumerate(chip_labels):
        chx = chip_gap + i * (chip_w + chip_gap)
        d.rounded_rectangle([chx, chip_y, chx + chip_w, chip_y + chip_h],
                            radius=8, fill=(255, 255, 255, 55))

        inner_w = chip_w - pad // 2
        fc2 = _fit_font(d, label, inner_w, W // 18, bold=True, min_sz=W // 28)
        tw_c = _text_w(d, label, fc2)
        th_c = _text_h(d, label, fc2)

        if tw_c <= inner_w:
            d.text((chx + (chip_w - tw_c) // 2,
                    chip_y + (chip_h - th_c) // 2),
                   label, font=fc2, fill=(255, 255, 255, 255))
        else:
            # 단어 경계로 2줄 분할
            words = label.split()
            mid   = max(1, len(words) // 2)
            line1 = " ".join(words[:mid])
            line2 = " ".join(words[mid:])
            fl1   = _fit_font(d, line1, inner_w, W // 19, bold=True, min_sz=W // 28)
            fl2   = _fit_font(d, line2, inner_w, W // 19, bold=True, min_sz=W // 28)
            lh1   = _text_h(d, line1, fl1)
            lh2   = _text_h(d, line2, fl2)
            ty0   = chip_y + (chip_h - lh1 - 4 - lh2) // 2
            d.text((chx + (chip_w - _text_w(d, line1, fl1)) // 2, ty0),
                   line1, font=fl1, fill=(255, 255, 255, 255))
            d.text((chx + (chip_w - _text_w(d, line2, fl2)) // 2, ty0 + lh1 + 4),
                   line2, font=fl2, fill=(255, 255, 255, 255))

    return Image.alpha_composite(img, ov).convert("RGB")


# ── 부자주방 스타일 (흰 배경 + Pillow 전체 렌더링) ──────────────────

BJ_RED   = (215, 0, 16)   # #D70010 부자주방 로고/강조 빨강
BJ_REDDK = (170, 0, 12)   # 그라데이션/음영용 짙은 빨강
BJ_BLACK = (26, 26, 26)
BJ_GRAY  = (110, 110, 110)
BJ_LGRAY = (245, 245, 245)
BJ_WHITE = (255, 255, 255)
BJ_TINT  = (247, 248, 250)   # 섹션 배경 은은한 틴트
BJ_CARD  = (252, 252, 253)   # 제품샷 카드 배경
BJ_LINE  = (228, 230, 234)   # 미세 구분선


# ── 프리미엄 렌더링 헬퍼 (입체감) ────────────────────────────────────

def _bj_bg(W, H, tint=BJ_TINT):
    """은은한 세로 그라데이션 배경 캔버스 (흰색→틴트)"""
    canvas = Image.new("RGB", (W, H), BJ_WHITE)
    top = (255, 255, 255)
    for i in range(H):
        t = i / max(H - 1, 1)
        r = int(top[0] + (tint[0] - top[0]) * t)
        g = int(top[1] + (tint[1] - top[1]) * t)
        b = int(top[2] + (tint[2] - top[2]) * t)
        ImageDraw.Draw(canvas).line([(0, i), (W, i)], fill=(r, g, b))
    return canvas


def _round_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def _drop_shadow(canvas, box, radius=28, blur=22, alpha=70, dy=12):
    """box(x0,y0,x1,y1) 위치에 부드러운 드롭섀도우를 깐다."""
    x0, y0, x1, y1 = [int(v) for v in box]
    pad = blur * 3
    w, h = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [pad, pad, pad + (x1 - x0), pad + (y1 - y0)],
        radius=radius, fill=(20, 22, 28, alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(sh, (x0 - pad, y0 - pad + dy), sh)


def _cover_resize(img, w, h):
    """object-fit: cover — 비율 유지하며 (w,h)를 꽉 채우도록 확대 후 중앙 크롭."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    r = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return r.crop((left, top, left + w, top + h))


def _bj_card_img(canvas, img, x, y, max_w, max_h,
                 radius=30, pad=0, card_bg=BJ_CARD, shadow=True,
                 valign="top", fit="contain"):
    """제품 이미지를 라운드 카드 안에 그림자와 함께 배치. 실제 점유 (w,h,y_bottom) 반환.
    valign='center'면 max_h 영역 안에서 세로 중앙 배치.
    fit='cover'면 영역을 꽉 채우도록 크롭(풀블리드 히어로용)."""
    if not img or max_w <= 0 or max_h <= 0:
        return 0, 0, y
    base = img.copy().convert("RGB")
    if fit == "cover":
        thumb = _cover_resize(base, max_w - pad * 2, max_h - pad * 2)
    else:
        thumb = base
        thumb.thumbnail((max_w - pad * 2, max_h - pad * 2), Image.LANCZOS)
    cw, ch = thumb.width + pad * 2, thumb.height + pad * 2
    cx = x + (max_w - cw) // 2
    if valign == "center" and ch < max_h:
        y = y + (max_h - ch) // 2
    if shadow:
        _drop_shadow(canvas, (cx, y, cx + cw, y + ch),
                     radius=radius, blur=20, alpha=55, dy=10)
    if pad > 0:
        card = Image.new("RGB", (cw, ch), card_bg)
        card.paste(thumb, (pad, pad))
    else:
        card = thumb
    canvas.paste(card, (cx, y), _round_mask((cw, ch), radius))
    return cw, ch, y + ch


def _bj_eyebrow(d, W, y, text, color=BJ_RED, size=24):
    """작은 빨강 라벨 + 양옆 선 (섹션 상단 아이브로우)"""
    f = _font(size, bold=True)
    tw = _text_w(d, text, f)
    th = _text_h(d, text, f)
    cx = W // 2
    d.text((cx - tw // 2, y), text, font=f, fill=color)
    line_y = y + th // 2
    gap = tw // 2 + 22
    d.line([(cx - gap - 46, line_y), (cx - gap, line_y)], fill=BJ_LINE, width=2)
    d.line([(cx + gap, line_y), (cx + gap + 46, line_y)], fill=BJ_LINE, width=2)
    return y + th + 16


def _split_title_desc(text):
    """특징 문장을 (제목, 설명)으로 분리. 다양한 구분자(— : － - 등) 대응."""
    text = text.strip()
    for sep in (" — ", "—", " – ", "–", " : ", "：", ":", " - "):
        if sep in text:
            a, b = text.split(sep, 1)
            a, b = a.strip(), b.strip()
            if a and b:
                return a, b
    w = text.split()
    if len(w) > 5:
        return " ".join(w[:4]), " ".join(w[4:])
    return text, ""


def _bj_name_lines(d, name, font_big, max_w):
    """상품명을 (작은 윗줄, 큰 아랫줄)로 분리. 로고 중복·괄호 제거 + 균형 분할."""
    name = _clean(name)
    for pre in ("부자주방 ", "부자주방"):
        if name.startswith(pre):
            name = name[len(pre):].strip()
    # 끝의 괄호 보조설명 제거: "... (인덕션·가스 겸용)"
    if "(" in name and name.rstrip().endswith(")"):
        name = name[:name.rfind("(")].strip()
    words = name.split()
    if len(words) <= 1:
        return "", name
    if len(words) == 2:
        return words[0], words[1]
    # 아랫줄(큰 글씨)이 max_w에 들어가도록 뒤에서부터 단어를 모음 (최대 3단어)
    big_n = 1
    for k in (3, 2, 1):
        cand = " ".join(words[-k:])
        if _text_w(d, cand, font_big) <= max_w:
            big_n = k
            break
    return " ".join(words[:-big_n]), " ".join(words[-big_n:])


def _bj_logo(d, W, y, size=46):
    f  = _font(size, bold=True)
    w1 = _text_w(d, "부자", f)
    w2 = _text_w(d, "주방", f)
    x  = (W - w1 - w2) // 2
    d.text((x, y), "부자", font=f, fill=BJ_RED)
    d.text((x + w1, y), "주방", font=f, fill=BJ_BLACK)
    return y + _text_h(d, "부자", f)


def _bj_red_box(d, W, y, text, font_size=40, pad_h=14, margin=40):
    f  = _fit_font(d, text, W - margin * 2, font_size, bold=True, min_sz=20)
    th = _text_h(d, text, f)
    bh = th + pad_h * 2
    d.rectangle([margin, y, W - margin, y + bh], fill=BJ_RED)
    tw = _text_w(d, text, f)
    d.text(((W - tw) // 2, y + pad_h), text, font=f, fill=BJ_WHITE)
    return y + bh


def _bj_place_img(canvas, img, x, y, max_w, max_h):
    if not img or max_w <= 0 or max_h <= 0:
        return 0, 0
    thumb = img.copy().convert("RGB")
    thumb.thumbnail((max_w, max_h), Image.LANCZOS)
    ix = x + (max_w - thumb.width) // 2
    iy = max(y, y + (max_h - thumb.height) // 2)
    canvas.paste(thumb, (ix, iy))
    return thumb.width, thumb.height


def _bj_mixed_text(d, text, f, y, W, black_words=1):
    words = text.split()
    if not words:
        return
    if len(words) <= black_words:
        tw = _text_w(d, text, f)
        d.text(((W - tw) // 2, y), text, font=f, fill=BJ_BLACK)
        return
    bp = " ".join(words[:black_words])
    rp = " ".join(words[black_words:])
    full_tw = _text_w(d, text, f)
    x = (W - full_tw) // 2
    d.text((x, y), bp, font=f, fill=BJ_BLACK)
    bw = _text_w(d, bp + " ", f)
    d.text((x + bw, y), rp, font=f, fill=BJ_RED)


def _bj_header(W, H, product, imgs):
    canvas = _bj_bg(W, H)
    d  = ImageDraw.Draw(canvas)
    pad = 60
    y   = pad + 6

    y = _bj_logo(d, W, y)
    y += 14
    # 로고 밑 짧은 빨강 언더라인
    d.rectangle([(W - 54) // 2, y, (W + 54) // 2, y + 4], fill=BJ_RED)
    y += 44

    cat = _clean(product.get("category", "")) or "주방 전문 업체"
    cat = " ".join(cat.split()[:6])
    y = _bj_eyebrow(d, W, y, cat, color=BJ_GRAY, size=26)
    y += 26

    f_big = _font(74, bold=True)
    line1, line2 = _bj_name_lines(d, product.get("product_name", "상품명"),
                                  f_big, W - 56)
    if line1:
        y = _bj_red_box(d, W, y, line1, font_size=38, pad_h=13, margin=80)
        y += 10
        y = _bj_red_box(d, W, y, line2, font_size=74, pad_h=18, margin=28)
    else:
        y = _bj_red_box(d, W, y, line2, font_size=64, pad_h=18, margin=40)

    sub = _clean(product.get("sub_headline", "") or product.get("headline", ""))
    if sub:
        y += 26
        # 한 줄로 줄여 표시 (너무 길면 줄바꿈)
        fs = _fit_font(d, sub, W - pad * 2, 28, min_sz=22)
        y = _ov_wrap(d, sub, fs, pad, y, W - pad * 2, 200, fill=BJ_GRAY, line_gap=6)
        # 가운데 정렬 보정은 생략(좌측정렬도 무방), 여백만
    y += 46

    # 남은 공간을 꽉 채우는 풀블리드 히어로 (가로형 사진 여백 방지)
    if imgs:
        hero_h = H - y - pad
        if hero_h > 200:
            _bj_card_img(canvas, imgs[0], pad, y, W - pad * 2, hero_h,
                         radius=34, pad=0, fit="cover")
    return canvas


def _bj_features(W, H, product, imgs):
    canvas = _bj_bg(W, H)
    d  = ImageDraw.Draw(canvas)
    feats = [_clean(f) for f in _parse_features(product.get("key_features", ""))][:4]
    pad = 56
    y   = pad

    y = _bj_eyebrow(d, W, y, "KEY FEATURES", color=BJ_RED, size=22)
    y += 8
    f_title = _font(60, bold=True)
    title = "핵심 특징"
    d.text(((W - _text_w(d, title, f_title)) // 2, y), title, font=f_title, fill=BJ_BLACK)
    y += _text_h(d, title, f_title) + 34

    if imgs:
        img_h = int((H - y - pad) * 0.36)
        _, _, y = _bj_card_img(canvas, imgs[0], pad, y, W - pad * 2, img_h,
                               radius=28, pad=22)
        y += 30

    n      = max(1, len(feats))
    gap    = 18
    card_h = min(176, (H - y - pad - gap * (n - 1)) // n)
    inner_w = W - pad * 2 - 116

    for i, feat in enumerate(feats):
        cx0, cx1 = pad, W - pad
        _drop_shadow(canvas, (cx0, y, cx1, y + card_h),
                     radius=22, blur=15, alpha=32, dy=7)
        canvas.paste(Image.new("RGB", (cx1 - cx0, card_h), BJ_WHITE),
                     (cx0, y), _round_mask((cx1 - cx0, card_h), 22))
        d = ImageDraw.Draw(canvas)

        # 번호 칩
        chip = 52
        chy = y + (card_h - chip) // 2
        d.rounded_rectangle([cx0 + 22, chy, cx0 + 22 + chip, chy + chip],
                            radius=14, fill=BJ_RED)
        fn = _font(28, bold=True)
        num = str(i + 1)
        d.text((cx0 + 22 + (chip - _text_w(d, num, fn)) // 2,
                chy + (chip - _text_h(d, num, fn)) // 2 - 2),
               num, font=fn, fill=BJ_WHITE)

        tx = cx0 + 22 + chip + 24
        first, rest = _split_title_desc(feat)

        fb = _fit_font(d, first, inner_w, 30, bold=True, min_sz=18)
        if rest:
            ty0 = y + card_h // 2 - _text_h(d, first, fb) - 4
            d.text((tx, ty0), first, font=fb, fill=BJ_BLACK)
            fr = _fit_font(d, rest, inner_w, 22, min_sz=15)
            _ov_wrap(d, rest, fr, tx, ty0 + _text_h(d, first, fb) + 8, inner_w,
                     card_h, fill=BJ_GRAY, line_gap=3)
        else:
            d.text((tx, y + (card_h - _text_h(d, first, fb)) // 2),
                   first, font=fb, fill=BJ_BLACK)
        y += card_h + gap

    return canvas


def _bj_feature_detail(W, H, product, imgs, idx):
    canvas = _bj_bg(W, H, tint=(248, 249, 251))
    d  = ImageDraw.Draw(canvas)
    feats = [_clean(f) for f in _parse_features(product.get("key_features", ""))]
    feat   = feats[idx] if idx < len(feats) else (feats[0] if feats else "핵심 특징")
    others = [f for j, f in enumerate(feats) if j != idx][:3]
    pad = 56
    y   = pad + 4

    # POINT 배지 (라운드 칩)
    pt    = f"POINT 0{idx + 1}"
    f_pl  = _font(22, bold=True)
    bw    = _text_w(d, pt, f_pl) + 44
    bh    = _text_h(d, pt, f_pl) + 22
    bx    = (W - bw) // 2
    d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=bh // 2, fill=BJ_RED)
    d.text((bx + (bw - _text_w(d, pt, f_pl)) // 2, y + (bh - _text_h(d, pt, f_pl)) // 2 - 2),
           pt, font=f_pl, fill=BJ_WHITE)
    y += bh + 28

    # Feature heading: "굵은 제목 : 부제목" 구조
    headline, subtag = _split_title_desc(feat)
    subtag = " ".join(subtag.split()[:10])

    f_head  = _fit_font(d, headline, W - pad * 2, 60, bold=True, min_sz=26)
    words_h = headline.split()
    bc      = 1 if len(words_h) <= 2 else min(2, len(words_h) - 1)
    _bj_mixed_text(d, headline, f_head, y, W, bc)
    y += _text_h(d, headline, f_head) + 14

    if subtag:
        f_sub  = _fit_font(d, subtag, W - pad * 2, 28, min_sz=18)
        d.text(((W - _text_w(d, subtag, f_sub)) // 2, y), subtag, font=f_sub, fill=BJ_GRAY)
        y += _text_h(d, subtag, f_sub)
    y += 36

    # Product image (카드 + 그림자, 크게)
    img_area_h = int(H * 0.48)
    if imgs:
        _, _, y = _bj_card_img(canvas, imgs[idx % len(imgs)], pad, y,
                               W - pad * 2, img_area_h, radius=30, pad=24)
    else:
        y += img_area_h
    y += 30
    d = ImageDraw.Draw(canvas)

    # Bullet points — 남은 공간을 채우는 라운드 패널 안에
    bullets = others[:3]
    if bullets:
        panel_top = y
        panel_h   = H - pad - panel_top
        if panel_h > 80:
            _drop_shadow(canvas, (pad, panel_top, W - pad, panel_top + panel_h),
                         radius=22, blur=15, alpha=26, dy=6)
            canvas.paste(Image.new("RGB", (W - pad * 2, panel_h), BJ_WHITE),
                         (pad, panel_top), _round_mask((W - pad * 2, panel_h), 22))
            d = ImageDraw.Draw(canvas)
            ipad  = 36
            fd    = _font(23)
            dot_r = 6
            # 불릿 묶음을 패널 안에서 세로 중앙 정렬
            line_h = _text_h(d, "가", fd) + 22
            block_h = line_h * len(bullets)
            by = panel_top + max(ipad, (panel_h - block_h) // 2)
            for bullet in bullets:
                cy = by + dot_r + 6
                d.ellipse([pad + ipad, cy - dot_r, pad + ipad + dot_r * 2, cy + dot_r], fill=BJ_RED)
                bx2 = pad + ipad + dot_r * 2 + 16
                by = _ov_wrap(d, bullet, fd, bx2, by, W - bx2 - pad - ipad,
                              panel_h, fill=BJ_BLACK, line_gap=5)
                by += 22

    return canvas


def _bj_specs(W, H, product, imgs):
    canvas = _bj_bg(W, H)
    d  = ImageDraw.Draw(canvas)
    rows = [(_clean(k), _clean(v)) for k, v in _parse_spec_rows(product.get("specs", ""))]
    pad = 56
    y   = pad

    y = _bj_eyebrow(d, W, y, "SPECIFICATION", color=BJ_RED, size=22)
    y += 8
    f_title = _font(64, bold=True)
    title   = "제품 정보"
    d.text(((W - _text_w(d, title, f_title)) // 2, y), title, font=f_title, fill=BJ_BLACK)
    y += _text_h(d, title, f_title) + 14

    f_sub = _font(22)
    sub   = "이미지는 실제품과 다소 차이가 있을 수 있습니다."
    d.text(((W - _text_w(d, sub, f_sub)) // 2, y), sub, font=f_sub, fill=BJ_GRAY)
    y += _text_h(d, sub, f_sub) + 32

    if imgs:
        _, _, y = _bj_card_img(canvas, imgs[0], pad, y, W - pad * 2, 320,
                               radius=28, pad=20)
        y += 30
    d = ImageDraw.Draw(canvas)

    name = _clean(product.get("product_name", "상품명"))
    y = _bj_red_box(d, W, y, name, font_size=32, pad_h=10, margin=100)
    y += 24

    if rows:
        n       = min(len(rows), 8)
        key_w   = int((W - pad * 2) * 0.36)
        table_h = H - y - pad
        row_h   = min(58, table_h // max(n, 1))
        tbl_h   = row_h * n
        # 라운드 컨테이너 + 그림자
        _drop_shadow(canvas, (pad, y, W - pad, y + tbl_h),
                     radius=20, blur=16, alpha=30, dy=7)
        canvas.paste(Image.new("RGB", (W - pad * 2, tbl_h), BJ_WHITE),
                     (pad, y), _round_mask((W - pad * 2, tbl_h), 20))
        d = ImageDraw.Draw(canvas)
        for i, (k, v) in enumerate(rows[:n]):
            ry = y + i * row_h
            if i % 2 == 1:
                d.rectangle([pad, ry, W - pad, ry + row_h], fill=(249, 250, 252))
            if i > 0:
                d.line([pad + 18, ry, W - pad - 18, ry], fill=BJ_LINE, width=1)
            d.line([pad + key_w, ry + 10, pad + key_w, ry + row_h - 10], fill=BJ_LINE, width=1)
            fk_f = _fit_font(d, k, key_w - 32, 22, bold=True, min_sz=14)
            fv_f = _fit_font(d, v, W - pad * 2 - key_w - 32, 21, min_sz=14)
            kt   = ry + (row_h - _text_h(d, k, fk_f)) // 2
            vt   = ry + (row_h - _text_h(d, v, fv_f)) // 2
            d.text((pad + 24, kt), k, font=fk_f, fill=BJ_RED)
            d.text((pad + key_w + 24, vt), v, font=fv_f, fill=BJ_BLACK)

    return canvas


def _bj_usage(W, H, product, imgs):
    canvas = _bj_bg(W, H)
    d  = ImageDraw.Draw(canvas)
    steps = [_clean(s) for s in _parse_steps(product.get("how_to_use", ""))]
    if not steps:
        steps = [_clean(f) for f in _parse_features(product.get("key_features", ""))[:4]]
    pad = 56
    y   = pad

    y = _bj_eyebrow(d, W, y, "HOW TO USE", color=BJ_RED, size=22)
    y += 8
    f_title = _font(58, bold=True)
    title   = "사용 방법"
    d.text(((W - _text_w(d, title, f_title)) // 2, y), title, font=f_title, fill=BJ_BLACK)
    y += _text_h(d, title, f_title) + 34

    if imgs:
        img_idx = min(1, len(imgs) - 1)
        _, _, y = _bj_card_img(canvas, imgs[img_idx], pad, y, W - pad * 2,
                               int(H * 0.38), radius=28, pad=22)
        y += 34
    d = ImageDraw.Draw(canvas)

    steps = steps[:4]
    n     = max(1, len(steps))
    gap   = 16
    card_h = min(150, (H - y - pad - gap * (n - 1)) // n)
    for i, step in enumerate(steps):
        if y + card_h > H - pad + 4:
            break
        cx0, cx1 = pad, W - pad
        _drop_shadow(canvas, (cx0, y, cx1, y + card_h),
                     radius=20, blur=14, alpha=30, dy=6)
        canvas.paste(Image.new("RGB", (cx1 - cx0, card_h), BJ_WHITE),
                     (cx0, y), _round_mask((cx1 - cx0, card_h), 20))
        d = ImageDraw.Draw(canvas)
        # STEP 번호
        dot_r = 26
        cy = y + card_h // 2
        d.ellipse([cx0 + 26, cy - dot_r, cx0 + 26 + dot_r * 2, cy + dot_r], fill=BJ_RED)
        fn = _font(26, bold=True)
        num = str(i + 1)
        d.text((cx0 + 26 + dot_r - _text_w(d, num, fn) // 2,
                cy - _text_h(d, num, fn) // 2 - 2), num, font=fn, fill=BJ_WHITE)
        tx  = cx0 + 26 + dot_r * 2 + 24
        fs  = _fit_font(d, step, cx1 - tx - 24, 25, min_sz=16)
        _ov_wrap(d, step, fs, tx, y + (card_h - _text_h(d, step, fs)) // 2 - 6,
                 cx1 - tx - 24, card_h, fill=BJ_BLACK, line_gap=5)
        y += card_h + gap

    return canvas


def _bj_cta(W, H, product, imgs):
    if imgs:
        bg = imgs[-1].copy().convert("RGBA").resize((W, H), Image.LANCZOS)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 170))
        canvas = Image.alpha_composite(bg, ov).convert("RGB")
    else:
        canvas = Image.new("RGB", (W, H), (20, 20, 20))
    d  = ImageDraw.Draw(canvas)
    tc  = BJ_WHITE
    pad = 50

    y = int(H * 0.10)
    f_logo = _font(42, bold=True)
    w1     = _text_w(d, "부자", f_logo)
    w2     = _text_w(d, "주방", f_logo)
    x      = (W - w1 - w2) // 2
    d.text((x, y), "부자", font=f_logo, fill=BJ_RED)
    d.text((x + w1, y), "주방", font=f_logo, fill=tc)
    y += _text_h(d, "부자", f_logo) + 36

    q  = "온라인 구매, 정말 안전할까요?"
    fq = _fit_font(d, q, W - pad * 2, 38, min_sz=22)
    d.text(((W - _text_w(d, q, fq)) // 2, y), q, font=fq, fill=tc)
    y += _text_h(d, q, fq) + 18

    ans = "물론입니다."
    fa  = _font(60, bold=True)
    d.text(((W - _text_w(d, ans, fa)) // 2, y), ans, font=fa, fill=tc)
    y += _text_h(d, ans, fa) + 16

    tag  = "말이 아니라 결과로 증명된 제품"
    ft2  = _fit_font(d, tag, W - pad * 2, 26, min_sz=18)
    d.text(((W - _text_w(d, tag, ft2)) // 2, y), tag, font=ft2, fill=tc)

    btn_w = int(W * 0.64)
    btn_h = 74
    btn_x = (W - btn_w) // 2
    btn_y = int(H * 0.65)
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h],
                        radius=btn_h // 2, fill=BJ_RED)
    f_btn = _font(32, bold=True)
    bt    = "지금 바로 구매하기"
    btw   = _text_w(d, bt, f_btn)
    bth   = _text_h(d, bt, f_btn)
    d.text((btn_x + (btn_w - btw) // 2, btn_y + (btn_h - bth) // 2),
           bt, font=f_btn, fill=BJ_WHITE)

    return canvas


def _generate_bj_section(key: str, product: dict, imgs: list) -> Image.Image:
    """부자주방 스타일 섹션 — Pillow 전용 (Gemini 배경 불필요)"""
    W = 1000
    if "01_헤더" in key:
        return _bj_header(W, 1778, product, imgs)
    H = 1333
    if "02_특징요약" in key:
        return _bj_features(W, H, product, imgs)
    if "03_특징1" in key:
        return _bj_feature_detail(W, H, product, imgs, 0)
    if "04_특징2" in key:
        return _bj_feature_detail(W, H, product, imgs, 1)
    if "05_특징3" in key:
        return _bj_feature_detail(W, H, product, imgs, 2)
    if "06_스펙" in key:
        return _bj_specs(W, H, product, imgs)
    if "07_사용법" in key:
        return _bj_usage(W, H, product, imgs)
    if "08_CTA" in key:
        return _bj_cta(W, H, product, imgs)
    return Image.new("RGB", (W, H), BJ_WHITE)


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
    """제목 + 텍스트 + 이미지들 → Claude Opus 4.8 종합 분석 → 마케팅 카피 dict.
    (함수명은 호환 위해 유지, 내부는 Claude)"""
    prompt = f"""당신은 한국 이커머스(쿠팡·네이버 스마트스토어) 최고의 상세페이지 카피라이터입니다.
업소용 주방기기 전문 셀러 '부자주방'의 상세페이지를 만든다고 가정하고, 아래 상품 정보와 첨부 이미지들을 종합 분석해 **실제로 구매전환을 높이는** 마케팅 카피를 개발하세요.

⚠️ 매우 중요:
- 첨부된 모든 이미지 안에 적힌 텍스트(스펙·수치·소재·특징·인증 등)를 꼼꼼히 읽고 카피에 반영하세요.
- 추상적 미사여구 말고, **구체적 수치·소재·규격·사용 상황**을 담은 설득력 있는 문장을 쓰세요.
- 업소(식당·매장) 사장님이 읽고 "이거다" 싶게, 실무적 이점(내구성·위생·효율·전기/가스비·공간)을 강조하세요.

페이지 제목: {title}
페이지 텍스트: {page_text[:2500] if page_text else "(없음)"}

아래 JSON 형식으로만 출력하세요. 코드블록 없이 순수 JSON만.
key_features는 이미지/텍스트에서 읽은 실제 특징을 마케팅 언어로 발전시켜 5개 작성.

⚠️ 형식 규칙:
- product_name: 브랜드명('부자주방') 없이, 괄호 보조설명 없이 순수 상품명만. 12자 내외 권장(최대 20자).
- key_features 각 줄은 반드시 "짧은핵심제목 : 구체설명" 형식. 제목은 8자 이내 임팩트 단어(예: 강력화력, 올스텐304, 자동안전), 설명은 수치·소재·이점을 담아 한 줄(30자 내외).
- headline/sub_headline은 한 줄로 간결하게(헤드라인 22자·서브 40자 내외).

{{
  "product_name": "정확한 상품명 (브랜드·괄호 제외, 짧게)",
  "category": "카테고리",
  "headline": "상세페이지 최상단 한 줄 메인 후킹 카피 (강력하게, 22자 내외)",
  "sub_headline": "헤드라인 보조 문구 한 줄 (40자 내외)",
  "key_features": "• 강력화력 : 구당 6,000kcal 고화력으로 빠른 조리\n• 올스텐304 : 녹슬지 않는 식품용 스테인리스 본체\n• 자동안전 : 불꽃 꺼짐 감지 시 가스 자동 차단\n• 넓은상판 : 1200mm 대형 작업대로 동선 최소화\n• 간편세척 : 분리형 트레이로 기름때 청소 30초",
  "specs": "항목: 값\n항목: 값\n항목: 값\n항목: 값",
  "how_to_use": "1. 사용법1\n2. 사용법2\n3. 사용법3\n4. 사용법4",
  "target_customer": "타겟 고객 설명 (어떤 업종/상황의 사장님인지 구체적으로)",
  "cta": "구매를 유도하는 마지막 한 줄 카피",
  "main_color": "#XXXXXX",
  "sub_color": "#XXXXXX",
  "background": "배경 스타일",
  "mood": "분위기 키워드",
  "font_style": "폰트 스타일"
}}"""
    try:
        result = await _claude_vision_json(prompt, list(images_to_send), max_tokens=3500)
        if "error" in result:
            return {"error": f"분석 실패: {result['error']}", "product_name": title,
                    "_rep_image": rep_image, "_images": images_to_send}
        result["_rep_image"] = rep_image
        result["_images"] = images_to_send
        return result
    except Exception as e:
        return {"error": f"분석 실패: {e}", "product_name": title,
                "_rep_image": rep_image, "_images": images_to_send}


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


async def fetch_coupang_reference(product_url: str) -> dict:
    """경쟁사 원문·이미지를 복제하지 않고 기획 참고용 텍스트만 반환한다."""
    parsed = urlparse((product_url or "").strip())
    host = (parsed.hostname or "").lower()
    if host != "coupang.com" and not host.endswith(".coupang.com"):
        raise ValueError("쿠팡 상품 링크를 확인해 주세요")
    title, page_text, _, _ = await _fetch_coupang_data(product_url)
    clean_text = re.sub(r"\s+", " ", page_text or "").strip()[:2000]
    if not title and not clean_text:
        raise ValueError("쿠팡 참고 상품 정보를 읽지 못했습니다")
    return {"title": (title or "").strip()[:300], "page_text": clean_text}


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
    """단일 상품 이미지 → Claude Opus 4.8 분석 → 마케팅 카피 dict"""
    prompt = """당신은 한국 이커머스 상세페이지 카피라이터입니다. 첨부된 업소용 주방기기 상품 이미지를 분석해, 이미지 안의 텍스트(스펙·수치·소재)까지 읽어서 아래 JSON만 출력하세요 (코드블록 없이 순수 JSON).
{"product_name":"정확한 상품명","category":"카테고리","headline":"메인 후킹 카피 한 줄","sub_headline":"보조 문구","key_features":"• 특징1(수치/소재 포함)\n• 특징2\n• 특징3\n• 특징4\n• 특징5","specs":"항목: 값\n항목: 값","how_to_use":"1. \n2. \n3. ","target_customer":"타겟 고객","cta":"구매 유도 카피","main_color":"#2C5F8A","sub_color":"#F0F4F8","background":"깔끔한 화이트","mood":"모던, 신뢰감 있는","font_style":"굵은 고딕체"}"""
    try:
        return await _claude_vision_json(prompt, [image_url], max_tokens=2500)
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
    """부자주방 스타일 — Pillow 전용 섹션 생성 (Gemini 배경 불필요)"""
    # 제품 이미지 수집 (대표 + 분석에 사용된 이미지들)
    img_urls: list[str] = []
    if image_url:
        img_urls.append(image_url)
    for u in product_data.get("_images", []):
        if u and u not in img_urls:
            img_urls.append(u)

    imgs: list[Image.Image] = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for url in img_urls[:6]:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    pil = Image.open(io.BytesIO(r.content)).convert("RGB")
                    imgs.append(pil)
            except Exception:
                pass

    for section in SECTIONS:
        try:
            pil_img = _generate_bj_section(section["key"], product_data, imgs)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=92)
            yield {
                "key":       section["key"],
                "name":      section["name"],
                "image_b64": base64.b64encode(buf.getvalue()).decode(),
            }
        except Exception as e:
            yield {
                "key":   section["key"],
                "name":  section["name"],
                "error": str(e),
            }
