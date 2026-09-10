# Attribution

The original prompts in `wikiskill/live/prompts/maintainer.md` and `proposer.md`
are reproduced from Appendix E.2 and E.3 of:

Liyan Tang, Cyrus Rashtchian, Chun-Sung Ferng, Andrew Tomkins, Da-Cheng Juan,
and Tu Vu. **WikiSkill: Compiling Agent Experience into Persistent Knowledge
for Skill Evolution**, arXiv:2608.27454v1, 2026.

- Paper: https://arxiv.org/abs/2608.27454v1
- Method and prompts: https://arxiv.org/html/2608.27454v1
- License: Creative Commons Attribution 4.0 International,
  https://creativecommons.org/licenses/by/4.0/

Prompt hashes and source sections are recorded in `wikiskill/live/prompts/sources.json`.
Project-authored integration additions are documented in [ADAPTATIONS.md](wikiskill/live/prompts/ADAPTATIONS.md)
and implemented in `wikiskill/live/generation.py`; they are appended separately from the original prompts.

The benchmark implementations, vendored scorers and experiment commands remain
excluded from this distribution. This project does not claim to reproduce the
paper's experimental scores.

Ollama integration uses its native chat, structured output and tool calling APIs:
https://docs.ollama.com/api/chat
https://docs.ollama.com/capabilities/structured-outputs
https://docs.ollama.com/capabilities/tool-calling
