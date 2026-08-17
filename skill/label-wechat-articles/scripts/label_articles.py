#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.labeling.schema import (  # noqa: E402
    APPLICATION_TYPES,
    DECISIONS,
    DOMAINS,
    EVIDENCE_TYPES,
    SCHEMA_VERSION,
    load_tree_spec,
    read_label,
    validate_payload,
)

ARTICLES_ROOT = Path("/root/workspace/wx-crawl/results/articles")
TEXT_SUFFIXES = {".csv", ".htm", ".html", ".json", ".log", ".md", ".txt", ".xml"}
ARTICLE_MARKERS = ("content.txt", "metadata.json", "data.json")


class LabelError(ValueError):
    pass


def article_directories() -> list[Path]:
    if not ARTICLES_ROOT.is_dir():
        return []

    articles: list[Path] = []
    for account_dir in ARTICLES_ROOT.iterdir():
        if not account_dir.is_dir():
            continue
        for article_dir in account_dir.iterdir():
            if not article_dir.is_dir():
                continue
            has_marker = any((article_dir / name).is_file() for name in ARTICLE_MARKERS)
            if has_marker or any(article_dir.glob("*.html")):
                articles.append(article_dir)
    return articles


def resolve_article_dir(raw_path: str) -> Path:
    path = Path(raw_path).resolve()
    root = ARTICLES_ROOT.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LabelError(f"article directory is outside {root}: {path}") from exc
    if len(relative.parts) != 2 or not path.is_dir():
        raise LabelError(f"not an article directory: {path}")
    return path


def label_errors(article_dir: Path) -> list[str]:
    label_path = article_dir / "label.json"
    if not label_path.is_file():
        return ["label.json is missing"]
    _, errors = read_label(label_path)
    return errors


def pending_articles() -> list[Path]:
    pending = [path for path in article_directories() if label_errors(path)]
    return sorted(pending, key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def inventory(article_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    ignored_media_count = 0
    for path in sorted(article_dir.rglob("*")):
        if not path.is_file() or path.name == "label.json":
            continue
        if not is_text_file(path):
            ignored_media_count += 1
            continue
        mime_type, _ = mimetypes.guess_type(path.name)
        files.append(
            {
                "path": str(path.relative_to(article_dir)),
                "bytes": path.stat().st_size,
                "mime_type": mime_type or "text/plain",
            }
        )
    return {
        "article_dir": str(article_dir),
        "files": files,
        "text_file_count": len(files),
        "ignored_media_count": ignored_media_count,
    }


def write_label(
    article_dir: Path,
    decision: str,
    reason_code: str,
    decision_path: list[str],
    reason: str,
    evidence: list[list[str]],
    application_type: str,
    domains: list[str],
    replace: bool,
) -> Path:
    label_path = article_dir / "label.json"
    if label_path.exists() and not replace:
        existing_errors = label_errors(article_dir)
        if not existing_errors:
            raise LabelError(f"valid label already exists; use --replace to overwrite: {label_path}")
        raise LabelError(
            f"invalid or legacy label already exists; re-read the article and use --replace: {label_path}"
        )

    selected = set(domains)
    tree_spec = load_tree_spec()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tree_version": tree_spec["version"],
        "decision": decision,
        "decision_path": decision_path,
        "reason_code": reason_code,
        "reason": reason.strip(),
        "evidence": [
            {"type": evidence_type, "location": location.strip(), "text": text.strip()}
            for evidence_type, location, text in evidence
        ],
        "application_type": application_type,
        "domains": [domain for domain in DOMAINS if domain in selected],
    }
    errors = validate_payload(payload)
    if errors:
        raise LabelError("; ".join(errors))

    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=article_dir,
            prefix=".label.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, label_path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return label_path


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def command_next(args: argparse.Namespace) -> None:
    for path in pending_articles()[: args.limit]:
        print(path)


def command_count(_: argparse.Namespace) -> None:
    articles = article_directories()
    pending = [path for path in articles if label_errors(path)]
    print_json({"articles": len(articles), "valid": len(articles) - len(pending), "pending": len(pending)})


def command_inventory(args: argparse.Namespace) -> None:
    print_json(inventory(resolve_article_dir(args.article_dir)))


def command_write(args: argparse.Namespace) -> None:
    path = write_label(
        resolve_article_dir(args.article_dir),
        args.decision,
        args.reason_code,
        args.path_step,
        args.reason,
        args.evidence,
        args.application_type,
        args.domain,
        args.replace,
    )
    print(path)


def command_validate(_: argparse.Namespace) -> None:
    articles = article_directories()
    invalid: list[dict[str, Any]] = []
    labeled = 0
    for article_dir in articles:
        if (article_dir / "label.json").is_file():
            labeled += 1
        errors = label_errors(article_dir)
        if errors and errors != ["label.json is missing"]:
            invalid.append({"article_dir": str(article_dir), "errors": errors})
    print_json(
        {
            "articles": len(articles),
            "labeled": labeled,
            "valid": labeled - len(invalid),
            "pending": len(articles) - labeled + len(invalid),
            "invalid": invalid,
        }
    )
    if invalid:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage per-article label.json files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser("next", help="print newest pending article paths")
    next_parser.add_argument("--limit", type=int, default=1)
    next_parser.set_defaults(func=command_next)

    count_parser = subparsers.add_parser("count", help="count valid and pending articles")
    count_parser.set_defaults(func=command_count)

    inventory_parser = subparsers.add_parser("inventory", help="list every article artifact")
    inventory_parser.add_argument("article_dir")
    inventory_parser.set_defaults(func=command_inventory)

    write_parser = subparsers.add_parser("write", help="validate and atomically write label.json")
    write_parser.add_argument("article_dir")
    write_parser.add_argument("--decision", required=True, choices=DECISIONS)
    write_parser.add_argument("--reason-code", required=True)
    write_parser.add_argument(
        "--path-step",
        action="append",
        default=[],
        required=True,
        help="repeat ordered steps such as E1:PASS and O1:O1-D1",
    )
    write_parser.add_argument("--reason", required=True)
    write_parser.add_argument(
        "--evidence",
        action="append",
        nargs=3,
        metavar=("TYPE", "LOCATION", "TEXT"),
        default=[],
        required=True,
        help="repeat TYPE LOCATION TEXT; TYPE follows the v2 evidence vocabulary",
    )
    write_parser.add_argument("--application-type", required=True, choices=APPLICATION_TYPES)
    write_parser.add_argument("--domain", action="append", default=[], choices=DOMAINS)
    write_parser.add_argument("--replace", action="store_true")
    write_parser.set_defaults(func=command_write)

    validate_parser = subparsers.add_parser("validate", help="validate all existing labels")
    validate_parser.set_defaults(func=command_validate)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "limit", 1) < 1:
        parser.error("--limit must be at least 1")
    if getattr(args, "evidence", None):
        invalid_types = [item[0] for item in args.evidence if item[0] not in EVIDENCE_TYPES]
        if invalid_types:
            parser.error("invalid --evidence TYPE: " + ", ".join(invalid_types))
    try:
        args.func(args)
    except LabelError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
