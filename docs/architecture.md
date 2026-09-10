# 技术参考

本文件描述当前模块职责、持久化和模型/HTTP 协议。安装、配置、采集范围及页面操作见 [README](../README.md)，开发命令见 [CONTRIBUTING](../CONTRIBUTING.md)。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `session_import.py` | 按请求枚举本机会话，导入选定文件；列举只读文件头和少量标题 |
| `capture.py` | 独立进程增量采集原始轨迹 |
| `store.py` | SQLite 事务、业务查询与轨迹存取 |
| `runtime.py` | 调度、固定输入、执行状态、消费关系和发布协调 |
| `generation.py` | 两阶段分析协议、材料范围与多轮工具读取 |
| `models.py` / `ollama.py` / `codex.py` | API、Ollama 与 Codex app-server 适配 |
| `evolution.py` | Wiki 原文来源、生成上下文、Skill 来源及反馈 |
| `skill_generation.py` | 当前 Wiki 选择、合并候选、新建和合并入队 |
| `skills.py` / `install.py` | 目录发布与恢复、独立安装 |
| `views.py` / `traces.py` / `web.py` | 读取视图及本机同源 HTTP 操作 |

## 原文与批次

原文逐行以字节保存在 `trace_records.original`，来源和轮次由 `trace_sources`、`trace_turns` 索引。读取进度与原文在同一事务提交，未完成行留待后续扫描；归档移动继续同一来源，截断或替换保留记录并新建段。展示文本与原始字节分离，读取接口同时提供 base64 和展示文本。

转换会话使用数据目录作为 cwd，并登记线程 ID；采集端同时检查两者，排除自身转换。`raw`、`observations` 保存的经验摘要与轨迹分开展示，其摘要批次仅供查询，不进入轨迹自动调度。

