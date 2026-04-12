"""可插拔记忆提供者的抽象基类。

记忆提供者为 agent 提供跨会话的持久召回能力。一次只能有
一个外部提供者与始终激活的内置记忆(MEMORY.md / USER.md)一起
运行。MemoryManager 强制执行此限制。

内置记忆始终作为第一个提供者激活,无法移除。
外部提供者(Honcho、Hindsight、Mem0 等)是附加的 — 它们不会
禁用内置存储。一次只能运行一个外部提供者,以防止工具
schema 膨胀和记忆后端冲突。

注册:
  1. 内置: BuiltinMemoryProvider — 始终存在,不可移除。
  2. 插件: 位于 plugins/memory/<name>/,通过 memory.provider 配置激活。

生命周期(由 MemoryManager 调用,在 run_agent.py 中连接):
  initialize()          — 连接、创建资源、预热
  system_prompt_block()  — 系统提示词的静态文本
  prefetch(query)        — 每轮之前的后台召回
  sync_turn(user, asst)  — 每轮之后的异步写入
  get_tool_schemas()     — 暴露给模型的工具 schema
  handle_tool_call()     — 分发工具调用
  shutdown()             — 清理退出

可选钩子(覆盖以启用):
  on_turn_start(turn, message, **kwargs) — 每轮刻度带运行时上下文
  on_session_end(messages)               — 会话结束提取
  on_pre_compress(messages) -> str       — 上下文压缩前提取
  on_memory_write(action, target, content) — 镜像内置记忆写入
  on_delegation(task, result, **kwargs)  — 父级观察子 agent 工作
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """记忆提供者的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """此提供者的短标识符(例如 'builtin'、'honcho'、'hindsight')。"""

    # -- 核心生命周期(实现这些) ------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """如果此提供者已配置、有凭据且就绪,则返回 True。

        在 agent 初始化期间调用,以决定是否激活提供者。
        不应发起网络调用 — 仅检查配置和已安装的依赖。
        """

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """为会话初始化。

        在 agent 启动时调用一次。可以创建资源(库、表),
        建立连接,启动后台线程等。

        kwargs 始终包含:
          - kclaw_home (str): 活动的 KCLAW_HOME 目录路径。使用此
            进行 profile 范围的存储,而不是硬编码 ``~/.kclaw``。
          - platform (str): "cli"、"telegram"、"discord"、"cron" 等。

        kwargs 还可能包含:
          - agent_context (str): "primary"、"subagent"、"cron" 或 "flush"。
            提供者应跳过非主要上下文的写入(cron 系统
            提示词会破坏用户表示)。
          - agent_identity (str): Profile 名称(例如 "coder")。用于
            按 profile 的提供者身份范围。
          - agent_workspace (str): 共享工作区名称(例如 "kclaw")。
          - parent_session_id (str): 对于子 agent,父级的 session_id。
          - user_id (str): 平台用户标识符(gateway 会话)。
        """

    def system_prompt_block(self) -> str:
        """返回要包含在系统提示词中的文本。

        在系统提示词组装期间调用。返回空字符串以跳过。
        这用于静态提供者信息(说明、状态)。预取的
        召回上下文通过 prefetch() 单独注入。
        """
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """为即将到来的轮次召回相关上下文。

        在每次 API 调用之前调用。返回格式化文本作为
        上下文注入,如果没有相关内容则返回空字符串。
        实现应该很快 — 使用后台线程进行实际召回,
        并在此返回缓存的结果。

        提供 session_id 用于服务并发会话的提供者
        (gateway 群聊、缓存 agent)。不需要每个会话
        范围的提供者可以忽略它。
        """
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """为下一轮排队后台召回。

        在每轮完成后调用。结果将在下一轮的 prefetch() 中
        被消费。默认为空操作 — 做后台预取的提供者
        应覆盖此方法。
        """

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """将完成的轮次持久化到后端。

        在每轮后调用。应该是非阻塞的 — 如果后端有延迟,
        排队进行后台处理。
        """

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回此提供者暴露的工具 schema。

        每个 schema 遵循 OpenAI 函数调用格式:
        {"name": "...", "description": "...", "parameters": {...}}

        如果此提供者没有工具(仅上下文),返回空列表。
        """

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """处理此提供者的某个工具的工具调用。

        必须返回 JSON 字符串(工具结果)。
        仅对 get_tool_schemas() 返回的工具名称调用。
        """
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """清理关闭 — 刷新队列、关闭连接。"""

    # -- 可选钩子(覆盖以启用) ---------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """在每轮开始时调用,带有用户消息。

        用于轮次计数、范围管理、周期性维护。

        kwargs 可能包含: remaining_tokens、model、platform、tool_count。
        提供者使用它们需要的;多余的被忽略。
        """

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """当会话结束时调用(显式退出或超时)。

        用于会话结束时的事实提取、摘要等。
        messages 是完整的对话历史。

        不在每轮后调用 — 仅在实际的会话边界
        (CLI 退出、/reset、gateway 会话过期)。
        """

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """在上下文压缩丢弃旧消息之前调用。

        用于从即将被压缩的消息中提取洞察。
        messages 是将被摘要/丢弃的列表。

        返回要包含在压缩摘要提示词中的文本,以便压缩器
        保留提供者提取的洞察。返回空字符串表示无贡献
        (向后兼容的默认值)。
        """
        return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """在子 agent 完成时在父 agent 上调用。

        父 agent 的记忆提供者获得 task+result 对作为
        委派了什么和返回了什么的观察。子 agent
        本身没有提供者会话(skip_memory=True)。

        task: 委派提示词
        result: 子 agent 的最终响应
        child_session_id: 子 agent 的 session_id
        """

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """返回此提供者设置所需的配置字段。

        由 'kclaw memory setup' 用于引导用户完成配置。
        每个字段是一个 dict:
          key:         配置键名(例如 'api_key'、'mode')
          description: 人类可读的描述
          secret:      True 如果应放入 .env(默认: False)
          required:    True 如果必需(默认: False)
          default:     默认值(可选)
          choices:     有效值列表(可选)
          url:         用户可以获取此凭据的 URL(可选)
          env_var:     密钥的显式环境变量名(默认: 自动生成)

        如果不需要配置(例如仅本地提供者),返回空列表。
        """
        return []

    def save_config(self, values: Dict[str, Any], kclaw_home: str) -> None:
        """将非密钥配置写入提供者的原生位置。

        在 'kclaw memory setup' 收集用户输入后调用。
        ``values`` 仅包含非密钥字段(密钥放入 .env)。
        ``kclaw_home`` 是活动的 KCLAW_HOME 目录路径。

        有原生配置文件(JSON、YAML)的提供者应覆盖
        此方法以写入其预期位置。仅使用
        环境变量的提供者可以保留默认值(空操作)。

        所有新的记忆提供者插件必须实现以下之一:
        - save_config() 用于原生配置文件格式,或
        - 仅使用环境变量(在这种情况下 get_config_schema() 字段
          都应设置 ``env_var``,此方法保持空操作)。
        """

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """当内置记忆工具写入条目时调用。

        action: 'add'、'replace' 或 'remove'
        target: 'memory' 或 'user'
        content: 条目内容

        用于将内置记忆写入镜像到你的后端。
        """
