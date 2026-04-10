#!/usr/bin/env python3
"""
使用 KClaw 轨迹格式的 SWE Runner

一个使用 KClaw-Agent 内置执行环境（local、docker、modal）
并以 KClaw-Agent 格式输出轨迹的运行器，与 batch_runner.py
和 trajectory_compressor.py 兼容。

特性：
- 使用 KClaw-Agent 的 Docker、Modal 或 Local 环境执行命令
- 以 KClaw 格式输出轨迹（from/value 对，包含 <tool_call>/<tool_response> XML）
- 与轨迹压缩管道兼容
- 支持从 JSONL 提示文件批量处理

使用方法：
    # 使用本地环境运行单个任务
    python mini_swe_runner.py --task "Create a hello world Python script" --env local

    # 使用 Docker 运行
    python mini_swe_runner.py --task "List files in /tmp" --env docker --image python:3.11-slim

    # 使用 Modal（云端）运行
    python mini_swe_runner.py --task "Install numpy and test it" --env modal --image python:3.11-slim

    # 从 JSONL 文件批量模式
    python mini_swe_runner.py --prompts_file prompts.jsonl --output_file trajectories.jsonl --env docker
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal

import fire
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()




# ============================================================================
# 终端工具定义（匹配 KClaw-Agent 格式）
# ============================================================================

TERMINAL_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": """在沙箱环境中执行 bash 命令。

**环境：**
- 隔离的执行环境（local、Docker 或 Modal 云端）
- 文件系统在同一个任务内的工具调用之间持久化
- 可访问互联网

**命令执行：**
- 通过 'command' 参数提供要执行的命令
- 可选的 'timeout' 参数（秒，默认：60）

**示例：**
- 运行命令：`{"command": "ls -la"}`
- 带超时：`{"command": "long_task.sh", "timeout": 300}`

**最佳实践：**
- 使用非交互式命令（避免 vim、nano、interactive python）
- 如果输出可能很大，管道到 cat
- 根据需要使用 apt-get 或 pip 安装工具

