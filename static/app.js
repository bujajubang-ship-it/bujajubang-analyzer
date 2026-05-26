let allProducts = [];
let currentFilter = 'all';
let currentSort = { key: null, asc: false };

let allCandidates = [];
let currentChannel = 'all';
let currentSrcPage = 1;
let totalSrcPages = 1;
let activeFilters = { comp: false, price: false };

// ── Page switching ──────────────────────────────────────────────────
function switchPage(page) {
  const isSearch = page === 'search';
  document.getElementById('nav-search').classList.toggle('active', isSearch);
  document.getElementById('nav-sourcing').classList.toggle('active', !isSearch);
  document.getElementById('hero-section').classList.toggle('hidden', !isSearch || currentSection !== 'hero');
  document.getElementById('loading-section').classList.toggle('hidden', !isSearch || currentSection !== 'loading');
  document.getElementById('result-section').classList.toggle('hidden', !isSearch || currentSection !== 'result');
  document.getElementById('sourcing-page').classList.toggle('hidden', isSearch);
  document.getElementById('header-search').style.display = isSearch && currentSection !== 'hero' ? 'flex' : 'none';
  if (!isSearch) { syncMemosFromServer().then(() => loadSourcing(currentSrcPage)); }
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

function filterChannel(channel, btn) {
  currentChannel = channel;
  currentSrcPage = 1;
  document.querySelectorAll('.stab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadSourcing(1);
}

function toggleSourcingFilter(type, btn) {
  activeFilters[type] = !activeFilters[type];
  btn.classList.toggle('active', activeFilters[type]);
  applyAndRender();
}

function applyFilters(candidates) {
  return candidates.filter(c => {
    if (activeFilters.comp && c.competition.intensity >= 30) return false;
    if (activeFilters.price && !c.filters.price_ok) return false;
    return true;
  });
}

function applyAndRender() {
  const filtered = applyFilters(allCandidates);
  const countEl = document.getElementById('sfil-count');
  if (countEl) {
    const isFiltering = activeFilters.comp || activeFilters.price;
    countEl.textContent = isFiltering ? `${filtered.length}개 표시 중 / 전체 ${allCandidates.length}개` : '';
  }
  renderSourcingCards(filtered);
}

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

function renderSourcingCards(candidates) {
  if (!candidates.length) {
    document.getElementById('sourcing-grid').innerHTML =
      '<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--muted)">해당 채널의 추천 상품이 없습니다.</div>';
    return;
  }
  document.getElementById('sourcing-grid').innerHTML = candidates.map(c => srcCard(c)).join('');
  candidates.forEach(c => renderMemoOnCard(c.id));
  updateMemoBadge();
  _imgQueue.length = 0;
  document.querySelectorAll('.src-img-wrap[data-name]').forEach(el => _imgQueue.push(el));
  if (!_imgRunning) _processNextImage();
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
  const borderCls = c.filters.price_ok ? 'card-price-ok' : 'card-price-warn';
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
    { ok: c.filters.price_ok, text: c.filters.price_ok ? `✓ 판매가 적정` : `✗ 판매가 재검토 필요` },
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
          <div class="src-profit-rate ${c.margin.rate >= 30 ? 'good' : 'ok'}">${c.margin.rate}% 마진</div>
        </div>
      </div>

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

      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px">🔑 키워드 클릭 → cninsider 검색 &nbsp;<span style="background:#fef9c3;color:#92400e;font-weight:700;padding:2px 6px;border-radius:4px;font-size:11px">⭐ 实力商家 배지 확인!</span></div>
        <div class="src-keywords">
          ${c.keywords_1688.map(kw => `<span class="src-kw-chip" onclick="open1688('${esc(kw)}')" title="cninsider에서 검색">${esc(kw)} 🔗</span>`).join('')}
        </div>
      </div>

      <div class="src-fit-reason">${esc(c.fit_reason)}</div>

      <div class="src-suppliers" id="suppliers-${c.id}">
        <div class="src-suppliers-title">🏭 추천 판매처 (실력상가)
          <button class="src-load-btn" onclick="loadRealSuppliers(${c.id}, '${esc(c.keywords_1688 && c.keywords_1688[0] ? c.keywords_1688[0] : c.name)}')">1688 실시간 조회</button>
        </div>
        <div class="src-suppliers-body" id="suppliers-body-${c.id}">
          <span style="color:#aaa;font-size:12px">버튼을 눌러 실제 실력상가 상품을 가져오세요</span>
        </div>
      </div>

      <div class="src-memo-area"></div>
    </div>
  </div>`;
}

function open1688(kw) {
  const url = 'https://www.cninsider.co.kr/mall/#/product?keywords=' + encodeURIComponent(kw) + '&type=text&imageAddress=&searchDiff=1';
  window.open(url, '_blank', 'noopener');
}

async function loadRealSuppliers(cardId, keyword) {
  const body = document.getElementById('suppliers-body-' + cardId);
  if (!body) return;
  body.innerHTML = '<span style="color:#aaa;font-size:12px">⏳ 1688 실력상가 조회 중...</span>';

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
