// EdgeOne Pages Edge Function: /api/recognize
// 药盒识别，调用火山方舟多模态大模型 - 兼容所有 HTTP 方法
// 环境变量：YAOWI_API_KEY / YAOWI_API_ENDPOINT / YAOWI_MODEL_ID
//
// 双通道设计：
// 1) POST 直传：请求体携带 {image: dataURL}（本地/自定义域名下可用）
// 2) GET 分片通道：EdgeOne 预览链接会丢弃大体积 POST body，此时前端把
//    dataURL 切成小片，通过 GET ?up=1&sid=..&i=..&n=..&d=.. 逐片存入 Redis，
//    最后一片触发组装并调用模型（GET 通道已被配对/事件/账号接口验证可靠）

const RECOGNITION_PROMPT = '你是一个专业的药品识别助手。请仔细识别这张药品包装图片，提取以下信息并以严格的JSON格式返回：{"drug_name": "药品通用名（中文）", "brand_name": "商品名", "specification": "规格", "frequency": "服用频次", "timing": "服用时间", "meal_relation": "餐前/餐后/空腹/不限", "contraindication": "主要禁忌（一句话）", "confidence": 0.0}。如果图片中无法确定某个字段，填"未识别"。只返回JSON，不要其他文字。';

export default function onRequest(context) {
  const { request, env } = context;

  // CORS 预检
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  return (async () => {
    try {
      const urlParams = new URL(request.url).searchParams;
      const data = { ...Object.fromEntries(urlParams.entries()) };

      // 读取请求体（记录长度用于诊断：EdgeOne 超限时会静默丢弃 body）
      let bodyLen = -1;
      let bodyCt = request.headers.get('content-type') || '无';
      let bodyData = {};
      try {
        const bodyText = await request.text();
        bodyLen = bodyText.length;
        if (bodyText) {
          try { bodyData = JSON.parse(bodyText); }
          catch (e) {
            const fp = new URLSearchParams(bodyText);
            bodyData = Object.fromEntries(fp.entries());
          }
        }
      } catch (e) { bodyLen = -1; }
      Object.assign(data, bodyData);

      const hasRedis = !!(env.UPSTASH_REDIS_REST_URL && env.UPSTASH_REDIS_REST_TOKEN);

      // ---- GET 分片上传通道 ----
      if (data.up === '1') {
        if (!hasRedis) return json(500, { error: '分片通道需要 Upstash Redis 配置' });
        return await handleChunk(env, data);
      }

      const imageData = data.image || '';
      if (!imageData) {
        return json(400, { error: '缺少图片数据(收到' + bodyLen + '字符,CT:' + bodyCt + ')' });
      }

      return await runRecognition(env, imageData);
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
}

// ---------- 分片上传：单片存储，最后一片组装识别 ----------
async function handleChunk(env, data) {
  const sid = String(data.sid || '').replace(/[^a-zA-Z0-9]/g, '').slice(0, 40);
  const idx = parseInt(data.i, 10);
  const total = parseInt(data.n, 10);
  if (!sid || isNaN(idx) || isNaN(total) || total < 1 || total > 300 || idx < 0 || idx >= total) {
    return json(400, { error: '分片参数无效' });
  }
  const piece = String(data.d || '');
  if (!piece) return json(400, { error: '分片数据为空' });

  await redisCmd(env, 'SET', 'recq:' + sid + ':' + idx, piece, 'EX', '180');

  if (idx < total - 1) {
    return json(200, { ok: true, got: idx });
  }

  // 最后一片：组装全部分片
  const keys = [];
  for (let k = 0; k < total; k++) keys.push('recq:' + sid + ':' + k);
  const got = await redisCmd(env, 'MGET', ...keys);
  const arr = got.result || [];
  let imageData = '';
  let missing = 0;
  for (let k = 0; k < total; k++) {
    if (arr[k]) imageData += arr[k];
    else missing++;
  }
  redisCmd(env, 'DEL', ...keys).catch(() => {});

  if (missing > 0) {
    return json(400, { error: '分片缺失' + missing + '片，请重试' });
  }
  return await runRecognition(env, imageData);
}

// ---------- 调用火山方舟识别 ----------
async function runRecognition(env, imageData) {
  const apiKey = env.YAOWI_API_KEY || '';
  const apiEndpoint = env.YAOWI_API_ENDPOINT || 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
  const modelId = env.YAOWI_MODEL_ID || 'doubao-seed-2-0-mini-260428';

  if (!apiKey) {
    return json(500, { error: '服务器未配置 API Key' });
  }

  const payload = {
    model: modelId,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: RECOGNITION_PROMPT },
          { type: 'image_url', image_url: { url: imageData } },
        ],
      },
    ],
    temperature: 0.1,
    max_tokens: 500,
  };

  const resp = await fetch(apiEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + apiKey,
    },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const detail = await resp.text();
    return json(502, { error: 'API返回错误 ' + resp.status, detail });
  }

  const result = await resp.json();
  const content = result.choices[0].message.content.trim();
  const drugInfo = extractJson(content);

  if (!drugInfo) {
    return json(500, { error: '模型返回格式解析失败', raw: content });
  }

  return json(200, drugInfo);
}

async function redisCmd(env, ...args) {
  const resp = await fetch(env.UPSTASH_REDIS_REST_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + env.UPSTASH_REDIS_REST_TOKEN,
    },
    body: JSON.stringify(args),
  });
  const d = await resp.json();
  if (!resp.ok) {
    throw new Error('Redis ' + resp.status + ': ' + (d && d.error || 'unknown'));
  }
  return d;
}

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

function extractJson(text) {
  if (text.startsWith('```')) {
    const lines = text.split('\n');
    lines.shift();
    text = lines.join('\n');
    text = text.split('```')[0];
  }
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start >= 0 && end > start) {
    text = text.slice(start, end + 1);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}
