/** Component tests for imports, details, bulk actions, tag folding, and themes. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, extractQuickRecordId } from "./App";
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
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
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

describe("extractQuickRecordId", () => {
  it.each([
    ["小明12天做了3本暑假作业，错了45道题，用掉6支笔", "123456"],
    ["第1段有23，第4段有567", "1234567"],
    ["编号 001-234-567-890", "001234567890"],
    ["标点：9，8。7！6？5；4", "987654"],
    ["中文一二三，全角１２３", ""],
  ])("joins only ASCII digits from %s", (text, expected) => {
    expect(extractQuickRecordId(text)).toBe(expected);
  });
});

describe("responsive visual contract", () => {
  it("folds desktop facets into one scrollable row with a visible disclosure button", () => {
    expect(styles).toMatch(/\.facet-bar\s*\{[^}]*grid-template-columns:auto minmax\(0,1fr\) auto/);
    expect(styles).toMatch(/\.facet-bar\s*>\s*div\s*\{[^}]*flex-wrap:nowrap;[^}]*max-height:30px;[^}]*overflow-x:auto/);
    expect(styles).toMatch(/\.facet-toggle\s*\{[^}]*display:grid/);
    expect(styles).toMatch(/\.facet-bar\.expanded\s*>\s*div\s*\{[^}]*flex-wrap:wrap;[^}]*max-height:none;[^}]*overflow:visible/);
  });

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

  it("folds the mobile facet list to one row and keeps the ribbon in its grid column", () => {
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.facet-bar\.expanded\s*>\s*div\s*\{[^}]*grid-column:1\/-1;[^}]*grid-row:2/);
    expect(styles).toMatch(/\.facet-bar\s*>\s*div\s*>\s*\.tag\s*\{[^}]*flex:0 0 auto;[^}]*white-space:nowrap/);
    expect(styles).toMatch(/\.comic-row::before\s*\{[^}]*grid-column:1;[^}]*grid-row:1/);
    expect(styles).not.toMatch(/\.comic-row::before\s*\{[^}]*position:absolute/);
  });

  it("stretches the mobile cover and ribbon across the complete card height", () => {
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.comic-row::before\s*\{[^}]*grid-row:1\/-1/);
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.cover-button\s*\{[^}]*grid-row:1\/-1/);
  });

  it("provides compact private rows and a responsive quick-record strip", () => {
    expect(styles).toMatch(/\.private-row\s*\{[^}]*grid-template-columns:38px minmax\(160px,1fr\) 84px 48px;[^}]*min-height:74px/);
    expect(styles).toMatch(/\.quick-record\s*\{[^}]*border-left:6px solid var\(--signal\)/);
    expect(styles).toMatch(/max-width:700px[\s\S]*?\.comic-row\.private-row\s*\{[^}]*min-height:74px/);
  });
});

describe("App", () => {
  it("expands and collapses the tag filter with the mobile disclosure button", async () => {
    const tags = Array.from({ length: 8 }, (_, index) => ({ name: `标签${index}`, emphasis: "normal" as const, count: index + 1 }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
      if (url.startsWith("/api/comics?")) return Response.json({ items: [], total: 0, page: 1, page_size: 50 });
      if (url.endsWith("/api/tags")) return Response.json(tags);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "显示敏感内容" }));
    const toggle = await screen.findByRole("button", { name: "展开标签筛选" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "收起标签筛选" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "收起标签筛选" }));
    expect(screen.getByRole("button", { name: "展开标签筛选" })).toHaveAttribute("aria-expanded", "false");
  });

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
    await screen.findByText("还没有记录。");
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "批量导入" }));
    fireEvent.change(screen.getByRole("textbox", { name: "导入文本" }), { target: { value: "JM123, 456" } });
    fireEvent.click(within(screen.getByRole("form", { name: "导入编号" })).getByRole("button", { name: "导入" }));
    expect(await screen.findByText("新增 2 · 重复 1 · 无效 3")).toBeInTheDocument();
  });

  it("keeps single refresh, export, details, and theme persistence available", async () => {
    const fetchMock = installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "刷新 JM123" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/comics/123/refresh", expect.objectContaining({ method: "POST" })));
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "显示敏感内容" }));
    fireEvent.click(await screen.findByRole("button", { name: "示例标题" }));
    expect(await screen.findByRole("complementary", { name: "漫画详情" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭详情" }));
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    expect(screen.getByRole("link", { name: "导出编号" })).toHaveAttribute("href", "/api/export/ids");
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

  it("records the digits hidden in a sentence without confirmation", async () => {
    const confirmMock = vi.spyOn(window, "confirm");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
      if (url.startsWith("/api/comics?")) return Response.json({ items: [], total: 0, page: 1, page_size: 50 });
      if (url.endsWith("/api/tags")) return Response.json([]);
      if (url.endsWith("/api/import")) return Response.json({ added: 1, duplicate: 0, invalid: 0 });
      throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const input = await screen.findByRole("textbox", { name: "快速记录" });
    fireEvent.change(input, { target: { value: "小明12天做了3本作业，错45道，用6支笔" } });
    fireEvent.submit(screen.getByRole("form", { name: "快速记录" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已记录");
    expect(input).toHaveValue("");
    const importCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/import"));
    expect(importCall?.[1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "123456" }) }));
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it("rejects digit-free quick text without making an import request", async () => {
    const fetchMock = installApiMock({ items: [], total: 0, page: 1, page_size: 50 });
    render(<App />);
    fireEvent.change(await screen.findByRole("textbox", { name: "快速记录" }), { target: { value: "只有中文一二三和全角１２３" } });
    fireEvent.submit(screen.getByRole("form", { name: "快速记录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("这句话里没有可记录的数字");
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/import"))).toBe(false);
  });

  it("reports duplicate quick records and blocks a second request while busy", async () => {
    let finishImport: ((response: Response) => void) | undefined;
    const pendingImport = new Promise<Response>((resolve) => { finishImport = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
      if (url.startsWith("/api/comics?")) return Response.json({ items: [], total: 0, page: 1, page_size: 50 });
      if (url.endsWith("/api/tags")) return Response.json([]);
      if (url.endsWith("/api/import")) return pendingImport;
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const form = await screen.findByRole("form", { name: "快速记录" });
    fireEvent.change(screen.getByRole("textbox", { name: "快速记录" }), { target: { value: "1和23" } });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/import"))).toHaveLength(1);

    finishImport?.(Response.json({ added: 0, duplicate: 1, invalid: 0 }));
    expect(await screen.findByRole("status")).toHaveTextContent("已经记录过");
    expect(screen.getByRole("textbox", { name: "快速记录" })).toHaveValue("");
  });

  it("keeps quick text available when the import request fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return Response.json({ username: "admin" });
      if (url.startsWith("/api/comics?")) return Response.json({ items: [], total: 0, page: 1, page_size: 50 });
      if (url.endsWith("/api/tags")) return Response.json([]);
      if (url.endsWith("/api/import")) return Response.json({ detail: "记录服务暂不可用" }, { status: 503 });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const input = await screen.findByRole("textbox", { name: "快速记录" });
    fireEvent.change(input, { target: { value: "今天看12页，记住345" } });
    fireEvent.submit(screen.getByRole("form", { name: "快速记录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("记录服务暂不可用");
    expect(input).toHaveValue("今天看12页，记住345");
  });

  it("starts concealed, reveals through More, and conceals again in the background", async () => {
    installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    render(<App />);

    await screen.findByText("JM123");
    expect(screen.queryByText("示例标题")).not.toBeInTheDocument();
    expect(screen.queryByText("作者")).not.toBeInTheDocument();
    expect(screen.queryByText("不喜欢")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看 JM123 详情" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "显示敏感内容" }));
    expect(await screen.findByText("示例标题")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "示例标题 封面" })).toHaveAttribute("src", "/api/comics/123/cover?v=2");

    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "隐藏敏感内容" }));
    expect(screen.queryByText("示例标题")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "显示敏感内容" }));
    expect(await screen.findByText("示例标题")).toBeInTheDocument();

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    fireEvent(document, new Event("visibilitychange"));
    expect(screen.queryByText("示例标题")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(localStorage.getItem("favlist-sensitive-visible")).toBeNull();
  });

  it("conceals sensitive content on pagehide", async () => {
    installApiMock({ items: [comicFixture()], total: 1, page: 1, page_size: 50 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "显示敏感内容" }));
    expect(await screen.findByText("示例标题")).toBeInTheDocument();
    fireEvent(window, new Event("pagehide"));
    expect(screen.queryByText("示例标题")).not.toBeInTheDocument();
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
