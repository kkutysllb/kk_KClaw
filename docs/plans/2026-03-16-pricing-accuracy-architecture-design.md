# 定价准确性架构

日期：2026-03-16

## 目标

KClaw 应仅在有官方来源支持用户实际计费路径时才显示相关美元或人民币计价成本。

本设计替换当前静态的启发式定价流程：

- `run_agent.py`
- `agent/usage_pricing.py`
- `agent/insights.py`
- `cli.py`

使用提供商感知的定价系统，该系统：

- 正确处理缓存计费
- 区分 `实际` 与 `估计` 与 `包含` 与 `未知`
- 在提供商暴露权威计费数据时进行事后成本协调
- 支持直接提供商、OpenRouter、订阅、企业定价和自定义端点

## 当前设计中的问题

当前 KClaw 行为有四个结构性问题：

1. 它仅存储 `prompt_tokens` 和 `completion_tokens`，这对于单独对缓存读取和缓存写入计费的提供商来说是不够的。
2. 它使用静态模型价格表和模糊启发式方法，可能偏离当前的官方定价。
3. 它假设公共 API 列表定价与用户的真实计费路径一致。
4. 它没有区分实时估计和协调后的计费成本。

## 设计原则

1. 定价前先规范化使用量。
2. 永远不要将缓存 token 合并为普通输入成本。
3. 明确跟踪确定性。
4. 将计费路径视为模型身份的一部分。
5. 优先使用官方机器可读源而非抓取的文档。
6. 在可用时使用事后提供商成本 API。
7. 显示 `n/a` 而不是编造精度。

## 高层架构

新系统有四个层次：

1. `usage_normalization`
   将原始提供商使用量转换为规范使用记录。
2. `pricing_source_resolution`
   确定计费路径、可信来源和适用的定价源。
3. `cost_estimation_and_reconciliation`
   在可能时产生即时估计，然后稍后用实际计费成本替换或注释它。
4. `presentation`
   `/usage`、`/insights` 和状态栏显示带有确定性元数据的成本。

## 规范使用记录

添加一个规范使用模型，每个提供商路径在任何定价计算之前都映射到该模型。

建议结构：

```python
@dataclass
class CanonicalUsage:
    provider: str
    billing_provider: str
    model: str
    billing_route: str

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    request_count: int = 1

    raw_usage: dict[str, Any] | None = None
    raw_usage_fields: dict[str, str] | None = None
    computed_fields: set[str] | None = None

    provider_request_id: str | None = None
    provider_generation_id: str | None = None
    provider_response_id: str | None = None
```

规则：

- `input_tokens` 仅表示非缓存输入。
- `cache_read_tokens` 和 `cache_write_tokens` 永远不合并到 `input_tokens`。
- `output_tokens` 不包括缓存指标。
- `reasoning_tokens` 是遥测数据，除非提供商正式单独计费。

这是与 `opencode` 相同的规范化模式，增加了出处和协调 ID。

## 提供商规范化规则

### OpenAI 直连

源使用字段：

- `prompt_tokens`
- `completion_tokens`
- `prompt_tokens_details.cached_tokens`

规范化：

- `cache_read_tokens = cached_tokens`
- `input_tokens = prompt_tokens - cached_tokens`
- `cache_write_tokens = 0` 除非 OpenAI 在相关路由中暴露
- `output_tokens = completion_tokens`

### Anthropic 直连

源使用字段：

- `input_tokens`
- `output_tokens`
- `cache_read_input_tokens`
- `cache_creation_input_tokens`

规范化：

- `input_tokens = input_tokens`
- `output_tokens = output_tokens`
- `cache_read_tokens = cache_read_input_tokens`
- `cache_write_tokens = cache_creation_input_tokens`

### OpenRouter

估计时间的使用的规范化应尽可能使用与基础提供商相同的规则从响应使用负载中进行。

协调时间的记录还应存储：

- OpenRouter 生成 ID
- 可用时的原生 token 字段
- `total_cost`
- `cache_discount`
- `upstream_inference_cost`
- `is_byok`

### Gemini / Vertex

在可用时使用官方 Gemini 或 Vertex 使用字段。

如果暴露了缓存内容 token：

- 将它们映射到 `cache_read_tokens`

如果路由没有暴露缓存创建指标：

- 存储 `cache_write_tokens = 0`
- 为以后扩展保留原始使用负载

### DeepSeek 和其他直接提供商

仅规范化正式暴露的字段。

如果提供商没有暴露缓存桶：

- 除非提供商明确记录如何推导，否则不要推断

### 订阅 / 包含成本路由

这些仍使用规范使用模型。

Token 正常跟踪。成本取决于计费模式，而非是否存在使用。

## 计费路由模型

KClaw 必须停止仅通过 `model` 进行定价。

