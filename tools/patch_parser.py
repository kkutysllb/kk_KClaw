#!/usr/bin/env python3
"""
V4A Patch Format Parser

Parses the V4A patch format used by codex, cline, and other coding agents.

V4A Format:
    *** Begin Patch
    *** Update File: path/to/file.py
    @@ optional context hint @@
     context line (space prefix)
    -removed line (minus prefix)
    +added line (plus prefix)
    *** Add File: path/to/new.py
    +new file content
    +line 2
    *** Delete File: path/to/old.py
    *** Move File: old/path.py -> new/path.py
    *** End Patch

用法:
    from tools.patch_parser import parse_v4a_patch, apply_v4a_operations

    operations, error = parse_v4a_patch(patch_content)
    if error:
        print(f"解析错误: {error}")
    else:
        result = apply_v4a_operations(operations, file_ops)
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any
from enum import Enum


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class HunkLine:
    """补丁块中的单行内容。"""
    prefix: str  # ' ', '-', 或 '+'
    content: str


@dataclass
class Hunk:
    """文件中的一个变更组。"""
    context_hint: Optional[str] = None
    lines: List[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    """V4A 补丁中的单个操作。"""
    operation: OperationType
    file_path: str
    new_path: Optional[str] = None  # 用于移动操作
    hunks: List[Hunk] = field(default_factory=list)
    content: Optional[str] = None  # 用于添加文件操作


def parse_v4a_patch(patch_content: str) -> Tuple[List[PatchOperation], Optional[str]]:
    """
    解析 V4A 格式的补丁。

    参数:
        patch_content: V4A 格式的补丁文本

    返回:
        (operations, error_message) 元组
        - 成功时: (operations 列表, None)
        - 失败时: ([], 错误描述)
    """
    lines = patch_content.split('\n')
    operations: List[PatchOperation] = []

    # 查找补丁边界
    start_idx = None
    end_idx = None
    
    for i, line in enumerate(lines):
        if '*** Begin Patch' in line or '***Begin Patch' in line:
            start_idx = i
        elif '*** End Patch' in line or '***End Patch' in line:
            end_idx = i
            break
    
    if start_idx is None:
        # 尝试在没有明确起始标记的情况下解析
        start_idx = -1
    
    if end_idx is None:
        end_idx = len(lines)
    
    # 解析边界之间的操作
    i = start_idx + 1
    current_op: Optional[PatchOperation] = None
    current_hunk: Optional[Hunk] = None

    while i < end_idx:
        line = lines[i]

        # 检查文件操作标记
        update_match = re.match(r'\*\*\*\s*Update\s+File:\s*(.+)', line)
        add_match = re.match(r'\*\*\*\s*Add\s+File:\s*(.+)', line)
        delete_match = re.match(r'\*\*\*\s*Delete\s+File:\s*(.+)', line)
        move_match = re.match(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)', line)
        
        if update_match:
            # 保存之前的操作
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.UPDATE,
                file_path=update_match.group(1).strip()
            )
            current_hunk = None
            
        elif add_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.ADD,
                file_path=add_match.group(1).strip()
            )
            current_hunk = Hunk()
            
        elif delete_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.DELETE,
                file_path=delete_match.group(1).strip()
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None
            
        elif move_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.MOVE,
                file_path=move_match.group(1).strip(),
                new_path=move_match.group(2).strip()
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None
            
        elif line.startswith('@@'):
            # 上下文提示 / 块标记
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)

                # 提取上下文提示
                hint_match = re.match(r'@@\s*(.+?)\s*@@', line)
                hint = hint_match.group(1) if hint_match else None
                current_hunk = Hunk(context_hint=hint)
                
        elif current_op and line:
            # 解析块行
            if current_hunk is None:
                current_hunk = Hunk()
            
            if line.startswith('+'):
                current_hunk.lines.append(HunkLine('+', line[1:]))
            elif line.startswith('-'):
                current_hunk.lines.append(HunkLine('-', line[1:]))
            elif line.startswith(' '):
                current_hunk.lines.append(HunkLine(' ', line[1:]))
            elif line.startswith('\\'):
                # "\ No newline at end of file" 标记 - 跳过
                pass
            else:
                # 作为上下文行处理（隐式空格前缀）
                current_hunk.lines.append(HunkLine(' ', line))
        
        i += 1
    
    # 不要忘记最后一个操作
    if current_op:
        if current_hunk and current_hunk.lines:
            current_op.hunks.append(current_hunk)
        operations.append(current_op)
    
    return operations, None


def apply_v4a_operations(operations: List[PatchOperation],
                          file_ops: Any) -> 'PatchResult':
    """
    使用文件操作接口应用 V4A 补丁操作。

    参数:
        operations: 从 parse_v4a_patch 返回的 PatchOperation 列表
        file_ops: 具有 read_file, write_file 方法的对象

    返回:
        包含所有操作结果的 PatchResult
    """
    # 在此处导入以避免循环导入
    from tools.file_operations import PatchResult
    
    files_modified = []
    files_created = []
    files_deleted = []
    all_diffs = []
    errors = []
    
    for op in operations:
        try:
            if op.operation == OperationType.ADD:
                result = _apply_add(op, file_ops)
                if result[0]:
                    files_created.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"添加文件失败 {op.file_path}: {result[1]}")
                    
            elif op.operation == OperationType.DELETE:
                result = _apply_delete(op, file_ops)
                if result[0]:
                    files_deleted.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"删除文件失败 {op.file_path}: {result[1]}")
                    
            elif op.operation == OperationType.MOVE:
                result = _apply_move(op, file_ops)
                if result[0]:
                    files_modified.append(f"{op.file_path} -> {op.new_path}")
                    all_diffs.append(result[1])
                else:
                    errors.append(f"移动文件失败 {op.file_path}: {result[1]}")
                    
            elif op.operation == OperationType.UPDATE:
                result = _apply_update(op, file_ops)
                if result[0]:
                    files_modified.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"更新文件失败 {op.file_path}: {result[1]}")
                    
        except Exception as e:
            errors.append(f"处理文件出错 {op.file_path}: {str(e)}")
    
    # 对所有修改/创建的文件运行 lint
    lint_results = {}
    for f in files_modified + files_created:
        if hasattr(file_ops, '_check_lint'):
            lint_result = file_ops._check_lint(f)
            lint_results[f] = lint_result.to_dict()
    
    combined_diff = '\n'.join(all_diffs)
    
    if errors:
        return PatchResult(
            success=False,
            diff=combined_diff,
            files_modified=files_modified,
            files_created=files_created,
            files_deleted=files_deleted,
            lint=lint_results if lint_results else None,
            error='; '.join(errors)
        )
    
    return PatchResult(
        success=True,
        diff=combined_diff,
        files_modified=files_modified,
        files_created=files_created,
        files_deleted=files_deleted,
        lint=lint_results if lint_results else None
    )


def _apply_add(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用添加文件操作。"""
    # 从块中提取内容（所有 + 行）
    content_lines = []
    for hunk in op.hunks:
        for line in hunk.lines:
            if line.prefix == '+':
                content_lines.append(line.content)
    
    content = '\n'.join(content_lines)
    
    result = file_ops.write_file(op.file_path, content)
    if result.error:
        return False, result.error
    
    diff = f"--- /dev/null\n+++ b/{op.file_path}\n"
    diff += '\n'.join(f"+{line}" for line in content_lines)
    
    return True, diff


