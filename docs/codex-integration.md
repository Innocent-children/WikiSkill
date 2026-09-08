# Codex 业务接入

业务入口是 `wikiskill-codex`，由 MCP、业务 skill 和本地后台 worker 组成。Python 3.11+，支持 macOS / Linux，使用已登录的 Codex CLI。论文实验仍通过原来的 `wikiskill` 运行；业务入口使用显式提交的任务经验，没有标准答案、验证集或候选效果评分。

## 安装

在仓库安装到长期可用的 Python 环境。开发环境可使用 `uv sync --frozen`；部署可在自己的虚拟环境执行 `python -m pip install /absolute/path/to/WikiSkill`。

```bash
uv run --frozen wikiskill-codex init
uv run --frozen wikiskill-codex install-skill
```

`init` 创建 `~/.wikiskill/config.json`，同时打印 MCP 启动命令。将输出中的 Python 绝对路径用于注册，避免依赖桌面应用的 PATH 或当前目录：

```bash
codex mcp add wikiskill -- /absolute/path/to/python -m wikiskill.live.cli --root /Users/your-name/.wikiskill mcp
```

重启或重新加载 Codex MCP 后，在新业务任务里使用 `$wikiskill`。`install-skill` 默认安装到 `~/.codex/skills/wikiskill/SKILL.md`；可用 `--directory` 指定其他受 Codex 发现的 skill 根目录。已有同名不同内容时会停止，避免覆盖其他 skill。生成的项目 Skill 保存在 `~/.wikiskill/skills`，业务 skill 通过 MCP `wikiskill_context` 加载对应项目的内容。

