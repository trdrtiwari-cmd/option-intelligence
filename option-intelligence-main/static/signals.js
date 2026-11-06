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
  $('statExits').textContent = `${count('TARGET')}${count('TRAIL') ? ' (+' + count('TRAIL') + ' trail)' : ''} / ${count('STOP')} / ${count('SQUAREOFF')}`;
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
    const stColor = st === 'TARGET' || (st === 'TRAIL' && Number(r.net_pnl_points) > 0) ? 'color:var(--green)' : st === 'STOP' || st === 'TRAIL' ? 'color:var(--red)' : '';
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
    render(); loadSummary(); loadBoard(); loadMonitor();
    loadPrices().then(render);
  } catch (e) {
    $('rows').innerHTML = `<tr><td colspan="11" class="empty">Could not load log: ${esc(e.message)}</td></tr>`;
  }
}

// ---- v1.19.5 signal board: every option signal with what the option did next
async function loadBoard() {
  try {
    const p = filters(); p.set('stage', $('boardStage').value); p.delete('limit');
    const r = await fetch('/api/signal-board?' + p, {credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    const d = await r.json();
    const pc = v => num(v) === null ? '—' : (v > 0 ? '+' : '') + fmt(v, 1) + '%';
    $('boardSum').innerHTML = d.summary.filter(s => s.count).map(s =>
      `<tr><td><b>${esc(s.stage)}</b></td><td>${s.count}</td><td>${s.with_outcome}</td><td>${s.pct_up20 === null ? '—' : s.pct_up20 + '%'}</td>` +
      `<td>${pc(s.avg_best_pct)} / ${pc(s.avg_worst_pct)}</td><td>${s.test_target} / ${s.test_stop} / ${s.test_open}</td>` +
      `<td>${s.test_win_rate === null ? '—' : s.test_win_rate + '%'}</td><td style="${tone(s.test_avg_points)}">${num(s.test_avg_points) === null ? '—' : signed(s.test_avg_points, 1)}</td></tr>`).join('')
      || '<tr><td colspan="8" class="empty">No option signals recorded for these filters yet.</td></tr>';
    const stColor = {BUY: 'var(--green)', 'NO BUY': 'var(--yellow)', FAILED: 'var(--red)', SETUP: 'var(--blue)'};
    $('boardRows').innerHTML = d.rows.length ? d.rows.map(r => {
      const done = !!r.outcome_at, prem = num(r.opt_entry) ?? num(r.option_ltp);
      const chg = v => num(v) === null || !prem ? '' : ` (${pc((v - prem) / prem * 100)})`;
      return `<tr><td><span class="sig-opt" style="color:${stColor[r.stage] || ''}">${esc(r.stage)} · ${esc(r.option_name || '')}</span>${sub(esc(timeOnly(r.created_at).slice(0, 16)) + ' · ' + esc(r.interval) + 'm ' + esc(r.mode) + (r.option_label ? ' · ' + esc(r.option_label) : ''))}</td>` +
        `<td>${prem === null ? '—' : fmt(prem)}</td><td>${fmt(r.index_price)}${sub(r.score ? 'score ' + esc(r.score) + ' · ' + esc(r.regime || '') : '')}</td>` +
        `<td>${num(r.opt_sl) === null ? '—' : fmt(r.opt_sl) + ' / ' + fmt(r.opt_target)}${sub(num(r.idx_sl) === null ? 'no plan (test uses 1.5 / 1.5 ATR)' : 'index ' + fmt(r.idx_sl) + ' / ' + fmt(r.idx_target))}</td>` +
        `<td>${done ? (num(r.opt_30) === null ? '—' : fmt(r.opt_30)) + ' / ' + (num(r.opt_60) === null ? '—' : fmt(r.opt_60) + chg(r.opt_60)) : '<span class="sub">after 60 min</span>'}</td>` +
        `<td>${done && num(r.best_pct_60) !== null ? `<span style="color:var(--green)">${pc(r.best_pct_60)}</span> / <span style="color:var(--red)">${pc(r.worst_pct_60)}</span>` : (done ? esc(r.outcome_note || '—') : '<span class="sub">pending</span>')}</td>` +
        `<td style="${tone(r.test_points)}">${r.test_result ? esc(r.test_result) + ' ' + signed(r.test_points, 1) : '—'}</td><td class="wrap">${esc(r.reason || '')}${r.blocked_by ? `<div class="sig-sub">Blocked by: ${esc(r.blocked_by)}</div>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="8" class="empty">No option signals recorded for these filters yet. They are added automatically during market hours.</td></tr>';
  } catch (e) { $('boardRows').innerHTML = `<tr><td colspan="8" class="empty">Could not load the signal board: ${esc(e.message)}</td></tr>`; }
}

async function loadConfig() {
  try { const r = await fetch('/api/signals/config', {credentials: 'same-origin'}); if (r.ok) cfg = await r.json(); } catch {}
  $('slAtr').value = cfg.sl_atr ?? ''; $('tgtAtr').value = cfg.target_atr ?? ''; $('costPts').value = cfg.cost_points ?? '';
  $('atrLine').textContent = cfg.custom
    ? `Custom plan active: stop ${cfg.sl_atr}× ATR, target ${cfg.target_atr}× ATR.`
    : `Default plan active: stop ${cfg.min_sl_atr ?? 1.5}× ATR (minimum ${cfg.min_sl_points} pts), target from the exit plan below.`;
  const o = cfg.option || {};
  if ($('optSteps')) {
    $('optSteps').value = String(o.steps ?? 2); $('optMoney').value = o.moneyness || 'ITM'; $('optLots').value = o.lots ?? 1;
    $('optCharges').value = o.charges_rs ?? 60; $('optRr').value = o.min_rr ?? 1.5; $('optExit').value = cfg.exit_plan || 'trail';
    if ($('optOnePer')) $('optOnePer').value = cfg.one_per_side || 'off';
    if ($('optDayTarget')) $('optDayTarget').value = String(cfg.daily_target_rs ?? 0);
    if ($('optTrendEntry')) $('optTrendEntry').value = cfg.trend_entry || 'off';
    $('optLine').textContent = `On WATCH / BUY the signal uses the option ${o.steps ?? 2} strike(s) ${o.moneyness === 'OTM' ? 'out of the money' : 'in the money'} from ATM on the signal side · ${o.lots ?? 1} lot(s) · charges ₹${o.charges_rs ?? 60} per trade · stop at least ${cfg.min_sl_atr ?? 1.5} ATR (${cfg.min_sl_points ?? 20} pts) · entries ${cfg.entry_windows || ''} IST${cfg.one_per_side === 'on' ? ' · only the first BUY per side each day' : ''}${cfg.daily_target_rs ? ` · stop for the day at ₹${Number(cfg.daily_target_rs).toLocaleString('en-IN')} profit` : ''}${cfg.trend_entry && cfg.trend_entry !== 'off' ? ' · strong-trend entries: ' + cfg.trend_entry : ''}.`;
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
                          opt_charges_rs: Number($('optCharges').value) || 0, opt_min_rr: Number($('optRr').value) || 1.5, opt_exit_plan: $('optExit').value,
                          ...($('optOnePer') ? {opt_one_per_side: $('optOnePer').value} : {}),
                          ...($('optDayTarget') ? {opt_daily_target_rs: Number($('optDayTarget').value)} : {}),
                          ...($('optTrendEntry') ? {opt_trend_entry: $('optTrendEntry').value} : {})});
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

function exportFile(type) { const u = '/api/signals/export.' + type + '?' + filters(); if (window.oiSaveUrl) window.oiSaveUrl(u, 'option_intelligence_signals.' + type).catch(() => {}); else location.href = u; }

try { $('qty').value = localStorage.getItem('oi-sig-qty') || ''; $('delta').value = localStorage.getItem('oi-sig-delta') || '0.5'; } catch {}
['qty', 'delta'].forEach(id => $(id).addEventListener('input', () => { try { localStorage.setItem('oi-sig-' + id, $(id).value); } catch {} render(); renderSummary(); }));
$('refreshLog').onclick = load;
$('grpDay').onclick = () => { group = 'day'; loadSummary(); };
$('grpMonth').onclick = () => { group = 'month'; loadSummary(); };
$('saveAtr').onclick = () => saveAtr(false);
$('resetAtr').onclick = () => saveAtr(true);
$('settleNow').onclick = settleNow;
$('boardStage').onchange = loadBoard;
$('boardCsv').onclick = () => { const p = filters(); p.set('stage', $('boardStage').value); p.delete('limit'); const u = '/api/signal-board/export.csv?' + p; if (window.oiSaveUrl) window.oiSaveUrl(u, 'option_intelligence_signal_board.csv').catch(() => {}); else location.href = u; };
if ($('saveOpt')) $('saveOpt').onclick = async () => { $('optMsg').textContent = 'Saving…'; await saveAtr('opt'); $('optMsg').textContent = $('atrMsg').textContent; };
$('exportXlsx').onclick = () => exportFile('xlsx');
$('exportCsv').onclick = () => exportFile('csv');
['startDate', 'endDate', 'symbol'].forEach(id => $(id).addEventListener('change', load));
loadConfig().then(load);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 10000);


// ---- v1.20.0 Claude strategy monitor
let monReviews = [];
const monColor = {high: 'var(--red)', medium: 'var(--yellow)', info: 'var(--blue)'};
function monDetail(e) {
  const d = e.detail || {};
  if (e.kind === 'LOSS' && Array.isArray(d.causes)) return d.causes.map(c => `<div>• <b>${esc(c.cause)}</b> - ${esc(c.why)}</div>`).join('');
  if (e.kind === 'MISSED' && Array.isArray(d.blocked_by)) return '<div>Blocked by: ' + d.blocked_by.map(esc).join(' · ') + '</div>';
  if (e.kind === 'MISSED' && d.reason) return '<div>' + esc(d.reason) + '</div>';
  if (e.kind === 'SUGGESTION') return `<div>${esc(d.evidence || '')}</div>` + (d.caution ? `<div class="sig-sub">Caution: ${esc(d.caution)}</div>` : '');
  return '';
}
function showReview(i) {
  const r = monReviews[i];
  if (!r) { $('monReviewText').textContent = 'No review yet. One runs every 30 minutes from 10:00 and 10 minutes after the close, or press "Run review now".'; return; }
  $('monReviewHead').textContent = `${r.kind === 'eod' ? 'End-of-day review' : r.kind === 'manual' ? 'Review (run manually)' : 'Intraday review'} · ${timeOnly(r.created_at).slice(0, 16)} · ${r.model ? esc(r.model) : 'code findings only'}`;
  $('monReviewText').textContent = r.text || '';
}
async function loadMonitor() {
  try {
    const p = filters(); p.delete('symbol'); p.delete('limit'); p.set('kind', $('monKind').value);
    const r = await fetch('/api/monitor?' + p, {credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    const d = await r.json(), st = d.status || {}, cfg = d.settings || {};
    $('monStatus').innerHTML = `${cfg.enabled ? '● Monitoring' : '○ Monitor off'} · ${st.ai ? esc(st.ai) + ' connected' : '<b>No AI connected</b>' + aiWhy(st, cfg)} · `
      + `${st.calls_today ?? 0} / ${cfg.max_calls} AI calls today · replay ${esc((st.combo || []).join(' '))}`
      + (st.last_review ? ` · last review ${esc(timeOnly(st.last_review).slice(11, 16))}` : '') + (st.running ? ' · <b>running…</b>' : '')
      + (st.error ? ` · <span style="color:var(--red)">${esc(st.error)}</span>` : '');
    if (document.activeElement?.closest?.('#monSettings') == null) {
      $('monEnabled').checked = !!cfg.enabled; $('monClaude').checked = !!cfg.claude; $('monPush').checked = !!cfg.push;
      $('monMax').value = cfg.max_calls; $('monProvider').value = cfg.provider || 'auto'; $('monEvery').value = cfg.review_every_min;
    }
    const keep = $('monReviewPick').value;
    monReviews = d.reviews || [];
    $('monReviewPick').innerHTML = monReviews.map((x, i) => `<option value="${i}">${esc(timeOnly(x.created_at).slice(0, 16))} · ${esc(x.kind)}${x.model ? '' : ' (code only)'}</option>`).join('');
    if (keep && monReviews[+keep]) $('monReviewPick').value = keep;
    showReview(+($('monReviewPick').value || 0));
    $('monRows').innerHTML = d.events.length ? d.events.map(e =>
      `<tr><td>${esc(timeOnly(e.created_at).slice(0, 16))}</td><td><span style="color:${monColor[e.severity] || ''};font-weight:600">${esc(e.kind)}</span>${sub(esc(e.symbol || ''))}</td>`
      + `<td class="wrap">${esc(e.title)}${monDetail(e)}</td><td class="wrap">${e.claude_note ? esc(e.claude_note) : '<span class="sub">' + noteHint(e, st) + '</span>'}</td></tr>`).join('')
      : '<tr><td colspan="4" class="empty">No findings for these dates. They are added automatically during market hours.</td></tr>';
  } catch (e) { $('monRows').innerHTML = `<tr><td colspan="4" class="empty">Could not load the monitor: ${esc(e.message)}</td></tr>`; }
}
$('monKind').addEventListener('change', loadMonitor);
$('monReviewPick').addEventListener('change', e => showReview(+e.target.value));
$('monSave').onclick = async () => {
  const body = {enabled: $('monEnabled').checked, claude: $('monClaude').checked, push: $('monPush').checked,
                max_calls: Number($('monMax').value) || 0, provider: $('monProvider').value, review_every_min: Number($('monEvery').value) || 30};
  const r = await fetch('/api/monitor/settings', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  $('monSaved').textContent = r.ok ? 'Saved.' : 'Only the owner account can change these.';
  loadMonitor();
};
$('monReview').onclick = async () => {
  const r = await fetch('/api/monitor/review-now', {method: 'POST', credentials: 'same-origin'});
  $('monSaved').textContent = r.ok ? 'Review started - it takes up to a minute (replay + Claude).' : 'Only the owner account can run it.';
  setTimeout(loadMonitor, 1500); setTimeout(loadMonitor, 20000); setTimeout(loadMonitor, 60000);
};

// v1.20.5: the Android app's WebView ignores file downloads, so: share sheet (send straight to the Claude app) -> normal
// download in a browser -> copy to clipboard (paste into a Claude chat). Every path tells you what happened.
$('monExport').onclick = async () => {
  const btn = $('monExport'), msg = t => { $('monSaved').textContent = t; };
  const day = $('endDate').value || new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
  btn.disabled = true; msg('Preparing the review file… (up to a minute if the replay has to run)');
  try {
    const r = await fetch('/api/monitor/export?day=' + encodeURIComponent(day), {credentials: 'same-origin'});
    if (!r.ok) throw Error((await r.text()).slice(0, 200) || r.status);
    const text = await r.text(), name = `strategy-review-${day}.json`;
    const native = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
    const hasFs = !!(window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Filesystem);
    if (window.oiSave && (!native || hasFs)) { msg((await window.oiSave(name, text, 'application/json')) + ' - then in Claude write: review this day'); return; }
    let copied = false;
    try { await navigator.clipboard.writeText(text + '\n\nreview this day'); copied = true; } catch {}
    if (!copied) { const ta = document.createElement('textarea'); ta.value = text + '\n\nreview this day'; document.body.appendChild(ta); ta.select();
      try { copied = document.execCommand('copy'); } catch {} ta.remove(); }
    msg(copied ? `Copied the ${day} review data (${Math.round(text.length / 1024)} KB). Open a Claude chat, long-press → Paste, send.`
               : 'Could not copy here - open this page in Chrome and press Download for Claude there.');
  } catch (e) { msg('Download failed: ' + e.message); }
  finally { btn.disabled = false; }
};

function aiWhy(st, cfg) {
  const k = st.keys || {};
  if (cfg.claude === false) return ' - turn on "AI explanations" and Save';
  if (cfg.provider === 'off') return ' - "AI for notes & reviews" is Off; choose Auto and Save';
  if (!k.gemini_var && !k.claude_var) return ' - the server sees no GEMINI_API_KEY' + (k.similar_variables && k.similar_variables.length ? ` (found: ${k.similar_variables.map(esc).join(', ')})` : '') + '. Check the name in Render → Environment, Save Changes, wait for the deploy.';
  if (cfg.provider === 'claude' && !k.claude_var) return ' - provider is Claude but only a Gemini key is set; choose Auto and Save';
  return '';
}
$('monTest').onclick = async () => {
  $('monSaved').textContent = 'Testing…';
  try { const r = await fetch('/api/monitor/test-ai', {method: 'POST', credentials: 'same-origin'}); const d = await r.json();
    $('monSaved').textContent = (d.ok ? '✓ ' : '✗ ') + (d.message || r.status);
  } catch (e) { $('monSaved').textContent = '✗ ' + e.message; }
  loadMonitor();
};

function noteHint(e, st) {            // v1.20.10: say why there is no AI note instead of a bare dash
  if (e.kind === 'SUGGESTION') return 'Summarised in the review above';
  if (e.kind === 'FEED' || e.kind === 'OPTION' || e.severity === 'info') return 'Explained by the finding itself';
  return st && st.ai ? 'Waiting for the AI - retried every 2 minutes' : 'No AI connected';
}


// v1.20.33: save the AI (Gemini / Claude) review shown above, plus the findings of the same day, as a text file
$('monReport').onclick = async () => {
  const i = +($('monReviewPick').value || 0), r = monReviews[i];
  if (!r) { $('monSaved').textContent = 'No AI review yet - press "Run review now" first.'; return; }
  const day = String(r.created_at || '').slice(0, 10);
  let evs = [];
  try { const q = await fetch(`/api/monitor?start=${day}&end=${day}&limit=300`, {credentials: 'same-origin'}); if (q.ok) evs = (await q.json()).events || []; } catch {}
  const lines = [`Option Intelligence - AI strategy report`, `Day: ${day}`,
    `Review: ${r.kind === 'eod' ? 'end of day' : r.kind} at ${timeOnly(r.created_at).slice(11, 16)} IST · ${r.model ? r.model : 'code findings only'}`, '',
    '=== AI REVIEW ===', r.text || '', '', `=== FINDINGS (${evs.length}) ===`];
  for (const e of evs) {
    lines.push(`${timeOnly(e.created_at).slice(11, 16)}  ${e.kind}${e.symbol ? ' ' + e.symbol : ''}  ${e.title}`);
    const d = e.detail || {};
    if (Array.isArray(d.blocked_by) && d.blocked_by.length) lines.push('    Blocked by: ' + d.blocked_by.join(' · '));
    if (e.claude_note) lines.push('    AI: ' + e.claude_note);
  }
  lines.push('', 'Code findings are facts from the app; the AI text is a second opinion - check it before changing any rule.');
  const name = `ai-report-${day}-${timeOnly(r.created_at).slice(11, 16).replace(':', '')}.txt`;
  try { $('monSaved').textContent = window.oiSave ? await window.oiSave(name, lines.join('\n'), 'text/plain') : 'Saving is not available on this page.'; }
  catch (e) { $('monSaved').textContent = 'Could not save the report: ' + e.message; }
};
