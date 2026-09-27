/* Clench Insights page. Fetches /api/insights (continuous aggregates only) and draws five charts.
   Simulated history and live data are separate datasets: a gray line vs blue points, plus a
   labeled "Simulated 4 weeks" span and a shaded "Live today" column, so color is never the only cue. */
'use strict';

const REFRESH_MS = 60_000;
const REASONS = [
  { key: 'disconnected', label: 'Headband disconnected', color: '--series-1' },
  { key: 'blocked', label: 'Signal blocked (movement)', color: '--series-2' },
  { key: 'stale', label: 'Signal stale', color: '--series-3' },
  { key: 'clock_skew', label: 'Late gesture (clock)', color: '--series-4' },
  { key: 'other', label: 'Paused, no board, other', color: '--series-other', from: ['paused', 'no_board', 'other'] },
];

const charts = {};
let last = null;

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d));
const pct = (v) => (v == null ? '-' : `${(v * 100).toFixed(1)}%`);
const shortDay = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

/* Draws the source regions behind the data: a caption over the simulated span, a shaded band
   with a caption on today's column, an optional horizontal reference, and recalibration marks. */
const regionsPlugin = {
  id: 'regions',
  beforeDatasetsDraw(chart, _args, opts) {
    const { ctx, chartArea: a, scales: { x, y } } = chart;
    if (!opts || !a) return;
    const n = chart.data.labels.length;
    const half = n > 1 ? (x.getPixelForValue(1) - x.getPixelForValue(0)) / 2 : 20;
    ctx.save();
    ctx.font = '12px system-ui, sans-serif';
    ctx.textBaseline = 'top';
    if (opts.liveIndex != null) {
      const cx = x.getPixelForValue(opts.liveIndex);
      ctx.fillStyle = css('--band');
      ctx.fillRect(cx - half, a.top, half * 2, a.bottom - a.top);
      ctx.fillStyle = css('--text-secondary');
      ctx.textAlign = 'right';
      ctx.fillText('Live today', Math.min(cx + half, a.right), a.top - 20);
    }
    if (opts.simRange) {
      const [s, e] = opts.simRange;
      const x0 = x.getPixelForValue(s) - half, x1 = x.getPixelForValue(e) + half;
      ctx.strokeStyle = css('--sim');
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0 + 2, a.top - 4); ctx.lineTo(x1 - 2, a.top - 4);
      ctx.stroke();
      ctx.fillStyle = css('--text-secondary');
      ctx.textAlign = 'left';
      ctx.fillText('Simulated 4 weeks', x0 + 2, a.top - 20);
    }
    if (opts.reference != null) {
      const py = y.getPixelForValue(opts.reference.value);
      ctx.strokeStyle = css('--text-muted');
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
      ctx.fillStyle = css('--text-muted');
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(opts.reference.label, a.left + 4, py - 2);
      ctx.textBaseline = 'top';
    }
    for (const i of opts.marks || []) {
      const px = x.getPixelForValue(i);
      ctx.strokeStyle = css('--text-muted');
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(px, a.top + 16); ctx.lineTo(px, a.bottom); ctx.stroke();
      ctx.fillStyle = css('--text-muted');
      ctx.textAlign = 'center';
      ctx.fillText('recalibrated', px, a.top + 2);
    }
    ctx.restore();
  },
};
Chart.register(regionsPlugin);

function simRange(series) {
  const idx = series.simulated.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0);
  return idx.length ? [idx[0], idx[idx.length - 1]] : null;
}

function baseOptions(data, extra = {}) {
  const grid = css('--grid'), text = css('--text-secondary'), n = data.days.length;
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    layout: { padding: { top: 26 } },
    scales: {
      x: { grid: { display: false }, border: { color: grid }, ticks: { color: text, maxRotation: 0, autoSkip: false,
        // Every third day, counted back from today, so "Today" is always labeled.
        callback(value, index) { return (n - 1 - index) % 3 === 0 ? this.getLabelForValue(value) : ''; } },
        ...extra.x },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: text }, ...extra.y },
    },
    plugins: {
      legend: { position: 'bottom', labels: { color: text, usePointStyle: true, boxHeight: 8 } },
      tooltip: { callbacks: extra.tooltip || {} },
      regions: { liveIndex: data.days.indexOf(data.today), ...extra.regions },
    },
  };
}

