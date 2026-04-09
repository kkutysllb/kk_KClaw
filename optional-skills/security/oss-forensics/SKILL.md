---
name: oss-forensics
description: |
  GitHub 仓库的供应链调查、证据恢复和取证分析。
  涵盖已删除提交恢复、强制推送检测、IOC 提取、多源证据收集、
  假设形成/验证和结构化取证报告。
  灵感来自 RAPTOR 的 1800+ 行 OSS 取证系统。
category: security
triggers:
  - "调查此仓库"
  - "调查 [owner/repo]"
  - "检查供应链是否被入侵"
  - "恢复已删除的提交"
  - "[owner/repo] 的取证分析"
  - "此仓库是否被入侵"
  - "供应链攻击"
  - "可疑提交"
  - "检测到强制推送"
  - "IOC 提取"
toolsets:
  - terminal
  - web
  - file
  - delegation
---

# OSS 安全取证技能

用于研究开源供应链攻击的 7 阶段多代理调查框架。
改编自 RAPTOR 的取证系统。涵盖 GitHub Archive、Wayback Machine、GitHub API、
本地 git 分析、IOC 提取、证据支持的假设形成和验证，
以及最终取证报告生成。

---

## 反幻觉防护栏

在每个调查步骤之前阅读这些。违反这些规定会使报告无效。

1. **证据优先规则**：任何报告、假设或摘要中的每个声明必须引用至少一个证据 ID（`EV-XXXX`）。禁止无引用的断言。
2. **坚守角色边界**：每个子代理（调查员）只有一个数据来源。不要混合来源。GH Archive 调查员不查询 GitHub API，反之亦然。角色边界是硬性的。
3. **事实与假设分离**：用 `[HYPOTHESIS]` 标记所有未验证的推论。只有针对原始来源验证的陈述才能作为事实陈述。
4. **不制造证据**：假设验证者必须在接受假设之前机械地检查每个引用的证据 ID 确实存在于证据存储中。
5. **需要证据的反证**：假设不能在没有特定、基于证据的反论点的情况下被驳斥。"未找到证据"不足以反驳 — 它只会使假设变得不确定。
6. **SHA/URL 双验证**：作为证据引用的任何提交 SHA、URL 或外部标识符必须在被标记为已验证之前从至少两个来源独立确认。
7. **可疑代码规则**：永远不要在本地运行在受调查仓库中发现的代码。仅静态分析，或在沙箱环境中使用 `execute_code`。
8. **秘密编辑**：在调查期间发现的任何 API 密钥、token 或凭据必须在最终报告中被编辑。仅在内部记录它们。

---

## 示例场景

- **场景 A：依赖混淆**：恶意包 `internal-lib-v2` 被上传到 NPM，版本高于内部包。调查员必须跟踪此包首次出现的时间，以及目标仓库中的 PushEvents 是否将 `package.json` 更新到此版本。
- **场景 B：维护者接管**：长期贡献者的账户被用来推送带有后门的 `.github/workflows/build.yml`。调查员查找此用户在长时间不活动后或来自新 IP/位置（如果可通过 BigQuery 检测到）的 PushEvents。
- **场景 C：强制推送隐藏**：开发者意外提交了生产秘密，然后强制推送来"修复"它。调查员使用 `git fsck` 和 GH Archive 恢复原始提交 SHA 并验证泄露了什么。

---

> **路径约定**：在此技能中，`SKILL_DIR` 指的是此技能安装目录的根（包含此 `SKILL.md` 的文件夹）。当技能被加载时，将 `SKILL_DIR` 解析为实际路径 — 例如 `~/.kclaw/skills/security/oss-forensics/` 或等效的 `optional-skills/`。所有脚本和模板引用都相对于它。

## 阶段 0：初始化

1. 创建调查工作目录：
   ```bash
   mkdir investigation_$(echo "REPO_NAME" | tr '/' '_')
   cd investigation_$(echo "REPO_NAME" | tr '/' '_')
   ```
2. 初始化证据存储：
   ```bash
   python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json list
   ```
