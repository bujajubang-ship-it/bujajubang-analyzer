/* 📌 쿠팡 소싱 마스터 가이드 — 마진 / ROAS 인터랙티브 계산기
   - React 18 + htm (CDN, 빌드 없음). 상태는 전부 React useState로만 관리 (localStorage 미사용)
   - 입력 변경 시 실시간 계산, 신호등 색상 + 한 줄 코멘트, 색상 범례 포함 */
(function () {
  if (!window.React || !window.ReactDOM || !window.htm) {
    console.error('계산기: React/htm 로드 실패');
    return;
  }
  const { useState } = React;
  const html = htm.bind(React.createElement);

  const COLORS = { green: '#16a34a', yellow: '#eab308', red: '#dc2626' };
  const DOTS   = { green: '🟢', yellow: '🟡', red: '🔴' };

  const num = (v) => { const n = parseFloat(v); return isFinite(n) ? n : 0; };
  const won = (n) => isFinite(n) ? Math.round(n).toLocaleString('ko-KR') + '원' : '—';
  const mult = (n) => isFinite(n) ? n.toFixed(2) + '배' : '—';
  const pctOf = (ratio) => isFinite(ratio) ? (ratio * 100).toFixed(1) + '%' : '—';

  // 입력 필드
  function Field({ label, value, set, suffix }) {
    return html`
      <label class="calc-field">
        <span class="calc-field-label">${label}</span>
        <span class="calc-input-wrap">
          <input type="number" inputmode="decimal" value=${value}
                 onInput=${(e) => set(e.target.value)} />
          ${suffix ? html`<span class="calc-suffix">${suffix}</span>` : null}
        </span>
      </label>`;
  }

  // 결과 한 줄
  function Row({ label, value, strong }) {
    return html`<div class=${'calc-row' + (strong ? ' strong' : '')}>
      <span>${label}</span><span class="calc-row-val">${value}</span></div>`;
  }

  // 신호등 + 코멘트
  function Signal({ color, text }) {
    return html`<div class="calc-signal" style=${{ borderColor: COLORS[color], background: COLORS[color] + '14' }}>
      <span class="calc-signal-dot">${DOTS[color]}</span>
      <span class="calc-signal-text" style=${{ color: COLORS[color] }}>${text}</span>
    </div>`;
  }

  // ── 마진 계산기 ──
  function MarginCalc() {
    const [price, setPrice]       = useState('10000');
    const [custShip, setCustShip] = useState('0');
    const [cost, setCost]         = useState('3000');
    const [myShip, setMyShip]     = useState('0');
    const [packing, setPacking]   = useState('500');
    const [channel, setChannel]   = useState('coupang');
    const [feePct, setFeePct]     = useState('11.88');

    const P = num(price), CS = num(custShip), C = num(cost),
          MS = num(myShip), PK = num(packing), FR = num(feePct) / 100;

    const fee = (P + CS) * FR;
    const vat = P / 11 - C / 11 - PK / 11 - fee / 11;
    const margin = P + CS - C - MS - PK - fee - vat;
    const marginRate = P > 0 ? margin / P : NaN;          // 비율
    const minRoas = margin > 0 ? (P / margin) * 1.1 : Infinity; // 배수

    // 마진율 신호등
    let mSig;
    if (marginRate >= 0.5) mSig = { color: 'green', text: '사입 가능 수준 (사입 목표 50% 이상)' };
    else if (marginRate >= 0.2) mSig = { color: 'yellow', text: '위탁 OK, 사입은 신중히 (위탁 기준 20% 이상)' };
    else mSig = { color: 'red', text: '마진 너무 낮음. 더 싼 원가 찾거나 포기' };

    // 최소ROAS 신호등 (% 기준)
    const minRoasPct = minRoas * 100;
    let rSig;
    if (minRoasPct <= 350) rSig = { color: 'green', text: '추천값(350%) 이하라 광고 굴리기 좋음' };
    else if (minRoasPct <= 450) rSig = { color: 'yellow', text: '광고 부담 있음. 마진 개선 권장' };
    else rSig = { color: 'red', text: '광고 부담 큼. 가격·원가·마진 재점검 필요' };

    const onChannel = (e) => {
      const v = e.target.value;
      setChannel(v);
      setFeePct(v === 'coupang' ? '11.88' : v === 'naver' ? '5.74' : feePct);
    };

    return html`
      <div class="calc-card">
        <div class="calc-card-title">🧮 마진 계산기</div>
        <div class="calc-inputs">
          <${Field} label="판매가" value=${price} set=${setPrice} suffix="원" />
          <${Field} label="고객배송비" value=${custShip} set=${setCustShip} suffix="원" />
          <${Field} label="원가" value=${cost} set=${setCost} suffix="원" />
          <${Field} label="나의배송비" value=${myShip} set=${setMyShip} suffix="원" />
          <${Field} label="포장비" value=${packing} set=${setPacking} suffix="원" />
          <label class="calc-field">
            <span class="calc-field-label">판매처</span>
            <span class="calc-input-wrap">
              <select value=${channel} onChange=${onChannel}>
                <option value="coupang">쿠팡 (11.88%)</option>
                <option value="naver">네이버 (5.74%)</option>
                <option value="custom">직접입력</option>
              </select>
            </span>
          </label>
          <${Field} label="수수료율" value=${feePct} set=${(v) => { setFeePct(v); setChannel('custom'); }} suffix="%" />
        </div>

        <div class="calc-results">
          <${Row} label="수수료" value=${won(fee)} />
          <${Row} label="부가세" value=${won(vat)} />
          <${Row} label="마진" value=${won(margin)} strong=${true} />
          <${Row} label="마진율" value=${pctOf(marginRate)} strong=${true} />
          <${Signal} ...${mSig} />
          <${Row} label="최소 ROAS" value=${mult(minRoas) + ' (' + pctOf(minRoas) + ')'} strong=${true} />
          <${Signal} ...${rSig} />
        </div>
      </div>`;
  }

  // ── ROAS 계산기 ──
  function RoasCalc() {
    const [price, setPrice]     = useState('10000');
    const [qty, setQty]         = useState('10');
    const [adCost, setAdCost]   = useState('10000');
    const [margin, setMargin]   = useState('3103');

    const P = num(price), Q = num(qty), AD = num(adCost), M = num(margin);

    const revenue = P * Q;
    const roas = AD > 0 ? revenue / AD : Infinity;       // 배수
    const totalMargin = M * Q;
    const adVat = AD * 1.1;
    const profit = totalMargin - adVat;
    const minRoas = M > 0 ? (P / M) * 1.1 : Infinity;    // 배수

    const ok = profit > 0 && roas >= minRoas;
    const sig = ok
      ? { color: 'green', text: '흑자, 광고 정상' }
      : { color: 'red', text: '적자/최소ROAS 미달 — 광고할수록 손해' };

    return html`
      <div class="calc-card">
        <div class="calc-card-title">📈 ROAS 계산기</div>
        <div class="calc-inputs">
          <${Field} label="판매가" value=${price} set=${setPrice} suffix="원" />
          <${Field} label="판매량" value=${qty} set=${setQty} suffix="개" />
          <${Field} label="광고비" value=${adCost} set=${setAdCost} suffix="원" />
          <${Field} label="마진(개당)" value=${margin} set=${setMargin} suffix="원" />
        </div>

        <div class="calc-results">
          <${Row} label="매출액" value=${won(revenue)} />
          <${Row} label="광고수익률(ROAS)" value=${mult(roas) + ' (' + pctOf(roas) + ')'} strong=${true} />
          <${Row} label="총마진" value=${won(totalMargin)} />
          <${Row} label="광고비(VAT포함)" value=${won(adVat)} />
          <${Row} label="순이익" value=${won(profit)} strong=${true} />
          <${Row} label="최소 ROAS" value=${mult(minRoas) + ' (' + pctOf(minRoas) + ')'} />
          <${Signal} ...${sig} />
        </div>
      </div>`;
  }

  // 색상 범례 + 안내
  function Legend() {
    return html`
      <div class="calc-legend">
        <div class="calc-legend-row"><span>🟢</span> <b style=${{ color: COLORS.green }}>초록 = 좋음</b> — 들어가도 되는 장사 (마진율 50%↑ 또는 흑자)</div>
        <div class="calc-legend-row"><span>🟡</span> <b style=${{ color: COLORS.yellow }}>노랑 = 보통</b> — 위탁은 가능하나 사입/광고는 신중히</div>
        <div class="calc-legend-row"><span>🔴</span> <b style=${{ color: COLORS.red }}>빨강 = 위험</b> — 적자 또는 마진 부족. 원가 재협상하거나 포기</div>
        <div class="calc-note">※ 판매가는 시장이 정한다. 이 계산기는 가격을 추천하는 게 아니라, 입력한 시장가로 들어갈 만한 장사인지 판정한다. 시장가를 넣었을 때 초록이 나오는 원가의 제품을 소싱하는 게 목표다.</div>
      </div>`;
  }

  function Calculators() {
    return html`
      <div class="calc-zone-inner">
        <div class="calc-grid">
          <${MarginCalc} />
          <${RoasCalc} />
        </div>
        <${Legend} />
      </div>`;
  }

  const root = document.getElementById('calc-root');
  if (root) ReactDOM.createRoot(root).render(html`<${Calculators} />`);
})();
