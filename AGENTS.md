# Repository integration rule

Changes to command entry points, CLI arguments or JSON output schemas,
authentication, pipeline stages, result paths, or failure semantics are not complete
until the DingTalk -> Hermes Agent call chain is synchronized.

For every such change:

1. Update the affected project Skill under `skill/`.
2. Check the Skill actually loaded under `/root/.hermes/skills/`; update an independent
   installed copy while preserving its local safety rules, or verify its symlink still
   targets this repository.
3. Check active prompts in `/root/.hermes/cron/jobs.json` for stale commands, arguments,
   or JSON field paths, and update them when affected.
4. Keep normal Agent automation on compact JSON stdout. Use `--verbose` only for an
   explicit human diagnosis, and store article-level details in files.
5. Validate the changed Skill, active cron JSON, relevant CLI contract, and tests before
   reporting completion.

