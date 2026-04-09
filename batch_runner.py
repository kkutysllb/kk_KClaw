#!/usr/bin/env python3
"""
批量 Agent 运行器

本模块提供并行批量处理能力，用于对数据集中的多个 prompt 运行 agent。包括：
- 数据集加载与分批
- 基于多进程的并行批量处理
- 断点续传与容错恢复
- 按正确格式保存轨迹（from/value 对）
- 跨批次的工具使用统计聚合

用法：
    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run
    
    # 从中断处恢复运行
    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run --resume
    
    # 使用特定的工具集分布
    python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run --distribution=image_gen
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from multiprocessing import Pool, Lock
import traceback
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, MofNCompleteColumn
from rich.console import Console

logger = logging.getLogger(__name__)
import fire

from run_agent import AIAgent
from toolset_distributions import (
    list_distributions, 
    sample_toolsets_from_distribution,
    validate_distribution
)
from model_tools import TOOL_TO_TOOLSET_MAP


# 全局配置，用于 worker 进程
_WORKER_CONFIG = {}

# 所有可能的工具 - 从 model_tools.py 的主映射表自动派生
# 当新工具添加到 TOOL_TO_TOOLSET_MAP 时会自动保持同步
# 用于 Arrow/Parquet（HuggingFace 数据集）的统一 schema，
# 以及在轨迹合并时过滤损坏的条目
ALL_POSSIBLE_TOOLS = set(TOOL_TO_TOOLSET_MAP.keys())

# 未使用工具的默认统计
DEFAULT_TOOL_STATS = {'count': 0, 'success': 0, 'failure': 0}


def _normalize_tool_stats(tool_stats: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    """
    标准化 tool_stats，确保包含所有可能的工具且 schema 一致。
    
    这确保 HuggingFace 数据集可以加载 JSONL 而不会出现 schema 不匹配错误。
    未使用的工具获得零计数。
    
    参数:
        tool_stats (Dict): 从提取得到的原始工具统计
        
    返回:
        Dict: 包含所有工具的标准化工具统计
    """
    normalized = {}
    
    # 添加所有可能工具的默认值
    for tool in ALL_POSSIBLE_TOOLS:
        if tool in tool_stats:
            normalized[tool] = tool_stats[tool].copy()
        else:
            normalized[tool] = DEFAULT_TOOL_STATS.copy()
    
    # 也包含意外的工具（以防添加了新工具）
    for tool, stats in tool_stats.items():
        if tool not in normalized:
            normalized[tool] = stats.copy()
    
    return normalized


def _normalize_tool_error_counts(tool_error_counts: Dict[str, int]) -> Dict[str, int]:
    """
    标准化 tool_error_counts，包含所有可能的工具。
    
    参数:
        tool_error_counts (Dict): 原始错误计数映射
        
    返回:
        Dict: 包含所有工具的标准化错误计数
    """
    normalized = {}
    
    # 添加所有可能工具的零默认值
    for tool in ALL_POSSIBLE_TOOLS:
        normalized[tool] = tool_error_counts.get(tool, 0)
    
    # 也包含意外的工具
    for tool, count in tool_error_counts.items():
        if tool not in normalized:
            normalized[tool] = count
    
    return normalized


def _extract_tool_stats(messages: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """
    从消息历史中提取工具使用统计。
    
    参数:
        messages (List[Dict]): 消息历史
        
    返回:
        Dict: 包含计数和成功/失败率的工具统计
    """
    tool_stats = {}
    
    # 跟踪工具调用及其结果
    tool_calls_map = {}  # 将 tool_call_id 映射到工具名
    
    for msg in messages:
        # 从 assistant 消息中跟踪工具调用
        if msg["role"] == "assistant" and "tool_calls" in msg and msg["tool_calls"]:
            for tool_call in msg["tool_calls"]:
                if not tool_call or not isinstance(tool_call, dict): continue
                tool_name = tool_call["function"]["name"]
                tool_call_id = tool_call["id"]
                
                # 如果工具不存在则初始化统计
                if tool_name not in tool_stats:
                    tool_stats[tool_name] = {
                        "count": 0,
                        "success": 0,
                        "failure": 0
                    }
                
                tool_stats[tool_name]["count"] += 1
                tool_calls_map[tool_call_id] = tool_name
        
        # 跟踪工具响应
        elif msg["role"] == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            content = msg.get("content", "")
            
            # 判断工具调用是否成功
            is_success = True
            try:
                # 尝试解析为 JSON 并检查实际错误值
                content_json = json.loads(content) if isinstance(content, str) else content
                
                if isinstance(content_json, dict):
                    # 检查 error 字段是否存在且值不为空
                    if "error" in content_json and content_json["error"] is not None:
                        is_success = False
                    
                    # 特殊处理 terminal 工具响应
                    # Terminal 将其响应包装在 "content" 字段中
                    if "content" in content_json and isinstance(content_json["content"], dict):
                        inner_content = content_json["content"]
                        # 检查实际错误（非空 error 字段）
                        # 注意：非零退出码不是失败 - 模型可以自我修正
                        if inner_content.get("error") is not None:
                            is_success = False
                    
                    # 检查某些工具使用的 "success": false 模式
                    if content_json.get("success") is False:
                        is_success = False
                        
            except (json.JSONDecodeError, ValueError, TypeError):
                # 如果不是 JSON，检查内容是否为空或明确表示错误
                # 注意：避免简单子字符串匹配以防止误报
                if not content:
                    is_success = False
                # 仅当明确以 "Error:" 或 "ERROR:" 开头时才标记为失败
                elif content.strip().lower().startswith("error:"):
                    is_success = False
            
            # 更新成功/失败计数
            if tool_call_id in tool_calls_map:
                tool_name = tool_calls_map[tool_call_id]
                if is_success:
                    tool_stats[tool_name]["success"] += 1
                else:
                    tool_stats[tool_name]["failure"] += 1
    
    return tool_stats


def _extract_reasoning_stats(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    统计有多少 assistant 轮次有推理 vs 没有推理。
    
    检查内容中的 <REASONING_SCRATCHPAD> 或非空的 'reasoning' 字段
    （原生思考 token）。返回用于跟踪推理覆盖率的计数。
    
    参数:
        messages: 消息历史
        
    返回:
        包含 'total_assistant_turns', 'turns_with_reasoning', 'turns_without_reasoning' 的字典
    """
    total = 0
    with_reasoning = 0
    
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        total += 1
        
        content = msg.get("content", "") or ""
        has_scratchpad = "<REASONING_SCRATCHPAD>" in content
        has_native_reasoning = bool(msg.get("reasoning", "").strip()) if msg.get("reasoning") else False
        
        if has_scratchpad or has_native_reasoning:
            with_reasoning += 1
    
    return {
        "total_assistant_turns": total,
        "turns_with_reasoning": with_reasoning,
        "turns_without_reasoning": total - with_reasoning,
        "has_any_reasoning": with_reasoning > 0,
    }


