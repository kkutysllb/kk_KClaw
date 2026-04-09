---
name: domain-intel
description: 使用 Python stdlib 进行被动域名侦察。子域名发现、SSL 证书检查、WHOIS 查询、DNS 记录、域名可用性检查和批量多域名分析。无需 API 密钥。
---

# 域名情报 — 被动 OSINT

仅使用 Python stdlib 进行被动域名侦察。
**零依赖。零 API 密钥。在 Linux、macOS 和 Windows 上工作。**

## 辅助脚本

此技能包含 `scripts/domain_intel.py` — 一个用于所有域名情报操作的完整 CLI 工具。

```bash
# 通过证书透明度日志进行子域名发现
python3 SKILL_DIR/scripts/domain_intel.py subdomains example.com

# SSL 证书检查（过期、密码套件、SAN、颁发者）
python3 SKILL_DIR/scripts/domain_intel.py ssl example.com

# WHOIS 查询（注册商、日期、名称服务器 — 100+ TLD）
python3 SKILL_DIR/scripts/domain_intel.py whois example.com

# DNS 记录（A、AAAA、MX、NS、TXT、CNAME）
python3 SKILL_DIR/scripts/domain_intel.py dns example.com

# 域名可用性检查（被动：DNS + WHOIS + SSL 信号）
python3 SKILL_DIR/scripts/domain_intel.py available coolstartup.io

# 批量分析 — 多个域名、多个检查并行
python3 SKILL_DIR/scripts/domain_intel.py bulk example.com github.com google.com
python3 SKILL_DIR/scripts/domain_intel.py bulk example.com github.com --checks ssl,dns
```

`SKILL_DIR` 是包含此 SKILL.md 文件的目录。所有输出都是结构化 JSON。

## 可用命令

| 命令 | 功能 | 数据来源 |
|------|------|----------|
| `subdomains` | 从证书日志中发现子域名 | crt.sh (HTTPS) |
| `ssl` | 检查 TLS 证书详情 | 直接 TCP:443 到目标 |
| `whois` | 注册信息、注册商、日期 | WHOIS 服务器 (TCP:43) |
| `dns` | A、AAAA、MX、NS、TXT、CNAME 记录 | 系统 DNS + Google DoH |
| `available` | 检查域名是否已注册 | DNS + WHOIS + SSL 信号 |
| `bulk` | 对多个域名运行多个检查 | 以上全部 |

## 何时使用此工具与内置工具

- **使用此技能** 用于基础设施问题：子域名、SSL 证书、WHOIS、DNS 记录、可用性
- **使用 `web_search`** 用于关于域名/公司做什么的一般研究
- **使用 `web_extract`** 获取网页的实际内容
- **使用 `terminal` 配合 `curl -I`** 进行简单的"此 URL 是否可访问"检查

| 任务 | 更好的工具 | 为什么 |
|------|------------|--------|
| "example.com 做什么？" | `web_extract` | 获取页面内容，而不是 DNS/WHOIS 数据 |
| "查找关于公司的信息" | `web_search` | 一般研究，不是特定于域名的 |
| "这个网站安全吗？" | `web_search` | 声誉检查需要网络上下文 |
| "检查 URL 是否可访问" | `terminal` 配合 `curl -I` | 简单的 HTTP 检查 |
| "找到 X 的子域名" | **此技能** | 这是唯一的被动来源 |
| "SSL 证书什么时候过期？" | **此技能** | 内置工具无法检查 TLS |
| "谁注册了这个域名？" | **此技能** | WHOIS 数据不在网络搜索中 |
| "coolstartup.io 可用吗？" | **此技能** | 通过 DNS+WHOIS+SSL 的被动可用性 |

## 平台兼容性

纯 Python stdlib（`socket`、`ssl`、`urllib`、`json`、`concurrent.futures`）。
在 Linux、macOS 和 Windows 上完全相同地工作，无依赖。

- **crt.sh 查询** 使用 HTTPS（端口 443）— 在大多数防火墙后工作
- **WHOIS 查询** 使用 TCP 端口 43 — 在严格网络上可能被阻止
- **DNS 查询** 使用 Google DoH（HTTPS）用于 MX/NS/TXT — 防火墙友好
- **SSL 检查** 连接到目标端口 443 — 这是唯一的"主动"操作

## 数据来源

所有查询都是**被动的** — 无端口扫描、无漏洞测试：

- **crt.sh** — 证书透明度日志（子域名发现，仅 HTTPS）
- **WHOIS 服务器** — 直接 TCP 到 100+ 权威 TLD 注册商
- **Google DNS-over-HTTPS** — MX、NS、TXT、CNAME 解析（防火墙友好）
- **系统 DNS** — A/AAAA 记录解析
- **SSL 检查** 是唯一的"主动"操作（到目标:443 的 TCP 连接）

## 注意

- WHOIS 查询使用 TCP 端口 43 — 在严格网络上可能被阻止
- 一些 WHOIS 服务器会编辑注册人信息（GDPR）— 向用户提及这一点
- 对于非常流行的域名（数千个证书），crt.sh 可能很慢 — 设置合理的预期
- 可用性检查是基于启发式的（3 个被动信号）— 不像注册商 API 那样权威

---

*由 [@FurkanL0](https://github.com/FurkanL0) 贡献*
