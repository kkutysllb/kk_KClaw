#!/usr/bin/env python3
"""
视觉工具模块

本模块提供与图像 URL 配合使用的视觉分析工具。
使用集中式辅助视觉路由器，可选择 OpenRouter、
Nous、Codex、原生 Anthropic 或自定义 OpenAI 兼容端点。

可用工具：
- vision_analyze_tool: 使用自定义提示分析图像 URL

功能：
- 从 URL 下载图像并转换为 base64 以便 API 兼容
- 全面的图像描述
- 基于用户查询的上下文感知分析
- 自动临时文件清理
- 适当的错误处理和验证
- 调试日志支持

用法：
    from vision_tools import vision_analyze_tool
    import asyncio

    # 分析图像
    result = await vision_analyze_tool(
        image_url="https://example.com/image.jpg",
        user_prompt="这座建筑是什么建筑风格？"
    )
"""

import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional
from urllib.parse import urlparse
import httpx
from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
from tools.debug_helpers import DebugSession
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_debug = DebugSession("vision_tools", env_var="VISION_TOOLS_DEBUG")

# Configurable HTTP download timeout for _download_image().
# Separate from auxiliary.vision.timeout which governs the LLM API call.
# Resolution: config.yaml auxiliary.vision.download_timeout → env var → 30s default.
def _resolve_download_timeout() -> float:
    env_val = os.getenv("KCLAW_VISION_DOWNLOAD_TIMEOUT", "").strip()
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass
    try:
        from kclaw_cli.config import load_config
        cfg = load_config()
        val = cfg.get("auxiliary", {}).get("vision", {}).get("download_timeout")
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 30.0

_VISION_DOWNLOAD_TIMEOUT = _resolve_download_timeout()


def _validate_image_url(url: str) -> bool:
    """
    图像 URL 格式的基本验证。

    参数:
        url (str): 要验证的 URL

    返回:
        bool: 如果 URL 看起来有效则为 True，否则为 False
    """
    if not url or not isinstance(url, str):
        return False

    # 基本的 HTTP/HTTPS URL 检查
    if not url.startswith(("http://", "https://")):
        return False

    # 解析以确保至少有一个网络位置；仍允许没有文件扩展名的 URL
    # （例如重定向到图像的 CDN 端点）。
    parsed = urlparse(url)
    if not parsed.netloc:
        return False

    # 阻止私有/内部地址以防止 SSRF
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        return False

    return True


def _detect_image_mime_type(image_path: Path) -> Optional[str]:
    """当文件看起来是支持的图像时返回 MIME 类型。"""
    with image_path.open("rb") as f:
        header = f.read(64)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if image_path.suffix.lower() == ".svg":
        head = image_path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
        if "<svg" in head:
            return "image/svg+xml"
    return None


async def _download_image(image_url: str, destination: Path, max_retries: int = 3) -> Path:
    """
    从 URL 下载图像到本地目标（异步），并带有重试逻辑。

    参数:
        image_url (str): 要下载的图像的 URL
        destination (Path): 图像应保存的路径
        max_retries (int): 最大重试次数（默认：3）

    返回:
        Path: 下载图像的路径

    抛出:
        Exception: 如果所有重试后下载失败
    """
    import asyncio
    
    # 如果不存在则创建父目录
    destination.parent.mkdir(parents=True, exist_ok=True)
    
    async def _ssrf_redirect_guard(response):
        """重新验证每个重定向目标以防止基于重定向的 SSRF。

        没有这个，攻击者可以托管一个公共 URL，通过 302 重定向
        到 http://169.254.169.254/，从而绕过预检 is_safe_url 检查。

        必须是异步的，因为 httpx.AsyncClient 等待事件钩子。
        """
        if response.is_redirect and response.next_request:
            redirect_url = str(response.next_request.url)
            from tools.url_safety import is_safe_url
            if not is_safe_url(redirect_url):
                raise ValueError(
                    f"Blocked redirect to private/internal address: {redirect_url}"
                )

    last_error = None
    for attempt in range(max_retries):
        try:
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])

            # 使用异步 httpx 下载图像并设置适当的请求头
            # 启用 follow_redirects 以处理重定向的图像 CDN（例如 Imgur、Picsum）
            # SSRF: event_hooks 验证每个重定向目标是否在私有 IP 范围内
            async with httpx.AsyncClient(
                timeout=_VISION_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                event_hooks={"response": [_ssrf_redirect_guard]},
            ) as client:
                response = await client.get(
                    image_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()

                final_url = str(response.url)
                blocked = check_website_access(final_url)
                if blocked:
                    raise PermissionError(blocked["message"])
                
                # Save the image content
                destination.write_bytes(response.content)
            
            return destination
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)  # 2秒、4秒、8秒
                logger.warning("Image download failed (attempt %s/%s): %s", attempt + 1, max_retries, str(e)[:50])
                logger.warning("Retrying in %ss...", wait_time)
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "Image download failed after %s attempts: %s",
                    max_retries,
                    str(e)[:100],
                    exc_info=True,
                )
    
    if last_error is None:
        raise RuntimeError(
            f"_download_image exited retry loop without attempting (max_retries={max_retries})"
        )
    raise last_error


