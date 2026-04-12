"""
定时任务的存储与管理。

任务保存在：~/.kclaw/cron/jobs.json
任务输出保存在：~/.kclaw/cron/output/{job_id}/{timestamp}.md
"""

import copy
import json
import logging
import tempfile
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from kclaw_constants import get_kclaw_home
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

from kclaw_time import now as _kclaw_now

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

# =============================================================================
# 配置
# =============================================================================

KCLAW_DIR = get_kclaw_home()
CRON_DIR = KCLAW_DIR / "cron"
JOBS_FILE = CRON_DIR / "jobs.json"
OUTPUT_DIR = CRON_DIR / "output"
ONESHOT_GRACE_SECONDS = 120


def _normalize_skill_list(skill: Optional[str] = None, skills: Optional[Any] = None) -> List[str]:
    """将遗留/单一技能和多技能输入规范化为唯一的有序列表。"""
    if skills is None:
        raw_items = [skill] if skill else []
    elif isinstance(skills, str):
        raw_items = [skills]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _apply_skill_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    """返回一个任务字典，其中规范的 `skills` 和遗留的 `skill` 字段已保持一致。"""
    normalized = dict(job)
    skills = _normalize_skill_list(normalized.get("skill"), normalized.get("skills"))
    normalized["skills"] = skills
    normalized["skill"] = skills[0] if skills else None
    return normalized


def _secure_dir(path: Path):
    """将目录设置为仅所有者可访问（0700）。在 Windows 上无操作。"""
    try:
        os.chmod(path, 0o700)
    except (OSError, NotImplementedError):
        pass  # Windows or other platforms where chmod is not supported


def _secure_file(path: Path):
    """将文件设置为仅所有者可读写（0600）。在 Windows 上无操作。"""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def ensure_dirs():
    """确保 cron 目录存在，并设置安全权限。"""
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _secure_dir(CRON_DIR)
    _secure_dir(OUTPUT_DIR)


# =============================================================================
# 调度解析
# =============================================================================

def parse_duration(s: str) -> int:
    """
    将持续时间字符串解析为分钟数。
    
    示例:
        "30m" → 30
        "2h" → 120
        "1d" → 1440
    """
    s = s.strip().lower()
    match = re.match(r'^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not match:
        raise ValueError(f"无效的持续时间: '{s}'。请使用类似 '30m'、'2h' 或 '1d' 的格式")
    
    value = int(match.group(1))
    unit = match.group(2)[0]  # 第一个字符: m、h 或 d
    
    multipliers = {'m': 1, 'h': 60, 'd': 1440}
    return value * multipliers[unit]


def parse_schedule(schedule: str) -> Dict[str, Any]:
    """
    将调度字符串解析为结构化格式。
    
    返回包含以下字段的字典:
        - kind: "once" | "interval" | "cron"
        - 对于 "once": "run_at" (ISO 时间戳)
        - 对于 "interval": "minutes" (整数)
        - 对于 "cron": "expr" (cron 表达式)
    
    示例:
        "30m"              → 30 分钟后执行一次
        "2h"               → 2 小时后执行一次
        "every 30m"        → 每 30 分钟重复执行
        "every 2h"         → 每 2 小时重复执行
        "0 9 * * *"        → cron 表达式
        "2026-02-03T14:00" → 指定时间执行一次
    """
    schedule = schedule.strip()
    original = schedule
    schedule_lower = schedule.lower()
    
    # "every X" 模式 → 重复间隔
    if schedule_lower.startswith("every "):
        duration_str = schedule[6:].strip()
        minutes = parse_duration(duration_str)
        return {
            "kind": "interval",
            "minutes": minutes,
            "display": f"every {minutes}m"
        }
    
    # 检查 cron 表达式（5 或 6 个空格分隔的字段）
    # Cron 字段: minute hour day month weekday [year]
    parts = schedule.split()
    if len(parts) >= 5 and all(
        re.match(r'^[\d\*\-,/]+$', p) for p in parts[:5]
    ):
        if not HAS_CRONITER:
            raise ValueError("Cron 表达式需要 'croniter' 包。请运行: pip install croniter")
        # 验证 cron 表达式
        try:
            croniter(schedule)
        except Exception as e:
            raise ValueError(f"无效的 cron 表达式 '{schedule}': {e}")
        return {
            "kind": "cron",
            "expr": schedule,
            "display": schedule
        }
    
    # ISO 时间戳（包含 T 或类似日期格式）
    if 'T' in schedule or re.match(r'^\d{4}-\d{2}-\d{2}', schedule):
        try:
            # 解析并验证
            dt = datetime.fromisoformat(schedule.replace('Z', '+00:00'))
            # 在解析时将 naive 时间戳转换为带时区的，
            # 这样存储的值不依赖于系统时区在检查时是否匹配。
            if dt.tzinfo is None:
                dt = dt.astimezone()  # 解释为本地时区
            return {
                "kind": "once",
                "run_at": dt.isoformat(),
                "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}"
            }
        except ValueError as e:
            raise ValueError(f"无效的时间戳 '{schedule}': {e}")
    
    # 类似 "30m"、"2h"、"1d" 的持续时间 → 从现在开始一次性执行
    try:
        minutes = parse_duration(schedule)
        run_at = _kclaw_now() + timedelta(minutes=minutes)
        return {
            "kind": "once",
            "run_at": run_at.isoformat(),
            "display": f"once in {original}"
        }
    except ValueError:
        pass
    
    raise ValueError(
        f"无效的调度 '{original}'。请使用:\n"
        f"  - 持续时间: '30m'、'2h'、'1d' (一次性)\n"
        f"  - 间隔: 'every 30m'、'every 2h' (重复执行)\n"
        f"  - Cron: '0 9 * * *' (cron 表达式)\n"
        f"  - 时间戳: '2026-02-03T14:00:00' (指定时间一次性执行)"
    )


def _ensure_aware(dt: datetime) -> datetime:
    """返回 KClaw 配置时区的带时区 datetime。

    向后兼容性:
    - 较早存储的时间戳可能是 naive 的。
    - Naive 值被解释为创建时使用的 *系统本地挂钟时间*（即
      `datetime.now()` 使用的时区），然后转换到配置的 KClaw 时区。

    这保留了跨时区变化的旧 naive 时间戳的相对顺序，
    并避免误报 "未到期" 的结果。
    """
    target_tz = _kclaw_now().tzinfo
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).astimezone(target_tz)
    return dt.astimezone(target_tz)


