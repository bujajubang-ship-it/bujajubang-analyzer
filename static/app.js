let allProducts = [];
let currentFilter = 'all';
let currentSort = { key: null, asc: false };

let allCandidates = [];
let currentChannel = 'all';
let currentSrcPage = 1;
let totalSrcPages = 1;
let activeFilters = {
  channel: 'all',      // 'all' | 'both' | 'coupang'  (server-side reload)
  grade: 'all',        // 'all' | '소싱추천' | '검토' | '비추'
  marginMin: 0,        // min margin % (0 = 전체)
  compMax: 100,        // max competition intensity (100 = 전체)
  minNet: 0,           // min net profit KRW
  pricePreset: 'all',  // 'all' | 'danger' | 'sweet' | 'premium' | 'custom'
  priceMin: null,
  priceMax: null,
  reviews: false,
  rocket: false,
  sort: 'score',       // 'score'|'margin'|'net'|'comp'|'price_asc'|'price_desc'
  categories: [],      // [] = 전체
};

// ── Page switching ──────────────────────────────────────────────────
function switchPage(page) {
  const isTracker   = page === 'tracker';
  const isSearch    = page === 'search';
  const isSourcing  = page === 'sourcing';
  const isPagemaker = page === 'pagemaker';
  const isCnmaker   = page === 'cnmaker';
  const isDanga     = page === 'danga';

  const navTracker = document.getElementById('nav-tracker');
  if (navTracker) navTracker.classList.toggle('active', isTracker);
  document.getElementById('nav-search').classList.toggle('active', isSearch);
  document.getElementById('nav-sourcing').classList.toggle('active', isSourcing);
  document.getElementById('nav-pagemaker').classList.toggle('active', isPagemaker);
  const navCn = document.getElementById('nav-cnmaker');
  if (navCn) navCn.classList.toggle('active', isCnmaker);
  const navDg = document.getElementById('nav-danga');
  if (navDg) navDg.classList.toggle('active', isDanga);

  const _trk = document.getElementById('tracker-section');
  if (_trk) _trk.classList.toggle('hidden', !isTracker);
  document.getElementById('hero-section').classList.toggle('hidden', !isSearch || currentSection !== 'hero');
  document.getElementById('loading-section').classList.toggle('hidden', !isSearch || currentSection !== 'loading');
  document.getElementById('result-section').classList.toggle('hidden', !isSearch || currentSection !== 'result');
  document.getElementById('sourcing-page').classList.toggle('hidden', !isSourcing);
  document.getElementById('pagemaker-page').classList.toggle('hidden', !isPagemaker);
  const _cn = document.getElementById('cnmaker-page');
  if (_cn) {
    _cn.classList.toggle('hidden', !isCnmaker);
    if (isCnmaker) {  // iframe 지연 로딩 (처음 열 때만)
      const fr = document.getElementById('cnmaker-frame');
      if (fr && !fr.src) fr.src = '/cnmaker';
    }
  }
  const _dg = document.getElementById('danga-page');
  if (_dg) _dg.classList.toggle('hidden', !isDanga);
  document.getElementById('header-search').style.display = isSearch && currentSection !== 'hero' ? 'flex' : 'none';

  if (isDanga) loadDanga();
  if (isTracker) loadTracker();
  if (isSourcing) {
    syncMemosFromServer().then(() => loadSourcing(currentSrcPage));
    _initCoupangPage();
  }
}

let currentSection = 'hero';

const MALL_COLORS = {
  coupang:     { bg: '#fff1f0', color: '#cf1322', label: '🛍️ 쿠팡' },
  '11st':      { bg: '#fff2e8', color: '#d46b08', label: '🔴 11번가' },
  gmarket:     { bg: '#fffbe6', color: '#d48806', label: '🟡 G마켓' },
  auction:     { bg: '#fff7e6', color: '#d46b08', label: '🟠 옥션' },
  ssg:         { bg: '#f9f0ff', color: '#531dab', label: '🟣 SSG' },
  lotte:       { bg: '#fff1f0', color: '#a8071a', label: '🔴 롯데온' },
  naver:       { bg: '#f6ffed', color: '#389e0d', label: '🟢 네이버' },
  wemakeprice: { bg: '#e6f4ff', color: '#096dd9', label: '🔵 위메프' },
  tmon:        { bg: '#fdf3e7', color: '#b45309', label: '🟤 티몬' },
  interpark:   { bg: '#fffbe6', color: '#b45309', label: '🟡 인터파크' },
  other:       { bg: '#f3f4f6', color: '#6b7280', label: '⬜ 기타' },
};

function mallBadge(type, name) {
  const m = MALL_COLORS[type] || MALL_COLORS.other;
  return `<span class="mall-badge" style="background:${m.bg};color:${m.color}">${name || m.label}</span>`;
}

function setKw(kw) {
  document.getElementById('hero-keyword').value = kw;
  doSearch();
}

function doSearch() {
  const heroInput = document.getElementById('hero-keyword');
  const headerInput = document.getElementById('header-keyword');
  const kw = (heroInput.value || headerInput.value || '').trim();
  if (!kw) { heroInput.focus(); return; }
  heroInput.value = kw;
  headerInput.value = kw;

  showSection('loading');

  const steps = ['네이버 쇼핑 API 검색 중...', '상품 데이터 수집 중...', '경쟁강도 분석 중...', '리포트 생성 중...'];
  let si = 0;
  const stepEl = document.getElementById('loading-step');
  const stepInterval = setInterval(() => {
    si = (si + 1) % steps.length;
    stepEl.textContent = steps[si];
  }, 700);

  fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword: kw }),
  }).then(res => {
    clearInterval(stepInterval);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    function read() {
      reader.read().then(({ done, value }) => {
        if (done) return;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.step === 'error') { alert(data.message); showSection('hero'); return; }
            if (data.step === 'progress' || data.step === 'start') {
              document.getElementById('loading-step').textContent = data.message;
            }
            if (data.step === 'done') { renderResult(data); showSection('result'); return; }
          } catch {}
        }
        read();
      });
    }
    read();
  }).catch(e => {
    clearInterval(stepInterval);
    alert('오류: ' + e.message);
    showSection('hero');
  });
}

function showSection(name) {
  currentSection = name;
  document.getElementById('hero-section').classList.toggle('hidden', name !== 'hero');
  document.getElementById('loading-section').classList.toggle('hidden', name !== 'loading');
  document.getElementById('result-section').classList.toggle('hidden', name !== 'result');
  document.getElementById('header-search').style.display = name !== 'hero' ? 'flex' : 'none';
}

function renderResult(data) {
  allProducts = data.products;
  currentFilter = 'all';
  currentSort = { key: null, asc: false };

  document.getElementById('result-kw').textContent = data.keyword;
  const source = data.source || '네이버 쇼핑';
  document.getElementById('result-meta').textContent =
    `${source} 검색 결과 약 ${data.total_results.toLocaleString()}개 중 상위 ${data.products.length}개 분석`;
  document.getElementById('dummy-badge').style.display = data.is_dummy ? '' : 'none';

  renderGauge(data.stats);
  renderStats(data.stats);
  renderMallBars(data.stats, data.products.length);
  renderInsights(data.stats, data.keyword);
  renderMallFilters(data.stats);
  renderTable(allProducts);
}

function renderGauge(stats) {
  const arc = document.getElementById('gauge-arc');
  const total = 283;
  arc.style.strokeDashoffset = total - (stats.score / 100) * total;
  arc.style.stroke = stats.color;
  document.getElementById('gauge-score').textContent = stats.score;
  document.getElementById('gauge-score').style.color = stats.color;
  document.getElementById('gauge-label').textContent = stats.label;
  document.getElementById('gauge-label').style.color = stats.color;
}

function renderStats(stats) {
  document.getElementById('stat-avg-price').textContent = '₩' + stats.avg_price.toLocaleString();
  document.getElementById('stat-min-price').textContent = '₩' + stats.min_price.toLocaleString();
  document.getElementById('stat-max-price').textContent = '₩' + stats.max_price.toLocaleString();
  document.getElementById('stat-unique-malls').textContent = stats.unique_malls + '개';
  document.getElementById('stat-top-mall').textContent = stats.top_mall;
  document.getElementById('stat-price-range').textContent = stats.price_range_ratio + '%';
}

function renderMallBars(stats, total) {
  const counts = stats.mall_counts || {};
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const maxCount = sorted[0]?.[1] || 1;
  document.getElementById('delivery-bars').innerHTML = sorted.map(([mall, cnt]) => {
    const mtype = Object.entries({
      쿠팡:'coupang','11번가':'11st','G마켓':'gmarket','옥션':'auction',
      'SSG.COM':'ssg','SSG닷컴':'ssg','롯데온':'lotte','위메프':'wemakeprice',
      '티몬':'tmon','인터파크':'interpark',
    }).find(([k]) => mall.includes(k))?.[1] || 'other';
    const m = MALL_COLORS[mtype] || MALL_COLORS.other;
    const pct = Math.round(cnt / total * 100);
    return `
      <div class="delivery-bar-row">
        <div class="delivery-bar-label" style="color:${m.color}">${mall}</div>
        <div class="delivery-bar-track">
          <div class="delivery-bar-fill" style="width:${Math.round(cnt/maxCount*100)}%;background:${m.color}"></div>
        </div>
        <div class="delivery-bar-count">${cnt}개 (${pct}%)</div>
      </div>`;
  }).join('');
}

function renderInsights(stats, kw) {
  const ins = [];
  const top = stats.top_mall || '-';
  const topCnt = (stats.mall_counts || {})[top] || 0;
  const topRatio = stats.unique_malls > 0 ? Math.round(topCnt / Object.values(stats.mall_counts || {}).reduce((a,b)=>a+b,0) * 100) : 0;

  if (topRatio >= 40) {
    ins.push({ color: '#ef4444', text: `<strong>${top}이 ${topRatio}%</strong> 점유 — 특정 플랫폼이 독점하고 있어 다른 채널 진입이 불리합니다.` });
  } else {
    ins.push({ color: '#22c55e', text: `<strong>${stats.unique_malls}개 플랫폼</strong>에 분산 — 경쟁이 다양해 신규 진입 가능성이 있습니다.` });
  }

  if (stats.price_range_ratio < 30) {
    ins.push({ color: '#ef4444', text: `가격 범위 편차 <strong>${stats.price_range_ratio}%</strong> — 가격이 좁게 몰려 있는 성숙 시장입니다.` });
  } else if (stats.price_range_ratio < 80) {
    ins.push({ color: '#eab308', text: `가격 범위 편차 <strong>${stats.price_range_ratio}%</strong> — 가격 포지셔닝 전략이 중요한 시장입니다.` });
  } else {
    ins.push({ color: '#22c55e', text: `가격 범위 편차 <strong>${stats.price_range_ratio}%</strong> — 가격 다양성이 높아 차별화 여지가 있습니다.` });
  }

  const avg = stats.avg_price;
  if (avg >= 500000) {
    ins.push({ color: '#f97316', text: `평균가 <strong>₩${avg.toLocaleString()}</strong> — 고가 시장으로 신뢰도·리뷰가 구매 결정에 크게 영향합니다.` });
  } else if (avg >= 100000) {
    ins.push({ color: '#eab308', text: `평균가 <strong>₩${avg.toLocaleString()}</strong> — 중가 시장으로 가격·스펙 비교가 핵심입니다.` });
  } else {
    ins.push({ color: '#22c55e', text: `평균가 <strong>₩${avg.toLocaleString()}</strong> — 저가 시장으로 가격 경쟁이 치열하지만 진입 비용이 낮습니다.` });
  }

  document.getElementById('insight-box').innerHTML = ins.map(i => `
    <div class="insight-item">
      <div class="insight-dot" style="background:${i.color}"></div>
      <div class="insight-text">${i.text}</div>
    </div>`).join('');
}

function renderMallFilters(stats) {
  const counts = stats.mall_counts || {};
  const top5 = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([m])=>m);
  const wrap = document.getElementById('table-filters');
  wrap.innerHTML = `<button class="filter-btn active" onclick="filterMall('all',this)">전체</button>` +
    top5.map(m => `<button class="filter-btn" onclick="filterMall('${esc(m)}',this)">${esc(m)}</button>`).join('');
}

function filterMall(mall, btn) {
  currentFilter = mall;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const filtered = mall === 'all' ? allProducts : allProducts.filter(p => p.mall === mall);
  renderTable(filtered);
}

function sortBy(key) {
  currentSort = currentSort.key === key
    ? { key, asc: !currentSort.asc }
    : { key, asc: false };
  const filtered = currentFilter === 'all' ? allProducts : allProducts.filter(p => p.mall === currentFilter);
  const sorted = [...filtered].sort((a, b) => {
    let va = a[key], vb = b[key];
    if (typeof va === 'string') return currentSort.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    return currentSort.asc ? va - vb : vb - va;
  });
  renderTable(sorted);
}

