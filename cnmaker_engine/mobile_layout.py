"""Mobile composition: retain source pixels for detail, render editable copy separately."""
import base64
import hashlib
import io
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import prompt_v3 as V


def prompt(form,index,action='',instruction='',current=False):
    mapped=10 if index==9 else index
    if index==0:form=dict(form,sellpoints=[])
    text=V.image_prompt(form,mapped,action,instruction,current)
    rules={0:'HERO는 큰 메인 사진 한 장과 상품명·짧은 핵심 문구만. 확대컷·디테일컷·원형 인셋·작은 사진·아이콘을 절대 넣지 마세요.',
        1:'모바일 한 열에 CHECK POINT 3개를 세로로 배치. 오른쪽 큰 사진 없음. 글자는 크고 설명은 짧게.',
        2:'실사용 메인 장면은 오직 사진만. 상품명·한글·영문·숫자·아이콘·문구 모두 없음.',
        6:'추가 활용 장면은 오직 사진만. 상품명·한글·영문·숫자·아이콘·문구 모두 없음. 메인 사용 장면과 다른 포즈·환경·각도.',
        7:'DETAIL 제목 외에는 번호·제목·설명·하단 글자가 없음. 실제 제품 사진을 그대로 활용.',
        8:'COLOR & SIZE와 PRODUCT INFO는 이 한 장에 통합. 상단에는 제품 누끼컷 한 장과 실제 판매 컬러 탭만. 하단에는 상품명·판매 옵션·확인된 사이즈 정보. 사진 반복·추가 상세컷 없음. 정보가 없으면 해당 행을 생략.'}
    return text+'\n[사용자가 확정한 최우선 레이아웃]\n'+rules.get(index,'글자 없는 정사각형 썸네일.')


def crop_box(asset):
    value=asset.get('crop') or asset.get('product_bbox') or [0,0,1,1]
    if not isinstance(value,list) or len(value)!=4 or any(type(x) not in (int,float) for x in value):return [0,0,1,1]
    l,t,r,b=[max(0,min(1,x)) for x in value]
    return [l,t,r,b] if r-l>.02 and b-t>.02 else [0,0,1,1]


def product_photo(doc,asset,path,G,quality,cutout=False):
    image=Image.open(path/asset['file']).convert('RGB')
    l,t,r,b=crop_box(asset)
    image=image.crop((round(l*image.width),round(t*image.height),round(r*image.width),round(b*image.height)))
    target=doc.get('primary_color') or doc.get('color_request','').split(',')[0].strip()
    aliases={'화이트':'흰색','white':'흰색','블랙':'검정','black':'검정','블랙색':'검정','블랙컬러':'검정'}
    normalized=lambda c:aliases.get(c.lower().strip(),c.lower().strip())
    needs_color=(target and normalized(target) not in [normalized(c) for c in asset.get('colors',[])]) or (not target and doc.get('color_refs'))
    cleanup=bool(asset.get('has_overlay') or asset.get('original_text'))
    if needs_color or cleanup or cutout:
        buf=io.BytesIO();image.save(buf,'JPEG',quality=95)
        refs=[('image/jpeg',base64.b64encode(buf.getvalue()).decode())]+[('image/jpeg',base64.b64encode((path/n).read_bytes()).decode()) for n in doc.get('color_refs',[])]
        key=hashlib.sha256(json.dumps(['clean-product-v1',refs,target,quality,cleanup,cutout]).encode()).hexdigest()[:24]
        cache=path/('clean-product-'+key+'.jpg')
        if cache.exists():return Image.open(cache).convert('RGB')
        task='첫 사진을 최소한으로 편집하세요. 제품 소재·짜임·마름모·패턴·길이·두께·형태·비율·봉제선은 원본과 동일하게 보존. 제품을 재디자인하지 마세요. 제품 고유의 무늬·로고는 보존. '
        if cleanup or cutout:
            task+='사진에 인쇄된 중국어·한글·영문 설명, 제목, 배지, 화살표, 안내선, 색상 나열 인셋 등 편집 그래픽을 모두 지우고 주변 배경으로 자연스럽게 복원. 제품 사진 자체만 남기세요. '
        if cutout:
            task+='PRODUCT INFO용 제품 누끼컷: 판매 제품 하나만 순백 배경 중앙에 전체 형태가 잘 보이게 배치. 사람·다리·손·신발·소품·착용 장면과 다른 색상 제품 나열을 제거. 상품의 실제 길이와 실루엣 보존. 글자·그림자 장식 없음. '
        else:
            task+='제품 포즈·배경·조명과 제품을 착용한 사람은 유지. '
        if needs_color:
            task+='제품 색상만 '+(target or '나머지 색상 기준 사진의 대표 색상')+'으로 변경. 다른 사진은 색조만 참고. 피부·배경·신발 재착색 금지. '
        task+='새 기능·무늬·구성품·글자를 생성하지 마세요.'
        raw=G._oai_image(task,ref_imgs_b64=refs,size='1024x1536',quality=quality)
        image=Image.open(io.BytesIO(raw)).convert('RGB');image.save(cache,'JPEG',quality=95)
    return image