3. 复制取证报告模板：
   ```bash
   cp SKILL_DIR/templates/forensic-report.md ./investigation-report.md
   ```
4. 创建 `iocs.md` 文件以跟踪发现的 Indicators of Compromise。
5. 记录调查开始时间、目标仓库和陈述的调查目标。

---

## 阶段 1：提示解析和 IOC 提取

**目标**：从用户的请求中提取所有结构化调查目标。

**行动**：
- 解析用户提示并提取：
  - 目标仓库（`owner/repo`）
  - 目标参与者（GitHub 句柄、电子邮件地址）
  - 感兴趣的时间窗口（提交日期范围、PR 时间戳）
  - 提供的 Indicators of Compromise：提交 SHA、文件路径、包名称、IP 地址、域名、API 密钥/token、恶意 URL
  - 任何关联的供应商安全报告或博客文章

**工具**：仅推理，或对于大文本块的正则提取使用 `execute_code`。

**输出**：用提取的 IOC 填充 `iocs.md`。每个 IOC 必须有：
- 类型（来自：COMMIT_SHA、FILE_PATH、API_KEY、SECRET、IP_ADDRESS、DOMAIN、PACKAGE_NAME、ACTOR_USERNAME、MALICIOUS_URL、OTHER）
- 值
- 来源（用户提供、推断）

**参考**：有关 IOC 分类，请参阅 [evidence-types.md](./references/evidence-types.md)。

---

## 阶段 2：并行证据收集

使用 `delegate_task`（批处理模式，最多 3 个并发）生成多达 5 个专业调查员子代理。每个调查员有**单一数据来源**，不得混合来源。

> **编排者注意**：在每个委托任务的 `context` 字段中传递阶段 1 的 IOC 列表和调查时间窗口。

---

### 调查员 1：本地 Git 调查员

**角色边界**：你只查询本地 Git 仓库。不要调用任何外部 API。

**行动**：
```bash
# 克隆仓库
git clone https://github.com/OWNER/REPO.git target_repo && cd target_repo

# 带有统计的完整提交日志
git log --all --full-history --stat --format="%H|%ae|%an|%ai|%s" > ../git_log.txt

# 检测强制推送证据（孤立/悬空提交）
git fsck --lost-found --unreachable 2>&1 | grep commit > ../dangling_commits.txt

# 检查重写历史的 reflog
git reflog --all > ../reflog.txt

# 列出所有分支包括删除的远程引用
git branch -a -v > ../branches.txt

# 查找可疑的大二进制文件添加
git log --all --diff-filter=A --name-only --format="%H %ai" -- "*.so" "*.dll" "*.exe" "*.bin" > ../binary_additions.txt

# 检查 GPG 签名异常
git log --show-signature --format="%H %ai %aN" > ../signature_check.txt 2>&1
```

**要收集的证据**（通过 `python3 SKILL_DIR/scripts/evidence-store.py add` 添加）：
- 每个悬空提交 SHA → 类型：`git`
- 强制推送证据（显示历史重写的 reflog）→ 类型：`git`
- 来自已验证贡献者的未签名提交 → 类型：`git`
- 可疑二进制文件添加 → 类型：`git`

**参考**：有关访问强制推送提交的信息，请参阅 [recovery-techniques.md](./references/recovery-techniques.md)。

---

### 调查员 2：GitHub API 调查员

**角色边界**：你只查询 GitHub REST API。不要在本地运行 git 命令。

