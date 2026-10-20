
const $ = (id) => document.getElementById(id);

const symbol = $('symbol');
const expiry = $('expiry');
const moneyness = $('moneyness');
const refreshButton = $('refresh');
const loadOptionsButton = $('loadOptions');

const labels = {
  advance_decline: 'Advance–Decline Ratio',
  sector_heatmap: 'Sector Heat Map',
  option_premium_change: 'Option Premium % Change',
  call_put_graph: 'CE/PE Premium Graph',
  index_direction: 'Index Price Direction'
};

const directionNames = {
  1: 'Bullish',
  0: 'Neutral',
  '-1': 'Bearish'
};

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'medium'
  }) + ' IST';
}

function number(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';

  return parsed.toLocaleString('en-IN', {
    maximumFractionDigits: digits
  });
}

function money(value) {
  const valueText = number(value, 2);
  return valueText === '—' ? valueText : '₹' + valueText;
}

function percentChange(current, previous) {
  const now = Number(current);
  const before = Number(previous);

  if (
    current === null || current === undefined ||
    previous === null || previous === undefined ||
    !Number.isFinite(now) || !Number.isFinite(before) ||
    before === 0
  ) {
    return '—';
  }

  const difference = now - before;
  const percent = difference / Math.abs(before) * 100;

  return `${number(difference, 0)} (${number(percent, 2)}%)`;
}

const SIGNAL_STYLE = {
  'BUY CE': { color: '#5fe0a8', mark: '▲' },
  'BUY PE': { color: '#ff7b7b', mark: '▼' },
  WAIT: { color: '#ffd58a', mark: 'Ⅱ' }
};

function setSignal(signal, text) {
  const value = SIGNAL_STYLE[signal] ? signal : 'WAIT';
  const style = SIGNAL_STYLE[value];
  $('signal').textContent = value;
  $('signal').style.color = style.color;
  const mark = document.querySelector('.signal-mark');
  if (mark) {
    mark.textContent = style.mark;
    mark.style.color = style.color;
  }
  if (text) $('message').textContent = text;
}

function setSignalWait(text) {
  setSignal('WAIT', text);
}

function setPill(element, text, state = '') {
  element.textContent = text;
  element.className = 'pill' + (state ? ' ' + state : '');
}

function biasText(bias) {
  return bias === null || bias === undefined ? '—' : (directionNames[bias] ?? '—');
}

