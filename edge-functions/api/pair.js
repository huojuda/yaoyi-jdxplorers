// EdgeOne Pages Edge Function: /api/pair
// 配对码生成与验证（移植自 api/pair.py）
// 环境变量（EdgeOne Pages 控制台 → 环境变量）：
//   UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...CORS_HEADERS },
  });
}

// 通过 Upstash REST API 执行 Redis 命令
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

function genPairCode() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

function genFamilyId() {
  return 'fam_' + String(Math.floor(10000000 + Math.random() * 90000000));
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestPost(context) {
  try {
    const env = context.env;
    if (!env.UPSTASH_REDIS_REST_URL || !env.UPSTASH_REDIS_REST_TOKEN) {
      return json(500, { error: '服务器未配置 Upstash Redis' });
    }

    const data = await context.request.json();
    const action = data.action || '';

    if (action === 'create') {
      const code = genPairCode();
      const familyId = genFamilyId();
      const result = await redisCmd(env, 'SET', 'pair:code:' + code, familyId, 'EX', 300);
      if (result.result !== 'OK') {
        return json(500, { error: '配对码生成失败' });
      }
      return json(200, { code, familyId, expiresIn: 300 });
    }

    if (action === 'verify') {
      const code = String(data.code || '').trim();
      const phone = String(data.phone || '').trim();
      if (!code || !phone) {
        return json(400, { error: '请填写配对码和联系电话' });
      }
      const result = await redisCmd(env, 'GET', 'pair:code:' + code);
      const familyId = result.result;
      if (!familyId) {
        return json(400, { error: '配对码无效或已过期，请重新生成' });
      }
      await redisCmd(env, 'SET', 'family:' + familyId + ':phone', phone);
      await redisCmd(env, 'DEL', 'pair:code:' + code);
      return json(200, { familyId, phone });
    }

    return json(400, { error: '未知操作，应为 create 或 verify' });
  } catch (e) {
    return json(500, { error: String(e && e.message || e) });
  }
}
