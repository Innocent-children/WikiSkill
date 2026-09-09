import { useState } from "react";
import { ErrorMessage } from "./components";

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
        <p
          className={busy || message ? "action-feedback" : undefined}
          role="status"
        >
          {busy ? "正在处理，请稍候…" : message}
        </p>
      </>
    ),
  };
}
