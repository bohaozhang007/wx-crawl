"""Shared open-source body download with paid TikHub fallback."""
from html import unescape
from . import cli as crawl
from .content import read_json, validate_article
try:
    from integrations.tikhub import TikHubError
except ModuleNotFoundError:
    from src.integrations.tikhub import TikHubError


def download_body(client, url, title, parent, logger, logs):
    """Each provider writes into a separate directory, avoiding stale fallback HTML."""
    attempts = []
    primary_parent = parent / "primary"
    primary_parent.mkdir(parents=True)
    try:
        _, article_dir = crawl.primary_download(url, title, primary_parent)
        actual = unescape(str(read_json(article_dir / "metadata.json").get("title") or title))
        result = validate_article(article_dir, actual)
        attempts.append({"provider": "wechat-mp-tools", "valid": result.success, "reason": result.reason})
        if result.success:
            return article_dir, actual, attempts
    except Exception as exc:
        attempts.append({"provider": "wechat-mp-tools", "valid": False, "error_type": type(exc).__name__})
    article_dir = parent / "rss"
    article_dir.mkdir()
    try:
        crawl.run_fallback(url, title, article_dir, logger, logs)
        actual = unescape(str(read_json(article_dir / "fallback_metadata.json").get("title") or title))
        result = validate_article(article_dir, actual)
        attempts.append({"provider": "we-mp-rss", "valid": result.success, "reason": result.reason})
        if result.success:
            return article_dir, actual, attempts
    except Exception as exc:
        attempts.append({"provider": "we-mp-rss", "valid": False, "error_type": type(exc).__name__})
    detail = client.fetch_article_detail(url)
    content = detail.get("content") or {}
    actual = unescape(str(content.get("title") or title))
    markup = content.get("content_noencode") or ""
    article_dir = parent / "tikhub"
    article_dir.mkdir()
    (article_dir / "article.html").write_text(markup, encoding="utf-8")
    result = validate_article(article_dir, actual)
    attempts.append({"provider": "tikhub", "valid": result.success, "reason": result.reason})
    if not result.success:
        raise TikHubError("所有正文下载器均未通过完整性校验")
    return article_dir, actual, attempts

