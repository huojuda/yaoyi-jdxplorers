#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按文件推送单个文件到 GitHub（走 REST Git Data API，不依赖 github.com）

本机 github.com 被 DNS 劫持（解析到 205.164.50.x），git push 不可用；
但 api.github.com 可达。本脚本用 Git Data API 的
blob → tree → commit → PATCH ref 四步完成一次等价于 git push 的提交。

与 deploy_via_gitdata.py 的分工：
  · deploy_via_gitdata.py 专用于站点主文件（index.html + tts/*.mp3），
    提交信息写死为 v1.4.3 的发布说明；
  · 本脚本是通用单文件推送器，用于推 .gitignore 之类的非站点文件，
    提交信息由调用方给出。

用法：
    set GITHUB_TOKEN_FILE=E:\\Jd\\new-chat\\.secrets\\github_pat.txt
    python push_file_via_gitdata.py .gitignore --message-file msg.txt
    python push_file_via_gitdata.py .gitignore --message-file msg.txt --push

安全设计：
  1) 令牌只从环境变量或文件读，不落盘、不进命令行历史。
  2) 推送前校验「远端该文件的 blob == 本地 HEAD 里的 blob」，
     不一致说明远端被别人改过，直接中止，不覆盖。
  3) 建提交前不 force，PATCH ref 时用 force=false，防止覆盖他人提交。
  4) 推送后回读远端 blob，与期望值比对，确认字节未被改动。
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "huojuda"
REPO = "yaoyi-jdxplorers"
BRANCH = "main"
REPO_ROOT = r"E:\Jd\new-chat\药忆-H5原型"
API = "https://api.github.com"
AUTHOR_NAME = "huojuda"
AUTHOR_EMAIL = "2189916060@qq.com"


def git(*args):
    r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def blob_sha(data: bytes) -> str:
    """git 对象格式的 blob 哈希：sha1(b'blob <len>\\0' + data)"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        path = os.environ.get("GITHUB_TOKEN_FILE")
        if path and os.path.exists(path):
            lines = []
            for line in open(path, encoding="utf-8"):
                s = line.strip()
                if s and not s.startswith("#"):
                    lines.append(s)
            token = lines[-1] if lines else ""
    if not token:
        sys.exit("错误：未提供凭据。请设置 GITHUB_TOKEN 或 GITHUB_TOKEN_FILE。")
    if not re.fullmatch(r"(github_pat_[A-Za-z0-9_]{50,}|gh[pousr]_[A-Za-z0-9]{30,})", token):
        sys.exit("错误：令牌格式不合法。fine-grained PAT 应以 github_pat_ 开头且长度足够；"
                 "请检查 .secrets/github_pat.txt 是否仍是占位文本。")
    return token


def api(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "yaoyi-push-file")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def main():
    do_push = "--push" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    rel = args[0].replace("\\", "/")

    if "--message-file" in sys.argv:
        mf = sys.argv[sys.argv.index("--message-file") + 1]
        message = open(mf, encoding="utf-8").read().strip()
    else:
        message = "chore(security): 加固 .gitignore"

    token = read_token()
    print("[0] 令牌已读取并通过格式校验")

    path = os.path.join(REPO_ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        sys.exit(f"[X] 本地文件不存在：{rel}")
    data = open(path, "rb").read()
    want = blob_sha(data)
    crlf = data.count(b"\r\n")
    print(f"[1] 本地 {rel}：{len(data):,} B  blob={want[:12]}  CRLF={crlf}")

    # 本地 HEAD 基线
    base = git("rev-parse", f"HEAD:{rel}")
    print(f"    本地 HEAD 基线：{base[:12] if base else '(未跟踪)'}")

    status, ref = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    if status != 200:
        sys.exit(f"[X] 读取分支失败（HTTP {status}）：{ref}")
    head_sha = ref["object"]["sha"]
    print(f"[2] 远端 HEAD：{head_sha[:12]}")

    status, commit = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/commits/{head_sha}", token)
    if status != 200:
        sys.exit(f"[X] 读取提交失败（HTTP {status}）：{commit}")
    base_tree = commit["tree"]["sha"]

    status, tree = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/trees/{base_tree}?recursive=1", token)
    if status != 200:
        sys.exit(f"[X] 读取树失败（HTTP {status}）：{tree}")
    remote_map = {e["path"]: e.get("sha") for e in tree.get("tree", []) if e.get("type") == "blob"}

    rb = remote_map.get(rel)
    print(f"[3] 远端该文件 blob：{rb[:12] if rb else '(不存在)'}")

    # 防覆盖守卫
    if rb is not None and base and rb != base:
        sys.exit(f"[X] 远端内容与本地基线不一致，已中止（避免覆盖别人的改动）。\n"
                 f"    远端 {rb[:12]} != 本地基线 {base[:12]}")
    if rb == want:
        print("[=] 远端已是这份内容，无需提交。")
        return
    print("    ✅ 与本地基线一致，可安全覆盖")

    if not do_push:
        print("\n[干跑] 校验全部通过。加 --push 即真正提交。")
        return

    # 1) 上传 blob
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/blobs", token,
                      {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
    if status not in (200, 201):
        sys.exit(f"[X] 上传 blob 失败（HTTP {status}）：{res}")
    if res["sha"] != want:
        sys.exit(f"[X] blob 校验失败：GitHub 返回 {res['sha'][:12]}，本地算得 {want[:12]}")
    print(f"[4] blob 已上传并校验一致：{want[:12]}")

    # 2) 建树（base_tree 合并）
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token,
                      {"base_tree": base_tree,
                       "tree": [{"path": rel, "mode": "100644", "type": "blob", "sha": want}]})
    if status not in (200, 201):
        sys.exit(f"[X] 建树失败（HTTP {status}）：{res}")
    new_tree = res["sha"]
    print(f"[5] 新树：{new_tree[:12]}（文件数 {len(res.get('tree', []))}）")

    # 3) 建提交
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/commits", token,
                      {"message": message, "tree": new_tree, "parents": [head_sha],
                       "author": {"name": AUTHOR_NAME, "email": AUTHOR_EMAIL},
                       "committer": {"name": AUTHOR_NAME, "email": AUTHOR_EMAIL}})
    if status not in (200, 201):
        sys.exit(f"[X] 建提交失败（HTTP {status}）：{res}")
    new_commit = res["sha"]
    print(f"[6] 新提交：{new_commit[:12]}")

    # 4) 移动分支（force=false）
    status, res = api("PATCH", f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", token,
                      {"sha": new_commit, "force": False})
    if status not in (200, 201):
        sys.exit(f"[X] 更新分支失败（HTTP {status}）：{res}")
    print(f"[7] 分支 {BRANCH} 已指向 {new_commit[:12]}")

    # 5) 回读校验
    status, ref2 = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    got = ref2.get("object", {}).get("sha") if status == 200 else None
    print(f"[8] 回读远端 HEAD：{got[:12] if got else '读取失败'}  "
          f"{'✅ 一致' if got == new_commit else '⚠️ 不一致'}")
    print(f"\n完成。https://github.com/{OWNER}/{REPO}/commit/{new_commit}")


if __name__ == "__main__":
    main()
