#!/usr/bin/env python3
"""
RL 训练 CLI 运行器

专用于 RL 训练工作流的 CLI 运行器，具备：
- 针对长时间运行训练的扩展超时
- RL 专用系统提示词
- 包含 RL 训练工具的完整工具集
- 30 分钟检查间隔的特殊处理

用法：
    python rl_cli.py "在 GSM8k 上训练数学推理模型"
    python rl_cli.py --interactive
    python rl_cli.py --list-environments

环境变量：
    TINKER_API_KEY: Tinker 服务的 API 密钥（必需）
    WANDB_API_KEY: WandB 指标的 API 密钥（必需）
    OPENROUTER_API_KEY: OpenRouter 的 API 密钥（agent 必需）
"""

import asyncio
import os
import sys
from pathlib import Path

import fire
import yaml

# 首先加载 ~/.kclaw/.env，然后加载项目根目录作为开发回退。
# 用户管理的环境文件应在重启时覆盖过期的 shell 导出。
_kclaw_home = get_kclaw_home()
_project_env = Path(__file__).parent / '.env'

from kclaw_cli.env_loader import load_kclaw_dotenv

_loaded_env_paths = load_kclaw_dotenv(kclaw_home=_kclaw_home, project_env=_project_env)
for _env_path in _loaded_env_paths:
    print(f"✅ 已从 {_env_path} 加载环境变量")

# 设置终端工作目录为 tinker-atropos 子模块
# 这确保终端命令在 RL 工作的正确上下文中运行
tinker_atropos_dir = Path(__file__).parent / 'tinker-atropos'
if tinker_atropos_dir.exists():
    os.environ['TERMINAL_CWD'] = str(tinker_atropos_dir)
    os.environ['KCLAW_QUIET'] = '1'  # 禁用临时子目录创建
    print(f"📂 终端工作目录: {tinker_atropos_dir}")
else:
    # 如果未找到子模块，回退到 kclaw 目录
    os.environ['TERMINAL_CWD'] = str(Path(__file__).parent)
    os.environ['KCLAW_QUIET'] = '1'
    print(f"⚠️  未找到 tinker-atropos 子模块，使用: {Path(__file__).parent}")

# 导入 agent 和工具
from run_agent import AIAgent
from tools.rl_training_tool import get_missing_keys


# ============================================================================
# 配置加载
# ============================================================================

from kclaw_constants import get_kclaw_home, OPENROUTER_BASE_URL

DEFAULT_MODEL = "anthropic/claude-opus-4.5"
DEFAULT_BASE_URL = OPENROUTER_BASE_URL


def load_kclaw_config() -> dict:
    """
    从 ~/.kclaw/config.yaml 加载配置。

    返回：
        dict: 包含 model、base_url 等的配置字典。
    """
    config_path = _kclaw_home / 'config.yaml'
    
    config = {
        "model": DEFAULT_MODEL,
        "base_url": DEFAULT_BASE_URL,
    }
    
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                file_config = yaml.safe_load(f) or {}
            
            # 从配置中获取模型
            if "model" in file_config:
                if isinstance(file_config["model"], str):
                    config["model"] = file_config["model"]
                elif isinstance(file_config["model"], dict):
                    config["model"] = file_config["model"].get("default", DEFAULT_MODEL)
            
            # 如果指定了 base_url 则获取
            if "base_url" in file_config:
                config["base_url"] = file_config["base_url"]
                
        except Exception as e:
            print(f"⚠️ 警告: 加载 config.yaml 失败: {e}")
    
    return config


# ============================================================================
# RL 专用配置
# ============================================================================

# 长时间运行的 RL 操作的扩展超时
RL_MAX_ITERATIONS = 200  # 允许更多迭代以适应长时间工作流

