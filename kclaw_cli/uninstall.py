"""
KClaw Agent 卸载程序。

提供以下选项：
- 保留数据卸载：仅移除代码，保留 ~/.kclaw/（配置、会话、日志）
- 完全卸载：移除所有内容，包括配置和数据
- 额外清理：移除通过 pip/conda 安装的 kclaw 包
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from kclaw_constants import get_kclaw_home

from kclaw_cli.colors import Colors, color

def log_info(msg: str):
    print(f"{color('→', Colors.CYAN)} {msg}")

def log_success(msg: str):
    print(f"{color('✓', Colors.GREEN)} {msg}")

def log_warn(msg: str):
    print(f"{color('⚠', Colors.YELLOW)} {msg}")

def log_error(msg: str):
    print(f"{color('✗', Colors.RED)} {msg}")

def is_install_directory(path: Path) -> bool:
    """判断路径是否为标准安装目录（~/.kclaw/kclaw/）而非开发源码目录。"""
    kclaw_home = get_kclaw_home()
    # 标准安装目录位于 ~/.kclaw/ 下
    if kclaw_home in path.parents or path.parent == kclaw_home:
        return True
    # 检查是否包含 .git 且有 pyproject.toml（典型的开发源码目录特征）
    if (path / ".git").exists() and (path / "pyproject.toml").exists():
        return False
    # 检查常见开发路径模式
    dev_patterns = ["/Projects/", "/workspace/", "/dev/", "/code/", "/src/"]
    for pattern in dev_patterns:
        if pattern in str(path):
            return False
    return True


def get_install_directory() -> Path | None:
    """获取标准安装目录路径（如果存在）。"""
    kclaw_home = get_kclaw_home()
    install_dir = kclaw_home / "kclaw"
    if install_dir.exists() and (install_dir / ".git").exists():
        return install_dir
    return None


def find_shell_configs() -> list:
    """查找可能包含 PATH 条目的 shell 配置文件。"""
    home = Path.home()
    configs = []
    
    candidates = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]
    
    for config in candidates:
        if config.exists():
            configs.append(config)
    
    return configs


def remove_path_from_shell_configs():
    """从 shell 配置文件中移除 KClaw PATH 条目。"""
    configs = find_shell_configs()
    removed_from = []
    
    for config_path in configs:
        try:
            content = config_path.read_text()
            original_content = content
            
            # 移除包含 kclaw 或 kclaw PATH 条目的行
            new_lines = []
            skip_next = False
            
            for line in content.split('\n'):
                # 跳过 "# KClaw Agent" 注释及其下一行
                if '# KClaw Agent' in line or '# kclaw' in line:
                    skip_next = True
                    continue
                if skip_next and ('kclaw' in line.lower() and 'PATH' in line):
                    skip_next = False
                    continue
                skip_next = False
                
                # 移除任何包含 kclaw 的 PATH 行
                if 'kclaw' in line.lower() and ('PATH=' in line or 'path=' in line.lower()):
                    continue
                    
                new_lines.append(line)
            
            new_content = '\n'.join(new_lines)
            
            # 清理多余的空行
            while '\n\n\n' in new_content:
                new_content = new_content.replace('\n\n\n', '\n\n')
            
            if new_content != original_content:
                config_path.write_text(new_content)
                removed_from.append(config_path)
                
        except Exception as e:
            log_warn(f"无法更新 {config_path}: {e}")
    
    return removed_from


def remove_wrapper_script():
    """移除 kclaw 包装脚本（如果存在）。"""
    wrapper_paths = [
        Path.home() / ".local" / "bin" / "kclaw",
        Path("/usr/local/bin/kclaw"),
    ]
    
    removed = []
    for wrapper in wrapper_paths:
        if wrapper.exists() or wrapper.is_symlink():
            try:
                # 检查是否是我们的包装脚本（包含 kclaw_cli 引用）
                if wrapper.is_symlink() or wrapper.exists():
                    try:
                        content = wrapper.read_text()
                        if 'kclaw_cli' in content or 'kclaw' in content:
                            wrapper.unlink()
                            removed.append(wrapper)
                    except Exception:
                        # 符号链接目标不存在时读取会失败，直接删除
                        wrapper.unlink()
                        removed.append(wrapper)
            except Exception as e:
                log_warn(f"无法移除 {wrapper}: {e}")
    
    return removed


def find_pip_installed_kclaw() -> list:
    """查找通过 pip/conda 安装的 kclaw 入口点。"""
    found = []
    
    # 方法1：通过 which/where 查找
    try:
        result = subprocess.run(
            ["which", "-a", "kclaw"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            for path_str in result.stdout.strip().split('\n'):
                path = Path(path_str.strip())
                if path.exists() and path not in [Path.home() / ".local" / "bin" / "kclaw"]:
                    # 读取内容确认是 pip 安装的入口点
                    try:
                        content = path.read_text()
                        if 'kclaw_cli' in content:
                            found.append(("entry_point", path))
                    except Exception:
                        pass
    except Exception:
        pass
    
    # 方法2：通过 pip show 查找
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "kclaw"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and "Name: kclaw" in result.stdout:
            for line in result.stdout.split('\n'):
                if line.startswith("Location:"):
                    location = line.split(":", 1)[1].strip()
                    found.append(("pip_package", Path(location)))
                    break
    except Exception:
        pass
    
    # 方法3：检查 conda 环境
    try:
        result = subprocess.run(
            ["conda", "list", "kclaw"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and "kclaw" in result.stdout:
            for line in result.stdout.split('\n'):
                if "kclaw" in line and not line.startswith("#"):
                    found.append(("conda_package", line.strip()))
                    break
    except Exception:
        pass
    
    return found


def uninstall_pip_package():
    """通过 pip 卸载 kclaw 包。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "kclaw", "-y"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and "Successfully uninstalled" in result.stdout:
            log_success("已通过 pip 卸载 kclaw 包")
            return True
        elif "Cannot uninstall requirement" in result.stdout or "not installed" in result.stdout:
            log_info("pip 中未找到 kclaw 包")
            return False
        else:
            log_warn("pip 卸载结果未确认")
            return False
    except Exception as e:
        log_warn(f"pip 卸载失败: {e}")
        return False


