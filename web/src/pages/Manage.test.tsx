import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Manage, Settings } from "./Manage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("saves execution settings and clears typed secrets after success", async () => {
  const config = {
    raw_threshold: 10,
    wiki_threshold: 5,
    raw_auto: false,
    wiki_auto: false,
    executor: "codex",
    api_provider: "chat_completions",
    api_url: "https://example.test/v1",
    api_model: "fixture",
    api_key_configured: true,
    codex_home: "/tmp/codex",
    install_directory: "/tmp/codex/skills",
    model: null,
    max_tokens: 384000,
    context_window: 1000000,
    timeout_seconds: 600,
    poll_seconds: 10,
    auto_start: true,
    codex_command: ["codex", "app-server"],
  };
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "PUT") {
      const {
        api_key: _key,
        clear_api_key: _clear,
        ...saved
      } = JSON.parse(options.body as string);
      return Response.json({ ...saved, api_key_configured: true });
    }
    return Response.json(config);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Settings />);
  const executor = await screen.findByLabelText("执行方式");
  expect(screen.queryByLabelText(/API key/)).toBeNull();
  fireEvent.change(executor, { target: { value: "api" } });
  fireEvent.change(screen.getByLabelText("最大输出（tokens）"), { target: { value: "500000" } });
  fireEvent.change(screen.getByLabelText("上下文窗口（tokens）"), { target: { value: "2000000" } });
  const key = await screen.findByLabelText(/API key/);
  expect((key as HTMLInputElement).value).toBe("");
  fireEvent.change(key, { target: { value: "test-key" } });
  fireEvent.click(screen.getByLabelText("自动 Raw → Wiki"));
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText("设置已保存");
  const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
  const write = calls.find(([, options]) => options?.method === "PUT")!;
  expect(JSON.parse(write[1].body as string)).toMatchObject({
    max_tokens: 500000,
    context_window: 2000000,
    api_key: "test-key",
    raw_auto: true,
  });
  expect(JSON.parse(write[1].body as string)).not.toHaveProperty(
    "api_key_configured",
  );
  await waitFor(() => expect((key as HTMLInputElement).value).toBe(""));
});

const queue = {
  pending: 2,
  batched: 0,
  waiting: 2,
  threshold: 10,
  reason: "below_threshold",
  job_id: null,
  job_project: null,
};
const project = {
  id: "alpha",
  name: "Alpha",
  path: "/projects/alpha",
  raw: queue,
  raw_count: 2,
  observation_count: 0,
  wiki_pages: 1,
  skills: [],
  updated_at: null,
  active_jobs: 0,
  failed_jobs: 0,
};
const snapshot = {
  initialized: true,
  root: "/tmp/wiki",
  captured_at: 0,
  cursor: 0,
  config: null,
  config_error: null,
  worker: { status: "online" as const },
  projects: [
    project,
    { ...project, id: "beta", name: "Beta", path: "/projects/beta" },
  ],
  totals: { projects: 2, skills: 0, active: 0, failed: 0 },
};

it("requires an explicit project and lets users filter project cards", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<Manage snapshot={snapshot} revision={0} refresh={() => {}} />);
  expect(
    screen.queryByRole("button", { name: "整理全部待处理记录" }),
  ).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("搜索项目"), {
    target: { value: "beta" },
  });
  expect(screen.queryByRole("heading", { name: "Alpha" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Beta" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Beta/ }).getAttribute("href")).toBe(
    "#/manage?project=beta",
  );
});

it("opens the requested workflow step and preserves project in step links", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [], next_offset: null })),
  );
  render(
    <Manage
      snapshot={snapshot}
      project="beta"
      layer="wiki"
      revision={0}
      refresh={() => {}}
    />,
  );
  expect(
    screen.getByRole("link", { name: /知识文档/ }).getAttribute("aria-current"),
  ).toBe("step");
  expect(
    screen.getByRole("link", { name: /原始记录/ }).getAttribute("href"),
  ).toBe("#/manage?project=beta&layer=raw");
  expect(await screen.findByText("还没有知识文档")).toBeTruthy();
});

