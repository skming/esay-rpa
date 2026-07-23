#!/usr/bin/env bash
# Easy RPA — 前端开发服务（纯浏览器模式），http://localhost:19174
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/common.sh"

PORT="19174"

banner "Easy RPA — 前端开发服务" "地址: http://localhost:$PORT"
ensure_node_modules

cd "$REPO_ROOT"
echo "▶  启动 Vite 开发服务器（端口 $PORT）…"
exec pnpm dev
