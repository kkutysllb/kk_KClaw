#!/usr/bin/env python3
"""
轨迹压缩器

对已完成的代理轨迹进行后处理，在目标 token 预算内压缩它们，
同时保留训练信号质量。

压缩策略：
1. 保护前几轮（system、human、第一个 gpt、第一个 tool）
2. 保护最后 N 轮（最终操作和结论）
3. 仅压缩中间轮次，从第二个 tool 响应开始
4. 仅压缩达到目标所需的内容
5. 用一条 human 摘要消息替换压缩区域
6. 保留剩余的工具调用（模型在摘要后继续工作）

使用方法：
    # 压缩目录中的 JSONL 文件
    python trajectory_compressor.py --input=data/my_run
    
    # 压缩单个 JSONL 文件
    python trajectory_compressor.py --input=data/trajectories.jsonl
    
    # 压缩文件的 15% 采样
    python trajectory_compressor.py --input=data/trajectories.jsonl --sample_percent=15
    
    # 使用自定义输出和 token 目标压缩
    python trajectory_compressor.py --input=data/trajectories.jsonl --output=compressed.jsonl --target_max_tokens=16000
    
    # 从目录中采样 10%
    python trajectory_compressor.py --input=data/my_run --sample_percent=10
"""

import json
import os
import time
import yaml
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime
import fire
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.console import Console
from kclaw_constants import OPENROUTER_BASE_URL
from agent.retry_utils import jittered_backoff

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()


@dataclass
class CompressionConfig:
    """轨迹压缩配置。"""
    # 分词器
    tokenizer_name: str = "moonshotai/Kimi-K2-Thinking"
    trust_remote_code: bool = True
    
    # 压缩目标
    target_max_tokens: int = 15250
    summary_target_tokens: int = 750
    
    # 保护的轮次
    protect_first_system: bool = True
    protect_first_human: bool = True
    protect_first_gpt: bool = True
    protect_first_tool: bool = True
    protect_last_n_turns: int = 4
    
    # 摘要（OpenRouter）
    summarization_model: str = "google/gemini-3-flash-preview"
    base_url: str = OPENROUTER_BASE_URL
    api_key_env: str = "OPENROUTER_API_KEY"
    temperature: float = 0.3
    max_retries: int = 3
    retry_delay: int = 2
    
    # 输出
    add_summary_notice: bool = True
    summary_notice_text: str = "\n\nSome of your previous tool responses may be summarized to preserve context."
    output_suffix: str = "_compressed"
    
    # 处理
    num_workers: int = 4
    max_concurrent_requests: int = 50  # 摘要的最大并发 API 调用数
    skip_under_target: bool = True
    save_over_limit: bool = True
    per_trajectory_timeout: int = 300  # 每条轨迹的超时时间（秒）（默认：5 分钟）
    
    # 指标
    metrics_enabled: bool = True
    metrics_per_trajectory: bool = True
    metrics_output_file: str = "compression_metrics.json"
    
    @classmethod
    def from_yaml(cls, yaml_path: str) -> "CompressionConfig":
        """从 YAML 文件加载配置。"""
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)
        
        config = cls()
        
        # 分词器
        if 'tokenizer' in data:
            config.tokenizer_name = data['tokenizer'].get('name', config.tokenizer_name)
            config.trust_remote_code = data['tokenizer'].get('trust_remote_code', config.trust_remote_code)
        
        # 压缩
        if 'compression' in data:
            config.target_max_tokens = data['compression'].get('target_max_tokens', config.target_max_tokens)
            config.summary_target_tokens = data['compression'].get('summary_target_tokens', config.summary_target_tokens)
        
        # 保护的轮次
        if 'protected_turns' in data:
            config.protect_first_system = data['protected_turns'].get('first_system', config.protect_first_system)
            config.protect_first_human = data['protected_turns'].get('first_human', config.protect_first_human)
            config.protect_first_gpt = data['protected_turns'].get('first_gpt', config.protect_first_gpt)
            config.protect_first_tool = data['protected_turns'].get('first_tool', config.protect_first_tool)
            config.protect_last_n_turns = data['protected_turns'].get('last_n_turns', config.protect_last_n_turns)
        
        # 摘要
        if 'summarization' in data:
            config.summarization_model = data['summarization'].get('model', config.summarization_model)
            config.base_url = data['summarization'].get('base_url') or config.base_url
            config.api_key_env = data['summarization'].get('api_key_env', config.api_key_env)
            config.temperature = data['summarization'].get('temperature', config.temperature)
            config.max_retries = data['summarization'].get('max_retries', config.max_retries)
            config.retry_delay = data['summarization'].get('retry_delay', config.retry_delay)
        
        # 输出
        if 'output' in data:
            config.add_summary_notice = data['output'].get('add_summary_notice', config.add_summary_notice)
            config.summary_notice_text = data['output'].get('summary_notice_text', config.summary_notice_text)
            config.output_suffix = data['output'].get('output_suffix', config.output_suffix)
        
        # 处理
        if 'processing' in data:
            config.num_workers = data['processing'].get('num_workers', config.num_workers)
            config.max_concurrent_requests = data['processing'].get('max_concurrent_requests', config.max_concurrent_requests)
            config.skip_under_target = data['processing'].get('skip_under_target', config.skip_under_target)
            config.save_over_limit = data['processing'].get('save_over_limit', config.save_over_limit)
        
        # 指标
        if 'metrics' in data:
            config.metrics_enabled = data['metrics'].get('enabled', config.metrics_enabled)
            config.metrics_per_trajectory = data['metrics'].get('per_trajectory', config.metrics_per_trajectory)
            config.metrics_output_file = data['metrics'].get('output_file', config.metrics_output_file)
        
        return config


@dataclass
class TrajectoryMetrics:
    """单个轨迹压缩的指标。"""
    original_tokens: int = 0
    compressed_tokens: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 1.0
    
    original_turns: int = 0
    compressed_turns: int = 0
    turns_removed: int = 0
    
    turns_compressed_start_idx: int = -1
    turns_compressed_end_idx: int = -1
    turns_in_compressed_region: int = 0
    
    was_compressed: bool = False
    still_over_limit: bool = False
    skipped_under_target: bool = False
    
    summarization_api_calls: int = 0
    summarization_errors: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": round(self.compression_ratio, 4),
            "original_turns": self.original_turns,
            "compressed_turns": self.compressed_turns,
            "turns_removed": self.turns_removed,
            "compression_region": {
                "start_idx": self.turns_compressed_start_idx,
                "end_idx": self.turns_compressed_end_idx,
                "turns_count": self.turns_in_compressed_region,
            },
            "was_compressed": self.was_compressed,
            "still_over_limit": self.still_over_limit,
            "skipped_under_target": self.skipped_under_target,
            "summarization_api_calls": self.summarization_api_calls,
            "summarization_errors": self.summarization_errors,
        }