it("keeps failed raw requests distinct from an empty record list", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ detail: "读取失败，请重试" }, { status: 500 }),
    ),
  );
  render(
    <Manage
      snapshot={snapshot}
      project="alpha"
      revision={0}
      refresh={() => {}}
    />,
  );
  expect(await screen.findByText("读取失败，请重试")).toBeTruthy();
  expect(screen.queryByText(/暂无轨迹/)).toBeNull();
});

it("selects only unprocessed records on the current page and submits their IDs", async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.includes("/turns"))
      return Response.json({
        items: [
          {
            id: 7,
            session: "session",
            turn_key: "turn",
            ended: 1,
            records: 2,
            pending: 1,
          },
        ],
        next_offset: null,
      });
    if (url.includes("/traces"))
      return Response.json({
        items: [
          {
            id: 1,
            text: "first",
            bytes_base64: "",
            event_type: "message",
            parse_error: null,
            consumed_by: null,
          },
          {
            id: 2,
            text: "second",
            bytes_base64: "",
            event_type: "message",
            parse_error: null,
            consumed_by: "old",
          },
        ],
        next_offset: null,
      });
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <Manage
      snapshot={snapshot}
      project="beta"
      revision={0}
      refresh={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: /对话轮次 #7/ }));
  fireEvent.click(
    await screen.findByRole("button", { name: "选中本页待处理记录" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "整理选定记录（1）" }));
  await screen.findByText("选定记录已入队");
  const writes = (
    fetcher.mock.calls as unknown as Array<[string, RequestInit]>
  ).filter(([, options]) => options?.method === "POST");
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe("/api/projects/beta/convert");
  expect(JSON.parse(writes[0][1].body as string)).toEqual({
    stage: "raw",
    inputs: [1],
  });
});

