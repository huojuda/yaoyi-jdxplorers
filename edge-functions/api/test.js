// 极简测试：验证 Edge Function 格式是否正确
export default function onRequest(context) {
  return new Response(JSON.stringify({ ok: true, method: context.request.method, msg: 'hello from edge' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}
