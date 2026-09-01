// sw.js — 药忆 Service Worker
// 缓存策略：
//   - 导航请求 → 缓存优先（应用外壳秒开）+ 离线降级到 index.html
//   - 静态资源 → 缓存优先 + 网络回填
//   - POST /api/recognize → 网络优先，永不缓存（每次图片不同）
// 版本更新：改 CACHE_NAME 版本号即可自动清理旧缓存

const CACHE_NAME = 'yaoyi-v1.0.0';

// 应用外壳：单文件 SPA，CSS/JS 已内嵌进 index.html
const APP_SHELL = [
  './',
  './index.html',
  './manifest.json'
];

// ============ install：预缓存应用外壳 ============
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      // addAll 原子性失败时逐个降级，避免单个资源缺失导致整体安装失败
      try {
        await cache.addAll(APP_SHELL);
      } catch (err) {
        console.warn('[SW] 部分预缓存失败，逐个重试:', err.message);
        await Promise.allSettled(APP_SHELL.map(url => cache.add(url)));
      }
      // 立即激活，让 bugfix 快速触达老人设备
      await self.skipWaiting();
    })()
  );
});

// ============ activate：清理旧版本缓存 ============
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      // 删除所有不等于当前 CACHE_NAME 的缓存（含历史版本）
      await Promise.all(
        keys.map(key => (key !== CACHE_NAME ? caches.delete(key) : null))
      );
      // 立即接管已打开的页面
      await self.clients.claim();
    })()
  );
});

// ============ fetch：按请求类型分流到不同缓存策略 ============
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 仅处理同源请求，跨域资源交给浏览器默认行为
  if (url.origin !== self.location.origin) return;

  // ---- 策略 A：POST /api/recognize → 网络优先，永不缓存 ----
  // 每次拍的照片不同，识别结果必须实时联网；POST body 也无法被 Cache API 缓存
  if (req.method === 'POST' && url.pathname === '/api/recognize') {
    event.respondWith(handleApiRecognize(req));
    return;
  }

  // 非 GET 请求直接放行
  if (req.method !== 'GET') return;

  // ---- 策略 B：导航请求 → 缓存优先 + 离线降级到 index.html ----
  if (req.mode === 'navigate') {
    event.respondWith(handleNavigation(req));
    return;
  }

  // ---- 策略 C：静态资源 → 缓存优先 + 网络回填 ----
  event.respondWith(handleStaticAsset(req));
});

// POST /api/recognize：纯网络，失败时返回结构化离线错误
async function handleApiRecognize(req) {
  try {
    return await fetch(req);
  } catch (err) {
    // 老人断网拍照时，返回 JSON 让前端弹"请连接网络后重试"
    return new Response(
      JSON.stringify({
        error: '网络不可用，药品识别需要联网，请检查网络后重试',
        offline: true
      }),
      {
        status: 503,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store'
        }
      }
    );
  }
}

// 导航请求：缓存优先 → 网络 → 离线降级到 index.html
async function handleNavigation(req) {
  const cache = await caches.open(CACHE_NAME);
  // 1. 命中缓存直接返回（应用外壳秒开）
  const cached = await cache.match(req, { ignoreSearch: true });
  if (cached) return cached;
  // 2. 缓存未命中，走网络并回填缓存
  try {
    const res = await fetch(req);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    // 3. 网络失败 → 降级到已缓存的 index.html
    const shell = await cache.match('./index.html') || await cache.match('./');
    if (shell) return shell;
    return new Response(
      '<!DOCTYPE html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<h2 style="font-family:sans-serif;text-align:center;margin-top:40px">当前处于离线状态</h2>' +
      '<p style="font-family:sans-serif;text-align:center;color:#666">请连接网络后重新打开应用</p>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}

// 静态资源：缓存优先 → 网络（回填）→ 缓存回退
async function handleStaticAsset(req) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const res = await fetch(req);
    if (res && res.status === 200 && res.type === 'basic') {
      cache.put(req, res.clone());
    }
    return res;
  } catch (err) {
    const fallback = await cache.match(req);
    if (fallback) return fallback;
    throw err;
  }
}