**行动**：
```bash
# 提交（分页）
curl -s "https://api.github.com/repos/OWNER/REPO/commits?per_page=100" > api_commits.json

# 拉取请求包括已关闭/已删除的
curl -s "https://api.github.com/repos/OWNER/REPO/pulls?state=all&per_page=100" > api_prs.json

# Issues
curl -s "https://api.github.com/repos/OWNER/REPO/issues?state=all&per_page=100" > api_issues.json

# 贡献者和协作者更改
curl -s "https://api.github.com/repos/OWNER/REPO/contributors" > api_contributors.json

# 仓库事件（最近 300 个）
curl -s "https://api.github.com/repos/OWNER/REPO/events?per_page=100" > api_events.json

# 检查特定可疑提交 SHA 详情
curl -s "https://api.github.com/repos/OWNER/REPO/git/commits/SHA" > commit_detail.json

# Releases
curl -s "https://api.github.com/repos/OWNER/REPO/releases?per_page=100" > api_releases.json

# 检查特定提交是否存在（强制推送的提交可能在 commits/ 上 404，但在 git/commits/ 上成功）
curl -s "https://api.github.com/repos/OWNER/REPO/commits/SHA" | jq .sha
```

**交叉引用目标**（将差异标记为证据）：
- PR 存在于归档中但从 API 中缺失 → 删除的证据
- 贡献者存在于归档事件中但不在贡献者列表中 → 权限撤销的证据
- 提交存在于归档 PushEvents 中但不在 API 提交列表中 → 强制推送/删除的证据

**参考**：有关 GH 事件类型，请参阅 [evidence-types.md](./references/evidence-types.md)。

---

### 调查员 3：Wayback Machine 调查员

**角色边界**：你只查询 WAYBACK MACHINE CDX API。不要使用 GitHub API。

**目标**：恢复已删除的 GitHub 页面（README、issues、PRs、releases、wiki 页面）。

**行动**：
```bash
# 搜索仓库主页的归档快照
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO&output=json&limit=100&from=YYYYMMDD&to=YYYYMMDD" > wayback_main.json

# 搜索特定已删除 issue
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO/issues/NUM&output=json&limit=50" > wayback_issue_NUM.json

# 搜索特定已删除 PR
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO/pull/NUM&output=json&limit=50" > wayback_pr_NUM.json

# 获取页面的最佳快照
# 使用 Wayback Machine URL：https://web.archive.org/web/TIMESTAMP/ORIGINAL_URL
# 示例：https://web.archive.org/web/20240101000000*/github.com/OWNER/REPO

# 高级：搜索已删除的 releases/tags
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO/releases/tag/*&output=json" > wayback_tags.json

# 高级：搜索历史 wiki 更改
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO/wiki/*&output=json" > wayback_wiki.json
```

**要收集的证据**：
- 带有其内容的已删除 issues/PRs 的归档快照
- 显示更改的历史 README 版本
- 存在于归档中但缺失于当前 GitHub 状态的证据

**参考**：有关 CDX API 参数，请参阅 [github-archive-guide.md](./references/github-archive-guide.md)。

---

### 调查员 4：GH Archive / BigQuery 调查员

**角色边界**：你只通过 BigQuery 查询 GITHUB ARCHIVE。这是所有公共 GitHub 事件的防篡改记录。

> **先决条件**：需要具有 BigQuery 访问权限的 Google Cloud 凭据（`gcloud auth application-default login`）。如果不可用，跳过此调查员并在报告中注明。

**成本优化规则**（强制）：
1. 在每个查询之前始终运行 `--dry_run` 以估计成本。
2. 使用 `_TABLE_SUFFIX` 按日期范围过滤以最小化扫描数据。
3. 仅 SELECT 所需的列。
4. 添加 LIMIT，除非是聚合查询。

```bash
# 模板：PushEvents 到 OWNER/REPO 的安全 BigQuery 查询
bq query --use_legacy_sql=false --dry_run "
SELECT created_at, actor.login, payload.commits, payload.before, payload.head,
       payload.size, payload.distinct_size
FROM \`githubarchive.month.*\`
WHERE _TABLE_SUFFIX BETWEEN 'YYYYMM' AND 'YYYYMM'
  AND type = 'PushEvent'
  AND repo.name = 'OWNER/REPO'
LIMIT 1000
"
# 如果成本可接受，重新运行不带 --dry_run

# 检测强制推送：零 distinct_size PushEvents 意味着提交被强制擦除
# payload.distinct_size = 0 AND payload.size > 0 → 强制推送指标

# 检查已删除分支事件
bq query --use_legacy_sql=false "
SELECT created_at, actor.login, payload.ref, payload.ref_type
FROM \`githubarchive.month.*\`
WHERE _TABLE_SUFFIX BETWEEN 'YYYYMM' AND 'YYYYMM'
  AND type = 'DeleteEvent'
  AND repo.name = 'OWNER/REPO'
LIMIT 200
"
```

