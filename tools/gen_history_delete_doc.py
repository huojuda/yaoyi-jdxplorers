# -*- coding: utf-8 -*-
"""从 index.html 里抽取「服药历史删除」相关代码，生成交付文档。
用 block() 程序化输出代码围栏，避免手写三引号边界出错。"""
import os

SRC = r'E:\Jd\new-chat\药忆-H5原型\index.html'
OUT = r'E:\Jd\new-chat\服药历史删除-实现说明.md'

s = open(SRC, encoding='utf-8').read()


def between(a, b, name):
    i = s.find(a)
    assert i >= 0, '未找到起锚点: ' + name
    j = s.find(b, i + len(a))
    assert j > i, '未找到止锚点: ' + name
    return s[i:j].rstrip('\n')


def block(lang, code):
    return '\n```' + lang + '\n' + code + '\n```\n'


parts = {
    'css':          between('/* ===== 服药历史：删除 / 批量管理（v1.4.0） ===== */', '/* ===== Toast ===== */', 'css'),
    'html_toolbar': between('  <!-- 服药历史管理：批量管理 / 清空全部 -->', '  <!-- 今天饮酒标记 -->', 'toolbar'),
    'html_modal':   between('<!-- ==================== 用药记录删除：二次确认弹窗 ==================== -->', '<div class="toast" id="toast"></div>', 'modal'),
    'js_data':      between('// ============================================================\n// 服药历史：删除（单条 / 批量 / 一键清空）', 'function checkDuplicate(drugId) {', 'data'),
    'js_render':    between('function renderHistory() {', '\n// ============================================================\n// 工具函数', 'render'),
    'js_load':      between('function loadRecords() {', 'function saveRecords(records) {', 'load'),
    'js_save':      between('function saveRecord(drug) {', 'function checkDuplicate(drugId) {', 'save'),
}
for k, v in parts.items():
    print('  抽取 %-13s %5d chars' % (k, len(v)))

D = []
A = D.append

