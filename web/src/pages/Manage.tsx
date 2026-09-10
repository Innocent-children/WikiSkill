import { useAction } from "../actions";
import { WikiSources } from "../WikiSources";
import { SessionPicker } from "../SessionPicker";
import { SkillGeneration } from "../SkillGeneration";
import { WikiTransfer } from "../WikiTransfer";
import { WikiSearch } from "../WikiSearch";
import { useEffect, useRef, useState } from "react";
import { mutate, request, useResource } from "../api";
import { Empty, ErrorMessage, Loading, Markdown } from "../components";
import { href, navigate } from "../routing";
import { query } from "../presentation";
import { confirmDiscardChanges, useUnsavedChanges } from "../unsaved";
import { ArrowRight, Check, Plus } from "lucide-react";
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

export function Settings() {
  const resource = useResource<Config>("/api/settings");
  return (
    <>
      {resource.error && <ErrorMessage error={resource.error} />}
      {resource.loading && !resource.data && <Loading />}
      {resource.data && <SettingsForm initial={resource.data} />}
    </>
  );
}

function SettingsForm({ initial }: { initial: Config }) {
  const [values, setValues] = useState(initial);
  const localDateTime = (value: string) => {
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      .toISOString().slice(0, 19);
  };
  const [scanSince, setScanSince] = useState(() => localDateTime(initial.automatic_scan_since));
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
              automatic_scan_since: new Date(scanSince).toISOString(),
              codex_command: JSON.parse(command),
              model: settings.model || null,
              api_key: key,
              clear_api_key: clear,
            },
            "PUT",
          );
          setValues(saved);
          setScanSince(localDateTime(saved.automatic_scan_since));
          setKey("");
          setClear(false);
        }, "设置已保存");
      }}
    >
      <h2>整理偏好</h2>
      <p>手动模式只分析所选会话；自动模式定时整理新增轨迹并更新相关 Skill。</p>
      <fieldset>
        <legend>01 · 选择整理经验的模型</legend>
        <p className="muted small">使用本机 Codex，或连接自己的模型 API。</p>
        <div className="manage-grid">
          <label>
            执行方式
            <select
              value={values.executor}
              onChange={(e) =>
                setValues({ ...values, executor: e.target.value })
              }
            >
              <option value="codex">Codex 新建会话</option>
              <option value="api">API 模型</option>
              <option value="ollama">本机 Ollama</option>
            </select>
          </label>
          {values.executor === "api" && (
            <>
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
                API key（
                {values.api_key_configured ? "已配置，留空保留" : "未配置"}）
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
            </>
          )}
          {values.executor === "ollama" && (
            <>
              {text("ollama_model", "Ollama 模型名称")}
              <p className="muted small">
                连接本机 127.0.0.1:11434，先运行 Ollama
                并下载所填模型。不会自动改用云端模型。
              </p>
            </>
          )}
          {values.executor === "codex" &&
            text("model", "Codex 模型（空值使用默认）")}
        </div>
      </fieldset>
      <fieldset>
        <legend>02 · 工作模式</legend>
        <label>
          分析模式
          <select
            value={values.capture_mode}
            onChange={(e) =>
              setValues({ ...values, capture_mode: e.target.value })
            }
          >
            <option value="manual">手动选择会话</option>
            <option value="automatic">自动监听并分析</option>
          </select>
        </label>
        <p className="muted small">
          手动：选择会话 → 提取 Wiki → 生成 Skill。自动：持续采集 → 定时分析 →
          自动更新相关 Skill。安装到 Codex 保持独立操作。
        </p>
        {values.capture_mode === "automatic" && (
          <>
            <label>
              自动扫描起始时间
              <input
                type="datetime-local"
                step="1"
                required
                value={scanSince}
                onChange={(e) => setScanSince(e.target.value)}
              />
            </label>
            <button type="button" onClick={() => setScanSince(localDateTime(new Date().toISOString()))}>
              使用当前时间
            </button>
            <p className="muted small">
              按本地时区填写，保存后固定。仅采集最后修改时间不早于此时间的会话文件，
              符合条件的会话保留完整原文；已采集内容按原进度继续。手动导入历史不受限制。
            </p>
            <p className="muted small">
              自动分析需等待同一会话连续 1 小时没有新增记录，且所有可识别轮次均已结束。
              等待执行期间出现新记录会重新计时；手动执行可立即开始。
            </p>
            {text("analysis_interval_minutes", "自动分析间隔（分钟）", "number")}
          </>
        )}
        <p className="muted small">
          模型按需继续读取材料，完成分析后结束本批；失败批次保留输入，不自动反复重试。
        </p>
        <label>
          <input
            type="checkbox"
            checked={values.auto_start}
            onChange={(e) =>
              setValues({ ...values, auto_start: e.target.checked })
            }
          />
          连接 MCP 时启动后台
        </label>
      </fieldset>
      <details className="settings-advanced">
        <summary>高级设置 · 目录、超时与启动命令</summary>
        <div className="manage-grid">
          {text("codex_home", "Codex 数据目录")}
          {text("install_directory", "Codex Skill 安装目录")}
          {text("poll_seconds", "后台扫描间隔（秒）", "number")}
          {text("timeout_seconds", "模型超时（秒）", "number")}
          <label>
            Codex 启动命令（JSON 数组）
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
            />
          </label>
        </div>
      </details>
      <div className="save-bar">
        <span className="muted small">修改后保存，下一次整理时生效。</span>
        <button className="primary" disabled={action.busy} type="submit">
          {action.busy ? "正在保存…" : "保存设置"}
        </button>
      </div>
      {action.status}
    </form>
  );
}

