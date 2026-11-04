/* Option Intelligence service worker (v__VERSION__).
   - Lets the app install to the home screen and open instantly.
   - Caches ONLY static files. Prices, signals, accounts and WebSockets always go to the network, so nothing stale is ever
     shown as live data.
   - If the network is down, page loads show a friendly offline screen. */
const VERSION = "__VERSION__";
const CACHE = "oi-shell-" + VERSION;
const SHELL_PAGES = new Set(["/", "/dashboard", "/signals"]);
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
    // v1.18.8: the three main pages open INSTANTLY from the saved copy (even while the server is waking up) and are refreshed
    // in the background for next time. A redirect (signed out) is never saved, and removes the saved copy.
    if (SHELL_PAGES.has(p)) {
      e.respondWith(caches.open(CACHE).then(async c => {
        const hit = await c.match(p);
        const update = fetch(req).then(res => {
          if (res.ok && !res.redirected) c.put(p, res.clone()); else if (res.redirected || res.status === 401) c.delete(p);
          return res;
        });
        if (hit) { e.waitUntil(update.catch(() => { })); return hit; }
        return update.catch(() => caches.match("/static/offline.html"));
      }));
      return;
    }
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
  const ev = (data.data||{}).event, options = { body: data.body || "A confirmed signal is available.", icon: "/static/icons/icon-192.png", badge: "/static/icons/icon-192.png", tag: "oi-" + ((data.data||{}).symbol || "signal") + "-" + ((data.data||{}).side || "confirmed") + "-" + (ev || ""), renotify: true, vibrate: [220, 100, 220], timestamp: Date.now(), requireInteraction: ev === "CONFIRMED", data: { url: data.url || "/", ...(data.data||{}) } };
  event.waitUntil(self.registration.showNotification(title, options));
});
self.addEventListener("notificationclick", event => {
  event.notification.close(); const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(clients.matchAll({type:"window", includeUncontrolled:true}).then(list => { for (const c of list) { if (c.url.startsWith(self.location.origin) && "focus" in c) { c.navigate(target); return c.focus(); } } return clients.openWindow ? clients.openWindow(target) : undefined; }));
});

// v1.18.6: if the browser rotates the push subscription, re-register it so background alerts keep arriving.
self.addEventListener("pushsubscriptionchange", event => {
  event.waitUntil((async () => {
    try {
      const cfg = await (await fetch("/api/push/config")).json();
      if (!cfg.vapid_public_key) return;
      const pad = "=".repeat((4 - cfg.vapid_public_key.length % 4) % 4);
      const key = Uint8Array.from(atob((cfg.vapid_public_key + pad).replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
      const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await fetch("/api/push/web/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subscription: sub.toJSON() }) });
    } catch (_) {}
  })());
});
