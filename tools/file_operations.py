#!/usr/bin/env python3
"""
文件操作模块

提供跨所有终端后端 (本地、docker、singularity、ssh、modal、daytona)
工作的文件操作能力 (读取、写入、修补、搜索)。

关键洞察是所有文件操作都可以表示为 shell 命令,
因此我们包装终端后端的 execute() 接口以提供统一的文件 API。

用法:
    from tools.file_operations import ShellFileOperations
    from tools.terminal_tool import _active_environments

    # 获取终端环境的文件操作
    file_ops = ShellFileOperations(terminal_env)

    # 读取文件
    result = file_ops.read_file("/path/to/file.py")

    # 写入文件
    result = file_ops.write_file("/path/to/new.py", "print('hello')")

    # 搜索内容
    result = file_ops.search("TODO", path=".", file_glob="*.py")
"""

import os
import re
import difflib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path
from kclaw_constants import get_kclaw_home
from tools.binary_extensions import BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# 写入路径拒绝列表 — 阻止写入敏感的 系统/凭证 文件
# ---------------------------------------------------------------------------

_HOME = str(Path.home())

WRITE_DENIED_PATHS = {
    os.path.realpath(p) for p in [
        os.path.join(_HOME, ".ssh", "authorized_keys"),
        os.path.join(_HOME, ".ssh", "id_rsa"),
        os.path.join(_HOME, ".ssh", "id_ed25519"),
        os.path.join(_HOME, ".ssh", "config"),
        str(get_kclaw_home() / ".env"),
        os.path.join(_HOME, ".bashrc"),
        os.path.join(_HOME, ".zshrc"),
        os.path.join(_HOME, ".profile"),
        os.path.join(_HOME, ".bash_profile"),
        os.path.join(_HOME, ".zprofile"),
        os.path.join(_HOME, ".netrc"),
        os.path.join(_HOME, ".pgpass"),
        os.path.join(_HOME, ".npmrc"),
        os.path.join(_HOME, ".pypirc"),
        "/etc/sudoers",
        "/etc/passwd",
        "/etc/shadow",
    ]
}

WRITE_DENIED_PREFIXES = [
    os.path.realpath(p) + os.sep for p in [
        os.path.join(_HOME, ".ssh"),
        os.path.join(_HOME, ".aws"),
        os.path.join(_HOME, ".gnupg"),
        os.path.join(_HOME, ".kube"),
        "/etc/sudoers.d",
        "/etc/systemd",
        os.path.join(_HOME, ".docker"),
        os.path.join(_HOME, ".azure"),
        os.path.join(_HOME, ".config", "gh"),
    ]
]


def _get_safe_write_root() -> Optional[str]:
    """返回解析后的 KCLAW_WRITE_SAFE_ROOT 路径,如果未设置则返回 None。

    设置后,所有 write_file/patch 操作都被限制在此目录树中。
    即使目标不在静态拒绝列表上,对其外部的写入也会被拒绝。
    这是针对 gateway/messaging 部署的 opt-in 加固,这些部署应该只接触工作区 checkout。
    """
    root = os.getenv("KCLAW_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def _is_write_denied(path: str) -> bool:
    """如果路径在写入拒绝列表上则返回 True。"""
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    # 1) 静态拒绝列表
    if resolved in WRITE_DENIED_PATHS:
        return True
    for prefix in WRITE_DENIED_PREFIXES:
        if resolved.startswith(prefix):
            return True

    # 2) 可选的安全根沙箱
    safe_root = _get_safe_write_root()
    if safe_root:
        if not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
            return True

    return False


# =============================================================================
# 结果数据类
# =============================================================================

@dataclass
class ReadResult:
    """读取文件的结果。"""
    content: str = ""
    total_lines: int = 0
    file_size: int = 0
    truncated: bool = False
    hint: Optional[str] = None
    is_binary: bool = False
    is_image: bool = False
    base64_content: Optional[str] = None
    mime_type: Optional[str] = None
    dimensions: Optional[str] = None  # For images: "WIDTHxHEIGHT"
    error: Optional[str] = None
    similar_files: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != []}


