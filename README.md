# wx-crawl

[English](README.md) | [简体中文](README-CN.md)

`wx-crawl` downloads WeChat Official Account article histories and article content. It uses [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools) as the primary history and article backend, and falls back to [we-mp-rss](https://github.com/rachelos/we-mp-rss) when the primary article download is missing or incomplete. Many thanks to both projects for providing the core upstream capabilities that make this workflow possible.

## 1. What it does

Give `wx-crawl` one or more WeChat Official Account article links, and it will identify the corresponding accounts and download their historical articles. The supplied article links are also downloaded explicitly.

The crawler maintains a local account registry, discovers new articles from each account's history, avoids downloading the same URL twice, and stores article text, HTML, metadata, and images in a structured local archive.

## 2. Project structure

```text
wx-crawl/
├── src/                         Crawler, authentication, and tool adapters
├── install/
│   ├── requirements.txt         Dependencies used by wx-crawl
│   └── third_party_versions.yaml
│                                Upstream repositories and pinned commits
├── third_party/                 Local upstream clones and virtual environments
├── config.yaml                  User-facing crawl configuration
├── input_template.csv           Minimal input CSV example
├── account_sources.csv          Persistent registry of discovered accounts
├── results/                     Downloaded articles and run records
└── .python-version              Recommended Python version
```

`third_party/`, `results/`, and `src/auth/config/` are local runtime data and are excluded from Git. Authentication tokens are stored under `src/auth/config/`; they must never be committed.

## 3. Quick start

1. The simplest option is to add one or more WeChat article URLs to `input_template.csv`, with each URL in its own cell.
2. Optionally, use any other CSV file and set `crawl.input_csv` in `config.yaml` to its path. The CSV requires no specific columns, headers, or layout; **each WeChat article URL must appear by itself in a cell**.
3. Select a crawl mode:
   - `incremental`: for an account that already has locally stored articles, download every article published after its most recent local article. For a newly discovered account with no local breakpoint, download its latest `articles_per_account` articles.
   - `window`: use the latest `articles_per_account` articles as the download range for every account. Articles already stored locally are skipped by URL.
4. Check the installation and configuration:

   ```bash
   python3 src/crawl.py --check
   ```

5. Start a crawl:

   ```bash
   python3 src/crawl.py
   ```

### Notes

- On the first authenticated run, the terminal prints the exact path of a QR-code PNG under the current run's `tools-log` directory. Scan it with WeChat and confirm the login; the credentials are then stored locally for later runs.
- Every input article URL is downloaded explicitly, even when it falls outside the selected history window.
- Newly discovered accounts are added to `account_sources.csv`, and all known account names are verified and refreshed on every run.
- Existing articles are deduplicated by URL.
- Tool services are stopped automatically when the run finishes or is interrupted.

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

Only successfully downloaded articles are added to the article collection and reports.

### `results/summary.csv`

One row per registered account:

| Column | Meaning |
| --- | --- |
| `编号` | Stable local account number |
| `公众号唯一ID` | Unique account ID returned by the upstream service |
| `公众号名称` | Current account name |
| `文章总数` | Total locally stored articles |
| `文章总存储（M）` | Total article storage in MiB |
| `最新文章时间` | Latest stored publication time |
| `最新文章标题` | Latest stored article title |

### Per-run records

Each run gets a timestamped directory under `results/record/`.

- `account_summary.csv` contains the account name, number of newly downloaded articles, crawl duration in minutes, and new storage in MiB.
- `article_details.csv` contains the account name, article title, and publication time for every article added by that run.
- `tools-log/` contains crawler and upstream-tool diagnostic logs. A temporary login QR code may appear here and is deleted after authentication.

### Per-article files

- `content.txt`: cleaned article text.
- `metadata.json` or `data.json`: title, source URL, publication time, and resource metadata.
- `*.html`: saved article HTML; a raw HTML copy may also be present.
- `media/`: downloaded cover and inline images.
- `fallback.html` and `fallback_metadata.json`: produced when `we-mp-rss` is used as the fallback.

## 5. Installation for Codex

The recommended setup method is to ask Codex to install and verify the project. Use this prompt from the repository root:

```text
Set up this wx-crawl repository for local execution.

Use Python 3.12 as specified by .python-version. Read
install/third_party_versions.yaml, clone both repositories into third_party/
using the exact directory names and commits recorded there, and do not modify
their tracked source files.

Create an independent .venv inside each third-party repository. Install the
wechat-mp-tools requirements plus install/requirements.txt into the
wechat-mp-tools environment. Install the we-mp-rss requirements into its own
environment, then install its Playwright Chromium browser.

Finally run `python3 src/crawl.py --check`, confirm both third-party Git
worktrees are clean, and make sure no tool service remains running.
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

## Acknowledgements

Special thanks to the maintainers and contributors of:

- [wechat-mp-tools](https://github.com/x554960766/wechat-mp-tools), which provides WeChat Official Account discovery, history retrieval, authentication, and primary article downloading.
- [we-mp-rss](https://github.com/rachelos/we-mp-rss), which provides browser-based article extraction used as the fallback when the primary result is incomplete.

`wx-crawl` integrates these projects without modifying their tracked source code.
