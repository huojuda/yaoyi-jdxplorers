#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 index.html 提交到 huojuda/yaoyi-jdxplorers 的 main 分支 —— 走 GitHub Git Data API。

为什么不用 git push：本机网络对 github.com（:443/:22）彻底封死（DNS 被劫持、
出口有 "Scholar Verify" 自签根 CA 做 TLS 中间人），而 api.github.com 可达。

为什么不用 Contents API：该接口 GET 会返回整个文件的 base64（约 325KB），
本机代理在约 320KB 处截断（IncompleteRead）。Git Data API 只上传、不下载大响应。

提交用 blob → tree → commit → 更新 ref 四步，与 `git push` 产生的对象完全一致。
脚本内置三重校验：
  1. 提交前：远端 index.html 的 blob sha 必须等于本地 HEAD 里的 blob sha（确认没人在远端改过它）
  2. 上传后：GitHub 返回的 blob sha 必须等于本地按 git 规则算出的 blob sha（确认字节没被改动）
  3. 提交后：重新读取远端树，index.html 的 blob sha 必须等于本地新 blob sha

用法：
    GITHUB_TOKEN_FILE=<路径> python deploy_via_gitdata.py          # 干跑
    GITHUB_TOKEN_FILE=<路径> python deploy_via_gitdata.py --push   # 提交
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "huojuda"
REPO = "yaoyi-jdxplorers"
BRANCH = "main"
FILE_PATH = "index.html"          # 主文件（仓库相对路径）
REPO_ROOT = r"E:\Jd\new-chat\药忆-H5原型"
LOCAL_FILE = os.path.join(REPO_ROOT, "index.html")


def collect_sync_files():
    """需要同步到远端的文件清单（仓库相对路径）。
    除主文件外，还包括预生成的播报音频 tts/*.mp3 —— 用 base_tree 合并的方式建树，
    所以任意路径（含子目录）都能在一次提交里新增/更新。"""
    files = ["index.html"]
    tts_dir = os.path.join(REPO_ROOT, "tts")
    if os.path.isdir(tts_dir):
        for n in sorted(os.listdir(tts_dir)):
            if n.lower().endswith(".mp3"):
                files.append("tts/" + n)
    return files


SYNC_FILES = collect_sync_files()


def local_path(rel):
    return os.path.join(REPO_ROOT, rel.replace("/", os.sep))
API = "https://api.github.com"

AUTHOR_NAME = "huojuda"
AUTHOR_EMAIL = "2189916060@qq.com"

COMMIT_MESSAGE = (
    "feat(voice): v1.4.3 预生成语音 —— 不再依赖手机系统 TTS，所有机型可朗读\n"
    "\n"
    "【问题定论】火炬手机的诊断数据显示：\n"
    "  ua=HuaweiBrowser/16.0.1.301 (Android 12)\n"
    "  supported=true 但 voicesCount=0（浏览器拿不到任何 TTS 音色）\n"
    "  audioApi/audioUnlocked=true, audioState=running（音频通道正常）\n"
    "  三次 speak()（含全默认档）全部 synthesis-failed\n"
    "→ 结论：该设备的浏览器**没有任何可用的系统 TTS 引擎**。\n"
    "  这不是本应用的 bug，而是「网页侧无解」的环境 —— 任何基于 speechSynthesis\n"
    "  的写法在这种机型上都不可能出声。\n"
    "\n"
    "【解法】让核心播报不再依赖系统 TTS\n"
    "· 结果页的「用药指导」话术完全由内置药品库字段拼成，是**固定文本**，\n"
    "  因此在本地用 Windows SAPI 一次性合成为 48 个 MP3（共 6.9MB，每个约 147KB），\n"
    "  随站点托管；点击时直接播放音频，**一次 speechSynthesis 都不调用**。\n"
    "· 覆盖：不受系统 TTS 影响 → 包括华为等零音色机型在内，所有手机都能出声。\n"
    "· 音质：本地合成一次，比在手机端现合成更稳定；播放位置由原生 audio 管理，\n"
    "  暂停/继续比 TTS 更可靠。\n"
    "· 体积：每次只加载当前这一个药品的音频（约 147KB），不影响首屏。\n"
    "· 动态文本（AI 问答、重复用药提醒）仍走系统 TTS，失败时给可复制诊断。\n"
    "\n"
    "【可靠性设计】\n"
    "· 文本指纹（UTF-16 码元 + FNV-1a 32 位，JS 与 Python 双端一致）写在清单里，\n"
    "  话术一改，指纹不匹配 → 自动退回系统 TTS，绝不「念的和屏幕上的不一样」。\n"
    "· 音频加载/播放失败 → 自动降级到系统 TTS，保证「点了总有反应」；\n"
    "  并记下该通道不可用，本次识别内不再重试（否则每次点击都先试音频再退回，\n"
    "  会把「暂停/继续」的语义冲掉 —— 这个坑已在测试中暴露并修掉）。\n"
    "· 点击语义先判通道：正在播→暂停、已暂停→继续，与走哪条通道无关。\n"
    "\n"
    "【部署工具升级】deploy_via_gitdata.py 与 sync_local_to_remote.py 现在支持\n"
    "  多文件同步（index.html + tts/*.mp3），仍用 base_tree 合并建树、逐文件校验 blob、\n"
    "  并以「复现出的 tree SHA 完全吻合」作为对齐成功与否的唯一判据。\n"
    "\n"
    "验证：新增 21 条定点回归（重点是「命中音频时 TTS 调用 0 次」和「JS/Python 指纹一致」）；\n"
    "既有语音套件加 Audio 降级桩后 16/16、23/23、16/16、13/13、14/14；\n"
    "摄像头 24、历史删除 46、登出隔离 20 —— 合计 193 条断言 0 失败。版本号 v1.4.3\n"
)


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()


