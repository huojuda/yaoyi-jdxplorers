# 药忆 — AI 拍照用药助手（H5）

> JDXplorers 参赛作品 · 面向阿尔茨海默症患者的 AI 拍照用药助手
> 版本 v1.1.7 · 已上线腾讯 EdgeOne Pages

---

## 项目简介

药忆是一款专为轻中度阿尔茨海默症（AD）患者设计的 AI 拍照用药助手。患者只需用手机对准药盒拍一张照片，系统自动识别药品并以大字体 + 慢速语音回答四个最关键的问题：一天吃几次、什么时间吃、饭前还是饭后、有什么禁忌。同时具备防重复服药检测、药物冲突预警和家属远程守护功能。

**核心差异化**：AD 患者的核心障碍不是"看不清"而是"记不住"，因此防重复服药确认和家属远程监护是本项目的灵魂功能。

---

## 技术架构

### 部署平台

- **线上**：腾讯 EdgeOne Pages（静态托管 + Edge Functions 边缘计算）
- **数据存储**：Upstash Redis（REST API，边缘函数直接访问）
- **AI 模型**：火山方舟 doubao-seed-2-0-mini-260428（多模态大模型，药盒识别）
- **本地开发**：Python `server.py`（ThreadingHTTPServer，端口 8080），镜像线上全部 API

### 系统拓扑

```
┌─────────────────────────────────────────────────────────┐
│                    用户浏览器（H5 SPA）                     │
│  index.html（单文件应用，CSS/JS 内嵌）                     │
│  · 拍照 / 相册选择  · 图片压缩（≤700KB）                  │
│  · 四问式结果展示  · 语音播报（0.8x 慢速）                │
│  · 用药记录（localStorage）· 药物冲突检测（18 条规则）     │
│  · 账号登录/注册  · 家属绑定码  · 实时事件轮询（3s）       │
└──────────────┬──────────────────────────┬─────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│   EdgeOne Pages 静态托管   │  │     Edge Functions（5 个）     │
│   index.html / manifest   │  │  /api/auth      账号系统       │
│   /sw.js（PWA，当前禁用）  │  │  /api/recognize 药盒 AI 识别   │
│                          │  │  /api/relation  家属绑定       │
│                          │  │  /api/events    事件上报/轮询   │
│                          │  │  /api/pair      配对码（兼容）   │
└──────────────────────────┘  └──────────┬───────────────────┘
                                           │
                              ┌────────────┴────────────┐
                              ▼                         ▼
                    ┌─────────────────┐     ┌─────────────────────┐
                    │  Upstash Redis  │     │  火山方舟多模态 API   │
                    │  · 用户/会话/绑定 │     │  doubao-seed-2-0-mini│
                    │  · 事件流/确认标记 │     │  药盒照片 → 药品 JSON  │
                    │  · 识别分片临时存储 │     └─────────────────────┘
                    └─────────────────┘
```

---

## 目录结构

```
药忆-H5原型/
├── index.html                  # 单文件 SPA（前端全部逻辑，约 3600 行）
├── manifest.json               # PWA 应用清单
├── sw.js                       # Service Worker（当前已禁用注册，保留文件）
├── server.py                   # 本地开发服务器（镜像全部 Edge Function API）
├── edge-functions/
│   └── api/
│       ├── auth.js             # 账号系统：注册/登录/重置/注销/会话/诊断
│       ├── recognize.js        # 药盒识别：调用火山方舟，支持 POST 直传 + GET 分片
│       ├── relation.js         # 家属绑定：6 位绑定码 gen/bind/list/unbind
│       ├── events.js           # 事件流：服药/求助/监视结束上报 + 家属轮询聚合
│       └── pair.js             # 配对码（旧版兼容，create/verify）
├── .gitignore
└── README.md                   # 本文件
```

> **API 密钥管理**：`api_config.json` 位于项目上级目录，仅 `server.py` 本地开发时读取；线上 EdgeOne Pages 通过环境变量 `YAOWI_API_KEY` / `YAOWI_API_ENDPOINT` / `YAOWI_MODEL_ID` 注入，前端代码中不含任何密钥。

---

## 已实现功能

### 1. 拍照识药（AI 识别，非演示模式）

- 调用手机后置摄像头（`getUserMedia`），实时取景 + 对准引导
- 摄像头不可用时自动降级为"从相册选择照片"
- **图片智能压缩**：先缩放到 1280px，再逐步降质量（0.8→0.4）和分辨率（×0.75），确保 base64 ≤ 700KB（EdgeOne 边缘函数 1MB 请求体上限）
- **双通道识别**：
  - 通道 A：POST 直传 `{image: dataURL}`
  - 通道 B：当 EdgeOne 预览链接丢弃大体积 POST body 时，自动切换为 GET 分片上传（每片 5000 字符，经 Redis 临时存储，最后一片触发组装识别）
