---
name: base
description: 使用USD定价查询Base（以太坊L2）区块链数据 — 钱包余额、代币信息、交易详情、gas分析、合约检查、鲸鱼检测和实时网络统计。使用Base RPC + CoinGecko。无需API密钥。
version: 0.1.0
author: youssefea
license: MIT
metadata:
  kclaw:
    tags: [Base, 区块链, 加密, Web3, RPC, DeFi, EVM, L2, 以太坊]
    related_skills: []
---

# Base区块链技能

通过CoinGecko的USD定价丰富查询Base（以太坊L2）链上数据。
8个命令：钱包投资组合、代币信息、交易、gas分析、
合约检查、鲸鱼检测、网络统计和价格查询。

无需API密钥。仅使用Python标准库（urllib、json、argparse）。

---

## 何时使用

- 用户询问Base钱包余额、代币持有量或投资组合价值
- 用户想要通过哈希检查特定交易
- 用户想要ERC-20代币元数据、价格、供应量或市值
- 用户想要了解Base gas成本和L1数据费用
- 用户想要检查合约（ERC类型检测、代理解析）
- 用户想要找到大额ETH转账（鲸鱼检测）
- 用户想要Base网络健康状况、gas价格或ETH价格
- 用户问"USDC/AERO/DEGEN/ETH的价格是多少？"

---

## 前置要求

辅助脚本仅使用Python标准库（urllib、json、argparse）。
无需外部包。

定价数据来自CoinGecko的免费API（无需密钥，速率限制约为10-30请求/分钟）。要更快查询，使用`--no-prices`标志。

---

## 快速参考

RPC端点（默认）：https://mainnet.base.org
覆盖：`export BASE_RPC_URL=https://your-private-rpc.com`

辅助脚本路径：`~/.kclaw/skills/blockchain/base/scripts/base_client.py`

```
python3 base_client.py wallet   <address> [--limit N] [--all] [--no-prices]
python3 base_client.py tx       <hash>
python3 base_client.py token    <contract_address>
python3 base_client.py gas
python3 base_client.py contract <address>
python3 base_client.py whales   [--min-eth N]
python3 base_client.py stats
python3 base_client.py price    <contract_address_or_symbol>
```

---

## 程序

### 0. 设置检查

```bash
python3 --version

# 可选：设置私有RPC以获得更好的速率限制
export BASE_RPC_URL="https://mainnet.base.org"

# 确认连接
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py stats
```

### 1. 钱包投资组合

获取ETH余额和带USD价值的ERC-20代币持有量。
通过链上`balanceOf`调用检查约15个知名Base代币（USDC、WETH、AERO、DEGEN等）。
代币按价值排序，过滤灰尘。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py \
  wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
```

标志：
- `--limit N` — 显示前N个代币（默认：20）
- `--all` — 显示所有代币，不过滤灰尘，无限制
- `--no-prices` — 跳过CoinGecko价格查询（更快，仅RPC）

输出包括：ETH余额 + USD价值、按价值排序的带价格代币列表、灰尘计数、USD投资组合总额。

注意：仅检查已知代币。未知ERC-20不会被发现。
对任何代币使用带有特定合约地址的`token`命令。

### 2. 交易详情

通过哈希检查完整交易。显示ETH价值转移、
gas使用、ETH/USD费用、状态和解码的ERC-20/ERC-721转账。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py \
  tx 0xabc123...your_tx_hash_here
```

输出：哈希、区块、from、to、价值（ETH + USD）、gas价格、gas使用、
费用、状态、合约创建地址（如果有）、代币转账。

### 3. 代币信息

获取ERC-20代币元数据：名称、符号、小数位、总供应量、价格、
市值和合约代码大小。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py \
  token 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
```

输出：名称、符号、小数位、总供应量、价格、市值。
通过eth_call直接从合约读取名称/符号/小数位。

### 4. Gas分析

详细gas分析，包括常见操作的成本估算。
显示当前gas价格、过去10个区块的base费用趋势、区块
利用率和ETH转账、ERC-20转账及swap的估计成本。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py gas
```

输出：当前gas价格、base费用、区块利用率、10个区块趋势、
ETH和USD成本估算。

注意：Base是L2 — 实际交易成本包括L1数据
发布费用，取决于calldata大小和L1 gas价格。显示的估算是仅L2执行成本。

### 5. 合约检查

检查地址：确定是EOA还是合约、检测
ERC-20/ERC-721/ERC-1155接口、解析EIP-1967代理
实现地址。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py \
  contract 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
```

输出：is_contract、代码大小、ETH余额、检测到的接口
（ERC-20、ERC-721、ERC-1155）、ERC-20元数据、代理实现
地址。

### 6. 鲸鱼检测器

扫描最新区块的大额ETH转账及USD价值。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py \
  whales --min-eth 1.0
```

注意：仅扫描最新区块 — 时间点快照，不是历史数据。
默认阈值是1.0 ETH（低于Solana的默认阈值，因为ETH
价值更高）。

### 7. 网络统计

实时Base网络健康状况：最新区块、链ID、gas价格、base费用、
区块利用率、交易计数和ETH价格。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py stats
```

### 8. 价格查询

通过合约地址或已知符号快速查询任何代币价格。

```bash
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py price ETH
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py price USDC
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py price AERO
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py price DEGEN
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py price 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
```

已知符号：ETH、WETH、USDC、cbETH、AERO、DEGEN、TOSHI、BRETT、
WELL、wstETH、rETH、cbBTC。

---

## 陷阱

- **CoinGecko速率限制** — 免费层允许约10-30请求/分钟。
  价格查询每个代币使用1个请求。使用`--no-prices`提高速度。
- **公共RPC速率限制** — Base的公共RPC限制请求。
  对于生产使用，将BASE_RPC_URL设置为私有端点
  （Alchemy、QuickNode、Infura）。
- **钱包仅显示已知代币** — 与Solana不同，EVM链没有
  内置的"获取所有代币"RPC。钱包命令通过`balanceOf`检查约15个流行
  Base代币。未知ERC-20不会出现。使用
  `token`命令获取任何特定合约。
- **代币名称从合约读取** — 如果合约没有实现
  `name()`或`symbol()`，这些字段可能为空。已知代币有
  硬编码标签作为后备。
- **Gas估算是仅L2** — Base交易成本包括L1
  数据发布费用（取决于calldata大小和L1 gas价格）。gas
  命令仅估算L2执行成本。
- **鲸鱼检测器仅扫描最新区块** — 不是历史数据。结果
  因查询时刻而异。默认阈值是1.0 ETH。
- **代理检测** — 仅检测EIP-1967代理。其他代理
  模式（EIP-1167最小代理、自定义存储槽）不检查。
- **429时重试** — RPC和CoinGecko调用在速率限制错误时最多重试2次
  指数退避。

---

## 验证

```bash
# 应打印Base链ID（8453）、最新区块、gas价格和ETH价格
python3 ~/.kclaw/skills/blockchain/base/scripts/base_client.py stats
```
