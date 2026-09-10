import { useState } from "react";
import { useResource } from "./api";
import { ErrorMessage, Pager } from "./components";
import type { Page } from "./types";
export function WikiSources({
  project,
  name,
}: {
  project: string;
  name: string;
}) {
  const [open, setOpen] = useState(false),
    [offset, setOffset] = useState(0);
  const data = useResource<Page<{ id: number; session: string; text: string }>>(
    open
      ? `/api/projects/${project}/wiki/${encodeURIComponent(name)}/sources?offset=${offset}`
      : null,
  );
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>查看关联原始记录</summary>
      <ErrorMessage error={data.error} />
      {data.data?.items.map((r) => (
        <article key={r.id}>
          <p>
            记录 #{r.id} · {r.session}
          </p>
          <pre className="code-block" style={{ whiteSpace: "pre-wrap" }}>
            {r.text}
          </pre>
        </article>
      ))}
      {data.data && !data.data.items.length && (
        <p>此页面没有可追溯的原始记录，可能由人工编写或导入。</p>
      )}
      {data.data && (
        <Pager
          offset={offset}
          next={data.data.next_offset}
          onPage={setOffset}
        />
      )}
    </details>
  );
}
