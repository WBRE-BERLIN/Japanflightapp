// Tiny dependency-free SVG charts: sparkline + multi-series line chart with hover.
import { esc, money, dayShort } from './util.js';

export function sparkline(values, { width = 120, height = 28 } = {}) {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return '';
  const xs = values.length - 1 || 1;
  const vals = pts.map(([, v]) => v);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const X = (i) => 2 + (i / xs) * (width - 4);
  const Y = (v) => 3 + (1 - (v - min) / span) * (height - 6);
  const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const [li, lv] = pts[pts.length - 1];
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
    <path d="${d}"/><circle cx="${X(li).toFixed(1)}" cy="${Y(lv).toFixed(1)}" r="3"/></svg>`;
}

/**
 * series: [{ key, label, color: 'var(--series-1)', points: [{x: 'YYYY-MM-DD', y: number|null}] }]
 */
export function lineChart(el, series, { height = 230, currency = 'EUR' } = {}) {
  const active = series.filter((s) => s.points.some((p) => p.y != null));
  if (!active.length) {
    el.innerHTML = '<p class="empty">No history yet – it builds up with every scrape run.</p>';
    return;
  }
  const xs = [...new Set(active.flatMap((s) => s.points.map((p) => p.x)))].sort();
  const ys = active.flatMap((s) => s.points.map((p) => p.y)).filter((v) => v != null);
  let min = Math.min(...ys);
  let max = Math.max(...ys);
  const pad = (max - min) * 0.12 || max * 0.05 || 10;
  min = Math.max(0, min - pad);
  max += pad;
  const W = Math.max(320, el.clientWidth || 640);
  const H = height;
  const m = { l: 56, r: 72, t: 10, b: 26 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const X = (x) => m.l + (xs.length === 1 ? iw / 2 : (xs.indexOf(x) / (xs.length - 1)) * iw);
  const Y = (y) => m.t + (1 - (y - min) / (max - min)) * ih;
  const ticks = niceTicks(min, max, 4);
  const xTicks = pickXTicks(xs, Math.max(2, Math.floor(iw / 90)));

  const grid = ticks.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`).join('');
  const yAxis = ticks.map((t) => `<text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${esc(money(t, currency))}</text>`).join('');
  const xAxis = xTicks.map((x) => `<text x="${X(x)}" y="${H - 6}" text-anchor="middle">${esc(dayShort(x))}</text>`).join('');

  // end labels, nudged apart so they do not collide
  const ends = active.map((s) => {
    const last = [...s.points].reverse().find((p) => p.y != null);
    return { s, x: X(last.x), y: Y(last.y), v: last.y };
  }).sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;

  const paths = active.map((s) => {
    let d = '';
    let pen = false;
    for (const p of s.points) {
      if (p.y == null) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`;
      pen = true;
    }
    const lone = s.points.filter((p) => p.y != null).length === 1
      ? s.points.filter((p) => p.y != null).map((p) => `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="4" fill="${s.color}"/>`).join('')
      : '';
    return `<path d="${d}" stroke="${s.color}"/>${lone}`;
  }).join('');
  const labels = ends.map((e) => `<text class="end-label" x="${W - m.r + 8}" y="${e.y + 4}">${esc(e.s.label)}</text>`).join('');

  el.classList.add('chart');
  el.innerHTML = `
    <div class="chart-legend">${active.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join('')}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cheapest price per day">
      <g class="grid">${grid}</g><g class="axis">${yAxis}${xAxis}</g>
      <g class="series">${paths}</g>${labels}
      <line class="xhair" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" visibility="hidden"/>
      <g class="dots"></g>
      <rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/>
    </svg>
    <div class="tooltip" hidden></div>`;

  const svg = el.querySelector('svg');
  const hair = el.querySelector('.xhair');
  const dots = el.querySelector('.dots');
  const tip = el.querySelector('.tooltip');
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    let best = xs[0];
    for (const x of xs) if (Math.abs(X(x) - px) < Math.abs(X(best) - px)) best = x;
    const cx = X(best);
    hair.setAttribute('x1', cx);
    hair.setAttribute('x2', cx);
    hair.setAttribute('visibility', 'visible');
    const rows = active.map((s) => ({ s, p: s.points.find((p) => p.x === best) })).filter((r) => r.p && r.p.y != null);
    dots.innerHTML = rows.map((r) => `<circle cx="${cx}" cy="${Y(r.p.y)}" r="4.5" fill="${r.s.color}" stroke="var(--surface)" stroke-width="2"/>`).join('');
    tip.innerHTML = `<div style="font-weight:600;margin-bottom:4px">${esc(dayShort(best))}</div>${rows
      .sort((a, b) => a.p.y - b.p.y)
      .map((r) => `<div class="tt-row"><span class="tt-k"><i class="tt-sw" style="background:${r.s.color}"></i>${esc(r.s.label)}</span><b>${esc(money(r.p.y, currency))}</b></div>`)
      .join('')}`;
    tip.hidden = false;
    const left = (cx / W) * r.width;
    tip.style.left = `${Math.min(Math.max(0, left + 12), r.width - tip.offsetWidth)}px`;
    tip.style.top = `${10}px`;
  };
  const leave = () => {
    hair.setAttribute('visibility', 'hidden');
    dots.innerHTML = '';
    tip.hidden = true;
  };
  const hit = el.querySelector('.hit');
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', leave);
}

function niceTicks(min, max, n) {
  const step0 = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(step0 || 1));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(Math.round(v));
  return out;
}

function pickXTicks(xs, n) {
  if (xs.length <= n) return xs;
  const out = [];
  for (let i = 0; i < n; i++) out.push(xs[Math.round((i / (n - 1)) * (xs.length - 1))]);
  return [...new Set(out)];
}
