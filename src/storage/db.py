from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from src.labeling.schema import read_label


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTICLES_ROOT = REPO_ROOT / "results" / "articles"
DEFAULT_DB_PATH = REPO_ROOT / "results" / "articles.sqlite3"
SELECTOR_SCRIPT = REPO_ROOT / "skill" / "article-label-export" / "scripts" / "select_articles.py"
APPLICATION_TYPES = ("科研项目申请", "科研指南申请")
DOMAINS = ("无人机", "卫星", "具身智能", "大模型", "空天", "机器人", "机械臂")
LOGGER = logging.getLogger("article-label-export")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id TEXT PRIMARY KEY,
    started_at INTEGER,
    finished_at INTEGER,
    status TEXT NOT NULL,
    imported_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    account_name TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL DEFAULT '',
    publish_time INTEGER NOT NULL DEFAULT 0,
    application_type TEXT NOT NULL CHECK (application_type IN ('科研项目申请', '科研指南申请')),
    domains_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    crawl_run TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (crawl_run) REFERENCES crawl_runs(run_id)
);

CREATE TABLE IF NOT EXISTS article_domains (
    article_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    PRIMARY KEY (article_id, domain),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    CHECK (domain IN ('无人机', '卫星', '具身智能', '大模型', '空天', '机器人', '机械臂'))
);

