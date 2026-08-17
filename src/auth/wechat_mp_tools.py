from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)

def service_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in PROXY_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment

def remove_legacy_auth_file(path: Path) -> None:
    try: path.unlink()
    except FileNotFoundError: pass

def ensure_login(
    api,
    logger,
    qr_path: Path,
    save_qr: Callable[[str, Path], Path],
    remove_qr: Callable[[Path], None],
    legacy_auth_path: Path,
    auth_display_path: Path,
    timeout_seconds: int,
    max_attempts: int = 3,
    force: bool = False,
    on_qr: Callable[[Path], None] | None = None,
    on_login: Callable[[], None] | None = None,
    on_timeout: Callable[[], None] | None = None,
) -> None:
    status = api.request("GET", "/api/auth/status")
    if status.get("logged_in") and not force:
        remove_legacy_auth_file(legacy_auth_path)
        return
    deadline = time.monotonic() + timeout_seconds
    displayed_qr = ""
    attempt = 0
    try:
        while time.monotonic() < deadline:
            attempt += 1
            api.request("POST", "/api/auth/login")
            logger.info("正在生成登录二维码（第 %d/%d 次，最长等待 %d 分钟）", attempt, max_attempts, timeout_seconds // 60)
            while time.monotonic() < deadline:
                time.sleep(1)
                status = api.request("GET", "/api/auth/status")
                if status.get("logged_in"):
                    remove_legacy_auth_file(legacy_auth_path)
                    logger.info("扫码成功，凭据已保存到 %s", auth_display_path)
                    if on_login is not None:
                        on_login()
                    return
                state = status.get("login_state") or {}
                qrcode_url = str(state.get("qrcode") or "")
                if qrcode_url and qrcode_url != displayed_qr:
                    saved_path = save_qr(qrcode_url, qr_path)
                    logger.info("登录二维码已生成，请打开并扫描：%s", saved_path)
                    if on_qr is not None:
                        on_qr(saved_path)
                    displayed_qr = qrcode_url
                if state.get("status") == "failed":
                    message = state.get("message") or "扫码登录失败"
                    if attempt >= max_attempts: raise RuntimeError(message)
                    logger.warning("登录请求失败，将重试：%s", message)
                    time.sleep(attempt)
                    break
            else: break
        if on_timeout is not None:
            on_timeout()
        raise RuntimeError("扫码登录等待超时")
    finally:
        remove_qr(qr_path)
        remove_legacy_auth_file(legacy_auth_path)

def ensure_wechat_login(*args, **kwargs):
    return ensure_login(*args, **kwargs)
