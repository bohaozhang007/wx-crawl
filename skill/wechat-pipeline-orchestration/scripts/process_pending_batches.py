#!/usr/bin/env python3
"""Discover and safely process every uncompleted crawl batch."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path("/root/workspace/wx-crawl")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.auth.dingtalk_notify import send_pipeline_stage

STATE = REPO / "skill/wechat-pipeline-orchestration/scripts/pipeline_state.py"
SELECTOR = REPO / "skill/article-label-export/scripts/select_articles.py"
DB = REPO / "wx-crawl-db"
SYNC = REPO / "skill/wechat-ai-table-sync/scripts/sync_articles.py"
RECORD = REPO / "results/record"
CRAWLER_LOCK = RECORD / ".crawler.lock"
PYTHON = REPO / ".venv/bin/python"
LABEL_PROCESS_ATTEMPTS = 2
LABEL_RETRY_DELAY_SECONDS = 5


def wait_for_crawler(
    lock_path: Path = CRAWLER_LOCK,
    timeout_seconds: float = 6 * 60 * 60,
    poll_interval: float = 5,
    settle_seconds: float = 2,
) -> bool:
    """Wait without Agent polling until a concurrent crawl has finalized its records."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    waited = False
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                waited = True
                if time.monotonic() - started >= timeout_seconds:
                    raise RuntimeError("timed out waiting for the active crawler")
                time.sleep(poll_interval)
                continue
            try:
                break
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    if waited:
        time.sleep(settle_seconds)
    return waited


def run(cmd: list[str], *, allow_fail: bool = False) -> str:
    result = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
    if result.returncode and not allow_fail:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def pending() -> list[Path]:
    raw = run([str(PYTHON), str(STATE), "list-pending", "--json"])
    return [Path(p) for p in json.loads(raw)]


