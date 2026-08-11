---
name: wechat-crawl-label-report
description: "Combine WeChat crawling, text-only article labeling, SQLite persistence, and filtered reporting: run a crawl, fully inspect and label that run's articles, persist matching articles and summaries to results/articles.sqlite3, remove non-selected current-run source directories after verified import, and report only articles matching both a research project/guide application category and at least one technical domain. Use when an agent is asked to crawl, classify, store, clean up, or return a filtered WeChat article digest."
---

# WeChat Crawl Label Report

Run the complete workflow in order. Keep the current run boundary; do not mix
old articles into the report unless the user explicitly requests a backfill.

## 1. Crawl

Follow `$wechat-official-account-crawler` from
`/root/workspace/wx-crawl/skill/wechat-crawl/SKILL.md`. Use its current
Procedure, process tracking, authentication, and QR-delivery rules, and wait for
the crawl to finish. Record the run directory printed by the crawler, or resolve
the newest `results/record/<timestamp>/` directory after completion.

Do not proceed to reporting after a failed or interrupted crawl unless the user
asks to report partial results. A successful crawl may still have zero new
articles; in that case report that no new candidates were available.

## 2. Label this run's articles

Use the inspection and classification rules from
`$label-wechat-articles` at
`/root/workspace/wx-crawl/skill/label-wechat-articles/SKILL.md`.

First enumerate only the articles listed in this run's
`article_details.csv`:

```bash
python3 /root/workspace/wx-crawl/skill/wechat-crawl-label-report/scripts/select_articles.py candidates \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

For every returned article directory, run the labeling workflow completely:
read all text to EOF, but do not open or inspect images, video, audio, or other
binary assets. Write its validated `label.json` with the second skill's writer.
Do not label from the title or grep alone. Do not use the second skill's
all-history loop unless the user explicitly asks for a backfill.

## 3. Select the intersection

After all current-run labels validate, run:

```bash
python3 /root/workspace/wx-crawl/skill/wechat-crawl-label-report/scripts/select_articles.py matches \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

An article is eligible only when both conditions hold:

1. `label.json.application_type` is `科研项目申请` or `科研指南申请`.
2. `label.json.domains` is a non-empty array.

`都不是`, an empty domain array, missing labels, invalid labels, missing title,
or missing URL must not appear in the report. The selector accepts only the
canonical values defined by the labeling skill and reports skipped records
separately.

## 4. Persist the filtered report

For each selected article, read enough of its complete textual `content.txt` and
textual metadata to write a factual, concise summary of 1-3 sentences. Do not
open or inspect any image or other media while summarizing.
Do not invent details from the title or labels. First save the summaries as a
JSON object keyed by the article URL at:

```text
/root/workspace/wx-crawl/results/record/<timestamp>/article_summaries.json
```

Example:

```json
{
  "https://mp.weixin.qq.com/s/example": "文章介绍……，并说明申报方向、截止时间和材料要求。"
}
```

Then generate the validated machine-readable report:

```bash
python3 /root/workspace/wx-crawl/skill/wechat-crawl-label-report/scripts/select_articles.py write-report \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>" \
  --summaries "/root/workspace/wx-crawl/results/record/<timestamp>/article_summaries.json"
```

The command writes `filtered_articles.json` in the run directory. It fails if a
selected article has no summary, an invalid label, or a missing URL. This file
is the source of truth for database import and must be generated before any
cleanup.

## 5. Import and clean up

Initialize the database when needed:

```bash
/root/workspace/wx-crawl/wx-crawl-db init
```

After `filtered_articles.json` is successfully written, import it:

```bash
/root/workspace/wx-crawl/wx-crawl-db ingest \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>"
```

The importer reads and stores the full text, metadata, URL, title, labels, and
summary. It is idempotent by URL. After a successful import, remove the
non-selected article directories from this run as part of the normal workflow:

```bash
/root/workspace/wx-crawl/wx-crawl-db prune \
  --run-dir "/root/workspace/wx-crawl/results/record/<timestamp>" \
  --confirm-delete
```

Without `--confirm-delete`, `prune` only prints what would be deleted. Never
delete article directories before the import succeeds. Omit the cleanup only
when the user explicitly asks to retain the source directories.

## 6. Return the report

Return a flat list in publish time descending order. Each item must contain exactly these user-facing fields:

```markdown
### 文章标题
- 分类：科研项目申请；领域：无人机、大模型
- 摘要：用 1-3 句话概括文章具体讲了什么、申请/指南内容与相关技术领域。
- 链接：https://mp.weixin.qq.com/s/...
```

Keep the original title and URL from `metadata.json`. Preserve the two layers
separately: show one application category and all matched domain categories.
When no article satisfies both conditions, say so explicitly and do not include
non-matching articles merely because their title contains a keyword.

If an article's text could not be fully read, leave it out and report its path
and unreadable text file after the main results. Do not write a speculative
summary.
