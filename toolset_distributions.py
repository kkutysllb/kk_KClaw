#!/usr/bin/env python3
"""
工具集分布模块

本模块定义了用于数据生成运行的工具集分布。
每个分布指定了应使用哪些工具集以及在批处理过程中
选择它们的概率。

分布是将工具集名称映射到其选择概率（%）的字典。
概率总和应为 100，但系统会在不同时进行归一化。

用法：
    from toolset_distributions import get_distribution, list_distributions

    # 获取特定分布
    dist = get_distribution("image_gen")

    # 列出所有可用分布
    all_dists = list_distributions()
"""

from typing import Dict, List, Optional
import random
from toolsets import validate_toolset


# 分布定义
# 每个键是分布名称，值是工具集名称：概率百分比的字典
DISTRIBUTIONS = {
    # 默认：所有工具 100% 时间可用
    "default": {
        "description": "所有可用工具，全部时间可用",
        "toolsets": {
            "web": 100,
            "vision": 100,
            "image_gen": 100,
            "terminal": 100,
            "file": 100,
            "moa": 100,
            "browser": 100
        }
    },

    # 图像生成重点分布
    "image_gen": {
        "description": "重点关注图像生成，配合视觉和网络支持",
        "toolsets": {
            "image_gen": 90,  # 80% 图像生成工具机会
            "vision": 90,      # 60% 视觉工具机会
            "web": 55,         # 40% 网络工具机会
            "terminal": 45,
            "moa": 10          # 20% 推理工具机会
        }
    },

    # 研究重点分布
    "research": {
        "description": "网络研究与视觉分析和推理",
        "toolsets": {
            "web": 90,       # 90% 网络工具机会
            "browser": 70,   # 70% 浏览器工具机会用于深度研究
            "vision": 50,    # 50% 视觉工具机会
            "moa": 40,       # 40% 推理工具机会
            "terminal": 10   # 10% 终端工具机会
        }
    },

    # 科学问题解决重点分布
    "science": {
        "description": "科学研究所需的网络、终端、文件和浏览器能力",
        "toolsets": {
            "web": 94,       # 94% 网络工具机会
            "terminal": 94,  # 94% 终端工具机会
            "file": 94,      # 94% 文件工具机会
            "vision": 65,    # 65% 视觉工具机会
            "browser": 50,   # 50% 访问论文/数据库的浏览器机会
            "image_gen": 15, # 15% 图像生成工具机会
            "moa": 10        # 10% 推理工具机会
        }
    },

    # 开发重点分布
    "development": {
        "description": "终端、文件工具和推理，偶尔进行网络查询",
        "toolsets": {
            "terminal": 80,  # 80% 终端工具机会
            "file": 80,      # 80% 文件工具机会（读、写、补丁、搜索）
            "moa": 60,       # 60% 推理工具机会
            "web": 30,       # 30% 网络工具机会
            "vision": 10     # 10% 视觉工具机会
        }
    },

    # 安全模式（无终端）
    "safe": {
        "description": "除终端外的所有工具以确保安全",
        "toolsets": {
            "web": 80,
            "browser": 70,   # 浏览器是安全的（无本地文件系统访问）
            "vision": 60,
            "image_gen": 60,
            "moa": 50
        }
    },

    # 均衡分布
    "balanced": {
        "description": "所有工具集概率相等",
        "toolsets": {
            "web": 50,
            "vision": 50,
            "image_gen": 50,
            "terminal": 50,
            "file": 50,
            "moa": 50,
            "browser": 50
        }
    },

    # 最小化（仅网络）
    "minimal": {
        "description": "仅用于基本研究网络工具",
        "toolsets": {
            "web": 100
        }
    },

    # 仅终端
    "terminal_only": {
        "description": "用于代码执行任务的终端和文件工具",
        "toolsets": {
            "terminal": 100,
            "file": 100
        }
    },

    # 终端 + 网络（常用于需要文档的编码任务）
    "terminal_web": {
        "description": "终端和文件工具，配合网络搜索用于文档查询",
        "toolsets": {
            "terminal": 100,
            "file": 100,
            "web": 100
        }
    },

    # 创意（视觉 + 图像生成）
    "creative": {
        "description": "重点关注图像生成和视觉分析",
        "toolsets": {
            "image_gen": 90,
            "vision": 90,
            "web": 30
        }
    },

    # 重推理
    "reasoning": {
        "description": "大量使用智能体，其他工具最少",
        "toolsets": {
            "moa": 90,
            "web": 30,
            "terminal": 20
        }
    },

    # 基于浏览器的网络交互
    "browser_use": {
        "description": "全功能基于浏览器的网络交互，包含搜索、视觉和页面控制",
        "toolsets": {
            "browser": 100,  # 所有浏览器工具始终可用
            "web": 80,       # 用于查找 URL 和快速查询的网络搜索
            "vision": 70     # 用于分析页面上发现的图像的视觉分析
        }
    },

    # 仅浏览器（无其他工具）
    "browser_only": {
        "description": "仅用于纯网络交互任务的浏览器自动化工具",
        "toolsets": {
            "browser": 100
        }
    },

    # 浏览器重点任务分布（用于 browser-use-tasks.jsonl）
    "browser_tasks": {
        "description": "浏览器重点分布（浏览器工具集包含 web_search 用于查找 URL，因为 Google 阻止直接浏览器搜索）",
        "toolsets": {
            "browser": 97,   # 97% - 浏览器工具（包含 web_search）几乎始终可用
            "vision": 12,    # 12% - 偶尔进行视觉分析
            "terminal": 15   # 15% - 偶尔进行本地操作的终端
        }
    },

    # 终端重点任务分布（用于 nous-terminal-tasks.jsonl）
    "terminal_tasks": {
        "description": "终端重点分布，终端/文件可用性高，偶尔使用其他工具",
        "toolsets": {
            "terminal": 97,   # 97% - 终端几乎始终可用
            "file": 97,       # 97% - 文件工具几乎始终可用
            "web": 97,        # 15% - 用于文档的网络搜索/抓取
            "browser": 75,    # 10% - 偶尔进行网络交互的浏览器
            "vision": 50,      # 8% - 很少进行视觉分析
            "image_gen": 10    # 3% - 很少进行图像生成
        }
    },

    # 混合浏览器+终端任务分布（用于 mixed-browser-terminal-tasks.jsonl）
    "mixed_tasks": {
        "description": "混合分布，浏览器、终端和文件可用性高，用于复杂任务",
        "toolsets": {
            "browser": 92,    # 92% - 浏览器工具高度可用
            "terminal": 92,   # 92% - 终端高度可用
            "file": 92,       # 92% - 文件工具高度可用
            "web": 35,        # 35% - 网络搜索/抓取相当常见
            "vision": 15,     # 15% - 偶尔进行视觉分析
            "image_gen": 15   # 15% - 偶尔进行图像生成
        }
    }
}


