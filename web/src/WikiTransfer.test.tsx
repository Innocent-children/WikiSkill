import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WikiTransfer } from "./WikiTransfer";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const preview = {
  preview_token: "signed-preview",
  counts: { added: 1, modified: 2, unchanged: 1 },
  pages: [
    {
      name: "build",
      status: "modified",
      before: "# Before",
      body: "# After",
      expected_digest: "before",
      diff: "-# Before\n+# After",
    },
    {
      name: "tests",
      status: "modified",
      before: "Old tests",
      body: "New tests",
      expected_digest: "tests",
      diff: "-Old tests\n+New tests",
    },
    {
      name: "new",
      status: "added",
      before: null,
      body: "# New",
      expected_digest: null,
      diff: "+# New",
    },
    {
      name: "same",
      status: "unchanged",
      before: "Same",
      body: "Same",
      expected_digest: "same",
      diff: "",
    },
  ],
};

async function openPreview() {
  fireEvent.change(screen.getByLabelText("导入 Wiki ZIP"), {
    target: {
      files: [
        new File(["zip-fixture"], "wiki.zip", { type: "application/zip" }),
      ],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览 / 重新预览 ZIP" }));
  await screen.findByText("预览完成，尚未写入任何 Wiki");
}

it("requires every modified page confirmation and submits the exact preview archive", async () => {
  const fetcher = vi.fn(async (url: string) =>
    Response.json(url.endsWith("/preview") ? preview : { new_changes: 3 }),
  );
  vi.stubGlobal("fetch", fetcher);
  const refresh = vi.fn();
  render(<WikiTransfer project="alpha" selected={[]} refresh={refresh} />);
  expect(screen.queryByRole("button", { name: "确认并导入" })).toBeNull();
  await openPreview();
  expect(screen.getByText("-# Before +# After")).toBeTruthy();
  const submit = screen.getByRole("button", {
    name: "确认并导入",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("确认修改 build"));
  expect(submit.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("确认修改 tests"));
  expect(submit.disabled).toBe(false);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(refresh).not.toHaveBeenCalled();
  fireEvent.click(submit);
  await screen.findByText("导入完成，记录 3 个 Wiki 版本");
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  const initial = JSON.parse(calls[0][1].body as string);
  expect(initial.archive_base64).toBe(btoa("zip-fixture"));
  expect(JSON.parse(calls[1][1].body as string)).toEqual({
    archive_base64: initial.archive_base64,
    preview_token: "signed-preview",
    overwrite: ["build", "tests"],
  });
  expect(refresh).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "确认并导入" })).toBeNull();
});

it("clears old confirmations after conflict, re-preview and file changes", async () => {
  const fetcher = vi.fn(async (url: string) =>
    url.endsWith("/preview")
      ? Response.json(preview)
      : Response.json(
          { detail: "项目 Wiki 已变化，请重新预览" },
          { status: 409 },
        ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<WikiTransfer project="alpha" selected={[]} refresh={() => {}} />);
  await openPreview();
  fireEvent.click(screen.getByLabelText("确认修改 build"));
  fireEvent.click(screen.getByLabelText("确认修改 tests"));
  fireEvent.click(screen.getByRole("button", { name: "确认并导入" }));
  await screen.findByText("项目 Wiki 已变化，请重新预览");
  expect(screen.queryByRole("button", { name: "确认并导入" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "预览 / 重新预览 ZIP" }));
  const build = (await screen.findByLabelText(
    "确认修改 build",
  )) as HTMLInputElement;
  expect(build.checked).toBe(false);
  fireEvent.click(build);
  fireEvent.change(screen.getByLabelText("导入 Wiki ZIP"), {
    target: { files: [new File(["other"], "other.zip")] },
  });
  expect(screen.queryByLabelText("确认修改 build")).toBeNull();
  expect(screen.queryByRole("button", { name: "确认并导入" })).toBeNull();
});

it("downloads all or only the passed cross-page selection and disables empty selection", async () => {
  const create = vi.fn(() => "blob:download");
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = vi.fn();
    },
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const fetcher = vi.fn(async () => new Response("zip"));
  vi.stubGlobal("fetch", fetcher);
  const view = render(
    <WikiTransfer project="alpha" selected={[]} refresh={() => {}} />,
  );
  expect(
    (
      screen.getByRole("button", {
        name: "导出选定 Wiki（0）",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "导出全部 Wiki" }));
  await screen.findByText("已下载当前项目全部 Wiki");
  view.rerender(
    <WikiTransfer
      project="alpha"
      selected={["first-page", "second-page"]}
      refresh={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "导出选定 Wiki（2）" }));
  await screen.findByText("已下载 2 篇选定 Wiki");
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  expect(calls[0]).toEqual([
    "/api/projects/alpha/wiki-export",
    { cache: "no-store" },
  ]);
  expect(JSON.parse(calls[1][1].body as string)).toEqual({
    pages: ["first-page", "second-page"],
  });
  expect(calls[1][1].method).toBe("POST");
  expect(click).toHaveBeenCalledTimes(2);
});

it("reports invalid ZIP and download errors without enabling import", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ detail: "无效 ZIP" }, { status: 409 })),
  );
  render(<WikiTransfer project="alpha" selected={[]} refresh={() => {}} />);
  fireEvent.change(screen.getByLabelText("导入 Wiki ZIP"), {
    target: { files: [new File(["bad"], "bad.zip")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览 / 重新预览 ZIP" }));
  await screen.findByText("无效 ZIP");
  expect(screen.queryByRole("button", { name: "确认并导入" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "导出全部 Wiki" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "导出全部 Wiki",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(screen.getByText("无效 ZIP")).toBeTruthy();
});
