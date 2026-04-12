"""kclaw webhook — 从 CLI 管理动态 Webhook 订阅。

用法:
    kclaw webhook subscribe <名称> [选项]
    kclaw webhook list
    kclaw webhook remove <名称>
    kclaw webhook test <名称> [--payload '{\"key\": \"value\"}']

订阅持久化到 ~/.kclaw/webhook_subscriptions.json，并在网关重启前
由 Webhook 适配器热加载。
"""

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict

from kclaw_constants import display_kclaw_home


_SUBSCRIPTIONS_FILENAME = "webhook_subscriptions.json"


def _kclaw_home() -> Path:
    from kclaw_constants import get_kclaw_home
    return get_kclaw_home()


def _subscriptions_path() -> Path:
    return _kclaw_home() / _SUBSCRIPTIONS_FILENAME


def _load_subscriptions() -> Dict[str, dict]:
    path = _subscriptions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subscriptions(subs: Dict[str, dict]) -> None:
    path = _subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(subs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(str(tmp_path), str(path))


def _get_webhook_config() -> dict:
    """加载 Webhook 平台配置。如果未配置则返回 {}。"""
    try:
        from kclaw_cli.config import load_config
        cfg = load_config()
        return cfg.get("platforms", {}).get("webhook", {})
    except Exception:
        return {}


def _is_webhook_enabled() -> bool:
    return bool(_get_webhook_config().get("enabled"))


def _get_webhook_base_url() -> str:
    wh = _get_webhook_config().get("extra", {})
    host = wh.get("host", "0.0.0.0")
    port = wh.get("port", 8644)
    display_host = "localhost" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}"


def _setup_hint() -> str:
    _dhh = display_kclaw_home()
    return f"""
  Webhook 平台未启用。设置方法：

  1. 运行网关设置向导：
     kclaw gateway setup

  2. 或手动添加到 {_dhh}/config.yaml：
     platforms:
       webhook:
         enabled: true
         extra:
           host: "0.0.0.0"
           port: 8644
           secret: "your-global-hmac-secret"

  3. 或在 {_dhh}/.env 中设置环境变量：
     WEBHOOK_ENABLED=true
     WEBHOOK_PORT=8644
     WEBHOOK_SECRET=your-global-secret

  然后启动网关: kclaw gateway run
"""


def _require_webhook_enabled() -> bool:
    """检查 Webhook 是否已启用。如果未启用则打印设置指南并返回 False。"""
    if _is_webhook_enabled():
        return True
    print(_setup_hint())
    return False


def webhook_command(args):
    """'kclaw webhook' 子命令的入口点。"""
    sub = getattr(args, "webhook_action", None)

    if not sub:
        print("用法: kclaw webhook {subscribe|list|remove|test}")
        print("运行 'kclaw webhook --help' 查看详情。")
        return

    if not _require_webhook_enabled():
        return

    if sub in ("subscribe", "add"):
        _cmd_subscribe(args)
    elif sub in ("list", "ls"):
        _cmd_list(args)
    elif sub in ("remove", "rm"):
        _cmd_remove(args)
    elif sub == "test":
        _cmd_test(args)


def _cmd_subscribe(args):
    name = args.name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        print(f"错误: 无效的名称 '{name}'。使用小写字母数字和连字符/下划线。")
        return

    subs = _load_subscriptions()
    is_update = name in subs

    secret = args.secret or secrets.token_urlsafe(32)
    events = [e.strip() for e in args.events.split(",")] if args.events else []

    route = {
        "description": args.description or f"智能体创建的订阅: {name}",
        "events": events,
        "secret": secret,
        "prompt": args.prompt or "",
        "skills": [s.strip() for s in args.skills.split(",")] if args.skills else [],
        "deliver": args.deliver or "log",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if args.deliver_chat_id:
        route["deliver_extra"] = {"chat_id": args.deliver_chat_id}

    subs[name] = route
    _save_subscriptions(subs)

    base_url = _get_webhook_base_url()
    status = "已更新" if is_update else "已创建"

    print(f"\n  {status} webhook 订阅: {name}")
    print(f"  URL:    {base_url}/webhooks/{name}")
    print(f"  密钥: {secret}")
    if events:
        print(f"  事件: {', '.join(events)}")
    else:
        print("  事件: (全部)")
    print(f"  投递: {route['deliver']}")
    if route.get("prompt"):
        prompt_preview = route["prompt"][:80] + ("..." if len(route["prompt"]) > 80 else "")
        print(f"  提示词: {prompt_preview}")
    print(f"\n  配置服务以 POST 到上述 URL。")
    print(f"  使用密钥进行 HMAC-SHA256 签名验证。")
    print(f"  网关必须运行才能接收事件 (kclaw gateway run)。\n")


def _cmd_list(args):
    subs = _load_subscriptions()
    if not subs:
        print("  没有动态 webhook 订阅。")
        print("  使用以下命令创建一个: kclaw webhook subscribe <名称>")
        return

    base_url = _get_webhook_base_url()
    print(f"\n  {len(subs)} 个 webhook 订阅:\n")
    for name, route in subs.items():
        events = ", ".join(route.get("events", [])) or "(全部)"
        deliver = route.get("deliver", "log")
        deliver = route.get("deliver", "log")
        desc = route.get("description", "")
        print(f"  ◆ {name}")
        if desc:
            print(f"    {desc}")
        print(f"    URL:     {base_url}/webhooks/{name}")
        print(f"    事件:   {events}")
        print(f"    Deliver: {deliver}")
        print()


def _cmd_remove(args):
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  没有名为 '{name}' 的订阅。")
        print("  注意：config.yaml 中的静态路由无法在此处删除。")
        return

    del subs[name]
    _save_subscriptions(subs)
    print(f"  已删除 webhook 订阅: {name}")


def _cmd_test(args):
    """向 webhook 路由发送测试 POST。"""
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  没有名为 '{name}' 的订阅。")
        return

    route = subs[name]
    secret = route.get("secret", "")
    base_url = _get_webhook_base_url()
    url = f"{base_url}/webhooks/{name}"

    payload = args.payload or '{"test": true, "event_type": "test", "message": "Hello from kclaw webhook test"}'

    import hmac
    import hashlib
    sig = "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    print(f"  正在发送测试 POST 到 {url}")
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "test",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"  响应 ({resp.status}): {body}")
    except Exception as e:
        print(f"  错误: {e}")
        print("  网关是否在运行？(kclaw gateway run)")
