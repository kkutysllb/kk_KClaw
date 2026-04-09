# 1Password CLI 入门（摘要）

官方文档：https://developer.1password.com/docs/cli/get-started/

## 核心流程

1. 安装 `op` CLI。
2. 在 1Password 应用中启用桌面应用集成。
3. 解锁应用。
4. 运行 `op signin` 并批准提示。
5. 用 `op whoami` 验证。

## 多账户

- 使用 `op signin --account <subdomain.1password.com>`
- 或设置 `OP_ACCOUNT`

## 非交互式 / 自动化

- 使用服务账户和 `OP_SERVICE_ACCOUNT_TOKEN`
- 优先使用 `op run` 和 `op inject` 进行运行时秘密处理
