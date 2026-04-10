---
name: blackbox
description: 将编码任务委托给Blackbox AI CLI代理。多模型代理，内置评判员，通过多个LLM运行任务并选择最佳结果。需要blackbox CLI和Blackbox AI API密钥。
version: 1.0.0
author: KClaw Agent (kkutysllb)
license: MIT
metadata:
  kclaw:
    tags: [编码代理, Blackbox, 多代理, 评判员, 多模型]
    related_skills: [claude-code, codex, kclaw]
---

# Blackbox CLI

通过KClaw终端将编码任务委托给[Blackbox AI](https://www.blackbox.ai/)。Blackbox是一个多模型编码代理CLI，将任务分派给多个LLM（Claude、Codex、Gemini、Blackbox Pro），并使用评判员选择最佳实现。

CLI是[开源的](https://github.com/blackboxaicode/cli)（GPL-3.0，TypeScript，从Gemini CLI分支），支持交互式会话、非交互式一次性执行、检查点、MCP和视觉模型切换。

## 前置要求

- 已安装Node.js 20+
- 已安装Blackbox CLI：`npm install -g @blackboxai/cli`
- 或从源码安装：
  ```
  git clone https://github.com/blackboxaicode/cli.git
  cd cli && npm install && npm install -g .
  ```
- 来自[app.blackbox.ai/dashboard](https://app.blackbox.ai/dashboard)的API密钥
- 已配置：运行`blackbox configure`并输入您的API密钥
- 在终端调用中使用`pty=true` — Blackbox CLI是一个交互式终端应用

## 一次性任务

```
terminal(command="blackbox --prompt 'Add JWT authentication with refresh tokens to the Express API'", workdir="/path/to/project", pty=true)
```

对于快速临时工作：
```
terminal(command="cd $(mktemp -d) && git init && blackbox --prompt 'Build a REST API for todos with SQLite'", pty=true)
```

## 后台模式（长时间任务）

对于需要几分钟的任务，使用后台模式以便您可以监控进度：

```
# 使用PTY在后台启动
terminal(command="blackbox --prompt 'Refactor the auth module to use OAuth 2.0'", workdir="~/project", background=true, pty=true)
# 返回session_id

# 监控进度
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# 如果Blackbox提问则提交输入
process(action="submit", session_id="<id>", data="yes")

# 如需要则终止
process(action="kill", session_id="<id>")
```

## 检查点和恢复

Blackbox CLI有内置检查点支持用于暂停和恢复任务：

```
# 任务完成后，Blackbox显示检查点标签
# 使用后续任务恢复：
terminal(command="blackbox --resume-checkpoint 'task-abc123-2026-03-06' --prompt 'Now add rate limiting to the endpoints'", workdir="~/project", pty=true)
```

## 会话命令

在交互式会话期间，使用这些命令：

| 命令 | 效果 |
|---------|--------|
| `/compress` | 缩小对话历史以节省token |
| `/clear` | 擦除历史并重新开始 |
| `/stats` | 查看当前token使用情况 |
| `Ctrl+C` | 取消当前操作 |

## PR审查

克隆到临时目录以避免修改工作树：

```
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && gh pr checkout 42 && blackbox --prompt 'Review this PR against main. Check for bugs, security issues, and code quality.'", pty=true)
```

## 并行工作

为独立任务生成多个Blackbox实例：

```
terminal(command="blackbox --prompt 'Fix the login bug'", workdir="/tmp/issue-1", background=true, pty=true)
terminal(command="blackbox --prompt 'Add unit tests for auth'", workdir="/tmp/issue-2", background=true, pty=true)

# 监控所有
process(action="list")
```

## 多模型模式

Blackbox的独特功能是通过多个模型运行相同任务并评判结果。通过`blackbox configure`配置要使用的模型 — 选择多个提供商以启用Chairman/评判员工作流，CLI评估来自不同模型的输出并选择最佳。

## 关键标志

| 标志 | 效果 |
|------|------|
| `--prompt "task"` | 非交互式一次性执行 |
| `--resume-checkpoint "tag"` | 从保存的检查点恢复 |
| `--yolo` | 自动批准所有操作和模型切换 |
| `blackbox session` | 启动交互式聊天会话 |
| `blackbox configure` | 更改设置、提供商、模型 |
| `blackbox info` | 显示系统信息 |

## 视觉支持

Blackbox自动检测输入中的图像，可以切换到多模态分析。VLM模式：
- `"once"` — 仅当前查询切换模型
- `"session"` — 整个会话切换
- `"persist"` — 保持在当前模型（不切换）

## Token限制

通过`.blackboxcli/settings.json`控制token使用：
```json
{
  "sessionTokenLimit": 32000
}
```

## 规则

1. **始终使用`pty=true`** — Blackbox CLI是一个交互式终端应用，没有PTY会挂起
2. **使用`workdir`** — 保持代理专注于正确的目录
3. **长时间任务使用后台** — 使用`background=true`并用`process`工具监控
4. **不要干扰** — 用`poll`/`log`监控，不要因为慢就终止会话
5. **报告结果** — 完成后检查发生了什么并为用户总结
6. **积分要花钱** — Blackbox使用基于积分的系统；多模型模式消耗积分更快
7. **检查前置条件** — 在委托之前验证`blackbox` CLI已安装
