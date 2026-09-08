# 验证记录

日期：2026-09-08。环境：macOS arm64、Python 3.11.15、Docker Engine 29.7.2。

## 最终结果

完整测试启用 Docker 和真实 ALFWorld 游戏后，**51 项全部通过，无跳过**，运行时间 11.474 秒。

```bash
WIKISKILL_DOCKER_TEST=1 \
WIKISKILL_ALFWORLD_GAME=/absolute/path/game.tw-pddl \
python -m unittest discover -s tests -v
```

| 检查内容 | 实际验证 |
| --- | --- |
| 原始提示词 | 七份文件逐一核对来源记录中的 SHA-256，验证运行时占位符填充 |
| 核心演化 | 全量训练、分层抽样、增量维护、至少四条记录、严格接受、持平拒绝、Wiki 保留 |
| 失败与恢复 | 请求失败、评分失败、回合耗尽、空模式更新、继续执行和原始文件拒绝覆盖 |
| 读取范围 | 推理不取得答案和 PURPOSE；Proposer 不取得验证、测试轨迹；无 Wiki 消融关闭历史读取 |
| Gemini | 经本地 HTTP 服务验证原生消息、函数声明、函数结果、调用 ID、思考签名、usage 和 token 上限 |
| Chat Completions | 经本地 HTTP 服务完成演化，验证认证头、工具历史、重试、错误与 CLI |
| SealQA | 验证 Google 查询参数、结果文件、独立判分输入、原始 A/B/C 模板和非法判分处理 |
| OfficeQA | 参考页、glob / grep / read，以及官方数值与混合日期文本评分 |
| SpreadsheetBench | 真正启动容器，写入 SUM 公式，LibreOffice 重算得到 5，评分并归档工作簿 |
| 表格失败路径 | 缺失和损坏工作簿、未重算参考答案、hard / soft 聚合、超时和只读系统目录 |
| ALFWorld | 加载官方游戏，依次移动、取书、移动、放书，由真实模拟器返回 won=True |
| 消融与迁移 | 四种 Wiki 访问组合、训练限定的 Wiki 输入、多来源迁移及无 Skill 对照 |
| 统计 | 三次运行聚合逻辑、按任务配对、等权宏平均、显著性分组、环境种子与资产一致性检查 |
| 数据与对照结果 | 明确 ID 划分、规模校验、转换映射、逐题外部分数导入 |

ALFWorld 集成使用官方 `json_2.1.2_tw-pddl.zip` 中的一个真实游戏，以文件给出的动作序列作为测试模型输出；它验证环境执行与评分，不评估大模型能力。表格集成同样使用受控模型响应，实际执行 Docker 和 LibreOffice。

## 安装验证

- `uv.lock` 已更新，包含当前依赖和可选基准依赖。
- Wheel 构建成功，含提示词、来源清单、Dockerfile、OfficeQA 评分模块和许可。
- 在全新的 Python 3.11 虚拟环境安装 wheel，从源码目录外运行。
- 安装后的完整离线演化演示、提示词资源读取及官方 OfficeQA 评分均通过。
- `prepare` 和 `init` 的文档示例实际执行成功，init 的模型调用数为 0。

演示结果副本见 [demo-report.json](demo-report.json)。其 `research_result` 为 false。

## 本次没有声称完成的实验

没有使用真实模型 API 测量五套完整基准，也没有给出论文成绩复现的结论。Google Search 和判分输入使用受控响应验证；Gemini 和 Chat Completions 使用本地 HTTP 服务验证协议。

模型权重、API 额度及作者未公开的划分 ID 需要运行者提供。功能对应关系、未公开参数和明确不纳入的对照算法本体见 [论文功能清单](paper-mapping.md)。
