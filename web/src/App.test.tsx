import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { href, navigate } from "./routing";

const queue = { pending: 0, batched: 0, waiting: 0, threshold: 10,
  reason: "below_threshold", job_id: null, job_project: null };
const snapshot = {
  initialized: true, root: "/tmp/wiki-test", captured_at: 0, cursor: 0,
  config: null, config_error: null, worker: { status: "online" },
  projects: ["alpha", "beta"].map((id) => ({ id, name: id, path: `/projects/${id}`,
    raw: queue, raw_count: 0, observation_count: 0, wiki_pages: 2,
    skills: [], updated_at: null, active_jobs: 0, failed_jobs: 0 })),
  totals: { projects: 2, skills: 0, active: 0, failed: 0 },
};

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  useLiveSnapshot: () => ({ data: snapshot, revision: 0, error: null,
    connection: "connected", refresh: vi.fn() }),
}));

const buildRoute = href("manage", { project: "alpha", layer: "wiki", wiki: "build" });
const testsRoute = href("manage", { project: "beta", layer: "wiki", wiki: "tests" });

beforeEach(() => {
  window.history.replaceState(null, "", buildRoute);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (/\/wiki\/(build|tests)$/.test(url)) {
      const name = url.split("/").at(-1)!;
      return Response.json({ name, body: `# ${name}`, digest: name,
        changes: { items: [], next_offset: null } });
    }
    return Response.json({ items: [{ name: "build", excerpt: "Build" },
      { name: "tests", excerpt: "Tests" }], next_offset: null });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "#/manage");
});

async function editor(name: string) {
  await waitFor(() => expect((screen.getByLabelText("页面名称") as HTMLInputElement).value).toBe(name));
  return screen.getByLabelText("正文（Markdown）") as HTMLTextAreaElement;
}

it("keeps the route, project picker and editor when link or project navigation is cancelled", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<StrictMode><App /></StrictMode>);
  const body = await editor("build");
  fireEvent.change(body, { target: { value: "Keep my edits" } });
  fireEvent.click(screen.getByRole("link", { name: /全部项目/ }));
  fireEvent.change(screen.getByLabelText("选择项目"), { target: { value: "beta" } });
  fireEvent.click(screen.getByRole("link", { name: /原始记录/ }));
  expect(confirm).toHaveBeenCalledTimes(3);
  expect(window.location.hash).toBe(buildRoute);
  expect((screen.getByLabelText("选择项目") as HTMLSelectElement).value).toBe("alpha");
  expect(body.value).toBe("Keep my edits");
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("link", { name: /原始记录/ }));
  expect(screen.queryByLabelText("正文（Markdown）")).toBeNull();
  expect(window.location.hash).toBe(href("manage", { project: "alpha", layer: "raw" }));
});

it("synchronizes a wiki deep link only after leaving is confirmed", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<App />);
  const body = await editor("build");
  fireEvent.change(body, { target: { value: "Keep my edits" } });
  act(() => navigate("manage", { project: "alpha", layer: "wiki", wiki: "tests" }));
  expect(body.value).toBe("Keep my edits");
  expect(window.location.hash).toBe(buildRoute);
  confirm.mockReturnValue(true);
  act(() => navigate("manage", { project: "alpha", layer: "wiki", wiki: "tests" }));
  expect((await editor("tests")).value).toBe("# tests");
});

it.each(["back", "forward"] as const)("restores cancelled browser %s without losing text or the history destination", async (direction) => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<App />);
  await editor("build");
  act(() => navigate("manage", { project: "beta", layer: "wiki", wiki: "tests" }));
  await editor("tests");
  if (direction === "forward") {
    act(() => window.history.back());
    await editor("build");
  }
  const current = direction === "back" ? testsRoute : buildRoute;
  const targetName = direction === "back" ? "build" : "tests";
  const body = screen.getByLabelText("正文（Markdown）") as HTMLTextAreaElement;
  fireEvent.change(body, { target: { value: "History draft" } });
  act(() => window.history[direction]());
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  await waitFor(() => expect(window.location.hash).toBe(current));
  expect(body.value).toBe("History draft");
  confirm.mockReturnValue(true);
  act(() => window.history[direction]());
  expect((await editor(targetName)).value).toBe(`# ${targetName}`);
  expect(confirm).toHaveBeenCalledTimes(2);
});

it("guards direct hash changes and permits navigation without edits", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<App />);
  const body = await editor("build");
  fireEvent.change(body, { target: { value: "Hash draft" } });
  act(() => { window.location.hash = testsRoute; });
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  expect(window.location.hash).toBe(buildRoute);
  expect(body.value).toBe("Hash draft");
  fireEvent.change(body, { target: { value: "# build" } });
  act(() => navigate("manage", { project: "beta", layer: "wiki", wiki: "tests" }));
  expect((await editor("tests")).value).toBe("# tests");
  expect(confirm).toHaveBeenCalledOnce();
});
