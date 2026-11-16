// v1.20.58: main terminal - "Derivatives filters" switch (owner) + the live readings of each check + the win rate of each option.
(function () {
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const tone = v => v === null || v === undefined ? '' : v > 0 ? 'color:var(--green)' : v < 0 ? 'color:var(--red)' : '';
  const sg = v => v === null || v === undefined ? '—' : (v > 0 ? '+' : '') + v;
  const say = m => { try { toast(m); } catch { console.log(m); } };
  const sym = () => ($('symbol') && $('symbol').value) || 'NIFTY';
  const LABELS = {off: 'Off', record: 'Record only', gates: 'Option A · gates', score: 'Option B · in score'};
  let lastKey = '', mode = null;

  function boot() {
    const sel = $('derivMode');
    if (!sel || !$('derivFilters')) return;
    fetch('/api/signals/config', {credentials: 'same-origin'}).then(r => r.ok ? r.json() : null).then(c => {
      if (c && c.deriv_mode) { sel.value = c.deriv_mode; mode = c.deriv_mode; showMode(); }
    }).catch(() => {});
    sel.addEventListener('change', async () => {
      const want = sel.value;
      try {
        const r = await fetch('/api/derivatives/mode', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode: want})});
        if (!r.ok) throw Error(r.status === 403 ? 'only the owner account can change it' : (await r.text()).slice(0, 120));
        const d = await r.json();
        mode = d.deriv_mode; showMode();
        say(`Derivatives filters: ${d.label} - signals recompute now`);
        try { lastAnalysisAt = 0; runAnalysis(true); } catch {}
      } catch (e) {
        if (mode) sel.value = mode;
        say('Derivatives filters not changed: ' + e.message);
      }
    });
    ['dfPeriod', 'symbol', 'scoreMin'].forEach(id => $(id) && $(id).addEventListener('change', loadStudy));
    loadStudy();
    setInterval(renderLive, 1500);
    setInterval(loadStudy, 5 * 60e3);
  }

  function showMode() {
    const p = $('dfMode');
    if (p) { p.textContent = LABELS[mode] || mode || '—'; p.className = 'pill ' + (mode === 'gates' || mode === 'score' ? 'ok' : 'muted'); }
  }

  function reading(c, side) {
    if (!c.ok) return ['no data', 'muted'];
    const k = String(c.key).slice(6);
    if (k === 'vol_calm' || k === 'no_decay') return c.bull ? ['calm / OK', 'ok'] : [k === 'vol_calm' ? 'SPIKE' : 'RANGE', 'bad'];
    if (c.bull && !c.bear) return [side === 'PE' ? 'CE side (against)' : 'CE side', side === 'PE' ? 'bad' : 'ok'];
    if (c.bear && !c.bull) return [side === 'CE' ? 'PE side (against)' : 'PE side', side === 'CE' ? 'bad' : 'ok'];
    return ['neutral', 'muted'];
  }

  function renderLive() {
    let a = null;
    try { a = lastAnalysis; } catch { return; }
    const d = a && a.derivatives;
    const key = d ? JSON.stringify([d.verdict, d.agree_text, a.last_closed_bar]) : String(!!a);
    if (key === lastKey) return;
    lastKey = key;
    if (!d) {
      $('dfVerdict').textContent = mode === 'off' ? 'Off - nothing is fetched or checked (v1.20.56 behaviour).' : 'Waiting for the next analysis (readings appear within about 30 s).';
      $('dfChecks').innerHTML = '<tr><td colspan="3" class="empty">No derivatives readings in this analysis</td></tr>';
      return;
    }
    if (d.mode && d.mode !== mode) { mode = d.mode; if ($('derivMode')) $('derivMode').value = mode; showMode(); }
    const side = d.side;
    const tech = d.score_tech || {}, b = d.score_b || {};
    const ts = side === 'CE' ? tech.bull : side === 'PE' ? tech.bear : Math.max(tech.bull || 0, tech.bear || 0);
    const effect = d.mode === 'gates' ? (d.would_block_a ? 'Option A is BLOCKING this signal' : 'Option A lets this signal through')
      : d.mode === 'score' ? `Option B score in use: ${b.side}/100 (technical alone ${ts})`
      : `Record only - not changing the signal. Option A would ${d.would_block_a ? 'BLOCK' : 'pass'} it; Option B score would be ${b.side}/100 (now ${ts})`;
    $('dfVerdict').innerHTML = `${side ? '<b>' + esc(side) + '</b> · ' : 'No CE / PE side · '}${esc(effect)}` +
      (d.blocked_by_a && d.blocked_by_a.length ? `<span class="sig-sub">A blocks on: ${esc(d.blocked_by_a.join(' · '))}</span>` : '');
    const rows = (d.checks || []).map(c => {
      const [t, cls] = reading(c, side);
      return `<tr><td>${esc(c.label)}<span class="sig-sub">weight ${c.weight} in Option B</span></td><td><span class="pill ${cls}">${esc(t)}</span></td><td style="white-space:normal;text-align:left;min-width:220px">${esc(c.detail)}</td></tr>`;
    });
    (d.risk_gates || []).forEach(g => rows.push(`<tr><td>${esc(g.label)}<span class="sig-sub">Option A and B</span></td><td><span class="pill ${g.pass ? 'ok' : 'bad'}">${g.pass ? 'pass' : 'BLOCK'}</span></td><td style="white-space:normal;text-align:left;min-width:220px">${esc(g.detail)}</td></tr>`));
    $('dfChecks').innerHTML = rows.join('') || '<tr><td colspan="3" class="empty">No checks</td></tr>';
  }

  async function loadStudy() {
    if (!$('dfStudy')) return;
    const days = Number(($('dfPeriod') && $('dfPeriod').value) || 30);
    const ist = t => new Date(t + 5.5 * 3600e3).toISOString().slice(0, 10);
    const end = ist(Date.now()), start = ist(Date.now() - (days - 1) * 86400e3);
    const sm = Number(($('scoreMin') && $('scoreMin').value) || 60);
    try {
      const r = await fetch('/api/diagnostics/derivatives?' + new URLSearchParams({start, end, symbol: sym(), score_min: String(sm)}), {credentials: 'same-origin'});
      if (!r.ok) throw Error(r.status === 401 ? 'log in to see results' : (await r.text()).slice(0, 120));
      const s = await r.json();
      const pick = s.options.filter(o => !/blocks|drops below/.test(o.group));
      $('dfStudy').innerHTML = pick.map(o => `<tr><td><b>${esc(o.group)}</b></td><td>${o.signals}</td><td>${o.tested}</td><td>${o.target} / ${o.stop}</td>` +
        `<td><b>${o.win_rate === null ? '—' : o.win_rate + '%'}</b></td><td style="${tone(o.avg_points)}">${sg(o.avg_points)}</td><td style="${tone(o.net_points)}">${sg(o.net_points)}</td></tr>`).join('');
      $('dfStudyNote').textContent = `${sym()} · ${days === 1 ? 'today' : 'last ' + days + ' days'} · ${s.with_readings} of ${s.setups} setups have readings · Option B at score ≥ ${sm}`;
      $('dfBest').textContent = s.verdict;
    } catch (e) {
      $('dfStudy').innerHTML = `<tr><td colspan="7" class="empty">Could not load: ${esc(e.message)}</td></tr>`;
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
