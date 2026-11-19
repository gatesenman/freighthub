#!/usr/bin/env bash
# 货代询价助手 - 网页/服务器模式（Linux / macOS）。
# 用法：./run-server.sh [端口]        环境变量：FREIGHT_HUB_HOST / FREIGHT_HUB_PORT / FREIGHT_HUB_DATA /
#        FREIGHT_HUB_CERT + FREIGHT_HUB_KEY（HTTPS） / FREIGHT_HUB_TRUST_PROXY=1（在 Nginx 后面） / FREIGHT_HUB_ADMIN_PASSWORD
set -euo pipefail
cd "$(dirname "$0")"
export FREIGHT_HUB_MODE=server
export FREIGHT_HUB_HOST="${FREIGHT_HUB_HOST:-0.0.0.0}"
export FREIGHT_HUB_PORT="${1:-${FREIGHT_HUB_PORT:-8765}}"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python run.py --server
