import logging
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

if "lxml" not in sys.modules:
    try:
        import lxml  # noqa: F401
    except ModuleNotFoundError:
        lxml_module = types.ModuleType("lxml")
        etree_module = types.ModuleType("lxml.etree")
        html_module = types.ModuleType("lxml.html")
        etree_module.ParserError = ValueError
        html_module.fromstring = lambda markup: None
        lxml_module.etree = etree_module
        lxml_module.html = html_module
        sys.modules["lxml"] = lxml_module
        sys.modules["lxml.etree"] = etree_module
        sys.modules["lxml.html"] = html_module

from src.crawler import cli


class AuthenticationGateTest(unittest.TestCase):
    def test_account_pool_error_is_authentication_required(self):
        self.assertTrue(cli.is_authentication_error(
            "账号池中无可用账号，请先在『账号池』页面添加/登录账号"
        ))
        self.assertTrue(cli.is_authentication_error("session expired"))
        self.assertFalse(cli.is_authentication_error("网络请求超时"))

    def test_refresh_registered_accounts_does_not_downgrade_auth_error_to_warning(self):
        api = Mock()
        api.request.side_effect = RuntimeError("账号池中无可用账号，请先在『账号池』页面添加/登录账号")
        account = cli.RegisteredAccount(1, "mp1", "测试号", "https://mp.weixin.qq.com/s/x")
        with self.assertRaises(cli.AuthenticationRequiredError):
            cli.refresh_registered_accounts(api, [account], {}, logging.getLogger("test"))


class IncrementalLookbackTest(unittest.TestCase):
    def config(self) -> cli.CrawlConfig:
        return cli.CrawlConfig(
            input_csv=Path("input.csv"),
            mode="incremental",
            articles_per_account=10,
            incremental_max_days=1,
        )

    def test_publish_time_parses_to_beijing_time(self):
        published_at = cli.publish_datetime(1786007756)
        self.assertIsNotNone(published_at)
        assert published_at is not None
        self.assertEqual(published_at.tzinfo, cli.LOCAL_TIMEZONE)
        self.assertEqual(
            published_at.strftime("%Y-%m-%d %H:%M:%S %z"),
            "2026-08-06 17:15:56 +0800",
        )
        self.assertEqual(cli.timestamp_seconds("1786007756000"), 1786007756)
        self.assertEqual(cli.extract_source_publish_time('var ct = "1786007756";'), 1786007756)
        self.assertEqual(
            cli.extract_source_publish_time('var publish_time = "1786007756000";'),
            1786007756,
        )

    def test_incremental_lookback_boundary_uses_publish_time_in_beijing(self):
        now = datetime(2026, 8, 14, 0, 6, 15, tzinfo=cli.LOCAL_TIMEZONE)
        after_cutoff = int(
            datetime(2026, 8, 13, 0, 6, 16, tzinfo=cli.LOCAL_TIMEZONE).timestamp()
        )
        at_cutoff = int(
            datetime(2026, 8, 13, 0, 6, 15, tzinfo=cli.LOCAL_TIMEZONE).timestamp()
        )
        before_cutoff = int(
            datetime(2026, 8, 13, 0, 6, 14, tzinfo=cli.LOCAL_TIMEZONE).timestamp()
        )
        page = [
            {
                "link": "https://mp.weixin.qq.com/s/after",
                "title": "after",
                "publish_time": str(after_cutoff),
            },
            {
                "link": "https://mp.weixin.qq.com/s/equal",
                "title": "equal",
                "publish_time": at_cutoff * 1000,
            },
            {
                "link": "https://mp.weixin.qq.com/s/before",
                "title": "before",
                "publish_time": before_cutoff,
            },
        ]
        with patch.object(cli, "fetch_nonempty_history_page", side_effect=[page]):
            candidates = cli.collect_history_candidates(
                Mock(),
                "fakeid",
                self.config(),
                set(),
                {},
                logging.getLogger("test"),
                now=now,
            )

        self.assertEqual([item[2]["title"] for item in candidates], ["after", "equal"])

    def test_incremental_lookback_can_read_existing_article_publish_time(self):
        publish_time = int(
            datetime(2026, 8, 13, 0, 6, 16, tzinfo=cli.LOCAL_TIMEZONE).timestamp()
        )
        with tempfile.TemporaryDirectory() as temporary_dir:
            account_dir = Path(temporary_dir) / "1_MP_WXS_1_Test"
            article_dir = account_dir / "2026_08_13_00_06_16_existing"
            article_dir.mkdir(parents=True)
            (article_dir / "data.json").write_text(
                (
                    '{"url":"https://mp.weixin.qq.com/s/existing",'
                    f'"title":"existing","publish_time":{publish_time}}}'
                ),
                encoding="utf-8",
            )
            existing_articles = cli.existing_article_index(account_dir)
            existing_key = cli.article_url_key("https://mp.weixin.qq.com/s/existing")
            assert existing_key is not None
            published_at = cli.article_publish_datetime(
                {"link": "https://mp.weixin.qq.com/s/existing"},
                existing_articles[existing_key],
            )

        self.assertIsNotNone(published_at)
        assert published_at is not None
        self.assertEqual(
            published_at.strftime("%Y-%m-%d %H:%M:%S %z"),
            "2026-08-13 00:06:16 +0800",
        )

    def test_incremental_lookback_can_read_source_publish_time(self):
        now = datetime(2026, 8, 14, 0, 6, 15, tzinfo=cli.LOCAL_TIMEZONE)
        after_cutoff = int(
            datetime(2026, 8, 13, 0, 6, 16, tzinfo=cli.LOCAL_TIMEZONE).timestamp()
        )
        page = [
            {
                "link": "https://mp.weixin.qq.com/s/source",
                "title": "source",
            }
        ]
        with patch.object(cli, "fetch_nonempty_history_page", side_effect=[page, []]), patch.object(
            cli, "source_publication_timestamp", return_value=after_cutoff
        ):
            candidates = cli.collect_history_candidates(
                Mock(),
                "fakeid",
                self.config(),
                set(),
                {},
                logging.getLogger("test"),
                now=now,
            )

        self.assertEqual([item[2]["title"] for item in candidates], ["source"])


if __name__ == "__main__":
    unittest.main()