A('# 服药历史「删除」功能 —— 实现说明与使用文档')
A('')
A('> 版本 **v1.4.0** ｜ 已上线 `https://yaoyi.site/` ｜ 单文件 SPA（`index.html` 内嵌全部 CSS/JS，无构建步骤）')
A('')
A('---')
A('')
A('## 一、落点：改在哪个页面 / 模块')
A('')
A('| 层 | 位置 | 改动 |')
A('|---|---|---|')
A('| **页面** | `#page-history`「📋 用药记录」 | 原来只有"看"，新增删除能力都在这一页 |')
A('| HTML | 页首标题之后 | 插入 `#history-toolbar`（批量管理 / 清空全部）与 `#history-selbar`（批量操作条） |')
A('| HTML | 文件末尾弹窗区，`<div class="toast">` 之前 | 插入 `#history-confirm-overlay` 二次确认弹窗 |')
A('| CSS | `/* ===== Toast ===== */` 之前 | 历史删除相关样式，全部复用已有设计变量 |')
A('| JS · 数据层 | `loadRecords()` / `saveRecord()` / `saveRecords()` 一带 | 记录加 `id`、老数据幂等迁移、删除逻辑 |')
A('| JS · 视图层 | `renderHistory()` | 列表加删除按钮 / 勾选框，并同步工具条状态 |')
A('| JS · 路由 | `showPage()` | 离开记录页时退出批量管理模式 |')
A('')
A('**没有新增后端接口。** 记录本来就存在设备本地，删除是纯前端操作。')
A('')
A('---')
A('')
A('## 二、涉及的数据字段与「接口」')
A('')
A('### 存储方式（与既有保持一致，未改动）')
A('')
A(block('js', "const STORAGE_KEY = 'yaoyi_medication_records';   // localStorage 单键，整体 JSON 覆盖写"))
A('### 记录结构')
A('')
A('| 字段 | 类型 | 说明 |')
A('|---|---|---|')
A('| `id` | string | **本次新增**。唯一标识，删除单条记录靠它定位 |')
A('| `drugId` | string | 药品库 key，用于关联 `DRUG_DB` 取名称/用法 |')
A('| `drugName` | string | 药品名（冗余存储，便于记录独立展示） |')
A('| `date` | string | `YYYY-MM-DD`，列表按它分组 |')
A('| `time` | string | `HH:mm` |')
A('| `timestamp` | number | 毫秒时间戳，同一天内按它倒序 |')
A('')
A('> **老数据兼容（关键）**：v1.4.0 以前的记录没有 `id`。`loadRecords()` 会按')
A('> `(timestamp|drugId|time)` **派生一个稳定 id 并回写**。派生只依赖记录自身字段，')
A('> 因此**幂等** —— 反复加载结果一致，"删除单条"永远能定位到目标。')
A('')
A('### 「接口」= 数据操作函数（沿用现有全局函数风格）')
A('')
A('| 函数 | 作用 |')
A('|---|---|')
A('| `recordKey(r)` | 取记录唯一键（`r.id` 优先，兜底派生） |')
A('| `visibleRecords()` | 返回"能渲染出来"的记录（药品已下架的本来就不显示），保证全选与计数与用户所见一致 |')
A('| `deleteRecordsByKeys(keys)` | **核心删除**：按唯一键批量删除，返回实际删除条数 |')
A('| `clearAllRecords()` | 清空全部，返回删除条数 |')
A('| `deleteRecordsByKeys/clearAllRecords` 之后统一调用 `refreshHistoryDependentViews()` | 刷新所有依赖视图 |')
A('')
A('---')
A('')
A('## 三、二次确认弹窗（所有删除动作的唯一入口）')
A('')
A('- 复用现有 `.alert-overlay` / `.alert-popup` 视觉，边框取危险色，保持全站一致。')
A('- 文案按动作区分：单条写明**药品名 + 日期时间**；批量写明**条数**；')
A('  清空额外写明**「无法恢复」以及会影响哪些统计**。')
A('- ✅ **确认按钮点下去之前，绝不修改任何数据** —— 这是防误删的核心，')
A('  已在回归测试里作为专门断言（"确认之前记录数不变"）。')
A('- 按钮为「确认删除（danger） / 取消」；点遮罩不关闭，必须明确选择。')
A('- `openHistoryConfirm()` 对 DOM 缺失做了兜底：退化为原生 `window.confirm`，功能不会失效。')
A('')
A('---')
A('')
A('## 四、删除后同步更新的范围')
A('')
A('集中在一个函数里，避免以后新增视图时漏刷：')
A('')
A(block('js', """function refreshHistoryDependentViews() {
  const safe = fn => { try { fn(); } catch (e) { console.warn('刷新失败:', e); } };
  safe(renderHistory);             // 历史列表本身
  safe(renderHistoryInteractions); // 今日药物相互作用提醒
  safe(renderComboAnalysis);       // AI 联合用药分析
  safe(renderAdherenceScore);      // 用药依从性评分
  safe(renderRepurchaseReminder);  // 复购提醒
  safe(renderTodayMeds);           // 首页「今日用药」
}"""))
A('另外的同步细节：')
A('')
A('- 记录被删空 → **自动退出批量管理模式**，列表显示空态。')
A('- 无记录时**隐藏工具条**，避免出现"点了没反应"的按钮。')
A('- 离开记录页 → 退出选择模式（`showPage()` 内），下次回来不会停在选择态。')
A('')
A('---')
A('')
A('## 五、完整实现代码')
A('')
A('### 5.1 数据层：`loadRecords()` —— 补 id（幂等迁移）')
A(block('js', parts['js_load']))
A('### 5.2 数据层：`saveRecord()` —— 写入 id')
A(block('js', parts['js_save']))
A('### 5.3 数据层 + 业务层：删除、批量管理、二次确认弹窗')
A(block('js', parts['js_data']))
A('### 5.4 视图层：`renderHistory()`')
A(block('js', parts['js_render']))
A('### 5.5 HTML：工具条与批量操作条（放在 `#page-history` 标题之后）')
A(block('html', parts['html_toolbar']))
A('### 5.6 HTML：二次确认弹窗（放在 `<div class="toast" id="toast"></div>` 之前）')
A(block('html', parts['html_modal']))
A('### 5.7 CSS')
A(block('css', parts['css']))
A('---')
A('')
A('## 六、使用说明')
A('')
A('### 删除单条')
A('')
A('1. 首页点「📋 用药记录」进入记录页。')
A('2. 找到目标记录，点右侧的 **🗑** 按钮。')
A('3. 弹窗会显示**要删的是哪个药、哪天的哪一时刻**；确认无误后点「确认删除」。')
A('   点「取消」不会有任何改动。')
A('')
A('### 批量删除')
A('')
A('1. 记录页顶部点「**批量管理**」→ 每行左侧出现圆形勾选框，点整行即可勾选。')
A('2. 用「**全选 / 取消全选**」快速选中，或逐条点选；计数条实时显示「已选 N / M 条」。')
A('3. 点「**删除选中**」→ 二次确认 → 完成。删除后自动退出管理模式。')
A('4. 中途想退出，点「取消」或顶部的「完成」，都不会改动数据。')
A('')
A('### 一键清空')
A('')
A('1. 记录页顶部点「**🗑 清空全部**」。')
A('2. 弹窗写明**将删除多少条**、**无法恢复**、以及会影响哪些统计。')
A('3. 点「确认清空」即清空。')
A('')
A('### 影响范围（删除后会一起变化的地方）')
A('')
A('| 位置 | 变化 |')
A('|---|---|')
A('| 用药记录列表 | 立即少掉被删的条目 |')
A('| 首页「今日用药」 | 当天的记录同步减少 |')
A('| 用药依从性评分 | 按剩余记录重新计算 |')
A('| AI 联合用药分析 | 按剩余记录重新分析 |')
A('| 复购提醒 | 按剩余记录重新评估 |')
A('| 重复服药提醒 | 直接读记录，下次进入结果页即以新数据为准 |')
A('')
A('### 数据安全边界')
A('')
A('- 记录只存在**这台设备的浏览器**里（`localStorage`）；清除浏览器数据、换设备都看不到，')
A('  删除后**没有云端副本可恢复**。')
A('- 正因为无法恢复，所有删除动作才强制走二次确认，且文案里明确写"无法恢复"。')
A('')
A('---')
A('')
A('## 七、验证情况')
A('')
A('新增行为级回归 **43 条断言全部通过**。其中最关键的一条：')
A('')
A('> **★ 确认之前记录数不变（防误删）** —— 点开删除按钮后、点确认之前，')
A('> 断言记录条的存储值仍为原值。')
A('')
A('其余覆盖：老数据 id 迁移的稳定性（跨次加载一致）、取消不删、单条只删中目标、')
A('批量删除、一键清空、删空后自动退出选择模式与工具条隐藏、六大依赖视图同步刷新。')
A('')
A('既有套件无退化：语音 16、时序 13、语音兼容 16、移动端 TTS 14、摄像头 24。')
A('')

doc = '\n'.join(D)
open(OUT, 'wb').write(doc.encode('utf-8'))

# 自检：围栏必须成对
fences = [l for l in doc.split('\n') if l.startswith('```')]
print('围栏行数 =', len(fences), '(偶数即配对正常)')
assert len(fences) % 2 == 0, '围栏不配对'
print('文档已生成：', OUT, os.path.getsize(OUT), 'bytes')