function lineDatasets(series, label, digits) {
  return [
    { label: `${label} (simulated 4 weeks)`, data: series.simulated, borderColor: css('--sim'),
      backgroundColor: css('--sim'), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, spanGaps: true,
      tension: 0.25, digits },
    { label: `${label} (live today)`, data: series.live, borderColor: css('--live'),
      backgroundColor: css('--live'), borderWidth: 2, pointRadius: 5, pointHoverRadius: 7,
      pointBorderColor: css('--surface-1'), pointBorderWidth: 2, spanGaps: true, digits },
  ];
}

const valueLabel = (d) => (ctx) =>
  ctx.parsed.y == null ? null : `${ctx.dataset.label}: ${Number(ctx.parsed.y).toFixed(d)}`;

function draw(name, config) {
  const fig = document.querySelector(`[data-chart="${name}"]`);
  if (charts[name]) charts[name].destroy();
  charts[name] = new Chart(fig.querySelector('canvas'), config);
}

function table(name, headers, rows) {
  const wrap = document.querySelector(`[data-chart="${name}"] .table-wrap`);
  const head = `<tr>${headers.map((h) => `<th scope="col">${h}</th>`).join('')}</tr>`;
  const body = rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('');
  wrap.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function source(data, key, i) {
  if (data[key].live[i] != null) return 'Live';
  if (data[key].simulated[i] != null) return 'Simulated';
  return '';
}

function render(data) {
  const labels = data.days.map((d) => (d === data.today ? 'Today' : shortDay(d)));
  const marks = data.recalibrated_days.map((d) => data.days.indexOf(d)).filter((i) => i >= 0);

  draw('strength', {
    type: 'line',
    data: { labels, datasets: lineDatasets(data.strength, 'Median strength', 2) },
    options: baseOptions(data, { y: { min: 0, max: 1 }, tooltip: { label: valueLabel(2) },
      regions: { simRange: simRange(data.strength), marks } }),
  });
  table('strength', ['Day', 'Source', 'Median strength'], data.days.map((d, i) =>
    [d, source(data, 'strength', i), fmt(data.strength.live[i] ?? data.strength.simulated[i])]));

  draw('margin', {
    type: 'line',
    data: { labels, datasets: lineDatasets(data.margin, 'Median margin', 2) },
    options: baseOptions(data, { y: { suggestedMin: 0.8, suggestedMax: 2.2 },
      tooltip: { label: (ctx) => (ctx.parsed.y == null ? null : `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}x threshold`) },
      regions: { simRange: simRange(data.margin), marks, reference: { value: 1, label: 'threshold (1.0x)' } } }),
  });
  table('margin', ['Day', 'Source', 'Median margin (x threshold)'], data.days.map((d, i) =>
    [d, source(data, 'margin', i), fmt(data.margin.live[i] ?? data.margin.simulated[i])]));

  const reasonValue = (r, i) => {
    const keys = r.from || [r.key];
    let sum = null;
    for (const k of keys) {
      const s = data.refusal_by_reason[k];
      const v = s.live[i] ?? s.simulated[i];
      if (v != null) sum = (sum ?? 0) + v;
    }
    return sum;
  };
  draw('refusal', {
    type: 'bar',
    data: {
      labels,
      datasets: REASONS.map((r) => ({
        label: r.label, data: data.days.map((_, i) => reasonValue(r, i)),
        backgroundColor: css(r.color), borderColor: css('--surface-1'), borderWidth: { top: 2 },
        borderRadius: 0, stack: 'refusals', maxBarThickness: 18,
      })),
    },
    options: baseOptions(data, {
      x: { stacked: true },
      y: { stacked: true, beginAtZero: true, ticks: { color: css('--text-secondary'), callback: (v) => `${Math.round(v * 100)}%` } },
      tooltip: { label: (ctx) => (ctx.parsed.y ? `${ctx.dataset.label}: ${pct(ctx.parsed.y)}` : null),
        footer: (items) => items.length ? `Total refused: ${pct(data.refusal_rate.live[items[0].dataIndex] ?? data.refusal_rate.simulated[items[0].dataIndex])}` : '' },
      regions: { simRange: simRange(data.refusal_rate) },
    }),
  });
  table('refusal', ['Day', 'Source', 'Total', ...REASONS.map((r) => r.label)], data.days.map((d, i) =>
    [d, source(data, 'refusal_rate', i), pct(data.refusal_rate.live[i] ?? data.refusal_rate.simulated[i]),
      ...REASONS.map((r) => pct(reasonValue(r, i)))]));

  draw('clenches', {
    type: 'line',
    data: { labels, datasets: lineDatasets(data.clenches_per_message, 'Clenches per message', 1) },
    options: baseOptions(data, { y: { beginAtZero: true, suggestedMax: 6 },
      tooltip: { label: valueLabel(1), footer: (items) => {
        if (!items.length) return '';
        const i = items[0].dataIndex, s = data.day1_clenches_per_message;
        const v = s.live[i] ?? s.simulated[i];
        return v == null ? '' : `Day 1 mode would take ${v.toFixed(1)}`;
      } },
      regions: { simRange: simRange(data.clenches_per_message) } }),
  });
  table('clenches', ['Day', 'Source', 'Clenches / message', 'Day 1 mode'], data.days.map((d, i) =>
    [d, source(data, 'clenches_per_message', i),
      fmt(data.clenches_per_message.live[i] ?? data.clenches_per_message.simulated[i], 1),
      fmt(data.day1_clenches_per_message.live[i] ?? data.day1_clenches_per_message.simulated[i], 1)]));

  draw('seconds', {
    type: 'line',
    data: { labels, datasets: lineDatasets(data.compose_s_per_message, 'Seconds per message', 1) },
    options: baseOptions(data, { y: { beginAtZero: true }, tooltip: { label: valueLabel(1) },
      regions: { simRange: simRange(data.compose_s_per_message) } }),
  });
  table('seconds', ['Day', 'Source', 'Seconds / message'], data.days.map((d, i) =>
    [d, source(data, 'compose_s_per_message', i),
      fmt(data.compose_s_per_message.live[i] ?? data.compose_s_per_message.simulated[i], 1)]));

  const t = data.live_today;
  document.getElementById('t-clenches').textContent = t.headband_clenches;
  document.getElementById('t-refused').textContent = t.headband_refused;
  document.getElementById('t-messages').textContent = t.messages;
  document.getElementById('t-keyboard').textContent = t.keyboard_gestures;

  const s = data.suggestion;
  document.getElementById('suggestion').hidden = !s.show;
  document.getElementById('no-suggestion').hidden = s.show;
  if (s.show) {
    document.getElementById('suggestion-text').textContent = s.message;
    document.getElementById('suggestion-reason').textContent = `Over the last ${s.days_used} days with data, ${s.reason}.`;
    document.getElementById('suggestion-sim').hidden = !s.uses_simulated;
  } else {
    document.getElementById('no-suggestion').textContent = `No suggestion: ${s.reason}.`;
  }

  const empty = data.days.every((_, i) => data.strength.simulated[i] == null && data.strength.live[i] == null);
  status(empty ? 'No clench data yet. Load the simulated history (python -m insights.seed) or start the logger.' : null);
}

function status(text) {
  const el = document.getElementById('status');
  el.hidden = !text;
  el.textContent = text || '';
}

async function load() {
  try {
    const res = await fetch('/api/insights', { cache: 'no-store' });
    const body = await res.json();
    if (!res.ok) { status(`Cannot load insights: ${body.error || res.status}.`); return; }
    last = body;
    render(body);
  } catch (e) {
    status('Cannot reach the insights service.');
  }
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => last && render(last));
load();
setInterval(load, REFRESH_MS);
