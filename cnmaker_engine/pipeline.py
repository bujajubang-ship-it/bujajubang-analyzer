"""CN인사이더 URL → 부자주방 스타일 상세페이지 (통합 파이프라인)"""
import os, json, io, re, base64, urllib.request, glob, time
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from playwright.sync_api import sync_playwright
import cn_transform as T

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = {}
ENV_PATH = os.path.join(BASE, "cn.env")
if os.path.exists(ENV_PATH):
    for l in open(ENV_PATH, encoding="utf-8"):
        if "=" in l:
            k, v = l.strip().split("=", 1); ENV[k] = v
for _name in ("CN_ID", "CN_PW"):
    if os.getenv(_name):
        ENV[_name] = os.getenv(_name)
AKEY = (os.getenv("ANTHROPIC_API_KEY") or ENV.get("ANTHROPIC_API_KEY") or "").strip()
STATE = os.path.join(BASE, "cn_state.json")
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninsider.co.kr/"}
RED=(215,0,16); BLACK=(28,28,28); GRAY=(150,150,150); WHITE=(255,255,255); DGRAY=(80,80,80); W=860
TINT=(247,248,250); CARD=(252,252,253); LINE=(228,230,234); LGRAY=(245,245,245)

def log(msg): print(f"[cnmaker] {msg}", flush=True)

# ---------- 프리미엄 렌더 헬퍼 (입체감) ----------
def _bg(H, tint=TINT):
    """흰색→틴트 세로 그라데이션 배경"""
    img=Image.new("RGB",(W,H),WHITE); px=img.load()
    for y in range(H):
        t=y/max(H-1,1)
        c=(int(255+(tint[0]-255)*t),int(255+(tint[1]-255)*t),int(255+(tint[2]-255)*t))
        for x in range(W): px[x,y]=c
    return img
def _bg_fast(H, tint=TINT):
    img=Image.new("RGB",(W,H),WHITE); d=ImageDraw.Draw(img)
    for y in range(H):
        t=y/max(H-1,1)
        d.line([(0,y),(W,y)],fill=(int(255+(tint[0]-255)*t),int(255+(tint[1]-255)*t),int(255+(tint[2]-255)*t)))
    return img
def _rmask(size,radius):
    m=Image.new("L",size,0); ImageDraw.Draw(m).rounded_rectangle([0,0,size[0]-1,size[1]-1],radius=radius,fill=255); return m
def _shadow(canvas,box,radius=28,blur=20,alpha=55,dy=10):
    x0,y0,x1,y1=[int(v) for v in box]; pad=blur*3
    w,h=(x1-x0)+pad*2,(y1-y0)+pad*2
    sh=Image.new("RGBA",(w,h),(0,0,0,0))
    ImageDraw.Draw(sh).rounded_rectangle([pad,pad,pad+(x1-x0),pad+(y1-y0)],radius=radius,fill=(20,22,28,alpha))
    sh=sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(sh,(x0-pad,y0-pad+dy),sh)
def _cover_resize(img,w,h):
    iw,ih=img.size; sc=max(w/iw,h/ih); nw,nh=max(1,int(iw*sc)),max(1,int(ih*sc))
    r=img.resize((nw,nh),Image.LANCZOS); l,t=(nw-w)//2,(nh-h)//2
    return r.crop((l,t,l+w,t+h))
def _card_img(canvas,img,x,y,maxw,maxh,radius=28,pad=22,card_bg=CARD,shadow=True,valign="top",fit="contain"):
    """라운드 카드+그림자 안에 이미지 배치. (cw,ch,y_bottom) 반환."""
    if not img or maxw<=0 or maxh<=0: return 0,0,y
    base=img.convert("RGB")
    if fit=="cover":
        thumb=_cover_resize(base,maxw-pad*2,maxh-pad*2)
    else:
        thumb=base.copy(); thumb.thumbnail((maxw-pad*2,maxh-pad*2),Image.LANCZOS)
    cw,ch=thumb.width+pad*2,thumb.height+pad*2
    cx=x+(maxw-cw)//2
    if valign=="center" and ch<maxh: y=y+(maxh-ch)//2
    if shadow: _shadow(canvas,(cx,y,cx+cw,y+ch),radius=radius,blur=18,alpha=50,dy=9)
    if pad>0:
        card=Image.new("RGB",(cw,ch),card_bg); card.paste(thumb,(pad,pad))
    else:
        card=thumb
    canvas.paste(card,(cx,y),_rmask((cw,ch),radius))
    return cw,ch,y+ch
def _eyebrow(d,y,text,color=RED,size=22):
    f=_font(size,True); tb=d.textbbox((0,0),text,font=f); tw=tb[2]-tb[0]; th=tb[3]-tb[1]
    cx=W//2; d.text((cx-tw//2,y),text,font=f,fill=color)
    ly=y+th//2; gap=tw//2+20
    d.line([(cx-gap-42,ly),(cx-gap,ly)],fill=LINE,width=2); d.line([(cx+gap,ly),(cx+gap+42,ly)],fill=LINE,width=2)
    return y+th+14
def _split_td(text):
    """'제목 : 설명' 분리 (—,:,- 등 대응)"""
    text=text.strip()
    for sep in (" — ","—"," – ","–"," : ","：",":"," - "):
        if sep in text:
            a,b=text.split(sep,1); a,b=a.strip(),b.strip()
            if a and b: return a,b
    w=text.split()
    if len(w)>5: return " ".join(w[:4])," ".join(w[4:])
    return text,""
def _name_lines(d,name,font_big,maxw):
    """상품명을 (작은윗줄,큰아랫줄)로. 로고중복·괄호 제거+균형분할."""
    name=name.strip()
    for pre in ("부자주방 ","부자주방"):
        if name.startswith(pre): name=name[len(pre):].strip()
    if "(" in name and name.rstrip().endswith(")"): name=name[:name.rfind("(")].strip()
    words=name.split()
    if len(words)<=1: return "",name
    if len(words)==2: return words[0],words[1]
    for k in (3,2,1):
        cand=" ".join(words[-k:])
        if d.textbbox((0,0),cand,font=font_big)[2]<=maxw: return " ".join(words[:-k]),cand
    return " ".join(words[:-1]),words[-1]

# ---------- Claude ----------
def _claude(content, max_tokens=2000):
    body={"model":"claude-opus-4-8","max_tokens":max_tokens,"messages":[{"role":"user","content":content}]}
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=json.dumps(body).encode(),
        headers={"x-api-key":AKEY,"anthropic-version":"2023-06-01","content-type":"application/json"})
    d=json.loads(urllib.request.urlopen(req,timeout=120).read())
    return "".join(c.get("text","") for c in d.get("content",[]) if c.get("type")=="text")

def _json(txt):
    txt=re.sub(r"```[a-z]*","",txt).strip("`\n ")
    s,e=txt.find("{"),txt.rfind("}")
    return json.loads(txt[s:e+1])

