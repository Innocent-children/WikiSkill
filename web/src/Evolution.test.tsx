import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SessionPicker } from "./SessionPicker";
import { SkillEvolution } from "./SkillEvolution";
import { Settings } from "./pages/Manage";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("browses without importing and submits only checked sessions for analysis", async () => {
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) =>
    options?.method === "POST"
      ? Response.json({ jobs: [{ job_id: "job" }] })
      : Response.json({
          items: [
            {
              id: "a",
              session: "a",
              title: "Fix build",
              cwd: "/project",
              bytes: 100,
              modified: 1,
            },
            {
              id: "b",
              session: "b",
              title: "Other discussion",
              cwd: "/other",
              bytes: 100,
              modified: 1,
            },
          ],
          next_offset: null,
        }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<SessionPicker refresh={() => {}} />);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("选择本机会话"));
  await screen.findByText("Fix build");
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(
    true,
  );
  fireEvent.click(screen.getAllByRole("checkbox")[0]);
  fireEvent.click(screen.getByText("提取选定会话的 Wiki"));
  await screen.findByText("查看本次分析 →");
  const posted = fetcher.mock.calls.filter(([, o]) => o?.method === "POST");
  expect(posted).toHaveLength(1);
  expect(JSON.parse(posted[0][1]!.body as string)).toEqual({
    ids: ["a"],
    analyze: true,
  });
});

it("saves negative feedback for the chosen version without generating or rolling back", async () => {
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) =>
    options?.method === "POST"
      ? Response.json({ id: 1 })
      : Response.json({
          sources: [
            { project: "p", name: "build", change_id: 2, needs_update: true },
          ],
          feedback: [],
        }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<SkillEvolution skill="s" version="v" revision={0} />);
  await screen.findByText(/Wiki 已更新/);
  fireEvent.change(screen.getByLabelText("具体结果或原因"), {
    target: { value: "Command fails on the target environment" },
  });
  fireEvent.click(screen.getByText("保存反馈"));
  await screen.findByText("反馈已保存，下次生成会参考");
  const posted = fetcher.mock.calls.filter(([, o]) => o?.method === "POST");
  expect(posted).toHaveLength(1);
  expect(posted[0][0]).toBe("/api/skills/s/feedback");
  expect(JSON.parse(posted[0][1]!.body as string)).toEqual({
    version_id: "v",
    kind: "problem",
    body: "Command fails on the target environment",
  });
});

it("selects full automatic mode and local Ollama with an explicit interval", async () => {
  const config = {
    capture_mode: "manual",
    analysis_interval_minutes: 60,


    executor: "codex",
    ollama_model: "",
    codex_command: ["codex", "app-server"],
    auto_start: false,
  };
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) =>
    Response.json(
      options?.method === "PUT" ? JSON.parse(options.body as string) : config,
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Settings />);
  await screen.findByLabelText("分析模式");
  fireEvent.change(screen.getByLabelText("分析模式"), {
    target: { value: "automatic" },
  });
  fireEvent.change(screen.getByLabelText("自动分析间隔（分钟）"), {
    target: { value: "30" },
  });
  fireEvent.change(screen.getByLabelText("执行方式"), {
    target: { value: "ollama" },
  });
  fireEvent.change(screen.getByLabelText("Ollama 模型名称"), {
    target: { value: "my-local-model" },
  });
  fireEvent.click(screen.getByText("保存设置"));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, o]) => o?.method === "PUT")).toBe(true),
  );
  const saved = fetcher.mock.calls.find(([, o]) => o?.method === "PUT")!;
  expect(JSON.parse(saved[1]!.body as string)).toMatchObject({
    capture_mode: "automatic",
    executor: "ollama",
    ollama_model: "my-local-model",
    analysis_interval_minutes: 30,
  });
  expect(screen.queryByLabelText(/API key/)).toBeNull();
});
