# Vercel Serverless Function: /api/recognize
# 部署后自动路由：前端 fetch('/api/recognize') → 此文件 handler()
# API Key 从 Vercel 环境变量读取（不进仓库，不暴露）
#
# 必需的环境变量（在 Vercel Dashboard → Settings → Environment Variables 设置）：
#   YAOWI_API_KEY     - 火山方舟 API Key
#   YAOWI_API_ENDPOINT - 火山方舟接口地址
#   YAOWI_MODEL_ID    - 模型ID

import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler


# 识别 Prompt（与本地 api_config.json 一致，硬编码避免依赖配置文件）
RECOGNITION_PROMPT = "你是一个专业的药品识别助手。请仔细识别这张药品包装图片，提取以下信息并以严格的JSON格式返回：{\"drug_name\": \"药品通用名（中文）\", \"brand_name\": \"商品名\", \"specification\": \"规格\", \"frequency\": \"服用频次\", \"timing\": \"服用时间\", \"meal_relation\": \"餐前/餐后/空腹/不限\", \"contraindication\": \"主要禁忌（一句话）\", \"confidence\": 0.0}。如果图片中无法确定某个字段，填\"未识别\"。只返回JSON，不要其他文字。"


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            # 1. 读取请求体
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            image_data = data.get("image", "")

            if not image_data:
                self._send_json(400, {"error": "缺少图片数据"})
                return

            # 2. 从环境变量读取配置
            api_key = os.environ.get("YAOWI_API_KEY", "")
            api_endpoint = os.environ.get("YAOWI_API_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
            model_id = os.environ.get("YAOWI_MODEL_ID", "doubao-seed-2-0-mini-260428")

            if not api_key:
                self._send_json(500, {"error": "服务器未配置 API Key"})
                return

            # 3. 构造火山方舟 API 请求（OpenAI 兼容格式）
            payload = {
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": RECOGNITION_PROMPT},
                            {"type": "image_url", "image_url": {"url": image_data}},
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            }

            req = urllib.request.Request(
                api_endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )

            # 4. 发送请求
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            # 5. 解析返回内容（提取 JSON）
            content = result["choices"][0]["message"]["content"].strip()
            drug_info = self._extract_json(content)

            if not drug_info:
                self._send_json(500, {"error": "模型返回格式解析失败", "raw": content})
                return

            self._send_json(200, drug_info)

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            self._send_json(502, {"error": f"API返回错误 {e.code}", "detail": err_body})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_OPTIONS(self):
        # CORS 预检
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Vercel 会自动收集日志，这里静默
        pass
