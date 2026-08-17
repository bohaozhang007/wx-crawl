---
name: wechat-ai-table-sync
description: Sync WeChat article records into a DingTalk AI table.
version: 0.1.0
author: 杨东霖, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [WeChat, DingTalk, AI Table, SQLite, Sync]
    related_skills: []
---

# WeChat AI Table Sync

Synchronize the local SQLite `articles` table into the existing DingTalk AI
multi-dimensional table. The workflow supports full and incremental modes and
uses the database article `id` as the stable upsert key. It does not add or
remove DingTalk fields and does not synchronize delivery `channel` state.

## When to Use

- The user asks to sync, refresh, or update WeChat article data in the DingTalk AI table.
- A daily scheduled job should mirror the local database into DingTalk.
- The user asks for a full rebuild or an incremental update of the target table.

## Target

- Database: `/root/workspace/wx-crawl/results/articles.sqlite3`
- Script: `scripts/sync_articles.py`
- Base/node ID: `P0MALyR8kNpXlRO7FYXjkO4bJ3bzYmDO`
- Sheet ID: `0md26ggk3sgnjzj22zp3e`
- Sheet name: `爬取公众号情况日志`
- DingTalk operator ID: the configured operator unionId; never substitute a Hermes user ID.

The existing table fields are exactly:

```text
id
content_text
publish_time
account_name
application_type
summary
url
domains
title
```

`domains` is populated from the SQLite article-domain relation / `domains_json`
value as comma-separated text. `account_id`, `cover_url`, `crawl_run`, timestamps,
and `channel` are intentionally not synchronized because the target table has
no corresponding fields.

## Prerequisites

1. The SQLite database has been initialized with `wx-crawl-db init`.
2. The database contains validated article rows. Empty databases sync zero rows.
3. The DingTalk app has access to the target table and the required Notable read/write scopes.
4. `$HERMES_HOME/.env` contains `DINGTALK_CLIENT_ID` and `DINGTALK_CLIENT_SECRET`.
5. The installed Alibaba Cloud DingTalk SDK includes `notable_1_0`.

Never print or store access tokens, client secrets, or full credential files.

## Commands

From `/root/workspace/wx-crawl`:

```text
terminal(command="python3 skill/wechat-ai-table-sync/scripts/sync_articles.py --mode incremental", timeout=300)
terminal(command="python3 skill/wechat-ai-table-sync/scripts/sync_articles.py --mode full", timeout=300)
```

Use `--db`, `--base-id`, `--sheet-id`, and `--operator-id` to override defaults
for a controlled test or another authorized table.

## Procedure

1. Load all rows from SQLite `articles`, ordered by `id`; parse `domains_json`.
   Completion criterion: every source row has a string value for the nine target fields.
2. Obtain a DingTalk access token from the configured application credentials.
   Completion criterion: token acquisition succeeds without exposing its value.
3. List all target records with pagination. Completion criterion: every remote
   record is considered, not only the first page.
4. Index remote records by their `fields.id` value. Completion criterion: duplicate
   source IDs are not created.
5. Map each database row to the fixed nine-field schema. Convert values to
   strings; join domain labels with commas.
6. In incremental mode, insert missing IDs and update only changed rows. In full
   mode, update every existing matching ID and insert missing IDs. The script does
   not delete remote records absent from SQLite.
7. Send writes in bounded batches. Completion criterion: the CLI prints JSON with
   `inserted`, `updated`, and `unchanged` counts.
8. Verify by listing the table again and checking that each source `id` maps to one
   remote record whose nine fields equal the mapped source fields.

## Safety and Idempotency

- `id` is the only synchronization key; it is the SQLite `articles.id`, not `article_id` from `deliveries`.
- `deliveries(article_id, channel)` is not copied into the AI table.
- Incremental sync never deletes remote rows. This prevents accidental loss of
  manually added table records; use a separate, explicitly requested cleanup workflow.
- Re-running the same sync is idempotent.
- Keep request batches bounded and retry only safe, failed API calls.
- If a write returns an authorization or validation error, stop and report it;
  do not claim synchronization completed.

## Scheduling

Create a durable Hermes cron job only after a manual crawl, label/export, import,
and sync have each succeeded:

```text
cronjob(action="create", schedule="0 22 * * *", name="wechat-article-label-filter-export-pipeline", skills=["wechat-official-account-crawler", "label-wechat-articles", "article-label-export", "wechat-article-database", "wechat-ai-table-sync"], workdir="/root/workspace/wx-crawl", enabled_toolsets=["terminal", "file", "vision"], prompt="Run the nightly WeChat pipeline. The crawl is optional: attempt the existing-registry incremental crawl through wechat-crawl. If a QR login is requested, deliver it and wait at most 5 minutes; if login is still incomplete, record an authentication-timeout warning, gracefully stop or abandon the unfinished crawl according to wechat-crawl rules, and continue. Do not treat that failed/interrupted run as successful input. Resolve completed run directories under results/record that contain article_details.csv; inspect each with python3 skill/article-label-export/scripts/select_articles.py status --run-dir <run-dir>, then process every pending successful run oldest first using article-label-export. For each run, enumerate only its candidates, read all textual content to EOF without opening media, write valid label.json files using label-wechat-articles, generate article_summaries.json and filtered_articles.json, run wx-crawl-db ingest --run-dir <run-dir>, verify the import JSON, and only then run wx-crawl-db prune --run-dir <run-dir> --confirm-delete. Do not use implicit newest-run selection while catching up multiple runs. After all successful imports, run python3 skill/wechat-ai-table-sync/scripts/sync_articles.py --mode incremental and verify its JSON counts and readback. Crawl/authentication timeout is a warning; labeling, import, and sync failures must be reported as errors. Deliver verified JSON counts, processed run IDs, skipped/unreadable paths, authentication warnings, and errors.")
```

A scheduled job must not claim success when the crawl is blocked by login or the
sync API fails. The job should run the crawler's existing shared-task locks and
never start a duplicate crawl.

## Verification

Run the unit tests before changing the API integration:

```text
terminal(command="python3 -m unittest skill/wechat-ai-table-sync/scripts/test_sync.py -v", workdir="/root/workspace/wx-crawl", timeout=60)
```

Also run:

```text
terminal(command="python3 -m py_compile skill/wechat-ai-table-sync/scripts/sync_articles.py && git diff --check", workdir="/root/workspace/wx-crawl", timeout=60)
```

A successful result includes real JSON counts and a post-write readback, not
merely a successful Python process exit.
