"""macOS、Windows、Linux 和 WSL2 的剪贴板图像提取。

提供单个函数 `save_clipboard_image(dest)`，用于检查系统剪贴板中的
图像数据，将其保存为 PNG 到 *dest*，成功返回 True。
无需外部 Python 依赖 — 仅使用平台自带的或常见安装的 OS 级 CLI 工具。

平台支持：
  macOS   — osascript（始终可用），pngpaste（如已安装）
  Windows — PowerShell via .NET System.Windows.Forms.Clipboard
  WSL2    — powershell.exe via .NET System.Windows.Forms.Clipboard
  Linux   — wl-paste (Wayland), xclip (X11)
"""

import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 缓存 WSL 检测（每个进程检查一次）
_wsl_detected: bool | None = None


def save_clipboard_image(dest: Path) -> bool:
    """从系统剪贴板提取图像并保存为 PNG。

    如果找到并保存了图像则返回 True，否则返回 False。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        return _macos_save(dest)
    if sys.platform == "win32":
        return _windows_save(dest)
    return _linux_save(dest)


def has_clipboard_image() -> bool:
    """快速检查：剪贴板当前是否包含图像？

    比 save_clipboard_image 更轻量 — 不提取或写入任何内容。
    """
    if sys.platform == "darwin":
        return _macos_has_image()
    if sys.platform == "win32":
        return _windows_has_image()
    if _is_wsl():
        return _wsl_has_image()
    if os.environ.get("WAYLAND_DISPLAY"):
        return _wayland_has_image()
    return _xclip_has_image()


# ── macOS ────────────────────────────────────────────────────────────────

def _macos_save(dest: Path) -> bool:
    """优先尝试 pngpaste（快速，支持更多格式），回退到 osascript。"""
    return _macos_pngpaste(dest) or _macos_osascript(dest)


def _macos_has_image() -> bool:
    """检查 macOS 剪贴板是否包含图像数据。"""
    try:
        info = subprocess.run(
            ["osascript", "-e", "clipboard info"],
            capture_output=True, text=True, timeout=3,
        )
        return "«class PNGf»" in info.stdout or "«class TIFF»" in info.stdout
    except Exception:
        return False


def _macos_pngpaste(dest: Path) -> bool:
    """Use pngpaste (brew install pngpaste) — fastest, cleanest."""
    try:
        r = subprocess.run(
            ["pngpaste", str(dest)],
            capture_output=True, timeout=3,
        )
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
    except FileNotFoundError:
        pass  # pngpaste 未安装
    except Exception as e:
        logger.debug("pngpaste 失败: %s", e)
    return False


def _macos_osascript(dest: Path) -> bool:
    """使用 osascript 从剪贴板提取 PNG 数据（始终可用）。"""
    if not _macos_has_image():
        return False

    # Extract as PNG
    script = (
        'try\n'
        '  set imgData to the clipboard as «class PNGf»\n'
        f'  set f to open for access POSIX file "{dest}" with write permission\n'
        '  write imgData to f\n'
        '  close access f\n'
        'on error\n'
        '  return "fail"\n'
        'end try\n'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "fail" not in r.stdout and dest.exists() and dest.stat().st_size > 0:
            return True
    except Exception as e:
        logger.debug("osascript 剪贴板提取失败: %s", e)
    return False


# ── 共享 PowerShell 脚本（原生 Windows + WSL2） ─────────────────────

# .NET System.Windows.Forms.Clipboard — 用于原生 Windows (powershell)
# 和 WSL2 (powershell.exe) 路径。
_PS_CHECK_IMAGE = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "[System.Windows.Forms.Clipboard]::ContainsImage()"
)

_PS_EXTRACT_IMAGE = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "Add-Type -AssemblyName System.Drawing;"
    "$img = [System.Windows.Forms.Clipboard]::GetImage();"
    "if ($null -eq $img) { exit 1 }"
    "$ms = New-Object System.IO.MemoryStream;"
    "$img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);"
    "[System.Convert]::ToBase64String($ms.ToArray())"
)


# ── 原生 Windows ────────────────────────────────────────────────────────

# 原生 Windows 使用 ``powershell``（Windows PowerShell 5.1，始终存在）
# 或 ``pwsh``（PowerShell 7+，可选）。发现结果在每个进程中缓存。


def _find_powershell() -> str | None:
    """返回第一个可用的 PowerShell 可执行文件，或 None。"""
    for name in ("powershell", "pwsh"):
        try:
            r = subprocess.run(
                [name, "-NoProfile", "-NonInteractive", "-Command", "echo ok"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "ok" in r.stdout:
                return name
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


# 缓存解析后的 PowerShell 可执行文件（每个进程检查一次）
_ps_exe: str | None | bool = False  # False = not yet checked


def _get_ps_exe() -> str | None:
    global _ps_exe
    if _ps_exe is False:
        _ps_exe = _find_powershell()
    return _ps_exe


def _windows_has_image() -> bool:
    """检查 Windows 剪贴板是否包含图像。"""
    ps = _get_ps_exe()
    if ps is None:
        return False
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _PS_CHECK_IMAGE],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and "True" in r.stdout
    except Exception as e:
        logger.debug("Windows 剪贴板图像检查失败: %s", e)
    return False


def _windows_save(dest: Path) -> bool:
    """通过 PowerShell → base64 PNG 在原生 Windows 上提取剪贴板图像。"""
    ps = _get_ps_exe()
    if ps is None:
        logger.debug("未找到 PowerShell — Windows 剪贴板图像粘贴不可用")
        return False
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _PS_EXTRACT_IMAGE],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False

        b64_data = r.stdout.strip()
        if not b64_data:
            return False

        png_bytes = base64.b64decode(b64_data)
        dest.write_bytes(png_bytes)
        return dest.exists() and dest.stat().st_size > 0

    except Exception as e:
        logger.debug("Windows 剪贴板图像提取失败: %s", e)
        dest.unlink(missing_ok=True)
    return False


# ── Linux ────────────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    """检测是否在 WSL（1 或 2）中运行。"""
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r") as f:
            _wsl_detected = "microsoft" in f.read().lower()
    except Exception:
        _wsl_detected = False
    return _wsl_detected


def _linux_save(dest: Path) -> bool:
    """按优先级尝试剪贴板后端：WSL → Wayland → X11。"""
    if _is_wsl():
        if _wsl_save(dest):
            return True
        # 继续 — WSLg 可能安装了 wl-paste 或 xclip

    if os.environ.get("WAYLAND_DISPLAY"):
        if _wayland_save(dest):
            return True

    return _xclip_save(dest)


# ── WSL2 (powershell.exe) ────────────────────────────────────────────────
# 重用上面定义的 _PS_CHECK_IMAGE / _PS_EXTRACT_IMAGE。

def _wsl_has_image() -> bool:
    """检查 Windows 剪贴板是否有图像（通过 powershell.exe）。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _PS_CHECK_IMAGE],
            capture_output=True, text=True, timeout=8,
        )
        return r.returncode == 0 and "True" in r.stdout
    except FileNotFoundError:
        logger.debug("未找到 powershell.exe — WSL 剪贴板不可用")
    except Exception as e:
        logger.debug("WSL 剪贴板检查失败: %s", e)
    return False


