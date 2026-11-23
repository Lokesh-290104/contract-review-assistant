"""Hybrid retrieval inside ONE contract owned by ONE user.

Two rankings are combined with reciprocal-rank fusion (RRF):
1. vector search (pgvector cosine distance) — finds clauses that *mean* the same thing
   even with unusual wording;
2. keyword scoring — legal clauses use fixed phrases ("governed by the laws of",
   "shall not exceed"), and exact phrases beat embeddings on those.

Every query filters on both `user_id` and `contract_id`, so vector search can never
return another user's text.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Chunk

RRF_K = 60


@dataclass
class Retrieved:
    chunk: Chunk
    score: float


def keyword_scores(texts: list[str], patterns: tuple[str, ...] | list[str]) -> list[float]:
    """Score each text: earlier patterns weigh more; repeated hits add a little."""
    compiled = [re.compile(p, re.I) for p in patterns]
    n = len(compiled)
    scores = []
    for text in texts:
        score = 0.0
        for i, rx in enumerate(compiled):
            hits = len(rx.findall(text))
            if hits:
                weight = (n - i) / n
                score += weight * (1.0 + 0.2 * min(hits - 1, 3))
        scores.append(score)
    return scores


def rrf_merge(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return fused


def retrieve(
    db: Session,
    user_id: str,
    contract_id: uuid.UUID,
    query_vector: list[float] | None,
    keywords: tuple[str, ...] | list[str],
    top_k: int = 6,
    include_opening: int = 0,
    pool: int = 10,
) -> list[Chunk]:
    scope = (Chunk.user_id == user_id, Chunk.contract_id == contract_id)
    chunks = db.scalars(select(Chunk).where(*scope).order_by(Chunk.idx)).all()
    if not chunks:
        return []
    by_id = {c.id: c for c in chunks}

    rankings: list[list[int]] = []
    if query_vector is not None:
        vec_ids = db.scalars(
            select(Chunk.id)
            .where(*scope, Chunk.embedding.is_not(None))
            .order_by(Chunk.embedding.cosine_distance(query_vector))
            .limit(pool)
        ).all()
        rankings.append(list(vec_ids))

    if keywords:
        scores = keyword_scores([c.text for c in chunks], keywords)
        ranked = sorted(
            (s, c.id) for s, c in zip(scores, chunks) if s > 0
        )
        rankings.append([cid for _, cid in sorted(ranked, key=lambda t: -t[0])][:pool])

    fused = rrf_merge(rankings)
    picked = [cid for cid, _ in sorted(fused.items(), key=lambda t: -t[1])][:top_k]
    for c in chunks[:include_opening]:
        if c.id not in picked:
            picked.append(c.id)
    # Show the model the clauses in document order; it reads more naturally.
    return sorted((by_id[cid] for cid in picked), key=lambda c: c.idx)
