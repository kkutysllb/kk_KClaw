"""
Telegram 贴纸描述缓存。

当用户发送贴纸时，我们通过视觉工具描述它们，
并按 file_unique_id 缓存描述，这样我们就不会在每次发送时
重新分析同一张贴纸图片。描述简洁（1-2 句话）。

缓存位置：~/.kclaw/sticker_cache.json
"""

import json
import time
from typing import Optional

from kclaw_cli.config import get_kclaw_home


CACHE_PATH = get_kclaw_home() / "sticker_cache.json"

# 用于描述贴纸的视觉提示 — 保持简洁以节省 tokens
STICKER_VISION_PROMPT = (
    "用 1-2 句话描述这张贴纸。专注于它描绘的内容 -- "
    "角色、动作、情感。简洁且客观。"
)


def _load_cache() -> dict:
    """从磁盘加载贴纸缓存。"""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """将贴纸缓存保存到磁盘。"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_cached_description(file_unique_id: str) -> Optional[dict]:
    """
    查找缓存的贴纸描述。

    返回：
        包含键 {description, emoji, set_name, cached_at} 的字典，或 None。
    """
    cache = _load_cache()
    return cache.get(file_unique_id)


def cache_sticker_description(
    file_unique_id: str,
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> None:
    """
    将贴纸描述存储到缓存中。

    参数：
        file_unique_id: Telegram 稳定的贴纸标识符。
        description:  视觉生成的描述文本。
        emoji:       关联的表情符号（例如 "😀"）。
        set_name:    如果可用的话，贴纸包名称。
    """
    cache = _load_cache()
    cache[file_unique_id] = {
        "description": description,
        "emoji": emoji,
        "set_name": set_name,
        "cached_at": time.time(),
    }
    _save_cache(cache)


def build_sticker_injection(
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> str:
    """
    为贴纸描述构建 warm 风格的注入文本。

    返回类似这样的字符串：
      [用户发送了一个来自 "MyPack" 的贴纸 😀~ 它显示: "A cat waving" (=^.w.^=)]
    """
    context = ""
    if set_name and emoji:
        context = f" {emoji} 来自 \"{set_name}\""
    elif emoji:
        context = f" {emoji}"

    return f"[用户发送了一个贴纸{context}~ 它显示: \"{description}\" (=^.w.^=)]"


def build_animated_sticker_injection(emoji: str = "") -> str:
    """
    为我们无法分析的动画/视频贴纸构建注入文本。
    """
    if emoji:
        return (
            f"[用户发送了一个动画贴纸 {emoji}~ "
            f"我目前还看不到动画贴纸，但表情符号暗示: {emoji}]"
        )
    return "[用户发送了一个动画贴纸~ 我目前还看不到动画贴纸]"
