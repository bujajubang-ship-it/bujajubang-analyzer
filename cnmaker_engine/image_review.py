"""Check product structure, framing and selling color before accepting edited images."""
import base64
import json
import re
import prompt_v3 as V


class ProductImageError(RuntimeError):
    pass


def target_color(doc):
    return (doc.get('primary_color') or re.split(r'[,，、;/\n]',doc.get('color_request') or doc.get('form',{}).get('option',''))[0]).strip()


def check(G, original, result, doc, path, *, cutout=False, scene=False, clean=True):
    target=target_color(doc)
    task='''[상품이미지 검증]
첫 사진은 실제 상품 원본, 두 번째는 생성 결과다. 제품만 비교하고 사람의 신체나 배경 색상은 판매 색상 검사에서 제외한다.
제품 기장/폭/발 또는 부품 크기/밴드 비율, 소재 짜임과 무늬가 명백하게 왜곡되면 structure_ok=false.
색상 지시가 있으면 원단 확대 사진과 작은 제품까지 해당 색상인지 확인. 다른 판매 색상도 대표 색상 지시와 다르면 color_ok=false.
제품 전체가 보이는 원본인데 결과에서 제품이나 착용 포즈가 불필요하게 잘리거나 확대되면 framing_ok=false.
원본 자체가 소재 확대 사진인 경우 확대를 허용하며, 없는 부분을 억지로 복원할 필요는 없다.
새 포즈/배경을 허용하는 장면은 원본과 포즈가 달라도 되지만 상품 형태·실루엣은 일치해야 한다.
누끼는 사람/배경을 제거할 수 있지만 제품 길이와 비율을 유지하고 전체가 보여야 한다.
글자 정리를 요구한 경우 결과에 중국어/설명 화살표/색상 인셋이 남으면 clean_ok=false.
JSON 하나: {"structure_ok":true,"color_ok":true,"framing_ok":true,"clean_ok":true,"reason":"문제 있을 때만 간결한 이유"}.
'''+json.dumps({'대표 판매 색상':target,'누끼':cutout,'새 포즈 허용':scene,'글자 정리':clean},ensure_ascii=False)
    content=[{'type':'text','text':task}]+[{'type':'image','source':{'type':'base64','media_type':'image/jpeg','data':base64.b64encode(raw).decode()}} for raw in (original,result)]
    def validate(value):
        if any(type(value.get(k)) is not bool for k in ('structure_ok','color_ok','framing_ok','clean_ok')):
            raise V.AnalysisFormatError('상품 이미지 검증 응답 형식 오류')
    result=V.request_json(G.P,content,1800,validate,path,'제품 비율·색상 검증',lambda s:None)
    failed=[k for k in ('structure_ok','color_ok','framing_ok','clean_ok') if not result[k] and (k!='clean_ok' or clean)]
    if failed:
        raise ProductImageError('제품 비율·색상·구도 검증 실패: '+str(result.get('reason') or ', '.join(failed))[:350])
    return result
