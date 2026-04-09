---
name: solana
description: 使用 Solana RPC + CoinGecko 查询 Solana 区块链数据（USD 定价）— 钱包余额、代币投资组合（带价值）、交易详情、NFT、鲸鱼检测和实时网络统计。无需 API 密钥。
version: 0.2.0
author: Deniz Alagoz (gizdusum), enhanced by KClaw Agent
license: MIT
metadata:
  kclaw:
    tags: [Solana, 区块链, 加密, Web3, RPC, DeFi, NFT]
    related_skills: []
---

# Solana 区块链技能

通过 CoinGecko 丰富 USD 定价的 Solana 链上数据查询。
8 个命令：钱包投资组合、代币信息、交易、活动、NFT、
鲸鱼检测、网络统计和价格查询。

无需 API 密钥。仅使用 Python 标准库（urllib、json、argparse）。

---

## 何时使用

- 用户询问 Solana 钱包余额、代币持有量或投资组合价值
- 用户想要检查特定交易的签名
- 用户想要 SPL 代币元数据、价格、供应量或顶级持有者
- 用户想要某个地址的最近交易历史
- 用户想要钱包拥有的 NFT
- 用户想要找到大额 SOL 转账（鲸鱼检测）
- 用户想要 Solana 网络健康状况、TPS、epoch 或 SOL 价格
- 用户问"BONK/JUP/SOL 的价格是多少？"

---

## 前置要求

辅助脚本仅使用 Python 标准库（urllib、json、argparse）。
无需外部包。

定价数据来自 CoinGecko 的免费 API（无需密钥，速率限制约为 10-30 请求/分钟）。要更快查询，使用 `--no-prices` 标志。

---

## 快速参考

RPC 端点（默认）：https://api.mainnet-beta.solana.com
覆盖：`export SOLANA_RPC_URL=https://your-private-rpc.com`

辅助脚本路径：`~/.kclaw/skills/blockchain/solana/scripts/solana_client.py`

```
python3 solana_client.py wallet   <address> [--limit N] [--all] [--no-prices]
python3 solana_client.py tx       <signature>
python3 solana_client.py token    <mint_address>
python3 solana_client.py activity <address> [--limit N]
python3 solana_client.py nft      <address>
python3 solana_client.py whales   [--min-sol N]
python3 solana_client.py stats
python3 solana_client.py price    <mint_or_symbol>
```

---

## 程序

### 0. 设置检查

```bash
python3 --version

# 可选：设置私有 RPC 以获得更好的速率限制
export SOLANA_RPC_URL="https://api.mainnet-beta.solana.com"

# 确认连接
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py stats
```

### 1. 钱包投资组合

获取 SOL 余额、带有 USD 值的 SPL 代币持有量、NFT 计数和
投资组合总额。代币按价值排序，过滤灰尘，按名称标记已知代币
（BONK、JUP、USDC 等）。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  wallet 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM
```

标志：
- `--limit N` — 显示前 N 个代币（默认：20）
- `--all` — 显示所有代币，不过滤灰尘，无限制
- `--no-prices` — 跳过 CoinGecko 价格查询（更快，仅 RPC）

输出包括：SOL 余额 + USD 价值、按价值排序的带价格代币列表、灰尘计数、NFT 摘要、USD 投资组合总额。

### 2. 交易详情

通过其 base58 签名检查完整交易。显示 SOL 和 USD
的余额变化。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  tx 5j7s8K...your_signature_here
```

输出：slot、时间戳、费用、状态、余额变化（SOL + USD）、
程序调用。

### 3. 代币信息

获取 SPL 代币元数据、当前价格、市值、供应量、
小数位、mint/freeze 权限和前 5 名持有者。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  token DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

输出：名称、符号、小数位、供应量、价格、市值、
带百分比的前 5 名持有者。

### 4. 最近活动

列出地址的最近交易（默认：最近 10 个，最多 25 个）。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  activity 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM --limit 25
```

### 5. NFT 投资组合

列出钱包拥有的 NFT（启发式：amount=1、decimals=0 的 SPL 代币）。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  nft 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM
```

注意：此启发式检测不到压缩 NFT（cNFT）。

### 6. 鲸鱼检测器

扫描最近区块的大额 SOL 转账（带 USD 价值）。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py \
  whales --min-sol 500
```

注意：仅扫描最新区块 — 时间点快照，不是历史数据。

### 7. 网络统计

实时 Solana 网络健康状况：当前 slot、epoch、TPS、供应量、验证者
版本、SOL 价格和市值。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py stats
```

### 8. 价格查询

快速价格检查任何代币（通过 mint 地址或已知符号）。

```bash
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py price BONK
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py price JUP
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py price SOL
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py price DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

已知符号：SOL、USDC、USDT、BONK、JUP、WETH、JTO、mSOL、stSOL、
PYTH、HNT、RNDR、WEN、W、TNSR、DRIFT、bSOL、JLP、WIF、MEW、BOME、PENGU。

---

## 陷阱

- **CoinGecko 速率限制** — 免费层允许约 10-30 请求/分钟。
  价格查询每个代币使用 1 个请求。有很多代币的钱包可能
  无法为所有代币获取价格。使用 `--no-prices` 提高速度。
- **公共 RPC 速率限制** — Solana mainnet 公共 RPC 限制请求。
  对于生产使用，设置 SOLANA_RPC_URL 为私有端点
 （Helius、QuickNode、Triton）。
- **NFT 检测是启发式的** — amount=1 + decimals=0。压缩
  NFT（cNFT）和 Token-2022 NFT 不会出现。
- **鲸鱼检测器仅扫描最新区块** — 不是历史数据。结果
  因您查询的时刻而异。
- **交易历史** — 公共 RPC 保留约 2 天。较旧的交易
  可能不可用。
- **代币名称** — 约 25 个知名代币按名称标记。其他
  显示缩写 mint 地址。使用 `token` 命令获取完整信息。
- **429 时重试** — RPC 和 CoinGecko 调用在速率限制错误时最多重试 2 次
  指数退避。

---

## 验证

```bash
# 应打印当前 Solana slot、TPS 和 SOL 价格
python3 ~/.kclaw/skills/blockchain/solana/scripts/solana_client.py stats
```
