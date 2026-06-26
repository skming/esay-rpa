#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Easy RPA — 后端启动脚本
# 后端地址: http://127.0.0.1:8765
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
HOST="127.0.0.1"
PORT="8765"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Easy RPA — 后端服务"
echo "  地址: http://$HOST:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 检查虚拟环境
if [ ! -f "$VENV_PYTHON" ]; then
  echo "❌  未找到 Python 虚拟环境: $VENV_PYTHON"
  echo "    请先在 backend/ 目录执行: uv sync"
  exit 1
fi

# 切换到 backend 目录运行
cd "$BACKEND_DIR"
echo "▶  启动 uvicorn（热重载）…"
exec "$VENV_PYTHON" -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  --log-level info
