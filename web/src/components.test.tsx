import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Markdown, QueueMeter } from "./components";

afterEach(cleanup);

it("renders collected Markdown without active HTML or remote image requests", () => {
  const { container } = render(
    <Markdown
      text={
        '# Lesson\n\n<script>alert(1)</script>\n\n<img src="https://example.test/private" onerror="alert(1)">\n\n![reference](https://example.test/pixel)\n\n[unsafe](javascript:alert%281%29)'
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "Lesson" })).toBeTruthy();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(
    screen.getByText("unsafe").closest("a")?.getAttribute("href"),
  ).toBeFalsy();
});

it("renders knowledge tables as readable table cells", () => {
  render(
    <Markdown
      text={"| 环境 | 命令 |\n| --- | --- |\n| 本地 | npm run build |"}
    />,
  );
  expect(screen.getByRole("columnheader", { name: "环境" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "npm run build" })).toBeTruthy();
});

it("distinguishes frozen batch inputs from later accumulated observations", () => {
  render(
    <QueueMeter
      project="project"
      label="Raw → Wiki"
      queue={{
        pending: 12,
        batched: 10,
        waiting: 2,
        threshold: 10,
        reason: "processing",
        job_id: "batch",
        job_project: "project",
      }}
    />,
  );
  expect(screen.getByText("本批 10 条 · 后续 2 条")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "查看批次" }).getAttribute("href"),
  ).toContain("job=batch");
  expect(screen.queryByText("还差 0 条达到阈值")).toBeNull();
});
