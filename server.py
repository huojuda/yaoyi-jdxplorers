#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
药忆 H5 后端代理
- 静态文件服务（index.html 等）
- /api/recognize：调用火山方舟多模态大模型识别药盒
- /api/pair：配对码生成与验证
- /api/events：事件上报(POST) + 轮询(GET)
- API Key 仅存于后端，不暴露给前端

用法：
    python server.py
    浏览器打开 http://localhost:8080
"""

import json
import os
import random
import time
import hashlib
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# ========== 配置 ==========
PORT = 8080
# api_config.json 在上级目录
CONFIG_PATH = Path(__file__).resolve().parent.parent / "api_config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

API_KEY = CONFIG["api_key"]
API_ENDPOINT = CONFIG["api_endpoint"]
MODEL_ID = CONFIG["primary_model"]["id"]
RECOGNITION_PROMPT = CONFIG["recognition_prompt"]

# Upstash Redis 配置（本地开发时从环境变量读取）
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# 静态文件根目录 = 当前目录（index.html 所在目录）
STATIC_DIR = str(Path(__file__).resolve().parent)


# ========== Upstash Redis 辅助函数 ==========
def redis_cmd(*args):
    """通过 Upstash REST API 执行 Redis 命令（数组格式）"""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise RuntimeError("未配置 UPSTASH_REDIS_URL / UPSTASH_REDIS_TOKEN")
    url = UPSTASH_URL.rstrip("/") + "/"
    payload = json.dumps(list(args)).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + UPSTASH_TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ========== 请求处理 ==========
class YaoyiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_POST(self):
        path = self.path.split("?")[0]  # 去掉 query 参数
        if path == "/api/recognize":
            self._handle_recognize()
        elif path == "/api/pair":
            self._handle_pair()
        elif path == "/api/events":
            self._handle_events_post()
        elif path == "/api/auth":
            self._handle_auth()
        else:
            self._send_json(404, {"error": "接口不存在"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/events":
            self._handle_events_get()
        elif path == "/api/auth":
            self._handle_auth()
        else:
            super().do_GET()  # 静态文件

    # ---------- 药品识别 ----------
    def _handle_recognize(self):
        try:
            # 1. 读取请求体
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            image_data = data.get("image", "")

            if not image_data:
                self._send_json(400, {"error": "缺少图片数据"})
                return

            print(f"[识别] 收到图片，base64 长度: {len(image_data)}")

            # 2. 构造火山方舟 API 请求（OpenAI 兼容格式）
            payload = {
                "model": MODEL_ID,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": RECOGNITION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data},
                            },
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            }

            req = urllib.request.Request(
                API_ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_KEY}",
                },
                method="POST",
            )

            # 3. 发送请求
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            # 4. 解析返回内容（提取 JSON）
            content = result["choices"][0]["message"]["content"].strip()
            drug_info = self._extract_json(content)

            if not drug_info:
                self._send_json(500, {"error": "模型返回格式解析失败", "raw": content})
                return

            print(f"[识别] 结果: {drug_info.get('drug_name')} / {drug_info.get('brand_name')}")
            self._send_json(200, drug_info)

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"[识别] API HTTP 错误 {e.code}: {err_body}")
            self._send_json(502, {"error": f"API返回错误 {e.code}", "detail": err_body})
        except Exception as e:
            print(f"[识别] 异常: {e}")
            self._send_json(500, {"error": str(e)})

    # ---------- 配对码生成与验证 ----------
    def _handle_pair(self):
        try:
            body = self._read_body()
            data = json.loads(body)
            action = data.get("action", "")

            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            if action == "create":
                code = str(random.randint(100000, 999999))
                family_id = "fam_" + str(random.randint(10000000, 99999999))
                redis_cmd("SET", "pair:code:" + code, family_id, "EX", 300)
                print(f"[配对] 生成配对码 {code} → familyId={family_id}")
                self._send_json(200, {"code": code, "familyId": family_id, "expiresIn": 300})

            elif action == "verify":
                code = data.get("code", "").strip()
                phone = data.get("phone", "").strip()
                if not code or not phone:
                    self._send_json(400, {"error": "请填写配对码和联系电话"})
                    return
                result = redis_cmd("GET", "pair:code:" + code)
                family_id = result.get("result")
                if not family_id:
                    self._send_json(400, {"error": "配对码无效或已过期"})
                    return
                redis_cmd("SET", "family:" + family_id + ":phone", phone)
                redis_cmd("DEL", "pair:code:" + code)
                print(f"[配对] 验证成功 code={code} → familyId={family_id}, phone={phone}")
                self._send_json(200, {"familyId": family_id, "phone": phone})

            else:
                self._send_json(400, {"error": "未知操作"})

        except Exception as e:
            print(f"[配对] 异常: {e}")
            self._send_json(500, {"error": str(e)})

    # ---------- 事件上报 (POST) ----------
    def _handle_events_post(self):
        try:
            body = self._read_body()
            data = json.loads(body)

            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            # 确认异常
            if data.get("action") == "acknowledge":
                family_id = data.get("familyId", "")
                ts = data.get("ts", 0)
                if not family_id or not ts:
                    self._send_json(400, {"error": "缺少 familyId 或 ts"})
                    return
                redis_cmd("SET", "family:" + family_id + ":ack:" + str(ts), "1")
                ack_event = {"type": "acknowledge", "timestamp": int(time.time() * 1000), "refTs": ts, "status": "acknowledged"}
                redis_cmd("RPUSH", "family:" + family_id + ":events", json.dumps(ack_event, ensure_ascii=False))
                self._send_json(200, {"ok": True})
                return

            # 正常事件上报
            family_id = data.get("familyId", "")
            event_type = data.get("type", "")
            if not family_id or not event_type:
                self._send_json(400, {"error": "缺少 familyId 或 type"})
                return

            valid_types = {"medication", "alert", "monitor_end"}
            if event_type not in valid_types:
                self._send_json(400, {"error": "未知事件类型: " + event_type})
                return

            event = {
                "type": event_type,
                "drugName": data.get("drugName", ""),
                "time": data.get("time", ""),
                "timestamp": int(time.time() * 1000),
                "status": "pending",
            }
            redis_cmd("RPUSH", "family:" + family_id + ":events", json.dumps(event, ensure_ascii=False))
            redis_cmd("LTRIM", "family:" + family_id + ":events", -200, -1)
            print(f"[事件] familyId={family_id} type={event_type} drug={data.get('drugName', '')}")
            self._send_json(200, {"ok": True, "event": event})

        except Exception as e:
            print(f"[事件] POST 异常: {e}")
            self._send_json(500, {"error": str(e)})

    # ---------- 事件轮询 (GET) ----------
    def _handle_events_get(self):
        try:
            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            family_id = params.get("familyId", [""])[0]
            after = int(params.get("after", ["0"])[0])

            if not family_id:
                self._send_json(400, {"error": "缺少 familyId 参数"})
                return

            result = redis_cmd("LRANGE", "family:" + family_id + ":events", 0, -1)
            raw_list = result.get("result", [])

            events = []
            for item in raw_list:
                try:
                    evt = json.loads(item)
                    if evt.get("timestamp", 0) > after:
                        events.append(evt)
                except (json.JSONDecodeError, TypeError):
                    continue

            # 已确认的异常标记
            ack_result = redis_cmd("KEYS", "family:" + family_id + ":ack:*")
            acknowledged = []
            for key in ack_result.get("result", []):
                ts = key.rsplit(":", 1)[-1]
                acknowledged.append(int(ts))

            self._send_json(200, {"events": events, "acknowledged": acknowledged})

        except Exception as e:
            print(f"[事件] GET 异常: {e}")
            self._send_json(500, {"error": str(e)})

    # ---------- 账号系统：注册/登录/me/登出 ----------
    def _handle_auth(self):
        try:
            # 合并 URL 参数与请求体（前端 apiPost 走 GET+query）
            qs = parse_qs(urlparse(self.path).query)
            data = {k: v[0] for k, v in qs.items()}
            try:
                body = self._read_body()
                if body:
                    data.update(json.loads(body))
            except Exception:
                pass

            action = data.get("action", "")

            # Authorization 头优先，其次 _auth 参数
            auth_header = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            token = auth_header or data.get("_auth", "")

            if action == "register":
                self._auth_register(data)
            elif action == "login":
                self._auth_login(data)
            elif action == "resetpw":
                self._auth_resetpw(data)
            elif action == "delete":
                self._auth_delete(data, token)
            elif action == "diag":
                self._auth_diag(data)
            elif action == "me":
                self._auth_me(token)
            elif action == "logout":
                if token:
                    redis_cmd("DEL", "session:" + token)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "未知操作，应为 register/login/me/logout"})

        except Exception as e:
            print(f"[账号] 异常: {e}")
            self._send_json(500, {"error": str(e)})

    def _auth_register(self, data):
        phone = str(data.get("phone", "")).strip()
        password = str(data.get("password", ""))
        role = str(data.get("role", "")).strip()
        display_name = str(data.get("displayName", "")).strip() or ("家人" if role == "caregiver" else "长辈")

        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return
        if len(password) < 6:
            self._send_json(400, {"error": "密码至少6位"})
            return
        if role not in ("patient", "caregiver"):
            self._send_json(400, {"error": "请选择角色：患者或家人"})
            return

        user_id = f"u_{int(time.time() * 1000)}{random.randint(1000, 9999)}"
        salt = os.urandom(16).hex()
        pass_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), 10000
        ).hex()

        # 先写用户数据，再原子发布手机号映射：保证映射存在时用户数据必然完整
        redis_cmd(
            "HSET", f"user:{user_id}",
            "phone", phone,
            "passHash", pass_hash,
            "salt", salt,
            "role", role,
            "displayName", display_name,
            "createdAt", str(int(time.time() * 1000)),
        )

        # 发布映射前回读验证，防止静默写入失败产生僵尸账号
        check = redis_cmd("HGET", f"user:{user_id}", "salt").get("result")
        if not check:
            self._send_json(500, {"error": "数据写入验证失败（Redis异常），请稍后重试"})
            return

        nx = redis_cmd("SET", "user:phone:" + phone, user_id, "NX")
        if nx.get("result") != "OK":
            redis_cmd("DEL", f"user:{user_id}")  # 清理本次写入的孤儿数据
            self._send_json(409, {"error": "该手机号已注册，请直接登录"})
            return

        token = os.urandom(32).hex()
        redis_cmd("SET", "session:" + token, user_id, "EX", "604800")
        print(f"[账号] 注册成功: {phone} ({role})")
        self._send_json(200, {"token": token, "user": {"userId": user_id, "phone": phone, "role": role, "displayName": display_name}})

    def _auth_login(self, data):
        phone = str(data.get("phone", "")).strip()
        password = str(data.get("password", ""))
        if not phone or not password:
            self._send_json(400, {"error": "请输入手机号和密码"})
            return

        fails = int((redis_cmd("GET", "login:fail:" + phone).get("result") or "0"))
        if fails >= 5:
            self._send_json(429, {"error": "失败次数过多，请10分钟后重试"})
            return

        user_id = redis_cmd("GET", "user:phone:" + phone).get("result")
        if not user_id:
            self._send_json(401, {"error": "手机号或密码错误"})
            return

        user = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}

        # 用户数据不完整（缺盐值或哈希）——僵尸账号：用本次输入的密码自动修复并直接登录
        if not user.get("salt") or not user.get("passHash"):
            salt = os.urandom(16).hex()
            pass_hash = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt), 10000
            ).hex()
            redis_cmd("HSET", f"user:{user_id}", "passHash", pass_hash, "salt", salt)
            redis_cmd("DEL", "login:fail:" + phone)

            # 写入后回读验证，防止静默丢失
            check = redis_cmd("HGET", f"user:{user_id}", "salt").get("result")
            if not check:
                self._send_json(500, {"error": "数据写入验证失败（Redis异常），请稍后重试"})
                return

            token = os.urandom(32).hex()
            redis_cmd("SET", "session:" + token, user_id, "EX", "604800")
            print(f"[账号] 自动修复僵尸账号: {phone}")
            self._send_json(200, {
                "token": token,
                "repaired": True,
                "user": {
                    "userId": user_id,
                    "phone": user.get("phone", phone),
                    "role": user.get("role", "patient"),
                    "displayName": user.get("displayName", "长辈"),
                },
            })
            return

        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(user.get("salt", "")), 10000
        ).hex()

        if computed != user.get("passHash"):
            cnt = redis_cmd("INCR", "login:fail:" + phone).get("result", 1)
            redis_cmd("EXPIRE", "login:fail:" + phone, "600")
            remaining = 5 - int(cnt)
            hint = f"（还可尝试{remaining}次）" if 0 < remaining <= 2 else ""
            self._send_json(401, {"error": "手机号或密码错误" + hint})
            return

        redis_cmd("DEL", "login:fail:" + phone)
        token = os.urandom(32).hex()
        redis_cmd("SET", "session:" + token, user_id, "EX", "604800")
        print(f"[账号] 登录成功: {phone}")
        self._send_json(200, {"token": token, "user": {"userId": user_id, "phone": user.get("phone"), "role": user.get("role"), "displayName": user.get("displayName")}})

    def _auth_resetpw(self, data):
        """重置密码（原型版：无短信验证，仅校验手机号已注册）"""
        phone = str(data.get("phone", "")).strip()
        password = str(data.get("password", ""))

        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return
        if len(password) < 6:
            self._send_json(400, {"error": "密码至少6位"})
            return

        user_id = redis_cmd("GET", "user:phone:" + phone).get("result")
        if not user_id:
            self._send_json(404, {"error": "该手机号尚未注册"})
            return

        salt = os.urandom(16).hex()
        pass_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), 10000
        ).hex()
        redis_cmd("HSET", f"user:{user_id}", "passHash", pass_hash, "salt", salt)

        # 写入后回读验证
        check = redis_cmd("HGET", f"user:{user_id}", "salt").get("result")
        if not check:
            self._send_json(500, {"error": "数据写入验证失败（Redis异常），请稍后重试"})
            return

        redis_cmd("DEL", "login:fail:" + phone)  # 重置成功即解除锁定

        print(f"[账号] 密码重置: {phone}")
        self._send_json(200, {"ok": True})

    def _auth_delete(self, data, token=""):
        """注销账号（校验手机号+密码后删除全部账号数据，不可恢复）"""
        phone = str(data.get("phone", "")).strip()
        password = str(data.get("password", ""))

        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return
        if not password:
            self._send_json(400, {"error": "请输入密码"})
            return

        # 防爆破：与登录共用失败计数
        fails = int((redis_cmd("GET", "login:fail:" + phone).get("result") or "0"))
        if fails >= 5:
            self._send_json(429, {"error": "失败次数过多，请10分钟后重试"})
            return

        user_id = redis_cmd("GET", "user:phone:" + phone).get("result")
        if not user_id:
            self._send_json(404, {"error": "该手机号尚未注册"})
            return

        user = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}
        if not user.get("salt") or not user.get("passHash"):
            self._send_json(409, {"error": "账号数据异常，请先用\"登录\"修复一次再注销"})
            return

        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(user.get("salt", "")), 10000
        ).hex()
        if computed != user.get("passHash"):
            cnt = redis_cmd("INCR", "login:fail:" + phone).get("result", 1)
            redis_cmd("EXPIRE", "login:fail:" + phone, "600")
            remaining = 5 - int(cnt)
            hint = f"（还可尝试{remaining}次）" if 0 < remaining <= 2 else ""
            self._send_json(401, {"error": "手机号或密码错误" + hint})
            return

        # 删除手机号映射 + 用户数据 + 失败计数 + 当前会话
        redis_cmd("DEL", "user:phone:" + phone)
        redis_cmd("DEL", f"user:{user_id}")
        redis_cmd("DEL", "login:fail:" + phone)
        if token:
            redis_cmd("DEL", "session:" + token)

        print(f"[账号] 注销账号: {phone}")
        self._send_json(200, {"ok": True})

    def _auth_diag(self, data):
        """账号诊断（只返回长度/存在性，不泄露盐值与哈希）"""
        phone = str(data.get("phone", "")).strip()
        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return

        user_id = redis_cmd("GET", "user:phone:" + phone).get("result")
        if not user_id:
            self._send_json(200, {"phone": phone, "exists": False})
            return

        user = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}
        fail = redis_cmd("GET", "login:fail:" + phone).get("result") or "0"

        self._send_json(200, {
            "phone": phone,
            "exists": True,
            "userId": user_id,
            "role": user.get("role", ""),
            "displayName": user.get("displayName", ""),
            "saltLen": len(user.get("salt", "")),
            "hashLen": len(user.get("passHash", "")),
            "failCount": int(fail),
        })

    def _auth_me(self, token):
        if not token:
            self._send_json(401, {"error": "未登录"})
            return
        user_id = redis_cmd("GET", "session:" + token).get("result")
        if not user_id:
            self._send_json(401, {"error": "登录已过期，请重新登录"})
            return
        user = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}

        # 账号已注销（用户数据为空）——会话作废
        if not user.get("salt"):
            redis_cmd("DEL", "session:" + token)
            self._send_json(401, {"error": "账号已注销或数据异常，请重新登录"})
            return

        redis_cmd("EXPIRE", "session:" + token, "604800")
        self._send_json(200, {"user": {"userId": user_id, "phone": user.get("phone"), "role": user.get("role"), "displayName": user.get("displayName")}})

    # ---------- 读取请求体 ----------
    def _read_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(content_length)

    # ---------- 从模型返回文本中提取 JSON ----------
    @staticmethod
    def _extract_json(text):
        # 去掉 markdown 代码块标记
        if text.startswith("```"):
            lines = text.split("\n", 1)
            if len(lines) > 1:
                text = lines[1]
            text = text.rsplit("```", 1)[0]

        # 找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # ---------- 工具方法 ----------
    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 简化日志，只打印非静态资源的请求
        msg = fmt % args
        if not (msg.startswith("GET /") and "favicon" not in msg):
            print(f"[{self.log_date_time_string()}] {msg}")


# ========== 启动 ==========
def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), YaoyiHandler)
    print("=" * 50)
    print("  药忆 H5 后端代理已启动")
    print(f"  模型: {MODEL_ID}")
    if UPSTASH_URL:
        print(f"  Upstash Redis: 已配置")
    else:
        print(f"  Upstash Redis: 未配置（配对/事件功能不可用）")
    print(f"  请在浏览器打开: http://localhost:{PORT}")
    print("  按 Ctrl+C 停止")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
