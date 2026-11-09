# -*- coding: utf-8 -*-
"""把内置药品库的播报固定话术预生成为 MP3（本地 Windows SAPI，无需任何云服务）。

背景：部分手机（如华为自带浏览器）的网页拿不到任何系统 TTS 音色，
speechSynthesis.speak() 必然 synthesis-failed。**只有不依赖系统 TTS 才能全覆盖。**
而"用药指导"这段播报文本完全由内置药品库字段拼成、是常量，所以可以离线预生成。

流程：抽取话术 → SAPI 合成 WAV → 降为单声道 16kHz → lameenc 编码 MP3

用法：python gen_tts_audio.py <输出目录>
"""
import json
import os
import re
import struct
import sys
import wave

import lameenc
import win32com.client

SRC = r'E:\Jd\new-chat\药忆-H5原型\index.html'
OUTDIR = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\姚锦烨\AppData\Local\Temp\tts_audio'
TMPDIR = r'C:\Users\姚锦烨\AppData\Local\Temp\tts_wav'
BITRATE = 32          # kbps，单声道语音足够
TARGET_RATE = 16000   # 降到 16k，体积减半且电话语音清晰度足够

os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(TMPDIR, exist_ok=True)

# ---------------- 1) 抽取话术 ----------------
s = open(SRC, encoding='utf-8').read()
i = s.find('const DRUG_DB')
assert i > 0
j = s.find('{', i)
depth, end = 0, None
for k in range(j, len(s)):
    if s[k] == '{':
        depth += 1
    elif s[k] == '}':
        depth -= 1
        if depth == 0:
            end = k
            break
blk = s[j:end + 1]
KEY_RE = re.compile(r'^  "([A-Za-z_]\w*)"\s*:\s*\{', re.M)
spans = [(m.group(1), m.start()) for m in KEY_RE.finditer(blk)]
spans.append((None, len(blk)))


def field(body, name):
    m = re.search(name + r'''\s*:\s*"([^"]*)"''', body)
    if m:
        return m.group(1)
    m = re.search(name + r"""\s*:\s*'([^']*)'""", body)
    return m.group(1) if m else ''


phrases = {}
for idx in range(len(spans) - 1):
    key, start = spans[idx]
    body = blk[start:spans[idx + 1][1]]
    name = field(body, 'name')
    text = ('这是' + name + '，' + field(body, 'plain_class') + '。'
            + '一天吃' + field(body, 'frequency') + '。'
            + field(body, 'timing') + '吃。'
            + field(body, 'meal_relation') + '。'
            + '需要注意：' + field(body, 'contraindication') + '。'
            + field(body, 'elderly_explanation'))
    phrases[key] = text
print('抽取到 %d 条话术' % len(phrases))

# ---------------- 2) 选中文音色 ----------------
voice = win32com.client.Dispatch('SAPI.SpVoice')
voices = voice.GetVoices()
chosen = None
for n in range(voices.Count):
    desc = voices.Item(n).GetDescription()
    if 'Chinese' in desc or 'Huihui' in desc:
        chosen = (n, desc)
        break
assert chosen, '本机没有中文语音引擎，无法生成'
voice.Voice = voices.Item(chosen[0])
print('使用音色：', chosen[1])


def synth_wav(text, path):
    if os.path.exists(path):
        os.remove(path)
    fs = win32com.client.Dispatch('SAPI.SpFileStream')
    # 3 = SSFMCreateForWrite
    fs.Open(path, 3, False)
    voice.AudioOutputStream = fs
    voice.Speak(text, 0)          # 0 = 同步朗读到输出流
    fs.Close()


def resample_to_16k_mono(src, dst):
    """把任意单/双声道 WAV 线性插值降到 16kHz 单声道 16bit。"""
    w = wave.open(src, 'rb')
    n_ch, sw, fr, n_frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n_frames)
    w.close()
    assert sw == 2, '预期 16bit PCM，实际 %d 字节' % sw
    # 解包成 int 列表（按声道交错）
    vals = struct.unpack('<%dh' % (len(raw) // 2), raw)
    if n_ch == 2:
        mono = [(vals[k] + vals[k + 1]) // 2 for k in range(0, len(vals) - 1, 2)]
    else:
        mono = list(vals)
    if fr != TARGET_RATE:
        ratio = fr / float(TARGET_RATE)
        out_len = int(len(mono) / ratio)
        out = []
        for m in range(out_len):
            pos = m * ratio
            p0 = int(pos)
            p1 = min(p0 + 1, len(mono) - 1)
            frac = pos - p0
            out.append(int(mono[p0] * (1 - frac) + mono[p1] * frac))
        mono = out
    data = struct.pack('<%dh' % len(mono), *mono)
    with wave.open(dst, 'wb') as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(TARGET_RATE)
        o.writeframes(data)
    return len(mono) / float(TARGET_RATE)


total_bytes = 0
manifest = {}
for key, text in phrases.items():
    wav_raw = os.path.join(TMPDIR, key + '.raw.wav')
    wav_16k = os.path.join(TMPDIR, key + '.16k.wav')
    mp3_path = os.path.join(OUTDIR, 'tts_' + key + '.mp3')
    synth_wav(text, wav_raw)
    secs = resample_to_16k_mono(wav_raw, wav_16k)

    w = wave.open(wav_16k, 'rb')
    pcm = w.readframes(w.getnframes())
    w.close()
    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE)
    enc.set_in_sample_rate(TARGET_RATE)
    enc.set_channels(1)
    enc.set_quality(5)
    data = enc.encode(pcm) + enc.flush()
    open(mp3_path, 'wb').write(data)
    total_bytes += len(data)
    manifest[key] = {'name': phrases[key][:0] or '', 'file': 'tts_' + key + '.mp3',
                     'secs': round(secs, 1), 'bytes': len(data)}
    print('  %-16s %5.1fs  %6d B  %s' % (key, secs, len(data), os.path.basename(mp3_path)))

print()
print('合计 %d 个文件 / %.2f MB' % (len(manifest), total_bytes / 1048576.0))
json.dump(manifest, open(os.path.join(OUTDIR, 'manifest.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
