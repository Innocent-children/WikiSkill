## Why

现有 Raw 接收模型整理后的四字段经验，WebUI 只读，不能支持用户正常使用 Codex 时自动积累轨迹、再在页面整理知识和安装 Skill 的流程。将日常操作集中在 WebUI，保留已有发布和恢复能力，删除不再使用的论文实验业务。

## What Changes

- 自动启动本机采集器，增量读取 Codex 实际写出的轨迹，将原始字节存入自身数据库，解析索引单独保存。
- 默认从启用时开始，WebUI 可主动导入已有历史；旧四字段记录保留并标为旧经验摘要。
- WebUI 手动 Raw→Wiki、Wiki→Skill，保留可配置的两项自动阈值；两阶段共用固定输入和恢复机制。
- 设置页配置 API 协议、地址、模型及密钥，或选用新建 Codex 会话。
- Wiki 可在 WebUI 新建编辑，保留 MCP 直接写 Wiki；页面展示历史、差异、回退和失败重试。
- Skill 保存在数据目录，页面可查看并复制完整目录到 Codex；同名目标先展示差异，再确认覆盖。
- **BREAKING**：停止将主动提交的经验摘要作为 Raw 采集入口，配置仅采用新的当前格式。
- **BREAKING**：移除论文实验、数据集评分、候选接受拒绝、消融和跨模型评测及不再使用的测试、依赖、文档，保留日常模型客户端。

## Capabilities

### New Capabilities

- `codex-raw-capture`: 模型外采集、无损存储、增量恢复、历史导入与旧摘要区分。
- `webui-knowledge-workflow`: 设置、手动及自动转换、Wiki 管理、历史恢复、Skill 安装。

### Modified Capabilities

无已有 OpenSpec 规格。

## Impact

仅 WikiSkill 仓库。涉及 `wikiskill/live/`、业务模型客户端、React WebUI、CLI/MCP/HTTP 接口、SQLite 存储、配置、测试、打包和使用文档。外部 Skill 纳管和多项目共享不作为本次新功能。采集只读 Codex 源文件，保存实际可取得内容，无法补全客户端未写出的信息。

## Confirmed clarifications

2026-09-08 用户确认：从启用时开始采集，可手动导入历史；旧摘要保留并明确标注；同名 Skill 安装展示差异并确认覆盖。

## Design choices to specify

Raw 自动阈值采用未处理的已结束轮次，包含完成和中断；Wiki 阈值采用新增正文版本。未知事件原文照常保存，无法归属轮次的内容在 Raw 页面显示并允许手动选入。两阶段分别提供自动开关，初始关闭以便先配置执行方式；手动转换无需达到阈值。以上是实现选择，不作为用户逐项确认的原话。
