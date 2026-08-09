from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html


ERROR_MARKERS = (
    "当前环境异常",
    "完成验证后即可继续访问",
    "该内容已被发布者删除",
    "内容已被作者删除",
    "该内容暂时无法查看",
    "此内容暂时无法查看",
    "此内容因违规无法查看",
    "违规无法查看",
    "内容审核中",
    "发送失败无法查看",
    "链接已过期",
    "系统出错",
)


@dataclass(frozen=True)
class Validation:
    success: bool
    text_length: int
    image_count: int
    html_path: Path | None
    reason: str = ""


def normalize_article_url(url: str) -> str:
    """Drop tracking fragments while retaining the parameters identifying an article."""
    url = (url or "").strip().replace("&amp;", "&")
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def article_url_key(url: str) -> str:
    """Return a stable WeChat article identity while dropping tracking parameters."""
    normalized = normalize_article_url(url)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    path = parts.path.rstrip("/") or "/"
    if path.startswith("/s/"):
        query = ""
    elif path == "/s":
        identity_keys = {"__biz", "mid", "idx", "sn"}
        identity = [(key, value) for key, value in parse_qsl(parts.query) if key in identity_keys]
        identity.sort()
        query = urlencode(identity)
    else:
        query = parts.query
    return urlunsplit(("https", parts.netloc.lower(), path, query, ""))


def safe_component(value: str, max_length: int = 90) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:max_length].rstrip(" ._") or "未命名"


def _parse(markup: str):
    try:
        return lxml_html.fromstring(markup or "<html><body></body></html>")
    except (etree.ParserError, ValueError):
        return lxml_html.fromstring("<html><body></body></html>")


def _body_node(document):
    selectors = (
        '//*[@id="js_content"]',
        '//*[contains(concat(" ", normalize-space(@class), " "), " rich_media_content ")]',
        '//*[@id="js_article"]',
        "//body",
    )
    for selector in selectors:
        nodes = document.xpath(selector)
        if nodes:
            return nodes[0]
    return document


def text_from_html(markup: str) -> str:
    node = _body_node(_parse(markup))
    for tag in node.xpath('.//script | .//style | .//noscript'):
        parent = tag.getparent()
        if parent is not None:
            parent.remove(tag)
    return "\n".join(
        line.strip() for line in node.text_content().splitlines() if line.strip()
    )


def _valid_image_count(node) -> int:
    count = 0
    for image in node.xpath('.//img'):
        source = (image.get("src") or image.get("data-src") or "").strip()
        if not source:
            continue
        lowered = source.lower()
        if lowered.startswith(
            ("data:image/", "http://", "https://", "//", "media/", "./media/", "../media/")
        ):
            count += 1
    return count


def choose_html(article_dir: Path) -> Path | None:
    fallback = article_dir / "fallback.html"
    if fallback.is_file():
        return fallback
    files = sorted(
        path
        for path in article_dir.glob("*.html")
        if not path.name.endswith("_raw.html")
    )
    return files[0] if files else None


def validate_article(article_dir: Path, title: str = "") -> Validation:
    html_path = choose_html(article_dir)
    if not title.strip() or html_path is None:
        return Validation(False, 0, 0, html_path, "缺少标题或正文 HTML")
    try:
        markup = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Validation(False, 0, 0, html_path, f"正文 HTML 无法读取: {exc}")

    node = _body_node(_parse(markup))
    body_text = "\n".join(
        line.strip() for line in node.text_content().splitlines() if line.strip()
    )
    compact = re.sub(r"\s+", "", body_text)
    image_count = _valid_image_count(node)
    if image_count == 0 and len(compact) < 300 and any(marker in compact for marker in ERROR_MARKERS):
        return Validation(False, len(compact), image_count, html_path, "命中异常页面特征")
    if len(compact) >= 50 or image_count >= 1:
        return Validation(True, len(compact), image_count, html_path)
    return Validation(False, len(compact), image_count, html_path, "正文文本和图片均不足")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}
