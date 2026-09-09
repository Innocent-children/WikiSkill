import { useRef, useState } from "react";
import { mutate } from "./api";
import { ErrorMessage } from "./components";
import type { WikiImportPreview } from "./types";

const MAX_ZIP_BYTES = 10 * 1024 * 1024;

function readZip(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("无法读取选定的 ZIP 文件"));
    reader.readAsDataURL(file);
  });
}

export function WikiTransfer({
  project,
  selected,
  refresh,
}: {
  project: string;
  selected: string[];
  refresh: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<{
    archive: string;
    result: WikiImportPreview;
  } | null>(null);
  const [confirmed, setConfirmed] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  const base = `/api/projects/${encodeURIComponent(project)}`;
  const modified =
    preview?.result.pages.filter((page) => page.status === "modified") ?? [];

  async function run(work: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await work();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function download(names?: string[]) {
    const response = await fetch(
      `${base}/wiki-export`,
      names
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pages: names }),
          }
        : { cache: "no-store" },
    );
    if (!response.ok) {
      const value = await response.json().catch(() => null);
      throw new Error(
        typeof value?.detail === "string" ? value.detail : "导出失败，请重试",
      );
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `wiki-${project}${names ? "-selected" : ""}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage(
      names ? `已下载 ${names.length} 篇选定 Wiki` : "已下载当前项目全部 Wiki",
    );
  }

  return (
    <section className="wiki-transfer" aria-label="Wiki 导入导出">
      <h3>Wiki 导入导出</h3>
      <p className="muted small">
        ZIP 仅包含已保存的 Markdown 正文；选定导出使用上方的跨页选择。
      </p>
      <div className="manage-actions">
        <button disabled={busy} onClick={() => void run(() => download())}>
          导出全部 Wiki
        </button>
        <button
          disabled={busy || !selected.length}
          onClick={() => void run(() => download([...selected]))}
        >
          导出选定 Wiki（{selected.length}）
        </button>
      </div>
      <label className="wiki-transfer-file">
        导入 Wiki ZIP
        <input
          type="file"
          accept=".zip,application/zip"
          disabled={busy}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setPreview(null);
            setConfirmed([]);
            setError(null);
            setMessage("");
          }}
        />
      </label>
      <p className="muted small">
        根目录使用小写字母、数字或连字符命名的 .md 文件（名称最长 101
        字符），正文须为非空 UTF-8。 ZIP 最大 10 MiB，最多 1000 页，单页 2
        MiB，正文合计 20 MiB。
      </p>
      <button
        disabled={busy || !file}
        onClick={() =>
          void run(async () => {
            setPreview(null);
            setConfirmed([]);
            if (!file) return;
            if (file.size > MAX_ZIP_BYTES)
              throw new Error("ZIP 文件超过 10 MiB 限制");
            const archive = await readZip(file);
            const result = await mutate<WikiImportPreview>(
              `${base}/wiki-import/preview`,
              { archive_base64: archive },
            );
            setPreview({ archive, result });
            setMessage("预览完成，尚未写入任何 Wiki");
          })
        }
      >
        预览 / 重新预览 ZIP
      </button>
      {preview && (
        <div className="wiki-transfer-preview">
          <p>
            新增 {preview.result.counts.added} 篇 · 修改{" "}
            {preview.result.counts.modified} 篇 · 无变化{" "}
            {preview.result.counts.unchanged} 篇
          </p>
          <p className="muted small">
            请查看同名修改并逐项确认。预览后项目 Wiki
            有新编辑时，整批导入会被拒绝，需要重新预览。
          </p>
          {preview.result.pages.map((page) => (
            <article key={page.name} className="wiki-transfer-page">
              <h4>
                {page.name} ·{" "}
                {
                  { added: "新增", modified: "修改", unchanged: "无变化" }[
                    page.status
                  ]
                }
              </h4>
              {page.status !== "unchanged" && (
                <>
                  <details open={page.status === "modified"}>
                    <summary>查看 {page.name} 的正文差异</summary>
                    <pre className="wiki-transfer-diff">{page.diff}</pre>
                  </details>
                  <details>
                    <summary>查看 {page.name} 的完整正文</summary>
                    {page.before !== null && (
                      <>
                        <h5>当前正文</h5>
                        <pre>{page.before}</pre>
                      </>
                    )}
                    <h5>导入正文</h5>
                    <pre>{page.body}</pre>
                  </details>
                </>
              )}
              {page.status === "modified" && (
                <label className="generation-choice">
                  <input
                    type="checkbox"
                    disabled={busy}
                    checked={confirmed.includes(page.name)}
                    onChange={(event) => {
                      setConfirmed((old) =>
                        event.target.checked
                          ? [...old, page.name]
                          : old.filter((name) => name !== page.name),
                      );
                    }}
                  />
                  确认修改 {page.name}
                </label>
              )}
            </article>
          ))}
          <button
            className="primary"
            disabled={
              busy ||
              modified.some((page) => !confirmed.includes(page.name)) ||
              !(preview.result.counts.added + preview.result.counts.modified)
            }
            onClick={() =>
              void run(async () => {
                const submission = preview;
                const overwrite = [...confirmed];
                setPreview(null);
                setConfirmed([]);
                const result = await mutate<{ new_changes: number }>(
                  `${base}/wiki-import`,
                  {
                    archive_base64: submission.archive,
                    preview_token: submission.result.preview_token,
                    overwrite,
                  },
                );
                setMessage(`导入完成，记录 ${result.new_changes} 个 Wiki 版本`);
                refresh();
              })
            }
          >
            确认并导入
          </button>
        </div>
      )}
      <ErrorMessage error={error} />
      <p role="status">{busy ? "正在处理，请稍候…" : message}</p>
    </section>
  );
}
