"""Check that a quote really appears in the contract, and find where.

We only forgive differences that PDF/Word extraction creates on its own: runs of
spaces and line breaks, curly vs straight quotes, different dash characters and
letter case. Words, numbers and punctuation must match. When a match is found we
return the *contract's own* text for that span, so the quote shown to the user is
always copied from the contract, never from the model.
"""

import re
from dataclasses import dataclass

_CHAR_MAP = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        " ": " ", " ": " ", " ": " ",
    }
)

MIN_QUOTE_CHARS = 12


@dataclass(frozen=True)
class QuoteMatch:
    start: int  # offsets in the original full text
    end: int
    text: str  # the contract's own words for this span


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-case, unify quotes/dashes, squeeze whitespace. Keeps a map back to original offsets."""
    out: list[str] = []
    index: list[int] = []
    prev_space = True  # drop leading whitespace
    for i, ch in enumerate(text.translate(_CHAR_MAP)):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            index.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            index.append(i)
            prev_space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def clean_model_quote(quote: str) -> str:
    """Remove wrapping quote marks and spaces the model adds around a quote."""
    q = quote.strip()
    q = re.sub(r'^["\'“”‘’`]+|["\'“”‘’`]+$', "", q).strip()
    return q


def find_quote(full_text: str, quote: str, prefer_ranges: list[tuple[int, int]] | None = None) -> QuoteMatch | None:
    """Locate `quote` in `full_text`. Returns None if it is not there word for word.

    If the quote appears more than once, prefer a match inside one of `prefer_ranges`
    (the chunks the model was shown).
    """
    q = clean_model_quote(quote)
    if len(q) < MIN_QUOTE_CHARS or "..." in q or "…" in q:
        return None  # too short to prove anything, or the model cut words out
    norm_full, index = _normalize_with_map(full_text)
    norm_q, _ = _normalize_with_map(q)
    if not norm_q:
        return None

    positions: list[int] = []
    pos = norm_full.find(norm_q)
    while pos != -1:
        positions.append(pos)
        pos = norm_full.find(norm_q, pos + 1)
    if not positions:
        return None

    def to_match(p: int) -> QuoteMatch:
        start = index[p]
        end = index[p + len(norm_q) - 1] + 1
        return QuoteMatch(start=start, end=end, text=full_text[start:end])

    matches = [to_match(p) for p in positions]
    for m in matches:
        if prefer_ranges and any(a <= m.start < b for a, b in prefer_ranges):
            return m
    return matches[0]


_QUOTED = re.compile(r'"([^"\n]{%d,})"|“([^”\n]{%d,})”' % (MIN_QUOTE_CHARS, MIN_QUOTE_CHARS))


def quoted_passages(answer: str) -> list[str]:
    """Pull the passages a chat answer puts in double quotes."""
    return [a or b for a, b in _QUOTED.findall(answer)]
