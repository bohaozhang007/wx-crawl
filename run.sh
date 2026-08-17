#!/usr/bin/env bash
set -euo pipefail

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
unset http_proxy https_proxy all_proxy

cd "$(dirname "$0")"

# 优先使用项目固定环境，与 src/crawl.py 的自动 re-exec 保持一致。
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

"$PY" scripts/check_wechat_auth.py
exec "$PY" src/crawl.py "$@"
