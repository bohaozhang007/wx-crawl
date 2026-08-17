---
name: wechat-pipeline-orchestration
description: Orchestrate or resume the WeChat article pipeline, including authentication, crawl, API-backed Python batch labeling, deterministic Python selection, SQLite import, cleanup, and DingTalk AI-table sync. Use for either the complete pipeline or an explicitly requested individual stage.
---

# WeChat Pipeline Orchestration

Run stages through their deterministic program entry points. Hermes coordinates commands,
checks machine-readable results, handles authentication interaction, and reports outcomes;
it must not label or semantically re-filter articles one at a time.

## Stage entry points

Use the project virtual environment and absolute paths:

```text
authentication/crawl  scripts/check_wechat_auth_notify.py; src/crawl.py --verbose
label                 python -m src.labeling.cli --run-dir <run_dir>
select                select_articles.py matches --run-dir <run_dir>
report                select_articles.py write-report --run-dir <run_dir> --summaries <json>
database              wx-crawl-db ingest/prune
AI-table sync          sync_articles.py --mode incremental
```

`<run_dir>` must be a direct child of `results/record/` containing
`article_details.csv`. Never run the unscoped label command from a batch pipeline.

## Complete workflow

1. Execute authentication preflight with the project interpreter:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/scripts/check_wechat_auth_notify.py
   ```

   Start the crawler only when the command exits zero and its final JSON reports
   `logged_in=true`, `active=true`, and `status=ready` or `ready_after_login`.
   The wrapper owns QR delivery and status notifications; do not send a duplicate QR.

2. Run the crawler under its shared lock:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/src/crawl.py --verbose
   ```

   Only a successful run containing `article_details.csv` may enter downstream stages.

3. Enumerate every valid incomplete batch oldest first. Skip batches covered by
   `pipeline_coverage.json` or completed by a valid `pipeline_state.json`.

4. Label one batch with a single Python process:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli \
     --run-dir /root/workspace/wx-crawl/results/record/<timestamp>
   ```

   By default, the command inherits the active Hermes provider, model, base URL,
   and provider key; do not request a duplicate labeling key when Hermes already
   has one. Run the same command with `--check` first to report the resolved source.
   Require exit code zero and JSON `failed=0`. Existing valid v2 labels are skipped.
   Never replace this command with the old per-article `inventory`/`write` Agent loop.
   Never proceed while selector `status` reports `pending_label_count > 0`.

5. Perform deterministic selection in Python:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/skill/article-label-export/scripts/select_articles.py \
     matches --run-dir /root/workspace/wx-crawl/results/record/<timestamp>
   ```

   Selection maps valid `KEEP` labels to selected articles without new semantic
   judgment. Verify `labeling_ledger.json` exists and its entry count equals the
   candidate count. Preserve DROP and REVIEW reasons from `label.json`.

6. Generate summaries only for selected articles, save `article_summaries.json`, then
   call `write-report`. Summary generation is not selection and must not change the
   label decision. Verify `filtered_articles.json` count equals the matches count.

7. Import and clean up only after a verified report:

   ```bash
   /root/workspace/wx-crawl/wx-crawl-db ingest --run-dir <run_dir>
   /root/workspace/wx-crawl/wx-crawl-db prune --run-dir <run_dir> --confirm-delete
   ```

   Never prune before successful labeling and import. Preserve source data on failure.

8. Synchronize once after all successful imports and verify the returned counts:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python \
     /root/workspace/wx-crawl/skill/wechat-ai-table-sync/scripts/sync_articles.py \
     --mode incremental
   ```

9. Write `pipeline_state.json` as completed only after every required stage and
   AI-table readback succeeds.

## Individual stages

When the user requests only one stage, run only that stage and its required read-only
precondition checks:

- crawl: authenticate and crawl; do not label automatically;
- label: require a specific `run_dir`, run the Python labeler, then report its counts;
- select: require zero pending labels, run `matches`, and report the ledger path;
- report/import: require validated matches and complete summaries before writing/importing;
- sync: read the existing SQLite database and synchronize without crawling or relabeling.

Do not expand a partial-stage request into destructive cleanup or unrelated external sync.

## Failure and completion rules

- Authentication or crawl failure blocks only the new crawl batch; a scheduled catch-up
  job may still process previously completed crawl batches.
- Label, selector, report, or database failure leaves the batch pending and preserves files.
- Use URL/idempotency keys for retries; do not infer completion from an Agent narrative.
- A successful report includes run IDs, labeling counts, pending/review/selected counts,
  ledger path and count, database results, cleanup results, and sync/readback results.
