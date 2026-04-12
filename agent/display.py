"""CLI 展示 — 旋转器、kawaii 表情、工具预览格式化。

纯展示函数和类,无 AIAgent 依赖。
由 AIAgent._execute_tool_calls 用于 CLI 反馈。
"""

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path

# ANSI 转义码,用于工具失败指示器着色
_RED = "\033[31m"
_RESET = "\033[0m"

logger = logging.getLogger(__name__)

_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[38;2;150;150;150m"
_ANSI_FILE = "\033[38;2;180;160;255m"
_ANSI_HUNK = "\033[38;2;120;120;140m"
_ANSI_MINUS = "\033[38;2;255;255;255;48;2;120;20;20m"
_ANSI_PLUS = "\033[38;2;255;255;255;48;2;20;90;20m"
_MAX_INLINE_DIFF_FILES = 6
_MAX_INLINE_DIFF_LINES = 80


@dataclass
class LocalEditSnapshot:
    """工具执行前的文件系统快照,用于写入后本地渲染 diff。"""
    paths: list[Path] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)

# =========================================================================
# 可配置的工具预览长度(0 = 无限制)
# 启动时由 CLI 或 gateway 从 display.tool_preview_length 配置设置一次。
# =========================================================================
_tool_preview_max_len: int = 0  # 0 = 无限制


def set_tool_preview_max_len(n: int) -> None:
    """设置工具调用预览的全局最大长度。0 = 无限制。"""
    global _tool_preview_max_len
    _tool_preview_max_len = max(int(n), 0) if n else 0


def get_tool_preview_max_len() -> int:
    """返回配置的最大预览长度(0 = 无限制)。"""
    return _tool_preview_max_len


# =========================================================================
# 皮肤感知助手(延迟导入以避免循环依赖)
# =========================================================================

def _get_skin():
    """获取活动皮肤配置,如果不可用则返回 None。"""
    try:
        from kclaw_cli.skin_engine import get_active_skin
        return get_active_skin()
    except Exception:
        return None


def get_skin_faces(key: str, default: list) -> list:
    """从活动皮肤获取旋转器表情列表,回退到默认值。"""
    skin = _get_skin()
    if skin:
        faces = skin.get_spinner_list(key)
        if faces:
            return faces
    return default


def get_skin_verbs() -> list:
    """从活动皮肤获取思考动词。"""
    skin = _get_skin()
    if skin:
        verbs = skin.get_spinner_list("thinking_verbs")
        if verbs:
            return verbs
    return KawaiiSpinner.THINKING_VERBS


def get_skin_tool_prefix() -> str:
    """从活动皮肤获取工具输出前缀字符。"""
    skin = _get_skin()
    if skin:
        return skin.tool_prefix
    return "┊"


def get_tool_emoji(tool_name: str, default: str = "⚡") -> str:
    """获取工具的显示表情。

    解析顺序:
    1. 活动皮肤的 ``tool_emojis`` 覆盖(如果已加载皮肤)
    2. 工具注册表的每个工具 ``emoji`` 字段
    3. *default* 回退
    """
    # 1. 皮肤覆盖
    skin = _get_skin()
    if skin and skin.tool_emojis:
        override = skin.tool_emojis.get(tool_name)
        if override:
            return override
    # 2. 注册表默认值
    try:
        from tools.registry import registry
        emoji = registry.get_emoji(tool_name, default="")
        if emoji:
            return emoji
    except Exception:
        pass
    # 3. 硬编码回退
    return default


# =========================================================================
# 工具预览(工具调用主要参数的单行摘要)
# =========================================================================

def _oneline(text: str) -> str:
    """将空白字符(包括换行)折叠为单个空格。"""
    return " ".join(text.split())