- 调用火山方舟 `doubao-seed-2-0-mini` 多模态模型，返回结构化 JSON（药品名、商品名、规格、频次、时间、餐前餐后、禁忌、置信度）
- 识别结果经"精确→包含→商品名"三级匹配内置药品数据库，从源头抑制大模型幻觉
- 数据库未收录的药品显示友好提示，不编造用药信息

### 2. 四问式结果页

- 药品名称 + 通俗分类 + 商品名 + 置信度
- **一天吃几次？**（大字体答案 + 详细剂量）
- **什么时间吃？**
- **饭前还是饭后？**
- **有什么禁忌？**（橙色警告卡片）
- 所有专业术语自动翻译为大白话（如"胆碱酯酶抑制剂"→"帮助改善记忆力的药"）

### 3. 语音播报

- 识别完成后自动语音播报完整用药指导
- 0.8 倍慢速（适老化），中文语音自动选择
- 重复服药场景优先播报警告
- 播报中可随时停止，按钮变红 + 脉冲动画

### 4. 防重复服药（核心差异化功能）

- 每次确认服药后记录到 localStorage（含日期、时间、药品 ID）
- 再次识别同一种药时，自动检测今天是否已服用
- 如已服用，显示红色震动警告："您今天 X 点已经吃过这个药了"
- 重复时隐藏"确认已服用"按钮，防止误操作
- 语音同步警告

### 5. 药物冲突预警

- 内置 **18 条**药物相互作用规则（严重 4 条 / 警告 9 条 / 注意 5 条）
- 支持双向片段匹配 + 酒精标记
- 红橙黄三级预警，每条冲突均附老人版口语化解释
- 识别结果页自动检查与当天已用药的冲突
- 历史记录页展示全部已检测到的冲突

### 6. 账号系统

- 手机号 + 密码注册/登录，角色选择（患者 / 家人）
- **安全设计**：
  - PBKDF2-SHA256 加盐哈希（10000 次迭代，WebCrypto 原生实现）
  - 会话 token 32 字节随机，TTL 7 天，滑动续期
  - 登录失败 5 次锁定 10 分钟
  - userId 由服务端生成，永不信任前端
  - 数据按 userId 命名空间隔离
- 支持密码重置、账号注销（校验密码后删除全部数据）
- 僵尸账号自动修复机制（数据损坏时用本次输入重建）
- 注册即登录，登录态持久化到 localStorage

### 7. 跨设备家属绑定

- **6 位绑定码模式**（双方在线即可，无需同时扫码）：
  - 患者端生成绑定码（120 秒有效，倒计时显示）
  - 家属端输入绑定码完成关联
- 双向关联存储（Redis Set），每人最多关联 10 人
- 角色校验：仅家人账号可发起绑定，对方必须是患者账号
- 支持解绑、关联列表查看
- 绑定后家属可远程查看该患者的用药日报和实时事件

### 8. 家属实时守护

- 家属端每 **3 秒**轮询所有关联患者的事件流
- 事件类型：
  - `medication`：患者确认服药 → 家属收到通知，进入 30 分钟"监视期"
  - `alert`：患者按下求助按钮 → 家属端弹出紧急弹窗 + 震动 + 系统通知
  - `monitor_end`：30 分钟监视期自然结束 → 状态恢复正常
  - `acknowledge`：家属确认已处理求助 → 弹窗关闭
- 患者端服药后显示 30 分钟监视浮窗，含一键求助按钮
- 家属端可一键拨打患者电话（`tel:` 链接）
- 用药日报：服药统计、漏服检测、冲突总览、按时间倒序的服药时间线，支持按日回溯

### 9. 用药记录

- 按日期分组展示历史用药记录（localStorage 持久化）
- 显示药品名、通俗分类、服用时间
- 今日记录高亮
- 支持酒精摄入标记（用于冲突检测）

### 10. PWA 支持

- `manifest.json` 定义应用名称、图标、主题色、全屏模式
- `sw.js` 实现应用外壳缓存 + 离线降级（API 请求永不缓存）
- **当前状态**：Service Worker 注册已临时禁用（确保每次加载最新代码），功能稳定后恢复
- 支持"添加到主屏幕"作为独立应用运行

---

## 内置药品数据库（23 种）

