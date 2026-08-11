#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


REPO_ROOT = Path("/root/workspace/wx-crawl")
ARTICLES_ROOT = REPO_ROOT / "results" / "articles"
RECORD_ROOT = REPO_ROOT / "results" / "record"
APPLICATION_TYPES = {"科研项目申请", "科研指南申请", "都不是"}
DOMAINS = ("无人机", "卫星", "具身智能", "大模型", "空天", "机器人", "机械臂")
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def article_directories() -> list[Path]:
    articles: list[Path] = []
    if not ARTICLES_ROOT.is_dir():
        return articles
    for account_dir in ARTICLES_ROOT.iterdir():
        if not account_dir.is_dir():
            continue
        for article_dir in account_dir.iterdir():
            if not article_dir.is_dir():
                continue
            if (
                (article_dir / "content.txt").is_file()
                or (article_dir / "metadata.json").is_file()
                or any(article_dir.glob("*.html"))
            ):
                articles.append(article_dir)
    return articles


def format_publish_time(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, LOCAL_TIMEZONE).strftime("%Y_%m_%d_%H_%M_%S")


def parse_publish_time(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def run_dir_candidates() -> list[Path]:
    if not RECORD_ROOT.is_dir():
        return []
    return sorted(
        (
            path
            for path in RECORD_ROOT.iterdir()
            if path.is_dir() and (path / "article_details.csv").is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def resolve_run_dir(raw: str | None) -> Path:
    if raw:
        path = Path(raw).resolve()
        root = RECORD_ROOT.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SystemExit(f"run directory is outside {root}: {path}") from exc
        if not (path / "article_details.csv").is_file():
            raise SystemExit(f"article_details.csv is missing: {path}")
        return path
    candidates = run_dir_candidates()
    if not candidates:
        raise SystemExit("no run record with article_details.csv was found")
    return candidates[0]


def load_run_rows(run_dir: Path) -> list[dict[str, str]]:
    with (run_dir / "article_details.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def article_metadata(article_dir: Path) -> dict[str, Any]:
    combined: dict[str, Any] = {}
    for name in ("metadata.json", "data.json", "fallback_metadata.json"):
        metadata = read_json(article_dir / name)
        for key in ("title", "url", "publish_time"):
            if not combined.get(key) and metadata.get(key):
                combined[key] = metadata[key]
    return combined


def account_name_from_dir(account_dir: Path) -> str:
    parts = account_dir.name.split("_", 4)
    return parts[4] if len(parts) == 5 else account_dir.name.rsplit("_", 1)[-1]


def is_wechat_article_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "mp.weixin.qq.com"


def match_run_articles(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    indexed: dict[tuple[str, str, str], list[Path]] = {}
    by_account_title: dict[tuple[str, str], list[Path]] = {}
    for article_dir in article_directories():
        metadata = article_metadata(article_dir)
        title = str(metadata.get("title") or "").strip()
        if not title:
            continue
        account_name = account_name_from_dir(article_dir.parent)
        publish_time = format_publish_time(metadata.get("publish_time"))
        indexed.setdefault((account_name, title, publish_time), []).append(article_dir)
        by_account_title.setdefault((account_name, title), []).append(article_dir)

    articles: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for row in load_run_rows(run_dir):
        account_name = str(row.get("公众号名称") or "").strip()
        title = str(row.get("爬取的文章名称") or "").strip()
        publish_time = str(row.get("文章发布时间") or "").strip()
        paths = indexed.get((account_name, title, publish_time), [])
        if not paths:
            paths = by_account_title.get((account_name, title), [])
        if not paths:
            skipped.append({"account": account_name, "title": title, "reason": "article directory not found"})
            continue
        for article_dir in paths[:1]:
            if str(article_dir) in seen_paths:
                continue
            seen_paths.add(str(article_dir))
            metadata = article_metadata(article_dir)
            articles.append(
                {
                    "article_dir": str(article_dir),
                    "title": str(metadata.get("title") or title).strip(),
                    "url": str(metadata.get("url") or "").strip(),
                    "publish_time": parse_publish_time(metadata.get("publish_time")),
                    "content_file": str(article_dir / "content.txt"),
                }
            )
    return articles, skipped


def valid_label(article_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    label_path = article_dir / "label.json"
    if not label_path.is_file():
        return None, "label.json is missing"
    try:
        label = json.loads(label_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid label JSON: {exc}"
    if not isinstance(label, dict) or set(label) != {"application_type", "domains"}:
        return None, "label schema is invalid"
    if label.get("application_type") not in APPLICATION_TYPES:
        return None, "application_type is invalid"
    domains = label.get("domains")
    if not isinstance(domains, list) or any(domain not in DOMAINS for domain in domains):
        return None, "domains is invalid"
    if len(domains) != len(set(map(str, domains))):
        return None, "domains contains duplicates"
    canonical = [domain for domain in DOMAINS if domain in domains]
    if domains != canonical:
        return None, "domains is not canonical"
    return label, None


def select_matches(run_dir: Path) -> dict[str, Any]:
    candidates, skipped = match_run_articles(run_dir)
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        label, reason = valid_label(Path(candidate["article_dir"]))
        if reason:
            skipped.append({"title": candidate["title"], "reason": reason})
            continue
        if not is_wechat_article_url(candidate["url"]):
            skipped.append({"title": candidate["title"], "reason": "valid WeChat article URL is missing"})
            continue
        if label["application_type"] == "都不是" or not label["domains"]:
            continue
        matches.append(
            {
                **candidate,
                "application_type": label["application_type"],
                "domains": label["domains"],
            }
        )
    matches.sort(key=lambda item: (item["publish_time"], item["title"]), reverse=True)
    return {"run_dir": str(run_dir), "count": len(matches), "articles": matches, "skipped": skipped}


def load_summaries(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read summaries JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("summaries JSON must map article URL (or article_dir/title) to a summary")
    summaries: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise SystemExit("summaries JSON keys and non-empty string values are required")
        summaries[key] = value.strip()
    return summaries


def write_report(run_dir: Path, summaries_path: Path, output_path: Path) -> None:
    selected = select_matches(run_dir)
    summaries = load_summaries(summaries_path)
    articles: list[dict[str, Any]] = []
    missing: list[str] = []
    for article in selected["articles"]:
        summary = (
            summaries.get(article["url"])
            or summaries.get(article["article_dir"])
            or summaries.get(article["title"])
        )
        if not summary:
            missing.append(article["title"])
            continue
        articles.append({**article, "summary": summary})
    if missing:
        raise SystemExit("missing summaries for: " + "；".join(missing))
    try:
        output_path = output_path.resolve()
        output_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise SystemExit(f"output report must be inside run directory: {output_path}") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_dir.name,
        "count": len(articles),
        "articles": articles,
        "skipped": selected["skipped"],
    }
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=".filtered_articles.", suffix=".tmp", delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Select current-run articles matching both label layers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("candidates", "matches"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--run-dir")
    report_parser = subparsers.add_parser("write-report", help="persist selected articles and agent summaries")
    report_parser.add_argument("--run-dir")
    report_parser.add_argument("--summaries", required=True, help="JSON object keyed by URL, article_dir, or title")
    report_parser.add_argument("--output", default="filtered_articles.json")
    args = parser.parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    candidates, skipped = match_run_articles(run_dir)
    if args.command == "candidates":
        print(json.dumps({"run_dir": str(run_dir), "count": len(candidates), "articles": candidates, "skipped": skipped}, ensure_ascii=False, indent=2))
        return
    if args.command == "matches":
        print(json.dumps(select_matches(run_dir), ensure_ascii=False, indent=2))
        return
    write_report(run_dir, Path(args.summaries), (run_dir / args.output).resolve())
    print(json.dumps({"run_dir": str(run_dir), "output": str((run_dir / args.output).resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
