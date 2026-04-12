"""kclaw logs — 查看和过滤 KClaw 日志文件。

支持跟踪、实时查看、会话过滤、级别过滤和相对时间范围。
所有日志文件位于 ~/.kclaw/logs/ 下。

用法示例::

    kclaw logs                    # agent.log 最后 50 行
    kclaw logs -f                 # 实时跟踪 agent.log
    kclaw logs errors             # errors.log 最后 50 行
    kclaw logs gateway -n 100     # gateway.log 最后 100 行
    kclaw logs --level WARNING    # 仅 WARNING 及以上级别
    kclaw logs --session abc123   # 按会话 ID 过滤
    kclaw logs --since 1h         # 最近一小时的日志
    kclaw logs --since 30m -f     # 从 30 分钟前开始跟踪
"""

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from kclaw_constants import get_kclaw_home, display_kclaw_home

# Known log files (name → filename)
LOG_FILES = {
    "agent": "agent.log",
    "errors": "errors.log",
    "gateway": "gateway.log",
}

# Log line timestamp regex — matches "2026-04-05 22:35:00,123" or
# "2026-04-05 22:35:00" at the start of a line.
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")

# Level extraction — matches " INFO ", " WARNING ", " ERROR ", " DEBUG ", " CRITICAL "
_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s")

