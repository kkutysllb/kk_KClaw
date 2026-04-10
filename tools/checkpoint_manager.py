"""
检查点管理器 — 通过影子 git 仓库实现透明的 filesystem 快照。

在文件修改操作（write_file、patch）之前自动创建工作目录快照，
每个对话轮次触发一次。提供回滚到任意先前检查点的功能。

这不是一个工具 — LLM 从不看到它。它是由 ``checkpoints`` 配置标志
或 ``--checkpoints`` CLI 标志控制的透明基础设施。

架构：
    ~/.kclaw/checkpoints/{sha256(abs_dir)[:16]}/   — 影子 git 仓库
        HEAD, refs/, objects/                        — 标准 git 内部结构
        KCLAW_WORKDIR                               — 原始目录路径
        info/exclude                                 — 默认排除项

影子仓库使用 GIT_DIR + GIT_WORK_TREE，因此不会有 git 状态泄漏到
用户项目目录中。
"""

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from kclaw_constants import get_kclaw_home
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CHECKPOINT_BASE = get_kclaw_home() / "checkpoints"

DEFAULT_EXCLUDES = [
    "node_modules/",
    "dist/",
    "build/",
    ".env",
    ".env.*",
    ".env.local",
    ".env.*.local",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.log",
    ".cache/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    ".git/",
]

# Git 子进程超时（秒）。
_GIT_TIMEOUT: int = max(10, min(60, int(os.getenv("KCLAW_CHECKPOINT_TIMEOUT", "30"))))

# 最大快照文件数 — 跳过超大目录以避免减速。
_MAX_FILES = 50_000


# ---------------------------------------------------------------------------
# 影子仓库辅助函数
# ---------------------------------------------------------------------------

def _shadow_repo_path(working_dir: str) -> Path:
    """确定性影子仓库路径：sha256(abs_path)[:16]。"""
    abs_path = str(Path(working_dir).resolve())
    dir_hash = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
    return CHECKPOINT_BASE / dir_hash


def _git_env(shadow_repo: Path, working_dir: str) -> dict:
    """构建重定向 git 到影子仓库的环境字典。"""
    env = os.environ.copy()
    env["GIT_DIR"] = str(shadow_repo)
    env["GIT_WORK_TREE"] = str(Path(working_dir).resolve())
    env.pop("GIT_INDEX_FILE", None)
    env.pop("GIT_NAMESPACE", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    return env


def _run_git(
    args: List[str],
    shadow_repo: Path,
    working_dir: str,
    timeout: int = _GIT_TIMEOUT,
    allowed_returncodes: Optional[Set[int]] = None,
) -> tuple:
    """针对影子仓库运行 git 命令。返回 (ok, stdout, stderr)。

    ``allowed_returncodes`` 抑制已知/预期的非零退出的错误日志，
    同时保留正常的 ``ok = (returncode == 0)`` 约定。
    示例：``git diff --cached --quiet`` 在有变更时返回 1。
    """
    env = _git_env(shadow_repo, working_dir)
    cmd = ["git"] + list(args)
    allowed_returncodes = allowed_returncodes or set()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(Path(working_dir).resolve()),
        )
        ok = result.returncode == 0
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if not ok and result.returncode not in allowed_returncodes:
            logger.error(
                "Git command failed: %s (rc=%d) stderr=%s",
                " ".join(cmd), result.returncode, stderr,
            )
        return ok, stdout, stderr
    except subprocess.TimeoutExpired:
        msg = f"git timed out after {timeout}s: {' '.join(cmd)}"
        logger.error(msg, exc_info=True)
        return False, "", msg
    except FileNotFoundError:
        logger.error("Git executable not found: %s", " ".join(cmd), exc_info=True)
        return False, "", "git not found"
    except Exception as exc:
        logger.error("Unexpected git error running %s: %s", " ".join(cmd), exc, exc_info=True)
        return False, "", str(exc)


