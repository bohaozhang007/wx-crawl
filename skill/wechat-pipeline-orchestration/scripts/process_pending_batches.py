#!/usr/bin/env python3
"""Discover and safely process every uncompleted crawl batch."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO = Path("/root/workspace/wx-crawl")
STATE = REPO / "skill/wechat-pipeline-orchestration/scripts/pipeline_state.py"
SELECTOR = REPO / "skill/article-label-export/scripts/select_articles.py"
DB = REPO / "wx-crawl-db"
SYNC = REPO / "skill/wechat-ai-table-sync/scripts/sync_articles.py"
RECORD = REPO / "results/record"
PYTHON = REPO / ".venv/bin/python"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Process all pending crawl batches; never crawls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="show per-run detailed JSON")
    args = parser.parse_args()
    batches = [p for p in pending() if not is_covered_by_backfill(p)]
    details = {
        "pending_batches": [str(p) for p in batches],
        "count": len(batches),
        "covered_by_backfill": len(pending()) - len(batches),
        "processed": [],
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
    for run_dir in batches:
        # Each batch is independent and is marked only after every stage succeeds.
        run([str(PYTHON), str(SELECTOR), "status", "--run-dir", str(run_dir)])
        run([str(PYTHON), str(SELECTOR), "candidates", "--run-dir", str(run_dir)])
        # A single API-backed Python process labels the complete run. Never
        # reintroduce an Agent-side per-article inventory/write loop here.
        label_result = parse_json_object(
            run([str(PYTHON), "-m", "src.labeling.cli", "--run-dir", str(run_dir)])
        )
        if int(label_result.get("failed", 0)):
            raise RuntimeError(f"{run_dir.name}: Python labeler reported failures")
        require_labels(run_dir)
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
        db_result = run([str(DB), "ingest", "--run-dir", str(run_dir)])
        db_json = parse_json_object(db_result)
        run([str(DB), "prune", "--run-dir", str(run_dir), "--confirm-delete"])
        sync_result = run([str(PYTHON), str(SYNC), "--mode", "incremental"])
        sync_json = parse_json_object(sync_result)
        run([str(PYTHON), str(STATE), "mark-completed", str(run_dir), "--selected", str(selected), "--database-result", json.dumps(db_json, ensure_ascii=False), "--sync-result", json.dumps(sync_json, ensure_ascii=False)])
        details["processed"].append(
            {"run_id": run_dir.name, "selected": selected, "database": db_json, "sync": sync_json}
        )
    details_path = write_details(details)
    payload = details if args.verbose else {
        "status": "ok", "command": "process-pending", "dry_run": False,
        "pending_count": len(batches), "processed_count": len(details["processed"]),
        "selected_count": sum(item["selected"] for item in details["processed"]),
        "details_file": str(details_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.verbose else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
