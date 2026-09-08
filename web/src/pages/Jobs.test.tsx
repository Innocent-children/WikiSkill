import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JobDetail } from "./Jobs";
import type { JobDetail as JobData } from "../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const completed: JobData = {
  id: "batch",
  project: "project",
  project_name: "Project",
  stage: "skill",
  skill: "skill",
  skill_path: "/skills/project",
  state: "done",
  created: 100,
  thread_id: "thread",
  error: null,
  report_sent: 0,
  report_error: "report timed out",
  input_count: 5,
  outcome: "changed",
  summary: "Added project instructions",
  publication_state: "applied",
  version_id: "version",
  phase: "job.done",
  updated_at: 150,
  retry_count: 0,
  running_confirmed: false,
  last_activity: 150,
  content_status: "published",
  report_status: "failed",
  inputs: [1, 2, 3, 4, 5],
  result: null,
  report: { outcome: "changed" },
  previous_report: null,
  started_at: 110,
  finished_at: 150,
};

function show(job: JobData) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(
        url.endsWith("/batch") ? job : { items: [], next_offset: null },
      ),
    ),
  );
  return render(<JobDetail id="batch" revision={0} now={180} />);
}

it("shows a published skill independently of a failed conversation report", async () => {
  show(completed);
  expect(await screen.findByText("Skill 已发布")).toBeTruthy();
  expect(screen.getByText("发送失败")).toBeTruthy();
  expect(screen.queryByText("处理失败")).toBeNull();
  expect(
    screen.getByRole("link", { name: "version" }).getAttribute("href"),
  ).toContain("version=version");
});

it("shows no-change completion and consumed inputs without a publication", async () => {
  show({
    ...completed,
    stage: "raw",
    skill: null,
    version_id: null,
    publication_state: null,
    outcome: "no_change",
    content_status: "no_change",
    report_status: "sent",
    report_sent: 1,
    report_error: null,
    report: {
      outcome: "no_change",
      pages: [{ name: "build", body: "Existing knowledge" }],
    },
  });
  expect(await screen.findByText("无新增版本")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "结果与报告" }));
  expect(
    screen.getByText("本批输入已消费，没有产生新的内容版本。"),
  ).toBeTruthy();
  expect(screen.queryByRole("link", { name: "查看版本差异" })).toBeNull();
  expect(
    screen.getByRole("link", { name: "build" }).getAttribute("href"),
  ).toContain("wiki=build");
});

it("labels the previous report separately while the current retry is pending", async () => {
  show({
    ...completed,
    state: "queued",
    phase: "job.retried",
    content_status: "pending",
    report_status: "pending",
    report_sent: 0,
    report_error: null,
    retry_count: 1,
    report: null,
    previous_report: { outcome: "failed" },
  });
  await screen.findByText("尚未发送");
  fireEvent.click(screen.getByRole("button", { name: "结果与报告" }));
  expect(screen.getByText("报告尚未生成")).toBeTruthy();
  expect(screen.getByText("上次尝试的报告")).toBeTruthy();
});

it.each(["generating", "queued"])(
  "does not claim a %s job is still running without a current heartbeat",
  async (state) => {
    show({
      ...completed,
      state,
      phase: state === "queued" ? "codex.connecting" : "job.generating",
      content_status: "pending",
      report_status: "pending",
      report_error: null,
      report: null,
      finished_at: null,
      running_confirmed: false,
    });
    expect(await screen.findByText("状态待确认")).toBeTruthy();
    expect(
      screen.getByText("这是最后记录的阶段；尚未确认 worker 仍在执行。"),
    ).toBeTruthy();
    expect(screen.queryByText("正在执行")).toBeNull();
  },
);