| 类别 | 药品 |
|---|---|
| **AD 核心用药** | 盐酸多奈哌齐（安理申）、盐酸美金刚（易倍申）、重酒石酸卡巴拉汀（艾斯能）、甘露特钠胶囊（九期一） |
| **降压药** | 苯磺酸氨氯地平（络活喜）、缬沙坦、美托洛尔、硝苯地平、厄贝沙坦 |
| **降糖药** | 盐酸二甲双胍（格华止）、格列美脲、格列齐特 |
| **降脂药** | 阿托伐他汀钙（立普妥）、瑞舒伐他汀 |
| **抗凝/抗血小板** | 阿司匹林肠溶片（拜阿司匹灵）、氯吡格雷、华法林 |
| **胃药** | 奥美拉唑肠溶胶囊（洛赛克）、多潘立酮 |
| **止痛药** | 布洛芬缓释胶囊（芬必得）、对乙酰氨基酚（扑热息痛） |
| **睡眠药** | 佐匹克隆、右佐匹克隆 |

> 每种药品包含：通用名、商品名（多个）、药物分类、通俗分类、服用频次、服用时间、餐前餐后关系、详细剂量、禁忌、禁忌详情、副作用、老人版口语化解释。

---

## API 接口说明

所有接口均部署为 EdgeOne Pages Edge Functions，路径为 `/api/*`。普通请求默认走 GET（参数拼 URL，绕开 EdgeOne 预览链接 POST body 丢失问题），仅图片识别走 POST。

### `/api/auth` — 账号系统

| action | 方法 | 说明 |
|---|---|---|
| `register` | GET | 注册（phone, password, role, displayName），注册即登录 |
| `login` | GET | 登录（phone, password），失败 5 次锁定 10 分钟 |
| `resetpw` | GET | 重置密码（phone, password），原型版无短信验证 |
| `delete` | GET | 注销账号（phone, password），删除全部数据不可恢复 |
| `me` | GET | 会话验证，返回当前用户信息，滑动续期 |
| `logout` | GET | 登出，销毁当前会话 |
| `diag` | GET | 账号诊断（仅返回长度/存在性，不泄露盐值哈希） |

### `/api/recognize` — 药盒识别

| 模式 | 方法 | 说明 |
|---|---|---|
| 直传 | POST | `{image: dataURL}`，直接调用火山方舟 |
| 分片 | GET | `?up=1&sid=..&i=..&n=..&d=..`，逐片存 Redis，最后一片组装识别 |

返回：`{drug_name, brand_name, specification, frequency, timing, meal_relation, contraindication, confidence}`

### `/api/relation` — 家属绑定

| action | 方法 | 说明 |
|---|---|---|
| `gen` | GET | 患者生成 6 位绑定码（120 秒有效） |
| `bind` | GET | 家属输入绑定码完成关联（code） |
| `list` | GET | 查询当前用户的所有关联 |
| `unbind` | GET | 解绑（targetUserId） |
| `pending` | GET | 待确认请求（当前简化版返回空） |

### `/api/events` — 事件流

| 模式 | 方法 | 说明 |
|---|---|---|
| 上报 | GET/POST | 患者端上报事件（type: medication/alert/monitor_end） |
| 求助确认 | GET/POST | 家属端确认求助（action=acknowledge, targetPatientId, ts） |
| 轮询 | GET | 家属端聚合所有关联患者的事件（after=时间戳），返回 events + acknowledged + patientPhones |

### `/api/pair` — 配对码（旧版兼容）

| action | 方法 | 说明 |
|---|---|---|
| `create` | GET | 生成 6 位配对码（300 秒有效），返回 familyId |
| `verify` | GET | 验证配对码（code, phone），绑定联系电话 |

---

## Redis 数据结构

| Key | 类型 | 说明 |
|---|---|---|
| `user:{userId}` | Hash | 用户数据（phone, passHash, salt, role, displayName, createdAt） |
| `user:phone:{phone}` | String | 手机号 → userId 映射 |
| `session:{token}` | String | 会话 token → userId，TTL 7 天 |
| `login:fail:{phone}` | String | 登录失败计数，TTL 10 分钟 |
| `relation:{userId}` | Set | 关联的对方 userId 集合（双向维护） |
| `bindCode:{6位码}` | String | 绑定码 → JSON {userId, role, createdAt}，TTL 120 秒 |
| `user:{patientId}:events` | List | 患者事件流（最多保留 200 条） |
| `user:{patientId}:ack:{ts}` | String | 求助确认标记 |
| `pair:code:{code}` | String | 旧版配对码 → familyId，TTL 300 秒 |
| `family:{familyId}:phone` | String | 旧版家庭联系电话 |
| `recq:{sid}:{idx}` | String | 识别分片临时存储，TTL 180 秒 |

---

## 本地开发

### 前置条件

- Python 3.8+
- Upstash Redis 账号（获取 REST URL 和 Token）
- 火山方舟 API Key（获取方式见下文）

### 启动步骤

1. **配置 API Key**：在项目上级目录创建 `api_config.json`（已存在则跳过）：

```json
{
  "api_endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
  "api_key": "你的火山方舟API Key",
  "primary_model": { "id": "doubao-seed-2-0-mini-260428" },
  "recognition_prompt": "..."
}
```

2. **配置 Upstash Redis**（环境变量）：