def _determine_mime_type(image_path: Path) -> str:
    """
    根据文件扩展名确定图像的 MIME 类型。

    参数:
        image_path (Path): 图像文件的路径

    返回:
        str: MIME 类型（如果未知默认为 image/jpeg）
    """
    extension = image_path.suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml'
    }
    return mime_types.get(extension, 'image/jpeg')


def _image_to_base64_data_url(image_path: Path, mime_type: Optional[str] = None) -> str:
    """
    将图像文件转换为 base64 编码的数据 URL。

    参数:
        image_path (Path): 图像文件的路径
        mime_type (Optional[str]): 图像的 MIME 类型（如果为 None 则自动检测）

    返回:
        str: Base64 编码的数据 URL（例如 "data:image/jpeg;base64,..."）
    """
    # 将图像作为字节读取
    data = image_path.read_bytes()
    
    # 编码为 base64
    encoded = base64.b64encode(data).decode("ascii")
    
    # 确定 MIME 类型
    mime = mime_type or _determine_mime_type(image_path)
    
    # 创建数据 URL
    data_url = f"data:{mime};base64,{encoded}"
    
    return data_url


async def vision_analyze_tool(
    image_url: str,
    user_prompt: str,
    model: str = None,
) -> str:
    """
    使用视觉 AI 分析来自 URL 或本地文件路径的图像。

    此工具接受 HTTP/HTTPS URL 或本地文件路径。对于 URL，
    它会首先下载图像。在两种情况下，图像都会转换为 base64，
    并通过 OpenRouter API 使用 Gemini 3 Flash Preview 进行处理。

    user_prompt 参数应由调用函数（通常是 model_tools.py）
    预先格式化，以包含完整描述请求和具体问题。

    参数:
        image_url (str): 要分析的图像的 URL 或本地文件路径。
                         接受 http://、https:// URL 或绝对/相对文件路径。
        user_prompt (str): 视觉模型的预格式化提示
        model (str): 要使用的视觉模型（默认：google/gemini-3-flash-preview）

    返回:
        str: 包含分析结果的 JSON 字符串，结构如下：
             {
                 "success": bool,
                 "analysis": str（如果为 None 则为错误消息）
             }

    抛出:
        Exception: 如果下载失败、分析失败或 API 密钥未设置

    注意:
        - 对于 URL，临时图像存储在 ./temp_vision_images/ 中并被清理
        - 对于本地文件路径，文件直接使用且不被删除
        - 支持常见图像格式（JPEG、PNG、GIF、WebP 等）
    """
    debug_call_data = {
        "parameters": {
            "image_url": image_url,
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "model": model
        },
        "error": None,
        "success": False,
        "analysis_length": 0,
        "model_used": model,
        "image_size_bytes": 0
    }
    
    temp_image_path = None
    # 跟踪处理后是否应清理文件。
    # 本地文件（例如来自图像缓存的）不应被删除。
    should_cleanup = True
    detected_mime_type = None
    
    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        logger.info("Analyzing image: %s", image_url[:60])
        logger.info("User prompt: %s", user_prompt[:100])
        
        # 确定这是本地文件路径还是远程 URL
        local_path = Path(os.path.expanduser(image_url))
        if local_path.is_file():
            # 本地文件路径（例如来自平台图像缓存）— 跳过下载
            logger.info("Using local image file: %s", image_url)
            temp_image_path = local_path
            should_cleanup = False  # 不删除缓存/本地文件
        elif _validate_image_url(image_url):
            # 远程 URL — 下载到临时位置
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])
            logger.info("Downloading image from URL...")
            temp_dir = Path("./temp_vision_images")
            temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_image_path)
            should_cleanup = True
        else:
            raise ValueError(
                "Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path."
            )
        
        # 获取图像文件大小用于日志记录
        image_size_bytes = temp_image_path.stat().st_size
        image_size_kb = image_size_bytes / 1024
        logger.info("Image ready (%.1f KB)", image_size_kb)

        detected_mime_type = _detect_image_mime_type(temp_image_path)
        if not detected_mime_type:
            raise ValueError("Only real image files are supported for vision analysis.")
        
        # 将图像转换为 base64 数据 URL
        logger.info("Converting image to base64...")
        image_data_url = _image_to_base64_data_url(temp_image_path, mime_type=detected_mime_type)
        # 计算 KB 大小以便更好地可读性
        data_size_kb = len(image_data_url) / 1024
        logger.info("Image converted to base64 (%.1f KB)", data_size_kb)
        
        debug_call_data["image_size_bytes"] = image_size_bytes
        
        # 使用提供的提示（model_tools.py 现在处理完整描述格式化）
        comprehensive_prompt = user_prompt
        
        # 准备带有 base64 编码图像的消息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": comprehensive_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url
                        }
                    }
                ]
            }
        ]
        
        logger.info("Processing image with vision model...")
        
        # 通过集中式路由器调用视觉 API。
        # 从 config.yaml 读取超时（auxiliary.vision.timeout），默认 120 秒。
        # 本地视觉模型（llama.cpp、ollama）可能需要远超 30 秒。
        vision_timeout = 120.0
        try:
            from kclaw_cli.config import load_config
            _cfg = load_config()
            _vt = _cfg.get("auxiliary", {}).get("vision", {}).get("timeout")
            if _vt is not None:
                vision_timeout = float(_vt)
        except Exception:
            pass
        call_kwargs = {
            "task": "vision",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2000,
            "timeout": vision_timeout,
        }
        if model:
            call_kwargs["model"] = model
        response = await async_call_llm(**call_kwargs)
        
        # 提取分析 — 如果内容为空则回退到推理
        analysis = extract_content_or_reasoning(response)

        # 对空内容重试一次（仅推理响应）
        if not analysis:
            logger.warning("Vision LLM returned empty content, retrying once")
            response = await async_call_llm(**call_kwargs)
            analysis = extract_content_or_reasoning(response)

        analysis_length = len(analysis)
        
        logger.info("Image analysis completed (%s characters)", analysis_length)
        
        # 准备成功响应
        result = {
            "success": True,
            "analysis": analysis or "There was a problem with the request and the image could not be analyzed."
        }
        
        debug_call_data["success"] = True
        debug_call_data["analysis_length"] = analysis_length
        
        # Log debug information
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        error_msg = f"Error analyzing image: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)
        
        # 检测视觉能力错误 — 给模型一个清晰的消息
        # 以便它可以通知用户而不是神秘的 API 错误。
        err_str = str(e).lower()
        if any(hint in err_str for hint in (
            "402", "insufficient", "payment required", "credits", "billing",
        )):
            analysis = (
                "Insufficient credits or payment required. Please top up your "
                f"API provider account and try again. Error: {e}"
            )
        elif any(hint in err_str for hint in (
            "does not support", "not support image", "invalid_request",
            "content_policy", "image_url", "multimodal",
            "unrecognized request argument", "image input",
        )):
            analysis = (
                f"{model} does not support vision or our request was not "
                f"accepted by the server. Error: {e}"
            )
        else:
            analysis = (
                "There was a problem with the request and the image could not "
                f"be analyzed. Error: {e}"
            )
        
        # 准备错误响应
        result = {
            "success": False,
            "error": error_msg,
            "analysis": analysis,
        }
        
        debug_call_data["error"] = error_msg
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
    
    finally:
        # 清理临时图像文件（但不清理本地/缓存文件）
        if should_cleanup and temp_image_path and temp_image_path.exists():
            try:
                temp_image_path.unlink()
                logger.debug("Cleaned up temporary image file")
            except Exception as cleanup_error:
                logger.warning(
                    "Could not delete temporary file: %s", cleanup_error, exc_info=True
                )


