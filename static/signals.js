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
// ₹ result of one signal: the recorded premium result per lot when the contract is known, else index points x delta x lot (or the quantity above)
const rowRupees = (r, netPts) => {
  if (num(r.net_rs) !== null && (r.trade_status && r.trade_status !== 'OPEN')) return Number(r.net_rs);
  const lot = num(r.lot) || qty(), d = num(r.opt_delta) || delta();
  return num(netPts) === null || !lot ? null : Number(netPts) * d * lot;
};

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
  const rs = closed.length ? closed.reduce((t, r) => t + (rowRupees(r, r.net_pnl_points) ?? 0), 0) : null;
  $('statRupee').textContent = rs === null ? '—' : '₹' + signed(rs, 0); $('statRupee').style.cssText = tone(rs);
  $('cfgLine').textContent = `Auto-exit target: ${cfg.target} (ATR multiple) · cost allowance ${cfg.cost_points} index pts per trade · time exit ${cfg.squareoff || '15:20'} IST · exits are checked every few seconds while the server is awake.`;

  $('rows').innerHTML = rows.length ? rows.map(r => {
    const st = r.trade_status || 'OPEN';
    const stColor = st === 'TARGET' ? 'var(--green)' : st === 'STOP' ? 'var(--red)' : '';
    const live = st === 'OPEN' ? openPnl(r) : null;
    const netv = st === 'OPEN' && live !== null ? live - cfg.cost_points : r.net_pnl_points;
    const note = st === 'OPEN' && live !== null ? ' <span class="sub">(live)</span>' : '';
    const bf = Number(r.levels_backfilled) ? ' <span class="sub" title="Saved before auto-exit existed: stop/target rebuilt from the current ATR">*</span>' : '';
    const ce = String(r.side).toUpperCase() === 'CE';
    const title = `<span class="rec-side ${ce ? 'rec-ce' : 'rec-pe'}">BUY ${ce ? 'CE' : 'PE'}</span> ${esc(r.contract || r.symbol)}`;
    const hasOpt = num(r.opt_entry) !== null;
    const two = (opt, idx, tag = '') => (hasOpt && num(opt) !== null ? `₹${fmt(opt)}` + (num(idx) !== null ? `<br><span class="sub">index ${fmt(idx)}${tag}</span>` : '') : (num(idx) === null ? '' : `${fmt(idx)}<br><span class="sub">index level</span>`));
    const rsv = rowRupees(r, netv);
    const detail = [`score ${esc(r.score)}`, esc(r.regime), num(r.rr) !== null ? `R:R ${fmt(r.rr, 2)}` : '', num(r.atr_at_entry) === null ? '' : `ATR ${fmt(r.atr_at_entry, 1)} (${fmt(r.sl_atr_mult, 2)}× / ${fmt(r.target_atr_mult, 2)}×)`,
                    st === 'OPEN' ? '' : esc(r.exit_reason), r.opt_basis ? `exit ${esc(r.opt_basis)}` : ''].filter(Boolean).join(' · ');
    return `<tr><td class="rec-head" data-label="Signal">${title}</td><td data-label="Date · time">${esc(r.session_date)} ${esc(timeOnly(r.generated_at).slice(11, 16))}</td>` +
      `<td data-label="Buy price">${two(r.opt_entry, r.entry ?? r.last_close)}</td><td data-label="Stop-loss">${two(r.opt_sl, r.stop_loss)}${bf}</td>` +
      `<td data-label="Target">${two(r.opt_target, r.target, r.target_name ? ' · ' + esc(r.target_name) : '')}</td>` +
      `<td data-label="Status" style="${stColor}">${esc(st)}</td><td data-label="Exit price">${st === 'OPEN' ? '' : two(r.opt_exit, r.exit_price)}</td><td data-label="Exit time">${esc(timeOnly(r.exit_time).slice(11, 16))}</td>` +
      `<td data-label="Net P/L (pts)" style="${tone(netv)}">${num(netv) === null ? '' : signed(netv)}${note}</td>` +
      `<td data-label="Net P/L (₹ / lot)" style="${tone(rsv)}">${rsv === null ? '' : '₹' + signed(rsv, 0)}${rsv !== null && num(r.net_rs) === null ? ' <span class="sub">est.</span>' : ''}</td>` +
      `<td class="wrapcol" data-label="Details">${detail}</td></tr>`;
  }).join('') : '<tr><td colspan="11" class="empty">No records for these filters yet. A BUY signal is recorded the moment a confirmed setup passes the option profit check during market hours.</td></tr>';
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
}