def build_tool_preview(tool_name: str, args: dict, max_len: int | None = None) -> str | None:
    """构建工具调用主要参数的简短预览用于显示。

    *max_len* 控制截断。``None``(默认)延迟到通过配置设置的全局
    ``_tool_preview_max_len``;``0`` 表示无限制。
    """
    if max_len is None:
        max_len = _tool_preview_max_len
    if not args:
        return None
    primary_args = {
        "terminal": "command", "web_search": "query", "web_extract": "urls",
        "read_file": "path", "write_file": "path", "patch": "path",
        "search_files": "pattern", "browser_navigate": "url",
        "browser_click": "ref", "browser_type": "text",
        "image_generate": "prompt", "text_to_speech": "text",
        "vision_analyze": "question", "mixture_of_agents": "user_prompt",
        "skill_view": "name", "skills_list": "category",
        "cronjob": "action",
        "execute_code": "code", "delegate_task": "goal",
        "clarify": "question", "skill_manage": "name",
    }

    if tool_name == "process":
        action = args.get("action", "")
        sid = args.get("session_id", "")
        data = args.get("data", "")
        timeout_val = args.get("timeout")
        parts = [action]
        if sid:
            parts.append(sid[:16])
        if data:
            parts.append(f'"{_oneline(data[:20])}"')
        if timeout_val and action == "wait":
            parts.append(f"{timeout_val}s")
        return " ".join(parts) if parts else None

    if tool_name == "todo":
        todos_arg = args.get("todos")
        merge = args.get("merge", False)
        if todos_arg is None:
            return "reading task list"
        elif merge:
            return f"updating {len(todos_arg)} task(s)"
        else:
            return f"planning {len(todos_arg)} task(s)"

    if tool_name == "session_search":
        query = _oneline(args.get("query", ""))
        return f"recall: \"{query[:25]}{'...' if len(query) > 25 else ''}\""

    if tool_name == "memory":
        action = args.get("action", "")
        target = args.get("target", "")
        if action == "add":
            content = _oneline(args.get("content", ""))
            return f"+{target}: \"{content[:25]}{'...' if len(content) > 25 else ''}\""
        elif action == "replace":
            return f"~{target}: \"{_oneline(args.get('old_text', '')[:20])}\""
        elif action == "remove":
            return f"-{target}: \"{_oneline(args.get('old_text', '')[:20])}\""
        return action

    if tool_name == "send_message":
        target = args.get("target", "?")
        msg = _oneline(args.get("message", ""))
        if len(msg) > 20:
            msg = msg[:17] + "..."
        return f"to {target}: \"{msg}\""

    if tool_name.startswith("rl_"):
        rl_previews = {
            "rl_list_environments": "listing envs",
            "rl_select_environment": args.get("name", ""),
            "rl_get_current_config": "reading config",
            "rl_edit_config": f"{args.get('field', '')}={args.get('value', '')}",
            "rl_start_training": "starting",
            "rl_check_status": args.get("run_id", "")[:16],
            "rl_stop_training": f"stopping {args.get('run_id', '')[:16]}",
            "rl_get_results": args.get("run_id", "")[:16],
            "rl_list_runs": "listing runs",
            "rl_test_inference": f"{args.get('num_steps', 3)} steps",
        }
        return rl_previews.get(tool_name)

    key = primary_args.get(tool_name)
    if not key:
        for fallback_key in ("query", "text", "command", "path", "name", "prompt", "code", "goal"):
            if fallback_key in args:
                key = fallback_key
                break

    if not key or key not in args:
        return None

    value = args[key]
    if isinstance(value, list):
        value = value[0] if value else ""

    preview = _oneline(str(value))
    if not preview:
        return None
    if max_len > 0 and len(preview) > max_len:
        preview = preview[:max_len - 3] + "..."
    return preview


# =========================================================================
# Inline diff previews for write actions
# =========================================================================