**要收集的证据**：
- 强制推送事件（payload.size > 0，payload.distinct_size = 0）
- 分支/标签的 DeleteEvents
- 可疑 CI/CD 自动化的 WorkflowRunEvents
- 先于 git 日志中"间隙"（历史重写的证据）的 PushEvents

**参考**：有关所有 12 种事件类型和查询模式，请参阅 [github-archive-guide.md](./references/github-archive-guide.md)。

---

### 调查员 5：IOC 丰富调查员

**角色边界**：你仅使用被动公共来源丰富阶段 1 的现有 IOC。不要执行目标仓库中的任何代码。

**行动**：
- 对于每个提交 SHA：通过直接 GitHub URL 尝试恢复（`github.com/OWNER/REPO/commit/SHA.patch`）
- 对于每个域名/IP：通过公共 WHOIS 服务的 `web_extract` 检查被动 DNS、WHOIS 记录
- 对于每个包名称：在 npm/PyPI 上检查匹配的恶意包报告
- 对于每个参与者用户名：检查 GitHub profile、贡献历史、账户年龄
- 使用 3 种方法恢复强制推送的提交（请参阅 [recovery-techniques.md](./references/recovery-techniques.md)）

---

## 阶段 3：证据整合

所有调查员完成后：

1. 运行 `python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json list` 查看所有收集的证据。
2. 对于每条证据，验证 `content_sha256` 哈希与原始来源匹配。
3. 按以下方式分组证据：
   - **时间线**：按时间顺序排列所有带时间戳的证据
   - **参与者**：按 GitHub 句柄或电子邮件分组
   - **IOC**：将证据与其相关的 IOC 链接
4. 识别**差异**：存在于一个来源但缺失于另一个来源的项目（关键删除指标）。
5. 将证据标记为 `[VERIFIED]`（从 2+ 个独立来源确认）或 `[UNVERIFIED]`（仅单一来源）。

---

## 阶段 4：假设形成

假设必须：
- 陈述特定声明（例如，"演员 X 在 DATE 强制推送到 BRANCH 以擦除提交 SHA"）
- 引用至少 2 个支持它的证据 ID（`EV-XXXX`、`EV-YYYY`）
- 识别可以反驳它的证据
- 在验证之前标记为 `[HYPOTHESIS]`

**常见假设模板**（请参阅 [investigation-templates.md](./references/investigation-templates.md)）：
- 维护者妥协：合法账户在接管后用于注入恶意代码
- 依赖混淆：包名称蹲点以拦截安装
- CI/CD 注入：恶意工作流更改以在构建期间运行代码
- Typosquatting：针对拼写错误者的近乎相同的包名称
- 凭据泄露：意外提交的 token/密钥，然后强制推送以擦除

对于每个假设，生成一个 `delegate_task` 子代理来尝试在确认之前找到反驳证据。

---

## 阶段 5：假设验证

验证者子代理必须机械地检查：

1. 对于每个假设，提取所有引用的证据 ID。
2. 验证每个 ID 存在于 `evidence.json` 中（任何 ID 缺失则硬失败 → 假设被拒绝为可能伪造）。
3. 验证每条 `[VERIFIED]` 证据从 2+ 个来源确认。
4. 检查逻辑一致性：证据描绘的时间线是否支持假设？
5. 检查替代解释：相同的证据模式是否可能来自良性原因？

**输出**：
- `VALIDATED`：所有证据被引用、验证、逻辑一致，没有合理的替代解释。
- `INCONCLUSIVE`：证据支持假设但存在替代解释或证据不足。
- `REJECTED`：缺失的证据 ID、将未验证证据作为事实引用、检测到逻辑不一致。