function renderTable(products) {
  document.getElementById('product-tbody').innerHTML = products.map(p => {
    const mtype = p.delivery || 'other';
    return `
    <tr>
      <td><span class="rank-num ${p.rank <= 3 ? 'top' : ''}">${p.rank}</span></td>
      <td>
        <div class="product-name">
          <a href="${esc(p.url)}" target="_blank" rel="noopener" ${p.url ? '' : 'style="pointer-events:none"'}>${esc(p.name)}</a>
        </div>
        <div class="product-brand">${esc(p.brand)}</div>
        ${p.category ? `<div class="product-cat">${esc(p.category)}</div>` : ''}
      </td>
      <td>
        <div class="price-val">₩${p.price.toLocaleString()}</div>
        ${p.discount > 0 ? `<div class="price-discount">-${p.discount}% 할인 가능</div>` : ''}
      </td>
      <td>${mallBadge(mtype, p.mall)}</td>
    </tr>`;
  }).join('');
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.activeElement === document.getElementById('header-keyword')) {
    doSearch();
  }
});

// ── Sourcing ────────────────────────────────────────────────────────

function loadSourcing(page) {
  page = page || currentSrcPage;
  currentSrcPage = page;
  document.getElementById('sourcing-grid').innerHTML =
    '<div class="src-loading"><div class="spinner-big"></div><div style="margin-top:12px;font-size:15px;font-weight:600">소싱 데이터 로딩 중...</div></div>';

  const params = new URLSearchParams({ channel: currentChannel, page });
  fetch('/api/sourcing?' + params)
    .then(r => r.json())
    .then(data => {
      allCandidates = data.candidates;
      totalSrcPages = data.meta.total_pages;
      currentSrcPage = data.meta.page;
      renderSourcingMeta(data.meta);
      renderSourcingSummary(allCandidates);
      buildCategoryChips(allCandidates);
      applyAndRender();
      renderPagination(data.meta);
    })
    .catch(e => {
      document.getElementById('sourcing-grid').innerHTML =
        `<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--muted)">데이터 로딩 실패: ${esc(e.message)}</div>`;
    });
}

function renderSourcingMeta(meta) {
  document.getElementById('sourcing-meta').innerHTML = `
    <div class="sourcing-meta-item">
      <span class="sourcing-meta-label">마지막 업데이트</span>
      <span class="sourcing-meta-val">${esc(meta.last_run)}</span>
    </div>
    <div class="sourcing-meta-item">
      <span class="sourcing-meta-label">다음 업데이트</span>
      <span class="sourcing-meta-val">${esc(meta.next_run)}</span>
    </div>
    ${meta.is_dummy ? '<span class="dummy-badge">더미 데이터</span>' : ''}
  `;
}

function renderSourcingSummary(candidates) {
  const avgMargin = candidates.length
    ? Math.round(candidates.reduce((s, c) => s + c.margin.rate, 0) / candidates.length) : 0;
  const avgNet = candidates.length
    ? Math.round(candidates.reduce((s, c) => s + c.margin.net, 0) / candidates.length) : 0;
  const topScore = candidates.length ? candidates[0].score : 0;
  document.getElementById('sourcing-summary').innerHTML = `
    <div class="sum-card"><div class="sum-icon">📦</div><div><div class="sum-val">${candidates.length}개</div><div class="sum-label">추천 상품</div></div></div>
    <div class="sum-card"><div class="sum-icon">💰</div><div><div class="sum-val">${avgMargin}%</div><div class="sum-label">평균 마진율</div></div></div>
    <div class="sum-card"><div class="sum-icon">💵</div><div><div class="sum-val">₩${avgNet.toLocaleString()}</div><div class="sum-label">평균 순이익</div></div></div>
    <div class="sum-card"><div class="sum-icon">🏆</div><div><div class="sum-val">${topScore}점</div><div class="sum-label">최고 추천점수</div></div></div>
  `;
}

// ── 새 필터 시스템 ──────────────────────────────────────────────────

function setFilterChip(group, val, btn) {
  if (group === 'channel') {
    activeFilters.channel = val;
    currentChannel = val;
    document.querySelectorAll('#fg-channel .fchip').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadSourcing(1);
    return;
  }
  activeFilters[group] = val;
  document.querySelectorAll(`#fg-${group} .fchip`).forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyAndRender();
}

function setSort(val, btn) {
  activeFilters.sort = val;
  document.querySelectorAll('#fg-sort .sort-chip').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyAndRender();
}

function setMarginSlider(val) {
  activeFilters.marginMin = parseInt(val) || 0;
  const el = document.getElementById('margin-slider-val');
  if (el) el.textContent = val > 0 ? val + '%+' : '전체';
  _updateSliderFill('margin-slider', val, 50);
  applyAndRender();
}

function setCompSlider(val) {
  activeFilters.compMax = parseInt(val);
  const el = document.getElementById('comp-slider-val');
  if (el) el.textContent = val < 100 ? val + ' 이하' : '전체';
  _updateSliderFill('comp-slider', val, 100);
  applyAndRender();
}

function setNetSlider(val) {
  activeFilters.minNet = parseInt(val) || 0;
  const el = document.getElementById('net-slider-val');
  if (el) el.textContent = val > 0 ? '₩' + parseInt(val).toLocaleString() + '+' : '전체';
  _updateSliderFill('net-slider', val, 15000);
  applyAndRender();
}

function _updateSliderFill(id, val, max) {
  const el = document.getElementById(id);
  if (!el) return;
  const pct = Math.round((val / max) * 100);
  el.style.setProperty('--pct', pct + '%');
  el.classList.toggle('has-value', pct > 0 && pct < 100);
}

function toggleCategory(cat, btn) {
  const idx = activeFilters.categories.indexOf(cat);
  if (idx >= 0) {
    activeFilters.categories.splice(idx, 1);
    btn.classList.remove('active');
  } else {
    activeFilters.categories.push(cat);
    btn.classList.add('active');
  }
  const clearBtn = document.getElementById('cat-clear-btn');
  if (clearBtn) clearBtn.style.display = activeFilters.categories.length > 0 ? '' : 'none';
  applyAndRender();
}

function clearCategories() {
  activeFilters.categories = [];
  document.querySelectorAll('#fg-category .fchip').forEach(b => b.classList.remove('active'));
  const clearBtn = document.getElementById('cat-clear-btn');
  if (clearBtn) clearBtn.style.display = 'none';
  applyAndRender();
}

function buildCategoryChips(candidates) {
  const cats = [...new Set(candidates.map(c => c.category || '기타'))].sort();
  const wrap = document.getElementById('fg-category');
  if (!wrap) return;
  wrap.innerHTML = cats.map(cat =>
    `<button class="fchip" onclick="toggleCategory('${esc(cat)}',this)">${esc(cat)}</button>`
  ).join('');
}

function setPricePreset(val, btn) {
  activeFilters.pricePreset = val;
  document.querySelectorAll('#fg-price-preset .fchip').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const customEl = document.getElementById('filter-price-custom');
  if (val === 'custom') {
    customEl.style.display = '';
  } else {
    customEl.style.display = 'none';
    activeFilters.priceMin = null;
    activeFilters.priceMax = null;
  }
  applyAndRender();
}

function applyCustomPrice() {
  const minVal = parseInt(document.getElementById('price-min-input').value) || null;
  const maxVal = parseInt(document.getElementById('price-max-input').value) || null;
  activeFilters.priceMin = minVal;
  activeFilters.priceMax = maxVal;
  applyAndRender();
}

function toggleCondition(type, btn) {
  activeFilters[type] = !activeFilters[type];
  btn.classList.toggle('active', activeFilters[type]);
  applyAndRender();
}

function resetAllFilters() {
  activeFilters = {
    channel: currentChannel,
    grade: 'all',
    marginMin: 0,
    compMax: 100,
    minNet: 0,
    pricePreset: 'all',
    priceMin: null,
    priceMax: null,
    reviews: false,
    rocket: false,
    sort: 'score',
    categories: [],
  };
  document.querySelectorAll('#fg-grade .fchip').forEach(b => {
    b.classList.toggle('active', b.dataset.val === 'all');
  });
  document.querySelectorAll('#fg-price-preset .fchip').forEach(b => {
    b.classList.toggle('active', b.dataset.val === 'all');
  });
  document.querySelectorAll('#fg-sort .sort-chip').forEach(b => {
    b.classList.toggle('active', b.dataset.val === 'score');
  });
  document.querySelectorAll('#fg-category .fchip').forEach(b => b.classList.remove('active'));
  const catClear = document.getElementById('cat-clear-btn');
  if (catClear) catClear.style.display = 'none';
  const marginSlider = document.getElementById('margin-slider');
  if (marginSlider) { marginSlider.value = 0; marginSlider.classList.remove('has-value'); }
  document.getElementById('margin-slider-val').textContent = '전체';
  const compSlider = document.getElementById('comp-slider');
  if (compSlider) { compSlider.value = 100; compSlider.classList.remove('has-value'); }
  document.getElementById('comp-slider-val').textContent = '전체';
  const netSlider = document.getElementById('net-slider');
  if (netSlider) { netSlider.value = 0; netSlider.classList.remove('has-value'); }
  document.getElementById('net-slider-val').textContent = '전체';
  document.getElementById('filter-price-custom').style.display = 'none';
  document.getElementById('price-min-input').value = '';
  document.getElementById('price-max-input').value = '';
  document.getElementById('fg-reviews').classList.remove('active');
  document.getElementById('fg-rocket').classList.remove('active');
  applyAndRender();
}

function applyFilters(candidates) {
  return candidates.filter(c => {
    // 추천 등급
    if (activeFilters.grade !== 'all' && c.rec_label !== activeFilters.grade) return false;
    // 카테고리
    if (activeFilters.categories.length > 0 && !activeFilters.categories.includes(c.category || '기타')) return false;
    // 마진율 슬라이더
    if (activeFilters.marginMin > 0 && c.margin.rate < activeFilters.marginMin) return false;
    // 경쟁강도 슬라이더
    if (activeFilters.compMax < 100 && c.competition.intensity > activeFilters.compMax) return false;
    // 순이익 최소
    if (activeFilters.minNet > 0 && c.margin.net < activeFilters.minNet) return false;
    // 판매가 프리셋
    const p = activeFilters.pricePreset;
    if (p === 'danger' && c.selling > 20000) return false;
    if (p === 'sweet' && (c.selling < 30000 || c.selling > 80000)) return false;
    if (p === 'premium' && c.selling < 80000) return false;
    if (p === 'custom') {
      if (activeFilters.priceMin && c.selling < activeFilters.priceMin) return false;
      if (activeFilters.priceMax && c.selling > activeFilters.priceMax) return false;
    }
    // 조건
    if (activeFilters.reviews && !c.filters.reviews_ok) return false;
    if (activeFilters.rocket && !c.filters.rocket_ok) return false;
    return true;
  });
}

function sortCandidates(candidates) {
  const sorted = [...candidates];
  switch (activeFilters.sort) {
    case 'margin':    sorted.sort((a, b) => b.margin.rate - a.margin.rate); break;
    case 'net':       sorted.sort((a, b) => b.margin.net - a.margin.net); break;
    case 'comp':      sorted.sort((a, b) => a.competition.intensity - b.competition.intensity); break;
    case 'price_asc': sorted.sort((a, b) => a.selling - b.selling); break;
    case 'price_desc':sorted.sort((a, b) => b.selling - a.selling); break;
    default:          sorted.sort((a, b) => b.score - a.score); break;
  }
  return sorted;
}

function countActiveFilters() {
  let n = 0;
  if (activeFilters.grade !== 'all') n++;
  if (activeFilters.marginMin > 0) n++;
  if (activeFilters.compMax < 100) n++;
  if (activeFilters.minNet > 0) n++;
  if (activeFilters.pricePreset !== 'all') n++;
  if (activeFilters.reviews) n++;
  if (activeFilters.rocket) n++;
  if (activeFilters.categories.length > 0) n++;
  return n;
}

function applyAndRender() {
  const filtered = sortCandidates(applyFilters(allCandidates));
  const countEl = document.getElementById('sfil-count');
  const badgeEl = document.getElementById('filter-active-count');
  const n = countActiveFilters();
  if (badgeEl) {
    badgeEl.textContent = `${n}개 필터 적용`;
    badgeEl.classList.toggle('visible', n > 0);
  }
  if (countEl) {
    countEl.textContent = `${filtered.length}개 표시 중 / 전체 ${allCandidates.length}개`;
  }
  renderSourcingCards(filtered);
}

// 하위호환 — 기존 코드가 참조할 수 있는 구 함수명
function filterChannel(channel, btn) { setFilterChip('channel', channel, btn); }

