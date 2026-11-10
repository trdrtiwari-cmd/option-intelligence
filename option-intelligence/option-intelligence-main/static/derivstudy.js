// v1.20.57: Diagnostics - effectiveness of each derivatives-filter option (Off / Option A / Option B / each check).
(function () {
  const $ = id => document.getElementById(id);
  if (!$('dvLoad')) return;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = (v, s = '') => v === null || v === undefined ? '—' : (s === '+' && v > 0 ? '+' : '') + v;
  const tone = v => v === null || v === undefined ? '' : v > 0 ? 'color:var(--green)' : v < 0 ? 'color:var(--red)' : '';
  const today = new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
  const from = new Date(Date.now() + 5.5 * 3600e3 - 30 * 86400e3).toISOString().slice(0, 10);
  if (!$('dvStart').value) $('dvStart').value = from;
  if (!$('dvEnd').value) $('dvEnd').value = today;
  const head = '<thead><tr><th>Group</th><th>Setups</th><th>Tested</th><th>Target / stop</th><th>Win rate</th><th>Avg pts</th><th>Net pts</th><th>Avg best premium %</th></tr></thead>';
  const row = (r, extra = '') => `<tr><td><b>${esc(r.group)}</b>${r.note ? `<span class="sig-sub">${esc(r.note)}</span>` : ''}${extra}</td><td>${r.signals}</td><td>${r.tested}</td>` +
    `<td>${r.target} / ${r.stop}</td><td>${r.win_rate === null ? '—' : r.win_rate + '%'}</td><td style="${tone(r.avg_points)}">${num(r.avg_points, '+')}</td>` +
    `<td style="${tone(r.net_points)}">${num(r.net_points, '+')}</td><td>${num(r.avg_best_pct)}</td></tr>`;
  async function load() {
    const p = new URLSearchParams({start: $('dvStart').value, end: $('dvEnd').value, symbol: $('dvSymbol').value, score_min: $('dvScore').value});
    $('dvMsg').textContent = 'Loading…';
    try {
      const r = await fetch('/api/diagnostics/derivatives?' + p, {credentials: 'same-origin'});
      if (!r.ok) throw Error(await r.text());
      const d = await r.json();
      $('dvMsg').textContent = `Live mode now: ${d.mode_label} · ${d.with_readings} of ${d.setups} setups have derivatives readings` +
        ` (record ${d.by_mode_recorded.record}, Option A ${d.by_mode_recorded.gates}, Option B ${d.by_mode_recorded.score}).`;
      $('dvVerdict').textContent = d.verdict;
      $('dvOptions').innerHTML = head + '<tbody>' + d.options.map(o => row(o)).join('') + '</tbody>';
      $('dvChecks').innerHTML = head.replace('<th>Group</th>', '<th>Check · reading</th>') + '<tbody>' + d.checks.map(c =>
        `<tr><td colspan="8" style="padding-top:12px"><b>${esc(c.label)}</b></td></tr>` + c.rows.map(x => row(x)).join('')).join('') + '</tbody>';
      $('dvNote').textContent = d.note;
    } catch (e) {
      $('dvMsg').textContent = 'Could not load: ' + e.message;
    }
  }
  $('dvLoad').addEventListener('click', load);
  load();
})();
