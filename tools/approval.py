"""危险命令审批——检测、提示和每会话状态。

此模块是危险命令系统的唯一真实来源：
- 模式检测（DANGEROUS_PATTERNS、detect_dangerous_command）
- 每会话审批状态（线程安全，按 session_key 键控）
- 审批提示（CLI 交互式 + 网关异步）
- 通过辅助 LLM 智能审批（自动批准低风险命令）
- 永久白名单持久化（config.yaml）
"""

import contextvars
import logging
import os
import re
import sys
import threading
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# 每个线程/任务的网关会话标识。
# 网关在执行器线程中并发运行智能体回合，因此读取
# 进程全局环境变量获取会话标识是竞争条件的。保留环境变量回退
# 用于遗留的单线程调用者，但在设置时优先使用上下文本地值。
_approval_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "approval_session_key",
    default="",
)


def set_current_session_key(session_key: str) -> contextvars.Token[str]:
    """将活动审批会话密钥绑定到当前上下文。"""
    return _approval_session_key.set(session_key or "")


def reset_current_session_key(token: contextvars.Token[str]) -> None:
    """恢复之前的审批会话密钥上下文。"""
    _approval_session_key.reset(token)


def get_current_session_key(default: str = "default") -> str:
    """返回活动会话密钥，优先使用上下文本地状态。"""
    session_key = _approval_session_key.get()
    if session_key:
        return session_key
    return os.getenv("KCLAW_SESSION_KEY", default)

# 即使通过 $HOME 或 $KCLAW_HOME 等 shell 扩展引用也应触发审批的敏感写目标。
_SSH_SENSITIVE_PATH = r'(?:~|\$home|\$\{home\})/\.ssh(?:/|$)'
_KCLAW_ENV_PATH = (
    r'(?:~\/\.kclaw/|'
    r'(?:\$home|\$\{home\})/\.kclaw/|'
    r'(?:\$kclaw_home|\$\{kclaw_home\})/)'
    r'\.env\b'
)
_SENSITIVE_WRITE_TARGET = (
    r'(?:/etc/|/dev/sd|'
    rf'{_SSH_SENSITIVE_PATH}|'
    rf'{_KCLAW_ENV_PATH})'
)

# =========================================================================
# 危险命令模式
# =========================================================================

