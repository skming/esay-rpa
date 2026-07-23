#!/usr/bin/env bash
# Easy RPA — 全栈开发环境一键启动：后端(8765) + 前端(19174) + Electron 桌面窗口。
# 依赖: pnpm / concurrently / wait-on / uvicorn
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/common.sh"

banner "Easy RPA — 全栈开发环境" \
  "后端  : http://127.0.0.1:8765" \
  "前端  : http://localhost:19174" \
  "模式  : Electron 桌面端"
require_venv
ensure_node_modules

echo ""
echo "▶  启动全栈开发环境 …"
cd "$REPO_ROOT"
exec pnpm concurrently \
  --kill-others \
  --prefix-colors "cyan,green,yellow" \
  --names "backend,frontend,electron" \
  "cd backend && $VENV_PYTHON -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload" \
  "pnpm dev" \
  "pnpm wait-on tcp:19174 tcp:8765 && electron ."
