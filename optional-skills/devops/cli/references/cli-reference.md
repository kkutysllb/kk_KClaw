# CLI 参考

## 安装

```bash
curl -fsSL https://cli.inference.sh | sh
```

## 全局命令

| 命令 | 描述 |
|------|------|
| `infsh help` | 显示帮助 |
| `infsh version` | 显示 CLI 版本 |
| `infsh update` | 更新 CLI 到最新版本 |
| `infsh login` | 认证 |
| `infsh me` | 显示当前用户 |

## 应用命令

### 发现

| 命令 | 描述 |
|------|------|
| `infsh app list` | 列出可用的应用 |
| `infsh app list --category <cat>` | 按类别过滤（image、video、audio、text、other）|
| `infsh app search <query>` | 搜索应用 |
| `infsh app list --search <query>` | 搜索应用（标志形式）|
| `infsh app list --featured` | 显示精选应用 |
| `infsh app list --new` | 按最新排序 |
| `infsh app list --page <n>` | 分页 |
| `infsh app list -l` | 详细表格视图 |
| `infsh app list --save <file>` | 保存到 JSON 文件 |
| `infsh app my` | 列出您部署的应用 |
| `infsh app get <app>` | 获取应用详情 |
| `infsh app get <app> --json` | 获取应用详情作为 JSON |

### 执行

| 命令 | 描述 |
|------|------|
| `infsh app run <app> --input <file>` | 使用输入文件运行应用 |
| `infsh app run <app> --input '<json>'` | 使用内联 JSON 运行 |
| `infsh app run <app> --input <file> --no-wait` | 运行而不等待完成 |
| `infsh app sample <app>` | 显示示例输入 |
| `infsh app sample <app> --save <file>` | 保存示例到文件 |

## 任务命令

| 命令 | 描述 |
|------|------|
| `infsh task get <task-id>` | 获取任务状态和结果 |
| `infsh task get <task-id> --json` | 获取任务作为 JSON |
| `infsh task get <task-id> --save <file>` | 保存任务结果到文件 |

### 开发

| 命令 | 描述 |
|------|------|
| `infsh app init` | 创建新应用（交互式）|
| `infsh app init <name>` | 使用名称创建新应用 |
| `infsh app test --input <file>` | 本地测试应用 |
| `infsh app deploy` | 部署应用 |
| `infsh app deploy --dry-run` | 验证而不部署 |
| `infsh app pull <id>` | 拉取应用源 |
| `infsh app pull --all` | 拉取您所有的应用 |

## 环境变量

| 变量 | 描述 |
|------|------|
| `INFSH_API_KEY` | API 密钥（覆盖配置）|

## Shell 补全

```bash
# Bash
infsh completion bash > /etc/bash_completion.d/infsh

# Zsh
infsh completion zsh > "${fpath[1]}/_infsh"

# Fish
infsh completion fish > ~/.config/fish/completions/infsh.fish
```

## 应用名称格式

应用使用 `namespace/app-name` 格式：

- `falai/flux-dev-lora` - fal.ai 的 FLUX 2 Dev
- `google/veo-3` - Google 的 Veo 3
- `infsh/sdxl` - inference.sh 的 SDXL
- `bytedance/seedance-1-5-pro` - ByteDance 的 Seedance
- `xai/grok-imagine-image` - xAI 的 Grok

版本固定：`namespace/app-name@version`

## 文档

- [CLI 安装指南](https://inference.sh/docs/extend/cli-setup) - 完整的 CLI 安装指南
- [运行应用](https://inference.sh/docs/apps/running) - 如何通过 CLI 运行应用
- [创建应用](https://inference.sh/docs/extend/creating-app) - 构建您自己的应用
- [部署](https://inference.sh/docs/extend/deploying) - 部署应用到云
