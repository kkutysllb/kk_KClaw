"""
OpenAI 兼容的 API 服务器平台适配器。

提供带有以下端点的 HTTP 服务器：
- POST /v1/chat/completions        — OpenAI Chat Completions 格式（无状态；通过 X-KClaw-Session-Id 头可选启用会话连续性）
- POST /v1/responses               — OpenAI Responses API 格式（通过 previous_response_id 有状态）
- GET  /v1/responses/{response_id} — 检索已存储的响应
- DELETE /v1/responses/{response_id} — 删除已存储的响应
- GET  /v1/models                  — 将 kclaw 列为可用模型
- POST /v1/runs                    — 启动运行，立即返回 run_id (202)
- GET  /v1/runs/{run_id}/events    — 结构化生命周期事件的 SSE 流
- GET  /health                     — 健康检查

任何 OpenAI 兼容的前端（Open WebUI、LobeChat、LibreChat、
AnythingLLM、NextChat、ChatBox 等）都可以通过将此适配器指向
http://localhost:8642/v1 来连接 kclaw。

依赖：
- aiohttp（已在 gateway 中可用）
"""

import asyncio
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
)

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
MAX_STORED_RESPONSES = 100
MAX_REQUEST_BYTES = 1_000_000  # 1 MB default limit for POST bodies


def check_api_server_requirements() -> bool:
    """检查 API 服务器依赖是否可用。"""
    return AIOHTTP_AVAILABLE


