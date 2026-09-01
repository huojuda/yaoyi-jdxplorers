# Vercel Serverless Function: /api/pair
# 配对码生成与验证，实现患者端与监护人端的跨设备绑定
#
# 必需的环境变量（在 Vercel Dashboard → Settings → Environment Variables 设置）：
#   UPSTASH_REDIS_URL   - Upstash Redis REST API 地址（如 https://xxx.upstash.io）
#   UPSTASH_REDIS_TOKEN  - Upstash Redis REST API Token
#
# 接口说明：
#   POST /api/pair  action="create"                    → 生成6位配对码，返回 {code, familyId}
#   POST /api/pair  action="verify", code, phone       → 验证配对码，返回 {familyId, phone}

import json
import os
import random
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def redis_cmd(*args):
    """通过 Upstash REST API 执行 Redis 命令（数组格式）"""
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


def gen_pair_code():
    """生成6位数字配对码"""
    return str(random.randint(100000, 999999))


def gen_family_id():
    """生成家庭唯一ID"""
    return "fam_" + str(random.randint(10000000, 99999999))


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            # 1. 读取请求体
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            action = data.get("action", "")

            # 2. 检查 Upstash 配置
            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            # 3. 路由处理
            if action == "create":
                self._handle_create(data)
            elif action == "verify":
                self._handle_verify(data)
            else:
                self._send_json(400, {"error": "未知操作，应为 create 或 verify"})

        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON"})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ---------- 生成配对码 ----------
    def _handle_create(self, data):
        code = gen_pair_code()
        family_id = gen_family_id()

        # 存入 Redis：pair:code:{code} → familyId，5分钟过期
        result = redis_cmd("SET", "pair:code:" + code, family_id, "EX", 300)

        if result.get("result") != "OK":
            self._send_json(500, {"error": "配对码生成失败"})
            return

        self._send_json(200, {
            "code": code,
            "familyId": family_id,
            "expiresIn": 300,
        })

    # ---------- 验证配对码 ----------
    def _handle_verify(self, data):
        code = data.get("code", "").strip()
        phone = data.get("phone", "").strip()

        if not code or not phone:
            self._send_json(400, {"error": "请填写配对码和联系电话"})
            return

        # 查询配对码对应的 familyId
        result = redis_cmd("GET", "pair:code:" + code)
        family_id = result.get("result")

        if not family_id:
            self._send_json(400, {"error": "配对码无效或已过期，请重新生成"})
            return

        # 存储监护人电话号码
        redis_cmd("SET", "family:" + family_id + ":phone", phone)
        # 删除已使用的配对码（一次性）
        redis_cmd("DEL", "pair:code:" + code)

        self._send_json(200, {
            "familyId": family_id,
            "phone": phone,
        })

    # ---------- CORS 预检 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
        pass
