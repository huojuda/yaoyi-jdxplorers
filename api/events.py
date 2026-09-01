# Vercel Serverless Function: /api/events
# 事件上报与轮询，实现患者端→监护人端的实时通信
#
# 必需的环境变量（与 api/pair.py 相同）：
#   UPSTASH_REDIS_URL   - Upstash Redis REST API 地址
#   UPSTASH_REDIS_TOKEN  - Upstash Redis REST API Token
#
# 接口说明：
#   POST /api/events  { familyId, type, drugName?, time? }        → 上报事件
#   POST /api/events  { action: "acknowledge", familyId, ts }     → 确认异常已处理
#   GET  /api/events?familyId=xxx&after=0                          → 轮询拉取事件
#
# 事件类型：
#   medication   — 患者确认服药，触发监护人通知 + 30分钟监视期
#   alert        — 患者按下求助按钮，监护人端立即弹窗
#   monitor_end  — 30分钟监视期自然结束
#   acknowledge  — 监护人确认已处理异常

import json
import os
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# 每个家庭最多保留的事件数量
MAX_EVENTS = 200


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


class handler(BaseHTTPRequestHandler):

    # ---------- POST：上报事件 / 确认异常 ----------
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            # 特殊动作：确认异常
            if data.get("action") == "acknowledge":
                self._handle_acknowledge(data)
                return

            # 正常事件上报
            self._handle_report(data)

        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON"})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ---------- GET：监护人轮询拉取事件 ----------
    def do_GET(self):
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

            # 从 Redis 读取事件列表
            result = redis_cmd("LRANGE", "family:" + family_id + ":events", 0, -1)
            raw_list = result.get("result", [])

            events = []
            for item in raw_list:
                try:
                    evt = json.loads(item)
                    # 只返回客户端尚未见过的事件（timestamp > after）
                    if evt.get("timestamp", 0) > after:
                        events.append(evt)
                except (json.JSONDecodeError, TypeError):
                    continue

            # 同时读取已确认的异常标记
            ack_result = redis_cmd("KEYS", "family:" + family_id + ":ack:*")
            ack_keys = ack_result.get("result", [])
            acknowledged = []
            for key in ack_keys:
                # key 格式: family:{id}:ack:{timestamp}
                ts = key.rsplit(":", 1)[-1]
                acknowledged.append(int(ts))

            self._send_json(200, {
                "events": events,
                "acknowledged": acknowledged,
            })

        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ---------- 上报事件 ----------
    def _handle_report(self, data):
        family_id = data.get("familyId", "")
        event_type = data.get("type", "")

        if not family_id or not event_type:
            self._send_json(400, {"error": "缺少 familyId 或 type"})
            return

        # 合法事件类型
        valid_types = {"medication", "alert", "monitor_end"}
        if event_type not in valid_types:
            self._send_json(400, {"error": "未知事件类型: " + event_type})
            return

        now_ms = int(time.time() * 1000)
        event = {
            "type": event_type,
            "drugName": data.get("drugName", ""),
            "time": data.get("time", ""),
            "timestamp": now_ms,
            "status": "pending",
        }

        # 写入 Redis 列表（RPUSH 追加到末尾）
        redis_cmd(
            "RPUSH",
            "family:" + family_id + ":events",
            json.dumps(event, ensure_ascii=False),
        )
        # 裁剪，只保留最近 MAX_EVENTS 条
        redis_cmd("LTRIM", "family:" + family_id + ":events", -MAX_EVENTS, -1)

        self._send_json(200, {"ok": True, "event": event})

    # ---------- 确认异常已处理 ----------
    def _handle_acknowledge(self, data):
        family_id = data.get("familyId", "")
        ts = data.get("ts", 0)

        if not family_id or not ts:
            self._send_json(400, {"error": "缺少 familyId 或 ts"})
            return

        # 标记该异常已被确认
        redis_cmd("SET", "family:" + family_id + ":ack:" + str(ts), "1")
        # 同时上报一条 acknowledge 事件（让其他设备也同步）
        ack_event = {
            "type": "acknowledge",
            "timestamp": int(time.time() * 1000),
            "refTs": ts,
            "status": "acknowledged",
        }
        redis_cmd(
            "RPUSH",
            "family:" + family_id + ":events",
            json.dumps(ack_event, ensure_ascii=False),
        )

        self._send_json(200, {"ok": True})

    # ---------- CORS 预检 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
