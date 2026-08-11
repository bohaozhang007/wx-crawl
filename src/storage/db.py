from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTICLES_ROOT = REPO_ROOT / "results" / "articles"
DEFAULT_DB_PATH = REPO_ROOT / "results" / "articles.sqlite3"
SELECTOR_SCRIPT = REPO_ROOT / "skill" / "wechat-crawl-label-report" / "scripts" / "select_articles.py"
APPLICATION_TYPES = ("科研项目申请", "科研指南申请")
DOMAINS = ("无人机", "卫星", "具身智能", "大模型", "空天", "机器人", "机械臂")

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
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1')"
        )


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
    run_id, raw_articles = report_articles(report_path)
    if not run_id.strip():
        raise StorageError("report run_id is empty")
    articles = [validate_article(item) for item in raw_articles]
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
            else:
                inserted += 1
        connection.execute(
            "UPDATE crawl_runs SET imported_count = ?, status = 'imported' WHERE run_id = ?",
            (len(articles), run_id),
        )
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
    completed = subprocess.run(
        ["python3", str(SELECTOR_SCRIPT), command, "--run-dir", str(run_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StorageError(f"selector returned invalid JSON: {exc}") from exc


def prune_run(
    run_dir: Path,
    report_path: Path,
    confirm: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    report_path = Path(report_path).resolve()
    _, selected_items = report_articles(report_path)
    selected = {str(article_dir_path(item.get("article_dir"))) for item in selected_items}
    candidates_payload = run_selector(run_dir, "candidates")
    candidates = [article_dir_path(item.get("article_dir")) for item in candidates_payload.get("articles", [])]
    unselected = [path for path in candidates if str(path) not in selected]
    if not confirm:
        return {"deleted": 0, "would_delete": [str(path) for path in unselected], "confirmed": False}
    deleted: list[str] = []
    for path in unselected:
        shutil.rmtree(path)
        deleted.append(str(path))
    with connect(db_path) as connection:
        connection.execute(
            "UPDATE crawl_runs SET deleted_count = ?, status = 'imported_and_pruned' WHERE run_id = ?",
            (len(deleted), run_dir.name),
        )
    return {"deleted": len(deleted), "paths": deleted, "confirmed": True}


def prune_all_unselected(db_path: Path = DEFAULT_DB_PATH, confirm: bool = False) -> dict[str, Any]:
    with connect(db_path) as connection:
        kept_urls = {row[0] for row in connection.execute("SELECT url FROM articles")}
    if confirm and not kept_urls:
        raise StorageError("refusing all-unselected cleanup because the database has no articles")
    unselected: list[Path] = []
    skipped: list[dict[str, str]] = []
    for article_dir in all_article_directories():
        value = metadata(article_dir)
        url = str(value.get("url") or "").strip()
        if not is_wechat_url(url):
            skipped.append({"article_dir": str(article_dir), "reason": "valid WeChat URL is missing"})
            continue
        if url not in kept_urls:
            unselected.append(article_dir)
    if not confirm:
        return {
            "deleted": 0,
            "would_delete": [str(path) for path in unselected],
            "skipped": skipped,
            "confirmed": False,
        }
    deleted: list[str] = []
    for path in unselected:
        shutil.rmtree(path)
        deleted.append(str(path))
    return {"deleted": len(deleted), "paths": deleted, "skipped": skipped, "confirmed": True}


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