def _resolved_path(path: str) -> Path:
    """解析可能相对的路径,相对于当前 cwd。"""
    candidate = Path(os.path.expanduser(path))
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _snapshot_text(path: Path) -> str | None:
    """返回 UTF-8 文件内容,缺失/不可读文件返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError):
        return None


def _display_diff_path(path: Path) -> str:
    """在 diff 中优先使用相对于 cwd 的路径(如果可用)。"""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        return str(path)


def _resolve_skill_manage_paths(args: dict) -> list[Path]:
    """将 skill_manage 写入目标解析为文件系统路径。"""
    action = args.get("action")
    name = args.get("name")
    if not action or not name:
        return []

    from tools.skill_manager_tool import _find_skill, _resolve_skill_dir

    if action == "create":
        skill_dir = _resolve_skill_dir(name, args.get("category"))
        return [skill_dir / "SKILL.md"]

    existing = _find_skill(name)
    if not existing:
        return []

    skill_dir = Path(existing["path"])
    if action in {"edit", "patch"}:
        file_path = args.get("file_path")
        return [skill_dir / file_path] if file_path else [skill_dir / "SKILL.md"]
    if action in {"write_file", "remove_file"}:
        file_path = args.get("file_path")
        return [skill_dir / file_path] if file_path else []
    if action == "delete":
        files = [path for path in sorted(skill_dir.rglob("*")) if path.is_file()]
        return files
    return []


def _resolve_local_edit_paths(tool_name: str, function_args: dict | None) -> list[Path]:
    """解析可写入工具的本地文件系统目标。"""
    if not isinstance(function_args, dict):
        return []

    if tool_name == "write_file":
        path = function_args.get("path")
        return [_resolved_path(path)] if path else []

    if tool_name == "patch":
        path = function_args.get("path")
        return [_resolved_path(path)] if path else []

    if tool_name == "skill_manage":
        return _resolve_skill_manage_paths(function_args)

    return []


def capture_local_edit_snapshot(tool_name: str, function_args: dict | None) -> LocalEditSnapshot | None:
    """捕获本地写入预览的 before 状态。"""
    paths = _resolve_local_edit_paths(tool_name, function_args)
    if not paths:
        return None

    snapshot = LocalEditSnapshot(paths=paths)
    for path in paths:
        snapshot.before[str(path)] = _snapshot_text(path)
    return snapshot


def _result_succeeded(result: str | None) -> bool:
    """保守检测工具结果是否表示成功。"""
    if not result:
        return False
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("error"):
        return False
    if "success" in data:
        return bool(data.get("success"))
    return True


def _diff_from_snapshot(snapshot: LocalEditSnapshot | None) -> str | None:
    """从存储的 before 状态和当前文件生成统一 diff 文本。"""
    if not snapshot:
        return None

    chunks: list[str] = []
    for path in snapshot.paths:
        before = snapshot.before.get(str(path))
        after = _snapshot_text(path)
        if before == after:
            continue

        display_path = _display_diff_path(path)
        diff = "".join(
            unified_diff(
                [] if before is None else before.splitlines(keepends=True),
                [] if after is None else after.splitlines(keepends=True),
                fromfile=f"a/{display_path}",
                tofile=f"b/{display_path}",
            )
        )
        if diff:
            chunks.append(diff)

    if not chunks:
        return None
    return "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks)


def extract_edit_diff(
    tool_name: str,
    result: str | None,
    *,
    function_args: dict | None = None,
    snapshot: LocalEditSnapshot | None = None,
) -> str | None:
    """从文件编辑工具结果中提取统一 diff。"""
    if tool_name == "patch" and result:
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            diff = data.get("diff")
            if isinstance(diff, str) and diff.strip():
                return diff

    if tool_name not in {"write_file", "patch", "skill_manage"}:
        return None
    if not _result_succeeded(result):
        return None
    return _diff_from_snapshot(snapshot)


def _emit_inline_diff(diff_text: str, print_fn) -> bool:
    """通过 CLI 的 prompt_toolkit 安全打印机发射渲染的 diff 文本。"""
    if print_fn is None or not diff_text:
        return False
    try:
        print_fn("  ┊ review diff")
        for line in diff_text.rstrip("\n").splitlines():
            print_fn(line)
        return True
    except Exception:
        return False


def _render_inline_unified_diff(diff: str) -> list[str]:
    """以 KClaw 的内联转录风格渲染统一 diff 行。"""
    rendered: list[str] = []
    from_file = None
    to_file = None

    for raw_line in diff.splitlines():
        if raw_line.startswith("--- "):
            from_file = raw_line[4:].strip()
            continue
        if raw_line.startswith("+++ "):
            to_file = raw_line[4:].strip()
            if from_file or to_file:
                rendered.append(f"{_ANSI_FILE}{from_file or 'a/?'} → {to_file or 'b/?'}{_ANSI_RESET}")
            continue
        if raw_line.startswith("@@"):
            rendered.append(f"{_ANSI_HUNK}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith("-"):
            rendered.append(f"{_ANSI_MINUS}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith("+"):
            rendered.append(f"{_ANSI_PLUS}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith(" "):
            rendered.append(f"{_ANSI_DIM}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line:
            rendered.append(raw_line)

    return rendered


def _split_unified_diff_sections(diff: str) -> list[str]:
    """将统一 diff 拆分为每个文件的节。"""
    sections: list[list[str]] = []
    current: list[str] = []

    for line in diff.splitlines():
        if line.startswith("--- ") and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        sections.append(current)

    return ["\n".join(section) for section in sections if section]


def _summarize_rendered_diff_sections(
    diff: str,
    *,
    max_files: int = _MAX_INLINE_DIFF_FILES,
    max_lines: int = _MAX_INLINE_DIFF_LINES,
) -> list[str]:
    """渲染 diff 节,同时限制文件数和总行数。"""
    sections = _split_unified_diff_sections(diff)
    rendered: list[str] = []
    omitted_files = 0
    omitted_lines = 0

    for idx, section in enumerate(sections):
        if idx >= max_files:
            omitted_files += 1
            omitted_lines += len(_render_inline_unified_diff(section))
            continue

        section_lines = _render_inline_unified_diff(section)
        remaining_budget = max_lines - len(rendered)
        if remaining_budget <= 0:
            omitted_lines += len(section_lines)
            omitted_files += 1
            continue

        if len(section_lines) <= remaining_budget:
            rendered.extend(section_lines)
            continue

        rendered.extend(section_lines[:remaining_budget])
        omitted_lines += len(section_lines) - remaining_budget
        omitted_files += 1 + max(0, len(sections) - idx - 1)
        for leftover in sections[idx + 1:]:
            omitted_lines += len(_render_inline_unified_diff(leftover))
        break

    if omitted_files or omitted_lines:
        summary = f"… omitted {omitted_lines} diff line(s)"
        if omitted_files:
            summary += f" across {omitted_files} additional file(s)/section(s)"
        rendered.append(f"{_ANSI_HUNK}{summary}{_ANSI_RESET}")

    return rendered


def render_edit_diff_with_delta(
    tool_name: str,
    result: str | None,
    *,
    function_args: dict | None = None,
    snapshot: LocalEditSnapshot | None = None,
    print_fn=None,
) -> bool:
    """内联渲染编辑 diff,不接管终端 UI。"""
    diff = extract_edit_diff(
        tool_name,
        result,
        function_args=function_args,
        snapshot=snapshot,
    )
    if not diff:
        return False
    try:
        rendered_lines = _summarize_rendered_diff_sections(diff)
    except Exception as exc:
        logger.debug("Could not render inline diff: %s", exc)
        return False
    return _emit_inline_diff("\n".join(rendered_lines), print_fn)


# =========================================================================
# KawaiiSpinner
# =========================================================================

class KawaiiSpinner:
    """带有 kawaii 表情的动画旋转器,用于工具执行期间的 CLI 反馈。"""

    SPINNERS = {
        'dots': ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
        'bounce': ['⠁', '⠂', '⠄', '⡀', '⢀', '⠠', '⠐', '⠈'],
        'grow': ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█', '▇', '▆', '▅', '▄', '▃', '▂'],
        'arrows': ['←', '↖', '↑', '↗', '→', '↘', '↓', '↙'],
        'star': ['✶', '✷', '✸', '✹', '✺', '✹', '✸', '✷'],
        'moon': ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'],
        'pulse': ['◜', '◠', '◝', '◞', '◡', '◟'],
        'brain': ['🧠', '💭', '💡', '✨', '💫', '🌟', '💡', '💭'],
        'sparkle': ['⁺', '˚', '*', '✧', '✦', '✧', '*', '˚'],
    }

    KAWAII_WAITING = [
        "(｡◕‿◕｡)", "(◕‿◕✿)", "٩(◕‿◕｡)۶", "(✿◠‿◠)", "( ˘▽˘)っ",
        "♪(´ε` )", "(◕ᴗ◕✿)", "ヾ(＾∇＾)", "(≧◡≦)", "(★ω★)",
    ]

    KAWAII_THINKING = [
        "(｡•́︿•̀｡)", "(◔_◔)", "(¬‿¬)", "( •_•)>⌐■-■", "(⌐■_■)",
        "(´･_･`)", "◉_◉", "(°ロ°)", "( ˘⌣˘)♡", "ヽ(>∀<☆)☆",
        "٩(๑❛ᴗ❛๑)۶", "(⊙_⊙)", "(¬_¬)", "( ͡° ͜ʖ ͡°)", "ಠ_ಠ",
    ]

    THINKING_VERBS = [
        "pondering", "contemplating", "musing", "cogitating", "ruminating",
        "deliberating", "mulling", "reflecting", "processing", "reasoning",
        "analyzing", "computing", "synthesizing", "formulating", "brainstorming",
    ]

    def __init__(self, message: str = "", spinner_type: str = 'dots', print_fn=None):
        self.message = message
        self.spinner_frames = self.SPINNERS.get(spinner_type, self.SPINNERS['dots'])
        self.running = False
        self.thread = None
        self.frame_idx = 0
        self.start_time = None
        self.last_line_len = 0
        # 可选的可调用对象,用于路由所有输出(例如静默后台 agent 的 no-op)。
        # 设置后,完全绕过 self._out,使覆盖 _print_fn 的 agent 保持完全静默。
        self._print_fn = print_fn
        # 立即捕获 stdout,在子 agent 的 redirect_stdout(devnull) 之前,
        # 否则 sys.stdout 会被替换为黑洞。
        self._out = sys.stdout

    def _write(self, text: str, end: str = '\n', flush: bool = False):
        """写入创建旋转器时捕获的 stdout。

        如果构造时提供了 print_fn,所有输出都通过它路由 —
        允许调用者使用 no-op lambda 静默旋转器。
        """
        if self._print_fn is not None:
            try:
                self._print_fn(text)
            except Exception:
                pass
            return
        try:
            self._out.write(text + end)
            if flush:
                self._out.flush()
        except (ValueError, OSError):
            pass

    @property
    def _is_tty(self) -> bool:
        """检查输出是否为真实终端,安全处理已关闭的流。"""
        try:
            return hasattr(self._out, 'isatty') and self._out.isatty()
        except (ValueError, OSError):
            return False

    def _is_patch_stdout_proxy(self) -> bool:
        """当 stdout 是 prompt_toolkit 的 StdoutProxy 时返回 True。
    
        patch_stdout 将 sys.stdout 包装在 StdoutProxy 中,该代理排队写入并
        在每次 flush() 周围注入换行符。\r 覆盖永远不会落在正确的行上 —
        每个旋转器帧最终都在自己的行上。
    
        CLI 已经通过专用 TUI 控件(_spinner_text)驱动旋转器显示,
        因此在 StdoutProxy 下 KawaiiSpinner 的基于 \r 的动画是多余的。
        """
        try:
            from prompt_toolkit.patch_stdout import StdoutProxy
            return isinstance(self._out, StdoutProxy)
        except ImportError:
            return False

    def _animate(self):
        # 当 stdout 不是真实终端时(例如 Docker、systemd、管道),
        # 完全跳过动画 — 它会产生大量日志膨胀。
        # 只需记录一次开始,让 stop() 记录完成。
        if not self._is_tty:
            self._write(f"  [tool] {self.message}", flush=True)
            while self.running:
                time.sleep(0.5)
            return

        # 当在 prompt_toolkit 的 patch_stdout 上下文中运行时,CLI
        # 通过专用 TUI 控件(_spinner_text)渲染旋转器状态。
        # 这里也驱动基于 \r 的动画会导致视觉重绘:StdoutProxy
        # 在每次 flush 周围注入换行符,因此每帧都落在新行上并覆盖状态栏。
        if self._is_patch_stdout_proxy():
            while self.running:
                time.sleep(0.1)
            return

        # 启动时缓存皮肤翅膀(避免每帧导入)
        skin = _get_skin()
        wings = skin.get_spinner_wings() if skin else []

        while self.running:
            if os.getenv("KCLAW_SPINNER_PAUSE"):
                time.sleep(0.1)
                continue
            frame = self.spinner_frames[self.frame_idx % len(self.spinner_frames)]
            elapsed = time.time() - self.start_time
            if wings:
                left, right = wings[self.frame_idx % len(wings)]
                line = f"  {left} {frame} {self.message} {right} ({elapsed:.1f}s)"
            else:
                line = f"  {frame} {self.message} ({elapsed:.1f}s)"
            pad = max(self.last_line_len - len(line), 0)
            self._write(f"\r{line}{' ' * pad}", end='', flush=True)
            self.last_line_len = len(line)
            self.frame_idx += 1
            time.sleep(0.12)

    def start(self):
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def update_text(self, new_message: str):
        self.message = new_message

    def print_above(self, text: str):
        """在旋转器上方打印一行而不破坏动画。

        清除当前旋转器行,打印文本,让下一次动画刻度
        在下方重新绘制旋转器。
        线程安全:使用捕获的 stdout 引用(self._out)。
        在 redirect_stdout(devnull) 内工作,因为 _write 绕过
        sys.stdout 并写入创建旋转器时捕获的 stdout。
        """
        if not self.running:
            self._write(f"  {text}", flush=True)
            return
        # 用空格清除旋转器行(不是 \033[K)以避免转义码损坏
        # 当 prompt_toolkit 的 patch_stdout 激活时 — 与 stop() 方法相同。
        # 然后打印文本;旋转器在下次刻度时重新绘制。
        blanks = ' ' * max(self.last_line_len + 5, 40)
        self._write(f"\r{blanks}\r  {text}", flush=True)

    def stop(self, final_message: str = None):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)

        is_tty = self._is_tty
        if is_tty:
            # 用空格清除旋转器行而不是 \033[K,以避免
            # prompt_toolkit 的 patch_stdout 激活时转义码损坏。
            blanks = ' ' * max(self.last_line_len + 5, 40)
            self._write(f"\r{blanks}\r", end='', flush=True)
        if final_message:
            elapsed = f" ({time.time() - self.start_time:.1f}s)" if self.start_time else ""
            if is_tty:
                self._write(f"  {final_message}", flush=True)
            else:
                self._write(f"  [done] {final_message}{elapsed}", flush=True)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# =========================================================================
# Kawaii 表情数组(由 AIAgent._execute_tool_calls 用于旋转器文本)
# =========================================================================

KAWAII_SEARCH = [
    "♪(´ε` )", "(｡◕‿◕｡)", "ヾ(＾∇＾)", "(◕ᴗ◕✿)", "( ˘▽˘)っ",
    "٩(◕‿◕｡)۶", "(✿◠‿◠)", "♪～(´ε｀ )", "(ノ´ヮ`)ノ*:・゚✧", "＼(◎o◎)／",
]
KAWAII_READ = [
    "φ(゜▽゜*)♪", "( ˘▽˘)っ", "(⌐■_■)", "٩(｡•́‿•̀｡)۶", "(◕‿◕✿)",
    "ヾ(＠⌒ー⌒＠)ノ", "(✧ω✧)", "♪(๑ᴖ◡ᴖ๑)♪", "(≧◡≦)", "( ´ ▽ ` )ノ",
]
KAWAII_TERMINAL = [
    "ヽ(>∀<☆)ノ", "(ノ°∀°)ノ", "٩(^ᴗ^)۶", "ヾ(⌐■_■)ノ♪", "(•̀ᴗ•́)و",
    "┗(＾0＾)┓", "(｀・ω・´)", "＼(￣▽￣)／", "(ง •̀_•́)ง", "ヽ(´▽`)/",
]
KAWAII_BROWSER = [
    "(ノ°∀°)ノ", "(☞゚ヮ゚)☞", "( ͡° ͜ʖ ͡°)", "┌( ಠ_ಠ)┘", "(⊙_⊙)？",
    "ヾ(•ω•`)o", "(￣ω￣)", "( ˇωˇ )", "(ᵔᴥᵔ)", "＼(◎o◎)／",
]
KAWAII_CREATE = [
    "✧*。٩(ˊᗜˋ*)و✧", "(ﾉ◕ヮ◕)ﾉ*:・ﾟ✧", "ヽ(>∀<☆)ノ", "٩(♡ε♡)۶", "(◕‿◕)♡",
    "✿◕ ‿ ◕✿", "(*≧▽≦)", "ヾ(＾-＾)ノ", "(☆▽☆)", "°˖✧◝(⁰▿⁰)◜✧˖°",
]
KAWAII_SKILL = [
    "ヾ(＠⌒ー⌒＠)ノ", "(๑˃ᴗ˂)ﻭ", "٩(◕‿◕｡)۶", "(✿╹◡╹)", "ヽ(・∀・)ノ",
    "(ノ´ヮ`)ノ*:・ﾟ✧", "♪(๑ᴖ◡ᴖ๑)♪", "(◠‿◠)", "٩(ˊᗜˋ*)و", "(＾▽＾)",
    "ヾ(＾∇＾)", "(★ω★)/", "٩(｡•́‿•̀｡)۶", "(◕ᴗ◕✿)", "＼(◎o◎)／",
    "(✧ω✧)", "ヽ(>∀<☆)ノ", "( ˘▽˘)っ", "(≧◡≦) ♡", "ヾ(￣▽￣)",
]
KAWAII_THINK = [
    "(っ°Д°;)っ", "(；′⌒`)", "(・_・ヾ", "( ´_ゝ`)", "(￣ヘ￣)",
    "(。-`ω´-)", "( ˘︹˘ )", "(¬_¬)", "ヽ(ー_ー )ノ", "(；一_一)",
]
KAWAII_GENERIC = [
    "♪(´ε` )", "(◕‿◕✿)", "ヾ(＾∇＾)", "٩(◕‿◕｡)۶", "(✿◠‿◠)",
    "(ノ´ヮ`)ノ*:・ﾟ✧", "ヽ(>∀<☆)ノ", "(☆▽☆)", "( ˘▽˘)っ", "(≧◡≦)",
]


# =========================================================================
# 可爱的工具消息(替换旋转器的完成行)
# =========================================================================

def _detect_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """检查工具结果字符串中是否有失败迹象。

    返回 ``(is_failure, suffix)``,*suffix* 是信息标签,
    例如终端失败的 ``" [exit 1]"``,或通用失败的 ``" [error]"``。
    成功时返回 ``(False, "")``。
    """
    if result is None:
        return False, ""

    if tool_name == "terminal":
        try:
            data = json.loads(result)
            exit_code = data.get("exit_code")
            if exit_code is not None and exit_code != 0:
                return True, f" [exit {exit_code}]"
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.debug("Could not parse terminal result as JSON for exit code check")
        return False, ""

    # 内存特定:区分“满”和真正错误
    if tool_name == "memory":
        try:
            data = json.loads(result)
            if data.get("success") is False and "exceed the limit" in data.get("error", ""):
                return True, " [full]"
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.debug("Could not parse memory result as JSON for capacity check")

    # 非终端工具的通用启发式
    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"

    return False, ""


def get_cute_tool_message(
    tool_name: str, args: dict, duration: float, result: str | None = None,
) -> str:
    """为 CLI 静默模式生成格式化的工具完成行。

    格式:``| {emoji} {verb:9} {detail}  {duration}``

    提供 *result* 时,检查行是否有失败指示器。
    失败的工具调用获得红色前缀和信息后缀。
    """
    dur = f"{duration:.1f}s"
    is_failure, failure_suffix = _detect_tool_failure(tool_name, result)
    skin_prefix = get_skin_tool_prefix()

    def _trunc(s, n=40):
        s = str(s)
        if _tool_preview_max_len == 0:
            return s  # no limit
        return (s[:n-3] + "...") if len(s) > n else s

    def _path(p, n=35):
        p = str(p)
        if _tool_preview_max_len == 0:
            return p  # no limit
        return ("..." + p[-(n-3):]) if len(p) > n else p

    def _wrap(line: str) -> str:
        """应用皮肤工具前缀和失败后缀。"""
        if skin_prefix != "┊":
            line = line.replace("┊", skin_prefix, 1)
        if not is_failure:
            return line
        return f"{line}{failure_suffix}"

    if tool_name == "web_search":
        return _wrap(f"┊ 🔍 search    {_trunc(args.get('query', ''), 42)}  {dur}")
    if tool_name == "web_extract":
        urls = args.get("urls", [])
        if urls:
            url = urls[0] if isinstance(urls, list) else str(urls)
            domain = url.replace("https://", "").replace("http://", "").split("/")[0]
            extra = f" +{len(urls)-1}" if len(urls) > 1 else ""
            return _wrap(f"┊ 📄 fetch     {_trunc(domain, 35)}{extra}  {dur}")
        return _wrap(f"┊ 📄 fetch     pages  {dur}")
    if tool_name == "web_crawl":
        url = args.get("url", "")
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        return _wrap(f"┊ 🕸️  crawl     {_trunc(domain, 35)}  {dur}")
    if tool_name == "terminal":
        return _wrap(f"┊ 💻 $         {_trunc(args.get('command', ''), 42)}  {dur}")
    if tool_name == "process":
        action = args.get("action", "?")
        sid = args.get("session_id", "")[:12]
        labels = {"list": "ls processes", "poll": f"poll {sid}", "log": f"log {sid}",
                  "wait": f"wait {sid}", "kill": f"kill {sid}", "write": f"write {sid}", "submit": f"submit {sid}"}
        return _wrap(f"┊ ⚙️  proc      {labels.get(action, f'{action} {sid}')}  {dur}")
    if tool_name == "read_file":
        return _wrap(f"┊ 📖 read      {_path(args.get('path', ''))}  {dur}")
    if tool_name == "write_file":
        return _wrap(f"┊ ✍️  write     {_path(args.get('path', ''))}  {dur}")
    if tool_name == "patch":
        return _wrap(f"┊ 🔧 patch     {_path(args.get('path', ''))}  {dur}")
    if tool_name == "search_files":
        pattern = _trunc(args.get("pattern", ""), 35)
        target = args.get("target", "content")
        verb = "find" if target == "files" else "grep"
        return _wrap(f"┊ 🔎 {verb:9} {pattern}  {dur}")
    if tool_name == "browser_navigate":
        url = args.get("url", "")
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        return _wrap(f"┊ 🌐 navigate  {_trunc(domain, 35)}  {dur}")
    if tool_name == "browser_snapshot":
        mode = "full" if args.get("full") else "compact"
        return _wrap(f"┊ 📸 snapshot  {mode}  {dur}")
    if tool_name == "browser_click":
        return _wrap(f"┊ 👆 click     {args.get('ref', '?')}  {dur}")
    if tool_name == "browser_type":
        return _wrap(f"┊ ⌨️  type      \"{_trunc(args.get('text', ''), 30)}\"  {dur}")
    if tool_name == "browser_scroll":
        d = args.get("direction", "down")
        arrow = {"down": "↓", "up": "↑", "right": "→", "left": "←"}.get(d, "↓")
        return _wrap(f"┊ {arrow}  scroll    {d}  {dur}")
    if tool_name == "browser_back":
        return _wrap(f"┊ ◀️  back      {dur}")
    if tool_name == "browser_press":
        return _wrap(f"┊ ⌨️  press     {args.get('key', '?')}  {dur}")
    if tool_name == "browser_get_images":
        return _wrap(f"┊ 🖼️  images    extracting  {dur}")
    if tool_name == "browser_vision":
        return _wrap(f"┊ 👁️  vision    analyzing page  {dur}")
    if tool_name == "todo":
        todos_arg = args.get("todos")
        merge = args.get("merge", False)
        if todos_arg is None:
            return _wrap(f"┊ 📋 plan      reading tasks  {dur}")
        elif merge:
            return _wrap(f"┊ 📋 plan      update {len(todos_arg)} task(s)  {dur}")
        else:
            return _wrap(f"┊ 📋 plan      {len(todos_arg)} task(s)  {dur}")
    if tool_name == "session_search":
        return _wrap(f"┊ 🔍 recall    \"{_trunc(args.get('query', ''), 35)}\"  {dur}")
    if tool_name == "memory":
        action = args.get("action", "?")
        target = args.get("target", "")
        if action == "add":
            return _wrap(f"┊ 🧠 memory    +{target}: \"{_trunc(args.get('content', ''), 30)}\"  {dur}")
        elif action == "replace":
            return _wrap(f"┊ 🧠 memory    ~{target}: \"{_trunc(args.get('old_text', ''), 20)}\"  {dur}")
        elif action == "remove":
            return _wrap(f"┊ 🧠 memory    -{target}: \"{_trunc(args.get('old_text', ''), 20)}\"  {dur}")
        return _wrap(f"┊ 🧠 memory    {action}  {dur}")
    if tool_name == "skills_list":
        return _wrap(f"┊ 📚 skills    list {args.get('category', 'all')}  {dur}")
    if tool_name == "skill_view":
        return _wrap(f"┊ 📚 skill     {_trunc(args.get('name', ''), 30)}  {dur}")
    if tool_name == "image_generate":
        return _wrap(f"┊ 🎨 create    {_trunc(args.get('prompt', ''), 35)}  {dur}")
    if tool_name == "text_to_speech":
        return _wrap(f"┊ 🔊 speak     {_trunc(args.get('text', ''), 30)}  {dur}")
    if tool_name == "vision_analyze":
        return _wrap(f"┊ 👁️  vision    {_trunc(args.get('question', ''), 30)}  {dur}")
    if tool_name == "mixture_of_agents":
        return _wrap(f"┊ 🧠 reason    {_trunc(args.get('user_prompt', ''), 30)}  {dur}")
    if tool_name == "send_message":
        return _wrap(f"┊ 📨 send      {args.get('target', '?')}: \"{_trunc(args.get('message', ''), 25)}\"  {dur}")
    if tool_name == "cronjob":
        action = args.get("action", "?")
        if action == "create":
            skills = args.get("skills") or ([] if not args.get("skill") else [args.get("skill")])
            label = args.get("name") or (skills[0] if skills else None) or args.get("prompt", "task")
            return _wrap(f"┊ ⏰ cron      create {_trunc(label, 24)}  {dur}")
        if action == "list":
            return _wrap(f"┊ ⏰ cron      listing  {dur}")
        return _wrap(f"┊ ⏰ cron      {action} {args.get('job_id', '')}  {dur}")
    if tool_name.startswith("rl_"):
        rl = {
            "rl_list_environments": "list envs", "rl_select_environment": f"select {args.get('name', '')}",
            "rl_get_current_config": "get config", "rl_edit_config": f"set {args.get('field', '?')}",
            "rl_start_training": "start training", "rl_check_status": f"status {args.get('run_id', '?')[:12]}",
            "rl_stop_training": f"stop {args.get('run_id', '?')[:12]}", "rl_get_results": f"results {args.get('run_id', '?')[:12]}",
            "rl_list_runs": "list runs", "rl_test_inference": "test inference",
        }
        return _wrap(f"┊ 🧪 rl        {rl.get(tool_name, tool_name.replace('rl_', ''))}  {dur}")
    if tool_name == "execute_code":
        code = args.get("code", "")
        first_line = code.strip().split("\n")[0] if code.strip() else ""
        return _wrap(f"┊ 🐍 exec      {_trunc(first_line, 35)}  {dur}")
    if tool_name == "delegate_task":
        tasks = args.get("tasks")
        if tasks and isinstance(tasks, list):
            return _wrap(f"┊ 🔀 delegate  {len(tasks)} parallel tasks  {dur}")
        return _wrap(f"┊ 🔀 delegate  {_trunc(args.get('goal', ''), 35)}  {dur}")

    preview = build_tool_preview(tool_name, args) or ""
    return _wrap(f"┊ ⚡ {tool_name[:9]:9} {_trunc(preview, 35)}  {dur}")


# =========================================================================
# Honcho 会话行(带有可点击 OSC 8 超链接的单行)
# =========================================================================

_DIM = "\033[2m"
_SKY_BLUE = "\033[38;5;117m"
_ANSI_RESET = "\033[0m"


def honcho_session_url(workspace: str, session_name: str) -> str:
    """构建会话的 Honcho 应用 URL。"""
    from urllib.parse import quote
    return (
        f"https://app.honcho.dev/explore"
        f"?workspace={quote(workspace, safe='')}"
        f"&view=sessions"
        f"&session={quote(session_name, safe='')}"
    )


def _osc8_link(url: str, text: str) -> str:
    """OSC 8 终端超链接(在 iTerm2、Ghostty、WezTerm 等中可点击)。"""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


# =========================================================================
# 上下文压力显示(CLI 用户可见警告)
# =========================================================================

# ANSI 颜色码,用于上下文压力层级
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_DIM_ANSI = "\033[2m"

# 条形图字符
_BAR_FILLED = "▰"
_BAR_EMPTY = "▱"
_BAR_WIDTH = 20


def format_context_pressure(
    compaction_progress: float,
    threshold_tokens: int,
    threshold_percent: float,
    compression_enabled: bool = True,
) -> str:
    """为 CLI 显示构建格式化的上下文压力行。

    条形图和百分比显示距压缩阈值的进度,
    而非原始上下文窗口。100% = 触发压缩。

    Args:
        compaction_progress: 距压缩有多近(0.0–1.0,1.0 = 触发)。
        threshold_tokens: 压缩阈值(token 数)。
        threshold_percent: 压缩阈值占上下文窗口的比例。
        compression_enabled: 自动压缩是否激活。
    """
    pct_int = min(int(compaction_progress * 100), 100)
    filled = min(int(compaction_progress * _BAR_WIDTH), _BAR_WIDTH)
    bar = _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)

    threshold_k = f"{threshold_tokens // 1000}k" if threshold_tokens >= 1000 else str(threshold_tokens)
    threshold_pct_int = int(threshold_percent * 100)

    color = f"{_BOLD}{_YELLOW}"
    icon = "⚠"
    if compression_enabled:
        hint = "compaction approaching"
    else:
        hint = "no auto-compaction"

    return (
        f"  {color}{icon} context {bar} {pct_int}% to compaction{_ANSI_RESET}"
        f"  {_DIM_ANSI}{threshold_k} threshold ({threshold_pct_int}%) · {hint}{_ANSI_RESET}"
    )


def format_context_pressure_gateway(
    compaction_progress: float,
    threshold_percent: float,
    compression_enabled: bool = True,
) -> str:
    """为消息平台构建纯文本上下文压力通知。

    无 ANSI — 只有 Unicode 和纯文本,适合 Telegram/Discord 等。
    百分比显示距压缩阈值的进度。
    """
    pct_int = min(int(compaction_progress * 100), 100)
    filled = min(int(compaction_progress * _BAR_WIDTH), _BAR_WIDTH)
    bar = _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)

    threshold_pct_int = int(threshold_percent * 100)

    icon = "⚠️"
    if compression_enabled:
        hint = f"Context compaction approaching (threshold: {threshold_pct_int}% of window)."
    else:
        hint = "Auto-compaction is disabled — context may be truncated."

    return f"{icon} Context: {bar} {pct_int}% to compaction\n{hint}"