// ---- results by day / month
let group = 'day', summary = null;
async function loadSummary() {
  try {
    const p = filters(); p.set('group', group); p.delete('limit');
    const r = await fetch('/api/signals/summary?' + p, {credentials: 'same-origin'});
    if (!r.ok) throw Error(await r.text());
    summary = await r.json(); renderSummary();
  } catch (e) { $('sumRows').innerHTML = `<tr><td colspan="12" class="empty">Could not load results: ${esc(e.message)}</td></tr>`; }
}
function renderSummary() {
  if (!summary) return;
  const t = summary.total, rp = v => { const x = rupees(v); return x === null ? '' : '₹' + signed(x, 0); };
  $('sumPeriodHead').textContent = group === 'month' ? 'Month' : 'Day';
  $('grpDay').className = group === 'day' ? '' : 'secondary'; $('grpMonth').className = group === 'month' ? '' : 'secondary';
  const card = (l, v, s = '') => `<div class="stat panel"><div class="label">${l}</div><div class="value" style="${s}">${v}</div></div>`;
  $('sumStats').innerHTML = card('Net P/L (index pts)', t.closed ? signed(t.net_points) : '—', tone(t.closed ? t.net_points : null)) +
    card('Win rate', t.win_rate === null ? '—' : t.win_rate + '%') + card('Profit factor', t.profit_factor ?? '—') +
    card('Expectancy / trade', t.expectancy === null ? '—' : signed(t.expectancy), tone(t.expectancy)) + card('Max drawdown (pts)', t.closed ? fmt(t.max_drawdown) : '—') +
    card('Best / worst trade', t.closed ? signed(t.best) + ' / ' + signed(t.worst) : '—');
  const prs = p => p.net_rs != null ? '₹' + signed(p.net_rs, 0) : rp(p.net_points);
  const L = (l, v, st = '') => `<td data-label="${l}"${st ? ` style="${st}"` : ''}>${v}</td>`;
  $('sumRows').innerHTML = summary.periods.length ? summary.periods.map(p =>
    `<tr><td class="rec-head" data-label="${group === 'month' ? 'Month' : 'Day'}">${esc(p.period)}</td>` + L('Signals', p.signals) +
    L('Closed', `${p.closed}${p.open ? ` <span class="sub">(+${p.open} open)</span>` : ''}`) + L('Win %', p.win_rate === null ? '' : p.win_rate + '%') +
    L('Target / stop / time', `${p.target_exits} / ${p.stop_exits} / ${p.time_exits}`) + L('Gross', p.closed ? signed(p.gross_points) : '', tone(p.gross_points)) +
    L('Costs', p.closed ? fmt(p.cost_points) : '') + L('Net (pts)', `<b>${p.closed ? signed(p.net_points) : '—'}</b>`, tone(p.net_points)) +
    L('Net ₹ / lot', p.closed ? prs(p) : '', tone(p.net_rs ?? rupees(p.net_points))) + L('Profit factor', p.profit_factor ?? '') +
    L('Avg win / loss', `${p.avg_win === null ? '' : signed(p.avg_win)} / ${p.avg_loss === null ? '' : signed(p.avg_loss)}`) + L('Max drawdown', p.closed ? fmt(p.max_drawdown) : '') + '</tr>').join('')
    : '<tr><td colspan="12" class="empty">No trades in this range yet.</td></tr>';
}
async function saveAtr(reset) {
  const msg = $('atrMsg'); msg.textContent = 'Saving…';
  const body = reset ? {} : {sl_atr: $('slAtr').value === '' ? null : Number($('slAtr').value), target_atr: $('tgtAtr').value === '' ? null : Number($('tgtAtr').value),
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
$('exportXlsx').onclick = () => exportFile('xlsx');
$('exportCsv').onclick = () => exportFile('csv');
['startDate', 'endDate', 'symbol'].forEach(id => $(id).addEventListener('change', load));
loadConfig().then(load);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 10000);
