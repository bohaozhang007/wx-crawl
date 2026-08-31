from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from lxml import html as lxml_html

from .content import choose_html, safe_component


FILE_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"}
LINK_HINT = re.compile(r"附件|下载|申报书|申请表|指南|模板|材料", re.I)


def discover_attachments(article_dir: Path, article_url: str) -> list[dict]:
    html_path = choose_html(article_dir)
    if html_path is None:
        return []
    document = lxml_html.fromstring(html_path.read_text(encoding="utf-8", errors="replace"))
    found: list[dict] = []
    seen: set[str] = set()
    for anchor in document.xpath("//a[@href]"):
        raw = str(anchor.get("href") or "").strip().replace("&amp;", "&")
        url = urljoin(article_url, raw)
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            continue
        text = " ".join(anchor.text_content().split()).strip()
        extension = Path(unquote(parts.path)).suffix.lower()
        is_download = extension in FILE_EXTENSIONS or bool(LINK_HINT.search(text)) or "download" in parts.path.lower()
        if not is_download or url in seen:
            continue
        if parts.hostname == "mp.weixin.qq.com" and parts.path.rstrip("/") in {"/s", "/s/"}:
            continue
        seen.add(url)
        found.append({"title": text or Path(unquote(parts.path)).name or "附件", "source_url": url})
    return found


def _response_filename(response, source_url: str, title: str, index: int) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    candidate = unquote(match.group(1).strip()) if match else Path(unquote(urlsplit(response.url or source_url).path)).name
    suffix = Path(candidate).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(response.headers.get("Content-Type", "").split(";", 1)[0]) or ""
    stem = Path(candidate).stem if candidate else title
    return safe_component(f"{index:02d}_{stem}", 100) + suffix[:10]


def archive_attachments(
    article_dir: Path,
    article_url: str,
    *,
    timeout_seconds: int = 30,
    max_bytes: int = 50 * 1024 * 1024,
) -> dict:
    candidates = discover_attachments(article_dir, article_url)
    output_dir = article_dir / "attachments"
    output_dir.mkdir(exist_ok=True)
    results: list[dict] = []
    for index, candidate in enumerate(candidates, start=1):
        record = {**candidate, "resolved_url": "", "status": "link_only", "local_path": "", "mime_type": "", "size_bytes": 0}
        download_path: Path | None = None
        try:
            with requests.get(
                candidate["source_url"],
                headers={"User-Agent": "Mozilla/5.0", "Referer": article_url},
                timeout=timeout_seconds,
                stream=True,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                record["resolved_url"] = response.url
                record["mime_type"] = response.headers.get("Content-Type", "").split(";", 1)[0]
                if record["mime_type"] == "text/html" and Path(urlsplit(response.url).path).suffix.lower() not in FILE_EXTENSIONS:
                    record["error"] = "resolved_to_html_page"
                else:
                    filename = _response_filename(response, candidate["source_url"], candidate["title"], index)
                    path = output_dir / filename
                    download_path = path
                    size = 0
                    with path.open("wb") as handle:
                        for chunk in response.iter_content(64 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError("attachment exceeds configured size limit")
                            handle.write(chunk)
                    record.update(status="downloaded", local_path=str(path.relative_to(article_dir)), size_bytes=size)
        except Exception as exc:
            if download_path is not None:
                download_path.unlink(missing_ok=True)
            record["error"] = str(exc)[:500]
        results.append(record)
    manifest = {"count": len(results), "downloaded": sum(x["status"] == "downloaded" for x in results), "attachments": results}
    (output_dir / "attachments.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
