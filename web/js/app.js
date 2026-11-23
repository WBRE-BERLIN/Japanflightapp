import { addDays, ago, day, dayDiff, dayShort, dur, esc, hm, money, safeUrl, span, stamp, store, toast } from './util.js';
import { lineChart, sparkline } from './charts.js';
import * as push from './push.js';

const $ = (s, r = document) => r.querySelector(s);
const PAGE = 15;
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
const state = { data: null, history: null, f: null, page: 1, focus: null };

// ── boot ────────────────────────────────────────────────────────────────────
boot();

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
  state.focus = new URLSearchParams(location.search).get('offer');
  bindSettings();
  try {
    const [latest, history] = await Promise.all([
      getJSON('data/latest.json'),
      getJSON('data/history.json').catch(() => null),
    ]);
    if (!latest.offers) throw new Error('no data yet');
    state.data = latest;
    state.history = history;
  } catch (e) {
    renderNoData(e);
    return;
  }
  state.f = { ...defaults(), ...store.get('jfw.filters', {}), pair: null };
  const focus = state.focus && state.data.offers.find((o) => o.id === state.focus);
  if (focus) {
    state.f.pair = focus.pair;
    state.f.win = 'all';
  }
  renderStatic();
  update();
  if (focus) requestAnimationFrame(() => document.getElementById(`o-${focus.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
}

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function defaults() {
  const c = state.data.config;
  return {
    win: 'all', dests: Object.keys(c.destinations), maxLay: c.filters.max_layover_hours,
    maxDur: c.filters.max_duration_hours, bagsOnly: false, sort: 'price', basis: 'bags',
    perPerson: false, allWild: false, exclAir: [], pair: null,
  };
}

function saveFilters() {
  const { pair, ...rest } = state.f;
  store.set('jfw.filters', rest);
}

// ── data helpers ────────────────────────────────────────────────────────────
const cfg = () => state.data.config;
const adults = () => cfg().adults || 2;
const cur = () => cfg().currency || 'EUR';
const price = (o) => (state.f.basis === 'bags' ? o.eff_total : o.price_total);
const shown = (v) => (state.f.perPerson ? v / adults() : v);
const fmt = (v) => money(shown(v), cur());
const destName = (code) => cfg().destinations[code]?.name || code;
const winLabel = (id) => (id === 'wild' ? 'Wildcard' : (cfg().windows.find((w) => w.id === id)?.label || id).split('·')[0].trim());

function maxLayover(leg) {
  if (!leg || leg.layovers == null) return null;
  return leg.layovers.reduce((m, l) => Math.max(m, l.duration_min || 0), 0);
}

function legOk(leg, f) {
  if (!leg) return true;
  if (leg.stops > 1) return false;
  if (leg.duration_min != null && leg.duration_min > f.maxDur * 60) return false;
  const lay = maxLayover(leg);
  if (lay != null && lay > f.maxLay * 60) return false;
  if (leg.self_transfer) return false;
  return true;
}

function basePass(o, f) {
  if (!f.dests.includes(o.dest)) return false;
  if (f.bagsOnly && o.bag.status !== 'included') return false;
  if (f.exclAir.length && o.airlines.some((a) => f.exclAir.includes(a))) return false;
  return legOk(o.outbound, f) && legOk(o.inbound, f);
}

/** Offers passing all filters except the window/pair selection. */
function filtered() {
  const f = state.f;
  const base = state.data.offers.filter((o) => basePass(o, f));
  const windowBest = Math.min(...base.filter((o) => o.window !== 'wild').map(price));
  const pct = cfg().wildcards?.min_saving_pct ?? 15;
  const wildLimit = Number.isFinite(windowBest) ? windowBest * (1 - pct / 100) : Infinity;
  for (const o of base) o._outstanding = o.window === 'wild' && price(o) <= wildLimit;
  return { base, windowBest, wildLimit };
}

function inWindow(o, f) {
  if (f.win === 'all') return o.window !== 'wild' || o._outstanding || f.allWild;
  return o.window === f.win;
}

function groupByPair(list) {
  const m = new Map();
  for (const o of list) {
    if (!m.has(o.pair)) m.set(o.pair, []);
    m.get(o.pair).push(o);
  }
  for (const v of m.values()) v.sort((a, b) => price(a) - price(b));
  return m;
}

const totalDur = (o) => (o.outbound?.duration_min ?? 1500) + (o.inbound?.duration_min ?? 1500);

// ── render: static parts ────────────────────────────────────────────────────
function renderStatic() {
  const c = cfg();
  $('#subtitle').textContent = `${c.origin.join('/')} → ${Object.values(c.destinations).map((d) => d.name).join(' · ')} · ${c.adults} adults · ${c.cabin.replace('_', ' ')}`;
  const gen = state.data.generated_at;
  const age = (Date.now() - new Date(gen)) / 3600000;
  const lvl = age < 30 ? '' : age < 60 ? 'warn' : 'bad';
  $('#updated').innerHTML = `<span class="dot ${lvl}"></span>${esc(ago(gen))}`;
  $('#updated').title = `Last update ${stamp(gen)}`;

  const notes = [];
  if (state.data.demo) notes.push(['info', '<b>Demo data.</b> These prices are made up so you can try the app. Real prices appear after the first scrape run.']);
  if (age > 36) notes.push(['', `Prices were last updated ${esc(ago(gen))}. Check the GitHub Actions scrape workflow.`]);
  if (state.data.run?.blocked) notes.push(['', 'The last run was rate-limited by Google; some date pairs were not refreshed.']);
  if (!navigator.onLine) notes.push(['', 'You are offline – showing the last saved prices.']);
  const b = $('#banner');
  b.hidden = !notes.length;
  b.innerHTML = notes.map(([cls, t]) => `<div class="${cls}">${t}</div>`).join('');

  renderFilters();
  renderAbout();
}

function renderFilters() {
  const f = state.f;
  const c = cfg();
  const wins = [['all', 'All'], ...c.windows.map((w) => [w.id, winLabel(w.id)]), ['wild', 'Wildcards']];
  const layOpts = [2, 3, 4, 5, 6];
  const durOpts = [16, 17, 18, 19, 20, 22, 24];
  const airlines = [...new Set(state.data.offers.flatMap((o) => o.airlines))].sort();
  $('#filters').innerHTML = `
    <div class="frow">
      <div class="seg" role="group" aria-label="Travel window" id="f-win">
        ${wins.map(([v, l]) => `<button data-v="${esc(v)}" aria-pressed="${f.win === v}">${esc(l)}</button>`).join('')}
      </div>
      ${Object.entries(c.destinations).map(([k, d]) => `<label class="toggle"><input type="checkbox" data-dest="${esc(k)}" ${f.dests.includes(k) ? 'checked' : ''}>${esc(d.name)}</label>`).join('')}
    </div>
    <div class="frow">
      <label class="field">Layover ≤
        <select id="f-lay">${layOpts.map((h) => `<option value="${h}" ${+f.maxLay === h ? 'selected' : ''}>${h} h</option>`).join('')}</select></label>
      <label class="field">Travel time ≤
        <select id="f-dur">${durOpts.map((h) => `<option value="${h}" ${+f.maxDur === h ? 'selected' : ''}>${h} h</option>`).join('')}</select></label>
      <label class="field">Sort
        <select id="f-sort">
          <option value="price" ${f.sort === 'price' ? 'selected' : ''}>Cheapest</option>
          <option value="duration" ${f.sort === 'duration' ? 'selected' : ''}>Fastest</option>
          <option value="date" ${f.sort === 'date' ? 'selected' : ''}>Departure date</option>
        </select></label>
      <label class="toggle"><input type="checkbox" id="f-bags" ${f.bagsOnly ? 'checked' : ''}>Bag included only</label>
    </div>
    <details class="more-filters">
      <summary>Exclude airlines &amp; more<span class="n" id="more-n" hidden></span></summary>
      <div class="frow" style="margin-bottom:8px">
        <span class="seg" role="group" aria-label="Price basis" id="f-basis">
          <button data-v="bags" aria-pressed="${f.basis === 'bags'}">Fare + bag estimate</button>
          <button data-v="fare" aria-pressed="${f.basis === 'fare'}">Fare only</button>
        </span>
        <span class="seg" role="group" aria-label="Price shown" id="f-pp">
          <button data-v="0" aria-pressed="${!f.perPerson}">Total for ${c.adults}</button>
          <button data-v="1" aria-pressed="${f.perPerson}">Per person</button>
        </span>
        <label class="toggle"><input type="checkbox" id="f-allwild" ${f.allWild ? 'checked' : ''}>Show all wildcards</label>
      </div>
      <div class="frow" aria-label="Exclude airlines"><span class="hint">Exclude:</span>
        ${airlines.map((a) => `<label class="toggle"><input type="checkbox" data-excl="${esc(a)}" ${f.exclAir.includes(a) ? 'checked' : ''}>${esc(a)}</label>`).join('') || '<span class="hint">–</span>'}
      </div>
    </details>`;
  bindFilterEvents();
  updateMoreCount();
}

function updateMoreCount() {
  const f = state.f;
  const n = f.exclAir.length + (f.basis !== 'bags') + (f.perPerson ? 1 : 0) + (f.allWild ? 1 : 0);
  const el = $('#more-n');
  if (!el) return;
  el.hidden = !n;
  el.textContent = n;
  el.title = `${n} active`;
}

let filterEventsBound = false;
function bindFilterEvents() {
  if (filterEventsBound) return;
  filterEventsBound = true;
  const box = $('#filters');
  box.addEventListener('click', (e) => {
    const b = e.target.closest('.seg button');
    if (!b) return;
    const v = b.dataset.v;
    const group = b.parentElement.id;
    if (group === 'f-win') setFilter({ win: v, pair: null });
    if (group === 'f-basis') setFilter({ basis: v });
    if (group === 'f-pp') setFilter({ perPerson: v === '1' });
  });
  box.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'f-lay') setFilter({ maxLay: +t.value });
    else if (t.id === 'f-dur') setFilter({ maxDur: +t.value });
    else if (t.id === 'f-sort') setFilter({ sort: t.value });
    else if (t.id === 'f-bags') setFilter({ bagsOnly: t.checked });
    else if (t.id === 'f-allwild') setFilter({ allWild: t.checked });
    else if (t.dataset.dest) {
      const dests = [...box.querySelectorAll('[data-dest]:checked')].map((i) => i.dataset.dest);
      if (!dests.length) { t.checked = true; return; }
      setFilter({ dests });
    } else if (t.dataset.excl) {
      setFilter({ exclAir: [...box.querySelectorAll('[data-excl]:checked')].map((i) => i.dataset.excl) });
    }
  });
}

function setFilter(patch) {
  Object.assign(state.f, patch);
  state.page = 1;
  saveFilters();
  for (const id of ['#f-win', '#f-basis', '#f-pp']) {
    const key = { '#f-win': 'win', '#f-basis': 'basis', '#f-pp': 'perPerson' }[id];
    document.querySelectorAll(`${id} button`).forEach((b) => {
      const val = key === 'perPerson' ? (b.dataset.v === '1') : b.dataset.v;
      b.setAttribute('aria-pressed', String(state.f[key] === val));
    });
  }
  updateMoreCount();
  update();
}

// ── render: dynamic parts ───────────────────────────────────────────────────
function update() {
  const ctx = filtered();
  renderHero(ctx);
  renderWindows(ctx);
  renderCalendar(ctx);
  renderList(ctx);
  renderHistory();
}

function renderHero({ base }) {
  const cands = base.filter((o) => inWindow(o, { ...state.f, win: 'all' }) && !o.stale);
  const best = cands.sort((a, b) => price(a) - price(b))[0];
  const el = $('#hero');
  if (!best) {
    el.innerHTML = `<div><div class="eyebrow">Cheapest right now</div><p class="hero-empty">Nothing matches your filters. Try a longer travel time or layover limit.</p></div>`;
    return;
  }
  const bagNote = best.bag.status === 'included' ? 'checked bag included'
    : `${money(best.price_total, cur())} fare + ≈${money(best.bag.extra_est, cur())} bags`;
  el.innerHTML = `
    <div>
      <div class="eyebrow">Cheapest right now · ${esc(winLabel(best.window))}</div>
      <div class="hero-price"><span class="big">${fmt(price(best))}</span>
        <span class="sub">${state.f.perPerson ? 'per person' : `for ${adults()} · ${money(price(best) / adults(), cur())} pp`}</span></div>
      <p class="hero-route">${esc(day(best.depart))} → ${esc(day(best.ret))} · ${best.nights} nights · ${esc(destName(best.dest))}</p>
      <p class="hint">${esc(best.airlines.join(', '))} · ${esc(bagNote)}${best.age_h != null ? ` · checked ${esc(ago(best.found_at))}` : ''}</p>
    </div>
    <div>${offerLegs(best)}<div class="links" style="margin-top:12px">${linkButtons(best, 1, 3)}</div></div>`;
}

function renderWindows({ base }) {
  const hist = state.history?.daily || {};
  const days = Object.keys(hist).sort().slice(-30);
  const cards = [...cfg().windows.map((w) => w.id), 'wild'].map((id, i) => {
    const list = base.filter((o) => o.window === id && (id !== 'wild' || o._outstanding || state.f.allWild));
    const best = list.reduce((b, o) => (!b || price(o) < price(b) ? o : b), null);
    const series = days.map((d) => hist[d]?.[`${id}|*`]?.[state.f.basis === 'bags' ? 'eff_total' : 'price_total'] ?? null);
    const known = series.filter((v) => v != null);
    const wk = series.length >= 8 ? series[series.length - 8] : known[0];
    const now = known[known.length - 1];
    let trend = '';
    if (wk != null && now != null && Math.abs(now - wk) >= 5) {
      const d = now - wk;
      trend = `<span class="${d < 0 ? 'trend-down' : 'trend-up'}">${d < 0 ? '↓' : '↑'} ${money(Math.abs(shown(d)), cur())}</span> vs 7 days`;
    }
    const perDest = Object.keys(cfg().destinations).map((dest) => {
      const b = list.filter((o) => o.dest === dest).reduce((m, o) => (!m || price(o) < price(m) ? o : m), null);
      return `<span class="wrow"><span>${esc(destName(dest))}</span><b>${b ? fmt(price(b)) : '–'}</b></span>`;
    }).join('');
    const label = id === 'wild' ? 'Wildcards · other dates' : cfg().windows.find((w) => w.id === id).label;
    const empty = id === 'wild'
      ? `<span class="wrow"><span>Nothing ≥${cfg().wildcards?.min_saving_pct ?? 15}% cheaper than the windows yet</span></span>`
      : '<span class="wrow"><span>No flights within your filters</span></span>';
    return `<button class="wcard" data-win="${esc(id)}" aria-pressed="${state.f.win === id}">
      <span class="wlabel"><span>${esc(label)}</span><span style="width:10px;height:3px;border-radius:2px;background:${SERIES[i] || SERIES[2]};align-self:center"></span></span>
      ${best ? `<span class="wprice">${fmt(price(best))} <small>${esc(span(best.depart, best.ret))}</small></span>${perDest}` : empty}
      <span class="wfoot"><span>${trend || (known.length ? `${known.length} days tracked` : 'no history yet')}</span>${sparkline(series, { width: 90, height: 24 })}</span>
    </button>`;
  });
  const el = $('#windows');
  el.innerHTML = cards.join('');
  el.querySelectorAll('.wcard').forEach((b) => b.addEventListener('click', () => {
    setFilter({ win: state.f.win === b.dataset.win ? 'all' : b.dataset.win, pair: null });
    $('#list-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
}

function renderCalendar({ base }) {
  const el = $('#calendar');
  const wins = cfg().windows.filter((w) => state.f.win === 'all' || state.f.win === w.id);
  if (!wins.length) {
    el.innerHTML = '<p class="hint">Wildcards are spread over the whole year – see the list below.</p>';
    return;
  }
  const byPair = groupByPair(base);
  const cov = state.data.coverage || {};
  const blocks = wins.map((w) => {
    const rows = [];
    for (let d = w.depart_from; d <= w.depart_to; d = addDays(d, 1)) rows.push(d);
    const cells = [];
    for (const d of rows) {
      for (const n of w.nights) {
        const r = addDays(d, n);
        if (w.latest_return && r > w.latest_return) { cells.push({ d, n, skip: true }); continue; }
        let best = null;
        let checked = false;
        for (const dest of state.f.dests) {
          const key = `${dest}|${d}|${r}`;
          if (cov[key]) checked = true;
          const o = byPair.get(key)?.[0];
          if (o && (!best || price(o) < price(best))) best = o;
        }
        cells.push({ d, n, r, best, checked });
      }
    }
    const vals = cells.filter((c) => c.best).map((c) => price(c.best)).sort((a, b) => a - b);
    const bucket = (v) => {
      if (vals.length < 2) return 5;
      const rank = vals.findIndex((x) => x >= v);
      return 5 - Math.min(5, Math.floor((rank / vals.length) * 6));
    };
    const head = `<tr><th class="row">Depart</th>${w.nights.map((n) => `<th>${n} nights</th>`).join('')}</tr>`;
    const body = rows.map((d) => `<tr><th class="row" scope="row">${esc(day(d))}</th>${w.nights.map((n) => {
      const c = cells.find((x) => x.d === d && x.n === n);
      if (c.skip) return '<td></td>';
      const pairSel = state.f.pair && state.f.pair.endsWith(`|${d}|${c.r}`);
      if (!c.best) {
        return `<td><span class="cell none" title="${c.checked ? 'Searched – nothing within your filters' : 'Not searched yet'}">${c.checked ? '–' : '·'}</span></td>`;
      }
      const o = c.best;
      const t = `${day(d)} → ${day(c.r)} · ${fmt(price(o))} · ${o.airlines.join(', ')} · ${destName(o.dest)}`;
      return `<td><button class="cell h${bucket(price(o))} ${pairSel ? 'sel' : ''}" data-pair="${esc(o.pair)}" title="${esc(t)}" aria-label="${esc(t)}">${esc(fmt(price(o)))}</button></td>`;
    }).join('')}</tr>`).join('');
    return `<div class="cal"><h3>${esc(w.label)}</h3><table>${head}${body}</table></div>`;
  });
  el.innerHTML = `<div class="cal-wrap">${blocks.join('')}</div>
    <div class="legend" style="margin-top:10px"><span>cheaper</span>${[5, 4, 3, 2, 1, 0].map((i) => `<span class="sw" style="background:var(--heat-${i})"></span>`).join('')}<span>pricier</span>
    <span style="margin-left:12px">· not searched yet</span><span>– nothing within filters</span></div>`;
  el.querySelectorAll('button.cell').forEach((b) => b.addEventListener('click', () => {
    const [, d, r] = b.dataset.pair.split('|');
    const cur = state.f.pair && state.f.pair.endsWith(`|${d}|${r}`);
    setFilter({ pair: cur ? null : `*|${d}|${r}` });
    if (!cur) $('#list-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
}

function renderList({ base }) {
  const f = state.f;
  let list = base.filter((o) => inWindow(o, f));
  if (f.pair) {
    const [dest, d, r] = f.pair.split('|');
    list = base.filter((o) => o.depart === d && o.ret === r && (dest === '*' || o.dest === dest));
  }
  const groups = [...groupByPair(list).values()].map((g) => ({ best: g[0], alts: g.slice(1) }));
  const sorters = {
    price: (a, b) => price(a.best) - price(b.best),
    duration: (a, b) => totalDur(a.best) - totalDur(b.best) || price(a.best) - price(b.best),
    date: (a, b) => a.best.depart.localeCompare(b.best.depart) || price(a.best) - price(b.best),
  };
  groups.sort(sorters[f.sort] || sorters.price);
  const minPrice = groups.length ? Math.min(...groups.map((g) => price(g.best))) : 0;

  $('#list-title').textContent = f.pair ? 'Flights for the selected dates' : f.win === 'wild' ? 'Wildcard dates' : 'Cheapest flights';
  $('#count').textContent = `${groups.length} date ${groups.length === 1 ? 'pair' : 'pairs'} · ${list.length} options`;
  const pf = $('#pair-filter');
  if (f.pair) {
    const [, d, r] = f.pair.split('|');
    pf.innerHTML = `<p class="pair-filter"><span class="chip">${esc(day(d))} → ${esc(day(r))}</span><button class="btn ghost" id="clear-pair">Show all dates ✕</button></p>`;
    $('#clear-pair').addEventListener('click', () => setFilter({ pair: null }));
  } else pf.innerHTML = '';

  const el = $('#offers');
  if (!groups.length) {
    el.innerHTML = `<div class="panel empty">No flights match. Relax the layover or travel-time limit – one-stop trips Berlin → Japan usually take 16–19 h.</div>`;
    $('#more').hidden = true;
    return;
  }
  const page = groups.slice(0, state.page * PAGE);
  el.innerHTML = page.map((g) => offerCard(g.best, g.alts, price(g.best) === minPrice)).join('');
  const more = $('#more');
  more.hidden = groups.length <= page.length;
  more.textContent = `Show more (${groups.length - page.length})`;
  more.onclick = () => { state.page += 1; renderList(filtered()); };
}

// ── offer card ──────────────────────────────────────────────────────────────
function offerCard(o, alts, isBest) {
  const focus = state.focus === o.id || alts.some((a) => a.id === state.focus);
  const s = o.pair_stats || {};
  const badges = [];
  const g = o.bag.source === 'Google';
  if (o.bag.status === 'included') badges.push(['good', `🧳 Bag included${g ? ' (Google)' : ''}`]);
  else if (g) badges.push(['warn', `🧳 1st bag ${o.bag.text} pp (Google)`]);
  else if (o.bag.status === 'extra') badges.push(['warn', `🧳 Bag extra ≈${money(o.bag.extra_est, cur())}`]);
  else badges.push(['warn', '🧳 Bag: check fare']);
  if (o._outstanding) badges.push(['deal', 'Outstanding wildcard']);
  if (s.low != null && o.price_total <= s.low + 1 && s.n > 2) badges.push(['deal', 'Lowest seen']);
  if (s.prev != null && o.price_total < s.prev - 1) badges.push(['deal', `↓ ${money(shown(s.prev - o.price_total), cur())} since last check`]);
  if (s.prev != null && o.price_total > s.prev + 1) badges.push(['', `↑ ${money(shown(o.price_total - s.prev), cur())}`]);
  if (o.price_level || o.typical_range) {
    const range = o.typical_range ? ` · usually ${money(shown(o.typical_range[0]), cur())}–${money(shown(o.typical_range[1]), cur())}` : '';
    badges.push([o.price_level === 'low' ? 'good' : o.price_level === 'high' ? 'warn' : '', `Google: ${o.price_level ? `prices ${o.price_level}` : 'price range'}${range}`]);
  }
  const flagText = {
    out_overnight_layover: 'Overnight layover (out)', in_overnight_layover: 'Overnight layover (return)',
    out_airport_change: 'Airport change (out)', in_airport_change: 'Airport change (return)',
    out_short_connection: 'Tight connection (out)', in_short_connection: 'Tight connection (return)',
    in_unknown: 'Return details not loaded', out_layover_unknown: 'Layover times unknown',
    cached_price: 'Cached price – verify',
  };
  for (const fl of o.flags) if (flagText[fl] && !(fl === 'in_layover_unknown')) badges.push(['warn', flagText[fl]]);
  if (o.stale) badges.push(['warn', `Not re-checked ${ago(o.found_at)}`]);
  badges.push(['info', o.source === 'travelpayouts' ? 'Aviasales' : o.source === 'demo' ? 'Demo' : 'Google Flights']);

  const series = (state.history?.pairs?.[o.pair] || []).map((e) => e[1] ?? e[2]);
  const spark = series.length > 1
    ? `${sparkline(series)}<span>low ${money(shown(Math.min(...series.filter((v) => v != null))), cur())}${s.low_day ? ` · ${esc(dayShort(s.low_day))}` : ''}</span>`
    : `<span>${o.found_at ? `checked ${esc(ago(o.found_at))}` : ''}</span>`;

  return `<article class="offer ${o.stale ? 'stale' : ''} ${focus ? 'focus' : ''}" id="o-${esc(o.id)}">
    <div class="o-head">
      <div>
        <div class="o-dates">${esc(day(o.depart))} → ${esc(day(o.ret))}</div>
        <div class="o-meta">${esc(winLabel(o.window))} · ${o.nights} nights · ${esc(destName(o.dest))} ${esc(o.outbound?.arr_airport || '')} · ${esc(o.airlines.join(', '))}</div>
      </div>
      <div class="o-price">
        <div class="main ${isBest ? 'best' : ''}">${fmt(price(o))}</div>
        <div class="sub">${state.f.perPerson ? `${money(price(o), cur())} for ${adults()}` : `${money(price(o) / adults(), cur())} pp`}</div>
        ${state.f.basis === 'bags' && o.bag.extra_est ? `<div class="bags">fare ${fmt(o.price_total)} + bags</div>` : ''}
      </div>
    </div>
    ${offerLegs(o)}
    ${vendorLine(o)}
    <div class="badges">${badges.map(([c, t]) => `<span class="badge ${c}">${esc(t)}</span>`).join('')}</div>
    <div class="o-foot"><div class="spark-box">${spark}</div><div class="links">${linkButtons(o, 1)}</div></div>
    ${alts.length ? `<details class="alts"><summary>${alts.length} more option${alts.length > 1 ? 's' : ''} for these dates</summary>${alts.map(altRow).join('')}</details>` : ''}
  </article>`;
}

function vendorLine(o) {
  const v = (o.booking_vendors || []).filter((x) => x.name);
  if (!v.length) return '';
  const items = v.slice(0, 4).map((x) => `${esc(x.name)}${x.airline ? ' <span title="Airline sells directly">✈</span>' : ''}${x.phone ? ' (phone)' : ''}${x.price ? ` ${esc(fmt(x.price))}` : ''}`);
  return `<p class="hint" style="margin:0">Sold by: ${items.join(' · ')}</p>`;
}

function altRow(o) {
  const lay = (leg) => (leg?.layovers || []).map((l) => `${l.airport} ${dur(l.duration_min)}`).join(', ') || (leg?.stops ? `${leg.stops} stop` : 'nonstop');
  const link = o.links[0];
  return `<div class="alt"><span><b>${fmt(price(o))}</b> · ${esc(o.airlines.join(', '))} · ${esc(destName(o.dest))} ${esc(o.outbound?.arr_airport || '')}
    <br><span class="hint">Out ${dur(o.outbound?.duration_min)} via ${esc(lay(o.outbound))} · Back ${dur(o.inbound?.duration_min)} via ${esc(lay(o.inbound))}</span></span>
    ${link ? `<a href="${esc(safeUrl(link.url))}" target="_blank" rel="noopener">${esc(link.label)} ↗</a>` : ''}</div>`;
}

function offerLegs(o) {
  return `<div class="legs">${legRow('Out', o.outbound)}${legRow('Back', o.inbound)}</div>`;
}

function legRow(label, leg) {
  if (!leg) {
    return `<div class="leg"><span class="dir">${label}</span><span class="hint">Return flight details not loaded – open the Google Flights link for options.</span></div>`;
  }
  const plus = dayDiff(leg.dep_time, leg.arr_time);
  const lays = leg.layovers;
  let track = '';
  if (lays && lays.length) {
    track = lays.map((l, i) => {
      const pos = ((i + 1) / (lays.length + 1)) * 100;
      const long = l.duration_min > state.f.maxLay * 60 * 0.75 || l.overnight;
      return `<span class="stop ${long ? 'long' : ''}" style="left:${pos}%"></span>
        <span class="lbl top" style="left:${pos}%">${esc(l.airport)} · ${dur(l.duration_min)}</span>`;
    }).join('');
  } else if (leg.stops) {
    track = `<span class="stop" style="left:50%"></span><span class="lbl top">${leg.stops} stop</span>`;
  } else {
    track = '<span class="lbl top">nonstop</span>';
  }
  track += `<span class="lbl bot">${dur(leg.duration_min)}</span>`;
  return `<div class="leg"><span class="dir">${label}</span>
    <div class="leg-line">
      <div class="leg-end"><div class="t">${hm(leg.dep_time)}</div><div class="ap">${esc(leg.dep_airport)}</div></div>
      <div class="track">${track}</div>
      <div class="leg-end r"><div class="t">${leg.arr_time ? hm(leg.arr_time) : '–'}${plus > 0 ? `<sup>+${plus}</sup>` : ''}</div><div class="ap">${esc(leg.arr_airport)}</div></div>
    </div>
    ${leg.airlines.length ? `<div class="leg-air">${esc(leg.airlines.join(' · '))}${leg.self_transfer ? ' · self-transfer' : ''}</div>` : ''}
  </div>`;
}

function linkButtons(o, primaryCount, max = 5) {
  const order = ['google_booking', 'aviasales', 'google', 'airline', 'skyscanner', 'kayak'];
  const links = [...o.links].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  return links.slice(0, max).map((l, i) => {
    const label = l.kind === 'google_booking' ? 'Book' : l.kind === 'airline' ? `${l.label} ↗` : l.label;
    return `<a class="btn ${i < primaryCount ? 'primary' : ''}" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${esc(label)}</a>`;
  }).join('');
}

// ── history ─────────────────────────────────────────────────────────────────
function renderHistory() {
  const hist = state.history?.daily || {};
  const days = Object.keys(hist).sort();
  const dest = state.f.dests.length === 1 ? state.f.dests[0] : '*';
  const field = state.f.basis === 'bags' ? 'eff_total' : 'price_total';
  const ids = [...cfg().windows.map((w) => w.id), 'wild'];
  const series = ids.map((id, i) => ({
    key: id,
    label: winLabel(id),
    color: SERIES[i] || SERIES[2],
    points: days.map((d) => {
      const v = hist[d]?.[`${id}|${dest}`]?.[field];
      return { x: d, y: v == null ? null : shown(v) };
    }),
  }));
  lineChart($('#history'), series, { currency: cur() });
  const rows = days.slice(-45).reverse();
  $('#history-table').innerHTML = rows.length ? `<table class="data"><thead><tr><th>Day</th>${series.map((s) => `<th class="num">${esc(s.label)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((d) => `<tr><td>${esc(day(d))}</td>${series.map((s) => {
      const p = s.points.find((x) => x.x === d);
      return `<td class="num">${p?.y != null ? money(p.y, cur()) : '–'}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table>` : '<p class="hint">No history yet.</p>';
}

// ── about / status ──────────────────────────────────────────────────────────
function renderAbout() {
  const runs = [...(state.data.runs || [])].reverse().slice(0, 10);
  const c = cfg();
  const row = (r) => {
    const g = r.sources?.google_flights || r.sources?.demo || {};
    const tp = r.sources?.travelpayouts || {};
    return `<tr><td>${esc(stamp(r.at))}</td><td>${esc(r.mode)}</td>
      <td class="num">${g.ok ?? 0}/${g.planned ?? 0}</td><td class="num">${g.no_results ?? 0}</td>
      <td class="num">${(g.error ?? 0) + (g.blocked ?? 0)}${r.blocked ? ' ⚠' : ''}</td>
      <td class="num">${tp.skipped ? '–' : (tp.offers ?? '–')}</td><td class="num">${r.seconds != null ? `${Math.round(r.seconds / 60)} min` : ''}</td></tr>`;
  };
  $('#about-body').innerHTML = `
    <div><h3>Recent scrape runs</h3><div class="scroll-x"><table class="data"><thead><tr><th>When</th><th>Mode</th>
      <th class="num">Searches OK</th><th class="num">No flights</th><th class="num">Errors</th><th class="num">Aviasales</th><th class="num">Time</th></tr></thead>
      <tbody>${runs.map(row).join('') || '<tr><td colspan="7">No runs yet</td></tr>'}</tbody></table></div></div>
    <div><h3>What is searched</h3>
      <p>${esc(c.origin.join('/'))} → ${Object.values(c.destinations).map((d) => `${esc(d.name)} (${esc(d.airports.join(', '))})`).join(' and ')}, ${c.adults} adults, round trip, max. ${c.constraints.max_stops} stop,
      layovers ≤ ${c.constraints.max_layover_hours} h and ≤ ${c.constraints.max_duration_hours} h per direction (the filters above narrow this further).</p>
      <p>${c.windows.map((w) => `<b>${esc(w.label)}</b>: departures ${esc(dayShort(w.depart_from))}–${esc(dayShort(w.depart_to))}, ${w.nights.join('/')} nights`).join('<br>')}<br>
      <b>Wildcards</b>: a rotating sample across the year, shown only when ≥ ${c.wildcards.min_saving_pct}% cheaper than the windows.</p></div>
    <div><h3>Sources</h3>
      <p><b>Google Flights</b> – fares of practically all airlines (Finnair, Lufthansa/SWISS/Austrian, Turkish, ANA, JAL, LOT, Qatar, Emirates, Air China …) and agencies; full schedule and layover details. Searched every night, the cheapest dates again at midday.</p>
      <p><b>Aviasales</b> (optional) – cached prices other travellers found recently; good for spotting cheap dates, but verify before booking.</p></div>
    <div><h3>About bags and prices</h3>
      <p>Prices are round-trip totals for ${c.adults} in ${esc(c.currency)} incl. taxes, as shown by the sources at the time of the check. Fares change quickly.</p>
      <p>For the cheapest itinerary of each search the scraper opens Google's booking page and takes the real checked-bag price from there (marked "Google"). Otherwise "Bag included" is based on each airline's usual policy for its cheapest economy fare to Japan (ANA, JAL, Turkish, LOT, Gulf and Chinese carriers include a bag; Lufthansa group, Finnair, KLM/Air France and BA usually sell "Light" fares without one). For the others ≈${money(c.baggage.fee_per_bag_per_direction, c.currency)} per bag and direction is added in "Fare + bag estimate". Always check the fare on the booking page.</p></div>`;
}

function renderNoData(err) {
  $('#updated').innerHTML = '<span class="dot bad"></span>no data';
  $('#hero').innerHTML = `<div><div class="eyebrow">No flight data yet</div>
    <p>The first scrape has not uploaded <code>data/latest.json</code> yet. Run the <b>Scrape flights</b> workflow on GitHub (Actions → Scrape flights → Run workflow).</p>
    <p class="hint">${esc(err?.message || '')}</p></div>`;
  for (const id of ['#windows', '#filters', '#calendar-panel', '#list-panel', '#history-panel']) $(id).hidden = true;
}

// ── settings & notifications ────────────────────────────────────────────────
function bindSettings() {
  const dlg = $('#settings');
  $('#btn-settings').addEventListener('click', () => { dlg.showModal(); refreshPushUI(); });
  dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });

  const theme = store.get('jfw.theme', 'auto');
  const setTheme = (t) => {
    store.set('jfw.theme', t);
    if (t === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    document.querySelectorAll('#s-theme button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === t)));
    if (state.data) renderHistory();
  };
  setTheme(theme);
  $('#s-theme').addEventListener('click', (e) => { const v = e.target.closest('button')?.dataset.v; if (v) setTheme(v); });
  $('#s-reset').addEventListener('click', () => {
    if (!state.data) return;
    state.f = defaults();
    saveFilters();
    renderFilters();
    update();
    toast('Filters reset');
  });

  $('#p-enable').addEventListener('click', () => withBusy('#p-enable', async () => {
    const prefs = readPrefs();
    push.savePrefs(prefs);
    const sub = await push.currentSubscription();
    if (sub) await push.syncPrefs(prefs, sub);
    else await push.enablePush(prefs);
    toast(sub ? 'Saved' : 'Notifications enabled');
    refreshPushUI();
  }));
  $('#p-test').addEventListener('click', () => withBusy('#p-test', async () => {
    await push.testPush(readPrefs());
    toast('Test notification sent');
  }));
  $('#p-disable').addEventListener('click', () => withBusy('#p-disable', async () => {
    await push.disablePush();
    toast('Notifications turned off on this device');
    refreshPushUI();
  }));
}

function readPrefs() {
  const windows = [...document.querySelectorAll('#p-windows input:checked')].map((i) => i.value);
  const alert = parseFloat($('#p-alert').value);
  return {
    ...push.loadPrefs(),
    daily: true,
    notify_time: $('#p-time').value || '08:00',
    windows: windows.length ? windows : ['may', 'oct', 'wild'],
    alert_below: Number.isFinite(alert) && alert > 0 ? alert : null,
    token: $('#p-token').value.trim(),
  };
}

async function withBusy(sel, fn) {
  const b = $(sel);
  b.disabled = true;
  try { await fn(); } catch (e) { toast(e.message || String(e)); setPushStatus(e.message || String(e), 'err'); } finally { b.disabled = false; }
}

function setPushStatus(text, cls = '') {
  const el = $('#push-status');
  el.className = `status-line ${cls}`;
  el.textContent = text;
}

async function refreshPushUI() {
  const prefs = push.loadPrefs();
  const wins = state.data ? [...cfg().windows.map((w) => [w.id, winLabel(w.id)]), ['wild', 'Wildcards']] : [['may', 'May'], ['oct', 'October'], ['wild', 'Wildcards']];
  $('#p-windows').innerHTML = wins.map(([id, l]) => `<label class="toggle"><input type="checkbox" value="${esc(id)}" ${prefs.windows.includes(id) ? 'checked' : ''}>${esc(l)}</label>`).join('');
  $('#p-time').value = prefs.notify_time;
  $('#p-alert').value = prefs.alert_below ?? '';
  $('#p-token').value = prefs.token || '';
  const sup = push.pushSupport();
  $('#ios-hint').hidden = !sup.needsInstall;
  if (!sup.supported) {
    setPushStatus(sup.needsInstall ? 'Add the app to your home screen first.' : 'This browser does not support push notifications.', 'err');
    $('#p-enable').disabled = true;
    return;
  }
  $('#p-enable').disabled = false;
  try {
    const st = await push.serverStatus();
    $('#p-token-row').hidden = !st.token_required;
    const sub = await push.currentSubscription();
    $('#p-test').hidden = !sub;
    $('#p-disable').hidden = !sub;
    $('#p-enable').textContent = sub ? 'Save settings' : 'Enable notifications';
    if (Notification.permission === 'denied') setPushStatus('Notifications are blocked in the browser settings for this site.', 'err');
    else if (sub) setPushStatus(`On for this device · daily at ${prefs.notify_time}${prefs.alert_below ? ` · alert below ${money(prefs.alert_below)}` : ''}`, 'ok');
    else setPushStatus('Off on this device.');
    if (!st.cron_configured) setPushStatus('Server: cron_key missing – notifications will not be scheduled. See README.', 'err');
  } catch {
    setPushStatus('Notification server (api/) not reachable – it runs on the all-inkl hosting, not in local previews.', 'err');
    $('#p-enable').disabled = true;
  }
}

window.addEventListener('online', () => state.data && renderStatic());
window.addEventListener('offline', () => state.data && renderStatic());
