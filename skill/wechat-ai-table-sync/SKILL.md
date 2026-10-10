---
name: wechat-ai-table-sync
description: Sync WeChat article records into a DingTalk AI table.
license: MIT
metadata:
  hermes:
    tags: [WeChat, DingTalk, AI Table, SQLite, Sync]
    related_skills: []
---

# WeChat AI Table Sync

Synchronize the local SQLite `articles` table into the existing DingTalk AI
multi-dimensional table. The workflow supports full and incremental modes and
uses the database article `id` as the stable upsert key. It adds missing managed fields but does not remove DingTalk fields and does not synchronize delivery `channel` state.

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

The managed table fields are:

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
deadline_at
deadline_text
attachments
```

`domains` is populated from the SQLite article-domain relation / `domains_json`
value as comma-separated text. `account_id`, `cover_url`, `crawl_run`, timestamps,
and `channel` are intentionally not synchronized because the target table has
no corresponding fields.

`deadline_at` is formatted in Asia/Shanghai time, `deadline_text` preserves source
wording, and `attachments` contains original URLs separated by newlines. The sync ensures
these managed fields exist but never deletes unrelated table fields.

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
   Completion criterion: every source row has correctly typed values for the managed fields.
2. Obtain a DingTalk access token from the configured application credentials.
   Completion criterion: token acquisition succeeds without exposing its value.
3. List all target records with pagination. Completion criterion: every remote
   record is considered, not only the first page.
4. Index remote records by their `fields.id` value. Completion criterion: duplicate
   source IDs are not created.
5. Map each database row to the managed field schema. Convert values to
   strings except native date columns (Unix milliseconds); join domain labels with commas.
6. In incremental mode, insert missing IDs and update only changed rows. In full
   mode, update every existing matching ID and insert missing IDs. The script does
   not delete remote records absent from SQLite.
7. Send writes in bounded batches. Completion criterion: the CLI prints JSON with
   `inserted`, `updated`, and `unchanged` counts.
8. Verify by listing the table again and checking that each source `id` maps to one
   remote record whose managed fields equal the mapped source fields.

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

Scheduled crawling is already owned by Hermes native no-agent script jobs; never add crawling
to an Agent cron prompt. If a nightly Hermes job is needed for downstream processing,
make it invoke the deterministic pending-batch entry point once:

```text
cronjob(action="create", schedule="0 22 * * *", name="wechat-article-label-filter-export-pipeline", skills=["wechat-pipeline-orchestration"], workdir="/root/workspace/wx-crawl", enabled_toolsets=["terminal"], prompt="Do not authenticate or crawl. Run /root/workspace/wx-crawl/.venv/bin/python /root/workspace/wx-crawl/skill/wechat-pipeline-orchestration/scripts/process_pending_batches.py exactly once, wait for completion, and report its compact JSON including processed_count, failed_batch_count, notification_failed_count, selected_count, and details_file. The Python program retries unresolved labels, isolates batch failures, and sends one direct DingTalk aggregate after labeling, selection/reporting, and storage/sync. Do not retry or duplicate stage notifications from the Agent.")
```

The Python entry point owns labeling, deterministic selection, report generation,
database import/prune, sync, and direct stage notifications. Do not repeat those stages
or notifications in the Agent.

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

## Current-version validation before sync

Before creating remote fields or mutating rows, the sync CLI verifies local source
labels against the current schema/tree and checks KEEP, application_type, domains
and summary against the SQLite selection. Missing/outdated/inconsistent evidence
blocks sync; rows are not silently omitted or deleted. A converged row count is not
a version audit. Historical records need relabeling and database reconciliation
before sync can resume. Read-only SQLite queries remain available for audit/dedup.


## Latest labeling contract (2026-09-08)

Read authoritative versions from `src.labeling.schema.current_contract()` on each run.
Currently label schema is 2, decision tree is 1.2, research profile is 1.3; these
are independent versions. Every label must include `profile_version` as well as
`schema_version` and `tree_version`. Missing or old versions require actual
relabeling; never stamp a new version onto an old decision.

Reports carry `label_schema_version`, `tree_version`, and `profile_version`, even
when empty. Import rejects old reports and revalidates the source KEEP label;
SQLite stores that full validated label as provenance. Sync and deadline reminders
verify current decisions before any outbound action. Count equality alone does
not certify rule-version consistency. Old or unavailable source decisions require
relabeling/reconciliation, not silent acceptance or deletion.

Completion and backfill coverage are reusable only when their `label_contract`
equals the current contract. Old unversioned markers do not certify completion.
Use `pipeline_state.py list-pending --json` for pending discovery; do not independently
skip a batch merely because its status says completed or its ID appears in coverage.
For pruned archives, recover source articles before reclassification; report the
migration as blocked until evidence is restored. Do not repeatedly retry missing
source data or declare a global migration complete based on a new batch.


## Geographic scope gate (2026-09-16)

Current authoritative contract: schema=2, tree=1.2, profile=1.3. Read current_contract()
instead of pinning these example versions. geography is required, with status
eligible/out_of_scope/unclear, regions (national/beijing/zhejiang/other/unknown),
and verbatim evidence. KEEP must pass G1 and have eligible geography with evidence.
The tree/profile upgrade invalidates historical labels without geographic review;
never stamp new versions or infer national scope from a national sponsor alone.

Only nationwide national programs and Beijing/Zhejiang local programs (including
subordinate cities/districts) are eligible. Other regional special programs, including
NSFC regional innovation joint fund (Jiangxi), are excluded even if national bodies
sponsor them or applicants nationwide may participate. Publisher location, event
venue, collaborator addresses and incidental region mentions are not project scope.
Mixed guides require a clearly independent eligible track with source evidence.
Unclear geographic scope means REVIEW, no selection/import/high alert/deadline alert.
Unknown deadlines still pass the separate deadline gate; do not confuse the two.

Selection reports include geography, and import compares it to the validated source.
Reminder scheduling and dispatch exclude other/unclassified geography, cancel pending
reminders, and report skipped_geography. Verified relabeling and reimport can restore
reminders cancelled solely for missing geography. No direct Agent reminder sends may
bypass these program checks. Existing historical DB/table records are not silently
removed; reconcile them against new labels before syncing. Do not claim old records
or completion/coverage markers meet the new contract. Preserve migration progress.

The manual label writer now requires --geography with a JSON object, for example
'{"status":"eligible","regions":["beijing"],"evidence":"北京市科技计划项目"}'.
Evidence must be from the actual article, not copied from this example.


## Project intake fields (2026-10-09)

Do not modify 项目入库表单 or its DingTalk automation. Enrich only 爬取公众号情况日志.
The seven additional columns are 正式项目名称、主管部门、申报金额（万元）、申报开始日期、
申报截止日期、申报要求摘要、预期成果. Start/end dates use native date columns displayed as YYYY-MM-DD; amounts use decimal RMB万元.
Existing url/attachments remain the reference-link/attachment sources.

New model outputs include optional project_intake (independent protocol version 1).
Every nonempty field needs verbatim body evidence, validated by project_intake.normalize.
Text values must be copied from that evidence; only unambiguous full dates and exact
per-project currency conversions are normalized. Unknown fields are omitted, not
zero or guessed text. No title fallback, no publisher-to-authority mapping, no
inferred year, no total-budget/cap/range-to-fixed-amount conversion. An unresolved
formal project identity means the entire extraction remains empty.

Do not fill 项目类别 or 紧急程度 until their definitions are confirmed. application_type
is not 纵向/横向; importance_level is not form urgency. Internal actor, organization,
state and recommendation fields remain human-managed. Do not map a general article
summary to 申报要求摘要 without explicit application conditions.

SQLite stores project_intake_json separately from decision labels. Existing decision
contracts are unchanged and need not be relabeled just to enrich these fields.
Sync revalidates evidence against stored body text, omits blank values, and fills
intake columns only when their remote values are empty. Even --mode full cannot
clear unknown values or replace human-entered intake values.

Optional historical enrichment (does not send alerts, change decisions or sync):
`.venv/bin/python -m src.labeling.enrich_intake --output <audit.json>` previews;
add `--article-id <id>` or `--limit <n>` to scope and `--apply` to persist with a
SQLite backup. Inspect the audit, then use the existing sync entry point. Failed
extractions remain unmodified. Do not overwrite or rebuild the target intake form.


## Native date fields (2026-10-10)

`deadline_at`, `申报开始日期`, `申报截止日期` must be created as `date` fields.
Write Unix milliseconds; interpret date-only extraction as Asia/Shanghai midnight.
Preserve deadline_at time precision (display YYYY-MM-DD HH:mm:ss). Display the two
intake dates as YYYY-MM-DD. SQLite timestamps remain seconds and extracted dates
remain ISO strings; conversion belongs only at the Notable sync boundary.

Read actual field types before writing. Existing text columns temporarily retain
formatted text until converted in the DingTalk UI; never delete/recreate columns
because automation references their IDs. The installed SDK UpdateField request has no type parameter. A raw request
returned HTTP 200 while the immediate readback still showed text; a later readback
confirmed all three dates with unchanged IDs. Never infer completion from HTTP
status alone: verify actual field metadata and preserved values.
Unknown dates remain omitted, and nonempty manual intake dates are preserved.
Compare date values and importance_factors JSON semantically for idempotent sync.
