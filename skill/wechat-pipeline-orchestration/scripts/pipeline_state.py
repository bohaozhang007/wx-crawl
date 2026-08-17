#!/usr/bin/env python3
"""Track completed WeChat label/import/sync batches without touching SQLite."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

RECORD_ROOT = Path("/root/workspace/wx-crawl/results/record")
STATE_NAME = "pipeline_state.json"
SCHEMA_VERSION = 1


def run_dirs() -> list[Path]:
    if not RECORD_ROOT.is_dir():
        return []
    return sorted(
        (p for p in RECORD_ROOT.iterdir() if p.is_dir() and (p / "article_details.csv").is_file()),
        key=lambda p: p.name,
    )


def read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads((path / STATE_NAME).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def load_coverage() -> set[str]:
    path = RECORD_ROOT / "pipeline_coverage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("covered_run_ids", []))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()


def is_completed(path: Path) -> bool:
    state = read_state(path)
    return (state.get("status") == "completed" and state.get("schema_version") == SCHEMA_VERSION and all(state.get("stages", {}).get(stage) == "completed" for stage in ("label", "report", "database", "cleanup", "ai_table_sync"))) or path.name in load_coverage()


def write_state(path: Path, *, selected: int | None = None, imported: dict[str, Any] | None = None, sync: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "run_id": path.name,
        "completed_at": int(time.time()),
        "stages": {"label": "completed", "report": "completed", "database": "completed", "cleanup": "completed", "ai_table_sync": "completed"},
    }
    if selected is not None:
        payload["selected_count"] = selected
    if imported is not None:
        payload["database_result"] = imported
    if sync is not None:
        payload["ai_table_result"] = sync
    fd, tmp = tempfile.mkstemp(prefix=".pipeline_state.", suffix=".tmp", dir=path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path / STATE_NAME)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list-pending")
    list_parser.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("run_dir")
    mark = sub.add_parser("mark-completed")
    mark.add_argument("run_dir")
    mark.add_argument("--selected", type=int)
    mark.add_argument("--database-result")
    mark.add_argument("--sync-result")
    args = parser.parse_args()

    if args.command == "list-pending":
        pending = [str(p) for p in run_dirs() if not is_completed(p)]
        if args.json:
            print(json.dumps(pending, ensure_ascii=False))
        else:
            print("\n".join(pending))
        return 0
    path = Path(args.run_dir).resolve()
    if path.parent != RECORD_ROOT.resolve() or not path.is_dir():
        raise SystemExit(f"invalid run directory: {path}")
    if args.command == "show":
        print(json.dumps(read_state(path), ensure_ascii=False, indent=2))
        return 0
    imported = json.loads(args.database_result) if args.database_result else None
    sync = json.loads(args.sync_result) if args.sync_result else None
    write_state(path, selected=args.selected, imported=imported, sync=sync)
    print(path / STATE_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
