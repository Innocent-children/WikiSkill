## ADDED Requirements

### Requirement: Automatic unmodified capture
系统 SHALL 在 MCP 启动后自动唤起唯一后台采集器，只读 Codex sessions 与 archived_sessions 的实际轨迹，将取得的完整行原始字节和来源偏移存入自身数据库；解析索引与原文分离。采集 SHALL 不调用模型，不依赖显式经验提交，不修改源文件。

#### Scenario: Normal Codex work
- **WHEN** 用户连接 MCP 后正常对话和调用其他工具
- **THEN** 新增轨迹自动入库，用户可在 WebUI 查看原文与会话信息，无需使用 wikiskill 工具

#### Scenario: Unknown event and unavailable content
- **WHEN** 记录包含未知事件、加密字段或上游已截断内容
- **THEN** 原始字节照常保存，页面说明解析情况，系统不生成不存在的内容

### Requirement: Incremental recovery
系统 SHALL 将读取位置与原文在同一事务保存；未完成行留待后续读取。归档移动 SHALL 继续同一来源，源文件截断或替换 SHALL 保留已有记录并建立新段。

#### Scenario: Restart after partial write
- **WHEN** 文件末尾尚未完成或采集进程重启
- **THEN** 已保存内容不重复，完整新增行在后续读取中入库，原文和偏移保持一致

### Requirement: Collection start and history
系统 SHALL 默认从首次启用时开始采集，WebUI SHALL 提供主动导入已有历史的操作；旧四字段摘要 SHALL 保留并标为旧经验摘要，与原始轨迹分开展示。

#### Scenario: First launch and explicit history import
- **WHEN** 用户首次启用，随后主动导入历史
- **THEN** 首次不自动导入启用前的正文；主动导入补齐历史且不重复已有 Raw，旧摘要仍可查看

### Requirement: Exclude generated sessions
WikiSkill 转换及报告使用的 Codex 会话 SHALL 被采集器识别并排除；采集与模型执行 SHALL 独立，长时间转换不阻塞轨迹积累。

#### Scenario: Background transformation
- **WHEN** WikiSkill 创建转换会话，同时用户继续使用 Codex
- **THEN** 用户轨迹继续保存，转换会话不回流到 Raw
