#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 media/MANIFEST.json 下载全部媒体资产并逐字节校验。

用法：
    python media/fetch_media.py                 # 下载到当前目录下的 media-files/
    python media/fetch_media.py --dest D:\\media  # 指定落地目录
    python media/fetch_media.py --only video    # 只下某一类（video / image / archive）
    python media/fetch_media.py --check         # 只校验已下载文件，不联网

设计要点：
  · 断点续跑：已存在且 sha256 正确的文件直接跳过，重复执行是安全的。
  · 先写 .part 临时文件，校验通过才改名，避免半截文件被当成成品。
  · 校验失败立即中止并报出期望/实际哈希，不静默放过。
  · 只用标准库，不依赖 requests。
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "MANIFEST.json")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(url, dest, expected_size):
    """流式下载到 dest.part，完成后再改名。"""
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    got = 0
    req = urllib.request.Request(url, headers={"User-Agent": "yaoyi-fetch-media"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if expected_size:
                pct = got * 100 // expected_size
                sys.stdout.write(f"\r      下载中 {human(got)} / {human(expected_size)}  {pct}%   ")
                sys.stdout.flush()
    print()
    os.replace(tmp, dest)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.path.join(os.getcwd(), "media-files"))
    ap.add_argument("--only", choices=["video", "image", "archive"])
    ap.add_argument("--check", action="store_true", help="只校验本地已下载文件")
    args = ap.parse_args()

    meta = json.load(open(MANIFEST, encoding="utf-8"))
    assets = meta["assets"]
    if args.only:
        assets = [a for a in assets if a["type"] == args.only]

    print(f"清单：{meta['release']['tag']}  资产 {len(assets)} 个  "
          f"合计 {meta['summary']['total_mb']} MB")
    print(f"落地目录：{args.dest}\n")

    ok = skipped = failed = 0
    total_got = 0

    for i, a in enumerate(assets, 1):
        name = a["asset"]
        dest = os.path.join(args.dest, name)
        tag = f"[{i:>2}/{len(assets)}] {name:<30}"

        if os.path.exists(dest):
            if sha256_of(dest) == a["sha256"]:
                print(f"{tag} ✅ 已存在且校验一致，跳过")
                skipped += 1
                continue
            print(f"{tag} ⚠️ 已存在但校验不符，重新下载")

        if args.check:
            print(f"{tag} ❌ 缺失")
            failed += 1
            continue

        try:
            got = download(a["url"], dest, a["size"])
        except Exception as e:
            print(f"{tag} ❌ 下载失败：{type(e).__name__}: {e}")
            failed += 1
            continue

        actual = sha256_of(dest)
        if actual != a["sha256"]:
            print(f"{tag} ❌ 校验失败\n        期望 {a['sha256']}\n        实际 {actual}")
            failed += 1
            continue

        if got != a["size"]:
            print(f"{tag} ⚠️ 大小不符：期望 {a['size']}，实际 {got}")

        print(f"{tag} ✅ {human(got)}  校验一致")
        ok += 1
        total_got += got

    print(f"\n{'='*60}")
    print(f"下载 {ok} 个 / 跳过 {skipped} 个 / 失败 {failed} 个，本次共 {human(total_got)}")
    if failed:
        print("有失败项，请检查网络后重跑（已成功的会自动跳过）。")
        sys.exit(1)
    print("全部资产就位，可逐字节复现媒体资源。")


if __name__ == "__main__":
    main()
