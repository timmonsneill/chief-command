// Chief Command service worker — minimal stub for PWA installability.
// No caching strategy yet; just registers so the browser treats the app as a PWA.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
