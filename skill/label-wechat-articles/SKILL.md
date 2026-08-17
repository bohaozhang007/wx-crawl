---
name: label-wechat-articles
description: Run or review API-backed labeling of crawled WeChat articles with a high-precision, traceable decision tree for research project/guide opportunities and target technical domains. Use when labeling, classifying, reviewing, backfilling, or explaining KEEP/DROP/REVIEW decisions under /root/workspace/wx-crawl/results/articles.
---

# Label WeChat Articles

## Automated batch workflow

Use the Python runner for normal batch labeling. It reads the rules and complete
article text, calls the resolved Hermes or explicitly overridden model, validates the
structured response and quoted evidence, and atomically writes `label.json`.

1. By default, inherit the active Hermes provider, model, base URL, and provider
   API key from `~/.hermes/config.yaml` and `~/.hermes/.env`. Use `LABEL_PROVIDER`,
   `LABEL_MODEL`, `LABEL_BASE_URL`, or `LABEL_API_KEY` only for an intentional
   per-labeling override. Never copy a key into tracked project files.
2. Validate configuration without an API request:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli --check
   ```

3. For a pipeline batch, test one pending article from its exact run:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli \
     --run-dir /root/workspace/wx-crawl/results/record/<timestamp> --limit 1
   ```

4. Run all missing or invalid labels in that batch only after the test succeeds:

   ```bash
   /root/workspace/wx-crawl/.venv/bin/python -m src.labeling.cli \
     --run-dir /root/workspace/wx-crawl/results/record/<timestamp>
   ```

Existing valid v2 labels that already contain `summary` are skipped. A valid v2 label
without `summary` is upgraded through the same model call; v1 labels are unsupported
and must be deleted rather than migrated in place. Use `--replace` only when the user
explicitly requests relabeling. Do not proceed to selection when the result reports failures.
The unscoped command labels every pending archive article and must not be used by the
pipeline orchestrator.

By default stdout is one compact JSON summary. Per-article outcomes are written to
the reported `details_file` (`labeling_result.json` for a run). Use `--verbose` only
for a human-requested diagnosis; normal Agent orchestration must use the compact
summary and open the details file only on failure.

## Manual review workflow

Use the following manual procedure only for a user-requested review, an explanation,
or when the API runner cannot be used. Do not manually duplicate a successful batch.

Process each article independently. Read all textual evidence before deciding.
Never classify from only a title, filename, summary, keyword grep, or partial read.

## Load the rules

Before labeling, read these references completely:

1. `references/decision-tree.md` — authoritative node order, terminal codes, and
   KEEP/DROP/REVIEW boundaries.
2. `references/research-profile.yaml` — canonical domains and task-scope matching rules.

Read `references/negative-cases.md` when calibrating ambiguous or high-false-positive
content. Treat `decision-tree.md` as the source of truth if examples conflict.

## Workflow

1. Get the next missing or invalid label, newest first:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py next
   ```

2. Inventory its text files:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py inventory "<article-dir>"
   ```

3. Read all textual article content:

   - Read `content.txt` from beginning to end. Continue in chunks until EOF.
   - Read `metadata.json` and useful visible text in other JSON/HTML files when
     `content.txt` omits captions, links, or sections.
   - Ignore scripts, CSS, navigation, advertisements, footers, and related-article
     recommendations as decision evidence.
   - Do not open, OCR, or inspect images, video, audio, PDF, or other binary media.
   - Route missing decisive text or a decisive inaccessible attachment through the
     appropriate REVIEW node; do not guess.

4. Execute `decision-tree.md` in order. Stop at the first terminal node. Record:

   - every visited node in `decision_path`;
   - the terminal node as `reason_code`;
   - an article-specific explanation in `reason`;
   - short, located source evidence supporting the conclusion.

5. Determine `application_type` independently from the final direction decision:

   - Use `科研指南申请` for a formal guide-led research call.
   - Use `科研项目申请` for another qualifying research project/task call.
   - Use `都不是` when the article does not establish a qualifying research call.
   - A qualifying research call outside every target domain is still a positive
     application type, but its final decision is DROP and `domains` is `[]`.

6. Assign domains only from the task evidence zone defined by `T1`. A term in policy
   background, biographies, issuer introductions, past results, unrelated roundup
   entries, footers, or related links does not match. Do not infer one domain from
   another; all inheritance rules default to false in `research-profile.yaml`.

7. Write the v2 label atomically. Repeat `--path-step`, `--evidence`, and `--domain`
   as needed. Evidence arguments are `TYPE LOCATION TEXT`:

   ```bash
   python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py write \
     "<article-dir>" \
     --decision KEEP \
     --path-step E1:PASS \
     --path-step O1:PASS \
     --path-step O2:PASS \
     --path-step R1:PASS \
     --path-step A1:PASS \
     --path-step T1:PASS \
     --path-step D1:PASS \
     --path-step K1 \
     --reason-code K1 \
     --reason "文章开放科研项目申报，并在任务要求中直接要求研发多模态大模型。" \
     --summary "文章发布科研项目申报通知，明确申报期限、材料要求和多模态大模型研究任务。" \
     --evidence solicitation "申报要求" "申报截止时间为……" \
     --evidence domain "研究内容" "研发多模态大模型训练与推理方法" \
     --application-type "科研项目申请" \
     --domain "大模型"
   ```

   Use `--replace` only when the user requests relabeling or when replacing a legacy
   v1/invalid label after re-reading the complete article.

8. Repeat until `next` prints nothing.

## Label contract

Write exactly these v2 fields:

```json
{
  "schema_version": 2,
  "tree_version": "1.0",
  "decision": "DROP",
  "decision_path": ["E1:PASS", "O1:O1-D1"],
  "reason_code": "O1-D1",
  "reason": "文章公布的是已完成评审的拟入选名单，没有开放新的申报机会。",
  "summary": "文章公示已完成评审的拟入选项目名单，并说明公示期限和意见反馈方式。",
  "evidence": [
    {
      "type": "negative",
      "text": "现将拟入选项目名单予以公示",
      "location": "正文第一段"
    }
  ],
  "application_type": "都不是",
  "domains": []
}
```

The API-backed Python labeler must generate `summary` in the same response for every
KEEP, DROP, or REVIEW article. Keep it factual, 1-3 sentences, normally 100-200 Chinese
characters, and separate from the decision rationale. Existing summary-less v2 labels
remain readable for migration, but normal batch labeling upgrades them before reporting.

Use evidence types only from:

- `solicitation`: current application/action evidence;
- `research_task`: research or technical task evidence;
- `domain`: target direction evidence inside the task scope;
- `negative`: evidence establishing a DROP branch;
- `missing_evidence`: a missing/unreadable artifact establishing REVIEW.

For KEEP, include at least one `solicitation` and one `domain` evidence entry and
explain both actionability and task-level domain relevance. For DROP and REVIEW,
state the concrete article-specific failure or uncertainty; never use a generic
sentence such as “不符合要求”.

## Selection boundary

Labeling performs the semantic judgment. Downstream selection performs no semantic
reinterpretation:

- KEEP → eligible for summary and database import;
- DROP → excluded;
- REVIEW → excluded from import and preserved for human review; never auto-prune it.

## Completion checks

Run:

```bash
python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py purge-v1 --confirm-delete
python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py count
python3 /root/workspace/wx-crawl/skill/label-wechat-articles/scripts/label_articles.py validate
```

Require zero pending labels and zero invalid labels before automatic selection. Treat
legacy v1 labels as pending and delete them; never mechanically promote them to v2 or KEEP.
