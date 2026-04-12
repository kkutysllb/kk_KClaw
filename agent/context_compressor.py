"""长对话的自动上下文窗口压缩。

自带 OpenAI 客户端的独立类，用于摘要。
使用辅助模型（便宜/快速）来摘要中间轮次，同时
保护头部和尾部上下文。

相比 v1 的改进:
  - 结构化摘要模板（目标、进展、决策、文件、下一步）
  - 迭代式摘要更新（跨多次压缩保留信息）
  - 基于 token 预算的尾部保护，而非固定消息数量
  - LLM 摘要前的工具输出修剪（廉价预处理）
  - 缩放式摘要预算（与压缩内容成比例）
  - 摘要器输入中更丰富的工具调用/结果细节
"""

import logging
import time
from typing import Any, Dict, List, Optional

from agent.auxiliary_client import call_llm
from agent.model_metadata import (
    get_model_context_length,
    estimate_messages_tokens_rough,
)

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = (
    "[上下文压缩] 此对话的早期轮次已被压缩以节省上下文空间。"
    "下面的摘要描述了已完成的工作，当前会话状态可能仍反映"
    "该工作（例如，文件可能已被修改）。使用摘要和当前状态"
    "从上次中断处继续，避免重复工作："
)
LEGACY_SUMMARY_PREFIX = "[上下文摘要]:"

# 摘要输出的最小 token 数
_MIN_SUMMARY_TOKENS = 2000
# 分配给摘要的压缩内容比例
_SUMMARY_RATIO = 0.20
# 摘要 token 的绝对上限（即使上下文窗口很大）
_SUMMARY_TOKENS_CEILING = 12_000

# 修剪旧工具结果时使用的占位符
_PRUNED_TOOL_PLACEHOLDER = "[旧工具输出已清除以节省上下文空间]"

# 每个 token 的粗略字符数估算
_CHARS_PER_TOKEN = 4
_SUMMARY_FAILURE_COOLDOWN_SECONDS = 600


