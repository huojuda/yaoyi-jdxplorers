"""扫描 index.html，产出功能清单所需的统计数据。只读，不改文件。"""
import re
import json
from pathlib import Path

P = Path(r"E:/Jd/new-chat/药忆-H5原型/index.html")
s = P.read_text(encoding="utf-8")
print("字符数:", len(s))
print("行数:", s.count("\n") + 1)

# ---------- 板块切分 ----------
def block(start_pat, end_pat):
    i = s.find(start_pat)
    if i < 0:
        return ""
    j = s.find(end_pat, i + len(start_pat))
    return s[i:j + len(end_pat)]

drug_block = block("const DRUG_DB = {", "\n};")
rules_block = block("const INTERACTION_RULES = [", "\n];")

# ---------- 药品库 ----------
entries = re.findall(
    r'id:\s*"([a-z0-9_]+)",\s*\n\s*name:\s*"([^"]+)"[\s\S]{0,400}?drug_class:\s*"([^"]*)"',
    drug_block,
)
print("\n=== 药品库 ===")
print("条目数:", len(entries))
by_class = {}
for did, name, cls in entries:
    by_class.setdefault(cls, []).append(name)
for cls, names in by_class.items():
    print(f"  [{cls}] ({len(names)}) {'、'.join(names)}")

ids = [e[0] for e in entries]

# ---------- 冲突规则 ----------
sevs = re.findall(r'severity:\s*"(\w+)"', rules_block)
print("\n=== 冲突规则 ===")
print("总条数:", len(re.findall(r'\{ id: "INT', rules_block)))
for k in ["danger", "warning", "caution"]:
    print(f"  {k}: {sevs.count(k)}")
types = set(re.findall(r'type:\s*"([^"]+)"', rules_block))
print("  涉及类型数:", len(types))

# ---------- 预渲染语音 ----------
tts_dir = Path(r"E:/Jd/new-chat/药忆-H5原型/tts")
tts_ids = sorted(p.stem.replace("tts_", "") for p in tts_dir.glob("*.mp3"))
print("\n=== 预渲染语音 ===")
print("mp3 数:", len(tts_ids))
print("有音频的药品:", "、".join(tts_ids))
print("无音频的药品:", "、".join(sorted(set(ids) - set(tts_ids))))

# ---------- 方言 ----------
m = re.search(r"function setDialect\(dialect\)\s*\{[\s\S]{0,600}?\n\}", s)
print("\n=== setDialect ===")
print(m.group(0)[:600] if m else "未找到")

# ---------- 音频清单常量 ----------
m = re.search(r"PRERENDERED_TTS\s*=\s*\{", s)
print("\n=== PRERENDERED_TTS 清单 ===")
print(m.group(0) if m else "未找到")

# ---------- 接口调用 ----------
print("\n=== 前端调用的接口 ===")
print(sorted(set(re.findall(r"api(?:Get|Post)\(\s*['\"](/api/[a-z]+)", s))))

# ---------- 本地存储键 ----------
print("\n=== 本地存储 base key ===")
m = re.search(r"USER_DATA_BASE_KEYS\s*=\s*\[([^\]]*)\]", s)
print(m.group(1).strip() if m else "未找到")
m = re.search(r"USER_DATA_PREFIXES\s*=\s*\[([^\]]*)\]", s)
print(m.group(1).strip() if m else "未找到")

# ---------- 监视 / 轮询常量 ----------
print("\n=== 关键常量 ===")
for pat in [r"MONITOR_DURATION\s*=\s*[^;]+", r"POLL[A-Z_]*\s*=\s*[^;]+",
            r"CACHE_NAME[^\n]*", r"SPEAK_RETRY_DELAYS\s*=\s*[^;]+"]:
    for mm in re.findall(pat, s):
        print(" ", mm.strip())
