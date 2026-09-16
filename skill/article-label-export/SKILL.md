---
name: article-label-export
description: "Process a completed WeChat crawl: fully inspect and decision-tree label one run, select only explicit KEEP articles, persist their summaries to results/articles.sqlite3, and safely prune only explicit DROP source directories while preserving REVIEW. Use after wechat-crawl when an agent is asked to classify, store, clean up, or return a filtered WeChat article digest."
---

# Article Label Export

This Skill starts after `$wechat-official-account-crawler` has completed a
successful crawl. Run that separate Skill first when fresh articles are needed.
Provide this Skill with the completed run directory, or use the newest run that
contains `article_details.csv`. Keep the current run boundary; do not mix old
articles into the report unless the user explicitly requests a backfill.

The crawl output is expected under:

```text
/root/workspace/wx-crawl/results/record/<timestamp>/
```

The run directory must contain `article_details.csv`. Article text and metadata
are read from the corresponding directories under:

```text
/root/workspace/wx-crawl/results/articles/
```

Do not process a failed or interrupted crawl unless the user explicitly asks to
report partial results. A successful crawl may still have zero new articles; in
that case report that no candidates were available.

## Identify the input set

The input to this Skill is exactly the article list in the selected run's
`article_details.csv`, matched to directories under `results/articles/`. It is
not all articles currently in the archive. To see the exact article directories
and titles that will be processed, run:

```bash
python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py candidates \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

To inspect whether that run has been labeled, summarized, and reported, run:

```bash
python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py status \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The command also saves the same JSON snapshot by default as
`processing_status.json` inside that run directory. Use `--output <filename>`
to choose another filename within the same run directory.

`待打标` means `label.json` is missing or invalid, `待人工复核` means its
decision is REVIEW, `已打标但未入选` means its decision is DROP, `待摘要`
means a legacy summary-less v2 KEEP has no fallback summary yet, and `已筛选`
means it is KEEP with a model-generated or migrated summary. The run-level files
`article_summaries.json` and `filtered_articles.json` show whether those stages
were written. Database import is verified separately from the JSON result of
`wx-crawl-db ingest`.

Every `select_articles.py` command and the `wx-crawl-db ingest` / `prune`
command writes progress logs to this run file:

```text
/root/workspace/wx-crawl/results/record/<timestamp>/article_label_export.log
```

Use that log to judge execution progress across candidate matching, label
filtering, summary/report writing, database import, and current-run cleanup.
Default stdout is one compact machine-readable JSON summary with counts and a
`details_file` path. Add `--verbose` only for an explicit human diagnosis; it
shows progress and detailed JSON and should not be used by normal Agent automation.

When several crawl runs are waiting for processing, inspect each run explicitly
and process them independently. For example, to inspect the three newest runs:

```bash
for run_dir in $(find /root/workspace/wx-crawl/results/record \
  -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -n 3); do
  python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py status \
    --run-dir "$run_dir"
done
```

Then process each run that has `待打标` articles, preferably oldest first. Pass
that run's directory to every `candidates`, `matches`, `write-report`,
`wx-crawl-db ingest`, and `prune` command. Each run gets its own
`article_summaries.json` and `filtered_articles.json`, and each import is
verified before its cleanup. Do not use the implicit newest-run lookup while
catching up multiple runs.

An article directory can occur in more than one run. A valid existing
`label.json` is reused for that article; the label writer does not need to
overwrite it. SQLite import is idempotent by article URL, so importing a later
run updates the existing database row rather than creating a duplicate.

## 1. Label this run's articles

Use the inspection and classification rules from
`$label-wechat-articles` at
`/root/workspace/wx-crawl/skill/label-wechat-articles/SKILL.md`.

First enumerate only the articles listed in this run's
`article_details.csv` (the same input set described above):

```bash
python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py candidates \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

Label the exact run with one API-backed Python process:

```bash
/root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The Python runner reads every article to EOF, calls the model, validates evidence,
and atomically writes the v2 decision and summary together. Do not replace it with
an Agent-side per-article loop. Do not use the all-history scope unless the user
explicitly asks for a backfill.

## 2. Execute the label decision

After all current-run labels validate, run:

```bash
python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py matches \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The selector performs no semantic reinterpretation. An article is eligible only
when `label.json.decision` is `KEEP`. `DROP` is excluded. `REVIEW` is excluded
from summaries and database import but retained for human review. Missing or
invalid labels block normal batch completion; missing title or URL also prevents
selection. The v2 schema validator independently guarantees that KEEP has a
positive application type, at least one canonical domain, detailed reasoning,
and the required evidence.

## 3. Persist the filtered report

Do not ask the Agent to summarize selected articles. The API-backed labeler already
generates a factual `summary` for every article in the same call that creates its v2
decision. The report writer reads the KEEP labels directly and materializes a
compatibility JSON object keyed by URL at:

```text
/root/workspace/wx-crawl/results/record/<timestamp>/article_summaries.json
```

Example:

```json
{
  "https://mp.weixin.qq.com/s/example": "文章介绍……，并说明申报方向、截止时间和材料要求。"
}
```

For an older summary-less v2 label only, an existing `article_summaries.json` may be
used as a migration fallback. New runs must not depend on an Agent-generated summary
file. Generate the validated machine-readable report directly:

```bash
python3 /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py write-report \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The command writes both normalized `article_summaries.json` and
`filtered_articles.json` in the run directory. It fails if a selected article has no
label/fallback summary, an invalid label, or a missing URL. This file
is the source of truth for database import and must be generated before any
cleanup.