后台继承本机 Codex 登录与模型配置；`model: null` 使用 Codex 默认模型，也可填写明确的 Codex 模型名。无需额外模型 API key。接入采用 [Codex app-server](https://developers.openai.com/codex/app-server) 的 stdio、`thread/start`、`turn/start` 和事件通知。每个优化批次创建持久新会话，生成与最终报告分别是该会话的两个 turn。后台线程禁用配置中的 MCP 服务，防止递归采集；模型输出由本地程序发布。

## 配置

只有下面这一套格式；未知字段、非正整数阈值及错误类型会报错。

```json
{
  "raw_threshold": 10,
  "wiki_threshold": 5,
  "manage_external": false,
  "external_skills": [],
  "codex_command": ["codex", "app-server"],
  "model": null,
  "timeout_seconds": 600,
  "poll_seconds": 10,
  "auto_start": true
}
```

桌面 PATH 找不到 Codex 时，将 `codex_command` 的第一个成员设为 Codex 可执行文件的绝对路径。命令按 argv 执行，不经过 shell。

默认只管理 WikiSkill 注册并生成的 Skill。纳入用户 Skill 需要三个动作：打开 `manage_external`，在 `external_skills` 中列出 Skill **目录**绝对路径，再通过 `wikiskill_enroll(project, directory)` 关联具体项目。一个 Skill 可以关联多个项目；它们共享同一个发布锁与版本记录。关掉开关或移除路径会停止后续发布，包括正在生成但尚未发布的结果。

MCP 每次调用重新读取配置，worker 每轮读取配置，发布前再次检查管理开关。`auto_start` 控制 MCP 是否唤起后台进程；它不终止已经运行的 worker。需要手动管理进程时设为 false，并在终端运行：

```bash
wikiskill-codex worker          # 持续检查，Ctrl+C 停止
wikiskill-codex worker --once   # 处理当前达到阈值的批次后退出
```

默认 MCP 启动时及采集后会请求启动脱离会话的 worker，进程锁使重复启动的进程立即退出。worker 会在 MCP 连接断开后继续工作；输出保存在 `~/.wikiskill/worker.log`。失败批次暂停，修正原因后显式重试。

## 采集与触发

MCP **不能自动监听其他工具调用或读取全部 Codex 对话**。业务 skill 在任务开始调用 `wikiskill_context`，在有可复用结果时显式提交：

```json
{
  "project": "/absolute/project",
  "source_id": "conversation-id:build-result",
  "observations": [{
    "problem": "项目构建使用了错误的 JDK",
    "action": "查看项目说明后切换 JAVA_HOME",
    "outcome": "使用项目规定的 JDK 后编译通过",
    "lesson": "构建前先读取项目的 JDK 规定"
  }],
  "metadata": {"source": "conversation-id", "timestamp": "2026-09-08"}
}
```

对应工具为 `wikiskill_collect`。CLI 也可以调用任一 MCP 操作：

```bash
wikiskill-codex call wikiskill_collect < observation.json
wikiskill-codex status --project /absolute/project
```

`source_id` 是幂等事件 ID；同 ID 重发返回原记录，换用不同观察内容会报错。不同 ID 中完全相同的观察不重复累计。原始提交完整保存在 Raw，去重后的 `problem/action/outcome/lesson` 用于计数。仅去掉换行风格、首尾空白和行尾空格差异，保留 Markdown 缩进；不声称能自动识别所有语义同义句。

Raw 阈值统计**未成功处理的不同观察条目数**。Wiki 阈值统计**该 Skill 未处理过的不同知识正文版本数**，同一页面的多个新正文版本可分别累计；已经见过的页面正文再次提交不作为新增。索引、日志和时间戳放在独立 `metadata`，不参与计数。模型也被要求仅返回实质知识正文；如果调用者将日志写进正文，程序不能从自然语言判断它的真实含义。

`wikiskill_wiki` 可提交完整的 `{name, body}` 页面列表及 metadata。达到阈值的批次冻结输入 ID，生成期间新到的内容留给下一批。同 Git common-dir 下的 worktree 共用项目，非 Git 目录按规范绝对路径区分。未达到阈值时只积累，即使业务任务已经结束，也不会强制优化。

成功处理但没有新知识或 Skill 修改的批次仍被消费，并出具 `no_change` 报告。失败保留输入，不自动反复调用模型。Raw 的消费与 Wiki 写入在一个 SQLite 事务中；Skill 发布记录与 Wiki 消费使用可恢复的发布日志衔接。

## 自动替换、历史与恢复

自动修改的文件是 `SKILL.md`；现有支持脚本、图片、其他资源完整保留。每次有修改时，先在 SQLite 保存整个 Skill 目录的前后内容、二进制文件、空目录、文件权限和差异，再构建替换目录并发布。结构检查会确认 YAML 名称、描述与正文，这是文件格式检查，不是候选效果验证。

同一个 Skill 的锁覆盖模型生成到发布。发布前比较整个目录与生成时的快照，遇到用户并发修改会保留用户文件并记录失败。Wiki 页面也检查生成期间的正文变化。目录替换包含两次 rename；两次之间异常退出时，发布日志和保留目录用于继续恢复。软链接及特殊文件不参与自动管理，会明确报错。

`wikiskill_history(skill_id)` 返回所有历史版本的差异；加上 `version_id` 返回前后完整目录，文件内容以 base64 表示。`wikiskill_rollback(skill_id, version_id, side)` 恢复 `before` 或 `after`，被替换的当前目录也保存为新版本。回退不会重新消费历史 Wiki 输入；将来有新内容达到阈值后仍可继续优化。

`wikiskill_retry(job_id)` 重试失败批次，复用已经保存的模型结果和发布日志，防止重复发布。因用户改动而过期且尚未开始发布的结果，可用 `regenerate: true` 按当前内容重新生成。已经准备发布日志时需先恢复该记录。遇到另一个正在运行的批次会返回忙碌，稍后重试。已完成批次的 retry 只补发缺失报告。

新会话和结果持久化之间若进程异常退出，可能需要在同一会话重新生成该批次；已保存结果及已发布版本不会重复应用。当前业务任务继续使用启动时加载的内容，下个任务才读取更新。

## 查询与报告

| 工具 | 用途 |
| --- | --- |
| `wikiskill_context` | 当前项目 Skill 正文、目录、内容哈希及资源清单 |
| `wikiskill_status` | 两层待处理数、阈值、Skill 范围、最近批次状态 |
| `wikiskill_query` | `raw` 原文、`wiki` 正文或 `reports` 批次详情；支持 key、offset、limit |
| `wikiskill_history` | 历史版本、完整快照与差异 |
| `wikiskill_enroll` | 将已允许的外部 Skill 关联到项目 |
| `wikiskill_rollback` / `wikiskill_retry` | 回退版本、重试失败或补发报告 |

每个批次的报告同步保存为 `~/.wikiskill/reports/<job-id>.json` 和 `.md`，包含会话 ID、输入 ID、实际结果、修改理由、差异和版本 ID。连接失败还没创建会话时，报告中的会话 ID 为空；Codex 无法出具报告时，本地保留 `report_error`。`done` 表示内容处理完成，`report_sent` 单独表示会话报告完成。不要把报告发送失败解释成 Skill 没有发布。

配置、队列、三层数据和全部版本存放在 `~/.wikiskill/state.sqlite3` 及同目录下的 `skills/`、`reports/`、`locks/`。这些文件包含用户主动提交的业务内容，采集前应移除密钥和无关私密信息。没有 WebUI；上述接口可供后续展示使用。

## 功能检查

```bash
uv run --frozen python -m unittest discover -s tests -p 'test_live_*.py' -v
```

测试覆盖真实 MCP stdio 子进程、Codex JSONL 事件交互、独立阈值、元数据排除、幂等采集、Git worktree 归并、用户并发修改、发布中断恢复、完整目录回退、报告补发及失败重试。模型行为用固定响应检查程序流程；真实 Codex 会话检查需使用当前机器的登录与模型配额。

2026-09-08 的真实接入检查在独立临时目录完成 Raw → Wiki → Skill，两个阶段分别创建持久 Codex 会话，均完成本地处理及会话报告；Skill 阶段生成了完整版本记录。该检查使用明确标注为测试的构建观察，仅验证接入流程，没有对生成 Skill 的效果评分。
