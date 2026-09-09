import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";
import { useLiveSnapshot } from "../api";
import { href, routeFromHash, type Route } from "../routing";
import type {
  Project,
  Queue,
  Skill,
  SkillDetail,
  Snapshot,
  Version,
} from "../types";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  useLiveSnapshot: vi.fn(),
}));

const queue: Queue = {
  pending: 0,
  batched: 0,
  waiting: 0,
  threshold: 5,
  reason: "below_threshold",
  job_id: null,
  job_project: null,
};
const shared: Skill = {
  id: "shared",
  name: "Build Guide",
  path: "/skills/build-guide",
  owned: 1,
  enabled: true,
  project_count: 2,
  wiki: queue,
  version_count: 2,
};
const external: Skill = {
  ...shared,
  id: "external",
  name: "Deploy Notes",
  path: "/external/Runbook/Deploy",
  owned: 0,
  enabled: false,
  project_count: 1,
};
const pending: Skill = {
  ...external,
  id: "pending",
  name: "部署 ?&+#",
  path: "/external/pending",
  enabled: null,
};
const archived: Skill = {
  ...shared,
  id: "archived",
  name: "Archive",
  path: "/skills/archive",
  enabled: false,
  project_count: 1,
};
const alpha: Project = {
  id: "alpha",
  name: "Alpha",
  path: "/projects/alpha",
  raw: queue,
  raw_count: 0,
  observation_count: 0,
  wiki_pages: 1,
  skills: [shared, external, pending],
  updated_at: null,
  active_jobs: 0,
  failed_jobs: 0,
};
const snapshot: Snapshot = {
  initialized: true,
  root: "/isolated/wiki",
  captured_at: 0,
  cursor: 0,
  config: null,
  config_error: null,
  worker: { status: "online" },
  projects: [
    alpha,
    { ...alpha, id: "beta", name: "Beta", skills: [{ ...shared }, archived] },
    { ...alpha, id: "empty", name: "Empty", skills: [] },
  ],
  totals: { projects: 3, skills: 4, active: 0, failed: 0 },
};

function setSnapshot(data = snapshot) {
  vi.mocked(useLiveSnapshot).mockReturnValue({
    key: "/api/snapshot",
    data,
    error: null,
    loading: false,
    connection: "connected",
    revision: 0,
    refresh: vi.fn(),
  });
}

function openList(params: Omit<Route, "view"> = {}) {
  window.history.replaceState(null, "", href("skills", params));
  return render(<App />);
}

function names() {
  return [...document.querySelectorAll(".skill-card h3")].map(
    (node) => node.textContent,
  );
}

function count(matched: number, total: number) {
  expect(
    screen.getByText(`匹配 ${matched} 个 Skill · 当前项目范围共 ${total} 个`),
  ).toBeTruthy();
}

async function search(text: string) {
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索 Skill" }), {
    target: { value: text },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Skill 搜索与筛选" }));
  await waitFor(() => expect(routeFromHash().q || "").toBe(text.trim()));
}

beforeEach(() => {
  setSnapshot();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("Unexpected request");
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  window.history.replaceState(null, "", "#/skills");
});

it("deduplicates shared Skill IDs and counts the current project scope", async () => {
  openList();
  count(4, 4);
  expect(names()).toEqual([
    "Build Guide",
    "Deploy Notes",
    "部署 ?&+#",
    "Archive",
  ]);
  fireEvent.change(screen.getByLabelText("选择项目"), {
    target: { value: "beta" },
  });
  await waitFor(() => count(2, 2));
  expect(names()).toEqual(["Build Guide", "Archive"]);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  ["  BUILD  ", "Build Guide"],
  ["/EXTERNAL/runbook", "Deploy Notes"],
  ["部署 ?&+#", "部署 ?&+#"],
])(
  "searches names or paths and round-trips the URL: %s",
  async (keyword, expected) => {
    openList();
    await search(keyword);
    await waitFor(() => expect(names()).toEqual([expected]));
    count(1, 4);
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(
      keyword.trim(),
    );
  },
);

it.each([
  ["enabled", "", ["Build Guide"]],
  ["disabled", "", ["Deploy Notes", "Archive"]],
  ["unconfirmed", "", ["部署 ?&+#"]],
  ["", "owned", ["Build Guide", "Archive"]],
  ["", "external", ["Deploy Notes", "部署 ?&+#"]],
  ["disabled", "owned", ["Archive"]],
])(
  "intersects management status %s and source %s",
  (status, source, expected) => {
    openList({ status, source });
    expect(names()).toEqual(expected);
    count(expected.length, 4);
  },
);

it("preserves applied filters on project changes and clears only filters", async () => {
  openList({
    project: "alpha",
    q: "build",
    status: "enabled",
    source: "owned",
  });
  count(1, 3);
  fireEvent.change(screen.getByLabelText("选择项目"), {
    target: { value: "beta" },
  });
  await waitFor(() => count(1, 2));
  expect(routeFromHash()).toMatchObject({
    project: "beta",
    q: "build",
    status: "enabled",
    source: "owned",
  });
  fireEvent.click(screen.getByRole("button", { name: "清空筛选" }));
  await waitFor(() => count(2, 2));
  expect(routeFromHash()).toEqual({ view: "skills", project: "beta" });
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("管理状态") as HTMLSelectElement).value).toBe(
    "",
  );
  expect((screen.getByLabelText("来源") as HTMLSelectElement).value).toBe("");
});