@dataclass
class WriteResult:
    """写入文件的结果。"""
    bytes_written: int = 0
    dirs_created: bool = False
    error: Optional[str] = None
    warning: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class PatchResult:
    """修补文件的结果。"""
    success: bool = False
    diff: str = ""
    files_modified: List[str] = field(default_factory=list)
    files_created: List[str] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    lint: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        result = {"success": self.success}
        if self.diff:
            result["diff"] = self.diff
        if self.files_modified:
            result["files_modified"] = self.files_modified
        if self.files_created:
            result["files_created"] = self.files_created
        if self.files_deleted:
            result["files_deleted"] = self.files_deleted
        if self.lint:
            result["lint"] = self.lint
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class SearchMatch:
    """单个搜索匹配。"""
    path: str
    line_number: int
    content: str
    mtime: float = 0.0  # Modification time for sorting


@dataclass
class SearchResult:
    """搜索的结果。"""
    matches: List[SearchMatch] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    truncated: bool = False
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        result = {"total_count": self.total_count}
        if self.matches:
            result["matches"] = [
                {"path": m.path, "line": m.line_number, "content": m.content}
                for m in self.matches
            ]
        if self.files:
            result["files"] = self.files
        if self.counts:
            result["counts"] = self.counts
        if self.truncated:
            result["truncated"] = True
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class LintResult:
    """代码检查文件的结果。"""
    success: bool = True
    skipped: bool = False
    output: str = ""
    message: str = ""
    
    def to_dict(self) -> dict:
        if self.skipped:
            return {"status": "skipped", "message": self.message}
        return {
            "status": "ok" if self.success else "error",
            "output": self.output
        }


@dataclass
class ExecuteResult:
    """执行 shell 命令的结果。"""
    stdout: str = ""
    exit_code: int = 0


# =============================================================================
# 抽象接口
# =============================================================================

