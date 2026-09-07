/* Photo selection happens before any paid analysis. */
let referenceFiles=[],photoJob=null,photoChoices=new Set(),photoUses={},photoCrops={},modalId=null,lastFailure='';
const oldRenderThumbs=renderThumbs;
renderThumbs=function(target='product') {
  if(target!=='reference'){oldRenderThumbs(target);return;}
  $('reference-thumbs').replaceChildren(...referenceFiles.map((url,i)=>{
    const box=document.createElement('div');box.className='thumb';const img=document.createElement('img');img.src=url;img.onclick=()=>showUpload(url);
    const remove=document.createElement('button');remove.className='x';remove.textContent='×';remove.onclick=()=>{referenceFiles.splice(i,1);renderThumbs('reference');};box.append(img,remove);return box;
  }));$('reference-photo-count').textContent=`${referenceFiles.length} / 10장`;
};
const oldAddFiles=addFiles;
addFiles=async function(list,target='product') {
  if(target!=='reference')return oldAddFiles(list,target);
  if(importing)return fail('사진을 등록하고 있습니다. 잠시 후 다시 넣어주세요.');
  const images=Array.from(list).filter(f=>f.type.startsWith('image/'));
  if(referenceFiles.length+images.length>10)return fail('참고용 사진은 최대 10장입니다.');
  importing=true;$('go').disabled=true;
  try {for(const file of images)referenceFiles.push(await normalizePhoto(file));}
  catch(error){fail(error.message);}
  finally{importing=false;renderThumbs('reference');$('go').disabled=submitting||state?.status==='running';$('reference-file').value='';}
};
$('reference-file').onchange=e=>addFiles(e.target.files,'reference');
$('reference-pick').onclick=()=>{pasteTarget='reference';$('reference-file').click();};
$('reference-drop').onclick=()=>{$('reference-drop').focus();pasteTarget='reference';};
$('reference-drop').onkeydown=e=>{if(e.key==='Enter')$('reference-file').click();};
['dragenter','dragover'].forEach(n=>$('reference-drop').addEventListener(n,e=>e.preventDefault()));
$('reference-drop').addEventListener('drop',e=>{e.preventDefault();pasteTarget='reference';addFiles(e.dataTransfer.files,'reference');});
document.addEventListener('focusin',e=>{if(e.target.closest('#reference-upload'))pasteTarget='reference';$('reference-drop').classList.toggle('paste-active',pasteTarget==='reference');if(pasteTarget==='reference'){$('color-drop').classList.remove('paste-active');$('drop').classList.remove('paste-active');}});
const imageURL=(doc,a)=>`${API}/image?id=${encodeURIComponent(doc.id)}&asset=${encodeURIComponent(a.id)}`;
const reviewPhotos=doc=>doc.workflow_version===4&&doc.status==='reviewing'?doc.assets.filter(a=>a.selected&&a.origin!=='reference'&&typeof a.usable==='boolean'):doc.assets;
const originalSources=renderSources;
renderSources=function(doc){
  if(doc.workflow_version!==4){originalSources(doc);return;}
  if(photoJob!==doc.id){photoJob=doc.id;photoChoices=new Set(doc.assets.filter(a=>a.selected).map(a=>a.id));photoUses={};photoCrops={};}
  if(doc.status==='selecting'&&doc.assets.length&&!photoChoices.size&&!$('source-photo-list').children.length)photoChoices=new Set(doc.assets.filter(a=>a.selected).map(a=>a.id));
  const visible=reviewPhotos(doc);
  $('source-gallery').hidden=!visible.length;$('source-gallery').open=['selecting','reviewing'].includes(doc.status);
  const signature=JSON.stringify([doc.id,doc.status,doc.assets,Array.from(photoChoices)]);
  if($('source-photo-list').dataset.signature!==signature){
    $('source-photo-list').dataset.signature=signature;
    $('source-photo-list').replaceChildren(...visible.map(a=>{
      const figure=sourcePhoto(doc,a);
      if(doc.status==='selecting'){
        const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=photoChoices.has(a.id);
        check.onchange=()=>togglePhoto(a.id,check.checked);label.append(check,document.createTextNode(' 사용'));figure.append(label);
        figure.classList.toggle('photo-excluded',!photoChoices.has(a.id));
      }return figure;
    }));
  }
  $('selected-photo-count').textContent=`${photoChoices.size}장 선택`;
  $('analyze-selected').textContent=`선택한 ${photoChoices.size}장 분석하기`;
  $('source-summary').textContent=`수집 ${doc.assets.length}장 · 선택 ${photoChoices.size}장`;
};
const originalPhoto=sourcePhoto;
sourcePhoto=function(doc,a){const f=originalPhoto(doc,a);f.querySelector('img').onclick=()=>openPhoto(a.id);f.querySelector('img').style.cursor='zoom-in';
  if(doc.workflow_version===4){f.querySelector('figcaption').textContent=(a.origin==='reference'?'참고용':a.use_as==='info'?'정보 자료':a.use_as==='product'?'제품 사진':'분석 전')+(a.view?' · '+a.view:'');
    if(doc.status==='reviewing'&&a.selected&&a.origin!=='reference'){
      const button=document.createElement('button');button.type='button';button.className='remove-analysis-photo';button.textContent='분석에서 제외';button.onclick=async e=>{e.stopPropagation();if(confirm('이 사진을 분석 결과와 생성 참조에서 제외할까요? 원본 파일은 보존됩니다.'))await selectionAction('remove_asset',{asset_id:a.id});};f.append(button);
    }
  }return f;};
