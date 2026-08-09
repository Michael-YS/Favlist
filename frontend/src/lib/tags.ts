/** Helpers for presenting server-ordered tags in constrained list rows. */
import type { Tag } from "../types";

/** Return the number of visible tags and the remaining folded tag count. */
export function visibleTags(tags: Tag[], mobile: boolean): { shown: Tag[]; hidden: number } {
  const limit = mobile ? 2 : 6;
  return { shown: tags.slice(0, limit), hidden: Math.max(0, tags.length - limit) };
}