DANGEROUS_PATTERNS = [
    (r'\brm\s+(-[^\s]*\s+)*/', "删除根路径下的文件"),
    (r'\brm\s+-[^\s]*r', "递归删除"),
    (r'\brm\s+--recursive\b', "递归删除（长标志）"),
    (r'\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b', "全局/其他可写权限"),
    (r'\bchmod\s+--recursive\b.*(777|666|o\+[rwx]*w|a\+[rwx]*w)', "递归全局/其他可写（长标志）"),
    (r'\bchown\s+(-[^\s]*)?R\s+root', "递归 chown 到 root"),
    (r'\bchown\s+--recursive\b.*root', "递归 chown 到 root（长标志）"),
    (r'\bmkfs\b', "格式化文件系统"),
    (r'\bdd\s+.*if=', "磁盘复制"),
    (r'>\s*/dev/sd', "写入块设备"),
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),
    (r'\bDELETE\s+FROM\b(?!.*\bWHERE\b)', "不带 WHERE 的 SQL DELETE"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
    (r'>\s*/etc/', "覆盖系统配置"),
    (r'\bsystemctl\s+(stop|disable|mask)\b', "停止/禁用系统服务"),
    (r'\bkill\s+-9\s+-1\b', "杀死所有进程"),
    (r'\bpkill\s+-9\b', "强制杀死进程"),
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork 炸弹"),
    # 任何通过 -c 或组合标志（如 -lc、-ic 等）调用 shell
    (r'\b(bash|sh|zsh|ksh)\s+-[^\s]*c(\s+|$)', "通过 -c/-lc 标志执行 shell 命令"),
    (r'\b(python[23]?|perl|ruby|node)\s+-[ec]\s+', "通过 -e/-c 标志执行脚本"),
    (r'\b(curl|wget)\b.*\|\s*(ba)?sh\b', "将远程内容管道到 shell"),
    (r'\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b', "通过进程替换执行远程脚本"),
    (rf'\btee\b.*["\']?{_SENSITIVE_WRITE_TARGET}', "通过 tee 覆盖系统文件"),
    (rf'>>?\s*["\']?{_SENSITIVE_WRITE_TARGET}', "通过重定向覆盖系统文件"),
    (r'\bxargs\s+.*\brm\b', "xargs 配合 rm"),
    (r'\bfind\b.*-exec\s+(/\S*/)?rm\b', "find -exec rm"),
    (r'\bfind\b.*-delete\b', "find -delete"),
    # 网关保护：永远不要在 systemd 管理之外启动网关
    (r'gateway\s+run\b.*(&\s*$|&\s*;|\bdisown\b|\bsetsid\b)', "在 systemd 之外启动网关（使用 'systemctl --user restart kclaw-gateway'）"),
    (r'\bnohup\b.*gateway\s+run\b', "在 systemd 之外启动网关（使用 'systemctl --user restart kclaw-gateway'）"),
    # 自我终止保护：防止智能体杀死自己的进程
    (r'\b(pkill|killall)\b.*\b(kclaw|gateway|cli\.py)\b', "杀死 kclaw/gateway 进程（自我终止）"),
    # 文件复制/移动/编辑到敏感系统路径
    (r'\b(cp|mv|install)\b.*\s/etc/', "复制/移动文件到 /etc/"),
    (r'\bsed\s+-[^\s]*i.*\s/etc/', "原地编辑系统配置"),
    (r'\bsed\s+--in-place\b.*\s/etc/', "原地编辑系统配置（长标志）"),
]


def _legacy_pattern_key(pattern: str) -> str:
    """为向后兼容重现旧的正则表达式派生审批密钥。"""
    return pattern.split(r'\b')[1] if r'\b' in pattern else pattern[:20]


_PATTERN_KEY_ALIASES: dict[str, set[str]] = {}
for _pattern, _description in DANGEROUS_PATTERNS:
    _legacy_key = _legacy_pattern_key(_pattern)
    _canonical_key = _description
    _PATTERN_KEY_ALIASES.setdefault(_canonical_key, set()).update({_canonical_key, _legacy_key})
    _PATTERN_KEY_ALIASES.setdefault(_legacy_key, set()).update({_legacy_key, _canonical_key})


def _approval_key_aliases(pattern_key: str) -> set[str]:
    """返回应匹配此模式的所有审批密钥。

    新的审批使用人类可读的描述字符串，但较旧的
    command_allowlist 条目和会话审批可能仍包含
    历史正则表达式派生的密钥。
    """
    return _PATTERN_KEY_ALIASES.get(pattern_key, {pattern_key})


# =========================================================================
# 检测
# =========================================================================

def _normalize_command_for_detection(command: str) -> str:
    """在危险模式匹配之前规范化命令字符串。

    剥离 ANSI 转义序列（通过 tools.ansi_strip 的完整 ECMA-48），
    空字节，并规范化 Unicode 全角字符，以便
    混淆技术无法绕过基于模式的检测。
    """
    from tools.ansi_strip import strip_ansi

    # 剥离所有 ANSI 转义序列（CSI、OSC、DCS、8 位 C1 等）
    command = strip_ansi(command)
    # 剥离空字节
    command = command.replace('\x00', '')
    # 规范化 Unicode（全角拉丁文、半角片假名等）
    command = unicodedata.normalize('NFKC', command)
    return command


def detect_dangerous_command(command: str) -> tuple:
    """检查命令是否匹配任何危险模式。

    返回:
        (is_dangerous, pattern_key, description) 或 (False, None, None)
    """
    command_lower = _normalize_command_for_detection(command).lower()
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE | re.DOTALL):
            pattern_key = description
            return (True, pattern_key, description)
    return (False, None, None)


