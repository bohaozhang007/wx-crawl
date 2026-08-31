from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.crawler.attachments import discover_attachments
from src.reminders.cli import reconcile_records
from src.storage.db import init_database


class AttachmentTest(unittest.TestCase):
    def test_discovers_file_and_keyword_links_but_not_article_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "article.html").write_text(
                '<div id="js_content"><a href="https://example.com/a.pdf">附件一</a>'
                '<a href="https://example.com/form">下载申请表</a>'
                '<a href="https://mp.weixin.qq.com/s/other">相关文章</a></div>',
                encoding="utf-8",
            )
            values = discover_attachments(root, "https://mp.weixin.qq.com/s/source")
            self.assertEqual([item["source_url"] for item in values], [
                "https://example.com/a.pdf", "https://example.com/form"
            ])


class ReminderTest(unittest.TestCase):
    def test_reconcile_creates_one_exact_reminder_and_cancels_changed_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "articles.sqlite3"
            init_database(database)
            now = 1_800_000_000
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """INSERT INTO crawl_runs(run_id,status,created_at) VALUES('run','imported',?)""",
                    (now,),
                )
                connection.execute(
                    """INSERT INTO articles(url,title,publish_time,application_type,deadline_status,deadline_at,crawl_run,created_at,updated_at)
                       VALUES('https://mp.weixin.qq.com/s/x','项目',0,'科研项目申请','confirmed',?,'run',?,?)""",
                    (now + 10 * 86400, now, now),
                )
            result = reconcile_records(database, now=now, config={"enabled": True, "days_before": 7, "max_attempts": 3})
            self.assertEqual(result["created"], 1)
            self.assertEqual(result["next_remind_at"], now + 3 * 86400)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE articles SET deadline_at=?", (now + 20 * 86400,))
            reconcile_records(database, now=now, config={"enabled": True, "days_before": 7, "max_attempts": 3})
            with sqlite3.connect(database) as connection:
                statuses = [row[0] for row in connection.execute("SELECT status FROM deadline_reminders ORDER BY id")]
            self.assertEqual(statuses, ["cancelled", "pending"])


if __name__ == "__main__":
    unittest.main()
