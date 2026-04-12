"""
KClawSweEnv -- 带有 Modal 沙箱的 SWE-Bench 风格环境

用于软件工程任务的具体环境，模型编写代码，
奖励函数运行测试来验证正确性。使用 Modal 终端后端
为每个 rollout 提供云隔离沙箱。

奖励函数使用 ToolContext.terminal() 在模型
Agent 循环期间使用的同一 Modal 沙箱中运行测试命令。
模型工具调用的所有文件系统状态都保留用于验证。

用法:
    # 阶段 1: OpenAI 服务器类型
    vllm serve YourModel --tool-parser kclaw
    run-api
    python environments/kclaw_swe_env.py serve \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name YourModel \\
        --openai.server_type openai \\
        --env.dataset_name bigcode/humanevalpack \\
        --env.terminal_backend modal

    # 阶段 2: VLLM 服务器类型（完整 RL 训练）
    python environments/kclaw_swe_env.py serve \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name YourModel \\
        --openai.server_type vllm \\
        --env.tool_call_parser kclaw \\
        --env.terminal_backend modal
"""

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保仓库根目录在 sys.path 中以便导入
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from datasets import load_dataset

from atroposlib.envs.base import ScoredDataGroup
from atroposlib.envs.server_handling.server_manager import APIServerConfig
from atroposlib.type_definitions import Item

from environments.agent_loop import AgentResult
from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig
from environments.tool_context import ToolContext

logger = logging.getLogger(__name__)


class KClawSweEnvConfig(KClawAgentEnvConfig):
    """SWE-bench 风格任务的配置及默认值。"""

    pass  # 继承所有字段，在 config_init 中覆盖默认值


class KClawSweEnv(KClawAgentBaseEnv):
    """
    使用 Modal 终端后端的 SWE-bench 风格环境。

    模型获取编程任务，使用 terminal + file + web 工具解决，
    奖励函数在同一 Modal 沙箱中运行测试来验证。

    子类化此环境以适配特定 SWE 数据集（HumanEval、SWE-bench 等），
    按需自定义 format_prompt() 和 compute_reward()。
    """

    name = "kclaw-swe"
    env_config_cls = KClawSweEnvConfig

    @classmethod
    def config_init(cls) -> Tuple[KClawSweEnvConfig, List[APIServerConfig]]:
        """
        SWE 环境的默认配置。

        使用 Modal 终端后端实现云隔离，以及 terminal + file + web 工具集。
        """
        env_config = KClawSweEnvConfig(
            # 工具集: terminal 用于运行代码，file 用于读/写，web 用于查阅文档
            enabled_toolsets=["terminal", "file", "web"],
            disabled_toolsets=None,
            distribution=None,
            # Agent 设置 -- SWE 任务需要更多轮次
            max_agent_turns=30,
            max_token_length=4096,
            agent_temperature=1.0,
            system_prompt=(
                "You are a skilled software engineer. You have access to a terminal, "
                "file tools, and web search. Use these tools to complete the coding task. "
                "Write clean, working code and verify it runs correctly before finishing."
            ),
            # Modal 后端，云隔离沙箱
            terminal_backend="modal",
            # 数据集 -- 通过 CLI 覆盖为你的特定 SWE 数据集
            dataset_name="bigcode/humanevalpack",
            dataset_split="test",
            prompt_field="prompt",
            # Atropos 设置
            group_size=4,
            tokenizer_name="NousResearch/DeepKClaw-3-Llama-3-3B-Preview",
            tool_call_parser="kclaw",
            steps_per_eval=50,
            total_steps=500,
            use_wandb=True,
            wandb_name="kclaw-swe",
        )

        server_configs = [
            APIServerConfig(
                base_url="http://localhost:8000/v1",
                model_name="NousResearch/DeepKClaw-3-Llama-3-3B-Preview",
                server_type="openai",  # 阶段 1; 切换为 "vllm" 以进入阶段 2
                api_key="",
            )
        ]

        return env_config, server_configs

    async def setup(self):
        """加载 SWE 数据集。"""
        if self.config.dataset_name:
            self.dataset = load_dataset(
                self.config.dataset_name, split=self.config.dataset_split
            )
        else:
            # 如果未指定数据集的占位符
            self.dataset = []
        self.iter = 0
        self.reward_buffer: List[float] = []

    async def get_next_item(self) -> Dict[str, Any]:
        """循环遍历 SWE 数据集。"""
        if not self.dataset:
            raise ValueError("未加载数据集。请在配置中设置 dataset_name。")
        item = self.dataset[self.iter % len(self.dataset)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        """
        格式化 SWE 任务提示。

        在子类中覆盖以适配不同的数据集格式。
        默认假设数据集有 'prompt' 字段和可选的 'test' 字段。
        """
        prompt = item.get(self.config.prompt_field, "")

        # 如果数据集有测试信息，将其包含在提示中
        test_info = item.get("test", item.get("test_code", item.get("tests", "")))
        if test_info:
            prompt += f"\n\n需要通过的测试:\n{test_info}"

        return prompt

    async def compute_reward(
        self, item: Dict[str, Any], result: AgentResult, ctx: ToolContext
    ) -> float:
        """
        通过在模型的 Modal 沙箱中运行测试来评分。

        默认实现:
        - 如果数据集项有 'test' 或 'test_code' 字段，运行它
        - 检查退出码: 0 = 通过，非零 = 失败
        - 文件创建给予部分积分

        在子类中覆盖以实现更复杂的奖励逻辑。
        """
        # 从数据集项中查找测试命令
        test_code = item.get("test", item.get("test_code", item.get("tests", "")))

        if test_code:
            # 在模型的沙箱中运行测试
            test_result = ctx.terminal(
                f'cd /workspace && python3 -c "{test_code}"', timeout=60
            )

            if test_result["exit_code"] == 0:
                self.reward_buffer.append(1.0)
                return 1.0

        # 部分积分: 检查模型是否创建了任何 Python 文件
        file_check = ctx.terminal("find /workspace -name '*.py' -newer /tmp/.start_marker 2>/dev/null | head -5")
        if file_check["exit_code"] == 0 and file_check.get("output", "").strip():
            self.reward_buffer.append(0.1)
            return 0.1

        self.reward_buffer.append(0.0)
        return 0.0

    async def evaluate(self, *args, **kwargs):
        """
        在保留集上运行评估。

        覆盖以实现数据集特定的评估逻辑。
        """
        start_time = time.time()
        end_time = time.time()

        eval_metrics = {"eval/placeholder": 0.0}
        await self.evaluate_log(
            metrics=eval_metrics,
            start_time=start_time,
            end_time=end_time,
        )

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """记录 SWE 特定指标。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        if self.reward_buffer:
            wandb_metrics["train/avg_reward"] = sum(self.reward_buffer) / len(
                self.reward_buffer
            )
            wandb_metrics["train/pass_rate"] = sum(
                1 for r in self.reward_buffer if r == 1.0
            ) / len(self.reward_buffer)
            self.reward_buffer = []

        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    KClawSweEnv.cli()
