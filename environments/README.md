# KClaw-Agent Atropos 环境

本目录包含 **kclaw** 工具调用能力与 **Atropos** RL 训练框架之间的集成层。它提供了通过多轮工具调用循环运行代理 LLMs、用任意奖励函数对输出进行评分以及将结果输入 Atropos 进行训练或评估所需的一切。

## 架构概述

```
                        Atropos 框架
                    ┌───────────────────────┐
                    │       BaseEnv          │  (atroposlib)
                    │  - 服务器管理   │
                    │  - 工作器调度   │
                    │  - Wandb 日志   │
                    │  - CLI (serve/process/
                    │    evaluate)           │
                    └───────────┬───────────┘
                                │ 继承
                    ┌───────────┴───────────┐
                    │  KClawAgentBaseEnv    │  kclaw_base_env.py
                    │  - 终端后端    │
                    │  - 工具解析     │
                    │  - 代理循环     │
                    │  - ToolContext          │
                    │  - 异步补丁     │
                    └───────────┬───────────┘
                                │ 继承
              ┌─────────────────┼─────────────────┐
              │                 │                  │
     TerminalTestEnv     KClawSweEnv    TerminalBench2EvalEnv
     (堆栈测试)     (SWE 训练)   (TB2 基准评估)
```

### 继承链

**BaseEnv**（来自 `atroposlib`）是 Atropos 基类。它提供：
- 服务器管理（OpenAI 兼容 API 服务器、VLLM、SGLang）
- 并行 rollouts 的工作器调度
- 用于指标和 rollout 日志的 Wandb 集成
- 带有三个子命令的 CLI 接口：`serve`、`process`、`evaluate`
- `evaluate_log()` 用于将评估结果保存到 JSON + samples.jsonl

**KClawAgentBaseEnv**（`kclaw_base_env.py`）用 kclaw 特性扩展 BaseEnv：
- 设置 `os.environ["TERMINAL_ENV"]` 以配置终端后端（local、docker、modal、daytona、ssh、singularity）
- 通过 `_resolve_tools_for_group()` 解析 kclaw 工具集（调用 `get_tool_definitions()`，查询 `tools/registry.py`）
- 实现 `collect_trajectory()`，运行完整代理循环并计算奖励
- 支持两阶段操作（阶段 1：OpenAI 服务器，阶段 2：VLLM ManagedServer）
- 在导入时应用异步安全工具操作的猴子补丁

具体环境继承自 `KClawAgentBaseEnv` 并实现：
- `setup()` -- 加载数据集，初始化状态
- `get_next_item()` -- 返回下一个 rollout 项目
- `format_prompt()` -- 将数据集项目转换为用户消息
- `compute_reward()` -- 使用 ToolContext 对 rollout 进行评分
- `evaluate()` -- 周期性评估逻辑

## 核心组件

### 代理循环（`agent_loop.py`）

`KClawAgentLoop` 是可重用的多轮代理引擎。它运行与 kclaw 的 `run_agent.py` 相同的模式：

1. 通过 `server.chat_completion()` 发送消息 + 工具到 API
2. 如果响应包含 `tool_calls`，通过 `handle_function_call()` 执行每个工具（委托给 `tools/registry.py` 的 `dispatch()`）
3. 将工具结果追加到对话并返回步骤 1
4. 如果响应没有 tool_calls，代理完成

工具调用在线程池（`run_in_executor`）中执行，因此内部使用 `asyncio.run()` 的后端（Modal、Docker）在 Atropos 的事件循环内部不会死锁。

返回一个包含完整对话历史、轮次计数、每轮推理内容、工具错误和可选 ManagedServer 状态（阶段 2）的 `AgentResult`。

### 工具上下文（`tool_context.py`）

`ToolContext` 是每个 rollout 的句柄，让奖励/验证函数直接访问 **所有** kclaw 工具，作用域限定为 rollout 的 `task_id`。相同的 `task_id` 意味着终端/浏览器会话与模型在 rollout 期间使用的会话 **相同**——所有状态（文件、进程、浏览器标签页）都被保留。

```python
async def compute_reward(self, item, result, ctx: ToolContext):
    # 在模型的终端沙盒中运行测试
    test = ctx.terminal("pytest -v")
    if test["exit_code"] == 0:
        return 1.0

    # 检查是否创建了文件
    content = ctx.read_file("/workspace/solution.py")
    if content.get("content"):
        return 0.5

    # 下载文件到本地进行验证（二进制安全）
    ctx.download_file("/remote/output.bin", "/local/output.bin")

    return 0.0
```

