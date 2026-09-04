"""CN인사이더 → gpt-image 상세페이지 (돈버는하마 프롬프트 방식, v2)
기존 pipeline.py의 로그인/스크랩/Claude를 재사용하고, 생성만 gpt-image로 교체."""
import os, json, io, re, base64, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from PIL import Image, ImageDraw, ImageFont
import pipeline as P   # 기존 모듈 재사용 (ensure_login, scrape, _claude, _json, log 등)

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = P.ENV
OKEY = (os.getenv("OPENAI_API_KEY") or P.ENV.get("OPENAI_API_KEY") or "").strip()
HDR = P.HDR
IMG_MODEL = "gpt-image-2"   # 최신 최고급 — 로고제거·한글 우수 (gpt-image-1 대비 검증완료)
PLAN_MODEL = os.getenv("CN_PLAN_MODEL", "gpt-5.6-terra").strip()
log = P.log
FONT_DIR = os.path.join(BASE, "fonts")
TITLE_FONT = os.path.join(FONT_DIR, "Pretendard-Black.otf")
MEDIUM_TITLE_FONT = os.path.join(FONT_DIR, "Pretendard-ExtraBold.otf")
BODY_FONT = os.path.join(FONT_DIR, "Pretendard-Regular.otf")
SMALL_FONT = os.path.join(FONT_DIR, "Pretendard-Medium.otf")


def _font(path, size):
    try:
        return ImageFont.truetype(path, max(8, int(size)))
    except Exception:
        return ImageFont.load_default()


def _section_size(section_index, low=False):
    sizes = [(860, 1920), (860, 860)] + [(860, 1290)] * 9
    width, height = sizes[min(max(section_index, 0), len(sizes) - 1)]
    return (width // 2, height // 2) if low else (width, height)


def _template_index(section, fallback_index):
    """Keep template identity stable even when earlier sections are disabled."""
    try:
        number = int(section.get("number"))
        if 1 <= number <= 11:
            return number - 1
    except (TypeError, ValueError, AttributeError):
        pass
    return fallback_index


def _wrap_text(draw, text, font, max_width, max_lines=4):
    words = list(str(text or "").replace("\n", " ").strip())
    lines, current = [], ""
    for char in words:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current.strip()); current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current.strip())
    return lines


