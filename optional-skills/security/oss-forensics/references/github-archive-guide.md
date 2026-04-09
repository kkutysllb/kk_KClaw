# GitHub Archive 查询指南（BigQuery）

GitHub Archive 将 GitHub 上的每个公共事件记录为不可变 JSON 记录。这些数据可通过 Google BigQuery 访问，是取证调查最可靠的来源 — 事件在记录后无法删除或修改。

## 公共数据集

- **项目**：`githubarchive`
- **表**：`day.YYYYMMDD`、`month.YYYYMM`、`year.YYYY`
- **成本**：每扫描 TiB $6.25。始终先运行 dry runs。
- **访问**：需要启用 BigQuery 的 Google Cloud 账户。免费层包含每月 1 TiB 的查询。

---

## 12 个 GitHub 事件类型

| 事件类型 | 记录内容 | 取证价值 |
|----------|----------|----------|
| `PushEvent` | 推送到分支的提交 | 强制推送检测、提交时间线、作者归属 |
| `PullRequestEvent` | PR 打开、关闭、合并、重新打开 | 已删除 PR 恢复、审查时间线 |
| `IssuesEvent` | Issue 打开、关闭、重新打开、标记 | 已删除 issue 恢复、社交工程痕迹 |
| `IssueCommentEvent` | Issues 和 PRs 上的评论 | 已删除评论恢复、通信模式 |
| `CreateEvent` | 分支、标签或仓库创建 | 可疑分支创建、标签时间 |
| `DeleteEvent` | 分支或标签删除 | 妥协后的清理证据 |
| `MemberEvent` | 添加或删除协作者 | 权限更改、访问升级 |
| `PublicEvent` | 仓库被公开 | 私有仓库意外暴露 |
| `WatchEvent` | 用户给仓库加星标 | 参与者侦察模式 |
| `ForkEvent` | 仓库被派生 | 清理前的代码泄露 |
| `ReleaseEvent` | 发布已发布、编辑、删除 | 恶意发布注入、已删除发布恢复 |
| `WorkflowRunEvent` | GitHub Actions 工作流触发 | CI/CD 滥用、未授权工作流运行 |

---

## 查询模板

### 基本：仓库的所有事件

```sql
SELECT
  created_at,
  type,
  actor.login,
  repo.name,
  payload
FROM
  `githubarchive.day.20240101`  -- 调整日期
WHERE
  repo.name = 'owner/repo'
  AND type IN ('PushEvent', 'DeleteEvent', 'MemberEvent')
ORDER BY
  created_at ASC
```

### 强制推送检测

强制推送产生 PushEvents，其中提交被覆盖。关键指标：
- `payload.distinct_size = 0` 且 `payload.size > 0` → 提交被擦除
- `payload.before` 包含重写前的 SHA（可恢复）

```sql
SELECT
  created_at,
  actor.login,
  JSON_EXTRACT_SCALAR(payload, '$.before') AS before_sha,
  JSON_EXTRACT_SCALAR(payload, '$.head') AS after_sha,
  JSON_EXTRACT_SCALAR(payload, '$.size') AS total_commits,
  JSON_EXTRACT_SCALAR(payload, '$.distinct_size') AS distinct_commits,
  JSON_EXTRACT_SCALAR(payload, '$.ref') AS branch_ref
FROM
  `githubarchive.month.*`
WHERE
  _TABLE_SUFFIX BETWEEN '202401' AND '202403'
  AND type = 'PushEvent'
  AND repo.name = 'owner/repo'
  AND CAST(JSON_EXTRACT_SCALAR(payload, '$.distinct_size') AS INT64) = 0
ORDER BY
  created_at ASC
```

### 已删除分支/标签检测

```sql
SELECT
  created_at,
  actor.login,
  JSON_EXTRACT_SCALAR(payload, '$.ref') AS deleted_ref,
  JSON_EXTRACT_SCALAR(payload, '$.ref_type') AS ref_type
FROM
  `githubarchive.month.*`
WHERE
  _TABLE_SUFFIX BETWEEN '202401' AND '202403'
  AND type = 'DeleteEvent'
  AND repo.name = 'owner/repo'
ORDER BY
  created_at ASC
```

### 协作者权限更改

```sql
SELECT
  created_at,
  actor.login,
  JSON_EXTRACT_SCALAR(payload, '$.action') AS action,
  JSON_EXTRACT_SCALAR(payload, '$.member.login') AS member
FROM
  `githubarchive.month.*`
WHERE
  _TABLE_SUFFIX BETWEEN '202401' AND '202403'
  AND type = 'MemberEvent'
  AND repo.name = 'owner/repo'
ORDER BY
  created_at ASC
```

### CI/CD 工作流活动

```sql
SELECT
  created_at,
  actor.login,
  JSON_EXTRACT_SCALAR(payload, '$.action') AS action,
  JSON_EXTRACT_SCALAR(payload, '$.workflow_run.name') AS workflow_name,
  JSON_EXTRACT_SCALAR(payload, '$.workflow_run.conclusion') AS conclusion,
  JSON_EXTRACT_SCALAR(payload, '$.workflow_run.head_sha') AS head_sha
FROM
  `githubarchive.month.*`
WHERE
  _TABLE_SUFFIX BETWEEN '202401' AND '202403'
  AND type = 'WorkflowRunEvent'
  AND repo.name = 'owner/repo'
ORDER BY
  created_at ASC
```

### 参与者活动分析

```sql
SELECT
  type,
  COUNT(*) AS event_count,
  MIN(created_at) AS first_event,
  MAX(created_at) AS last_event
FROM
  `githubarchive.month.*`
WHERE
  _TABLE_SUFFIX BETWEEN '202301' AND '202412'
  AND actor.login = 'suspicious-username'
GROUP BY type
ORDER BY event_count DESC
```

---

## 成本优化（强制）

1. **始终先 dry run**：在执行之前添加 `--dry_run` 标志到 `bq query` 以查看估计的字节扫描量。
2. **使用 `_TABLE_SUFFIX`**：尽可能缩小日期范围。窄窗口使用 `day.*` 表最便宜；宽范围使用 `month.*`。
3. **仅选择需要的列**：避免 `SELECT *`。`payload` 列很大 — 仅选择特定的 JSON 路径。
4. **添加 LIMIT**：探索期间使用 `LIMIT 1000`。仅在最终详尽查询时移除。
5. **WHERE 中的列过滤**：在 payload 提取之前在索引列（`type`、`repo.name`、`actor.login`）上过滤。

**成本估算**：GH Archive 一个月的数据约 1-2 TiB 未压缩。查询特定仓库 + 事件类型与 `_TABLE_SUFFIX` 通常扫描 1-10 GiB（$0.006-$0.06）。

---

## 通过 KClaw 访问

**选项 A：BigQuery CLI**（如果 `gcloud` 已安装）
```bash
bq query --use_legacy_sql=false --format=json "YOUR QUERY"
```

**选项 B：Python**（通过 `execute_code`）
```python
from google.cloud import bigquery
client = bigquery.Client()
query = "YOUR QUERY"
results = client.query(query).result()
for row in results:
    print(dict(row))
```

**选项 C：没有 GCP 凭据可用**
如果 BigQuery 不可用，在报告中记录此限制。使用其他 4 个调查员（Git、GitHub API、Wayback Machine、IOC 丰富）— 它们涵盖大多数调查需求，无需 BigQuery。
