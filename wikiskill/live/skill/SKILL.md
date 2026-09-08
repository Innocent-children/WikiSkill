---
name: wikiskill
description: Query accumulated project knowledge and Skills, or directly write a Wiki page when the user asks to capture reusable knowledge.
---

Use WikiSkill MCP for project knowledge queries and user-requested Wiki authoring. Supply the current project's absolute directory.

- `wikiskill_context` reads the project's generated Skill.
- `wikiskill_query` reads Raw, Wiki, or reports with pagination.
- `wikiskill_wiki` saves named Wiki pages using complete bodies.
- `wikiskill_status` and `wikiskill_history` explain execution and publication history.

Raw collection is performed outside the model by the background transcript reader. Continue the user's task normally. The WebUI owns manual conversion, model settings, history operations and copying generated Skills to Codex.

When writing Wiki, preserve actual outcomes and scope, and use concrete reusable instructions. Report only operations confirmed by the MCP result.
