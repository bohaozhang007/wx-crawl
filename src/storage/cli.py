from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import (
    APPLICATION_TYPES,
    DEFAULT_DB_PATH,
    DOMAINS,
    StorageError,
    import_report,
    init_database,
    mark_delivered,
    prune_all_unselected,
    prune_run,
    query_articles,
    stats,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RECORD_ROOT = REPO_ROOT / "results" / "record"


def resolve_run_dir(raw: str | None) -> Path:
    if raw:
        path = Path(raw).resolve()
    else:
        candidates = sorted(
            (path for path in RECORD_ROOT.iterdir() if path.is_dir() and (path / "article_details.csv").is_file()),
            key=lambda path: path.name,
            reverse=True,
        ) if RECORD_ROOT.is_dir() else []
        if not candidates:
            raise StorageError("no crawl run with article_details.csv was found")
        path = candidates[0]
    try:
        path.relative_to(RECORD_ROOT.resolve())
    except ValueError as exc:
        raise StorageError(f"run directory is outside {RECORD_ROOT}: {path}") from exc
    if not (path / "article_details.csv").is_file():
        raise StorageError(f"article_details.csv is missing: {path}")
    return path


def report_path(run_dir: Path, raw: str | None) -> Path:
    path = Path(raw).resolve() if raw else run_dir / "filtered_articles.json"
    try:
        path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise StorageError(f"report is outside run directory: {path}") from exc
    if not path.is_file():
        raise StorageError(f"filtered report is missing: {path}")
    return path


def output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Store and query filtered WeChat articles")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize the SQLite database")

    ingest = subparsers.add_parser("ingest", help="import a filtered_articles.json report")
    ingest.add_argument("--run-dir")
    ingest.add_argument("--report")
    ingest.add_argument("--prune", action="store_true", help="preview or prune non-selected current-run articles")
    ingest.add_argument("--confirm-delete", action="store_true", help="actually delete non-selected article directories")

    listing = subparsers.add_parser("list", help="list stored articles as JSON")
    listing.add_argument("--domain", choices=DOMAINS)
    listing.add_argument("--application-type", choices=APPLICATION_TYPES)
    listing.add_argument("--since", type=int)
    listing.add_argument("--until", type=int)
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--channel")
    listing.add_argument("--pending-only", action="store_true")
    listing.add_argument("--json", action="store_true", help="kept for explicit machine-readable output")

    pending = subparsers.add_parser("pending-delivery", help="list articles pending delivery")
    pending.add_argument("--channel", required=True)
    pending.add_argument("--limit", type=int, default=100)
    pending.add_argument("--json", action="store_true", help="kept for explicit machine-readable output")

    delivered = subparsers.add_parser("mark-delivered", help="mark articles as delivered")
    delivered.add_argument("--channel", required=True)
    delivered.add_argument("--id", type=int, action="append", required=True)
    delivered.add_argument("--response", default="")

    prune = subparsers.add_parser("prune", help="preview or delete non-selected current-run articles")
    prune.add_argument("--run-dir")
    prune.add_argument("--report")
    prune.add_argument("--all-unselected", action="store_true", help="scan all article directories, not just one run")
    prune.add_argument("--confirm-delete", action="store_true")

    subparsers.add_parser("stats", help="show database statistics")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "init":
            init_database(args.db)
            output({"database": str(args.db.resolve()), "initialized": True})
        elif args.command == "ingest":
            run_dir = resolve_run_dir(args.run_dir)
            report = report_path(run_dir, args.report)
            result = import_report(report, args.db)
            if args.prune:
                result["prune"] = prune_run(run_dir, report, args.confirm_delete, args.db)
            output(result)
        elif args.command == "list":
            output(query_articles(args.db, domain=args.domain, application_type=args.application_type, since=args.since, until=args.until, limit=args.limit, channel=args.channel, pending_only=args.pending_only))
        elif args.command == "pending-delivery":
            output(query_articles(args.db, channel=args.channel, limit=args.limit, pending_only=True))
        elif args.command == "mark-delivered":
            output({"updated": mark_delivered(args.db, args.id, args.channel, args.response)})
        elif args.command == "prune":
            if args.all_unselected:
                output(prune_all_unselected(args.db, args.confirm_delete))
            else:
                run_dir = resolve_run_dir(args.run_dir)
                report = report_path(run_dir, args.report)
                output(prune_run(run_dir, report, args.confirm_delete, args.db))
        elif args.command == "stats":
            output(stats(args.db))
    except (StorageError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
