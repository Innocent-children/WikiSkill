import { ArrowRight } from "lucide-react";
import { href } from "../routing";
import { elapsed, short, time } from "../presentation";
import { ErrorMessage, Fields } from "../components";
import type { Snapshot } from "../types";
import { Settings } from "./Manage";
import { useAction } from "../actions";
import { mutate, useResource } from "../api";

export function System({ snapshot, now }: { snapshot: Snapshot; now: number }) {
  const worker = snapshot.worker;
  const config = snapshot.config;
  const action = useAction();
  const services = useResource<{ state: string; url: string | null; collector: string; worker: string }>(
    "/api/services", Math.floor(now / 5),
  );
  const rootArgument = "'" + snapshot.root.replaceAll("'", "'\\''") + "'";
  return (
    <div className="settings-layout">
      <section className="panel document-panel">
        <h2>开始使用 WikiSkill</h2>
        <p>先选择下面的整理方式，再进入工作台查看原始记录、整理知识文档和生成 Skill。</p>
        <p>后台启动后会自动采集新对话；首次从现在开始，已有对话可通过“导入已有历史”读取。缺少模型设置时仍可查看记录。</p>
        <div className="manage-actions">
          <a href={href("manage")}>进入知识工作台 <ArrowRight size={14} /></a>
        </div>
        <details>
          <summary>可选：连接 Codex MCP</summary>
          <p>采集本机轨迹可以独立运行。需要 Codex 查询或写入知识时，运行下面的命令，将输出的 mcp_command 注册为本机 stdio MCP，然后重新连接客户端。</p>
          <pre className="code-block">wikiskill --root {rootArgument} init</pre>
        </details>
      </section>
      <Settings />
      <section className="panel document-panel">
        <h2>数据接入与后台</h2>
        <p className="muted">
          导入本机已有对话；后台负责采集新记录和执行生成。
        </p>
        <ErrorMessage error={services.error} />
        {services.data && <p role="status">
          控制面板：{services.data.state === "running" ? "运行中" : services.data.state === "degraded" ? "部分后台已停止" : "独立页面，后台未连接"}
          {" · "}原文采集：{services.data.collector === "running" ? "运行中" : "未运行"}
          {" · "}知识整理：{services.data.worker === "running" ? "运行中" : "未运行"}
        </p>}
        <p className="muted small">关闭页面或终端后后台继续运行。停止后可再次运行 wikiskill 打开面板。</p>
        <pre className="code-block">wikiskill --root {rootArgument} stop</pre>
        <div className="manage-actions">
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(
                () => mutate("/api/history-import"),
                "已请求导入本机历史，对应项目和记录将在扫描后出现。",
              )
            }
          >
            导入已有历史
          </button>
          <button
            disabled={action.busy || services.data?.state === "running"}
            onClick={() =>
              void action.run(
                () => mutate("/api/start"),
                "后台已就绪，连接状态会自动更新。",
              )
            }
          >
            {services.data?.state === "running" ? "后台运行中" : "启动后台"}
          </button>
        </div>
        {action.status}
      </section>
      <details className="panel document-panel runtime-details">
        <summary>后台运行信息</summary>
        <div className="eyebrow">RUNTIME</div>
        <h2>后台进程</h2>
        <Fields
          items={[
            [
              "状态",
              worker.status === "online"
                ? "在线"
                : worker.status === "stale"
                  ? "心跳过期，运行状态待确认"
                  : worker.status === "stopped"
                    ? "已停止"
                    : "尚未记录心跳",
            ],
            ["进程 PID", worker.pid],
            ["启动时间", time(worker.started, true)],
            ["最近心跳", time(worker.heartbeat, true)],
            [
              "距离心跳",
              worker.heartbeat ? elapsed(worker.heartbeat, now) : "未记录",
            ],
            [
              "当前批次",
              worker.job_id ? (
                <a href={href("jobs", { job: worker.job_id })}>
                  <code>{short(worker.job_id)}</code>
                  <ArrowRight size={14} />
                </a>
              ) : (
                "没有正在记录的批次"
              ),
            ],
            ["最近活动", time(worker.last_activity, true)],
          ]}
        />
        {worker.status === "unknown" && (
          <p className="muted">
            新版本 worker 启动后会记录心跳；已有批次和内容仍可查看。
          </p>
        )}
        <ErrorMessage error={worker.error || null} />
      </details>
      <details className="panel document-panel runtime-details">
        <summary>本地数据与文件位置</summary>
        <div className="eyebrow">LOCAL STORAGE</div>
        <h2>本地数据</h2>
        <Fields
          items={[
            ["数据目录", <code>{snapshot.root}</code>],
            ["配置文件", <code>{snapshot.root}/config.json</code>],
            ["数据库", <code>{snapshot.root}/state.sqlite3</code>],
            ["Worker 日志", <code>{snapshot.root}/worker.log</code>],
            ["服务启动日志", <code>{snapshot.root}/service.log</code>],
            ["报告目录", <code>{snapshot.root}/reports/</code>],
            [
              "Codex 启动命令",
              config ? (
                <code>{JSON.stringify(config.codex_command)}</code>
              ) : (
                "配置不可用"
              ),
            ],
          ]}
        />
      </details>
    </div>
  );
}
