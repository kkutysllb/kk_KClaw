"""
定时任务调度器 - 执行到期任务。

提供 tick() 函数检查到期任务并运行它们。网关每 60 秒从后台线程调用一次。

使用基于文件的锁（~/.kclaw/cron/.tick.lock），这样如果多个进程重叠，
只有一个 tick 能运行。
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import subprocess
import sys

# fcntl 是 Unix 专用的；在 Windows 上使用 msvcrt 进行文件锁定
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import Optional

# 在 repo 级别导入之前，将父目录添加到路径。
# 如果没有这个，独立调用（例如在 `kclaw update` 重新加载模块后）
# 会因为找不到 kclaw_time 等模块而报 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).parent.parent))

from kclaw_constants import get_kclaw_home
from kclaw_cli.config import load_config
from kclaw_time import now as _kclaw_now

logger = logging.getLogger(__name__)

# 有效的投递平台 — 用于验证用户提供的 cron 投递目标中的平台名称，
# 防止通过精心设计的名称枚举环境变量。
_KNOWN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "signal",
    "matrix", "mattermost", "homeassistant", "dingtalk", "feishu",
    "wecom", "sms", "email", "webhook", "bluebubbles",
})

from cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_run

# 标记：当 cron agent 没有新内容报告时，它可以在响应开头
# 添加此标记以禁止投递。输出仍会保存在本地供审计。
SILENT_MARKER = "[SILENT]"

# 解析 KClaw 主目录（尊重 KCLAW_HOME 覆盖）
_kclaw_home = get_kclaw_home()

# 基于文件的锁防止 gateway + daemon + systemd timer 并发 tick
_LOCK_DIR = _kclaw_home / "cron"
_LOCK_FILE = _LOCK_DIR / ".tick.lock"


def _resolve_origin(job: dict) -> Optional[dict]:
    """从任务中提取来源信息，保留任何额外的路由元数据。"""
    origin = job.get("origin")
    if not origin:
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _resolve_delivery_target(job: dict) -> Optional[dict]:
    """解析 cron 任务的具体自动投递目标（如果有）。"""
    deliver = job.get("deliver", "local")
    origin = _resolve_origin(job)

    if deliver == "local":
        return None

    if deliver == "origin":
        if origin:
            return {
                "platform": origin["platform"],
                "chat_id": str(origin["chat_id"]),
                "thread_id": origin.get("thread_id"),
            }
        # 来源缺失（例如任务通过 API/脚本创建）— 尝试每个
        # 平台的主频道作为后备，而不是静默丢弃。
        for platform_name in ("matrix", "telegram", "discord", "slack", "bluebubbles"):
            chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
            if chat_id:
                logger.info(
                    "任务 '%s' 设置了 deliver=origin 但无来源；回退到 %s 主频道",
                    job.get("name", job.get("id", "?")),
                    platform_name,
                )
                return {
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": None,
                }
        return None

    if ":" in deliver:
        platform_name, rest = deliver.split(":", 1)
        platform_key = platform_name.lower()

        from tools.send_message_tool import _parse_target_ref

        parsed_chat_id, parsed_thread_id, is_explicit = _parse_target_ref(platform_key, rest)
        if is_explicit:
            chat_id, thread_id = parsed_chat_id, parsed_thread_id
        else:
            chat_id, thread_id = rest, None

        # 解析类似 "Alice (dm)" 的友好标签为真实 ID。
        try:
            from gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_key, chat_id)
            if resolved:
                parsed_chat_id, parsed_thread_id, resolved_is_explicit = _parse_target_ref(platform_key, resolved)
                if resolved_is_explicit:
                    chat_id, thread_id = parsed_chat_id, parsed_thread_id
                else:
                    chat_id = resolved
        except Exception:
            pass

        return {
            "platform": platform_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    platform_name = deliver
    if origin and origin.get("platform") == platform_name:
        return {
            "platform": platform_name,
            "chat_id": str(origin["chat_id"]),
            "thread_id": origin.get("thread_id"),
        }

    if platform_name.lower() not in _KNOWN_DELIVERY_PLATFORMS:
        return None
    chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
    if not chat_id:
        return None

    return {
        "platform": platform_name,
        "chat_id": chat_id,
        "thread_id": None,
    }


# 媒体扩展名集合 — 与 gateway/platforms/base.py:_process_message_background 保持同步
_AUDIO_EXTS = frozenset({'.ogg', '.opus', '.mp3', '.wav', '.m4a'})
_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _send_media_via_adapter(adapter, chat_id: str, media_files: list, metadata: dict | None, loop, job: dict) -> None:
    """通过实时适配器将提取的媒体文件作为原生平台附件发送。

    根据文件扩展名将每个文件路由到适当的适配器方法（send_voice、send_image_file、
    send_video、send_document）— 镜像 ``BasePlatformAdapter._process_message_background``
    中的路由逻辑。
    """
    from pathlib import Path

    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            if ext in _AUDIO_EXTS:
                coro = adapter.send_voice(chat_id=chat_id, audio_path=media_path, metadata=metadata)
            elif ext in _VIDEO_EXTS:
                coro = adapter.send_video(chat_id=chat_id, video_path=media_path, metadata=metadata)
            elif ext in _IMAGE_EXTS:
                coro = adapter.send_image_file(chat_id=chat_id, image_path=media_path, metadata=metadata)
            else:
                coro = adapter.send_document(chat_id=chat_id, file_path=media_path, metadata=metadata)

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            result = future.result(timeout=30)
            if result and not getattr(result, "success", True):
                logger.warning(
                    "任务 '%s'：媒体发送失败 %s: %s",
                    job.get("id", "?"), media_path, getattr(result, "error", "unknown"),
                )
        except Exception as e:
            logger.warning("任务 '%s'：发送媒体 %s 失败: %s", job.get("id", "?"), media_path, e)


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    """
    将任务输出投递到配置的目标（原聊天、特定平台等）。

    当提供 ``adapters`` 和 ``loop`` 时（gateway 正在运行），优先使用实时适配器 —
    这支持需要加密的端到端加密房间（例如 Matrix），因为独立 HTTP 路径无法加密。
    如果适配器路径失败或不可用，则回退到独立发送。

    成功返回 None，失败返回错误字符串。
    """
    target = _resolve_delivery_target(job)
    if not target:
        if job.get("deliver", "local") != "local":
            msg = f"无法解析投递目标 deliver={job.get('deliver', 'local')}"
            logger.warning("任务 '%s'：%s", job["id"], msg)
            return msg
        return None  # 仅本地任务不投递 — 不是失败

    platform_name = target["platform"]
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    from tools.send_message_tool import _send_to_platform
    from gateway.config import load_gateway_config, Platform

    platform_map = {
        "telegram": Platform.TELEGRAM,
        "discord": Platform.DISCORD,
        "slack": Platform.SLACK,
        "whatsapp": Platform.WHATSAPP,
        "signal": Platform.SIGNAL,
        "matrix": Platform.MATRIX,
        "mattermost": Platform.MATTERMOST,
        "homeassistant": Platform.HOMEASSISTANT,
        "dingtalk": Platform.DINGTALK,
        "feishu": Platform.FEISHU,
        "wecom": Platform.WECOM,
        "email": Platform.EMAIL,
        "sms": Platform.SMS,
        "bluebubbles": Platform.BLUEBUBBLES,
    }
    platform = platform_map.get(platform_name.lower())
    if not platform:
        msg = f"未知平台 '{platform_name}'"
        logger.warning("任务 '%s'：%s", job["id"], msg)
        return msg

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"加载网关配置失败: {e}"
        logger.error("任务 '%s'：%s", job["id"], msg)
        return msg

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        msg = f"平台 '{platform_name}' 未配置/未启用"
        logger.warning("任务 '%s'：%s", job["id"], msg)
        return msg

    # 可选择用页眉/页脚包装内容，让用户知道这是 cron 投递。
    # 默认启用包装；将 config.yaml 中的 cron.wrap_response: false 设置为简洁输出。
    wrap_response = True
    try:
        user_cfg = load_config()
        wrap_response = user_cfg.get("cron", {}).get("wrap_response", True)
    except Exception:
        pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        delivery_content = (
            f"Cronjob Response: {task_name}\n"
            f"-------------\n\n"
            f"{content}\n\n"
            f"Note: The agent cannot see this message, and therefore cannot respond to it."
        )
    else:
        delivery_content = content

    # 提取 MEDIA: 标签，以便附件作为文件转发，而不是原始文本
    from gateway.platforms.base import BasePlatformAdapter
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)

    # 当 gateway 运行时优先使用实时适配器 — 这支持需要加密的端到端加密房间
    # （例如 Matrix），因为独立 HTTP 路径无法加密。
    runtime_adapter = (adapters or {}).get(platform)
    if runtime_adapter is not None and loop is not None and getattr(loop, "is_running", lambda: False)():
        send_metadata = {"thread_id": thread_id} if thread_id else None
        try:
            # Send cleaned text (MEDIA tags stripped) — not the raw content
            text_to_send = cleaned_delivery_content.strip()
            adapter_ok = True
            if text_to_send:
                future = asyncio.run_coroutine_threadsafe(
                    runtime_adapter.send(chat_id, text_to_send, metadata=send_metadata),
                    loop,
                )
                send_result = future.result(timeout=60)
                if send_result and not getattr(send_result, "success", True):
                    err = getattr(send_result, "error", "unknown")
                    logger.warning(
                        "任务 '%s'：实时适配器发送到 %s:%s 失败 (%s)，回退到独立模式",
                        job["id"], platform_name, chat_id, err,
                    )
                    adapter_ok = False  # 回退到独立路径

            # 通过实时适配器将提取的媒体文件作为原生附件发送
            if adapter_ok and media_files:
                _send_media_via_adapter(runtime_adapter, chat_id, media_files, send_metadata, loop, job)

            if adapter_ok:
                logger.info("任务 '%s'：通过实时适配器投递到 %s:%s", job["id"], platform_name, chat_id)
                return None
        except Exception as e:
            logger.warning(
                "任务 '%s'：实时适配器投递到 %s:%s 失败 (%s)，回退到独立模式",
                job["id"], platform_name, chat_id, e,
            )

    # 独立路径：在新的事件循环中运行异步发送（从任何线程都是安全的）
    coro = _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files)
    try:
        result = asyncio.run(coro)
    except RuntimeError:
        # asyncio.run() 在等待协程之前检查是否有正在运行的循环；
        # 当它抛出错误时，原来的协程从未启动 — 关闭它以防止
        # "coroutine was never awaited" RuntimeWarning，然后在没有运行循环的新线程中重试。
        coro.close()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files))
            result = future.result(timeout=30)
    except Exception as e:
        msg = f"投递到 {platform_name}:{chat_id} 失败: {e}"
        logger.error("任务 '%s'：%s", job["id"], msg)
        return msg

    if result and result.get("error"):
        msg = f"投递错误: {result['error']}"
        logger.error("任务 '%s'：%s", job["id"], msg)
        return msg

    logger.info("任务 '%s'：已投递到 %s:%s", job["id"], platform_name, chat_id)
    return None


_SCRIPT_TIMEOUT = 120  # 秒


def _run_job_script(script_path: str) -> tuple[bool, str]:
    """执行 cron 任务的数据收集脚本并捕获其输出。

    脚本必须位于 KCLAW_HOME/scripts/ 内。相对路径和绝对路径都会
    针对此目录进行解析和验证，以防止通过路径遍历或绝对路径注入
    执行任意脚本。

    参数:
        script_path: Python 脚本的路径。相对路径相对于 KCLAW_HOME/scripts/ 解析。
            绝对路径和 ~ 前缀的路径也会被验证以确保它们保持在 scripts 目录内。

    返回:
        (success, output) — 失败时 *output* 包含错误消息，以便
        LLM 向用户报告问题。
    """
    from kclaw_constants import get_kclaw_home

    scripts_dir = get_kclaw_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir_resolved = scripts_dir.resolve()

    raw = Path(script_path).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    else:
        path = (scripts_dir / raw).resolve()

    # 防止路径遍历、绝对路径注入和符号链接逃逸 —
    # 脚本必须位于 KCLAW_HOME/scripts/ 内。
    try:
        path.relative_to(scripts_dir_resolved)
    except ValueError:
        return False, (
            f"阻止：脚本路径解析到 scripts 目录外部 "
            f"({scripts_dir_resolved}): {script_path!r}"
        )

    if not path.exists():
        return False, f"找不到脚本: {path}"
    if not path.is_file():
        return False, f"脚本路径不是文件: {path}"

    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            cwd=str(path.parent),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            parts = [f"脚本以代码 {result.returncode} 退出"]
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if stdout:
                parts.append(f"stdout:\n{stdout}")
            return False, "\n".join(parts)

        # 在脚本输出注入到 LLM 提示上下文之前，
        # 清除可能出现的任何敏感信息。
        try:
            from agent.redact import redact_sensitive_text
            stdout = redact_sensitive_text(stdout)
        except Exception:
            pass
        return True, stdout

    except subprocess.TimeoutExpired:
        return False, f"脚本在 {_SCRIPT_TIMEOUT}s 后超时: {path}"
    except Exception as exc:
        return False, f"脚本执行失败: {exc}"


def _build_job_prompt(job: dict) -> str:
    """为 cron 任务构建有效提示词，可选择先加载一个或多个技能。"""
    prompt = job.get("prompt", "")
    skills = job.get("skills")

    # 如果配置了数据收集脚本，则运行它并将其输出作为上下文注入。
    script_path = job.get("script")
    if script_path:
        success, script_output = _run_job_script(script_path)
        if success:
            if script_output:
                prompt = (
                    "## 脚本输出\n"
                    "以下数据由预运行脚本收集。"
                    "将其作为分析的上下文使用。\n\n"
                    f"```\n{script_output}\n```\n\n"
                    f"{prompt}"
                )
            else:
                prompt = (
                    "[脚本运行成功但未产生输出。]\n\n"
                    f"{prompt}"
                )
        else:
            prompt = (
                "## 脚本错误\n"
                "数据收集脚本失败。请向用户报告。\n\n"
                f"```\n{script_output}\n```\n\n"
                f"{prompt}"
            )

    # 始终前置 cron 执行指导，以便 agent 了解投递机制并在适当时抑制投递。
    cron_hint = (
        "[SYSTEM: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be automatically delivered "
        "to the user — do NOT use send_message or try to deliver "
        "the output yourself. Just produce your report/output as your "
        "final response and the system handles the rest. "
        "SILENT: If there is genuinely nothing new to report, respond "
        "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
        "Never combine [SILENT] with content — either report your "
        "findings normally, or say [SILENT] and nothing more.]\n\n"
    )
    prompt = cron_hint + prompt
    if skills is None:
        legacy = job.get("skill")
        skills = [legacy] if legacy else []

    skill_names = [str(name).strip() for name in skills if str(name).strip()]
    if not skill_names:
        return prompt

    from tools.skills_tool import skill_view

    parts = []
    skipped: list[str] = []
    for skill_name in skill_names:
        loaded = json.loads(skill_view(skill_name))
        if not loaded.get("success"):
            error = loaded.get("error") or f"无法加载技能 '{skill_name}'"
            logger.warning("定时任务 '%s'：技能未找到，跳过 — %s", job.get("name", job.get("id")), error)
            skipped.append(skill_name)
            continue

        content = str(loaded.get("content") or "").strip()
        if parts:
            parts.append("")
        parts.extend(
            [
                f'[SYSTEM: The user has invoked the "{skill_name}" skill, indicating they want you to follow its instructions. The full skill content is loaded below.]',
                "",
                content,
            ]
        )

    if skipped:
        notice = (
            f"[SYSTEM: The following skill(s) were listed for this job but could not be found "
            f"and were skipped: {', '.join(skipped)}. "
            f"Start your response with a brief notice so the user is aware, e.g.: "
            f"'⚠️ Skill(s) not found and skipped: {', '.join(skipped)}']"
        )
        parts.insert(0, notice)

    if prompt:
        parts.extend(["", f"用户提供了以下指令以及技能调用: {prompt}"])
    return "\n".join(parts)


def run_job(job: dict) -> tuple[bool, str, str, Optional[str]]:
    """
    执行单个定时任务。
    
    返回:
        元组 (success, full_output_doc, final_response, error_message)
    """
    from run_agent import AIAgent
    
    # 初始化 SQLite 会话存储，以便 cron 任务消息被持久化
    # 并可通过 session_search 发现（与 gateway/run.py 相同的模式）。
    _session_db = None
    try:
        from kclaw_state import SessionDB
        _session_db = SessionDB()
    except Exception as e:
        logger.debug("任务 '%s'：SQLite 会话存储不可用: %s", job.get("id", "?"), e)
    
    job_id = job["id"]
    job_name = job["name"]
    prompt = _build_job_prompt(job)
    origin = _resolve_origin(job)
    _cron_session_id = f"cron_{job_id}_{_kclaw_now().strftime('%Y%m%d_%H%M%S')}"

    logger.info("正在运行任务 '%s' (ID: %s)", job_name, job_id)
    logger.info("提示词: %s", prompt[:100])

    try:
        # 注入来源上下文，以便 agent 的 send_message 工具知道聊天。
        # 必须在 try 块内部，以便 finally 清理始终运行。
        if origin:
            os.environ["KCLAW_SESSION_PLATFORM"] = origin["platform"]
            os.environ["KCLAW_SESSION_CHAT_ID"] = str(origin["chat_id"])
            if origin.get("chat_name"):
                os.environ["KCLAW_SESSION_CHAT_NAME"] = origin["chat_name"]
        # 每次运行都重新读取 .env 和 config.yaml，以便 provider/key
        # 更改无需重启 gateway 即可生效。
        from dotenv import load_dotenv
        try:
            load_dotenv(str(_kclaw_home / ".env"), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(_kclaw_home / ".env"), override=True, encoding="latin-1")

        delivery_target = _resolve_delivery_target(job)
        if delivery_target:
            os.environ["KCLAW_CRON_AUTO_DELIVER_PLATFORM"] = delivery_target["platform"]
            os.environ["KCLAW_CRON_AUTO_DELIVER_CHAT_ID"] = str(delivery_target["chat_id"])
            if delivery_target.get("thread_id") is not None:
                os.environ["KCLAW_CRON_AUTO_DELIVER_THREAD_ID"] = str(delivery_target["thread_id"])

        model = job.get("model") or os.getenv("KCLAW_MODEL") or ""

        # 从 config.yaml 加载模型、推理、预填充、工具集、provider 路由
        _cfg = {}
        try:
            import yaml
            _cfg_path = str(_kclaw_home / "config.yaml")
            if os.path.exists(_cfg_path):
                with open(_cfg_path) as _f:
                    _cfg = yaml.safe_load(_f) or {}
                _model_cfg = _cfg.get("model", {})
                if not job.get("model"):
                    if isinstance(_model_cfg, str):
                        model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        model = _model_cfg.get("default", model)
        except Exception as e:
            logger.warning("任务 '%s'：加载 config.yaml 失败，使用默认值: %s", job_id, e)

        # 从 config.yaml 读取推理配置
        from kclaw_constants import parse_reasoning_effort
        effort = str(_cfg.get("agent", {}).get("reasoning_effort", "")).strip()
        reasoning_config = parse_reasoning_effort(effort)

        # 从 env 或 config.yaml 读取预填充消息
        prefill_messages = None
        prefill_file = os.getenv("KCLAW_PREFILL_MESSAGES_FILE", "") or _cfg.get("prefill_messages_file", "")
        if prefill_file:
            import json as _json
            pfpath = Path(prefill_file).expanduser()
            if not pfpath.is_absolute():
                pfpath = _kclaw_home / pfpath
            if pfpath.exists():
                try:
                    with open(pfpath, "r", encoding="utf-8") as _pf:
                        prefill_messages = _json.load(_pf)
                    if not isinstance(prefill_messages, list):
                        prefill_messages = None
                except Exception as e:
                    logger.warning("任务 '%s'：解析预填充消息文件 '%s' 失败: %s", job_id, pfpath, e)
                    prefill_messages = None

        # 最大迭代次数
        max_iterations = _cfg.get("agent", {}).get("max_turns") or _cfg.get("max_turns") or 90

        # Provider 路由
        pr = _cfg.get("provider_routing", {})
        smart_routing = _cfg.get("smart_model_routing", {}) or {}

        from kclaw_cli.runtime_provider import (
            resolve_runtime_provider,
            format_runtime_provider_error,
        )
        try:
            runtime_kwargs = {
                "requested": job.get("provider") or os.getenv("KCLAW_INFERENCE_PROVIDER"),
            }
            if job.get("base_url"):
                runtime_kwargs["explicit_base_url"] = job.get("base_url")
            runtime = resolve_runtime_provider(**runtime_kwargs)
        except Exception as exc:
            message = format_runtime_provider_error(exc)
            raise RuntimeError(message) from exc

        from agent.smart_model_routing import resolve_turn_route
        turn_route = resolve_turn_route(
            prompt,
            smart_routing,
            {
                "model": model,
                "api_key": runtime.get("api_key"),
                "base_url": runtime.get("base_url"),
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "command": runtime.get("command"),
                "args": list(runtime.get("args") or []),
            },
        )

        agent = AIAgent(
            model=turn_route["model"],
            api_key=turn_route["runtime"].get("api_key"),
            base_url=turn_route["runtime"].get("base_url"),
            provider=turn_route["runtime"].get("provider"),
            api_mode=turn_route["runtime"].get("api_mode"),
            acp_command=turn_route["runtime"].get("command"),
            acp_args=turn_route["runtime"].get("args"),
            max_iterations=max_iterations,
            reasoning_config=reasoning_config,
            prefill_messages=prefill_messages,
            providers_allowed=pr.get("only"),
            providers_ignored=pr.get("ignore"),
            providers_order=pr.get("order"),
            provider_sort=pr.get("sort"),
            disabled_toolsets=["cronjob", "messaging", "clarify"],
            quiet_mode=True,
            skip_memory=True,  # Cron 系统提示词会破坏用户表示
            platform="cron",
            session_id=_cron_session_id,
            session_db=_session_db,
        )
        
        # 使用基于 *无活动* 的超时运行 agent：任务可以运行数小时
        #（如果它在积极调用工具/接收流式 token），
        # 但在配置的时间内无活动的挂起 API 调用或卡住的工具会被捕获并终止。
        # 默认 600s（10 分钟无活动）；通过 KCLAW_CRON_TIMEOUT 环境变量覆盖。0 = 无限制。
        #
        # 使用 agent 内置的活动跟踪器（通过 _touch_activity() 在每次工具调用、
        # API 调用和流式增量时更新）。
        _cron_timeout = float(os.getenv("KCLAW_CRON_TIMEOUT", 600))
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        _POLL_INTERVAL = 5.0
        _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _cron_future = _cron_pool.submit(agent.run_conversation, prompt)
        _inactivity_timeout = False
        try:
            if _cron_inactivity_limit is None:
                # 无限制 — 只需等待结果。
                result = _cron_future.result()
            else:
                result = None
                while True:
                    done, _ = concurrent.futures.wait(
                        {_cron_future}, timeout=_POLL_INTERVAL,
                    )
                    if done:
                        result = _cron_future.result()
                        break
                    # Agent 仍在运行 — 检查无活动状态。
                    _idle_secs = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            _act = agent.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    if _idle_secs >= _cron_inactivity_limit:
                        _inactivity_timeout = True
                        break
        except Exception:
            _cron_pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            _cron_pool.shutdown(wait=False)

        if _inactivity_timeout:
            # 从 agent 的活动跟踪器构建诊断摘要。
            _activity = {}
            if hasattr(agent, "get_activity_summary"):
                try:
                    _activity = agent.get_activity_summary()
                except Exception:
                    pass
            _last_desc = _activity.get("last_activity_desc", "unknown")
            _secs_ago = _activity.get("seconds_since_activity", 0)
            _cur_tool = _activity.get("current_tool")
            _iter_n = _activity.get("api_call_count", 0)
            _iter_max = _activity.get("max_iterations", 0)

            logger.error(
                "任务 '%s' 空闲 %.0fs（无活动限制 %.0fs）"
                "| last_activity=%s | iteration=%s/%s | tool=%s",
                job_name, _secs_ago, _cron_inactivity_limit,
                _last_desc, _iter_n, _iter_max,
                _cur_tool or "none",
            )
            if hasattr(agent, "interrupt"):
                agent.interrupt("定时任务超时（无活动）")
            raise TimeoutError(
                f"定时任务 '{job_name}' 空闲 "
                f"{int(_secs_ago)}s（限制 {int(_cron_inactivity_limit)}s）"
                f"— 最后活动: {_last_desc}"
            )

        final_response = result.get("final_response", "") or ""
        # 使用单独的变量用于日志显示；保持 final_response 干净以供投递逻辑使用
        #（空响应 = 不投递）。
        logged_response = final_response if final_response else "（未生成响应）"
        
        output = f"""# 定时任务: {job_name}

