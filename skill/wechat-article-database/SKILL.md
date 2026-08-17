---
name: wechat-article-database
description: "Manage the SQLite database of filtered WeChat Official Account articles under /root/workspace/wx-crawl/results/articles.sqlite3. Use when an agent needs to initialize the database, import a validated filtered_articles.json report, query articles for DingTalk or other consumers, track delivery status, inspect statistics, or safely remove source article directories after verified import. Always use the repository's wx-crawl-db CLI; do not write ad-hoc SQL or delete article files directly."
---

# WeChat Article Database

Use the repository CLI at `/root/workspace/wx-crawl/wx-crawl-db`. The database
stores only articles with a validated v2 label whose decision is `KEEP`. The
v2 label contract itself requires a qualifying application type, a non-empty
task-scope domain list, a terminal reason code, reasoning, and evidence. It keeps the
title, URL, account, publish time, labels, model-generated summary, and full text. The
database is the durable store; `results/articles/` is the crawl staging area.

## Safety Rules

- Do not use `sqlite3` directly or edit `results/articles.sqlite3` by hand.
- Do not import a raw crawl, a `matches` preview, or an unvalidated JSON file.
  Import only `filtered_articles.json` produced by the report selector's
  `write-report` command.
- Complete and verify the database import before any cleanup.
- Treat `prune --confirm-delete` and `prune --all-unselected --confirm-delete`
  as destructive operations. Run them only for the requested scope.
- Current-run cleanup removes only explicit DROP candidates listed in that
  run's `article_details.csv`; it preserves REVIEW and inconsistent unreported KEEP.
- Full-history cleanup requires valid v2 labels, protects KEEP and REVIEW, and
  considers only explicit DROP directories. DROP directories with an existing
  SQLite row or without a valid WeChat URL are retained/reported rather than deleted.
- Keep compact CLI JSON summaries available for callers; do not wrap them in prose.
  Detailed cleanup results are written to the returned `details_file`. Query
  commands return full records only with explicit `--json` or `--verbose`.

## Initialize

Initialize the default database when it does not exist:

```bash
/root/workspace/wx-crawl/wx-crawl-db init
```

The default path is `/root/workspace/wx-crawl/results/articles.sqlite3`. Use
`--db <path>` before the subcommand for an isolated database or test database.

## Import a Filtered Run

Require a completed crawl and a valid run directory containing
`filtered_articles.json`. If the report has not been generated, first use the
`article-label-export` skill to run `write-report`; it reads summaries already generated
inside v2 labels and materializes `article_summaries.json`. Do not ask the Agent to
summarize again or bypass label validation.

Import idempotently by URL:

```bash
/root/workspace/wx-crawl/wx-crawl-db ingest \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The command writes the full text and metadata into a transaction. Re-importing
the same URL updates the existing row instead of creating a duplicate. Check
the JSON result for `articles`, `inserted`, and `updated` before cleanup.

To preview cleanup as part of the same operation, add `--prune`; this does not
delete anything without `--confirm-delete`:

```bash
/root/workspace/wx-crawl/wx-crawl-db ingest \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>" \
  --prune
```

## Query for Consumers

All query commands emit JSON. Use `list` for filters needed by a webhook or
another Agent:

```bash
/root/workspace/wx-crawl/wx-crawl-db list --json
/root/workspace/wx-crawl/wx-crawl-db list \
  --domain 具身智能 \
  --application-type 科研项目申请 \
  --since 1785480000 \
  --limit 20 \
  --json
```

The returned records include `id`, `title`, `url`, `account_name`,
`publish_time`, `application_type`, `domains`, `summary`, and `content_text`.
Use `--since` and `--until` as Unix timestamps. Use `--limit` to bound a
response; keep it bounded for DingTalk messages.

## DingTalk Delivery State

The importer creates a `pending` delivery record for the `dingtalk` channel.
Fetch unsent articles:

```bash
/root/workspace/wx-crawl/wx-crawl-db pending-delivery \
  --channel dingtalk \
  --limit 20 \
  --json
```

Send the selected records through the caller's DingTalk integration. Only after
the webhook succeeds, mark the corresponding database IDs as sent:

```bash
/root/workspace/wx-crawl/wx-crawl-db mark-delivered \
  --channel dingtalk \
  --id 123 \
  --response '{"errcode":0}'
```

Repeat `--id` for multiple articles. Do not mark an article delivered before a
successful external send. The CLI does not contact DingTalk itself.

## Cleanup

Preview explicit DROP article directories from one crawl run:

```bash
/root/workspace/wx-crawl/wx-crawl-db prune \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

After verifying that the preceding import succeeded, delete only those
current-run DROP directories. REVIEW and KEEP remain protected:

```bash
/root/workspace/wx-crawl/wx-crawl-db prune \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>" \
  --confirm-delete
```

To clean all historical source directories that are not represented in the
database, preview first and then explicitly confirm:

```bash
/root/workspace/wx-crawl/wx-crawl-db prune --all-unselected
/root/workspace/wx-crawl/wx-crawl-db prune --all-unselected --confirm-delete
```

Never run the all-history command against an empty or unverified database. The
CLI refuses confirmed all-history cleanup when the database has no articles.

## Statistics and Verification

Inspect stored totals and domain counts:

```bash
/root/workspace/wx-crawl/wx-crawl-db stats
```

For a normal run, report the import result, selected article count, cleanup
scope, and any skipped directories. If import fails, stop and leave source
directories untouched.
