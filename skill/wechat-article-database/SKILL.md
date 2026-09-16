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

## Current label enforcement

Ingest requires a source label matching the current schema and decision-tree
version, decision KEEP, and matching application_type/domains/summary/deadline/
importance. Old report files cannot bypass this gate. Existing rows are historical
state, not proof of current rule validity. Keep them for audit pending relabeling
and database reconciliation; do not declare a global version migration complete
based solely on local/remote row counts. This gate does not rewrite existing rows.


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


## Deadline eligibility gate (2026-09-08)

Before selecting an opportunity or sending a high-importance alert, evaluate its
structured deadline against the current clock using src.labeling.eligibility.deadline_expired.
A confirmed positive timestamp at or before now excludes the article, regardless
of KEEP/high or project significance. Future, missing and ambiguous deadlines
pass this gate and still require the normal decision/importance checks. Do not
infer expiration from an ambiguous date or the model's cached urgency factor.
The selector records selection=expired; alerts report skipped_expired. Import
rechecks expiration to reject reports that became stale after selection. Retain
source labels and archives; do not rewrite the model decision merely due to time.
This is a dynamic downstream filter, not a new label schema/tree/profile version.


## Deadline eligibility gate (2026-09-08)

Before selecting an opportunity or sending a high-importance alert, evaluate its
structured deadline against the current clock using src.labeling.eligibility.deadline_expired.
A confirmed positive timestamp at or before now excludes the article, regardless
of KEEP/high or project significance. Future, missing and ambiguous deadlines
pass this gate and still require the normal decision/importance checks. Do not
infer expiration from an ambiguous date or the model's cached urgency factor.
The selector records selection=expired; alerts report skipped_expired. Import
rechecks expiration to reject reports that became stale after selection. Retain
source labels and archives; do not rewrite the model decision merely due to time.
This is a dynamic downstream filter, not a new label schema/tree/profile version.


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
