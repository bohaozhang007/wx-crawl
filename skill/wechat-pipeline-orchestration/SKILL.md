---
name: wechat-pipeline-orchestration
description: Orchestrate or resume the WeChat article pipeline, including authentication, crawl, API-backed Python batch labeling, deterministic Python selection, SQLite import, cleanup, and DingTalk AI-table sync. Use for either the complete pipeline or an explicitly requested individual stage.
---

# WeChat Pipeline Orchestration

Run stages through their deterministic program entry points. Scheduled crawling is a
native no-agent script task; Hermes may start an explicitly requested crawl with one command,
but must not reproduce its authentication, waiting, or verification state machine.

## Stage entry points

Use the project virtual environment and absolute paths:

```text
authentication/crawl  ./run.sh [--notify]
label                 python -m src.labeling.cli --run-dir <run_dir>
select                select_articles.py matches --run-dir <run_dir>
report                select_articles.py write-report --run-dir <run_dir>
database              wx-crawl-db ingest/prune
AI-table sync          sync_articles.py --mode incremental
```

`<run_dir>` must be a direct child of `results/record/` containing
`article_details.csv`. Never run the unscoped label command from a batch pipeline.
Normal automation must not add `--verbose`: each command prints one compact JSON
summary to stdout and writes per-article details into the reported files. Add
`--verbose` only for an explicit human diagnosis; do not feed verbose output back
into the Agent when a summary and `details_file` are sufficient.

## Complete workflow

1. For an explicitly requested fresh crawl, invoke the single blocking entry point once:

   ```bash
   cd /root/workspace/wx-crawl && ./run.sh
   ```

   The program owns service startup, credential checks, DingTalk QR delivery, scan
   waiting, crawl execution, records, and cleanup. Do not run a separate authentication
   preflight, poll a background subprocess from the Agent, send another QR, or re-check
   files after exit code zero and JSON `status=ok`.

   Scheduled full-pipeline processing should not start another crawl: the 20:00
   Hermes no-agent crawl job produces the input batch independently.

2. Enumerate every valid incomplete batch oldest first. Skip batches covered by
   `pipeline_coverage.json` or completed by a valid `pipeline_state.json`. The
   deterministic pending-batch script first waits on `results/record/.crawler.lock`
   for an active crawl to finish and settle its final CSV files; do not add Agent-side
   polling or enumerate batches before that wait.

3. Label one batch with a single Python process:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli \
     --run-dir /root/workspace/wx-crawl/results/record/<timestamp>
   ```

   By default, the command inherits the active Hermes provider, model, base URL,
   and provider key; do not request a duplicate labeling key when Hermes already
   has one. Run the same command with `--check` first to report the resolved source.
   Require exit code zero and JSON `failed=0`. Existing v2 labels with summaries,
   deadlines, and importance are skipped; incomplete v2 labels are upgraded in the
   same model call. High-importance notifications are idempotent, so a downstream retry
   cannot resend an already delivered alert. Delete v1
   labels rather than attempting to promote them.
   The pending-batch program makes one additional process-level labeling attempt when
   any articles fail; the second invocation skips valid labels and calls the model only
   for unresolved articles. If failures remain, leave that batch pending, continue all
   later batches, and retry the pending batch on the next pipeline run.
   Never replace this command with the old per-article `inventory`/`write` Agent loop.
   Never proceed while selector `status` reports `pending_label_count > 0`.
   Read `labeling_result.json` only when the compact counts indicate a failure or
   the user requests article-level details.

4. Perform deterministic selection in Python:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py \
     matches --run-dir /root/workspace/wx-crawl/results/record/<timestamp>
   ```

   Selection maps valid `KEEP` labels to selected articles without new semantic
   judgment. Verify `labeling_ledger.json` exists and its entry count equals the
   candidate count. Preserve DROP and REVIEW reasons from `label.json`.

5. Call `write-report` directly. The label API already generated `summary` in the same
   response as every v2 decision; the report writer selects KEEP summaries and writes
   `article_summaries.json` for compatibility. Never make the Agent read articles and
   summarize them again. Verify `filtered_articles.json` count equals the matches count.

6. Import and clean up only after a verified report:

   ```bash
   /root/workspace/wx-crawl/wx-crawl-db ingest --run-dir <run_dir>
   /root/workspace/wx-crawl/wx-crawl-db prune --run-dir <run_dir> --confirm-delete
   ```

   Never prune before successful labeling and import. Preserve source data on failure.

