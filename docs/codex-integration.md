# Codex 接入

## 安装与启动

安装后运行 `wikiskill`，自动初始化并启动面板与转换 worker，自动模式另行启动采集器；从源码运行可用 `uv sync --frozen`、`uv run --frozen wikiskill`。`wikiskill` 与 `wikiskill-codex` 都使用同一 CLI。

需要 MCP 工具时，运行 `wikiskill init`，将输出的 `mcp_command` 注册为 Codex 本机 stdio MCP。重新连接后自动唤醒整套后台并复用已有实例，不打开浏览器。自动采集独立于 MCP 连接。也可用 `--root /absolute/data` 指定隔离目录；MCP 和 WebUI 必须使用相同目录。

`start` 启动整套后台但不打开浏览器；`status` 包含实际地址与运行状态；`stop` 停止托管后台并保留数据。`auto_start=false` 时，仍可主动运行 `wikiskill` 或 `start`。工作模式与后台自动唤醒保持独立。默认端口 8765 被占用时自动选择空闲端口；以命令输出的地址为准。

`worker --once` 执行当前可运行批次；`collector --once` 扫描一次轨迹。`web` 保留为前台页面开发入口。单独运行的 worker/collector 由启动终端管理，统一服务发现进程锁冲突会提示先停止独立进程。停止会中断当前模型执行，重启沿用已有批次恢复逻辑。若后续 MCP 操作又唤醒了服务，可关闭设置中的自动启动开关。

## 环境诊断

```bash
wikiskill-codex --root /absolute/data doctor
wikiskill-codex --root /absolute/data doctor --json
wikiskill-codex doctor --help
```

使用 uv 安装时，可在这些命令前加 `uv run --frozen`。`--root` 放在子命令前，省略时使用默认数据目录；`wikiskill` 支持相同用法。
doctor 可在初始化前或配置损坏时独立运行。它只读取配置及文件元数据，不修改数据、不创建目录或数据库、不运行 Codex 命令、不启动后台、不调用模型或联网。uv 自身的环境同步属于启动工具的行为。

检查包括数据目录的读写权限、`config.json` 的格式和权限、`state.sqlite3`、`skills`/`reports`/`locks`/`worker.log` 的路径类型及访问权限，以及 `codex_home`、其会话目录、`install_directory`。
数据目录或配置缺失是错误，提示使用同一 `--root` 执行 `init`；配置无效时提示具体字段修复，或在备份配置后使用 `init --reset-settings` 恢复当前默认设置。
运行文件和安装目录尚未生成、但父目录具备创建权限时仅提示；会话目录尚不存在也仅提示，可能还没有相应会话。
已存在路径的类型或所需访问权限错误会阻止通过。所有建议都由用户自行执行。

执行方式按当前配置检查：

- `codex`：`codex_command` 首项必须能找到且可执行。相对命令路径、相对 PATH 项按数据目录解释，与实际启动 Codex 的工作目录一致。`model` 为 null 时使用 Codex 默认模型。
- `ollama`：检查本机模型名称是否配置；不发起请求、不验证 Ollama 服务或模型是否可用。
- `api`：检查 API 协议、地址和端口格式，并要求填写 `api_model`。没有 Codex 命令只提示。`api_key` 只显示“已配置/未配置”；为空时提示需要认证的服务应填写 key，免认证服务可以留空。

默认输出逐项列出状态及修复建议。JSON 输出包含 `ok`（布尔值）、`executor`（`codex`、`api`，无法读取有效配置时为 null）和 `checks` 数组。
每个检查包含 `id`、`status`、`message`、`repair`；`status` 是 `ok`、`warning` 或 `error`，没有建议时 `repair` 为空字符串。
出现任一 `error` 时 `ok=false` 且退出 `1`；只有 `ok`/`warning` 时退出 `0`。目录不可用时只返回能完成的检查，配置无效时省略依赖有效配置的检查。

