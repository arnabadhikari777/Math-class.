const CACHE_NAME = 'math-class-v6';

// Only truly static, non-login-dependent assets are safe to precache.
// '/' and '/download' were removed on purpose: those pages render
// differently depending on whether the student is logged in, so caching
// them meant the app could keep showing a stale "Login" page forever,
// even after a successful login.
const PRECACHE = [
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// --- Real push notifications ---
// Fired by the browser when the server sends a push message (via VAPID),
// even if this site/app isn't open. Shows a system notification on the
// student's phone/laptop.
self.addEventListener('push', (event) => {
  let data = { title: 'Arnab Math Class', body: 'You have a new update.', url: '/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }
  const options = {
    body: data.body,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { url: data.url || '/' },
    vibrate: [100, 50, 100]
  };
  event.waitUntil(self.registration.showNotification(data.title, options));
});

// Tapping the notification opens the relevant page (or focuses an
// already-open tab instead of opening a duplicate one).
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  const isStaticAsset = url.pathname.startsWith('/static/');

  if (isStaticAsset) {
    // Static files never change per-student, so cache-first is fine and fast.
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const networked = fetch(event.request).then((response) => {
          if (response && response.status === 200 && response.type === 'basic') {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => cached);
        return cached || networked;
      })
    );
    return;
  }

  // Every other page (/, /login, /student/dashboard, /take-test, etc.) depends
  // on the student's login/session state, so it must always be requested
  // fresh from the server first. Cache is only a fallback for when the
  // student has no internet connection at all.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