@dataclass 
class AggregateMetrics:
    """所有轨迹的聚合指标。"""
    total_trajectories: int = 0
    trajectories_compressed: int = 0
    trajectories_skipped_under_target: int = 0
    trajectories_still_over_limit: int = 0
    trajectories_failed: int = 0
    
    total_tokens_before: int = 0
    total_tokens_after: int = 0
    total_tokens_saved: int = 0
    
    total_turns_before: int = 0
    total_turns_after: int = 0
    total_turns_removed: int = 0
    
    total_summarization_calls: int = 0
    total_summarization_errors: int = 0
    
    # 分布统计
    compression_ratios: List[float] = field(default_factory=list)
    tokens_saved_list: List[int] = field(default_factory=list)
    turns_removed_list: List[int] = field(default_factory=list)
    
    processing_start_time: str = ""
    processing_end_time: str = ""
    processing_duration_seconds: float = 0.0
    
    def add_trajectory_metrics(self, metrics: TrajectoryMetrics):
        """将轨迹的指标添加到聚合中。"""
        self.total_trajectories += 1
        self.total_tokens_before += metrics.original_tokens
        self.total_tokens_after += metrics.compressed_tokens
        self.total_tokens_saved += metrics.tokens_saved
        self.total_turns_before += metrics.original_turns
        self.total_turns_after += metrics.compressed_turns
        self.total_turns_removed += metrics.turns_removed
        self.total_summarization_calls += metrics.summarization_api_calls
        self.total_summarization_errors += metrics.summarization_errors
        
        if metrics.was_compressed:
            self.trajectories_compressed += 1
            self.compression_ratios.append(metrics.compression_ratio)
            self.tokens_saved_list.append(metrics.tokens_saved)
            self.turns_removed_list.append(metrics.turns_removed)
        
        if metrics.skipped_under_target:
            self.trajectories_skipped_under_target += 1
        
        if metrics.still_over_limit:
            self.trajectories_still_over_limit += 1
    
    def to_dict(self) -> Dict[str, Any]:
        avg_compression_ratio = (
            sum(self.compression_ratios) / len(self.compression_ratios) 
            if self.compression_ratios else 1.0
        )
        avg_tokens_saved = (
            sum(self.tokens_saved_list) / len(self.tokens_saved_list)
            if self.tokens_saved_list else 0
        )
        avg_turns_removed = (
            sum(self.turns_removed_list) / len(self.turns_removed_list)
            if self.turns_removed_list else 0
        )
        
        return {
            "summary": {
                "total_trajectories": self.total_trajectories,
                "trajectories_compressed": self.trajectories_compressed,
                "trajectories_skipped_under_target": self.trajectories_skipped_under_target,
                "trajectories_still_over_limit": self.trajectories_still_over_limit,
                "trajectories_failed": self.trajectories_failed,
                "compression_rate": round(self.trajectories_compressed / max(self.total_trajectories, 1), 4),
            },
            "tokens": {
                "total_before": self.total_tokens_before,
                "total_after": self.total_tokens_after,
                "total_saved": self.total_tokens_saved,
                "overall_compression_ratio": round(self.total_tokens_after / max(self.total_tokens_before, 1), 4),
            },
            "turns": {
                "total_before": self.total_turns_before,
                "total_after": self.total_turns_after,
                "total_removed": self.total_turns_removed,
            },
            "averages": {
                "avg_compression_ratio": round(avg_compression_ratio, 4),
                "avg_tokens_saved_per_compressed": round(avg_tokens_saved, 1),
                "avg_turns_removed_per_compressed": round(avg_turns_removed, 2),
            },
            "summarization": {
                "total_api_calls": self.total_summarization_calls,
                "total_errors": self.total_summarization_errors,
                "success_rate": round(1 - (self.total_summarization_errors / max(self.total_summarization_calls, 1)), 4),
            },
            "processing": {
                "start_time": self.processing_start_time,
                "end_time": self.processing_end_time,
                "duration_seconds": round(self.processing_duration_seconds, 2),
            },
        }


