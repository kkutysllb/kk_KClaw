# 身份验证与设置

## 安装 CLI

```bash
curl -fsSL https://cli.inference.sh | sh
```

## 登录

```bash
infsh login
```

这会打开浏览器进行身份验证。登录后，凭据会本地存储。

## 检查身份验证

```bash
infsh me
```

如果已认证，显示您的用户信息。

## 环境变量

对于 CI/CD 或脚本，设置您的 API 密钥：

```bash
export INFSH_API_KEY=your-api-key
```

环境变量覆盖配置文件。

## 更新 CLI

```bash
infsh update
```

或重新安装：

```bash
curl -fsSL https://cli.inference.sh | sh
```

## 故障排除

| 错误 | 解决方案 |
|------|----------|
| "未认证" | 运行 `infsh login` |
| "命令未找到" | 重新安装 CLI 或添加到 PATH |
| "API 密钥无效" | 检查 `INFSH_API_KEY` 或重新登录 |

## 文档

- [CLI 设置](https://inference.sh/docs/extend/cli-setup) - 完整的 CLI 安装指南
- [API 身份验证](https://inference.sh/docs/api/authentication) - API 密钥管理
- [秘密](https://inference.sh/docs/secrets/overview) - 管理凭据
