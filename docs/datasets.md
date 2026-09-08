# 五类任务的数据要求

数据目录提供 `train.jsonl`、`validation.jsonl`、`test.jsonl`。资产路径相对数据目录；可以通过 `prepare` 从源目录复制。`files` 是相对路径到文本内容的映射，`context` 是公开信息，答案路径放在私有的 `evaluation`。

## LiveMath

```json
{"id":"math-1","prompt":"Which number is even?","answer":"B","context":{"choices":{"A":"3","B":"4"}}}
```

choices 可以是标签到内容的对象，或按 A、B、C 顺序的数组。题目自带选项时可省略。默认按选择标签精确评分。

## SealQA

```json
{"id":"seal-1","prompt":"What is the requested factual value?","answer":"reference answer"}
```

配置 Google API 环境变量和独立 judge_model。搜索结果保存在代理可读的 `search/results-N.json`。判分模型取得问题、参考答案和预测，返回 A / B / C；只有 A 得 1 分。服务错误或非法判分会中止本轮，不会假装得到有效验证分数。

论文使用 2026 年 7 月版本，准备数据时使用对应快照；程序不会自动用最新版替换。

## SpreadsheetBench

```json
{"id":"sheet-1","prompt":"Sum B1 and C1 into A1.","answer":"completed workbook","context":{"instruction_type":"Cell-Level Manipulation","answer_position":"Sheet1!A1","cases":[{"input_workbook":"spreadsheet/sheet-1/1_input.xlsx"}]},"evaluation":{"reference_workbooks":["spreadsheet/sheet-1/1_answer.xlsx"]}}
```

instruction_type 为 Cell-Level Manipulation 或 Sheet-Level Manipulation。answer_position 支持单元格、范围、逗号分隔的多范围和带引号的工作表名。每个公开 case 对应一个私有参考工作簿，可以提供原基准的三个测试工作簿。

每个 case 单独执行，模型只得到输入副本。默认 hard 要求全部正确，soft 取均值，记录同时给出两者。参考工作簿公式需已重算并保存缓存值。

`prepare --benchmark spreadsheet` 的源记录字段为 id、instruction、instruction_type、answer_position。默认读取 `spreadsheet/<id>/<1|2|3>_<id>_input.xlsx` 与对应 `_answer.xlsx`；也可以显式提供 cases 和 reference_workbooks。

## OfficeQA

```json
{"id":"office-1","prompt":"What was the reported amount?","answer":"543 million","context":{"oracle_pages":[{"path":"documents/bulletin.txt","start_line":10,"end_line":35}]}}
```

解析后的 Treasury 文档放到 documents/，配置 documents_root。oracle_pages 的每项可以是直接文本，或文档路径加 1 开始的行号范围。所有参考页进入第一次请求，代理仍可用 glob / grep / read 查全文。

也可把文档直接放入每条记录的 files，此时不配置 documents_root。使用官方 score_answer，relative_tolerance 为全局相对误差，默认 0。

## ALFWorld

```json
{"id":"alf-1","prompt":"put a book in sofa.","answer":"success","context":{"game_file":"games/alf-1/game.tw-pddl"}}
```

game_file 指向官方生成的 .tw-pddl。适配器通过 TextWorld / ALFWorld 加载逻辑；模型只取得当前观察、最近历史和可用动作。分数来自模拟器 won，不按模型自行声称的 success 评分。

max_steps、history_length、seed 是配置项。无效动作记入反馈，达到步数上限即结束。

## prepare 源文件

源文件是 JSON 数组。LiveMath、SealQA、OfficeQA 使用 id、question、answer，按类型额外提供 choices 或 oracle_pages。ALFWorld 使用 id、task_description、game_file。files、context、evaluation 可随记录提供。

划分文件：

```json
{"train":["id1","id2","id3","id4"],"validation":["id5"],"test":["id6"]}
```

特殊字符 ID 转为稳定任务 ID，映射保存在 preparation.json。不同划分的重复 ID 或完全相同输入会被拒绝。

| 论文规模检查 | Train | Validation | Test |
| --- | ---: | ---: | ---: |
| live_math | 35 | 18 | 124 |
| sealqa | 16 | 10 | 85 |
| spreadsheet | 80 | 40 | 280 |
| officeqa | 50 | 24 | 172 |
| alfworld | 39 | 18 | 134 |

数量相同不代表与作者使用同一批样本。外部对照分数通过 import-results 导入时，必须包含转换后测试集的全部任务 ID，每条恰好一次。
