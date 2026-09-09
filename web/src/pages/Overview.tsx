import { Activity, ArrowRight, CircleAlert, Folder } from "lucide-react";
import { useResource } from "../api";
import { href, type Route } from "../routing";
import { elapsed, phaseLabel, time } from "../presentation";
import {
  Empty,
  ErrorMessage,
  Fields,
  JobTable,
  Loading,
  QueueMeter,
} from "../components";
import { query } from "../presentation";
import type { Job, JobDetail as JobData, Page, Snapshot } from "../types";

export function Overview({
  snapshot,
  route,
  revision,
  now,
}: {
  snapshot: Snapshot;
  route: Route;
  revision: number;
  now: number;
}) {
  const projects = route.project
    ? snapshot.projects.filter((p) => p.id === route.project)
    : snapshot.projects;
  const jobs = useResource<Page<Job>>(
    "/api/jobs" + query({ project: route.project, limit: 5 }),
    revision,
  );
  const current = useResource<JobData>(
    snapshot.worker.job_id
      ? `/api/jobs/${encodeURIComponent(snapshot.worker.job_id)}`
      : null,
    revision,
  );
  const currentJob =
    current.data && (!route.project || current.data.project === route.project)
      ? current.data
      : null;
  const uniqueSkills = new Set(
    projects.flatMap((p) => p.skills.map((s) => s.id)),
  ).size;
  return (
    <>
      <div className="summary-grid">
        <div className="summary">
          <span>待整理经验</span>
          <strong>
            {projects.reduce((sum, p) => sum + p.raw.pending, 0)}
            <small>条</small>
          </strong>
          <p>项目内去重后的未消费观察</p>
        </div>
        <div className="summary">
          <span>待处理批次</span>
          <strong>
            {projects.reduce((sum, p) => sum + p.active_jobs, 0)}
            <small>个</small>
          </strong>
          <p>已入队及尚未完成的批次</p>
        </div>
        <div className="summary">
          <span>关联 Skill</span>
          <strong>
            {uniqueSkills}
            <small>个</small>
          </strong>
          <p>{projects.length} 个项目中的独立 Skill</p>
        </div>
      </div>
      {projects.some((p) => p.failed_jobs) && (
        <div className="notice warning">
          <CircleAlert size={18} />
          <span>
            {projects.reduce((sum, p) => sum + p.failed_jobs, 0)}{" "}
            个批次失败，输入已保留。
          </span>
          <a href={href("jobs", { project: route.project, state: "failed" })}>
            查看原因 <ArrowRight size={14} />
          </a>
        </div>
      )}
      <section className="section">
        <div className="section-heading">
          <h2>当前处理</h2>
          <span className="eyebrow">WORKER</span>
        </div>
        {currentJob ? (
          <div className="panel current-panel">
            <div>
              <div className="eyebrow">
                {currentJob.project_name} ·{" "}
                {currentJob.stage === "raw" ? "RAW → WIKI" : "WIKI → SKILL"}
              </div>
              <h3 className="current-phase">{phaseLabel(currentJob)}</h3>
              <p className="muted">
                {currentJob.running_confirmed
                  ? "后台正在处理此批次"
                  : "当前显示最后一次记录，运行状态待确认"}
              </p>
            </div>
            <div className="current-facts">
              <Fields
                items={[
                  ["本批输入", `${currentJob.input_count} 条`],
                  [
                    "本次耗时",
                    elapsed(
                      currentJob.started_at,
                      currentJob.finished_at ??
                        (currentJob.running_confirmed
                          ? now
                          : currentJob.last_activity),
                    ),
                  ],
                  ["最近活动", time(currentJob.last_activity)],
                ]}
              />
              <a
                className="button primary"
                href={href("jobs", {
                  project: currentJob.project,
                  job: currentJob.id,
                })}
              >
                查看批次
                <ArrowRight size={16} />
              </a>
            </div>
          </div>
        ) : (
          <div className="panel idle-panel">
            <div className="idle-icon">
              <Activity size={24} />
            </div>
            <div>
              <h3>
                {snapshot.worker.status === "online"
                  ? "等待下一次处理"
                  : snapshot.worker.status === "unknown"
                    ? "尚未记录后台心跳"
                    : "后台状态待确认"}
              </h3>
              <p>
                {snapshot.worker.status === "online"
                  ? "达到阈值的项目会在后台调度检查时进入处理。"
                  : "已有内容和历史仍可查看，运行信息中可查看进程记录。"}
              </p>
            </div>
            <a href={href("system", { project: route.project })}>
              运行信息
              <ArrowRight size={16} />
            </a>
          </div>
        )}
      </section>
      <section className="section">
        <div className="section-heading">
          <h2>项目积累</h2>
          <span className="muted">Raw → Wiki → Skill</span>
        </div>
        <div className="project-grid">
          {projects.map((project) => (
            <article className="panel project-card" key={project.id}>
              <div className="project-card-heading">
                <div className="folder-mark">
                  <Folder size={20} />
                </div>
                <div>
                  <a href={href("overview", { project: project.id })}>
                    <h3>{project.name}</h3>
                  </a>
                  <p className="muted path" title={project.path}>
                    {project.path}
                  </p>
                </div>
                <a
                  className="icon-button"
                  aria-label={`查看 ${project.name} 的知识`}
                  href={href("knowledge", { project: project.id })}
                >
                  <ArrowRight size={17} />
                </a>
              </div>
              <QueueMeter
                label="Raw → Wiki"
                queue={project.raw}
                project={project.id}
              />
              {(route.project
                ? project.skills
                : project.skills.slice(0, 1)
              ).map((skill) => (
                <QueueMeter
                  key={skill.id}
                  label={`Wiki → ${skill.name}`}
                  queue={skill.wiki}
                  project={project.id}
                  skill={skill.id}
                />
              ))}
              <div className="project-card-footer">
                <span>{project.wiki_pages} 个 Wiki 页面</span>
                <a href={href("skills", { project: project.id })}>
                  {project.skills.length} 个 Skill
                  <ArrowRight size={13} />
                </a>
              </div>
            </article>
          ))}
        </div>
      </section>
      <section className="section panel">
        <div className="panel-heading">
          <h2>最近批次</h2>
          <a href={href("jobs", { project: route.project })}>
            查看全部
            <ArrowRight size={14} />
          </a>
        </div>
        <ErrorMessage error={jobs.error} />
        {jobs.data?.items.length ? (
          <JobTable jobs={jobs.data.items} />
        ) : jobs.loading ? (
          <Loading />
        ) : (
          <Empty title="还没有优化批次">
            达到内容阈值后，批次会自动出现在这里。
          </Empty>
        )}
      </section>
    </>
  );
}
