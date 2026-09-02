// EdgeOne Pages Edge Function: /api/events
// 事件上报(POST) + 监护人轮询(GET) - 兼容所有 HTTP 方法
// 事件类型：medication / alert / monitor_end / acknowledge

const MAX_EVENTS = 200;
const VALID_TYPES = ['medication', 'alert', 'monitor_end'];

export default function onRequest(context) {
  const { request, env } = context;

  // CORS 预检
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  return (async () => {
    try {
      if (!env.UPSTASH_REDIS_REST_URL || !env.UPSTASH_REDIS_REST_TOKEN) {
        return json(500, { error: '服务器未配置 Upstash Redis' });
      }

      const urlParams = new URL(request.url).searchParams;
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
      } catch (e) { /* body read failed, use URL params only */ }

      // 合并：body 优先，URL 补充
      const data = { ...Object.fromEntries(urlParams.entries()), ...bodyData };

      // 如果 URL 有 after 参数且是数字，就是轮询请求（GET 兼容）
      const hasAfter = urlParams.has('after') || data.after !== undefined;
      const isAck = data.action === 'acknowledge';
      const hasEventType = data.type && VALID_TYPES.includes(data.type);

      // 判断操作类型：有 familyId+after=轮询；有 type=事件上报；有 action=ack=确认
      if (data.familyId && (hasAfter || !hasEventType)) {
        return await handleGet(env, data);
      } else if (isAck || hasEventType || data.familyId) {
        return await handlePost(env, data);
      }

      return json(400, { error: '无法识别操作，请提供 familyId + after（轮询）或 familyId + type（上报）' });
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

async function redisCmd(env, ...args) {
  const resp = await fetch(env.UPSTASH_REDIS_REST_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + env.UPSTASH_REDIS_REST_TOKEN,
    },
    body: JSON.stringify(args),
  });
  return await resp.json();
}

async function handleGet(env, data) {
  const familyId = data.familyId || '';
  const after = parseInt(data.after || '0', 10);

  if (!familyId) {
    return json(400, { error: '缺少 familyId 参数' });
  }

  const result = await redisCmd(env, 'LRANGE', 'family:' + familyId + ':events', '0', '-1');
  const rawList = result.result || [];

  const events = [];
  for (const item of rawList) {
    try {
      const evt = JSON.parse(item);
      if ((evt.timestamp || 0) > after) events.push(evt);
    } catch (e) { /* skip */ }
  }

  const ackResult = await redisCmd(env, 'KEYS', 'family:' + familyId + ':ack:*');
  const acknowledged = (ackResult.result || []).map(key => {
    const ts = key.split(':').pop();
    return parseInt(ts, 10);
  });

  return json(200, { events, acknowledged });
}

async function handlePost(env, data) {
  // 确认异常
  if (data.action === 'acknowledge') {
    const familyId = data.familyId || '';
    const ts = parseInt(data.ts || '0', 10);
    if (!familyId || !ts) {
      return json(400, { error: '缺少 familyId 或 ts' });
    }
    await redisCmd(env, 'SET', 'family:' + familyId + ':ack:' + ts, '1');
    const ackEvent = {
      type: 'acknowledge',
      timestamp: Date.now(),
      refTs: ts,
      status: 'acknowledged',
    };
    await redisCmd(env, 'RPUSH', 'family:' + familyId + ':events', JSON.stringify(ackEvent));
    return json(200, { ok: true });
  }

  // 正常事件上报
  const familyId = data.familyId || '';
  const eventType = data.type || '';
  if (!familyId || !eventType) {
    return json(400, { error: '缺少 familyId 或 type' });
  }
  if (!VALID_TYPES.includes(eventType)) {
    return json(400, { error: '未知事件类型: ' + eventType });
  }

  const event = {
    type: eventType,
    drugName: data.drugName || '',
    time: data.time || '',
    timestamp: Date.now(),
    status: 'pending',
  };
  await redisCmd(env, 'RPUSH', 'family:' + familyId + ':events', JSON.stringify(event));
  await redisCmd(env, 'LTRIM', 'family:' + familyId + ':events', String(-MAX_EVENTS), '-1');

  return json(200, { ok: true, event });
}
