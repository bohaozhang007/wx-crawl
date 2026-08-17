from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
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
LOG_PATH_NAME = "article_label_export.log"
LOGGER = logging.getLogger("article-label-export")


def setup_logging(log_dir: Path | None = None, verbose: bool = False) -> None:
    LOGGER.setLevel(logging.INFO)
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    LOGGER.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if verbose:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        LOGGER.addHandler(console)

    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / LOG_PATH_NAME, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError as exc:
        LOGGER.warning("无法写入流程日志文件 %s: %s", log_dir / LOG_PATH_NAME, exc)


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


def output(value: object, verbose: bool = False) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if verbose else None))


def write_details(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return path


def add_verbose(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--verbose", action="store_true", help="show detailed logs and JSON")


def transient_details_path(name: str) -> Path:
    return RECORD_ROOT / ".cli" / f"{name}_{time.time_ns()}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Store and query filtered WeChat articles")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize the SQLite database")
    add_verbose(init)

    ingest = subparsers.add_parser("ingest", help="import a filtered_articles.json report")
    ingest.add_argument("--run-dir")
    ingest.add_argument("--report")
    ingest.add_argument("--prune", action="store_true", help="preview explicit DROP current-run articles")
    ingest.add_argument("--confirm-delete", action="store_true", help="actually delete explicit DROP article directories")
    add_verbose(ingest)

    listing = subparsers.add_parser("list", help="list stored articles as JSON")
    listing.add_argument("--domain", choices=DOMAINS)
    listing.add_argument("--application-type", choices=APPLICATION_TYPES)
    listing.add_argument("--since", type=int)
    listing.add_argument("--until", type=int)
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--channel")
    listing.add_argument("--pending-only", action="store_true")
    listing.add_argument("--json", action="store_true", help="kept for explicit machine-readable output")
    add_verbose(listing)

    pending = subparsers.add_parser("pending-delivery", help="list articles pending delivery")
    pending.add_argument("--channel", required=True)
    pending.add_argument("--limit", type=int, default=100)
    pending.add_argument("--json", action="store_true", help="kept for explicit machine-readable output")
    add_verbose(pending)

    delivered = subparsers.add_parser("mark-delivered", help="mark articles as delivered")
    delivered.add_argument("--channel", required=True)
    delivered.add_argument("--id", type=int, action="append", required=True)
    delivered.add_argument("--response", default="")
    add_verbose(delivered)

    prune = subparsers.add_parser("prune", help="preview or delete explicit DROP current-run articles")
    prune.add_argument("--run-dir")
    prune.add_argument("--report")
    prune.add_argument("--all-unselected", action="store_true", help="scan all article directories, not just one run")
    prune.add_argument("--confirm-delete", action="store_true")
    add_verbose(prune)

    stats_parser = subparsers.add_parser("stats", help="show database statistics")
    add_verbose(stats_parser)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_dir_for_log: Path | None = None
    if args.command in {"ingest", "prune"} and not getattr(args, "all_unselected", False):
        try:
            run_dir_for_log = resolve_run_dir(args.run_dir)
        except StorageError:
            run_dir_for_log = None
    setup_logging(run_dir_for_log, args.verbose)
    started = time.monotonic()
    LOGGER.info("开始执行 wx-crawl-db command=%s db=%s", args.command, Path(args.db).resolve())
    try:
        if args.command == "init":
            init_database(args.db)
            output({"status": "ok", "command": "init", "database": str(args.db.resolve()), "initialized": True}, args.verbose)
        elif args.command == "ingest":
            run_dir = run_dir_for_log or resolve_run_dir(args.run_dir)
            LOGGER.info("开始导入命令 run_dir=%s", run_dir)
            report = report_path(run_dir, args.report)
            result = import_report(report, args.db)
            if args.prune:
                prune_result = prune_run(run_dir, report, args.confirm_delete, args.db)
                details_path = write_details(run_dir / "cleanup_result.json", prune_result)
                result["prune"] = prune_result if args.verbose else {
                    "confirmed": bool(prune_result.get("confirmed")),
                    "deleted": int(prune_result.get("deleted", 0) or 0),
                    "would_delete": len(prune_result.get("would_delete", [])),
                    "protected_count": len(prune_result.get("protected", [])),
                    "details_file": str(details_path),
                }
            output({"status": "ok", "command": "ingest", **result}, args.verbose)
            LOGGER.info("导入命令完成 run_id=%s articles=%s", result.get("run_id"), result.get("articles"))
        elif args.command == "list":
            result = query_articles(args.db, domain=args.domain, application_type=args.application_type, since=args.since, until=args.until, limit=args.limit, channel=args.channel, pending_only=args.pending_only)
            if args.json or args.verbose:
                output(result, args.verbose)
            else:
                path = write_details(transient_details_path("database_query_result"), result)
                output({"status": "ok", "command": "list", "count": len(result), "details_file": str(path)})
        elif args.command == "pending-delivery":
            result = query_articles(args.db, channel=args.channel, limit=args.limit, pending_only=True)
            if args.json or args.verbose:
                output(result, args.verbose)
            else:
                path = write_details(transient_details_path("pending_delivery_result"), result)
                output({"status": "ok", "command": "pending-delivery", "count": len(result), "details_file": str(path)})
        elif args.command == "mark-delivered":
            output({"status": "ok", "command": "mark-delivered", "updated": mark_delivered(args.db, args.id, args.channel, args.response)}, args.verbose)
        elif args.command == "prune":
            if args.all_unselected:
                result = prune_all_unselected(args.db, args.confirm_delete)
                details_path = write_details(transient_details_path("cleanup_all_unselected_result"), result)
            else:
                run_dir = run_dir_for_log or resolve_run_dir(args.run_dir)
                LOGGER.info("开始清理命令 run_dir=%s confirm=%s", run_dir, args.confirm_delete)
                report = report_path(run_dir, args.report)
                result = prune_run(run_dir, report, args.confirm_delete, args.db)
                details_path = write_details(run_dir / "cleanup_result.json", result)
            if args.verbose:
                output(result, True)
            else:
                output({
                    "status": "ok", "command": "prune",
                    "confirmed": bool(result.get("confirmed")),
                    "deleted": int(result.get("deleted", 0) or 0),
                    "would_delete": len(result.get("would_delete", [])),
                    "protected_count": len(result.get("protected", [])),
                    "skipped_count": len(result.get("skipped", [])),
                    "details_file": str(details_path),
                })
        elif args.command == "stats":
            output({"status": "ok", "command": "stats", **stats(args.db)}, args.verbose)
    except (StorageError, OSError) as exc:
        LOGGER.error("wx-crawl-db 执行失败 command=%s error=%s", args.command, exc)
        parser.error(str(exc))
    finally:
        LOGGER.info("wx-crawl-db 执行结束 command=%s elapsed=%.1fs", args.command, time.monotonic() - started)


if __name__ == "__main__":
    main()
