# 从零恢复手册（RESTORE）

> 适用场景：本地电脑上的所有文件被删除、换机、硬盘损坏 —— 只剩一个 GitHub 账号。
> 本文档给出**最短且可验证**的恢复路径。全部命令已在 Windows + Git Bash 下实测。

---

## 一句话结论

```bash
git clone https://github.com/huojuda/yaoyi-jdxplorers.git
cd yaoyi-jdxplorers
python media/fetch_media.py
```

以上三条命令跑完，代码、文档、工具、48 个语音、966.8 MB 视频素材全部回到本地。
剩下的只有**密钥**需要你从 EdgeOne 控制台重新配置（密钥按设计不存仓库）。

---

## 一、恢复后你会得到什么

| 类别 | 内容 | 来源 |
|---|---|---|
| 站点代码 | `index.html`（253,803 字符 / 7,780 行）、`manifest.json`、`sw.js` | `git clone` |
| 后端 | `edge-functions/api/` 6 个边缘函数 | `git clone` |
| 语音资源 | `tts/` 48 个 mp3，6.9 MB | `git clone` |
| 文档 | `docs/` 7 篇（功能介绍、实现说明、部署手册、视频脚本、密钥治理报告…） | `git clone` |
| 工具 | `tools/` 9 个脚本（TTS 生成、规模分析、部署、同步、媒体上传…） | `git clone` |
| 配置模板 | `config/api_config.example.json`（脱敏，无密钥） | `git clone` |
| **视频 / 图片素材** | 18 个资产，**966.8 MB**（镜头 3–9、屏幕录制 2 段、参考图、素材压缩包） | `fetch_media.py` |

---

## 二、分步操作

### 第 1 步：克隆仓库

```bash
git clone https://github.com/huojuda/yaoyi-jdxplorers.git
cd yaoyi-jdxplorers
```

克隆下来**工作区文件共 8.2 MB**（84 个跟踪文件）；把 `.git` 历史算进去约 21 MB。
媒体不在这里，是有意为之（见第四节）。

**验证**：

```bash
git log --oneline -1          # 应看到最新提交
ls tts/*.mp3 | wc -l          # 应为 48
ls edge-functions/api/        # 应为 6 个 js
```

#### 关于换行符（Windows 必读）

仓库里 `index.html` 是**纯 LF**，长度 315,986 字节。仓库已用 `.gitattributes` 声明
`* text=auto eol=lf`，正常情况下检出来仍是 LF。

但在**加了 `.gitattributes` 之前的旧克隆**上，如果本机 `core.autocrlf=true`，
检出会变成 CRLF：315,986 → **323,766 字节**（多出 7,780 个 CR）。这不是文件损坏，
是 git 的本地转换。想确认仓库里到底是什么内容，绕过工作区看 blob：

```bash
git cat-file -s HEAD:index.html        # 应为 315986
git cat-file -p HEAD:index.html | md5sum
# 期望 e9067e27c17ad9e0d994371b3141972a  ← 与线上站点逐字节一致
```

CRLF 会带来四个具体后果（都已踩过）：EdgeOne 产物多传约 4 KB；
`tools/deploy_via_gitdata.py` 的换行符守卫会中止部署；
`tools/sync_local_to_remote.py` 的内容寻址对不齐远端 blob；
本机 `git push` 会撞 non-fast-forward。

若你的旧克隆已是 CRLF，转换回来：

```bash
git rm --cached -r . && git reset --hard
```

### 第 2 步：拉取媒体资产

```bash
python media/fetch_media.py
```

默认落在 `./media-files/`，可指定目录：

```bash
python media/fetch_media.py --dest D:\yaoyi-media
```

脚本行为：

- 按 `media/MANIFEST.json` 逐个下载
- 每个文件先写 `.part`，**sha256 校验通过才改名**，半截文件不会冒充成品
- 已存在且哈希正确的自动跳过 —— **重复执行是安全的**，中断后重跑即可续上
- 任一文件校验失败立即报错并中止，不静默放过

**验证**：

```bash
python media/fetch_media.py --check     # 只校验不下载
# 期望：18 个资产全部 ✅
```

单独补某类：

```bash
python media/fetch_media.py --only video     # 只下视频
python media/fetch_media.py --only image     # 只下图
```

### 第 3 步：配置密钥

密钥**不在仓库里**，这是 2026-11-08 密钥治理后的既定状态，不是遗漏。

本地开发：

```bash
export YAOWI_API_KEY=<从火山方舟控制台获取>
export YAOWI_API_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3/chat/completions
export YAOWI_MODEL_ID=doubao-seed-2-0-mini-260428
```

线上：在 EdgeOne 控制台「环境变量」里配置同名的三个变量，**改完必须重新部署才生效**。

字段结构参考 `config/api_config.example.json`（只有字段名和端点，没有密钥值）。

> 如果原 Key 已丢失：去 <https://console.volcengine.com/ark> 新建一个，替换环境变量即可。
> 模型、端点、提示词都在 `config/api_config.example.json` 里，不用重新想。

### 第 4 步：验证站点能跑

```bash
# 起本地服务器（任选其一）
python -m http.server 8080
# 浏览器打开 http://localhost:8080/index.html
```

**验收点**：

- 首页能打开，字体大、按钮大（适老化样式生效）
- 点「拍照识药」选一张药盒图 → 4 张问答卡出现
- 结果页点喇叭能出声（走的是 `tts/*.mp3` 预渲染音频，不依赖系统 TTS）

### 第 5 步（可选）：改话术后重新生成语音