# RL 专用系统提示词
RL_SYSTEM_PROMPT = """你是一名专注于大语言模型强化学习的自动后训练工程师。

## 你的能力

你可以通过 Tinker-Atropos 使用 RL 训练工具对模型运行强化学习：

1. **发现**：使用 `rl_list_environments` 查看可用的 RL 环境
2. **检查**：阅读环境文件以了解其工作方式（验证器、数据加载、奖励）
3. **检查数据**：使用终端探索 HuggingFace 数据集并了解其格式
4. **创建**：复制现有环境作为模板，根据需要进行修改
5. **配置**：使用 `rl_select_environment` 和 `rl_edit_config` 设置训练
6. **测试**：在完整训练之前始终使用 `rl_test_inference` 验证你的设置
7. **训练**：使用 `rl_start_training` 开始训练，`rl_check_status` 监控进度
8. **评估**：使用 `rl_get_results` 并分析 WandB 指标来评估性能

## 环境文件

环境文件位于：`tinker-atropos/tinker_atropos/environments/`

研究现有环境以学习模式。关注：
- `load_dataset()` 调用 - 数据如何加载
- `score_answer()` / `score()` - 验证逻辑
- `get_next_item()` - 提示词格式
- `system_prompt` - 指令格式
- `config_init()` - 默认配置

## 创建新环境

创建新环境的步骤：
1. 阅读现有环境文件（例如 gsm8k_tinker.py）
2. 使用终端探索目标数据集格式
3. 复制环境文件作为模板
4. 修改数据集加载、提示词格式和验证器逻辑
5. 训练前用 `rl_test_inference` 测试

## 重要指南

- **训练前务必测试**：训练运行需要数小时 - 先验证一切正常
- **监控指标**：检查 WandB 的 reward/mean 和 percent_correct
- **状态检查间隔**：状态检查之间至少等待 30 分钟
- **提前停止**：如果指标表现不佳或停滞，提前停止训练
- **快速迭代**：从小 total_steps 开始验证，然后扩大规模

## 可用工具集

你可以使用：
- **RL 工具**：环境发现、配置管理、训练、测试
- **终端**：运行命令、检查文件、探索数据集
- **Web**：搜索信息、文档、论文
- **文件工具**：读取和修改代码文件

当被要求训练模型时，遵循以下工作流：
1. 列出可用环境
2. 选择并配置适当的环境
3. 使用示例提示词测试
4. 以保守设置开始训练
5. 监控进度并根据需要调整
"""

# RL 工作流启用的工具集
RL_TOOLSETS = ["terminal", "web", "rl"]


# ============================================================================
# 辅助函数
# ============================================================================

def check_requirements():
    """检查所有必需的环境变量和服务是否可用。"""
    errors = []
    
    # 检查 API 密钥
    if not os.getenv("OPENROUTER_API_KEY"):
        errors.append("OPENROUTER_API_KEY 未设置 - agent 必需")
    
    missing_rl_keys = get_missing_keys()
    if missing_rl_keys:
        errors.append(f"缺少 RL API 密钥: {', '.join(missing_rl_keys)}")
    
    if errors:
        print("❌ 缺少必要条件:")
        for error in errors:
            print(f"   - {error}")
        print("\n请在 .env 文件或 shell 中设置这些环境变量。")
        return False
    
    return True


def check_tinker_atropos():
    """检查 tinker-atropos 子模块是否已正确设置。"""
    tinker_path = Path(__file__).parent / "tinker-atropos"
    
    if not tinker_path.exists():
        return False, "未找到 tinker-atropos 子模块。请运行: git submodule update --init"
    
    envs_path = tinker_path / "tinker_atropos" / "environments"
    if not envs_path.exists():
        return False, f"未在 {envs_path} 找到 environments 目录"
    
    env_files = list(envs_path.glob("*.py"))
    env_files = [f for f in env_files if not f.name.startswith("_")]
    
    return True, {"path": str(tinker_path), "environments_count": len(env_files)}


def list_environments_sync():
    """列出可用环境（同步包装器）。"""
    from tools.rl_training_tool import rl_list_environments
    import json
    
    async def _list():
        result = await rl_list_environments()
        return json.loads(result)
    
    return asyncio.run(_list())


# ============================================================================
# 主 CLI
# ============================================================================