def solve_captcha(b64, mime):
    txt=_claude([{"type":"text","text":"이 캡차 이미지의 글자를 정확히 읽어줘. 영문 대소문자와 숫자. 사선 노이즈 무시. 답만 출력, 공백·설명 없이."},
        {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}], 40)
    return re.sub(r"[^A-Za-z0-9]","",txt).strip()

# ---------- 로그인 (세션 없거나 만료시) ----------
def ensure_login(ctx):
    pg=ctx.new_page()
    pg.goto("https://www.cninsider.co.kr/mall/#/homePage", wait_until="domcontentloaded", timeout=40000)
    pg.wait_for_timeout(3000)
    if "login" not in pg.url.lower():
        log("세션 유효"); pg.close(); return True
    log("로그인 필요 → 캡차 풀이")
    pg.goto("https://www.cninsider.co.kr/mall/#/login", wait_until="domcontentloaded", timeout=40000)
    pg.wait_for_timeout(3500)
    for att in range(6):
        info=pg.eval_on_selector_all("img[src^='data:image']","els=>els.map(e=>({w:e.naturalWidth,src:e.src}))")
        cap=max(info,key=lambda x:x['w'])
        b64=cap['src'].split(",",1)[1]; raw=base64.b64decode(b64)
        mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
        code=solve_captcha(b64,mime)
        log(f"캡차[{att+1}]: {code}")
        ins=pg.query_selector_all("input.el-input__inner")
        ins[0].fill(ENV["CN_ID"]); ins[1].fill(ENV["CN_PW"]); ins[2].fill(code)
        pg.wait_for_timeout(400); pg.click("button.logo_btn", timeout=6000); pg.wait_for_timeout(4500)
        if "login" not in pg.url.lower():
            ctx.storage_state(path=STATE); log("로그인 성공·세션저장"); pg.close(); return True
        try:
            for im in pg.query_selector_all("img[src^='data:image']"):
                bb=im.bounding_box()
                if bb and bb["width"]>150: im.click(); break
        except: pass
        pg.wait_for_timeout(1500)
    pg.close(); return False

# ---------- 스크래핑 ----------
def scrape(ctx, url):
    pg=ctx.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(9000)
    if "login" in pg.url.lower():
        pg.close(); raise RuntimeError("로그인 안됨")
    body=pg.inner_text("body")
    imgs=pg.eval_on_selector_all("img","els=>els.map(e=>({w:e.naturalWidth,h:e.naturalHeight,src:e.src}))")
    main=[]; seen=set()
    for i in imgs:
        src=i['src']
        if not src.startswith("http") or 'alicdn' not in src: continue
        key=src.split('!!')[0]
        if i['w']>=400 and abs(i['w']-i['h'])<60 and key not in seen:
            seen.add(key); main.append(src)
    title=""
    for line in body.split("\n"):
        if len(line)>20 and re.search("[가-힣]",line) and not any(x in line for x in ["CN인사이더","장바구니","고객","로그인","멤버","공지"]):
            title=line.strip(); break
    pg.close()
    return {"title":title, "main_imgs":main[:16]}

# ---------- 번역·카피 ----------
def make_copy(data):
    imgs=[]
    for src in data["main_imgs"][:2]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
            mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
            imgs.append((mime,base64.b64encode(raw).decode()))
        except: pass
    prompt=f"""당신은 한국 이커머스 상세페이지 카피라이터입니다.
중국 1688에서 소싱한 상품을 한국 소비자용 상세페이지 카피로 다듬어주세요.
원본 상품명(자동번역체): {data['title']}
요구: 어색한 번역체→자연스러운 한국어, 첨부 이미지로 실제 특징 파악, 검색 잘되는 상품명. 아래 JSON만 출력(코드블록X).
{{"product_name":"자연스러운 상품명","category":"카테고리","headline":"메인 후킹 한 줄","sub_headline":"보조 한 줄","key_features":"• 특징1\\n• 특징2\\n• 특징3\\n• 특징4\\n• 특징5","specs":"소재: \\n색상: \\n사이즈: \\n용도: ","how_to_use":"1. \\n2. \\n3. ","target_customer":"타겟","cta":"구매 유도 카피"}}"""
    content=[{"type":"text","text":prompt}]
    for mime,b64 in imgs: content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    copy=_json(_claude(content, 2000))
    copy["main_imgs"]=data["main_imgs"]
    return copy

# ---------- 이미지 평가 ----------
def score_images(srcs):
    out=[]
    for src in srcs[:16]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
            mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
            b64=base64.b64encode(raw).decode()
            r=_json(_claude([{"type":"text","text":'JSON만:{"chinese_amount":0~10,"product_clarity":0~10,"text_zones":["top"/"bottom"/"left"/"right"]}'},
                {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}], 250))
            r["src"]=src; out.append(r)
        except: pass
    out.sort(key=lambda x:(x.get("chinese_amount",5), -x.get("product_clarity",5)))
    return out

# ---------- 합성 ----------
def _font(sz,bold=True):
    nm="PretendardBold.ttf" if bold else "Pretendard.ttf"
    return ImageFont.truetype(os.path.join(BASE,"fonts",nm), sz)
def _fit(d,txt,maxw,start,bold=True,minsz=22):
    sz=start
    while sz>minsz:
        f=_font(sz,bold)
        if d.textbbox((0,0),txt,font=f)[2]<=maxw: return f
        sz-=2
    return _font(minsz,bold)