# =========================================================================
# 每会话审批状态（线程安全）
# =========================================================================

_lock = threading.Lock()
_pending: dict[str, dict] = {}
_session_approved: dict[str, set] = {}
_permanent_approved: set = set()

# =========================================================================
# 阻塞式网关审批（镜像 CLI 的同步 input() 流程）
# =========================================================================
# 每会话待审批队列。多个线程（并行
# 子智能体、execute_code RPC 处理器）可以并发阻塞——每个获得
# 自己的 threading.Event。/approve 解析最旧的，/approve all
# 解析会话中每个待审批的。

class _ApprovalEntry:
    """网关会话内的一个待处理危险命令审批。"""
    __slots__ = ("event", "data", "result")

    def __init__(self, data: dict):
        self.event = threading.Event()
        self.data = data          # command, description, pattern_keys, …
        self.result: Optional[str] = None  # "once"|"session"|"always"|"deny"


_gateway_queues: dict[str, list] = {}        # session_key → [_ApprovalEntry, …]
_gateway_notify_cbs: dict[str, object] = {}  # session_key → callable(approval_data)


def register_gateway_notify(session_key: str, cb) -> None:
    """注册每会话回调以向用户发送审批请求。

    回调签名是 ``cb(approval_data: dict) -> None``，其中
    *approval_data* 包含 ``command``、``description`` 和
    ``pattern_keys``。回调桥接 sync→async（在智能体
    线程中运行，必须在事件循环上调度实际发送）。
    """
    with _lock:
        _gateway_notify_cbs[session_key] = cb


def unregister_gateway_notify(session_key: str) -> None:
    """注销每会话网关审批回调。

    发出信号给此会话的所有阻塞线程，以免它们永远挂起
    （例如当智能体运行完成或被中断时）。
    """
    with _lock:
        _gateway_notify_cbs.pop(session_key, None)
        entries = _gateway_queues.pop(session_key, [])
        for entry in entries:
            entry.event.set()


def resolve_gateway_approval(session_key: str, choice: str,
                             resolve_all: bool = False) -> int:
    """由网关的 /approve 或 /deny 处理器调用以解除阻塞
    等待的智能体线程。

    当 *resolve_all* 为 True 时，会话中每个待审批的都会被
    立即解析（``/approve all``）。否则只解析最旧的一个
    （FIFO）。

    返回解析的审批数量（0 表示没有待审批的）。
    """
    with _lock:
        queue = _gateway_queues.get(session_key)
        if not queue:
            return 0
        if resolve_all:
            targets = list(queue)
            queue.clear()
        else:
            targets = [queue.pop(0)]
        if not queue:
            _gateway_queues.pop(session_key, None)

    for entry in targets:
        entry.result = choice
        entry.event.set()
    return len(targets)


def has_blocking_approval(session_key: str) -> bool:
    """检查会话是否有一个或多个阻塞网关审批等待中。"""
    with _lock:
        return bool(_gateway_queues.get(session_key))


def pending_approval_count(session_key: str) -> int:
    """返回会话的待处理阻塞审批数量。"""
    with _lock:
        return len(_gateway_queues.get(session_key, []))


def submit_pending(session_key: str, approval: dict):
    """为会话存储待处理的审批请求。"""
    with _lock:
        _pending[session_key] = approval


def pop_pending(session_key: str) -> Optional[dict]:
    """检索并移除会话的待审批。"""
    with _lock:
        return _pending.pop(session_key, None)


def has_pending(session_key: str) -> bool:
    """检查会话是否有待处理的审批请求。"""
    with _lock:
        return session_key in _pending


def approve_session(session_key: str, pattern_key: str):
    """仅为此会话批准一个模式。"""
    with _lock:
        _session_approved.setdefault(session_key, set()).add(pattern_key)