class ContextCompressor:
    """当接近模型的上下文限制时压缩对话上下文。

    算法:
      1. 修剪旧工具结果（廉价，无 LLM 调用）
      2. 保护头部消息（系统提示词 + 首次交换）
      3. 基于 token 预算保护尾部消息（最近的约 20K token）
      4. 使用结构化 LLM 提示词摘要中间轮次
      5. 在后续压缩时，迭代更新前一次的摘要
    """

    def __init__(
        self,
        model: str,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_last_n: int = 20,
        summary_target_ratio: float = 0.20,
        quiet_mode: bool = False,
        summary_model_override: str = None,
        base_url: str = "",
        api_key: str = "",
        config_context_length: int | None = None,
        provider: str = "",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.provider = provider
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.summary_target_ratio = max(0.10, min(summary_target_ratio, 0.80))
        self.quiet_mode = quiet_mode

        self.context_length = get_model_context_length(
            model, base_url=base_url, api_key=api_key,
            config_context_length=config_context_length,
            provider=provider,
        )
        self.threshold_tokens = int(self.context_length * threshold_percent)
        self.compression_count = 0

        # 从阈值（而非总上下文）派生 token 预算
        target_tokens = int(self.threshold_tokens * self.summary_target_ratio)
        self.tail_token_budget = target_tokens
        self.max_summary_tokens = min(
            int(self.context_length * 0.05), _SUMMARY_TOKENS_CEILING,
        )

        if not quiet_mode:
            logger.info(
                "Context compressor initialized: model=%s context_length=%d "
                "threshold=%d (%.0f%%) target_ratio=%.0f%% tail_budget=%d "
                "provider=%s base_url=%s",
                model, self.context_length, self.threshold_tokens,
                threshold_percent * 100, self.summary_target_ratio * 100,
                self.tail_token_budget,
                provider or "none", base_url or "none",
            )
        self._context_probed = False  # 从上下文错误降级后为 True

        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0

        self.summary_model = summary_model_override or ""

        # 存储前一次压缩的摘要用于迭代更新
        self._previous_summary: Optional[str] = None
        self._summary_failure_cooldown_until: float = 0.0

    def update_from_response(self, usage: Dict[str, Any]):
        """从 API 响应更新跟踪的 token 使用量。"""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """检查上下文是否超过压缩阈值。"""
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return tokens >= self.threshold_tokens

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """使用粗略估算的快速预检（在 API 调用之前）。"""
        rough_estimate = estimate_messages_tokens_rough(messages)
        return rough_estimate >= self.threshold_tokens

    def get_status(self) -> Dict[str, Any]:
        """获取当前压缩状态用于显示/日志。"""
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": min(100, (self.last_prompt_tokens / self.context_length * 100)) if self.context_length else 0,
            "compression_count": self.compression_count,
        }

    # ------------------------------------------------------------------
    # 工具输出修剪（廉价预处理，无 LLM 调用）
    # ------------------------------------------------------------------

    def _prune_old_tool_results(
        self, messages: List[Dict[str, Any]], protect_tail_count: int,
        protect_tail_tokens: int | None = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """用短占位符替换旧工具结果内容。

        从末尾向前遍历，保护落在 ``protect_tail_tokens`` 内的
        最近消息（当提供时）或最后 ``protect_tail_count`` 条消息
        （向后兼容默认值）。当两者都提供时，token 预算优先，
        消息数作为硬性最低下限。

        返回 (pruned_messages, pruned_count)。
        """
        if not messages:
            return messages, 0

        result = [m.copy() for m in messages]
        pruned = 0

        # 确定修剪边界
        if protect_tail_tokens is not None and protect_tail_tokens > 0:
            # 基于 token 预算的方法：向前累积 token
            accumulated = 0
            boundary = len(result)
            min_protect = min(protect_tail_count, len(result) - 1)
            for i in range(len(result) - 1, -1, -1):
                msg = result[i]
                content_len = len(msg.get("content") or "")
                msg_tokens = content_len // _CHARS_PER_TOKEN + 10
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        args = tc.get("function", {}).get("arguments", "")
                        msg_tokens += len(args) // _CHARS_PER_TOKEN
                if accumulated + msg_tokens > protect_tail_tokens and (len(result) - i) >= min_protect:
                    boundary = i
                    break
                accumulated += msg_tokens
                boundary = i
            prune_boundary = max(boundary, len(result) - min_protect)
        else:
            prune_boundary = len(result) - protect_tail_count

        for i in range(prune_boundary):
            msg = result[i]
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not content or content == _PRUNED_TOOL_PLACEHOLDER:
                continue
            # 仅当内容超过 200 字符时才修剪
            if len(content) > 200:
                result[i] = {**msg, "content": _PRUNED_TOOL_PLACEHOLDER}
                pruned += 1

        return result, pruned

    # ------------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------------

    def _compute_summary_budget(self, turns_to_summarize: List[Dict[str, Any]]) -> int:
        """根据压缩内容量缩放摘要 token 预算。

        最大值随模型的上下文窗口缩放（上下文的 5%，
        上限为 ``_SUMMARY_TOKENS_CEILING``），因此大上下文模型
        可以获得更丰富的摘要，而不是被硬性限制在 8K token。
        """
        content_tokens = estimate_messages_tokens_rough(turns_to_summarize)
        budget = int(content_tokens * _SUMMARY_RATIO)
        return max(_MIN_SUMMARY_TOKENS, min(budget, self.max_summary_tokens))

    # 摘要器输入的截断限制。这些限制了每条消息摘要模型看到
    # 的内容量 — 预算是摘要模型的上下文窗口，而非主模型的。
    _CONTENT_MAX = 6000       # 每条消息体的总字符数
    _CONTENT_HEAD = 4000      # 从开头保留的字符数
    _CONTENT_TAIL = 1500      # 从末尾保留的字符数
    _TOOL_ARGS_MAX = 1500     # 工具调用参数字符数
    _TOOL_ARGS_HEAD = 1200    # 工具参数开头保留的字符数

    def _serialize_for_summary(self, turns: List[Dict[str, Any]]) -> str:
        """将对话轮次序列化为带标签的文本供摘要器使用。

        包含工具调用参数和结果内容（每条消息最多
        ``_CONTENT_MAX`` 字符），以便摘要器可以保留
        文件路径、命令和输出等具体细节。
        """
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""

            # 工具结果：保留足够的内容供摘要器使用
            if role == "tool":
                tool_id = msg.get("tool_call_id", "")
                if len(content) > self._CONTENT_MAX:
                    content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
                parts.append(f"[TOOL RESULT {tool_id}]: {content}")
                continue

            # 助手消息：包含工具调用名称和参数
            if role == "assistant":
                if len(content) > self._CONTENT_MAX:
                    content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tc_parts = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            name = fn.get("name", "?")
                            args = fn.get("arguments", "")
                            # 截断长参数但保留足够的上下文
                            if len(args) > self._TOOL_ARGS_MAX:
                                args = args[:self._TOOL_ARGS_HEAD] + "..."
                            tc_parts.append(f"  {name}({args})")
                        else:
                            fn = getattr(tc, "function", None)
                            name = getattr(fn, "name", "?") if fn else "?"
                            tc_parts.append(f"  {name}(...)")
                    content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
                parts.append(f"[ASSISTANT]: {content}")
                continue

            # 用户和其他角色
            if len(content) > self._CONTENT_MAX:
                content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
            parts.append(f"[{role.upper()}]: {content}")

        return "\n\n".join(parts)

    def _generate_summary(self, turns_to_summarize: List[Dict[str, Any]]) -> Optional[str]:
        """生成对话轮次的结构化摘要。

        使用结构化模板（目标、进展、决策、文件、下一步），
        灵感来自 Pi-mono 和 OpenCode。当存在前一次摘要时，
        生成迭代更新而不是从头摘要。

        如果所有尝试失败则返回 None — 调用者应丢弃中间轮次
        而不生成摘要，而不是注入无用的占位符。
        """
        now = time.monotonic()
        if now < self._summary_failure_cooldown_until:
            logger.debug(
                "Skipping context summary during cooldown (%.0fs remaining)",
                self._summary_failure_cooldown_until - now,
            )
            return None

        summary_budget = self._compute_summary_budget(turns_to_summarize)
        content_to_summarize = self._serialize_for_summary(turns_to_summarize)

        if self._previous_summary:
            # 迭代更新：保留现有信息，添加新进展
            prompt = f"""You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{self._previous_summary}

NEW TURNS TO INCORPORATE:
{content_to_summarize}

Update the summary using this exact structure. PRESERVE all existing information that is still relevant. ADD new progress. Move items from "In Progress" to "Done" when completed. Remove information only if it is clearly obsolete.

## Goal
[What the user is trying to accomplish — preserve from previous summary, update if goal evolved]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions — accumulate across compactions]

## Progress
### Done
[Completed work — include specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why they were made]

## Relevant Files
[Files read, modified, or created — with brief note on each. Accumulate across compactions.]

## Next Steps
[What needs to happen next to continue the work]

## Critical Context
[Any specific values, error messages, configuration details, or data that would be lost without explicit preservation]

## Tools & Patterns
[Which tools were used, how they were used effectively, and any tool-specific discoveries. Accumulate across compactions.]

Target ~{summary_budget} tokens. Be specific — include file paths, command outputs, error messages, and concrete values rather than vague descriptions.

Write only the summary body. Do not include any preamble or prefix."""
        else:
            # 首次压缩：从头摘要
            prompt = f"""Create a structured handoff summary for a later assistant that will continue this conversation after earlier turns are compacted.

TURNS TO SUMMARIZE:
{content_to_summarize}

Use this exact structure:

## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Progress
### Done
[Completed work — include specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why they were made]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Next Steps
[What needs to happen next to continue the work]

## Critical Context
[Any specific values, error messages, configuration details, or data that would be lost without explicit preservation]

## Tools & Patterns
[Which tools were used, how they were used effectively, and any tool-specific discoveries (e.g., preferred flags, working invocations, successful command patterns)]

Target ~{summary_budget} tokens. Be specific — include file paths, command outputs, error messages, and concrete values rather than vague descriptions. The goal is to prevent the next assistant from repeating work or losing important details.

Write only the summary body. Do not include any preamble or prefix."""

        try:
            call_kwargs = {
                "task": "compression",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": summary_budget * 2,
                # timeout resolved from auxiliary.compression.timeout config by call_llm
            }
            if self.summary_model:
                call_kwargs["model"] = self.summary_model
            response = call_llm(**call_kwargs)
            content = response.choices[0].message.content
            # 处理内容不是字符串的情况（如 llama.cpp 返回的 dict）
            if not isinstance(content, str):
                content = str(content) if content else ""
            summary = content.strip()
            # 存储用于下次压缩的迭代更新
            self._previous_summary = summary
            self._summary_failure_cooldown_until = 0.0
            return self._with_summary_prefix(summary)
        except RuntimeError:
            self._summary_failure_cooldown_until = time.monotonic() + _SUMMARY_FAILURE_COOLDOWN_SECONDS
            logging.warning("上下文压缩：没有可用的提供者进行摘要。"
                            "中间轮次将在没有摘要的情况下被丢弃，"
                            "持续 %d 秒。",
                            _SUMMARY_FAILURE_COOLDOWN_SECONDS)
            return None
        except Exception as e:
            self._summary_failure_cooldown_until = time.monotonic() + _SUMMARY_FAILURE_COOLDOWN_SECONDS
            logging.warning(
                "生成上下文摘要失败：%s。"
                "摘要尝试暂停 %d 秒。",
                e,
                _SUMMARY_FAILURE_COOLDOWN_SECONDS,
            )
            return None

    @staticmethod
    def _with_summary_prefix(summary: str) -> str:
        """将摘要文本规范化为当前的压缩交接格式。"""
        text = (summary or "").strip()
        for prefix in (LEGACY_SUMMARY_PREFIX, SUMMARY_PREFIX):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                break
        return f"{SUMMARY_PREFIX}\n{text}" if text else SUMMARY_PREFIX

    # ------------------------------------------------------------------
    # 工具调用/工具结果配对完整性助手
    # ------------------------------------------------------------------

    @staticmethod
    def _get_tool_call_id(tc) -> str:
        """从工具调用条目（dict 或 SimpleNamespace）中提取调用 ID。"""
        if isinstance(tc, dict):
            return tc.get("id", "")
        return getattr(tc, "id", "") or ""

    def _sanitize_tool_pairs(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """修复压缩后孤立的 tool_call / tool_result 配对。

        两种故障模式:
        1. 工具*结果*引用的 call_id 对应的助手 tool_call 已被
           移除（摘要/截断）。API 会以 "No tool call found for
           function call output with call_id ..." 拒绝。
        2. 助手消息有 tool_calls 但对应的结果已被丢弃。
           API 会拒绝，因为每个 tool_call 必须跟随一个
           匹配 call_id 的工具结果。

        此方法移除孤立的结果并为孤立的调用插入存根结果，
        确保消息列表始终格式正确。
        """
        surviving_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = self._get_tool_call_id(tc)
                    if cid:
                        surviving_call_ids.add(cid)

        result_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_call_ids.add(cid)

        # 1. 移除 call_id 没有匹配助手 tool_call 的工具结果
        orphaned_results = result_call_ids - surviving_call_ids
        if orphaned_results:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned_results)
            ]
            if not self.quiet_mode:
                logger.info("压缩清理器：移除了 %d 个孤立工具结果", len(orphaned_results))

        # 2. 为结果已被丢弃的助手 tool_calls 添加存根结果
        missing_results = surviving_call_ids - result_call_ids
        if missing_results:
            patched: List[Dict[str, Any]] = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        cid = self._get_tool_call_id(tc)
                        if cid in missing_results:
                            patched.append({
                                "role": "tool",
                                "content": "[来自早期对话的结果 — 参见上方上下文摘要]",
                                "tool_call_id": cid,
                            })
            messages = patched
            if not self.quiet_mode:
                logger.info("压缩清理器：添加了 %d 个存根工具结果", len(missing_results))

        return messages

    def _align_boundary_forward(self, messages: List[Dict[str, Any]], idx: int) -> int:
        """将压缩起始边界向前推过任何孤立工具结果。

        如果 ``messages[idx]`` 是工具结果，向前滑动直到遇到
        非工具消息，以免在工具结果组中间开始摘要区域。
        """
        while idx < len(messages) and messages[idx].get("role") == "tool":
            idx += 1
        return idx

    def _align_boundary_backward(self, messages: List[Dict[str, Any]], idx: int) -> int:
        """将压缩结束边界向后拉以避免拆分
        tool_call / 结果组。

        如果边界落在工具结果组中间（即 ``idx`` 前面
        有连续的工具消息），向后遍历所有这些消息找到父助手消息。
        如果找到，将边界移到助手消息之前，这样整个
        助手 + tool_results 组都被包含在摘要区域中，
        而不是被拆分（当 ``_sanitize_tool_pairs`` 移除孤立的
        尾部结果时会导致静默数据丢失）。
        """
        if idx <= 0 or idx >= len(messages):
            return idx
        # 向后遍历连续的工具结果
        check = idx - 1
        while check >= 0 and messages[check].get("role") == "tool":
            check -= 1
        # 如果到达的是带 tool_calls 的父助手消息，将边界
        # 移到它之前，使整个组一起被摘要。
        if check >= 0 and messages[check].get("role") == "assistant" and messages[check].get("tool_calls"):
            idx = check
        return idx

    # ------------------------------------------------------------------
    # 基于 token 预算的尾部保护
    # ------------------------------------------------------------------

    def _find_tail_cut_by_tokens(
        self, messages: List[Dict[str, Any]], head_end: int,
        token_budget: int | None = None,
    ) -> int:
        """从消息末尾向前遍历，累积 token 直到达到预算。
        返回尾部开始的索引。

        ``token_budget`` 默认为 ``self.tail_token_budget``，由
        ``summary_target_ratio * context_length`` 派生，因此它会
        随模型的上下文窗口自动缩放。

        Token 预算是主要标准。硬性最少 3 条消息始终受保护，
        但预算可以超出最多 1.5 倍以避免在超大消息
        （工具输出、文件读取等）内部切割。如果即使最少的 3 条消息
        也超过 1.5 倍预算，切割点设在头部之后以确保压缩仍能执行。

        绝不在 tool_call/result 组内部切割。
        """
        if token_budget is None:
            token_budget = self.tail_token_budget
        n = len(messages)
        # 硬性最低：始终在尾部保留至少 3 条消息
        min_tail = min(3, n - head_end - 1) if n - head_end > 1 else 0
        soft_ceiling = int(token_budget * 1.5)
        accumulated = 0
        cut_idx = n  # start from beyond the end

        for i in range(n - 1, head_end - 1, -1):
            msg = messages[i]
            content = msg.get("content") or ""
            msg_tokens = len(content) // _CHARS_PER_TOKEN + 10  # +10 for role/metadata
            # 在估算中包含工具调用参数
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    msg_tokens += len(args) // _CHARS_PER_TOKEN
            # 一旦超过软上限即停止（除非未达到最低消息数）
            if accumulated + msg_tokens > soft_ceiling and (n - i) >= min_tail:
                break
            accumulated += msg_tokens
            cut_idx = i

        # 确保至少保护 min_tail 条消息
        fallback_cut = n - min_tail
        if cut_idx > fallback_cut:
            cut_idx = fallback_cut

        # 如果 token 预算会保护所有内容（小对话），
        # 强制在头部之后切割以便压缩仍能移除中间轮次。
        if cut_idx <= head_end:
            cut_idx = max(fallback_cut, head_end + 1)

        # 对齐以避免拆分工具组
        cut_idx = self._align_boundary_backward(messages, cut_idx)

        return max(cut_idx, head_end + 1)

    # ------------------------------------------------------------------
    # 主压缩入口
    # ------------------------------------------------------------------

    def compress(self, messages: List[Dict[str, Any]], current_tokens: int = None) -> List[Dict[str, Any]]:
        """通过摘要中间轮次压缩对话消息。

        算法:
          1. 修剪旧工具结果（廉价预处理，无 LLM 调用）
          2. 保护头部消息（系统提示词 + 首次交换）
          3. 基于 token 预算找到尾部边界（约 20K token 的最近上下文）
          4. 使用结构化 LLM 提示词摘要中间轮次
          5. 重新压缩时，迭代更新前一次的摘要

        压缩后，孤立的 tool_call / tool_result 配对会被清理，
        确保 API 永远不会收到不匹配的 ID。
        """
        n_messages = len(messages)
        # 最低需要头部 + 3 条尾部消息（token 预算决定实际尾部大小）
        _min_for_compress = self.protect_first_n + 3 + 1
        if n_messages <= _min_for_compress:
            if not self.quiet_mode:
                logger.warning(
                    "无法压缩：仅有 %d 条消息（需要 > %d）",
                    n_messages, _min_for_compress,
                )
            return messages

        display_tokens = current_tokens if current_tokens else self.last_prompt_tokens or estimate_messages_tokens_rough(messages)

        # 阶段 1：修剪旧工具结果（廉价，无 LLM 调用）
        messages, pruned_count = self._prune_old_tool_results(
            messages, protect_tail_count=self.protect_last_n,
            protect_tail_tokens=self.tail_token_budget,
        )
        if pruned_count and not self.quiet_mode:
            logger.info("预压缩：修剪了 %d 个旧工具结果", pruned_count)

        # 阶段 2：确定边界
        compress_start = self.protect_first_n
        compress_start = self._align_boundary_forward(messages, compress_start)

        # 使用基于 token 预算的尾部保护而非固定消息数
        compress_end = self._find_tail_cut_by_tokens(messages, compress_start)

        if compress_start >= compress_end:
            return messages

        turns_to_summarize = messages[compress_start:compress_end]

        if not self.quiet_mode:
            logger.info(
                "上下文压缩触发（%d token >= %d 阈值）",
                display_tokens,
                self.threshold_tokens,
            )
            logger.info(
                "模型上下文限制：%d token（%.0f%% = %d）",
                self.context_length,
                self.threshold_percent * 100,
                self.threshold_tokens,
            )
            tail_msgs = n_messages - compress_end
            logger.info(
                "摘要轮次 %d-%d（%d 轮），保护 %d 头部 + %d 尾部消息",
                compress_start + 1,
                compress_end,
                len(turns_to_summarize),
                compress_start,
                tail_msgs,
            )

        # 阶段 3：生成结构化摘要
        summary = self._generate_summary(turns_to_summarize)

        # 阶段 4：组装压缩后的消息列表
        compressed = []
        for i in range(compress_start):
            msg = messages[i].copy()
            if i == 0 and msg.get("role") == "system" and self.compression_count == 0:
                msg["content"] = (
                    (msg.get("content") or "")
                    + "\n\n[注意：部分早期对话轮次已被压缩为交接摘要以保留上下文空间。当前会话状态可能仍反映早期工作，因此请基于该摘要和状态继续，而不是重新执行工作。]"
                )
            compressed.append(msg)

        _merge_summary_into_tail = False
        if summary:
            last_head_role = messages[compress_start - 1].get("role", "user") if compress_start > 0 else "user"
            first_tail_role = messages[compress_end].get("role", "user") if compress_end < n_messages else "user"
            # 选择一个避免与两端邻居同角色冲突的角色。
            # 优先级：避免与头部（已提交）冲突，然后是尾部。
            if last_head_role in ("assistant", "tool"):
                summary_role = "user"
            else:
                summary_role = "assistant"
            # 如果选择的角色与尾部冲突且翻转不会与头部冲突，
            # 则翻转角色。
            if summary_role == first_tail_role:
                flipped = "assistant" if summary_role == "user" else "user"
                if flipped != last_head_role:
                    summary_role = flipped
                else:
                    # 两种角色都会创建连续的同角色消息
                    # （例如 head=assistant, tail=user — 两种角色都不行）。
                    # 将摘要合并到第一条尾部消息中，
                    # 而不是插入会破坏交替的独立消息。
                    _merge_summary_into_tail = True
            if not _merge_summary_into_tail:
                compressed.append({"role": summary_role, "content": summary})
        else:
            if not self.quiet_mode:
                logger.debug("没有可用的摘要模型 — 中间轮次已丢弃但无摘要")

        for i in range(compress_end, n_messages):
            msg = messages[i].copy()
            if _merge_summary_into_tail and i == compress_end:
                original = msg.get("content") or ""
                msg["content"] = summary + "\n\n" + original
                _merge_summary_into_tail = False
            compressed.append(msg)

        self.compression_count += 1

        compressed = self._sanitize_tool_pairs(compressed)

        if not self.quiet_mode:
            new_estimate = estimate_messages_tokens_rough(compressed)
            saved_estimate = display_tokens - new_estimate
            logger.info(
                "压缩完成：%d -> %d 条消息（约节省 %d token）",
                n_messages,
                len(compressed),
                saved_estimate,
            )
            logger.info("压缩 #%d 完成", self.compression_count)

        return compressed
