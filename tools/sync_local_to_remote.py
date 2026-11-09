#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地仓库对齐到远端提交 d3fdc2c（GitHub 上那个由 API 创建的提交）。

难点：本机 git 协议不通（github.com 被封），无法 fetch。但 git 对象是内容寻址的，
只要能在本地**逐字节复现**出同样的 tree 与 commit 对象，算出的 SHA 就会与远端一致，
从而可以安全地把本地引用指过去。脚本用 SHA 是否吻合来自证"复现正确"。

安全性：全程使用临时索引文件（GIT_INDEX_FILE），不碰 .git/index；
只有在 tree 与 commit 的 SHA 都精确吻合后才 update-ref。
"""
import json
import os
import subprocess
import sys
import urllib.request

REPO = r"E:\Jd\new-chat\药忆-H5原型"
OWNER_REPO = "huojuda/yaoyi-jdxplorers"


def collect_sync_files():
    """与 deploy_via_gitdata.py 保持一致的待同步文件清单。"""
    files = ["index.html"]
    tts_dir = os.path.join(REPO, "tts")
    if os.path.isdir(tts_dir):
        for n in sorted(os.listdir(tts_dir)):
            if n.lower().endswith(".mp3"):
                files.append("tts/" + n)
    return files


SYNC_FILES = collect_sync_files()

# 用法：sync_local_to_remote.py [远端提交SHA] [--file <用于重建 tree 的内容文件>]
#   --file 用于"远端那一版内容与当前工作区不同"的场合
#   （例如远端是同一文件的 CRLF 变体）。默认用仓库里的 index.html。
_args = list(sys.argv[1:])
CONTENT_FILE = None
if "--file" in _args:
    _i = _args.index("--file")
    if _i + 1 >= len(_args):
        sys.exit("错误：--file 后面要跟文件路径")
    CONTENT_FILE = _args[_i + 1]
    del _args[_i:_i + 2]
# 远端提交 SHA：命令行第一个参数优先，否则取 main 当前 HEAD
REMOTE_COMMIT = _args[0] if _args else None
TMP_INDEX = os.path.join(os.environ.get("TEMP", "/tmp"), "yaoyi_sync_index")

# 凭据解析：与 deploy_via_gitdata.py 保持一致的优先级
#   1) 环境变量 GITHUB_TOKEN
#   2) 环境变量 GITHUB_TOKEN_FILE（推荐，避免 token 出现在进程列表/命令历史里）
#   3) 兼容旧路径 %TEMP%\yaoyi_gh_token2.txt
# 旧版把路径写死在第 3 条，而系统会清理 TEMP，导致脚本随时可能失效。
def _load_token():
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok:
        return tok
    path = os.environ.get("GITHUB_TOKEN_FILE")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    legacy = os.path.join(os.environ.get("TEMP", "/tmp"), "yaoyi_gh_token2.txt")
    if os.path.exists(legacy):
        with open(legacy, encoding="utf-8") as f:
            return f.read().strip()
    sys.exit("错误：未提供凭据。请设置 GITHUB_TOKEN 或 GITHUB_TOKEN_FILE。")


token = _load_token()


def api_get(url):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "yaoyi-sync")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read() or b"{}")


if not REMOTE_COMMIT:
    REMOTE_COMMIT = api_get(f"https://api.github.com/repos/{OWNER_REPO}/git/ref/heads/main")["object"]["sha"]


def git(*args, env=None, binary=False):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, env=e)
    if r.returncode != 0:
        sys.exit(f"[X] git {' '.join(args)} 失败：{r.stderr.decode('utf-8', 'replace')}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()


print("=== 1) 取远端提交的权威信息 ===")
c = api_get(f"https://api.github.com/repos/{OWNER_REPO}/git/commits/{REMOTE_COMMIT}")
remote_tree = c["tree"]["sha"]
parent = c["parents"][0]["sha"]
author = c["author"]
committer = c["committer"]
message = c["message"]
print(f"  tree={remote_tree[:12]}  parent={parent[:12]}")
print(f"  author   = {author['name']} <{author['email']}> {author['date']}")
print(f"  committer= {committer['name']} <{committer['email']}> {committer['date']}")
print(f"  message  = {message.splitlines()[0]!r} ... 末尾={message[-3:]!r}")

print("\n=== 2) 本地复现新 tree ===")
base_tree = git("rev-parse", "HEAD^{tree}")
print(f"  基线 tree = {base_tree[:12]}（本地 HEAD）")
# 说明：远端父提交的 tree 与本地 HEAD 可能不同（例如本次一次性新增了 tts/*.mp3），
# 所以这里不再要求整树相等 —— 正确性完全由下面 write-tree 的 SHA 是否吻合来证明。

env = {"GIT_INDEX_FILE": TMP_INDEX}
git("read-tree", base_tree, env=env)
new_blob = None
for rel in SYNC_FILES:
    if CONTENT_FILE and rel == "index.html":
        src_path = CONTENT_FILE          # --file 覆盖（用于复现 CRLF 变体等特殊状态）
    else:
        src_path = os.path.join(REPO, rel.replace("/", os.sep))
    if not os.path.isfile(src_path):
        sys.exit(f"[X] 本地缺少文件：{rel}")
    # --no-filters 很关键：不加的话 git 会对文件套用 autocrlf 的 clean 过滤器，
    # 含 CRLF 的内容会被归一成 LF，算出与远端不同的 blob（曾因此对齐失败）。
    blob = git("hash-object", "-w", "--no-filters", src_path)
    # --add 必须带上：本次可能新增了 HEAD 里不存在的路径（如 tts/*.mp3），
    # 不带 --add 时 update-index --cacheinfo 会拒绝新增条目。
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
    if rel == "index.html":
        new_blob = blob
print(f"  已写入 {len(SYNC_FILES)} 个文件的 blob（index.html={new_blob[:12]}）")
my_tree = git("write-tree", env=env)
print(f"  复现的 tree = {my_tree[:12]}   远端 = {remote_tree[:12]}")
if my_tree != remote_tree:
    sys.exit("[X] tree SHA 不吻合，本地对齐放弃（仓库未做任何改动）")
print("  ✅ tree 逐位吻合")

print("\n=== 3) 本地复现 commit 对象（试多种日期/消息写法，以 SHA 吻合为准）===")
from datetime import datetime, timezone
a_ts = int(datetime.strptime(author["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
c_ts = int(datetime.strptime(committer["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
print(f"  author ts={a_ts}  committer ts={c_ts}")

found = None
for a_date in (f"{a_ts} +0000", f"{a_ts} +0800"):
    for c_date in (f"{c_ts} +0000", f"{c_ts} +0800"):
        for suffix in ("", "\n"):
            body = message + suffix
            mf = os.path.join(os.environ.get("TEMP", "/tmp"), "yaoyi_commit_msg.txt")
            with open(mf, "wb") as f:
                f.write(body.encode("utf-8"))
            e2 = {
                "GIT_AUTHOR_NAME": author["name"], "GIT_AUTHOR_EMAIL": author["email"],
                "GIT_AUTHOR_DATE": a_date,
                "GIT_COMMITTER_NAME": committer["name"], "GIT_COMMITTER_EMAIL": committer["email"],
                "GIT_COMMITTER_DATE": c_date,
            }
            sha = git("commit-tree", my_tree, "-p", parent, "-F", mf, env=e2)
            tag = f"a={a_date} c={c_date} suffix={suffix!r}"
            if sha == REMOTE_COMMIT:
                print(f"  ✅ 命中：{tag} -> {sha[:12]}")
                found = (sha, tag)
                break
        if found:
            break
    if found:
        break

if not found:
    print("  ⚠️ 未能逐字节复现 commit（日期时区/消息尾部的写法不同）。")
    print("  → 完全不改本地引用：本地 HEAD 仍在 65323a3，工作区内容已与远端相同。")
    sys.exit(0)

print("\n=== 4) 对齐本地引用 ===")
head_before = git("rev-parse", "HEAD")
git("update-ref", "refs/heads/main", REMOTE_COMMIT)
git("update-ref", "refs/remotes/origin/main", REMOTE_COMMIT)
git("reset", "--mixed", REMOTE_COMMIT)
print(f"  refs/heads/main          : {head_before[:12]} -> {git('rev-parse', 'HEAD')[:12]}")
print(f"  refs/remotes/origin/main : {git('rev-parse', 'refs/remotes/origin/main')[:12]}")
print(f"  git branch -vv :\n    {git('branch', '-vv')}")
print(f"  git status     : {git('status', '--short') or '(工作区干净)'}")