def _recoverable_oneshot_run_at(
    schedule: Dict[str, Any],
    now: datetime,
    *,
    last_run_at: Optional[str] = None,
) -> Optional[str]:
    """如果一次性任务仍有资格触发，则返回其运行时间。

    一次性任务有一个小宽限期，以便在请求分钟后几秒创建的任务
    仍能在下一次 tick 时运行。一旦一次性任务已经运行过，
    它就不再有资格运行。
    """
    if schedule.get("kind") != "once":
        return None
    if last_run_at:
        return None

    run_at = schedule.get("run_at")
    if not run_at:
        return None

    run_at_dt = _ensure_aware(datetime.fromisoformat(run_at))
    if run_at_dt >= now - timedelta(seconds=ONESHOT_GRACE_SECONDS):
        return run_at
    return None


def _compute_grace_seconds(schedule: dict) -> int:
    """计算任务可以延迟多长时间仍能赶上而不是快进。

    使用调度周期的一半，并在 120 秒到 2 小时之间限制。
    这确保每日任务可以最多延迟 2 小时后仍能赶上，
    而频繁执行的任务（如每 5-10 分钟）仍然会快速快进。
    """
    MIN_GRACE = 120
    MAX_GRACE = 7200  # 2 小时

    kind = schedule.get("kind")

    if kind == "interval":
        period_seconds = schedule.get("minutes", 1) * 60
        grace = period_seconds // 2
        return max(MIN_GRACE, min(grace, MAX_GRACE))

    if kind == "cron" and HAS_CRONITER:
        try:
            now = _kclaw_now()
            cron = croniter(schedule["expr"], now)
            first = cron.get_next(datetime)
            second = cron.get_next(datetime)
            period_seconds = int((second - first).total_seconds())
            grace = period_seconds // 2
            return max(MIN_GRACE, min(grace, MAX_GRACE))
        except Exception:
            pass

    return MIN_GRACE


