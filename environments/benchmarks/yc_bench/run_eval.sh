#!/bin/bash

# YC-Bench 评估
#
# 需要: pip install "kclaw[yc-bench]"
#
# 从仓库根目录运行:
#   bash environments/benchmarks/yc_bench/run_eval.sh
#
# 覆盖模型:
#   bash environments/benchmarks/yc_bench/run_eval.sh \
#       --openai.model_name anthropic/claude-opus-4-20250514
#
# 运行单个 preset:
#   bash environments/benchmarks/yc_bench/run_eval.sh \
#       --env.presets '["fast_test"]' --env.seeds '[1]'

set -euo pipefail

mkdir -p logs evals/yc-bench
LOG_FILE="logs/yc_bench_$(date +%Y%m%d_%H%M%S).log"

echo "YC-Bench 评估"
echo "日志: $LOG_FILE"
echo ""

PYTHONUNBUFFERED=1 LOGLEVEL="${LOGLEVEL:-INFO}" \
  python environments/benchmarks/yc_bench/yc_bench_env.py evaluate \
  --config environments/benchmarks/yc_bench/default.yaml \
  "$@" \
  2>&1 | tee "$LOG_FILE"

echo ""
echo "日志已保存到: $LOG_FILE"
