/* Service worker: offline shell, fresh data, push notifications. */
const VERSION = '__BUILD__';
const SHELL = `jfw-shell-${VERSION}`;
const DATA = 'jfw-data';
const ASSETS = [
  './', 'index.html', 'css/app.css', 'js/app.js', 'js/util.js', 'js/charts.js', 'js/push.js',
  'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png', 'icons/badge-96.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('jfw-shell-') && k !== SHELL) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.includes('/api/')) return; // always network
  if (url.pathname.includes('/data/')) {
    // network first, fall back to the last copy when offline
    e.respondWith((async () => {
      try {
        const res = await fetch(e.request, { cache: 'no-store' });
        if (res.ok) (await caches.open(DATA)).put(url.pathname, res.clone());
        return res;
      } catch {
        const hit = await caches.match(url.pathname, { cacheName: DATA });
        return hit || new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } });
      }
    })());
    return;
  }
  // app shell: cache first, refresh in background
  e.respondWith((async () => {
    const cache = await caches.open(SHELL);
    const hit = await cache.match(e.request, { ignoreSearch: url.pathname.endsWith('/') || url.pathname.endsWith('index.html') });
    const net = fetch(e.request).then((res) => {
      if (res.ok) cache.put(e.request, res.clone());
      return res;
    }).catch(() => null);
    return hit || (await net) || cache.match('index.html');
  })());
});

self.addEventListener('push', (e) => {
  let msg = { title: 'Japan Flight Watch', body: 'New flight prices are available.' };
  try { if (e.data) msg = { ...msg, ...e.data.json() }; } catch { if (e.data) msg.body = e.data.text(); }
  e.waitUntil(self.registration.showNotification(msg.title, {
    body: msg.body,
    icon: 'icons/icon-192.png',
    badge: 'icons/badge-96.png',
    tag: msg.tag || 'jfw',
    renotify: msg.tag === 'alert',
    data: { url: msg.url || './' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || './', self.registration.scope).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if (w.url.startsWith(self.registration.scope)) {
        await w.focus();
        return w.navigate(target);
      }
    }
    return self.clients.openWindow(target);
  })());
});