def compute_next_run(schedule: Dict[str, Any], last_run_at: Optional[str] = None) -> Optional[str]:
    """
    计算调度下一次运行时间。

    返回 ISO 时间戳字符串，如果没有更多运行则返回 None。
    """
    now = _kclaw_now()

    if schedule["kind"] == "once":
        return _recoverable_oneshot_run_at(schedule, now, last_run_at=last_run_at)

    elif schedule["kind"] == "interval":
        minutes = schedule["minutes"]
        if last_run_at:
            # 下一次运行 = 上次运行 + 间隔
            last = _ensure_aware(datetime.fromisoformat(last_run_at))
            next_run = last + timedelta(minutes=minutes)
        else:
            # 首次运行 = 现在 + 间隔
            next_run = now + timedelta(minutes=minutes)
        return next_run.isoformat()

    elif schedule["kind"] == "cron":
        if not HAS_CRONITER:
            return None
        cron = croniter(schedule["expr"], now)
        next_run = cron.get_next(datetime)
        return next_run.isoformat()

    return None


# =============================================================================
# 任务 CRUD 操作
# =============================================================================

def load_jobs() -> List[Dict[str, Any]]:
    """从存储中加载所有任务。"""
    ensure_dirs()
    if not JOBS_FILE.exists():
        return []
    
    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("jobs", [])
    except json.JSONDecodeError:
        # 使用 strict=False 重试，以处理字符串值中的裸控制字符
        try:
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                data = json.loads(f.read(), strict=False)
                jobs = data.get("jobs", [])
                if jobs:
                    # 自动修复：用正确的转义重写
                    save_jobs(jobs)
                    logger.warning("自动修复了 jobs.json（包含无效的控制字符）")
                return jobs
        except Exception:
            return []
    except IOError:
        return []