def is_approved(session_key: str, pattern_key: str) -> bool:
    """检查模式是否已批准（会话范围或永久）。

    同时接受当前规范密钥和旧正则表达式派生的密钥，以便
    现有的 command_allowlist 条目在密钥迁移后继续工作。
    """
    aliases = _approval_key_aliases(pattern_key)
    with _lock:
        if any(alias in _permanent_approved for alias in aliases):
            return True
        session_approvals = _session_approved.get(session_key, set())
        return any(alias in session_approvals for alias in aliases)


def approve_permanent(pattern_key: str):
    """将模式添加到永久白名单。"""
    with _lock:
        _permanent_approved.add(pattern_key)


def load_permanent(patterns: set):
    """从配置批量加载永久白名单条目。"""
    with _lock:
        _permanent_approved.update(patterns)


def clear_session(session_key: str):
    """清除会话的所有审批和待处理请求。"""
    with _lock:
        _session_approved.pop(session_key, None)
        _pending.pop(session_key, None)
        _gateway_notify_cbs.pop(session_key, None)
        # 发出信号给所有阻塞线程，以免它们永远挂起
        entries = _gateway_queues.pop(session_key, [])
        for entry in entries:
            entry.event.set()


# =========================================================================
# 永久白名单的配置持久化
# =========================================================================

def load_permanent_allowlist() -> set:
    """从配置加载永久允许的命令模式。

    同时将它们同步到审批模块，以便 is_approved() 对
    之前会话中添加的 'always' 模式有效。
    """
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        patterns = set(config.get("command_allowlist", []) or [])
        if patterns:
            load_permanent(patterns)
        return patterns
    except Exception:
        return set()


def save_permanent_allowlist(patterns: set):
    """将永久允许的命令模式保存到配置。"""
    try:
        from kclaw_cli.config import load_config, save_config
        config = load_config()
        config["command_allowlist"] = list(patterns)
        save_config(config)
    except Exception as e:
        logger.warning("无法保存白名单：%s", e)


# =========================================================================
# 审批提示 + 编排
# =========================================================================

def prompt_dangerous_approval(command: str, description: str,
                              timeout_seconds: int | None = None,
                              allow_permanent: bool = True,
                              approval_callback=None) -> str:
    """提示用户批准危险命令（仅限 CLI）。

    参数:
        allow_permanent: 当为 False 时，隐藏 [a]lways 选项（当存在
            tirith 警告时使用，因为对于内容级安全发现，
            广泛的永久白名单是不合适的）。
        approval_callback: CLI 注册的可选回调用于
            prompt_toolkit 集成。签名：
            (command, description, *, allow_permanent=True) -> str。

    返回: 'once'、'session'、'always' 或 'deny'
    """
    if timeout_seconds is None:
        timeout_seconds = _get_approval_timeout()

    if approval_callback is not None:
        try:
            return approval_callback(command, description,
                                     allow_permanent=allow_permanent)
        except Exception:
            return "deny"

    os.environ["KCLAW_SPINNER_PAUSE"] = "1"
    try:
        while True:
            print()
            print(f"  ⚠️  危险命令：{description}")
            print(f"      {command}")
            print()
            if allow_permanent:
                print("      [o]一次  |  [s]会话  |  [a]永久  |  [d]拒绝")
            else:
                print("      [o]一次  |  [s]会话  |  [d]拒绝")
            print()
            sys.stdout.flush()

            result = {"choice": ""}

            def get_input():
                try:
                    prompt = "      选择 [o/s/a/D]: " if allow_permanent else "      选择 [o/s/D]: "
                    result["choice"] = input(prompt).strip().lower()
                except (EOFError, OSError):
                    result["choice"] = ""

            thread = threading.Thread(target=get_input, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)

            if thread.is_alive():
                print("\n      ⏱ 超时 - 拒绝命令")
                return "deny"

            choice = result["choice"]
            if choice in ('o', 'once'):
                print("      ✓ 允许一次")
                return "once"
            elif choice in ('s', 'session'):
                print("      ✓ 仅允许本次会话")
                return "session"
            elif choice in ('a', 'always'):
                if not allow_permanent:
                    print("      ✓ 仅允许本次会话")
                    return "session"
                print("      ✓ 添加到永久白名单")
                return "always"
            else:
                print("      ✗ 已拒绝")
                return "deny"

    except (EOFError, KeyboardInterrupt):
        print("\n      ✗ 已取消")
        return "deny"
    finally:
        if "KCLAW_SPINNER_PAUSE" in os.environ:
            del os.environ["KCLAW_SPINNER_PAUSE"]
        print()
        sys.stdout.flush()


