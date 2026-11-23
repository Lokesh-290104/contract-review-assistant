import { useEffect, useRef } from "react";
import { Clause, locationLabel } from "../api";

export interface Highlight {
  chunkId: number;
  quote: string | null;
  start: number | null; // offset of the quote in the full contract text, when known
}

/** The whole contract, clause by clause. The selected clause is scrolled into view and its quote highlighted. */
export default function ClauseViewer({ clauses, highlight }: { clauses: Clause[]; highlight: Highlight | null }) {
  const refs = useRef(new Map<number, HTMLElement>());

  useEffect(() => {
    if (!highlight) return;
    refs.current.get(highlight.chunkId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlight]);

  if (clauses.length === 0) return <div className="empty small">The contract text appears here once it has been read.</div>;

  return (
    <div className="clauses">
      {clauses.map((c) => {
        const active = highlight?.chunkId === c.id;
        return (
          <section
            key={c.id}
            ref={(el) => {
              if (el) refs.current.set(c.id, el);
              else refs.current.delete(c.id);
            }}
            className={`clause ${active ? "active" : ""}`}
          >
            <div className="clause-meta">
              {[c.heading, locationLabel(c.section, null) !== "location unknown" ? locationLabel(c.section, null) : null,
                c.page_start ? (c.page_start === c.page_end ? `page ${c.page_start}` : `pages ${c.page_start}–${c.page_end}`) : null]
                .filter(Boolean)
                .join(" · ")}
            </div>
            <p className="clause-text">{active ? withMark(c, highlight!) : c.text}</p>
          </section>
        );
      })}
    </div>
  );
}

function withMark(c: Clause, h: Highlight) {
  let from = -1;
  let len = 0;
  if (h.start != null && h.quote && h.start >= c.start_char && h.start < c.end_char) {
    from = h.start - c.start_char;
    len = h.quote.length;
  } else if (h.quote) {
    from = c.text.indexOf(h.quote);
    len = h.quote.length;
  }
  if (from < 0) return c.text;
  return (
    <>
      {c.text.slice(0, from)}
      <mark>{c.text.slice(from, from + len)}</mark>
      {c.text.slice(from + len)}
    </>
  );
}
