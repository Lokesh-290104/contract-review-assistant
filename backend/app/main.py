"""HTTP API. Every /api/contracts route requires a verified Clerk user and only touches that user's rows."""

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, load_only

from . import worker
from .auth import CurrentUser, current_user
from .chat import stream_answer
from .checklist import CHECKLIST, ITEMS_BY_KEY
from .config import get_settings
from .db import create_schema, get_db
from .models import ChatMessage, Chunk, Contract, ContractFile, ContractStatus, Finding
from .parsing import ParseError, detect_type
from .schemas import ChatMessageOut, ChatRequest, ClauseOut, ContractDetail, ContractOut, FindingOut

log = logging.getLogger(__name__)
READ_CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SKIP_STARTUP_DB") != "1":
        create_schema()
        n = worker.recover_interrupted()
        if n:
            log.warning("marked %d interrupted reviews as failed", n)
    yield
    worker.shutdown()


app = FastAPI(title="Contract Review Assistant API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def reject_huge_uploads(request: Request, call_next):
    """Refuse an oversized upload from its Content-Length, before the body is read."""
    if request.method == "POST" and request.url.path == "/api/contracts":
        length = request.headers.get("content-length")
        # +64 KB leaves room for the multipart headers around the file itself.
        if length and length.isdigit() and int(length) > get_settings().max_upload_bytes + 64 * 1024:
            mb = get_settings().max_upload_mb
            return JSONResponse({"detail": f"The file is larger than {mb} MB."}, status_code=413)
    return await call_next(request)


def owned_contract(db: Session, user: CurrentUser, contract_id: uuid.UUID) -> Contract:
    """Load a contract only if it belongs to the signed-in user. Otherwise 404 (we don't reveal it exists)."""
    contract = db.scalar(select(Contract).where(Contract.id == contract_id, Contract.user_id == user.id))
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found.")
    return contract


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/contracts", response_model=list[ContractOut])
def list_contracts(db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    return db.scalars(
        select(Contract)
        .options(load_only(
            Contract.id, Contract.filename, Contract.content_type, Contract.size_bytes, Contract.status,
            Contract.error, Contract.created_at, Contract.reviewed_at,
        ))
        .where(Contract.user_id == user.id)
        .order_by(Contract.created_at.desc())
    ).all()


@app.post("/api/contracts", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
def upload_contract(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(current_user),
):
    limit = get_settings().max_upload_bytes
    name = os.path.basename((file.filename or "").replace("\\", "/")).strip()[:255]
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file has no name.")

    # Read in 1 MB pieces and stop as soon as the file passes the limit.
    parts: list[bytes] = []
    size = 0
    while piece := file.file.read(READ_CHUNK):
        size += len(piece)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"The file is larger than {limit // (1024 * 1024)} MB."
            )
        parts.append(piece)
    data = b"".join(parts)
    if size == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file is empty.")
    try:
        kind = detect_type(name, data[:8192])
    except ParseError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    contract = Contract(user_id=user.id, filename=name, content_type=kind, size_bytes=size)
    db.add(contract)
    db.flush()
    db.add(ContractFile(contract_id=contract.id, data=data))
    db.commit()
    worker.submit_review(contract.id, user.id)
    db.refresh(contract)
    return contract


@app.get("/api/contracts/{contract_id}", response_model=ContractDetail)
def get_contract(contract_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    contract = owned_contract(db, user, contract_id)
    findings = db.scalars(
        select(Finding).where(Finding.user_id == user.id, Finding.contract_id == contract.id)
    ).all()
    order = {item.key: i for i, item in enumerate(CHECKLIST)}
    finding_out = []
    for f in sorted(findings, key=lambda f: order.get(f.item_key, 99)):
        out = FindingOut.model_validate(f)
        out.label = ITEMS_BY_KEY[f.item_key].label if f.item_key in ITEMS_BY_KEY else f.item_key
        finding_out.append(out)
    clauses = db.scalars(
        select(Chunk).where(Chunk.user_id == user.id, Chunk.contract_id == contract.id).order_by(Chunk.idx)
    ).all()
    return ContractDetail(
        **ContractOut.model_validate(contract).model_dump(),
        has_pages=contract.has_pages,
        findings=finding_out,
        clauses=[ClauseOut.model_validate(c) for c in clauses],
    )


@app.delete("/api/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(contract_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    contract = owned_contract(db, user, contract_id)
    db.delete(contract)  # chunks, findings, chat and the file go with it (ON DELETE CASCADE)
    db.commit()


@app.post("/api/contracts/{contract_id}/review", response_model=ContractOut, status_code=status.HTTP_202_ACCEPTED)
def rerun_review(contract_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    contract = owned_contract(db, user, contract_id)
    # One atomic UPDATE, so two quick clicks on Retry can never start two reviews.
    claimed = db.execute(
        update(Contract)
        .where(Contract.id == contract.id, Contract.user_id == user.id, Contract.status != ContractStatus.PROCESSING)
        .values(status=ContractStatus.PROCESSING, error=None)
    ).rowcount
    db.commit()
    if not claimed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This contract is already being reviewed.")
    worker.submit_review(contract.id, user.id)
    db.refresh(contract)
    return contract


@app.get("/api/contracts/{contract_id}/messages", response_model=list[ChatMessageOut])
def list_messages(contract_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    contract = owned_contract(db, user, contract_id)
    return db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id, ChatMessage.contract_id == contract.id)
        .order_by(ChatMessage.id)
    ).all()


@app.post("/api/contracts/{contract_id}/chat")
def chat(
    contract_id: uuid.UUID,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(current_user),
):
    contract = owned_contract(db, user, contract_id)
    if contract.status != ContractStatus.REVIEWED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Chat opens once the review has finished.")
    question = body.question.strip()
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Please type a question.")
    return StreamingResponse(
        stream_answer(contract.id, user.id, question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
