"""
KClawAgentBaseEnv -- KClaw-Agent + Atropos 的抽象基环境

提供所有 kclaw 环境共享的 Atropos 集成管道：
- 双模式操作（第一阶段使用 OpenAI 服务器，第二阶段使用 VLLM ManagedServer）
- 每个分组的工具集/分布解析
- 通过 KClawAgentLoop 进行 Agent 循环编排
- 用于奖励函数的 ToolContext 创建
- 从 ManagedServer 状态构建 ScoredDataGroup

子类只需实现：
    setup()           -- 加载数据集，初始化状态
    get_next_item()   -- 从数据集中返回下一个项目
    format_prompt()   -- 将数据集项目转换为用户消息
    compute_reward()  -- 对 rollout 进行评分（可完全访问 ToolContext）
    evaluate()        -- 定期评估
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# 确保 kclaw 仓库根目录在 sys.path 上，以便无论从哪里调用脚本，
# 都能正确导入 `from model_tools import ...` 和 `from environments.X import ...`。
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from pydantic import Field

# 从 kclaw/.env 加载 API 密钥，以便所有环境都能访问它们
_env_path = _repo_root / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)

# 应用猴子补丁以在 Atropos 的事件循环内实现异步安全的工具操作。
# 这会修补 SwerexModalEnvironment 使用后台线程而不是 asyncio.run()，
# 后者会在 Atropos 内部导致死锁。对普通 CLI 也安全。
from environments.patches import apply_patches
apply_patches()

from atroposlib.envs.base import (
    BaseEnv,
    BaseEnvConfig,
    ScoredDataGroup,
    ScoredDataItem,
)
from atroposlib.envs.server_handling.server_manager import (
    APIServerConfig,
    ServerBaseline,
    ServerManager,
)
from atroposlib.type_definitions import Item

from environments.agent_loop import AgentResult, KClawAgentLoop
from environments.tool_context import ToolContext
from tools.budget_config import (
    DEFAULT_RESULT_SIZE_CHARS,
    DEFAULT_TURN_BUDGET_CHARS,
    DEFAULT_PREVIEW_SIZE_CHARS,
)

# 导入 kclaw 工具集基础设施
from model_tools import get_tool_definitions
from toolset_distributions import sample_toolsets_from_distribution

logger = logging.getLogger(__name__)


class KClawAgentEnvConfig(BaseEnvConfig):
    """
    kclaw Atropos 环境的配置。

    在 BaseEnvConfig 基础上扩展了特定于 Agent 的设置，包括工具集、
    终端后端、数据集加载和工具调用解析。
    """

    # --- 工具集配置 ---
    # 互斥：使用 enabled_toolsets 或 distribution 之一
    enabled_toolsets: Optional[List[str]] = Field(
        default=None,
        description="kclaw 工具集的显式列表（例如 ['terminal', 'file', 'web']）。"
        "如果为 None 且 distribution 也为 None，则启用所有可用的工具集。",
    )
    disabled_toolsets: Optional[List[str]] = Field(
        default=None,
        description="要禁用的工具集。作为过滤器应用于 enabled_toolsets 或 distribution 之上。",
    )
    distribution: Optional[str] = Field(
        default=None,
        description="来自 toolset_distributions.py 的工具集分布名称"
        "（例如 'development', 'terminal_tasks'）。每个分组采样一次。"
        "与 enabled_toolsets 互斥。",
    )

    # --- Agent 循环配置 ---
    max_agent_turns: int = Field(
        default=30,
        description="每个 rollout 的最大 LLM 调用次数（工具调用迭代次数）。",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Agent 的系统提示词。工具通过 tools= 参数处理，"
        "不嵌入在提示词文本中。",
    )
    agent_temperature: float = Field(
        default=1.0,
        description="rollout 期间 Agent 生成的采样温度。",
    )

    # --- 终端后端 ---
    terminal_backend: str = Field(
        default="local",
        description="终端后端: 'local'、'docker'、'modal'、'daytona'、'ssh'、'singularity'。"
        "生产级 RL 推荐使用 Modal 或 Daytona（每个 rollout 云端隔离）。",
    )
    terminal_timeout: int = Field(
        default=120,
        description="终端工具调用的每个命令超时时间（秒）。"
        "超过此时间的命令会被终止。对于有长时间运行命令的任务增加此值"
        "（编译、pip install 等）。",
    )
    terminal_lifetime: int = Field(
        default=3600,
        description="沙箱不活动生命周期（秒）。清理线程会终止"
        "空闲时间超过此值的沙箱。必须大于工具调用之间的最大间隔"
        "（例如等待 LLM 响应的时间）。",
    )

    # --- 数据集 ---
    dataset_name: Optional[str] = Field(
        default=None,
        description="HuggingFace 数据集名称。如果任务定义为内联则可选。",
    )
    dataset_split: str = Field(
        default="train",
        description="要使用的数据集划分。",
    )
    prompt_field: str = Field(
        default="prompt",
        description="数据集中包含提示词的字段。",
    )

    # --- 线程池 ---
    tool_pool_size: int = Field(
        default=128,
        description="工具执行的线程池大小。每个并发任务需要一个"
        "线程用于工具调用。必须足够大以支持并行评估。"
        "太小会导致线程池耗尽。",
    )

    # --- 第二阶段: 工具调用解析 ---
    tool_call_parser: str = Field(
        default="kclaw",
        description="第二阶段（VLLM 服务器类型）的工具调用解析器名称。"
        "在第一阶段（OpenAI 服务器类型，VLLM 本地解析）中被忽略。"
        "选项: kclaw、mistral、llama3_json、qwen、deepseek_v3 等。",
    )

    # --- 工具结果预算 ---
    # 默认值从 tools.budget_config 导入（单一真实来源）。
    default_result_size_chars: int = Field(
        default=DEFAULT_RESULT_SIZE_CHARS,
        description="持久化大结果到沙箱的默认每个工具阈值（字符数）。"
        "超过此值的结果会被写入 /tmp/kclaw-results/"
        "并替换为预览。每个工具的注册表值优先，"
        "除非通过 tool_result_overrides 覆盖。",
    )
    turn_budget_chars: int = Field(
        default=DEFAULT_TURN_BUDGET_CHARS,
        description="每个助手轮次的聚合字符预算。如果单个轮次中的所有工具结果"
        "超过此值，则首先将最大的结果持久化到磁盘。",
    )
    preview_size_chars: int = Field(
        default=DEFAULT_PREVIEW_SIZE_CHARS,
        description="工具结果持久化后显示的内联预览大小。",
    )
    tool_result_overrides: Optional[Dict[str, int]] = Field(
        default=None,
        description="每个工具的阈值覆盖（字符数）。键是工具名称，"
        "值是字符阈值。同时覆盖默认值和注册表的每个工具值。"
        "示例: {'terminal': 10000, 'search_files': 5000}。"
        "注意: read_file 固定为无穷大，无法覆盖。",
    )

    # --- Provider 特定参数 ---
    # 作为 extra_body 传递给 OpenAI 客户端的 chat.completions.create() 调用。
    # 用于 OpenRouter provider 偏好设置、transforms、路由设置等。
    # 示例 YAML:
    #   extra_body:
    #     provider:
    #       ignore: ["DeepInfra", "Fireworks"]
    #       order: ["Together"]
    #     transforms: ["middle-out"]
    extra_body: Optional[Dict[str, Any]] = Field(
        default=None,
        description="传递给 OpenAI 客户端 "
        "chat.completions.create() 的额外 body 参数。用于 OpenRouter provider 偏好设置、"
        "transforms 和其他 provider 特定设置。",
    )

    def build_budget_config(self):
        """从环境配置字段构建 BudgetConfig。"""
        from tools.budget_config import BudgetConfig
        return BudgetConfig(
            default_result_size=self.default_result_size_chars,
            turn_budget=self.turn_budget_chars,
            preview_size=self.preview_size_chars,
            tool_overrides=dict(self.tool_result_overrides) if self.tool_result_overrides else {},
        )


class KClawAgentBaseEnv(BaseEnv):
    """
    kclaw Atropos 集成的抽象基环境。

    处理两种操作模式:
    - 第一阶段（OpenAI 服务器类型）：直接使用 server.chat_completion()。
      服务器（VLLM、SGLang、OpenRouter、OpenAI）原生处理工具调用解析
      和推理提取。DummyManagedServer 提供占位符 token。适合 SFT 数据生成、
      验证器测试、评估。

    - 第二阶段（VLLM 服务器类型）：使用 ManagedServer 通过 /generate 获取
      精确的 token ID + logprobs。客户端工具调用解析器从原始输出重建结构化
      tool_calls。具备完整的 RL 训练能力。

    子类必须实现:
        setup()           -- 加载数据集，初始化状态
        get_next_item()   -- 返回要 roll out 的下一个项目
        format_prompt()   -- 将数据集项目转换为用户消息字符串
        compute_reward()  -- 使用 ToolContext 对 rollout 进行评分
        evaluate()        -- 定期评估
    """

    name: Optional[str] = "kclaw"
    env_config_cls = KClawAgentEnvConfig

    def __init__(
        self,
        config: KClawAgentEnvConfig,
        server_configs: Union[ServerBaseline, List[APIServerConfig]],
        slurm=False,
        testing=False,
    ):
        super().__init__(config, server_configs, slurm, testing)

        # 设置终端环境变量，以便 kclaw 工具能获取它们。
        # 这些都可以通过配置字段而不是要求用户设置 shell 环境变量来覆盖。
        if config.terminal_backend:
            os.environ["TERMINAL_ENV"] = config.terminal_backend
        os.environ["TERMINAL_TIMEOUT"] = str(config.terminal_timeout)
        os.environ["TERMINAL_LIFETIME_SECONDS"] = str(config.terminal_lifetime)
        print(
            f"🖥️  Terminal: backend={config.terminal_backend}, "
            f"timeout={config.terminal_timeout}s, lifetime={config.terminal_lifetime}s"
        )

        # 调整 Agent 循环的工具执行线程池大小。
        # 这必须足够大以容纳并发任务数量
        #（例如，89 个并行 TB2 评估任务每个都需要一个线程用于工具调用）。
        from environments.agent_loop import resize_tool_pool
        resize_tool_pool(config.tool_pool_size)

        # 在 ServerManager 上设置 tool_parser，以便 ManagedServer 使用它
        # 进行双向工具调用翻译（原始文本 ↔ OpenAI tool_calls）。
        if hasattr(self.server, 'tool_parser'):
            self.server.tool_parser = config.tool_call_parser
            print(f"🔧 Tool parser: {config.tool_call_parser}")

        # 当前分组的已解析工具（在 collect_trajectories 中设置）
        self._current_group_tools: Optional[Tuple[List[Dict], Set[str]]] = None

        # 用于 wandb 日志的工具错误跟踪
        self._tool_error_buffer: List[Dict[str, Any]] = []

    # =========================================================================
    # 工具集解析（每个分组）
    # =========================================================================

    def _resolve_tools_for_group(self) -> Tuple[List[Dict[str, Any]], Set[str]]:
        """
        为分组解析工具集。在 collect_trajectories() 中调用一次，
        然后由分组中所有 collect_trajectory() 调用共享。

        如果设置了 distribution，则进行概率采样。
        如果设置了 enabled_toolsets，则使用该显式列表。
        disabled_toolsets 作为过滤器应用于上述结果之上。

        返回:
            (tool_schemas, valid_tool_names) 元组
        """
        config = self.config

        if config.distribution:
            group_toolsets = sample_toolsets_from_distribution(config.distribution)
            logger.info("从 '%s' 采样工具集: %s", config.distribution, group_toolsets)
        else:
            group_toolsets = config.enabled_toolsets  # None 表示“所有可用的”
            if group_toolsets is None:
                logger.warning(
                    "enabled_toolsets 为 None -- 加载所有工具包括消息工具。"
                    "为 RL 训练设置明确的 enabled_toolsets。"
                )

        tools = get_tool_definitions(
            enabled_toolsets=group_toolsets,
            disabled_toolsets=config.disabled_toolsets,
            quiet_mode=True,
        )

        valid_names = {t["function"]["name"] for t in tools} if tools else set()
        logger.info("为分组解析了 %d 个工具: %s", len(valid_names), sorted(valid_names))
        return tools, valid_names

    # =========================================================================
    # 服务器模式检测
    # =========================================================================

    def _use_managed_server(self) -> bool:
        """
        确定是否应使用 ManagedServer（第二阶段）还是直接服务器（第一阶段）。

        当服务器类型为 'vllm' 或 'sglang' 时使用第二阶段（ManagedServer），
        这些服务器通过 /generate 端点进行精确的 token 跟踪。

        当服务器类型为 'openai' 时使用第一阶段（直接服务器），
        使用 /v1/chat/completions 进行原生工具调用解析。
        """
        if not self.server.servers:
            return False

        server = self.server.servers[0]
        # 如果是 OpenAI 服务器（非 VLLM/SGLang），使用直接模式
        from atroposlib.envs.server_handling.openai_server import OpenAIServer
        return not isinstance(server, OpenAIServer)

    # =========================================================================
    # 核心 Atropos 集成
    # =========================================================================

    async def collect_trajectories(
        self, item: Item
    ) -> Tuple[
        Union[Optional[ScoredDataGroup], List[Optional[ScoredDataGroup]]],
        List[Item],
    ]:
        """
        重写 collect_trajectories 以便每个分组解析一次工具集，
        然后委托给标准分组级收集。

        默认的 BaseEnv.collect_trajectories() 并行调用 collect_trajectory()
        group_size 次。我们在这里解析一次工具并存储它们，供给所有这些调用使用。
        """
        # 为此分组解析工具集（由分组中的所有 rollouts 共享）
        self._current_group_tools = self._resolve_tools_for_group()

        # 委托给调用 collect_trajectory() 的默认实现
        # group_size 次通过 asyncio.gather
        return await super().collect_trajectories(item)

    # =========================================================================
    # Wandb rollout 显示 — 格式化轨迹以便查看
    # =========================================================================

    @staticmethod
    def _format_trajectory_for_display(messages: List[Dict[str, Any]]) -> str:
        """
        将对话消息格式化为可读的轨迹字符串，
        用于 wandb rollout 表格。以结构化方式显示工具调用、工具结果和推理，
        而不是原始 token 解码。
        """
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "system":
                parts.append(f"[SYSTEM]\n{content}")

            elif role == "user":
                parts.append(f"[USER]\n{content}")

            elif role == "assistant":
                # 如果存在推理则显示
                reasoning = msg.get("reasoning_content", "")
                if reasoning:
                    # 截断过长的推理以供显示
                    if len(reasoning) > 300:
                        reasoning = reasoning[:300] + "..."
                    parts.append(f"[ASSISTANT thinking]\n{reasoning}")

                # 显示内容
                if content:
                    parts.append(f"[ASSISTANT]\n{content}")

                # 显示工具调用
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args = func.get("arguments", "{}")
                    # 截断过长的参数以供显示
                    if len(args) > 200:
                        args = args[:200] + "..."
                    parts.append(f"[TOOL CALL] {name}({args})")

            elif role == "tool":
                tool_id = msg.get("tool_call_id", "")
                result = content
                # 截断过长的工具结果以供显示
                if len(result) > 500:
                    result = result[:500] + "..."
                parts.append(f"[TOOL RESULT] {result}")

        return "\n\n".join(parts)

    async def add_rollouts_for_wandb(
        self,
        scored_data,
        item=None,
    ):
        """
        重写以显示格式化的轨迹（工具调用可见），
        而不是丢失所有结构的原始 token 解码。
        """
        num_keep = self.config.num_rollouts_per_group_for_logging
        if num_keep == -1:
            num_keep = self.config.group_size

        group = []
        for i in range(min(num_keep, len(scored_data.get("scores", [])))):
            score = scored_data["scores"][i]

            # 如果可用则使用 messages 以便丰富显示
            messages = None
            if scored_data.get("messages") and i < len(scored_data["messages"]):
                messages = scored_data["messages"][i]

            if messages:
                text = self._format_trajectory_for_display(messages)
            elif scored_data.get("tokens") and i < len(scored_data["tokens"]):
                text = self.tokenizer.decode(scored_data["tokens"][i])
            else:
                text = "(无数据)"

            group.append((text, score))

        self.rollouts_for_wandb.append(group)
        if len(self.rollouts_for_wandb) > self.config.num_rollouts_to_keep:
            self.rollouts_for_wandb.pop(0)

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """将基础指标（包括工具错误）记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        # 记录工具错误统计
        if self._tool_error_buffer:
            wandb_metrics["train/tool_errors_count"] = len(self._tool_error_buffer)

            # 将错误详情记录为摘要字符串（表格在临时文件清理时可能崩溃）
            error_summaries = []
            for err in self._tool_error_buffer:
                error_summaries.append(
                    f"[turn {err['turn']}] {err['tool']}({err['args'][:80]}) -> {err['error'][:150]}"
                )
            wandb_metrics["train/tool_error_details"] = "\n".join(error_summaries)

            # 也打印到标准输出以便立即可见
            for summary in error_summaries:
                print(f"  工具错误: {summary}")

            self._tool_error_buffer = []
        else:
            wandb_metrics["train/tool_errors_count"] = 0

        await super().wandb_log(wandb_metrics)

    async def collect_trajectory(
        self, item: Item
    ) -> Tuple[Optional[Union[ScoredDataItem, Any]], List[Item]]:
        """
        运行单个 rollout：Agent 循环 + 奖励计算。

        由 collect_trajectories() 并行调用 group_size 次。
        每次调用都有自己唯一的 task_id 用于终端/浏览器会话隔离。
        """
        task_id = str(uuid.uuid4())

        # 获取分组级工具（在 collect_trajectories 中解析一次）
        if self._current_group_tools is None:
            # 后备方案：如果在 collect_trajectories 外部调用，则每个轨迹解析一次
            tools, valid_names = self._resolve_tools_for_group()
        else:
            tools, valid_names = self._current_group_tools

        # 构建初始消息
        messages: List[Dict[str, Any]] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        messages.append({"role": "user", "content": self.format_prompt(item)})

        # 运行 Agent 循环
        result: AgentResult
        if self._use_managed_server():
            # 第二阶段: ManagedServer 配合 ToolCallTranslator — 精确 tokens + logprobs
            # tool_parser 在 __init__ 中设置在 ServerManager 上并传递给
            # ManagedServer，后者使用 ToolCallTranslator 进行原始文本与
            # OpenAI tool_calls 之间的双向翻译。
            try:
                async with self.server.managed_server(
                    tokenizer=self.tokenizer,
                    preserve_think_blocks=bool(self.config.thinking_mode),
                ) as managed:
                    agent = KClawAgentLoop(
                        server=managed,
                        tool_schemas=tools,
                        valid_tool_names=valid_names,
                        max_turns=self.config.max_agent_turns,
                        task_id=task_id,
                        temperature=self.config.agent_temperature,
                        max_tokens=self.config.max_token_length,
                        extra_body=self.config.extra_body,
                        budget_config=self.config.build_budget_config(),
                    )
                    result = await agent.run(messages)
            except NotImplementedError:
                # 不允许 DummyManagedServer — 回退到第一阶段
                logger.warning(
                    "ManagedServer 不可用（OpenAI 服务器？）。"
                    "回退到直接服务器模式。"
                )
                agent = KClawAgentLoop(
                    server=self.server,
                    tool_schemas=tools,
                    valid_tool_names=valid_names,
                    max_turns=self.config.max_agent_turns,
                    task_id=task_id,
                    temperature=self.config.agent_temperature,
                    max_tokens=self.config.max_token_length,
                    extra_body=self.config.extra_body,
                    budget_config=self.config.build_budget_config(),
                )
                result = await agent.run(messages)
        else:
            # 第一阶段: OpenAI 服务器 — 原生 tool_calls，占位符 tokens
            agent = KClawAgentLoop(
                server=self.server,
                tool_schemas=tools,
                valid_tool_names=valid_names,
                max_turns=self.config.max_agent_turns,
                task_id=task_id,
                temperature=self.config.agent_temperature,
                max_tokens=self.config.max_token_length,
                extra_body=self.config.extra_body,
                budget_config=self.config.build_budget_config(),
            )
            result = await agent.run(messages)

        # 如果 Agent 循环没有产生有意义的工作则跳过奖励计算
        #（例如第一轮 API 调用失败）。不值得启动 Modal 沙箱
        # 来验证从未创建的文件。
        only_system_and_user = all(
            msg.get("role") in ("system", "user") for msg in result.messages
        )
        if result.turns_used == 0 or only_system_and_user:
            logger.warning(
                "Agent 循环未产生输出（turns=%d, msgs=%d）。跳过奖励。",
                result.turns_used, len(result.messages),
            )
            reward = 0.0
        else:
            # 使用 ToolContext 计算奖励（给验证器完整工具访问权限）
            ctx = ToolContext(task_id)
            try:
                reward = await self.compute_reward(item, result, ctx)
            except Exception as e:
                logger.error("compute_reward 失败: %s", e)
                reward = 0.0
            finally:
                ctx.cleanup()

        # 跟踪工具错误用于 wandb 日志
        if result.tool_errors:
            for err in result.tool_errors:
                self._tool_error_buffer.append({
                    "turn": err.turn,
                    "tool": err.tool_name,
                    "args": err.arguments[:150],
                    "error": err.error[:300],
                    "result": err.tool_result[:300],
                })

        # 从 ManagedServer 状态构建 ScoredDataItem
        # 第二阶段: SequenceNodes 的真实 tokens/masks/logprobs
        # 第一阶段: 占位符 tokens（仍然需要有效的 ScoredDataItem 用于管道）
        nodes = (result.managed_state or {}).get("nodes", [])

        if nodes:
            # 第二阶段（或 DummyManagedServer）：使用实际节点数据
            node = nodes[-1]  # 最终序列节点 = 完整轨迹
            scored_item: Dict[str, Any] = {
                "tokens": node.tokens,
                "masks": node.masked_tokens,
                "scores": reward,
            }

            # 如果有 logprobs 则包含（第二阶段）
            if hasattr(node, "logprobs") and node.logprobs:
                scored_item["advantages"] = None  # 由训练器计算
                scored_item["ref_logprobs"] = None
        else:
            # 第一阶段没有 managed 状态：创建占位符 tokens
            # 以便数据管道不会中断。这些不适合
            # 用于训练但允许流程模式（SFT 数据生成）工作。
            # 对完整对话进行分词以获取近似 tokens。
            full_text = "\n".join(
                msg.get("content", "") for msg in result.messages if msg.get("content")
            )
            if self.tokenizer:
                tokens = self.tokenizer.encode(full_text, add_special_tokens=True)
            else:
                tokens = list(range(min(len(full_text) // 4, 128)))

            scored_item = {
                "tokens": tokens,
                "masks": [-100] + tokens[1:],  # 将第一个 token 作为提示词掩码
                "scores": reward,
            }

        # 始终包含 messages 用于 wandb rollout 显示和数据日志记录
        scored_item["messages"] = result.messages

        return scored_item, []

    # =========================================================================
    # 抽象方法 — 子类必须实现
    # =========================================================================

    @abstractmethod
    async def setup(self):
        """
        加载数据集，初始化状态。

        在环境启动时调用一次。典型实现：
            self.dataset = load_dataset(self.config.dataset_name, split=self.config.dataset_split)
            self.iter = 0
        """
        raise NotImplementedError

    @abstractmethod
    async def get_next_item(self) -> Item:
        """
        从数据集中返回下一个用于 rollout 的项目。

        由基环境的 main loop 调用以获取 worker 的项目。
        应该循环遍历数据集。
        """
        raise NotImplementedError

    @abstractmethod
    def format_prompt(self, item: Item) -> str:
        """
        将数据集项目转换为 Agent 的用户消息。

        参数:
            item: 数据集项目（字典、元组等）

        返回:
            要发送给 Agent 的提示词字符串
        """
        raise NotImplementedError

    @abstractmethod
    async def compute_reward(
        self, item: Item, result: AgentResult, ctx: ToolContext
    ) -> float:
        """
        对 rollout 进行评分。可完全访问：
        - item: 原始数据集项目（ground truth、测试命令等）
        - result: 包含完整消息、轮次计数、推理等的 AgentResult
        - ctx: ToolContext — 调用任意 kclaw 工具（terminal、file、web、
               browser、vision...），作用域限定在此 rollout 的沙箱中。
               没有任何限制。

        参数:
            item: 被 rollout 的数据集项目
            result: Agent 的 rollout 结果
            ctx: 具有完整工具访问权限的 ToolContext 用于验证

        返回:
            奖励浮点数（通常为 0.0 到 1.0，但任何浮点数都有效）
        """
        raise NotImplementedError

    @abstractmethod
    async def evaluate(self, *args, **kwargs):
        """
        定期评估。每隔 steps_per_eval 步调用一次。

        典型实现在留出的评估集上运行 Agent，
        并通过 wandb/evaluate_log 记录指标。
        """
        raise NotImplementedError
