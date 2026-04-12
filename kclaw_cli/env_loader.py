"""跨入口点一致加载 KClaw .env 文件的帮助函数。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv_with_fallback(path: Path, *, override: bool) -> None:
    try:
        load_dotenv(dotenv_path=path, override=override, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(dotenv_path=path, override=override, encoding="latin-1")


def load_kclaw_dotenv(
    *,
    kclaw_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
) -> list[Path]:
    """加载 KClaw 环境文件，用户配置优先。

    行为：
    - `~/.kclaw/.env` 存在时覆盖过时的 shell 导出值。
    - 项目 `.env` 作为开发回退，仅在用户环境存在时填充缺失值。
    - 如果不存在用户环境，项目 `.env` 也会覆盖过时的 shell 变量。
    """
    loaded: list[Path] = []

    home_path = Path(kclaw_home or os.getenv("KCLAW_HOME", Path.home() / ".kclaw"))
    user_env = home_path / ".env"
    project_env_path = Path(project_env) if project_env else None

    if user_env.exists():
        _load_dotenv_with_fallback(user_env, override=True)
        loaded.append(user_env)

    if project_env_path and project_env_path.exists():
        _load_dotenv_with_fallback(project_env_path, override=not loaded)
        loaded.append(project_env_path)

    return loaded
