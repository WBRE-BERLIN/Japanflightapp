// Formatting + small helpers
const LOCALE = 'en-GB';
const moneyFmt = new Map();

export function money(v, cur = 'EUR') {
  if (v == null || Number.isNaN(v)) return '–';
  if (!moneyFmt.has(cur)) {
    moneyFmt.set(cur, new Intl.NumberFormat(LOCALE, { style: 'currency', currency: cur, maximumFractionDigits: 0 }));
  }
  return moneyFmt.get(cur).format(v);
}

export function moneyShort(v) {
  if (v == null) return '–';
  return v >= 10000 ? `${Math.round(v / 1000)}k` : v >= 1000 ? `${(v / 1000).toFixed(2).replace(/0$/, '')}k` : String(Math.round(v));
}

const dFmt = new Intl.DateTimeFormat(LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
const dFmtShort = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short' });
const dtFmt = new Intl.DateTimeFormat(LOCALE, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

export const asDate = (iso) => new Date(`${iso.slice(0, 10)}T12:00:00`);
export const day = (iso) => dFmt.format(asDate(iso));
export const dayShort = (iso) => dFmtShort.format(asDate(iso));
export const stamp = (iso) => (iso ? dtFmt.format(new Date(iso)) : '–');
export function span(a, b) {
  const da = asDate(a);
  const db = asDate(b);
  if (da.getMonth() === db.getMonth()) return `${da.getDate()}–${dFmtShort.format(db)}`;
  return `${dFmtShort.format(da)}–${dFmtShort.format(db)}`;
}
export const hm = (iso) => (iso ? iso.slice(11, 16) : '–');

export function dur(min) {
  if (min == null) return '–';
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h}h ${String(m).padStart(2, '0')}m` : `${h}h`;
}

export function ago(iso) {
  if (!iso) return '';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function dayDiff(aIso, bIso) {
  if (!aIso || !bIso) return 0;
  return Math.round((asDate(bIso) - asDate(aIso)) / 86400000);
}

export function addDays(iso, n) {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

export function safeUrl(u) {
  try {
    const url = new URL(u, location.href);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : '#';
  } catch {
    return '#';
  }
}

export const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode etc. */
    }
  },
};

let toastTimer;
export function toast(msg) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    el.setAttribute('role', 'status');
    document.body.append(el);
  }
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3800);
}