function togglePhoto(id,value){value?photoChoices.add(id):photoChoices.delete(id);renderSources(state);}
function setAllPhotos(value){photoChoices=new Set(value?state.assets.map(a=>a.id):[]);renderSources(state);}
async function confirmPhotos(){if(!photoChoices.size)return fail('사용할 실제 상품 사진을 선택해 주세요.');await selectionAction('select_photos',{asset_ids:Array.from(photoChoices),uses:photoUses});}
async function selectionAction(action,extra={}){
  if(submitting||state?.status==='running')return;submitting=true;fail('');
  try{if(['select_photos','back_to_selection'].includes(action))planJob=null;const result=await api('/action?id='+encodeURIComponent(job),{action,...extra});state=result;renderDraft(result);await poll();return true;}
  catch(e){fail(e.message);}finally{submitting=false;}
}
function showUpload(url){$('photo-large').src=url;$('photo-large').style.clipPath='none';$('photo-large').classList.remove('native-size');$('photo-modal-controls').replaceChildren();const zoom=document.createElement('button');zoom.textContent='원본 크기로 보기';zoom.onclick=()=>{const expanded=$('photo-large').classList.toggle('native-size');zoom.textContent=expanded?'화면에 맞추기':'원본 크기로 보기';};$('photo-modal-controls').append(zoom);if(!$('photo-modal').open)$('photo-modal').showModal();}
function openPhoto(id){modalId=id;const a=state.assets.find(a=>a.id===id);if(!a)return;showUpload(imageURL(state,a));
  const box=$('photo-modal-controls');
  if(state.status==='selecting'){
    const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=photoChoices.has(id);check.onchange=()=>togglePhoto(id,check.checked);label.append(check,document.createTextNode(' 이 사진 사용'));box.append(label);
    if(a.origin!=='reference'){
      const select=document.createElement('select');select.innerHTML='<option value="auto">용도 자동 분류</option><option value="product">제품 사진</option><option value="info">정보만 참고</option>';select.value=photoUses[id]||a.use_override||'auto';select.onchange=()=>photoUses[id]=select.value;box.append(select);
    }
  }
  if(state.workflow_version===4&&['reviewing','done','partial'].includes(state.status)&&a.origin!=='reference'){
    const text=document.createElement('p');text.textContent='제품 사진 사용 영역 (%) — 원본 글자를 제외할 수 있습니다. 기획 저장 시 적용됩니다.';box.append(text);
    const crop=photoCrops[id]||a.crop||a.product_bbox||[0,0,1,1];
    ['왼쪽','위','오른쪽','아래'].forEach((name,i)=>{
      const label=document.createElement('label'),input=document.createElement('input');input.type='number';input.min='0';input.max='100';input.value=Math.round(crop[i]*100);
      input.onchange=()=>{const c=photoCrops[id]||[...crop];c[i]=Math.max(0,Math.min(1,Number(input.value)/100));photoCrops[id]=c;$('photo-large').style.clipPath=`inset(${c[1]*100}% ${(1-c[2])*100}% ${(1-c[3])*100}% ${c[0]*100}%)`;};label.append(document.createTextNode(name),input);box.append(label);
    });
  }
}
function movePhoto(delta){const photos=state?reviewPhotos(state):[];if(!photos.length)return;const i=photos.findIndex(a=>a.id===modalId);openPhoto(photos[(i+delta+photos.length)%photos.length].id);}
['photo-modal','analysis-error-modal'].forEach(id=>$(id).addEventListener('click',e=>{if(e.target===$(id)){const r=$(id).getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$(id).close();}}));
$('photo-modal').addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')movePhoto(-1);if(e.key==='ArrowRight')movePhoto(1);});
const originalPlan=renderPlan;
renderPlan=function(doc){originalPlan(doc);if(doc.workflow_version!==4)return;
  const box=$('photo-review-fields');if(!doc.form?.product_name)return;
  const signature=JSON.stringify([doc.id,doc.form]);if(box.dataset.signature===signature)return;box.dataset.signature=signature;box.replaceChildren();
  const heading=document.createElement('h3');heading.textContent='PRODUCT INFO · 상품정보';box.append(heading);
  const help=document.createElement('p');help.textContent='제품 종류에 맞춰 추천한 항목입니다. 내용을 수정하거나 항목을 추가·삭제하세요. 빈 값은 상세페이지에 표시되지 않습니다.';box.append(help);
  const rows=document.createElement('div');rows.id='product-info-rows';box.append(rows);
  const values=Array.isArray(doc.form.product_info)?doc.form.product_info:[{label:'상품명',value:doc.form.product_name},{label:'컬러',value:doc.form.option},{label:'사이즈',value:doc.form.size},{label:'소재',value:''},{label:'제조국',value:''}];
  values.forEach(row=>addProductInfoRow(row));
  const add=document.createElement('button');add.type='button';add.textContent='+ 상품정보 항목 추가';add.onclick=()=>addProductInfoRow({label:'',value:''});box.append(add);
  if(doc.form.review_notes?.length){const note=document.createElement('p');note.className='draft-help';note.textContent='문구 검토 참고: '+doc.form.review_notes.join(' / ');box.append(note);}
  const save=document.createElement('button');save.textContent='문구·상품정보 저장';save.onclick=()=>savePlan();box.append(save);
  const replan=document.createElement('button');replan.type='button';replan.textContent='다시 기획하기';replan.title='현재 선택한 사진을 유지하고 상품 기획만 다시 만듭니다.';replan.onclick=async()=>{if(confirm('현재 선택한 사진으로 핵심장점과 상품정보를 다시 기획할까요?'))await selectionAction('replan');};box.append(replan);
  $('preserve-help').hidden=false;
};
function addProductInfoRow(row){
  const box=$('product-info-rows');if(box.children.length>=24)return fail('상품정보는 최대 24개입니다.');
  const line=document.createElement('div');line.className='product-info-row';
  const label=document.createElement('input');label.dataset.infoLabel='';label.placeholder='항목명';label.setAttribute('aria-label','상품정보 항목명');label.maxLength=60;label.value=row.label||'';
  const value=document.createElement('textarea');value.dataset.infoValue='';value.placeholder='입력 필요';value.setAttribute('aria-label','상품정보 내용');value.maxLength=600;value.rows=2;value.value=row.value||'';
  const remove=document.createElement('button');remove.type='button';remove.textContent='삭제';remove.onclick=()=>line.remove();line.append(label,value,remove);box.append(line);
}
const originalSave=savePlan;
savePlan=async function(){if(state?.workflow_version!==4)return originalSave();
  const form={};document.querySelectorAll('[data-field]').forEach(x=>form[x.dataset.field]=x.value);
  form.sellpoints=[0,1,2].map(i=>({title:document.querySelector(`[data-point-title="${i}"]`).value,desc:document.querySelector(`[data-point-desc="${i}"]`).value}));
  form.product_info=Array.from(document.querySelectorAll('.product-info-row')).map(row=>({label:row.querySelector('[data-info-label]').value,value:row.querySelector('[data-info-value]').value}));form.crops=photoCrops;
  return selectionAction('plan',{form});
};
async function confirmReview(){if(await savePlan())await selectionAction('generate_all');}
const originalRender=renderDraft;
renderDraft=function(doc){originalRender(doc);const v4=doc.workflow_version===4;
  $('photo-selection-actions').hidden=!v4||doc.status!=='selecting';$('photo-selection-bottom').hidden=!v4||doc.status!=='selecting';$('review-generate').hidden=!v4||doc.status!=='reviewing';
  $('back-photos').hidden=!v4||doc.status==='running'||doc.status==='selecting';
  $('generation-results').hidden=v4&&(doc.status==='selecting'||doc.status==='reviewing'||!doc.sections.length);
  if(v4){$('section-list').hidden=['selecting','reviewing'].includes(doc.status);$('plan-editor').hidden=!doc.form?.product_name;if(doc.status==='reviewing')$('plan-editor').open=true;
    document.querySelectorAll('.draft-section').forEach((card,i)=>{if([1,3,4,5,7,8].includes(i)){card.querySelector('.edit-label').hidden=true;card.querySelector('[data-kind=edit]').hidden=true;}});
    $('low-download').disabled=doc.sections.length!==10||!doc.sections.slice(0,9).every(s=>s.low);$('high-download').disabled=doc.sections.length!==10||!doc.sections.slice(0,9).every(s=>s.high);
    if(doc.failure_id&&doc.failure_id!==lastFailure){lastFailure=doc.failure_id;$('analysis-error-message').textContent=`${doc.failed_stage||'분석'}: ${doc.error}`;if(!$('analysis-error-modal').open)$('analysis-error-modal').showModal();}
  }else $('section-list').hidden=false;
};
document.addEventListener('click',e=>{if(e.target.tagName==='IMG'&&e.target.closest('#thumbs,#color-thumbs'))showUpload(e.target.src);});
const originalProgress=renderProgress;
renderProgress=function(doc){originalProgress(doc);if(doc.workflow_version!==4)return;
  const phases=['collection','selecting','analysis','reviewing','generation'];const names=['사진 수집','사진 선택','선택 사진 분석','문구 확인','이미지 생성'];
  let phase=phases.indexOf(doc.phase);if(phase<0)phase=0;
  $('draft-stages').replaceChildren(...names.map((name,i)=>{const li=document.createElement('li');li.textContent=name;li.className=i<phase||doc.status==='done'?'complete':i===phase?'current':'';return li;}));
  if(doc.status==='selecting')$('draft-timing').textContent='사진 선택을 기다리고 있습니다. 아직 AI 분석을 시작하지 않았습니다.';
  if(doc.status==='reviewing')$('draft-timing').textContent='핵심 장점과 상품정보를 확인한 뒤 전체 시안 생성을 눌러주세요. 사진은 자동 선정합니다.';
};
