---
name: wechat-official-account-crawler
description: Crawl and archive WeChat Official Account articles.
version: 0.1.0
author: 杨东霖, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [WeChat, Official Accounts, Crawler, Archive]
    related_skills: []
---

# WeChat Official Account Crawler

Use the local `wx-crawl` framework to discover Official Accounts from article links, crawl account history, and maintain a local article archive. This skill operates the existing project; it does not bypass WeChat authentication or anti-abuse controls.

## When to Use

- The user asks to crawl, download, archive, update, or monitor WeChat Official Account articles.
- The user provides one or more `https://mp.weixin.qq.com/s/...` links and asks to collect the corresponding account history.
- The user asks to refresh the existing local WeChat article archive or summarize the latest crawl.
- The user asks to schedule recurring Official Account collection; run this workflow through `cronjob`.

Do not use for arbitrary web pages, WeChat Channels video, mini-program content, audio, or guaranteed recovery of deleted/private articles.

## Project Contract

- Repository: `/root/workspace/wx-crawl`
- Entrypoint: `python3 src/crawl.py`
- Safe preflight: `python3 src/crawl.py --check`
- Runtime helper: `scripts/run_crawl.py` in this skill directory
- Persistent account registry: `account_sources.csv`
- Derived archive: `results/articles/`
- Global summary: `results/summary.csv`
- Per-run records: `results/record/<timestamp>/`

A normal run always processes **every account already registered in `account_sources.csv`**, not only accounts supplied in the current input. Supplied links discover or refresh accounts and are explicit article candidates, but do not limit the run to those accounts.

## Prerequisites

1. Linux with the prepared pinned environments under `third_party/`.
2. Python 3.12 runtime as required by the project; `src/crawl.py` automatically re-execs into the prepared `wechat-mp-tools` environment.
3. Network access to WeChat and upstream services.
4. WeChat mobile app available for QR authentication when credentials are absent or expired.
5. Sufficient disk space; article images can make the archive large.

Never expose or commit `src/auth/config/`, temporary QR images, or other credentials.

## Procedure

### 1. Interpret scope

Extract these optional values from the request:

- One or more full `https://mp.weixin.qq.com/s/...` URLs.
- Mode: `incremental` (default) or `window`.
- History size: positive integer, defaulting to the current project setting.
- Whether the user wants a readiness check, a real crawl, or only a result summary.

If the user supplied no URL and asks to refresh/update, use the existing registry. If the request implies only one account, explain that the framework still processes the complete registry before running; do not falsely claim account-only isolation.

### 2. Inspect current scope

Use `read_file` on `config.yaml` and `account_sources.csv`. Report the registered-account count before a real crawl when it materially affects runtime. Do not edit the registry manually.

### 3. Enforce one shared task

All callers and all DingTalk users share the same repository and archive. Invoke only `scripts/run_crawl.py`; it acquires `results/record/.hermes-request.lock` **before** changing `config.yaml` or creating temporary input. If another helper-driven request is active, it exits with code `75` and says `已有一个微信公众号爬取任务正在运行...，本次请求未重复启动。`

Treat exit code `75` as an idempotent duplicate request, not a crawl failure: do not start another process, do not modify configuration, and tell the caller that the existing shared task continues. Never kill or replace the first user's task merely because another user requested a refresh.

The crawler also holds its own `results/record/.crawler.lock` during real execution, covering direct/manual crawler starts. If output says `已有一个爬取任务正在运行`, likewise attach conceptually to the existing run and do not retry in a loop.

### 4. Preflight authentication before starting the crawler

For the plain existing-registry run, first query the local account-pool status (reuse or start the compatible `wechat-mp-tools` service as described below). If it reports no usable account, immediately start QR login, capture the triggering caller destination, and send the verified QR to that caller before launching `scripts/run_crawl.py`. In a DM send to that caller's DM; in a group send to the same group with a native mention using the caller's stable inbound user ID. Do not start a doomed crawl just to discover `无可用账号` after 31 account checks.

For current project settings, after authentication/preflight:

```text
terminal(command="python3 src/crawl.py --check", workdir="/root/workspace/wx-crawl", timeout=120)
```

For request-specific URLs/mode/count, invoke the helper with `--check-only`; repeat `--url` for multiple links:

```text
terminal(command="python3 <skill-dir>/scripts/run_crawl.py --check-only --url '<wechat-url>' --mode incremental --count 30", timeout=120)
```

Stop and report the exact error if preflight fails. Do not start a crawl with missing dependencies or invalid configuration.

### 5. Start the real crawl and enforce the authentication gate

Crawls may run for a long time and may require QR login. Start them as a tracked background process with completion notification:

```text
terminal(command="python3 <skill-dir>/scripts/run_crawl.py --verbose --url '<wechat-url>' --mode incremental --count 30", background=true, notify_on_complete=true)
```

For the existing registry with no new seed URL, omit all `--url` arguments.

Immediately poll the tracked process. Treat any of these as a mandatory authentication trigger: `登录已过期`, `session expired`, `凭证已失效`, `无可用账号`, `暂无可用微信读书账号`, `正在生成登录二维码`, or a new `tools-log/wechat-login-qr.png` path. Do not let the crawl fail first and do not ask the user whether they want to log in.

On trigger:

