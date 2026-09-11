// EdgeOne Pages Edge Function: /api/ask
// AI 用药问答助手 - 基于当前药品信息回答用户的个性化用药问题
// 环境变量：YAOWI_API_KEY / YAOWI_API_ENDPOINT / YAOWI_MODEL_ID

const SYSTEM_PROMPT = `你是一位专业、耐心的老年用药顾问，服务对象是阿尔茨海默症患者及其家属。

回答规则：
1. 用大白话回答，避免专业术语，句子简短，适合老年人理解
2. 每次回答不超过150字
3. 涉及剂量调整、换药、停药、联合用药等医疗决策时，必须明确建议"请咨询医生或药师"，不要给出具体剂量调整方案
4. 如果问题与当前药品无关，可以简单回答并建议咨询医生
5. 回答末尾固定加上："以上为AI参考，具体请遵医嘱。"
6. 语气温暖、有同理心，像家人一样关心

请根据以下药品信息和用户问题给出回答。`;

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
      // 读取请求体
      let bodyData = {};
      try {
        const bodyText = await request.text();
        if (bodyText) {
          try { bodyData = JSON.parse(bodyText); }
          catch (e) {
            const fp = new URLSearchParams(bodyText);
            bodyData = Object.fromEntries(fp.entries());
          }
        }
      } catch (e) {}

      // 也支持 GET 参数
      const urlParams = new URL(request.url).searchParams;
      const data = { ...Object.fromEntries(urlParams.entries()), ...bodyData };

      const question = (data.question || '').trim();
      const drugInfo = data.drugInfo || null;

      if (!question) {
        return json(400, { error: '缺少问题内容' });
      }

      if (question.length > 200) {
        return json(400, { error: '问题太长，请简短描述' });
      }

      return await askAI(env, question, drugInfo);
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
}

async function askAI(env, question, drugInfo) {
  const apiKey = env.YAOWI_API_KEY || '';
  const apiEndpoint = env.YAOWI_API_ENDPOINT || 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
  const modelId = env.YAOWI_MODEL_ID || 'doubao-seed-2-0-mini-260428';

  if (!apiKey) {
    return json(500, { error: '服务器未配置 API Key' });
  }

  // 构建药品信息上下文
  let drugContext = '';
  if (drugInfo) {
    const d = typeof drugInfo === 'string' ? JSON.parse(drugInfo) : drugInfo;
    drugContext = `
当前药品信息：
- 药品名称：${d.name || '未知'}
- 商品名：${(d.brand_names || []).join('、') || '未知'}
- 类别：${d.plain_class || d.drug_class || '未知'}
- 用法：${d.frequency || ''}，${d.timing || ''}，${d.meal_relation || ''}
- 剂量：${d.dosage || '未知'}
- 禁忌：${d.contraindication || ''}
- 详细禁忌：${d.contraindication_detail || ''}
- 副作用：${d.side_effects || ''}
- 老人说明：${d.elderly_explanation || ''}
`;
  }

  const userPrompt = `${drugContext ? drugContext + '\n' : ''}用户问题：${question}`;

  const payload = {
    model: modelId,
    messages: [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: userPrompt },
    ],
    temperature: 0.3,
    max_tokens: 300,
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
    return json(502, { error: 'AI服务返回错误 ' + resp.status, detail });
  }

  const result = await resp.json();
  let answer = result.choices[0].message.content.trim();

  // 确保末尾有免责声明
  if (!answer.includes('AI参考') && !answer.includes('遵医嘱')) {
    answer += '\n\n以上为AI参考，具体请遵医嘱。';
  }

  return json(200, { answer, question });
}

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'no-store, no-cache, must-revalidate',
    },
  });
}