def _init_shadow_repo(shadow_repo: Path, working_dir: str) -> Optional[str]:
    """如需要则初始化影子仓库。返回错误字符串或 None。"""
    if (shadow_repo / "HEAD").exists():
        return None

    shadow_repo.mkdir(parents=True, exist_ok=True)

    ok, _, err = _run_git(["init"], shadow_repo, working_dir)
    if not ok:
        return f"Shadow repo init failed: {err}"

    _run_git(["config", "user.email", "kclaw@local"], shadow_repo, working_dir)
    _run_git(["config", "user.name", "KClaw Checkpoint"], shadow_repo, working_dir)

    info_dir = shadow_repo / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "exclude").write_text(
        "\n".join(DEFAULT_EXCLUDES) + "\n", encoding="utf-8"
    )

    (shadow_repo / "KCLAW_WORKDIR").write_text(
        str(Path(working_dir).resolve()) + "\n", encoding="utf-8"
    )

    logger.debug("Initialised checkpoint repo at %s for %s", shadow_repo, working_dir)
    return None


def _dir_file_count(path: str) -> int:
    """快速文件计数估计（如果超过 _MAX_FILES 则提前停止）。"""
    count = 0
    try:
        for _ in Path(path).rglob("*"):
            count += 1
            if count > _MAX_FILES:
                return count
    except (PermissionError, OSError):
        pass
    return count


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------

