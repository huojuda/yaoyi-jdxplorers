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
import re
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

# AI 用药问答系统提示词（与 edge-functions/api/ask.js 保持一致）
ASK_SYSTEM_PROMPT = '''你是一位专业、耐心的老年用药顾问，服务对象是阿尔茨海默症患者及其家属。

回答规则：
1. 用大白话回答，避免专业术语，句子简短，适合老年人理解
2. 每次回答不超过150字
3. 涉及剂量调整、换药、停药、联合用药等医疗决策时，必须明确建议"请咨询医生或药师"，不要给出具体剂量调整方案
4. 如果问题与当前药品无关，可以简单回答并建议咨询医生
5. 回答末尾固定加上："以上为AI参考，具体请遵医嘱。"
6. 语气温暖、有同理心，像家人一样关心

请根据以下药品信息和用户问题给出回答。'''

# 联合用药深度分析系统提示词（结构化 JSON 输出）
ANALYZE_SYSTEM_PROMPT = '''你是一位专业、严谨的老年用药安全顾问，服务阿尔茨海默症患者及其家属。
任务：对患者同时使用的多种药物做联合用药安全分析。

规则：
1. 只基于公认的药品说明书、临床指南和明确的药物相互作用知识作答，不得编造药品或相互作用。
2. 重点识别：重复用药（同类成分叠加）、严重相互作用（如出血风险、低血糖、过度中枢抑制、QT间期延长等）、药物与酒精的禁忌、对老年人风险更高的组合。
3. 证据不足、不确定或不在你知识范围内的组合，不要硬判为危险，归入"注意"并建议咨询医生或药师。
4. 用老人听得懂的大白话，每条建议具体、可执行，不堆砌专业术语。
5. 只输出一个 JSON 对象，不要输出 markdown 代码块或任何多余文字，结构如下：
{
  "level": "safe|low|medium|high",
  "summary": "一句话总体结论，不超过40字，老人能听懂",
  "points": [
    {"severity": "danger|warning|caution|safe", "title": "简短标题", "advice": "给老人的具体建议，大白话，不超过50字"}
  ],
  "overallAdvice": "总体叮嘱，不超过60字"
}
level 判定标准：存在明确同服禁忌或严重相互作用为 high；存在需要监测、间隔或调整的相互作用为 medium；仅有轻微注意事项为 low；无明显相互作用为 safe。
points 最多 5 条，按严重程度从高到低排序；确无风险时给 1 条 severity 为 safe 的提示。overallAdvice 末尾需提醒具体用药遵医嘱。'''

