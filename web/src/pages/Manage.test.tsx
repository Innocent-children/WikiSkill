import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Settings } from "./Manage";

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
    input_budget: 200000,
    timeout_seconds: 600,
    poll_seconds: 10,
    auto_start: true,
    codex_command: ["codex", "app-server"],
  };
  const fetcher = vi.fn(async () => Response.json(config));
  vi.stubGlobal("fetch", fetcher);
  render(<Settings />);
  const key = await screen.findByLabelText(/API key/);
  expect((key as HTMLInputElement).value).toBe("");
  fireEvent.change(key, { target: { value: "test-key" } });
  fireEvent.click(screen.getByLabelText("自动 Raw → Wiki"));
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await screen.findByText("设置已保存");
  const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
  const write = calls.find(([, options]) => options?.method === "PUT")!;
  expect(JSON.parse(write[1].body as string)).toMatchObject({
    api_key: "test-key",
    raw_auto: true,
  });
  expect(JSON.parse(write[1].body as string)).not.toHaveProperty(
    "api_key_configured",
  );
  await waitFor(() => expect((key as HTMLInputElement).value).toBe(""));
});
