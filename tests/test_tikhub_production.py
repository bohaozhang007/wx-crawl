import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.crawler import cli
from src.crawler.tikhub_pipeline import HistoryPages
from src.integrations.tikhub import TikHubError


class ProductionTests(unittest.TestCase):
    def test_short_and_empty_pages_use_cursor_until_end(self):
        client = Mock()
        client.fetch_account_articles.side_effect = [
            {'articles': [{'url': 'a', 'create_time': 10}], 'is_end': 0, 'next_offset': 'a+/='},
            {'articles': [], 'is_end': 0, 'next_offset': 'b+/='},
            {'articles': [{'url': 'b'}], 'is_end': 1},
        ]
        pages = HistoryPages(client, 'gh_test')
        self.assertEqual(pages(0)[0]['link'], 'a')
        self.assertEqual(pages(10)[0]['link'], 'b')
        self.assertEqual(pages(20), [])
        self.assertEqual([c.args[1] for c in client.fetch_account_articles.call_args_list], ['', 'a+/=', 'b+/='])

    def test_outer_retry_does_not_repeat_paid_request(self):
        client = Mock()
        client.fetch_account_articles.side_effect = TikHubError('402')
        pages = HistoryPages(client, 'gh_test')
        for _ in range(3):
            with self.assertRaises(TikHubError):
                pages(0)
        self.assertEqual(client.fetch_account_articles.call_count, 1)

    def test_tikhub_dispatch_never_starts_legacy_service_even_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = cli.CrawlConfig(root/'input.csv', 'incremental', 1, history_backend='tikhub')
            report = cli.RunReport(config)
            with patch.object(cli, 'RESULTS_ROOT', root), patch.object(cli, 'ARTICLES_ROOT', root/'articles'), patch.object(cli, 'RECORD_ROOT', root/'record'), patch.object(cli, 'ManagedService') as legacy, patch('src.crawler.tikhub_pipeline.execute_tikhub', side_effect=TikHubError('402')):
                with self.assertRaises(TikHubError):
                    cli.execute(config, [], report, logging.getLogger('test'), root/'logs')
                legacy.assert_not_called()
                self.assertFalse((root/'.working'/report.stamp).exists())

class IdentityRecoveryTests(unittest.TestCase):
    def client(self):
        from src.integrations.tikhub import TikHubClient
        client = object.__new__(TikHubClient)
        client.prefetched_pages = {}
        client.identity_events = []
        client.cache_username = Mock()
        return client

    def test_search_verifies_biz_not_just_name_and_prefetches_page(self):
        import base64
        c = self.client()
        a = cli.RegisteredAccount(1, 'MP_WXS_123', '同名账号', 'url')
        c.search_accounts = Mock(return_value={'items': [
            {'jumpInfo': {'nickName': a.name, 'userName': 'gh_wrong'}},
            {'jumpInfo': {'nickName': a.name, 'userName': 'gh_right'}},
        ]})
        def page(biz):
            return {'articles': [{'url': 'https://mp.weixin.qq.com/s?__biz='+base64.b64encode(str(biz).encode()).decode()}]}
        c.fetch_account_articles = Mock(side_effect=[page(999), page(123)])
        self.assertEqual(c.resolve_by_search(a), 'gh_right')
        c.cache_username.assert_called_once_with(a.mp_id, 'gh_right')
        self.assertIn('gh_right', c.prefetched_pages)

    def test_wrong_biz_is_never_cached(self):
        c=self.client()
        a=cli.RegisteredAccount(1,'MP_WXS_123','同名账号','url')
        c.search_accounts=Mock(return_value={'items':[{'jumpInfo':{'nickName':a.name,'userName':'gh_wrong'}}]})
        c.fetch_account_articles=Mock(return_value={'articles':[{'url':'https://mp.weixin.qq.com/s?__biz=OTk5'}]})
        with self.assertRaises(TikHubError):c.resolve_by_search(a)
        c.cache_username.assert_not_called()

    def test_seed_400_uses_archived_article_but_402_stops(self):
        from src.integrations.tikhub import TikHubHTTPError
        c=self.client()
        a=cli.RegisteredAccount(1,'MP_WXS_123','账号','https://mp.weixin.qq.com/s/seed')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'article').mkdir()
            (root/'article/metadata.json').write_text('{"url":"https://mp.weixin.qq.com/s/alternate"}')
            with patch('src.integrations.tikhub.ROOT',root), patch('src.crawler.cli.account_directory',return_value=root):
                c.fetch_article_detail=Mock(side_effect=[TikHubHTTPError('fetch_article_detail',400),{'bizUin':123,'content':{'user_name':'gh_valid'}}])
                self.assertEqual(c.resolve_username(a),'gh_valid')
                self.assertEqual(c.fetch_article_detail.call_count,2)
                c.fetch_article_detail=Mock(side_effect=TikHubHTTPError('fetch_article_detail',402))
                with self.assertRaises(TikHubHTTPError):c.resolve_username(a)
                self.assertEqual(c.fetch_article_detail.call_count,1)


if __name__ == '__main__':
    unittest.main()
