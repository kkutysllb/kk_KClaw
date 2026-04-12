"""
YCBenchEvalEnv -- YC-Bench 长期 Agent 基准评估环境

在 YC-Bench 上评估 Agentic LLM：一个确定性的长期基准，
Agent 在 1-3 年的模拟运行中担任 AI 初创公司的 CEO。
Agent 管理 4 个领域的现金流、员工、任务和声望，
完全通过 CLI 子进程调用与 SQLite 支持的离散事件仿真交互。

与 TerminalBench2（每个任务二进制通过/失败）不同，YC-Bench 衡量持续的
多轮策略连贯性 -- Agent 是否能在数百轮中管理复合决策而不破产。

这是纯评估环境。运行方式:

    python environments/benchmarks/yc_bench/yc_bench_env.py evaluate \
        --config environments/benchmarks/yc_bench/default.yaml

评估流程:
    1. setup()     -- 验证 yc-bench 已安装，构建评估矩阵（preset x seed）
    2. evaluate()  -- 顺序遍历所有运行:
        a. rollout_and_score_eval()  -- 每次运行的 Agent 循环
            - 通过 `sim init`（而非 `run`）初始化新的 yc-bench 仿真
            - 使用 terminal 工具运行 KClawAgentLoop
            - 读取最终 SQLite DB 提取分数
            - 返回生存 (0/1) + 归一化资金分数
        b. 聚合每个 preset 和整体指标
        c. 通过 evaluate_log() 和 wandb 记录结果

关键特性:
  - 纯 CLI 接口: Agent 通过 terminal 工具调用 yc-bench 子命令
  - 确定性: 相同 seed + preset = 相同世界（基于 SHA256 的 RNG）
  - 多维度评分: 生存 + 归一化最终资金
  - 按 preset 难度细分结果
  - 每次运行独立的 SQLite DB（无跨运行状态泄漏）

需要: pip install kclaw[yc-bench]
"""

import asyncio
import datetime
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.agent_loop import KClawAgentLoop
from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig

logger = logging.getLogger(__name__)

# =============================================================================
# 系统提示词
# =============================================================================