**完成：**
- 任务完成时，输出：echo "MINI_SWE_AGENT_FINAL_OUTPUT" 后跟结果
""",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 bash 命令"
                },
                "timeout": {
                    "type": "integer",
                    "description": "命令超时秒数（默认：60）"
                }
            },
            "required": ["command"]
        }
    }
}


# ============================================================================
# 环境工厂
# ============================================================================

def create_environment(
    env_type: str = "local",
    image: str = "python:3.11-slim",
    cwd: str = "/tmp",
    timeout: int = 60,
    **kwargs
):
    """
    使用 KClaw-Agent 内置后端创建执行环境。

    参数：
        env_type: "local"、"docker"、"modal" 之一
        image: Docker/Modal 镜像名称（local 时忽略）
        cwd: 工作目录
        timeout: 默认命令超时
        **kwargs: 额外的环境特定选项

    返回：
        带有 execute() 和 cleanup() 方法的环境实例
    """
    if env_type == "local":
        from tools.environments.local import LocalEnvironment
        return LocalEnvironment(cwd=cwd, timeout=timeout)

    elif env_type == "docker":
        from tools.environments.docker import DockerEnvironment
        return DockerEnvironment(image=image, cwd=cwd, timeout=timeout, **kwargs)

    elif env_type == "modal":
        from tools.environments.modal import ModalEnvironment
        return ModalEnvironment(image=image, cwd=cwd, timeout=timeout, **kwargs)

    else:
        raise ValueError(f"未知环境类型: {env_type}。请使用 'local'、'docker' 或 'modal'")


# ============================================================================
# 使用 KClaw 轨迹格式的 Mini-SWE Runner
# ============================================================================

class MiniSWERunner:
    """
    使用 KClaw-Agent 内置执行环境并以 KClaw-Agent 格式输出轨迹的 Agent 运行器。
    """

    def __init__(
        self,
        model: str = "anthropic/claude-sonnet-4.6",
        base_url: str = None,
        api_key: str = None,
        env_type: str = "local",
        image: str = "python:3.11-slim",
        cwd: str = "/tmp",
        max_iterations: int = 15,
        command_timeout: int = 60,
        verbose: bool = False,
    ):
        """
        初始化 Mini-SWE Runner。

        参数：
            model: OpenAI 兼容 API 的模型名称
            base_url: API 基础 URL（可选，未提供时使用环境变量）
            api_key: API 密钥（可选，未提供时使用环境变量）
            env_type: 环境类型 - "local"、"docker" 或 "modal"
            image: Docker/Modal 镜像（local 时忽略）
            cwd: 命令工作目录
            max_iterations: 最大工具调用迭代次数
            command_timeout: 命令默认超时
            verbose: 启用详细日志
        """
        self.model = model
        self.max_iterations = max_iterations
        self.command_timeout = command_timeout
        self.verbose = verbose
        self.env_type = env_type
        self.image = image
        self.cwd = cwd

        # 设置日志
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)

        # 通过集中式 provider 路由器初始化 LLM 客户端。
        # 如果提供了显式 api_key/base_url（例如来自 CLI 参数），
        # 直接构造。否则使用 OpenRouter 的路由器。
        if api_key or base_url:
            from openai import OpenAI
            client_kwargs = {
                "base_url": base_url or "https://openrouter.ai/api/v1",
                "api_key": api_key or os.getenv(
                    "OPENROUTER_API_KEY",
                    os.getenv("ANTHROPIC_API_KEY",
                              os.getenv("OPENAI_API_KEY", ""))),
            }
            self.client = OpenAI(**client_kwargs)
        else:
            from agent.auxiliary_client import resolve_provider_client
            self.client, _ = resolve_provider_client("openrouter", model=model)
            if self.client is None:
                # 回退：尝试自动检测
                self.client, _ = resolve_provider_client("auto", model=model)
            if self.client is None:
                from openai import OpenAI
                self.client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.getenv("OPENROUTER_API_KEY", ""))

        # 环境将在每个任务中创建
        self.env = None

        # 工具定义
        self.tools = [TERMINAL_TOOL_DEFINITION]

        print("🤖 Mini-SWE Runner 已初始化")
        print(f"   Model: {self.model}")
        print(f"   Environment: {self.env_type}")
        if self.env_type != "local":
            print(f"   Image: {self.image}")
        print(f"   Max iterations: {self.max_iterations}")

    def _create_env(self):
        """创建执行环境。"""
        print(f"🔧 正在创建 {self.env_type} 环境...")
        self.env = create_environment(
            env_type=self.env_type,
            image=self.image,
            cwd=self.cwd,
            timeout=self.command_timeout
        )
        print("✅ 环境已就绪")

    def _cleanup_env(self):
        """清理执行环境。"""
        if self.env is not None:
            if hasattr(self.env, 'cleanup'):
                self.env.cleanup()
            elif hasattr(self.env, 'stop'):
                self.env.stop()
            self.env = None

    def _execute_command(self, command: str, timeout: int = None) -> Dict[str, Any]:
        """
        在环境中执行命令。

        参数：
            command: 要执行的 bash 命令
            timeout: 可选的超时覆盖

        返回：
            包含 'output' 和 'returncode' 的字典
        """
        if self.env is None:
            self._create_env()

        try:
            result = self.env.execute(command, timeout=timeout or self.command_timeout)
            return {
                "output": result.get("output", ""),
                "exit_code": result.get("returncode", 0),
                "error": None
            }
        except Exception as e:
            return {
                "output": "",
                "exit_code": -1,
                "error": str(e)
            }

    def _format_tools_for_system_message(self) -> str:
        """格式化系统消息的工具定义。"""
        formatted_tools = []
        for tool in self.tools:
            func = tool["function"]
            formatted_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
                "required": None
            })
        return json.dumps(formatted_tools, ensure_ascii=False)

    def _convert_to_kclaw_format(
        self,
        messages: List[Dict[str, Any]],
        user_query: str,
        completed: bool
    ) -> List[Dict[str, Any]]:
        """
        将内部消息格式转换为 KClaw 轨迹格式。

        这会产生与 batch_runner.py 完全相同的格式。
        """
        trajectory = []

        # 带工具定义的系统消息
        system_msg = (
            "You are a function calling AI model. You are provided with function signatures within <tools> </tools> XML tags. "
            "You may call one or more functions to assist with the user query. If available tools are not relevant in assisting "
            "with user query, just respond in natural conversational language. Don't make assumptions about what values to plug "
            "into functions. After calling & executing the functions, you will be provided with function results within "
            "<tool_response> </tool_response> XML tags. Here are the available tools:\n"
            f"<tools>\n{self._format_tools_for_system_message()}\n</tools>\n"
            "For each function call return a JSON object, with the following pydantic model json schema for each:\n"
            "{'title': 'FunctionCall', 'type': 'object', 'properties': {'name': {'title': 'Name', 'type': 'string'}, "
            "'arguments': {'title': 'Arguments', 'type': 'object'}}, 'required': ['name', 'arguments']}\n"
            "Each function call should be enclosed within <tool_call> </tool_call> XML tags.\n"
            "Example:\n<tool_call>\n{'name': <function-name>,'arguments': <args-dict>}\n</tool_call>"
        )

        trajectory.append({"from": "system", "value": system_msg})
        trajectory.append({"from": "human", "value": user_query})

        # 处理消息（跳过第一条用户消息，因为已经添加）
        i = 1
        while i < len(messages):
            msg = messages[i]

            if msg["role"] == "assistant":
                # 助手消息可能包含工具调用
                if msg.get("tool_calls"):
                    content = ""

                    if msg.get("content"):
                        content += msg["content"] + "\n"

                    # 以 XML 格式添加工具调用
                    for tool_call in msg["tool_calls"]:
                        if not tool_call or not isinstance(tool_call, dict): continue
                        try:
                            arguments = json.loads(tool_call["function"]["arguments"]) \
                                if isinstance(tool_call["function"]["arguments"], str) \
                                else tool_call["function"]["arguments"]
                        except json.JSONDecodeError:
                            arguments = {}

                        tool_call_json = {
                            "name": tool_call["function"]["name"],
                            "arguments": arguments
                        }
                        content += f"<tool_call>\n{json.dumps(tool_call_json, ensure_ascii=False)}\n</tool_call>\n"

                    trajectory.append({"from": "gpt", "value": content.rstrip()})

                    # 收集后续工具响应
                    tool_responses = []
                    j = i + 1
                    while j < len(messages) and messages[j]["role"] == "tool":
                        tool_msg = messages[j]
                        tool_content = tool_msg["content"]

                        # 尝试解析为 JSON
                        try:
                            if tool_content.strip().startswith(("{", "[")):
                                tool_content = json.loads(tool_content)
                        except (json.JSONDecodeError, AttributeError):
                            pass

                        tool_response = "<tool_response>\n"
                        tool_response += json.dumps({
                            "tool_call_id": tool_msg.get("tool_call_id", ""),
                            "name": msg["tool_calls"][len(tool_responses)]["function"]["name"] \
                                if len(tool_responses) < len(msg["tool_calls"]) else "unknown",
                            "content": tool_content
                        }, ensure_ascii=False)
                        tool_response += "\n</tool_response>"
                        tool_responses.append(tool_response)
                        j += 1

                    if tool_responses:
                        trajectory.append({"from": "tool", "value": "\n".join(tool_responses)})
                        i = j - 1

                else:
                    # 常规助手消息（无工具调用）
                    content = ""
                    if msg.get("reasoning"):
                        content = f"<think>{msg['reasoning']}\n</think>\n"
                    content += msg.get("content") or ""
                    trajectory.append({"from": "gpt", "value": content})

            elif msg["role"] == "user":
                trajectory.append({"from": "human", "value": msg["content"]})

            i += 1

        return trajectory

    def run_task(self, task: str) -> Dict[str, Any]:
        """
        运行单个任务并返回带轨迹的结果。

        参数：
            task: 要执行的任务/提示

        返回：
            包含轨迹、完成状态和元数据的字典
        """
        print(f"\n{'='*60}")
        print(f"📝 Task: {task[:80]}{'...' if len(task) > 80 else ''}")
        print(f"{'='*60}")

        # 初始化环境
        self._create_env()

        # 消息历史
        messages = [{"role": "user", "content": task}]

        # LLM 的系统提示（临时的 — 不保存到轨迹）
        system_prompt = """You are an AI agent that can execute bash commands to complete tasks.

