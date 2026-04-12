# 添加新的消息平台

将新的消息平台集成到 KClaw 网关的清单。
将此作为参考来构建新的适配器 — 这里的每个项目都是代码库中存在的真实集成点。
缺少任何一个都会导致功能损坏、功能缺失或行为不一致。

---

## 1. 核心适配器 (`gateway/platforms/<platform>.py`)

适配器是 `gateway/platforms/base.py` 中 `BasePlatformAdapter` 的子类。

### 必需方法

| 方法 | 用途 |
|------|------|
| `__init__(self, config)` | 解析配置，初始化状态。调用 `super().__init__(config, Platform.YOUR_PLATFORM)` |
| `connect() -> bool` | 连接平台，启动监听器。成功时返回 True |
| `disconnect()` | 停止监听器，关闭连接，取消任务 |
| `send(chat_id, text, ...) -> SendResult` | 发送文本消息 |
| `send_typing(chat_id)` | 发送打字状态指示器 |
| `send_image(chat_id, image_url, caption) -> SendResult` | 发送图片 |
| `get_chat_info(chat_id) -> dict` | 返回聊天的 `{name, type, chat_id}` |

### 可选方法 (基类中有默认存根)

| 方法 | 用途 |
|------|------|
| `send_document(chat_id, path, caption)` | 发送文件附件 |
| `send_voice(chat_id, path)` | 发送语音消息 |
| `send_video(chat_id, path, caption)` | 发送视频 |
| `send_animation(chat_id, path, caption)` | 发送 GIF/动画 |
| `send_image_file(chat_id, path, caption)` | 从本地文件发送图片 |

### 必需函数

```python
def check_<platform>_requirements() -> bool:
    """检查此平台的依赖是否可用。"""
```

### 需要遵循的关键模式

- 使用 `self.build_source(...)` 构造 `SessionSource` 对象
- 调用 `self.handle_message(event)` 分发入站消息到网关
- 从 base 导入使用 `MessageEvent`、`MessageType`、`SendResult`
- 使用 `cache_image_from_bytes`、`cache_audio_from_bytes`、`cache_document_from_bytes` 处理附件
- 过滤自身消息（防止回复循环）
- 如果平台有同步/回显消息则过滤
- 在所有日志输出中隐藏敏感标识符（电话号码、令牌）
- 对于流连接实现指数退避 + 抖动的重连机制
- 如果平台有消息大小限制则设置 `MAX_MESSAGE_LENGTH`

---

## 2. 平台枚举 (`gateway/config.py`)

将平台添加到 `Platform` 枚举中：

```python
class Platform(Enum):
    ...
    YOUR_PLATFORM = "your_platform"
```

Add env var loading in `_apply_env_overrides()`:

```python
# Your Platform
your_token = os.getenv("YOUR_PLATFORM_TOKEN")
if your_token:
    if Platform.YOUR_PLATFORM not in config.platforms:
        config.platforms[Platform.YOUR_PLATFORM] = PlatformConfig()
    config.platforms[Platform.YOUR_PLATFORM].enabled = True
    config.platforms[Platform.YOUR_PLATFORM].token = your_token
```

如果你的平台不使用 token/api_key（如 WhatsApp 使用 `enabled` 标志，Signal 使用 `extra` 字典），
则更新 `get_connected_platforms()`。

---

## 3. 适配器工厂 (`gateway/run.py`)

添加到 `_create_adapter()` 中：

```python
elif platform == Platform.YOUR_PLATFORM:
    from gateway.platforms.your_platform import YourAdapter, check_your_requirements
    if not check_your_requirements():
        logger.warning("Your Platform: dependencies not met")
        return None
    return YourAdapter(config)
```

---

## 4. 授权映射 (`gateway/run.py`)

添加到 `_is_user_authorized()` 中的两个字典：

```python
platform_env_map = {
    ...
    Platform.YOUR_PLATFORM: "YOUR_PLATFORM_ALLOWED_USERS",
}
platform_allow_all_map = {
    ...
    Platform.YOUR_PLATFORM: "YOUR_PLATFORM_ALLOW_ALL_USERS",
}
```

---

## 5. 会话源 (`gateway/session.py`)

如果你的平台需要额外的身份字段（如 Signal 的 UUID 配合电话号码），
将其添加到 `SessionSource` 数据类中，使用 `Optional` 默认值，
并更新 base.py 中的 `to_dict()`、`from_dict()` 和 `build_source()`。

---

## 6. 系统提示词提示 (`agent/prompt_builder.py`)

添加 `PLATFORM_HINTS` 条目，让代理知道它在哪个平台上：

```python
PLATFORM_HINTS = {
    ...
    "your_platform": (
        "You are on Your Platform. "
        "Describe formatting capabilities, media support, etc."
    ),
}
```

如果没有这个，代理不会知道它在你的平台上，
可能会使用不适当的格式（如在不支持 Markdown 的平台上使用 Markdown）。

