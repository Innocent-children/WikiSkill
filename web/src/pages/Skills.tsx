import { useAction } from "./Manage";
import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  CircleAlert,
  FileDiff,
  FileText,
  Folder,
} from "lucide-react";
import { mutate, useResource } from "../api";
import { href, navigate, type Route } from "../routing";
import { query, short, time } from "../presentation";
import {
  Back,
  Badge,
  DocumentTabs,
  Empty,
  ErrorMessage,
  Loading,
  Markdown,
  Pager,
} from "../components";
import type { Skill, SkillDetail, Snapshot, Version } from "../types";

export function Skills({
  route,
  snapshot,
  revision,
}: {
  route: Route;
  snapshot: Snapshot;
  revision: number;
}) {
  const projects = route.project
    ? snapshot.projects.filter((p) => p.id === route.project)
    : snapshot.projects;
  const skills = new Map<string, Skill>();
  projects.forEach((project) =>
    project.skills.forEach((skill) => skills.set(skill.id, skill)),
  );
  if (route.skill)
    return <SkillPage key={route.skill} route={route} revision={revision} />;
  return (
    <div className="project-grid">
      {[...skills.values()].map((skill) => (
        <a
          className="panel skill-card"
          key={skill.id}
          href={href("skills", { project: route.project, skill: skill.id })}
        >
          <div className="skill-card-top">
            <span className="folder-mark">
              <FileDiff size={23} />
            </span>
            <Badge
              tone={
                skill.enabled
                  ? "green"
                  : skill.enabled === false
                    ? "neutral"
                    : "amber"
              }
            >
              {skill.enabled
                ? "管理已开启"
                : skill.enabled === false
                  ? "管理已关闭"
                  : "配置待确认"}
            </Badge>
          </div>
          <h3>{skill.name}</h3>
          <p className="muted path">{skill.path}</p>
          <div className="skill-card-footer">
            <span>
              {skill.owned ? "WikiSkill 生成" : "外部 Skill"} ·{" "}
              {skill.project_count} 个关联项目
            </span>
            <span>
              {skill.version_count} 个版本
              <ArrowRight size={14} />
            </span>
          </div>
        </a>
      ))}
    </div>
  );
}

