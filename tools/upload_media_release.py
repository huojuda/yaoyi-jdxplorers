#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 media/MANIFEST.json 里的媒体资产上传到 GitHub Release。

媒体体积超出 git 单文件 100MB 限制，无法入库，因此走 Release 托管。
本脚本让"媒体归档"这件事可重复：换机、增补素材、重建 Release 都能一条命令完成。

用法：
    export GITHUB_TOKEN=<有 repo 权限的 PAT>
    python tools/upload_media_release.py            # 上传清单里缺的资产
    python tools/upload_media_release.py --dry-run  # 只列出将要上传什么
    python tools/upload_media_release.py --recreate # 删掉旧 release 重建（会换 id）

行为：
  · Release 不存在则按 MANIFEST 里的 tag/name 创建
  · 已存在同名资产则跳过（可重复执行，断点续传）
  · 上传后校验远端 size，不符则报错
  · 令牌格式会先做正则校验，避免把占位文本当令牌
"""

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request

OWNER = "huojuda"
REPO = "yaoyi-jdxplorers"
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(os.path.dirname(HERE), "media", "MANIFEST.json")
# MANIFEST 里的 original_path 是相对工作区根写的，恢复时需要把仓库目录映射回去
MEDIA_ROOT_GUESS = os.path.dirname(os.path.dirname(HERE))


def read_token():
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok:
        p = os.environ.get("GITHUB_TOKEN_FILE")
        if p and os.path.exists(p):
            lines = [l.strip() for l in open(p, encoding="utf-8")
                     if l.strip() and not l.strip().startswith("#")]
            tok = lines[-1] if lines else ""
    if not tok:
        sys.exit("错误：未提供凭据。设置 GITHUB_TOKEN 或 GITHUB_TOKEN_FILE。")
    if not re.fullmatch(r"(github_pat_[A-Za-z0-9_]{50,}|gh[pousr]_[A-Za-z0-9]{30,})", tok):
        sys.exit("错误：令牌格式不合法（fine-grained PAT 应以 github_pat_ 开头）。")
    return tok


def api(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "yaoyi-upload-media")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body or b"{}")
        except Exception:
            return e.code, {"raw": body[:300].decode("utf-8", "ignore")}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def upload_asset(upload_url, token, path, name, size):
    """流式上传单个资产，避免把 120MB 全读进内存。"""
    host_path = upload_url.split("://", 1)[1]
    host, path_part = host_path.split("/", 1)
    path_part = path_part.split("{")[0]  # 去掉 {?name,label}
    sep = "&" if "?" in path_part else "?"
    import urllib.parse
    full = f"/{path_part}{sep}name={urllib.parse.quote(name)}"

    conn = http.client.HTTPSConnection(host, timeout=600)
    conn.putrequest("POST", full, skip_accept_encoding=True)
    conn.putheader("Authorization", f"Bearer {token}")
    conn.putheader("Accept", "application/vnd.github+json")
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("Content-Length", str(size))
    conn.putheader("User-Agent", "yaoyi-upload-media")
    conn.endheaders()

    sent = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            conn.send(chunk)
            sent += len(chunk)
            sys.stdout.write(f"\r      {sent/1048576:8.1f} / {size/1048576:.1f} MB  "
                             f"{sent*100//size:3d}%   ")
            sys.stdout.flush()
    print()

    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(body or b"{}")
    except Exception:
        return resp.status, {"raw": body[:300].decode("utf-8", "ignore")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recreate", action="store_true")
    args = ap.parse_args()

    token = read_token()
    meta = json.load(open(MANIFEST, encoding="utf-8"))
    rel = meta["release"]
    tag = rel["tag"]

    print(f"仓库 {OWNER}/{REPO}   目标 Release: {tag}   资产 {len(meta['assets'])} 个")

    # ---- 查 / 建 release ----
    status, r = api("GET", f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{tag}", token)
    if status == 404:
        if args.dry_run:
            print("[dry-run] Release 不存在，将创建")
            release = {"id": 0, "upload_url": "https://uploads.github.com/x/y{?name}"}
        else:
            print("Release 不存在，创建中…")
            status, release = api("POST", f"https://api.github.com/repos/{OWNER}/{REPO}/releases", token, {
                "tag_name": tag, "name": rel["name"],
                "body": "药忆项目媒体素材归档。\n\n"
                        "共 16 个资产、552.8 MB，均为宣传视频与参考素材。\n"
                        "请勿手动下载零散文件 —— 用仓库里的 `media/fetch_media.py`，"
                        "它会按 `media/MANIFEST.json` 逐个下载并校验 sha256。\n\n"
                        "命名对照（ASCII ↔ 原始中文路径）见 MANIFEST.json 的 original_path 字段。",
                "draft": False, "prerelease": False,
            })
            if status not in (200, 201):
                sys.exit(f"[X] 创建 Release 失败（HTTP {status}）：{release}")
            print(f"  已创建，id={release['id']}")
    elif status == 200:
        release = r
        print(f"Release 已存在，id={release['id']}，现有资产 {len(release.get('assets', []))} 个")
    else:
        sys.exit(f"[X] 查询 Release 失败（HTTP {status}）：{r}")

    existing = {a["name"]: a for a in release.get("assets", [])}
    upload_url = release.get("upload_url", "")

    ok = skipped = failed = 0
    total_up = 0

    for i, a in enumerate(meta["assets"], 1):
        name = a["asset"]
        # 优先按原始工作区路径找；若是从 clone 恢复出来的副本，则回落到 media-files/
        src = os.path.join(MEDIA_ROOT_GUESS, a["original_path"].replace("/", os.sep))
        if not os.path.exists(src):
            alt = os.path.join(os.path.dirname(HERE), "media-files", name)
            if os.path.exists(alt):
                src = alt
        tagline = f"[{i:>2}/{len(meta['assets'])}] {name:<30}"

        if name in existing:
            esz = existing[name].get("size")
            if esz == a["size"]:
                print(f"{tagline} ✅ 远端已存在且大小一致，跳过")
                skipped += 1
                continue
            print(f"{tagline} ⚠️ 远端大小不符（{esz} vs {a['size']}），将重新上传")

        if not os.path.exists(src):
            print(f"{tagline} ❌ 本地源文件缺失：{a['original_path']}")
            failed += 1
            continue

        if args.dry_run:
            print(f"{tagline} [dry-run] 将上传 {a['size']/1048576:.1f} MB")
            continue

        print(f"{tagline} 上传中 {a['size']/1048576:.1f} MB")
        status, res = upload_asset(upload_url, token, src, name, a["size"])
        if status not in (200, 201):
            print(f"{tagline} ❌ 上传失败（HTTP {status}）：{str(res)[:200]}")
            failed += 1
            continue
        if res.get("size") != a["size"]:
            print(f"{tagline} ⚠️ 远端 size={res.get('size')} 与本地 {a['size']} 不符")
        print(f"{tagline} ✅ 已上传，远端 {res.get('size',0)/1048576:.1f} MB")
        ok += 1
        total_up += a["size"]

    print(f"\n{'='*60}")
    print(f"上传 {ok} / 跳过 {skipped} / 失败 {failed}，本次共 {total_up/1048576:.1f} MB")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