---

## 7. 工具集 (`toolsets.py`)

为你的平台添加命名工具集：

```python
"kclaw-your-platform": {
    "description": "Your Platform bot toolset",
    "tools": _KCLAW_CORE_TOOLS,
    "includes": []
},
```

并将其添加到 `kclaw-gateway` 组合工具集中：

```python
"kclaw-gateway": {
    "includes": [..., "kclaw-your-platform"]
}
```

---

## 8. Cron 投递 (`cron/scheduler.py`)

添加到 `_deliver_result()` 中的 `platform_map`：

```python
platform_map = {
    ...
    "your_platform": Platform.YOUR_PLATFORM,
}
```

如果没有这个，`cronjob(action="create", deliver="your_platform", ...)` 会静默失败。

---

## 9. 发送消息工具 (`tools/send_message_tool.py`)

添加到 `send_message_tool()` 中的 `platform_map`：

```python
platform_map = {
    ...
    "your_platform": Platform.YOUR_PLATFORM,
}
```

在 `_send_to_platform()` 中添加路由：

```python
elif platform == Platform.YOUR_PLATFORM:
    return await _send_your_platform(pconfig, chat_id, message)
```

实现 `_send_your_platform()` — 一个独立的异步函数，
用于在不需要完整适配器的情况下发送单条消息（供 cron 作业
和网关进程外的 send_message 工具使用）。

更新工具 schema 中的 `target` 描述以包含你的平台示例。

---

## 10. Cronjob 工具 Schema (`tools/cronjob_tools.py`)

更新 `deliver` 参数描述和文档字符串，
以提及你的平台作为投递选项。

---

## 11. 渠道目录 (`gateway/channel_directory.py`)

如果你的平台无法枚举聊天（大多数平台都无法），
将其添加到基于会话发现的列表中：

```python
for plat_name in ("telegram", "whatsapp", "signal", "your_platform"):
```

---

## 12. 状态显示 (`kclaw_cli/status.py`)

添加到消息平台部分的 `platforms` 字典中：

```python
platforms = {
    ...
    "Your Platform": ("YOUR_PLATFORM_TOKEN", "YOUR_PLATFORM_HOME_CHANNEL"),
}
```

---

## 13. 网关设置向导 (`kclaw_cli/gateway.py`)

添加到 `_PLATFORMS` 列表中：

```python
{
    "key": "your_platform",
    "label": "Your Platform",
    "emoji": "📱",
    "token_var": "YOUR_PLATFORM_TOKEN",
    "setup_instructions": [...],
    "vars": [...],
}
```

如果你的平台需要自定义设置逻辑（连接测试、二维码、
策略选择），添加一个 `_setup_your_platform()` 函数，
并在平台选择开关中路由到它。

如果你的平台的"已配置"检查与标准的 `bool(get_env_value(token_var))` 不同，
则更新 `_platform_status()`。

---

## 14. 电话/ID 隐藏 (`agent/redact.py`)

如果你的平台使用敏感标识符（电话号码等），
添加正则表达式模式和隐藏函数到 `agent/redact.py`。
这确保标识符在所有日志输出中被屏蔽，
而不仅仅是你的适配器的日志中。

---

## 15. 文档

| 文件 | 需要更新的内容 |
|------|---------------|
| `README.md` | 功能表中添加平台列表 + 文档表 |
| `AGENTS.md` | 网关描述 + 环境变量配置部分 |
| `website/docs/user-guide/messaging/<platform>.md` | **新增** — 完整设置指南（参考现有平台文档模板） |
| `website/docs/user-guide/messaging/index.md` | 架构图、工具集表、安全示例、下一步链接 |
| `website/docs/reference/environment-variables.md` | 平台的所有环境变量 | |

---

## 16. 测试 (`tests/gateway/test_<platform>.py`)

建议的测试覆盖范围：

- 平台枚举存在且值正确
- 通过 `_apply_env_overrides` 从环境变量加载配置
- 适配器初始化（配置解析、允许列表处理、默认值）
- 辅助函数（隐藏、解析、文件类型检测）
- 会话源往返（to_dict → from_dict）
- 授权集成（平台在允许列表映射中）
- 发送消息工具路由（平台在 platform_map 中）

可选但有价值：
- 消息处理流程的异步测试（模拟平台 API）
- SSE/WebSocket 重连逻辑
- 附件处理
- 群组消息过滤

---

## 快速验证

实现所有内容后，使用以下命令验证：

# 所有测试通过
python -m pytest tests/ -q

# 使用你的平台名搜索，找到任何遗漏的集成点
grep -r "telegram\|discord\|whatsapp\|slack" gateway/ tools/ agent/ cron/ kclaw_cli/ toolsets.py \
  --include="*.py" -l | sort -u
# 检查输出中的每个文件 — 如果它提到了其他平台但没有提到你的，则遗漏了
