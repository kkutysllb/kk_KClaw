# YC-Bench：长期智能体基准测试

[YC-Bench](https://github.com/collinear-ai/yc-bench) 由 [Collinear AI](https://collinear.ai/) 开发，是一个确定性的长期基准测试，用于测试LLM智能体作为科技创业公司CEO的能力。智能体管理一家模拟公司1-3年，在4个技能领域内对资源分配、现金流、任务管理和声望专业化做出复合决策。

与TerminalBench2（使用二元通过/失败评估每个任务的编码能力）不同，YC-Bench衡量的是**长期战略一致性**——智能体是否能保持一致的策略、管理复合后果，并在数百轮中调整计划。

## 设置

```bash
# 安装yc-bench（可选依赖）
pip install "kclaw[yc-bench]"

# 或从源码安装
git clone https://github.com/collinear-ai/yc-bench
cd yc-bench && pip install -e .

# 验证
yc-bench --help
```

## 运行

```bash
# 从仓库根目录运行：
bash environments/benchmarks/yc_bench/run_eval.sh

# 或直接运行：
python environments/benchmarks/yc_bench/yc_bench_env.py evaluate \
    --config environments/benchmarks/yc_bench/default.yaml

# 覆盖模型：
bash environments/benchmarks/yc_bench/run_eval.sh \
    --openai.model_name anthropic/claude-opus-4-20250514

# 快速单个预设测试：
bash environments/benchmarks/yc_bench/run_eval.sh \
    --env.presets '["fast_test"]' --env.seeds '[1]'
```

## 工作原理

### 架构

```
KClawAgentLoop（我们的智能体）
  -> 终端工具 -> subprocess("yc-bench company status") -> JSON输出
  -> 终端工具 -> subprocess("yc-bench task accept --task-id X") -> JSON
  -> 终端工具 -> subprocess("yc-bench sim resume") -> JSON（推进时间）
  -> ...（每次运行100-500轮）
```

环境通过`yc-bench sim init`初始化模拟（不是`yc-bench run`，后者会启动yc-bench内置的智能体循环）。我们的`KClawAgentLoop`然后通过CLI命令驱动所有交互。

### 模拟机制

- **4个技能领域**：研究、推理、数据环境、训练
- **声望系统**（1.0-10.0）：限制访问更高报酬的任务
- **员工管理**：初级/中级/高级，具有领域特定技能率
- **吞吐量分割**：`effective_rate = base_rate / N` 每员工活跃任务
- **财务压力**：每月工资，破产 = 游戏结束
- **确定性**：基于SHA256的RNG — 相同种子+预设 = 相同世界

### 难度预设

| 预设 | 员工数 | 任务数 | 重点 |
|-----------|-----------|-------|-------|
| tutorial  | 3         | 50    | 基本循环机制 |
| easy      | 5         | 100   | 吞吐量意识 |
| **medium**| 5         | 150   | 名望攀升 + 领域专业化 |
| **hard**  | 7         | 200   | 精确ETA推理 |
| nightmare | 8         | 300   | 工资压力下的持续完美 |
| fast_test | （varies）| （varies）| 快速验证（约50轮） |

默认评估运行 **fast_test + medium + hard** × 3个种子 = 9次运行。

### 评分

```
composite = 0.5 × survival + 0.5 × normalised_funds
```

- **Survival**（二元）：公司是否避免了破产？
- **Normalised funds**（0.0-1.0）：相对于初始25万美元资本的日志尺度相对值

## 配置

`default.yaml`中的关键字段：

| 字段 | 默认 | 描述 |
|-------|---------|-------------|
| `presets` | `["fast_test", "medium", "hard"]` | 评估哪些预设 |
| `seeds` | `[1, 2, 3]` | 每个预设的RNG种子 |
| `max_agent_turns` | 200 | 每次运行的最多LLM调用次数 |
| `run_timeout` | 3600 | 每次运行的墙钟超时（秒） |
| `survival_weight` | 0.5 | 生存期在综合评分中的权重 |
| `funds_weight` | 0.5 | 标准化资金在综合评分中的权重 |
| `horizon_years` | null | 覆盖范围（null = 自动从预设） |

## 成本和时间估算

每次运行是100-500轮LLM调用。典型API费率下每次运行的近似成本：

| 预设 | 轮数 | 时间 | 预估成本 |
|--------|-------|------|-----------|
| fast_test | ~50 | 5-10分钟 | $1-5 |
| medium | ~200 | 20-40分钟 | $5-15 |
| hard | ~300 | 30-60分钟 | $10-25 |

完整默认评估（9次运行）：约3-6小时，$50-200取决于模型。

## 参考

- [collinear-ai/yc-bench](https://github.com/collinear-ai/yc-bench) — 官方仓库
- [Collinear AI](https://collinear.ai/) — yc-bench背后的公司
- [TerminalBench2](../terminalbench_2/) — 每个任务编码基准（互补）