def save_jobs(jobs: List[Dict[str, Any]]):
    """将所有任务保存到存储。"""
    ensure_dirs()
    fd, tmp_path = tempfile.mkstemp(dir=str(JOBS_FILE.parent), suffix='.tmp', prefix='.jobs_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({"jobs": jobs, "updated_at": _kclaw_now().isoformat()}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, JOBS_FILE)
        _secure_file(JOBS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_job(
    prompt: str,
    schedule: str,
    name: Optional[str] = None,
    repeat: Optional[int] = None,
    deliver: Optional[str] = None,
    origin: Optional[Dict[str, Any]] = None,
    skill: Optional[str] = None,
    skills: Optional[List[str]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    script: Optional[str] = None,
) -> Dict[str, Any]:
    """
    创建新的定时任务。

    参数:
        prompt: 要运行的提示词（必须是自包含的，或当设置了 skill 时的任务指令）
        schedule: 调度字符串（参见 parse_schedule）
        name: 可选的友好名称
        repeat: 运行次数（None = 永远，1 = 一次）
        deliver: 输出投递位置（"origin"、"local"、"telegram" 等）
        origin: 任务创建来源信息（用于 "origin" 投递）
        skill: 可选的遗留单一技能名称，在运行提示词前加载
        skills: 可选的有序技能列表，在运行提示词前加载
        model: 可选的每任务模型覆盖
        provider: 可选的每任务 provider 覆盖
        base_url: 可选的每任务 base URL 覆盖
        script: 可选的 Python 脚本路径，其 stdout 在每次运行时注入到
                提示词中。脚本在 agent 执行前运行，其输出作为上下文前置。
                适用于数据收集/变更检测。

    返回:
        创建的任务字典
    """
    parsed_schedule = parse_schedule(schedule)

    # 规范化 repeat：将 0 或负值视为 None（无限）
    if repeat is not None and repeat <= 0:
        repeat = None

    # 如果未指定，为一次性调度自动设置 repeat=1
    if parsed_schedule["kind"] == "once" and repeat is None:
        repeat = 1

    # 默认投递到 origin（如果有），否则为 local
    if deliver is None:
        deliver = "origin" if origin else "local"

    job_id = uuid.uuid4().hex[:12]
    now = _kclaw_now().isoformat()

    normalized_skills = _normalize_skill_list(skill, skills)
    normalized_model = str(model).strip() if isinstance(model, str) else None
    normalized_provider = str(provider).strip() if isinstance(provider, str) else None
    normalized_base_url = str(base_url).strip().rstrip("/") if isinstance(base_url, str) else None
    normalized_model = normalized_model or None
    normalized_provider = normalized_provider or None
    normalized_base_url = normalized_base_url or None
    normalized_script = str(script).strip() if isinstance(script, str) else None
    normalized_script = normalized_script or None

    label_source = (prompt or (normalized_skills[0] if normalized_skills else None)) or "cron job"
    job = {
        "id": job_id,
        "name": name or label_source[:50].strip(),
        "prompt": prompt,
        "skills": normalized_skills,
        "skill": normalized_skills[0] if normalized_skills else None,
        "model": normalized_model,
        "provider": normalized_provider,
        "base_url": normalized_base_url,
        "script": normalized_script,
        "schedule": parsed_schedule,
        "schedule_display": parsed_schedule.get("display", schedule),
        "repeat": {
            "times": repeat,  # None = 永远
            "completed": 0
        },
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": now,
        "next_run_at": compute_next_run(parsed_schedule),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        # 投递配置
        "deliver": deliver,
        "origin": origin,  # 跟踪任务创建来源，用于 "origin" 投递
    }

    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)

    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """根据 ID 获取任务。"""
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return _apply_skill_fields(job)
    return None


def list_jobs(include_disabled: bool = False) -> List[Dict[str, Any]]:
    """列出所有任务，可选择是否包含已禁用的任务。"""
    jobs = [_apply_skill_fields(j) for j in load_jobs()]
    if not include_disabled:
        jobs = [j for j in jobs if j.get("enabled", True)]
    return jobs


def update_job(job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """根据 ID 更新任务，必要时刷新派生的调度字段。"""
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] != job_id:
            continue

        updated = _apply_skill_fields({**job, **updates})
        schedule_changed = "schedule" in updates

        if "skills" in updates or "skill" in updates:
            normalized_skills = _normalize_skill_list(updated.get("skill"), updated.get("skills"))
            updated["skills"] = normalized_skills
            updated["skill"] = normalized_skills[0] if normalized_skills else None

        if schedule_changed:
            updated_schedule = updated["schedule"]
            updated["schedule_display"] = updates.get(
                "schedule_display",
                updated_schedule.get("display", updated.get("schedule_display")),
            )
            if updated.get("state") != "paused":
                updated["next_run_at"] = compute_next_run(updated_schedule)

        if updated.get("enabled", True) and updated.get("state") != "paused" and not updated.get("next_run_at"):
            updated["next_run_at"] = compute_next_run(updated["schedule"])

        jobs[i] = updated
        save_jobs(jobs)
        return _apply_skill_fields(jobs[i])
    return None


def pause_job(job_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """暂停任务但不删除。"""
    return update_job(
        job_id,
        {
            "enabled": False,
            "state": "paused",
            "paused_at": _kclaw_now().isoformat(),
            "paused_reason": reason,
        },
    )


def resume_job(job_id: str) -> Optional[Dict[str, Any]]:
    """恢复已暂停的任务，并从现在起计算下一次运行时间。"""
    job = get_job(job_id)
    if not job:
        return None

    next_run_at = compute_next_run(job["schedule"])
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": next_run_at,
        },
    )


def trigger_job(job_id: str) -> Optional[Dict[str, Any]]:
    """安排任务在下一个调度器 tick 时运行。"""
    job = get_job(job_id)
    if not job:
        return None
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": _kclaw_now().isoformat(),
        },
    )


def remove_job(job_id: str) -> bool:
    """根据 ID 删除任务。"""
    jobs = load_jobs()
    original_len = len(jobs)
    jobs = [j for j in jobs if j["id"] != job_id]
    if len(jobs) < original_len:
        save_jobs(jobs)
        return True
    return False


def mark_job_run(job_id: str, success: bool, error: Optional[str] = None,
                 delivery_error: Optional[str] = None):
    """
    标记任务已运行。
    
    更新 last_run_at、last_status、递增完成计数、
    计算 next_run_at，并在达到重复限制时自动删除。

    ``delivery_error`` 与 agent 错误分开跟踪 — 一个任务
    可能成功（agent 产生了输出）但投递失败（平台宕机）。
    """
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] == job_id:
            now = _kclaw_now().isoformat()
            job["last_run_at"] = now
            job["last_status"] = "ok" if success else "error"
            job["last_error"] = error if not success else None
            # 单独跟踪投递失败 — 成功投递时清除
            job["last_delivery_error"] = delivery_error
            
            # 递增完成计数
            if job.get("repeat"):
                job["repeat"]["completed"] = job["repeat"].get("completed", 0) + 1
                
                # 检查是否达到重复限制
                times = job["repeat"].get("times")
                completed = job["repeat"]["completed"]
                if times is not None and times > 0 and completed >= times:
                    # 删除任务（达到限制）
                    jobs.pop(i)
                    save_jobs(jobs)
                    return
            
            # 计算下一次运行
            job["next_run_at"] = compute_next_run(job["schedule"], now)

            # 如果没有下一次运行（一次性已完成），则禁用
            if job["next_run_at"] is None:
                job["enabled"] = False
                job["state"] = "completed"
            elif job.get("state") != "paused":
                job["state"] = "scheduled"

            save_jobs(jobs)
            return
    
    save_jobs(jobs)


