#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


REPO_ROOT = Path("/root/workspace/wx-crawl")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.labeling.schema import read_label  # noqa: E402

ARTICLES_ROOT = REPO_ROOT / "results" / "articles"
RECORD_ROOT = REPO_ROOT / "results" / "record"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
LOG_PATH_NAME = "article_label_export.log"
LOGGER = logging.getLogger("article-label-export")


def setup_logging(run_dir: Path) -> None:
    LOGGER.setLevel(logging.INFO)
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    LOGGER.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    LOGGER.addHandler(console)

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(run_dir / LOG_PATH_NAME, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError as exc:
        LOGGER.warning("无法写入流程日志文件 %s: %s", run_dir / LOG_PATH_NAME, exc)


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
    LOGGER.info("开始匹配本轮文章 run_dir=%s", run_dir)
    LOGGER.info("扫描文章归档目录 articles_root=%s", ARTICLES_ROOT)
    indexed: dict[tuple[str, str, str], list[Path]] = {}
    by_account_title: dict[tuple[str, str], list[Path]] = {}
    archive_dirs = article_directories()
    LOGGER.info("文章归档扫描完成 article_dirs=%d", len(archive_dirs))
    for article_dir in archive_dirs:
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
    rows = load_run_rows(run_dir)
    LOGGER.info("读取运行文章列表完成 rows=%d csv=%s", len(rows), run_dir / "article_details.csv")
    for index, row in enumerate(rows, start=1):
        account_name = str(row.get("公众号名称") or "").strip()
        title = str(row.get("爬取的文章名称") or "").strip()
        publish_time = str(row.get("文章发布时间") or "").strip()
        paths = indexed.get((account_name, title, publish_time), [])
        if not paths:
            paths = by_account_title.get((account_name, title), [])
        if not paths:
            skipped.append({"account": account_name, "title": title, "reason": "article directory not found"})
            LOGGER.warning("匹配文章 [%d/%d] 未找到目录 account=%s title=%s", index, len(rows), account_name, title)
            continue
        for article_dir in paths[:1]:
            if str(article_dir) in seen_paths:
                LOGGER.info("匹配文章 [%d/%d] 已跳过重复目录 title=%s path=%s", index, len(rows), title, article_dir)
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
            LOGGER.info("匹配文章 [%d/%d] 已找到 title=%s path=%s", index, len(rows), title, article_dir)
    LOGGER.info("本轮文章匹配完成 matched=%d skipped=%d", len(articles), len(skipped))
    return articles, skipped


def valid_label(article_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    label_path = article_dir / "label.json"
    if not label_path.is_file():
        return None, "label.json is missing"
    label, errors = read_label(label_path)
    if errors:
        return None, "; ".join(errors)
    return label, None


def select_matches(run_dir: Path) -> dict[str, Any]:
    LOGGER.info("开始筛选命中文章 run_dir=%s", run_dir)
    candidates, skipped = match_run_articles(run_dir)
    matches: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        label, reason = valid_label(Path(candidate["article_dir"]))
        entry: dict[str, Any] = {
            "account": account_name_from_dir(Path(candidate["article_dir"]).parent),
            "title": candidate["title"],
            "url": candidate["url"],
            "article_dir": candidate["article_dir"],
            "publish_time": candidate["publish_time"],
            "application_type": label["application_type"] if label else None,
            "domains": list(label["domains"]) if label else None,
            "decision": label["decision"] if label else "PENDING",
            "decision_path": list(label["decision_path"]) if label else None,
            "reason_code": label["reason_code"] if label else None,
            "reason": label["reason"] if label else reason,
            "evidence": list(label["evidence"]) if label else None,
        }
        if reason:
            entry["selection"] = "pending"
            skipped.append({"title": candidate["title"], "reason": reason})
            LOGGER.warning("筛选文章 [%d/%d] 跳过 title=%s reason=%s", index, len(candidates), candidate["title"], reason)
            ledger.append(entry)
            continue
        if not is_wechat_article_url(candidate["url"]):
            entry["selection"] = "skipped"
            entry["selection_reason"] = "valid WeChat article URL is missing"
            skipped.append({"title": candidate["title"], "reason": entry["selection_reason"]})
            LOGGER.warning("筛选文章 [%d/%d] 跳过 title=%s reason=%s", index, len(candidates), candidate["title"], entry["selection_reason"])
            ledger.append(entry)
            continue
        if label["decision"] != "KEEP":
            entry["selection"] = "review" if label["decision"] == "REVIEW" else "skipped"
            skipped.append(
                {
                    "title": candidate["title"],
                    "decision": label["decision"],
                    "reason_code": label["reason_code"],
                    "reason": label["reason"],
                }
            )
            LOGGER.info(
                "筛选文章 [%d/%d] 未入选 title=%s decision=%s reason_code=%s",
                index,
                len(candidates),
                candidate["title"],
                label["decision"],
                label["reason_code"],
            )
            ledger.append(entry)
            continue
        entry["selection"] = "selected"
        matches.append(
            {
                **candidate,
                "decision": label["decision"],
                "decision_path": label["decision_path"],
                "reason_code": label["reason_code"],
                "reason": label["reason"],
                "evidence": label["evidence"],
                "application_type": label["application_type"],
                "domains": label["domains"],
            }
        )
        ledger.append(entry)
        LOGGER.info("筛选文章 [%d/%d] 已入选 title=%s application_type=%s domains=%s", index, len(candidates), candidate["title"], label["application_type"], label["domains"])
    for item in skipped:
        if isinstance(item, dict) and "article_dir" not in item and "reason" in item and item["reason"] == "article directory not found":
            ledger.append(
                {
                    "account": item.get("account", ""),
                    "title": item.get("title", ""),
                    "url": "",
                    "article_dir": "",
                    "publish_time": "",
                    "application_type": None,
                    "domains": None,
                    "decision": "PENDING",
                    "decision_path": None,
                    "reason_code": None,
                    "reason": "article directory not found",
                    "evidence": None,
                    "selection": "pending",
                }
            )
    matches.sort(key=lambda item: (item["publish_time"], item["title"]), reverse=True)
    write_ledger(run_dir, ledger, len(ledger), len(matches))
    LOGGER.info(
        "命中文章筛选完成 matched_candidates=%d ledger_entries=%d matches=%d skipped=%d",
        len(candidates),
        len(ledger),
        len(matches),
        len(skipped),
    )
    return {"run_dir": str(run_dir), "count": len(matches), "articles": matches, "skipped": skipped}


def write_ledger(run_dir: Path, ledger: list[dict[str, Any]], candidates: int, selected: int) -> None:
    """Persist a durable per-article audit ledger before any prune happens.

    The ledger records the real label and screening decision for every
    candidate of this run. It must be written during the matches stage, while
    article directories and label.json files still exist, so later pruning
    cannot erase the audit trail.
    """
    output_path = run_dir / "labeling_ledger.json"
    payload = {
        "schema_version": 2,
        "run_id": run_dir.name,
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds"),
        "stage": "matches",
        "candidates": candidates,
        "selected": selected,
        "keep": sum(item.get("decision") == "KEEP" for item in ledger),
        "drop": sum(item.get("decision") == "DROP" for item in ledger),
        "review": sum(item.get("decision") == "REVIEW" for item in ledger),
        "pending": sum(item.get("decision") == "PENDING" for item in ledger),
        "not_selected": len(ledger) - selected,
        "entries": ledger,
    }
    write_json_atomic(payload, output_path)
    LOGGER.info("标注审计台账写入完成 output=%s entries=%d", output_path, len(ledger))


def load_summaries(path: Path) -> dict[str, str]:
    LOGGER.info("读取文章摘要 summaries=%s", path)
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
    LOGGER.info("文章摘要读取完成 summaries=%d", len(summaries))
    return summaries


def write_report(run_dir: Path, summaries_path: Path, output_path: Path) -> None:
    LOGGER.info("开始生成筛选报告 run_dir=%s summaries=%s output=%s", run_dir, summaries_path, output_path)
    selected = select_matches(run_dir)
    summaries = load_summaries(summaries_path)
    articles: list[dict[str, Any]] = []
    missing: list[str] = []
    for index, article in enumerate(selected["articles"], start=1):
        summary = (
            summaries.get(article["url"])
            or summaries.get(article["article_dir"])
            or summaries.get(article["title"])
        )
        if not summary:
            missing.append(article["title"])
            LOGGER.warning("报告文章 [%d/%d] 缺少摘要 title=%s", index, len(selected["articles"]), article["title"])
            continue
        articles.append({**article, "summary": summary})
        LOGGER.info("报告文章 [%d/%d] 已加入 title=%s", index, len(selected["articles"]), article["title"])
    if missing:
        LOGGER.error("筛选报告生成失败 missing_summaries=%d", len(missing))
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
    LOGGER.info("筛选报告生成完成 output=%s articles=%d skipped=%d", output_path, len(articles), len(selected["skipped"]))


def processing_status(run_dir: Path) -> dict[str, Any]:
    """Describe this run's processing state without changing any files."""
    LOGGER.info("开始检查处理状态 run_dir=%s", run_dir)
    candidates, unmatched = match_run_articles(run_dir)
    selected = select_matches(run_dir)
    selected_paths = {item["article_dir"] for item in selected["articles"]}
    summaries_path = run_dir / "article_summaries.json"
    summaries: dict[str, str] = {}
    summaries_error = ""
    if summaries_path.is_file():
        try:
            summaries = load_summaries(summaries_path)
        except SystemExit as exc:
            summaries_error = str(exc)

    articles: list[dict[str, Any]] = []
    for candidate in candidates:
        article_dir = Path(candidate["article_dir"])
        label, label_error = valid_label(article_dir)
        is_selected = candidate["article_dir"] in selected_paths
        has_summary = bool(
            summaries.get(candidate["url"])
            or summaries.get(candidate["article_dir"])
            or summaries.get(candidate["title"])
        )
        if label_error:
            state = "待打标"
        elif label["decision"] == "REVIEW":
            state = "待人工复核"
        elif label["decision"] == "KEEP" and is_selected and not has_summary:
            state = "待摘要"
        elif label["decision"] == "KEEP" and is_selected:
            state = "已筛选"
        else:
            state = "已打标但未入选"
        articles.append(
            {
                "article_dir": candidate["article_dir"],
                "title": candidate["title"],
                "url": candidate["url"],
                "label": label,
                "state": state,
            }
        )

    report_path = run_dir / "filtered_articles.json"
    report_state = "已生成" if report_path.is_file() else "未生成"
    status = {
        "run_dir": str(run_dir),
        "input_count": len(candidates),
        "pending_label_count": sum(item["state"] == "待打标" for item in articles),
        "review_count": sum(item["state"] == "待人工复核" for item in articles),
        "selected_count": len(selected["articles"]),
        "summary_file": str(summaries_path),
        "summary_state": "已生成" if summaries_path.is_file() and not summaries_error else "未生成",
        "summary_error": summaries_error,
        "report_file": str(report_path),
        "report_state": report_state,
        "articles": articles,
        "unmatched": unmatched,
        "skipped": selected["skipped"],
    }
    LOGGER.info(
        "处理状态检查完成 input=%d pending_label=%d review=%d selected=%d summary_state=%s report_state=%s unmatched=%d skipped=%d",
        status["input_count"],
        status["pending_label_count"],
        status["review_count"],
        status["selected_count"],
        status["summary_state"],
        status["report_state"],
        len(unmatched),
        len(selected["skipped"]),
    )
    return status


def write_json_atomic(payload: dict[str, Any], output_path: Path) -> None:
    output_path = output_path.resolve()
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False
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
    LOGGER.info("JSON 文件写入完成 output=%s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Select current-run articles with an explicit KEEP decision")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("candidates", "matches"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--run-dir")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-dir")
    status_parser.add_argument("--output", default="processing_status.json")
    report_parser = subparsers.add_parser("write-report", help="persist selected articles and agent summaries")
    report_parser.add_argument("--run-dir")
    report_parser.add_argument("--summaries", required=True, help="JSON object keyed by URL, article_dir, or title")
    report_parser.add_argument("--output", default="filtered_articles.json")
    args = parser.parse_args()
    run_dir = resolve_run_dir(args.run_dir)
    setup_logging(run_dir)
    started = time.monotonic()
    LOGGER.info("开始执行 article-label-export command=%s run_dir=%s", args.command, run_dir)
    try:
        if args.command == "candidates":
            candidates, skipped = match_run_articles(run_dir)
            print(json.dumps({"run_dir": str(run_dir), "count": len(candidates), "articles": candidates, "skipped": skipped}, ensure_ascii=False, indent=2))
            LOGGER.info("候选文章输出完成 count=%d skipped=%d", len(candidates), len(skipped))
            return
        if args.command == "matches":
            payload = select_matches(run_dir)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            LOGGER.info("命中文章输出完成 count=%d skipped=%d", payload["count"], len(payload["skipped"]))
            return
        if args.command == "status":
            output_path = (run_dir / args.output).resolve()
            try:
                output_path.relative_to(run_dir.resolve())
            except ValueError as exc:
                raise SystemExit(f"status output must be inside run directory: {output_path}") from exc
            output_path.parent.mkdir(parents=True, exist_ok=True)
            status = processing_status(run_dir)
            status["status_file"] = str(output_path)
            write_json_atomic(status, output_path)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            LOGGER.info("处理状态输出完成 output=%s", output_path)
            return
        write_report(run_dir, Path(args.summaries), (run_dir / args.output).resolve())
        print(json.dumps({"run_dir": str(run_dir), "output": str((run_dir / args.output).resolve())}, ensure_ascii=False, indent=2))
    except SystemExit as exc:
        LOGGER.error("article-label-export 执行失败 command=%s run_dir=%s error=%s", args.command, run_dir, exc)
        raise
    except Exception as exc:
        LOGGER.exception("article-label-export 执行异常 command=%s run_dir=%s error=%s", args.command, run_dir, exc)
        raise
    finally:
        LOGGER.info("article-label-export 执行结束 command=%s run_dir=%s elapsed=%.1fs", args.command, run_dir, time.monotonic() - started)


if __name__ == "__main__":
    main()