1. Keep the original crawl process running; never start a duplicate crawl.
2. Poll until the newest run's `tools-log/wechat-login-qr.png` exists and has non-zero size. This is the crawler-generated WeChat Reading login QR, not a book-cover image or arbitrary page screenshot.
3. Load it with `vision_analyze` and verify that it is a complete QR code. If invalid, wait for the next generated file rather than sending it.
4. Immediately send `MEDIA:<absolute-qr-path>` to the **exact caller destination captured from the triggering inbound message**. In a DM, deliver it to that caller's DM. In a group, deliver it to the same group and include a native mention of that caller using the inbound message's stable user ID (not only `@display-name` text). On DingTalk this means the OpenAPI image-send path plus the platform-native `atUserIds`/card mention field; verify that the user is actually highlighted/clickable and notified. Never broadcast the QR to a home/default channel and never send it only to the task owner if a different user triggered the run. Include a short request to scan in WeChat and confirm.
5. Continue polling the original process and login state. If the QR expires or the login API starts another attempt, send the replacement QR once; deduplicate by file hash or QR URL so the same QR is not sent repeatedly.
6. Authentication is complete only when `/api/auth/status` reports `logged_in: true` and `login_state.status: success`, or the crawler logs `扫码成功`. Then let the original crawl continue automatically—do not wait for a separate user reply to resume it.
7. Delete the temporary QR after success/expiry only if the crawler has not already removed it. Never expose tokens, cookies, or account-pool files.

This QR-delivery gate is required for interactive and scheduled runs. A cron run must deliver the QR to its configured destination and report that it is waiting for a scan; it must not claim the crawl completed while authentication is pending.

### 6. Verify completion

A successful process exit is necessary but not sufficient. Verify:

1. Exit code is zero and output contains `爬取结束`.
2. Read the newest `results/record/<timestamp>/account_summary.csv` and `article_details.csv`.
3. Read `results/summary.csv`.
4. If failures or warnings occurred, inspect the matching `tools-log/crawler.log`.
5. Report newly downloaded article count, affected accounts, record directory, global summary path, and any skipped/failing items.

Do not infer success from article directories alone; failed and interrupted runs also create run-record directories.

## Result Interpretation

- `results/articles/<number>_<mp_id>_<name>/<timestamp>_<title>/content.txt` contains cleaned article text.
- `metadata.json` or `data.json` carries title, URL, time, and media metadata.
- Saved HTML and `media/` hold article rendering and images.
- `results/summary.csv` is the archive-wide derived summary.
- Per-run `article_details.csv` lists only articles successfully added in that run.
- Failed downloads are absent from result CSVs; diagnostics are in `tools-log/`.

## Add or Refresh Collection Accounts

The account pool contains **WeChat Reading (微信读书) collection identities**, not Official Account administrator credentials. Never ask for a password, cookie, or token; use QR login.

For DingTalk users, prefer the API-driven QR flow:

1. Confirm no crawl owns `.crawler.lock`; do not modify the pool during an active crawl.
2. Start `third_party/wechat-mp-tools/.venv/bin/python app.py --host 127.0.0.1 --port 5200 --no-browser` as a tracked background process if its health endpoint is not already available.
3. POST `http://127.0.0.1:5200/api/auth/login`.
4. Poll `GET /api/auth/status` until `login_state.qrcode` appears.
5. Deliver the QR according to platform capability. DingTalk local-image delivery is supported when the running Hermes gateway includes the OpenAPI media path: the adapter uploads with `/media/upload`, then sends `sampleImageMsg` through group or OTO robot OpenAPI. Use `MEDIA:/absolute/path.png`, verify delivery succeeded, and delete the temporary QR after login or expiry. If the gateway has not yet been restarted onto that implementation, state that clearly and use a secure web UI/SSH-tunnel fallback rather than claiming delivery succeeded.
6. Continue polling until `logged_in` is true and `login_state.status` is `success`; then query `GET /api/account-pool` and report the new/updated nickname and pool status without exposing tokens.
7. Remove the temporary QR and stop the service only if this workflow started it.

Alternatively, for a human using the web UI, run the service bound to `127.0.0.1`, use an SSH tunnel to port 5200 if remote, open the **账号池** page, click **添加账号**, scan, and confirm. Adding a second distinct WeChat identity appends it; scanning the same identity refreshes its credentials.

## Scheduling

For a recurring refresh, create a `cronjob` with a self-contained prompt that loads this skill and runs the existing registry in `incremental` mode. Set `workdir` to `/root/workspace/wx-crawl`, include `terminal`, `file`, and `vision` toolsets, and deliver only a verified run summary. Warn that unattended runs can stall when WeChat requires a fresh QR login; the job must report that condition rather than claiming success.

## Pitfalls

- Input URLs add discovery seeds but do not restrict the complete registry crawl.
- `incremental` is uncapped for an existing account until a local URL breakpoint is found; a fresh machine uses `articles_per_account` as its initial range.
- `window` checks only the latest configured number of upstream history entries.
- Only one crawler instance can use the repository at a time.
- Port `127.0.0.1:5200` may be used by `wechat-mp-tools`; reuse only a compatible existing service.
- Proxy variables can interfere with local API calls; the project `run.sh` clears them and the helper does the same.
- Authentication QR images expire quickly and are deleted after authentication/cleanup.
- Content validation intentionally accepts short or image-heavy articles; video/audio/interactive resources are not guaranteed.
- Existing articles are deduplicated by normalized URL.
- Interrupt with one `SIGINT`/Ctrl+C when possible so the crawler writes an interrupted record and cleans up services.

## Verification Checklist

- [ ] Shared request lock was acquired; exit code `75` was treated as “already running,” never retried.
- [ ] The request scope and complete-registry behavior were made clear.
- [ ] `--check` passed before a real crawl.
- [ ] Any required QR login was actually completed.
- [ ] The crawl process reached a terminal exit state.
- [ ] Latest per-run CSVs and global summary were read.
- [ ] Failures were checked in `crawler.log` and reported honestly.
- [ ] Final response includes real counts and absolute result paths.