可用方法：
- **终端**：`terminal(command, timeout)` -- 运行 shell 命令
- **文件**：`read_file(path)`、`write_file(path, content)`、`search(query, path)`
- **传输**：`upload_file()`、`upload_dir()`、`download_file()`、`download_dir()` -- 主机和沙盒之间的二进制安全文件传输
- **Web**：`web_search(query)`、`web_extract(urls)`
- **浏览器**：`browser_navigate(url)`、`browser_snapshot()`
- **通用**：`call_tool(name, args)` -- 按名称调用任何 kclaw 工具
- **清理**：`cleanup()` -- 释放所有资源（在 `compute_reward` 后自动调用）

### 补丁（`patches.py`）

**问题**：一些 kclaw 工具在内部使用 `asyncio.run()`（例如 Modal 后端）。当从 Atropos 的事件循环内部调用时，这会崩溃，因为 `asyncio.run()` 不能嵌套。

**解决方案**：`ModalEnvironment` 使用专用的 `_AsyncWorker` 后台线程和自己的事件循环。调用代码看到同步接口，但内部所有异步 Modal SDK 调用都发生在线程上，因此不会与 Atropos 的循环冲突。这直接构建在 `tools/environments/modal.py` 中——不需要猴子补丁。

`patches.py` 现在是一个无操作（为保持与导入的向后兼容性而保留）。

### 工具调用解析器（`tool_call_parsers/`）

客户端解析器，从原始模型输出文本中提取结构化 `tool_calls`。在**阶段 2**（VLLM 服务器类型）中使用，其中 ManagedServer 的 `/generate` 端点返回不带工具调用解析的原始文本。

每个解析器是对应 VLLM 解析器 `extract_tool_calls()` 逻辑的独立重新实现。不需要 VLLM 依赖——仅使用标准库（`re`、`json`、`uuid`）和 `openai` 类型。

可用解析器：
- `kclaw` -- KClaw/ChatML `<tool_call>` XML 格式
- `mistral` -- Mistral `[TOOL_CALLS]` 格式
- `llama3_json` -- Llama 3 JSON 工具调用
- `qwen` -- Qwen 工具调用格式
- `qwen3_coder` -- Qwen3 Coder 格式
- `deepseek_v3` -- DeepSeek V3 格式
- `deepseek_v3_1` -- DeepSeek V3.1 格式
- `kimi_k2` -- Kimi K2 格式
- `longcat` -- Longcat 格式
- `glm45` / `glm47` -- GLM 模型格式

用法：
```python
from environments.tool_call_parsers import get_parser

parser = get_parser("kclaw")
content, tool_calls = parser.parse(raw_model_output)
```

在阶段 1（OpenAI 服务器类型）中，不需要这些解析器——服务器本地处理工具调用解析。

## 两阶段操作

### 阶段 1：OpenAI 服务器（评估 / SFT 数据生成）

使用 `server.chat_completion()` 和 `tools=` 参数。服务器（VLLM、SGLang、OpenRouter、OpenAI）本地处理工具调用解析。返回带有结构化 `tool_calls` 的 `ChatCompletion` 对象。

- 适用于：评估、SFT 数据生成、测试
- 运行方式：`serve`（带 `run-api`）、`process` 或 `evaluate` 子命令
- 为 Atropos 管道创建占位符令牌

### 阶段 2：VLLM ManagedServer（完整 RL 训练）

使用 ManagedServer 获取精确令牌 ID + logprobs（通过 `/generate`）。客户端工具调用解析器（来自 `tool_call_parsers/`）从原始输出重建结构化 `tool_calls`。

- 适用于：带 GRPO/PPO 的完整 RL 训练
- 运行方式：`serve` 子命令
- 真实令牌、掩码和 logprobs 流经管道

## 目录结构

```
environments/
├── README.md                     # 本文件
├── __init__.py                   # 包导出
├── kclaw_base_env.py            # 抽象基类（KClawAgentBaseEnv）
├── agent_loop.py                 # 多轮代理引擎（KClawAgentLoop）
├── tool_context.py               # rollout 奖励函数的工具访问
├── patches.py                    # Modal 后端的异步安全补丁
│
├── tool_call_parsers/            # 阶段 2 客户端解析器
│   ├── __init__.py               # 注册表 + 基类
│   ├── kclaw_parser.py
│   ├── mistral_parser.py
│   ├── llama_parser.py
│   ├── qwen_parser.py
│   ├── qwen3_coder_parser.py
│   ├── deepseek_v3_parser.py
│   ├── deepseek_v3_1_parser.py
│   ├── kimi_k2_parser.py
│   ├── longcat_parser.py
│   ├── glm45_parser.py
│   └── glm47_parser.py
│
├── terminal_test_env/            # 堆栈验证环境
│   └── terminal_test_env.py
│
├── kclaw_swe_env/               # SWE-bench 风格训练环境
│   └── kclaw_swe_env.py
│
└── benchmarks/                   # 评估基准
    ├── terminalbench_2/          # 89 个终端任务，Modal 沙盒
    │   └── terminalbench2_env.py
    ├── tblite/                   # 100 个校准任务（快速 TB2 代理）
    │   └── tblite_env.py
    └── yc_bench/                 # 长时战略基准
        └── yc_bench_env.py
```

