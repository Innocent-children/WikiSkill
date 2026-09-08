---
name: wikiskill
description: Use accumulated project Skills and record reusable outcomes from Codex business tasks through WikiSkill MCP. Apply when the user wants WikiSkill-assisted work or project experience capture; supports status, history and rollback requests.
---

# WikiSkill business work

For a business task using WikiSkill, call `wikiskill_context` with the absolute project
directory. Git worktrees accumulate into the same project. Read the returned `skill_md`
for relevant Skills and use that content for this task. Current user instructions and
the actual task scope take priority over learned guidance. Keep the loaded content
stable for the current task; later tasks receive published updates.

After obtaining a useful outcome, call `wikiskill_collect` with a stable `source_id`
(conversation ID plus an event label works well) and the observations worth retaining.
Each observation contains `problem`, `action`, `outcome`, and `lesson` strings. State
what was actually done and observed, including failures and unresolved outcomes.
Use `metadata` for timestamps, source links, log references and task labels. Remove
credentials and unrelated private content before submitting. Raw records retain the
submitted text. Reuse the same source ID when retrying the same submission; use a new
ID for a genuinely new outcome. Avoid copying an entire transcript when a focused
observation contains the reusable learning.

The MCP server receives only explicitly submitted observations. It cannot listen to
other tools or read every Codex conversation automatically. The server deduplicates
content and triggers Raw → Wiki and Wiki → Skill only at their separate configured
thresholds. Task completion alone does not request an optimization. Continue the
business task without waiting for background generation or asking the user to approve
each evolution.

For curated knowledge, `wikiskill_wiki` accepts complete named page bodies and separate
metadata. Use it when the user supplies or requests a knowledge-page update. Index,
log and timestamp updates belong in metadata. `wikiskill_status` shows pending counts
and jobs; `wikiskill_query` reads Raw, Wiki or reports and supports `key`, `offset` and
`limit`. Reports include the Codex session ID and local publication outcome. Report
generation and content publication are separate: `report_error` never means the Skill
was rolled back.

External Skills participate only after the user enables `manage_external`, lists their
absolute directories in `~/.wikiskill/config.json` under `external_skills`, and associates
each relevant project using `wikiskill_enroll`. The default manages generated Skills.

Use `wikiskill_history` to inspect diffs and full versions. When the user requests a
rollback, call `wikiskill_rollback` with the selected `skill_id`, `version_id`, and
`side` (`before` or `after`). A rollback retains the replaced content as another version.
For a failed batch, inspect its report before `wikiskill_retry`; `regenerate=true`
rebuilds an unpublished proposal from current content. Retrying a finished job only
resends its missing report. If a busy worker refuses a retry, keep the report and retry
after it finishes. Backend errors should be reported accurately without blocking
unrelated business work.
