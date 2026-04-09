# OpenThoughts-TBLite 评估环境

此环境在 [OpenThoughts-TBLite](https://huggingface.co/datasets/open-thoughts/OpenThoughts-TBLite) 基准上评估终端智能体，后者是 [Terminal-Bench 2.0](https://www.tbench.ai/leaderboard/terminal-bench/2.0) 的难度校准子集。

## 来源

OpenThoughts-TBLite由 [OpenThoughts](https://www.openthoughts.ai/) 智能体团队与 [Snorkel AI](https://snorkel.ai/) 和 [Bespoke Labs](https://bespokelabs.ai/) 合作创建。原始数据集和文档位于：

- **数据集（来源）：** [open-thoughts/OpenThoughts-TBLite](https://huggingface.co/datasets/open-thoughts/OpenThoughts-TBLite)
- **GitHub：** [open-thoughts/OpenThoughts-TBLite](https://github.com/open-thoughts/OpenThoughts-TBLite)
- **博客文章：** [openthoughts.ai/blog/openthoughts-tblite](https://www.openthoughts.ai/blog/openthoughts-tblite)

## 我们的数据集

我们将源数据转换为与Terminal-Bench 2.0环境相同的模式（预构建的Docker Hub镜像、base64编码的测试tarball等），并发布为：

- **数据集（我们的）：** [NousResearch/openthoughts-tblite](https://huggingface.co/datasets/NousResearch/openthoughts-tblite)
- **Docker镜像：** Docker Hub上的 `nousresearch/tblite-<task-name>:latest`（100个镜像）

转换脚本位于 `scripts/prepare_tblite_dataset.py`。

## 为什么选择TBLite？

Terminal-Bench 2.0是终端智能体最强的前沿评估之一，但当模型得分接近下限时（例如Qwen 3 8B低于1%），许多变化在 aggregate score 看起来相同。TBLite通过使用Claude Haiku 4.5作为参考来校准任务难度来解决这个问题：

| 难度 | 通过率范围 | 任务数 |
|------------|----------------|-------|
| 简单       | >= 70%         | 40    |
| 中等     | 40-69%         | 26    |
| 困难       | 10-39%         | 26    |
| 极端    | < 10%          | 8     |

这提供了足够的可解决问题来快速检测小改进，同时保留了足够的困难任务以避免饱和。TBLite和TB2分数之间的相关性为 **r = 0.911**。

TBLite也比完整TB2快2.6-8倍，使其在迭代循环中实用。

## 用法

```bash
# 运行完整基准
python environments/benchmarks/tblite/tblite_env.py evaluate

# 过滤到特定任务
python environments/benchmarks/tblite/tblite_env.py evaluate \
    --env.task_filter "broken-python,pandas-etl"

# 使用不同的模型
python environments/benchmarks/tblite/tblite_env.py evaluate \
    --server.model_name "qwen/qwen3-30b"
```

## 架构

`TBLiteEvalEnv` 是 `TerminalBench2EvalEnv` 的一个薄子类。所有评估逻辑（智能体循环、Docker沙箱管理、测试验证、指标）都是继承的。只有默认值不同：

| 设置        | TB2                              | TBLite                                  |
|----------------|----------------------------------|-----------------------------------------|
| 数据集        | `NousResearch/terminal-bench-2`  | `NousResearch/openthoughts-tblite`      |
| 任务数          | 89                               | 100                                     |
| 任务超时   | 1800秒（30分钟）                   | 1200秒（20分钟）                          |
| Wandb名称     | `terminal-bench-2`               | `openthoughts-tblite`                   |

## 引用

```bibtex
@software{OpenThoughts-TBLite,
  author = {OpenThoughts-Agent team, Snorkel AI, Bespoke Labs},
  month = Feb,
  title = {{OpenThoughts-TBLite: A High-Signal Benchmark for Iterating on Terminal Agents}},
  howpublished = {https://www.openthoughts.ai/blog/openthoughts-tblite},
  year = {2026}
}
```
