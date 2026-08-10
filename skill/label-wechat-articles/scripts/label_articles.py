#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any


ARTICLES_ROOT = Path("/root/workspace/wx-crawl/results/articles")
APPLICATION_TYPES = ("科研项目申请", "科研指南申请", "都不是")
DOMAINS = ("无人机", "卫星", "具身智能", "大模型", "空天", "机器人", "机械臂")
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


def validate_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root value must be a JSON object"]

    expected_keys = {"application_type", "domains"}
    actual_keys = set(payload)
    if actual_keys != expected_keys:
        errors.append(
            "keys must be exactly application_type and domains; got "
            + ", ".join(sorted(actual_keys))
        )

    application_type = payload.get("application_type")
    if application_type not in APPLICATION_TYPES:
        errors.append("application_type is not an allowed value")

    domains = payload.get("domains")
    if not isinstance(domains, list):
        errors.append("domains must be a JSON array")
    else:
        unknown = [value for value in domains if value not in DOMAINS]
        if unknown:
            errors.append("domains contains unknown values: " + ", ".join(map(str, unknown)))
        if len(domains) != len(set(map(str, domains))):
            errors.append("domains contains duplicate values")
        expected_order = [domain for domain in DOMAINS if domain in domains]
        if domains != expected_order:
            errors.append("domains is not in canonical order")
    return errors


def label_errors(article_dir: Path) -> list[str]:
    label_path = article_dir / "label.json"
    if not label_path.is_file():
        return ["label.json is missing"]
    try:
        payload = json.loads(label_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"cannot read valid JSON: {exc}"]
    return validate_payload(payload)


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
    application_type: str,
    domains: list[str],
    replace: bool,
) -> Path:
    label_path = article_dir / "label.json"
    if label_path.exists() and not replace:
        existing_errors = label_errors(article_dir)
        if not existing_errors:
            raise LabelError(f"valid label already exists; use --replace to overwrite: {label_path}")

    selected = set(domains)
    payload = {
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
    try:
        args.func(args)
    except LabelError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
