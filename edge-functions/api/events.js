// EdgeOne Pages Edge Function: /api/events
// 事件上报 + 监护人轮询（账号关联体系：事件挂在患者 userId 下，家属通过 relation 读取）
// 事件类型：medication / alert / monitor_end / acknowledge
// Redis 结构：
//   user:{patientId}:events    → List of 事件 JSON（RPUSH + LTRIM 保留最近200条）
//   user:{patientId}:ack:{ts}  → 求助确认标记
//   relation:{userId}          → Set of 对方userId（由 /api/relation 维护）

const MAX_EVENTS = 200;
const VALID_TYPES = ['medication', 'alert', 'monitor_end'];

export default function onRequest(context) {
  const { request, env } = context;

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
          catch (e) { bodyData = Object.fromEntries(new URLSearchParams(bodyText).entries()); }
        }
      } catch (e) {}

      const data = { ...Object.fromEntries(urlParams.entries()), ...bodyData };

      // 认证：Authorization 头优先，其次 _auth 参数
      const authHeader = request.headers.get('Authorization') || '';
      const token = (authHeader.startsWith('Bearer ') ? authHeader.slice(7) : authHeader).trim()
        || String(data._auth || '').trim();
      if (!token) return json(401, { error: '请先登录' });

      const userId = (await redisCmd(env, 'GET', 'session:' + token)).result;
      if (!userId) return json(401, { error: '登录已过期，请重新登录' });

      if (data.action === 'acknowledge') {
        return await handleAck(env, userId, data);
      }
      if (data.type && VALID_TYPES.includes(data.type)) {
        return await handlePost(env, userId, data);
      }
      // 无 type → 视为轮询
      return await handleGet(env, userId, data);
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
}

// ---------- 事件上报（患者端登录后调用） ----------
async function handlePost(env, patientId, data) {
  const eventType = data.type;
  const event = {
    type: eventType,
    drugId: data.drugId || '',
    drugName: data.drugName || '',
    date: data.date || new Date().toISOString().slice(0, 10),
    time: data.time || '',
    timestamp: Date.now(),
    patientId,
    status: 'pending',
  };
  await redisCmd(env, 'RPUSH', 'user:' + patientId + ':events', JSON.stringify(event));
  await redisCmd(env, 'LTRIM', 'user:' + patientId + ':events', String(-MAX_EVENTS), '-1');
  return json(200, { ok: true, event });
}

// ---------- 求助确认（家属端） ----------
async function handleAck(env, caregiverId, data) {
  const targetPatientId = String(data.targetPatientId || '').trim();
  const ts = parseInt(data.ts || '0', 10);
  if (!targetPatientId || !ts) {
    return json(400, { error: '缺少 targetPatientId 或 ts' });
  }
  // 校验确实关联，防止任意写他人事件流
  const related = await redisCmd(env, 'SISMEMBER', 'relation:' + caregiverId, targetPatientId);
  if (!related.result) return json(403, { error: '未关联该患者' });

  await redisCmd(env, 'SET', 'user:' + targetPatientId + ':ack:' + ts, '1');
  const ackEvent = {
    type: 'acknowledge',
    timestamp: Date.now(),
    refTs: ts,
    patientId: targetPatientId,
    status: 'acknowledged',
  };
  await redisCmd(env, 'RPUSH', 'user:' + targetPatientId + ':events', JSON.stringify(ackEvent));
  await redisCmd(env, 'LTRIM', 'user:' + targetPatientId + ':events', String(-MAX_EVENTS), '-1');
  return json(200, { ok: true });
}

// ---------- 轮询（家属端登录后调用，聚合所有关联患者的事件） ----------
async function handleGet(env, caregiverId, data) {
  const after = parseInt(data.after || '0', 10);

  const members = (await redisCmd(env, 'SMEMBERS', 'relation:' + caregiverId)).result || [];
  const events = [];
  const acknowledged = [];
  const patientPhones = {};

  for (const patientId of members) {
    const user = (await redisCmd(env, 'HGETALL', 'user:' + patientId)).result || {};
    const patientName = user.displayName || '';
    patientPhones[patientId] = user.phone || '';

    const rawList = (await redisCmd(env, 'LRANGE', 'user:' + patientId + ':events', '0', '-1')).result || [];
    for (const item of rawList) {
      try {
        const evt = JSON.parse(item);
        if ((evt.timestamp || 0) > after) {
          evt.patientId = evt.patientId || patientId;
          evt.patientName = patientName;
          events.push(evt);
        }
      } catch (e) {}
    }

    const ackKeys = (await redisCmd(env, 'KEYS', 'user:' + patientId + ':ack:*')).result || [];
    for (const key of ackKeys) {
      acknowledged.push(parseInt(key.split(':').pop(), 10));
    }
  }

  events.sort((a, b) => a.timestamp - b.timestamp);

  return json(200, {
    events,
    acknowledged,
    patientPhones,
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
  const d = await resp.json();
  if (!resp.ok) throw new Error('Redis ' + resp.status + ': ' + (d && d.error || 'unknown'));
  // Upstash REST 的 HGETALL 返回扁平数组，统一转成对象
  if (args[0] === 'HGETALL' && Array.isArray(d.result)) {
    const obj = {};
    for (let i = 0; i < d.result.length; i += 2) obj[d.result[i]] = d.result[i + 1];
    d.result = obj;
  }
  return d;
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
