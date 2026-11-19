// POPCORE's service worker: it only shows web order notifications (push_notifications.py) and opens 网站订单 when one is
// tapped. It never caches pages or files, so every visit still loads the newest POPCORE.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()))

self.addEventListener('push', event => {
  let message = {}
  try { message = event.data ? event.data.json() : {} } catch { message = {} }
  event.waitUntil(self.registration.showNotification(message.title || 'POPCORE', {
    body: message.body || '',
    tag: message.tag,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: message.url || '/web-orders' },
  }))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const url = new URL(event.notification.data?.url || '/web-orders', self.location.origin).href
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const open = windows.find(client => new URL(client.url).origin === self.location.origin)
    if (!open) return self.clients.openWindow(url)
    // A window this worker does not control yet cannot be sent elsewhere; it is brought forward as it is.
    return open.focus().then(client => client.navigate(url).catch(() => client))
  }))
})
