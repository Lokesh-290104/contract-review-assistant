"""Follow-up chat: retrieve clauses for the question, stream the answer, then check its quotes."""

import json
import re
import uuid
from collections.abc import Iterator

from sqlalchemy import delete, select

from .db import new_session
from .llm import AIError, RateLimited, get_ai
from .models import ChatMessage, Chunk, Contract
from .parsing import page_for_offset
from .prompts import chat_messages
from .quotes import find_quote, quoted_passages
from .retrieval import retrieve

HISTORY_TURNS = 6
_STOP = {
    "what", "when", "where", "which", "does", "will", "would", "could", "should", "there", "their",
    "this", "that", "with", "from", "have", "about", "contract", "agreement", "under", "into", "they",
}


def question_keywords(question: str) -> list[str]:
    words = {w for w in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", question.lower()) if w not in _STOP}
    return [rf"\b{re.escape(w[:-1] if w.endswith('s') else w)}" for w in sorted(words)]


def check_citations(answer: str, contract: Contract, chunks: list[Chunk]) -> list[dict]:
    """Each double-quoted passage in the answer is looked up in the contract."""
    out = []
    for passage in quoted_passages(answer):
        m = find_quote(contract.full_text or "", passage, [(c.start_char, c.end_char) for c in chunks])
        if m is None:
            out.append({"quote": passage, "verified": False})
            continue
        holder = next((c for c in chunks if c.start_char <= m.start < c.end_char), None)
        page = page_for_offset(contract.page_starts, m.start) if contract.has_pages and contract.page_starts else None
        out.append(
            {
                "quote": m.text,
                "verified": True,
                "section": (holder.section or holder.heading) if holder else None,
                "page": page,
                "chunk_id": holder.id if holder else None,
            }
        )
    return out


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream_answer(contract_id: uuid.UUID, user_id: str, question: str) -> Iterator[str]:
    """Generator used by the SSE endpoint. Opens its own DB session (it outlives the request scope)."""
    db = new_session()
    try:
        contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.user_id == user_id))
        if contract is None:
            yield _sse("error", {"message": "Contract not found."})
            return

        history_rows = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.contract_id == contract_id)
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY_TURNS)
        ).all()
        history = [{"role": m.role, "content": m.content} for m in reversed(history_rows)]

        user_msg = ChatMessage(contract_id=contract_id, user_id=user_id, role="user", content=question)
        db.add(user_msg)
        db.commit()
        yield _sse("user", {"id": user_msg.id, "created_at": user_msg.created_at.isoformat()})

        ai = get_ai()
        parts: list[str] = []
        try:
            qv = ai.embed([question])[0]
            chunks = retrieve(db, user_id, contract_id, qv, question_keywords(question), top_k=6)
            for piece in ai.stream(chat_messages(question, chunks, history)):
                parts.append(piece)
                yield _sse("token", {"t": piece})
        except AIError as exc:
            # Remove the unanswered question so history stays clean, then tell the UI why.
            db.execute(delete(ChatMessage).where(ChatMessage.id == user_msg.id, ChatMessage.user_id == user_id))
            db.commit()
            yield _sse("error", {"message": str(exc), "rate_limited": isinstance(exc, RateLimited)})
            return

        answer = "".join(parts).strip() or "The contract doesn't say."
        citations = check_citations(answer, contract, chunks)
        reply = ChatMessage(
            contract_id=contract_id, user_id=user_id, role="assistant", content=answer, citations=citations
        )
        db.add(reply)
        db.commit()
        yield _sse(
            "done",
            {
                "id": reply.id,
                "role": "assistant",
                "content": answer,
                "citations": citations,
                "created_at": reply.created_at.isoformat(),
            },
        )
    finally:
        db.close()