function signed(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function describe(key, value) {
  if (!value) return 'डेटा उपलब्ध नहीं';

  if (key === 'advance_decline') {
    return `${biasText(value.bias)} · A/D ${value.ratio ?? '—'}`;
  }

  if (key === 'sector_heatmap') {
    return `${biasText(value.bias)} · ${value.up ?? 0}↑ / ${value.down ?? 0}↓`;
  }

  if (key === 'option_premium_change') {
    return `${biasText(value.bias)} · CE ${signed(value.ce_pct)} · PE ${signed(value.pe_pct)}`;
  }

  if (key === 'call_put_graph') {
    return `${biasText(value.bias)} · CE ${signed(value.ce_session_pct)} · PE ${signed(value.pe_session_pct)}`;
  }

  if (key === 'index_direction') {
    const price = money(value.price);
    const change = value.change_pct === null || value.change_pct === undefined ? '' : ` · ${signed(value.change_pct)}`;
    return `${biasText(value.bias)} · ${price}${change}`;
  }

  return 'डेटा उपलब्ध';
}

function details(key, value) {
  if (!value) return 'डेटा अनुपलब्ध — signal में उपयोग नहीं';

  if (key === 'advance_decline') {
    return `Advances ${value.advances} · Declines ${value.declines} · Unchanged ${value.unchanged} (${value.counted}/${value.expected} stocks)`;
  }

  if (key === 'sector_heatmap') {
    const list = (value.sectors || []).map((s) => `${s.name} ${signed(s.pct)}`);
    return `Average ${signed(value.average_pct)} · ` + list.join(', ');
  }

  if (key === 'option_premium_change' || key === 'call_put_graph') {
    const extra = [value.expiry ? `Expiry ${value.expiry}` : '', value.strike ? `Strike ${number(value.strike, 0)}` : '']
      .filter(Boolean).join(' · ');
    if (key === 'call_put_graph') {
      return `Recent: CE ${signed(value.ce_recent_pct)} · PE ${signed(value.pe_recent_pct)}` + (extra ? ` · ${extra}` : '');
    }
    return 'Previous close के मुकाबले' + (extra ? ` · ${extra}` : '');
  }

  if (key === 'index_direction') {
    const state = value.fresh === true ? 'Fresh candle' : (value.status === 'MARKET_CLOSED' ? 'Market closed — last session' : 'Stale/unverified');
    return `Candle: ${formatTime(value.candle_time)} · ${state}`;
  }

  return '';
}

function updateCheckCards(data) {
  const cards = [...document.querySelectorAll('#checks .check:not(.note)')];

  cards.forEach((card) => {
    const heading = card.querySelector('h3')?.textContent;
    const key = Object.keys(labels).find((item) => labels[item] === heading);
    if (!key) return;

    const value = data?.[key];
    const strong = card.querySelector('strong');
    const small = card.querySelector('small');

    strong.textContent = describe(key, value);
    strong.style.color = !value ? '#ffd58a'
      : value.bias === 1 ? '#5fe0a8'
      : value.bias === -1 ? '#ff7b7b'
      : '#ffd58a';
    small.textContent = details(key, value);
  });
}

async function refreshSnapshot() {
  setSignalWait('Index data और required checks की जाँच हो रही है।');
  $('updated').textContent = 'जाँच जारी…';
  setPill($('livePill'), 'DATA STATUS: CHECKING');

  refreshButton.disabled = true;

  try {
    const response = await fetch(
      `/api/snapshot/${encodeURIComponent(symbol.value)}`,
      { cache: 'no-store' }
    );

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const result = await response.json();

    setSignal(
      result.signal,
      result.message ||
      (result.reasons || []).join(' ') ||
      'Required checks अभी पूर्ण नहीं हैं।'
    );

    $('updated').textContent = result.as_of
      ? `Candle: ${formatTime(result.as_of)} · Checked: ${formatTime(result.checked_at)}`
      : `Checked: ${formatTime(result.checked_at)}`;

    updateCheckCards(result.data);

    const status = result.market_data_status || 'UNAVAILABLE';

    if (status === 'RECEIVED' && result.fresh) {
      setPill($('livePill'), 'INDEX: FRESH', 'good');
    } else if (status === 'MARKET_CLOSED') {
      setPill($('livePill'), 'MARKET CLOSED', 'warn');
    } else if (status === 'STALE') {
      setPill($('livePill'), 'INDEX: STALE', 'warn');
    } else if (status === 'NOT_CONFIGURED') {
      setPill($('livePill'), 'TOKEN NOT SET', 'bad');
    } else if (status === 'API_ERROR' || status === 'CONNECTION_ERROR') {
      setPill($('livePill'), 'INDEX API: ERROR', 'bad');
    } else {
      setPill($('livePill'), 'INDEX: UNAVAILABLE', 'warn');
    }

    const notes = (result.reasons || []).filter((r) => r && r !== result.message);
    $('sourceStatus').textContent =
      `Source: ${result.source || 'Unknown'} · Status: ${status} · ` +
      (result.ready ? 'All checks available' : 'Required checks incomplete') +
      (notes.length ? ' — ' + notes.join(' | ') : '');

  } catch (error) {
    setSignalWait('Snapshot API से डेटा नहीं मिला। कोई अनुमानित signal नहीं दिखाया जाएगा।');
    $('updated').textContent = 'Connection error';
    $('sourceStatus').textContent = 'Snapshot API unavailable.';
    setPill($('livePill'), 'DATA STATUS: ERROR', 'bad');
    console.error('Snapshot request failed:', error);
  } finally {
    refreshButton.disabled = false;
  }
}

function renderOptionRows(data) {
  const ce = data?.ce || {};
  const pe = data?.pe || {};

  const rows = [
    ['Strike', number(ce.strike, 0), number(pe.strike, 0)],
    ['Instrument Key', ce.instrument_key || '—', pe.instrument_key || '—'],
    ['LTP', money(ce.ltp), money(pe.ltp)],
    ['Previous Close', money(ce.close_price), money(pe.close_price)],
    ['Volume', number(ce.volume, 0), number(pe.volume, 0)],
    ['Open Interest (OI)', number(ce.oi, 0), number(pe.oi, 0)],
    ['OI Change (current OI − previous OI)', percentChange(ce.oi, ce.prev_oi), percentChange(pe.oi, pe.prev_oi)],
    ['Previous OI', number(ce.prev_oi, 0), number(pe.prev_oi, 0)],
    ['IV', number(ce.iv, 2), number(pe.iv, 2)],
    ['Delta', number(ce.delta, 4), number(pe.delta, 4)],
    ['Theta', number(ce.theta, 4), number(pe.theta, 4)],
    ['Gamma', number(ce.gamma, 5), number(pe.gamma, 5)],
    ['Vega', number(ce.vega, 4), number(pe.vega, 4)]
  ];

  $('optionRows').replaceChildren();

  rows.forEach(([label, callValue, putValue]) => {
    const tr = document.createElement('tr');

    [label, callValue, putValue].forEach((value, index) => {
      const td = document.createElement('td');
      td.textContent = value;
      if (index === 1) td.className = 'ce-col';
      if (index === 2) td.className = 'pe-col';
      tr.appendChild(td);
    });

    $('optionRows').appendChild(tr);
  });
}

function renderOptionError(message) {
  $('optionRows').replaceChildren();

  const tr = document.createElement('tr');
  const td = document.createElement('td');
  td.colSpan = 3;
  td.textContent = message || 'Option data उपलब्ध नहीं है।';
  tr.appendChild(td);
  $('optionRows').appendChild(tr);
}

async function loadOptions() {
  loadOptionsButton.disabled = true;
  setPill($('optionStatus'), 'OPTIONS: LOADING');
  $('optionMessage').textContent = 'Option-chain data प्राप्त हो रहा है…';

  try {
    const params = new URLSearchParams({
      moneyness: moneyness.value
    });

    if (expiry.value) params.set('expiry_date', expiry.value);

    const response = await fetch(
      `/api/options/${encodeURIComponent(symbol.value)}?${params.toString()}`,
      { cache: 'no-store' }
    );

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const result = await response.json();

    if (!result.ok) {
      renderOptionError(result.message || 'Option API ने data नहीं दिया।');
      $('optionMessage').textContent = result.message || 'Option data unavailable.';
      $('spotValue').textContent = '—';
      $('atmValue').textContent = '—';
      $('selectedExpiry').textContent = result.expiry || '—';
      $('fetchTime').textContent = '—';
      setPill($('optionStatus'), 'OPTIONS: ERROR', 'bad');

      if (Array.isArray(result.available_expiries)) {
        populateExpiries(result.available_expiries, expiry.value);
      }
      return;
    }

    if (Array.isArray(result.available_expiries)) {
      populateExpiries(result.available_expiries, result.expiry);
    }

    $('spotValue').textContent = number(result.spot, 2);
    $('atmValue').textContent = number(result.atm_strike, 0);
    $('selectedExpiry').textContent = result.expiry || '—';
    $('fetchTime').textContent = formatTime(result.fetched_at);

    renderOptionRows(result);

    $('optionMessage').textContent =
      `${result.symbol} · ${result.moneyness} · Expiry ${result.expiry}. ` +
      'Server fetch time quote timestamp नहीं है; freshness independently verified नहीं है।';

    setPill($('optionStatus'), 'OPTIONS: RECEIVED', 'good');

  } catch (error) {
    renderOptionError('Option API से संपर्क नहीं हो सका। Render logs और API status जाँचें।');
    $('optionMessage').textContent = 'Request failed: ' + error.message;
    setPill($('optionStatus'), 'OPTIONS: ERROR', 'bad');
    console.error('Option request failed:', error);
  } finally {
    loadOptionsButton.disabled = false;
  }
}

function populateExpiries(expiries, preferredValue = '') {
  const previous = preferredValue || expiry.value;

  expiry.replaceChildren();

  if (!Array.isArray(expiries) || expiries.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'Expiry उपलब्ध नहीं';
    expiry.appendChild(option);
    return;
  }

  expiries.forEach((date) => {
    const option = document.createElement('option');
    option.value = date;
    option.textContent = date;
    expiry.appendChild(option);
  });

  if (expiries.includes(previous)) {
    expiry.value = previous;
  } else {
    expiry.value = expiries[0];
  }
}

refreshButton.addEventListener('click', refreshSnapshot);
loadOptionsButton.addEventListener('click', loadOptions);

symbol.addEventListener('change', async () => {
  await refreshSnapshot();
  await loadOptions();
});

expiry.addEventListener('change', loadOptions);
moneyness.addEventListener('change', loadOptions);

async function initializeDashboard() {
  await refreshSnapshot();
  await loadOptions();
}

initializeDashboard();
