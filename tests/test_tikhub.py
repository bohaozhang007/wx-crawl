import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import requests

from src.integrations.tikhub import TikHubClient, TikHubError
from src.crawler.tikhub_sample import download_body, finish_pipeline


class TikHubTests(unittest.TestCase):
    @patch.dict(os.environ, {"TIKHUB_API_KEY": "test-secret"})
    def test_billing_cap_and_no_retry_after_timeout(self):
        session = Mock()
        session.post.side_effect = requests.Timeout("Authorization test-secret")
        client = TikHubClient(max_requests=1, session=session)
        with self.assertRaises(TikHubError) as error:
            client.fetch_article_detail("https://mp.weixin.qq.com/s/test")
        self.assertNotIn("test-secret", str(error.exception))
        with self.assertRaisesRegex(TikHubError, "上限"):
            client.fetch_account_articles("gh_test")
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(client.calls[0]["status"], "transport_error")

    @patch.dict(os.environ, {"TIKHUB_API_KEY": "test-secret"})
    def test_cursor_passed_unmodified_and_business_error_sanitized(self):
        session = Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {"code": 402, "message": "test-secret", "data": {}}
        client = TikHubClient(session=session)
        with self.assertRaises(TikHubError) as error:
            client.fetch_account_articles("gh_test", "a+/=")
        self.assertNotIn("test-secret", str(error.exception))
        self.assertEqual(session.post.call_args.kwargs["json"]["offset"], "a+/=")

    def test_valid_free_body_never_calls_paid_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "valid"
            article.mkdir()
            (article / "body.html").write_text('<div id="js_content">' + '正文' * 50 + '</div>')
            client = Mock()
            with patch('src.crawler.tikhub_sample.crawl.primary_download', return_value=({}, article)), patch('src.crawler.tikhub_sample.crawl.run_fallback') as rss:
                result, _, attempts = download_body(client, 'url', 'title', root, Mock(), root)
            self.assertEqual(result, article)
            self.assertEqual(attempts[-1]['provider'], 'wechat-mp-tools')
            rss.assert_not_called()
            client.fetch_article_detail.assert_not_called()

    def test_rss_timeout_still_uses_paid_fallback_with_clean_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = Mock()
            client.fetch_article_detail.return_value = {"content": {"title": "文章", "content_noencode": '<div id="js_content">' + '正文' * 50 + '</div>'}}
            with patch('src.crawler.tikhub_sample.crawl.primary_download', side_effect=RuntimeError()), patch('src.crawler.tikhub_sample.crawl.run_fallback', side_effect=TimeoutError()):
                result, _, attempts = download_body(client, 'url', 'title', root, Mock(), root)
            self.assertEqual(result.name, 'tikhub')
            self.assertEqual([a['valid'] for a in attempts], [False, False, True])
            client.fetch_article_detail.assert_called_once_with('url')

    def test_label_failure_blocks_report_and_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('src.labeling.config.load_labeling_config', return_value=Mock(model='test', provider='test')), patch('src.labeling.model.OpenAILabelModel'), patch('src.labeling.runner.run_labeling', new=AsyncMock(return_value={'failed': 1})), patch('src.crawler.tikhub_sample.command') as command:
                with self.assertRaisesRegex(RuntimeError, '停止筛选入库'):
                    finish_pipeline([root / 'article'], root, {})
                command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
