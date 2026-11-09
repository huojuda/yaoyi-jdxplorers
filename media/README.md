# media/ —— 媒体资源清单与下载器

这个目录**不放媒体本体**，只放"媒体在哪里、怎么取、怎么校验"。

## 文件

| 文件 | 作用 |
|---|---|
| `MANIFEST.json` | 16 个媒体资产的清单：ASCII 资产名、原始路径、大小、sha256、下载地址 |
| `fetch_media.py` | 按清单批量下载 + 逐字节校验 |
| `README.md` | 本文件 |

## 媒体本体在哪

GitHub Release **`media-v1`**：

```
https://github.com/huojuda/yaoyi-jdxplorers/releases/tag/media-v1
```

## 怎么取

```bash
python media/fetch_media.py                 # 全部，落到 ./media-files/
python media/fetch_media.py --dest D:\media # 指定目录
python media/fetch_media.py --only video    # 只下视频
python media/fetch_media.py --check         # 只校验已下载的
```

脚本特性：

- **断点续跑**：已存在且 sha256 正确的跳过，重复执行安全
- **防半截**：先写 `.part`，校验通过才改名
- **强校验**：任一文件哈希不符立即报错中止
- **零依赖**：只用 Python 标准库

## 为什么不放 git

| 事实 | 数值 |
|---|---|
| GitHub 单文件硬限制 | 100 MB |
| 最大单个素材 `镜头8.mp4` | 120.9 MB → **直接被拒** |
| 全部素材合计 | 552.8 MB |
| 代码仓库本身 | 7.5 MB |

把 552.8 MB 塞进 git 的代价：每次 clone 都要拉半 GB；一旦提交就永久留在历史里，
想瘦身必须重写历史。

所以：**代码入 git，媒体入 Release，清单入 git**。Release 挂在同一个仓库下，
恢复来源仍然只有一个。

## 资产命名

Release 里用 ASCII 名（`scene-08.mp4`），原始中文路径记录在 `MANIFEST.json` 的
`original_path` 字段里。例如：

```json
{
  "asset": "scene-08.mp4",
  "original_path": "video/01_AIscene/镜头8.mp4",
  "size": 126789965,
  "sha256": "76889b5c2f29630d...",
  "url": "https://github.com/huojuda/yaoyi-jdxplorers/releases/download/media-v1/scene-08.mp4"
}
```

## 增补新素材

1. 在 GitHub 上给 `media-v1` Release 追加资产（或用新 tag `media-v2`）
2. 重新生成清单里的 `size` / `sha256` / `url` 三个字段
3. 提交 `MANIFEST.json`

`fetch_media.py` 不需要改 —— 它完全按清单驱动。

## 关于 `ai-scene-all.zip`

这个压缩包与 `video/01_AIscene/` 目录**不是互相包含的关系**：

- zip 里有 `镜头10.mp4`，磁盘目录里没有
- 磁盘目录里有 `镜头5改.mp4`，zip 里没有

所以两者都保留，**不能当重复项删掉其中一个**。
