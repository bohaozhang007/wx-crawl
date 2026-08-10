---
name: label-wechat-articles
description: Read the complete text of crawled WeChat Official Account articles, without opening images or other media, then write a validated label.json into each article directory. Use when an agent is asked to label, classify, tag, review, or backfill articles under /root/workspace/wx-crawl/results/articles by research-application type and technical domain.
---

# Label WeChat Articles

Process every missing or invalid `label.json` below
`/root/workspace/wx-crawl/results/articles`. Judge one article at a time. Never
classify from only its title, summary, filename, keyword grep, or a partial read.

## Workflow

1. Get the next unlabeled article, newest first:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py next
   ```

2. Inventory the text files in that article:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py inventory "<article-dir>"
   ```

3. Inspect all textual article content before deciding:

   - Read `content.txt` from beginning to end. Use chunks when necessary and
     continue until EOF; do not rely on truncated command output.
   - Read `metadata.json` and the useful article fields in every textual
     JSON/HTML file. Use rendered/raw HTML or `data.json` to recover visible
     text, captions, or links omitted from `content.txt`. Ignore JavaScript,
     CSS, URLs, menus, advertisements, and related-article recommendations as
     evidence.
   - Do not open, OCR, thumbnail, or otherwise inspect images. Do not inspect
     video, audio, PDF, or other binary/media assets. Classify strictly from
     text; report only unreadable textual files as blockers.

4. Choose both classification layers using the rules below.

5. Write the label with the bundled writer. Repeat `--domain` for every match;
   omit it when no domain matches:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py write \
     "<article-dir>" \
     --application-type "科研指南申请" \
     --domain "无人机" \
     --domain "大模型"
   ```

6. Repeat until `next` prints nothing. Do not overwrite an existing valid label
   unless the user explicitly requests relabeling; use `--replace` only then.

## Application type

Set `application_type` to exactly one of:

- `科研指南申请`: The meaningful article text contains a
  research/funding guide concept such as `指南`, `申报指南`, `征集指南`,
  `项目指南`, or an equivalent. Prefer this value when both guide and project
  terms match.
- `科研项目申请`: No research-guide concept matches, but meaningful text
  contains a research project/application concept such as `项目`, `课题`,
  `专项`, `计划`, `基金`, `申报`, `申请`, `征集`, `揭榜挂帅`, `立项`,
  or an equivalent.
- `都不是`: Neither rule matches.

Use high recall as requested: one meaningful occurrence in the article's actual
text is enough. Do not require the application or project to be the main topic.
Do not count a generic software user guide, HTML/code token, navigation,
advertisement, or related-article title as a research-guide match.

## Technical domains

Set `domains` to an array containing every matching canonical value below. A
meaningful occurrence in text is enough even when it is not the article's main
topic. Judge each domain independently; do not infer one label only because
another label matched.

- `无人机`: includes 无人飞行器, UAV, drone, and unambiguously drone-related
  low-altitude systems.
- `卫星`: includes satellite, 星座, 遥感卫星, and satellite-context 北斗.
- `具身智能`: includes 具身, embodied AI, and embodied agent systems.
- `大模型`: includes LLM, large/foundation models, GPT-like models, and
  multimodal large models.
- `空天`: includes 航空航天, 航天, aerospace, spaceflight, rockets, spacecraft,
  and directly equivalent concepts.
- `机器人`: includes robot, 人形机器人, 四足机器人, 工业/服务机器人, and
  equivalent robotic systems.
- `机械臂`: includes robotic arm, manipulator, and equivalent arm systems.

Use `[]` when none match. Never write `null`, `"None"`, free-form synonyms, or a
domain outside this list.

## Output schema

Write exactly one `label.json` in the article directory with only these fields:

```json
{
  "application_type": "科研指南申请",
  "domains": [
    "无人机",
    "大模型"
  ]
}
```

The writer validates values, orders domain labels canonically, and replaces the
file atomically. Do not modify any crawled source content or media.

## Completion checks

Run both commands after the batch:

```bash
python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py count
python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py validate
```

Completion requires `pending` to be zero and every existing label to validate.
Report totals for valid labels, pending articles, and blocked unreadable text files.
