"""
KClaw-Agent Atropos 强化学习训练环境

提供 kclaw 工具调用能力与 Atropos 强化学习训练框架之间的分层集成。

核心层级:
    - agent_loop: 可复用的多轮 Agent 循环，支持标准 OpenAI 规范的工具调用
    - tool_context: 每个 rollout 的工具访问句柄，用于奖励/验证函数
    - kclaw_base_env: Atropos 的抽象基环境（BaseEnv 子类）
    - tool_call_parsers: 客户端工具调用解析器注册表，用于第二阶段（VLLM /generate）

具体环境实现:
    - terminal_test_env/: 用于测试栈的简单文件创建任务
    - kclaw_swe_env/: 使用 Modal 沙箱的 SWE-bench 风格任务

基准测试（仅用于评估）:
    - benchmarks/terminalbench_2/: Terminal-Bench 2.0 评估
"""

try:
    from environments.agent_loop import AgentResult, KClawAgentLoop
    from environments.tool_context import ToolContext
    from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig
except ImportError:
    # atroposlib 未安装 — 环境不可用，但子模块如 tool_call_parsers 仍可直接导入。
    pass

__all__ = [
    "AgentResult",
    "KClawAgentLoop",
    "ToolContext",
    "KClawAgentBaseEnv",
    "KClawAgentEnvConfig",
]
