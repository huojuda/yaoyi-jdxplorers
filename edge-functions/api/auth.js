// EdgeOne Pages Edge Function: /api/auth
// 账号系统：注册 / 登录 / 会话验证 / 登出
// 环境变量：UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
// 安全设计：
//   - PBKDF2-SHA256 10000次迭代加盐哈希（WebCrypto原生实现，边缘CPU安全）
//   - 会话token 32字节随机，TTL 7天
//   - 登录失败5次锁定10分钟
//   - 数据按 userId 命名空间隔离，userId 由服务端生成，永不信任前端

const SESSION_TTL = 604800; // 7天（秒）
const PBKDF2_ITERATIONS = 10000; // 边缘函数CPU限制下的安全下限，生产可提升至10万
const MAX_LOGIN_FAILS = 5;
const LOCK_SECONDS = 600; // 锁定10分钟
const VALID_ROLES = ['patient', 'caregiver'];

export default function onRequest(context) {
  const { request, env } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      },
    });
  }

  return (async () => {
    try {
      if (!env.UPSTASH_REDIS_REST_URL || !env.UPSTASH_REDIS_REST_TOKEN) {
        return json(500, { error: '服务器未配置 Upstash Redis' });
      }

      // 合并 URL 参数与请求体（兼容手机端 POST body 丢失问题）
      const urlParams = new URL(request.url).searchParams;
      let bodyData = {};
      try {
        const bodyText = await request.text();
        if (bodyText) {
          try { bodyData = JSON.parse(bodyText); }
          catch (e) {
            bodyData = Object.fromEntries(new URLSearchParams(bodyText).entries());
          }
        }
      } catch (e) { /* 忽略 */ }
      const data = { ...Object.fromEntries(urlParams.entries()), ...bodyData };

      // token 优先从 Authorization 头取，其次 query（兼容预览链路）
      const authHeader = request.headers.get('Authorization') || '';
      const token = authHeader.replace(/^Bearer\s+/i, '') || data._auth || '';

      const action = data.action || '';

      if (action === 'register') return await handleRegister(env, data);
      if (action === 'login') return await handleLogin(env, data);
      if (action === 'resetpw') return await handleResetPw(env, data);
      if (action === 'diag') return await handleDiag(env, data);
      if (action === 'me') return await handleMe(env, token);
      if (action === 'logout') return await handleLogout(env, token);

      return json(400, { error: '未知操作，应为 register/login/resetpw/diag/me/logout' });
    } catch (e) {
      return json(500, { error: String(e && e.message || e) });
    }
  })();
}

// ---------- 注册 ----------
async function handleRegister(env, data) {
  const phone = String(data.phone || '').trim();
  const password = String(data.password || '');
  const role = String(data.role || '').trim();
  const displayName = String(data.displayName || '').trim() || (role === 'caregiver' ? '家人' : '长辈');

  if (!/^1\d{10}$/.test(phone)) {
    return json(400, { error: '请输入11位手机号' });
  }
  if (password.length < 6) {
    return json(400, { error: '密码至少6位' });
  }
  if (!VALID_ROLES.includes(role)) {
    return json(400, { error: '请选择角色：患者或家人' });
  }

  // 手机号占用检查（NX 原子操作防并发重复注册）
  const userId = genUserId();
  const salt = randomHex(16);
  const passHash = await pbkdf2(password, salt);

  // 先写用户数据，再原子发布手机号映射：保证映射存在时用户数据必然完整
  //（若先发布映射、后写数据，中途失败会产生"映射存在但无盐值/哈希"的僵尸账号，登录永远失败）
  await redisCmd(env, 'HSET', 'user:' + userId,
    'phone', phone,
    'passHash', passHash,
    'salt', salt,
    'role', role,
    'displayName', displayName,
    'createdAt', String(Date.now())
  );

  const nx = await redisCmd(env, 'SET', 'user:phone:' + phone, userId, 'NX');
  if (nx.result !== 'OK') {
    await redisCmd(env, 'DEL', 'user:' + userId); // 清理本次写入的孤儿数据
    return json(409, { error: '该手机号已注册，请直接登录' });
  }

  // 注册即登录
  const token = randomHex(32);
  await redisCmd(env, 'SET', 'session:' + token, userId, 'EX', String(SESSION_TTL));

  return json(200, {
    token,
    user: { userId, phone, role, displayName },
  });
}

