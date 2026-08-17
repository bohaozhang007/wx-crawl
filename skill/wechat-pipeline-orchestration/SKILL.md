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
   Require exit code zero and JSON `failed=0`. Existing v2 labels with summaries are
   skipped; summary-less v2 labels are upgraded in the same model call. Delete v1
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

- crawl: run `./run.sh` once; authentication and QR handling are internal; do not label automatically;
- label: require a specific `run_dir`, run the Python labeler, then report its counts;
- select: require zero pending labels, run `matches`, and report the ledger path;
- report/import: require validated matches and complete summaries before writing/importing;
- sync: read the existing SQLite database and synchronize without crawling or relabeling.

Do not expand a partial-stage request into destructive cleanup or unrelated external sync.

## Failure and completion rules

- Authentication or crawl failure blocks only the new crawl batch; a scheduled catch-up
  job may still process previously completed crawl batches.
- Label, selector, report, or database failure leaves that batch pending and preserves
  files; it must not prevent later independent batches from running.
- Use URL/idempotency keys for retries; do not infer completion from an Agent narrative.
- A successful report includes run IDs, labeling counts, pending/review/selected counts,
  ledger path and count, database results, cleanup results, and sync/readback results.
