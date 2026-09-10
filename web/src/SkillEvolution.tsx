import { useState } from "react";
import { mutate, useResource } from "./api";
import { useAction } from "./actions";
import { ErrorMessage } from "./components";
import { href } from "./routing";
import { time } from "./presentation";

type Detail = {
  sources: {
    project: string;
    name: string;
    change_id: number;
    needs_update: boolean;
  }[];
  feedback: {
    id: number;
    version_id: string;
    kind: string;
    body: string;
    created: number;
  }[];
};
const labels: Record<string, string> = {
  useful: "实际使用有效",
  problem: "使用遇到问题",
  rejected: "不采用此改动",
  note: "补充说明",
  rollback: "已恢复历史内容",
};
export function SkillEvolution({
  skill,
  version,
  revision,
}: {
  skill: string;
  version?: string;
  revision: number;
}) {
  const [local, setLocal] = useState(0),
    [kind, setKind] = useState("problem"),
    [body, setBody] = useState("");
  const action = useAction(() => setLocal((n) => n + 1));
  const data = useResource<Detail>(
    `/api/skills/${encodeURIComponent(skill)}/evolution`,
    revision + local,
  );
  return (
    <section className="panel document-panel" aria-label="Skill 来源与反馈">
      <h3>来源与后续改进</h3>
      <ErrorMessage error={data.error} />
      {data.data?.sources.map((s) => (
        <p key={`${s.project}/${s.name}`}>
          <a
            href={href("manage", {
              project: s.project,
              layer: "wiki",
              wiki: s.name,
            })}
          >
            {s.name}
          </a>
          {" · "}生成依据：版本 {s.change_id}
          {s.needs_update
            ? " · Wiki 已更新，可重新选择合并"
            : " · 当前依据未变化"}
        </p>
      ))}
      {!data.data?.sources.length && (
        <p className="muted">暂无已记录的 Wiki 来源。</p>
      )}
      <p className="muted small">
        生成不代表已验证有效。反馈会在后续生成时提供给模型；填写反馈不会调用模型或修改
        Skill。
      </p>
      {version && (
        <form
          className="manage-form"
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(async () => {
              await mutate(`/api/skills/${skill}/feedback`, {
                version_id: version,
                kind,
                body,
              });
              setBody("");
            }, "反馈已保存，下次生成会参考");
          }}
        >
          <p>
            反馈针对当前选中的版本：<code>{version}</code>
          </p>
          <label>
            反馈类型
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(labels)
                .filter(([k]) => k !== "rollback")
                .map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          <label>
            具体结果或原因
            <textarea
              required
              maxLength={8000}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="发生了什么、采用了哪条指导、结果如何"
            />
          </label>
          <button disabled={action.busy || !body.trim()}>保存反馈</button>
          {action.status}
        </form>
      )}
      {data.data?.feedback.map((f) => (
        <article key={f.id}>
          <strong>{labels[f.kind] || f.kind}</strong>
          <small>
            {" "}
            · {time(f.created)} · {f.version_id.slice(0, 8)}
          </small>
          <p style={{ whiteSpace: "pre-wrap" }}>{f.body}</p>
        </article>
      ))}
    </section>
  );
}