引入计费路由描述符：

```python
@dataclass
class BillingRoute:
    provider: str
    base_url: str | None
    model: str
    billing_mode: str
    organization_hint: str | None = None
```

`billing_mode` 值：

- `official_cost_api`
- `official_generation_api`
- `official_models_api`
- `official_docs_snapshot`
- `subscription_included`
- `user_override`
- `custom_contract`
- `unknown`

示例：

- 带 Costs API 访问权限的 OpenAI 直连 API：`official_cost_api`
- 带 Usage & Cost API 访问权限的 Anthropic 直连 API：`official_cost_api`
- 协调前的 OpenRouter 请求：`official_models_api`
- 生成查找后的 OpenRouter 请求：`official_generation_api`
- GitHub Copilot 风格订阅路由：`subscription_included`
- 本地 OpenAI 兼容服务器：`unknown`
- 带配置费率的企业合同：`custom_contract`

## 成本状态模型

每个显示的成本应包含：

```python
@dataclass
class CostResult:
    amount_usd: Decimal | None
    status: Literal["actual", "estimated", "included", "unknown"]
    source: Literal[
        "provider_cost_api",
        "provider_generation_api",
        "provider_models_api",
        "official_docs_snapshot",
        "user_override",
        "custom_contract",
        "none",
    ]
    label: str
    fetched_at: datetime | None
    pricing_version: str | None
    notes: list[str]
```

展示规则：

- `actual`：显示美元金额作为最终结果
- `estimated`：带估计标签显示美元金额
- `included`：根据 UX 选择显示 `included` 或 `$0.00 (included)`
- `unknown`：显示 `n/a`

## 官方来源层次

按此顺序解析成本：

1. 请求级或账户级官方计费成本
2. 官方机器可读模型定价
3. 官方文档快照
4. 用户覆盖或自定义合同
5. 未知

如果当前计费路由存在更高置信度来源，系统不得跳到较低级别。

## 提供商特定真相规则

### OpenAI 直连

优先真相：

1. 用于协调后支出的 Costs API
2. 用于实时估计的官方定价页面

### Anthropic 直连

优先真相：

1. 用于协调后支出的 Usage & Cost API
2. 用于实时估计的官方定价文档

### OpenRouter

优先真相：

1. `GET /api/v1/generation` 用于协调后的 `total_cost`
2. `GET /api/v1/models` 定价用于实时估计

不要使用底层提供商公共定价作为 OpenRouter 计费的真相来源。

### Gemini / Vertex

优先真相：

1. 路由可用时用于协调后支出的官方计费导出或计费 API
2. 用于估计的官方定价文档

### DeepSeek

优先真相：

1. 未来可用时的官方机器可读成本源
2. 今天的官方定价文档快照

### 订阅包含路由

优先真相：

1. 明确将模型标记为包含在订阅中的路由配置

这些应显示 `included`，而非 API 列表价格估计。

### 自定义端点 / 本地模型

优先真相：

1. 用户覆盖
2. 自定义合同配置
3. 未知

这些默认为 `unknown`。

## 定价目录

用更丰富的定价目录替换当前的 `MODEL_PRICING` 字典。

建议记录：

```python
@dataclass
class PricingEntry:
    provider: str
    route_pattern: str
    model_pattern: str

    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None
    cache_read_cost_per_million: Decimal | None = None
    cache_write_cost_per_million: Decimal | None = None
    request_cost: Decimal | None = None
    image_cost: Decimal | None = None

    source: str = "official_docs_snapshot"
    source_url: str | None = None
    fetched_at: datetime | None = None
    pricing_version: str | None = None
```

目录应感知路由：

- `openai:gpt-5`
- `anthropic:claude-opus-4-6`
- `openrouter:anthropic/claude-opus-4.6`
- `copilot:gpt-4o`

这避免了直接提供商计费与聚合商计费的混淆。

## 定价同步架构

引入定价同步子系统而不是手动维护单个硬编码表。

建议模块：

- `agent/pricing/catalog.py`
- `agent/pricing/sources.py`
- `agent/pricing/sync.py`
- `agent/pricing/reconcile.py`
- `agent/pricing/types.py`

### 同步源

- OpenRouter 模型 API
- 不存在 API 时的官方提供商文档快照
- 配置中的用户覆盖

### 同步输出

本地缓存定价条目并附带：

- 源 URL
- 获取时间戳
- 版本/哈希
- 置信度/源类型

### 同步频率

- 启动时预热缓存
- 根据源不同每 6 到 24 小时后台刷新一次
- 手动 `kclaw pricing sync`

## 协调架构

实时请求最初可能仅产生估计。当提供商暴露实际计费成本时，KClaw 应稍后协调它们。

建议流程：