这是唯一需要额外依赖的环节。`tts/*.mp3` 与 `index.html` 里的话术通过**文本指纹**绑定，
话术改了就必须重新生成音频，否则指纹不匹配会降级到系统 TTS（在无音色机型上就是没声音）。

```bash
pip install pywin32 lameenc        # Windows SAPI 合成 → WAV → MP3
python tools/gen_tts_audio.py      # 生成 48 个 mp3
python tools/write_tts_manifest.py # 回写 PRERENDERED_TTS 清单
```

⚠️ `gen_tts_audio.py` 里的 `text_hash()`（`utf-16-le` 逐码元）必须与 `index.html` 里的
`ttsTextHash()`（UTF-16 码元 + FNV-1a 32 位）**逐位一致**，否则命中不了。

---

## 三、部署更新

站点是 GitHub → EdgeOne 联动，`push` 到 `main` 自动构建，约 20 秒生效。

```bash
git push origin main
```

如果 `git push` 不通（历史上 `github.com` 被封过），用仓库内的部署脚本走 REST API：

```bash
export GITHUB_TOKEN=<有 repo 权限的 PAT>
python tools/deploy_via_gitdata.py --push        # 只推 index.html + tts/*.mp3
python tools/push_file_via_gitdata.py <文件> --push   # 推其它文件
```

**上线判据**：看响应头 `Last-Modified` 是否更新，而不是只看 HTTP 200 ——
200 分不出新旧构建。

```bash
curl -sSI https://yaoyi.site/ | grep -i last-modified
```

---

## 四、媒体为什么不放在 git 里

GitHub 对单个文件有 **100 MB 硬限制**，而 `镜头8.mp4` 是 120.9 MB，直接推会被拒。

即使压到限制内，966.8 MB 全部入库还有两个后果：

1. 每次 `git clone` 都要拉半 GB，而 99% 的情况下只需要代码（8.2 MB）
2. **一旦提交就永久留在历史里**，以后想瘦身必须重写历史，代价极高

所以采用分层：

```
代码 + 语音 + 文档 + 工具  →  git 仓库（8.2 MB）
视频 + 图片素材            →  GitHub Release  media-v1（966.8 MB）
```

Release 挂在本仓库下，地址是
`https://github.com/huojuda/yaoyi-jdxplorers/releases/tag/media-v1`，
所以**恢复来源仍然只有一个** —— 不需要第三方网盘，也不会出现"仓库在、素材丢了"的情况。

### 资产命名规则

原始文件名是中文（`镜头3.mp4`、`清晨室内长木桌场景 (1).mp4`），下载 URL 里带中文容易出转义问题，
所以 Release 里统一换成 ASCII 名，**原名与现名的对应关系完整记录在 `media/MANIFEST.json` 的
`original_path` 字段**：

| Release 资产名 | 原始路径 |
|---|---|
| `scene-08.mp4` | `video/01_AIscene/镜头8.mp4` |
| `scene-05-rev.mp4` | `video/01_AIscene/镜头5改.mp4` |
| `scene-09-alt.mp4` | `video/01_AIscene/镜9.mp4` |
| `ai-scene-all.zip` | `video/01_AIscene.zip` |

（完整 16 条见 `media/MANIFEST.json`）

---

## 五、完整性自检清单

恢复完成后逐项核对：

| # | 检查项 | 期望值 |
|---|---|---|
| 1 | `git rev-parse HEAD` | 与 GitHub 网页上的最新提交一致 |
| 2 | `ls tts/*.mp3 \| wc -l` | **48** |
| 3 | `ls edge-functions/api/` | **6** 个 js |
| 4 | `python media/fetch_media.py --check` | 18 个资产全部 ✅ |
| 5 | `media-files/` 总大小 | **966.8 MB** |
| 6 | 站点首页可打开 | 大字号界面正常渲染 |
| 7 | 结果页能出声 | 预渲染音频播放 |
| 8 | `python tools/analyze_yaoyi.py` | 输出「药品 50 / 规则 40 / mp3 48」 |

第 8 项是最强的整体校验 —— 它会从 `index.html` 里现查所有规模数据，
数字对得上就说明代码文件没损坏、没被截断。

---

## 六、仓库里**没有**什么（诚实说明）

避免误以为"克隆了就什么都有了"：

| 未入库内容 | 原因 | 丢了怎么办 |
|---|---|---|
| API 密钥（方舟 Key） | 安全设计：密钥只放 EdgeOne 环境变量 | 控制台新建，替换环境变量 |
| 参赛提交件（`创作说明*.pdf` / `.docx`、`药忆功能介绍.docx`） | 属交付成品，非工程必需 | 按 `docs/功能介绍.md` 重新排版 |
| `dist-upload/`、`dist-upload.zip` | 由源码生成的中间产物 | `python tools/build_submission_package.py` 重建 |
| `server.py`（早期 Python 后端） | 已被边缘函数完全取代，线上 404 | 不需要 |
| `药忆/` 归档目录（内含旧工作区副本） | 与工程本体重复的历史产物；其中两段屏幕录制已单独收进 Release | 录屏走 `fetch_media.py --only video`；其余不需要 |
| 浏览器缓存的 `localStorage` 用户数据 | 本来就在用户设备上 | 无法恢复，且不属项目资产 |

---

## 七、换机恢复的完整时序

```
1. 装 Git / Python
2. git clone                     → 代码 8.2 MB
3. python media/fetch_media.py   → 媒体 966.8 MB（18 个文件）
4. 配 3 个环境变量（EdgeOne 控制台 + 本地）
5. 本地 python -m http.server 起服务自测
6. 需要发布时：git push 或 tools/deploy_via_gitdata.py
```

全程不需要任何原始机器上的残留文件。
