# Codex 接入

## 安装与启动

运行 `uv sync --frozen`、`uv run --frozen wikiskill-codex init`。将输出的 `mcp_command` 注册为 Codex 本机 stdio MCP，重新连接后会请求启动采集器和转换 worker。`wikiskill` 与 `wikiskill-codex` 都使用同一业务 CLI。

WebUI：`uv run --frozen wikiskill-codex web`。默认仅监听 `127.0.0.1:8765`。也可用 `--root /absolute/data` 指定隔离目录；MCP 和 WebUI 必须使用相同目录。

`start` 请求启动后台；`worker --once` 执行当前可运行批次；`collector --once` 扫描一次轨迹。`auto_start=false` 时由用户自行运行 worker 和 collector。同一数据目录分别用进程锁保证唯一采集器和 worker。

## Raw

原文逐行作为字节保存在 `trace_records.original`，来源、偏移和读取进度一起提交。索引在 `trace_sources`、`trace_turns`，原文页面可下载原始字节。默认跳过启用前正文；“导入已有历史”补读早期记录并按来源偏移去重。归档移动继续同一来源；截断或替换保留原记录并新建段。未完成行留到后续扫描。

采集使用独立进程，不依赖模型调用。WikiSkill 转换使用数据目录作为 cwd 并登记线程 ID，采集端排除这些会话。旧的 `raw`、`observations` 表保留旧经验摘要，与新的 Raw 分开。

## Wiki 和 Skill

页面手动提交固定输入，后续新增内容留待下一批。失败批次保留输入，页面可重试现有结果或使用当前设置重新生成。模型输入预算只限制批次和模型请求，不改写、截断数据库原文。单条记录或上下文超过预算时明确失败，可提高预算后重新生成。

Wiki 编辑比较读取时的正文摘要值，冲突时拒绝覆盖。实际正文变化均记录版本，回退到旧正文也产生新版本。Skill 发布沿用锁、before/after 完整快照及 prepared/applied 恢复记录，保留二进制资源和权限。

安装是独立操作。页面复制数据目录中的完整 Skill 到所配置 Codex Skill 目录；同名目标要求确认差异，执行时再次比较来源与目标。异常时保留临时目录、旧目录和安装日志，下次安装会尝试恢复已授权的复制。生成和回退不会自动修改 Codex 目录。

## MCP 工具

- `wikiskill_wiki`：写入知识页，参数为项目绝对路径及 name/body 页面数组。
- `wikiskill_context`：读取项目自产 Skill。
- `wikiskill_status`、`wikiskill_query`：查询计数、原始轨迹、Wiki 和报告。
- `wikiskill_history`、`wikiskill_rollback`、`wikiskill_retry`：查询版本、回退和重试。

自动采集不需要 `$wikiskill`。可选 `install-skill` 安装的辅助说明仅指导查询和直接写 Wiki，不再要求模型提交 Raw。外部 Skill 纳管入口退出当前主流程，已有数据保持。

## 当前配置

设置页保存唯一当前格式。字段包括 `raw_auto`、`wiki_auto`、`raw_threshold`、`wiki_threshold`、`executor`、`api_provider`、`api_url`、`api_model`、`api_key`、`codex_home`、`install_directory`、`codex_command`、`model`、`input_budget`、`timeout_seconds`、`poll_seconds`、`auto_start`。API 支持 Chat Completions 和 Gemini；Codex 使用当前本机登录。

旧配置不做识别或兼容。升级时停止旧后台，执行 `init --reset-settings` 并重新配置。旧数据和历史不删除；已有旧摘要批次仅保留查询，不进入新轨迹自动调度。