# 防重复服药提醒话术（短、温和、纯口语，供 TTS 播报与卡片展示）
DUPLICATE_SYSTEM_PROMPT = '''你是一位温暖、耐心的老年照护顾问，正在提醒一位轻中度阿尔茨海默症老人不要重复服药。
要求：
1. 只用一到两句温和、口语化的中文，不超过60字，像家人轻声提醒，不要训斥、不要用专业术语。
2. 明确告诉老人：这个药今天已经吃过了、现在不用再吃；并给一个简单的动作建议（例如把药盒盖上、放到一边，不确定就问家人）。
3. 不要出现"AI""免责""遵医嘱""系统""JSON"等字样，只输出提醒话术本身。'''

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
        elif path == "/api/relation":
            self._handle_relation()
        elif path == "/api/ask":
            self._handle_ask()
        else:
            self._send_json(404, {"error": "接口不存在"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/events":
            self._handle_events_get()
        elif path == "/api/auth":
            self._handle_auth()
        elif path == "/api/relation":
            self._handle_relation()
        elif path == "/api/ask":
            self._handle_ask()
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

    # ---------- AI 用药问答 / 联合用药分析 ----------
    def _read_body_data(self):
        """读取 POST JSON/表单 或 GET query，返回 dict"""
        if self.command == "POST":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            if body:
                try:
                    return json.loads(body)
                except Exception:
                    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", errors="replace")).items()}
            return {}
        parsed = urlparse(self.path)
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def _ark_chat(self, system_prompt, user_prompt, temperature=0.3, max_tokens=300):
        """调用火山方舟对话模型，返回文本内容；失败抛异常（统一用 urllib，无需 requests）"""
        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            API_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + API_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _build_drug_context(drug_info):
        if not drug_info:
            return ""
        if isinstance(drug_info, str):
            try:
                drug_info = json.loads(drug_info)
            except Exception:
                drug_info = {}
        d = drug_info
        return (
            "\n当前药品信息：\n"
            f"- 药品名称：{d.get('name', '未知')}\n"
            f"- 商品名：{'、'.join(d.get('brand_names', []) or []) or '未知'}\n"
            f"- 类别：{d.get('plain_class') or d.get('drug_class') or '未知'}\n"
            f"- 用法：{d.get('frequency', '')}，{d.get('timing', '')}，{d.get('meal_relation', '')}\n"
            f"- 剂量：{d.get('dosage', '未知')}\n"
            f"- 禁忌：{d.get('contraindication', '')}\n"
            f"- 详细禁忌：{d.get('contraindication_detail', '')}\n"
            f"- 副作用：{d.get('side_effects', '')}\n"
            f"- 老人说明：{d.get('elderly_explanation', '')}\n"
        )

    def _handle_ask(self):
        try:
            data = self._read_body_data()

            # 联合用药深度分析模式
            if data.get("mode") == "analyze":
                self._ask_analyze(data)
                return

            # 防重复服药 AI 话术模式
            if data.get("mode") == "duplicate":
                self._ask_duplicate(data)
                return

            question = (data.get("question", "") or "").strip()
            drug_info = data.get("drugInfo", None)

            if not question:
                self._send_json(400, {"error": "缺少问题内容"})
                return

            if len(question) > 200:
                self._send_json(400, {"error": "问题太长"})
                return

            print(f"[AI问答] 问题: {question}")

            drug_context = self._build_drug_context(drug_info)
            user_prompt = f"{drug_context}\n用户问题：{question}" if drug_context else f"用户问题：{question}"

            try:
                answer = self._ark_chat(ASK_SYSTEM_PROMPT, user_prompt, 0.3, 300)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                self._send_json(502, {"error": f"AI服务返回错误 {e.code}", "detail": detail[:500]})
                return
            except Exception as e:
                print(f"[AI问答] 调用失败: {e}")
                self._send_json(502, {"error": f"AI服务调用失败: {e}"})
                return

            if "AI参考" not in answer and "遵医嘱" not in answer:
                answer += "\n\n以上为AI参考，具体请遵医嘱。"

            self._send_json(200, {"answer": answer, "question": question})

        except Exception as e:
            print(f"[AI问答] 错误: {e}")
            import traceback
            traceback.print_exc()
            self._send_json(500, {"error": str(e)})

    def _ask_analyze(self, data):
        drugs = data.get("drugs", [])
        if isinstance(drugs, str):
            try:
                drugs = json.loads(drugs)
            except Exception:
                drugs = drugs.split(",")
        if not isinstance(drugs, list):
            drugs = []
        drugs = [str(d).strip() for d in drugs if str(d).strip()][:12]
        if len(drugs) < 2:
            self._send_json(400, {"error": "至少需要两种药品才能分析"})
            return

        rule_hits = data.get("ruleHits")
        if isinstance(rule_hits, str):
            try:
                rule_hits = json.loads(rule_hits)
            except Exception:
                rule_hits = None

        rule_section = ""
        if isinstance(rule_hits, list) and rule_hits:
            lines = []
            for r in rule_hits[:8]:
                if not isinstance(r, dict):
                    continue
                sev = "严重" if r.get("severity") == "danger" else ("警告" if r.get("severity") == "warning" else "注意")
                pair = r.get("pair")
                if isinstance(pair, list):
                    pair_s = " + ".join(str(x) for x in pair)
                else:
                    pair_s = f"{r.get('a', '')} + {r.get('b', '')}"
                lines.append(f"- [{sev}] {pair_s}：{r.get('desc') or r.get('elderly') or ''}")
            if lines:
                rule_section = "\n前端内置规则引擎已命中以下相互作用（供你参考，需结合专业知识独立判断，不要照抄）：\n" + "\n".join(lines) + "\n"

        user_prompt = f"患者正在同时使用以下 {len(drugs)} 种药品/物质：{'、'.join(drugs)}。{rule_section}\n请按要求只输出 JSON。"
        print(f"[AI联合用药分析] {len(drugs)} 种: {'、'.join(drugs)}")

        try:
            content = self._ark_chat(ANALYZE_SYSTEM_PROMPT, user_prompt, 0.2, 700)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            # 分析失败不阻断页面，前端静默降级为仅展示规则结果
            self._send_json(200, {"analysis": None, "error": f"AI服务返回错误 {e.code}", "detail": detail})
            return
        except Exception as e:
            print(f"[AI联合用药分析] 调用失败: {e}")
            self._send_json(200, {"analysis": None, "error": "AI服务网络异常"})
            return

        analysis = self._extract_json(content)
        if (not analysis or analysis.get("level") not in ("safe", "low", "medium", "high")
                or not isinstance(analysis.get("points"), list)):
            self._send_json(200, {"analysis": None, "error": "AI返回格式解析失败"})
            return

        valid_sev = {"danger", "warning", "caution", "safe"}
        points = []
        for p in analysis["points"]:
            if not isinstance(p, dict) or p.get("severity") not in valid_sev:
                continue
            points.append({
                "severity": p["severity"],
                "title": str(p.get("title", ""))[:30],
                "advice": str(p.get("advice", ""))[:120],
            })
        points = points[:5]
        if not points:
            self._send_json(200, {"analysis": None, "error": "AI返回内容为空"})
            return

        analysis = {
            "level": analysis["level"],
            "summary": str(analysis.get("summary", ""))[:80],
            "points": points,
            "overallAdvice": str(analysis.get("overallAdvice", ""))[:120],
        }
        self._send_json(200, {"analysis": analysis, "drugs": drugs})

    def _ask_duplicate(self, data):
        drug_name = str(data.get("drugName", "这个药") or "这个药").strip()[:30]
        drug_class = str(data.get("drugClass", "") or "").strip()[:20]
        took_time = str(data.get("time", "") or "").strip()[:10]

        class_part = f"（{drug_class}）" if drug_class else ""
        time_part = f"，今天最近一次服药时间是 {took_time}" if took_time else ""
        user_prompt = f"药品：{drug_name}{class_part}{time_part}。老人现在又拿起这个药准备吃，请生成一句提醒话术。"
        print(f"[AI重复提醒] {drug_name} 最近服药 {took_time}")

        try:
            text = self._ark_chat(DUPLICATE_SYSTEM_PROMPT, user_prompt, 0.6, 120)
        except Exception as e:
            print(f"[AI重复提醒] 调用失败: {e}")
            # 失败不阻断，前端静默降级为固定警告
            self._send_json(200, {"text": None, "error": "AI服务异常"})
            return

        # 去掉残留引号、markdown
        text = text.strip().strip('"').strip("“").strip("”").replace("```", "").strip()
        if not text:
            self._send_json(200, {"text": None, "error": "AI返回为空"})
            return
        self._send_json(200, {"text": text[:120]})

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

    # ---------- 事件上报 (POST)：账号关联体系，事件挂在患者 userId 下 ----------
    def _handle_events_post(self):
        try:
            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            # 认证（URL 参数 + body 双通道）
            qs = parse_qs(urlparse(self.path).query)
            data = {k: v[0] for k, v in qs.items()}
            try:
                body = self._read_body()
                if body:
                    data.update(json.loads(body))
            except Exception:
                pass

            auth_header = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            token = auth_header or data.get("_auth", "")
            if not token:
                self._send_json(401, {"error": "请先登录"})
                return
            user_id = redis_cmd("GET", "session:" + token).get("result")
            if not user_id:
                self._send_json(401, {"error": "登录已过期，请重新登录"})
                return

            # 求助确认（家属端）
            if data.get("action") == "acknowledge":
                target_pid = str(data.get("targetPatientId", "")).strip()
                ts = str(data.get("ts", "")).strip()
                if not target_pid or not ts:
                    self._send_json(400, {"error": "缺少 targetPatientId 或 ts"})
                    return
                related = redis_cmd("SISMEMBER", f"relation:{user_id}", target_pid).get("result")
                if not related:
                    self._send_json(403, {"error": "未关联该患者"})
                    return
                redis_cmd("SET", f"user:{target_pid}:ack:{ts}", "1")
                ack_event = {
                    "type": "acknowledge",
                    "timestamp": int(time.time() * 1000),
                    "refTs": int(ts),
                    "patientId": target_pid,
                    "status": "acknowledged",
                }
                redis_cmd("RPUSH", f"user:{target_pid}:events", json.dumps(ack_event, ensure_ascii=False))
                redis_cmd("LTRIM", f"user:{target_pid}:events", -200, -1)
                self._send_json(200, {"ok": True})
                return

            # 正常事件上报（患者端）
            event_type = data.get("type", "")
            valid_types = {"medication", "alert", "monitor_end"}
            if event_type not in valid_types:
                self._send_json(400, {"error": "未知事件类型: " + event_type})
                return

            event = {
                "type": event_type,
                "drugId": data.get("drugId", ""),
                "drugName": data.get("drugName", ""),
                "date": data.get("date", time.strftime("%Y-%m-%d")),
                "time": data.get("time", ""),
                "timestamp": int(time.time() * 1000),
                "patientId": user_id,
                "status": "pending",
            }
            redis_cmd("RPUSH", f"user:{user_id}:events", json.dumps(event, ensure_ascii=False))
            redis_cmd("LTRIM", f"user:{user_id}:events", -200, -1)
            print(f"[事件] patient={user_id} type={event_type} drug={data.get('drugName', '')}")
            self._send_json(200, {"ok": True, "event": event})

        except Exception as e:
            print(f"[事件] POST 异常: {e}")
            self._send_json(500, {"error": str(e)})

    # ---------- 事件轮询 (GET)：家属端聚合所有关联患者的事件 ----------
    def _handle_events_get(self):
        try:
            if not UPSTASH_URL or not UPSTASH_TOKEN:
                self._send_json(500, {"error": "服务器未配置 Upstash Redis"})
                return

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            after = int(params.get("after", ["0"])[0])

            qs = parse_qs(urlparse(self.path).query)
            auth_header = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            token = auth_header or (qs.get("_auth", [""])[0])
            if not token:
                self._send_json(401, {"error": "请先登录"})
                return
            user_id = redis_cmd("GET", "session:" + token).get("result")
            if not user_id:
                self._send_json(401, {"error": "登录已过期，请重新登录"})
                return

            members = redis_cmd("SMEMBERS", f"relation:{user_id}").get("result") or []
            events = []
            acknowledged = []
            patient_phones = {}

            for pid in members:
                user = redis_cmd("HGETALL", f"user:{pid}").get("result") or {}
                patient_name = user.get("displayName", "")
                patient_phones[pid] = user.get("phone", "")

                raw_list = redis_cmd("LRANGE", f"user:{pid}:events", 0, -1).get("result") or []
                for item in raw_list:
                    try:
                        evt = json.loads(item)
                        if evt.get("timestamp", 0) > after:
                            evt["patientId"] = evt.get("patientId") or pid
                            evt["patientName"] = patient_name
                            events.append(evt)
                    except (json.JSONDecodeError, TypeError):
                        continue

                ack_keys = redis_cmd("KEYS", f"user:{pid}:ack:*").get("result") or []
                for key in ack_keys:
                    acknowledged.append(int(key.rsplit(":", 1)[-1]))

            events.sort(key=lambda e: e.get("timestamp", 0))
            self._send_json(200, {"events": events, "acknowledged": acknowledged, "patientPhones": patient_phones})

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
            elif action == "sendcode":
                self._auth_sendcode(data)
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
                self._send_json(400, {"error": "未知操作，应为 register/login/sendcode/resetpw/delete/diag/me/logout"})

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

            # 手机号已被占用：区分完整账号与损坏账号
            existing_id = redis_cmd("GET", "user:phone:" + phone).get("result")
            existing = redis_cmd("HGETALL", f"user:{existing_id}").get("result") or {} if existing_id else {}

            if not existing.get("salt") or not existing.get("passHash"):
                # 损坏账号（无密码数据，无法登录）：用本次提交的数据整体重建
                redis_cmd(
                    "HSET", f"user:{existing_id}",
                    "phone", phone,
                    "passHash", pass_hash,
                    "salt", salt,
                    "role", role,
                    "displayName", display_name,
                )
                if not redis_cmd("HGET", f"user:{existing_id}", "salt").get("result"):
                    self._send_json(500, {"error": "数据写入验证失败（Redis异常），请稍后重试"})
                    return
                token = os.urandom(32).hex()
                redis_cmd("SET", "session:" + token, existing_id, "EX", "604800")
                print(f"[账号] 损坏账号重建: {phone} ({role})")
                self._send_json(200, {"token": token, "repaired": True, "user": {"userId": existing_id, "phone": phone, "role": role, "displayName": display_name}})
                return

            if not existing.get("role") or not existing.get("displayName") or existing.get("role") != role:
                # 身份缺失，或与本次提交不一致：验证密码后按本次提交修正（密码即本人凭证，不提升权限）
                computed = hashlib.pbkdf2_hmac(
                    "sha256", password.encode(), bytes.fromhex(existing["salt"]), 10000
                ).hex()
                if computed != existing["passHash"]:
                    self._send_json(401, {"error": "该手机号已注册且密码不匹配。请输入当前密码来修正身份，或直接登录"})
                    return
                redis_cmd("HSET", f"user:{existing_id}", "role", role, "displayName", display_name)
                token = os.urandom(32).hex()
                redis_cmd("SET", "session:" + token, existing_id, "EX", "604800")
                print(f"[账号] 身份字段补全: {phone} ({role})")
                self._send_json(200, {"token": token, "repaired": True, "user": {"userId": existing_id, "phone": phone, "role": role, "displayName": display_name}})
                return

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

    def _auth_sendcode(self, data):
        """发送密码重置验证码（原型版无短信网关，验证码在响应 demoCode 中直接返回）"""
        phone = str(data.get("phone", "")).strip()
        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return

        user_id = redis_cmd("GET", "user:phone:" + phone).get("result")
        if not user_id:
            self._send_json(404, {"error": "该手机号尚未注册"})
            return

        # 重发冷却
        cd = redis_cmd("GET", "resetcode:cd:" + phone).get("result")
        if cd:
            self._send_json(429, {"error": f"验证码已发送，请{cd}秒后再试", "retryAfter": int(cd)})
            return

        # 每小时申请次数上限
        n = int(redis_cmd("INCR", "resetcode:n:" + phone).get("result", "1"))
        if n == 1:
            redis_cmd("EXPIRE", "resetcode:n:" + phone, "3600")
        if n > 5:
            self._send_json(429, {"error": "请求过于频繁，请1小时后再试"})
            return

        code = f"{random.randint(0, 999999):06d}"
        redis_cmd("SET", "resetcode:" + phone, code, "EX", "300")
        redis_cmd("SET", "resetcode:cd:" + phone, "60", "EX", "60")
        redis_cmd("DEL", "resetcode:fail:" + phone)
        print(f"[账号] 重置验证码已发送: {phone} → {code}（演示环境）")
        self._send_json(200, {
            "ok": True,
            "demoCode": code,  # 仅原型演示：生产环境删除，改走短信
            "expiresIn": 300,
            "cooldown": 60,
            "note": "原型演示环境，验证码直接展示；正式版将通过短信下发",
        })

    def _auth_resetpw(self, data):
        """重置密码（须校验6位验证码）"""
        phone = str(data.get("phone", "")).strip()
        password = str(data.get("password", ""))
        code = str(data.get("code", "")).strip()

        if not (phone.startswith("1") and len(phone) == 11 and phone.isdigit()):
            self._send_json(400, {"error": "请输入11位手机号"})
            return
        if len(password) < 6:
            self._send_json(400, {"error": "密码至少6位"})
            return
        if not re.match(r"^\d{6}$", code):
            self._send_json(400, {"error": "请输入6位验证码"})
            return

        saved_code = redis_cmd("GET", "resetcode:" + phone).get("result")
        if not saved_code:
            self._send_json(400, {"error": "验证码已过期，请重新获取"})
            return

        fail_key = "resetcode:fail:" + phone
        fails = int(redis_cmd("GET", fail_key).get("result") or "0")
        if fails >= 5:
            redis_cmd("DEL", "resetcode:" + phone, fail_key)
            self._send_json(429, {"error": "验证码错误次数过多，请重新获取"})
            return

        if saved_code != code:
            cnt = int(redis_cmd("INCR", fail_key).get("result", "1"))
            if cnt == 1:
                redis_cmd("EXPIRE", fail_key, "300")
            remaining = 5 - cnt
            hint = f"，还可尝试{remaining}次" if remaining > 0 else ""
            self._send_json(400, {"error": "验证码不正确" + hint})
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

        # 验证码一次性使用，清理冷却/失败计数/登录锁定
        redis_cmd("DEL", "resetcode:" + phone, "resetcode:cd:" + phone, fail_key, "login:fail:" + phone)

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
            # 僵尸账号（数据损坏且无法恢复）无法验证密码：直接清理，避免死锁
            redis_cmd("DEL", "user:phone:" + phone)
            redis_cmd("DEL", f"user:{user_id}")
            redis_cmd("DEL", "login:fail:" + phone)
            if token:
                redis_cmd("DEL", "session:" + token)
            print(f"[账号] 注销僵尸账号: {phone}")
            self._send_json(200, {"ok": True, "wasZombie": True})
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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 账号关联 ----------
    def _handle_relation(self):
        try:
            qs = parse_qs(urlparse(self.path).query)
            data = {k: v[0] for k, v in qs.items()}
            try:
                body = self._read_body()
                if body:
                    data.update(json.loads(body))
            except Exception:
                pass

            action = data.get("action", "")
            auth_header = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            token = auth_header or data.get("_auth", "")
            if not token:
                self._send_json(401, {"error": "请先登录"})
                return

            user_id = redis_cmd("GET", "session:" + token).get("result")
            if not user_id:
                self._send_json(401, {"error": "登录已过期"})
                return

            if action == "gen":
                code = str(random.randint(10000000, 99999999))
                user = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}
                redis_cmd("SET", f"bindCode:{code}", json.dumps({
                    "userId": user_id, "role": user.get("role"), "createdAt": int(time.time() * 1000)
                }), "EX", "120")
                self._send_json(200, {"code": code, "expiresIn": 120, "role": user.get("role"), "displayName": user.get("displayName")})

            elif action == "bind":
                code = str(data.get("code", "")).strip()
                if not re.match(r"^\d{8}$", code):
                    self._send_json(400, {"error": "请输入8位绑定码"})
                    return
                # 防爆破：按发起绑定的账号计数，连续失败5次锁定5分钟
                fail_key = f"bindFail:{user_id}"
                fail_count = int(redis_cmd("GET", fail_key).get("result") or "0")
                if fail_count >= 5:
                    self._send_json(429, {"error": "绑定失败次数过多，请5分钟后再试"})
                    return
                raw = redis_cmd("GET", f"bindCode:{code}").get("result")
                if not raw:
                    cnt = int(redis_cmd("INCR", fail_key).get("result", "1"))
                    if cnt == 1:
                        redis_cmd("EXPIRE", fail_key, "300")
                    remaining = 5 - cnt
                    hint = f"（还可尝试{remaining}次）" if remaining > 0 else ""
                    self._send_json(404, {"error": "绑定码无效或已过期" + hint})
                    return
                info = json.loads(raw)
                patient_id = info["userId"]
                if patient_id == user_id:
                    self._send_json(400, {"error": "不能绑定自己"})
                    return
                caregiver = redis_cmd("HGETALL", f"user:{user_id}").get("result") or {}
                patient = redis_cmd("HGETALL", f"user:{patient_id}").get("result") or {}
                if caregiver.get("role") != "caregiver":
                    self._send_json(403, {"error": "仅家人账号可发起绑定"})
                    return
                if patient.get("role") != "patient":
                    self._send_json(403, {"error": "对方不是患者账号"})
                    return
                already = redis_cmd("SISMEMBER", f"relation:{user_id}", patient_id).get("result")
                if already:
                    redis_cmd("DEL", f"bindCode:{code}", fail_key)
                    self._send_json(200, {"ok": True, "already": True})
                    return
                redis_cmd("SADD", f"relation:{user_id}", patient_id)
                redis_cmd("SADD", f"relation:{patient_id}", user_id)
                redis_cmd("DEL", f"bindCode:{code}", fail_key)
                self._send_json(200, {"ok": True, "patient": {
                    "userId": patient_id, "displayName": patient.get("displayName"),
                    "phone": patient.get("phone"), "role": patient.get("role"),
                }})

            elif action == "list":
                members = redis_cmd("SMEMBERS", f"relation:{user_id}").get("result") or []
                results = []
                for mid in members:
                    u = redis_cmd("HGETALL", f"user:{mid}").get("result") or {}
                    if u:
                        results.append({"userId": mid, "displayName": u.get("displayName"),
                                        "phone": u.get("phone"), "role": u.get("role")})
                self._send_json(200, {"list": results})

            elif action == "unbind":
                target = str(data.get("targetUserId", "")).strip()
                if not target:
                    self._send_json(400, {"error": "缺少 targetUserId"})
                    return
                redis_cmd("SREM", f"relation:{user_id}", target)
                redis_cmd("SREM", f"relation:{target}", user_id)
                self._send_json(200, {"ok": True})

            elif action == "pending":
                self._send_json(200, {"pending": []})

            else:
                self._send_json(400, {"error": "action 应为 gen/bind/list/unbind/pending"})

        except Exception as e:
            print(f"[关联] 异常: {e}")
            self._send_json(500, {"error": str(e)})

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
