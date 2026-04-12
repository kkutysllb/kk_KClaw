"""
WebResearchEnv — 多步网络研究强化学习环境
============================================================

训练模型进行准确、高效、多来源的网络研究。

奖励信号:
  - 答案正确性  （LLM 评判，0.0-1.0）
  - 来源多样性    （使用 ≥2 个不同域名）
  - 效率          （惩罚过多工具调用）
  - 工具使用      （实际使用网络工具的奖励）

数据集: FRAMES 基准测试（Google，2024）— 多跳事实问题
  HuggingFace: google/frames-benchmark
  回退:    内置示例问题（无需 HF token）

用法:
    # 第一阶段（OpenAI 兼容服务器）
    python environments/web_research_env.py serve \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name YourModel \\
        --openai.server_type openai

    # Process 模式（离线数据生成）
    python environments/web_research_env.py process \\
        --env.data_path_to_save_groups data/web_research.jsonl

    # 独立评估
    python environments/web_research_env.py evaluate \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name YourModel

构建者: github.com/jackx707
灵感来源: GroceryMind — 生产级 KClaw agent 执行实时网络研究
             跨德国杂货店（firecrawl + kclaw）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pydantic import Field

# 确保 kclaw 根目录在路径上
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# ---------------------------------------------------------------------------
# 可选的 HuggingFace 数据集导入
# ---------------------------------------------------------------------------
try:
    from datasets import load_dataset
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

from atroposlib.envs.base import ScoredDataGroup
from atroposlib.envs.server_handling.server_manager import APIServerConfig
from atroposlib.type_definitions import Item

from environments.kclaw_base_env import KClawAgentBaseEnv, KClawAgentEnvConfig
from environments.agent_loop import AgentResult
from environments.tool_context import ToolContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 回退示例数据集（HuggingFace 不可用时使用）
# 需要真实网络搜索才能回答的多跳问题。
# ---------------------------------------------------------------------------
SAMPLE_QUESTIONS = [
    {
        "question": "What is the current population of the capital city of the country that won the 2022 FIFA World Cup?",
        "answer": "Buenos Aires has approximately 3 million people in the city proper, or around 15 million in the greater metro area.",
        "difficulty": "medium",
        "hops": 2,
    },
    {
        "question": "Who is the CEO of the company that makes the most widely used open-source container orchestration platform?",
        "answer": "The Linux Foundation oversees Kubernetes. CNCF (Cloud Native Computing Foundation) is the specific body — it does not have a traditional CEO but has an executive director.",
        "difficulty": "medium",
        "hops": 2,
    },
    {
        "question": "What programming language was used to write the original version of the web framework used by Instagram?",
        "answer": "Django, which Instagram was built on, is written in Python.",
        "difficulty": "easy",
        "hops": 2,
    },
    {
        "question": "In what year was the university founded where the inventor of the World Wide Web currently holds a professorship?",
        "answer": "Tim Berners-Lee holds a professorship at MIT (founded 1861) and the University of Southampton (founded 1952).",
        "difficulty": "hard",
        "hops": 3,
    },
    {
        "question": "What is the latest stable version of the programming language that ranks #1 on the TIOBE index as of this year?",
        "answer": "Python is currently #1 on TIOBE. The latest stable version should be verified via the official python.org site.",
        "difficulty": "medium",
        "hops": 2,
    },
    {
        "question": "How many employees does the parent company of Instagram have?",
        "answer": "Meta Platforms (parent of Instagram) employs approximately 70,000+ people as of recent reports.",
        "difficulty": "medium",
        "hops": 2,
    },
    {
        "question": "What is the current interest rate set by the central bank of the country where the Eiffel Tower is located?",
        "answer": "The European Central Bank sets rates for France/eurozone. The current rate should be verified — it has changed frequently in 2023-2025.",
        "difficulty": "hard",
        "hops": 2,
    },
    {
        "question": "Which company acquired the startup founded by the creator of Oculus VR?",
        "answer": "Palmer Luckey founded Oculus VR, which was acquired by Facebook (now Meta). He later founded Anduril Industries.",
        "difficulty": "medium",
        "hops": 2,
    },
    {
        "question": "What is the market cap of the company that owns the most popular search engine in Russia?",
        "answer": "Yandex (now split into separate entities after 2024 restructuring). Current market cap should be verified via financial sources.",
        "difficulty": "hard",
        "hops": 2,
    },
    {
        "question": "What was the GDP growth rate of the country that hosted the most recent Summer Olympics?",
        "answer": "Paris, France hosted the 2024 Summer Olympics. France's recent GDP growth should be verified via World Bank or IMF data.",
        "difficulty": "hard",
        "hops": 2,
    },
]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class WebResearchEnvConfig(KClawAgentEnvConfig):
    """网络研究强化学习环境的配置。"""

    # 奖励权重
    correctness_weight: float = Field(
        default=0.6,
        description="奖励中答案正确性的权重（LLM 评判分数）。"
    )
    tool_usage_weight: float = Field(
        default=0.2,
        description="工具使用信号的权重（模型是否实际使用了网络工具？）。"
    )
    efficiency_weight: float = Field(
        default=0.2,
        description="效率信号的权重（惩罚过多工具调用）。"
    )
    diversity_bonus: float = Field(
        default=0.1,
        description="引用 ≥2 个不同域名的奖励。"
    )

    # 效率阈值
    efficient_max_calls: int = Field(
        default=5,
        description="效率惩罚开始前的最大工具调用次数。"
    )
    heavy_penalty_calls: int = Field(
        default=10,
        description="效率惩罚急剧增加的调用次数。"
    )

    # 评估
    eval_size: int = Field(
        default=20,
        description="用于评估的留出项目数量。"
    )
    eval_split_ratio: float = Field(
        default=0.1,
        description="留出用于评估的数据集比例（0.0-1.0）。"
    )

    # 数据集
    dataset_name: str = Field(
        default="google/frames-benchmark",
        description="研究问题的 HuggingFace 数据集名称。"
    )


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------

class WebResearchEnv(KClawAgentBaseEnv):
    """
    用于训练多步网络研究技能的强化学习环境。

    模型会收到一个需要 2-3 跳网络研究的事实问题，
    必须使用 web_search / web_extract 工具来查找和综合答案。

    多信号奖励:
      60% — 答案正确性（LLM 评判）
      20% — 工具使用（模型是否实际搜索了网络？）
      20% — 效率（惩罚超过5次工具调用）

    引用 ≥2 个不同域名可获得 +0.1 奖励。
    """

    name = "web-research"
    env_config_cls = WebResearchEnvConfig

    # 此环境的默认工具集 — web 用于搜索，file 用于保存笔记
    default_toolsets = ["web", "file"]

    @classmethod
    def config_init(cls) -> Tuple[WebResearchEnvConfig, List[APIServerConfig]]:
        """网络研究环境的默认配置。"""
        env_config = WebResearchEnvConfig(
            enabled_toolsets=["web", "file"],
            max_agent_turns=15,
            agent_temperature=1.0,
            system_prompt=(
                "You are a highly capable research agent. When asked a factual question, "
                "always use web_search to find current, accurate information before answering. "
                "Cite at least 2 sources. Be concise and accurate."
            ),
            group_size=4,
            total_steps=1000,
            steps_per_eval=100,
            use_wandb=True,
            wandb_name="web-research",
        )

        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4.5",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[dict] = []
        self._eval_items: list[dict] = []
        self._index: int = 0

        # 用于 wandb 的指标跟踪
        self._reward_buffer: list[float] = []
        self._correctness_buffer: list[float] = []
        self._tool_usage_buffer: list[float] = []
        self._efficiency_buffer: list[float] = []
        self._diversity_buffer: list[float] = []

    # ------------------------------------------------------------------
    # 1. Setup — 加载数据集
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """加载 FRAMES 基准测试或回退到内置示例。"""
        if HF_AVAILABLE:
            try:
                logger.info("正在从 HuggingFace 加载 FRAMES 基准测试...")
                ds = load_dataset(self.config.dataset_name, split="test")
                self._items = [
                    {
                        "question": row["Prompt"],
                        "answer": row["Answer"],
                        "difficulty": row.get("reasoning_types", "unknown"),
                        "hops": 2,
                    }
                    for row in ds
                ]
                # 留出用于评估
                eval_size = max(
                    self.config.eval_size,
                    int(len(self._items) * self.config.eval_split_ratio),
                )
                random.shuffle(self._items)
                self._eval_items = self._items[:eval_size]
                self._items = self._items[eval_size:]
                logger.info(
                    f"从 FRAMES 基准测试加载了 {len(self._items)} 个训练 / {len(self._eval_items)} 个评估项目。"
                )
                return
            except Exception as e:
                logger.warning(f"无法从 HuggingFace 加载 FRAMES: {e}。使用内置示例。")

        # 回退
        random.shuffle(SAMPLE_QUESTIONS)
        split = max(1, len(SAMPLE_QUESTIONS) * 8 // 10)
        self._items = SAMPLE_QUESTIONS[:split]
        self._eval_items = SAMPLE_QUESTIONS[split:]
        logger.info(
            f"使用内置示例数据集: {len(self._items)} 个训练 / "
            f"{len(self._eval_items)} 个评估项目。"
        )

    # ------------------------------------------------------------------
    # 2. get_next_item — 返回下一个问题
    # ------------------------------------------------------------------

    async def get_next_item(self) -> dict:
        """返回下一个项目，循环遍历数据集。"""
        if not self._items:
            raise RuntimeError("数据集为空。你调用了 setup() 了吗？")
        item = self._items[self._index % len(self._items)]
        self._index += 1
        return item

    # ------------------------------------------------------------------
    # 3. format_prompt — 构建面向用户的提示
    # ------------------------------------------------------------------

    def format_prompt(self, item: dict) -> str:
        """将研究问题格式化为任务提示。"""
        return (
            f"Research the following question thoroughly using web search. "
            f"You MUST search the web to find current, accurate information — "
            f"do not rely solely on your training data.\n\n"
            f"Question: {item['question']}\n\n"
            f"Requirements:\n"
            f"- Use web_search and/or web_extract tools to find information\n"
            f"- Search at least 2 different sources\n"
            f"- Provide a concise, accurate answer (2-4 sentences)\n"
            f"- Cite the sources you used"
        )

    # ------------------------------------------------------------------
    # 4. compute_reward — 多信号评分
    # ------------------------------------------------------------------

    async def compute_reward(
        self,
        item: dict,
        result: AgentResult,
        ctx: ToolContext,
    ) -> float:
        """
        多信号奖励函数:

          correctness_weight * correctness  — LLM 评判将答案与真实答案比较
          tool_usage_weight  * tool_used    — 二进制：模型是否使用了网络工具？
          efficiency_weight  * efficiency   — 惩罚浪费的 tool 使用
          + diversity_bonus                 — 来源多样性（≥2 个不同域名）
        """
        # 从消息中提取最终回复（最后一条带有内容的 assistant 消息）
        final_response = ""
        tools_used: list[str] = []
        for msg in reversed(result.messages):
            if msg.get("role") == "assistant" and msg.get("content") and not final_response:
                final_response = msg["content"]
            # 从 tool call 消息中收集工具名称
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name", "")
                    if name:
                        tools_used.append(name)
        tool_call_count: int = result.turns_used or len(tools_used)

        cfg = self.config

        # ---- 信号 1: 答案正确性（LLM 评判） ----------------
        correctness = await self._llm_judge(
            question=item["question"],
            expected=item["answer"],
            model_answer=final_response,
        )

        # ---- 信号 2: 网络工具使用 --------------------------------
        web_tools = {"web_search", "web_extract", "search", "firecrawl"}
        tool_used = 1.0 if any(t in web_tools for t in tools_used) else 0.0

        # ---- 信号 3: 效率 ------------------------------------
        if tool_call_count <= cfg.efficient_max_calls:
            efficiency = 1.0
        elif tool_call_count <= cfg.heavy_penalty_calls:
            efficiency = 1.0 - (tool_call_count - cfg.efficient_max_calls) * 0.08
        else:
            efficiency = max(0.0, 1.0 - (tool_call_count - cfg.efficient_max_calls) * 0.12)

        # ---- 奖励: 来源多样性 ---------------------------------
        domains = self._extract_domains(final_response)
        diversity = cfg.diversity_bonus if len(domains) >= 2 else 0.0

        # ---- 组合 ------------------------------------------------
        reward = (
            cfg.correctness_weight * correctness
            + cfg.tool_usage_weight * tool_used
            + cfg.efficiency_weight * efficiency
            + diversity
        )
        reward = min(1.0, max(0.0, reward))  # 钳制到 [0, 1]

        # 跟踪用于 wandb
        self._reward_buffer.append(reward)
        self._correctness_buffer.append(correctness)
        self._tool_usage_buffer.append(tool_used)
        self._efficiency_buffer.append(efficiency)
        self._diversity_buffer.append(diversity)

        logger.debug(
            f"Reward breakdown — correctness={correctness:.2f}, "
            f"tool_used={tool_used:.1f}, efficiency={efficiency:.2f}, "
            f"diversity={diversity:.1f} → total={reward:.3f}"
        )

        return reward

    # ------------------------------------------------------------------
    # 5. evaluate — 在留出的评估集上运行
    # ------------------------------------------------------------------

    async def evaluate(self, *args, **kwargs) -> None:
        """使用完整的 Agent 循环和工具在留出的评估集上运行评估。

        每个评估项目都通过与训练相同的 Agent 循环运行——
        模型可以使用 web_search、web_extract 等来研究答案。
        这衡量的是实际的 Agent 研究能力，而不仅仅是知识。
        """
        import time
        import uuid
        from environments.agent_loop import KClawAgentLoop
        from environments.tool_context import ToolContext

        items = self._eval_items
        if not items:
            logger.warning("没有可用的评估项目。")
            return

        eval_size = min(self.config.eval_size, len(items))
        eval_items = items[:eval_size]

        logger.info(f"在 {len(eval_items)} 个问题上运行评估（使用 Agent 循环 + 工具）...")
        start_time = time.time()
        samples = []

        # 为所有评估项目一次性解析工具
        tools, valid_names = self._resolve_tools_for_group()

        for i, item in enumerate(eval_items):
            task_id = str(uuid.uuid4())
            logger.info(f"评估 [{i+1}/{len(eval_items)}]: {item['question'][:80]}...")

            try:
                # 构建消息
                messages: List[Dict[str, Any]] = []
                if self.config.system_prompt:
                    messages.append({"role": "system", "content": self.config.system_prompt})
                messages.append({"role": "user", "content": self.format_prompt(item)})

                # 使用工具运行完整的 Agent 循环
                agent = KClawAgentLoop(
                    server=self.server,
                    tool_schemas=tools,
                    valid_tool_names=valid_names,
                    max_turns=self.config.max_agent_turns,
                    task_id=task_id,
                    temperature=0.0,  # 评估时使用确定性采样
                    max_tokens=self.config.max_token_length,
                    extra_body=self.config.extra_body,
                    budget_config=self.config.build_budget_config(),
                )
                result = await agent.run(messages)

                # 从消息中提取最终回复和工具使用情况
                final_response = ""
                tool_call_count = 0
                for msg in reversed(result.messages):
                    if msg.get("role") == "assistant" and msg.get("content") and not final_response:
                        final_response = msg["content"]
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        tool_call_count += len(msg["tool_calls"])

                # 计算奖励（包含用于正确性的 LLM 评判）
                # 临时保存缓冲区长度以便我们可以提取
                # 正确性分数而不调用评判器两次，并避免
                # 用评估数据污染训练指标缓冲区。
                buf_len = len(self._correctness_buffer)
                ctx = ToolContext(task_id)
                try:
                    reward = await self.compute_reward(item, result, ctx)
                finally:
                    ctx.cleanup()

                # 从缓冲区中提取正确性分数（compute_reward 追加的）
                # 然后从训练缓冲区中移除评估条目
                correctness = (
                    self._correctness_buffer[buf_len]
                    if len(self._correctness_buffer) > buf_len
                    else 0.0
                )
                # 回滚缓冲区以避免污染训练指标
                for buf in (
                    self._reward_buffer, self._correctness_buffer,
                    self._tool_usage_buffer, self._efficiency_buffer,
                    self._diversity_buffer,
                ):
                    if len(buf) > buf_len:
                        buf.pop()

                samples.append({
                    "prompt": item["question"],
                    "response": final_response[:500],
                    "expected": item["answer"],
                    "correctness": correctness,
                    "reward": reward,
                    "tool_calls": tool_call_count,
                    "turns": result.turns_used,
                })

                logger.info(
                    f"  → 正确性={correctness:.2f}, 奖励={reward:.3f}, "
                    f"工具={tool_call_count}, 轮次={result.turns_used}"
                )

            except Exception as e:
                logger.error(f"评估项目出错: {e}")
                samples.append({
                    "prompt": item["question"],
                    "response": f"ERROR: {e}",
                    "expected": item["answer"],
                    "correctness": 0.0,
                    "reward": 0.0,
                    "tool_calls": 0,
                    "turns": 0,
                })

        end_time = time.time()

        # 计算聚合指标
        correctness_scores = [s["correctness"] for s in samples]
        rewards = [s["reward"] for s in samples]
        tool_counts = [s["tool_calls"] for s in samples]
        n = len(samples)

        eval_metrics = {
            "eval/mean_correctness": sum(correctness_scores) / n if n else 0.0,
            "eval/mean_reward": sum(rewards) / n if n else 0.0,
            "eval/mean_tool_calls": sum(tool_counts) / n if n else 0.0,
            "eval/tool_usage_rate": sum(1 for t in tool_counts if t > 0) / n if n else 0.0,
            "eval/n_items": n,
        }

        logger.info(
            f"评估完成 — 正确性={eval_metrics['eval/mean_correctness']:.3f}, "
            f"奖励={eval_metrics['eval/mean_reward']:.3f}, "
            f"工具使用率={eval_metrics['eval/tool_usage_rate']:.0%}"
        )

        await self.evaluate_log(
            metrics=eval_metrics,
            samples=samples,
            start_time=start_time,
            end_time=end_time,
        )

    # ------------------------------------------------------------------
    # 6. wandb_log — 自定义指标
    # ------------------------------------------------------------------

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None) -> None:
        """将奖励分解指标记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        if self._reward_buffer:
            n = len(self._reward_buffer)
            wandb_metrics["train/mean_reward"] = sum(self._reward_buffer) / n
            wandb_metrics["train/mean_correctness"] = sum(self._correctness_buffer) / n
            wandb_metrics["train/mean_tool_usage"] = sum(self._tool_usage_buffer) / n
            wandb_metrics["train/mean_efficiency"] = sum(self._efficiency_buffer) / n
            wandb_metrics["train/mean_diversity"] = sum(self._diversity_buffer) / n
            wandb_metrics["train/total_rollouts"] = n

            # 准确率分组
            wandb_metrics["train/correct_rate"] = (
                sum(1 for c in self._correctness_buffer if c >= 0.7) / n
            )
            wandb_metrics["train/tool_usage_rate"] = (
                sum(1 for t in self._tool_usage_buffer if t > 0) / n
            )

            # 清空缓冲区
            self._reward_buffer.clear()
            self._correctness_buffer.clear()
            self._tool_usage_buffer.clear()
            self._efficiency_buffer.clear()
            self._diversity_buffer.clear()

        await super().wandb_log(wandb_metrics)

    # ------------------------------------------------------------------
    # 私有辅助方法
    # ------------------------------------------------------------------

    async def _llm_judge(
        self,
        question: str,
        expected: str,
        model_answer: str,
    ) -> float:
        """
        使用服务器的 LLM 评判答案正确性。
        如果 LLM 调用失败则回退到关键词启发式方法。
        """
        if not model_answer or not model_answer.strip():
            return 0.0

        judge_prompt = (
            "You are an impartial judge evaluating the quality of an AI research answer.\n\n"
            f"Question: {question}\n\n"
            f"Reference answer: {expected}\n\n"
            f"Model answer: {model_answer}\n\n"
            "Score the model answer on a scale from 0.0 to 1.0 where:\n"
            "  1.0 = fully correct and complete\n"
            "  0.7 = mostly correct with minor gaps\n"
            "  0.4 = partially correct\n"
            "  0.1 = mentions relevant topic but wrong or very incomplete\n"
            "  0.0 = completely wrong or no answer\n\n"
            "Consider: factual accuracy, completeness, and relevance.\n"
            'Respond with ONLY a JSON object: {"score": <float>, "reason": "<one sentence>"}'
        )

        try:
            response = await self.server.chat_completion(
                messages=[{"role": "user", "content": judge_prompt}],
                n=1,
                max_tokens=150,
                temperature=0.0,
                split="eval",
            )
            text = response.choices[0].message.content if response.choices else ""
            parsed = self._parse_judge_json(text)
            if parsed is not None:
                return float(parsed)
        except Exception as e:
            logger.debug(f"LLM 评判失败: {e}。使用启发式方法。")

        return self._heuristic_score(expected, model_answer)

    @staticmethod
    def _parse_judge_json(text: str) -> Optional[float]:
        """从 LLM 评判的 JSON 响应中提取分数浮点数。"""
        try:
            clean = re.sub(r"```(?:json)?|```", "", text).strip()
            data = json.loads(clean)
            score = float(data.get("score", -1))
            if 0.0 <= score <= 1.0:
                return score
        except Exception:
            match = re.search(r'"score"\s*:\s*([0-9.]+)', text)
            if match:
                score = float(match.group(1))
                if 0.0 <= score <= 1.0:
                    return score
        return None

    @staticmethod
    def _heuristic_score(expected: str, model_answer: str) -> float:
        """轻量级关键词重叠分数作为回退方案。"""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "of", "in", "on",
            "at", "to", "for", "with", "and", "or", "but", "it", "its",
            "this", "that", "as", "by", "from", "be", "has", "have", "had",
        }

        def tokenize(text: str) -> set:
            tokens = re.findall(r'\b\w+\b', text.lower())
            return {t for t in tokens if t not in stopwords and len(t) > 2}

        expected_tokens = tokenize(expected)
        answer_tokens = tokenize(model_answer)

        if not expected_tokens:
            return 0.5

        overlap = len(expected_tokens & answer_tokens)
        union = len(expected_tokens | answer_tokens)

        jaccard = overlap / union if union > 0 else 0.0
        recall = overlap / len(expected_tokens)
        return min(1.0, 0.4 * jaccard + 0.6 * recall)

    @staticmethod
    def _extract_domains(text: str) -> set:
        """从回复中引用的 URL 提取唯一域名。"""
        urls = re.findall(r'https?://[^\s\)>\]"\']+', text)
        domains = set()
        for url in urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower().lstrip("www.")
                if domain:
                    domains.add(domain)
            except Exception:
                pass
        return domains


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    WebResearchEnv.cli()
