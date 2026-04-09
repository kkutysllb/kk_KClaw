#!/usr/bin/env python3
"""
文件操作的模糊匹配模块

实现多策略匹配链，以鲁棒地查找和替换文本，
适应 LLM 生成代码中常见的空白、缩进和转义变化。

8 策略链（灵感来自 OpenCode），按顺序尝试：
1. 精确匹配 - 直接字符串比较
2. 行裁剪 - 去除每行前导/尾随空白
3. 空白规范化 - 将多个空格/制表符折叠为单个空格
4. 缩进灵活 - 完全忽略缩进差异
5. 转义规范化 - 将 \\n 字面量转换为实际换行
6. 边界裁剪 - 仅裁剪首尾行空白
7. 块锚定 - 匹配首行+尾行，对中间部分使用相似度
8. 上下文感知 - 50% 行相似度阈值

通过 replace_all 标志处理多occurrence匹配。

用法：
    from tools.fuzzy_match import fuzzy_find_and_replace

    new_content, match_count, error = fuzzy_find_and_replace(
        content="def foo():\\n    pass",
        old_string="def foo():",
        new_string="def bar():",
        replace_all=False
    )
"""

import re
from typing import Tuple, Optional, List, Callable
from difflib import SequenceMatcher

UNICODE_MAP = {
    "\u201c": '"', "\u201d": '"',  # 智能双引号
    "\u2018": "'", "\u2019": "'",  # 智能单引号
    "\u2014": "--", "\u2013": "-", # em/en 破折号
    "\u2026": "...", "\u00a0": " ", # 省略号和非中断空格
}

def _unicode_normalize(text: str) -> str:
    """将 Unicode 字符规范化为其标准 ASCII 等价物。"""
    for char, repl in UNICODE_MAP.items():
        text = text.replace(char, repl)
    return text


def fuzzy_find_and_replace(content: str, old_string: str, new_string: str,
                            replace_all: bool = False) -> Tuple[str, int, Optional[str]]:
    """
    使用越来越模糊的匹配策略链查找和替换文本。

    参数:
        content: 要搜索的文件内容
        old_string: 要查找的文本
        new_string: 替换文本
        replace_all: 如果为 True，替换所有出现；如果为 False，需要唯一性

    返回:
        (new_content, match_count, error_message) 元组
        - 如果成功：(修改后的内容，替换数量，None)
        - 如果失败：(原始内容，0，错误描述)
    """
    if not old_string:
        return content, 0, "old_string 不能为空"

    if old_string == new_string:
        return content, 0, "old_string 和 new_string 相同"

    # 按顺序尝试每个匹配策略
    strategies: List[Tuple[str, Callable]] = [
        ("exact", _strategy_exact),
        ("line_trimmed", _strategy_line_trimmed),
        ("whitespace_normalized", _strategy_whitespace_normalized),
        ("indentation_flexible", _strategy_indentation_flexible),
        ("escape_normalized", _strategy_escape_normalized),
        ("trimmed_boundary", _strategy_trimmed_boundary),
        ("block_anchor", _strategy_block_anchor),
        ("context_aware", _strategy_context_aware),
    ]

    for strategy_name, strategy_fn in strategies:
        matches = strategy_fn(content, old_string)

        if matches:
            # 使用此策略找到匹配
            if len(matches) > 1 and not replace_all:
                return content, 0, (
                    f"找到 {len(matches)} 个 old_string 的匹配。"
                    f"提供更多上下文使其唯一，或使用 replace_all=True。"
                )

            # 执行替换
            new_content = _apply_replacements(content, matches, new_string)
            return new_content, len(matches), None

    # 没有策略找到匹配
    return content, 0, "无法在文件中找到 old_string 的匹配"


def _apply_replacements(content: str, matches: List[Tuple[int, int]], new_string: str) -> str:
    """
    在给定位置执行替换。

    参数:
        content: 原始内容
        matches: 要替换的 (start, end) 位置列表
        new_string: 替换文本

    返回:
        应用替换后的内容
    """
    # 按位置排序匹配（从后到前替换）
    # 这保留了较早匹配的位置
    sorted_matches = sorted(matches, key=lambda x: x[0], reverse=True)

    result = content
    for start, end in sorted_matches:
        result = result[:start] + new_string + result[end:]

    return result


# =============================================================================
# 匹配策略
# =============================================================================

def _strategy_exact(content: str, pattern: str) -> List[Tuple[int, int]]:
    """策略 1：精确字符串匹配。"""
    matches = []
    start = 0
    while True:
        pos = content.find(pattern, start)
        if pos == -1:
            break
        matches.append((pos, pos + len(pattern)))
        start = pos + 1
    return matches