function renderPagination(meta) {
  let el = document.getElementById('src-pagination');
  if (!el) {
    el = document.createElement('div');
    el.id = 'src-pagination';
    el.className = 'src-pagination';
    document.getElementById('sourcing-section').appendChild(el);
  }
  if (meta.total_pages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <button class="src-page-btn" onclick="goSrcPage(${meta.page - 1})" ${meta.page <= 1 ? 'disabled' : ''}>← 이전 100개</button>
    <span class="src-page-info">${meta.page} / ${meta.total_pages} 페이지 &nbsp;·&nbsp; 총 ${meta.total}개</span>
    <button class="src-page-btn" onclick="goSrcPage(${meta.page + 1})" ${meta.page >= meta.total_pages ? 'disabled' : ''}>다음 100개 →</button>
  `;
}

function goSrcPage(page) {
  loadSourcing(page);
  document.getElementById('sourcing-section').scrollIntoView({ behavior: 'smooth' });
}

// ── 이미지 순서대로 로드 ────────────────────────────────────────────
const _imgQueue = [];
let _imgRunning = false;

// ── 쿠팡 시장 데이터 지연 로드 ────────────────────────────────────
const _cpQueue = [];
let _cpRunning = false;

// ── 1688 실력상가 자동 로드 ───────────────────────────────────────
const _supplierQueue = [];
let _supplierRunning = false;

function _processNextSupplier() {
  if (_supplierQueue.length === 0) { _supplierRunning = false; return; }
  _supplierRunning = true;
  const { id, keyword } = _supplierQueue.shift();
  const el = document.getElementById('suppliers-body-' + id);
  if (!el || !el.isConnected || el.dataset.loaded) { _processNextSupplier(); return; }
  el.dataset.loaded = '1';
  loadRealSuppliers(id, keyword).finally(() => setTimeout(_processNextSupplier, 600));
}

function renderCoupangBox(d) {
  const ratioColor = d.coupang_ratio > 50 ? '#ef4444' : d.coupang_ratio > 25 ? '#f59e0b' : '#22c55e';
  return `
    <div class="src-coupang-title">🛍️ 쿠팡 시장 (네이버 기준)</div>
    <div class="src-coupang-grid">
      <div class="src-coupang-item"><div class="src-coupang-val">₩${d.price_min.toLocaleString()}</div><div class="src-coupang-label">최저가</div></div>
      <div class="src-coupang-item"><div class="src-coupang-val">₩${d.price_avg.toLocaleString()}</div><div class="src-coupang-label">평균가</div></div>
      <div class="src-coupang-item"><div class="src-coupang-val">₩${d.price_max.toLocaleString()}</div><div class="src-coupang-label">최고가</div></div>
      <div class="src-coupang-item"><div class="src-coupang-val" style="color:${ratioColor}">${d.coupang_ratio}%</div><div class="src-coupang-label">쿠팡 점유율</div></div>
      <div class="src-coupang-item"><div class="src-coupang-val">${d.coupang_count}개</div><div class="src-coupang-label">쿠팡 상품 수</div></div>
      <div class="src-coupang-item"><div class="src-coupang-val">${(d.total || 0).toLocaleString()}</div><div class="src-coupang-label">전체 검색량</div></div>
    </div>`;
}

function _processNextCoupang() {
  if (_cpQueue.length === 0) { _cpRunning = false; return; }
  _cpRunning = true;
  const { id, keyword } = _cpQueue.shift();
  const el = document.getElementById('cp-' + id);
  if (!el || !el.isConnected || el.dataset.loaded) { _processNextCoupang(); return; }
  el.dataset.loaded = '1';
  fetch('/api/coupang-market?keyword=' + encodeURIComponent(keyword))
    .then(r => r.json())
    .then(d => {
      if (d.error || d.coupang_count === 0) { el.style.display = 'none'; return; }
      el.innerHTML = renderCoupangBox(d);
    })
    .catch(() => { el.style.display = 'none'; })
    .finally(() => setTimeout(_processNextCoupang, 350));
}

function renderSourcingCards(candidates) {
  if (!candidates.length) {
    document.getElementById('sourcing-grid').innerHTML =
      '<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--muted)">조건에 맞는 상품이 없습니다. 필터를 조정해보세요.</div>';
    return;
  }
  document.getElementById('sourcing-grid').innerHTML = candidates.map(c => srcCard(c)).join('');
  candidates.forEach(c => renderMemoOnCard(c.id));
  updateMemoBadge();
  _imgQueue.length = 0;
  document.querySelectorAll('.src-img-wrap[data-name]').forEach(el => _imgQueue.push(el));
  if (!_imgRunning) _processNextImage();
  _cpQueue.length = 0;
  candidates.forEach(c => _cpQueue.push({ id: c.id, keyword: c.name }));
  if (!_cpRunning) _processNextCoupang();

  _supplierQueue.length = 0;
  candidates.forEach(c => _supplierQueue.push({ id: c.id, keyword: c.keywords_1688?.[0] || c.name }));
  if (!_supplierRunning) _processNextSupplier();
}

function _processNextImage() {
  if (_imgQueue.length === 0) { _imgRunning = false; return; }
  _imgRunning = true;
  const wrap = _imgQueue.shift();
  if (!wrap || !wrap.isConnected || wrap.dataset.loaded) { _processNextImage(); return; }
  wrap.dataset.loaded = '1';
  fetch(`/api/image?name=${encodeURIComponent(wrap.dataset.name)}`)
    .then(r => r.json())
    .then(d => {
      if (d.url) {
        const ph = wrap.querySelector('.src-img-placeholder');
        if (ph) {
          const img = document.createElement('img');
          img.src = d.url;
          img.alt = wrap.dataset.name;
          img.onerror = () => {};
          wrap.replaceChild(img, ph);
        }
      }
    })
    .catch(() => {})
    .finally(() => setTimeout(_processNextImage, 180));
}

function srcCard(c) {
  const borderCls = c.margin.rate >= 30 ? 'card-margin-good' : c.margin.rate >= 15 ? 'card-margin-ok' : 'card-margin-bad';
  const scoreCls = c.score >= 70 ? 'high' : c.score >= 50 ? 'mid' : 'low';
  const comp = c.competition;
  const compCls = comp.intensity < 30 ? 'comp-low' : comp.intensity < 50 ? 'comp-mid' : 'comp-high';
  const compW = Math.round(comp.intensity);
  const channelTag = c.channel === 'both'
    ? '<span class="src-channel-tag both">쿠팡+스마트스토어</span>'
    : '<span class="src-channel-tag coupang">쿠팡 전용</span>';
  const moq = c.moq || {};
  const breakdown = c.score_breakdown;
  const recCls = c.rec_cls || scoreCls;
  const recLabel = c.rec_label || (c.score >= 80 ? '소싱추천' : c.score >= 60 ? '검토' : '비추');

  const filters = [
    { ok: c.filters.reviews_ok, text: c.filters.reviews_ok ? `✓ 경쟁 리뷰 적음 (${comp.top_reviews}개)` : `✗ 경쟁 리뷰 많음 (${comp.top_reviews}개)` },
    { ok: c.filters.rocket_ok, text: c.filters.rocket_ok ? `✓ 로켓배송 적음 (${comp.rocket_ratio}%)` : `✗ 로켓배송 많음 (${comp.rocket_ratio}%)` },
    { ok: c.selling > 20000 && c.selling <= 80000, text: c.selling <= 20000 ? `⚠ 박리다매 위험 (~₩${(20000).toLocaleString()})` : c.selling <= 80000 ? `✓ 적정가 (₩${c.selling.toLocaleString()})` : `🔍 고가 신중 (₩${c.selling.toLocaleString()})` },
  ];

  return `
  <div class="src-card ${borderCls}" data-id="${c.id}" data-name="${esc(c.name)}">
    <div class="src-img-wrap" data-name="${esc(c.name)}">
      <span class="src-img-placeholder">📦</span>
      <div class="src-rank-overlay">#${c.rank}</div>
      <div class="src-score-overlay ${scoreCls}">${c.score}</div>
    </div>
    <div class="src-card-body">
      <div class="src-card-head">
        <span class="src-rec-badge ${recCls}">${recLabel}</span>
        <span class="src-category-tag">${esc(c.category)}</span>
        ${channelTag}
      </div>

      <div>
        <div class="src-name">${esc(c.name)}</div>
        <div class="src-related">연관: ${esc(c.related)}</div>
      </div>

      <div class="src-price-flow">
        <div class="src-price-flow-item">
          <div class="src-price-flow-label">매입가격</div>
          <div class="src-price-flow-val">₩${c.sourcing.toLocaleString()}</div>
        </div>
        <div class="src-price-flow-arrow">→</div>
        <div class="src-price-flow-item">
          <div class="src-price-flow-label">판매예상가</div>
          <div class="src-price-flow-val">₩${c.selling.toLocaleString()}</div>
        </div>
        <div class="src-price-flow-arrow">→</div>
        <div class="src-profit-hero">
          <div class="src-profit-net">+₩${c.margin.net.toLocaleString()}</div>
          <div class="src-profit-rate ${c.margin.rate >= 30 ? 'good' : c.margin.rate >= 15 ? 'ok' : 'bad'}">${c.margin.rate}% 마진</div>
        </div>
      </div>
      <div class="src-cost-detail">소싱 ₩${c.sourcing.toLocaleString()} · 물류 ₩${c.logistics.toLocaleString()} · 쿠팡수수료 ₩${c.margin.commission.toLocaleString()} (10.8%)</div>

      <div class="src-moq-box">
        <div class="src-moq-title">📦 MOQ 분석</div>
        <div class="src-moq-grid">
          <div class="src-moq-item">
            <div class="src-moq-val">${moq.qty || 0}개</div>
            <div class="src-moq-label">최소 주문 수량</div>
          </div>
          <div class="src-moq-item">
            <div class="src-moq-val">₩${(moq.total_purchase || 0).toLocaleString()}</div>
            <div class="src-moq-label">총 매입비용</div>
          </div>
          <div class="src-moq-item">
            <div class="src-moq-val">₩${(moq.total_profit || 0).toLocaleString()}</div>
            <div class="src-moq-label">전량판매 순이익</div>
          </div>
          <div class="src-moq-item">
            <div class="src-moq-val">${moq.break_even || 0}개</div>
            <div class="src-moq-label">손익분기 수량</div>
          </div>
        </div>
      </div>

      <div class="src-comp-row">
        <span class="src-comp-label">경쟁강도</span>
        <div class="src-comp-track"><div class="src-comp-fill ${compCls}" style="width:${compW}%"></div></div>
        <span class="src-comp-val" style="color:${comp.intensity<30?'var(--green)':comp.intensity<50?'var(--yellow)':'#ef4444'}">${comp.label}</span>
      </div>

      <div class="src-breakdown">
        <div class="src-breakdown-item"><div class="src-breakdown-val">${breakdown.competition}</div><div class="src-breakdown-label">경쟁</div></div>
        <div class="src-breakdown-item"><div class="src-breakdown-val">${breakdown.margin}</div><div class="src-breakdown-label">마진</div></div>
        <div class="src-breakdown-item"><div class="src-breakdown-val">${breakdown.relevance}</div><div class="src-breakdown-label">적합도</div></div>
        <div class="src-breakdown-item"><div class="src-breakdown-val">${breakdown.customer_fit}</div><div class="src-breakdown-label">고객핏</div></div>
      </div>

      <div class="src-filters">
        ${filters.map(f => `<span class="src-filter-tag ${f.ok ? 'ok' : 'bad'}">${f.text}</span>`).join('')}
      </div>

      <div class="src-coupang-box" id="cp-${c.id}">
        <div class="src-coupang-loading">🛍️ 쿠팡 시장 로딩 중...</div>
      </div>

      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px">🔑 키워드 클릭 → cninsider 검색 (가격범위 자동 적용) &nbsp;<span style="background:#fef9c3;color:#92400e;font-weight:700;padding:2px 6px;border-radius:4px;font-size:11px">⭐ 实力商家 배지 확인!</span></div>
        <div class="src-keywords">
          ${c.keywords_1688.map(kw => `<span class="src-kw-chip" onclick="open1688('${esc(kw)}',${c.sourcing})" title="cninsider 실력상가 검색">${esc(kw)} 🔗</span>`).join('')}
        </div>
      </div>

      <div class="src-fit-reason">${esc(c.fit_reason)}</div>

      <div class="src-suppliers" id="suppliers-${c.id}">
        <div class="src-suppliers-title">🏭 cninsider 실력상가 바로가기</div>
        <div class="src-suppliers-body" id="suppliers-body-${c.id}">
          <span style="color:#aaa;font-size:12px">⏳ 조회 중...</span>
        </div>
      </div>

      <div class="src-memo-area"></div>
    </div>
  </div>`;
}

function open1688(kw, sourcingKrw) {
  // cninsider 실력상가 검색 (가격범위 자동 적용)
  const params = new URLSearchParams({ keyword: kw });
  // 소싱 원가 기반 CNY 가격 범위 자동 설정 (1 CNY ≈ 190 KRW)
  if (sourcingKrw && sourcingKrw > 0) {
    const cny = sourcingKrw / 190;
    params.set('minPrice', Math.max(1, Math.round(cny * 0.4)));
    params.set('maxPrice', Math.round(cny * 3.5));
  }
  window.open('https://www.cninsider.co.kr/mall/#/search?' + params.toString(), '_blank', 'noopener');
}

async function loadRealSuppliers(cardId, keyword) {
  const body = document.getElementById('suppliers-body-' + cardId);
  if (!body) return;
  body.innerHTML = '<span style="color:#aaa;font-size:12px">⏳ cninsider 실력상가 조회 중...</span>';

  try {
    const res = await fetch('/api/sourcing/suppliers?keyword=' + encodeURIComponent(keyword));
    const data = await res.json();
    const suppliers = data.suppliers || [];

    if (!suppliers.length) {
      body.innerHTML = '<span style="color:#aaa;font-size:12px">실력상가 상품을 찾지 못했습니다</span>';
      return;
    }

    body.innerHTML = suppliers.map(s => `
      <a class="src-supplier-row src-supplier-product" href="${esc(s.url)}" target="_blank" rel="noopener">
        <img class="src-supplier-img" src="${esc(s.image)}" alt="" onerror="this.style.display='none'">
        <div class="src-supplier-info">
          <div class="src-supplier-top">
            <span class="src-sj-badge">⭐ 실력상가 ${esc(s.medal)}</span>
            <span class="src-supplier-stat">월 ${s.month_sold.toLocaleString()}건</span>
            <span class="src-supplier-stat">★ ${s.rating}</span>
            ${s.repurchase ? `<span class="src-supplier-stat">재구매 ${esc(s.repurchase)}</span>` : ''}
          </div>
          <div class="src-supplier-name">${esc(s.name)}</div>
          ${s.price ? `<div class="src-supplier-price">¥${esc(s.price)}</div>` : ''}
        </div>
      </a>`).join('');
  } catch (e) {
    body.innerHTML = '<span style="color:#e55;font-size:12px">조회 실패: ' + e.message + '</span>';
  }
}

// ── Memo system (localStorage) ───────────────────────────────────────

function getMemos() {
  try { return JSON.parse(localStorage.getItem('sourcing_memos') || '{}'); } catch { return {}; }
}

function _setLocal(memos) {
  localStorage.setItem('sourcing_memos', JSON.stringify(memos));
}

// 서버 메모 불러와서 로컬과 병합 (나중에 저장된 것 우선)
async function syncMemosFromServer() {
  try {
    const serverMemos = await fetch('/api/memos').then(r => r.json());
    const local = getMemos();
    const merged = { ...local };
    for (const [id, m] of Object.entries(serverMemos)) {
      if (!local[id] || new Date(m.savedAt) > new Date(local[id]?.savedAt)) {
        merged[id] = m;
      }
    }
    _setLocal(merged);
    updateMemoBadge();
  } catch {}
}

function saveMemo(id, name, text) {
  const memos = getMemos();
  if (text.trim()) {
    const entry = { text: text.trim(), name, savedAt: new Date().toLocaleString('ko-KR') };
    memos[id] = entry;
    _setLocal(memos);
    fetch(`/api/memos/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry) }).catch(() => {});
  } else {
    delete memos[id];
    _setLocal(memos);
    fetch(`/api/memos/${id}`, { method: 'DELETE' }).catch(() => {});
  }
  updateMemoBadge();
}

