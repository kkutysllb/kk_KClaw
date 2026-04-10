#!/usr/bin/env python3
"""
文本转语音工具模块

支持五种 TTS 提供者：
- Edge TTS（默认，免费，无需 API 密钥）：Microsoft Edge 神经语音
- ElevenLabs（高级）：高质量语音，需要 ELEVENLABS_API_KEY
- OpenAI TTS：良好质量，需要 OPENAI_API_KEY
- MiniMax TTS：高质量语音克隆，需要 MINIMAX_API_KEY
- NeuTTS（本地，免费，无需 API 密钥）：通过 neutts_cli 的设备上 TTS，需要安装 neutts

输出格式：
- Opus (.ogg) 用于 Telegram 语音气泡（Edge TTS 需要 ffmpeg）
- MP3 (.mp3) 用于其他所有内容（CLI、Discord、WhatsApp）

配置从 ~/.kclaw/config.yaml 的 'tts:' 键下加载。
用户选择提供者和语音；模型只发送文本。

用法：
    from tools.tts_tool import text_to_speech_tool, check_tts_requirements

    result = text_to_speech_tool(text="Hello world")
"""

import asyncio
import datetime
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, Any, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)
from tools.managed_tool_gateway import resolve_managed_tool_gateway
from tools.tool_backend_helpers import managed_nous_tools_enabled, resolve_openai_audio_api_key

# ---------------------------------------------------------------------------
# 延迟导入 -- 提供者只在实际使用时导入，以避免在无头环境（SSH、Docker、WSL、无 PortAudio）中崩溃。
# ---------------------------------------------------------------------------

def _import_edge_tts():
    """延迟导入 edge_tts。返回模块或抛出 ImportError。"""
    import edge_tts
    return edge_tts

def _import_elevenlabs():
    """延迟导入 ElevenLabs 客户端。返回类或抛出 ImportError。"""
    from elevenlabs.client import ElevenLabs
    return ElevenLabs

def _import_openai_client():
    """延迟导入 OpenAI 客户端。返回类或抛出 ImportError。"""
    from openai import OpenAI as OpenAIClient
    return OpenAIClient

def _import_sounddevice():
    """延迟导入 sounddevice。返回模块或抛出 ImportError/OSError。"""
    import sounddevice as sd
    return sd


# ===========================================================================
# 默认值
# ===========================================================================
DEFAULT_PROVIDER = "edge"
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_ELEVENLABS_STREAMING_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MINIMAX_MODEL = "speech-2.8-hd"
DEFAULT_MINIMAX_VOICE_ID = "English_Graceful_Lady"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1/t2a_v2"

def _get_default_output_dir() -> str:
    from kclaw_constants import get_kclaw_dir
    return str(get_kclaw_dir("cache/audio", "audio_cache"))

DEFAULT_OUTPUT_DIR = _get_default_output_dir()
MAX_TEXT_LENGTH = 4000


# ===========================================================================
# 配置加载器 -- 从 ~/.kclaw/config.yaml 读取 tts: 部分
# ===========================================================================
def _load_tts_config() -> Dict[str, Any]:
    """
    Load TTS configuration from ~/.kclaw/config.yaml.

    Returns a dict with provider settings. Falls back to defaults
    for any missing fields.
    """
    try:
        from kclaw_cli.config import load_config
        config = load_config()
        return config.get("tts", {})
    except ImportError:
        logger.debug("kclaw_cli.config not available, using default TTS config")
        return {}
    except Exception as e:
        logger.warning("Failed to load TTS config: %s", e, exc_info=True)
        return {}


def _get_provider(tts_config: Dict[str, Any]) -> str:
    """获取配置的 TTS 提供者名称。"""
    return (tts_config.get("provider") or DEFAULT_PROVIDER).lower().strip()


# ===========================================================================
# ffmpeg Opus 转换（Edge TTS MP3 -> OGG Opus 用于 Telegram）
# ===========================================================================
def _has_ffmpeg() -> bool:
    """检查系统上是否有 ffmpeg。"""
    return shutil.which("ffmpeg") is not None