it("saves a new wiki and uses the saved digest for subsequent edits", async () => {
  let savedBody = "# Build";
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "PUT") {
      savedBody = JSON.parse(options.body as string).pages[0].body;
      return Response.json({});
    }
    if (url.endsWith("/wiki/build"))
      return Response.json({
        name: "build",
        body: savedBody,
        digest: "saved",
        changes: { items: [], next_offset: null },
      });
    return Response.json({ items: [], next_offset: null });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <Manage
      snapshot={snapshot}
      project="alpha"
      layer="wiki"
      revision={0}
      refresh={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  fireEvent.change(screen.getByLabelText("页面名称"), {
    target: { value: "build" },
  });
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), {
    target: { value: "# Build" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("Wiki 已保存");
  expect((screen.getByLabelText("页面名称") as HTMLInputElement).disabled).toBe(
    true,
  );
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), {
    target: { value: "# Updated" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("Wiki 已保存");
  const writes = fetcher.mock.calls.filter(
    ([, options]) => options?.method === "PUT",
  );
  expect(JSON.parse(writes[1][1]!.body as string)).toEqual({
    pages: [{ name: "build", body: "# Updated" }],
    expected: { build: "saved" },
  });
});

function wikiFixture() {
  const pages: Record<string, { body: string; digest: string }> = {
    build: { body: "# Build", digest: "original" },
    tests: { body: "# Tests", digest: "tests" },
  };
  const control = {
    failWrite: false,
    failRead: false,
    pending: null as Promise<void> | null,
  };
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "PUT") {
      if (control.pending) await control.pending;
      if (control.failWrite)
        return Response.json({ detail: "保存失败" }, { status: 500 });
      const { name, body } = JSON.parse(options.body as string).pages[0];
      pages[name] = { body, digest: "saved" };
      return Response.json({});
    }
    if (url.includes("/wiki?"))
      return Response.json({
        items: Object.keys(pages).map((name) => ({ name, excerpt: name })),
        next_offset: null,
      });
    if (control.failRead)
      return Response.json({ detail: "读取失败" }, { status: 500 });
    const name = url.split("/").at(-1)!;
    return Response.json({
      name, ...pages[name], changes: { items: [], next_offset: null },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return { pages, control, fetcher };
}

function wikiView(revision = 0) {
  return <Manage snapshot={snapshot} project="alpha" layer="wiki"
    revision={revision} refresh={() => {}} />;
}

function unloadIsBlocked() {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

it("keeps edited text when document changes or a new page are cancelled", async () => {
  const { fetcher, pages } = wikiFixture();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(wikiView());
  fireEvent.click(await screen.findByRole("button", { name: "打开 Wiki build" }));
  const body = await screen.findByLabelText("正文（Markdown）") as HTMLTextAreaElement;
  fireEvent.change(body, { target: { value: "Unsaved build" } });
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki build" }));
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki tests" }));
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(body.value).toBe("Unsaved build");
  expect(unloadIsBlocked()).toBe(true);
  expect(pages.build.body).toBe("# Build");
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki tests" }));
  await waitFor(() => expect((screen.getByLabelText("正文（Markdown）") as HTMLTextAreaElement).value).toBe("# Tests"));
  expect(unloadIsBlocked()).toBe(false);
});

it("guards a new page name and resets a second new page only after confirmation", async () => {
  wikiFixture();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const view = render(wikiView());
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect(unloadIsBlocked()).toBe(false);
  fireEvent.change(screen.getByLabelText("页面名称"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect((screen.getByLabelText("页面名称") as HTMLInputElement).value).toBe("draft");
  expect(unloadIsBlocked()).toBe(true);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect((screen.getByLabelText("页面名称") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("正文（Markdown）") as HTMLTextAreaElement).value).toBe("");
  expect(unloadIsBlocked()).toBe(false);
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), { target: { value: "Body only" } });
  expect(unloadIsBlocked()).toBe(true);
  view.unmount();
  expect(unloadIsBlocked()).toBe(false);
});

it("clears the guard when edits are reverted or a save succeeds", async () => {
  wikiFixture();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(wikiView());
  fireEvent.click(await screen.findByRole("button", { name: "打开 Wiki build" }));
  const body = await screen.findByLabelText("正文（Markdown）");
  expect(unloadIsBlocked()).toBe(false);
  fireEvent.change(body, { target: { value: "Modified" } });
  fireEvent.change(body, { target: { value: "# Build" } });
  expect(unloadIsBlocked()).toBe(false);
  fireEvent.change(body, { target: { value: "Saved content" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("Wiki 已保存");
  expect(unloadIsBlocked()).toBe(false);
  expect(screen.queryByText("有未保存的修改")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect(confirm).not.toHaveBeenCalled();
});

it("preserves input and the original expected digest after a failed save and refresh", async () => {
  const { control, pages, fetcher } = wikiFixture();
  control.failWrite = true;
  vi.spyOn(window, "confirm").mockReturnValue(false);
  const view = render(wikiView());
  fireEvent.click(await screen.findByRole("button", { name: "打开 Wiki build" }));
  const body = await screen.findByLabelText("正文（Markdown）") as HTMLTextAreaElement;
  fireEvent.change(body, { target: { value: "Keep this input" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("保存失败");
  expect(pages.build.body).toBe("# Build");
  pages.build = { body: "Someone else's update", digest: "remote" };
  const readsBefore = fetcher.mock.calls.length;
  view.rerender(wikiView(1));
  await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(readsBefore));
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki tests" }));
  expect(body.value).toBe("Keep this input");
  expect(unloadIsBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("保存失败");
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "PUT");
  expect(writes).toHaveLength(2);
  expect(JSON.parse(writes[1][1]!.body as string).expected).toEqual({ build: "original" });
});

it("keeps edits made while a save is pending dirty after the submitted body is saved", async () => {
  const { control, pages } = wikiFixture();
  let finish!: () => void;
  control.pending = new Promise<void>((resolve) => { finish = resolve; });
  render(wikiView());
  fireEvent.click(await screen.findByRole("button", { name: "打开 Wiki build" }));
  const body = await screen.findByLabelText("正文（Markdown）") as HTMLTextAreaElement;
  fireEvent.change(body, { target: { value: "Submitted" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  fireEvent.change(body, { target: { value: "Typed later" } });
  finish();
  await screen.findByText("Wiki 已保存");
  expect(pages.build.body).toBe("Submitted");
  expect(body.value).toBe("Typed later");
  expect(unloadIsBlocked()).toBe(true);
});

it("retains a new draft when reading back a successful write fails", async () => {
  const { control, pages } = wikiFixture();
  control.failRead = true;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(wikiView());
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  fireEvent.change(screen.getByLabelText("页面名称"), { target: { value: "draft" } });
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), { target: { value: "Draft content" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Wiki" }));
  await screen.findByText("读取失败");
  expect(pages.draft.body).toBe("Draft content");
  fireEvent.click(screen.getByRole("button", { name: /新建知识页/ }));
  expect(confirm).toHaveBeenCalledOnce();
  expect((screen.getByLabelText("正文（Markdown）") as HTMLTextAreaElement).value).toBe("Draft content");
  expect(unloadIsBlocked()).toBe(true);
});

it("opens a newly added project only after it appears in the snapshot", async () => {
  window.history.replaceState(null, "", "#/manage");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ id: "new-project" })),
  );
  const refresh = vi.fn();
  const view = render(
    <Manage snapshot={snapshot} revision={0} refresh={refresh} />,
  );
  fireEvent.change(screen.getByLabelText("项目目录"), {
    target: { value: " /projects/new " },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "添加并打开" }).closest("form")!,
  );
  await screen.findByText("项目已添加");
  expect(refresh).toHaveBeenCalledOnce();
  expect(window.location.hash).toBe("#/manage");
  view.rerender(
    <Manage
      snapshot={{
        ...snapshot,
        projects: [...snapshot.projects, { ...project, id: "new-project" }],
      }}
      revision={1}
      refresh={refresh}
    />,
  );
  await waitFor(() =>
    expect(window.location.hash).toBe(
      "#/manage?project=new-project&layer=wiki",
    ),
  );
  window.history.replaceState(null, "", "#/manage");
});

it("selects Wiki across pages and starts generation from a saved detail", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/preview")) {
      const pages = JSON.parse(options!.body as string).pages as string[];
      return Response.json({
        pages: pages.map((name) => ({ name, body: name, digest: name })),
        suggested_name: "wiki-guide",
        candidates: [],
        unavailable: [],
      });
    }
    if (url.includes("/wiki?offset=20"))
      return Response.json({
        items: [{ name: "tests", excerpt: "Testing" }],
        next_offset: null,
      });
    if (url.includes("/wiki?"))
      return Response.json({
        items: [{ name: "build", excerpt: "Building" }],
        next_offset: 20,
      });
    return Response.json({
      name: "tests",
      body: "Testing",
      digest: "tests",
      changes: { items: [], next_offset: null },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <Manage
      snapshot={snapshot}
      project="alpha"
      layer="wiki"
      revision={0}
      refresh={() => {}}
    />,
  );
  fireEvent.click(await screen.findByLabelText("选择 Wiki build"));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByLabelText("选择 Wiki tests"));
  expect((screen.getByRole("button", { name: "导出选定 Wiki（2）" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: "从选定 Wiki 生成 Skill（2）" }),
  );
  await screen.findByRole("button", { name: "新建并生成 Skill" });
  expect(
    fetcher.mock.calls.some(
      ([url, options]) =>
        url.endsWith("/preview") &&
        JSON.parse(options!.body as string).pages.join(",") === "build,tests",
    ),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "关闭生成面板" }));
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki tests" }));
  const generate = await screen.findByRole("button", {
    name: "从此 Wiki 生成 Skill",
  });
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), {
    target: { value: "Unsaved edits" },
  });
  expect((generate as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("正文（Markdown）"), {
    target: { value: "Testing" },
  });
  fireEvent.click(generate);
  await screen.findByRole("button", { name: "新建并生成 Skill" });
  expect(
    fetcher.mock.calls.some(
      ([url, options]) =>
        url.endsWith("/preview") &&
        JSON.parse(options!.body as string).pages.join(",") === "tests",
    ),
  ).toBe(true);
});

it("keeps generation on Wiki and global operations in settings", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        disk_text: "# Saved Skill",
        versions: { items: [], next_offset: null },
      }),
    ),
  );
  render(
    <Manage
      snapshot={{
        ...snapshot,
        projects: [
          {
            ...project,
            skills: [
              {
                id: "skill",
                name: "build-guide",
                path: "/tmp/skills/build-guide",
                owned: 1,
                enabled: true,
                project_count: 1,
                wiki: queue,
                version_count: 1,
              },
            ],
          },
        ],
      }}
      project="alpha"
      layer="skill"
      revision={0}
      refresh={() => {}}
    />,
  );
  expect(
    await screen.findByRole("button", { name: "安装到 Codex" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "生成 Skill" })).toBeNull();
  expect(screen.queryByText("选择待处理 Wiki 版本")).toBeNull();
  expect(screen.queryByRole("button", { name: "导入已有历史" })).toBeNull();
  expect(screen.queryByRole("button", { name: "启动后台" })).toBeNull();
});