def git_try(*args):
    """与 git() 相同，但失败时返回空串而不是抛异常（用于"文件还没进 HEAD"这类正常情况）。"""
    r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def blob_sha(data: bytes) -> str:
    """按 git 的对象格式算 blob 哈希：sha1(b'blob <len>\\0' + data)"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        path = os.environ.get("GITHUB_TOKEN_FILE")
        if path and os.path.exists(path):
            token = open(path, encoding="utf-8").read().strip()
    if not token:
        sys.exit("错误：未提供凭据。请设置 GITHUB_TOKEN 或 GITHUB_TOKEN_FILE。")
    return token


def api(method, url, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "yaoyi-deploy")
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
    token = read_token()

    local_bytes = open(LOCAL_FILE, "rb").read()
    new_blob = blob_sha(local_bytes)
    # ---- 收集待同步文件 ----
    local = {}
    for rel in SYNC_FILES:
        p = local_path(rel)
        if not os.path.isfile(p):
            sys.exit(f"[X] 本地缺少文件：{rel}")
        local[rel] = open(p, "rb").read()
    print(f"[1] 本地待同步 {len(local)} 个文件；{FILE_PATH}：{len(local_bytes):,} B  blob={new_blob[:12]}")

    # ---- 换行符守卫 ----
    # 本仓库的 index.html 历来是纯 LF。若工作区文件变成 CRLF，通常是
    # "用文本模式写文件"造成的（Windows 上 open(p,'w') 会把 \n 全转成 \r\n）。
    # 后果：① 站点多传约 4KB；② 本机 git 的 autocrlf 会把 CRLF 归一回 LF，
    # 导致 sync_local_to_remote.py 复现出的 blob 与远端不一致而放弃对齐，
    # 之后火炬本机 push 就会撞 non-fast-forward。所以在部署前直接拦下。
    crlf = local_bytes.count(b"\r\n")
    if crlf:
        sys.exit(
            f"[X] index.html 含 {crlf} 处 CRLF 换行，与仓库惯例（纯 LF）不符，已中止。\n"
            f"    修法：以二进制模式把 CRLF 转回 LF ——\n"
            f"    python -c \"p='index.html';d=open(p,'rb').read();open(p,'wb').write(d.replace(b'\\r\\n',b'\\n'))\"\n"
            f"    （并用 open(p,'w',encoding='utf-8',newline='') 或 'wb' 写文件，避免再次转换）"
        )
    print("    换行符：纯 LF ✅")

    # 基线：本地 HEAD 里各文件的 blob（用于确认远端没被别人改过）
    base_blobs = {}
    for rel in SYNC_FILES:
        b = git_try("rev-parse", f"HEAD:{rel}")
        base_blobs[rel] = b or None          # 为空说明是本次新增
    print(f"    本地基线：{sum(1 for v in base_blobs.values() if v)} 个文件已在 HEAD 中，"
          f"{sum(1 for v in base_blobs.values() if not v)} 个为新增")

    status, repo = api("GET", f"{API}/repos/{OWNER}/{REPO}", token)
    if status != 200:
        sys.exit(f"[X] 仓库不可访问（HTTP {status}）：{repo}")
    print(f"[2] 仓库：{repo['full_name']}  private={repo['private']}  default={repo['default_branch']}")

    status, ref = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    if status != 200:
        sys.exit(f"[X] 读取分支失败（HTTP {status}）：{ref}")
    head_sha = ref["object"]["sha"]
    print(f"[3] 远端 HEAD：{head_sha[:12]}")

    status, commit = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/commits/{head_sha}", token)
    if status != 200:
        sys.exit(f"[X] 读取提交失败（HTTP {status}）：{commit}")
    base_tree = commit["tree"]["sha"]

    status, tree = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/trees/{base_tree}?recursive=1", token)
    if status != 200:
        sys.exit(f"[X] 读取树失败（HTTP {status}）：{tree}")
    remote_map = {e["path"]: e.get("sha")
                  for e in tree.get("tree", []) if e.get("type") == "blob"}

    # ---- 逐文件对照：远端 vs 本地基线（防止覆盖别人的改动）----
    print("[4] 逐文件对照（远端 / 本地基线）：")
    changed, added = [], []
    for rel in SYNC_FILES:
        rb, lb = remote_map.get(rel), base_blobs.get(rel)
        nb = blob_sha(local[rel])
        if rb is None and lb is not None:
            sys.exit(f"[X] 远端缺少 {rel}，但本地基线里存在，已中止（可能被他人删除/改动）")
        if lb is None:
            added.append(rel)
        if rb is not None and lb is not None and rb != lb:
            sys.exit(f"[X] {rel} 远端内容与本地基线不一致，已中止（避免覆盖别人的改动）。\n"
                     f"    远端 {rb[:12]} != 本地基线 {lb[:12]}")
        if rb != nb:
            changed.append(rel)
    if added:
        print(f"    新增：{len(added)} 个（如 {added[0]}）")
    if not changed:
        sys.exit("[=] 远端已是这份内容，无需提交。")
    print(f"    需要更新 {len(changed)} 个文件（如 {changed[0]}）")
    print("    ✅ 与本地基线一致，可安全覆盖")

    if not do_push:
        print("\n[干跑] 校验全部通过。加 --push 即真正提交。")
        return

    # ---- 1) 逐个上传 blob ----
    entries = []
    for rel in changed:
        b = blob_sha(local[rel])
        status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/blobs", token,
                          {"content": base64.b64encode(local[rel]).decode("ascii"), "encoding": "base64"})
        if status not in (200, 201):
            sys.exit(f"[X] 上传 {rel} 的 blob 失败（HTTP {status}）：{res}")
        if res["sha"] != b:
            sys.exit(f"[X] {rel} 的 blob 校验失败：GitHub 返回 {res['sha'][:12]}，"
                     f"本地算得 {b[:12]}（字节被改动）")
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": b})
    print(f"[5] 已上传 {len(entries)} 个 blob，逐个校验一致")

    # ---- 2) 建树（base_tree 合并，自动处理 tts/ 这类子目录）----
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token,
                      {"base_tree": base_tree, "tree": entries})
    if status not in (200, 201):
        sys.exit(f"[X] 建树失败（HTTP {status}）：{res}")
    new_tree = res["sha"]
    print(f"[6] 新树：{new_tree[:12]}")

    # ---- 3) 建提交 ----
    status, res = api("POST", f"{API}/repos/{OWNER}/{REPO}/git/commits", token,
                      {"message": COMMIT_MESSAGE, "tree": new_tree, "parents": [head_sha],
                       "author": {"name": AUTHOR_NAME, "email": AUTHOR_EMAIL},
                       "committer": {"name": AUTHOR_NAME, "email": AUTHOR_EMAIL}})
    if status not in (200, 201):
        sys.exit(f"[X] 建提交失败（HTTP {status}）：{res}")
    new_commit = res["sha"]
    print(f"[7] 新提交：{new_commit[:12]}  {COMMIT_MESSAGE.splitlines()[0]}")

    # ---- 4) 更新分支引用 ----
    status, res = api("PATCH", f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", token,
                      {"sha": new_commit, "force": False})
    if status != 200:
        sys.exit(f"[X] 更新分支失败（HTTP {status}）：{res}")
    print(f"[8] {BRANCH} 已推进到 {res['object']['sha'][:12]}")

    # ---- 5) 提交后复核 ----
    status, ref2 = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    verify_sha = ref2["object"]["sha"]
    status, c2 = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/commits/{verify_sha}", token)
    status, t2 = api("GET", f"{API}/repos/{OWNER}/{REPO}/git/trees/{c2['tree']['sha']}", token)
    e2 = next((e for e in t2["tree"] if e["path"] == FILE_PATH), None)
    ok = e2 and e2["sha"] == new_blob
    print(f"[9] 复核：远端 {FILE_PATH} blob={e2['sha'][:12] if e2 else '?'}  "
          f"本地={new_blob[:12]}  {'✅ 一致' if ok else '❌ 不一致'}")
    if not ok:
        sys.exit("[X] 复核未通过。")
    print(f"    远端 {FILE_PATH} 大小 = {e2.get('size')} B（本地 {len(local_bytes)} B）")
    print("\n完成。EdgeOne 的 GitHub 联动应已收到推送并开始构建。")


if __name__ == "__main__":
    main()