def _convert_to_opus(mp3_path: str) -> Optional[str]:
    """
    将 MP3 文件转换为 OGG Opus 格式，用于 Telegram 语音气泡。

    参数:
        mp3_path: 输入 MP3 文件的路径。

    返回:
        .ogg 文件的路径，如果转换失败则返回 None。
    """
    if not _has_ffmpeg():
        return None

    ogg_path = mp3_path.rsplit(".", 1)[0] + ".ogg"
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", mp3_path, "-acodec", "libopus",
             "-ac", "1", "-b:a", "64k", "-vbr", "off", ogg_path, "-y"],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg conversion failed with return code %d: %s", 
                          result.returncode, result.stderr.decode('utf-8', errors='ignore')[:200])
            return None
        if os.path.exists(ogg_path) and os.path.getsize(ogg_path) > 0:
            return ogg_path
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg OGG conversion timed out after 30s")
    except FileNotFoundError:
        logger.warning("ffmpeg not found in PATH")
    except Exception as e:
        logger.warning("ffmpeg OGG conversion failed: %s", e, exc_info=True)
    return None


# ===========================================================================
# 提供者：Edge TTS（免费）
# ===========================================================================
async def _generate_edge_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    使用 Edge TTS 生成音频。

    参数:
        text: 要转换的文本。
        output_path: 保存 MP3 文件的位置。
        tts_config: TTS 配置字典。

    返回:
        保存音频文件的路径。
    """
    _edge_tts = _import_edge_tts()
    edge_config = tts_config.get("edge", {})
    voice = edge_config.get("voice", DEFAULT_EDGE_VOICE)

    communicate = _edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path


# ===========================================================================
# 提供者：ElevenLabs（高级）
# ===========================================================================
def _generate_elevenlabs(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    使用 ElevenLabs 生成音频。

    参数:
        text: 要转换的文本。
        output_path: 保存音频文件的位置。
        tts_config: TTS 配置字典。

    返回:
        保存音频文件的路径。
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set. Get one at https://elevenlabs.io/")

    el_config = tts_config.get("elevenlabs", {})
    voice_id = el_config.get("voice_id", DEFAULT_ELEVENLABS_VOICE_ID)
    model_id = el_config.get("model_id", DEFAULT_ELEVENLABS_MODEL_ID)

    # 根据文件扩展名确定输出格式
    if output_path.endswith(".ogg"):
        output_format = "opus_48000_64"
    else:
        output_format = "mp3_44100_128"

    ElevenLabs = _import_elevenlabs()
    client = ElevenLabs(api_key=api_key)
    audio_generator = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
    )

    # audio_generator 产生块 — 写入所有块
    with open(output_path, "wb") as f:
        for chunk in audio_generator:
            f.write(chunk)

    return output_path


# ===========================================================================
# 提供者：OpenAI TTS
# ===========================================================================
def _generate_openai_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    使用 OpenAI TTS 生成音频。

    参数:
        text: 要转换的文本。
        output_path: 保存音频文件的位置。
        tts_config: TTS 配置字典。

    返回:
        保存音频文件的路径。
    """
    api_key, base_url = _resolve_openai_audio_client_config()

    oai_config = tts_config.get("openai", {})
    model = oai_config.get("model", DEFAULT_OPENAI_MODEL)
    voice = oai_config.get("voice", DEFAULT_OPENAI_VOICE)
    base_url = oai_config.get("base_url", base_url)

    # 根据扩展名确定响应格式
    if output_path.endswith(".ogg"):
        response_format = "opus"
    else:
        response_format = "mp3"

    OpenAIClient = _import_openai_client()
    client = OpenAIClient(api_key=api_key, base_url=base_url)
    try:
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format=response_format,
            extra_headers={"x-idempotency-key": str(uuid.uuid4())},
        )

        response.stream_to_file(output_path)
        return output_path
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


