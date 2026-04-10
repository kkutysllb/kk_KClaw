"""Voice Mode -- Push-to-talk audio recording and playback for the CLI.

Provides audio capture via sounddevice, WAV encoding via stdlib wave,
STT dispatch via tools.transcription_tools, and TTS playback via
sounddevice or system audio players.

Dependencies (optional):
    pip install sounddevice numpy
    or: pip install kclaw[voice]
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟音频导入 -- 不在模块级别导入，以避免在无头环境（SSH、Docker、WSL、无 PortAudio）中崩溃。
# ---------------------------------------------------------------------------

def _import_audio():
    """延迟导入 sounddevice 和 numpy。返回 (sd, np)。

    如果库不可用（如无头服务器上缺少 PortAudio），则抛出 ImportError 或 OSError。
    """
    import sounddevice as sd
    import numpy as np
    return sd, np


def _audio_available() -> bool:
    """如果可以导入音频库则返回 True。"""
    try:
        _import_audio()
        return True
    except (ImportError, OSError):
        return False


def detect_audio_environment() -> dict:
    """检测当前环境是否支持音频 I/O。

    返回包含 'available'（bool）、'warnings'（阻止语音模式的硬失败原因列表）
    和 'notices'（不阻止语音模式的信息性消息列表）的字典。
    """
    warnings = []   # 硬失败：这些会阻止语音模式
    notices = []     # 信息性：记录但不阻止

    # SSH 检测
    if any(os.environ.get(v) for v in ('SSH_CLIENT', 'SSH_TTY', 'SSH_CONNECTION')):
        warnings.append("Running over SSH -- no audio devices available")

    # Docker 检测
    if os.path.exists('/.dockerenv'):
        warnings.append("Running inside Docker container -- no audio devices")

    # WSL 检测 — PulseAudio 桥接使音频在 WSL 中工作。
    # 仅在未配置 PULSE_SERVER 时阻止。
    try:
        with open('/proc/version', 'r') as f:
            if 'microsoft' in f.read().lower():
                if os.environ.get('PULSE_SERVER'):
                    notices.append("Running in WSL with PulseAudio bridge")
                else:
                    warnings.append(
                        "Running in WSL -- audio requires PulseAudio bridge.\n"
                        "  1. Set PULSE_SERVER=unix:/mnt/wslg/PulseServer\n"
                        "  2. Create ~/.asoundrc pointing ALSA at PulseAudio\n"
                        "  3. Verify with: arecord -d 3 /tmp/test.wav && aplay /tmp/test.wav"
                    )
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # 检查音频库
    try:
        sd, _ = _import_audio()
        try:
            devices = sd.query_devices()
            if not devices:
                warnings.append("No audio input/output devices detected")
        except Exception:
            # 在带 PulseAudio 的 WSL 中，设备查询可能失败，即使
            # 录制/播放正常工作。如果设置了 PULSE_SERVER 则不阻止。
            if os.environ.get('PULSE_SERVER'):
                notices.append("Audio device query failed but PULSE_SERVER is set -- continuing")
            else:
                warnings.append("Audio subsystem error (PortAudio cannot query devices)")
    except ImportError:
        warnings.append("Audio libraries not installed (pip install sounddevice numpy)")
    except OSError:
        warnings.append(
            "PortAudio system library not found -- install it first:\n"
            "  Linux:  sudo apt-get install libportaudio2\n"
            "  macOS:  brew install portaudio\n"
            "Then retry /voice on."
        )

    return {
        "available": not warnings,
        "warnings": warnings,
        "notices": notices,
    }

# ---------------------------------------------------------------------------
# 录制参数
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000  # Whisper native rate
CHANNELS = 1  # Mono
DTYPE = "int16"  # 16-bit PCM
SAMPLE_WIDTH = 2  # bytes per sample (int16)
MAX_RECORDING_SECONDS = 120  # Safety cap

# 静音检测默认值
SILENCE_RMS_THRESHOLD = 200  # RMS below this = silence (int16 range 0-32767)
SILENCE_DURATION_SECONDS = 3.0  # Seconds of continuous silence before auto-stop

# 语音录制的临时目录
_TEMP_DIR = os.path.join(tempfile.gettempdir(), "kclaw_voice")


# ============================================================================
# 音频提示（蜂鸣音）
# ============================================================================
def play_beep(frequency: int = 880, duration: float = 0.12, count: int = 1) -> None:
    """使用 numpy + sounddevice 播放短蜂鸣音。

    参数:
        frequency: 音调频率（Hz，默认 880 = A5）。
        duration: 每次蜂鸣的持续时间（秒）。
        count: 播放的蜂鸣次数（之间有短暂间隔）。
    """
    try:
        sd, np = _import_audio()
    except (ImportError, OSError):
        return
    try:
        gap = 0.06  # 蜂鸣之间的间隔（秒）
        samples_per_beep = int(SAMPLE_RATE * duration)
        samples_per_gap = int(SAMPLE_RATE * gap)

        parts = []
        for i in range(count):
            t = np.linspace(0, duration, samples_per_beep, endpoint=False)
            # 应用淡入/淡出以避免咔嗒声
            tone = np.sin(2 * np.pi * frequency * t)
            fade_len = min(int(SAMPLE_RATE * 0.01), samples_per_beep // 4)
            tone[:fade_len] *= np.linspace(0, 1, fade_len)
            tone[-fade_len:] *= np.linspace(1, 0, fade_len)
            parts.append((tone * 0.3 * 32767).astype(np.int16))
            if i < count - 1:
                parts.append(np.zeros(samples_per_gap, dtype=np.int16))

        audio = np.concatenate(parts)
        sd.play(audio, samplerate=SAMPLE_RATE)
        # sd.wait() 调用 Event.wait() 且无超时 — 如果音频设备停滞会永远挂起。
        # 使用 2 秒上限轮询并强制停止。
        deadline = time.monotonic() + 2.0
        while sd.get_stream() and sd.get_stream().active and time.monotonic() < deadline:
            time.sleep(0.01)
        sd.stop()
    except Exception as e:
        logger.debug("Beep playback failed: %s", e)


# ============================================================================
# AudioRecorder（音频录制器）
# ============================================================================
class AudioRecorder:
    """使用 sounddevice.InputStream 的线程安全音频录制器。

    用法::

        recorder = AudioRecorder()
        recorder.start(on_silence_stop=my_callback)
        # ... 用户说话 ...
        wav_path = recorder.stop()   # 返回 WAV 文件路径
        # 或
        recorder.cancel()            # 丢弃而不保存

    如果提供了 ``on_silence_stop``，当用户沉默 ``silence_duration`` 秒后，
    录制会自动停止并调用回调函数。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Any = None
        self._frames: List[Any] = []
        self._recording = False
        self._start_time: float = 0.0
        # 静音检测状态
        self._has_spoken = False
        self._speech_start: float = 0.0  # 语音尝试开始的时间
        self._dip_start: float = 0.0  # 当前低于阈值开始下降的时间
        self._min_speech_duration: float = 0.3  # 确认语音所需的语音秒数
        self._max_dip_tolerance: float = 0.3  # 重置语音前的最大下降持续时间
        self._silence_start: float = 0.0
        self._resume_start: float = 0.0  # 跟踪静音开始后的持续语音
        self._resume_dip_start: float = 0.0  # 恢复检测的下降容差跟踪器
        self._on_silence_stop = None
        self._silence_threshold: int = SILENCE_RMS_THRESHOLD
        self._silence_duration: float = SILENCE_DURATION_SECONDS
        self._max_wait: float = 15.0  # 自动停止前等待语音的最大秒数
        # 录制期间看到的峰值 RMS（用于 stop() 中的语音存在检查）
        self._peak_rms: int = 0
        # 实时音频电平（供 UI 读取以进行视觉反馈）
        self._current_rms: int = 0

    # -- 公共属性 ---------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_seconds(self) -> float:
        if not self._recording:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def current_rms(self) -> int:
        """当前音频输入 RMS 电平（0-32767）。每个音频块更新。"""
        return self._current_rms

    # -- 公共方法 ------------------------------------------------------

    def _ensure_stream(self) -> None:
        """创建一次音频 InputStream 并保持其活动状态。

        流在录制器的整个生命周期内保持打开状态。在录制之间，
        回调函数只是丢弃音频块（``_recording`` 为 ``False``）。
        这样可以避免 CoreAudio 错误，即在 macOS 上关闭和
        重新打开 ``InputStream`` 会无限期挂起。
        """
        if self._stream is not None:
            return  # already alive

        sd, np = _import_audio()

        def _callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                logger.debug("sounddevice status: %s", status)
            # 当不录制时流处于空闲状态 — 丢弃音频。
            if not self._recording:
                return
            self._frames.append(indata.copy())

            # 计算 RMS 以进行电平显示和静音检测
            rms = int(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
            self._current_rms = rms
            if rms > self._peak_rms:
                self._peak_rms = rms

            # 静音检测
            if self._on_silence_stop is not None:
                now = time.monotonic()
                elapsed = now - self._start_time

                if rms > self._silence_threshold:
                    # 音频高于阈值 — 这是语音（或噪音）。
                    self._dip_start = 0.0  # Reset dip tracker
                    if self._speech_start == 0.0:
                        self._speech_start = now
                    elif not self._has_spoken and now - self._speech_start >= self._min_speech_duration:
                        self._has_spoken = True
                        logger.debug("Speech confirmed (%.2fs above threshold)",
                                     now - self._speech_start)
                    # 确认语音后，仅在语音持续（>0.3s 高于阈值）时重置静音计时器。
                    # 环境噪音的短暂峰值不应重置计时器。
                    if not self._has_spoken:
                        self._silence_start = 0.0
                    else:
                        # 使用下降容差跟踪恢复的语音。
                        # 语音中短暂低于阈值是正常的，
                        # 所以我们模仿初始语音检测模式：
                        # 开始跟踪，容忍短暂下降，0.3 秒后确认。
                        self._resume_dip_start = 0.0  # Above threshold — no dip
                        if self._resume_start == 0.0:
                            self._resume_start = now
                        elif now - self._resume_start >= self._min_speech_duration:
                            self._silence_start = 0.0
                            self._resume_start = 0.0
                elif self._has_spoken:
                    # 语音确认后低于阈值。
                    # 在重置恢复跟踪器之前使用下降容差 —
                    # 自然语音有短暂的低于阈值下降。
                    if self._resume_start > 0:
                        if self._resume_dip_start == 0.0:
                            self._resume_dip_start = now
                        elif now - self._resume_dip_start >= self._max_dip_tolerance:
                            # Sustained dip — user actually stopped speaking
                            self._resume_start = 0.0
                            self._resume_dip_start = 0.0
                elif self._speech_start > 0:
                    # 我们处于语音尝试中但 RMS 下降了。
                    # 容忍短暂下降（音节之间的微停顿）。
                    if self._dip_start == 0.0:
                        self._dip_start = now
                    elif now - self._dip_start >= self._max_dip_tolerance:
                        # 下降持续时间过长 — 真正的静音，重置
                        logger.debug("Speech attempt reset (dip lasted %.2fs)",
                                     now - self._dip_start)
                        self._speech_start = 0.0
                        self._dip_start = 0.0

                # 触发静音回调的条件：
                # 1. 用户说话后沉默 silence_duration，或
                # 2. 在 max_wait 秒内完全未检测到语音
                should_fire = False
                if self._has_spoken and rms <= self._silence_threshold:
                    # 用户正在说话，现在沉默了
                    if self._silence_start == 0.0:
                        self._silence_start = now
                    elif now - self._silence_start >= self._silence_duration:
                        logger.info("Silence detected (%.1fs), auto-stopping",
                                    self._silence_duration)
                        should_fire = True
                elif not self._has_spoken and elapsed >= self._max_wait:
                    logger.info("No speech within %.0fs, auto-stopping",
                                self._max_wait)
                    should_fire = True

                if should_fire:
                    with self._lock:
                        cb = self._on_silence_stop
                        self._on_silence_stop = None  # fire only once
                    if cb:
                        def _safe_cb():
                            try:
                                cb()
                            except Exception as e:
                                logger.error("Silence callback failed: %s", e, exc_info=True)
                        threading.Thread(target=_safe_cb, daemon=True).start()

        # 创建流 — 可能在 CoreAudio 上阻塞（仅第一次调用）。
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=_callback,
            )
            stream.start()
        except Exception as e:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise RuntimeError(
                f"Failed to open audio input stream: {e}. "
                "Check that a microphone is connected and accessible."
            ) from e
        self._stream = stream

    def start(self, on_silence_stop=None) -> None:
        """开始从默认输入设备捕获音频。

        底层 InputStream 创建一次并在录制之间保持活动状态。
        后续调用只是重置检测状态并通过 ``_recording`` 切换帧收集。

        参数:
            on_silence_stop: 可选回调，在语音后检测到静音时调用（在守护线程中）。
                回调不接受任何参数。使用此回调可自动停止录制并触发转录。

        如果 sounddevice/numpy 未安装或已在进行录制，则抛出 ``RuntimeError``。
        """
        try:
            _import_audio()
        except (ImportError, OSError) as e:
            raise RuntimeError(
                "Voice mode requires sounddevice and numpy.\n"
                "Install with: pip install sounddevice numpy\n"
                "Or: pip install kclaw[voice]"
            ) from e

        with self._lock:
            if self._recording:
                return  # 已在录制

            self._frames = []
            self._start_time = time.monotonic()
            self._has_spoken = False
            self._speech_start = 0.0
            self._dip_start = 0.0
            self._silence_start = 0.0
            self._resume_start = 0.0
            self._resume_dip_start = 0.0
            self._peak_rms = 0
            self._current_rms = 0
            self._on_silence_stop = on_silence_stop

        # 确保持久流处于活动状态（首次调用后无操作）。
        self._ensure_stream()

        with self._lock:
            self._recording = True
        logger.info("Voice recording started (rate=%d, channels=%d)", SAMPLE_RATE, CHANNELS)

    def _close_stream_with_timeout(self, timeout: float = 3.0) -> None:
        """关闭音频流并设置超时以防止 CoreAudio 挂起。"""
        if self._stream is None:
            return

        stream = self._stream
        self._stream = None

        def _do_close():
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        t = threading.Thread(target=_do_close, daemon=True)
        t.start()
        # 以短间隔轮询，以免阻塞 Ctrl+C
        deadline = __import__("time").monotonic() + timeout
        while t.is_alive() and __import__("time").monotonic() < deadline:
            t.join(timeout=0.1)
        if t.is_alive():
            logger.warning("Audio stream close timed out after %.1fs — forcing ahead", timeout)

    def stop(self) -> Optional[str]:
        """停止录制并将捕获的音频写入 WAV 文件。

        底层流保持活动状态以供重用 — 仅停止帧收集。

        返回:
            WAV 文件的路径，如果未捕获音频则返回 ``None``。
        """
        with self._lock:
            if not self._recording:
                return None

            self._recording = False
            self._current_rms = 0
            # 流保持活动 — 无需关闭。

            if not self._frames:
                return None

            # Concatenate frames and write WAV
            _, np = _import_audio()
            audio_data = np.concatenate(self._frames, axis=0)
            self._frames = []

            elapsed = time.monotonic() - self._start_time
            logger.info("Voice recording stopped (%.1fs, %d samples)", elapsed, len(audio_data))

            # 跳过非常短的录制（< 0.3 秒音频）
            min_samples = int(SAMPLE_RATE * 0.3)
            if len(audio_data) < min_samples:
                logger.debug("Recording too short (%d samples), discarding", len(audio_data))
                return None

            # 使用峰值 RMS 跳过静音录制（不是整体平均值，
            # 会被录制结尾的静音稀释）。
            if self._peak_rms < SILENCE_RMS_THRESHOLD:
                logger.info("Recording too quiet (peak RMS=%d < %d), discarding",
                            self._peak_rms, SILENCE_RMS_THRESHOLD)
                return None

            return self._write_wav(audio_data)

    def cancel(self) -> None:
        """停止录制并丢弃所有捕获的音频。

        底层流保持活动状态以供重用。
        """
        with self._lock:
            self._recording = False
            self._frames = []
            self._on_silence_stop = None
            self._current_rms = 0
        logger.info("Voice recording cancelled")

    def shutdown(self) -> None:
        """释放音频流。禁用语音模式时调用。"""
        with self._lock:
            self._recording = False
            self._frames = []
            self._on_silence_stop = None
        # 在锁外关闭流以避免与音频回调死锁
        self._close_stream_with_timeout()
        logger.info("AudioRecorder shut down")

    # -- 私有辅助方法 -----------------------------------------------------

    @staticmethod
    def _write_wav(audio_data) -> str:
        """将 numpy int16 音频数据写入 WAV 文件。

        返回文件路径。
        """
        os.makedirs(_TEMP_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        wav_path = os.path.join(_TEMP_DIR, f"recording_{timestamp}.wav")

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

        file_size = os.path.getsize(wav_path)
        logger.info("WAV written: %s (%d bytes)", wav_path, file_size)
        return wav_path


# ============================================================================
# Whisper 幻觉过滤器
# ============================================================================
# Whisper commonly hallucinates these phrases on silent/near-silent audio.
WHISPER_HALLUCINATIONS = {
    "thank you.",
    "thank you",
    "thanks for watching.",
    "thanks for watching",
    "subscribe to my channel.",
    "subscribe to my channel",
    "like and subscribe.",
    "like and subscribe",
    "please subscribe.",
    "please subscribe",
    "thank you for watching.",
    "thank you for watching",
    "bye.",
    "bye",
    "you",
    "the end.",
    "the end",
    # Non-English hallucinations (common on silence)
    "продолжение следует",
    "продолжение следует...",
    "sous-titres",
    "sous-titres réalisés par la communauté d'amara.org",
    "sottotitoli creati dalla comunità amara.org",
    "untertitel von stephanie geiges",
    "amara.org",
    "www.mooji.org",
    "ご視聴ありがとうございました",
}

# 重复幻觉的正则表达式模式（例如 "Thank you. Thank you. Thank you."）
_HALLUCINATION_REPEAT_RE = re.compile(
    r'^(?:thank you|thanks|bye|you|ok|okay|the end|\.|\s|,|!)+$',
    flags=re.IGNORECASE,
)


def is_whisper_hallucination(transcript: str) -> bool:
    """检查转录是否是已知的 Whisper 静音幻觉。"""
    cleaned = transcript.strip().lower()
    if not cleaned:
        return True
    # 与已知短语精确匹配
    if cleaned.rstrip('.!') in WHISPER_HALLUCINATIONS or cleaned in WHISPER_HALLUCINATIONS:
        return True
    # 重复模式（例如 "Thank you. Thank you. Thank you. you"）
    if _HALLUCINATION_REPEAT_RE.match(cleaned):
        return True
    return False


# ============================================================================
# STT 分发
# ============================================================================
def transcribe_recording(wav_path: str, model: Optional[str] = None) -> Dict[str, Any]:
    """使用现有的 Whisper 管道转录 WAV 录制。

    委托给 ``tools.transcription_tools.transcribe_audio()``。
    过滤掉已知的 Whisper 静音幻觉。

    参数:
        wav_path: WAV 文件的路径。
        model: Whisper 模型名称（默认：从配置或 ``whisper-1``）。

    返回:
        包含 ``success``、``transcript`` 和可选 ``error`` 的字典。
    """
    from tools.transcription_tools import transcribe_audio

    result = transcribe_audio(wav_path, model=model)

    # 过滤掉 Whisper 幻觉（常见于静音/接近静音的音频）
    if result.get("success") and is_whisper_hallucination(result.get("transcript", "")):
        logger.info("Filtered Whisper hallucination: %r", result["transcript"])
        return {"success": True, "transcript": "", "filtered": True}

    return result


# ============================================================================
# 音频播放（可中断）
# ============================================================================

# 活动播放进程的全局引用，以便可以中断。
_active_playback: Optional[subprocess.Popen] = None
_playback_lock = threading.Lock()


def stop_playback() -> None:
    """中断当前正在播放的音频（如果有）。"""
    global _active_playback
    with _playback_lock:
        proc = _active_playback
        _active_playback = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            logger.info("Audio playback interrupted")
        except Exception:
            pass
    # 如果 sounddevice 播放处于活动状态也停止它
    try:
        sd, _ = _import_audio()
        sd.stop()
    except Exception:
        pass


def play_audio_file(file_path: str) -> bool:
    """通过默认输出设备播放音频文件。

    策略：
    1. 可用时通过 ``sounddevice.play()`` 播放 WAV 文件。
    2. 系统命令：``afplay``（macOS）、``ffplay``（跨平台）、
       ``aplay``（Linux ALSA）。

    可以通过调用 ``stop_playback()`` 中断播放。

    返回:
        如果播放成功则返回 ``True``，否则返回 ``False``。
    """
    global _active_playback

    if not os.path.isfile(file_path):
        logger.warning("Audio file not found: %s", file_path)
        return False

    # 尝试使用 sounddevice 播放 WAV 文件
    if file_path.endswith(".wav"):
        try:
            sd, np = _import_audio()
            with wave.open(file_path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                audio_data = np.frombuffer(frames, dtype=np.int16)
                sample_rate = wf.getframerate()

            sd.play(audio_data, samplerate=sample_rate)
            # sd.wait() calls Event.wait() without timeout — hangs forever if
            # the audio device stalls.  Poll with a ceiling and force-stop.
            duration_secs = len(audio_data) / sample_rate
            deadline = time.monotonic() + duration_secs + 2.0
            while sd.get_stream() and sd.get_stream().active and time.monotonic() < deadline:
                time.sleep(0.01)
            sd.stop()
            return True
        except (ImportError, OSError):
            pass  # 音频库不可用，回退到系统播放器
        except Exception as e:
            logger.debug("sounddevice playback failed: %s", e)

    # 回退到系统音频播放器（使用 Popen 以便可中断）
    system = platform.system()
    players = []

    if system == "Darwin":
        players.append(["afplay", file_path])
    players.append(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", file_path])
    if system == "Linux":
        players.append(["aplay", "-q", file_path])

    for cmd in players:
        exe = shutil.which(cmd[0])
        if exe:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with _playback_lock:
                    _active_playback = proc
                proc.wait(timeout=300)
                with _playback_lock:
                    _active_playback = None
                return True
            except subprocess.TimeoutExpired:
                logger.warning("System player %s timed out, killing process", cmd[0])
                proc.kill()
                proc.wait()
                with _playback_lock:
                    _active_playback = None
            except Exception as e:
                logger.debug("System player %s failed: %s", cmd[0], e)
                with _playback_lock:
                    _active_playback = None

    logger.warning("No audio player available for %s", file_path)
    return False


# ============================================================================
# 需求检查
# ============================================================================
def check_voice_requirements() -> Dict[str, Any]:
    """检查是否满足所有语音模式需求。

    返回:
        包含 ``available``、``audio_available``、``stt_available``、
        ``missing_packages`` 和 ``details`` 的字典。
    """
    # 确定 STT 提供者可用性
    from tools.transcription_tools import _get_provider, _load_stt_config, is_stt_enabled
    stt_config = _load_stt_config()
    stt_enabled = is_stt_enabled(stt_config)
    stt_provider = _get_provider(stt_config)
    stt_available = stt_enabled and stt_provider != "none"

    missing: List[str] = []
    has_audio = _audio_available()

    if not has_audio:
        missing.extend(["sounddevice", "numpy"])

    # 环境检测
    env_check = detect_audio_environment()

    available = has_audio and stt_available and env_check["available"]
    details_parts = []

    if has_audio:
        details_parts.append("Audio capture: OK")
    else:
        details_parts.append("Audio capture: MISSING (pip install sounddevice numpy)")

    if not stt_enabled:
        details_parts.append("STT provider: DISABLED in config (stt.enabled: false)")
    elif stt_provider == "local":
        details_parts.append("STT provider: OK (local faster-whisper)")
    elif stt_provider == "groq":
        details_parts.append("STT provider: OK (Groq)")
    elif stt_provider == "openai":
        details_parts.append("STT provider: OK (OpenAI)")
    else:
        details_parts.append(
            "STT provider: MISSING (pip install faster-whisper, "
            "or set GROQ_API_KEY / VOICE_TOOLS_OPENAI_KEY)"
        )

    for warning in env_check["warnings"]:
        details_parts.append(f"Environment: {warning}")
    for notice in env_check.get("notices", []):
        details_parts.append(f"Environment: {notice}")

    return {
        "available": available,
        "audio_available": has_audio,
        "stt_available": stt_available,
        "missing_packages": missing,
        "details": "\n".join(details_parts),
        "environment": env_check,
    }


# ============================================================================
# 临时文件清理
# ============================================================================
def cleanup_temp_recordings(max_age_seconds: int = 3600) -> int:
    """删除旧的临时语音录制文件。

    参数:
        max_age_seconds: 删除早于此时间的文件（默认：1 小时）。

    返回:
        删除的文件数。
    """
    if not os.path.isdir(_TEMP_DIR):
        return 0

    deleted = 0
    now = time.time()

    for entry in os.scandir(_TEMP_DIR):
        if entry.is_file() and entry.name.startswith("recording_") and entry.name.endswith(".wav"):
            try:
                age = now - entry.stat().st_mtime
                if age > max_age_seconds:
                    os.unlink(entry.path)
                    deleted += 1
            except OSError:
                pass

    if deleted:
        logger.debug("Cleaned up %d old voice recordings", deleted)
    return deleted
