# Gemini OAuth Provider — 实现计划

## 目标
添加一个通过 Google OAuth 认证的一级 `gemini` 提供商，使用标准 Gemini API（不是 Cloud Code Assist）。拥有 Google AI 订阅或 Gemini API 访问权限的用户可以通过浏览器进行认证，无需手动复制 API 密钥。

## 架构决策
- **路径 A（已选择）：** 标准 Gemini API at `generativelanguage.googleapis.com/v1beta/openai/`
- **不是路径 B：** Cloud Code Assist（`cloudcode-pa.googleapis.com`）— 速率受限的免费层、内部 API、账户封禁风险
- 通过 OpenAI SDK 的标准 `chat_completions` api_mode — 无需新的 api_mode
- 我们自己的 OAuth 凭据 — 不与 Gemini CLI 共享 token

## OAuth 流程
- **类型：** 授权码 + PKCE（S256）— 与 clawdbot/pi-mono 相同的模式
- **Auth URL：** `https://accounts.google.com/o/oauth2/v2/auth`
- **Token URL：** `https://oauth2.googleapis.com/token`
- **重定向：** `http://localhost:8085/oauth2callback`（localhost 回调服务器）
- **后备：** 手动 URL 粘贴用于远程/WSL/无头环境
- **Scopes：** `https://www.googleapis.com/auth/cloud-platform`、`https://www.googleapis.com/auth/userinfo.email`
- **PKCE：** S256 代码挑战、32 字节随机验证器

## 客户端 ID
- 需要在 Nous Research GCP 项目上注册"桌面应用"OAuth 客户端
- 在代码中附带 client_id + client_secret（Google 将已安装应用秘密视为非机密）
- 或者：通过环境变量接受用户提供的 client_id 作为覆盖

## Token 生命周期
- 存储在 `~/.kclaw/gemini_oauth.json`（不与 `~/.gemini/oauth_creds.json` 共享）
- 字段：`client_id`、`client_secret`、`refresh_token`、`access_token`、`expires_at`、`email`
- 文件权限：0o600
- 每次 API 调用前：检查过期时间，如果距过期不足 5 分钟则刷新
- 刷新：使用 `grant_type=refresh_token` POST 到 token URL
- 并发访问的文件锁定（多个代理会话）

## API 集成
- Base URL：`https://generativelanguage.googleapis.com/v1beta/openai/`
- 认证：`Authorization: Bearer <access_token>`（作为 `api_key` 传递给 OpenAI SDK）
- api_mode：`chat_completions`（标准）
- 模型：gemini-2.5-pro、gemini-2.5-flash、gemini-2.0-flash 等

## 需要创建/修改的文件

### 新文件
1. `agent/google_oauth.py` — OAuth 流程（PKCE、localhost 服务器、token 交换、刷新）
   - `start_oauth_flow()` — 打开浏览器、启动回调服务器
   - `exchange_code()` — code → tokens
   - `refresh_access_token()` — 刷新流程
   - `load_credentials()` / `save_credentials()` — 文件 I/O 带锁定
   - `get_valid_access_token()` — 检查过期，需要时刷新
   - 约 200 行

### 需要修改的现有文件
2. `kclaw_cli/auth.py` — 添加 ProviderConfig 用于 "gemini"，auth_type="oauth_google"
3. `kclaw_cli/models.py` — 添加 Gemini 模型目录
4. `kclaw_cli/runtime_provider.py` — 添加 gemini 分支（读取 OAuth token、构建 OpenAI 客户端）
5. `kclaw_cli/main.py` — 添加 `_model_flow_gemini()`，添加到提供商选择
6. `kclaw_cli/setup.py` — 添加 gemini 认证流程（触发浏览器 OAuth）
7. `run_agent.py` — API 调用前的 Token 刷新（如 Copilot 模式）
8. `agent/auxiliary_client.py` — 在辅助解析链中添加 gemini
9. `agent/model_metadata.py` — 添加 Gemini 模型上下文长度

### 测试
10. `tests/agent/test_google_oauth.py` — OAuth 流程单元测试
11. `tests/test_api_key_providers.py` — 添加 gemini 提供商测试

### 文档
12. `website/docs/getting-started/quickstart.md` — 在提供商表中添加 gemini
13. `website/docs/user-guide/configuration.md` — Gemini 设置部分
14. `website/docs/reference/environment-variables.md` — 新环境变量

## 估计范围
新代码约 400 行，修改约 150 行，测试约 100 行，文档约 50 行 = 总计约 700 行

## 先决条件
- 在 Nous Research GCP 项目上注册桌面 OAuth 客户端
- 或者：通过 KCLAW_GEMINI_CLIENT_ID 环境变量接受用户提供的 client_id

## 参考实现
- clawdbot: `extensions/google/oauth.flow.ts`（PKCE + localhost 服务器）
- pi-mono: `packages/ai/src/utils/oauth/google-gemini-cli.ts`（相同流程）
- kclaw Copilot OAuth: `kclaw_cli/main.py` `_copilot_device_flow()`（不同流程类型但相同的生命周期模式）
