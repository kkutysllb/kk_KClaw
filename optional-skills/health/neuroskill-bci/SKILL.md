---
name: neuroskill-bci
description: >
  连接到运行的 NeuroSkill 实例，并将用户实时的认知和情绪状态（专注、放松、心情、认知负荷、嗜睡、
  心率、HRV、睡眠分期和 40+ 衍生 EXG 分数）融入响应。需要 BCI 可穿戴设备（Muse 2/S 或 OpenBCI）
  和在本地运行的 NeuroSkill 桌面应用。
version: 1.0.0
author: KClaw Agent + Nous Research
license: MIT
metadata:
  kclaw:
    tags: [BCI, 神经反馈, 健康, 专注, EEG, 认知状态, 生物识别, neuroskill]
    category: health
    related_skills: []
---

# NeuroSkill BCI 集成

将 KClaw 连接到运行的 [NeuroSkill](https://neuroskill.com/) 实例，从 BCI 可穿戴设备读取实时大脑和身体指标。用它来提供认知感知的响应、建议干预措施，并随时间追踪心理表现。

> **⚠️ 仅供研究使用** — NeuroSkill 是一个开源研究工具。它**不是**医疗器械，**未**经过 FDA、CE 或任何监管机构批准。切勿将这些指标用于临床诊断或治疗。

请参阅 `references/metrics.md` 获取完整指标参考，`references/protocols.md` 获取干预协议，`references/api.md` 获取 WebSocket/HTTP API。

---

## 前置要求

- **Node.js 20+** 已安装（`node --version`）
- **NeuroSkill 桌面应用** 运行中，已连接 BCI 设备
- **BCI 硬件**：Muse 2、Muse S 或 OpenBCI（4 通道 EEG + PPG + IMU 通过 BLE）
- `npx neuroskill status` 返回数据无错误

### 验证设置
```bash
node --version                    # 必须为 20+
npx neuroskill status             # 完整系统快照
npx neuroskill status --json      # 机器可解析的 JSON
```

如果 `npx neuroskill status` 返回错误，请告诉用户：
- 确保 NeuroSkill 桌面应用已打开
- 确保 BCI 设备已开机并通过蓝牙连接
- 检查信号质量 — NeuroSkill 中的绿色指示灯（每个电极 ≥0.7）
- 如果 `command not found`，请安装 Node.js 20+

---

## CLI 参考：`npx neuroskill <command>`

所有命令支持 `--json`（原始 JSON，管道安全）和 `--full`（人类摘要 + JSON）。

| 命令 | 描述 |
|---------|-------------|
| `status` | 完整系统快照：设备、分数、频段、比率、睡眠、历史 |
| `session [N]` | 单会话分解，包含前半/后半趋势（0=最近） |
| `sessions` | 列出所有日期的记录会话 |
| `search` | ANN 相似性搜索，查找神经相似历史时刻 |
| `compare` | A/B 会话比较，包含指标差异和趋势分析 |
| `sleep [N]` | 睡眠阶段分类（Waking/N1/N2/N3/REM）与分析 |
| `label "text"` | 在当前时刻创建时间戳注释 |
| `search-labels "query"` | 对过去标签进行语义向量搜索 |
| `interactive "query"` | 跨模态 4 层图搜索（文本 → EXG → 标签） |
| `listen` | 实时事件流（默认 5 秒，设置 `--seconds N`） |
| `umap` | 会话嵌入的 3D UMAP 投影 |
| `calibrate` | 打开校准窗口并启动 profile |
| `timer` | 启动专注计时器（Pomodoro/深度工作/短专注预设） |
| `notify "title" "body"` | 通过 NeuroSkill 应用发送 OS 通知 |
| `raw '{json}'` | 原始 JSON 直通到服务器 |

### 全局标志
| 标志 | 描述 |
|------|-------------|
| `--json` | 原始 JSON 输出（无 ANSI，管道安全） |
| `--full` | 人类摘要 + 彩色 JSON |
| `--port <N>` | 覆盖服务器端口（默认：自动发现，通常为 8375） |
| `--ws` | 强制使用 WebSocket 传输 |
| `--http` | 强制使用 HTTP 传输 |
| `--k <N>` | 最近邻数量（search、search-labels） |
| `--seconds <N>` | listen 的持续时间（默认：5） |
| `--trends` | 显示每个会话的指标趋势（sessions） |
| `--dot` | Graphviz DOT 输出（interactive） |

---

## 1. 检查当前状态

### 获取实时指标
```bash
npx neuroskill status --json
```

**始终使用 `--json`** 以便可靠解析。默认输出是彩色人类可读的文本。

### 响应中的关键字段

`scores` 对象包含所有实时指标（0–1 范围，除非另有说明）：

```jsonc
{
  "scores": {
    "focus": 0.70,           // β / (α + θ) — 持续注意力
    "relaxation": 0.40,      // α / (β + θ) — 平静清醒
    "engagement": 0.60,      // 主动心理投入
    "meditation": 0.52,      // alpha + 静止 + HRV 一致性
    "mood": 0.55,            // 来自 FAA、TAR、BAR 的复合指标
    "cognitive_load": 0.33,  // 额叶 θ / 颞叶 α · f(FAA, TBR)
    "drowsiness": 0.10,      // TAR + TBR + 下降频谱质心
    "hr": 68.2,              // 心率（来自 PPG）
    "snr": 14.3,             // 信噪比（dB）
    "stillness": 0.88,       // 0–1；1 = 完全静止
    "faa": 0.042,            // 额叶 Alpha 不对称（+ = 接近）
    "tar": 0.56,             // Theta/Alpha 比率
    "bar": 0.53,             // Beta/Alpha 比率
    "tbr": 1.06,             // Theta/Beta 比率（ADHD 代理）
    "apf": 10.1,             // Alpha 峰值频率（Hz）
    "coherence": 0.614,      // 半球间一致性
    "bands": {
      "rel_delta": 0.28, "rel_theta": 0.18,
      "rel_alpha": 0.32, "rel_beta": 0.17, "rel_gamma": 0.05
    }
  }
}
```

还包括：`device`（状态、电池、固件）、`signal_quality`（每个电极 0–1）、
`session`（持续时间、epoch）、`embeddings`、`labels`、`sleep` 摘要和 `history`。

### 解释输出

解析 JSON 并将指标翻译成自然语言。永远不要仅报告原始数字 — 始终赋予它们含义：

**应该：**
> "您现在的专注力很强，达到 0.70 — 处于心流状态。心率稳定在 68 bpm，您的 FAA 是正的，表明良好的接近动力。现在是处理复杂任务的好时机。"

**不应该：**
> "专注力：0.70，放松度：0.40，心率：68"

关键解释阈值（请参阅 `references/metrics.md` 获取完整指南）：
- **专注力 > 0.70** → 心流状态区域，保护它
- **专注力 < 0.40** → 建议休息或协议
- **嗜睡 > 0.60** → 疲劳警告，微睡眠风险
- **放松度 < 0.30** → 需要压力干预
- **认知负荷 > 0.70 持续** → 心灵倾倒或休息
- **TBR > 1.5** → Theta 占主导，执行控制减弱
- **FAA < 0** → 退缩/负面影响 — 考虑 FAA 再平衡
- **SNR < 3 dB** → 信号不可靠，建议重新定位电极

---

## 2. 会话分析

### 单会话分解
```bash
npx neuroskill session --json         # 最近的会话
npx neuroskill session 1 --json       # 上一个会话
npx neuroskill session 0 --json | jq '{focus: .metrics.focus, trend: .trends.focus}'
```

返回完整指标，包含**前半 vs 后半趋势**（`"up"`、`"down"`、`"flat"`）。
用这个来描述会话如何演变：

> "您的专注力从 0.64 开始，到结束时上升到 0.76 — 明显的上升趋势。认知负荷从 0.38 下降到 0.28，表明随着您进入状态，任务变得更加自动。"

### 列出所有会话
```bash
npx neuroskill sessions --json
npx neuroskill sessions --trends      # 显示每个会话的指标趋势
```

---

## 3. 历史搜索

### 神经相似性搜索
```bash
npx neuroskill search --json                    # 自动：最近会话，k=5
npx neuroskill search --k 10 --json             # 10 个最近邻
npx neuroskill search --start <UTC> --end <UTC> --json
```

使用 HNSW 近似最近邻搜索在 128-D ZUNA 嵌入上查找历史上神经相似的时刻。返回距离统计、时间分布（一天中的小时）和最匹配的日子。

当用户问时使用这个：
- "我上次处于这种状态是什么时候？"
- "找到我最佳专注力的会话"
- "我通常什么时候会在下午崩溃？"

### 语义标签搜索
```bash
npx neuroskill search-labels "deep focus" --k 10 --json
npx neuroskill search-labels "stress" --json | jq '[.results[].EXG_metrics.tbr]'
```

使用向量嵌入（Xenova/bge-small-en-v1.5）搜索标签文本。返回匹配标签及其在标记时刻的关联 EXG 指标。

### 跨模态图搜索
```bash
npx neuroskill interactive "deep focus" --json
npx neuroskill interactive "deep focus" --dot | dot -Tsvg > graph.svg
```

4 层图：查询 → 文本标签 → EXG 点 → 附近标签。使用 `--k-text`、`--k-EXG`、`--reach <minutes>` 调整。

---

## 4. 会话比较
```bash
npx neuroskill compare --json                   # 自动：最近 2 个会话
npx neuroskill compare --a-start <UTC> --a-end <UTC> --b-start <UTC> --b-end <UTC> --json
```

返回约 50 个指标的绝对变化、百分比变化和方向指标差异。还包括 `insights.improved[]` 和 `insights.declined[]` 数组、两个会话的睡眠分期和 UMAP 作业 ID。

用上下文解释比较 — 提及趋势，而不仅仅是差异：
> "昨天您有两个强专注力块（上午 10 点和下午 2 点）。今天您从上午 11 点左右开始有一个，现在仍在继续。您今天的整体投入更高了，但压力峰值更多 — 您的压力指数上升了 15%，FAA 更频繁地转为负。"

```bash
# 按改进百分比排序指标
npx neuroskill compare --json | jq '.insights.deltas | to_entries | sort_by(.value.pct) | reverse'
```

---

## 5. 睡眠数据
```bash
npx neuroskill sleep --json                     # 最近 24 小时
npx neuroskill sleep 0 --json                   # 最近的睡眠会话
npx neuroskill sleep --start <UTC> --end <UTC> --json
```

返回逐 epoch 睡眠分期（5 秒窗口）和分析：
- **阶段代码**：0=Waking，1=N1，2=N2，3=N3（深度），4=REM
- **分析**：efficiency_pct、onset_latency_min、rem_latency_min、bout 计数
- **健康目标**：N3 15–25%、REM 20–25%、效率 >85%、入睡 <20 分钟

```bash
npx neuroskill sleep --json | jq '.summary | {n3: .n3_epochs, rem: .rem_epochs}'
npx neuroskill sleep --json | jq '.analysis.efficiency_pct'
```

当用户提到睡眠、疲倦或恢复时使用这个。

---

## 6. 标记时刻
```bash
npx neuroskill label "breakthrough"
npx neuroskill label "studying algorithms"
npx neuroskill label "post-meditation"
npx neuroskill label --json "focus block start"   # 返回 label_id
```

在以下情况下自动标记时刻：
- 用户报告突破或洞察
- 用户开始新任务类型（例如"切换到代码审查"）
- 用户完成重要协议
- 用户要求您标记当前时刻
- 发生显著状态转换时（进入/离开心流）

标签存储在数据库中，并通过 `search-labels` 和 `interactive` 命令索引以便后续检索。

---

## 7. 实时流
```bash
npx neuroskill listen --seconds 30 --json
npx neuroskill listen --seconds 5 --json | jq '[.[] | select(.event == "scores")]'
```

流式传输指定持续时间的实时 WebSocket 事件（EXG、PPG、IMU、分数、标签）。需要 WebSocket 连接（不适用于 `--http`）。

将此用于持续监控场景，或在协议期间实时观察指标变化。

---

## 8. UMAP 可视化
```bash
npx neuroskill umap --json                      # 自动：最近 2 个会话
npx neuroskill umap --a-start <UTC> --a-end <UTC> --b-start <UTC> --b-end <UTC> --json
```

ZUNA 嵌入的 GPU 加速 3D UMAP 投影。`separation_score` 指示两个会话的神经差异程度：
- **> 1.5** → 会话神经上不同（不同大脑状态）
- **< 0.5** → 两个会话的大脑状态相似

---

## 9. 主动状态感知

### 会话开始检查
在会话开始时，如果用户提到他们戴着设备或询问状态，可选择运行状态检查：
```bash
npx neuroskill status --json
```

注入简短状态摘要：
> "快速检查：专注力正在建立，达到 0.62，放松度良好为 0.55，您的 FAA 是正的 — 接近动力已激活。看起来是个好的开始。"

### 何时主动提及状态

**仅在以下情况下提及认知状态**：
- 用户明确询问（"我做得怎么样？"、"检查我的专注力"）
- 用户报告难以集中、压力或疲劳
- 达到关键阈值（嗜睡 > 0.70、专注力 < 0.30 持续）
- 用户要做认知要求高的事情并询问准备情况

**当专注力 > 0.75 时不要**打断心流状态来报告指标。保护会话 — 沉默是正确的响应。

---

## 10. 建议协议

当指标表明需要时，从 `references/protocols.md` 建议协议。始终在开始前询问 — 永远不要打断心流状态：

> "您的专注力在过去 15 分钟内一直在下降，TBR 攀升超过 1.5 — Theta 主导和精神疲劳的迹象。想让我带您完成 Theta-Beta 神经反馈锚定吗？这是一个 90 秒的练习，使用有节奏的计数和呼吸来抑制 theta 并提升 beta。"

关键触发器：
- **专注力 < 0.40，TBR > 1.5** → Theta-Beta 神经反馈锚定或 Box Breathing
- **放松度 < 0.30，stress_index 高** → 心脏一致性或 4-7-8 Breathing
- **认知负荷 > 0.70 持续** → 认知负荷卸载（心灵倾倒）
- **嗜睡 > 0.60** → 超昼夜重置或 Wake Reset
- **FAA < 0（负）** → FAA 再平衡
- **心流状态（专注力 > 0.75，参与度 > 0.70）** → 不要打断
- **高静止 + headache_index** → Neck Release Sequence
- **低 RMSSD（< 25ms）** → 迷走神经调节

---

## 11. 其他工具

### 专注计时器
```bash
npx neuroskill timer --json
```
启动专注计时器窗口，包含 Pomodoro（25/5）、深度工作（50/10）或短专注（15/5）预设。

### 校准
```bash
npx neuroskill calibrate
npx neuroskill calibrate --profile "Eyes Open"
```
打开校准窗口。当信号质量差或用户想要建立个性化基线时有用。

### OS 通知
```bash
npx neuroskill notify "Break Time" "Your focus has been declining for 20 minutes"
```

### 原始 JSON 直通
```bash
npx neuroskill raw '{"command":"status"}' --json
```
用于尚未映射到 CLI 子命令的任何服务器命令。

---

## 错误处理

| 错误 | 可能原因 | 修复 |
|-------|-------------|------|
| `npx neuroskill status` 挂起 | NeuroSkill 应用未运行 | 打开 NeuroSkill 桌面应用 |
| `device.state: "disconnected"` | BCI 设备未连接 | 检查蓝牙、设备电池 |
| 所有分数返回 0 | 电极接触不良 | 重新定位头带、润湿电极 |
| `signal_quality` 值 < 0.7 | 电极松动 | 调整贴合、清洁电极触点 |
| SNR < 3 dB | 信号嘈杂 | 尽量减少头部移动，检查环境 |
| `command not found: npx` | Node.js 未安装 | 安装 Node.js 20+ |

---

## 示例交互

**"我现在做得怎么样？"**
```bash
npx neuroskill status --json
```
→ 自然解释分数，提及专注力、放松度、心情和任何显著比率（FAA、TBR）。仅在指标表明需要时才建议行动。

**"我无法集中注意力"**
```bash
npx neuroskill status --json
```
→ 检查指标是否确认（高 theta、低 beta、TBR 上升、嗜睡增加）。如果确认，从 `references/protocols.md` 建议适当协议。如果指标看起来正常，问题可能是动力性的而非神经性的。

**"比较我今天和昨天的专注力"**
```bash
npx neuroskill compare --json
```
→ 解释趋势，而不仅仅是数字。提及什么改善了，什么下降了，以及可能的原因。

**"我上次处于心流状态是什么时候？"**
```bash
npx neuroskill search-labels "flow" --json
npx neuroskill search --json
```
→ 报告时间戳、关联指标和用户当时在做什么（来自标签）。

**"我睡得怎么样？"**
```bash
npx neuroskill sleep --json
```
→ 报告睡眠架构（N3%、REM%、效率），与健康目标比较，并注意任何问题（高清醒 epoch、低 REM）。

**"标记这个时刻 — 我刚刚有了突破"**
```bash
npx neuroskill label "breakthrough"
```
→ 确认标签已保存。可选择记下当前指标以记住状态。

---

## 参考

- [NeuroSkill 论文 — arXiv:2603.03212](https://arxiv.org/abs/2603.03212)（Kosmyna & Hauptmann，MIT Media Lab）
- [NeuroSkill 桌面应用](https://github.com/NeuroSkill-com/skill)（GPLv3）
- [NeuroLoop CLI 伴侣](https://github.com/NeuroSkill-com/neuroloop)（GPLv3）
- [MIT Media Lab 项目](https://www.media.mit.edu/projects/neuroskill/overview/）
