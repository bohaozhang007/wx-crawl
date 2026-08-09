#!/usr/bin/env bash

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
unset http_proxy https_proxy all_proxy

cd "$(dirname "$0")"
exec python3 src/crawl.py "$@"
