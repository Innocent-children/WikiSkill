# Paper prompt adaptations

`maintainer.md` and `proposer.md` preserve the original extracted Appendix E.2/E.3 text. `sources.json` records their hashes, paper URL and attribution. Runtime adapters append the following explicit integration requirements; the original files remain unchanged.

- Files are virtual views of frozen SQLite/Skill snapshots. Wiki patterns map to `wiki/patterns/<name>.md`; index and log have dedicated storage, and history/feedback are available through `wiki/skill-impact.md` and referenced history documents.
- Maintainer output uses the paper's create_patterns/update_patterns/update_index/append_log fields. Ollama receives the actual schema in `format`, with `think=false` and temperature 0. Existing patch targets are selected from exact unique source lines, paragraphs or sections to prevent transcription mistakes. The host associates changed pages with the selected Raw batch.
- Proposer uses native read_file and finish tools. The finish function arguments directly form the proposal object; there is no extra proposal wrapper. Context-specific names and readable paths are declared in the tool schema. All writes remain host-owned.
- The current batch authorizes one Skill name. Existing metadata, unrelated assets and prior PURPOSE history are preserved. SKILL.md must include YAML name and description. A Wiki pattern is not an active Skill; source-free acknowledgements are not task procedures, and orchestration instructions are never used as invented task knowledge.
- The paper requires at least four traces. A selected batch with fewer than four uses all available traces; traces group available source records by execution turn. No extra traces are invented or silently imported.
- Actual published versions and human feedback replace benchmark validation scores. A publication is not an acceptance decision based on measured performance. Negative-feedback history includes the candidate content and diff.
- User-selected original content is not sampled or truncated. No application token or call-count limits are introduced. Model/protocol failures stop the batch and preserve inputs and diagnostic responses for explicit retry.
- Explanations follow the source task language; exact commands, identifiers and original patch targets retain their spelling.
