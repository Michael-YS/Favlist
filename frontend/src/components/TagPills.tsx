/** Responsive tag-pill presentation with backend-defined ordering and emphasis. */
import { visibleTags } from "../lib/tags";
import type { Tag } from "../types";

interface TagPillsProps { tags: Tag[]; all?: boolean; }

/** Render one concise tag pill, including a warning mark for disliked tags. */
function TagPill({ tag }: { tag: Tag }) {
  return <span className={`tag ${tag.emphasis}`}>{tag.emphasis === "disliked" && "⚠ "}{tag.name}</span>;
}

/** Render a pre-sized pill sequence and its folded tag count. */
function TagSlice({ tags, mobile }: { tags: Tag[]; mobile: boolean }) {
  const { shown, hidden } = visibleTags(tags, mobile);
  return <>{shown.map((tag) => <TagPill key={tag.name} tag={tag} />)}{hidden > 0 && <span className="tag normal">+{hidden}</span>}</>;
}

/** Render all tags in a detail drawer or responsive folded tags in a list row. */
export function TagPills({ tags, all = false }: TagPillsProps) {
  if (all) return <div className="tags all-tags">{tags.map((tag) => <TagPill key={tag.name} tag={tag} />)}</div>;
  return <div className="tags"><span className="desktop-tags"><TagSlice tags={tags} mobile={false} /></span><span className="mobile-tags"><TagSlice tags={tags} mobile /></span></div>;
}