def font(size,bold=False):
    root=Path(__file__).with_name('fonts')
    return ImageFont.truetype(str(root/('Pretendard-ExtraBold.otf' if bold else 'Pretendard-Regular.otf')),size)


def lines(text,width,size):
    f=font(size);result=[];line=''
    for ch in str(text):
        if ch=='\n' or f.getlength(line+ch)>width:
            result.append(line);line='' if ch=='\n' else ch
        else:line+=ch
    if line:result.append(line)
    return result


def compose(doc,index,chosen,path,quality,G,instruction=''):
    """Photo pixels are pasted, not regenerated, unless color conversion is requested."""
    points=doc['form'].get('sellpoints',[]) if index==1 else doc.get('section_copy',{}).get('details',doc['form'].get('sellpoints',[]))
    if index==1:
        heights=[max(360,80+len(lines(p['title'],750,48))*62+len(lines(p['desc'],750,32))*45+60) for p in points[:3]]
        canvas=Image.new('RGB',(860,200+sum(heights)),'white');draw=ImageDraw.Draw(canvas)
        draw.text((55,50),'CHECK POINT',font=font(52,True),fill='#222222')
        top=170
        for i,p in enumerate(points[:3]):
            y=top;top+=heights[i]
            draw.text((55,y),f'0{i+1}',font=font(42,True),fill='#777777');y+=65
            for line in lines(p['title'],750,48):draw.text((55,y),line,font=font(48,True),fill='#222222');y+=62
            for line in lines(p['desc'],750,32):draw.text((55,y+20),line,font=font(32),fill='#444444');y+=45
    elif index==8:
        return product_info(doc,chosen,path,quality,G)
    else:
        assets=chosen[:2] if index==7 else chosen[:1]
        photos=[product_photo(doc,a,path,G,quality) for a in assets]
        heading='DETAIL' if index==7 else f'CHECK POINT {index-2:02d}'
        copy=[] if index==7 else [(points[index-3]['title'],48,True),(points[index-3]['desc'],32,False)]
        height=140+sum(len(lines(text,750,size))*(size+14)+25 for text,size,_ in copy)
        resized=[]
        for photo in photos:
            im=ImageOps.contain(photo,(800,1100));resized.append(im);height+=im.height+25
        canvas=Image.new('RGB',(860,height),'white');draw=ImageDraw.Draw(canvas);y=40
        draw.text((45,y),heading,font=font(40,True),fill='#333333');y+=75
        for text,size,bold in copy:
            for line in lines(text,750,size):draw.text((55,y),line,font=font(size,bold),fill='#222222');y+=size+14
            y+=25
        for im in resized:canvas.paste(im,((860-im.width)//2,y));y+=im.height+25
    out=io.BytesIO();canvas.save(out,'JPEG',quality=95);return out.getvalue()


def product_info(doc,chosen,path,quality,G):
    from merchandising import recommend
    form=doc['form']
    rows=[r for r in recommend(form) if r['value'].strip()]
    # Explicitly deleted rows remain deleted; render only confirmed, nonempty values.
    if 'product_info' in form:rows=[r for r in form['product_info'] if r['value'].strip()]
    photo=product_photo(doc,chosen[0],path,G,quality,cutout=True)
    photo=ImageOps.contain(photo,(750,780))
    colors=[c.strip() for c in (doc.get('color_request') or form.get('option','')).replace('，',',').split(',') if c.strip()]
    color_lines=lines(' · '.join(colors),750,30) if colors else []
    heights=[max(75,len(lines(r['value'],500,29))*43+30,len(lines(r['label'],180,28))*42+30) for r in rows]
    height=180+photo.height+len(color_lines)*45+60+sum(heights)
    canvas=Image.new('RGB',(860,height),'white');draw=ImageDraw.Draw(canvas)
    draw.text((50,45),'PRODUCT INFO',font=font(48,True),fill='#222222')
    y=145;canvas.paste(photo,((860-photo.width)//2,y));y+=photo.height+25
    for line in color_lines:
        draw.rounded_rectangle((50,y,810,y+40),radius=8,fill='#f3f4f6')
        draw.text((65,y+2),line,font=font(30),fill='#333333');y+=45
    y+=25
    for row,h in zip(rows,heights):
        draw.line((50,y,810,y),fill='#dddddd',width=2)
        for n,line in enumerate(lines(row['label'],180,28)):draw.text((60,y+15+n*42),line,font=font(28,True),fill='#333333')
        for n,line in enumerate(lines(row['value'],500,29)):draw.text((275,y+15+n*43),line,font=font(29),fill='#333333')
        y+=h
    out=io.BytesIO();canvas.save(out,'JPEG',quality=95);return out.getvalue()
