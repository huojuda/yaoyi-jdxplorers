# EdgeOne Pages 部署执行手册（药忆 v1.4.0）

> 生成时间：2026-09-22
> 适用项目：`E:\Jd\new-chat\药忆-H5原型`
> 线上地址：https://yaoyi.site

---

## 〇、先说明一件事

**这份手册里的上传动作需要你在浏览器控制台里完成，我无法代劳。**
原因：我没有你 EdgeOne 账号的登录态，也没有 API Token，无法访问控制台或调用部署接口。

你面前有两条路：

| 路径 | 你需要做的 | 我需要的 |
|---|---|---|
| **A. 控制台手动上传（推荐，最稳）** | 照本手册第四节操作，把 `dist-upload/` 拖上去 | 无 |
| **B. CLI 部署（我帮你跑）** | 去 EdgeOne 控制台生成一个 API Token 给我 | Token（有权限范围，用完可撤销） |

路径 A 大约 5 分钟。路径 B 更快但要交出 Token，你自行权衡。

**我已经把上传包准备好了**：`药忆-H5原型\dist-upload\`，
只含该上传的 9 个文件，已逐个字节比对确认与源文件一致，不会误传本地脚本和文档。

---

## 一、需要上传的内容与目录结构

### 1.1 上传包目录结构（照原样保持层级，不要拍平）

```
dist-upload/
├── index.html                                  ← 根目录，243,835 B
├── manifest.json                               ← 根目录，1,124 B
├── sw.js                                       ← 根目录，4,901 B
└── edge-functions/
    └── api/
        ├── auth.js                             ← 19,586 B
        ├── recognize.js                        ←  6,631 B
        ├── ask.js                              ← 13,256 B
        ├── relation.js                         ←  8,348 B
        ├── events.js                           ←  6,391 B
        └── pair.js                             ←  3,929 B
