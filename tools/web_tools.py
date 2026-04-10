#!/usr/bin/env python3
"""
独立 Web 工具模块

本模块提供通用的 Web 工具，可与多个后端提供商配合使用。
后端在 ``kclaw tools`` 设置期间选择（config.yaml 中的 web.backend）。
如果可用，KClaw 可以通过 Nous 托管的工具网关为 Nous 订阅者路由 Firecrawl 调用。

可用工具：
- web_search_tool: 在网络上搜索信息
- web_extract_tool: 从特定网页提取内容
- web_crawl_tool: 使用特定指令抓取网站

后端兼容性：
- Exa: https://exa.ai (搜索, 提取)
- Firecrawl: https://docs.firecrawl.dev/introduction (搜索, 提取, 抓取；直接或派生的 firecrawl-gateway.<domain>，仅限 Nous 订阅者)
- Parallel: https://docs.parallel.ai (搜索, 提取)
- Tavily: https://tavily.com (搜索, 提取, 抓取)

LLM 处理：
- 使用 OpenRouter API 和 Gemini 3 Flash Preview 进行智能内容提取
- 提取关键摘录并创建 markdown 摘要以减少 token 使用

调试模式：
- 设置 WEB_TOOLS_DEBUG=true 以启用详细日志记录
- 在 ./logs 目录中创建 web_tools_debug_UUID.json
- 捕获所有工具调用、结果和压缩指标

用法：
    from web_tools import web_search_tool, web_extract_tool, web_crawl_tool

    # 在网络上搜索
    results = web_search_tool("Python 机器学习库", limit=3)

    # 从 URL 提取内容
    content = web_extract_tool(["https://example.com"], format="markdown")

    # 抓取网站
    crawl_data = web_crawl_tool("example.com", "查找联系信息")
"""

import json
import logging
import os
import re
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from firecrawl import Firecrawl
from agent.auxiliary_client import (
    async_call_llm,
    extract_content_or_reasoning,
    get_async_text_auxiliary_client,
)
from tools.debug_helpers import DebugSession
from tools.managed_tool_gateway import (
    build_vendor_gateway_url,
    read_nous_access_token as _read_nous_access_token,
    resolve_managed_tool_gateway,
)
from tools.tool_backend_helpers import managed_nous_tools_enabled
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)


# ─── 后端选择 ────────────────────────────────────────────────────────

def _has_env(name: str) -> bool:
    val = os.getenv(name)
    return bool(val and val.strip())

def _load_web_config() -> dict:
    """从 ~/.kclaw/config.yaml 加载 ``web:`` 部分。"""
    try:
        from kclaw_cli.config import load_config
        return load_config().get("web", {})
    except (ImportError, Exception):
        return {}

def _get_backend() -> str:
    """确定使用哪个 Web 后端。

    从 config.yaml 读取 ``web.backend``（由 ``kclaw tools`` 设置）。
    对于手动配置密钥但未运行设置的用户，回退到存在的任何 API 密钥。
    """
    configured = (_load_web_config().get("backend") or "").lower().strip()
    if configured in ("parallel", "firecrawl", "tavily", "exa"):
        return configured

    # 手动/传统配置的回退方案 — 选择最高优先级的可用后端。
    # 当为 Nous 订阅者配置了托管工具网关时，Firecrawl 也被视为可用。
    backend_candidates = (
        ("firecrawl", _has_env("FIRECRAWL_API_KEY") or _has_env("FIRECRAWL_API_URL") or _is_tool_gateway_ready()),
        ("parallel", _has_env("PARALLEL_API_KEY")),
        ("tavily", _has_env("TAVILY_API_KEY")),
        ("exa", _has_env("EXA_API_KEY")),
    )
    for backend, available in backend_candidates:
        if available:
            return backend

    return "firecrawl"  # default (backward compat)


def _is_backend_available(backend: str) -> bool:
    """当所选后端当前可用时返回 True。"""
    if backend == "exa":
        return _has_env("EXA_API_KEY")
    if backend == "parallel":
        return _has_env("PARALLEL_API_KEY")
    if backend == "firecrawl":
        return check_firecrawl_api_key()
    if backend == "tavily":
        return _has_env("TAVILY_API_KEY")
    return False

# ─── Firecrawl 客户端 ────────────────────────────────────────────────────────

_firecrawl_client = None
_firecrawl_client_config = None


def _get_direct_firecrawl_config() -> Optional[tuple[Dict[str, str], tuple[str, Optional[str], Optional[str]]]]:
    """返回显式直接 Firecrawl kwargs + 缓存键，或在未设置时返回 None。"""
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    api_url = os.getenv("FIRECRAWL_API_URL", "").strip().rstrip("/")

    if not api_key and not api_url:
        return None

    kwargs: Dict[str, str] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if api_url:
        kwargs["api_url"] = api_url

    return kwargs, ("direct", api_url or None, api_key or None)


def _get_firecrawl_gateway_url() -> str:
    """返回已配置的 Firecrawl 网关 URL。"""
    return build_vendor_gateway_url("firecrawl")


def _is_tool_gateway_ready() -> bool:
    """当网关 URL 和 Nous 订阅者令牌可用时返回 True。"""
    return resolve_managed_tool_gateway("firecrawl", token_reader=_read_nous_access_token) is not None


def _has_direct_firecrawl_config() -> bool:
    """当直接配置了 Firecrawl 时返回 True。"""
    return _get_direct_firecrawl_config() is not None


def _raise_web_backend_configuration_error() -> None:
    """为不支持的 Web 后端配置引发清晰的错误。"""
    message = (
        "Web tools are not configured. "
        "Set FIRECRAWL_API_KEY for cloud Firecrawl or set FIRECRAWL_API_URL for a self-hosted Firecrawl instance."
    )
    if managed_nous_tools_enabled():
        message += (
            " If you have the hidden Nous-managed tools flag enabled, you can also login to Nous "
            "(`kclaw model`) and provide FIRECRAWL_GATEWAY_URL or TOOL_GATEWAY_DOMAIN."
        )
    raise ValueError(message)


def _firecrawl_backend_help_suffix() -> str:
    """返回 Firecrawl 帮助文本的可选托管网关指导。"""
    if not managed_nous_tools_enabled():
        return ""
    return (
        ", or, if you have the hidden Nous-managed tools flag enabled, login to Nous and use "
        "FIRECRAWL_GATEWAY_URL or TOOL_GATEWAY_DOMAIN"
    )


def _web_requires_env() -> list[str]:
    """返回当前启用的 Web 后端的工具元数据环境变量。"""
    requires = [
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "TAVILY_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
    ]
    if managed_nous_tools_enabled():
        requires.extend(
            [
                "FIRECRAWL_GATEWAY_URL",
                "TOOL_GATEWAY_DOMAIN",
                "TOOL_GATEWAY_SCHEME",
                "TOOL_GATEWAY_USER_TOKEN",
            ]
        )
    return requires


def _get_firecrawl_client():
    """获取或创建 Firecrawl 客户端。

    显式配置时，Direct Firecrawl 优先。否则
    KClaw 为已登录的 Nous 订阅者回退到 Firecrawl 工具网关。
    """
    global _firecrawl_client, _firecrawl_client_config

    direct_config = _get_direct_firecrawl_config()
    if direct_config is not None:
        kwargs, client_config = direct_config
    else:
        managed_gateway = resolve_managed_tool_gateway(
            "firecrawl",
            token_reader=_read_nous_access_token,
        )
        if managed_gateway is None:
            logger.error("Firecrawl client initialization failed: missing direct config and tool-gateway auth.")
            _raise_web_backend_configuration_error()

        kwargs = {
            "api_key": managed_gateway.nous_user_token,
            "api_url": managed_gateway.gateway_origin,
        }
        client_config = (
            "tool-gateway",
            kwargs["api_url"],
            managed_gateway.nous_user_token,
        )

    if _firecrawl_client is not None and _firecrawl_client_config == client_config:
        return _firecrawl_client

    _firecrawl_client = Firecrawl(**kwargs)
    _firecrawl_client_config = client_config
    return _firecrawl_client

# ─── Parallel 客户端 ─────────────────────────────────────────────────────────

_parallel_client = None
_async_parallel_client = None

def _get_parallel_client():
    """获取或创建 Parallel 同步客户端（延迟初始化）。

    需要 PARALLEL_API_KEY 环境变量。
    """
    from parallel import Parallel
    global _parallel_client
    if _parallel_client is None:
        api_key = os.getenv("PARALLEL_API_KEY")
        if not api_key:
            raise ValueError(
                "PARALLEL_API_KEY environment variable not set. "
                "Get your API key at https://parallel.ai"
            )
        _parallel_client = Parallel(api_key=api_key)
    return _parallel_client