```bash
# Windows PowerShell
$env:UPSTASH_REDIS_REST_URL="https://your-upstash-url.upstash.io"
$env:UPSTASH_REDIS_REST_TOKEN="your-upstash-token"

# 或在启动前设置
```

3. **启动服务器**：

```bash
cd 药忆-H5原型
python server.py
```

4. **打开浏览器**：访问 `http://localhost:8080`

> 未配置 Upstash Redis 时，配对/事件/账号功能不可用，但拍照识别和本地用药记录仍可使用（识别 API 需配置 api_config.json）。

### 本地服务器与线上 Edge Functions 的对应关系

`server.py` 完整镜像了 5 个 Edge Function 的全部接口逻辑，包括：
- 账号系统（PBKDF2 哈希、会话、防爆破、僵尸账号修复）
- 药盒识别（调用火山方舟，POST 直传）
- 家属绑定（绑定码 gen/bind/list/unbind）
- 事件流（上报/轮询/求助确认）
- 配对码（create/verify）

区别仅在于：线上用 WebCrypto + Upstash REST，本地用 hashlib + urllib，接口行为完全一致。

---

## 部署到 EdgeOne Pages

### 环境变量

在 EdgeOne Pages 控制台配置以下环境变量：

| 变量名 | 说明 |
|---|---|
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis REST Token |
| `YAOWI_API_KEY` | 火山方舟 API Key |
| `YAOWI_API_ENDPOINT` | 火山方舟 API 端点（可选，默认北京区） |
| `YAOWI_MODEL_ID` | 模型 ID（可选，默认 doubao-seed-2-0-mini-260428） |

### 部署注意事项

- Edge Functions 有 **1MB 请求体上限**，前端已做图片压缩（≤700KB）和分片上传兜底
- 预览链接有 **3 小时 token 有效期**，前端已实现 token 持久化（localStorage）和过期自动清理
- API 响应已设置 `Cache-Control: no-store`，防止 CDN 缓存导致身份状态异常
- `vercel.json` 等 Vercel 配置已移除，本项目不依赖 Vercel

---

## 适老化设计规范

| 设计元素 | 参数 |
|---|---|
| 正文字号 | 18px |
| 标题字号 | 28-36px |
| 四问式答案字号 | 22px |
| 按钮高度 | ≥56px，主按钮内边距 20px |
| 语音播报语速 | 0.8 倍速 |
| 配色 | 绿色主调（健康/安全），橙色警告，红色危险 |
| 操作路径 | 拍照→结果 ≤ 2 次点击 |
| 页面缩放 | 禁止用户缩放（避免误操作） |
| 绑定码字号 | 48px，字间距 12px |
| 求助按钮 | 常驻浮窗，红色高亮，一键触发 |

---

## 浏览器兼容性

| 浏览器 | 拍照 | 语音播报 | PWA | 备注 |
|---|---|---|---|---|
| Chrome（安卓） | ✅ | ✅ | ✅ | 推荐 |
| Edge | ✅ | ✅ | ✅ | 推荐 |
| Safari（iOS） | ✅ | ✅ | ✅ | 需 HTTPS 或 localhost |
| 微信内置浏览器 | ⚠️ | ✅ | ❌ | 摄像头可能受限 |
| Firefox | ✅ | ⚠️ | ⚠️ | 语音支持有限 |

> `getUserMedia` 需要 HTTPS 环境或 localhost。直接用 `file://` 协议打开时摄像头不可用，会自动降级为相册选择。

---

## 版本历史

| 版本 | 说明 |
|---|---|
| v1.0.0 | 初始版本：拍照识药、四问式结果、语音播报、防重复服药 |
| v1.0.4 | 家属实时守护：设备配对 + 服药通知 + 30 分钟监视期 + 一键求助 |
| v1.1.0 | 迁移到账号关联体系：事件挂在患者 userId 下，家属通过 relation 读取 |
| v1.1.1 | 注册时自动修复损坏账号 |
| v1.1.2 | 登录修复不再持久化默认角色，注册作为身份修正通道 |
| v1.1.3 | 禁用 CDN 缓存 API 响应（修复身份不持久化问题） |
| v1.1.4 | 修复 Upstash HGETALL 扁平数组转对象（修复用户数据读不出问题） |
| v1.1.5 | 修复绑定码弹窗缺少 .show 类（弹窗不可见问题） |
| v1.1.6 | 修复绑定码弹窗被静态 #pair-modal 共享 .alert-overlay 类阻塞 |
| v1.1.7 | 修复 relation getUser 双重转换导致角色检查失败 |

---

## 免责声明

本项目为参赛演示作品，药品信息仅供参考，不构成医疗建议。实际用药请遵医嘱。账号系统为原型实现，密码重置未接入短信验证，生产环境需补充。
