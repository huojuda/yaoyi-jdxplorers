// EdgeOne Pages Edge Function: /api/relation
// 患者-监护人账号关联：绑定码模式（与原6位配对码交互一致，双方在线即可）
// Redis 结构：
//   relation:{userId} → Set of 对方userId（双向维护）
//   bindCode:{6位码} → JSON {userId, role, createdAt} EX 120
// 操作：gen / bind / list / unbind / pending

const CODE_TTL = 120; // 绑定码有效期秒
const MAX_RELATIONS = 10;

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
        return json(500, { error: '未配置 Upstash Redis' });
      }

      const urlParams = new URL(request.url).searchParams;
      let bodyData = {};
      try {
        const bt = await request.text();
        if (bt) {
          try { bodyData = JSON.parse(bt); }
          catch (e) { bodyData = Object.fromEntries(new URLSearchParams(bt).entries()); }
        }
      } catch (e) {}
      const data = { ...Object.fromEntries(urlParams.entries()), ...bodyData };

      const action = String(data.action || '').trim();
      const token = authFrom(data, request);
      const userId = await resolveToken(env, token);
      if (!userId) return json(401, { error: '请先登录' });

      switch (action) {
        case 'gen':    return await handleGen(env, userId);
        case 'bind':   return await handleBind(env, userId, data);
        case 'list':   return await handleList(env, userId);
        case 'unbind': return await handleUnbind(env, userId, data);
        case 'pending':return await handlePending(env, userId);
        default:       return json(400, { error: 'action 应为 gen/bind/list/unbind/pending' });
      }
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
}

// ---------- 生成绑定码（患者端调用） ----------
async function handleGen(env, userId) {
  const user = await getUser(env, userId);
  if (!user) return json(404, { error: '用户不存在' });

  // 同一用户的新码会覆盖旧码
  const code = String(Math.floor(100000 + Math.random() * 900000));
  await redisCmd(env, 'SET', 'bindCode:' + code,
    JSON.stringify({ userId, role: user.role, createdAt: Date.now() }),
    'EX', String(CODE_TTL));

  return json(200, { code, expiresIn: CODE_TTL, role: user.role, displayName: user.displayName });
}

// ---------- 监护人输入绑定码，完成关联 ----------
async function handleBind(env, caregiverId, data) {
  const code = String(data.code || '').trim();
  if (!/^\d{6}$/.test(code)) return json(400, { error: '请输入6位绑定码' });

  const raw = await redisCmd(env, 'GET', 'bindCode:' + code);
  if (!raw.result) return json(404, { error: '绑定码无效或已过期' });

  let info;
  try { info = JSON.parse(raw.result); } catch (e) {
    return json(400, { error: '绑定码数据异常' });
  }

  const patientId = info.userId;
  if (patientId === caregiverId) return json(400, { error: '不能绑定自己' });

  const caregiver = await getUser(env, caregiverId);
  const patient = await getUser(env, patientId);
  if (!caregiver || !patient) return json(404, { error: '用户不存在' });
  if (caregiver.role !== 'caregiver') return json(403, { error: '仅家人账号可发起绑定' });
  if (patient.role !== 'patient') return json(403, { error: '对方不是患者账号' });

  // 检查是否已关联
  const already = await redisCmd(env, 'SISMEMBER', 'relation:' + caregiverId, patientId);
  if (already.result) {
    await redisCmd(env, 'DEL', 'bindCode:' + code);
    return json(200, { ok: true, already: true });
  }

  // 检查双方关联上限
  const careSize = await redisCmd(env, 'SCARD', 'relation:' + caregiverId);
  const patSize  = await redisCmd(env, 'SCARD', 'relation:' + patientId);
  if (careSize.result >= MAX_RELATIONS) return json(400, { error: '您已关联长辈过多' });
  if (patSize.result >= MAX_RELATIONS)  return json(400, { error: '对方已关联家人过多' });

  // 双向存储
  await redisCmd(env, 'SADD', 'relation:' + caregiverId, patientId);
  await redisCmd(env, 'SADD', 'relation:' + patientId, caregiverId);
  await redisCmd(env, 'DEL', 'bindCode:' + code);

  return json(200, {
    ok: true,
    patient: { userId: patient.userId, displayName: patient.displayName, phone: patient.phone, role: patient.role },
  });
}

// ---------- 查询当前用户的所有关联 ----------
async function handleList(env, userId) {
  const members = await redisCmd(env, 'SMEMBERS', 'relation:' + userId);
  const ids = members.result || [];
  const results = [];
  for (const id of ids) {
    const u = await getUser(env, id);
    if (u) results.push({ userId: u.userId, displayName: u.displayName, phone: u.phone, role: u.role });
  }
  return json(200, { list: results });
}

// ---------- 解绑 ----------
async function handleUnbind(env, userId, data) {
  const targetId = String(data.targetUserId || '').trim();
  if (!targetId) return json(400, { error: '缺少 targetUserId' });

  await redisCmd(env, 'SREM', 'relation:' + userId, targetId);
  await redisCmd(env, 'SREM', 'relation:' + targetId, userId);
  return json(200, { ok: true });
}

// ---------- 辅助 ----------
async function handlePending(env, userId) {
  // 当前简化版：绑定码模式下无需轮询等待确认
  // 后续扩展手机号主动请求时，pending 用来查询待确认请求
  return json(200, { pending: [] });
}

async function getUser(env, userId) {
  const h = await redisCmd(env, 'HGETALL', 'user:' + userId);
  const fields = h.result || [];
  if (fields.length === 0) return null;
  const obj = {};
  for (let i = 0; i < fields.length; i += 2) obj[fields[i]] = fields[i + 1];
  obj.userId = userId;
  return obj;
}

async function resolveToken(env, token) {
  if (!token) return null;
  const r = await redisCmd(env, 'GET', 'session:' + token);
  return r.result || null;
}

function authFrom(data, request) {
  const h = request.headers.get('Authorization') || '';
  const fromHeader = h.startsWith('Bearer ') ? h.slice(7).trim() : '';
  return fromHeader || String(data._auth || '').trim() || '';
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
