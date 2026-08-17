#!/usr/bin/env python3
"""Run one crawl for Hermes no-agent cron and print a ready-to-deliver summary."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run.sh"


def last_json_object(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def delivery_message(payload: dict[str, object], returncode: int) -> str:
    status = str(payload.get("status") or "failed")
    if payload.get("command") == "check" and status == "ok":
        return (
            "✅ 微信公众号 no-agent 爬取预检通过"
            f"\n输入链接：{int(payload.get('seed_url_count') or 0)} 个"
            f"\n登记公众号：{int(payload.get('registered_account_count') or 0)} 个"
            f"\n模式：{payload.get('mode') or '-'}"
        )
    if returncode == 0 and status == "ok":
        duration = float(payload.get("duration_seconds") or 0) / 60
        return (
            "✅ 微信公众号 no-agent 爬取完成"
            f"\n运行：{payload.get('run_id') or '-'}"
            f"\n公众号：{int(payload.get('account_count') or 0)} 个"
            f"\n新增文章：{int(payload.get('new_article_count') or 0)} 篇"
            f"\n耗时：{duration:.1f} 分钟"
            f"\n记录：{payload.get('record_dir') or '-'}"
        )
    title = "⏹️ 微信公众号 no-agent 爬取已中断" if status == "interrupted" else "❌ 微信公众号 no-agent 爬取失败"
    error = str(payload.get("error") or f"进程退出码 {returncode}").replace("\n", " ")[:500]
    return f"{title}\n运行：{payload.get('run_id') or '-'}\n原因：{error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    command = [str(RUNNER), "--scheduled"]
    if args.check:
        command.append("--check")
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    payload = last_json_object(result.stdout)
    if payload is None:
        tail = " ".join(result.stderr.splitlines()[-5:])[:500]
        payload = {"status": "failed", "error": tail or "爬虫未返回 JSON 结果"}
    print(delivery_message(payload, result.returncode), flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