def remove_stale_entry_points():
    """移除残留的 kclaw 入口点脚本。"""
    removed = []
    
    # 常见的入口点位置
    search_dirs = []
    
    # 当前 Python 的 Scripts/bin 目录
    python_parent = Path(sys.executable).parent
    search_dirs.append(python_parent)
    
    # conda 环境
    if "conda" in str(python_parent) or "miniconda" in str(python_parent) or "anaconda" in str(python_parent):
        search_dirs.append(python_parent)
    
    # 用户 site-packages 的 bin 目录
    try:
        result = subprocess.run(
            [sys.executable, "-m", "site", "--user-base"],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            user_base = Path(result.stdout.strip())
            search_dirs.append(user_base / "bin")
    except Exception:
        pass
    
    for search_dir in search_dirs:
        kclaw_script = search_dir / "kclaw"
        if kclaw_script.exists():
            try:
                content = kclaw_script.read_text()
                if 'kclaw_cli' in content:
                    kclaw_script.unlink()
                    removed.append(kclaw_script)
                    log_success(f"已移除入口点: {kclaw_script}")
            except Exception as e:
                log_warn(f"无法移除 {kclaw_script}: {e}")
    
    return removed


def uninstall_gateway_service():
    """停止并卸载网关服务（如果正在运行）。"""
    import platform
    
    if platform.system() != "Linux":
        return False
    
    try:
        from kclaw_cli.gateway import get_service_name
        svc_name = get_service_name()
    except Exception:
        svc_name = "kclaw-gateway"

    service_file = Path.home() / ".config" / "systemd" / "user" / f"{svc_name}.service"
    
    if not service_file.exists():
        return False
    
    try:
        # 停止服务
        subprocess.run(
            ["systemctl", "--user", "stop", svc_name],
            capture_output=True,
            check=False
        )
        
        # 禁用服务
        subprocess.run(
            ["systemctl", "--user", "disable", svc_name],
            capture_output=True,
            check=False
        )
        
        # 移除服务文件
        service_file.unlink()
        
        # 重载 systemd
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False
        )
        
        return True
        
    except Exception as e:
        log_warn(f"无法完全移除网关服务: {e}")
        return False


