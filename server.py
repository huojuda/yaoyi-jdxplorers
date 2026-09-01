#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
药忆 H5 后端代理
- 静态文件服务（index.html 等）
- /api/recognize：调用火山方舟多模态大模型识别药盒
- API Key 仅存于后端，不暴露给前端

用法：
    python server.py
    浏览器打开 http://localhost:8000
"""

import json
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
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

# 静态文件根目录 = 当前目录（index.html 所在目录）
STATIC_DIR = str(Path(__file__).resolve().parent)


# ========== 请求处理 ==========
class YaoyiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_POST(self):
        if self.path == "/api/recognize":
            self._handle_recognize()
        else:
            self._send_json(404, {"error": "接口不存在"})

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
    server = HTTPServer(("0.0.0.0", PORT), YaoyiHandler)
    print("=" * 50)
    print("  药忆 H5 后端代理已启动")
    print(f"  模型: {MODEL_ID}")
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
