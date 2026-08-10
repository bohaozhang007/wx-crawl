---
name: wechat-crawl-label-report
description: "Combine the WeChat crawler and article-labeling workflows: run a crawl, fully inspect and label the articles added by that run, then report only articles that match both a research project/guide application category and at least one technical domain such as drones, embodied intelligence, aerospace, satellites, large models, robots, or robotic arms. Use when an agent is asked to crawl and return a filtered article digest with title, categories, summary, and WeChat URL."
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

## 4. Return the report

For each selected article, read enough of its complete textual `content.txt` and
textual metadata to write a factual, concise summary of 1-3 sentences. Do not
open or inspect any image or other media while summarizing.
Do not invent details from the title or labels. Return a flat list in publish
time descending order. Each item must contain exactly these user-facing fields:

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
