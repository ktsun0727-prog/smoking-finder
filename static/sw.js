// 앱 설치(PWA)용 서비스 워커.
// 화면 파일은 '네트워크 먼저' — 서버가 켜져 있으면 항상 최신 화면을 받고, 연결이 끊겼을 때만 저장본을 보여 준다.
// API(/api, /auth)는 저장하지 않는다(로그인·데이터가 오래된 채로 남지 않게).
const CACHE = "smoking-finder-v2";
const SHELL = ["/", "/manifest.webmanifest", "/icon.svg", "/privacy", "/terms"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then(r => r || caches.match("/")))
  );
});
