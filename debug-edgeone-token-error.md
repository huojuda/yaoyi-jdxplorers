# Debug Session: edgeone-token-error

## Symptoms
- User sees `SyntaxError: Unexpected token '<'` on API calls
- URL bar shows bare domain without `?eo_token=` params

## Root Cause
**H1 + H2 双成立**：
1. 用户刷新/重开页面丢失了 URL 里的 `?eo_token=`，EdgeOne 对裸域名返回 401 HTML
2. 旧代码 `resp.json()` 尝试解析 HTML → SyntaxError，未捕获 HTTP 非 200
3. token 有效期仅 3 小时，过期后即使在 URL 里也没用

## Fix Applied (commit c78f5b0)
1. **Token 持久化**：页面加载时从 URL 提取 eo_token/eo_time 存 localStorage
2. **双重回退**：`edgeTokenQS()` 优先 URL，其次 localStorage，刷新不丢
3. **stale 清理**：API 收到 UNAUTHORIZED 时自动清 localStorage 里的旧 token
4. **缺失提示横幅**：EdgeOne 域名上无 token → 顶部红色横幅提示获取新预览链接
5. **错误消息可读**：401 直接返回中文提示"访问链接已过期"而非 SyntaxError
6. **Service Worker 版本升级**：`yaoyi-v2.0.0` → `yaoyi-v2.1.0` 强制清缓存

## Status
[CLOSED] - Fix deployed, waiting user verification
