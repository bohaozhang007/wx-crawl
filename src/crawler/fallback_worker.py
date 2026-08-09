from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
RSS_PROJECT = ROOT / "third_party" / "we-mp-rss"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(RSS_PROJECT) not in sys.path:
    sys.path.insert(0, str(RSS_PROJECT))

try:
    from integrations.we_mp_rss import load_article_fetcher
except ModuleNotFoundError:
    from src.integrations.we_mp_rss import load_article_fetcher


def full_html(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head>"
        "<meta charset=\"UTF-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1.0\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{max-width:680px;margin:0 auto;padding:20px;"
        "font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
        "line-height:1.8;color:#333}img{max-width:100%;height:auto}</style>"
        "</head><body>"
        f"<h1>{html.escape(title)}</h1><div id=\"js_content\">{body}</div>"
        "</body></html>"
    )


async def fetch(url: str, output: Path, title_hint: str) -> int:
    WXArticleFetcher = load_article_fetcher(RSS_PROJECT)

    fetcher = WXArticleFetcher(wait_timeout=30_000)
    try:
        article = await fetcher.get_article_content(url)
    finally:
        await fetcher.Close()

    content = str(article.get("content") or "")
    error = str(article.get("fetch_error") or "").strip()
    if error or not content or content == "DELETED":
        print(error or "we-mp-rss 未返回正文", file=sys.stderr)
        return 2

    title = str(article.get("title") or title_hint or "未命名").strip()
    output.mkdir(parents=True, exist_ok=True)
    (output / "fallback.html").write_text(full_html(title, content), encoding="utf-8")

    soup = BeautifulSoup(content, "html.parser")
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )
    old_text_path = output / "content.txt"
    old_text = ""
    if old_text_path.is_file():
        old_text = old_text_path.read_text(encoding="utf-8", errors="replace")
    if len(text) > len(old_text.strip()):
        old_text_path.write_text(text, encoding="utf-8")

    public_meta = {
        "source": "we-mp-rss fallback",
        "url": url,
        "title": title,
        "publish_time": article.get("publish_time") or 0,
        "author": article.get("author") or "",
        "article_type": article.get("article_type", 0),
    }
    (output / "fallback_metadata.json").write_text(
        json.dumps(public_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", default="")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(fetch(args.url, args.output, args.title)))


if __name__ == "__main__":
    main()
