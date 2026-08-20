const CACHE = "anagrind-59d68c6eb13f";
const ASSETS = ["./", "./index.html", "./manifest.webmanifest",
                "./icon-180.png", "./icon-192.png", "./icon-512.png",
                "./icon-maskable.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.match(event.request, {ignoreSearch: true})
      .then(hit => hit || fetch(event.request).catch(err => {
        // Offline, and the URL is not one we precached. Every navigation here
        // is the same single page, so serve it rather than the browser's
        // offline error. Subresources keep failing, which is what they mean.
        if (event.request.mode === "navigate") return caches.match("./");
        throw err;
      }))
  );
});
