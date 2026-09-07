"""User-controlled collection, selection, analysis and copy review."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import prompt_v3 as V
import merchandising as M

TITLES = V.TITLES[:8] + ['PRODUCT INFO', '대표 썸네일']


def catalogue(url, path):
    """A browser subprocess bounds collection; original downloads are deferred."""
    output = path / 'catalogue.json'
    output.unlink(missing_ok=True)
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('selection_worker.py')), url, str(output)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name != 'nt')
    try:
        process.wait(timeout=50)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
            process.wait(timeout=2)
    if output.exists():
        return json.loads(output.read_text(encoding='utf-8'))
    return {'images': [], 'warning': '1분 안에 링크 목록을 확보하지 못했습니다. 확보된 사진을 선택하거나 사진 수집을 다시 시도해 주세요.'}


def collect(D, jid):
    try:
        with D.LOCK:
            doc = D.read(jid)
            D.record_progress(doc, '사진 목록 수집 중 — 원본 다운로드와 AI 분석은 선택 후 진행합니다.')
        data = catalogue(doc['url'], D.folder(jid)) if doc.get('url') else {'images': []}
        assets = [dict(id=n[:-4], file=n, origin='upload', role='product', selected=True, hint='실제 상품 이미지') for n in doc['inputs']]
        assets += [dict(id=n[:-4], file=n, origin='reference', role='reference', selected=True, hint='구도·포즈·패션·배경 참고 전용') for n in doc.get('style_refs', [])]
        seen = set()
        for row in data.get('images', [])[:48]:
            url = row.get('url', '')
            if not url.startswith(('http://', 'https://')) or url in seen: continue
            seen.add(url)
            ident = 'link-' + hashlib.sha256(url.encode()).hexdigest()[:20]
            assets.append(dict(id=ident, file=ident+'.jpg', url=url, origin='link', selected=True,
                role=row.get('role', 'product'), hint=row.get('hint', '')))
        with D.LOCK:
            doc.update(assets=assets, status='selecting', phase='selecting', error='', warning=data.get('warning', ''),
                title=doc.get('submitted_title') or data.get('title') or doc['title'], collection_complete=True)
            D.record_progress(doc, f'사진 {len(assets)}장 수집 — 사용할 사진을 선택해 주세요.')
    except Exception as error:
        with D.LOCK:
            doc = D.read(jid)
            doc.update(status='error', phase='collection', error=D.error_text(error), message='사진 목록을 수집하지 못했습니다.')
            D.save(doc)
    finally:
        D.ACTIVE.discard(jid)


def materialize(path, asset):
    target = path / asset['file']
    if target.exists(): return target.read_bytes()
    req = urllib.request.Request(asset['url'], headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read(12*1024*1024+1)
    if len(raw)>12*1024*1024: raise ValueError('선택한 사진이 12MB를 초과합니다.')
    with Image.open(io.BytesIO(raw)) as im:
        if im.width*im.height>40_000_000: raise ValueError('선택한 사진이 너무 큽니다.')
        im=im.convert('RGB'); im.thumbnail((2000,7000))
        buf=io.BytesIO(); im.save(buf,'JPEG',quality=95);raw=buf.getvalue()
    # Concurrent preview/analysis requests never see a partly written image.
    import uuid
    tmp=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    tmp.write_bytes(raw);tmp.replace(target)
    return raw


def analyze(D, jid):
    try:
        with D.LOCK:
            doc=D.read(jid)
        path=D.folder(jid)
        chosen=[a for a in doc['assets'] if a.get('selected')]
        def progress(message):
            with D.LOCK: D.record_progress(doc,message)
        progress(f'선택한 사진 {len(chosen)}장 준비 중')
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda a:materialize(path,a),chosen))
        product=[a for a in chosen if a['origin']!='reference']
        D.S.analyze(product,doc['title'],path,D.G,progress)
        for a in product:
            a['use_as']=a.get('use_override') or ('info' if a.get('role')=='size' or a.get('information_only') else 'product')
        usable=[a for a in product if a.get('usable')]
        if not any(a.get('usable') and a['use_as']=='product' for a in usable):
            raise ValueError('선택한 사진에서 실제 제품 사진을 확인하지 못했습니다. 사진 선택에서 같은 제품 사진을 추가해 주세요.')
        progress('상품 정보와 판매 색상 정리 중')
        content=[{'type':'text','text':V.planning(doc['title'],doc,[{k:a.get(k) for k in
            ('id','description','view','role','sections','colors','original_text','translation')} for a in usable])}]
        for name in doc.get('color_refs',[]):content.extend([{'type':'text','text':'색상 기준 캡처 — 색조만 참고'},D.S.image_content(path,name)])
        form=V.request_json(D.G.P,content,5500,V.validate_form,path,'상품·색상 기획',progress)
        plan=form.get('photo_plan') or {}
        photo_ids={a['id'] for a in usable if a['use_as']=='product'}
        plan={str(i):[ident for ident in plan.get(str(i),[]) if isinstance(ident,str) and ident in photo_ids][:6]
              for i in range(11) if isinstance(plan,dict) and isinstance(plan.get(str(i)),list)}
        extra={'product_info':M.recommend(form),'review_notes':[str(x)[:500] for x in form.get('review_notes',[])[:12]] if isinstance(form.get('review_notes',[]),list) else []}
        form={k:str(form.get(k) or '')[:500] for k in ('brand','product_name','category','option','size','mood')}|{'sellpoints':form['sellpoints']}
        form.update(extra)
        form['brand']='';form['product_name']=doc.get('submitted_title') or doc['title']
        if doc.get('color_request'):form['option']=doc['color_request']
        for row in form['product_info']:
            if row['label'] in ('상품명','제품명'):row['value']=form['product_name']
            if row['label'] in ('컬러','색상','판매 색상') and form.get('option'):row['value']=form['option']
        if doc.get('color_refs') and not form.get('option'):
            raise ValueError('색상 캡처에서 판매 색상을 확인하지 못했습니다. 색상명을 입력하거나 색상 기준을 다시 등록해 주세요.')
        if not doc.get('primary_color') and (doc.get('color_refs') or doc.get('color_request')):
            doc['primary_color']=form['option'].split(',')[0].strip()
        with D.LOCK:
            doc.update(form=form,photo_plan=plan,status='reviewing',phase='reviewing',error='',
                source_version=2,prompt_version=4,refs=[a['file'] for a in usable if a['use_as']=='product'],
                source_summary=dict(link=sum(a['origin']=='link' for a in chosen),upload=len(doc['inputs']),
                    usable=len(usable),excluded=len(doc['assets'])-len(chosen)),
                sections=[dict(index=i,title=t,status='pending',error='',failed_action='',low='',high='',revision=0) for i,t in enumerate(TITLES)])
            D.record_progress(doc,'분석 완료 — 핵심 장점과 상품정보를 확인한 뒤 생성해 주세요. 사진은 구간에 맞게 자동 선정합니다.')
    except Exception as error:
        with D.LOCK:
            doc=D.read(jid)
            doc.update(status='error',phase='analysis',failed_stage=doc.get('message',''),error=D.error_text(error),
                failure_id=str(time.time_ns()),message='분석을 완료하지 못했습니다. 사진 선택과 완료된 분석은 보존됐습니다.')
            D.save(doc)
    finally:D.ACTIVE.discard(jid)


def action(D,doc,body):
    kind=body.get('action');jid=doc['id']
    if kind=='back_to_selection':
        doc.update(status='selecting',phase='selecting',error='',failure_id='',message='사용할 사진을 다시 선택해 주세요.')
        D.save(doc);return D.public(doc)
    if kind=='remove_asset':
        asset_id=body.get('asset_id')
        asset=next((a for a in doc.get('assets',[]) if a.get('id')==asset_id),None)
        if not asset or asset.get('origin')=='reference':
            raise D.DraftError('분석 사진을 찾지 못했습니다.')
        if doc.get('status') not in ('reviewing','partial','done'):
            raise D.DraftError('문구 확인 단계에서만 분석 사진을 제외할 수 있습니다.')
        product=[a for a in doc.get('assets',[]) if a.get('selected') and a.get('origin')!='reference' and a.get('id')!=asset_id and a.get('use_as')!='info']
        if not product:
            raise D.DraftError('제품 사진은 한 장 이상 남겨야 합니다.')
        asset['selected']=False;asset['excluded_after_analysis']=True
        for key,ids in list(doc.get('photo_plan',{}).items()):
            doc['photo_plan'][key]=[x for x in ids if x!=asset_id]
        for key,value in list(doc.get('section_photos',{}).items()):
            if value==asset_id:doc['section_photos'].pop(key,None)
        doc['refs']=[a['file'] for a in doc['assets'] if a.get('selected') and a.get('use_as')=='product']
        D.record_progress(doc,f'사진 제외 — {asset.get("view") or asset_id}. 원본은 보존했습니다.')
        D.save(doc);return D.public(doc)
    if kind=='replan':
        if doc.get('status')!='reviewing':
            raise D.DraftError('문구 확인 단계에서 다시 기획할 수 있습니다.')
        doc.update(status='running',phase='analysis',error='',message='선택한 사진으로 상품 기획을 다시 만드는 중입니다.',run_started_at=time.time())
        D.ACTIVE.add(jid);D.save(doc)
        D.threading.Thread(target=analyze,args=(D,jid),daemon=True).start()
        return D.public(doc)
    if kind=='select_photos':
        ids=body.get('asset_ids')
        registered={a['id'] for a in doc['assets']}
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in registered for i in ids):
            raise D.DraftError('사용할 실제 상품 사진을 선택해 주세요.')
        if not any(a['id'] in ids and a['origin']!='reference' for a in doc['assets']):
            raise D.DraftError('참고용 사진 외에 실제 상품 사진을 한 장 이상 선택해 주세요.')
        overrides=body.get('uses',{})
        if not isinstance(overrides,dict) or any(v not in ('auto','product','info') for v in overrides.values()):raise D.DraftError('사진 용도를 확인해 주세요.')
        for a in doc['assets']:
            a['selected']=a['id'] in ids
            if a['id'] in overrides:a['use_override']=overrides[a['id']] if overrides[a['id']]!='auto' else None
        if doc.get('sections'):
            archived=doc.setdefault('previous_versions',[])
            archived.append({k:doc.get(k) for k in ('sections','form','section_photos','translations')})
        doc.update(sections=[],form={},photo_plan={},section_photos={},translations={})
        target=analyze;phase='analysis'
    elif kind=='recollect':
        target=collect;phase='collection'
    elif kind=='retry' and doc.get('phase') in ('analysis','collection'):
        target=analyze if doc['phase']=='analysis' else collect;phase=doc['phase']
    elif kind=='generate_all':
        if doc['status']!='reviewing':raise D.DraftError('분석·문구 확인을 먼저 완료해 주세요.')
        target=generate_all;phase='generation'
    else:return None
    doc.update(status='running',phase=phase,error='',failed_stage='',failure_id='',run_started_at=time.time())
    D.ACTIVE.add(jid);D.save(doc)
    D.threading.Thread(target=target,args=(D,jid),daemon=True).start()
    return D.public(doc)


def generate_all(D,jid):
    D.run_actions(jid,[(i,'low') for i in range(10)])


def save_review(doc,form):
    from mobile_layout import crop_box
    V.validate_form(doc['form'])
    doc['form']['product_info']=M.normalize_rows(form['product_info']) if 'product_info' in form else M.recommend(doc['form'])
    choices=form.get('section_photos',doc.get('section_photos',{}))
    photos={a['id']:a for a in doc['assets'] if a.get('selected') and a.get('usable') and a.get('use_as')!='info' and a['origin']!='reference'}
    if not isinstance(choices,dict) or any(k not in {str(i) for i in range(10)} or v not in photos for k,v in choices.items() if v):
        raise ValueError('구간의 기준 사진을 다시 선택해 주세요.')
    doc['section_photos']={k:v for k,v in choices.items() if v}
    edits=form.get('translations',{})
    if not isinstance(edits,dict):raise ValueError('번역 문구를 확인해 주세요.')
    doc['translations']={a['id']:str(edits.get(a['id'],a.get('translation','')))[:1500] for a in doc['assets'] if a.get('selected') and a['origin']!='reference'}
    for a in doc['assets']:
        if a['id'] in doc['translations']:a['translation']=doc['translations'][a['id']]
    crops=form.get('crops',{})
    if isinstance(crops,dict):
        for a in doc['assets']:
            if a['id'] in crops:a['crop']=crop_box({'crop':crops[a['id']]})
