"""CN인사이더 → gpt-image 상세페이지 (돈버는하마 프롬프트 방식, v2)
기존 pipeline.py의 로그인/스크랩/Claude를 재사용하고, 생성만 gpt-image로 교체."""
import os, json, io, re, base64, urllib.request, urllib.error, time
from PIL import Image
import pipeline as P   # 기존 모듈 재사용 (ensure_login, scrape, _claude, _json, log 등)

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = P.ENV
OKEY = (os.getenv("OPENAI_API_KEY") or P.ENV.get("OPENAI_API_KEY") or "").strip()
HDR = P.HDR
IMG_MODEL = "gpt-image-2"   # 최신 최고급 — 로고제거·한글 우수 (gpt-image-1 대비 검증완료)
log = P.log

def normalize_url(url):
    """login?redirect=... 형태면 실제 상품 URL로 변환."""
    import urllib.parse
    if "#/login" in url and "redirect" in url:
        frag=url.split("#",1)[1]
        q=frag.split("?",1)[1] if "?" in frag else ""
        rd=urllib.parse.unquote(dict(urllib.parse.parse_qsl(q)).get("redirect",""))
        if rd: return "https://www.cninsider.co.kr/mall/#"+rd
    return url

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
        imgs=pg.eval_on_selector_all("img","els=>els.map(e=>({w:e.naturalWidth,h:e.naturalHeight,src:e.src}))")
        main=[]; seen=set()
        for i in imgs:
            src=i.get('src','')
            if not src.startswith("http") or 'alicdn' not in src: continue
            key=src.split('!!')[0]
            if i['w']>=400 and abs(i['w']-i['h'])<60 and key not in seen:
                seen.add(key); main.append(src)
        title=""
        for line in body.split("\n"):
            if len(line)>20 and re.search("[가-힣]",line) and not any(x in line for x in ["CN인사이더","장바구니","고객","로그인","멤버","공지","환영"]):
                title=line.strip(); break
        b.close()
        return {"title":title, "main_imgs":main[:16]}

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
