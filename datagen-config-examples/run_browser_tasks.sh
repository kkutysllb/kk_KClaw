#!/bin/bash

# =============================================================================
# 示例：浏览器聚焦的数据生成
# =============================================================================
#
# 生成用于浏览器自动化任务的工具调用轨迹。
# Agent 可以导航网站、填写表单、提取信息等。
#
# 分布：浏览器 97%，网页 20%，视觉 12%，终端 15%
#
# 前置条件：
#   - ~/.kclaw/.env 中配置了 OPENROUTER_API_KEY
#   - ~/.kclaw/.env 中配置了 BROWSERBASE_API_KEY（用于浏览器工具）
#   - 一个 JSONL 格式的数据集文件，每行包含一个 {"prompt": "..."}
#
# 用法：
#   cd ~/.kclaw/kclaw
#   bash datagen-config-examples/run_browser_tasks.sh
#
# 输出：data/browser_tasks_example/trajectories.jsonl
# =============================================================================
mkdir -p logs

LOG_FILE="logs/browser_tasks_$(date +%Y%m%d_%H%M%S).log"
echo "📝 Logging to: $LOG_FILE"

# Point to the example dataset in this directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

python batch_runner.py \
  --dataset_file="$SCRIPT_DIR/example_browser_tasks.jsonl" \
  --batch_size=5 \
  --run_name="browser_tasks_example" \
  --distribution="browser_tasks" \
  --model="anthropic/claude-sonnet-4" \
  --base_url="https://openrouter.ai/api/v1" \
  --num_workers=3 \
  --max_turns=30 \
  --ephemeral_system_prompt="You are an AI assistant with browser automation capabilities. Your primary task is to navigate and interact with web pages to accomplish user goals.

IMPORTANT GUIDELINES:

1. SEARCHING: Do NOT search directly on Google via the browser — they block automated searches. Use the web_search tool first to find URLs, then navigate to them with browser tools.

2. COOKIE/PRIVACY DIALOGS: After navigating to a page, check for cookie consent or privacy popups. Dismiss them by clicking Accept/Close/OK before interacting with other elements. Take a fresh browser_snapshot afterward.

3. HANDLING TIMEOUTS: If an action times out, the element may be blocked by an overlay. Take a new snapshot and look for dialogs to dismiss. If none, try an alternative approach or report the issue.

4. GENERAL: Use browser tools to click, fill forms, and extract information. Use terminal for local file operations. Verify your actions and handle errors gracefully." \
  2>&1 | tee "$LOG_FILE"

echo "✅ Done. Log: $LOG_FILE"

# =============================================================================
# 常用可添加的选项：
#
#   --resume                  如果中断，从检查点恢复
#   --verbose                 启用详细日志记录
#   --max_tokens=63000        设置最大响应令牌数
#   --reasoning_disabled      禁用模型思考/推理令牌
#   --providers_allowed="anthropic,google"  限制为特定提供商
#   --prefill_messages_file="configs/prefill.json"  少样本提示
# =============================================================================
