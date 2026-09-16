---
name: wechat-official-account-crawler
description: Run, check, troubleshoot, or explain the local wx-crawl WeChat Official Account archive. Use when a user asks to crawl registered accounts, add article-link seeds, inspect crawl results, or manage the direct no-agent crawl schedule.
---

# WeChat Official Account Crawler

After a valid article is archived, scan article anchors for attachment links. Always
write source links to `attachments/attachments.json` and download directly accessible
files best-effort. Authentication pages, online documents, expired links, and size-limit
failures remain `link_only` and never make an otherwise valid article fail.

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

After a successful batch is recorded, the crawler immediately invokes the Python V2
labeler for that batch. The same model response produces the decision, summary,
deadline, and high/medium/low importance. High-importance KEEP articles are sent
directly to DingTalk with the configured person mentioned. Alert delivery is idempotent;
labeling or notification failures remain retryable by the downstream pipeline and do
not invalidate an otherwise successful crawl archive.

## History backend and authentication

The production default is now `crawl.history_backend: tikhub`. `./run.sh --check`
must report `crawl_backend=tikhub`. Both manual and scheduled `run.sh` calls use
TikHub WeChat MP V2 HTTP history, then wechat-mp-tools body download, we-mp-rss body
fallback, and finally paid TikHub detail if both local downloaders fail validation.
TikHub content is validated before labeling/import too. No WeRead service, account
pool, relay URL, token refresh, or login QR is required on this path. Never manually
start wechat-mp-tools authentication, probe weread.111965.xyz, or repeatedly request
QR codes to recover a TikHub run. Do not fall back to those steps on a TikHub error.

Credentials come from TIKHUB_API_KEY or ignored src/auth/config/tikhub.env; verified
account identities are cached. `crawl.tikhub_max_requests` caps all paid requests
per run (default 120). Errors do not automatically repeat paid HTTP calls. Check
`record_dir/tools-log/tikhub.json` for request counts, body providers, and account
failures. Standard output retains the existing batch schema with
`crawl_backend=tikhub`; `history_complete` describes completion of the configured
window/incremental policy, not exhaustive lifetime history. Identity/list/body
failures mark the crawl incomplete and retain successful archives for retry.

`history_backend: legacy` is an explicit manual rollback only. Its obsolete WeRead
login flow is not a recovery action for normal TikHub operation. Check current model
configuration and actual API responses before claiming DeepSeek balance is depleted;
old conversation summaries and prior cron errors are not current balance evidence.

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

## Bounded TikHub sample pipeline

For an explicitly requested TikHub trial, use:

```bash
.venv/bin/python -m src.crawler.tikhub_sample \
  --account 3 --account 4 --account 5 --per-account 2 --max-pages 2 --max-requests 12
```

Account values are registry numbers. This opt-in command fetches WeChat MP V2 lists
through direct HTTP, deduplicates against the archive and SQLite, downloads bodies
with wechat-mp-tools then we-mp-rss, validates HTML, and uses paid TikHub detail only
if both fail. It uses opaque next_offset/is_end pagination, never short-page length.
Limits bound pages per account, new candidates per account, and all paid attempts;
there is no automatic TikHub retry. Budget estimates count successful responses at
USD 0.01 each; network timeouts may still be billed and require account reconciliation.
TIKHUB_API_KEY comes from the environment or ignored src/auth/config/tikhub.env.
Verified gh_ identifiers are cached in ignored src/auth/config/tikhub-accounts.json.

The normal model runner labels one article first, then the remaining sample. On
label failure, stop before selection/import. Only validated KEEP articles are
imported through wx-crawl-db; DROP/REVIEW remain archived. No cleanup or DingTalk
send is performed. Reports live in results/record/samples/<timestamp>/, deliberately
outside the scheduler's direct-child batch scan. Pass this exact nested run path to
select_articles.py and wx-crawl-db for diagnostics. The label CLI accepts only direct
child runs; to retry labeling a sample use its explicit --article-dir scope and be
aware that the label CLI normally sends importance alerts.

Normal stdout is one compact JSON summary with status, articles, requests,
estimated_tikhub_usd, database, and details_file. Read sample_result.json on failure.
The production run.sh and scheduled calls now use the same TikHub backend and shared body fallback; the sample entrypoint remains scoped and does not send notifications.

For a deliberately chosen sample category, add `--title-contains 科研` to filter
list titles before downloading. This is sampling only and must never override the
full-text label decision. The filter and other limits are recorded in parameters.

If all bodies were archived but labeling or import failed, resume without new TikHub
calls or notifications:

```bash
.venv/bin/python -m src.crawler.tikhub_sample --resume results/record/samples/<timestamp>
```

This reuses valid labels, keeps each labeling attempt's usage files, and imports by
URL idempotently. It does not re-download articles or replace valid labels. Use this
entrypoint instead of the alerting label CLI for sample recovery.

Sample Chat Completions labeling allows up to 16384 output tokens (production labeling uses labeling.max_output_tokens, currently 16384). This bounds reasoning plus JSON output; it does not relax
schema/evidence validation. TikHub cost estimates exclude model costs.

## Account identity recovery

A single stale seed article must not determine whether an account is crawlable.
For an uncached account the TikHub client tries the seed and at most two distinct
archived article URLs. HTTP 400 or identity mismatch allows these alternatives;
401/402 and transport errors stop without extra automatic paid recovery calls.
If article identity remains unavailable, search the exact account name through
WeChat Search V2, then verify the returned gh_ candidate against the numeric biz in
its history article URLs. Never cache a candidate by name alone. At most three
exact-name candidates are checked; the verified first list page is reused for
crawling. All these requests share the configured per-run cap. Search is an identity
fallback, not the normal history source. Cached identities skip this work later.

`tools-log/tikhub.json.identity_events` records fallback URLs, mismatches and verified
identities. A known mismatched seed must not be archived under the wrong account.
Repair erroneous registry seed links only with matching biz evidence. A failed run
can still have successful archives: consult article_details.csv/account_summary.csv,
and keep its pending downstream record rather than interpreting failure as zero data.
