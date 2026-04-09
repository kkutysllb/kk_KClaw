---
name: usage-patterns
description: 测试环境和评估模型的使用模式，涵盖process模式、evaluate模式、serve模式等。
---

# 使用模式 — 测试环境和评估模型

## 模式1：测试环境工作（process模式）

使用`process`模式来验证环境端到端运行，然后再进行完整设置。这会生成轨迹而不需要Atropos训练服务器。

**运行前：** 询问用户的推理设置（参见SKILL.md"推理设置"部分）。用他们选择的具体值替换下面示例中的`<BASE_URL>`、`<MODEL>`和`<SERVER_TYPE>`。

### 步骤1：运行1条轨迹

```bash
cd ~/.kclaw/kclaw
source venv/bin/activate

python environments/your_env.py process \
  --env.total_steps 1 \
  --env.group_size 1 \
  --env.use_wandb false \
  --env.data_path_to_save_groups /tmp/test_output.jsonl \
  --openai.base_url "<BASE_URL>" \
  --openai.model_name "<MODEL>" \
  --openai.server_type <SERVER_TYPE> \
  --openai.health_check false
```

### 步骤2：验证输出

```python
import json
for line in open("/tmp/test_output.jsonl"):
    data = json.loads(line)
    print(f"分数: {data.get('scores', [])}")
    print(f"令牌序列数: {len(data.get('tokens', []))}")
    # 检查消息包含工具调用
    for msg_list in data.get("messages", []):
        roles = [m.get("role") for m in msg_list]
        print(f"角色: {roles}")
        for m in reversed(msg_list):
            if m.get("role") == "assistant" and m.get("content"):
                print(f"响应: {m['content'][:200]}...")
                break
```

### 检查项：
- **分数不全为0.0** — 如果是，compute_reward有问题
- **分数在[0, 1]范围内** — 不能为负数，不能大于1
- **消息包含"tool"角色条目** — 智能体使用了工具
- **令牌序列非空**
- **在.jsonl旁边生成了HTML可视化**

### 常见失败：
- `'AgentResult' object has no attribute 'X'` — 访问了不存在的字段。参见agentresult-fields.md。
- 分数始终为0.0 — 奖励函数静默出错
- 分数始终为1.0 — 验证太宽松或未运行


## 模式2：评估模型（evaluate模式）

使用`evaluate`模式在环境的评估分割上对模型进行基准测试。这会为每个评估项目运行带工具的完整智能体循环。

### 步骤1：运行评估

```bash
python environments/your_env.py evaluate \
  --env.eval_size 20 \
  --env.use_wandb false \
  --env.data_dir_to_save_evals /tmp/eval_results \
  --openai.base_url "<BASE_URL>" \
  --openai.model_name "<MODEL>" \
  --openai.server_type <SERVER_TYPE> \
  --openai.health_check false
```

### 步骤2：读取结果

标准输出显示lighteval兼容的表格：

```
评估结果: your-env_eval
|指标          |  值|
|平均正确性| 0.850 |
|平均奖励     | 0.920 |
|平均工具调用 | 4.300 |
|项目数         | 20    |
评估完成用时367秒
```

JSON结果保存到评估目录：

```python
import json
data = json.load(open("/tmp/eval_results/metrics.json"))
for metric, value in data["results"]["all"].items():
    print(f"{metric}: {value}")
```

### 步骤3：比较模型

使用不同模型运行评估并比较metrics.json文件。

### 检查项：
- **"data_dir_to_save_evals未设置"** — 忘记了标志，结果不会保存
- **工具使用率 = 0** — evaluate()使用的是chat_completion而不是KClawAgentLoop
- **所有分数相同** — judge失败，回退到启发式
- **非常慢** — 每个项目运行完整智能体循环（~30-90秒）。使用`--env.eval_size 5`进行快速检查。


## 模式3：生成训练数据（process模式，更大规模）

生成用于离线训练或分析的轨迹数据：

```bash
python environments/your_env.py process \
  --env.total_steps 50 \
  --env.group_size 4 \
  --env.use_wandb false \
  --env.data_path_to_save_groups data/trajectories.jsonl \
  --openai.base_url "<BASE_URL>" \
  --openai.model_name "<MODEL>" \
  --openai.server_type <SERVER_TYPE> \
  --openai.health_check false
```

### 分析分布：

```python
import json
scores = []
for line in open("data/trajectories.jsonl"):
    data = json.loads(line)
    scores.extend(data.get("scores", []))

print(f"总数: {len(scores)}, 平均: {sum(scores)/len(scores):.3f}")
for bucket in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
    count = sum(1 for s in scores if abs(s - bucket) < 0.1)
    print(f"  {bucket:.1f}: {'█' * count} ({count})")
```

### 检查项：
- **分数分布有方差** — RL需要分数方差。全相同的分数无用。


## 模式4：完整RL训练（serve模式）

使用Atropos进行实际RL训练：

```bash
# 终端1：启动Atropos API服务器
run-api

# 终端2：启动环境
python environments/your_env.py serve \
  --config environments/your_env/default.yaml
```

使用VLLM的阶段2：

```bash
# 终端1：VLLM服务器
python -m vllm.entrypoints.openai.api_server --model your-model --port 8000

# 终端2：Atropos API
run-api

# 终端3：环境
python environments/your_env.py serve \
  --openai.base_url http://localhost:8000/v1 \
  --openai.model_name your-model \
  --openai.server_type vllm
```


## 模式5：快速冒烟测试

在花钱调用API之前验证导入和配置：

```python
from environments.your_env import YourEnv
print(f"名称: {YourEnv.name}")
cfg, servers = YourEnv.config_init()
print(f"工具集: {cfg.enabled_toolsets}")
print(f"服务器: {servers[0].model_name}")
print("所有导入正常")
```


## 时间预期

| 模式 | 项目数 | 每项目时间 | 总计 |
|------|-------|--------------|-------|
| process（1个项目） | 1 | 30-90秒 | ~1分钟 |
| evaluate（5个项目） | 5 | 30-90秒 | ~5分钟 |
| evaluate（20个项目） | 20 | 30-90秒 | ~15-30分钟 |
| process（50个项目） | 50 | 30-90秒 | ~30-75分钟 |

时间是针对使用Claude Sonnet类模型的云API。本地模型可能更快或更慢，取决于硬件。