def _strategy_line_trimmed(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 2：逐行空白裁剪匹配。

    匹配前去除每行前导/尾随空白。
    """
    # 通过裁剪每行来规范化模式和内容
    pattern_lines = [line.strip() for line in pattern.split('\n')]
    pattern_normalized = '\n'.join(pattern_lines)

    content_lines = content.split('\n')
    content_normalized_lines = [line.strip() for line in content_lines]

    # 从规范化位置构建回原始位置的映射
    return _find_normalized_matches(
        content, content_lines, content_normalized_lines,
        pattern, pattern_normalized
    )


def _strategy_whitespace_normalized(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 3：将多个空白折叠为单个空格。
    """
    def normalize(s):
        # 将多个空格/制表符折叠为单个空格，保留换行
        return re.sub(r'[ \t]+', ' ', s)

    pattern_normalized = normalize(pattern)
    content_normalized = normalize(content)

    # 在规范化内容中查找，映射回原始内容
    matches_in_normalized = _strategy_exact(content_normalized, pattern_normalized)

    if not matches_in_normalized:
        return []

    # 将位置映射回原始内容
    return _map_normalized_positions(content, content_normalized, matches_in_normalized)


def _strategy_indentation_flexible(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 4：完全忽略缩进差异。

    匹配前去除所有前导空白。
    """
    content_lines = content.split('\n')
    content_stripped_lines = [line.lstrip() for line in content_lines]
    pattern_lines = [line.lstrip() for line in pattern.split('\n')]

    return _find_normalized_matches(
        content, content_lines, content_stripped_lines,
        pattern, '\n'.join(pattern_lines)
    )


def _strategy_escape_normalized(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 5：将转义序列转换为实际字符。

    处理 \\n -> 换行、\\t -> 制表符等。
    """
    def unescape(s):
        # 转换常见转义序列
        return s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')

    pattern_unescaped = unescape(pattern)

    if pattern_unescaped == pattern:
        # 没有要转换的转义，跳过此策略
        return []

    return _strategy_exact(content, pattern_unescaped)


def _strategy_trimmed_boundary(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 6：仅裁剪首尾行的空白。

    当模式边界有空白差异时有用。
    """
    pattern_lines = pattern.split('\n')
    if not pattern_lines:
        return []

    # 仅裁剪首尾行
    pattern_lines[0] = pattern_lines[0].strip()
    if len(pattern_lines) > 1:
        pattern_lines[-1] = pattern_lines[-1].strip()

    modified_pattern = '\n'.join(pattern_lines)

    content_lines = content.split('\n')

    # 在内容中搜索匹配块
    matches = []
    pattern_line_count = len(pattern_lines)

    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i:i + pattern_line_count]

        # 裁剪此块的首尾
        check_lines = block_lines.copy()
        check_lines[0] = check_lines[0].strip()
        if len(check_lines) > 1:
            check_lines[-1] = check_lines[-1].strip()

        if '\n'.join(check_lines) == modified_pattern:
            # 找到匹配 - 计算原始位置
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))

    return matches


def _strategy_block_anchor(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 7：通过首尾行锚定匹配。
    使用宽松阈值和 unicode 规范化进行调整。
    """
    # 规范化两个字符串用于比较，同时保留原始内容用于偏移计算
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)

    pattern_lines = norm_pattern.split('\n')
    if len(pattern_lines) < 2:
        return []

    first_line = pattern_lines[0].strip()
    last_line = pattern_lines[-1].strip()

    # 使用规范化行进行匹配逻辑
    norm_content_lines = norm_content.split('\n')
    # 但使用原始行计算 start/end 位置以防止索引偏移
    orig_content_lines = content.split('\n')

    pattern_line_count = len(pattern_lines)

    potential_matches = []
    for i in range(len(norm_content_lines) - pattern_line_count + 1):
        if (norm_content_lines[i].strip() == first_line and
            norm_content_lines[i + pattern_line_count - 1].strip() == last_line):
            potential_matches.append(i)

    matches = []
    candidate_count = len(potential_matches)

    # 阈值逻辑：唯一匹配 0.10（最大灵活性），多个候选 0.30
    threshold = 0.10 if candidate_count == 1 else 0.30

    for i in potential_matches:
        if pattern_line_count <= 2:
            similarity = 1.0
        else:
            # 比较规范化中间部分
            content_middle = '\n'.join(norm_content_lines[i+1:i+pattern_line_count-1])
            pattern_middle = '\n'.join(pattern_lines[1:-1])
            similarity = SequenceMatcher(None, content_middle, pattern_middle).ratio()

        if similarity >= threshold:
            # 使用原始行计算位置以确保文件中正确的字符偏移
            start_pos, end_pos = _calculate_line_positions(
                orig_content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))

    return matches


def _strategy_context_aware(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 8：逐行相似度，50% 阈值。

    查找至少 50% 行具有高相似度的块。
    """
    pattern_lines = pattern.split('\n')
    content_lines = content.split('\n')

    if not pattern_lines:
        return []

    matches = []
    pattern_line_count = len(pattern_lines)

    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i:i + pattern_line_count]

        # 计算逐行相似度
        high_similarity_count = 0
        for p_line, c_line in zip(pattern_lines, block_lines):
            sim = SequenceMatcher(None, p_line.strip(), c_line.strip()).ratio()
            if sim >= 0.80:
                high_similarity_count += 1

        # 需要至少 50% 的行具有高相似度
        if high_similarity_count >= len(pattern_lines) * 0.5:
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))

    return matches


# =============================================================================
# 辅助函数
# =============================================================================

def _calculate_line_positions(content_lines: List[str], start_line: int,
                              end_line: int, content_length: int) -> Tuple[int, int]:
    """从行索引计算开始和结束字符位置。

    参数:
        content_lines: 行列表（不带换行符）
        start_line: 起始行索引（0 基）
        end_line: 结束行索引（独占，0 基）
        content_length: 原始内容字符串的总长度

    返回:
        原始内容中的 (start_pos, end_pos) 元组
    """
    start_pos = sum(len(line) + 1 for line in content_lines[:start_line])
    end_pos = sum(len(line) + 1 for line in content_lines[:end_line]) - 1
    if end_pos >= content_length:
        end_pos = content_length
    return start_pos, end_pos


def _find_normalized_matches(content: str, content_lines: List[str],
                              content_normalized_lines: List[str],
                              pattern: str, pattern_normalized: str) -> List[Tuple[int, int]]:
    """
    在规范化内容中找到匹配并映射回原始位置。

    参数:
        content: 原始内容字符串
        content_lines: 按行分割的原始内容
        content_normalized_lines: 规范化后的内容行
        pattern: 原始模式
        pattern_normalized: 规范化后的模式

    返回:
        原始内容中的 (start, end) 位置列表
    """
    pattern_norm_lines = pattern_normalized.split('\n')
    num_pattern_lines = len(pattern_norm_lines)

    matches = []

    for i in range(len(content_normalized_lines) - num_pattern_lines + 1):
        # 检查此块是否匹配
        block = '\n'.join(content_normalized_lines[i:i + num_pattern_lines])

        if block == pattern_normalized:
            # 找到匹配 - 计算原始位置
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + num_pattern_lines, len(content)
            )
            matches.append((start_pos, end_pos))

    return matches


def _map_normalized_positions(original: str, normalized: str,
                               normalized_matches: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """
    将规范化字符串中的位置映射回原始字符串。

    这是用于空白规范化的尽力映射。
    """
    if not normalized_matches:
        return []

    # 从规范化到原始构建字符映射
    orig_to_norm = []  # orig_to_norm[i] = 规范化中的位置

    orig_idx = 0
    norm_idx = 0

    while orig_idx < len(original) and norm_idx < len(normalized):
        if original[orig_idx] == normalized[norm_idx]:
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            norm_idx += 1
        elif original[orig_idx] in ' \t' and normalized[norm_idx] == ' ':
            # 原始有空格/制表符，规范化为空格
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            # 还没 advance norm_idx - 直到所有空白被消耗
            if orig_idx < len(original) and original[orig_idx] not in ' \t':
                norm_idx += 1
        elif original[orig_idx] in ' \t':
            # 原始中的额外空白
            orig_to_norm.append(norm_idx)
            orig_idx += 1
        else:
            # 不匹配 - 不应该发生在我们的规范化中
            orig_to_norm.append(norm_idx)
            orig_idx += 1

    # 填充剩余部分
    while orig_idx < len(original):
        orig_to_norm.append(len(normalized))
        orig_idx += 1

    # 反向映射：对于每个规范化位置，找到原始范围
    norm_to_orig_start = {}
    norm_to_orig_end = {}

    for orig_pos, norm_pos in enumerate(orig_to_norm):
        if norm_pos not in norm_to_orig_start:
            norm_to_orig_start[norm_pos] = orig_pos
        norm_to_orig_end[norm_pos] = orig_pos

    # 映射匹配
    original_matches = []
    for norm_start, norm_end in normalized_matches:
        # 找到原始开始
        if norm_start in norm_to_orig_start:
            orig_start = norm_to_orig_start[norm_start]
        else:
            # 找到最近的
            orig_start = min(i for i, n in enumerate(orig_to_norm) if n >= norm_start)

        # 找到原始结束
        if norm_end - 1 in norm_to_orig_end:
            orig_end = norm_to_orig_end[norm_end - 1] + 1
        else:
            orig_end = orig_start + (norm_end - norm_start)

        # 扩展以包含被规范化的尾随空白
        while orig_end < len(original) and original[orig_end] in ' \t':
            orig_end += 1

        original_matches.append((orig_start, min(orig_end, len(original))))

    return original_matches