export function SkillPage({
  route,
  revision,
}: {
  route: Route;
  revision: number;
}) {
  const [offset, setOffset] = useState(0);
  const [localRevision, setLocalRevision] = useState(0);
  const action = useAction(() => setLocalRevision((n) => n + 1));
  const [tab, setTab] = useState("current");
  const skill = useResource<SkillDetail>(
    `/api/skills/${encodeURIComponent(route.skill!)}${query({ offset })}`,
    revision + localRevision,
  );
  const selected = route.version || skill.data?.versions.items[0]?.id;
  const version = useResource<Version>(
    selected
      ? `/api/skills/${encodeURIComponent(route.skill!)}/versions/${encodeURIComponent(selected)}`
      : null,
    revision + localRevision,
  );
  useEffect(() => {
    if (route.version) setTab("diff");
  }, [route.version]);
  const data = skill.data;
  return (
    <>
      <Back to={href("skills", { project: route.project })}>全部 Skill</Back>
      <ErrorMessage error={skill.error} />
      {action.status}
      {selected && skill.data?.enabled && (
        <div className="manage-actions">
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(
                () =>
                  mutate(`/api/skills/${route.skill}/rollback`, {
                    version_id: selected,
                    side: "after",
                  }),
                "已恢复选定版本的完整目录",
              )
            }
          >
            恢复选定历史版本
          </button>
        </div>
      )}
      {data ? (
        <>
          <div className="detail-heading">
            <div>
              <div className="eyebrow">
                {data.owned ? "WIKISKILL 生成" : "外部 SKILL"}
              </div>
              <h2>{data.name}</h2>
              <span className="muted path">{data.path}</span>
            </div>
            <Badge tone={data.enabled ? "green" : "neutral"}>
              {data.enabled
                ? "管理已开启"
                : data.enabled === null
                  ? "配置待确认"
                  : "管理已关闭"}
            </Badge>
          </div>
          <div className="linked-projects">
            {data.projects.map((project) => (
              <a
                key={project.id}
                href={href("overview", { project: project.id })}
              >
                <Folder size={14} />
                {project.name}
              </a>
            ))}
          </div>
          <DocumentTabs
            values={[
              { id: "current", label: "当前内容" },
              { id: "diff", label: "版本差异" },
              { id: "before", label: "修改前" },
              { id: "after", label: "修改后" },
              { id: "files", label: "快照文件" },
            ]}
            active={tab}
            onChange={setTab}
          />
          <div className="skill-detail-grid">
            <div className="panel document-panel">
              <ErrorMessage error={version.error} />
              {tab === "current" ? (
                <>
                  <ErrorMessage error={data.disk_error} />
                  {data.disk_text !== null &&
                    data.published_text !== null &&
                    data.disk_text !== data.published_text && (
                      <div className="notice warning">
                        <CircleAlert size={18} />
                        <span>磁盘内容与最近已发布版本不同。</span>
                      </div>
                    )}
                  {data.disk_text === null && data.published_text !== null && (
                    <p className="muted">
                      磁盘文件暂不可读，以下为最近已发布内容。
                    </p>
                  )}
                  {data.disk_text || data.published_text ? (
                    <Markdown text={data.disk_text ?? data.published_text!} />
                  ) : (
                    <Empty title="Skill 正文尚未生成">
                      Wiki 内容达到阈值后会生成项目 Skill。
                    </Empty>
                  )}
                </>
              ) : version.data ? (
                <>
                  <div className="document-heading">
                    <span className="muted">
                      版本 {short(version.data.id)} ·{" "}
                      {time(version.data.created)}
                    </span>
                    {version.data.source_job && (
                      <a
                        href={href("jobs", {
                          job: version.data.source_job,
                        })}
                      >
                        关联批次
                        <ArrowRight size={14} />
                      </a>
                    )}
                  </div>
                  {version.data.state === "prepared" && (
                    <div className="notice warning">
                      <CircleAlert size={18} />
                      <span>发布尚未确认完成，以下是保留的发布快照。</span>
                    </div>
                  )}
                  {tab === "diff" ? (
                    <pre className="diff-block">
                      {version.data.diff
                        ? version.data.diff.split("\n").map((line, i) => (
                            <span
                              className={
                                line.startsWith("+") && !line.startsWith("+++")
                                  ? "addition"
                                  : line.startsWith("-") &&
                                      !line.startsWith("---")
                                    ? "deletion"
                                    : ""
                              }
                              key={i}
                            >
                              {line || " "}
                              <br />
                            </span>
                          ))
                        : "没有文本差异"}
                    </pre>
                  ) : tab === "files" ? (
                    <div>
                      {(["before", "after"] as const).map((side) => (
                        <div key={side} className="file-section">
                          <h3>{side === "before" ? "修改前" : "修改后"}目录</h3>
                          {version.data![side].files.length ? (
                            <ul className="file-list">
                              {version.data![side].files.map((file) => (
                                <li key={file.path}>
                                  {file.type === "directory" ? (
                                    <Folder size={16} />
                                  ) : (
                                    <FileText size={16} />
                                  )}
                                  <span>
                                    <code>{file.path}</code>
                                    <small>
                                      {file.mode} ·{" "}
                                      {file.bytes.toLocaleString()} bytes
                                    </small>
                                  </span>
                                  {file.type === "file" && (
                                    <a
                                      className="icon-button"
                                      aria-label={`下载${side === "before" ? "修改前" : "修改后"} ${file.path}`}
                                      href={`/api/skills/${encodeURIComponent(data.id)}/versions/${encodeURIComponent(version.data!.id)}/file${query({ side, name: file.path })}`}
                                      download
                                    >
                                      <ArrowDownToLine size={17} />
                                    </a>
                                  )}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <p className="muted">目录为空</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Markdown
                      text={
                        version.data[tab === "before" ? "before" : "after"]
                          .skill_md || "此快照没有 SKILL.md。"
                      }
                    />
                  )}
                </>
              ) : version.loading ? (
                <Loading />
              ) : (
                <Empty title="还没有版本记录">
                  首次发布后会保存完整目录快照和差异。
                </Empty>
              )}
            </div>
            <aside className="panel version-panel">
              <div className="eyebrow">VERSIONS</div>
              <h3>版本历史</h3>
              {data.versions.items.length ? (
                <div className="version-list">
                  {data.versions.items.map((item) => (
                    <button
                      key={item.id}
                      aria-pressed={selected === item.id}
                      onClick={() => {
                        setTab("diff");
                        navigate("skills", {
                          project: route.project,
                          skill: data.id,
                          version: item.id,
                        });
                      }}
                    >
                      <span>
                        {item.job_id.startsWith("rollback-")
                          ? "目录回退"
                          : "Skill 更新"}
                        <span
                          className={`version-state ${item.state === "prepared" ? "text-warning" : ""}`}
                        >
                          {item.state === "applied" ? "已发布" : "待确认"}
                        </span>
                      </span>
                      <time>{time(item.created)}</time>
                      <code>{short(item.id)}</code>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="muted">尚未发布</p>
              )}
              {(data.versions.next_offset !== null || offset > 0) && (
                <Pager
                  offset={offset}
                  next={data.versions.next_offset}
                  onPage={setOffset}
                />
              )}
            </aside>
          </div>
        </>
      ) : (
        !skill.error && <Loading />
      )}
    </>
  );
}
