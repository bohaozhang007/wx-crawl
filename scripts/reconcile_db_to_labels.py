"""Reconcile SQLite rows with current-contract archive labels (geography contract 2026-09-17).

For each DB row:
- source label is current-contract KEEP  -> refresh row fields + label_json from the label
- source label is DROP / REVIEW / missing -> remove the row (local), recorded for remote deletion

Writes a change manifest; never touches the remote table itself.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.labeling.schema import read_label

DB = ROOT / "results" / "articles.sqlite3"
ARTICLES = ROOT / "results" / "articles"
MANIFEST = ROOT / "results" / "research" / "2026-09-16-geography-policy" / "db_reconcile_manifest.json"


def url_of(article_dir: Path) -> str:
    for name in ("metadata.json", "fallback_metadata.json", "data.json"):
        path = article_dir / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("url")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if value:
            return str(value)
    return ""


def build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for account_dir in ARTICLES.iterdir():
        if not account_dir.is_dir():
            continue
        for article_dir in account_dir.iterdir():
            if not article_dir.is_dir():
                continue
            if not (article_dir / "label.json").exists():
                continue
            url = url_of(article_dir)
            if url:
                index[url] = str(article_dir)
    return index


def main() -> int:
    index = build_index()
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    rows = connection.execute("SELECT * FROM articles ORDER BY id").fetchall()

    updates, removals, blocked = [], [], []
    for row in rows:
        article_dir = index.get(row["url"])
        if not article_dir:
            blocked.append({"id": row["id"], "title": row["title"], "reason": "no source article dir"})
            continue
        label, errors = read_label(Path(article_dir) / "label.json")
        if errors or label is None:
            blocked.append({"id": row["id"], "title": row["title"], "reason": "; ".join(errors)[:200]})
            continue
        decision = label.get("decision")
        if decision != "KEEP":
            removals.append({
                "id": row["id"], "url": row["url"], "title": row["title"],
                "decision": decision, "reason_code": label.get("reason_code"),
                "geography": (label.get("geography") or {}).get("status"),
                "article_dir": article_dir,
            })
            continue
        updates.append({"id": row["id"], "url": row["url"], "title": row["title"], "label": label, "article_dir": article_dir})

    now = int(time.time())
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        for item in removals:
            rid = item["id"]
            connection.execute("DELETE FROM article_domains WHERE article_id=?", (rid,))
            connection.execute("DELETE FROM deliveries WHERE article_id=?", (rid,))
            connection.execute("DELETE FROM deadline_reminders WHERE article_id=?", (rid,))
            connection.execute("DELETE FROM articles WHERE id=?", (rid,))
        for item in updates:
            label = item["label"]
            deadline = label.get("deadline") or {}
            importance = label.get("importance") or {}
            domains = list(label.get("domains") or [])
            connection.execute(
                """UPDATE articles SET application_type=?, domains_json=?, summary=?, updated_at=?,
                   deadline_status=?, deadline_text=?, deadline_at=?,
                   importance_level=?, importance_reason=?, importance_factors_json=?, label_json=?
                   WHERE id=?""",
                (
                    label.get("application_type"), json.dumps(domains, ensure_ascii=False), label.get("summary", ""), now,
                    str(deadline.get("status") or "missing"), str(deadline.get("raw_text") or ""),
                    deadline.get("timestamp") if deadline.get("status") == "confirmed" else None,
                    str(importance.get("level") or ""), str(importance.get("reason") or ""),
                    json.dumps(importance.get("factors") or {}, ensure_ascii=False),
                    json.dumps(label, ensure_ascii=False), item["id"],
                ),
            )
            connection.execute("DELETE FROM article_domains WHERE article_id=?", (item["id"],))
            for domain in domains:
                connection.execute("INSERT OR IGNORE INTO article_domains(article_id, domain) VALUES (?,?)", (item["id"], domain))

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": str(DB),
        "rows_before": len(rows),
        "updated": [{"id": i["id"], "title": i["title"], "geography": (i["label"].get("geography") or {}).get("status"),
                     "deadline": (i["label"].get("deadline") or {}).get("status")} for i in updates],
        "removed": removals,
        "blocked": blocked,
        "rows_after": len(rows) - len(removals),
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"rows_before": len(rows), "updated": len(updates), "removed": len(removals),
                      "blocked": len(blocked), "rows_after": len(rows) - len(removals),
                      "manifest": str(MANIFEST)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
