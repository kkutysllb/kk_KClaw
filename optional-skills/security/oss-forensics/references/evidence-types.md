# 证据类型参考

OSS 取证调查中使用的所有证据类型、IOC 类型、GitHub 事件类型和观察类型的分类。

---

## 证据来源类型

| 类型 | 描述 | 示例来源 |
|------|------|----------|
| `git` | 来自本地 git 仓库分析的数据 | `git log`、`git fsck`、`git reflog`、`git blame` |
| `gh_api` | 来自 GitHub REST API 响应的数据 | `/repos/.../commits`、`/repos/.../pulls`、`/repos/.../events` |
| `gh_archive` | 来自 GitHub Archive (BigQuery) 的数据 | `githubarchive.month.*` BigQuery 表 |
| `web_archive` | 来自 Wayback Machine 的归档网页 | CDX API 结果、`web.archive.org/web/...` 快照 |
| `ioc` | 来自任何来源的 Indicator of Compromise | 从供应商报告、git 历史、网络跟踪中提取 |
| `analysis` | 跨来源关联的派生洞察 | "SHA 存在于归档但从 API 中缺失" |
| `vendor_report` | 外部安全供应商或研究报告 | CVE 咨询、博客文章、NVD 记录 |
| `manual` | 调查员手动记录的观察 | 关于行为模式、时间线间隙的笔记 |

---

## IOC 类型

| 类型 | 描述 | 示例 |
|------|------|------|
| `COMMIT_SHA` | 与恶意活动链接的 git 提交哈希 | `abc123def456...` |
| `FILE_PATH` | 仓库内的可疑文件 | `src/utils/crypto.js`、`dist/index.min.js` |
| `API_KEY` | 意外提交的 API 密钥 | `AKIA...` (AWS)、`ghp_...` (GitHub PAT) |
| `SECRET` | 通用秘密/凭据 | 数据库密码、私钥 blob |
| `IP_ADDRESS` | C2 服务器或攻击者 IP | `192.0.2.1` |
| `DOMAIN` | 恶意或可疑域名 | `evil-cdn.io`、 typosquatted 包注册域名 |
| `PACKAGE_NAME` | 恶意或蹲点的包名称 | `colo-rs` (typosquatting `color`)、`lodash-utils` |
| `ACTOR_USERNAME` | 与攻击链接的 GitHub 句柄 | `malicious-bot-account` |
| `MALICIOUS_URL` | 到恶意资源的 URL | `https://evil.example.com/payload.sh` |
| `WORKFLOW_FILE` | 可疑 CI/CD 工作流文件 | `.github/workflows/release.yml` |
| `BRANCH_NAME` | 可疑分支 | `refs/heads/temp-fix-do-not-merge` |
| `TAG_NAME` | 可疑 git 标签 | `v1.0.0-security-patch` |
| `RELEASE_NAME` | 可疑发布 | 没有关联标签或变更日志的发布 |
| `OTHER` | 未分类 IOC 的 catch-all | — |

---

## GitHub Archive 事件类型（12 种）

| 事件类型 | 取证相关性 |
|----------|------------|
| `PushEvent` | 核心：`payload.distinct_size=0` 且 `payload.size>0` → 强制推送。`payload.before`/`payload.head` 显示重写历史。|
| `PullRequestEvent` | 检测已删除的 PR、新账户的快速 open→close 模式、PR |
| `IssueEvent` | 检测已删除的 issues、协调标记、快速关闭漏洞报告 |
| `IssueCommentEvent` | 已删除评论、快速活动爆发 |
| `WatchEvent` | 星标农场活动（来自新账户的协调加星）|
| `ForkEvent` | 恶意提交前的异常派生模式 |
| `CreateEvent` | 分支/标签创建：信号新发布或代码注入点 |
| `DeleteEvent` | 分支/标签删除：关键 — 通常用于隐藏痕迹 |
| `ReleaseEvent` | 未授权发布、发布后修改的发布产物 |
| `MemberEvent` | 添加/删除协作者：维护者妥协指标 |
| `PublicEvent` | 仓库被公开（有时会短暂投放恶意代码）|
| `WorkflowRunEvent` | CI/CD 管道执行：工作流注入、秘密泄露 |

---

## 证据验证状态

| 状态 | 含义 |
|------|------|
| `unverified` | 从单一来源收集，未交叉引用 |
| `single_source` | 主要来源已直接确认（例如，SHA 在 GitHub 上解析），但没有第二个来源 |
| `multi_source_verified` | 从 2+ 个独立来源确认（例如，GH Archive 和 GitHub API 都显示相同事件）|

只有 `multi_source_verified` 证据可以作为事实被引用在经验证的假设中。
`unverified` 和 `single_source` 证据必须标记为 `[UNVERIFIED]` 或 `[SINGLE-SOURCE]`。

---

## 观察类型（仿照 RAPTOR）

| 类型 | 描述 |
|------|------|
| `CommitObservation` | 具有元数据（作者、日期、更改的文件）的特定提交 SHA |
| `ForceWashObservation` | 提交被强制擦除的证据 |
| `DanglingCommitObservation` | 存在于 git 对象存储中但从任何 ref 不可达的 SHA |
| `IssueObservation` | GitHub issue（当前或归档），包含标题、正文、时间戳 |
| `PRObservation` | 具有 diff 摘要、审查者的 GitHub PR（当前或归档）|
| `IOC` | 带上下文的单个 Indicator of Compromise |
| `TimelineGap` | 预期活动异常缺失的时期 |
| `ActorAnomalyObservation` | 特定 GitHub 参与者的行为异常 |
| `WorkflowAnomalyObservation` | 可疑 CI/CD 工作流更改或意外运行 |
| `CrossSourceDiscrepancy` | 项存在于一个来源但缺失于另一个来源（强删除指标）|
