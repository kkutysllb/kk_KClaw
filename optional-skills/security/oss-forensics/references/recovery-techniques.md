# 已删除内容恢复技术

## 关键洞察：GitHub 永远不会完全删除强制推送的提交

强制推送的提交从分支历史中删除，但保留在 GitHub 的服务器上，直到垃圾回收运行（可能需要数周到数月）。这是已删除提交恢复的基础。

---

## 方法 1：直接 GitHub URL（最快 — 无需认证）

如果您有提交 SHA，即使它已被强制推送，也可以直接访问：

```bash
# 查看提交元数据
curl -s "https://github.com/OWNER/REPO/commit/SHA"

# 下载为补丁（包含完整 diff）
curl -s "https://github.com/OWNER/REPO/commit/SHA.patch" > recovered_commit.patch

# 下载为 diff
curl -s "https://github.com/OWNER/REPO/commit/SHA.diff" > recovered_commit.diff

# 示例（Istio 凭据泄露 - 真实事件）：
curl -s "https://github.com/istio/istio/commit/FORCE_PUSHED_SHA.patch"
```

**何时有效**：SHA 已知（来自 GH Archive、Wayback Machine 或 `git fsck`）
**何时失败**：GitHub 已对对象进行垃圾回收（罕见，通常在强制推送后 30-90 天）

---

## 方法 2：GitHub REST API

```bash
# 适用于被强制推送但仍在服务器上的提交
# 注意：/commits/SHA 可能 404，但 /git/commits/SHA 对于孤立提交通常成功
curl -s "https://api.github.com/repos/OWNER/REPO/git/commits/SHA" | jq .

# 获取强制推送提交的树（文件列表）
curl -s "https://api.github.com/repos/OWNER/REPO/git/trees/SHA?recursive=1" | jq .

# 从强制推送提交获取特定文件
curl -s "https://api.github.com/repos/OWNER/REPO/contents/PATH?ref=SHA" | jq .content | base64 -d
```

---

## 方法 3：通过 SHA 的 Git Fetch（本地 — 需要克隆）

```bash
# 直接通过 SHA 将孤立提交获取到本地仓库
cd target_repo
git fetch origin SHA
git log FETCH_HEAD -1   # 查看提交
git diff FETCH_HEAD~1 FETCH_HEAD  # 查看 diff

# 如果 SHA 最近被强制推送，仍可获取
# 这在 GitHub GC 运行后停止工作
```

---

## 方法 4：通过 git fsck 的悬空提交

```bash
cd target_repo

# 查找所有不可达对象（包含强制推送的提交）
git fsck --unreachable --no-reflogs 2>&1 | grep "unreachable commit" | awk '{print $3}' > dangling_shas.txt

# 对于每个悬空提交，获取其元数据
while read sha; do
  echo "=== $sha ===" >> dangling_details.txt
  git show --stat "$sha" >> dangling_details.txt 2>&1
done < dangling_shas.txt

# 注意：悬空对象仅存在于本地克隆 — 与 GitHub 的副本不同
# GitHub 的副本可通过方法 1-3 访问，直到 GC 运行
```

---

## 恢复已删除的 GitHub Issues 和 PRs

### 通过 Wayback Machine CDX API

```bash
# 查找特定 issue 的所有归档快照
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO/issues/NUMBER&output=json&limit=50&fl=timestamp,statuscode,original" | python3 -m json.tool

# 获取最佳快照
# 使用 CDX 结果中的时间戳：
# https://web.archive.org/web/TIMESTAMP/https://github.com/OWNER/REPO/issues/NUMBER
curl -s "https://web.archive.org/web/TIMESTAMP/https://github.com/OWNER/REPO/issues/NUMBER" > issue_NUMBER_archived.html

# 查找日期范围内仓库的所有快照
curl -s "https://web.archive.org/cdx/search/cdx?url=github.com/OWNER/REPO*&output=json&from=20240101&to=20240201&limit=200&fl=timestamp,urlkey,statuscode" | python3 -m json.tool
```

### 通过 GitHub API（有限 — 仅未删除内容）

```bash
# 已关闭的 issues（未删除）可检索
curl -s "https://api.github.com/repos/OWNER/REPO/issues?state=closed&per_page=100" | jq '.[].number'

# 注意：已删除的 issues/PRs 不会出现在 API 中。使用 Wayback Machine 或 GH Archive。
```

### 通过 GitHub Archive（用于事件历史 — 不是内容）

```sql
-- 查找日期范围内仓库的所有 IssueEvents
SELECT created_at, actor.login, payload.action, payload.issue.number, payload.issue.title
FROM `githubarchive.day.*`
WHERE _TABLE_SUFFIX BETWEEN '20240101' AND '20240201'
  AND type = 'IssuesEvent'
  AND repo.name = 'OWNER/REPO'
ORDER BY created_at
```

---

## 从已知提交恢复已删除的文件

```bash
# 如果您有提交 SHA（即使被强制推送）：
git show SHA:path/to/file.py > recovered_file.py

# 或通过 API（base64 编码内容）：
curl -s "https://api.github.com/repos/OWNER/REPO/contents/path/to/file.py?ref=SHA" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
print(base64.b64decode(d['content']).decode())
"
```

---

## 证据记录

恢复任何已删除内容后，立即记录：

```bash
python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json add \
  --source "git fetch origin FORCE_PUSHED_SHA" \
  --content "Recovered commit: FORCE_PUSHED_SHA | Author: attacker@example.com | Date: 2024-01-15 | Added file: malicious.sh" \
  --type git \
  --actor "attacker-handle" \
  --url "https://github.com/OWNER/REPO/commit/FORCE_PUSHED_SHA.patch" \
  --timestamp "2024-01-15T00:00:00Z" \
  --verification single_source \
  --notes "Commit force-pushed off main branch on 2024-01-16. Recovered via direct fetch."
```

---

## 恢复失败模式

| 失败 | 原因 | 解决方案 |
|------|------|----------|
| `git fetch origin SHA` 返回"不是我们的 ref" | GitHub GC 已运行 | 尝试方法 1/2，搜索 Wayback Machine |
| `github.com/OWNER/REPO/commit/SHA` 返回 404 | GC 运行或 SHA 错误 | 通过 GH Archive 验证 SHA；尝试部分 SHA 搜索 |
| Wayback Machine 没有快照 | 页面从未被 IA 抓取 | 检查 `commoncrawl.org`，检查 Google Cache |
| BigQuery 显示事件但没有内容 | GH Archive 存储事件元数据，不是文件内容 | 恢复仅揭示事件发生，不是内容 |
