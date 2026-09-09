## ADDED Requirements

### Requirement: Manual and automatic transformations
WebUI SHALL 在原始记录页提供 Raw→Wiki，在 Wiki 列表与详情提供 Wiki→Skill 手动操作。Raw 选择待处理记录，Wiki 选择当前正文；设置页 SHALL 保留两项自动阈值及开关。Raw 阈值按未处理已结束轮次计数，包含中断轮次；Wiki 阈值按新增正文版本计数。手动操作 SHALL 不受阈值限制。

#### Scenario: Fixed manual batch
- **WHEN** 用户提交选定输入后又积累新内容
- **THEN** 当前批次只处理提交时固定的输入，新内容留待后续处理；Wiki 正文允许再次用于新建或更新 Skill，Raw 已处理记录不重复消费

#### Scenario: Automatic thresholds
- **WHEN** 对应自动开关开启且待处理数量达到阈值
- **THEN** 创建自动批次；Wiki 自动转换仅更新所属项目的汇总 Skill。专题 Skill 与其他项目关联的 Skill 由用户手动选择 Wiki 更新；未开启或未达到阈值时不自动创建

### Requirement: Configurable execution
设置页 SHALL 配置 API 协议、地址、模型名和密钥，或选择本机 Codex 新会话执行。两种执行方式 SHALL 共用输入、结果检查、持久化和本地报告，密钥 SHALL 不回显到页面读取接口、事件或报告。

#### Scenario: API and Codex execution
- **WHEN** 用户选择 API 或 Codex 执行任一转换阶段
- **THEN** 使用所选方式生成相同业务结果结构，页面展示成功或具体失败及重试操作

### Requirement: Wiki authoring and history
MCP SHALL 保留直接写 Wiki；WebUI SHALL 支持新建、编辑、查看历史差异和回退，写入与回退均生成可追踪正文版本并计入 Wiki 转换输入。

#### Scenario: Concurrent editing
- **WHEN** 页面或模型基于旧内容保存，而同一页面已经发生修改
- **THEN** 拒绝覆盖并提示刷新或重新生成，已经保存的内容保持完整

### Requirement: Recoverable execution and publication
系统 SHALL 复用固定输入、发布锁、并发修改检查、发布记录与异常恢复；WebUI SHALL 展示执行进度、本地报告、历史差异、回退及失败重试。失败 SHALL 保留输入，未成功发布不得标为已处理。

#### Scenario: Failure and retry
- **WHEN** 模型执行或发布失败，用户选择重试
- **THEN** 复用固定输入及可恢复的已存结果；需要重新生成时显式执行，已发布部分依据记录恢复，不重复应用

### Requirement: Skill viewing and installation
生成 Skill SHALL 留在 WikiSkill 数据目录，WebUI SHALL 查看正文、资源、历史及差异，并复制完整目录到 Codex Skill 目录。系统 SHALL 仅在用户点击安装后复制；同名目标 SHALL 展示差异并要求确认覆盖，确认后仍检查目标是否改变。

#### Scenario: Install and conflict
- **WHEN** 用户安装 Skill，目标不存在或已确认当前同名目标差异
- **THEN** 完整目录被复制，页面显示安装结果；确认后目标改变则要求重新查看差异，安装失败保留可恢复的原目录

### Requirement: Business product scope
系统 SHALL 删除论文实验入口、数据集评分、候选接受拒绝、消融、跨模型评测及不再使用的相关代码、测试、依赖和文档，保留日常转换模型客户端。配置 SHALL 仅采用当前新格式。

#### Scenario: Business-only distribution
- **WHEN** 用户安装并运行 WikiSkill
- **THEN** 文档与命令以自动采集、WebUI 知识管理和 Skill 生成为主，日常模型调用可用，实验命令退出

### Requirement: Generate from selected current Wiki pages
Wiki 列表 SHALL 支持单选、多选和跨页选择；详情 SHALL 支持从当前已保存正文生成。生成面板 SHALL 默认新建 Skill，提供可用名称，查找所有受管理项目的已有 Skill 并展示用途、匹配词、关联项目和正文。用户 SHALL 可明确选择合并到已有 Skill，合并保留已有用途与资源。已处理过的 Wiki SHALL 仍可选择。

#### Scenario: New Skill or explicit merge
- **WHEN** 用户选择一篇或多篇 Wiki 并打开生成面板
- **THEN** 默认新建，候选按共同词排序并支持搜索；选择合并时更新目标 Skill，关联到当前项目，沿用发布与历史流程

#### Scenario: Fixed complete selection
- **WHEN** 用户提交生成
- **THEN** 检查预览时的正文及合并目标内容是否仍有效，固定全部选定正文；发生变化或超过输入上限时明确拒绝，不静默截断选择

### Requirement: Focused page responsibilities
工作台各页 SHALL 分别负责原始记录整理、Wiki 编辑与生成、Skill 查看与安装；历史页 SHALL 负责差异、下载和恢复；执行记录 SHALL 负责进度和重试；设置 SHALL 负责配置、历史导入和后台启动。跨职责操作 SHALL 使用链接进入对应页面。
