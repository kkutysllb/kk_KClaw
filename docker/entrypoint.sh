#!/bin/bash
# Docker 入口点：将配置文件引导到挂载卷，然后运行 kclaw。
set -e

KCLAW_HOME="/opt/data"
INSTALL_DIR="/opt/kclaw"

# 创建必要的目录结构。缓存和平台目录（cache/images、cache/audio、
# platforms/whatsapp 等）由应用程序按需创建 — 不要在此预创建，
# 以便新安装获得来自 get_kclaw_dir() 的整合布局。
mkdir -p "$KCLAW_HOME"/{cron,sessions,logs,hooks,memories,skills}

# .env
if [ ! -f "$KCLAW_HOME/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$KCLAW_HOME/.env"
fi

# config.yaml
if [ ! -f "$KCLAW_HOME/config.yaml" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$KCLAW_HOME/config.yaml"
fi

# SOUL.md
if [ ! -f "$KCLAW_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$KCLAW_HOME/SOUL.md"
fi

# 同步捆绑的技能（基于清单，以便保留用户编辑）
if [ -d "$INSTALL_DIR/skills" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py"
fi

exec kclaw "$@"
