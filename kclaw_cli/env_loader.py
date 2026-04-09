"""Helpers for loading KClaw .env files consistently across entrypoints."""

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
    """Load KClaw environment files with user config taking precedence.

    Behavior:
    - `~/.kclaw/.env` overrides stale shell-exported values when present.
    - project `.env` acts as a dev fallback and only fills missing values when
      the user env exists.
    - if no user env exists, the project `.env` also overrides stale shell vars.
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