按会话分组的批次保存原始记录 ID；缺少会话 ID 时按来源 identity 分组。手动提交后新增内容留待下一批。自动批次在排队与执行前检查 [静默条件](../README.md#分析与重试)，等待期间新增记录在满足条件后加入该会话批次，已开始的模型请求继续完成。等待中的会话不阻塞其他可执行会话。

已有等待、执行中或失败批次的会话暂停新增批次；手动整理全部跳过它们，明确选中忙碌会话时提示处理已有批次。首次执行读取最新 Wiki 并保存上下文，重试复用可恢复输入和结果；明确重新生成才按原记录及当前 Wiki 创建新尝试。

Wiki 实际修改产生版本。自动生成按 `skill_wiki` 查找关联 Skill；未关联页面按页面名与项目标识创建独立专题 Skill。共享 Skill 同时最多有一批，后续项目变更留待处理。`no_action` 记录已检查来源，避免重复生成。

## 分析协议

原始附录 E.2/E.3 提示词位于 `wikiskill/live/prompts`，项目只在运行时追加明确的接口和业务条件，差异记录于 [ADAPTATIONS.md](../wikiskill/live/prompts/ADAPTATIONS.md)。

Wiki Maintainer 接收选定轨迹、当前完整模式页、目录和日志。返回 `create_patterns / update_patterns / update_index / append_log`。Ollama 请求的 `format` 和本地 jsonschema 校验使用同一份 Schema；已有页面的补丁目标由真实唯一行、段落或章节生成候选，防止模型拼错旧文本。所有模式页、目录和日志在同一事务中更新；并发编辑会整批拒绝。`wiki_documents` 单独保存目录和日志，不触发生成无关专题 Skill。

Skill Proposer 使用真正的 `read_file(path)` 和 `finish(proposal)` 工具。finish 的函数参数直接组成提案对象，包含 `action=create/patch/no_action` 及对应字段，没有额外 proposal 包装。Ollama 原生参数对象、Chat Completions 的 JSON 参数字符串及 Gemini 调用签名分别由适配器处理。Codex 使用 app-server 动态工具（experimental API），宿主仅接受本线程的 read_file/finish 回调。

Proposer 先读取目录和历史，修改前读取所选模式页、可用轨迹（至少四条，不足四条时读全部）、已有 Skill 及最近负面反馈对应的修改。允许读取的文本资源来自目标 Skill 的固定快照。普通文字不能作为最终提案；只有通过结构和业务校验的 finish 才能发布，且不能与读取调用混在同一响应。新建 Skill 必须有 YAML 头部及 PURPOSE.md；补丁保持原有资源和来源历史。无具体任务知识时返回 no_action，禁止将生成器自己的操作说明编造成业务 Skill。

Ollama 使用原生 `/api/chat`，显式 `think=false`、`stream=false`、temperature 0。Maintainer 使用 `format`，Proposer 使用 `tools`，两者分开。API Maintainer 使用 response_format 或 Gemini responseJsonSchema；不支持结构化输出的服务明确失败，不静默改回裸文本。

模型调用和失败行为见 [README](../README.md#模型设置)。Codex 按 app-server 会话轮次记录用量，不推算内部模型调用次数；协议失败不用额外模型调用猜测或修复 JSON。

响应在解析前保存到 `reports/model-responses/<job>/<attempt>-<sequence>.json`，文件权限 0600。保存响应而非认证请求头，不记录 API key 配置。WebUI 的“模型响应原文与工具调用”分页展示所有保留尝试，读取记录不调用模型。连接失败、尚未收到响应时没有响应文件，以批次错误为准。

## 来源、反馈与发布

`wiki_sources(change_id,record_id)` 将 Wiki 正文版本关联到原始记录。后续编辑保留已有来源，新分析追加有效来源。旧生成页面可从其保存的 Raw 批次追溯；人工新建或 ZIP 导入页面没有原文时保持未知。来源表示关联记录，不证明正文每一句话都成立。

`skill_wiki(skill,project,name,change_id)` 记录每个 Skill 已处理的 Wiki 版本。手动选择或自动生成都使用当前正文，历史版本只用来解释修改过程。已有页面全文不变时不会产生无意义版本。

`skill_feedback` 记录选定已发布版本的实际结果、问题、拒绝原因与备注。恢复历史内容时额外记录替换前版本与恢复目标。反馈保留完整文本，在后续批次固定下来；批次期间新增反馈留给下一次生成。反馈不会触发模型或自动回退。

实际修改发布时，由宿主将选中 Wiki 版本和修改摘要追加到 `PURPOSE.md`，与 `SKILL.md` 在同一次完整目录快照中发布、恢复和安装。Wiki 的已有版本记录和批次摘要组成知识演化日志，生成器可以按需查阅。

Wiki 编辑比较读取时正文的摘要，实际变化记录版本，A→B→A 也产生新版本。模型提交并发冲突时整批拒绝。Skill 发布采用锁、before/after 完整快照和 prepared/applied 恢复记录，保留二进制资源和权限；恢复先处理未完成发布，再创建新发布记录。

安装持锁复核来源与目标 digest，同名目标需要确认差异。准备完整临时目录后保留旧目录并替换目标；失败保留安装日志和目录供下次恢复。符号链接目标及越界路径拒绝写入。

## HTTP 接口

HTTP 接受本机 Host，浏览器请求须同源；服务控制另校验当前实例本地令牌。模型任务入队后由 worker 执行。

`GET /api/projects/{project}/wiki` 接受 `q`、`offset`、`limit`。搜索当前名称或正文，先过滤再分页；省略 `q` 或传空字符串保持原列表行为。查询参数需 URL 编码，例如 `?q=100%25&limit=20&offset=0` 搜索字面的 `100%`。

| 方法与路径 | 请求 / 响应 |
| --- | --- |
| `GET /api/projects/{project}/wiki-export` | 全部 Wiki，返回 `application/zip` |
| `POST /api/projects/{project}/wiki-export` | `{"pages":["build","tests"]}`；空列表、重复或缺失页报错 |
| `POST /api/projects/{project}/wiki-import/preview` | `{"archive_base64":"..."}`；返回 `pages`（状态、前后正文、diff、expected_digest）、`counts`、`preview_token` |
| `POST /api/projects/{project}/wiki-import` | `{"archive_base64":"...","preview_token":"...","overwrite":["build"]}`；返回 `new_changes` 和 `counts` |

ZIP 内容限制见 [README](../README.md#wiki-zip-导入导出)。确认列表必须精确包含所有修改页，新增页无需加入。输入与冲突错误返回 `409` 和 `detail`，项目不存在返回 `404`。

导入事务复核完整项目正文 digest 与 Wiki 编辑序号；预览后任一页面编辑（包括空白变化或改回旧正文）均使预览失效。token 绑定项目、ZIP 和当前服务实例。新增与修改各记录一个版本，无变化不修改元数据或版本，事务错误不留下部分写入。

## 诊断输出

`doctor` 在初始化前或配置损坏时也可独立运行，仅读取配置及文件元数据。数据目录或配置缺失为 error；可创建但尚未生成的运行目录、安装目录或会话目录为提示，已存在路径类型或访问权限不符会阻止通过。

Codex 命令的相对路径和相对 PATH 项按数据目录解释，与实际子进程 cwd 一致。API 检查地址、端口、模型及 key 是否配置，免认证服务可留空 key；Ollama 仅检查模型名称配置。输出使用固定提示，省略实际路径、地址、模型名、命令参数、配置原文和原始异常。JSON 字段与退出码见 [README](../README.md#排查问题)。
