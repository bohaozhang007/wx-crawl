# wx-crawl

[English](README.md) | [简体中文](README-CN.md)

`wx-crawl` builds a local archive of WeChat Official Account history and article content. It uses [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools) for account discovery, history retrieval, authentication, and primary article downloading, and falls back to [we-mp-rss](https://github.com/rachelos/we-mp-rss) when the primary article result is missing or incomplete. Many thanks to both projects for making this workflow possible.

## Contents

- [1. What it does](#1-what-it-does)
- [2. Project structure](#2-project-structure)
- [3. Quick start](#3-quick-start)
- [4. Crawl results](#4-crawl-results)
- [5. Installation for Codex](#5-installation-for-codex)
- [6. Crawl lifecycle and Git synchronization](#6-crawl-lifecycle-and-git-synchronization)
- [7. Troubleshooting](#7-troubleshooting)
- [Acknowledgements](#acknowledgements)

## 1. What it does

Give `wx-crawl` one or more WeChat Official Account article links. It resolves the corresponding accounts, retrieves their history, and downloads article content into a persistent local archive. Every supplied article link is also downloaded explicitly, even if it falls outside the selected history range.

The normal workflow is:

1. Scan the input CSV for WeChat article URLs.
2. Resolve each URL to an account and merge newly discovered accounts into `account_sources.csv`.
3. Revalidate every registered account ID and name.
4. Retrieve history candidates according to the configured crawl mode.
5. Skip article URLs that already exist locally.
6. Download each candidate with `wechat-mp-tools`.
7. If the primary result fails a lightweight content check, retry it with `we-mp-rss`.
8. Save successful text, HTML, metadata, and images, then update the run records and global summary.

### Content scope and success rule

The project is designed for article text and images. Video, audio, mini-program content, interactive components, and other embedded resources are not guaranteed to be downloaded.

A result is accepted when it has a title and usable HTML containing either at least 50 non-whitespace text characters or at least one valid image, unless it matches a known WeChat error page. This deliberately permissive rule keeps useful image-heavy or short articles. If both download backends fail this check, the article is skipped and the reason is written to the tool logs.

## 2. Project structure

```text
wx-crawl/
├── src/
│   ├── auth/                    Authentication storage and login adapter
│   ├── crawler/                 Crawl orchestration, validation, and reporting
│   ├── integrations/            Runtime adapters for third-party tools
│   └── crawl.py                 Command-line entry point
├── install/
│   ├── requirements.txt         Dependencies used directly by wx-crawl
│   └── third_party_versions.yaml
│                                Upstream repositories and pinned commits
├── third_party/                 Local upstream clones and virtual environments
├── config.yaml                  User-facing crawl configuration
├── input_template.csv           Default input CSV
├── account_sources.csv          Persistent registry of discovered accounts
├── results/                     Downloaded articles and run records
└── .python-version              Recommended Python version
```

### Persistent registry versus run input

- `input_template.csv`, or another CSV selected in `config.yaml`, is the discovery input for the current run. URLs found there can add or refresh accounts.
- `account_sources.csv` is the persistent account registry. It stores a stable local number, upstream account ID, current account name, and an example article URL.
- The example URL stored for each registered account is treated as an explicit article and is downloaded if it is not already local.

**The input CSV does not limit the run to only the accounts listed in that file. After merging new input, every normal run processes every account already present in `account_sources.csv`.** Review the registry before starting a large crawl.

### Local and private data

`third_party/`, `results/`, and `src/auth/config/` are excluded from Git:

- `third_party/` contains upstream source clones and large virtual environments.
- `results/` contains the local article archive and can grow to many gigabytes.
- `src/auth/config/` contains reusable authentication credentials and must never be committed or shared.

The Git repository carries `account_sources.csv`, but it does not carry downloaded articles or authentication state. A fresh clone therefore keeps the source registry while building its own local results and credentials.

## 3. Quick start

Complete the installation in [Section 5](#5-installation-for-codex) before the first run.

### 3.1 Prepare the input

The simplest option is to replace the example URL in `input_template.csv`, or add more WeChat article URLs below it. Put one URL in each cell.

You may instead use any CSV file and set `crawl.input_csv` in `config.yaml` to its path. Relative paths are resolved from the repository root. The CSV requires no specific headers, columns, or layout; **each WeChat article URL must appear by itself in a cell**.

Only links in the WeChat article form `https://mp.weixin.qq.com/s/...` are treated as account seeds. Duplicate input URLs are normalized and processed once.

If the input CSV contains no recognized URL but `account_sources.csv` already contains accounts, the crawler prints a warning and continues with the existing registry. If both are empty, the run stops with an error.

### 3.2 Configure the crawl

```yaml
crawl:
  input_csv: input_template.csv
  mode: incremental
  articles_per_account: 30
```

| Setting | Meaning |
| --- | --- |
| `input_csv` | Input CSV path, relative to the repository root unless absolute |
| `mode` | `incremental` or `window` |
| `articles_per_account` | Positive history-window size used by `window` mode and by newly discovered accounts in `incremental` mode |

The modes behave as follows:

- `incremental`: for an account with locally stored articles, the first matching local URL found while paging backward through the account history acts as the breakpoint. All newer history entries become download candidates. This mode is not capped by `articles_per_account` for an existing account. For a newly discovered account with no local breakpoint, the latest `articles_per_account` history entries form the download range.
- `window`: for every account, the latest `articles_per_account` history entries form the download range.

In either mode, URLs already stored locally are skipped. Explicit URLs from the input CSV and the example URLs in `account_sources.csv` are considered independently of the history-window limit.

`articles_per_account` controls the number of upstream history entries considered, not a guaranteed number of successful files. If an article fails both download backends, the number saved in that run may be lower.

### 3.3 Check local readiness

```bash
python3 src/crawl.py --check
```

`--check` validates the local Python environments, required paths, `config.yaml`, the CSV extension and readability, the account registry, and the authentication storage layout. It reports how many input links and registered accounts were found.

It does not start a crawl, request a QR code, validate current credentials against the live service, or prove that a real article can currently be downloaded.

### 3.4 Start the crawl

```bash
python3 src/crawl.py
```

For more detailed console and file logging:

```bash
python3 src/crawl.py --verbose
```

Press `Ctrl+C` once to stop a long run. Articles that were already completed remain available, an interrupted run record is written, temporary working data is removed, and any tool service started by that run is closed.

### Notes

- On the first authenticated run, the terminal prints the exact path of a QR-code PNG under the current run's `tools-log` directory. Scan it with WeChat and confirm the login within five minutes. The QR image is removed afterward, while the credentials are stored locally for reuse.
- One authentication is used for the history backend; a separate scan is not required for every Official Account.
- Expired or invalid credentials may cause a later run to request a new scan.
- Newly discovered accounts are added to `account_sources.csv`. Every registered account name is revalidated on each run; stable account IDs are retained if an identity check is inconsistent.
- Existing articles are deduplicated by normalized URL.
- Accounts and articles are processed sequentially with short randomized request delays. Large registries can therefore take a long time.
- Only one crawler instance may run in the same repository at a time.
- A `wechat-mp-tools` service started by the crawler is stopped automatically when the run finishes or is interrupted. If a compatible service was already running before the crawl, it is reused and left running.

## 4. Crawl results

```text
results/
├── summary.csv
├── articles/
│   └── <number>_<mp_id>_<account_name>/
│       └── <YYYY_MM_DD_HH_MM_SS>_<article_title>/
│           ├── content.txt
│           ├── metadata.json
│           ├── *.html
│           └── media/
└── record/
    └── <YYYY_MM_DD_HH_MM_SS>/
        ├── account_summary.csv
        ├── article_details.csv
        └── tools-log/
```

Directory timestamps use `YYYY_MM_DD_HH_MM_SS` in the Asia/Shanghai timezone. Account names and article titles are sanitized and may be shortened to remain safe as directory names.

Only successfully downloaded articles are added to `results/articles/` and the result CSV files. Failed articles are not listed in the result CSV files; their diagnostics remain in `tools-log/`.

### 4.1 `results/summary.csv`

This file is rebuilt from the local archive and contains one row for every registered account, including accounts with zero local articles.

| Column | Meaning |
| --- | --- |
| `编号` | Stable local account number |
| `公众号唯一ID` | Unique account ID returned by the upstream service |
| `公众号名称` | Current verified account name |
| `文章总数` | Total locally stored articles |
| `文章总存储（M）` | Total article storage in MiB |
| `最新文章时间` | Latest stored publication time |
| `最新文章标题` | Latest stored article title |

### 4.2 Per-run records

Each attempted crawl creates a timestamped directory under `results/record/`. A record is also written for failed or interrupted runs.

- `account_summary.csv`: account name, number of newly downloaded articles, account crawl duration in minutes, and storage added in MiB. A final total row summarizes the run.
- `article_details.csv`: account name, article title, and publication time for every article added by that run.
- `tools-log/crawler.log`: detailed crawler decisions, warnings, retries, and failures.
- `tools-log/wechat-mp-tools.log`: primary service output.
- `tools-log/we-mp-rss.log`: fallback output when the fallback is used.
- `tools-log/wechat-login-qr.png`: temporary login QR code; it is deleted after authentication or cleanup.

### 4.3 Per-article files

The exact files depend on which backend succeeds:

- `content.txt`: cleaned article text.
- `metadata.json` or `data.json`: title, source URL, publication time, and resource metadata.
- `*.html`: saved article HTML; a raw HTML copy may also be present.
- `media/`: downloaded cover and inline images when available.
- `fallback.html` and `fallback_metadata.json`: produced when `we-mp-rss` supplies the accepted fallback result.

Do not use directory names as the only identity for an article. URL metadata is the authoritative deduplication key.

## 5. Installation for Codex

### Requirements

- A Linux environment. The current implementation uses Linux-style process groups, file locking, and virtual-environment paths.
- Python 3.12, as specified by `.python-version`.
- Network access to GitHub, Python package indexes, WeChat article pages, and the upstream authentication service.
- A WeChat mobile app for the first QR-code authentication.
- Enough free disk space for two virtual environments, Playwright Chromium, and the article archive.

The recommended setup method is to ask Codex to install and verify the project. Use this prompt from the repository root:

```text
Set up this wx-crawl repository for local execution on Linux.

Use Python 3.12 as specified by .python-version. Read
install/third_party_versions.yaml, clone both repositories into third_party/
using the exact directory names and pinned commits recorded there, and do not
modify their tracked source files.

Create an independent .venv inside each third-party repository. Install the
wechat-mp-tools requirements plus install/requirements.txt into the
wechat-mp-tools environment. Install the we-mp-rss requirements into its own
environment, then install its matching Playwright Chromium browser and any
required Linux browser libraries.

Run `python3 src/crawl.py --check`. Confirm that both third-party Git
worktrees have no tracked changes, both environments pass `python -m pip
check`, the Chromium executable can launch headlessly, authentication storage
is excluded from Git, and no tool service remains running.
```

The expected third-party layout is:

```text
third_party/
├── wechat-mp-tools/
│   └── .venv/
└── we-mp-rss/
    └── .venv/
```

Python 3.12 is intentional: the pinned `wechat-mp-tools` dependencies require `mitmproxy` 12, and this project has been validated with Python 3.12.

The third-party repositories are pinned because `wx-crawl` uses some of their Python modules and internal classes through local adapters. Updating either upstream repository may require updating and retesting the adapters in `src/auth/` or `src/integrations/`. Keep upstream worktrees unmodified so the same pinned versions can be reproduced on another machine.

## 6. Crawl lifecycle and Git synchronization

The crawler separates portable account-source information from machine-local crawl state. A normal run follows these phases.

### 6.1 Discover and merge account sources

1. Read every cell in the configured input CSV and extract recognized WeChat article URLs.
2. Normalize URLs and remove duplicate inputs before making account-resolution requests.
3. Resolve each usable article URL to an upstream account ID and current account name.
4. Merge the resolved accounts into `account_sources.csv` by account ID:
   - If the account ID is new, append a row with the next stable local number, account ID, current name, and the input article as its example URL.
   - If the account ID already exists, keep its local number, refresh its name when necessary, and update its example URL.
   - Multiple input links from the same account produce one registry entry, while each distinct input article remains an explicit download candidate for that run.
5. Revalidate every existing registry row through its example URL. Account names may be updated; if the returned account ID conflicts with the stored ID, the crawler keeps the stored ID and writes a warning instead of silently changing account identity.
6. Rewrite `account_sources.csv` and rebuild `results/summary.csv` before article crawling begins.

An input CSV with no recognized link therefore means “add no new account”; it does not mean “crawl nothing” when the registry already contains accounts.

### 6.2 Crawl the complete registry

After the merge, the crawler processes every row in `account_sources.csv` in stable-number order:

1. Read locally stored article metadata and build the URL deduplication set for that account.
2. Retrieve history candidates according to `incremental` or `window` mode.
3. Add missing explicit articles from the registry example URL and the current input CSV, regardless of the history-window limit.
4. Try the primary downloader, validate the result, and use the fallback only when the primary result is unusable.
5. Move each successful article from temporary working storage into its final account directory.
6. Remove temporary candidate data, continue to the next article and account, then update the run reports and global summary.

This ordering ensures that newly discovered accounts become part of the persistent registry before the full account crawl starts.

### 6.3 What Git synchronizes

`account_sources.csv` is intentionally tracked by Git. Committing it lets another clone learn which Official Accounts are registered, along with their stable local numbers, IDs, current names, and example article links. This makes the source list easy to review and synchronize across machines.

The following state is intentionally not synchronized through Git:

- `results/articles/`: downloaded content, URL deduplication data, and incremental breakpoints.
- `results/record/` and `results/summary.csv`: machine-local run history and derived statistics.
- `src/auth/config/`: private authentication credentials.
- `third_party/`: reproducible upstream clones and virtual environments.

Consequently, a fresh clone can inherit the same account registry but has no local article breakpoint. In `incremental` mode, each such account is treated as having no local history and uses the latest `articles_per_account` entries as its initial download range. To continue from exactly the same incremental state on another machine, copy `results/articles/` separately; do not commit it to Git.

## 7. Troubleshooting

### No article URL is detected

- Confirm that `crawl.input_csv` points to an existing file whose extension is `.csv`.
- Put each complete `https://mp.weixin.qq.com/s/...` link in its own cell.
- Run `python3 src/crawl.py --check` to see the detected-link and registered-account counts.
- Remember that a non-empty registry continues to run even when the current input contains no new link.

### The QR code does not appear or login fails

- Read the exact QR path printed in the terminal and open the PNG before the five-minute timeout.
- Confirm the login in the WeChat app after scanning.
- Check the current run's `tools-log/wechat-mp-tools.log` and `tools-log/crawler.log`.
- If credentials have expired or a live request returns an authentication error, start a new run so the invalid account can be replaced through a fresh scan.

### The fallback browser fails

- Confirm that the `we-mp-rss` virtual environment contains the pinned Playwright version and its matching Chromium build.
- Install any missing Linux shared libraries required by Chromium.
- Review `tools-log/we-mp-rss.log`. Optional database-related warnings from the upstream tool do not necessarily mean that article extraction failed; use the final article validation result as the outcome.

### A run is slow

History pages, accounts, and articles are intentionally processed sequentially with request delays. Runtime depends on registry size, selected mode, article size, image count, retries, and fallback-browser use. Use `Ctrl+C` for a controlled stop rather than terminating the entire process group externally.

### Port 5200 is already in use

`wechat-mp-tools` uses the local address `127.0.0.1:5200`. Stop an unrelated process using that port, or verify that the existing service is a compatible `wechat-mp-tools` instance before starting the crawler.

## Acknowledgements

Special thanks to the maintainers and contributors of:

- [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools), which provides WeChat Official Account discovery, history retrieval, authentication, and primary article downloading.
- [we-mp-rss](https://github.com/rachelos/we-mp-rss), which provides browser-based article extraction used as the fallback when the primary result is incomplete.

`wx-crawl` integrates these projects without modifying their tracked source code.
