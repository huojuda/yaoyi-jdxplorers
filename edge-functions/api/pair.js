// EdgeOne Pages Edge Function: /api/pair
// 配对码生成与验证 - 兼容所有 HTTP 方法，多种数据来源
// 环境变量：UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN

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
      if (!env.UPSTASH_REDIS_REST_URL || !env.UPSTASH_REDIS_REST_TOKEN) {
        return json(500, { error: '服务器未配置 Upstash Redis' });
      }

      // 从多个来源收集参数，兼容所有设备
      const params = new URL(request.url).searchParams;
      let action = params.get('action') || '';
      let code = params.get('code') || '';
      let phone = params.get('phone') || '';

      // 尝试从请求体读取
      try {
        const bodyText = await request.text();
        if (bodyText) {
          // 尝试 JSON
          try {
            const body = JSON.parse(bodyText);
            action = action || body.action || '';
            code = code || body.code || '';
            phone = phone || body.phone || '';
          } catch (e) {
            // 尝试 form-urlencoded
            const formParams = new URLSearchParams(bodyText);
            action = action || formParams.get('action') || '';
            code = code || formParams.get('code') || '';
            phone = phone || formParams.get('phone') || '';
          }
        }
      } catch (e) {
        // 忽略，body 读不到就只用 URL 参数
      }

      if (!action) {
        return json(400, { error: '缺少 action 参数，请检查请求格式' });
      }

      if (action === 'create') {
        const pairCode = genPairCode();
        const familyId = genFamilyId();
        const result = await redisCmd(env, 'SET', 'pair:code:' + pairCode, familyId, 'EX', 300);
        if (result.result !== 'OK') {
          return json(500, { error: '配对码生成失败' });
        }
        return json(200, { code: pairCode, familyId, expiresIn: 300 });
      }

      if (action === 'verify') {
        code = String(code || '').trim();
        phone = String(phone || '').trim();
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

      return json(400, { error: '未知操作: ' + action + '，应为 create 或 verify' });
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

function genPairCode() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

function genFamilyId() {
  return 'fam_' + String(Math.floor(10000000 + Math.random() * 90000000));
}
