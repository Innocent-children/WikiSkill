# WebUI 与开发

知识工作台是主要入口：项目选择、Raw 原文、Wiki 编辑、Skill 生成与安装。执行记录展示批次阶段、固定输入、报告、失败和重试；历史页面展示完整 Skill 目录差异与资源下载；设置页面配置两阶段阈值、执行模型、密钥及目录。

HTTP 仅接受本机 Host，浏览器访问要求同源。JSON 写操作返回输入错误或冲突提示；模型任务入队后由后台执行。GET 原文提供原始字节 base64 和单独的展示文本，未知编码的展示替代字符不会修改保存的字节。

## 开发

```bash
uv sync --frozen
uv run python -m unittest discover -s tests
cd web
npm ci
npm test
npm run build
```

前端使用 React、TypeScript、Vite；需要 Node.js 22.12+。构建资源写入 `wikiskill/live/web_assets`，Python 包直接提供这些资源。Python 测试使用临时目录、模拟 JSONL 和本地协议桩，避免写入正式数据。

后端职责：`capture.py` 读取轨迹；`Store` 管理事务与查询；`Runtime` 调度和转换；`models.py` 适配 API；`CodexSession` 管理 app-server；`SkillManager` 发布恢复；`Installer` 复制到 Codex；`ReadView`/`TraceView` 生成读取数据；`web.py` 提供同源 HTTP 操作。

OpenSpec 变更位于 `openspec/changes/raw-webui-platform`。代码和规格描述同一个完整流程，实验内容不再包含于发行包。