**任务 ID:** {job_id}
**运行时间:** {_kclaw_now().strftime('%Y-%m-%d %H:%M:%S')}
**调度:** {job.get('schedule_display', 'N/A')}

## 提示词

{prompt}

## 响应

{logged_response}
"""
        
        logger.info("任务 '%s' 成功完成", job_name)
        return True, output, final_response, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("任务 '%s' 失败: %s", job_name, error_msg)
        
        output = f"""# 定时任务: {job_name} (失败)

**任务 ID:** {job_id}
**运行时间:** {_kclaw_now().strftime('%Y-%m-%d %H:%M:%S')}
**调度:** {job.get('schedule_display', 'N/A')}

## 提示词

{prompt}

## 错误

```
{error_msg}
```
"""
        return False, output, "", error_msg

    finally:
        # 清理注入的环境变量，以免泄露到其他任务
        for key in (
            "KCLAW_SESSION_PLATFORM",
            "KCLAW_SESSION_CHAT_ID",
            "KCLAW_SESSION_CHAT_NAME",
            "KCLAW_CRON_AUTO_DELIVER_PLATFORM",
            "KCLAW_CRON_AUTO_DELIVER_CHAT_ID",
            "KCLAW_CRON_AUTO_DELIVER_THREAD_ID",
        ):
            os.environ.pop(key, None)
        if _session_db:
            try:
                _session_db.end_session(_cron_session_id, "cron_complete")
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("任务 '%s'：结束会话失败: %s", job_id, e)
            try:
                _session_db.close()
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("任务 '%s'：关闭 SQLite 会话存储失败: %s", job_id, e)


def tick(verbose: bool = True, adapters=None, loop=None) -> int:
    """
    检查并运行所有到期任务。
    
    使用文件锁，以便一次只有一个 tick 运行，即使 gateway 的
    进程内 ticker 与独立守护进程或手动 tick 重叠。
    
    参数:
        verbose: 是否打印状态消息
        adapters: 可选的 Platform → 实时适配器的字典（来自 gateway）
        loop: 可选的 asyncio 事件循环（来自 gateway），用于实时适配器发送
    
    返回:
        执行的任务数量（如果另一个 tick 已在运行则返回 0）
    """
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)

    # 跨平台文件锁定：Unix 用 fcntl，Windows 用 msvcrt
    lock_fd = None
    try:
        lock_fd = open(_LOCK_FILE, "w")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        logger.debug("跳过 tick — 另一个实例持有锁")
        if lock_fd is not None:
            lock_fd.close()
        return 0

    try:
        due_jobs = get_due_jobs()

        if verbose and not due_jobs:
            logger.info("%s - 无到期任务", _kclaw_now().strftime('%H:%M:%S'))
            return 0

        if verbose:
            logger.info("%s - %s 个任务到期", _kclaw_now().strftime('%H:%M:%S'), len(due_jobs))

        executed = 0
        for job in due_jobs:
            try:
                # 对于重复任务（cron/interval），在执行前将 next_run_at 推进到
                # 下一次出现。这样，如果进程在运行期间崩溃，
                # 任务不会在重启时重新触发。
                # 一次性任务保持不变，以便在重启时仍能重试。
                advance_next_run(job["id"])

                success, output, final_response, error = run_job(job)

                output_file = save_job_output(job["id"], output)
                if verbose:
                    logger.info("输出已保存到: %s", output_file)

                # 将最终响应投递到来源/目标聊天。
                # 如果 agent 返回了 [SILENT]，则跳过投递（但输出已在上方保存）。
                # 失败的任务始终投递。
                deliver_content = final_response if success else f"⚠️ 定时任务 '{job.get('name', job['id'])}' 失败:\n{error}"
                should_deliver = bool(deliver_content)
                if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
                    logger.info("任务 '%s'：agent 返回了 %s — 跳过投递", job["id"], SILENT_MARKER)
                    should_deliver = False

                delivery_error = None
                if should_deliver:
                    try:
                        delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                    except Exception as de:
                        delivery_error = str(de)
                        logger.error("任务 %s 的投递失败: %s", job["id"], de)

                mark_job_run(job["id"], success, error, delivery_error=delivery_error)
                executed += 1

            except Exception as e:
                logger.error("处理任务 %s 时出错: %s", job['id'], e)
                mark_job_run(job["id"], False, str(e))

        return executed
    finally:
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        elif msvcrt:
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        lock_fd.close()


if __name__ == "__main__":
    tick(verbose=True)
