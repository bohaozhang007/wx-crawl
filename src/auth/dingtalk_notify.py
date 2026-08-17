from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Callable


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path.home() / ".hermes" / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _settings():
    import yaml
    cfg = yaml.safe_load((Path.home() / ".hermes" / "config.yaml").read_text(encoding="utf-8")) or {}
    return cfg.get("gateway", {}).get("platforms", {}).get("dingtalk", {}).get("extra", {}) or {}


def _send_text(content: str) -> None:
    import requests
    extra = _settings()
    webhook = str(extra.get("webhook_url") or "")
    if not webhook:
        raise RuntimeError("DingTalk webhook_url is not configured")
    ids = extra.get("cron_mention_user_ids") or ["021616681719-1773375672", "2669682637-288741163"]
    response = requests.post(webhook, json={
        "msgtype": "text",
        "text": {"content": content},
        "at": {"atUserIds": ids, "isAtAll": False},
    }, timeout=30)
    response.raise_for_status()
    body = response.json()
    if body.get("errcode", 0) != 0:
        raise RuntimeError(f"DingTalk webhook error: {body.get('errmsg', 'unknown')}")


def send_login_qr(qr_path: Path, logger: Callable[[str], None] = lambda _: None) -> None:
    """Send a QR image plus a native-mention login request."""
    from dingtalk_stream import Credential, DingTalkStreamClient
    from alibabacloud_dingtalk.robot_1_0 import client as robot_client, models as robot_models
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as tea_util_models
    extra = _settings(); chat_id = str(extra.get("cron_chat_id") or "")
    if not chat_id: raise RuntimeError("DingTalk cron_chat_id is not configured")
    env = _env(); client_id, secret = env.get("DINGTALK_CLIENT_ID", ""), env.get("DINGTALK_CLIENT_SECRET", "")
    stream = DingTalkStreamClient(Credential(client_id, secret))
    token = stream.get_access_token()
    media_id = stream.upload_to_dingtalk(qr_path.read_bytes(), filetype="image", filename=qr_path.name, mimetype=mimetypes.guess_type(qr_path.name)[0] or "image/png")
    request = robot_models.OrgGroupSendRequest(msg_key="sampleImageMsg", msg_param=json.dumps({"photoURL": media_id}, ensure_ascii=False), open_conversation_id=chat_id, robot_code=extra.get("robot_code") or client_id)
    robot_client.Client(open_api_models.Config(protocol="https", region_id="central")).org_group_send_with_options(request, robot_models.OrgGroupSendHeaders(x_acs_dingtalk_access_token=token), tea_util_models.RuntimeOptions())
    _send_text("微信读书凭证已失效，请扫码登录二维码。")
    logger("登录二维码及登录提醒已发送到 DingTalk 群，已原生@配置用户")


def send_auth_status(status: str, detail: str = "") -> None:
    messages = {
        "success": "✅ 微信读书登录成功，凭证已恢复，正在继续微信公众号爬取。",
        "timeout": "⏱️ 微信读书登录等待 5 分钟超时，本次微信公众号爬取无法继续。",
        "qr_error": "❌ 微信读书二维码发送失败，本次微信公众号爬取无法继续。",
    }
    message = messages.get(status, f"微信公众号认证状态更新：{status}")
    if detail: message += f"\n详情：{detail}"
    _send_text(message)


def crawl_status_message(payload: dict[str, object]) -> str:
    """Build the compact no-agent crawl notification without exposing secrets."""
    status = str(payload.get("status") or "failed")
    run_id = str(payload.get("run_id") or "-")
    if status == "ok":
        duration = float(payload.get("duration_seconds") or 0) / 60
        return (
            "✅ 微信公众号定时爬取完成"
            f"\n运行：{run_id}"
            f"\n公众号：{int(payload.get('account_count') or 0)} 个"
            f"\n新增文章：{int(payload.get('new_article_count') or 0)} 篇"
            f"\n耗时：{duration:.1f} 分钟"
            f"\n记录：{payload.get('record_dir') or '-'}"
        )
    if status == "interrupted":
        title = "⏹️ 微信公众号定时爬取已中断"
    else:
        title = "❌ 微信公众号定时爬取失败"
    error = str(payload.get("error") or "未知错误").replace("\n", " ")[:500]
    return f"{title}\n运行：{run_id}\n原因：{error}"


def send_crawl_status(payload: dict[str, object]) -> None:
    _send_text(crawl_status_message(payload))


def pipeline_stage_message(stage: str, payload: dict[str, object]) -> str:
    """Build one compact notification for a completed high-level pipeline stage."""
    duration = float(payload.get("duration_seconds") or 0) / 60
    failed = int(payload.get("failed_batch_count") or 0)
    marker = "⚠️" if failed else "✅"
    if stage == "label":
        return (
            f"{marker} 微信公众号流水线：打标阶段完成"
            f"\n批次：{int(payload.get('batch_count') or 0)} 个"
            f"\n文章记录：{int(payload.get('article_count') or 0)} 篇"
            f"\n新生成标签：{int(payload.get('labeled_count') or 0)} 篇"
            f"\n重试文章：{int(payload.get('retry_article_count') or 0)} 篇"
            f"\n失败批次：{int(payload.get('failed_batch_count') or 0)} 个"
            f"\n耗时：{duration:.1f} 分钟"
        )
    if stage == "select":
        return (
            f"{marker} 微信公众号流水线：筛选与报告阶段完成"
            f"\nKEEP：{int(payload.get('keep_count') or 0)} 篇"
            f"\nDROP：{int(payload.get('drop_count') or 0)} 篇"
            f"\nREVIEW：{int(payload.get('review_count') or 0)} 篇"
            f"\n失败批次：{int(payload.get('failed_batch_count') or 0)} 个"
            f"\n耗时：{duration:.1f} 分钟"
        )
    if stage == "storage":
        return (
            f"{marker} 微信公众号流水线：入库与同步阶段完成"
            f"\n完成批次：{int(payload.get('completed_batch_count') or 0)} 个"
            f"\n失败批次：{int(payload.get('failed_batch_count') or 0)} 个"
            f"\n筛选入库文章：{int(payload.get('article_count') or 0)} 篇"
            f"\n数据库新增/更新：{int(payload.get('db_inserted') or 0)}/"
            f"{int(payload.get('db_updated') or 0)}"
            f"\nAI表新增/更新/未变化：{int(payload.get('sync_inserted') or 0)}/"
            f"{int(payload.get('sync_updated') or 0)}/"
            f"{int(payload.get('sync_unchanged') or 0)}"
            f"\n耗时：{duration:.1f} 分钟"
        )
    raise ValueError(f"unknown pipeline stage: {stage}")


def send_pipeline_stage(stage: str, payload: dict[str, object]) -> None:
    _send_text(pipeline_stage_message(stage, payload))
