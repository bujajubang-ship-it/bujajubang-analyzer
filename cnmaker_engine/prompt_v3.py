"""Versioned user-supplied prompts and validated, resumable analysis responses."""
import hashlib
import json
from pathlib import Path
import re

TEXT = Path(__file__).with_name('prompt_v3.txt').read_text(encoding='utf-8-sig')
_parts = re.split(r'(?m)^={10,}\n(?=(?:[A-I]|E-\d+)\. )', TEXT.replace('\r\n', '\n'))
BLOCKS = {}
for _part in _parts:
    _match = re.match(r'([A-I]|E-\d+)\. [^\n]*\n', _part)
    if _match:
        BLOCKS[_match[1]] = re.sub(r'(?m)^={10,}\s*$', '', _part[_match.end():]).strip()

TITLES = ['HERO', 'CHECK POINT 3개 요약', '실사용 메인 장면', 'CHECK POINT 01',
          'CHECK POINT 02', 'CHECK POINT 03', '추가 활용 장면', 'DETAIL',
          'COLOR & SIZE', 'PRODUCT INFO', '대표 썸네일']


def fill(text, values):
    # Substitute once: literal braces in a user's product name stay literal.
    return re.sub(r'\{([^{}]+)\}', lambda m: str(values.get(m[1], m[0])), text)


def planning(title, doc, assets):
    return fill(BLOCKS['A'].split('[출력]')[0] + '\n' + BLOCKS['C'], {
        '상품명': title, '사용자 판매 색상': doc.get('color_request', ''),
        '대표 색상': doc.get('primary_color', ''),
        '사진 분석 JSON': json.dumps(assets, ensure_ascii=False)})


def image_prompt(form, index, action='', instruction='', current=False):
    points = form.get('sellpoints', [])
    values = {'상품명': form.get('product_name', ''), '판매 옵션': form.get('option', ''),
              '규격': form.get('size', ''), '수정 요청': instruction}
    for i in range(3):
        point = points[i] if i < len(points) else {}
        values.update({f'소구점{i+1} 제목': point.get('title', ''), f'소구점{i+1} 근거': point.get('desc', '')})
    if current and action in ('edit', 'high'):
        body = BLOCKS['G' if action == 'edit' else 'H']
    else:
        body = BLOCKS[f'E-{index}']
    result = fill(BLOCKS['D'] + '\n' + body + '\n' + BLOCKS['I'], values)
    if current and action in ('edit', 'high'):
        result += '\n확정 소비자용 정보:\n' + json.dumps({k: form.get(k) for k in ('product_name', 'option', 'size', 'sellpoints')}, ensure_ascii=False)
    if index == 10:
        result += '\n이 이미지는 대표 썸네일입니다. 위 세로형·소비자용 문구 지시 대신 정사각형 1:1, 모든 문구와 로고 없는 사진으로 생성하세요.'
    return result


class AnalysisFormatError(ValueError):
    pass


def parse_response(text):
    """Accept one complete object with fences/prose, never merge ambiguous objects."""
    clean = re.sub(r'```(?:json)?', '', text, flags=re.I).strip()
    start = clean.find('{')
    if start < 0 or any(c in clean[:start] for c in '[]'):
        raise AnalysisFormatError('JSON 객체가 없습니다.')
    try:
        value, end = json.JSONDecoder().raw_decode(clean, start)
    except (ValueError, TypeError) as error:
        raise AnalysisFormatError('JSON 응답이 잘렸거나 형식이 올바르지 않습니다.') from error
    if any(c in clean[end:] for c in '{}[]'):
        raise AnalysisFormatError('분석 응답에 여러 JSON이 포함됐습니다.')
    if not isinstance(value, dict):
        raise AnalysisFormatError('JSON 객체가 필요합니다.')
    return value


def validate_photos(value, ids):
    rows = value.get('photos')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise AnalysisFormatError('사진 분석 목록이 올바르지 않습니다.')
    actual = [r.get('id') for r in rows]
    if len(actual) != len(ids) or any(not isinstance(x, str) for x in actual) or set(actual) != set(ids):
        raise AnalysisFormatError('사진 id가 누락·중복되거나 달라졌습니다.')
    if any(type(r.get('usable')) is not bool for r in rows):
        raise AnalysisFormatError('제품 동일성 판단이 누락됐습니다.')


def validate_form(value):
    points = value.get('sellpoints')
    if not isinstance(points, list) or len(points) != 3 or any(
        not isinstance(p, dict) or any(not isinstance(p.get(k), str) or not p[k].strip() for k in ('title', 'desc')) for p in points):
        raise AnalysisFormatError('서로 다른 CHECK POINT 3개가 필요합니다.')
    if len({p['title'].strip() for p in points}) != 3:
        raise AnalysisFormatError('CHECK POINT 제목이 중복됐습니다.')


def request_json(P, content, tokens, validate, path, label, progress):
    """Cache only validated responses; one bounded retry on malformed model output."""
    key = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    target = Path(path) / ('analysis-v3-' + key + '.json')
    if target.exists():
        try:
            value = json.loads(target.read_text(encoding='utf-8'))
            validate(value)
            progress(label + ' — 저장된 분석 사용')
            return value
        except (ValueError, TypeError, AttributeError):
            pass
    for attempt in range(2):
        try:
            request = content if not attempt else content + [{'type': 'text', 'text':
                '직전 응답 형식 검증에 실패했습니다. 완전한 JSON 객체 하나만 출력하세요. 코드블록·설명·추가 JSON 금지. 요청한 모든 id 및 필수 항목을 빠짐없이 반환하세요.'}]
            value = parse_response(P._claude(request, tokens))
            validate(value)
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            temp.replace(target)
            return value
        except AnalysisFormatError as error:
            if attempt:
                raise AnalysisFormatError(label + ' 응답 형식을 두 번 확인했지만 읽지 못했습니다. 재시도하면 저장된 분석 이후부터 이어갑니다.') from error
            progress(label + ' — 응답 형식 오류, 해당 분석 1회 재시도 중')
