/* Option Intelligence service worker (v__VERSION__).
   - Lets the app install to the home screen and open instantly.
   - Caches ONLY static files. Prices, signals, accounts and WebSockets always go to the network, so nothing stale is ever
     shown as live data.
   - If the network is down, page loads show a friendly offline screen. */
const VERSION = "__VERSION__";
const CACHE = "oi-shell-" + VERSION;
const PRECACHE = ["/static/offline.html", "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/manifest.webmanifest"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("oi-shell-") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  const p = url.pathname;
  if (p.startsWith("/api/") || p.startsWith("/ws/") || p.startsWith("/auth/") || p === "/sw.js") return;   // always live
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (p.startsWith("/static/")) {
    // Versioned files (?v=1.x.y) never change for a given URL: cache first. Anything else: network first, cache as backup.
    if (url.searchParams.has("v")) {
      e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
        return res;
      })));
    } else {
      e.respondWith(fetch(req).then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
        return res;
      }).catch(() => caches.match(req)));
    }
  }
});

// Background Web Push: works while the installed PWA/browser tab is closed (HTTPS required).
self.addEventListener("push", event => {
  let data = {}; try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = data.title || "Option Intelligence";
  const options = { body: data.body || "A confirmed signal is available.", icon: "/static/icons/icon-192.png", badge: "/static/icons/icon-192.png", tag: "oi-" + ((data.data||{}).symbol || "signal") + "-" + ((data.data||{}).side || "confirmed"), renotify: true, data: { url: data.url || "/", ...(data.data||{}) } };
  event.waitUntil(self.registration.showNotification(title, options));
});
self.addEventListener("notificationclick", event => {
  event.notification.close(); const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(clients.matchAll({type:"window", includeUncontrolled:true}).then(list => { for (const c of list) { if (c.url.startsWith(self.location.origin) && "focus" in c) { c.navigate(target); return c.focus(); } } return clients.openWindow ? clients.openWindow(target) : undefined; }));
});
