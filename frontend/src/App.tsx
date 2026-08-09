/**
 * Authenticated React user interface for browsing and maintaining Favlist.
 *
 * The server owns all filtering and tag prioritisation rules; this view only
 * presents the supplied data and coordinates requests made through `api`.
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "./api";
import { TagPills } from "./components/TagPills";
import { visibleTags } from "./lib/tags";
import type { Comic, ImportSummary, Tag } from "./types";

type Theme = "light" | "dark";

interface LoginProps {
  onLogin: () => void;
}

interface ComicRowProps {
  comic: Comic;
  selected: boolean;
  onSelect: (id: number) => void;
  onOpen: (id: number) => void;
  onRefresh: (id: number) => void;
}

interface DetailDrawerProps {
  comic: Comic;
  onClose: () => void;
}

interface ImportDialogProps {
  onClose: () => void;
  onImported: () => void;
}

interface LibraryProps {
  onLogout: () => void;
}

/** Return the saved theme, or the current operating-system colour preference. */
function initialTheme(): Theme {
  const savedTheme = localStorage.getItem("favlist-theme");
  if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Convert an unknown thrown value into a message that is safe to display. */
function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

/** Build the authenticated cover URL, including the server-managed cache version. */
function coverUrl(comic: Comic): string {
  return `/api/comics/${comic.id}/cover?v=${comic.cover_version}`;
}

/** Render the login form for the sole administrator account. */
function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  /** Send credentials to the API and enter the library after a successful login. */
  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await api.login(username, password);
      onLogin();
    } catch (reason) {
      setError(errorMessage(reason, "登录失败，请稍后重试。"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-intro" aria-hidden="true">
        <span className="login-index">PRIVATE COLLECTION</span>
        <div className="login-spines"><i /><i /><i /><i /></div>
        <p>把喜欢的作品，整理成一座随时可以返回的私人档案馆。</p>
      </section>
      <form className="panel login" onSubmit={submit}>
        <p className="eyebrow">私人收藏档案</p>
        <h1>Favlist</h1>
        <p className="muted">登录以整理、检索和维护你的收藏。</p>
        <label>
          用户名
          <input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label>
          密码
          <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button disabled={submitting || !username || !password}>{submitting ? "登录中…" : "登录"}</button>
      </form>
    </main>
  );
}

/** Render one responsive horizontal comic row. */
function ComicRow({ comic, selected, onSelect, onOpen, onRefresh }: ComicRowProps) {
  const title = comic.title || "等待获取标题";
  return (
    <article className="comic-row" data-comic-id={`JM ${comic.id}`}>
      <input aria-label={`选择 JM${comic.id}`} type="checkbox" checked={selected} onChange={() => onSelect(comic.id)} />
      <button className="cover-button" onClick={() => onOpen(comic.id)} aria-label={`查看 JM${comic.id} 详情`}>
        <img className="cover" src={coverUrl(comic)} alt={`${title} 封面`} />
      </button>
      <div className="identity">
        <strong>JM{comic.id}</strong>
        <button className="title-link" onClick={() => onOpen(comic.id)}>{title}</button>
      </div>
      <span className="author">{comic.author || "—"}</span>
      <TagPills tags={comic.tags} />
      <span className={`status ${comic.status}`} title={comic.error ?? undefined}>{comic.status}</span>
      <button className="icon-button" onClick={() => onRefresh(comic.id)} aria-label={`刷新 JM${comic.id}`}>↻</button>
    </article>
  );
}

/** Render the full metadata drawer for the selected comic. */
function DetailDrawer({ comic, onClose }: DetailDrawerProps) {
  return (
    <div className="backdrop" onMouseDown={onClose} role="presentation">
      <aside className="drawer" onMouseDown={(event) => event.stopPropagation()} aria-label="漫画详情">
        <button className="close" onClick={onClose} aria-label="关闭详情">×</button>
        <img className="detail-cover" src={coverUrl(comic)} alt={`${comic.title || `JM${comic.id}`} 大封面`} />
        <h2>{comic.title || `JM${comic.id}`}</h2>
        <p className="muted">JM{comic.id} · {comic.author || "未知作者"}</p>
        <TagPills tags={comic.tags} all />
        {comic.description && <p>{comic.description}</p>}
        <dl>
          <dt>页数</dt><dd>{comic.page_count ?? "—"}</dd>
          <dt>发布日期</dt><dd>{comic.published_at ?? "—"}</dd>
          <dt>观看 / 喜欢 / 评论</dt><dd>{comic.views ?? 0} / {comic.likes ?? 0} / {comic.comments ?? 0}</dd>
          {comic.error && <><dt>最近错误</dt><dd className="error">{comic.error}</dd></>}
        </dl>
        <a className="button-link" href={`https://18comic.vip/album/${comic.id}`} target="_blank" rel="noreferrer">打开 JM 页面</a>
      </aside>
    </div>
  );
}

/** Render a text-only import dialog and display the backend import totals. */
function ImportDialog({ onClose, onImported }: ImportDialogProps) {
  const [text, setText] = useState("");
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  /** Persist the entered identifiers, then reload the current list page. */
  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setSummary(await api.importIds(text));
      onImported();
    } catch (reason) {
      setError(errorMessage(reason, "导入失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="backdrop" role="presentation">
      <form className="dialog panel" onSubmit={submit} aria-label="导入编号">
        <h2>导入编号</h2>
        <textarea aria-label="导入文本" autoFocus placeholder="JM123, 456 或每行一个编号" value={text} onChange={(event) => setText(event.target.value)} />
        {summary && <p className="summary">新增 {summary.added} · 重复 {summary.duplicate} · 无效 {summary.invalid}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <footer><button type="button" className="secondary" onClick={onClose}>关闭</button><button disabled={busy || !text.trim()}>{busy ? "导入中…" : "导入"}</button></footer>
      </form>
    </div>
  );
}

/** Render the authenticated library and coordinate its server-backed state. */
function Library({ onLogout }: LibraryProps) {
  const [items, setItems] = useState<Comic[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("added_desc");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<Comic | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  /** Derive the server query from the current pagination and filter controls. */
  const query = useMemo(() => {
    const params = new URLSearchParams({ page: String(page), page_size: "50", sort });
    if (search.trim()) params.set("search", search.trim());
    selectedTags.forEach((tag) => params.append("tag", tag));
    return params;
  }, [page, search, sort, selectedTags]);

  /** Fetch the visible page and reset selection which no longer belongs to it. */
  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const result = await api.comics(query);
      setItems(result.items);
      setTotal(result.total);
      setSelected((current) => current.filter((id) => result.items.some((comic) => comic.id === id)));
      setError("");
    } catch (reason) {
      setError(errorMessage(reason, "列表加载失败，请稍后重试。"));
    } finally {
      setLoading(false);
    }
  }, [query]);

  /** Load server state when any list control changes. */
  useEffect(() => { void load(); }, [load]);

  /** Reload available facets after a list-changing request. */
  useEffect(() => {
    void api.tags().then(setTags).catch((reason: unknown) => setError(errorMessage(reason, "标签加载失败，请稍后重试。")));
  }, [items.length]);

  /** Poll only while server-side jobs are processing metadata or covers. */
  useEffect(() => {
    if (!items.some((item) => ["pending", "loading", "refreshing"].includes(item.status))) return;
    const timer = window.setInterval(() => void load(), 2_000);
    return () => window.clearInterval(timer);
  }, [items, load]);

  /** Toggle a comic in the current bulk selection. */
  function toggleSelection(id: number): void {
    setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  /** Toggle one tag facet; all selected facets are sent as an AND query. */
  function toggleTag(name: string): void {
    setPage(1);
    setSelectedTags((current) => current.includes(name) ? current.filter((tag) => tag !== name) : [...current, name]);
  }

  /** Fetch one full record and open its metadata drawer. */
  async function openDetail(id: number): Promise<void> {
    try { setDetail(await api.comic(id)); } catch (reason) { setError(errorMessage(reason, "详情加载失败，请稍后重试。")); }
  }

  /** Queue a forced refresh for one record and refresh its displayed state. */
  async function refreshOne(id: number): Promise<void> {
    try { await api.refresh(id); await load(); } catch (reason) { setError(errorMessage(reason, "刷新失败，请稍后重试。")); }
  }

  /** Queue a forced refresh for the selected records. */
  async function refreshSelected(): Promise<void> {
    try { await api.bulkRefresh(selected); setSelected([]); await load(); } catch (reason) { setError(errorMessage(reason, "批量刷新失败，请稍后重试。")); }
  }

  /** Delete selected records only after two explicit browser confirmations. */
  async function deleteSelected(): Promise<void> {
    if (!window.confirm(`确定删除 ${selected.length} 条记录及其封面？`)) return;
    if (!window.confirm("此操作不可撤销。确认永久删除？")) return;
    try { await api.delete(selected); setSelected([]); await load(); } catch (reason) { setError(errorMessage(reason, "删除失败，请稍后重试。")); }
  }

  /** Sign out of the session and switch the top-level application state. */
  async function logout(): Promise<void> {
    try { await api.logout(); onLogout(); } catch (reason) { setError(errorMessage(reason, "退出失败，请稍后重试。")); }
  }

  return (
    <main className="app-shell">
      <header className="library-header"><div><p className="eyebrow">私人收藏档案</p><div className="title-line"><h1>Favlist</h1><span className="record-count">{total} records</span></div></div><nav><a className="button-link secondary" href="/api/export/ids">导出</a><button aria-label="导入" onClick={() => setShowImport(true)}>＋ 导入编号</button><button className="secondary" onClick={() => void logout()}>退出</button></nav></header>
      <section className="toolbar"><div className="search-field"><span aria-hidden="true">⌕</span><input aria-label="搜索" placeholder="搜索编号、标题、作者或标签" value={search} onChange={(event) => { setPage(1); setSearch(event.target.value); }} /></div><select aria-label="排序" value={sort} onChange={(event) => { setPage(1); setSort(event.target.value); }}><option value="added_desc">最近添加</option><option value="title_asc">标题</option><option value="id_asc">编号升序</option><option value="id_desc">编号降序</option></select></section>
      {tags.length > 0 && <section className="facet-bar" aria-label="标签筛选"><span className="facet-label">标签索引</span><div>{tags.map((tag) => <button key={tag.name} className={`tag ${tag.emphasis} ${selectedTags.includes(tag.name) ? "active" : ""}`} onClick={() => toggleTag(tag.name)}>{tag.name} ({tag.count ?? 0})</button>)}</div></section>}
      {selected.length > 0 && <section className="bulk panel"><strong>已选 {selected.length} 条</strong><button onClick={() => void refreshSelected()}>批量刷新</button><button className="danger" onClick={() => void deleteSelected()}>删除</button></section>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="catalogue-heading"><h2>馆藏目录</h2><p>封面与编号共同构成检索入口</p></div>
      <section className="list" aria-busy={loading}>{loading && items.length === 0 ? <div className="empty panel">正在整理馆藏…</div> : items.map((comic) => <ComicRow key={comic.id} comic={comic} selected={selected.includes(comic.id)} onSelect={toggleSelection} onOpen={(id) => void openDetail(id)} onRefresh={(id) => void refreshOne(id)} />)}{!loading && items.length === 0 && <div className="empty panel"><strong>还没有条目。点击“导入”开始。</strong><span>导入 JM 编号，建立你的第一份收藏档案。</span><button onClick={() => setShowImport(true)}>导入编号</button></div>}</section>
      <footer className="pager"><button disabled={page <= 1 || loading} onClick={() => setPage((current) => current - 1)}>上一页</button><span>第 {page} 页</span><button disabled={page * 50 >= total || loading} onClick={() => setPage((current) => current + 1)}>下一页</button></footer>
      {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={() => void load()} />}
      {detail && <DetailDrawer comic={detail} onClose={() => setDetail(null)} />}
    </main>
  );
}

/** Own authentication and theme state for the complete browser application. */
export function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);

  /** Persist and apply the selected colour theme. */
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("favlist-theme", theme);
  }, [theme]);

  /** Check whether the session cookie currently authenticates the browser. */
  useEffect(() => { void api.me().then(() => setAuthenticated(true)).catch(() => setAuthenticated(false)); }, []);

  /** Toggle between the two explicitly supported themes. */
  function toggleTheme(): void { setTheme((current) => current === "dark" ? "light" : "dark"); }

  if (authenticated === null) return <main className="loading">加载中…</main>;
  return <><button className="theme-toggle" aria-label="切换主题" onClick={toggleTheme}>{theme === "dark" ? "☀" : "☾"}</button>{authenticated ? <Library onLogout={() => setAuthenticated(false)} /> : <Login onLogin={() => setAuthenticated(true)} />}</>;
}

export { visibleTags };