it("distinguishes no Skill from no matches, including a workspace without projects", async () => {
  const view = openList({ project: "empty", q: "missing" });
  count(0, 0);
  expect(screen.getByText("尚无 Skill")).toBeTruthy();
  expect(screen.queryByText("没有匹配结果")).toBeNull();
  fireEvent.change(screen.getByLabelText("选择项目"), {
    target: { value: "alpha" },
  });
  await screen.findByText("没有匹配结果");
  count(0, 3);
  expect(screen.queryByText("尚无 Skill")).toBeNull();
  view.unmount();
  setSnapshot({
    ...snapshot,
    projects: [],
    totals: { ...snapshot.totals, projects: 0, skills: 0 },
  });
  openList();
  expect(screen.getByText("尚无 Skill")).toBeTruthy();
  count(0, 0);
});

it("treats unknown URL filter values as all and allows clearing them", async () => {
  openList({ status: "unknown", source: "unknown" });
  count(4, 4);
  expect((screen.getByLabelText("管理状态") as HTMLSelectElement).value).toBe(
    "",
  );
  expect((screen.getByLabelText("来源") as HTMLSelectElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "清空筛选" }));
  await waitFor(() => expect(window.location.hash).toBe("#/skills"));
});

it("restores submitted search and selects on remount and browser back/forward", async () => {
  const view = openList();
  await search("guide");
  await waitFor(() => count(1, 4));
  fireEvent.change(screen.getByLabelText("来源"), {
    target: { value: "external" },
  });
  await screen.findByText("没有匹配结果");
  const filteredURL = window.location.hash;
  view.unmount();
  render(<App />);
  count(0, 4);
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(
    "guide",
  );
  expect((screen.getByLabelText("来源") as HTMLSelectElement).value).toBe(
    "external",
  );
  act(() => window.history.back());
  await waitFor(() => count(1, 4));
  expect((screen.getByLabelText("来源") as HTMLSelectElement).value).toBe("");
  act(() => window.history.forward());
  await waitFor(() => count(0, 4));
  expect(window.location.hash).toBe(filteredURL);
  expect((screen.getByLabelText("来源") as HTMLSelectElement).value).toBe(
    "external",
  );
});

const version: Version = {
  id: "v1",
  job_id: "job-1",
  state: "applied",
  created: 1,
  diff_size: 4,
  skill: "shared",
  diff: "+updated",
  source_job: null,
  before: { skill_md: "# Before", files: [] },
  after: {
    skill_md: "# After",
    files: [{ path: "SKILL.md", type: "file", mode: "644", bytes: 7 }],
  },
};
const detail: SkillDetail = {
  ...shared,
  projects: [alpha],
  disk_text: "# Current body",
  published_text: "# Current body",
  disk_error: null,
  versions: { items: [version, { ...version, id: "v2" }], next_offset: null },
};

it("keeps filters through detail and versions while preserving download and rollback", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") return Response.json({});
    if (url.includes("/versions/"))
      return Response.json({ ...version, id: url.split("/").at(-1) });
    return Response.json(detail);
  });
  vi.stubGlobal("fetch", fetcher);
  openList({
    project: "alpha",
    q: "build",
    status: "enabled",
    source: "owned",
  });
  const listURL = window.location.hash;
  fireEvent.click(screen.getByRole("link", { name: /Build Guide/ }));
  await screen.findByText("Current body");
  expect(
    screen.getByRole("link", { name: "全部 Skill" }).getAttribute("href"),
  ).toBe(listURL);
  fireEvent.click(screen.getByRole("button", { name: /v2/ }));
  await waitFor(() =>
    expect(routeFromHash()).toMatchObject({
      q: "build",
      status: "enabled",
      source: "owned",
      skill: "shared",
      version: "v2",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "快照文件" }));
  const download = await screen.findByRole("link", {
    name: "下载修改后 SKILL.md",
  });
  expect(download.getAttribute("href")).toBe(
    "/api/skills/shared/versions/v2/file?side=after&name=SKILL.md",
  );
  fireEvent.click(screen.getByRole("button", { name: "恢复选定历史版本" }));
  await screen.findByText("已恢复选定版本的完整目录");
  const write = fetcher.mock.calls.find(
    ([, options]) => options?.method === "POST",
  )!;
  expect(write[0]).toBe("/api/skills/shared/rollback");
  expect(JSON.parse(write[1]!.body as string)).toEqual({
    version_id: "v2",
    side: "after",
  });
  fireEvent.click(screen.getByRole("link", { name: "全部 Skill" }));
  await waitFor(() => count(1, 3));
  expect(window.location.hash).toBe(listURL);
});

it("retains a filtered return link when the detail request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ detail: "读取失败" }, { status: 500 })),
  );
  openList({ q: "build", status: "enabled", skill: "shared" });
  await screen.findByText("读取失败");
  fireEvent.click(screen.getByRole("link", { name: "全部 Skill" }));
  await waitFor(() => count(1, 4));
  expect(routeFromHash()).toEqual({
    view: "skills",
    q: "build",
    status: "enabled",
  });
});