def _get_async_parallel_client():
    """获取或创建 Parallel 异步客户端（延迟初始化）。

    需要 PARALLEL_API_KEY 环境变量。
    """
    from parallel import AsyncParallel
    global _async_parallel_client
    if _async_parallel_client is None:
        api_key = os.getenv("PARALLEL_API_KEY")
        if not api_key:
            raise ValueError(
                "PARALLEL_API_KEY environment variable not set. "
                "Get your API key at https://parallel.ai"
            )
        _async_parallel_client = AsyncParallel(api_key=api_key)
    return _async_parallel_client

# ─── Tavily 客户端 ───────────────────────────────────────────────────────────

_TAVILY_BASE_URL = "https://api.tavily.com"


def _tavily_request(endpoint: str, payload: dict) -> dict:
    """向 Tavily API 发送 POST 请求。

    身份验证通过 JSON body 中的 ``api_key`` 提供（不是基于 header 的认证）。
    如果未设置 ``TAVILY_API_KEY`` 则引发 ``ValueError``。
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise ValueError(
            "TAVILY_API_KEY environment variable not set. "
            "Get your API key at https://app.tavily.com/home"
        )
    payload["api_key"] = api_key
    url = f"{_TAVILY_BASE_URL}/{endpoint.lstrip('/')}"
    logger.info("Tavily %s request to %s", endpoint, url)
    response = httpx.post(url, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def _normalize_tavily_search_results(response: dict) -> dict:
    """将 Tavily /search 响应规范化为标准 Web 搜索格式。

    Tavily 返回 ``{results: [{title, url, content, score, ...}]}``。
    我们映射到 ``{success, data: {web: [{title, url, description, position}]}}``。
    """
    web_results = []
    for i, result in enumerate(response.get("results", [])):
        web_results.append({
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "description": result.get("content", ""),
            "position": i + 1,
        })
    return {"success": True, "data": {"web": web_results}}


def _normalize_tavily_documents(response: dict, fallback_url: str = "") -> List[Dict[str, Any]]:
    """将 Tavily /extract 或 /crawl 响应规范化为标准文档格式。

    将结果映射到 ``{url, title, content, raw_content, metadata}`` 并
    includes any ``failed_results`` / ``failed_urls`` as error entries.
    """
    documents: List[Dict[str, Any]] = []
    for result in response.get("results", []):
        url = result.get("url", fallback_url)
        raw = result.get("raw_content", "") or result.get("content", "")
        documents.append({
            "url": url,
            "title": result.get("title", ""),
            "content": raw,
            "raw_content": raw,
            "metadata": {"sourceURL": url, "title": result.get("title", "")},
        })
    # 处理失败的结果
    for fail in response.get("failed_results", []):
        documents.append({
            "url": fail.get("url", fallback_url),
            "title": "",
            "content": "",
            "raw_content": "",
            "error": fail.get("error", "extraction failed"),
            "metadata": {"sourceURL": fail.get("url", fallback_url)},
        })
    for fail_url in response.get("failed_urls", []):
        url_str = fail_url if isinstance(fail_url, str) else str(fail_url)
        documents.append({
            "url": url_str,
            "title": "",
            "content": "",
            "raw_content": "",
            "error": "extraction failed",
            "metadata": {"sourceURL": url_str},
        })
    return documents


def _to_plain_object(value: Any) -> Any:
    """尽可能将 SDK 对象转换为纯 Python 数据结构。"""
    if value is None:
        return None

    if isinstance(value, (dict, list, str, int, float, bool)):
        return value

    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
        except Exception:
            pass

    return value


def _normalize_result_list(values: Any) -> List[Dict[str, Any]]:
    """将混合的 SDK/list 负载规范化为字典列表。"""
    if not isinstance(values, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in values:
        plain = _to_plain_object(item)
        if isinstance(plain, dict):
            normalized.append(plain)
    return normalized


def _extract_web_search_results(response: Any) -> List[Dict[str, Any]]:
    """跨 SDK/direct/gateway 响应形状提取 Firecrawl 搜索结果。"""
    response_plain = _to_plain_object(response)

    if isinstance(response_plain, dict):
        data = response_plain.get("data")
        if isinstance(data, list):
            return _normalize_result_list(data)

        if isinstance(data, dict):
            data_web = _normalize_result_list(data.get("web"))
            if data_web:
                return data_web
            data_results = _normalize_result_list(data.get("results"))
            if data_results:
                return data_results

        top_web = _normalize_result_list(response_plain.get("web"))
        if top_web:
            return top_web

        top_results = _normalize_result_list(response_plain.get("results"))
        if top_results:
            return top_results

    if hasattr(response, "web"):
        return _normalize_result_list(getattr(response, "web", []))

    return []


def _extract_scrape_payload(scrape_result: Any) -> Dict[str, Any]:
    """跨 SDK 和网关变体规范化 Firecrawl 抓取负载形状。"""
    result_plain = _to_plain_object(scrape_result)
    if not isinstance(result_plain, dict):
        return {}

    nested = result_plain.get("data")
    if isinstance(nested, dict):
        return nested

    return result_plain


DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION = 5000

def _is_nous_auxiliary_client(client: Any) -> bool:
    """当解析的辅助后端是 Nous Portal 时返回 True。"""
    from urllib.parse import urlparse

    base_url = str(getattr(client, "base_url", "") or "")
    host = (urlparse(base_url).hostname or "").lower()
    return host == "nousresearch.com" or host.endswith(".nousresearch.com")


def _resolve_web_extract_auxiliary(model: Optional[str] = None) -> tuple[Optional[Any], Optional[str], Dict[str, Any]]:
    """解析当前的 web-extract 辅助客户端、模型和额外 body。"""
    client, default_model = get_async_text_auxiliary_client("web_extract")
    configured_model = os.getenv("AUXILIARY_WEB_EXTRACT_MODEL", "").strip()
    effective_model = model or configured_model or default_model

    extra_body: Dict[str, Any] = {}
    if client is not None and _is_nous_auxiliary_client(client):
        from agent.auxiliary_client import get_auxiliary_extra_body
        extra_body = get_auxiliary_extra_body() or {"tags": ["product=kclaw"]}

    return client, effective_model, extra_body


def _get_default_summarizer_model() -> Optional[str]:
    """返回当前 Web 提取摘要的默认模型。"""
    _, model, _ = _resolve_web_extract_auxiliary()
    return model

_debug = DebugSession("web_tools", env_var="WEB_TOOLS_DEBUG")


async def process_content_with_llm(
    content: str,
    url: str = "",
    title: str = "",
    model: Optional[str] = None,
    min_length: int = DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION
) -> Optional[str]:
    """
    使用 LLM 处理 Web 内容以创建带有关键摘录的智能摘要。

    此函数通过 OpenRouter API 使用 Gemini 3 Flash Preview（或指定模型）
    来智能提取关键信息并创建 markdown 摘要，
    在保留所有重要信息的同时显著减少 token 使用。

    对于非常大的内容（>500k 字符），使用分块处理和综合。
    对于极大的内容（>2M 字符），完全拒绝处理。

    参数:
        content (str): 要处理的原始内容
        url (str): 源 URL（用于上下文，可选）
        title (str): 页面标题（用于上下文，可选）
        model (str): 用于处理的模型（默认: google/gemini-3-flash-preview）
        min_length (int): 触发处理的最小内容长度（默认: 5000）

    返回:
        Optional[str]: 处理的 markdown 内容，如果内容太短或处理失败则返回 None
    """
    # 大小阈值
    MAX_CONTENT_SIZE = 2_000_000  # 2M 字符 - 完全拒绝超过此限制
    CHUNK_THRESHOLD = 500_000     # 500k 字符 - 使用分块处理超过此限制
    CHUNK_SIZE = 100_000          # 每块 100k 字符
    MAX_OUTPUT_SIZE = 5000        # 最终输出硬上限
    
    try:
        content_len = len(content)
        
        # 如果内容过大则拒绝
        if content_len > MAX_CONTENT_SIZE:
            size_mb = content_len / 1_000_000
            logger.warning("Content too large (%.1fMB > 2MB limit). Refusing to process.", size_mb)
            return f"[Content too large to process: {size_mb:.1f}MB. Try using web_crawl with specific extraction instructions, or search for a more focused source.]"
        
        # 如果内容太短则跳过处理
        if content_len < min_length:
            logger.debug("Content too short (%d < %d chars), skipping LLM processing", content_len, min_length)
            return None
        
        # 创建上下文信息
        context_info = []
        if title:
            context_info.append(f"Title: {title}")
        if url:
            context_info.append(f"Source: {url}")
        context_str = "\n".join(context_info) + "\n\n" if context_info else ""
        
        # 检查是否需要分块处理
        if content_len > CHUNK_THRESHOLD:
            logger.info("Content large (%d chars). Using chunked processing...", content_len)
            return await _process_large_content_chunked(
                content, context_str, model, CHUNK_SIZE, MAX_OUTPUT_SIZE
            )
        
        # 标准单遍处理普通内容
        logger.info("Processing content with LLM (%d characters)", content_len)
        
        processed_content = await _call_summarizer_llm(content, context_str, model)
        
        if processed_content:
            # 强制执行输出上限
            if len(processed_content) > MAX_OUTPUT_SIZE:
                processed_content = processed_content[:MAX_OUTPUT_SIZE] + "\n\n[... summary truncated for context management ...]"
            
            # 记录压缩指标
            processed_length = len(processed_content)
            compression_ratio = processed_length / content_len if content_len > 0 else 1.0
            logger.info("Content processed: %d -> %d chars (%.1f%%)", content_len, processed_length, compression_ratio * 100)
        
        return processed_content
        
    except Exception as e:
        logger.warning(
            "web_extract LLM summarization failed (%s). "
            "Tip: increase auxiliary.web_extract.timeout in config.yaml "
            "or switch to a faster auxiliary model.",
            str(e)[:120],
        )
        # 回退到截断的原始内容，而不是返回无用的错误消息。
        # 前 ~5000 个字符几乎总是比 "[Failed to process content: ...]" 对模型更有用。
        truncated = content[:MAX_OUTPUT_SIZE]
        if len(content) > MAX_OUTPUT_SIZE:
            truncated += (
                f"\n\n[Content truncated — showing first {MAX_OUTPUT_SIZE:,} of "
                f"{len(content):,} chars. LLM summarization timed out. "
                f"To fix: increase auxiliary.web_extract.timeout in config.yaml, "
                f"or use a faster auxiliary model. Use browser_navigate for the full page.]"
            )
        return truncated


async def _call_summarizer_llm(
    content: str,
    context_str: str,
    model: Optional[str],
    max_tokens: int = 20000,
    is_chunk: bool = False,
    chunk_info: str = ""
) -> Optional[str]:
    """
    进行单次 LLM 调用以摘要内容。

    参数:
        content: 要摘要的内容
        context_str: 上下文信息（标题、URL）
        model: 使用的模型
        max_tokens: 最大输出 token
        is_chunk: 这是否是一个较大文档的块
        chunk_info: 关于块位置的信息（例如 "Chunk 2/5"）

    返回:
        摘要内容或失败时返回 None
    """
    if is_chunk:
        # 块特定提示 - 知道这是部分内容
        system_prompt = """You are an expert content analyst processing a SECTION of a larger document. Your job is to extract and summarize the key information from THIS SECTION ONLY.