class ResponseStore:
    """
    SQLite 支持的 LRU 存储，用于 Responses API 状态。

    每个存储的响应都包含完整的内部对话历史
    （包含工具调用和结果），以便可以通过后续的
    previous_response_id 请求进行重建。

    跨网关重启持久化。如果磁盘路径不可用，
    则回退到内存 SQLite。
    """

    def __init__(self, max_size: int = MAX_STORED_RESPONSES, db_path: str = None):
        self._max_size = max_size
        if db_path is None:
            try:
                from kclaw_cli.config import get_kclaw_home
                db_path = str(get_kclaw_home() / "response_store.db")
            except Exception:
                db_path = ":memory:"
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                accessed_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                name TEXT PRIMARY KEY,
                response_id TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def get(self, response_id: str) -> Optional[Dict[str, Any]]:
        """通过 ID 检索存储的响应（更新访问时间用于 LRU）。"""
        row = self._conn.execute(
            "SELECT data FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        import time
        self._conn.execute(
            "UPDATE responses SET accessed_at = ? WHERE response_id = ?",
            (time.time(), response_id),
        )
        self._conn.commit()
        return json.loads(row[0])

    def put(self, response_id: str, data: Dict[str, Any]) -> None:
        """存储响应，达到容量时驱逐最旧的。"""
        import time
        self._conn.execute(
            "INSERT OR REPLACE INTO responses (response_id, data, accessed_at) VALUES (?, ?, ?)",
            (response_id, json.dumps(data, default=str), time.time()),
        )
        # Evict oldest entries beyond max_size
        count = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        if count > self._max_size:
            self._conn.execute(
                "DELETE FROM responses WHERE response_id IN "
                "(SELECT response_id FROM responses ORDER BY accessed_at ASC LIMIT ?)",
                (count - self._max_size,),
            )
        self._conn.commit()

    def delete(self, response_id: str) -> bool:
        """从存储中移除响应。找到并删除时返回 True。"""
        cursor = self._conn.execute(
            "DELETE FROM responses WHERE response_id = ?", (response_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_conversation(self, name: str) -> Optional[str]:
        """获取会话名称的最新 response_id。"""
        row = self._conn.execute(
            "SELECT response_id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def set_conversation(self, name: str, response_id: str) -> None:
        """将会话名称映射到其最新的 response_id。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO conversations (name, response_id) VALUES (?, ?)",
            (name, response_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        try:
            self._conn.close()
        except Exception:
            pass

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# CORS 中间件
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def cors_middleware(request, handler):
        """为明确允许的来源添加 CORS 头；处理 OPTIONS 预检请求。"""
        adapter = request.app.get("api_server_adapter")
        origin = request.headers.get("Origin", "")
        cors_headers = None
        if adapter is not None:
            if not adapter._origin_allowed(origin):
                return web.Response(status=403)
            cors_headers = adapter._cors_headers_for_origin(origin)

        if request.method == "OPTIONS":
            if cors_headers is None:
                return web.Response(status=403)
            return web.Response(status=200, headers=cors_headers)

        response = await handler(request)
        if cors_headers is not None:
            response.headers.update(cors_headers)
        return response
else:
    cors_middleware = None  # type: ignore[assignment]


def _openai_error(message: str, err_type: str = "invalid_request_error", param: str = None, code: str = None) -> Dict[str, Any]:
    """OpenAI 风格的错误信封。"""
    return {
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": code,
        }
    }


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def body_limit_middleware(request, handler):
        """根据 Content-Length 尽早拒绝过大的请求体。"""
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > MAX_REQUEST_BYTES:
                        return web.json_response(_openai_error("Request body too large.", code="body_too_large"), status=413)
                except ValueError:
                    return web.json_response(_openai_error("Invalid Content-Length header.", code="invalid_content_length"), status=400)
        return await handler(request)
else:
    body_limit_middleware = None  # type: ignore[assignment]

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def security_headers_middleware(request, handler):
        """向所有响应（包括错误）添加安全头。"""
        response = await handler(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response
else:
    security_headers_middleware = None  # type: ignore[assignment]


class _IdempotencyCache:
    """带 TTL 和基本 LRU 语义的内存幂等性缓存。"""
    def __init__(self, max_items: int = 1000, ttl_seconds: int = 300):
        from collections import OrderedDict
        self._store = OrderedDict()
        self._ttl = ttl_seconds
        self._max = max_items

    def _purge(self):
        import time as _t
        now = _t.time()
        expired = [k for k, v in self._store.items() if now - v["ts"] > self._ttl]
        for k in expired:
            self._store.pop(k, None)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    async def get_or_set(self, key: str, fingerprint: str, compute_coro):
        self._purge()
        item = self._store.get(key)
        if item and item["fp"] == fingerprint:
            return item["resp"]
        resp = await compute_coro()
        import time as _t
        self._store[key] = {"resp": resp, "fp": fingerprint, "ts": _t.time()}
        self._purge()
        return resp


_idem_cache = _IdempotencyCache()


def _make_request_fingerprint(body: Dict[str, Any], keys: List[str]) -> str:
    from hashlib import sha256
    subset = {k: body.get(k) for k in keys}
    return sha256(repr(subset).encode("utf-8")).hexdigest()


class APIServerAdapter(BasePlatformAdapter):
    """
    OpenAI 兼容的 HTTP API 服务器适配器。

    运行一个 aiohttp Web 服务器，接受 OpenAI 格式的请求，
    并通过 kclaw 的 AIAgent 路由它们。
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.API_SERVER)
        extra = config.extra or {}
        self._host: str = extra.get("host", os.getenv("API_SERVER_HOST", DEFAULT_HOST))
        self._port: int = int(extra.get("port", os.getenv("API_SERVER_PORT", str(DEFAULT_PORT))))
        self._api_key: str = extra.get("key", os.getenv("API_SERVER_KEY", ""))
        self._cors_origins: tuple[str, ...] = self._parse_cors_origins(
            extra.get("cors_origins", os.getenv("API_SERVER_CORS_ORIGINS", "")),
        )
        self._app: Optional["web.Application"] = None
        self._runner: Optional["web.AppRunner"] = None
        self._site: Optional["web.TCPSite"] = None
        self._response_store = ResponseStore()
        # Active run streams: run_id -> asyncio.Queue of SSE event dicts
        self._run_streams: Dict[str, "asyncio.Queue[Optional[Dict]]"] = {}
        # Creation timestamps for orphaned-run TTL sweep
        self._run_streams_created: Dict[str, float] = {}
        self._session_db: Optional[Any] = None  # Lazy-init SessionDB for session continuity

    @staticmethod
    def _parse_cors_origins(value: Any) -> tuple[str, ...]:
        """将配置的 CORS 来源规范化为稳定的元组。"""
        if not value:
            return ()

        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [str(value)]

        return tuple(str(item).strip() for item in items if str(item).strip())

    def _cors_headers_for_origin(self, origin: str) -> Optional[Dict[str, str]]:
        """返回允许的浏览器来源的 CORS 头。"""
        if not origin or not self._cors_origins:
            return None

        if "*" in self._cors_origins:
            headers = dict(_CORS_HEADERS)
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Max-Age"] = "600"
            return headers

        if origin not in self._cors_origins:
            return None

        headers = dict(_CORS_HEADERS)
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
        headers["Access-Control-Max-Age"] = "600"
        return headers

    def _origin_allowed(self, origin: str) -> bool:
        """允许非浏览器客户端和明确配置的浏览器来源。"""
        if not origin:
            return True

        if not self._cors_origins:
            return False

        return "*" in self._cors_origins or origin in self._cors_origins

    # ------------------------------------------------------------------
    # 认证辅助
    # ------------------------------------------------------------------

    def _check_auth(self, request: "web.Request") -> Optional["web.Response"]:
        """
        验证 Authorization 头中的 Bearer 令牌。

        认证成功返回 None，失败时返回 401 web.Response。
        如果未配置 API 密钥，则允许所有请求。
        """
        if not self._api_key:
            return None  # 未配置密钥 — 允许所有（仅本地使用）

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if hmac.compare_digest(token, self._api_key):
                return None  # 认证成功

        return web.json_response(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )

    # ------------------------------------------------------------------
    # 会话数据库辅助
    # ------------------------------------------------------------------

    def _ensure_session_db(self):
        """惰性初始化并返回共享的 SessionDB 实例。

        会话持久化到 ``state.db``，以便 ``kclaw sessions list``
        显示 API 服务器对话以及 CLI 和网关的对话。
        """
        if self._session_db is None:
            try:
                from kclaw_state import SessionDB
                self._session_db = SessionDB()
            except Exception as e:
                logger.debug("API 服务器的 SessionDB 不可用: %s", e)
        return self._session_db

    # ------------------------------------------------------------------
    # 代理创建辅助
    # ------------------------------------------------------------------

    def _create_agent(
        self,
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
    ) -> Any:
        """
        使用网关的运行时配置创建 AIAgent 实例。

        使用 _resolve_runtime_agent_kwargs() 从 config.yaml / 环境变量中选择
        model、api_key、base_url 等。从 config.yaml 的
        platform_toolsets.api_server 解析工具集（与所有其他
        网关平台相同），回退到 kclaw-api-server 默认值。
        """
        from run_agent import AIAgent
        from gateway.run import _resolve_runtime_agent_kwargs, _resolve_gateway_model, _load_gateway_config
        from kclaw_cli.tools_config import _get_platform_tools

        runtime_kwargs = _resolve_runtime_agent_kwargs()
        model = _resolve_gateway_model()

        user_config = _load_gateway_config()
        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))

        max_iterations = int(os.getenv("KCLAW_MAX_ITERATIONS", "90"))

        # 加载回退提供商链，使 API 服务器平台具有
        # 与 Telegram/Discord/Slack 相同的回退行为（修复 #4954）。
        from gateway.run import GatewayRunner
        fallback_model = GatewayRunner._load_fallback_model()

        agent = AIAgent(
            model=model,
            **runtime_kwargs,
            max_iterations=max_iterations,
            quiet_mode=True,
            verbose_logging=False,
            ephemeral_system_prompt=ephemeral_system_prompt or None,
            enabled_toolsets=enabled_toolsets,
            session_id=session_id,
            platform="api_server",
            stream_delta_callback=stream_delta_callback,
            tool_progress_callback=tool_progress_callback,
            session_db=self._ensure_session_db(),
            fallback_model=fallback_model,
        )
        return agent

    # ------------------------------------------------------------------
    # HTTP 处理器
    # ------------------------------------------------------------------

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health — 简单健康检查。"""
        return web.json_response({"status": "ok", "platform": "kclaw"})

    async def _handle_models(self, request: "web.Request") -> "web.Response":
        """GET /v1/models — 将 kclaw 列为可用模型。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        return web.json_response({
            "object": "list",
            "data": [
                {
                    "id": "kclaw",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "kclaw",
                    "permission": [],
                    "root": "kclaw",
                    "parent": None,
                }
            ],
        })

    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
        """POST /v1/chat/completions — OpenAI Chat Completions 格式。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # 解析请求体
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(_openai_error("请求体中的 JSON 无效"), status=400)

        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return web.json_response(
                {"error": {"message": "缺少或无效的 'messages' 字段", "type": "invalid_request_error"}},
                status=400,
            )

        stream = body.get("stream", False)

        # 提取系统消息（成为叠加在核心之上的临时系统提示词）
        system_prompt = None
        conversation_messages: List[Dict[str, str]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                # 累积系统消息
                if system_prompt is None:
                    system_prompt = content
                else:
                    system_prompt = system_prompt + "\n" + content
            elif role in ("user", "assistant"):
                conversation_messages.append({"role": role, "content": content})

        # 提取最后一条用户消息作为主要输入
        user_message = ""
        history = []
        if conversation_messages:
            user_message = conversation_messages[-1].get("content", "")
            history = conversation_messages[:-1]

        if not user_message:
            return web.json_response(
                {"error": {"message": "消息中未找到用户消息", "type": "invalid_request_error"}},
                status=400,
            )

        # 允许调用者通过传递 X-KClaw-Session-Id 继续现有会话。
        # 提供时，从 state.db 加载历史而非请求体。
        provided_session_id = request.headers.get("X-KClaw-Session-Id", "").strip()
        if provided_session_id:
            session_id = provided_session_id
            try:
                db = self._ensure_session_db()
                if db is not None:
                    history = db.get_messages_as_conversation(session_id)
            except Exception as e:
                logger.warning("加载会话历史失败 %s: %s", session_id, e)
                history = []
        else:
            session_id = str(uuid.uuid4())
            # 历史已在上面从请求体设置

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        model_name = body.get("model", "kclaw")
        created = int(time.time())

        if stream:
            import queue as _q
            _stream_q: _q.Queue = _q.Queue()

            def _on_delta(delta):
                # 过滤 None — 代理发送 stream_delta_callback(None)
                # 来通知 CLI 显示在工具执行前关闭响应框，
                # 但 SSE 写入器使用 None 作为流结束标记。
                # 转发它会过早关闭 HTTP 响应，
                # 导致 Open WebUI（及类似前端）错过
                # 工具调用后的最终答案。
                # SSE 循环通过 agent_task.done() 检测完成。
                if delta is not None:
                    _stream_q.put(delta)

            def _on_tool_progress(event_type, name, preview, args, **kwargs):
                """为 Open WebUI 将工具进度注入 SSE 流。"""
                if event_type != "tool.started":
                    return  # 仅在聊天流中显示工具开始事件
                if name.startswith("_"):
                    return  # 跳过内部事件 (_thinking)
                from agent.display import get_tool_emoji
                emoji = get_tool_emoji(name)
                label = preview or name
                _stream_q.put(f"\n`{emoji} {label}`\n")

            # 在后台启动代理。agent_ref 是一个可变容器，
            # 以便 SSE 写入器可以在客户端断开时中断代理。
            agent_ref = [None]
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_progress_callback=_on_tool_progress,
                agent_ref=agent_ref,
            ))

            return await self._write_sse_chat_completion(
                request, completion_id, model_name, created, _stream_q,
                agent_task, agent_ref, session_id=session_id,
            )

        # 非流式：运行代理（可选带 Idempotency-Key）
        async def _compute_completion():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(body, keys=["model", "messages", "tools", "tool_choice", "stream"])
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_completion)
            except Exception as e:
                logger.error("运行聊天补全代理时出错: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"内部服务器错误: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_completion()
            except Exception as e:
                logger.error("运行聊天补全代理时出错: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"内部服务器错误: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response", "")
        if not final_response:
            final_response = result.get("error", "(未生成响应)")

        response_data = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_response,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

        return web.json_response(response_data, headers={"X-KClaw-Session-Id": session_id})

    async def _write_sse_chat_completion(
        self, request: "web.Request", completion_id: str, model: str,
        created: int, stream_q, agent_task, agent_ref=None, session_id: str = None,
    ) -> "web.StreamResponse":
        """从代理的 stream_delta_callback 队列写入真实流式 SSE。

        如果客户端在流中间断开连接（网络中断、浏览器标签页关闭），
        代理通过 ``agent.interrupt()`` 被中断，使其在下一个循环迭代时
        停止进行 LLM API 调用，并取消 asyncio 任务包装器。
        """
        import queue as _q

        sse_headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        # CORS 中间件无法在 prepare() 刷新头后向 StreamResponse 注入头，
        # 因此提前解析 CORS 头。
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-KClaw-Session-Id"] = session_id
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        try:
            # 角色块
            role_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await response.write(f"data: {json.dumps(role_chunk)}\n\n".encode())

            # 当代理的内容块从队列到达时流式传输
            loop = asyncio.get_event_loop()
            while True:
                try:
                    delta = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        # 排空任何剩余项
                        while True:
                            try:
                                delta = stream_q.get_nowait()
                                if delta is None:
                                    break
                                content_chunk = {
                                    "id": completion_id, "object": "chat.completion.chunk",
                                    "created": created, "model": model,
                                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                                }
                                await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())
                            except _q.Empty:
                                break
                        break
                    continue

                if delta is None:  # 流结束标记
                    break

                content_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())

            # 从已完成的代理获取 usage
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            try:
                result, agent_usage = await agent_task
                usage = agent_usage or usage
            except Exception:
                pass

            # 结束块
            finish_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
            await response.write(f"data: {json.dumps(finish_chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            # 客户端在流中间断开连接。
            # 中断代理使其在下一个循环迭代时停止 LLM API 调用，
            # 然后取消 asyncio 任务包装器。
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE client disconnected")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("SSE 客户端断开连接；中断代理任务 %s", completion_id)

        return response

    async def _handle_responses(self, request: "web.Request") -> "web.Response":
        """POST /v1/responses — OpenAI Responses API 格式。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # 解析请求体
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": {"message": "请求体中的 JSON 无效", "type": "invalid_request_error"}},
                status=400,
            )

        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("缺少 'input' 字段"), status=400)

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")
        conversation = body.get("conversation")
        store = body.get("store", True)

        # conversation 和 previous_response_id 是互斥的
        if conversation and previous_response_id:
            return web.json_response(_openai_error("不能同时使用 'conversation' 和 'previous_response_id'"), status=400)

        # 将会话名称解析为最新的 response_id
        if conversation:
            previous_response_id = self._response_store.get_conversation(conversation)
            # 如果会话尚不存在不报错 — 这是一个新会话

        # 规范化输入为消息列表
        input_messages: List[Dict[str, str]] = []
        if isinstance(raw_input, str):
            input_messages = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            for item in raw_input:
                if isinstance(item, str):
                    input_messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    role = item.get("role", "user")
                    content = item.get("content", "")
                    # 处理可能为内容部分列表的内容
                    if isinstance(content, list):
                        text_parts = []
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "input_text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, dict) and part.get("type") == "output_text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, str):
                                text_parts.append(part)
                        content = "\n".join(text_parts)
                    input_messages.append({"role": role, "content": content})
        else:
            return web.json_response(_openai_error("'input' 必须是字符串或数组"), status=400)

        # 接受请求体中的显式 conversation_history。
        # 这让无状态客户端提供自己的历史，而非
        # 依赖服务端通过 previous_response_id 的响应链。
        # 优先级：显式 conversation_history > previous_response_id。
        conversation_history: List[Dict[str, str]] = []
        raw_history = body.get("conversation_history")
        if raw_history:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' 必须是消息对象数组"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] 必须有 'role' 和 'content' 字段"),
                        status=400,
                    )
                conversation_history.append({"role": str(entry["role"]), "content": str(entry["content"])})
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        if not conversation_history and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored is None:
                return web.json_response(_openai_error(f"未找到上一个响应: {previous_response_id}"), status=404)
            conversation_history = list(stored.get("conversation_history", []))
            # 如果未提供指令，从上一个响应继承
            if instructions is None:
                instructions = stored.get("instructions")

        # 将新输入消息追加到历史（除最后一条外的所有消息成为历史）
        for msg in input_messages[:-1]:
            conversation_history.append(msg)

        # 最后一条输入消息是 user_message
        user_message = input_messages[-1].get("content", "") if input_messages else ""
        if not user_message:
            return web.json_response(_openai_error("输入中未找到用户消息"), status=400)

        # 截断支持
        if body.get("truncation") == "auto" and len(conversation_history) > 100:
            conversation_history = conversation_history[-100:]

        # 运行代理（支持 Idempotency-Key）
        session_id = str(uuid.uuid4())

        async def _compute_response():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(
                body,
                keys=["input", "instructions", "previous_response_id", "conversation", "model", "tools"],
            )
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_response)
            except Exception as e:
                logger.error("运行响应代理时出错: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"内部服务器错误: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_response()
            except Exception as e:
                logger.error("运行响应代理时出错: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"内部服务器错误: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response", "")
        if not final_response:
            final_response = result.get("error", "(未生成响应)")

        response_id = f"resp_{uuid.uuid4().hex[:28]}"
        created_at = int(time.time())

        # 构建完整的对话历史以供存储
        # （包含代理运行中的工具调用）
        full_history = list(conversation_history)
        full_history.append({"role": "user", "content": user_message})
        # 如果可用，添加代理的内部消息
        agent_messages = result.get("messages", [])
        if agent_messages:
            full_history.extend(agent_messages)
        else:
            full_history.append({"role": "assistant", "content": final_response})

        # 构建输出项（包含工具调用 + 最终消息）
        output_items = self._extract_output_items(result)

        response_data = {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "created_at": created_at,
            "model": body.get("model", "kclaw"),
            "output": output_items,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

        # 存储完整的响应对象以供后续链接 / GET 检索
        if store:
            self._response_store.put(response_id, {
                "response": response_data,
                "conversation_history": full_history,
                "instructions": instructions,
            })
            # 更新会话映射，以便下次使用相同会话名称的请求
            # 自动链接到此响应
            if conversation:
                self._response_store.set_conversation(conversation, response_id)

        return web.json_response(response_data)

    # ------------------------------------------------------------------
    # GET / DELETE 响应端点
    # ------------------------------------------------------------------

    async def _handle_get_response(self, request: "web.Request") -> "web.Response":
        """GET /v1/responses/{response_id} — 检索已存储的响应。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        stored = self._response_store.get(response_id)
        if stored is None:
            return web.json_response(_openai_error(f"未找到响应: {response_id}"), status=404)

        return web.json_response(stored["response"])

    async def _handle_delete_response(self, request: "web.Request") -> "web.Response":
        """DELETE /v1/responses/{response_id} — 删除已存储的响应。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        deleted = self._response_store.delete(response_id)
        if not deleted:
            return web.json_response(_openai_error(f"未找到响应: {response_id}"), status=404)

        return web.json_response({
            "id": response_id,
            "object": "response",
            "deleted": True,
        })

    # ------------------------------------------------------------------
    # Cron 作业 API
    # ------------------------------------------------------------------

    # 一次性检查 cron 模块可用性（非每请求）
    _CRON_AVAILABLE = False
    try:
        from cron.jobs import (
            list_jobs as _cron_list,
            get_job as _cron_get,
            create_job as _cron_create,
            update_job as _cron_update,
            remove_job as _cron_remove,
            pause_job as _cron_pause,
            resume_job as _cron_resume,
            trigger_job as _cron_trigger,
        )
        # 包装为 staticmethod 以防止描述符绑定 — 这些是普通的
        # 模块函数，不是实例方法。没有这个，self._cron_*()
        # 会将 ``self`` 作为第一个位置参数注入，每次调用
        # 都会引发 TypeError。
        _cron_list = staticmethod(_cron_list)
        _cron_get = staticmethod(_cron_get)
        _cron_create = staticmethod(_cron_create)
        _cron_update = staticmethod(_cron_update)
        _cron_remove = staticmethod(_cron_remove)
        _cron_pause = staticmethod(_cron_pause)
        _cron_resume = staticmethod(_cron_resume)
        _cron_trigger = staticmethod(_cron_trigger)
        _CRON_AVAILABLE = True
    except ImportError:
        pass

    _JOB_ID_RE = __import__("re").compile(r"[a-f0-9]{12}")
    # 更新的允许字段 — 防止客户端注入任意键
    _UPDATE_ALLOWED_FIELDS = {"name", "schedule", "prompt", "deliver", "skills", "skill", "repeat", "enabled"}
    _MAX_NAME_LENGTH = 200
    _MAX_PROMPT_LENGTH = 5000

    def _check_jobs_available(self) -> Optional["web.Response"]:
        """如果 cron 模块不可用则返回错误响应。"""
        if not self._CRON_AVAILABLE:
            return web.json_response(
                {"error": "Cron 模块不可用"}, status=501,
            )
        return None

    def _check_job_id(self, request: "web.Request") -> tuple:
        """验证并提取 job_id。返回 (job_id, error_response)。"""
        job_id = request.match_info["job_id"]
        if not self._JOB_ID_RE.fullmatch(job_id):
            return job_id, web.json_response(
                {"error": "无效的作业 ID 格式"}, status=400,
            )
        return job_id, None

    async def _handle_list_jobs(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs — 列出所有 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            include_disabled = request.query.get("include_disabled", "").lower() in ("true", "1")
            jobs = self._cron_list(include_disabled=include_disabled)
            return web.json_response({"jobs": jobs})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_create_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs — 创建新的 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            body = await request.json()
            name = (body.get("name") or "").strip()
            schedule = (body.get("schedule") or "").strip()
            prompt = body.get("prompt", "")
            deliver = body.get("deliver", "local")
            skills = body.get("skills")
            repeat = body.get("repeat")

            if not name:
                return web.json_response({"error": "名称是必需的"}, status=400)
            if len(name) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"名称必须 ≤ {self._MAX_NAME_LENGTH} 个字符"}, status=400,
                )
            if not schedule:
                return web.json_response({"error": "计划是必需的"}, status=400)
            if len(prompt) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"提示词必须 ≤ {self._MAX_PROMPT_LENGTH} 个字符"}, status=400,
                )
            if repeat is not None and (not isinstance(repeat, int) or repeat < 1):
                return web.json_response({"error": "repeat 必须是正整数"}, status=400)

            kwargs = {
                "prompt": prompt,
                "schedule": schedule,
                "name": name,
                "deliver": deliver,
            }
            if skills:
                kwargs["skills"] = skills
            if repeat is not None:
                kwargs["repeat"] = repeat

            job = self._cron_create(**kwargs)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_get_job(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs/{job_id} — 获取单个 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = self._cron_get(job_id)
            if not job:
                return web.json_response({"error": "作业未找到"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_update_job(self, request: "web.Request") -> "web.Response":
        """PATCH /api/jobs/{job_id} — 更新 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            body = await request.json()
            # 白名单允许的字段以防止任意键注入
            sanitized = {k: v for k, v in body.items() if k in self._UPDATE_ALLOWED_FIELDS}
            if not sanitized:
                return web.json_response({"error": "没有有效的字段可更新"}, status=400)
            # 如果存在则验证长度
            if "name" in sanitized and len(sanitized["name"]) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"名称必须 ≤ {self._MAX_NAME_LENGTH} 个字符"}, status=400,
                )
            if "prompt" in sanitized and len(sanitized["prompt"]) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"提示词必须 ≤ {self._MAX_PROMPT_LENGTH} 个字符"}, status=400,
                )
            job = self._cron_update(job_id, sanitized)
            if not job:
                return web.json_response({"error": "作业未找到"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_delete_job(self, request: "web.Request") -> "web.Response":
        """DELETE /api/jobs/{job_id} — 删除 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            success = self._cron_remove(job_id)
            if not success:
                return web.json_response({"error": "作业未找到"}, status=404)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_pause_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/pause — 暂停 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = self._cron_pause(job_id)
            if not job:
                return web.json_response({"error": "作业未找到"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_resume_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/resume — 恢复已暂停的 cron 作业。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = self._cron_resume(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_run_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/run — 触发立即执行。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = self._cron_trigger(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ------------------------------------------------------------------
    # 输出提取辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_output_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从代理的消息构建完整的输出项数组。

        遍历 *result["messages"]* 并发出：
        - 每个工具调用助手指手消息的 ``function_call`` 项
        - 每个工具角色消息的 ``function_call_output`` 项
        - 带有代理文本回复的最终 ``message`` 项
        """
        items: List[Dict[str, Any]] = []
        messages = result.get("messages", [])

        for msg in messages:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    items.append({
                        "type": "function_call",
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                        "call_id": tc.get("id", ""),
                    })
            elif role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content", ""),
                })

        # Final assistant message
        final = result.get("final_response", "")
        if not final:
            final = result.get("error", "(No response generated)")

        items.append({
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": final,
                }
            ],
        })
        return items

    # ------------------------------------------------------------------
    # 代理执行
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        agent_ref: Optional[list] = None,
    ) -> tuple:
        """
        在线程执行器中创建代理并运行对话。

        返回 ``(result_dict, usage_dict)``，其中 *usage_dict* 包含
        ``input_tokens``、``output_tokens`` 和 ``total_tokens``。

        如果 *agent_ref* 是一个单元素列表，AIAgent 实例存储在
        ``agent_ref[0]``，在 ``run_conversation`` 开始之前。
        这允许调用者（如 SSE 写入器）从另一个线程调用
        ``agent.interrupt()`` 来停止进行中的 LLM 调用。
        """
        loop = asyncio.get_event_loop()

        def _run():
            agent = self._create_agent(
                ephemeral_system_prompt=ephemeral_system_prompt,
                session_id=session_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
            )
            if agent_ref is not None:
                agent_ref[0] = agent
            result = agent.run_conversation(
                user_message=user_message,
                conversation_history=conversation_history,
            )
            usage = {
                "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
            }
            return result, usage

        return await loop.run_in_executor(None, _run)

    # ------------------------------------------------------------------
    # /v1/runs — structured event streaming
    # ------------------------------------------------------------------

    _MAX_CONCURRENT_RUNS = 10  # 防止无限制的资源分配
    _RUN_STREAM_TTL = 300  # 孤立运行被清理前的秒数

    def _make_run_event_callback(self, run_id: str, loop: "asyncio.AbstractEventLoop"):
        """返回一个工具进度回调，将结构化事件推送到运行的 SSE 队列。"""
        def _push(event: Dict[str, Any]) -> None:
            q = self._run_streams.get(run_id)
            if q is None:
                return
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

        def _callback(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs):
            ts = time.time()
            if event_type == "tool.started":
                _push({
                    "event": "tool.started",
                    "run_id": run_id,
                    "timestamp": ts,
                    "tool": tool_name,
                    "preview": preview,
                })
            elif event_type == "tool.completed":
                _push({
                    "event": "tool.completed",
                    "run_id": run_id,
                    "timestamp": ts,
                    "tool": tool_name,
                    "duration": round(kwargs.get("duration", 0), 3),
                    "error": kwargs.get("is_error", False),
                })
            elif event_type == "reasoning.available":
                _push({
                    "event": "reasoning.available",
                    "run_id": run_id,
                    "timestamp": ts,
                    "text": preview or "",
                })
            # _thinking 和 subagent_progress 故意不转发

        return _callback

    async def _handle_runs(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs — 启动代理运行，立即返回 run_id。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # 强制并发限制
        if len(self._run_streams) >= self._MAX_CONCURRENT_RUNS:
            return web.json_response(
                _openai_error(f"并发运行过多 (最大 {self._MAX_CONCURRENT_RUNS})", code="rate_limit_exceeded"),
                status=429,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(_openai_error("无效的 JSON"), status=400)

        raw_input = body.get("input")
        if not raw_input:
            return web.json_response(_openai_error("缺少 'input' 字段"), status=400)

        user_message = raw_input if isinstance(raw_input, str) else (raw_input[-1].get("content", "") if isinstance(raw_input, list) else "")
        if not user_message:
            return web.json_response(_openai_error("输入中未找到用户消息"), status=400)

        run_id = f"run_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        q: "asyncio.Queue[Optional[Dict]]" = asyncio.Queue()
        self._run_streams[run_id] = q
        self._run_streams_created[run_id] = time.time()

        event_cb = self._make_run_event_callback(run_id, loop)

        # Also wire stream_delta_callback so message.delta events flow through
        def _text_cb(delta: Optional[str]) -> None:
            if delta is None:
                return
            try:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event": "message.delta",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "delta": delta,
                })
            except Exception:
                pass

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")

        # 接受请求体中的显式 conversation_history。
        # 优先级：显式 conversation_history > previous_response_id。
        conversation_history: List[Dict[str, str]] = []
        raw_history = body.get("conversation_history")
        if raw_history:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' 必须是消息对象数组"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] 必须有 'role' 和 'content' 字段"),
                        status=400,
                    )
                conversation_history.append({"role": str(entry["role"]), "content": str(entry["content"])})
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        if not conversation_history and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored:
                conversation_history = list(stored.get("conversation_history", []))
                if instructions is None:
                    instructions = stored.get("instructions")

        # 当 input 是多消息数组时，提取除最后一条外的所有消息
        # 作为对话历史（最后一条成为 user_message）。
        # 仅在未提供显式历史时触发。
        if not conversation_history and isinstance(raw_input, list) and len(raw_input) > 1:
            for msg in raw_input[:-1]:
                if isinstance(msg, dict) and msg.get("role") and msg.get("content"):
                    content = msg["content"]
                    if isinstance(content, list):
                        # 将多部分内容块展平为文本
                        content = " ".join(
                            part.get("text", "") for part in content
                            if isinstance(part, dict) and part.get("type") == "text"
                        )
                    conversation_history.append({"role": msg["role"], "content": str(content)})

        session_id = body.get("session_id") or run_id
        ephemeral_system_prompt = instructions

        async def _run_and_close():
            try:
                agent = self._create_agent(
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    session_id=session_id,
                    stream_delta_callback=_text_cb,
                    tool_progress_callback=event_cb,
                )
                def _run_sync():
                    r = agent.run_conversation(
                        user_message=user_message,
                        conversation_history=conversation_history,
                    )
                    u = {
                        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                        "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                    }
                    return r, u

                result, usage = await asyncio.get_running_loop().run_in_executor(None, _run_sync)
                final_response = result.get("final_response", "") if isinstance(result, dict) else ""
                q.put_nowait({
                    "event": "run.completed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "output": final_response,
                    "usage": usage,
                })
            except Exception as exc:
                logger.exception("[api_server] 运行 %s 失败", run_id)
                try:
                    q.put_nowait({
                        "event": "run.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "error": str(exc),
                    })
                except Exception:
                    pass
            finally:
                # 标记：信号 SSE 流关闭
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

        task = asyncio.create_task(_run_and_close())
        try:
            self._background_tasks.add(task)
        except TypeError:
            pass
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)

        return web.json_response({"run_id": run_id, "status": "started"}, status=202)

    async def _handle_run_events(self, request: "web.Request") -> "web.StreamResponse":
        """GET /v1/runs/{run_id}/events — 结构化代理生命周期事件的 SSE 流。"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]

        # 允许在运行注册前稍微提前订阅（竞争条件窗口）
        for _ in range(20):
            if run_id in self._run_streams:
                break
            await asyncio.sleep(0.05)
        else:
            return web.json_response(_openai_error(f"未找到运行: {run_id}", code="run_not_found"), status=404)

        q = self._run_streams[run_id]

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                if event is None:
                    # 运行结束 — 发送最终 SSE 注释并关闭
                    await response.write(b": stream closed\n\n")
                    break
                payload = f"data: {json.dumps(event)}\n\n"
                await response.write(payload.encode())
        except Exception as exc:
            logger.debug("[api_server] 运行 %s 的 SSE 流错误: %s", run_id, exc)
        finally:
            self._run_streams.pop(run_id, None)
            self._run_streams_created.pop(run_id, None)

        return response

    async def _sweep_orphaned_runs(self) -> None:
        """定期清理从未被消费的运行流。"""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            stale = [
                run_id
                for run_id, created_at in list(self._run_streams_created.items())
                if now - created_at > self._RUN_STREAM_TTL
            ]
            for run_id in stale:
                logger.debug("[api_server] 清理孤立运行 %s", run_id)
                self._run_streams.pop(run_id, None)
                self._run_streams_created.pop(run_id, None)

    # ------------------------------------------------------------------
    # BasePlatformAdapter 接口
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """启动 aiohttp Web 服务器。"""
        if not AIOHTTP_AVAILABLE:
            logger.warning("[%s] aiohttp 未安装", self.name)
            return False

        try:
            mws = [mw for mw in (cors_middleware, body_limit_middleware, security_headers_middleware) if mw is not None]
            self._app = web.Application(middlewares=mws)
            self._app["api_server_adapter"] = self
            self._app.router.add_get("/health", self._handle_health)
            self._app.router.add_get("/v1/health", self._handle_health)
            self._app.router.add_get("/v1/models", self._handle_models)
            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
            self._app.router.add_post("/v1/responses", self._handle_responses)
            self._app.router.add_get("/v1/responses/{response_id}", self._handle_get_response)
            self._app.router.add_delete("/v1/responses/{response_id}", self._handle_delete_response)
            # Cron jobs management API
            self._app.router.add_get("/api/jobs", self._handle_list_jobs)
            self._app.router.add_post("/api/jobs", self._handle_create_job)
            self._app.router.add_get("/api/jobs/{job_id}", self._handle_get_job)
            self._app.router.add_patch("/api/jobs/{job_id}", self._handle_update_job)
            self._app.router.add_delete("/api/jobs/{job_id}", self._handle_delete_job)
            self._app.router.add_post("/api/jobs/{job_id}/pause", self._handle_pause_job)
            self._app.router.add_post("/api/jobs/{job_id}/resume", self._handle_resume_job)
            self._app.router.add_post("/api/jobs/{job_id}/run", self._handle_run_job)
            # Structured event streaming
            self._app.router.add_post("/v1/runs", self._handle_runs)
            self._app.router.add_get("/v1/runs/{run_id}/events", self._handle_run_events)
            # 启动后台清理以清理孤立的（未消费的）运行流
            sweep_task = asyncio.create_task(self._sweep_orphaned_runs())
            try:
                self._background_tasks.add(sweep_task)
            except TypeError:
                pass
            if hasattr(sweep_task, "add_done_callback"):
                sweep_task.add_done_callback(self._background_tasks.discard)

            # 端口冲突检测 — 如果端口已被使用则快速失败
            import socket as _socket
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                    _s.settimeout(1)
                    _s.connect(('127.0.0.1', self._port))
                logger.error('[%s] 端口 %d 已被占用。请在 config.yaml 中设置不同的端口: platforms.api_server.port', self.name, self._port)
                return False
            except (ConnectionRefusedError, OSError):
                pass  # 端口空闲

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()

            self._mark_connected()
            logger.info(
                "[%s] API 服务器监听于 http://%s:%d",
                self.name, self._host, self._port,
            )
            return True

        except Exception as e:
            logger.error("[%s] 启动 API 服务器失败: %s", self.name, e)
            return False

    async def disconnect(self) -> None:
        """停止 aiohttp Web 服务器。"""
        self._mark_disconnected()
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("[%s] API 服务器已停止", self.name)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        未使用 — HTTP 请求/响应周期直接处理投递。
        """
        return SendResult(success=False, error="API 服务器使用 HTTP 请求/响应，而非 send()")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """返回 API 服务器的基本信息。"""
        return {
            "name": "API 服务器",
            "type": "api",
            "host": self._host,
            "port": self._port,
        }
