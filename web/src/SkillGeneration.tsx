import { useEffect, useRef, useState } from "react";
import { mutate } from "./api";
import { useAction } from "./actions";
import { ErrorMessage, Loading, Markdown } from "./components";
import { href } from "./routing";

type Candidate = {
  id: string;
  name: string;
  description: string;
  skill_md: string;
  digest: string;
  matched_terms: string[];
  linked_pages?: string[];
  score: number;
  projects: { id: string; name: string }[];
};
type Preview = {
  pages: { name: string; body: string; digest: string }[];
  suggested_name: string;
  candidates: Candidate[];
  unavailable: { name: string; reason: string }[];
};

export function SkillGeneration({
  project,
  pages,
  close,
  refresh = () => {},
}: {
  project: string;
  pages: string[];
  close: () => void;
  refresh?: () => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<{
    job_id: string;
    skill_id: string;
  } | null>(null);
  const action = useAction(refresh);
  const section = useRef<HTMLElement>(null);
  const selection = JSON.stringify(pages);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    section.current?.focus();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    let active = true;
    setPreview(null);
    setError(null);
    setTarget("");
    mutate<Preview>(`/api/projects/${project}/skill-generation/preview`, {
      pages: JSON.parse(selection),
    })
      .then((value) => {
        if (active) {
          setPreview(value);
          setName(value.suggested_name);
        }
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [project, selection, attempt]);
  const candidate = preview?.candidates.find((item) => item.id === target);
  const candidates =
    preview?.candidates.filter((item) =>
      `${item.name} ${item.description} ${item.skill_md} ${item.projects.map((p) => p.name).join(" ")}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) || [];
  return (
    <section
      className="panel document-panel skill-generation"
      aria-label="从选定 Wiki 生成 Skill"
      tabIndex={-1}
      ref={section}
    >
      <div className="document-heading">
        <h2>从选定 Wiki 生成 Skill</h2>
        <button disabled={action.busy} onClick={close}>
          关闭生成面板
        </button>
      </div>
      {result ? (
        <div role="status">
          <p>
            已提交 {pages.length} 篇 Wiki，生成结果保存在 WikiSkill，安装后可在
            Codex 使用。
          </p>
          <div className="manage-actions">
            <a href={href("jobs", { project, job: result.job_id })}>
              查看生成进度
            </a>
            <a href={href("skills", { project, skill: result.skill_id })}>
              查看 Skill 正文
            </a>
            <a href={href("manage", { project, layer: "skill" })}>前往安装</a>
          </div>
        </div>
      ) : (
        <>
          <p>
            已选 {pages.length} 篇：{pages.join("、")}。使用已保存的当前正文。
          </p>
          <ErrorMessage error={error} />
          {!preview && !error && <Loading />}
          <button
            disabled={action.busy}
            onClick={() => setAttempt((n) => n + 1)}
          >
            重新读取正文与候选
          </button>
          {preview && (
            <form
              className="manage-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action.run(
                  async () => {
                    const value = await mutate<{
                      job_id: string;
                      skill_id: string;
                    }>(`/api/projects/${project}/skill-generation`, {
                      pages,
                      expected: Object.fromEntries(
                        preview.pages.map((p) => [p.name, p.digest]),
                      ),
                      ...(candidate
                        ? {
                            skill: candidate.id,
                            skill_digest: candidate.digest,
                          }
                        : { name }),
                    });
                    setResult(value);
                  },
                  candidate ? "合并已入队" : "新建 Skill 已入队",
                );
              }}
            >
              <fieldset disabled={action.busy}>
                <legend>生成方式</legend>
                <label className="generation-choice">
                  <input
                    type="radio"
                    name="skill-target"
                    checked={!target}
                    onChange={() => setTarget("")}
                  />
                  新建 Skill（默认）
                </label>
                {!target && (
                  <label>
                    Skill 名称
                    <input
                      required
                      pattern="[a-z0-9][a-z0-9-]{0,63}"
                      maxLength={64}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                    <span className="muted small">
                      小写字母、数字或连字符，最长 64 个字符。
                    </span>
                  </label>
                )}
                <h3>查找可合并的已有 Skill</h3>
                <p className="muted small">
                  搜索 WikiSkill 管理的所有项目 Skill，优先展示已关联所选 Wiki
                  的
                  Skill，再按正文匹配词排序。匹配词用于查找，请结合职责和正文选择合并对象。
                </p>
                <label>
                  搜索已有 Skill
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="名称、用途、正文或项目"
                  />
                </label>
                {preview.unavailable.length > 0 && (
                  <p className="notice warning">
                    部分 Skill 无法读取：
                    {preview.unavailable
                      .map((s) => `${s.name}（${s.reason}）`)
                      .join("；")}
                  </p>
                )}
                {candidates.length === 0 && (
                  <p className="muted">
                    没有找到可合并的 Skill，可以继续新建。
                  </p>
                )}
                <div className="generation-candidates">
                  {candidates.map((item) => (
                    <label className="generation-candidate" key={item.id}>
                      <span className="generation-choice">
                        <input
                          type="radio"
                          name="skill-target"
                          checked={target === item.id}
                          onChange={() => setTarget(item.id)}
                        />
                        合并到 {item.name}
                      </span>
                      <span>{item.description}</span>
                      <span className="muted small">
                        关联项目：
                        {item.projects.map((p) => p.name).join("、") || "无"}
                      </span>
                      <span className="muted small">
                        {item.matched_terms.length
                          ? `匹配词：${item.matched_terms.join("、")}`
                          : "未发现共同词，请确认用途是否一致"}
                      </span>
                    </label>
                  ))}
                </div>
                {candidate && (
                  <div className="generation-target">
                    <p>
                      将更新 <strong>{candidate.name}</strong>
                      ，保留已有内容和资源。
                      {candidate.projects.length > 1 ||
                      !candidate.projects.some((p) => p.id === project)
                        ? "该 Skill 与其他项目关联，合并后的内容会供这些项目共同使用。"
                        : ""}
                    </p>
                    <details>
                      <summary>查看合并目标正文</summary>
                      <Markdown
                        text={candidate.skill_md.replace(
                          /^---\n[\s\S]*?\n---\n/,
                          "",
                        )}
                      />
                    </details>
                  </div>
                )}
                <details>
                  <summary>查看本次 Wiki 正文</summary>
                  {preview.pages.map((p) => (
                    <article key={p.name}>
                      <h3>{p.name}</h3>
                      <Markdown text={p.body} />
                    </article>
                  ))}
                </details>
                <button
                  className="primary"
                  disabled={action.busy || (!target && !name)}
                >
                  {candidate ? `合并到 ${candidate.name}` : "新建并生成 Skill"}
                </button>
              </fieldset>
            </form>
          )}
          {action.status}
        </>
      )}
    </section>
  );
}
