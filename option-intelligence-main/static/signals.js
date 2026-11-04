const $ = id => document.getElementById(id);
let rows = [], cfg = {target: 'T1', cost_points: 4}, livePrice = {};

function filters() {
  return new URLSearchParams({start: $('startDate').value, end: $('endDate').value, symbol: $('symbol').value, limit: '10000'});
}
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
const num = v => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
const fmt = (v, d = 2) => num(v) === null ? '' : Number(v).toLocaleString('en-IN', {minimumFractionDigits: d, maximumFractionDigits: d});
const signed = (v, d = 2) => num(v) === null ? '—' : (Number(v) > 0 ? '+' : '') + fmt(v, d);
const tone = v => num(v) === null ? '' : Number(v) > 0 ? 'color:var(--green)' : Number(v) < 0 ? 'color:var(--red)' : '';
const timeOnly = iso => iso ? String(iso).replace('T', ' ').replace(/\+05:30$/, '') : '';
const qty = () => Math.max(0, Number($('qty').value) || 0);
const delta = () => Math.min(1, Math.max(0.1, Number($('delta').value) || 0.5));
const rupees = pts => num(pts) === null || !qty() ? null : Number(pts) * delta() * qty();
// v1.19: real money result when the row has an option (premium x qty - charges); older rows fall back to the points estimate.
function rowRupees(r, livePts) {
  if (num(r.net_pnl_rs) !== null) return {v: Number(r.net_pnl_rs), est: false};
  if (livePts !== undefined && livePts !== null && num(r.option_entry) !== null && num(r.qty) !== null && num(r.option_delta) !== null)
    return {v: livePts * Number(r.option_delta) * Number(r.qty) - (num(r.charges_rs) || 0), est: true};
  const x = rupees(livePts !== undefined ? livePts : r.net_pnl_points);
  return x === null ? {v: null, est: true} : {v: x, est: true};
}
const rsf = (v, d = 0) => num(v) === null ? '—' : (Number(v) > 0 ? '+' : Number(v) < 0 ? '−' : '') + '₹' + Math.abs(Number(v)).toLocaleString('en-IN', {minimumFractionDigits: d, maximumFractionDigits: d});
const sub = t => t ? ` <span class="sig-sub">${t}</span>` : '';

// Mark-to-market for trades that are still open (uses the latest index price the server has).
function openPnl(r) {
  const px = livePrice[r.symbol], entry = num(r.entry) ?? num(r.last_close);
  if (px == null || entry == null) return null;
  return (px - entry) * (String(r.side).toUpperCase() === 'CE' ? 1 : -1);
}

