"""API response shapes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    error: str | None
    created_at: datetime
    reviewed_at: datetime | None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_key: str
    label: str = ""
    status: str
    summary: str
    quote: str | None
    quote_start: int | None
    quote_end: int | None
    section: str | None
    page: int | None
    chunk_id: int | None
    verified: bool
    note: str | None
    risk: str | None


class ClauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idx: int
    section: str | None
    heading: str | None
    page_start: int | None
    page_end: int | None
    start_char: int
    end_char: int
    text: str


class ContractDetail(ContractOut):
    has_pages: bool
    findings: list[FindingOut]
    clauses: list[ClauseOut]


class Citation(BaseModel):
    quote: str
    verified: bool
    section: str | None = None
    page: int | None = None
    chunk_id: int | None = None


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    citations: list[Citation] | None
    created_at: datetime


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
