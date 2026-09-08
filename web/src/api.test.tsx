import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLiveSnapshot, useResource } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("read-only data loading", () => {
  it("does not replace a new selection with a late response from the previous one", async () => {
    const first = deferred();
    const second = deferred();
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string) =>
        path === "/first" ? first.promise : second.promise,
      ),
    );
    const hook = renderHook(({ path }) => useResource<{ id: string }>(path), {
      initialProps: { path: "/first" },
    });
    hook.rerender({ path: "/second" });
    await act(async () => {
      second.resolve(Response.json({ id: "second" }));
    });
    await waitFor(() => expect(hook.result.current.data?.id).toBe("second"));
    await act(async () => {
      first.resolve(Response.json({ id: "first" }));
    });
    expect(hook.result.current.data?.id).toBe("second");
  });

  it("retains the last snapshot when a refresh fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ id: "saved" }))
        .mockRejectedValueOnce(new Error("connection lost")),
    );
    const hook = renderHook(
      ({ revision }) => useResource<{ id: string }>("/item", revision),
      { initialProps: { revision: 0 } },
    );
    await waitFor(() => expect(hook.result.current.data?.id).toBe("saved"));
    hook.rerender({ revision: 1 });
    await waitFor(() =>
      expect(hook.result.current.error).toBe("connection lost"),
    );
    expect(hook.result.current.data?.id).toBe("saved");
  });

  it("refreshes after event stream reconnection and closes the subscription on unmount", async () => {
    class Source {
      static current: Source;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      listeners: Record<string, () => void> = {};
      close = vi.fn();
      constructor() {
        Source.current = this;
      }
      addEventListener(name: string, listener: () => void) {
        this.listeners[name] = listener;
      }
    }
    vi.stubGlobal("EventSource", Source);
    let cursor = 1;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ cursor })),
    );
    const hook = renderHook(() => useLiveSnapshot());
    await waitFor(() => expect(hook.result.current.data?.cursor).toBe(1));
    act(() => Source.current.onerror?.());
    expect(hook.result.current.connection).toBe("reconnecting");
    expect(hook.result.current.data?.cursor).toBe(1);
    cursor = 2;
    act(() => Source.current.onopen?.());
    await waitFor(() => expect(hook.result.current.data?.cursor).toBe(2));
    expect(hook.result.current.connection).toBe("connected");
    const source = Source.current;
    hook.unmount();
    expect(source.close).toHaveBeenCalledOnce();
  });
});
