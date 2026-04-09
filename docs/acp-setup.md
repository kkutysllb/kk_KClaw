# KClaw Agent — ACP（代理客户端协议）设置指南

KClaw Agent 支持 **代理客户端协议（ACP）**，允许它作为编码代理在您的编辑器中运行。ACP 让您的 IDE 向 KClaw 发送任务，KClaw 用文件编辑、终端命令和解释响应 — 所有内容都本地显示在编辑器 UI 中。

---

## 前置要求

- KClaw Agent 已安装并配置（`kclaw setup` 已完成）
- 在 `~/.kclaw/.env` 中设置 API 密钥 / 提供商或通过 `kclaw login`
- Python 3.11+

安装 ACP 扩展：

```bash
pip install -e ".[acp]"
```

---

## VS Code 设置

### 1. 安装 ACP Client 扩展

打开 VS Code 并从市场安装 **ACP Client**：

- 按 `Ctrl+Shift+X`（在 macOS 上为 `Cmd+Shift+X`）
- 搜索 **"ACP Client"**
- 点击 **安装**

或从命令行安装：

```bash
code --install-extension anysphere.acp-client
```

### 2. 配置 settings.json

打开 VS Code 设置（`Ctrl+,` → 点击 `{}` 图标进入 JSON）并添加：

```json
{
  "acpClient.agents": [
    {
      "name": "kclaw",
      "registryDir": "/path/to/kclaw/acp_registry"
    }
  ]
}
```

将 `/path/to/kclaw` 替换为您 KClaw Agent 安装的实际路径（例如 `~/.kclaw/kclaw`）。

或者，如果 `kclaw` 在您的 PATH 上，ACP Client 可以通过注册表目录自动发现它。

### 3. 重启 VS Code

配置后，重启 VS Code。您应该会在 ACP 代理选择器中看到 **KClaw Agent**，位于聊天/代理面板中。

---

## Zed 设置

Zed 内置 ACP 支持。

### 1. 配置 Zed 设置

打开 Zed 设置（在 macOS 上为 `Cmd+,`，在 Linux 上为 `Ctrl+,`）并添加到您的 `settings.json`：

```json
{
  "agent_servers": {
    "kclaw": {
      "type": "custom",
      "command": "kclaw",
      "args": ["acp"],
    },
  },
}
```

### 2. 重启 Zed

KClaw Agent 将出现在代理面板中。选择它并开始对话。

---

## JetBrains 设置（IntelliJ、PyCharm、WebStorm 等）

### 1. 安装 ACP 插件

- 打开 **Settings** → **Plugins** → **Marketplace**
- 搜索 **"ACP"** 或 **"Agent Client Protocol"**
- 安装并重启 IDE

### 2. 配置代理

- 打开 **Settings** → **Tools** → **ACP Agents**
- 点击 **+** 添加新代理
- 将注册表目录设置为您 `acp_registry/` 文件夹的路径：
  `/path/to/kclaw/acp_registry`
- 点击 **OK**

### 3. 使用代理

打开 ACP 面板（通常在右侧边栏）并选择 **KClaw Agent**。

---

## 您将看到的内容

连接后，您的编辑器提供 KClaw Agent 的本地界面：

### 聊天面板
对话界面，您可以在其中描述任务、提问和给出指令。KClaw 用解释和操作响应。

### 文件差异
当 KClaw 编辑文件时，您可以在编辑器中看到标准差异。您可以：
- **接受** 个别更改
- **拒绝** 您不想要的更改
- **在应用前** 查看完整差异

### 终端命令
当 KClaw 需要运行 shell 命令（构建、测试、安装）时，编辑器会在集成终端中显示它们。根据您的设置：
- 命令可能自动运行
- 或者您可能会被提示 **批准** 每个命令

### 审批流程
对于潜在破坏性操作，编辑器会在 KClaw 继续之前提示您审批。这包括：
- 文件删除
- Shell 命令
- Git 操作

---

## 配置

ACP 下的 KClaw Agent 使用 **与 CLI 相同的配置**：

- **API 密钥 / 提供商**：`~/.kclaw/.env`
- **代理配置**：`~/.kclaw/config.yaml`
- **技能**：`~/.kclaw/skills/`
- **会话**：`~/.kclaw/state.db`

您可以运行 `kclaw setup` 配置提供商，或直接编辑 `~/.kclaw/.env`。

### 更改模型

编辑 `~/.kclaw/config.yaml`：

```yaml
model: openrouter/nous/kclaw-3-llama-3.1-70b
```

或设置 `KCLAW_MODEL` 环境变量。

### 工具集

ACP 会话默认使用精选的 `kclaw-acp` 工具集。它专为编辑器工作流设计，故意排除消息传递、cronjob 管理和音频优先 UX 功能等内容。

---

## 故障排除

### 代理未出现在编辑器中

1. **检查注册表路径** — 确保编辑器设置中的 `acp_registry/` 目录路径正确且包含 `agent.json`。
2. **检查 `kclaw` 在 PATH 上** — 在终端中运行 `which kclaw`。如果找不到，您可能需要激活虚拟环境或将其添加到 PATH。
3. **更改设置后重启编辑器**。

### 代理启动但立即出错

1. 运行 `kclaw doctor` 检查您的配置。
2. 检查您是否有有效 API 密钥：`kclaw status`
3. 尝试直接在终端运行 `kclaw acp` 查看错误输出。

### "Module not found" 错误

确保您安装了 ACP 扩展：

```bash
pip install -e ".[acp]"
```

### 响应缓慢

- ACP 流式传输响应，因此您应该看到增量输出。如果代理似乎卡住了，检查您的网络连接和 API 提供商状态。
- 一些提供商有速率限制。尝试切换到不同的模型/提供商。

### 终端命令权限被拒绝

如果编辑器阻止终端命令，请检查 ACP Client 扩展设置中的自动审批或手动审批偏好。

### 日志

KClaw 在 ACP 模式下运行时将日志写入 stderr。检查：
- VS Code：**Output** 面板 → 选择 **ACP Client** 或 **KClaw Agent**
- Zed：**View** → **Toggle Terminal** 并检查进程输出
- JetBrains：**Event Log** 或 ACP 工具窗口

您也可以启用详细日志记录：

```bash
KCLAW_LOG_LEVEL=DEBUG kclaw acp
```

---

## 进一步阅读

- [ACP 规范](https://github.com/anysphere/acp)
- [KClaw Agent 文档](https://github.com/NousResearch/kclaw)
- 运行 `kclaw --help` 获取所有 CLI 选项
