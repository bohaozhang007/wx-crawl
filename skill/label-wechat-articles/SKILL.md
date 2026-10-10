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

Existing valid v2 labels that already contain `summary`, structured `deadline`, and
`importance` are skipped. A valid v2 label missing any of these fields is upgraded
through the same model call; v1 labels are unsupported
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
   Apply `excluded_task_families` before positive domain matching: satellite/data/
   remote-sensing/communications/navigation application projects remain excluded even
   when they use large models or agents. Preserve the explicit exception for space
   robots/manipulators whose core task is in-orbit or space assembly, manufacturing,
   construction, repair, maintenance, inspection, grasping, manipulation, or servicing.

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
  "tree_version": "1.2",
  "profile_version": "1.3",
  "geography": {"status":"eligible","regions":["national"],"evidence":"面向全国征集"},
  "decision": "DROP",
  "decision_path": ["E1:PASS", "O1:O1-D1"],
  "reason_code": "O1-D1",
  "reason": "文章公布的是已完成评审的拟入选名单，没有开放新的申报机会。",
  "summary": "文章公示已完成评审的拟入选项目名单，并说明公示期限和意见反馈方式。",
  "deadline": {
    "status": "missing",
    "raw_text": "",
    "timestamp": null,
    "timezone": "Asia/Shanghai"
  },
  "importance": {
    "level": "not_applicable",
    "reason": "文章不是当前开放申报的科研项目或指南，不评估项目重要度。",
    "factors": {
      "deadline_urgency": "unknown",
      "project_significance": "unknown",
      "amount_level": "unknown",
      "amount_raw_text": "",
      "domain_fit": "none"
    }
  },
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

The same response must generate `deadline`. Use `confirmed` only for an unambiguous
final applicant submission deadline and convert it to an Asia/Shanghai Unix timestamp.
Use `ambiguous` with verbatim source text and a null timestamp when it cannot be converted,
or `missing` when absent. Only confirmed deadlines are scheduled.

The same response must also generate `importance`. Only KEEP articles use `high`,
`medium`, or `low`; DROP and REVIEW use `not_applicable`. Apply the prompt's explicit
deadline urgency, project significance, stated amount, and domain-fit rules. Preserve
stated amount text verbatim and never infer an undisclosed amount. High alerts are sent
by Python after the label is validated, not by the Agent.

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

## TikHub sample runner

The opt-in TikHub sample pipeline calls the same model/validator directly without
importance alerts. It supplies a 16384-token output ceiling for Chat Completions to
leave room for reasoning plus the JSON label. Production labeling reads labeling.max_output_tokens (currently 16384); absent configuration retains the 4096-token compatibility default. Empty JSON errors include finish_reason for diagnosis. Sample
recovery is `python -m src.crawler.tikhub_sample --resume <sample-run>`; valid labels
are reused, evidence validation remains mandatory, and per-attempt usage is retained.

## Current-version requirement

`schema_version` is the label format (currently 2); `tree_version` is the decision
rule version (currently 1.2). Always read the version from references/decision-tree.md.
They are independent, and tree 1.0 does not mean schema v1. Both schema v1 and an old
tree version are invalid for current work. The runner must relabel them from full
text, never skip them as valid or mechanically edit a version number. The shared
validator reloads the tree definition so a long-lived process cannot reuse a stale
version cache. Old source files may remain pending until replacement validates.
Importance alerts must not send labels that fail current-version validation.


## Latest labeling contract (2026-09-08)

Read authoritative versions from `src.labeling.schema.current_contract()` on each run.
Currently label schema is 2, decision tree is 1.2, research profile is 1.3; these
are independent versions. Every label must include `profile_version` as well as
`schema_version` and `tree_version`. Missing or old versions require actual
relabeling; never stamp a new version onto an old decision.

Reports carry `label_schema_version`, `tree_version`, and `profile_version`, even
when empty. Import rejects old reports and revalidates the source KEEP label;
SQLite stores that full validated label as provenance. Sync and deadline reminders
verify current decisions before any outbound action. Count equality alone does
not certify rule-version consistency. Old or unavailable source decisions require
relabeling/reconciliation, not silent acceptance or deletion.

Completion and backfill coverage are reusable only when their `label_contract`
equals the current contract. Old unversioned markers do not certify completion.
Use `pipeline_state.py list-pending --json` for pending discovery; do not independently
skip a batch merely because its status says completed or its ID appears in coverage.
For pruned archives, recover source articles before reclassification; report the
migration as blocked until evidence is restored. Do not repeatedly retry missing
source data or declare a global migration complete based on a new batch.


