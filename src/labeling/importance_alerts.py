from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.auth.dingtalk_notify import send_high_importance_alert
from .eligibility import deadline_expired, geography_eligible


REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER_NAME = "importance_alert.json"


def _config() -> dict[str, Any]:
    payload = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    value = payload.get("importance_alert") or {}
    if not isinstance(value, dict):
        raise ValueError("config.yaml importance_alert must be a mapping")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _fingerprint(label: dict[str, Any], url: str) -> str:
    importance = label.get("importance") or {}
    raw = json.dumps(
        {
            "url": url,
            "tree_version": label.get("tree_version"),
            "profile_version": label.get("profile_version"),
            "level": importance.get("level"),
            "reason": importance.get("reason"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def notify_high_importance(article_dirs: list[Path]) -> dict[str, Any]:
    cfg = _config()
    if not bool(cfg.get("enabled", True)):
        return {"high": 0, "sent": 0, "skipped_sent": 0, "skipped_expired": 0, "skipped_geography": 0, "failed": 0, "errors": []}
    ids = [str(value).strip() for value in cfg.get("mention_user_ids", []) if str(value).strip()]
    if not ids:
        raise ValueError("importance_alert.mention_user_ids must contain at least one DingTalk userId")

    result: dict[str, Any] = {"high": 0, "sent": 0, "skipped_sent": 0, "skipped_expired": 0, "skipped_geography": 0, "failed": 0, "errors": []}
    for article_dir in article_dirs:
        label = _read_json(article_dir / "label.json")
        if deadline_expired(label):
            result["skipped_expired"] += 1
            continue
        if not geography_eligible(label):
            result["skipped_geography"] += 1
            continue
        if label.get("decision") != "KEEP" or (label.get("importance") or {}).get("level") != "high":
            continue
        from .schema import validate_payload
        if validate_payload(label, require_summary=True, require_deadline=True, require_importance=True):
            result["failed"] += 1
            result["errors"].append({"article_dir": str(article_dir), "error": "current-version valid label required before alert"})
            continue
        result["high"] += 1
        metadata = _read_json(article_dir / "metadata.json")
        fallback = _read_json(article_dir / "data.json")
        metadata = {**fallback, **{key: value for key, value in metadata.items() if value not in (None, "")}}
        url = str(metadata.get("url") or "")
        fingerprint = _fingerprint(label, url)
        marker = article_dir / MARKER_NAME
        prior = _read_json(marker)
        if prior.get("status") == "sent":
            result["skipped_sent"] += 1
            continue
        importance = label["importance"]
        factors = importance.get("factors") or {}
        deadline = label.get("deadline") or {}
        item = {
            "title": metadata.get("title") or article_dir.name,
            "url": url,
            "account_name": metadata.get("account_name") or article_dir.parent.name,
            "deadline_text": deadline.get("raw_text") or "",
            "amount_text": factors.get("amount_raw_text") or "",
            "domains": "、".join(label.get("domains") or []),
            "summary": label.get("summary") or "",
            "reason": importance.get("reason") or "",
        }
        try:
            response = send_high_importance_alert(item, ids)
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"article_dir": str(article_dir), "error": str(exc)[:1000]})
            continue
        _write_atomic(marker, {
            "schema_version": 1,
            "status": "sent",
            "fingerprint": fingerprint,
            "sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "response": response,
            "mention_user_ids": ids,
        })
        result["sent"] += 1
    return result
