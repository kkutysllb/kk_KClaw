"""
TerminalTestEnv -- 简单测试环境，用于验证技术栈

自包含环境，带内联任务（无需外部数据集）。
每个任务要求模型在已知路径创建具有特定内容的文件。
奖励验证器通过 cat 读取文件并检查内容是否匹配。

仅启用 terminal + file 工具集。默认使用 Modal 终端后端
和 OpenRouter (Claude)。

训练任务 (3 个):
    1. 创建 ~/greeting.txt，内容为 "Hello from KClaw Agent"
    2. 创建 ~/count.txt，内容为 1-5 的数字，每行一个
    3. 创建 ~/answer.txt，内容为 123 + 456 的结果

评估任务 (1 个):
    1. 创建 ~/result.txt，内容为 6 * 7 的结果

用法:
    # 启动 Atropos API 服务器
    run-api

    # 运行环境（默认使用 OpenRouter + Modal）
    python environments/terminal_test_env.py serve

    # 进程模式（不需要 run-api，保存为 JSONL）
    python environments/terminal_test_env.py process \\
        --env.data_path_to_save_groups terminal_test_output.jsonl
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保仓库根目录在 sys.path 中以便导入
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from atroposlib.envs.base import ScoredDataGroup
from atroposlib.envs.server_handling.server_manager import APIServerConfig
from atroposlib.type_definitions import Item

from environments.agent_loop import AgentResult
from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig
from environments.tool_context import ToolContext

logger = logging.getLogger(__name__)


# =============================================================================
# 内联任务定义 -- 无需外部数据集
# =============================================================================

TRAIN_TASKS = [
    {
        "prompt": "Create a file at ~/greeting.txt containing exactly the text: Hello from KClaw Agent",
        "verify_path": "~/greeting.txt",
        "expected_content": "Hello from KClaw Agent",
    },
    {
        "prompt": "Create a file at ~/count.txt containing the numbers 1 through 5, one per line",
        "verify_path": "~/count.txt",
        "expected_content": "1\n2\n3\n4\n5",
    },
    {
        "prompt": "Create a file at ~/answer.txt containing the result of 123 + 456",
        "verify_path": "~/answer.txt",
        "expected_content": "579",
    },
]

EVAL_TASKS = [
    {
        "prompt": "Create a file at ~/result.txt containing the result of 6 * 7",
        "verify_path": "~/result.txt",
        "expected_content": "42",
    },
]


class TerminalTestEnvConfig(KClawAgentEnvConfig):
    """适合终端测试的默认配置。"""

    pass  # 继承所有字段，在 config_init 中覆盖默认值


class TerminalTestEnv(KClawAgentBaseEnv):
    """
    带内联文件创建任务的简单测试环境。

    所有任务遵循相同模式: "在 ~/X.txt 创建内容为 Y 的文件"。
    验证器在 rollout 的终端中运行 `cat ~/X.txt` 并检查输出
    是否匹配预期字符串。所有任务使用相同的验证逻辑。

    此环境旨在端到端验证完整技术栈:
    - Agent 循环执行工具调用 (terminal/file)
    - ToolContext 为奖励函数提供终端访问
    - 奖励函数通过 cat 验证文件内容
    - 评分数据流经 Atropos 管线
    """

    name = "terminal-test"
    env_config_cls = TerminalTestEnvConfig

    @classmethod
    def config_init(cls) -> Tuple[TerminalTestEnvConfig, List[APIServerConfig]]:
        """
        终端测试环境的默认配置。

        使用 Modal 终端后端实现云隔离，
        OpenRouter + Claude 进行推理。API 密钥从 ~/kclaw/.env 加载。
        """
        env_config = TerminalTestEnvConfig(
            # 仅 terminal + file 工具
            enabled_toolsets=["terminal", "file"],
            disabled_toolsets=None,
            distribution=None,
            # Agent 设置
            max_agent_turns=10,  # 简单任务，不需要多轮
            max_token_length=16000,
            agent_temperature=1.0,
            system_prompt=(
                "You are a helpful assistant with access to a terminal and file tools. "
                "Complete the user's request by using the available tools. "
                "Be precise and follow instructions exactly."
            ),
            # Modal 终端后端，每个 rollout 一个云隔离沙箱
            terminal_backend="modal",
            # Atropos 设置
            group_size=3,              # 每组 3 个 rollout
            tokenizer_name="NousResearch/q-30b-t-h45-e1",
            tool_call_parser="kclaw",
            steps_per_eval=3,          # 3 步后评估
            total_steps=3,             # 总共 3 组（每步一组）
            use_wandb=True,
            wandb_name="terminal-test",
            ensure_scores_are_not_same=False,  # 允许简单任务的全同分数
            # 无外部数据集
            dataset_name=None,
        )

        # OpenRouter + Claude -- API 密钥从 .env 加载 (OPENROUTER_API_KEY)
        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-opus-4.6",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,  # OpenRouter 没有 /health 端点
            )
        ]

        return env_config, server_configs

    async def setup(self):
        """初始化内联任务列表。"""
        self.train_tasks = list(TRAIN_TASKS)
        self.eval_tasks = list(EVAL_TASKS)
        self.iter = 0
        # 跟踪奖励统计用于 wandb 日志
        self.reward_buffer: List[float] = []

    async def get_next_item(self) -> Dict[str, str]:
        """循环遍历训练任务。"""
        item = self.train_tasks[self.iter % len(self.train_tasks)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, str]) -> str:
        """提示直接在任务项中。"""
        return item["prompt"]

    async def compute_reward(
        self, item: Dict[str, str], result: AgentResult, ctx: ToolContext
    ) -> float:
        """
        通过 cat 读取预期文件路径并检查内容是否匹配来验证。
        所有任务使用相同的验证器 -- 它们都写入已知路径的文件。

        评分:
            1.0 = 完全匹配
            0.5 = 预期内容存在但有额外内容
            0.0 = 文件不存在或内容不匹配
        """
        verify_result = ctx.terminal(f"cat {item['verify_path']}")

        # 文件不存在或无法读取
        if verify_result["exit_code"] != 0:
            self.reward_buffer.append(0.0)
            return 0.0

        actual = verify_result.get("output", "").strip()
        expected = item["expected_content"].strip()

        # 完全匹配
        if actual == expected:
            self.reward_buffer.append(1.0)
            return 1.0

        # 部分积分: 预期内容存在但有额外内容
        if expected in actual:
            self.reward_buffer.append(0.5)
            return 0.5

        self.reward_buffer.append(0.0)
        return 0.0

    async def evaluate(self, *args, **kwargs):
        """
        使用 Agent 循环运行评估任务并验证结果。
        记录准确率指标。
        """
        start_time = time.time()
        correct = 0
        total = len(self.eval_tasks)
        samples = []

        for eval_item in self.eval_tasks:
            try:
                # 评估使用简单的单轮补全（非完整 Agent 循环）
                # 以保持评估快速。Agent 循环通过训练测试。
                completion = await self.server.chat_completion(
                    messages=[
                        {"role": "system", "content": self.config.system_prompt or ""},
                        {"role": "user", "content": eval_item["prompt"]},
                    ],
                    n=1,
                    max_tokens=self.config.max_token_length,
                    temperature=0.0,
                    split="eval",
                )

                response_content = (
                    completion.choices[0].message.content if completion.choices else ""
                )

                samples.append(
                    {
                        "prompt": eval_item["prompt"],
                        "response": response_content,
                        "expected": eval_item["expected_content"],
                    }
                )

            except Exception as e:
                logger.error("评估项目失败: %s", e)
                samples.append(
                    {
                        "prompt": eval_item["prompt"],
                        "response": f"ERROR: {e}",
                        "expected": eval_item["expected_content"],
                    }
                )

        end_time = time.time()

        eval_metrics = {
            "eval/num_samples": total,
        }

        await self.evaluate_log(
            metrics=eval_metrics,
            samples=samples,
            start_time=start_time,
            end_time=end_time,
        )

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """记录训练指标，包括奖励统计和准确率。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        if self.reward_buffer:
            total = len(self.reward_buffer)
            correct = sum(1 for r in self.reward_buffer if r == 1.0)
            partial = sum(1 for r in self.reward_buffer if r == 0.5)

            wandb_metrics["train/avg_reward"] = sum(self.reward_buffer) / total
            wandb_metrics["train/accuracy"] = correct / total
            wandb_metrics["train/partial_match_rate"] = partial / total
            wandb_metrics["train/total_rollouts"] = total
            self.reward_buffer = []

        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    TerminalTestEnv.cli()
