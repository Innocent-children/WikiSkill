import type { Job, Queue } from "./types";

export const phaseNames: Record<string, string> = {
  "job.queued": "批次已入队",
  "model.response": "收到模型响应",
  "job.started": "准备批次输入",
  "codex.connecting": "连接 Codex",
  "codex.session": "会话已创建",
  "job.generating": "生成内容",
  "job.prepared": "生成结果已保存",
  "wiki.updated": "Wiki 已写入",
  "publication.prepared": "版本快照已保存",
  "publication.applied": "Skill 已发布",
  "job.applied": "内容处理完成",
  "report.sending": "发送会话报告",
  "report.sent": "会话报告已发送",
  "report.failed": "会话报告发送失败",
  "job.done": "批次完成",
  "job.failed": "批次执行失败",
  "job.retried": "批次重新入队",
};
export const contentNames: Record<string, string> = {
  pending: "尚未写入",
  published: "Skill 已发布",
  publication_pending: "发布待确认",
  no_change: "无新增版本",
  wiki_written: "Wiki 已更新",
  failed: "未完成处理",
};
export const reportNames: Record<string, string> = {
  sending: "正在发送",
  unconfirmed: "发送待确认",
  sent: "已发送",
  failed: "发送失败",
  pending: "尚未发送",
};
export function phaseLabel(job: Job): string {
  if (job.state === "failed") return "处理失败";
  if (job.state === "done") return "处理完成";
  if (job.phase === "report.sending") return "发送报告";
  if (job.state === "generating")
    return job.stage === "raw" ? "生成 Wiki" : "生成 Skill";
  if (job.state === "prepared") return "准备写入";
  if (job.state === "applied") return "内容已处理";
  if (job.phase === "job.started") return "准备输入";
  return job.phase === "codex.connecting" || job.phase === "codex.session"
    ? "连接 Codex"
    : "等待处理";
}
export function unconfirmed(job: Job): boolean {
  return (
    !job.running_confirmed &&
    !["done", "failed"].includes(job.state) &&
    (job.state !== "queued" ||
      (!!job.phase &&
        !["job.queued", "job.retried", "job.manual"].includes(job.phase)))
  );
}
export function queueLabel(queue: Queue): string {
  const labels: Record<string, string> = {
    config_error: "配置读取失败",
    disabled: "等待手动选择",
    failed: "失败批次待重试",
    shared_busy: "等待共享 Skill 的其他批次",
    queued: "已入队，等待执行",
    processing: "本批正在处理",
    ready: "等待自动分析",
    scheduled: `下次分析：${time(queue.next_analysis_at)}`,
    accumulating: "等待新增内容",
  };
  return labels[queue.reason] ?? "等待处理";
}
export function time(value: number | null | undefined, full = false): string {
  return value == null
    ? "未记录"
    : new Date(value * 1000).toLocaleString(
        "zh-CN",
        full
          ? undefined
          : {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            },
      );
}
export function elapsed(
  start: number | null,
  end: number | null | undefined,
): string {
  if (start == null || end == null) return "未记录";
  const seconds = Math.max(0, Math.floor(end - start));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}
export function short(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "未记录";
}
export function query(
  params: Record<string, string | number | undefined | null>,
): string {
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== null && value !== "")
      values.set(key, String(value));
  return values.size ? `?${values.toString()}` : "";
}