## 具体环境

### TerminalTestEnv（`terminal_test_env/`）

一个自包含的环境，包含内联任务（无需外部数据集），用于验证完整堆栈端到端。每个任务要求模型在已知路径创建文件，验证器检查内容是否匹配。

```bash
# 服务模式（需要 run-api）
run-api
python environments/terminal_test_env/terminal_test_env.py serve

# 处理模式（无 run-api，保存到 JSONL）
python environments/terminal_test_env/terminal_test_env.py process \
    --env.data_path_to_save_groups terminal_test_output.jsonl
```

### KClawSweEnv（`kclaw_swe_env/`）

SWE-bench 风格训练环境。模型获得编码任务，使用终端 + 文件 + Web 工具来解决问题，奖励函数在相同的 Modal 沙盒中运行测试。

```bash
python environments/kclaw_swe_env/kclaw_swe_env.py serve \
    --openai.model_name YourModel \
    --env.dataset_name bigcode/humanevalpack \
    --env.terminal_backend modal
```

### TerminalBench2EvalEnv（`benchmarks/terminalbench_2/`）

Terminal-Bench 2.0 基准（89 个任务）的**仅评估**环境。每个任务获得一个预构建的 Docker Hub 镜像、一个自然语言指令和一个测试套件。代理使用终端 + 文件工具来解决问题，然后测试套件验证正确性。

遵循标准 Atropos 评估模式（如 GPQA、MMLU 等）：
- 通过 `evaluate` 子命令运行（不需要 `run-api`）
- `setup()` 加载数据集，`evaluate()` 运行所有任务
- `rollout_and_score_eval()` 处理每个任务的代理循环 + 测试验证
- 将验证器输出下载到本地以进行可靠的奖励检查（Harbor 模式）

```bash
# 运行完整基准
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6

# 运行任务子集
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6 \
    --env.task_filter fix-git,git-multibranch

# 跳过特定任务
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6 \
    --env.skip_tasks heavy-task,slow-task
```

## 创建新环境

### 训练环境

1. 在 `environments/` 下创建一个新目录
2. 创建继承自 `KClawAgentBaseEnv` 的环境文件
3. 实现四个抽象方法 + `evaluate()`

```python
from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig

class MyEnvConfig(KClawAgentEnvConfig):
    pass  # 根据需要添加自定义字段

class MyEnv(KClawAgentBaseEnv):
    name = "my-env"
    env_config_cls = MyEnvConfig

    @classmethod
    def config_init(cls):
        env_config = MyEnvConfig(
            enabled_toolsets=["terminal", "file"],
            terminal_backend="modal",
            # ... 其他配置
        )
        server_configs = [APIServerConfig(...)]
        return env_config, server_configs

    async def setup(self):
        self.dataset = load_dataset(...)
        self.iter = 0

    async def get_next_item(self):
        item = self.dataset[self.iter % len(self.dataset)]
        self.iter += 1
        return item

    def format_prompt(self, item):
        return item["instruction"]

    async def compute_reward(self, item, result, ctx):
        # ctx 为您提供对 rollout 沙盒的完整工具访问
        test = ctx.terminal("pytest -v")
        return 1.0 if test["exit_code"] == 0 else 0.0

    async def evaluate(self, *args, **kwargs):
        # 周期性评估逻辑
        ...

if __name__ == "__main__":
    MyEnv.cli()
```

### 仅评估环境（基准）

对于评估基准，请遵循 `terminalbench2_env.py` 中的模式：
1. 在 `environments/benchmarks/your-benchmark/` 下创建
2. 继承自 `KClawAgentBaseEnv`
3. 设置仅评估配置：`eval_handling=STOP_TRAIN`、`steps_per_eval=1`、`total_steps=1`
4. 存根训练方法（`collect_trajectories`、`score`）
5. 实现 `rollout_and_score_eval()` 和 `evaluate()`
6. 使用 `evaluate` 子命令运行

## 关键配置字段

| 字段 | 描述 | 默认 |
|-------|-------------|---------|
| `enabled_toolsets` | 启用哪些 kclaw 工具集 | `None`（全部） |
| `disabled_toolsets` | 禁用的工具集 | `None` |
| `distribution` | 概率工具集分发名称 | `None` |
| `max_agent_turns` | 每个 rollout 的最大 LLM 调用次数 | `30` |
| `agent_temperature` | 采样温度 | `1.0` |
| `terminal_backend` | `local`、`docker`、`modal`、`daytona`、`ssh`、`singularity` | `local` |
| `system_prompt` | 代理的系统消息 | `None` |
| `tool_call_parser` | 阶段 2 的解析器名称 | `kclaw` |
| `eval_handling` | `STOP_TRAIN`、`LIMIT_TRAIN`、`NONE` | `STOP_TRAIN` |
