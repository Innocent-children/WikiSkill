import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SkillGeneration } from "./SkillGeneration";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const preview = {
  pages: [
    { name: "build", body: "# Build", digest: "wiki-digest" },
    { name: "tests", body: "# Tests", digest: "tests-digest" },
  ],
  suggested_name: "build-combined",
  candidates: [
    {
      id: "existing",
      name: "build-guide",
      description: "Build with Maven",
      skill_md: "# Existing instructions",
      digest: "skill-digest",
      matched_terms: ["maven"],
      score: 1,
      projects: [{ id: "other", name: "Other project" }],
    },
  ],
  unavailable: [],
};

it("defaults to a new Skill even when a merge candidate matches and submits every selected page", async () => {
  const fetcher = vi.fn(async (url: string) =>
    Response.json(
      url.endsWith("/preview") ? preview : { job_id: "job", skill_id: "new" },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <SkillGeneration
      project="alpha"
      pages={["build", "tests"]}
      close={() => {}}
    />,
  );
  await screen.findByText("Build with Maven");
  expect(
    (
      screen.getByRole("radio", {
        name: "新建 Skill（默认）",
      }) as HTMLInputElement
    ).checked,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "新建并生成 Skill" }));
  await screen.findByRole("link", { name: "查看生成进度" });
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  expect(JSON.parse(calls[1][1].body as string)).toEqual({
    pages: ["build", "tests"],
    expected: { build: "wiki-digest", tests: "tests-digest" },
    name: "build-combined",
  });
});

it("lets users search and choose a cross-project merge while keeping the selected target visible", async () => {
  const fetcher = vi.fn(async (url: string) =>
    Response.json(
      url.endsWith("/preview")
        ? preview
        : { job_id: "merge", skill_id: "existing" },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <SkillGeneration
      project="alpha"
      pages={["build", "tests"]}
      close={() => {}}
    />,
  );
  await screen.findByText("Build with Maven");
  fireEvent.change(screen.getByLabelText("搜索已有 Skill"), {
    target: { value: "Maven" },
  });
  fireEvent.click(screen.getByRole("radio", { name: /合并到 build-guide/ }));
  expect(screen.getByText(/供这些项目共同使用/)).toBeTruthy();
  expect(screen.getByText("Existing instructions")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("搜索已有 Skill"), {
    target: { value: "nonexistent" },
  });
  expect(
    screen.getByRole("button", { name: "合并到 build-guide" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "合并到 build-guide" }));
  await screen.findByRole("link", { name: "查看生成进度" });
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  expect(JSON.parse(calls[1][1].body as string)).toEqual({
    pages: ["build", "tests"],
    expected: { build: "wiki-digest", tests: "tests-digest" },
    skill: "existing",
    skill_digest: "skill-digest",
  });
});

it("keeps preview errors and stale submission failures actionable", async () => {
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/preview"))
        return ++reads === 1
          ? Response.json({ detail: "读取失败" }, { status: 409 })
          : Response.json(preview);
      return Response.json(
        { detail: "Wiki 正文已变化，请重新打开生成面板" },
        { status: 409 },
      );
    }),
  );
  render(
    <SkillGeneration
      project="alpha"
      pages={["build", "tests"]}
      close={() => {}}
    />,
  );
  await screen.findByText("读取失败");
  expect(screen.queryByRole("button", { name: "新建并生成 Skill" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重新读取正文与候选" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "新建并生成 Skill" }),
  );
  await screen.findByText("Wiki 正文已变化，请重新打开生成面板");
  expect(screen.queryByRole("link", { name: "查看生成进度" })).toBeNull();
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "重新读取正文与候选",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
});