YC_BENCH_SYSTEM_PROMPT = """\
You are the autonomous CEO of an early-stage AI startup in a deterministic
business simulation. You manage the company exclusively through the `yc-bench`
CLI tool. Your primary goal is to **survive** until the simulation horizon ends
without going bankrupt, while **maximising final funds**.

## Simulation Mechanics

- **Funds**: You start with $250,000 seed capital. Revenue comes from completing
  tasks. Rewards scale with your prestige: `base × (1 + scale × (prestige − 1))`.
- **Domains**: There are 4 skill domains: **research**, **inference**,
  **data_environment**, and **training**. Each has its own prestige level
  (1.0-10.0). Higher prestige unlocks better-paying tasks.
- **Employees**: You have employees (Junior/Mid/Senior) with domain-specific
  skill rates. **Throughput splits**: `effective_rate = base_rate / N` where N
  is the number of active tasks assigned to that employee. Focus beats breadth.
- **Payroll**: Deducted automatically on the first business day of each month.
  Running out of funds = bankruptcy = game over.
- **Time**: The simulation runs on business days (Mon-Fri), 09:00-18:00.
  Time only advances when you call `yc-bench sim resume`.

## Task Lifecycle

1. Browse market tasks with `market browse`
2. Accept a task with `task accept` (this sets its deadline)
3. Assign employees with `task assign`
4. Dispatch with `task dispatch` to start work
5. Call `sim resume` to advance time and let employees make progress
6. Tasks complete when all domain requirements are fulfilled

**Penalties for failure vary by difficulty preset.** Completing a task on time
earns full reward + prestige gain. Missing a deadline or cancelling a task
incurs prestige penalties -- cancelling is always more costly than letting a
task fail, so cancel only as a last resort.

## CLI Commands

### Observe
- `yc-bench company status`                                         -- funds, prestige, runway
- `yc-bench employee list`                                          -- skills, salary, active tasks
- `yc-bench market browse [--domain D] [--required-prestige-lte N]` -- available tasks
- `yc-bench task list [--status active|planned]`                    -- your tasks
- `yc-bench task inspect --task-id UUID`                            -- progress, deadline, assignments
- `yc-bench finance ledger [--category monthly_payroll|task_reward]` -- transaction history
- `yc-bench report monthly`                                         -- monthly P&L

### Act
- `yc-bench task accept --task-id UUID`                              -- accept from market
- `yc-bench task assign --task-id UUID --employee-id UUID`           -- assign employee
- `yc-bench task dispatch --task-id UUID`                            -- start work (needs >=1 assignment)
- `yc-bench task cancel --task-id UUID --reason "text"`              -- cancel (prestige penalty)
- `yc-bench sim resume`                                              -- advance simulation clock

### Memory (persists across context truncation)
- `yc-bench scratchpad read`            -- read your persistent notes
- `yc-bench scratchpad write --content "text"`  -- overwrite notes
- `yc-bench scratchpad append --content "text"` -- append to notes
- `yc-bench scratchpad clear`           -- clear notes

## Strategy Guidelines

1. **Specialise in 2-3 domains** to climb the prestige ladder faster and unlock
   high-reward tasks. Don't spread thin across all 4 domains early on.
2. **Focus employees** -- assigning one employee to many tasks halves their
   throughput per additional task. Keep assignments concentrated.
3. **Use the scratchpad** to track your strategy, upcoming deadlines, and
   employee assignments. This persists even if conversation context is truncated.
4. **Monitor runway** -- always know how many months of payroll you can cover.
   Accept high-reward tasks before payroll dates.
5. **Don't over-accept** -- taking too many tasks and missing deadlines cascades
   into prestige loss, locking you out of profitable contracts.
6. Use `finance ledger` and `report monthly` to track revenue trends.

## Your Turn

Each turn:
1. Call `yc-bench company status` and `yc-bench task list` to orient yourself.
2. Check for completed tasks and pending deadlines.
3. Browse market for profitable tasks within your prestige level.
4. Accept, assign, and dispatch tasks strategically.
5. Call `yc-bench sim resume` to advance time.
6. Repeat until the simulation ends.

Think step by step before acting."""

# 起始资金（美分）（$250,000）
INITIAL_FUNDS_CENTS = 25_000_000

# 每个 preset 的默认期限（年）
_PRESET_HORIZONS = {
    "tutorial": 1,
    "easy": 1,
    "medium": 1,
    "hard": 1,
    "nightmare": 1,
    "fast_test": 1,
    "default": 3,
    "high_reward": 1,
}


# =============================================================================
# 配置
# =============================================================================

class YCBenchEvalConfig(KClawAgentEnvConfig):
    """
    YC-Bench 评估环境配置。

    扩展 KClawAgentEnvConfig，添加 YC-Bench 特定设置，包括
    preset 选择、seed 控制、评分和仿真参数。
    """

    presets: List[str] = Field(
        default=["fast_test", "medium", "hard"],
        description="YC-Bench preset 名称列表。",
    )
    seeds: List[int] = Field(
        default=[1, 2, 3],
        description="随机种子 -- 每个 preset x seed = 一次运行。",
    )
    run_timeout: int = Field(
        default=3600,
        description="每次运行的最大挂钟时间（秒）。默认 60 分钟。",
    )
    survival_weight: float = Field(
        default=0.5,
        description="生存 (0/1) 在综合分数中的权重。",
    )
    funds_weight: float = Field(
        default=0.5,
        description="归一化最终资金在综合分数中的权重。",
    )
    db_dir: str = Field(
        default="/tmp/yc_bench_dbs",
        description="每次运行的 SQLite 数据库目录。",
    )
    horizon_years: Optional[int] = Field(
        default=None,
        description=(
            "仿真期限（年）。如果为 None（默认），从 "
            "preset 名称推断（大多数为 1 年，'default' 为 3 年）。"
        ),
    )
    company_name: str = Field(
        default="BenchCo",
        description="模拟公司名称。",
    )
    start_date: str = Field(
        default="01/01/2025",
        description="仿真开始日期，MM/DD/YYYY 格式（yc-bench 约定）。",
    )


