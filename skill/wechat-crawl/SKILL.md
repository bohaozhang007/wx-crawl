---
name: wechat-crawl
description: Run the WeChat Official Account crawler in /root/workspace/wx-crawl and deliver its temporary login QR code to the chat when authentication is required. Use when an agent is asked to launch, run, monitor, or troubleshoot this repository's crawler or its WeChat QR-code login.
---

# WeChat Crawl

Run the repository's existing launcher directly. Do not add tmux, `nohup`, or
another wrapper.

## Start

Use the execution tool with `/root/workspace/wx-crawl` as the working directory:

```bash
./run.sh
```

Pass through any user-requested arguments. Keep the command's process handle or
terminal session so the crawler can continue running and its output can be
observed. Configure the execution tool to yield a live process handle instead of
killing the command on timeout. Do not start a second copy while the first
invocation is still active.

The launcher already changes to the repository root and starts the supported
Python entry point. Its normal output and the files under `results/record/` are
the source of truth for progress and completion.

## Handle QR login

During startup, inspect both the command output and the newest run directory:

```bash
find /root/workspace/wx-crawl/results/record \
  -mindepth 3 -maxdepth 3 -type f \
  -path '*/tools-log/wechat-login-qr.png' -printf '%T@ %p\n' \
  | sort -nr | head -1
```

When the command prints a QR path or this command returns a file, attach that PNG
to the chat immediately using the platform's native image/file attachment tool.
For Codex, use the local image viewing tool. For OpenClaw-compatible transports
without a dedicated attachment call, emit a standalone media directive:

```text
MEDIA:/root/workspace/wx-crawl/results/record/<start-time>/tools-log/wechat-login-qr.png
```

Ask the user to scan it with WeChat and confirm login. Do not reply with only a
filesystem path, copy or transform the image, or wait until the five-minute
login timeout before sending it. The crawler removes the short-lived PNG after
successful authentication or cleanup, so use the path while it exists.

Continue observing the original process after sending the image. Report whether
the crawl is still running, completed successfully, or exited with an error,
including the run-record path when available.

## Progress and results

For a detached or long-running execution, inspect the latest run log without
starting another crawler:

```bash
find /root/workspace/wx-crawl/results/record \
  -mindepth 3 -maxdepth 3 -type f -name crawler.log -printf '%T@ %p\n' \
  | sort -n | tail -1
```

The QR code is normally at:
`results/record/<YYYY_MM_DD_HH_MM_SS>/tools-log/wechat-login-qr.png`.
It is temporary and is deleted after login or task cleanup.
