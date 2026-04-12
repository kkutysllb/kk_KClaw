"""
OpenThoughts-TBLite 评估环境

Terminal-Bench 2.0 的轻量、快速替代方案，用于迭代终端 Agent。
使用与 TerminalBench2EvalEnv 相同的评估逻辑，但默认使用
NousResearch/openthoughts-tblite 数据集（100 个难度校准任务 vs TB2 的 89 个更难任务）。

TBLite 任务是 TB2 的精选子集，其难度分布设计为即使是较小的模型也能获得有意义的信号：
  - 简单（40 个任务）: >= 70% 通过率（Claude Haiku 4.5）
  - 中等（26 个任务）: 40-69% 通过率
  - 困难（26 个任务）: 10-39% 通过率
  - 极端（8 个任务）:  < 10% 通过率

用法:
    python environments/benchmarks/tblite/tblite_env.py evaluate

    # 过滤特定任务:
    python environments/benchmarks/tblite/tblite_env.py evaluate \\
        --env.task_filter "broken-python,pandas-etl"
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.benchmarks.terminalbench_2.terminalbench2_env import (
    TerminalBench2EvalConfig,
    TerminalBench2EvalEnv,
)


class TBLiteEvalConfig(TerminalBench2EvalConfig):
    """OpenThoughts-TBLite 评估环境配置。

    继承所有 TB2 配置字段。仅数据集默认值和任务超时不同 --
    TBLite 任务经校准更快完成。
    """

    dataset_name: str = Field(
        default="NousResearch/openthoughts-tblite",
        description="包含 TBLite 任务的 HuggingFace 数据集。",
    )

    task_timeout: int = Field(
        default=1200,
        description="每个任务的最大挂钟时间（秒）。TBLite 任务"
        "通常比 TB2 快，因此 20 分钟通常足够。",
    )


class TBLiteEvalEnv(TerminalBench2EvalEnv):
    """OpenThoughts-TBLite 评估环境。

    继承 TerminalBench2EvalEnv 的所有评估逻辑（Agent 循环、
    测试验证、Docker 镜像解析、指标、wandb 日志）。
    仅默认配置不同。
    """

    name = "openthoughts-tblite"
    env_config_cls = TBLiteEvalConfig

    @classmethod
    def config_init(cls) -> Tuple[TBLiteEvalConfig, List[APIServerConfig]]:
        env_config = TBLiteEvalConfig(
            enabled_toolsets=["terminal", "file"],
            disabled_toolsets=None,
            distribution=None,

            max_agent_turns=60,
            max_token_length=16000,
            agent_temperature=0.6,
            system_prompt=None,

            terminal_backend="modal",
            terminal_timeout=300,

            test_timeout=180,

            # 100 个任务并行
            tool_pool_size=128,

            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,

            tokenizer_name="NousResearch/KClaw-3-Llama-3.1-8B",
            use_wandb=True,
            wandb_name="openthoughts-tblite",
            ensure_scores_are_not_same=False,
        )

        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs


if __name__ == "__main__":
    TBLiteEvalEnv.cli()
