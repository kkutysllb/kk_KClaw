"""
TerminalBench2Env -- Terminal-Bench 2.0 评估环境

在 Terminal-Bench 2.0 的挑战性终端任务上评估 Agentic LLM。
每个任务提供唯一的 Docker 环境（预构建在 Docker Hub 上）、自然语言指令和验证测试套件。
Agent 使用 terminal + file 工具完成任务，然后在同一沙箱内运行测试套件。

这是纯评估环境（非训练环境）。设计为通过 `evaluate` 子命令运行：

    python environments/terminalbench2_env.py evaluate \\
        --env.dataset_name NousResearch/terminal-bench-2

评估流程:
    1. setup()     -- 从 HuggingFace 加载 TB2 数据集
    2. evaluate()  -- 遍历所有任务，每个任务经过:
        a. rollout_and_score_eval()  -- 每个任务的 Agent 循环 + 测试验证
            - 解析 Docker 镜像（预构建 Hub 镜像或 Dockerfile 回退）
            - 通过 register_task_env_overrides() 注册每个任务的 Modal 沙箱
            - 运行 KClawAgentLoop（terminal + file 工具）
            - 上传测试套件并在同一沙箱中运行 test.sh
            - 返回二进制通过/失败结果
        b. 聚合每个任务、每个类别和整体通过率
        c. 通过 evaluate_log() 和 wandb 记录结果

关键特性:
  - 每个任务的 Modal 沙箱，使用预构建的 Docker Hub 镜像
  - 二进制奖励: 1.0 表示所有测试通过，0.0 表示失败
  - 通过 asyncio.Semaphore 控制并发的并行评估
  - 每个任务、每个类别和整体通过率跟踪
"""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保仓库根目录在 sys.path 中以便导入
_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.agent_loop import AgentResult, KClawAgentLoop
from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig
from environments.tool_context import ToolContext
from tools.terminal_tool import (
    register_task_env_overrides,
    clear_task_env_overrides,
    cleanup_vm,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 配置
# =============================================================================

class TerminalBench2EvalConfig(KClawAgentEnvConfig):
    """
    Terminal-Bench 2.0 评估环境配置。

    扩展 KClawAgentEnvConfig，添加 TB2 特定设置，包括数据集加载、
    测试执行、任务过滤和评估并发。
    """

    # --- 数据集 ---
    dataset_name: str = Field(
        default="NousResearch/terminal-bench-2",
        description="包含 TB2 任务的 HuggingFace 数据集。",
    )

    # --- 测试执行 ---
    test_timeout: int = Field(
        default=180,
        description="Agent 完成后运行测试套件的超时时间（秒）。",
    )

    # --- 镜像策略 ---
    force_build: bool = Field(
        default=False,
        description="如果为 True，始终从 Dockerfile 构建（忽略 docker_image）。"
        "适用于测试自定义 Dockerfile。",
    )

    # --- 任务过滤（从 CLI 逗号分隔） ---
    task_filter: Optional[str] = Field(
        default=None,
        description="逗号分隔的要运行的任务名（例如 'fix-git,git-multibranch'）。"
        "如果未设置，运行所有任务。",
    )
    skip_tasks: Optional[str] = Field(
        default=None,
        description="逗号分隔的要跳过的任务名，在默认跳过列表之上。",
    )

    # --- 每个任务的挂钟超时 ---
    task_timeout: int = Field(
        default=1800,
        description="每个任务的最大挂钟时间（秒）（Agent 循环 + 验证）。"
        "超过此时间的任务计为失败。默认 30 分钟。",
    )

    # --- 并发控制 ---
    max_concurrent_tasks: int = Field(
        default=8,
        description="最大并发任务数。"
        "限制并发 Modal 沙箱创建以避免异步/线程死锁。"
        "Modal 有内部限制，同时创建过多沙箱"
        "会导致线程池内的阻塞调用死锁。",
    )

    # --- 评估并发 ---
    eval_concurrency: int = Field(
        default=0,
        description="最大并行评估任务数。"
        "0 表示无限制（所有任务同时运行）。"
        "本地后端建议设为 8 以避免机器过载。",
    )


# 无法在 Modal 上正常运行的任务，从评分中排除。
MODAL_INCOMPATIBLE_TASKS = {
    "qemu-startup",        # 需要 KVM/硬件虚拟化
    "qemu-alpine-ssh",     # 需要 KVM/硬件虚拟化
    "crack-7z-hash",       # 密码暴力破解 -- 云沙箱超时太慢
}


# =============================================================================
# Tar 解压辅助函数
# =============================================================================

def _normalize_tar_member_parts(member_name: str) -> list:
    """返回 tar 成员的安全路径组件，或抛出 ValueError。"""
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)

    if (
        not normalized_name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ValueError(f"不安全的归档成员路径: {member_name}")

    parts = [part for part in posix_path.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"不安全的归档成员路径: {member_name}")
    return parts


def _safe_extract_tar(tar: tarfile.TarFile, target_dir: Path) -> None:
    """解压 tar 归档文件，禁止路径遍历和链接条目。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    for member in tar.getmembers():
        parts = _normalize_tar_member_parts(member.name)
        target = target_dir.joinpath(*parts)
        target_real = target.resolve(strict=False)

        try:
            target_real.relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"Unsafe archive member path: {member.name}") from exc

        if member.isdir():
            target_real.mkdir(parents=True, exist_ok=True)
            continue

        if not member.isfile():
            raise ValueError(f"不支持的归档成员类型: {member.name}")

        target_real.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"无法读取归档成员: {member.name}")

        with extracted, open(target_real, "wb") as dst:
            shutil.copyfileobj(extracted, dst)

        try:
            os.chmod(target_real, member.mode & 0o777)
        except OSError:
            pass


def _extract_base64_tar(b64_data: str, target_dir: Path):
    """将 base64 编码的 tar.gz 归档文件解压到 target_dir。"""
    if not b64_data:
        return
    raw = base64.b64decode(b64_data)
    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        _safe_extract_tar(tar, target_dir)


# =============================================================================
# 主环境
# =============================================================================

class TerminalBench2EvalEnv(KClawAgentBaseEnv):
    """
    Terminal-Bench 2.0 评估环境（纯评估，无训练）。

    继承 KClawAgentBaseEnv 的:
      - 终端后端设置 (os.environ["TERMINAL_ENV"])
      - 通过 _resolve_tools_for_group() 进行工具解析
      - 异步安全工具操作的猴子补丁
      - Wandb 轨迹格式化

    评估流程（由 `environment.py evaluate` 触发）:
      1. setup()    -- 从 HuggingFace 加载数据集
      2. evaluate() -- 通过 rollout_and_score_eval() 运行所有任务

    rollout_and_score_eval() 中的每个任务:
      1. 解析 Docker 镜像（预构建 Hub 镜像或 Dockerfile 回退）
      2. 注册每个任务的 Modal 沙箱覆盖
      3. 使用 terminal + file 工具运行 KClawAgentLoop
      4. 上传测试套件并在同一沙箱中执行 test.sh
      5. 检查 /logs/verifier/reward.txt 判断通过/失败
      6. 清理沙箱、覆盖和临时文件
    """

    name = "terminal-bench-2"
    env_config_cls = TerminalBench2EvalConfig

    @classmethod
    def config_init(cls) -> Tuple[TerminalBench2EvalConfig, List[APIServerConfig]]:
        """
        Terminal-Bench 2.0 评估的默认配置。

        使用纯评估设置:
          - eval_handling=STOP_TRAIN 以便评估流程干净运行
          - steps_per_eval=1, total_steps=1 以便评估立即触发
          - group_size=1（每组一个 rollout，每个任务开销大）

        使用 Modal 终端后端（每个任务一个云隔离沙箱）和
        OpenRouter + Claude 进行推理。
        """
        env_config = TerminalBench2EvalConfig(
            # 仅 terminal + file 工具（Agent 通过 shell 命令交互）
            enabled_toolsets=["terminal", "file"],
            disabled_toolsets=None,
            distribution=None,

            # Agent 设置 -- TB2 任务复杂，需要多轮
            max_agent_turns=60,
            max_token_length=16000,
            agent_temperature=0.6,
            system_prompt=None,

            # Modal 后端，每个任务一个云隔离沙箱
            terminal_backend="modal",
            terminal_timeout=300,   # 每个命令 5 分钟（构建、pip install 等）

            # 测试执行超时（TB2 测试脚本可能安装 pytest 等依赖）
            test_timeout=180,

            # 89 个任务并行运行，每个需要一个线程进行工具调用
            tool_pool_size=128,

            # --- 纯评估 Atropos 设置 ---
            # 这些设置使环境作为纯评估环境工作:
            #   - STOP_TRAIN: 评估期间暂停训练（评估环境标准）
            #   - steps_per_eval=1, total_steps=1: 评估立即触发
            #   - group_size=1: 每组一个 rollout（每个任务开销大）
            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,

            tokenizer_name="NousResearch/KClaw-3-Llama-3.1-8B",
            use_wandb=True,
            wandb_name="terminal-bench-2",
            ensure_scores_are_not_same=False,  # 二进制奖励可能全部为 0 或 1
        )

        # OpenRouter + Claude -- API 密钥从 .env 加载
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

    # =========================================================================
    # Setup -- 加载数据集
    # =========================================================================

    async def setup(self):
        """从 HuggingFace 加载 Terminal-Bench 2.0 数据集。"""
        from datasets import load_dataset

        # 自动设置 terminal_lifetime 为 task_timeout + 120 秒，
        # 确保沙箱在活跃任务期间不会被终止，
        # 但任务超时后仍能及时清理。
        lifetime = self.config.task_timeout + 120
        self.config.terminal_lifetime = lifetime
        os.environ["TERMINAL_LIFETIME_SECONDS"] = str(lifetime)
        print(f"  Terminal 生命周期自动设置为 {lifetime}s (task_timeout + 120s)")

        print(f"正在加载 TB2 数据集: {self.config.dataset_name}")
        ds = load_dataset(self.config.dataset_name, split="train")

        # 应用任务过滤（从 CLI 传入的逗号分隔字符串）
        tasks = list(ds)
        if self.config.task_filter:
            allowed = {name.strip() for name in self.config.task_filter.split(",")}
            tasks = [t for t in tasks if t["task_name"] in allowed]
            print(f"  已过滤为 {len(tasks)} 个任务: {sorted(allowed)}")

        # 跳过与当前后端不兼容的任务（如 Modal 上的 QEMU）
        # 以及用户指定的 skip_tasks
        skip = set(MODAL_INCOMPATIBLE_TASKS) if self.config.terminal_backend == "modal" else set()
        if self.config.skip_tasks:
            skip |= {name.strip() for name in self.config.skip_tasks.split(",")}
        if skip:
            before = len(tasks)
            tasks = [t for t in tasks if t["task_name"] not in skip]
            skipped = before - len(tasks)
            if skipped > 0:
                print(f"  已跳过 {skipped} 个不兼容任务: {sorted(skip & {t['task_name'] for t in ds})}")

        self.all_eval_items = tasks
        self.iter = 0

        # 构建类别索引以支持按类别统计指标
        self.category_index: Dict[str, List[int]] = defaultdict(list)
        for i, task in enumerate(self.all_eval_items):
            self.category_index[task.get("category", "unknown")].append(i)

        # 奖励跟踪用于 wandb 日志
        self.eval_metrics: List[Tuple[str, float]] = []

        # 流式 JSONL 写入器 -- 每个任务完成后立即保存完整对话，
        # 即使 Ctrl+C 也能保留数据。
        # 带时间戳的文件名使每次运行产生唯一文件。
        import datetime
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._streaming_path = os.path.join(log_dir, f"samples_{run_ts}.jsonl")
        self._streaming_file = open(self._streaming_path, "w")
        self._streaming_lock = __import__("threading").Lock()
        print(f"  流式结果写入: {self._streaming_path}")

        print(f"TB2 就绪: {len(self.all_eval_items)} 个任务，跨 {len(self.category_index)} 个类别")
        for cat, indices in sorted(self.category_index.items()):
            print(f"  {cat}: {len(indices)} 个任务")

    def _save_result(self, result: Dict[str, Any]):
        """立即将单个任务结果写入流式 JSONL 文件。"""
        if not hasattr(self, "_streaming_file") or self._streaming_file.closed:
            return
        with self._streaming_lock:
            self._streaming_file.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            self._streaming_file.flush()

    # =========================================================================
    # 训练管线桩 -- 纯评估模式下不使用
    # =========================================================================
    # 这些满足 KClawAgentBaseEnv 的抽象方法要求。
    # evaluate 子命令直接调用 setup() -> evaluate()，完全绕过训练管线。

    async def get_next_item(self):
        """返回下一个项目（桩 -- 纯评估模式下不使用）。"""
        item = self.all_eval_items[self.iter % len(self.all_eval_items)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        """返回任务指令作为用户提示。"""
        return item["instruction"]

    async def compute_reward(self, item, result, ctx) -> float:
        """计算奖励（桩 -- 实际验证在 rollout_and_score_eval 中）。"""
        return 0.0

    async def collect_trajectories(self, item):
        """收集轨迹（桩 -- 纯评估模式下不使用）。"""
        return None, []

    async def score(self, rollout_group_data):
        """评分 rollout（桩 -- 纯评估模式下不使用）。"""
        return None

    # =========================================================================
    # Docker 镜像解析
    # =========================================================================

    def _resolve_task_image(
        self, item: Dict[str, Any], task_name: str
    ) -> Tuple[str, Optional[Path]]:
        """
        解析任务的 Docker 镜像，回退到 Dockerfile。

        策略（模仿 Harbor 的方法）:
        1. 如果 force_build=True，始终从 environment_tar 中的 Dockerfile 构建
        2. 如果 docker_image 可用，使用预构建的 Docker Hub 镜像（快速）
        3. 否则，从 environment_tar 提取 Dockerfile 并构建（慢）

        Returns:
            (modal_image, temp_dir) -- modal_image 是 Docker Hub 名称或
            Dockerfile 路径。temp_dir 在提取了需要后续清理的文件时设置。
        """
        docker_image = item.get("docker_image", "")
        environment_tar = item.get("environment_tar", "")

        # 快速路径: 使用预构建的 Docker Hub 镜像
        if docker_image and not self.config.force_build:
            logger.info("任务 %s: 使用预构建镜像 %s", task_name, docker_image)
            return docker_image, None

        # 慢路径: 从 environment_tar 提取 Dockerfile 并构建
        if environment_tar:
            task_dir = Path(tempfile.mkdtemp(prefix=f"tb2-{task_name}-"))
            _extract_base64_tar(environment_tar, task_dir)
            dockerfile_path = task_dir / "Dockerfile"
            if dockerfile_path.exists():
                logger.info(
                    "任务 %s: 从 Dockerfile 构建 (force_build=%s, docker_image=%s)",
                    task_name, self.config.force_build, bool(docker_image),
                )
                return str(dockerfile_path), task_dir

        # 两者都不可用 -- 如果 force_build 为 True 则回退到 Hub 镜像
        if docker_image:
            logger.warning(
                "任务 %s: force_build=True 但没有 environment_tar，"
                "回退到 docker_image %s", task_name, docker_image,
            )
            return docker_image, None

        return "", None

    # =========================================================================
    # 每个任务的评估 -- Agent 循环 + 测试验证
    # =========================================================================

    async def rollout_and_score_eval(self, eval_item: Dict[str, Any]) -> Dict:
        """
        评估单个 TB2 任务: 运行 Agent 循环，然后用测试验证。

        这是核心评估方法。对于每个任务:
        1. 解析 Docker 镜像并注册 Modal 沙箱覆盖
        2. 使用 terminal + file 工具运行 KClawAgentLoop
        3. 上传测试套件到沙箱
        4. 执行 test.sh 并检查结果
        5. 清理沙箱和临时文件

        Args:
            eval_item: 数据集中的单个 TB2 任务字典

        Returns:
            包含 'passed' (bool)、'reward' (float)、'task_name' (str)、
            'category' (str) 和可选调试信息的字典
        """
        task_name = eval_item.get("task_name", "unknown")
        category = eval_item.get("category", "unknown")
        task_id = str(uuid.uuid4())
        task_dir = None  # 在提取 Dockerfile 时设置（需要清理）

        from tqdm import tqdm
        tqdm.write(f"  [开始] {task_name} (task_id={task_id[:8]})")
        task_start = time.time()

        try:
            # --- 1. 解析 Docker 镜像 ---
            modal_image, task_dir = self._resolve_task_image(eval_item, task_name)
            if not modal_image:
                logger.error("任务 %s: 没有 docker_image 或 environment_tar，跳过", task_name)
                return {
                    "passed": False, "reward": 0.0,
                    "task_name": task_name, "category": category,
                    "error": "no_image",
                }

            # --- 2. 注册每个任务的镜像覆盖 ---
            # 同时设置 modal_image 和 docker_image，确保无论配置了哪个后端
            # 都能使用任务镜像。
            register_task_env_overrides(task_id, {
                "modal_image": modal_image,
                "docker_image": modal_image,
                "cwd": "/app",
            })
            logger.info(
                "任务 %s: 已为 task_id %s 注册镜像覆盖",
                task_name, task_id[:8],
            )

            # --- 3. 解析工具并构建消息 ---
            tools, valid_names = self._resolve_tools_for_group()

            messages: List[Dict[str, Any]] = []
            if self.config.system_prompt:
                messages.append({"role": "system", "content": self.config.system_prompt})
            messages.append({"role": "user", "content": self.format_prompt(eval_item)})

            # --- 4. 运行 Agent 循环 ---
            # 对 vLLM/SGLang 后端使用 ManagedServer（阶段2）以获取
            # 通过 /generate 的 token 级跟踪。对 OpenAI 端点回退到
            # 直接 ServerManager（阶段1）。
            if self._use_managed_server():
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
            else:
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

            # --- 5. 验证 -- 在 Agent 沙箱中运行测试套件 ---
            # 如果 Agent 没有产生有意义的输出则跳过验证
            only_system_and_user = all(
                msg.get("role") in ("system", "user") for msg in result.messages
            )
            if result.turns_used == 0 or only_system_and_user:
                logger.warning(
                    "任务 %s: Agent 没有产生输出 (turns=%d)。奖励=0。",
                    task_name, result.turns_used,
                )
                reward = 0.0
            else:
                # 在线程中运行测试，使阻塞的 ctx.terminal() 调用
                # 不会冻结整个事件循环（这会导致所有其他任务、
                # tqdm 更新和超时计时器停滞）。
                ctx = ToolContext(task_id)
                try:
                    loop = asyncio.get_event_loop()
                    reward = await loop.run_in_executor(
                        None,  # 默认线程池
                        self._run_tests, eval_item, ctx, task_name,
                    )
                except Exception as e:
                    logger.error("任务 %s: 测试验证失败: %s", task_name, e)
                    reward = 0.0
                finally:
                    ctx.cleanup()

            passed = reward == 1.0
            status = "通过" if passed else "失败"
            elapsed = time.time() - task_start
            tqdm.write(f"  [{status}] {task_name} (turns={result.turns_used}, {elapsed:.0f}s)")
            logger.info(
                "任务 %s: reward=%.1f, turns=%d, finished=%s",
                task_name, reward, result.turns_used, result.finished_naturally,
            )

            out = {
                "passed": passed,
                "reward": reward,
                "task_name": task_name,
                "category": category,
                "turns_used": result.turns_used,
                "finished_naturally": result.finished_naturally,
                "messages": result.messages,
            }
            self._save_result(out)
            return out

        except Exception as e:
            elapsed = time.time() - task_start
            logger.error("任务 %s: rollout 失败: %s", task_name, e, exc_info=True)
            tqdm.write(f"  [错误] {task_name}: {e} ({elapsed:.0f}s)")
            out = {
                "passed": False, "reward": 0.0,
                "task_name": task_name, "category": category,
                "error": str(e),
            }
            self._save_result(out)
            return out

        finally:
            # --- 清理: 清除覆盖、沙箱和临时文件 ---
            clear_task_env_overrides(task_id)
            try:
                cleanup_vm(task_id)
            except Exception as e:
                logger.debug("VM 清理 %s: %s", task_id[:8], e)
            if task_dir and task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def _run_tests(
        self, item: Dict[str, Any], ctx: ToolContext, task_name: str
    ) -> float:
        """
        上传并执行 Agent 沙箱中的测试套件，然后
        下载验证器输出到本地读取奖励值。

        遵循 Harbor 的验证模式:
        1. 上传 tests/ 目录到沙箱
        2. 在沙箱内执行 test.sh
        3. 下载 /logs/verifier/ 目录到本地临时目录
        4. 使用原生 Python I/O 本地读取 reward.txt

        本地下载避免了在 Modal VM 上使用 file_read 工具的问题，
        并与 Harbor 的验证方式一致。

        TB2 测试脚本 (test.sh) 通常:
        1. 通过 uv/pip 安装 pytest
        2. 对 /tests/ 中的测试文件运行 pytest
        3. 将结果写入 /logs/verifier/reward.txt

        Args:
            item: TB2 任务字典（包含 tests_tar, test_sh）
            ctx: 作用域为该任务沙箱的 ToolContext
            task_name: 用于日志记录

        Returns:
            1.0 表示测试通过，0.0 表示失败
        """
        tests_tar = item.get("tests_tar", "")
        test_sh = item.get("test_sh", "")

        if not test_sh:
            logger.warning("任务 %s: 没有 test_sh 内容，reward=0", task_name)
            return 0.0

        # 在沙箱中创建所需目录
        ctx.terminal("mkdir -p /tests /logs/verifier")

        # 上传测试文件到沙箱（通过 base64 实现二进制安全）
        if tests_tar:
            tests_temp = Path(tempfile.mkdtemp(prefix=f"tb2-tests-{task_name}-"))
            try:
                _extract_base64_tar(tests_tar, tests_temp)
                ctx.upload_dir(str(tests_temp), "/tests")
            except Exception as e:
                logger.warning("任务 %s: 上传测试文件失败: %s", task_name, e)
            finally:
                shutil.rmtree(tests_temp, ignore_errors=True)

        # 写入测试运行脚本 (test.sh)
        ctx.write_file("/tests/test.sh", test_sh)
        ctx.terminal("chmod +x /tests/test.sh")

        # 执行测试套件
        logger.info(
            "任务 %s: 正在运行测试套件 (timeout=%ds)",
            task_name, self.config.test_timeout,
        )
        test_result = ctx.terminal(
            "bash /tests/test.sh",
            timeout=self.config.test_timeout,
        )

        exit_code = test_result.get("exit_code", -1)
        output = test_result.get("output", "")

        # 下载验证器输出目录到本地，然后用原生 Python I/O 读取 reward.txt。
        # 这避免了在 Modal VM 上使用 file_read 的问题，并与 Harbor 的验证方式一致。
        reward = 0.0
        local_verifier_dir = Path(tempfile.mkdtemp(prefix=f"tb2-verifier-{task_name}-"))
        try:
            ctx.download_dir("/logs/verifier", str(local_verifier_dir))

            reward_file = local_verifier_dir / "reward.txt"
            if reward_file.exists() and reward_file.stat().st_size > 0:
                content = reward_file.read_text().strip()
                if content == "1":
                    reward = 1.0
                elif content == "0":
                    reward = 0.0
                else:
                    # 意外内容 -- 尝试解析为浮点数
                    try:
                        reward = float(content)
                    except (ValueError, TypeError):
                        logger.warning(
                            "任务 %s: reward.txt 内容异常 (%r)，"
                            "回退到 exit_code=%d",
                            task_name, content, exit_code,
                        )
                        reward = 1.0 if exit_code == 0 else 0.0
            else:
                # reward.txt 未写入 -- 回退到退出码
                logger.warning(
                    "任务 %s: 下载后未找到 reward.txt，"
                    "回退到 exit_code=%d",
                    task_name, exit_code,
                )
                reward = 1.0 if exit_code == 0 else 0.0
        except Exception as e:
            logger.warning(
                "任务 %s: 下载验证器目录失败: %s，"
                "回退到 exit_code=%d",
                task_name, e, exit_code,
            )
            reward = 1.0 if exit_code == 0 else 0.0
        finally:
            shutil.rmtree(local_verifier_dir, ignore_errors=True)

        # 记录测试输出用于调试失败
        if reward == 0.0:
            output_preview = output[-500:] if output else "(no output)"
            logger.info(
                "任务 %s: 失败 (exit_code=%d)\n%s",
                task_name, exit_code, output_preview,
            )

        return reward

    # =========================================================================
    # Evaluate -- eval 子命令的主入口
    # =========================================================================

    async def _eval_with_timeout(self, item: Dict[str, Any]) -> Dict:
        """
        为 rollout_and_score_eval 包装每个任务的挂钟超时。

        如果任务超过 task_timeout 秒，自动计为失败。
        这防止单个任务无限挂起。
        """
        task_name = item.get("task_name", "unknown")
        category = item.get("category", "unknown")
        try:
            return await asyncio.wait_for(
                self.rollout_and_score_eval(item),
                timeout=self.config.task_timeout,
            )
        except asyncio.TimeoutError:
            from tqdm import tqdm
            elapsed = self.config.task_timeout
            tqdm.write(f"  [超时] {task_name} (超过 {elapsed}s 挂钟限制)")
            logger.error("任务 %s: 挂钟超时 %ds", task_name, elapsed)
            out = {
                "passed": False, "reward": 0.0,
                "task_name": task_name, "category": category,
                "error": f"timeout ({elapsed}s)",
            }
            self._save_result(out)
            return out

    async def evaluate(self, *args, **kwargs) -> None:
        """
        运行 Terminal-Bench 2.0 评估，覆盖所有任务。

        通过以下方式调用时的主入口:
            python environments/terminalbench2_env.py evaluate

        通过 asyncio.gather() 运行所有任务的 rollout_and_score_eval()
        （与 GPQA 和其他 Atropos 评估环境相同的模式）。
        每个任务都有挂钟超时保护，防止挂起。

        抑制嘈杂的 Modal/terminal 输出 (KCLAW_QUIET) 以保持 tqdm 进度条可见。
        """
        start_time = time.time()

        # 将所有日志路由通过 tqdm.write() 以保持进度条
        # 固定在底部，日志行在其上方滚动。
        from tqdm import tqdm

        class _TqdmHandler(logging.Handler):
            def emit(self, record):
                try:
                    tqdm.write(self.format(record))
                except Exception:
                    self.handleError(record)

        handler = _TqdmHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root = logging.getLogger()
        root.handlers = [handler]  # Replace any existing handlers
        root.setLevel(logging.INFO)

        # 抑制嘈杂的第三方日志器
        logging.getLogger("httpx").setLevel(logging.WARNING)      # 每个 HTTP 请求
        logging.getLogger("openai").setLevel(logging.WARNING)     # OpenAI 客户端重试
        logging.getLogger("rex-deploy").setLevel(logging.WARNING) # Swerex 部署
        logging.getLogger("rex_image_builder").setLevel(logging.WARNING)  # 镜像构建

        print(f"\n{'='*60}")
        print("正在启动 Terminal-Bench 2.0 评估")
        print(f"{'='*60}")
        print(f"  数据集: {self.config.dataset_name}")
        print(f"  任务总数: {len(self.all_eval_items)}")
        print(f"  最大 Agent 轮次: {self.config.max_agent_turns}")
        print(f"  任务超时: {self.config.task_timeout}s")
        print(f"  终端后端: {self.config.terminal_backend}")
        print(f"  工具线程池: {self.config.tool_pool_size}")
        print(f"  终端超时: {self.config.terminal_timeout}s/命令")
        print(f"  终端生命周期: {self.config.terminal_lifetime}s (自动: task_timeout + 120)")
        print(f"  最大并发任务: {self.config.max_concurrent_tasks}")
        print(f"{'='*60}\n")

        # 信号量限制并发 Modal 沙箱创建。
        # 没有这个，所有 86 个任务会同时启动，每个通过线程池工作器中的
        # asyncio.run() 创建 Modal 沙箱。Modal 的阻塞调用 (App.lookup 等)
        # 在同时创建过多时会死锁。
        semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)

        async def _eval_with_semaphore(item):
            async with semaphore:
                return await self._eval_with_timeout(item)

        # 启动所有任务并带挂钟超时，在进度条上跟踪实时准确率
        total_tasks = len(self.all_eval_items)
        eval_tasks = [
            asyncio.ensure_future(_eval_with_semaphore(item))
            for item in self.all_eval_items
        ]

        results = []
        passed_count = 0
        pbar = tqdm(total=total_tasks, desc="正在评估 TB2", dynamic_ncols=True)
        try:
            for coro in asyncio.as_completed(eval_tasks):
                result = await coro
                results.append(result)
                if result and result.get("passed"):
                    passed_count += 1
                done = len(results)
                pct = (passed_count / done * 100) if done else 0
                pbar.set_postfix_str(f"pass={passed_count}/{done} ({pct:.1f}%)")
                pbar.update(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pbar.close()
            print(f"\n\n已中断! 正在清理 {len(eval_tasks)} 个任务...")
            # 取消所有待处理任务
            for task in eval_tasks:
                task.cancel()
            # 让取消传播（finally 块会运行 cleanup_vm）
            await asyncio.gather(*eval_tasks, return_exceptions=True)
            # 安全起见: 清理所有剩余沙箱
            from tools.terminal_tool import cleanup_all_environments
            cleanup_all_environments()
            print("所有沙箱已清理。")
            return
        finally:
            pbar.close()

        end_time = time.time()

        # 过滤 None 结果（不应发生，但安全起见）
        valid_results = [r for r in results if r is not None]

        if not valid_results:
            print("警告: 未获得有效的评估结果")
            return

        # ---- 计算指标 ----
        total = len(valid_results)
        passed = sum(1 for r in valid_results if r.get("passed"))
        overall_pass_rate = passed / total if total > 0 else 0.0

        # 按类别细分
        cat_results: Dict[str, List[Dict]] = defaultdict(list)
        for r in valid_results:
            cat_results[r.get("category", "unknown")].append(r)

        # 构建指标字典
        eval_metrics = {
            "eval/pass_rate": overall_pass_rate,
            "eval/total_tasks": total,
            "eval/passed_tasks": passed,
            "eval/evaluation_time_seconds": end_time - start_time,
        }

        # 按类别指标
        for category, cat_items in sorted(cat_results.items()):
            cat_passed = sum(1 for r in cat_items if r.get("passed"))
            cat_total = len(cat_items)
            cat_pass_rate = cat_passed / cat_total if cat_total > 0 else 0.0
            cat_key = category.replace(" ", "_").replace("-", "_").lower()
            eval_metrics[f"eval/pass_rate_{cat_key}"] = cat_pass_rate

        # 存储指标用于 wandb_log
        self.eval_metrics = [(k, v) for k, v in eval_metrics.items()]

        # ---- 打印摘要 ----
        print(f"\n{'='*60}")
        print("Terminal-Bench 2.0 评估结果")
        print(f"{'='*60}")
        print(f"整体通过率: {overall_pass_rate:.4f} ({passed}/{total})")
        print(f"评估时间: {end_time - start_time:.1f} 秒")

        print("\n类别细分:")
        for category, cat_items in sorted(cat_results.items()):
            cat_passed = sum(1 for r in cat_items if r.get("passed"))
            cat_total = len(cat_items)
            cat_rate = cat_passed / cat_total if cat_total > 0 else 0.0
            print(f"  {category}: {cat_rate:.1%} ({cat_passed}/{cat_total})")

        # 打印单个任务结果
        print("\n任务结果:")
        for r in sorted(valid_results, key=lambda x: x.get("task_name", "")):
            status = "通过" if r.get("passed") else "失败"
            turns = r.get("turns_used", "?")
            error = r.get("error", "")
            extra = f" (错误: {error})" if error else ""
            print(f"  [{status}] {r['task_name']} (turns={turns}){extra}")

        print(f"{'='*60}\n")

        # 构建用于 evaluate_log 的样本记录（包含完整对话）
        samples = [
            {
                "task_name": r.get("task_name"),
                "category": r.get("category"),
                "passed": r.get("passed"),
                "reward": r.get("reward"),
                "turns_used": r.get("turns_used"),
                "error": r.get("error"),
                "messages": r.get("messages"),
            }
            for r in valid_results
        ]

        # 记录评估结果
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
                    "terminal_backend": self.config.terminal_backend,
                },
            )
        except Exception as e:
            print(f"记录评估结果时出错: {e}")

        # 关闭流式文件
        if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
            self._streaming_file.close()
            print(f"  实时结果已保存到: {self._streaming_path}")

        # 终止所有剩余沙箱。超时任务留下孤立的线程池工作器
        # 仍在执行命令 -- cleanup_all 会停止它们。
        from tools.terminal_tool import cleanup_all_environments
        print("\n正在清理所有沙箱...")
        cleanup_all_environments()

        # 关闭工具线程池，使超时任务的孤立工作器立即被终止，
        # 而不是继续对已死沙箱重试并刷屏 TimeoutError 警告。
        from environments.agent_loop import _tool_executor
        _tool_executor.shutdown(wait=False, cancel_futures=True)
        print("完成。")

    # =========================================================================
    # Wandb 日志
    # =========================================================================

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """记录 TB2 特定指标到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        # 添加存储的评估指标
        for metric_name, metric_value in self.eval_metrics:
            wandb_metrics[metric_name] = metric_value
        self.eval_metrics = []

        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    TerminalBench2EvalEnv.cli()