function deleteMemo(id) {
  const memos = getMemos();
  delete memos[id];
  _setLocal(memos);
  fetch(`/api/memos/${id}`, { method: 'DELETE' }).catch(() => {});
  updateMemoBadge();
}

function updateMemoBadge() {
  const count = Object.keys(getMemos()).length;
  const btn = document.getElementById('memo-fab');
  if (btn) btn.textContent = count > 0 ? `📝 내 메모 (${count})` : '📝 내 메모';
}

function openMemoEditor(id) {
  const card = document.querySelector(`.src-card[data-id="${id}"]`);
  if (!card) return;
  const existing = getMemos()[id]?.text || '';
  const memoArea = card.querySelector('.src-memo-area');
  memoArea.innerHTML = `
    <textarea class="src-memo-input" placeholder="이 상품에 대한 메모를 남기세요..." rows="3">${esc(existing)}</textarea>
    <div class="src-memo-btns">
      <button class="src-memo-save" onclick="submitMemo(${id})">저장</button>
      <button class="src-memo-cancel" onclick="renderMemoOnCard(${id})">취소</button>
    </div>
  `;
  memoArea.querySelector('textarea').focus();
}

function submitMemo(id) {
  const card = document.querySelector(`.src-card[data-id="${id}"]`);
  if (!card) return;
  const text = card.querySelector('.src-memo-input')?.value || '';
  saveMemo(id, card.dataset.name, text);
  renderMemoOnCard(id);
  if (document.getElementById('memo-panel')?.classList.contains('open')) renderMemoPanel();
}

function renderMemoOnCard(id) {
  const card = document.querySelector(`.src-card[data-id="${id}"]`);
  if (!card) return;
  const memo = getMemos()[id];
  const memoArea = card.querySelector('.src-memo-area');
  if (memo) {
    memoArea.innerHTML = `
      <div class="src-memo-saved">
        <span class="src-memo-icon">📌</span>
        <div class="src-memo-text">${esc(memo.text)}</div>
        <div class="src-memo-actions">
          <button class="src-memo-edit-btn" onclick="openMemoEditor(${id})" title="수정">✏️</button>
          <button class="src-memo-del-btn" onclick="deleteMemoFromCard(${id})" title="삭제">🗑️</button>
        </div>
      </div>
      <div class="src-memo-date">${memo.savedAt}</div>
    `;
  } else {
    memoArea.innerHTML = `<button class="src-memo-btn" onclick="openMemoEditor(${id})">📝 메모 남기기</button>`;
  }
}

function deleteMemoFromCard(id) {
  deleteMemo(id);
  renderMemoOnCard(id);
  if (document.getElementById('memo-panel')?.classList.contains('open')) renderMemoPanel();
}

function toggleMemoPanel() {
  const panel = document.getElementById('memo-panel');
  const overlay = document.getElementById('memo-overlay');
  const isOpen = panel.classList.toggle('open');
  overlay.style.display = isOpen ? 'block' : 'none';
  if (isOpen) renderMemoPanel();
}

function renderMemoPanel() {
  const memos = getMemos();
  const list = document.getElementById('memo-panel-list');
  const entries = Object.entries(memos).sort((a, b) => {
    return new Date(b[1].savedAt) - new Date(a[1].savedAt);
  });
  if (!entries.length) {
    list.innerHTML = '<div class="memo-panel-empty">저장된 메모가 없습니다.<br>카드 하단의 📝 메모 남기기를 눌러보세요.</div>';
    return;
  }
  list.innerHTML = entries.map(([id, m]) => `
    <div class="memo-panel-item" id="memo-item-${id}" onclick="jumpToCard(${id}, event)">
      <div class="memo-panel-name">${esc(m.name)} <span class="memo-panel-goto">→ 상품 보기</span></div>
      <div class="memo-panel-text">${esc(m.text)}</div>
      <div class="memo-panel-meta">
        <span class="memo-panel-date">${m.savedAt}</span>
        <button class="memo-panel-del" onclick="deleteMemoFromPanel(${id})">삭제</button>
      </div>
    </div>
  `).join('');
}

function deleteMemoFromPanel(id) {
  deleteMemo(id);
  document.getElementById(`memo-item-${id}`)?.remove();
  const card = document.querySelector(`.src-card[data-id="${id}"]`);
  if (card) renderMemoOnCard(id);
  const memos = getMemos();
  if (!Object.keys(memos).length) renderMemoPanel();
}

function jumpToCard(id, event) {
  if (event.target.classList.contains('memo-panel-del')) return;
  const card = document.querySelector(`.src-card[data-id="${id}"]`);
  if (card) {
    toggleMemoPanel();
    setTimeout(() => {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('card-highlight');
      setTimeout(() => card.classList.remove('card-highlight'), 1800);
    }, 300);
  } else {
    // 현재 페이지에 없으면 해당 페이지로 이동
    const targetPage = id <= 100 ? 1 : 2;
    toggleMemoPanel();
    goSrcPage(targetPage);
    setTimeout(() => {
      const c = document.querySelector(`.src-card[data-id="${id}"]`);
      if (c) {
        c.scrollIntoView({ behavior: 'smooth', block: 'center' });
        c.classList.add('card-highlight');
        setTimeout(() => c.classList.remove('card-highlight'), 1800);
      }
    }, 900);
  }
}

// ── Coupang Sourcing ─────────────────────────────────────────────────
let _cpOppLoaded = false;

function _initCoupangPage() {
  if (_cpOppLoaded) return;
  const oppSection = document.getElementById('cp-opp-section');
  const loading = document.getElementById('coupang-loading');
  oppSection.classList.add('hidden');
  loading.classList.remove('hidden');

  fetch('/api/coupang/opportunities')
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
      loading.classList.add('hidden');
      if (data.error) {
        document.getElementById('cp-opp-grid').innerHTML =
          '<p style="text-align:center;color:#ef4444;padding:30px">오류: ' + data.error + '</p>';
      } else {
        renderCpOpportunities(data.opportunities || []);
        if ((data.opportunities || []).length > 0) _cpOppLoaded = true;
      }
      oppSection.classList.remove('hidden');
    })
    .catch(e => {
      loading.classList.add('hidden');
      document.getElementById('cp-opp-grid').innerHTML =
        '<p style="text-align:center;color:#ef4444;padding:30px">오류: ' + e.message
        + '<br><button onclick="_cpOppLoaded=false;_initCoupangPage()" style="margin-top:12px;padding:8px 18px;background:#cf1322;color:#fff;border:none;border-radius:8px;cursor:pointer">다시 시도</button></p>';
      oppSection.classList.remove('hidden');
    });
}


function renderCpOpportunities(opps) {
  const grid = document.getElementById('cp-opp-grid');
  if (!opps.length) {
    grid.innerHTML = '<p style="text-align:center;color:#6b7280;padding:40px">분석 결과 없음 — 다시 스캔해보세요.</p>';
    return;
  }
  grid.innerHTML = opps.map(o => {
    const rr = o.rocket_ratio >= 0
      ? '<span class="opp-badge-rocket">🚀 로켓 ' + o.rocket_ratio + '%</span>'
      : '<span class="opp-badge-rocket" style="background:#f3f4f6;color:#9ca3af">🚀 데이터없음</span>';
    const revLabel = o.avg_reviews === 0
      ? '<span class="opp-tag good">리뷰 0개 (선점 기회)</span>'
      : o.avg_reviews < 100
        ? '<span class="opp-tag good">리뷰 ' + o.avg_reviews + '개 (경쟁 낮음)</span>'
        : '<span class="opp-tag">리뷰 평균 ' + o.avg_reviews.toLocaleString() + '개</span>';

    // 이미지가 있으면 바로 표시, 없으면 lazy-load (data-name으로 /api/image 호출)
    const hasImg = !!o.image;
    const imgHtml = hasImg
      ? '<img src="' + o.image + '" alt="" onerror="this.style.display=\'none\'" />'
      : '<span class="src-img-placeholder">📦</span>';
    const wrapAttr = hasImg ? '' : ' data-name="' + o.keyword + '"';

    return '<div class="opp-card">'
      + '<div class="opp-img-wrap"' + wrapAttr + '>'
      + imgHtml
      + '<div class="opp-score" style="background:' + o.color + '">' + o.score + '</div>'
      + '</div>'
      + '<div class="opp-body">'
      + '<div class="opp-kw">' + o.keyword + '</div>'
      + '<div class="opp-label" style="color:' + o.color + '">' + o.label + '</div>'
      + '<div class="opp-metrics">'
      + '<div class="opp-metric"><span class="opp-metric-icon">📦</span><span>검색결과 ' + (o.total || 0).toLocaleString() + '개</span></div>'
      + '<div class="opp-metric"><span class="opp-metric-icon">💰</span><span>평균가 ₩' + (o.avg_price || 0).toLocaleString() + '</span></div>'
      + '<div class="opp-metric"><span class="opp-metric-icon">🛍️</span><span>쿠팡 점유 ' + o.coupang_ratio + '%</span></div>'
      + '</div>'
      + '<div class="opp-tags">' + revLabel + rr + '</div>'
      + (o.landing_url
        ? '<a class="opp-btn" href="' + o.landing_url + '" target="_blank">쿠팡에서 보기 →</a>'
        : '<span class="opp-btn-dim">쿠팡 데이터 없음</span>')
      + '</div>'
      + '</div>';
  }).join('');

  // 이미지 없는 카드 lazy-load
  document.querySelectorAll('#cp-opp-grid .opp-img-wrap[data-name]').forEach(el => _imgQueue.push(el));
  if (!_imgRunning) _processNextImage();
}


