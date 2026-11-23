"""Clause-aware chunking.

Contracts are written as numbered clauses, so we split where a new clause starts
(`1.`, `4.2`, `Section 7`, `ARTICLE V`, or an ALL-CAPS title) instead of every N
characters. Each chunk keeps its section number, its parent heading, its page
range and its exact character offsets in the full text, so a quote can always be
traced back to where it sits in the contract.
"""

import re
from dataclasses import dataclass

from .parsing import page_for_offset

MAX_CHUNK_CHARS = 1800
MIN_BODY_CHARS = 60  # a heading with less text than this is merged into the next chunk

_ARTICLE = re.compile(r"^\s*(ARTICLE|Article)\s+([IVXLC]+|\d+)\b[.:]?\s*(.*)$")
_SECTION = re.compile(r"^\s*(SECTION|Section|Clause|CLAUSE)\s+(\d+(?:\.\d+)*)[.:]?\s*(.*)$")
# "1. Term", "4.2 The Client...", "12) Notices" — needs a dot/bracket so "30 days" never matches.
_NUMBERED = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})(?:\.|\))?\s+(\S.*)$")
_NUMBERED_STRICT = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2}){0,3}(\.|\))\s|^\s*\d{1,2}\.\d{1,2}")


@dataclass
class ChunkDraft:
    idx: int
    section: str | None
    heading: str | None
    start_char: int
    end_char: int
    text: str
    page_start: int | None
    page_end: int | None

    def embedding_text(self) -> str:
        label = " ".join(p for p in (self.section and f"Section {self.section}", self.heading) if p)
        return f"{label}\n{self.text}" if label else self.text


def _is_caps_title(line: str) -> bool:
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    return (
        3 <= len(s) <= 80
        and len(letters) >= 3
        and all(c.isupper() for c in letters)
        and not s.endswith((".", ",", ";"))
        and len(s.split()) <= 8
    )


def _heading_info(line: str) -> tuple[str | None, str | None] | None:
    """Return (section number, title) if this line starts a new clause, else None."""
    m = _ARTICLE.match(line)
    if m:
        return f"Article {m.group(2)}", (m.group(3).strip() or None)
    m = _SECTION.match(line)
    if m:
        return m.group(2), (m.group(3).strip() or None)
    if _NUMBERED_STRICT.match(line):
        m = _NUMBERED.match(line)
        if m:
            rest = m.group(2).strip()
            # Short rest = a title ("Termination"); long rest = clause text starting on the same line.
            title = rest.rstrip(".:") if len(rest) <= 60 and not rest.endswith((",", ";")) else None
            if title and title.isupper():
                title = title.title()
            return m.group(1), title
    if _is_caps_title(line):
        return None, line.strip().title()
    return None


def _trim(full: str, start: int, end: int) -> tuple[int, int]:
    while start < end and full[start].isspace():
        start += 1
    while end > start and full[end - 1].isspace():
        end -= 1
    return start, end


def _split_long(full: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split a long clause at paragraph, then line, then sentence edges."""
    if end - start <= MAX_CHUNK_CHARS:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    cur = start
    while end - cur > MAX_CHUNK_CHARS:
        window = full[cur : cur + MAX_CHUNK_CHARS]
        cut = -1
        for sep in ("\n\n", "\n", ". ", "; ", " "):
            pos = window.rfind(sep, MAX_CHUNK_CHARS // 3)
            if pos != -1:
                cut = pos + len(sep)
                break
        if cut <= 0:
            cut = MAX_CHUNK_CHARS
        pieces.append((cur, cur + cut))
        cur += cut
    pieces.append((cur, end))
    return [p for p in (_trim(full, a, b) for a, b in pieces) if p[1] > p[0]]


def chunk_text(full: str, page_starts: list[int], has_pages: bool) -> list[ChunkDraft]:
    # 1. Find every clause start: (char offset, section, title).
    starts: list[tuple[int, str | None, str | None]] = []
    offset = 0
    for line in full.split("\n"):
        info = _heading_info(line)
        if info is not None:
            starts.append((offset, info[0], info[1]))
        offset += len(line) + 1

    if not starts or starts[0][0] > 0:
        starts.insert(0, (0, None, "Preamble"))

    # 2. Turn starts into raw spans, remembering the latest top-level title as the heading.
    spans: list[tuple[int, int, str | None, str | None]] = []
    parent_title: dict[str, str] = {}
    for i, (s, section, title) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else len(full)
        heading = title
        if section and "." in section and not title:
            heading = parent_title.get(section.split(".")[0])
        if section and title:
            parent_title[section.split(".")[0]] = title
        spans.append((s, e, section, heading))

    # 3. Merge heading-only spans ("4. TERMINATION") into the clause that follows.
    merged: list[tuple[int, int, str | None, str | None]] = []
    carry: tuple[int, str | None, str | None] | None = None
    for s, e, section, heading in spans:
        if carry is not None:
            s = carry[0]
            section = section or carry[1]
            heading = heading or carry[2]
            carry = None
        a, b = _trim(full, s, e)
        if b - a < MIN_BODY_CHARS and len(merged) + 1 < len(spans):
            carry = (s, section, heading)
            continue
        merged.append((s, e, section, heading))
    if carry is not None:  # trailing heading with nothing after it
        merged.append((carry[0], len(full), carry[1], carry[2]))

    # 4. Split long clauses and build the final chunks.
    chunks: list[ChunkDraft] = []
    for s, e, section, heading in merged:
        a, b = _trim(full, s, e)
        if b <= a:
            continue
        for ps, pe in _split_long(full, a, b):
            chunks.append(
                ChunkDraft(
                    idx=len(chunks),
                    section=section,
                    heading=heading,
                    start_char=ps,
                    end_char=pe,
                    text=full[ps:pe],
                    page_start=page_for_offset(page_starts, ps) if has_pages else None,
                    page_end=page_for_offset(page_starts, pe - 1) if has_pages else None,
                )
            )
    return chunks
