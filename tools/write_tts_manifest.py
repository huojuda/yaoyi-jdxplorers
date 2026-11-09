# -*- coding: utf-8 -*-
"""生成 PRERENDERED_TTS 清单（药品 id → [音频文件, 文本指纹]）并写入 index.html。

文本指纹算法必须与前端 ttsTextHash() 完全一致（UTF-16 码元 + FNV-1a 32 位），
这样一旦 buildSpeakText 的输出变了，前端就会自动忽略旧音频、退回系统 TTS，
不会出现"念的和屏幕上的不一样"。

用法：python write_tts_manifest.py [--dry]
"""
import os
import re
import sys

SRC = r'E:\Jd\new-chat\药忆-H5原型\index.html'
TTS_DIR = r'E:\Jd\new-chat\药忆-H5原型\tts'

A = '// >>> PRERENDERED_TTS_AUTO_GENERATED (由 .workbuddy/write_tts_manifest.py 生成，勿手改)'
B = '// <<< PRERENDERED_TTS_AUTO_GENERATED'

s = open(SRC, encoding='utf-8').read()


def extract_phrases(text):
    i = text.find('const DRUG_DB')
    j = text.find('{', i)
    depth, end = 0, None
    for k in range(j, len(text)):
        if text[k] == '{':
            depth += 1
        elif text[k] == '}':
            depth -= 1
            if depth == 0:
                end = k
                break
    blk = text[j:end + 1]
    key_re = re.compile(r'^  "([A-Za-z_]\w*)"\s*:\s*\{', re.M)
    spans = [(m.group(1), m.start()) for m in key_re.finditer(blk)]
    spans.append((None, len(blk)))

    def field(body, name):
        m = re.search(name + r'''\s*:\s*"([^"]*)"''', body)
        if m:
            return m.group(1)
        m = re.search(name + r"""\s*:\s*'([^']*)'""", body)
        return m.group(1) if m else ''

    out = []
    for idx in range(len(spans) - 1):
        key, start = spans[idx]
        body = blk[start:spans[idx + 1][1]]
        name = field(body, 'name')
        t = ('这是' + name + '，' + field(body, 'plain_class') + '。'
             + '一天吃' + field(body, 'frequency') + '。'
             + field(body, 'timing') + '吃。'
             + field(body, 'meal_relation') + '。'
             + '需要注意：' + field(body, 'contraindication') + '。'
             + field(body, 'elderly_explanation'))
        out.append((key, t))
    return out


def text_hash(s_):
    """必须是 utf-16-le 逐码元，才能与 JS 的 charCodeAt 完全一致。"""
    u = s_.encode('utf-16-le')
    x = 2166136261
    for i in range(0, len(u), 2):
        c = u[i] | (u[i + 1] << 8)
        x ^= c
        x = (x * 16777619) & 0xFFFFFFFF
    return format(x, 'x')


phrases = extract_phrases(s)

lines = [A,
         '// 预生成播报音频：把「用药指导」这段固定话术在本地用 Windows SAPI 合成 MP3，',
         '// 随站点一起托管。命中时直接播音频，**完全不依赖手机的系统 TTS** ——',
         '// 这是华为等"网页拿不到任何 TTS 音色"机型唯一能出声的办法。',
         '// 值 = [音频相对路径, 文本指纹]；指纹不匹配说明话术已改，自动退回系统 TTS。',
         'const PRERENDERED_TTS = {']
missing = []
for key, text in phrases:
    fn = 'tts_' + key + '.mp3'
    path = os.path.join(TTS_DIR, fn)
    if not os.path.exists(path):
        missing.append(key)
        continue
    lines.append('  %s: ["tts/%s", "%s"],' % (key, fn, text_hash(text)))
lines.append('};')
lines.append(B)

if missing:
    print('⚠️ 以下药品缺音频，已跳过：', missing)

block = '\n'.join(lines) + '\n'

if '--dry' in sys.argv:
    print(block[:900])
    print('...')
    print('清单条目：', len(lines) - 6)
    sys.exit(0)

if A in s and B in s:
    i = s.find(A)
    j = s.find(B) + len(B)
    s = s[:i] + block.rstrip('\n') + s[j:]
    action = '替换'
else:
    # 首次插入：放在 buildSpeakText 之前
    anchor = '// 结果页播放文案（点击播报与自动播报共用）'
    assert anchor in s, '未找到插入锚点'
    s = s.replace(anchor, block + '\n' + anchor, 1)
    action = '插入'

open(SRC, 'wb').write(s.encode('utf-8'))
d = open(SRC, 'rb').read()
print('%s PRERENDERED_TTS 清单：%d 条' % (action, len(lines) - 6))
print('index.html bytes=%d CRLF=%d' % (len(d), d.count(b'\r\n')))