// ---------- 登录 ----------
async function handleLogin(env, data) {
  const phone = String(data.phone || '').trim();
  const password = String(data.password || '');

  if (!phone || !password) {
    return json(400, { error: '请输入手机号和密码' });
  }

  // 防爆破锁定检查
  const failCount = await redisCmd(env, 'GET', 'login:fail:' + phone);
  if ((parseInt(failCount.result || '0', 10)) >= MAX_LOGIN_FAILS) {
    return json(429, { error: '失败次数过多，请10分钟后重试' });
  }

  const userIdRes = await redisCmd(env, 'GET', 'user:phone:' + phone);
  const userId = userIdRes.result;
  if (!userId) {
    return json(401, { error: '手机号或密码错误' });
  }

  const user = (await redisCmd(env, 'HGETALL', 'user:' + userId)).result || {};

  // 用户数据不完整（缺盐值或哈希）——僵尸账号，引导重置而非无意义的"密码错误"
  if (!user.salt || !user.passHash) {
    return json(409, { error: '账号数据异常，请点击"忘记密码"重置密码' });
  }

  const computed = await pbkdf2(password, user.salt);

  if (computed !== user.passHash) {
    const cnt = await redisCmd(env, 'INCR', 'login:fail:' + phone);
    await redisCmd(env, 'EXPIRE', 'login:fail:' + phone, String(LOCK_SECONDS));
    const remaining = MAX_LOGIN_FAILS - parseInt(cnt.result || '1', 10);
    return json(401, { error: '手机号或密码错误' + (remaining > 0 && remaining <= 2 ? '（还可尝试' + remaining + '次）' : '') });
  }

  // 登录成功，清除失败计数
  await redisCmd(env, 'DEL', 'login:fail:' + phone);

  const token = randomHex(32);
  await redisCmd(env, 'SET', 'session:' + token, userId, 'EX', String(SESSION_TTL));

  return json(200, {
    token,
    user: {
      userId,
      phone: user.phone,
      role: user.role,
      displayName: user.displayName,
    },
  });
}

// ---------- 重置密码（原型版：无短信验证，仅校验手机号已注册） ----------
async function handleResetPw(env, data) {
  const phone = String(data.phone || '').trim();
  const password = String(data.password || '');

  if (!/^1\d{10}$/.test(phone)) {
    return json(400, { error: '请输入11位手机号' });
  }
  if (password.length < 6) {
    return json(400, { error: '密码至少6位' });
  }

  const userIdRes = await redisCmd(env, 'GET', 'user:phone:' + phone);
  const userId = userIdRes.result;
  if (!userId) {
    return json(404, { error: '该手机号尚未注册' });
  }

  const salt = randomHex(16);
  const passHash = await pbkdf2(password, salt);
  await redisCmd(env, 'HSET', 'user:' + userId, 'passHash', passHash, 'salt', salt);

  // 重置成功即解除登录失败锁定
  await redisCmd(env, 'DEL', 'login:fail:' + phone);

  return json(200, { ok: true });
}

// ---------- 账号诊断（只返回长度/存在性，不泄露盐值与哈希） ----------
async function handleDiag(env, data) {
  const phone = String(data.phone || '').trim();
  if (!/^1\d{10}$/.test(phone)) {
    return json(400, { error: '请输入11位手机号' });
  }

  const userId = (await redisCmd(env, 'GET', 'user:phone:' + phone)).result;
  if (!userId) {
    return json(200, { phone, exists: false });
  }

  const user = (await redisCmd(env, 'HGETALL', 'user:' + userId)).result || {};
  const fail = await redisCmd(env, 'GET', 'login:fail:' + phone);

  return json(200, {
    phone,
    exists: true,
    userId,
    role: user.role || '',
    displayName: user.displayName || '',
    saltLen: (user.salt || '').length,
    hashLen: (user.passHash || '').length,
    failCount: parseInt(fail.result || '0', 10),
  });
}

// ---------- 会话验证 ----------
async function handleMe(env, token) {
  if (!token) return json(401, { error: '未登录' });
  const userIdRes = await redisCmd(env, 'GET', 'session:' + token);
  const userId = userIdRes.result;
  if (!userId) return json(401, { error: '登录已过期，请重新登录' });

  const user = (await redisCmd(env, 'HGETALL', 'user:' + userId)).result || {};
  // 滑动续期
  await redisCmd(env, 'EXPIRE', 'session:' + token, String(SESSION_TTL));

  return json(200, {
    user: {
      userId,
      phone: user.phone,
      role: user.role,
      displayName: user.displayName,
    },
  });
}

// ---------- 登出 ----------
async function handleLogout(env, token) {
  if (token) await redisCmd(env, 'DEL', 'session:' + token);
  return json(200, { ok: true });
}

// ---------- 工具函数 ----------

// PBKDF2-SHA256 派生（WebCrypto 原生，返回hex）
async function pbkdf2(password, saltHex) {
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    'raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']
  );
  const saltBytes = hexToBytes(saltHex);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: saltBytes, iterations: PBKDF2_ITERATIONS },
    keyMaterial,
    256
  );
  return bytesToHex(new Uint8Array(bits));
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

function randomHex(byteLen) {
  const bytes = new Uint8Array(byteLen);
  crypto.getRandomValues(bytes);
  return bytesToHex(bytes);
}

function bytesToHex(bytes) {
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

function genUserId() {
  return 'u_' + Date.now() + Math.floor(1000 + Math.random() * 9000);
}
