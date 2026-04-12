"""轨迹保存工具和静态助手函数。

_convert_to_trajectory_format 保留为 AIAgent 方法(batch_runner.py
调用 agent._convert_to_trajectory_format)。仅静态助手和
文件写入逻辑位于此处。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def convert_scratchpad_to_think(content: str) -> str:
    """将 <REASONING_SCRATCHPAD> 标签转换为 <think> 标签。"""
    if not content or "<REASONING_SCRATCHPAD>" not in content:
        return content
    return content.replace("<REASONING_SCRATCHPAD>", "<think>").replace("</REASONING_SCRATCHPAD>", "</think>")


def has_incomplete_scratchpad(content: str) -> bool:
    """检查内容是否有开始 <REASONING_SCRATCHPAD> 但没有结束标签。"""
    if not content:
        return False
    return "<REASONING_SCRATCHPAD>" in content and "</REASONING_SCRATCHPAD>" not in content


def save_trajectory(trajectory: List[Dict[str, Any]], model: str,
                    completed: bool, filename: str = None):
    """将轨迹条目追加到 JSONL 文件。

    Args:
        trajectory: ShareGPT 格式的对话列表。
        model: 用于元数据的模型名称。
        completed: 对话是否成功完成。
        filename: 覆盖输出文件名。默认为 trajectory_samples.jsonl
                  或 failed_trajectories.jsonl,取决于 ``completed``。
    """
    if filename is None:
        filename = "trajectory_samples.jsonl" if completed else "failed_trajectories.jsonl"

    entry = {
        "conversations": trajectory,
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "completed": completed,
    }

    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("轨迹已保存到 %s", filename)
    except Exception as e:
        logger.warning("保存轨迹失败: %s", e)
