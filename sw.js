// Service Worker — cache offline de áudio e shell
const AUDIO_CACHE = 'music-audio-v2';
const SHELL_CACHE = 'music-shell-v2';
const SHELL_FILES = ['/', '/index.html', '/manifest.json'];

// ---------- Instalação ----------
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL_FILES)).catch(() => {})
  );
  self.skipWaiting();
});

// ---------- Ativação ----------
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== AUDIO_CACHE && k !== SHELL_CACHE)
          .map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// ---------- Fetch ----------
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Áudio: cache-first
  if (url.pathname.startsWith('/audio/')) {
    event.respondWith(handleAudio(event.request, url));
    return;
  }

  // API: network-first, sem cache
  if (url.pathname.startsWith('/api/')) {
    return; // deixa o navegador lidar normalmente
  }

  // Shell: cache-first com fallback
  if (event.request.method === 'GET') {
    event.respondWith(handleShell(event.request));
  }
});

async function handleAudio(request, url) {
  const cache = await caches.open(AUDIO_CACHE);
  const cacheKey = url.pathname;

  // Verifica se já está no cache
  const cached = await cache.match(cacheKey);
  if (cached) {
    // Atualiza o cabeçalho de status pra UI
    notifyClients({ type: 'offline-play', path: cacheKey });
    return cached;
  }

  // Não está: baixa completo, guarda e devolve
  try {
    const response = await fetch(cacheKey);
    if (response.ok) {
      const clone = response.clone();
      await cache.put(cacheKey, clone);
      notifyClients({ type: 'cached', path: cacheKey });
    }
    return response;
  } catch (e) {
    // Offline e não tem no cache: retorna erro
    notifyClients({ type: 'offline-miss', path: cacheKey });
    return new Response('offline e nao cacheado', { status: 503 });
  }
}

async function handleShell(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  if (cached) {
    // Atualiza em background
    fetch(request).then(r => { if (r.ok) cache.put(request, r.clone()); }).catch(() => {});
    return cached;
  }
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (e) {
    return new Response('offline', { status: 503 });
  }
}

function notifyClients(msg) {
  self.clients.matchAll({ includeUncontrolled: true }).then(clients => {
    clients.forEach(c => c.postMessage(msg));
  });
}

// ---------- Mensagens do cliente ----------
self.addEventListener('message', async event => {
  const data = event.data || {};

  // Força download de um áudio para o cache
  if (data.type === 'prefetch' && data.videoId) {
    const url = '/audio/' + data.videoId;
    const cache = await caches.open(AUDIO_CACHE);
    const existing = await cache.match(url);
    if (existing) {
      event.source.postMessage({ type: 'prefetch-done', videoId: data.videoId, ok: true, already: true });
      return;
    }
    try {
      const res = await fetch(url);
      if (res.ok) {
        await cache.put(url, res.clone());
        event.source.postMessage({ type: 'prefetch-done', videoId: data.videoId, ok: true });
      } else {
        event.source.postMessage({ type: 'prefetch-done', videoId: data.videoId, ok: false });
      }
    } catch (e) {
      event.source.postMessage({ type: 'prefetch-done', videoId: data.videoId, ok: false });
    }
    return;
  }

  // Remove um áudio do cache
  if (data.type === 'uncache' && data.videoId) {
    const cache = await caches.open(AUDIO_CACHE);
    await cache.delete('/audio/' + data.videoId);
    event.source.postMessage({ type: 'uncache-done', videoId: data.videoId });
    return;
  }

  // Lista o que está cacheado
  if (data.type === 'list-cached') {
    const cache = await caches.open(AUDIO_CACHE);
    const keys = await cache.keys();
    const ids = keys.map(k => k.url.split('/audio/')[1]).filter(Boolean);
    event.source.postMessage({ type: 'list-cached-done', ids });
    return;
  }

  // Limpa todo o cache de áudio
  if (data.type === 'clear-cache') {
    await caches.delete(AUDIO_CACHE);
    event.source.postMessage({ type: 'clear-cache-done' });
    return;
  }
});