function render() {
  const closed = rows.filter(r => r.trade_status && r.trade_status !== 'OPEN' && r.trade_status !== 'EXPIRED');
  const open = rows.filter(r => (r.trade_status || 'OPEN') === 'OPEN');
  const count = s => closed.filter(r => r.trade_status === s).length;
  const net = closed.reduce((t, r) => t + (num(r.net_pnl_points) ?? 0), 0);
  const wins = closed.filter(r => num(r.net_pnl_points) > 0).length;
  const openSum = open.map(openPnl).filter(v => v !== null);
  const sides = rows.map(r => String(r.side || '').toUpperCase());

  $('statRows').textContent = rows.length;
  $('statSides').textContent = `${sides.filter(x => x === 'CE').length} / ${sides.filter(x => x === 'PE').length}`;
  $('statWait').textContent = `${open.length} open / ${closed.length} closed`;
  $('statExits').textContent = `${count('TARGET')} / ${count('STOP')} / ${count('SQUAREOFF')}`;
  $('statWin').textContent = closed.length ? `${(wins / closed.length * 100).toFixed(0)}% (${wins}/${closed.length})` : '—';
  $('statNet').textContent = closed.length ? signed(net) : '—'; $('statNet').style.cssText = tone(closed.length ? net : null);
  const o = openSum.length ? openSum.reduce((a, b) => a + b, 0) : null;
  $('statOpen').textContent = o === null ? '—' : signed(o); $('statOpen').style.cssText = tone(o);
  const rsAll = closed.map(r => rowRupees(r)).filter(x => x.v !== null);
  const rs = rsAll.length ? rsAll.reduce((t, x) => t + x.v, 0) : null, rsEst = rsAll.some(x => x.est);
  $('statRupee').textContent = rs === null ? '—' : rsf(rs); $('statRupee').style.cssText = tone(rs);
  $('statRupeeSub').textContent = rs === null ? 'needs option rows, or a quantity above' : rsEst ? 'includes estimates (older rows: points × delta × quantity)' : 'premium × quantity − charges';
  $('cfgLine').textContent = `Target: ${cfg.target || 'T2'} or just before the next support / resistance · cost allowance ${cfg.cost_points} index pts per trade · time exit ${cfg.squareoff || '15:20'} IST · exits from the 1-minute price path.`;

  $('rows').innerHTML = rows.length ? rows.map(r => {
    const st = r.trade_status || 'OPEN';
    const stColor = st === 'TARGET' ? 'color:var(--green)' : st === 'STOP' ? 'color:var(--red)' : '';
    const live = st === 'OPEN' ? openPnl(r) : null;
    const netv = st === 'OPEN' && live !== null ? live - cfg.cost_points : r.net_pnl_points;
    const money = st === 'OPEN' ? (live === null ? {v: null} : rowRupees(r, live)) : rowRupees(r);
    const liveTag = st === 'OPEN' && live !== null ? ' (live)' : '';
    const name = r.option_name || `${r.symbol} ${r.side === 'CE' ? 'CE' : 'PE'}`;
    const when = timeOnly(r.generated_at).slice(0, 16);
    const opt = num(r.option_entry) !== null;
    return `<tr>` +
      `<td><span class="sig-opt">BUY ${esc(name)}</span>${sub(esc(when) + ' · ' + esc(r.interval) + 'm ' + esc(r.mode))}</td>` +
      `<td>${opt ? fmt(r.option_entry) : '—'}${sub(opt ? 'premium' : 'no option recorded')}</td>` +
      `<td>${opt ? fmt(r.option_sl) : fmt(r.stop_loss)}${sub(opt ? 'index ' + fmt(r.stop_loss) : 'index')}</td>` +
      `<td>${opt ? fmt(r.option_target) : fmt(r.target)}${sub((opt ? 'index ' + fmt(r.target) : 'index') + (r.target_basis ? ' · ' + esc(r.target_basis) : ''))}</td>` +
      `<td style="${stColor}"><b>${esc(st)}</b>${sub(esc(timeOnly(r.exit_time).slice(11, 19)))}</td>` +
      `<td>${num(r.option_exit) !== null ? fmt(r.option_exit) : num(r.exit_price) !== null ? fmt(r.exit_price) : '—'}${sub(num(r.option_exit) !== null ? 'index ' + fmt(r.exit_price) + (r.option_exit_source ? ' · ' + esc(r.option_exit_source) : '') : num(r.exit_price) !== null ? 'index' : '')}</td>` +
      `<td style="${tone(money.v)}"><b>${money.v === null ? '—' : rsf(money.v)}</b>${sub(money.v === null ? '' : (money.est ? 'estimate' : 'after charges') + liveTag)}</td>` +
      `<td style="${tone(netv)}">${num(netv) === null ? '—' : signed(netv)}${sub(st === 'OPEN' ? liveTag.trim() : 'gross ' + signed(r.gross_pnl_points))}</td>` +
      `<td>${fmt(r.entry ?? r.last_close)}</td>` +
      `<td>${num(r.rr) === null ? '—' : fmt(r.rr, 2)}${sub(num(r.atr_at_entry) === null ? '' : 'ATR ' + fmt(r.atr_at_entry, 1) + ' · SL ' + fmt(r.sl_atr_mult, 2) + '× · T ' + fmt(r.target_atr_mult, 2) + '×')}</td>` +
      `<td class="wrap">${esc([r.score ? 'score ' + r.score : '', r.regime, r.pattern, r.exit_reason, r.reason].filter(Boolean).join(' · '))}</td></tr>`;
  }).join('') : '<tr><td colspan="11" class="empty">No BUY signals for these filters yet. They are recorded automatically during market hours.</td></tr>';
}

async function loadPrices() {
  for (const sym of [...new Set(rows.filter(r => (r.trade_status || 'OPEN') === 'OPEN').map(r => r.symbol))]) {
    try {
      const r = await fetch('/api/index-ltp?symbol=' + encodeURIComponent(sym), {credentials: 'same-origin'});
      if (r.ok) { const q = await r.json(); if (num(q.ltp) !== null) livePrice[sym] = Number(q.ltp); }
    } catch {}
  }
}

