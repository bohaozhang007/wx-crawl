from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRECHECK = ROOT / "scripts" / "check_wechat_auth.py"

# 直接以脚本运行时 sys.path[0] 是 scripts/，必须把项目根目录加进 sys.path，
# 否则 `from src.auth.dingtalk_notify import ...` 会报 ModuleNotFoundError。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(PRECHECK)], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=360,
    )
    lines = [x.strip() for x in result.stdout.splitlines() if x.strip()]
    payload = {}
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict): payload = value; break
    status = payload.get("status")
    if result.returncode == 0 and status in {"ready", "ready_after_login"}:
        from src.auth.dingtalk_notify import send_auth_status
        send_auth_status("success")
        return 0
    if status == "authentication_timeout":
        from src.auth.dingtalk_notify import send_auth_status
        send_auth_status("timeout")
    elif status in {"service_start_failed", "api_unavailable", "login_failed"}:
        from src.auth.dingtalk_notify import send_auth_status
        send_auth_status("qr_error", str(payload))
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode or 10

if __name__ == "__main__":
    raise SystemExit(main())
