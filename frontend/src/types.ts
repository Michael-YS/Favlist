/** Domain and FastAPI response types consumed by the Favlist web client. */
export type Emphasis = "disliked" | "liked" | "normal";
export type ComicStatus = "pending" | "loading" | "ready" | "error" | "refreshing";

/** A tag and its visual emphasis computed by the backend. */
export interface Tag { name: string; emphasis: Emphasis; count?: number; display_order?: number; }

/** Comic metadata returned in a list row or detail response. */
export interface Comic {
  id: number; title: string | null; description?: string | null; author: string | null; page_count?: number | null;
  published_at?: string | null; views?: number | null; likes?: number | null; comments?: number | null;
  status: ComicStatus; error: string | null; tags: Tag[]; cover_version: number; added_at?: string; refreshed_at?: string | null;
}

/** A single server-side paginated comic response. */
export interface ComicPage { items: Comic[]; total: number; page: number; page_size: number; }

/** Counts reported after the backend imports a text block. */
export interface ImportSummary { added: number; duplicate: number; invalid: number; }