两种输出均使用固定提示，省略实际路径、URL、模型名、命令参数、配置原文及原始异常内容，避免输出密钥或含凭据地址。
“通过”只表示本地检查未发现阻塞问题，不验证 Codex 登录、API 连接/认证、模型可用性或 SQLite 内容完整性。诊断中读取的权限也可能在之后发生变化。

## Raw

原文逐行作为字节保存在 `trace_records.original`，来源、偏移和读取进度一起提交。索引在 `trace_sources`、`trace_turns`，原文页面可下载原始字节。默认跳过启用前正文；“导入已有历史”补读早期记录并按来源偏移去重。归档移动继续同一来源；截断或替换保留原记录并新建段。未完成行留到后续扫描。

采集使用独立进程，不依赖模型调用。WikiSkill 转换使用数据目录作为 cwd 并登记线程 ID，采集端排除这些会话。旧的 `raw`、`observations` 表保留旧经验摘要，与新的 Raw 分开。

## Wiki 和 Skill

页面手动提交固定输入，后续新增内容留待下一批。失败批次保留输入，页面可重试现有结果或使用当前设置重新生成。应用不按 token 或调用次数限制分析，原文完整保留；模型服务报告失败时，保留输入供手动重试。

Wiki 编辑比较读取时的正文摘要值，冲突时拒绝覆盖。实际正文变化均记录版本，回退到旧正文也产生新版本。Skill 发布沿用锁、before/after 完整快照及 prepared/applied 恢复记录，保留二进制资源和权限。

安装是独立操作。页面复制数据目录中的完整 Skill 到所配置 Codex Skill 目录；同名目标要求确认差异，执行时再次比较来源与目标。异常时保留临时目录、旧目录和安装日志，下次安装会尝试恢复已授权的复制。生成和回退不会自动修改 Codex 目录。

## MCP 工具

- `wikiskill_wiki`：写入知识页，参数为项目绝对路径及 name/body 页面数组。
- `wikiskill_context`：读取项目自产 Skill。
- `wikiskill_status`、`wikiskill_query`：查询计数、原始轨迹、Wiki 和报告。
- `wikiskill_history`、`wikiskill_rollback`、`wikiskill_retry`：查询版本、回退和重试。

自动采集不需要 `$wikiskill`。可选 `install-skill` 安装的辅助说明仅指导查询和直接写 Wiki，不再要求模型提交 Raw。外部 Skill 纳管入口退出当前主流程，已有数据保持。

## 当前配置

设置页保存唯一当前格式。模式使用 `capture_mode=manual/automatic`；定时间隔使用 `analysis_interval_minutes`。执行方式 `executor` 可选 codex、api、ollama；本机 Ollama 使用 `ollama_model`。API 使用 `api_provider`、`api_url`、`api_model`、`api_key`，支持 Chat Completions 和 Gemini。其他设置为 `codex_home`、`install_directory`、`codex_command`、`model`、`timeout_seconds`、`poll_seconds`、`auto_start`。当前流程见 [演化流程](evolution.md)。

旧配置不做识别或兼容。升级时停止旧后台，执行 `init --reset-settings` 并重新配置。旧数据和历史不删除；已有旧摘要批次仅保留查询，不进入新轨迹自动调度。

## 结构化生成与模型响应

Ollama 使用原生 `/api/chat`：Wiki 整理传入 JSON Schema，Skill 生成使用 read_file/finish 工具；本机 Qwen3.5 关闭 thinking。Codex 的 Wiki 整理继续使用 outputSchema，Skill 生成启用 app-server 动态工具，仅向模型开放当前批次材料。原始论文提示词与项目适配说明分开存放。

解析失败时，在执行记录展开“模型响应原文与工具调用”，检查正文、思考字段、工具参数和结束原因。修复执行环境或更新代码后，选择“使用当前设置重新生成”，创建新的模型执行尝试并保留旧响应。无需清空 Raw、Wiki 或 Skill。