// ── Page Maker ──────────────────────────────────────────────────────
let _pmLogoB64 = '';
let _pmRemoveLogoB64 = '';
let _pmLogoPos = 'bottom-right';
let _pmScrapedImages = []; // [{url, selected, isMain}]

function pmScrape() {
  const url = document.getElementById('pm-url').value.trim();
  if (!url) return;

  const btn    = document.getElementById('pm-scrape-btn');
  const status = document.getElementById('pm-scrape-status');
  btn.disabled = true;
  btn.textContent = '불러오는 중...';
  status.textContent = '이미지 스캔 중...';
  status.classList.remove('hidden', 'pm-status-err');

  fetch('/api/pagemaker/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      btn.textContent = '이미지 불러오기';

      if (data.error) {
        status.textContent = '오류: ' + data.error;
        status.classList.add('pm-status-err');
        return;
      }
      if (!data.images || data.images.length === 0) {
        status.textContent = '이미지를 찾지 못했습니다. URL을 확인해주세요.';
        status.classList.add('pm-status-err');
        return;
      }

      status.textContent = data.images.length + '개 이미지 발견' + (data.title ? ' — ' + data.title : '');
      status.dataset.title = data.title || '';
      status.dataset.desc  = data.description || '';
      _pmScrapedImages = data.images.map((u, i) => ({ url: u, selected: true, isMain: i === 0 }));
      pmRenderImages();

      document.getElementById('pm-images-card').classList.remove('hidden');
      document.getElementById('pm-logo-card').classList.remove('hidden');
      document.getElementById('pm-process-card').classList.remove('hidden');
      document.getElementById('pm-ai-card').classList.remove('hidden');
      pmUpdateSelCount();
    })
    .catch(e => {
      btn.disabled = false;
      btn.textContent = '이미지 불러오기';
      status.textContent = '연결 오류: ' + e.message;
      status.classList.add('pm-status-err');
    });
}

function pmRenderImages() {
  const grid = document.getElementById('pm-img-grid');
  grid.innerHTML = _pmScrapedImages.map((item, i) => {
    return '<div class="pm-img-card' + (item.selected ? '' : ' pm-img-desel') + '" id="pm-ic-' + i + '">'
      + '<img src="' + item.url + '" alt="" onerror="this.parentNode.style.display=\'none\'" loading="lazy" />'
      + '<div class="pm-img-controls">'
      + '<label class="pm-chk-label">'
      + '<input type="checkbox" ' + (item.selected ? 'checked' : '') + ' onchange="pmToggleSel(' + i + ',this.checked)" />'
      + ' 선택</label>'
      + '<div class="pm-type-toggle">'
      + '<button class="pm-type-btn' + (item.isMain ? ' active' : '') + '" onclick="pmSetType(' + i + ',true)">메인</button>'
      + '<button class="pm-type-btn' + (!item.isMain ? ' active' : '') + '" onclick="pmSetType(' + i + ',false)">상세</button>'
      + '</div>'
      + '</div>'
      + '</div>';
  }).join('');
}

function pmToggleSel(i, checked) {
  _pmScrapedImages[i].selected = checked;
  const card = document.getElementById('pm-ic-' + i);
  if (card) card.classList.toggle('pm-img-desel', !checked);
  pmUpdateSelCount();
}

function pmSetType(i, isMain) {
  _pmScrapedImages[i].isMain = isMain;
  // re-render just that card's buttons
  const card = document.getElementById('pm-ic-' + i);
  if (!card) return;
  card.querySelectorAll('.pm-type-btn').forEach((btn, j) => {
    btn.classList.toggle('active', j === (isMain ? 0 : 1));
  });
}

function pmSelectAll(checked) {
  _pmScrapedImages.forEach((item, i) => {
    item.selected = checked;
    const card = document.getElementById('pm-ic-' + i);
    if (card) {
      card.classList.toggle('pm-img-desel', !checked);
      const cb = card.querySelector('input[type=checkbox]');
      if (cb) cb.checked = checked;
    }
  });
  pmUpdateSelCount();
}

function pmUpdateSelCount() {
  const n = _pmScrapedImages.filter(x => x.selected).length;
  document.getElementById('pm-sel-count').textContent = n + '개 선택됨';
}

function pmRemoveLogoChanged(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    _pmRemoveLogoB64 = e.target.result.split(',')[1];
    const wrap = document.getElementById('pm-rl-preview-wrap');
    wrap.innerHTML = '<img src="' + e.target.result + '" class="pm-logo-thumb" alt="remove-logo" />'
      + '<span class="pm-logo-fname">' + file.name + '</span>';
  };
  reader.readAsDataURL(file);
}

function pmLogoChanged(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    _pmLogoB64 = e.target.result.split(',')[1];
    document.getElementById('pm-logo-name').textContent = file.name;

    // show small preview
    const wrap = document.getElementById('pm-logo-preview-wrap');
    wrap.innerHTML = '<img src="' + e.target.result + '" class="pm-logo-thumb" alt="logo" />'
      + '<span class="pm-logo-fname">' + file.name + '</span>';
  };
  reader.readAsDataURL(file);
}

function pmSetPos(btn) {
  document.querySelectorAll('.pm-pos-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _pmLogoPos = btn.dataset.pos;
}

let _pmUseAi = false;
let _pmAiProductData = {};
let _pmAiImageUrl = '';
let _pmAiSections = [];  // [{key, name, image_b64}]

function pmToggleAi(checked) {
  _pmUseAi = checked;
}

// ── AI 분석 ────────────────────────────────────────────────────────
async function pmAiAnalyze() {
  const btn    = document.getElementById('pm-ai-analyze-btn');
  const status = document.getElementById('pm-ai-analyze-status');
  btn.disabled = true;
  btn.textContent = '분석 중...';
  status.classList.remove('hidden');

  // 현재 입력된 상품 URL로 페이지 전체 분석
  const productUrl = (document.getElementById('pm-url') || {}).value?.trim()
                  || _pmAiImageUrl;

  if (!productUrl) { alert('상품 URL을 먼저 입력하세요.'); btn.disabled = false; btn.textContent = '🔍 AI 분석 시작'; return; }

  status.textContent = '📄 페이지 스크래핑 중... 상세이미지 + 텍스트 읽는 중 (20~40초)';

  try {
    const resp = await fetch('/api/pagemaker/analyze-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: productUrl }),
    });
    const data = await resp.json();
    if (data.error && !data.product_name) throw new Error(data.error);

    // 대표 이미지 URL 저장 (이미지 생성 시 시각 참조용)
    if (data._rep_image) _pmAiImageUrl = data._rep_image;

    _pmAiProductData = data;
    pmFillAiFields(data);
    document.getElementById('pm-ai-step2').classList.remove('hidden');
    status.textContent = '✅ 분석 완료! 내용을 확인·수정 후 생성을 시작하세요.';
  } catch (e) {
    status.textContent = '오류: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 AI 분석 시작';
  }
}

function pmFillAiFields(d) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
  set('pai-name',     d.product_name);
  set('pai-category', d.category);
  set('pai-features', d.key_features);
  set('pai-specs',    d.specs);
  set('pai-howto',    d.how_to_use);
  set('pai-target',   d.target_customer);
  set('pai-background', d.background);
  set('pai-mood',     d.mood);

  const mc = (d.main_color || '#2C5F8A').match(/#[0-9a-fA-F]{6}/);
  const sc = (d.sub_color  || '#F0F4F8').match(/#[0-9a-fA-F]{6}/);
  const mainHex = mc ? mc[0] : '#2C5F8A';
  const subHex  = sc ? sc[0] : '#F0F4F8';
  set('pai-main-color', mainHex);
  set('pai-main-color-text', d.main_color || mainHex);
  set('pai-sub-color',  subHex);
  set('pai-sub-color-text', d.sub_color || subHex);

  document.getElementById('pai-main-color').addEventListener('input', e => {
    document.getElementById('pai-main-color-text').value = e.target.value;
  });
  document.getElementById('pai-sub-color').addEventListener('input', e => {
    document.getElementById('pai-sub-color-text').value = e.target.value;
  });

  // 폰트 미리보기 업데이트
  pmUpdateFontPreview();
  document.getElementById('pai-font-family')?.addEventListener('change', pmUpdateFontPreview);
}

const FONT_CSS_MAP = {
  'Pretendard': "'Pretendard', sans-serif",
  '나눔고딕':   "'Nanum Gothic', sans-serif",
  '나눔명조':   "'Nanum Myeongjo', serif",
  '블랙한산스': "'Black Han Sans', sans-serif",
  '도현':       "'Do Hyeon', sans-serif",
  '주아':       "'Jua', sans-serif",
};
const FONT_GSTATIC = {
  'Pretendard': 'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css',
  '나눔고딕':   'https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700&display=swap',
  '나눔명조':   'https://fonts.googleapis.com/css2?family=Nanum+Myeongjo:wght@400;700&display=swap',
  '블랙한산스': 'https://fonts.googleapis.com/css2?family=Black+Han+Sans&display=swap',
  '도현':       'https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap',
  '주아':       'https://fonts.googleapis.com/css2?family=Jua&display=swap',
};

function pmUpdateFontPreview() {
  const sel = document.getElementById('pai-font-family');
  const preview = document.getElementById('pai-font-preview');
  if (!sel || !preview) return;
  const val = sel.value;
  if (!val) {
    preview.style.fontFamily = '';
    preview.textContent = '미리보기: 부자주방 위생용기 최고의 선택';
    return;
  }
  // Google Fonts 동적 로드
  if (FONT_GSTATIC[val] && !document.querySelector(`link[data-gfont="${val}"]`)) {
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = FONT_GSTATIC[val];
    link.setAttribute('data-gfont', val);
    document.head.appendChild(link);
  }
  preview.style.fontFamily = FONT_CSS_MAP[val] || '';
  preview.textContent = `미리보기 (${val}): 부자주방 위생용기 최고의 선택`;
}

function pmReadAiFields() {
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  return {
    product_name:    get('pai-name'),
    category:        get('pai-category'),
    key_features:    get('pai-features'),
    specs:           get('pai-specs'),
    how_to_use:      get('pai-howto'),
    target_customer: get('pai-target'),
    main_color:      get('pai-main-color-text') || get('pai-main-color'),
    sub_color:       get('pai-sub-color-text')  || get('pai-sub-color'),
    background:      get('pai-background'),
    mood:            get('pai-mood'),
    font_family:     get('pai-font-family'),
  };
}

// ── AI 생성 ────────────────────────────────────────────────────────
async function pmAiGenerate() {
  const productData = pmReadAiFields();
  const btn = document.getElementById('pm-ai-gen-btn');
  btn.disabled = true;
  btn.textContent = '생성 중...';

  const step3 = document.getElementById('pm-ai-step3');
  step3.classList.remove('hidden');
  const grid     = document.getElementById('pm-ai-preview-grid');
  const label    = document.getElementById('pm-ai-progress-label');
  const bar      = document.getElementById('pm-ai-progress-bar');
  const dlArea   = document.getElementById('pm-ai-dl-area');
  grid.innerHTML = '';
  dlArea.classList.add('hidden');
  _pmAiSections  = [];

  const TOTAL = 8;
  let done = 0;

  label.textContent = '생성 시작 중...';
  bar.style.width = '0%';

  try {
    const resp = await fetch('/api/pagemaker/ai-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_url: _pmAiImageUrl, product_data: productData }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        let evt;
        try { evt = JSON.parse(json); } catch { continue; }

        if (evt.done) {
          label.textContent = '✅ 생성 완료!';
          bar.style.width = '100%';
          pmBuildAiZip();
          break;
        }

        if (evt.error) {
          const div = document.createElement('div');
          div.className = 'pm-ai-section-err';
          div.textContent = (evt.name || evt.key) + ': ❌ ' + evt.error;
          grid.appendChild(div);
          done++;
        } else if (evt.image_b64) {
          done++;
          _pmAiSections.push(evt);
          const pct = Math.round(done / TOTAL * 100);
          bar.style.width = pct + '%';
          label.textContent = done + ' / ' + TOTAL + ' 완료 — ' + evt.name;

          const div = document.createElement('div');
          div.className = 'pm-ai-section-card';
          div.innerHTML = '<div class="pm-ai-sec-name">' + evt.name + '</div>'
            + '<img src="data:image/jpeg;base64,' + evt.image_b64 + '" alt="' + evt.name + '" />';
          grid.appendChild(div);
        }
      }
    }
  } catch (e) {
    label.textContent = '오류: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ AI 상세페이지 8장 생성';
  }
}

