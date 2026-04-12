#!/bin/bash

# Terminal-Bench 2.0 评估
#
# 从仓库根目录运行:
#   bash environments/benchmarks/terminalbench_2/run_eval.sh
#
# 覆盖模型:
#   bash environments/benchmarks/terminalbench_2/run_eval.sh \
#       --openai.model_name anthropic/claude-sonnet-4
#
# 运行子集:
#   bash environments/benchmarks/terminalbench_2/run_eval.sh \
#       --env.task_filter fix-git,git-multibranch
#
# 所有终端设置（后端、超时、生命周期、池大小）都
# 通过环境配置字段配置 -- 不需要环境变量。

set -euo pipefail

mkdir -p logs evals/terminal-bench-2
LOG_FILE="logs/terminalbench2_$(date +%Y%m%d_%H%M%S).log"

echo "Terminal-Bench 2.0 评估"
echo "日志文件: $LOG_FILE"
echo ""

# 无缓冲 Python 输出，使日志实时写入
export PYTHONUNBUFFERED=1

# 显示 INFO 级别的 Agent 循环计时（每轮 API/工具耗时）
# 这些写入日志文件; tqdm + [开始]/[通过]/[失败] 输出到终端
export LOGLEVEL=INFO

python terminalbench2_env.py evaluate \
  --config default.yaml \
  "$@" \
  2>&1 | tee "$LOG_FILE"

echo ""
echo "日志已保存到: $LOG_FILE"
echo "评估结果: evals/terminal-bench-2/"
