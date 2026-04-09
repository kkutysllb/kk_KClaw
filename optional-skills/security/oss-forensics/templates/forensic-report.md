# 取证调查报告

> **说明**：填写所有部分。每个事实声明必须至少引用一个 `[EV-XXXX]` 证据 ID。
> 在最终确定之前删除占位符文本和说明注释。将所有秘密编辑为 `[REDACTED]`。

---

## 执行摘要

**目标仓库**：`OWNER/REPO`
**调查期间**：YYYY-MM-DD 至 YYYY-MM-DD
**裁决**：[已入侵 / 清洁 / 不确定]
**置信度**：[高 / 中 / 低]
**报告日期**：YYYY-MM-DD
**调查员**：[代理会话 ID 或分析师姓名]

<!-- 一段话：调查了什么、发现了什么、建议了什么。-->

---

## 事件时间线

> 所有时间戳为 UTC。每个事件必须至少引用一个证据 ID。

| 时间戳 (UTC) | 事件 | 证据 IDs | 来源 |
|-----------------|-------|--------------|--------|
| YYYY-MM-DDTHH:MM:SSZ | _描述事件_ | [EV-XXXX] | git / gh_api / gh_archive / web_archive |
| | | | |

---

## 已验证的假设

### 假设 1：_简短标题_

**状态**：[VALIDATED / INCONCLUSIVE / REJECTED]

**声明**：_假设的完整陈述。_

**支持证据**：
- [EV-XXXX]：_此证据显示什么_
- [EV-YYYY]：_此证据显示什么_

**考虑的反驳证据**：_什么可以反驳这个，为什么它被排除或没有。_

**置信度**：[高 / 中 / 低，以及原因]

---

## 威胁指标 (IOC 列表)

| 类型 | 值 | 状态 | 证据 |
|------|-------|--------|------|
| COMMIT_SHA | `abc123...` | 已确认恶意 | [EV-XXXX] |
| ACTOR_USERNAME | `handle` | 疑似入侵 | [EV-YYYY] |
| FILE_PATH | `src/evil.js` | 已确认恶意 | [EV-ZZZZ] |
| DOMAIN | `evil-cdn.io` | 已确认 C2 | [EV-WWWW] |

---

## 受影响的版本

| 版本 / 标签 | 发布日期 | 包含恶意代码 | 证据 |
|---------------|-----------|------------------------|------|
| `v1.2.3` | YYYY-MM-DD | 是 / 否 / 未知 | [EV-XXXX] |

---

## 证据注册表

> 由以下命令生成：`python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json export`

<!-- 在此处粘贴 evidence-store.py 导出命令的 Markdown 表格输出 -->

| ID | 类型 | 来源 | 参与者 | 验证状态 | 事件时间戳 | URL |
|----|------|--------|-------|--------------|-----------------|-----|
| EV-0001 | | | | | | |

---

## 保管链

> 由以下命令生成：`python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json export`

<!-- 在此处粘贴导出输出中的保管链部分 -->

| 证据 ID | 行动 | 时间戳 | 来源 |
|-------------|--------|-----------|--------|
| EV-0001 | add | | |

---

## 技术发现

### Git 历史分析

_总结本地 git 分析的发现：悬空提交、reflog 异常、未签名提交、二进制添加等。_

### GitHub API 分析

_总结 GitHub REST API 的发现：已删除的 PR/issues、贡献者更改、发布异常等。_

### GitHub Archive 分析

_总结 BigQuery 的发现：强制推送事件、删除事件、工作流异常、成员更改等。_
_注意：如果 BigQuery 不可用，明确说明。_

### Wayback Machine 分析

_总结 archive.org 的发现：恢复的已删除页面、历史内容差异等。_

### IOC 丰富

_总结丰富结果：域名的 WHOIS 数据、恢复的提交内容、参与者账户分析等。_

---

## 建议

### 立即行动（如确认入侵）

- [ ] 轮换所有可能已暴露的 GitHub token、API 密钥和凭据
- [ ] 在所有受影响的包中将依赖项版本固定到哈希
- [ ] 如适用，发布安全咨询 / CVE
- [ ] 通知下游用户/包注册商（npm、PyPI 等）
- [ ] 撤销被入侵账户的访问权限，并用硬件 2FA 重新保护
- [ ] 审计所有 CI/CD 工作流文件以查找未授权修改
- [ ] 审查入侵窗口期间发布的所有版本

### 监控建议

- [ ] 在 `main`/`master` 上启用分支保护（要求代码审查、禁止强制推送）
- [ ] 启用必需的提交签名（GPG/SSH）
- [ ] 设置 GitHub 审计日志流以进行未来监控
- [ ] 在锁文件中将关键依赖项固定到已知良好的 SHA

---

## 限制和注意事项

- _列出任何不可用的数据来源（例如，没有 BigQuery 访问权限）_
- _注意任何仅为单一来源的证据（未独立验证）_
- _注意任何无法确认或否认的假设_

---

## 参考

- 证据存储：`evidence.json`（SHA-256 完整性：运行 `python3 SKILL_DIR/scripts/evidence-store.py --store evidence.json verify`）
- 相关问题：[链接到 GitHub issues、CVE、安全咨询]
- RAPTOR 框架：https://github.com/gadievron/raptor
