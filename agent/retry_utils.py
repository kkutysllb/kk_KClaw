"""重试工具函数 — 抖动退避算法用于去相关重试。

用抖动延迟替换固定的指数退避,防止多个会话同时
重试同一受限提供者时产生雷击效应。
"""

import random
import threading
import time

# 单调计数器,用于进程内抖动种子的唯一性。
# 通过锁保护,避免并发重试路径中的竞态条件
# (例如多个 gateway 会话同时重试)。
_jitter_counter = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """计算抖动指数退避延迟。

    Args:
        attempt: 从 1 开始的重试尝试次数。
        base_delay: 第 1 次尝试的基础延迟(秒)。
        max_delay: 最大延迟上限(秒)。
        jitter_ratio: 用作随机抖动范围的计算延迟比例。
            0.5 表示抖动范围为 [0, 0.5 * delay]。

    Returns:
        延迟秒数: min(base * 2^(attempt-1), max_delay) + jitter。

    抖动使并发重试去相关,因此多个会话
    访问同一提供者时不会在同一时刻重试。
    """
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    # 使用 时间 + 计数器 作为种子,即使时钟精度较低也能去相关。
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)

    return delay + jitter
