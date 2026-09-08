import { useState } from "react";
import { mutate, useResource } from "../api";
import { ErrorMessage, Markdown } from "../components";
import { href } from "../routing";
import type { Config, Page, Snapshot } from "../types";

type Turn = {
  id: number;
  session: string;
  turn_key: string;
  ended: number;
  records: number;
  pending: number;
};
type RecordItem = {
  id: number;
  text: string;
  bytes_base64: string;
  event_type: string;
  parse_error: string | null;
  consumed_by: string | null;
};
type Wiki = {
  name: string;
  body: string;
  digest: string;
  changes: Page<{ id: number; body: string; diff: string }>;
};
type Preview = {
  target: string;
  exists: boolean;
  diff: string;
  files: string[];
  source_digest: string;
  target_digest: string;
};

export function useAction(refresh: () => void = () => {}) {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(
    work: () => Promise<unknown>,
    success: string | ((value: unknown) => string) = "已完成",
  ) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      const value = await work();
      setMessage(typeof success === "function" ? success(value) : success);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return {
    run,
    busy,
    status: (
      <>
        <ErrorMessage error={error} />
        <p role="status">{message}</p>
      </>
    ),
  };
}

export function Settings() {
  const resource = useResource<Config>("/api/settings");
  return (
    <>
      {resource.error && <ErrorMessage error={resource.error} />}
      {resource.data && <SettingsForm initial={resource.data} />}
    </>
  );
}

function SettingsForm({ initial }: { initial: Config }) {
  const [values, setValues] = useState(initial);
  const [key, setKey] = useState("");
  const [clear, setClear] = useState(false);
  const [command, setCommand] = useState(JSON.stringify(initial.codex_command));
  const action = useAction();
  const text = (name: keyof Config, label: string, type = "text") => (
    <label>
      {label}
      <input
        type={type}
        value={String(values[name] ?? "")}
        onChange={(e) =>
          setValues({
            ...values,
            [name]: type === "number" ? Number(e.target.value) : e.target.value,
          })
        }
      />
    </label>
  );
  return (
    <form
      className="panel document-panel manage-form"
      onSubmit={(e) => {
        e.preventDefault();
        const { api_key_configured: _, ...settings } = values;
        void action.run(async () => {
          const saved = await mutate<Config>(
            "/api/settings",
            {
              ...settings,
              codex_command: JSON.parse(command),
              model: settings.model || null,
              api_key: key,
              clear_api_key: clear,
            },
            "PUT",
          );
          setValues(saved);
          setKey("");
          setClear(false);
        }, "设置已保存");
      }}
    >
      <h2>转换与采集设置</h2>
      <p>自动采集只保存原文。模型仅在手动转换或开启的阈值满足时运行。</p>
      <div className="manage-grid">
        <label>
          执行方式
          <select
            value={values.executor}
            onChange={(e) => setValues({ ...values, executor: e.target.value })}
          >
            <option value="codex">Codex 新建会话</option>
            <option value="api">API 模型</option>
          </select>
        </label>
        <label>
          API 协议
          <select
            value={values.api_provider}
            onChange={(e) =>
              setValues({ ...values, api_provider: e.target.value })
            }
          >
            <option value="chat_completions">Chat Completions</option>
            <option value="gemini">Gemini</option>
          </select>
        </label>
        {text("api_url", "API 地址")}
        {text("api_model", "API 模型")}
        <label>
          API key（{values.api_key_configured ? "已配置，留空保留" : "未配置"}）
          <input
            type="password"
            autoComplete="new-password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={clear}
            onChange={(e) => setClear(e.target.checked)}
          />
          清除已存密钥
        </label>
        {text("model", "Codex 模型（空值使用默认）")}
        {text("input_budget", "模型输入字符预算", "number")}
        {text("raw_threshold", "Raw 自动阈值（已结束轮次）", "number")}
        {text("wiki_threshold", "Wiki 自动阈值（正文版本）", "number")}
        {(
          [
            ["raw_auto", "自动 Raw → Wiki"],
            ["wiki_auto", "自动 Wiki → Skill"],
            ["auto_start", "连接 MCP 时自动启动后台"],
          ] as const
        ).map(([name, label]) => (
          <label key={name}>
            <input
              type="checkbox"
              checked={values[name]}
              onChange={(e) =>
                setValues({ ...values, [name]: e.target.checked })
              }
            />
            {label}
          </label>
        ))}
        {text("codex_home", "Codex 数据目录")}
        {text("install_directory", "Codex Skill 安装目录")}
        {text("poll_seconds", "后台扫描间隔（秒）", "number")}
        {text("timeout_seconds", "模型超时（秒）", "number")}
        <label>
          Codex 启动命令（JSON 数组）
          <input value={command} onChange={(e) => setCommand(e.target.value)} />
        </label>
      </div>
      <button disabled={action.busy} type="submit">
        保存设置
      </button>
      {action.status}
    </form>
  );
}

