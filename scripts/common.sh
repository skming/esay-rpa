#!/usr/bin/env bash
# Easy RPA 启动脚本公共库：路径解析 + 依赖前置检查 + 横幅输出，被 start-*.sh source。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

# 打印带上下边框的标题横幅，每个参数占一行。
banner() {
  local rule
  rule="$(printf '━%.0s' {1..44})"
  printf '%s\n' "$rule"
  printf '  %s\n' "$@"
  printf '%s\n' "$rule"
}

# 后端虚拟环境缺失即退出并提示 uv sync。
require_venv() {
  if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌  未找到 Python 虚拟环境: $VENV_PYTHON" >&2
    echo "    请先在 backend/ 执行: uv sync" >&2
    exit 1
  fi
}

# node_modules 缺失则自动安装。
ensure_node_modules() {
  if [ ! -d "$REPO_ROOT/node_modules" ]; then
    echo "⚠  node_modules 不存在，正在安装 …"
    (cd "$REPO_ROOT" && pnpm install)
  fi
}
