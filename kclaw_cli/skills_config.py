"""
KClaw Agent 的技能配置。
`kclaw skills` 进入此模块。

切换单个技能或类别的开/关，全局或按平台。
配置存储在 ~/.kclaw/config.yaml 的 skills: 下：

  skills:
    disabled: [skill-a, skill-b]          # 全局禁用列表
    platform_disabled:                    # 每个平台的覆盖
      telegram: [skill-c]
      cli: []
"""
from typing import List, Optional, Set

from kclaw_cli.config import load_config, save_config
from kclaw_cli.colors import Colors, color

PLATFORMS = {
    "cli":      "🖥️  CLI",
    "telegram": "📱 Telegram",
    "discord":  "💬 Discord",
    "slack":    "💼 Slack",
    "whatsapp": "📱 WhatsApp",
    "signal":   "📡 Signal",
    "bluebubbles": "💬 BlueBubbles",
    "email":    "📧 Email",
    "homeassistant": "🏠 Home Assistant",
    "mattermost": "💬 Mattermost",
    "matrix":   "💬 Matrix",
    "dingtalk": "💬 DingTalk",
    "feishu": "🪽 Feishu",
    "wecom": "💬 WeCom",
    "webhook": "🔗 Webhook",
}

# ─── 配置辅助函数 ───────────────────────────────────────────────────────────

def get_disabled_skills(config: dict, platform: Optional[str] = None) -> Set[str]:
    """返回禁用的技能名称。平台特定列表回退到全局。"""
    skills_cfg = config.get("skills", {})
    global_disabled = set(skills_cfg.get("disabled", []))
    if platform is None:
        return global_disabled
    platform_disabled = skills_cfg.get("platform_disabled", {}).get(platform)
    if platform_disabled is None:
        return global_disabled
    return set(platform_disabled)


def save_disabled_skills(config: dict, disabled: Set[str], platform: Optional[str] = None):
    """将禁用的技能名称持久化到配置。"""
    config.setdefault("skills", {})
    if platform is None:
        config["skills"]["disabled"] = sorted(disabled)
    else:
        config["skills"].setdefault("platform_disabled", {})
        config["skills"]["platform_disabled"][platform] = sorted(disabled)
    save_config(config)


# ─── 技能发现 ─────────────────────────────────────────────────────────

def _list_all_skills() -> List[dict]:
    """返回所有已安装的技能（忽略禁用状态）。"""
    try:
        from tools.skills_tool import _find_all_skills
        return _find_all_skills(skip_disabled=True)
    except Exception:
        return []


def _get_categories(skills: List[dict]) -> List[str]:
    """返回排序的唯一类别名称（None -> 'uncategorized'）。"""
    return sorted({s["category"] or "uncategorized" for s in skills})


# ─── 平台选择 ──────────────────────────────────────────────────────

def _select_platform() -> Optional[str]:
    """询问用户要配置哪个平台，或全局。"""
    options = [("global", "所有平台（全局默认）")] + list(PLATFORMS.items())
    print()
    print(color("  为以下配置技能:", Colors.BOLD))
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    print()
    try:
        raw = input(color("  选择 [1]: ", Colors.YELLOW)).strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if not raw:
        return None  # global
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            key = options[idx][0]
            return None if key == "global" else key
    except ValueError:
        pass
    return None


# ─── 类别切换 ─────────────────────────────────────────────────────────

def _toggle_by_category(skills: List[dict], disabled: Set[str]) -> Set[str]:
    """一次性切换某个类别中的所有技能。"""
    from kclaw_cli.curses_ui import curses_checklist

    categories = _get_categories(skills)
    cat_labels = []
    # 当类别中并非所有技能都被禁用时，该类别"已启用"（选中）
    pre_selected = set()
    for i, cat in enumerate(categories):
        cat_skills = [s["name"] for s in skills if (s["category"] or "uncategorized") == cat]
        cat_labels.append(f"{cat}（{len(cat_skills)} 个技能）")
        if not all(s in disabled for s in cat_skills):
            pre_selected.add(i)

    chosen = curses_checklist(
        "类别 — 切换整个类别",
        cat_labels, pre_selected, cancel_returns=pre_selected,
    )

    new_disabled = set(disabled)
    for i, cat in enumerate(categories):
        cat_skills = {s["name"] for s in skills if (s["category"] or "uncategorized") == cat}
        if i in chosen:
            new_disabled -= cat_skills  # 类别启用 → 从禁用中移除
        else:
            new_disabled |= cat_skills  # 类别禁用 → 添加到禁用中
    return new_disabled


# ─── 入口点 ──────────────────────────────────────────────────────────────

def skills_command(args=None):
    """`kclaw skills` 的入口点。"""
    from kclaw_cli.curses_ui import curses_checklist

    config = load_config()
    skills = _list_all_skills()

    if not skills:
        print(color("  未安装任何技能。", Colors.DIM))
        return

    # 步骤 1: 选择平台
    platform = _select_platform()
    platform_label = PLATFORMS.get(platform, "所有平台") if platform else "所有平台"

    # 步骤 2: 选择模式 — 单独或按类别
    print()
    print(color(f"  配置平台：{platform_label}", Colors.DIM))
    print()
    print("  1. 单独切换技能")
    print("  2. 按类别切换")
    print()
    try:
        mode = input(color("  选择 [1]: ", Colors.YELLOW)).strip() or "1"
    except (KeyboardInterrupt, EOFError):
        return

    disabled = get_disabled_skills(config, platform)

    if mode == "2":
        new_disabled = _toggle_by_category(skills, disabled)
    else:
        # 构建标签并将索引映射到技能名称
        labels = [
            f"{s['name']}  （{s['category'] or 'uncategorized'}）  —  {s['description'][:55]}"
            for s in skills
        ]
        # "selected" = enabled（未禁用）— 符合 [✓] 约定
        pre_selected = {i for i, s in enumerate(skills) if s["name"] not in disabled}
        chosen = curses_checklist(
            f"{platform_label} 的技能",
            labels, pre_selected, cancel_returns=pre_selected,
        )
        # 任何未选中项都被禁用
        new_disabled = {skills[i]["name"] for i in range(len(skills)) if i not in chosen}

    if new_disabled == disabled:
        print(color("  无更改。", Colors.DIM))
        return

    save_disabled_skills(config, new_disabled, platform)
    enabled_count = len(skills) - len(new_disabled)
    print(color(f"✓ 已保存：{enabled_count} 已启用，{len(new_disabled)} 已禁用（{platform_label}）。", Colors.GREEN))