def _normalize_approval_mode(mode) -> str:
    """规范化从 YAML/配置加载的审批模式值。

    YAML 1.1 将裸词（如 `off`）视为布尔值，因此配置条目如
    `approvals:\n  mode: off` 被解析为 False，除非加引号。
    将其视为预期的字符串模式，而不是回退到手动审批。
    """
    if isinstance(mode, bool):
        return "off" if mode is False else "manual"
    if isinstance(mode, str):
        normalized = mode.strip().lower()
        return normalized or "manual"
    return "manual"


def _get_approval_config() -> dict:
    """读取审批配置块。返回包含 'mode'、'timeout' 等的字典。"""
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        return config.get("approvals", {}) or {}
    except Exception:
        return {}


def _get_approval_mode() -> str:
    """从配置读取审批模式。返回 'manual'、'smart' 或 'off'。"""
    mode = _get_approval_config().get("mode", "manual")
    return _normalize_approval_mode(mode)


def _get_approval_timeout() -> int:
    """从配置读取审批超时。默认为 60 秒。"""
    try:
        return int(_get_approval_config().get("timeout", 60))
    except (ValueError, TypeError):
        return 60


def _smart_approve(command: str, description: str) -> str:
    """使用辅助 LLM 评估风险并决定审批。

    如果 LLM 确定命令安全则返回 'approve'，
    如果确实危险则返回 'deny'，如果不确定则返回 'escalate'。

    灵感来自 OpenAI Codex 的智能审批守护子智能体
    (openai/codex#13860)。
    """
    try:
        from agent.auxiliary_client import get_text_auxiliary_client, auxiliary_max_tokens_param

        client, model = get_text_auxiliary_client(task="approval")
        if not client or not model:
            logger.debug("智能审批：无辅助客户端可用，升级处理")
            return "escalate"

        prompt = f"""你是一个 AI 编程智能体的安全审查员。一个终端命令被模式匹配标记为潜在危险。

命令：{command}
标记原因：{description}

评估此命令的实际风险。许多被标记的命令是误报——例如，`python -c "print('hello')"` 被标记为"通过 -c 标志执行脚本"，但完全无害。

规则：
- 如果命令明显安全（良性脚本执行、安全文件操作、开发工具、包安装、git 操作等），则批准
- 如果命令可能真正损害系统（递归删除重要路径、覆盖系统文件、fork 炸弹、擦除磁盘、删除数据库等），则拒绝
- 如果你不确定，则升级

只用一个词回应：APPROVE、DENY 或 ESCALATE"""

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **auxiliary_max_tokens_param(16),
            temperature=0,
        )

        answer = (response.choices[0].message.content or "").strip().upper()

        if "APPROVE" in answer:
            return "approve"
        elif "DENY" in answer:
            return "deny"
        else:
            return "escalate"

    except Exception as e:
        logger.debug("智能审批：LLM 调用失败（%s），升级处理", e)
        return "escalate"