def advance_next_run(job_id: str) -> bool:
    """在执行前抢先推进重复任务的 next_run_at。

    在 run_job() 之前调用，这样如果进程在执行中途崩溃，
    任务不会在下次 gateway 重启时重新触发。这将调度器
    从至少一次转换为至多一次 — 错过一次运行比
    在崩溃循环中触发数十次要好得多。

    一次性任务保持不变，以便在重启时仍能重试。

    如果 next_run_at 被推进则返回 True，否则返回 False。
    """
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            kind = job.get("schedule", {}).get("kind")
            if kind not in ("cron", "interval"):
                return False
            now = _kclaw_now().isoformat()
            new_next = compute_next_run(job["schedule"], now)
            if new_next and new_next != job.get("next_run_at"):
                job["next_run_at"] = new_next
                save_jobs(jobs)
                return True
            return False
    return False


def get_due_jobs() -> List[Dict[str, Any]]:
    """获取所有现在应该运行的任务。

    对于重复任务（cron/interval），如果预定时间已过期
    （超过一个周期，例如因为 gateway 宕机），
    任务会快进到下一次运行而不是立即触发。
    这样可以防止 gateway 重启时出现大量错过的任务。
    """
    now = _kclaw_now()
    raw_jobs = load_jobs()
    jobs = [_apply_skill_fields(j) for j in copy.deepcopy(raw_jobs)]
    due = []
    needs_save = False

    for job in jobs:
        if not job.get("enabled", True):
            continue

        next_run = job.get("next_run_at")
        if not next_run:
            recovered_next = _recoverable_oneshot_run_at(
                job.get("schedule", {}),
                now,
                last_run_at=job.get("last_run_at"),
            )
            if not recovered_next:
                continue

            job["next_run_at"] = recovered_next
            next_run = recovered_next
            logger.info(
                "任务 '%s' 没有 next_run_at；正在恢复一次性运行于 %s",
                job.get("name", job["id"]),
                recovered_next,
            )
            for rj in raw_jobs:
                if rj["id"] == job["id"]:
                    rj["next_run_at"] = recovered_next
                    needs_save = True
                    break

        next_run_dt = _ensure_aware(datetime.fromisoformat(next_run))
        if next_run_dt <= now:
            schedule = job.get("schedule", {})
            kind = schedule.get("kind")

            # 对于重复任务，检查预定时间是否已过期
            # （gateway 宕机错过了时间窗口）。快进到
            # 下一次出现而不是触发过期的运行。
            grace = _compute_grace_seconds(schedule)
            if kind in ("cron", "interval") and (now - next_run_dt).total_seconds() > grace:
                # 任务已超过追赶宽限期 — 这是过期的错过运行。
                # 宽限期随调度周期缩放：每日=2小时，每小时=30分钟，每10分钟=5分钟。
                new_next = compute_next_run(schedule, now.isoformat())
                if new_next:
                    logger.info(
                        "任务 '%s' 错过了预定时间 (%s, grace=%ds)。"
                        "快进到下一次运行: %s",
                        job.get("name", job["id"]),
                        next_run,
                        grace,
                        new_next,
                    )
                    # 更新存储中的任务
                    for rj in raw_jobs:
                        if rj["id"] == job["id"]:
                            rj["next_run_at"] = new_next
                            needs_save = True
                            break
                    continue  # 跳过这次运行

            due.append(job)

    if needs_save:
        save_jobs(raw_jobs)

    return due


def save_job_output(job_id: str, output: str):
    """将任务输出保存到文件。"""
    ensure_dirs()
    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)
    _secure_dir(job_output_dir)
    
    timestamp = _kclaw_now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = job_output_dir / f"{timestamp}.md"
    
    fd, tmp_path = tempfile.mkstemp(dir=str(job_output_dir), suffix='.tmp', prefix='.output_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, output_file)
        _secure_file(output_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    
    return output_file
