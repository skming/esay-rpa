#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Easy RPA — 前端启动脚本（纯浏览器开发模式）
# 前端地址: http://localhost:19174
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="19174"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Easy RPA — 前端开发服务"
echo "  地址: http://localhost:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 检查 node_modules
if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "⚠  未找到 node_modules，正在安装依赖 …"
  cd "$SCRIPT_DIR" && pnpm install
fi

cd "$SCRIPT_DIR"
echo "▶  启动 Vite 开发服务器（端口 $PORT）…"
exec pnpm dev
