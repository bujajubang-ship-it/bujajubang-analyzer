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


def product_photo(doc,asset,path,G,quality):
    image=Image.open(path/asset['file']).convert('RGB')
    l,t,r,b=crop_box(asset)
    image=image.crop((round(l*image.width),round(t*image.height),round(r*image.width),round(b*image.height)))
    target=doc.get('primary_color') or doc.get('color_request','').split(',')[0].strip()
    aliases={'화이트':'흰색','white':'흰색','블랙':'검정','black':'검정','블랙색':'검정','블랙컬러':'검정'}
    normalized=lambda c:aliases.get(c.lower().strip(),c.lower().strip())
    needs_color=(target and normalized(target) not in [normalized(c) for c in asset.get('colors',[])]) or (not target and doc.get('color_refs'))
    if needs_color:
        buf=io.BytesIO();image.save(buf,'JPEG',quality=95)
        refs=[('image/jpeg',base64.b64encode(buf.getvalue()).decode())]+[('image/jpeg',base64.b64encode((path/n).read_bytes()).decode()) for n in doc.get('color_refs',[])]
        key=hashlib.sha256(json.dumps([refs,target]).encode()).hexdigest()[:24]
        cache=path/('recolor-'+key+'.jpg')
        if cache.exists():return Image.open(cache).convert('RGB')
        raw=G._oai_image('첫 사진의 제품 색상만 '+(target or '나머지 색상 기준 사진의 대표 색상')+'으로 변경하세요. 제품 소재·짜임·마름모·패턴·길이·두께·형태·비율·봉제선과 포즈·배경·조명은 그대로. 새 기능·무늬·구성품·문구 생성 금지. 다른 사진은 색조만 참고. 제품 외 피부·배경·신발 재착색 금지.',ref_imgs_b64=refs,size='1024x1536',quality=quality)
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
    points=doc['form'].get('sellpoints',[])
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
