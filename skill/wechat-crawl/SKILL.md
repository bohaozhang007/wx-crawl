---
name: wechat-official-account-crawler
description: Run, check, troubleshoot, or explain the local wx-crawl WeChat Official Account archive. Use when a user asks to crawl registered accounts, add article-link seeds, inspect crawl results, or manage the direct no-agent crawl schedule.
---

# WeChat Official Account Crawler

Operate `/root/workspace/wx-crawl`. The crawler reads `config.yaml`, updates
`account_sources.csv`, crawls every registered account, and writes the archive under
`results/`.

## Commands

Check local configuration without network crawling:

```bash
cd /root/workspace/wx-crawl && ./run.sh --check
```

Run one crawl:

```bash
cd /root/workspace/wx-crawl && ./run.sh
```

Run one crawl and send the final compact result to the configured DingTalk group:

```bash
cd /root/workspace/wx-crawl && ./run.sh --notify
```

Normal stdout is one compact JSON object. Use `--verbose` only for a requested human
diagnosis. Launch the command once; do not reproduce its authentication state machine,
poll its subprocess from the Agent, or re-check output files after `status=ok`.

## Authentication

Authentication is program-owned. The crawler starts `wechat-mp-tools`, reuses saved
credentials when valid, and otherwise sends one QR image to the configured DingTalk
group, waits up to five minutes for a scan, then continues automatically. Because the
upstream status endpoint can temporarily accept a stale token, the crawler also treats
an authentication error from a real account/history request as expired credentials:
it forces a fresh QR login and retries that operation once. It sends login and terminal
status notifications and closes any service it started. Never ask for cookies or tokens
and never send a second QR from the Agent.

## Scheduling

Recurring crawls use Hermes native script-only no-agent jobs:

```bash
hermes cron list
```

The 12:05 and 20:00 jobs run `~/.hermes/scripts/wx_crawl_no_agent.sh` with
`no_agent=true`: the scheduler executes the script and delivers stdout without an
LLM call. Do not create an additional Agent-mode crawl schedule. The crawler's file
lock rejects overlapping manual and scheduled starts.

## Inputs and results

- Put one full `https://mp.weixin.qq.com/s/...` link per CSV cell.
- Select the CSV and crawl mode in `config.yaml`.
- `account_sources.csv` is the durable account registry; every crawl processes it.
- `results/articles/` contains the deduplicated archive.
- `results/summary.csv` contains archive-wide account totals.
- `results/record/<run_id>/account_summary.csv` and `article_details.csv` describe one
  run.
- `results/record/<run_id>/tools-log/` contains diagnostics.

If compact JSON reports failure, inspect its `record_dir` or `details_file`, then read
that run's `tools-log/crawler.log`. Do not infer success from directories alone.
