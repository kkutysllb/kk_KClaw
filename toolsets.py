#!/usr/bin/env python3
"""
工具集模块

本模块提供了一种灵活的系统，用于定义和管理工具别名/工具集。
工具集允许你将工具分组以适应特定场景，可以由独立工具或其他工具集组成。

功能特性:
- 定义包含特定工具的自定义工具集
- 从其他工具集组合工具集
- 针对典型用例的内置常用工具集
- 轻松扩展新工具集
- 支持动态工具集解析

使用方法:
    from toolsets import get_toolset, resolve_toolset, get_all_toolsets
    
    # 获取特定工具集的工具
    tools = get_toolset("research")
    
    # 解析工具集以获取所有工具名称（包括组合工具集中的工具）
    all_tools = resolve_toolset("full_stack")
"""

from typing import List, Dict, Any, Set, Optional


# CLI 和所有消息平台工具集共享的工具列表。
# 编辑此处可同时更新所有平台。
_KCLAW_CORE_TOOLS = [
    # 网页
    "web_search", "web_extract",
    # 终端 + 进程管理
    "terminal", "process",
    # 文件操作
    "read_file", "write_file", "patch", "search_files",
    # 视觉 + 图像生成
    "vision_analyze", "image_generate",
    # 技能
    "skills_list", "skill_view", "skill_manage",
    # 浏览器自动化
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images",
    "browser_vision", "browser_console",
    # 语音合成
    "text_to_speech",
    # 规划与记忆
    "todo", "memory",
    # 会话历史搜索
    "session_search",
    # 澄清问题
    "clarify",
    # 代码执行 + 委托
    "execute_code", "delegate_task",
    # 定时任务管理
    "cronjob",
    # 跨平台消息（通过 check_fn 限制 gateway 必须运行）
    "send_message",
    # Home Assistant 智能家居控制（通过 check_fn 限制 HASS_TOKEN）
    "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",
]