def main(
    task: str = None,
    model: str = None,
    api_key: str = None,
    base_url: str = None,
    max_iterations: int = RL_MAX_ITERATIONS,
    interactive: bool = False,
    list_environments: bool = False,
    check_server: bool = False,
    verbose: bool = False,
    save_trajectories: bool = True,
):
    """
    RL 训练 CLI - 专用于 RL 训练工作流的运行器。

    参数：
        task: 训练任务/目标（例如 "在 GSM8k 上训练数学模型"）
        model: agent 使用的模型（未提供时从 ~/.kclaw/config.yaml 读取）
        api_key: OpenRouter API 密钥（未提供时使用 OPENROUTER_API_KEY 环境变量）
        base_url: API 基础 URL（从配置读取或默认为 OpenRouter）
        max_iterations: 最大 agent 迭代次数（默认: 200，用于长时间工作流）
        interactive: 以交互模式运行（多轮对话）
        list_environments: 仅列出可用的 RL 环境并退出
        check_server: 检查 RL API 服务器是否运行并退出
        verbose: 启用详细日志
        save_trajectories: 保存对话轨迹（RL 默认: True）

    示例：
        # 在特定环境上训练
        python rl_cli.py "在 GSM8k 数学问题上训练模型"

        # 交互模式
        python rl_cli.py --interactive

        # 列出可用环境
        python rl_cli.py --list-environments

        # 检查服务器状态
        python rl_cli.py --check-server
    """
    # 从 ~/.kclaw/config.yaml 加载配置
    config = load_kclaw_config()
    
    # 未显式提供时使用配置值
    if model is None:
        model = config["model"]
    if base_url is None:
        base_url = config["base_url"]
    
    print("🎯 RL 训练 Agent")
    print("=" * 60)
    
    # 处理设置检查
    if check_server:
        print("\n🔍 正在检查 tinker-atropos 设置...")
        ok, result = check_tinker_atropos()
        if ok:
            print("✅ 已找到 tinker-atropos 子模块")
            print(f"   路径: {result.get('path')}")
            print(f"   发现的环境数: {result.get('environments_count', 0)}")
            
            # 同时检查 API 密钥
            missing = get_missing_keys()
            if missing:
                print(f"\n⚠️  缺少 API 密钥: {', '.join(missing)}")
                print("   请添加到 ~/.kclaw/.env")
            else:
                print("✅ API 密钥已配置")
        else:
            print(f"❌ tinker-atropos 未设置: {result}")
            print("\n设置步骤:")
            print("  git submodule update --init")
            print("  pip install -e ./tinker-atropos")
        return
    
    # 处理环境列表
    if list_environments:
        print("\n📋 可用的 RL 环境:")
        print("-" * 40)
        try:
            data = list_environments_sync()
            if "error" in data:
                print(f"❌ 错误: {data['error']}")
                return
            
            envs = data.get("environments", [])
            if not envs:
                print("未找到环境。")
                print("\n请确保 tinker-atropos 已设置:")
                print("  git submodule update --init")
                return
            
            for env in envs:
                print(f"\n  📦 {env['name']}")
                print(f"     类名: {env['class_name']}")
                print(f"     路径: {env['file_path']}")
                if env.get('description'):
                    desc = env['description'][:100] + "..." if len(env.get('description', '')) > 100 else env.get('description', '')
                    print(f"     描述: {desc}")
            
            print(f"\n📊 共计: {len(envs)} 个环境")
            print("\n使用 `rl_select_environment(name)` 选择环境进行训练。")
        except Exception as e:
            print(f"❌ 列出环境时出错: {e}")
            print("\n请确保 tinker-atropos 已设置:")
            print("  git submodule update --init")
            print("  pip install -e ./tinker-atropos")
        return
    
    # 检查必要条件
    if not check_requirements():
        sys.exit(1)
    
    # 未提供任务时设置默认值
    if not task and not interactive:
        print("\n⚠️  未提供任务。使用 --interactive 进入交互模式或提供任务。")
        print("\n示例:")
        print('  python rl_cli.py "在 GSM8k 数学问题上训练模型"')
        print('  python rl_cli.py "创建用于代码生成的 RL 环境"')
        print('  python rl_cli.py --interactive')
        return
    
    # 获取 API 密钥
    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ 未提供 API 密钥。请设置 OPENROUTER_API_KEY 或传入 --api-key")
        sys.exit(1)
    
    print(f"\n🤖 模型: {model}")
    print(f"🔧 最大迭代次数: {max_iterations}")
    print(f"📁 工具集: {', '.join(RL_TOOLSETS)}")
    print("=" * 60)
    
    # 使用 RL 配置创建 agent
    agent = AIAgent(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_iterations=max_iterations,
        enabled_toolsets=RL_TOOLSETS,
        save_trajectories=save_trajectories,
        verbose_logging=verbose,
        quiet_mode=False,
        ephemeral_system_prompt=RL_SYSTEM_PROMPT,
    )
    
    if interactive:
        # 交互模式 - 多轮对话
        print("\n🔄 交互式 RL 训练模式")
        print("输入 'quit' 或 'exit' 结束会话。")
        print("输入 'status' 检查活跃的训练运行。")
        print("-" * 40)
        
        while True:
            try:
                user_input = input("\n🎯 RL Task> ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() in ('quit', 'exit', 'q'):
                    print("\n👋 再见！")
                    break
                
                if user_input.lower() == 'status':
                    # 快速状态检查
                    from tools.rl_training_tool import rl_list_runs
                    import json
                    result = asyncio.run(rl_list_runs())
                    runs = json.loads(result)
                    if isinstance(runs, list) and runs:
                        print("\n📊 活跃运行:")
                        for run in runs:
                            print(f"  - {run['run_id']}: {run['environment']} ({run['status']})")
                    else:
                        print("\n没有活跃运行。")
                    continue
                
                # 运行 agent
                print("\n" + "=" * 60)
                response = agent.run_conversation(user_input)
                print("\n" + "=" * 60)
                
            except KeyboardInterrupt:
                print("\n\n👋 已中断。再见！")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                if verbose:
                    import traceback
                    traceback.print_exc()
    else:
        # 单任务模式
        print(f"\n📝 任务: {task}")
        print("-" * 40)
        
        try:
            response = agent.run_conversation(task)
            print("\n" + "=" * 60)
            print("✅ 任务完成")
        except KeyboardInterrupt:
            print("\n\n⚠️ 被用户中断")
        except Exception as e:
            print(f"\n❌ 错误: {e}")
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    fire.Fire(main)
