// EdgeOne Pages Edge Function: /api/recognize
// 药盒识别，调用火山方舟多模态大模型
// 环境变量：YAOWI_API_KEY / YAOWI_API_ENDPOINT / YAOWI_MODEL_ID

const RECOGNITION_PROMPT = '你是一个专业的药品识别助手。请仔细识别这张药品包装图片，提取以下信息并以严格的JSON格式返回：{"drug_name": "药品通用名（中文）", "brand_name": "商品名", "specification": "规格", "frequency": "服用频次", "timing": "服用时间", "meal_relation": "餐前/餐后/空腹/不限", "contraindication": "主要禁忌（一句话）", "confidence": 0.0}。如果图片中无法确定某个字段，填"未识别"。只返回JSON，不要其他文字。';

export default function onRequest(context) {
  const { request, env } = context;
  const method = request.method;

  if (method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  if (method !== 'POST') {
    return json(405, { error: 'Method Not Allowed' });
  }

  return (async () => {
    try {
      const data = await request.json();
      const imageData = data.image || '';

      if (!imageData) {
        return json(400, { error: '缺少图片数据' });
      }

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
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
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
