"""工具结果持久化 -- 保留大输出而不是截断。

防止上下文窗口溢出的防御分为三个层次:

1. **每个工具的输出上限**（在每个工具内部）: 像 search_files 这样的工具
   在返回之前会预先截断自己的输出。这是第一道防线,
   也是工具作者唯一能控制的。

2. **每个结果的持久化** (maybe_persist_tool_result): 工具返回后,
   如果输出超过工具注册的阈值 (registry.get_max_result_size),
   完整输出会通过 env.execute() 写入沙箱的
   /tmp/kclaw-results/{tool_use_id}.txt。
   上下文中的内容被替换为预览 + 文件路径引用。
   模型可以通过 read_file 工具在任何后端访问完整输出。

3. **每轮聚合预算** (enforce_turn_budget): 收集完单个助手轮次中的所有工具结果后,
   如果总大小超过 MAX_TURN_BUDGET_CHARS (200K),
   最大的未持久化结果会被写入磁盘,直到聚合大小在预算内。
   这可以处理多个中等大小的结果组合导致上下文溢出的情况。
"""

import logging
import uuid

from tools.budget_config import (
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
    DEFAULT_BUDGET,
)

logger = logging.getLogger(__name__)
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
STORAGE_DIR = "/tmp/kclaw-results"
HEREDOC_MARKER = "KCLAW_PERSIST_EOF"
_BUDGET_TOOL_NAME = "__budget_enforcement__"


def generate_preview(content: str, max_chars: int = DEFAULT_PREVIEW_SIZE_CHARS) -> tuple[str, bool]:
    """在 max_chars 内的最后一个换行符处截断。返回 (preview, has_more)。"""
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[:last_nl + 1]
    return truncated, True


def _heredoc_marker(content: str) -> str:
    """返回一个不会与内容冲突的 heredoc 分隔符。"""
    if HEREDOC_MARKER not in content:
        return HEREDOC_MARKER
    return f"KCLAW_PERSIST_{uuid.uuid4().hex[:8]}"


def _write_to_sandbox(content: str, remote_path: str, env) -> bool:
    """通过 env.execute() 将内容写入沙箱。成功时返回 True。"""
    marker = _heredoc_marker(content)
    cmd = (
        f"mkdir -p {STORAGE_DIR} && cat > {remote_path} << '{marker}'\n"
        f"{content}\n"
        f"{marker}"
    )
    result = env.execute(cmd, timeout=30)
    return result.get("returncode", 1) == 0


def _build_persisted_message(
    preview: str,
    has_more: bool,
    original_size: int,
    file_path: str,
) -> str:
    """构建 <persisted-output> 替换块。"""
    size_kb = original_size / 1024
    if size_kb >= 1024:
        size_str = f"{size_kb / 1024:.1f} MB"
    else:
        size_str = f"{size_kb:.1f} KB"

    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += f"This tool result was too large ({original_size:,} characters, {size_str}).\n"
    msg += f"Full output saved to: {file_path}\n"
    msg += "Use the read_file tool with offset and limit to access specific sections of this output.\n\n"
    msg += f"Preview (first {len(preview)} chars):\n"
    msg += preview
    if has_more:
        msg += "\n..."
    msg += f"\n{PERSISTED_OUTPUT_CLOSING_TAG}"
    return msg


def maybe_persist_tool_result(
    content: str,
    tool_name: str,
    tool_use_id: str,
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
    threshold: int | float | None = None,
) -> str:
    """第二层: 将超大的结果持久化到沙箱,返回预览 + 路径。

    通过 env.execute() 写入,使得文件可以从任何后端访问
    (本地、Docker、SSH、Modal、Daytona)。如果写入失败或没有可用的 env,
    则回退到内联截断。

    参数:
        content: 原始工具结果字符串。
        tool_name: 工具名称 (用于阈值查找)。
        tool_use_id: 此工具调用的唯一 ID (用作文件名)。
        env: 活动的 BaseEnvironment 实例,或 None。
        config: 控制阈值和预览大小的 BudgetConfig。
        threshold: 显式覆盖; 优先于配置解析。

    返回:
        如果内容小则返回原始内容,否则返回 <persisted-output> 替换。
    """
    effective_threshold = threshold if threshold is not None else config.resolve_threshold(tool_name)

    if effective_threshold == float("inf"):
        return content

    if len(content) <= effective_threshold:
        return content

    remote_path = f"{STORAGE_DIR}/{tool_use_id}.txt"
    preview, has_more = generate_preview(content, max_chars=config.preview_size)

    if env is not None:
        try:
            if _write_to_sandbox(content, remote_path, env):
                logger.info(
                    "Persisted large tool result: %s (%s, %d chars -> %s)",
                    tool_name, tool_use_id, len(content), remote_path,
                )
                return _build_persisted_message(preview, has_more, len(content), remote_path)
        except Exception as exc:
            logger.warning("Sandbox write failed for %s: %s", tool_use_id, exc)

    logger.info(
        "Inline-truncating large tool result: %s (%d chars, no sandbox write)",
        tool_name, len(content),
    )
    return (
        f"{preview}\n\n"
        f"[Truncated: tool response was {len(content):,} chars. "
        f"Full output could not be saved to sandbox.]"
    )


def enforce_turn_budget(
    tool_messages: list[dict],
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
) -> list[dict]:
    """第三层: 强制执行一轮中所有工具结果的聚合预算。

    如果总字符数超过预算,首先持久化最大的未持久化结果
    (通过沙箱写入),直到总大小在预算内。已持久化的结果会被跳过。

    原地修改列表并返回。
    """
    candidates = []
    total_size = 0
    for i, msg in enumerate(tool_messages):
        content = msg.get("content", "")
        size = len(content)
        total_size += size
        if PERSISTED_OUTPUT_TAG not in content:
            candidates.append((i, size))

    if total_size <= config.turn_budget:
        return tool_messages

    candidates.sort(key=lambda x: x[1], reverse=True)

    for idx, size in candidates:
        if total_size <= config.turn_budget:
            break
        msg = tool_messages[idx]
        content = msg["content"]
        tool_use_id = msg.get("tool_call_id", f"budget_{idx}")

        replacement = maybe_persist_tool_result(
            content=content,
            tool_name=_BUDGET_TOOL_NAME,
            tool_use_id=tool_use_id,
            env=env,
            config=config,
            threshold=0,
        )
        if replacement != content:
            total_size -= size
            total_size += len(replacement)
            tool_messages[idx]["content"] = replacement
            logger.info(
                "Budget enforcement: persisted tool result %s (%d chars)",
                tool_use_id, size,
            )

    return tool_messages
