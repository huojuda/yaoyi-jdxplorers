// EdgeOne Pages Edge Function: /api/events
// 事件上报(POST) + 监护人轮询(GET)
// 事件类型：medication / alert / monitor_end / acknowledge

const MAX_EVENTS = 200;
const VALID_TYPES = ['medication', 'alert', 'monitor_end'];

export default function onRequest(context) {
  const { request, env } = context;
  const method = request.method;

  if (method === 'OPTIONS') {
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

      if (method === 'GET') {
        return await handleGet(env, request);
      } else if (method === 'POST') {
        return await handlePost(env, request);
      } else {
        return json(405, { error: 'Method Not Allowed' });
      }
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

async function handleGet(env, request) {
  const url = new URL(request.url);
  const familyId = url.searchParams.get('familyId') || '';
  const after = parseInt(url.searchParams.get('after') || '0', 10);

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

async function handlePost(env, request) {
  const data = await request.json();

  // 确认异常
  if (data.action === 'acknowledge') {
    const familyId = data.familyId || '';
    const ts = data.ts || 0;
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