CREATE TABLE IF NOT EXISTS deliveries (
    article_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    sent_at INTEGER,
    response TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (article_id, channel),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_articles_publish_time ON articles(publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_articles_application_type ON articles(application_type);
CREATE INDEX IF NOT EXISTS idx_articles_crawl_run ON articles(crawl_run);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries(channel, status);
"""


class StorageError(ValueError):
    """Raised when an import or storage operation cannot be completed safely."""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    LOGGER.info("开始初始化数据库 db=%s", Path(db_path).resolve())
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1')"
        )
    LOGGER.info("数据库初始化完成 db=%s", Path(db_path).resolve())


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read JSON: {path}: {exc}") from exc


def report_articles(report_path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = read_json(report_path)
    if isinstance(payload, list):
        return report_path.parent.name, payload
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise StorageError("filtered report must be a list or an object with an articles array")
    run_id = str(payload.get("run_id") or report_path.parent.name)
    return run_id, payload["articles"]


def is_wechat_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "mp.weixin.qq.com"


def article_dir_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise StorageError("report article_dir is required")
    path = Path(raw).resolve()
    root = ARTICLES_ROOT.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise StorageError(f"article_dir is outside {root}: {path}") from exc
    if len(relative.parts) != 2 or not path.is_dir():
        raise StorageError(f"article_dir is not a two-level article directory: {path}")
    return path


def account_details(article_dir: Path) -> tuple[str, str]:
    parts = article_dir.parent.name.split("_", 4)
    if len(parts) == 5:
        return parts[4], parts[3]
    return article_dir.parent.name, ""


def metadata(article_dir: Path) -> dict[str, Any]:
    for name in ("metadata.json", "data.json", "fallback_metadata.json"):
        path = article_dir / name
        if path.is_file():
            value = read_json(path)
            if isinstance(value, dict):
                return value
    return {}


def all_article_directories() -> list[Path]:
    if not ARTICLES_ROOT.is_dir():
        return []
    result: list[Path] = []
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
                result.append(article_dir)
    return result


def text_content(article_dir: Path) -> str:
    path = article_dir / "content.txt"
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StorageError(f"cannot read article text: {path}: {exc}") from exc


def validate_article(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise StorageError("each report article must be a JSON object")
    article_dir = article_dir_path(item.get("article_dir"))
    metadata_value = metadata(article_dir)
    title = str(item.get("title") or metadata_value.get("title") or "").strip()
    url = str(item.get("url") or metadata_value.get("url") or "").strip()
    if not title:
        raise StorageError(f"article title is missing: {article_dir}")
    if not is_wechat_url(url):
        raise StorageError(f"article URL is not a WeChat article URL: {url}")
    application_type = item.get("application_type")
    if application_type not in APPLICATION_TYPES:
        raise StorageError(f"invalid application_type for {url}: {application_type}")
    domains = item.get("domains")
    if not isinstance(domains, list) or not domains:
        raise StorageError(f"at least one domain is required for {url}")
    if (
        any(not isinstance(domain, str) or domain not in DOMAINS for domain in domains)
        or len(domains) != len(set(domains))
    ):
        raise StorageError(f"invalid domains for {url}: {domains}")
    canonical_domains = [domain for domain in DOMAINS if domain in domains]
    if domains != canonical_domains:
        raise StorageError(f"domains are not in canonical order for {url}")
    summary = item.get("summary")
    if not isinstance(summary, str):
        raise StorageError(f"summary is required for {url}")
    account_name, account_id = account_details(article_dir)
    publish_time = item.get("publish_time") or metadata_value.get("publish_time") or 0
    try:
        publish_time = int(publish_time or 0)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"invalid publish_time for {url}: {publish_time}") from exc
    return {
        "article_dir": article_dir,
        "title": title,
        "url": url,
        "account_name": account_name,
        "account_id": account_id,
        "publish_time": publish_time,
        "application_type": application_type,
        "domains": canonical_domains,
        "summary": summary.strip(),
        "content_text": text_content(article_dir),
        "cover_url": str(item.get("cover_url") or metadata_value.get("cover_url") or "").strip(),
    }


def import_report(report_path: Path, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    report_path = Path(report_path).resolve()
    LOGGER.info("开始导入筛选报告 report=%s db=%s", report_path, Path(db_path).resolve())
    run_id, raw_articles = report_articles(report_path)
    if not run_id.strip():
        raise StorageError("report run_id is empty")
    LOGGER.info("筛选报告读取完成 run_id=%s raw_articles=%d", run_id, len(raw_articles))
    articles: list[dict[str, Any]] = []
    for index, item in enumerate(raw_articles, start=1):
        article = validate_article(item)
        articles.append(article)
        LOGGER.info("导入校验 [%d/%d] 已通过 title=%s url=%s", index, len(raw_articles), article["title"], article["url"])
    urls = [article["url"] for article in articles]
    if len(urls) != len(set(urls)):
        raise StorageError("filtered report contains duplicate article URLs")
    now = int(time.time())
    init_database(db_path)
    inserted = 0
    updated = 0
    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO crawl_runs(run_id, started_at, finished_at, status, created_at)
               VALUES (?, NULL, ?, 'imported', ?)
               ON CONFLICT(run_id) DO UPDATE SET finished_at=excluded.finished_at,
               status='imported'""",
            (run_id, now, now),
        )
        for article in articles:
            existing = connection.execute(
                "SELECT id FROM articles WHERE url = ?", (article["url"],)
            ).fetchone()
            connection.execute(
                """INSERT INTO articles(
                    url, title, account_name, account_id, publish_time,
                    application_type, domains_json, summary, content_text,
                    cover_url, crawl_run, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    account_name=excluded.account_name,
                    account_id=excluded.account_id,
                    publish_time=excluded.publish_time,
                    application_type=excluded.application_type,
                    domains_json=excluded.domains_json,
                    summary=excluded.summary,
                    content_text=excluded.content_text,
                    cover_url=excluded.cover_url,
                    crawl_run=excluded.crawl_run,
                    updated_at=excluded.updated_at""",
                (
                    article["url"],
                    article["title"],
                    article["account_name"],
                    article["account_id"],
                    article["publish_time"],
                    article["application_type"],
                    json.dumps(article["domains"], ensure_ascii=False),
                    article["summary"],
                    article["content_text"],
                    article["cover_url"],
                    run_id,
                    now,
                    now,
                ),
            )
            article_id = connection.execute(
                "SELECT id FROM articles WHERE url = ?", (article["url"],)
            ).fetchone()[0]
            connection.execute("DELETE FROM article_domains WHERE article_id = ?", (article_id,))
            connection.executemany(
                "INSERT INTO article_domains(article_id, domain) VALUES (?, ?)",
                [(article_id, domain) for domain in article["domains"]],
            )
            connection.execute(
                "INSERT OR IGNORE INTO deliveries(article_id, channel, status) VALUES (?, 'dingtalk', 'pending')",
                (article_id,),
            )
            if existing:
                updated += 1
                LOGGER.info("导入文章 [%d/%d] 已更新 title=%s url=%s", inserted + updated, len(articles), article["title"], article["url"])
            else:
                inserted += 1
                LOGGER.info("导入文章 [%d/%d] 已新增 title=%s url=%s", inserted + updated, len(articles), article["title"], article["url"])
        connection.execute(
            "UPDATE crawl_runs SET imported_count = ?, status = 'imported' WHERE run_id = ?",
            (len(articles), run_id),
        )
    LOGGER.info("筛选报告导入完成 run_id=%s articles=%d inserted=%d updated=%d", run_id, len(articles), inserted, updated)
    return {"run_id": run_id, "articles": len(articles), "inserted": inserted, "updated": updated}


def query_articles(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    domain: str | None = None,
    application_type: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 100,
    channel: str | None = None,
    pending_only: bool = False,
) -> list[dict[str, Any]]:
    if domain and domain not in DOMAINS:
        raise StorageError(f"unknown domain: {domain}")
    if application_type and application_type not in APPLICATION_TYPES:
        raise StorageError(f"unknown application type: {application_type}")
    if limit < 1:
        raise StorageError("limit must be at least 1")
    clauses: list[str] = []
    params: list[Any] = []
    if domain:
        clauses.append("EXISTS (SELECT 1 FROM article_domains ad WHERE ad.article_id = a.id AND ad.domain = ?)")
        params.append(domain)
    if application_type:
        clauses.append("a.application_type = ?")
        params.append(application_type)
    if since is not None:
        clauses.append("a.publish_time >= ?")
        params.append(since)
    if until is not None:
        clauses.append("a.publish_time <= ?")
        params.append(until)
    if pending_only:
        if not channel:
            raise StorageError("channel is required for pending delivery queries")
        clauses.append("EXISTS (SELECT 1 FROM deliveries d WHERE d.article_id = a.id AND d.channel = ? AND d.status = 'pending')")
        params.append(channel)
    query = "SELECT a.* FROM articles a"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY a.publish_time DESC, a.id DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["domains"] = json.loads(item.pop("domains_json"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise StorageError(f"invalid domains_json for article {item.get('id')}") from exc
        result.append(item)
    return result


def mark_delivered(db_path: Path, article_ids: Iterable[int], channel: str, response: str = "") -> int:
    ids = [int(article_id) for article_id in article_ids]
    if not ids:
        raise StorageError("at least one article id is required")
    now = int(time.time())
    try:
        with connect(db_path) as connection:
            count = 0
            for article_id in ids:
                cursor = connection.execute(
                    """INSERT INTO deliveries(article_id, channel, status, sent_at, response)
                       VALUES (?, ?, 'sent', ?, ?)
                       ON CONFLICT(article_id, channel) DO UPDATE SET status='sent', sent_at=excluded.sent_at, response=excluded.response""",
                    (article_id, channel, now, response),
                )
                count += cursor.rowcount
    except sqlite3.IntegrityError as exc:
        raise StorageError(f"cannot mark delivery for unknown article: {ids}") from exc
    return count


def run_selector(run_dir: Path, command: str) -> dict[str, Any]:
    LOGGER.info("调用选择脚本 command=%s run_dir=%s", command, run_dir)
    completed = subprocess.run(
        ["python3", str(SELECTOR_SCRIPT), command, "--run-dir", str(run_dir)],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StorageError(f"selector returned invalid JSON: {exc}") from exc
    details_file = payload.get("details_file") if isinstance(payload, dict) else None
    if isinstance(details_file, str):
        try:
            payload = json.loads(Path(details_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"cannot read selector details for {command}: {details_file}: {exc}"
            ) from exc
    LOGGER.info("选择脚本调用完成 command=%s run_dir=%s", command, run_dir)
    return payload


def require_run_labels(run_dir: Path) -> dict[str, Any]:
    """Refuse destructive cleanup while any candidate article is unlabeled.

    The selector status payload reports ``pending_label_count``; a non-zero
    value means label.json files were never written for this run. Deleting
    those directories would destroy the only copy of crawled articles, so this
    guard aborts instead of pruning.
    """
    status = run_selector(run_dir, "status")
    pending = int(status.get("pending_label_count", 0) or 0)
    if pending:
        raise StorageError(
            f"refusing cleanup for {run_dir.name}: {pending} article(s) still need label.json; label before prune"
        )
    return status


def prune_run(
    run_dir: Path,
    report_path: Path,
    confirm: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    report_path = Path(report_path).resolve()
    LOGGER.info("开始清理本轮未入选文章 run_dir=%s report=%s confirm=%s", run_dir, report_path, confirm)
    status = require_run_labels(run_dir)
    _, selected_items = report_articles(report_path)
    selected = {str(article_dir_path(item.get("article_dir"))) for item in selected_items}
    LOGGER.info("读取已入选文章完成 selected=%d", len(selected))
    candidates_payload = run_selector(run_dir, "candidates")
    candidates = [article_dir_path(item.get("article_dir")) for item in candidates_payload.get("articles", [])]
    decisions = {
        str(item.get("article_dir")): (item.get("label") or {}).get("decision")
        for item in status.get("articles", [])
        if isinstance(item, dict)
    }
    unselected = [
        path
        for path in candidates
        if str(path) not in selected and decisions.get(str(path)) == "DROP"
    ]
    protected = [
        {
            "article_dir": str(path),
            "decision": decisions.get(str(path), "UNKNOWN"),
        }
        for path in candidates
        if str(path) not in selected and decisions.get(str(path)) != "DROP"
    ]
    LOGGER.info(
        "清理范围计算完成 candidates=%d explicit_drop=%d protected=%d",
        len(candidates),
        len(unselected),
        len(protected),
    )
    if not confirm:
        LOGGER.info("清理预览完成 would_delete=%d", len(unselected))
        return {
            "deleted": 0,
            "would_delete": [str(path) for path in unselected],
            "protected": protected,
            "confirmed": False,
        }
    deleted: list[str] = []
    for index, path in enumerate(unselected, start=1):
        LOGGER.info("删除未入选文章目录 [%d/%d] path=%s", index, len(unselected), path)
        shutil.rmtree(path)
        deleted.append(str(path))
    with connect(db_path) as connection:
        connection.execute(
            "UPDATE crawl_runs SET deleted_count = ?, status = ? WHERE run_id = ?",
            (
                len(deleted),
                "imported_and_pruned_review_pending"
                if any(item["decision"] == "REVIEW" for item in protected)
                else "imported_and_pruned",
                run_dir.name,
            ),
        )
    LOGGER.info("本轮未入选文章清理完成 deleted=%d run_id=%s", len(deleted), run_dir.name)
    return {"deleted": len(deleted), "paths": deleted, "protected": protected, "confirmed": True}


def prune_all_unselected(db_path: Path = DEFAULT_DB_PATH, confirm: bool = False) -> dict[str, Any]:
    LOGGER.info("开始全量未入选文章清理 confirm=%s db=%s", confirm, Path(db_path).resolve())
    with connect(db_path) as connection:
        kept_urls = {row[0] for row in connection.execute("SELECT url FROM articles")}
    if confirm and not kept_urls:
        raise StorageError("refusing all-unselected cleanup because the database has no articles")
    unselected: list[Path] = []
    protected: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    article_dirs = all_article_directories()
    invalid_labels: list[dict[str, Any]] = []
    for article_dir in article_dirs:
        label_path = article_dir / "label.json"
        if not label_path.is_file():
            invalid_labels.append({"article_dir": str(article_dir), "errors": ["label.json is missing"]})
            continue
        _, errors = read_label(label_path)
        if errors:
            invalid_labels.append({"article_dir": str(article_dir), "errors": errors})
    if invalid_labels:
        raise StorageError(
            f"refusing all-unselected cleanup: {len(invalid_labels)} article(s) have missing or invalid v2 labels; relabel before prune"
        )
    LOGGER.info("全量文章目录扫描完成 article_dirs=%d kept_urls=%d", len(article_dirs), len(kept_urls))
    for index, article_dir in enumerate(article_dirs, start=1):
        label, errors = read_label(article_dir / "label.json")
        if errors or label is None:
            raise StorageError(f"label became invalid during cleanup scan: {article_dir}")
        if label["decision"] != "DROP":
            protected.append({"article_dir": str(article_dir), "decision": label["decision"]})
            LOGGER.info(
                "全量清理扫描 [%d/%d] 受保护 path=%s decision=%s",
                index,
                len(article_dirs),
                article_dir,
                label["decision"],
            )
            continue
        value = metadata(article_dir)
        url = str(value.get("url") or "").strip()
        if not is_wechat_url(url):
            skipped.append({"article_dir": str(article_dir), "reason": "valid WeChat URL is missing"})
            LOGGER.warning("全量清理扫描 [%d/%d] 跳过 path=%s reason=valid WeChat URL is missing", index, len(article_dirs), article_dir)
            continue
        if url not in kept_urls:
            unselected.append(article_dir)
            LOGGER.info("全量清理扫描 [%d/%d] 待删除 path=%s", index, len(article_dirs), article_dir)
    if not confirm:
        LOGGER.info("全量清理预览完成 would_delete=%d skipped=%d", len(unselected), len(skipped))
        return {
            "deleted": 0,
            "would_delete": [str(path) for path in unselected],
            "protected": protected,
            "skipped": skipped,
            "confirmed": False,
        }
    deleted: list[str] = []
    for index, path in enumerate(unselected, start=1):
        LOGGER.info("全量删除未入选文章目录 [%d/%d] path=%s", index, len(unselected), path)
        shutil.rmtree(path)
        deleted.append(str(path))
    LOGGER.info("全量未入选文章清理完成 deleted=%d skipped=%d", len(deleted), len(skipped))
    return {
        "deleted": len(deleted),
        "paths": deleted,
        "protected": protected,
        "skipped": skipped,
        "confirmed": True,
    }


def stats(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS articles, SUM(application_type = '科研指南申请') AS guides, SUM(application_type = '科研项目申请') AS projects FROM articles"
        ).fetchone()
        domains = connection.execute(
            "SELECT domain, COUNT(*) AS count FROM article_domains GROUP BY domain ORDER BY count DESC, domain"
        ).fetchall()
    return {
        "articles": row["articles"],
        "科研指南申请": row["guides"] or 0,
        "科研项目申请": row["projects"] or 0,
        "domains": {item["domain"]: item["count"] for item in domains},
    }
