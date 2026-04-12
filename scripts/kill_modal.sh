#!/bin/bash
# 停止所有正在运行的 Modal 应用（沙箱、部署等）
#
# 用法：
#   bash scripts/kill_modal.sh          # 停止 kclaw 沙箱
#   bash scripts/kill_modal.sh --all    # 停止所有 Modal 应用

set -uo pipefail

echo "正在获取 Modal 应用列表..."
APP_LIST=$(modal app list 2>/dev/null)

if [[ "${1:-}" == "--all" ]]; then
    echo "正在停止所有 Modal 应用..."
    echo "$APP_LIST" | grep -oE 'ap-[A-Za-z0-9]+' | sort -u | while read app_id; do
        echo "  正在停止 $app_id"
        modal app stop "$app_id" 2>/dev/null || true
    done
else
    echo "正在停止 kclaw 沙箱..."
    APPS=$(echo "$APP_LIST" | grep 'kclaw' | grep -oE 'ap-[A-Za-z0-9]+' || true)
    if [[ -z "$APPS" ]]; then
            echo "  未找到 kclaw 应用。"
    else
        echo "$APPS" | while read app_id; do
            echo "  正在停止 $app_id"
            modal app stop "$app_id" 2>/dev/null || true
        done
    fi
fi

echo ""
echo "当前 kclaw 状态:"
modal app list 2>/dev/null | grep -E 'State|kclaw' || echo "  (无)"
