from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

import yaml

from src.auth.dingtalk_notify import send_deadline_reminders
from src.storage.db import DEFAULT_DB_PATH, init_database


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config.yaml"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE = UNIT_DIR / "wx-crawl-deadline-reminder.service"
TIMER = UNIT_DIR / "wx-crawl-deadline-reminder.timer"
TZ = ZoneInfo("Asia/Shanghai")


def settings() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    value = cfg.get("deadline_reminder") or {}
    return {
        "enabled": bool(value.get("enabled", True)),
        "days_before": int(value.get("days_before", 7)),
        "retry_minutes": int(value.get("retry_minutes", 30)),
        "max_attempts": int(value.get("max_attempts", 3)),
    }


def reconcile_records(db_path: Path, *, now: int | None = None, config: dict | None = None) -> dict:
    now = int(now or time.time())
    cfg = config or settings()
    init_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """UPDATE deadline_reminders SET status='cancelled'
               WHERE status IN ('pending','failed') AND article_id IN (
                 SELECT id FROM articles WHERE deadline_status != 'confirmed'
                    OR deadline_at IS NULL OR deadline_at <= ?
               )""",
            (now,),
        )
        connection.execute(
            """UPDATE deadline_reminders SET status='cancelled'
               WHERE status IN ('pending','failed') AND NOT EXISTS (
                 SELECT 1 FROM articles a WHERE a.id=deadline_reminders.article_id
                   AND a.deadline_at=deadline_reminders.deadline_at
               )"""
        )
        created = 0
        if cfg["enabled"]:
            rows = connection.execute(
                "SELECT id, deadline_at FROM articles WHERE deadline_status='confirmed' AND deadline_at>?",
                (now,),
            ).fetchall()
            for article_id, deadline_at in rows:
                remind_at = max(now + 5, int(deadline_at) - cfg["days_before"] * 86400)
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO deadline_reminders(
                         article_id, deadline_at, remind_at, reminder_days, status
                       ) VALUES (?, ?, ?, ?, 'pending')""",
                    (article_id, deadline_at, remind_at, cfg["days_before"]),
                )
                created += cursor.rowcount
        next_row = connection.execute(
            """SELECT MIN(remind_at) FROM deadline_reminders
               WHERE status IN ('pending','failed') AND attempts < ?""",
            (cfg["max_attempts"],),
        ).fetchone()
    return {"created": created, "next_remind_at": next_row[0] if next_row else None}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def install_next_timer(next_remind_at: int | None) -> str:
    _atomic_write(SERVICE, f"""[Unit]
Description=wx-crawl deadline reminder dispatcher

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={ROOT / '.venv/bin/python'} -m src.reminders.cli dispatch
""")
    if next_remind_at is None:
        subprocess.run(["systemctl", "--user", "disable", "--now", TIMER.name], check=False, capture_output=True)
        return "idle"
    calendar = datetime.fromtimestamp(next_remind_at, TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")
    _atomic_write(TIMER, f"""[Unit]
Description=Next wx-crawl deadline reminder

[Timer]
OnCalendar={calendar}
Persistent=true
AccuracySec=1min
Unit={SERVICE.name}

[Install]
WantedBy=timers.target
""")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", TIMER.name], check=True, capture_output=True)
    subprocess.run(["systemctl", "--user", "restart", TIMER.name], check=True, capture_output=True)
    return calendar


def dispatch(db_path: Path, *, now: int | None = None) -> dict:
    now = int(now or time.time()); cfg = settings(); init_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT r.id reminder_id, r.attempts, r.reminder_days, a.id article_id,
                      a.title, a.url, a.account_name, a.deadline_at, a.deadline_text
               FROM deadline_reminders r JOIN articles a ON a.id=r.article_id
               WHERE r.status IN ('pending','failed') AND r.remind_at<=? AND r.attempts<?
                 AND a.deadline_at>? ORDER BY a.deadline_at, a.id""",
            (now, cfg["max_attempts"], now),
        ).fetchall()
        items = [dict(row) for row in rows]
        if not items:
            return {"due": 0, "sent": 0}
        try:
            response = send_deadline_reminders(items)
        except Exception as exc:
            for item in items:
                attempts = item["attempts"] + 1
                connection.execute(
                    """UPDATE deadline_reminders SET status='failed', attempts=?, remind_at=?, response=? WHERE id=?""",
                    (attempts, now + cfg["retry_minutes"] * 60, str(exc)[:1000], item["reminder_id"]),
                )
            return {"due": len(items), "sent": 0, "failed": len(items), "error": str(exc)[:500]}
        else:
            ids = [item["reminder_id"] for item in items]
            connection.executemany(
                "UPDATE deadline_reminders SET status='sent', attempts=attempts+1, sent_at=?, response=? WHERE id=?",
                [(now, response[:1000], value) for value in ids],
            )
    return {"due": len(items), "sent": len(items)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage one persistent no-agent deadline timer")
    parser.add_argument("command", choices=("reconcile", "dispatch", "status"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    if args.command == "dispatch":
        result = dispatch(args.db)
        state = reconcile_records(args.db)
        result["timer"] = install_next_timer(state["next_remind_at"])
    elif args.command == "reconcile":
        result = reconcile_records(args.db)
        result["timer"] = install_next_timer(result["next_remind_at"])
    else:
        result = reconcile_records(args.db)
    failed = int(result.get("failed", 0) or 0)
    print(json.dumps({"status": "failed" if failed else "ok", "command": args.command, **result}, ensure_ascii=False, separators=(",", ":")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