class CheckpointManager:
    """管理自动文件系统检查点。

    旨在由 AIAgent 拥有。在每个对话轮次开始时调用 ``new_turn()``，
    在任何文件修改工具调用之前调用 ``ensure_checkpoint(dir, reason)``。
    管理器进行去重，因此每个目录每个轮次最多拍摄一个快照。

    参数
    ----------
    enabled : bool
        主开关（来自配置 / CLI 标志）。
    max_snapshots : int
        每个目录最多保留此数量的检查点。
    """

    def __init__(self, enabled: bool = False, max_snapshots: int = 50):
        self.enabled = enabled
        self.max_snapshots = max_snapshots
        self._checkpointed_dirs: Set[str] = set()
        self._git_available: Optional[bool] = None  # lazy probe

    # ------------------------------------------------------------------
    # 轮次生命周期
    # ------------------------------------------------------------------

    def new_turn(self) -> None:
        """重置每轮去重。在每次代理迭代开始时调用。"""
        self._checkpointed_dirs.clear()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def ensure_checkpoint(self, working_dir: str, reason: str = "auto") -> bool:
        """如果启用且本轮尚未完成，则拍摄检查点。

        如果拍摄了检查点则返回 True，否则返回 False。
        不会抛出异常 — 所有错误都被静默记录。
        """
        if not self.enabled:
            return False

        # 延迟 git 探测
        if self._git_available is None:
            self._git_available = shutil.which("git") is not None
            if not self._git_available:
                logger.debug("Checkpoints disabled: git not found")
        if not self._git_available:
            return False

        abs_dir = str(Path(working_dir).resolve())

        # 跳过根目录、主目录和其他过于宽泛的目录
        if abs_dir in ("/", str(Path.home())):
            logger.debug("Checkpoint skipped: directory too broad (%s)", abs_dir)
            return False

        # 本轮是否已检查点？
        if abs_dir in self._checkpointed_dirs:
            return False

        self._checkpointed_dirs.add(abs_dir)

        try:
            return self._take(abs_dir, reason)
        except Exception as e:
            logger.debug("Checkpoint failed (non-fatal): %s", e)
            return False

    def list_checkpoints(self, working_dir: str) -> List[Dict]:
        """列出目录的可用检查点。

        返回包含以下键的字典列表：hash, short_hash, timestamp, reason,
        files_changed, insertions, deletions。按最新优先排序。
        """
        abs_dir = str(Path(working_dir).resolve())
        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return []

        ok, stdout, _ = _run_git(
            ["log", "--format=%H|%h|%aI|%s", "-n", str(self.max_snapshots)],
            shadow, abs_dir,
        )

        if not ok or not stdout:
            return []

        results = []
        for line in stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                entry = {
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "timestamp": parts[2],
                    "reason": parts[3],
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                }
                # 获取此提交的 diffstat
                stat_ok, stat_out, _ = _run_git(
                    ["diff", "--shortstat", f"{parts[0]}~1", parts[0]],
                    shadow, abs_dir,
                    allowed_returncodes={128, 129},  # first commit has no parent
                )
                if stat_ok and stat_out:
                    self._parse_shortstat(stat_out, entry)
                results.append(entry)
        return results

    @staticmethod
    def _parse_shortstat(stat_line: str, entry: Dict) -> None:
        """将 git --shortstat 输出解析为 entry 字典。"""
        import re
        m = re.search(r'(\d+) file', stat_line)
        if m:
            entry["files_changed"] = int(m.group(1))
        m = re.search(r'(\d+) insertion', stat_line)
        if m:
            entry["insertions"] = int(m.group(1))
        m = re.search(r'(\d+) deletion', stat_line)
        if m:
            entry["deletions"] = int(m.group(1))

    def diff(self, working_dir: str, commit_hash: str) -> Dict:
        """显示检查点与当前工作树之间的差异。

        返回包含 success、diff 文本和统计摘要的字典。
        """
        abs_dir = str(Path(working_dir).resolve())
        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return {"success": False, "error": "No checkpoints exist for this directory"}

        # 验证提交是否存在
        ok, _, err = _run_git(
            ["cat-file", "-t", commit_hash], shadow, abs_dir,
        )
        if not ok:
            return {"success": False, "error": f"Checkpoint '{commit_hash}' not found"}

        # 暂存当前状态以与检查点进行比较
        _run_git(["add", "-A"], shadow, abs_dir, timeout=_GIT_TIMEOUT * 2)

        # 获取统计摘要：检查点 vs 当前工作树
        ok_stat, stat_out, _ = _run_git(
            ["diff", "--stat", commit_hash, "--cached"],
            shadow, abs_dir,
        )

        # 获取实际 diff（限制以避免终端泛滥）
        ok_diff, diff_out, _ = _run_git(
            ["diff", commit_hash, "--cached", "--no-color"],
            shadow, abs_dir,
        )

        # 取消暂存以避免污染影子仓库索引
        _run_git(["reset", "HEAD", "--quiet"], shadow, abs_dir)

        if not ok_stat and not ok_diff:
            return {"success": False, "error": "Could not generate diff"}

        return {
            "success": True,
            "stat": stat_out if ok_stat else "",
            "diff": diff_out if ok_diff else "",
        }

    def restore(self, working_dir: str, commit_hash: str, file_path: str = None) -> Dict:
        """将文件恢复到检查点状态。

        使用 ``git checkout <hash> -- .``（或特定文件），可恢复跟踪的文件
        而不移动 HEAD — 安全且可逆。

        参数
        ----------
        file_path : str, optional
            如果提供，则仅恢复此文件而非整个目录。

        返回包含成功/错误信息的字典。
        """
        abs_dir = str(Path(working_dir).resolve())
        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return {"success": False, "error": "No checkpoints exist for this directory"}

        # 验证提交是否存在
        ok, _, err = _run_git(
            ["cat-file", "-t", commit_hash], shadow, abs_dir,
        )
        if not ok:
            return {"success": False, "error": f"Checkpoint '{commit_hash}' not found", "debug": err or None}

        # 在恢复前拍摄当前状态的检查点（以便可以撤销撤销）
        self._take(abs_dir, f"pre-rollback snapshot (restoring to {commit_hash[:8]})")

        # 恢复 — 完整目录或单个文件
        restore_target = file_path if file_path else "."
        ok, stdout, err = _run_git(
            ["checkout", commit_hash, "--", restore_target],
            shadow, abs_dir, timeout=_GIT_TIMEOUT * 2,
        )

        if not ok:
            return {"success": False, "error": f"Restore failed: {err}", "debug": err or None}

        # 获取有关已恢复内容的信息
        ok2, reason_out, _ = _run_git(
            ["log", "--format=%s", "-1", commit_hash], shadow, abs_dir,
        )
        reason = reason_out if ok2 else "unknown"

        result = {
            "success": True,
            "restored_to": commit_hash[:8],
            "reason": reason,
            "directory": abs_dir,
        }
        if file_path:
            result["file"] = file_path
        return result

    def get_working_dir_for_path(self, file_path: str) -> str:
        """将文件路径解析为其用于检查点的工作目录。

        从文件的父目录向上查找合理的项目根目录
        （包含 .git、pyproject.toml、package.json 等的目录）。
        回退到文件的父目录。
        """
        path = Path(file_path).resolve()
        if path.is_dir():
            candidate = path
        else:
            candidate = path.parent

        # 向上查找项目根目录标记
        markers = {".git", "pyproject.toml", "package.json", "Cargo.toml",
                    "go.mod", "Makefile", "pom.xml", ".hg", "Gemfile"}
        check = candidate
        while check != check.parent:
            if any((check / m).exists() for m in markers):
                return str(check)
            check = check.parent

        # 未找到项目根目录 — 使用文件的父目录
        return str(candidate)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _take(self, working_dir: str, reason: str) -> bool:
        """拍摄快照。成功时返回 True。"""
        shadow = _shadow_repo_path(working_dir)

        # 如需要则初始化
        err = _init_shadow_repo(shadow, working_dir)
        if err:
            logger.debug("Checkpoint init failed: %s", err)
            return False

        # 快速大小保护 — 不要尝试快照过大的目录
        if _dir_file_count(working_dir) > _MAX_FILES:
            logger.debug("Checkpoint skipped: >%d files in %s", _MAX_FILES, working_dir)
            return False

        # 暂存所有内容
        ok, _, err = _run_git(
            ["add", "-A"], shadow, working_dir, timeout=_GIT_TIMEOUT * 2,
        )
        if not ok:
            logger.debug("Checkpoint git-add failed: %s", err)
            return False

        # 检查是否有任何内容需要提交
        ok_diff, diff_out, _ = _run_git(
            ["diff", "--cached", "--quiet"],
            shadow,
            working_dir,
            allowed_returncodes={1},
        )
        if ok_diff:
            # 没有要提交的更改
            logger.debug("Checkpoint skipped: no changes in %s", working_dir)
            return False

        # 提交
        ok, _, err = _run_git(
            ["commit", "-m", reason, "--allow-empty-message"],
            shadow, working_dir, timeout=_GIT_TIMEOUT * 2,
        )
        if not ok:
            logger.debug("Checkpoint commit failed: %s", err)
            return False

        logger.debug("Checkpoint taken in %s: %s", working_dir, reason)

        # 修剪旧快照
        self._prune(shadow, working_dir)

        return True

    def _prune(self, shadow_repo: Path, working_dir: str) -> None:
        """通过孤立重置仅保留最后 max_snapshots 个提交。"""
        ok, stdout, _ = _run_git(
            ["rev-list", "--count", "HEAD"], shadow_repo, working_dir,
        )
        if not ok:
            return

        try:
            count = int(stdout)
        except ValueError:
            return

        if count <= self.max_snapshots:
            return

        # 获取截止点处提交的哈希
        ok, cutoff_hash, _ = _run_git(
            ["rev-list", "--reverse", "HEAD", "--skip=0",
             "--max-count=1"],
            shadow_repo, working_dir,
        )

        # 为简单起见，我们实际上不进行修剪 — git 的 pack 机制
        # 高效地处理这个问题，且对象很小。日志
        # 列表已由 max_snapshots 限制。
        # 完全修剪需要 rebase --onto 或 filter-branch，这对于
        # 后台特性来说太脆弱了。我们只限制日志视图。
        logger.debug("检查点仓库有 %d 个提交（限制 %d）", count, self.max_snapshots)


def format_checkpoint_list(checkpoints: List[Dict], directory: str) -> str:
    """格式化检查点列表以显示给用户。"""
    if not checkpoints:
        return f"No checkpoints found for {directory}"

    lines = [f"📸 Checkpoints for {directory}:\n"]
    for i, cp in enumerate(checkpoints, 1):
        # 解析 ISO 时间戳为可读格式
        ts = cp["timestamp"]
        if "T" in ts:
            ts = ts.split("T")[1].split("+")[0].split("-")[0][:5]  # HH:MM
            date = cp["timestamp"].split("T")[0]
            ts = f"{date} {ts}"

        # 构建变更摘要
        files = cp.get("files_changed", 0)
        ins = cp.get("insertions", 0)
        dele = cp.get("deletions", 0)
        if files:
            stat = f"  ({files} file{'s' if files != 1 else ''}, +{ins}/-{dele})"
        else:
            stat = ""

        lines.append(f"  {i}. {cp['short_hash']}  {ts}  {cp['reason']}{stat}")

    lines.append("\n  /rollback <N>             restore to checkpoint N")
    lines.append("  /rollback diff <N>        preview changes since checkpoint N")
    lines.append("  /rollback <N> <file>      restore a single file from checkpoint N")
    return "\n".join(lines)
