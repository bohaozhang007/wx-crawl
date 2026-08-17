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
- Entrypoint: `/root/workspace/wx-crawl/run.sh` (wrapper runs credential preflight, then `src/crawl.py`)
- Crawler entrypoint: `python3 src/crawl.py`
- Credential preflight: `python3 scripts/check_wechat_auth.py --check-only`
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
- Incremental lookback: `incremental_max_days` positive integer, default `1`; applies only to `incremental` mode.
- Whether the user wants a readiness check, a real crawl, or only a result summary.

If the user supplied no URL and asks to refresh/update, use the existing registry. If the request implies only one account, explain that the framework still processes the complete registry before running; do not falsely claim account-only isolation.

### 2. Inspect current scope

Use `read_file` on `config.yaml` and `account_sources.csv`. Report the registered-account count before a real crawl when it materially affects runtime. Do not edit the registry manually.

### 3. Enforce one shared task

All callers and DingTalk users share the same repository and archive. The
crawler itself acquires `results/record/.crawler.lock` during real execution,
covering direct, manual, and scheduled starts. If output says `已有一个爬取任务正在运行`, treat it as a duplicate request: do not start another
process, kill the existing process, or retry in a loop.

### 4. Credential preflight before every real crawl

Always run the fast preflight before `src/crawl.py`:

```bash
python3 /root/workspace/wx-crawl/scripts/check_wechat_auth.py --check-only
```

The preflight checks both `/api/auth/status` and `/api/account-pool`. Continue only when `logged_in=true` and at least one account has `status=active`. The repository wrapper performs the same gate automatically:

```bash
/root/workspace/wx-crawl/run.sh --verbose
```

When credentials are absent/expired, the account pool has `active=0`, all accounts are `invalid`, or the API reports `WeReadError401`/token expiry, the preflight calls `/api/auth/login`, waits for a new QR, sends the verified image to the configured DingTalk group, and sends a native text mention to both configured recipients: Zhang Bohao (`021616681719-1773375672`) and Yang Donglin (`2669682637-288741163`). It waits for login success before starting the crawl. A timeout or failed QR delivery is terminal; do not start the crawler or claim success.

### 5. Shared task and stale-lock gate
```text
terminal(command="python3 src/crawl.py --verbose", workdir="/root/workspace/wx-crawl", background=true, notify_on_complete=true)
```

Alternatively use the repository wrapper, which clears proxy variables before
invoking the same entrypoint:

```text
terminal(command="./run.sh --verbose", workdir="/root/workspace/wx-crawl", background=true, notify_on_complete=true)
```

When the scheduled task invokes `scripts/check_wechat_auth_notify.py`, that wrapper is the sole owner of QR delivery and authentication status messages. The Agent must not independently poll/login, send `MEDIA:` paths, forward a second QR, or claim status notifications that are not present in the wrapper output. The wrapper sends exactly one QR per distinct login qrcode URL, then sends a success or timeout status message.

On trigger:

1. Keep the original crawl process running; never start a duplicate crawl.
2. Poll until the newest run's `tools-log/wechat-login-qr.png` exists and has non-zero size. This is the crawler-generated WeChat Reading login QR, not a book-cover image or arbitrary page screenshot.
3. Load it with `vision_analyze` and verify that it is a complete QR code. If invalid, wait for the next generated file rather than sending it.
4. Immediately send `MEDIA:<absolute-qr-path>` to the **exact caller destination captured from the triggering inbound message**. In a DM, deliver it to that caller's DM. In a group, deliver it to the same group and include a native mention of that caller using the inbound message's stable user ID (not only `@display-name` text). On DingTalk this means the OpenAPI image-send path plus the platform-native `atUserIds`/card mention field; verify that the user is actually highlighted/clickable and notified. Never broadcast the QR to a home/default channel and never send it only to the task owner if a different user triggered the run. Include a short request to scan in WeChat and confirm.
5. Continue polling the original process and login state. If the QR expires or the login API starts another attempt, send the replacement QR once; deduplicate by file hash or QR URL so the same QR is not sent repeatedly.
6. Authentication is complete only when `/api/auth/status` reports `logged_in: true` and `login_state.status: success`, or the crawler logs `扫码成功`. Then let the original crawl continue automatically—do not wait for a separate user reply to resume it.
7. Delete the temporary QR after success/expiry only if the crawler has not already removed it. Never expose tokens, cookies, or account-pool files.

This QR-delivery gate is required for interactive and scheduled runs. A cron
run must deliver the QR to its configured destination and report that it is
waiting for a scan; it must not claim the crawl completed while authentication
is pending.

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
- `incremental` scans at most `incremental_max_days` days (default 1) and stops at the first local URL breakpoint or the time boundary; a fresh machine also applies that boundary while using `articles_per_account` as its initial range.
- Always run `scripts/check_wechat_auth.py` before a real crawl; do not wait for per-account identity validation to discover expired credentials.
- `window` checks only the latest configured number of upstream history entries.
- Only one crawler instance can use the repository at a time.
- Port `127.0.0.1:5200` may be used by `wechat-mp-tools`; reuse only a compatible existing service.
- Proxy variables can interfere with local API calls; the project `run.sh` clears them and the helper does the same.
- Authentication QR images expire quickly and are deleted after authentication/cleanup.
- Content validation intentionally accepts short or image-heavy articles; video/audio/interactive resources are not guaranteed.
- Existing articles are deduplicated by normalized URL.
- Interrupt with one `SIGINT`/Ctrl+C when possible so the crawler writes an interrupted record and cleans up services.

## Existing runtime limitation

The crawler now raises `AuthenticationRequiredError` when the local API reports
an unavailable account, but DingTalk QR delivery is an Agent/gateway concern.
For scheduled execution, the pipeline prompt must catch this signal, invoke the
same QR flow, send the image to its configured origin group with native mention,
and wait for login before continuing. Do not treat this error as an ordinary
per-account warning or proceed to labeling/export with a failed crawl.

- 认证预检脚本：`/root/workspace/wx-crawl/scripts/check_wechat_auth.py`
- 统一运行入口：`/root/workspace/wx-crawl/run.sh`（先预检凭证，再运行爬取）

## Verification Checklist

- [ ] The request scope and complete-registry behavior were made clear.
- [ ] `--check` passed before a real crawl.
- [ ] Any required QR login was actually completed.
- [ ] The crawl process reached a terminal exit state.
- [ ] Latest per-run CSVs and global summary were read.
- [ ] Failures were checked in `crawler.log` and reported honestly.
- [ ] Final response includes real counts and absolute result paths.
