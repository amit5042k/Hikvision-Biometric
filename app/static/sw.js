// Service worker for the installable attendance app (version __VERSION__).
// Pages are always fetched from the server (attendance data is never stored on
// the phone); only the app's look (CSS, icons) and an offline page are cached.
const CACHE = 'attendance-shell-__VERSION__';
const SHELL = ['/offline', '/static/style.css', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
  if (req.mode === 'navigate') {
    // network only; show the offline page when there is no connection
    event.respondWith(fetch(req).catch(() => caches.match('/offline')));
    return;
  }
  if (new URL(req.url).pathname.startsWith('/static/')) {
    // static files: cached copy first, refreshed in the background
    event.respondWith(caches.open(CACHE).then(cache => cache.match(req).then(hit => {
      const fresh = fetch(req).then(resp => { if (resp.ok) cache.put(req, resp.clone()); return resp; }).catch(() => hit);
      return hit || fresh;
    })));
  }
});
