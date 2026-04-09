# Supermemory 记忆提供商

具有 profile 召回、语义搜索、显式记忆工具和会话结束对话摄取功能的语义长期记忆。

## 需求

- `pip install supermemory`
- 从 [supermemory.ai](https://supermemory.ai) 获取 Supermemory API 密钥

## 设置

```bash
kclaw memory setup    # 选择 "supermemory"
```

或手动：

```bash
kclaw config set memory.provider supermemory
echo 'SUPERMEMORY_API_KEY=***' >> ~/.kclaw/.env
```

## 配置

配置文件：`$KCLAW_HOME/supermemory.json`

| 键 | 默认值 | 描述 |
|-----|--------|------|
| `container_tag` | `kclaw` | 用于搜索和写入的容器标签。支持 `{identity}` 模板用于 profile 范围的标签（例如 `kclaw-{identity}` → `kclaw-coder`）。 |
| `auto_recall` | `true` | 在每轮之前注入相关记忆上下文 |
| `auto_capture` | `true` | 在每次响应后存储清理的用户-助手对话轮次 |
| `max_recall_results` | `10` | 格式化为上下文的最大召回项目数 |
| `profile_frequency` | `50` | 在第一轮和每 N 轮包含 profile 事实 |
| `capture_mode` | `all` | 默认跳过微小或平凡的对话轮次 |
| `search_mode` | `hybrid` | 搜索模式：`hybrid`（profile + 记忆）、`memories`（仅记忆）、`documents`（仅文档）|
| `entity_context` | 内置默认 | 传递给 Supermemory 的提取指导 |
| `api_timeout` | `5.0` | SDK 和摄取请求的超时 |

### 环境变量

| 变量 | 描述 |
|------|------|
| `SUPERMEMORY_API_KEY` | API 密钥（必需）|
| `SUPERMEMORY_CONTAINER_TAG` | 覆盖容器标签（优先于配置文件）|

## 工具

| 工具 | 描述 |
|------|------|
| `supermemory_store` | 存储显式记忆 |
| `supermemory_search` | 通过语义相似性搜索记忆 |
| `supermemory_forget` | 通过 ID 或最佳匹配查询忘记记忆 |
| `supermemory_profile` | 检索持久 profile 和最近上下文 |

## 行为

启用后，KClaw 可以：

- 在每轮之前预取相关记忆上下文
- 在每次完成响应后存储清理的对话轮次
- 在会话结束时摄取完整会话以获得更丰富的图更新
- 暴露用于搜索、存储、忘记和 profile 访问的显式工具

## Profile 范围的容器

在 `container_tag` 中使用 `{identity}` 来按 KClaw profile 范围划分记忆：

```json
{
  "container_tag": "kclaw-{identity}"
}
```

对于名为 `coder` 的 profile，这解析为 `kclaw-coder`。默认 profile 解析为 `kclaw-default`。如果没有 `{identity}`，所有 profiles 共享相同的容器。

## 多容器模式

对于高级设置（例如 OpenClaw 风格的多工作区），您可以启用自定义容器标签，以便代理可以跨多个命名容器读写：

```json
{
  "container_tag": "kclaw",
  "enable_custom_container_tags": true,
  "custom_containers": ["project-alpha", "project-beta", "shared-knowledge"],
  "custom_container_instructions": "使用 project-alpha 进行编码任务，project-beta 进行研究，shared-knowledge 用于团队范围内的事实。"
}
```

启用后：
- `supermemory_search`、`supermemory_store`、`supermemory_forget` 和 `supermemory_profile` 接受可选的 `container_tag` 参数
- 标签必须在白名单中：主容器 + `custom_containers`
- 自动操作（轮次同步、预取、记忆写入镜像、会话摄取）始终仅使用**主**容器
- 自定义容器指令被注入系统提示

## 支持

- [Supermemory Discord](https://supermemory.link/discord)
- [support@supermemory.com](mailto:support@supermemory.com)
- [supermemory.ai](https://supermemory.ai)
