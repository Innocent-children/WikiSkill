import { useAction } from "../actions";
import { WikiSearch } from "../WikiSearch";
import { useState } from "react";
import { ArrowRight, BookOpen, FileText } from "lucide-react";
import { mutate, useResource } from "../api";
import { href, navigate, type Route } from "../routing";
import { query, short, time } from "../presentation";
import {
  Back,
  Badge,
  DocumentTabs,
  Empty,
  ErrorMessage,
  Json,
  Loading,
  Markdown,
  ObservationBody,
  Pager,
  ProjectChooser,
} from "../components";
import type {
  Page,
  Raw,
  RawDetail,
  Snapshot,
  Wiki,
  WikiDetail,
} from "../types";

export function Knowledge({
  route,
  snapshot,
  revision,
}: {
  route: Route;
  snapshot: Snapshot;
  revision: number;
}) {
  if (!route.project)
    return (
      <>
        <p className="muted section-intro">
          选择项目，查看其原始经验与知识页面。
        </p>
        <ProjectChooser projects={snapshot.projects} view="knowledge" />
      </>
    );
  return route.raw ? (
    <RawPage project={route.project} id={route.raw} revision={revision} />
  ) : route.wiki ? (
    <WikiPage project={route.project} name={route.wiki} revision={revision} />
  ) : (
    <KnowledgeList route={route} revision={revision} />
  );
}

export function KnowledgeList({
  route,
  revision,
}: {
  route: Route;
  revision: number;
}) {
  const layer = route.layer === "wiki" ? "wiki" : "raw";
  const offset = Math.max(0, Number(route.offset) || 0);
  const search = layer === "wiki" ? route.q || "" : "";
  const result = useResource<Page<Raw | Wiki>>(
    `/api/projects/${encodeURIComponent(route.project!)}/${layer}${query({ offset, q: search })}`,
    revision,
  );
  return (
    <>
      <DocumentTabs
        values={[
          { id: "raw", label: "旧经验摘要" },
          { id: "wiki", label: "知识页面 Wiki" },
        ]}
        active={layer}
        onChange={(layer) =>
          navigate("knowledge", { project: route.project, layer })
        }
      />
      {layer === "wiki" && (
        <WikiSearch
          key={route.project}
          value={search}
          onSearch={(q) =>
            navigate("knowledge", { project: route.project, layer, q })
          }
        />
      )}
      <ErrorMessage error={result.error} />
      {result.data?.items.length ? (
        <div className="document-list">
          {result.data.items.map((item) =>
            layer === "raw" ? (
              <a
                className="panel knowledge-card"
                key={(item as Raw).id}
                href={href("knowledge", {
                  project: route.project,
                  layer,
                  raw: (item as Raw).id,
                })}
              >
                <FileText size={21} />
                <div>
                  <h3>{(item as Raw).title || "旧经验摘要"}</h3>
                  <p className="muted">
                    {(item as Raw).submitted} 条提交 · 新增{" "}
                    {(item as Raw).added} 条旧摘要条目
                  </p>
                  <code>{(item as Raw).source_id}</code>
                </div>
                <time>{time((item as Raw).created)}</time>
                <ArrowRight size={17} />
              </a>
            ) : (
              <a
                className="panel knowledge-card"
                key={(item as Wiki).name}
                href={href("knowledge", {
                  project: route.project,
                  layer,
                  wiki: (item as Wiki).name,
                })}
              >
                <BookOpen size={21} />
                <div>
                  <h3>{(item as Wiki).name}</h3>
                  <p className="excerpt">{(item as Wiki).excerpt}</p>
                  <span className="muted small">
                    {(item as Wiki).revisions} 个不同正文版本 ·{" "}
                    {(item as Wiki).characters} 字符
                  </span>
                </div>
                <ArrowRight size={17} />
              </a>
            ),
          )}
        </div>
      ) : result.loading ? (
        <Loading />
      ) : search && result.error ? null : (
        <div className="panel">
          <Empty
            title={
              search
                ? "没有匹配的 Wiki"
                : layer === "raw"
                  ? "还没有旧经验摘要"
                  : "还没有知识页面"
            }
          >
            {search
              ? "试试其他名称或正文内容，或清空搜索查看全部文档。"
              : layer === "raw"
                ? "在 Codex 任务中提交可复用经验后，这里会保留原始内容。"
                : "原始经验达到阈值并处理完成后，可以在这里查看整理结果。"}
          </Empty>
        </div>
      )}
      {result.data && (
        <Pager
          offset={offset}
          next={result.data.next_offset}
          onPage={(offset) =>
            navigate("knowledge", {
              project: route.project,
              layer,
              offset: String(offset),
              q: search,
            })
          }
        />
      )}
    </>
  );
}

