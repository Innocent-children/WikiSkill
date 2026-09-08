import { useState } from "react";
import { ArrowDownToLine, ArrowRight, CircleAlert, Check } from "lucide-react";
import { useResource } from "../api";
import { href, navigate, type Route } from "../routing";
import {
  contentNames,
  elapsed,
  phaseLabel,
  phaseNames,
  query,
  reportNames,
  short,
  time,
  unconfirmed,
} from "../presentation";
import {
  Back,
  Badge,
  DocumentTabs,
  Empty,
  ErrorMessage,
  Fields,
  JobTable,
  Json,
  Loading,
  Markdown,
  ObservationBody,
  Pager,
} from "../components";
import type { Event, Input, Job, JobDetail as JobData, Page } from "../types";

export function Jobs({ route, revision }: { route: Route; revision: number }) {
  const offset = Math.max(0, Number(route.offset) || 0);
  const jobs = useResource<Page<Job>>(
    "/api/jobs" +
      query({
        project: route.project,
        state: route.state,
        stage: route.stage,
        offset,
      }),
    revision,
  );
  const change = (changes: Partial<Route>) =>
    navigate("jobs", {
      project: route.project,
      state: route.state,
      stage: route.stage,
      ...changes,
    });
  return (
    <>
      <div className="toolbar">
        <label>
          执行状态
          <select
            value={route.state || ""}
            onChange={(e) => change({ state: e.target.value })}
          >
            <option value="">全部状态</option>
            <option value="queued">等待处理</option>
            <option value="generating">正在生成</option>
            <option value="prepared">结果已保存</option>
            <option value="applied">内容已处理</option>
            <option value="done">处理完成</option>
            <option value="failed">处理失败</option>
          </select>
        </label>
        <label>
          处理阶段
          <select
            value={route.stage || ""}
            onChange={(e) => change({ stage: e.target.value })}
          >
            <option value="">全部阶段</option>
            <option value="raw">Raw → Wiki</option>
            <option value="skill">Wiki → Skill</option>
          </select>
        </label>
      </div>
      <ErrorMessage error={jobs.error} />
      <div className="panel">
        {jobs.data?.items.length ? (
          <JobTable jobs={jobs.data.items} />
        ) : jobs.loading ? (
          <Loading />
        ) : (
          <Empty title="没有符合条件的批次">
            可以调整筛选条件，或等待项目内容达到阈值。
          </Empty>
        )}
        {jobs.data && (
          <Pager
            offset={offset}
            next={jobs.data.next_offset}
            onPage={(offset) => change({ offset: String(offset) })}
          />
        )}
      </div>
    </>
  );
}