def run_uninstall(args):
    """
    运行卸载过程。
    
    选项：
    - 保留数据卸载：移除代码，保留 ~/.kclaw/ 的配置和会话数据
    - 完全卸载：移除所有内容，包括 ~/.kclaw/ 的所有数据
    """
    source_root = Path(__file__).parent.parent.resolve()
    kclaw_home = get_kclaw_home()
    install_dir = get_install_directory()
    is_dev_dir = not is_install_directory(source_root)
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.MAGENTA, Colors.BOLD))
    print(color("│            ⚕ KClaw Agent 卸载程序                     │", Colors.MAGENTA, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.MAGENTA, Colors.BOLD))
    print()
    
    # 显示受影响的内容
    print(color("当前安装信息:", Colors.CYAN, Colors.BOLD))
    
    if is_dev_dir:
        print(f"  源码目录:  {source_root} " + color("(开发目录 — 不会自动删除)", Colors.YELLOW))
    else:
        print(f"  安装目录:  {source_root}")
    
    if install_dir and install_dir != source_root:
        print(f"  安装目录:  {install_dir}")
    
    print(f"  配置文件:  {kclaw_home / 'config.yaml'}")
    print(f"  密钥文件:  {kclaw_home / '.env'}")
    print(f"  数据目录:  {kclaw_home / 'cron/'}, {kclaw_home / 'sessions/'}, {kclaw_home / 'logs/'}")
    
    # 检测 pip/conda 安装
    pip_installs = find_pip_installed_kclaw()
    if pip_installs:
        print()
        print(color("检测到其他安装方式:", Colors.YELLOW, Colors.BOLD))
        for install_type, location in pip_installs:
            if install_type == "entry_point":
                print(f"  入口点:    {location}")
            elif install_type == "pip_package":
                print(f"  pip 包:    {location}")
            elif install_type == "conda_package":
                print(f"  conda 包:  {location}")
    
    # 开发目录保护提示
    if is_dev_dir:
        print()
        print(color("⚠️  检测到当前运行在开发源码目录中!", Colors.YELLOW, Colors.BOLD))
        print(color("   源码目录将不会被删除，仅清理运行环境。", Colors.YELLOW))
    
    print()
    
    # 确认卸载方式
    print(color("卸载选项:", Colors.YELLOW, Colors.BOLD))
    print()
    print("  1) " + color("保留数据", Colors.GREEN) + " - 仅清理运行环境，保留配置/会话/日志")
    print("     (推荐 — 重新安装后可恢复原有设置)")
    print()
    print("  2) " + color("完全卸载", Colors.RED) + " - 清理运行环境 + 删除所有数据")
    print("     (警告: 将永久删除所有配置、会话和日志)")
    print()
    print("  3) " + color("取消", Colors.CYAN) + " - 不执行卸载")
    print()
    
    try:
        choice = input(color("请选择 [1/2/3]: ", Colors.BOLD)).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("已取消。")
        return
    
    if choice == "3" or choice.lower() in ("c", "cancel", "q", "quit", "n", "no"):
        print()
        print("卸载已取消。")
        return
    
    full_uninstall = (choice == "2")
    
    # 确定要删除的代码目录
    code_dirs_to_remove = []
    
    if install_dir and install_dir.exists():
        code_dirs_to_remove.append(install_dir)
    
    # 只有非开发目录才考虑删除源码目录本身
    if not is_dev_dir and source_root.exists():
        if source_root not in code_dirs_to_remove:
            code_dirs_to_remove.append(source_root)
    elif is_dev_dir and source_root.exists():
        # 开发目录：询问是否也删除（默认不删除）
        print()
        print(color("源码目录:", Colors.CYAN), f"{source_root}")
        try:
            del_dev = input(color("是否也删除源码目录? [y/N]: ", Colors.BOLD)).strip().lower()
        except (KeyboardInterrupt, EOFError):
            del_dev = "n"
        if del_dev in ("y", "yes"):
            code_dirs_to_remove.append(source_root)
        else:
            log_info(f"已保留源码目录: {source_root}")
    
    # 最终确认
    print()
    if full_uninstall:
        print(color("⚠️  警告: 这将永久删除所有 KClaw 数据!", Colors.RED, Colors.BOLD))
        print(color("   包括: 配置、API 密钥、会话、定时任务、日志", Colors.RED))
    else:
        print("这将清理 KClaw 运行环境，但保留您的配置和数据。")
    
    if code_dirs_to_remove:
        print()
        print(color("将删除以下代码目录:", Colors.YELLOW))
        for d in code_dirs_to_remove:
            print(f"  - {d}")
    
    print()
    try:
        confirm = input(f"输入 '{color('yes', Colors.YELLOW)}' 确认: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        print("已取消。")
        return
    
    if confirm != "yes":
        print()
        print("卸载已取消。")
        return
    
    print()
    print(color("正在卸载...", Colors.CYAN, Colors.BOLD))
    print()
    
    # 1. 停止并卸载网关服务
    log_info("正在检查网关服务...")
    if uninstall_gateway_service():
        log_success("网关服务已停止并移除")
    else:
        log_info("未找到网关服务")
    
    # 2. 从 shell 配置中移除 PATH 条目
    log_info("正在从 shell 配置中移除 PATH 条目...")
    removed_configs = remove_path_from_shell_configs()
    if removed_configs:
        for config in removed_configs:
            log_success(f"已更新 {config}")
    else:
        log_info("未找到需要移除的 PATH 条目")
    
    # 3. 移除包装脚本
    log_info("正在移除 kclaw 命令...")
    removed_wrappers = remove_wrapper_script()
    if removed_wrappers:
        for wrapper in removed_wrappers:
            log_success(f"已移除 {wrapper}")
    else:
        log_info("未找到包装脚本")
    
    # 4. 卸载 pip/conda 安装的包
    log_info("正在检查 pip/conda 安装...")
    if pip_installs:
        log_info("检测到通过 pip/conda 安装的 kclaw 包，正在卸载...")
        if uninstall_pip_package():
            # 清理残留的入口点
            remove_stale_entry_points()
        else:
            log_info("如需手动卸载 pip 包，请运行:")
            log_info("  pip uninstall kclaw")
    else:
        log_info("未检测到 pip/conda 安装")
    
    # 5. 移除代码目录
    if code_dirs_to_remove:
        for code_dir in code_dirs_to_remove:
            log_info(f"正在移除代码目录 {code_dir}...")
            try:
                if code_dir.exists():
                    shutil.rmtree(code_dir)
                    log_success(f"已移除 {code_dir}")
            except Exception as e:
                log_warn(f"无法完全移除 {code_dir}: {e}")
                log_info("您可能需要手动删除")
    else:
        log_info("无需移除代码目录")
    
    # 6. 可选移除 ~/.kclaw/ 数据目录
    if full_uninstall:
        log_info("正在移除配置和数据...")
        try:
            if kclaw_home.exists():
                shutil.rmtree(kclaw_home)
                log_success(f"已移除 {kclaw_home}")
        except Exception as e:
            log_warn(f"无法完全移除 {kclaw_home}: {e}")
            log_info("您可能需要手动删除")
    else:
        log_info(f"已保留配置和数据: {kclaw_home}")
    
    # 完成
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.GREEN, Colors.BOLD))
    print(color("│              ✓ 卸载完成!                               │", Colors.GREEN, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.GREEN, Colors.BOLD))
    print()
    
    if not full_uninstall:
        print(color("您的配置和数据已保留:", Colors.CYAN))
        print(f"  {kclaw_home}/")
        print()
        print("使用现有设置重新安装:")
        print(color("  ./scripts/install.sh", Colors.DIM))
        print()
    
    print(color("请重新加载 shell 以完成卸载:", Colors.YELLOW))
    shell_name = Path(os.environ.get("SHELL", "/bin/zsh")).name
    if shell_name == "zsh":
        print("  source ~/.zshrc")
    elif shell_name == "bash":
        print("  source ~/.bashrc")
    else:
        print("  source ~/.bashrc  # 或 ~/.zshrc")
    print()
    print("感谢使用 KClaw Agent! ⚕")
    print()
