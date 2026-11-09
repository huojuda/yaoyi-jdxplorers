#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按文件推送一个或多个文件到 GitHub（走 REST Git Data API，不依赖 github.com）

本机 github.com 曾被 DNS 劫持，git push 不可用；但 api.github.com 可达。
本脚本用 Git Data API 的 blob → tree → commit → PATCH ref 四步，
完成一次等价于 git push 的提交。

与 deploy_via_gitdata.py 的分工：
  · deploy_via_gitdata.py 专用于站点主文件（index.html + tts/*.mp3），
    提交信息写死为 v1.4.3 的发布说明；
  · 本脚本是通用推送器，用于推 .gitignore / 文档 / 工具等非站点文件，
    可一次推多个文件，提交信息由调用方给出。

用法：
    set GITHUB_TOKEN_FILE=E:\\Jd\\new-chat\\.secrets\\github_pat.txt
    python push_file_via_gitdata.py .gitignore --message-file msg.txt
    python push_file_via_gitdata.py README.md RESTORE.md media/README.md ^
           media/MANIFEST.json --message "docs: 修正数字" --push

安全设计：
  1) 令牌只从环境变量或文件读，不落盘、不进命令行历史。
  2) 推送前校验「远端该文件的 blob == 本地 HEAD 里的 blob」，
     不一致说明远端被别人改过，直接中止，不覆盖。
  3) 建提交前不 force，PATCH ref 时用 force=false，防止覆盖他人提交。
  4) 文本文件若含 CRLF 直接中止 —— 本仓库 .gitattributes 声明了 eol=lf，
     手工拼 blob 不会做换行符转换，带 CRLF 推上去会污染远端。
  5) 推送后回读远端 blob，与期望值比对，确认字节未被改动。
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
API = "https://api.github.com"
AUTHOR_NAME = "huojuda"
AUTHOR_EMAIL = "2189916060@qq.com"


def find_repo_root(start):
    """从脚本位置向上找同时含 .git 与 index.html 的目录，兼容脚本被放在 tools/ 或别处。"""
    d = os.path.abspath(start)
    for _ in range(6):
        if os.path.isdir(os.path.join(d, ".git")) and os.path.isfile(os.path.join(d, "index.html")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    sys.exit("错误：找不到仓库根（需同时含 .git 与 index.html）。可用 REPO_ROOT 环境变量指定。")


REPO_ROOT = os.environ.get("REPO_ROOT") or find_repo_root(os.path.dirname(os.path.abspath(__file__)))

TEXT_EXT = {".md", ".py", ".json", ".html", ".js", ".css", ".txt", ".yml",
            ".yaml", ".xml", ".svg", ".gitignore", ".gitattributes", ""}


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


def parse_args(argv):
    """极简解析：位置参数=待推文件；--message/--message-file 取下一个值；--push 开关。"""
    files, opts, i, do_push = [], {"message": None, "message_file": None}, 0, False
    while i < len(argv):
        a = argv[i]
        if a == "--push":
            do_push = True
        elif a == "--message":
            i += 1
            opts["message"] = argv[i]
        elif a == "--message-file":
            i += 1
            opts["message_file"] = argv[i]
        elif a.startswith("--"):
            sys.exit(f"未知选项：{a}")
        else:
            files.append(a)
        i += 1
    return files, opts, do_push


def main():
    files, opts, do_push = parse_args(sys.argv[1:])
    if not files:
        sys.exit(__doc__)

    if opts["message_file"]:
        message = open(opts["message_file"], encoding="utf-8").read().strip()
    else:
        message = opts["message"] or "chore: 更新仓库文件"

    token = read_token()
    print("[0] 令牌已读取并通过格式校验")

    # ---- 本地读取 + 校验 ----
    plan = []  # [(rel, data_or_None, want_sha, base_sha)]
    for raw in files:
        rel = raw.replace("\\", "/").lstrip("./")
        path = os.path.join(REPO_ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            sys.exit(f"[X] 本地文件不存在：{rel}")
        data = open(path, "rb").read()
        want = blob_sha(data)
        crlf = data.count(b"\r\n")
        ext = os.path.splitext(rel)[1].lower()
        if crlf and (ext in TEXT_EXT or "." not in os.path.basename(rel)):
            sys.exit(f"[X] {rel} 含 {crlf} 个 CRLF，与 .gitattributes 的 eol=lf 冲突，已中止。\n"
                     f"    修法：python -c \"p=r'{path}';d=open(p,'rb').read();open(p,'wb').write(d.replace(b'\\r\\n',b'\\n'))\"")
        base = git("rev-parse", f"HEAD:{rel}") or None
        print(f"[1] {rel}：{len(data):,} B  blob={want[:12]}  CRLF={crlf}  本地基线={(base or '(未跟踪)')[:12]}")
        plan.append((rel, data, want, base))

    # ---- 远端基线 ----
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

    # ---- 防覆盖守卫 ----
    todo = []
    for rel, data, want, base in plan:
        rb = remote_map.get(rel)
        print(f"[3] 远端 {rel} blob：{(rb or '(不存在)')[:12]}")
        if rb is not None and base and rb != base:
            sys.exit(f"[X] 远端 {rel} 与本地基线不一致，已中止（避免覆盖别人的改动）。\n"
                     f"    远端 {rb[:12]} != 本地基线 {base[:12]}")
        if rb == want:
            print("    [=] 远端已是这份内容，跳过")
            continue
        todo.append((rel, data, want))

    if not todo:
        print("\n[=] 所有文件远端都已是最新，无需提交。")
        return
    print(f"    ✅ {len(todo)} 个文件可安全写入")

    if not do_push:
        print("\n[干跑] 校验全部通过。加 --push 即真正提交。")
        return

    # 1) 上传 blob
    entries = []
    for rel, data, want in todo:
        status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/blobs", token,
                          {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        if status not in (200, 201):
            sys.exit(f"[X] 上传 {rel} 的 blob 失败（HTTP {status}）：{res}")
        if res["sha"] != want:
            sys.exit(f"[X] {rel} blob 校验失败：GitHub 返回 {res['sha'][:12]}，本地算得 {want[:12]}")
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": want})
        print(f"[4] blob 已上传并校验一致：{rel} → {want[:12]}")

    # 2) 建树（base_tree 合并）
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token,
                      {"base_tree": base_tree, "tree": entries})
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
    if got != new_commit:
        sys.exit("[X] 回读不一致，请检查分支保护规则")
    print(f"\n完成。https://github.com/{OWNER}/{REPO}/commit/{new_commit}")


if __name__ == "__main__":
    main()
