# 参与开发

## 本地环境

需要 Python 3.11+、uv 和 Node.js 22.12+。在仓库根目录执行：

```bash
uv sync --frozen
cd web
npm ci
npm run build
cd ..
uv run --frozen wikiskill --root /absolute/path/to/wikiskill-dev
```

开发时使用独立数据目录。前端采用 React、TypeScript 和 Vite，`npm run build` 将资源写入 `wikiskill/live/web_assets`；这些资源随 Python 包分发。修改前端后提交对应生产构建产物。

需要热更新时，在一个终端运行 `uv run --frozen wikiskill --root /absolute/path/to/wikiskill-dev web --port 8765`，另一个终端在 `web` 目录执行 `npm run dev`。Vite 的 `/api` 代理指向 `127.0.0.1:8765`，后端端口需与之对应。前台 `web` 只提供页面服务；需要执行入队批次时，另开终端用相同 `--root` 运行 `worker`。自动采集另行运行 `collector`，独立进程由各自终端管理。

## 检查改动

前端依赖安装后，在 `web` 目录执行：

```bash
npm test
npm run build
```

构建完成后，在仓库根目录执行 Python 测试，避免构建清空资源目录干扰服务启动测试：

```bash
uv run --frozen python -m unittest discover -s tests
git diff --check
```

按改动范围运行相关检查。文档修改检查内容、相对链接和命令；前端修改运行相关页面测试和构建；涉及公共运行入口、存储或调度时运行完整 Python 套件。不要将模拟协议通过描述为真实模型效果已验证。

Python 测试使用临时目录、模拟 JSONL 和本地协议桩；前端使用 Vitest / Testing Library。真实模型检查需显式运行，例如：

```bash
uv run --frozen python scripts/verify_ollama_workflow.py --model qwen3.5:9b
```

此脚本需要已启动的本机 Ollama 和已下载模型，使用合成轨迹检查生成流程，保留隔离数据及模型响应，不修改现有知识库或安装 Skill。它不评测论文成绩或实际业务效果。

## 修改与提交

- 先确认模块职责和已有实现方式，技术流程见 [技术参考](docs/architecture.md)。业务行为放入对应业务模块，配置类负责配置值和校验。
- 覆盖核心成功路径、主要失败路径以及本次修复的具体问题。模型调用使用协议桩，真实服务检查单独说明。
- 原始论文提示词保持原文；接口适配由宿主实现，并同步 [适配说明](wikiskill/live/prompts/ADAPTATIONS.md)。
- 文档只描述当前行为。使用和配置集中在 [README](README.md)，开发操作放在本文件，内部协议放在技术参考；同一规则只维护一处，其他位置链接过去。
- Commit 使用英文，标题为 `feat: ...`、`fix: ...`、`docs: ...` 等，正文说明具体改动及作用。PR 描述问题、最终行为和实际执行的检查；未执行的相关检查明确说明。

## 报告问题

在 [Issues](https://github.com/Innocent-children/WikiSkill/issues) 中附上系统、Python/Node 版本、执行方式、复现步骤、预期与实际结果，以及相关错误。提交前移除 key、认证信息和私人会话内容；优先提供最小合成输入。

仓库未声明项目代码许可证，第三方提示词说明见 [NOTICE](NOTICE.md)。