## Deadline eligibility gate (2026-09-08)

Before selecting an opportunity or sending a high-importance alert, evaluate its
structured deadline against the current clock using src.labeling.eligibility.deadline_expired.
A confirmed positive timestamp at or before now excludes the article, regardless
of KEEP/high or project significance. Future, missing and ambiguous deadlines
pass this gate and still require the normal decision/importance checks. Do not
infer expiration from an ambiguous date or the model's cached urgency factor.
The selector records selection=expired; alerts report skipped_expired. Import
rechecks expiration to reject reports that became stale after selection. Retain
source labels and archives; do not rewrite the model decision merely due to time.
This is a dynamic downstream filter, not a new label schema/tree/profile version.


## Geographic scope gate (2026-09-16)

Current authoritative contract: schema=2, tree=1.2, profile=1.3. Read current_contract()
instead of pinning these example versions. geography is required, with status
eligible/out_of_scope/unclear, regions (national/beijing/zhejiang/other/unknown),
and verbatim evidence. KEEP must pass G1 and have eligible geography with evidence.
The tree/profile upgrade invalidates historical labels without geographic review;
never stamp new versions or infer national scope from a national sponsor alone.

Only nationwide national programs and Beijing/Zhejiang local programs (including
subordinate cities/districts) are eligible. Other regional special programs, including
NSFC regional innovation joint fund (Jiangxi), are excluded even if national bodies
sponsor them or applicants nationwide may participate. Publisher location, event
venue, collaborator addresses and incidental region mentions are not project scope.
Mixed guides require a clearly independent eligible track with source evidence.
Unclear geographic scope means REVIEW, no selection/import/high alert/deadline alert.
Unknown deadlines still pass the separate deadline gate; do not confuse the two.

Selection reports include geography, and import compares it to the validated source.
Reminder scheduling and dispatch exclude other/unclassified geography, cancel pending
reminders, and report skipped_geography. Verified relabeling and reimport can restore
reminders cancelled solely for missing geography. No direct Agent reminder sends may
bypass these program checks. Existing historical DB/table records are not silently
removed; reconcile them against new labels before syncing. Do not claim old records
or completion/coverage markers meet the new contract. Preserve migration progress.

The manual label writer now requires --geography with a JSON object, for example
'{"status":"eligible","regions":["beijing"],"evidence":"北京市科技计划项目"}'.
Evidence must be from the actual article, not copied from this example.


## Project intake fields (2026-10-09)

Do not modify 项目入库表单 or its DingTalk automation. Enrich only 爬取公众号情况日志.
The seven additional text columns are 正式项目名称、主管部门、申报金额（万元）、申报开始日期、
申报截止日期、申报要求摘要、预期成果. Dates use YYYY-MM-DD; amounts use decimal RMB万元.
Existing url/attachments remain the reference-link/attachment sources.

New model outputs include optional project_intake (independent protocol version 1).
Every nonempty field needs verbatim body evidence, validated by project_intake.normalize.
Text values must be copied from that evidence; only unambiguous full dates and exact
per-project currency conversions are normalized. Unknown fields are omitted, not
zero or guessed text. No title fallback, no publisher-to-authority mapping, no
inferred year, no total-budget/cap/range-to-fixed-amount conversion. An unresolved
formal project identity means the entire extraction remains empty.

Do not fill 项目类别 or 紧急程度 until their definitions are confirmed. application_type
is not 纵向/横向; importance_level is not form urgency. Internal actor, organization,
state and recommendation fields remain human-managed. Do not map a general article
summary to 申报要求摘要 without explicit application conditions.

SQLite stores project_intake_json separately from decision labels. Existing decision
contracts are unchanged and need not be relabeled just to enrich these fields.
Sync revalidates evidence against stored body text, omits blank values, and fills
intake columns only when their remote values are empty. Even --mode full cannot
clear unknown values or replace human-entered intake values.

Optional historical enrichment (does not send alerts, change decisions or sync):
`.venv/bin/python -m src.labeling.enrich_intake --output <audit.json>` previews;
add `--article-id <id>` or `--limit <n>` to scope and `--apply` to persist with a
SQLite backup. Inspect the audit, then use the existing sync entry point. Failed
extractions remain unmodified. Do not overwrite or rebuild the target intake form.