# =============================================================================
# 评分辅助函数
# =============================================================================

def _read_final_score(db_path: str) -> Dict[str, Any]:
    """
    从 YC-Bench SQLite 数据库读取最终游戏状态。

    返回包含 final_funds_cents (int)、survived (bool)、
    terminal_reason (str) 的字典。

    注意: yc-bench 表名是复数 -- 'companies' 而非 'company'，
    'sim_events' 而非 'simulation_log'。
    """
    if not os.path.exists(db_path):
        logger.warning("数据库未找到: %s", db_path)
        return {
            "final_funds_cents": 0,
            "survived": False,
            "terminal_reason": "db_missing",
        }

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 从 'companies' 表读取最终资金
        cur.execute("SELECT funds_cents FROM companies LIMIT 1")
        row = cur.fetchone()
        funds = row[0] if row else 0

        # 从 'sim_events' 表确定终止原因
        terminal_reason = "unknown"
        try:
            cur.execute(
                "SELECT event_type FROM sim_events "
                "WHERE event_type IN ('bankruptcy', 'horizon_end') "
                "ORDER BY scheduled_at DESC LIMIT 1"
            )
            event_row = cur.fetchone()
            if event_row:
                terminal_reason = event_row[0]
        except sqlite3.OperationalError:
            # 如果仿真未进展，表可能不存在
            pass

        survived = funds >= 0 and terminal_reason != "bankruptcy"
        return {
            "final_funds_cents": funds,
            "survived": survived,
            "terminal_reason": terminal_reason,
        }

    except Exception as e:
        logger.error("读取数据库失败 %s: %s", db_path, e)
        return {
            "final_funds_cents": 0,
            "survived": False,
            "terminal_reason": f"db_error: {e}",
        }
    finally:
        if conn:
            conn.close()


def _compute_composite_score(
    final_funds_cents: int,
    survived: bool,
    survival_weight: float = 0.5,
    funds_weight: float = 0.5,
    initial_funds_cents: int = INITIAL_FUNDS_CENTS,
) -> float:
    """
    从生存和最终资金计算综合分数。

    Score = survival_weight * survival_score
          + funds_weight * normalised_funds_score

    归一化资金使用相对于初始资本的对数刻度:
    - funds <= 0:          0.0
    - funds == initial:   ~0.15
    - funds == 10x:       ~0.52
    - funds == 100x:       1.0
    """
    survival_score = 1.0 if survived else 0.0

    if final_funds_cents <= 0:
        funds_score = 0.0
    else:
        max_ratio = 100.0
        ratio = final_funds_cents / max(initial_funds_cents, 1)
        funds_score = min(math.log1p(ratio) / math.log1p(max_ratio), 1.0)

    return survival_weight * survival_score + funds_weight * funds_score


# =============================================================================
# 主环境
# =============================================================================