When you need to run commands, use the 'terminal' tool with your bash command.

**Important:**
- When you have completed the task successfully, run: echo "MINI_SWE_AGENT_FINAL_OUTPUT" followed by a summary
- Be concise and efficient in your approach
- Install any needed tools with apt-get or pip
- Avoid interactive commands (no vim, nano, less, etc.)

Complete the user's task step by step."""

        api_call_count = 0
        completed = False
        final_response = None

        try:
            while api_call_count < self.max_iterations:
                api_call_count += 1
                print(f"\n🔄 API call #{api_call_count}/{self.max_iterations}")

                # 准备 API 消息
                api_messages = [{"role": "system", "content": system_prompt}] + messages

                # 发起 API 调用
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=api_messages,
                        tools=self.tools,
                        timeout=300.0
                    )
                except Exception as e:
                    self.logger.error(f"API 调用失败: {e}")
                    break

                assistant_message = response.choices[0].message

                # 记录助手响应
                if assistant_message.content:
                    print(f"🤖 Assistant: {assistant_message.content[:100]}...")

                # 检查工具调用
                if assistant_message.tool_calls:
                    print(f"🔧 Tool calls: {len(assistant_message.tool_calls)}")

                    # 添加带工具调用的助手消息
                    messages.append({
                        "role": "assistant",
                        "content": assistant_message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in assistant_message.tool_calls
                        ]
                    })

                    # 执行每个工具调用
                    for tc in assistant_message.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}

                        command = args.get("command", "echo 'No command provided'")
                        timeout = args.get("timeout", self.command_timeout)

                        print(f"   📞 terminal: {command[:60]}...")

                        # 执行命令
                        result = self._execute_command(command, timeout)

                        # 格式化结果
                        result_json = json.dumps({
                            "content": {
                                "output": result["output"],
                                "exit_code": result["exit_code"],
                                "error": result["error"]
                            }
                        }, ensure_ascii=False)

                        # 检查任务完成信号
                        if "MINI_SWE_AGENT_FINAL_OUTPUT" in result["output"]:
                            print("   ✅ 任务完成信号已检测！")
                            completed = True

                        #添加工具响应
                        messages.append({
                            "role": "tool",
                            "content": result_json,
                            "tool_call_id": tc.id
                        })

                        print(f"   ✅ exit_code={result['exit_code']}, output={len(result['output'])} chars")

                    # 如果任务完成，我们可以停止
                    if completed:
                        final_response = assistant_message.content
                        break

                else:
                    # 无工具调用 — 最终响应
                    final_response = assistant_message.content or ""
                    messages.append({
                        "role": "assistant",
                        "content": final_response
                    })
                    completed = True
                    print("🎉 Agent 完成（无更多工具调用）")
                    break

            if api_call_count >= self.max_iterations:
                print(f"⚠️  达到最大迭代次数 ({self.max_iterations})")

        finally:
            # 清理环境
            self._cleanup_env()

        # 转换为 KClaw 轨迹格式
        trajectory = self._convert_to_kclaw_format(messages, task, completed)

        return {
            "conversations": trajectory,
            "completed": completed,
            "api_calls": api_call_count,
            "metadata": {
                "model": self.model,
                "env_type": self.env_type,
                "timestamp": datetime.now().isoformat()
            }
        }

    def run_batch(
        self,
        prompts: List[str],
        output_file: str
    ) -> List[Dict[str, Any]]:
        """
        运行多个任务并将轨迹保存到 JSONL 文件。

        参数：
            prompts: 任务提示列表
            output_file: 输出 JSONL 文件路径

        返回：
            结果列表
        """
        results = []

        print(f"\n📦 正在运行 {len(prompts)} 个任务的批次")
        print(f"📁 Output: {output_file}")

        with open(output_file, 'w', encoding='utf-8') as f:
            for i, prompt in enumerate(prompts, 1):
                print(f"\n{'='*60}")
                print(f"📋 Task {i}/{len(prompts)}")
                print(f"{'='*60}")

                try:
                    result = self.run_task(prompt)
                    results.append(result)

                    # 立即写入文件
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                    f.flush()

                    print(f"✅ Task {i} 完成 (api_calls={result['api_calls']})")

                except Exception as e:
                    self.logger.error(f"Task {i} 出错: {e}")
                    error_result = {
                        "conversations": [],
                        "completed": False,
                        "api_calls": 0,
                        "error": str(e),
                        "metadata": {"timestamp": datetime.now().isoformat()}
                    }
                    results.append(error_result)
                    f.write(json.dumps(error_result, ensure_ascii=False) + "\n")
                    f.flush()

        print(f"\n✅ 批次完成！{len(results)} 个轨迹已保存到 {output_file}")
        return results