# ===========================================================================
# 提供者：MiniMax TTS
# ===========================================================================
def _generate_minimax_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    使用 MiniMax TTS API 生成音频。

    MiniMax 返回十六进制编码的音频数据。支持流式（SSE）和
    非流式模式。此实现为简单起见使用非流式。

    参数:
        text: 要转换的文本（最多 10,000 个字符）。
        output_path: 保存音频文件的位置。
        tts_config: TTS 配置字典。

    返回:
        保存音频文件的路径。
    """
    import requests

    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not set. Get one at https://platform.minimax.io/")

    mm_config = tts_config.get("minimax", {})
    model = mm_config.get("model", DEFAULT_MINIMAX_MODEL)
    voice_id = mm_config.get("voice_id", DEFAULT_MINIMAX_VOICE_ID)
    speed = mm_config.get("speed", 1)
    vol = mm_config.get("vol", 1)
    pitch = mm_config.get("pitch", 0)
    base_url = mm_config.get("base_url", DEFAULT_MINIMAX_BASE_URL)

    # 根据输出扩展名确定音频格式
    if output_path.endswith(".wav"):
        audio_format = "wav"
    elif output_path.endswith(".flac"):
        audio_format = "flac"
    else:
        audio_format = "mp3"

    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": audio_format,
            "channel": 1,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    response = requests.post(base_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    result = response.json()
    base_resp = result.get("base_resp", {})
    status_code = base_resp.get("status_code", -1)

    if status_code != 0:
        status_msg = base_resp.get("status_msg", "unknown error")
        raise RuntimeError(f"MiniMax TTS API error (code {status_code}): {status_msg}")

    hex_audio = result.get("data", {}).get("audio", "")
    if not hex_audio:
        raise RuntimeError("MiniMax TTS returned empty audio data")

    # MiniMax 返回十六进制编码的音频（不是 base64）
    audio_bytes = bytes.fromhex(hex_audio)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return output_path


# ===========================================================================
# NeuTTS（本地，通过 neutts_cli 的设备上 TTS）
# ===========================================================================

def _check_neutts_available() -> bool:
    """检查 neutts 引擎是否可导入（本地安装）。"""
    try:
        import importlib.util
        return importlib.util.find_spec("neutts") is not None
    except Exception:
        return False


def _default_neutts_ref_audio() -> str:
    """返回捆绑的默认语音参考音频的路径。"""
    return str(Path(__file__).parent / "neutts_samples" / "jo.wav")


def _default_neutts_ref_text() -> str:
    """返回捆绑的默认语音参考文本的路径。"""
    return str(Path(__file__).parent / "neutts_samples" / "jo.txt")


def _generate_neutts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """使用本地 NeuTTS 引擎生成语音。

    通过 tools/neutts_synth.py 在子进程中运行合成，以将
    ~500MB 模型保持在单独的进程中，合成后退出。
    输出 WAV；如果需要，调用者处理 Telegram 的转换。
    """
    import sys

    neutts_config = tts_config.get("neutts", {})
    ref_audio = neutts_config.get("ref_audio", "") or _default_neutts_ref_audio()
    ref_text = neutts_config.get("ref_text", "") or _default_neutts_ref_text()
    model = neutts_config.get("model", "neuphonic/neutts-air-q4-gguf")
    device = neutts_config.get("device", "cpu")

    # NeuTTS 原生输出 WAV — 使用 .wav 路径进行生成，
    # 让调用者之后转换为最终格式。
    wav_path = output_path
    if not output_path.endswith(".wav"):
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"

    synth_script = str(Path(__file__).parent / "neutts_synth.py")
    cmd = [
        sys.executable, synth_script,
        "--text", text,
        "--out", wav_path,
        "--ref-audio", ref_audio,
        "--ref-text", ref_text,
        "--model", model,
        "--device", device,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 从 stderr 中过滤掉 "OK:" 行
        error_lines = [l for l in stderr.splitlines() if not l.startswith("OK:")]
        raise RuntimeError(f"NeuTTS synthesis failed: {chr(10).join(error_lines) or 'unknown error'}")

    # 如果调用者想要 .mp3 或 .ogg，则从 WAV 转换
    if wav_path != output_path:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            conv_cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            subprocess.run(conv_cmd, check=True, timeout=30)
            os.remove(wav_path)
        else:
            # 没有 ffmpeg — 只需将 WAV 重命名为预期路径
            os.rename(wav_path, output_path)

    return output_path


# ===========================================================================
# 主工具函数
# ===========================================================================
def text_to_speech_tool(
    text: str,
    output_path: Optional[str] = None,
) -> str:
    """
    将文本转换为语音音频。

    从 ~/.kclaw/config.yaml（tts: 部分）读取提供者/语音配置。
    模型发送文本；用户配置语音和提供者。

    在消息平台上，返回的 MEDIA:<path> 标签被发送管道拦截，
    并作为原生语音消息传递。在 CLI 模式下，文件保存到 ~/voice-memos/。

    参数:
        text: 要转换为语音的文本。
        output_path: 可选的自定义保存路径。默认为 ~/voice-memos/<timestamp>.mp3

    返回:
        str: 包含 success、file_path 和可选 MEDIA 标签的 JSON 结果。
    """
    if not text or not text.strip():
        return tool_error("Text is required", success=False)

    # Truncate very long text with a warning
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning("TTS text too long (%d chars), truncating to %d", len(text), MAX_TEXT_LENGTH)
        text = text[:MAX_TEXT_LENGTH]

    tts_config = _load_tts_config()
    provider = _get_provider(tts_config)

    # 从网关环境变量检测平台以选择最佳输出格式。
    # Telegram 语音气泡需要 Opus (.ogg)；OpenAI 和 ElevenLabs 可以
    # 原生生成 Opus（不需要 ffmpeg）。Edge TTS 总是输出 MP3，
    # 需要 ffmpeg 进行转换。
    platform = os.getenv("KCLAW_SESSION_PLATFORM", "").lower()
    want_opus = (platform == "telegram")

    # Determine output path
    if output_path:
        file_path = Path(output_path).expanduser()
    else:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(DEFAULT_OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 对于支持原生 Opus 输出的提供者，使用 .ogg 用于 Telegram，
        # 否则回退到 .mp3（Edge TTS 稍后会尝试 ffmpeg 转换）。
        if want_opus and provider in ("openai", "elevenlabs"):
            file_path = out_dir / f"tts_{timestamp}.ogg"
        else:
            file_path = out_dir / f"tts_{timestamp}.mp3"

    # 确保父目录存在
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_str = str(file_path)

    try:
        # Generate audio with the configured provider
        if provider == "elevenlabs":
            try:
                _import_elevenlabs()
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "ElevenLabs provider selected but 'elevenlabs' package not installed. Run: pip install elevenlabs"
                }, ensure_ascii=False)
            logger.info("Generating speech with ElevenLabs...")
            _generate_elevenlabs(text, file_str, tts_config)

        elif provider == "openai":
            try:
                _import_openai_client()
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "OpenAI provider selected but 'openai' package not installed."
                }, ensure_ascii=False)
            logger.info("Generating speech with OpenAI TTS...")
            _generate_openai_tts(text, file_str, tts_config)

        elif provider == "minimax":
            logger.info("Generating speech with MiniMax TTS...")
            _generate_minimax_tts(text, file_str, tts_config)

        elif provider == "neutts":
            if not _check_neutts_available():
                return json.dumps({
                    "success": False,
                    "error": "NeuTTS provider selected but neutts is not installed. "
                             "Run kclaw setup and choose NeuTTS, or install espeak-ng and run python -m pip install -U neutts[all]."
                }, ensure_ascii=False)
            logger.info("Generating speech with NeuTTS (local)...")
            _generate_neutts(text, file_str, tts_config)

        else:
            # Default: Edge TTS (free), with NeuTTS as local fallback
            edge_available = True
            try:
                _import_edge_tts()
            except ImportError:
                edge_available = False

            if edge_available:
                logger.info("Generating speech with Edge TTS...")
                try:
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(
                            lambda: asyncio.run(_generate_edge_tts(text, file_str, tts_config))
                        ).result(timeout=60)
                except RuntimeError:
                    asyncio.run(_generate_edge_tts(text, file_str, tts_config))
            elif _check_neutts_available():
                logger.info("Edge TTS not available, falling back to NeuTTS (local)...")
                provider = "neutts"
                _generate_neutts(text, file_str, tts_config)
            else:
                return json.dumps({
                    "success": False,
                    "error": "No TTS provider available. Install edge-tts (pip install edge-tts) "
                             "or set up NeuTTS for local synthesis."
                }, ensure_ascii=False)

        # 检查文件是否实际创建
        if not os.path.exists(file_str) or os.path.getsize(file_str) == 0:
            return json.dumps({
                "success": False,
                "error": f"TTS generation produced no output (provider: {provider})"
            }, ensure_ascii=False)

        # 尝试为 Telegram 兼容性进行 Opus 转换
        # Edge TTS 输出 MP3，NeuTTS 输出 WAV — 两者都需要 ffmpeg 转换
        voice_compatible = False
        if provider in ("edge", "neutts", "minimax") and not file_str.endswith(".ogg"):
            opus_path = _convert_to_opus(file_str)
            if opus_path:
                file_str = opus_path
                voice_compatible = True
        elif provider in ("elevenlabs", "openai"):
            # 如果路径以 .ogg 结尾，这些提供者可以原生输出 Opus
            voice_compatible = file_str.endswith(".ogg")

        file_size = os.path.getsize(file_str)
        logger.info("TTS audio saved: %s (%s bytes, provider: %s)", file_str, f"{file_size:,}", provider)

        # 构建带 MEDIA 标签的响应以进行平台传递
        media_tag = f"MEDIA:{file_str}"
        if voice_compatible:
            media_tag = f"[[audio_as_voice]]\n{media_tag}"

        return json.dumps({
            "success": True,
            "file_path": file_str,
            "media_tag": media_tag,
            "provider": provider,
            "voice_compatible": voice_compatible,
        }, ensure_ascii=False)

    except ValueError as e:
        # 配置错误（缺少 API 密钥等）
        error_msg = f"TTS configuration error ({provider}): {e}"
        logger.error("%s", error_msg)
        return tool_error(error_msg, success=False)
    except FileNotFoundError as e:
        # 缺少依赖项或文件
        error_msg = f"TTS dependency missing ({provider}): {e}"
        logger.error("%s", error_msg, exc_info=True)
        return tool_error(error_msg, success=False)
    except Exception as e:
        # 意外错误
        error_msg = f"TTS generation failed ({provider}): {e}"
        logger.error("%s", error_msg, exc_info=True)
        return tool_error(error_msg, success=False)


# ===========================================================================
# 需求检查
# ===========================================================================
def check_tts_requirements() -> bool:
    """
    检查是否至少有一个 TTS 提供者可用。

    Edge TTS 不需要 API 密钥且是默认的，所以如果包已安装，TTS 就可用。

    返回:
        bool: 如果至少有一个提供者可以工作则为 True。
    """
    try:
        _import_edge_tts()
        return True
    except ImportError:
        pass
    try:
        _import_elevenlabs()
        if os.getenv("ELEVENLABS_API_KEY"):
            return True
    except ImportError:
        pass
    try:
        _import_openai_client()
        if _has_openai_audio_backend():
            return True
    except ImportError:
        pass
    if os.getenv("MINIMAX_API_KEY"):
        return True
    if _check_neutts_available():
        return True
    return False


def _resolve_openai_audio_client_config() -> tuple[str, str]:
    """返回直接 OpenAI 音频配置或托管网关回退。"""
    direct_api_key = resolve_openai_audio_api_key()
    if direct_api_key:
        return direct_api_key, DEFAULT_OPENAI_BASE_URL

    managed_gateway = resolve_managed_tool_gateway("openai-audio")
    if managed_gateway is None:
        message = "Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set"
        if managed_nous_tools_enabled():
            message += ", and the managed OpenAI audio gateway is unavailable"
        raise ValueError(message)

    return managed_gateway.nous_user_token, urljoin(
        f"{managed_gateway.gateway_origin.rstrip('/')}/", "v1"
    )


def _has_openai_audio_backend() -> bool:
    """当 OpenAI 音频可以使用直接凭证或托管网关时返回 True。"""
    return bool(resolve_openai_audio_api_key() or resolve_managed_tool_gateway("openai-audio"))


# ===========================================================================
# 流式 TTS：ElevenLabs 的逐句管道
# ===========================================================================
# 句子边界模式：标点符号后跟空格或换行符
_SENTENCE_BOUNDARY_RE = re.compile(r'(?<=[.!?])(?:\s|\n)|(?:\n\n)')

# Markdown 剥离模式（与 cli.py _voice_speak_response 相同）
_MD_CODE_BLOCK = re.compile(r'```[\s\S]*?```')
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MD_URL = re.compile(r'https?://\S+')
_MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
_MD_ITALIC = re.compile(r'\*(.+?)\*')
_MD_INLINE_CODE = re.compile(r'`(.+?)`')
_MD_HEADER = re.compile(r'^#+\s*', flags=re.MULTILINE)
_MD_LIST_ITEM = re.compile(r'^\s*[-*]\s+', flags=re.MULTILINE)
_MD_HR = re.compile(r'---+')
_MD_EXCESS_NL = re.compile(r'\n{3,}')


def _strip_markdown_for_tts(text: str) -> str:
    """Remove markdown formatting that shouldn't be spoken aloud."""
    text = _MD_CODE_BLOCK.sub(' ', text)
    text = _MD_LINK.sub(r'\1', text)
    text = _MD_URL.sub('', text)
    text = _MD_BOLD.sub(r'\1', text)
    text = _MD_ITALIC.sub(r'\1', text)
    text = _MD_INLINE_CODE.sub(r'\1', text)
    text = _MD_HEADER.sub('', text)
    text = _MD_LIST_ITEM.sub('', text)
    text = _MD_HR.sub('', text)
    text = _MD_EXCESS_NL.sub('\n\n', text)
    return text.strip()


