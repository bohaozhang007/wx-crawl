from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable


class WechRssHistoryError(RuntimeError):
    pass


class WechRssRiskControlError(WechRssHistoryError):
    """The upstream account is risk-controlled and must not be retried automatically."""


class WechRssHistoryProvider:
    """Use WechRss as a direct, non-server WeRead history provider.

    The upstream checkout stays unmodified. Credentials are stored in this
    project's private auth directory and only normalized article metadata is
    exposed to the crawler.
    """

    def __init__(
        self,
        project: Path,
        credential_path: Path,
        *,
        logger,
        qr_path: Path,
        on_qr: Callable[[Path], None],
        on_status: Callable[[str, str], None],
        auth_wait_seconds: int = 300,
    ) -> None:
        if not project.is_dir():
            raise WechRssHistoryError(f"WechRss 源码不存在：{project}")
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        try:
            from weread_auth import WeReadAuthClient, WeReadCredentials
            from wechat_mp_fetcher import (
                AuthExpiredError,
                RiskControlError,
                WeReadMobileClient,
            )
            from article_identity import resolve_article_identity
        except Exception as exc:
            raise WechRssHistoryError(f"无法加载 WechRss：{exc}") from exc

        self.WeReadCredentials = WeReadCredentials
        self.WeReadMobileClient = WeReadMobileClient
        self.AuthExpiredError = AuthExpiredError
        self.RiskControlError = RiskControlError
        self.resolve_article_identity = resolve_article_identity
        self.auth_client = WeReadAuthClient(timeout=25)
        self.credential_path = credential_path
        self.logger = logger
        self.qr_path = qr_path
        self.on_qr = on_qr
        self.on_status = on_status
        self.auth_wait_seconds = auth_wait_seconds
        self.credentials = None

    def _save(self, credentials) -> None:
        self.credential_path.parent.mkdir(parents=True, exist_ok=True)
        self.credential_path.parent.chmod(0o700)
        temporary = self.credential_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(credentials), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.credential_path)
        self.credential_path.chmod(0o600)
        self.credentials = credentials

    def _load(self):
        if self.credentials is not None:
            return self.credentials
        try:
            data = json.loads(self.credential_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            credentials = self.WeReadCredentials.from_mapping(data)
            credentials.validate_basic()
        except Exception:
            return None
        self.credentials = credentials
        return credentials

    def _write_qr(self, data_uri: str) -> None:
        import base64

        marker = "base64,"
        if marker not in data_uri:
            raise WechRssHistoryError("WechRss 返回的二维码格式异常")
        self.qr_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.qr_path.with_name(".wechrss-login-qr.tmp.png")
        temporary.write_bytes(base64.b64decode(data_uri.split(marker, 1)[1]))
        temporary.chmod(0o600)
        temporary.replace(self.qr_path)
        self.qr_path.chmod(0o600)

    def _qr_login(self) -> None:
        try:
            qr = self.auth_client.request_qr()
            self._write_qr(qr.qr_data_uri)
            self.on_qr(self.qr_path)
            deadline = time.monotonic() + self.auth_wait_seconds
            last = None
            while time.monotonic() < deadline:
                poll = self.auth_client.poll_qr_once(qr.uuid, last=last, timeout=20)
                last = poll.errcode
                if poll.status == "confirmed":
                    credentials = self.auth_client.exchange_qr(poll.wx_code)
                    initialized = self.auth_client.initialize_feature(credentials)
                    if initialized.guest_token:
                        credentials.guestToken = initialized.guest_token
                    if initialized.sync_key:
                        credentials.syncKey = initialized.sync_key
                    self._save(credentials)
                    self.on_status("success", "WechRss 历史后端登录成功")
                    return
                if poll.status == "scanned":
                    self.logger.info("WechRss 二维码已扫码，等待手机确认")
            self.on_status("timeout", "WechRss 历史后端扫码超时")
            raise WechRssHistoryError("WechRss 扫码登录等待超时")
        except WechRssHistoryError:
            raise
        except Exception as exc:
            raise WechRssHistoryError(f"WechRss 二维码登录失败：{exc}") from exc
        finally:
            try:
                self.qr_path.unlink()
            except FileNotFoundError:
                pass

    def ensure_login(self) -> None:
        if self._load() is None:
            self._qr_login()

    def _client(self):
        credentials = self._load()
        if credentials is None:
            self._qr_login()
            credentials = self.credentials
        return self.WeReadMobileClient(
            credentials.accessToken,
            credentials.vid,
            timeout=25,
            min_interval=2,
            version_headers=self.auth_client.version_headers(),
        )

    def _call(self, operation):
        try:
            return operation(self._client())
        except self.RiskControlError as exc:
            raise WechRssRiskControlError(
                "WechRss 被微信读书风控（-2041/-2010/429）；已停止自动请求。"
                "请打开官方微信读书完成必要的人工操作，等待账号恢复后再运行"
            ) from exc
        except self.AuthExpiredError:
            credentials = self._load()
            try:
                if credentials is None or not credentials.can_refresh:
                    raise WechRssHistoryError("WechRss 凭据不可续期")
                self.logger.info("WechRss 凭据失效，尝试 refreshToken 自动续期")
                self._save(self.auth_client.refresh(credentials))
                return operation(self._client())
            except Exception as exc:
                self.logger.warning("WechRss 自动续期失败，重新发送二维码：%s", exc)
                self.credentials = None
                self._qr_login()
                return operation(self._client())

    def resolve_account(self, article_url: str) -> dict[str, str]:
        try:
            identity = self.resolve_article_identity(article_url)
        except Exception as exc:
            raise WechRssHistoryError(f"WechRss 无法识别公众号链接：{exc}") from exc
        return {
            "fakeid": str(identity.book_id),
            "nickname": str(identity.account_name or "").strip(),
        }

    def history_page(self, mp_id: str, begin: int, count: int) -> list[dict]:
        articles = self._call(
            lambda client: client.get_articles(
                mp_id,
                count=count,
                offset=None if begin == 0 else begin,
                synckey=0,
            )
        )
        return [
            {
                "link": article.url,
                "title": article.title,
                "publish_time": article.publish_at,
                "update_time": article.publish_at,
                "nickname": article.author,
                "review_id": article.review_id,
            }
            for article in articles
            if article.url
        ]