# ============================================================================
# CLI 接口
# ============================================================================

def main(
    task: str = None,
    prompts_file: str = None,
    output_file: str = "swe-runner-test1.jsonl",
    model: str = "claude-sonnet-4-20250514",
    base_url: str = None,
    api_key: str = None,
    env: str = "local",
    image: str = "python:3.11-slim",
    cwd: str = "/tmp",
    max_iterations: int = 15,
    timeout: int = 60,
    verbose: bool = False,
):
    """
    使用 KClaw 轨迹格式输出运行 SWE 任务。

    参数：
        task: 要运行的单个任务（使用此参数或 prompts_file）
        prompts_file: 包含提示的 JSONL 文件（每行：{"prompt": "..."}）
        output_file: 轨迹输出 JSONL 文件
        model: 模型名称（默认：claude-sonnet-4-20250514）
        base_url: API 基础 URL（可选）
        api_key: API 密钥（可选，使用环境变量）
        env: 环境类型 - "local"、"docker" 或 "modal"
        image: Docker/Modal 镜像（默认：python:3.11-slim）
        cwd: 工作目录（默认：/tmp）
        max_iterations: 最大工具调用迭代次数（默认：15）
        timeout: 命令超时秒数（默认：60）
        verbose: 启用详细日志

    示例：
        # 使用本地环境的单个任务
        python mini_swe_runner.py --task "Create hello.py that prints Hello World"

        # 使用 Docker 的单个任务
        python mini_swe_runner.py --task "List files" --env docker

        # 从文件批量运行
        python mini_swe_runner.py --prompts_file tasks.jsonl --output_file results.jsonl
    """
    print("🚀 Mini-SWE Runner with KClaw Trajectory Format")
    print("=" * 60)

    # 初始化 runner
    runner = MiniSWERunner(
        model=model,
        base_url=base_url,
        api_key=api_key,
        env_type=env,
        image=image,
        cwd=cwd,
        max_iterations=max_iterations,
        command_timeout=timeout,
        verbose=verbose,
    )

    if task:
        # 单任务模式
        result = runner.run_task(task)

        # 保存到文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

        print(f"\n📁 轨迹已保存到: {output_file}")
        print(f"✅ 已完成: {result['completed']}")
        print(f"📞 API 调用: {result['api_calls']}")
        print(f"💬 对话轮次: {len(result['conversations'])}")

    elif prompts_file:
        # 批量模式
        prompts = []
        with open(prompts_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        prompts.append(entry.get("prompt", entry.get("task", "")))
                    except json.JSONDecodeError:
                        prompts.append(line)

        if not prompts:
            print(f"❌ 在 {prompts_file} 中未找到提示")
            return

        runner.run_batch(prompts, output_file)

    else:
        print("❌ 请提供 --task 或 --prompts_file")
        print("   示例：python mini_swe_runner.py --task 'Create a hello world script'")


if __name__ == "__main__":
    fire.Fire(main)