def _apply_delete(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用删除文件操作。"""
    # 首先读取文件以获取 diff
    read_result = file_ops.read_file(op.file_path)

    if read_result.error and "not found" in read_result.error.lower():
        # 文件不存在，无需删除
        return True, f"# {op.file_path} 已删除或不存在"
    
    # 通过底层环境直接使用 shell 命令删除
    rm_result = file_ops._exec(f"rm -f {file_ops._escape_shell_arg(op.file_path)}")
    
    if rm_result.exit_code != 0:
        return False, rm_result.stdout
    
    diff = f"--- a/{op.file_path}\n+++ /dev/null\n# File deleted"
    return True, diff


def _apply_move(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用移动文件操作。"""
    # 使用 shell mv 命令
    mv_result = file_ops._exec(
        f"mv {file_ops._escape_shell_arg(op.file_path)} {file_ops._escape_shell_arg(op.new_path)}"
    )
    
    if mv_result.exit_code != 0:
        return False, mv_result.stdout
    
    diff = f"# Moved: {op.file_path} -> {op.new_path}"
    return True, diff


def _apply_update(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用更新文件操作。"""
    # 读取当前内容
    read_result = file_ops.read_file(op.file_path, limit=10000)
    
    if read_result.error:
        return False, f"无法读取文件: {read_result.error}"
    
    # 解析内容（移除行号）
    current_lines = []
    for line in read_result.content.split('\n'):
        if re.match(r'^\s*\d+\|', line):
            # 行格式: "    123|content"
            parts = line.split('|', 1)
            if len(parts) == 2:
                current_lines.append(parts[1])
            else:
                current_lines.append(line)
        else:
            current_lines.append(line)
    
    current_content = '\n'.join(current_lines)
    
    # 应用每个块
    new_content = current_content

    for hunk in op.hunks:
        # 从上下文和已删除的行构建搜索模式
        search_lines = []
        replace_lines = []
        
        for line in hunk.lines:
            if line.prefix == ' ':
                search_lines.append(line.content)
                replace_lines.append(line.content)
            elif line.prefix == '-':
                search_lines.append(line.content)
            elif line.prefix == '+':
                replace_lines.append(line.content)
        
        if search_lines:
            search_pattern = '\n'.join(search_lines)
            replacement = '\n'.join(replace_lines)
            
            # 使用模糊匹配
            from tools.fuzzy_match import fuzzy_find_and_replace
            new_content, count, error = fuzzy_find_and_replace(
                new_content, search_pattern, replacement, replace_all=False
            )
            
            if error and count == 0:
                # 如果有上下文提示，尝试使用它
                if hunk.context_hint:
                    # 找到上下文提示位置并在附近搜索
                    hint_pos = new_content.find(hunk.context_hint)
                    if hint_pos != -1:
                        # 在提示周围窗口中搜索
                        window_start = max(0, hint_pos - 500)
                        window_end = min(len(new_content), hint_pos + 2000)
                        window = new_content[window_start:window_end]
                        
                        window_new, count, error = fuzzy_find_and_replace(
                            window, search_pattern, replacement, replace_all=False
                        )
                        
                        if count > 0:
                            new_content = new_content[:window_start] + window_new + new_content[window_end:]
                            error = None
                
                if error:
                    return False, f"无法应用块: {error}"
        else:
            # 仅添加的块（无上下文或已删除的行）。
            # 插入到上下文提示指示的位置，或文件末尾。
            insert_text = '\n'.join(replace_lines)
            if hunk.context_hint:
                hint_pos = new_content.find(hunk.context_hint)
                if hint_pos != -1:
                    # 在包含上下文提示的行之后插入
                    eol = new_content.find('\n', hint_pos)
                    if eol != -1:
                        new_content = new_content[:eol + 1] + insert_text + '\n' + new_content[eol + 1:]
                    else:
                        new_content = new_content + '\n' + insert_text
                else:
                    new_content = new_content.rstrip('\n') + '\n' + insert_text + '\n'
            else:
                new_content = new_content.rstrip('\n') + '\n' + insert_text + '\n'
    
    # 写入新内容
    write_result = file_ops.write_file(op.file_path, new_content)
    if write_result.error:
        return False, write_result.error
    
    # 生成 diff
    import difflib
    diff_lines = difflib.unified_diff(
        current_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{op.file_path}",
        tofile=f"b/{op.file_path}"
    )
    diff = ''.join(diff_lines)
    
    return True, diff
