import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { System } from "./System";
import type { Snapshot } from "../types";

vi.mock("./Manage", () => ({ Settings: () => <div>模型设置表单</div> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const snapshot = {
  root: "/tmp/wiki data", config: null,
  worker: { status: "online", pid: 10, started: 1, heartbeat: 2, job_id: null, error: null },
} as Snapshot;

it("shows service readiness and usable onboarding links with the correct stop root", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({
    state: "running", url: "http://127.0.0.1:8765", collector: "running", worker: "running",
  })));
  render(<System snapshot={snapshot} now={10} />);
  expect(await screen.findByText(/控制面板：运行中/)).toBeTruthy();
  expect(screen.getByRole("link", { name: /进入知识工作台/ }).getAttribute("href")).toBe("#/manage");
  expect(screen.getByText("wikiskill --root '/tmp/wiki data' stop")).toBeTruthy();
  expect((screen.getByRole("button", { name: "后台运行中" }) as HTMLButtonElement).disabled).toBe(true);
});

it("allows repairing a missing collector even when the worker heartbeat is online", async () => {
  const fetcher = vi.fn(async () => Response.json({
    state: "degraded", url: "http://127.0.0.1:8765", collector: "stopped", worker: "running",
  }));
  vi.stubGlobal("fetch", fetcher);
  render(<System snapshot={snapshot} now={10} />);
  await screen.findByText(/部分后台已停止/);
  fireEvent.click(screen.getByRole("button", { name: "启动后台" }));
  await screen.findByText("后台已就绪，连接状态会自动更新。");
  expect(fetcher).toHaveBeenCalledWith("/api/start", expect.objectContaining({ method: "POST" }));
});
