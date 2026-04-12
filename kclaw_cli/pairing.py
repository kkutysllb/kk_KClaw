"""
DM 配对系统的 CLI 命令。

用法：
    kclaw pairing list              # 显示所有待处理 + 已批准的用户
    kclaw pairing approve <平台> <代码>  # 批准配对代码
    kclaw pairing revoke <平台> <用户ID> # 撤销用户访问权限
    kclaw pairing clear-pending     # 清除所有过期/待处理的代码
"""

def pairing_command(args):
    """处理 kclaw pairing 子命令。"""
    from gateway.pairing import PairingStore

    store = PairingStore()
    action = getattr(args, "pairing_action", None)

    if action == "list":
        _cmd_list(store)
    elif action == "approve":
        _cmd_approve(store, args.platform, args.code)
    elif action == "revoke":
        _cmd_revoke(store, args.platform, args.user_id)
    elif action == "clear-pending":
        _cmd_clear_pending(store)
    else:
        print("用法: kclaw pairing {list|approve|revoke|clear-pending}")
        print("运行 'kclaw pairing --help' 查看详情。")


def _cmd_list(store):
    """列出所有待处理和已批准的用户。"""
    pending = store.list_pending()
    approved = store.list_approved()

    if not pending and not approved:
        print("没有配对数据。还没有人尝试过配对~")
        return

    if pending:
        print(f"\n  待处理配对请求 ({len(pending)}):")
        print(f"  {'Platform':<12} {'Code':<10} {'User ID':<20} {'Name':<20} {'Age'}")
        print(f"  {'--------':<12} {'----':<10} {'-------':<20} {'----':<20} {'---'}")
        for p in pending:
            print(
                f"  {p['platform']:<12} {p['code']:<10} {p['user_id']:<20} "
                f"{p.get('user_name', ''):<20} {p['age_minutes']}m ago"
            )
    else:
        print(f"\n  暂无待处理的配对请求。")

    if approved:
        print(f"\n  已批准用户 ({len(approved)}):")
        print(f"  {'Platform':<12} {'User ID':<20} {'Name':<20}")
        print(f"  {'--------':<12} {'-------':<20} {'----':<20}")
        for a in approved:
            print(f"  {a['platform']:<12} {a['user_id']:<20} {a.get('user_name', ''):<20}")
    else:
        print("\n  没有已批准的用户。")

    print()


def _cmd_approve(store, platform: str, code: str):
    """批准一个配对代码。"""
    platform = platform.lower().strip()
    code = code.upper().strip()

    result = store.approve_code(platform, code)
    if result:
        uid = result["user_id"]
        name = result.get("user_name", "")
        display = f"{name} ({uid})" if name else uid
        print(f"\n  已批准！用户 {display} 在 {platform} 上现在可以使用机器人了~")
        print("  他们下次发送消息时将自动被识别。\n")
    else:
        print(f"\n  代码 '{code}' 在平台 '{platform}' 上未找到或已过期。")
        print("  运行 'kclaw pairing list' 查看待处理的代码。\n")


def _cmd_revoke(store, platform: str, user_id: str):
    """撤销用户的访问权限。"""
    platform = platform.lower().strip()

    if store.revoke(platform, user_id):
        print(f"\n  已撤销用户 {user_id} 在 {platform} 上的访问权限。\n")
    else:
        print(f"\n  在 {platform} 的已批准列表中未找到用户 {user_id}。\n")


def _cmd_clear_pending(store):
    """清除所有待处理的配对代码。"""
    count = store.clear_pending()
    if count:
        print(f"\n  已清除 {count} 个待处理的配对请求。\n")
    else:
        print("\n  没有需要清除的待处理请求。\n")
