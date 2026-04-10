#!/usr/bin/env python3
"""
技能同步 -- 基于清单的捆绑技能播种和更新。

将捆绑技能从仓库的 skills/ 目录复制到 ~/.kclaw/skills/，
并使用清单来跟踪哪些技能已被同步及其原始哈希。

清单格式 (v2)：每行是 "skill_name:origin_hash"，其中 origin_hash
是捆绑技能上次同步到用户目录时的 MD5。
旧的 v1 清单（无哈希的纯名称）会自动迁移。

更新逻辑：
  - 新技能（不在清单中）：复制到用户目录，记录原始哈希。
  - 现有技能（在清单中，存在于用户目录中）：
      * 如果用户副本匹配原始哈希：用户未修改 → 如果捆绑包已更改，
        则可安全更新。记录新的原始哈希。
      * 如果用户副本与原始哈希不同：用户已自定义 → 跳过。
  - 用户删除的（在清单中，不存在于用户目录中）：尊重，不重新添加。
  - 从捆绑包中移除的（在清单中，已从仓库中消失）：从清单中清理。

清单位于 ~/.kclaw/skills/.bundled_manifest。
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from kclaw_constants import get_kclaw_home
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


KCLAW_HOME = get_kclaw_home()
SKILLS_DIR = KCLAW_HOME / "skills"
MANIFEST_FILE = SKILLS_DIR / ".bundled_manifest"


def _get_bundled_dir() -> Path:
    """定位捆绑的 skills/ 目录。

    首先检查 KCLAW_BUNDLED_SKILLS 环境变量（由 Nix wrapper 设置），
    然后回退到从此源文件的相对路径。
    """
    env_override = os.getenv("KCLAW_BUNDLED_SKILLS")
    if env_override:
        return Path(env_override)
    return Path(__file__).parent.parent / "skills"


def _read_manifest() -> Dict[str, str]:
    """
    将清单读取为 {skill_name: origin_hash} 的字典。

    处理 v1（纯名称）和 v2（name:hash）格式。
    v1 条目获得空哈希字符串，这会在下次同步时触发迁移。
    """
    if not MANIFEST_FILE.exists():
        return {}
    try:
        result = {}
        for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                # v2 format: name:hash
                name, _, hash_val = line.partition(":")
                result[name.strip()] = hash_val.strip()
            else:
                # v1 format: plain name — empty hash triggers migration
                result[line] = ""
        return result
    except (OSError, IOError):
        return {}


def _write_manifest(entries: Dict[str, str]):
    """以 v2 格式（name:hash）原子性地写入清单文件。

    使用临时文件 + os.replace() 以避免进程崩溃或中途中断时损坏。
    """
    import tempfile

    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(f"{name}:{hash_val}" for name, hash_val in sorted(entries.items())) + "\n"

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(MANIFEST_FILE.parent),
            prefix=".bundled_manifest_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, MANIFEST_FILE)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to write skills manifest %s: %s", MANIFEST_FILE, e, exc_info=True)


def _discover_bundled_skills(bundled_dir: Path) -> List[Tuple[str, Path]]:
    """
    在捆绑目录中找到所有 SKILL.md 文件。
    返回 (skill_name, skill_directory_path) 元组列表。
    """
    skills = []
    if not bundled_dir.exists():
        return skills

    for skill_md in bundled_dir.rglob("SKILL.md"):
        path_str = str(skill_md)
        if "/.git/" in path_str or "/.github/" in path_str or "/.hub/" in path_str:
            continue
        skill_dir = skill_md.parent
        skill_name = skill_dir.name
        skills.append((skill_name, skill_dir))

    return skills


def _compute_relative_dest(skill_dir: Path, bundled_dir: Path) -> Path:
    """
    计算 SKILLS_DIR 中的目标路径，保留类别结构。
    例如，bundled/skills/mlops/axolotl -> ~/.kclaw/skills/mlops/axolotl
    """
    rel = skill_dir.relative_to(bundled_dir)
    return SKILLS_DIR / rel


def _dir_hash(directory: Path) -> str:
    """计算目录中所有文件内容的哈希以进行变更检测。"""
    hasher = hashlib.md5()
    try:
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                rel = fpath.relative_to(directory)
                hasher.update(str(rel).encode("utf-8"))
                hasher.update(fpath.read_bytes())
    except (OSError, IOError):
        pass
    return hasher.hexdigest()


def sync_skills(quiet: bool = False) -> dict:
    """
    使用清单将捆绑技能同步到 ~/.kclaw/skills/。

    返回：
        包含以下键的字典：copied (list)、updated (list)、skipped (int)、
                        user_modified (list)、cleaned (list)、total_bundled (int)
    """
    bundled_dir = _get_bundled_dir()
    if not bundled_dir.exists():
        return {
            "copied": [], "updated": [], "skipped": 0,
            "user_modified": [], "cleaned": [], "total_bundled": 0,
        }

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    bundled_skills = _discover_bundled_skills(bundled_dir)
    bundled_names = {name for name, _ in bundled_skills}

    copied = []
    updated = []
    user_modified = []
    skipped = 0

    for skill_name, skill_src in bundled_skills:
        dest = _compute_relative_dest(skill_src, bundled_dir)
        bundled_hash = _dir_hash(skill_src)

        if skill_name not in manifest:
            # ── New skill — never offered before ──
            try:
                if dest.exists():
                    # User already has a skill with the same name — don't overwrite
                    skipped += 1
                    manifest[skill_name] = bundled_hash
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_src, dest)
                    copied.append(skill_name)
                    manifest[skill_name] = bundled_hash
                    if not quiet:
                        print(f"  + {skill_name}")
            except (OSError, IOError) as e:
                if not quiet:
                    print(f"  ! Failed to copy {skill_name}: {e}")
                # Do NOT add to manifest — next sync should retry

        elif dest.exists():
            # ── Existing skill — in manifest AND on disk ──
            origin_hash = manifest.get(skill_name, "")
            user_hash = _dir_hash(dest)

            if not origin_hash:
                # v1 migration: no origin hash recorded. Set baseline from
                # user's current copy so future syncs can detect modifications.
                manifest[skill_name] = user_hash
                if user_hash == bundled_hash:
                    skipped += 1  # already in sync
                else:
                    # Can't tell if user modified or bundled changed — be safe
                    skipped += 1
                continue

            if user_hash != origin_hash:
                # User modified this skill — don't overwrite their changes
                user_modified.append(skill_name)
                if not quiet:
                    print(f"  ~ {skill_name} (user-modified, skipping)")
                continue

            # User copy matches origin — check if bundled has a newer version
            if bundled_hash != origin_hash:
                try:
                    # Move old copy to a backup so we can restore on failure
                    backup = dest.with_suffix(".bak")
                    shutil.move(str(dest), str(backup))
                    try:
                        shutil.copytree(skill_src, dest)
                        manifest[skill_name] = bundled_hash
                        updated.append(skill_name)
                        if not quiet:
                            print(f"  ↑ {skill_name} (updated)")
                        # Remove backup after successful copy
                        shutil.rmtree(backup, ignore_errors=True)
                    except (OSError, IOError):
                        # Restore from backup
                        if backup.exists() and not dest.exists():
                            shutil.move(str(backup), str(dest))
                        raise
                except (OSError, IOError) as e:
                    if not quiet:
                        print(f"  ! Failed to update {skill_name}: {e}")
            else:
                skipped += 1  # bundled unchanged, user unchanged

        else:
            # ── In manifest but not on disk — user deleted it ──
            skipped += 1

    # Clean stale manifest entries (skills removed from bundled dir)
    cleaned = sorted(set(manifest.keys()) - bundled_names)
    for name in cleaned:
        del manifest[name]

    # Also copy DESCRIPTION.md files for categories (if not already present)
    for desc_md in bundled_dir.rglob("DESCRIPTION.md"):
        rel = desc_md.relative_to(bundled_dir)
        dest_desc = SKILLS_DIR / rel
        if not dest_desc.exists():
            try:
                dest_desc.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(desc_md, dest_desc)
            except (OSError, IOError) as e:
                logger.debug("Could not copy %s: %s", desc_md, e)

    _write_manifest(manifest)

    return {
        "copied": copied,
        "updated": updated,
        "skipped": skipped,
        "user_modified": user_modified,
        "cleaned": cleaned,
        "total_bundled": len(bundled_skills),
    }


if __name__ == "__main__":
    print("Syncing bundled skills into ~/.kclaw/skills/ ...")
    result = sync_skills(quiet=False)
    parts = [
        f"{len(result['copied'])} new",
        f"{len(result['updated'])} updated",
        f"{result['skipped']} unchanged",
    ]
    if result["user_modified"]:
        parts.append(f"{len(result['user_modified'])} user-modified (kept)")
    if result["cleaned"]:
        parts.append(f"{len(result['cleaned'])} cleaned from manifest")
    print(f"\nDone: {', '.join(parts)}. {result['total_bundled']} total bundled.")