def check_dangerous_command(command: str, env_type: str,
                            approval_callback=None) -> dict:
    """检查命令是否危险并处理审批。

    这是由 terminal_tool 在执行任何命令之前调用的主要入口点。
    它协调检测、会话检查和提示。

    参数:
        command: 要检查的 shell 命令。
        env_type: 终端后端类型（'local'、'ssh'、'docker' 等）。
        approval_callback: 用于交互式提示的可选 CLI 回调。

    返回:
        {"approved": True/False, "message": str or None, ...}
    """
    if env_type in ("docker", "singularity", "modal", "daytona"):
        return {"approved": True, "message": None}

    # --yolo：绕过所有审批提示
    if os.getenv("KCLAW_YOLO_MODE"):
        return {"approved": True, "message": None}

    is_dangerous, pattern_key, description = detect_dangerous_command(command)
    if not is_dangerous:
        return {"approved": True, "message": None}

    session_key = get_current_session_key()
    if is_approved(session_key, pattern_key):
        return {"approved": True, "message": None}

    is_cli = os.getenv("KCLAW_INTERACTIVE")
    is_gateway = os.getenv("KCLAW_GATEWAY_SESSION")

    if not is_cli and not is_gateway:
        return {"approved": True, "message": None}

    if is_gateway or os.getenv("KCLAW_EXEC_ASK"):
        submit_pending(session_key, {
            "command": command,
            "pattern_key": pattern_key,
            "description": description,
        })
        return {
            "approved": False,
            "pattern_key": pattern_key,
            "status": "approval_required",
            "command": command,
            "description": description,
            "message": (
                f"⚠️ 此命令可能危险（{description}）。"
                f"正在请求用户批准。\n\n**命令：**\n```\n{command}\n```"
            ),
        }

    choice = prompt_dangerous_approval(command, description,
                                       approval_callback=approval_callback)

    if choice == "deny":
        return {
            "approved": False,
            "message": f"已阻止：用户拒绝此潜在危险命令（匹配 '{description}' 模式）。请勿重试此命令——用户已明确拒绝。",
            "pattern_key": pattern_key,
            "description": description,
        }

    if choice == "session":
        approve_session(session_key, pattern_key)
    elif choice == "always":
        approve_session(session_key, pattern_key)
        approve_permanent(pattern_key)
        save_permanent_allowlist(_permanent_approved)

    return {"approved": True, "message": None}


# =========================================================================
# 组合预执行保护（tirith + 危险命令检测）
# =========================================================================

def _format_tirith_description(tirith_result: dict) -> str:
    """从 tirith 发现构建人类可读的描述。

    包括每个发现的严重性、标题和描述，以便用户
    可以做出明智的批准决定。
    """
    findings = tirith_result.get("findings") or []
    if not findings:
        summary = tirith_result.get("summary") or "检测到安全问题"
        return f"安全扫描：{summary}"

    parts = []
    for f in findings:
        severity = f.get("severity", "")
        title = f.get("title", "")
        desc = f.get("description", "")
        if title and desc:
            parts.append(f"[{severity}] {title}: {desc}" if severity else f"{title}: {desc}")
        elif title:
            parts.append(f"[{severity}] {title}" if severity else title)
    if not parts:
        summary = tirith_result.get("summary") or "检测到安全问题"
        return f"安全扫描：{summary}"

    return "安全扫描 — " + "; ".join(parts)


