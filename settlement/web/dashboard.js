const $ = id => document.getElementById(id);
const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
const count = new Intl.NumberFormat('en-IN');
const compact = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 });
const percentage = n => n === null ? '—' : n.toFixed(2) + '%';
let token = '';
let daily = [];
let query;
let offset = 0;
const pageSize = 25;
let sequence = 0;

async function fetchData(endpoint, params) {
  const suffix = params ? '?' + params.toString() : '';
  const response = await fetch('/api/v1/' + endpoint + suffix, {
    headers: { Authorization: 'Bearer ' + token }
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Check the selected filters and try again.');
  }
  return response.json();
}

function status(message, isError = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', isError);
}

$('access').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  token = $('token').value;
  $('token').value = '';
  status('Connecting…');
  try {
    const data = await fetchData('data-status');
    $('start').value = data.start_date ?? '';
    $('end').value = data.end_date ?? '';
    $('access').hidden = true;
    $('filters').hidden = false;
    $('freshness').textContent = data.last_ingested_at_utc
      ? 'Last load: ' + new Date(data.last_ingested_at_utc.replace(' ', 'T') + 'Z').toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST'
      : '';
    if (data.start_date) await refresh();
    else status('No successful payments are available.');
  } catch (error) {
    token = '';
    status(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('disconnect').addEventListener('click', () => {
  sequence++;
  token = '';
  $('access').hidden = false;
  $('filters').hidden = true;
  $('report').hidden = true;
  $('freshness').textContent = '';
  status('');
  $('token').focus();
});

$('filters').addEventListener('submit', event => {
  event.preventDefault();
  refresh();
});

async function refresh() {
  const request = ++sequence;
  query = new URLSearchParams({ start_date: $('start').value, end_date: $('end').value });
  if ($('merchant').value) query.set('merchant_id', $('merchant').value);
  $('report').hidden = true;
  status('Loading settlement metrics…');
  try {
    const [summary, days, gaps, merchants, payments] = await Promise.all([
      fetchData('settlement-summary', query), fetchData('daily-trend', query),
      fetchData('merchant-gaps', query), fetchData('merchant-exceptions', query),
      fetchData('payment-exceptions', paymentQuery(0)),
    ]);
    if (request !== sequence) return;
    daily = days;
    renderCards(summary);
    renderGaps(gaps);
    renderMerchants(merchants);
    renderPayments(payments, 0);
    renderDailyValues();
    $('report').hidden = false;
    drawTrend();
    status(summary.transaction_count ? '' : 'No successful payments match these filters.');
  } catch (error) {
    if (request === sequence) status(error.message, true);
  }
}

function renderCards(summary) {
  const metrics = [
    ['Successful payments', count.format(summary.transaction_count)],
    ['Transaction volume', currency.format(summary.transaction_amount)],
    ['Settlement rate', percentage(summary.settlement_rate)],
    ['Settlement gap', currency.format(summary.settlement_gap)],
    ['30-minute SLA', percentage(summary.sla_rate)],
    ['Merchants breaching both thresholds', count.format(summary.merchant_risk_count)],
  ];
  $('cards').replaceChildren(...metrics.map(([label, value]) => {
    const card = document.createElement('article');
    card.className = 'card';
    const title = document.createElement('span');
    const number = document.createElement('strong');
    title.textContent = label;
    number.textContent = value;
    card.append(title, number);
    return card;
  }));
}

function renderGaps(rows) {
  $('gaps').replaceChildren();
  if (!rows.length) $('gaps').textContent = 'No merchant gaps in this period.';
  for (const row of rows) {
    const item = document.createElement('div');
    item.className = 'bar-row';
    const label = document.createElement('span');
    label.className = 'bar-label';
    label.textContent = row.merchant_name;
    const track = document.createElement('div');
    track.className = 'bar-track';
    track.setAttribute('aria-hidden', 'true');
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.width = 100 * row.settlement_gap / rows[0].settlement_gap + '%';
    track.append(bar);
    const value = document.createElement('span');
    value.textContent = currency.format(row.settlement_gap);
    item.append(label, track, value);
    $('gaps').append(item);
  }
}

function tableRow(values) {
  const row = document.createElement('tr');
  for (const value of values) {
    const cell = row.insertCell();
    if (value instanceof Node) cell.append(value);
    else cell.textContent = value;
  }
  return row;
}

function renderMerchants(rows) {
  $('exceptions').replaceChildren(...rows.map(row => {
    const button = document.createElement('button');
    button.className = 'secondary';
    button.textContent = 'Filter';
    button.setAttribute('aria-label', 'Filter by ' + row.merchant_name);
    button.onclick = () => {
      $('merchant').value = row.merchant_id;
      refresh();
    };
    return tableRow([row.merchant_name + ' · ' + row.merchant_id, row.risk_level,
      percentage(row.settlement_rate), percentage(row.sla_rate), currency.format(row.settlement_gap), button]);
  }));
  if (!rows.length) $('exceptions').append(tableRow(['No merchants breached a threshold.']));
}

function paymentQuery(skip) {
  const params = new URLSearchParams(query);
  params.set('limit', pageSize);
  params.set('offset', skip);
  return params;
}

function renderPayments(rows, skip) {
  offset = skip;
  $('payments').replaceChildren(...rows.map(row => tableRow([row.transaction_id, row.merchant_id,
    row.transaction_ts, row.status.replaceAll('_', ' ') + (row.overdue ? ' · overdue' : ''),
    currency.format(row.settlement_gap)])));
  if (!rows.length) $('payments').append(tableRow(['No payment exceptions.']));
  $('prev').disabled = skip === 0;
  $('next').disabled = rows.length < pageSize;
}

async function pageBy(delta) {
  const request = ++sequence;
  const skip = offset + delta;
  $('prev').disabled = $('next').disabled = true;
  try {
    const rows = await fetchData('payment-exceptions', paymentQuery(skip));
    if (request === sequence) renderPayments(rows, skip);
  } catch (error) {
    if (request === sequence) {
      status(error.message, true);
      $('prev').disabled = offset === 0;
      $('next').disabled = false;
    }
  }
}

$('prev').onclick = () => pageBy(-pageSize);
$('next').onclick = () => pageBy(pageSize);

function renderDailyValues() {
  const table = document.createElement('table');
  const header = table.createTHead().insertRow();
  for (const name of ['Payment date', 'Successful payments', 'Settled']) {
    const cell = document.createElement('th');
    cell.textContent = name;
    header.append(cell);
  }
  const body = table.createTBody();
  for (const row of daily) {
    body.append(tableRow([row.date, currency.format(row.transaction_amount), currency.format(row.settled_amount)]));
  }
  $('daily-values').replaceChildren(table);
}

function drawTrend() {
  if ($('report').hidden) return;
  const canvas = $('trend');
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const scale = window.devicePixelRatio || 1;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale, scale);
  ctx.font = '11px Segoe UI, sans-serif';
  ctx.fillStyle = '#43566d';
  if (!daily.length) {
    ctx.fillText('No values in this period.', 6, 22);
    return;
  }
  const max = daily.reduce((value, row) => Math.max(value, row.transaction_amount, row.settled_amount), 1);
  const left = 60, right = width - 18, top = 15, bottom = height - 29;
  const first = Date.parse(daily[0].date);
  const duration = Date.parse(daily.at(-1).date) - first;
  const x = row => duration ? left + (right - left) * (Date.parse(row.date) - first) / duration : (left + right) / 2;
  const y = value => bottom - (bottom - top) * value / max;
  for (let i = 0; i <= 4; i++) {
    const value = max * i / 4;
    ctx.strokeStyle = '#dae3ed';
    ctx.beginPath();
    ctx.moveTo(left, y(value));
    ctx.lineTo(right, y(value));
    ctx.stroke();
    ctx.fillText('₹' + compact.format(value), 2, y(value) + 4);
  }
  const labels = new Set([0, Math.floor((daily.length - 1) / 2), daily.length - 1]);
  for (const i of labels) {
    const label = daily[i].date.slice(5);
    ctx.fillText(label, Math.min(width - 36, x(daily[i]) - 13), height - 7);
  }
  for (const [field, color] of [['transaction_amount', '#315db0'], ['settled_amount', '#168675']]) {
    ctx.strokeStyle = ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    daily.forEach((row, i) => i ? ctx.lineTo(x(row), y(row[field])) : ctx.moveTo(x(row), y(row[field])));
    ctx.stroke();
    if (daily.length === 1) {
      ctx.beginPath();
      ctx.arc(x(daily[0]), y(daily[0][field]), 3, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
}

new ResizeObserver(drawTrend).observe($('trend'));

