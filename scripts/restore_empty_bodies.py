"""Restore empty content.txt bodies for archive articles (open-source first, TikHub fallback).

Classification:
- image_only: body HTML valid with images but zero extractable text (poster articles).
- restored: text extracted and written to content.txt.
- failed: all providers failed (kept for the blocker report).

Usage:
  .venv/bin/python scripts/restore_empty_bodies.py --dry-run
  .venv/bin/python scripts/restore_empty_bodies.py --limit 3
  .venv/bin/python scripts/restore_empty_bodies.py          # all
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crawler.body_download import download_body
from crawler.content import read_json, text_from_html, validate_article, choose_html
from integrations.tikhub import TikHubClient

ARTICLES_ROOT = ROOT / "results" / "articles"
RESULT_FILE = ROOT / "results" / "research" / "2026-09-08-label-contract-fix" / "body_restore_result.json"


def article_url(article_dir: Path) -> str:
    for name in ("metadata.json", "fallback_metadata.json"):
        value = read_json(article_dir / name).get("url")
        if value:
            return str(value)
    return ""


def collect_empty() -> list[tuple[Path, str]]:
    empty = []
    for account_dir in sorted(ARTICLES_ROOT.iterdir()):
        if not account_dir.is_dir():
            continue
        for article_dir in sorted(account_dir.iterdir()):
            if not article_dir.is_dir():
                continue
            content = article_dir / "content.txt"
            if not content.exists() or content.stat().st_size == 0:
                empty.append((article_dir, article_url(article_dir)))
    return empty


def _img_count(markup: str) -> int:
    return len(re.findall(r"<img[^>]+>", markup))


def restore_one(client, article_dir: Path, url: str, logger: logging.Logger) -> dict:
    title = article_dir.name[20:] if len(article_dir.name) > 20 else article_dir.name
    result = {"dir": str(article_dir), "url": url, "title": title, "ok": False}
    if not url:
        result["reason"] = "no url in metadata"
        return result
    work_parent = article_dir / ".restore_work"
    try:
        downloaded, actual, attempts = download_body(client, url, title, work_parent, logger, Path(article_dir / "tools"))
        validation = validate_article(downloaded, actual)
        html_path = choose_html(downloaded)
        if not validation.success or html_path is None:
            result["reason"] = f"validation failed: {validation.reason}"
            result["attempts"] = attempts
            return result
        markup = html_path.read_text(encoding="utf-8", errors="replace")
        text = text_from_html(markup)
        imgs = _img_count(markup)
        if not text.strip():
            if imgs:
                result.update(ok=False, classification="image_only", image_count=imgs, actual=actual, attempts=attempts,
                              reason="valid HTML with images but no extractable text (poster article)")
            else:
                result.update(ok=False, classification="no_text_no_img", actual=actual, attempts=attempts,
                              reason="valid HTML but neither text nor images")
            return result
        # write body files back into the canonical article dir (preserve label.json etc.)
        (article_dir / "content.txt").write_text(text, encoding="utf-8")
        dest_html = article_dir / f"{article_dir.name}.html"
        dest_html.write_text(markup, encoding="utf-8")
        metadata = read_json(article_dir / "metadata.json")
        metadata.update(title=actual or metadata.get("title"), url=url,
                        body_provider=attempts[-1]["provider"] if attempts else "unknown",
                        body_restored_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        (article_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        result.update(ok=True, classification="restored", text_len=len(text), image_count=imgs, actual=actual, attempts=attempts)
        return result
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return result
    finally:
        shutil.rmtree(work_parent, ignore_errors=True)
        shutil.rmtree(article_dir / "tools", ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logger = logging.getLogger("restore")

    empty = collect_empty()
    print(f"empty-content articles: {len(empty)}")
    if args.dry_run:
        for article_dir, url in empty:
            print(f"  {article_dir.name[:60]:<62} url={url[:60]}")
        return 0

    targets = empty if not args.limit else empty[: args.limit]
    client = TikHubClient(max_requests=max(10, len(targets) * 3))
    results = []
    for index, (article_dir, url) in enumerate(targets, 1):
        result = restore_one(client, article_dir, url, logger)
        status = result.get("classification", "OK" if result["ok"] else "FAIL")
        print(f"[{index}/{len(targets)}] {status:<12} {article_dir.name[:50]} :: {result.get('reason', '')[:80]}")
        results.append(result)
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    restored = sum(1 for r in results if r.get("classification") == "restored")
    image_only = sum(1 for r in results if r.get("classification") == "image_only")
    failed = sum(1 for r in results if not r["ok"] and r.get("classification") in (None, "no_text_no_img"))
    print(f"\nrestored: {restored} | image_only: {image_only} | failed: {failed} / {len(targets)}")
    print(f"result file: {RESULT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
