"""Production TikHub history backend; never starts WeRead authentication."""
from __future__ import annotations

import json
import subprocess
from html import unescape

from . import cli as crawl
from .body_download import download_body
from .content import article_url_key, read_json, text_from_html, validate_article
try:
    from integrations.tikhub import TikHubClient, TikHubError
except ModuleNotFoundError:
    from src.integrations.tikhub import TikHubClient, TikHubError


class HistoryPages:
    """Adapt opaque cursors to the existing sequential fetcher; cache failures too."""
    def __init__(self, client, username):
        self.client, self.username = client, username
        self.offset = ""
        self.ended = False
        self.cache = {}
        self.cursors = set()

    def __call__(self, begin):
        if begin in self.cache:
            value = self.cache[begin]
            if isinstance(value, Exception):
                raise value
            return value
        if self.ended:
            return []
        try:
            # Empty pages may still have a next cursor. Never stop by page length.
            while True:
                page = self.client.fetch_account_articles(self.username, self.offset)
                self.ended = bool(page.get("is_end"))
                offset = page.get("next_offset")
                if not self.ended:
                    if not offset or offset in self.cursors:
                        raise TikHubError("TikHub 历史游标缺失或重复")
                    self.cursors.add(offset)
                    self.offset = offset
                items = [{**a, "link": a.get("url", ""), "title": unescape(a.get("title") or ""), "update_time": a.get("create_time") or a.get("update_time")} for a in page.get("articles", [])]
                if items or self.ended:
                    self.cache[begin] = items
                    return items
        except Exception as exc:
            self.cache[begin] = exc
            raise


def database_keys():
    if not (crawl.RESULTS_ROOT / "articles.sqlite3").is_file():
        return set()
    result = subprocess.run([str(crawl.ROOT / "wx-crawl-db"), "list", "--limit", "1000000", "--json"], cwd=crawl.ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("读取 SQLite URL 去重索引失败")
    return {article_url_key(a["url"]) for a in json.loads(result.stdout)}


def execute_tikhub(config, seed_urls, report, work_root, logger, logs):
    report.crawl_backend = "tikhub"
    client = TikHubClient(max_requests=config.tikhub_max_requests)
    failures = []
    body_results = []
    logs.mkdir(parents=True, exist_ok=True)
    try:
        registry = crawl.load_registry()
        known_seeds = {article_url_key(a.sample_url): a for a in registry}
        explicit = {}
        for url in seed_urls:
            known = known_seeds.get(article_url_key(url))
            if known:
                explicit.setdefault(known.mp_id, []).append(url)
                continue
            detail = client.fetch_article_detail(url)
            content = detail.get("content") or {}
            mp_id = f"MP_WXS_{detail.get('bizUin')}"
            if not detail.get("bizUin") or not content.get("user_name", "").startswith("gh_"):
                raise TikHubError("输入文章未返回可验证公众号身份")
            client.cache_username(mp_id, content["user_name"])
            registry, _ = crawl.merge_discovered_accounts(registry, [{"fakeid": mp_id, "nickname": content.get("nick_name"), "sample_url": url}])
            explicit.setdefault(mp_id, []).append(url)
        if not registry:
            raise RuntimeError("没有可处理的公众号")
        crawl.save_registry(registry)
        stored = database_keys()

        def body(url, title, parent):
            article_dir, actual, attempts = download_body(client, url, title, parent, logger, logs)
            validation = validate_article(article_dir, actual)
            (article_dir / "content.txt").write_text(text_from_html(validation.html_path.read_text(encoding="utf-8")), encoding="utf-8")
            metadata = read_json(article_dir / "metadata.json")
            metadata.update(title=actual, url=url, body_provider=attempts[-1]["provider"])
            (article_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            body_results.append({"url": url, "title": actual, "attempts": attempts})
            return metadata, article_dir

        for account in registry:
            timing = report.start_account(account)
            try:
                username = client.resolve_username(account)
                rejected = {article_url_key(e["url"]) for e in client.identity_events if e.get("account") == account.name and e.get("error") == "identity_mismatch"}
                explicit_urls = [url for url in [account.sample_url, *explicit.get(account.mp_id, [])] if article_url_key(url) not in rejected]
                if rejected:
                    logger.warning("%s：身份不匹配的示例链接不归档到该公众号，详情见 identity_events", account.name)
                crawl.crawl_account(None, account, work_root, config, timing, logger, logs,
                    explicit_urls,
                    history_page_fetcher=HistoryPages(client, username), body_downloader=body, stored_keys=stored)
                timing.finish("成功")
            except Exception as exc:
                timing.finish("处理失败")
                failures.append({"account": account.name, "error": str(exc)})
                logger.error("TikHub 公众号处理失败 %s：%s", account.name, exc)
                if len(client.calls) >= client.max_requests:
                    break
        if failures:
            report.history_complete = False
            raise RuntimeError(f"TikHub 爬取有 {len(failures)} 个失败账号；详情见 {logs / 'tikhub.json'}")
    finally:
        (logs / "tikhub.json").write_text(json.dumps({"calls": client.calls, "identity_events": client.identity_events, "body_results": body_results, "failures": failures, "estimated_usd": round(sum(c['status']=='success' for c in client.calls)*.01, 2)}, ensure_ascii=False, indent=2), encoding="utf-8")