export function RawPage({
  project,
  id,
  revision,
}: {
  project: string;
  id: string;
  revision: number;
}) {
  const raw = useResource<RawDetail>(
    `/api/projects/${encodeURIComponent(project)}/raw/${encodeURIComponent(id)}`,
    revision,
  );
  return (
    <>
      <Back to={href("knowledge", { project, layer: "raw" })}>旧经验摘要</Back>
      <ErrorMessage error={raw.error} />
      {raw.data ? (
        <>
          <div className="detail-heading">
            <div>
              <div className="eyebrow">RAW</div>
              <h2>原始经验</h2>
              <code>{raw.data.source_id}</code>
            </div>
            <span className="muted">{time(raw.data.created, true)}</span>
          </div>
          <div className="document-list">
            {raw.data.observations.map((observation, index) => (
              <article className="panel document-panel" key={index}>
                <div className="document-heading">
                  <h3>观察 {index + 1}</h3>
                  <Badge
                    tone={
                      observation.consumed_by
                        ? "green"
                        : observation.batch?.state === "failed"
                          ? "danger"
                          : observation.batch
                            ? "blue"
                            : "neutral"
                    }
                  >
                    {observation.consumed_by
                      ? "已消费"
                      : observation.batch?.state === "failed"
                        ? "批次失败，输入保留"
                        : observation.batch
                          ? "已纳入批次"
                          : "等待积累"}
                  </Badge>
                </div>
                <ObservationBody body={observation.body} />
                {observation.duplicate && (
                  <p className="muted">
                    与已有观察相同，此次提交不重复累计。
                    {observation.canonical_raw_id &&
                      observation.canonical_raw_id !== id && (
                        <>
                          {" "}
                          <a
                            href={href("knowledge", {
                              project,
                              layer: "raw",
                              raw: observation.canonical_raw_id,
                            })}
                          >
                            查看首次提交
                          </a>
                        </>
                      )}
                  </p>
                )}
                {(observation.consumed_by || observation.batch) && (
                  <a
                    className="subdued-link"
                    href={href("jobs", {
                      project,
                      job: observation.consumed_by || observation.batch!.id,
                    })}
                  >
                    查看关联批次
                    <ArrowRight size={15} />
                  </a>
                )}
              </article>
            ))}
          </div>
          <details className="disclosure panel">
            <summary>旧经验摘要与元数据</summary>
            <Json value={raw.data.payload} />
          </details>
        </>
      ) : (
        !raw.error && <Loading />
      )}
    </>
  );
}

export function WikiPage({
  project,
  name,
  revision,
}: {
  project: string;
  name: string;
  revision: number;
}) {
  const [offset, setOffset] = useState(0);
  const [localRevision, setLocalRevision] = useState(0);
  const action = useAction(() => setLocalRevision((n) => n + 1));
  const wiki = useResource<WikiDetail>(
    `/api/projects/${encodeURIComponent(project)}/wiki/${encodeURIComponent(name)}${query({ offset })}`,
    revision + localRevision,
  );
  return (
    <>
      <Back to={href("knowledge", { project, layer: "wiki" })}>知识页面</Back>
      <ErrorMessage error={wiki.error} />
      {action.status}
      {wiki.data ? (
        <>
          <div className="detail-heading">
            <div>
              <div className="eyebrow">WIKI</div>
              <h2>{wiki.data.name}</h2>
              <a href={href("manage", { project, layer: "wiki", wiki: name })}>
                打开 Wiki：编辑或生成 Skill →
              </a>
              <code>{short(wiki.data.digest)}</code>
            </div>
          </div>
          <article className="panel document-panel">
            <Markdown text={wiki.data.body} />
          </article>
          <section className="section">
            <div className="section-heading">
              <h2>正文版本与消费情况</h2>
            </div>
            <div className="document-list">
              {wiki.data.changes.items.map((change) => (
                <article className="panel document-panel" key={change.id}>
                  <div className="document-heading">
                    <h3>正文版本 #{change.id}</h3>
                    {change.source_job ? (
                      <a
                        href={href("jobs", { project, job: change.source_job })}
                      >
                        来源批次
                        <ArrowRight size={14} />
                      </a>
                    ) : (
                      <Badge>手动提交</Badge>
                    )}
                  </div>
                  <div className="consumer-list">
                    {change.consumers.map((skill) => (
                      <div key={skill.id}>
                        <a href={href("skills", { project, skill: skill.id })}>
                          {skill.path.split("/").pop()}
                        </a>
                        {skill.consumed_by ? (
                          <a
                            href={href("jobs", {
                              project,
                              job: skill.consumed_by,
                            })}
                          >
                            <Badge tone="green">已消费</Badge>
                          </a>
                        ) : (
                          <Badge>待消费</Badge>
                        )}
                      </div>
                    ))}
                  </div>
                  <details className="disclosure">
                    <summary>查看该版本正文与差异</summary>
                    <pre className="code-block">{change.diff}</pre>
                    <button
                      disabled={action.busy}
                      onClick={() =>
                        void action.run(
                          () =>
                            mutate(`/api/projects/${project}/wiki/rollback`, {
                              change_id: change.id,
                              expected: wiki.data!.digest,
                            }),
                          "已回退为新正文版本",
                        )
                      }
                    >
                      回退到此正文
                    </button>
                    <Markdown text={change.body} />
                  </details>
                </article>
              ))}
            </div>
            <Pager
              offset={offset}
              next={wiki.data.changes.next_offset}
              onPage={setOffset}
            />
          </section>
          <details className="disclosure panel">
            <summary>页面元数据</summary>
            <Json value={wiki.data.metadata} />
          </details>
        </>
      ) : (
        !wiki.error && <Loading />
      )}
    </>
  );
}