def is_covered_by_backfill(path: Path) -> bool:
    coverage = RECORD / "pipeline_coverage.json"
    try:
        data = json.loads(coverage.read_text(encoding="utf-8"))
        return path.name in set(data.get("covered_run_ids", []))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def parse_json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        # Older selector versions may emit surrounding whitespace or log lines;
        # recover the last complete JSON object without trusting a single tail line.
        decoder = json.JSONDecoder()
        candidates: list[tuple[int, dict]] = []
        for index, char in enumerate(raw):
            if char != "{":
                continue
            try:
                candidate, length = decoder.raw_decode(raw[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                candidates.append((length, candidate))
        if not candidates:
            raise RuntimeError(f"invalid command JSON: {raw}")
        value = max(candidates, key=lambda item: item[0])[1]
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid command JSON: {raw}")
    return value


def status_json(run_dir: Path) -> dict:
    raw = run([str(PYTHON), str(SELECTOR), "status", "--run-dir", str(run_dir)])
    try:
        return parse_json_object(raw)
    except RuntimeError as exc:
        raise RuntimeError(f"invalid status JSON for {run_dir}: {raw}") from exc


def require_labels(run_dir: Path) -> dict:
    status = status_json(run_dir)
    pending = int(status.get("pending_label_count", 0))
    if pending:
        raise RuntimeError(
            f"{run_dir.name}: {pending} articles still need label.json; refusing matches/report/ingest/completed"
        )
    return status


def label_run_with_retries(
    run_dir: Path,
    attempts: int = LABEL_PROCESS_ATTEMPTS,
    retry_delay: float = LABEL_RETRY_DELAY_SECONDS,
    sleep_fn=time.sleep,
) -> tuple[dict, list[dict]]:
    """Retry only unresolved articles; valid v2 labels are skipped by the labeler."""
    if attempts < 1:
        raise ValueError("label attempts must be at least one")
    history: list[dict] = []
    result: dict = {}
    for attempt in range(1, attempts + 1):
        result = parse_json_object(
            run(
                [str(PYTHON), "-m", "src.labeling.cli", "--run-dir", str(run_dir)],
                allow_fail=True,
            )
        )
        history.append(
            {
                "attempt": attempt,
                "candidates": int(result.get("candidates", 0)),
                "labeled": int(result.get("labeled", 0)),
                "failed": int(result.get("failed", 0)),
                "skipped_valid": int(result.get("skipped_valid", 0)),
                "details_file": result.get("details_file"),
            }
        )
        if not int(result.get("failed", 0)):
            return result, history
        if attempt < attempts:
            sleep_fn(retry_delay)
    return result, history


def write_details(payload: dict) -> Path:
    stamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S_%f")
    path = RECORD / f"pipeline_execution_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return path


def label_batch(run_dir: Path) -> dict:
    run([str(PYTHON), str(SELECTOR), "status", "--run-dir", str(run_dir)])
    run([str(PYTHON), str(SELECTOR), "candidates", "--run-dir", str(run_dir)])
    label_result, label_attempts = label_run_with_retries(run_dir)
    if int(label_result.get("failed", 0)):
        raise RuntimeError(
            f"{run_dir.name}: Python labeler still has "
            f"{int(label_result.get('failed', 0))} failures after "
            f"{len(label_attempts)} process attempts; retry on the next pipeline run"
        )
    status = require_labels(run_dir)
    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "article_count": int(status.get("input_count", 0)),
        "label_attempts": label_attempts,
    }


def select_batch(context: dict) -> dict:
    run_dir = Path(context["run_dir"])
    match_result = parse_json_object(
        run([str(PYTHON), str(SELECTOR), "matches", "--run-dir", str(run_dir)])
    )
    selected = int(match_result.get("count", 0))
    ledger_path = run_dir / "labeling_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if len(ledger.get("entries", [])) != int(ledger.get("candidates", -1)):
        raise RuntimeError(f"{run_dir.name}: invalid or incomplete labeling ledger")
    report = run_dir / "filtered_articles.json"
    # New v2 labels contain a summary from the same model call. The report
    # writer reads it directly and materializes article_summaries.json for
    # compatibility; no Agent-side per-article summary loop is allowed.
    run([str(PYTHON), str(SELECTOR), "write-report", "--run-dir", str(run_dir)])
    report_data = json.loads(report.read_text(encoding="utf-8"))
    if int(report_data.get("count", len(report_data.get("articles", [])))) != selected:
        raise RuntimeError(f"{run_dir.name}: matches/report count mismatch")
    decisions: dict[str, int] = {"KEEP": 0, "DROP": 0, "REVIEW": 0}
    for entry in ledger.get("entries", []):
        decision = entry.get("decision")
        if decision in decisions:
            decisions[decision] += 1
    return {**context, "selected": selected, "decisions": decisions}


def ingest_batch(context: dict) -> dict:
    run_dir = Path(context["run_dir"])
    db_json = parse_json_object(run([str(DB), "ingest", "--run-dir", str(run_dir)]))
    run([str(DB), "prune", "--run-dir", str(run_dir), "--confirm-delete"])
    return {**context, "database": db_json}


def complete_batch(context: dict, sync_json: dict) -> dict:
    run_dir = Path(context["run_dir"])
    run(
        [
            str(PYTHON), str(STATE), "mark-completed", str(run_dir),
            "--selected", str(context["selected"]),
            "--database-result", json.dumps(context["database"], ensure_ascii=False),
            "--sync-result", json.dumps(sync_json, ensure_ascii=False),
        ]
    )
    return {**context, "sync": sync_json}


def run_isolated(items: list, stage: str, operation) -> tuple[list[dict], list[dict]]:
    """Keep one batch failure from stopping the same stage for later batches."""
    succeeded: list[dict] = []
    failed: list[dict] = []
    for item in items:
        run_dir = Path(item["run_dir"]) if isinstance(item, dict) else Path(item)
        try:
            succeeded.append(operation(item))
        except Exception as exc:
            failed.append(
                {"run_id": run_dir.name, "stage": stage, "error": str(exc)[:2000]}
            )
    return succeeded, failed


def notify_stage(stage: str, payload: dict, notifications: list[dict]) -> None:
    """Notify directly from Python; delivery failure is recorded but never blocks data work."""
    try:
        send_pipeline_stage(stage, payload)
        notifications.append({"stage": stage, "status": "sent"})
    except Exception as exc:
        notifications.append(
            {"stage": stage, "status": "failed", "error": str(exc)[:1000]}
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Process all pending crawl batches; never crawls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="show per-run detailed JSON")
    args = parser.parse_args()
    waited_for_crawler = wait_for_crawler()
    all_pending = pending()
    batches = [p for p in all_pending if not is_covered_by_backfill(p)]
    details = {
        "pending_batches": [str(p) for p in batches],
        "count": len(batches),
        "covered_by_backfill": len(all_pending) - len(batches),
        "waited_for_crawler": waited_for_crawler,
        "processed": [],
        "failed": [],
    }
    if args.dry_run:
        details_path = write_details(details)
        payload = details if args.verbose else {
            "status": "ok", "command": "process-pending", "dry_run": True,
            "pending_count": len(batches), "processed_count": 0,
            "details_file": str(details_path),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.verbose else None))
        return 0
    details["notifications"] = []

    label_started = time.monotonic()
    labeled, label_failures = run_isolated(batches, "label", label_batch)
    details["failed"].extend(label_failures)
    label_summary = {
        "batch_count": len(batches),
        "article_count": sum(item["article_count"] for item in labeled),
        "labeled_count": sum(
            attempt["labeled"]
            for item in labeled
            for attempt in item["label_attempts"]
        ),
        "retry_article_count": sum(
            attempt["candidates"]
            for item in labeled
            for attempt in item["label_attempts"][1:]
        ),
        "failed_batch_count": len(details["failed"]),
        "duration_seconds": round(time.monotonic() - label_started, 3),
    }
    details["label_summary"] = label_summary
    if batches:
        notify_stage("label", label_summary, details["notifications"])

    select_started = time.monotonic()
    selected_batches, select_failures = run_isolated(labeled, "select", select_batch)
    details["failed"].extend(select_failures)
    select_summary = {
        "keep_count": sum(item["decisions"]["KEEP"] for item in selected_batches),
        "drop_count": sum(item["decisions"]["DROP"] for item in selected_batches),
        "review_count": sum(item["decisions"]["REVIEW"] for item in selected_batches),
        "failed_batch_count": len(details["failed"]),
        "duration_seconds": round(time.monotonic() - select_started, 3),
    }
    details["select_summary"] = select_summary
    if batches:
        notify_stage("select", select_summary, details["notifications"])

    storage_started = time.monotonic()
    ingested, ingest_failures = run_isolated(selected_batches, "database", ingest_batch)
    details["failed"].extend(ingest_failures)
    sync_json: dict = {"inserted": 0, "updated": 0, "unchanged": 0}
    completed: list[dict] = []
    if ingested:
        try:
            sync_json = parse_json_object(
                run([str(PYTHON), str(SYNC), "--mode", "incremental"])
            )
        except Exception as exc:
            details["failed"].extend(
                {
                    "run_id": item["run_id"],
                    "stage": "ai_table_sync",
                    "error": str(exc)[:2000],
                }
                for item in ingested
            )
        else:
            completed, complete_failures = run_isolated(
                ingested,
                "complete",
                lambda item: complete_batch(item, sync_json),
            )
            details["failed"].extend(complete_failures)
    details["processed"] = completed
    storage_summary = {
        "completed_batch_count": len(completed),
        "failed_batch_count": len(details["failed"]),
        "article_count": sum(item["selected"] for item in completed),
        "db_inserted": sum(int(item["database"].get("inserted", 0)) for item in completed),
        "db_updated": sum(int(item["database"].get("updated", 0)) for item in completed),
        "sync_inserted": int(sync_json.get("inserted", 0)),
        "sync_updated": int(sync_json.get("updated", 0)),
        "sync_unchanged": int(sync_json.get("unchanged", 0)),
        "duration_seconds": round(time.monotonic() - storage_started, 3),
    }
    details["storage_summary"] = storage_summary
    if batches:
        notify_stage("storage", storage_summary, details["notifications"])

    details_path = write_details(details)
    has_failures = bool(details["failed"])
    notification_failed_count = sum(
        item.get("status") == "failed" for item in details["notifications"]
    )
    payload = details if args.verbose else {
        "status": "failed" if has_failures else "ok",
        "command": "process-pending", "dry_run": False,
        "pending_count": len(batches), "processed_count": len(details["processed"]),
        "failed_batch_count": len(details["failed"]),
        "notification_failed_count": notification_failed_count,
        "selected_count": sum(item["selected"] for item in details["processed"]),
        "details_file": str(details_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.verbose else None))
    return 1 if has_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
