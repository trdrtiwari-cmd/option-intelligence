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
  const rs = closed.length ? rupees(net) : null;
  $('statRupee').textContent = rs === null ? (qty() ? '—' : 'enter quantity') : '₹' + signed(rs, 0); $('statRupee').style.cssText = tone(rs);
  $('cfgLine').textContent = `Auto-exit target: ${cfg.target} (ATR multiple) · cost allowance ${cfg.cost_points} index pts per trade · time exit ${cfg.squareoff || '15:20'} IST · exits are checked every few seconds while the server is awake.`;

  $('rows').innerHTML = rows.length ? rows.map(r => {
    const st = r.trade_status || 'OPEN';
    const stColor = st === 'TARGET' ? 'var(--green)' : st === 'STOP' ? 'var(--red)' : '';
    const live = st === 'OPEN' ? openPnl(r) : null;
    const gross = st === 'OPEN' ? live : r.gross_pnl_points, netv = st === 'OPEN' && live !== null ? live - cfg.cost_points : r.net_pnl_points;
    const note = st === 'OPEN' && live !== null ? ' <span class="sub">(live)</span>' : '';
    const bf = Number(r.levels_backfilled) ? ' <span class="sub" title="Saved before auto-exit existed: stop/target rebuilt from the current ATR">*</span>' : '';
    return `<tr><td>${esc(r.session_date)}</td><td>${esc(timeOnly(r.generated_at))}</td><td>${esc(r.symbol)}</td><td>${esc(r.interval)}m</td><td>${esc(r.mode)}</td>` +
      `<td>${r.side === 'CE' ? 'BUY CE' : 'BUY PE'}</td><td>${esc(r.score)}</td><td>${esc(r.regime)}</td><td>${esc(r.pattern)}</td>` +
      `<td>${fmt(r.entry ?? r.last_close)}</td><td>${fmt(r.stop_loss)}${bf}</td><td>${fmt(r.target)}${r.target_name ? ` <span class="sub">${esc(r.target_name)}</span>` : ''}</td>` +
      `<td>${num(r.atr_at_entry) === null ? '' : fmt(r.atr_at_entry, 1) + ' <span class="sub">/ ' + fmt(r.sl_atr_mult, 2) + '× / ' + fmt(r.target_atr_mult, 2) + '×</span>'}</td>` +
      `<td style="${stColor}">${esc(st)}</td><td>${fmt(r.exit_price)}</td><td>${esc(timeOnly(r.exit_time))}</td><td>${esc(r.exit_reason)}</td>` +
      `<td style="${tone(gross)}">${num(gross) === null ? '' : signed(gross)}${note}</td><td style="${tone(netv)}">${num(netv) === null ? '' : signed(netv)}${note}</td>` +
      `<td style="${tone(rupees(netv))}">${rupees(netv) === null ? '' : '₹' + signed(rupees(netv), 0)}</td><td>${esc(r.reason)}</td></tr>`;
  }).join('') : '<tr><td colspan="21" class="empty">No records for these filters yet. Open the terminal and refresh analysis during market hours.</td></tr>';
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
    $('rows').innerHTML = `<tr><td colspan="21" class="empty">Could not load log: ${esc(e.message)}</td></tr>`;
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
  $('sumRows').innerHTML = summary.periods.length ? summary.periods.map(p =>
    `<tr><td>${esc(p.period)}</td><td>${p.signals}</td><td>${p.closed}${p.open ? ` <span class="sub">(+${p.open} open)</span>` : ''}</td><td>${p.win_rate === null ? '' : p.win_rate + '%'}</td>` +
    `<td>${p.target_exits} / ${p.stop_exits} / ${p.time_exits}</td><td style="${tone(p.gross_points)}">${p.closed ? signed(p.gross_points) : ''}</td><td>${p.closed ? fmt(p.cost_points) : ''}</td>` +
    `<td style="${tone(p.net_points)}"><b>${p.closed ? signed(p.net_points) : '—'}</b></td><td style="${tone(rupees(p.net_points))}">${p.closed ? rp(p.net_points) : ''}</td>` +
    `<td>${p.profit_factor ?? ''}</td><td>${p.avg_win === null ? '' : signed(p.avg_win)} / ${p.avg_loss === null ? '' : signed(p.avg_loss)}</td><td>${p.closed ? fmt(p.max_drawdown) : ''}</td></tr>`).join('')
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
