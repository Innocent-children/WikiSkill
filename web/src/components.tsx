import React, { memo } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Folder,
  Layers3,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { href, type View } from "./routing";
import {
  contentNames,
  phaseLabel,
  queueLabel,
  reportNames,
  short,
  time,
  unconfirmed,
} from "./presentation";
import type { Job, Project, Queue } from "./types";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      <span className="status-dot" />
      {children}
    </span>
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <Layers3 aria-hidden="true" size={28} />
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  );
}

export function ErrorMessage({ error }: { error: string | null }) {
  return error ? (
    <div className="notice danger" role="alert">
      <CircleAlert size={18} />
      <span>{error}</span>
    </div>
  ) : null;
}

export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="loading-dot" />
      正在读取…
    </div>
  );
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
          img: ({ alt }) => (
            <span className="muted">[图片：{alt || "资源文件"}]</span>
          ),
          a: ({ href: url, children }) => (
            <a href={url} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export function Fields({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="fields">
      {items.map(([label, value]) => (
        <React.Fragment key={label}>
          <dt>{label}</dt>
          <dd>{value ?? "未记录"}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function Pager({
  offset,
  next,
  onPage,
}: {
  offset: number;
  next: number | null;
  onPage: (offset: number) => void;
}) {
  return (
    <div className="pager">
      <span>第 {Math.floor(offset / 20) + 1} 页</span>
      <button
        className="icon-button"
        aria-label="上一页"
        disabled={offset === 0}
        onClick={() => onPage(Math.max(0, offset - 20))}
      >
        <ChevronLeft size={18} />
      </button>
      <button
        className="icon-button"
        aria-label="下一页"
        disabled={next === null}
        onClick={() => next !== null && onPage(next)}
      >
        <ChevronRight size={18} />
      </button>
    </div>
  );
}

export function Back({
  to,
  children,
}: {
  to: string;
  children: React.ReactNode;
}) {
  return (
    <a className="back" href={to}>
      <ArrowLeft size={16} />
      {children}
    </a>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="code-block">{JSON.stringify(value, null, 2)}</pre>;
}

export function DocumentTabs({
  values,
  active,
  onChange,
}: {
  values: { id: string; label: string }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="tabs" aria-label="详情视图">
      {values.map((item) => (
        <button
          key={item.id}
          type="button"
          aria-pressed={item.id === active}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function QueueMeter({
  label,
  queue,
  project,
  skill,
}: {
  label: string;
  queue: Queue;
  project: string;
  skill?: string;
}) {
  const ratio = queue.threshold
    ? Math.min(100, (queue.pending / queue.threshold) * 100)
    : 0;
  return (
    <div className="queue-meter">
      <div className="meter-title">
        <span>{label}</span>
        <span className="tabular">
          <strong>{queue.pending}</strong>
          <span className="muted"> / {queue.threshold ?? "—"}</span>
        </span>
      </div>
      <div
        className="meter-track"
        role="progressbar"
        aria-label={`${label}待处理数量`}
        aria-valuenow={queue.pending}
        aria-valuemin={0}
        aria-valuemax={Math.max(queue.threshold ?? 1, queue.pending, 1)}
      >
        <span style={{ width: `${ratio}%` }} />
      </div>
      <div className="meter-note">
        <span className={queue.reason === "failed" ? "text-danger" : ""}>
          {queueLabel(queue)}
        </span>
        {queue.job_id && (
          <a
            href={href("jobs", {
              project: queue.job_project || project,
              job: queue.job_id,
            })}
          >
            查看批次 <ArrowRight size={12} />
          </a>
        )}
      </div>
      {queue.batched > 0 && (
        <div className="muted small">
          本批 {queue.batched} 条 · 后续 {queue.waiting} 条
        </div>
      )}
      {skill && (
        <a
          className="small subdued-link truncate"
          href={href("skills", { project, skill })}
        >
          查看 Skill <ArrowRight size={12} />
        </a>
      )}
    </div>
  );
}

export function ProjectChooser({
  projects,
  view,
}: {
  projects: Project[];
  view: View;
}) {
  return (
    <div className="project-grid">
      {projects.map((project) => (
        <a
          className="project-select panel"
          key={project.id}
          href={href(view, { project: project.id })}
        >
          <Folder size={22} />
          <div>
            <h3>{project.name}</h3>
            <span className="muted path">{project.path}</span>
          </div>
          <ArrowRight size={18} />
        </a>
      ))}
    </div>
  );
}

export function JobTable({ jobs }: { jobs: Job[] }) {
  return (
    <div className="table-scroll">
      <table className="jobs-table">
        <thead>
          <tr>
            <th>批次 / 项目</th>
            <th>执行阶段</th>
            <th>内容结果</th>
            <th>会话报告</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <a
                  className="job-title"
                  href={href("jobs", { project: job.project, job: job.id })}
                >
                  {job.stage === "raw" ? "Raw → Wiki" : "Wiki → Skill"}
                  <ArrowRight size={14} />
                </a>
                <div className="muted small">
                  {job.project_name} · {short(job.id)}
                </div>
              </td>
              <td>
                <Badge
                  tone={
                    job.state === "failed"
                      ? "danger"
                      : job.state === "done"
                        ? "neutral"
                        : job.running_confirmed
                          ? "blue"
                          : "amber"
                  }
                >
                  {unconfirmed(job) ? "状态待确认" : phaseLabel(job)}
                </Badge>
              </td>
              <td>
                <span
                  className={
                    job.content_status === "publication_pending"
                      ? "text-warning"
                      : ""
                  }
                >
                  {contentNames[job.content_status]}
                </span>
              </td>
              <td>
                <span
                  className={
                    job.report_status === "failed" ? "text-warning" : "muted"
                  }
                >
                  {reportNames[job.report_status]}
                </span>
              </td>
              <td className="muted tabular">{time(job.created)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ObservationBody({
  body,
}: {
  body: { problem: string; action: string; outcome: string; lesson: string };
}) {
  return (
    <Fields
      items={[
        ["问题", body.problem],
        ["处理", body.action || "未填写"],
        ["结果", body.outcome],
        ["经验", body.lesson || "未填写"],
      ]}
    />
  );
}
