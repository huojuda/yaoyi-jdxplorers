"""构建提交包：把作品 demo 所需文件复制到独立的打包目录（保持原目录结构）。

只读源目录，不修改任何原始文件。
"""
import shutil
from pathlib import Path

SRC = Path(r"E:/Jd/new-chat/药忆-H5原型")
DST = Path(r"E:/Jd/new-chat/药忆_提交包_20260929")

# 白名单：作品运行 + 源码 + 资源
TOP_FILES = ["index.html", "manifest.json", "sw.js", "README.md"]
TOP_DIRS = ["tts", "edge-functions"]

# 明确排除（存在则不复制，用于自检）
EXCLUDED = [
    ".git", ".gitignore", "__pycache__", "dist-upload", "dist-upload.zip",
    "server.py", "api_config.json", "models.json",
    "EdgeOne-部署执行手册.md", "EdgeOne-部署核对清单.md", "debug-edgeone-token-error.md",
    "test_family.js", "test_interactions.js", "test_recognize.py", "流程图-v2",
]

if DST.exists():
    # 只清理我们自己建的包目录
    assert DST.name == "药忆_提交包_20260929", f"意外的目标目录：{DST}"
    shutil.rmtree(DST)
DST.mkdir(parents=True)

copied = []
for f in TOP_FILES:
    s = SRC / f
    if not s.exists():
        print(f"[跳过] 源文件不存在：{f}")
        continue
    shutil.copy2(s, DST / f)
    copied.append(f)

for d in TOP_DIRS:
    s = SRC / d
    if not s.is_dir():
        print(f"[跳过] 源目录不存在：{d}")
        continue
    shutil.copytree(s, DST / d)
    copied.append(d + "/")

# ---- 自检：确认排除项没有混进来 ----
print("\n=== 自检：排除项是否意外混入 ===")
leak = []
for name in EXCLUDED:
    hits = list(DST.rglob(name))
    if hits:
        leak.append(name)
        for h in hits:
            print("  !!", h)
if not leak:
    print("  OK，无排除项混入")

# ---- 自检：敏感串扫描 ----
print("\n=== 自检：提交包内敏感串扫描 ===")
import re
PAT = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_|Bearer\s+[A-Za-z0-9_.-]{30,}|ark-[A-Za-z0-9-]{20,}", re.I)
flagged = []
for p in DST.rglob("*"):
    if p.is_file() and p.suffix.lower() in (".html", ".js", ".json", ".md", ".py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if PAT.search(txt):
            flagged.append(str(p.relative_to(DST)))
print("  命中：", flagged if flagged else "无")

# ---- 输出清单 ----
print("\n=== 提交包清单 ===")
total = 0
rows = []
for p in sorted(DST.rglob("*")):
    if p.is_file():
        size = p.stat().st_size
        total += size
        rows.append((str(p.relative_to(DST)).replace("\\", "/"), size))

for rel, size in rows:
    if rel.startswith("tts/"):
        continue
    print(f"  {rel}  ({size:,} bytes)")
tts_files = [r for r in rows if r[0].startswith("tts/")]
if tts_files:
    tts_size = sum(s for _, s in tts_files)
    print(f"  tts/  —— {len(tts_files)} 个 mp3 文件  ({tts_size:,} bytes)")

print(f"\n文件总数：{len(rows)}")
print(f"总体积：{total:,} bytes = {total/1024/1024:.2f} MB")
print(f"目标目录：{DST}")