class TrajectoryCompressor:
    """
    将代理轨迹压缩到目标 token 预算内。
    
    压缩策略：
    1. 保留受保护的头部轮次（system、human、第一个 gpt+tool）
    2. 保留受保护的尾部轮次（最后 N 轮）
    3. 仅从可压缩的中间区域压缩所需的内容
    4. 用一条 human 摘要消息替换压缩的轮次
    5. 保留剩余的中间轮次（模型继续使用工具）
    """
    
    def __init__(self, config: CompressionConfig):
        """初始化压缩器。"""
        self.config = config
        self.aggregate_metrics = AggregateMetrics()
        
        # 初始化分词器
        self._init_tokenizer()
        
        # 初始化 OpenRouter 客户端
        self._init_summarizer()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)
    
    def _init_tokenizer(self):
        """初始化 HuggingFace 分词器用于 token 计数。"""
        try:
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.config.tokenizer_name,
                trust_remote_code=self.config.trust_remote_code
            )
            print(f"✅ 分词器已加载: {self.config.tokenizer_name}")
        except Exception as e:
            raise RuntimeError(f"加载分词器 '{self.config.tokenizer_name}' 失败: {e}")
    
    def _init_summarizer(self):
        """初始化 LLM 路由用于摘要（同步和异步）。

        使用集中式 provider 路由的 call_llm/async_call_llm，
        该路由在内部处理认证、请求头和 provider 检测。
        对于自定义端点，回退到原始客户端构造。
        """

        provider = self._detect_provider()
        if provider:
            # 存储 provider 用于 _generate_summary 调用
            self._llm_provider = provider
            self._use_call_llm = True
            # 验证 provider 是否可用
            from agent.auxiliary_client import resolve_provider_client
            client, _ = resolve_provider_client(
                provider, model=self.config.summarization_model)
            if client is None:
                raise RuntimeError(
                    f"Provider '{provider}' 未配置。"
                    f"请检查您的 API 密钥或运行: kclaw setup")
            self.client = None  # 不直接使用
            self.async_client = None  # 不直接使用
        else:
            # 自定义端点 — 使用配置中的原始 base_url + api_key_env
            self._use_call_llm = False
            api_key = os.getenv(self.config.api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"缺少 API 密钥。请设置 {self.config.api_key_env} "
                    f"环境变量。")
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key, base_url=self.config.base_url)
            # AsyncOpenAI 在 _get_async_client() 中延迟创建，
            # 以便它绑定到当前事件循环 — 避免当 process_directory()
            # 被多次调用时（每次调用通过 asyncio.run() 创建新循环）出现"事件循环已关闭"错误。
            self.async_client = None
            self._async_client_api_key = api_key

        print(f"✅ 摘要客户端已初始化: {self.config.summarization_model}")
        print(f"   最大并发请求数: {self.config.max_concurrent_requests}")

    def _get_async_client(self):
        """返回绑定到当前事件循环的 AsyncOpenAI 客户端。

        延迟创建，以便 ``asyncio.run()`` 在
        ``process_directory()`` 中的每次调用都能获得绑定到其自己循环的客户端，
        避免重复调用时出现"事件循环已关闭"错误。
        """
        from openai import AsyncOpenAI
        # Always create a fresh client so it binds to the running loop.
        self.async_client = AsyncOpenAI(
            api_key=self._async_client_api_key,
            base_url=self.config.base_url,
        )
        return self.async_client

    def _detect_provider(self) -> str:
        """从配置的 base_url 检测 provider 名称。"""
        url = (self.config.base_url or "").lower()
        if "openrouter" in url:
            return "openrouter"
        if "nousresearch.com" in url:
            return "nous"
        if "chatgpt.com/backend-api/codex" in url:
            return "codex"
        if "api.z.ai" in url:
            return "zai"
        if "moonshot.ai" in url or "api.kimi.com" in url:
            return "kimi-coding"
        if "minimaxi.com" in url:
            return "minimax-cn"
        if "minimax.io" in url:
            return "minimax"
        # 未知的 base_url — 不是已知的 provider
        return ""
    
    def count_tokens(self, text: str) -> int:
        """使用配置的分词器计算文本中的 token 数。"""
        if not text:
            return 0
        try:
            return len(self.tokenizer.encode(text))
        except Exception:
            # 回退到字符估算
            return len(text) // 4
    
    def count_trajectory_tokens(self, trajectory: List[Dict[str, str]]) -> int:
        """计算轨迹中的总 token 数。"""
        return sum(self.count_tokens(turn.get("value", "")) for turn in trajectory)
    
    def count_turn_tokens(self, trajectory: List[Dict[str, str]]) -> List[int]:
        """计算轨迹中每轮的 token 数。"""
        return [self.count_tokens(turn.get("value", "")) for turn in trajectory]
    
    def _find_protected_indices(self, trajectory: List[Dict[str, str]]) -> Tuple[set, int, int]:
        """
        查找受保护轮次的索引。
        
        返回:
            Tuple of (protected_set, compressible_start, compressible_end)
        """
        n = len(trajectory)
        protected = set()
        
        # 跟踪首次出现
        first_system = first_human = first_gpt = first_tool = None
        
        for i, turn in enumerate(trajectory):
            role = turn.get("from", "")
            if role == "system" and first_system is None:
                first_system = i
            elif role == "human" and first_human is None:
                first_human = i
            elif role == "gpt" and first_gpt is None:
                first_gpt = i
            elif role == "tool" and first_tool is None:
                first_tool = i
        
        # 保护首次出现的轮次
        if self.config.protect_first_system and first_system is not None:
            protected.add(first_system)
        if self.config.protect_first_human and first_human is not None:
            protected.add(first_human)
        if self.config.protect_first_gpt and first_gpt is not None:
            protected.add(first_gpt)
        if self.config.protect_first_tool and first_tool is not None:
            protected.add(first_tool)
        
        # 保护最后 N 轮
        for i in range(max(0, n - self.config.protect_last_n_turns), n):
            protected.add(i)
        
        # 确定可压缩区域
        # 在最后一个受保护的头部轮次之后开始
        head_protected = [i for i in protected if i < n // 2]
        tail_protected = [i for i in protected if i >= n // 2]
        
        compressible_start = max(head_protected) + 1 if head_protected else 0
        compressible_end = min(tail_protected) if tail_protected else n
        
        return protected, compressible_start, compressible_end
    
    def _extract_turn_content_for_summary(self, trajectory: List[Dict[str, str]], start: int, end: int) -> str:
        """
        从要摘要的轮次中提取内容。
        
        参数:
            trajectory: 完整轨迹
            start: 起始索引（包含）
            end: 结束索引（不包含）
            
        返回:
            用于摘要的格式化轮次内容字符串
        """
        parts = []
        for i in range(start, end):
            turn = trajectory[i]
            role = turn.get("from", "unknown")
            value = turn.get("value", "")
            
            # 为摘要提示截断非常长的值
            if len(value) > 3000:
                value = value[:1500] + "\n...[truncated]...\n" + value[-500:]
            
            parts.append(f"[Turn {i} - {role.upper()}]:\n{value}")
        
        return "\n\n".join(parts)

    @staticmethod
    def _coerce_summary_content(content: Any) -> str:
        """将摘要模型输出规范化为安全字符串。"""
        if not isinstance(content, str):
            content = str(content) if content else ""
        return content.strip()

    @staticmethod
    def _ensure_summary_prefix(summary: str) -> str:
        """将摘要文本规范化以包含预期前缀恰好一次。"""
        text = (summary or "").strip()
        if text.startswith("[CONTEXT SUMMARY]:"):
            return text
        return "[CONTEXT SUMMARY]:" if not text else f"[CONTEXT SUMMARY]: {text}"
    
    def _generate_summary(self, content: str, metrics: TrajectoryMetrics) -> str:
        """
        使用 OpenRouter 生成压缩轮次的摘要。
        
        参数:
            content: 要摘要的内容
            metrics: 要更新的指标对象
            
        返回:
            摘要字符串
        """
        prompt = f"""请简洁地摘要以下代理对话轮次。此摘要将替换对话历史中的这些轮次。

请从描述助手做了什么和学习到了什么的客观角度撰写摘要。包括：
1. 助手采取了什么操作（工具调用、搜索、文件操作）
2. 获取的关键信息或结果
3. 任何重要的决定或发现
4. 相关数据、文件名、值或输出

请保持摘要的事实性和信息性。目标约为 {self.config.summary_target_tokens} 个 token。

---
要摘要的轮次：
{content}
---

仅撰写摘要，以"[CONTEXT SUMMARY]:"前缀开头。"""

        for attempt in range(self.config.max_retries):
            try:
                metrics.summarization_api_calls += 1
                
                if getattr(self, '_use_call_llm', False):
                    from agent.auxiliary_client import call_llm
                    response = call_llm(
                        provider=self._llm_provider,
                        model=self.config.summarization_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        max_tokens=self.config.summary_target_tokens * 2,
                    )
                else:
                    response = self.client.chat.completions.create(
                        model=self.config.summarization_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        max_tokens=self.config.summary_target_tokens * 2,
                    )
                
                summary = self._coerce_summary_content(response.choices[0].message.content)
                return self._ensure_summary_prefix(summary)
                
            except Exception as e:
                metrics.summarization_errors += 1
                self.logger.warning(f"摘要尝试 {attempt + 1} 失败: {e}")
                
                if attempt < self.config.max_retries - 1:
                    time.sleep(jittered_backoff(attempt + 1, base_delay=self.config.retry_delay, max_delay=30.0))
                else:
                    # 回退：创建基本摘要
                    return "[CONTEXT SUMMARY]: [摘要生成失败 - 先前的轮次包含已被压缩以节省上下文空间的工具调用和响应。]"
    
    async def _generate_summary_async(self, content: str, metrics: TrajectoryMetrics) -> str:
        """
        使用 OpenRouter 生成压缩轮次的摘要（异步版本）。
        
        参数:
            content: 要摘要的内容
            metrics: 要更新的指标对象
            
        返回:
            摘要字符串
        """
        prompt = f"""请简洁地摘要以下代理对话轮次。此摘要将替换对话历史中的这些轮次。

请从描述助手做了什么和学习到了什么的客观角度撰写摘要。包括：
1. 助手采取了什么操作（工具调用、搜索、文件操作）
2. 获取的关键信息或结果
3. 任何重要的决定或发现
4. 相关数据、文件名、值或输出

请保持摘要的事实性和信息性。目标约为 {self.config.summary_target_tokens} 个 token。

---
要摘要的轮次：
{content}
---

仅撰写摘要，以"[CONTEXT SUMMARY]:"前缀开头。"""

        for attempt in range(self.config.max_retries):
            try:
                metrics.summarization_api_calls += 1
                
                if getattr(self, '_use_call_llm', False):
                    from agent.auxiliary_client import async_call_llm
                    response = await async_call_llm(
                        provider=self._llm_provider,
                        model=self.config.summarization_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        max_tokens=self.config.summary_target_tokens * 2,
                    )
                else:
                    response = await self._get_async_client().chat.completions.create(
                        model=self.config.summarization_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        max_tokens=self.config.summary_target_tokens * 2,
                    )
                
                summary = self._coerce_summary_content(response.choices[0].message.content)
                return self._ensure_summary_prefix(summary)
                
            except Exception as e:
                metrics.summarization_errors += 1
                self.logger.warning(f"摘要尝试 {attempt + 1} 失败: {e}")
                
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(jittered_backoff(attempt + 1, base_delay=self.config.retry_delay, max_delay=30.0))
                else:
                    # 回退：创建基本摘要
                    return "[CONTEXT SUMMARY]: [摘要生成失败 - 先前的轮次包含已被压缩以节省上下文空间的工具调用和响应。]"
    
    def compress_trajectory(
        self,
        trajectory: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], TrajectoryMetrics]:
        """
        将单个轨迹压缩到目标 token 预算内。
        
        算法：
        1. 计算总 token 数
        2. 如果低于目标，跳过
        3. 找到可压缩区域（受保护的头部和尾部之间）
        4. 计算需要节省多少 token
        5. 从可压缩区域开始累积轮次直到达到节省目标
        6. 用一条 human 摘要消息替换累积的轮次
        7. 保留剩余的轮次不变
        
        参数:
            trajectory: 对话轮次列表
            
        返回:
            Tuple of (compressed_trajectory, metrics)
        """
        metrics = TrajectoryMetrics()
        metrics.original_turns = len(trajectory)
        
        # 计算每轮的 token 数
        turn_tokens = self.count_turn_tokens(trajectory)
        total_tokens = sum(turn_tokens)
        metrics.original_tokens = total_tokens
        
        # 检查是否需要压缩
        if total_tokens <= self.config.target_max_tokens:
            metrics.skipped_under_target = True
            metrics.compressed_tokens = total_tokens
            metrics.compressed_turns = len(trajectory)
            metrics.compression_ratio = 1.0
            return trajectory, metrics
        
        # 找到受保护区域
        protected, compress_start, compress_end = self._find_protected_indices(trajectory)
        
        # 检查是否有可压缩内容
        if compress_start >= compress_end:
            # 没有可压缩内容，按原样返回
            metrics.compressed_tokens = total_tokens
            metrics.compressed_turns = len(trajectory)
            metrics.still_over_limit = total_tokens > self.config.target_max_tokens
            return trajectory, metrics
        
        # 计算需要节省多少
        tokens_to_save = total_tokens - self.config.target_max_tokens
        
        # 我们将用 1 个摘要轮次替换 N 个轮次
        # 净节省 = (N 个轮次的 token 总和) - summary_target_tokens
        # 我们需要：net_savings >= tokens_to_save
        # 所以：轮次总和 >= tokens_to_save + summary_target_tokens
        target_tokens_to_compress = tokens_to_save + self.config.summary_target_tokens
        
        # 从 compress_start 开始累积轮次直到有足够的节省
        accumulated_tokens = 0
        compress_until = compress_start
        
        for i in range(compress_start, compress_end):
            accumulated_tokens += turn_tokens[i]
            compress_until = i + 1  # 独占结束
            
            # 检查是否有足够的节省
            if accumulated_tokens >= target_tokens_to_compress:
                break
        
        # 如果仍然没有足够的节省，压缩整个可压缩区域
        if accumulated_tokens < target_tokens_to_compress and compress_until < compress_end:
            compress_until = compress_end
            accumulated_tokens = sum(turn_tokens[compress_start:compress_end])
        
        # 记录压缩区域
        metrics.turns_compressed_start_idx = compress_start
        metrics.turns_compressed_end_idx = compress_until
        metrics.turns_in_compressed_region = compress_until - compress_start
        
        # 提取要摘要的内容
        content_to_summarize = self._extract_turn_content_for_summary(
            trajectory, compress_start, compress_until
        )
        
        # 生成摘要
        summary = self._generate_summary(content_to_summarize, metrics)
        
        # 构建压缩后的轨迹
        compressed = []
        
        # 添加头部（压缩区域之前的轮次）
        for i in range(compress_start):
            turn = trajectory[i].copy()
            # 向 system 消息添加通知
            if turn.get("from") == "system" and self.config.add_summary_notice:
                turn["value"] = turn["value"] + self.config.summary_notice_text
            compressed.append(turn)
        
        # 添加摘要作为 human 消息
        compressed.append({
            "from": "human",
            "value": summary
        })
        
        # 添加尾部（压缩区域之后的轮次）
        for i in range(compress_until, len(trajectory)):
            compressed.append(trajectory[i].copy())
        
        # 计算最终指标
        metrics.compressed_turns = len(compressed)
        metrics.compressed_tokens = self.count_trajectory_tokens(compressed)
        metrics.turns_removed = metrics.original_turns - metrics.compressed_turns
        metrics.tokens_saved = metrics.original_tokens - metrics.compressed_tokens
        metrics.compression_ratio = metrics.compressed_tokens / max(metrics.original_tokens, 1)
        metrics.was_compressed = True
        metrics.still_over_limit = metrics.compressed_tokens > self.config.target_max_tokens
        
        return compressed, metrics
    
    async def compress_trajectory_async(
        self,
        trajectory: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], TrajectoryMetrics]:
        """
        将单个轨迹压缩到目标 token 预算内（异步版本）。
        
        与 compress_trajectory 算法相同，但使用异步 API 调用进行摘要。
        """
        metrics = TrajectoryMetrics()
        metrics.original_turns = len(trajectory)
        
        # 计算每轮的 token 数
        turn_tokens = self.count_turn_tokens(trajectory)
        total_tokens = sum(turn_tokens)
        metrics.original_tokens = total_tokens
        
        # 检查是否需要压缩
        if total_tokens <= self.config.target_max_tokens:
            metrics.skipped_under_target = True
            metrics.compressed_tokens = total_tokens
            metrics.compressed_turns = len(trajectory)
            metrics.compression_ratio = 1.0
            return trajectory, metrics
        
        # 找到受保护区域
        protected, compress_start, compress_end = self._find_protected_indices(trajectory)
        
        # 检查是否有可压缩内容
        if compress_start >= compress_end:
            metrics.compressed_tokens = total_tokens
            metrics.compressed_turns = len(trajectory)
            metrics.still_over_limit = total_tokens > self.config.target_max_tokens
            return trajectory, metrics
        
        # 计算需要节省多少
        tokens_to_save = total_tokens - self.config.target_max_tokens
        target_tokens_to_compress = tokens_to_save + self.config.summary_target_tokens
        
        # 从 compress_start 开始累积轮次直到有足够的节省
        accumulated_tokens = 0
        compress_until = compress_start
        
        for i in range(compress_start, compress_end):
            accumulated_tokens += turn_tokens[i]
            compress_until = i + 1
            if accumulated_tokens >= target_tokens_to_compress:
                break
        
        # 如果仍然没有足够的节省，压缩整个可压缩区域
        if accumulated_tokens < target_tokens_to_compress and compress_until < compress_end:
            compress_until = compress_end
            accumulated_tokens = sum(turn_tokens[compress_start:compress_end])
        
        # 记录压缩区域
        metrics.turns_compressed_start_idx = compress_start
        metrics.turns_compressed_end_idx = compress_until
        metrics.turns_in_compressed_region = compress_until - compress_start
        
        # 提取要摘要的内容
        content_to_summarize = self._extract_turn_content_for_summary(
            trajectory, compress_start, compress_until
        )
        
        # 生成摘要（异步）
        summary = await self._generate_summary_async(content_to_summarize, metrics)
        
        # 构建压缩后的轨迹
        compressed = []
        
        # 添加头部（压缩区域之前的轮次）
        for i in range(compress_start):
            turn = trajectory[i].copy()
            if turn.get("from") == "system" and self.config.add_summary_notice:
                turn["value"] = turn["value"] + self.config.summary_notice_text
            compressed.append(turn)
        
        # 添加摘要作为 human 消息
        compressed.append({
            "from": "human",
            "value": summary
        })
        
        # 添加尾部（压缩区域之后的轮次）
        for i in range(compress_until, len(trajectory)):
            compressed.append(trajectory[i].copy())
        
        # 计算最终指标
        metrics.compressed_turns = len(compressed)
        metrics.compressed_tokens = self.count_trajectory_tokens(compressed)
        metrics.turns_removed = metrics.original_turns - metrics.compressed_turns
        metrics.tokens_saved = metrics.original_tokens - metrics.compressed_tokens
        metrics.compression_ratio = metrics.compressed_tokens / max(metrics.original_tokens, 1)
        metrics.was_compressed = True
        metrics.still_over_limit = metrics.compressed_tokens > self.config.target_max_tokens
        
        return compressed, metrics
    
    async def process_entry_async(self, entry: Dict[str, Any]) -> Tuple[Dict[str, Any], TrajectoryMetrics]:
        """
        处理单个 JSONL 条目（异步版本）。
        """
        if "conversations" not in entry:
            metrics = TrajectoryMetrics()
            return entry, metrics
        
        trajectory = entry["conversations"]
        compressed_trajectory, metrics = await self.compress_trajectory_async(trajectory)
        
        # 创建带有压缩轨迹的新条目
        result = entry.copy()
        result["conversations"] = compressed_trajectory
        
        # 如果启用，添加压缩元数据
        if self.config.metrics_per_trajectory and metrics.was_compressed:
            result["compression_metrics"] = metrics.to_dict()
        
        return result, metrics
    
    def process_entry(self, entry: Dict[str, Any]) -> Tuple[Dict[str, Any], TrajectoryMetrics]:
        """
        处理单个 JSONL 条目。
        
        参数:
            entry: 包含 'conversations' 字段的 JSONL 条目
            
        返回:
            Tuple of (processed_entry, metrics)
        """
        if "conversations" not in entry:
            metrics = TrajectoryMetrics()
            return entry, metrics
        
        trajectory = entry["conversations"]
        compressed_trajectory, metrics = self.compress_trajectory(trajectory)
        
        # 创建带有压缩轨迹的新条目
        result = entry.copy()
        result["conversations"] = compressed_trajectory
        
        # 如果启用，添加压缩元数据
        if self.config.metrics_per_trajectory and metrics.was_compressed:
            result["compression_metrics"] = metrics.to_dict()
        
        return result, metrics
    
    def process_file(
        self, 
        input_path: Path, 
        output_path: Path,
        progress_callback: Optional[Callable[[TrajectoryMetrics], None]] = None
    ) -> List[TrajectoryMetrics]:
        """
        处理单个 JSONL 文件。
        
        参数:
            input_path: 输入 JSONL 文件路径
            output_path: 输出 JSONL 文件路径
            progress_callback: 每个条目处理后使用其指标调用的可选回调
            
        返回:
            每条轨迹的指标列表
        """
        file_metrics = []
        
        # 读取所有条目
        entries = []
        with open(input_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        self.logger.warning(f"跳过 {input_path}:{line_num} 处的无效 JSON: {e}")
        
        # 处理条目
        processed_entries = []
        for entry in entries:
            try:
                processed_entry, metrics = self.process_entry(entry)
                processed_entries.append(processed_entry)
                file_metrics.append(metrics)
                self.aggregate_metrics.add_trajectory_metrics(metrics)
                
                # 如果提供了回调则调用
                if progress_callback:
                    progress_callback(metrics)
                
            except Exception as e:
                self.logger.error(f"处理条目时出错: {e}")
                self.aggregate_metrics.trajectories_failed += 1
                # 出错时保留原始条目
                processed_entries.append(entry)
                empty_metrics = TrajectoryMetrics()
                file_metrics.append(empty_metrics)
                
                if progress_callback:
                    progress_callback(empty_metrics)
        
        # 写入输出
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            for entry in processed_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        
        return file_metrics
    
    def process_directory(self, input_dir: Path, output_dir: Path):
        """
        使用异步并行处理压缩目录中的所有 JSONL 文件。
        
        参数:
            input_dir: 包含 JSONL 文件的输入目录
            output_dir: 压缩文件的输出目录
        """
        # 运行异步版本
        asyncio.run(self._process_directory_async(input_dir, output_dir))
    
    async def _process_directory_async(self, input_dir: Path, output_dir: Path):
        """
        使用并行 API 调用的异步目录处理实现。
        """
        console = Console()
        
        # 记录开始时间
        self.aggregate_metrics.processing_start_time = datetime.now().isoformat()
        start_time = time.time()
        
        # 查找所有 JSONL 文件
        jsonl_files = sorted(input_dir.glob("*.jsonl"))
        
        if not jsonl_files:
            self.logger.warning(f"在 {input_dir} 中未找到 JSONL 文件")
            return
        
        # 从所有文件加载所有条目
        console.print("\n[dim]正在加载所有条目...[/dim]")
        all_entries = []  # (file_path, entry_idx, entry) 列表
        
        for file_path in jsonl_files:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f):
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            all_entries.append((file_path, line_num, entry))
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"跳过 {file_path}:{line_num} 处的无效 JSON: {e}")
        
        total_entries = len(all_entries)
        
        console.print(f"\n{'='*60}")
        console.print(f"📂 输入: {input_dir}")
        console.print(f"📂 输出: {output_dir}")
        console.print(f"📄 待处理文件: {len(jsonl_files)}")
        console.print(f"📊 总轨迹数: {total_entries:,}")
        console.print(f"🎯 目标最大 token 数: {self.config.target_max_tokens:,}")
        console.print(f"📝 摘要目标 token 数: {self.config.summary_target_tokens}")
        console.print(f"⚡ 最大并发 API 调用数: {self.config.max_concurrent_requests}")
        console.print(f"{'='*60}\n")
        
        # 创建用于速率限制的信号量
        semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
        
        # 进度显示跟踪（带锁的线程安全）
        progress_lock = asyncio.Lock()
        compressed_count = 0
        skipped_count = 0
        api_calls = 0
        in_flight = 0
        
        # 结果存储: {file_path: {entry_idx: (processed_entry, metrics)}}
        results = {f: {} for f in jsonl_files}
        
        # 单独跟踪超时
        timeout_count = 0
        
        async def process_single(file_path: Path, entry_idx: int, entry: Dict, 
                                  progress, main_task, status_task):
            """使用信号量速率限制和超时处理单个条目。"""
            nonlocal compressed_count, skipped_count, api_calls, in_flight, timeout_count
            
            async with semaphore:
                # 跟踪进行中的任务
                async with progress_lock:
                    in_flight += 1
                
                try:
                    # 应用每条轨迹的超时
                    processed_entry, metrics = await asyncio.wait_for(
                        self.process_entry_async(entry),
                        timeout=self.config.per_trajectory_timeout
                    )
                    results[file_path][entry_idx] = (processed_entry, metrics)
                    
                    # 更新聚合指标（带锁以保证线程安全）
                    async with progress_lock:
                        self.aggregate_metrics.add_trajectory_metrics(metrics)
                        
                        # 更新计数器
                        if metrics.was_compressed:
                            compressed_count += 1
                            api_calls += metrics.summarization_api_calls
                        if metrics.skipped_under_target:
                            skipped_count += 1
                        
                        in_flight -= 1
                        
                        # 更新进度
                        progress.advance(main_task)
                        progress.update(
                            status_task,
                            description=f"[dim]✅ {compressed_count} 已压缩 | ⏭️ {skipped_count} 已跳过 | ⏱️ {timeout_count} 超时 | 🔄 {api_calls} API 调用 | ⚡ {in_flight} 进行中[/dim]"
                        )
                
                except asyncio.TimeoutError:
                    self.logger.warning(f"处理 {file_path}:{entry_idx} 超时（>{self.config.per_trajectory_timeout}秒）")
                    
                    async with progress_lock:
                        self.aggregate_metrics.trajectories_failed += 1
                        timeout_count += 1
                        in_flight -= 1
                        progress.advance(main_task)
                        progress.update(
                            status_task,
                            description=f"[dim]✅ {compressed_count} 已压缩 | ⏭️ {skipped_count} 已跳过 | ⏱️ {timeout_count} 超时 | 🔄 {api_calls} API 调用 | ⚡ {in_flight} 进行中[/dim]"
                        )
                    
                    # 完全跳过此条目（不包含在输出中）
                    results[file_path][entry_idx] = None
                    
                except Exception as e:
                    self.logger.error(f"处理 {file_path}:{entry_idx} 时出错: {e}")
                    
                    async with progress_lock:
                        self.aggregate_metrics.trajectories_failed += 1
                        in_flight -= 1
                        progress.advance(main_task)
                    
                    # 出错时保留原始条目
                    results[file_path][entry_idx] = (entry, TrajectoryMetrics())
        
        # 创建进度条
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=10  # 异步时更高的刷新率
        ) as progress:
            # 主要任务用于整体进度
            main_task = progress.add_task(
                f"[cyan]正在压缩 {total_entries:,} 条轨迹",
                total=total_entries
            )
            
            # 状态行任务
            status_task = progress.add_task(
                "[dim]正在启动...[/dim]",
                total=None
            )
            
            # 创建所有任务
            tasks = [
                process_single(file_path, entry_idx, entry, progress, main_task, status_task)
                for file_path, entry_idx, entry in all_entries
            ]
            
            # 并发运行所有任务（信号量限制实际并发数）
            await asyncio.gather(*tasks)
            
            # 移除状态任务
            progress.remove_task(status_task)
        
        # 写入结果到输出文件（保持原始顺序）
        console.print("\n[dim]正在写入输出文件...[/dim]")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        for file_path in jsonl_files:
            output_path = output_dir / file_path.name
            file_results = results[file_path]
            
            # 按原始条目索引排序以保持顺序，跳过 None（超时）条目
            sorted_entries = [
                file_results[idx][0] 
                for idx in sorted(file_results.keys()) 
                if file_results[idx] is not None
            ]
            
            with open(output_path, 'w', encoding='utf-8') as f:
                for entry in sorted_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        
        # 记录结束时间
        self.aggregate_metrics.processing_end_time = datetime.now().isoformat()
        self.aggregate_metrics.processing_duration_seconds = time.time() - start_time
        
        # 打印摘要
        self._print_summary()
        
        # 保存指标
        if self.config.metrics_enabled:
            metrics_path = output_dir / self.config.metrics_output_file
            with open(metrics_path, 'w') as f:
                json.dump(self.aggregate_metrics.to_dict(), f, indent=2)
            console.print(f"\n💾 指标已保存到 {metrics_path}")
    
    def _print_summary(self):
        """打印全面的压缩摘要统计信息。"""
        m = self.aggregate_metrics.to_dict()
        
        # 计算一些额外的统计信息
        total = m['summary']['total_trajectories']
        compressed = m['summary']['trajectories_compressed']
        skipped = m['summary']['trajectories_skipped_under_target']
        over_limit = m['summary']['trajectories_still_over_limit']
        failed = m['summary']['trajectories_failed']
        
        # Token 统计
        tokens_before = m['tokens']['total_before']
        tokens_after = m['tokens']['total_after']
        tokens_saved = m['tokens']['total_saved']
        
        # 计算百分比
        compressed_pct = (compressed / max(total, 1)) * 100
        skipped_pct = (skipped / max(total, 1)) * 100
        over_limit_pct = (over_limit / max(total, 1)) * 100
        
        print(f"\n")
        print(f"╔{'═'*70}╗")
        print(f"║{'轨迹压缩报告':^70}║")
        print(f"╠{'═'*70}╣")
        
        # 轨迹部分
        print(f"║{'':2}📁 轨迹{' '*54}║")
        print(f"║{'─'*70}║")
        print(f"║{'':4}总处理数:           {total:>10,}{' '*32}║")
        print(f"║{'':4}├─ 已压缩:         {compressed:>10,}  ({compressed_pct:>5.1f}%){' '*18}║")
        print(f"║{'':4}├─ 已跳过（低于限制）:{skipped:>9,}  ({skipped_pct:>5.1f}%){' '*18}║")
        print(f"║{'':4}├─ 仍超限制:       {over_limit:>10,}  ({over_limit_pct:>5.1f}%){' '*18}║")
        print(f"║{'':4}└─ 失败:           {failed:>10,}{' '*32}║")
        
        print(f"╠{'═'*70}╣")
        
        # Token 部分
        print(f"║{'':2}🔢 Token{' '*60}║")
        print(f"║{'─'*70}║")
        print(f"║{'':4}压缩前:            {tokens_before:>15,} tokens{' '*21}║")
        print(f"║{'':4}压缩后:            {tokens_after:>15,} tokens{' '*21}║")
        print(f"║{'':4}总节省:            {tokens_saved:>15,} tokens{' '*21}║")
        print(f"║{'':4}总体压缩率:        {m['tokens']['overall_compression_ratio']:>14.1%}{' '*28}║")
        
        if tokens_before > 0:
            savings_pct = (tokens_saved / tokens_before) * 100
            print(f"║{'':4}节省空间:          {savings_pct:>14.1f}%{' '*28}║")
        
        print(f"╠{'═'*70}╣")
        
        # 轮次部分
        print(f"║{'':2}💬 对话轮次{' '*48}║")
        print(f"║{'─'*70}║")
        print(f"║{'':4}压缩前:            {m['turns']['total_before']:>15,} 轮{' '*22}║")
        print(f"║{'':4}压缩后:            {m['turns']['total_after']:>15,} 轮{' '*22}║")
        print(f"║{'':4}总移除:            {m['turns']['total_removed']:>15,} 轮{' '*22}║")
        
        print(f"╠{'═'*70}╣")
        
        # 平均值部分（仅针对已压缩的轨迹）
        print(f"║{'':2}📈 平均值（仅已压缩轨迹）{' '*37}║")
        print(f"║{'─'*70}║")
        if compressed > 0:
            print(f"║{'':4}平均压缩率:         {m['averages']['avg_compression_ratio']:>14.1%}{' '*28}║")
            print(f"║{'':4}平均节省 Token:    {m['averages']['avg_tokens_saved_per_compressed']:>14,.0f}{' '*28}║")
            print(f"║{'':4}平均移除轮次:      {m['averages']['avg_turns_removed_per_compressed']:>14.1f}{' '*28}║")
        else:
            print(f"║{'':4}没有轨迹被压缩{' '*38}║")
        
        print(f"╠{'═'*70}╣")
        
        # 摘要 API 部分
        print(f"║{'':2}🤖 摘要 API{' '*49}║")
        print(f"║{'─'*70}║")
        print(f"║{'':4}API 调用次数:        {m['summarization']['total_api_calls']:>15,}{' '*27}║")
        print(f"║{'':4}错误数:             {m['summarization']['total_errors']:>15,}{' '*27}║")
        print(f"║{'':4}成功率:            {m['summarization']['success_rate']:>14.1%}{' '*28}║")
        
        print(f"╠{'═'*70}╣")
        
        # 处理时间部分
        duration = m['processing']['duration_seconds']
        if duration > 60:
            time_str = f"{duration/60:.1f} 分钟"
        else:
            time_str = f"{duration:.1f} 秒"
        
        throughput = total / max(duration, 0.001)
        
        print(f"║{'':2}⏱️  处理时间{' '*51}║")
        print(f"║{'─'*70}║")
        print(f"║{'':4}耗时:               {time_str:>20}{' '*22}║")
        print(f"║{'':4}吞吐量:             {throughput:>15.1f} 轨迹/秒{' '*18}║")
        print(f"║{'':4}开始时间:           {m['processing']['start_time'][:19]:>20}{' '*22}║")
        print(f"║{'':4}结束时间:           {m['processing']['end_time'][:19]:>20}{' '*22}║")
        
        print(f"╚{'═'*70}╝")
        
        # 如果有数据则打印分布摘要
        if self.aggregate_metrics.compression_ratios:
            ratios = self.aggregate_metrics.compression_ratios
            tokens_saved_list = self.aggregate_metrics.tokens_saved_list
            
            print(f"\n📊 分布摘要：")
            print(f"   压缩率: 最小={min(ratios):.2%}, 最大={max(ratios):.2%}, 中位数={sorted(ratios)[len(ratios)//2]:.2%}")
            print(f"   节省 Token: 最小={min(tokens_saved_list):,}, 最大={max(tokens_saved_list):,}, 中位数={sorted(tokens_saved_list)[len(tokens_saved_list)//2]:,}")