async function load() {
  try {
    const r = await fetch('/api/signals?' + filters(), {credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    rows = (await r.json()).rows || [];
    $('count').textContent = `${rows.length} records`;
    render(); loadSummary();
    loadPrices().then(render);
  } catch (e) {
    $('rows').innerHTML = `<tr><td colspan="11" class="empty">Could not load log: ${esc(e.message)}</td></tr>`;
  }
}

async function loadConfig() {
  try { const r = await fetch('/api/signals/config', {credentials: 'same-origin'}); if (r.ok) cfg = await r.json(); } catch {}
  $('slAtr').value = cfg.sl_atr ?? ''; $('tgtAtr').value = cfg.target_atr ?? ''; $('costPts').value = cfg.cost_points ?? '';
  $('atrLine').textContent = cfg.custom
    ? `Custom plan active: stop ${cfg.sl_atr}× ATR, target ${cfg.target_atr}× ATR.`
    : `Default plan active: stop ${cfg.default_sl_atr}× ATR (minimum ${cfg.min_sl_points} pts), target ${cfg.target} (${cfg.default_target_atr}× ATR for T2).`;
  const o = cfg.option || {};
  if ($('optSteps')) {
    $('optSteps').value = String(o.steps ?? 2); $('optMoney').value = o.moneyness || 'ITM'; $('optLots').value = o.lots ?? 1;
    $('optCharges').value = o.charges_rs ?? 60; $('optRr').value = o.min_rr ?? 1.5; $('optExit').value = cfg.exit_plan || 'balanced';
    $('optLine').textContent = `On WATCH / BUY the signal uses the option ${o.steps ?? 2} strike(s) ${o.moneyness === 'OTM' ? 'out of the money' : 'in the money'} from ATM on the signal side · ${o.lots ?? 1} lot(s) · charges ₹${o.charges_rs ?? 60} per trade · ${cfg.exit_plan === 'winrate' ? 'exit plan: HIGH WIN-RATE (small target, wider stop).' : `a BUY needs reward : risk ≥ ${o.min_rr ?? 1.5}.`}`;
  }
}

// ---- results by day / month
let group = 'day', summary = null;
async function loadSummary() {
  try {
    const p = filters(); p.set('group', group); p.delete('limit');
    const r = await fetch('/api/signals/summary?' + p, {credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    summary = await r.json(); renderSummary();
  } catch (e) { $('sumRows').innerHTML = `<tr><td colspan="11" class="empty">Could not load results: ${esc(e.message)}</td></tr>`; }
}
function renderSummary() {
  if (!summary) return;
  const t = summary.total, rp = v => { const x = rupees(v); return x === null ? '' : rsf(x); };
  $('sumPeriodHead').textContent = group === 'month' ? 'Month' : 'Day';
  $('grpDay').className = group === 'day' ? '' : 'secondary'; $('grpMonth').className = group === 'month' ? '' : 'secondary';
  const card = (l, v, s = '') => `<div class="stat panel"><div class="label">${l}</div><div class="value" style="${s}">${v}</div></div>`;
  $('sumStats').innerHTML = card('Net P/L (index pts)', t.closed ? signed(t.net_points) : '—', tone(t.closed ? t.net_points : null)) +
    card('Win rate', t.win_rate === null ? '—' : t.win_rate + '%') + card('Profit factor', t.profit_factor ?? '—') +
    card('Expectancy / trade', t.expectancy === null ? '—' : signed(t.expectancy), tone(t.expectancy)) + card('Max drawdown (pts)', t.closed ? fmt(t.max_drawdown) : '—') +
    card('Best / worst trade', t.closed ? signed(t.best) + ' / ' + signed(t.worst) : '—') +
    card('Net P/L (₹)', t.net_rs == null ? (rupees(t.net_points) === null || !t.closed ? '—' : rsf(rupees(t.net_points)) + ' est.') : rsf(t.net_rs), tone(t.net_rs ?? (t.closed ? t.net_points : null)));
  $('sumRows').innerHTML = summary.periods.length ? summary.periods.map(p => {
    const money = p.net_rs != null ? rsf(p.net_rs) + (p.rs_trades < p.closed ? ` <span class="sig-sub">${p.rs_trades} of ${p.closed} trades</span>` : '') : (p.closed && rp(p.net_points) ? rp(p.net_points) + ' <span class="sig-sub">estimate</span>' : '—');
    return `<tr><td>${esc(p.period)}</td><td>${p.signals}</td><td>${p.closed}${p.open ? ` <span class="sig-sub">+${p.open} open</span>` : ''}</td><td>${p.win_rate === null ? '—' : p.win_rate + '%'}</td>` +
      `<td>${p.target_exits} / ${p.stop_exits} / ${p.time_exits}</td><td style="${tone(p.net_rs ?? p.net_points)}"><b>${money}</b></td>` +
      `<td style="${tone(p.net_points)}"><b>${p.closed ? signed(p.net_points) : '—'}</b></td><td>${p.closed ? signed(p.gross_points) + ' / ' + fmt(p.cost_points) : '—'}</td>` +
      `<td>${p.profit_factor ?? '—'}</td><td>${p.avg_win === null ? '—' : signed(p.avg_win)} / ${p.avg_loss === null ? '—' : signed(p.avg_loss)}</td><td>${p.closed ? fmt(p.max_drawdown) : '—'}</td></tr>`;
  }).join('')
    : '<tr><td colspan="11" class="empty">No trades in this range yet.</td></tr>';
}
async function saveAtr(reset) {
  const msg = $('atrMsg'); msg.textContent = 'Saving…';
  const optBody = () => ({opt_steps: Number($('optSteps').value), opt_moneyness: $('optMoney').value, opt_lots: Number($('optLots').value) || 1,
                          opt_charges_rs: Number($('optCharges').value) || 0, opt_min_rr: Number($('optRr').value) || 1.5, opt_exit_plan: $('optExit').value});
  const keepAtr = cfg.custom ? {sl_atr: cfg.sl_atr, target_atr: cfg.target_atr} : {};
  const keepCost = $('costPts').value === '' ? {} : {cost_points: Number($('costPts').value)};
  const body = reset === 'opt' ? {...optBody(), ...keepAtr, ...keepCost} : reset ? optBody() : {...optBody(), sl_atr: $('slAtr').value === '' ? null : Number($('slAtr').value), target_atr: $('tgtAtr').value === '' ? null : Number($('tgtAtr').value),
                             cost_points: $('costPts').value === '' ? null : Number($('costPts').value)};
  try {
    const r = await fetch('/api/signals/config', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch {} throw Error(d || r.status); }
    cfg = await r.json(); msg.textContent = 'Saved - applies to new signals.'; await loadConfig(); load();
  } catch (e) { msg.textContent = 'Not saved: ' + (e.message || e); }
}

async function settleNow() {
  const b = $('settleNow'); b.disabled = true;
  try {
    const r = await fetch('/api/signals/settle', {method: 'POST', credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    const d = await r.json();
    $('count').textContent = d.closed ? `${d.closed} trade(s) closed` : 'No open trade hit an exit';
  } catch (e) { $('count').textContent = 'Exit check failed: ' + e.message; }
  finally { b.disabled = false; load(); }
}

function exportFile(type) { location.href = '/api/signals/export.' + type + '?' + filters(); }

try { $('qty').value = localStorage.getItem('oi-sig-qty') || ''; $('delta').value = localStorage.getItem('oi-sig-delta') || '0.5'; } catch {}
['qty', 'delta'].forEach(id => $(id).addEventListener('input', () => { try { localStorage.setItem('oi-sig-' + id, $(id).value); } catch {} render(); renderSummary(); }));
$('refreshLog').onclick = load;
$('grpDay').onclick = () => { group = 'day'; loadSummary(); };
$('grpMonth').onclick = () => { group = 'month'; loadSummary(); };
$('saveAtr').onclick = () => saveAtr(false);
$('resetAtr').onclick = () => saveAtr(true);
$('settleNow').onclick = settleNow;
if ($('saveOpt')) $('saveOpt').onclick = async () => { $('optMsg').textContent = 'Saving…'; await saveAtr('opt'); $('optMsg').textContent = $('atrMsg').textContent; };
$('exportXlsx').onclick = () => exportFile('xlsx');
$('exportCsv').onclick = () => exportFile('csv');
['startDate', 'endDate', 'symbol'].forEach(id => $(id).addEventListener('change', load));
loadConfig().then(load);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 10000);