被拒绝的假设反馈到阶段 4 进行细化（最多 3 次迭代）。

---

## 阶段 6：最终报告生成

使用 [forensic-report.md](./templates/forensic-report.md) 中的模板填充 `investigation-report.md`。

**强制部分**：
- 执行摘要：一段话的裁决（受损/清洁/不确定），附置信度
- 时间线：所有重大事件的按时间顺序重建，带证据引用
- 已验证假设：每个假设及其状态和支持证据 ID
- 证据注册表：所有 `EV-XXXX` 条目的表格，包含来源、类型和验证状态
- IOC 列表：所有提取和丰富的 Indicators of Compromise
- 保管链：证据如何收集、从哪些来源收集、在什么时间戳收集
- 建议：如果检测到妥协，立即缓解措施；监控建议

**报告规则**：
- 每个事实声明必须至少有一个 `[EV-XXXX]` 引用
- 执行摘要必须说明置信度（高/中/低）
- 所有秘密/凭据必须编辑为 `[REDACTED]`

---

## 阶段 7：完成

1. 运行最终证据计数：`python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json list`
2. 归档完整调查目录。
3. 如果妥协被确认：
   - 列出立即缓解措施（轮换凭据、固定依赖哈希、通知受影响用户）
   - 识别受影响的版本/包
   - 注意披露义务（如果是公共包：与包注册机构协调）
4. 向用户呈现最终的 `investigation-report.md`。

---

## 道德使用指南

此技能旨在用于**防御性安全调查** — 保护开源软件免受供应链攻击。不得用于：

- **骚扰或跟踪**贡献者或维护者
- **人肉搜索** — 将 GitHub 活动与真实身份相关联以进行恶意目的
- **竞争情报** — 未经授权调查专有或内部仓库
- **虚假指控** — 在没有验证证据的情况下发布调查结果（请参阅反幻觉防护栏）

调查应以**最小侵入**原则进行：仅收集验证或反驳假设所需的证据。在发布结果时，遵循负责任的披露实践，并在公开披露之前与受影响的维护者协调。

如果调查揭示了真正的妥协，遵循协调的漏洞披露流程：
1. 首先私下通知仓库维护者
2. 允许合理的补救时间（通常 90 天）
3. 如果发布的包受影响，与包注册机构（npm、PyPI 等）协调
4. 如果适当，提交 CVE

---

## API 速率限制

GitHub REST API 强制执行速率限制，如果管理不当会中断大型调查。

**认证请求**：5,000/小时（需要 `GITHUB_TOKEN` 环境变量或 `gh` CLI 认证）
**未认证请求**：60/小时（不可用于调查）

**最佳实践**：
- 始终认证：`export GITHUB_TOKEN=ghp_...` 或使用 `gh` CLI（自动认证）
- 使用条件请求（`If-None-Match` / `If-Modified-Since` 头）以避免在未更改数据上消耗配额
- 对于分页端点，按顺序获取所有页面 — 不要针对同一端点并行化
- 检查 `X-RateLimit-Remaining` 头；如果低于 100，为 `X-RateLimit-Reset` 时间戳暂停
- BigQuery 有自己的配额（免费层每天 10 TiB）— 始终先 dry run
- Wayback Machine CDX API：没有正式速率限制，但请礼貌（最多 1-2 req/sec）

如果在调查中途达到速率限制，将部分结果记录在证据存储中并在报告中注明限制。

---

## 参考材料

- [github-archive-guide.md](./references/github-archive-guide.md) — BigQuery 查询、CDX API、12 种事件类型
- [evidence-types.md](./references/evidence-types.md) — IOC 分类、证据来源类型、观察类型
- [recovery-techniques.md](./references/recovery-techniques.md) — 恢复已删除的提交、PR、issues
- [investigation-templates.md](./references/investigation-templates.md) — 每种攻击类型的预建假设模板
- [evidence-store.py](./scripts/evidence-store.py) — 用于管理证据 JSON 存储的 CLI 工具
- [forensic-report.md](./templates/forensic-report.md) — 结构化报告模板
