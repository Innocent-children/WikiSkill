import { useState } from "react";
import { mutate, useResource } from "./api";
import { useAction } from "./actions";
import { ErrorMessage, Loading, Pager } from "./components";
import { href } from "./routing";
import { query, time } from "./presentation";
import type { Page } from "./types";

type Session = {
  id: string;
  session: string;
  title: string;
  cwd: string;
  bytes: number;
  modified: number;
};
export function SessionPicker({ refresh }: { refresh: () => void }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const [jobs, setJobs] = useState<{ job_id: string }[]>([]);
  const sessions = useResource<Page<Session>>(
    open ? "/api/sessions" + query({ q, offset }) : null,
    revision,
  );
  const action = useAction(() => {
    setRevision((n) => n + 1);
    refresh();
  });
  return (
    <section className="panel document-panel" aria-label="选择本机会话">
      <button onClick={() => setOpen(!open)}>
        {open ? "收起本机会话" : "选择本机会话"}
      </button>
      <p className="muted small">
        浏览和导入不调用模型。选中会话后可直接提取 Wiki；手动模式下 Skill
        由你另外生成。
      </p>
      {open && (
        <>
          <form
            className="manage-actions"
            onSubmit={(e) => {
              e.preventDefault();
              setQ(search);
              setOffset(0);
              setRevision((n) => n + 1);
            }}
          >
            <label>
              搜索会话
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="标题、项目目录或会话 ID"
              />
            </label>
            <button>搜索 / 刷新</button>
          </form>
          <ErrorMessage error={sessions.error} />
          {sessions.loading && <Loading />}
          <div className="document-list">
            {sessions.data?.items.map((s) => (
              <label key={s.id} className="panel document-panel">
                <input
                  type="checkbox"
                  checked={selected.includes(s.id)}
                  onChange={(e) =>
                    setSelected((prev) =>
                      e.target.checked
                        ? [...prev, s.id]
                        : prev.filter((i) => i !== s.id),
                    )
                  }
                />
                <strong>{s.title}</strong>
                <p className="muted small">
                  {s.cwd} · {time(s.modified)} · {Math.ceil(s.bytes / 1024)} KiB
                </p>
              </label>
            ))}
          </div>
          {sessions.data && !sessions.data.items.length && (
            <p>没有匹配的会话。</p>
          )}
          {sessions.data && (
            <Pager
              offset={offset}
              next={sessions.data.next_offset}
              onPage={setOffset}
            />
          )}
          <div className="manage-actions">
            <button
              disabled={action.busy || !selected.length}
              onClick={() =>
                void action.run(async () => {
                  await mutate("/api/sessions/import", {
                    ids: selected,
                    analyze: false,
                  });
                  setSelected([]);
                }, "选定会话已导入，尚未调用模型")
              }
            >
              仅导入选定会话（{selected.length}）
            </button>
            <button
              className="primary"
              disabled={action.busy || !selected.length}
              onClick={() =>
                void action.run(async () => {
                  const result = await mutate<{ jobs: { job_id: string }[] }>(
                    "/api/sessions/import",
                    { ids: selected, analyze: true },
                  );
                  setJobs(result.jobs);
                  setSelected([]);
                }, "已导入选定会话；未处理内容已提交分析")
              }
            >
              提取选定会话的 Wiki
            </button>
          </div>
          {jobs.map((j) => (
            <p key={j.job_id}>
              <a href={href("jobs", { job: j.job_id })}>查看本次分析 →</a>
            </p>
          ))}
          {action.status}
        </>
      )}
    </section>
  );
}
