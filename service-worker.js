const CACHE_NAME = 'quran-reciter-v12';
const ASSETS_TO_CACHE = [
    '/',
    'https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Scheherazade+New:wght@400;700&family=Noto+Naskh+Arabic:wght@400;700&family=Cairo:wght@400;700&family=Lateef:wght@400;700&family=Rakkas&display=swap'
];

// Install Event: Cache App Shell
self.addEventListener('install', (event) => {
    console.log('[SW] Installing Service Worker...');
    event.waitUntil(
        caches.open(CACHE_NAME).then(async (cache) => {
            console.log('[SW] Caching App Shell');
            for (const asset of ASSETS_TO_CACHE) {
                try {
                    await cache.add(asset);
                    console.log(`[SW] Cached: ${asset}`);
                } catch (err) {
                    console.error(`[SW] Failed to cache: ${asset}`, err);
                }
            }
        }).then(() => {
            console.log('[SW] Install Complete');
            return self.skipWaiting();
        })
    );
});

// Activate Event: Clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[SW] Activating Service Worker...');
    const cacheWhitelist = [CACHE_NAME, 'audio-cache', 'api-cache'];
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (!cacheWhitelist.includes(cacheName)) {
                        console.log('[SW] Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch Event: Intercept requests
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // 0. Ignore External API (Bypass SW entirely)
    if (url.href.includes('api.alquran.cloud')) {
        return;
    }

    // 0b. Bypass critical static data (always fetch fresh to prevent stale cache issues)
    if (url.pathname.includes('surah-data.js')) {
        return;
    }

    // 1. Ignore POST requests (cannot be cached)
    if (event.request.method === 'POST') {
        return;
    }

    // 1. Handle Audio Files AND JSON files (Cache First, then Network)
    if (url.pathname.endsWith('.mp3') || url.pathname.endsWith('.json') || url.pathname.includes('/cache/')) {
        console.log('[SW] Intercepting:', url.pathname);
        event.respondWith(
            caches.open('audio-cache').then((cache) => {
                return cache.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        console.log('[SW] Found in cache:', url.pathname);
                        return cachedResponse;
                    }

                    console.log('[SW] Not in cache. Fetching full file...', url.pathname);

                    // IMPORTANT: Audio players send "Range" headers (e.g. bytes=0-).
                    // The Cache API CANNOT store 206 Partial Content responses.
                    // We must fetch the FULL file (status 200) to cache it.

                    // Create a new request without the Range header
                    const newHeaders = new Headers(event.request.headers);
                    newHeaders.delete('range');
                    // Use same-origin URL to avoid CORS issues
                    const fetchUrl = new URL(url.pathname, self.location.origin).href;
                    const newRequest = new Request(fetchUrl, {
                        method: event.request.method,
                        headers: newHeaders,
                        mode: 'cors',
                        credentials: 'same-origin'
                    });

                    return fetch(newRequest).then((networkResponse) => {
                        console.log('[SW] Network response status:', networkResponse.status, 'for', url.pathname);
                        // Only cache valid 200 responses
                        if (networkResponse.status === 200) {
                            console.log('[SW] Caching file:', url.pathname);
                            // Use the original URL as the cache key
                            const cacheUrl = new URL(url.pathname, self.location.origin).href;
                            cache.put(cacheUrl, networkResponse.clone())
                                .then(() => console.log('[SW] Cache put success for:', url.pathname))
                                .catch(err => console.error('[SW] Cache put failed:', err));
                        } else {
                            console.log('[SW] Not caching - status was:', networkResponse.status);
                        }
                        return networkResponse;
                    }).catch(err => {
                        console.error('[SW] Fetch failed:', err);
                        throw err;
                    });
                });
            })
        );
        return;
    }

    // 2. Handle API Requests (Network First, then Cache)
    if (url.pathname === '/history' || url.pathname.startsWith('/recitation/')) {
        event.respondWith(
            caches.open('api-cache').then((cache) => {
                return fetch(event.request).then((networkResponse) => {
                    // Clone and cache the response
                    if (networkResponse.status === 200) {
                        cache.put(event.request, networkResponse.clone());
                    }
                    return networkResponse;
                }).catch(() => {
                    // Network failed, try cache
                    return cache.match(event.request);
                });
            })
        );
        return;
    }

    // 3. Default: Stale-While-Revalidate for other assets
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            const fetchPromise = fetch(event.request).then((networkResponse) => {
                // Check if valid response before caching
                if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== 'basic') {
                    return networkResponse;
                }

                const responseToCache = networkResponse.clone();

                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, responseToCache);
                });
                return networkResponse;
            });
            return cachedResponse || fetchPromise;
        })
    );
});
