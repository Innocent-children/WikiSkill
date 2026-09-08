# WikiSkill

按照 [WikiSkill 论文](https://arxiv.org/html/2608.27454v1)实现的独立 Python 项目，包含演化算法、原始提示词、五类任务环境、模型接口、消融实验、跨模型迁移和统计分析。逐项对应见 [论文功能清单](docs/paper-mapping.md)。

Python 3.11+，macOS / Linux；完整环境建议使用 Python 3.11。

## Codex 日常业务

通过 MCP + skill 采集项目经验，Raw → Wiki、Wiki → Skill 分别按新增待处理内容阈值触发。后台使用新的 Codex 会话，自动备份并替换 Skill，完整保留历史和差异；报告保存在该会话及本地。业务模式不使用候选效果评分，不实现 WebUI。

```bash
uv sync --frozen
uv run --frozen wikiskill-codex init
uv run --frozen wikiskill-codex install-skill
```

配置位于 `~/.wikiskill/config.json`。按 `init` 输出的 Python 路径注册 MCP 后，在业务任务使用 `$wikiskill`。默认只管理自产 Skill；外部 Skill 通过开关、目录列表和项目关联纳入管理。安装、采集方式、阈值计量、后台运行、查询与回退见 [Codex 接入说明](docs/codex-integration.md)。下文介绍原有论文实验入口。

## 安装与快速检查

```bash
uv sync --python 3.11 --extra benchmarks
uv run wikiskill --help
uv run wikiskill demo runs/demo
uv run wikiskill status runs/demo
```

普通问答可以省略 `--extra benchmarks`。仅表格使用 `--extra spreadsheet`，仅 ALFWorld 使用 `--extra alfworld`。也可通过 `pip install -e '.[benchmarks]'` 安装。

`demo` 使用预设响应检查程序逻辑，实际经过执行记录、Wiki 维护、工具调用、候选验证和接受/拒绝。**演示分数不代表大模型效果。**

## 模型和任务配置

`models` 分别配置 `inference`、`maintainer`、`proposer` 三个角色，支持两种协议：

- `chat_completions`：兼容 `/v1/chat/completions`，可连接配置好工具调用的 vLLM。
- `gemini`：原生 `generateContent`，支持函数调用与结果、思考签名、usage、seed 和思考预算。

`api_key_env` 指定密钥所在的环境变量名。无认证本地服务可填空字符串；配置和运行记录保存变量名。`examples/gemini-model.json` 可以替换任意模型角色。

| 配置文件 | 环境 | 评分 |
| --- | --- | --- |
| `examples/live_math.json` | 单次无工具数学推理 | 选择标签匹配 |
| `examples/sealqa.json` | Google 搜索、任务文件与搜索结果读取 | 独立判分模型，原始 A/B/C 模板 |
| `examples/spreadsheet.json` | Docker 内的 bash、Python、LibreOffice | 指定区域比较，多工作簿 hard / soft 评分 |
| `examples/officeqa.json` | 初始参考页、glob / grep / read | 固定提交的官方数值、单位、列表和文本评分 |
| `examples/alfworld.json` | 真实 ALFWorld / TextWorld 状态转换 | 模拟器 won 状态 |

五类任务使用论文附录 E 的原始系统提示词。SealQA 还必须单独配置 `judge_model`；OfficeQA 的 `relative_tolerance` 默认 0。通用问答仍可使用 `examples/config.json`。

```bash
uv run wikiskill init runs/math --data data/live_math --config examples/live_math.json
uv run wikiskill run runs/math
uv run wikiskill evaluate runs/math --no-skills --output runs/math-no-skill.json
uv run wikiskill evaluate runs/math --output runs/math-evolved.json
uv run wikiskill compare --baseline runs/math-no-skill.json --candidate runs/math-evolved.json
uv run wikiskill export runs/math runs/math-skills
```

`init` 不调用模型。运行时模型名、地址、密钥和配额由实际服务提供。

## 数据

数据目录包含 `train.jsonl`、`validation.jsonl`、`test.jsonl`，每行一个任务：

```json
{"id":"q1","prompt":"Which option equals 2?","answer":"B","context":{"choices":{"A":"1","B":"2"}}}
```

`id`、`prompt`、`files`、`context` 属于公开输入；`answer`、`evaluation` 由评分代码持有。具体字段见 [五类任务数据说明](docs/datasets.md)。

```bash
uv run wikiskill prepare data/example --benchmark live_math \
  --source examples/source-records.json --splits examples/splits.json
uv run wikiskill check-data data/example
```

`prepare` 使用明确提供的划分 ID；`--paper-sizes` 检查论文中的数量，不猜测作者的具体样本。训练集至少四条，验证和测试集各至少一条；程序拒绝重复 ID 及跨划分完全相同的输入。源记录格式和资产复制方式见数据说明。

## 表格环境

启动 Docker Desktop 后构建镜像：

```bash
uv run wikiskill sandbox-build
```

每个任务只挂载自己的输入和输出目录。答案、宿主机目录、其他任务和 Wiki 不挂载；容器关闭网络，系统目录只读，并限制时间、内存和进程数。Python 与系统库正常可读，以便执行代码。

模型读取 `/workspace/task/input.xlsx`，保存 `/workspace/task/output.xlsx`。随后通过 LibreOffice 重算公式，再比较 `answer_position`。损坏或缺失的输出记为任务失败。参考答案的公式需已有缓存值；未重算的参考数据会报错，避免把空缓存误判为正确。多工作簿的 hard 分数要求全部正确，soft 分数取正确比例，两者都会记录。

## 演化流程

1. 从空 Skill 开始，用验证集取得初始分数。
2. 每轮执行完整训练集，追加保存服务返回的推理文本、工具请求、工具结果和答案。
3. 最多抽取五条失败、三条成功记录，每条给 Maintainer 的文本最多 15,000 字符，原始文件完整保留。
4. Maintainer 更新模式页、完整索引和日志。
5. Proposer 阅读 Wiki、历次结果和至少四条不同的训练记录，提出一个 Skill 修改。
6. 在完整验证集上评估候选，**严格超过历史最好分数才接受**；持平和退步都拒绝，Wiki 保留。
7. 记录提案、差异、完整候选、决定与演化指标；验证达到 1.0 提前结束。

推理时注入 `SKILL.md` 全文，默认不提供 Wiki。`PURPOSE.md` 记录来源和修改历史。测试集只用于独立评估，逐题测试结果不会交给 Maintainer 或 Proposer。

## 重复实验、消融和迁移

```bash
uv run wikiskill experiment runs/math-repeats --data data/live_math \
  --config examples/live_math.json --seeds 41 42 43
uv run wikiskill ablate runs/math-ablation --data data/live_math \
  --config examples/live_math.json --seeds 41 42 43
uv run wikiskill transfer-matrix runs/transfer --plan examples/transfer-plan.json
```

`experiment` 默认运行三个独立种子。`ablate` 覆盖四种 Wiki 访问组合及无 Skill 对照：`inference_wiki_access` 只影响训练，`proposer_wiki_access=false` 时关闭 Maintainer，Proposer 无法读取 Wiki 和历史提案。论文未公开无 Wiki 版本的完整提示词，这份消融提示词在项目中单独列出。

迁移计划的路径相对计划文件目录；每个来源提供已演化工作目录列表，每个目标提供模型配置。来源没有生成任何 Skill 时标记为不可用。单次迁移可使用 `evaluate --skills DIR --inference-config MODEL.json`。

## 统计和演化过程

```bash
uv run wikiskill analyze --plan examples/comparison-plan.json --output runs/comparison.json
uv run wikiskill history runs/math --output runs/math-history.json
```

`analyze` 先对每个任务取独立运行的平均分，再进行配对抽样。默认 1,000 次 bootstrap；跨任务族分别抽样后等权求宏平均。输出分数、95% 区间、p 值和显著性分组。采用的双侧居中 bootstrap 公式写入结果；论文没有进一步公开 p 值估计细节。

`history` 输出每轮模式数量、模式文本长度、Skill 数量与长度、优化调用数，以及修改在早、中、晚阶段被接受的分布。

对照算法的外部逐题分数可导入后统一比较：

```bash
uv run wikiskill import-results --scores external-scores.json --data data/live_math \
  --config examples/live_math.json --benchmark live_math --method evoskill --model source-model --seed 41 \
  --output runs/evoskill-math-41.json
```

分数文件是 `[{"task_id":"q1","score":1}, ...]`，必须包含全部测试任务且每条恰好一次。配置用于记录对应的任务资产与评分设置，导入时不调用模型。比较会检查这些信息是否一致。EvoSkill、SkillOpt、Trace2Skill 是其他算法，本项目接入其结果进行比较，不另写三套算法。

## 存储与继续运行

```text
run/
├── inputs.json                 # 配置和数据位置
├── state.json                  # 已接受 Skill、最好分数、完成迭代
├── raw/
│   ├── traces/                 # 训练记录
│   ├── optimizer/              # Maintainer / Proposer 对话
│   ├── evaluations/            # 验证、测试和判分记录
│   ├── artifacts/              # 工作簿等产物及哈希
│   └── events/                 # 决定或错误
├── wiki/{index.md,logs.md,skill-impact.md,patterns/}
├── skills/<name>/{SKILL.md,PURPOSE.md}
├── candidates/
└── work/                       # 各次执行的独立目录
```

Raw 文件通过排他创建和只读权限防止程序覆盖。接受快照原子保存；中断后重跑会重新执行尚未完成的迭代，追加新的 attempt，保留已有 Wiki。恢复单位是迭代，不会续接半段模型对话。

`run --iterations N` 设置目标总迭代数。模型、数据、提示词或实验设置改变时应新建目录。配置只有一套当前格式，不维护旧格式转换。

## 验证与来源

```bash
uv run --extra benchmarks python -m unittest discover -s tests -v
WIKISKILL_DOCKER_TEST=1 uv run --extra benchmarks python -m unittest discover -s tests -p 'test_sandbox_integration.py' -v
WIKISKILL_ALFWORLD_GAME=/absolute/path/game.tw-pddl uv run --extra benchmarks python -m unittest discover -s tests -p 'test_benchmarks.py' -v
```

实际检查见 [验证记录](docs/verification.md)，提示词和评分代码的来源、哈希与许可见 [NOTICE.md](NOTICE.md)。

模型权重、API 配额、五套完整数据和作者未公开的划分 ID 不随项目分发。本次不声明重现论文表格中的模型成绩。