Important guidelines for chunk processing:
1. Do NOT write introductions or conclusions - this is a partial document
2. Focus on extracting ALL key facts, figures, data points, and insights from this section
3. Preserve important quotes, code snippets, and specific details verbatim
4. Use bullet points and structured formatting for easy synthesis later
5. Note any references to other sections (e.g., "as mentioned earlier", "see below") without trying to resolve them

Your output will be combined with summaries of other sections, so focus on thorough extraction rather than narrative flow."""

        user_prompt = f"""Extract key information from this SECTION of a larger document:

{context_str}{chunk_info}

SECTION CONTENT:
{content}

Extract all important information from this section in a structured format. Focus on facts, data, insights, and key details. Do not add introductions or conclusions."""

    else:
        # 标准完整文档提示
        system_prompt = """You are an expert content analyst. Your job is to process web content and create a comprehensive yet concise summary that preserves all important information while dramatically reducing bulk.

Create a well-structured markdown summary that includes:
1. Key excerpts (quotes, code snippets, important facts) in their original format
2. Comprehensive summary of all other important information
3. Proper markdown formatting with headers, bullets, and emphasis

Your goal is to preserve ALL important information while reducing length. Never lose key facts, figures, insights, or actionable information. Make it scannable and well-organized."""

        user_prompt = f"""Please process this web content and create a comprehensive markdown summary:

{context_str}CONTENT TO PROCESS:
{content}

Create a markdown summary that captures all key information in a well-organized, scannable format. Include important quotes and code snippets in their original formatting. Focus on actionable information, specific details, and unique insights."""

    # 使用重试逻辑调用 LLM — 由于摘要是可选项，保持重试次数较低；
    # 调用方在失败时会回退到截断的内容。
    max_retries = 2
    retry_delay = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            aux_client, effective_model, extra_body = _resolve_web_extract_auxiliary(model)
            if aux_client is None or not effective_model:
                logger.warning("No auxiliary model available for web content processing")
                return None
            call_kwargs = {
                "task": "web_extract",
                "model": effective_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
                # 没有明确的超时 — async_call_llm 从 config 读取 auxiliary.web_extract.timeout
                # （默认 360s / 6min）。使用慢速本地模型的用户可以
                # 在 config.yaml 中增加它。
            }
            if extra_body:
                call_kwargs["extra_body"] = extra_body
            response = await async_call_llm(**call_kwargs)
            content = extract_content_or_reasoning(response)
            if content:
                return content
            # 仅推理/空响应 — 让重试循环处理它
            logger.warning("LLM returned empty content (attempt %d/%d), retrying", attempt + 1, max_retries)
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue
            return content  # Return whatever we got after exhausting retries
        except RuntimeError:
            logger.warning("No auxiliary model available for web content processing")
            return None
        except Exception as api_error:
            last_error = api_error
            if attempt < max_retries - 1:
                logger.warning("LLM API call failed (attempt %d/%d): %s", attempt + 1, max_retries, str(api_error)[:100])
                logger.warning("Retrying in %ds...", retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                raise last_error
    
    return None


async def _process_large_content_chunked(
    content: str,
    context_str: str,
    model: Optional[str],
    chunk_size: int,
    max_output_size: int
) -> Optional[str]:
    """
    通过分块处理大内容，并行摘要每个块，然后综合摘要。

    参数:
        content: 要处理的大内容
        context_str: 上下文信息
        model: 使用的模型
        chunk_size: 每个块的字符大小
        max_output_size: 最大最终输出大小

    返回:
        综合摘要或失败时返回 None
    """
    # 将内容分割成块
    chunks = []
    for i in range(0, len(content), chunk_size):
        chunk = content[i:i + chunk_size]
        chunks.append(chunk)
    
    logger.info("Split into %d chunks of ~%d chars each", len(chunks), chunk_size)
    
    # 并行摘要每个块
    async def summarize_chunk(chunk_idx: int, chunk_content: str) -> tuple[int, Optional[str]]:
        """摘要单个块。"""
        try:
            chunk_info = f"[Processing chunk {chunk_idx + 1} of {len(chunks)}]"
            summary = await _call_summarizer_llm(
                chunk_content, 
                context_str, 
                model, 
                max_tokens=10000,
                is_chunk=True,
                chunk_info=chunk_info
            )
            if summary:
                logger.info("Chunk %d/%d summarized: %d -> %d chars", chunk_idx + 1, len(chunks), len(chunk_content), len(summary))
            return chunk_idx, summary
        except Exception as e:
            logger.warning("Chunk %d/%d failed: %s", chunk_idx + 1, len(chunks), str(e)[:50])
            return chunk_idx, None
    
    # 并行运行所有块摘要
    tasks = [summarize_chunk(i, chunk) for i, chunk in enumerate(chunks)]
    results = await asyncio.gather(*tasks)
    
    # 按顺序收集成功的摘要
    summaries = []
    for chunk_idx, summary in sorted(results, key=lambda x: x[0]):
        if summary:
            summaries.append(f"## Section {chunk_idx + 1}\n{summary}")
    
    if not summaries:
        logger.debug("All chunk summarizations failed")
        return "[Failed to process large content: all chunk summarizations failed]"
    
    logger.info("Got %d/%d chunk summaries", len(summaries), len(chunks))
    
    # 如果只有一个块成功，直接返回（带上限）
    if len(summaries) == 1:
        result = summaries[0]
        if len(result) > max_output_size:
            result = result[:max_output_size] + "\n\n[... truncated ...]"
        return result
    
    # 将摘要综合成最终摘要
    logger.info("Synthesizing %d summaries...", len(summaries))
    
    combined_summaries = "\n\n---\n\n".join(summaries)
    
    synthesis_prompt = f"""You have been given summaries of different sections of a large document. 
Synthesize these into ONE cohesive, comprehensive summary that:
1. Removes redundancy between sections
2. Preserves all key facts, figures, and actionable information
3. Is well-organized with clear structure
4. Is under {max_output_size} characters

{context_str}SECTION SUMMARIES:
{combined_summaries}