export function Manage({
  snapshot,
  revision,
  refresh,
  project,
  layer,
  wiki,
}: {
  snapshot: Snapshot;
  revision: number;
  refresh: () => void;
  project?: string;
  layer?: string;
  wiki?: string;
}) {
  const [path, setPath] = useState("");
  const [createdProject, setCreatedProject] = useState<string | null>(null);
  useEffect(() => {
    if (
      createdProject &&
      snapshot.projects.some((p) => p.id === createdProject)
    ) {
      navigate("manage", { project: createdProject, layer: "wiki" });
      setCreatedProject(null);
    }
  }, [createdProject, snapshot.projects]);
  const [search, setSearch] = useState("");
  const selected = snapshot.projects.find((p) => p.id === project);
  const tab = ["raw", "wiki", "skill"].includes(layer || "") ? layer! : "raw";
  const createAction = useAction(refresh);
  const steps = [
    {
      id: "raw",
      title: "原始记录",
      description: "查看对话，整理为知识",
      count: selected?.raw.pending,
    },
    {
      id: "wiki",
      title: "知识文档",
      description: "阅读、编辑与生成 Skill",
      count: selected?.wiki_pages,
    },
    {
      id: "skill",
      title: "Skill 管理",
      description: "查看结果与安装",
      count: selected?.skills.filter((s) => s.owned).length,
    },
  ];
  const guide = (
    <details
      className="panel setup-guide"
      open={!snapshot.projects.length ? true : undefined}
    >
      <summary>
        使用指南与数据接入{" "}
        <span className="muted small">从第一次使用到安装 Skill</span>
      </summary>
      <div className="guide-grid">
        <div>
          <span className="step-number">1</span>
          <h3>连接与配置</h3>
          <p>
            运行 <code>wikiskill-codex init</code>，将输出的 mcp_command 注册到
            Codex。可选择手动处理，或在设置中开启自动监听。
          </p>
          <a href={href("system", { project })}>
            前往设置 <ArrowRight size={14} />
          </a>
        </div>
        <div>
          <span className="step-number">2</span>
          <h3>准备项目经验</h3>
          <p>
            首次采集从启用时开始。导入历史可读取本机已有对话，也可以添加项目后直接编写知识。
          </p>
          <a href={href("system", { project })}>前往设置导入历史 →</a>
        </div>
        <div>
          <span className="step-number">3</span>
          <h3>整理并应用</h3>
          <p>
            选择项目，将记录整理为知识，检查后生成 Skill，最后点击“安装到
            Codex”。
          </p>
          <a href={href("jobs", { project })}>
            查看执行进度 <ArrowRight size={14} />
          </a>
        </div>
      </div>
      <div className="guide-footer">
        <span className="muted small">
          {snapshot.worker.status === "online"
            ? "后台已连接，按所选模式工作"
            : "需要后台运行才能采集记录和执行整理"}
        </span>
        <a href={href("system", { project })}>管理后台 →</a>
      </div>
    </details>
  );
  return (
    <div className="manage-workspace">
      <SessionPicker refresh={refresh} />
      <section className={`workspace-hero ${selected ? "is-project" : ""}`}>
        <div className="workspace-title">
          <div className="eyebrow">
            {selected ? "PROJECT WORKSPACE" : "WIKISKILL / PERSONAL KNOWLEDGE"}
          </div>
          <h1>
            {selected ? (
              selected.name
            ) : (
              <>
                让经验，
                <br />
                <em>持续生长。</em>
              </>
            )}
          </h1>
          <p>
            {selected
              ? selected.path
              : "记录解决问题的过程，积累值得复用的知识。"}
          </p>
        </div>
        {!selected && (
          <dl className="workspace-totals" aria-label="知识空间统计">
            <div>
              <dt>项目空间</dt>
              <dd>{snapshot.projects.length.toString().padStart(2, "0")}</dd>
            </div>
            <div>
              <dt>知识文档</dt>
              <dd>
                {snapshot.projects
                  .reduce((total, p) => total + p.wiki_pages, 0)
                  .toString()
                  .padStart(2, "0")}
              </dd>
            </div>
            <div>
              <dt>项目 Skill</dt>
              <dd>
                {new Set(
                  snapshot.projects.flatMap((p) =>
                    p.skills.filter((s) => s.owned).map((s) => s.id),
                  ),
                ).size
                  .toString()
                  .padStart(2, "0")}
              </dd>
            </div>
          </dl>
        )}
      </section>
      {!snapshot.projects.length && guide}
      {selected ? (
        <>
          <div className="project-context">
            <a className="subdued-link" href={href("manage")}>
              ← 全部项目
            </a>
            <a className="subdued-link" href={href("jobs", { project })}>
              执行记录{" "}
              {selected.active_jobs > 0
                ? `· ${selected.active_jobs} 个进行中`
                : ""}
              <ArrowRight size={14} />
            </a>
          </div>
          <nav className="workflow-steps" aria-label="项目工作流程">
            {steps.map((step, index) => (
              <a
                key={step.id}
                href={href("manage", { project, layer: step.id })}
                aria-current={tab === step.id ? "step" : undefined}
              >
                <span className="step-number">{index + 1}</span>
                <div>
                  <strong>
                    {step.title} <small>{step.count ?? 0}</small>
                  </strong>
                  <span>{step.description}</span>
                </div>
              </a>
            ))}
          </nav>
          {(selected.active_jobs > 0 || selected.failed_jobs > 0) && (
            <div className="next-action">
              <span className="muted small">
                {selected.active_jobs > 0
                  ? "正在整理，结果会自动更新。"
                  : "有执行记录需要处理。"}
              </span>
              {selected.active_jobs > 0 && (
                <a href={href("jobs", { project })}>查看进度 →</a>
              )}
              {selected.failed_jobs > 0 && (
                <a href={href("jobs", { project, state: "failed" })}>
                  失败记录（{selected.failed_jobs}）→
                </a>
              )}
            </div>
          )}
          {tab === "raw" ? (
            <RawPanel
              key={selected.id}
              project={selected.id}
              revision={revision}
              refresh={refresh}
            />
          ) : tab === "wiki" ? (
            <WikiPanel
              initialName={wiki}
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
        <>
          {project && <ErrorMessage error="未找到这个项目，请重新选择。" />}
          <div className="section-heading library-heading">
            <div>
              <h2>
                我的项目{" "}
                <span className="muted small">
                  {snapshot.projects.length} 个项目
                </span>
              </h2>
            </div>
            {snapshot.projects.length > 0 && (
              <input
                className="search-input"
                aria-label="搜索项目"
                placeholder="搜索项目…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            )}
          </div>
          <div className="project-grid studio-projects">
            {snapshot.projects
              .filter((p) =>
                `${p.name} ${p.path}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((p, index) => (
                <a
                  className="panel workspace-project"
                  key={p.id}
                  href={href("manage", { project: p.id })}
                >
                  <div className="project-card-meta">
                    <span>
                      PROJECT / {(index + 1).toString().padStart(2, "0")}
                    </span>
                    <ArrowRight size={18} />
                  </div>
                  <div className="project-card-heading">
                    <div>
                      <h3>{p.name}</h3>
                      <p className="muted" title={p.path}>
                        {p.path}
                      </p>
                    </div>
                  </div>
                  <div className="project-stats">
                    <span>
                      <strong>{p.raw.pending}</strong>待整理记录
                    </span>
                    <span>
                      <strong>{p.wiki_pages}</strong>知识文档
                    </span>
                    <span>
                      <strong>{p.skills.filter((s) => s.owned).length}</strong>
                      项目 Skill
                    </span>
                  </div>
                  <div className="project-card-footer">
                    <span>
                      {p.active_jobs
                        ? `${p.active_jobs} 个操作进行中`
                        : p.failed_jobs
                          ? `${p.failed_jobs} 个操作失败，可查看执行记录`
                          : p.wiki_pages
                            ? "知识持续积累中"
                            : "等待第一篇知识"}
                    </span>
                    <span>打开项目</span>
                  </div>
                </a>
              ))}
          </div>
          {search &&
            !snapshot.projects.some((p) =>
              `${p.name} ${p.path}`
                .toLowerCase()
                .includes(search.toLowerCase()),
            ) && <Empty title="没有匹配的项目">试试其他名称或路径。</Empty>}
          <details
            className="panel add-project"
            open={!snapshot.projects.length ? true : undefined}
          >
            <summary>
              <Plus size={17} /> 添加本地项目
            </summary>
            <p className="muted small">
              填写项目的绝对路径。添加后即可编写知识文档；已有对话可在设置中导入。
            </p>
            <form
              className="manage-actions"
              onSubmit={(e) => {
                e.preventDefault();
                void createAction.run(async () => {
                  const result = await mutate<{ id: string }>("/api/projects", {
                    path: path.trim(),
                  });
                  setPath("");
                  setCreatedProject(result.id);
                }, "项目已添加");
              }}
            >
              <input
                required
                aria-label="项目目录"
                placeholder="例如 /Users/you/projects/my-app"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
              <button
                className="primary"
                disabled={createAction.busy || !path.trim()}
              >
                添加并打开
              </button>
            </form>
            {createAction.status}
          </details>
        </>
      )}
      {snapshot.projects.length > 0 && guide}
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
      <div className="document-heading">
        <h2>原始记录</h2>
        <a href={href("manage", { project, layer: "wiki" })}>
          查看整理后的知识 <ArrowRight size={14} />
        </a>
      </div>
      <p>整理全部记录，或选择对话后勾选部分内容。</p>
      <div className="manage-actions">
        <button
          className="primary"
          disabled={action.busy}
          onClick={() =>
            void action.run(
              () =>
                mutate(`/api/projects/${project}/convert`, { stage: "raw" }),
              "整理批次已创建，可在执行记录查看进度",
            )
          }
        >
          整理全部待处理记录
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
      <a className="subdued-link" href={href("jobs", { project })}>
        查看整理进度 →
      </a>
      {action.status}
      <ErrorMessage error={turns.error} />
      <div className="manage-split">
        <div>
          {turns.loading && !turns.data && <Loading />}
          {turns.data?.items.map((t) => (
            <button
              className="manage-row"
              key={t.id}
              aria-pressed={turn === t.id}
              onClick={() => {
                setTurn(t.id);
                setRecordOffset(0);
              }}
            >
              <strong>
                {t.turn_key === "unassigned"
                  ? "未归属轮次的记录"
                  : `对话轮次 #${t.id}`}
              </strong>
              <span>
                {t.session} · {t.ended ? "已结束" : "未结束"} · {t.records} 条 /{" "}
                {t.pending} 待处理
              </span>
            </button>
          ))}
          {turns.data && !turns.data.items.length && (
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
          {turn === null && (
            <Empty title="选择一个对话轮次">
              在左侧选择记录，查看原文或勾选要整理的内容。
            </Empty>
          )}
          {records.loading && !records.data && <Loading />}
          {records.data && !records.data.items.length && (
            <Empty title="此轮次没有记录" />
          )}
          {records.data && records.data.items.some((r) => !r.consumed_by) && (
            <div className="manage-actions">
              <button
                onClick={() =>
                  setChosen(
                    Array.from(
                      new Set([
                        ...chosen,
                        ...records
                          .data!.items.filter((r) => !r.consumed_by)
                          .map((r) => r.id),
                      ]),
                    ),
                  )
                }
              >
                选中本页待处理记录
              </button>
              <button disabled={!chosen.length} onClick={() => setChosen([])}>
                清空选择（{chosen.length}）
              </button>
            </div>
          )}
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
  initialName,
  project,
  revision,
  refresh,
}: {
  initialName?: string;
  project: string;
  revision: number;
  refresh: () => void;
}) {
  const [offset, setOffset] = useState(0),
    [name, setName] = useState<string | null>(initialName || null);
  const [newEditor, setNewEditor] = useState(0);
  useEffect(() => setName(initialName || null), [initialName]);
  const openWiki = (nextName: string) => {
    if (nextName !== "" && nextName === name) return;
    if (!confirmDiscardChanges()) return;
    if (nextName === "") setNewEditor((value) => value + 1);
    setName(nextName);
  };
  const [selected, setSelected] = useState<string[]>([]);
  const [generation, setGeneration] = useState<{
    pages: string[];
    id: number;
  } | null>(null);
  const generationSequence = useRef(0);
  const openGeneration = (pages: string[]) =>
    setGeneration({ pages, id: ++generationSequence.current });
  const [search, setSearch] = useState("");
  const list = useResource<Page<{ name: string; excerpt: string }>>(
    `/api/projects/${project}/wiki${query({ offset, q: search })}`,
    revision,
  );
  const detail = useResource<Wiki>(
    name ? `/api/projects/${project}/wiki/${encodeURIComponent(name)}` : null,
    revision,
  );
  return (
    <section className="panel document-panel">
      <div className="document-heading">
        <h2>知识文档</h2>
        <button className="primary" onClick={() => openWiki("")}>
          ＋ 新建知识页
        </button>
      </div>
      <p className="muted small">
        将项目做法整理成 Markdown 文档，保存后可用于生成 Skill。
      </p>
      <WikiSearch
        value={search}
        onSearch={(value) => {
          setSearch(value);
          setOffset(0);
        }}
      />
      <div className="manage-actions">
        <button
          disabled={!selected.length}
          onClick={() => openGeneration([...selected])}
        >
          从选定 Wiki 生成 Skill（{selected.length}）
        </button>
        <button
          disabled={!list.data?.items.length}
          onClick={() =>
            setSelected((old) => [
              ...new Set([...old, ...list.data!.items.map((p) => p.name)]),
            ])
          }
        >
          选择本页 Wiki
        </button>
        <button disabled={!selected.length} onClick={() => setSelected([])}>
          清空 Wiki 选择
        </button>
      </div>
      {generation && (
        <SkillGeneration
          key={generation.id}
          project={project}
          pages={generation.pages}
          close={() => setGeneration(null)}
          refresh={refresh}
        />
      )}
      <WikiTransfer
        key={project}
        project={project}
        selected={selected}
        refresh={refresh}
      />
      <ErrorMessage error={list.error} />
      <div className="manage-split">
        <div>
          {list.loading && !list.data && <Loading />}
          {!list.error && list.data && !list.data.items.length && (
            <Empty title={search ? "没有匹配的 Wiki" : "还没有知识文档"}>
              {search
                ? "试试其他名称或正文内容，或清空搜索查看全部文档。"
                : "从原始记录整理，或点击“新建知识页”开始编写。"}
            </Empty>
          )}
          {list.data?.items.map((p) => (
            <div className="wiki-selection-row" key={p.name}>
              <input
                type="checkbox"
                aria-label={`选择 Wiki ${p.name}`}
                checked={selected.includes(p.name)}
                onChange={(e) =>
                  setSelected((old) =>
                    e.target.checked
                      ? [...old, p.name]
                      : old.filter((n) => n !== p.name),
                  )
                }
              />
              <button
                className="manage-row"
                aria-label={`打开 Wiki ${p.name}`}
                aria-pressed={name === p.name}
                onClick={() => openWiki(p.name)}
              >
                <strong>{p.name}</strong>
                <span>{p.excerpt}</span>
              </button>
            </div>
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
          {name === null && (
            <Empty title="选择一篇知识文档">
              从左侧打开文档，阅读和编辑内容。
            </Empty>
          )}
          {detail.loading && !detail.data && <Loading />}
          {name === "" ? (
            <WikiEditor
              key={`new-${newEditor}`}
              project={project}
              refresh={refresh}
              onGenerate={(name) => openGeneration([name])}
            />
          ) : (
            detail.data && (
              <WikiEditor
                key={detail.data.name}
                project={project}
                initial={detail.data}
                onGenerate={(name) => openGeneration([name])}
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
  onGenerate,
}: {
  project: string;
  initial?: Wiki;
  refresh: () => void;
  onGenerate: (name: string) => void;
}) {
  const [name, setName] = useState(initial?.name || ""),
    [body, setBody] = useState(initial?.body || "");
  const [expected, setExpected] = useState(initial?.digest || null);
  const [savedBody, setSavedBody] = useState(initial?.body || "");
  const [savedName, setSavedName] = useState(initial?.name || "");
  const action = useAction(refresh);
  const dirty = name !== savedName || body !== savedBody;
  useUnsavedChanges(dirty || action.busy);
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
            const saved = await request<Wiki>(
              `/api/projects/${project}/wiki/${encodeURIComponent(name)}`,
            );
            if (saved.body !== body) {
              throw new Error(
                "保存后正文已发生变化。已保留当前输入，请重新打开文档后核对。",
              );
            }
            setExpected(saved.digest);
            setSavedBody(saved.body);
            setSavedName(name);
          }, "Wiki 已保存");
        }}
      >
        <label>
          页面名称
          <input
            required
            pattern="[a-z0-9][a-z0-9-]{0,100}"
            disabled={expected !== null || action.busy}
            placeholder="例如 build-and-test"
            title="使用小写字母、数字或连字符，以字母或数字开头，最长 101 个字符"
            aria-describedby="wiki-name-hint"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <p id="wiki-name-hint" className="muted small">
          名称使用小写字母、数字或连字符，例如 build-and-test。保存后名称固定。
        </p>
        <label>
          正文（Markdown）
          <textarea
            required
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <button className="primary" disabled={action.busy}>
          {action.busy ? "正在保存…" : "保存 Wiki"}
        </button>
      </form>
      {action.status}
      {dirty && (
        <p className="muted small" role="status">
          有未保存的修改
        </p>
      )}
      <details>
        <summary>正文预览</summary>
        <Markdown text={body} />
      </details>
      {expected !== null && (
        <WikiSources key={name} project={project} name={name} />
      )}
      {expected !== null && (
        <div className="manage-actions">
          <button
            disabled={action.busy || body !== savedBody}
            onClick={() => onGenerate(name)}
          >
            从此 Wiki 生成 Skill
          </button>
          {body !== savedBody && (
            <span className="muted small">请先保存正文，再生成 Skill。</span>
          )}
          <a href={href("knowledge", { project, wiki: name, layer: "wiki" })}>
            查看历史与恢复版本
          </a>
        </div>
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
      <div className="notice">
        <Check size={18} />
        <span>
          在此查看和安装已有 Skill。生成新内容请前往知识文档选择 Wiki。
        </span>
      </div>
      {!project.skills.some((s) => s.owned) && (
        <Empty title="此项目暂无 Skill">
          请先在知识文档中选择 Wiki 生成 Skill。
          <a href={href("manage", { project: project.id, layer: "wiki" })}>
            前往知识文档 →
          </a>
        </Empty>
      )}
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
          className="primary"
          title={
            !detail.data?.disk_text
              ? "请先在知识文档中生成 Skill，再安装"
              : undefined
          }
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
        <a href={href("jobs", { project })}>查看生成进度 →</a>
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
    </section>
  );
}
