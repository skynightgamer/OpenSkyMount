// OpenSkyMount service worker: makes the app installable and lets the UI shell open without a network.
// It never caches the API or the WebSocket: mount state and commands always go to the server live, and
// without a connection the app says so (it cannot control anything offline).
const CACHE = "osm-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icon.svg"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws") return; // always live
  if (url.pathname.startsWith("/assets/")) {
    // Hashed build files never change: cache first.
    event.respondWith(
      caches.match(event.request).then(
        (hit) =>
          hit ||
          fetch(event.request).then((res) => {
            if (res.ok) caches.open(CACHE).then((c) => c.put(event.request, res.clone()));
            return res;
          }),
      ),
    );
    return;
  }
  // Everything else (the page itself): network first, cached copy when offline.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && event.request.mode === "navigate") caches.open(CACHE).then((c) => c.put("/", res.clone()));
        return res;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/"))),
  );
});