def check_vision_requirements() -> bool:
    """检查配置的运行时视觉路径是否可以解析客户端。"""
    try:
        from agent.auxiliary_client import resolve_vision_provider_client

        _provider, client, _model = resolve_vision_provider_client()
        return client is not None
    except Exception:
        return False


def get_debug_session_info() -> Dict[str, Any]:
    """
    Get information about the current debug session.
    
    Returns:
        Dict[str, Any]: Dictionary containing debug session information
    """
    return _debug.get_session_info()


if __name__ == "__main__":
    """
    Simple test/demo when run directly
    """
    print("👁️ Vision Tools Module")
    print("=" * 40)
    
    # Check if vision model is available
    api_available = check_vision_requirements()
    
    if not api_available:
        print("❌ No auxiliary vision model available")
        print("Configure a supported multimodal backend (OpenRouter, Nous, Codex, Anthropic, or a custom OpenAI-compatible endpoint).")
        exit(1)
    else:
        print("✅ Vision model available")
    
    print("🛠️ Vision tools ready for use!")
    
    # Show debug mode status
    if _debug.active:
        print(f"🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        print(f"   Debug logs will be saved to: ./logs/vision_tools_debug_{_debug.session_id}.json")
    else:
        print("🐛 Debug mode disabled (set VISION_TOOLS_DEBUG=true to enable)")
    
    print("\nBasic usage:")
    print("  from vision_tools import vision_analyze_tool")
    print("  import asyncio")
    print("")
    print("  async def main():")
    print("      result = await vision_analyze_tool(")
    print("          image_url='https://example.com/image.jpg',")
    print("          user_prompt='What do you see in this image?'")
    print("      )")
    print("      print(result)")
    print("  asyncio.run(main())")
    
    print("\nExample prompts:")
    print("  - 'What architectural style is this building?'")
    print("  - 'Describe the emotions and mood in this image'")
    print("  - 'What text can you read in this image?'")
    print("  - 'Identify any safety hazards visible'")
    print("  - 'What products or brands are shown?'")
    
    print("\nDebug mode:")
    print("  # Enable debug logging")
    print("  export VISION_TOOLS_DEBUG=true")
    print("  # Debug logs capture all vision analysis calls and results")
    print("  # Logs saved to: ./logs/vision_tools_debug_UUID.json")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": "Analyze images using AI vision. Provides a comprehensive description and answers a specific question about the image content.",
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Image URL (http/https) or local file path to analyze."
            },
            "question": {
                "type": "string",
                "description": "Your specific question or request about the image to resolve. The AI will automatically provide a complete image description AND answer your specific question."
            }
        },
        "required": ["image_url", "question"]
    }
}


def _handle_vision_analyze(args: Dict[str, Any], **kw: Any) -> Awaitable[str]:
    image_url = args.get("image_url", "")
    question = args.get("question", "")
    full_prompt = (
        "Fully describe and explain everything about this image, then answer the "
        f"following question:\n\n{question}"
    )
    model = os.getenv("AUXILIARY_VISION_MODEL", "").strip() or None
    return vision_analyze_tool(image_url, full_prompt, model)


registry.register(
    name="vision_analyze",
    toolset="vision",
    schema=VISION_ANALYZE_SCHEMA,
    handler=_handle_vision_analyze,
    check_fn=check_vision_requirements,
    is_async=True,
    emoji="👁️",
)