def get_distribution(name: str) -> Optional[Dict[str, any]]:
    """
    通过名称获取工具集分布。

    参数:
        name (str): 分布名称

    返回:
        Dict: 包含描述和工具集的分布定义
        None: 如果未找到分布
    """
    return DISTRIBUTIONS.get(name)


def list_distributions() -> Dict[str, Dict]:
    """
    列出所有可用分布。

    返回:
        Dict: 所有分布定义
    """
    return DISTRIBUTIONS.copy()


def sample_toolsets_from_distribution(distribution_name: str) -> List[str]:
    """
    基于分布的概率对工具集进行采样。

    分布中的每个工具集都有被包含的 % 概率。
    这允许同时激活多个工具集。

    参数:
        distribution_name (str): 要从中采样的分布名称

    返回:
        List[str]: 采样的工具集名称列表

    抛出:
        ValueError: 如果分布名称未找到
    """
    dist = get_distribution(distribution_name)
    if not dist:
        raise ValueError(f"未知分布：{distribution_name}")

    # 基于每个工具集的概率独立采样
    selected_toolsets = []

    for toolset_name, probability in dist["toolsets"].items():
        # 验证工具集是否存在
        if not validate_toolset(toolset_name):
            print(f"⚠️  警告：分布 '{distribution_name}' 中的工具集 '{toolset_name}' 无效")
            continue

        # 掷骰子——如果随机值小于概率，则包含此工具集
        if random.random() * 100 < probability:
            selected_toolsets.append(toolset_name)

    # 如果没有选择任何工具集（可能在低概率时发生），
    # 通过选择最高概率的工具集来确保至少选择一个工具集
    if not selected_toolsets and dist["toolsets"]:
        # 找到概率最高的工具集
        highest_prob_toolset = max(dist["toolsets"].items(), key=lambda x: x[1])[0]
        if validate_toolset(highest_prob_toolset):
            selected_toolsets.append(highest_prob_toolset)

    return selected_toolsets


def validate_distribution(distribution_name: str) -> bool:
    """
    检查分布名称是否有效。

    参数:
        distribution_name (str): 要验证的分布名称

    返回:
        bool: 如果有效则为 True，否则为 False
    """
    return distribution_name in DISTRIBUTIONS


def print_distribution_info(distribution_name: str) -> None:
    """
    打印有关分布的详细信息。

    参数:
        distribution_name (str): 分布名称
    """
    dist = get_distribution(distribution_name)
    if not dist:
        print(f"❌ 未知分布：{distribution_name}")
        return

    print(f"\n📊 分布：{distribution_name}")
    print(f"   描述：{dist['description']}")
    print("   工具集：")
    for toolset, prob in sorted(dist["toolsets"].items(), key=lambda x: x[1], reverse=True):
        print(f"     • {toolset:15} : {prob:3}% 概率")


if __name__ == "__main__":
    """
    分布系统的演示和测试
    """
    print("📊 工具集分布演示")
    print("=" * 60)

    # 列出所有分布
    print("\n📋 可用分布：")
    print("-" * 40)
    for name, dist in list_distributions().items():
        print(f"\n  {name}:")
        print(f"    {dist['description']}")
        toolset_list = ", ".join([f"{ts}({p}%)" for ts, p in dist["toolsets"].items()])
        print(f"    工具集：{toolset_list}")

    # 演示采样
    print("\n\n🎲 采样示例：")
    print("-" * 40)

    test_distributions = ["image_gen", "research", "balanced", "default"]

    for dist_name in test_distributions:
        print(f"\n{dist_name}:")
        # 采样 5 次以显示变异性
        samples = []
        for _ in range(5):
            sampled = sample_toolsets_from_distribution(dist_name)
            samples.append(sorted(sampled))

        print(f"  样本 1: {samples[0]}")
        print(f"  样本 2: {samples[1]}")
        print(f"  样本 3: {samples[2]}")
        print(f"  样本 4: {samples[3]}")
        print(f"  样本 5: {samples[4]}")

    # 显示详细信息
    print("\n\n📊 详细分布信息：")
    print("-" * 40)
    print_distribution_info("image_gen")
    print_distribution_info("research")
