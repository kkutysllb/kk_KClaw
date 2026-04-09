---
name: agentresult-fields
description: AgentResult dataclass字段参考，涵盖消息历史、managed_state、turns_used等字段及其用法。
---

# AgentResult字段参考

`AgentResult`在`environments/agent_loop.py`中定义为dataclass。

## 字段

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `messages` | `List[Dict[str, Any]]` | 完整对话历史（OpenAI消息格式） |
| `managed_state` | `Optional[Dict]` | 如果是阶段2则为ManagedServer.get_state()，否则为None |
| `turns_used` | `int` | 循环期间进行的LLM调用次数 |
| `finished_naturally` | `bool` | 如果模型自行停止调用工具则为True |
| `reasoning_per_turn` | `List[Optional[str]]` | 每轮提取的推理内容 |
| `tool_errors` | `List[ToolError]` | 循环期间遇到的工具错误 |

## ToolError字段

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `turn` | `int` | 发生错误的轮次 |
| `tool_name` | `str` | 失败的工具名称 |
| `arguments` | `str` | 传递给工具的参数 |
| `error` | `str` | 错误消息 |
| `tool_result` | `str` | 返回给模型的结果 |

## 从消息中提取数据

消息遵循OpenAI格式。常见模式：

```python
# 获取最终助手响应
for msg in reversed(result.messages):
    if msg.get("role") == "assistant" and msg.get("content"):
        final_response = msg["content"]
        break

# 获取所有使用的工具名称
tools = []
for msg in result.messages:
    if msg.get("role") == "assistant" and msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            tools.append(fn.get("name", ""))

# 获取工具结果
for msg in result.messages:
    if msg.get("role") == "tool":
        tool_output = msg.get("content", "")
        call_id = msg.get("tool_call_id", "")
```

## 不存在的字段

这些是常见错误 — AgentResult**没有**这些：
- `final_response` — 从消息中提取
- `tool_calls` — 从消息中提取  
- `tools_used` — 从消息中提取
- `output` — 从消息中提取
- `response` — 从消息中提取
