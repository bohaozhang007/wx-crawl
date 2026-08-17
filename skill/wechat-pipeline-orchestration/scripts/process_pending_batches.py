#!/usr/bin/env python3
"""Discover and safely process every uncompleted crawl batch."""
from __future__ import annotations

import argparse
import json
import subprocess
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Process all pending crawl batches; never crawls")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    batches = [p for p in pending() if not is_covered_by_backfill(p)]
    print(json.dumps({"pending_batches": [str(p) for p in batches], "count": len(batches), "covered_by_backfill": len(pending()) - len(batches)}, ensure_ascii=False))
    if args.dry_run:
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
        summaries = run_dir / "article_summaries.json"
        if selected and not summaries.is_file():
            raise RuntimeError(f"{run_dir.name}: selected articles exist but summaries are missing")
        if not summaries.is_file():
            summaries.write_text("{}\n", encoding="utf-8")
        run([str(PYTHON), str(SELECTOR), "write-report", "--run-dir", str(run_dir), "--summaries", str(summaries)])
        report_data = json.loads(report.read_text(encoding="utf-8"))
        if int(report_data.get("count", len(report_data.get("articles", [])))) != selected:
            raise RuntimeError(f"{run_dir.name}: matches/report count mismatch")
        db_result = run([str(DB), "ingest", "--run-dir", str(run_dir)])
        db_json = parse_json_object(db_result)
        run([str(DB), "prune", "--run-dir", str(run_dir), "--confirm-delete"])
        sync_result = run([str(PYTHON), str(SYNC), "--mode", "incremental"])
        sync_json = parse_json_object(sync_result)
        run([str(PYTHON), str(STATE), "mark-completed", str(run_dir), "--selected", str(selected), "--database-result", json.dumps(db_json, ensure_ascii=False), "--sync-result", json.dumps(sync_json, ensure_ascii=False)])
        print(json.dumps({"run_id": run_dir.name, "selected": selected, "database": db_json, "sync": sync_json}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
