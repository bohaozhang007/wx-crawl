"""Bounded TikHub list -> free body -> paid fallback -> label -> SQLite sample."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

from src.integrations.tikhub import TikHubClient, TikHubError
from src.crawler import cli as crawl
from src.crawler.content import article_url_key, normalize_article_url, read_json, text_from_html, validate_article


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


from .body_download import download_body


def command(args, run_dir, name):
    completed = subprocess.run(args, cwd=crawl.ROOT, capture_output=True, text=True)
    (run_dir / f"{name}.log").write_text(completed.stdout + completed.stderr)
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"{name} 未返回 JSON，见运行日志") from None
    if completed.returncode or (isinstance(payload, dict) and payload.get("status") == "failed"):
        raise RuntimeError(f"{name} 失败，见运行日志")
    return payload


def run(args, run_dir, details):
    client = TikHubClient(max_requests=args.max_requests)
    details["calls"] = client.calls
    registry = {a.number: a for a in crawl.load_registry()}
    if any(number not in registry for number in args.account):
        raise RuntimeError("指定的公众号编号不存在")
    accounts = [registry[number] for number in args.account]
    logs = run_dir / "tools-log"
    logs.mkdir()
    logger = logging.getLogger("tikhub-sample")
    logger.addHandler(logging.FileHandler(logs / "crawler.log"))
    logger.setLevel(logging.INFO)
    work = run_dir / ".working"
    work.mkdir()
    rows = []
    article_dirs = []
    # De-duplicate against both the archive and the durable database (which can
    # retain articles whose staging directory has been cleaned up).
    from src.storage.db import DEFAULT_DB_PATH
    database = command([str(crawl.ROOT / "wx-crawl-db"), "list", "--limit", "1000000", "--json"], run_dir, "database-before") if DEFAULT_DB_PATH.exists() else {}
    stored = database if isinstance(database, list) else database.get("articles", [])
    existing = {article_url_key(item.get("url", "")) for item in stored} if isinstance(stored, list) else set()
    crawl.ARTICLES_ROOT.mkdir(parents=True, exist_ok=True)
    for account_dir in crawl.ARTICLES_ROOT.iterdir():
        if account_dir.is_dir():
            existing.update(crawl.existing_article_keys(account_dir))
    try:
        for account in accounts:
            username = client.resolve_username(account)
            offset = ""
            cursors = set()
            candidates = []
            seen = set()
            for page_number in range(args.max_pages):
                page = client.fetch_account_articles(username, offset)
                write_json(run_dir / f"list-{account.number}-{page_number + 1}.json", page)
                for item in page.get("articles", []):
                    if args.title_contains and args.title_contains not in unescape(str(item.get("title") or "")):
                        continue
                    url = normalize_article_url(item.get("url", ""))
                    key = article_url_key(url)
                    if not key or key in existing or key in seen:
                        details["duplicates_skipped"] += 1
                        continue
                    seen.add(key)
                    candidates.append((url, key, item))
                    if len(candidates) >= args.per_account:
                        break
                if len(candidates) >= args.per_account or page.get("is_end"):
                    break
                next_offset = page.get("next_offset")
                if not next_offset or next_offset in cursors:
                    raise TikHubError("历史游标缺失或重复，停止付费翻页")
                cursors.add(next_offset)
                offset = next_offset
            for url, key, item in candidates:
                title = unescape(str(item.get("title") or "未命名"))
                logger.info("下载 %s：%s", account.name, title)
                parent = work / hashlib.sha256(key.encode()).hexdigest()[:16]
                parent.mkdir()
                entry = {"account": account.name, "url": url, "title": title}
                details["articles"].append(entry)
                article_dir, title, attempts = download_body(client, url, title, parent, logger, logs)
                entry["attempts"] = attempts
                validation = validate_article(article_dir, title)
                markup = validation.html_path.read_text(encoding="utf-8")
                text = text_from_html(markup)
                (article_dir / "content.txt").write_text(text, encoding="utf-8")
                timestamp = crawl.timestamp_seconds(item.get("create_time") or item.get("update_time"))
                if not timestamp:
                    raise RuntimeError("列表缺少文章发布时间")
                metadata = read_json(article_dir / "metadata.json")
                metadata.update(title=title, url=url, publish_time=timestamp, account_name=account.name, body_provider=attempts[-1]["provider"])
                write_json(article_dir / "metadata.json", metadata)
                account_dir = crawl.reconcile_account_directory(account)
                account_dir.mkdir(parents=True, exist_ok=True)
                destination = crawl.final_destination(account_dir, timestamp, title, url)
                article_dir.rename(destination)
                try:
                    crawl.archive_attachments(destination, url, timeout_seconds=30, max_bytes=50 * 1024 ** 2)
                except Exception as exc:
                    entry["attachment_error_type"] = type(exc).__name__
                entry.update(title=title, path=str(destination), text_length=validation.text_length, images=validation.image_count)
                article_dirs.append(destination)
                existing.add(key)
                rows.append([account.name, title, crawl.format_publish_time(timestamp)])
                crawl.write_csv_atomic(run_dir / "article_details.csv", ["公众号名称", "爬取的文章名称", "文章发布时间"], rows)
                write_json(run_dir / "sample_result.json", details)
        crawl.write_csv_atomic(run_dir / "article_details.csv", ["公众号名称", "爬取的文章名称", "文章发布时间"], rows)
        if not article_dirs:
            details["status"] = "no_new_articles"
            return
        finish_pipeline(article_dirs, run_dir, details)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def finish_pipeline(article_dirs, run_dir, details):
    # Call the normal model runner, without its CLI's notification side effect.
    from src.labeling.config import load_labeling_config
    from src.labeling.model import OpenAILabelModel
    from src.labeling.runner import run_labeling
    from src.labeling.cli import write_details, write_usage
    config = load_labeling_config()
    for batch in [article_dirs[:1], article_dirs[1:]]:
        if not batch:
            continue
        result = asyncio.run(run_labeling(batch, OpenAILabelModel(config, max_output_tokens=16384), concurrency=config.concurrency, max_retries=config.max_retries, replace=False))
        result.update(model=config.model, provider=config.provider, run_dir=str(run_dir))
        # Retain each phase's usage rather than overwriting the single-article test.
        phase = run_dir / ("label-test-" if batch == article_dirs[:1] else "label-rest-")
        phase = phase.with_name(phase.name + datetime.now().strftime("%H%M%S_%f"))
        phase.mkdir(exist_ok=True)
        write_details(result, phase)
        write_usage(result, phase)
        if result["failed"]:
            raise RuntimeError("打标失败，保留正文与诊断，停止筛选入库")
    selector = str(crawl.ROOT / "skill/article-label-export/scripts/select_articles.py")
    python = str(crawl.ROOT / ".venv/bin/python")
    details["selection"] = command([python, selector, "matches", "--run-dir", str(run_dir)], run_dir, "matches")
    details["report"] = command([python, selector, "write-report", "--run-dir", str(run_dir)], run_dir, "report")
    details["database"] = command([str(crawl.ROOT / "wx-crawl-db"), "ingest", "--run-dir", str(run_dir)], run_dir, "ingest")
    details["status"] = "ok"
    crawl.update_global_summary()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", type=int, action="append", default=[], help="登记表编号，可重复")
    parser.add_argument("--per-account", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--title-contains", default="", help="可选的抽样标题子串；不参与打标决策")
    parser.add_argument("--resume", type=Path, help="只重试既有样本的打标/筛选/入库，不重新下载")
    args = parser.parse_args()
    if not args.account and not args.resume:
        parser.error("provide --account or --resume")
    if args.account and args.resume:
        parser.error("--account and --resume are mutually exclusive")
    if min(args.per_account, args.max_pages, args.max_requests) < 1:
        parser.error("limits must be positive")
    if len(set(args.account)) != len(args.account):
        parser.error("account numbers must be unique")
    run_dir = crawl.RECORD_ROOT / "samples" / datetime.now().strftime("%Y_%m_%d_%H_%M_%S_%f")
    if args.resume:
        run_dir = args.resume.resolve()
        if run_dir.parent != (crawl.RECORD_ROOT / "samples").resolve() or not (run_dir / "sample_result.json").is_file():
            parser.error("--resume must name an existing sample run")
    else:
        run_dir.mkdir(parents=True)
    details = {"status": "running", "run_dir": str(run_dir), "scope": "sample_no_notifications", "parameters": vars(args), "duplicates_skipped": 0, "articles": []}
    if args.resume:
        details = read_json(run_dir / "sample_result.json")
        details["status"] = "running"
        details.pop("error", None)
    try:
        with crawl.single_instance(), (run_dir / "tools-output.log").open("a") as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if args.resume:
                paths = [Path(a["path"]) for a in details["articles"] if a.get("path")]
                if len(paths) != len(details["articles"]) or not paths:
                    raise RuntimeError("采集未完成，不能只恢复打标入库")
                finish_pipeline(paths, run_dir, details)
            else:
                run(args, run_dir, details)
    except Exception as exc:
        details.update(status="failed", error=str(exc) if isinstance(exc, (TikHubError, RuntimeError)) else type(exc).__name__)
    details["estimated_tikhub_usd"] = round(sum(c.get("status") == "success" for c in details.get("calls", [])) * .01, 2)
    write_json(run_dir / "sample_result.json", details)
    print(json.dumps({"status": details["status"], "articles": len(details["articles"]), "requests": len(details.get("calls", [])), "estimated_tikhub_usd": details["estimated_tikhub_usd"], "database": details.get("database"), "details_file": str(run_dir / "sample_result.json")}, ensure_ascii=False))
    return 1 if details["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
