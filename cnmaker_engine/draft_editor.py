"""Persistent draft editing around the original ten-section CN layout."""
import base64
import io
import json
import os
import sys
from pathlib import Path
import re
import threading
import time
import urllib.request
import uuid
import zipfile

from PIL import Image, ImageOps
import gptmaker as G
import source_photos as S
import prompt_v3 as V3
import selection_flow as F

ROOT = Path(__file__).resolve().parent / 'results' / 'drafts'
LOCK = threading.RLock()
ACTIVE = set()
TITLES = V3.TITLES


class DraftError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def folder(jid):
    if not isinstance(jid, str) or not re.fullmatch(r'[0-9a-f]{12}', jid):
        raise DraftError('작업 번호를 확인해 주세요.')
    return ROOT / jid


def save(doc):
    target = folder(doc['id']) / 'state.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    doc['updated'] = time.time()
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, target)


def read(jid):
    try:
        doc = json.loads((folder(jid) / 'state.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise DraftError('작업을 찾을 수 없습니다.', 404)
    if doc['status'] == 'running' and jid not in ACTIVE:
        doc.update(status='partial', message='작업이 중단되었습니다. 실패 구간 재시도로 이어서 만드세요.')
        for section in doc['sections']:
            if section['status'] in ('running', 'pending'):
                section.update(status='error', error='작업 중단 — 다시 시도해 주세요.')
        save(doc)
    return doc


def public(doc):
    result = {key: value for key, value in doc.items() if key not in ('refs', 'url', 'inputs', 'assets', 'color_refs', 'style_refs')}
    result['assets'] = [{key: value for key, value in asset.items() if key not in ('file', 'url')} for asset in doc.get('assets', [])]
    result['color_samples'] = [name[:-4] for name in doc.get('color_refs', [])]
    return result


def status(jid):
    with LOCK:
        return public(read(jid))


def history():
    with LOCK:
        paths = sorted(ROOT.glob('*/state.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
        return {'items': [public(read(p.parent.name)) for p in paths]}


def jpeg(raw, maximum=1600):
    if len(raw) > 12 * 1024 * 1024:
        raise DraftError('사진 한 장은 12MB 이하로 등록해 주세요.')
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width * original.height > 40_000_000:
                raise DraftError('사진 크기가 너무 큽니다. 축소 후 등록해 주세요.')
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
            result = io.BytesIO()
            image.save(result, 'JPEG', quality=88)
            return result.getvalue()
    except DraftError:
        raise
    except Exception:
        raise DraftError('읽을 수 없는 사진이 있습니다. JPG·PNG·WebP로 등록해 주세요.')


def decode_images(images, maximum):
    if not isinstance(images, list) or len(images) > maximum:
        raise DraftError(f'사진은 최대 {maximum}장까지 등록할 수 있습니다.')
    decoded = []
    for value in images:
        if not isinstance(value, str) or len(value) > 16 * 1024 * 1024:
            raise DraftError('사진 데이터가 너무 큽니다.')
        try:
            raw = base64.b64decode(value.split(',', 1)[-1], validate=True)
        except Exception:
            raise DraftError('사진 데이터가 올바르지 않습니다.')
        decoded.append(jpeg(raw))
    return decoded


def color_settings(request, primary):
    request, primary = str(request or '').strip(), str(primary or '').strip()
    if len(request) > 300 or len(primary) > 60:
        raise DraftError('판매 색상은 300자, 대표 색상은 60자 이하로 적어주세요.')
    colors = list(dict.fromkeys(x.strip() for x in re.split(r'[,，、;\n]+', request) if x.strip()))
    if len(colors) > 12:
        raise DraftError('판매 색상은 최대 12개까지 쉼표로 구분해 주세요.')
    if colors and primary and primary not in colors:
        raise DraftError('대표 색상은 판매 색상 중 하나를 같은 이름으로 적어주세요.')
    return ', '.join(colors), primary or (colors[0] if colors else '')


def create(body):
    images = body.get('images') or []
    url = str(body.get('url') or '').strip()
    if not url and not images:
        raise DraftError('상품 링크 또는 제품 사진을 등록해 주세요. 색상 기준 사진만으로는 만들 수 없습니다.')
    if url and (not url.startswith(('https://', 'http://')) or G.P.detect_source(G.normalize_url(url)) not in ('cninsider', 'cafe24')):
        raise DraftError('CN인사이더·카페24 상품 링크를 사용하거나 사진을 직접 올려주세요.')
    decoded = decode_images(images, 10)
    color_images = decode_images(body.get('color_images') or [], 3)
    style_images = decode_images(body.get('reference_images') or [], 10)
    color_request, primary_color = color_settings(body.get('color_request'), body.get('primary_color'))
    if primary_color and not color_request and not color_images:
        color_request = primary_color
    title = str(body.get('title') or '')
    if len(title) > 200:
        raise DraftError('판매상품명은 200자 이하로 입력해 주세요. 입력한 이름은 그대로 사용합니다.')
    jid = uuid.uuid4().hex[:12]
    doc = dict(id=jid, title=title, submitted_title=title, created_at=time.time(), run_started_at=time.time(), url=url, category=body.get('category', 'kitchen'),
               status='running', message='상품 정보 확인 중', sections=[], form={}, refs=[], inputs=[], warning='', error='',
               color_request=color_request, primary_color=primary_color, color_refs=[], style_refs=[], assets=[], workflow_version=body.get('workflow_version',3))
    with LOCK:
        folder(jid).mkdir(parents=True)
        for i, raw in enumerate(decoded):
            name = f'input-{i}.jpg'
            (folder(jid) / name).write_bytes(raw)
            doc['inputs'].append(name)
        for i, raw in enumerate(color_images):
            name = f'color-{i}.jpg'
            (folder(jid) / name).write_bytes(raw)
            doc['color_refs'].append(name)
        for i, raw in enumerate(style_images):
            name=f'reference-{i}.jpg';(folder(jid)/name).write_bytes(raw);doc['style_refs'].append(name)
        ACTIVE.add(jid)
        save(doc)
        if doc['workflow_version']==4:
            threading.Thread(target=F.collect,args=(sys.modules[__name__],jid),daemon=True).start()
        else:
            threading.Thread(target=initial, args=(jid,), daemon=True).start()
    return {'id': jid}


def error_text(error):
    text = str(error)
    text = re.sub(r'(?i)(sk-[a-z0-9_-]+|bearer\s+\S+)', '[숨김]', text)
    return text[:240] or '이미지를 만들지 못했습니다. 다시 시도해 주세요.'


def record_progress(doc, message):
    doc['message'] = message
    events = doc.setdefault('progress_events', [])
    events.append({'time': time.time(), 'message': message})
    doc['progress_events'] = events[-16:]
    save(doc)


def prepare(jid, preserve=False):
    with LOCK:
        doc = read(jid)
    def progress(message):
        with LOCK:
            record_progress(doc, message)
    progress('링크의 제품·옵션·상세 사진 수집 중')
    if not preserve and doc.get('collection_complete') and doc.get('assets') and all((folder(jid) / a['file']).is_file() for a in doc['assets']):
        assets, title, warning = doc['assets'], doc['title'], doc.get('warning', '')
        progress(f'저장된 제품 사진 {len(assets)}장 사용 — 완료된 분석부터 이어갑니다.')
    else:
        assets, title, warning = S.collect(doc, folder(jid), G, progress)
        with LOCK:
            doc.update(assets=assets, title=doc.get('submitted_title') or title, warning=warning, collection_complete=True)
            save(doc)
    title = doc.get('submitted_title') or title
    S.analyze(assets, title, folder(jid), G, progress)
    usable = [a for a in assets if a['usable']]
    if doc.get('url') and not any(a['origin'] == 'link' for a in usable):
        raise DraftError('링크 사진에서 같은 제품을 확인하지 못했습니다. 상품 링크와 직접 올린 사진이 같은 제품인지 확인해 주세요.')
    progress('제품 정보와 판매 색상 정리 중')
    prompt = V3.planning(title, doc, [{k: a.get(k) for k in ('id', 'description', 'view', 'role', 'sections', 'colors', 'original_text', 'translation')} for a in usable])
    content = [{'type': 'text', 'text': prompt}]
    for name in doc.get('color_refs', []):
        content.extend([{'type': 'text', 'text': '판매 색상 기준 캡처'}, S.image_content(folder(jid), name)])
    form = V3.request_json(G.P, content, 5500, V3.validate_form, folder(jid), '상품 정보·판매 색상 기획', progress)
    if not isinstance(form, dict):
        raise DraftError('상품 분석 결과가 올바르지 않습니다. 다시 시도해 주세요.')
    photo_plan = form.get('photo_plan') or {}
    photo_plan = {str(i): [ident for ident in photo_plan.get(str(i), []) if isinstance(ident, str) and any(a['id']==ident for a in usable)][:6]
                  for i in range(11) if isinstance(photo_plan, dict) and isinstance(photo_plan.get(str(i)), list)}
    points = form.get('sellpoints') or []
    if not isinstance(points, list):
        points = []
    form = {key: str(form.get(key) or '')[:500] for key in ('brand', 'product_name', 'category', 'option', 'size', 'mood')} | {'sellpoints': [dict(title=str(x.get('title', ''))[:200], desc=str(x.get('desc', ''))[:500]) for x in points[:3] if isinstance(x, dict)]}
    if preserve:
        form = dict(doc['form'])
    form['brand'] = ''
    form['product_name'] = doc.get('submitted_title') or (form.get('product_name') if preserve else title) or form.get('product_name') or '상품'
    if doc.get('color_request'):
        form['option'] = doc['color_request']
    if doc.get('color_refs') and not form.get('option'):
        raise DraftError('캡처에서 판매 색상을 확인하지 못했습니다. 색상명을 함께 적어주세요.')
    with LOCK:
        doc.update(form=form, title=form['product_name'] or title or '상품', refs=[a['file'] for a in usable], assets=assets,
                   source_version=2, prompt_version=3, photo_plan={} if preserve else photo_plan, warning=warning.strip(), source_summary=dict(link=sum(a['origin'] == 'link' for a in assets),
                   upload=len(doc['inputs']), usable=len(usable), excluded=len(assets)-len(usable)))
        if not doc.get('primary_color') and (doc.get('color_request') or doc.get('color_refs')):
            doc['primary_color'] = re.split(r'[,，、;\n]', form.get('option', ''))[0].strip()
        if not preserve:
            doc['sections'] = [dict(index=i, title=t, status='pending', error='', failed_action='', low='', high='', revision=0) for i, t in enumerate(TITLES)]
        save(doc)
    G.log(f"draft={jid} sources link={doc['source_summary']['link']} upload={len(doc['inputs'])} usable={len(usable)} excluded={len(assets)-len(usable)}")


def prompt_for(doc, index, action='', instruction='', current=False):
    form = dict(doc['form'], brand='', product_name=doc.get('submitted_title') or doc['title'])
    if doc.get('workflow_version')==4:
        from mobile_layout import prompt
        return prompt(form,index,action,instruction,current)
    return V3.image_prompt(form, index, action, instruction, current)


def generate(jid, index, action, instruction=''):
    with LOCK:
        doc = read(jid)
        section = doc['sections'][index]
        titles=F.TITLES if doc.get('workflow_version')==4 else TITLES
        thumbnail=index==(9 if doc.get('workflow_version')==4 else 10)
        section.update(status='running', error='', failed_action=action)
        record_progress(doc, f'{index + 1}/{len(titles)} 구간 · {titles[index]} {"고화질" if action == "high" else "부분 수정" if action == "edit" else "저해상도"} 생성 중')
    chosen = S.choose(doc, 10 if thumbnail else index)
    if doc.get('workflow_version')==4:
        lead=doc.get('section_photos',{}).get(str(index))
        if lead:
            chosen=sorted(chosen,key=lambda a:a['id']!=lead)
            primary=next((a for a in doc['assets'] if a['id']==lead and a.get('selected') and a.get('use_as')!='info' and a.get('usable')),None)
            if primary and primary not in chosen:chosen=[primary]+chosen[:11]
    if not chosen:
        raise DraftError('제품 참고 사진이 없습니다. 사진 수집을 다시 시도해 주세요.')
    if doc.get('workflow_version')==4 and index in (1,3,4,5,7):
        chosen=chosen[:2 if index==7 else 1]
    styles=[a for a in doc.get('assets',[]) if a.get('selected') and a['origin']=='reference'][:2] if doc.get('workflow_version')==4 else []
    if doc.get('workflow_version')==4 and index in (1,3,4,5,7):styles=[]
    if styles:chosen=chosen[:10]
    reference_names = [a['file'] for a in chosen] + doc.get('color_refs', []) + [a['file'] for a in styles]
    refs = [('image/jpeg', base64.b64encode((folder(jid) / name).read_bytes()).decode()) for name in reference_names]
    current = section['high'] or section['low']
    prompt = prompt_for(doc, index, action, instruction, bool(current))
    if current and action in ('edit', 'high'):
        refs.insert(0, ('image/jpeg', base64.b64encode((folder(jid) / current).read_bytes()).decode()))
    elif instruction:
        prompt += '\n추가 요청: ' + instruction
    prompt += '\n' + S.reference_prompt(doc, index, chosen, int(bool(current and action in ('edit', 'high'))))
    if styles:
        prompt += '\n마지막 '+str(len(styles))+'장은 참고용 이미지입니다. 구도·포즈·패션·배경만 참고하고 제품 형태·수량·글자·스펙·구성은 절대 가져오지 마세요.'
    if doc.get('workflow_version')==4:
        prompt += '\n첫 제품 참고 사진을 이 구간의 큰 구도·크기·착용 방식 기준으로 사용하세요. 제품 형태는 그대로, 포즈·패션·배경만 자연스럽게 변형하세요.'
    if doc.get('workflow_version')==4:
        prompt += '\n사용자가 수정·확정한 번역 자료(새로운 상품명·수량·스펙을 추가하지 마세요): '+json.dumps({a['id']:doc.get('translations',{}).get(a['id'],a.get('translation','')) for a in chosen},ensure_ascii=False)
    if instruction:
        prompt += '\n현재 수정 요청(판매 색상 설정을 바꾸려면 상품 정보에서 변경): ' + instruction
    with LOCK:
        section['reference_attempt'] = dict(ids=[a['id'] for a in chosen], colors=[name[:-4] for name in doc.get('color_refs', [])], count=len(refs), action=action)
        save(doc)
    G.log(f"draft={jid} section={index} action={action} references={','.join(a['id'] for a in chosen)} colors={len(doc.get('color_refs', []))} total={len(refs)}")
    quality = 'high' if action == 'high' else 'low'
    try:
        for attempt in range(2):
            try:
                if doc.get('workflow_version')==4 and index in (1,3,4,5,7):
                    from mobile_layout import compose
                    raw=compose(doc,index,chosen,folder(jid),quality,G,instruction)
                else:
                    raw = G._oai_image(prompt, ref_imgs_b64=refs, size='1024x1024' if thumbnail else '1024x1536', quality=quality)
                break
            except Exception as error:
                temporary = any(word in str(error).lower() for word in ('429', '500', '502', '503', '504', 'timeout', 'timed out', 'temporar', 'server_error', 'connection reset'))
                if attempt or not temporary:
                    raise
                with LOCK:
                    doc['message'] = section['title'] + ' 일시 오류 — 한 번 더 시도 중'
                    save(doc)
                time.sleep(2)
        image = Image.open(io.BytesIO(raw)).convert('RGB')
        width = (1000 if thumbnail else 860) if quality == 'high' else 430
        image = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        name = f'{index}-{quality}-{uuid.uuid4().hex[:8]}.jpg'
        image.save(folder(jid) / name, 'JPEG', quality=92 if quality == 'high' else 85)
        with LOCK:
            section[quality] = name
            if quality == 'low':
                section['high'] = ''
            section.update(status='done', error='', failed_action='', revision=section['revision'] + 1, prompt_version=3)
            if action not in ('edit', 'high'):
                section['title'] = titles[index]
            section.update(reference_ids=[a['id'] for a in chosen], color_sample_ids=[name[:-4] for name in doc.get('color_refs', [])], reference_count=len(refs))
            record_progress(doc, f'{index + 1}/{len(titles)} 구간 · {titles[index]} 완료')
    except Exception as error:
        with LOCK:
            section.update(status='error', error=error_text(error))
            save(doc)


def finish(jid):
    with LOCK:
        doc = read(jid)
        failures = sum(s['status'] == 'error' for s in doc['sections'])
        doc.update(status='partial' if failures else 'done', message=f'{failures}개 구간 실패 — 해당 구간만 다시 시도할 수 있습니다.' if failures else '시안 준비 완료. 필요한 구간을 수정하고 고화질로 완성하세요.')
        save(doc)
        ACTIVE.discard(jid)


def initial(jid):
    try:
        prepare(jid)
        for index in range(len(TITLES)):
            generate(jid, index, 'low')
        finish(jid)
    except Exception as error:
        with LOCK:
            doc = read(jid)
            doc.update(status='error', error=error_text(error), failed_stage=doc.get('message', ''), message='상품 분석을 완료하지 못했습니다.')
            for section in doc['sections']:
                if section['status'] in ('running', 'pending'):
                    section.update(status='error', error=error_text(error), failed_action=section['failed_action'] or 'low')
            save(doc)
            ACTIVE.discard(jid)


def action(jid, body):
    kind = body.get('action')
    if kind not in ('regenerate', 'edit', 'high', 'retry', 'plan','select_photos','back_to_selection','generate_all','recollect'):
        raise DraftError('지원하지 않는 작업입니다.')
    instruction = str(body.get('instruction') or '')[:2000].strip()
    with LOCK:
        doc = read(jid)
        if jid in ACTIVE:
            raise DraftError('현재 작업이 끝난 뒤 다시 눌러주세요.', 409)
        if doc.get('workflow_version')==4:
            result=F.action(sys.modules[__name__],doc,body)
            if result is not None:return result
            if doc.get('status') in ('selecting','reviewing') and kind!='plan':
                raise DraftError('사진 선택과 문구 확인을 완료하고 전체 생성 버튼을 눌러주세요.')
        if kind == 'plan':
            if not doc['sections']:
                raise DraftError('상품 분석을 먼저 완료해 주세요.')
            form = body.get('form')
            if not isinstance(form, dict):
                raise DraftError('상품 정보를 확인해 주세요.')
            if len(str(form.get('product_name') or '')) > 200:
                raise DraftError('판매상품명은 200자 이하로 입력해 주세요.')
            request, primary = color_settings(form.get('color_request', doc.get('color_request')), form.get('primary_color', doc.get('primary_color')))
            if primary and not request and not doc.get('color_refs'):
                request = primary
            doc.update(color_request=request, primary_color=primary)
            for key in ('product_name', 'option', 'size', 'mood'):
                doc['form'][key] = str(form.get(key, doc['form'].get(key, '')))[:500]
            points = form.get('sellpoints')
            if isinstance(points, list) and len(points) == 3 and all(isinstance(x, dict) for x in points):
                if points != doc['form'].get('sellpoints'):
                    doc.pop('photo_plan', None)
                doc['form']['sellpoints'] = [{k: str(x.get(k, ''))[:500] for k in ('title', 'desc')} for x in points]
            if doc.get('workflow_version')==4:
                F.save_review(doc,form)
            doc['title'] = doc['form']['product_name'] or doc['title']
            doc['submitted_title'] = doc['title']
            doc['form']['brand'] = ''
            if request:
                doc['form']['option'] = request
            save(doc)
            return public(doc)
        if kind == 'retry' and not doc['sections']:
            doc.update(status='running', error='', failed_stage='', run_started_at=time.time(), message='상품 분석 다시 시도 중')
            ACTIVE.add(jid)
            save(doc)
            threading.Thread(target=initial, args=(jid,), daemon=True).start()
            return public(doc)
        indices = body.get('indices', [])
        if kind == 'retry':
            indices = [s['index'] for s in doc['sections'] if s['status'] in ('error', 'pending')]
        if not isinstance(indices, list) or not indices or len(indices) > 11 or any(type(i) is not int or i < 0 or i >= len(doc['sections']) for i in indices):
            raise DraftError('작업할 구간을 선택해 주세요.')
        indices = list(dict.fromkeys(indices))
        if kind == 'edit' and (not instruction or len(indices) != 1):
            raise DraftError('수정할 구간 하나와 수정 내용을 입력해 주세요.')
        if doc.get('workflow_version')==4 and kind=='edit' and indices[0] in (1,3,4,5,7):
            raise DraftError('원본 사진 유지 구간입니다. 기획에서 문구·기준 사진·사용 영역을 저장한 뒤 이 구간 새로 만들기를 눌러주세요.')
        if kind in ('edit', 'high') and any(not doc['sections'][i]['low'] for i in indices):
            raise DraftError('저해상도 시안을 먼저 만들어 주세요.')
        tasks = [(i, (doc['sections'][i]['failed_action'] or 'low') if kind == 'retry' else kind) for i in indices]
        # Remember edit instructions so retries do not silently lose the requested change.
        for i, operation in tasks:
            if kind != 'retry':
                doc['sections'][i]['instruction'] = instruction
            doc['sections'][i].update(status='pending', error='', failed_action=operation)
        doc.update(status='running', error='', run_started_at=time.time(), message='선택 구간 작업 준비 중')
        ACTIVE.add(jid)
        save(doc)
        threading.Thread(target=run_actions, args=(jid, tasks), daemon=True).start()
        return public(doc)


def run_actions(jid, tasks):
    try:
        with LOCK:
            needs_sources = read(jid).get('source_version') != 2
        if needs_sources:
            prepare(jid, preserve=True)
        for index, kind in tasks:
            try:
                with LOCK:
                    instruction = read(jid)['sections'][index].get('instruction', '')
                generate(jid, index, kind, instruction)
            except Exception as error:
                with LOCK:
                    doc = read(jid)
                    doc['sections'][index].update(status='error', error=error_text(error), failed_action=kind)
                    save(doc)
    except Exception as error:
        with LOCK:
            doc = read(jid)
            for index, kind in tasks:
                doc['sections'][index].update(status='error', error=error_text(error), failed_action=kind)
            save(doc)
    finally:
        finish(jid)


def image_bytes(jid, index, quality):
    if quality not in ('low', 'high') or type(index) is not int or not 0 <= index < len(TITLES):
        raise DraftError('이미지 구간을 확인해 주세요.')
    with LOCK:
        doc = read(jid)
        if index >= len(doc['sections']) or not doc['sections'][index][quality]:
            raise DraftError('아직 생성되지 않은 이미지입니다.', 404)
        return (folder(jid) / doc['sections'][index][quality]).read_bytes()


def source_bytes(jid, asset_id):
    with LOCK:
        doc = read(jid)
        asset=next((dict(a) for a in doc.get('assets',[]) if a['id']==asset_id),None)
        registered = {a['id']: a['file'] for a in doc.get('assets', [])}
        registered.update({name[:-4]: name for name in doc.get('color_refs', [])})
        if asset_id not in registered:
            raise DraftError('등록된 참고 사진이 아닙니다.', 404)
    if asset and doc.get('workflow_version')==4:
        return F.materialize(folder(jid),asset)
    return (folder(jid) / registered[asset_id]).read_bytes()


def download(jid, quality, indices=None):
    with LOCK:
        doc = read(jid)
        if quality == 'zip':
            indices = indices if indices is not None else [s['index'] for s in doc['sections'] if s['high']]
            if not indices or any(type(i) is not int or not 0 <= i < len(doc['sections']) or not doc['sections'][i]['high'] for i in indices):
                raise DraftError('선택 구간을 고화질로 먼저 만들어 주세요.', 409)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as output:
                for i in indices:
                    output.writestr(f'{i+1:02d}.jpg', (folder(jid) / doc['sections'][i]['high']).read_bytes())
            return buffer.getvalue(), 'application/zip'
        count=9 if doc.get('workflow_version')==4 else 10
        if quality not in ('low', 'high') or len(doc['sections']) < count or any(not s[quality] for s in doc['sections'][:count]):
            raise DraftError(f'상세페이지 {count}개 구간을 모두 완성한 뒤 다운로드해 주세요.', 409)
        images = [Image.open(io.BytesIO((folder(jid) / s[quality]).read_bytes())).convert('RGB') for s in doc['sections'][:count]]
    width = 860 if quality == 'high' else 430
    canvas = Image.new('RGB', (width, sum(im.height for im in images)), 'white')
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height
    output = io.BytesIO()
    canvas.save(output, 'JPEG', quality=92 if quality == 'high' else 85)
    return output.getvalue(), 'image/jpeg'


def handle(handler, method):
    from urllib.parse import urlparse, parse_qs
    path = urlparse(handler.path)
    if not path.path.startswith('/cnmaker/drafts'):
        return False
    try:
        if handler.headers.get('x-secret') != handler.draft_secret:
            raise DraftError('forbidden', 403)
        q = parse_qs(path.query)
        jid = q.get('id', [''])[0]
        if method == 'POST':
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 <= length <= 30 * 1024 * 1024:
                raise DraftError('사진 용량이 너무 큽니다.', 413)
            body = json.loads(handler.rfile.read(length) or '{}')
            if not isinstance(body, dict):
                raise DraftError('요청 형식이 올바르지 않습니다.')
            if path.path == '/cnmaker/drafts':
                result = create(body)
            elif path.path == '/cnmaker/drafts/action':
                result = action(jid, body)
            else:
                raise DraftError('요청을 찾을 수 없습니다.', 404)
            handler._send(200, result)
        elif path.path == '/cnmaker/drafts':
            handler._send(200, status(jid) if jid else history())
        elif path.path == '/cnmaker/drafts/image':
            raw = source_bytes(jid, q['asset'][0]) if 'asset' in q else image_bytes(jid, int(q.get('index', ['-1'])[0]), q.get('quality', ['low'])[0])
            handler._send(200, raw, 'image/jpeg')
        elif path.path == '/cnmaker/drafts/download':
            indices = [int(i) for i in q['indices'][0].split(',')] if 'indices' in q else None
            raw, mime = download(jid, q.get('quality', ['low'])[0], indices)
            handler._send(200, raw, mime)
        else:
            raise DraftError('요청을 찾을 수 없습니다.', 404)
    except DraftError as error:
        handler._send(error.status, {'error': str(error)})
    except (ValueError, TypeError):
        handler._send(400, {'error': '요청 형식을 확인해 주세요.'})
    except Exception as error:
        handler._send(500, {'error': error_text(error)})
    return True
