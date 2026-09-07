"""Consumer copy instructions and editable category-specific specifications."""
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