def _process_single_prompt(
    prompt_index: int,
    prompt_data: Dict[str, Any],
    batch_num: int,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    使用 agent 处理单个 prompt。
    
    参数:
        prompt_index (int): 数据集中的 prompt 索引
        prompt_data (Dict): 包含 'prompt' 字段和可选 'image' 字段的 prompt 数据
        batch_num (int): 批次编号
        config (Dict): 包含 agent 参数的配置字典
        
    返回:
        Dict: 包含轨迹、统计和元数据的结果
    """
    prompt = prompt_data["prompt"]
    task_id = f"task_{prompt_index}"
    
    # 每个 prompt 的容器镜像覆盖：如果数据集行有 'image' 字段，
    # 为这个任务的沙箱注册它。适用于 Docker、Modal、Singularity 和 Daytona。
    container_image = prompt_data.get("image") or prompt_data.get("docker_image")
    if container_image:
        # 在 agent 循环消耗 token 之前验证镜像是否可访问。
        # 对于 Docker：检查本地缓存，然后尝试拉取。
        # 对于 Modal：跳过本地检查（Modal 在服务端拉取）。
        env_type = os.getenv("TERMINAL_ENV", "local")
        if env_type == "docker":
            import subprocess as _sp
            try:
                probe = _sp.run(
                    ["docker", "image", "inspect", container_image],
                    capture_output=True, timeout=10,
                )
                if probe.returncode != 0:
                    if config.get("verbose"):
                        print(f"   Prompt {prompt_index}: Pulling docker image {container_image}...", flush=True)
                    pull = _sp.run(
                        ["docker", "pull", container_image],
                        capture_output=True, text=True, timeout=600,
                    )
                    if pull.returncode != 0:
                        return {
                            "success": False,
                            "prompt_index": prompt_index,
                            "error": f"Docker image not available: {container_image}\n{pull.stderr[:500]}",
                            "trajectory": None,
                            "tool_stats": {},
                            "toolsets_used": [],
                            "metadata": {"batch_num": batch_num, "timestamp": datetime.now().isoformat()},
                        }
            except FileNotFoundError:
                pass  # Docker CLI 未安装 — 跳过检查（例如 Modal 后端）
            except Exception as img_err:
                if config.get("verbose"):
                    print(f"   Prompt {prompt_index}: Docker image check failed: {img_err}", flush=True)

        from tools.terminal_tool import register_task_env_overrides
        overrides = {
            "docker_image": container_image,
            "modal_image": container_image,
            "singularity_image": f"docker://{container_image}",
            "daytona_image": container_image,
        }
        if prompt_data.get("cwd"):
            overrides["cwd"] = prompt_data["cwd"]
        register_task_env_overrides(task_id, overrides)
        if config.get("verbose"):
            print(f"   Prompt {prompt_index}: Using container image {container_image}")
    
    try:
        # 从分布中为此 prompt 采样工具集
        selected_toolsets = sample_toolsets_from_distribution(config["distribution"])
        
        if config.get("verbose"):
            print(f"   Prompt {prompt_index}: Using toolsets {selected_toolsets}")
        
        # 使用采样的工具集和日志前缀初始化 agent
        log_prefix = f"[B{batch_num}:P{prompt_index}]"
        agent = AIAgent(
            base_url=config.get("base_url"),
            api_key=config.get("api_key"),
            model=config["model"],
            max_iterations=config["max_iterations"],
            enabled_toolsets=selected_toolsets,
            save_trajectories=False,  # 我们自己处理保存
            verbose_logging=config.get("verbose", False),
            ephemeral_system_prompt=config.get("ephemeral_system_prompt"),
            log_prefix_chars=config.get("log_prefix_chars", 100),
            log_prefix=log_prefix,
            providers_allowed=config.get("providers_allowed"),
            providers_ignored=config.get("providers_ignored"),
            providers_order=config.get("providers_order"),
            provider_sort=config.get("provider_sort"),
            max_tokens=config.get("max_tokens"),
            reasoning_config=config.get("reasoning_config"),
            prefill_messages=config.get("prefill_messages"),
            skip_context_files=True,  # 不要用 SOUL.md/AGENTS.md 污染轨迹
            skip_memory=True,  # 不要在批量运行中使用持久化记忆
        )

        # 使用 task_id 运行 agent 以确保每个任务获得自己的隔离 VM
        result = agent.run_conversation(prompt, task_id=task_id)
        
        # 提取工具使用统计
        tool_stats = _extract_tool_stats(result["messages"])
        
        # 提取推理覆盖率统计
        reasoning_stats = _extract_reasoning_stats(result["messages"])
        
        # 转换为轨迹格式（使用现有方法）
        trajectory = agent._convert_to_trajectory_format(
            result["messages"],
            prompt,
            result["completed"]
        )
        
        return {
            "success": True,
            "prompt_index": prompt_index,
            "trajectory": trajectory,
            "tool_stats": tool_stats,
            "reasoning_stats": reasoning_stats,
            "completed": result["completed"],
            "partial": result.get("partial", False),
            "api_calls": result["api_calls"],
            "toolsets_used": selected_toolsets,
            "metadata": {
                "batch_num": batch_num,
                "timestamp": datetime.now().isoformat(),
                "model": config["model"]
            }
        }
    
    except Exception as e:
        print(f"❌ Error processing prompt {prompt_index}: {e}")
        if config.get("verbose"):
            traceback.print_exc()
        
        return {
            "success": False,
            "prompt_index": prompt_index,
            "error": str(e),
            "trajectory": None,
            "tool_stats": {},
            "toolsets_used": [],
            "metadata": {
                "batch_num": batch_num,
                "timestamp": datetime.now().isoformat()
            }
        }


def _process_batch_worker(args: Tuple) -> Dict[str, Any]:
    """
    Worker 函数，处理单个批次的 prompts。
    
    参数:
        args (Tuple): (batch_num, batch_data, output_dir, completed_prompts, config)
        
    返回:
        Dict: 带统计的批次结果
    """
    batch_num, batch_data, output_dir, completed_prompts_set, config = args
    
    output_dir = Path(output_dir)
    print(f"\n🔄 Batch {batch_num}: Starting ({len(batch_data)} prompts)")
    
    # 此批次的输出文件
    batch_output_file = output_dir / f"batch_{batch_num}.jsonl"
    
    # 过滤掉已完成的 prompts
    prompts_to_process = [
        (idx, data) for idx, data in batch_data
        if idx not in completed_prompts_set
    ]
    
    if not prompts_to_process:
        print(f"✅ Batch {batch_num}: Already completed (skipping)")
        return {
            "batch_num": batch_num,
            "processed": 0,
            "skipped": len(batch_data),
            "tool_stats": {},
            "completed_prompts": []
        }
    
    print(f"   Processing {len(prompts_to_process)} prompts (skipping {len(batch_data) - len(prompts_to_process)} already completed)")
        
    # 初始化此批次的聚合统计
    batch_tool_stats = {}
    batch_reasoning_stats = {"total_assistant_turns": 0, "turns_with_reasoning": 0, "turns_without_reasoning": 0}
    completed_in_batch = []
    discarded_no_reasoning = 0
        
    # 在此批次中顺序处理每个 prompt
    for prompt_index, prompt_data in prompts_to_process:
        # 处理 prompt
        result = _process_single_prompt(
            prompt_index,
            prompt_data,
            batch_num,
            config
        )
            
        # 如果成功则保存轨迹
        if result["success"] and result["trajectory"]:
            # 丢弃所有轮次都没有推理的样本
            reasoning = result.get("reasoning_stats", {})
            if not reasoning.get("has_any_reasoning", True):
                print(f"   🚫 Prompt {prompt_index} discarded (no reasoning in any turn)")
                discarded_no_reasoning += 1
                continue
                
            # 获取并标准化工具统计以保持所有条目的统一 schema
            raw_tool_stats = result.get("tool_stats", {})
            tool_stats = _normalize_tool_stats(raw_tool_stats)
                
            # 创建标准化工具错误计数，将工具名映射到其失败计数
            raw_error_counts = {
                tool_name: stats.get("failure", 0)
                for tool_name, stats in raw_tool_stats.items()
            }
            tool_error_counts = _normalize_tool_error_counts(raw_error_counts)
                
            trajectory_entry = {
                "prompt_index": prompt_index,
                "conversations": result["trajectory"],
                "metadata": result["metadata"],
                "completed": result["completed"],
                "partial": result.get("partial", False),  # 如果因无效工具调用而停止则为 True
                "api_calls": result["api_calls"],
                "toolsets_used": result["toolsets_used"],
                "tool_stats": tool_stats,  # 完整统计：{tool: {count, success, failure}} - 已标准化
                "tool_error_counts": tool_error_counts  # 简单统计：{tool: failure_count} - 已标准化
            }
                
            # 追加到批次输出文件
            with open(batch_output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trajectory_entry, ensure_ascii=False) + "\n")
            
        # 聚合工具统计
        for tool_name, stats in result.get("tool_stats", {}).items():
            if tool_name not in batch_tool_stats:
                batch_tool_stats[tool_name] = {
                    "count": 0,
                    "success": 0,
                    "failure": 0
                }
                
            batch_tool_stats[tool_name]["count"] += stats["count"]
            batch_tool_stats[tool_name]["success"] += stats["success"]
            batch_tool_stats[tool_name]["failure"] += stats["failure"]
            
        # 聚合推理统计
        for key in batch_reasoning_stats:
            batch_reasoning_stats[key] += result.get("reasoning_stats", {}).get(key, 0)
            
        # 仅在成功保存时才标记为已完成（失败的 prompts 可以在恢复时重试）
        if result["success"] and result["trajectory"]:
            completed_in_batch.append(prompt_index)
            status = "⚠️  partial" if result.get("partial") else "✅"
            print(f"   {status} Prompt {prompt_index} completed")
        else:
            print(f"   ❌ Prompt {prompt_index} failed (will retry on resume)")
    
    print(f"✅ Batch {batch_num}: Completed ({len(prompts_to_process)} prompts processed)")
    
    return {
        "batch_num": batch_num,
        "processed": len(prompts_to_process),
        "skipped": len(batch_data) - len(prompts_to_process),
        "tool_stats": batch_tool_stats,
        "reasoning_stats": batch_reasoning_stats,
        "discarded_no_reasoning": discarded_no_reasoning,
        "completed_prompts": completed_in_batch
    }


class BatchRunner:
    """
    管理带断点续传和统计的 agent prompts 批量处理。
    """
    
    def __init__(
        self,
        dataset_file: str,
        batch_size: int,
        run_name: str,
        distribution: str = "default",
        max_iterations: int = 10,
        base_url: str = None,
        api_key: str = None,
        model: str = "claude-opus-4-20250514",
        num_workers: int = 4,
        verbose: bool = False,
        ephemeral_system_prompt: str = None,
        log_prefix_chars: int = 100,
        providers_allowed: List[str] = None,
        providers_ignored: List[str] = None,
        providers_order: List[str] = None,
        provider_sort: str = None,
        max_tokens: int = None,
        reasoning_config: Dict[str, Any] = None,
        prefill_messages: List[Dict[str, Any]] = None,
        max_samples: int = None,
    ):
        """
        初始化批量运行器。

        参数:
            dataset_file (str): 包含 'prompt' 字段的数据集 JSONL 文件路径
            batch_size (int): 每个批次的 prompt 数量
            run_name (str): 此运行的名称（用于断点续传和输出）
            distribution (str): 要使用的工具集分布（默认: "default"）
            max_iterations (int): 每次 agent 运行的最多迭代次数
            base_url (str): 模型 API 的基础 URL
            api_key (str): 模型的 API 密钥
            model (str): 要使用的模型名称
            num_workers (int): 并行 worker 进程数量
            verbose (bool): 启用详细日志
            ephemeral_system_prompt (str): agent 执行期间使用的系统提示词，但不会保存到轨迹（可选）
            log_prefix_chars (int): 工具调用/响应日志预览中显示的字符数（默认: 20）
            providers_allowed (List[str]): 允许的 OpenRouter providers（可选）
            providers_ignored (List[str]): 忽略的 OpenRouter providers（可选）
            providers_order (List[str]): 按顺序尝试的 OpenRouter providers（可选）
            provider_sort (str): 按价格/吞吐量/延迟排序 providers（可选）
            max_tokens (int): 模型响应的最大 token 数（可选，未设置则使用模型默认值）
            reasoning_config (Dict): OpenRouter 推理配置覆盖（例如 {"effort": "none"} 禁用思考）
            prefill_messages (List[Dict]): 作为预填充对话上下文的追加消息（few-shot 提示）
            max_samples (int): 仅处理数据集中的前 N 个样本（可选，未设置则处理全部）
        """
        self.dataset_file = Path(dataset_file)
        self.batch_size = batch_size
        self.run_name = run_name
        self.distribution = distribution
        self.max_iterations = max_iterations
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.num_workers = num_workers
        self.verbose = verbose
        self.ephemeral_system_prompt = ephemeral_system_prompt
        self.log_prefix_chars = log_prefix_chars
        self.providers_allowed = providers_allowed
        self.providers_ignored = providers_ignored
        self.providers_order = providers_order
        self.provider_sort = provider_sort
        self.max_tokens = max_tokens
        self.reasoning_config = reasoning_config
        self.prefill_messages = prefill_messages
        self.max_samples = max_samples
        
        # 验证分布
        if not validate_distribution(distribution):
            raise ValueError(f"Unknown distribution: {distribution}. Available: {list(list_distributions().keys())}")
        
        # 设置输出目录
        self.output_dir = Path("data") / run_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 断点文件
        self.checkpoint_file = self.output_dir / "checkpoint.json"
        
        # 统计文件
        self.stats_file = self.output_dir / "statistics.json"
        
        # 加载数据集（并可选择截断到 max_samples）
        self.dataset = self._load_dataset()
        if self.max_samples and self.max_samples < len(self.dataset):
            full_count = len(self.dataset)
            self.dataset = self.dataset[:self.max_samples]
            print(f"✂️  Truncated dataset from {full_count} to {self.max_samples} samples (--max_samples)")
        
        # 创建批次
        self.batches = self._create_batches()
        
        print("📊 Batch Runner Initialized")
        print(f"   Dataset: {self.dataset_file} ({len(self.dataset)} prompts)")
        print(f"   Batch size: {self.batch_size}")
        print(f"   Total batches: {len(self.batches)}")
        print(f"   Run name: {self.run_name}")
        print(f"   Distribution: {self.distribution}")
        print(f"   Output directory: {self.output_dir}")
        print(f"   Workers: {self.num_workers}")
        if self.ephemeral_system_prompt:
            prompt_preview = self.ephemeral_system_prompt[:60] + "..." if len(self.ephemeral_system_prompt) > 60 else self.ephemeral_system_prompt
            print(f"   🔒 Ephemeral system prompt: '{prompt_preview}'")
    
    def _load_dataset(self) -> List[Dict[str, Any]]:
        """
        从 JSONL 文件加载数据集。
        
        返回:
            List[Dict]: 数据集条目列表
        """
        if not self.dataset_file.exists():
            raise FileNotFoundError(f"Dataset file not found: {self.dataset_file}")
        
        dataset = []
        with open(self.dataset_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                    if 'prompt' not in entry:
                        print(f"⚠️  Warning: Line {line_num} missing 'prompt' field, skipping")
                        continue
                    dataset.append(entry)
                except json.JSONDecodeError as e:
                    print(f"⚠️  Warning: Invalid JSON on line {line_num}: {e}")
                    continue
        
        if not dataset:
            raise ValueError(f"No valid entries found in dataset file: {self.dataset_file}")
        
        return dataset
    
    def _create_batches(self) -> List[List[Tuple[int, Dict[str, Any]]]]:
        """
        将数据集分割为带索引的批次。
        
        返回:
            批次列表，每个批次是 (index, entry) 元组的列表
        """
        batches = []
        for i in range(0, len(self.dataset), self.batch_size):
            batch = [(idx, entry) for idx, entry in enumerate(self.dataset[i:i + self.batch_size], start=i)]
            batches.append(batch)
        
        return batches
    
    def _load_checkpoint(self) -> Dict[str, Any]:
        """
        如果存在则加载断点数据。
        
        返回:
            Dict: 带已完成 prompt 索引的断点数据
        """
        if not self.checkpoint_file.exists():
            return {
                "run_name": self.run_name,
                "completed_prompts": [],
                "batch_stats": {},
                "last_updated": None
            }
        
        try:
            with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  Warning: Failed to load checkpoint: {e}")
            return {
                "run_name": self.run_name,
                "completed_prompts": [],
                "batch_stats": {},
                "last_updated": None
            }
    
    def _save_checkpoint(self, checkpoint_data: Dict[str, Any], lock: Optional[Lock] = None):
        """
        保存断点数据。
        
        参数:
            checkpoint_data (Dict): 要保存的断点数据
            lock (Lock): 用于线程安全访问的可选锁
        """
        checkpoint_data["last_updated"] = datetime.now().isoformat()

        from utils import atomic_json_write
        if lock:
            with lock:
                atomic_json_write(self.checkpoint_file, checkpoint_data)
        else:
            atomic_json_write(self.checkpoint_file, checkpoint_data)
    
    def _scan_completed_prompts_by_content(self) -> set:
        """
        扫描所有批次文件并按实际内容提取已完成的 prompts。
        
        这提供了更强大的恢复机制，通过 prompt 文本而非索引进行匹配，
        即使索引不匹配也能恢复。
        
        返回:
            set: 已成功处理的 prompt 文本集合
        """
        completed_prompts = set()
        batch_files = sorted(self.output_dir.glob("batch_*.jsonl"))
        
        if not batch_files:
            return completed_prompts
        
        print(f"📂 Scanning {len(batch_files)} batch files for completed prompts...")
        
        for batch_file in batch_files:
            try:
                with open(batch_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            
                            # 跳过失败的条目 - 我们要重试这些
                            if entry.get("failed", False):
                                continue
                            
                            # 从对话中提取 human/user prompt
                            conversations = entry.get("conversations", [])
                            for msg in conversations:
                                if msg.get("from") == "human":
                                    prompt_text = msg.get("value", "").strip()
                                    if prompt_text:
                                        completed_prompts.add(prompt_text)
                                    break  # 只需第一个 human 消息
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print(f"  ⚠️  Warning: Error reading {batch_file.name}: {e}")
        
        return completed_prompts
    
    def _filter_dataset_by_completed(self, completed_prompts: set) -> Tuple[List[Dict], List[int]]:
        """
        过滤数据集，排除已完成的 prompts。
        
        参数:
            completed_prompts: 已完成的 prompt 文本集合
            
        返回:
            (filtered_dataset, skipped_indices) 元组
        """
        filtered_dataset = []
        skipped_indices = []
        
        for idx, entry in enumerate(self.dataset):
            # 从数据集条目中提取 prompt
            prompt_text = entry.get("prompt", "").strip()
            
            # 也检查对话格式
            if not prompt_text:
                conversations = entry.get("conversations", [])
                for msg in conversations:
                    role = msg.get("role") or msg.get("from")
                    if role in ("user", "human"):
                        prompt_text = (msg.get("content") or msg.get("value", "")).strip()
                        break
            
            if prompt_text in completed_prompts:
                skipped_indices.append(idx)
            else:
                # 保留原始索引用于跟踪
                filtered_dataset.append((idx, entry))
        
        return filtered_dataset, skipped_indices
    
    def run(self, resume: bool = False):
        """
        运行批量处理管道。
        
        参数:
            resume (bool): 是否从断点恢复
        """
        print("\n" + "=" * 70)
        print("🚀 Starting Batch Processing")
        print("=" * 70)
        
        # 智能恢复：扫描批次文件按内容查找已完成的 prompts
        completed_prompt_texts = set()
        if resume:
            completed_prompt_texts = self._scan_completed_prompts_by_content()
            if completed_prompt_texts:
                print(f"   Found {len(completed_prompt_texts)} already-completed prompts by content matching")
        
        # 过滤数据集，只包含未处理的 prompts
        if resume and completed_prompt_texts:
            filtered_entries, skipped_indices = self._filter_dataset_by_completed(completed_prompt_texts)
            
            if not filtered_entries:
                print("\n✅ All prompts have already been processed!")
                return
            
            # 从过滤条目重新创建批次（保留原始索引用于跟踪）
            batches_to_process = []
            for i in range(0, len(filtered_entries), self.batch_size):
                batch = filtered_entries[i:i + self.batch_size]
                batches_to_process.append(batch)
            
            self.batches = batches_to_process
            
            # 打印醒目的恢复摘要
            print("\n" + "=" * 70)
            print("📊 RESUME SUMMARY")
            print("=" * 70)
            print(f"   Original dataset size:     {len(self.dataset):,} prompts")
            print(f"   Already completed:         {len(skipped_indices):,} prompts")
            print("   ─────────────────────────────────────────")
            print(f"   🎯 RESUMING WITH:          {len(filtered_entries):,} prompts")
            print(f"   New batches created:       {len(batches_to_process)}")
            print("=" * 70 + "\n")
        
        # 加载现有断点（使恢复不会覆盖先前的进度）
        checkpoint_data = self._load_checkpoint()
        if checkpoint_data.get("run_name") != self.run_name:
            checkpoint_data = {
                "run_name": self.run_name,
                "completed_prompts": [],
                "batch_stats": {},
                "last_updated": None
            }
        
        # 为 workers 准备配置
        config = {
            "distribution": self.distribution,
            "model": self.model,
            "max_iterations": self.max_iterations,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "verbose": self.verbose,
            "ephemeral_system_prompt": self.ephemeral_system_prompt,
            "log_prefix_chars": self.log_prefix_chars,
            "providers_allowed": self.providers_allowed,
            "providers_ignored": self.providers_ignored,
            "providers_order": self.providers_order,
            "provider_sort": self.provider_sort,
            "max_tokens": self.max_tokens,
            "reasoning_config": self.reasoning_config,
            "prefill_messages": self.prefill_messages,
        }
        
        # 为了向后兼容，仍然按索引跟踪（但这次于内容匹配）
        completed_prompts_set = set(checkpoint_data.get("completed_prompts", []))
        
        # 跨所有批次聚合统计
        total_tool_stats = {}
        
        start_time = time.time()
        
        print(f"\n🔧 Initializing {self.num_workers} worker processes...")
        
        # 断点写入发生在父进程；保留锁以确保安全
        checkpoint_lock = Lock()

        # 并行处理批次
        with Pool(processes=self.num_workers) as pool:
            # 为每个批次创建任务
            tasks = [
                (
                    batch_num,
                    batch_data,
                    str(self.output_dir),  # 转换为字符串以进行 pickle
                    completed_prompts_set,
                    config
                )
                for batch_num, batch_data in enumerate(self.batches)
            ]
            
            print(f"✅ Created {len(tasks)} batch tasks")
            print("🚀 Starting parallel batch processing...\n")
            
            # 使用 rich Progress 以获得更好的可视跟踪和持久底部栏
            # redirect_stdout/stderr 让 rich 管理所有输出，使进度条保持整洁
            results = []
            console = Console(force_terminal=True)
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]📦 Batches"),
                BarColumn(bar_width=40),
                MofNCompleteColumn(),
                TextColumn("•"),
                TimeRemainingColumn(),
                console=console,
                refresh_per_second=2,
                transient=False,
                redirect_stdout=False,
                redirect_stderr=False,
            ) as progress:
                task = progress.add_task("Processing", total=len(tasks))
                
                # Temporarily suppress DEBUG logging to avoid bar interference
                root_logger = logging.getLogger()
                original_level = root_logger.level
                root_logger.setLevel(logging.WARNING)
                
                try:
                    for result in pool.imap_unordered(_process_batch_worker, tasks):
                        results.append(result)
                        progress.update(task, advance=1)

                        # Incremental checkpoint update (so resume works after crash)
                        try:
                            batch_num = result.get('batch_num')
                            completed = result.get('completed_prompts', []) or []
                            completed_prompts_set.update(completed)

                            if isinstance(batch_num, int):
                                checkpoint_data.setdefault('batch_stats', {})[str(batch_num)] = {
                                    'processed': result.get('processed', 0),
                                    'skipped': result.get('skipped', 0),
                                    'discarded_no_reasoning': result.get('discarded_no_reasoning', 0),
                                }

                            checkpoint_data['completed_prompts'] = sorted(completed_prompts_set)
                            self._save_checkpoint(checkpoint_data, lock=checkpoint_lock)
                        except Exception as ckpt_err:
                            # Don't fail the run if checkpoint write fails
                            print(f"⚠️  Warning: Failed to save incremental checkpoint: {ckpt_err}")
                except Exception as e:
                    logger.error("Batch worker failed: %s", e, exc_info=True)
                    raise
                finally:
                    root_logger.setLevel(original_level)
        
        # Aggregate all batch statistics and update checkpoint
        all_completed_prompts = list(completed_prompts_set)
        total_reasoning_stats = {"total_assistant_turns": 0, "turns_with_reasoning": 0, "turns_without_reasoning": 0}
        
        for batch_result in results:
            # Add newly completed prompts
            all_completed_prompts.extend(batch_result.get("completed_prompts", []))
            
            # Aggregate tool stats
            for tool_name, stats in batch_result.get("tool_stats", {}).items():
                if tool_name not in total_tool_stats:
                    total_tool_stats[tool_name] = {
                        "count": 0,
                        "success": 0,
                        "failure": 0
                    }
                
                total_tool_stats[tool_name]["count"] += stats["count"]
                total_tool_stats[tool_name]["success"] += stats["success"]
                total_tool_stats[tool_name]["failure"] += stats["failure"]
            
            # Aggregate reasoning stats
            for key in total_reasoning_stats:
                total_reasoning_stats[key] += batch_result.get("reasoning_stats", {}).get(key, 0)
        
        # Save final checkpoint (best-effort; incremental writes already happened)
        try:
            checkpoint_data["completed_prompts"] = all_completed_prompts
            self._save_checkpoint(checkpoint_data, lock=checkpoint_lock)
        except Exception as ckpt_err:
            print(f"âš ï¸  Warning: Failed to save final checkpoint: {ckpt_err}")
        
        # Calculate success rates
        for tool_name in total_tool_stats:
            stats = total_tool_stats[tool_name]
            total_calls = stats["success"] + stats["failure"]
            if total_calls > 0:
                stats["success_rate"] = round(stats["success"] / total_calls * 100, 2)
                stats["failure_rate"] = round(stats["failure"] / total_calls * 100, 2)
            else:
                stats["success_rate"] = 0.0
                stats["failure_rate"] = 0.0
        
        # Combine ALL batch files in directory into a single trajectories.jsonl file
        # This includes both old batches (from previous runs) and new batches (from resume)
        # Also filter out corrupted entries (where model generated invalid tool names)
        combined_file = self.output_dir / "trajectories.jsonl"
        print(f"\n📦 Combining ALL batch files into {combined_file.name}...")
        
        # Valid tools auto-derived from model_tools.py — no manual updates needed
        VALID_TOOLS = ALL_POSSIBLE_TOOLS
        
        total_entries = 0
        filtered_entries = 0
        batch_files_found = 0
        
        # 查找输出目录中所有批次文件（处理恢复时合并旧+新）
        all_batch_files = sorted(self.output_dir.glob("batch_*.jsonl"))
        
        with open(combined_file, 'w', encoding='utf-8') as outfile:
            for batch_file in all_batch_files:
                batch_files_found += 1
                batch_num = batch_file.stem.split("_")[1]  # 提取批次编号用于日志
                
                with open(batch_file, 'r', encoding='utf-8') as infile:
                    for line in infile:
                        total_entries += 1
                        try:
                            data = json.loads(line)
                            tool_stats = data.get('tool_stats', {})
                            
                            # 检查无效的工具名（模型幻觉）
                            invalid_tools = [k for k in tool_stats if k not in VALID_TOOLS]
                            
                            if invalid_tools:
                                filtered_entries += 1
                                invalid_preview = invalid_tools[0][:50] + "..." if len(invalid_tools[0]) > 50 else invalid_tools[0]
                                print(f"   ⚠️  Filtering corrupted entry (batch {batch_num}): invalid tool '{invalid_preview}'")
                                continue
                            
                            outfile.write(line)
                        except json.JSONDecodeError:
                            filtered_entries += 1
                            print(f"   ⚠️  Filtering invalid JSON entry (batch {batch_num})")
        
        if filtered_entries > 0:
            print(f"⚠️  Filtered {filtered_entries} corrupted entries out of {total_entries} total")
        print(f"✅ Combined {batch_files_found} batch files into trajectories.jsonl ({total_entries - filtered_entries} entries)")
        
        # 保存最终统计
        final_stats = {
            "run_name": self.run_name,
            "distribution": self.distribution,
            "total_prompts": len(self.dataset),
            "total_batches": len(self.batches),
            "batch_size": self.batch_size,
            "model": self.model,
            "completed_at": datetime.now().isoformat(),
            "duration_seconds": round(time.time() - start_time, 2),
            "tool_statistics": total_tool_stats,
            "reasoning_statistics": total_reasoning_stats,
        }
        
        with open(self.stats_file, 'w', encoding='utf-8') as f:
            json.dump(final_stats, f, indent=2, ensure_ascii=False)
        
        # 打印摘要
        print("\n" + "=" * 70)
        print("📊 BATCH PROCESSING COMPLETE")
        print("=" * 70)
        print(f"✅ Prompts processed this run: {sum(r.get('processed', 0) for r in results)}")
        print(f"✅ Total trajectories in merged file: {total_entries - filtered_entries}")
        print(f"✅ Total batch files merged: {batch_files_found}")
        print(f"⏱️  Total duration: {round(time.time() - start_time, 2)}s")
        print("\n📈 Tool Usage Statistics:")
        print("-" * 70)
        
        if total_tool_stats:
            # 按计数降序排序
            sorted_tools = sorted(
                total_tool_stats.items(),
                key=lambda x: x[1]["count"],
                reverse=True
            )
            
            print(f"{'Tool Name':<25} {'Count':<10} {'Success':<10} {'Failure':<10} {'Success Rate':<12}")
            print("-" * 70)
            for tool_name, stats in sorted_tools:
                print(
                    f"{tool_name:<25} "
                    f"{stats['count']:<10} "
                    f"{stats['success']:<10} "
                    f"{stats['failure']:<10} "
                    f"{stats['success_rate']:.1f}%"
                )
        else:
            print("No tool calls were made during this run.")
        
        # 打印推理覆盖率统计
        total_discarded = sum(r.get("discarded_no_reasoning", 0) for r in results)
        
        print("\n🧠 Reasoning Coverage:")
        print("-" * 70)
        total_turns = total_reasoning_stats["total_assistant_turns"]
        with_reasoning = total_reasoning_stats["turns_with_reasoning"]
        without_reasoning = total_reasoning_stats["turns_without_reasoning"]
        if total_turns > 0:
            pct_with = round(with_reasoning / total_turns * 100, 1)
            pct_without = round(without_reasoning / total_turns * 100, 1)
            print(f"   Total assistant turns:    {total_turns:,}")
            print(f"   With reasoning:           {with_reasoning:,} ({pct_with}%)")
            print(f"   Without reasoning:        {without_reasoning:,} ({pct_without}%)")
        else:
            print("   No assistant turns recorded.")
        if total_discarded > 0:
            print(f"   🚫 Samples discarded (zero reasoning): {total_discarded:,}")
        
        print(f"\n💾 Results saved to: {self.output_dir}")
        print("   - Trajectories: trajectories.jsonl (combined)")
        print("   - Individual batches: batch_*.jsonl (for debugging)")
        print(f"   - Statistics: {self.stats_file.name}")
        print(f"   - Checkpoint: {self.checkpoint_file.name}")


def main(
    dataset_file: str = None,
    batch_size: int = None,
    run_name: str = None,
    distribution: str = "default",
    model: str = "anthropic/claude-sonnet-4.6",
    api_key: str = None,
    base_url: str = "https://openrouter.ai/api/v1",
    max_turns: int = 10,
    num_workers: int = 4,
    resume: bool = False,
    verbose: bool = False,
    list_distributions: bool = False,
    ephemeral_system_prompt: str = None,
    log_prefix_chars: int = 100,
    providers_allowed: str = None,
    providers_ignored: str = None,
    providers_order: str = None,
    provider_sort: str = None,
    max_tokens: int = None,
    reasoning_effort: str = None,
    reasoning_disabled: bool = False,
    prefill_messages_file: str = None,
    max_samples: int = None,
):
    """
    从数据集运行 agent prompts 的批量处理。

    参数:
        dataset_file (str): 每个条目包含 'prompt' 字段的 JSONL 文件路径
        batch_size (int): 每个批次的 prompt 数量
        run_name (str): 此运行的名称（用于输出和断点续传）
        distribution (str): 要使用的工具集分布（默认: "default"）
        model (str): 要使用的模型名称（默认: "claude-opus-4-20250514"）
        api_key (str): 模型认证的 API 密钥
        base_url (str): 模型 API 的基础 URL
        max_turns (int): 每个 prompt 的最多工具调用迭代次数（默认: 10）
        num_workers (int): 并行 worker 进程数量（默认: 4）
        resume (bool): 如果运行中断则从断点恢复（默认: False）
        verbose (bool): 启用详细日志（默认: False）
        list_distributions (bool): 列出可用的工具集分布并退出
        ephemeral_system_prompt (str): agent 执行期间使用的系统提示词，但不会保存到轨迹（可选）
        log_prefix_chars (int): 工具调用/响应日志预览中显示的字符数（默认: 20）
        providers_allowed (str): 允许的 OpenRouter providers 逗号分隔列表（例如 "anthropic,openai"）
        providers_ignored (str): 忽略的 OpenRouter providers 逗号分隔列表（例如 "together,deepinfra"）
        providers_order (str): 按顺序尝试的 OpenRouter providers 逗号分隔列表（例如 "anthropic,openai,google"）
        provider_sort (str): 按 "price"、"throughput" 或 "latency" 排序 providers（仅 OpenRouter）
        max_tokens (int): 模型响应的最大 token 数（可选，未设置则使用模型默认值）
        reasoning_effort (str): OpenRouter 推理努力级别："xhigh"、"high"、"medium"、"low"、"minimal"、"none"（默认: "medium"）
        reasoning_disabled (bool): 完全禁用推理/思考 token（默认: False）
        prefill_messages_file (str): 包含预填充消息的 JSON 文件路径（{role, content} 字典列表）
        max_samples (int): 仅处理数据集中的前 N 个样本（可选，未设置则处理全部）
        
    示例:
        # 基本用法
        python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run
        
        # 从中断处恢复运行
        python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run --resume
        
        # 使用特定分布
        python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=image_test --distribution=image_gen
        
        # 禁用推理并设置最大 token
        python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run \\
                               --reasoning_disabled --max_tokens=128000
        
        # 从文件加载预填充消息
        python batch_runner.py --dataset_file=data.jsonl --batch_size=10 --run_name=my_run \\
                               --prefill_messages_file=configs/prefill_opus.json
        
        # 列出可用的分布
        python batch_runner.py --list_distributions
    """
    # 处理列表分布
    if list_distributions:
        from toolset_distributions import list_distributions as get_all_dists, print_distribution_info
        
        print("📊 Available Toolset Distributions")
        print("=" * 70)
        
        all_dists = get_all_dists()
        for dist_name in sorted(all_dists.keys()):
            print_distribution_info(dist_name)
        
        print("\n💡 Usage:")
        print("  python batch_runner.py --dataset_file=data.jsonl --batch_size=10 \\")
        print("                         --run_name=my_run --distribution=<name>")
        return
    
    # 验证必需参数
    if not dataset_file:
        print("❌ Error: --dataset_file is required")
        return
    
    if not batch_size or batch_size < 1:
        print("❌ Error: --batch_size must be a positive integer")
        return
    
    if not run_name:
        print("❌ Error: --run_name is required")
        return
    
    # 解析 provider 偏好（逗号分隔字符串转为列表）
    providers_allowed_list = [p.strip() for p in providers_allowed.split(",")] if providers_allowed else None
    providers_ignored_list = [p.strip() for p in providers_ignored.split(",")] if providers_ignored else None
    providers_order_list = [p.strip() for p in providers_order.split(",")] if providers_order else None
    
    # 从 CLI 参数构建 reasoning_config
    # --reasoning_disabled 优先，然后是 --reasoning_effort，最后是默认值（medium）
    reasoning_config = None
    if reasoning_disabled:
        # 完全禁用推理/思考 token
        reasoning_config = {"effort": "none"}
        print("🧠 Reasoning: DISABLED (effort=none)")
    elif reasoning_effort:
        # 使用指定的努力级别
        valid_efforts = ["xhigh", "high", "medium", "low", "minimal", "none"]
        if reasoning_effort not in valid_efforts:
            print(f"❌ Error: --reasoning_effort must be one of: {', '.join(valid_efforts)}")
            return
        reasoning_config = {"enabled": True, "effort": reasoning_effort}
        print(f"🧠 Reasoning effort: {reasoning_effort}")
    
    # 如果提供了则从 JSON 文件加载预填充消息
    prefill_messages = None
    if prefill_messages_file:
        try:
            with open(prefill_messages_file, 'r', encoding='utf-8') as f:
                prefill_messages = json.load(f)
            if not isinstance(prefill_messages, list):
                print("❌ Error: prefill_messages_file must contain a JSON array of messages")
                return
            print(f"💬 Loaded {len(prefill_messages)} prefill messages from {prefill_messages_file}")
        except Exception as e:
            print(f"❌ Error loading prefill messages: {e}")
            return
    
    # 初始化并运行批量运行器
    try:
        runner = BatchRunner(
            dataset_file=dataset_file,
            batch_size=batch_size,
            run_name=run_name,
            distribution=distribution,
            max_iterations=max_turns,
            base_url=base_url,
            api_key=api_key,
            model=model,
            num_workers=num_workers,
            verbose=verbose,
            ephemeral_system_prompt=ephemeral_system_prompt,
            log_prefix_chars=log_prefix_chars,
            providers_allowed=providers_allowed_list,
            providers_ignored=providers_ignored_list,
            providers_order=providers_order_list,
            provider_sort=provider_sort,
            max_tokens=max_tokens,
            reasoning_config=reasoning_config,
            prefill_messages=prefill_messages,
            max_samples=max_samples,
        )

        runner.run(resume=resume)
    
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        if verbose:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    fire.Fire(main)

