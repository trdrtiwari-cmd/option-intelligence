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
      `<td style="${stColor}">${esc(st)}</td><td>${fmt(r.exit_price)}</td><td>${esc(timeOnly(r.exit_time))}</td><td>${esc(r.exit_reason)}</td>` +
      `<td style="${tone(gross)}">${num(gross) === null ? '' : signed(gross)}${note}</td><td style="${tone(netv)}">${num(netv) === null ? '' : signed(netv)}${note}</td>` +
      `<td style="${tone(rupees(netv))}">${rupees(netv) === null ? '' : '₹' + signed(rupees(netv), 0)}</td><td>${esc(r.reason)}</td></tr>`;
  }).join('') : '<tr><td colspan="20" class="empty">No records for these filters yet. Open the terminal and refresh analysis during market hours.</td></tr>';
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
    render();
    loadPrices().then(render);
  } catch (e) {
    $('rows').innerHTML = `<tr><td colspan="20" class="empty">Could not load log: ${esc(e.message)}</td></tr>`;
  }
}

async function loadConfig() {
  try { const r = await fetch('/api/signals/config', {credentials: 'same-origin'}); if (r.ok) cfg = await r.json(); } catch {}
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
['qty', 'delta'].forEach(id => $(id).addEventListener('input', () => { try { localStorage.setItem('oi-sig-' + id, $(id).value); } catch {} render(); }));
$('refreshLog').onclick = load;
$('settleNow').onclick = settleNow;
$('exportXlsx').onclick = () => exportFile('xlsx');
$('exportCsv').onclick = () => exportFile('csv');
['startDate', 'endDate', 'symbol'].forEach(id => $(id).addEventListener('change', load));
loadConfig().then(load);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 10000);
