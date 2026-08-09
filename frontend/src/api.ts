/** Typed, cookie-aware HTTP client for the Favlist FastAPI endpoints. */
import type { Comic, ComicPage, ImportSummary, Tag } from "./types";

/** Turn a failed response into the API detail message when it is available. */
async function ensureOk(response: Response): Promise<Response> {
  if (response.ok) return response;
  const body: unknown = await response.json().catch(() => ({ detail: response.statusText }));
  const detail = typeof body === "object" && body !== null && "detail" in body ? String(body.detail) : response.statusText;
  throw new Error(detail || `请求失败 (${response.status})`);
}

/** Perform an authenticated API request and support the backend's 204 responses. */
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (method !== "GET") headers.set("X-Favlist-CSRF", "1");
  const response = await ensureOk(await fetch(url, {
    credentials: "same-origin",
    ...init,
    headers,
  }));
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** Public client methods matching the FastAPI API contract. */
export const api = {
  /** Start the administrator session. */
  login: (username: string, password: string) => request<{ username: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  /** End the current session. */
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  /** Return the session owner or reject when unauthenticated. */
  me: () => request<{ username: string }>("/api/auth/me"),
  /** Return the filtered and paginated comic list. */
  comics: (params: URLSearchParams) => request<ComicPage>(`/api/comics?${params.toString()}`),
  /** Return every detail field for a comic. */
  comic: (id: number) => request<Comic>(`/api/comics/${id}`),
  /** Return tag facets with server-calculated emphasis. */
  tags: () => request<Tag[]>("/api/tags"),
  /** Import a free-form block of identifiers. */
  importIds: (text: string) => request<ImportSummary>("/api/import", { method: "POST", body: JSON.stringify({ text }) }),
  /** Queue one record for forced metadata refresh. */
  refresh: (id: number) => request<void>(`/api/comics/${id}/refresh`, { method: "POST" }),
  /** Queue multiple records for forced metadata refresh. */
  bulkRefresh: (ids: number[]) => request<void>("/api/comics/bulk-refresh", { method: "POST", body: JSON.stringify({ ids }) }),
  /** Permanently delete multiple records and their cached covers. */
  delete: (ids: number[]) => request<void>("/api/comics", { method: "DELETE", body: JSON.stringify({ ids }) }),
};