class YCBenchEvalEnv(KClawAgentBaseEnv):
    """
    YC-Bench 长期 Agent 基准评估环境（纯评估）。

    每个评估项是 (preset, seed) 对。环境通过 ``yc-bench sim init``
    （而非 ``yc-bench run``，后者会启动竞争的内置 Agent 循环）初始化仿真。
    然后 KClawAgentLoop 通过 terminal 工具驱动交互，调用各个 yc-bench CLI 命令。

    Agent 循环结束后，读取 SQLite DB 提取最终分数。

    评分:
      composite = 0.5 * survival + 0.5 * normalised_funds
    """

    name = "yc-bench"
    env_config_cls = YCBenchEvalConfig

    @classmethod
    def config_init(cls) -> Tuple[YCBenchEvalConfig, List[APIServerConfig]]:
        env_config = YCBenchEvalConfig(
            enabled_toolsets=["terminal"],
            disabled_toolsets=None,
            distribution=None,
            max_agent_turns=200,
            max_token_length=32000,
            agent_temperature=0.0,
            system_prompt=YC_BENCH_SYSTEM_PROMPT,
            terminal_backend="local",
            terminal_timeout=60,
            presets=["fast_test", "medium", "hard"],
            seeds=[1, 2, 3],
            run_timeout=3600,
            survival_weight=0.5,
            funds_weight=0.5,
            db_dir="/tmp/yc_bench_dbs",
            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,
            tokenizer_name="NousResearch/KClaw-3-Llama-3.1-8B",
            use_wandb=True,
            wandb_name="yc-bench",
            ensure_scores_are_not_same=False,
        )

        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4.6",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs

    # =========================================================================
    # Setup
    # =========================================================================

    async def setup(self):
        """验证 yc-bench 已安装并构建评估矩阵。"""
        # 验证 yc-bench CLI 可用
        try:
            result = subprocess.run(
                ["yc-bench", "--help"], capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise RuntimeError(
                "未找到 yc-bench CLI。安装方式:\n"
                '  pip install "kclaw[yc-bench]"\n'
                "或: git clone https://github.com/collinear-ai/yc-bench "
                "&& cd yc-bench && pip install -e ."
            )
        print("yc-bench CLI 已验证。")

        # 构建评估矩阵: preset x seed
        self.all_eval_items = [
            {"preset": preset, "seed": seed}
            for preset in self.config.presets
            for seed in self.config.seeds
        ]
        self.iter = 0

        os.makedirs(self.config.db_dir, exist_ok=True)
        self.eval_metrics: List[Tuple[str, float]] = []

        # 流式 JSONL 日志用于崩溃安全的结果持久化
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._streaming_path = os.path.join(log_dir, f"samples_{run_ts}.jsonl")
        self._streaming_file = open(self._streaming_path, "w")
        self._streaming_lock = threading.Lock()

        print(f"\nYC-Bench 评估矩阵: {len(self.all_eval_items)} 次运行")
        for item in self.all_eval_items:
            print(f"  preset={item['preset']!r}  seed={item['seed']}")
        print(f"流式结果写入: {self._streaming_path}\n")

    def _save_result(self, result: Dict[str, Any]):
        """立即将单次运行结果写入流式 JSONL 文件。"""
        if not hasattr(self, "_streaming_file") or self._streaming_file.closed:
            return
        with self._streaming_lock:
            self._streaming_file.write(
                json.dumps(result, ensure_ascii=False, default=str) + "\n"
            )
            self._streaming_file.flush()

    # =========================================================================
    # 训练管线桩（纯评估 -- 不使用）
    # =========================================================================

    async def get_next_item(self):
        item = self.all_eval_items[self.iter % len(self.all_eval_items)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        preset = item["preset"]
        seed = item["seed"]
        return (
            f"A new YC-Bench simulation has been initialized "
            f"(preset='{preset}', seed={seed}).\n"
            f"Your company '{self.config.company_name}' is ready.\n\n"
            "Begin by calling:\n"
            "1. `yc-bench company status` -- see your starting funds and prestige\n"
            "2. `yc-bench employee list` -- see your team and their skills\n"
            "3. `yc-bench market browse --required-prestige-lte 1` -- find tasks "
            "you can take\n\n"
            "Then accept 2-3 tasks, assign employees, dispatch them, and call "
            "`yc-bench sim resume` to advance time. Repeat this loop until the "
            "simulation ends (horizon reached or bankruptcy)."
        )

    async def compute_reward(self, item, result, ctx) -> float:
        return 0.0

    async def collect_trajectories(self, item):
        return None, []

    async def score(self, rollout_group_data):
        return None

    # =========================================================================
    # 每次运行的评估
    # =========================================================================

    async def rollout_and_score_eval(self, eval_item: Dict[str, Any]) -> Dict:
        """
        评估单个 (preset, seed) 运行。

        1. 设置 DATABASE_URL 和 YC_BENCH_EXPERIMENT 环境变量
        2. 通过 ``yc-bench sim init``（而非 ``run``）初始化仿真
        3. 使用 terminal 工具运行 KClawAgentLoop
        4. 读取 SQLite DB 计算最终分数
        5. 返回包含生存、资金和综合分数的结果字典
        """
        preset = eval_item["preset"]
        seed = eval_item["seed"]
        run_id = str(uuid.uuid4())[:8]
        run_key = f"{preset}_seed{seed}_{run_id}"

        from tqdm import tqdm
        tqdm.write(f"  [开始] preset={preset!r} seed={seed} (run_id={run_id})")
        run_start = time.time()

        # 每次运行独立的 DB -- 防止跨运行状态泄漏
        db_path = os.path.join(self.config.db_dir, f"yc_bench_{run_key}.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["YC_BENCH_EXPERIMENT"] = preset

        # 确定期限: 显式配置覆盖 > preset 查找 > 默认 1
        horizon = self.config.horizon_years or _PRESET_HORIZONS.get(preset, 1)

        try:
            # ----------------------------------------------------------
            # 步骤 1: 通过 CLI 初始化仿真
            # 重要: 我们使用 `sim init`，而非 `yc-bench run`。
            # `yc-bench run` 会启动 yc-bench 自己的 LLM Agent 循环（通过
            # LiteLLM），这会与我们的 KClawAgentLoop 竞争。
            # `sim init` 只是设置世界然后返回。
            # ----------------------------------------------------------
            init_cmd = [
                "yc-bench", "sim", "init",
                "--seed", str(seed),
                "--start-date", self.config.start_date,
                "--company-name", self.config.company_name,
                "--horizon-years", str(horizon),
            ]
            init_result = subprocess.run(
                init_cmd, capture_output=True, text=True, timeout=30,
            )
            if init_result.returncode != 0:
                error_msg = (init_result.stderr or init_result.stdout).strip()
                raise RuntimeError(f"yc-bench sim init 失败: {error_msg}")

            tqdm.write(f"    仿真已初始化 (horizon={horizon}yr)")

            # ----------------------------------------------------------
            # 步骤 2: 运行 KClawAgentLoop
            # ----------------------------------------------------------
            tools, valid_names = self._resolve_tools_for_group()

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": YC_BENCH_SYSTEM_PROMPT},
                {"role": "user", "content": self.format_prompt(eval_item)},
            ]

            agent = KClawAgentLoop(
                server=self.server,
                tool_schemas=tools,
                valid_tool_names=valid_names,
                max_turns=self.config.max_agent_turns,
                task_id=run_id,
                temperature=self.config.agent_temperature,
                max_tokens=self.config.max_token_length,
                extra_body=self.config.extra_body,
                budget_config=self.config.build_budget_config(),
            )
            result = await agent.run(messages)

            # ----------------------------------------------------------
            # 步骤 3: 从仿真 DB 读取最终分数
            # ----------------------------------------------------------
            score_data = _read_final_score(db_path)
            final_funds = score_data["final_funds_cents"]
            survived = score_data["survived"]
            terminal_reason = score_data["terminal_reason"]

            composite = _compute_composite_score(
                final_funds_cents=final_funds,
                survived=survived,
                survival_weight=self.config.survival_weight,
                funds_weight=self.config.funds_weight,
            )

            elapsed = time.time() - run_start
            status = "存活" if survived else "破产"
            if final_funds >= 0:
                funds_str = f"${final_funds / 100:,.0f}"
            else:
                funds_str = f"-${abs(final_funds) / 100:,.0f}"

            tqdm.write(
                f"  [{status}] preset={preset!r} seed={seed} "
                f"funds={funds_str} score={composite:.3f} "
                f"turns={result.turns_used} ({elapsed:.0f}s)"
            )

            out = {
                "preset": preset,
                "seed": seed,
                "survived": survived,
                "final_funds_cents": final_funds,
                "final_funds_usd": final_funds / 100,
                "terminal_reason": terminal_reason,
                "composite_score": composite,
                "turns_used": result.turns_used,
                "finished_naturally": result.finished_naturally,
                "elapsed_seconds": elapsed,
                "db_path": db_path,
                "messages": result.messages,
            }
            self._save_result(out)
            return out

        except Exception as e:
            elapsed = time.time() - run_start
            logger.error("运行 %s 失败: %s", run_key, e, exc_info=True)
            tqdm.write(
                f"  [错误] preset={preset!r} seed={seed}: {e} ({elapsed:.0f}s)"
            )
            out = {
                "preset": preset,
                "seed": seed,
                "survived": False,
                "final_funds_cents": 0,
                "final_funds_usd": 0.0,
                "terminal_reason": f"error: {e}",
                "composite_score": 0.0,
                "turns_used": 0,
                "error": str(e),
                "elapsed_seconds": elapsed,
            }
            self._save_result(out)
            return out

    # =========================================================================
    # Evaluate
    # =========================================================================

    async def _run_with_timeout(self, item: Dict[str, Any]) -> Dict:
        """为单次 rollout 包装挂钟超时。"""
        preset = item["preset"]
        seed = item["seed"]
        try:
            return await asyncio.wait_for(
                self.rollout_and_score_eval(item),
                timeout=self.config.run_timeout,
            )
        except asyncio.TimeoutError:
            from tqdm import tqdm
            tqdm.write(
                f"  [超时] preset={preset!r} seed={seed} "
                f"(超过 {self.config.run_timeout}s)"
            )
            out = {
                "preset": preset,
                "seed": seed,
                "survived": False,
                "final_funds_cents": 0,
                "final_funds_usd": 0.0,
                "terminal_reason": f"timeout ({self.config.run_timeout}s)",
                "composite_score": 0.0,
                "turns_used": 0,
                "error": "timeout",
            }
            self._save_result(out)
            return out

    async def evaluate(self, *args, **kwargs) -> None:
        """
        运行 YC-Bench 评估，覆盖所有 (preset, seed) 组合。

        顺序运行 -- 每次运行 100-500 轮，并行化会非常昂贵
        且导致环境变量冲突。
        """
        start_time = time.time()
        from tqdm import tqdm

        # --- tqdm 兼容日志处理器（TB2 模式） ---
        class _TqdmHandler(logging.Handler):
            def emit(self, record):
                try:
                    tqdm.write(self.format(record))
                except Exception:
                    self.handleError(record)

        root = logging.getLogger()
        handler = _TqdmHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        root.handlers = [handler]
        for noisy in ("httpx", "openai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # --- 打印配置摘要 ---
        print(f"\n{'='*60}")
        print("正在启动 YC-Bench 评估")
        print(f"{'='*60}")
        print(f"  Presets: {self.config.presets}")
        print(f"  Seeds: {self.config.seeds}")
        print(f"  总运行数: {len(self.all_eval_items)}")
        print(f"  最大轮次/运行: {self.config.max_agent_turns}")
        print(f"  运行超时: {self.config.run_timeout}s")
        print(f"{'='*60}\n")

        results = []
        pbar = tqdm(
            total=len(self.all_eval_items), desc="YC-Bench", dynamic_ncols=True
        )

        try:
            for item in self.all_eval_items:
                result = await self._run_with_timeout(item)
                results.append(result)
                survived_count = sum(1 for r in results if r.get("survived"))
                pbar.set_postfix_str(
                    f"survived={survived_count}/{len(results)}"
                )
                pbar.update(1)

        except (KeyboardInterrupt, asyncio.CancelledError):
            tqdm.write("\n[已中断] 停止评估...")
            pbar.close()
            try:
                from tools.terminal_tool import cleanup_all_environments
                cleanup_all_environments()
            except Exception:
                pass
            if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
                self._streaming_file.close()
            return

        pbar.close()
        end_time = time.time()

        # --- 计算指标 ---
        valid = [r for r in results if r is not None]
        if not valid:
            print("警告: 没有有效结果。")
            return

        total = len(valid)
        survived_total = sum(1 for r in valid if r.get("survived"))
        survival_rate = survived_total / total if total else 0.0
        avg_score = (
            sum(r.get("composite_score", 0) for r in valid) / total
            if total
            else 0.0
        )

        preset_results: Dict[str, List[Dict]] = defaultdict(list)
        for r in valid:
            preset_results[r["preset"]].append(r)

        eval_metrics = {
            "eval/survival_rate": survival_rate,
            "eval/avg_composite_score": avg_score,
            "eval/total_runs": total,
            "eval/survived_runs": survived_total,
            "eval/evaluation_time_seconds": end_time - start_time,
        }

        for preset, items in sorted(preset_results.items()):
            ps = sum(1 for r in items if r.get("survived"))
            pt = len(items)
            pa = (
                sum(r.get("composite_score", 0) for r in items) / pt
                if pt
                else 0
            )
            key = preset.replace("-", "_")
            eval_metrics[f"eval/survival_rate_{key}"] = ps / pt if pt else 0
            eval_metrics[f"eval/avg_score_{key}"] = pa

        self.eval_metrics = [(k, v) for k, v in eval_metrics.items()]

        # --- 打印摘要 ---
        print(f"\n{'='*60}")
        print("YC-Bench 评估结果")
        print(f"{'='*60}")
        print(
            f"整体存活率: {survival_rate:.1%} "
            f"({survived_total}/{total})"
        )
        print(f"平均综合分数: {avg_score:.4f}")
        print(f"评估时间: {end_time - start_time:.1f}s")

        print("\n按 preset 细分:")
        for preset, items in sorted(preset_results.items()):
            ps = sum(1 for r in items if r.get("survived"))
            pt = len(items)
            pa = (
                sum(r.get("composite_score", 0) for r in items) / pt
                if pt
                else 0
            )
            print(f"  {preset}: {ps}/{pt} 存活  avg_score={pa:.4f}")
            for r in items:
                status = "存活" if r.get("survived") else "破产"
                funds = r.get("final_funds_usd", 0)
                print(
                    f"    seed={r['seed']}  [{status}]  "
                    f"${funds:,.0f}  "
                    f"score={r.get('composite_score', 0):.3f}"
                )

        print(f"{'='*60}\n")

        # --- 记录结果 ---
        samples = [
            {k: v for k, v in r.items() if k != "messages"} for r in valid
        ]

        try:
            await self.evaluate_log(
                metrics=eval_metrics,
                samples=samples,
                start_time=start_time,
                end_time=end_time,
                generation_parameters={
                    "temperature": self.config.agent_temperature,
                    "max_tokens": self.config.max_token_length,
                    "max_agent_turns": self.config.max_agent_turns,
                },
            )
        except Exception as e:
            print(f"记录结果时出错: {e}")

        # --- 清理（TB2 模式） ---
        if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
            self._streaming_file.close()
            print(f"结果已保存到: {self._streaming_path}")

        try:
            from tools.terminal_tool import cleanup_all_environments
            cleanup_all_environments()
        except Exception:
            pass

        try:
            from environments.agent_loop import _tool_executor
            _tool_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # =========================================================================
    # Wandb 日志
    # =========================================================================

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """记录 YC-Bench 特定指标到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}
        for k, v in self.eval_metrics:
            wandb_metrics[k] = v
        self.eval_metrics = []
        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    YCBenchEvalEnv.cli()