def stream_tts_to_speaker(
    text_queue: queue.Queue,
    stop_event: threading.Event,
    tts_done_event: threading.Event,
    display_callback: Optional[Callable[[str], None]] = None,
):
    """Consume text deltas from *text_queue*, buffer them into sentences,
    and stream each sentence through ElevenLabs TTS to the speaker in
    real-time.

    Protocol:
        * The producer puts ``str`` deltas onto *text_queue*.
        * A ``None`` sentinel signals end-of-text (flush remaining buffer).
        * *stop_event* can be set to abort early (e.g. user interrupt).
        * *tts_done_event* is **set** in the ``finally`` block so callers
          waiting on it (continuous voice mode) know playback is finished.
    """
    tts_done_event.clear()

    try:
        # --- TTS client setup (optional -- display_callback works without it) ---
        client = None
        output_stream = None
        voice_id = DEFAULT_ELEVENLABS_VOICE_ID
        model_id = DEFAULT_ELEVENLABS_STREAMING_MODEL_ID

        tts_config = _load_tts_config()
        el_config = tts_config.get("elevenlabs", {})
        voice_id = el_config.get("voice_id", voice_id)
        model_id = el_config.get("streaming_model_id",
                                 el_config.get("model_id", model_id))

        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            logger.warning("ELEVENLABS_API_KEY not set; streaming TTS audio disabled")
        else:
            try:
                ElevenLabs = _import_elevenlabs()
                client = ElevenLabs(api_key=api_key)
            except ImportError:
                logger.warning("elevenlabs package not installed; streaming TTS disabled")

            # Open a single sounddevice output stream for the lifetime of
            # this function.  ElevenLabs pcm_24000 produces signed 16-bit
            # little-endian mono PCM at 24 kHz.
            if client is not None:
                try:
                    sd = _import_sounddevice()
                    output_stream = sd.OutputStream(
                        samplerate=24000, channels=1, dtype="int16",
                    )
                    output_stream.start()
                except (ImportError, OSError) as exc:
                    logger.debug("sounddevice not available: %s", exc)
                    output_stream = None
                except Exception as exc:
                    logger.warning("sounddevice OutputStream failed: %s", exc)
                    output_stream = None

        sentence_buf = ""
        min_sentence_len = 20
        long_flush_len = 100
        queue_timeout = 0.5
        _spoken_sentences: list[str] = []  # track spoken sentences to skip duplicates
        # Regex to strip complete <think>...</think> blocks from buffer
        _think_block_re = re.compile(r'<think[\s>].*?</think>', flags=re.DOTALL)

        def _speak_sentence(sentence: str):
            """显示句子并可选地生成和播放音频。"""
            if stop_event.is_set():
                return
            cleaned = _strip_markdown_for_tts(sentence).strip()
            if not cleaned:
                return
            # 跳过重复/近似重复的句子（LLM 重复）
            cleaned_lower = cleaned.lower().rstrip(".!,")
            for prev in _spoken_sentences:
                if prev.lower().rstrip(".!,") == cleaned_lower:
                    return
            _spoken_sentences.append(cleaned)
            # 在 TTS 处理前在屏幕上显示原始句子
            if display_callback is not None:
                display_callback(sentence)
            # 如果没有可用的 TTS 客户端则跳过音频生成
            if client is None:
                return
            # 截断非常长的句子
            if len(cleaned) > MAX_TEXT_LENGTH:
                cleaned = cleaned[:MAX_TEXT_LENGTH]
            try:
                audio_iter = client.text_to_speech.convert(
                    text=cleaned,
                    voice_id=voice_id,
                    model_id=model_id,
                    output_format="pcm_24000",
                )
                if output_stream is not None:
                    for chunk in audio_iter:
                        if stop_event.is_set():
                            break
                        import numpy as _np
                        audio_array = _np.frombuffer(chunk, dtype=_np.int16)
                        output_stream.write(audio_array.reshape(-1, 1))
                else:
                    # 回退：将块写入临时文件并通过系统播放器播放
                    _play_via_tempfile(audio_iter, stop_event)
            except Exception as exc:
                logger.warning("Streaming TTS sentence failed: %s", exc)

        def _play_via_tempfile(audio_iter, stop_evt):
            """将 PCM 块写入临时 WAV 文件并播放。"""
            tmp_path = None
            try:
                import wave
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16 位
                    wf.setframerate(24000)
                    for chunk in audio_iter:
                        if stop_evt.is_set():
                            break
                        wf.writeframes(chunk)
                from tools.voice_mode import play_audio_file
                play_audio_file(tmp_path)
            except Exception as exc:
                logger.warning("Temp-file TTS fallback failed: %s", exc)
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        while not stop_event.is_set():
            # 从队列读取下一个增量
            try:
                delta = text_queue.get(timeout=queue_timeout)
            except queue.Empty:
                # 超时：如果我们已积累了一个长的缓冲区，则刷新它
                if len(sentence_buf) > long_flush_len:
                    _speak_sentence(sentence_buf)
                    sentence_buf = ""
                continue

            if delta is None:
                # 文本结束标记：剥离任何剩余的 think 块，刷新
                sentence_buf = _think_block_re.sub('', sentence_buf)
                if sentence_buf.strip():
                    _speak_sentence(sentence_buf)
                break

            sentence_buf += delta

            # --- Think 块过滤 ---
            # 从缓冲区剥离完整的 <think>...</think> 块。
            # 即使标签跨越多个增量也能正常工作。
            sentence_buf = _think_block_re.sub('', sentence_buf)

            # 如果末尾有一个不完整的 <think 标签，则等待更多数据
            # 然后再提取句子（结束标签可能下一步到达）。
            if '<think' in sentence_buf and '</think>' not in sentence_buf:
                continue

            # Check for sentence boundaries
            while True:
                m = _SENTENCE_BOUNDARY_RE.search(sentence_buf)
                if m is None:
                    break
                end_pos = m.end()
                sentence = sentence_buf[:end_pos]
                sentence_buf = sentence_buf[end_pos:]
                # 将短片段合并到下一个句子
                if len(sentence.strip()) < min_sentence_len:
                    sentence_buf = sentence + sentence_buf
                    break
                _speak_sentence(sentence)

        # 耗尽队列中任何剩余的项目
        while True:
            try:
                text_queue.get_nowait()
            except queue.Empty:
                break

        # output_stream 在下面的 finally 块中关闭

    except Exception as exc:
        logger.warning("Streaming TTS pipeline error: %s", exc)
    finally:
        # 始终关闭音频输出流以避免锁定设备
        if output_stream is not None:
            try:
                output_stream.stop()
                output_stream.close()
            except Exception:
                pass
        tts_done_event.set()


