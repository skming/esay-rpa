#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Easy RPA — 全栈开发环境一键启动
#
# 启动顺序:
#   1. 后端  http://127.0.0.1:8765  (FastAPI + uvicorn --reload)
#   2. 前端  http://localhost:19174  (Vite HMR)
#   3. 等待前后端就绪后启动 Electron 桌面窗口
#
# 依赖: pnpm / concurrently / wait-on / uvicorn
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

# ── 前置检查 ─────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Easy RPA — 全栈开发环境"
echo "  后端  : http://127.0.0.1:8765"
echo "  前端  : http://localhost:19174"
echo "  模式  : Electron 桌面端"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f "$VENV_PYTHON" ]; then
  echo ""
  echo "❌  未找到 backend/.venv，请先执行:"
  echo "    cd backend && uv sync"
  exit 1
fi

if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
  echo "⚠  node_modules 不存在，正在安装 …"
  cd "$SCRIPT_DIR" && pnpm install
fi

# ── 启动 ─────────────────────────────────────────────────────────
echo ""
echo "▶  启动全栈开发环境 …"
cd "$SCRIPT_DIR"

exec pnpm concurrently \
  --kill-others \
  --prefix-colors "cyan,green,yellow" \
  --names "backend,frontend,electron" \
  "cd backend && $VENV_PYTHON -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload" \
  "pnpm dev" \
  "pnpm wait-on tcp:19174 tcp:8765 && electron ."