class FileOperations(ABC):
    """跨终端后端的文件操作抽象接口。"""

    @abstractmethod
    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        """读取带有分页支持的文件。"""
        ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> WriteResult:
        """写入文件内容,按需创建目录。"""
        ...

    @abstractmethod
    def patch_replace(self, path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> PatchResult:
        """使用模糊匹配替换文件中的文本。"""
        ...

    @abstractmethod
    def patch_v4a(self, patch_content: str) -> PatchResult:
        """应用 V4A 格式补丁。"""
        ...

    @abstractmethod
    def search(self, pattern: str, path: str = ".", target: str = "content",
               file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
               output_mode: str = "content", context: int = 0) -> SearchResult:
        """搜索内容或文件。"""
        ...


# =============================================================================
# 基于 Shell 的实现
# =============================================================================

# 图像扩展名 (我们可以作为 base64 返回的二进制子集)
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico'}

# 按文件扩展名的 linters
LINTERS = {
    '.py': 'python -m py_compile {file} 2>&1',
    '.js': 'node --check {file} 2>&1',
    '.ts': 'npx tsc --noEmit {file} 2>&1',
    '.go': 'go vet {file} 2>&1',
    '.rs': 'rustfmt --check {file} 2>&1',
}

# 读取操作的最大限制
MAX_LINES = 2000
MAX_LINE_LENGTH = 2000
MAX_FILE_SIZE = 50 * 1024  # 50KB


class ShellFileOperations(FileOperations):
    """
    通过 shell 命令实现的文件操作。

    适用于任何具有 execute(command, cwd) 方法的终端后端。
    这包括本地、docker、singularity、ssh、modal 和 daytona 环境。
    """

    def __init__(self, terminal_env, cwd: str = None):
        """
        使用终端环境初始化文件操作。

        参数:
            terminal_env: 任何具有 execute(command, cwd) 方法的对象。
                         返回 {"output": str, "returncode": int}
            cwd: 工作目录 (默认为 env 的 cwd 或当前目录)
        """
        self.env = terminal_env
        # 从各种可能的来源确定 cwd。
        # 重要: 不要回退到 os.getcwd() — 那是 HOST 的本地路径,
        # 在容器/云后端 (modal, docker) 中不存在。
        # 如果没有任何东西提供 cwd,使用 "/" 作为安全的通用默认值。
        self.cwd = cwd or getattr(terminal_env, 'cwd', None) or \
                   getattr(getattr(terminal_env, 'config', None), 'cwd', None) or "/"
        
        # 命令可用性检查的缓存
        self._command_cache: Dict[str, bool] = {}
    
    def _exec(self, command: str, cwd: str = None, timeout: int = None,
              stdin_data: str = None) -> ExecuteResult:
        """通过终端后端执行命令。

        参数:
            stdin_data: 如果提供,会被 pipe 到进程的 stdin,
                        而不是嵌入在命令字符串中。绕过 ARG_MAX。
        """
        kwargs = {}
        if timeout:
            kwargs['timeout'] = timeout
        if stdin_data is not None:
            kwargs['stdin_data'] = stdin_data
        
        result = self.env.execute(command, cwd=cwd or self.cwd, **kwargs)
        return ExecuteResult(
            stdout=result.get("output", ""),
            exit_code=result.get("returncode", 0)
        )
    
    def _has_command(self, cmd: str) -> bool:
        """检查命令在环境中是否存在 (带缓存)。"""
        if cmd not in self._command_cache:
            result = self._exec(f"command -v {cmd} >/dev/null 2>&1 && echo 'yes'")
            self._command_cache[cmd] = result.stdout.strip() == 'yes'
        return self._command_cache[cmd]
    
    def _is_likely_binary(self, path: str, content_sample: str = None) -> bool:
        """
        检查文件是否可能是二进制文件。

        使用扩展名检查 (快速) + 内容分析 (回退)。
        """
        ext = os.path.splitext(path)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return True
        
        # 内容分析: >30% 不可打印字符 = 二进制
        if content_sample:
            if not content_sample:
                return False
            non_printable = sum(1 for c in content_sample[:1000] 
                               if ord(c) < 32 and c not in '\n\r\t')
            return non_printable / min(len(content_sample), 1000) > 0.30
        
        return False
    
    def _is_image(self, path: str) -> bool:
        """检查文件是否是我们可以作为 base64 返回的图像。"""
        ext = os.path.splitext(path)[1].lower()
        return ext in IMAGE_EXTENSIONS
    
    def _add_line_numbers(self, content: str, start_line: int = 1) -> str:
        """以 LINE_NUM|CONTENT 格式向内容添加行号。"""
        lines = content.split('\n')
        numbered = []
        for i, line in enumerate(lines, start=start_line):
            # 截断长行
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + "... [truncated]"
            numbered.append(f"{i:6d}|{line}")
        return '\n'.join(numbered)
    
    def _expand_path(self, path: str) -> str:
        """
        将 shell 风格的路径(如 ~ 和 ~user)展开为绝对路径。

        这必须在 shell 转义之前完成,因为 ~ 在单引号内不会展开。
        """
        if not path:
            return path
        
        # 处理 ~ 和 ~user
        if path.startswith('~'):
            # Get home directory via the terminal environment
            result = self._exec("echo $HOME")
            if result.exit_code == 0 and result.stdout.strip():
                home = result.stdout.strip()
                if path == '~':
                    return home
                elif path.startswith('~/'):
                    return home + path[1:]  # Replace ~ with home
                # ~username 格式 - 在让 shell 展开之前提取并验证用户名
                # (防止通过类似 "~; rm -rf /" 的路径进行 shell 注入)。
                rest = path[1:]  # strip leading ~
                slash_idx = rest.find('/')
                username = rest[:slash_idx] if slash_idx >= 0 else rest
                if username and re.fullmatch(r'[a-zA-Z0-9._-]+', username):
                    # 只展开 ~username (而不是完整路径) 以避免通过
                    # 类似 "~user/$(malicious)" 的路径后缀进行 shell 注入。
                    expand_result = self._exec(f"echo ~{username}")
                    if expand_result.exit_code == 0 and expand_result.stdout.strip():
                        user_home = expand_result.stdout.strip()
                        suffix = path[1 + len(username):]  # e.g. "/rest/of/path"
                        return user_home + suffix
        
        return path
    
    def _escape_shell_arg(self, arg: str) -> str:
        """转义字符串以安全用于 shell 命令。"""
        # Use single quotes and escape any single quotes in the string
        return "'" + arg.replace("'", "'\"'\"'") + "'"
    
    def _unified_diff(self, old_content: str, new_content: str, filename: str) -> str:
        """生成新旧内容之间的统一 diff。"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}"
        )
        return ''.join(diff)
    
    # =========================================================================
    # READ Implementation
    # =========================================================================
    
    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        """
        Read a file with pagination, binary detection, and line numbers.
        
        Args:
            path: File path (absolute or relative to cwd)
            offset: Line number to start from (1-indexed, default 1)
            limit: Maximum lines to return (default 500, max 2000)
        
        Returns:
            ReadResult with content, metadata, or error info
        """
        # 展开 ~ 和其他 shell 路径
        path = self._expand_path(path)
        
        # 限制 limit
        limit = min(limit, MAX_LINES)
        
        # 检查文件是否存在并获取大小 (wc -c 是 POSIX,在 Linux + macOS 上可用)
        stat_cmd = f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null"
        stat_result = self._exec(stat_cmd)
        
        if stat_result.exit_code != 0:
            # 文件未找到 - 尝试建议类似的文件
            return self._suggest_similar_files(path)
        
        try:
            file_size = int(stat_result.stdout.strip())
        except ValueError:
            file_size = 0
        
        # Check if file is too large
        if file_size > MAX_FILE_SIZE:
            # Still try to read, but warn
            pass
        
        # 图像永远不会内联 — 重定向到视觉工具
        if self._is_image(path):
            return ReadResult(
                is_image=True,
                is_binary=True,
                file_size=file_size,
                hint=(
                    "Image file detected. Automatically redirected to vision_analyze tool. "
                    "Use vision_analyze with this file path to inspect the image contents."
                ),
            )
        
        # 读取样本以检查二进制内容
        sample_cmd = f"head -c 1000 {self._escape_shell_arg(path)} 2>/dev/null"
        sample_result = self._exec(sample_cmd)
        
        if self._is_likely_binary(path, sample_result.stdout):
            return ReadResult(
                is_binary=True,
                file_size=file_size,
                error="Binary file - cannot display as text. Use appropriate tools to handle this file type."
            )
        
        # 使用 sed 进行分页读取
        end_line = offset + limit - 1
        read_cmd = f"sed -n '{offset},{end_line}p' {self._escape_shell_arg(path)}"
        read_result = self._exec(read_cmd)
        
        if read_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {read_result.stdout}")
        
        # 获取总行数
        wc_cmd = f"wc -l < {self._escape_shell_arg(path)}"
        wc_result = self._exec(wc_cmd)
        try:
            total_lines = int(wc_result.stdout.strip())
        except ValueError:
            total_lines = 0
        
        # Check if truncated
        truncated = total_lines > end_line
        hint = None
        if truncated:
            hint = f"Use offset={end_line + 1} to continue reading (showing {offset}-{end_line} of {total_lines} lines)"
        
        return ReadResult(
            content=self._add_line_numbers(read_result.stdout, offset),
            total_lines=total_lines,
            file_size=file_size,
            truncated=truncated,
            hint=hint
        )
    
    def _suggest_similar_files(self, path: str) -> ReadResult:
        """当请求的文件未找到时,建议类似的文件。"""
        # 获取目录和文件名
        dir_path = os.path.dirname(path) or "."
        filename = os.path.basename(path)
        
        # List files in directory
        ls_cmd = f"ls -1 {self._escape_shell_arg(dir_path)} 2>/dev/null | head -20"
        ls_result = self._exec(ls_cmd)
        
        similar = []
        if ls_result.exit_code == 0 and ls_result.stdout.strip():
            files = ls_result.stdout.strip().split('\n')
            # 简单相似性: 与目标共享一些字符的文件
            for f in files:
                # Check if filenames share significant overlap
                common = set(filename.lower()) & set(f.lower())
                if len(common) >= len(filename) * 0.5:  # 50% character overlap
                    similar.append(os.path.join(dir_path, f))
        
        return ReadResult(
            error=f"File not found: {path}",
            similar_files=similar[:5]  # Limit to 5 suggestions
        )
    
    # =========================================================================
    # WRITE Implementation
    # =========================================================================
    
    def write_file(self, path: str, content: str) -> WriteResult:
        """
        Write content to a file, creating parent directories as needed.

        Pipes content through stdin to avoid OS ARG_MAX limits on large
        files. The content never appears in the shell command string —
        only the file path does.

        Args:
            path: File path to write
            content: Content to write

        Returns:
            WriteResult with bytes written or error
        """
        # 展开 ~ 和其他 shell 路径
        path = self._expand_path(path)

        # 阻止写入敏感路径
        if _is_write_denied(path):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")

        # Create parent directories
        parent = os.path.dirname(path)
        dirs_created = False
        
        if parent:
            mkdir_cmd = f"mkdir -p {self._escape_shell_arg(parent)}"
            mkdir_result = self._exec(mkdir_cmd)
            if mkdir_result.exit_code == 0:
                dirs_created = True
        
        # 通过 stdin 管道写入 — 内容完全绕过 shell 参数解析,
        # 所以无论文件大小如何都没有 ARG_MAX 限制。
        write_cmd = f"cat > {self._escape_shell_arg(path)}"
        write_result = self._exec(write_cmd, stdin_data=content)
        
        if write_result.exit_code != 0:
            return WriteResult(error=f"Failed to write file: {write_result.stdout}")
        
        # Get bytes written (wc -c is POSIX, works on Linux + macOS)
        stat_cmd = f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null"
        stat_result = self._exec(stat_cmd)
        
        try:
            bytes_written = int(stat_result.stdout.strip())
        except ValueError:
            bytes_written = len(content.encode('utf-8'))
        
        return WriteResult(
            bytes_written=bytes_written,
            dirs_created=dirs_created
        )
    
    # =========================================================================
    # PATCH Implementation (Replace Mode)
    # =========================================================================
    
    def patch_replace(self, path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> PatchResult:
        """
        Replace text in a file using fuzzy matching.

        Args:
            path: File path to modify
            old_string: Text to find (must be unique unless replace_all=True)
            new_string: Replacement text
            replace_all: If True, replace all occurrences

        Returns:
            PatchResult with diff and lint results
        """
        # 展开 ~ 和其他 shell 路径
        path = self._expand_path(path)

        # 阻止写入敏感路径
        if _is_write_denied(path):
            return PatchResult(error=f"Write denied: '{path}' is a protected system/credential file.")

        # Read current content
        read_cmd = f"cat {self._escape_shell_arg(path)} 2>/dev/null"
        read_result = self._exec(read_cmd)
        
        if read_result.exit_code != 0:
            return PatchResult(error=f"Failed to read file: {path}")
        
        content = read_result.stdout
        
        # 导入并使用模糊匹配
        from tools.fuzzy_match import fuzzy_find_and_replace
        
        new_content, match_count, error = fuzzy_find_and_replace(
            content, old_string, new_string, replace_all
        )
        
        if error:
            return PatchResult(error=error)
        
        if match_count == 0:
            return PatchResult(error=f"Could not find match for old_string in {path}")
        
        # Write back
        write_result = self.write_file(path, new_content)
        if write_result.error:
            return PatchResult(error=f"Failed to write changes: {write_result.error}")
        
        # 生成 diff
        diff = self._unified_diff(content, new_content, path)
        
        # 自动 lint
        lint_result = self._check_lint(path)
        
        return PatchResult(
            success=True,
            diff=diff,
            files_modified=[path],
            lint=lint_result.to_dict() if lint_result else None
        )
    
    def patch_v4a(self, patch_content: str) -> PatchResult:
        """
        应用 V4A 格式补丁。

        V4A 格式:
            *** Begin Patch
            *** Update File: path/to/file.py
            @@ context hint @@
             context line
            -removed line
            +added line
            *** End Patch

        参数:
            patch_content: V4A 格式补丁字符串

        返回:
            带有所做更改的 PatchResult
        """
        # 导入补丁解析器
        from tools.patch_parser import parse_v4a_patch, apply_v4a_operations
        
        operations, parse_error = parse_v4a_patch(patch_content)
        if parse_error:
            return PatchResult(error=f"Failed to parse patch: {parse_error}")
        
        # Apply operations
        result = apply_v4a_operations(operations, self)
        return result
    
    def _check_lint(self, path: str) -> LintResult:
        """
        在编辑后对文件运行语法检查。

        参数:
            path: 要 lint 的文件路径

        返回:
            带有状态和任何错误的 LintResult
        """
        ext = os.path.splitext(path)[1].lower()
        
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")
        
        # 检查 linter 命令是否可用
        linter_cmd = LINTERS[ext]
        # Extract the base command (first word)
        base_cmd = linter_cmd.split()[0]
        
        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")
        
        # Run linter
        cmd = linter_cmd.format(file=self._escape_shell_arg(path))
        result = self._exec(cmd, timeout=30)
        
        return LintResult(
            success=result.exit_code == 0,
            output=result.stdout.strip() if result.stdout.strip() else ""
        )
    
    # =========================================================================
    # SEARCH Implementation
    # =========================================================================
    
    def search(self, pattern: str, path: str = ".", target: str = "content",
               file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
               output_mode: str = "content", context: int = 0) -> SearchResult:
        """
        Search for content or files.
        
        Args:
            pattern: Regex (for content) or glob pattern (for files)
            path: Directory/file to search (default: cwd)
            target: "content" (grep) or "files" (glob)
            file_glob: File pattern filter for content search (e.g., "*.py")
            limit: Max results (default 50)
            offset: Skip first N results
            output_mode: "content", "files_only", or "count"
            context: Lines of context around matches
        
        Returns:
            SearchResult with matches or file list
        """
        # 展开 ~ 和其他 shell 路径
        path = self._expand_path(path)
        
        # 在搜索前验证路径是否存在
        check = self._exec(f"test -e {self._escape_shell_arg(path)} && echo exists || echo not_found")
        if "not_found" in check.stdout:
            return SearchResult(
                error=f"Path not found: {path}. Verify the path exists (use 'terminal' to check).",
                total_count=0
            )
        
        if target == "files":
            return self._search_files(pattern, path, limit, offset)
        else:
            return self._search_content(pattern, path, file_glob, limit, offset, 
                                        output_mode, context)
    
    def _search_files(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """按名称模式 (glob 类似) 搜索文件。"""
        # Auto-prepend **/ for recursive search if not already present
        if not pattern.startswith('**/') and '/' not in pattern:
            search_pattern = pattern
        else:
            search_pattern = pattern.split('/')[-1]

        # 优先使用 ripgrep: 尊重 .gitignore,默认排除隐藏目录,
        # 并且有并行目录遍历 (在宽树上比 find 快 ~200 倍)。
        # 与已使用 rg 的 _search_content 一致。
        if self._has_command('rg'):
            return self._search_files_rg(search_pattern, path, limit, offset)

        # 回退: find (较慢,不了解 .gitignore)
        if not self._has_command('find'):
            return SearchResult(
                error="File search requires 'rg' (ripgrep) or 'find'. "
                      "Install ripgrep for best results: "
                      "https://github.com/BurntSushi/ripgrep#installation"
            )

        # 排除隐藏目录 (与 ripgrep 的默认行为匹配)。
        hidden_exclude = "-not -path '*/.*'"

        cmd = f"find {self._escape_shell_arg(path)} {hidden_exclude} -type f -name {self._escape_shell_arg(search_pattern)} " \
              f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn | tail -n +{offset + 1} | head -n {limit}"

        result = self._exec(cmd, timeout=60)

        if not result.stdout.strip():
            # 尝试不带 -printf (BSD find 兼容性 — macOS)
            cmd_simple = f"find {self._escape_shell_arg(path)} {hidden_exclude} -type f -name {self._escape_shell_arg(search_pattern)} " \
                        f"2>/dev/null | head -n {limit + offset} | tail -n +{offset + 1}"
            result = self._exec(cmd_simple, timeout=60)

        files = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(' ', 1)
            if len(parts) == 2 and parts[0].replace('.', '').isdigit():
                files.append(parts[1])
            else:
                files.append(line)

        return SearchResult(
            files=files,
            total_count=len(files)
        )

    def _search_files_rg(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """使用 ripgrep 的 --files 模式按名称搜索文件。

        rg --files 尊重 .gitignore,默认排除隐藏目录,
        并且使用并行目录遍历,在宽树上比 find 快约 200 倍。
        """
        # rg --files -g 使用 glob 模式; 包装裸名称以便在任何深度匹配
        # (等同于 find -name)。
        if '/' not in pattern and not pattern.startswith('*'):
            glob_pattern = f"*{pattern}"
        else:
            glob_pattern = pattern

        fetch_limit = limit + offset
        cmd = (
            f"rg --files -g {self._escape_shell_arg(glob_pattern)} "
            f"{self._escape_shell_arg(path)} 2>/dev/null "
            f"| head -n {fetch_limit}"
        )
        result = self._exec(cmd, timeout=60)

        all_files = [f for f in result.stdout.strip().split('\n') if f]
        page = all_files[offset:offset + limit]

        return SearchResult(
            files=page,
            total_count=len(all_files),
            truncated=len(all_files) >= fetch_limit,
        )
    
    def _search_content(self, pattern: str, path: str, file_glob: Optional[str],
                        limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """在文件内搜索内容 (类似 grep)。"""
        # Try ripgrep first (fast), fallback to grep (slower but works)
        if self._has_command('rg'):
            return self._search_with_rg(pattern, path, file_glob, limit, offset, 
                                        output_mode, context)
        elif self._has_command('grep'):
            return self._search_with_grep(pattern, path, file_glob, limit, offset,
                                          output_mode, context)
        else:
            # rg 和 grep 都不可用 (没有 Git Bash 的 Windows 等)
            return SearchResult(
                error="Content search requires ripgrep (rg) or grep. "
                      "Install ripgrep: https://github.com/BurntSushi/ripgrep#installation"
            )
    
    def _search_with_rg(self, pattern: str, path: str, file_glob: Optional[str],
                        limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """使用 ripgrep 搜索。"""
        cmd_parts = ["rg", "--line-number", "--no-heading", "--with-filename"]
        
        # Add context if requested
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        
        # Add file glob filter (must be quoted to prevent shell expansion)
        if file_glob:
            cmd_parts.extend(["--glob", self._escape_shell_arg(file_glob)])
        
        # Output mode handling
        if output_mode == "files_only":
            cmd_parts.append("-l")  # Files only
        elif output_mode == "count":
            cmd_parts.append("-c")  # Count per file
        
        # Add pattern and path
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        
        # 获取额外的行,以便我们可以在切片之前报告真实的总数。
        # 对于上下文模式,rg 在组之间发出分隔符行 ("--"),
        # 所以我们会大量获取并在 Python 中过滤。
        fetch_limit = limit + offset + 200 if context > 0 else limit + offset
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        
        cmd = " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        
        # rg exit codes: 0=matches found, 1=no matches, 2=error
        if result.exit_code == 2 and not result.stdout.strip():
            error_msg = result.stderr.strip() if hasattr(result, 'stderr') and result.stderr else "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)
        
        # Parse results based on output mode
        if output_mode == "files_only":
            all_files = [f for f in result.stdout.strip().split('\n') if f]
            total = len(all_files)
            page = all_files[offset:offset + limit]
            return SearchResult(files=page, total_count=total)
        
        elif output_mode == "count":
            counts = {}
            for line in result.stdout.strip().split('\n'):
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    if len(parts) == 2:
                        try:
                            counts[parts[0]] = int(parts[1])
                        except ValueError:
                            pass
            return SearchResult(counts=counts, total_count=sum(counts.values()))
        
        else:
            # 解析内容匹配和上下文行。
            # rg 匹配行:   "file:lineno:content"  (冒号分隔符)
            # rg 上下文行: "file-lineno-content"   (破折号分隔符)
            # rg 组分隔符:    "--"
            # 注意: 在 Windows 上,路径包含驱动器号 (例如 C:\path),
            # 所以简单的 split(":") 会出问题。使用正则表达式处理两个平台。
            _match_re = re.compile(r'^([A-Za-z]:)?(.*?):(\d+):(.*)$')
            _ctx_re = re.compile(r'^([A-Za-z]:)?(.*?)-(\d+)-(.*)$')
            matches = []
            for line in result.stdout.strip().split('\n'):
                if not line or line == "--":
                    continue
                
                # Try match line first (colon-separated: file:line:content)
                m = _match_re.match(line)
                if m:
                    matches.append(SearchMatch(
                        path=(m.group(1) or '') + m.group(2),
                        line_number=int(m.group(3)),
                        content=m.group(4)[:500]
                    ))
                    continue
                
                # 尝试上下文行 (破折号分隔: file-line-content)
                # 仅在请求上下文时尝试以避免误报
                if context > 0:
                    m = _ctx_re.match(line)
                    if m:
                        matches.append(SearchMatch(
                            path=(m.group(1) or '') + m.group(2),
                            line_number=int(m.group(3)),
                            content=m.group(4)[:500]
                        ))
            
            total = len(matches)
            page = matches[offset:offset + limit]
            return SearchResult(
                matches=page,
                total_count=total,
                truncated=total > offset + limit
            )
    
    def _search_with_grep(self, pattern: str, path: str, file_glob: Optional[str],
                          limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """使用 grep 的回退搜索。"""
        cmd_parts = ["grep", "-rnH"]  # -H forces filename even for single-file searches
        
        # 排除隐藏目录 (与 ripgrep 的默认行为匹配)。
        # 这可以防止搜索 .hub/index-cache/、.git/ 等内部内容。
        cmd_parts.append("--exclude-dir='.*'")
        
        # Add context if requested
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        
        # Add file pattern filter (must be quoted to prevent shell expansion)
        if file_glob:
            cmd_parts.extend(["--include", self._escape_shell_arg(file_glob)])
        
        # Output mode handling
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        
        # Add pattern and path
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        
        # 大量获取以便我们可以在切片之前计算总数
        fetch_limit = limit + offset + (200 if context > 0 else 0)
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        
        cmd = " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        
        # grep exit codes: 0=matches found, 1=no matches, 2=error
        if result.exit_code == 2 and not result.stdout.strip():
            error_msg = result.stderr.strip() if hasattr(result, 'stderr') and result.stderr else "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)
        
        if output_mode == "files_only":
            all_files = [f for f in result.stdout.strip().split('\n') if f]
            total = len(all_files)
            page = all_files[offset:offset + limit]
            return SearchResult(files=page, total_count=total)
        
        elif output_mode == "count":
            counts = {}
            for line in result.stdout.strip().split('\n'):
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    if len(parts) == 2:
                        try:
                            counts[parts[0]] = int(parts[1])
                        except ValueError:
                            pass
            return SearchResult(counts=counts, total_count=sum(counts.values()))
        
        else:
            # grep 匹配行:   "file:lineno:content" (冒号)
            # grep 上下文行: "file-lineno-content"  (破折号)
            # grep 组分隔符:    "--"
            # 注意: 在 Windows 上,路径包含驱动器号 (例如 C:\path),
            # 所以简单的 split(":") 会出问题。使用正则表达式处理两个平台。
            _match_re = re.compile(r'^([A-Za-z]:)?(.*?):(\d+):(.*)$')
            _ctx_re = re.compile(r'^([A-Za-z]:)?(.*?)-(\d+)-(.*)$')
            matches = []
            for line in result.stdout.strip().split('\n'):
                if not line or line == "--":
                    continue
                
                m = _match_re.match(line)
                if m:
                    matches.append(SearchMatch(
                        path=(m.group(1) or '') + m.group(2),
                        line_number=int(m.group(3)),
                        content=m.group(4)[:500]
                    ))
                    continue
                
                if context > 0:
                    m = _ctx_re.match(line)
                    if m:
                        matches.append(SearchMatch(
                            path=(m.group(1) or '') + m.group(2),
                            line_number=int(m.group(3)),
                            content=m.group(4)[:500]
                        ))

            
            total = len(matches)
            page = matches[offset:offset + limit]
            return SearchResult(
                matches=page,
                total_count=total,
                truncated=total > offset + limit
            )
