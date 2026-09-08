# WikiSkill 功能对应清单

依据 [WikiSkill arXiv:2608.27454v1](https://arxiv.org/html/2608.27454v1) 的正文、算法 1、附录和实验分析实现。

| 论文能力 | 实现位置 | 状态 |
| --- | --- | --- |
| Raw / Wiki / Skill 三层、原始轨迹只追加 | `storage.py` / `evolution.py` | 已实现，含工具、评分与文件产物 |
| 空 Skill、初始验证、全量训练 | `EvolutionEngine` | 已实现 |
| Skill 全文注入、默认推理隔离 Wiki | `InferenceAgent` / 任务工具 | 已实现 |
| 五失败、三成功抽样与 15,000 字符上限 | `sample_traces` | 已实现，原始文件保留完整内容 |
| 模式目录、完整索引、演化日志 | `WikiMaintainer` / `apply_wiki_update` | 已实现 |
| append / replace / insert_after | `patches.py` | 已实现 |
| ReAct Proposer、按需读取至少四条记录 | `SkillProposer` / `converse` | 已实现 |
| 单个 Skill 提案及 SKILL / PURPOSE 文件 | `apply_proposal` | 已实现 create、替换、patch、no_action，标准 YAML |
| 严格提高才接受、Skill 回退、Wiki 保留 | `EvolutionEngine` | 已实现，持平拒绝 |
| 完整提案、候选、差异、决定历史 | `append_impact` | 已实现 |
| 初始或后续验证满分提前停止 | `EvolutionEngine` | 已实现 |
| 五类任务与两个优化代理的原始提示词 | `prompts/*.md` / `sources.json` | 从原文提取，哈希可核对 |
| LiveMath 单次无工具推理 | `benchmarks/qa.py:LiveMath` | 已实现 |
| SealQA Google 搜索、文件读取、独立判分 | `benchmarks/qa.py:SealQA` | 已实现，原始 A/B/C 判分模板 |
| SpreadsheetBench bash、公式重算和评分 | `benchmarks/spreadsheet.py` / `sandbox.py` | 已实现，多工作簿 hard / soft 分数 |
| OfficeQA 初始参考页和 glob / grep / read | `benchmarks/qa.py:OfficeQA` | 已实现 |
| OfficeQA 数值、单位、列表与文本评分 | `benchmarks/vendor/officeqa_reward.py` | 纳入固定提交的官方代码及许可 |
| ALFWorld 多步状态转换与成功评分 | `benchmarks/alfworld.py` | 已实现，真实 TextWorld / ALFWorld |
| Qwen / Gemma 等模型服务 | `ChatCompletionsModel` | 已实现兼容 vLLM 的消息与函数调用 |
| 原生 Gemini 服务 | `GeminiModel` | 已实现，回传函数结果和思考签名 |
| 三次独立运行与测试平均 | `experiment` / `average_reports` | 已实现 |
| 四种 Wiki 访问消融 | `ablate` / `EvolutionConfig` | 已实现，关闭 Proposer Wiki 时关闭 Maintainer |
| 跨模型、跨任务族迁移矩阵 | `transfer-matrix` / `evaluate` | 已实现 |
| 每个基准的配对 bootstrap | `statistics.py` | 已实现，默认 1,000 次 |
| 分层抽样、等权宏平均、显著性分组 | `compare_methods` | 已实现，默认 p < 0.05 |
| Wiki / Skill 增长与接受时间分布 | 逐轮 dynamics / `history` | 已实现 |
| 对照方法结果接入与比较 | `import-results` / `analyze` | 已实现 |

## 明确不纳入的内容

EvoSkill、SkillOpt、Trace2Skill 是三个独立研究方法，是论文的比较对象，不是 WikiSkill 的组成部分。在这里另写三套算法会把项目扩展成多个方法的实现合集，因此接入其评估结果进行比较，不实现它们本身。开发时已向用户说明这一范围。

模型训练、微调或修改模型权重不属于 WikiSkill。本项目也不加入 THOR 图像模拟器；论文使用的 ALFWorld 是文本交互环境。

## 原文与实现

七份 WikiSkill 提示词由 `scripts/extract_prompts.py` 从 arXiv HTML 的 `data:text/plain;base64` 原始下载内容提取，没有改写正文。`sources.json` 记录字节数与 SHA-256；运行时填充 `skill_section`、`task_desc` 和 ALFWorld 当前观察等占位符。

正文写 `wiki/logs.md`，Maintainer 提示词写 `wiki/log.md`。物理文件采用正文名称，向代理说明并提供读取别名。这是论文内命名差异的处理，不是历史配置兼容。

SealQA 判分模板从其论文附录 C 的原始代码块另行提取。OfficeQA 评分代码固定在提交 `7b9a3c154ef9fb40215bb67934afc43e6799de16`；不声称作者使用了同一未公开的提交。

SpreadsheetBench 按公开评估中的指定区域、多工作簿、数值舍入、空值、日期时间和 hard / soft 聚合实现，公式先重算。公开评分没有启用字体和填充颜色比较，本项目不额外改变这一评分方式。

## 未公开或有歧义的细节

- 完整模型请求参数、随机种子、最大回合和具体划分 ID 未全部公开。项目将这些值明确配置，不把默认值称作作者参数。
- 推理可访问 Wiki 的消融没有公开完整输入模板。本项目只在训练阶段提供完整 Wiki，验证与测试保持只使用 Skill。
- 无 Wiki Proposer 的完整提示词未公开。本项目移除 Wiki 依赖，保留读取、提案与输出约定，并单独存放该提示词。
- 论文未说明 bootstrap p 值的单双侧、居中或零计数处理。本项目采用双侧居中配对 bootstrap 与 `(extreme+1)/(B+1)` 修正，在输出中注明。
- 补丁目标重复出现时没有明确选择规则。本项目要求精确目标只出现一次，失败后允许代理重新提交。
- `no_action` 来自附录 E。本项目记录该迭代并跳过无变化候选的验证。
- 表格任务通过容器隔离数据。Python 与系统库可读；宿主机、其他任务、答案与 Wiki 不挂载。执行代码所需的系统库不能一并禁读。

## 代码和外部实验

模型权重、服务配额、完整基准数据和未公开的划分 ID 是运行输入。配置后，五类适配器实际执行对应环境和评分，不以扩展接口代替功能。

本次验证包含真实 Docker / LibreOffice 和 ALFWorld；网络协议与完整演化使用受控模型响应。检查分数不代表模型效果，也没有声称重测论文表格成绩。
