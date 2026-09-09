import { useEffect, useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { KnowledgeList } from "./Knowledge";
import { routeFromHash } from "../routing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "#/knowledge");
});

function RoutedList() {
  const [route, setRoute] = useState(routeFromHash);
  useEffect(() => {
    const update = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return <KnowledgeList route={route} revision={0} />;
}

it("stores Wiki search in the URL, preserves it across pages and resets when changed or cleared", async () => {
  window.history.replaceState(null, "", "#/knowledge?project=alpha&layer=wiki&offset=20");
  const fetcher = vi.fn(async (url: string) => {
    const params = new URL(url, "http://localhost").searchParams;
    if (params.get("q") === "missing") return Response.json({ items: [], next_offset: null });
    return Response.json({
      items: [{ name: params.get("offset") === "20" ? "later" : "first", excerpt: "Current body", revisions: 1, characters: 12 }],
      next_offset: params.get("offset") === "20" ? null : 20,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<RoutedList />);
  await screen.findByRole("heading", { name: "later" });
  const term = "中文 %_ &?+";
  fireEvent.change(screen.getByLabelText("搜索 Wiki"), { target: { value: term } });
  fireEvent.submit(screen.getByRole("search"));
  await screen.findByRole("heading", { name: "first" });
  expect(routeFromHash()).toMatchObject({ project: "alpha", layer: "wiki", q: term });
  expect(routeFromHash().offset).toBeUndefined();
  expect(screen.getByText("第 1 页")).toBeTruthy();
  const lastParams = () => new URL(fetcher.mock.calls.at(-1)![0], "http://localhost").searchParams;
  expect(lastParams().get("q")).toBe(term);
  expect(lastParams().get("offset")).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("heading", { name: "later" });
  expect(routeFromHash()).toMatchObject({ q: term, offset: "20" });
  expect(lastParams().get("q")).toBe(term);
  expect((screen.getByLabelText("搜索 Wiki") as HTMLInputElement).value).toBe(term);
  fireEvent.change(screen.getByLabelText("搜索 Wiki"), { target: { value: "missing" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await screen.findByText("没有匹配的 Wiki");
  expect(screen.getByText("第 1 页")).toBeTruthy();
  expect(screen.queryByText("还没有知识页面")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "清空搜索" }));
  await screen.findByRole("heading", { name: "first" });
  expect(routeFromHash().q).toBeUndefined();
  expect(routeFromHash().offset).toBeUndefined();
  expect(lastParams().has("q")).toBe(false);
  expect((screen.getByLabelText("搜索 Wiki") as HTMLInputElement).value).toBe("");

  window.location.hash = "#/knowledge?project=alpha&layer=wiki&q=restored&offset=20";
  await screen.findByRole("heading", { name: "later" });
  await waitFor(() => expect((screen.getByLabelText("搜索 Wiki") as HTMLInputElement).value).toBe("restored"));
});

it("keeps failed search distinct from no matches and leaves the Raw list unfiltered", async () => {
  const fetcher = vi.fn(async (url: string) => url.includes("/wiki")
    ? Response.json({ detail: "搜索读取失败" }, { status: 503 })
    : Response.json({ items: [], next_offset: null }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(<KnowledgeList route={{ view: "knowledge", project: "alpha", layer: "wiki", q: "needle" }} revision={0} />);
  expect(await screen.findByText("搜索读取失败")).toBeTruthy();
  expect(screen.queryByText("没有匹配的 Wiki")).toBeNull();
  expect(screen.queryByText("还没有知识页面")).toBeNull();
  view.rerender(<KnowledgeList route={{ view: "knowledge", project: "alpha", layer: "raw", q: "needle" }} revision={0} />);
  await screen.findByText("还没有旧经验摘要");
  expect(screen.queryByRole("searchbox")).toBeNull();
  expect(fetcher.mock.calls.at(-1)![0]).toBe("/api/projects/alpha/raw?offset=0");
});
