"""ByteRover 记忆插件 — MemoryProvider 接口。

通过 ByteRover CLI (``brv``) 实现持久化记忆。将知识组织成
层级上下文树，支持分级检索（模糊文本 → LLM 驱动的
搜索）。本地优先，可选云同步。

原始 PR #3499 由 hieuntg81 提交，已适配 MemoryProvider ABC。

依赖：``brv`` CLI 已安装（npm install -g byterover-cli 或
curl -fsSL https://byterover.dev/install.sh | sh）。

通过环境变量配置（通过各 profile 的 .env 进行 profile 级作用域）：
  BRV_API_KEY   — ByteRover API 密钥（云功能需要，本地可选）

工作目录：$KCLAW_HOME/byterover/（profile 级上下文树）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# 超时设置
_QUERY_TIMEOUT = 10   # brv query — 应该很快
_CURATE_TIMEOUT = 120  # brv curate — 可能涉及 LLM 处理

# 最小长度以过滤噪音
_MIN_QUERY_LEN = 10
_MIN_OUTPUT_LEN = 20


# ---------------------------------------------------------------------------
# brv 二进制文件路径解析（缓存，线程安全）
# ---------------------------------------------------------------------------

_brv_path_lock = threading.Lock()
_cached_brv_path: Optional[str] = None


def _resolve_brv_path() -> Optional[str]:
    """在 PATH 或常见安装位置查找 brv 二进制文件。"""
    global _cached_brv_path
    with _brv_path_lock:
        if _cached_brv_path is not None:
            return _cached_brv_path if _cached_brv_path != "" else None

    found = shutil.which("brv")
    if not found:
        home = Path.home()
        candidates = [
            home / ".brv-cli" / "bin" / "brv",
            Path("/usr/local/bin/brv"),
            home / ".npm-global" / "bin" / "brv",
        ]
        for c in candidates:
            if c.exists():
                found = str(c)
                break

    with _brv_path_lock:
        if _cached_brv_path is not None:
            return _cached_brv_path if _cached_brv_path != "" else None
        _cached_brv_path = found or ""
    return found


def _run_brv(args: List[str], timeout: int = _QUERY_TIMEOUT,
             cwd: str = None) -> dict:
    """运行 brv CLI 命令。返回 {success, output, error}。"""
    brv_path = _resolve_brv_path()
    if not brv_path:
        return {"success": False, "error": "brv CLI not found. Install: npm install -g byterover-cli"}

    cmd = [brv_path] + args
    effective_cwd = cwd or str(_get_brv_cwd())
    Path(effective_cwd).mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    brv_bin_dir = str(Path(brv_path).parent)
    env["PATH"] = brv_bin_dir + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=effective_cwd, env=env,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            return {"success": True, "output": stdout}
        return {"success": False, "error": stderr or stdout or f"brv exited {result.returncode}"}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"brv timed out after {timeout}s"}
    except FileNotFoundError:
        global _cached_brv_path
        with _brv_path_lock:
            _cached_brv_path = None
        return {"success": False, "error": "brv CLI not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_brv_cwd() -> Path:
    """brv 上下文树的 profile 级工作目录。"""
    from kclaw_constants import get_kclaw_home
    return get_kclaw_home() / "byterover"


# ---------------------------------------------------------------------------
# 工具模式
# ---------------------------------------------------------------------------

QUERY_SCHEMA = {
    "name": "brv_query",
    "description": (
        "Search ByteRover's persistent knowledge tree for relevant context. "
        "Returns memories, project knowledge, architectural decisions, and "
        "patterns from previous sessions. Use for any question where past "
        "context would help."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
        },
        "required": ["query"],
    },
}

CURATE_SCHEMA = {
    "name": "brv_curate",
    "description": (
        "Store important information in ByteRover's persistent knowledge tree. "
        "Use for architectural decisions, bug fixes, user preferences, project "
        "patterns — anything worth remembering across sessions. ByteRover's LLM "
        "automatically categorizes and organizes the memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to remember."},
        },
        "required": ["content"],
    },
}

STATUS_SCHEMA = {
    "name": "brv_status",
    "description": "Check ByteRover status — CLI version, context tree stats, cloud sync state.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


# ---------------------------------------------------------------------------
# MemoryProvider 实现
# ---------------------------------------------------------------------------

class ByteRoverMemoryProvider(MemoryProvider):
    """通过 brv CLI 实现 ByteRover 持久化记忆。"""

    def __init__(self):
        self._cwd = ""
        self._session_id = ""
        self._turn_count = 0
        self._sync_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "byterover"

    def is_available(self) -> bool:
        """检查 brv CLI 是否已安装。不进行网络调用。"""
        return _resolve_brv_path() is not None

    def get_config_schema(self):
        return [
            {
                "key": "api_key",
                "description": "ByteRover API key (optional, for cloud sync)",
                "secret": True,
                "env_var": "BRV_API_KEY",
                "url": "https://app.byterover.dev",
            },
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        self._cwd = str(_get_brv_cwd())
        self._session_id = session_id
        self._turn_count = 0
        Path(self._cwd).mkdir(parents=True, exist_ok=True)

    def system_prompt_block(self) -> str:
        if not _resolve_brv_path():
            return ""
        return (
            "# ByteRover Memory\n"
            "Active. Persistent knowledge tree with hierarchical context.\n"
            "Use brv_query to search past knowledge, brv_curate to store "
            "important facts, brv_status to check state."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """在代理首次 LLM 调用之前同步运行 brv 查询。

        阻塞直到查询完成（最多 _QUERY_TIMEOUT 秒），确保
        结果在模型调用之前可以作为上下文使用。
        """
        if not query or len(query.strip()) < _MIN_QUERY_LEN:
            return ""
        result = _run_brv(
            ["query", "--", query.strip()[:5000]],
            timeout=_QUERY_TIMEOUT, cwd=self._cwd,
        )
        if result["success"] and result.get("output"):
            output = result["output"].strip()
            if len(output) > _MIN_OUTPUT_LEN:
                return f"## ByteRover Context\n{output}"
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """无操作：prefetch() 现在在轮次开始时同步运行。"""
        pass

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """在后台整理对话轮次（非阻塞）。"""
        self._turn_count += 1

        # 只整理实质性的轮次
        if len(user_content.strip()) < _MIN_QUERY_LEN:
            return

        def _sync():
            try:
                combined = f"User: {user_content[:2000]}\nAssistant: {assistant_content[:2000]}"
                _run_brv(
                    ["curate", "--", combined],
                    timeout=_CURATE_TIMEOUT, cwd=self._cwd,
                )
            except Exception as e:
                logger.debug("ByteRover sync failed: %s", e)

        # 等待之前的同步完成
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="brv-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """将内置记忆写入同步到 ByteRover。"""
        if action not in ("add", "replace") or not content:
            return

        def _write():
            try:
                label = "User profile" if target == "user" else "Agent memory"
                _run_brv(
                    ["curate", "--", f"[{label}] {content}"],
                    timeout=_CURATE_TIMEOUT, cwd=self._cwd,
                )
            except Exception as e:
                logger.debug("ByteRover memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="brv-memwrite")
        t.start()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """在上下文压缩丢弃轮次之前提取洞察。"""
        if not messages:
            return ""

        # 构建即将被压缩的消息摘要
        parts = []
        for msg in messages[-10:]:  # last 10 messages
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
                parts.append(f"{role}: {content[:500]}")

        if not parts:
            return ""

        combined = "\n".join(parts)

        def _flush():
            try:
                _run_brv(
                    ["curate", "--", f"[Pre-compression context]\n{combined}"],
                    timeout=_CURATE_TIMEOUT, cwd=self._cwd,
                )
                logger.info("ByteRover pre-compression flush: %d messages", len(parts))
            except Exception as e:
                logger.debug("ByteRover pre-compression flush failed: %s", e)

        t = threading.Thread(target=_flush, daemon=True, name="brv-flush")
        t.start()
        return ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [QUERY_SCHEMA, CURATE_SCHEMA, STATUS_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if tool_name == "brv_query":
            return self._tool_query(args)
        elif tool_name == "brv_curate":
            return self._tool_curate(args)
        elif tool_name == "brv_status":
            return self._tool_status()
        return tool_error(f"Unknown tool: {tool_name}")

    def shutdown(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

    # -- Tool implementations ------------------------------------------------

    def _tool_query(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")

        result = _run_brv(
            ["query", "--", query.strip()[:5000]],
            timeout=_QUERY_TIMEOUT, cwd=self._cwd,
        )

        if not result["success"]:
            return tool_error(result.get("error", "Query failed"))

        output = result.get("output", "").strip()
        if not output or len(output) < _MIN_OUTPUT_LEN:
            return json.dumps({"result": "No relevant memories found."})

        # 截断非常长的结果
        if len(output) > 8000:
            output = output[:8000] + "\n\n[... truncated]"

        return json.dumps({"result": output})

    def _tool_curate(self, args: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")

        result = _run_brv(
            ["curate", "--", content],
            timeout=_CURATE_TIMEOUT, cwd=self._cwd,
        )

        if not result["success"]:
            return tool_error(result.get("error", "Curate failed"))

        return json.dumps({"result": "Memory curated successfully."})

    def _tool_status(self) -> str:
        result = _run_brv(["status"], timeout=15, cwd=self._cwd)
        if not result["success"]:
            return tool_error(result.get("error", "Status check failed"))
        return json.dumps({"status": result.get("output", "")})


# ---------------------------------------------------------------------------
# 插件入口点
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """将 ByteRover 注册为记忆提供者插件。"""
    ctx.register_memory_provider(ByteRoverMemoryProvider())
