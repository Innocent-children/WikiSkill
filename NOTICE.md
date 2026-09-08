# Attribution

The seven Appendix E prompts in `wikiskill/prompts/` are reproduced from:

Liyan Tang, Cyrus Rashtchian, Chun-Sung Ferng, Andrew Tomkins, Da-Cheng Juan,
and Tu Vu. **WikiSkill: Compiling Agent Experience into Persistent Knowledge
for Skill Evolution**, arXiv:2608.27454v1, 2026.

- Paper: https://arxiv.org/abs/2608.27454v1
- Prompt source: https://arxiv.org/html/2608.27454v1#A5
- License: Creative Commons Attribution 4.0 International,
  https://creativecommons.org/licenses/by/4.0/

These files contain the original text extracted from the page's embedded
plain-text downloads. Template variables are filled at runtime. File hashes
and the extraction method are recorded in `wikiskill/prompts/sources.json`
and `scripts/extract_prompts.py`. The generic demonstration prompt and the
explicit no-wiki ablation instructions are project-authored.

ALFWorld is used through its installed package, not copied into this project:
https://github.com/alfworld/alfworld (MIT).

SpreadsheetBench's task and scoring conventions are referenced from:
https://github.com/RUCKBReasoning/SpreadsheetBench (CC BY-SA 4.0).
The adapter and comparison implementation here are independently written;
benchmark datasets remain subject to their own licenses.

The OfficeQA scorer `wikiskill/benchmarks/vendor/officeqa_reward.py` is included
from databricks/officeqa commit `7b9a3c154ef9fb40215bb67934afc43e6799de16`
under Apache-2.0. Its license is included as `vendor/LICENSE-OFFICEQA` and its
source hash is recorded in `vendor/sources.json`.

The SealQA auto-rater template is extracted from the embedded code listing in
Appendix C of Pham et al., *SealQA: Raising the Bar for Reasoning in
Search-Augmented Language Models*, https://arxiv.org/html/2506.01062v3.
Its separate extraction record is `wikiskill/prompts/grader-sources.json`.
