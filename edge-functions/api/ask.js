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

// 联合用药深度分析（结构化 JSON 输出）
const ANALYZE_SYSTEM_PROMPT = `你是一位专业、严谨的老年用药安全顾问，服务阿尔茨海默症患者及其家属。
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
points 最多 5 条，按严重程度从高到低排序；确无风险时给 1 条 severity 为 safe 的提示。overallAdvice 末尾需提醒具体用药遵医嘱。`;

// 防重复服药提醒话术（短、温和、纯口语，供 TTS 播报与卡片展示）
const DUPLICATE_SYSTEM_PROMPT = `你是一位温暖、耐心的老年照护顾问，正在提醒一位轻中度阿尔茨海默症老人不要重复服药。
要求：
1. 只用一到两句温和、口语化的中文，不超过60字，像家人轻声提醒，不要训斥、不要用专业术语。
2. 明确告诉老人：这个药今天已经吃过了、现在不用再吃；并给一个简单的动作建议（例如把药盒盖上、放到一边，不确定就问家人）。
3. 不要出现"AI""免责""遵医嘱""系统""JSON"等字样，只输出提醒话术本身。`;

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

      // 联合用药深度分析模式（结构化 JSON）
      if (data.mode === 'analyze') {
        let drugs = data.drugs || [];
        if (typeof drugs === 'string') {
          try { drugs = JSON.parse(drugs); } catch (e) { drugs = drugs.split(','); }
        }
        let ruleHits = data.ruleHits || null;
        if (typeof ruleHits === 'string') {
          try { ruleHits = JSON.parse(ruleHits); } catch (e) { ruleHits = null; }
        }
        drugs = Array.isArray(drugs) ? drugs.map(d => String(d).trim()).filter(Boolean).slice(0, 12) : [];
        if (drugs.length < 2) {
          return json(400, { error: '至少需要两种药品才能分析' });
        }
        return await analyzeCombo(env, drugs, Array.isArray(ruleHits) ? ruleHits.slice(0, 8) : null);
      }

      // 防重复服药 AI 话术
      if (data.mode === 'duplicate') {
        const drugName = String(data.drugName || '这个药').trim().slice(0, 30);
        const drugClass = String(data.drugClass || '').trim().slice(0, 20);
        const tookTime = String(data.time || '').trim().slice(0, 10);
        return await duplicateTalk(env, drugName, drugClass, tookTime);
      }

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

// ---------- 防重复服药 AI 话术 ----------
async function duplicateTalk(env, drugName, drugClass, tookTime) {
  const apiKey = env.YAOWI_API_KEY || '';
  const apiEndpoint = env.YAOWI_API_ENDPOINT || 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
  const modelId = env.YAOWI_MODEL_ID || 'doubao-seed-2-0-mini-260428';

  if (!apiKey) {
    return json(500, { error: '服务器未配置 API Key' });
  }

  const classPart = drugClass ? `（${drugClass}）` : '';
  const timePart = tookTime ? `，今天最近一次服药时间是 ${tookTime}` : '';
  const userPrompt = `药品：${drugName}${classPart}${timePart}。老人现在又拿起这个药准备吃，请生成一句提醒话术。`;

  const payload = {
    model: modelId,
    messages: [
      { role: 'system', content: DUPLICATE_SYSTEM_PROMPT },
      { role: 'user', content: userPrompt },
    ],
    temperature: 0.6,
    max_tokens: 120,
  };

  try {
    const resp = await fetch(apiEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      return json(200, { text: null, error: 'AI服务返回错误 ' + resp.status });
    }
    const result = await resp.json();
    let text = (result.choices && result.choices[0] && result.choices[0].message && result.choices[0].message.content || '').trim();
    // 去掉可能残留的引号、markdown
    text = text.replace(/^["“”]+|["“”]+$/g, '').replace(/```/g, '').trim();
    if (!text) {
      return json(200, { text: null, error: 'AI返回为空' });
    }
    return json(200, { text: text.slice(0, 120) });
  } catch (e) {
    return json(200, { text: null, error: 'AI服务网络异常' }); // 前端静默降级到固定警告
  }
}

// ---------- 联合用药深度分析 ----------
async function analyzeCombo(env, drugs, ruleHits) {
  const apiKey = env.YAOWI_API_KEY || '';
  const apiEndpoint = env.YAOWI_API_ENDPOINT || 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
  const modelId = env.YAOWI_MODEL_ID || 'doubao-seed-2-0-mini-260428';

  if (!apiKey) {
    return json(500, { error: '服务器未配置 API Key' });
  }

  let ruleSection = '';
  if (ruleHits && ruleHits.length) {
    const lines = ruleHits.map(r => {
      const sev = r.severity === 'danger' ? '严重' : r.severity === 'warning' ? '警告' : '注意';
      const pair = Array.isArray(r.pair) ? r.pair.join(' + ') : `${r.a || ''} + ${r.b || ''}`;
      return `- [${sev}] ${pair}：${r.desc || r.elderly || ''}`;
    });
    ruleSection = `\n前端内置规则引擎已命中以下相互作用（供你参考，需结合你的专业知识独立判断，不要照抄）：\n${lines.join('\n')}\n`;
  }

  const userPrompt = `患者正在同时使用以下 ${drugs.length} 种药品/物质：${drugs.join('、')}。${ruleSection}\n请按要求只输出 JSON。`;

  const payload = {
    model: modelId,
    messages: [
      { role: 'system', content: ANALYZE_SYSTEM_PROMPT },
      { role: 'user', content: userPrompt },
    ],
    temperature: 0.2,
    max_tokens: 700,
  };

  let resp;
  try {
    resp = await fetch(apiEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey,
      },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    return json(200, { analysis: null, error: 'AI服务网络异常' }); // 前端静默降级到规则结果
  }

  if (!resp.ok) {
    const detail = await resp.text().catch(() => '');
    return json(200, { analysis: null, error: 'AI服务返回错误 ' + resp.status, detail: detail.slice(0, 300) });
  }

  const result = await resp.json();
  const content = (result.choices && result.choices[0] && result.choices[0].message && result.choices[0].message.content || '').trim();
  const analysis = extractJson(content);

  // 结构校验与归一化
  if (!analysis || !['safe', 'low', 'medium', 'high'].includes(analysis.level) || !Array.isArray(analysis.points)) {
    return json(200, { analysis: null, error: 'AI返回格式解析失败' });
  }
  analysis.points = analysis.points
    .filter(p => p && ['danger', 'warning', 'caution', 'safe'].includes(p.severity))
    .slice(0, 5)
    .map(p => ({
      severity: p.severity,
      title: String(p.title || '').slice(0, 30),
      advice: String(p.advice || '').slice(0, 120),
    }));
  if (!analysis.points.length) {
    return json(200, { analysis: null, error: 'AI返回内容为空' });
  }
  analysis.summary = String(analysis.summary || '').slice(0, 80);
  analysis.overallAdvice = String(analysis.overallAdvice || '').slice(0, 120);

  return json(200, { analysis, drugs });
}

function extractJson(text) {
  if (!text) return null;
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