def _wsl_save(dest: Path) -> bool:
    """通过 powershell.exe → base64 → 解码为 PNG 提取剪贴板图像。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _PS_EXTRACT_IMAGE],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False

        b64_data = r.stdout.strip()
        if not b64_data:
            return False

        png_bytes = base64.b64decode(b64_data)
        dest.write_bytes(png_bytes)
        return dest.exists() and dest.stat().st_size > 0

    except FileNotFoundError:
        logger.debug("未找到 powershell.exe — WSL 剪贴板不可用")
    except Exception as e:
        logger.debug("WSL 剪贴板提取失败: %s", e)
        dest.unlink(missing_ok=True)
    return False


# ── Wayland (wl-paste) ──────────────────────────────────────────────────

def _wayland_has_image() -> bool:
    """检查 Wayland 剪贴板是否有图像内容。"""
    try:
        r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0 and any(
            t.startswith("image/") for t in r.stdout.splitlines()
        )
    except FileNotFoundError:
        logger.debug("wl-paste 未安装 — Wayland 剪贴板不可用")
    except Exception:
        pass
    return False


def _wayland_save(dest: Path) -> bool:
    """使用 wl-paste 提取剪贴板图像（Wayland 会话）。"""
    try:
        # 检查可用的 MIME 类型
        types_r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=3,
        )
        if types_r.returncode != 0:
            return False
        types = types_r.stdout.splitlines()

        # 优先 PNG，回退到其他图像格式
        mime = None
        for preferred in ("image/png", "image/jpeg", "image/bmp",
                          "image/gif", "image/webp"):
            if preferred in types:
                mime = preferred
                break

        if not mime:
            return False

        # 提取图像数据
        with open(dest, "wb") as f:
            subprocess.run(
                ["wl-paste", "--type", mime],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5, check=True,
            )

        if not dest.exists() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return False

        # BMP 需要转换为 PNG（WSLg 中的常见情况，
        # 通过 RDP 从 Windows 剪贴板桥接时仅支持 BMP。
        if mime == "image/bmp":
            return _convert_to_png(dest)

        return True

    except FileNotFoundError:
        logger.debug("wl-paste 未安装 — Wayland 剪贴板不可用")
    except Exception as e:
        logger.debug("wl-paste 剪贴板提取失败: %s", e)
        dest.unlink(missing_ok=True)
    return False


def _convert_to_png(path: Path) -> bool:
    """将图像文件原地转换为 PNG（需要 Pillow 或 ImageMagick）。"""
    # 优先尝试 Pillow（通常安装在 venv 中）
    try:
        from PIL import Image
        img = Image.open(path)
        img.save(path, "PNG")
        return True
    except ImportError:
        pass
    except Exception as e:
        logger.debug("Pillow BMP→PNG 转换失败: %s", e)

    # 回退到 ImageMagick convert
    tmp = path.with_suffix(".bmp")
    try:
        path.rename(tmp)
        r = subprocess.run(
            ["convert", str(tmp), "png:" + str(path)],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0 and path.exists() and path.stat().st_size > 0:
            tmp.unlink(missing_ok=True)
            return True
        else:
            # 转换失败 — 恢复原始文件
            tmp.rename(path)
    except FileNotFoundError:
        logger.debug("ImageMagick 未安装 — 无法将 BMP 转换为 PNG")
        if tmp.exists() and not path.exists():
            tmp.rename(path)
    except Exception as e:
        logger.debug("ImageMagick BMP→PNG 转换失败: %s", e)
        if tmp.exists() and not path.exists():
            tmp.rename(path)

    # 无法转换 — BMP 仍然可用于大多数 API
    return path.exists() and path.stat().st_size > 0


# ── X11 (xclip) ─────────────────────────────────────────────────────────

def _xclip_has_image() -> bool:
    """检查 X11 剪贴板是否有图像内容。"""
    try:
        r = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0 and "image/png" in r.stdout
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return False


def _xclip_save(dest: Path) -> bool:
    """使用 xclip 提取剪贴板图像（X11 会话）。"""
    # Check if clipboard has image content
    try:
        targets = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, text=True, timeout=3,
        )
        if "image/png" not in targets.stdout:
            return False
    except FileNotFoundError:
        logger.debug("xclip 未安装 — X11 剪贴板图像粘贴不可用")
        return False
    except Exception:
        return False

    # 提取 PNG 数据
    try:
        with open(dest, "wb") as f:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5, check=True,
            )
        if dest.exists() and dest.stat().st_size > 0:
            return True
    except Exception as e:
        logger.debug("xclip 图像提取失败: %s", e)
        dest.unlink(missing_ok=True)
    return False