def _draw_box_text(draw, xy, text, font, max_width, anchor="la", max_lines=3, padding=8):
    """Draw readable text without reproducing the guide template's black boxes."""
    lines = _wrap_text(draw, text, font, max_width - padding * 2, max_lines)
    if not lines:
        return
    line_height = draw.textbbox((0, 0), "가A", font=font)[3] + max(3, font.size // 7)
    widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
    x, y = xy
    for line, width in zip(lines, widths):
        if anchor == "ma":
            x = min(max(x, width // 2 + padding), draw._image.width - width // 2 - padding)
            left = x - width // 2
        elif anchor == "ra":
            x = min(max(x, width + padding), draw._image.width - padding)
            left = x - width
        else:
            x = min(max(x, padding), draw._image.width - width - padding)
            left = x
        sample = draw._image.crop((max(0, left), max(0, y), min(draw._image.width, left + width), min(draw._image.height, y + line_height)))
        mean = sum(sample.resize((1, 1)).getpixel((0, 0))) / 3 if sample.width and sample.height else 255
        fill = (250, 250, 250) if mean < 125 else (28, 26, 24)
        draw.text((x, y), line, font=font, fill=fill, anchor=anchor)
        y += line_height + max(2, padding // 2)


def compose_plan_text(image, plan, section, section_index):
    """Overlay Korean copy using the supplied 11-section 860x1290 template."""
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    scale = image.width / 860.0
    sx = lambda value: int(value * scale)
    title = str(section.get("title") or "").strip()
    body = str(section.get("body") or "").strip()
    product = plan.get("product") or {}
    features = [str(item.get("title") or "").strip() for item in (plan.get("features") or []) if isinstance(item, dict)]
    big = _font(TITLE_FONT, sx(68)); medium = _font(MEDIUM_TITLE_FONT, sx(42))
    body_font = _font(BODY_FONT, sx(29)); small = _font(SMALL_FONT, sx(23))

    if section_index == 0:
        _draw_box_text(draw, (sx(430), sx(105)), product.get("summary") or "추천 상품", small, sx(620), "ma", 1)
        _draw_box_text(draw, (sx(430), sx(190)), product.get("name") or title, big, sx(700), "ma", 2)
        _draw_box_text(draw, (sx(430), sx(350)), " · ".join(features[:4]), body_font, sx(690), "ma", 2)
    elif section_index == 1:
        _draw_box_text(draw, (sx(430), sx(110)), title, medium, sx(650), "ma", 2)
        _draw_box_text(draw, (sx(430), sx(245)), body, body_font, sx(680), "ma", 3)
    elif section_index == 2:
        _draw_box_text(draw, (sx(790), sx(135)), "이런 점이 만족스러워요", small, sx(400), "ra", 1)
        _draw_box_text(draw, (sx(790), sx(215)), title, big, sx(700), "ra", 2)
    elif section_index == 3:
        _draw_box_text(draw, (sx(430), sx(85)), "POINT REVIEW", medium, sx(600), "ma", 1)
        _draw_box_text(draw, (sx(430), sx(180)), title, body_font, sx(690), "ma", 2)
        _draw_box_text(draw, (sx(430), sx(1030)), body, small, sx(700), "ma", 3)
    elif section_index == 4:
        _draw_box_text(draw, (sx(95), sx(150)), title, big, sx(680), "la", 2)
        _draw_box_text(draw, (sx(115), sx(880)), body, body_font, sx(650), "la", 3)
    elif section_index == 5:
        _draw_box_text(draw, (sx(430), sx(80)), title, big, sx(720), "ma", 2)
        for i, feature in enumerate(features[:4]):
            _draw_box_text(draw, (sx(440), sx(350 + i * 205)), feature, body_font, sx(360), "la", 2)
    elif 6 <= section_index <= 9:
        point = features[min(section_index - 6, max(0, len(features) - 1))] if features else title
        _draw_box_text(draw, (sx(430), sx(90)), "CHECK POINT %02d" % (section_index - 5), small, sx(400), "ma", 1)
        _draw_box_text(draw, (sx(430), sx(205)), point or title, medium, sx(720), "ma", 2)
        _draw_box_text(draw, (sx(430), sx(325)), body, small, sx(640), "ma", 4)
    else:
        _draw_box_text(draw, (sx(430), sx(85)), "PRODUCT INFO", medium, sx(520), "ma", 1)
        info = [
            product.get("name"), "소재  " + str(product.get("material") or "확인 필요"),
            "색상  " + str(product.get("color") or "확인 필요"), "크기  " + str(product.get("size") or "확인 필요"),
            "구성  " + str(product.get("composition") or "확인 필요"), "제조국/수입원  중국/주식회사 부자홀딩스",
            "사용법  " + str(product.get("usage") or "확인 필요"),
            "주의사항  " + str(product.get("caution") or "확인 필요"),
        ]
        _draw_box_text(draw, (sx(430), sx(790)), "\n".join(filter(None, info)), small, sx(680), "ma", 7)
    return image


def create_text_plan(content):
    """Create the reviewed text plan with the OpenAI key already on Lightsail."""
    if not OKEY:
        raise RuntimeError("OpenAI API 키가 설정되지 않았습니다")
    body = {
        "model": PLAN_MODEL,
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "medium"},
        "max_output_tokens": 8000,
        "store": False,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + OKEY, "Content-Type": "application/json"},
    )
    log("GPT 내부 기획 시작 (%s)" % PLAN_MODEL)
    started_at = time.time()
    try:
        response = json.loads(urllib.request.urlopen(request, timeout=180).read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("OpenAI 기획안 오류 %d: %s" % (exc.code, detail)) from exc
    parts = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if block.get("type") in ("output_text", "text"):
                parts.append(str(block.get("text") or ""))
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("GPT 기획안 응답이 비어 있습니다")
    log("GPT 내부 기획 완료 (%d초)" % max(1, int(time.time() - started_at)))
    return text

def normalize_url(url):
    """login?redirect=... 형태면 실제 상품 URL로 변환."""
    import urllib.parse
    if "#/login" in url and "redirect" in url:
        frag=url.split("#",1)[1]
        q=frag.split("?",1)[1] if "?" in frag else ""
        rd=urllib.parse.unquote(dict(urllib.parse.parse_qsl(q)).get("redirect",""))
        if rd: return "https://www.cninsider.co.kr/mall/#"+rd
    return url

def _select_product_image_urls(items):
    """브라우저에서 모은 후보 중 1688 상품 사진으로 보이는 주소만 고른다."""
    ranked = []
    seen = set()
    blocked = ("icon", "logo", "avatar", "banner", "sprite", "loading", "blank", "qr")
    for item in items:
        src = str(item.get("src") or "").strip().replace("\\u002F", "/")
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http") or "alicdn" not in src.lower():
            continue
        clean = src.split("!!", 1)[0]
        clean = re.sub(r"_[0-9]+x[0-9]+(?:q[0-9]+)?\.(jpg|jpeg|png|webp)$", r".\1", clean, flags=re.I)
        key = clean.split("?", 1)[0]
        if key in seen or any(word in key.lower() for word in blocked):
            continue
        width = int(item.get("w") or 0)
        height = int(item.get("h") or 0)
        if width and height and max(width, height) < 120:
            continue
        square = width and height and abs(width - height) <= max(width, height) * 0.25
        tall_detail = width and height and height >= width * 1.3
        score = (3 if square else 0) + (4 if tall_detail else 0) + (2 if max(width, height) >= 400 else 0)
        if any(word in key.lower() for word in ("imgextra", "bao/uploaded", "offer")):
            score += 2
        seen.add(key)
        ranked.append((score, clean))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [src for _, src in ranked[:24]]

def _collect_product_images(pg):
    """src 외 지연 로딩·srcset·배경·네트워크 이미지까지 함께 수집한다."""
    for y in (700, 1400, 2200, 3200, 4500, 6000, 8000, 10000, 12000):
        pg.evaluate("(y) => window.scrollTo(0, y)", y)
        pg.wait_for_timeout(350)
    items = pg.evaluate(r"""() => {
        const out = [];
        const add = (src, w=0, h=0) => {
            if (!src) return;
            String(src).split(',').forEach(part => {
                const url = part.trim().split(/\s+/)[0];
                if (url) out.push({src:url, w:Number(w)||0, h:Number(h)||0});
            });
        };
        document.querySelectorAll('img').forEach(el => {
            const w = el.naturalWidth || el.width, h = el.naturalHeight || el.height;
            ['currentSrc','src','data-src','data-lazy-src','data-lazyload-src','data-original','srcset','data-srcset']
                .forEach(name => add(el[name] || el.getAttribute(name), w, h));
        });
        document.querySelectorAll('source').forEach(el => add(el.srcset || el.getAttribute('srcset')));
        document.querySelectorAll('[style*="background"]').forEach(el => {
            const match = getComputedStyle(el).backgroundImage.match(/url\(["']?(.*?)["']?\)/);
            if (match) add(match[1], el.clientWidth, el.clientHeight);
        });
        performance.getEntriesByType('resource')
            .filter(entry => entry.initiatorType === 'img')
            .forEach(entry => add(entry.name));
        return out;
    }""")
    return _select_product_image_urls(items)

def _clean_product_title(value):
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    title = re.sub(r"\s*[-_|]\s*(?:1688|阿里巴巴).*$", "", title, flags=re.I)
    if title.lower() in {"cninsider", "cn인사이더"}:
        return ""
    return title[:200]

def _is_access_blocked(title, body=""):
    text = (str(title or "") + "\n" + str(body or "")[:1000]).lower()
    return any(message in text for message in (
        "access denied",
        "访问被拒绝",
        "页面无法访问",
        "punish page",
    ))

def _collect_product_title(pg, body):
    candidates = pg.evaluate(r"""() => [
        document.querySelector('meta[property="og:title"]')?.content,
        document.querySelector('meta[name="title"]')?.content,
        document.querySelector('h1')?.innerText,
        document.title
    ].filter(Boolean)""")
    for candidate in candidates:
        title = _clean_product_title(candidate)
        if len(title) >= 8:
            return title
    for line in body.split("\n"):
        title = _clean_product_title(line)
        if len(title) > 20 and re.search("[가-힣一-龥]", title):
            return title
    return ""

def _solve_captcha(b64, mime):
    """캡차 판독 (Claude). 한글 설명 섞여도 영숫자만 추출."""
    txt=P._claude([{"type":"text","text":"이 이미지에 보이는 글자와 숫자를 그대로 읽어줘. 빨간 글씨이고 사선 노이즈가 있어. 추측이라도 4자 내외로 답해. 영문 대소문자와 숫자만."},
        {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}], 50)
    m=re.findall(r"[A-Za-z0-9]+", txt)
    # 가장 긴 토큰(보통 캡차 코드)
    return max(m, key=len) if m else ""

def _do_captcha_login(pg):
    """로그인 페이지에서 캡차 풀어 로그인. 성공 여부 반환."""
    pg.goto("https://www.cninsider.co.kr/mall/#/login", wait_until="domcontentloaded", timeout=40000)
    pg.wait_for_timeout(3500)
    for att in range(6):
        info=pg.eval_on_selector_all("img[src^='data:image']","els=>els.map(e=>({w:e.naturalWidth,src:e.src}))")
        if not info: pg.wait_for_timeout(1500); continue
        cap=max(info,key=lambda x:x['w'])
        b64=cap['src'].split(",",1)[1]; raw=base64.b64decode(b64)
        # ⚠️ data URL의 png 표기가 거짓일 수 있음 → 실제 바이트 시그니처로 판별
        if raw[:3]==b"\xff\xd8\xff": mime="image/jpeg"
        elif raw[:4]==b"\x89PNG": mime="image/png"
        elif raw[:4]==b"GIF8": mime="image/gif"
        elif raw[:4]==b"RIFF": mime="image/webp"
        else: mime="image/jpeg"
        code=_solve_captcha(b64,mime); log(f"캡차[{att+1}]: {code} (mime={mime})")
        ins=pg.query_selector_all("input.el-input__inner")
        if len(ins)<3: pg.wait_for_timeout(1500); continue
        ins[0].fill(ENV["CN_ID"]); ins[1].fill(ENV["CN_PW"]); ins[2].fill(code)
        pg.wait_for_timeout(400)
        try: pg.click("button.logo_btn", timeout=6000)
        except: pass
        pg.wait_for_timeout(4500)
        if "login" not in pg.url.lower():
            log("로그인 성공·세션저장"); pg.context.storage_state(path=P.STATE); return True
        # 캡차 새로고침
        try:
            for im in pg.query_selector_all("img[src^='data:image']"):
                bb=im.bounding_box()
                if bb and bb["width"]>150: im.click(); break
        except: pass
        pg.wait_for_timeout(1500)
    return False

def login_and_scrape(url):
    """상품 페이지 기준으로 로그인 검증 → 막히면 캡차 로그인 → 스크랩."""
    from playwright.sync_api import sync_playwright
    url=normalize_url(url)
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        ctx=b.new_context(storage_state=P.STATE if os.path.exists(P.STATE) else None,
                          viewport={"width":1366,"height":3000},locale="ko-KR")
        pg=ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(6000)
        if "login" in pg.url.lower():   # 세션 만료 → 상품페이지가 로그인으로 튕김
            log("상품페이지 접근에 로그인 필요 → 캡차 로그인")
            if not _do_captcha_login(pg):
                b.close(); raise RuntimeError("로그인 실패")
            pg.goto(url, wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(8000)
            if "login" in pg.url.lower(): b.close(); raise RuntimeError("로그인 후에도 상품 접근 불가")
        else:
            log("세션 유효(상품페이지 접근됨)")
            pg.wait_for_timeout(4000)
        # 스크랩
        body=pg.inner_text("body")
        main=_collect_product_images(pg)
        title=_collect_product_title(pg, body)
        if _is_access_blocked(title, body):
            b.close()
            raise RuntimeError("1688 direct access blocked; use a CN Insider link or uploaded images")
        for line in []:
            if len(line)>20 and re.search("[가-힣]",line) and not any(x in line for x in ["CN인사이더","장바구니","고객","로그인","멤버","공지","환영"]):
                title=line.strip(); break
        b.close()
        return {"title":title, "main_imgs":main}

# ---------- OpenAI 이미지 생성 ----------
def _oai_image(prompt, ref_imgs_b64=None, size="1024x1536", quality="high"):
    """ref_imgs_b64: list of (mime,b64). 있으면 images/edit, 없으면 generations."""
    if ref_imgs_b64:
        # multipart/form-data for images/edit
        boundary = "----cnmaker" + str(int(time.time()*1000))
        parts = []
        def add_field(name, value):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        def add_file(name, fname, mime, raw):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{fname}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            parts.append(raw); parts.append(b'\r\n')
        add_field("model", IMG_MODEL)
        add_field("prompt", prompt)
        add_field("size", size)
        add_field("quality", quality)
        add_field("n", "1")
        for i,(mime,b64) in enumerate(ref_imgs_b64[:4]):
            ext = "png" if "png" in mime else "jpg"
            add_file("image[]", f"ref{i}.{ext}", mime, base64.b64decode(b64))
        parts.append(f'--{boundary}--\r\n'.encode())
        data = b"".join(parts)
        req = urllib.request.Request("https://api.openai.com/v1/images/edits", data=data,
            headers={"Authorization":"Bearer "+OKEY, "Content-Type":f"multipart/form-data; boundary={boundary}"})
    else:
        body = json.dumps({"model":IMG_MODEL,"prompt":prompt,"size":size,"quality":quality,"n":1}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
            headers={"Authorization":"Bearer "+OKEY, "Content-Type":"application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=300).read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()); ei = err.get("error") or {}
            msg = ei.get("message", ""); code = ei.get("code", "")
        except Exception:
            msg = ""; code = ""
        if code == "billing_hard_limit_reached" or "billing" in (code or "").lower() or "billing" in (msg or "").lower():
            raise RuntimeError("OpenAI 결제 한도 초과 — platform.openai.com에서 크레딧 충전/한도 상향 필요")
        raise RuntimeError(f"OpenAI 오류 {e.code}: {msg or e.reason}")
    b64 = d["data"][0].get("b64_json")
    return base64.b64decode(b64)

# ---------- 폼 자동완성 (Opus: 소구점·폼) ----------
def fill_form(data):
    """스크랩 데이터 + 제품이미지 → 돈버는하마 폼 필드 자동완성 (Opus)."""
    imgs=[]
    for src in data["main_imgs"][:3]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
            mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
            imgs.append((mime,base64.b64encode(raw).decode()))
        except: pass
    prompt=f"""당신은 10년차 쿠팡 상세페이지 기획자입니다. 중국 1688에서 소싱한 상품을 한국 쿠팡용 상세페이지로 만들기 위한 정보를 정리하세요.
원본 상품명(자동번역체, 어색함): {data['title']}
첨부 제품 이미지를 분석해서 실제 제품 특징을 파악하세요.

요구:
- 자연스럽고 검색 잘 되는 한국어 상품명 (번역투 금지)
- 타제품과 차별화되는 핵심 소구점 3가지 (각각 제목+근거, 이미지에서 실제로 보이는 특징 기반)
- 옵션/사이즈는 이미지·상품명에서 추정 가능한 것만. 모르면 "단일 옵션"/"" 으로.

아래 JSON만 출력(코드블록 없이):
{{"brand":"부자주방","product_name":"자연스러운 상품명(15자내외)","category":"카테고리","option":"옵션(없으면 '단일 옵션')","size":"사이즈/규격(모르면 빈문자열)","sellpoints":[{{"title":"소구점1 제목(8자내외)","desc":"근거 한 줄"}},{{"title":"소구점2 제목","desc":"근거 한 줄"}},{{"title":"소구점3 제목","desc":"근거 한 줄"}}],"mood":"제품에 어울리는 무드 키워드"}}"""
    content=[{"type":"text","text":prompt}]
    for mime,b64 in imgs: content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    form=P._json(P._claude(content, 2000))
    form["main_imgs"]=data["main_imgs"]
    return form

# ---------- 카페24(국내 경쟁사) 스크랩 + 폼 (번역X, 문구 재작성) ----------
def scrape_cafe24(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        ctx=b.new_context(viewport={"width":1280,"height":2600},locale="ko-KR",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
        data=P.scrape_cafe24(ctx,url)
        b.close()
    return data

def fill_form_cafe24(data):
    """카페24 경쟁사 상세 → 우리 폼 자동완성 (번역X, 문구는 우리 톤으로 새로, 저작권 회피)."""
    imgs=[]
    for src in (data.get("main_imgs",[])[:2] + data.get("detail_imgs",[])[:2])[:4]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=25).read()
            # Claude 이미지 제한(한 변 8000px·용량) 대응 → 리사이즈 후 전송(분석용이라 저해상도 충분)
            im=Image.open(io.BytesIO(raw)).convert("RGB")
            if max(im.size)>1500:
                r=1500/max(im.size); im=im.resize((int(im.width*r),int(im.height*r)),Image.LANCZOS)
            bio=io.BytesIO(); im.save(bio,"JPEG",quality=85)
            imgs.append(("image/jpeg",base64.b64encode(bio.getvalue()).decode()))
        except Exception: pass
    prompt=f"""당신은 10년차 쿠팡 상세페이지 기획자입니다. 아래는 국내 경쟁사 상세페이지에서 가져온 상품 정보와 이미지입니다.
경쟁사 상품명(참고): {data.get('title','')}
첨부 이미지를 분석해 실제 제품 특징을 파악하세요.

⚠️ 매우 중요(저작권·차별화):
- 경쟁사 문구·표현을 그대로 베끼지 마세요. 소구점의 '내용(사실)'만 참고하고, 문장·표현은 부자주방 톤으로 완전히 새로 쓰세요.
- 상품명도 검색 잘 되게 자연스럽게 재작성(경쟁사 상품명 그대로 복사 금지).

요구:
- 부자주방(업소용 주방기기) 브랜드로, 자연스러운 한국어 상품명(15자내외)
- 타제품과 차별화되는 핵심 소구점 3가지 (제목+근거, 이미지에서 실제로 보이는 특징 기반, 우리 문구로 새로)
- 옵션/사이즈는 이미지·상품명에서 추정 가능한 것만. 모르면 "단일 옵션"/"".

아래 JSON만 출력(코드블록 없이):
{{"brand":"부자주방","product_name":"자연스러운 상품명","category":"카테고리","option":"옵션(없으면 '단일 옵션')","size":"사이즈/규격(모르면 빈문자열)","sellpoints":[{{"title":"소구점1 제목(8자내외)","desc":"근거 한 줄"}},{{"title":"소구점2 제목","desc":"근거 한 줄"}},{{"title":"소구점3 제목","desc":"근거 한 줄"}}],"mood":"제품에 어울리는 무드 키워드"}}"""
    content=[{"type":"text","text":prompt}]
    for mime,b64 in imgs: content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    form=P._json(P._claude(content, 2000))
    form["main_imgs"]=data.get("main_imgs",[])
    return form

# ---------- 섹션별 프롬프트 (돈버는하마 PDF 기반) ----------
COMMON = """당신은 10년차 쿠팡 상세페이지 기획자이자 프리미엄 브랜드 디자이너입니다.
첨부한 제품 이미지를 분석하여 한국형 프리미엄 모바일 상세페이지의 한 섹션 이미지를 제작하세요.

[절대 규칙]
- 첨부된 제품의 디자인·색상·형태·소재감·패턴·비율·질감을 절대 변경하지 마세요. 실제 제품 그대로 유지.
- ⚠️ 단, 제품 표면에 인쇄된 중국어 브랜드 로고·상표·워터마크·중국어 글자는 모두 제거하고 깨끗한 무지(無地) 표면으로 표현하세요. (예: 파란 사각 로고, 한자 마크 등 → 삭제). 제품 형태·색·재질은 유지하되 타사 브랜딩만 지워주세요.
- 영수증·종이 위의 글자는 의미 없는 깨알 텍스트가 아니라 자연스러운 한글 영수증처럼 보이게 하거나 흐릿하게 처리하세요.
- AI가 임의로 색상·옵션·구성품을 추가하지 마세요.
- 한국 소비자가 봤을 때 어색하거나 번역투인 표현 금지. 실제 국내 브랜드 상세페이지처럼 자연스럽고 세련되게.
- 한글 텍스트는 또렷하고 정확하게. 작은 글씨·긴 문장·과한 장식 금지. 정렬과 여백 정확히.
- 세로형 모바일 섹션. 제품이 선명하게 돋보이고 배경은 차분하게.

[디자인 톤]
- 색상: 베이지톤 중심 / 배경: 밝은 아이보리·라이트그레이 / 폰트: 굵고 깔끔한 고딕(Noto Sans KR Bold 느낌)
- 무드: 미니멀, 프리미엄, 따뜻한 감성
"""

def section_prompts(f):
    b=f.get("brand","부자주방"); pn=f.get("product_name","상품"); opt=f.get("option",""); sz=f.get("size","")
    sp=f.get("sellpoints",[{"title":"","desc":""}]*3)
    def s(i): return sp[i] if i<len(sp) else {"title":"","desc":""}
    P_=[]
    # 1 히어로
    P_.append(COMMON+f"""[섹션 1: 히어로 이미지]
구성: 브랜드명 + 제품명 + 한글 메인카피 + 제품 실사용 연출 감성샷(인테리어/라이프스타일 무드).
브랜드명: "{b}" (가장 작고 얇게, 상단)
제품명: "{pn}" (가장 크고 굵게, 메인 타이틀)
메인카피: 소구점 중 전환율 높은 1개를 골라 1~2줄 감성 카피 (제품명보다 작고 중간 굵기)
크기 위계 반드시: 브랜드명 < 메인카피 < 제품명. 제품명이 가장 강조.""")
    # 2 리뷰/평점
    P_.append(COMMON+f"""[섹션 2: 리뷰/평점]
별점 4.9/5 를 크게. 실제 구매자가 쓴 듯 자연스럽고 신뢰감 있는 후기 카드 3개(각 1줄, 제품 사진 없이).
후기는 "{pn}"의 특징과 소구점({s(0)['title']},{s(1)['title']},{s(2)['title']}) 기반. 프리미엄하고 정돈된 느낌.""")
    # 3 핵심가치 3
    P_.append(COMMON+f"""[섹션 3: 3가지 핵심가치]
메인카피: "왜 쓰면 쓸수록 만족스러울까요?"
번호 01/02/03 카드 + 제품 사진 + 한 줄 설명.
01: {s(0)['title']} — {s(0)['desc']}
02: {s(1)['title']} — {s(1)['desc']}
03: {s(2)['title']} — {s(2)['desc']}
번호 카드 형식 유지하되 과하지 않고 깔끔하게.""")
    # 4~6 Point (미니멀 규칙)
    POINT_RULE="""[Point 섹션 전용 규칙] 큰 메인카피 + 짧은 설명(1~2줄) + 제품 감성샷 중심으로 미니멀하게. 아이콘 칩·기능 박스·원형 카테고리 라벨·키워드 배지 절대 넣지 마세요. 여백 넓게, 제품 이미지가 중심인 프리미엄 레이아웃."""
    P_.append(COMMON+f"""[섹션 4: Point 01 - {s(0)['title']}]
{POINT_RULE}
메인카피: {s(0)['title']}의 장점이 감성적으로 느껴지게. 짧은 설명: {s(0)['desc']} 를 어떤 상황에서 쓰면 좋은지 상상되게.""")
    P_.append(COMMON+f"""[섹션 5: Point 02 - {s(1)['title']}]
{POINT_RULE}
메인카피: {s(1)['title']}의 실용적 장점이 바로 전달되게. 짧은 설명: {s(1)['desc']}. 기능/구조가 드러나는 제품 이미지 + 원형 확대컷 1개(제품 소재·구조·마감 중 하나만).""")
    P_.append(COMMON+f"""[섹션 6: Point 03 - {s(2)['title']}]
{POINT_RULE}
메인카피: {s(2)['title']}의 활용도·편리함이 느껴지게. 짧은 설명: {s(2)['desc']}. 다양한 활용 장면을 제품 이미지 연출로 자연스럽게. 원형 설명 박스/라벨 나열 금지.""")
    # 7 비교표
    P_.append(COMMON+f"""[섹션 7: 비교 - 타사 vs 자사]
메인카피 2줄: "(1줄) 왜 {b}" / "(2줄) {pn}일까요?"
VS 레이아웃. 왼쪽=「{b} {pn}」(밝고 우수해 보이게), 오른쪽=일반 유사제품(약간 어두운 톤으로 아쉬워 보이게).
비교 항목 4가지(디자인·사용편의성·기능성·활용도 느낌)지만 항목 글자는 넣지 말고 색·아이콘으로 자사 우위가 한눈에. 제품 사진은 「{b} {pn}」 바로 아래 한 번만.""")
    # 8 디테일
    P_.append(COMMON+f"""[섹션 8: 디테일]
메인카피: "작은 디테일까지 세심하게"
제품 이미지에서 확인 가능한 소재·마감·구조·손잡이·뚜껑·패턴·질감 중 2가지를 확대컷으로.
01: 첫 디테일 제목 + 짧은 설명 / 02: 둘째 디테일 제목 + 짧은 설명. 깔끔한 2단 카드.""")
    # 9 컬러&사이즈
    P_.append(COMMON+f"""[섹션 9: 컬러 & 사이즈]
옵션: {opt or '단일 옵션'} / 사이즈: {sz or '제품 기준'}
실제 존재하는 색상·옵션만. AI가 임의 컬러/옵션 추가 금지. 옵션 1개면 단일 옵션임을 깔끔하게.""")
    # 10 제품정보
    P_.append(COMMON+f"""[섹션 10: 제품 정보]
메인카피: "구매 전 꼭 확인하세요"
치수도(깔끔한 선과 화살표) + 스펙 테이블.
스펙: 브랜드명 {b} / 제품명 {pn} / 색상·옵션 {opt or '단일 옵션'} / 사이즈 {sz or '-'}
하단 안내: "측정 위치와 방법에 따라 약간의 오차가 있을 수 있습니다." 없는 스펙 임의 생성 금지.""")
    return P_

# ---------- 대표이미지 (돈버는하마 대표이미지 프롬프트) ----------
def make_thumbnail(form, refs, out_path):
    """제품 연출 대표이미지 1000x1000 (글씨 없이). refs=레퍼런스 제품사진."""
    pn=form.get("product_name","상품")
    prompt=f"""이건 내가 판매하려는 제품인 '{pn}'이야. 첨부 이미지가 실제 내 제품이야.
1. 이 제품의 용도를 분석한 다음, 실제로 사용하는 모습을 자연스럽게 연출해줘. 내 제품의 디자인·색상·형태는 절대 변형하지 마.
2. 단, 제품 표면의 중국어 로고·상표·워터마크는 제거하고 깨끗한 무지 표면으로.
3. 첨부 이미지의 장점(구도·조명·분위기)만 분석해서 살리되, 배경을 똑같이 복사하지는 마.
4. 클릭율이 높은 쇼핑몰 대표 썸네일처럼, 제품이 가장 돋보이고 깔끔한 프리미엄 감성으로 만들어줘.
5. ⚠️ 이미지 안에 글씨·텍스트·로고는 절대 넣지 마. (오직 제품과 배경 연출만)
정사각 1:1 구도, 제품이 중앙에 크게."""
    raw=_oai_image(prompt, ref_imgs_b64=refs, size="1024x1024", quality="high")
    Image.open(io.BytesIO(raw)).convert("RGB").save(out_path,"JPEG",quality=92)
    return out_path


def _load_product_refs(image_paths, reference_urls, limit):
    """Keep at least one CN-link image in the product references when available."""
    uploaded, linked = [], []
    for path in image_paths:
        try:
            with open(path, "rb") as image_file:
                raw = image_file.read()
            uploaded.append(("image/png" if raw[:4] == b"\x89PNG" else "image/jpeg", base64.b64encode(raw).decode()))
        except Exception:
            pass
    for url in reference_urls:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=20).read()
            linked.append(("image/png" if raw[:4] == b"\x89PNG" else "image/jpeg", base64.b64encode(raw).decode()))
        except Exception:
            pass
        if len(linked) >= max(1, limit // 2):
            break
    selected = []
    if uploaded:
        selected.append(uploaded.pop(0))
    if linked and len(selected) < limit:
        selected.append(linked.pop(0))
    while len(selected) < limit and (uploaded or linked):
        source = uploaded if uploaded else linked
        selected.append(source.pop(0))
    return selected


def run_plan_draft(plan, image_paths, reference_urls, out_path, on_section=None, style_image_paths=None):
    """확정된 기획안으로 글자 없는 저해상도 구간 시안을 만든다."""
    style_refs = []
    for path in (style_image_paths or [])[:2]:
        try:
            with open(path, "rb") as image_file:
                raw = image_file.read()
            style_refs.append(("image/png" if raw[:4] == b"\x89PNG" else "image/jpeg", base64.b64encode(raw).decode()))
        except Exception:
            pass
    product_refs = _load_product_refs(image_paths, reference_urls, 2 if style_refs else 4)
    if not product_refs:
        raise RuntimeError("시안에 사용할 제품 사진이 없습니다")
    generation_refs = product_refs + style_refs

    product = plan.get("product") or {}
    palette = plan.get("palette") or {}
    option_names = product.get("color") or "단일 옵션"
    multi_option = len([value for value in re.split(r"[,/\n]", option_names) if value.strip()]) > 1
    sections = [section for section in (plan.get("sections") or []) if section.get("enabled", True)]
    if not sections:
        raise RuntimeError("사용할 상세페이지 구간이 없습니다")
    completed = 0
    progress_lock = Lock()

    def generate_section(item):
        nonlocal completed
        index, section = item
        template_index = _template_index(section, index)
        layout_notes = [
            "[메인 배너, 860×1920] 실제 제품을 착용하거나 사용하는 감성적인 대표 장면을 크게 보여주세요. 사람의 손, 착용한 다리, 실제 사용 모습처럼 사람이 조금이라도 나오는 장면이 좋습니다. 제품 형태는 상품 링크 사진과 동일하게 유지하되 색상과 옵션은 입력된 내용만 따르세요. 제품이 가장 먼저 보이게 하고 상단 중앙 35%는 보조문구·상품명·짧은 체크포인트 3개가 한 줄로 들어갈 수 있도록 깨끗하고 단순하게 비우세요.",
            "[제품 사용 만족도 설명, 860×860] 화면 중앙에 영문 보조제목, 실제 사용 시 만족할 수 있는 점, 제품 기획 시 고려한 점이 들어갈 넓고 단정한 여백을 만드세요. 배경은 미니멀한 단색 또는 은은한 질감으로 구성하세요.",
            "[제품 후기 배너] 제품만 단독으로 선명하고 크게 보여주고 배경은 은은한 단색으로 구성하세요. 실제 옵션이 여러 개면 모두 함께 정돈해 보여주세요. 제품은 화면 중앙부터 아래쪽에 배치하고, 상단 오른쪽은 짧은 보조문구와 큰 후기 제목용으로 비우세요. 불필요한 소품은 최소화하세요.",
            "[제품 후기 상세내용] 2열×2행의 네 개 후기 카드에 사용할 서로 다른 실제 사용 장면 4컷을 한 화면에 구성하세요. 네 장면은 각각 체크포인트 1·2·3·4의 장점을 보여주고 실제 고객이 직접 촬영한 듯 자연스러워야 합니다. 같은 사진·포즈·카메라 각도를 반복하지 마세요. 각 카드 아래쪽 약 25%는 후기 제목과 짧은 설명용으로 비우고 각 카드 왼쪽 위에는 노란색 별 5개만 표시하세요. 글자·검은 박스·카드 테두리는 생성하지 마세요.",
            "[체크포인트 배너] 사용자가 실제로 제품을 사용하는 뒷모습 또는 옆모습의 생활 장면을 크게 보여주세요. 제품의 사용 방식이 명확히 보여야 합니다. 화면 상단 왼쪽은 큰 질문형 제목용으로, 화면 하단은 이미지 설명용으로 비우세요.",
            "[체크포인트 정리] 제품의 핵심 체크포인트 4가지를 보여주는 상세 클로즈업 4컷을 왼쪽 세로열에 동일한 크기의 원형 크롭을 고려해 배치하세요. 각 사진 오른쪽에는 체크포인트명과 설명이 들어갈 넓은 흰 여백을 남기세요. 상단 중앙 약 22%도 보조문구와 큰 제목용으로 비우세요.",
            "[CHECK POINT 01 상세] 첫 번째 체크포인트가 실제로 드러나는 제품 클로즈업 한 컷과, 같은 장점이 사용 중에 보이는 착용·사용 장면 한 컷을 만드세요. 상단 30%는 체크포인트 번호·짧은 제목·설명용으로 비우고, 아래 큰 이미지 영역에서 제품이 선명하게 보이게 하세요.",
            "[CHECK POINT 02 상세] 두 번째 체크포인트의 구조·소재·기능을 확인할 수 있는 상세 클로즈업과 실제 사용 장면을 구성하세요. 앞 구간과 다른 촬영 각도와 구도를 사용하세요. 상단 30%는 제목과 설명용으로 깨끗하게 비우세요.",
            "[CHECK POINT 03 상세] 세 번째 체크포인트의 효과나 편의성을 확인할 수 있는 제품 상세 컷과 실제 사용 장면을 구성하세요. 앞 구간들과 다른 거리·방향·소품을 사용하세요. 상단 30%는 제목과 설명용으로 깨끗하게 비우세요.",
            "[CHECK POINT 04 상세] 네 번째 체크포인트 또는 추가 활용법을 보여주는 제품 상세 컷과 실제 사용 장면을 구성하세요. 앞 구간들과 중복되지 않는 장소·포즈·카메라 각도를 사용하세요. 상단 30%는 제목과 설명용으로 깨끗하게 비우세요.",
            "[PRODUCT INFO] 제품의 전체 형태·구성품·색상 옵션을 한눈에 확인할 수 있는 깔끔한 누끼형 또는 정돈된 제품 사진을 화면 상단 중앙 영역에 배치하세요. 여러 실제 색상이 있으면 빠짐없이 모두 보여주세요. 최상단은 PRODUCT INFO 제목용, 화면 아래쪽 절반은 제품명·소재·색상·크기·구성·사용법·주의사항용으로 완전히 비우세요.",
        ]
        rating_rule = "후기 상세 구간의 각 카드 왼쪽 위에 지정된 노란색 별 5개만 허용합니다." if template_index == 3 else "별점도 넣지 마세요."
        prompt = f"""CN인사이더 상품 링크 속 이미지와 참고 이미지를 바탕으로, 제품 상세사진의 실제 제품을 사용한 한국 쇼핑몰 상세페이지 배경 장면을 만드세요.
제품명: {product.get('name') or '상품'}
구간: {section.get('type') or section.get('number')}
추가 제품 컷 세부사항: {section.get('image_prompt') or section.get('body') or ''}
실제 색상·옵션명: {option_names}
배경색: {palette.get('background') or '아이보리'}
포인트색: {palette.get('accent') or '차콜'}
템플릿 배치: {layout_notes[min(template_index, len(layout_notes)-1)]}
추가 제품 컷 세부사항이 구간의 목적이나 템플릿 배치와 충돌하면 반드시 템플릿 배치를 우선하세요.
템플릿은 사진 영역과 문구 여백의 위치만 참고하세요. 안내용 검은 박스, 검은 테두리, 초록색 표시, 샘플 풍경, 임시 도형은 절대 따라 만들지 마세요.
문구 여백에는 제품, 인물, 손, 소품이 절대 들어오지 않게 하여 나중에 합성되는 글자와 겹치지 않도록 하세요.
절대 규칙: 이미지 안에 글자, 숫자, 로고, 워터마크, 가짜 리뷰를 넣지 마세요. {rating_rule}
제품의 색상, 형태, 구조, 구성품 수량을 바꾸지 마세요. 세로형 모바일 구도, 차분한 저채도 배경."""
        if style_refs:
            prompt += f"""
첨부 이미지 중 앞의 {len(product_refs)}장은 제품 기준사진이며 제품은 이 사진과 동일해야 합니다.
뒤의 {len(style_refs)}장은 연출 참고사진일 뿐입니다. 참고사진의 인물 얼굴·모델·포즈·몸의 방향·카메라 각도·의상·소품·배경·구도를 그대로 복제하지 마세요.
참고사진과 명확히 구별되는 새로운 모델, 새로운 포즈, 다른 촬영 각도와 패션, 독창적인 장면으로 만드세요."""
        if multi_option:
            prompt += """
여러 색상·옵션이 입력되었습니다. 첨부된 서로 다른 옵션의 실제 제품 사진을 구간마다 번갈아 참고하고,
전체 상세페이지에는 입력된 여러 옵션이 고르게 등장하게 하세요. 한 제품에 옵션을 임의로 합치거나
첨부 사진에 없는 색상·무늬·형태를 새로 만들지 마세요."""
        raw = _oai_image(prompt, ref_imgs_b64=generation_refs, size="1024x1024" if template_index == 1 else "1024x1536", quality="low")
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image = image.resize(_section_size(template_index, low=True), Image.LANCZOS)
        image = compose_plan_text(image, plan, section, template_index)
        section_path = os.path.splitext(out_path)[0] + f"_section_{index}.jpg"
        base_path = os.path.splitext(out_path)[0] + f"_section_{index}_base.jpg"
        Image.open(io.BytesIO(raw)).convert("RGB").resize(_section_size(template_index, low=True), Image.LANCZOS).save(base_path, "JPEG", quality=84)
        image.save(section_path, "JPEG", quality=84)
        if on_section:
            on_section(index, section_path)
        with progress_lock:
            completed += 1
            log(f"저해상도 시안 {completed}/{len(sections)} 생성 완료")
        return image

    # 초안은 속도가 우선이므로 최대 6개 구간을 동시에 생성한다. map으로 최종 순서는 유지한다.
    workers = min(6, len(sections))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        drafts = list(executor.map(generate_section, enumerate(sections)))
    final = Image.new("RGB", (430, sum(image.height for image in drafts)), (255, 255, 255))
    y = 0
    for image in drafts:
        final.paste(image, (0, y)); y += image.height
    final.save(out_path, "JPEG", quality=82)
    return {"product_name": product.get("name") or "상품", "section_count": len(drafts)}


def recompose_plan_section(job_base_path, plan, section_index, show_text=True):
    """Rebuild one low-resolution section from its untouched base image."""
    sections = [section for section in (plan.get("sections") or []) if section.get("enabled", True)]
    if section_index < 0 or section_index >= len(sections):
        raise ValueError("구간 번호가 올바르지 않습니다")
    base_path = job_base_path + f"_section_{section_index}_base.jpg"
    output_path = job_base_path + f"_section_{section_index}.jpg"
    if not os.path.exists(base_path):
        raise FileNotFoundError("글자 없는 원본 시안을 찾지 못했습니다")
    image = Image.open(base_path).convert("RGB")
    if show_text:
        template_index = _template_index(sections[section_index], section_index)
        image = compose_plan_text(image, plan, sections[section_index], template_index)
    image.save(output_path, "JPEG", quality=84)
    return output_path


def run_plan_section_high(plan, section_index, image_paths, reference_urls, out_path, style_image_paths=None,
                          quality="high", output_size=None, compose_text=True):
    """선택한 활성 구간 하나를 최종용 high 품질로 생성한다."""
    sections = [section for section in (plan.get("sections") or []) if section.get("enabled", True)]
    if section_index < 0 or section_index >= len(sections):
        raise RuntimeError("고화질 생성에 필요한 제품 사진 또는 구간이 없습니다")
    style_refs = []
    for path in (style_image_paths or [])[:2]:
        try:
            with open(path, "rb") as image_file:
                raw = image_file.read()
            style_refs.append(("image/png" if raw[:4] == b"\x89PNG" else "image/jpeg", base64.b64encode(raw).decode()))
        except Exception:
            pass
    product_refs = _load_product_refs(image_paths, reference_urls, 2 if style_refs else 4)
    if not product_refs:
        raise RuntimeError("고화질 생성에 필요한 제품 사진이 없습니다")
    generation_refs = product_refs + style_refs
    product, palette, section = plan.get("product") or {}, plan.get("palette") or {}, sections[section_index]
    template_index = _template_index(section, section_index)
    prompt = f"""첨부 사진의 실제 제품을 그대로 유지한 한국 쇼핑몰 상세페이지 배경 장면을 만드세요.
제품명: {product.get('name') or '상품'}
구간: {section.get('type') or section.get('number')}
이미지 계획: {section.get('image_prompt') or section.get('body') or ''}
실제 색상·옵션명: {product.get('color') or '단일 옵션'}
배경색: {palette.get('background') or '아이보리'}
포인트색: {palette.get('accent') or '차콜'}
절대 규칙: 이미지 안에 글자, 숫자, 로고, 워터마크, 가짜 리뷰, 별점을 넣지 마세요.
제품의 색상, 형태, 구조, 구성품 수량을 바꾸지 마세요. 세로형 모바일 구도, 차분한 저채도 배경."""
    if style_refs:
        prompt += f"""
첨부 이미지 중 앞의 {len(product_refs)}장은 제품 기준사진으로 제품을 동일하게 유지하세요.
뒤의 {len(style_refs)}장은 연출 참고용입니다. 인물 얼굴·모델·포즈·몸 방향·카메라 각도·의상·소품·배경·구도를 복제하지 말고 명확히 다른 독창적인 장면을 만드세요."""
    raw = _oai_image(prompt, ref_imgs_b64=generation_refs, size="1024x1024" if template_index == 1 else "1024x1536", quality=quality)
    image = Image.open(io.BytesIO(raw)).convert("RGB").resize(output_size or _section_size(template_index, low=quality != "high"), Image.LANCZOS)
    if compose_text:
        image = compose_plan_text(image, plan, section, template_index)
    image.save(out_path, "JPEG", quality=92 if quality == "high" else 84)
    return {"product_name": product.get("name") or "상품"}

# ---------- 메인: 상세페이지 생성 ----------
def run(url, out_path, category='kitchen'):
    src=P.detect_source(normalize_url(url))
    log(f"[v2] 소스: {src} · 스크래핑...")
    if src=="cninsider":
        data=login_and_scrape(url)
        if not data["main_imgs"]: raise RuntimeError("상품 이미지를 가져오지 못했습니다")
        log(f"제품샷 {len(data['main_imgs'])}장")
        log("폼 자동완성(Opus)..."); form=fill_form(data)
    else:  # 카페24 등 국내 자사몰 (번역X, 문구 재작성)
        data=scrape_cafe24(url)
        if not data.get("main_imgs"): raise RuntimeError("상품 이미지를 가져오지 못했습니다")
        log(f"제품샷 {len(data['main_imgs'])}장")
        log("폼 자동완성(Opus, 국내 문구 재작성)..."); form=fill_form_cafe24(data)
    log(f"→ {form['product_name']} / 소구점 {[s['title'] for s in form.get('sellpoints',[])]}")

    # 레퍼런스 제품사진 3장 (gpt-image edit용)
    refs=[]
    for s in data["main_imgs"][:3]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(s,headers=HDR),timeout=20).read()
            mime="image/png" if raw[:4]==b"\x89PNG" else "image/jpeg"
            refs.append((mime,base64.b64encode(raw).decode()))
        except: pass
    if not refs: raise RuntimeError("레퍼런스 이미지 다운로드 실패")

    prompts=section_prompts(form)
    sections=[]; last_err=""
    for i,pr in enumerate(prompts,1):
        log(f"섹션 {i}/10 생성(gpt-image)...")
        try:
            raw=_oai_image(pr, ref_imgs_b64=refs, size="1024x1536", quality="high")
            sections.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        except Exception as e:
            last_err=str(e); log(f"섹션 {i} 실패: {last_err[:150]}")
            if "결제 한도" in last_err:   # 결제 문제면 나머지도 다 실패 → 즉시 중단
                log("→ OpenAI 결제 문제로 나머지 섹션 생성 중단"); break
    if not sections: raise RuntimeError(last_err[:180] if last_err else "섹션 생성 전부 실패")

    # 폭 860으로 통일 후 세로 스택
    W=860
    rs=[s.resize((W,int(s.height*W/s.width)),Image.LANCZOS) for s in sections]
    total=sum(s.height for s in rs)
    final=Image.new("RGB",(W,total),(255,255,255)); y=0
    for s in rs: final.paste(s,(0,y)); y+=s.height
    final.save(out_path,"JPEG",quality=92)
    log(f"상세페이지 완성 {final.size} ({len(sections)}섹션)")
    # 대표이미지도 생성 (out_path 기준 _thumb.jpg)
    thumb_path=out_path.rsplit(".",1)[0]+"_thumb.jpg"
    try:
        log("대표이미지 생성(gpt-image)..."); make_thumbnail(form, refs, thumb_path)
        log("대표이미지 완성")
    except Exception as e:
        log(f"대표이미지 실패(상세는 정상): {str(e)[:120]}"); thumb_path=None
    return {"product_name":form["product_name"],"size":final.size,"copy":form,"thumb":thumb_path}

if __name__=="__main__":
    import sys
    r=run(sys.argv[1], "/home/ubuntu/cnmaker/result_v2.jpg")
    print(json.dumps({"ok":True,"product_name":r["product_name"],"size":r["size"]},ensure_ascii=False))