def _center(d,txt,f,y,fill): d.text(((W-d.textbbox((0,0),txt,font=f)[2])//2,y),txt,font=f,fill=fill)
def _redbox(d,txt,f,y,padx=34,pady=16):
    tb=d.textbbox((0,0),txt,font=f); tw=tb[2]-tb[0]; th=tb[3]-tb[1]; bw=tw+padx*2; bx=(W-bw)//2
    d.rectangle([bx,y,bx+bw,y+th+pady*2],fill=RED); d.text((bx+padx,y+pady-tb[1]),txt,font=f,fill=WHITE)
    return y+th+pady*2
def _cover(p,zones):
    d=ImageDraw.Draw(p,"RGBA"); w,h=p.size
    for z in zones:
        b={"top":[0,0,w,int(h*0.16)],"bottom":[0,int(h*0.84),w,h],"left":[0,int(h*0.18),int(w*0.34),int(h*0.72)],"right":[int(w*0.66),int(h*0.18),w,int(h*0.72)]}.get(z)
        if not b: continue
        col=p.crop(tuple(b)).resize((1,1)).getpixel((0,0)); d.rectangle(b,fill=col+(235,))
    return p
def _getimg(score,seed):
    src=score["src"]
    if src.startswith("file://"):
        raw=open(src[7:],"rb").read()
    else:
        raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
    p=Image.open(io.BytesIO(T.transform(raw,seed=seed))).convert("RGB")
    zones=score.get("text_zones",[])
    if isinstance(zones,list) and score.get("chinese_amount",0)>1:
        zones=[z for z in zones if isinstance(z,str)]
        p=_cover(p,zones)
    return p

def _wraptext(d,txt,f,maxw):
    """텍스트를 maxw 안에 들어가게 줄 리스트로"""
    words=txt.split(); lines=[]; line=""
    for wd in words:
        t=(line+" "+wd).strip()
        if d.textbbox((0,0),t,font=f)[2]>maxw and line:
            lines.append(line); line=wd
        else: line=t
    if line: lines.append(line)
    return lines

def compose(copy, scores, out_path, category='kitchen'):
    clean=[s for s in scores if s.get("chinese_amount",9)<=2] or scores
    kitchen=(category=="kitchen")
    _acc=None
    for _sc in scores:
        if _sc.get("_accent"): _acc=tuple(_sc["_accent"]); break
    ACC=RED if kitchen else (_acc or (37,99,235))
    def _rb(d,txt,f,y,padx=34,pady=16):
        tb=d.textbbox((0,0),txt,font=f); tw=tb[2]-tb[0]; th=tb[3]-tb[1]; bw=tw+padx*2; bx=(W-bw)//2
        d.rectangle([bx,y,bx+bw,y+th+pady*2],fill=ACC); d.text((bx+padx,y+pady-tb[1]),txt,font=f,fill=WHITE)
        return y+th+pady*2
    feats=[x.strip("• ").strip() for x in copy["key_features"].split("\n") if x.strip()]
    sec=[]
    # ── 헤더 (그라데이션 + 풀블리드 히어로) ──
    H=1480; img=_bg_fast(H); d=ImageDraw.Draw(img); pad=56; y=58
    if kitchen:
        lf=_font(50); lw=d.textbbox((0,0),"부자주방",font=lf)[2]; lx=(W-lw)//2
        d.text((lx,y),"부자",font=lf,fill=RED); d.text((lx+d.textbbox((0,0),"부자",font=lf)[2],y),"주방",font=lf,fill=BLACK)
        y+=d.textbbox((0,0),"부자",font=lf)[3]+10
        d.rectangle([(W-50)//2,y,(W+50)//2,y+4],fill=ACC); y+=34
    cat=" ".join((copy.get("category","") or "주방 전문").split()[:6])
    y=_eyebrow(d,y,cat,color=GRAY,size=24); y+=22
    hl=_wraptext(d,copy["headline"],_font(38),W-120);
    for ln in hl[:2]: _center(d,ln,_fit(d,ln,W-120,38),y,BLACK); y+=50
    y+=14
    fbig=_font(56); l1,l2=_name_lines(d,copy["product_name"],fbig,W-120)
    if l1:
        y=_rb(d,l1,_fit(d,l1,W-160,34),y,padx=30,pady=11); y+=8
        y=_rb(d,l2,_fit(d,l2,W-120,56),y,padx=34,pady=15)
    else:
        y=_rb(d,l2,_fit(d,l2,W-140,52),y,padx=34,pady=15)
    sub=copy.get("sub_headline","")
    if sub:
        y+=18; sf=_fit(d,sub,W-pad*2,26,bold=False)
        for ln in _wraptext(d,sub,sf,W-pad*2)[:2]: _center(d,ln,sf,y,GRAY); y+=_font(26,False).getbbox(ln)[3]+6
    y+=34
    hero_h=H-y-pad
    if hero_h>200:
        _card_img(img,_getimg_cat(clean[0],3,category),pad,y,W-pad*2,hero_h,radius=30,pad=0,fit="cover")
    sec.append(img)

    # ── 제품 상세 + 메인샷 (카드) ──
    H=1080; img=_bg_fast(H); d=ImageDraw.Draw(img); y=64
    y=_eyebrow(d,y,"PRODUCT",color=ACC,size=22); y+=6
    _center(d,"제품 상세",_font(64),y,BLACK); y+=92
    _center(d,"이미지는 실제품과 다소 차이가 있을 수 있습니다.",_font(22,False),y,GRAY); y+=58
    _card_img(img,_getimg_cat(clean[1] if len(clean)>1 else clean[0],5,category),56,y,W-112,H-y-56,radius=28,pad=20,valign="center")
    sec.append(img)

    # ── 핵심 특징 (번호칩 카드) ──
    pad=56; gap=18; chip=50
    inner_w=W-pad*2-chip-44
    cardhs=[]; tmp=ImageDraw.Draw(Image.new("RGB",(10,10)))
    for ft in feats:
        t,desc=_split_td(ft)
        n=len(_wraptext(tmp,desc,_font(25,False),inner_w)) if desc else 0
        cardhs.append(max(96, 44+n*36))
    H=220+sum(cardhs)+gap*max(0,len(feats)-1)+40; img=_bg_fast(H); d=ImageDraw.Draw(img); y=60
    y=_eyebrow(d,y,"KEY FEATURES",color=ACC,size=22); y+=4
    _center(d,"핵심 특징",_font(56),y,BLACK); y+=110
    for i,(ft,ch) in enumerate(zip(feats,cardhs)):
        _shadow(img,(pad,y,W-pad,y+ch),radius=20,blur=14,alpha=30,dy=6)
        img.paste(Image.new("RGB",(W-pad*2,ch),WHITE),(pad,y),_rmask((W-pad*2,ch),20)); d=ImageDraw.Draw(img)
        cy=y+(ch-chip)//2
        d.rounded_rectangle([pad+22,cy,pad+22+chip,cy+chip],radius=13,fill=ACC)
        nf=_font(27); nm=str(i+1); nb=d.textbbox((0,0),nm,font=nf)
        d.text((pad+22+(chip-nb[2])//2,cy+(chip-nb[3])//2-2),nm,font=nf,fill=WHITE)
        tx=pad+22+chip+24; t,desc=_split_td(ft)
        tf=_fit(d,t,inner_w,29,True,minsz=20)
        if desc:
            ty=y+ch//2-_font(29,True).getbbox(t)[3]-2
            d.text((tx,ty),t,font=tf,fill=BLACK)
            df=_font(24,False); dy=ty+_font(29,True).getbbox(t)[3]+8
            for ln in _wraptext(d,desc,df,inner_w): d.text((tx,dy),ln,font=df,fill=GRAY); dy+=36
        else:
            d.text((tx,y+(ch-tf.getbbox(t)[3])//2),t,font=tf,fill=BLACK)
        y+=ch+gap
    sec.append(img)

    # ── 특징 상세: 깨끗한 이미지마다 POINT 섹션 ──
    pts=clean[2:8] if len(clean)>2 else []
    for idx,sc in enumerate(pts,1):
        H=820; img=_bg_fast(H,tint=(248,249,251)); d=ImageDraw.Draw(img); y=56
        pb=f"POINT {idx:02d}"; pf=_font(22); pbw=d.textbbox((0,0),pb,font=pf)[2]+44; pbh=d.textbbox((0,0),pb,font=pf)[3]+22
        bx=(W-pbw)//2; d.rounded_rectangle([bx,y,bx+pbw,y+pbh],radius=pbh//2,fill=ACC)
        d.text((bx+(pbw-d.textbbox((0,0),pb,font=pf)[2])//2,y+10),pb,font=pf,fill=WHITE); y+=pbh+22
        desc=""
        if idx-1<len(feats):
            t,desc=_split_td(feats[idx-1]); t=t.split("(")[0].strip()
            for ln in _wraptext(d,t,_font(44),W-140)[:2]: _center(d,ln,_fit(d,ln,W-140,44),y,BLACK); y+=58
        if desc:
            y+=6; sf=_fit(d,desc,W-140,26,bold=False)
            for ln in _wraptext(d,desc,sf,W-160)[:2]: _center(d,ln,sf,y,GRAY); y+=38
        y+=26
        _card_img(img,_getimg_cat(sc,10+idx,category),pad,y,W-pad*2,H-y-56,radius=28,pad=20,valign="center")
        sec.append(img)

    # ── 다양한 컬러 (라운드 카드 그리드) ──
    rest=clean[8:14] if len(clean)>8 else clean[2:6]
    if len(rest)>=2:
        cell=(W-pad*2-30)//2; rows=(min(len(rest),6)+1)//2
        H=200+rows*(cell+30); img=_bg_fast(H); d=ImageDraw.Draw(img); y=56
        y=_eyebrow(d,y,"GALLERY",color=ACC,size=22); y+=4
        _center(d,"다양한 컬러·각도",_font(46),y,BLACK)
        for i,sc in enumerate(rest[:6]):
            xx=pad+(i%2)*(cell+30); yy=190+(i//2)*(cell+30)
            _card_img(img,_getimg_cat(sc,20+i,category),xx,yy,cell,cell,radius=22,pad=16,fit="cover")
        sec.append(img)

    # ── 스펙표 (라운드 컨테이너) ──
    specs=[s.split(":",1) for s in copy.get("specs","").split("\n") if ":" in s]
    if specs:
        vf=_font(25,False); keyw=230
        rowhs=[]; tmp=ImageDraw.Draw(Image.new("RGB",(10,10)))
        for k,v in specs:
            rowhs.append(max(60,len(_wraptext(tmp,(v.strip() or "-"),vf,W-pad*2-keyw-48))*36+24))
        tbl=sum(rowhs); H=210+tbl+50; img=_bg_fast(H); d=ImageDraw.Draw(img); y=56
        y=_eyebrow(d,y,"SPECIFICATION",color=ACC,size=22); y+=4
        _center(d,"상품 정보",_font(52),y,BLACK); y+=104
        _shadow(img,(pad,y,W-pad,y+tbl),radius=18,blur=14,alpha=28,dy=6)
        img.paste(Image.new("RGB",(W-pad*2,tbl),WHITE),(pad,y),_rmask((W-pad*2,tbl),18)); d=ImageDraw.Draw(img)
        yy=y
        for i,((k,v),rh) in enumerate(zip(specs,rowhs)):
            if i%2==1: d.rectangle([pad,yy,W-pad,yy+rh],fill=(249,250,252))
            if i>0: d.line([pad+16,yy,W-pad-16,yy],fill=LINE,width=1)
            d.line([pad+keyw,yy+10,pad+keyw,yy+rh-10],fill=LINE,width=1)
            d.text((pad+24,yy+rh//2-16),k.strip(),font=_font(26),fill=ACC)
            lines=_wraptext(d,(v.strip() or "-"),vf,W-pad*2-keyw-48); ty=yy+(rh-len(lines)*36)//2
            for ln in lines: d.text((pad+keyw+24,ty),ln,font=vf,fill=DGRAY); ty+=36
            yy+=rh
        sec.append(img)

    # ── CTA (어두운 히어로) ──
    H=560
    if clean:
        bg=_cover_resize(_getimg_cat(clean[-1],30,category),W,H)
        ov=Image.new("RGBA",(W,H),(15,16,20,180)); img=Image.alpha_composite(bg.convert("RGBA"),ov).convert("RGB")
    else:
        img=Image.new("RGB",(W,H),(20,20,24))
    d=ImageDraw.Draw(img); y=70
    lf=_font(40); lw=d.textbbox((0,0),"부자주방",font=lf)[2]; lx=(W-lw)//2
    d.text((lx,y),"부자",font=lf,fill=RED); d.text((lx+d.textbbox((0,0),"부자",font=lf)[2],y),"주방",font=lf,fill=WHITE); y+=70
    cl=_wraptext(d,copy.get("sub_headline",""),_font(32),W-120)
    for ln in cl[:2]: _center(d,ln,_fit(d,ln,W-120,32),y,WHITE); y+=44
    bw=int(W*0.6); bh=78; bx=(W-bw)//2; by=int(H*0.66)
    d.rounded_rectangle([bx,by,bx+bw,by+bh],radius=bh//2,fill=ACC)
    bt="지금 구매하기"; bf=_font(36); btb=d.textbbox((0,0),bt,font=bf)
    d.text((bx+(bw-btb[2])//2,by+(bh-btb[3])//2-2),bt,font=bf,fill=WHITE)
    sec.append(img)

    total=sum(s.height for s in sec); final=Image.new("RGB",(W,total),WHITE); y=0
    for s in sec: final.paste(s,(0,y)); y+=s.height
    final.save(out_path,"JPEG",quality=90)
    return final.size


# ===== 소스별 스크래핑 (카페24 자사몰) =====
def scrape_cafe24(ctx, url):
    pg=ctx.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=50000)
    pg.wait_for_timeout(5000)
    for y in range(0,9000,800):
        pg.evaluate(f"window.scrollTo(0,{y})"); pg.wait_for_timeout(250)
    pg.evaluate("window.scrollTo(0,0)"); pg.wait_for_timeout(800)
    title=pg.title() or ""
    # 상품명 정제
    title=re.sub(r"\s*[-|]\s*[^-|]*$","",title).strip() if len(title)>40 else title
    body=pg.inner_text("body")
    # 이미지: 제품샷(정사각 400+) + 상세(세로 긴 것)
    imgs=pg.eval_on_selector_all("img","els=>els.map(e=>({w:e.naturalWidth,h:e.naturalHeight,src:e.src}))")
    main=[]; detail=[]; seen=set()
    for i in imgs:
        src=i.get("src","")
        if not src.startswith("http") or src in seen: continue
        if any(x in src.lower() for x in ["icon","btn","banner","logo","skin","cctv","detail_b","//s.","blank"]): continue
        low=src.lower()
        if any(x in low for x in ["skinimg","/skin/","cctv","detail_b","common","/img/"]): continue
        if i["w"]>=400 and abs(i["w"]-i["h"])<120:
            seen.add(src); main.append(src)
        elif i["w"]>=600 and i["h"]>=300:
            seen.add(src); detail.append(src)
    pg.close()
    return {"title":title, "main_imgs":main[:12], "detail_imgs":detail[:10]}

def detect_source(url):
    u=(url or "").lower()
    if "cninsider" in u: return "cninsider"
    if "cafe24" in u or "icekhan" in u or "/product/detail.html" in u: return "cafe24"
    if "smartstore.naver" in u or "shopping.naver" in u: return "naver"
    if "coupang.com" in u: return "coupang"
    return "cafe24"  # 기타 자사몰은 카페24식으로 시도


def make_copy_from_imgs(title, imgs_b64):
    """업로드 이미지(b64 리스트) → Opus 카피"""
    prompt=f"""당신은 한국 이커머스 상세페이지 카피라이터입니다.
첨부된 상품 이미지(경쟁사/참고 이미지)를 분석해, 부자주방(업소용 주방기기) 스타일의 새 상세페이지 카피를 만들어주세요.
{('참고 상품명/메모: '+title) if title else ''}
요구: 이미지로 제품 특징 파악, 검색 잘되는 자연스러운 상품명, 실무적 셀링포인트. 아래 JSON만(코드블록X).
{{"product_name":"상품명","category":"카테고리","headline":"메인 후킹 한 줄","sub_headline":"보조 한 줄","key_features":"• 특징1\\n• 특징2\\n• 특징3\\n• 특징4\\n• 특징5","specs":"소재: \\n규격: \\n전원: \\n용도: ","how_to_use":"1. \\n2. \\n3. ","target_customer":"타겟","cta":"구매 유도 카피"}}"""
    content=[{"type":"text","text":prompt}]
    for mime,b64 in imgs_b64[:6]:
        content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    copy=_json(_claude(content,2000))
    return copy

def run_from_images(image_paths, title, out_path, category='kitchen'):
    """네이버·쿠팡 등: 업로드된 이미지 파일들로 상세페이지 생성"""
    imgs_b64=[]; srcs=[]
    for i,fp in enumerate(image_paths):
        raw=open(fp,"rb").read()
        mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else ("image/png" if raw[:4]==b"\x89PNG" else "image/jpeg")
        imgs_b64.append((mime,base64.b64encode(raw).decode()))
        srcs.append("file://"+fp)
    log("카피 생성..."); copy=make_copy_from_imgs(title, imgs_b64); log(f"→ {copy['product_name']}")
    log("이미지 평가...");
    # 업로드 이미지는 로컬파일 → score용 src를 파일경로로, _getimg가 읽게 처리
    scores=[]
    for i,(fp,(mime,b64)) in enumerate(zip(image_paths,imgs_b64)):
        try:
            r=_json(_claude([{"type":"text","text":'JSON만:{"chinese_amount":0~10,"product_clarity":0~10,"text_zones":[]}'},
                {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}],250))
        except: r={"chinese_amount":0,"product_clarity":7,"text_zones":[]}
        r["src"]="file://"+fp; scores.append(r)
    scores.sort(key=lambda x:(x.get("chinese_amount",5),-x.get("product_clarity",5)))
    copy["main_imgs"]=srcs
    if category=="other":
        try:
            tone=_analyze_tone(imgs_b64[0][1], imgs_b64[0][0])
            for sc in scores: sc["_hue"]=tone.get("hue_deg",140); sc["_accent"]=tone.get("accent_rgb",[37,99,235])
        except Exception: pass
    log("합성..."); size=compose(copy,scores,out_path,category=category); log(f"완성 {size}")
    return {"product_name":copy["product_name"],"size":size,"copy":copy}

def compose_selfmall(copy, main_imgs, detail_imgs, out_path):
    """자사몰(카페24): 원본 상세 이미지 정보를 100% 보존.
    부자주방 헤더 + (변형한)대표제품샷 + 원본 상세이미지 전부 + CTA."""
    sec=[]
    # ── 헤더 (로고+헤드라인+빨간박스 상품명+대표 제품샷) ──
    H=1300; img=Image.new("RGB",(W,H),WHITE); d=ImageDraw.Draw(img)
    lf=_font(50); lw=d.textbbox((0,0),"부자주방",font=lf)[2]; lx=(W-lw)//2
    d.text((lx,55),"부자",font=lf,fill=RED); d.text((lx+d.textbbox((0,0),"부자",font=lf)[2],55),"주방",font=lf,fill=BLACK)
    hl=_wraptext(d,copy["headline"],_font(40),W-100); yy=170
    for ln in hl[:2]: _center(d,ln,_fit(d,ln,W-100,40),yy,BLACK); yy+=56
    short=" ".join(copy["product_name"].split()[:3]); _redbox(d,short,_fit(d,short,W-140,56),yy+15)
    # 대표 제품샷(변형) — 정사각 main
    if main_imgs:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(main_imgs[0],headers=HDR),timeout=20).read()
            p=Image.open(io.BytesIO(T.transform(raw,seed=3))).convert("RGB"); p.thumbnail((640,640))
            img.paste(p,((W-p.width)//2,max(yy+150,420)))
        except Exception: pass
    sec.append(img)

    # ── 원본 상세 이미지 전부 (정보 100% 보존, 폭만 W로 맞춤) ──
    used=set()
    for src in detail_imgs:
        if src in used: continue
        used.add(src)
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=25).read()
            di=Image.open(io.BytesIO(raw)).convert("RGB")
            # 폭을 W(860)에 맞춰 리사이즈 (정보 그대로, 폰트만 선명)
            nw=W; nh=int(di.height*W/di.width)
            di=di.resize((nw,nh),Image.LANCZOS)
            sec.append(di)
        except Exception:
            continue

    # ── 상세이미지가 없으면 AI 특징/스펙 섹션으로 보완 ──
    if len(sec)==1:
        feats=[x.strip("• ").strip() for x in copy.get("key_features","").split("\n") if x.strip()]
        if feats:
            rowh=[max(70,len(_wraptext(ImageDraw.Draw(Image.new("RGB",(10,10))),f,_font(31,False),W-180))*46+30) for f in feats]
            H=230+sum(rowh); im=Image.new("RGB",(W,H),WHITE); d=ImageDraw.Draw(im); _redbox(d,"핵심 특징",_font(48),55,padx=46); y=210
            for ft,rh in zip(feats,rowh):
                d.ellipse([64,y+8,92,y+36],fill=RED)
                for ln in _wraptext(d,ft,_font(31,False),W-180): d.text((116,y),ln,font=_font(31,False),fill=BLACK); y+=46
                y+= rh-len(_wraptext(d,ft,_font(31,False),W-180))*46
            sec.append(im)

    # ── CTA ──
    H=420; img=Image.new("RGB",(W,H),WHITE); d=ImageDraw.Draw(img)
    cl=_wraptext(d,copy.get("sub_headline",""),_font(34),W-100); yy=85
    for ln in cl[:2]: _center(d,ln,_fit(d,ln,W-100,34),yy,BLACK); yy+=46
    _redbox(d,"지금 구매하기",_font(46),yy+30,padx=66); sec.append(img)

    total=sum(s.height for s in sec); final=Image.new("RGB",(W,total),WHITE); y=0
    for s in sec: final.paste(s,(0,y)); y+=s.height
    final.save(out_path,"JPEG",quality=88)
    return final.size

# ===== 카테고리별 처리 (주방기구 vs 기타) =====
def _analyze_tone(b64, mime):
    """Opus로 이미지 배경 주조색 분석 → 보색 추천"""
    try:
        r=_json(_claude([{"type":"text","text":'이 상품 이미지를 보고 JSON만(코드블록X). {"bg_hue":"배경 주조색 한국어","shift_to":"원본과 자연스럽게 다른 어울리는 색","hue_deg":배경 색상회전 각도(60~200 정수),"accent_rgb":[R,G,B 상세페이지 강조색으로 쓸, 제품 분위기에 어울리고 원본 메인색과 다른 세련된 색]}'},
            {"type":"image","source":{"type":"base64","media_type":mime,"data":b64}}],250))
        return r
    except Exception:
        return {"hue_deg":140,"accent_rgb":[37,99,235]}

def _bg_recolor(img, hue_deg=140):
    """배경 톤을 은은하게만 이동 (A안: 피부·제품 손상 최소화).
    초록~파랑 등 자연배경 색상대만 살짝 시프트, 살색(주황~빨강)은 보존."""
    import numpy as _np
    hsv=_np.array(img.convert("HSV")).astype(_np.float32)
    h,sat,v=hsv[...,0],hsv[...,1],hsv[...,2]
    H=h/255.0*360.0  # 0~360
    # 살색대(0~50도, 330~360도)는 보존, 그 외(자연배경)만 약하게 회전
    skin=((H<55)|(H>320)).astype(_np.float32)
    satmask=_np.clip((sat-50)/120.0,0,1)
    eff=(1-skin)*satmask*0.30   # 최대 30%만 적용 (은은하게)
    shift=(hue_deg/360.0)*255.0
    hsv[...,0]=(h+shift*eff)%255.0
    # 채도도 살짝 낮춰 파스텔감
    hsv[...,1]=hsv[...,1]*(1-0.12*eff)
    return Image.fromarray(hsv.astype("uint8"),"HSV").convert("RGB")

def _getimg_cat(score, seed, category):
    """제품 이미지 가져오기 + 변형. other면 배경 색변형."""
    src=score["src"]
    if src.startswith("file://"): raw=open(src[7:],"rb").read()
    else: raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
    p=Image.open(io.BytesIO(T.transform(raw,seed=seed))).convert("RGB")
    zones=score.get("text_zones",[])
    if isinstance(zones,list) and score.get("chinese_amount",0)>1:
        p=_cover(p,[z for z in zones if isinstance(z,str)])
    if category=="other":
        p=_bg_recolor(p, score.get("_hue",140))
    return p

# ===== 자사몰 정보 재조립 (OCR → 부자주방 표/불릿) =====
def extract_detail_info(detail_imgs, category='kitchen'):
    """상세 이미지들을 Opus로 읽어 특징·스펙·비교표 텍스트 추출."""
    imgs_b64=[]
    for src in detail_imgs[:8]:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=25).read()
            # 너무 길면 위/아래 분할 (Opus 이미지 크기 제한 대응)
            im=Image.open(io.BytesIO(raw)).convert("RGB")
            if im.height>2200:
                # 위·중·아래 3등분
                parts=3; h=im.height//parts
                for k in range(parts):
                    crop=im.crop((0,k*h,im.width,min((k+1)*h,im.height)))
                    bio=io.BytesIO(); crop.save(bio,"JPEG",quality=85)
                    imgs_b64.append(("image/jpeg",base64.b64encode(bio.getvalue()).decode()))
            else:
                bio=io.BytesIO(); im.save(bio,"JPEG",quality=85)
                imgs_b64.append(("image/jpeg",base64.b64encode(bio.getvalue()).decode()))
        except Exception: continue
    if not imgs_b64: return {}
    # 주방기구: 판매 브랜드·상호·경쟁사명을 '부자주방'으로 치환. 기타제품: 부자주방 로고는 안 넣되 경쟁사 브랜딩은 제거해 깔끔하게.
    if category=="kitchen":
        brand_rule="\n5. [브랜드 치환] 이미지에 보이는 판매 브랜드명·상호·회사명·경쟁사명 등 고유 브랜드/상호 표기는 비교표 셀·헤더·일반 문장 어디에 있든 전부 '부자주방'으로 바꿔 적으세요. 단 '타사','일반제품','기타 제품' 같은 포괄적 비교 표현은 그대로 두세요."
    else:
        brand_rule="\n5. [브랜드 제거] 이미지에 보이는 판매 브랜드명·상호·회사명·경쟁사명 등 고유 브랜드/상호 표기는 비교표 셀·헤더·일반 문장 어디에 있든 전부 삭제하거나 '본 제품' 같은 일반 표현으로 바꿔서, 특정 브랜드·상호가 전혀 드러나지 않게 하세요. (부자주방를 포함해 어떤 브랜드명도 새로 넣지 마세요. '타사','일반제품' 같은 포괄적 비교 표현은 그대로 두세요.)"
    prompt="""첨부된 상품 상세페이지 이미지들에 적힌 모든 글자를 한 글자도 빠뜨리지 말고 읽어서 아래 JSON으로 구조화하세요(코드블록 없이 순수 JSON).

[절대 규칙]
1. 정보를 절대 요약·생략하지 마세요. 이미지에 있는 모든 설명·문장·표·수치를 다 담으세요.
2. 중국어/영어/일본어가 있으면 자연스러운 한국어로 번역해서 넣으세요. (어색한 직역 금지, 매끄러운 한국어)
3. 표(비교표·스펙표)는 모든 행·열을 빠짐없이 옮기세요.
4. 섹션이 여러 개면 sections에 모두 추가하세요. 누락된 섹션이 없어야 합니다."""+brand_rule+"""

{"sections":[{"heading":"섹션 제목","bullets":["문장1(완전한 내용)","문장2"]}],"specs":[["항목","값"]],"compare":{"headers":["구분","컬럼2","컬럼3"],"rows":[["행이름","값","값"]]}}"""
    content=[{"type":"text","text":prompt}]
    for mime,b64 in imgs_b64[:16]:
        content.append({"type":"image","source":{"type":"base64","media_type":mime,"data":b64}})
    try:
        return _json(_claude(content, 8000))
    except Exception as e:
        log(f"정보추출 실패: {e}"); return {}

def compose_remake(copy, main_imgs, detail_info, out_path, category="kitchen"):
    """자사몰 정보를 부자주방 스타일로 새로 디자인 (OCR 재조립)."""
    kitchen = (category=="kitchen")
    ACCENT = RED; hue=140
    if not kitchen and main_imgs:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(main_imgs[0],headers=HDR),timeout=20).read()
            mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
            tone=_analyze_tone(base64.b64encode(raw).decode(),mime)
            hue=tone.get("hue_deg",140); ACCENT=tuple(tone.get("accent_rgb",[37,99,235])); log(f"기타 톤: {tone.get('bg_hue')}→{tone.get('shift_to')} accent{ACCENT}")
        except Exception: ACCENT=(37,99,235)
    sec=[]
    def getp(src,seed,thumb):
        raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
        p=Image.open(io.BytesIO(T.transform(raw,seed=seed))).convert("RGB")
        if not kitchen: p=_bg_recolor(p,hue)
        p.thumbnail(thumb); return p
    def rbox(d,txt,f,y,padx=34,pady=16):
        tb=d.textbbox((0,0),txt,font=f); tw=tb[2]-tb[0]; th=tb[3]-tb[1]; bw=tw+padx*2; bx=(W-bw)//2
        d.rectangle([bx,y,bx+bw,y+th+pady*2],fill=ACCENT); d.text((bx+padx,y+pady-tb[1]),txt,font=f,fill=WHITE)
        return y+th+pady*2

    pad=56
    # ── 헤더 (그라데이션 + cover 히어로) ──
    H=1300; img=_bg_fast(H); d=ImageDraw.Draw(img); y=58
    if kitchen:
        lf=_font(50); lw=d.textbbox((0,0),"부자주방",font=lf)[2]; lx=(W-lw)//2
        d.text((lx,y),"부자",font=lf,fill=RED); d.text((lx+d.textbbox((0,0),"부자",font=lf)[2],y),"주방",font=lf,fill=BLACK)
        y+=d.textbbox((0,0),"부자",font=lf)[3]+10; d.rectangle([(W-50)//2,y,(W+50)//2,y+4],fill=ACCENT); y+=34
    cat=" ".join((copy.get("category","") or "상품 상세").split()[:6])
    y=_eyebrow(d,y,cat,color=GRAY,size=24); y+=22
    hl=_wraptext(d,copy["headline"],_font(38),W-120)
    for ln in hl[:2]: _center(d,ln,_fit(d,ln,W-120,38),y,BLACK); y+=50
    y+=14
    fbig=_font(56); l1,l2=_name_lines(d,copy["product_name"],fbig,W-120)
    if l1:
        y=rbox(d,l1,_fit(d,l1,W-160,34),y,padx=30,pady=11); y+=8; y=rbox(d,l2,_fit(d,l2,W-120,56),y,padx=34,pady=15)
    else:
        y=rbox(d,l2,_fit(d,l2,W-140,52),y,padx=34,pady=15)
    sub=copy.get("sub_headline","")
    if sub:
        y+=18; sf=_fit(d,sub,W-pad*2,26,bold=False)
        for ln in _wraptext(d,sub,sf,W-pad*2)[:2]: _center(d,ln,sf,y,GRAY); y+=_font(26,False).getbbox(ln)[3]+6
    y+=30
    if main_imgs:
        try: _card_img(img,getp(main_imgs[0],3,(2000,2000)),pad,y,W-pad*2,H-y-pad,radius=30,pad=0,fit="cover")
        except Exception: pass
    sec.append(img)

    # ── 추출 정보: 섹션별 (모든 bullet 보존, 라운드 카드) ──
    for si,s in enumerate((detail_info.get("sections") or [])[:8]):
        bullets=s.get("bullets") or []
        if not bullets: continue
        ff=_font(29,False); tmp=ImageDraw.Draw(Image.new('RGB',(10,10)))
        bhs=[max(46,len(_wraptext(tmp,b,ff,W-pad*2-64))*44+22) for b in bullets]
        cardh=sum(bhs)+36
        H=210+cardh+30; im=_bg_fast(H); d=ImageDraw.Draw(im); y=56
        head=s.get("heading","특징")[:24]
        y=_eyebrow(d,y,f"SECTION {si+1:02d}",color=ACCENT,size=20); y+=2
        _center(d,head,_fit(d,head,W-140,46),y,BLACK); y+=88
        _shadow(im,(pad,y,W-pad,y+cardh),radius=20,blur=14,alpha=28,dy=6)
        im.paste(Image.new("RGB",(W-pad*2,cardh),WHITE),(pad,y),_rmask((W-pad*2,cardh),20)); d=ImageDraw.Draw(im)
        by=y+18
        for b,bh in zip(bullets,bhs):
            d.ellipse([pad+26,by+9,pad+26+16,by+25],fill=ACCENT)
            ty=by
            for ln in _wraptext(d,b,ff,W-pad*2-64): d.text((pad+26+30,ty),ln,font=ff,fill=BLACK); ty+=44
            by+=bh
        sec.append(im)

    # ── 제품 추가 이미지 (카드) ──
    if len(main_imgs)>1:
        H=820; im=_bg_fast(H); d=ImageDraw.Draw(im); y=56
        y=_eyebrow(d,y,"PRODUCT",color=ACCENT,size=22); y+=4
        _center(d,"제품 이미지",_font(46),y,BLACK); y+=92
        try: _card_img(im,getp(main_imgs[1],7,(2000,2000)),pad,y,W-pad*2,H-y-pad,radius=28,pad=20,valign="center")
        except Exception: pass
        sec.append(im)

    # ── 스펙표 (재원, 라운드 컨테이너) ──
    specs=detail_info.get("specs") or [[k.strip() for k in s.split(":",1)] for s in copy.get("specs","").split("\n") if ":" in s]
    if specs:
        vf=_font(25,False); keyw=240; tmp=ImageDraw.Draw(Image.new('RGB',(10,10)))
        rowhs=[max(58,len(_wraptext(tmp,str(v) or "-",vf,W-pad*2-keyw-48))*36+24) for k,v in specs]
        tbl=sum(rowhs); H=210+tbl+40; im=_bg_fast(H); d=ImageDraw.Draw(im); y=56
        y=_eyebrow(d,y,"SPECIFICATION",color=ACCENT,size=22); y+=4
        _center(d,"제품 정보",_font(50),y,BLACK); y+=100
        _shadow(im,(pad,y,W-pad,y+tbl),radius=18,blur=14,alpha=28,dy=6)
        im.paste(Image.new("RGB",(W-pad*2,tbl),WHITE),(pad,y),_rmask((W-pad*2,tbl),18)); d=ImageDraw.Draw(im)
        yy=y
        for i,((k,v),rh) in enumerate(zip(specs,rowhs)):
            if i%2==1: d.rectangle([pad,yy,W-pad,yy+rh],fill=(249,250,252))
            if i>0: d.line([pad+16,yy,W-pad-16,yy],fill=LINE,width=1)
            d.line([pad+keyw,yy+10,pad+keyw,yy+rh-10],fill=LINE,width=1)
            d.text((pad+24,yy+rh//2-16),str(k).strip()[:14],font=_font(26),fill=ACCENT)
            lines=_wraptext(d,str(v).strip() or "-",vf,W-pad*2-keyw-48); ty=yy+(rh-len(lines)*36)//2
            for ln in lines: d.text((pad+keyw+24,ty),ln,font=vf,fill=DGRAY); ty+=36
            yy+=rh
        sec.append(im)

    # ── 비교표 (라운드 컨테이너, 모든 행·열 보존) ──
    cmp=detail_info.get("compare") or {}
    if cmp.get("rows"):
        heads=cmp.get("headers",[]); rows=cmp["rows"]; ncol=max(len(heads),max(len(r) for r in rows))
        colw=(W-pad*2)//ncol; tblw=colw*ncol; vf=_font(22,False); tmp=ImageDraw.Draw(Image.new('RGB',(10,10)))
        rowhs=[60]+[max(54,max(len(_wraptext(tmp,str(c),vf,colw-16)) for c in r)*30+18) for r in rows]
        tbl=sum(rowhs); H=200+tbl+30; im=_bg_fast(H); d=ImageDraw.Draw(im); y=56
        y=_eyebrow(d,y,"COMPARE",color=ACCENT,size=22); y+=4
        _center(d,"제품 비교",_font(50),y,BLACK); y+=100
        _shadow(im,(pad,y,pad+tblw,y+tbl),radius=18,blur=14,alpha=26,dy=6)
        im.paste(Image.new("RGB",(tblw,tbl),WHITE),(pad,y),_rmask((tblw,tbl),18)); d=ImageDraw.Draw(im)
        yy=y
        for ci in range(ncol):
            x=pad+ci*colw; d.rectangle([x,yy,x+colw,yy+rowhs[0]],fill=ACCENT)
            txt=str(heads[ci]) if ci<len(heads) else ""
            tw=d.textbbox((0,0),txt[:12],font=_font(22))[2]; d.text((x+(colw-tw)//2,yy+18),txt[:12],font=_font(22),fill=WHITE)
        yy+=rowhs[0]
        for ri,(r,rh) in enumerate(zip(rows,rowhs[1:])):
            if ri%2==1: d.rectangle([pad,yy,pad+tblw,yy+rh],fill=(249,250,252))
            for ci in range(ncol):
                x=pad+ci*colw
                if ci>0: d.line([x,yy+8,x,yy+rh-8],fill=LINE,width=1)
                txt=str(r[ci]) if ci<len(r) else ""; vy=yy+12
                for ln in _wraptext(d,txt,vf,colw-16):
                    lw=d.textbbox((0,0),ln,font=vf)[2]; d.text((x+(colw-lw)//2,vy),ln,font=vf,fill=BLACK if ci==0 else DGRAY); vy+=30
            if ri>0: d.line([pad+12,yy,pad+tblw-12,yy],fill=LINE,width=1)
            yy+=rh
        sec.append(im)

    # ── CTA (어두운 히어로) ──
    H=560
    if main_imgs:
        try:
            ci=getp(main_imgs[0],30,(2000,2000)); bg=_cover_resize(ci,W,H)
            ov=Image.new("RGBA",(W,H),(15,16,20,180)); img=Image.alpha_composite(bg.convert("RGBA"),ov).convert("RGB")
        except Exception: img=Image.new("RGB",(W,H),(20,20,24))
    else: img=Image.new("RGB",(W,H),(20,20,24))
    d=ImageDraw.Draw(img); y=70
    if kitchen:
        lf=_font(40); lw=d.textbbox((0,0),"부자주방",font=lf)[2]; lx=(W-lw)//2
        d.text((lx,y),"부자",font=lf,fill=RED); d.text((lx+d.textbbox((0,0),"부자",font=lf)[2],y),"주방",font=lf,fill=WHITE); y+=70
    cl=_wraptext(d,copy.get("sub_headline",""),_font(32),W-120)
    for ln in cl[:2]: _center(d,ln,_fit(d,ln,W-120,32),y,WHITE); y+=44
    bw=int(W*0.6); bh=78; bx=(W-bw)//2; by=int(H*0.66)
    d.rounded_rectangle([bx,by,bx+bw,by+bh],radius=bh//2,fill=ACCENT)
    bt="지금 구매하기"; bf=_font(36); btb=d.textbbox((0,0),bt,font=bf)
    d.text((bx+(bw-btb[2])//2,by+(bh-btb[3])//2-2),bt,font=bf,fill=WHITE)
    sec.append(img)

    total=sum(s.height for s in sec); final=Image.new("RGB",(W,total),WHITE); y=0
    for s in sec: final.paste(s,(0,y)); y+=s.height
    final.save(out_path,"JPEG",quality=89)
    return final.size

def run(url, out_path, category='kitchen'):
    src=detect_source(url)
    log(f"소스: {src}")
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        if src=="cninsider":
            ctx=b.new_context(storage_state=STATE if os.path.exists(STATE) else None,
                              viewport={"width":1366,"height":3000},locale="ko-KR")
            if not ensure_login(ctx): b.close(); raise RuntimeError("로그인 실패")
            log("스크래핑...")
            try:
                data=scrape(ctx,url)
            except RuntimeError:
                # 세션 실제 만료 → 강제 재로그인 후 재시도
                log("세션만료 감지, 재로그인...")
                import os as _os
                if _os.path.exists(STATE): _os.remove(STATE)
                ctx.close(); ctx=b.new_context(viewport={"width":1366,"height":3000},locale="ko-KR")
                if not ensure_login(ctx): b.close(); raise RuntimeError("재로그인 실패")
                data=scrape(ctx,url)
        else:  # cafe24 등 자사몰
            ctx=b.new_context(viewport={"width":1280,"height":2600},locale="ko-KR",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
            log("스크래핑..."); data=scrape_cafe24(ctx,url)
        log(f"상품명: {data['title'][:40]} / 제품샷 {len(data['main_imgs'])} / 상세 {len(data.get('detail_imgs',[]))}")
        b.close()
    if not data["main_imgs"] and not data.get("detail_imgs"):
        raise RuntimeError("상품 이미지를 가져오지 못했습니다 (차단되었거나 비공개 상품)")
    log("카피 생성..."); copy=make_copy(data); log(f"→ {copy['product_name']}")
    if src!="cninsider" and data.get("detail_imgs"):
        # 자사몰: 상세이미지 글자 읽어 부자주방 스타일로 재조립
        log("상세정보 추출(OCR)..."); info=extract_detail_info(data["detail_imgs"], category=category)
        log("재디자인 합성..."); size=compose_remake(copy, data["main_imgs"], info, out_path, category=category)
    else:
        log("이미지 평가..."); scores=score_images(data["main_imgs"])
        if category=="other":
            try:
                raw=urllib.request.urlopen(urllib.request.Request(data["main_imgs"][0],headers=HDR),timeout=20).read()
                mime="image/jpeg" if raw[:3]==b"\xff\xd8\xff" else "image/png"
                tone=_analyze_tone(base64.b64encode(raw).decode(),mime)
                for sc in scores: sc["_hue"]=tone.get("hue_deg",140); sc["_accent"]=tone.get("accent_rgb",[37,99,235]); sc["_accent"]=tone.get("accent_rgb",[37,99,235])
            except Exception: pass
        log("합성..."); size=compose(copy,scores,out_path,category=category)
    return {"product_name":copy["product_name"],"size":size,"copy":copy}

if __name__=="__main__":
    import sys
    r=run(sys.argv[1], "/home/ubuntu/cnmaker/result.jpg")
    print(json.dumps({"ok":True,"product_name":r["product_name"],"size":r["size"]},ensure_ascii=False))
