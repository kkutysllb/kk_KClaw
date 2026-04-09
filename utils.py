"""kclaw 的共享工具函数。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Union

import yaml


TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    """使用项目的共享真值字符串集合强制转换布尔类值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def env_var_enabled(name: str, default: str = "") -> bool:
    """当环境变量设置为真值时返回 True。"""
    return is_truthy_value(os.getenv(name, default), default=False)


def atomic_json_write(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    **dump_kwargs: Any,
) -> None:
    """原子地将 JSON 数据写入文件。

    使用临时文件 + fsync + os.replace 来确保目标文件永远不会
    处于部分写入状态。如果进程在写入过程中崩溃，
    文件的先前版本保持不变。

    参数:
        path: 目标文件路径（将创建或覆盖）。
        data: 要写入的 JSON 可序列化数据。
        indent: JSON 缩进（默认 2）。
        **dump_kwargs: 转发到 json.dump() 的其他关键字参数，例如
            default=str 用于非原生类型。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=indent,
                ensure_ascii=False,
                **dump_kwargs,
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # 有意捕获 BaseException，以便对于进程级
        # 中断（在重新引发原始信号之前），临时文件清理仍然运行。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_yaml_write(
    path: Union[str, Path],
    data: Any,
    *,
    default_flow_style: bool = False,
    sort_keys: bool = False,
    extra_content: str | None = None,
) -> None:
    """原子地将 YAML 数据写入文件。

    使用临时文件 + fsync + os.replace 来确保目标文件永远不会
    处于部分写入状态。如果进程在写入过程中崩溃，
    文件的先前版本保持不变。

    参数:
        path: 目标文件路径（将创建或覆盖）。
        data: 要写入的 YAML 可序列化数据。
        default_flow_style: YAML 流样式（默认 False）。
        sort_keys: 是否对字典键排序（默认 False）。
        extra_content: 可选字符串，在 YAML dump 之后追加
            （例如用户参考的注释部分）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=default_flow_style, sort_keys=sort_keys)
            if extra_content:
                f.write(extra_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # 与 atomic_json_write 匹配：对于进程级
        # 中断也必须执行清理，然后我们重新引发它们。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