### Audit ledger (mandatory)

The `matches` command writes schema-v2 `labeling_ledger.json` inside the run
directory **before any prune**. It records every candidate's KEEP/DROP/REVIEW
decision, decision path, terminal `reason_code`, article-specific reason,
evidence, labels, and final selection state. The ledger survives
`prune --confirm-delete`, so the audit trail for why an article was retained,
excluded, or held for review does not disappear with its source directory.

- Never backfill a `skipped` reason of `article directory not found` as a
  screening verdict; that string only means the directory was already gone when
  matching ran (usually after an earlier prune).
- Treat `filtered_articles.json` `skipped` as a summary view; treat
  `labeling_ledger.json` as the durable per-article audit record.

## 4. Import and clean up

Initialize the database when needed:

```bash
/root/workspace/wx-crawl/wx-crawl-db init
```

After `filtered_articles.json` is successfully written, import it:

```bash
/root/workspace/wx-crawl/wx-crawl-db ingest \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The importer reads and stores the full text, metadata, URL, title, labels, and
summary. It is idempotent by URL. After a successful import, remove only source
directories explicitly labeled DROP as part of the normal workflow:

```bash
/root/workspace/wx-crawl/wx-crawl-db prune \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>" \
  --confirm-delete
```

Without `--confirm-delete`, `prune` only prints what would be deleted. REVIEW is
always protected. A KEEP article missing from the filtered report is also
protected because that discrepancy indicates a pipeline error. Never delete
article directories before the import succeeds. Omit cleanup only when the user
explicitly asks to retain source directories.

## 5. Return the report

Return a flat list in publish time descending order. Each item must contain exactly these user-facing fields:

```markdown
### 文章标题
- 分类：科研项目申请；领域：无人机、大模型
- 摘要：用 1-3 句话概括文章具体讲了什么、申请/指南内容与相关技术领域。
- 链接：https://mp.weixin.qq.com/s/...
```

Keep the original title and URL from `metadata.json`. Preserve the two layers
separately: show one application category and all matched domain categories.
When no article satisfies both conditions, say so explicitly and do not include
non-matching articles merely because their title contains a keyword.

If an article's text could not be fully read, leave it out and report its path
and unreadable text file after the main results. Do not write a speculative
summary.

## Scheduling

For a nightly end-to-end refresh, schedule one orchestrator job rather than
independent label and table-sync jobs. The nightly crawl is optional: attempt
the separate `wechat-crawl` Skill first, but do not let authentication block the
rest of the pipeline. If a QR login is requested, deliver it and wait at most
five minutes. If login has not completed by then, record an authentication
timeout, stop or abandon that unfinished crawl according to the crawler's
graceful interruption rules, and continue with already completed crawl runs.
Never treat the failed/interrupted run as successful input.

After the optional crawl phase, inspect all run directories with `status` and
process pending successful runs oldest first. For every eligible run, complete
labeling, `matches`, summaries, `write-report`, database `ingest`, and confirmed
current-run `prune` before moving to the next run. Only after all successful
imports should it run:

```bash
python3 /root/workspace/wx-crawl/skill/wechat-ai-table-sync/scripts/sync_articles.py \
  --mode incremental
```

The scheduled job must use the explicit `--run-dir` for every batch and retain
the crawler's shared-task locks. A crawl/authentication timeout is a reported
warning, not a reason to skip later labeling, import, cleanup, or table sync.
Labeling, database import, and table sync failures remain terminal for that
stage and must be reported without claiming success. Include the `vision`
toolset because the crawler may need to deliver and verify a login QR code. A
single job also prevents table sync from racing an unfinished database import.

## TikHub sample scope

The opt-in `src.crawler.tikhub_sample` command also runs labeling, selection and
SQLite import for its exact sample. Its nested `results/record/samples/<timestamp>`
records are excluded from automatic pending-batch scanning; do not promote them
into scheduled DingTalk sync or cleanup without a request. `sample_result.json`
records completion and import counts. Normal label rules still apply, including
retaining image-only articles for REVIEW when decisive text is missing.

Resume a fully archived sample with `.venv/bin/python -m src.crawler.tikhub_sample
--resume results/record/samples/<timestamp>`. This bypasses notification delivery,
reuses valid labels, and retries selection/import through the normal validators.

## Strict version gate

The current contract is schema_version=2 plus the tree_version declared by the
repository decision tree (currently 1.1). A matches preview may list PENDING items,
but write-report refuses completion while any run article has a missing, invalid,
or outdated label or cannot be matched. A valid all-DROP/REVIEW run still produces
a zero-selection report. Reports and ledgers now record the expected label schema
and tree version; these are separate from the report/ledger's own schema version.
Database ingest revalidates the source label and requires current KEEP with report
fields equal to that label. Do not use an old saved report to bypass relabeling.


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
