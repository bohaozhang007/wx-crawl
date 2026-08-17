#!/usr/bin/env python3
"""Preflight WeChat Reading credentials before starting a crawl.

Exit codes: 0=ready, 10=QR/login completed, 11=timeout, 12=login failed,
20=service/API unavailable, 21=service startup failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MP_PROJECT = ROOT / "third_party" / "wechat-mp-tools"
MP_PYTHON = MP_PROJECT / ".venv" / "bin" / "python"
API = "http://127.0.0.1:5200"
QR = ROOT / "results" / "record" / ".auth-preflight" / "wechat-login-qr.png"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def api(method: str, path: str, **kwargs):
    response = requests.request(method, API + path, timeout=kwargs.pop("timeout", 10), **kwargs)
    response.raise_for_status()
    return response.json()


def ready(status: dict, pool: dict) -> bool:
    if not status.get("logged_in"):
        return False
    accounts = pool.get("accounts") or pool.get("result") or pool.get("data") or []
    if isinstance(accounts, dict):
        accounts = accounts.get("accounts") or accounts.get("list") or []
    return any(str(a.get("status", "")).lower() == "active" for a in accounts if isinstance(a, dict))


def start_service():
    try:
        api("GET", "/api/auth/status", timeout=2)
        return None
    except Exception:
        pass
    if not MP_PYTHON.exists():
        raise RuntimeError(f"wechat-mp-tools Python 不存在: {MP_PYTHON}")
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        env.pop(key, None)
    log = ROOT / "results" / "record" / ".auth-preflight" / "wechat-mp-tools.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("a", encoding="utf-8")
    process = subprocess.Popen([str(MP_PYTHON), "app.py", "--host", "127.0.0.1", "--port", "5200", "--no-browser"], cwd=MP_PROJECT, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            handle.close()
            raise RuntimeError(f"wechat-mp-tools 启动失败，日志: {log}")
        try:
            api("GET", "/api/auth/status", timeout=2)
            return process
        except Exception:
            time.sleep(0.5)
    process.terminate()
    handle.close()
    raise RuntimeError("wechat-mp-tools 启动超时")


def parse_preflight_result(output: str, exit_code: int) -> dict[str, object]:
    """Require a machine-readable preflight result; never infer readiness."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    payload = {}
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    ready = exit_code == 0 and payload.get("status") in {"ready", "ready_after_login"} and payload.get("active") is True and payload.get("logged_in") is True
    return {"ready": ready, "exit_code": exit_code, "payload": payload, "raw_tail": lines[-5:]}


def run_credential_preflight() -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_wechat_auth.py")],
        cwd=ROOT, text=True, capture_output=True, timeout=360,
    )
    parsed = parse_preflight_result(result.stdout, result.returncode)
    parsed["stderr_tail"] = result.stderr.splitlines()[-10:]
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        process = start_service()
        status = api("GET", "/api/auth/status")
        pool = api("GET", "/api/account-pool")
        if ready(status, pool):
            print(json.dumps({"status": "ready", "logged_in": True, "active": True}, ensure_ascii=False))
            return 0
        if args.check_only:
            print(json.dumps({"status": "login_required", "logged_in": bool(status.get("logged_in")), "active": False}, ensure_ascii=False))
            return 10
        api("POST", "/api/auth/login")
        sent = set()
        deadline = time.monotonic() + args.wait_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
            status = api("GET", "/api/auth/status")
            state = status.get("login_state") or {}
            qrcode_url = str(state.get("qrcode") or "")
            if qrcode_url and qrcode_url not in sent:
                import qrcode
                QR.parent.mkdir(parents=True, exist_ok=True)
                tmp = QR.with_suffix(".tmp.png")
                qrcode.make(qrcode_url).save(tmp)
                os.replace(tmp, QR)
                from src.auth.dingtalk_notify import send_login_qr
                send_login_qr(QR, lambda msg: print(msg, flush=True))
                sent.add(qrcode_url)
                print(json.dumps({"status": "qr_sent", "path": str(QR)}, ensure_ascii=False), flush=True)
            if status.get("logged_in"):
                pool = api("GET", "/api/account-pool")
                if ready(status, pool):
                    print(json.dumps({"status": "ready_after_login", "active": True}, ensure_ascii=False))
                    return 0
            if state.get("status") == "failed":
                print(json.dumps({"status": "login_failed", "message": state.get("message", "")}, ensure_ascii=False))
                return 12
        print(json.dumps({"status": "authentication_timeout", "qr_sent": bool(sent)}, ensure_ascii=False))
        return 11
    except requests.RequestException as exc:
        print(json.dumps({"status": "api_unavailable", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 20
    except Exception as exc:
        print(json.dumps({"status": "service_start_failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 21


if __name__ == "__main__":
    raise SystemExit(main())
