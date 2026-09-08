import { ArrowRight } from "lucide-react";
import { href } from "../routing";
import { elapsed, short, time } from "../presentation";
import { ErrorMessage, Fields } from "../components";
import type { Snapshot } from "../types";

export function System({ snapshot, now }: { snapshot: Snapshot; now: number }) {
  const worker = snapshot.worker;
  const config = snapshot.config;
  return (
    <div className="system-grid">
      <section className="panel document-panel">
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
      </section>
      <section className="panel document-panel">
        <div className="eyebrow">CONFIGURATION</div>
        <h2>当前配置</h2>
        {config ? (
          <Fields
            items={[
              ["Raw 阈值", `${config.raw_threshold} 条不同观察`],
              ["Wiki 阈值", `${config.wiki_threshold} 个不同正文版本`],
              ["调度检查间隔", `${config.poll_seconds} 秒`],
              ["模型超时", `${config.timeout_seconds} 秒`],
              ["模型", config.model || "继承 Codex 默认模型"],
              ["自动启动", config.auto_start ? "开启" : "关闭"],
              ["外部 Skill 管理", config.manage_external ? "开启" : "关闭"],
              [
                "允许的外部目录",
                config.external_skills.length
                  ? config.external_skills.map((path) => (
                      <div key={path} className="path">
                        {path}
                      </div>
                    ))
                  : "未配置",
              ],
            ]}
          />
        ) : (
          <ErrorMessage error={snapshot.config_error} />
        )}
      </section>
      <section className="panel document-panel system-storage">
        <div className="eyebrow">LOCAL STORAGE</div>
        <h2>本地数据</h2>
        <Fields
          items={[
            ["数据目录", <code>{snapshot.root}</code>],
            ["配置文件", <code>{snapshot.root}/config.json</code>],
            ["数据库", <code>{snapshot.root}/state.sqlite3</code>],
            ["Worker 日志", <code>{snapshot.root}/worker.log</code>],
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
      </section>
    </div>
  );
}