async function pmBuildAiZip() {
  if (_pmAiSections.length === 0) return;
  try {
    const zip = new JSZip();
    for (const sec of _pmAiSections) {
      const bytes = Uint8Array.from(atob(sec.image_b64), c => c.charCodeAt(0));
      zip.file(sec.key + '.jpg', bytes);
    }
    const blob = await zip.generateAsync({ type: 'blob' });
    const url  = URL.createObjectURL(blob);
    const link = document.getElementById('pm-ai-dl-link');
    link.href  = url;
    document.getElementById('pm-ai-dl-area').classList.remove('hidden');
  } catch (e) {
    console.error('zip 생성 오류:', e);
  }
}

async function pmProcess() {
  const selected = _pmScrapedImages.filter(x => x.selected);
  if (selected.length === 0) {
    alert('이미지를 최소 1개 선택하세요.');
    return;
  }

  const btn    = document.getElementById('pm-process-btn');
  const dlArea = document.getElementById('pm-dl-area');
  btn.disabled = true;
  btn.textContent = _pmUseAi ? '✨ AI 분석 중... (10~20초)' : '변환 중...';
  dlArea.classList.add('hidden');

  const sizePct = parseInt(document.getElementById('pm-size-range').value) / 100;

  // scrape 시 저장된 title/description 재사용
  const titleEl = document.getElementById('pm-scrape-status');
  const productTitle = titleEl ? (titleEl.dataset.title || '') : '';
  const productDesc  = titleEl ? (titleEl.dataset.desc  || '') : '';

  try {
    const resp = await fetch('/api/pagemaker/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        images:           selected.map(x => x.url),
        is_main:          selected.map(x => x.isMain),
        logo_b64:         _pmLogoB64 || null,
        logo_position:    _pmLogoPos,
        logo_size_pct:    sizePct,
        use_ai:           _pmUseAi,
        product_title:    productTitle,
        product_desc:     productDesc,
        remove_logo_b64:  _pmRemoveLogoB64 || null,
      }),
    });

    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(err);
    }

    const blob   = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const link   = document.getElementById('pm-dl-link');
    link.href    = objUrl;
    dlArea.classList.remove('hidden');
  } catch (e) {
    alert('변환 오류: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🚀 변환 시작';
  }
}


/* ============================================================
   📋 품목 진행상황 트래커 (메인 최상단) — 단계별·품목별 추적
   - tracker.json 서버 저장 (GET/POST/PUT/DELETE /api/tracker)
   - 각 품목 = 가로 8단계 스테퍼 (점 클릭으로 단계 이동)
   ============================================================ */
// ── 파이프라인 정의: 소싱 / 로켓 ──
const SOURCING_STAGES = [
  { key: 'keyword',  label: '키워드 발굴', emoji: '🔍', color: '#6366f1', bg: '#eef2ff' },
  { key: 'analysis', label: '시장성 분석', emoji: '📊', color: '#8b5cf6', bg: '#f5f3ff' },
  { key: 'research', label: '소싱처 조사', emoji: '🏭', color: '#ec4899', bg: '#fdf2f8' },
  { key: 'confirm',  label: '소싱 확정',   emoji: '🎯', color: '#f97316', bg: '#fff7ed' },
];
const ROCKET_STAGES = [
  { key: 'item',         label: '아이템선정', emoji: '🎯', color: '#6366f1', bg: '#eef2ff' },
  { key: 'sample_order', label: '샘플주문',   emoji: '🛒', color: '#8b5cf6', bg: '#f5f3ff' },
  { key: 'sample_check', label: '샘플검수',   emoji: '🔬', color: '#a855f7', bg: '#faf5ff' },
  { key: 'upload',       label: '업로드',     emoji: '⬆️', color: '#ec4899', bg: '#fdf2f8' },
  { key: 'order',        label: '발주',       emoji: '🧾', color: '#f97316', bg: '#fff7ed' },
  { key: 'detail',       label: '상세페이지', emoji: '🎨', color: '#eab308', bg: '#fefce8' },
  { key: 'kwset',        label: '키워드세팅', emoji: '🏷️', color: '#14b8a6', bg: '#f0fdfa' },
  { key: 'stock_in',     label: '쿠팡입고',   emoji: '📦', color: '#06b6d4', bg: '#ecfeff' },
  { key: 'selling',      label: '판매중',     emoji: '🟢', color: '#22c55e', bg: '#f0fdf4' },
  { key: 'defense',      label: '최적화·방어', emoji: '🛡️', color: '#8b5cf6', bg: '#f5f3ff' },
];
const PIPELINES = {
  sourcing: { key: 'sourcing', title: '🧭 소싱 파이프라인', sub: '키워드 발굴 → 소싱 확정', stages: SOURCING_STAGES },
  rocket:   { key: 'rocket',   title: '🚀 로켓 파이프라인', sub: '아이템선정 → 최적화·방어', stages: ROCKET_STAGES },
};
const PIPELINE_ORDER = ['sourcing', 'rocket'];

// 개별 입력 필드 (8가지)
const ITEM_FIELDS = [
  { key: 'link1688',        label: '1688 링크',  type: 'url',      ph: 'https://...' },
  { key: 'orderOption',     label: '주문옵션',   type: 'text',     ph: '예: 베이지-8팩' },
  { key: 'unitPrice',       label: '단가',       type: 'text',     ph: '예: 3.35위안' },
  { key: 'orderQty',        label: '발주량',     type: 'number',   ph: '예: 30' },
  { key: 'coupangLink',     label: '쿠팡 링크',  type: 'url',      ph: 'https://...' },
  { key: 'initialPrice',    label: '최초판매가', type: 'number',   ph: '원' },
  { key: 'expectedRevenue', label: '기대매출',   type: 'number',   ph: '원' },
  { key: 'memo',            label: '메모',       type: 'textarea', ph: '특이사항' },
];

// 구버전(단일 트래커) 데이터 → 파이프라인 마이그레이션 매핑
const LEGACY_STAGE_MAP = {
  keyword:  { pipeline: 'sourcing', stage: 'keyword' },
  analysis: { pipeline: 'sourcing', stage: 'analysis' },
  sourcing: { pipeline: 'sourcing', stage: 'research' },
  sample:   { pipeline: 'rocket',   stage: 'sample_order' },
  order:    { pipeline: 'rocket',   stage: 'order' },
  detail:   { pipeline: 'rocket',   stage: 'detail' },
  kwset:    { pipeline: 'rocket',   stage: 'kwset' },
  listing:  { pipeline: 'rocket',   stage: 'upload' },
  selling:  { pipeline: 'rocket',   stage: 'selling' },
  defense:  { pipeline: 'rocket',   stage: 'defense' },
};

let trackerItems = {};   // { id: {name, pipeline, stage, ...8필드} }
let trackerEdit = null;  // 펼쳐서 편집 중인 id
let pipeSort = { sourcing: { key: 'name', asc: true }, rocket: { key: 'name', asc: true } };
let pipeSearch = { sourcing: '', rocket: '' };
let pipeActive = { sourcing: true, rocket: true };  // 파이프라인 활성/비활성 (제목 옆 토글)

function _trkEsc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _trkAttr(s) { return _trkEsc(s).replace(/"/g, '&quot;'); }
function _trkNum(v) { const n = parseFloat(String(v == null ? '' : v).replace(/[^0-9.-]/g, '')); return isFinite(n) ? n : 0; }
function _trkWon(v) { return (v === '' || v == null) ? '' : _trkNum(v).toLocaleString('ko-KR') + '원'; }
function pipeStages(p) { return (PIPELINES[p] || PIPELINES.sourcing).stages; }

// 로드 시 구버전 데이터 정규화 (서버는 편집 전까지 원본 유지)
function normalizeItem(it) {
  if (!it.pipeline) {
    const m = LEGACY_STAGE_MAP[it.stage] || { pipeline: 'sourcing', stage: it.stage };
    it.pipeline = m.pipeline;
    it.stage = m.stage;
  }
  return it;
}

async function loadTracker() {
  try {
    const res = await fetch('/api/tracker');
    trackerItems = await res.json();
  } catch (e) { trackerItems = {}; }
  Object.values(trackerItems).forEach(normalizeItem);
  renderTracker();
}

async function addTrackerItem(pipeline) {
  const name = prompt('추가할 품목 이름을 입력하세요');
  if (!name || !name.trim()) return;
  await fetch('/api/tracker', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name.trim(), pipeline, stage: pipeStages(pipeline)[0].key }),
  });
  loadTracker();
}