it("searches Wiki with literal text, resets pages, and keeps selection and the open document", async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/wiki/build"))
      return Response.json({
        name: "build", body: "Saved body", digest: "saved",
        changes: { items: [], next_offset: null },
      });
    const params = new URL(url, "http://localhost").searchParams;
    const q = params.get("q");
    if (q === "missing") return Response.json({ items: [], next_offset: null });
    return Response.json({
      items: [{ name: params.get("offset") === "20" ? "later" : "build", excerpt: q || "All Wiki" }],
      next_offset: params.get("offset") === "20" ? null : 20,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Manage snapshot={snapshot} project="alpha" layer="wiki" revision={0} refresh={() => {}} />);
  fireEvent.click(await screen.findByLabelText("选择 Wiki build"));
  fireEvent.click(screen.getByRole("button", { name: "打开 Wiki build" }));
  await screen.findByDisplayValue("Saved body");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "打开 Wiki later" });
  const input = screen.getByRole("searchbox", { name: "搜索 Wiki" });
  const term = "中文 %_ &?+";
  fireEvent.change(input, { target: { value: term } });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByRole("button", { name: "打开 Wiki build" });
  expect(screen.getByText("第 1 页")).toBeTruthy();
  expect((screen.getByLabelText("选择 Wiki build") as HTMLInputElement).checked).toBe(true);
  expect(screen.getByDisplayValue("Saved body")).toBeTruthy();
  const lastParams = () => new URL(fetcher.mock.calls.at(-1)![0], "http://localhost").searchParams;
  expect(lastParams().get("q")).toBe(term);
  expect(lastParams().get("offset")).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "打开 Wiki later" });
  expect(lastParams().get("q")).toBe(term);
  expect(lastParams().get("offset")).toBe("20");
  fireEvent.change(input, { target: { value: "missing" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await screen.findByText("没有匹配的 Wiki");
  expect(screen.getByText("第 1 页")).toBeTruthy();
  expect(screen.queryByText("还没有知识文档")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "清空搜索" }));
  await screen.findByRole("button", { name: "打开 Wiki build" });
  expect(lastParams().has("q")).toBe(false);
  expect(lastParams().get("offset")).toBe("0");
  expect((input as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("选择 Wiki build") as HTMLInputElement).checked).toBe(true);
});

it("shows a failed Wiki search as an error rather than no matches", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    url.includes("q=")
      ? Response.json({ detail: "搜索读取失败" }, { status: 503 })
      : Response.json({ items: [], next_offset: null }),
  ));
  render(<Manage snapshot={snapshot} project="alpha" layer="wiki" revision={0} refresh={() => {}} />);
  await screen.findByText("还没有知识文档");
  fireEvent.change(screen.getByLabelText("搜索 Wiki"), { target: { value: "query" } });
  fireEvent.submit(screen.getByRole("search"));
  expect(await screen.findByText("搜索读取失败")).toBeTruthy();
  expect(screen.queryByText("没有匹配的 Wiki")).toBeNull();
  expect(screen.queryByText("还没有知识文档")).toBeNull();
});