def check_all_command_guards(command: str, env_type: str,
                             approval_callback=None) -> dict:
    """运行所有预执行安全检查并返回单个批准决定。

    从 tirith 和危险命令检测收集发现，然后
    将它们作为单个组合批准请求呈现。这防止
    当只向用户显示其中一个检查时，网关 force=True 重放绕过另一个检查。
    """
    # 为两个检查跳过容器
    if env_type in ("docker", "singularity", "modal", "daytona"):
        return {"approved": True, "message": None}

    # --yolo 或 approvals.mode=off：绕过所有审批提示
    approval_mode = _get_approval_mode()
    if os.getenv("KCLAW_YOLO_MODE") or approval_mode == "off":
        return {"approved": True, "message": None}

    is_cli = os.getenv("KCLAW_INTERACTIVE")
    is_gateway = os.getenv("KCLAW_GATEWAY_SESSION")
    is_ask = os.getenv("KCLAW_EXEC_ASK")

    # 保留现有的非交互式行为：在 CLI/gateway/ask
    # 流程之外，我们不阻塞审批，也不执行外部保护工作。
    if not is_cli and not is_gateway and not is_ask:
        return {"approved": True, "message": None}

    # --- 阶段 1：从两个检查收集发现 ---

    # Tirith 检查——包装器保证预期失败不会引发异常。
    # 只捕获 ImportError（模块未安装）。
    tirith_result = {"action": "allow", "findings": [], "summary": ""}
    try:
        from tools.tirith_security import check_command_security
        tirith_result = check_command_security(command)
    except ImportError:
        pass  # tirith 模块未安装——允许

    # 危险命令检查（仅检测，不审批）
    is_dangerous, pattern_key, description = detect_dangerous_command(command)

    # --- 阶段 2：决定 ---

    # 收集需要批准的警告
    warnings = []  # (pattern_key, description, is_tirith) 列表

    session_key = get_current_session_key()

    # Tirith block/warn → 通过丰富的发现结果可批准警告。
    # 之前，tirith "block" 是带有无批准提示的硬阻塞。
    # 现在 block 和 warn 都经过审批流程，以便用户可以
    # 查看解释并在理解风险时批准。
    if tirith_result["action"] in ("block", "warn"):
        findings = tirith_result.get("findings") or []
        rule_id = findings[0].get("rule_id", "unknown") if findings else "unknown"
        tirith_key = f"tirith:{rule_id}"
        tirith_desc = _format_tirith_description(tirith_result)
        if not is_approved(session_key, tirith_key):
            warnings.append((tirith_key, tirith_desc, True))

    if is_dangerous:
        if not is_approved(session_key, pattern_key):
            warnings.append((pattern_key, description, False))

    # 没有需要警告的
    if not warnings:
        return {"approved": True, "message": None}

    # --- 阶段 2.5：智能审批（辅助 LLM 风险评估）---
    # 当 approvals.mode=smart 时，在提示用户之前询问辅助 LLM。
    # 灵感来自 OpenAI Codex 的智能审批守护子智能体
    # (openai/codex#13860)。
    if approval_mode == "smart":
        combined_desc_for_llm = "; ".join(desc for _, desc, _ in warnings)
        verdict = _smart_approve(command, combined_desc_for_llm)
        if verdict == "approve":
            # 自动批准并为这些模式授予会话级批准
            for key, _, _ in warnings:
                approve_session(session_key, key)
            logger.debug("智能审批：自动批准 '%s'（%s）",
                         command[:60], combined_desc_for_llm)
            return {"approved": True, "message": None,
                    "smart_approved": True,
                    "description": combined_desc_for_llm}
        elif verdict == "deny":
            combined_desc_for_llm = "; ".join(desc for _, desc, _ in warnings)
            return {
                "approved": False,
                "message": f"被智能审批阻止：{combined_desc_for_llm}。"
                           "该命令被评估为确实危险。请勿重试。",
                "smart_denied": True,
            }
        # verdict == "escalate" → 继续进行手动提示

    # --- 阶段 3：批准 ---

    # 合并描述以进行单一批准提示
    combined_desc = "; ".join(desc for _, desc, _ in warnings)
    primary_key = warnings[0][0]
    all_keys = [key for key, _, _ in warnings]
    has_tirith = any(is_t for _, _, is_t in warnings)

    # 网关/异步批准——阻塞智能体线程直到用户
    # 使用 /approve 或 /deny 回应，镜像 CLI 的同步
    # input() 流程。智能体永远不会看到 "approval_required"；它要么
    # 获得命令输出（已批准），要么获得明确的 "BLOCKED" 消息。
    if is_gateway or is_ask:
        notify_cb = None
        with _lock:
            notify_cb = _gateway_notify_cbs.get(session_key)

        if notify_cb is not None:
            # --- 基于队列的阻塞网关批准 ---
            # 每次调用获得自己的 _ApprovalEntry，以便并行子智能体
            # 和 execute_code 线程可以并发阻塞。
            approval_data = {
                "command": command,
                "pattern_key": primary_key,
                "pattern_keys": all_keys,
                "description": combined_desc,
            }
            entry = _ApprovalEntry(approval_data)
            with _lock:
                _gateway_queues.setdefault(session_key, []).append(entry)

            # 通知用户（桥接同步智能体线程 → 异步网关）
            try:
                notify_cb(approval_data)
            except Exception as exc:
                logger.warning("网关批准通知失败：%s", exc)
                with _lock:
                    queue = _gateway_queues.get(session_key, [])
                    if entry in queue:
                        queue.remove(entry)
                    if not queue:
                        _gateway_queues.pop(session_key, None)
                return {
                    "approved": False,
                    "message": "已阻止：向用户发送批准请求失败。请勿重试。",
                    "pattern_key": primary_key,
                    "description": combined_desc,
                }

            # 阻塞直到用户回应或超时（默认 5 分钟）
            timeout = _get_approval_config().get("gateway_timeout", 300)
            try:
                timeout = int(timeout)
            except (ValueError, TypeError):
                timeout = 300
            resolved = entry.event.wait(timeout=timeout)

            # 从队列中清理此条目
            with _lock:
                queue = _gateway_queues.get(session_key, [])
                if entry in queue:
                    queue.remove(entry)
                if not queue:
                    _gateway_queues.pop(session_key, None)

            choice = entry.result
            if not resolved or choice is None or choice == "deny":
                reason = "超时" if not resolved else "被用户拒绝"
                return {
                    "approved": False,
                    "message": f"已阻止：命令 {reason}。请勿重试此命令。",
                    "pattern_key": primary_key,
                    "description": combined_desc,
                }

            # 用户批准——根据范围持久化（与 CLI 相同的逻辑）
            for key, _, is_tirith in warnings:
                if choice == "session" or (choice == "always" and is_tirith):
                    approve_session(session_key, key)
                elif choice == "always":
                    approve_session(session_key, key)
                    approve_permanent(key)
                    save_permanent_allowlist(_permanent_approved)
                # choice == "once"：不持久化——命令仅允许这次，
                # 与 CLI 的行为一致。

            return {"approved": True, "message": None,
                    "user_approved": True, "description": combined_desc}

        # 回退：未注册网关回调（例如 cron、batch）。
        # 为向后兼容返回 approval_required。
        submit_pending(session_key, {
            "command": command,
            "pattern_key": primary_key,
            "pattern_keys": all_keys,
            "description": combined_desc,
        })
        return {
            "approved": False,
            "pattern_key": primary_key,
            "status": "approval_required",
            "command": command,
            "description": combined_desc,
            "message": (
                f"⚠️ {combined_desc}。正在请求用户批准。\n\n**命令：**\n```\n{command}\n```"
            ),
        }

    # CLI 交互式：单一合并提示
    # 当存在任何 tirith 警告时隐藏 [a]lways
    choice = prompt_dangerous_approval(command, combined_desc,
                                       allow_permanent=not has_tirith,
                                       approval_callback=approval_callback)

    if choice == "deny":
        return {
            "approved": False,
            "message": "已阻止：用户拒绝。请勿重试。",
            "pattern_key": primary_key,
            "description": combined_desc,
        }

    # 为每个警告单独持久化批准
    for key, _, is_tirith in warnings:
        if choice == "session" or (choice == "always" and is_tirith):
            # tirith：仅会话（无永久广泛白名单）
            approve_session(session_key, key)
        elif choice == "always":
            # 危险模式：永久允许
            approve_session(session_key, key)
            approve_permanent(key)
            save_permanent_allowlist(_permanent_approved)

    return {"approved": True, "message": None,
            "user_approved": True, "description": combined_desc}


# 模块导入时从配置加载永久白名单
load_permanent_allowlist()
