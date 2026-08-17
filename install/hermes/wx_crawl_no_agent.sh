#!/usr/bin/env bash
set -euo pipefail

exec /root/workspace/wx-crawl/.venv/bin/python \
  /root/workspace/wx-crawl/scripts/run_no_agent_crawl.py