1. 代理调用完成。
2. KClaw 存储规范使用量加上协调 ID。
3. KClaw 如果存在定价源则计算即时估计。
4. 支持时，协调工作器获取实际成本。
5. 会话和消息记录用 `实际` 成本更新。

这可以运行：

- 对于廉价查找为内联
- 对于延迟提供商会计为异步

## 持久性更改

会话存储应停止仅存储聚合 prompt/completion 总数。

添加使用量和成本确定性的字段：

- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `estimated_cost_usd`
- `actual_cost_usd`
- `cost_status`
- `cost_source`
- `pricing_version`
- `billing_provider`
- `billing_mode`

如果模式扩展对于一个 PR 太大，添加新的定价事件表：

```text
session_cost_events
  id
  session_id
  request_id
  provider
  model
  billing_mode
  input_tokens
  output_tokens
  cache_read_tokens
  cache_write_tokens
  estimated_cost_usd
  actual_cost_usd
  cost_status
  cost_source
  pricing_version
  created_at
  updated_at
```

## KClaw 接触点

### `run_agent.py`

当前职责：

- 解析原始提供商使用量
- 更新会话 token 计数器

新职责：

- 构建 `CanonicalUsage`
- 更新规范计数器
- 存储协调 ID
- 向定价子系统发出使用事件

### `agent/usage_pricing.py`

当前职责：

- 静态查找表
- 直接成本计算

新职责：

- 移动或替换为定价目录外观
- 无模糊模型系列启发式方法
- 无计费路由上下文的直接定价

### `cli.py`

当前职责：

- 直接从 prompt/completion 总数计算会话成本

新职责：

- 显示 `CostResult`
- 显示状态徽章：
  - `actual`
  - `estimated`
  - `included`
  - `n/a`

### `agent/insights.py`

当前职责：

- 从静态定价重新计算历史估计

新职责：

- 聚合存储的定价事件
- 优先使用实际成本而非估计
- 仅在协调不可用时显示估计

## UX 规则

### 状态栏

显示以下之一：

- `$1.42`
- `~$1.42`
- `included`
- `cost n/a`

其中：

- `$1.42` 表示 `actual`
- `~$1.42` 表示 `estimated`
- `included` 表示订阅支持或明确零成本路由
- `cost n/a` 表示未知

### `/usage`

显示：

- token 桶
- 估计成本
- 可用时的实际成本
- 成本状态
- 定价源

### `/insights`

聚合：

- 实际成本总计
- 仅估计总计
- 未知成本会话数
- 包含成本会话数

## 配置和覆盖

在配置中添加用户可配置的定价覆盖：

```yaml
pricing:
  mode: hybrid
  sync_on_startup: true
  sync_interval_hours: 12
  overrides:
    - provider: openrouter
      model: anthropic/claude-opus-4.6
      billing_mode: custom_contract
      input_cost_per_million: 4.25
      output_cost_per_million: 22.0
      cache_read_cost_per_million: 0.5
      cache_write_cost_per_million: 6.0
  included_routes:
    - provider: copilot
      model: "*"
    - provider: codex-subscription
      model: "*"
```

对于匹配的计费路由，覆盖必须优先于目录默认值。

## 推出计划

### 第一阶段

- 添加规范使用模型
- 在 `run_agent.py` 中拆分缓存 token 桶
- 停止对缓存膨胀的 prompt 总数定价
- 用改进的后端数学保留当前 UI

### 第二阶段

- 添加路由感知定价目录
- 集成 OpenRouter 模型 API 同步
- 添加 `estimated` vs `included` vs `unknown`

### 第三阶段

- 为 OpenRouter 生成成本添加协调
- 添加实际成本持久性
- 更新 `/insights` 以优先使用实际成本

### 第四阶段

- 添加直接 OpenAI 和 Anthropic 协调路径
- 添加用户覆盖和合同定价
- 添加定价同步 CLI 命令

## 测试策略

添加测试：

- OpenAI 缓存 token 减法
- Anthropic 缓存读取/写入分离
- OpenRouter 估计 vs 实际协调
- 显示 `included` 的订阅支持模型
- 显示 `n/a` 的自定义端点
- 覆盖优先级
- 陈旧目录回退行为

假设启发式定价的当前测试应用路由感知期望替换。

## 非目标

- 在没有官方来源或用户覆盖的情况下精确重建企业计费
- 为缺少缓存桶数据的旧会话填充完美历史成本
- 在请求时抓取任意提供商网页

## 建议

不要扩展现有的 `MODEL_PRICING` 字典。

该路径无法满足产品需求。KClaw 应改为迁移到：

- 规范使用规范化
- 路由感知定价源
- 估计-然后-协调成本生命周期
- UI 中明确的确定性状态

这是使声明"KClaw 定价在可能的情况下由官方来源支持，否则明确标记"站得住脚的最低架构。
