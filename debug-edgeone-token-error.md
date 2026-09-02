# Debug Session: edgeone-token-error

## Symptoms
- User sees `SyntaxError: Unexpected token '<', '<!doctype html' ... is not valid JSON` on guardian-side pairing verify
- URL bar shows bare domain `yaoyi-jdxplorers-wphvdfc9.edgeone.cool` WITHOUT `?eo_token=...` query params
- Previously had same error on patient-side pairing create; fix was pushed (`f066b73`) but user reports it still broken on guardian side

## Reproduction Steps
1. Open EdgeOne preview URL (with token) on phone
2. Patient side: click 设备配对 → verify code generation works
3. Guardian side: navigate to 家属查看, enter code + phone → 确认配对 → ERROR

## Hypotheses (falsifiable)

### H1: Preview link expired / user navigated without token
The user refreshed or reopened the page, losing the `?eo_token=...` query string. Without token, EdgeOne returns 401 HTML for ALL API calls. The token passthrough code only forwards token IF it exists in `location.search`. **Test**: check if current preview URL still works; ask user to confirm URL bar content.

### H2: EdgeOne hasn't deployed latest code
My fix (apiUrl + token passthrough) was in commit `f066b73`. If EdgeOne still serves old HTML without token passthrough, ALL API calls fail because fetch('/api/pair') doesn't carry token. **Test**: curl the deployment and grep for `apiUrl` or `edgeTokenQS`.

### H3: Token passthrough code doesn't cover verify path
The verify path calls `apiPost('/api/pair', { action: 'verify', code, phone })`. Check if this goes through apiUrl correctly. **Test**: code inspection + curl with token.

### H4: EdgeOne deployment cache issue
Service Worker might have cached old HTML. User needs hard refresh.

## Evidence Collected
- [pending] Deployment version on EdgeOne
- [pending] Curl test with fresh preview token
- [pending] User confirms URL bar has ?eo_token= params

## Status
[OPEN]