async function setTrackerStage(id, idx) {
  const it = trackerItems[id];
  if (!it) return;
  const stages = pipeStages(it.pipeline);
  if (idx < 0 || idx >= stages.length) return;
  await fetch(`/api/tracker/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pipeline: it.pipeline, stage: stages[idx].key }),
  });
  loadTracker();
}

async function moveTrackerStage(id, dir) {
  const it = trackerItems[id];
  if (!it) return;
  const idx = pipeStages(it.pipeline).findIndex(s => s.key === it.stage);
  await setTrackerStage(id, idx + dir);
}

async function deleteTrackerItem(id) {
  if (!confirm('이 품목을 목록에서 삭제할까요?')) return;
  await fetch(`/api/tracker/${id}`, { method: 'DELETE' });
  loadTracker();
}

function editTrackerItem(id) { trackerEdit = id; renderTracker(); }
function cancelTrackerEdit() { trackerEdit = null; renderTracker(); }

async function saveTrackerItem(id) {
  const it = trackerItems[id] || {};
  const payload = { pipeline: it.pipeline, stage: it.stage };
  ITEM_FIELDS.forEach(f => {
    const el = document.getElementById(`trk-f-${f.key}-${id}`);
    if (el) payload[f.key] = el.value;
  });
  // 로켓 파이프라인: 기대매출은 수동입력 없이 예상총마진 자동계산으로 저장
  if (it.pipeline === 'rocket') {
    const per = _trkNum(payload.initialPrice) * 0.881 - _trkNum(payload.unitPrice) * 350;
    payload.expectedRevenue = String(Math.round(per * _trkNum(payload.orderQty)));
  }
  await fetch(`/api/tracker/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  trackerEdit = null;
  loadTracker();
}

// 정렬 / 검색
function sortPipe(p, key) {
  const s = pipeSort[p];
  if (s.key === key) s.asc = !s.asc; else { s.key = key; s.asc = true; }
  renderTracker();
}
function onPipeSearch(p, val) {
  pipeSearch[p] = val;
  renderPipeList(p);  // 리스트만 갱신 → 검색창 포커스 유지
}

// 파이프라인 활성/비활성 토글 (제목 옆)
function togglePipeActive(p) {
  pipeActive[p] = !pipeActive[p];
  renderPipeToggles();
  renderTracker();
}
function renderPipeToggles() {
  const el = document.getElementById('pipe-toggles');
  if (!el) return;
  el.innerHTML = PIPELINE_ORDER.map(p =>
    `<button class="pipe-tab${pipeActive[p] ? ' active' : ''}" onclick="togglePipeActive('${p}')">${PIPELINES[p].title}</button>`
  ).join('');
}

function toggleTrackerSection() {
  const list = document.getElementById('tracker-list');
  const btn  = document.getElementById('tracker-collapse-btn');
  if (!list) return;
  const collapsed = list.style.display === 'none';
  list.style.display = collapsed ? '' : 'none';
  if (btn) btn.textContent = collapsed ? '접기 ▲' : '펼치기 ▼';
}

// 📌 쿠팡 소싱 마스터 가이드 — 접이식 공지 보드 (localStorage 미사용, 기본 접힘)
function toggleGuideBoard() {
  const b = document.getElementById('guide-board');
  if (b) b.classList.toggle('collapsed');
}

// 가이드 보드 내부 아코디언 — 각 섹션 독립 토글 (여러 개 동시 열림 허용, localStorage 미사용)
function toggleAcc(btn) {
  const acc = btn.closest('.gacc');
  if (acc) acc.classList.toggle('open');
}

function fieldSummary(it) {
  const chips = [];
  if (it.unitPrice)       chips.push(`<span class="trk-chip">단가 ${_trkEsc(it.unitPrice)}</span>`);
  if (it.orderQty)        chips.push(`<span class="trk-chip">발주 ${_trkEsc(it.orderQty)}개</span>`);
  if (it.initialPrice)    chips.push(`<span class="trk-chip">판매가 ${_trkWon(it.initialPrice)}</span>`);
  if (it.expectedRevenue) chips.push(`<span class="trk-chip strong">기대매출 ${_trkWon(it.expectedRevenue)}</span>`);
  const links = [];
  if (it.link1688)   links.push(`<a class="trk-link" href="${_trkAttr(it.link1688)}" target="_blank" rel="noopener">🔗 1688</a>`);
  if (it.coupangLink) links.push(`<a class="trk-link cp" href="${_trkAttr(it.coupangLink)}" target="_blank" rel="noopener">🛒 쿠팡</a>`);
  if (it.memo) chips.push(`<span class="trk-chip memo">📝 ${_trkEsc(it.memo)}</span>`);
  return (chips.length || links.length) ? `<div class="trk-summary">${links.join('')}${chips.join('')}</div>` : '';
}

function _trkMarginHtml(price, unit, qty) {
  const p = _trkNum(price), u = _trkNum(unit), q = _trkNum(qty);
  const per = Math.round(p * 0.881 - u * 350);
  const tot = Math.round(per * q);
  const pc = per >= 0 ? '#059669' : '#dc2626', tc = tot >= 0 ? '#059669' : '#dc2626';
  return `개당마진 <b style="color:${pc}">${per.toLocaleString('ko-KR')}원</b> = (판매가 × 0.881) − (단가 × <b>350</b>)<br>`
       + `예상총마진 <b style="color:${tc}">${tot.toLocaleString('ko-KR')}원</b> = 개당마진 × 발주량${q ? ' (' + q + '개)' : ''}<br>`
       + `<span style="color:#9ca3af;font-size:11px">※ 350 = 환율+판관비 상수 · 0.881 = 쿠팡 정산비율</span>`;
}
function trkCalcMargin(id) {
  const g = k => { const e = document.getElementById(`trk-f-${k}-${id}`); return e ? e.value : ''; };
  const box = document.getElementById(`trk-margin-${id}`);
  if (box) box.innerHTML = _trkMarginHtml(g('initialPrice'), g('unitPrice'), g('orderQty'));
}
function editForm(id, it) {
  const isRocket = it.pipeline === 'rocket';
  const calcKeys = { initialPrice: 1, unitPrice: 1, orderQty: 1 };
  const fields = ITEM_FIELDS.map(f => {
    if (isRocket && f.key === 'expectedRevenue') {
      return `<label class="trk-f full"><span>마진 (자동계산)</span>`
        + `<div id="trk-margin-${id}" style="padding:9px 11px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;font-size:13px;line-height:1.7">${_trkMarginHtml(it.initialPrice, it.unitPrice, it.orderQty)}</div></label>`;
    }
    const oninp = (isRocket && calcKeys[f.key]) ? ` oninput="trkCalcMargin('${id}')"` : '';
    const input = f.type === 'textarea'
      ? `<textarea id="trk-f-${f.key}-${id}" rows="2" placeholder="${f.ph}">${_trkEsc(it[f.key])}</textarea>`
      : `<input id="trk-f-${f.key}-${id}" type="${f.type === 'number' ? 'number' : 'text'}" value="${_trkAttr(it[f.key])}" placeholder="${f.ph}"${oninp} />`;
    return `<label class="trk-f${f.type === 'textarea' ? ' full' : ''}"><span>${f.label}</span>${input}</label>`;
  }).join('');
  return `<div class="trk-edit">
    <div class="trk-edit-grid">${fields}</div>
    <div class="trk-edit-btns">
      <button class="trk-btn-save" onclick="saveTrackerItem('${id}')">저장</button>
      <button class="trk-btn-cancel" onclick="cancelTrackerEdit()">취소</button>
    </div>
  </div>`;
}

function trackerRow(id, it) {
  const stages = pipeStages(it.pipeline);
  let curIdx = stages.findIndex(s => s.key === it.stage);
  if (curIdx < 0) curIdx = 0;
  const cur = stages[curIdx];
  const editing = trackerEdit === id;

  const stepper = stages.map((s, i) => {
    const done = i < curIdx, active = i === curIdx;
    const dotStyle = active
      ? `background:${s.color};border-color:${s.color};color:#fff;box-shadow:0 0 0 4px ${s.color}40,0 4px 14px ${s.color}70`
      : done ? `background:${s.color}22;border-color:${s.color};color:${s.color}`
             : 'background:#f3f4f6;border-color:#d1d5db;color:#9ca3af';
    const labelStyle = active ? `color:${s.color};font-weight:800` : done ? `color:${s.color}` : 'color:#9ca3af';
    const lineStyle = (done || active) ? `background:${s.color}` : 'background:#e5e7eb';
    const dot = done ? '✓' : s.emoji;
    return `
      <div class="trk-step-wrap">
        <button class="trk-step-dot" style="${dotStyle}" onclick="setTrackerStage('${id}',${i})" title="${s.label}">${dot}</button>
        <div class="trk-step-label" style="${labelStyle}">${s.label}</div>
      </div>
      ${i < stages.length - 1 ? `<div class="trk-step-line" style="${lineStyle}"></div>` : ''}`;
  }).join('');

  return `
  <div class="trk-row">
    <div class="trk-row-head">
      <div class="trk-row-left">
        <span class="trk-cur-badge" style="color:${cur.color};background:${cur.bg}">${cur.emoji} ${cur.label}</span>
        <span class="trk-name">${_trkEsc(it.name)}</span>
      </div>
      <div class="trk-row-actions">
        <button class="trk-arrow" onclick="moveTrackerStage('${id}',-1)" ${curIdx > 0 ? '' : 'disabled'}>←</button>
        <button class="trk-arrow" onclick="moveTrackerStage('${id}',1)" ${curIdx < stages.length - 1 ? '' : 'disabled'}>→</button>
        <button class="trk-memo-btn" onclick="${editing ? 'cancelTrackerEdit()' : `editTrackerItem('${id}')`}">${editing ? '닫기' : '✏️ 입력'}</button>
        <button class="trk-del" onclick="deleteTrackerItem('${id}')" title="삭제">🗑</button>
      </div>
    </div>
    <div class="trk-stepper">${stepper}</div>
    ${fieldSummary(it)}
    ${editing ? editForm(id, it) : ''}
  </div>`;
}

function pipeItemsSorted(p) {
  const s = pipeSort[p];
  const q = (pipeSearch[p] || '').trim().toLowerCase();
  let ids = Object.keys(trackerItems).filter(id => (trackerItems[id].pipeline || 'sourcing') === p);
  if (q) ids = ids.filter(id => String(trackerItems[id].name || '').toLowerCase().includes(q));
  ids.sort((a, b) => {
    const A = trackerItems[a], B = trackerItems[b];
    const r = s.key === 'name'
      ? String(A.name || '').localeCompare(String(B.name || ''), 'ko')
      : _trkNum(A[s.key]) - _trkNum(B[s.key]);
    return s.asc ? r : -r;
  });
  return ids;
}

function renderPipeList(p) {
  const el = document.getElementById(`pipe-list-${p}`);
  if (!el) return;
  const ids = pipeItemsSorted(p);
  if (!ids.length) {
    const q = (pipeSearch[p] || '').trim();
    el.innerHTML = `<div class="trk-empty">${q ? '검색 결과가 없습니다.' : '아직 품목이 없습니다. <strong>+ 품목 추가</strong>로 시작하세요.'}</div>`;
    return;
  }
  el.innerHTML = ids.map(id => trackerRow(id, trackerItems[id])).join('');
}

function pipeHeader(p) {
  const cfg = PIPELINES[p];
  const s = pipeSort[p];
  const total = Object.keys(trackerItems).filter(id => (trackerItems[id].pipeline || 'sourcing') === p).length;
  const sortBtn = (key, label) =>
    `<button class="trk-sort${s.key === key ? ' active' : ''}" onclick="sortPipe('${p}','${key}')">${label}${s.key === key ? (s.asc ? ' ▲' : ' ▼') : ''}</button>`;
  return `
    <div class="pipe-head">
      <div>
        <div class="pipe-title">${cfg.title} <span class="pipe-count">${total}</span></div>
        <div class="pipe-sub">${cfg.sub}</div>
      </div>
      <button class="tracker-add-btn" onclick="addTrackerItem('${p}')">+ 품목 추가</button>
    </div>
    <div class="pipe-controls">
      <span class="pipe-ctrl-label">정렬</span>
      ${sortBtn('name', '상품명')}
      ${sortBtn('initialPrice', '판매가')}
      ${sortBtn('expectedRevenue', '기대매출')}
      <input class="pipe-search" type="search" placeholder="🔍 상품명 검색" value="${_trkAttr(pipeSearch[p])}" oninput="onPipeSearch('${p}', this.value)" />
    </div>
    <div class="pipe-list" id="pipe-list-${p}"></div>`;
}

function renderTracker() {
  renderPipeToggles();
  const el = document.getElementById('tracker-list');
  if (!el) return;
  const active = PIPELINE_ORDER.filter(p => pipeActive[p]);
  if (!active.length) {
    el.innerHTML = '<div class="trk-empty">표시할 파이프라인을 선택하세요. (위 토글에서 활성화)</div>';
    return;
  }
  el.innerHTML = active.map(p => `<div class="pipe-block" id="pipe-block-${p}">${pipeHeader(p)}</div>`).join('');
  active.forEach(renderPipeList);
}

// 초기 로드 — 메인 진입 시 '쿠팡 품목 진행상황'을 기본 페이지로 (app.js는 body 끝 로드라 DOM 준비됨)
switchPage('tracker');

// ===== AI 개선 상담 + 결재함 =====
let SITE_ROLE = '';
const _siteChat = [];
function loadMe(){
  fetch('/api/me').then(r=>r.json()).then(d=>{
    SITE_ROLE = d.role || '';
    const who = document.getElementById('site-who');
    const _rn = {boss:'👑 사장님', staff:'👤 경리', design:'👤 디자이너', sourcing:'👤 소싱직원', cs:'👤 CS직원'};
    if (who) who.textContent = _rn[SITE_ROLE] || '';
    if (SITE_ROLE === 'boss') {
      const card = document.getElementById('site-approvals-card');
      if (card) card.style.display = 'block';
      loadSiteApprovals();
    }
  }).catch(()=>{});
}
function _siteBubble(role, text){
  const log = document.getElementById('site-chat-log');
  const mine = role === 'user';
  const div = document.createElement('div');
  div.style.cssText = 'margin:8px 0;display:flex;'+(mine?'justify-content:flex-end':'justify-content:flex-start');
  div.innerHTML = `<div style="max-width:78%;white-space:pre-wrap;line-height:1.5;font-size:14px;padding:10px 13px;border-radius:12px;${mine?'background:#D70010;color:#fff':'background:#fff;border:1px solid #e5e7eb;color:#1f2937'}">${(text||'').replace(/</g,'&lt;')}</div>`;
  log.appendChild(div); log.scrollTop = log.scrollHeight;
  return div;
}
function siteChatSend(){
  const inp = document.getElementById('site-chat-in');
  const btn = document.getElementById('site-chat-btn');
  const msg = (inp.value||'').trim();
  if (!msg) return;
  inp.value=''; btn.disabled=true;
  _siteChat.push({role:'user', content:msg}); _siteBubble('user', msg);
  const wait = _siteBubble('assistant', '…');
  fetch('/api/site_chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:_siteChat})})
    .then(r=>r.json()).then(d=>{
      if (d.error){ wait.querySelector('div').textContent='⚠️ '+d.error; btn.disabled=false; return; }
      wait.querySelector('div').textContent = d.reply || '(응답 없음)';
      _siteChat.push({role:'assistant', content:d.reply||''});
      if (d.approval){
        const b=_siteBubble('assistant', `📋 대표 결재요청 등록됨 #${d.approval.id}\n"${d.approval.title}" — ${d.approval.desc}`);
        b.querySelector('div').style.background='#fff7ed'; b.querySelector('div').style.borderColor='#fdba74';
        if (SITE_ROLE==='boss') loadSiteApprovals();
      }
      btn.disabled=false;
    }).catch(()=>{ wait.querySelector('div').textContent='⚠️ 연결 실패'; btn.disabled=false; });
}
function loadSiteApprovals(){
  fetch('/api/site_approvals').then(r=>r.json()).then(d=>{
    const box=document.getElementById('site-approvals-list');
    if (!box) return;
    const items=(d.items||[]).slice().reverse();
    if (!items.length){ box.innerHTML='<div style="color:#9ca3af">대기 중인 결재 요청이 없어요.</div>'; return; }
    box.innerHTML=items.map(a=>{
      const st=a.status||'대기';
      const color=st==='승인'?'#16a34a':(st==='반려'?'#b91c1c':'#d97706');
      const acts=st==='대기'?`<button onclick="siteApprovalAct(${a.id},'approve')" style="background:#16a34a;color:#fff;border:none;border-radius:7px;padding:6px 12px;cursor:pointer;font-size:13px;font-weight:600">승인</button> <button onclick="siteApprovalAct(${a.id},'reject')" style="background:#fee2e2;color:#b91c1c;border:none;border-radius:7px;padding:6px 12px;cursor:pointer;font-size:13px;font-weight:600">반려</button>`:`<span style="color:${color};font-weight:700">${st}</span>`;
      return `<div style="display:flex;align-items:center;gap:12px;padding:10px;border:1px solid #eee;border-radius:10px;margin-bottom:8px"><div style="flex:1;min-width:0"><div style="font-weight:600;font-size:14px">${(a.title||'').replace(/</g,'&lt;')}</div><div style="font-size:12px;color:#9ca3af">${(a.desc||'').replace(/</g,'&lt;')} · ${a.who||''}</div></div>${acts}</div>`;
    }).join('');
  }).catch(()=>{});
}
function siteApprovalAct(id, action){
  fetch('/api/site_approvals/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})})
    .then(r=>r.json()).then(()=>loadSiteApprovals()).catch(()=>{});
}
function siteLogout(){
  fetch('/jageum/api/logout',{method:'POST'}).then(()=>{location.href='/';}).catch(()=>{location.href='/';});
}
loadMe();

// ═══════════ 💰 거래처 단가 / 마진 v3 (20컬럼·자동계산) ═══════════
let DANGA = [];
// n=숫자우측정렬, calc=자동계산(읽기전용), pct=퍼센트, label=표시명(키와다를때)
const DG_COLS = [
  {k:'거래처',w:92},{k:'카테고리',w:88},{k:'제품명',w:160},{k:'옵션',w:88},{k:'모델명',w:96},{k:'사이즈',w:76},
  {k:'협정가',w:92,n:1},{k:'부가세',w:62},{k:'업체할인율',w:74,n:1},
  {k:'매입가',w:96,n:1,calc:1},{k:'매입배송비',w:80,n:1},
  {k:'N판매가',w:86,n:1},{k:'N고객배송비',w:88,n:1,label:'고객부담배송비'},{k:'N마진',w:88,n:1,calc:1},{k:'N마진율',w:72,calc:1,pct:1},
  {k:'C판매가',w:86,n:1},{k:'C고객배송비',w:88,n:1,label:'고객부담배송비'},{k:'C마진',w:88,n:1,calc:1},{k:'C마진율',w:72,calc:1,pct:1},
  {k:'재고',w:54,n:1},
];
function _dgN(v){ const n=parseFloat(String(v==null?'':v).replace(/[^0-9.\-]/g,'')); return isFinite(n)?n:0; }
function _dgFmt(v){ if(v===''||v==null) return ''; if(typeof v==='number') return v.toLocaleString('ko-KR'); const s=String(v); if(/%/.test(s)) return s; const n=parseFloat(s.replace(/,/g,'')); return isNaN(n)?s:n.toLocaleString('ko-KR'); }
function _dgEsc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }
function dgMaeipga(r){ const hy=_dgN(r['협정가']); if(!hy) return ''; const dc=_dgN(r['업체할인율']); const vat=String(r['부가세']||'').indexOf('별도')>=0?1.1:1; return Math.round(hy*vat*(1-dc/100)); }
function dgMargin(r,pk,ck){ const cost=dgMaeipga(r); if(cost==='') return ''; const sp=_dgN(r[pk]); if(!sp) return ''; return Math.round((sp+_dgN(r[ck]))*0.94-cost-_dgN(r['매입배송비'])); }
function dgCalc(r,k){
  if(k==='매입가') return dgMaeipga(r);
  if(k==='N마진') return dgMargin(r,'N판매가','N고객배송비');
  if(k==='C마진') return dgMargin(r,'C판매가','C고객배송비');
  if(k==='N마진율'){ const sp=_dgN(r['N판매가']),m=dgMargin(r,'N판매가','N고객배송비'); return (!sp||m==='')?'':(m/sp*100).toFixed(2)+'%'; }
  if(k==='C마진율'){ const sp=_dgN(r['C판매가']),m=dgMargin(r,'C판매가','C고객배송비'); return (!sp||m==='')?'':(m/sp*100).toFixed(2)+'%'; }
  return '';
}
function loadDanga(){ fetch('/api/danga').then(r=>r.json()).then(rows=>{ DANGA=Array.isArray(rows)?rows:[]; dgBuildSupplier(); renderDanga(); }).catch(()=>{DANGA=[];renderDanga();}); }
function dgBuildSupplier(){
  const sel=document.getElementById('danga-supplier'); if(!sel) return;
  const sup=[...new Set(DANGA.map(r=>r['거래처']).filter(Boolean))].sort(); const cur=sel.value;
  sel.innerHTML='<option value="">전체 거래처 ('+DANGA.length+')</option>'+sup.map(s=>`<option value="${_dgEsc(s)}">${_dgEsc(s)} (${DANGA.filter(r=>r['거래처']===s).length})</option>`).join('');
  if(cur) sel.value=cur;
}
let dgCollapsed={};
function dgToggle(s){ dgCollapsed[s]=(dgCollapsed[s]===false); renderDanga(); }
function dgExpandAll(v){ [...new Set(DANGA.map(r=>r['거래처']||'(미분류)'))].forEach(s=>{dgCollapsed[s]=!v;}); renderDanga(); }
function renderDanga(){
  const th=document.getElementById('danga-thead'), tb=document.getElementById('danga-tbody'); if(!tb) return;
  const NC=DG_COLS.length+1;
  th.innerHTML='<tr style="text-align:left">'+DG_COLS.map(c=>`<th style="padding:7px 6px;min-width:${c.w}px;${(c.n||c.pct)?'text-align:right':''};${c.calc?'background:#ecfdf5':''}">${c.label||c.k}${c.calc?' <span style="color:#059669;font-size:10px">자동</span>':''}</th>`).join('')+'<th></th></tr>';
  const sup=(document.getElementById('danga-supplier').value||''), q=(document.getElementById('danga-search').value||'').trim().toLowerCase();
  const groups={};
  DANGA.forEach((r,i)=>{
    if(sup&&r['거래처']!==sup) return;
    const hay=((r['제품명']||'')+' '+(r['카테고리']||'')+' '+(r['사이즈']||'')+' '+(r['옵션']||'')+' '+(r['모델명']||'')+' '+(r['거래처']||'')).toLowerCase();
    if(q&&!hay.includes(q)) return;
    const g=r['거래처']||'(미분류)'; (groups[g]=groups[g]||[]).push({r,i});
  });
  tb.innerHTML=''; let shown=0;
  Object.keys(groups).sort().forEach(gs=>{
    const items=groups[gs];
    const collapsed=q?false:(sup===gs?false:(dgCollapsed[gs]!==false));
    const sj=_dgEsc(gs).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    const hdr=document.createElement('tr');
    hdr.innerHTML=`<td colspan="${NC}" onclick="dgToggle('${sj}')" style="background:#eef2ff;padding:8px 12px;cursor:pointer;font-weight:700;border-top:2px solid #c7d2fe">${collapsed?'▶':'▼'} ${_dgEsc(gs)} <span style="color:#6b7280;font-weight:400;font-size:12px">(${items.length}개)</span></td>`;
    tb.appendChild(hdr);
    if(collapsed) return;
    items.forEach(({r,i})=>{
      shown++;
      const tr=document.createElement('tr'); tr.style.borderTop='1px solid #f0f0f0';
      const flags=r._flag||[];
      tr.innerHTML=DG_COLS.map(c=>{
        if(c.calc){
          const v=dgCalc(r,c.k); const neg=(typeof v==='number'&&v<0)||(c.pct&&parseFloat(v)<0);
          return `<td style="padding:3px 5px;text-align:right;background:#f7fdfb"><span id="dgc-${c.k}-${i}" style="color:${neg?'#dc2626':'#059669'};font-weight:600">${c.pct?(v||''):_dgFmt(v)}</span></td>`;
        }
        const disp=c.n?_dgFmt(r[c.k]):_dgEsc(r[c.k]); const y=flags.includes(c.k);
        return `<td style="padding:1px;${y?'background:#fde68a':''}"><input value="${disp}" oninput="dangaEdit(${i},'${c.k}',this.value)" style="width:100%;min-width:${c.w-8}px;border:0;background:transparent;padding:5px 4px;font-size:13px;${c.n?'text-align:right':''}"/></td>`;
      }).join('')+`<td style="padding:1px;text-align:center"><button onclick="dangaDel(${i})" style="border:0;background:none;color:#dc2626;cursor:pointer">✕</button></td>`;
      tb.appendChild(tr);
    });
  });
  const unc=DANGA.filter(r=>(r._flag||[]).length&&(!sup||r['거래처']===sup)).length;
  const s=document.getElementById('danga-summary');
  if(s) s.innerHTML=`거래처 <b>${Object.keys(groups).length}</b>곳 · 총 <b>${DANGA.length}</b>개`+((q||sup)?` · 표시 ${shown}개`:'')+(unc?` · <span style="background:#fde68a;padding:1px 5px;border-radius:4px">⚠️ 확인필요 ${unc}개</span>`:'');
}
function dangaEdit(i,k,v){
  if(!DANGA[i]) return; DANGA[i][k]=v;
  DG_COLS.forEach(c=>{ if(c.calc){ const el=document.getElementById(`dgc-${c.k}-${i}`); if(el){ const val=dgCalc(DANGA[i],c.k); el.textContent=c.pct?(val||''):_dgFmt(val); const neg=(typeof val==='number'&&val<0)||(c.pct&&parseFloat(val)<0); el.style.color=neg?'#dc2626':'#059669'; } } });
}
function dangaAddRow(){ const o={}; DG_COLS.forEach(c=>{ if(!c.calc) o[c.k]=''; }); const sup=document.getElementById('danga-supplier').value; if(sup)o['거래처']=sup; DANGA.unshift(o); renderDanga(); }
function dangaDel(i){ if(confirm('이 행 삭제할까요?')){ DANGA.splice(i,1); renderDanga(); } }
function dangaDelSupplier(){
  const sup=document.getElementById('danga-supplier').value; if(!sup){ alert('먼저 거래처를 선택하세요.'); return; }
  const cnt=DANGA.filter(r=>r['거래처']===sup).length;
  if(confirm(`'${sup}' 거래처의 ${cnt}개 행을 전부 삭제할까요? (💾저장해야 반영)`)){ DANGA=DANGA.filter(r=>r['거래처']!==sup); document.getElementById('danga-supplier').value=''; dgBuildSupplier(); renderDanga(); }
}
function dangaSave(){
  const s=document.getElementById('danga-status'); s.textContent='저장 중…';
  fetch('/api/danga',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(DANGA)}).then(r=>r.json()).then(d=>{ s.textContent=d.ok?('✅ 저장됨 ('+d.count+')'):'❌ 실패'; dgBuildSupplier(); setTimeout(()=>{s.textContent='';},3000); }).catch(()=>{s.textContent='❌ 오류';});
}
function dangaImport(){
  const inputCols=DG_COLS.filter(c=>!c.calc);
  const txt=prompt('엑셀/CSV 붙여넣기 (탭 또는 콤마 구분)\n입력 열 순서(자동계산 제외): '+inputCols.map(c=>c.label||c.k).join(' · ')+'\n(첫 줄 헤더면 자동 무시 · 기존에 추가됨)');
  if(!txt) return;
  const lines=txt.replace(/\r/g,'').split('\n'), rows=[];
  lines.forEach((ln,idx)=>{ if(!ln.trim())return; const c=(ln.split('\t').length>1)?ln.split('\t'):ln.split(','); if(idx===0&&/거래처|제품|협정가|매입/.test(ln))return; if(!(c[0]||'').trim()&&!(c[2]||'').trim())return; const o={}; inputCols.forEach((col,j)=>o[col.k]=(c[j]||'').trim()); rows.push(o); });
  if(rows.length){ DANGA=rows.concat(DANGA); dgBuildSupplier(); renderDanga(); alert(rows.length+'개 불러왔어요. 확인 후 💾저장 누르세요.'); } else alert('불러올 행이 없어요.');
}
function _dg2(n){ return String(n).padStart(2,'0'); }
function dangaExport(){
  const head=DG_COLS.map(c=>c.label||c.k).join(',');
  const esc=s=>'"'+String(s==null?'':s).replace(/"/g,'""')+'"';
  const lines=DANGA.map(r=>DG_COLS.map(c=>esc(c.calc?dgCalc(r,c.k):r[c.k])).join(','));
  const csv='﻿'+head+'\n'+lines.join('\n');
  const d=new Date();
  const fn='거래처단가'+String(d.getFullYear()).slice(2)+_dg2(d.getMonth()+1)+_dg2(d.getDate())+_dg2(d.getHours())+_dg2(d.getMinutes())+'.csv';
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download=fn; a.click();
}
