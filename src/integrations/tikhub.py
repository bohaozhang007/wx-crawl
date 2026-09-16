"""Small, bounded HTTP client for TikHub WeChat MP V2 (no automatic retries)."""
from __future__ import annotations

import json
import os
import base64
from html import unescape
from urllib.parse import urlsplit, parse_qs
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]


class TikHubError(RuntimeError):
    pass


class TikHubHTTPError(TikHubError):
    def __init__(self, endpoint, status_code):
        self.status_code = status_code
        super().__init__(f"TikHub {endpoint} HTTP {status_code}")


class TikHubClient:
    def __init__(self, *, max_requests: int = 20, session=None):
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        key = os.environ.get("TIKHUB_API_KEY", "")
        credential = ROOT / "src/auth/config/tikhub.env"
        if not key and credential.is_file():
            for line in credential.read_text().splitlines():
                if line.startswith("TIKHUB_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('\"\'')
        if not key:
            raise TikHubError("缺少 TIKHUB_API_KEY")
        self.session = session or requests.Session()
        self.key = key
        self.max_requests = max_requests
        self.calls: list[dict] = []
        self.prefetched_pages = {}
        self.identity_events = []

    def _post(self, endpoint: str, payload: dict, *, family="wechat_mp") -> dict:
        if len(self.calls) >= self.max_requests:
            raise TikHubError("已达到 TikHub 请求上限")
        call = {"endpoint": endpoint, "status": "attempted"}
        self.calls.append(call)
        try:
            response = self.session.post(
                f"https://api.tikhub.io/api/v1/{family}/v2/{endpoint}",
                headers={"Authorization": f"Bearer {self.key}"},
                json={**payload, "raw": False}, timeout=(10, 60),
                allow_redirects=False,
            )
        except requests.RequestException:
            # Upstream errors can echo Authorization. Never persist raw errors.
            call["status"] = "transport_error"
            raise TikHubError("TikHub 网络请求失败；未自动重试，扣费情况待核对") from None
        call["http_status"] = response.status_code
        if response.status_code != 200:
            call["status"] = "http_error"
            raise TikHubHTTPError(endpoint, response.status_code)
        try:
            body = response.json()
            valid = isinstance(body, dict) and body.get("code") == 200 and isinstance(body.get("data"), dict)
        except ValueError:
            valid = False
        if not valid:
            call["status"] = "invalid_response"
            raise TikHubError("TikHub 响应结构或业务状态异常")
        call["status"] = "success"
        return body["data"]

    def fetch_account_articles(self, username: str, offset: str = "") -> dict:
        if not offset and username in self.prefetched_pages:
            return self.prefetched_pages.pop(username)
        return self._post("fetch_account_articles", {"username": username, "offset": offset, "page_size": 20})

    def fetch_article_detail(self, url: str) -> dict:
        return self._post("fetch_article_detail", {"url": url})

    def resolve_username(self, account) -> str:
        path = ROOT / "src/auth/config/tikhub-accounts.json"
        cache = json.loads(path.read_text()) if path.is_file() else {}
        if account.mp_id in cache:
            return cache[account.mp_id]
        # Try the seed plus at most two distinct archived URLs. Only a 400 or
        # identity mismatch permits recovery; never turn 401/402 into more calls.
        try:
            from crawler.content import article_url_key, read_json
            from crawler.cli import account_directory
        except ModuleNotFoundError:
            from src.crawler.content import article_url_key, read_json
            from src.crawler.cli import account_directory
        candidates = [account.sample_url]
        seen = {article_url_key(account.sample_url)}
        for file in sorted(account_directory(account).glob("*/metadata.json"), reverse=True):
            url = str(read_json(file).get("url") or "")
            key = article_url_key(url)
            if key and key not in seen:
                seen.add(key)
                candidates.append(url)
            if len(candidates) >= 3:
                break
        for url in candidates:
            try:
                detail = self.fetch_article_detail(url)
            except TikHubHTTPError as exc:
                if exc.status_code != 400:
                    raise
                self.identity_events.append({"account": account.name, "url": url, "error": "HTTP 400"})
                continue
            username = (detail.get("content") or {}).get("user_name", "")
            if f"MP_WXS_{detail.get('bizUin')}" == account.mp_id and username.startswith("gh_"):
                self.cache_username(account.mp_id, username)
                self.identity_events.append({"account": account.name, "source": "article", "url": url, "verified_biz": detail['bizUin']})
                return username
            self.identity_events.append({"account": account.name, "url": url, "error": "identity_mismatch", "returned_biz": detail.get("bizUin")})
        return self.resolve_by_search(account)

    def search_accounts(self, keyword):
        return self._post("fetch_search", {"keyword": keyword, "business_type": "account"}, family="wechat_search")

    def resolve_by_search(self, account):
        result = self.search_accounts(account.name)
        candidates = []
        for item in result.get("items", []):
            jump = item.get("jumpInfo") or {}
            username = jump.get("userName", "")
            if unescape(jump.get("nickName") or "").strip() == account.name.strip() and username.startswith("gh_") and username not in candidates:
                candidates.append(username)
        for username in candidates[:3]:
            page = self.fetch_account_articles(username)
            identities = set()
            for article in page.get("articles", []):
                encoded = parse_qs(urlsplit(unescape(article.get("url") or "")).query).get("__biz", [])
                if encoded:
                    try:
                        biz = base64.b64decode(encoded[0], validate=True).decode("ascii")
                        if biz.isdigit():
                            identities.add("MP_WXS_" + biz)
                    except (ValueError, UnicodeError):
                        pass
            if identities == {account.mp_id}:
                self.cache_username(account.mp_id, username)
                self.prefetched_pages[username] = page
                self.identity_events.append({"account": account.name, "source": "search_and_history_biz", "verified_id": account.mp_id, "username": username})
                return username
        raise TikHubError("示例及备用文章不可用，搜索未找到经历史 biz 核验的公众号身份")

    def cache_username(self, mp_id: str, username: str) -> None:
        if not username.startswith("gh_"):
            raise TikHubError("无效的公众号标识")
        path = ROOT / "src/auth/config/tikhub-accounts.json"
        cache = json.loads(path.read_text()) if path.is_file() else {}
        cache[mp_id] = username
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)
