"""Consumer copy instructions and editable category-specific specifications."""
import hashlib
import json
COPY_RULES = '''
[최우선 한국 소비자용 작문 규칙]
사진 원문은 직역하지 말고 한국 쇼핑몰에서 일상적으로 쓰는 표현으로 의역한다.
원문·번역 자료를 종합하여 서로 다른 핵심 장점 3개와 상품정보에 자연스럽게 녹인다.
작은 소재·형태·구조의 단서에서도 사용 장면과 이점을 적극적으로 확장해 매력적인 마케팅 초안을 쓴다.
제목은 12~22자 안팎의 짧고 감각적인 문장, 설명은 45~75자 안팎으로 구조와 사용감을 연결한다.
예: 넓은 밴드 → 넓게 감싸주고, 움직일 때도 든든하게. 도톰한 바닥 → 발이 닿는 순간, 폭신한 착용감.
기계적인 직역, 같은 장점 반복, 전문용어 나열은 피한다. 사용자가 최종 문구를 검토한다.
원문 광고가 설명하는 흡한·완충·미끄럼 방지 등의 장점도 상품이 제시한 단서로 활용한다.
별도 시험 자료가 없다는 이유만으로 이런 장점을 모두 삭제하고 외형 설명만 쓰지 않는다.
예: 缓震와 도톰한 바닥 조직 → 걸음마다 폭신하게, 발바닥을 편안하게 받쳐줘요.
형태 설명에서 끝내지 말고 매력적인 사용감으로 연결하되 절대적 성능 보장 표현은 피한다.
추정한 사용감·성능은 review_notes 배열에 검토할 내용으로 별도 기록한다. 소비자용 문구에 검토 표시를 넣지 않는다.
소재명·치수·수량·제조국·주의사항은 사실 그대로 보존한다. 없는 수치·인증·시험 결과·의학적 효능을 만들지 않는다.
참고용 사진의 문구·스펙·세트 수량은 가져오지 않는다.

[추가 JSON 필드]
product_info: [{"label":"항목명","value":"확인된 값 또는 빈 문자열"}].
제품 종류를 판단해 적합한 항목을 추천한다. 의류는 소재·컬러·사이즈·제조국·세탁방법,
스포츠용품은 규격·무게·소재·구성품·제조국·주의사항,
식기는 크기·용량·재질·제조국·전자레인지/식기세척기 사용 가능 여부 등.
확인되지 않은 값은 빈 문자열로 두고, 사용 가능 여부를 재질만으로 추측하지 않는다.
review_notes: ["사용자가 검토할 추정 표현과 근거"].
photo_plan: 각 구간 번호 문자열을 키로 실제 상품 사진 id 배열을 반환한다.
0 HERO, 1 글자만 있는 장점 요약(사진 없음), 2 실사용, 3~5 각 장점의 디테일,
6 추가 활용, 7 DETAIL(서로 다른 디테일 2장), 8 PRODUCT INFO 누끼용 전체 제품, 10 썸네일.
각 장점을 실제로 보여주는 사진을 첫 번째에 배치하고 충분한 사진이 있으면 3~5의 첫 사진을 중복하지 않는다.
제품 정보를 담은 글자 위주 사진은 사실 추출에만 사용한다.
'''


def normalize_rows(rows):
    if not isinstance(rows, list) or len(rows)>24:
        raise ValueError('상품정보는 최대 24개 항목으로 입력해 주세요.')
    result=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('label'),str) or not isinstance(row.get('value',''),str):
            raise ValueError('상품정보 항목과 내용을 확인해 주세요.')
        label=row['label'].strip()[:60]
        if label: result.append({'label':label,'value':row.get('value','').strip()[:600]})
    return result


