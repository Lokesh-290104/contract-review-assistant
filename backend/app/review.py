"""The review pipeline: parse -> chunk -> embed -> retrieve per item -> ask model -> verify quotes.

It runs in a background worker thread (see worker.py), so the upload request returns
at once and the server keeps answering other requests.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .checklist import CHECKLIST, ChecklistItem
from .chunking import chunk_text
from .config import get_settings
from .db import new_session
from .llm import AIClient, AIError, get_ai
from .models import Chunk, Contract, ContractFile, ContractStatus, Finding
from .parsing import ParseError, extract_text, page_for_offset
from .prompts import review_messages
from .quotes import find_quote
from .retrieval import retrieve
from .risk import risk_flag

log = logging.getLogger(__name__)

VALID_STATUSES = {"found", "unclear", "not_found"}
NOT_FOUND_SUMMARY = "Not found in the contract."
UNVERIFIED_NOTE = (
    "The model's quote could not be found word for word in the contract, so this item is marked unclear."
)


@dataclass
class ModelAnswer:
    status: str
    summary: str
    quote: str


def parse_model_answer(text: str) -> ModelAnswer | None:
    """Read the JSON object from the model's reply (models sometimes wrap it in ```json fences)."""
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    status = str(data.get("status", "")).strip().lower().replace(" ", "_")
    if status not in VALID_STATUSES:
        return None
    return ModelAnswer(
        status=status,
        summary=" ".join(str(data.get("summary") or "").split())[:300],
        quote=str(data.get("quote") or ""),
    )


def build_finding(
    item: ChecklistItem,
    answer: ModelAnswer | None,
    contract: Contract,
    shown: list[Chunk],
    all_chunks: list[Chunk],
) -> Finding:
    """Turn the model's answer into a stored finding, checking the quote against the contract."""
    f = Finding(contract_id=contract.id, user_id=contract.user_id, item_key=item.key)

    if answer is None:
        f.status, f.summary, f.verified = "unclear", "The AI answer could not be read.", False
        f.note = "The model did not return a valid answer for this item."
    elif answer.status == "not_found":
        f.status, f.summary, f.verified = "not_found", NOT_FOUND_SUMMARY, False
    else:
        match = find_quote(
            contract.full_text or "", answer.quote, [(c.start_char, c.end_char) for c in shown]
        )
        f.summary = answer.summary or "(no summary given)"
        if match is None:
            f.status, f.verified = "unclear", False
            if answer.quote.strip():
                shown_quote = " ".join(answer.quote.split())
                shown_quote = shown_quote if len(shown_quote) <= 300 else shown_quote[:300] + "…"
                f.note = f'{UNVERIFIED_NOTE} The model quoted: "{shown_quote}"'
            else:
                f.note = "The model gave no quote for this item."
        else:
            f.status = answer.status
            f.verified = True
            f.quote, f.quote_start, f.quote_end = match.text, match.start, match.end
            holder = next((c for c in all_chunks if c.start_char <= match.start < c.end_char), None)
            if holder is not None:
                f.chunk_id, f.section = holder.id, holder.section or holder.heading
            if contract.has_pages and contract.page_starts:
                f.page = page_for_offset(contract.page_starts, match.start)

    f.risk = risk_flag(item.key, f.status, f.quote)
    return f


def _prepare_chunks(db: Session, contract: Contract, ai: AIClient) -> list[Chunk]:
    """Parse, chunk and embed — skipped on a retry when chunks already exist."""
    existing = db.scalars(
        select(Chunk).where(Chunk.user_id == contract.user_id, Chunk.contract_id == contract.id).order_by(Chunk.idx)
    ).all()
    if existing:
        return list(existing)

    data = db.get(ContractFile, contract.id).data
    extracted = extract_text(contract.content_type, data)
    contract.full_text = extracted.full_text
    contract.page_starts = extracted.page_starts
    contract.has_pages = extracted.has_pages

    drafts = chunk_text(extracted.full_text, extracted.page_starts, extracted.has_pages)
    vectors = ai.embed([d.embedding_text() for d in drafts])
    dim = get_settings().embed_dim
    if any(len(v) != dim for v in vectors):
        raise AIError(
            f"The embedding model returned vectors of size {len(vectors[0])}, but EMBED_DIM is {dim}. "
            "Set EMBED_DIM to match the model and recreate the database."
        )
    chunks = [
        Chunk(
            contract_id=contract.id,
            user_id=contract.user_id,
            idx=d.idx,
            section=d.section,
            heading=d.heading,
            page_start=d.page_start,
            page_end=d.page_end,
            start_char=d.start_char,
            end_char=d.end_char,
            text=d.text,
            embedding=v,
        )
        for d, v in zip(drafts, vectors)
    ]
    db.add_all(chunks)
    db.commit()  # keep the embeddings even if a later step is rate-limited
    return chunks


def review_contract(db: Session, contract: Contract, ai: AIClient) -> list[Finding]:
    chunks = _prepare_chunks(db, contract, ai)
    db.execute(delete(Finding).where(Finding.user_id == contract.user_id, Finding.contract_id == contract.id))

    query_vectors = ai.embed([item.query for item in CHECKLIST])  # one batched call for all 8 items
    findings = []
    for item, qv in zip(CHECKLIST, query_vectors):
        shown = retrieve(
            db, contract.user_id, contract.id, qv, item.keywords,
            top_k=6, include_opening=2 if item.include_opening else 0,
        )
        answer = parse_model_answer(ai.complete(review_messages(item, shown))) if shown else None
        if not shown:
            answer = ModelAnswer("not_found", NOT_FOUND_SUMMARY, "")
        findings.append(build_finding(item, answer, contract, shown, chunks))
    db.add_all(findings)
    return findings


def run_review(contract_id: uuid.UUID, user_id: str) -> None:
    """Entry point for the worker thread. Never raises; records failures on the contract."""
    db = new_session()
    try:
        contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.user_id == user_id))
        if contract is None:
            return  # deleted while queued
        try:
            review_contract(db, contract, get_ai())
            contract.status, contract.error = ContractStatus.REVIEWED, None
            contract.reviewed_at = datetime.now(timezone.utc)
        except (ParseError, AIError) as exc:
            db.rollback()
            contract = db.get(Contract, contract_id)
            if contract is None:
                return
            contract.status, contract.error = ContractStatus.FAILED, str(exc)
        except Exception:  # noqa: BLE001 - last line of defence for the worker thread
            log.exception("review failed for %s", contract_id)
            db.rollback()
            contract = db.get(Contract, contract_id)
            if contract is None:
                return
            contract.status, contract.error = ContractStatus.FAILED, "Something went wrong while reviewing. Please retry."
        db.commit()
    finally:
        db.close()