def main(
    input: str,
    output: str = None,
    config: str = "configs/trajectory_compression.yaml",
    target_max_tokens: int = None,
    tokenizer: str = None,
    sample_percent: float = None,
    seed: int = 42,
    dry_run: bool = False,
):
    """
    将代理轨迹压缩到目标 token 预算内。
    
    支持单个 JSONL 文件和包含多个 JSONL 文件的目录。
    可选择在压缩前对轨迹进行百分比采样。
    
    参数:
        input: JSONL 文件路径或包含 JSONL 文件的目录路径
        output: 输出路径（文件输入为文件，目录输入为目录）
                默认值：在输入名称后添加 "_compressed" 后缀
        config: YAML 配置文件路径
        target_max_tokens: 从配置覆盖目标 token 数
        tokenizer: 从配置覆盖分词器名称
        sample_percent: 压缩前对此百分比的轨迹进行采样（1-100）
        seed: 用于采样可重现性的随机种子（默认：42）
        dry_run: 分析而不压缩（仅显示将要发生什么）
    
    示例:
        # 压缩目录（原行为）
        python trajectory_compressor.py --input=data/my_run
        
        # 压缩单个文件
        python trajectory_compressor.py --input=data/trajectories.jsonl
        
        # 压缩文件的 15% 采样
        python trajectory_compressor.py --input=data/trajectories.jsonl --sample_percent=15
        
        # 带自定义输出的 10% 采样压缩
        python trajectory_compressor.py --input=data/trajectories.jsonl --sample_percent=10 --output=data/sampled_compressed.jsonl
    """
    import random
    import tempfile
    import shutil
    
    print("🗜️  轨迹压缩器")
    print("=" * 60)
    
    # 加载配置
    config_path = Path(config)
    if config_path.exists():
        print(f"📋 从 {config} 加载配置")
        compression_config = CompressionConfig.from_yaml(config)
    else:
        print(f"⚠️  配置未找到于 {config}，使用默认值")
        compression_config = CompressionConfig()
    
    # 应用 CLI 覆盖
    if target_max_tokens:
        compression_config.target_max_tokens = target_max_tokens
    if tokenizer:
        compression_config.tokenizer_name = tokenizer
    
    # 验证 sample_percent
    if sample_percent is not None:
        if sample_percent <= 0 or sample_percent > 100:
            print(f"❌ sample_percent 必须介于 1 和 100 之间，得到 {sample_percent}")
            return
        print(f"🎲 将采样 {sample_percent}% 的轨迹（seed={seed}）")
    
    # 设置路径并确定输入类型
    input_path = Path(input)
    if not input_path.exists():
        print(f"❌ 输入未找到: {input}")
        return
    
    is_file_input = input_path.is_file()
    
    if is_file_input:
        print(f"📄 输入模式：单个 JSONL 文件")
        
        # 对于文件输入，默认输出是带 _compressed 后缀的文件
        if output:
            output_path = Path(output)
        else:
            output_path = input_path.parent / (input_path.stem + compression_config.output_suffix + ".jsonl")
        
        # 从单个文件加载条目
        entries = []
        with open(input_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        print(f"⚠️  跳过第 {line_num} 行的无效 JSON: {e}")
        
        total_entries = len(entries)
        print(f"   从 {input_path.name} 加载了 {total_entries:,} 条轨迹")
        
        # 如果请求则采样
        if sample_percent is not None:
            random.seed(seed)
            sample_size = max(1, int(total_entries * sample_percent / 100))
            entries = random.sample(entries, sample_size)
            print(f"   采样了 {len(entries):,} 条轨迹（{total_entries:,} 中的 {sample_percent}%）")
        
        if dry_run:
            print(f"\n🔍 试运行模式 - 分析但不写入")
            print(f"📄 将处理: {len(entries):,} 条轨迹")
            print(f"📄 将输出到: {output_path}")
            return
        
        # 创建临时目录用于处理
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_input_dir = Path(temp_dir) / "input"
            temp_output_dir = Path(temp_dir) / "output"
            temp_input_dir.mkdir()
            
            # 将条目写入临时文件
            temp_input_file = temp_input_dir / "trajectories.jsonl"
            with open(temp_input_file, 'w', encoding='utf-8') as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            
            # 初始化压缩器并处理
            compressor = TrajectoryCompressor(compression_config)
            compressor.process_directory(temp_input_dir, temp_output_dir)
            
            # 将结果复制到输出路径（合并 temp_output_dir 中的所有文件）
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as out_f:
                for jsonl_file in sorted(temp_output_dir.glob("*.jsonl")):
                    with open(jsonl_file, 'r', encoding='utf-8') as in_f:
                        for line in in_f:
                            out_f.write(line)
            
            # 如果指标文件存在则复制
            metrics_file = temp_output_dir / compression_config.metrics_output_file
            if metrics_file.exists():
                metrics_output = output_path.parent / (output_path.stem + "_metrics.json")
                shutil.copy(metrics_file, metrics_output)
                print(f"💾 指标已保存到 {metrics_output}")
        
        print(f"\n✅ 压缩完成！")
        print(f"📄 输出: {output_path}")
        
    else:
        # 目录输入 - 原行为
        print(f"📁 输入模式：JSONL 文件目录")
        
        if output:
            output_path = Path(output)
        else:
            output_path = input_path.parent / (input_path.name + compression_config.output_suffix)
        
        # 如果为目录模式请求采样，我们需要不同地处理
        if sample_percent is not None:
            print(f"\n⚠️  从目录采样：将每个文件的 {sample_percent}% 进行采样")
            
            # 创建带有采样文件的临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_input_dir = Path(temp_dir) / "input"
                temp_input_dir.mkdir()
                
                random.seed(seed)
                total_original = 0
                total_sampled = 0
                
                # 从每个 JSONL 文件采样
                for jsonl_file in sorted(input_path.glob("*.jsonl")):
                    entries = []
                    with open(jsonl_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    entries.append(json.loads(line))
                                except json.JSONDecodeError:
                                    pass
                    
                    total_original += len(entries)
                    sample_size = max(1, int(len(entries) * sample_percent / 100))
                    sampled_entries = random.sample(entries, min(sample_size, len(entries)))
                    total_sampled += len(sampled_entries)
                    
                    # 写入采样条目
                    temp_file = temp_input_dir / jsonl_file.name
                    with open(temp_file, 'w', encoding='utf-8') as f:
                        for entry in sampled_entries:
                            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                
                print(f"   从 {total_original:,} 条轨迹中采样了 {total_sampled:,} 条")
                
                if dry_run:
                    print(f"\n🔍 试运行模式 - 分析但不写入")
                    print(f"📁 将处理: {temp_input_dir}")
                    print(f"📁 将输出到: {output_path}")
                    return
                
                # 初始化压缩器并处理采样数据
                compressor = TrajectoryCompressor(compression_config)
                compressor.process_directory(temp_input_dir, output_path)
        else:
            if dry_run:
                print(f"\n🔍 试运行模式 - 分析但不写入")
                print(f"📁 将处理: {input_path}")
                print(f"📁 将输出到: {output_path}")
                return
            
            # 直接初始化压缩器并处理
            compressor = TrajectoryCompressor(compression_config)
            compressor.process_directory(input_path, output_path)
        
        print("\n✅ 压缩完成！")


if __name__ == "__main__":
    fire.Fire(main)