export function JobDetail({
  id,
  revision,
  now,
}: {
  id: string;
  revision: number;
  now: number;
}) {
  const [tab, setTab] = useState("overview");
  const [inputOffset, setInputOffset] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);
  const job = useResource<JobData>(
    `/api/jobs/${encodeURIComponent(id)}`,
    revision,
  );
  const inputs = useResource<Page<Input>>(
    tab === "inputs"
      ? `/api/jobs/${encodeURIComponent(id)}/inputs${query({ offset: inputOffset, limit: 20 })}`
      : null,
    revision,
  );
  const events = useResource<Page<Event>>(
    `/api/jobs/${encodeURIComponent(id)}/events${query({ offset: eventOffset, limit: 20 })}`,
    revision,
  );
  if (!job.data)
    return job.error ? <ErrorMessage error={job.error} /> : <Loading />;
  const data = job.data;
  return (
    <>
      <Back to={href("jobs", { project: data.project })}>全部批次</Back>
      <div className="detail-heading">
        <div>
          <div className="eyebrow">
            {data.project_name} ·{" "}
            {data.stage === "raw" ? "RAW → WIKI" : "WIKI → SKILL"}
          </div>
          <h2>{data.stage === "raw" ? "整理项目经验" : "更新项目 Skill"}</h2>
          <code>{data.id}</code>
        </div>
        <Badge
          tone={
            data.state === "failed"
              ? "danger"
              : data.state === "done"
                ? "green"
                : data.running_confirmed
                  ? "blue"
                  : "amber"
          }
        >
          {data.state === "done"
            ? "处理完成"
            : data.state === "failed"
              ? "处理失败"
              : data.running_confirmed
                ? "正在执行"
                : data.state === "queued" && !unconfirmed(data)
                  ? "等待执行"
                  : "状态待确认"}
        </Badge>
      </div>
      <ErrorMessage error={job.error} />
      {data.error && (
        <div className="notice danger">
          <CircleAlert size={18} />
          <span>{data.error}</span>
        </div>
      )}
      {data.report_error && (
        <div className="notice warning">
          <CircleAlert size={18} />
          <span>
            会话报告发送失败：{data.report_error}。内容结果请查看下方记录。
          </span>
        </div>
      )}
      <DocumentTabs
        values={[
          { id: "overview", label: "处理过程" },
          { id: "inputs", label: `本批输入 · ${data.input_count}` },
          { id: "report", label: "结果与报告" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "overview" && (
        <div className="detail-grid">
          <div className="panel stage-panel">
            <span className="eyebrow">当前阶段</span>
            <h3 className="stage-title">{phaseLabel(data)}</h3>
            <p className="muted">
              {unconfirmed(data)
                ? "这是最后记录的阶段；尚未确认 worker 仍在执行。"
                : data.summary ||
                  "本批输入已固定，处理期间新增的内容会继续积累。"}
            </p>
            <div className="result-pair">
              <div>
                <span>内容结果</span>
                <strong>{contentNames[data.content_status]}</strong>
              </div>
              <div>
                <span>会话报告</span>
                <strong
                  className={
                    data.report_status === "failed" ? "text-warning" : ""
                  }
                >
                  {reportNames[data.report_status]}
                </strong>
              </div>
            </div>
            <Fields
              items={[
                ["本批输入", `${data.input_count} 条`],
                ["创建时间", time(data.created, true)],
                ["开始时间", time(data.started_at, true)],
                [
                  "本次耗时",
                  elapsed(
                    data.started_at,
                    data.finished_at ??
                      (data.running_confirmed ? now : data.last_activity),
                  ),
                ],
                ["重试次数", data.retry_count],
                ["Codex 会话", <code>{data.thread_id || "尚未创建"}</code>],
                [
                  "Skill 版本",
                  data.version_id && data.skill ? (
                    <a
                      href={href("skills", {
                        project: data.project,
                        skill: data.skill,
                        version: data.version_id,
                      })}
                    >
                      {short(data.version_id)}
                      <ArrowRight size={14} />
                    </a>
                  ) : (
                    "没有发布版本"
                  ),
                ],
              ]}
            />
          </div>
          <div className="panel timeline-panel">
            <div className="eyebrow">HISTORY</div>
            <h3>处理时间线</h3>
            <ErrorMessage error={events.error} />
            {events.data?.items.length ? (
              <ol className="timeline">
                {events.data.items.map((event) => (
                  <li
                    key={event.seq}
                    className={event.kind.endsWith("failed") ? "failed" : ""}
                  >
                    <span className="timeline-dot" />
                    <div>
                      <span>{phaseNames[event.kind] || event.kind}</span>
                      <time>{time(event.created, true)}</time>
                      {typeof event.payload.error === "string" && (
                        <p className="text-danger">{event.payload.error}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">这个批次没有记录阶段时间。</p>
            )}
            {events.data &&
              (events.data.next_offset !== null || eventOffset > 0) && (
                <Pager
                  offset={eventOffset}
                  next={events.data.next_offset}
                  onPage={setEventOffset}
                />
              )}
          </div>
        </div>
      )}
      {tab === "inputs" && (
        <>
          <ErrorMessage error={inputs.error} />
          {inputs.loading && !inputs.data ? (
            <Loading />
          ) : (
            <div className="document-list">
              {inputs.data?.items.map((input) => (
                <article key={input.id} className="panel document-panel">
                  <div className="document-heading">
                    <h3>{input.name || `观察 #${input.id}`}</h3>
                    {input.raw_id && (
                      <a
                        href={href("knowledge", {
                          project: data.project,
                          layer: "raw",
                          raw: input.raw_id,
                        })}
                      >
                        原始提交
                        <ArrowRight size={14} />
                      </a>
                    )}
                    {input.name && (
                      <a
                        href={href("knowledge", {
                          project: data.project,
                          layer: "wiki",
                          wiki: input.name,
                        })}
                      >
                        知识页面
                        <ArrowRight size={14} />
                      </a>
                    )}
                  </div>
                  {typeof input.body === "string" ? (
                    <Markdown text={input.body} />
                  ) : (
                    <ObservationBody body={input.body} />
                  )}
                  {input.source_id && (
                    <div className="document-footnote">
                      来源 <code>{input.source_id}</code>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
          {inputs.data && (
            <Pager
              offset={inputOffset}
              next={inputs.data.next_offset}
              onPage={setInputOffset}
            />
          )}
        </>
      )}
      {tab === "report" && (
        <div className="panel document-panel">
          {data.report ? (
            <>
              <div className="document-heading">
                <h3>处理结果</h3>
                <a
                  className="button"
                  href={`/api/jobs/${encodeURIComponent(id)}/report`}
                  download
                >
                  <ArrowDownToLine size={16} />
                  下载报告
                </a>
              </div>
              <p>{data.summary}</p>
              {!!data.report.pages?.length && (
                <div className="document-heading">
                  {data.report.pages.map((page) => (
                    <a
                      key={page.name}
                      className="button"
                      href={href("knowledge", {
                        project: data.project,
                        layer: "wiki",
                        wiki: page.name,
                      })}
                    >
                      {page.name}
                      <ArrowRight size={14} />
                    </a>
                  ))}
                </div>
              )}
              {data.content_status === "no_change" && (
                <div className="notice">
                  <Check size={18} />
                  <span>本批输入已消费，没有产生新的内容版本。</span>
                </div>
              )}
              {typeof data.report.conversation_report === "string" && (
                <Markdown text={data.report.conversation_report} />
              )}
              {data.skill && data.version_id && (
                <a
                  className="button"
                  href={href("skills", {
                    project: data.project,
                    skill: data.skill,
                    version: data.version_id,
                  })}
                >
                  查看版本差异
                  <ArrowRight size={16} />
                </a>
              )}
              <details className="disclosure">
                <summary>完整报告</summary>
                <Json value={data.report} />
              </details>
            </>
          ) : (
            <Empty title="报告尚未生成">内容处理后会在这里显示实际结果。</Empty>
          )}
          {data.previous_report && (
            <details className="disclosure">
              <summary>上次尝试的报告</summary>
              <Json value={data.previous_report} />
            </details>
          )}
          {data.result && (
            <details className="disclosure">
              <summary>已保存的模型输出</summary>
              <Json value={data.result} />
            </details>
          )}
        </div>
      )}
    </>
  );
}