7. Synchronize once after all successful imports and verify the returned counts:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/skill/wechat-ai-table-sync/scripts/sync_articles.py \
     --mode incremental
   ```

8. Write `pipeline_state.json` as completed only after every required stage and
   AI-table readback succeeds.

9. Reconcile confirmed deadlines and adjust the single persistent
   `wx-crawl-deadline-reminder.timer` to the earliest pending reminder. It invokes
   `python -m src.reminders.cli dispatch` directly without an Agent, sends one grouped
   DingTalk message, records idempotent delivery state, retries bounded failures, and
   schedules the next exact reminder instead of polling daily.
   The configured lead time is 14 days.

The scheduled pending-batch program sends exactly one aggregate DingTalk notification
after each high-level downstream stage finishes: labeling, selection/reporting, and
database/AI-table synchronization. It never sends article-, batch-, or retry-level
progress messages. These notifications are direct Python webhook calls and do not enter
the Hermes Agent context. A notification delivery failure is recorded in the execution
details but does not fail or retry the data pipeline. The Agent must not duplicate these
stage notifications; it reports only the final compact execution JSON.

## Individual stages

When the user requests only one stage, run only that stage and its required read-only
precondition checks:

- crawl: run `./run.sh` once; TikHub V2 history requires no WeRead login or QR; the crawler
  immediately labels its newly recorded batch and sends high-importance alerts itself;
- label: require a specific `run_dir`, run the Python labeler, then report its counts;
- select: require zero pending labels, run `matches`, and report the ledger path;
- report/import: require validated matches and complete summaries before writing/importing;
- sync: read the existing SQLite database and synchronize without crawling or relabeling.

Do not expand a partial-stage request into destructive cleanup or unrelated external sync.

## Failure and completion rules

- Authentication or crawl failure blocks only the new crawl batch; a scheduled catch-up
  job may still process previously completed crawl batches.
- Current crawl runs default to TikHub V2 HTTP history (`crawl_backend=tikhub`).
  wechat-mp-tools and we-mp-rss only download bodies; paid TikHub detail is the last
  body fallback. Do not start old WeRead services or ask for QR login. Successful
  `status=ok, history_complete=true` means the configured history scope completed.
  Historical successful `crawl_backend=wechrss` batches remain eligible for processing.
  A failed current crawl must not be reported as a completed full batch.
- Label, selector, report, or database failure leaves that batch pending and preserves
  files; it must not prevent later independent batches from running.
- Use URL/idempotency keys for retries; do not infer completion from an Agent narrative.
- A successful report includes run IDs, labeling counts, pending/review/selected counts,
  ledger path and count, database results, cleanup results, and sync/readback results.

For TikHub identity failures, the crawler owns bounded alternate-article/search
recovery and validates numeric biz before caching. Do not repeatedly retry the same
HTTP 400 seed, infer account identity from its name alone, or revert to WeRead login.
Crawl failure may coexist with archived articles; retain the original per-run CSV
and pending stage state when a separate recovery crawl repairs only failed accounts.

Every stage must use the current label schema (2) and repository decision-tree
version (currently 1.1). A historical tree_version=1.0 is not schema v1, but is still
invalid for current selection. Old tags remain pending until full-text relabeling;
never just update the version field. A zero-selection completed batch requires zero
pending labels. Existing database/AI-table count convergence does not establish
current-version compliance; sync is blocked if source labels cannot verify it.


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

### Pruned-dir batches are permanently wedged at write-report (observed 2026-09-10)

`select_articles.py` records every `article directory not found` row as
`selection: pending` (~L304-322), so `write-report` raises
`cannot write completed report: N articles have missing/invalid/...` for any legacy
batch whose dirs an earlier `prune --confirm-delete` removed. Observed: 40 batches /
4779 ledger entries / 4243 pending rows; only 101 rows had a cross-account surviving
copy and ~34 unique URLs were recoverable from ledgers — the rest have **no URL
anywhere** (`article_details.csv` stores only 公众号名称/标题/发布时间, and
`processing_status_final.json`'s `unmatched` list is URL-less too).
`process_pending_batches.py` reproduces the identical failure on every retry
(label finds 0 candidates, select fails, three aggregate stage notifications re-send),
so retrying is not a fix. Resolution requires either (a) a recovery crawl that
re-archives the articles (window mode reaches only the newest `articles_per_account`;
incremental `incremental_max_days: 1` never reaches older posts), or (b) explicit user
approval to treat those batches as superseded. Never weaken the gate and never report
those batches complete; instead confirm the knowledge base is intact (all archive dirs
carry the current contract; KEEP-dir URL set == SQLite URL set).


### Tree 1.2 geography gate: unsatisfiable KEEP path (fixed 2026-09-17)

`schema.py` requires the literal step `G1:PASS` in `decision_path` for KEEP, but the
decision tree and `prompt.py` never told the model to emit `节点:PASS` markers — the
model emits bare nodes (`["E1","O1","G1","O2",…]`), so every eligible article failed
with `KEEP requires passing the G1 geography gate`: zero KEEPs under tree 1.2, old
KEEP archive rows stuck pending, DB/sync blocked. Fixed by adding execution rule 7 to
`skill/label-wechat-articles/references/decision-tree.md` (the file the loader reads)
requiring `节点:PASS` for passed nodes, the terminal node last, and KEEP paths to
contain `G1:PASS` and end at `K1`. No version bump (semantics unchanged). A correct
KEEP path is
`["E1:PASS","O1:PASS","G1:PASS","O2:PASS","R1:PASS","A1:PASS","T1:PASS","D1:PASS","K1"]`.
Provider content moderation (`Content Exists Risk`, DeepSeek HTTP 400) is not
retryable on the same provider — relabel those articles with the configured fallback
provider via `LABEL_PROVIDER/LABEL_MODEL/LABEL_BASE_URL/LABEL_API_KEY`.

### DB reconcile under a new contract (geography runbook, executed 2026-09-17)

After a contract bump that also changes selection rules, `require_current_selection`
blocks sync for every DB row whose stored fields/provenance no longer match its
source label. Reconcile with `scripts/reconcile_db_to_labels.py`: refresh each row's
`application_type/domains/summary/deadline_*/importance_*` and `label_json` from the
current-contract KEEP label, delete rows whose source label is now DROP/REVIEW
(new geography rule: unclear scope = REVIEW and never imported), then
`sync_articles.py --mode incremental` and read back with the URL/id-set diff plus
field-level comparison. Note `wx-crawl-db ingest` refuses reports containing expired
deadlines (`application deadline has passed; regenerate the selection report`), so
only non-expired KEEPs can be (re)imported; expired rows are reconciled in place.

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


## Project intake fields (2026-10-09)

Do not modify 项目入库表单 or its DingTalk automation. Enrich only 爬取公众号情况日志.
The seven additional text columns are 正式项目名称、主管部门、申报金额（万元）、申报开始日期、
申报截止日期、申报要求摘要、预期成果. Dates use YYYY-MM-DD; amounts use decimal RMB万元.
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