# 核心工具集定义
# 可以包含独立工具或引用其他工具集
TOOLSETS = {
    # 基础工具集 - 独立工具类别
    "web": {
        "description": "网页搜索和内容提取工具",
        "tools": ["web_search", "web_extract"],
        "includes": []  # 不包含其他工具集
    },
    
    "search": {
        "description": "仅网页搜索（不含内容提取/抓取）",
        "tools": ["web_search"],
        "includes": []
    },
    
    "vision": {
        "description": "图像分析和视觉工具",
        "tools": ["vision_analyze"],
        "includes": []
    },
    
    "image_gen": {
        "description": "创意生成工具（图像）",
        "tools": ["image_generate"],
        "includes": []
    },
    
    "terminal": {
        "description": "终端/命令执行和进程管理工具",
        "tools": ["terminal", "process"],
        "includes": []
    },
    
    "moa": {
        "description": "高级推理和问题解决工具",
        "tools": ["mixture_of_agents"],
        "includes": []
    },
    
    "skills": {
        "description": "访问、创建、编辑和管理技能文档，包含专业指令和知识",
        "tools": ["skills_list", "skill_view", "skill_manage"],
        "includes": []
    },
    
    "browser": {
        "description": "用于网页交互的浏览器自动化工具（导航、点击、输入、滚动、iframe、长按点击），包含用于查找 URL 的网页搜索",
        "tools": [
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console", "web_search"
        ],
        "includes": []
    },
    
    "cronjob": {
        "description": "定时任务管理工具 - 创建、列出、更新、暂停、恢复、删除和触发计划任务",
        "tools": ["cronjob"],
        "includes": []
    },
    
    "messaging": {
        "description": "跨平台消息：向 Telegram、Discord、Slack、短信等发送消息",
        "tools": ["send_message"],
        "includes": []
    },
    
    "rl": {
        "description": "在 Tinker-Atropos 上运行强化学习的强化学习训练工具",
        "tools": [
            "rl_list_environments", "rl_select_environment",
            "rl_get_current_config", "rl_edit_config",
            "rl_start_training", "rl_check_status",
            "rl_stop_training", "rl_get_results",
            "rl_list_runs", "rl_test_inference"
        ],
        "includes": []
    },
    
    "file": {
        "description": "文件操作工具：读取、写入、打补丁（模糊匹配）、搜索（内容 + 文件）",
        "tools": ["read_file", "write_file", "patch", "search_files"],
        "includes": []
    },
    
    "tts": {
        "description": "语音合成：使用 Edge TTS（免费）、ElevenLabs 或 OpenAI 将文本转换为音频",
        "tools": ["text_to_speech"],
        "includes": []
    },
    
    "todo": {
        "description": "多步骤工作的任务规划和跟踪",
        "tools": ["todo"],
        "includes": []
    },
    
    "memory": {
        "description": "跨会话的持久化记忆（个人笔记 + 用户资料）",
        "tools": ["memory"],
        "includes": []
    },
    
    "session_search": {
        "description": "通过摘要搜索和回忆过去的对话",
        "tools": ["session_search"],
        "includes": []
    },
    
    "clarify": {
        "description": "向用户提出澄清问题（多选或开放式）",
        "tools": ["clarify"],
        "includes": []
    },
    
    "code_execution": {
        "description": "运行以编程方式调用工具的 Python 脚本（减少 LLM 往返次数）",
        "tools": ["execute_code"],
        "includes": []
    },
    
    "delegation": {
        "description": "为复杂子任务生成具有隔离上下文的子代理",
        "tools": ["delegate_task"],
        "includes": []
    },

    # "honcho" 工具集已移除 — Honcho 现在是记忆提供者插件。
    # 工具通过 MemoryManager 注入，而非工具集系统。

    "homeassistant": {
        "description": "Home Assistant 智能家居控制与监控",
        "tools": ["ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service"],
        "includes": []
    },


    # 场景特定工具集
    
    "debugging": {
        "description": "调试和故障排除工具包",
        "tools": ["terminal", "process"],
        "includes": ["web", "file"]  # 用于搜索错误消息和解决方案，以及文件操作
    },
    
    "safe": {
        "description": "不含终端访问的安全工具包",
        "tools": [],
        "includes": ["web", "vision", "image_gen"]
    },
    
    # ==========================================================================
    # 完整 KClaw 工具集（CLI + 消息平台）
    #
    # 所有平台共享相同的核心工具（包括 send_message，
    # 通过其 check_fn 限制 gateway 必须运行）。
    # ==========================================================================

    "kclaw-acp": {
        "description": "编辑器集成（VS Code、Zed、JetBrains）— 无消息、音频或澄清界面的编码专用工具",
        "tools": [
            "web_search", "web_extract",
            "terminal", "process",
            "read_file", "write_file", "patch", "search_files",
            "vision_analyze",
            "skills_list", "skill_view", "skill_manage",
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console",
            "todo", "memory",
            "session_search",
            "execute_code", "delegate_task",
        ],
        "includes": []
    },

    "kclaw-api-server": {
        "description": "OpenAI 兼容 API 服务器 — 通过 HTTP 访问完整代理工具（无交互式 UI 工具如 clarify 或 send_message）",
        "tools": [
            # 网页
            "web_search", "web_extract",
            # 终端 + 进程管理
            "terminal", "process",
            # 文件操作
            "read_file", "write_file", "patch", "search_files",
            # 视觉 + 图像生成
            "vision_analyze", "image_generate",
            # 技能
            "skills_list", "skill_view", "skill_manage",
            # 浏览器自动化
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console",
            # 规划与记忆
            "todo", "memory",
            # 会话历史搜索
            "session_search",
            # 代码执行 + 委托
            "execute_code", "delegate_task",
            # 定时任务管理
            "cronjob",
            # Home Assistant 智能家居控制（通过 check_fn 限制 HASS_TOKEN）
            "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",

        ],
        "includes": []
    },
    
    "kclaw-cli": {
        "description": "完整交互式 CLI 工具集 - 所有默认工具加定时任务管理",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },
    
    "kclaw-telegram": {
        "description": "Telegram 机器人工具集 - 个人使用完全访问权限（终端有安全检查）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },
    
    "kclaw-discord": {
        "description": "Discord 机器人工具集 - 完全访问权限（终端通过危险命令审批进行安全检查）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },
    
    "kclaw-whatsapp": {
        "description": "WhatsApp 机器人工具集 - 与 Telegram 类似（个人消息，更可信）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },
    
    "kclaw-slack": {
        "description": "Slack 机器人工具集 - 工作空间完全访问权限（终端有安全检查）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },
    
    "kclaw-signal": {
        "description": "Signal 机器人工具集 - 加密消息平台（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-bluebubbles": {
        "description": "BlueBubbles iMessage 机器人工具集 - 通过本地 BlueBubbles 服务器连接 Apple iMessage",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-homeassistant": {
        "description": "Home Assistant 机器人工具集 - 智能家居事件监控与控制",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-email": {
        "description": "邮件机器人工具集 - 通过邮件与 KClaw 交互（IMAP/SMTP）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-mattermost": {
        "description": "Mattermost 机器人工具集 - 自托管团队消息（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-matrix": {
        "description": "Matrix 机器人工具集 - 去中心化加密消息（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-dingtalk": {
        "description": "钉钉机器人工具集 - 企业消息平台（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-feishu": {
        "description": "飞书机器人工具集 - 通过飞书/ Lark 进行企业消息（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-wecom": {
        "description": "企业微信机器人工具集 - 企业微信消息（完全访问权限）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-sms": {
        "description": "短信机器人工具集 - 通过短信与 KClaw 交互（Twilio）",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-webhook": {
        "description": "Webhook 工具集 - 接收和处理外部 webhook 事件",
        "tools": _KCLAW_CORE_TOOLS,
        "includes": []
    },

    "kclaw-gateway": {
        "description": "Gateway 工具集 - 所有消息平台工具的并集",
        "tools": [],
        "includes": ["kclaw-telegram", "kclaw-discord", "kclaw-whatsapp", "kclaw-slack", "kclaw-signal", "kclaw-bluebubbles", "kclaw-homeassistant", "kclaw-email", "kclaw-sms", "kclaw-mattermost", "kclaw-matrix", "kclaw-dingtalk", "kclaw-feishu", "kclaw-wecom", "kclaw-webhook"]
    }
}



def get_toolset(name: str) -> Optional[Dict[str, Any]]:
    """
    按名称获取工具集定义。
    
    参数:
        name (str): 工具集名称
        
    返回:
        Dict: 包含 description、tools 和 includes 的工具集定义
        None: 如果工具集未找到
    """
    # 返回工具集定义
    return TOOLSETS.get(name)


def resolve_toolset(name: str, visited: Set[str] = None) -> List[str]:
    """
    递归解析工具集以获取所有工具名称。
    
    此函数通过递归解析包含的工具集并组合所有工具来处理工具集组合。
    
    参数:
        name (str): 要解析的工具集名称
        visited (Set[str]): 已访问工具集的集合（用于循环检测）
        
    返回:
        List[str]: 工具集中所有工具名称的列表
    """
    if visited is None:
        visited = set()
    
    # 代表所有工具集中所有工具的特殊别名
    # 这确保未来的工具集会自动包含，无需更改。
    if name in {"all", "*"}:
        all_tools: Set[str] = set()
        for toolset_name in get_toolset_names():
            # 每个分支使用新的 visited 集合以避免跨分支污染
            resolved = resolve_toolset(toolset_name, visited.copy())
            all_tools.update(resolved)
        return list(all_tools)

    # 检查循环 / 已解析（菱形依赖）。
    # 静默返回 [] — 要么是菱形（不是 bug，工具已通过
    # 另一条路径收集），要么是真正的循环（安全跳过）。
    if name in visited:
        return []

    visited.add(name)

    # 获取工具集定义
    toolset = TOOLSETS.get(name)
    if not toolset:
        # 回退到工具注册表以获取插件提供的工具集
        if name in _get_plugin_toolset_names():
            try:
                from tools.registry import registry
                return [e.name for e in registry._tools.values() if e.toolset == name]
            except Exception:
                pass
        return []

    # 收集直接工具
    tools = set(toolset.get("tools", []))

    # 递归解析包含的工具集，在兄弟包含之间共享 visited 集合，
    # 以便菱形依赖只解析一次，且同一循环的循环警告
    # 不会触发多次。
    for included_name in toolset.get("includes", []):
        included_tools = resolve_toolset(included_name, visited)
        tools.update(included_tools)
    
    return list(tools)


def resolve_multiple_toolsets(toolset_names: List[str]) -> List[str]:
    """
    解析多个工具集并组合它们的工具。
    
    参数:
        toolset_names (List[str]): 要解析的工具集名称列表
        
    返回:
        List[str]: 所有工具名称的合并列表（去重）
    """
    all_tools = set()
    
    for name in toolset_names:
        tools = resolve_toolset(name)
        all_tools.update(tools)
    
    return list(all_tools)


def _get_plugin_toolset_names() -> Set[str]:
    """返回由插件注册的工具集名称（来自工具注册表）。

    这些是存在于注册表中但不在静态 ``TOOLSETS`` 字典中的工具集
    —— 即它们是在加载时由插件添加的。
    """
    try:
        from tools.registry import registry
        return {
            entry.toolset
            for entry in registry._tools.values()
            if entry.toolset not in TOOLSETS
        }
    except Exception:
        return set()


def get_all_toolsets() -> Dict[str, Dict[str, Any]]:
    """
    获取所有可用的工具集及其定义。

    包括静态定义的工具集和插件注册的工具集。
    
    返回:
        Dict: 所有工具集定义
    """
    result = TOOLSETS.copy()
    # 添加插件提供的工具集（合成条目）
    for ts_name in _get_plugin_toolset_names():
        if ts_name not in result:
            try:
                from tools.registry import registry
                tools = [e.name for e in registry._tools.values() if e.toolset == ts_name]
                result[ts_name] = {
                    "description": f"插件工具集: {ts_name}",
                    "tools": tools,
                }
            except Exception:
                pass
    return result


def get_toolset_names() -> List[str]:
    """
    获取所有可用工具集的名称（不包括别名）。

    包括插件注册的工具集名称。
    
    返回:
        List[str]: 工具集名称列表
    """
    names = set(TOOLSETS.keys())
    names |= _get_plugin_toolset_names()
    return sorted(names)




def validate_toolset(name: str) -> bool:
    """
    检查工具集名称是否有效。
    
    参数:
        name (str): 要验证的工具集名称
        
    返回:
        bool: 如果有效返回 True，否则返回 False
    """
    # 为了方便接受特殊别名名称
    if name in {"all", "*"}:
        return True
    if name in TOOLSETS:
        return True
    # 检查工具注册表以获取插件提供的工具集
    return name in _get_plugin_toolset_names()


def create_custom_toolset(
    name: str,
    description: str,
    tools: List[str] = None,
    includes: List[str] = None
) -> None:
    """
    在运行时创建自定义工具集。
    
    参数:
        name (str): 新工具集的名称
        description (str): 工具集描述
        tools (List[str]): 要包含的直接工具
        includes (List[str]): 要包含的其他工具集
    """
    TOOLSETS[name] = {
        "description": description,
        "tools": tools or [],
        "includes": includes or []
    }




def get_toolset_info(name: str) -> Dict[str, Any]:
    """
    获取工具集的详细信息，包括解析后的工具。
    
    参数:
        name (str): 工具集名称
        
    返回:
        Dict: 详细的工具集信息
    """
    toolset = get_toolset(name)
    if not toolset:
        return None
    
    resolved_tools = resolve_toolset(name)
    
    return {
        "name": name,
        "description": toolset["description"],
        "direct_tools": toolset["tools"],
        "includes": toolset["includes"],
        "resolved_tools": resolved_tools,
        "tool_count": len(resolved_tools),
        "is_composite": bool(toolset["includes"])
    }




if __name__ == "__main__":
    print("工具集系统演示")
    print("=" * 60)
    
    print("\n可用工具集:")
    print("-" * 40)
    for name, toolset in get_all_toolsets().items():
        info = get_toolset_info(name)
        composite = "[组合]" if info["is_composite"] else "[独立]"
        print(f"  {composite} {name:20} - {toolset['description']}")
        print(f"     工具数: {len(info['resolved_tools'])} 个")
    
    print("\n工具集解析示例:")
    print("-" * 40)
    for name in ["web", "terminal", "safe", "debugging"]:
        tools = resolve_toolset(name)
        print(f"\n  {name}:")
        print(f"    解析为 {len(tools)} 个工具: {', '.join(sorted(tools))}")
    
    print("\n多工具集解析:")
    print("-" * 40)
    combined = resolve_multiple_toolsets(["web", "vision", "terminal"])
    print("  组合 ['web', 'vision', 'terminal']:")
    print(f"    结果: {', '.join(sorted(combined))}")
    
    print("\n创建自定义工具集:")
    print("-" * 40)
    create_custom_toolset(
        name="my_custom",
        description="我的自定义工具集，用于特定任务",
        tools=["web_search"],
        includes=["terminal", "vision"]
    )
    custom_info = get_toolset_info("my_custom")
    print("  创建了 'my_custom' 工具集:")
    print(f"    描述: {custom_info['description']}")
    print(f"    解析后的工具: {', '.join(custom_info['resolved_tools'])}")
