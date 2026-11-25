// Web Push registration against the PHP backend in /api
import { store } from './util.js';

const API = 'api/';

export function pushSupport() {
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  return { supported, isIOS, standalone, needsInstall: isIOS && !standalone };
}

export async function serverStatus() {
  const r = await fetch(`${API}status.php`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`server status ${r.status}`);
  return r.json();
}

async function post(path, body, token) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-App-Token': token } : {}) },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok || data.ok === false) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
  return data;
}

function keyBytes(b64u) {
  const pad = '='.repeat((4 - (b64u.length % 4)) % 4);
  const raw = atob((b64u + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function currentSubscription() {
  if (!('serviceWorker' in navigator)) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export function loadPrefs() {
  return {
    daily: true,
    notify_time: '08:00',
    windows: ['may', 'wild'],
    alert_below: null,
    token: '',
    ...store.get('jfw.push', {}),
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin',
  };
}

export function savePrefs(p) {
  store.set('jfw.push', p);
}

export async function enablePush(prefs) {
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('Notifications were not allowed in the browser.');
  const st = await serverStatus();
  if (!st.vapid_public_key) throw new Error(st.push_error || 'Server has no push key');
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub) {
    // re-subscribe if the server key changed
    const cur = sub.options?.applicationServerKey;
    if (cur && btoa(String.fromCharCode(...new Uint8Array(cur))) !== btoa(String.fromCharCode(...keyBytes(st.vapid_public_key)))) {
      await sub.unsubscribe();
      sub = null;
    }
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(st.vapid_public_key) });
  }
  await syncPrefs(prefs, sub);
  return sub;
}

export async function syncPrefs(prefs, sub = null) {
  sub = sub || (await currentSubscription());
  if (!sub) return null;
  const { token, ...rest } = prefs;
  return post('subscribe.php', { subscription: sub.toJSON(), prefs: rest }, token);
}

export async function disablePush() {
  const sub = await currentSubscription();
  if (!sub) return;
  try { await post('unsubscribe.php', { endpoint: sub.endpoint }); } catch { /* server may be gone */ }
  await sub.unsubscribe();
}

export async function testPush(prefs) {
  const sub = await currentSubscription();
  if (!sub) throw new Error('Notifications are not enabled on this device.');
  return post('test.php', { endpoint: sub.endpoint }, prefs.token);
}