Create a single, unified markdown summary."""

    try:
        aux_client, effective_model, extra_body = _resolve_web_extract_auxiliary(model)
        if aux_client is None or not effective_model:
            logger.warning("No auxiliary model for synthesis, concatenating summaries")
            fallback = "\n\n".join(summaries)
            if len(fallback) > max_output_size:
                fallback = fallback[:max_output_size] + "\n\n[... truncated ...]"
            return fallback

        call_kwargs = {
            "task": "web_extract",
            "model": effective_model,
            "messages": [
                {"role": "system", "content": "You synthesize multiple summaries into one cohesive, comprehensive summary. Be thorough but concise."},
                {"role": "user", "content": synthesis_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 20000,
        }
        if extra_body:
            call_kwargs["extra_body"] = extra_body
        response = await async_call_llm(**call_kwargs)
        final_summary = extract_content_or_reasoning(response)

        # 在内容为空时重试一次（仅推理响应）
        if not final_summary:
            logger.warning("Synthesis LLM returned empty content, retrying once")
            response = await async_call_llm(**call_kwargs)
            final_summary = extract_content_or_reasoning(response)

        # 如果重试后仍为 None，回退到连接的摘要
        if not final_summary:
            logger.warning("Synthesis failed after retry — concatenating chunk summaries")
            fallback = "\n\n".join(summaries)
            if len(fallback) > max_output_size:
                fallback = fallback[:max_output_size] + "\n\n[... truncated ...]"
            return fallback

        # 强制执行硬上限
        if len(final_summary) > max_output_size:
            final_summary = final_summary[:max_output_size] + "\n\n[... summary truncated for context management ...]"
        
        original_len = len(content)
        final_len = len(final_summary)
        compression = final_len / original_len if original_len > 0 else 1.0
        
        logger.info("Synthesis complete: %d -> %d chars (%.2f%%)", original_len, final_len, compression * 100)
        return final_summary
        
    except Exception as e:
        logger.warning("Synthesis failed: %s", str(e)[:100])
        # 回退到带截断的连接摘要
        fallback = "\n\n".join(summaries)
        if len(fallback) > max_output_size:
            fallback = fallback[:max_output_size] + "\n\n[... truncated due to synthesis failure ...]"
        return fallback


def clean_base64_images(text: str) -> str:
    """
    从文本中移除 base64 编码的图像以减少 token 数量和杂乱。

    此函数查找并移除各种格式的 base64 编码图像：
    - (data:image/png;base64,...)
    - (data:image/jpeg;base64,...)
    - (data:image/svg+xml;base64,...)
    - data:image/[type];base64,... (without parentheses)
    
    Args:
        text: The text content to clean
        
    Returns:
        Cleaned text with base64 images replaced with placeholders
    """
    # 匹配包裹在括号中的 base64 编码图像的模式
    # 匹配：(data:image/[type];base64,[base64-string])
    base64_with_parens_pattern = r'\(data:image/[^;]+;base64,[A-Za-z0-9+/=]+\)'
    
    # 匹配不带括号的 base64 编码图像的模式
    # 匹配：data:image/[type];base64,[base64-string]
    base64_pattern = r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+'
    
    # 首先替换带括号的图像
    cleaned_text = re.sub(base64_with_parens_pattern, '[BASE64_IMAGE_REMOVED]', text)
    
    # 然后替换任何剩余的非括号图像
    cleaned_text = re.sub(base64_pattern, '[BASE64_IMAGE_REMOVED]', cleaned_text)
    
    return cleaned_text


# ─── Exa 客户端 ──────────────────────────────────────────────────────────────

_exa_client = None

def _get_exa_client():
    """获取或创建 Exa 客户端（延迟初始化）。

    需要 EXA_API_KEY 环境变量。
    """
    from exa_py import Exa
    global _exa_client
    if _exa_client is None:
        api_key = os.getenv("EXA_API_KEY")
        if not api_key:
            raise ValueError(
                "EXA_API_KEY environment variable not set. "
                "Get your API key at https://exa.ai"
            )
        _exa_client = Exa(api_key=api_key)
        _exa_client.headers["x-exa-integration"] = "kclaw"
    return _exa_client


# ─── Exa 搜索和提取辅助函数 ─────────────────────────────────────────────

def _exa_search(query: str, limit: int = 10) -> dict:
    """使用 Exa SDK 搜索并以字典形式返回结果。"""
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"error": "Interrupted", "success": False}

    logger.info("Exa search: '%s' (limit=%d)", query, limit)
    response = _get_exa_client().search(
        query,
        num_results=limit,
        contents={
            "highlights": True,
        },
    )

    web_results = []
    for i, result in enumerate(response.results or []):
        highlights = result.highlights or []
        web_results.append({
            "url": result.url or "",
            "title": result.title or "",
            "description": " ".join(highlights) if highlights else "",
            "position": i + 1,
        })

    return {"success": True, "data": {"web": web_results}}


def _exa_extract(urls: List[str]) -> List[Dict[str, Any]]:
    """使用 Exa SDK 从 URL 提取内容。

    返回符合 LLM 后处理管道期望结构的 result 字典列表
    （url, title, content, metadata）。
    """
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return [{"url": u, "error": "Interrupted", "title": ""} for u in urls]

    logger.info("Exa extract: %d URL(s)", len(urls))
    response = _get_exa_client().get_contents(
        urls,
        text=True,
    )

    results = []
    for result in response.results or []:
        content = result.text or ""
        url = result.url or ""
        title = result.title or ""
        results.append({
            "url": url,
            "title": title,
            "content": content,
            "raw_content": content,
            "metadata": {"sourceURL": url, "title": title},
        })

    return results


# ─── Parallel 搜索和提取辅助函数 ────────────────────────────────────────

def _parallel_search(query: str, limit: int = 5) -> dict:
    """使用 Parallel SDK 搜索并以字典形式返回结果。"""
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"error": "Interrupted", "success": False}

    mode = os.getenv("PARALLEL_SEARCH_MODE", "agentic").lower().strip()
    if mode not in ("fast", "one-shot", "agentic"):
        mode = "agentic"

    logger.info("Parallel search: '%s' (mode=%s, limit=%d)", query, mode, limit)
    response = _get_parallel_client().beta.search(
        search_queries=[query],
        objective=query,
        mode=mode,
        max_results=min(limit, 20),
    )

    web_results = []
    for i, result in enumerate(response.results or []):
        excerpts = result.excerpts or []
        web_results.append({
            "url": result.url or "",
            "title": result.title or "",
            "description": " ".join(excerpts) if excerpts else "",
            "position": i + 1,
        })

    return {"success": True, "data": {"web": web_results}}


async def _parallel_extract(urls: List[str]) -> List[Dict[str, Any]]:
    """使用 Parallel 异步 SDK 从 URL 提取内容。

    返回符合 LLM 后处理管道期望结构的 result 字典列表
    LLM post-processing pipeline (url, title, content, metadata).
    """
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return [{"url": u, "error": "Interrupted", "title": ""} for u in urls]

    logger.info("Parallel extract: %d URL(s)", len(urls))
    response = await _get_async_parallel_client().beta.extract(
        urls=urls,
        full_content=True,
    )

    results = []
    for result in response.results or []:
        content = result.full_content or ""
        if not content:
            content = "\n\n".join(result.excerpts or [])
        url = result.url or ""
        title = result.title or ""
        results.append({
            "url": url,
            "title": title,
            "content": content,
            "raw_content": content,
            "metadata": {"sourceURL": url, "title": title},
        })

    for error in response.errors or []:
        results.append({
            "url": error.url or "",
            "title": "",
            "content": "",
            "error": error.content or error.error_type or "extraction failed",
            "metadata": {"sourceURL": error.url or ""},
        })

    return results


def web_search_tool(query: str, limit: int = 5) -> str:
    """
    Search the web for information using available search API backend.

    This function provides a generic interface for web search that can work
    with multiple backends (Parallel or Firecrawl).

    Note: This function returns search result metadata only (URLs, titles, descriptions).
    Use web_extract_tool to get full content from specific URLs.
    
    Args:
        query (str): The search query to look up
        limit (int): Maximum number of results to return (default: 5)
    
    Returns:
        str: JSON string containing search results with the following structure:
             {
                 "success": bool,
                 "data": {
                     "web": [
                         {
                             "title": str,
                             "url": str,
                             "description": str,
                             "position": int
                         },
                         ...
                     ]
                 }
             }
    
    Raises:
        Exception: If search fails or API key is not set
    """
    debug_call_data = {
        "parameters": {
            "query": query,
            "limit": limit
        },
        "error": None,
        "results_count": 0,
        "original_response_size": 0,
        "final_response_size": 0
    }
    
    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        # 分派到已配置的后端
        backend = _get_backend()
        if backend == "parallel":
            response_data = _parallel_search(query, limit)
            debug_call_data["results_count"] = len(response_data.get("data", {}).get("web", []))
            result_json = json.dumps(response_data, indent=2, ensure_ascii=False)
            debug_call_data["final_response_size"] = len(result_json)
            _debug.log_call("web_search_tool", debug_call_data)
            _debug.save()
            return result_json

        if backend == "exa":
            response_data = _exa_search(query, limit)
            debug_call_data["results_count"] = len(response_data.get("data", {}).get("web", []))
            result_json = json.dumps(response_data, indent=2, ensure_ascii=False)
            debug_call_data["final_response_size"] = len(result_json)
            _debug.log_call("web_search_tool", debug_call_data)
            _debug.save()
            return result_json

        if backend == "tavily":
            logger.info("Tavily search: '%s' (limit: %d)", query, limit)
            raw = _tavily_request("search", {
                "query": query,
                "max_results": min(limit, 20),
                "include_raw_content": False,
                "include_images": False,
            })
            response_data = _normalize_tavily_search_results(raw)
            debug_call_data["results_count"] = len(response_data.get("data", {}).get("web", []))
            result_json = json.dumps(response_data, indent=2, ensure_ascii=False)
            debug_call_data["final_response_size"] = len(result_json)
            _debug.log_call("web_search_tool", debug_call_data)
            _debug.save()
            return result_json

        logger.info("Searching the web for: '%s' (limit: %d)", query, limit)

        response = _get_firecrawl_client().search(
            query=query,
            limit=limit
        )

        web_results = _extract_web_search_results(response)
        results_count = len(web_results)
        logger.info("Found %d search results", results_count)
        
        # 构建仅包含搜索元数据的响应（URL、标题、描述）
        response_data = {
            "success": True,
            "data": {
                "web": web_results
            }
        }
        
        # 捕获调试信息
        debug_call_data["results_count"] = results_count
        
        # 转换为 JSON
        result_json = json.dumps(response_data, indent=2, ensure_ascii=False)
        
        debug_call_data["final_response_size"] = len(result_json)

        # 记录调试信息
        _debug.log_call("web_search_tool", debug_call_data)
        _debug.save()
        
        return result_json
        
    except Exception as e:
        error_msg = f"Error searching web: {str(e)}"
        logger.debug("%s", error_msg)

        debug_call_data["error"] = error_msg
        _debug.log_call("web_search_tool", debug_call_data)
        _debug.save()

        return tool_error(error_msg)


async def web_extract_tool(
    urls: List[str],
    format: str = None,
    use_llm_processing: bool = True,
    model: Optional[str] = None,
    min_length: int = DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION
) -> str:
    """
    Extract content from specific web pages using available extraction API backend.

    This function provides a generic interface for web content extraction that
    can work with multiple backends. Currently uses Firecrawl.

    Args:
        urls (List[str]): List of URLs to extract content from
        format (str): Desired output format ("markdown" or "html", optional)
        use_llm_processing (bool): Whether to process content with LLM for summarization (default: True)
        model (Optional[str]): The model to use for LLM processing (defaults to current auxiliary backend model)
        min_length (int): Minimum content length to trigger LLM processing (default: 5000)

    Security: URLs are checked for embedded secrets before fetching.
    
    Returns:
        str: JSON string containing extracted content. If LLM processing is enabled and successful,
             the 'content' field will contain the processed markdown summary instead of raw content.
    
    Raises:
        Exception: If extraction fails or API key is not set
    """
    # 阻止包含嵌入 secrets 的 URL（防止泄露）
    from agent.redact import _PREFIX_RE
    for _url in urls:
        if _PREFIX_RE.search(_url):
            return json.dumps({
                "success": False,
                "error": "Blocked: URL contains what appears to be an API key or token. "
                         "Secrets must not be sent in URLs.",
            })

    debug_call_data = {
        "parameters": {
            "urls": urls,
            "format": format,
            "use_llm_processing": use_llm_processing,
            "model": model,
            "min_length": min_length
        },
        "error": None,
        "pages_extracted": 0,
        "pages_processed_with_llm": 0,
        "original_response_size": 0,
        "final_response_size": 0,
        "compression_metrics": [],
        "processing_applied": []
    }
    
    try:
        logger.info("Extracting content from %d URL(s)", len(urls))

        # ── SSRF 保护 — 在任何后端之前过滤掉私有/内部 URL ──
        safe_urls = []
        ssrf_blocked: List[Dict[str, Any]] = []
        for url in urls:
            if not is_safe_url(url):
                ssrf_blocked.append({
                    "url": url, "title": "", "content": "",
                    "error": "Blocked: URL targets a private or internal network address",
                })
            else:
                safe_urls.append(url)

        # 仅将安全 URL 分派到已配置的后端
        if not safe_urls:
            results = []
        else:
            backend = _get_backend()

            if backend == "parallel":
                results = await _parallel_extract(safe_urls)
            elif backend == "exa":
                results = _exa_extract(safe_urls)
            elif backend == "tavily":
                logger.info("Tavily extract: %d URL(s)", len(safe_urls))
                raw = _tavily_request("extract", {
                    "urls": safe_urls,
                    "include_images": False,
                })
                results = _normalize_tavily_documents(raw, fallback_url=safe_urls[0] if safe_urls else "")
            else:
                # ── Firecrawl 提取 ──
                # 确定 Firecrawl v2 请求的格式
                formats: List[str] = []
                if format == "markdown":
                    formats = ["markdown"]
                elif format == "html":
                    formats = ["html"]
                else:
                    # 默认：为 LLM 可读性请求 markdown 并包含 html 作为备份
                    formats = ["markdown", "html"]

                # 为简单性和可靠性始终使用单独抓取
                # 批量抓取会增加复杂性，对于少量 URL 来说没有太大好处
                results: List[Dict[str, Any]] = []

                from tools.interrupt import is_interrupted as _is_interrupted
                for url in safe_urls:
                    if _is_interrupted():
                        results.append({"url": url, "error": "Interrupted", "title": ""})
                        continue

                    # 网站策略检查 — 在获取之前阻止
                    blocked = check_website_access(url)
                    if blocked:
                        logger.info("Blocked web_extract for %s by rule %s", blocked["host"], blocked["rule"])
                        results.append({
                            "url": url, "title": "", "content": "",
                            "error": blocked["message"],
                            "blocked_by_policy": {"host": blocked["host"], "rule": blocked["rule"], "source": blocked["source"]},
                        })
                        continue

                    try:
                        logger.info("Scraping: %s", url)
                        # 在线程中运行同步 Firecrawl 抓取，
                        # 60s 超时，这样卡住的获取不会阻止会话。
                        try:
                            scrape_result = await asyncio.wait_for(
                                asyncio.to_thread(
                                    _get_firecrawl_client().scrape,
                                    url=url,
                                    formats=formats,
                                ),
                                timeout=60,
                            )
                        except asyncio.TimeoutError:
                            logger.warning("Firecrawl scrape timed out for %s", url)
                            results.append({
                                "url": url, "title": "", "content": "",
                                "error": "Scrape timed out after 60s — page may be too large or unresponsive. Try browser_navigate instead.",
                            })
                            continue

                        scrape_payload = _extract_scrape_payload(scrape_result)
                        metadata = scrape_payload.get("metadata", {})
                        title = ""
                        content_markdown = scrape_payload.get("markdown")
                        content_html = scrape_payload.get("html")

                        # 确保 metadata 是一个字典（不是对象）
                        if not isinstance(metadata, dict):
                            if hasattr(metadata, 'model_dump'):
                                metadata = metadata.model_dump()
                            elif hasattr(metadata, '__dict__'):
                                metadata = metadata.__dict__
                            else:
                                metadata = {}

                        # 从 metadata 获取标题
                        title = metadata.get("title", "")

                        # 重定向后重新检查最终 URL
                        final_url = metadata.get("sourceURL", url)
                        final_blocked = check_website_access(final_url)
                        if final_blocked:
                            logger.info("Blocked redirected web_extract for %s by rule %s", final_blocked["host"], final_blocked["rule"])
                            results.append({
                                "url": final_url, "title": title, "content": "", "raw_content": "",
                                "error": final_blocked["message"],
                                "blocked_by_policy": {"host": final_blocked["host"], "rule": final_blocked["rule"], "source": final_blocked["source"]},
                            })
                            continue

                        # 根据请求的格式选择内容
                        chosen_content = content_markdown if (format == "markdown" or (format is None and content_markdown)) else content_html or content_markdown or ""

                        results.append({
                            "url": final_url,
                            "title": title,
                            "content": chosen_content,
                            "raw_content": chosen_content,
                            "metadata": metadata  # Now guaranteed to be a dict
                        })

                    except Exception as scrape_err:
                        logger.debug("Scrape failed for %s: %s", url, scrape_err)
                        results.append({
                            "url": url,
                            "title": "",
                            "content": "",
                            "raw_content": "",
                            "error": str(scrape_err)
                        })

        # 将任何 SSRF 阻止的结果合并回来
        if ssrf_blocked:
            results = ssrf_blocked + results

        response = {"results": results}
        
        pages_extracted = len(response.get('results', []))
        logger.info("Extracted content from %d pages", pages_extracted)
        
        debug_call_data["pages_extracted"] = pages_extracted
        debug_call_data["original_response_size"] = len(json.dumps(response))
        effective_model = model or _get_default_summarizer_model()
        auxiliary_available = check_auxiliary_model()

        # 如果启用，使用 LLM 处理每个结果
        if use_llm_processing and auxiliary_available:
            logger.info("Processing extracted content with LLM (parallel)...")
            debug_call_data["processing_applied"].append("llm_processing")

            # 准备并行处理的任务
            async def process_single_result(result):
                """使用 LLM 处理单个结果并返回带有指标的结果。"""
                url = result.get('url', 'Unknown URL')
                title = result.get('title', '')
                raw_content = result.get('raw_content', '') or result.get('content', '')
                
                if not raw_content:
                    return result, None, "no_content"
                
                original_size = len(raw_content)

                # 使用 LLM 处理内容
                processed = await process_content_with_llm(
                    raw_content, url, title, effective_model, min_length
                )
                
                if processed:
                    processed_size = len(processed)
                    compression_ratio = processed_size / original_size if original_size > 0 else 1.0

                    # 使用处理后的内容更新结果
                    result['content'] = processed
                    result['raw_content'] = raw_content
                    
                    metrics = {
                        "url": url,
                        "original_size": original_size,
                        "processed_size": processed_size,
                        "compression_ratio": compression_ratio,
                        "model_used": effective_model
                    }
                    return result, metrics, "processed"
                else:
                    metrics = {
                        "url": url,
                        "original_size": original_size,
                        "processed_size": original_size,
                        "compression_ratio": 1.0,
                        "model_used": None,
                        "reason": "content_too_short"
                    }
                    return result, metrics, "too_short"

            # 并行运行所有 LLM 处理
            results_list = response.get('results', [])
            tasks = [process_single_result(result) for result in results_list]
            processed_results = await asyncio.gather(*tasks)

            # 收集指标并打印结果
            for result, metrics, status in processed_results:
                url = result.get('url', 'Unknown URL')
                if status == "processed":
                    debug_call_data["compression_metrics"].append(metrics)
                    debug_call_data["pages_processed_with_llm"] += 1
                    logger.info("%s (processed)", url)
                elif status == "too_short":
                    debug_call_data["compression_metrics"].append(metrics)
                    logger.info("%s (no processing - content too short)", url)
                else:
                    logger.warning("%s (no content to process)", url)
        else:
            if use_llm_processing and not auxiliary_available:
                logger.warning("LLM processing requested but no auxiliary model available, returning raw content")
                debug_call_data["processing_applied"].append("llm_processing_unavailable")
            # 打印提取页面的摘要以进行调试（原始行为）
            for result in response.get('results', []):
                url = result.get('url', 'Unknown URL')
                content_length = len(result.get('raw_content', ''))
                logger.info("%s (%d characters)", url, content_length)

        # 将输出修剪为每个条目的最小字段：title、content、error
        trimmed_results = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "error": r.get("error"),
                **({  "blocked_by_policy": r["blocked_by_policy"]} if "blocked_by_policy" in r else {}),
            }
            for r in response.get("results", [])
        ]
        trimmed_response = {"results": trimmed_results}

        if trimmed_response.get("results") == []:
            result_json = tool_error("Content was inaccessible or not found")

            cleaned_result = clean_base64_images(result_json)
        
        else:
            result_json = json.dumps(trimmed_response, indent=2, ensure_ascii=False)
            
            cleaned_result = clean_base64_images(result_json)
        
        debug_call_data["final_response_size"] = len(cleaned_result)
        debug_call_data["processing_applied"].append("base64_image_removal")
        
        # 记录调试信息
        _debug.log_call("web_extract_tool", debug_call_data)
        _debug.save()
        
        return cleaned_result
            
    except Exception as e:
        error_msg = f"Error extracting content: {str(e)}"
        logger.debug("%s", error_msg)
        
        debug_call_data["error"] = error_msg
        _debug.log_call("web_extract_tool", debug_call_data)
        _debug.save()
        
        return tool_error(error_msg)


async def web_crawl_tool(
    url: str, 
    instructions: str = None, 
    depth: str = "basic", 
    use_llm_processing: bool = True,
    model: Optional[str] = None,
    min_length: int = DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION
) -> str:
    """
    Crawl a website with specific instructions using available crawling API backend.
    
    This function provides a generic interface for web crawling that can work
    with multiple backends. Currently uses Firecrawl.
    
    Args:
        url (str): The base URL to crawl (can include or exclude https://)
        instructions (str): Instructions for what to crawl/extract using LLM intelligence (optional)
        depth (str): Depth of extraction ("basic" or "advanced", default: "basic")
        use_llm_processing (bool): Whether to process content with LLM for summarization (default: True)
        model (Optional[str]): The model to use for LLM processing (defaults to current auxiliary backend model)
        min_length (int): Minimum content length to trigger LLM processing (default: 5000)
    
    Returns:
        str: JSON string containing crawled content. If LLM processing is enabled and successful,
             the 'content' field will contain the processed markdown summary instead of raw content.
             Each page is processed individually.
    
    Raises:
        Exception: If crawling fails or API key is not set
    """
    debug_call_data = {
        "parameters": {
            "url": url,
            "instructions": instructions,
            "depth": depth,
            "use_llm_processing": use_llm_processing,
            "model": model,
            "min_length": min_length
        },
        "error": None,
        "pages_crawled": 0,
        "pages_processed_with_llm": 0,
        "original_response_size": 0,
        "final_response_size": 0,
        "compression_metrics": [],
        "processing_applied": []
    }
    
    try:
        effective_model = model or _get_default_summarizer_model()
        auxiliary_available = check_auxiliary_model()
        backend = _get_backend()

        # Tavily 通过其 /crawl 端点支持抓取
        if backend == "tavily":
            # 确保 URL 有协议
            if not url.startswith(('http://', 'https://')):
                url = f'https://{url}'

            # SSRF 保护 — 阻止私有/内部地址
            if not is_safe_url(url):
                return json.dumps({"results": [{"url": url, "title": "", "content": "",
                    "error": "Blocked: URL targets a private or internal network address"}]}, ensure_ascii=False)

            # 网站策略检查
            blocked = check_website_access(url)
            if blocked:
                logger.info("Blocked web_crawl for %s by rule %s", blocked["host"], blocked["rule"])
                return json.dumps({"results": [{"url": url, "title": "", "content": "", "error": blocked["message"],
                    "blocked_by_policy": {"host": blocked["host"], "rule": blocked["rule"], "source": blocked["source"]}}]}, ensure_ascii=False)

            from tools.interrupt import is_interrupted as _is_int
            if _is_int():
                return tool_error("Interrupted", success=False)

            logger.info("Tavily crawl: %s", url)
            payload: Dict[str, Any] = {
                "url": url,
                "limit": 20,
                "extract_depth": depth,
            }
            if instructions:
                payload["instructions"] = instructions
            raw = _tavily_request("crawl", payload)
            results = _normalize_tavily_documents(raw, fallback_url=url)

            response = {"results": results}
            # 继续执行下面共享的 LLM 处理和修剪
            # （跳过 Firecrawl 特定的抓取逻辑）
            pages_crawled = len(response.get('results', []))
            logger.info("Crawled %d pages", pages_crawled)
            debug_call_data["pages_crawled"] = pages_crawled
            debug_call_data["original_response_size"] = len(json.dumps(response))

            # 如果启用，使用 LLM 处理每个结果
            if use_llm_processing and auxiliary_available:
                logger.info("Processing crawled content with LLM (parallel)...")
                debug_call_data["processing_applied"].append("llm_processing")

                async def _process_tavily_crawl(result):
                    page_url = result.get('url', 'Unknown URL')
                    title = result.get('title', '')
                    content = result.get('content', '')
                    if not content:
                        return result, None, "no_content"
                    original_size = len(content)
                    processed = await process_content_with_llm(content, page_url, title, effective_model, min_length)
                    if processed:
                        result['raw_content'] = content
                        result['content'] = processed
                        metrics = {"url": page_url, "original_size": original_size, "processed_size": len(processed),
                                   "compression_ratio": len(processed) / original_size if original_size else 1.0, "model_used": effective_model}
                        return result, metrics, "processed"
                    metrics = {"url": page_url, "original_size": original_size, "processed_size": original_size,
                               "compression_ratio": 1.0, "model_used": None, "reason": "content_too_short"}
                    return result, metrics, "too_short"

                tasks = [_process_tavily_crawl(r) for r in response.get('results', [])]
                processed_results = await asyncio.gather(*tasks)
                for result, metrics, status in processed_results:
                    if status == "processed":
                        debug_call_data["compression_metrics"].append(metrics)
                        debug_call_data["pages_processed_with_llm"] += 1

            if use_llm_processing and not auxiliary_available:
                logger.warning("LLM processing requested but no auxiliary model available, returning raw content")
                debug_call_data["processing_applied"].append("llm_processing_unavailable")

            trimmed_results = [{"url": r.get("url", ""), "title": r.get("title", ""), "content": r.get("content", ""), "error": r.get("error"),
                **({  "blocked_by_policy": r["blocked_by_policy"]} if "blocked_by_policy" in r else {})} for r in response.get("results", [])]
            result_json = json.dumps({"results": trimmed_results}, indent=2, ensure_ascii=False)
            cleaned_result = clean_base64_images(result_json)
            debug_call_data["final_response_size"] = len(cleaned_result)
            _debug.log_call("web_crawl_tool", debug_call_data)
            _debug.save()
            return cleaned_result

        # web_crawl 需要 Firecrawl 或 Firecrawl 工具网关 — Parallel 没有 crawl API
        if not check_firecrawl_api_key():
            return json.dumps({
                "error": "web_crawl requires Firecrawl. Set FIRECRAWL_API_KEY, FIRECRAWL_API_URL"
                         f"{_firecrawl_backend_help_suffix()}, or use web_search + web_extract instead.",
                "success": False,
            }, ensure_ascii=False)

        # 确保 URL 有协议
        if not url.startswith(('http://', 'https://')):
            url = f'https://{url}'
            logger.info("Added https:// prefix to URL: %s", url)
        
        instructions_text = f" with instructions: '{instructions}'" if instructions else ""
        logger.info("Crawling %s%s", url, instructions_text)
        
        # SSRF 保护 — 阻止私有/内部地址
        if not is_safe_url(url):
            return json.dumps({"results": [{"url": url, "title": "", "content": "",
                "error": "Blocked: URL targets a private or internal network address"}]}, ensure_ascii=False)

        # 网站策略检查 — 在抓取之前阻止
        blocked = check_website_access(url)
        if blocked:
            logger.info("Blocked web_crawl for %s by rule %s", blocked["host"], blocked["rule"])
            return json.dumps({"results": [{"url": url, "title": "", "content": "", "error": blocked["message"],
                "blocked_by_policy": {"host": blocked["host"], "rule": blocked["rule"], "source": blocked["source"]}}]}, ensure_ascii=False)

        # 使用 Firecrawl 的 v2 crawl 功能
        # 文档：https://docs.firecrawl.dev/features/crawl
        # crawl() 方法自动等待完成并返回所有数据

        # 构建 crawl 参数 — 保持简单
        crawl_params = {
            "limit": 20,  # 限制要抓取的页面数量
            "scrape_options": {
                "formats": ["markdown"]  # Just markdown for simplicity
            }
        }
        
        # 注意：'prompt' 参数在 crawl 中未记录
        # 指令通常与 Extract 端点一起使用，而不是 Crawl
        if instructions:
            logger.info("Instructions parameter ignored (not supported in crawl API)")
        
        from tools.interrupt import is_interrupted as _is_int
        if _is_int():
            return tool_error("Interrupted", success=False)

        try:
            crawl_result = _get_firecrawl_client().crawl(
                url=url,
                **crawl_params
            )
        except Exception as e:
            logger.debug("Crawl API call failed: %s", e)
            raise

        pages: List[Dict[str, Any]] = []
        
        # 处理 crawl 结果 — crawl 方法返回带有 data 属性的 CrawlJob 对象
        data_list = []
        
        # crawl_result 是一个 CrawlJob 对象，带有包含 Document 对象列表的 'data' 属性
        if hasattr(crawl_result, 'data'):
            data_list = crawl_result.data if crawl_result.data else []
            logger.info("Status: %s", getattr(crawl_result, 'status', 'unknown'))
            logger.info("Retrieved %d pages", len(data_list))
            
            # 调试：如果没有数据则检查其他属性
            if not data_list:
                logger.debug("CrawlJob attributes: %s", [attr for attr in dir(crawl_result) if not attr.startswith('_')])
                logger.debug("Status: %s", getattr(crawl_result, 'status', 'N/A'))
                logger.debug("Total: %s", getattr(crawl_result, 'total', 'N/A'))
                logger.debug("Completed: %s", getattr(crawl_result, 'completed', 'N/A'))
                
        elif isinstance(crawl_result, dict) and 'data' in crawl_result:
            data_list = crawl_result.get("data", [])
        else:
            logger.warning("Unexpected crawl result type")
            logger.debug("Result type: %s", type(crawl_result))
            if hasattr(crawl_result, '__dict__'):
                logger.debug("Result attributes: %s", list(crawl_result.__dict__.keys()))
        
        for item in data_list:
            # 处理每个抓取的页面 — 正确处理对象序列化
            page_url = "Unknown URL"
            title = ""
            content_markdown = None
            content_html = None
            metadata = {}
            
            # 从 item 中提取数据
            if hasattr(item, 'model_dump'):
                # Pydantic 模型 — 使用 model_dump 获取字典
                item_dict = item.model_dump()
                content_markdown = item_dict.get('markdown')
                content_html = item_dict.get('html')
                metadata = item_dict.get('metadata', {})
            elif hasattr(item, '__dict__'):
                # 具有属性的常规对象
                content_markdown = getattr(item, 'markdown', None)
                content_html = getattr(item, 'html', None)
                
                # 处理 metadata — 如果是对象则转换为字典
                metadata_obj = getattr(item, 'metadata', {})
                if hasattr(metadata_obj, 'model_dump'):
                    metadata = metadata_obj.model_dump()
                elif hasattr(metadata_obj, '__dict__'):
                    metadata = metadata_obj.__dict__
                elif isinstance(metadata_obj, dict):
                    metadata = metadata_obj
                else:
                    metadata = {}
            elif isinstance(item, dict):
                # 已经是字典
                content_markdown = item.get('markdown')
                content_html = item.get('html')
                metadata = item.get('metadata', {})
            
            # 确保 metadata 是字典（不是对象）
            if not isinstance(metadata, dict):
                if hasattr(metadata, 'model_dump'):
                    metadata = metadata.model_dump()
                elif hasattr(metadata, '__dict__'):
                    metadata = metadata.__dict__
                else:
                    metadata = {}
            
            # 从 metadata 中提取 URL 和标题
            page_url = metadata.get("sourceURL", metadata.get("url", "Unknown URL"))
            title = metadata.get("title", "")
            
            # Re-check crawled page URL against policy
            page_blocked = check_website_access(page_url)
            if page_blocked:
                logger.info("Blocked crawled page %s by rule %s", page_blocked["host"], page_blocked["rule"])
                pages.append({
                    "url": page_url, "title": title, "content": "", "raw_content": "",
                    "error": page_blocked["message"],
                    "blocked_by_policy": {"host": page_blocked["host"], "rule": page_blocked["rule"], "source": page_blocked["source"]},
                })
                continue

            # Choose content (prefer markdown)
            content = content_markdown or content_html or ""
            
            pages.append({
                "url": page_url,
                "title": title,
                "content": content,
                "raw_content": content,
                "metadata": metadata  # Now guaranteed to be a dict
            })

        response = {"results": pages}
        
        pages_crawled = len(response.get('results', []))
        logger.info("Crawled %d pages", pages_crawled)
        
        debug_call_data["pages_crawled"] = pages_crawled
        debug_call_data["original_response_size"] = len(json.dumps(response))
        
        # Process each result with LLM if enabled
        if use_llm_processing and auxiliary_available:
            logger.info("Processing crawled content with LLM (parallel)...")
            debug_call_data["processing_applied"].append("llm_processing")
            
            # Prepare tasks for parallel processing
            async def process_single_crawl_result(result):
                """Process a single crawl result with LLM and return updated result with metrics."""
                page_url = result.get('url', 'Unknown URL')
                title = result.get('title', '')
                content = result.get('content', '')
                
                if not content:
                    return result, None, "no_content"
                
                original_size = len(content)
                
                # Process content with LLM
                processed = await process_content_with_llm(
                    content, page_url, title, effective_model, min_length
                )
                
                if processed:
                    processed_size = len(processed)
                    compression_ratio = processed_size / original_size if original_size > 0 else 1.0
                    
                    # Update result with processed content
                    result['raw_content'] = content
                    result['content'] = processed
                    
                    metrics = {
                        "url": page_url,
                        "original_size": original_size,
                        "processed_size": processed_size,
                        "compression_ratio": compression_ratio,
                        "model_used": effective_model
                    }
                    return result, metrics, "processed"
                else:
                    metrics = {
                        "url": page_url,
                        "original_size": original_size,
                        "processed_size": original_size,
                        "compression_ratio": 1.0,
                        "model_used": None,
                        "reason": "content_too_short"
                    }
                    return result, metrics, "too_short"

            # 并行运行所有 LLM 处理
            results_list = response.get('results', [])
            tasks = [process_single_crawl_result(result) for result in results_list]
            processed_results = await asyncio.gather(*tasks)

            # 收集指标并打印结果
            for result, metrics, status in processed_results:
                page_url = result.get('url', 'Unknown URL')
                if status == "processed":
                    debug_call_data["compression_metrics"].append(metrics)
                    debug_call_data["pages_processed_with_llm"] += 1
                    logger.info("%s (processed)", page_url)
                elif status == "too_short":
                    debug_call_data["compression_metrics"].append(metrics)
                    logger.info("%s (no processing - content too short)", page_url)
                else:
                    logger.warning("%s (no content to process)", page_url)
        else:
            if use_llm_processing and not auxiliary_available:
                logger.warning("LLM processing requested but no auxiliary model available, returning raw content")
                debug_call_data["processing_applied"].append("llm_processing_unavailable")
            # Print summary of crawled pages for debugging (original behavior)
            for result in response.get('results', []):
                page_url = result.get('url', 'Unknown URL')
                content_length = len(result.get('content', ''))
                logger.info("%s (%d characters)", page_url, content_length)
        
        # Trim output to minimal fields per entry: title, content, error
        trimmed_results = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "error": r.get("error"),
                **({  "blocked_by_policy": r["blocked_by_policy"]} if "blocked_by_policy" in r else {}),
            }
            for r in response.get("results", [])
        ]
        trimmed_response = {"results": trimmed_results}
        
        result_json = json.dumps(trimmed_response, indent=2, ensure_ascii=False)
        # Clean base64 images from crawled content
        cleaned_result = clean_base64_images(result_json)
        
        debug_call_data["final_response_size"] = len(cleaned_result)
        debug_call_data["processing_applied"].append("base64_image_removal")
        
        # Log debug information
        _debug.log_call("web_crawl_tool", debug_call_data)
        _debug.save()
        
        return cleaned_result
        
    except Exception as e:
        error_msg = f"Error crawling website: {str(e)}"
        logger.debug("%s", error_msg)
        
        debug_call_data["error"] = error_msg
        _debug.log_call("web_crawl_tool", debug_call_data)
        _debug.save()
        
        return tool_error(error_msg)


# Convenience function to check Firecrawl credentials
def check_firecrawl_api_key() -> bool:
    """
    Check whether the Firecrawl backend is available.

    Availability is true when either:
    1) direct Firecrawl config (`FIRECRAWL_API_KEY` or `FIRECRAWL_API_URL`), or
    2) Firecrawl gateway origin + Nous Subscriber access token
       (fallback when direct Firecrawl is not configured).

    Returns:
        bool: True if direct Firecrawl or the tool-gateway can be used.
    """
    return _has_direct_firecrawl_config() or _is_tool_gateway_ready()


def check_web_api_key() -> bool:
    """Check whether the configured web backend is available."""
    configured = _load_web_config().get("backend", "").lower().strip()
    if configured in ("exa", "parallel", "firecrawl", "tavily"):
        return _is_backend_available(configured)
    return any(_is_backend_available(backend) for backend in ("exa", "parallel", "firecrawl", "tavily"))


def check_auxiliary_model() -> bool:
    """Check if an auxiliary text model is available for LLM content processing."""
    client, _, _ = _resolve_web_extract_auxiliary()
    return client is not None


def get_debug_session_info() -> Dict[str, Any]:
    """Get information about the current debug session."""
    return _debug.get_session_info()


if __name__ == "__main__":
    """
    Simple test/demo when run directly
    """
    print("🌐 Standalone Web Tools Module")
    print("=" * 40)
    
    # Check if API keys are available
    web_available = check_web_api_key()
    tool_gateway_available = _is_tool_gateway_ready()
    firecrawl_key_available = bool(os.getenv("FIRECRAWL_API_KEY", "").strip())
    firecrawl_url_available = bool(os.getenv("FIRECRAWL_API_URL", "").strip())
    nous_available = check_auxiliary_model()
    default_summarizer_model = _get_default_summarizer_model()

    if web_available:
        backend = _get_backend()
        print(f"✅ Web backend: {backend}")
        if backend == "exa":
            print("   Using Exa API (https://exa.ai)")
        elif backend == "parallel":
            print("   Using Parallel API (https://parallel.ai)")
        elif backend == "tavily":
            print("   Using Tavily API (https://tavily.com)")
        else:
            if firecrawl_url_available:
                print(f"   Using self-hosted Firecrawl: {os.getenv('FIRECRAWL_API_URL').strip().rstrip('/')}")
            elif firecrawl_key_available:
                print("   Using direct Firecrawl cloud API")
            elif tool_gateway_available:
                print(f"   Using Firecrawl tool-gateway: {_get_firecrawl_gateway_url()}")
            else:
                print("   Firecrawl backend selected but not configured")
    else:
        print("❌ No web search backend configured")
        print(
            "Set EXA_API_KEY, PARALLEL_API_KEY, TAVILY_API_KEY, FIRECRAWL_API_KEY, FIRECRAWL_API_URL"
            f"{_firecrawl_backend_help_suffix()}"
        )

    if not nous_available:
        print("❌ No auxiliary model available for LLM content processing")
        print("Set OPENROUTER_API_KEY, configure Nous Portal, or set OPENAI_BASE_URL + OPENAI_API_KEY")
        print("⚠️  Without an auxiliary model, LLM content processing will be disabled")
    else:
        print(f"✅ Auxiliary model available: {default_summarizer_model}")

    if not web_available:
        exit(1)

    print("🛠️  Web tools ready for use!")
    
    if nous_available:
        print(f"🧠 LLM content processing available with {default_summarizer_model}")
        print(f"   Default min length for processing: {DEFAULT_MIN_LENGTH_FOR_SUMMARIZATION} chars")
    
    # Show debug mode status
    if _debug.active:
        print(f"🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        print(f"   Debug logs will be saved to: {_debug.log_dir}/web_tools_debug_{_debug.session_id}.json")
    else:
        print("🐛 Debug mode disabled (set WEB_TOOLS_DEBUG=true to enable)")
    
    print("\nBasic usage:")
    print("  from web_tools import web_search_tool, web_extract_tool, web_crawl_tool")
    print("  import asyncio")
    print("")
    print("  # Search (synchronous)")
    print("  results = web_search_tool('Python tutorials')")
    print("")
    print("  # Extract and crawl (asynchronous)")
    print("  async def main():")
    print("      content = await web_extract_tool(['https://example.com'])")
    print("      crawl_data = await web_crawl_tool('example.com', 'Find docs')")
    print("  asyncio.run(main())")
    
    if nous_available:
        print("\nLLM-enhanced usage:")
        print("  # Content automatically processed for pages >5000 chars (default)")
        print("  content = await web_extract_tool(['https://python.org/about/'])")
        print("")
        print("  # Customize processing parameters")
        print("  crawl_data = await web_crawl_tool(")
        print("      'docs.python.org',")
        print("      'Find key concepts',")
        print("      model='google/gemini-3-flash-preview',")
        print("      min_length=3000")
        print("  )")
        print("")
        print("  # Disable LLM processing")
        print("  raw_content = await web_extract_tool(['https://example.com'], use_llm_processing=False)")
    
    print("\nDebug mode:")
    print("  # Enable debug logging")
    print("  export WEB_TOOLS_DEBUG=true")
    print("  # Debug logs capture:")
    print("  # - All tool calls with parameters")
    print("  # - Original API responses")
    print("  # - LLM compression metrics")
    print("  # - Final processed results")
    print("  # Logs saved to: ./logs/web_tools_debug_UUID.json")
    
    print("\n📝 Run 'python test_web_tools_llm.py' to test LLM processing capabilities")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": "Search the web for information on any topic. Returns up to 5 relevant results with titles, URLs, and descriptions.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up on the web"
            }
        },
        "required": ["query"]
    }
}

WEB_EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": "Extract content from web page URLs. Returns page content in markdown format. Also works with PDF URLs (arxiv papers, documents, etc.) — pass the PDF link directly and it converts to markdown text. Pages under 5000 chars return full markdown; larger pages are LLM-summarized and capped at ~5000 chars per page. Pages over 2M chars are refused. If a URL fails or times out, use the browser tool to access it instead.",
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to extract content from (max 5 URLs per call)",
                "maxItems": 5
            }
        },
        "required": ["urls"]
    }
}

registry.register(
    name="web_search",
    toolset="web",
    schema=WEB_SEARCH_SCHEMA,
    handler=lambda args, **kw: web_search_tool(args.get("query", ""), limit=5),
    check_fn=check_web_api_key,
    requires_env=_web_requires_env(),
    emoji="🔍",
    max_result_size_chars=100_000,
)
registry.register(
    name="web_extract",
    toolset="web",
    schema=WEB_EXTRACT_SCHEMA,
    handler=lambda args, **kw: web_extract_tool(
        args.get("urls", [])[:5] if isinstance(args.get("urls"), list) else [], "markdown"),
    check_fn=check_web_api_key,
    requires_env=_web_requires_env(),
    is_async=True,
    emoji="📄",
    max_result_size_chars=100_000,
)
