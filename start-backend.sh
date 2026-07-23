#!/usr/bin/env bash
# Easy RPA — 后端服务（uvicorn 热重载），http://127.0.0.1:8765
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/common.sh"

HOST="127.0.0.1"
PORT="8765"

banner "Easy RPA — 后端服务" "地址: http://$HOST:$PORT"
require_venv

cd "$BACKEND_DIR"
echo "▶  启动 uvicorn（热重载）…"
exec "$VENV_PYTHON" -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  --log-level info
