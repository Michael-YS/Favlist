/** Component tests for imports, details, bulk actions, tag folding, and themes. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { api } from "./api";
import { visibleTags } from "./lib/tags";
import styles from "./styles.css?raw";
import type { Comic, ComicPage } from "./types";

/** Return a complete comic fixture that mirrors FastAPI's ComicRead response. */
function comicFixture(overrides: Partial<Comic> = {}): Comic {
  return { id: 123, title: "示例标题", description: "示例简介", author: "作者", page_count: 20, published_at: "2025-01-01", views: 10, likes: 2, comments: 1, status: "ready", error: null, tags: [{ name: "不喜欢", emphasis: "disliked" }, { name: "喜欢", emphasis: "liked" }, { name: "普通", emphasis: "normal" }], cover_version: 2, ...overrides };
}

/** Install a fetch mock serving one authenticated list and its supported actions. */
function installApiMock(page: ComicPage): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
    if (url.endsWith("/api/auth/logout")) return new Response(null, { status: 204 });
    if (url.startsWith("/api/comics?")) return Response.json(page);
    if (url.endsWith("/api/tags")) return Response.json([]);
    if (url.endsWith("/api/import")) return Response.json({ added: 2, duplicate: 1, invalid: 3 });
    if (url.endsWith("/api/comics/123")) return Response.json(page.items[0]);
    if (url.endsWith("/api/comics/123/refresh") || url.endsWith("/api/comics/bulk-refresh") || url.endsWith("/api/comics")) return new Response(null, { status: 204 });
    throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Set up browser APIs and a default authenticated empty library before each test. */
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  installApiMock({ items: [], total: 0, page: 1, page_size: 50 });
});

/** Restore spies and mocked browser APIs after each test. */
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("visibleTags", () => {
  it("keeps server priority while folding six desktop tags and two mobile tags", () => {
    const tags = Array.from({ length: 8 }, (_, index) => ({ name: `T${index}`, emphasis: "normal" as const }));
    expect(visibleTags(tags, false)).toEqual({ shown: tags.slice(0, 6), hidden: 2 });
    expect(visibleTags(tags, true)).toEqual({ shown: tags.slice(0, 2), hidden: 6 });
  });

  it("keeps disliked and liked tags in the two mobile emphasis slots", () => {
    const tags = comicFixture().tags;
    expect(visibleTags(tags, true)).toEqual({ shown: tags.slice(0, 2), hidden: 1 });
    expect(visibleTags(tags, true).shown.map((tag) => tag.emphasis)).toEqual(["disliked", "liked"]);
  });
});

describe("responsive visual contract", () => {
  it("uses distinct disliked colours in light and dark themes", () => {
    expect(styles).toContain("--disliked-bg:#fee4e2");
    expect(styles).toContain("--disliked-bg:#571815");
    expect(styles).toContain(".tag.disliked");
    expect(styles).toContain("border-color:#f04438");
  });

  it("stops the liked-tag rainbow animation when reduced motion is requested", () => {
    expect(styles).toContain("@media (prefers-reduced-motion:reduce)");
    expect(styles).toMatch(/prefers-reduced-motion:reduce[\s\S]*?\.tag\.liked\s*\{\s*animation:none/);
  });

  it("keeps a horizontal grid row and mobile tag pills at phone widths", () => {
    expect(styles).toContain("@media (max-width:700px)");
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.comic-row\s*\{\s*grid-template-columns:/);
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.mobile-tags\s*\{\s*display:inline/);
  });
});

describe("App", () => {
  it("shows authentication loading and then exposes a list failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
      if (url.startsWith("/api/comics?")) return Response.json({ detail: "列表暂不可用" }, { status: 503 });
      if (url.endsWith("/api/tags")) return Response.json([]);
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<App />);
    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("列表暂不可用");
  });

  it("shows import totals returned by the backend", async () => {
    render(<App />);
    await screen.findByText("还没有条目。点击“导入”开始。");
    fireEvent.click(screen.getByRole("button", { name: "导入" }));
    fireEvent.change(screen.getByRole("textbox", { name: "导入文本" }), { target: { value: "JM123, 456" } });
    fireEvent.click(within(screen.getByRole("form", { name: "导入编号" })).getByRole("button", { name: "导入" }));
    expect(await screen.findByText("新增 2 · 重复 1 · 无效 3")).toBeInTheDocument();
  });

  it("keeps single refresh, export, details, and theme persistence available", async () => {
    const fetchMock = installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    render(<App />);
    expect(await screen.findByRole("link", { name: "导出" })).toHaveAttribute("href", "/api/export/ids");
    fireEvent.click(await screen.findByRole("button", { name: "刷新 JM123" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/comics/123/refresh", expect.objectContaining({ method: "POST" })));
    fireEvent.click(await screen.findByRole("button", { name: "示例标题" }));
    expect(await screen.findByRole("complementary", { name: "漫画详情" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("favlist-theme")).toBe("dark");
  });

  it("requires two confirmations before deleting a selected row", async () => {
    const fetchMock = installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(true).mockReturnValueOnce(true);
    render(<App />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 JM123" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/comics", expect.objectContaining({ method: "DELETE" })));
  });

  it("submits the selected IDs to the bulk refresh endpoint", async () => {
    const fetchMock = installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    render(<App />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 JM123" }));
    fireEvent.click(screen.getByRole("button", { name: "批量刷新" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/comics/bulk-refresh", expect.objectContaining({ method: "POST", body: JSON.stringify({ ids: [123] }) })));
  });
});

describe("api request security", () => {
  it("adds the Favlist CSRF header to every unsafe API method", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/login")) return Response.json({ username: "admin" });
      if (url.endsWith("/api/import")) return Response.json({ added: 0, duplicate: 0, invalid: 0 });
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.login("admin", "password");
    await api.logout();
    await api.importIds("JM123");
    await api.refresh(123);
    await api.bulkRefresh([123]);
    await api.delete([123]);

    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("X-Favlist-CSRF")).toBe("1");
    }
  });
});