```

### 1.2 每个文件的作用

| 文件 | 作用 | 本次是否有改动 |
|---|---|---|
| `index.html` | 整个前端单文件应用（CSS/JS 全内嵌） | ✅ **有**（移除验证码 UI） |
| `manifest.json` | PWA 清单，图标用内联 data URI，**无额外图片资源** | 无 |
| `sw.js` | Service Worker，`CACHE_NAME = 'yaoyi-v3.0.0'` | ✅ **有**（v2.1.0 → v3.0.0） |
| `edge-functions/api/auth.js` | 注册/登录/会话/重置密码 | ✅ **有**（移除短信逻辑） |
| `edge-functions/api/recognize.js` | 拍照识药（调火山方舟） | 无 |
| `edge-functions/api/ask.js` | AI 问答 + 联合用药深度分析 + 防重复话术 | ✅ **有**（v1.4.0 新增） |
| `edge-functions/api/relation.js` | 家属关系绑定查询 | 无 |
| `edge-functions/api/events.js` | 服药/求助事件上报与轮询 | 无 |
| `edge-functions/api/pair.js` | 8 位绑定码生成与校验 | 无 |

### 1.3 ⚠️ 绝对不要上传的文件

`dist-upload/` 里已经排除了这些，如果你改用手动挑文件的方式，请注意：

| 文件 | 不能上传的原因 |
|---|---|
| `server.py` | 本地开发用的后端镜像，与边缘函数职责重复 |
| `api_config.json` | **含火山方舟 API Key**（在上级目录） |
| `test_*.js` / `test_*.py` | 测试脚本，非部署产物 |
| `__pycache__/` | Python 缓存 |
| `debug-edgeone-token-error.md` | 内部调试记录 |
| `药忆-创作说明.docx` | 参赛材料，与部署无关 |
| `README.md` / `EdgeOne-部署核对清单.md` | 文档，可选上传（不推荐，会暴露内部信息） |

---

## 二、边缘函数运行环境与触发方式

### 2.1 运行环境

| 项目 | 说明 |
|---|---|
| **运行时** | EdgeOne Pages Edge Functions，运行在 **V8 隔离环境**（非 Node.js） |
| **可用能力** | Web 标准 API：`fetch`、`crypto.subtle`（WebCrypto）、`TextEncoder/Decoder`、`btoa/atob`、`URL`、`Response`、`Request` |
| **不可用** | `require`、`process`、`fs`、`Buffer` 等 Node API；**不支持 npm 依赖** |
| **依赖情况** | ✅ 本项目 6 个边缘函数**零外部依赖**，各自独立自包含，互相不 import |
| **网络出口** | 可主动发起 HTTPS 请求（用于调 Upstash REST 与火山方舟） |
| **请求体上限** | **1 MB**（前端已做图片压缩 ≤700KB 兜底） |
| **执行模型** | Serverless，无需管理服务器，自动弹性伸缩 |

> 因为我们用的是 WebCrypto 而非 Node crypto，所以**不需要任何构建步骤**，
> `.js` 文件直接上传即可运行。

### 2.2 处理函数约定

每个文件必须默认导出一个 `onRequest(context)` 函数，返回 `Response`：

```js
export default function onRequest(context) {
  const { request, env } = context;   // context 还含 params / uuid / waitUntil
  return new Response('...', { status: 200, headers: {...} });
}
```

`context.env` 就是**环境变量的读取入口**，第三节的变量都从这里取。

### 2.3 路由触发方式（按目录结构自动生成，无需配置）

EdgeOne Pages 会扫描 `edge-functions/` 目录，**按文件路径自动生成路由**，无需任何路由配置文件。

| 文件路径 | 触发的 URL 路由 | 方法 |
|---|---|---|
| `edge-functions/api/auth.js` | `/api/auth` | POST / GET / OPTIONS |
| `edge-functions/api/recognize.js` | `/api/recognize` | POST / OPTIONS |
| `edge-functions/api/ask.js` | `/api/ask` | POST / OPTIONS |
| `edge-functions/api/relation.js` | `/api/relation` | GET / POST / OPTIONS |
| `edge-functions/api/events.js` | `/api/events` | GET（轮询）/ POST（上报）/ OPTIONS |
| `edge-functions/api/pair.js` | `/api/pair` | POST / GET / OPTIONS |

各接口的入参约定：

| 路由 | 入参 |
|---|---|
| `/api/auth` | `action` = `register` \| `login` \| `resetpw` \| `delete` \| `diag` \| `me` \| `logout`，配 `phone` / `password` / `role` / `displayName` |
| `/api/recognize` | `image`（base64 data URL） |
| `/api/ask` | `question`；或 `mode=analyze`（联合用药深度分析）、`mode=duplicate`（防重复话术） |
| `/api/relation` | `action` + `code` / `userId` 等 |
| `/api/events` | `action=acknowledge` 或 `drugId` 上报 |
| `/api/pair` | `action` = `create` \| `verify` |

> ⚠️ **两条关键规则**（来自官方文档）：
> 1. **路由大小写敏感** —— `/API/auth` 不会命中 `api/auth.js`
> 2. **静态资源优先** —— 若某路径同时存在静态文件和边缘函数，请求会走静态文件。
>    我们的函数都在 `/api/*` 下，与静态文件无冲突，安全。

---

## 三、所需配置与依赖

### 3.1 环境变量（在 EdgeOne Pages 控制台配置）

| 变量名 | 必需性 | 用途 | 值来源 |
|---|---|---|---|
| `UPSTASH_REDIS_REST_URL` | **必需** | 账号/会话/事件/绑定码存储 | Upstash 控制台 |
| `UPSTASH_REDIS_REST_TOKEN` | **必需** | 同上 | Upstash 控制台 |
| `YAOWI_API_KEY` | **必需** | 拍照识药 + AI 问答 | 火山方舟控制台 |
| `YAOWI_API_ENDPOINT` | 可选 | 默认北京区 | 留空即用默认 |
| `YAOWI_MODEL_ID` | 可选 | 默认 `doubao-seed-2-0-mini-260428` | 留空即用默认 |

**哪个函数依赖哪些变量：**

| 函数 | Upstash | 火山方舟 |
|---|---|---|
| `auth.js` | ✅ 必需 | — |
| `recognize.js` | 可选（有则记识别历史） | ✅ 必需 |
| `ask.js` | — | ✅ 必需 |
| `relation.js` | ✅ 必需 | — |
| `events.js` | ✅ 必需 | — |
| `pair.js` | ✅ 必需 | — |

> 📌 **短信相关的 `SMS_*` 变量已全部废弃**，不要再配置（该方案因个人主体无法申请短信签名已放弃）。

### 3.2 外部服务依赖

| 服务 | 端点 | 说明 |
|---|---|---|
| Upstash Redis（REST） | 由 `UPSTASH_REDIS_REST_URL` 指定 | 非腾讯云，第三方，免费额度够用 |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3/chat/completions` | OpenAI 兼容格式 |

### 3.3 部署前检查

- [ ] 5 个环境变量已在控制台配好（注意 `YAOWI_API_KEY` 别填错，这是最容易出错的一项）
- [ ] `dist-upload/` 里的 `sw.js` 确认是 `yaoyi-v3.0.0`
- [ ] 本地 `git status` 干净（当前为 `ea28789`）
- [ ] 确认没有把 `api_config.json` 放进上传包

---

## 四、完整部署步骤

### 路径 A · 控制台手动上传

**第 1 步 · 进入项目**
1. 打开 EdgeOne Pages 控制台，找到 `yaoyi.site` 对应的项目
2. 进入 **部署 / Deployments** 页

**第 2 步 · 确认环境变量已配**
1. 左侧 **设置 / Settings → 环境变量 / Environment Variables**
2. 按第 3.1 节逐条核对
3. ⚠️ **变量的改动通常需要重新部署才生效**，所以先把变量配好再上传文件

**第 3 步 · 上传**
1. 选择 **直接上传 / Upload** 方式
2. 把 `E:\Jd\new-chat\药忆-H5原型\dist-upload` 整个文件夹拖进去
   （或者先压缩成 zip 再上传，保持内部目录层级不变）
3. 确认上传列表里能看到那 9 个文件，且 `edge-functions/api/` 层级正确
4. 点 **部署**

**第 4 步 · 等待构建**
- 构建过程会扫描 `edge-functions/` 并自动注册 6 条路由
- 通常 1 分钟内完成，控制台会显示部署成功

### 路径 B · CLI 部署（你给我 Token 后我来跑）

```bash
npm install -g edgeone
cd "E:/Jd/new-chat/药忆-H5原型"
edgeone pages deploy dist-upload --name <你的项目名> -t <TOKEN> -e production
```

---

## 五、部署后验证方式

### 5.1 静态文件是否更新

```bash
# 应显示今天的日期，不再是 2026-09-11
curl -sSI https://yaoyi.site/ | grep -i last-modified

# 应为 243835（本地大小）
curl -sSI https://yaoyi.site/ | grep -i content-length

# sw.js 版本应为 yaoyi-v3.0.0（旧版是 v2.1.0）
curl -s https://yaoyi.site/sw.js | grep CACHE_NAME

# manifest 应为合法 JSON
curl -s https://yaoyi.site/manifest.json | head -3
```

### 5.2 边缘函数是否真的跑起来（关键验证）

**这一步能区分"函数已注册"和"回退到了静态文件"**——如果函数没生效，
请求会落到静态资源，返回 HTML 而不是 JSON。

```bash
# ① 函数存活探针：应返回 JSON 的"未知操作"错误，而不是 HTML
curl -s -X POST https://yaoyi.site/api/auth \
  -H "Content-Type: application/json" -d '{}'
# 预期：{"error":"未知操作，应为 register/login/resetpw/delete/diag/me/logout"}

# ② Redis 是否配好：若变量没生效会返回"服务器未配置 Upstash Redis"
curl -s -X POST https://yaoyi.site/api/auth \
  -H "Content-Type: application/json" \
  -d '{"action":"diag","phone":"13800138000"}'
# 预期：JSON 响应（非 500 未配置错误）

# ③ 火山方舟 Key 是否配好：缺 Key 会返回"服务器未配置 API Key"
curl -s -X POST https://yaoyi.site/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"阿司匹林饭前还是饭后吃"}'
# 预期：返回带 AI 回答的 JSON，且末尾含"以上为AI参考，具体请遵医嘱"
```

**判断口诀**：只要返回的是 **JSON**，说明边缘函数已经接管了 `/api/*`；返回 **HTML** 就说明没生效。

### 5.3 真机端到端验证（浏览器做不到的部分）

用手机浏览器打开 https://yaoyi.site，逐项过：

| # | 验证项 | 通过标准 |
|---|---|---|
| 1 | 拍照识药 | 后置摄像头、相册两个入口都能出结果 |
| 2 | 四问结果页 | 吃几次/什么时间/饭前饭后/禁忌 四项都有，语音 0.8 倍速可播可停 |
| 3 | 防重复 | 同一种药连拍两次 → 第二次红色拦截 + AI 话术 |
| 4 | 联合用药 | 识别 ≥2 种药 → 规则速查 + 大模型深度分析都出现 |
| 5 | 账号 | 注册 / 登录 / **重置密码（手机号+新密码）** / 注销 全通 |
| 6 | 家属绑定 | 患者 8 位码 → 家属绑定成功 → 服药事件 3 秒内推送 |
| 7 | 一键求助 | 按下后家属端 3 秒内弹窗 |
| 8 | PWA | 可添加到主屏幕；飞行模式下刷新仍能打开外壳 |
| 9 | SW 更新 | 在线刷新后拿到 v3.0.0（DevTools → Application → Cache Storage 看缓存名） |

> ⚠️ **第 9 项要注意**：线上 sw.js 从 v2.1.0 跨到了 v3.0.0，
> 老用户第一次访问会触发缓存重建。验证时如果发现还是旧页面，
> 让浏览器彻底关闭再打开，或清一次站点数据。

---

## 六、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `/api/*` 返回 HTML 而不是 JSON | 边缘函数没上传成功，或目录层级拍平了 | 检查上传包里 `edge-functions/api/xxx.js` 层级是否正确 |
| 返回"服务器未配置 Upstash Redis" | 环境变量没配，或配了但没重新部署 | 补配变量后**重新部署一次** |
| 返回"服务器未配置 API Key" | `YAOWI_API_KEY` 缺失 | 补配并重新部署 |
| 页面还是老版本 | SW 缓存 | 关掉浏览器重开；或 DevTools → Application → Clear storage |
| 拍照按钮无反应 | 非 HTTPS 或非 localhost | 确认走 https://yaoyi.site |
| 重置密码后登不进 | 旧密码错误计数残留 | 已在重置成功时清理 `login:fail:*`，若仍异常联系我 |
| 部署成功但路由 404 | 文件名大小写不符 | 路由大小写敏感，必须严格一致 |

---

## 七、当前状态（部署前）

| 阶段 | 状态 |
|---|---|
| 阶段 0 代码硬门槛 | ✅ 完成 |
| **阶段 1 静态文件上传** | ❌ **待执行**（线上仍 v1.3.0） |
| **阶段 2 边缘函数路由** | ❌ **待执行** |
| 阶段 3 环境变量 | ❓ 待控制台核对 |
| 阶段 4 短信网关 | ⛔ 已放弃（改为手机号+新密码） |
| 阶段 5 域名与 HTTPS | ✅ 已完成 |
| 阶段 6 上线后自测 | ⏸ 依赖阶段 1、2 完成后执行 |

**线上现状（2026-09-22 实测）**：`yaoyi.site` 返回 200，证书正常，
但三个静态文件 Last-Modified 均为 `2026-09-11 15:13 GMT`，`sw.js` 仍是 `yaoyi-v2.1.0`
→ 说明**本次 v1.4.0 从未上线过**，这是最优先要补的。
