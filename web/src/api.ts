import { useEffect, useState } from "react";
import type { Snapshot } from "./types";

export async function request<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, { signal, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `读取失败（${response.status}）`,
    );
  }
  return response.json() as Promise<T>;
}

export function useResource<T>(path: string | null, revision = 0) {
  const [value, setValue] = useState<{
    key: string | null;
    data: T | null;
    error: string | null;
    loading: boolean;
  }>({ key: null, data: null, error: null, loading: !!path });
  useEffect(() => {
    const controller = new AbortController();
    if (!path) return;
    let active = true;
    setValue((old) => ({
      key: path,
      data: old.key === path ? old.data : null,
      error: null,
      loading: true,
    }));
    request<T>(path, controller.signal)
      .then((data) => {
        if (active) setValue({ key: path, data, error: null, loading: false });
      })
      .catch((error: Error) => {
        if (active)
          setValue((old) => ({ ...old, error: error.message, loading: false }));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [path, revision]);
  return value.key === path
    ? value
    : { data: null, error: null, loading: !!path };
}

export function useLiveSnapshot() {
  const [revision, setRevision] = useState(0);
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "reconnecting"
  >("connecting");
  const snapshot = useResource<Snapshot>("/api/snapshot", revision);
  useEffect(() => {
    const source = new EventSource("/api/events");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        setRevision((value) => value + 1);
      }, 150);
    };
    source.onopen = () => {
      setConnection("connected");
      refresh();
    };
    source.addEventListener("change", () => {
      setConnection("connected");
      refresh();
    });
    source.addEventListener("unavailable", () => {
      setConnection("reconnecting");
      refresh();
    });
    source.onerror = () => setConnection("reconnecting");
    const visible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      source.close();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  return {
    ...snapshot,
    connection,
    revision,
    refresh: () => setRevision((value) => value + 1),
  };
}