# ===========================================================================
# Main -- 快速诊断
# ===========================================================================
if __name__ == "__main__":
    print("🔊 Text-to-Speech Tool Module")
    print("=" * 50)

    def _check(importer, label):
        try:
            importer()
            return True
        except ImportError:
            return False

    print("\nProvider availability:")
    print(f"  Edge TTS:   {'installed' if _check(_import_edge_tts, 'edge') else 'not installed (pip install edge-tts)'}")
    print(f"  ElevenLabs: {'installed' if _check(_import_elevenlabs, 'el') else 'not installed (pip install elevenlabs)'}")
    print(f"    API Key:  {'set' if os.getenv('ELEVENLABS_API_KEY') else 'not set'}")
    print(f"  OpenAI:     {'installed' if _check(_import_openai_client, 'oai') else 'not installed'}")
    print(
        "    API Key:  "
        f"{'set' if resolve_openai_audio_api_key() else 'not set (VOICE_TOOLS_OPENAI_KEY or OPENAI_API_KEY)'}"
    )
    print(f"  MiniMax:    {'API key set' if os.getenv('MINIMAX_API_KEY') else 'not set (MINIMAX_API_KEY)'}")
    print(f"  ffmpeg:     {'✅ found' if _has_ffmpeg() else '❌ not found (needed for Telegram Opus)'}")
    print(f"\n  Output dir: {DEFAULT_OUTPUT_DIR}")

    config = _load_tts_config()
    provider = _get_provider(config)
    print(f"  Configured provider: {provider}")


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

TTS_SCHEMA = {
    "name": "text_to_speech",
    "description": "Convert text to speech audio. Returns a MEDIA: path that the platform delivers as a voice message. On Telegram it plays as a voice bubble, on Discord/WhatsApp as an audio attachment. In CLI mode, saves to ~/voice-memos/. Voice and provider are user-configured, not model-selected.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to convert to speech. Keep under 4000 characters."
            },
            "output_path": {
                "type": "string",
                "description": "Optional custom file path to save the audio. Defaults to ~/.kclaw/audio_cache/<timestamp>.mp3"
            }
        },
        "required": ["text"]
    }
}

registry.register(
    name="text_to_speech",
    toolset="tts",
    schema=TTS_SCHEMA,
    handler=lambda args, **kw: text_to_speech_tool(
        text=args.get("text", ""),
        output_path=args.get("output_path")),
    check_fn=check_tts_requirements,
    emoji="🔊",
)