export function Manage({
  snapshot,
  revision,
  refresh,
  project,
}: {
  snapshot: Snapshot;
  revision: number;
  refresh: () => void;
  project?: string;
}) {
  const [tab, setTab] = useState("raw");
  const [path, setPath] = useState("");
  const selected =
    snapshot.projects.find((p) => p.id === project) || snapshot.projects[0];
  const action = useAction(refresh);
  return (
    <div className="manage-workspace">
      <section className="panel document-panel">
        <div className="eyebrow">KNOWLEDGE WORKSPACE</div>
        <h2>从轨迹到可复用的经验</h2>
        <p>
          正常使用 Codex，自动积累原始记录。在这里整理 Wiki、生成
          Skill，再决定何时安装。
        </p>
        <div className="manage-actions">
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(() => mutate("/api/start"), "后台启动已请求")
            }
          >
            启动后台
          </button>
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(
                () => mutate("/api/history-import"),
                "历史导入已请求，后台将读取已有轨迹",
              )
            }
          >
            导入已有历史
          </button>
          <a href={href("system")}>配置模型与自动阈值</a>
        </div>
        <form
          className="manage-actions"
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(
              () => mutate("/api/projects", { path }),
              "项目已添加",
            );
          }}
        >
          <input
            aria-label="项目目录"
            placeholder="项目绝对路径，可手动添加"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <button disabled={action.busy || !path}>添加项目</button>
        </form>
        {action.status}
      </section>
      {selected ? (
        <>
          <nav className="manage-tabs" aria-label="知识层">
            {[
              ["raw", "Raw 原始轨迹"],
              ["wiki", "Wiki 知识页"],
              ["skill", "Skill 生成与安装"],
            ].map(([id, label]) => (
              <button
                key={id}
                aria-pressed={tab === id}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <p className="muted">
            当前项目：{selected.name} · {selected.path}
          </p>
          {tab === "raw" ? (
            <RawPanel
              key={selected.id}
              project={selected.id}
              revision={revision}
              refresh={refresh}
            />
          ) : tab === "wiki" ? (
            <WikiPanel
              key={selected.id}
              project={selected.id}
              revision={revision}
              refresh={refresh}
            />
          ) : (
            <SkillPanel
              key={selected.id}
              project={selected}
              revision={revision}
              refresh={refresh}
            />
          )}
        </>
      ) : (
        <section className="panel document-panel">
          <h3>等待新的轨迹</h3>
          <p>
            首次启用从当前位置开始。可添加项目直接编写
            Wiki，或点击“导入已有历史”。
          </p>
        </section>
      )}
    </div>
  );
}

function RawPanel({
  project,
  revision,
  refresh,
}: {
  project: string;
  revision: number;
  refresh: () => void;
}) {
  const [offset, setOffset] = useState(0),
    [recordOffset, setRecordOffset] = useState(0);
  const [turn, setTurn] = useState<number | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const turns = useResource<Page<Turn>>(
    `/api/projects/${project}/turns?offset=${offset}`,
    revision,
  );
  const records = useResource<Page<RecordItem>>(
    turn === null
      ? null
      : `/api/projects/${project}/traces?turn_id=${turn}&offset=${recordOffset}`,
    revision,
  );
  const action = useAction(refresh);
  return (
    <section className="panel document-panel">
      <h2>Raw 原始轨迹</h2>
      <p>
        原文来自 Codex
        实际写出的文件，未知事件和失败记录也会保留；加密或上游未写出的内容无法还原。
      </p>
      <div className="manage-actions">
        <button
          disabled={action.busy}
          onClick={() =>
            void action.run(
              () =>
                mutate(`/api/projects/${project}/convert`, { stage: "raw" }),
              "整理批次已创建，可在执行记录查看进度",
            )
          }
        >
          整理待处理 Raw → Wiki
        </button>
        <button
          disabled={action.busy || !chosen.length}
          onClick={() =>
            void action.run(async () => {
              await mutate(`/api/projects/${project}/convert`, {
                stage: "raw",
                inputs: chosen,
              });
              setChosen([]);
            }, "选定记录已入队")
          }
        >
          整理选定记录（{chosen.length}）
        </button>
        <a href={href("knowledge", { project, layer: "raw" })}>
          查看旧经验摘要
        </a>
      </div>
      {action.status}
      <ErrorMessage error={turns.error} />
      <div className="manage-split">
        <div>
          {turns.data?.items.map((t) => (
            <button
              className="manage-row"
              key={t.id}
              onClick={() => {
                setTurn(t.id);
                setRecordOffset(0);
              }}
            >
              <strong>
                {t.turn_key === "unassigned" ? "未归属轮次的记录" : t.turn_key}
              </strong>
              <span>
                {t.session} · {t.ended ? "已结束" : "未结束"} · {t.records} 条 /{" "}
                {t.pending} 待处理
              </span>
            </button>
          ))}
          {!turns.data?.items.length && (
            <p>暂无轨迹。新内容将在后台下一次扫描后显示。</p>
          )}
          <Pagination
            offset={offset}
            next={turns.data?.next_offset}
            size={20}
            set={setOffset}
          />
        </div>
        <div>
          <ErrorMessage error={records.error} />
          {records.data?.items.map((r) => (
            <article key={r.id} className="trace-record">
              <label>
                <input
                  type="checkbox"
                  disabled={!!r.consumed_by}
                  checked={chosen.includes(r.id)}
                  onChange={(e) =>
                    setChosen(
                      e.target.checked
                        ? [...chosen, r.id]
                        : chosen.filter((x) => x !== r.id),
                    )
                  }
                />
                #{r.id} · {r.event_type} · {r.consumed_by ? "已处理" : "待处理"}
              </label>
              {r.parse_error && <p>解析失败，原文已保存：{r.parse_error}</p>}
              <pre className="code-block">{r.text}</pre>
              <a
                download={`raw-${r.id}.jsonl`}
                href={`data:application/octet-stream;base64,${r.bytes_base64}`}
              >
                下载原始字节
              </a>
            </article>
          ))}
          {turn !== null && (
            <Pagination
              offset={recordOffset}
              next={records.data?.next_offset}
              size={50}
              set={setRecordOffset}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function Pagination({
  offset,
  next,
  size,
  set,
}: {
  offset: number;
  next?: number | null;
  size: number;
  set: (n: number) => void;
}) {
  return (
    <div className="manage-actions">
      <button
        disabled={!offset}
        onClick={() => set(Math.max(0, offset - size))}
      >
        上一页
      </button>
      <span>第 {Math.floor(offset / size) + 1} 页</span>
      <button disabled={next == null} onClick={() => set(next!)}>
        下一页
      </button>
    </div>
  );
}

function WikiPanel({
  project,
  revision,
  refresh,
}: {
  project: string;
  revision: number;
  refresh: () => void;
}) {
  const [offset, setOffset] = useState(0),
    [name, setName] = useState<string | null>(null);
  const list = useResource<Page<{ name: string; excerpt: string }>>(
    `/api/projects/${project}/wiki?offset=${offset}`,
    revision,
  );
  const detail = useResource<Wiki>(
    name ? `/api/projects/${project}/wiki/${encodeURIComponent(name)}` : null,
    revision,
  );
  return (
    <section className="panel document-panel">
      <h2>Wiki 知识页</h2>
      <button onClick={() => setName("")}>新建知识页</button>
      <ErrorMessage error={list.error} />
      <div className="manage-split">
        <div>
          {list.data?.items.map((p) => (
            <button
              className="manage-row"
              key={p.name}
              onClick={() => setName(p.name)}
            >
              <strong>{p.name}</strong>
              <span>{p.excerpt}</span>
            </button>
          ))}
          <Pagination
            offset={offset}
            next={list.data?.next_offset}
            size={20}
            set={setOffset}
          />
        </div>
        <div>
          <ErrorMessage error={detail.error} />
          {name === "" ? (
            <WikiEditor key="new" project={project} refresh={refresh} />
          ) : (
            detail.data && (
              <WikiEditor
                key={detail.data.name}
                project={project}
                initial={detail.data}
                refresh={refresh}
              />
            )
          )}
        </div>
      </div>
    </section>
  );
}

function WikiEditor({
  project,
  initial,
  refresh,
}: {
  project: string;
  initial?: Wiki;
  refresh: () => void;
}) {
  const [name, setName] = useState(initial?.name || ""),
    [body, setBody] = useState(initial?.body || "");
  const [expected, setExpected] = useState(initial?.digest || null);
  const action = useAction(refresh);
  return (
    <>
      <form
        className="manage-form"
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            await mutate(
              `/api/projects/${project}/wiki`,
              { pages: [{ name, body }], expected: { [name]: expected } },
              "PUT",
            );
            const response = await fetch(
              `/api/projects/${project}/wiki/${encodeURIComponent(name)}`,
            );
            const saved: Wiki = await response.json();
            setExpected(saved.digest);
          }, "Wiki 已保存");
        }}
      >
        <label>
          页面名称
          <input
            required
            pattern="[a-z0-9][a-z0-9-]{0,100}"
            disabled={!!initial}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          正文
          <textarea
            required
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <button disabled={action.busy}>保存 Wiki</button>
      </form>
      {action.status}
      <details>
        <summary>正文预览</summary>
        <Markdown text={body} />
      </details>
      {initial && (
        <>
          <h3>历史版本与回退</h3>
          <a
            href={href("knowledge", {
              project,
              wiki: initial.name,
              layer: "wiki",
            })}
          >
            查看全部历史与版本差异
          </a>
          {initial.changes.items.map((c) => (
            <details key={c.id}>
              <summary>正文版本 #{c.id}</summary>
              <pre className="code-block">{c.diff}</pre>
              <Markdown text={c.body} />
              <button
                disabled={action.busy}
                onClick={() =>
                  void action.run(
                    () =>
                      mutate(`/api/projects/${project}/wiki/rollback`, {
                        change_id: c.id,
                        expected: initial.digest,
                      }),
                    "已回退，保存为新正文版本",
                  )
                }
              >
                回退到此正文
              </button>
            </details>
          ))}
        </>
      )}
    </>
  );
}

function SkillPanel({
  project,
  revision,
  refresh,
}: {
  project: Snapshot["projects"][number];
  revision: number;
  refresh: () => void;
}) {
  return (
    <>
      {project.skills
        .filter((s) => s.owned)
        .map((s) => (
          <SkillActions
            key={s.id}
            skill={s.id}
            project={project.id}
            name={s.name}
            revision={revision}
            refresh={refresh}
          />
        ))}
    </>
  );
}

function WikiInputs({
  project,
  skill,
  revision,
  refresh,
}: {
  project: string;
  skill: string;
  revision: number;
  refresh: () => void;
}) {
  const [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<number[]>([]);
  const list = useResource<Page<{ id: number; name: string; excerpt: string }>>(
    `/api/projects/${project}/wiki-inputs?skill=${skill}&offset=${offset}`,
    revision,
  );
  const action = useAction(refresh);
  return (
    <details>
      <summary>选择待处理 Wiki 版本</summary>
      <ErrorMessage error={list.error} />
      {list.data?.items.map((item) => (
        <label className="manage-row" key={item.id}>
          <span>
            <input
              type="checkbox"
              checked={selected.includes(item.id)}
              onChange={(e) =>
                setSelected(
                  e.target.checked
                    ? [...selected, item.id]
                    : selected.filter((id) => id !== item.id),
                )
              }
            />
            {item.name} · #{item.id}
          </span>
          <span>{item.excerpt}</span>
        </label>
      ))}
      <Pagination
        offset={offset}
        next={list.data?.next_offset}
        size={20}
        set={setOffset}
      />
      <button
        disabled={action.busy || !selected.length}
        onClick={() =>
          void action.run(async () => {
            await mutate(`/api/projects/${project}/convert`, {
              stage: "skill",
              skill,
              inputs: selected,
            });
            setSelected([]);
          }, "选定 Wiki 版本已入队")
        }
      >
        从选定版本生成 Skill
      </button>
      {action.status}
    </details>
  );
}

function SkillActions({
  skill,
  project,
  name,
  revision,
  refresh,
}: {
  skill: string;
  project: string;
  name: string;
  revision: number;
  refresh: () => void;
}) {
  const detail = useResource<{
    disk_text: string | null;
    versions: Page<{ id: string; created: number }>;
  }>(`/api/skills/${skill}`, revision);
  const [preview, setPreview] = useState<Preview | null>(null);
  const action = useAction(refresh);
  return (
    <section className="panel document-panel">
      <h2>{name}</h2>
      <p>生成结果保存在数据目录；点击安装后才复制到 Codex。</p>
      <div className="manage-actions">
        <button
          disabled={action.busy}
          onClick={() =>
            void action.run(
              () =>
                mutate(`/api/projects/${project}/convert`, {
                  stage: "skill",
                  skill,
                }),
              "Skill 生成批次已创建",
            )
          }
        >
          Wiki → Skill
        </button>
        <button
          disabled={action.busy || !detail.data?.disk_text}
          onClick={() =>
            void action.run(
              async () => {
                const response = await fetch(`/api/skills/${skill}/install`);
                const value = await response.json();
                if (!response.ok) throw new Error(value.detail);
                if (value.exists) setPreview(value);
                else
                  await mutate(`/api/skills/${skill}/install`, {
                    source_digest: value.source_digest,
                    target_digest: value.target_digest,
                    overwrite: false,
                  });
                return value.exists;
              },
              (exists) =>
                exists
                  ? "请查看同名目标差异，再确认覆盖"
                  : "已复制到 Codex Skill 目录",
            )
          }
        >
          安装到 Codex
        </button>
        <a href={href("skills", { skill, project })}>完整历史与资源差异</a>
      </div>
      {action.status}
      <ErrorMessage error={detail.error} />
      <details>
        <summary>查看完整 SKILL.md 原文</summary>
        <pre className="code-block">
          {detail.data?.disk_text || "尚未生成 Skill。"}
        </pre>
      </details>
      <Markdown
        text={
          detail.data?.disk_text?.replace(/^---\n[\s\S]*?\n---\n/, "") ||
          "尚未生成 Skill。"
        }
      />
      {preview && (
        <div className="install-preview">
          <h3>
            {preview.exists ? "同名 Skill 已存在，请确认覆盖" : "安装预览"}
          </h3>
          <p>{preview.target}</p>
          <p>
            将复制 {preview.files.length} 个文件和目录条目。安装后由 Codex
            在后续会话发现。
          </p>
          <pre className="code-block">{preview.diff || "内容相同"}</pre>
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(async () => {
                await mutate(`/api/skills/${skill}/install`, {
                  source_digest: preview.source_digest,
                  target_digest: preview.target_digest,
                  overwrite: preview.exists,
                });
                setPreview(null);
              }, "已安装到 Codex Skill 目录")
            }
          >
            {preview.exists ? "确认覆盖并安装" : "确认安装"}
          </button>
          <button onClick={() => setPreview(null)}>取消</button>
        </div>
      )}
      <WikiInputs
        project={project}
        skill={skill}
        revision={revision}
        refresh={refresh}
      />
      <h3>版本回退</h3>
      {detail.data?.versions.items.map((v) => (
        <div className="manage-actions" key={v.id}>
          <a href={href("skills", { skill, version: v.id, project })}>
            {new Date(v.created * 1000).toLocaleString()} · 查看差异
          </a>
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(
                () =>
                  mutate(`/api/skills/${skill}/rollback`, {
                    version_id: v.id,
                    side: "after",
                  }),
                "数据目录已回退；需要同步到 Codex 时请重新安装",
              )
            }
          >
            恢复此版本
          </button>
        </div>
      ))}
    </section>
  );
}