# Level ordering for >= filtering
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _parse_since(since_str: str) -> Optional[datetime]:
    """Parse a relative time string like '1h', '30m', '2d' into a datetime cutoff.

    Returns None if the string can't be parsed.
    """
    since_str = since_str.strip().lower()
    match = re.match(r"^(\d+)\s*([smhd])$", since_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    delta = {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
    }[unit]
    return datetime.now() - delta


def _parse_line_timestamp(line: str) -> Optional[datetime]:
    """从日志行中提取时间戳。如果无法解析则返回 None。"""
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _extract_level(line: str) -> Optional[str]:
    """从行中提取日志级别。"""
    m = _LEVEL_RE.search(line)
    return m.group(1) if m else None


def _matches_filters(
    line: str,
    *,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
) -> bool:
    """检查日志行是否通过所有活动过滤器。"""
    if since is not None:
        ts = _parse_line_timestamp(line)
        if ts is not None and ts < since:
            return False

    if min_level is not None:
        level = _extract_level(line)
        if level is not None:
            if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(min_level, 0):
                return False

    if session_filter is not None:
        if session_filter not in line:
            return False

    return True


def tail_log(
    log_name: str = "agent",
    *,
    num_lines: int = 50,
    follow: bool = False,
    level: Optional[str] = None,
    session: Optional[str] = None,
    since: Optional[str] = None,
) -> None:
    """读取并显示日志行，可选择实时跟踪。

    参数
    ----------
    log_name
        要读取的日志："agent"、"errors"、"gateway"。
    num_lines
        显示的最近行数（跟踪开始前）。
    follow
        如果为 True，持续监视新行（Ctrl+C 停止）。
    level
        显示的最低日志级别（例如 "WARNING"）。
    session
        要过滤的会话 ID 子字符串。
    since
        相对时间字符串（例如 "1h"、"30m"）。
    """
    filename = LOG_FILES.get(log_name)
    if filename is None:
        print(f"Unknown log: {log_name!r}. Available: {', '.join(sorted(LOG_FILES))}")
        sys.exit(1)

    log_path = get_kclaw_home() / "logs" / filename
    if not log_path.exists():
        print(f"日志文件未找到：{log_path}")
        print(f"(日志在 KClaw 运行时创建 — 请先运行 'kclaw chat')")
        sys.exit(1)

    # Parse --since into a datetime cutoff
    since_dt = None
    if since:
        since_dt = _parse_since(since)
        if since_dt is None:
            print(f"无效的 --since 值：{since!r}。请使用 '1h'、'30m'、'2d' 格式。")
            sys.exit(1)

    min_level = level.upper() if level else None
    if min_level and min_level not in _LEVEL_ORDER:
        print(f"无效的 --level：{level!r}。请使用 DEBUG、INFO、WARNING、ERROR 或 CRITICAL。")
        sys.exit(1)

    has_filters = min_level is not None or session is not None or since_dt is not None

    # Read and display the tail
    try:
        lines = _read_tail(log_path, num_lines, has_filters=has_filters,
                           min_level=min_level, session_filter=session,
                           since=since_dt)
    except PermissionError:
        print(f"权限被拒绝：{log_path}")
        sys.exit(1)

    # Print header
    filter_parts = []
    if min_level:
        filter_parts.append(f"level>={min_level}")
    if session:
        filter_parts.append(f"session={session}")
    if since:
        filter_parts.append(f"since={since}")
    filter_desc = f" [{', '.join(filter_parts)}]" if filter_parts else ""

    if follow:
        print(f"--- {display_kclaw_home()}/logs/{filename}{filter_desc}（按 Ctrl+C 停止）---")
    else:
        print(f"--- {display_kclaw_home()}/logs/{filename}{filter_desc}（最近 {num_lines} 行）---")

    for line in lines:
        print(line, end="")

    if not follow:
        return

    # Follow mode — poll for new content
    try:
        _follow_log(log_path, min_level=min_level, session_filter=session,
                     since=since_dt)
    except KeyboardInterrupt:
        print("\n--- 已停止 ---")


def _read_tail(
    path: Path,
    num_lines: int,
    *,
    has_filters: bool = False,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
) -> list:
    """从日志文件中读取最后 num_lines 匹配的行。

    当过滤器激活时，我们会读取更多原始行以确保有足够的匹配。
    """
    if has_filters:
        # Read more lines to ensure we get enough after filtering.
        # For large files, read last 10K lines and filter down.
        raw_lines = _read_last_n_lines(path, max(num_lines * 20, 2000))
        filtered = [
            l for l in raw_lines
            if _matches_filters(l, min_level=min_level,
                                session_filter=session_filter, since=since)
        ]
        return filtered[-num_lines:]
    else:
        return _read_last_n_lines(path, num_lines)


def _read_last_n_lines(path: Path, n: int) -> list:
    """高效地从文件中读取最后 N 行。

    对于小于 1MB 的文件，读取整个文件（快速、简单）。
    对于更大的文件，从末尾读取块。
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return []

        # For files up to 1MB, just read the whole thing — simple and correct.
        if size <= 1_048_576:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return all_lines[-n:]

        # For large files, read chunks from the end.
        with open(path, "rb") as f:
            chunk_size = 8192
            lines = []
            pos = size

            while pos > 0 and len(lines) <= n + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                chunk_lines = chunk.split(b"\n")
                if lines:
                    # 将新块的最后一个不完整行与已有的第一个不完整行合并。
                    lines[0] = chunk_lines[-1] + lines[0]
                    lines = chunk_lines[:-1] + lines
                else:
                    lines = chunk_lines
                chunk_size = min(chunk_size * 2, 65536)

            # Decode and return last N non-empty lines.
            decoded = []
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    decoded.append(raw.decode("utf-8", errors="replace") + "\n")
                except Exception:
                    decoded.append(raw.decode("latin-1") + "\n")
            return decoded[-n:]

    except Exception:
        # Fallback: read entire file
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return all_lines[-n:]


def _follow_log(
    path: Path,
    *,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
) -> None:
    """轮询日志文件获取新内容并打印匹配的行。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        # Seek to end
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                if _matches_filters(line, min_level=min_level,
                                    session_filter=session_filter, since=since):
                    print(line, end="")
                    sys.stdout.flush()
            else:
                time.sleep(0.3)


def list_logs() -> None:
    """打印可用的日志文件及大小。"""
    log_dir = get_kclaw_home() / "logs"
    if not log_dir.exists():
        print(f"{display_kclaw_home()}/logs/ 目录下无日志")
        return

    print(f"{display_kclaw_home()}/logs/ 中的日志文件：\n")
    found = False
    for entry in sorted(log_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".log":
            size = entry.stat().st_size
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            age = datetime.now() - mtime
            if age.total_seconds() < 60:
                age_str = "刚刚"
            elif age.total_seconds() < 3600:
                age_str = f"{int(age.total_seconds() / 60)}分钟前"
            elif age.total_seconds() < 86400:
                age_str = f"{int(age.total_seconds() / 3600)}小时前"
            else:
                age_str = mtime.strftime("%Y-%m-%d")
            print(f"  {entry.name:<25} {size_str:>8}   {age_str}")
            found = True

    if not found:
        print("  （尚无日志文件 — 请运行 'kclaw chat' 生成日志）")
