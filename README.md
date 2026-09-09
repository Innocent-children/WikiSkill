# WikiSkill

以 WebUI 为主的本地 Codex 经验管理工具。连接 MCP 后，后台从 Codex 本地轨迹增量保存原始记录；在页面整理 Wiki、生成 Skill，并复制到 Codex 的 Skill 目录。

Python 3.11+，macOS / Linux。前端开发需要 Node.js 22.12+。

```bash
uv sync --frozen
uv run --frozen wikiskill-codex init
uv run --frozen wikiskill-codex web
```

按 `init` 输出的 `mcp_command` 注册本机 MCP。WebUI 默认打开地址为 `http://127.0.0.1:8765`。

1. 在设置页选择 Codex 新建会话，或填写 API 协议、地址、模型及 API key。
2. 连接 MCP 后正常使用 Codex；首次采集从启用时开始，也可在页面点击“导入已有历史”。
3. 在知识工作台选择项目、查看 Raw 原文，点击 Raw → Wiki；Wiki 可新建、编辑和回退。
4. 在 Wiki 列表或详情选择一篇或多篇正文生成 Skill，默认新建，也可查找并合并到已有 Skill。结果先保存在数据目录。
5. 点击“安装到 Codex”。目标不存在时直接复制完整目录；同名目标展示差异，确认后覆盖。

知识工作台的“知识文档”页支持 **导出全部 Wiki** 和 **导出选定 Wiki**。选定导出复用跨页勾选，未选中时禁用；ZIP 只含对应页面的当前 Markdown 正文。导入 ZIP 时先预览新增、修改与无变化页面，查看同名差异并逐项确认，再点击“确认并导入”。预览后项目 Wiki 有新编辑时会整批拒绝，重新预览后才能重试。导入通过已有 Wiki 版本记录保存，可在知识历史中查看和恢复。

两阶段自动转换默认关闭，可分别开启并设置阈值。Raw 按未处理的已结束轮次计数（包含中断），Wiki 按新增正文版本计数，自动转换只更新项目汇总 Skill，专题 Skill 通过手动选择 Wiki 更新。手动转换无需满足阈值。

## 数据与运行

默认数据目录 `~/.wikiskill`，包含配置、SQLite、自产 Skill、报告、锁和后台日志。API key 仅保存于权限受限的本地配置，页面读取接口、批次配置和报告不回显密钥。

排查本地环境可运行 `wikiskill-codex --root /absolute/data doctor`，机器读取可加 `doctor --json`。
诊断会检查配置、执行方式所需设置、Codex 命令和相关目录，并给出修复建议；即使目录尚未初始化或配置损坏也能运行。
有阻止使用的问题时退出码为 `1`，否则为 `0`（可包含提示）。诊断只读取本地信息，不创建目录或数据库、不启动后台或命令、不调用模型或联网；输出只包含检查状态，不回显路径、配置值或密钥。
详细状态含义见 [环境诊断](docs/codex-integration.md#环境诊断)。

采集独立于模型执行，读取 `CODEX_HOME`（默认 `~/.codex`）中的 `sessions` 和 `archived_sessions`。完整行字节与偏移在同一事务入库；未知事件原样保留，展示索引单独存储。WikiSkill 自身转换会话排除采集。

只能保存 Codex 实际写出的内容。加密内容、客户端未记录或已截断的工具输出不能还原。轨迹格式变化可能需要更新解析器，原文保存不依赖语义解析成功。

现有四字段经验保留并标为“旧经验摘要”。配置采用当前格式，升级时停止旧后台，执行 `wikiskill-codex init --reset-settings` 替换设置，再通过 WebUI 填写；该命令保留数据库、Skill 和报告。旧摘要批次保留供查看，不自动执行为新 Raw 批次。

详见 [Codex 接入](docs/codex-integration.md) 和 [WebUI 与开发](docs/webui.md)。实验、评分、消融和跨模型评测入口已移除。
