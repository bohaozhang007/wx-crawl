from __future__ import annotations

import os
from pathlib import Path


def _ensure_private_file(path: Path, default_content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if not path.exists():
        path.write_text(default_content, encoding="utf-8")
    path.chmod(0o600)


def _ensure_runtime_link(link_path: Path, target_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        if link_path.resolve() == target_path.resolve():
            return
        raise RuntimeError(f"认证软链接指向了其他文件：{link_path}")
    if link_path.exists():
        raise RuntimeError(f"认证路径已存在且不是软链接：{link_path}")
    relative_target = os.path.relpath(target_path, link_path.parent)
    link_path.symlink_to(relative_target)


def ensure_auth_layout(root: Path, mp_project: Path, rss_project: Path) -> Path:
    """Create private auth storage and runtime links without editing tool source."""
    config_dir = root / "src" / "auth" / "config"
    wechat_auth = config_dir / "wechat-mp-tools.yaml"
    rss_auth = config_dir / "we-mp-rss.yaml"
    _ensure_private_file(wechat_auth, "[]\n")
    _ensure_private_file(rss_auth, "{}\n")
    _ensure_runtime_link(mp_project / "data" / "account_pool.json", wechat_auth)
    _ensure_runtime_link(rss_project / "data" / "wx.lic", rss_auth)
    return config_dir