def recommend(form):
    rows=normalize_rows(form.get('product_info',[]))
    if rows: return rows
    kind=(form.get('category','')+' '+form.get('product_name','')).lower()
    labels=['상품명','컬러','사이즈','소재','제조국','구성','주의사항']
    if any(k in kind for k in ('양말','의류','옷','니삭스','apparel','clothing')):labels+=['세탁 방법']
    elif any(k in kind for k in ('식기','컵','접시','그릇','주방','tableware','kitchen')):labels+=['용량','전자레인지 사용','식기세척기 사용']
    elif any(k in kind for k in ('스포츠','운동','sports')):labels+=['무게','사용 방법']
    values={'상품명':form.get('product_name',''),'컬러':form.get('option',''),'사이즈':form.get('size','')}
    return [{'label':k,'value':values.get(k,'')} for k in labels]


def section_copy(doc, G, path, progress):
    """Derive hero/detail copy from the latest reviewed summary, without changing it."""
    import prompt_v3 as V
    source={'version':'section-copy-v2-short-emotional','model':getattr(G.P,'ANALYSIS_CACHE_ID','legacy'),'product_name':doc['form']['product_name'],'sellpoints':doc['form']['sellpoints'],
            'evidence':[{'description':a.get('description',''),'translation':a.get('translation','')}
                        for a in doc.get('assets',[]) if a.get('selected',True) and a.get('usable') and a.get('origin')!='reference']}
    key=hashlib.sha256(json.dumps(source,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if doc.get('section_copy_key')==key and doc.get('section_copy'):return doc['section_copy']
    prompt='''[구간별 문구 변주]
사용자가 확정한 핵심 장점 3개를 기준으로 구간별 문구를 작성한다.
2번 CHECK POINT 3개 요약에는 확정 문구를 그대로 사용하므로 절대 수정하지 않는다.
HERO는 제품 장점에 감성과 고객이 바라는 경험을 더한 짧은 한 줄(12~36자)을 쓴다. 예: 최상의 퍼포먼스를 위한 탄탄한 파트너. 2~3단어 제한 없음.
CHECK POINT 01~03은 각각 대응하는 확정 장점을 고객이 이해하기 쉬운 일상적 말로 확장한다.
제목은 요약 제목과 다르게 쓰고, 설명은 구체적인 사용감만 남겨 45~65자 안팎, 모바일 조판에서 2~3줄로 쓴다. 장황한 사용 장면 나열 금지.
원문 번역의 장점과 구조를 활용해 풍부하게 작문한다. 요약 문장을 그대로 복사하거나 세 구간을 같은 내용으로 쓰지 않는다.
사용자가 지운 장점이나 반대 의미를 다시 넣지 말고 확정 장점의 의도를 최우선으로 한다.
없는 수치·인증·시험 결과·의학적 효능·소재·구성품은 만들어내지 않는다.
JSON 객체 하나만 반환: {"hero":"감성적인 한 줄","details":[{"title":"확장 제목","desc":"확장 설명"}, ... 총 3개]}.
'''+json.dumps(source,ensure_ascii=False)
    def validate(value):
        if not isinstance(value.get('hero'),str) or not 4<=len(value['hero'].strip())<=36 or '\n' in value.get('hero',''):
            raise V.AnalysisFormatError('HERO 문구는 36자 이하의 감성적인 한 줄이어야 합니다.')
        V.validate_form({'sellpoints':value.get('details')})
        from mobile_layout import lines
        if any(len(lines(p['desc'],750,32))>3 for p in value['details']):
            raise V.AnalysisFormatError('설명을 줄여 모바일 3줄 이내로 작성하세요.')
        for point,summary in zip(value['details'],source['sellpoints']):
            if point['title'].strip()==summary['title'].strip() or point['desc'].strip()==summary['desc'].strip():
                raise V.AnalysisFormatError('개별 CHECK POINT는 요약과 다른 확장 문구가 필요합니다.')
    progress('구간별 문구 작성 중 — HERO 감성 문구·CHECK POINT 2~3줄 작성')
    result=V.request_json(G.P,[{'type':'text','text':prompt}],4000,validate,path,'구간별 문구',progress)
    doc.update(section_copy_key=key,section_copy=result)
    return result
