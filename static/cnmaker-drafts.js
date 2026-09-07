/* The restored screen with a draft-first workflow. */
let job = null, mode = 'url', src = 'cninsider', files = [], state = null;
let polling = null, importing = false, submitting = false, selected = new Set(), edits = {}, planJob = null;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const API = '/cnmaker/api/drafts';
const NOTES = {cninsider:'상품 링크와 실제 제품 사진을 등록하면 저해상도 시안을 먼저 만듭니다.',cafe24:'상품 정보를 바탕으로 저해상도 시안을 먼저 만듭니다.'};
function fail(message) {
  $('err').textContent = message; $('err').className = message ? 'err' : '';
  $('draft-action-error').textContent = message;
}
async function api(path='', body) {
  const response = await fetch(API + path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok || (result.error && !result.id)) throw new Error(result.error || '요청을 처리하지 못했습니다.');
  return result;
}
document.querySelectorAll('.tab').forEach(tab => tab.onclick = () => {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on')); tab.classList.add('on');
  src = tab.dataset.src; mode = tab.dataset.mode;
  $('urlmode').hidden = mode !== 'url'; $('urlnote').textContent = NOTES[src] || '';
});
$('urlnote').textContent = NOTES.cninsider;
function renderThumbs() {
  $('thumbs').replaceChildren();
  files.forEach((data, index) => {
    const box = document.createElement('div'); box.className = 'thumb';
    const img = document.createElement('img'); img.src = data; img.alt = `제품 사진 ${index+1}`;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'x'; button.textContent = '×'; button.setAttribute('aria-label',`사진 ${index+1} 삭제`);
    button.onclick = event => {event.stopPropagation(); if (!importing) {files.splice(index,1);renderThumbs();}};
    box.append(img,button); $('thumbs').append(box);
  });
  $('photo-count').textContent = `${files.length} / 10장`;
}
async function normalizePhoto(file) {
  if (file.size > 20*1024*1024) throw new Error('사진 한 장은 20MB 이하로 선택해 주세요.');
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = url; await image.decode();
    const scale = Math.min(1,1600/Math.max(image.width,image.height));
    const canvas = document.createElement('canvas'); canvas.width = Math.max(1,Math.round(image.width*scale)); canvas.height = Math.max(1,Math.round(image.height*scale));
    const context = canvas.getContext('2d'); context.fillStyle = '#fff'; context.fillRect(0,0,canvas.width,canvas.height); context.drawImage(image,0,0,canvas.width,canvas.height);
    return canvas.toDataURL('image/jpeg',0.88);
  } finally {URL.revokeObjectURL(url);}
}
async function addFiles(list) {
  if (importing) return fail('사진을 등록하고 있습니다. 잠시만 기다려 주세요.');
  const images = Array.from(list).filter(f => f.type.startsWith('image/'));
  if (images.length + files.length > 10) return fail('사진은 최대 10장입니다. 기존 사진을 삭제한 뒤 등록해 주세요.');
  importing = true; $('go').disabled = true; fail('');
  try {for (const file of images) files.push(await normalizePhoto(file));}
  catch(error) {fail(error.message || '읽을 수 없는 사진입니다. JPG·PNG·WebP를 사용해 주세요.');}
  finally {importing = false; $('go').disabled = submitting || state?.status === 'running'; renderThumbs(); $('file').value = '';}
}
$('drop').onclick = () => $('file').click();
$('drop').onkeydown = event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();$('file').click();}};
$('file').onchange = event => addFiles(event.target.files);
['dragenter','dragover'].forEach(name => $('drop').addEventListener(name,event => {event.preventDefault();$('drop').classList.add('over');}));
['dragleave','drop'].forEach(name => $('drop').addEventListener(name,event => {event.preventDefault();$('drop').classList.remove('over');}));
$('drop').addEventListener('drop',event => addFiles(event.dataTransfer.files));
document.addEventListener('paste',event => {
  const images = Array.from(event.clipboardData?.items || []).filter(item => item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
  if (images.length) {event.preventDefault();addFiles(images);}
});
async function go() {
  if (importing || submitting) return;
  if (state?.status === 'running') return fail('현재 시안 작업이 끝난 뒤 새 시안을 만들어 주세요.');
  const url = mode === 'url' ? $('url').value.trim() : '';
  if (!url && !files.length) return fail('상품 링크 또는 사진을 등록해 주세요.');
  submitting = true; $('go').disabled = true; fail('');
  try {
    const result = await api('',{url,images:files,title:$('title').value.trim(),category:$('cat_kitchen').checked?'kitchen':'other'});
    await openDraft(result.id);
  } catch(error) {fail(error.message);}
  finally {submitting = false; $('go').disabled = importing || state?.status === 'running';}
}
async function openDraft(id) {
  clearTimeout(polling); job = id; state = null; selected = new Set(); edits = {}; planJob = null;
  try {localStorage.setItem('cnmaker-draft-job',id);} catch (_) {}
  $('draft-result').hidden = false; $('draft-result').scrollIntoView({behavior:'smooth',block:'start'});
  await poll();
}
async function poll() {
  const requested = job; clearTimeout(polling);
  try {
    const result = await api('?id='+encodeURIComponent(requested));
    if (requested !== job) return;
    state = result; renderDraft(result);
    if (result.status === 'running') polling = setTimeout(poll,2200);
    else loadDraftHistory();
  } catch(error) {
    if (requested !== job) return;
    $('draft-status').textContent = error.message;
    if (/찾을 수 없/.test(error.message)) {try {localStorage.removeItem('cnmaker-draft-job');} catch (_) {} return;}
    polling = setTimeout(poll,5000);
  }
}
function renderDraft(doc) {
  const busy = doc.status === 'running';
  $('go').disabled = busy || importing || submitting;
  $('draft-name').textContent = doc.title || '저해상도 시안';
  $('draft-status').textContent = doc.message;
  $('draft-warning').textContent = [doc.warning,doc.error].filter(Boolean).join(' ');
  $('retry-failed').hidden = !doc.error && !doc.sections.some(s => ['error','pending'].includes(s.status));
  $('retry-failed').disabled = busy;
  $('high-selected').disabled = busy; $('select-all').disabled = busy;
  $('low-download').disabled = !doc.sections.length || !doc.sections.slice(0,10).every(s=>s.low);
  $('high-download').disabled = !doc.sections.length || !doc.sections.slice(0,10).every(s=>s.high);
  const signature = JSON.stringify([doc.id,busy,doc.sections.map(s=>[s.revision,s.status,s.error])]);
  if ($('section-list').dataset.signature !== signature) {
    $('section-list').dataset.signature = signature;
    $('section-list').replaceChildren();
    doc.sections.forEach(section => {
      const i = section.index, tier = section.high ? 'high' : 'low';
      const card = document.createElement('article'); card.className = 'draft-section';
      const imageUrl = `${API}/image?id=${encodeURIComponent(doc.id)}&index=${i}&quality=${tier}&v=${section.revision}`;
      card.innerHTML = `<div class="draft-image">${section[tier] ? `<img loading="lazy" src="${imageUrl}" alt="${esc(section.title)} 시안">` : '<div class="empty-image">시안을 준비하고 있습니다.</div>'}</div><div class="draft-controls"><h3><label><input type="checkbox" ${selected.has(i)?'checked':''}> ${i+1}. ${esc(section.title)}</label></h3><div class="quality-badge">${section.high?'고화질 완성':section.low?'저해상도 시안':'미생성'} · ${section.status==='running'?'생성 중':section.status==='pending'?'대기':section.status==='error'?'실패':'완료'}</div><p class="section-error">${esc(section.error)}</p><label class="edit-label">현재 이미지에서 수정할 부분<textarea maxlength="2000" placeholder="예: 제품은 그대로 두고 배경만 밝은 주방으로 바꿔줘">${esc(edits[i] || '')}</textarea></label><div class="draft-buttons"><button data-kind="edit">요청한 부분 수정</button><button data-kind="regenerate">이 구간 새로 만들기</button><button data-kind="high" class="primary">이 구간 고화질</button></div>${section[tier]?`<a class="section-download" href="${imageUrl}" download="${i+1}-${tier}.jpg">${tier==='high'?'고화질':'시안'} 구간 다운로드</a>`:''}</div>`;
      card.querySelector('input').onchange = event => {event.target.checked?selected.add(i):selected.delete(i);};
      card.querySelector('textarea').oninput = event => {edits[i] = event.target.value;};
      card.querySelectorAll('button').forEach(button => {
        button.disabled = busy || (['edit','high'].includes(button.dataset.kind) && !section.low);
        button.onclick = () => runAction(button.dataset.kind,[i],edits[i] || '');
      });
      $('section-list').append(card);
    });
  }
  if (doc.form.product_name && planJob !== doc.id) {renderPlan(doc); planJob = doc.id;}
  $('save-plan').disabled = busy;
}
function renderPlan(doc) {
  const form = doc.form;
  $('plan-fields').innerHTML = ['product_name','option','size','mood'].map((key,i)=>`<label>${['상품명','색상·옵션','규격','분위기'][i]}<input data-field="${key}" value="${esc(form[key])}"></label>`).join('') + [0,1,2].map(i=>`<label>핵심장점 ${i+1}<input data-point-title="${i}" value="${esc(form.sellpoints?.[i]?.title)}"><textarea data-point-desc="${i}">${esc(form.sellpoints?.[i]?.desc)}</textarea></label>`).join('');
  $('plan-editor').hidden = false;
}
async function savePlan() {
  const form = {};
  document.querySelectorAll('[data-field]').forEach(input=>form[input.dataset.field]=input.value);
  form.sellpoints = [0,1,2].map(i=>({title:document.querySelector(`[data-point-title="${i}"]`).value,desc:document.querySelector(`[data-point-desc="${i}"]`).value}));
  await runAction('plan',[], '', form);
}
async function runAction(action,indices=[],instruction='',form) {
  if (state?.status === 'running' || submitting) return;
  if (action === 'edit' && !instruction.trim()) return fail('현재 이미지에서 수정할 부분을 적어주세요.');
  submitting = true; fail('');
  try {
    const result = await api('/action?id='+encodeURIComponent(job),{action,indices,instruction,form});
    state = result; renderDraft(result);
    if (action === 'plan') $('plan-message').textContent = '저장했습니다. 이후 다시 만드는 구간에 적용됩니다.';
    await poll();
  } catch(error) {fail(error.message);}
  finally {submitting = false; $('go').disabled = importing || state?.status === 'running';}
}
function selectAll() {
  if (!state) return;
  selected = new Set(state.sections.filter(s=>s.low).map(s=>s.index));
  $('section-list').dataset.signature = ''; renderDraft(state);
}
function highSelected() {
  if (!selected.size) return fail('고화질로 만들 구간에 체크해 주세요.');
  runAction('high',Array.from(selected));
}
async function downloadDraft(quality) {
  if (!job) return;
  let query = `?id=${encodeURIComponent(job)}&quality=${quality}`;
  if (quality === 'zip' && selected.size) query += '&indices='+Array.from(selected).join(',');
  fail('');
  try {
    const response = await fetch(API+'/download'+query);
    if (!response.ok) {const result=await response.json();throw new Error(result.error || '다운로드 실패');}
    const blob = await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
    link.href=url;link.download=`${state.title || '상세페이지'}-${quality}.${quality==='zip'?'zip':'jpg'}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  } catch(error) {fail(error.message);}
}
async function loadDraftHistory() {
  try {
    const result = await api(); $('draft-history').replaceChildren();
    result.items.forEach(item=>{
      const row=document.createElement('div');row.className='hrow';
      const name=document.createElement('span');name.className='hinfo';name.textContent=(item.title||'상품 분석 중')+' · '+new Date(item.updated*1000).toLocaleString();
      const button=document.createElement('button');button.textContent='시안 열기';button.onclick=()=>openDraft(item.id);row.append(name,button);$('draft-history').append(row);
    });
  } catch(error) {$('draft-history').textContent=error.message;}
}
async function loadHistory() {
  try {
    const response=await fetch('/cnmaker/api/history'), data=await response.json();
    $('historylist').replaceChildren();
    (data.items||[]).forEach(item=>{
      const row=document.createElement('div');row.className='hrow';
      const label=document.createElement('span');label.className='hinfo';label.textContent=item.product_name||'이전 상세페이지';
      const link=document.createElement('a');link.href='/cnmaker/api/result?job='+encodeURIComponent(item.job);link.textContent='다운로드';link.download='상세페이지.jpg';row.append(label,link);$('historylist').append(row);
    });
  } catch (_) {$('historylist').textContent='기존 기록을 불러오지 못했습니다. 새로고침해 주세요.';}
}
loadHistory();loadDraftHistory();renderThumbs();
try {const previous=localStorage.getItem('cnmaker-draft-job');if(previous)openDraft(previous);} catch (_) {}
